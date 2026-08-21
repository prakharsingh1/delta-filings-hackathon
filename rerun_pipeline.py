"""Bring every monitored company's events up to a full assessment.

Processes only what is missing, so it never spends credit re-doing work that
already has a good assessment:

  1. events with no assessment (or a failed one) -> Agent 2, then Agent 3
     where the assessed risk warrants documents
  2. optionally, fresh filing-sourced events for companies that have none

Run:  python3 rerun_pipeline.py              # fill in the gaps
      python3 rerun_pipeline.py --filings     # also add filing events
      python3 rerun_pipeline.py --force       # re-assess everything
"""

import sys
import time

import events_store as es
from database import get_connection

DEMO_USER = "demo-judge"
ACTION_MIN_RISK = "medium"


def _preflight():
    from agents.client import gemini_healthy
    h = gemini_healthy(force=True)
    if not h["ok"]:
        print(f"STOPPED — {h['error']}")
        print("Nothing was changed. Re-run once the provider is available.")
        return False
    print("Provider reachable. Starting.\n")
    return True


def pending_event_ids(force=False):
    conn = get_connection()
    if force:
        rows = conn.execute("SELECT id FROM governance_events ORDER BY id").fetchall()
    else:
        rows = conn.execute("""
            SELECT e.id FROM governance_events e
            LEFT JOIN event_analysis a ON a.event_id = e.id
            WHERE a.id IS NULL OR a.model IN ('', 'none')
            ORDER BY e.id
        """).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def assess(event_id):
    """Agent 2 then Agent 3. Returns a short status string."""
    from agents import intelligence as agent2, action as agent3

    ev = es.get_event(event_id)
    if not ev:
        return "missing"

    res = agent2.analyse_event(ev)
    if res["model"] == "none":
        return f"FAILED ({(res.get('error') or '')[:60]})"

    es.save_analysis(
        event_id, finding=res["finding"], why_it_matters=res["why_it_matters"],
        regulatory_area=res["regulatory_area"], mandate_ids=res["mandate_ids"],
        risk_level=res["risk_level"], confidence=res["confidence"],
        reasoning=res["reasoning"], recommended_actions=res["recommended_actions"],
        affected_areas=res["affected_areas"], model=res["model"],
        latency_ms=res["latency_ms"])
    es.log_audit("agent2", "analyze", event_id, ev["symbol"],
                 detail=f"Impact assessed — risk {res['risk_level']}, "
                        f"confidence {res['confidence']}%",
                 detail_obj={"mandate_ids": res["mandate_ids"],
                             "rejected_mandate_ids": res.get("rejected_mandate_ids", [])},
                 model=res["model"], latency_ms=res["latency_ms"])
    es.set_status(event_id, "analyzed", actor="agent2", actor_kind="agent",
                  note="Governance assessment completed")

    order = {"low": 0, "medium": 1, "high": 2}
    if order.get(res["risk_level"], 0) < order.get(ACTION_MIN_RISK, 0):
        return f"{res['risk_level']} ({res['confidence']}%) — no documents needed"

    arts = agent3.run(es.get_event(event_id), res, user_id=DEMO_USER)
    if arts:
        es.log_audit("agent3", "generate", event_id, ev["symbol"],
                     detail=f"Prepared {len(arts)} document(s): "
                            + ", ".join(a["artifact_type"] for a in arts),
                     model=arts[0].get("model", ""))
        es.set_status(event_id, "actioned", actor="agent3", actor_kind="agent",
                      note=f"{len(arts)} document(s) prepared")
    return f"{res['risk_level']} ({res['confidence']}%) — {len(arts)} documents"


def summary():
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) c FROM governance_events").fetchone()["c"]
    assessed = conn.execute(
        "SELECT COUNT(*) c FROM event_analysis WHERE model NOT IN ('', 'none')").fetchone()["c"]
    arts = conn.execute("SELECT COUNT(*) c FROM event_artifacts").fetchone()["c"]
    risks = dict(conn.execute(
        "SELECT risk_level, COUNT(*) FROM event_analysis "
        "WHERE model NOT IN ('','none') GROUP BY risk_level").fetchall())
    conn.close()
    print("\n" + "=" * 60)
    print(f"  events            {total}")
    print(f"  assessed          {assessed}")
    print(f"  unassessed        {total - assessed}")
    print(f"  documents         {arts}")
    print(f"  risk spread       {risks}")


if __name__ == "__main__":
    if not _preflight():
        sys.exit(1)

    if "--filings" in sys.argv:
        from seed_events import seed_filing_events
        seed_filing_events(per_company=5, run_analysis=True, run_action=True)

    ids = pending_event_ids(force="--force" in sys.argv)
    print(f"{len(ids)} events to assess\n")

    stopped = False
    for i, eid in enumerate(ids, 1):
        ev = es.get_event(eid)
        print(f"[{i}/{len(ids)}] event {eid} · {ev['symbol']} · {ev['event_label'][:34]}",
              flush=True)
        out = assess(eid)
        print(f"      {out}", flush=True)
        # Stop early rather than burn the whole list against a dead provider.
        if out.startswith("FAILED") and "RESOURCE_EXHAUSTED" in out:
            print("\nProvider exhausted mid-run — stopping so the rest can be "
                  "retried later. Everything completed so far is saved.")
            stopped = True
            break
        time.sleep(0.5)

    summary()
    sys.exit(2 if stopped else 0)
