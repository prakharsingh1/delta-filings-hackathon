"""Entity workspace — any Indian company or LLP a user is responsible for.

A CS in a company adds their own entity; a practising CS adds every client.
Each entity gets a profile (type, flags, state, directors) and the rulebook
turns that profile into a tracked calendar of dated obligations. Events
("director appointed", "charge created") add their follow-up filings.

All reads and writes are scoped by user_id — an entity id alone never grants
access.
"""
from __future__ import annotations

import calendar
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone

import rulebook as rb
from database import get_connection

IST = timezone(timedelta(hours=5, minutes=30))


def today_ist():
    """Deadlines are Indian dates. The server clock runs on UTC, which is a
    day behind between midnight and 5:30 am IST."""
    return datetime.now(IST).date()

# DSC expiry is not a statutory filing, but an expired certificate blocks every
# MCA filing the director signs, so it sits on the same calendar.
DSC_RULE = {
    "id": "IN-PRAC-DSC", "jurisdiction": "IN", "regime": "Companies Act",
    "name": "Renew digital signature (DSC)", "form": "DSC", "law": "Practice",
    "types": rb.ALL_TYPES, "schedule": {"kind": "per_person"},
    "penalty": {"type": "text", "text": "Filings signed by this director are blocked until renewed."},
    "source": "https://www.mca.gov.in/", "flags": [], "any_flags": [], "not_flags": [],
    "states": [], "category": "Practice",
    "description": "Renew the director's DSC before it expires.",
}
CUSTOM_RULE = {
    "id": "CUSTOM", "jurisdiction": "IN", "regime": "Custom", "name": "Task", "form": "",
    "law": "", "types": rb.ALL_TYPES, "schedule": {"kind": "custom"},
    "penalty": {"type": "text", "text": ""}, "source": "", "flags": [], "any_flags": [],
    "not_flags": [], "states": [], "category": "Task", "description": "",
}
rb.RULES_BY_ID[DSC_RULE["id"]] = DSC_RULE
rb.RULES_BY_ID[CUSTOM_RULE["id"]] = CUSTOM_RULE

DUE_SOON_DAYS = 15
HORIZON_DAYS = 180
DEFAULT_LOOKBACK_DAYS = 60


