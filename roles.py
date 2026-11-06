"""User roles — who the user is decides where they land and what the top
nav shows first. Every module stays reachable from the "All tools" menu, so
a role only changes emphasis, never access.

Stored in SQLite (not Supabase metadata) so it works for the demo session and
survives without an auth round-trip. Users onboarded before roles existed
have no row and are treated as listed-company compliance officers — the
product they signed up for.
"""
from __future__ import annotations

from datetime import datetime

from database import get_connection

ROLES = {
    "cro": {
        "label": "Risk officer",
        "blurb": "Watch borrowers, vendors and investees for early warning signs.",
        "home": "/risk",
    },
    "compliance": {
        "label": "Compliance officer, listed company",
        "blurb": "Exchange disclosures, insider-trading controls and the LODR calendar.",
        "home": "/dashboard",
    },
    "cs": {
        "label": "Company secretary",
        "blurb": "Your company's filings, meetings, registers and deadlines.",
        "home": "/workspace",
    },
    "pcs": {
        "label": "Practising CS or firm",
        "blurb": "Every client's compliance calendar in one place.",
        "home": "/workspace",
    },
}
DEFAULT_ROLE = "compliance"

# Every destination in the product. `key` doubles as the active_page value.
MODULES = {
    "dashboard":     {"label": "Dashboard", "href": "/dashboard", "group": "Monitor"},
    "intelligence":  {"label": "Signal", "href": "/intelligence", "group": "Monitor"},
    "announcements": {"label": "Disclosures", "href": None, "group": "Monitor"},
    "compare":       {"label": "Compare", "href": None, "group": "Monitor"},
    "prices":        {"label": "Stock prices", "href": "/prices", "group": "Monitor"},
    "din_search":    {"label": "DIN search", "href": "/din-search", "group": "Monitor"},
    "news":          {"label": "Regulatory news", "href": "/news", "group": "Monitor"},
    "workspace":     {"label": "Workspace", "href": "/workspace", "group": "Comply"},
    "compliance":    {"label": "Listed compliance", "href": "/compliance", "group": "Comply"},
    "rulebook":      {"label": "Rulebook", "href": "/workspace/rulebook", "group": "Comply"},
    "sdd":           {"label": "Insider trading (SDD)", "href": "/sdd", "group": "Comply"},
    "requests":      {"label": "Document requests", "href": "/requests", "group": "Comply"},
    "risk":          {"label": "Risk desk", "href": "/risk", "group": "Risk"},
    "risk_alerts":   {"label": "Alerts", "href": "/risk/alerts", "group": "Risk"},
    "risk_network":  {"label": "Director network", "href": "/risk/network", "group": "Risk"},
    "risk_report":   {"label": "Committee report", "href": "/risk/report", "group": "Risk"},
    "draft":         {"label": "Draft", "href": "/draft", "group": "Create"},
}
MODULE_GROUPS = ["Monitor", "Comply", "Risk", "Create"]

# Top-nav labels: one or two words, so a role's row fits on a laptop screen.
NAV_LABELS = {
    "compliance": "Compliance", "sdd": "Insider trading", "news": "News", "risk": "Risk desk",
    "risk_alerts": "Alerts", "risk_network": "Network", "requests": "Requests",
}

# Top-nav items per role, in order. Everything else lives in "All tools".
NAV = {
    "cro": ["risk", "risk_alerts", "risk_network", "news", "intelligence", "announcements"],
    "compliance": ["dashboard", "intelligence", "draft", "announcements", "compliance", "sdd", "news"],
    "cs": ["workspace", "news", "draft", "rulebook", "requests"],
    "pcs": ["workspace", "requests", "news", "draft", "rulebook"],
}


def init_roles_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def get_role(user_id):
    if not user_id:
        return DEFAULT_ROLE
    conn = get_connection()
    row = conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["role"] if row and row["role"] in ROLES else DEFAULT_ROLE


def set_role(user_id, role):
    if role not in ROLES or not user_id:
        return False
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO user_roles (user_id, role, updated_at) VALUES (?,?,?)",
                 (user_id, role, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()
    return True


def home_for(user_id):
    return ROLES[get_role(user_id)]["home"]


def nav_for(role):
    return NAV.get(role, NAV[DEFAULT_ROLE])
