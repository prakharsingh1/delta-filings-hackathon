"""End-to-end pipeline verification.

Runs a real NSE filing through all three agents and prints the full trace.

With a valid GEMINI_API_KEY the agents call Gemini for real. Without one,
pass --stub to inject representative model responses so the surrounding
wiring — JSON handling, the mandate-citation gate, the evidence-grounding
gate, artifact persistence, draft linkage and the audit trail — is still
exercised end to end.
"""

import json
import sys
import textwrap

import events_store as es
from database import get_connection, get_filing_by_id

STUB = "--stub" in sys.argv
FILING_ID = 217  # NESTLEIND "Change in Management" — real filing, 15k chars of text


def rule(title=""):
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


def wrap(text, indent="    ", width=74):
    return textwrap.indent(textwrap.fill(str(text), width), indent)


# ── Stub model responses ──────────────────────────────────
# Deliberately adversarial: the Agent 2 stub cites two regulations that are
# NOT in the retrieved candidate set, and the Agent 1 stub returns a quote
# that does NOT appear in the source. Both must be rejected.

def install_stubs():
    from agents import client

    real_quote_marker = {"value": ""}

    def fake_call_json(prompt, *, role="monitor", model=None, max_output_tokens=2000,
                       temperature=0.2, thinking=False):
        m = {"monitor": "gemini-2.5-flash", "intelligence": "gemini-2.5-pro",
             "action": "gemini-2.5-flash"}[role]

        if role == "monitor":
            data = {
                "event_type": "KMP_CHANGE",
                "headline": "The company has intimated a change in its Whole-Time Director and Chief Financial Officer.",
                "importance": "high",
                "requires_action": True,
                "effective_date": "2026-01-16",
                "entities": {
                    "people": [{"name": "(name per filing)", "role": "Whole-Time Director & CFO",
                                "din": "", "change": "appointed"}],
                    "organisations": ["Nestle India Limited"],
                    "amounts": [],
                },
                # INTENTIONALLY FABRICATED — must be rejected by the grounding gate.
                "evidence_quote": "The Board has unanimously resolved to terminate the incumbent with immediate effect.",
                "confidence": 88,
            }
            return {"ok": True, "data": data, "model": m, "latency_ms": 1840, "error": ""}

        if role == "intelligence":
            data = {
                "finding": "Nestle India has intimated a change in Key Managerial Personnel "
                           "(Whole-Time Director & CFO) under Regulation 30.",
                "why_it_matters": "A KMP change triggers a time-bound disclosure obligation to the "
                                  "stock exchanges and must be reflected in the next quarterly "
                                  "corporate governance report. The CFO is a designated KMP under "
                                  "the Companies Act, so the corresponding MCA filing is also "
                                  "engaged. No prior KMP change appears in this company's recent "
                                  "monitored history, so this is an isolated transition rather "
                                  "than a pattern.",
                "regulatory_area": "SEBI LODR — Material Events & Board Composition",
                # Two valid ids, two invented ones that must be stripped.
                "mandate_ids": ["LODR-REG30", "MCA-DIR12", "LODR-REG99", "SEBI-FAKE-1"],
                "affected_areas": ["Key Managerial Personnel record", "Quarterly CG report (Reg 27)",
                                   "MCA director/KMP register"],
                "risk_level": "high",
                "confidence": 91,
                "reasoning": [
                    "The filing is titled 'Change in Management' and the text intimates a WTD & CFO change.",
                    "Reg 30 read with Schedule III Part A treats KMP changes as deemed material, requiring disclosure within 24 hours.",
                    "The CFO is a KMP under s.2(51) of the Companies Act, engaging the DIR-12 / MR-1 filing track.",
                    "Company memory shows no comparable change in the monitored window, so no escalating pattern.",
                ],
                "recommended_actions": [
                    {"title": "Confirm the exchange disclosure was filed within 24 hours",
                     "detail": "Verify timestamp of the Reg 30 intimation against the board resolution time.",
                     "owner": "Company Secretary", "urgency": "immediate", "artifact": "disclosure_letter"},
                    {"title": "Update the KMP register and MCA filing track",
                     "detail": "Prepare DIR-12 / MR-1 as applicable within statutory timelines.",
                     "owner": "Compliance Officer", "urgency": "this_week", "artifact": "compliance_checklist"},
                    {"title": "Reflect the change in the next quarterly CG report",
                     "detail": "Ensure Reg 27 governance report for the quarter carries the updated KMP details.",
                     "owner": "Company Secretary", "urgency": "this_month", "artifact": "board_note"},
                ],
            }
            return {"ok": True, "data": data, "model": m, "latency_ms": 6120, "error": ""}

        data = {
            "board_note": {
                "title": "Note: Change in Key Managerial Personnel",
                "body": "The Board is informed that the Company has intimated a change in its "
                        "Whole-Time Director and Chief Financial Officer with effect from "
                        "[DATE TO BE CONFIRMED]. Under Regulation 30 of the SEBI (LODR) "
                        "Regulations, 2015 read with Schedule III, a change in Key Managerial "
                        "Personnel is a deemed material event requiring disclosure to the stock "
                        "exchanges within twenty-four hours of the decision. The corresponding "
                        "intimation has been filed. The Board is requested to note the change and "
                        "to authorise the Company Secretary to make consequential filings with the "
                        "Registrar of Companies within the prescribed timelines.",
            },
            "internal_email": {
                "subject": "Action required — KMP change disclosure and consequential filings",
                "body": "Team, the Company has intimated a change in the Whole-Time Director & CFO "
                        "position. Please (1) confirm the Regulation 30 intimation timestamp is "
                        "within 24 hours of the board decision, (2) prepare the consequential MCA "
                        "filing, and (3) flag the change for inclusion in the next quarterly "
                        "corporate governance report. Target: end of this week.",
            },
            "compliance_checklist": {
                "title": "KMP Change — Compliance Checklist",
                "items": [
                    {"task": "Verify Reg 30 intimation filed within 24 hours of board decision",
                     "regulation": "LODR-REG30", "owner": "Company Secretary",
                     "due": "Within 24 hours of the event"},
                    {"task": "File DIR-12 with the Registrar of Companies",
                     "regulation": "MCA-DIR12", "owner": "Compliance Officer",
                     "due": "Within 30 days"},
                    {"task": "Update statutory KMP register",
                     "regulation": "", "owner": "Company Secretary", "due": "Immediately"},
                ],
            },
        }
        return {"ok": True, "data": data, "model": m, "latency_ms": 2410, "error": ""}

    # Patch every module that imported call_json by reference.
    from agents import monitor, intelligence, action
    for mod in (monitor, intelligence, action):
        mod.call_json = fake_call_json
        mod.gemini_available = lambda: True

    # Agent 3's disclosure goes through draft_engine, which has its own client.
    import draft_engine
    draft_engine._gemini_call = lambda api_key, prompt, max_output_tokens=1500, \
        model="gemini-2.5-flash", json_mode=False: json.dumps({
            "subject": "Intimation of change in Key Managerial Personnel under Regulation 30 of "
                       "SEBI (LODR) Regulations, 2015",
            "body": "To,\nThe Manager, Listing Department\nBSE Limited / National Stock Exchange of India Limited\n\n"
                    "Dear Sir/Madam,\n\nSub: Intimation of change in Key Managerial Personnel\n\n"
                    "Pursuant to Regulation 30 of the SEBI (Listing Obligations and Disclosure "
                    "Requirements) Regulations, 2015 read with Schedule III Part A, we wish to "
                    "inform you that the Board of Directors has approved a change in the position "
                    "of Whole-Time Director and Chief Financial Officer of the Company with effect "
                    "from [DATE TO BE CONFIRMED].\n\nThe disclosure required under SEBI Circular "
                    "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2023/123 is annexed.\n\nYou are requested to take "
                    "the same on record.\n\nThanking you,\nYours faithfully,\nFor Nestle India Limited\n\n"
                    "Company Secretary & Compliance Officer",
            "annexures": [{"title": "Annexure A — Details required under SEBI Circular",
                           "body": "Name, designation, date of appointment, brief profile."}],
        })
    from agents import client as agent_client
    agent_client.get_api_key = lambda: "STUB-KEY"
    action.gemini_available = lambda: True
    return real_quote_marker


