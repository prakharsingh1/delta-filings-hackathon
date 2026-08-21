"""Demo access path for the hackathon build.

Why this exists: every authed route on Delta Filings goes through
`login_required`, which redirects unpaid users to /checkout. A judge opening
the app would hit sign-in, then a payment wall. In demo mode we hand them a
pre-provisioned read-only session instead, so the first thing they see is the
Intelligence Center.

Demo mode defaults to ON in this branch (the hackathon build is never the
Railway production deploy). Set DELTA_DEMO_MODE=0 to turn it off.
"""

import os
from datetime import datetime

from database import get_connection, get_watchlist

# Companies the demo session monitors. Real listed issuers — see
# seed_demo.py for the rule on what we will and won't attribute to them.
DEMO_SYMBOLS = [
    # FMCG / IT
    "HINDUNILVR", "RELIANCE", "INFY", "ITC", "NESTLEIND",
    # Metals & mining — top 5 by market cap
    "TATASTEEL", "HINDALCO", "JSWSTEEL", "VEDL", "JINDALSTEL",
]

DEMO_USER_ID = "demo-judge"

DEMO_USER = {
    "id": DEMO_USER_ID,
    "name": "Demo Reviewer",
    "email": "demo@deltafilings.in",
    "industry": "FMCG & Metals",   # two sectors are monitored, not one
    "onboarded": True,
    "is_demo": True,
}


def demo_mode_enabled():
    """Demo mode is on unless explicitly disabled."""
    return os.environ.get("DELTA_DEMO_MODE", "1").strip() not in ("0", "false", "False", "")


def is_demo_user(user):
    return bool(user and user.get("id") == DEMO_USER_ID)


def activate(session):
    """Put the demo user into the Flask session and make sure it has data."""
    session["user_id"] = DEMO_USER_ID
    session["user_cache"] = dict(DEMO_USER)
    session["demo"] = True
    ensure_demo_watchlist()
    return dict(DEMO_USER)


def ensure_demo_watchlist():
    """Seed the demo user's watchlist if empty. Idempotent."""
    existing = {w["symbol"] for w in get_watchlist(DEMO_USER_ID)}
    missing = [s for s in DEMO_SYMBOLS if s not in existing]
    if not missing:
        return existing

    conn = get_connection()
    now = datetime.now().isoformat()
    for sym in missing:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (user_id, symbol, name, added_at) VALUES (?, ?, ?, ?)",
            (DEMO_USER_ID, sym, sym, now),
        )
    conn.commit()
    conn.close()
    return set(DEMO_SYMBOLS)
