"""Per-user draft history.

Every draft is owned by exactly one user. List/get/update/delete all filter
by `user_id` so user B can never see, fetch, or modify user A's drafts —
not even by guessing IDs. Routes 404 instead of 403 on ownership failure
so no metadata leaks (a 403 would confirm the draft exists).
"""

import json
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from database import get_connection


def init_drafts_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            company_symbol TEXT NOT NULL,
            company_name TEXT NOT NULL,
            filing_type TEXT NOT NULL,
            subject TEXT DEFAULT '',
            body TEXT DEFAULT '',
            annexures_json TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}',
            new_info TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_drafts_user "
        "ON user_drafts(user_id, updated_at DESC)"
    )

    # ── Migration: add updated_by_user_id for activity tracking ──
    cols = {r[1] for r in conn.execute("PRAGMA table_info(user_drafts)").fetchall()}
    if "updated_by_user_id" not in cols:
        conn.execute("ALTER TABLE user_drafts ADD COLUMN updated_by_user_id TEXT DEFAULT ''")
    if "updated_by_email" not in cols:
        conn.execute("ALTER TABLE user_drafts ADD COLUMN updated_by_email TEXT DEFAULT ''")

    # ── Collaborators table: owner is implicit on user_drafts.user_id;
    # this table holds the *additional* people invited to a draft.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_collaborators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            user_email TEXT DEFAULT '',
            user_name TEXT DEFAULT '',
            role TEXT NOT NULL DEFAULT 'editor',     -- 'editor' | 'viewer'
            designation TEXT DEFAULT '',              -- free-text display label
            added_at TEXT NOT NULL,
            added_by TEXT,
            UNIQUE(draft_id, user_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_draft_collab_user "
        "ON draft_collaborators(user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_draft_collab_draft "
        "ON draft_collaborators(draft_id)"
    )

    # ── Invite tokens: one-shot links the owner generates and shares.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            draft_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'editor',
            designation TEXT DEFAULT '',
            invited_email TEXT DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            used_at TEXT,
            used_by_user_id TEXT,
            revoked INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_draft_invites_token ON draft_invites(token)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_draft_invites_draft ON draft_invites(draft_id)")

    conn.commit()
    conn.close()


def _serialize(annexures, metadata):
    try:
        ax = json.dumps(annexures or [])
    except Exception:
        ax = "[]"
    try:
        mt = json.dumps(metadata or {})
    except Exception:
        mt = "{}"
    return ax, mt