def init_entities_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            jurisdiction TEXT NOT NULL DEFAULT 'IN',
            cin TEXT DEFAULT '',
            pan TEXT DEFAULT '',
            gstin TEXT DEFAULT '',
            state TEXT DEFAULT '',
            incorporation_date TEXT DEFAULT '',
            share_capital INTEGER DEFAULT 0,
            symbol TEXT DEFAULT '',
            flags_json TEXT NOT NULL DEFAULT '{}',
            tracking_from TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entities_user ON entities(user_id, archived);

        CREATE TABLE IF NOT EXISTS entity_people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            din TEXT DEFAULT '',
            pan TEXT DEFAULT '',
            designation TEXT DEFAULT '',
            appointed_on TEXT DEFAULT '',
            ceased_on TEXT DEFAULT '',
            din_allotted_on TEXT DEFAULT '',
            dsc_expiry TEXT DEFAULT '',
            email TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_people_entity ON entity_people(entity_id);
        CREATE INDEX IF NOT EXISTS idx_people_din ON entity_people(din);

        CREATE TABLE IF NOT EXISTS entity_obligations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            rule_id TEXT NOT NULL,
            period TEXT NOT NULL,
            title TEXT DEFAULT '',
            due_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            filed_on TEXT DEFAULT '',
            srn TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            origin TEXT NOT NULL DEFAULT 'rule',
            event_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(entity_id, rule_id, period)
        );
        CREATE INDEX IF NOT EXISTS idx_eob_entity ON entity_obligations(entity_id, due_date);

        CREATE TABLE IF NOT EXISTS entity_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_date TEXT NOT NULL,
            details TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
    """)
    for col in ("evidence_url", "evidence_text", "suggest_date", "suggest_url", "suggest_text"):
        try:
            conn.execute(f"ALTER TABLE entity_obligations ADD COLUMN {col} TEXT DEFAULT ''")
        except Exception:
            pass   # already there
    # An earlier version closed SEBI rows itself from keyword matches on NSE
    # titles, and closed many on unrelated announcements. Those rows (evidence
    # but no SRN, so never confirmed by a person) go back to pending; the next
    # sync offers a match again only if a strict one exists.
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if not conn.execute("SELECT 1 FROM settings WHERE key = 'migr_exchange_autoclose_undone'").fetchone():
        conn.execute(
            """UPDATE entity_obligations
                  SET status = 'pending', filed_on = '', evidence_url = '', evidence_text = ''
                WHERE status = 'filed' AND rule_id LIKE 'IN-SEBI-%' AND evidence_text != ''
                  AND (srn = '' OR srn IS NULL)""")
        conn.execute("INSERT INTO settings (key, value) VALUES ('migr_exchange_autoclose_undone', ?)", (_now(),))
    conn.commit()
    conn.close()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _row_to_entity(row):
    e = dict(row)
    try:
        e["flags"] = json.loads(e.pop("flags_json") or "{}")
    except (TypeError, ValueError):
        e["flags"] = {}
    e["type_label"] = rb.ENTITY_TYPES.get(e["entity_type"], e["entity_type"])
    return e


def _clean_flags(flags):
    return {k: True for k, v in (flags or {}).items() if k in rb.FLAGS and v}


# ── Entities ────────────────────────────────────────────────────────────────

def create_entity(user_id, data):
    name = (data.get("name") or "").strip()[:200]
    etype = data.get("entity_type")
    if not name:
        raise ValueError("Name is required.")
    if etype not in rb.ENTITY_TYPES:
        raise ValueError("Choose a valid entity type.")
    flags = _clean_flags(data.get("flags"))
    if etype == "listed":
        flags["secretarial_audit"] = True
    tracking_from = _parse_date(data.get("tracking_from")) or (today_ist() - timedelta(days=DEFAULT_LOOKBACK_DAYS))
    now = _now()
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO entities (user_id, name, entity_type, cin, pan, gstin, state,
               incorporation_date, share_capital, symbol, flags_json, tracking_from,
               created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, name, etype, (data.get("cin") or "").strip().upper()[:25],
         (data.get("pan") or "").strip().upper()[:10], (data.get("gstin") or "").strip().upper()[:15],
         (data.get("state") or "").strip()[:60], (data.get("incorporation_date") or "")[:10],
         int(data.get("share_capital") or 0), (data.get("symbol") or "").strip().upper()[:30],
         json.dumps(flags), tracking_from.isoformat(), now, now),
    )
    eid = cur.lastrowid
    conn.commit()
    conn.close()
    sync_obligations(user_id, eid)
    return eid


def update_entity(user_id, entity_id, data):
    ent = get_entity(user_id, entity_id)
    if not ent:
        return False
    fields = {}
    for k, maxlen in (("name", 200), ("cin", 25), ("pan", 10), ("gstin", 15), ("state", 60),
                      ("incorporation_date", 10), ("symbol", 30), ("tracking_from", 10)):
        if k in data:
            v = (data.get(k) or "").strip()[:maxlen]
            fields[k] = v.upper() if k in ("cin", "pan", "gstin", "symbol") else v
    if "entity_type" in data and data["entity_type"] in rb.ENTITY_TYPES:
        fields["entity_type"] = data["entity_type"]
    if "share_capital" in data:
        fields["share_capital"] = int(data.get("share_capital") or 0)
    if "flags" in data:
        flags = _clean_flags(data.get("flags"))
        if fields.get("entity_type", ent["entity_type"]) == "listed":
            flags["secretarial_audit"] = True
        fields["flags_json"] = json.dumps(flags)
    if not fields:
        return True
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn = get_connection()
    conn.execute(f"UPDATE entities SET {sets} WHERE id = ? AND user_id = ?",
                 (*fields.values(), entity_id, user_id))
    conn.commit()
    conn.close()
    sync_obligations(user_id, entity_id)
    return True


def archive_entity(user_id, entity_id):
    conn = get_connection()
    try:
        has_sdd = conn.execute("SELECT 1 FROM sdd_upsi WHERE entity_id = ? LIMIT 1", (entity_id,)).fetchone()
    except Exception:   # SDD tables not created in this database
        has_sdd = None
    if has_sdd and get_entity(user_id, entity_id):
        conn.close()
        # Archiving hides the company everywhere, its SDD included, and SEBI
        # PIT Reg 3(6) requires the SDD to be kept for at least eight years.
        raise ValueError("This company has an insider-trading database (SDD). SEBI requires it to be kept for at "
                         "least eight years, so the company can't be archived.")
    cur = conn.execute("UPDATE entities SET archived = 1, updated_at = ? WHERE id = ? AND user_id = ?",
                       (_now(), entity_id, user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_entity(user_id, entity_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM entities WHERE id = ? AND user_id = ? AND archived = 0",
                       (entity_id, user_id)).fetchone()
    conn.close()
    return _row_to_entity(row) if row else None


def list_entities(user_id):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM entities WHERE user_id = ? AND archived = 0 ORDER BY name",
                        (user_id,)).fetchall()
    conn.close()
    return [_row_to_entity(r) for r in rows]


# ── People ──────────────────────────────────────────────────────────────────

def add_person(user_id, entity_id, data):
    if not get_entity(user_id, entity_id):
        return None
    name = (data.get("name") or "").strip()[:160]
    if not name:
        raise ValueError("Name is required.")
    din = "".join(ch for ch in (data.get("din") or "") if ch.isdigit())[:8]
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO entity_people (entity_id, name, din, pan, designation, appointed_on,
               ceased_on, din_allotted_on, dsc_expiry, email, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (entity_id, name, din, (data.get("pan") or "").strip().upper()[:10],
         (data.get("designation") or "").strip()[:80], (data.get("appointed_on") or "")[:10],
         (data.get("ceased_on") or "")[:10], (data.get("din_allotted_on") or "")[:10],
         (data.get("dsc_expiry") or "")[:10], (data.get("email") or "").strip()[:160], _now()),
    )
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    sync_obligations(user_id, entity_id)
    return pid


def remove_person(user_id, entity_id, person_id):
    if not get_entity(user_id, entity_id):
        return False
    conn = get_connection()
    row = conn.execute("SELECT din FROM entity_people WHERE id = ? AND entity_id = ?",
                       (person_id, entity_id)).fetchone()
    conn.execute("DELETE FROM entity_people WHERE id = ? AND entity_id = ?", (person_id, entity_id))
    if row:
        # Drop the person's still-pending reminders; filed history stays.
        conn.execute(
            """DELETE FROM entity_obligations WHERE entity_id = ? AND status = 'pending'
               AND origin = 'person' AND (period LIKE ? OR period LIKE ?)""",
            (entity_id, f"%DIN {row['din']}%" if row["din"] else "__none__", f"%#{person_id}"))
    conn.commit()
    conn.close()
    return True


def list_people(entity_id):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM entity_people WHERE entity_id = ? ORDER BY ceased_on != '', name",
                        (entity_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def all_dins_for_user(user_id):
    """{din: [(entity_name, person_name, designation)]} for active people."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT p.din, p.name AS person, p.designation, e.name AS entity, e.id AS entity_id
           FROM entity_people p JOIN entities e ON e.id = p.entity_id
           WHERE e.user_id = ? AND e.archived = 0 AND p.din != '' AND (p.ceased_on = '' OR p.ceased_on IS NULL)""",
        (user_id,)).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["din"], []).append(dict(r))
    return out


# ── Obligations ─────────────────────────────────────────────────────────────

def _period_end(rule, occ):
    """Last day of the period an occurrence reports on, read from the period
    label rulebook.occurrences builds. None when it can't be told."""
    kind = rule["schedule"]["kind"]
    label, due = occ["period"], occ["due_date"]
    try:
        if kind == "monthly":  # "Jul 2026"
            mon, year = label.split()
            m, y = list(calendar.month_abbr).index(mon), int(year)
            return date(y, m, calendar.monthrange(y, m)[1])
        if kind in ("quarterly_after", "quarterly_fixed", "quarterly_nextmonth"):  # "Q1 FY2026-27"
            q, fy = re.match(r"Q([1-4]) FY(\d{4})", label).groups()
            fy = int(fy)
            return [date(fy, 6, 30), date(fy, 9, 30), date(fy, 12, 31), date(fy + 1, 3, 31)][int(q) - 1]
        if kind == "half_fixed":
            # "Apr–Sep 2026", "Oct–Mar 2027", "Jan–Jun 2026", "Jul–Dec, due Jan 2027":
            # the half ends in the month after the dash, in the due date's year
            # unless that month comes after the due month (due early next year).
            m = list(calendar.month_abbr).index(re.match(r"\w{3}–(\w{3})", label).group(1))
            y = due.year if m <= due.month else due.year - 1
            return date(y, m, calendar.monthrange(y, m)[1])
        if kind == "annual":  # "FY2025-26"
            return date(int(re.match(r"FY(\d{4})", label).group(1)) + 1, 3, 31)
        if kind == "annual_calendar":  # "CY2025"
            return date(int(re.match(r"CY(\d{4})", label).group(1)), 12, 31)
    except (AttributeError, ValueError):
        return None
    return None


