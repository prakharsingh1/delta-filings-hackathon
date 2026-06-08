"""SEBI letter drafting engine.

Pipeline:
  1. User picks a company + filing type
  2. We look up the company's recent filings of that type from our cache
     and pull their PDF text (already stored or extracted on demand)
  3. User describes what's new
  4. Gemini drafts the letter in the company's house style, using the past
     filings as few-shot tone references
  5. User edits with AI assist (Polish / Match tone / Make formal)
  6. Final letter rendered to PDF (reportlab) and DOCX (python-docx) with
     SEBI-format letterhead

This is a high-trust feature — users will sign and submit these letters.
We never auto-submit; the AI never claims compliance review; every draft
shows a "review before submitting" banner. The exports include only what
the user has reviewed in the editor.
"""

import io
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# ── Filing type registry ──
# Each entry maps a user-friendly label to:
#   - keywords used to find prior filings of this type in the company's cache
#   - subject template the user can edit
#   - any special body conventions
FILING_TYPES = [
    # ──────────────────────────────────────────────────────────
    # PERIODIC & MANDATORY (LODR + Companies Act)
    # ──────────────────────────────────────────────────────────
    {
        "id": "integrated_governance",
        "label": "Integrated Filing — Governance (Reg 13/27/31/32 consolidated)",
        "category": "Periodic — Governance",
        "subject_default": "Submission of Integrated Filing (Governance) under SEBI Circular SEBI/HO/CFD/CFD-PoD-1/P/CIR/2024/185",
        "keywords": ["integrated filing", "governance", "regulation 27", "regulation 13",
                     "regulation 31", "regulation 32", "compliance certificate"],
        "body_hint": "Quarter-end date and reference to attached XBRL submission consolidating Reg 13 (investor grievance), Reg 27 (CG report), Reg 31 (SHP) and Reg 32 (deviation/variation).",
        "annexure_hints": "Annexure I — Compliance Report on Corporate Governance. II — Statement of Investor Complaints. III — Shareholding Pattern (also XBRL). IV — Statement of Deviation/Variation if applicable.",
    },
    {
        "id": "integrated_financial",
        "label": "Integrated Filing — Financial (Reg 33)",
        "category": "Periodic — Financial",
        "subject_default": "Submission of Integrated Filing (Financial) under SEBI Circular CIR/2024/185",
        "keywords": ["integrated filing", "financial result", "regulation 33"],
        "body_hint": "Quarter/year end, board approval date, references to attached results and auditor's report.",
        "annexure_hints": "Annexure I — Statement of Standalone & Consolidated Results (table). II — Auditor's Limited Review / Independent Audit Report. III — Press release / mgmt commentary.",
    },
    {
        "id": "reg33_results",
        "label": "Regulation 33 — Submission of Financial Results",
        "category": "Periodic — Financial",
        "subject_default": "Submission of Financial Results under Regulation 33 of SEBI (LODR) Regulations, 2015",
        "keywords": ["financial result", "regulation 33", "audited", "unaudited", "quarterly result", "annual result"],
        "body_hint": "Specify the period (quarter / year ended), state the board approved the results, and reference the attached XBRL / PDF.",
        "annexure_hints": "Annexure I — Standalone & Consolidated Results table. II — Auditor's Report. III — Press release if applicable.",
    },
    {
        "id": "reg47_newspaper",
        "label": "Regulation 47 — Newspaper Publication of Results",
        "category": "Periodic — Financial",
        "subject_default": "Newspaper Publication of Financial Results under Regulation 47 of SEBI (LODR) Regulations, 2015",
        "keywords": ["regulation 47", "newspaper", "publication", "financial results"],
        "body_hint": "Names of newspapers (English + regional), date of publication, reference to attached clippings.",
        "annexure_hints": "Annexure I — Newspaper clippings (PDF) showing published results.",
    },
    {
        "id": "reg13_grievance",
        "label": "Regulation 13 — Investor Grievance Statement",
        "category": "Periodic — Investor Relations",
        "subject_default": "Statement of Investor Complaints under Regulation 13 of SEBI (LODR) Regulations, 2015",
        "keywords": ["investor complaint", "investor grievance", "regulation 13"],
        "body_hint": "Quarter, opening complaints, received, resolved, pending; certify resolution timelines as required.",
        "annexure_hints": "Annexure I — Complaints table (Pending at start | Received | Disposed | Pending at end | >30 days pending).",
    },
    {
        "id": "shareholding_pattern",
        "label": "Regulation 31 — Shareholding Pattern",
        "category": "Periodic — Investor Relations",
        "subject_default": "Submission of Shareholding Pattern under Regulation 31 of SEBI (LODR) Regulations, 2015",
        "keywords": ["shareholding pattern", "regulation 31"],
        "body_hint": "Quarter-end date, reference to attached XBRL submission.",
        "annexure_hints": "Annexure I — Shareholding pattern in prescribed Table I/II/III/IV format (also filed via XBRL).",
    },
    {
        "id": "reg32_deviation",
        "label": "Regulation 32 — Statement of Deviation / Variation",
        "category": "Periodic — Investor Relations",
        "subject_default": "Statement of Deviation / Variation under Regulation 32 of SEBI (LODR) Regulations, 2015",
        "keywords": ["regulation 32", "deviation", "variation", "ipo proceeds", "qip proceeds"],
        "body_hint": "Quarter, original objects per offer document, actual deployment, deviations and reasons.",
        "annexure_hints": "Annexure I — Statement comparing planned vs actual utilization of issue proceeds.",
    },
    {
        "id": "reg7_compliance_cert",
        "label": "Regulation 7(3) — Half-yearly Compliance Certificate",
        "category": "Periodic — Compliance",
        "subject_default": "Compliance Certificate under Regulation 7(3) of SEBI (LODR) Regulations, 2015",
        "keywords": ["regulation 7", "reg 7(3)", "compliance certificate", "share transfer", "rta"],
        "body_hint": "Half-year ended, certificate jointly signed by Compliance Officer and RTA, confirming maintenance of share transfer/grievance records.",
        "annexure_hints": "Annexure I — Joint compliance certificate signed by Compliance Officer and RTA.",
    },
    {
        "id": "reg40_pcs",
        "label": "Regulation 40(9) — PCS Certificate",
        "category": "Periodic — Compliance",
        "subject_default": "Certificate from Practising Company Secretary under Regulation 40(9) of SEBI (LODR) Regulations, 2015",
        "keywords": ["regulation 40", "reg 40(9)", "practising company secretary", "pcs certificate"],
        "body_hint": "Half-year ended, PCS confirms timely issuance of share transfer/transmission certificates.",
        "annexure_hints": "Annexure I — PCS certificate.",
    },
    {
        "id": "reg74_recon",
        "label": "Regulation 76 — Reconciliation of Share Capital Audit",
        "category": "Periodic — Compliance",
        "subject_default": "Reconciliation of Share Capital Audit Report under Regulation 76 of SEBI (D&P) Regulations, 2018",
        "keywords": ["regulation 76", "reconciliation", "share capital audit", "rsca"],
        "body_hint": "Quarter-end, reference to PCS-issued reconciliation report.",
        "annexure_hints": "Annexure I — Reconciliation of Share Capital Audit Report by PCS.",
    },
    {
        "id": "reg24a_ascr",
        "label": "Regulation 24A — Annual Secretarial Compliance Report",
        "category": "Periodic — Compliance",
        "subject_default": "Annual Secretarial Compliance Report under Regulation 24A of SEBI (LODR) Regulations, 2015",
        "keywords": ["regulation 24a", "secretarial compliance", "ascr"],
        "body_hint": "Financial year ended, reference to attached PCS-issued Annual Secretarial Compliance Report.",
        "annexure_hints": "Annexure I — Annual Secretarial Compliance Report.",
    },
    {
        "id": "reg34_ar",
        "label": "Regulation 34 — Annual Report Filing",
        "category": "Periodic — Annual",
        "subject_default": "Submission of Annual Report under Regulation 34 of SEBI (LODR) Regulations, 2015",
        "keywords": ["annual report", "regulation 34", "ar", "annual filing"],
        "body_hint": "Financial year ended, AGM date, confirmation that the Annual Report was sent to shareholders 21 days before AGM.",
        "annexure_hints": "Annexure I — Annual Report (full document, attached separately).",
    },
    {
        "id": "brsr",
        "label": "BRSR — Business Responsibility & Sustainability Report",
        "category": "Periodic — Annual",
        "subject_default": "Submission of BRSR under Regulation 34(2)(f) of SEBI (LODR) Regulations, 2015",
        "keywords": ["brsr", "business responsibility", "sustainability report", "regulation 34"],
        "body_hint": "Financial year, reference to BRSR forming part of the Annual Report.",
        "annexure_hints": "Annexure I — BRSR / BRSR Core, in prescribed format.",
    },

    # ──────────────────────────────────────────────────────────
    # MATERIAL EVENTS (Regulation 30)
    # ──────────────────────────────────────────────────────────
    {
        "id": "reg30_disclosure",
        "label": "Regulation 30 — Material Event Disclosure",
        "category": "Material Events",
        "subject_default": "Disclosure under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["regulation 30", "reg 30", "reg. 30", "material event",
                     "intimation", "disclosure", "outcome of", "press release",
                     "general updates"],
        "body_hint": "State the material event, key facts (parties, dates, financial impact), and effective date. Be specific.",
        "annexure_hints": "Disclosures required under SEBI Circular CIR/CFD/CMD/4/2015 (Annexure I) — typically a structured Q&A: details of the event, financial impact, parties involved, expected closing date.",
    },
    {
        "id": "auditor_change",
        "label": "Change in Statutory / Secretarial Auditor (Reg 30)",
        "category": "Material Events — Governance",
        "subject_default": "Disclosure regarding change in Auditor under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["change in auditor", "appointment of auditor", "resignation of auditor", "statutory auditor", "secretarial auditor"],
        "body_hint": "Outgoing auditor name + reason for cessation; incoming auditor name, firm registration, term, fees. Brief profile of incoming auditor.",
        "annexure_hints": "Annexure I — Brief profile of new auditor + disclosures under SEBI Cir CIR/CFD/CMD/4/2015. Annexure II — Resignation letter / consent letter.",
    },
    {
        "id": "subsidiary_material",
        "label": "Material Subsidiary Disclosure (Reg 16/30)",
        "category": "Material Events — Restructuring",
        "subject_default": "Disclosure regarding material subsidiary under Regulation 16 / 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["material subsidiary", "subsidiary", "regulation 16"],
        "body_hint": "Subsidiary name, what makes it material (turnover/net worth threshold), date of becoming material.",
        "annexure_hints": "Annexure I — Particulars of the subsidiary (incorporation, business, financial size).",
    },
    {
        "id": "acquisition_disclosure",
        "label": "Acquisition / Merger / Scheme of Arrangement",
        "category": "Material Events — Restructuring",
        "subject_default": "Disclosure of Acquisition under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["acquisition", "merger", "amalgamation", "scheme of arrangement", "scheme"],
        "body_hint": "Target name, consideration, % stake, mode (cash/share-swap/scheme), expected closing date, regulatory approvals required, rationale.",
        "annexure_hints": "Annexure I — Disclosures under SEBI Cir CIR/CFD/CMD/4/2015 (target details, financial impact, terms). Annexure II — Board resolution if required.",
    },
    {
        "id": "divestment_disclosure",
        "label": "Sale / Divestment of Subsidiary or Undertaking",
        "category": "Material Events — Restructuring",
        "subject_default": "Disclosure of Divestment under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["divestment", "sale of", "transfer of business", "stake sale", "slump sale"],
        "body_hint": "Asset/undertaking sold, buyer, consideration, mode, expected closing, board approval.",
        "annexure_hints": "Annexure I — Particulars of the divestment (target, valuation basis, terms).",
    },
    {
        "id": "credit_rating",
        "label": "Credit Rating Action Disclosure",
        "category": "Material Events — Financial",
        "subject_default": "Disclosure of Credit Rating Action under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["credit rating", "rating action", "upgrade", "downgrade", "rating agency"],
        "body_hint": "Rating agency name, instrument rated, old vs new rating, outlook, date, brief rationale (per agency).",
        "annexure_hints": "Annexure I — Rating rationale letter from the credit rating agency.",
    },
    {
        "id": "litigation_action",
        "label": "Litigation / Regulatory Action / Order",
        "category": "Material Events — Legal",
        "subject_default": "Disclosure of Litigation / Regulatory Action under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["litigation", "regulatory action", "show cause", "order", "penalty", "settlement"],
        "body_hint": "Authority/court, nature of proceeding, date of order, monetary impact, company's response, next steps.",
        "annexure_hints": "Annexure I — Copy of the order / show-cause notice (or summary).",
    },
    {
        "id": "insolvency_proceeding",
        "label": "Corporate Insolvency / IBC Proceeding Disclosure",
        "category": "Material Events — Legal",
        "subject_default": "Disclosure under Regulation 30 — Corporate Insolvency Proceeding",
        "keywords": ["insolvency", "ibc", "nclt", "cirp", "resolution professional"],
        "body_hint": "NCLT bench, petition number, applicant, date of admission/initiation, current status, IRP/RP appointed.",
        "annexure_hints": "Annexure I — NCLT order / admission summary.",
    },
    {
        "id": "rpt_disclosure",
        "label": "Related Party Transactions Disclosure (Reg 23)",
        "category": "Material Events — Governance",
        "subject_default": "Disclosure of Related Party Transactions under Regulation 23 of SEBI (LODR) Regulations, 2015",
        "keywords": ["related party", "rpt", "regulation 23"],
        "body_hint": "Period, summary of RPTs (counterparty, nature, value, basis of pricing, audit committee approval).",
        "annexure_hints": "Annexure I — RPT statement in prescribed format (table of transactions).",
    },
    {
        "id": "general_updates",
        "label": "General Updates / Other Reg 30 Information",
        "category": "Material Events",
        "subject_default": "General Updates under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["general updates", "intimation", "regulation 30", "disclosure"],
        "body_hint": "Brief but specific description of the update being disclosed.",
        "annexure_hints": "Optional Annexure I — supporting document if any (notice, certificate, intimation).",
    },

    # ──────────────────────────────────────────────────────────
    # MEETINGS & RESOLUTIONS
    # ──────────────────────────────────────────────────────────
    {
        "id": "reg29_board_meeting",
        "label": "Regulation 29 — Prior Intimation of Board Meeting",
        "category": "Meetings",
        "subject_default": "Intimation of Board Meeting under Regulation 29 of SEBI (LODR) Regulations, 2015",
        "keywords": ["regulation 29", "reg 29", "board meeting", "prior intimation", "intimation of board"],
        "body_hint": "Provide meeting date, time, and the items on the agenda (e.g. consider unaudited financial results for the quarter ended …).",
        "annexure_hints": "Usually no annexure for prior intimations. Only include trading-window-closure intimation as Annexure I if applicable.",
    },
    {
        "id": "board_change",
        "label": "Change in Directors / Key Managerial Personnel (Reg 30)",
        "category": "Material Events — Governance",
        "subject_default": "Disclosure regarding change in Directors / KMP under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["change in director", "appointment", "resignation", "key managerial", "kmp"],
        "body_hint": "Name, designation, effective date, brief profile (for appointments) or reason (for resignations). Include DIN/PAN as appropriate.",
        "annexure_hints": "Annexure I — Brief profile + disclosures required under SEBI Circular CIR/CFD/CMD/4/2015 (date of appointment, term, brief profile, qualifications, listed companies in which directorship held, relationships, whether on debarment list, etc.).",
    },
    {
        "id": "outcome_board_meeting",
        "label": "Outcome of Board Meeting (Reg 30 / Schedule III)",
        "category": "Meetings",
        "subject_default": "Outcome of Board Meeting under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["outcome of board", "outcome of board meeting", "board outcome"],
        "body_hint": "Date of board meeting, time it commenced and concluded, list of items considered and decisions taken (results, dividends, appointments, etc.).",
        "annexure_hints": "Annexure I — Decisions in tabular form. Annexure II — Press release / specific resolutions if attached.",
    },
    {
        "id": "egm_intimation",
        "label": "EGM / Extra-Ordinary General Meeting Intimation",
        "category": "Meetings",
        "subject_default": "Intimation of Extra-Ordinary General Meeting under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["extra-ordinary general meeting", "egm", "extraordinary general meeting"],
        "body_hint": "EGM date, time, mode (physical/VC/OAVM), book closure dates, e-voting cut-offs.",
        "annexure_hints": "Annexure I — Notice of EGM with explanatory statement under Section 102 of the Companies Act, 2013.",
    },
    {
        "id": "postal_ballot_notice",
        "label": "Postal Ballot Notice",
        "category": "Meetings",
        "subject_default": "Intimation of Postal Ballot under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["postal ballot", "remote e-voting", "e-voting cut-off"],
        "body_hint": "Resolutions proposed, e-voting start/end dates, scrutinizer, cut-off date for voting eligibility.",
        "annexure_hints": "Annexure I — Postal ballot notice with resolutions and explanatory statement.",
    },
    {
        "id": "proceedings_agm",
        "label": "Proceedings of AGM / EGM (Reg 30(6))",
        "category": "Meetings",
        "subject_default": "Proceedings of AGM under Regulation 30(6) of SEBI (LODR) Regulations, 2015",
        "keywords": ["proceedings of annual", "proceedings of extra", "proceedings of agm", "proceedings of egm"],
        "body_hint": "Date/time of meeting, attendance, summary of speeches, summary of resolutions transacted (subject to e-voting result).",
        "annexure_hints": "Annexure I — Summary of proceedings (chair's address highlights, Q&A, resolutions).",
    },

    # ──────────────────────────────────────────────────────────
    # CAPITAL ACTIONS
    # ──────────────────────────────────────────────────────────
    {
        "id": "dividend",
        "label": "Dividend Declaration / Record Date",
        "category": "Capital Actions",
        "subject_default": "Intimation of Dividend / Record Date under Regulation 30 / 42 of SEBI (LODR) Regulations, 2015",
        "keywords": ["dividend", "record date", "interim dividend", "final dividend"],
        "body_hint": "Per-share dividend, type (interim/final), record date, payment date, and board meeting reference.",
        "annexure_hints": "Usually inline; attach board resolution as Annexure I only if requested by the exchange.",
    },
    {
        "id": "record_date",
        "label": "Record Date Intimation (Reg 42)",
        "category": "Capital Actions",
        "subject_default": "Intimation of Record Date under Regulation 42 of SEBI (LODR) Regulations, 2015",
        "keywords": ["record date", "regulation 42"],
        "body_hint": "Purpose (dividend / bonus / rights / split), record date, no-trading effect, cut-off rationale.",
        "annexure_hints": "Inline; no annexure typically required.",
    },
    {
        "id": "book_closure",
        "label": "Book Closure Notice (Reg 42)",
        "category": "Capital Actions",
        "subject_default": "Notice of Book Closure under Regulation 42 of SEBI (LODR) Regulations, 2015",
        "keywords": ["book closure", "register of members", "book closing"],
        "body_hint": "Purpose, book-closure period (start to end dates), references to AGM/dividend/rights as applicable.",
        "annexure_hints": "Inline; no annexure typically required.",
    },
    {
        "id": "buyback_announcement",
        "label": "Buyback — Public Announcement / Disclosure",
        "category": "Capital Actions",
        "subject_default": "Disclosure regarding Buyback of Equity Shares under SEBI (Buyback of Securities) Regulations, 2018",
        "keywords": ["buyback", "buy-back", "public announcement", "tender offer"],
        "body_hint": "Buyback size (₹ + shares), price, mode (tender / open market), record date, expected timeline, board/shareholder approvals.",
        "annexure_hints": "Annexure I — Public Announcement. Annexure II — Buyback Committee resolution. Annexure III — Letter of Offer if attached.",
    },
    {
        "id": "bonus_issue",
        "label": "Bonus Issue Announcement",
        "category": "Capital Actions",
        "subject_default": "Disclosure regarding Bonus Issue under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["bonus issue", "bonus shares"],
        "body_hint": "Bonus ratio (e.g. 1:1), record date, source (free reserves / securities premium / capital redemption), board approval, regulatory approvals pending.",
        "annexure_hints": "Annexure I — Board resolution / source of bonus statement.",
    },
    {
        "id": "rights_issue",
        "label": "Rights Issue Announcement",
        "category": "Capital Actions",
        "subject_default": "Disclosure regarding Rights Issue under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["rights issue", "rights entitlement"],
        "body_hint": "Issue size, ratio, price, record date, opening/closing dates, lead managers.",
        "annexure_hints": "Annexure I — Letter of Offer / abridged offer document.",
    },
    {
        "id": "preferential_qip",
        "label": "Preferential Issue / QIP Disclosure",
        "category": "Capital Actions",
        "subject_default": "Disclosure regarding Preferential Issue / Qualified Institutional Placement under SEBI (ICDR) Regulations, 2018",
        "keywords": ["preferential issue", "qip", "qualified institutional", "icdr"],
        "body_hint": "Number of shares, issue price, allottees (preferential) / floor price (QIP), pricing formula, dates of board/shareholder approvals.",
        "annexure_hints": "Annexure I — Allottee list with pre/post stake. II — Pricing certificate by SEBI-registered Merchant Banker / PCS.",
    },
    {
        "id": "stock_split",
        "label": "Stock Split / Sub-division / Consolidation",
        "category": "Capital Actions",
        "subject_default": "Disclosure regarding Sub-division / Consolidation of Equity Shares",
        "keywords": ["stock split", "sub-division", "consolidation of shares", "subdivision"],
        "body_hint": "Old face value vs new, ratio, record date, board/shareholder approval status.",
        "annexure_hints": "Annexure I — Resolution of board.",
    },
    {
        "id": "ncd_issue",
        "label": "Non-Convertible Debenture / Bond Issuance",
        "category": "Capital Actions",
        "subject_default": "Disclosure regarding issuance of Non-Convertible Debentures under SEBI (Issue and Listing of NCS) Regulations, 2021",
        "keywords": ["non-convertible debenture", "ncd", "ncs", "bond issuance", "debenture"],
        "body_hint": "Size, tenor, coupon, mode (private placement / public), credit rating, listing exchange.",
        "annexure_hints": "Annexure I — Term sheet. II — Credit rating letter. III — Trustee consent.",
    },

    # ──────────────────────────────────────────────────────────
    # AGM (kept after EGM for sidebar grouping)
    # ──────────────────────────────────────────────────────────
    {
        "id": "agm_intimation",
        "label": "AGM Intimation (Annual General Meeting)",
        "category": "Meetings",
        "subject_default": "Intimation of Annual General Meeting under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["annual general meeting", "agm", "egm", "extra-ordinary general", "notice of"],
        "body_hint": "Meeting date, time, mode (physical/VC/OAVM), book closure dates, e-voting cut-offs, and link to the notice if attached.",
        "annexure_hints": "Annexure I — Notice of AGM/EGM with explanatory statement under Section 102 of the Companies Act, 2013.",
    },
    {
        "id": "voting_results",
        "label": "Regulation 44 — Voting Results",
        "category": "Meetings",
        "subject_default": "Submission of Voting Results under Regulation 44 of SEBI (LODR) Regulations, 2015",
        "keywords": ["voting result", "regulation 44", "scrutinizer", "postal ballot", "e-voting result"],
        "body_hint": "AGM/EGM/postal ballot reference, scrutinizer's report attached, summary of resolutions and votes for/against.",
        "annexure_hints": "Annexure I — Voting results table per resolution (Resolution / Type / Total votes / Favour / Against / Invalid / % in favour). Annexure II — Scrutinizer's Report duly signed.",
    },
    # ──────────────────────────────────────────────────────────
    # PIT — Insider Trading (SEBI PIT Regulations, 2015)
    # ──────────────────────────────────────────────────────────
    {
        "id": "pit_initial_disclosure",
        "label": "PIT Reg 7(1) — Initial Disclosure (Promoter / KMP / Director)",
        "category": "PIT — Insider Trading",
        "subject_default": "Initial Disclosure under Regulation 7(1) of SEBI (PIT) Regulations, 2015",
        "keywords": ["regulation 7(1)", "initial disclosure", "promoter shareholding", "pit regulation"],
        "body_hint": "Name of person, designation/category, holding as on cut-off date, percentage of total share capital.",
        "annexure_hints": "Annexure I — Form B (initial disclosure of holdings as required by SEBI PIT).",
    },
    {
        "id": "pit_continual_disclosure",
        "label": "PIT Reg 7(2) — Continual Disclosure (Trade by Insider)",
        "category": "PIT — Insider Trading",
        "subject_default": "Continual Disclosure under Regulation 7(2) of SEBI (PIT) Regulations, 2015",
        "keywords": ["regulation 7(2)", "continual disclosure", "insider trading", "trade by insider"],
        "body_hint": "Insider name, relationship, securities traded, mode (purchase/sale/pledge), value, date(s), pre/post holding.",
        "annexure_hints": "Annexure I — Form C (continual disclosure of changes).",
    },
    {
        "id": "trading_window_closure",
        "label": "Trading Window Closure / Reopening Intimation",
        "category": "PIT — Insider Trading",
        "subject_default": "Intimation of Closure of Trading Window under SEBI (PIT) Regulations, 2015",
        "keywords": ["trading window", "closure", "designated persons"],
        "body_hint": "Reason (board meeting on results / unpublished price-sensitive information), closure period (from–to date), reference to Code of Conduct.",
        "annexure_hints": "Inline; no annexure typically required.",
    },
    {
        "id": "code_of_conduct_disclosure",
        "label": "Code of Conduct (PIT) — Disclosure / Update",
        "category": "PIT — Insider Trading",
        "subject_default": "Submission of Code of Conduct under Regulation 9 of SEBI (PIT) Regulations, 2015",
        "keywords": ["code of conduct", "code of fair disclosure", "regulation 9"],
        "body_hint": "Reference to revised Code adopted by the Board, effective date.",
        "annexure_hints": "Annexure I — Updated Code of Conduct / Code of Fair Disclosure.",
    },

    # ──────────────────────────────────────────────────────────
    # SAST — Substantial Acquisition of Shares & Takeovers (2011)
    # ──────────────────────────────────────────────────────────
    {
        "id": "sast_29_1",
        "label": "SAST Reg 29(1) — Acquisition crossing 5% threshold",
        "category": "SAST — Takeovers",
        "subject_default": "Disclosure under Regulation 29(1) of SEBI (SAST) Regulations, 2011",
        "keywords": ["regulation 29(1)", "sast", "5%", "acquirer disclosure"],
        "body_hint": "Acquirer name (and PAC if any), pre/post acquisition shareholding, transaction details, date.",
        "annexure_hints": "Annexure I — Form for disclosure under SAST Reg 29(1).",
    },
    {
        "id": "sast_29_2",
        "label": "SAST Reg 29(2) — Promoter 2% incremental disclosure",
        "category": "SAST — Takeovers",
        "subject_default": "Disclosure under Regulation 29(2) of SEBI (SAST) Regulations, 2011",
        "keywords": ["regulation 29(2)", "sast", "promoter holding", "2%"],
        "body_hint": "Promoter name, mode of acquisition/disposal, change in stake, dates.",
        "annexure_hints": "Annexure I — Form for disclosure under SAST Reg 29(2).",
    },
    {
        "id": "sast_30",
        "label": "SAST Reg 30 — Annual Promoter Disclosure (March 31)",
        "category": "SAST — Takeovers",
        "subject_default": "Annual Disclosure under Regulation 30 of SEBI (SAST) Regulations, 2011",
        "keywords": ["regulation 30 sast", "sast annual", "promoter annual disclosure"],
        "body_hint": "As of 31 March each year — promoter group holdings (name, PAN, equity, voting rights, encumbered).",
        "annexure_hints": "Annexure I — Annual disclosure form (Format under SAST Reg 30).",
    },
    {
        "id": "sast_31",
        "label": "SAST Reg 31 — Encumbrance / Pledge Disclosure",
        "category": "SAST — Takeovers",
        "subject_default": "Disclosure of Encumbrance under Regulation 31 of SEBI (SAST) Regulations, 2011",
        "keywords": ["regulation 31 sast", "encumbrance", "pledge", "invocation"],
        "body_hint": "Pledgor / pledgee, securities encumbered, reason, date, percentage of total holding.",
        "annexure_hints": "Annexure I — Encumbrance/pledge disclosure form.",
    },

    # ──────────────────────────────────────────────────────────
    # OTHER / SPECIFIC LODR
    # ──────────────────────────────────────────────────────────
    {
        "id": "depositories_certificate",
        "label": "SEBI (Depositories & Participants) Reg 74 — Certificate",
        "category": "Periodic — Compliance",
        "subject_default": "Certificate under Regulation 74(5) of SEBI (Depositories & Participants) Regulations, 2018",
        "keywords": ["regulation 74", "depositories", "rta certificate", "registrar certificate"],
        "body_hint": "Quarter-end, RTA-issued certificate confirming dematerialization of securities and reconciliation.",
        "annexure_hints": "Annexure I — RTA-signed certificate.",
    },
    {
        "id": "reg39_loss_certificate",
        "label": "Regulation 39 — Loss / Duplicate Share Certificate",
        "category": "Material Events — Investor Relations",
        "subject_default": "Disclosure regarding Loss / Issue of Duplicate Share Certificate under Regulation 39 of SEBI (LODR) Regulations, 2015",
        "keywords": ["regulation 39", "loss of share", "duplicate certificate"],
        "body_hint": "Folio / DP-ID, certificate numbers lost, distinctive numbers, reason, action taken.",
        "annexure_hints": "Annexure I — Statement of lost certificates.",
    },
    {
        "id": "reg40_transfer",
        "label": "Regulation 40 — Transfer / Transmission of Securities",
        "category": "Material Events — Investor Relations",
        "subject_default": "Disclosure regarding Transfer / Transmission under Regulation 40 of SEBI (LODR) Regulations, 2015",
        "keywords": ["regulation 40", "transfer of securities", "transmission"],
        "body_hint": "Folio / DP-ID, holder, mode (transfer / transmission / re-mat), particulars.",
        "annexure_hints": "Annexure I — Particulars of transfer / transmission requests processed.",
    },
    {
        "id": "reg46_website",
        "label": "Regulation 46 — Website Disclosure Compliance",
        "category": "Periodic — Compliance",
        "subject_default": "Compliance with Regulation 46 — Website Disclosure",
        "keywords": ["regulation 46", "website disclosure"],
        "body_hint": "Confirmation that all items prescribed under Reg 46 are uploaded and current on the company website; URL.",
        "annexure_hints": "Annexure I — Checklist / screenshot of website disclosures (optional).",
    },

    # ──────────────────────────────────────────────────────────
    # PRESS / GENERIC
    # ──────────────────────────────────────────────────────────
    {
        "id": "press_release",
        "label": "Press Release / Disclosure (general)",
        "category": "Material Events",
        "subject_default": "Submission of Press Release under Regulation 30 of SEBI (LODR) Regulations, 2015",
        "keywords": ["press release"],
        "body_hint": "Briefly describe the event the press release covers; attach the release.",
        "annexure_hints": "Annexure I — Full text of the press release.",
    },
    {
        "id": "generic",
        "label": "Other / Generic SEBI letter",
        "category": "Other",
        "subject_default": "",
        "keywords": [],
        "body_hint": "Write a clear subject line and 1–3 paragraph body. Include all material facts.",
        "annexure_hints": "Add annexures only if the letter references attached documents (e.g., resolutions, certificates, schedules).",
    },
]


