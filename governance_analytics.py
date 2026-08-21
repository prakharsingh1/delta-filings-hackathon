"""Structural governance analysis — the layer that answers "why".

Delta's agents were describing events ("an independent director ceased; board
9 -> 8") and citing the disclosure rule. That is the observation, not the
analysis. A senior Company Secretary reads the same filing and asks:

    Does the board still satisfy Reg 17 composition?
    Does the Audit Committee still have quorum and an independent chair?
    Is this the third departure in eighteen months?
    Which directors are approaching the s.149(11) tenure cap?
    How does the independence ratio compare with sector peers?
    Is one person now carrying four committees?

All of that is computable from the governance snapshots already stored. This
module computes it deterministically and hands the result to the agents, so
the model reasons over arithmetic rather than producing it.

Every check carries the provision it tests. Thresholds are encoded once here
rather than being left to a prompt.
"""

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import governance_snapshots as gs

INDEPENDENT = "independent"
EXECUTIVE = "executive"
NON_EXECUTIVE = "non-executive"


# ── Helpers ───────────────────────────────────────────────

def _is_independent(d) -> bool:
    """True only for a genuinely independent director.

    NSE writes the non-independent category as "Non-Executive - Non
    Independent Director", which CONTAINS the substring "independent". A naive
    `"independent" in category` counted those directors as independent and
    inflated every ratio, quorum count and peer comparison. Exclude the
    negated form first.
    """
    cat = (d.get("category") or "").lower()
    if "non independent" in cat or "non-independent" in cat:
        return False
    return "independent" in cat


def _is_executive(d: Dict) -> bool:
    cat = (d.get("category") or "").lower()
    return EXECUTIVE in cat and INDEPENDENT not in cat and "non-executive" not in cat


def _active(rows: List[Dict]) -> List[Dict]:
    return [r for r in rows if not r.get("ceased")]


