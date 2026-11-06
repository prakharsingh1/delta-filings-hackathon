"""Statutory registers and the board / general meeting kit.

Registers: every change is a new version of the row; the old version is kept
and marked superseded, so the register shows its own history the way the
physical book would.

Meetings: pick agenda items from a library of standard business, and the kit
drafts the notice, agenda, resolutions and a minutes skeleton (downloadable
as .docx). Marking a meeting held logs events on the entity (board meeting
held, resolutions passed, directors appointed, shares allotted ...), which
put MGT-14, DIR-12, PAS-3, Reg 30 and minutes signing on the calendar.
"""
from __future__ import annotations

import io
import json
import math
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta

from database import get_connection
import entities as ents

# ── Registers ───────────────────────────────────────────────────────────────
# Field lists follow the prescribed forms; kept to the columns a CS actually
# fills in day to day. `required` is the minimum that identifies an entry.
REGISTERS = {
    "members": {
        "label": "Register of members", "short": "Members", "form": "MGT-1", "law": "Companies Act s.88",
        "about": "Every member with folio, class and number of shares, and the dates they joined or left.",
        "required": ["name"],
        "fields": [("folio", "Folio no."), ("name", "Member name"), ("pan", "PAN"),
                   ("address", "Address"), ("email", "Email"), ("share_class", "Class of shares"),
                   ("shares", "Shares held"), ("paid_up", "Amount paid up (₹)"),
                   ("date_became_member", "Date of becoming member"), ("date_ceased", "Date of cessation"),
                   ("nominee", "Nominee")],
    },
    "directors_kmp": {
        "label": "Register of directors and KMP", "short": "Directors and KMP", "form": "Rule 17",
        "law": "Companies Act s.170",
        "about": "Every director and KMP with DIN or PAN, designation, and dates of appointment and cessation.",
        "required": ["name"],
        "fields": [("name", "Name"), ("din_pan", "DIN / PAN"), ("designation", "Designation"),
                   ("appointed_on", "Date of appointment"), ("ceased_on", "Date of cessation"),
                   ("address", "Residential address"), ("shares", "Shares held")],
    },
    "charges": {
        "label": "Register of charges", "short": "Charges", "form": "CHG-7", "law": "Companies Act s.85",
        "about": "Every charge on the company's property, with the holder, amount and dates of creation, "
                 "modification and satisfaction.",
        "required": ["holder"],
        "fields": [("charge_id", "Charge ID"), ("holder", "Charge holder"), ("amount", "Amount secured (₹)"),
                   ("created_on", "Date of creation"), ("property", "Property charged"),
                   ("modified_on", "Date of modification"), ("satisfied_on", "Date of satisfaction")],
    },
    "contracts": {
        "label": "Register of contracts with related parties", "short": "Related party contracts",
        "form": "MBP-4", "law": "Companies Act s.189",
        "about": "Contracts and arrangements with related parties, with the board and members' approval dates.",
        "required": ["party"],
        "fields": [("party", "Related party"), ("relationship", "Relationship"), ("nature", "Nature of contract"),
                   ("value", "Value (₹)"), ("board_approval", "Board approval date"),
                   ("member_approval", "Members' approval date")],
    },
    "loans_investments": {
        "label": "Register of loans, guarantees and investments", "short": "Loans and investments",
        "form": "MBP-2", "law": "Companies Act s.186",
        "about": "Loans given, guarantees, security and investments, with the board resolution behind each.",
        "required": ["party"],
        "fields": [("party", "Party"), ("nature", "Loan / guarantee / security / investment"),
                   ("amount", "Amount (₹)"), ("date", "Date"), ("board_resolution", "Board resolution date"),
                   ("purpose", "Purpose"), ("rate", "Rate of interest (%)")],
    },
    "transfers": {
        "label": "Register of share transfers", "short": "Share transfers", "form": "SH-4 log",
        "law": "Companies Act s.56",
        "about": "Share transfers with transferor, transferee, number of shares, consideration and new folio.",
        "required": ["transferor", "transferee"],
        "fields": [("date", "Date of transfer"), ("transferor", "Transferor"), ("transferee", "Transferee"),
                   ("shares", "Shares"), ("consideration", "Consideration (₹)"), ("folio", "New folio")],
    },
}

_NUMBER_KEYS = {"shares", "paid_up", "amount", "value", "consideration", "rate"}
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_DIN_RE = re.compile(r"^[0-9]{8}$")


def field_kind(key, label=""):
    """'number', 'date' or 'text' — drives input types and validation."""
    if key in _NUMBER_KEYS:
        return "number"
    if key.endswith("_on") or key.startswith("date") or "date" in label.lower():
        return "date"
    return "text"


def register_meta():
    """Register definitions in the shape the pages need."""
    return [{
        "key": k, "label": r["label"], "short": r["short"], "form": r["form"], "law": r["law"],
        "about": r["about"], "required": r["required"],
        "fields": [{"key": f, "label": lab, "kind": field_kind(f, lab)} for f, lab in r["fields"]],
    } for k, r in REGISTERS.items()]


