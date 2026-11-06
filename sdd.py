"""Structured Digital Database (SEBI PIT Reg 3(5)/(6)) and insider-trading desk.

Reg 3(6) requires the SDD to be kept internally "with adequate internal
controls and checks such as time stamping and audit trails to ensure
non-tampering". So entries here are append-only: there is no edit or delete
path. A mistake is fixed by a correction entry that points at the original.
Each entry stores the SHA-256 of (previous entry's hash + its own canonical
contents); `verify_chain` recomputes the chain and names the first row that
does not match, which is what an auditor asks for.

Also covers the other day-to-day PIT work: designated persons, trading window
closures, and pre-clearance of trades (valid for 7 trading days, refused while
the window is closed).

Times are Indian Standard Time. The server may run in UTC, but a compliance
officer reads and types IST, and the trading window is an IST calendar.
"""
from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

from database import get_connection
import entities as ents

GENESIS = "0" * 64
RECIPIENT_TYPES = {
    "designated": "Designated person",
    "connected": "Connected person",
    "external": "External (advisor, auditor, banker, other fiduciary)",
}
RELATIONS = {"self": "Designated person", "immediate_relative": "Immediate relative"}
TXN_TYPES = {"buy": "Buy", "sell": "Sell", "pledge": "Pledge", "other": "Other"}
PRECLEARANCE_VALID_TRADING_DAYS = 7
# India has no daylight saving, so a fixed offset is exact and needs no tzdata.
IST = timezone(timedelta(hours=5, minutes=30))
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ID_PREFIX = "ID: "
MAX_INT = 2**63 - 1  # largest SQLite INTEGER


def init_sdd_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sdd_upsi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            nature TEXT DEFAULT '',
            opened_at TEXT NOT NULL,
            public_on TEXT DEFAULT '',
            created_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sdd_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            upsi_id INTEGER NOT NULL,
            action TEXT NOT NULL DEFAULT 'share',
            corrects_id INTEGER,
            shared_by_name TEXT NOT NULL,
            shared_by_pan TEXT DEFAULT '',
            shared_with_name TEXT NOT NULL,
            shared_with_pan TEXT DEFAULT '',
            shared_with_org TEXT DEFAULT '',
            recipient_type TEXT NOT NULL,
            purpose TEXT DEFAULT '',
            shared_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            recorded_by TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sdd_entity ON sdd_entries(entity_id, id);

        CREATE TABLE IF NOT EXISTS pit_persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            pan TEXT DEFAULT '',
            designation TEXT DEFAULT '',
            relation TEXT NOT NULL DEFAULT 'self',
            related_to INTEGER,
            email TEXT DEFAULT '',
            added_at TEXT NOT NULL,
            removed_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS pit_windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            closes_on TEXT NOT NULL,
            opens_on TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pit_preclearance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            txn_type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            est_value INTEGER DEFAULT 0,
            requested_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            decided_at TEXT DEFAULT '',
            decided_by TEXT DEFAULT '',
            valid_until TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        );
    """)
    # UPSI records, windows, pre-clearance and the designated persons list are
    # updated in place, so their history is a second hash chain, and the
    # anchor records how long both chains are, so deleting the newest entries
    # shows up too (a chain alone only proves what is left is consistent).
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            ref_id INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            recorded_by TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pit_log_entity ON pit_log(entity_id, id);
        CREATE TABLE IF NOT EXISTS sdd_anchor (
            entity_id INTEGER PRIMARY KEY,
            entries INTEGER NOT NULL,
            entries_head TEXT NOT NULL,
            log_entries INTEGER NOT NULL,
            log_head TEXT NOT NULL,
            mac TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        );
    """)
    # UPSI created before the log existed: log them as they stand now.
    for u in conn.execute("""SELECT * FROM sdd_upsi u WHERE NOT EXISTS
                             (SELECT 1 FROM pit_log l WHERE l.entity_id = u.entity_id AND l.ref_id = u.id
                              AND l.kind IN ('upsi_opened', 'upsi_baseline'))""").fetchall():
        _log(conn, u["entity_id"], "upsi_baseline", u["id"], _upsi_state(u), "system")
    conn.commit()
    # The approver's note used to overwrite the requester's note; keep both.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pit_preclearance)")}
    if "decision_note" not in cols:
        try:
            conn.execute("ALTER TABLE pit_preclearance ADD COLUMN decision_note TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # another worker added it first
    conn.commit()
    conn.close()


# ── Small helpers ───────────────────────────────────────────────────────────

def _now_dt():
    return datetime.now(IST).replace(tzinfo=None, microsecond=0)


def _now():
    return _now_dt().isoformat(timespec="seconds")


def today_ist():
    return datetime.now(IST).date()


def _owned(user_id, entity_id):
    return ents.get_entity(user_id, entity_id) is not None


def _int(value, default=0):
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, float):
        if not value.is_integer():
            return default
        n = int(value)
    else:
        try:
            n = int(str(value).strip())
        except ValueError:
            return default
    # SQLite integers are 64-bit; a longer number would crash the query.
    return n if abs(n) <= MAX_INT else default