def get_filing_types() -> List[Dict]:
    return [{k: v for k, v in t.items() if k != "keywords"} for t in FILING_TYPES]


def get_filing_type(filing_type_id: str) -> Optional[Dict]:
    for t in FILING_TYPES:
        if t["id"] == filing_type_id:
            return t
    return None


# ── Past examples lookup ──

def find_past_examples(symbol: str, filing_type_id: str,
                       max_examples: int = 8,
                       extract_text: bool = True) -> List[Dict]:
    """Return up to `max_examples` past filings matching the type's
    keywords, most recent first. Each example has `title`, `date`,
    `description`, `pdf_link`, and `text` (extracted body, may be empty).

    `extract_text=False` lets the UI list past filings cheaply (no PDF
    fetches) so it can show the user *all* past filings of this type
    while still doing on-demand text extraction only for the few that
    feed the AI."""
    from database import get_cached_filings, get_filing_by_id
    from pdf_parser import download_and_extract

    ftype = get_filing_type(filing_type_id)
    if not ftype:
        return []
    keywords = [k.lower() for k in ftype.get("keywords", [])]
    if not keywords:
        return []

    filings = get_cached_filings(symbol)
    matches = []
    for f in filings:
        hay = ((f.get("title", "") or "") + " " +
               (f.get("description", "") or "") + " " +
               (f.get("tags", "") or "")).lower()
        score = sum(1 for k in keywords if k in hay)
        if score == 0:
            continue
        matches.append((score, f))
    matches = matches[:max_examples]

    out = []
    for score, f in matches:
        text = (f.get("raw_text") or "").strip()
        if extract_text and not text and f.get("pdf_link"):
            try:
                text = download_and_extract(f["pdf_link"]) or ""
            except Exception:
                text = ""
        out.append({
            "id": f.get("id"),
            "title": f.get("title", ""),
            "date": f.get("date", ""),
            "description": f.get("description", ""),
            "pdf_link": f.get("pdf_link", ""),
            "text": text[:6000],   # per-example cap; full texts blow up prompts
        })
    return out


