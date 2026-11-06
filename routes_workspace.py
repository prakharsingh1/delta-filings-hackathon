"""Entity workspace: the compliance calendar home for a CS or practising CS,
one page per company or LLP, the rulebook catalogue, and the JSON APIs
behind them.

Every read and write goes through entities.py with the signed-in user's id,
so an entity, person or obligation id alone never grants access.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from functools import wraps

from draft_links import draft_url
from flask import Blueprint, Response, abort, jsonify, render_template, request

import entities as ents
import rulebook as rb
from database import get_watchlist

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
    "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    # Union territories
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]

# Characters 7-8 of a CIN are the ROC state code. Used to fill in the state
# when a client list carries only the CIN (professional tax depends on it).
CIN_STATE = {
    "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam", "BR": "Bihar",
    "CT": "Chhattisgarh", "CG": "Chhattisgarh", "GA": "Goa", "GJ": "Gujarat", "HR": "Haryana",
    "HP": "Himachal Pradesh", "JH": "Jharkhand", "KA": "Karnataka", "KL": "Kerala",
    "MP": "Madhya Pradesh", "MH": "Maharashtra", "MN": "Manipur", "ML": "Meghalaya",
    "MZ": "Mizoram", "NL": "Nagaland", "OR": "Odisha", "PB": "Punjab", "RJ": "Rajasthan",
    "SK": "Sikkim", "TN": "Tamil Nadu", "TG": "Telangana", "TS": "Telangana", "TR": "Tripura",
    "UP": "Uttar Pradesh", "UR": "Uttarakhand", "UK": "Uttarakhand", "WB": "West Bengal",
    "AN": "Andaman and Nicobar Islands", "CH": "Chandigarh",
    "DN": "Dadra and Nagar Haveli and Daman and Diu", "DD": "Dadra and Nagar Haveli and Daman and Diu",
    "DL": "Delhi", "JK": "Jammu and Kashmir", "LA": "Ladakh", "LD": "Lakshadweep", "PY": "Puducherry",
}

CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
LLPIN_RE = re.compile(r"^[A-Z]{3}-\d{4}$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")
DIN_RE = re.compile(r"^\d{8}$")
AMOUNT_RE = re.compile(r"^\d+(\.\d+)?$")

# Profile questions, grouped the way a CS thinks about them. Hints say what
# ticking the box adds, and must stay in line with rulebook.RULES.
FLAG_SECTIONS = [
    ("Size and status", ["small_company", "dormant_company", "startup_private", "small_llp", "llp_audit",
                         "sme_listed", "lodr_small", "top100", "top1000"]),
    ("Tax", ["tds_deductor", "tax_audit", "transfer_pricing", "tcs_collector", "sft_reportable"]),
    ("GST", ["gst_registered", "gst_qrmp", "gst_turnover_2cr", "gst_turnover_5cr", "job_work", "gst_lut_exports"]),
    ("People", ["has_employees", "employees_5", "employees_10", "pf_covered", "esi_covered"]),
    ("Cross-border", ["fdi", "odi", "ecb", "opi"]),
    ("Corporate", ["cfs_required", "msme_dues", "csr", "cost_audit", "secretarial_audit",
                   "issue_proceeds_unutilised"]),
]
FLAG_HINTS = {
    "small_company": "Half-yearly board meetings instead of quarterly, and no PAS-6. Paid-up capital up to ₹10 crore and turnover up to ₹100 crore; not a holding or subsidiary company.",
    "dormant_company": "Half-yearly board meetings instead of quarterly.",
    "startup_private": "Half-yearly board meetings instead of quarterly.",
    "small_llp": "Lower late fees on Forms 8 and 11.",
    "llp_audit": "Moves the income tax return to 31 October.",
    "sme_listed": "Removes Reg 47 newspaper ads and Reg 24A; adds the annual SDD certificate.",
    "lodr_small": "Removes Reg 23(9) and Reg 24A; adds the annual SDD certificate.",
    "top100": "Adds the AGM-by-31-August rule (Reg 44(5)).",
    "top1000": "Adds BRSR.",
    "tds_deductor": "Adds monthly TDS deposits, quarterly TDS returns and Form 131 certificates.",
    "tax_audit": "Adds the tax audit report due 30 September.",
    "transfer_pricing": "Adds Form 3CEB and moves the return to 30 November.",
    "tcs_collector": "Adds monthly TCS deposits and quarterly Form 143.",
    "sft_reportable": "Adds the annual SFT statement (Form 165) due 31 May.",
    "gst_registered": "Adds GSTR-1 and GSTR-3B.",
    "gst_qrmp": "Quarterly GSTR-1 and GSTR-3B, with PMT-06 in between. Turnover up to ₹5 crore only.",
    "gst_turnover_2cr": "Adds the GSTR-9 annual return.",
    "gst_turnover_5cr": "Adds the GSTR-9C reconciliation.",
    "job_work": "Adds ITC-04 (half-yearly above ₹5 crore turnover, else annual).",
    "gst_lut_exports": "Adds the yearly LUT renewal due 31 March.",
    "has_employees": "Adds Form 130 when you deduct TDS, and professional tax in Maharashtra and Karnataka.",
    "employees_5": "Adds the Maharashtra labour welfare fund.",
    "employees_10": "Adds the POSH annual report and labour welfare fund in Maharashtra and Karnataka.",
    "pf_covered": "Adds the monthly PF challan and ECR.",
    "esi_covered": "Adds monthly ESI contributions.",
    "fdi": "Adds the FLA return, FC-GPR on allotment and Form DI on downstream investment.",
    "odi": "Adds the FLA return and the ODI annual performance report.",
    "ecb": "Adds the monthly ECB-2 return.",
    "opi": "Adds the half-yearly Form OPI.",
    "cfs_required": "Adds AOC-4 CFS (consolidated statements).",
    "msme_dues": "Adds half-yearly MSME-1.",
    "csr": "Adds the CSR-2 report.",
    "cost_audit": "Adds CRA-2 and CRA-4.",
    "secretarial_audit": "Adds MR-3. Always on for listed companies.",
    "issue_proceeds_unutilised": "Adds the quarterly Reg 32 deviation statement.",
}
# A child question only means something when its parent is ticked.
FLAG_PARENT = {
    "gst_qrmp": "gst_registered", "gst_turnover_2cr": "gst_registered",
    "gst_turnover_5cr": "gst_registered", "job_work": "gst_registered",
    "gst_lut_exports": "gst_registered", "employees_5": "has_employees",
    "employees_10": "has_employees", "pf_covered": "has_employees", "esi_covered": "has_employees",
}
# Questions that only apply to some entity types.
FLAG_TYPES = {
    "small_company": ["private"], "dormant_company": ["private", "public_unlisted"],
    "startup_private": ["private"], "small_llp": ["llp"], "llp_audit": ["llp"],
    "sme_listed": ["listed"], "lodr_small": ["listed"], "top100": ["listed"], "top1000": ["listed"],
    "issue_proceeds_unutilised": ["listed"],
    "cfs_required": ["private", "public_unlisted", "listed", "section8"],
    "msme_dues": rb.COMPANY_TYPES, "csr": rb.COMPANY_TYPES, "cost_audit": rb.COMPANY_TYPES,
    "secretarial_audit": rb.COMPANY_TYPES,
}

TYPE_SHORT = {
    "private": "Private", "public_unlisted": "Public unlisted", "listed": "Listed",
    "opc": "OPC", "section8": "Section 8", "llp": "LLP",
}

FREQUENCIES = [
    ("monthly", "Monthly"), ("quarterly", "Quarterly"), ("half-yearly", "Half-yearly"),
    ("annual", "Annual"), ("event", "On an event"), ("per-director", "Per director"),
]

STATE_LABELS = {"overdue": "Overdue", "due_soon": "Due soon", "upcoming": "Upcoming",
                "filed": "Filed", "na": "Not applicable"}

IMPORT_MAX = 300


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


TYPE_ALIASES = {
    "private": "private", "pvt": "private", "pvt ltd": "private", "private limited": "private",
    "private ltd": "private", "private company": "private", "private limited company": "private",
    "ptc": "private",
    "public": "public_unlisted", "public unlisted": "public_unlisted", "unlisted": "public_unlisted",
    "unlisted public": "public_unlisted", "public limited": "public_unlisted",
    "public ltd": "public_unlisted", "public company": "public_unlisted",
    "public limited company": "public_unlisted", "plc": "public_unlisted",
    "listed": "listed", "listed company": "listed", "listed public": "listed",
    "opc": "opc", "one person": "opc", "one person company": "opc",
    "section8": "section8", "section 8": "section8", "sec 8": "section8", "s8": "section8",
    "section 8 company": "section8", "npl": "section8", "not for profit": "section8",
    "llp": "llp", "limited liability partnership": "llp",
}
for _k, _label in rb.ENTITY_TYPES.items():
    TYPE_ALIASES[_norm(_k)] = _k
    TYPE_ALIASES[_norm(_label)] = _k


# ── Parsing and validation ──────────────────────────────────────────────────

def _s(v):
    """A JSON value as a stripped string; anything that isn't text or a number is blank."""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        return ""
    return str(v).strip()


