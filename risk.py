"""Counterparty risk desk for risk officers.

A CRO lists the companies their institution is exposed to — borrowers,
vendors, customers, investees — with the rupee exposure. Delta watches each
one and raises signals:

  * Exchange filings (listed counterparties): insolvency, payment default,
    fraud or forensic audit, auditor or independent-director resignation,
    rating downgrades, pledge invocation, regulatory action.
  * SEBI orders and recovery proceedings naming the counterparty (from the
    regulator's feed, see reg_news.py) plus the per-company SEBI enforcement
    table for listed names.
  * IBBI public announcements of insolvency (CIRP / liquidation) — works for
    unlisted companies too.
  * Share-price shocks (listed).
  * Results not filed for 150+ days (listed).
  * Anything the risk team logs by hand (payment delays, covenant breaches).

Signals decay with age and roll up into a 0–100 score with a band and the
reasons behind it. Signals are also the alert queue: acknowledge, assign,
resolve or dismiss, with a log of who did what.

Unlisted MCA data (charges, filing defaults, struck-off status) needs a
licensed data source; it is not scraped here.
"""
from __future__ import annotations

import queue
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from database import get_connection, get_all_known_companies, INDUSTRY_WATCHLISTS

SEVERITY_WEIGHT = {"critical": 40, "high": 20, "medium": 8, "low": 2}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
BANDS = [(60, "Severe"), (35, "High"), (15, "Moderate"), (0, "Low")]
# Score floor for an open critical signal in the last 90 days (the Severe line).
CRITICAL_FLOOR = 60
CATEGORIES = {
    "borrower": "Borrower", "vendor": "Vendor / supplier", "customer": "Customer",
    "investee": "Investee", "guarantor": "Guarantor", "other": "Other",
}
# Low-severity signals (a tax order, a routine director exit) feed the score
# and the counterparty timeline but are notes, not alerts: the queue, the
# alert counts and notifications cover medium and above.
ALERT_SEVERITIES = ("critical", "high", "medium")
STATUSES = {"open": "Open", "acknowledged": "Acknowledged", "resolved": "Resolved", "dismissed": "Dismissed"}
# Filter groups for the alert queue -> the `source` values stored on signals.
SOURCE_GROUPS = {
    "filing": ("Exchange filings", ("Exchange filing", "Exchange filings")),
    "sebi": ("SEBI", ("SEBI",)),
    "ibbi": ("IBBI", ("IBBI",)),
    "market": ("Market data", ("Market data",)),
    "manual": ("Logged by your team", ("Logged by risk team",)),
}
# Repeats of the same kind of signal count for less: the fourth tax order
# says little the first did not.
REPEAT_FACTORS = (1.0, 0.5, 0.25)
LOOKBACK_DAYS = 365
MERGE_WINDOW_DAYS = 7
SCAN_EVERY_HOURS = 6
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_filings_fetcher = None


def set_filings_fetcher(fn):
    """main.process_filings, injected to avoid a circular import."""
    global _filings_fetcher
    _filings_fetcher = fn


def _frozen():
    try:
        from demo_cache import frozen
        return frozen()
    except Exception:
        return False


def live_enabled():
    """False when DELTA_DEMO_FREEZE forbids outbound calls."""
    return not _frozen()


def init_risk_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS counterparties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            symbol TEXT DEFAULT '',
            cin TEXT DEFAULT '',
            pan TEXT DEFAULT '',
            category TEXT NOT NULL DEFAULT 'other',
            exposure_inr REAL DEFAULT 0,
            owner TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            last_scanned_at TEXT DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cp_user ON counterparties(user_id, archived);

        CREATE TABLE IF NOT EXISTS cp_people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            counterparty_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            din TEXT DEFAULT '',
            designation TEXT DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            added_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cp_people ON cp_people(counterparty_id);

        CREATE TABLE IF NOT EXISTS risk_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            counterparty_id INTEGER NOT NULL,
            signal_key TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT DEFAULT '',
            source TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            event_date TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            assignee TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(counterparty_id, fingerprint)
        );
        CREATE INDEX IF NOT EXISTS idx_signals_cp ON risk_signals(counterparty_id, event_date DESC);

        CREATE TABLE IF NOT EXISTS risk_signal_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            note TEXT DEFAULT '',
            at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_signal_log ON risk_signal_log(signal_id);

        CREATE TABLE IF NOT EXISTS risk_scores (
            counterparty_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            score INTEGER NOT NULL,
            band TEXT NOT NULL,
            PRIMARY KEY (counterparty_id, day)
        );

        CREATE TABLE IF NOT EXISTS ibbi_announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            debtor_name TEXT NOT NULL,
            norm_name TEXT NOT NULL,
            pa_type TEXT DEFAULT '',
            announce_date TEXT NOT NULL,
            last_submission TEXT DEFAULT '',
            applicant TEXT DEFAULT '',
            ip_name TEXT DEFAULT '',
            pdf_url TEXT DEFAULT '',
            fetched_at TEXT NOT NULL,
            UNIQUE(norm_name, announce_date, pa_type)
        );
        CREATE INDEX IF NOT EXISTS idx_ibbi_norm ON ibbi_announcements(norm_name);
    """)
    conn.commit()
    conn.close()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _s(v):
    """A request value as a stripped string; JSON numbers, lists and nulls
    must never reach .strip() or a dict lookup."""
    if v is None or isinstance(v, (dict, list, bool)):
        return ""
    return str(v).strip()


def _choice(v, allowed, default):
    return v if isinstance(v, str) and v in allowed else default


# ── Names ───────────────────────────────────────────────────────────────────

_SUFFIXES = r"\b(private|pvt|limited|ltd|llp|the|co|company|corporation|corp|inc)\b"


def norm_name(name):
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(_SUFFIXES, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_match(cp_norm, text_norm):
    """'exact' | 'partial' | None. Partial needs a 2+ word name found as a
    whole-word run, so 'tata' alone never matches every Tata company."""
    if not cp_norm or not text_norm:
        return None
    if cp_norm == text_norm:
        return "exact"
    if len(cp_norm.split()) >= 2 and re.search(rf"\b{re.escape(cp_norm)}\b", text_norm):
        return "partial"
    return None


def mask_pan(pan):
    pan = (pan or "").strip()
    if len(pan) < 6:
        return pan
    return pan[:2] + "*" * (len(pan) - 5) + pan[-3:]


# ── Counterparties ──────────────────────────────────────────────────────────

_CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
_LLPIN_RE = re.compile(r"^[A-Z]{3}-\d{4}$")
_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")
_URL_RE = re.compile(r"^https?://[^\s<>\"']+$", re.I)


def _known_symbols():
    syms = set(get_all_known_companies() or [])
    for pairs in INDUSTRY_WATCHLISTS.values():
        syms |= {s for s, _ in pairs}
    return syms


def _known_names():
    return {s: n for pairs in INDUSTRY_WATCHLISTS.values() for s, n in pairs}


def parse_bulk(text):
    """One counterparty per line: name, then optional NSE symbol / CIN / PAN,
    category and exposure in any order, separated by commas, tabs or pipes."""
    known = _known_symbols()
    names = _known_names()
    out = []
    for line in (text or "").splitlines()[:500]:
        # Rows pasted from a spreadsheet are tab-separated and may carry
        # Indian-grouped amounts (1,50,00,000), so commas only split when
        # there are no tabs.
        sep = r"[\t|]" if "\t" in line else r"[,|;]"
        if "\t" not in line:
            # A comma-separated row can still carry an Indian-grouped amount
            # ("Omega Ltd, 1,50,00,000, borrower"). Join the commas inside a
            # field that is wholly such a number, so it stays one field; a CIN
            # or LLPIN ending in digits is never touched (it is not a whole field).
            line = _GROUPED_FIELD.sub(lambda m: m.group(1) + m.group(2).replace(",", "") + (m.group(3) or "") + m.group(4), line)
        parts = [p.strip() for p in re.split(sep, line) if p.strip()]
        if not parts:
            continue
        rec = {"name": "", "symbol": "", "cin": "", "pan": "", "category": "other", "exposure_inr": 0}
        for p in parts:
            up = p.upper().replace(" ", "")
            num = re.sub(r"[₹\s]|rs\.?|inr", "", p.lower()).replace(",", "")
            if _CIN_RE.match(up) or _LLPIN_RE.match(up):
                rec["cin"] = up
            elif _PAN_RE.match(up):
                rec["pan"] = up
            elif " " not in p.strip() and up in known and not rec["symbol"]:
                # Only a single token can be a symbol; "Tata Steel" squeezed to
                # TATASTEEL must stay the name.
                rec["symbol"] = up
            elif p.lower() in CATEGORIES:
                rec["category"] = p.lower()
            elif p.lower() in ("vendor / supplier", "supplier"):
                rec["category"] = "vendor"
            elif re.fullmatch(r"\d+(\.\d+)?(cr|crore|crores|l|lakh|lakhs|lac|lacs)?", num):
                rec["exposure_inr"] = _parse_amount(num)
            elif not rec["name"]:
                rec["name"] = p[:200]
        if not rec["name"] and rec["symbol"]:
            rec["name"] = names.get(rec["symbol"], rec["symbol"])
        if rec["name"]:
            out.append(rec)
    return out


_GROUPED_FIELD = re.compile(
    r"(?:(?<=^)|(?<=[,|;]))(\s*(?:₹\s*|rs\.?\s*|inr\s*)?)"
    r"(\d{1,3}(?:,\d{2})*,\d{3}|\d{1,3}(?:,\d{3})+)(\.\d+)?"
    r"(\s*(?:cr|crore|crores|l|lakh|lakhs|lac|lacs)?\s*)(?=[,|;]|$)", re.I)


def _parse_amount(s):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(cr|crore|crores|l|lakh|lakhs|lac|lacs)?", s)
    if not m:
        return 0
    v = float(m.group(1))
    unit = m.group(2) or ""
    if unit.startswith("cr"):
        v *= 1e7
    elif unit:
        v *= 1e5
    return v


def _amount(v):
    """Exposure in rupees from a number or numeric string; ValueError otherwise."""
    if v is None or v == "":
        return 0.0
    if isinstance(v, (bool, dict, list)):
        raise ValueError("Exposure must be an amount in rupees.")
    try:
        n = float(str(v).replace(",", "").strip())
    except ValueError:
        raise ValueError("Exposure must be an amount in rupees.")
    if n < 0 or n != n or n > 1e15:
        raise ValueError("Exposure must be a positive amount in rupees.")
    return round(n, 2)


def _clean_ids(data, fields):
    """Upper-case and validate symbol / CIN / PAN where present."""
    out = {}
    if "symbol" in fields:
        sym = _s(data.get("symbol")).upper()
        if sym and not _SYMBOL_RE.match(sym):
            raise ValueError("NSE symbol should be letters and numbers only, like HINDUNILVR.")
        out["symbol"] = sym
    if "cin" in fields:
        cin = _s(data.get("cin")).upper().replace(" ", "")
        if cin and not (_CIN_RE.match(cin) or _LLPIN_RE.match(cin)):
            raise ValueError("CIN should be 21 characters, like L15140MH1933PLC002030 (or an LLPIN like AAB-1234).")
        out["cin"] = cin
    if "pan" in fields:
        pan = _s(data.get("pan")).upper().replace(" ", "")
        if pan and not _PAN_RE.match(pan):
            raise ValueError("PAN should be 10 characters, like AAACH1004N.")
        out["pan"] = pan
    return out


def find_duplicate(user_id, name, symbol="", exclude_id=None):
    """Id of an active counterparty with the same name or NSE symbol, other
    than `exclude_id` (the one being edited)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM counterparties WHERE user_id = ? AND archived = 0 AND id != ? "
        "AND (lower(name) = lower(?) OR (symbol != '' AND symbol = ?))",
        (user_id, exclude_id or 0, _s(name), _s(symbol).upper())).fetchone()
    conn.close()
    return row["id"] if row else None


