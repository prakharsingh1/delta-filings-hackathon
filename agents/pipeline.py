"""Pipeline orchestration — the CS team working a filing end to end.

    filing -> Agent 1 (detect) -> governance_events
           -> Agent 2 (assess) -> event_analysis
           -> Agent 3 (prepare) -> event_artifacts + user_drafts
           -> audit_log at every step
           -> human approval

Nothing here calls a model directly; it sequences the three agents and owns
persistence and the audit trail.
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import events_store as es
from . import action as agent3
from . import intelligence as agent2
from . import monitor as agent1

ACTOR_LABELS = {
    "agent1": "Compliance Monitoring Agent",
    "agent2": "Governance Intelligence Agent",
    "agent3": "Action Execution Agent",
}


def _dedupe_key(symbol: str, filing_id: Any, event_type: str) -> str:
    raw = f"{symbol}::{filing_id}::{event_type}"
    return hashlib.sha1(raw.encode()).hexdigest()[:24]


def process_filing(filing: Dict, symbol: str = "", company_name: str = "",
                   user_id: str = "", scenario: str = "live",
                   run_analysis: bool = True, run_action: bool = True,
                   force_surface: bool = False,
                   action_min_risk: Optional[str] = None) -> Optional[Dict]:
    """Run one filing through the full team. Returns a trace, or None if the
    filing produced nothing worth a reviewer's attention."""
    symbol = (symbol or filing.get("company") or "").upper()
    trace: Dict[str, Any] = {"symbol": symbol, "filing_id": filing.get("id"),
                             "steps": [], "event_id": None}

    # ── Agent 1 ──
    detection = agent1.analyse_filing(filing, symbol=symbol, company_name=company_name)
    trace["steps"].append({
        "agent": "agent1", "label": ACTOR_LABELS["agent1"], "action": "detect",
        "result": detection["event_label"], "importance": detection["importance"],
        "model": detection["model"], "latency_ms": detection["latency_ms"],
        "method": detection["method"],
    })

    if not force_surface and not agent1.should_surface(detection):
        trace["skipped"] = f"routine ({detection['event_type']})"
        return trace

    event_id = es.upsert_event(
        dedupe_key=_dedupe_key(symbol, filing.get("id"), detection["event_type"]),
        symbol=symbol,
        company_name=company_name or symbol,
        filing_id=filing.get("id"),
        source_kind="filing",
        event_type=detection["event_type"],
        event_label=detection["event_label"],
        category=detection["category"],
        title=detection["title"],
        headline=detection["headline"],
        detected_at=_filing_datetime(filing),
        effective_date=detection["effective_date"],
        importance=detection["importance"],
        requires_action=detection["requires_action"],
        delta=detection["delta"],
        entities=detection["entities"],
        source_ref=detection["source_ref"],
        scenario=scenario,
        agent_model=detection["model"],
    )
    trace["event_id"] = event_id

    es.log_audit("agent1", "detect", event_id, symbol,
                 detail=f"Filing classified as {detection['event_label']} "
                        f"({detection['importance']} importance)",
                 detail_obj={"method": detection["method"],
                             "confidence": detection["confidence"],
                             "has_delta": bool(detection["delta"])},
                 model=detection["model"], latency_ms=detection["latency_ms"])

    if not run_analysis:
        return trace

    # ── Agent 2 ──
    event = es.get_event(event_id)
    analysis = agent2.analyse_event(event)

    es.save_analysis(
        event_id,
        finding=analysis["finding"],
        why_it_matters=analysis["why_it_matters"],
        regulatory_area=analysis["regulatory_area"],
        mandate_ids=analysis["mandate_ids"],
        risk_level=analysis["risk_level"],
        confidence=analysis["confidence"],
        reasoning=analysis["reasoning"],
        recommended_actions=analysis["recommended_actions"],
        affected_areas=analysis["affected_areas"],
        model=analysis["model"],
        latency_ms=analysis["latency_ms"],
    )
    es.log_audit("agent2", "analyze", event_id, symbol,
                 detail=f"Impact assessed — risk {analysis['risk_level']}, "
                        f"confidence {analysis['confidence']}%",
                 detail_obj={"mandate_ids": analysis["mandate_ids"],
                             "mandates_retrieved": len(analysis.get("mandates") or []),
                             "rejected_mandate_ids": analysis.get("rejected_mandate_ids", []),
                             "prior_events_considered": len(analysis.get("memory", {}).get("prior_events", []))},
                 model=analysis["model"], latency_ms=analysis["latency_ms"])
    es.set_status(event_id, "analyzed", actor="agent2", actor_kind="agent",
                  note="Governance assessment completed")

    trace["steps"].append({
        "agent": "agent2", "label": ACTOR_LABELS["agent2"], "action": "analyze",
        "result": analysis["finding"], "risk": analysis["risk_level"],
        "confidence": analysis["confidence"], "mandates": analysis["mandate_ids"],
        "model": analysis["model"], "latency_ms": analysis["latency_ms"],
    })

    if not run_action:
        return trace

    # Skip document generation below the risk floor, when one is set.
    if action_min_risk:
        order = {"low": 0, "medium": 1, "high": 2}
        if order.get(analysis["risk_level"], 0) < order.get(action_min_risk, 0):
            trace["action_skipped"] = f"risk {analysis['risk_level']} < {action_min_risk}"
            return trace

    # ── Agent 3 ──
    event = es.get_event(event_id)
    artifacts = agent3.run(event, analysis, user_id=user_id)
    if artifacts:
        es.log_audit("agent3", "generate", event_id, symbol,
                     detail=f"Prepared {len(artifacts)} document(s): "
                            + ", ".join(a["artifact_type"] for a in artifacts),
                     detail_obj={"artifacts": [a["artifact_type"] for a in artifacts],
                                 "draft_ids": [a.get("draft_id") for a in artifacts]},
                     model=artifacts[0].get("model", ""))
        es.set_status(event_id, "actioned", actor="agent3", actor_kind="agent",
                      note=f"{len(artifacts)} document(s) prepared")
        trace["steps"].append({
            "agent": "agent3", "label": ACTOR_LABELS["agent3"], "action": "generate",
            "result": ", ".join(a["artifact_type"] for a in artifacts),
            "count": len(artifacts),
            "model": artifacts[0].get("model", ""),
        })

    return trace