def _date(v):
    try:
        return date.fromisoformat(_s(v)[:10])
    except ValueError:
        return None


def _type_from_cin(cin):
    if LLPIN_RE.match(cin):
        return "llp"
    if not CIN_RE.match(cin):
        return None
    if cin[0] == "L":
        return "listed"
    return {"PTC": "private", "FTC": "private", "PLC": "public_unlisted",
            "FLC": "public_unlisted", "OPC": "opc", "NPL": "section8"}.get(cin[12:15])


def _type_from_name(name):
    n = _norm(name)
    words = n.split()
    if words and words[-1] == "llp":
        return "llp"
    if "opc" in words:
        return "opc"
    if n.endswith(("private limited", "pvt ltd", "pvt limited", "private ltd")):
        return "private"
    return None


def _state_from_cin(cin):
    return CIN_STATE.get(cin[6:8], "") if CIN_RE.match(cin) else ""


def _check_cin_type(cin, etype):
    if not cin:
        return
    if etype == "llp" and CIN_RE.match(cin):
        raise ValueError("An LLP has an LLPIN, like AAB-1234, not a company CIN.")
    if etype != "llp" and LLPIN_RE.match(cin):
        raise ValueError("That is an LLPIN. Set the type to LLP, or enter the company's CIN.")


def _clean_entity(data, current=None):
    """Validate a create (current=None) or partial update payload into the
    shape entities.create_entity / update_entity expect."""
    partial = current is not None
    out = {}
    today = ents.today_ist()

    if "name" in data or not partial:
        name = _s(data.get("name"))
        if not name:
            raise ValueError("Name is required.")
        if len(name) > 200:
            raise ValueError("Keep the name under 200 characters.")
        out["name"] = name
    if "entity_type" in data or not partial:
        etype = _s(data.get("entity_type"))
        if etype not in rb.ENTITY_TYPES:
            raise ValueError("Choose a valid entity type.")
        out["entity_type"] = etype
    if "cin" in data:
        cin = _s(data["cin"]).upper().replace(" ", "")
        if cin and not (CIN_RE.match(cin) or LLPIN_RE.match(cin)):
            raise ValueError("That CIN doesn't look right. A CIN has 21 characters, like "
                             "U72900MH2015PTC123456. An LLPIN looks like AAB-1234.")
        out["cin"] = cin
    if "pan" in data:
        pan = _s(data["pan"]).upper().replace(" ", "")
        if pan and not PAN_RE.match(pan):
            raise ValueError("PAN should be 10 characters, like AABCA1234B.")
        out["pan"] = pan
    if "gstin" in data:
        gstin = _s(data["gstin"]).upper().replace(" ", "")
        if gstin and not GSTIN_RE.match(gstin):
            raise ValueError("GSTIN should be 15 characters, like 27AABCA1234B1Z5.")
        out["gstin"] = gstin
    if "state" in data:
        state = _s(data["state"])
        if state and state not in INDIAN_STATES:
            raise ValueError("Choose a state or union territory from the list.")
        out["state"] = state
    if "symbol" in data:
        symbol = _s(data["symbol"]).upper()
        if symbol and not SYMBOL_RE.match(symbol):
            raise ValueError("That doesn't look like an NSE symbol.")
        out["symbol"] = symbol
    if "incorporation_date" in data:
        raw = _s(data["incorporation_date"])
        d = _date(raw)
        if raw and not d:
            raise ValueError("Enter the incorporation date as a date.")
        if d and d > today:
            raise ValueError("The incorporation date can't be in the future.")
        out["incorporation_date"] = d.isoformat() if d else ""
    if "tracking_from" in data:
        raw = _s(data["tracking_from"])
        d = _date(raw)
        if raw and not d:
            raise ValueError("Enter 'Track from' as a date.")
        if d:
            out["tracking_from"] = d.isoformat()
        elif partial:
            # An empty start would make the window roll forward daily and
            # silently drop old overdue filings.
            raise ValueError("'Track from' is required.")
    if "share_capital" in data:
        raw = _s(data["share_capital"]).replace(",", "").replace("₹", "").replace(" ", "")
        if raw and not AMOUNT_RE.match(raw):
            raise ValueError("Enter share capital in rupees, as a number.")
        amount = int(float(raw)) if raw else 0
        if amount > 10 ** 14:
            raise ValueError("That share capital looks too large. Enter it in rupees.")
        out["share_capital"] = amount
    if "flags" in data:
        raw = data["flags"]
        if not isinstance(raw, dict):
            raise ValueError("Profile answers must be a set of yes/no values.")
        out["flags"] = {k: True for k, v in raw.items() if k in rb.FLAGS and v is True}

    etype = out.get("entity_type") or (current or {}).get("entity_type")
    cin = out["cin"] if "cin" in out else (current or {}).get("cin", "")
    _check_cin_type(cin, etype)

    if "flags" in out:
        flags = out["flags"]
        for child, parent in FLAG_PARENT.items():
            if not flags.get(parent):
                flags.pop(child, None)
        for key, types in FLAG_TYPES.items():
            if etype not in types:
                flags.pop(key, None)
        if flags.get("gst_turnover_5cr"):
            flags["gst_turnover_2cr"] = True
            if flags.get("gst_qrmp"):
                raise ValueError("QRMP is only open to turnover up to ₹5 crore. Untick one of the two.")
    return out


