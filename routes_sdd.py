"""Insider-trading desk (SEBI PIT Regulations, 2015).

One desk per entity: the structured digital database of UPSI sharing
(Reg 3(5)/(6)), designated persons and immediate relatives, trading window
closures and pre-clearance of trades. The rules and the hash chain live in
sdd.py; this module is pages, JSON APIs and the CSV export.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from functools import wraps

from flask import Blueprint, Response, abort, jsonify, render_template, request

import entities as ents
import sdd
from database import get_watchlist

SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def _fmt_dt(value):
    """'29 Sep 2026, 10:30'."""
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return value or ""
    return f"{dt.day} {dt.strftime('%b %Y')}, {dt.strftime('%H:%M')}"


def _num(n):
    """Indian digit grouping: 12,34,567."""
    whole = str(abs(int(n or 0)))
    if len(whole) > 3:
        head = ",".join(re.findall(r"\d{1,2}", whole[:-3][::-1]))[::-1]
        whole = f"{head},{whole[-3:]}"
    return ("-" if int(n or 0) < 0 else "") + whole


def _inr(n):
    """Same output as dfInr() in app_base, for server-rendered cells."""
    n = float(n or 0)
    if n >= 1e7:
        return f"₹{n / 1e7:.{0 if n >= 1e9 else 2}f} cr"
    if n >= 1e5:
        return f"₹{n / 1e5:.2f} L"
    return f"₹{_num(round(n))}"


def _csv_cell(value):
    """Stop a spreadsheet from running a cell as a formula."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _chain_label(chain):
    if not chain:
        return "", "chip-low"
    if chain["ok"]:
        n = chain["entries"]
        return f"Chain intact · {n} {'entry' if n == 1 else 'entries'}", "chip-ok"
    if chain.get("broken_at"):
        return f"Tampering detected at entry #{chain['broken_at']}", "chip-critical"
    return "Tampering detected", "chip-critical"


