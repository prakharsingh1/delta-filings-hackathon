"""Document requests: owner pages, the client's public upload page, and JSON APIs.

Owner routes sit behind login_required and are scoped to current_user_id().
The public routes (/r/<token>...) need no account; the token is the only key,
and it stops working when the owner closes the request.
"""
from __future__ import annotations

import collections
import re
import tempfile
import threading
import time
import unicodedata
import zipfile
from datetime import date, datetime
from urllib.parse import quote

from flask import Blueprint, abort, jsonify, make_response, render_template, request, send_file, url_for
from werkzeug.exceptions import RequestEntityTooLarge

import docrequests as dr
import entities as ents
from database import get_watchlist

PUBLIC_MAX_BODY = 16 * 1024 * 1024
RATE_WINDOW_SECONDS = 600
RATE_MAX_UPLOADS = 30

_rate_hits: dict[str, collections.deque] = {}
_rate_lock = threading.Lock()


def _rate_limited(token):
    """In-memory, per-process limit on upload attempts per link."""
    now = time.monotonic()
    with _rate_lock:
        hits = _rate_hits.setdefault(token, collections.deque())
        while hits and now - hits[0] > RATE_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= RATE_MAX_UPLOADS:
            return True
        hits.append(now)
        if len(_rate_hits) > 5000:
            for key in [k for k, q in _rate_hits.items() if not q or now - q[-1] > RATE_WINDOW_SECONDS]:
                _rate_hits.pop(key, None)
    return False


# ── Formatting ──────────────────────────────────────────────────────────────

def fmt_date(value):
    try:
        d = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return ""
    return f"{d.day} {d.strftime('%b %Y')}"


def fmt_dt(value):
    try:
        t = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return ""
    hour = t.hour % 12 or 12
    return f"{t.day} {t.strftime('%b %Y')}, {hour}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def fmt_size(n):
    n = int(n or 0)
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}".removesuffix(".0") + " MB"
    if n >= 1024:
        return f"{round(n / 1024)} KB"
    return f"{n} bytes"


def _due_note(req, today):
    """Short line under the due date: days left, or how late."""
    try:
        due = date.fromisoformat(req.get("due_date") or "")
    except ValueError:
        return ""
    if req.get("state") in ("closed", "complete"):
        return ""
    days = (due - today).days
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} late"
    if days == 0:
        return "Due today"
    return f"{days} day{'s' if days != 1 else ''} left"


def _safe_stem(text, fallback):
    """Client filenames and titles are untrusted: drop path separators and
    control characters, and bound the length."""
    name = unicodedata.normalize("NFKC", text or "")
    name = re.sub(r'[\x00-\x1f\x7f"\\/:*?<>|;]+', "_", name).strip(" ._")
    return name[:120].strip(" ._") or fallback


def _download_name(original, ext, fallback):
    """Sanitised client filename, forced to the extension the file is stored under."""
    original = original or ""
    stem = original.rsplit(".", 1)[0] if "." in original else original
    return f"{_safe_stem(stem, fallback)}.{ext}"


# ── Sharing ─────────────────────────────────────────────────────────────────

def _public_link(token):
    url = url_for("docs.public_page", token=token, _external=True)
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    if proto == "https" and url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


def _whatsapp_number(contact):
    """wa.me wants the full international number without '+'. Indian mobiles
    are often written as 10 digits, so add 91 for those."""
    if not contact or "@" in contact:
        return ""
    digits = re.sub(r"\D", "", contact)
    if len(digits) == 11 and digits[0] == "0" and digits[1] in "6789":
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return digits
    if contact.strip().startswith("+") and 11 <= len(digits) <= 15:
        return digits
    return ""