def add_counterparty(user_id, data, scan=True):
    """Create a counterparty (or return the existing one with that name or
    symbol). `scan` starts a background live scan."""
    name = _s(data.get("name"))[:200]
    if not name:
        raise ValueError("Name is required.")
    ids = _clean_ids(data, ("symbol", "cin", "pan"))
    exposure = _amount(data.get("exposure_inr"))
    cat = _choice(data.get("category"), CATEGORIES, "other")
    dup = find_duplicate(user_id, name, ids["symbol"])
    if dup:
        return dup
    now = _now()
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO counterparties (user_id, name, symbol, cin, pan, category, exposure_inr, owner, notes,
               created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, name, ids["symbol"], ids["cin"], ids["pan"], cat, exposure,
         _s(data.get("owner"))[:120], _s(data.get("notes"))[:1000], now, now))
    cid = cur.lastrowid
    conn.commit()
    conn.close()
    if scan:
        start_initial_scan(cid)
    return cid


_initial_queue = queue.Queue()
_initial_workers = []
INITIAL_SCAN_WORKERS = 2


def _initial_worker():
    while True:
        cid = _initial_queue.get()
        try:
            _initial_scan(cid)
        except Exception as e:
            print(f"[RISK] initial scan {cid} failed: {e}")
        finally:
            _initial_queue.task_done()


def start_initial_scan(cid):
    """Queue a background live scan for a newly added counterparty (fetches
    its filings first if we have none). A small fixed pool works the queue,
    so onboarding 200 names does not open 200 threads against NSE and IBBI.
    No-op when outbound calls are frozen."""
    if _frozen():
        return False
    _initial_queue.put(cid)
    with _live_lock:
        while len(_initial_workers) < INITIAL_SCAN_WORKERS:
            t = threading.Thread(target=_initial_worker, name=f"risk-initial-{len(_initial_workers)}", daemon=True)
            t.start()
            _initial_workers.append(t)
    return True


def _initial_scan(cid):
    cp = _get(cid)
    if not cp:
        return
    try:
        if cp["symbol"] and _filings_fetcher:
            from database import count_filings
            if not count_filings(cp["symbol"]):
                _filings_fetcher(cp["symbol"])
    except Exception as e:
        print(f"[RISK] initial filings fetch failed for {cp['symbol']}: {e}")
    try:
        scan_counterparty(cid, live=True)
    except Exception as e:
        print(f"[RISK] initial scan failed for {cid}: {e}")


def update_counterparty(user_id, cid, data):
    before = get_counterparty(user_id, cid)
    if not before:
        return False
    fields = {}
    if "name" in data:
        name = _s(data.get("name"))[:200]
        if not name:
            raise ValueError("Name is required.")
        fields["name"] = name
    fields.update(_clean_ids(data, [k for k in ("symbol", "cin", "pan") if k in data]))
    for k, n in (("owner", 120), ("notes", 1000)):
        if k in data:
            fields[k] = _s(data.get(k))[:n]
    cat = _choice(data.get("category"), CATEGORIES, None)
    if cat:
        fields["category"] = cat
    if "exposure_inr" in data:
        fields["exposure_inr"] = _amount(data.get("exposure_inr"))
    if not fields:
        return True
    fields["updated_at"] = _now()
    conn = get_connection()
    conn.execute(f"UPDATE counterparties SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ? AND user_id = ?",
                 (*fields.values(), cid, user_id))
    old_sym = before["symbol"]
    if old_sym and fields.get("symbol", old_sym) != old_sym:
        # Filings, price moves and the board were the old symbol's, i.e.
        # another company's. Untouched signals go; ones the team has worked
        # on are dismissed with a note so their log still reads.
        rows = conn.execute(
            """SELECT s.id, EXISTS(SELECT 1 FROM risk_signal_log l WHERE l.signal_id = s.id) AS worked
               FROM risk_signals s WHERE s.counterparty_id = ? AND s.status != 'dismissed'
               AND s.source IN ('Exchange filing', 'Exchange filings', 'Market data')""", (cid,)).fetchall()
        note = f"NSE symbol changed from {old_sym} to {fields['symbol'] or 'none'}."
        for r in rows:
            if r["worked"]:
                conn.execute("UPDATE risk_signals SET status = 'dismissed', updated_at = ? WHERE id = ?",
                             (fields["updated_at"], r["id"]))
                conn.execute("INSERT INTO risk_signal_log (signal_id, user_id, action, note, at) VALUES (?,?,?,?,?)",
                             (r["id"], user_id, "dismissed", note, fields["updated_at"]))
            else:
                conn.execute("DELETE FROM risk_signals WHERE id = ?", (r["id"],))
        conn.execute("DELETE FROM cp_people WHERE counterparty_id = ? AND source = 'governance'", (cid,))
    conn.commit()
    conn.close()
    return True


