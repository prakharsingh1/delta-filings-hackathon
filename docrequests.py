"""Client document requests: one private link per request.

A practising CS lists what a client owes (DIR-2, bank statements, sanction
letter) and sends a link. The client opens it without an account and uploads
against each item. Files are stored on disk under random names; the client's
filename is kept only as metadata and never becomes part of a path.

Owner reads and writes are scoped by user_id. The public side reaches a
request only through its token, and only while the request is open.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from database import DB_PATH, get_connection

# Timestamps and "today" are Indian time: the server runs in UTC, and an
# upload at 10:00 am IST must not show as 4:30 am or land on the wrong date.
IST = timezone(timedelta(hours=5, minutes=30))

ALLOWED_EXTENSIONS = ("pdf", "png", "jpg", "jpeg", "docx", "xlsx", "xls", "csv")
MIME_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
}
# Formats with a fixed file signature. xls and csv are left out on purpose:
# bank and Tally exports named .xls are often HTML or tab-separated text.
# A password-protected docx or xlsx is an OLE container, not a zip, so both
# signatures are accepted for those.
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_SIGNATURES = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "docx": (b"PK\x03\x04", _OLE),
    "xlsx": (b"PK\x03\x04", _OLE),
}
MB = 1024 * 1024
MAX_FILE_BYTES = 15 * MB
MAX_REQUEST_BYTES = 100 * MB
MAX_USER_BYTES = 300 * MB
MAX_ITEMS = 40
STATUSES = ("open", "closed")

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_STORED_RE = re.compile(r"^[0-9a-f]{32}\.[a-z]{3,4}$")

# Checklists a CS actually sends. Notes say what "complete" looks like so the
# client does not send the wrong version.
TEMPLATES = [
    {"key": "annual_filing", "name": "Annual filing (AOC-4 / MGT-7)", "items": [
        {"title": "Audited financial statements", "note": "Balance sheet, profit and loss, cash flow and notes, signed by the auditor and directors"},
        {"title": "Auditor's report with CARO annexure", "note": "Signed copy showing the UDIN"},
        {"title": "Board's report with annexures", "note": "Include AOC-1 and AOC-2 where they apply"},
        {"title": "List of shareholders as on 31 March", "note": "Name, folio, shares held, and any transfers during the year"},
        {"title": "Changes in directors and KMP during the year", "note": "Names, DINs and dates of appointment or cessation"},
        {"title": "Board and general meeting dates", "note": "Every meeting held in the year, with attendance of each director"},
        {"title": "AGM notice and date of AGM", "note": ""},
        {"title": "Related party transactions", "note": "Party, nature of contract, amount and approval date"},
        {"title": "CSR spend details", "note": "Only if section 135 applies: amount spent, projects, unspent account transfers"},
    ]},
    {"key": "director_appointment", "name": "Director appointment", "items": [
        {"title": "Form DIR-2: consent to act as director", "note": "Signed by the appointee"},
        {"title": "Form DIR-8: intimation of non-disqualification", "note": "Signed by the appointee"},
        {"title": "Form MBP-1: disclosure of interest", "note": "Other companies, LLPs and firms the appointee is interested in"},
        {"title": "PAN card of the appointee", "note": "Self-attested copy"},
        {"title": "Address proof of the appointee", "note": "Passport, voter ID, driving licence, or a utility bill not older than two months"},
        {"title": "Passport-size photograph", "note": "Recent, as JPG or PNG"},
        {"title": "DIN allotment letter", "note": "Only if the appointee already holds a DIN"},
        {"title": "Declaration of independence under section 149(7)", "note": "Independent directors only"},
        {"title": "IICA databank registration", "note": "Independent directors only: registration certificate or screenshot"},
    ]},
    {"key": "share_allotment", "name": "Share allotment", "items": [
        {"title": "List of allottees", "note": "Name, address, PAN, number of shares and amount paid by each"},
        {"title": "Bank statement showing application money received", "note": "From the separate bank account, with each allottee's credit visible"},
        {"title": "PAN and address proof of each allottee", "note": ""},
        {"title": "Signed application forms", "note": "With the PAS-4 acknowledgement for a private placement"},
        {"title": "Valuation report from a registered valuer", "note": "Needed for a preferential allotment or private placement"},
        {"title": "Current MOA", "note": "To confirm authorised capital covers the allotment"},
        {"title": "FIRC and KYC of foreign allottees", "note": "Only if any allottee is a non-resident; needed for FC-GPR"},
    ]},
    {"key": "charge_creation", "name": "Charge creation", "items": [
        {"title": "Sanction letter from the lender", "note": "All pages, with the lender's name and address"},
        {"title": "Executed loan agreement or deed of hypothecation", "note": "Signed copy showing the date of execution; CHG-1 is due 30 days from it"},
        {"title": "Mortgage deed and list of title deeds", "note": "Only if immovable property is mortgaged"},
        {"title": "Schedule of assets charged", "note": "Description, location and value of each asset"},
        {"title": "Board resolution authorising the borrowing", "note": "Certified true copy"},
        {"title": "Special resolution under section 180(1)(a) or (c)", "note": "Only if the borrowing crosses the section 180 limits"},
    ]},
    {"key": "gst_monthly", "name": "GST monthly", "items": [
        {"title": "Sales register for the month", "note": "Invoice-wise, with the GSTIN of every B2B buyer"},
        {"title": "Purchase register for the month", "note": "Invoice-wise, with supplier GSTINs"},
        {"title": "Credit and debit notes", "note": "Issued and received during the month"},
        {"title": "Reverse charge expenses", "note": "Legal fees, GTA freight, import of services"},
        {"title": "Export invoices and shipping bills", "note": "Only if there were exports; say whether under LUT or with IGST"},
        {"title": "E-invoice and e-way bill summary", "note": "Only if e-invoicing applies to you"},
        {"title": "Bank statement for the month", "note": "All current accounts"},
    ]},
    {"key": "income_tax_return", "name": "Income tax return", "items": [
        {"title": "Form 16 and Form 16A", "note": "From your employer and anyone who deducted TDS"},
        {"title": "Form 26AS and AIS", "note": "Download both from the income tax portal"},
        {"title": "Bank statements for the full year", "note": "Every account, including any closed during the year"},
        {"title": "Interest certificates", "note": "Savings, fixed and recurring deposits"},
        {"title": "Capital gains statements", "note": "From your broker and from CAMS or KFintech for mutual funds"},
        {"title": "Home loan interest certificate", "note": "Showing principal and interest separately"},
        {"title": "Proof of deductions", "note": "80C, 80D and 80G receipts; needed only under the old regime"},
        {"title": "Rental income details", "note": "Rent received, municipal tax paid, tenant name"},
        {"title": "Last year's ITR acknowledgement", "note": ""},
    ]},
    {"key": "registered_office", "name": "Change of registered office", "items": [
        {"title": "Utility bill for the new premises", "note": "Electricity, water, gas or telephone bill, not older than two months"},
        {"title": "Rent or lease agreement", "note": "Only if the premises are rented"},
        {"title": "No-objection certificate from the owner", "note": "Signed by the owner, allowing use as the registered office"},
        {"title": "Latest rent receipt", "note": "Only if the premises are rented"},
        {"title": "Photographs of the office", "note": "Outside, showing the company name board, and inside"},
    ]},
]
TEMPLATES_BY_KEY = {t["key"]: t for t in TEMPLATES}


def init_docrequests_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS doc_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            entity_id INTEGER,
            title TEXT NOT NULL,
            client_name TEXT NOT NULL,
            client_contact TEXT DEFAULT '',
            requested_by TEXT DEFAULT '',
            items_json TEXT NOT NULL DEFAULT '[]',
            token TEXT NOT NULL UNIQUE,
            due_date TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_docreq_user ON doc_requests(user_id, status);

        CREATE TABLE IF NOT EXISTS doc_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            item_index INTEGER NOT NULL,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_docup_request ON doc_uploads(request_id, item_index);
    """)
    conn.commit()
    conn.close()