# ── Style fingerprint: cached deep-language pass ──
# Once per (company, filing_type, day), we let Gemini read 6-10 historical
# letters and extract the company's recurring phrases — opening line,
# regulatory citation phrasing, sign-off, designations, anything idiomatic.
# That fingerprint becomes a much smaller, higher-signal context for the
# actual draft generation than dumping raw text into every prompt.

def _ensure_style_table():
    from database import get_connection
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_style_fingerprints (
            symbol TEXT NOT NULL,
            filing_type TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            example_count INTEGER NOT NULL,
            learned_at TEXT NOT NULL,
            PRIMARY KEY (symbol, filing_type)
        )
    """)
    conn.commit()
    conn.close()


def get_cached_style(symbol: str, filing_type: str,
                     max_age_hours: int = 24) -> Optional[Dict]:
    _ensure_style_table()
    from database import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM draft_style_fingerprints WHERE symbol=? AND filing_type=?",
        (symbol.upper(), filing_type),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        learned = datetime.fromisoformat(row["learned_at"])
        age_h = (datetime.now() - learned).total_seconds() / 3600
        if age_h > max_age_hours:
            return None
    except Exception:
        return None
    return dict(row)


def save_style_fingerprint(symbol: str, filing_type: str,
                           fingerprint: str, example_count: int):
    _ensure_style_table()
    from database import get_connection
    conn = get_connection()
    conn.execute(
        """INSERT INTO draft_style_fingerprints
           (symbol, filing_type, fingerprint, example_count, learned_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(symbol, filing_type) DO UPDATE SET
               fingerprint=excluded.fingerprint,
               example_count=excluded.example_count,
               learned_at=excluded.learned_at""",
        (symbol.upper(), filing_type, fingerprint, example_count,
         datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def learn_company_style(api_key: str, symbol: str, filing_type: str,
                        examples: List[Dict]) -> Optional[str]:
    """Read up to 8 historical letters and extract the company's recurring
    house-style patterns. Cached for 24h per (symbol, filing_type) so the
    deep pass only runs once. Returns the fingerprint text, or None if
    AI is unavailable or there's nothing to learn from."""
    if not examples:
        return None

    cached = get_cached_style(symbol, filing_type)
    if cached and cached["example_count"] >= len(examples):
        return cached["fingerprint"]

    # Stitch up to 8 examples, capped per-example so the input stays sane.
    body = []
    used = 0
    for i, ex in enumerate(examples[:8], 1):
        text = (ex.get("text") or "").strip()
        if not text:
            continue
        body.append(f"--- LETTER #{i} ({ex.get('date','')}) ---\n{text[:2500]}")
        used += 1
    if used < 1:
        return None

    prompt = (
        f"You are an editor analyzing the house writing style of {symbol}'s "
        f"compliance correspondence. Below are {used} past letters of the same "
        "type from this company. Extract the recurring style patterns the "
        "company actually uses, with concrete quotations.\n\n"
        "Output PLAIN TEXT in this exact structure (use these section headers):\n"
        "OPENING PHRASES: <one or two sentences listing the typical opening, with quotes>\n"
        "REGULATORY CITATION STYLE: <how they cite SEBI regulations — quote a few examples>\n"
        "RECURRING BOILERPLATE: <standing phrases like 'we hereby inform' / 'kindly take this on record' — quote them>\n"
        "SIGN-OFF CONVENTION: <how letters close, signer designation patterns>\n"
        "TONE NOTES: <short — formal/passive/concise, level of detail, quirks>\n\n"
        "Be terse. ~12-18 lines total. Quote exact phrases the company uses so "
        "future drafts can reuse them verbatim. Skip a section if the examples "
        "don't reveal a clear pattern.\n\n"
        + "\n\n".join(body)
    )

    fingerprint = _gemini_call(api_key, prompt, max_output_tokens=1200)
    if fingerprint:
        save_style_fingerprint(symbol, filing_type, fingerprint, used)
    return fingerprint


# ── Gemini calls ──

_FREE_TIER_MODELS = {"gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"}


def _gemini_call(api_key: str, prompt: str,
                 max_output_tokens: int = 1500,
                 model: str = "gemini-2.5-flash",
                 json_mode: bool = False) -> Optional[str]:
    """Make one Gemini call with thinking disabled, model fallback, retry
    on 503. Returns the text or None on total failure.

    `json_mode=True` asks Gemini to return strict JSON (used by the draft
    generator so we can split body + annexures cleanly without parsing
    free-form headings)."""
    try:
        from google import genai
    except Exception as e:
        print(f"[DRAFT] genai import failed: {e}")
        return None
    if model not in _FREE_TIER_MODELS:
        model = "gemini-2.5-flash"

    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[DRAFT] client init failed: {e}")
        return None

    chain = [model]
    for fb in ("gemini-2.0-flash", "gemini-2.5-flash-lite"):
        if fb != model:
            chain.append(fb)

    config = {
        "max_output_tokens": max_output_tokens,
        "temperature": 0.3,
        "thinking_config": {"thinking_budget": 0},
    }
    if json_mode:
        config["response_mime_type"] = "application/json"

    import time
    last_err = None
    for m in chain:
        for attempt in range(2):
            try:
                resp = client.models.generate_content(
                    model=m, contents=prompt, config=config,
                )
                text = (resp.text or "").strip()
                if text:
                    return text
            except Exception as e:
                last_err = e
                msg = str(e)
                if "503" in msg or "UNAVAILABLE" in msg or "overloaded" in msg.lower():
                    if attempt == 0:
                        time.sleep(1.5)
                        continue
                print(f"[DRAFT] gemini {m} failed (try {attempt+1}): {msg[:160]}")
                break
    if last_err:
        print(f"[DRAFT] gemini exhausted: {last_err}")
    return None


def generate_draft(api_key: str, company_name: str, filing_type_id: str,
                   new_info: str, past_examples: List[Dict],
                   subject_override: str = "",
                   symbol: str = "",
                   style_fingerprint: str = "") -> Dict:
    """Produce a first-draft letter body in the company's tone.

    We give Gemini TWO complementary signals:
      1. A pre-extracted **style fingerprint** (compact, high-signal — learned
         once from up to 8 past letters and cached for 24h).
      2. **2-3 raw examples** so it has actual sentences to anchor on.

    The fingerprint is far more efficient than dumping 8 full letters into
    every draft prompt and produces noticeably tighter tone matching."""
    ftype = get_filing_type(filing_type_id) or {}
    label = ftype.get("label", "SEBI letter")
    body_hint = ftype.get("body_hint", "")
    subject = subject_override.strip() or ftype.get("subject_default", label)

    # Try to learn (or load cached) deep style fingerprint.
    if not style_fingerprint and symbol and past_examples:
        try:
            style_fingerprint = learn_company_style(api_key, symbol,
                                                    filing_type_id,
                                                    past_examples) or ""
        except Exception as e:
            print(f"[DRAFT] style fingerprint failed: {e}")

    examples_block = ""
    for i, ex in enumerate(past_examples[:3], 1):
        snippet = (ex.get("text") or ex.get("description", ""))[:1500].strip()
        if snippet:
            examples_block += f"\n--- PAST FILING #{i} ({ex.get('date','')}) ---\n{snippet}\n"

    style_block = ""
    if style_fingerprint:
        style_block = (
            "\nLearned house-style fingerprint for this company (use these "
            "phrases verbatim where they fit):\n"
            "==========\n"
            f"{style_fingerprint.strip()}\n"
            "==========\n"
        )

    annexure_hints = ftype.get("annexure_hints", "") or ""

    prompt = (
        f"You are drafting a SEBI compliance letter for {company_name}. "
        f"Letter type: {label}.\n\n"
        f"Body guidance: {body_hint}\n"
        f"Typical annexures for this type: {annexure_hints}\n\n"
        f"What's NEW that this letter must convey:\n{new_info.strip()}\n"
        + style_block
        + (f"\nReference these recent past letters from the SAME company "
           f"as additional tone anchors:\n{examples_block}\n" if examples_block else "")
        + "\nReturn STRICT JSON in this exact shape:\n"
        "{\n"
        '  "body": "<the letter body — plain text, NO greeting/sign-off/subject/date>",\n'
        '  "annexures": [\n'
        '    {"title": "Annexure I — <descriptive title>", "body": "<full annexure content>"},\n'
        "    ...\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        " - body: 2-4 short paragraphs. Cites the regulatory basis. References annexures by Roman numeral when relevant ('attached as Annexure I'). Uses company's standing phrases from the fingerprint verbatim.\n"
        " - annexures: include only annexures that actually make sense for THIS letter type and the NEW info given. Most letters have 0-2 annexures; some (Reg 33, Reg 44) have 2-3.\n"
        " - Annexure body should be substantive — for board appointments, the full SEBI Cir 2015 Q&A profile; for voting, a markdown pipe-table; for financial results, a structured statement of results.\n"
        " - Use markdown pipe-tables for tabular data: | Header | ... |\\n| --- | ... |\\n| cell | ... |\n"
        " - DO NOT invent facts. Where data is missing write [PLEASE FILL — short hint].\n"
        " - Output ONLY the JSON. No prose before or after.\n"
    )
    raw = _gemini_call(api_key, prompt, max_output_tokens=3500, json_mode=True) or ""

    body = ""
    annexures: List[Dict] = []
    if raw:
        import json as _json
        try:
            parsed = _json.loads(raw)
            body = (parsed.get("body") or "").strip()
            for a in (parsed.get("annexures") or []):
                title = (a.get("title") or "").strip()
                a_body = (a.get("body") or "").strip()
                if title and a_body:
                    annexures.append({"title": title, "body": a_body})
        except Exception as e:
            print(f"[DRAFT] JSON parse failed, falling back to raw text: {e}")
            body = raw  # graceful degrade — give them something to edit

    return {
        "subject": subject,
        "body": body,
        "annexures": annexures,
        "style_fingerprint": style_fingerprint,
    }


def _gemini_call_grounded(api_key: str, prompt: str,
                          max_output_tokens: int = 2500,
                          model: str = "gemini-2.5-flash") -> Optional[str]:
    """Gemini call with Google Search grounding enabled — lets the model
    look up live company data instead of relying on its training cutoff.
    Grounding is incompatible with `response_mime_type='application/json'`
    so callers should ask for JSON in the prompt and tolerate surrounding
    prose if the model adds any."""
    try:
        from google import genai
        from google.genai import types as genai_types
    except Exception as e:
        print(f"[DRAFT] genai import failed (grounded): {e}")
        return None
    if model not in _FREE_TIER_MODELS:
        model = "gemini-2.5-flash"

    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[DRAFT] grounded client init failed: {e}")
        return None

    try:
        config = genai_types.GenerateContentConfig(
            max_output_tokens=max_output_tokens,
            temperature=0.2,
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        )
    except Exception as e:
        # Some SDK versions don't expose every type — fall back to dict.
        print(f"[DRAFT] grounded config init: {e}")
        config = {
            "max_output_tokens": max_output_tokens,
            "temperature": 0.2,
            "tools": [{"google_search": {}}],
            "thinking_config": {"thinking_budget": 0},
        }

    import time
    for attempt in range(2):
        try:
            resp = client.models.generate_content(
                model=model, contents=prompt, config=config,
            )
            text = (resp.text or "").strip()
            if text:
                return text
        except Exception as e:
            msg = str(e)
            if "503" in msg or "UNAVAILABLE" in msg or "overloaded" in msg.lower():
                if attempt == 0:
                    time.sleep(1.5)
                    continue
            print(f"[DRAFT] grounded call failed: {msg[:200]}")
            break
    return None


def _extract_json_object(text: str) -> Optional[str]:
    """Pull the first balanced JSON object out of a possibly-prose response.
    Grounded responses sometimes include prose around the JSON."""
    if not text:
        return None
    text = text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].lstrip()
    # Find the outermost { ... }
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def ai_fill_placeholders(api_key: str, company_name: str, symbol: str,
                         filing_type: str, new_info: str, body: str,
                         annexures: List[Dict], placeholders: List[Dict]) -> List[Dict]:
    """Ask Gemini to pre-fill placeholder fields where it can with high
    confidence. Returns a list of {pattern, value} entries — fields it
    can't confidently fill are simply omitted, leaving them for the user.

    Conservative by design: we'd rather under-fill than fabricate dates,
    amounts, or names. The prompt explicitly forbids inventing facts."""
    if not placeholders:
        return []

    ph_block_lines = []
    for i, p in enumerate(placeholders, 1):
        ph_block_lines.append(f"{i}. PATTERN: {p['pattern']}")
        ph_block_lines.append(f"   LABEL: {p.get('label','')}")
        if p.get("hint"):
            ph_block_lines.append(f"   HINT: {p['hint']}")
    placeholder_block = "\n".join(ph_block_lines)

    annexures_text = "\n\n".join(
        f"--- {a.get('title','')} ---\n{a.get('body','')}"
        for a in (annexures or [])
    )

    prompt = (
        f"You fill placeholder fields in a SEBI compliance letter for "
        f"{company_name} (NSE: {symbol}). Filing type: {filing_type}.\n\n"
        f"--- USER'S DESCRIPTION OF THE EVENT ---\n"
        f"{new_info or '(no description provided)'}\n\n"
        f"--- CURRENT LETTER BODY ---\n{body or '(empty)'}\n\n"
        f"--- ANNEXURES ---\n{annexures_text or '(none)'}\n\n"
        f"--- PLACEHOLDERS TO FILL ---\n{placeholder_block}\n\n"
        "RULES — read carefully:\n"
        "1. ANSWER ONLY THE FIELD. Crisp, direct values — no sentences, no preamble, no "
        "qualifiers, no 'this represents…' explanations. Just the value.\n"
        "2. Match the question's UNIT. If they ask for a number, give a number. If they "
        "ask for a date, give a date. If they ask for a name, give a name. Don't paraphrase.\n"
        "3. Use Google Search to look up listed-company facts (annual turnover, CIN, "
        "registered office, sector, market cap, employee count). FILL THESE — they're public.\n"
        "4. FILL aggressively. Skipping is for truly user-private data only (specific "
        "transaction date the user didn't tell you, exact deal price they didn't tell you, "
        "names of newly-appointed people they didn't tell you).\n"
        "5. NEVER write 'Not specified', 'Unknown', 'N/A', 'TBD', or any refusal phrase as "
        "a value. Just OMIT that placeholder from the fills array — the user will fill it.\n"
        "6. Match the EXACT pattern string in your output (braces, brackets, em-dashes, "
        "apostrophes, all preserved).\n\n"
        "ANSWER FORMAT EXAMPLES:\n"
        "  Q: Airtel's latest annual turnover\n"
        "  A: \"Approximately ₹1,49,982 crore (FY2025)\"\n"
        "  ✗ NOT: \"Bharti Airtel reported a consolidated revenue of approximately ₹1,49,982 crore for FY2025, "
        "reflecting strong growth in their wireless segment...\"\n\n"
        "  Q: Percentage of shareholding acquired\n"
        "  A: \"51%\"\n"
        "  ✗ NOT: \"The acquisition involves a 51% controlling stake which gives the company majority…\"\n\n"
        "  Q: Effective date of acquisition  (user said: '15 May 2026')\n"
        "  A: \"15 May 2026\"\n\n"
        "  Q: Effective date of acquisition  (user did NOT say a date)\n"
        "  A: omit this placeholder from the fills array entirely\n\n"
        "  Q: Mode of acquisition  (user said: 'cash')\n"
        "  A: \"Cash\"\n\n"
        "  Q: Registered office address of [Company]\n"
        "  A: \"Regent Chambers, 7th Floor, 208 Nariman Point, Mumbai – 400 021\"\n\n"
        "  Q: CIN of [Company]\n"
        "  A: \"L24100MH1969PLC014336\"\n\n"
        "  Q: Sector of [Company]\n"
        "  A: \"Chemicals\"\n\n"
        "  Q: Expected financial impact\n"
        "  A: omit unless the user gave a concrete number/range\n\n"
        "Keep every value under ~80 characters. If you need more, you're explaining — stop.\n\n"
        "Return STRICT JSON:\n"
        '{ "fills": [ {"pattern": "<exact pattern>", "value": "<short crisp value>"}, ... ] }\n'
        "Output ONLY the JSON. No commentary, no markdown fences."
    )

    # Try grounded first — it can look up live company data. If grounded
    # returns nothing OR returns valid JSON with zero fills, fall back to
    # non-grounded JSON mode which is more reliable for parsing.
    print(f"[DRAFT] ai_fill: attempting grounded call for {len(placeholders)} placeholders...")
    raw = _gemini_call_grounded(api_key, prompt, max_output_tokens=2500)
    if not raw:
        print("[DRAFT] ai_fill: grounded returned empty, falling back to non-grounded")
        raw = _gemini_call(api_key, prompt, max_output_tokens=2500, json_mode=True)
    if not raw:
        print("[DRAFT] ai_fill: empty response from Gemini")
        return []
    import json as _json
    candidate = _extract_json_object(raw) or raw
    try:
        parsed = _json.loads(candidate)
        fills = parsed.get("fills") or []
    except Exception as e:
        print(f"[DRAFT] ai_fill JSON parse failed: {e}")
        print(f"[DRAFT] ai_fill raw response (first 500 chars): {raw[:500]!r}")
        return []

    # Defensive filter — drop "I don't know" answers ONLY when the entire
    # value is a refusal (don't kill legit fills that contain phrases like
    # "exact figure not available" inside an otherwise-good answer).
    REFUSAL_VALUES = {
        "not specified", "unknown", "n/a", "na", "not available",
        "information not available", "not provided", "not stated",
        "not mentioned", "to be determined", "tbd", "tba",
        "please specify", "needs clarification", "no information",
        "not specified in the user's description",
        "not specified in user's description",
        "not specified in the description",
    }

    def _trim_to_short_answer(v: str) -> str:
        """Cap verbose answers — first sentence or 120 chars, whichever
        is shorter. Lets the prompt's 'crisp answer' rule survive even if
        the model still drifts into prose."""
        v = v.strip().strip('"').strip("'").strip()
        # If the model glued multiple paragraphs, take just the first.
        v = v.split("\n\n", 1)[0].strip()
        # Single-line first.
        v = v.split("\n", 1)[0].strip()
        # If a full sentence (ends with .!?) and is much longer than 80 chars,
        # take just the first sentence.
        if len(v) > 80:
            for end in (". ", "? ", "! "):
                idx = v.find(end)
                if 10 < idx < 120:
                    v = v[:idx + 1].strip()
                    break
        # Hard cap
        if len(v) > 200:
            v = v[:200].rstrip(", ;-—") + "…"
        return v

    def _clean_fills(raw_fills):
        out = []
        for f in raw_fills:
            pat = str(f.get("pattern", "")).strip()
            val = str(f.get("value", "")).strip()
            if not pat or not val:
                continue
            normalized = val.lower().strip(" .'\"")
            if normalized in REFUSAL_VALUES:
                continue
            if any(normalized.startswith(rv + " ") or normalized.startswith(rv + ".")
                   for rv in REFUSAL_VALUES):
                continue
            out.append({"pattern": pat, "value": _trim_to_short_answer(val)})
        return out

    cleaned = _clean_fills(fills)
    print(f"[DRAFT] ai_fill: {len(cleaned)} usable fills "
          f"(grounded model produced {len(fills)} for {len(placeholders)} placeholders)")

    # If grounded returned zero usable fills, try the non-grounded JSON-mode
    # path as a second shot — it's more reliable at producing parseable JSON
    # even if it can't look up live data, and at minimum it should fill
    # values the user gave in `new_info`.
    if not cleaned:
        print("[DRAFT] ai_fill: zero usable fills from grounded, retrying with non-grounded")
        raw2 = _gemini_call(api_key, prompt, max_output_tokens=2500, json_mode=True)
        if raw2:
            try:
                parsed2 = _json.loads(_extract_json_object(raw2) or raw2)
                fills2 = parsed2.get("fills") or []
                cleaned = _clean_fills(fills2)
                print(f"[DRAFT] ai_fill (retry): {len(cleaned)} usable fills "
                      f"(produced {len(fills2)} for {len(placeholders)})")
            except Exception as e:
                print(f"[DRAFT] ai_fill retry parse failed: {e}")

    return cleaned


