"""Governance event store — the spine of the AI Company Secretary pipeline.

Delta Filings previously had no object representing "something happened at a
company". Filings had comma-separated tags and that was it, so nothing could
be scored, queued, approved, or audited.

Four tables, all additive — no existing table is altered:

    governance_events   what happened + what changed        (Agent 1 + delta)
    event_analysis      why it matters                      (Agent 2, 1:1)
    event_artifacts     what we produced about it           (Agent 3, 1:many)
    audit_log           who/what touched it and when        (everything)

Status lifecycle:
    new -> analyzed -> actioned -> approved | dismissed | escalated
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from database import get_connection

STATUSES = ("new", "analyzed", "actioned", "approved", "dismissed", "escalated")
IMPORTANCE = ("high", "medium", "low")
RISK_LEVELS = ("high", "medium", "low")

# Terminal states — an event here is out of the reviewer's queue.
CLOSED_STATUSES = ("approved", "dismissed")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _loads(raw, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return fallback


# ── Schema ────────────────────────────────────────────────

def init_events_db():
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS governance_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            company_name TEXT DEFAULT '',
            filing_id INTEGER,
            source_kind TEXT NOT NULL DEFAULT 'filing',
            event_type TEXT NOT NULL,
            event_label TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'General',
            title TEXT DEFAULT '',
            headline TEXT DEFAULT '',
            detected_at TEXT NOT NULL,
            effective_date TEXT DEFAULT '',
            importance TEXT NOT NULL DEFAULT 'medium',
            requires_action INTEGER NOT NULL DEFAULT 0,
            delta_json TEXT DEFAULT '',
            entities_json TEXT DEFAULT '',
            source_ref_json TEXT DEFAULT '',
            scenario TEXT NOT NULL DEFAULT 'live',
            agent_model TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gov_events_symbol ON governance_events(symbol, detected_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gov_events_queue ON governance_events(status, importance)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gov_events_filing ON governance_events(filing_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL UNIQUE,
            finding TEXT DEFAULT '',
            why_it_matters TEXT DEFAULT '',
            regulatory_area TEXT DEFAULT '',
            mandate_ids_json TEXT DEFAULT '[]',
            risk_level TEXT DEFAULT 'medium',
            confidence INTEGER DEFAULT 0,
            reasoning_json TEXT DEFAULT '[]',
            recommended_actions_json TEXT DEFAULT '[]',
            affected_areas_json TEXT DEFAULT '[]',
            model TEXT DEFAULT '',
            latency_ms INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            artifact_type TEXT NOT NULL,
            title TEXT DEFAULT '',
            body TEXT DEFAULT '',
            filing_type TEXT DEFAULT '',
            draft_id INTEGER,
            metadata_json TEXT DEFAULT '{}',
            model TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_artifacts_event ON event_artifacts(event_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            actor_kind TEXT NOT NULL DEFAULT 'agent',
            action TEXT NOT NULL,
            event_id INTEGER,
            symbol TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            detail_json TEXT DEFAULT '{}',
            model TEXT DEFAULT '',
            latency_ms INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC)")

    conn.commit()
    conn.close()
    print("[EVENTS] Event intelligence tables initialized.")


# ── Audit trail ───────────────────────────────────────────

def log_audit(actor: str, action: str, event_id: Optional[int] = None,
              symbol: str = "", detail: str = "", detail_obj: Any = None,
              model: str = "", latency_ms: int = 0, actor_kind: str = "agent"):
    """Append-only. Every agent write and every human decision goes through here."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO audit_log
           (ts, actor, actor_kind, action, event_id, symbol, detail, detail_json, model, latency_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (_now(), actor, actor_kind, action, event_id, symbol, detail,
         json.dumps(detail_obj or {}), model, latency_ms),
    )
    conn.commit()
    conn.close()