def _parse_import(lines_of_parts, existing):
    """Rows of [name, type?, cin?] -> preview rows with an error per bad row."""
    have_names = {e["name"].lower() for e in existing}
    have_cins = {e["cin"] for e in existing if e.get("cin")}
    batch_names, batch_cins = set(), set()
    rows = []
    for line_no, parts in lines_of_parts:
        name = (parts[0] if parts else "").strip().strip('"').strip()
        etype, cin, unread = None, "", []
        for p in parts[1:]:
            v = p.strip().strip('"').strip()
            if not v:
                continue
            compact = v.upper().replace(" ", "")
            if CIN_RE.match(compact) or LLPIN_RE.match(compact):
                cin = compact
                continue
            t = TYPE_ALIASES.get(_norm(v))
            if t and not etype:
                etype = t
                continue
            unread.append(v)
        guessed = False
        if not etype and cin:
            etype, guessed = _type_from_cin(cin), True
        if not etype:
            etype, guessed = _type_from_name(name), True

        error = ""
        if not name:
            error = "Name is missing."
        elif len(name) > 200:
            error = "Name is over 200 characters."
        elif unread:
            bad = unread[0][:40]
            error = (f"Check the CIN “{bad}”." if any(ch.isdigit() for ch in bad)
                     else f"Unknown type “{bad}”.")
        elif not etype:
            error = "Add a type: private, public, listed, opc, section8 or llp."
        else:
            try:
                _check_cin_type(cin, etype)
            except ValueError as e:
                error = str(e)
        if not error:
            key = name.lower()
            if key in have_names or (cin and cin in have_cins):
                error = "Already in your workspace."
            elif key in batch_names or (cin and cin in batch_cins):
                error = "Listed twice."
        if not error:
            batch_names.add(name.lower())
            if cin:
                batch_cins.add(cin)
        rows.append({
            "line": line_no, "name": name[:200], "entity_type": etype or "",
            "type_label": rb.ENTITY_TYPES.get(etype, "") if etype else "",
            "type_guessed": bool(etype) and guessed, "cin": cin,
            "state": _state_from_cin(cin), "error": error,
        })
    return rows


