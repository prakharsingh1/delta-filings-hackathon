"""Statutory registers and the meeting kit: pages, CSV / .docx downloads, JSON APIs.

Every read and write goes through secretarial.py / entities.py with the
signed-in user's id; anything the user does not own is a 404.
"""
import csv
import io
import re
from datetime import date

from flask import Blueprint, Response, abort, jsonify, render_template, request, send_file

import entities as ents
import secretarial as sec
from database import get_watchlist

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _csv_safe(v):
    """Stop spreadsheet formula injection: a cell that starts with = + - @ is
    prefixed with an apostrophe, unless it is a plain number."""
    s = str(v if v is not None else "")
    if s and s[0] in "=+-@\t\r" and not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return "'" + s
    return s


def create_blueprint(login_required, current_user_id, current_user):
    bp = Blueprint("secretarial", __name__)

    # ── helpers ────────────────────────────────────────────────────────────
    def _json_body():
        """(body, error_response). Mutating APIs take JSON objects only."""
        if not request.is_json:
            return None, (jsonify(error="JSON body required"), 415)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return None, (jsonify(error="Send a JSON object."), 400)
        return body, None

    def _entity_or_404(uid, eid):
        ent = ents.get_entity(uid, eid)
        if not ent:
            abort(404)
        return ent

    def _page(template, **ctx):
        uid = current_user_id()
        return render_template(template, active_page="workspace", watchlist=get_watchlist(uid), **ctx)

    def _register_payload(uid, eid, key):
        rows = sec.list_register(uid, eid, key, include_history=True)
        return {"rows": rows, "count": sum(1 for r in rows if r["is_current"])}

    # ── pages ──────────────────────────────────────────────────────────────
    @bp.route("/workspace/entity/<int:eid>/registers")
    @login_required
    def registers_page(eid):
        uid = current_user_id()
        ent = _entity_or_404(uid, eid)
        meta = sec.register_meta()
        data = {r["key"]: sec.list_register(uid, eid, r["key"], include_history=True) for r in meta}
        counts = sec.register_counts(uid, eid)
        tab = request.args.get("tab")
        return _page("registers.html", entity=ent, registers=meta, reg_data=data, counts=counts,
                     initial_tab=tab if tab in sec.REGISTERS else "members")

    @bp.route("/workspace/entity/<int:eid>/registers/<key>.csv")
    @login_required
    def register_csv(eid, key):
        uid = current_user_id()
        ent = _entity_or_404(uid, eid)
        reg = sec.REGISTERS.get(key)
        if not reg:
            abort(404)
        history = request.args.get("history") == "1"
        rows = sec.list_register(uid, eid, key, include_history=history)
        buf = io.StringIO()
        w = csv.writer(buf)
        head = [label for _, label in reg["fields"]] + ["Version", "Recorded at"]
        if history:
            head.append("Status")
        w.writerow(head)
        for r in rows:
            line = [_csv_safe(r["data"].get(k, "")) for k, _ in reg["fields"]]
            line += [r["version"], r["created_at"].replace("T", " ")]
            if history:
                line.append("Current" if r["is_current"] else "Superseded")
            w.writerow(line)
        safe = re.sub(r"[^A-Za-z0-9]+", "_", ent["name"]).strip("_")[:40] or "entity"
        name = f"{safe}_{reg['form'].replace(' ', '_')}_{key}{'_history' if history else ''}_{date.today().isoformat()}.csv"
        # BOM so Excel opens the ₹ headers as UTF-8.
        return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"',
                                 "Cache-Control": "no-store"})

    @bp.route("/workspace/entity/<int:eid>/meetings")
    @login_required
    def meetings_page(eid):
        uid = current_user_id()
        ent = _entity_or_404(uid, eid)
        today = date.today().isoformat()
        meetings = sec.list_meetings(uid, eid)
        upcoming = sorted([m for m in meetings if m["status"] == "planned" and m["scheduled_for"][:10] >= today],
                          key=lambda m: m["scheduled_for"])
        up_ids = {m["id"] for m in upcoming}
        past = [m for m in meetings if m["id"] not in up_ids]
        return _page("meetings.html", entity=ent, upcoming=upcoming, past=past, today=today,
                     kinds=sec.MEETING_KINDS, modes=sec.MEETING_MODES, notice=sec.notice_rules(),
                     library=sec.agenda_library(ent), fmt_day=sec.fmt_day, fmt_time=sec.fmt_time)

    @bp.route("/workspace/meetings/<int:mid>")
    @login_required
    def meeting_page(mid):
        uid = current_user_id()
        m = sec.get_meeting(uid, mid)
        if not m:
            abort(404)
        ent = _entity_or_404(uid, m["entity_id"])
        today = date.today().isoformat()
        held_default = min(m["scheduled_for"][:10], today)
        return _page("meeting_detail.html", entity=ent, meeting=m, today=today, held_default=held_default,
                     library=sec.agenda_library(ent, m["kind"]), fmt_day=sec.fmt_day, fmt_when=sec.fmt_when,
                     placeholder_labels={k: sec.placeholder_label(k) for a in sec.AGENDA_LIBRARY
                                         for k in sec.placeholders(a["resolution"])})

    @bp.route("/workspace/meetings/<int:mid>/pack.docx")
    @login_required
    def meeting_pack(mid):
        out = sec.build_docx(current_user_id(), mid)
        if not out:
            abort(404)
        buf, name = out
        return send_file(buf, mimetype=DOCX_MIME, as_attachment=True, download_name=name, max_age=0)

    # ── register APIs ──────────────────────────────────────────────────────
    @bp.route("/api/secretarial/entities/<int:eid>/registers/<key>", methods=["POST"])
    @login_required
    def api_register_add(eid, key):
        body, err = _json_body()
        if err:
            return err
        uid = current_user_id()
        if key not in sec.REGISTERS or not ents.get_entity(uid, eid):
            return jsonify(error="Not found"), 404
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        try:
            rid = sec.save_register_row(uid, eid, key, data)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not rid:
            return jsonify(error="Not found"), 404
        return jsonify(ok=True, id=rid, **_register_payload(uid, eid, key)), 201

    @bp.route("/api/secretarial/entities/<int:eid>/registers/<key>/<row_key>", methods=["PUT"])
    @login_required
    def api_register_edit(eid, key, row_key):
        body, err = _json_body()
        if err:
            return err
        uid = current_user_id()
        if not sec.get_register_row(uid, eid, key, row_key):
            return jsonify(error="Not found"), 404
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        try:
            rid = sec.save_register_row(uid, eid, key, data, row_key=row_key)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not rid:
            return jsonify(error="Not found"), 404
        return jsonify(ok=True, id=rid, **_register_payload(uid, eid, key))

    # ── meeting APIs ───────────────────────────────────────────────────────
    @bp.route("/api/secretarial/entities/<int:eid>/meetings", methods=["POST"])
    @login_required
    def api_meeting_create(eid):
        body, err = _json_body()
        if err:
            return err
        try:
            mid = sec.create_meeting(current_user_id(), eid, body)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not mid:
            return jsonify(error="Not found"), 404
        return jsonify(ok=True, id=mid, url=f"/workspace/meetings/{mid}"), 201

    @bp.route("/api/secretarial/meetings/<int:mid>/items", methods=["PUT"])
    @login_required
    def api_meeting_items(mid):
        body, err = _json_body()
        if err:
            return err
        uid = current_user_id()
        try:
            ok = sec.update_meeting_items(uid, mid, body.get("items"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not ok:
            return jsonify(error="Not found"), 404
        m = sec.get_meeting(uid, mid)
        return jsonify(ok=True, items=m["items"], checks=m["checks"], mgt14_keys=m["mgt14_keys"])

    @bp.route("/api/secretarial/meetings/<int:mid>/held", methods=["POST"])
    @login_required
    def api_meeting_held(mid):
        body, err = _json_body()
        if err:
            return err
        uid = current_user_id()
        try:
            res = sec.mark_held(uid, mid, body.get("held_on"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if res is None:
            return jsonify(error="Not found"), 404
        return jsonify(ok=True, held_on=res["held_on"], followups=res["followups"])

    @bp.route("/api/secretarial/meetings/<int:mid>", methods=["DELETE"])
    @login_required
    def api_meeting_delete(mid):
        _, err = _json_body()
        if err:
            return err
        try:
            eid = sec.delete_meeting(current_user_id(), mid)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not eid:
            return jsonify(error="Not found"), 404
        return jsonify(ok=True, redirect=f"/workspace/entity/{eid}/meetings")

    return bp
