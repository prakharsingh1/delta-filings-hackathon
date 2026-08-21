"""What is the user looking at right now.

Context is resolved server-side from the route rather than guessed by the
widget, so the assistant's view of the screen always matches what was
actually rendered.
"""

from typing import Any, Dict, Optional

PAGE_TYPES = ("dashboard", "command_center", "compliance", "company",
              "filing", "event", "director", "draft", "prices", "generic")

# Route's active_page -> copilot page_type.
_ACTIVE_PAGE_MAP = {
    "dashboard": "dashboard",
    "intelligence": "command_center",
    "compliance": "compliance",
    "company": "company",
    "announcements": "company",
    "compare": "company",
    "din_search": "director",
    "draft": "draft",
    "prices": "prices",
    "watchlist": "dashboard",
}


def build_context(active_page: str = "", view_args: Optional[Dict] = None,
                  args: Optional[Dict] = None, user_symbols=None) -> Dict[str, Any]:
    """Resolve the current screen into a context the assistant can reason over."""
    view_args = view_args or {}
    args = args or {}
    page_type = _ACTIVE_PAGE_MAP.get(active_page, "generic")

    ctx: Dict[str, Any] = {
        "page_type": page_type,
        "active_page": active_page,
        # The identifiers this context was resolved FROM. The widget echoes
        # these back on each turn so the server can rebuild the exact same
        # context; without them the round-trip lost the company and the
        # assistant answered as if it had no records.
        "source": {"active_page": active_page,
                   "view_args": dict(view_args),
                   "args": dict(args)},
        "company": None,
        "company_name": None,
        "filing_id": None,
        "event_id": None,
        "director": None,
        "din": None,
        "regulation": None,
        "document": None,
        "watchlist": list(user_symbols or []),
        "available_actions": [],
        "facts": {},
    }

    symbol = (view_args.get("symbol") or args.get("symbol") or "").upper() or None
    if symbol:
        ctx["company"] = symbol

    if view_args.get("event_id"):
        ctx["page_type"] = "event"
        ctx["event_id"] = view_args["event_id"]

    if args.get("filing_id") or view_args.get("filing_id"):
        ctx["page_type"] = "filing"
        ctx["filing_id"] = args.get("filing_id") or view_args.get("filing_id")

    if args.get("din"):
        ctx["page_type"] = "director"
        ctx["din"] = args.get("din")

    if args.get("draft_id"):
        ctx["document"] = f"draft:{args['draft_id']}"

    return enrich(ctx)