def upload_dir():
    """Where files live. Read per call so tests and deploys can point it elsewhere."""
    return os.environ.get("DELTA_UPLOAD_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(DB_PATH)), "uploads")


def _ensure_upload_dir():
    path = upload_dir()
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _now():
    return datetime.now(IST).replace(tzinfo=None).isoformat(timespec="seconds")


def today_ist():
    return datetime.now(IST).date()


def _parse_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _clean_text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _clean_items(raw):
    if not isinstance(raw, list):
        raise ValueError("Add at least one document to the checklist.")
    items = []
    for it in raw:
        if isinstance(it, str):
            it = {"title": it}
        if not isinstance(it, dict):
            continue
        title = _clean_text(it.get("title"), 200)
        if not title:
            continue
        items.append({"title": title, "note": _clean_text(it.get("note"), 300)})
    if not items:
        raise ValueError("Add at least one document to the checklist.")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"A request can list up to {MAX_ITEMS} documents.")
    return items


def _load_items(row):
    try:
        items = json.loads(row["items_json"] or "[]")
    except (TypeError, ValueError):
        items = []
    return [i for i in items if isinstance(i, dict)]


def _state(req, received, total, today):
    """Owner-facing status: closed, complete, overdue or open."""
    if req["status"] == "closed":
        return "closed", "Closed"
    if total and received >= total:
        return "complete", "All received"
    due = _parse_date(req.get("due_date"))
    if due and due < today:
        return "overdue", "Overdue"
    return "open", "Waiting on client"


