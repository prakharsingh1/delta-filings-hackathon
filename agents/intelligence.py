"""Agent 2 — Governance Intelligence Agent.

Persona: Senior Company Secretary.
Question it answers: "Why does this matter, and what do we do about it?"

This is the agent the product lives or dies on, so its central constraint is
that it CANNOT invent a regulation. The flow is retrieval-grounded:

    event -> retrieve candidate mandates from compliance.SEBI_MANDATES
          -> retrieve company memory (prior events, compliance posture)
          -> Gemini Pro reasons over ONLY what was retrieved
          -> mandate_ids validated against the retrieved set before saving

Anything the model returns that isn't in the candidate set is discarded. A
citation on screen is therefore always a real row from the mandate library.
"""

import json
import re
from typing import Any, Dict, List, Optional

from .client import call_json, gemini_available

# Event type -> mandate ids that are near-always relevant. Deterministic
# anchors so retrieval never comes back empty for the events that matter.
_EVENT_MANDATE_HINTS = {
    "BOARD_CHANGE": ["LODR-REG30", "LODR-REG27", "MCA-DIR12", "MCA-MGT14"],
    "KMP_CHANGE": ["LODR-REG30", "LODR-REG27", "MCA-DIR12"],
    "AUDITOR_CHANGE": ["LODR-REG30", "MCA-ADT1", "LODR-REG24A", "MCA-MGT14"],
    "RELATED_PARTY_TRANSACTION": ["LODR-REG23-RPT", "LODR-REG27", "LODR-REG34-AR"],
    "CREDIT_RATING_CHANGE": ["LODR-REG30"],
    "BOARD_MEETING_OUTCOME": ["LODR-REG30", "LODR-REG33", "MCA-BOARD"],
    "BOARD_MEETING_INTIMATION": ["LODR-REG29", "MCA-BOARD"],
    "FINANCIAL_RESULTS": ["LODR-REG33", "MCA-AOC4"],
    "SHAREHOLDING_CHANGE": ["LODR-REG31", "PIT-REG7"],
    "RESTRUCTURING": ["LODR-REG30", "MCA-MGT14", "LODR-REG34-AR"],
    "REGULATORY_ACTION": ["LODR-REG30", "LODR-REG24A"],
    "TRADING_WINDOW": ["PIT-TRADING-WINDOW", "PIT-SDD"],
    "DIVIDEND_CORPORATE_ACTION": ["LODR-REG30", "LODR-REG27"],
    "AGM_EGM": ["LODR-AGM", "LODR-REG44", "MCA-MGT7"],
    "COMPLIANCE_CERTIFICATE": ["LODR-REG74", "LODR-REG76-RSCA", "LODR-REG24A"],
    "DISCLOSURE_OTHER": ["LODR-REG30"],
}

_STOPWORDS = {"the", "of", "and", "for", "with", "under", "from", "that", "this",
              "has", "have", "been", "are", "was", "were", "its", "his", "her",
              "their", "company", "limited", "ltd", "board", "filing", "report"}


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def retrieve_mandates(event: Dict, limit: int = 8) -> List[Dict]:
    """Pull candidate obligations from the existing mandate library.

    Scoring combines an explicit event-type hint with keyword overlap against
    each mandate's own `keywords`, name and description.
    """
    try:
        from compliance import SEBI_MANDATES
    except Exception as e:
        print(f"[AGENT2] mandate library unavailable: {e}")
        return []

    etype = event.get("event_type", "")
    hinted = set(_EVENT_MANDATE_HINTS.get(etype, []))

    haystack = " ".join(str(event.get(k, "")) for k in
                        ("event_label", "headline", "title", "category"))
    ev_tokens = _tokens(haystack)

    scored = []
    for m in SEBI_MANDATES:
        score = 0.0
        if m.get("id") in hinted:
            score += 10.0

        hay_lower = haystack.lower()
        for kw in m.get("keywords", []):
            k = kw.lower().strip()
            if k and re.search(r"\b" + re.escape(k) + r"\b", hay_lower):
                score += 4.0

        overlap = ev_tokens & _tokens(f"{m.get('name','')} {m.get('description','')}")
        score += min(len(overlap) * 0.8, 4.0)

        if event.get("category") and event["category"].lower() in (m.get("category", "").lower()):
            score += 1.5

        if score > 0:
            scored.append((score, m))

    scored.sort(key=lambda x: -x[0])
    out = []
    for score, m in scored[:limit]:
        out.append({
            "id": m.get("id"),
            "name": m.get("name"),
            "regulation": m.get("regulation"),
            "description": m.get("description"),
            "category": m.get("category"),
            "regime": m.get("regime"),
            "applies_to": m.get("applies_to"),
            "penalty": m.get("penalty"),
            "section_ref": m.get("section_ref", ""),
            "sebi_circular": m.get("sebi_circular", ""),
            "frequency": m.get("frequency", ""),
            "_score": round(score, 1),
        })
    return out