def sync_obligations(user_id, entity_id, today=None):
    """Bring an entity's calendar in line with its profile. Idempotent."""
    ent = get_entity(user_id, entity_id)
    if not ent:
        return 0
    today = today or today_ist()
    start = _parse_date(ent["tracking_from"]) or (today - timedelta(days=DEFAULT_LOOKBACK_DAYS))
    inc = _parse_date(ent["incorporation_date"])
    if inc and inc > start:
        start = inc
    end = today + timedelta(days=HORIZON_DAYS)
    now = _now()
    wanted = set()
    conn = get_connection()
    try:
        # Forms due a fixed number of days after the AGM follow the date the AGM
        # was actually held, once it is recorded on the AGM row.
        agm_held = {r["period"]: _parse_date(r["filed_on"]) for r in conn.execute(
            "SELECT period, filed_on FROM entity_obligations WHERE entity_id = ? AND rule_id = 'IN-MCA-AGM' "
            "AND status = 'filed' AND filed_on != ''", (entity_id,)).fetchall()}
        for rule in rb.applicable_rules(ent, include_events=False):
            after = rule["schedule"].get("after_agm")
            for occ in rb.occurrences(rule, start - timedelta(days=0), end):
                if after and agm_held.get(occ["period"]):
                    occ = {**occ, "due_date": agm_held[occ["period"]] + timedelta(days=after)}
                # A company incorporated in August owes nothing for July or
                # for the financial year before it existed.
                pend = _period_end(rule, occ) if inc else None
                if pend and pend < inc:
                    continue
                wanted.add((rule["id"], occ["period"]))
                conn.execute(
                    """INSERT OR IGNORE INTO entity_obligations
                       (entity_id, rule_id, period, due_date, origin, created_at, updated_at)
                       VALUES (?,?,?,?, 'rule', ?, ?)""",
                    (entity_id, rule["id"], occ["period"], occ["due_date"].isoformat(), now, now))
                # A corrected rule (or a one-off extension) moves the due date
                # of rows still open; filed and N/A rows keep their history.
                conn.execute(
                    """UPDATE entity_obligations SET due_date = ?, updated_at = ?
                       WHERE entity_id = ? AND rule_id = ? AND period = ? AND status = 'pending'
                         AND due_date != ?""",
                    (occ["due_date"].isoformat(), now, entity_id, rule["id"], occ["period"],
                     occ["due_date"].isoformat()))

        for p in list_people(entity_id):
            if p.get("ceased_on"):
                continue
            if p.get("din"):
                # One row per 3-year cycle: the cycle year is part of the key,
                # otherwise a filed cycle blocks the next one (UNIQUE period).
                for due in rb.dir3kyc_cycles(_parse_date(p.get("din_allotted_on")), start, end):
                    period = f"{p['name']} · DIN {p['din']} · {due.year}"
                    wanted.add((rb.DIR3KYC_RULE["id"], period))
                    conn.execute(
                        """INSERT OR IGNORE INTO entity_obligations
                           (entity_id, rule_id, period, due_date, origin, created_at, updated_at)
                           VALUES (?,?,?,?, 'person', ?, ?)""",
                        (entity_id, rb.DIR3KYC_RULE["id"], period, due.isoformat(), now, now))
            dsc = _parse_date(p.get("dsc_expiry"))
            if dsc and dsc <= end:
                period = f"{p['name']} · #{p['id']}"
                wanted.add((DSC_RULE["id"], period))
                conn.execute(
                    """INSERT OR IGNORE INTO entity_obligations
                       (entity_id, rule_id, period, due_date, origin, created_at, updated_at)
                       VALUES (?,?,?,?, 'person', ?, ?)""",
                    (entity_id, DSC_RULE["id"], period, dsc.isoformat(), now, now))

        # A profile change (e.g. GST de-registration) retires pending rule rows
        # that no longer apply. Filed rows and event/custom rows are kept.
        rows = conn.execute(
            "SELECT id, rule_id, period FROM entity_obligations WHERE entity_id = ? "
            "AND status = 'pending' AND origin IN ('rule', 'person')", (entity_id,)).fetchall()
        stale = [r["id"] for r in rows if (r["rule_id"], r["period"]) not in wanted]
        for sid in stale:
            conn.execute("DELETE FROM entity_obligations WHERE id = ?", (sid,))
        try:
            suggest_from_exchange(conn, ent, today)
        except Exception as e:   # a matching problem must never break the calendar
            print(f"[ENTITIES] exchange matching failed for entity {entity_id}: {e}")
        conn.commit()
    finally:
        conn.close()
    return len(wanted)