def improve_text(api_key: str, text: str, mode: str = "polish",
                 past_examples: Optional[List[Dict]] = None,
                 instruction: str = "") -> Optional[str]:
    """Modes:
      - 'polish': fix grammar, clarity, formality without changing meaning
      - 'tone':   rewrite to match the company's past filings (uses examples)
      - 'formal': make stricter / more SEBI-conventional
      - 'custom': follow the user's free-form `instruction` (e.g. "make it
        shorter", "add a paragraph about regulatory approvals", "translate
        to Hindi"). Always preserves SEBI-letter shape unless the
        instruction explicitly says otherwise."""
    if not text.strip():
        return None
    if mode == "custom":
        if not instruction.strip():
            return None
        instr = (
            "Modify the SEBI compliance letter DRAFT below per the user's instruction. "
            "Preserve facts unless the instruction explicitly tells you to change them. "
            "Keep [PLEASE FILL …] placeholders in place unless the instruction fills them. "
            "Output ONLY the modified body — no greeting, no sign-off, no commentary.\n\n"
            f"USER INSTRUCTION: {instruction.strip()}\n"
        )
    elif mode == "tone":
        examples_block = ""
        for i, ex in enumerate((past_examples or [])[:2], 1):
            snippet = (ex.get("text") or ex.get("description", ""))[:1200].strip()
            if snippet:
                examples_block += f"\n--- PAST FILING #{i} ---\n{snippet}\n"
        instr = (
            "Rewrite the DRAFT below to match the tone and standing phrases "
            "used by the company in the past filings shown.\n"
            f"{examples_block}\n"
            "Preserve every fact in the draft. Output the rewritten body only.\n"
        )
    elif mode == "formal":
        instr = (
            "Rewrite the DRAFT below in stricter, SEBI-conventional formal "
            "register: passive voice where natural, 'we hereby inform / submit / "
            "intimate', 'pursuant to', 'in compliance with'. Preserve every fact. "
            "Output the rewritten body only.\n"
        )
    else:  # polish
        instr = (
            "Polish the DRAFT below: fix grammar, punctuation, awkward phrasing, "
            "and SEBI-formality. Do NOT change the meaning or add new facts. "
            "Do not remove [PLEASE FILL] placeholders. Output the polished body only.\n"
        )
    prompt = instr + "\n--- DRAFT ---\n" + text.strip()
    return _gemini_call(api_key, prompt, max_output_tokens=1500)