def _split_import_text(text):
    out = []
    first = True
    for n, line in enumerate((text or "").splitlines(), 1):
        raw = line.strip()
        if not raw:
            continue
        # csv honours quotes, so "Sharma, Rao & Co LLP", llp stays one name.
        delim = "\t" if "\t" in raw else ","
        try:
            parts = next(csv.reader([raw], delimiter=delim, skipinitialspace=True))
        except csv.Error:
            parts = raw.split(delim)
        # Skip a header row pasted from a spreadsheet.
        if first and _norm(parts[0]) in ("name", "company", "company name", "entity", "entity name", "client"):
            first = False
            continue
        first = False
        out.append((n, parts))
    return out


# ── Presentation helpers ────────────────────────────────────────────────────

def _types_text(types):
    ts = set(types)
    if ts == set(rb.ALL_TYPES):
        return "All companies and LLPs"
    if ts == set(rb.COMPANY_TYPES):
        return "All companies"
    return ", ".join(TYPE_SHORT[t] for t in rb.ALL_TYPES if t in ts)


def _conditions_text(rule):
    parts = []
    if rule["flags"]:
        parts.append(", ".join(rb.FLAGS.get(f, f) for f in rule["flags"]))
    if rule["any_flags"]:
        parts.append(" or ".join(rb.FLAGS.get(f, f) for f in rule["any_flags"]))
    if rule["states"]:
        parts.append("State is " + " or ".join(rule["states"]))
    text = ("Only if: " + "; ".join(parts)) if parts else ""
    if rule["not_flags"]:
        nots = "Not if: " + ", ".join(rb.FLAGS.get(f, f) for f in rule["not_flags"])
        text = f"{text}. {nots}" if text else nots
    return text


def _rule_row(rule):
    row = rb.rule_summary(rule)
    row["types"] = list(rule["types"])
    row["applies_to"] = _types_text(rule["types"])
    row["conditions"] = _conditions_text(rule)
    return row


