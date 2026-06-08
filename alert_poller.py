"""Always-on price alert poller.

When the user has /prices open the alerts evaluate on every page poll
(see api_prices in main.py). This module covers the other case: the user
closed the tab and is just waiting for a Telegram ping. Every 60 seconds
during Indian market hours we:

  1. Pull the union of symbols across all enabled alerts.
  2. Fetch a single quote for each (cached at the NSE layer for 120s).
  3. Group alerts by user_id and call evaluate_user_alerts(), which
     handles cooldown + Telegram dispatch already.

Concurrency notes:
  - Gunicorn runs multiple workers; without coordination each would
    poll, doubling NSE load. We use an opportunistic DB-backed lease
    (cron_meta table + CAS update) so only one worker per minute does
    the work. If the holder dies, another worker takes over on the
    next tick — at worst we miss one cycle.
  - Cooldown in price_alerts already dedupes fires within 60 min, so
    a worst-case race just costs an extra DB write, not a spam Telegram.
"""

import os
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from database import get_connection
from price_alerts import evaluate_user_alerts
from stock_prices import fetch_stock_quote


POLL_INTERVAL_SEC = 60
LEASE_SEC = 50  # we own the work for ~one cycle; expires before the next tick

# Indian market hours: 09:00–15:30 IST, Mon–Fri (NSE holidays not modeled —
# we just poll and get null prices on holidays, which won't fire anything).
IST = timezone(timedelta(hours=5, minutes=30))


def _ensure_cron_table():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cron_meta (
            name TEXT PRIMARY KEY,
            last_run_at TEXT,
            holder TEXT,
            lease_expires_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def _try_claim_lease(name: str, holder: str) -> bool:
    """Optimistic CAS: claim the lease iff nobody else holds an unexpired one.
    Returns True if this worker won."""
    _ensure_cron_table()
    now = datetime.now()
    expires = now + timedelta(seconds=LEASE_SEC)
    now_iso = now.isoformat()
    expires_iso = expires.isoformat()

    conn = get_connection()
    try:
        # Insert if missing
        conn.execute(
            "INSERT OR IGNORE INTO cron_meta (name, last_run_at, holder, lease_expires_at) "
            "VALUES (?, '', '', '')",
            (name,),
        )
        # CAS update: only succeed if the existing lease is empty/expired
        cur = conn.execute(
            "UPDATE cron_meta SET holder = ?, lease_expires_at = ?, last_run_at = ? "
            "WHERE name = ? AND (lease_expires_at = '' OR lease_expires_at < ?)",
            (holder, expires_iso, now_iso, name, now_iso),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _is_market_hours() -> bool:
    now = datetime.now(IST)
    # Mon=0 ... Sun=6
    if now.weekday() >= 5:
        return False
    minute_of_day = now.hour * 60 + now.minute
    return (9 * 60) <= minute_of_day <= (15 * 60 + 30)


def _all_alerted_symbols() -> List[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM price_alerts WHERE enabled = 1"
    ).fetchall()
    conn.close()
    return [r["symbol"] for r in rows]


def _alerts_by_user() -> Dict[str, List[str]]:
    """user_id → list of distinct symbols this user has enabled alerts for.
    Used to scope the price snapshot per user when calling
    evaluate_user_alerts (it filters by user_id internally too)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT user_id, symbol FROM price_alerts WHERE enabled = 1"
    ).fetchall()
    conn.close()
    out: Dict[str, List[str]] = {}
    for r in rows:
        out.setdefault(r["user_id"], []).append(r["symbol"])
    return out


def _poll_once(holder: str) -> None:
    if not _try_claim_lease("alert_poll", holder):
        return
    if not _is_market_hours():
        return

    symbols = _all_alerted_symbols()
    if not symbols:
        return

    prices: Dict[str, Dict] = {}
    for sym in symbols:
        q = fetch_stock_quote(sym)
        if q:
            prices[sym] = q
        time.sleep(0.2)  # be polite to NSE

    if not prices:
        return

    user_map = _alerts_by_user()
    fired_total = 0
    for user_id in user_map:
        try:
            fired = evaluate_user_alerts(user_id, prices)
            fired_total += len(fired)
        except Exception as e:
            print(f"[POLLER] eval failed for {user_id}: {e}")
    if fired_total:
        print(f"[POLLER] cycle: {len(prices)} symbols, {fired_total} alerts fired.")


def _loop():
    holder = f"pid-{os.getpid()}"
    print(f"[POLLER] alert poller started ({holder})")
    while True:
        try:
            _poll_once(holder)
        except Exception as e:
            print(f"[POLLER] cycle error: {e}")
        time.sleep(POLL_INTERVAL_SEC)


_started = False
_started_lock = threading.Lock()


def start_alert_poller():
    """Idempotent — safe to call from each gunicorn worker on import.
    The DB-backed lease ensures only one worker actually does the work
    per cycle even when several call this at boot."""
    global _started
    with _started_lock:
        if _started:
            return
        _started = True
    t = threading.Thread(target=_loop, name="alert-poller", daemon=True)
    t.start()