# SEBI rule -> the listed-company tracker's mandate, whose announcement
# categories (compliance.ANNOUNCEMENT_CATEGORIES) decide what counts as a match.
RULE_MANDATE = {
    "IN-SEBI-REG33": "LODR-REG33", "IN-SEBI-REG74": "LODR-REG74", "IN-SEBI-REG23": "LODR-REG23-RPT",
    "IN-SEBI-REG24A": "LODR-REG24A", "IN-SEBI-BRSR": "LODR-BRSR", "IN-SEBI-PIT-TW": "PIT-TRADING-WINDOW",
}


def suggest_from_exchange(conn, entity, today=None):
    """Point a listed company's open SEBI rows at the NSE filing that looks like
    them. Nothing is closed here: the match is stored as a suggestion and the
    CS confirms it with Mark filed. A filing matches only when its category is
    the rule's own and it was made after the period ended (for the trading
    window, around the quarter end) and no later than 30 days after the due
    date. Returns the number of rows with a suggestion."""
    conn.execute("UPDATE entity_obligations SET suggest_date = '', suggest_url = '', suggest_text = '' "
                 "WHERE entity_id = ? AND suggest_text != ''", (entity["id"],))
    sym = (entity.get("symbol") or "").strip().upper()
    if entity["entity_type"] != "listed" or not sym:
        return 0
    from compliance import ANNOUNCEMENT_CATEGORIES
    filings = [dict(r) for r in conn.execute(
        "SELECT title, sort_date, pdf_link FROM filings WHERE company = ? AND sort_date != ''", (sym,)).fetchall()]
    if not filings:
        return 0
    today = (today or today_ist()).isoformat()
    found = 0
    rows = conn.execute(
        "SELECT * FROM entity_obligations WHERE entity_id = ? AND status = 'pending' AND origin = 'rule' "
        "AND rule_id IN (%s)" % ",".join("?" * len(RULE_MANDATE)),
        (entity["id"], *RULE_MANDATE)).fetchall()
    for ob in rows:
        rule = rb.RULES_BY_ID.get(ob["rule_id"])
        due = _parse_date(ob["due_date"])
        pend = _period_end(rule, {"period": ob["period"], "due_date": due}) if rule and due else None
        if not pend:
            continue
        if ob["rule_id"] == "IN-SEBI-PIT-TW":   # the closure is announced before the quarter ends
            lo, hi = pend - timedelta(days=30), pend + timedelta(days=10)
        else:
            lo, hi = pend + timedelta(days=1), due + timedelta(days=30)
        lo, hi = lo.isoformat(), min(hi.isoformat(), today)
        cats = ANNOUNCEMENT_CATEGORIES[RULE_MANDATE[ob["rule_id"]]]
        hits = sorted((f for f in filings
                       if lo <= f["sort_date"][:10] <= hi and (f.get("title") or "").strip().lower() in cats),
                      key=lambda f: f["sort_date"])
        if not hits:
            continue
        best = hits[0]
        conn.execute(
            "UPDATE entity_obligations SET suggest_date = ?, suggest_url = ?, suggest_text = ? WHERE id = ?",
            (best["sort_date"][:10], (best.get("pdf_link") or "")[:600], (best.get("title") or "")[:200], ob["id"]))
        found += 1
    return found