def _filing_datetime(filing: Dict) -> str:
    """Prefer the filing's own date so the timeline reflects when things
    happened, not when we happened to process them."""
    sd = (filing.get("sort_date") or "").strip()
    if sd:
        return sd if len(sd) > 10 else sd + "T00:00:00"
    return datetime.now().isoformat(timespec="seconds")


def process_symbol(symbol: str, limit: int = 10, user_id: str = "",
                   company_name: str = "", **kw) -> List[Dict]:
    """Run the most recent filings for one company through the team."""
    from database import get_cached_filings

    filings = get_cached_filings(symbol) or []
    filings = sorted(filings, key=lambda f: f.get("sort_date", ""), reverse=True)[:limit]

    traces = []
    for f in filings:
        try:
            t = process_filing(dict(f), symbol=symbol, company_name=company_name or symbol,
                               user_id=user_id, **kw)
            if t:
                traces.append(t)
        except Exception as e:
            print(f"[PIPELINE] {symbol} filing {f.get('id')} failed: {str(e)[:200]}")
    return traces


# ── Governance-delta sourced events ───────────────────────

def process_governance_delta(symbol: str, before_q: str, after_q: str,
                             company_name: str = "", user_id: str = "",
                             run_analysis: bool = True, run_action: bool = True) -> Optional[Dict]:
    """Create an event from a real board-composition change between two
    stored governance snapshots.

    This is the DELTA path: the before/after here is parsed from the company's
    own SEBI filing, not inferred by a model. It is what makes "8 directors ->
    7 directors" a fact on screen rather than a claim.
    """
    import governance_snapshots as gs

    symbol = symbol.upper()
    delta = gs.compute_delta(symbol, before_q, after_q)
    if not delta or not delta["has_board_change"]:
        return None

    joined = delta["directors_joined"]
    departed = delta["directors_departed"]

    # Event type and importance follow what actually moved.
    if departed and any("independent" in (d.get("category") or "").lower() for d in departed):
        event_type, label = "BOARD_CHANGE", "Independent Director Cessation"
        importance = "high"
    elif departed:
        event_type, label = "BOARD_CHANGE", "Director Cessation"
        importance = "high"
    elif joined:
        event_type, label = "BOARD_CHANGE", "Director Appointment"
        importance = "medium"
    elif delta.get("committee_changes"):
        event_type, label = "BOARD_CHANGE", "Board Committee Composition Change"
        importance = "medium"
    else:
        event_type, label = "BOARD_CHANGE", "Board Role Change"
        importance = "low"

    entities = {
        "people": (
            [{"name": d["name"], "role": d.get("category", ""), "din": d.get("din", ""),
              "change": "appointed", "date": d.get("appointed", "")} for d in joined]
            + [{"name": d["name"], "role": d.get("category", ""), "din": d.get("din", ""),
                "change": "ceased", "date": d.get("ceased", "")} for d in departed]
        ),
        "organisations": [company_name or symbol],
        "amounts": [],
    }

    effective = ""
    for d in departed + joined:
        cand = d.get("ceased") or d.get("appointed") or ""
        if cand and (not effective or cand > effective):
            effective = cand

    event_id = es.upsert_event(
        dedupe_key=_dedupe_key(symbol, f"gov:{before_q}->{after_q}", event_type),
        symbol=symbol,
        company_name=company_name or symbol,
        filing_id=None,
        source_kind="governance_report",
        event_type=event_type,
        event_label=label,
        category="Board Governance",
        title=f"Corporate Governance Report — quarter ended {after_q}",
        headline=delta["summary_line"],
        detected_at=(gs._quarter_to_date(after_q) or "") + "T09:00:00",
        effective_date=effective,
        importance=importance,
        requires_action=True,
        delta=delta,
        entities=entities,
        source_ref={
            "document": f"SEBI Integrated Filing (Governance) — {after_q}",
            "url": delta.get("after_source_url", ""),
            "comparison_url": delta.get("before_source_url", ""),
            "date": after_q,
            "page": None,
            "quote": "",
            "basis": "XBRL board composition table, compared against "
                     f"the quarter ended {before_q}",
        },
        scenario="live",
        agent_model="deterministic:governance_snapshot",
    )

    es.log_audit("agent1", "detect", event_id, symbol,
                 detail=f"Board composition change detected: {delta['summary_line']}",
                 detail_obj={"method": "governance_snapshot_delta",
                             "before_period": before_q, "after_period": after_q,
                             "joined": len(joined), "departed": len(departed)},
                 model="deterministic")

    trace = {"symbol": symbol, "event_id": event_id, "source": "governance_delta",
             "steps": [{"agent": "agent1", "label": ACTOR_LABELS["agent1"],
                        "action": "detect", "result": label,
                        "importance": importance, "model": "deterministic",
                        "latency_ms": 0, "method": "governance_snapshot_delta"}]}

    if not run_analysis:
        return trace

    event = es.get_event(event_id)
    analysis = agent2.analyse_event(event)
    es.save_analysis(
        event_id, finding=analysis["finding"], why_it_matters=analysis["why_it_matters"],
        regulatory_area=analysis["regulatory_area"], mandate_ids=analysis["mandate_ids"],
        risk_level=analysis["risk_level"], confidence=analysis["confidence"],
        reasoning=analysis["reasoning"], recommended_actions=analysis["recommended_actions"],
        affected_areas=analysis["affected_areas"], model=analysis["model"],
        latency_ms=analysis["latency_ms"])
    es.log_audit("agent2", "analyze", event_id, symbol,
                 detail=f"Impact assessed — risk {analysis['risk_level']}, "
                        f"confidence {analysis['confidence']}%",
                 detail_obj={"mandate_ids": analysis["mandate_ids"],
                             "mandates_retrieved": len(analysis.get("mandates") or []),
                             "rejected_mandate_ids": analysis.get("rejected_mandate_ids", [])},
                 model=analysis["model"], latency_ms=analysis["latency_ms"])
    es.set_status(event_id, "analyzed", actor="agent2", actor_kind="agent",
                  note="Governance assessment completed")
    trace["steps"].append({"agent": "agent2", "label": ACTOR_LABELS["agent2"],
                           "action": "analyze", "result": analysis["finding"],
                           "risk": analysis["risk_level"], "confidence": analysis["confidence"],
                           "mandates": analysis["mandate_ids"], "model": analysis["model"],
                           "latency_ms": analysis["latency_ms"]})

    if not run_action:
        return trace

    event = es.get_event(event_id)
    artifacts = agent3.run(event, analysis, user_id=user_id)
    if artifacts:
        es.log_audit("agent3", "generate", event_id, symbol,
                     detail=f"Prepared {len(artifacts)} document(s): "
                            + ", ".join(a["artifact_type"] for a in artifacts),
                     detail_obj={"artifacts": [a["artifact_type"] for a in artifacts]},
                     model=artifacts[0].get("model", ""))
        es.set_status(event_id, "actioned", actor="agent3", actor_kind="agent",
                      note=f"{len(artifacts)} document(s) prepared")
        trace["steps"].append({"agent": "agent3", "label": ACTOR_LABELS["agent3"],
                               "action": "generate",
                               "result": ", ".join(a["artifact_type"] for a in artifacts),
                               "count": len(artifacts), "model": artifacts[0].get("model", "")})
    return trace