def build_company_memory(symbol: str, exclude_event_id: Optional[int] = None,
                         days: int = 365, limit: int = 12) -> Dict:
    """Company context so Agent 2 reasons about a pattern, not an isolated row.

    A third director change in a year is a different finding from the first —
    this is what lets the agent say so.
    """
    memory: Dict[str, Any] = {"symbol": symbol, "prior_events": [],
                              "event_type_counts": {}, "compliance": {}}

    try:
        from events_store import get_company_timeline
        timeline = get_company_timeline(symbol, days=days)
    except Exception:
        timeline = []

    for ev in timeline:
        if exclude_event_id and ev.get("id") == exclude_event_id:
            continue
        memory["event_type_counts"][ev["event_type"]] = \
            memory["event_type_counts"].get(ev["event_type"], 0) + 1
        if len(memory["prior_events"]) < limit:
            memory["prior_events"].append({
                "date": (ev.get("detected_at") or "")[:10],
                "event": ev.get("event_label"),
                "type": ev.get("event_type"),
                "importance": ev.get("importance"),
                "headline": (ev.get("headline") or "")[:160],
                "status": ev.get("status"),
            })

    # Structural governance analysis — composition against Reg 17, committee
    # quorum and independence against Regs 18-21, tenure against s.149,
    # turnover trend, committee concentration and peer standing. This is what
    # turns "a director left" into "the SRC is now at its statutory minimum".
    try:
        import governance_analytics as ga
        from demo import DEMO_SYMBOLS
        profile = ga.full_profile(symbol, peers=list(DEMO_SYMBOLS))
        memory["structural"] = {
            "composition": profile.get("composition", {}),
            "committee_headroom": {
                c["label"]: c.get("headroom")
                for c in (profile.get("committees") or {}).values()
                if c.get("headroom") is not None},
            "breaches": profile.get("breaches", []),
            "forward_risks": profile.get("risks", []),
            "turnover": {k: v for k, v in (profile.get("turnover") or {}).items()
                         if k != "periods_with_change"},
            "tenure_attention": (profile.get("tenure") or {}).get("term_attention", []),
            "concentration": (profile.get("concentration") or {}).get("most_loaded", [])[:3],
            "peer_standing": profile.get("peers", {}),
        }
    except Exception as e:
        print(f"[AGENT2] structural analysis unavailable for {symbol}: {str(e)[:120]}")

    # Current compliance posture from the existing obligations engine.
    try:
        from compliance import get_company_compliance
        cc = get_company_compliance(symbol) or {}
        memory["compliance"] = {
            "score": cc.get("score"),
            "overdue": len(cc.get("overdue", []) or []),
            "upcoming": len(cc.get("upcoming", []) or []),
            "health": cc.get("health_tag") or cc.get("health"),
        }
    except Exception as e:
        print(f"[AGENT2] compliance memory unavailable for {symbol}: {str(e)[:100]}")

    return memory