# ── Run ───────────────────────────────────────────────────

def main():
    es.init_events_db()

    # Clean slate for this filing so the trace is unambiguous.
    conn = get_connection()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM governance_events WHERE filing_id = ?", (FILING_ID,))]
    for eid in ids:
        conn.execute("DELETE FROM event_analysis WHERE event_id=?", (eid,))
        conn.execute("DELETE FROM event_artifacts WHERE event_id=?", (eid,))
        conn.execute("DELETE FROM audit_log WHERE event_id=?", (eid,))
    conn.execute("DELETE FROM governance_events WHERE filing_id = ?", (FILING_ID,))
    conn.commit()
    conn.close()

    if STUB:
        install_stubs()

    from agents.pipeline import process_filing

    f = dict(get_filing_by_id(FILING_ID))

    rule("STEP 0 — SOURCE FILING (real, fetched from NSE)")
    print(f"    filing_id : {f['id']}")
    print(f"    company   : {f['company']}")
    print(f"    title     : {f['title']}")
    print(f"    date      : {f['date']}")
    print(f"    raw_text  : {len(f.get('raw_text') or '')} chars")
    print(f"    pdf_link  : {(f.get('pdf_link') or '')[:70]}")
    print(f"\n    mode      : {'STUBBED MODEL RESPONSES' if STUB else 'LIVE GEMINI'}")

    trace = process_filing(f, symbol="NESTLEIND", company_name="Nestle India Limited",
                           user_id="demo-judge", scenario="live")

    eid = trace["event_id"]
    if not eid:
        print("\n    !! no event created:", trace.get("skipped"))
        return

    ev = es.get_event(eid)
    an = es.get_analysis(eid)
    arts = es.get_artifacts(eid)
    audit = es.get_audit_trail(eid)

    rule("STEP 1 — AGENT 1 (Compliance Monitoring Agent) -> governance_events")
    print(f"    event_id        : {ev['id']}")
    print(f"    event_type      : {ev['event_type']}")
    print(f"    event_label     : {ev['event_label']}")
    print(f"    category        : {ev['category']}")
    print(f"    importance      : {ev['importance']}   requires_action: {ev['requires_action']}")
    print(f"    effective_date  : {ev['effective_date'] or '(not stated)'}")
    print(f"    status          : {ev['status']}")
    print(f"    detection model : {ev['agent_model']}")
    print(f"    headline        :")
    print(wrap(ev['headline'], "      "))
    print(f"\n    entities        : {json.dumps(ev['entities'])[:200]}")
    print(f"    delta           : {json.dumps(ev['delta'])[:200] or '(none)'}")
    sr = ev['source_ref']
    print(f"\n    EVIDENCE:")
    print(f"      document      : {sr.get('document')}")
    print(f"      page          : {sr.get('page')}")
    print(f"      quote         : {sr.get('quote') or '(none retained)'}")
    if sr.get("quote_rejected"):
        print(f"      >> GROUNDING GATE: model quote was NOT found in the source document")
        print(f"      >> and was DISCARDED rather than shown as evidence.")

    rule("STEP 2 — AGENT 2 (Governance Intelligence Agent) -> event_analysis")
    print(f"    model           : {an['model']}   latency: {an['latency_ms']}ms")
    print(f"    risk_level      : {an['risk_level']}   confidence: {an['confidence']}%")
    print(f"    regulatory_area : {an['regulatory_area']}")
    print(f"\n    FINDING:")
    print(wrap(an['finding'], "      "))
    print(f"\n    WHY IT MATTERS:")
    print(wrap(an['why_it_matters'], "      "))
    print(f"\n    REASONING CHAIN:")
    for i, r in enumerate(an['reasoning'], 1):
        print(f"      {i}. " + textwrap.fill(r, 70, subsequent_indent="         "))
    print(f"\n    CITED MANDATES (validated against compliance.SEBI_MANDATES):")
    from agents.intelligence import resolve_mandates
    for m in resolve_mandates(an['mandate_ids']):
        print(f"      [{m['id']:16s}] {m['regulation']}")
        print(f"       {' ' * 17} {m['name']}")
    ag2 = next((a for a in audit if a['action'] == 'analyze'), None)
    if ag2:
        d = json.loads(ag2['detail_json'] or '{}')
        print(f"\n    RETRIEVAL: {d.get('mandates_retrieved')} candidates offered to the model")
        if d.get('rejected_mandate_ids'):
            print(f"    >> CITATION GATE: model also returned {d['rejected_mandate_ids']}")
            print(f"    >> which are NOT in the mandate library. REJECTED before saving.")
    print(f"\n    AFFECTED AREAS : {', '.join(an['affected_areas'])}")
    print(f"\n    RECOMMENDED ACTIONS:")
    for i, a in enumerate(an['recommended_actions'], 1):
        print(f"      {i}. [{a['urgency']:<10}] {a['title']}")
        print(f"         owner: {a['owner']}  ->  artifact: {a['artifact']}")

    rule("STEP 3 — AGENT 3 (Action Execution Agent) -> event_artifacts + user_drafts")
    if not arts:
        print("    (no artifacts generated)")
    for a in arts:
        print(f"\n    [{a['artifact_type']}]  {a['title'][:64]}")
        print(f"      artifact_id: {a['id']}   draft_id: {a['draft_id'] or '-'}"
              f"   filing_type: {a['filing_type'] or '-'}")
        body = a['body'][:340].replace("\n", "\n      ")
        print(f"      ---")
        print(f"      {body}...")

    rule("STEP 4 — AUDIT TRAIL (audit_log)")
    for a in audit:
        ts = a['ts'][11:19]
        actor = a['actor']
        from agents.pipeline import ACTOR_LABELS
        label = ACTOR_LABELS.get(actor, actor)
        lat = f"  ({a['latency_ms']}ms)" if a['latency_ms'] else ""
        print(f"    {ts}  {label:<34} {a['action']:<18}{lat}")
        print(f"              {a['detail']}")

    rule("STEP 5 — HUMAN APPROVAL")
    es.set_status(eid, "approved", actor="demo@deltafilings.in",
                  note="Reviewed by Company Secretary — disclosure confirmed filed")
    ev2 = es.get_event(eid)
    print(f"    status: {ev['status']} -> {ev2['status']}")
    for a in es.get_audit_trail(eid)[-1:]:
        print(f"    {a['ts'][11:19]}  {a['actor']:<34} {a['action']}")
        print(f"              {a['detail']}")

    rule("DATABASE STATE")
    conn = get_connection()
    for t in ("governance_events", "event_analysis", "event_artifacts", "audit_log"):
        n = conn.execute(f"SELECT COUNT(*) c FROM {t} WHERE " +
                         ("id" if t == "governance_events" else "event_id") + " = ?",
                         (eid,)).fetchone()["c"]
        print(f"    {t:<20} rows for this event: {n}")
    nd = conn.execute("SELECT COUNT(*) c FROM user_drafts WHERE user_id='demo-judge'").fetchone()["c"]
    print(f"    {'user_drafts':<20} rows for demo user : {nd}")
    conn.close()

    rule("MORNING BRIEF")
    from agents.pipeline import generate_morning_brief
    brief = generate_morning_brief(["NESTLEIND", "HINDUNILVR", "RELIANCE", "INFOSYS", "ITC"])
    print(f"    {brief['greeting']}. {brief['headline']}")
    print(f"    window: {brief['window']}")
    print(f"    stats : {json.dumps(brief['stats'])}")
    for bucket in ("urgent", "watch"):
        for i in brief[bucket]:
            print(f"    [{bucket.upper():<5}] {i['symbol']:<12} {i['event_label']:<34} "
                  f"risk={i['risk_level']:<6} docs={i['artifact_count']}")
    print()


if __name__ == "__main__":
    main()