def _text(value, limit):
    """Trimmed text from a JSON value; a number or null from a client is not a crash."""
    return "" if value is None or isinstance(value, (dict, list)) else str(value).strip()[:limit]


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(IST).replace(tzinfo=None)
    return dt.replace(microsecond=0)


def human_date(d):
    """'1 Oct 2026' — the way a CS writes a date in a board note."""
    d = _parse_date(d) if not isinstance(d, date) else d
    return f"{d.day} {d.strftime('%b %Y')}" if d else ""


def _clean_pan(value, whose=""):
    pan = re.sub(r"\s+", "", str(value or "")).upper()
    if pan and not PAN_RE.match(pan):
        label = f"{whose} PAN" if whose else "PAN"
        raise ValueError(f"{label} should look like ABCDE1234F (5 letters, 4 digits, 1 letter).")
    return pan


def mask_pan(pan):
    """First 2 + last 3 of a PAN. An alternative identifier (stored as
    'ID: …') is masked the same way, since it may be an Aadhaar or passport."""
    pan = (pan or "").strip()
    if pan.startswith(ID_PREFIX):
        ident = pan[len(ID_PREFIX):]
        return ID_PREFIX + (ident[:2] + "•••" + ident[-3:] if len(ident) > 6 else ident)
    return pan[:2] + "•••••" + pan[-3:] if len(pan) == 10 else pan


# ── UPSI items ──────────────────────────────────────────────────────────────

def create_upsi(user_id, entity_id, title, nature=""):
    if not _owned(user_id, entity_id):
        return None
    title = _text(title, 200)
    if not title:
        raise ValueError("Describe the UPSI (e.g. 'Q2 FY27 results').")
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO sdd_upsi (entity_id, title, nature, opened_at, created_by) VALUES (?,?,?,?,?)",
        (entity_id, title, _text(nature, 500), _now(), user_id))
    uid = cur.lastrowid
    _log(conn, entity_id, "upsi_opened", uid,
         _upsi_state(conn.execute("SELECT * FROM sdd_upsi WHERE id = ?", (uid,)).fetchone()), user_id)
    conn.commit()
    conn.close()
    return uid


def mark_upsi_public(user_id, entity_id, upsi_id, public_on):
    """Record the day the information became generally available. Returns
    False when the UPSI is not this entity's; raises when already public."""
    if not _owned(user_id, entity_id):
        return False
    day = _parse_date(public_on) if public_on else today_ist()
    if not day:
        raise ValueError("Give the date the information became public.")
    if day > today_ist():
        raise ValueError("The public date cannot be in the future.")
    conn = get_connection()
    try:
        row = conn.execute("SELECT public_on FROM sdd_upsi WHERE id = ? AND entity_id = ?",
                           (upsi_id, entity_id)).fetchone()
        if not row:
            return False
        if row["public_on"]:
            raise ValueError(f"Already marked public on {human_date(row['public_on'])}.")
        conn.execute("UPDATE sdd_upsi SET public_on = ? WHERE id = ? AND entity_id = ? AND public_on = ''",
                     (day.isoformat(), upsi_id, entity_id))
        _log(conn, entity_id, "upsi_public", upsi_id,
             _upsi_state(conn.execute("SELECT * FROM sdd_upsi WHERE id = ?", (upsi_id,)).fetchone()), user_id)
        conn.commit()
        return True
    finally:
        conn.close()