def _share(req, link):
    """WhatsApp and email links. Once some documents are in, the message asks
    only for what is still pending, so the same button works as a reminder."""
    pending = [it["title"] for it in req["items"] if not it["received"]]
    partial = 0 < req["received"] < req["total"]
    due = fmt_date(req.get("due_date"))
    name = req["client_name"]
    sender = req.get("requested_by") or ""

    if partial:
        n = len(pending)
        lead = (f"{n} document{'s are' if n != 1 else ' is'} still pending for {req['title']}. "
                f"Please upload {'them' if n != 1 else 'it'} here:")
        subject = f"Pending documents: {req['title']}"
    else:
        lead = f"Please upload the documents for {req['title']} using this private link:"
        subject = f"Documents needed: {req['title']}"
    due_line = f"Due by {due}." if due else ""

    wa_text = " ".join(p for p in [f"Hello {name},", lead[0].lower() + lead[1:], link, due_line, "No account is needed.",
                                   f"- {sender}" if sender else ""] if p)
    body_lines = [f"Hello {name},", "", lead, link, ""]
    if pending:
        body_lines.append("Documents needed:")
        body_lines += [f"{i}. {t}" for i, t in enumerate(pending[:25], 1)]
        if len(pending) > 25:
            body_lines.append(f"...and {len(pending) - 25} more, listed on the page.")
        body_lines.append("")
    if due_line:
        body_lines.append(due_line)
    body_lines.append("No account or password is needed. PDF, Word, Excel, CSV, JPG or PNG, "
                      "up to 15 MB per file.")
    if sender:
        body_lines += ["", "Regards,", sender]
    body = "\n".join(body_lines)

    contact = req.get("client_contact") or ""
    email_to = contact if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact) else ""
    return {
        "message": wa_text,
        "whatsapp": f"https://wa.me/{_whatsapp_number(contact)}?text={quote(wa_text, safe='')}",
        "mailto": f"mailto:{quote(email_to, safe='@')}?subject={quote(subject, safe='')}&body={quote(body, safe='')}",
    }


def _no_index(resp, status=None):
    resp = make_response(resp)
    if status is not None:
        resp.status_code = status
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