def get_audit_trail(event_id: int) -> List[Dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE event_id = ? ORDER BY id ASC", (event_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_audit(limit: int = 50) -> List[Dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Events ────────────────────────────────────────────────

def upsert_event(*, dedupe_key: str, symbol: str, event_type: str, event_label: str,
                 category: str = "General", company_name: str = "", filing_id=None,
                 source_kind: str = "filing", title: str = "", headline: str = "",
                 detected_at: str = "", effective_date: str = "",
                 importance: str = "medium", requires_action: bool = False,
                 delta: Any = None, entities: Any = None, source_ref: Any = None,
                 scenario: str = "live", agent_model: str = "") -> int:
    """Insert an event, or refresh the detection fields if it already exists.

    Idempotent on dedupe_key so re-running the pipeline over the same filing
    doesn't duplicate the reviewer's queue. An existing event's *status* and
    human decisions are never overwritten.
    """
    now = _now()
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM governance_events WHERE dedupe_key = ?", (dedupe_key,)
    ).fetchone()

    payload = (
        symbol.upper(), company_name or symbol.upper(), filing_id, source_kind,
        event_type, event_label, category, title, headline,
        detected_at or now, effective_date, importance,
        1 if requires_action else 0,
        json.dumps(delta or {}), json.dumps(entities or {}), json.dumps(source_ref or {}),
        scenario, agent_model, now,
    )

    if row:
        event_id = row["id"]
        # A re-run must never *lose* detection detail. COALESCE-style guard:
        # an empty JSON blob in the new payload leaves the stored one intact,
        # so a partial re-detect can't blank out a populated delta.
        conn.execute("""
            UPDATE governance_events SET
                symbol=?, company_name=?, filing_id=?, source_kind=?,
                event_type=?, event_label=?, category=?, title=?, headline=?,
                detected_at=?, effective_date=?, importance=?, requires_action=?,
                delta_json      = CASE WHEN ? IN ('', '{}') THEN delta_json      ELSE ? END,
                entities_json   = CASE WHEN ? IN ('', '{}') THEN entities_json   ELSE ? END,
                source_ref_json = CASE WHEN ? IN ('', '{}') THEN source_ref_json ELSE ? END,
                scenario=?, agent_model=?, updated_at=?
            WHERE id=?
        """, payload[:13] + (
            payload[13], payload[13],
            payload[14], payload[14],
            payload[15], payload[15],
        ) + payload[16:] + (event_id,))
    else:
        conn.execute("""
            INSERT INTO governance_events
                (dedupe_key, symbol, company_name, filing_id, source_kind,
                 event_type, event_label, category, title, headline,
                 detected_at, effective_date, importance, requires_action,
                 delta_json, entities_json, source_ref_json,
                 scenario, agent_model, updated_at, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
        """, (dedupe_key,) + payload + (now,))
        event_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

    conn.commit()
    conn.close()
    return event_id


def _hydrate(row) -> Dict:
    e = dict(row)
    e["delta"] = _loads(e.pop("delta_json", ""), {})
    e["entities"] = _loads(e.pop("entities_json", ""), {})
    e["source_ref"] = _loads(e.pop("source_ref_json", ""), {})
    e["requires_action"] = bool(e.get("requires_action"))
    e["is_demo_scenario"] = e.get("scenario") == "demo"
    return e


