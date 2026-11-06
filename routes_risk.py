"""Risk desk — counterparty watch for chief risk officers.

Pages: portfolio (/risk), one counterparty, the alert queue, the director
network and the printable committee report, plus a CSV export. JSON APIs
under /api/risk. Every read and write is scoped to the signed-in user;
backend functions return None/False for records the user does not own and
those become 404s here.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, jsonify, render_template, request

import risk
from database import get_watchlist

SEV_ORDER = ["critical", "high", "medium", "low"]
SEV_LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}
BAND_ORDER = ["Severe", "High", "Moderate", "Low"]
STATUS_FILTERS = [("active", "Active"), ("open", "Open"), ("acknowledged", "Acknowledged"),
                  ("resolved", "Resolved"), ("dismissed", "Dismissed"), ("all", "All")]


# ── Formatting helpers (also registered as template filters) ───────────────

def _indian_group(n):
    s = str(int(round(abs(n))))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def inr(n):
    """Rupees in lakh / crore, matching dfInr() in app_base."""
    n = float(n or 0)
    if n >= 1e7:
        return f"₹{n / 1e7:.0f} cr" if n >= 1e9 else f"₹{n / 1e7:.2f} cr"
    if n >= 1e5:
        return f"₹{n / 1e5:.2f} L"
    return "₹" + _indian_group(n)


def rel_time(ts):
    if not ts:
        return "Not yet"
    try:
        then = datetime.fromisoformat(str(ts)[:19])
    except ValueError:
        return str(ts)[:10]
    secs = (datetime.now() - then).total_seconds()
    if secs < 60:
        return "Just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        h = int(secs // 3600)
        return f"{h} h ago"
    d = int(secs // 86400)
    if d == 1:
        return "Yesterday"
    if d < 30:
        return f"{d} days ago"
    return then.strftime("%d %b %Y")


def nice_date(s):
    try:
        return date.fromisoformat(str(s)[:10]).strftime("%d %b %Y")
    except ValueError:
        return s or ""


def days_since(s):
    try:
        return (date.today() - date.fromisoformat(str(s)[:10])).days
    except ValueError:
        return None


def safe_url(u):
    """Only http(s) links are rendered as hrefs."""
    u = (u or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else ""


def _sparkline(points, w=640, h=120, pad=8):
    """Polyline for a 0–100 score series in a fixed viewBox, x by date."""
    if not points:
        return None
    days = [date.fromisoformat(p["day"][:10]) for p in points]
    span = max(1, (days[-1] - days[0]).days)
    xs = [pad + (w - 2 * pad) * ((d - days[0]).days / span) for d in days]
    ys = [pad + (h - 2 * pad) * (1 - min(100, max(0, p["score"])) / 100) for p in points]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    area = f"M{xs[0]:.1f},{h - pad:.1f} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in zip(xs, ys)) + f" L{xs[-1]:.1f},{h - pad:.1f} Z"
    bands = [{"label": lbl, "y": round(pad + (h - 2 * pad) * (1 - v / 100), 1)} for v, lbl in ((60, "Severe"), (35, "High"), (15, "Moderate"))]
    scores = [p["score"] for p in points]
    return {"w": w, "h": h, "line": line, "area": area, "bands": bands,
            "last_x": round(xs[-1], 1), "last_y": round(ys[-1], 1),
            "first_day": nice_date(points[0]["day"]), "last_day": nice_date(points[-1]["day"]),
            "min": min(scores), "max": max(scores)}


# ── Request helpers ─────────────────────────────────────────────────────────

def _json_body():
    """(data, error_response). Mutating endpoints accept JSON only."""
    if not request.is_json:
        return None, (jsonify(error="JSON body required"), 415)
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None, (jsonify(error="Send a JSON object."), 400)
    return data, None


def _signal_json(s):
    return {"id": s["id"], "status": s["status"], "assignee": s.get("assignee") or "",
            "severity": s["severity"], "title": s["title"]}


def create_blueprint(login_required, current_user_id, current_user):
    bp = Blueprint("risk_desk", __name__)

    bp.add_app_template_filter(inr, "risk_inr")
    bp.add_app_template_filter(rel_time, "risk_rel")
    bp.add_app_template_filter(nice_date, "risk_date")
    bp.add_app_template_filter(risk.mask_pan, "risk_mask_pan")
    bp.add_app_template_filter(safe_url, "risk_url")

    def _page(template, active, **ctx):
        uid = current_user_id()
        return render_template(template, active_page=active, watchlist=get_watchlist(uid),
                               sev_label=SEV_LABEL, statuses=risk.STATUSES, categories=risk.CATEGORIES, **ctx)

    def _owned(cid):
        cp = risk.get_counterparty(current_user_id(), cid)
        if not cp:
            abort(404)
        return cp

    # ── Pages ───────────────────────────────────────────────────────────────

    @bp.route("/risk")
    @login_required
    def risk_home():
        uid = current_user_id()
        risk.scan_pending(uid)
        pf = risk.portfolio(uid)
        latest = risk.list_signals(uid, status="active", limit=8, alerts_only=True)
        total = pf["total_exposure"] or 0
        strip = [{"band": b, "count": pf["by_band"][b], "exposure": pf["exposure_by_band"][b],
                  "pct": (pf["exposure_by_band"][b] / total * 100) if total else 0} for b in BAND_ORDER]
        return _page("risk.html", "risk", pf=pf, latest=latest, strip=strip,
                     at_risk_pct=(pf["exposure_at_risk"] / total * 100) if total else 0,
                     live_running=risk.live_scan_running(uid))

    @bp.route("/risk/counterparty/<int:cid>")
    @login_required
    def risk_counterparty(cid):
        uid = current_user_id()
        cp = _owned(cid)
        sc = risk.score_counterparty(cid)
        # Recorded daily scores where we have them; before the first one,
        # weekly scores rebuilt from signal dates so the chart spans 90 days.
        recorded = risk.score_history(cid, 90)
        first = recorded[0]["day"] if recorded else None
        hist = [p for p in risk.rebuilt_history(cid, 90) if not first or p["day"] < first] + recorded
        rebuilt_until = nice_date(first) if first and hist[0]["day"] < first else (None if first else "today")
        signals = risk.list_signals(uid, cid, limit=500)
        counts = {k: sum(1 for s in signals if s["status"] == k) for k in risk.STATUSES}
        counts["active"] = counts["open"] + counts["acknowledged"]
        counts["alerts"] = sum(1 for s in signals if s["status"] in ("open", "acknowledged")
                               and s["severity"] in risk.ALERT_SEVERITIES)
        return _page("risk_counterparty.html", "risk", cp=cp, sc=sc, change=risk.change_30d(cid, sc["score"]),
                     spark=_sparkline(hist), rebuilt_until=rebuilt_until, signals=signals, counts=counts,
                     people=risk.list_people(cid), today=date.today().isoformat())

    @bp.route("/risk/alerts")
    @login_required
    def risk_alerts():
        uid = current_user_id()
        status = request.args.get("status", "active")
        if status not in dict(STATUS_FILTERS):
            status = "active"
        # Default view is alerts (medium and above); "all" adds low-severity notes.
        severity = request.args.get("severity", "")
        severity = severity if (severity in risk.SEVERITY_WEIGHT or severity == "all") else ""
        source = request.args.get("source", "")
        source = source if source in risk.SOURCE_GROUPS else ""
        q = (request.args.get("q") or "").strip()[:80]
        try:
            cp_id = int(request.args.get("cp") or 0)
        except ValueError:
            cp_id = 0
        cps = risk.list_counterparties(uid)
        if cp_id and not any(c["id"] == cp_id for c in cps):
            cp_id = 0
        base = risk.list_signals(uid, cid=cp_id or None, status=None if status == "all" else status,
                                 source=source or None, q=q or None, limit=5000)
        sev_counts = {k: sum(1 for s in base if s["severity"] == k) for k in SEV_ORDER}
        if severity == "all":
            rows = list(base)
        elif severity:
            rows = [s for s in base if s["severity"] == severity]
        else:
            rows = [s for s in base if s["severity"] in risk.ALERT_SEVERITIES]
        rows.sort(key=lambda s: (s["event_date"], s["id"]), reverse=True)
        rows.sort(key=lambda s: risk.SEVERITY_ORDER.get(s["severity"], 9))
        shown = rows[:500]
        filtered = bool(severity or source or q or cp_id or status != "active")
        return _page("risk_alerts.html", "risk_alerts", rows=shown, total=len(rows), sev_counts=sev_counts,
                     hidden_low=sev_counts["low"] if not severity else 0,
                     status=status, severity=severity, source=source, q=q, cp_id=cp_id, cps=cps,
                     status_filters=STATUS_FILTERS, sources=risk.SOURCE_GROUPS, filtered=filtered,
                     has_counterparties=bool(cps))

    @bp.route("/risk/network")
    @login_required
    def risk_network():
        uid = current_user_id()
        net = risk.director_network(uid)
        return _page("risk_network.html", "risk_network", net=net,
                     cp_count=len(risk.list_counterparties(uid)))

    @bp.route("/risk/report")
    @login_required
    def risk_report():
        uid = current_user_id()
        cu = current_user() or {}
        pf = risk.portfolio(uid)
        today = date.today()
        since = today - timedelta(days=30)
        all_sigs = risk.list_signals(uid, limit=10000)
        recent = [s for s in all_sigs if s["event_date"] >= since.isoformat() and s["status"] != "dismissed"]
        recent_by_sev = {k: [s for s in recent if s["severity"] == k] for k in SEV_ORDER}
        open_serious = [dict(s, age=days_since(s["event_date"])) for s in all_sigs
                        if s["status"] in ("open", "acknowledged") and s["severity"] in ("critical", "high")]
        open_serious.sort(key=lambda s: (risk.SEVERITY_ORDER[s["severity"]], -(s["age"] or 0)))
        total = pf["total_exposure"] or 0
        band_rows = [{"band": b, "count": pf["by_band"][b], "exposure": pf["exposure_by_band"][b],
                      "pct": (pf["exposure_by_band"][b] / total * 100) if total else 0} for b in BAND_ORDER]
        cat_rows = sorted(
            [{"key": k, "label": risk.CATEGORIES.get(k, k.title()), **v,
              "pct_at_risk": (v["at_risk"] / v["exposure"] * 100) if v["exposure"] else 0}
             for k, v in pf["by_category"].items()], key=lambda r: -r["exposure"])
        top = [r for r in pf["rows"] if r["score"] > 0][:10]
        return _page("risk_report.html", "risk_report", pf=pf, band_rows=band_rows, cat_rows=cat_rows, top=top,
                     recent=recent, recent_by_sev=recent_by_sev, open_serious=open_serious,
                     institution=(cu.get("company") or cu.get("name") or "").strip(),
                     today=today, since=since, total=total,
                     at_risk_pct=(pf["exposure_at_risk"] / total * 100) if total else 0,
                     weights=risk.SEVERITY_WEIGHT, bands=risk.BANDS)

    @bp.route("/risk/export.csv")
    @login_required
    def risk_export():
        uid = current_user_id()
        pf = risk.portfolio(uid)

        def cell(v):
            # Spreadsheet formula injection: a name like "=HYPERLINK(...)"
            # must stay text when the file is opened in Excel.
            if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
                return "'" + v
            return v

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Counterparty", "Category", "NSE symbol", "CIN", "PAN (masked)", "Exposure (INR)", "Score", "Band",
                    "30-day change", "Active alerts", "Critical alerts", "Latest signal", "Latest signal date",
                    "Owner", "Last scanned"])
        for r in pf["rows"]:
            ls = r.get("latest_signal") or {}
            w.writerow([cell(x) for x in (
                r["name"], risk.CATEGORIES.get(r["category"], r["category"]), r["symbol"], r["cin"],
                risk.mask_pan(r["pan"]), round(r["exposure_inr"] or 0, 2), r["score"], r["band"],
                r["change_30d"] if r["change_30d"] is not None else "", r["active_alerts"], r["critical_alerts"],
                ls.get("title", ""), ls.get("event_date", ""), r["owner"], (r["last_scanned_at"] or "")[:16].replace("T", " "))])
        body = "﻿" + buf.getvalue()
        return Response(body, mimetype="text/csv", headers={
            "Content-Disposition": f"attachment; filename=counterparty-risk-{date.today().isoformat()}.csv",
            "Cache-Control": "no-store"})

    # ── Counterparty APIs ───────────────────────────────────────────────────

    @bp.route("/api/risk/counterparties", methods=["POST"])
    @login_required
    def api_add_counterparty():
        data, err = _json_body()
        if err:
            return err
        uid = current_user_id()
        try:
            name = risk._s(data.get("name"))
            dup = risk.find_duplicate(uid, name, data.get("symbol")) if name else None
            if dup:
                return jsonify(error=f"{name} is already on your list.", id=dup, url=f"/risk/counterparty/{dup}"), 409
            cid = risk.add_counterparty(uid, data, scan=False)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        res = risk.scan_counterparty(cid, live=False) or {}
        live = risk.start_initial_scan(cid)
        return jsonify(id=cid, url=f"/risk/counterparty/{cid}", new=res.get("new", 0),
                       score=(res.get("score") or {}).get("score", 0), live=live), 201

    @bp.route("/api/risk/import/preview", methods=["POST"])
    @login_required
    def api_import_preview():
        data, err = _json_body()
        if err:
            return err
        uid = current_user_id()
        text = risk._s(data.get("text"))
        if not text:
            return jsonify(error="Paste at least one line."), 400
        rows = risk.parse_bulk(text)
        if not rows:
            return jsonify(error="No counterparties found. Put one per line, starting with the name."), 400
        out = []
        seen = set()
        for r in rows[:500]:
            key = (r["name"].lower(), r["symbol"])
            dup = bool(risk.find_duplicate(uid, r["name"], r["symbol"])) or key in seen
            seen.add(key)
            out.append({"name": r["name"], "symbol": r["symbol"], "cin": r["cin"], "pan": risk.mask_pan(r["pan"]),
                        "category": risk.CATEGORIES.get(r["category"], "Other"), "exposure_inr": r["exposure_inr"],
                        "duplicate": dup})
        return jsonify(rows=out, count=len(out), duplicates=sum(1 for r in out if r["duplicate"]))

    @bp.route("/api/risk/import", methods=["POST"])
    @login_required
    def api_import():
        data, err = _json_body()
        if err:
            return err
        uid = current_user_id()
        text = risk._s(data.get("text"))
        rows = risk.parse_bulk(text)
        if not rows:
            return jsonify(error="No counterparties found. Put one per line, starting with the name."), 400
        created, skipped, errors = [], 0, []
        for r in rows[:500]:
            if risk.find_duplicate(uid, r["name"], r["symbol"]):
                skipped += 1
                continue
            try:
                created.append(risk.add_counterparty(uid, r, scan=False))
            except ValueError as e:
                errors.append(f"{r['name']}: {e}")
        new_signals = 0
        for cid in created:
            new_signals += (risk.scan_counterparty(cid, live=False) or {}).get("new", 0)
        live = bool(created) and risk.scan_user_async(uid)
        return jsonify(created=len(created), skipped=skipped, errors=errors[:20], new_signals=new_signals, live=live)

    @bp.route("/api/risk/counterparties/<int:cid>", methods=["PATCH"])
    @login_required
    def api_update_counterparty(cid):
        data, err = _json_body()
        if err:
            return err
        uid = current_user_id()
        before = risk.get_counterparty(uid, cid)
        if not before:
            return jsonify(error="Counterparty not found."), 404
        try:
            if "name" in data or "symbol" in data:
                dup = risk.find_duplicate(uid, data.get("name", before["name"]), data.get("symbol", before["symbol"]),
                                          exclude_id=cid)
                if dup:
                    return jsonify(error="Another counterparty on your list has that name or symbol."), 409
            if not risk.update_counterparty(uid, cid, data):
                return jsonify(error="Counterparty not found."), 404
        except ValueError as e:
            return jsonify(error=str(e)), 400
        after = risk.get_counterparty(uid, cid)
        if after["symbol"] != before["symbol"] or after["name"] != before["name"]:
            risk.scan_counterparty(cid, live=False)
        return jsonify(ok=True)

    @bp.route("/api/risk/counterparties/<int:cid>", methods=["DELETE"])
    @login_required
    def api_archive_counterparty(cid):
        _, err = _json_body()
        if err:
            return err
        if not risk.archive_counterparty(current_user_id(), cid):
            return jsonify(error="Counterparty not found."), 404
        return jsonify(ok=True)

    @bp.route("/api/risk/counterparties/<int:cid>/scan", methods=["POST"])
    @login_required
    def api_scan_one(cid):
        _, err = _json_body()
        if err:
            return err
        if not risk.get_counterparty(current_user_id(), cid):
            return jsonify(error="Counterparty not found."), 404
        live = risk.live_enabled()
        res = risk.scan_counterparty(cid, live=live) or {}
        sc = res.get("score") or {}
        return jsonify(new=res.get("new", 0), score=sc.get("score", 0), band=sc.get("band", "Low"), live=live)

    @bp.route("/api/risk/scan", methods=["POST"])
    @login_required
    def api_scan_all():
        _, err = _json_body()
        if err:
            return err
        uid = current_user_id()
        n = risk.scan_user(uid, live=False)
        live = risk.scan_user_async(uid)
        return jsonify(new=n, live=live, live_running=risk.live_scan_running(uid))

    @bp.route("/api/risk/counterparties/<int:cid>/signals", methods=["POST"])
    @login_required
    def api_add_signal(cid):
        data, err = _json_body()
        if err:
            return err
        try:
            sid = risk.add_manual_signal(current_user_id(), cid, data)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not sid:
            return jsonify(error="Counterparty not found."), 404
        return jsonify(id=sid), 201

    @bp.route("/api/risk/counterparties/<int:cid>/people", methods=["POST"])
    @login_required
    def api_add_person(cid):
        data, err = _json_body()
        if err:
            return err
        try:
            pid = risk.add_person(current_user_id(), cid, data)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not pid:
            return jsonify(error="Counterparty not found."), 404
        return jsonify(id=pid), 201

    @bp.route("/api/risk/counterparties/<int:cid>/people/<int:pid>", methods=["DELETE"])
    @login_required
    def api_remove_person(cid, pid):
        _, err = _json_body()
        if err:
            return err
        if not risk.remove_person(current_user_id(), cid, pid):
            return jsonify(error="Director not found."), 404
        return jsonify(ok=True)

    # ── Signal APIs ─────────────────────────────────────────────────────────

    @bp.route("/api/risk/signals/<int:sid>/action", methods=["POST"])
    @login_required
    def api_signal_action(sid):
        data, err = _json_body()
        if err:
            return err
        uid = current_user_id()
        action = data.get("action")
        try:
            ok = risk.act_on_signal(uid, sid, action, data.get("note"), assignee=data.get("assignee"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not ok:
            return jsonify(error="Alert not found."), 404
        return jsonify(ok=True, signal=_signal_json(risk.get_signal(uid, sid)))

    @bp.route("/api/risk/signals/bulk", methods=["POST"])
    @login_required
    def api_signal_bulk():
        data, err = _json_body()
        if err:
            return err
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            return jsonify(error="Select at least one alert."), 400
        try:
            n = risk.bulk_act(current_user_id(), ids, data.get("action"), data.get("note"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not n:
            return jsonify(error="None of those alerts were found."), 404
        return jsonify(updated=n)

    @bp.route("/api/risk/signals/<int:sid>/log")
    @login_required
    def api_signal_log(sid):
        uid = current_user_id()
        if not risk.get_signal(uid, sid):
            return jsonify(error="Alert not found."), 404
        log = [{"action": e["action"], "note": e["note"] or "", "at": e["at"],
                "when": rel_time(e["at"]), "date": nice_date(e["at"])} for e in risk.signal_log(uid, sid)]
        return jsonify(log=log)

    return bp
