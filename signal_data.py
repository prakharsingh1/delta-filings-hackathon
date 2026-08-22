"""Aggregations behind the Signal page.

The action queue answers "what do I do next". These add the surrounding
picture a Company Secretary actually needs: how each company is trending,
where the risk is concentrated, and what is coming up — all from records
Delta already holds, so nothing here is generated.
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from database import get_connection
from demo_cache import cached

_CLOSED = ("approved", "dismissed")


@cached("signal.company_cards", ttl=900)
def company_cards(symbols: List[str]) -> List[Dict]:
    """Per-company posture: board trend, open work, next deadline, activity."""
    import governance_snapshots as gs

    conn = get_connection()
    out = []
    for sym in symbols:
        snaps = gs.list_snapshots(sym)
        # Oldest -> newest so the sparkline reads left to right.
        trend = [s["summary"].get("total_directors") for s in reversed(snaps)
                 if s["summary"].get("total_directors")]
        latest = snaps[0]["summary"] if snaps else {}

        row = conn.execute(
            """SELECT
                 COUNT(*) AS total,
                 SUM(CASE WHEN status NOT IN ('approved','dismissed') THEN 1 ELSE 0 END) AS open
               FROM governance_events WHERE symbol = ?""", (sym,)).fetchone()

        high = conn.execute(
            """SELECT COUNT(*) c FROM governance_events e
               JOIN event_analysis a ON a.event_id = e.id
               WHERE e.symbol = ? AND a.risk_level = 'high'
                 AND e.status NOT IN ('approved','dismissed')""", (sym,)).fetchone()["c"]

        nxt = conn.execute(
            """SELECT mandate_id, due_date FROM compliance_obligations
               WHERE symbol = ? AND status IN ('pending','unverified')
                 AND due_date >= date('now') ORDER BY due_date LIMIT 1""", (sym,)).fetchone()

        unmatched = conn.execute(
            """SELECT COUNT(*) c FROM compliance_obligations
               WHERE symbol = ? AND status IN ('pending','unverified')
                 AND due_date < date('now')""", (sym,)).fetchone()["c"]

        filings = conn.execute(
            "SELECT COUNT(*) c, MAX(sort_date) m FROM filings WHERE company = ?", (sym,)).fetchone()

        days_to_due = None
        if nxt and nxt["due_date"]:
            try:
                days_to_due = (date.fromisoformat(nxt["due_date"]) - date.today()).days
            except ValueError:
                pass

        out.append({
            "symbol": sym,
            "board_trend": trend,
            "board_now": latest.get("total_directors"),
            "board_delta": (trend[-1] - trend[0]) if len(trend) > 1 else 0,
            "independent_pct": latest.get("independent_ratio_pct"),
            "events_total": row["total"] or 0,
            "events_open": row["open"] or 0,
            "high_risk": high,
            "next_due": nxt["due_date"] if nxt else None,
            "next_mandate": nxt["mandate_id"] if nxt else None,
            "days_to_due": days_to_due,
            "to_verify": unmatched,
            "filings": filings["c"] or 0,
            "last_filing": (filings["m"] or "")[:10],
        })
    conn.close()
    return out


@cached("signal.risk_breakdown", ttl=900)
def risk_breakdown(symbols: List[str]) -> Dict[str, Any]:
    """Where the open work is concentrated — by category and by company."""
    if not symbols:
        return {"by_category": [], "by_company": [], "by_risk": {}}
    ph = ",".join("?" * len(symbols))
    conn = get_connection()

    by_cat = [dict(r) for r in conn.execute(
        f"""SELECT e.category AS label, COUNT(*) AS n
            FROM governance_events e
            WHERE e.symbol IN ({ph}) AND e.status NOT IN ('approved','dismissed')
            GROUP BY e.category ORDER BY n DESC""", symbols)]

    by_co = [dict(r) for r in conn.execute(
        f"""SELECT e.symbol AS label, COUNT(*) AS n
            FROM governance_events e
            WHERE e.symbol IN ({ph}) AND e.status NOT IN ('approved','dismissed')
            GROUP BY e.symbol ORDER BY n DESC""", symbols)]

    by_risk = dict(conn.execute(
        f"""SELECT COALESCE(NULLIF(a.risk_level,''), e.importance) AS r, COUNT(*)
            FROM governance_events e
            LEFT JOIN event_analysis a ON a.event_id = e.id
            WHERE e.symbol IN ({ph}) AND e.status NOT IN ('approved','dismissed')
            GROUP BY r""", symbols).fetchall())
    conn.close()

    total = sum(x["n"] for x in by_cat) or 1
    for x in by_cat:
        x["pct"] = round(x["n"] / total * 100)
    return {"by_category": by_cat, "by_company": by_co, "by_risk": by_risk}


@cached("signal.deadline_runway", ttl=1800)
def deadline_runway(symbols: List[str]) -> Dict[str, Any]:
    """Obligation load in the next 7 / 30 / 90 days."""
    if not symbols:
        return {"buckets": [], "total": 0}
    ph = ",".join("?" * len(symbols))
    conn = get_connection()
    buckets = []
    for label, days in (("7 days", 7), ("30 days", 30), ("90 days", 90)):
        n = conn.execute(
            f"""SELECT COUNT(*) c FROM compliance_obligations
                WHERE symbol IN ({ph}) AND status IN ('pending','unverified')
                  AND due_date BETWEEN date('now') AND date('now', ?)""",
            symbols + [f"+{days} day"]).fetchone()["c"]
        buckets.append({"label": label, "n": n})
    conn.close()
    return {"buckets": buckets, "total": buckets[-1]["n"] if buckets else 0}


@cached("signal.document_stats", ttl=900)
def document_stats(symbols: List[str]) -> Dict[str, Any]:
    """What Delta has already drafted and is waiting on a human for."""
    if not symbols:
        return {"total": 0, "by_type": [], "awaiting": 0}
    ph = ",".join("?" * len(symbols))
    conn = get_connection()
    by_type = [dict(r) for r in conn.execute(
        f"""SELECT a.artifact_type AS label, COUNT(*) AS n
            FROM event_artifacts a JOIN governance_events e ON e.id = a.event_id
            WHERE e.symbol IN ({ph}) GROUP BY a.artifact_type ORDER BY n DESC""", symbols)]
    awaiting = conn.execute(
        f"""SELECT COUNT(DISTINCT e.id) c
            FROM governance_events e JOIN event_artifacts a ON a.event_id = e.id
            WHERE e.symbol IN ({ph}) AND e.status NOT IN ('approved','dismissed')""",
        symbols).fetchone()["c"]
    conn.close()
    return {"total": sum(x["n"] for x in by_type), "by_type": by_type, "awaiting": awaiting}


@cached("signal.activity_sparkline", ttl=3600)
def activity_sparkline(symbols: List[str], weeks: int = 12) -> List[Dict]:
    """Filing volume per week — the ambient noise level the agents filter."""
    if not symbols:
        return []
    ph = ",".join("?" * len(symbols))
    conn = get_connection()
    out = []
    for w in range(weeks - 1, -1, -1):
        start = (date.today() - timedelta(days=(w + 1) * 7)).isoformat()
        end = (date.today() - timedelta(days=w * 7)).isoformat()
        n = conn.execute(
            f"""SELECT COUNT(*) c FROM filings WHERE company IN ({ph})
                AND substr(sort_date,1,10) > ? AND substr(sort_date,1,10) <= ?""",
            symbols + [start, end]).fetchone()["c"]
        out.append({"week_ending": end, "n": n})
    conn.close()
    return out


def agent_status() -> Dict[str, Any]:
    """What the agent team is configured as and when it last ran."""
    conn = get_connection()
    last = conn.execute(
        "SELECT ts, actor FROM audit_log WHERE actor_kind='agent' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    runs = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE actor_kind='agent'").fetchone()["c"]
    conn.close()

    try:
        from agents.client import AGENT_MODELS, gemini_healthy
        models = AGENT_MODELS
        from demo_cache import frozen
        if frozen():
            # The health probe is a live generateContent call — a full second,
            # and an outbound request frozen mode exists to prevent. The agents
            # have already run; report them as configured.
            health = {"ok": True, "error": ""}
        else:
            health = gemini_healthy()
    except Exception:
        health, models = {"ok": False, "error": "unavailable"}, {}

    return {
        "healthy": health.get("ok", False),
        "error": health.get("error", ""),
        "last_run": (last["ts"] if last else ""),
        "actions": runs,
        "agents": [
            {"key": "monitor", "name": "Monitoring", "role": "Detects governance events in filings",
             "model": models.get("monitor", "")},
            {"key": "intelligence", "name": "Intelligence", "role": "Assesses impact and cites regulation",
             "model": models.get("intelligence", "")},
            {"key": "action", "name": "Drafting", "role": "Prepares the documents each event needs",
             "model": models.get("action", "")},
        ],
    }