def save_draft(user_id: str, company_symbol: str, company_name: str,
               filing_type: str, subject: str, body: str,
               annexures: List[Dict], metadata: Dict,
               new_info: str = "") -> int:
    """Insert a fresh draft. Returns the new id."""
    init_drafts_db()
    if not user_id:
        raise ValueError("user_id required")
    ax, mt = _serialize(annexures, metadata)
    now = datetime.now().isoformat()
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO user_drafts
               (user_id, company_symbol, company_name, filing_type,
                subject, body, annexures_json, metadata_json, new_info,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, (company_symbol or "").upper(), company_name or "",
             filing_type or "", subject or "", body or "",
             ax, mt, new_info or "", now, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_draft(draft_id: int, user_id: str,
                 user_email: str = "", **fields) -> bool:
    """Update an existing draft. Returns True if a row was changed.
    Allowed for the owner OR any collaborator with role='editor'.
    Returns False if the draft doesn't exist, or the user has no edit
    permission, or the user has only 'viewer' access — caller treats
    all of those as 404 to avoid leaking metadata."""
    init_drafts_db()
    if not user_id:
        return False
    role = get_effective_role(draft_id, user_id)
    if role not in ("owner", "editor"):
        return False

    allowed = {"company_symbol", "company_name", "filing_type", "subject",
               "body", "new_info"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v if v is not None else "")
    if "annexures" in fields:
        sets.append("annexures_json = ?")
        vals.append(json.dumps(fields["annexures"] or []))
    if "metadata" in fields:
        sets.append("metadata_json = ?")
        vals.append(json.dumps(fields["metadata"] or {}))
    if not sets:
        return False
    now = datetime.now().isoformat()
    sets.append("updated_at = ?")
    vals.append(now)
    sets.append("updated_by_user_id = ?")
    vals.append(user_id)
    sets.append("updated_by_email = ?")
    vals.append(user_email or "")
    vals.append(draft_id)

    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE user_drafts SET {', '.join(sets)} WHERE id = ?",
            vals,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_effective_role(draft_id: int, user_id: str) -> Optional[str]:
    """Return 'owner' / 'editor' / 'viewer' / None for this (draft, user).
    Source of truth for ALL permission checks in the drafts feature."""
    if not user_id:
        return None
    init_drafts_db()
    conn = get_connection()
    try:
        owner_row = conn.execute(
            "SELECT user_id FROM user_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if not owner_row:
            return None
        if owner_row["user_id"] == user_id:
            return "owner"
        collab_row = conn.execute(
            "SELECT role FROM draft_collaborators WHERE draft_id = ? AND user_id = ?",
            (draft_id, user_id),
        ).fetchone()
        if collab_row:
            return collab_row["role"] or "viewer"
    finally:
        conn.close()
    return None


def get_draft(draft_id: int, user_id: str) -> Optional[Dict]:
    """Load a specific draft IF the requester is the owner OR a collaborator.
    Returns None otherwise — never reveal that it exists for someone else."""
    role = get_effective_role(draft_id, user_id)
    if role is None:
        return None
    init_drafts_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM user_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["annexures"] = json.loads(d.get("annexures_json") or "[]")
    except Exception:
        d["annexures"] = []
    try:
        d["metadata"] = json.loads(d.get("metadata_json") or "{}")
    except Exception:
        d["metadata"] = {}
    d["effective_role"] = role
    d["is_owner"] = (role == "owner")
    return d


def list_user_drafts(user_id: str, limit: int = 100) -> List[Dict]:
    """Most recent first. Includes drafts the user OWNS plus drafts they
    were invited to as a collaborator. Each row is annotated with the
    user's effective role on that draft so the UI can render badges."""
    init_drafts_db()
    if not user_id:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT d.id, d.user_id AS owner_id, d.company_symbol, d.company_name,
                      d.filing_type, d.subject, d.created_at, d.updated_at,
                      d.updated_by_user_id, d.updated_by_email,
                      length(d.body) AS body_len, d.annexures_json,
                      CASE WHEN d.user_id = ? THEN 'owner'
                           ELSE COALESCE(c.role, 'viewer') END AS effective_role
               FROM user_drafts d
               LEFT JOIN draft_collaborators c
                      ON c.draft_id = d.id AND c.user_id = ?
               WHERE d.user_id = ? OR c.user_id = ?
               ORDER BY d.updated_at DESC
               LIMIT ?""",
            (user_id, user_id, user_id, user_id, limit),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            ax = json.loads(d.pop("annexures_json") or "[]")
            d["annexure_count"] = len(ax)
        except Exception:
            d["annexure_count"] = 0
        d["is_owner"] = (d.get("effective_role") == "owner")
        out.append(d)
    return out


def delete_draft(draft_id: int, user_id: str) -> bool:
    """Deletion is owner-only. Cascades collaborators + invites."""
    init_drafts_db()
    if not user_id:
        return False
    if get_effective_role(draft_id, user_id) != "owner":
        return False
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM user_drafts WHERE id = ? AND user_id = ?",
            (draft_id, user_id),
        )
        if cur.rowcount:
            conn.execute("DELETE FROM draft_collaborators WHERE draft_id = ?", (draft_id,))
            conn.execute("DELETE FROM draft_invites WHERE draft_id = ?", (draft_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Team management ──────────────────────────────────────────

VALID_ROLES = {"editor", "viewer"}


def list_team(draft_id: int, requester_user_id: str) -> Optional[Dict]:
    """Return the full team list (owner + collaborators + pending invites)
    for a draft, but only if the requester is part of it."""
    role = get_effective_role(draft_id, requester_user_id)
    if role is None:
        return None
    init_drafts_db()
    conn = get_connection()
    try:
        owner_row = conn.execute(
            "SELECT user_id FROM user_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        owner_id = owner_row["user_id"] if owner_row else None

        collabs = conn.execute(
            "SELECT id, user_id, user_email, user_name, role, designation, "
            "added_at FROM draft_collaborators WHERE draft_id = ? "
            "ORDER BY added_at ASC",
            (draft_id,),
        ).fetchall()

        invites = []
        if role == "owner":
            # Only the owner sees pending invite tokens (security: reduces
            # leakage of join URLs to other team members).
            inv_rows = conn.execute(
                "SELECT id, token, role, designation, invited_email, "
                "created_at, expires_at, used_at, used_by_user_id, revoked "
                "FROM draft_invites WHERE draft_id = ? "
                "AND used_at IS NULL AND revoked = 0 "
                "ORDER BY created_at DESC",
                (draft_id,),
            ).fetchall()
            invites = [dict(r) for r in inv_rows]
    finally:
        conn.close()

    return {
        "owner_id": owner_id,
        "viewer_role": role,
        "collaborators": [dict(c) for c in collabs],
        "pending_invites": invites,
    }


def add_collaborator(draft_id: int, owner_user_id: str,
                     new_user_id: str, new_user_email: str = "",
                     new_user_name: str = "", role: str = "editor",
                     designation: str = "") -> Optional[Dict]:
    """Add a known user as a collaborator. Owner-only. Idempotent if the
    user is already on the team (returns the existing row).

    Returns None on permission failure or on cap reached (5 collaborators)."""
    if get_effective_role(draft_id, owner_user_id) != "owner":
        return None
    if not new_user_id:
        return None
    if role not in VALID_ROLES:
        role = "editor"
    init_drafts_db()
    conn = get_connection()
    try:
        # Cap at 5 collaborators per draft.
        cnt = conn.execute(
            "SELECT COUNT(*) FROM draft_collaborators WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()[0]
        existing = conn.execute(
            "SELECT * FROM draft_collaborators WHERE draft_id = ? AND user_id = ?",
            (draft_id, new_user_id),
        ).fetchone()
        if existing:
            return dict(existing)
        if cnt >= 5:
            return None
        now = datetime.now().isoformat()
        conn.execute(
            """INSERT INTO draft_collaborators
               (draft_id, user_id, user_email, user_name, role, designation,
                added_at, added_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (draft_id, new_user_id, new_user_email, new_user_name,
             role, designation, now, owner_user_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM draft_collaborators WHERE draft_id = ? AND user_id = ?",
            (draft_id, new_user_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_collaborator_role(draft_id: int, owner_user_id: str,
                             collab_user_id: str,
                             role: Optional[str] = None,
                             designation: Optional[str] = None) -> bool:
    """Change role and/or designation. Owner-only. Either field may be None
    to leave it unchanged. role must be in VALID_ROLES if provided."""
    if get_effective_role(draft_id, owner_user_id) != "owner":
        return False
    init_drafts_db()
    sets, vals = [], []
    if role is not None:
        if role not in VALID_ROLES:
            return False
        sets.append("role = ?"); vals.append(role)
    if designation is not None:
        sets.append("designation = ?"); vals.append(designation)
    if not sets:
        return False
    vals.extend([draft_id, collab_user_id])
    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE draft_collaborators SET {', '.join(sets)} "
            f"WHERE draft_id = ? AND user_id = ?",
            vals,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def remove_collaborator(draft_id: int, owner_user_id: str,
                        collab_user_id: str) -> bool:
    if get_effective_role(draft_id, owner_user_id) != "owner":
        return False
    init_drafts_db()
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM draft_collaborators WHERE draft_id = ? AND user_id = ?",
            (draft_id, collab_user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Invite tokens ────────────────────────────────────────────

def create_invite(draft_id: int, owner_user_id: str,
                  role: str = "editor", designation: str = "",
                  invited_email: str = "",
                  expires_in_days: int = 14) -> Optional[Dict]:
    """Owner-only. Generates a single-use opaque token. Anyone who hits
    /draft/invite/<token> while signed in is added to the team; if not
    signed in, the token is stashed in their session and claimed once
    they sign up or sign in."""
    if get_effective_role(draft_id, owner_user_id) != "owner":
        return None
    if role not in VALID_ROLES:
        role = "editor"
    init_drafts_db()
    token = secrets.token_urlsafe(24)
    now = datetime.now()
    expires_at = (now + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO draft_invites
               (token, draft_id, role, designation, invited_email,
                created_by, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (token, draft_id, role, designation, invited_email,
             owner_user_id, now.isoformat(), expires_at),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "token": token, "draft_id": draft_id, "role": role,
        "designation": designation, "invited_email": invited_email,
        "expires_at": expires_at,
    }


def get_invite(token: str) -> Optional[Dict]:
    if not token:
        return None
    init_drafts_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM draft_invites WHERE token = ?", (token,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def revoke_invite(token: str, owner_user_id: str) -> bool:
    inv = get_invite(token)
    if not inv:
        return False
    if get_effective_role(inv["draft_id"], owner_user_id) != "owner":
        return False
    init_drafts_db()
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE draft_invites SET revoked = 1 WHERE token = ?", (token,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_teams_overview(user_id: str, max_drafts: int = 30) -> List[Dict]:
    """One-round-trip overview of every draft this user is part of (owns
    or was invited to), with the full team list flattened in. Powers the
    'Teams & sharing' panel on the Draft home page."""
    if not user_id:
        return []
    init_drafts_db()
    conn = get_connection()
    try:
        # First: drafts the user is on
        drafts = conn.execute(
            """SELECT d.id, d.user_id AS owner_id, d.subject, d.company_symbol,
                      d.filing_type, d.updated_at, d.updated_by_email,
                      CASE WHEN d.user_id = ? THEN 'owner'
                           ELSE COALESCE(c.role, 'viewer') END AS my_role
               FROM user_drafts d
               LEFT JOIN draft_collaborators c
                      ON c.draft_id = d.id AND c.user_id = ?
               WHERE d.user_id = ? OR c.user_id = ?
               ORDER BY d.updated_at DESC
               LIMIT ?""",
            (user_id, user_id, user_id, user_id, max_drafts),
        ).fetchall()
        if not drafts:
            return []

        # Second: every collaborator on those drafts in one query
        ids = [d["id"] for d in drafts]
        placeholders = ",".join("?" for _ in ids)
        collabs = conn.execute(
            f"SELECT * FROM draft_collaborators WHERE draft_id IN ({placeholders})",
            ids,
        ).fetchall()
    finally:
        conn.close()

    # Index collaborators by draft_id
    by_draft: Dict[int, List[Dict]] = {}
    for c in collabs:
        by_draft.setdefault(c["draft_id"], []).append(dict(c))

    out = []
    for d in drafts:
        members = list(by_draft.get(d["id"], []))
        out.append({
            "id": d["id"],
            "owner_id": d["owner_id"],
            "subject": d["subject"],
            "company_symbol": d["company_symbol"],
            "filing_type": d["filing_type"],
            "updated_at": d["updated_at"],
            "updated_by_email": d["updated_by_email"],
            "my_role": d["my_role"],
            "is_owner": (d["my_role"] == "owner"),
            "team_size": len(members) + 1,    # +1 for the owner
            "collaborators": members,
        })
    return out


def claim_invite(token: str, user_id: str, user_email: str = "",
                 user_name: str = "") -> Tuple[bool, str, Optional[int]]:
    """Used when a user hits the invite URL while authenticated. Validates
    token (not used / not revoked / not expired / draft still exists) and
    adds the user as a collaborator. Returns (ok, message, draft_id).

    Idempotent: if the user is already on the team, returns ok=True with
    a 'already_member' message."""
    inv = get_invite(token)
    if not inv:
        return False, "Invite link is invalid.", None
    if inv.get("revoked"):
        return False, "This invite has been revoked.", None
    if inv.get("used_at"):
        return False, "This invite link has already been used.", None
    if inv.get("expires_at"):
        try:
            if datetime.fromisoformat(inv["expires_at"]) < datetime.now():
                return False, "This invite link has expired.", None
        except Exception:
            pass

    draft_id = inv["draft_id"]
    init_drafts_db()
    conn = get_connection()
    try:
        owner = conn.execute(
            "SELECT user_id FROM user_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if not owner:
            return False, "Draft no longer exists.", None
        owner_id = owner["user_id"]
        if owner_id == user_id:
            return True, "You're already the owner of this draft.", draft_id

        existing = conn.execute(
            "SELECT id FROM draft_collaborators "
            "WHERE draft_id = ? AND user_id = ?",
            (draft_id, user_id),
        ).fetchone()

        now = datetime.now().isoformat()
        if not existing:
            # Cap: 5 collaborators (matches add_collaborator).
            cnt = conn.execute(
                "SELECT COUNT(*) FROM draft_collaborators WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()[0]
            if cnt >= 5:
                return False, "This draft already has the maximum number of collaborators.", draft_id
            conn.execute(
                """INSERT INTO draft_collaborators
                   (draft_id, user_id, user_email, user_name, role,
                    designation, added_at, added_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (draft_id, user_id, user_email, user_name,
                 inv.get("role") or "editor",
                 inv.get("designation") or "",
                 now, owner_id),
            )

        # Mark the invite as used regardless (even if user was already a
        # member — closes the link to others).
        conn.execute(
            "UPDATE draft_invites SET used_at = ?, used_by_user_id = ? "
            "WHERE token = ?",
            (now, user_id, token),
        )
        conn.commit()
    finally:
        conn.close()

    return True, "You've been added to the draft team.", draft_id