_PROMPT = """You are a Senior Company Secretary with 20 years of experience advising
Indian listed companies on SEBI LODR, the Companies Act 2013 and PIT Regulations.

A monitoring associate has flagged the following event for your assessment.
Write the assessment your CS team will act on tomorrow morning.

═══ EVENT ═══
Company:        {company} ({symbol})
Event:          {event_label} [{event_type}]
Detected:       {detected_at}
Effective date: {effective_date}
What the filing states: {headline}

Source document: {doc_title}
Evidence quote from the filing: {quote}

═══ STRUCTURED CHANGE DETECTED ═══
{delta}

═══ ENTITIES EXTRACTED ═══
{entities}

═══ COMPANY MEMORY (this company's history AND computed structural analysis) ═══
The `structural` block is DETERMINISTIC — board composition, committee quorum
and independence, tenure, turnover and peer standing were computed from the
company's own filed governance reports, not estimated. Treat those figures as
fact and reason from them. `forward_risks` lists what is compliant today but
one change away from not being; `committee_headroom` is how many departures a
committee can absorb before it breaches its statutory minimum.
{memory}

═══ CANDIDATE REGULATORY REQUIREMENTS ═══
These were retrieved from our mandate library. You may ONLY cite from this list.
{mandates}

═══ YOUR TASK ═══
Produce a governance assessment. Reason like a senior professional, not an
analyst restating the filing. Specifically:
  - Use the computed structural figures. "The Audit Committee is at its
    statutory minimum of 3" beats "committee composition may be affected".
  - Say what this event does to headroom: which committee or ratio is now
    closer to a floor, and how many further departures it can absorb.
  - Connect it to the pattern in company memory and to peer standing where
    that changes the picture. A third independent departure is a different
    finding from the first.
  - Name the specific consequential filings and internal records that follow.
  - If the structural analysis shows no real exposure, say so plainly rather
    than manufacturing concern.

ABSOLUTE RULES:
1. `mandate_ids` MUST contain only ids copied exactly from the candidate list
   above. If none genuinely apply, return an empty list. NEVER write a
   regulation number that is not in the list.
2. You are assessing a REAL listed company. State only what the evidence
   supports. Do not allege wrongdoing, non-compliance, or misconduct. If the
   filing is a routine disclosure, say so plainly.
3. Frame findings as procedural requirements ("a disclosure obligation is
   triggered"), never as accusations ("the company failed to...").
4. `confidence` reflects how well the available evidence supports your
   assessment. Low evidence means low confidence — do not inflate it.
   NEVER return 100. Certainty is not available from documentary evidence
   alone, and a compliance professional will not trust a tool that claims it.
   Cap yourself at 95 even when the evidence is unambiguous.
5. This is decision-support for a qualified professional, not legal advice.

Return ONLY this JSON:
{{
  "finding": "<one precise sentence stating what has been detected>",
  "why_it_matters": "<2-4 sentences: the governance significance, what is affected, and why a CS should care. Reference the company's history if relevant.>",
  "regulatory_area": "<short label, e.g. 'SEBI LODR — Board Composition & Material Events'>",
  "mandate_ids": ["<ids from the candidate list only>"],
  "affected_areas": ["<e.g. 'Board composition', 'Audit Committee quorum', 'Quarterly CG report'>"],
  "risk_level": "high" | "medium" | "low",
  "confidence": <0-100 integer>,
  "reasoning": [
    "<step 1 of your reasoning>",
    "<step 2>",
    "<step 3>"
  ],
  "recommended_actions": [
    {{
      "title": "<imperative action, e.g. 'Verify board composition against Reg 17'>",
      "detail": "<what specifically to check or prepare>",
      "owner": "Company Secretary" | "Compliance Officer" | "Board" | "Legal",
      "urgency": "immediate" | "this_week" | "this_month",
      "artifact": "<one of: disclosure_letter, board_note, internal_email, compliance_checklist, none>"
    }}
  ]
}}

risk_level guidance:
  high   - board composition/independence affected, material RPT, regulatory
           action, restructuring, or a repeat pattern in company memory
  medium - a disclosure obligation with a firm deadline
  low    - routine periodic compliance already satisfied by the filing itself
"""