def log_event(user_id, entity_id, event_type, event_date, details=""):
    ent = get_entity(user_id, entity_id)
    if not ent:
        return None
    if event_type not in rb.EVENT_TYPES:
        raise ValueError("Unknown event type.")
    d = _parse_date(event_date)
    if not d:
        raise ValueError("Event date is required.")
    now = _now()
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO entity_events (entity_id, event_type, event_date, details, created_by, created_at) "
        "VALUES (?,?,?,?,?,?)", (entity_id, event_type, d.isoformat(), (details or "")[:500], user_id, now))
    event_id = cur.lastrowid
    created = []
    for ob in rb.event_obligations(event_type, d, ent):
        period = f"{ob['period']} #{event_id}"
        conn.execute(
            """INSERT OR IGNORE INTO entity_obligations
               (entity_id, rule_id, period, due_date, origin, event_id, notes, created_at, updated_at)
               VALUES (?,?,?,?, 'event', ?, ?, ?, ?)""",
            (entity_id, ob["rule_id"], period, ob["due_date"].isoformat(), event_id,
             (details or "")[:300], now, now))
        created.append(ob["rule_id"])
    conn.commit()
    conn.close()
    return {"event_id": event_id, "created": created}


def add_task(user_id, entity_id, title, due_date, notes="", origin="custom"):
    if not get_entity(user_id, entity_id):
        return None
    d = _parse_date(due_date)
    title = (title or "").strip()[:200]
    if not d or not title:
        raise ValueError("Title and due date are required.")
    now = _now()
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO entity_obligations (entity_id, rule_id, period, title, due_date, notes,
               origin, created_at, updated_at) VALUES (?, 'CUSTOM', ?, ?, ?, ?, ?, ?, ?)""",
        (entity_id, f"task-{secrets.token_hex(6)}", title, d.isoformat(), (notes or "")[:1000],
         origin, now, now))
    oid = cur.lastrowid
    conn.commit()
    conn.close()
    return oid


def mark_past_filed(user_id, entity_id, before=None):
    """Onboarding shortcut: a CS adding a company whose past filings are
    already done marks every open item due before `before` (default today)
    as filed on its due date, in one step. Returns how many rows changed."""
    if not get_entity(user_id, entity_id):
        return None
    cutoff = _parse_date(before) or today_ist()
    now = _now()
    conn = get_connection()
    cur = conn.execute(
        """UPDATE entity_obligations SET status = 'filed', filed_on = due_date, updated_at = ?,
               notes = CASE WHEN notes = '' THEN 'Marked filed in bulk when setting up' ELSE notes END
           WHERE entity_id = ? AND status = 'pending' AND due_date < ?""",
        (now, entity_id, cutoff.isoformat()))
    conn.commit()
    conn.close()
    return cur.rowcount


def update_obligation(user_id, obligation_id, data):
    conn = get_connection()
    row = conn.execute(
        """SELECT o.* FROM entity_obligations o JOIN entities e ON e.id = o.entity_id
           WHERE o.id = ? AND e.user_id = ?""", (obligation_id, user_id)).fetchone()
    if not row:
        conn.close()
        return False
    status = data.get("status", row["status"])
    if status not in ("pending", "filed", "na"):
        conn.close()
        raise ValueError("Invalid status.")
    # Editing only the SRN or notes of a filed item keeps its filing date.
    filed_on = str((data.get("filed_on") if "filed_on" in data else row["filed_on"]) or "")[:10]
    if status == "filed" and filed_on:
        # A bad or future date would silently skew penalties and on-time rates.
        d = _parse_date(filed_on)
        if not d:
            conn.close()
            raise ValueError("Enter the filing date as a date.")
        if d > today_ist():
            conn.close()
            raise ValueError("The filing date can't be in the future.")
        filed_on = d.isoformat()
    if status == "filed" and not filed_on:
        filed_on = today_ist().isoformat()
    if status != "filed":
        filed_on = ""
    # The CS confirming the exchange filing Delta found keeps it as evidence.
    ev_url, ev_text = row["evidence_url"] or "", row["evidence_text"] or ""
    if status == "filed" and data.get("accept_suggestion") and row["suggest_text"]:
        ev_url, ev_text = row["suggest_url"] or "", row["suggest_text"]
    if status != "filed":
        ev_url = ev_text = ""
    conn.execute(
        "UPDATE entity_obligations SET status = ?, filed_on = ?, srn = ?, notes = ?, evidence_url = ?, "
        "evidence_text = ?, updated_at = ? WHERE id = ?",
        (status, filed_on, (data.get("srn", row["srn"]) or "")[:40],
         (data.get("notes", row["notes"]) or "")[:1000], ev_url, ev_text, _now(), obligation_id))
    conn.commit()
    conn.close()
    if row["rule_id"] == "IN-MCA-AGM":
        sync_obligations(user_id, row["entity_id"])   # AOC-4, MGT-7, ADT-1 ... follow the AGM date
    return True


def record_agm(user_id, entity_id, held_on):
    """An AGM held on `held_on`: mark that year's AGM row held and re-date the
    forms that follow it. Creates the row when the calendar window did not
    include it. Returns the FY label, or None if the entity is not the user's."""
    ent = get_entity(user_id, entity_id)
    d = _parse_date(held_on)
    if not ent or not d:
        return None
    period = rb.agm_fy_label(d)
    now = _now()
    conn = get_connection()
    row = conn.execute("SELECT id FROM entity_obligations WHERE entity_id = ? AND rule_id = 'IN-MCA-AGM' AND period = ?",
                       (entity_id, period)).fetchone()
    if row:
        conn.execute("UPDATE entity_obligations SET status = 'filed', filed_on = ?, updated_at = ? WHERE id = ?",
                     (d.isoformat(), now, row["id"]))
    else:
        due = date(int(period[2:6]) + 1, 9, 30)
        conn.execute("""INSERT INTO entity_obligations (entity_id, rule_id, period, due_date, status, filed_on, origin, created_at, updated_at)
                        VALUES (?, 'IN-MCA-AGM', ?, ?, 'filed', ?, 'rule', ?, ?)""",
                     (entity_id, period, due.isoformat(), d.isoformat(), now, now))
    conn.commit()
    conn.close()
    sync_obligations(user_id, entity_id)
    return period


