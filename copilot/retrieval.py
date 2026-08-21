"""Grounding data for the assistant, pulled from Delta's own records.

The same discipline as the agent pipeline: regulations are RETRIEVED from
compliance.SEBI_MANDATES and offered to the model as a closed list. The model
selects from what it is given; it does not get to invent a citation.
"""

import re
from typing import Any, Dict, List

_STOP = {"the", "and", "for", "with", "what", "which", "does", "this", "that",
         "are", "was", "how", "why", "when", "any", "our", "from", "into",
         "should", "would", "could", "have", "has", "can", "will", "need"}


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


# Agent 1's taxonomy matches whole phrases as they appear in NSE filing titles
# ("resignation of director"). People do not type that — they type "director
# resignation" — so free-text questions need their own matcher. Each entry is
# (required terms, any-of terms) -> event type.
_QUESTION_TOPICS = [
    # Stems, not whole words: "stepped down" must match as readily as
    # "step down", and "resigned" as readily as "resignation".
    (({"director", "directors"}, {"resign", "cease", "cessation", "quit",
                                  "step", "leav", "left", "appoint", "chang",
                                  "retire", "retir", "induct", "exit", "join"}),
     "BOARD_CHANGE"),
    (({"board"}, {"chang", "composition", "compose", "independent", "size",
                  "member", "reconstitut"}), "BOARD_CHANGE"),
    (({"kmp", "managerial", "ceo", "cfo", "managing director",
       "company secretary", "compliance officer"}, set()), "KMP_CHANGE"),
    (({"auditor", "auditors", "audit firm"}, set()), "AUDITOR_CHANGE"),
    (({"related party", "rpt"}, set()), "RELATED_PARTY_TRANSACTION"),
    (({"credit rating", "rating"}, set()), "CREDIT_RATING_CHANGE"),
    (({"result", "results", "quarterly", "financial statement"}, set()), "FINANCIAL_RESULTS"),
    (({"shareholding", "takeover", "pledge", "sast", "acquisition of shares"},
      set()), "SHAREHOLDING_CHANGE"),
    (({"merger", "demerger", "amalgamation", "scheme", "restructur",
       "divestment", "slump sale"}, set()), "RESTRUCTURING"),
    (({"penalty", "show cause", "adjudicat", "tribunal", "litigation",
       "regulatory action"}, set()), "REGULATORY_ACTION"),
    (({"trading window", "insider"}, set()), "TRADING_WINDOW"),
    (({"dividend", "buyback", "bonus issue", "stock split", "record date"},
      set()), "DIVIDEND_CORPORATE_ACTION"),
    (({"agm", "egm", "general meeting", "postal ballot"}, set()), "AGM_EGM"),
]


def _infer_event_types(text: str) -> list:
    """Event types a free-text question is plausibly about. Multiple matches
    are fine — retrieval is a shortlist, not a decision."""
    t = (text or "").lower()
    out = []
    for (required, qualifiers), etype in _QUESTION_TOPICS:
        if not any(term in t for term in required):
            continue
        if qualifiers and not any(term in t for term in qualifiers):
            continue
        out.append(etype)
    return out