def archive_counterparty(user_id, cid):
    conn = get_connection()
    cur = conn.execute("UPDATE counterparties SET archived = 1, updated_at = ? WHERE id = ? AND user_id = ? AND archived = 0",
                       (_now(), cid, user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def _get(cid):
    conn = get_connection()
    row = conn.execute("SELECT * FROM counterparties WHERE id = ? AND archived = 0", (cid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_counterparty(user_id, cid):
    cp = _get(cid)
    return cp if cp and cp["user_id"] == user_id else None


def list_counterparties(user_id):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM counterparties WHERE user_id = ? AND archived = 0 ORDER BY name",
                        (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def all_counterparty_symbols():
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT symbol FROM counterparties WHERE archived = 0 AND symbol != ''").fetchall()
    conn.close()
    return [r["symbol"] for r in rows]


# ── Filing classifier ───────────────────────────────────────────────────────
#
# Calibrated against ~6,300 real NSE announcements for five large issuers.
# `title` is the exchange's category ("Credit Rating", "Action(s) taken or
# orders passed"), `description` the one-line summary, `body` the extracted
# letter text when we have it. Most announcements are routine; the rules
# below are ordered so the unambiguous adverse events win, routine
# categories only trip on unambiguous phrases, and benign look-alikes
# (schemes sanctioned by NCLT, auditor rotation, ESG scores, rating
# reaffirmations, pledge releases, trading-window notices) fall through.

_ROUTINE_TITLES = (
    "press release", "newspaper", "investor presentation", "analyst", "institutional investor",
    "con. call", "trading window", "loss of share", "duplicate share", "shareholders meeting",
    "postal ballot", "annual general meeting", "agm", "book closure", "record date", "dividend",
    "esop", "esos", "esps", "sbeb", "allotment", "certificate under", "financial result",
    "results update", "integrated filing", "transcript", "recording of", "minutes of",
    "notice of", "voting pattern", "bonus", "stock split", "regulation 44", "registrar",
    "increase in paid-up", "extinguishment", "grant of", "scheme of arrangement",
    "scheme of amalgamation", "amalgamation", "merger", "demerger", "court convened",
    "capacity addition", "investor meet", "publish audited", "outcome of agm",
    "outcome of annual general",
)

_IBC_CONTEXT = re.compile(
    r"insolvency|bankruptcy|\bibc\b|\bcirp\b|resolution professional|operational creditor|"
    r"financial creditor|section (7|9|10) of (the )?(ibc|insolvency|i&b|code)|corporate debtor")
_SCHEME = re.compile(r"scheme of (arrangement|amalgamation)|composite scheme|compromise or arrangement|section 23[0-2]")
_IBC_STRONG = re.compile(r"insolvency|bankruptcy|\bibc\b|\bcirp\b|resolution professional|section (7|9|10) of")
_IBC_ADMITTED = re.compile(
    r"\bcirp\b|admitted|admission of|initiat\w* (of )?(the )?corporate insolvency|commence\w* (of )?(the )?corporate insolvency|"
    r"interim resolution professional|moratorium|corporate insolvency resolution process")
_SUBSIDIARY = re.compile(r"subsidiar|step[- ]down|associate compan|joint venture|\bjv\b")
_FRAUD_BENIGN = re.compile(r"anti[- ]fraud|fraud (risk|prevention|awareness)|fraudulent (share|certificate|transfer of share)|"
                           r"impersonat|fake (website|email|social)|caution(ary)? (notice|to investors)")
_DEFAULT_WORDS = ("default in payment", "default in repayment", "defaulted", "delay in payment of interest",
                  "delay in payment of principal", "delay in repayment", "delay in servicing",
                  "non-payment of interest", "non payment of interest", "non-payment of principal",
                  "failed to pay", "failure to pay", "unable to pay", "not been able to pay",
                  "defaults on payment", "disclosure of default", "default on payment", "default on loan")
_DEFAULT_NEGATION = re.compile(r"\bno default|not in default|\bnot defaulted|\bha(s|ve) not (made any )?default|"
                               r"\bno (instance of )?default|not committed any default|\bnil\b|has been paid|have been paid|paid on (the )?due date|"
                               r"\bno delay|not applicable|no instance|timely (payment|paid|serviced)|officer who is in default")
_TAX_AUTH = re.compile(r"\bgst\b|cgst|sgst|igst|state tax|commercial tax|income[- ]tax|\bcustoms\b|\bexcise\b|service tax|"
                       r"superintendent of taxes|assessment order|tax demand|\bdgft\b|goods and services tax|"
                       r"commissioner of (central|state|gst)|\bvat\b")
# Nearly every Reg 30 letter cites "SEBI (Listing Obligations ...)", so SEBI
# only counts as the acting regulator next to an enforcement word.
_REGULATOR = re.compile(r"(order|letter|warning|notice|penalty|direction)s? .{0,40}(\bsebi\b|securities and exchange board)|"
                        r"(\bsebi\b|securities and exchange board of india)\W{0,3}(has |had )?(passed|issued|imposed|levied|order|warning|"
                        r"notice|penalty|direction|adjudicat|show cause)|reserve bank|\brbi\b|competition commission|\bcci\b|"
                        r"fssai|food safety|ministry|pollution control|\bnhb\b|\birdai\b|\bpfrda\b|\bdgca\b|"
                        r"prohibitory order|suspension of (the )?licen[cs]e|cancellation of (the )?licen[cs]e|closure order")
_FAVOURABLE = re.compile(r"stay (order )?(has been )?granted|stay order|granted (a )?stay|set aside|quashed|"
                         r"in favou?r of the company|allowed the appeal|dropped the (proceedings|demand)")
_ID_CONCERN = re.compile(r"concern|governance|disagree|difference(s)? of opinion|not in a position to|lack of|"
                         r"not been provided|not provided|unable to discharge|independence")
_ID_BENIGN = re.compile(r"completion of (his|her|their|the)? ?(second |first )?(tenure|term)|retire|superannuation|"
                        r"health|medical|demise|passed away|appointed as .{0,60}(government|governor|ambassador|secretary to)|"
                        r"attain\w* the age|age of 75")
# Not "rated" or a bare "care": "rated capacity" and "health care" are not ratings.
_RATING_CONTEXT = re.compile(r"credit rating|\bratings?\b|crisil|\bicra\b|\bcare ?(ratings?|edge)\b|"
                             r"\bcare (aaa|aa|a|bbb|bb|b|c|d|a[1-4])(?![a-z])|india ratings|brickwork|acuite|infomerics")
# Rating letters carry hedged boilerplate: "reserves the right to suspend,
# withdraw or revise", "factors that could lead to a downgrade", "may be moved
# to Issuer Not Cooperating". Only sentences stating what happened count.
_HEDGED = re.compile(r"\b(may|might|could|would|reserves? the right|[Ii]n case|[Ii]n the event|[Ff]ailing which|[Uu]nless)\b|"
                     r"[Ff]actors\b|[Ss]ensitivit")
# A resignation and an auditor in the same summary, with no appointment
# between them: "re-appointment of statutory auditors" next to a director's
# resignation is not an auditor resigning.
_AUDITOR_RESIGNED = re.compile(
    r"(resign|stepped down|step down|relinquish)(?:(?!appoint|approv)[^;]){0,160}?auditor|"
    r"auditors?\b(?:(?!appoint|approv)[^;]){0,120}?(resign|stepped down|step down|relinquish)")
_RATING_TOKEN = re.compile(r"(?<![A-Za-z0-9])(A[1-4]\+?|AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|C[+-]?|D)(?![A-Za-z0-9])")
_LT_SCALE = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-",
             "B+", "B", "B-", "C+", "C", "C-", "D"]
_ST_SCALE = ["A1+", "A1", "A2+", "A2", "A3+", "A3", "A4+", "A4", "D"]


def _stated(text):
    """`text` without its hedged sentences (see _HEDGED)."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])", text or "")
    return " ".join(p for p in parts if not _HEDGED.search(p))


def _rating_move(raw):
    """-1 downgrade, +1 upgrade, 0 unchanged, None unknown — read from a
    'revised from CARE AA-; Stable to CARE A+; Stable' style phrase. Letters
    often say 'received from CRISIL Ratings to inform' first, so every
    from/to pair is tried."""
    for m in re.finditer(r"from\s+(.{0,50}?)\s+to\s+(?=(.{0,50}))", raw, flags=re.I | re.S):
        a = _RATING_TOKEN.search(m.group(1))
        b = _RATING_TOKEN.search(m.group(2))
        if not a or not b:
            continue
        a, b = a.group(1), b.group(1)
        for scale in (_ST_SCALE, _LT_SCALE):
            if a in scale and b in scale:
                ia, ib = scale.index(a), scale.index(b)
                return 0 if ia == ib else (1 if ib < ia else -1)
    return None


def _classify_rating(t, tb, raw, title_l):
    """Rating actions. ESG scores are not credit ratings; reaffirmations,
    upgrades and fresh assignments are not risk signals. `t` decides whether
    this is about a rating at all and wins when it names the action; otherwise
    the letter's non-hedged sentences settle which way."""
    if not _RATING_CONTEXT.search(t):
        return None
    esg = r"\besg\b|\besq\b|sustainability rating"
    if re.search(esg, t):
        return None
    inc = r"not cooperating|non[- ]cooperat"
    down = r"downgrad|default grade|rating of d\b|to d\b|suspend"
    # The exchange's summary line is authoritative when it names the action.
    if re.search(inc, t):
        return ("rating_adverse", "high", "Rating agency flags the company as not cooperating")
    if re.search(down, t):
        return ("rating_adverse", "high", "Credit rating downgraded")
    if re.search(esg, tb):
        return None
    raw = _stated(raw)
    t = re.sub(r"\s+", " ", raw.lower())
    if re.search(inc, t):
        return ("rating_adverse", "high", "Rating agency flags the company as not cooperating")
    if re.search(down, t):
        return ("rating_adverse", "high", "Credit rating downgraded")
    move = _rating_move(raw)
    if move == -1:
        return ("rating_adverse", "high", "Credit rating downgraded")
    if re.search(r"(stable|positive|developing)\s+to\s+negative|watch with negative|negative implications|"
                 r"outlook (revised |changed )?(to )?negative|credit watch negative|rating watch negative", t):
        return ("rating_watch", "medium", "Rating outlook or watch turned negative")
    if move == 1 or re.search(r"upgrad|reaffirm|re-affirm|positive|assigned|stable", t):
        return None
    if "withdr" in t:
        return ("rating_withdrawn", "low", "Credit rating withdrawn")
    if "revision" in title_l or "revised" in t:
        return ("rating_revised", "low", "Credit rating revised; open the filing for the direction")
    return None


def classify_filing(title, description="", body=""):
    """(signal_key, severity, label) for an exchange filing, or None.

    `body` is the extracted letter text where available; it only settles
    rating direction and resignation reasons, never raises a signal alone."""
    title_l = (title or "").lower()
    t = f"{title} {description}".lower()
    t = re.sub(r"\s+", " ", t)
    raw = f"{title} {description} {(body or '')[:6000]}"
    tb = re.sub(r"\s+", " ", raw.lower())
    has = lambda words, s=t: any(w in s for w in words)
    routine = any(k in title_l for k in _ROUTINE_TITLES)

    # 1. Insolvency under the IBC — only with IBC vocabulary, so a scheme of
    #    arrangement sanctioned by the NCLT never reads as insolvency.
    # An NCLT-convened creditors' meeting for a scheme of arrangement uses
    # "financial creditors" too; without real IBC words it is not insolvency.
    scheme_only = _SCHEME.search(t) and not _IBC_STRONG.search(t)
    if _IBC_CONTEXT.search(t) and not scheme_only:
        if re.search(r"resolution applicant|resolution plan submitted by the company|acquisition of .{0,80}under", t):
            return None
        if re.search(r"dismiss|withdrawn|withdrawal of|settled|closure of (the )?(cirp|insolvency)|set aside", t):
            return ("insolvency_closed", "low", "Insolvency petition dismissed, withdrawn or settled")
        if _SUBSIDIARY.search(t):
            return ("insolvency_subsidiary", "high", "Insolvency proceedings at a subsidiary or associate")
        if re.search(r"(filed|initiated) by the company|company has (filed|initiated)|company filed", t):
            return None
        if _IBC_ADMITTED.search(t):
            return ("insolvency", "critical", "Insolvency proceedings admitted (CIRP)")
        if not routine:
            return ("insolvency_petition", "high", "Insolvency petition filed against the company")

    # 2. Liquidation / winding up.
    if re.search(r"\bliquidat(ion|or|ed)\b|winding[- ]up|wound up", t):
        if "voluntar" in t:
            pass
        elif _SUBSIDIARY.search(t):
            if not routine:
                return ("liquidation_subsidiary", "low", "Subsidiary in liquidation")
        elif re.search(r"liquidation order|order (of|for) (the )?liquidation|liquidator (has been |was )?appointed|appointed (an? |the )?(official )?liquidator|ordered .{0,30}wound up|winding[- ]up order", t):
            return ("liquidation", "critical", "Liquidation or winding-up ordered")
        elif not routine:
            return ("liquidation", "high", "Liquidation or winding-up disclosed")

    # 3. Payment default and lender enforcement.
    if "wilful defaulter" in t and not re.search(r"\bnot\b.{0,30}wilful defaulter", t):
        return ("wilful_defaulter", "critical", "Declared a wilful defaulter")
    hits = [w for w in _DEFAULT_WORDS if w in t]
    if hits and not _DEFAULT_NEGATION.search(t):
        if "disclosure of default" in t and not set(hits) - {"disclosure of default", "defaults on payment", "default on payment"}:
            # The SEBI default-disclosure format is also filed with nil figures.
            if body and _DEFAULT_NEGATION.search(tb):
                return None
            if not body:
                return ("default", "high", "Default disclosure filed; check the amounts")
        return ("default", "critical", "Payment default or delay disclosed")
    if re.search(r"(account|loan)s?\b.{0,40}?(classified|declared) as (an? )?(npa|non[- ]performing)|declared (an? )?npa", t):
        return ("npa", "critical", "Loan account classified as NPA")
    if not routine:
        if re.search(r"sarfaesi|section 13\(2\)|recall(ed)? (notice|of (the )?loan)|loan recall", t):
            return ("lender_action", "high", "Lender enforcement (recall or SARFAESI notice)")
        if re.search(r"one[- ]time settlement|\bots\b", t):
            return ("debt_restructuring", "high", "One-time settlement with lenders")
        if re.search(r"debt restructuring|restructuring of (its |the )?(debt|loans|borrowings)|inter[- ]creditor agreement|"
                     r"prudential framework for resolution", t):
            return ("debt_restructuring", "high", "Debt restructuring")

    # 4. Fraud, investigation, arrest.
    if "forensic audit" in t:
        return ("forensic_audit", "critical", "Forensic audit initiated")
    if re.search(r"enforcement directorate|\bcbi\b|central bureau of investigation|serious fraud investigation|\bsfio\b|"
                 r"economic offences wing", t):
        return ("investigation", "critical", "Investigation by ED, CBI, SFIO or police")
    if re.search(r"\bfraud|misappropriat|siphon|diversion of funds", t) and not _FRAUD_BENIGN.search(t):
        if re.search(r"borrower|reported to (the )?(rbi|reserve bank)", t):
            return ("fraud_borrower", "medium", "Fraud in a borrower account reported")
        if not routine or re.search(r"fraud (by|committed|at)|misappropriat|siphon|diversion of funds", t):
            return ("fraud", "critical", "Fraud or diversion of funds disclosed")
    if not routine and re.search(r"\barrest(ed)?\b", t):
        return ("arrest", "critical", "Arrest of a promoter, director or officer")
    if re.search(r"search and seizure|search operation|searches? (was |were )?conducted|survey (operation|under section 133a)|"
                 r"income[- ]tax (search|survey)", t):
        return ("search", "high", "Search or survey by authorities")
    if not routine and "whistle" in t and "complaint" in t:
        return ("whistleblower", "high", "Whistle-blower complaint disclosed")

    resign = bool(re.search(r"resign|stepped down|step down|relinquish", t))

    # 5. Auditors.
    aud = _AUDITOR_RESIGNED.search(t) if resign else None
    if aud:
        if re.search(r"secretarial auditor|internal auditor|cost auditor", t[max(0, aud.start() - 20):aud.end()]):
            return ("auditor_resigned_other", "low", "Secretarial, internal or cost auditor resigned")
        return ("auditor_resigned", "high", "Statutory auditor resigned")
    if re.search(r"\b(qualified|adverse) (audit )?opinion|disclaimer of opinion|impact of audit qualification", t) \
            and not re.search(r"\bunmodified|\bunqualified", t):
        return ("audit_qualified", "medium", "Auditor qualified the accounts")
    if re.search(r"material uncertainty.{0,60}going concern|going concern.{0,40}(doubt|uncertain)|ability to continue as a going concern", t) \
            and not re.search(r"no material uncertainty", t):
        return ("going_concern", "high", "Auditor raised a going-concern doubt")

    # 6. Credit ratings.
    rating = _classify_rating(t, tb, raw, title_l)
    if rating:
        return rating

    # 7. Pledges and encumbrances.
    pledge = re.search(r"pledg|encumb", t)
    if pledge and re.search(r"invo(cation|ked)", t):
        return ("pledge_invoked", "high", "Pledged shares invoked")
    if pledge and not routine and not re.search(r"release|revocation|revoked|released|pledges support|"
                                                r"not (created|made|encumbered)|no encumbrance|31\(4\)|trading window", t):
        if "share" in t:
            return ("pledge", "medium", "Promoter shares pledged or encumbered")

    # 8. Board and management exits.
    if resign and "independent director" in t:
        reason = tb
        if _ID_CONCERN.search(reason):
            return ("id_resigned", "high", "Independent director resigned citing concerns")
        if _ID_BENIGN.search(reason):
            return ("id_resigned_tenure", "low", "Independent director left (tenure, age or health)")
        return ("id_resigned", "medium", "Independent director resigned before term")
    if resign and re.search(r"chief financial officer|\bcfo\b|chief executive|\bceo\b|managing director|\bmd\b", t):
        return ("kmp_resigned", "medium", "CEO, MD or CFO resigned")
    if resign and re.search(r"company secretary|compliance officer|key managerial", t):
        return ("cs_resigned", "low", "Company secretary or other KMP resigned")

    # 9. Regulatory action, tax orders, litigation.
    if re.search(r"attachment of|attached (the |its )?(bank|propert|asset)|freez(e|ing) of (bank )?account", t):
        return ("attachment", "high", "Assets or bank accounts attached")
    reg_title = title_l.startswith("action(s)") or "orders passed" in title_l
    if reg_title or re.search(r"show cause|penalty|adjudicat|demand order|\bsummons\b|prohibitory order", t):
        if _FAVOURABLE.search(tb):
            return ("regulatory_update", "low", "Favourable update on a regulatory matter")
        regulator = _REGULATOR.search(tb)
        if _TAX_AUTH.search(tb) and not regulator:
            return ("tax_order", "low", "Tax order or demand")
        if routine and not reg_title:
            return None
        if regulator or re.search(r"penalty|show cause|adjudicat|\bsummons\b", t):
            return ("regulatory_action", "medium", "Regulatory action, penalty or order")
        # The exchange category alone: large issuers file these for routine
        # tax and consumer-forum orders, so it stays low until the filing says more.
        return ("order_disclosed", "low", "Order or action by an authority; details in the filing")
    if "pendency of litigation" in title_l or "litigation" in title_l:
        return ("litigation", "low", "Litigation update")

    # 10. Operations.
    if not routine:
        if re.search(r"strikes?|lock-?outs?|disturbance", title_l) or re.search(r"\b(strike|lockout|lock-out)\b", t):
            return ("operations", "medium", "Strike, lockout or disruption at operations")
        if re.search(r"\bfire\b.{0,80}(plant|factory|unit|premises|warehouse|facility)|"
                     r"(plant|factory|unit|premises|warehouse|facility).{0,80}\bfire\b", t):
            return ("operations", "medium", "Fire or accident at a facility")
        if re.search(r"cyber[- ]?(security )?(incident|attack)|ransomware|data breach", t):
            return ("cyber", "medium", "Cyber security incident")
        if re.search(r"suspension of (operations|production|manufacturing)|(temporary )?(shutdown|closure) of (the |its )?"
                     r"(plant|factory|unit|operations)|force majeure|(operations|production|manufacturing) "
                     r"(has been |have been |was |were |is |are )?(temporarily )?(suspended|halted|shut down)", t):
            return ("operations", "medium", "Operations suspended at a facility")
        if re.search(r"suspension of trading|trading (in .{0,40})?(has been |shall be )?suspended", t) \
                and not re.search(r"debenture|\bncds?\b|commercial paper|bonds?\b|redemption|record date", t):
            return ("trading_suspended", "high", "Trading in the shares suspended")

    # 11. Results delayed.
    if has(["delay in submission of financial results", "postponement of board meeting",
            "postponed the board meeting", "unable to submit the financial results"]):
        return ("results_delay", "medium", "Results or board meeting delayed")

    # 12. Low-weight housekeeping worth a line in the timeline.
    if resign and "director" in t and not routine:
        return ("director_resigned", "low", "Director resigned")
    if "change in auditor" in t and not re.search(r"rotation|completion of|re-?appoint|appointment of|appointed|retir", tb):
        return ("auditor_change", "low", "Change in auditors, reason not stated")
    return None


def _filing_date(f):
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime((f.get("date") or "")[:20].strip(), fmt).date()
        except ValueError:
            continue
    sd = f.get("sort_date") or ""
    try:
        return date.fromisoformat(sd[:10])
    except ValueError:
        return None


_RESULTS_RE = re.compile(r"financial result|results? update|results for the|integrated filing|unaudited financial|"
                         r"audited financial|publish audited")


def _is_results_filing(f):
    return bool(_RESULTS_RE.search(f"{f.get('title') or ''} {f.get('description') or ''}".lower()))


# ── Signal collection ───────────────────────────────────────────────────────

def _insert_signal(conn, cid, key, severity, title, detail, source, url, event_date, fingerprint):
    now = _now()
    cur = conn.execute(
        """INSERT OR IGNORE INTO risk_signals (counterparty_id, signal_key, severity, title, detail, source,
               source_url, event_date, detected_at, fingerprint, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, key, severity, title[:300], (detail or "")[:1000], source, (url or "")[:600],
         event_date.isoformat() if isinstance(event_date, date) else str(event_date)[:10], now, fingerprint[:200], now))
    return cur.rowcount > 0


def _collect_filings(conn, cp, since):
    if not cp["symbol"]:
        return []
    rows = conn.execute(
        "SELECT id, title, description, date, sort_date, pdf_link, substr(raw_text, 1, 6000) AS body "
        "FROM filings WHERE company = ?", (cp["symbol"],)).fetchall()
    # Signals already raised from filings, so the same event filed twice
    # (a letter plus the structured Reg 30 form a day later) stays one alert.
    existing = conn.execute(
        "SELECT signal_key, event_date, fingerprint FROM risk_signals WHERE counterparty_id = ? AND source = 'Exchange filing'",
        (cp["id"],)).fetchall()
    seen_fps = {r["fingerprint"] for r in existing}
    raised = []
    for r in existing:
        try:
            raised.append((r["signal_key"], date.fromisoformat(r["event_date"][:10])))
        except ValueError:
            continue
    dated = []
    latest_result = None
    latest_any = None
    for r in rows:
        f = dict(r)
        d = _filing_date(f)
        if not d:
            continue
        latest_any = max(latest_any or d, d)
        if _is_results_filing(f):
            latest_result = max(latest_result or d, d)
        if d >= since:
            dated.append((d, f))
    dated.sort(key=lambda x: (x[0], x[1]["id"]))
    new = []
    for d, f in dated:
        c = classify_filing(f["title"] or "", f["description"] or "", f.get("body") or "")
        if not c:
            continue
        key, sev, label = c
        fp = f"filing:{f['id']}"
        if fp in seen_fps:
            continue
        if any(k == key and abs((d - rd).days) <= MERGE_WINDOW_DAYS for k, rd in raised):
            continue
        detail = f"{f['title']}: {(f['description'] or '')[:600]}"
        if _insert_signal(conn, cp["id"], key, sev, label, detail, "Exchange filing", f.get("pdf_link") or "", d, fp):
            new.append((sev, label))
            raised.append((key, d))
            seen_fps.add(fp)
    today = date.today()
    if latest_any and (today - latest_any).days < 60 and latest_result and (today - latest_result).days > 150:
        if _insert_signal(conn, cp["id"], "results_stale", "medium",
                          f"No financial results filed since {latest_result.strftime('%d %b %Y')}",
                          "The company is filing other disclosures but no results in over 150 days.",
                          "Exchange filings", "", today, f"results_stale:{latest_result.isoformat()}"):
            new.append(("medium", "Results overdue"))
    if latest_result:
        # Results filed since an earlier "no results" alert: close it, or it
        # sits in the queue long after the company caught up.
        for r in conn.execute("SELECT id, fingerprint FROM risk_signals WHERE counterparty_id = ? AND signal_key = 'results_stale' "
                              "AND status IN ('open', 'acknowledged')", (cp["id"],)).fetchall():
            if r["fingerprint"].split(":", 1)[-1] < latest_result.isoformat():
                conn.execute("UPDATE risk_signals SET status = 'resolved', updated_at = ? WHERE id = ?", (_now(), r["id"]))
                conn.execute("INSERT INTO risk_signal_log (signal_id, user_id, action, note, at) VALUES (?,?,?,?,?)",
                             (r["id"], cp["user_id"], "resolved",
                              f"Results filed on {latest_result.strftime('%d %b %Y')}; closed by the scanner.", _now()))
    return new


_SEBI_RESPONDENT = re.compile(r"\b(?:in respect of|against|with respect to|in the case of)\s+(?:m/s\.?\s+)?(.+?)"
                              r"(?=\s+in the matter of|\s+in matter of|\s*\[|\s*\(formerly|$)", re.I)
_SEBI_MATTER = re.compile(r"\bin (?:the )?matter of\s+(?:m/s\.?\s+)?(.+?)(?=\s*\[|\s*<|\s*\(formerly|$)", re.I)
_TRADING_MATTER = re.compile(r"insider trading|trading in (the )?shares of|circulation of upsi|upsi|whatsapp|"
                             r"front[- ]running|non[- ]redressal|scores", re.I)


def _match_part(cpn, part):
    part = re.sub(r"<[^>]+>", " ", part or "")
    # "ITC Ltd. and Rajendra Kumar Singhi" names two respondents.
    best = None
    for piece in re.split(r"\s+and\s+|,\s*", part):
        m = name_match(cpn, norm_name(piece))
        if m == "exact":
            return "exact"
        best = best or m
    return best or name_match(cpn, norm_name(part))


def sebi_match(cp_name, title):
    """How a SEBI order title relates to a counterparty.

    Returns (severity, label) or None. An order *against* the company is
    high; an order in a matter *about* the company (its promoters, or people
    trading its shares) is medium, or low for insider-trading cases where the
    company is only the scrip; anything else is ignored."""
    cpn = norm_name(cp_name)
    if not cpn:
        return None
    t = re.sub(r"<[^>]+>", " ", title or "")
    resp = _SEBI_RESPONDENT.search(t)
    matter = _SEBI_MATTER.search(t)
    r_match = _match_part(cpn, resp.group(1)) if resp else None
    m_match = _match_part(cpn, matter.group(1)) if matter else None
    if r_match:
        label = "SEBI order against this company"
        if r_match == "partial":
            return ("medium", label + " (name match, verify)")
        return ("high", label)
    if m_match:
        if _TRADING_MATTER.search(t):
            return ("low", "SEBI order on trading in this company's shares")
        label = "SEBI order in a matter concerning this company"
        return ("medium", label + (" (name match, verify)" if m_match == "partial" else ""))
    if not resp and not matter:
        whole = name_match(cpn, norm_name(t))
        if whole:
            return ("medium", "SEBI item naming this company (verify)")
    return None


def _collect_sebi(conn, cp, since):
    from reg_news import enforcement_items
    new = []
    try:
        items = enforcement_items(since_days=LOOKBACK_DAYS)
    except Exception:
        items = []
    for it in items:
        m = sebi_match(cp["name"], it["title"])
        if not m:
            continue
        sev, label = m
        if it["kind"] == "recovery":
            label = label.replace("SEBI order", "SEBI recovery proceeding")
        if _insert_signal(conn, cp["id"], "sebi_order", sev, label, it["title"], "SEBI", it["url"],
                          it["published_at"][:10], f"sebi:{it['url']}"):
            new.append((sev, label))
    if cp["symbol"]:
        try:
            rows = conn.execute(
                "SELECT * FROM sebi_enforcement_actions WHERE symbol = ? AND confidence IN ('high', 'medium')",
                (cp["symbol"],)).fetchall()
        except Exception:
            rows = []
        for r in rows:
            d = dict(r)
            od = (d.get("order_date") or "")[:10]
            if not od or od < since.isoformat():
                continue
            m = sebi_match(cp["name"], d.get("title", ""))
            if not m:
                continue
            sev, label = m
            if _insert_signal(conn, cp["id"], "sebi_order", sev, label, re.sub(r"<[^>]+>", " ", d.get("title", "")),
                              "SEBI", d.get("url", ""), od, f"sebi:{d.get('url', '')}"):
                new.append((sev, label))
    return new


def _collect_ibbi(conn, cp, since):
    new = []
    cpn = norm_name(cp["name"])
    if not cpn:
        return new
    first = cpn.split()[0]
    rows = conn.execute("SELECT * FROM ibbi_announcements WHERE norm_name LIKE ? AND announce_date >= ?",
                        (f"%{first}%", since.isoformat())).fetchall()
    for r in rows:
        a = dict(r)
        m = name_match(cpn, a["norm_name"])
        if not m:
            continue
        liquidation = "liquidation" in (a["pa_type"] or "").lower()
        label = ("IBBI: liquidation announced" if liquidation else "IBBI: insolvency (CIRP) announced")
        sev = "critical" if m == "exact" else "high"
        if m == "partial":
            label += " (name match, verify)"
        detail = f"{a['pa_type']} · Corporate debtor: {a['debtor_name']} · Applicant: {a['applicant']} · IP: {a['ip_name']}"
        if _insert_signal(conn, cp["id"], "ibbi", sev, label, detail, "IBBI", a["pdf_url"], a["announce_date"],
                          f"ibbi:{a['norm_name']}:{a['announce_date']}:{(a['pa_type'] or '')[:40]}"):
            new.append((sev, label))
    return new


def _collect_price(conn, cp):
    if not cp["symbol"]:
        return []
    try:
        from stock_prices import fetch_stock_quote
        q = fetch_stock_quote(cp["symbol"]) or {}
    except Exception as e:
        print(f"[RISK] quote failed for {cp['symbol']}: {e}")
        return []
    pc = q.get("pChange")
    try:
        pc = float(pc)
    except (TypeError, ValueError):
        return []
    if pc > -5:
        return []
    sev = "high" if pc <= -10 else "medium"
    label = f"Share price fell {abs(pc):.1f}% in a day"
    today = date.today()
    if _insert_signal(conn, cp["id"], "price_drop", sev, label, f"Last price ₹{q.get('lastPrice')}",
                      "Market data", "", today, f"price:{today.isoformat()}"):
        return [(sev, label)]
    return []


def _sync_board(conn, cp):
    """Pull the latest board (with DINs) for a listed counterparty from the
    governance snapshots, replacing earlier governance-sourced rows."""
    if not cp["symbol"]:
        return
    try:
        row = conn.execute(
            "SELECT board_json FROM governance_snapshots WHERE symbol = ? ORDER BY quarter_date DESC LIMIT 1",
            (cp["symbol"],)).fetchone()
    except Exception:
        return
    if not row:
        return
    import json
    try:
        board = json.loads(row["board_json"] or "[]")
    except ValueError:
        return
    conn.execute("DELETE FROM cp_people WHERE counterparty_id = ? AND source = 'governance'", (cp["id"],))
    now = _now()
    for d in board:
        if d.get("ceased") or not d.get("name"):
            continue
        conn.execute(
            "INSERT INTO cp_people (counterparty_id, name, din, designation, source, added_at) VALUES (?,?,?,?, 'governance', ?)",
            (cp["id"], d.get("name", "")[:160], _norm_din(d.get("din")), (d.get("category") or "")[:120], now))


def scan_counterparty(cid, live=False):
    """Collect signals for one counterparty, then record today's score.
    `live` adds network calls (quote, IBBI name search, board backfill)."""
    cp = _get(cid)
    if not cp:
        return None
    since = date.today() - timedelta(days=LOOKBACK_DAYS)
    if live:
        ibbi_search(cp["name"])
        if cp["symbol"]:
            try:
                import governance_snapshots as govsnap
                if not govsnap.list_snapshots(cp["symbol"]):
                    govsnap.backfill(cp["symbol"], max_reports=1)
            except Exception as e:
                print(f"[RISK] board backfill failed for {cp['symbol']}: {e}")
    conn = get_connection()
    try:
        new = []
        new += _collect_filings(conn, cp, since)
        new += _collect_sebi(conn, cp, since)
        new += _collect_ibbi(conn, cp, since)
        if live:
            new += _collect_price(conn, cp)
        _sync_board(conn, cp)
        conn.execute("UPDATE counterparties SET last_scanned_at = ? WHERE id = ?", (_now(), cid))
        conn.commit()
    finally:
        conn.close()
    sc = score_counterparty(cid)
    _record_score(cid, sc)
    if new:
        _notify(cp, new)
    return {"new": len(new), "score": sc}


def scan_user(user_id, live=False):
    total = 0
    for cp in list_counterparties(user_id):
        r = scan_counterparty(cp["id"], live=live)
        total += (r or {}).get("new", 0)
    return total


def scan_pending(user_id, limit=100):
    """Offline scan of counterparties that have never been scanned (added
    at onboarding or while outbound calls were off). Local data only, about
    25 ms a name, so the portfolio page can call it on load."""
    conn = get_connection()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM counterparties WHERE user_id = ? AND archived = 0 AND (last_scanned_at = '' OR last_scanned_at IS NULL) "
        "ORDER BY id LIMIT ?", (user_id, limit)).fetchall()]
    conn.close()
    for cid in ids:
        try:
            scan_counterparty(cid, live=False)
        except Exception as e:
            print(f"[RISK] pending scan {cid} failed: {e}")
    return len(ids)


_live_scans = set()
_live_lock = threading.Lock()


def scan_user_async(user_id):
    """Live scan of every counterparty in a background thread (regulator
    feed, IBBI, quotes). Returns False if one is already running for this
    user or outbound calls are frozen."""
    if _frozen():
        return False
    with _live_lock:
        if user_id in _live_scans:
            return False
        _live_scans.add(user_id)

    def _run():
        try:
            try:
                import reg_news
                reg_news.refresh()
            except Exception as e:
                print(f"[RISK] news refresh failed: {e}")
            n = scan_user(user_id, live=True)
            print(f"[RISK] live scan for {user_id}: {n} new signals")
        except Exception as e:
            print(f"[RISK] live scan for {user_id} failed: {e}")
        finally:
            with _live_lock:
                _live_scans.discard(user_id)

    threading.Thread(target=_run, name=f"risk-live-{user_id}", daemon=True).start()
    return True


def live_scan_running(user_id):
    with _live_lock:
        return user_id in _live_scans


def _notify(cp, new):
    serious = [n for n in new if n[0] in ("critical", "high")]
    if not serious or _frozen():
        return
    try:
        from database import get_user_telegram
        from price_alerts import send_telegram
        tg = get_user_telegram(cp["user_id"])
        if tg and tg.get("chat_id"):
            lines = "\n".join(f"• {label}" for _, label in serious[:5])
            send_telegram(tg["chat_id"], f"Delta risk alert — {cp['name']}\n{lines}")
    except Exception as e:
        print(f"[RISK] notify failed: {e}")


# ── IBBI ────────────────────────────────────────────────────────────────────

IBBI_URL = "https://ibbi.gov.in/public-announcement"


def _parse_ibbi(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    out = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        cells = [td.get_text(" ", strip=True) for td in tds]
        link = tds[6].find("a", href=True) if len(tds) > 6 else None
        try:
            ad = datetime.strptime(cells[1], "%d-%m-%Y").date().isoformat()
        except ValueError:
            continue
        out.append({"pa_type": cells[0][:200], "announce_date": ad, "last_submission": cells[2][:20],
                    "debtor_name": cells[3][:300], "applicant": cells[4][:300], "ip_name": cells[5][:200],
                    "pdf_url": (link["href"] if link else "").replace("//uploads", "/uploads")[:600]})
    return out


def _store_ibbi(rows):
    if not rows:
        return 0
    now = _now()
    conn = get_connection()
    n = 0
    for r in rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO ibbi_announcements (debtor_name, norm_name, pa_type, announce_date,
                   last_submission, applicant, ip_name, pdf_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (r["debtor_name"], norm_name(r["debtor_name"]), r["pa_type"], r["announce_date"],
             r["last_submission"], r["applicant"], r["ip_name"], r["pdf_url"], now))
        n += cur.rowcount
    conn.commit()
    conn.close()
    return n


def ibbi_refresh_latest(pages=3):
    n = 0
    for p in range(1, pages + 1):
        try:
            r = requests.get(IBBI_URL, params={"page": p}, headers={"User-Agent": _UA}, timeout=25)
            if r.status_code != 200:
                break
            n += _store_ibbi(_parse_ibbi(r.text))
        except Exception as e:
            print(f"[RISK] IBBI page {p} failed: {str(e)[:120]}")
            break
        time.sleep(1)
    return n


def ibbi_search(name):
    """Search IBBI announcements by debtor name (IBBI's own title filter)."""
    q = re.sub(_SUFFIXES, " ", (name or "").lower())
    q = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", q)).strip()
    if len(q) < 3:
        return 0
    try:
        r = requests.get(IBBI_URL, params={"title": q}, headers={"User-Agent": _UA}, timeout=25)
        if r.status_code != 200:
            return 0
        return _store_ibbi(_parse_ibbi(r.text))
    except Exception as e:
        print(f"[RISK] IBBI search failed: {str(e)[:120]}")
        return 0


# ── Scoring ─────────────────────────────────────────────────────────────────

def _decay(event_date, today):
    try:
        age = (today - date.fromisoformat(event_date[:10])).days
    except ValueError:
        return 0
    if age < 0:
        return 0
    if age <= 30:
        return 1.0
    if age <= 90:
        return 0.7
    if age <= 180:
        return 0.45
    if age <= LOOKBACK_DAYS:
        return 0.25
    return 0


def band_for(score):
    for floor, label in BANDS:
        if score >= floor:
            return label
    return "Low"


def _score_rows(rows, today):
    """Score from signal rows as of `today`. Signals dated after `today`
    are ignored, which is what lets us rebuild past scores."""
    groups = {}
    for s in rows:
        w = SEVERITY_WEIGHT.get(s["severity"], 0) * _decay(s["event_date"], today)
        if s["status"] == "resolved":
            w *= 0.5
        if w <= 0:
            continue
        # Hand-logged signals are distinct issues; everything else repeats.
        gk = f"manual:{s['id']}" if s["signal_key"] == "manual" else s["signal_key"]
        groups.setdefault(gk, []).append((w, s))
    contributions = []
    for lst in groups.values():
        lst.sort(key=lambda x: -x[0])
        for i, (w, s) in enumerate(lst):
            contributions.append((w * REPEAT_FACTORS[min(i, len(REPEAT_FACTORS) - 1)], s))
    contributions.sort(key=lambda x: (-x[0], x[1]["event_date"]))
    score = int(round(min(100, sum(w for w, _ in contributions))))
    # An unresolved insolvency admission, payment default or fraud from the
    # last 90 days is Severe on its own, whatever else the name has going on.
    if any(s["severity"] == "critical" and s["status"] in ("open", "acknowledged")
           and _decay(s["event_date"], today) >= 0.7 for _, s in contributions):
        score = max(score, CRITICAL_FLOOR)
    return score, contributions


def score_counterparty(cid, today=None):
    today = today or date.today()
    conn = get_connection()
    rows = conn.execute("SELECT * FROM risk_signals WHERE counterparty_id = ? AND status != 'dismissed'",
                        (cid,)).fetchall()
    conn.close()
    return _score_dict(*_score_rows([dict(r) for r in rows], today))


def _score_dict(score, contributions):
    raw = int(round(min(100, sum(w for w, _ in contributions))))
    return {"score": score, "band": band_for(score), "floored": score > raw, "raw_score": raw,
            "reasons": [{"points": round(w, 1), "title": s["title"], "severity": s["severity"],
                         "event_date": s["event_date"], "id": s["id"], "source": s["source"]}
                        for w, s in contributions[:6]]}


def _record_score(cid, sc):
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO risk_scores (counterparty_id, day, score, band) VALUES (?,?,?,?)",
                 (cid, date.today().isoformat(), sc["score"], sc["band"]))
    conn.commit()
    conn.close()


def score_history(cid, days=90):
    since = (date.today() - timedelta(days=days)).isoformat()
    conn = get_connection()
    rows = conn.execute("SELECT day, score FROM risk_scores WHERE counterparty_id = ? AND day >= ? ORDER BY day",
                        (cid, since)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def rebuilt_history(cid, days=90, step=7):
    """Weekly scores for the last `days`, rebuilt from signal dates. Used
    until daily scans have recorded a history of their own."""
    conn = get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM risk_signals WHERE counterparty_id = ? AND status != 'dismissed'", (cid,)).fetchall()]
    conn.close()
    today = date.today()
    out = []
    for back in range(days, -1, -step):
        d = today - timedelta(days=back)
        out.append({"day": d.isoformat(), "score": _score_rows(rows, d)[0]})
    if out[-1]["day"] != today.isoformat():
        out.append({"day": today.isoformat(), "score": _score_rows(rows, today)[0]})
    return out


def change_30d(cid, score_now):
    """Score change over 30 days: against the recorded score on or before
    that day, else a score rebuilt from the signals known by then."""
    cutoff = date.today() - timedelta(days=30)
    conn = get_connection()
    row = conn.execute("SELECT score FROM risk_scores WHERE counterparty_id = ? AND day <= ? ORDER BY day DESC LIMIT 1",
                       (cid, cutoff.isoformat())).fetchone()
    if row:
        conn.close()
        return score_now - row["score"]
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM risk_signals WHERE counterparty_id = ? AND status != 'dismissed'", (cid,)).fetchall()]
    conn.close()
    return score_now - _score_rows(rows, cutoff)[0]


# ── Alerts ──────────────────────────────────────────────────────────────────

def list_signals(user_id, cid=None, status=None, limit=200, severity=None, source=None, q=None, alerts_only=False):
    sql = """SELECT s.*, c.name AS cp_name, c.exposure_inr, c.category, c.symbol AS cp_symbol FROM risk_signals s
             JOIN counterparties c ON c.id = s.counterparty_id
             WHERE c.user_id = ? AND c.archived = 0"""
    args = [user_id]
    if cid:
        sql += " AND s.counterparty_id = ?"
        args.append(cid)
    if status == "active":
        sql += " AND s.status IN ('open', 'acknowledged')"
    elif status in STATUSES:
        sql += " AND s.status = ?"
        args.append(status)
    if severity in SEVERITY_WEIGHT:
        sql += " AND s.severity = ?"
        args.append(severity)
    elif alerts_only:
        sql += " AND s.severity != 'low'"
    if source in SOURCE_GROUPS:
        vals = SOURCE_GROUPS[source][1]
        sql += f" AND s.source IN ({','.join('?' for _ in vals)})"
        args += list(vals)
    if q:
        like = f"%{q.strip()[:80]}%"
        sql += " AND (s.title LIKE ? OR s.detail LIKE ? OR c.name LIKE ? OR s.assignee LIKE ?)"
        args += [like, like, like, like]
    sql += " ORDER BY s.event_date DESC, s.id DESC LIMIT ?"
    args.append(limit)
    conn = get_connection()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_signal(user_id, signal_id):
    conn = get_connection()
    row = conn.execute(
        """SELECT s.*, c.name AS cp_name FROM risk_signals s JOIN counterparties c ON c.id = s.counterparty_id
           WHERE s.id = ? AND c.user_id = ? AND c.archived = 0""", (signal_id, user_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_manual_signal(user_id, cid, data):
    """Log a signal by hand. Returns the new signal id, or None when the
    counterparty is not the user's."""
    cp = get_counterparty(user_id, cid)
    if not cp:
        return None
    sev = _choice(data.get("severity"), SEVERITY_WEIGHT, "medium")
    title = _s(data.get("title"))[:300]
    if not title:
        raise ValueError("Describe the signal.")
    raw_date = _s(data.get("event_date"))[:10] or date.today().isoformat()
    try:
        d = date.fromisoformat(raw_date)
    except ValueError:
        raise ValueError("Date should be in YYYY-MM-DD format.")
    if d > date.today():
        raise ValueError("The date cannot be in the future.")
    url = _s(data.get("url"))[:600]
    if url and not _URL_RE.match(url):
        raise ValueError("Link should start with http:// or https://.")
    fp = f"manual:{secrets.token_hex(8)}"
    conn = get_connection()
    _insert_signal(conn, cid, "manual", sev, title, _s(data.get("detail"))[:1000], "Logged by risk team",
                   url, d, fp)
    sid = conn.execute("SELECT id FROM risk_signals WHERE counterparty_id = ? AND fingerprint = ?", (cid, fp)).fetchone()["id"]
    conn.execute("INSERT INTO risk_signal_log (signal_id, user_id, action, note, at) VALUES (?,?,?,?,?)",
                 (sid, user_id, "logged", "", _now()))
    conn.commit()
    conn.close()
    _record_score(cid, score_counterparty(cid))
    return sid


ACTIONS = ("acknowledged", "resolved", "dismissed", "open", "assign")


def act_on_signal(user_id, signal_id, action, note="", assignee=None, rescore=True):
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("Unknown action.")
    assignee = _s(assignee)[:120]
    conn = get_connection()
    row = conn.execute(
        """SELECT s.* FROM risk_signals s JOIN counterparties c ON c.id = s.counterparty_id
           WHERE s.id = ? AND c.user_id = ? AND c.archived = 0""", (signal_id, user_id)).fetchone()
    if not row:
        conn.close()
        return False
    if action == "assign":
        conn.execute("UPDATE risk_signals SET assignee = ?, updated_at = ? WHERE id = ?",
                     (assignee, _now(), signal_id))
        logged = f"assigned to {assignee}" if assignee else "unassigned"
    else:
        conn.execute("UPDATE risk_signals SET status = ?, updated_at = ? WHERE id = ?", (action, _now(), signal_id))
        logged = "reopened" if action == "open" else action
    conn.execute("INSERT INTO risk_signal_log (signal_id, user_id, action, note, at) VALUES (?,?,?,?,?)",
                 (signal_id, user_id, logged, _s(note)[:500], _now()))
    conn.commit()
    conn.close()
    if rescore:
        _record_score(row["counterparty_id"], score_counterparty(row["counterparty_id"]))
    return row["counterparty_id"]


def bulk_act(user_id, signal_ids, action, note=""):
    """Apply one status change to many signals. Returns how many changed;
    ids the user does not own are skipped."""
    if not isinstance(action, str) or action not in ("acknowledged", "resolved", "dismissed", "open"):
        raise ValueError("Bulk actions are acknowledge, resolve, dismiss or reopen.")
    touched = set()
    n = 0
    for sid in signal_ids[:500]:
        cid = act_on_signal(user_id, sid, action, note, rescore=False)
        if cid:
            n += 1
            touched.add(cid)
    for cid in touched:
        _record_score(cid, score_counterparty(cid))
    return n


def signal_log(user_id, signal_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT l.* FROM risk_signal_log l JOIN risk_signals s ON s.id = l.signal_id
           JOIN counterparties c ON c.id = s.counterparty_id WHERE l.signal_id = ? AND c.user_id = ?
           ORDER BY l.id""", (signal_id, user_id)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Portfolio views ─────────────────────────────────────────────────────────

def portfolio(user_id):
    """Every active counterparty with score, 30-day change and alert counts,
    plus portfolio totals. Three queries whatever the size of the book."""
    cps = list_counterparties(user_id)
    today = date.today()
    cutoff = today - timedelta(days=30)
    conn = get_connection()
    sig_rows = conn.execute(
        """SELECT s.* FROM risk_signals s JOIN counterparties c ON c.id = s.counterparty_id
           WHERE c.user_id = ? AND c.archived = 0""", (user_id,)).fetchall()
    past = {r["counterparty_id"]: r["score"] for r in conn.execute(
        """SELECT r.counterparty_id, r.score FROM risk_scores r
           JOIN (SELECT counterparty_id, MAX(day) AS d FROM risk_scores WHERE day <= ? GROUP BY counterparty_id) m
             ON m.counterparty_id = r.counterparty_id AND m.d = r.day
           JOIN counterparties c ON c.id = r.counterparty_id WHERE c.user_id = ?""",
        (cutoff.isoformat(), user_id)).fetchall()}
    conn.close()
    by_cp = {}
    for r in sig_rows:
        by_cp.setdefault(r["counterparty_id"], []).append(dict(r))
    rows = []
    active_all = []
    for cp in cps:
        sigs = by_cp.get(cp["id"], [])
        live = [s for s in sigs if s["status"] != "dismissed"]
        sc = _score_dict(*_score_rows(live, today))
        prev = past.get(cp["id"])
        if prev is None:
            prev = _score_rows(live, cutoff)[0]
        active = sorted((s for s in sigs if s["status"] in ("open", "acknowledged")),
                        key=lambda s: (s["event_date"], s["id"]), reverse=True)
        active_all += active
        alerts = [s for s in active if s["severity"] in ALERT_SEVERITIES]
        rows.append({**cp, **sc, "change_30d": sc["score"] - prev,
                     "active_alerts": len(alerts),
                     "active_notes": len(active) - len(alerts),
                     "critical_alerts": sum(1 for s in alerts if s["severity"] == "critical"),
                     "latest_signal": active[0] if active else None})
    rows.sort(key=lambda r: (-r["score"], -(r["exposure_inr"] or 0)))
    total_exposure = sum(r["exposure_inr"] or 0 for r in rows)
    at_risk = sum(r["exposure_inr"] or 0 for r in rows if r["band"] in ("High", "Severe"))
    by_band = {b: 0 for _, b in BANDS}
    exposure_by_band = {b: 0.0 for _, b in BANDS}
    for r in rows:
        by_band[r["band"]] += 1
        exposure_by_band[r["band"]] += r["exposure_inr"] or 0
    by_category = {}
    for r in rows:
        c = by_category.setdefault(r["category"], {"count": 0, "exposure": 0.0, "at_risk": 0.0})
        c["count"] += 1
        c["exposure"] += r["exposure_inr"] or 0
        if r["band"] in ("High", "Severe"):
            c["at_risk"] += r["exposure_inr"] or 0
    active = active_all
    alerts = [s for s in active if s["severity"] in ALERT_SEVERITIES]
    return {
        "rows": rows, "total_exposure": total_exposure, "exposure_at_risk": at_risk,
        "by_band": by_band, "exposure_by_band": exposure_by_band, "by_category": by_category,
        "active_alerts": len(alerts),
        "active_notes": len(active) - len(alerts),
        "alerts_by_severity": {k: sum(1 for s in active if s["severity"] == k) for k in SEVERITY_WEIGHT},
        "count": len(rows),
    }


def _norm_din(din):
    d = "".join(ch for ch in str(din or "") if ch.isdigit())[:8]
    return d.zfill(8) if d else ""


def director_network(user_id):
    """Directors (by DIN) shared between counterparties and the user's own
    entities. Returns nodes, edges and a flat table of overlaps."""
    import entities as ents
    orgs = {}
    din_orgs = {}
    names = {}
    conn = get_connection()
    for cp in list_counterparties(user_id):
        key = f"cp-{cp['id']}"
        orgs[key] = {"id": key, "label": cp["name"], "kind": "counterparty", "band": score_counterparty(cp["id"])["band"],
                     "href": f"/risk/counterparty/{cp['id']}"}
        for p in conn.execute("SELECT * FROM cp_people WHERE counterparty_id = ? AND din != ''", (cp["id"],)).fetchall():
            din = _norm_din(p["din"])
            if not din:
                continue
            din_orgs.setdefault(din, set()).add(key)
            names.setdefault(din, p["name"])
    conn.close()
    try:
        own = ents.all_dins_for_user(user_id)
    except Exception:
        own = {}
    for raw_din, lst in own.items():
        din = _norm_din(raw_din)
        if not din:
            continue
        for rec in lst:
            key = f"en-{rec['entity_id']}"
            orgs.setdefault(key, {"id": key, "label": rec["entity"], "kind": "entity", "band": None,
                                  "href": f"/workspace/entity/{rec['entity_id']}"})
            din_orgs.setdefault(din, set()).add(key)
            names.setdefault(din, rec["person"])
    edges = {}
    overlaps = []
    for din, keys in din_orgs.items():
        if len(keys) < 2:
            continue
        ks = sorted(keys)
        overlaps.append({"din": din, "name": names.get(din, ""), "orgs": [orgs[k] for k in ks]})
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                e = edges.setdefault((ks[i], ks[j]), {"source": ks[i], "target": ks[j], "directors": []})
                e["directors"].append(names.get(din, din))
    linked = {k for e in edges for k in e}
    return {"nodes": [orgs[k] for k in sorted(linked)], "edges": list(edges.values()),
            "overlaps": sorted(overlaps, key=lambda o: (-len(o["orgs"]), o["name"])),
            "org_count": len(orgs), "director_count": len(din_orgs)}


def list_people(cid):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM cp_people WHERE counterparty_id = ? ORDER BY source, name", (cid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_person(user_id, cid, data):
    if not get_counterparty(user_id, cid):
        return None
    name = _s(data.get("name"))[:160]
    if not name:
        raise ValueError("Name is required.")
    raw = _s(data.get("din"))
    din = "".join(ch for ch in raw if ch.isdigit())
    if raw and (len(din) != 8 or len(raw.replace(" ", "")) != 8):
        raise ValueError("DIN should be 8 digits.")
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO cp_people (counterparty_id, name, din, designation, source, added_at) VALUES (?,?,?,?, 'manual', ?)",
        (cid, name, din, _s(data.get("designation"))[:120], _now()))
    conn.commit()
    conn.close()
    return cur.lastrowid


def remove_person(user_id, cid, person_id):
    """Remove a director the user added. Board rows from governance filings
    are refreshed on every scan, so only manual rows can be removed."""
    if not get_counterparty(user_id, cid):
        return False
    conn = get_connection()
    cur = conn.execute("DELETE FROM cp_people WHERE id = ? AND counterparty_id = ? AND source = 'manual'",
                       (person_id, cid))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ── Background loop ─────────────────────────────────────────────────────────

_started = False
_started_lock = threading.Lock()
LEASE_NAME = "risk-scan"


def _cycle():
    import reg_news
    reg_news.refresh()
    ibbi_refresh_latest(pages=3)
    conn = get_connection()
    ids = [r["id"] for r in conn.execute("SELECT id FROM counterparties WHERE archived = 0").fetchall()]
    conn.close()
    for cid in ids:
        try:
            scan_counterparty(cid, live=True)
        except Exception as e:
            print(f"[RISK] scan {cid} failed: {e}")
        time.sleep(2)
    print(f"[RISK] cycle complete ({len(ids)} counterparties)")


def _loop():
    from alert_poller import _try_claim_lease
    import os
    holder = f"pid-{os.getpid()}"
    time.sleep(90)
    while True:
        try:
            if _try_claim_lease(LEASE_NAME, holder):
                _cycle()
        except Exception as e:
            print(f"[RISK] cycle error: {e}")
        time.sleep(SCAN_EVERY_HOURS * 3600)


def start_risk_scanner():
    global _started
    with _started_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="risk-scanner", daemon=True).start()
