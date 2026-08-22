"""Portfolio briefing — the agent reasoning ACROSS companies, not one event.

Every other agent looks at a single filing or a single event. A Company
Secretary's actual question in the morning is broader: across everything I
watch, what matters, what connects, and what do I start with. This produces
that, from the assessments already stored.

Cached: it summarises stored records, so it does not need to re-run on every
page load.
"""

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from database import get_connection

CACHE_TTL_MIN = 180


def init_briefing_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_briefings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_key TEXT NOT NULL UNIQUE,
            headline TEXT DEFAULT '',
            themes_json TEXT DEFAULT '[]',
            priority_json TEXT DEFAULT '[]',
            watch_items_json TEXT DEFAULT '[]',
            model TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _scope_key(symbols: List[str], fingerprint: str) -> str:
    raw = ",".join(sorted(symbols)) + "|" + fingerprint
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def _fingerprint(symbols: List[str]) -> str:
    """Changes when the open work changes, so the cache invalidates itself."""
    if not symbols:
        return "empty"
    ph = ",".join("?" * len(symbols))
    conn = get_connection()
    row = conn.execute(
        f"""SELECT COUNT(*) n, COALESCE(MAX(e.updated_at),'') u
            FROM governance_events e WHERE e.symbol IN ({ph})
              AND e.status NOT IN ('approved','dismissed')""", symbols).fetchone()
    conn.close()
    return f"{row['n']}:{row['u']}"