def _applicable(ent):
    """Rules that apply to this profile, grouped by regime; recurring first."""
    rules = rb.applicable_rules(ent, include_events=True) + [rb.DIR3KYC_RULE]
    groups = []
    for regime in rb.REGIMES:
        rs = [r for r in rules if r["regime"] == regime]
        if rs:
            rs.sort(key=lambda r: r["schedule"]["kind"] == "event")
            groups.append({"regime": regime, "rules": [_rule_row(r) for r in rs]})
    return groups


ENTITY_KEYS = ("id", "name", "entity_type", "type_label", "cin", "pan", "gstin", "state",
               "incorporation_date", "share_capital", "symbol", "flags", "tracking_from")
ITEM_KEYS = ("id", "entity_id", "entity_name", "rule_id", "name", "form", "regime", "law",
             "source", "description", "period", "due_date", "status", "state", "filed_on", "srn",
             "notes", "origin", "event_id", "days_to_due", "penalty", "evidence_url", "evidence_text",
             "suggest_date", "suggest_url", "suggest_text")
# The home page lists every open filing across all clients (a CS firm with
# 100 clients has thousands), so it carries only what that page shows.
OVERVIEW_ITEM_KEYS = ("id", "entity_id", "entity_name", "name", "form", "regime", "period",
                      "due_date", "state", "srn", "notes", "origin", "days_to_due", "penalty")


def _entity_json(e):
    return {k: e.get(k) for k in ENTITY_KEYS}


def _entity_brief(e):
    return {k: e.get(k) for k in ("id", "name", "entity_type", "type_label", "cin", "state", "symbol")}


def _item_json(i):
    return {k: i.get(k) for k in ITEM_KEYS}


def _mask_pan(pan):
    pan = pan or ""
    return (pan[:2] + "*****" + pan[-3:]) if len(pan) == 10 else ""


def _person_json(p, today):
    d = {k: p.get(k) or "" for k in ("id", "name", "din", "designation", "appointed_on", "ceased_on",
                                     "din_allotted_on", "dsc_expiry")}
    d["id"] = p["id"]
    d["pan_masked"] = _mask_pan(p.get("pan"))
    d["next_kyc"] = ""
    if p.get("din") and not p.get("ceased_on"):
        allotted = _date(p.get("din_allotted_on"))
        d["next_kyc"] = rb.dir3kyc_due(allotted, today).isoformat()
    return d


def _overview_json(ov):
    return {
        "today": ov["today"],
        "summary": ov["summary"],
        "entities": [{"entity": _entity_brief(x["entity"]), "summary": x["summary"]} for x in ov["entities"]],
        "items": [{k: i.get(k) for k in OVERVIEW_ITEM_KEYS} for i in ov["items"]],
        "view": ov["view"], "counts": ov["counts"], "total": ov["total"], "regimes": ov["regimes"],
        "next_due": {k: ov["next_due"].get(k) for k in OVERVIEW_ITEM_KEYS} if ov["next_due"] else None,
    }


def _overview_args():
    """The view the home page asked for: ?f=overdue|next15|next90|all, regime, entity."""
    f = request.args.get("f") or None
    try:
        eid = int(request.args.get("entity") or 0) or None
    except ValueError:
        eid = None
    return {"view": f, "regime": (request.args.get("regime") or "")[:40], "entity_id": eid}


def _calendar_json(cal, today):
    ent = cal["entity"]
    # The Draft wizard keys on the NSE symbol when there is one, else the name.
    company = ent.get("symbol") or ent["name"]
    items = []
    for i in cal["items"]:
        row = _item_json(i)
        row["draft_url"] = draft_url(i.get("rule_id"), company, ent["name"])
        items.append(row)
    return {
        "today": today.isoformat(),
        "entity": _entity_brief(ent),
        "summary": cal["summary"],
        "items": items,
    }


def _csv_cell(v):
    # Neutralise spreadsheet formulas in user-typed text (CSV injection).
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _fmt_day(iso):
    d = _date(iso)
    return f"{d.day} {d.strftime('%b %Y')}" if d else ""


# ── Blueprint ───────────────────────────────────────────────────────────────

