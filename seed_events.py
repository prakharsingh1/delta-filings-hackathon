"""Populate the Intelligence Center from real, publicly disclosed data.

Two sources, both real:

  1. Governance deltas — board composition changes computed from each
     company's own SEBI Integrated Filing (Governance) XBRL. These carry
     genuine before/after numbers and named directors with DINs.

  2. Corporate filings — the NSE announcements already cached in `filings`,
     run through Agent 1.

Nothing here invents an event. No adverse event is attributed to any company
that did not disclose it themselves. Run with --reset to rebuild.
"""

import sys
import time

import events_store as es
import governance_snapshots as gs
from agents.pipeline import process_filing, process_governance_delta
from database import get_cached_filings, get_connection

COMPANIES = {
    "HINDUNILVR": "Hindustan Unilever Limited",
    "RELIANCE": "Reliance Industries Limited",
    "INFY": "Infosys Limited",
    "ITC": "ITC Limited",
    "NESTLEIND": "Nestle India Limited",
}

DEMO_USER = "demo-judge"


def reset():
    conn = get_connection()
    for t in ("event_artifacts", "event_analysis", "audit_log", "governance_events"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM user_drafts WHERE user_id = ?", (DEMO_USER,))
    conn.commit()
    conn.close()
    print("[SEED] cleared existing events, analyses, artifacts, audit log")


def seed_governance_deltas(run_analysis=True, run_action=True):
    total = 0
    for sym, name in COMPANIES.items():
        periods = gs.find_change_periods(sym)
        if not periods:
            print(f"[SEED] {sym}: no governance snapshots to compare")
            continue
        for p in periods:
            t = process_governance_delta(
                sym, p["before_period"], p["after_period"],
                company_name=name, user_id=DEMO_USER,
                run_analysis=run_analysis, run_action=run_action)
            if t:
                total += 1
                print(f"[SEED] {sym} {p['before_period']}->{p['after_period']}: "
                      f"event {t['event_id']} — {p['summary']}")
    print(f"[SEED] {total} governance-delta events")
    return total


def seed_filing_events(per_company=6, run_analysis=True, run_action=True,
                       action_min_risk="medium"):
    """Filing-sourced events. Documents are generated only at or above
    `action_min_risk` — routine periodic filings do not need a draft pack."""
    total = 0
    for sym, name in COMPANIES.items():
        filings = sorted(get_cached_filings(sym) or [],
                         key=lambda f: f.get("sort_date", ""), reverse=True)
        if not filings:
            print(f"[SEED] {sym}: no cached filings")
            continue
        done = 0
        for f in filings:
            if done >= per_company:
                break
            try:
                t = process_filing(dict(f), symbol=sym, company_name=name,
                                   user_id=DEMO_USER, scenario="live",
                                   run_analysis=run_analysis, run_action=run_action,
                                   action_min_risk=action_min_risk)
            except Exception as e:
                print(f"[SEED] {sym} filing {f.get('id')} failed: {str(e)[:120]}")
                continue
            if t and t.get("event_id"):
                done += 1
                total += 1
                step = t["steps"][-1]
                print(f"[SEED] {sym} filing {f.get('id')}: event {t['event_id']} — "
                      f"{step.get('result', '')[:60]} ({step.get('agent')})", flush=True)
        print(f"[SEED] {sym}: {done} filing events")
    print(f"[SEED] {total} filing events")
    return total


def summary():
    stats = es.get_center_stats(list(COMPANIES))
    print("\n" + "=" * 62)
    print("INTELLIGENCE CENTER STATE")
    print("=" * 62)
    for k in ("companies_monitored", "filings_processed", "events_detected",
              "pending_review", "risk_alerts", "agent_actions"):
        print(f"   {k:22s} {stats[k]}")
    print(f"   by_importance          {stats['by_importance']}")
    print(f"   by_status              {stats['by_status']}")


if __name__ == "__main__":
    es.init_events_db()
    gs.init_snapshots_db()

    if "--reset" in sys.argv:
        reset()

    no_ai = "--no-ai" in sys.argv
    if "--only-filings" not in sys.argv:
        seed_governance_deltas(run_analysis=not no_ai, run_action=not no_ai)
    if "--filings" in sys.argv or "--only-filings" in sys.argv:
        seed_filing_events(per_company=6, run_analysis=not no_ai,
                           run_action=not no_ai)
    summary()