# ── Owner side ──────────────────────────────────────────────────────────────

def create_request(user_id, data, requested_by=""):
    data = data or {}
    title = _clean_text(data.get("title"), 200)
    client_name = _clean_text(data.get("client_name"), 160)
    if not title:
        raise ValueError("Give the request a title.")
    if not client_name:
        raise ValueError("Enter the client's name.")
    raw_items = data.get("items")
    tpl = data.get("template")
    if not raw_items and isinstance(tpl, str) and tpl in TEMPLATES_BY_KEY:
        raw_items = TEMPLATES_BY_KEY[tpl]["items"]
    items = _clean_items(raw_items)

    entity_id = data.get("entity_id")
    if entity_id in (None, "", 0, "0"):
        entity_id = None
    else:
        import entities as _ents
        try:
            entity_id = int(entity_id)
        except (TypeError, ValueError):
            raise ValueError("Choose one of your entities.")
        if not _ents.get_entity(user_id, entity_id):
            raise ValueError("Choose one of your entities.")

    due_raw = str(data.get("due_date") or "").strip()
    due = _parse_date(due_raw) if due_raw else None
    if due_raw and not due:
        raise ValueError("Enter the due date as YYYY-MM-DD.")

    now = _now()
    token = secrets.token_urlsafe(24)
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO doc_requests (user_id, entity_id, title, client_name, client_contact,
               requested_by, items_json, token, due_date, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?, 'open', ?, ?)""",
        (user_id, entity_id, title, client_name, _clean_text(data.get("client_contact"), 160),
         _clean_text(requested_by, 160), json.dumps(items), token,
         due.isoformat() if due else "", now, now),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return {"id": rid, "token": token}


def list_requests(user_id, today=None):
    today = today or today_ist()
    conn = get_connection()
    rows = conn.execute(
        """SELECT r.*, e.name AS entity_name,
                  (SELECT COUNT(DISTINCT u.item_index) FROM doc_uploads u WHERE u.request_id = r.id) AS received,
                  (SELECT COUNT(*) FROM doc_uploads u WHERE u.request_id = r.id) AS file_count,
                  (SELECT MAX(u.uploaded_at) FROM doc_uploads u WHERE u.request_id = r.id) AS last_upload_at
             FROM doc_requests r
             LEFT JOIN entities e ON e.id = r.entity_id AND e.user_id = r.user_id
            WHERE r.user_id = ?
            ORDER BY (r.status = 'closed'), r.created_at DESC, r.id DESC""",
        (user_id,)).fetchall()
    conn.close()
    out = []
    for row in rows:
        req = dict(row)
        total = len(_load_items(row))
        req.pop("items_json", None)
        req["total"] = total
        req["received"] = min(req["received"] or 0, total)
        req["pct"] = round(100 * req["received"] / total) if total else 0
        req["state"], req["state_label"] = _state(req, req["received"], total, today)
        out.append(req)
    return out


def get_request(user_id, request_id, today=None):
    today = today or today_ist()
    conn = get_connection()
    row = conn.execute(
        """SELECT r.*, e.name AS entity_name, e.archived AS entity_archived FROM doc_requests r
             LEFT JOIN entities e ON e.id = r.entity_id AND e.user_id = r.user_id
            WHERE r.id = ? AND r.user_id = ?""", (request_id, user_id)).fetchone()
    if not row:
        conn.close()
        return None
    uploads = conn.execute(
        """SELECT id, item_index, original_name, size, sha256, uploaded_at FROM doc_uploads
            WHERE request_id = ? ORDER BY uploaded_at, id""", (request_id,)).fetchall()
    conn.close()
    req = dict(row)
    items = _load_items(row)
    req.pop("items_json", None)
    by_item = {}
    for u in uploads:
        by_item.setdefault(u["item_index"], []).append(dict(u))
    req["items"] = [{"index": i, "title": it.get("title", ""), "note": it.get("note", ""),
                     "uploads": by_item.get(i, []), "received": bool(by_item.get(i))}
                    for i, it in enumerate(items)]
    req["total"] = len(items)
    req["received"] = sum(1 for it in req["items"] if it["received"])
    req["file_count"] = len(uploads)
    req["bytes_used"] = sum(u["size"] for u in uploads)
    req["pct"] = round(100 * req["received"] / req["total"]) if req["total"] else 0
    req["state"], req["state_label"] = _state(req, req["received"], req["total"], today)
    return req


def set_status(user_id, request_id, status):
    if status not in STATUSES:
        raise ValueError("Status must be open or closed.")
    conn = get_connection()
    cur = conn.execute("UPDATE doc_requests SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                       (status, _now(), request_id, user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_request(user_id, request_id):
    conn = get_connection()
    # Hold the write lock from reading the file list to the delete, so an
    # upload cannot commit in between and leave a file with no row.
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT id FROM doc_requests WHERE id = ? AND user_id = ?",
                       (request_id, user_id)).fetchone()
    if not row:
        conn.close()
        return False
    names = [r["stored_name"] for r in conn.execute(
        "SELECT stored_name FROM doc_uploads WHERE request_id = ?", (request_id,)).fetchall()]
    conn.execute("DELETE FROM doc_uploads WHERE request_id = ?", (request_id,))
    conn.execute("DELETE FROM doc_requests WHERE id = ? AND user_id = ?", (request_id, user_id))
    conn.commit()
    conn.close()
    base = upload_dir()
    for name in names:
        if _STORED_RE.match(name or ""):
            try:
                os.remove(os.path.join(base, name))
            except OSError:
                pass
    return True


def file_path_for(user_id, upload_id):
    """Owner-only lookup of an uploaded file. None if not theirs or missing on disk."""
    conn = get_connection()
    row = conn.execute(
        """SELECT u.*, r.items_json FROM doc_uploads u JOIN doc_requests r ON r.id = u.request_id
            WHERE u.id = ? AND r.user_id = ?""", (upload_id, user_id)).fetchone()
    conn.close()
    if not row or not _STORED_RE.match(row["stored_name"] or ""):
        return None
    path = os.path.join(upload_dir(), row["stored_name"])
    if not os.path.isfile(path):
        return None
    items = _load_items(row)
    idx = row["item_index"]
    return {"path": path, "request_id": row["request_id"], "item_index": idx,
            "item_title": items[idx]["title"] if 0 <= idx < len(items) else "",
            "original_name": row["original_name"], "size": row["size"],
            "ext": row["stored_name"].rsplit(".", 1)[1],
            "mimetype": MIME_TYPES.get(row["stored_name"].rsplit(".", 1)[1], "application/octet-stream")}


def storage_used(user_id):
    conn = get_connection()
    row = conn.execute(
        """SELECT COALESCE(SUM(u.size), 0) AS n FROM doc_uploads u
             JOIN doc_requests r ON r.id = u.request_id WHERE r.user_id = ?""", (user_id,)).fetchone()
    conn.close()
    return row["n"]


# ── Client side (token) ─────────────────────────────────────────────────────

def valid_token(token):
    return bool(_TOKEN_RE.match(token or ""))


def get_by_token(token):
    """Public view of an open request: no file names, no owner id."""
    if not valid_token(token):
        return None
    conn = get_connection()
    row = conn.execute("SELECT * FROM doc_requests WHERE token = ? AND status = 'open'", (token,)).fetchone()
    if not row:
        conn.close()
        return None
    got = {r["item_index"] for r in conn.execute(
        "SELECT DISTINCT item_index FROM doc_uploads WHERE request_id = ?", (row["id"],)).fetchall()}
    conn.close()
    items = _load_items(row)
    return {
        "title": row["title"], "client_name": row["client_name"],
        "requested_by": row["requested_by"] or "", "due_date": row["due_date"] or "",
        "items": [{"index": i, "title": it.get("title", ""), "note": it.get("note", ""),
                   "received": i in got} for i, it in enumerate(items)],
    }


def _client_filename(name):
    """Basename only, printable, bounded. Metadata only; never used in a path."""
    name = re.split(r"[\\/]", str(name or ""))[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:200]


def _stream_size(stream):
    try:
        pos = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(pos)
        return size
    except (AttributeError, OSError, ValueError):
        return None


def _usage(conn, request_id, user_id):
    per_request = conn.execute("SELECT COALESCE(SUM(size), 0) FROM doc_uploads WHERE request_id = ?",
                               (request_id,)).fetchone()[0]
    per_user = conn.execute(
        """SELECT COALESCE(SUM(u.size), 0) FROM doc_uploads u JOIN doc_requests r ON r.id = u.request_id
            WHERE r.user_id = ?""", (user_id,)).fetchone()[0]
    return per_request, per_user


def _check_room(per_request, per_user, size):
    if per_request + size > MAX_REQUEST_BYTES:
        raise ValueError("This request has reached its 100 MB upload limit. "
                         "Contact the person who sent you the link.")
    if per_user + size > MAX_USER_BYTES:
        raise ValueError("Uploads are paused because the requester's storage is full. "
                         "Let them know so they can clear space.")


def save_upload(token, item_index, filestorage):
    """Store one client file. None if the link is unknown or closed; ValueError
    with a message the client can act on for anything else."""
    if not valid_token(token):
        return None
    conn = get_connection()
    req = conn.execute("SELECT id, user_id, items_json FROM doc_requests WHERE token = ? AND status = 'open'",
                       (token,)).fetchone()
    if not req:
        conn.close()
        return None
    items = _load_items(req)
    try:
        item_index = int(item_index)
    except (TypeError, ValueError):
        conn.close()
        raise ValueError("Choose which document this file is for.")
    if not 0 <= item_index < len(items):
        conn.close()
        raise ValueError("Choose which document this file is for.")
    if filestorage is None or not getattr(filestorage, "filename", ""):
        conn.close()
        raise ValueError("Choose a file to upload.")
    original = _client_filename(filestorage.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_EXTENSIONS:
        conn.close()
        raise ValueError("Upload a PDF, Word, Excel, CSV, JPG or PNG file.")

    size = _stream_size(filestorage.stream)
    if size is not None:
        if size == 0:
            conn.close()
            raise ValueError("This file is empty.")
        if size > MAX_FILE_BYTES:
            conn.close()
            raise ValueError("This file is over 15 MB. Compress it or split it and try again.")
    try:
        _check_room(*_usage(conn, req["id"], req["user_id"]), size or 0)
    finally:
        conn.close()

    head = filestorage.stream.read(1024)
    sigs = _SIGNATURES.get(ext)
    if ext == "pdf" and b"%PDF" not in head:
        raise ValueError("This file does not look like a PDF. Check it opens, then try again.")
    if sigs and not head.startswith(sigs):
        raise ValueError(f"This file does not look like a real .{ext} file. Check it opens, then try again.")

    base = _ensure_upload_dir()
    stored_name = f"{secrets.token_hex(16)}.{ext}"
    final_path = os.path.join(base, stored_name)
    tmp_path = final_path + ".part"
    digest = hashlib.sha256()
    written = 0
    try:
        with open(tmp_path, "xb") as out:
            chunk = head
            while chunk:
                written += len(chunk)
                if written > MAX_FILE_BYTES:
                    raise ValueError("This file is over 15 MB. Compress it or split it and try again.")
                digest.update(chunk)
                out.write(chunk)
                chunk = filestorage.stream.read(256 * 1024)
        if written == 0:
            raise ValueError("This file is empty.")

        # Re-check under a write lock so two parallel uploads cannot both slip
        # under the limit, and so a request closed mid-upload takes nothing.
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            still_open = conn.execute("SELECT 1 FROM doc_requests WHERE id = ? AND status = 'open'",
                                      (req["id"],)).fetchone()
            if not still_open:
                conn.rollback()
                return None
            _check_room(*_usage(conn, req["id"], req["user_id"]), written)
            os.replace(tmp_path, final_path)
            cur = conn.execute(
                """INSERT INTO doc_uploads (request_id, item_index, original_name, stored_name, size,
                       sha256, uploaded_at) VALUES (?,?,?,?,?,?,?)""",
                (req["id"], item_index, original or f"document.{ext}", stored_name, written,
                 digest.hexdigest(), _now()))
            conn.execute("UPDATE doc_requests SET updated_at = ? WHERE id = ?", (_now(), req["id"]))
            conn.commit()
            upload_id = cur.lastrowid
        except BaseException:
            conn.rollback()
            if os.path.exists(final_path):
                os.remove(final_path)
            raise
        finally:
            conn.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return {"id": upload_id, "item_index": item_index, "size": written}