def list_upsi(user_id, entity_id):
    if not _owned(user_id, entity_id):
        return []
    conn = get_connection()
    rows = conn.execute(
        """SELECT u.*, (SELECT COUNT(*) FROM sdd_entries e WHERE e.upsi_id = u.id) AS entries
           FROM sdd_upsi u WHERE entity_id = ? ORDER BY opened_at DESC, id DESC""", (entity_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Hash chain ──────────────────────────────────────────────────────────────

_HASHED_FIELDS = ("entity_id", "upsi_id", "action", "corrects_id", "shared_by_name", "shared_by_pan",
                  "shared_with_name", "shared_with_pan", "shared_with_org", "recipient_type",
                  "purpose", "shared_at", "recorded_at", "recorded_by")


def _entry_hash(prev_hash, fields):
    canonical = json.dumps({k: fields.get(k) for k in _HASHED_FIELDS}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def _upsi_state(row):
    return {k: row[k] for k in ("title", "nature", "opened_at", "public_on", "created_by")}


def _log_hash(prev_hash, rec):
    canonical = json.dumps(rec, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def _mac(text):
    """HMAC of the anchor with DELTA_SDD_KEY, a secret kept outside the
    database, so rewriting the database alone cannot forge a valid anchor.
    Empty when no key is configured. Never change the key once set."""
    key = os.environ.get("DELTA_SDD_KEY", "").strip()
    return hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest() if key else ""


def _chain_state(conn, entity_id):
    def tail(table):
        n = conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE entity_id = ?", (entity_id,)).fetchone()["c"]
        last = conn.execute(f"SELECT hash FROM {table} WHERE entity_id = ? ORDER BY id DESC LIMIT 1",
                            (entity_id,)).fetchone()
        return n, (last["hash"] if last else GENESIS)
    return (*tail("sdd_entries"), *tail("pit_log"))


def _anchor_text(entity_id, n, head, ln, lhead):
    return f"{int(entity_id)}|{n}|{head}|{ln}|{lhead}"


def _write_anchor(conn, entity_id):
    n, head, ln, lhead = _chain_state(conn, entity_id)
    conn.execute(
        """INSERT INTO sdd_anchor (entity_id, entries, entries_head, log_entries, log_head, mac, updated_at)
           VALUES (?,?,?,?,?,?,?) ON CONFLICT(entity_id) DO UPDATE SET entries = excluded.entries,
           entries_head = excluded.entries_head, log_entries = excluded.log_entries, log_head = excluded.log_head,
           mac = excluded.mac, updated_at = excluded.updated_at""",
        (int(entity_id), n, head, ln, lhead, _mac(_anchor_text(entity_id, n, head, ln, lhead)), _now()))


def _row(conn, table, row_id):
    r = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return dict(r) if r else {}


def _log(conn, entity_id, kind, ref_id, payload, user_id):
    """Append to the activity chain and move the anchor. Call inside the same
    transaction as the change it records, after that change's first write
    (which takes SQLite's write lock, so two appends cannot interleave)."""
    last = conn.execute("SELECT hash FROM pit_log WHERE entity_id = ? ORDER BY id DESC LIMIT 1",
                        (entity_id,)).fetchone()
    prev = last["hash"] if last else GENESIS
    rec = {"entity_id": int(entity_id), "kind": kind, "ref_id": int(ref_id), "payload": payload,
           "recorded_at": _now(), "recorded_by": str(user_id)}
    conn.execute(
        "INSERT INTO pit_log (entity_id, kind, ref_id, payload_json, recorded_at, recorded_by, prev_hash, hash) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (rec["entity_id"], kind, rec["ref_id"], json.dumps(payload, sort_keys=True, ensure_ascii=False),
         rec["recorded_at"], rec["recorded_by"], prev, _log_hash(prev, rec)))
    _write_anchor(conn, entity_id)


def record_entry(user_id, entity_id, data):
    """Append one sharing event. Returns the new entry id."""
    if not _owned(user_id, entity_id):
        return None
    entity_id = int(entity_id)  # the hash must see the same type SQLite returns
    upsi_id = _int(data.get("upsi_id"))
    rtype = data.get("recipient_type")
    if not isinstance(rtype, str) or rtype not in RECIPIENT_TYPES:
        raise ValueError("Choose who the information was shared with.")
    by_name = _text(data.get("shared_by_name"), 160)
    with_name = _text(data.get("shared_with_name"), 160)
    if not by_name or not with_name:
        raise ValueError("Both the sharer and the recipient are required.")
    by_pan = _clean_pan(data.get("shared_by_pan"), "Sharer's")
    with_pan = _clean_pan(data.get("shared_with_pan"), "Recipient's")
    ident = " ".join(_text(data.get("identifier_note"), 200).split())[:36]
    if not with_pan and not ident:
        # Reg 3(5): PAN, or another identifier authorised by law where PAN is unavailable.
        raise ValueError("Recipient PAN is required (or state the alternative identifier).")

    now = _now_dt()
    shared_at = now
    if data.get("shared_at"):
        shared_at = _parse_dt(data.get("shared_at"))
        if not shared_at:
            raise ValueError("Give the date and time the information was shared.")
        if shared_at > now + timedelta(minutes=5):
            raise ValueError("The sharing time cannot be in the future.")

    corrects_id = _int(data.get("corrects_id")) or None
    if data.get("corrects_id") and not corrects_id:
        raise ValueError("The entry being corrected does not exist.")
    action = "correction" if corrects_id else "share"

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM sdd_upsi WHERE id = ? AND entity_id = ?",
                            (upsi_id, entity_id)).fetchone():
            raise ValueError("Pick the UPSI this entry belongs to.")
        if corrects_id and not conn.execute("SELECT 1 FROM sdd_entries WHERE id = ? AND entity_id = ?",
                                            (corrects_id, entity_id)).fetchone():
            raise ValueError("The entry being corrected does not exist.")
        last = conn.execute("SELECT hash FROM sdd_entries WHERE entity_id = ? ORDER BY id DESC LIMIT 1",
                            (entity_id,)).fetchone()
        prev_hash = last["hash"] if last else GENESIS
        fields = {
            "entity_id": entity_id, "upsi_id": upsi_id, "action": action,
            "corrects_id": corrects_id,
            "shared_by_name": by_name,
            "shared_by_pan": by_pan,
            "shared_with_name": with_name,
            "shared_with_pan": with_pan or f"{ID_PREFIX}{ident}",
            "shared_with_org": _text(data.get("shared_with_org"), 160),
            "recipient_type": rtype,
            "purpose": _text(data.get("purpose"), 500),
            "shared_at": shared_at.isoformat(timespec="seconds"),
            "recorded_at": now.isoformat(timespec="seconds"),
            "recorded_by": user_id,
        }
        h = _entry_hash(prev_hash, fields)
        cur = conn.execute(
            f"INSERT INTO sdd_entries ({', '.join(_HASHED_FIELDS)}, prev_hash, hash) "
            f"VALUES ({', '.join('?' for _ in _HASHED_FIELDS)}, ?, ?)",
            (*[fields[k] for k in _HASHED_FIELDS], prev_hash, h))
        _write_anchor(conn, entity_id)
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_entries(user_id, entity_id, upsi_id=None, mask=True):
    if not _owned(user_id, entity_id):
        return []
    conn = get_connection()
    q = """SELECT e.*, u.title AS upsi_title, u.nature AS upsi_nature, u.public_on AS upsi_public_on
           FROM sdd_entries e JOIN sdd_upsi u ON u.id = e.upsi_id WHERE e.entity_id = ?"""
    args = [entity_id]
    if upsi_id:
        q += " AND e.upsi_id = ?"
        args.append(upsi_id)
    rows = conn.execute(q + " ORDER BY e.id DESC", args).fetchall()
    conn.close()
    out = []
    corrected_by = {}
    for r in rows:
        d = dict(r)
        if mask:
            d["shared_by_pan"] = mask_pan(d["shared_by_pan"])
            d["shared_with_pan"] = mask_pan(d["shared_with_pan"])
        d["recipient_label"] = RECIPIENT_TYPES.get(d["recipient_type"], d["recipient_type"])
        # Auditors look at the gap between the event and the entry.
        shared, recorded = _parse_dt(d["shared_at"]), _parse_dt(d["recorded_at"])
        d["logged_after_days"] = (recorded - shared).days if shared and recorded and recorded > shared else 0
        if d["corrects_id"]:
            corrected_by.setdefault(d["corrects_id"], []).append(d["id"])
        out.append(d)
    for d in out:
        d["corrected_by"] = sorted(corrected_by.get(d["id"], []))
    return out


def get_entry(user_id, entity_id, entry_id):
    """One entry, unmasked — used to prefill a correction."""
    if not _owned(user_id, entity_id):
        return None
    conn = get_connection()
    row = conn.execute("SELECT * FROM sdd_entries WHERE id = ? AND entity_id = ?", (entry_id, entity_id)).fetchone()
    conn.close()
    if not row:
        return None
    d = {k: row[k] for k in row.keys() if k not in ("prev_hash", "hash")}
    pan = d.get("shared_with_pan") or ""
    d["no_pan"] = pan.startswith(ID_PREFIX)
    d["identifier_note"] = pan[len(ID_PREFIX):] if d["no_pan"] else ""
    if d["no_pan"]:
        d["shared_with_pan"] = ""
    return d


def verify_chain(user_id, entity_id):
    """Recompute both chains, compare their lengths and heads with the anchor,
    and check every UPSI record against its last logged state.
    Returns {ok, entries, broken_at, head, problem, checked_at}."""
    if not _owned(user_id, entity_id):
        return None
    entity_id = int(entity_id)
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM sdd_entries WHERE entity_id = ? ORDER BY id", (entity_id,)).fetchall()
        logs = conn.execute("SELECT * FROM pit_log WHERE entity_id = ? ORDER BY id", (entity_id,)).fetchall()
        anchor = conn.execute("SELECT * FROM sdd_anchor WHERE entity_id = ?", (entity_id,)).fetchone()
        upsi = conn.execute("SELECT * FROM sdd_upsi WHERE entity_id = ?", (entity_id,)).fetchall()
        if anchor is None and (rows or logs):
            # Records from before the anchor existed: anchor them as they are.
            _write_anchor(conn, entity_id)
            conn.commit()
            anchor = conn.execute("SELECT * FROM sdd_anchor WHERE entity_id = ?", (entity_id,)).fetchone()
    finally:
        conn.close()

    def broken(problem, at=None):
        return {"ok": False, "entries": len(rows), "broken_at": at, "head": None,
                "problem": problem, "checked_at": _now()}

    prev = GENESIS
    for r in rows:
        d = dict(r)
        if d["prev_hash"] != prev or _entry_hash(prev, d) != d["hash"]:
            return broken(f"Entry #{d['id']} no longer matches its fingerprint. It, or the entry before it, "
                          "was changed or removed outside this desk.", d["id"])
        prev = d["hash"]
    head = prev

    lprev, last_state = GENESIS, {}
    for r in logs:
        rec = {"entity_id": r["entity_id"], "kind": r["kind"], "ref_id": r["ref_id"],
               "payload": json.loads(r["payload_json"]), "recorded_at": r["recorded_at"],
               "recorded_by": r["recorded_by"]}
        if r["prev_hash"] != lprev or _log_hash(lprev, rec) != r["hash"]:
            return broken("The activity log (UPSI, trading window, pre-clearance and designated persons) was "
                          f"changed outside this desk at record #{r['id']}.")
        lprev = r["hash"]
        if r["kind"].startswith("upsi_"):
            last_state[r["ref_id"]] = rec["payload"]

    if anchor:
        if anchor["entries"] != len(rows):
            gone = anchor["entries"] - len(rows)
            return broken(f"The record shows {anchor['entries']} entries but {len(rows)} "
                          f"{'remains' if len(rows) == 1 else 'remain'}"
                          + (f": the newest {gone} {'was' if gone == 1 else 'were'} deleted outside this desk."
                             if gone > 0 else "."))
        if anchor["entries_head"] != head:
            return broken("The newest entry was replaced outside this desk.")
        if (anchor["log_entries"], anchor["log_head"]) != (len(logs), lprev):
            return broken("Records were removed from the end of the activity log outside this desk.")
        if anchor["mac"]:
            expect = _mac(_anchor_text(entity_id, anchor["entries"], anchor["entries_head"],
                                       anchor["log_entries"], anchor["log_head"]))
            if expect and not hmac.compare_digest(expect, anchor["mac"]):
                return broken("The integrity anchor was rewritten outside this desk.")

    for u in upsi:
        logged = last_state.get(u["id"])
        if logged is None:
            return broken(f"UPSI “{u['title']}” was added outside this desk.")
        if logged != _upsi_state(u):
            return broken(f"UPSI “{logged.get('title') or u['title']}” was edited outside this desk.")
    if len(last_state) > len(upsi):
        return broken("A UPSI record was deleted outside this desk.")

    return {"ok": True, "entries": len(rows), "broken_at": None, "head": head, "problem": "",
            "checked_at": _now()}


def export_rows(user_id, entity_id):
    """Unmasked rows for the CSV export (the compliance officer's own record)."""
    return list(reversed(list_entries(user_id, entity_id, mask=False)))


def desk_summary(user_id, entity_id, today=None):
    """Counts for the entity picker and the desk header."""
    if not _owned(user_id, entity_id):
        return None
    conn = get_connection()
    upsi = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(public_on = ''), 0) AS open FROM sdd_upsi "
                        "WHERE entity_id = ?", (entity_id,)).fetchone()
    entries = conn.execute("SELECT COUNT(*) FROM sdd_entries WHERE entity_id = ?", (entity_id,)).fetchone()[0]
    persons = conn.execute("SELECT COUNT(*) FROM pit_persons WHERE entity_id = ? AND removed_at = ''",
                           (entity_id,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM pit_preclearance WHERE entity_id = ? AND status = 'pending'",
                           (entity_id,)).fetchone()[0]
    conn.close()
    return {
        "upsi": upsi["n"], "open_upsi": upsi["open"], "entries": entries, "persons": persons,
        "pending_preclearance": pending,
        "window_closed": window_closed_on(entity_id, today or today_ist()),
        "chain": verify_chain(user_id, entity_id),
    }


# ── Designated persons ──────────────────────────────────────────────────────

def add_person(user_id, entity_id, data):
    if not _owned(user_id, entity_id):
        return None
    name = _text(data.get("name"), 160)
    if not name:
        raise ValueError("Name is required.")
    relation = data.get("relation") or "self"
    if not isinstance(relation, str) or relation not in RELATIONS:
        raise ValueError("Choose whether this is a designated person or an immediate relative.")
    pan = _clean_pan(data.get("pan"))
    email = _text(data.get("email"), 160)
    if email and not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address.")
    conn = get_connection()
    try:
        related_to = None
        if relation == "immediate_relative":
            related_to = _int(data.get("related_to"))
            if not related_to or not conn.execute(
                    "SELECT 1 FROM pit_persons WHERE id = ? AND entity_id = ? AND relation = 'self' AND removed_at = ''",
                    (related_to, entity_id)).fetchone():
                raise ValueError("Pick the designated person this relative belongs to.")
        if pan and conn.execute("SELECT 1 FROM pit_persons WHERE entity_id = ? AND pan = ? AND removed_at = ''",
                                (entity_id, pan)).fetchone():
            raise ValueError("Someone with this PAN is already on the list.")
        cur = conn.execute(
            """INSERT INTO pit_persons (entity_id, name, pan, designation, relation, related_to, email, added_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (entity_id, name, pan, _text(data.get("designation"), 100), relation,
             related_to, email, _now()))
        _log(conn, entity_id, "person_added", cur.lastrowid, _row(conn, "pit_persons", cur.lastrowid), user_id)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def remove_person(user_id, entity_id, person_id):
    """Soft remove. A designated person's immediate relatives go with them,
    since they are covered only through that person."""
    if not _owned(user_id, entity_id):
        return False
    now = _now()
    conn = get_connection()
    try:
        cur = conn.execute("UPDATE pit_persons SET removed_at = ? WHERE id = ? AND entity_id = ? AND removed_at = ''",
                           (now, person_id, entity_id))
        if cur.rowcount == 0:
            return False
        conn.execute("UPDATE pit_persons SET removed_at = ? WHERE related_to = ? AND entity_id = ? AND removed_at = ''",
                     (now, person_id, entity_id))
        _log(conn, entity_id, "person_removed", person_id, _row(conn, "pit_persons", person_id), user_id)
        conn.commit()
        return True
    finally:
        conn.close()


def list_persons(user_id, entity_id):
    """Active persons, each designated person followed by their relatives."""
    if not _owned(user_id, entity_id):
        return []
    conn = get_connection()
    rows = conn.execute(
        """SELECT p.*, r.name AS related_name FROM pit_persons p
           LEFT JOIN pit_persons r ON r.id = p.related_to AND r.entity_id = p.entity_id
           WHERE p.entity_id = ? AND p.removed_at = '' ORDER BY p.name COLLATE NOCASE""",
        (entity_id,)).fetchall()
    conn.close()
    heads, relatives = [], {}
    for r in rows:
        d = dict(r)
        d["pan_masked"] = mask_pan(d["pan"])
        d["relation_label"] = RELATIONS.get(d["relation"], d["relation"])
        if d["relation"] == "immediate_relative" and d["related_to"]:
            relatives.setdefault(d["related_to"], []).append(d)
        else:
            heads.append(d)
    out = []
    for h in heads:
        out.append(h)
        out.extend(relatives.pop(h["id"], []))
    for rest in relatives.values():
        out.extend(rest)
    return out


# ── Trading window ──────────────────────────────────────────────────────────

def close_window(user_id, entity_id, closes_on, opens_on="", reason=""):
    if not _owned(user_id, entity_id):
        return None
    c = _parse_date(closes_on)
    if not c:
        raise ValueError("Closing date is required.")
    o = None
    if opens_on:
        o = _parse_date(opens_on)
        if not o:
            raise ValueError("The reopening date is not a valid date.")
        if o <= c:
            raise ValueError("The window must reopen after the day it closes.")
    conn = get_connection()
    try:
        for w in conn.execute("SELECT closes_on, opens_on FROM pit_windows WHERE entity_id = ?", (entity_id,)):
            wc, wo = _parse_date(w["closes_on"]), _parse_date(w["opens_on"])
            if (o is None or wc < o) and (wo is None or c < wo):
                if wo:
                    raise ValueError(f"This overlaps the closure from {human_date(wc)} to {human_date(wo)}.")
                raise ValueError(f"The closure from {human_date(wc)} has no reopening date yet. Reopen it first.")
        cur = conn.execute(
            "INSERT INTO pit_windows (entity_id, closes_on, opens_on, reason, created_by, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (entity_id, c.isoformat(), o.isoformat() if o else "", _text(reason, 300), user_id, _now()))
        _log(conn, entity_id, "window_closed", cur.lastrowid, _row(conn, "pit_windows", cur.lastrowid), user_id)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def set_window_open(user_id, entity_id, window_id, opens_on):
    """Set (or move) the reopening date. A reopening already in the past is
    history and stays as recorded."""
    if not _owned(user_id, entity_id):
        return False
    o = _parse_date(opens_on) if opens_on else today_ist()
    if not o:
        raise ValueError("Give the reopening date.")
    conn = get_connection()
    try:
        w = conn.execute("SELECT * FROM pit_windows WHERE id = ? AND entity_id = ?", (window_id, entity_id)).fetchone()
        if not w:
            return False
        wc, wo = _parse_date(w["closes_on"]), _parse_date(w["opens_on"])
        if wo and wo <= today_ist():
            raise ValueError(f"This window already reopened on {human_date(wo)}.")
        if o <= wc:
            raise ValueError("The window must reopen after the day it closes.")
        nxt = conn.execute("SELECT closes_on FROM pit_windows WHERE entity_id = ? AND id != ? AND closes_on > ? "
                           "ORDER BY closes_on LIMIT 1", (entity_id, window_id, w["closes_on"])).fetchone()
        if nxt and o > _parse_date(nxt["closes_on"]):
            raise ValueError(f"That runs into the next closure from {human_date(nxt['closes_on'])}.")
        conn.execute("UPDATE pit_windows SET opens_on = ? WHERE id = ? AND entity_id = ?",
                     (o.isoformat(), window_id, entity_id))
        _log(conn, entity_id, "window_reopen_set", window_id, _row(conn, "pit_windows", window_id), user_id)
        conn.commit()
        return True
    finally:
        conn.close()


def list_windows(user_id, entity_id, today=None):
    if not _owned(user_id, entity_id):
        return []
    today = today or today_ist()
    conn = get_connection()
    rows = conn.execute("SELECT * FROM pit_windows WHERE entity_id = ? ORDER BY closes_on DESC",
                        (entity_id,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        c, o = _parse_date(d["closes_on"]), _parse_date(d["opens_on"])
        if c > today:
            d["state"] = "scheduled"
        elif o is None or o > today:
            d["state"] = "closed"
        else:
            d["state"] = "reopened"
        d["can_reopen"] = o is None or o > today
        out.append(d)
    return out


def window_closed_on(entity_id, day):
    """True when `day` falls inside any closure. A window with no reopening date
    stays closed."""
    iso = day.isoformat()
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM pit_windows WHERE entity_id = ? AND closes_on <= ? AND (opens_on = '' OR opens_on > ?)",
        (entity_id, iso, iso)).fetchone()
    conn.close()
    return row is not None


def window_status(user_id, entity_id, today=None):
    """Today's position: the closure in force (if any) and the next scheduled one."""
    today = today or today_ist()
    wins = list_windows(user_id, entity_id, today)
    current = next((w for w in wins if w["state"] == "closed"), None)
    upcoming = sorted((w for w in wins if w["state"] == "scheduled"), key=lambda w: w["closes_on"])
    return {"closed": current is not None, "current": current,
            "next": upcoming[0] if upcoming else None, "today": today.isoformat()}


def _quarter_end_on_or_after(d):
    m = ((d.month - 1) // 3 + 1) * 3
    return date(d.year, m, calendar.monthrange(d.year, m)[1])


def _previous_quarter_end(d):
    qe = _quarter_end_on_or_after(d)
    return date(qe.year, qe.month - 2, 1) - timedelta(days=1)


_RESULTS_WORDING = {3: "quarter and year", 6: "quarter", 9: "quarter and half year", 12: "quarter and nine months"}


def suggest_closure(user_id, entity_id, today=None):
    """Prefill for 'Close window'. Schedule B closes the window from the end of
    each quarter until 48 hours after results, so the suggestion is the day
    after a quarter end: the one just gone if its results are still due
    (45 days, 60 for March) and no closure covers it, else the next one."""
    today = today or today_ist()
    wins = [(_parse_date(w["closes_on"]), _parse_date(w["opens_on"]))
            for w in list_windows(user_id, entity_id, today)]

    def covered(qe):
        # A closure near the quarter end that is still closed the day after it.
        # An ad hoc closure that reopened before then (a board meeting on an
        # acquisition, say) is not the results closure.
        start = qe + timedelta(days=1)
        return any(qe - timedelta(days=31) <= c <= qe + timedelta(days=15) and (o is None or o > start)
                   for c, o in wins)

    prev_qe = _previous_quarter_end(today)
    grace = 60 if prev_qe.month == 3 else 45
    if today <= prev_qe + timedelta(days=grace) and not covered(prev_qe):
        qe = prev_qe
    else:
        qe = _quarter_end_on_or_after(today)
        for _ in range(4):
            if not covered(qe):
                break
            qe = _quarter_end_on_or_after(qe + timedelta(days=1))
    tense = "ending" if qe >= today else "ended"
    return {
        "closes_on": (qe + timedelta(days=1)).isoformat(),
        "quarter_end": qe.isoformat(),
        "reason": f"Financial results for the {_RESULTS_WORDING[qe.month]} {tense} {human_date(qe)}",
    }


# ── Pre-clearance ───────────────────────────────────────────────────────────

def _add_trading_days(d, n):
    """Weekdays only — exchange holidays are not modelled, so this is the
    conservative (earliest) expiry."""
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def request_preclearance(user_id, entity_id, data):
    if not _owned(user_id, entity_id):
        return None
    pid = _int(data.get("person_id"))
    qty = _int(data.get("quantity"))
    txn = data.get("txn_type")
    if not isinstance(txn, str) or txn not in TXN_TYPES:
        raise ValueError("Choose the type of transaction.")
    if qty <= 0:
        raise ValueError("Quantity must be a whole number above zero.")
    est = 0
    if data.get("est_value") not in (None, ""):
        est = _int(data.get("est_value"), -1)
        if est < 0:
            raise ValueError("Estimated value must be a whole rupee amount, zero or more.")
    conn = get_connection()
    try:
        if not conn.execute("SELECT 1 FROM pit_persons WHERE id = ? AND entity_id = ? AND removed_at = ''",
                            (pid, entity_id)).fetchone():
            raise ValueError("Pick a designated person.")
        cur = conn.execute(
            """INSERT INTO pit_preclearance (entity_id, person_id, txn_type, quantity, est_value, requested_at, notes)
               VALUES (?,?,?,?,?,?,?)""",
            (entity_id, pid, txn, qty, est, _now(), _text(data.get("notes"), 500)))
        _log(conn, entity_id, "preclearance_requested", cur.lastrowid,
             _row(conn, "pit_preclearance", cur.lastrowid), user_id)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def decide_preclearance(user_id, entity_id, request_id, approve, notes="", today=None):
    """Approve or reject. Approval is refused while the trading window is closed,
    and its validity stops the day before a closure that starts inside the
    7 trading days. Returns {status, valid_until, cut_by_closure} on success,
    False when the request is not this entity's, None when the entity is not
    the user's."""
    if not _owned(user_id, entity_id):
        return None
    today = today or today_ist()
    notes = _text(notes, 500)
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT r.status, p.name, p.removed_at FROM pit_preclearance r
               JOIN pit_persons p ON p.id = r.person_id WHERE r.id = ? AND r.entity_id = ?""",
            (request_id, entity_id)).fetchone()
        if not row:
            return False
        if row["status"] != "pending":
            raise ValueError(f"This request was already {row['status']}.")
        cut = None
        valid_until = ""
        if approve:
            if row["removed_at"]:
                raise ValueError(f"{row['name']} is no longer on the designated persons list.")
            if window_closed_on(entity_id, today):
                raise ValueError("The trading window is closed today. Pre-clearance cannot be granted until it reopens.")
            valid = _add_trading_days(today, PRECLEARANCE_VALID_TRADING_DAYS)
            nxt = conn.execute("SELECT closes_on FROM pit_windows WHERE entity_id = ? AND closes_on > ? "
                               "AND closes_on <= ? ORDER BY closes_on LIMIT 1",
                               (entity_id, today.isoformat(), valid.isoformat())).fetchone()
            if nxt:
                cut = nxt["closes_on"]
                valid = _parse_date(cut) - timedelta(days=1)
            valid_until = valid.isoformat()
        elif not notes:
            raise ValueError("Give a reason for rejecting the request.")
        status = "approved" if approve else "rejected"
        cur = conn.execute(
            """UPDATE pit_preclearance SET status = ?, decided_at = ?, decided_by = ?, valid_until = ?,
                   decision_note = ? WHERE id = ? AND entity_id = ? AND status = 'pending'""",
            (status, _now(), user_id, valid_until, notes, request_id, entity_id))
        if cur.rowcount == 0:
            conn.rollback()
            return False
        _log(conn, entity_id, "preclearance_" + status, request_id, _row(conn, "pit_preclearance", request_id), user_id)
        conn.commit()
        return {"status": status, "valid_until": valid_until, "cut_by_closure": cut or ""}
    finally:
        conn.close()


def list_preclearance(user_id, entity_id, today=None):
    if not _owned(user_id, entity_id):
        return []
    today = (today or today_ist()).isoformat()
    conn = get_connection()
    rows = conn.execute(
        """SELECT r.*, p.name AS person_name, p.designation, p.relation, p.removed_at AS person_removed_at
           FROM pit_preclearance r JOIN pit_persons p ON p.id = r.person_id
           WHERE r.entity_id = ? ORDER BY r.requested_at DESC, r.id DESC""",
        (entity_id,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["txn_label"] = TXN_TYPES.get(d["txn_type"], d["txn_type"])
        expired = d["status"] == "approved" and d["valid_until"] and d["valid_until"] < today
        d["state"] = "expired" if expired else d["status"]
        out.append(d)
    return out