def _cached(key: str) -> Optional[Dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM portfolio_briefings WHERE scope_key = ?", (key,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        age = (datetime.now() - datetime.fromisoformat(row["created_at"])).total_seconds() / 60
        if age > CACHE_TTL_MIN:
            return None
    except (ValueError, TypeError):
        return None
    return {
        "headline": row["headline"],
        "themes": json.loads(row["themes_json"] or "[]"),
        "priority": json.loads(row["priority_json"] or "[]"),
        "watch_items": json.loads(row["watch_items_json"] or "[]"),
        "model": row["model"], "cached": True,
    }


def _gather(symbols: List[str]) -> Dict[str, Any]:
    import events_store as es
    import signal_data as sd

    events = [e for e in es.list_events_with_analysis(symbols, limit=120)
              if e["status"] not in es.CLOSED_STATUSES]

    compact = [{
        "id": e["id"], "company": e["symbol"], "event": e["event_label"],
        "category": e["category"], "risk": e["risk_level"],
        "detected": (e.get("detected_at") or "")[:10],
        "what_changed": (e.get("delta") or {}).get("summary_line") or e.get("headline", "")[:140],
        "finding": (e.get("finding") or "")[:200],
        "regulations": e.get("mandate_ids", []),
        "documents_ready": e.get("artifact_count", 0),
    } for e in events[:40]]

    return {
        "open_events": compact,
        "companies": [{k: c[k] for k in
                       ("symbol", "board_now", "board_delta", "independent_pct",
                        "events_open", "high_risk", "to_verify")}
                      for c in sd.company_cards(symbols)],
        "deadlines": sd.deadline_runway(symbols)["buckets"],
        "risk": sd.risk_breakdown(symbols)["by_risk"],
    }


_PROMPT = """You are the senior Company Secretary reviewing an entire portfolio before
the working day starts. You are not looking at one filing — you are looking across
{n} listed companies at everything currently open.

═══ OPEN GOVERNANCE WORK ═══
{data}

Write the briefing you would give your team. Look for what a per-event view misses:
patterns repeating across companies, several items that share one deadline or one
regulation, a company drifting in a direction worth naming, work that can be batched.

RULES:
1. Use ONLY the data above. Every company named is a real listed issuer — state
   only what the records show, never allege non-compliance or wrongdoing.
2. Frame everything as procedure ("a disclosure obligation is engaged"), never
   accusation ("the company failed to").
3. Be specific. Name companies and counts. No filler, no "in today's dynamic
   regulatory environment".
4. "priority" must reference real event ids from the data.

Return ONLY this JSON:
{{
  "headline": "<one sentence, max 150 chars: the single most useful thing to know this morning>",
  "themes": [
    {{"stat": "<the number that carries the point, e.g. '17 of 24' or '3 companies'>",
      "stat_label": "<2-4 words naming what the number counts, e.g. 'board governance events'>",
      "title": "<the theme in 5 words or fewer>",
      "detail": "<ONE short clause adding what the number does not say. Max 90 characters. No preamble, no restating the number.>",
      "companies": ["<symbols>"]}}
  ],
  "priority": [
    {{"event_id": <id from the data>, "company": "<symbol>",
      "why_first": "<one clause, max 90 characters, on why this is ahead of the rest>"}}
  ],
  "watch_items": [
    "<one forward-looking clause, max 100 characters>"
  ]
}}

Aim for 3-4 themes, 3 priority items, 2-3 watch items.

WRITE TIGHT. This is read at a glance before a working day, not studied. Every
theme leads with its number. No sentence may restate what the number already
says, and nothing may open with filler like "This indicates" or "A total of"."""


def generate(symbols: List[str], force: bool = False) -> Dict[str, Any]:
    """Portfolio-level briefing. Never raises."""
    init_briefing_db()
    if not symbols:
        return _empty()

    key = _scope_key(symbols, _fingerprint(symbols))
    if not force:
        hit = _cached(key)
        if hit:
            return hit

    from .client import call_json, gemini_available
    if not gemini_available():
        return _empty("The AI service is unavailable, so no portfolio briefing was generated.")

    data = _gather(symbols)
    if not data["open_events"]:
        return _empty("Nothing is currently open across the monitored companies.")

    prompt = _PROMPT.format(n=len(symbols),
                            data=json.dumps(data, indent=2, default=str)[:14000])
    res = call_json(prompt, role="intelligence", model="gemini-2.5-flash",
                    max_output_tokens=2000, temperature=0.3, timeout_ms=90_000)

    if not res["ok"] or not isinstance(res["data"], dict):
        return _empty("The portfolio briefing could not be generated.")

    d = res["data"]
    valid_ids = {e["id"] for e in data["open_events"]}

    themes = [{
        "title": str(t.get("title", ""))[:120],
        "stat": str(t.get("stat", ""))[:22],
        "stat_label": str(t.get("stat_label", ""))[:40],
        "detail": str(t.get("detail", ""))[:130],
        "companies": [str(c)[:12] for c in (t.get("companies") or [])[:6]],
    } for t in (d.get("themes") or [])[:4] if isinstance(t, dict) and t.get("title")]

    # Priorities must point at real events, or they are not actionable.
    priority = []
    for p in (d.get("priority") or [])[:4]:
        if not isinstance(p, dict):
            continue
        try:
            eid = int(p.get("event_id"))
        except (TypeError, ValueError):
            continue
        if eid in valid_ids:
            priority.append({"event_id": eid, "company": str(p.get("company", ""))[:12],
                             "why_first": str(p.get("why_first", ""))[:130]})

    watch = [str(w)[:130] for w in (d.get("watch_items") or [])[:3]]
    headline = str(d.get("headline", ""))[:200]

    conn = get_connection()
    conn.execute("""
        INSERT INTO portfolio_briefings
            (scope_key, headline, themes_json, priority_json, watch_items_json, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope_key) DO UPDATE SET
            headline=excluded.headline, themes_json=excluded.themes_json,
            priority_json=excluded.priority_json, watch_items_json=excluded.watch_items_json,
            model=excluded.model, created_at=excluded.created_at
    """, (key, headline, json.dumps(themes), json.dumps(priority), json.dumps(watch),
          res["model"], datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()

    return {"headline": headline, "themes": themes, "priority": priority,
            "watch_items": watch, "model": res["model"], "cached": False}


def _empty(msg: str = "") -> Dict[str, Any]:
    return {"headline": msg, "themes": [], "priority": [], "watch_items": [],
            "model": "none", "cached": False}