def _decorate(ob, ent, today):
    rule = rb.RULES_BY_ID.get(ob["rule_id"]) or CUSTOM_RULE
    due = _parse_date(ob["due_date"])
    filed = _parse_date(ob.get("filed_on"))
    d = dict(ob)
    d["name"] = ob.get("title") or rule["name"]
    d["form"] = rule["form"]
    d["regime"] = rule["regime"]
    d["law"] = rule["law"]
    d["source"] = rule["source"]
    d["description"] = rule["description"]
    d["days_to_due"] = (due - today).days if due else None
    if ob["status"] == "pending":
        if due and due < today:
            d["state"] = "overdue"
        elif due and (due - today).days <= DUE_SOON_DAYS:
            d["state"] = "due_soon"
        else:
            d["state"] = "upcoming"
    else:
        d["state"] = ob["status"]
    pen = None
    if due and ob["status"] in ("pending", "filed"):
        if ob["status"] == "pending" and due < today:
            pen = rb.estimate_penalty(rule, due, today, ent)
        elif ob["status"] == "filed" and filed and filed > due:
            pen = rb.estimate_penalty(rule, due, today, ent, filed_on=filed)
    d["penalty"] = pen
    d["entity_name"] = ent["name"]
    d["entity_id"] = ent["id"]
    return d