def create_blueprint(login_required, current_user_id, current_user):
    bp = Blueprint("workspace", __name__)

    # Calendars roll forward as time passes. The entity page re-syncs on every
    # load; the overview re-syncs each entity at most once a day per process.
    synced = {}

    def _sync_stale(uid):
        today = ents.today_ist().isoformat()
        for e in ents.list_entities(uid):
            key = (uid, e["id"])
            if synced.get(key) != today:
                ents.sync_obligations(uid, e["id"])
                synced[key] = today

    def _page(template, active, **ctx):
        return render_template(template, active_page=active,
                               watchlist=get_watchlist(current_user_id()), **ctx)

    def _not_found():
        return jsonify(error="Not found"), 404

    def mutating(f):
        """JSON-only body, and ValueError from validation becomes a 400."""
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not request.is_json:
                return jsonify(error="JSON body required"), 415
            try:
                return f(*args, **kwargs)
            except ValueError as e:
                return jsonify(error=str(e)), 400
        return wrapper

    def _body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    # ── Pages ──────────────────────────────────────────────────────────────

    @bp.route("/workspace")
    @login_required
    def workspace_home():
        uid = current_user_id()
        _sync_stale(uid)
        ov = ents.workspace_overview(uid, **_overview_args())
        return _page("workspace.html", "workspace", data=_overview_json(ov),
                     entity_types=rb.ENTITY_TYPES, states=INDIAN_STATES, regimes=rb.REGIMES)

    @bp.route("/workspace/entity/<int:eid>")
    @login_required
    def entity_page(eid):
        uid = current_user_id()
        ent = ents.get_entity(uid, eid)
        if not ent:
            abort(404)
        ents.sync_obligations(uid, eid)
        today = ents.today_ist()
        synced[(uid, eid)] = today.isoformat()
        cal = ents.entity_calendar(uid, eid)
        data = {
            "entity": _entity_json(ent),
            "calendar": _calendar_json(cal, today),
            "people": [_person_json(p, today) for p in ents.list_people(eid)],
            "events": ents.list_events(uid, eid),
            "applicable": _applicable(ent),
        }
        flag_sections = [
            {"title": title, "flags": [{"key": k, "label": rb.FLAGS[k], "hint": FLAG_HINTS.get(k, ""),
                                         "parent": FLAG_PARENT.get(k, ""),
                                         "types": " ".join(FLAG_TYPES.get(k, rb.ALL_TYPES))}
                                        for k in keys]}
            for title, keys in FLAG_SECTIONS
        ]
        return _page("workspace_entity.html", "workspace", entity=ent, data=data,
                     entity_types=rb.ENTITY_TYPES, states=INDIAN_STATES,
                     event_types=rb.EVENT_TYPES, flag_sections=flag_sections,
                     setup=request.args.get("setup") == "1",
                     tracking_from_label=_fmt_day(ent["tracking_from"]),
                     pan_masked=_mask_pan(ent.get("pan")))

    @bp.route("/workspace/rulebook")
    @login_required
    def rulebook_page():
        rules = [_rule_row(r) for r in rb.RULES + [rb.DIR3KYC_RULE]]
        groups = [{"regime": g, "rules": [r for r in rules if r["regime"] == g]} for g in rb.REGIMES]
        return _page("rulebook.html", "rulebook", groups=[g for g in groups if g["rules"]],
                     rule_count=len(rules), regimes=rb.REGIMES, entity_types=rb.ENTITY_TYPES,
                     frequencies=FREQUENCIES, reviewed=_fmt_day(rb.RULEBOOK_REVIEWED))

    @bp.route("/workspace/entity/<int:eid>/calendar.csv")
    @login_required
    def calendar_csv(eid):
        uid = current_user_id()
        cal = ents.entity_calendar(uid, eid)
        if not cal:
            abort(404)
        ent = cal["entity"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Entity", "Filing", "Form", "Law", "Regime", "Period", "Due date", "Status",
                    "Filed on", "SRN", "Days late", "Estimated penalty (INR)", "Penalty basis",
                    "Notes", "Source"])
        for i in cal["items"]:
            pen = i["penalty"] or {}
            status = STATE_LABELS.get(i["state"], i["state"])
            if i["state"] == "filed" and pen:
                status = "Filed late"
            w.writerow([_csv_cell(v) for v in (
                ent["name"], i["name"], i["form"], i["law"], i["regime"],
                re.sub(r"\s*(·\s*)?#\d+$", "", i["period"]) if i["origin"] != "custom" else "",
                i["due_date"], status, i["filed_on"] or "", i["srn"] or "",
                pen.get("days_late", ""), pen.get("amount") if pen.get("amount") is not None else "",
                pen.get("basis", ""), i["notes"] or "", i["source"] or "")])
        slug = re.sub(r"[^A-Za-z0-9]+", "-", ent["name"]).strip("-")[:60] or "entity"
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{slug}-calendar.csv"',
                                 "Cache-Control": "no-store"})

    # ── Read APIs ──────────────────────────────────────────────────────────

    @bp.route("/api/workspace/overview")
    @login_required
    def api_overview():
        uid = current_user_id()
        _sync_stale(uid)
        return jsonify(_overview_json(ents.workspace_overview(uid, **_overview_args())))

    @bp.route("/api/workspace/entities/<int:eid>/calendar")
    @login_required
    def api_calendar(eid):
        cal = ents.entity_calendar(current_user_id(), eid)
        if not cal:
            return _not_found()
        return jsonify(_calendar_json(cal, ents.today_ist()))

    @bp.route("/api/workspace/entities/<int:eid>/events/preview")
    @login_required
    def api_event_preview(eid):
        ent = ents.get_entity(current_user_id(), eid)
        if not ent:
            return _not_found()
        etype = request.args.get("type", "")
        if etype not in rb.EVENT_TYPES:
            return jsonify(error="Choose an event type."), 400
        d = _date(request.args.get("date"))
        if not d:
            return jsonify(error="Enter the event date."), 400
        items = []
        for ob in rb.event_obligations(etype, d, ent):
            rule = rb.RULES_BY_ID[ob["rule_id"]]
            items.append({"rule_id": rule["id"], "name": rule["name"], "form": rule["form"],
                          "law": rule["law"], "regime": rule["regime"],
                          "due_date": ob["due_date"].isoformat()})
        items.sort(key=lambda x: x["due_date"])
        return jsonify(items=items, event_label=rb.EVENT_TYPES[etype])

    # ── Entities ───────────────────────────────────────────────────────────

    @bp.route("/api/workspace/entities", methods=["POST"])
    @login_required
    @mutating
    def api_create_entity():
        uid = current_user_id()
        data = _clean_entity(_body())
        if data.get("cin") and not data.get("state"):
            data["state"] = _state_from_cin(data["cin"])
        eid = ents.create_entity(uid, data)
        return jsonify(id=eid, url=f"/workspace/entity/{eid}?setup=1"), 201

    @bp.route("/api/workspace/entities/import", methods=["POST"])
    @login_required
    @mutating
    def api_import_entities():
        uid = current_user_id()
        body = _body()
        if isinstance(body.get("rows"), list):
            lines = [(n, [_s(r.get("name")), _s(r.get("entity_type")), _s(r.get("cin"))])
                     for n, r in enumerate(body["rows"], 1) if isinstance(r, dict)]
        else:
            lines = _split_import_text(_s(body.get("text")))
        if not lines:
            raise ValueError("Paste at least one line: name, type, CIN.")
        if len(lines) > IMPORT_MAX:
            raise ValueError(f"Import up to {IMPORT_MAX} at a time.")
        rows = _parse_import(lines, ents.list_entities(uid))
        ready = [r for r in rows if not r["error"]]
        if body.get("dry_run") is True:
            return jsonify(rows=rows, ready=len(ready), skipped=len(rows) - len(ready))
        ids = []
        for r in ready:
            ids.append(ents.create_entity(uid, {"name": r["name"], "entity_type": r["entity_type"],
                                                "cin": r["cin"], "state": r["state"]}))
            synced[(uid, ids[-1])] = ents.today_ist().isoformat()
        return jsonify(rows=rows, created=len(ids), ids=ids, skipped=len(rows) - len(ids)), 201

    @bp.route("/api/workspace/entities/<int:eid>", methods=["PATCH"])
    @login_required
    @mutating
    def api_update_entity(eid):
        uid = current_user_id()
        ent = ents.get_entity(uid, eid)
        if not ent:
            return _not_found()
        data = _clean_entity(_body(), current=ent)
        if not ents.update_entity(uid, eid, data):
            return _not_found()
        ent = ents.get_entity(uid, eid)
        cal = ents.entity_calendar(uid, eid)
        s = cal["summary"]
        return jsonify(ok=True, entity=_entity_json(ent), summary=s,
                       open=s["overdue"] + s["due_soon"] + s["upcoming"],
                       applicable=_applicable(ent))

    @bp.route("/api/workspace/entities/<int:eid>", methods=["DELETE"])
    @login_required
    @mutating
    def api_archive_entity(eid):
        uid = current_user_id()
        if not ents.archive_entity(uid, eid):
            return _not_found()
        synced.pop((uid, eid), None)
        return jsonify(ok=True)

    @bp.route("/api/workspace/entities/<int:eid>/sync", methods=["POST"])
    @login_required
    @mutating
    def api_sync(eid):
        uid = current_user_id()
        if not ents.get_entity(uid, eid):
            return _not_found()
        tracked = ents.sync_obligations(uid, eid)
        synced[(uid, eid)] = ents.today_ist().isoformat()
        cal = ents.entity_calendar(uid, eid)
        return jsonify(ok=True, tracked=tracked, summary=cal["summary"])

    # ── People ─────────────────────────────────────────────────────────────

    @bp.route("/api/workspace/entities/<int:eid>/people", methods=["POST"])
    @login_required
    @mutating
    def api_add_person(eid):
        uid = current_user_id()
        if not ents.get_entity(uid, eid):
            return _not_found()
        body = _body()
        data = {"name": _s(body.get("name")), "designation": _s(body.get("designation"))[:80]}
        if not data["name"]:
            raise ValueError("Name is required.")
        din = re.sub(r"\s", "", _s(body.get("din")))
        if din and not DIN_RE.match(din):
            raise ValueError("A DIN has 8 digits.")
        data["din"] = din
        today = ents.today_ist()
        labels = {"appointed_on": "Appointed on", "ceased_on": "Ceased on",
                  "din_allotted_on": "DIN allotted on", "dsc_expiry": "DSC expiry"}
        for key, label in labels.items():
            raw = _s(body.get(key))
            d = _date(raw)
            if raw and not d:
                raise ValueError(f"Enter '{label}' as a date.")
            if d and key != "dsc_expiry" and d > today:
                raise ValueError(f"'{label}' can't be in the future.")
            data[key] = d.isoformat() if d else ""
        if data["ceased_on"] and data["appointed_on"] and data["ceased_on"] < data["appointed_on"]:
            raise ValueError("'Ceased on' is before 'Appointed on'.")
        pid = ents.add_person(uid, eid, data)
        if pid is None:
            return _not_found()
        return jsonify(id=pid, people=[_person_json(p, today) for p in ents.list_people(eid)]), 201

    @bp.route("/api/workspace/entities/<int:eid>/people/<int:pid>", methods=["DELETE"])
    @login_required
    @mutating
    def api_remove_person(eid, pid):
        uid = current_user_id()
        if not ents.get_entity(uid, eid):
            return _not_found()
        if not any(p["id"] == pid for p in ents.list_people(eid)):
            return _not_found()
        ents.remove_person(uid, eid, pid)
        today = ents.today_ist()
        return jsonify(ok=True, people=[_person_json(p, today) for p in ents.list_people(eid)])

    # ── Events and tasks ───────────────────────────────────────────────────

    @bp.route("/api/workspace/entities/<int:eid>/events", methods=["POST"])
    @login_required
    @mutating
    def api_log_event(eid):
        uid = current_user_id()
        if not ents.get_entity(uid, eid):
            return _not_found()
        body = _body()
        details = _s(body.get("details"))
        if len(details) > 500:
            raise ValueError("Keep the details under 500 characters.")
        res = ents.log_event(uid, eid, _s(body.get("event_type")), _s(body.get("event_date")), details)
        if res is None:
            return _not_found()
        created = [{"rule_id": r, "name": rb.RULES_BY_ID[r]["name"], "form": rb.RULES_BY_ID[r]["form"]}
                   for r in res["created"]]
        return jsonify(event_id=res["event_id"], created=created,
                       events=ents.list_events(uid, eid)), 201

    @bp.route("/api/workspace/entities/<int:eid>/tasks", methods=["POST"])
    @login_required
    @mutating
    def api_add_task(eid):
        uid = current_user_id()
        if not ents.get_entity(uid, eid):
            return _not_found()
        body = _body()
        title = _s(body.get("title"))
        if len(title) > 200:
            raise ValueError("Keep the title under 200 characters.")
        if not title:
            raise ValueError("Give the task a title.")
        if not _date(body.get("due_date")):
            raise ValueError("Enter a due date.")
        oid = ents.add_task(uid, eid, title, _s(body.get("due_date")), _s(body.get("notes")))
        if oid is None:
            return _not_found()
        return jsonify(id=oid), 201

    # ── Obligations ────────────────────────────────────────────────────────

    @bp.route("/api/workspace/entities/<int:eid>/mark-past-filed", methods=["POST"])
    @login_required
    @mutating
    def api_mark_past_filed(eid):
        n = ents.mark_past_filed(current_user_id(), eid)
        if n is None:
            return _not_found()
        return jsonify(ok=True, updated=n)

    @bp.route("/api/workspace/obligations/<int:oid>", methods=["PATCH"])
    @login_required
    @mutating
    def api_update_obligation(oid):
        body = _body()
        payload = {}
        if "status" in body:
            payload["status"] = _s(body["status"])
        if "filed_on" in body:
            payload["filed_on"] = _s(body["filed_on"])
        if "srn" in body:
            payload["srn"] = _s(body["srn"]).upper()
        if "notes" in body:
            payload["notes"] = _s(body["notes"])
        if body.get("accept_suggestion") is True:
            payload["accept_suggestion"] = True
        if not ents.update_obligation(current_user_id(), oid, payload):
            return _not_found()
        return jsonify(ok=True)

    return bp