def create_blueprint(login_required, current_user_id, current_user):
    # main.py is not ours to edit, so the tables are created here, once.
    dr.init_docrequests_db()
    bp = Blueprint("docs", __name__)

    def _requested_by():
        u = current_user() or {}
        name = (u.get("name") or "").strip()
        company = (u.get("company") or "").strip()
        if name and company and company.lower() != name.lower():
            return f"{name}, {company}"
        return name or company

    # ── Owner pages ────────────────────────────────────────────────────────

    @bp.route("/requests")
    @login_required
    def requests_page():
        uid = current_user_id()
        today = dr.today_ist()
        all_reqs = dr.list_requests(uid, today=today)
        show = request.args.get("show", "open")
        if show not in ("open", "closed", "all"):
            show = "open"
        shown = [r for r in all_reqs if show == "all" or (r["status"] == "closed") == (show == "closed")]
        open_reqs = [r for r in all_reqs if r["status"] == "open"]
        counts = {"open": len(open_reqs), "closed": len(all_reqs) - len(open_reqs), "all": len(all_reqs)}
        kpis = {
            "open": len(open_reqs),
            "outstanding": sum(r["total"] - r["received"] for r in open_reqs),
            "overdue": sum(1 for r in open_reqs if r["state"] == "overdue"),
            "complete": sum(1 for r in open_reqs if r["state"] == "complete"),
        }
        for r in shown:
            r["due_note"] = _due_note(r, today)
        return render_template(
            "requests.html", active_page="requests", watchlist=get_watchlist(uid),
            requests=shown, counts=counts, show=show, kpis=kpis,
            entities=ents.list_entities(uid), templates=dr.TEMPLATES,
            storage_used=dr.storage_used(uid), storage_limit=dr.MAX_USER_BYTES,
            fmt_date=fmt_date, fmt_size=fmt_size,
        )

    @bp.route("/requests/<int:rid>")
    @login_required
    def request_detail(rid):
        uid = current_user_id()
        today = dr.today_ist()
        req = dr.get_request(uid, rid, today=today)
        if not req:
            abort(404)
        link = _public_link(req["token"])
        return render_template(
            "request_detail.html", active_page="requests", watchlist=get_watchlist(uid),
            req=req, link=link, share=_share(req, link), due_note=_due_note(req, today),
            fmt_date=fmt_date, fmt_dt=fmt_dt, fmt_size=fmt_size,
        )

    @bp.route("/requests/<int:rid>/files/<int:upload_id>")
    @login_required
    def download_file(rid, upload_id):
        f = dr.file_path_for(current_user_id(), upload_id)
        if not f or f["request_id"] != rid:
            abort(404)
        resp = send_file(f["path"], mimetype=f["mimetype"], as_attachment=True,
                         download_name=_download_name(f["original_name"], f["ext"], f"document-{upload_id}"),
                         max_age=0)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @bp.route("/requests/<int:rid>/files.zip")
    @login_required
    def download_all(rid):
        uid = current_user_id()
        req = dr.get_request(uid, rid)
        if not req or not req["file_count"]:
            abort(404)
        # Bounded by the 100 MB per-request cap; spills to disk past 20 MB.
        buf = tempfile.SpooledTemporaryFile(max_size=20 * 1024 * 1024)
        used = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for it in req["items"]:
                num = f"{it['index'] + 1:02d}"
                folder = _safe_stem(f"{num} {it['title'].replace(':', '')[:60]}", num)
                for u in it["uploads"]:
                    f = dr.file_path_for(uid, u["id"])
                    if not f:
                        continue
                    name = _download_name(f["original_name"], f["ext"], f"document-{u['id']}")
                    arc = f"{folder}/{name}"
                    if arc in used:
                        arc = f"{folder}/{name.rsplit('.', 1)[0]} ({u['id']}).{f['ext']}"
                    used.add(arc)
                    zf.write(f["path"], arc)
        buf.seek(0)
        resp = send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"{_safe_stem(req['title'].replace(':', ''), f'request-{rid}')}.zip",
                         max_age=0)
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    # ── Owner APIs ─────────────────────────────────────────────────────────

    @bp.route("/api/requests/templates")
    @login_required
    def api_templates():
        return jsonify(templates=dr.TEMPLATES)

    @bp.route("/api/requests", methods=["POST"])
    @login_required
    def api_create():
        if not request.is_json:
            return jsonify(error="JSON body required"), 415
        uid = current_user_id()
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Send the request as a JSON object."), 400
        try:
            created = dr.create_request(uid, data, requested_by=_requested_by())
        except ValueError as e:
            return jsonify(error=str(e)), 400
        req = dr.get_request(uid, created["id"])
        link = _public_link(req["token"])
        return jsonify(id=req["id"], url=link, detail_url=f"/requests/{req['id']}",
                       client_name=req["client_name"], share=_share(req, link)), 201

    @bp.route("/api/requests/<int:rid>", methods=["PATCH"])
    @login_required
    def api_update(rid):
        if not request.is_json:
            return jsonify(error="JSON body required"), 415
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Send the request as a JSON object."), 400
        try:
            ok = dr.set_status(current_user_id(), rid, data.get("status"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not ok:
            return jsonify(error="Request not found"), 404
        return jsonify(ok=True, status=data.get("status"))

    @bp.route("/api/requests/<int:rid>", methods=["DELETE"])
    @login_required
    def api_delete(rid):
        if not request.is_json:
            return jsonify(error="JSON body required"), 415
        if not dr.delete_request(current_user_id(), rid):
            return jsonify(error="Request not found"), 404
        return jsonify(ok=True)

    # ── Public (no account) ────────────────────────────────────────────────

    @bp.route("/r/<token>")
    def public_page(token):
        req = dr.get_by_token(token)
        if not req:
            return _no_index(render_template("request_public.html", req=None), 404)
        received = sum(1 for it in req["items"] if it["received"])
        return _no_index(render_template(
            "request_public.html", req=req, received=received, total=len(req["items"]),
            due=fmt_date(req["due_date"]), upload_url=url_for("docs.public_upload", token=token),
            max_mb=dr.MAX_FILE_BYTES // (1024 * 1024), extensions=dr.ALLOWED_EXTENSIONS,
        ))

    @bp.route("/r/<token>/upload", methods=["POST"])
    def public_upload(token):
        too_big = "This file is over 15 MB. Compress it or split it and try again."
        if request.content_length is not None and request.content_length > PUBLIC_MAX_BODY:
            return _no_index(jsonify(error=too_big), 413)
        try:
            # Also caps chunked bodies that send no Content-Length (Flask 3.1+).
            request.max_content_length = PUBLIC_MAX_BODY
        except AttributeError:
            pass
        if not dr.get_by_token(token):
            return _no_index(jsonify(error="This link is no longer active."), 404)
        if _rate_limited(token):
            return _no_index(jsonify(error="Too many uploads in a short time. Wait a few minutes and try again."), 429)
        try:
            item_index = request.form.get("item_index")
            upload = request.files.get("file")
        except RequestEntityTooLarge:
            return _no_index(jsonify(error=too_big), 413)
        try:
            saved = dr.save_upload(token, item_index, upload)
        except ValueError as e:
            return _no_index(jsonify(error=str(e)), 400)
        if not saved:
            return _no_index(jsonify(error="This link is no longer active."), 404)
        return _no_index(jsonify(ok=True, item_index=saved["item_index"], received=True), 201)

    return bp
