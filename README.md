# Delta Filings — AI Company Secretary

**Track 1 — Agentic AI in Financial Services**
Focus area: *Corporate governance & secretarial compliance operations for Indian listed companies* (proposed bottleneck, per Section 3 of the brief).

An agentic system that watches corporate filings, works out what changed, decides why it matters against a retrieved regulation library, drafts the documents the change requires, and stops for a human to approve.

---

## The bottleneck

A Company Secretary at a listed Indian company is accountable for every SEBI LODR, Companies Act, PIT and SAST obligation the company carries. The work is high-volume, deadline-driven and personally liable — Reg 30 material events must reach the exchanges within 24 hours, DIR-12 within 30 days.

Today that means a team manually reading exchange announcements, cross-checking them against a mental map of the regulations, and drafting the same letters by hand. Delta does the reading, the mapping and the first draft; the CS does the deciding.

---

## Running it

```bash
pip install -r requirements.txt

# Demo mode: no sign-in, no payment wall, all data served from cache
DELTA_DEMO_FREEZE=1 DELTA_SKIP_PRELOAD=1 DELTA_DEMO_MODE=1 python3 main.py
# → http://localhost:8080/demo
```

| Variable | Purpose |
|---|---|
| `DELTA_DEMO_MODE=1` | Provisions a read-only reviewer session; skips auth and the payment gate |
| `DELTA_DEMO_FREEZE=1` | Serves every external-data endpoint from the local cache and makes **no outbound calls** |
| `DELTA_SKIP_PRELOAD=1` | Skips the network-heavy startup backfill |
| `GEMINI_API_KEY` | Required for the agents; without it the system degrades to its deterministic layer |
| `FIRECRAWL_API_KEY` | Used only for sources that block direct server-side requests (SEBI, company sites) |

To refresh the caches with the network available: `python3 prewarm_demo.py`

---

## Architecture

```
NSE filing / quarterly governance report
        │
        ▼
┌───────────────────────┐
│ 1. Monitoring Agent   │  gemini-2.5-flash
│    "What happened?"   │  16-entry closed taxonomy. Rules classify first;
└───────────┬───────────┘  the model only interprets the filing text.
            ▼
┌───────────────────────┐
│ 2. Delta Engine       │  deterministic — no model
│    "What changed?"    │  before/after board composition parsed from the
└───────────┬───────────┘  company's own XBRL governance filings
            ▼
┌───────────────────────┐
│ 3. Intelligence Agent │  gemini-2.5-pro
│    "Why does it       │  retrieves candidate mandates from a 47-entry
│     matter?"          │  library, then reasons over ONLY those
└───────────┬───────────┘
            ▼
┌───────────────────────┐
│ 4. Action Agent       │  gemini-2.5-flash
│    "What do we file?" │  drafts disclosure / board note / email /
└───────────┬───────────┘  checklist via the existing 101-type draft engine
            ▼
┌───────────────────────┐
│ 5. HUMAN APPROVAL     │  Approve · Dismiss · Escalate
└───────────┬───────────┘  nothing is ever filed automatically
            ▼
      append-only audit trail
```

Two further agents sit alongside the pipeline:

- **Portfolio Briefing** (`agents/briefing.py`) — reasons *across* companies rather than one event at a time. On the current dataset it surfaced that LODR Reg 30 is cited in 22 of 24 open events, and a board-refreshment pattern spanning four companies.
- **Delta Copilot** (`copilot/`) — a context-aware assistant on every screen. It receives the page, company and document being viewed, so the user never re-states context.

### Guardrails (Section 3 requirement)

Both mechanisms the brief asks for are implemented, and both are enforced in **code**, not only in prompts:

**Agent-catches-agent (self-correction)**

| Gate | What it does | Observed |
|---|---|---|
| Citation gate | Agent 2's regulation IDs are validated against the retrieved candidate set; anything not offered is discarded | Rejected `LODR-REG99`, `SEBI-FAKE-1` in testing; **0 invalid citations** across 59 stored |
| Evidence gate | A "verbatim" quote is checked against the source document and dropped if absent | Fired on real data — 4 quotes discarded, 10 grounded quotes kept |
| Unsupported-claim scanner | Flags reason-attributions the source never stated | Caught the drafter writing *"ceased … due to completion of his tenure"* about a named director when the filing said no such thing |
| Confidence cap | Hard-capped at 95% in code | A compliance tool must never present an AI judgement as certain |

**Human-in-the-loop**

Every event carries a status lifecycle — `new → analyzed → actioned → approved | dismissed | escalated`. Documents are prepared but never sent. Each decision is written to `audit_log` with reviewer identity and timestamp.

---

## What's real vs. what's mocked

