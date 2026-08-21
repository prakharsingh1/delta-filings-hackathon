"""Supporting context for a single governance event.

The event page had the assessment and the documents. What a Company Secretary
also wants in front of them: who is actually on the board right now, which
obligations this event puts on the calendar, whether it is part of a pattern,
and how it compares with the rest of the portfolio. All of it is already in
Delta's records.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from database import get_connection


def board_roster(symbol: str, quarter: Optional[str] = None) -> Dict[str, Any]:
    """The board as filed, split by category, for the relevant quarter."""
    import governance_snapshots as gs

    snaps = gs.list_snapshots(symbol)
    if not snaps:
        return {}
    target = quarter or snaps[0]["quarter"]
    snap = gs.get_snapshot(symbol, target) or gs.get_snapshot(symbol, snaps[0]["quarter"])
    if not snap:
        return {}

    active = [d for d in snap["board"] if not d.get("ceased")]
    def bucket(d):
        cat = (d.get("category") or "").lower()
        if "independent" in cat:
            return "independent"
        if "executive" in cat and "non" not in cat:
            return "executive"
        return "non_executive"

    grouped: Dict[str, List[Dict]] = {"executive": [], "independent": [], "non_executive": []}
    for d in active:
        grouped[bucket(d)].append({
            "name": d.get("name"), "din": d.get("din"),
            "category": d.get("category"), "appointed": d.get("appointed"),
            "committees": d.get("committee_memberships"),
        })

    return {
        "quarter": snap["quarter"],
        "source_url": snap.get("source_url", ""),
        "total": len(active),
        "groups": grouped,
        "summary": snap.get("summary", {}),
    }


def triggered_obligations(symbol: str, event_type: str, mandate_ids: List[str],
                          limit: int = 6) -> List[Dict]:
    """Obligations on the calendar that this event's regulations point at."""
    if not mandate_ids:
        return []
    ph = ",".join("?" * len(mandate_ids))
    conn = get_connection()
    rows = conn.execute(
        f"""SELECT mandate_id, period, due_date, status FROM compliance_obligations
            WHERE symbol = ? AND mandate_id IN ({ph})
              AND due_date >= date('now', '-120 day')
            ORDER BY due_date LIMIT ?""",
        [symbol.upper()] + list(mandate_ids) + [limit]).fetchall()
    conn.close()

    try:
        from compliance import SEBI_MANDATES
        by_id = {m["id"]: m for m in SEBI_MANDATES}
    except Exception:
        by_id = {}

    out = []
    for r in rows:
        m = by_id.get(r["mandate_id"], {})
        days = None
        try:
            days = (date.fromisoformat(r["due_date"]) - date.today()).days
        except (ValueError, TypeError):
            pass
        out.append({
            "mandate_id": r["mandate_id"],
            "name": m.get("name", r["mandate_id"]),
            "regulation": m.get("regulation", ""),
            "period": r["period"], "due_date": r["due_date"],
            "status": r["status"], "days": days,
            "penalty": m.get("penalty", ""),
        })
    return out


def similar_events(event_id: int, event_type: str, symbol: str,
                   limit: int = 5) -> Dict[str, List[Dict]]:
    """Is this a one-off, or a pattern?

    Two comparisons: the same company's history of this event type, and the
    same event type at the other companies being monitored.
    """
    conn = get_connection()
    same_co = [dict(r) for r in conn.execute(
        """SELECT e.id, e.event_label, e.detected_at, e.status,
                  COALESCE(a.risk_level, e.importance) AS risk
           FROM governance_events e LEFT JOIN event_analysis a ON a.event_id = e.id
           WHERE e.symbol = ? AND e.event_type = ? AND e.id != ?
           ORDER BY e.detected_at DESC LIMIT ?""",
        (symbol.upper(), event_type, event_id, limit))]

    other_co = [dict(r) for r in conn.execute(
        """SELECT e.id, e.symbol, e.event_label, e.detected_at,
                  COALESCE(a.risk_level, e.importance) AS risk
           FROM governance_events e LEFT JOIN event_analysis a ON a.event_id = e.id
           WHERE e.event_type = ? AND e.symbol != ? AND e.id != ?
           ORDER BY e.detected_at DESC LIMIT ?""",
        (event_type, symbol.upper(), event_id, limit))]
    conn.close()
    return {"same_company": same_co, "other_companies": other_co}


def peer_comparison(symbol: str) -> List[Dict]:
    """Board composition across every monitored company, for context."""
    import governance_snapshots as gs
    from database import get_watchlist

    peers = []
    for w in get_watchlist("demo-judge"):
        sym = w["symbol"]
        snaps = gs.list_snapshots(sym)
        if not snaps:
            continue
        su = snaps[0]["summary"]
        peers.append({
            "symbol": sym,
            "total": su.get("total_directors"),
            "independent": su.get("independent_directors"),
            "independent_pct": su.get("independent_ratio_pct"),
            "is_current": sym == symbol.upper(),
        })
    peers.sort(key=lambda p: -(p["independent_pct"] or 0))
    return peers


def source_excerpt(filing_id: Optional[int], chars: int = 1200) -> Dict[str, Any]:
    """The filing text behind the event, so the evidence is readable in place."""
    if not filing_id:
        return {}
    conn = get_connection()
    r = conn.execute(
        "SELECT title, date, pdf_link, substr(COALESCE(raw_text,''),1,?) AS excerpt, "
        "length(COALESCE(raw_text,'')) AS full_len FROM filings WHERE id = ?",
        (chars, filing_id)).fetchone()
    conn.close()
    if not r:
        return {}
    return {"title": r["title"], "date": r["date"], "pdf_link": r["pdf_link"],
            "excerpt": (r["excerpt"] or "").strip(), "full_len": r["full_len"] or 0}


def external_coverage(symbol: str, company_name: str, event_label: str,
                      effective_date: str = "", limit: int = 4) -> List[Dict]:
    """Public reporting on this event, via Firecrawl search.

    Read-only corroboration. Results are shown as sources for the reviewer to
    judge — Delta does not draw conclusions from them.
    """
    try:
        import firecrawl_client as fc
    except Exception:
        return []
    if not fc.available():
        return []

    when = (effective_date or "")[:4]
    query = f"{company_name or symbol} {event_label} {when} NSE BSE disclosure".strip()
    try:
        return fc.search(query, limit=limit, timeout=45)
    except Exception as e:
        print(f"[EVENT-CTX] coverage search failed: {str(e)[:120]}")
        return []
