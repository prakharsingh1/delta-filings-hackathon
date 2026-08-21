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
    # Top 5 Indian FMCG issuers by market capitalisation. Single sector on
    # purpose — a Company Secretary works one industry's regulatory calendar.
    "HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR",
]

DEMO_USER_ID = "demo-judge"

DEMO_USER = {
    "id": DEMO_USER_ID,
    "name": "Prakhar",
    "email": "demo@deltafilings.in",
    "industry": "FMCG",
    "onboarded": True,
    "is_demo": True,
}


def demo_mode_enabled():
    """Demo mode is on unless explicitly disabled."""
    return os.environ.get("DELTA_DEMO_MODE", "1").strip() not in ("0", "false", "False", "")


def is_demo_user(user):
    return bool(user and user.get("id") == DEMO_USER_ID)


def refresh_if_stale(session, user):
    """Re-seed the session cache when the demo profile has changed.

    The signed-in profile is cached in the Flask session, so editing
    DEMO_USER (name, industry) left existing browser sessions showing the old
    values until the cookie was cleared. Cheap dict compare per request.
    """
    if not is_demo_user(user):
        return user
    if all(user.get(k) == v for k, v in DEMO_USER.items()):
        return user
    session["user_cache"] = dict(DEMO_USER)
    return dict(DEMO_USER)


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