# ── Morning brief ─────────────────────────────────────────

def generate_morning_brief(symbols: List[str], hours: int = 24) -> Dict:
    """"Here is what changed overnight." The first screen a CS should see.

    Pure aggregation over what the agents already produced — no model call, so
    it renders instantly and can never contradict the stored analysis.
    """
    now = datetime.now()
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")

    events = es.list_events(symbols=symbols, limit=500)
    recent = [e for e in events if (e.get("created_at") or "") >= cutoff]

    # An empty overnight window is normal; fall back to the open queue so the
    # brief is still useful rather than blank.
    window_label = f"last {hours} hours"
    if not recent:
        recent = [e for e in events if e.get("status") not in es.CLOSED_STATUSES]
        window_label = "open items"

    from database import get_connection
    conn = get_connection()
    if symbols:
        ph = ",".join("?" * len(symbols))
        filings_processed = conn.execute(
            f"SELECT COUNT(*) c FROM filings WHERE company IN ({ph})",
            [s.upper() for s in symbols]).fetchone()["c"]
    else:
        filings_processed = conn.execute("SELECT COUNT(*) c FROM filings").fetchone()["c"]
    conn.close()

    urgent, watch, informational = [], [], []
    for e in recent:
        analysis = es.get_analysis(e["id"]) or {}
        assessed = bool(analysis.get("model") not in (None, "", "none"))
        item = {
            "event_id": e["id"],
            "symbol": e["symbol"],
            "company_name": e.get("company_name") or e["symbol"],
            "event_label": e["event_label"],
            "headline": e.get("headline", ""),
            "importance": e["importance"],
            "status": e["status"],
            "is_demo_scenario": e.get("is_demo_scenario", False),
            "assessed": assessed,
            "risk_level": analysis.get("risk_level", e["importance"]) if assessed else "unassessed",
            "confidence": analysis.get("confidence", 0),
            "finding": analysis.get("finding", ""),
            "top_action": (analysis.get("recommended_actions") or [{}])[0].get("title", ""),
            "artifact_count": len(es.get_artifacts(e["id"])),
        }
        risk = item["risk_level"]
        if risk == "high" and e["status"] not in es.CLOSED_STATUSES:
            urgent.append(item)
        elif risk == "medium" and e["status"] not in es.CLOSED_STATUSES:
            watch.append(item)
        else:
            informational.append(item)

    requires_attention = len(urgent) + len(watch)
    if urgent:
        headline = (f"{len(urgent)} event{'s' if len(urgent) != 1 else ''} "
                    f"need{'' if len(urgent) != 1 else 's'} your attention today.")
    elif watch:
        headline = f"{len(watch)} item{'s' if len(watch) != 1 else ''} to review. Nothing urgent."
    else:
        headline = "No governance events require action. All monitored companies are clear."

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "greeting": _greeting(now),
        "window": window_label,
        "headline": headline,
        "stats": {
            "companies_monitored": len(symbols),
            "filings_processed": filings_processed,
            "events_detected": len(recent),
            "requires_attention": requires_attention,
            "documents_prepared": sum(i["artifact_count"] for i in urgent + watch + informational),
        },
        "urgent": urgent,
        "watch": watch,
        "informational": informational[:10],
    }


def _greeting(now: datetime) -> str:
    h = now.hour
    if h < 12:
        return "Good morning"
    if h < 17:
        return "Good afternoon"
    return "Good evening"