def entity_calendar(user_id, entity_id, today=None):
    ent = get_entity(user_id, entity_id)
    if not ent:
        return None
    today = today or today_ist()
    conn = get_connection()
    rows = conn.execute("SELECT * FROM entity_obligations WHERE entity_id = ? ORDER BY due_date",
                        (entity_id,)).fetchall()
    conn.close()
    items = [_decorate(dict(r), ent, today) for r in rows]
    return {"entity": ent, "items": items, "summary": _summarise(items)}


def _summarise(items):
    overdue = [i for i in items if i["state"] == "overdue"]
    exposure = sum((i["penalty"] or {}).get("amount") or 0 for i in overdue)
    # Only rules with a stated (interest-based or discretionary) penalty count;
    # custom tasks carry no penalty text and an expired DSC is not a penalty.
    uncomputed = sum(1 for i in overdue if i["penalty"] and i["penalty"]["amount"] is None
                     and i["penalty"].get("basis") and i["rule_id"] != DSC_RULE["id"])
    closed = [i for i in items if i["status"] == "filed"]
    on_time = [i for i in closed if not i["penalty"]]
    done_or_due = closed + overdue
    return {
        "overdue": len(overdue),
        "due_soon": sum(1 for i in items if i["state"] == "due_soon"),
        "upcoming": sum(1 for i in items if i["state"] == "upcoming"),
        "filed": len(closed),
        "exposure": exposure,
        "exposure_uncomputed": uncomputed,
        "on_time_rate": round(100 * len(on_time) / len(done_or_due)) if done_or_due else None,
    }


OVERVIEW_VIEWS = ("overdue", "next15", "next90", "all")


def _in_view(view, state, days):
    if view == "overdue":
        return state == "overdue"
    if view == "next15":
        return state != "overdue" and days <= 15
    if view == "next90":
        return state != "overdue" and days <= 90
    return True