def analyse_event(event: Dict, use_model: bool = True) -> Dict:
    """Produce the governance assessment for one detected event. Never raises."""
    symbol = event.get("symbol", "")
    mandates = retrieve_mandates(event)
    memory = build_company_memory(symbol, exclude_event_id=event.get("id"))

    valid_ids = {m["id"] for m in mandates}

    fallback = {
        "finding": event.get("headline") or event.get("event_label", "Governance event detected."),
        "why_it_matters": ("Automated assessment unavailable — this event was classified by rule "
                           "and requires manual review by the Company Secretary."),
        "regulatory_area": mandates[0]["regulation"] if mandates else "",
        "mandate_ids": [m["id"] for m in mandates[:3]],
        "affected_areas": [],
        "risk_level": event.get("importance", "medium"),
        "confidence": 25,
        "reasoning": ["Rule-based classification only; no model assessment available."],
        "recommended_actions": [{
            "title": "Manually review this filing",
            "detail": "The AI assessment could not be generated. Review the source document directly.",
            "owner": "Company Secretary", "urgency": "this_week", "artifact": "none",
        }],
        "model": "none", "latency_ms": 0,
        "mandates": mandates, "memory": memory,
    }

    if not use_model or not gemini_available():
        return fallback

    prompt = _PROMPT.format(
        company=event.get("company_name") or symbol,
        symbol=symbol,
        event_label=event.get("event_label", ""),
        event_type=event.get("event_type", ""),
        detected_at=(event.get("detected_at") or "")[:10],
        effective_date=event.get("effective_date") or "not stated",
        headline=event.get("headline", ""),
        doc_title=(event.get("source_ref") or {}).get("document", ""),
        quote=(event.get("source_ref") or {}).get("quote") or "(no verbatim quote could be grounded in the source document)",
        delta=json.dumps(event.get("delta") or {}, indent=2) if event.get("delta") else "(no structured before/after comparison available)",
        entities=json.dumps(event.get("entities") or {}, indent=2),
        memory=json.dumps(memory, indent=2, default=str),
        mandates=json.dumps([{k: v for k, v in m.items() if not k.startswith("_")}
                             for m in mandates], indent=2),
    )

    # Pro with thinking on — this is the one call where reasoning depth is
    # worth the latency.
    res = call_json(prompt, role="intelligence", max_output_tokens=3000,
                    temperature=0.25, thinking=True)

    if not res["ok"] or not isinstance(res["data"], dict):
        fallback["error"] = res.get("error", "")
        return fallback

    d = res["data"]

    # Hard gate: drop any citation the model produced that we did not retrieve.
    raw_ids = d.get("mandate_ids") or []
    kept = [i for i in raw_ids if i in valid_ids]
    dropped = [i for i in raw_ids if i not in valid_ids]
    if dropped:
        print(f"[AGENT2] rejected ungrounded mandate ids: {dropped}")

    risk = d.get("risk_level")
    try:
        # Hard-capped at 95: this product must never present an AI assessment
        # as certain. The prompt asks for the same, but a model that ignores
        # it should not be able to put "100%" in front of a CS professional.
        confidence = max(0, min(95, int(d.get("confidence", 50))))
    except (TypeError, ValueError):
        confidence = 50

    actions = []
    for a in (d.get("recommended_actions") or [])[:6]:
        if isinstance(a, dict) and a.get("title"):
            actions.append({
                "title": str(a.get("title"))[:200],
                "detail": str(a.get("detail", ""))[:600],
                "owner": str(a.get("owner", "Company Secretary"))[:60],
                "urgency": a.get("urgency") if a.get("urgency") in ("immediate", "this_week", "this_month") else "this_week",
                "artifact": a.get("artifact") if a.get("artifact") in (
                    "disclosure_letter", "board_note", "internal_email",
                    "compliance_checklist", "none") else "none",
            })

    return {
        "finding": str(d.get("finding", ""))[:500],
        "why_it_matters": str(d.get("why_it_matters", ""))[:2000],
        "regulatory_area": str(d.get("regulatory_area", ""))[:200],
        "mandate_ids": kept,
        "rejected_mandate_ids": dropped,
        "affected_areas": [str(x)[:120] for x in (d.get("affected_areas") or [])[:8]],
        "risk_level": risk if risk in ("high", "medium", "low") else event.get("importance", "medium"),
        "confidence": confidence,
        "reasoning": [str(x)[:500] for x in (d.get("reasoning") or [])[:8]],
        "recommended_actions": actions,
        "model": res["model"],
        "latency_ms": res["latency_ms"],
        "mandates": mandates,
        "memory": memory,
    }


def resolve_mandates(mandate_ids: List[str]) -> List[Dict]:
    """Hydrate stored mandate ids back into full rows for the UI."""
    try:
        from compliance import SEBI_MANDATES
    except Exception:
        return []
    by_id = {m["id"]: m for m in SEBI_MANDATES}
    return [by_id[i] for i in mandate_ids if i in by_id]