def enrich(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Attach real records for whatever the context points at.

    Everything here comes from Delta's own tables — the assistant is grounded
    in what the platform actually holds, not in what a model recalls.
    """
    from database import get_connection

    conn = get_connection()
    try:
        if ctx.get("company"):
            _enrich_company(conn, ctx)
        if ctx.get("event_id"):
            _enrich_event(ctx)
        if ctx.get("filing_id"):
            _enrich_filing(conn, ctx)
        if ctx.get("din"):
            _enrich_director(conn, ctx)
        if ctx["page_type"] in ("compliance", "command_center", "dashboard"):
            _enrich_portfolio(conn, ctx)
    except Exception as e:
        print(f"[COPILOT] context enrich failed: {str(e)[:140]}")
    finally:
        conn.close()

    ctx["available_actions"] = _actions_for(ctx)
    return ctx


def _enrich_company(conn, ctx):
    sym = ctx["company"]
    row = conn.execute(
        "SELECT COUNT(*) n, MAX(sort_date) newest FROM filings WHERE company = ?",
        (sym,)).fetchone()
    ctx["facts"]["filings_on_record"] = row["n"]
    ctx["facts"]["latest_filing_date"] = (row["newest"] or "")[:10]

    ev = conn.execute(
        "SELECT COUNT(*) n FROM governance_events WHERE symbol = ?", (sym,)).fetchone()
    ctx["facts"]["governance_events"] = ev["n"]

    name = conn.execute(
        "SELECT company_name FROM governance_events WHERE symbol = ? AND company_name != '' LIMIT 1",
        (sym,)).fetchone()
    if name:
        ctx["company_name"] = name["company_name"]

    ob = conn.execute(
        "SELECT COUNT(*) n FROM compliance_obligations "
        "WHERE symbol = ? AND status IN ('pending','unverified') AND due_date >= date('now')",
        (sym,)).fetchone()
    ctx["facts"]["upcoming_obligations"] = ob["n"]

    try:
        import governance_snapshots as gs
        d = gs.compute_delta(sym)
        if d:
            ctx["facts"]["latest_board_delta"] = {
                "from": d["before_period"], "to": d["after_period"],
                "summary": d["summary_line"],
                "before": d["before"], "after": d["after"],
            }
    except Exception:
        pass


def _enrich_event(ctx):
    import events_store as es
    ev = es.get_event(ctx["event_id"])
    if not ev:
        return
    ctx["company"] = ev["symbol"]
    ctx["company_name"] = ev.get("company_name")
    an = es.get_analysis(ctx["event_id"]) or {}
    arts = es.get_artifacts(ctx["event_id"])
    ctx["facts"]["event"] = {
        "label": ev["event_label"], "category": ev["category"],
        "detected": (ev.get("detected_at") or "")[:10],
        "effective_date": ev.get("effective_date"),
        "status": ev["status"], "headline": ev.get("headline"),
        "delta": (ev.get("delta") or {}).get("summary_line"),
        "evidence": ev.get("source_ref", {}).get("document"),
    }
    if an:
        ctx["regulation"] = an.get("regulatory_area")
        ctx["facts"]["assessment"] = {
            "finding": an.get("finding"),
            "why_it_matters": an.get("why_it_matters"),
            "risk_level": an.get("risk_level"),
            "mandate_ids": an.get("mandate_ids"),
            "recommended_actions": [a.get("title") for a in (an.get("recommended_actions") or [])],
        }
    ctx["facts"]["documents_prepared"] = [a["artifact_type"] for a in arts]


def _enrich_filing(conn, ctx):
    row = conn.execute(
        "SELECT id, company, title, description, date, tags, pdf_link, "
        "substr(COALESCE(raw_text,''), 1, 3000) AS excerpt "
        "FROM filings WHERE id = ?", (ctx["filing_id"],)).fetchone()
    if not row:
        return
    ctx["company"] = row["company"]
    ctx["document"] = row["title"]
    ctx["facts"]["filing"] = {
        "title": row["title"], "date": row["date"],
        "description": row["description"], "tags": row["tags"],
        "excerpt": row["excerpt"],
    }


def _enrich_director(conn, ctx):
    din = ctx["din"]
    rows = conn.execute(
        "SELECT symbol, quarter, board_json FROM governance_snapshots "
        "ORDER BY quarter_date DESC").fetchall()
    import json
    seen = {}
    for r in rows:
        for d in json.loads(r["board_json"] or "[]"):
            if (d.get("din") or "") == din:
                seen.setdefault(r["symbol"], {
                    "name": d.get("name"), "category": d.get("category"),
                    "appointed": d.get("appointed"), "latest_quarter": r["quarter"],
                })
                ctx["director"] = d.get("name")
    if seen:
        ctx["facts"]["director_positions"] = seen


def _enrich_portfolio(conn, ctx):
    syms = ctx.get("watchlist") or []
    if not syms:
        return
    ph = ",".join("?" * len(syms))
    ctx["facts"]["portfolio"] = {
        "companies": syms,
        "filings_on_record": conn.execute(
            f"SELECT COUNT(*) n FROM filings WHERE company IN ({ph})", syms).fetchone()["n"],
        "open_events": conn.execute(
            f"SELECT COUNT(*) n FROM governance_events WHERE symbol IN ({ph}) "
            f"AND status NOT IN ('approved','dismissed')", syms).fetchone()["n"],
        "obligations_due_60d": conn.execute(
            f"SELECT COUNT(*) n FROM compliance_obligations WHERE symbol IN ({ph}) "
            f"AND status IN ('pending','unverified') "
            f"AND due_date BETWEEN date('now') AND date('now','+60 day')", syms).fetchone()["n"],
    }


def _actions_for(ctx) -> list:
    """What Delta can actually do from here — used to keep the assistant's
    offers honest rather than promising features that don't exist."""
    base = ["explain_regulation", "compliance_checklist"]
    pt = ctx["page_type"]
    if pt in ("event", "filing"):
        base += ["draft_disclosure", "board_note", "internal_email"]
    if pt in ("company", "event"):
        base += ["governance_summary", "compare_quarters"]
    if pt in ("compliance", "command_center", "dashboard"):
        base += ["deadline_summary", "overdue_review"]
    if pt == "director":
        base += ["director_history", "governance_summary"]
    return base