def find_mandates(question: str, ctx: Dict, limit: int = 6) -> List[Dict]:
    """Regulations relevant to the question and the current screen."""
    try:
        from compliance import SEBI_MANDATES
    except Exception:
        return []

    # Anything the page already cites outranks keyword matching.
    pinned = set((ctx.get("facts", {}).get("assessment", {}) or {}).get("mandate_ids") or [])

    # Infer the governance event the question is about and pin that event
    # type's mandates. Pure keyword overlap missed the obvious ones — asking
    # "what filings does a director resignation trigger?" returned DIR-12 but
    # not LODR Reg 30, because Reg 30's own keywords never say "resignation".
    # The agent taxonomy already encodes this mapping, so reuse it.
    try:
        from agents.intelligence import _EVENT_MANDATE_HINTS
        subject = f"{question} {(ctx.get('facts', {}).get('event') or {}).get('label', '')}"
        for etype in _infer_event_types(subject):
            pinned |= set(_EVENT_MANDATE_HINTS.get(etype, []))
    except Exception as e:
        print(f"[COPILOT] event-type inference unavailable: {str(e)[:90]}")
    q_tokens = _tokens(f"{question} {ctx.get('regulation') or ''} "
                       f"{(ctx.get('facts', {}).get('event') or {}).get('label', '')}")

    explicit = set((ctx.get("facts", {}).get("assessment", {}) or {}).get("mandate_ids") or [])
    scored = []
    for m in SEBI_MANDATES:
        score = 14.0 if m["id"] in explicit else (9.0 if m["id"] in pinned else 0.0)
        hay = f"{m.get('name','')} {m.get('description','')} {m.get('regulation','')}".lower()
        for kw in m.get("keywords", []):
            k = kw.lower().strip()
            if k and re.search(r"\b" + re.escape(k) + r"\b", question.lower()):
                score += 5.0
        score += min(len(q_tokens & _tokens(hay)) * 0.9, 5.0)
        if score > 0:
            scored.append((score, m))

    scored.sort(key=lambda x: -x[0])
    return [{
        "id": m["id"], "name": m.get("name"), "regulation": m.get("regulation"),
        "description": m.get("description"), "penalty": m.get("penalty", ""),
        "section_ref": m.get("section_ref", ""), "frequency": m.get("frequency", ""),
        "regime": m.get("regime", ""),
    } for _, m in scored[:limit]]


def recent_filings(symbol: str, limit: int = 8) -> List[Dict]:
    from database import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT title, date, tags FROM filings WHERE company = ? "
        "ORDER BY sort_date DESC LIMIT ?", (symbol.upper(), limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def open_obligations(symbols: List[str], days: int = 90, limit: int = 12) -> List[Dict]:
    from database import get_connection
    if not symbols:
        return []
    ph = ",".join("?" * len(symbols))
    conn = get_connection()
    rows = conn.execute(
        f"SELECT symbol, mandate_id, period, due_date FROM compliance_obligations "
        f"WHERE symbol IN ({ph}) AND status IN ('pending','unverified') "
        f"AND due_date BETWEEN date('now') AND date('now', ?) "
        f"ORDER BY due_date LIMIT ?", [s.upper() for s in symbols] + [f"+{days} day", limit]
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recent_events(symbols: List[str], limit: int = 8) -> List[Dict]:
    import events_store as es
    out = []
    for e in es.list_events_with_analysis(symbols or None, limit=limit):
        out.append({
            "symbol": e["symbol"], "event": e["event_label"],
            "detected": (e.get("detected_at") or "")[:10],
            "risk": e.get("risk_level"), "status": e["status"],
            "finding": (e.get("finding") or "")[:220],
            "documents": e.get("artifact_count", 0),
        })
    return out


def gather(question: str, ctx: Dict) -> Dict[str, Any]:
    """Everything worth putting in front of the model for this question."""
    bundle: Dict[str, Any] = {"mandates": find_mandates(question, ctx)}

    sym = ctx.get("company")
    if sym:
        bundle["recent_filings"] = recent_filings(sym)
        bundle["company_events"] = recent_events([sym], limit=6)
        # Computed structure, so the assistant answers "why" with arithmetic
        # rather than generalities.
        try:
            import governance_analytics as ga
            prof = ga.full_profile(sym, peers=ctx.get("watchlist") or [])
            bundle["structural_analysis"] = {
                "composition": prof.get("composition", {}),
                "breaches": prof.get("breaches", []),
                "forward_risks": prof.get("risks", []),
                "committees": {c["label"]: {"members": c["members"],
                                            "independent": c["independent_members"],
                                            "headroom": c.get("headroom")}
                               for c in (prof.get("committees") or {}).values()},
                "tenure_attention": (prof.get("tenure") or {}).get("term_attention", []),
                "turnover": {k: v for k, v in (prof.get("turnover") or {}).items()
                             if k != "periods_with_change"},
                "peer_standing": prof.get("peers", {}),
            }
        except Exception as e:
            print(f"[COPILOT] structural analysis unavailable for {sym}: {str(e)[:110]}")

    if ctx["page_type"] in ("compliance", "command_center", "dashboard"):
        wl = ctx.get("watchlist") or []
        bundle["upcoming_obligations"] = open_obligations(wl)
        bundle["open_events"] = recent_events(wl, limit=8)

    return bundle