def get_event(event_id: int) -> Optional[Dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM governance_events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    return _hydrate(row) if row else None


def list_events(symbols: Optional[List[str]] = None, status: Optional[str] = None,
                importance: Optional[str] = None, limit: int = 200,
                since_days: Optional[int] = None) -> List[Dict]:
    sql = "SELECT * FROM governance_events WHERE 1=1"
    args: List[Any] = []

    if symbols:
        sql += f" AND symbol IN ({','.join('?' * len(symbols))})"
        args += [s.upper() for s in symbols]
    if status:
        sql += " AND status = ?"
        args.append(status)
    if importance:
        sql += " AND importance = ?"
        args.append(importance)
    if since_days:
        cutoff = (datetime.now() - timedelta(days=since_days)).date().isoformat()
        sql += " AND substr(detected_at, 1, 10) >= ?"
        args.append(cutoff)

    # High risk first, then most recent.
    sql += """ ORDER BY CASE importance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
               detected_at DESC LIMIT ?"""
    args.append(limit)

    conn = get_connection()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [_hydrate(r) for r in rows]


def set_status(event_id: int, status: str, actor: str = "system",
               note: str = "", actor_kind: str = "human") -> bool:
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    conn = get_connection()
    cur = conn.execute(
        "UPDATE governance_events SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), event_id),
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    if changed:
        log_audit(actor=actor, actor_kind=actor_kind, action=f"status:{status}",
                  event_id=event_id, detail=note or f"Status set to {status}")
    return changed


# ── Analysis (Agent 2) ────────────────────────────────────

def save_analysis(event_id: int, *, finding: str = "", why_it_matters: str = "",
                  regulatory_area: str = "", mandate_ids: Optional[List[str]] = None,
                  risk_level: str = "medium", confidence: int = 0,
                  reasoning: Optional[List[str]] = None,
                  recommended_actions: Optional[List[Any]] = None,
                  affected_areas: Optional[List[str]] = None,
                  model: str = "", latency_ms: int = 0) -> int:
    conn = get_connection()
    conn.execute("DELETE FROM event_analysis WHERE event_id = ?", (event_id,))
    conn.execute("""
        INSERT INTO event_analysis
            (event_id, finding, why_it_matters, regulatory_area, mandate_ids_json,
             risk_level, confidence, reasoning_json, recommended_actions_json,
             affected_areas_json, model, latency_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (event_id, finding, why_it_matters, regulatory_area,
          json.dumps(mandate_ids or []), risk_level, int(confidence or 0),
          json.dumps(reasoning or []), json.dumps(recommended_actions or []),
          json.dumps(affected_areas or []), model, latency_ms, _now()))
    conn.commit()
    aid = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.close()
    return aid


def get_analysis(event_id: int) -> Optional[Dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM event_analysis WHERE event_id = ?", (event_id,)).fetchone()
    conn.close()
    if not row:
        return None
    a = dict(row)
    a["mandate_ids"] = _loads(a.pop("mandate_ids_json", ""), [])
    a["reasoning"] = _loads(a.pop("reasoning_json", ""), [])
    a["recommended_actions"] = _loads(a.pop("recommended_actions_json", ""), [])
    a["affected_areas"] = _loads(a.pop("affected_areas_json", ""), [])
    return a


# ── Artifacts (Agent 3) ───────────────────────────────────

def save_artifact(event_id: int, *, artifact_type: str, title: str = "",
                  body: str = "", filing_type: str = "", draft_id=None,
                  metadata: Any = None, model: str = "") -> int:
    conn = get_connection()
    conn.execute("""
        INSERT INTO event_artifacts
            (event_id, artifact_type, title, body, filing_type, draft_id,
             metadata_json, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (event_id, artifact_type, title, body, filing_type, draft_id,
          json.dumps(metadata or {}), model, _now()))
    conn.commit()
    aid = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.close()
    return aid


def get_artifacts(event_id: int) -> List[Dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM event_artifacts WHERE event_id = ? ORDER BY id ASC", (event_id,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        a = dict(r)
        a["metadata"] = _loads(a.pop("metadata_json", ""), {})
        out.append(a)
    return out


def clear_artifacts(event_id: int):
    """Agent 3 re-runs replace the artifact set rather than appending duplicates."""
    conn = get_connection()
    conn.execute("DELETE FROM event_artifacts WHERE event_id = ?", (event_id,))
    conn.commit()
    conn.close()


# ── Dashboard aggregates ──────────────────────────────────

def get_center_stats(symbols: Optional[List[str]] = None) -> Dict:
    """Headline counters for the Intelligence Center."""
    where, args = "", []
    if symbols:
        where = f" WHERE symbol IN ({','.join('?' * len(symbols))})"
        args = [s.upper() for s in symbols]

    conn = get_connection()
    total = conn.execute(f"SELECT COUNT(*) c FROM governance_events{where}", args).fetchone()["c"]
    by_status = {r["status"]: r["c"] for r in conn.execute(
        f"SELECT status, COUNT(*) c FROM governance_events{where} GROUP BY status", args)}
    # Risk is the ASSESSED level where Agent 2 has run, falling back to the
    # detector's importance. Reading raw `importance` here while the morning
    # brief read the analysed risk made the same screen show "8 risk alerts"
    # next to "nothing urgent".
    by_importance = {}
    for r in conn.execute(
            f"""SELECT COALESCE(NULLIF(a.risk_level, ''), e.importance) AS risk, COUNT(*) c
                FROM governance_events e
                LEFT JOIN event_analysis a ON a.event_id = e.id
                {where.replace('symbol', 'e.symbol')}
                GROUP BY risk""", args):
        by_importance[r["risk"]] = r["c"]

    if symbols:
        placeholders = ",".join("?" * len(symbols))
        filings = conn.execute(
            f"SELECT COUNT(*) c FROM filings WHERE company IN ({placeholders})", args
        ).fetchone()["c"]
    else:
        filings = conn.execute("SELECT COUNT(*) c FROM filings").fetchone()["c"]

    agent_actions = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE actor_kind = 'agent'"
    ).fetchone()["c"]
    conn.close()

    pending = sum(by_status.get(s, 0) for s in ("new", "analyzed", "actioned"))
    # "Needs attention" = high + medium assessed risk, which is exactly what the
    # morning brief queues. Counting only `high` showed 0 while the brief listed
    # 13 items, because the Intelligence Agent correctly downgrades a quarterly
    # report that CONFIRMS an already-disclosed change to a verification task.
    needs_attention = by_importance.get("high", 0) + by_importance.get("medium", 0)
    return {
        "companies_monitored": len(symbols) if symbols else 0,
        "filings_processed": filings,
        "events_detected": total,
        "pending_review": pending,
        "risk_alerts": needs_attention,
        "high_risk": by_importance.get("high", 0),
        "by_status": by_status,
        "by_importance": by_importance,
        "agent_actions": agent_actions,
    }


def get_company_timeline(symbol: str, days: int = 365) -> List[Dict]:
    """Chronological event history for one company — powers the governance timeline
    and gives Agent 2 its company memory."""
    cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM governance_events
           WHERE symbol = ? AND substr(detected_at, 1, 10) >= ?
           ORDER BY detected_at DESC""",
        (symbol.upper(), cutoff),
    ).fetchall()
    conn.close()
    return [_hydrate(r) for r in rows]


def list_events_with_analysis(symbols: Optional[List[str]] = None, limit: int = 200,
                              **kw) -> List[Dict]:
    """Events joined to their analysis in one pass — the Intelligence Center
    register renders straight from this."""
    events = list_events(symbols=symbols, limit=limit, **kw)
    if not events:
        return []

    ids = [e["id"] for e in events]
    conn = get_connection()
    rows = conn.execute(
        f"SELECT * FROM event_analysis WHERE event_id IN ({','.join('?' * len(ids))})", ids
    ).fetchall()
    counts = {r["event_id"]: r["c"] for r in conn.execute(
        f"SELECT event_id, COUNT(*) c FROM event_artifacts "
        f"WHERE event_id IN ({','.join('?' * len(ids))}) GROUP BY event_id", ids)}
    conn.close()

    by_event = {}
    for r in rows:
        a = dict(r)
        a["mandate_ids"] = _loads(a.pop("mandate_ids_json", ""), [])
        a["recommended_actions"] = _loads(a.pop("recommended_actions_json", ""), [])
        a["reasoning"] = _loads(a.pop("reasoning_json", ""), [])
        a["affected_areas"] = _loads(a.pop("affected_areas_json", ""), [])
        by_event[a["event_id"]] = a

    for e in events:
        a = by_event.get(e["id"])
        e["analysis"] = a
        e["risk_level"] = (a or {}).get("risk_level") or e["importance"]
        e["confidence"] = (a or {}).get("confidence") or 0
        e["finding"] = (a or {}).get("finding") or e.get("headline") or ""
        e["mandate_ids"] = (a or {}).get("mandate_ids") or []
        e["artifact_count"] = counts.get(e["id"], 0)
        e["top_action"] = ((a or {}).get("recommended_actions") or [{}])[0].get("title", "")
    return events
