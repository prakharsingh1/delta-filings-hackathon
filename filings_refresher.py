"""Periodic filings refresher.

Nothing else in the app keeps filings fresh. Onboarding seeds a user's
companies once, and `/company/<symbol>` lazily backfills only the company a
user actually opens. Without this loop, every watchlist company nobody clicks
drifts stale indefinitely — which is how four of five Chemicals companies ended
up 3-4 months behind NSE while the dashboard still looked current.

Safe across gunicorn workers: the same DB-backed lease the alert poller uses
means only one worker performs a cycle, even though every worker starts this
thread at boot.
"""

import os
import time
import threading
from datetime import datetime

from database import get_all_watchlist_symbols, get_filings_last_fetched
from alert_poller import _try_claim_lease

# How stale a symbol must be before we re-fetch it.
STALE_AFTER_HOURS = int(os.environ.get("DELTA_FILINGS_STALE_HOURS", "12"))
# How long to wait between cycles.
CYCLE_MINUTES = int(os.environ.get("DELTA_FILINGS_CYCLE_MINUTES", "60"))
# Politeness gap between symbols — NSE rate-limits aggressively and each
# process_filings() call already makes several requests.
GAP_SECONDS = int(os.environ.get("DELTA_FILINGS_GAP_SECONDS", "20"))
LEASE_NAME = "filings-refresh"

_started = False
_started_lock = threading.Lock()


def _is_stale(symbol):
    meta = get_filings_last_fetched(symbol)
    if not meta or not meta.get("last_fetched_at"):
        return True
    try:
        last = datetime.fromisoformat(meta["last_fetched_at"])
    except (TypeError, ValueError):
        return True
    return (datetime.now() - last).total_seconds() >= STALE_AFTER_HOURS * 3600


def _cycle(process_fn):
    # Counterparty symbols ride along so the risk desk sees fresh filings too.
    try:
        from risk import all_counterparty_symbols
        extra = all_counterparty_symbols()
    except Exception:
        extra = []
    symbols = sorted(set(get_all_watchlist_symbols()) | set(extra))
    due = [s for s in symbols if _is_stale(s)]
    if not due:
        return
    print(f"[FILINGS-REFRESH] {len(due)}/{len(symbols)} symbols stale (>{STALE_AFTER_HOURS}h): {due}")
    for sym in due:
        try:
            process_fn(sym)
        except Exception as e:
            print(f"[FILINGS-REFRESH] {sym} failed: {e}")
        time.sleep(GAP_SECONDS)
    print(f"[FILINGS-REFRESH] cycle complete ({len(due)} symbols)")


def _loop(process_fn):
    holder = f"pid-{os.getpid()}"
    # Stagger the first run so a deploy's workers don't all wake together and
    # so boot isn't competing with onboarding fetches for the DB.
    time.sleep(120)
    while True:
        try:
            if _try_claim_lease(LEASE_NAME, holder):
                _cycle(process_fn)
        except Exception as e:
            print(f"[FILINGS-REFRESH] cycle error: {e}")
        time.sleep(CYCLE_MINUTES * 60)


def start_filings_refresher(process_fn):
    """Idempotent — safe to call from every gunicorn worker at import time.

    `process_fn` is injected (main.process_filings) to avoid a circular import.
    """
    global _started
    with _started_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, args=(process_fn,),
                     name="filings-refresher", daemon=True).start()
    print(f"[FILINGS-REFRESH] started (every {CYCLE_MINUTES}m, stale>{STALE_AFTER_HOURS}h)")