def init_secretarial_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS register_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            register_key TEXT NOT NULL,
            row_key TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            data_json TEXT NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_register_rows ON register_rows(entity_id, register_key, is_current);

        CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            venue TEXT DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'physical',
            chair TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'planned',
            items_json TEXT NOT NULL DEFAULT '[]',
            held_on TEXT DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_meetings_entity ON meetings(entity_id, scheduled_for);
    """)
    # Columns added after the first release. Several workers may start at once,
    # so a "duplicate column" from a racing ALTER is expected and harmless.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(meetings)").fetchall()}
    for col, ddl in (("sched_event_id", "INTEGER"), ("followups_json", "TEXT NOT NULL DEFAULT '{}'")):
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE meetings ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass
    conn.commit()
    conn.close()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _parse_day(s):
    try:
        return date.fromisoformat(str(s or "").strip()[:10])
    except ValueError:
        return None


def _parse_dt(s):
    try:
        return datetime.fromisoformat(str(s or "").strip())
    except ValueError:
        return None


def fmt_day(d):
    """12 Oct 2026."""
    if isinstance(d, str):
        d = _parse_day(d)
    return f"{d.day} {d.strftime('%b %Y')}" if d else ""


def _clean_value(key, label, raw):
    v = str(raw if raw is not None else "").strip()[:300]
    if not v:
        return ""
    kind = field_kind(key, label)
    if kind == "number":
        n = v.replace(",", "").replace("₹", "").replace("%", "").strip()
        try:
            f = float(n)
        except ValueError:
            raise ValueError(f"{label}: enter a number.")
        if math.isnan(f) or math.isinf(f) or f < 0:
            raise ValueError(f"{label}: enter a number of zero or more.")
        return n
    if kind == "date":
        d = _parse_day(v)
        if not d or len(v) != 10:
            raise ValueError(f"{label}: enter a valid date.")
        return d.isoformat()
    if key == "pan":
        v = v.upper().replace(" ", "")
        if not _PAN_RE.match(v):
            raise ValueError("PAN should look like ABCDE1234F.")
    elif key == "din_pan":
        v = v.upper().replace(" ", "")
        if not (_DIN_RE.match(v) or _PAN_RE.match(v)):
            raise ValueError("Enter an 8-digit DIN or a 10-character PAN.")
    elif key == "email" and ("@" not in v or " " in v):
        raise ValueError("Email: enter a valid address.")
    return v


def get_register_row(user_id, entity_id, register_key, row_key):
    """Current version of one row, or None."""
    if not ents.get_entity(user_id, entity_id) or register_key not in REGISTERS:
        return None
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM register_rows WHERE entity_id = ? AND register_key = ? AND row_key = ? AND is_current = 1",
        (entity_id, register_key, str(row_key or ""))).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["data"] = json.loads(d.pop("data_json"))
    return d


def save_register_row(user_id, entity_id, register_key, data, row_key=None):
    """Insert a new row, or a new version of `row_key`. Returns the new id."""
    if not ents.get_entity(user_id, entity_id):
        return None
    reg = REGISTERS.get(register_key)
    if not reg:
        raise ValueError("Unknown register.")
    if not isinstance(data, dict):
        raise ValueError("Send the entry as an object of fields.")
    clean = {k: _clean_value(k, lab, data.get(k)) for k, lab in reg["fields"]}
    if not any(clean.values()):
        raise ValueError("Fill in at least one field.")
    labels = dict(reg["fields"])
    missing = [labels[k] for k in reg["required"] if not clean.get(k)]
    if missing:
        raise ValueError(f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} required.")
    conn = get_connection()
    try:
        version = 1
        if row_key:
            prev = conn.execute(
                "SELECT version, data_json FROM register_rows WHERE entity_id = ? AND register_key = ? "
                "AND row_key = ? AND is_current = 1", (entity_id, register_key, row_key)).fetchone()
            if not prev:
                raise ValueError("That entry no longer exists.")
            if json.loads(prev["data_json"]) == clean:
                raise ValueError("No changes to save.")
            version = prev["version"] + 1
            conn.execute("UPDATE register_rows SET is_current = 0 WHERE entity_id = ? AND register_key = ? "
                         "AND row_key = ?", (entity_id, register_key, row_key))
        else:
            # Millisecond prefix keeps creation order; the suffix stops two
            # entries saved in the same millisecond from merging histories.
            row_key = f"r{int(datetime.now().timestamp() * 1000)}{secrets.token_hex(2)}"
        cur = conn.execute(
            """INSERT INTO register_rows (entity_id, register_key, row_key, version, data_json, created_by, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (entity_id, register_key, row_key, version, json.dumps(clean, ensure_ascii=False), user_id, _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_register(user_id, entity_id, register_key, include_history=False):
    if not ents.get_entity(user_id, entity_id) or register_key not in REGISTERS:
        return []
    conn = get_connection()
    q = "SELECT * FROM register_rows WHERE entity_id = ? AND register_key = ?"
    if not include_history:
        q += " AND is_current = 1"
    rows = conn.execute(q + " ORDER BY row_key, version", (entity_id, register_key)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d.pop("data_json"))
        out.append(d)
    return out


def register_counts(user_id, entity_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT register_key, COUNT(*) AS n FROM register_rows r JOIN entities e ON e.id = r.entity_id
           WHERE r.entity_id = ? AND e.user_id = ? AND r.is_current = 1 GROUP BY register_key""",
        (entity_id, user_id)).fetchall()
    conn.close()
    return {r["register_key"]: r["n"] for r in rows}


# ── Meeting kit ─────────────────────────────────────────────────────────────
# `mgt14`: "all" = MGT-14 for every company; "public" = s.179(3) matters,
# which private companies are exempt from filing; None = no filing.
# `event`: entity event logged when the meeting is marked held, which puts the
# item's own follow-up filing (DIR-12, PAS-3, INC-22, Reg 30) on the calendar.
# `ordinary`: ordinary business at an AGM (s.102(2)(a)); everything else at a
# general meeting is special business and needs an explanatory statement.
AGENDA_LIBRARY = [
    {"key": "leave", "title": "Leave of absence",
     "resolution": "RESOLVED THAT leave of absence be granted to the directors who could not attend the meeting.",
     "mgt14": None, "kinds": ["board"]},
    {"key": "prev_minutes", "title": "Confirm minutes of the previous meeting",
     "resolution": "RESOLVED THAT the minutes of the previous meeting of the Board, as circulated, be and are hereby confirmed.",
     "mgt14": None, "kinds": ["board", "committee"]},
    {"key": "disclosures", "title": "Note directors' disclosures (MBP-1, DIR-8)",
     "resolution": "RESOLVED THAT the disclosures of interest in Form MBP-1 and the declarations of non-disqualification in Form DIR-8 received from the directors be and are hereby taken on record.",
     "mgt14": None, "kinds": ["board"]},
    {"key": "fin_statements", "title": "Approve financial statements and Board's report",
     "resolution": "RESOLVED THAT the audited financial statements for the financial year ended 31 March {fy_end_year}, together with the Board's report and annexures, be and are hereby approved, and that any two directors be authorised to sign them on behalf of the Board.",
     "mgt14": "public", "kinds": ["board"]},
    {"key": "call_agm", "title": "Convene the Annual General Meeting",
     "resolution": "RESOLVED THAT the Annual General Meeting of the members be held on {agm_date} and the draft notice placed before the Board be and is hereby approved.",
     "mgt14": None, "kinds": ["board"]},
    {"key": "auditor", "title": "Recommend appointment of statutory auditor",
     "resolution": "RESOLVED THAT, subject to the approval of the members, {auditor_name} be recommended for appointment as statutory auditors for a term of five years.",
     "mgt14": None, "kinds": ["board"]},
    {"key": "additional_director", "title": "Appoint an additional director (s.161)",
     "resolution": "RESOLVED THAT {director_name} (DIN {din}) be and is hereby appointed as an Additional Director of the Company with effect from {effective_date}, to hold office up to the date of the next Annual General Meeting.",
     "mgt14": None, "kinds": ["board"], "event": "director_appointed"},
    {"key": "kmp", "title": "Appoint key managerial personnel",
     "resolution": "RESOLVED THAT {person_name} be and is hereby appointed as {kmp_role} of the Company with effect from {effective_date} on the terms placed before the Board.",
     "mgt14": "public", "kinds": ["board"], "event": "kmp_appointed"},
    {"key": "borrow", "title": "Approve borrowings (s.179(3)(d))",
     "resolution": "RESOLVED THAT the Board hereby approves borrowing of up to ₹{amount} from {lender} on the terms placed before the meeting, within the limits approved under section 180(1)(c).",
     "mgt14": "public", "kinds": ["board"]},
    {"key": "invest", "title": "Approve investment of funds (s.179(3)(e))",
     "resolution": "RESOLVED THAT the Board hereby approves investment of up to ₹{amount} in {investee} in accordance with section 186.",
     "mgt14": "public", "kinds": ["board"]},
    {"key": "loan_given", "title": "Grant loan / guarantee / security (s.179(3)(f))",
     "resolution": "RESOLVED THAT the Board hereby approves a loan of up to ₹{amount} to {party} in accordance with section 186.",
     "mgt14": "public", "kinds": ["board"]},
    {"key": "allotment", "title": "Allot securities (s.179(3)(c))",
     "resolution": "RESOLVED THAT {shares} equity shares of ₹{face_value} each be and are hereby allotted to the persons listed in the annexure, against the consideration received.",
     "mgt14": "public", "kinds": ["board"], "event": "shares_allotted"},
    {"key": "rpt", "title": "Approve related party transaction (s.188)",
     "resolution": "RESOLVED THAT consent of the Board be and is hereby accorded to enter into a contract with {party}, a related party, for {nature} of value up to ₹{amount}, on an arm's length basis.",
     "mgt14": None, "kinds": ["board"]},
    {"key": "bank", "title": "Open bank account / change authorised signatories",
     "resolution": "RESOLVED THAT a current account be opened with {bank} and that {signatories} be authorised to operate it.",
     "mgt14": None, "kinds": ["board"]},
    {"key": "reg_office", "title": "Shift registered office within the city",
     "resolution": "RESOLVED THAT the registered office of the Company be shifted to {address} with effect from {effective_date}.",
     "mgt14": None, "kinds": ["board"], "event": "registered_office_changed"},
    {"key": "adopt_fs_agm", "title": "Adopt financial statements (ordinary business)",
     "resolution": "RESOLVED THAT the audited financial statements for the financial year ended 31 March {fy_end_year}, together with the reports of the Board and the Auditors, be and are hereby received, considered and adopted.",
     "mgt14": None, "kinds": ["agm"], "ordinary": True},
    {"key": "reappoint_director", "title": "Re-appoint director retiring by rotation",
     "resolution": "RESOLVED THAT {director_name} (DIN {din}), who retires by rotation and being eligible offers himself/herself for re-appointment, be and is hereby re-appointed as a director.",
     "mgt14": None, "kinds": ["agm"], "ordinary": True},
    {"key": "special_resolution", "title": "Special resolution (s.114(2))",
     "resolution": "RESOLVED THAT, pursuant to the applicable provisions of the Companies Act, 2013, {matter}.",
     "mgt14": "all", "kinds": ["agm", "egm"], "special": True},
]
AGENDA_BY_KEY = {a["key"]: a for a in AGENDA_LIBRARY}
MEETING_KINDS = {"board": "Board meeting", "agm": "Annual General Meeting",
                 "egm": "Extraordinary General Meeting", "committee": "Committee meeting"}
MEETING_MODES = {"physical": "Physical", "vc": "Video conference", "hybrid": "Hybrid"}
NOTICE_DAYS = {"board": 7, "agm": 21, "egm": 21, "committee": 7}
# General meetings need 21 *clear* days (s.101): the day the notice goes out
# and the day of the meeting are both excluded.
NOTICE_CLEAR = {"agm", "egm"}
SHORT_NOTICE = {
    "board": "Shorter notice is allowed only for urgent business, with at least one independent director "
             "present, or ratification by one afterwards (s.173(3)).",
    "committee": "Shorter notice is allowed only for urgent business, with at least one independent director "
                 "present, or ratification by one afterwards (s.173(3), SS-1).",
    "agm": "Shorter notice needs written consent of at least 95% of the members entitled to vote (s.101(1)).",
    "egm": "Shorter notice needs consent of a majority in number of members holding at least 95% of the "
           "paid-up voting capital (s.101(1)).",
}

_PH_RE = re.compile(r"\{([a-z_]+)\}")
_PH_LABELS = {"fy_end_year": "financial year", "din": "DIN", "agm_date": "AGM date",
              "kmp_role": "KMP role", "committee_name": "committee name"}


def placeholder_label(key):
    return _PH_LABELS.get(key, key.replace("_", " "))


def placeholders(text):
    """Unfilled {blanks} in a resolution, in order of first appearance."""
    seen = []
    for k in _PH_RE.findall(text or ""):
        if k not in seen:
            seen.append(k)
    return seen


def notice_deadline(kind, meeting_day):
    """Last day the notice can go out and still give the minimum notice."""
    need = NOTICE_DAYS.get(kind, 7)
    return meeting_day - timedelta(days=need + (1 if kind in NOTICE_CLEAR else 0))


def notice_rules():
    """Notice periods for the planning dialog: {kind: {days, clear, short}}."""
    return {k: {"days": NOTICE_DAYS[k], "clear": k in NOTICE_CLEAR, "short": SHORT_NOTICE[k]}
            for k in MEETING_KINDS}


def _is_private(ent):
    return (ent or {}).get("entity_type") in ("private", "opc")


def item_needs_mgt14(key, ent):
    if (ent or {}).get("entity_type") == "llp":  # MGT-14 is a Companies Act form
        return False
    rule = (AGENDA_BY_KEY.get(key) or {}).get("mgt14")
    return rule == "all" or (rule == "public" and not _is_private(ent))


def agenda_library(ent, kind=None):
    """Library items (optionally for one meeting kind), with MGT-14 worked out for this entity."""
    out = []
    for a in AGENDA_LIBRARY:
        if kind and kind not in a["kinds"]:
            continue
        out.append({"key": a["key"], "title": a["title"], "resolution": a["resolution"],
                    "kinds": a["kinds"], "mgt14": item_needs_mgt14(a["key"], ent),
                    "blanks": placeholders(a["resolution"])})
    return out


def _decorate_meeting(m):
    m["items"] = json.loads(m.pop("items_json", None) or "[]")
    try:
        m["followups"] = json.loads(m.pop("followups_json", None) or "{}")
    except ValueError:
        m["followups"] = {}
    m["kind_label"] = MEETING_KINDS.get(m["kind"], m["kind"])
    m["mode_label"] = MEETING_MODES.get(m.get("mode"), m.get("mode") or "")
    return m


def _get_meeting(user_id, meeting_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT m.* FROM meetings m JOIN entities e ON e.id = m.entity_id "
        "WHERE m.id = ? AND e.user_id = ? AND e.archived = 0",
        (meeting_id, user_id)).fetchone()
    conn.close()
    return _decorate_meeting(dict(row)) if row else None


def get_meeting(user_id, meeting_id):
    m = _get_meeting(user_id, meeting_id)
    if m:
        ent = ents.get_entity(user_id, m["entity_id"])
        m["checks"] = meeting_checks(m, ent)
        m["mgt14_keys"] = sorted({it["key"] for it in mgt14_items(m, ent)})
        m["followup_items"] = meeting_followups(user_id, m)
    return m


def _clean_items(items, kind):
    if not isinstance(items, list):
        raise ValueError("Agenda items must be a list.")
    clean = []
    for it in items:
        if isinstance(it, str):
            it = {"key": it}
        if not isinstance(it, dict):
            continue
        key = it.get("key")
        lib = AGENDA_BY_KEY.get(key) if isinstance(key, str) else None
        if not lib or kind not in lib["kinds"]:
            continue
        title = str(it.get("title") or "").strip()[:200] or lib["title"]
        resolution = str(it.get("resolution") or "").strip()[:3000] or lib["resolution"]
        clean.append({"key": lib["key"], "title": title, "resolution": resolution})
    if len(clean) > 60:
        raise ValueError("A meeting can have at most 60 agenda items.")
    return clean


def create_meeting(user_id, entity_id, data):
    ent = ents.get_entity(user_id, entity_id)
    if not ent:
        return None
    if not isinstance(data, dict):
        raise ValueError("Send the meeting as an object.")
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in MEETING_KINDS:
        raise ValueError("Choose a meeting type.")
    raw = str(data.get("scheduled_for") or "").strip()
    dt = _parse_dt(raw)
    if not dt or len(raw) < 16:
        raise ValueError("Enter the date and time of the meeting.")
    if not 2000 <= dt.year <= 2100:
        raise ValueError("Enter a date between 2000 and 2100.")
    when = dt.strftime("%Y-%m-%dT%H:%M")
    mode = data.get("mode") or "physical"
    if not isinstance(mode, str) or mode not in MEETING_MODES:
        raise ValueError("Choose physical, video conference or hybrid.")
    raw_items = data.get("items") or []
    if not isinstance(raw_items, list):
        raise ValueError("Agenda items must be a list.")
    seen, keys = set(), []
    for k in raw_items:
        if isinstance(k, str) and k not in seen:
            seen.add(k)
            keys.append(k)
    items = _clean_items(keys, kind)
    title = str(data.get("title") or "").strip()[:200] or f"{MEETING_KINDS[kind]}, {fmt_day(dt.date())}"
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO meetings (entity_id, kind, title, scheduled_for, venue, mode, chair, items_json,
               created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (entity_id, kind, title, when, str(data.get("venue") or "").strip()[:200], mode,
         str(data.get("chair") or "").strip()[:120], json.dumps(items, ensure_ascii=False), user_id, _now()))
    mid = cur.lastrowid
    conn.commit()
    conn.close()
    # Listed companies: Reg 29 prior intimation. It only strictly applies to
    # meetings on results, dividend, buyback, fund raising or bonus, so the
    # meeting page says so and the user can mark it N/A on the calendar.
    # A meeting recorded after its date gets no reminder: the window has
    # closed, and a backdated row would show as overdue at ₹10,000 a day.
    if kind == "board" and ent["entity_type"] == "listed" and dt.date() >= date.today():
        r = ents.log_event(user_id, entity_id, "board_meeting_scheduled", when[:10], title)
        if r:
            conn = get_connection()
            conn.execute("UPDATE meetings SET sched_event_id = ? WHERE id = ?", (r["event_id"], mid))
            conn.commit()
            conn.close()
    return mid


def update_meeting_items(user_id, meeting_id, items):
    m = _get_meeting(user_id, meeting_id)
    if not m:
        return False
    clean = _clean_items(items, m["kind"])
    conn = get_connection()
    conn.execute("UPDATE meetings SET items_json = ? WHERE id = ?", (json.dumps(clean, ensure_ascii=False), meeting_id))
    conn.commit()
    conn.close()
    return True


def delete_meeting(user_id, meeting_id):
    """Delete a meeting that is still planned; returns its entity id.
    Held meetings stay on record."""
    m = _get_meeting(user_id, meeting_id)
    if not m:
        return False
    if m["status"] != "planned":
        raise ValueError("A held meeting stays on record and cannot be deleted.")
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM meetings WHERE id = ? AND status = 'planned'", (meeting_id,))
        if cur.rowcount == 0:
            raise ValueError("A held meeting stays on record and cannot be deleted.")
        ev = m.get("sched_event_id")
        if ev:
            # Drop the Reg 29 reminder this meeting put on the calendar (pending
            # or marked N/A), unless it has been filed: the filing stays on record.
            conn.execute("DELETE FROM entity_obligations WHERE entity_id = ? AND event_id = ? AND status != 'filed'",
                         (m["entity_id"], ev))
            left = conn.execute("SELECT COUNT(*) AS n FROM entity_obligations WHERE entity_id = ? AND event_id = ?",
                                (m["entity_id"], ev)).fetchone()["n"]
            if not left:
                conn.execute("DELETE FROM entity_events WHERE id = ? AND entity_id = ?", (ev, m["entity_id"]))
        conn.commit()
    finally:
        conn.close()
    return m["entity_id"]


def meeting_checks(m, ent, today=None):
    """Plain-language checks shown on the meeting page: level ok / warn / info."""
    today = today or date.today()
    out = []
    dt = _parse_dt(m["scheduled_for"])
    if not dt:
        return out
    when = dt.date()
    kind = m["kind"]
    need = NOTICE_DAYS.get(kind, 7)
    period = f"{need} clear days" if kind in NOTICE_CLEAR else f"{need} days"
    if m["status"] == "planned":
        latest = notice_deadline(kind, when)
        if when < today:
            out.append({"level": "warn",
                        "text": f"The meeting date ({fmt_day(when)}) has passed. Mark it as held with the actual "
                                "date, or delete it if it did not take place."})
        elif today > latest:
            out.append({"level": "warn",
                        "text": f"Notice needed {period} and should have gone out by {fmt_day(latest)}. "
                                + SHORT_NOTICE.get(kind, "")})
        else:
            out.append({"level": "ok", "text": f"Send the notice by {fmt_day(latest)} ({period} before the meeting)."})
        if kind == "board" and (ent or {}).get("entity_type") == "listed" and m.get("sched_event_id"):
            out.append({"level": "info",
                        "text": "Reg 29 prior intimation to the exchanges is on the calendar; it must go at least "
                                "two working days before the meeting. It applies when the board considers results, "
                                "dividend, buyback, fund raising or bonus; otherwise mark it N/A."})
    else:
        out.append({"level": "ok", "text": f"Held on {fmt_day(m.get('held_on'))}. Follow-up filings are on the "
                                           "entity calendar."})
    blanks = []
    for it in m["items"]:
        for k in placeholders(it.get("resolution")):
            if k not in blanks:
                blanks.append(k)
    if blanks:
        out.append({"level": "warn",
                    "text": "Fill before circulating: " + ", ".join(placeholder_label(k) for k in blanks) + "."})
    filings = mgt14_items(m, ent)
    if filings:
        names = ", ".join(i["title"] for i in filings)
        held = _parse_day(m.get("held_on")) if m["status"] == "held" else None
        if held:
            out.append({"level": "info",
                        "text": f"MGT-14 is due by {fmt_day(held + timedelta(days=30))} for: {names}."})
        else:
            out.append({"level": "info",
                        "text": f"{len(filings)} item{'s' if len(filings) != 1 else ''} will need MGT-14 within "
                                f"30 days of the meeting: {names}."})
    return out


def mgt14_items(m, ent):
    return [it for it in m["items"] if item_needs_mgt14(it.get("key"), ent)]


def meeting_followups(user_id, m):
    """Calendar rows created when the meeting was marked held, with live status."""
    fu = m.get("followups") or {}
    events, tasks = set(fu.get("events") or []), set(fu.get("tasks") or [])
    if not events and not tasks:
        return []
    cal = ents.entity_calendar(user_id, m["entity_id"])
    if not cal:
        return []
    out = []
    for i in cal["items"]:
        if i.get("event_id") in events or i["id"] in tasks:
            # Event rows carry "<event label> · <date> #<id>" as their period;
            # the label says which Reg 30 / DIR-12 row belongs to which item.
            about = i["period"].split(" · ")[0] if i.get("event_id") else ""
            out.append({"id": i["id"], "name": i["name"], "form": i["form"], "due_date": i["due_date"],
                        "state": i["state"], "status": i["status"], "about": about})
    out.sort(key=lambda i: (i["due_date"], i["name"]))
    return out


def mark_held(user_id, meeting_id, held_on):
    m = _get_meeting(user_id, meeting_id)
    if not m:
        return None
    if m["status"] == "held":
        raise ValueError("This meeting is already marked as held.")
    if held_on:
        held_d = _parse_day(held_on)
        if not held_d:
            raise ValueError("Enter the date the meeting was held.")
    else:
        held_d = date.today()
    if held_d > date.today():
        raise ValueError("The meeting cannot be marked held on a future date.")
    if held_d.year < 2000:
        raise ValueError("Enter a date from 2000 onwards.")
    held = held_d.isoformat()
    conn = get_connection()
    cur = conn.execute("UPDATE meetings SET status = 'held', held_on = ? WHERE id = ? AND status = 'planned'",
                       (held, meeting_id))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:  # a second click raced the first
        raise ValueError("This meeting is already marked as held.")

    ent = ents.get_entity(user_id, m["entity_id"])
    eid, title = m["entity_id"], m["title"]
    created, event_ids, task_ids = [], [], []

    def log(event_type, details):
        r = ents.log_event(user_id, eid, event_type, held, details)
        if r:
            event_ids.append(r["event_id"])
            created.extend(r["created"])

    if m["kind"] == "board":
        log("board_meeting_held", title)
    elif m["kind"] in ("agm", "egm"):
        log("general_meeting_held", title)
        if m["kind"] == "agm" and ent and ent["entity_type"] != "llp":
            ents.record_agm(user_id, eid, held)
    # The rulebook's minutes rule covers board meetings only; s.118 and SS-1/
    # SS-2 need committee and general meeting minutes signed within 30 days too.
    if m["kind"] != "board" and ent and ent["entity_type"] != "llp":
        what = "committee" if m["kind"] == "committee" else "general meeting"
        tid = ents.add_task(user_id, eid, f"Sign minutes of the {what}", (held_d + timedelta(days=30)).isoformat(),
                            notes=f"{title}. Minutes must be signed within 30 days of the meeting (s.118).")
        if tid:
            task_ids.append(tid)
    mgt = mgt14_items(m, ent)
    if mgt:
        log("resolution_passed", "; ".join(i["title"] for i in mgt))
    done = set()
    for it in m["items"]:
        ev = (AGENDA_BY_KEY.get(it["key"]) or {}).get("event")
        if ev and ev not in done:
            done.add(ev)
            log(ev, f"{it['title']} ({title})")

    conn = get_connection()
    conn.execute("UPDATE meetings SET followups_json = ? WHERE id = ?",
                 (json.dumps({"events": event_ids, "tasks": task_ids}), meeting_id))
    conn.commit()
    conn.close()
    m.update(status="held", held_on=held, followups={"events": event_ids, "tasks": task_ids})
    return {"created": created, "held_on": held, "followups": meeting_followups(user_id, m)}


def list_meetings(user_id, entity_id):
    ent = ents.get_entity(user_id, entity_id)
    if not ent:
        return []
    conn = get_connection()
    rows = conn.execute("SELECT * FROM meetings WHERE entity_id = ? ORDER BY scheduled_for DESC, id DESC",
                        (entity_id,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = _decorate_meeting(dict(r))
        d["mgt14_count"] = len(mgt14_items(d, ent))
        dt = _parse_dt(d["scheduled_for"])
        d["notice_by"] = notice_deadline(d["kind"], dt.date()).isoformat() if dt else ""
        out.append(d)
    return out


# ── Meeting pack (.docx) ────────────────────────────────────────────────────

def _long_day(d):
    return f"{d.strftime('%A')}, {d.day} {d.strftime('%B %Y')}"


def _clock(dt):
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'a.m.' if dt.hour < 12 else 'p.m.'}"


def fmt_time(s):
    """'2026-10-12T11:00' -> '11:00 a.m.'"""
    dt = _parse_dt(s)
    return _clock(dt) if dt and len(str(s)) >= 16 else ""


def fmt_when(s):
    """'2026-10-12T11:00' -> 'Monday, 12 October 2026 at 11:00 a.m.'"""
    dt = _parse_dt(s)
    return f"{_long_day(dt.date())} at {_clock(dt)}" if dt else str(s or "")


# Word's XML refuses control characters. Text pasted from Word carries \x0b
# (manual line break) and \x0c (page break); keep those as line breaks.
_XML_BAD = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")


def _xml_text(s):
    return _XML_BAD.sub("", str(s or "").replace("\x0b", "\n").replace("\x0c", "\n"))


def _place_phrase(m):
    # An empty venue stays as a visible {venue} blank in the pack.
    venue = (m.get("venue") or "").strip()
    if m.get("mode") == "vc":
        return " through video conferencing or other audio-visual means" + (
            f" (deemed venue: {venue})" if venue else "")
    at = f" at {venue}" if venue else " at {venue}"
    if m.get("mode") == "hybrid":
        return at + ", with the option to join through video conferencing"
    return at


def build_docx(user_id, meeting_id):
    """Notice + agenda (+ explanatory statement) + resolutions + minutes skeleton as one .docx.

    Unfilled blanks keep their braces and are set in bold on a yellow
    highlight, so nobody circulates a notice with {amount} still in it."""
    from docx import Document
    from docx.enum.text import WD_COLOR_INDEX
    from docx.shared import Pt

    m = _get_meeting(user_id, meeting_id)
    if not m:
        return None
    ent = ents.get_entity(user_id, m["entity_id"])
    if not ent:
        return None
    kind = m["kind"]
    general = kind in ("agm", "egm")
    dt = _parse_dt(m["scheduled_for"])
    when = f"{_long_day(dt.date())} at {_clock(dt)}" if dt else m["scheduled_for"]
    place = _place_phrase(m)
    items = m["items"]

    def is_special(it):
        lib = AGENDA_BY_KEY.get(it["key"]) or {}
        return general and (kind == "egm" or not lib.get("ordinary"))

    # A general meeting notice lists ordinary business first; use that one
    # order everywhere so item numbers match across notice, resolutions and minutes.
    if general:
        items = [i for i in items if not is_special(i)] + [i for i in items if is_special(i)]
    held_d = _parse_day(m.get("held_on")) if m["status"] == "held" else None
    minutes_when = when
    if held_d and dt and held_d != dt.date():
        minutes_when = f"{_long_day(held_d)} at {_clock(dt)}"

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    def para(text, style_name=None, italic=False):
        """Paragraph with any {blank} made obvious: braces kept, bold, highlighted."""
        p = doc.add_paragraph(style=style_name)
        for part in re.split(r"(\{[a-z_]+\})", _xml_text(text)):
            if not part:
                continue
            run = p.add_run(part)
            run.italic = italic
            if _PH_RE.fullmatch(part):
                run.bold = True
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
        return p

    def header():
        doc.add_heading(_xml_text(ent["name"].upper()), level=1)
        line = " · ".join(x for x in [f"CIN: {ent['cin']}" if ent.get("cin") else "", ent.get("state") or ""] if x)
        if line:
            doc.add_paragraph(_xml_text(line))

    def h3(text):
        doc.add_paragraph(_xml_text(text), style="Heading 3")

    # 1. Notice and agenda
    header()
    doc.add_heading(f"Notice of {m['kind_label']}", level=2)
    if kind == "board":
        body = f"a meeting of the Board of Directors of {ent['name']}"
    elif kind == "committee":
        body = f"a meeting of the {{committee_name}} Committee of the Board of {ent['name']}"
    else:
        body = f"an {m['kind_label']} of the members of {ent['name']}"
    para(f"Notice is hereby given that {body} will be held on {when}{place}, to transact the following business.")

    doc.add_heading("Agenda", level=2)
    if not items:
        para("{agenda_items}")
    elif general:
        n = 0
        for label, group in (("Ordinary business", [i for i in items if not is_special(i)]),
                             ("Special business", [i for i in items if is_special(i)])):
            if group:
                doc.add_heading(label, level=3)
                for it in group:
                    n += 1
                    para(f"{n}. {it['title']}")
    else:
        for i, it in enumerate(items, 1):
            para(f"{i}. {it['title']}")

    if general:
        doc.add_heading("Notes", level=3)
        notes = []
        if m.get("mode") != "vc":
            notes.append("A member entitled to attend and vote may appoint a proxy to attend and vote instead of "
                         "himself or herself; a proxy need not be a member. Proxy forms must reach the registered "
                         "office at least 48 hours before the meeting (s.105).")
        if m.get("mode") in ("vc", "hybrid"):
            notes.append("Members attending through video conferencing are counted for the purpose of quorum (s.103).")
        if any(is_special(i) for i in items):
            notes.append("The explanatory statement under section 102 for the special business is annexed.")
        for i, t in enumerate(notes, 1):
            para(f"{i}. {t}")
        para(f"\nBy order of the Board\nFor {ent['name']}\n\n\nCompany Secretary\nPlace: ____________\nDate: ____________")
    else:
        para(f"\nFor {ent['name']}\n\n\nCompany Secretary / Director\nPlace: ____________\nDate: ____________")

    special = [(n, it) for n, it in enumerate(items, 1) if is_special(it)]
    if special:
        doc.add_page_break()
        header()
        doc.add_heading("Explanatory statement under section 102", level=2)
        for n, it in special:
            h3(f"Item {n}: {it['title']}")
            para("{material_facts}")
            para("None of the directors, key managerial personnel or their relatives is concerned or interested "
                 "in this resolution, except {interested_persons}.")

    # 2. Draft resolutions
    doc.add_page_break()
    header()
    doc.add_heading("Draft resolutions", level=2)
    blanks = []
    for it in items:
        for k in placeholders(it["resolution"]):
            if k not in blanks:
                blanks.append(k)
    if blanks:
        para("Fill the highlighted blanks before circulating: "
             + ", ".join(placeholder_label(k) for k in blanks) + ".", italic=True)
    for i, it in enumerate(items, 1):
        h3(f"Item {i}: {it['title']}")
        if general:
            para("Special resolution" if (AGENDA_BY_KEY.get(it["key"]) or {}).get("special")
                 else "Ordinary resolution", italic=True)
        para(it["resolution"])
        if item_needs_mgt14(it["key"], ent):
            para("File MGT-14 within 30 days of passing this resolution.", italic=True)

    # 3. Minutes skeleton
    doc.add_page_break()
    header()
    doc.add_heading(f"Minutes of the {m['kind_label']}", level=2)
    para(f"Held on {minutes_when}{place}.")
    people = [p for p in ents.list_people(m["entity_id"]) if not p.get("ceased_on")]
    directors = [p for p in people if p.get("din")]
    doc.add_heading("Present", level=3)
    if directors:
        for p in directors:
            desig = f", {p['designation']}" if p.get("designation") else ""
            para(f"{p['name']} (DIN {p['din']}){desig}: " + ("present / absent" if general else "present / leave of absence"))
    else:
        para("Directors: ______________________")
    if general:
        para("Members present in person: ________    By proxy: ________    Authorised representatives: ________")
    kmp = [p for p in people if not p.get("din")]
    para("In attendance: " + (", ".join(f"{p['name']}" + (f" ({p['designation']})" if p.get("designation") else "")
                                        for p in kmp) if kmp else "______________________"))
    para(f"Chairperson: {m.get('chair') or '______________'}")
    para("Quorum: The Chairperson confirmed that the requisite quorum was present throughout the meeting.")
    for i, it in enumerate(items, 1):
        h3(f"{i}. {it['title']}")
        para("Discussion: ______________________")
        para(it["resolution"])
    para("\nThere being no other business, the meeting concluded with a vote of thanks to the Chair.\n\n\n"
         "Chairperson\nDate of signing: ____________\nPlace: ____________")

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", ent["name"]).strip("_")[:40] or "meeting"
    return buf, f"{safe}_{kind}_{m['scheduled_for'][:10]}.docx"