def create_blueprint(login_required, current_user_id, current_user):
    bp = Blueprint("sdd", __name__)

    @bp.url_value_preprocessor
    def _bounded_ids(endpoint, values):
        # An id longer than SQLite's 64-bit integer cannot exist; without this
        # the query raises OverflowError and the page is a 500.
        if values and any(isinstance(v, int) and v > sdd.MAX_INT for v in values.values()):
            abort(404)

    def json_api(f):
        """JSON-only mutations; backend ValueError becomes a 400 with its text."""
        @wraps(f)
        def wrapper(*args, **kwargs):
            if request.method in ("POST", "PATCH", "PUT", "DELETE") and not request.is_json:
                return jsonify(error="JSON body required"), 415
            try:
                return f(*args, **kwargs)
            except ValueError as e:
                return jsonify(error=str(e)), 400
        return wrapper

    def body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def not_found():
        return jsonify(error="Not found"), 404

    def page_context(uid, **extra):
        return dict(active_page="sdd", watchlist=get_watchlist(uid), fmt_dt=_fmt_dt,
                    fmt_d=sdd.human_date, inr=_inr, num=_num, chain_label=_chain_label, **extra)

    # ── Pages ────────────────────────────────────────────────────────────

    @bp.route("/sdd")
    @login_required
    def sdd_index():
        uid = current_user_id()
        today = sdd.today_ist()
        rows = [{"entity": e, "summary": sdd.desk_summary(uid, e["id"], today)}
                for e in ents.list_entities(uid)]
        rows.sort(key=lambda r: (r["entity"]["entity_type"] != "listed", r["entity"]["name"].lower()))
        return render_template("sdd_index.html", **page_context(uid, rows=rows))

    @bp.route("/sdd/<int:eid>")
    @login_required
    def sdd_desk(eid):
        uid = current_user_id()
        ent = ents.get_entity(uid, eid)
        if not ent:
            abort(404)
        today = sdd.today_ist()
        windows = sdd.list_windows(uid, eid, today)
        return render_template(
            "sdd.html",
            **page_context(
                uid, ent=ent, today=today.isoformat(),
                summary=sdd.desk_summary(uid, eid, today),
                upsi=sdd.list_upsi(uid, eid),
                entries=sdd.list_entries(uid, eid),
                persons=sdd.list_persons(uid, eid),
                windows=windows,
                wstatus=sdd.window_status(uid, eid, today),
                suggestion=sdd.suggest_closure(uid, eid, today),
                requests=sdd.list_preclearance(uid, eid, today),
                recipient_types=sdd.RECIPIENT_TYPES,
                txn_types=sdd.TXN_TYPES,
                valid_days=sdd.PRECLEARANCE_VALID_TRADING_DAYS,
            ))

    @bp.route("/sdd/<int:eid>/export.csv")
    @login_required
    def sdd_export(eid):
        uid = current_user_id()
        ent = ents.get_entity(uid, eid)
        if not ent:
            abort(404)
        me = current_user() or {}
        buf = io.StringIO()
        buf.write("﻿")  # lets Excel read names with non-ASCII letters
        w = csv.writer(buf)
        w.writerow(["Entry", "Type of entry", "Corrects entry", "UPSI", "Nature of UPSI", "UPSI public on",
                    "Shared at (IST)", "Shared by", "Shared by PAN", "Shared with", "Shared with PAN or identifier",
                    "Organisation", "Recipient type", "Purpose", "Recorded at (IST)", "Recorded by",
                    "Previous hash", "Hash"])
        for r in sdd.export_rows(uid, eid):
            recorded_by = me.get("email") if r["recorded_by"] == uid and me.get("email") else r["recorded_by"]
            w.writerow([_csv_cell(v) for v in (
                r["id"], "Correction" if r["action"] == "correction" else "Sharing", r["corrects_id"] or "",
                r["upsi_title"], r["upsi_nature"], r["upsi_public_on"],
                r["shared_at"].replace("T", " "), r["shared_by_name"], r["shared_by_pan"],
                r["shared_with_name"], r["shared_with_pan"], r["shared_with_org"], r["recipient_label"],
                r["purpose"], r["recorded_at"].replace("T", " "), recorded_by, r["prev_hash"], r["hash"])])
        # The symbol can be edited freely in the workspace; a quote or a
        # non-Latin letter would break the header, so both sources are slugged.
        slug = re.sub(r"[^a-z0-9]+", "-", (ent.get("symbol") or ent["name"]).lower()).strip("-")[:40] or "entity"
        resp = Response(buf.getvalue(), mimetype="text/csv; charset=utf-8")
        resp.headers["Content-Disposition"] = f'attachment; filename="sdd-{slug}-{sdd.today_ist().isoformat()}.csv"'
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ── Entity (for users who have none yet) ─────────────────────────────

    @bp.route("/api/sdd/entities", methods=["POST"])
    @login_required
    @json_api
    def api_create_entity():
        data = body()
        symbol = re.sub(r"\s+", "", sdd._text(data.get("symbol"), 40)).upper()
        if symbol and not SYMBOL_RE.match(symbol):
            raise ValueError("Use the NSE symbol as listed, e.g. TATAMOTORS or BAJAJ-AUTO.")
        eid = ents.create_entity(current_user_id(), {
            "name": sdd._text(data.get("name"), 200), "entity_type": "listed", "symbol": symbol})
        return jsonify(ok=True, id=eid, url=f"/sdd/{eid}"), 201

    # ── UPSI and the sharing log ─────────────────────────────────────────

    @bp.route("/api/sdd/<int:eid>/upsi", methods=["POST"])
    @login_required
    @json_api
    def api_create_upsi(eid):
        data = body()
        uid = sdd.create_upsi(current_user_id(), eid, data.get("title"), data.get("nature"))
        return (jsonify(ok=True, id=uid), 201) if uid else not_found()

    @bp.route("/api/sdd/<int:eid>/upsi/<int:upsi_id>/public", methods=["POST"])
    @login_required
    @json_api
    def api_upsi_public(eid, upsi_id):
        ok = sdd.mark_upsi_public(current_user_id(), eid, upsi_id, body().get("public_on"))
        return jsonify(ok=True) if ok else not_found()

    @bp.route("/api/sdd/<int:eid>/entries", methods=["POST"])
    @login_required
    @json_api
    def api_record_entry(eid):
        entry_id = sdd.record_entry(current_user_id(), eid, body())
        return (jsonify(ok=True, id=entry_id), 201) if entry_id else not_found()

    @bp.route("/api/sdd/<int:eid>/entries/<int:entry_id>")
    @login_required
    def api_get_entry(eid, entry_id):
        entry = sdd.get_entry(current_user_id(), eid, entry_id)
        return jsonify(entry) if entry else not_found()

    @bp.route("/api/sdd/<int:eid>/verify")
    @login_required
    def api_verify(eid):
        result = sdd.verify_chain(current_user_id(), eid)
        if result is None:
            return not_found()
        label, cls = _chain_label(result)
        return jsonify({**result, "label": label, "chip": cls, "checked_label": _fmt_dt(result["checked_at"])})

    # ── Designated persons ───────────────────────────────────────────────

    @bp.route("/api/sdd/<int:eid>/persons", methods=["POST"])
    @login_required
    @json_api
    def api_add_person(eid):
        pid = sdd.add_person(current_user_id(), eid, body())
        return (jsonify(ok=True, id=pid), 201) if pid else not_found()

    @bp.route("/api/sdd/<int:eid>/persons/<int:pid>", methods=["DELETE"])
    @login_required
    @json_api
    def api_remove_person(eid, pid):
        return jsonify(ok=True) if sdd.remove_person(current_user_id(), eid, pid) else not_found()

    # ── Trading window ───────────────────────────────────────────────────

    @bp.route("/api/sdd/<int:eid>/windows", methods=["POST"])
    @login_required
    @json_api
    def api_close_window(eid):
        data = body()
        wid = sdd.close_window(current_user_id(), eid, data.get("closes_on"), data.get("opens_on") or "",
                               data.get("reason") or "")
        return (jsonify(ok=True, id=wid), 201) if wid else not_found()

    @bp.route("/api/sdd/<int:eid>/windows/<int:wid>/open", methods=["POST"])
    @login_required
    @json_api
    def api_open_window(eid, wid):
        opens_on = body().get("opens_on")
        if not opens_on:
            raise ValueError("Give the reopening date.")
        ok = sdd.set_window_open(current_user_id(), eid, wid, opens_on)
        return jsonify(ok=True) if ok else not_found()

    # ── Pre-clearance ────────────────────────────────────────────────────

    @bp.route("/api/sdd/<int:eid>/preclearance", methods=["POST"])
    @login_required
    @json_api
    def api_request_preclearance(eid):
        rid = sdd.request_preclearance(current_user_id(), eid, body())
        return (jsonify(ok=True, id=rid), 201) if rid else not_found()

    @bp.route("/api/sdd/<int:eid>/preclearance/<int:rid>/decide", methods=["POST"])
    @login_required
    @json_api
    def api_decide_preclearance(eid, rid):
        data = body()
        decision = data.get("decision")
        if decision not in ("approve", "reject"):
            raise ValueError("Choose approve or reject.")
        result = sdd.decide_preclearance(current_user_id(), eid, rid, decision == "approve", data.get("note") or "")
        if not result:
            return not_found()
        return jsonify(ok=True, **result, valid_until_label=sdd.human_date(result["valid_until"]),
                       cut_by_closure_label=sdd.human_date(result["cut_by_closure"]))

    return bp
