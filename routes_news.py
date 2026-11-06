"""Regulatory news radar: SEBI and RBI updates, filtered for the signed-in user.

The feed itself is global (reg_news.py); what is per user is the "For you"
selection (role + entity profiles), the counterparty mentions (their risk
desk list) and the tasks they file against their own entities.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

import os

import company_news
import entities as ents
import reg_news as news
import risk
import roles
from database import get_watchlist
from demo_cache import frozen as demo_frozen

PAGE_SIZE = 30
MAX_LIMIT = 100
MANUAL_REFRESH_GAP = 300  # seconds between manual refreshes, per process
TABS = {
    "foryou": "For you",
    "all": "All",
    "circulars": "Circulars",
    "enforcement": "Enforcement",
    "rbi": "RBI",
}

_refresh_lock = threading.Lock()
_last_manual_refresh = None  # time.monotonic() of the last manual refresh

_PAN_RE = re.compile(r"\b([A-Z]{2})[A-Z]{3}\d{2}(\d{2}[A-Z])\b")


def _mask_pan(text):
    # SEBI recovery titles print the defaulter's PAN; show it masked.
    return _PAN_RE.sub(lambda m: m.group(1) + "•" * 5 + m.group(2), text or "")


def _updated_label(iso):
    if not iso:
        return "Not updated yet"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "Not updated yet"
    secs = max(0, (datetime.now() - dt).total_seconds())
    if secs < 60:
        return "Updated just now"
    mins = int(secs // 60)
    if mins < 60:
        return f"Updated {mins} minute{'s' if mins != 1 else ''} ago"
    hours = mins // 60
    if hours < 24:
        return f"Updated {hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return "Updated yesterday" if days == 1 else f"Updated {days} days ago"


def _date_label(iso):
    try:
        d = date.fromisoformat((iso or "")[:10])
    except ValueError:
        return ""
    return f"{d.day} {d:%b %Y}"


def _int_arg(args, name, default, lo, hi):
    raw = args.get(name)
    if raw in (None, ""):
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number.")
    return max(lo, min(hi, v))


def _relevant(uid):
    return news.relevant_topics(roles.get_role(uid), ents.list_entities(uid))


def _parse(uid, args):
    """Query args -> (state, filters, topic, offset, limit). ValueError on bad input."""
    tab = args.get("tab") or "foryou"
    if tab not in TABS:
        raise ValueError("Unknown tab.")
    source = (args.get("source") or "").strip().upper()
    if source and source not in news.SOURCES:
        raise ValueError("Source must be SEBI or RBI.")
    kind = (args.get("kind") or "").strip().lower()
    if kind and kind not in news.KIND_LABELS:
        raise ValueError("Unknown kind.")
    topic = (args.get("topic") or "").strip()
    if topic and topic not in news.TOPICS:
        raise ValueError("Unknown topic.")
    q = (args.get("q") or "").strip()[:80]
    include_recovery = (args.get("include_recovery") or "").lower() in ("1", "true", "yes", "on")
    offset = _int_arg(args, "offset", 0, 0, 100000)
    limit = _int_arg(args, "limit", PAGE_SIZE, 1, MAX_LIMIT)

    filters = {"source": source or None, "kind": kind or None, "q": q or None}
    if tab == "foryou":
        filters["topics_any"] = sorted(_relevant(uid))
    elif tab == "circulars":
        filters["kinds"] = news.CIRCULAR_KINDS
    elif tab == "enforcement":
        filters["kinds"] = news.ENFORCEMENT_KINDS
    elif tab == "rbi":
        filters["source"] = "RBI"
    if tab in ("foryou", "all") and not include_recovery and kind != "recovery":
        filters["exclude_kinds"] = ("recovery",)

    state = {"tab": tab, "topic": topic, "source": source if tab != "rbi" else "", "q": q,
             "include_recovery": include_recovery}
    return state, filters, topic, offset, limit


def _payload(uid, filters, topic, offset, limit, with_counts):
    rows = news.list_news(topic=topic or None, limit=limit + 1, offset=offset, **filters)
    has_more = len(rows) > limit
    rows = rows[:limit]

    cps = [(c["id"], c["name"], risk.norm_name(c["name"])) for c in risk.list_counterparties(uid)]
    items = []
    for r in rows:
        mentions = []
        if cps:
            title_norm = risk.norm_name(r["title"])
            # name_match skips one-word names ("infosys") unless the whole title
            # is the name; for SEBI orders also use the risk desk's matcher,
            # which reads the "in the matter of / in respect of" party.
            sebi = r["source"] == "SEBI"
            mentions = [{"id": cid, "name": name} for cid, name, cn in cps
                        if risk.name_match(cn, title_norm) or (sebi and risk.sebi_match(name, r["title"]))]
        items.append({
            "id": r["id"],
            "source": r["source"],
            "kind": r["kind"],
            "kind_label": r["kind_label"],
            "title": _mask_pan(r["title"]),
            "url": r["url"],
            "excerpt": _mask_pan(r["excerpt"]),
            "topics": r["topic_list"],
            "date": (r["published_at"] or "")[:10],
            "date_label": _date_label(r["published_at"]),
            "mentions": mentions,
        })
    out = {
        "items": items,
        "has_more": has_more,
        "next_offset": offset + len(items),
        "total_stored": news.count_all(),
        "last_refresh_label": _updated_label(news.last_refresh()),
        "refreshing": news.refreshing(),
    }
    if with_counts:
        counts = news.topic_counts(**{k: v for k, v in filters.items() if k != "topic"})
        # A list, not a dict: JSON responses sort keys and topics have an order.
        out["topic_counts"] = [[t, n] for t, n in counts.items()]
    return out


_LOGO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logos")


def _logo_url(symbol):
    """The committed brand logo for a symbol, or '' when there is none."""
    for ext in ("svg", "png", "jpg", "jpeg", "webp"):
        if os.path.exists(os.path.join(_LOGO_DIR, f"{symbol}.{ext}")):
            return f"/static/logos/{symbol}.{ext}"
    return ""


def create_blueprint(login_required, current_user_id, current_user):
    bp = Blueprint("news", __name__)

    @bp.route("/news")
    @login_required
    def news_page():
        uid = current_user_id()
        watchlist = get_watchlist(uid)
        if (request.args.get("view") or "").strip() != "regulatory" and watchlist:
            by_symbol = {w["symbol"]: w for w in watchlist}
            symbol = (request.args.get("symbol") or "").strip().upper()
            if symbol not in by_symbol:
                # The URL always names the company: Copilot reads it from the query
                # to ground its answers, and the link is shareable.
                return redirect(url_for("news.news_page", symbol=watchlist[0]["symbol"]))
            chosen = by_symbol[symbol]
            return render_template(
                "news_company.html", active_page="news", watchlist=watchlist, symbol=symbol,
                company={"symbol": symbol, "name": company_news.clean_name(chosen.get("name") or symbol),
                         "logo": _logo_url(symbol)},
                frozen=demo_frozen(),
            )
        started = news.refresh_async()
        try:
            state, filters, topic, _, _ = _parse(uid, request.args)
        except ValueError:
            state, filters, topic, _, _ = _parse(uid, {})
        init = _payload(uid, filters, topic, 0, PAGE_SIZE, with_counts=True)
        init["state"] = state
        # The thread may not hold the fetch lock yet; tell the page anyway.
        init["refreshing"] = init["refreshing"] or started

        role = roles.get_role(uid)
        entity_list = ents.list_entities(uid)
        relevant = news.relevant_topics(role, entity_list)
        return render_template(
            "news.html",
            active_page="news",
            watchlist=get_watchlist(uid),
            init=init,
            state=state,
            tabs=TABS,
            role_label=roles.ROLES.get(role, {}).get("label", ""),
            for_you_topics=[t for t in news.TOPICS if t in relevant],
            has_entities=bool(entity_list),
            entities=[{"id": e["id"], "name": e["name"]} for e in entity_list],
            last_refresh_label=init["last_refresh_label"],
        )

    @bp.route("/api/news/company/<symbol>")
    @login_required
    def api_company_news(symbol):
        """The company desk, served from the response cache; ?refresh=1 rebuilds it."""
        uid = current_user_id()
        symbol = (symbol or "").strip().upper()
        names = {w["symbol"]: (w.get("name") or w["symbol"]) for w in get_watchlist(uid)}
        if symbol not in names:
            return jsonify(error="Add the company to your watchlist first."), 404
        refresh = (request.args.get("refresh") or "").lower() in ("1", "true", "yes") and not demo_frozen()
        try:
            payload = company_news.desk(symbol, names[symbol], refresh=refresh)
        except Exception as e:  # a dead upstream must read as a message, not a 500 page
            print(f"[NEWS] desk build failed for {symbol}: {e}")
            return jsonify(error="Could not build the desk right now. Try again in a minute.", detail=str(e)[:160]), 502
        if payload is None:
            return jsonify(frozen=True, symbol=symbol, company=names[symbol])
        return jsonify(payload)

    @bp.route("/api/news")
    @login_required
    def api_news():
        uid = current_user_id()
        try:
            state, filters, topic, offset, limit = _parse(uid, request.args)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        out = _payload(uid, filters, topic, offset, limit, with_counts=(offset == 0))
        out["state"] = state
        return jsonify(out)

    @bp.route("/api/news/refresh", methods=["POST"])
    @login_required
    def api_news_refresh():
        global _last_manual_refresh
        if not request.is_json:
            return jsonify(error="JSON body required"), 415
        if demo_frozen():
            return jsonify(new=0, message="Live fetching is paused in this demo. Showing saved updates.",
                           last_refresh_label=_updated_label(news.last_refresh()))
        if news.refreshing():
            return jsonify(new=0, message="Already checking SEBI and RBI. The list will update shortly.",
                           last_refresh_label=_updated_label(news.last_refresh()))
        with _refresh_lock:
            now = time.monotonic()
            if _last_manual_refresh is not None and now - _last_manual_refresh < MANUAL_REFRESH_GAP:
                wait = int((MANUAL_REFRESH_GAP - (now - _last_manual_refresh) + 59) // 60)
                return jsonify(error=f"Checked a moment ago. Try again in {wait} minute{'s' if wait != 1 else ''}."), 429
            _last_manual_refresh = now
        before = news.last_refresh()
        new = news.refresh(force=True)
        if not new and news.last_refresh() == before:
            with _refresh_lock:
                _last_manual_refresh = None
            if news.refreshing():
                # A background fetch took the lock first; it does the same job.
                return jsonify(new=0, message="Already checking SEBI and RBI. The list will update shortly.",
                               last_refresh_label=_updated_label(news.last_refresh()))
            # Every feed failed (refresh only stamps last_refresh on success).
            return jsonify(error="Could not reach SEBI or RBI just now. Try again in a minute."), 502
        if new:
            message = f"{new} new update{'s' if new != 1 else ''}."
        else:
            message = "No new updates since the last check."
        return jsonify(new=new, message=message, last_refresh_label=_updated_label(news.last_refresh()))

    @bp.route("/api/news/<int:nid>/task", methods=["POST"])
    @login_required
    def api_news_task(nid):
        if not request.is_json:
            return jsonify(error="JSON body required"), 415
        uid = current_user_id()
        item = news.get_item(nid)
        if not item:
            return jsonify(error="Update not found."), 404
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(error="Send a JSON object."), 400
        try:
            eid = int(body.get("entity_id"))
        except (TypeError, ValueError):
            return jsonify(error="Choose an entity."), 400
        # Keep the source link even if the note was edited or is long:
        # the note is trimmed, the URL always goes last (notes cap at 1000).
        url = item["url"]
        note = str(body.get("notes") or "").replace(url, "").strip()
        notes = (note[:max(0, 1000 - len(url) - 1)].rstrip() + "\n" + url).strip()
        title = "Review: " + _mask_pan(item["title"])[:150]
        try:
            oid = ents.add_task(uid, eid, title, str(body.get("due_date") or ""), notes=notes, origin="news")
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if oid is None:
            return jsonify(error="Entity not found."), 404
        return jsonify(ok=True, id=oid, entity_id=eid, title=title), 201

    return bp