# ── Letter rendering ──

# NSE & BSE Listing Department addresses — these are the standard
# recipients for almost every SEBI LODR letter.
LISTING_ADDRESSES = [
    {
        "exchange": "BSE Limited",
        "lines": [
            "The Manager",
            "Listing Department",
            "BSE Limited",
            "Phiroze Jeejeebhoy Towers, Dalal Street",
            "Mumbai – 400 001",
        ],
        "scrip_code_label": "Scrip Code",
    },
    {
        "exchange": "National Stock Exchange of India Limited",
        "lines": [
            "The Manager",
            "Listing Department",
            "National Stock Exchange of India Limited",
            "Exchange Plaza, Bandra Kurla Complex",
            "Bandra (East), Mumbai – 400 051",
        ],
        "scrip_code_label": "Symbol",
    },
]


def build_letter_data(company_name: str, registered_office: str = "",
                      cin: str = "", subject: str = "", body: str = "",
                      signer_name: str = "", signer_designation: str = "",
                      bse_code: str = "", nse_symbol: str = "",
                      letter_date: str = "",
                      annexures: Optional[List[Dict]] = None) -> Dict:
    """Normalize all fields used by both the PDF and DOCX renderers.

    `annexures` is a list of {title, body}. Each will render on its own
    new page after the main letter body."""
    if not letter_date:
        letter_date = datetime.now().strftime("%d %B, %Y")
    cleaned_annexures = []
    for a in (annexures or []):
        title = (a.get("title") or "").strip()
        body_t = (a.get("body") or "").strip()
        if title and body_t:
            cleaned_annexures.append({"title": title, "body": body_t})
    return {
        "company_name": (company_name or "").strip().upper(),
        "registered_office": (registered_office or "").strip(),
        "cin": (cin or "").strip(),
        "subject": (subject or "").strip(),
        "body": (body or "").strip(),
        "signer_name": (signer_name or "").strip(),
        "signer_designation": (signer_designation or "").strip(),
        "bse_code": (bse_code or "").strip(),
        "nse_symbol": (nse_symbol or "").strip(),
        "letter_date": letter_date,
        "annexures": cleaned_annexures,
    }