def workspace_overview(user_id, today=None, view=None, regime="", entity_id=None, limit=300):
    """Every entity's calendar merged — the practising CS's morning view.

    Built for a firm with hundreds of clients: one query for all rows, counts
    and summaries computed in a single pass, and only the rows of the chosen
    view (overdue / next15 / next90 / all, optionally one regime or entity)
    decorated and returned, at most `limit` of them. `view=None` picks the
    first non-empty of overdue, next15, next90, all."""
    today = today or today_ist()
    t = today.isoformat()
    soon = (today + timedelta(days=DUE_SOON_DAYS)).isoformat()
    ent_list = list_entities(user_id)
    by_id = {e["id"]: e for e in ent_list}
    conn = get_connection()
    rows = conn.execute(
        """SELECT o.* FROM entity_obligations o JOIN entities e ON e.id = o.entity_id
           WHERE e.user_id = ? AND e.archived = 0""", (user_id,)).fetchall()
    conn.close()

    def blank():
        return {"overdue": 0, "due_soon": 0, "upcoming": 0, "filed": 0, "exposure": 0,
                "exposure_uncomputed": 0, "on_time": 0}
    per = {eid: blank() for eid in by_id}
    total = blank()
    open_rows, regimes = [], {}
    for r in rows:
        ent = by_id.get(r["entity_id"])
        if not ent:
            continue
        rule = rb.RULES_BY_ID.get(r["rule_id"]) or CUSTOM_RULE
        due, status = r["due_date"], r["status"]
        acc = per[ent["id"]]
        if status == "pending":
            if due < t:
                state = "overdue"
                pen = rb.estimate_penalty(rule, _parse_date(due), today, ent)
                if pen:
                    if pen["amount"] is not None:
                        acc["exposure"] += pen["amount"]
                    elif pen.get("basis") and rule["id"] != DSC_RULE["id"]:
                        acc["exposure_uncomputed"] += 1
            elif due <= soon:
                state = "due_soon"
            else:
                state = "upcoming"
            acc[state] += 1
            open_rows.append((state, r, rule, ent))
            regimes[rule["regime"]] = True
        elif status == "filed":
            acc["filed"] += 1
            filed = r["filed_on"] or ""
            late = bool(filed) and filed > due and rb.estimate_penalty(
                rule, _parse_date(due), today, ent, filed_on=_parse_date(filed))
            if not late:
                acc["on_time"] += 1
    for acc in per.values():
        for k in total:
            total[k] += acc[k]

    def finish(acc):
        done_or_due = acc["filed"] + acc["overdue"]
        out = {k: v for k, v in acc.items() if k != "on_time"}
        out["on_time_rate"] = round(100 * acc["on_time"] / done_or_due) if done_or_due else None
        return out

    scoped = [(st, r, rule, ent) for st, r, rule, ent in open_rows
              if (not regime or rule["regime"] == regime) and (not entity_id or ent["id"] == entity_id)]
    days = {id(r): (_parse_date(r["due_date"]) - today).days for _, r, _, _ in scoped}
    counts = {v: sum(1 for st, r, _, _ in scoped if _in_view(v, st, days[id(r)])) for v in OVERVIEW_VIEWS}
    if view not in OVERVIEW_VIEWS:
        view = next((v for v in OVERVIEW_VIEWS[:3] if counts[v]), "all")
    chosen = [x for x in scoped if _in_view(view, x[0], days[id(x[1])])]
    chosen.sort(key=lambda x: (x[0] != "overdue", x[1]["due_date"]))
    items = [_decorate(dict(r), ent, today) for _, r, _, ent in chosen[:limit]]
    nxt = min((x for x in open_rows if x[0] == "due_soon"), key=lambda x: x[1]["due_date"], default=None)
    return {
        "entities": [{"entity": e, "summary": finish(per[e["id"]])} for e in ent_list],
        "items": items,
        "summary": finish(total),
        "today": t,
        "view": view,
        "counts": counts,
        "total": len(chosen),
        "regimes": [r for r in rb.REGIMES if r in regimes] + sorted(r for r in regimes if r not in rb.REGIMES),
        "next_due": _decorate(dict(nxt[1]), nxt[3], today) if nxt else None,
    }


def list_events(user_id, entity_id):
    if not get_entity(user_id, entity_id):
        return []
    conn = get_connection()
    rows = conn.execute("SELECT * FROM entity_events WHERE entity_id = ? ORDER BY event_date DESC, id DESC",
                        (entity_id,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["label"] = rb.EVENT_TYPES.get(d["event_type"], d["event_type"])
        out.append(d)
    return out
