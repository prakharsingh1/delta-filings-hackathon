# 1-PAGER CONTENT — paste into the official template, section by section
# Keep their layout and ordering. Trim to fit one page; the (—) lines are the
# first things to cut.

## 1. Team & Track
Team: <team name> · Members: <names>
**Track 1 — Agentic AI in Financial Services**
Focus area: Corporate governance & secretarial compliance operations for Indian
listed companies (self-proposed bottleneck, per Section 3).

## 2. The Problem
A Company Secretary at a listed Indian company is personally liable for every
SEBI LODR, Companies Act, PIT and SAST obligation the company carries — Reg 30
material events must reach the exchanges within 24 hours, DIR-12 within 30 days.
A 3–5 person team manually reads exchange announcements, maps each one to the
regulations it triggers, and hand-drafts the same letters every quarter. The
cost is senior time spent on clerical reading; the risk is that one missed
disclosure in a 2,000-filing year is a personal-liability event.

## 3. What You Built
An agentic system that reads a company's filings, computes what actually changed
against its own prior disclosures, decides which regulations that triggers,
drafts the documents required, and stops for a Company Secretary to approve.
Running on 6,276 real NSE filings across 5 listed FMCG issuers it produced 53
governance events, 52 assessments and 124 draft documents — none of which can be
sent without a human decision.

## 4. Architecture Snapshot
Filing → **Monitoring Agent** (flash) classifies against a 16-entry closed
taxonomy → **Delta Engine** (deterministic) computes before/after board
composition from the company's own XBRL → **Intelligence Agent** (pro) retrieves
candidate mandates from a 47-entry regulation library and reasons over only
those → **Action Agent** (flash) drafts disclosure / board note / email /
checklist → **Human approval** → append-only audit trail.

Guardrails, enforced in code not prompts:
• Citation gate — regulation IDs not in the retrieved set are discarded.
  0 invalid citations across 59 stored; rejected planted fakes in testing.
• Evidence gate — a "verbatim" quote absent from the source is dropped.
  Fired on real data: 4 discarded, 10 kept.
• Confidence hard-capped at 95%. Nothing is ever filed automatically.

## 5. Who Pays, and Why Now
**Buyer:** the Company Secretary at a mid-cap NSE-listed issuer running a 3–5
person secretarial team — the person who signs the compliance certificate.
Budget sits with them, not IT.
**Why now:** SEBI's Dec-2024 Integrated Filing circular consolidated Reg
13/27/31/32 into structured XBRL, so board composition is machine-readable for
the first time — the delta engine is only possible because of that. In parallel,
frontier-model cost has fallen far enough that assessing every filing for every
monitored company is economically trivial against one CS hour.

## 6. Regulatory Pathway
Delta is a **B2B tool for a regulated professional**, not a regulated entity. It
prepares and cites; a qualified Company Secretary signs and files. No filing is
submitted by the system, no client money is touched, no advice is issued in
Delta's name — so no SEBI intermediary registration is engaged. The boundary is
enforced in the product: every document is a draft pending approval, and every
automated and human step is written to an append-only audit trail that can be
reconstructed for an auditor or the exchange.

## 7. What's Fake vs. What's Real
**Real:** 6,276 filings pulled live from NSE for HINDUNILVR, ITC, NESTLEIND,
BRITANNIA and DABUR (~2004–2026). 35 quarterly governance snapshots parsed from
those companies' own SEBI Integrated Filing XBRL — every board delta ("9
directors → 8", named directors with DINs) is computed from filings, not
generated. 47-mandate regulation library with real section refs, circular
numbers and penalties. 305 obligations, 53 events, 124 documents produced by the
pipeline.
**Mocked:** auth is bypassed in demo mode (Supabase + Razorpay exist in the
codebase); external endpoints are served from a local cache so the demo does not
depend on venue wifi.
**Must be true in production:** a licensed filing feed with an SLA (NSE's public
API rate-limits and 403s server-side callers — we hit this during the build);
MCA21/TRACES/RBI coverage, since 38 of 55 unmatched obligations are filed with
authorities that never touch the exchanges; and counsel on the
prepare-vs-advise liability boundary.

## 8. What's Next
No — tonight's build is the proof, not the product. Monday's first build is the
**data contract**: a filing feed we can put an SLA behind, because a Company
Secretary will not stake personal liability on a scraper. Tonight proved agents
can turn a filing into a defensible, cited action list with a human gate. That
is the hard part and it works. Dependability is the part you sell.