def _parse_markdown_table(block: str) -> Optional[List[List[str]]]:
    """Parse a pipe-delimited markdown table block into rows.
    Returns the data rows (header first, separator stripped) or None
    if `block` isn't a valid table."""
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    if not all("|" in l for l in lines):
        return None
    # Drop the markdown separator row (---|---|---)
    rows = []
    for l in lines:
        # Strip leading/trailing pipes then split
        inner = l.strip()
        if inner.startswith("|"): inner = inner[1:]
        if inner.endswith("|"): inner = inner[:-1]
        cells = [c.strip() for c in inner.split("|")]
        # separator row: all cells contain only dashes/colons
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        rows.append(cells)
    if len(rows) < 2:
        return None
    return rows


def _split_body_into_chunks(body: str) -> List[Tuple[str, object]]:
    """Split a body string into a sequence of (kind, payload) chunks where
    kind is 'para' (str payload) or 'table' (list-of-rows payload). This
    lets renderers preserve markdown tables while still flowing prose."""
    out: List[Tuple[str, object]] = []
    blocks = re.split(r"\n\s*\n", body)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        tbl = _parse_markdown_table(block)
        if tbl:
            out.append(("table", tbl))
        else:
            out.append(("para", block))
    return out


def _safe_filename(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")
    return s[:80] or "letter"


def render_pdf(letter: Dict) -> bytes:
    """Render the letter to a PDF using reportlab. Layout follows real
    Indian listed-company filings: company name centered, registered office
    + CIN under it, both BSE & NSE addressed in two columns, subject in
    bold underline, body justified, signature block right-aligned."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
        PageBreak,
    )
    from reportlab.lib import colors

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=letter["subject"][:80] or "SEBI Letter",
        author=letter["company_name"],
    )

    styles = getSampleStyleSheet()
    h_style = ParagraphStyle(
        "header", parent=styles["Title"], alignment=TA_CENTER,
        fontSize=14, leading=18, spaceAfter=2, textColor=colors.black,
    )
    sub_h_style = ParagraphStyle(
        "subheader", parent=styles["Normal"], alignment=TA_CENTER,
        fontSize=9, leading=12, textColor=colors.HexColor("#444444"),
    )
    body_style = ParagraphStyle(
        "body", parent=styles["Normal"], fontSize=11, leading=16,
        alignment=TA_JUSTIFY, spaceAfter=10,
    )
    addr_style = ParagraphStyle(
        "addr", parent=styles["Normal"], fontSize=10, leading=13,
    )
    subject_style = ParagraphStyle(
        "subject", parent=styles["Normal"], fontSize=11, leading=16,
        spaceBefore=6, spaceAfter=12, textColor=colors.black,
    )
    sig_style = ParagraphStyle(
        "sig", parent=styles["Normal"], fontSize=11, leading=15,
    )

    story = []

    # ── Letterhead ──
    story.append(Paragraph(letter["company_name"] or "&nbsp;", h_style))
    if letter["registered_office"]:
        story.append(Paragraph(letter["registered_office"], sub_h_style))
    if letter["cin"]:
        story.append(Paragraph(f"CIN: {letter['cin']}", sub_h_style))
    story.append(Spacer(1, 4 * mm))
    # Horizontal rule
    rule = Table([[""]], colWidths=[170 * mm], rowHeights=[0.6])
    rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.6, colors.black)]))
    story.append(rule)
    story.append(Spacer(1, 6 * mm))

    # ── Date ──
    story.append(Paragraph(f"Date: {letter['letter_date']}", body_style))

    # ── Two-column listing addresses ──
    bse_lines = list(LISTING_ADDRESSES[0]["lines"])
    nse_lines = list(LISTING_ADDRESSES[1]["lines"])
    if letter["bse_code"]:
        bse_lines.append(f"<b>Scrip Code:</b> {letter['bse_code']}")
    if letter["nse_symbol"]:
        nse_lines.append(f"<b>Symbol:</b> {letter['nse_symbol']}")
    bse_paras = [Paragraph(l, addr_style) for l in bse_lines]
    nse_paras = [Paragraph(l, addr_style) for l in nse_lines]
    addr_table = Table([[bse_paras, nse_paras]], colWidths=[85 * mm, 85 * mm])
    addr_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(addr_table)
    story.append(Spacer(1, 6 * mm))

    # ── Subject ──
    if letter["subject"]:
        story.append(Paragraph(
            f"<b><u>Subject: {letter['subject']}</u></b>", subject_style))

    # ── Greeting ──
    story.append(Paragraph("Dear Sir / Madam,", body_style))

    # ── Body — preserve paragraph breaks + render markdown tables ──
    def _flow_body(body_text):
        flow = []
        for kind, payload in _split_body_into_chunks(body_text or ""):
            if kind == "table":
                rows = payload
                col_count = max(len(r) for r in rows)
                # Pad short rows
                norm_rows = [r + [""] * (col_count - len(r)) for r in rows]
                # Wrap each cell as a Paragraph for proper wrapping
                table_data = [
                    [Paragraph(c, addr_style) for c in r] for r in norm_rows
                ]
                t = Table(
                    table_data,
                    colWidths=[(170 / col_count) * mm] * col_count,
                    repeatRows=1,
                )
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94A3B8")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                flow.append(t)
                flow.append(Spacer(1, 6 * mm))
            else:
                para_html = payload.replace("\n", "<br/>")
                flow.append(Paragraph(para_html, body_style))
        return flow

    story.extend(_flow_body(letter["body"]))

    # ── Sign-off ──
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Yours faithfully,", body_style))
    story.append(Spacer(1, 2 * mm))
    if letter["company_name"]:
        story.append(Paragraph(f"<b>For {letter['company_name']}</b>", sig_style))
    story.append(Spacer(1, 14 * mm))   # space for physical signature
    if letter["signer_name"]:
        story.append(Paragraph(f"<b>{letter['signer_name']}</b>", sig_style))
    if letter["signer_designation"]:
        story.append(Paragraph(letter["signer_designation"], sig_style))

    # ── Annexures: each on its own page, with a centered title heading ──
    annexure_title_style = ParagraphStyle(
        "annx_title", parent=styles["Title"], alignment=TA_CENTER,
        fontSize=13, leading=18, spaceAfter=10,
    )
    for annx in letter.get("annexures", []):
        story.append(PageBreak())
        # Repeat the company name in small at the top so each annexure
        # page is self-identifying when paginated.
        story.append(Paragraph(letter["company_name"] or "&nbsp;", sub_h_style))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(f"<b><u>{annx['title']}</u></b>", annexure_title_style))
        story.append(Spacer(1, 2 * mm))
        story.extend(_flow_body(annx["body"]))

    doc.build(story)
    return buf.getvalue()


def render_docx(letter: Dict) -> bytes:
    """Render the same letter to a Word .docx file using python-docx."""
    from docx import Document
    from docx.shared import Pt, Mm, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    # Page margins ~A4 standard
    section = doc.sections[0]
    section.top_margin = Mm(20)
    section.bottom_margin = Mm(20)
    section.left_margin = Mm(20)
    section.right_margin = Mm(20)

    # ── Letterhead ──
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(letter["company_name"] or "")
    run.bold = True
    run.font.size = Pt(16)

    if letter["registered_office"]:
        p2 = doc.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r2 = p2.add_run(letter["registered_office"])
        r2.font.size = Pt(9)
        r2.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
    if letter["cin"]:
        p3 = doc.add_paragraph()
        p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r3 = p3.add_run(f"CIN: {letter['cin']}")
        r3.font.size = Pt(9)
        r3.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    # Horizontal rule via bottom border on a paragraph
    rule = doc.add_paragraph()
    pPr = rule._p.get_or_add_pPr()
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:color"), "000000")
    pBdr.append(bottom)
    pPr.append(pBdr)

    # ── Date ──
    p_date = doc.add_paragraph()
    p_date.add_run(f"Date: {letter['letter_date']}").font.size = Pt(11)

    # ── Two-column addresses via table ──
    bse_lines = list(LISTING_ADDRESSES[0]["lines"])
    nse_lines = list(LISTING_ADDRESSES[1]["lines"])
    if letter["bse_code"]:
        bse_lines.append(f"Scrip Code: {letter['bse_code']}")
    if letter["nse_symbol"]:
        nse_lines.append(f"Symbol: {letter['nse_symbol']}")

    table = doc.add_table(rows=1, cols=2)
    table.autofit = True
    cells = table.rows[0].cells
    cells[0].text = ""
    cells[1].text = ""
    for i, lines in enumerate([bse_lines, nse_lines]):
        cell = cells[i]
        # First paragraph goes in cell.paragraphs[0]; subsequent we add.
        cell.paragraphs[0].add_run(lines[0]).font.size = Pt(10)
        for ln in lines[1:]:
            cp = cell.add_paragraph()
            cp.add_run(ln).font.size = Pt(10)

    doc.add_paragraph()

    # ── Subject ──
    if letter["subject"]:
        p_subj = doc.add_paragraph()
        run = p_subj.add_run(f"Subject: {letter['subject']}")
        run.bold = True
        run.underline = True
        run.font.size = Pt(11)

    # ── Greeting ──
    doc.add_paragraph().add_run("Dear Sir / Madam,").font.size = Pt(11)

    # ── Body — preserve paragraph breaks + render markdown tables ──
    def _docx_flow_body(d, body_text):
        for kind, payload in _split_body_into_chunks(body_text or ""):
            if kind == "table":
                rows = payload
                col_count = max(len(r) for r in rows)
                norm = [r + [""] * (col_count - len(r)) for r in rows]
                tbl = d.add_table(rows=len(norm), cols=col_count)
                tbl.style = "Light Grid Accent 1"
                for i, row in enumerate(norm):
                    for j, cell in enumerate(row):
                        tc = tbl.rows[i].cells[j]
                        tc.text = ""
                        run = tc.paragraphs[0].add_run(cell)
                        run.font.size = Pt(10)
                        if i == 0:
                            run.bold = True
                d.add_paragraph()
            else:
                p = d.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                p.add_run(payload).font.size = Pt(11)

    _docx_flow_body(doc, letter["body"])

    # ── Sign-off ──
    doc.add_paragraph()
    doc.add_paragraph().add_run("Yours faithfully,").font.size = Pt(11)
    if letter["company_name"]:
        p_for = doc.add_paragraph()
        rf = p_for.add_run(f"For {letter['company_name']}")
        rf.bold = True
        rf.font.size = Pt(11)
    # Space for signature
    for _ in range(3):
        doc.add_paragraph()
    if letter["signer_name"]:
        p_sn = doc.add_paragraph()
        rs = p_sn.add_run(letter["signer_name"])
        rs.bold = True
        rs.font.size = Pt(11)
    if letter["signer_designation"]:
        doc.add_paragraph().add_run(letter["signer_designation"]).font.size = Pt(11)

    # ── Annexures: each on a new page with a centered title heading ──
    from docx.enum.text import WD_BREAK
    for annx in letter.get("annexures", []):
        # Page break
        last = doc.add_paragraph()
        last.add_run().add_break(WD_BREAK.PAGE)
        # Tiny header repeating the company name
        ph = doc.add_paragraph()
        ph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rh = ph.add_run(letter["company_name"] or "")
        rh.font.size = Pt(9)
        rh.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
        # Annexure title (centered, bold + underline)
        pt = doc.add_paragraph()
        pt.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rt = pt.add_run(annx["title"])
        rt.bold = True
        rt.underline = True
        rt.font.size = Pt(13)
        doc.add_paragraph()
        _docx_flow_body(doc, annx["body"])

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def export_filename(letter: Dict, ext: str) -> str:
    base = _safe_filename(
        f"{letter['company_name'][:30]}_{letter.get('subject','letter')[:50]}"
    )
    return f"{base}.{ext}"