def _parse(d: str) -> Optional[date]:
    try:
        return datetime.strptime((d or "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _years_since(d: str, ref: Optional[date] = None) -> Optional[float]:
    dt = _parse(d)
    if not dt:
        return None
    return round(((ref or date.today()) - dt).days / 365.25, 1)


def _finding(ok: bool, provision: str, requirement: str, observed: str,
             severity: str = "info") -> Dict:
    return {"compliant": ok, "provision": provision, "requirement": requirement,
            "observed": observed, "severity": ("ok" if ok else severity)}


# ── 1. Board composition against SEBI LODR Reg 17 ─────────

def board_composition(symbol: str, quarter: str = "") -> Dict[str, Any]:
    snaps = gs.list_snapshots(symbol)
    if not snaps:
        return {}
    snap = gs.get_snapshot(symbol, quarter or snaps[0]["quarter"])
    if not snap:
        return {}

    board = _active(snap["board"])
    total = len(board)
    independents = [d for d in board if _is_independent(d)]
    executives = [d for d in board if _is_executive(d)]
    ratio = round(len(independents) / total * 100) if total else 0

    # Reg 17(1)(b): one-third independent where the chairperson is
    # non-executive; one-half where the chair is executive.
    chair = next((d for d in board if "chair" in (d.get("category") or "").lower()), None)
    chair_is_exec = bool(chair and _is_executive(chair))
    required_pct = 50 if chair_is_exec else 33.33
    findings = [
        _finding(ratio >= required_pct, "SEBI LODR Reg 17(1)(b)",
                 f"At least {'one-half' if chair_is_exec else 'one-third'} of the board "
                 f"shall be independent directors ({required_pct:.0f}%)",
                 f"{len(independents)} of {total} independent ({ratio}%)"
                 + (f"; chairperson is executive" if chair_is_exec else ""),
                 severity="high"),
        _finding(total >= 6, "SEBI LODR Reg 17(1)(c)",
                 "The board shall have at least 6 directors (top 2000 listed entities)",
                 f"{total} directors", severity="high"),
        _finding(total <= 15, "Companies Act s.149(1)",
                 "A company shall have a maximum of 15 directors without a special resolution",
                 f"{total} directors", severity="medium"),
    ]

    women = [d for d in board if (d.get("name") or "").strip().lower().startswith(("ms", "mrs", "miss"))]
    women_independent = [d for d in women if _is_independent(d)]
    findings.append(_finding(
        bool(women_independent), "SEBI LODR Reg 17(1)(a)",
        "At least one independent woman director (top 1000 listed entities)",
        f"{len(women_independent)} identified from filed titles"
        + (" — verify against the register" if not women_independent else ""),
        severity="medium"))

    return {
        "quarter": snap["quarter"],
        "total_directors": total,
        "independent_directors": len(independents),
        "executive_directors": len(executives),
        "independence_ratio_pct": ratio,
        "required_independence_pct": required_pct,
        "headroom_directors": max(0, len(independents) - _ceil_needed(total, required_pct)),
        "chair_is_executive": chair_is_exec,
        "findings": findings,
        "source_url": snap.get("source_url", ""),
    }


def _ceil_needed(total: int, pct: float) -> int:
    import math
    return math.ceil(total * pct / 100)


# ── 2. Committee health ───────────────────────────────────

# Committee id -> (label, minimum members, minimum independent fraction,
#                  chair must be independent, provision)
_COMMITTEE_RULES = {
    "audit_committee": ("Audit Committee", 3, 2 / 3, True, "SEBI LODR Reg 18(1)"),
    "nomination_and_remuneration_committee": ("Nomination & Remuneration Committee", 3, 2 / 3, True, "SEBI LODR Reg 19(1)"),
    "stakeholders_relationship_committee": ("Stakeholders Relationship Committee", 3, 0.0, False, "SEBI LODR Reg 20(2)"),
    "risk_management_committee": ("Risk Management Committee", 3, 0.0, False, "SEBI LODR Reg 21(2)"),
}


def committee_health(symbol: str, quarter: str = "") -> Dict[str, Any]:
    snaps = gs.list_snapshots(symbol)
    if not snaps:
        return {}
    snap = gs.get_snapshot(symbol, quarter or snaps[0]["quarter"])
    if not snap:
        return {}

    out = {}
    for cid, members in (snap.get("committees") or {}).items():
        rule = _COMMITTEE_RULES.get(cid)
        active = _active(members)
        if not active:
            continue
        independents = [m for m in active if _is_independent(m)]
        chair = next((m for m in active if "chair" in (m.get("role") or "").lower()), None)

        entry = {
            "label": (rule[0] if rule else cid.replace("_", " ").title()),
            "members": len(active),
            "independent_members": len(independents),
            "chair": chair.get("name") if chair else None,
            "chair_independent": bool(chair and _is_independent(chair)),
            "findings": [],
        }

        if rule:
            label, min_members, min_indep_frac, chair_indep_required, provision = rule
            entry["findings"].append(_finding(
                len(active) >= min_members, provision,
                f"{label} shall have at least {min_members} members",
                f"{len(active)} members", severity="high"))
            # How many departures until the minimum is breached — the number a
            # CS actually wants when a director leaves.
            entry["headroom"] = len(active) - min_members

            if min_indep_frac:
                need = _ceil_needed(len(active), min_indep_frac * 100)
                entry["findings"].append(_finding(
                    len(independents) >= need, provision,
                    f"At least two-thirds of {label} members shall be independent",
                    f"{len(independents)} of {len(active)} independent (need {need})",
                    severity="high"))
            if chair_indep_required:
                entry["findings"].append(_finding(
                    entry["chair_independent"], provision,
                    f"The chairperson of the {label} shall be an independent director",
                    f"chaired by {chair.get('name')} ({chair.get('category','')})" if chair
                    else "no chairperson identified in the filing",
                    severity="high"))
        out[cid] = entry
    return out


# ── 3. Tenure and board refreshment ───────────────────────

def tenure_analysis(symbol: str) -> Dict[str, Any]:
    """Independent-director tenure against the s.149(10)/(11) term limits."""
    snaps = gs.list_snapshots(symbol)
    if not snaps:
        return {}
    snap = gs.get_snapshot(symbol, snaps[0]["quarter"])
    board = _active(snap["board"])

    rows, approaching = [], []
    for d in board:
        yrs = _years_since(d.get("appointed", ""))
        if yrs is None:
            continue
        row = {"name": d["name"], "din": d.get("din", ""),
               "category": d.get("category", ""), "years_served": yrs,
               "independent": _is_independent(d)}
        rows.append(row)
        # s.149(10): a term of up to 5 years; s.149(11): no more than two
        # consecutive terms. Past 5 years means a reappointment resolution
        # should already exist — which is a different question from a term
        # about to expire, so the two are not conflated.
        if row["independent"]:
            if yrs >= 9:
                approaching.append({**row, "note": (
                    "approaching the two-term / 10-year ceiling under s.149(11) — "
                    "beyond it, reappointment as an independent director is not available")})
            elif yrs >= 5.5:
                approaching.append({**row, "note": (
                    "has served beyond a first 5-year term — confirm the special resolution "
                    "for the second term is on record (s.149(10))")})
            elif yrs >= 4.25:
                approaching.append({**row, "note": (
                    "first 5-year term approaching expiry (s.149(10)) — reappointment "
                    "requires a special resolution")})

    tenures = [r["years_served"] for r in rows]
    return {
        "quarter": snap["quarter"],
        "directors": sorted(rows, key=lambda r: -r["years_served"]),
        "average_tenure_years": round(sum(tenures) / len(tenures), 1) if tenures else 0,
        "longest_serving": max(rows, key=lambda r: r["years_served"]) if rows else None,
        "term_attention": approaching,
    }


# ── 4. Turnover pattern across quarters ───────────────────

def turnover_pattern(symbol: str) -> Dict[str, Any]:
    """Board churn across every stored quarter — turns a single departure into
    a trend, which is the difference between an event and a finding."""
    snaps = gs.list_snapshots(symbol)
    if len(snaps) < 2:
        return {}

    periods, joined_total, departed_total = [], 0, 0
    for newer, older in zip(snaps, snaps[1:]):
        d = gs.compute_delta(symbol, older["quarter"], newer["quarter"])
        if not d:
            continue
        j, dep = len(d["directors_joined"]), len(d["directors_departed"])
        joined_total += j
        departed_total += dep
        if j or dep:
            periods.append({
                "from": d["before_period"], "to": d["after_period"],
                "joined": j, "departed": dep,
                "joined_names": [x["name"] for x in d["directors_joined"]],
                "departed_names": [x["name"] for x in d["directors_departed"]],
                "independent_departures": sum(
                    1 for x in d["directors_departed"] if _is_independent(x)),
            })

    quarters = len(snaps) - 1
    latest = gs.get_snapshot(symbol, snaps[0]["quarter"])
    size = len(_active(latest["board"])) if latest else 0
    indep_departures = sum(p["independent_departures"] for p in periods)

    return {
        "quarters_observed": quarters,
        "span": f"{snaps[-1]['quarter']} to {snaps[0]['quarter']}",
        "total_joined": joined_total,
        "total_departed": departed_total,
        "independent_departures": indep_departures,
        "annualised_turnover_pct": round(
            (departed_total / quarters * 4) / size * 100) if quarters and size else 0,
        "periods_with_change": periods,
        "pattern": _describe_pattern(departed_total, indep_departures, quarters, size),
    }


def _describe_pattern(departed: int, indep_departed: int, quarters: int, size: int) -> str:
    if not quarters or not size:
        return "insufficient history"
    per_year = departed / quarters * 4
    if indep_departed >= 3:
        return (f"{indep_departed} independent directors have left across "
                f"{quarters} quarters — sustained turnover in the independent cohort")
    if per_year >= size * 0.3:
        return f"elevated churn — roughly {per_year:.1f} departures a year on a board of {size}"
    if departed == 0:
        return "stable — no departures across the observed window"
    return f"routine — {departed} departure(s) across {quarters} quarters"


# ── 5. Committee load concentration ───────────────────────

def concentration(symbol: str, quarter: str = "") -> Dict[str, Any]:
    """Directors carrying several committees at once — a key-person dependency
    that a single resignation converts into multiple quorum problems."""
    snaps = gs.list_snapshots(symbol)
    if not snaps:
        return {}
    snap = gs.get_snapshot(symbol, quarter or snaps[0]["quarter"])
    if not snap:
        return {}

    load: Dict[str, Dict] = {}
    for cid, members in (snap.get("committees") or {}).items():
        label = _COMMITTEE_RULES.get(cid, (cid.replace("_", " ").title(),))[0]
        for m in _active(members):
            e = load.setdefault(m["name"], {"name": m["name"], "committees": [],
                                            "chairs": 0, "independent": _is_independent(m)})
            e["committees"].append(label)
            if "chair" in (m.get("role") or "").lower():
                e["chairs"] += 1

    ranked = sorted(load.values(), key=lambda x: (-len(x["committees"]), -x["chairs"]))
    heavy = [r for r in ranked if len(r["committees"]) >= 3]
    return {
        "quarter": snap["quarter"],
        "most_loaded": ranked[:5],
        "multi_committee_directors": len(heavy),
        "note": (f"{len(heavy)} director(s) sit on three or more committees — "
                 f"a single departure would affect multiple committees at once")
        if heavy else "committee load is spread across the board",
    }


# ── 6. Peer benchmarking ──────────────────────────────────

def peer_benchmark(symbol: str, peers: List[str]) -> Dict[str, Any]:
    """Where this company sits against its monitored sector peers. Context a
    single company's own numbers cannot give."""
    rows = []
    for s in {symbol.upper(), *[p.upper() for p in peers]}:
        c = board_composition(s)
        if not c:
            continue
        rows.append({"symbol": s, "board_size": c["total_directors"],
                     "independence_pct": c["independence_ratio_pct"],
                     "independent": c["independent_directors"]})
    if not rows:
        return {}

    ratios = sorted(r["independence_pct"] for r in rows)
    mid = len(ratios) // 2
    median = ratios[mid] if len(ratios) % 2 else round((ratios[mid - 1] + ratios[mid]) / 2)
    me = next((r for r in rows if r["symbol"] == symbol.upper()), None)
    sizes = [r["board_size"] for r in rows]

    verdict = ""
    if me:
        gap = me["independence_pct"] - median
        if gap >= 5:
            verdict = f"above the peer median on board independence (+{gap} pts)"
        elif gap <= -5:
            verdict = f"below the peer median on board independence ({gap} pts)"
        else:
            verdict = "in line with the peer median on board independence"

    return {
        "peer_set": [r["symbol"] for r in rows],
        "rows": sorted(rows, key=lambda r: -r["independence_pct"]),
        "median_independence_pct": median,
        "median_board_size": sorted(sizes)[len(sizes) // 2],
        "this_company": me,
        "verdict": verdict,
    }


# ── 7. Disclosure timeliness from our own filing history ──

def disclosure_timeliness(symbol: str, days: int = 540) -> Dict[str, Any]:
    """How quickly this company's board-change disclosures actually reach the
    exchange, measured from the filings Delta holds."""
    from database import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT title, date, sort_date FROM filings WHERE company = ? "
        "AND (LOWER(title) LIKE '%director%' OR LOWER(title) LIKE '%management%' "
        "     OR LOWER(title) LIKE '%key managerial%') "
        "AND sort_date >= date('now', ?) ORDER BY sort_date DESC LIMIT 40",
        (symbol.upper(), f"-{days} day")).fetchall()
    conn.close()

    return {
        "window_days": days,
        "board_related_filings": len(rows),
        "most_recent": [{"title": r["title"], "date": r["date"][:11]} for r in rows[:5]],
        "cadence_note": (f"{len(rows)} board/KMP-related disclosures in the last "
                         f"{days // 30} months")
        if rows else "no board-related disclosures found in the window",
    }


# ── Aggregate ─────────────────────────────────────────────

def full_profile(symbol: str, peers: Optional[List[str]] = None) -> Dict[str, Any]:
    """Everything, for the agents and the assistant to reason over."""
    symbol = symbol.upper()
    profile = {
        "symbol": symbol,
        "composition": board_composition(symbol),
        "committees": committee_health(symbol),
        "tenure": tenure_analysis(symbol),
        "turnover": turnover_pattern(symbol),
        "concentration": concentration(symbol),
        "timeliness": disclosure_timeliness(symbol),
    }
    if peers:
        profile["peers"] = peer_benchmark(symbol, peers)
    profile["breaches"] = collect_breaches(profile)
    profile["risks"] = collect_risks(profile)
    return profile


def collect_breaches(profile: Dict) -> List[Dict]:
    """Every failed structural check, flattened — the headline a CS wants."""
    out = []
    for f in (profile.get("composition") or {}).get("findings", []):
        if not f["compliant"]:
            out.append({"area": "Board composition", **f})
    for cid, c in (profile.get("committees") or {}).items():
        for f in c.get("findings", []):
            if not f["compliant"]:
                out.append({"area": c["label"], **f})
    return out


def collect_risks(profile: Dict) -> List[Dict]:
    """Things that are compliant TODAY but one change away from not being.

    This is the difference between a compliance report and advice. "The Audit
    Committee has three members" is an observation; "one departure breaches
    Reg 18(1)" is what a senior CS actually says.
    """
    risks: List[Dict] = []

    comp = profile.get("composition") or {}
    if comp:
        headroom = comp.get("headroom_directors", 99)
        if headroom <= 1:
            risks.append({
                "area": "Board composition", "severity": "high" if headroom == 0 else "medium",
                "provision": "SEBI LODR Reg 17(1)(b)",
                "risk": (f"Independent representation is {comp['independence_ratio_pct']}% against a "
                         f"{comp['required_independence_pct']:.0f}% floor. "
                         + ("Any independent cessation breaches the requirement."
                            if headroom == 0 else
                            "One independent cessation would take it to the floor.")),
            })

    for cid, c in (profile.get("committees") or {}).items():
        hr = c.get("headroom")
        if hr is not None and hr <= 0:
            risks.append({
                "area": c["label"], "severity": "high",
                "provision": next((f["provision"] for f in c.get("findings", [])), ""),
                "risk": (f"{c['label']} is at its statutory minimum of {c['members']} members. "
                         f"One departure puts the committee below quorum."),
            })

    conc = profile.get("concentration") or {}
    for m in (conc.get("most_loaded") or [])[:1]:
        if len(m.get("committees", [])) >= 4:
            risks.append({
                "area": "Key-person concentration", "severity": "medium",
                "provision": "",
                "risk": (f"{m['name']} sits on {len(m['committees'])} committees"
                         + (f" and chairs {m['chairs']}" if m["chairs"] else "")
                         + f" ({', '.join(m['committees'][:4])}). A single cessation would "
                           f"require simultaneous reconstitution of each."),
            })

    for a in (profile.get("tenure") or {}).get("term_attention", [])[:3]:
        risks.append({
            "area": "Board refreshment", "severity": "medium" if a["years_served"] >= 9 else "low",
            "provision": "Companies Act s.149(10)-(11)",
            "risk": f"{a['name']} has served {a['years_served']} years — {a['note']}. "
                    f"Succession planning and the reappointment resolution need lead time.",
        })

    peers = profile.get("peers") or {}
    me = peers.get("this_company")
    if me and peers.get("median_independence_pct"):
        gap = me["independence_pct"] - peers["median_independence_pct"]
        if gap <= -5:
            risks.append({
                "area": "Peer standing", "severity": "low", "provision": "",
                "risk": (f"Board independence is {me['independence_pct']}% against a peer median of "
                         f"{peers['median_independence_pct']}% across {len(peers['peer_set'])} monitored "
                         f"issuers — compliant, but below where the sector sits. Proxy advisers "
                         f"benchmark on this."),
            })

    turn = profile.get("turnover") or {}
    if turn.get("independent_departures", 0) >= 3:
        risks.append({
            "area": "Board stability", "severity": "high", "provision": "",
            "risk": (f"{turn['independent_departures']} independent directors have left over "
                     f"{turn['span']}. Sustained turnover in the independent cohort attracts "
                     f"scrutiny regardless of individual reasons."),
        })
    return risks