This matters more than the demo, so it is stated precisely.

### Real

| | |
|---|---|
| **6,276 corporate filings** | Pulled live from NSE's announcements API for 5 listed FMCG issuers, spanning ~2004–2026 |
| **35 quarterly governance snapshots** | Parsed from each company's own SEBI Integrated Filing (Governance) XBRL. Board deltas — "9 directors → 8", named directors with DINs — are computed from these, not generated |
| **47-mandate regulation library** | SEBI LODR / PIT / SAST, Companies Act, FEMA and tax obligations with real regulation references, section refs, circular numbers, penalties and due-day arithmetic. Hand-built, pre-dates this event |
| **305 compliance obligations** | Generated from that library against real quarter-ends |
| **53 governance events, 52 AI assessments, 124 documents** | Produced by the pipeline over the real filings above |
| **Companies** | HINDUNILVR, ITC, NESTLEIND, BRITANNIA, DABUR — real issuers, real public disclosures |

### Mocked / simulated

| | |
|---|---|
| **Authentication** | Demo mode provisions a session directly. Production uses Supabase auth + a Razorpay payment gate, both already built |
| **Response cache** | For the demo, external endpoints are served from a local snapshot so nothing depends on venue wifi or NSE uptime. `DELTA_DEMO_FREEZE=0` restores live fetching |
| **Deployment** | Runs locally. The production instance on Railway is the pre-hackathon version |

### What would have to be true in production

1. **A filing feed with an SLA.** We scrape NSE's public announcements API. NSE rate-limits and 403s server-side callers; a production system needs a licensed data feed or an exchange API agreement.
2. **MCA / TRACES / RBI coverage.** Delta reads exchange announcements. MCA forms (MSME-1, PAS-6, DPT-3), TDS returns and FEMA filings never appear there. The product currently routes those to the right portal rather than pretending to verify them — closing that loop needs MCA21 data access.
3. **Model spend at scale.** Agent 2 runs on Pro at ~24s and real token cost per event. Per-company economics need measuring before pricing.
4. **A professional liability position.** Delta prepares and cites; a qualified CS signs. That boundary is a product decision as much as a legal one and needs counsel before any customer relies on it.

### Deliberate constraints on real companies

Every company shown is a real listed issuer, so the system is built not to make claims their filings do not support:

- No adverse event is attributed to any company unless it disclosed it.
- An obligation with no matched filing is **never** shown as a missed deadline. 38 of 55 such items are filed with MCA/TRACES/RBI where no exchange record exists; the UI says where to confirm instead. Only a `missed` status — requiring two independent sources to confirm a non-filing — renders as overdue. Currently **0 obligations** are claimed overdue.
- Cross-checks against the public web return *"no public confirmation found"*, never *"not filed"*.

---

## Repository layout

```
main.py                  Flask app, ~100 routes
agents/
  client.py              one Gemini client; per-role model assignment
  monitor.py             Agent 1 — taxonomy + rule pass + extraction
  intelligence.py        Agent 2 — mandate retrieval + reasoning
  action.py              Agent 3 — delegates to draft_engine
  briefing.py            cross-portfolio reasoning
  pipeline.py            orchestration + audit trail
copilot/                 context manager, suggestions, retrieval, handler
events_store.py          governance_events / event_analysis / event_artifacts / audit_log
governance_snapshots.py  XBRL board composition capture + delta engine
compliance.py            47-mandate library, obligations, scoring   (pre-existing)
draft_engine.py          101 filing types, PDF/DOCX render           (pre-existing)
verification_routes.py   which authority each obligation is filed with
web_crosscheck.py        public-record corroboration
demo_cache.py            response cache + freeze mode
prewarm_demo.py          fills every cache before a demo
```

### Prior work

Delta Filings existed before this event as a filing-tracking and drafting tool — `compliance.py`, `draft_engine.py`, `drafts_store.py`, `governance.py`, `fetch.py` and the auth/payments layer pre-date it. Section 4 of the brief permits building on an existing personal repository.

Built during the event: the entire agent layer (`agents/`), the event domain model (`events_store.py`), the governance snapshot and delta engine, Delta Copilot (`copilot/`), the Signal workspace, the verification-routing and cross-check layers, and the demo cache. Commit history is on the `hackathon` branch.

---

## Testing

```bash
python3 test_pipeline_trace.py --stub   # full pipeline trace with stubbed model responses
python3 prewarm_demo.py                 # warm every cache, surfaces broken routes
```

`test_pipeline_trace.py` runs one real NSE filing end to end and prints the trace: detection → delta → assessment → documents → audit → approval. The `--stub` responses are deliberately adversarial — they include a fabricated quote and two invented regulation IDs — so the guardrails can be seen rejecting them.
