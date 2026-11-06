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

    # ══════════════════════════════════════════════════════════════════
    # MCA / COMPANIES ACT — board & general meeting documents
    # (standalone documents, not exchange letters → doc_kind set)
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "board_notice", "doc_kind": "notice",
        "label": "Notice of Board Meeting (s.173)",
        "category": "Companies Act — Meetings & Notices",
        "subject_default": "Notice of Meeting of the Board of Directors",
        "keywords": ["board meeting notice", "section 173", "notice of board", "ss-1"],
        "body_hint": "Date/time/venue/mode of the board meeting, ≥7 days' notice, list of agenda items, note on participation by video-conference.",
        "annexure_hints": "Agenda with notes on each item; draft resolutions.",
    },
    {
        "id": "agm_notice", "doc_kind": "notice",
        "label": "Notice of Annual General Meeting (s.101)",
        "category": "Companies Act — Meetings & Notices",
        "subject_default": "Notice of the Annual General Meeting",
        "keywords": ["agm notice", "annual general meeting", "section 101", "section 102", "explanatory statement"],
        "body_hint": "Day/date/time/venue/mode, ordinary business (adoption of accounts, dividend, director rotation, auditor), any special business as numbered resolutions, e-voting details, cut-off date.",
        "annexure_hints": "Explanatory Statement under s.102 for each special-business item; route map / e-voting instructions.",
    },
    {
        "id": "egm_notice", "doc_kind": "notice",
        "label": "Notice of Extraordinary General Meeting (s.100)",
        "category": "Companies Act — Meetings & Notices",
        "subject_default": "Notice of the Extraordinary General Meeting",
        "keywords": ["egm notice", "extraordinary general meeting", "section 100", "section 102"],
        "body_hint": "Meeting particulars, the special-business resolutions (ordinary/special), e-voting details.",
        "annexure_hints": "Explanatory Statement under s.102 for each item.",
    },
    {
        "id": "postal_ballot", "doc_kind": "notice",
        "label": "Postal Ballot Notice (s.110)",
        "category": "Companies Act — Meetings & Notices",
        "subject_default": "Postal Ballot Notice",
        "keywords": ["postal ballot", "section 110", "remote e-voting"],
        "body_hint": "Resolutions proposed by postal ballot, remote e-voting window (start/end), scrutinizer details, last date for votes.",
        "annexure_hints": "Explanatory Statement under s.102; postal ballot form.",
    },
    {
        "id": "board_minutes", "doc_kind": "minutes",
        "label": "Minutes of Board Meeting (SS-1)",
        "category": "Companies Act — Minutes",
        "subject_default": "Minutes of the Meeting of the Board of Directors",
        "keywords": ["board minutes", "ss-1", "minutes of board meeting"],
        "body_hint": "Serial no., day/date/time/venue, directors present (incl. via VC), quorum, leave of absence, confirmation of previous minutes, each agenda item with the decision/resolution, time of conclusion.",
        "annexure_hints": "Resolutions passed verbatim; attendance register reference.",
    },
    {
        "id": "agm_minutes", "doc_kind": "minutes",
        "label": "Minutes of General Meeting (SS-2)",
        "category": "Companies Act — Minutes",
        "subject_default": "Minutes of the General Meeting",
        "keywords": ["general meeting minutes", "ss-2", "agm minutes", "egm minutes"],
        "body_hint": "Meeting type/date/time/venue, chairman, members present & quorum, business transacted, resolutions with for/against e-voting numbers, time of conclusion.",
        "annexure_hints": "Scrutinizer's report; resolutions as passed.",
    },
    {
        "id": "board_resolution", "doc_kind": "resolution",
        "label": "Board Resolution (general)",
        "category": "Companies Act — Resolutions",
        "subject_default": "Certified True Copy of the Board Resolution",
        "keywords": ["board resolution", "resolved that", "certified true copy", "ctc"],
        "body_hint": "State the matter approved by the Board and the authorisation to a director/CS/authorised signatory to act. Use 'RESOLVED THAT' language.",
        "annexure_hints": "None usually; attach the underlying document if referenced.",
    },
    {
        "id": "special_resolution", "doc_kind": "resolution",
        "label": "Special Resolution (s.114)",
        "category": "Companies Act — Resolutions",
        "subject_default": "Special Resolution",
        "keywords": ["special resolution", "section 114", "75%", "members"],
        "body_hint": "The special-business matter requiring a 75% majority (e.g. alteration of MoA/AoA, related-party approval, ESOP, borrowing/s.180). Use 'RESOLVED THAT' / 'RESOLVED FURTHER THAT'.",
        "annexure_hints": "Explanatory statement reference.",
    },
    {
        "id": "mgt14_resolution", "doc_kind": "resolution",
        "label": "Resolution for MGT-14 Filing (s.117)",
        "category": "Companies Act — Resolutions",
        "subject_default": "Resolution under Section 117 read with Section 179(3)",
        "keywords": ["mgt-14", "section 117", "section 179(3)", "board resolution filing"],
        "body_hint": "The board/special resolution to be filed with the ROC in MGT-14 (e.g. accounts approval, borrowing powers, investment, loans). Resolution language + effective authorisation.",
        "annexure_hints": "Certified true copy for ROC attachment.",
    },

    # ── MCA / Companies Act — covering content for ROC e-forms ──
    {
        "id": "dir12_covering", "doc_kind": "document",
        "label": "DIR-12 — Director/KMP Change (covering + resolution)",
        "category": "Companies Act — ROC Filings",
        "subject_default": "Appointment / Cessation of Director / KMP — DIR-12",
        "keywords": ["dir-12", "appointment", "resignation", "director", "kmp", "section 168", "section 170"],
        "body_hint": "Particulars of the appointment/cessation/change, effective date, the enabling board/members resolution, and consent (DIR-2)/declaration references for ROC filing within 30 days.",
        "annexure_hints": "Board resolution (CTC); DIR-2 consent; DIR-8 declaration; letter of resignation if applicable.",
    },
    {
        "id": "adt1_covering", "doc_kind": "document",
        "label": "ADT-1 — Auditor Appointment (covering + resolution)",
        "category": "Companies Act — ROC Filings",
        "subject_default": "Appointment of Statutory Auditor — ADT-1",
        "keywords": ["adt-1", "auditor appointment", "section 139"],
        "body_hint": "Name & firm of the auditor, period of appointment, AGM date, members' resolution, auditor's eligibility certificate reference; file within 15 days of AGM.",
        "annexure_hints": "Members' resolution (CTC); auditor's written consent & eligibility certificate (s.139/141).",
    },
    {
        "id": "mbp1_disclosure", "doc_kind": "document",
        "label": "MBP-1 — Disclosure of Interest by Director (s.184)",
        "category": "Companies Act — Director Disclosures",
        "subject_default": "Disclosure of Interest by Director in Form MBP-1",
        "keywords": ["mbp-1", "disclosure of interest", "section 184"],
        "body_hint": "Director's name, the bodies corporate/firms/other entities in which the director is interested and the nature/extent of interest, given at the first board meeting of the FY.",
        "annexure_hints": "Schedule of interested entities.",
    },
    {
        "id": "dir8_declaration", "doc_kind": "document",
        "label": "DIR-8 — Declaration of Non-Disqualification (s.164)",
        "category": "Companies Act — Director Disclosures",
        "subject_default": "Declaration under Section 164(2) and Section 143(3)(g) — Form DIR-8",
        "keywords": ["dir-8", "non-disqualification", "section 164"],
        "body_hint": "Director declares they are not disqualified under s.164(1)/(2) for appointment/continuation; given annually and on appointment.",
        "annexure_hints": "None.",
    },
    {
        "id": "independence_declaration", "doc_kind": "document",
        "label": "Declaration of Independence (s.149(7))",
        "category": "Companies Act — Director Disclosures",
        "subject_default": "Declaration of Independence under Section 149(6)/(7)",
        "keywords": ["independent director", "declaration of independence", "section 149"],
        "body_hint": "Independent director confirms they meet the s.149(6) criteria and IICA databank registration; given at first board meeting and first meeting of each FY.",
        "annexure_hints": "IICA registration reference.",
    },

    # ── CSR / Governance policies ──
    {
        "id": "csr_resolution", "doc_kind": "resolution",
        "label": "CSR — Committee/Board Resolution & Action Plan (s.135)",
        "category": "Companies Act — CSR",
        "subject_default": "Resolution approving the Annual CSR Action Plan",
        "keywords": ["csr", "section 135", "csr policy", "action plan"],
        "body_hint": "Approve the CSR policy/annual action plan, the 2% spend computation, ongoing projects, and implementing agencies (CSR-1 registered).",
        "annexure_hints": "Annual Action Plan; project-wise budget table.",
    },
    {
        "id": "rpt_policy", "doc_kind": "document",
        "label": "Related Party Transactions Policy",
        "category": "Governance — Policies",
        "subject_default": "Policy on Related Party Transactions",
        "keywords": ["rpt policy", "related party", "regulation 23", "section 188"],
        "body_hint": "Definitions, materiality thresholds (2% / ₹1,000 cr), the audit-committee/board/shareholder approval gates, omnibus approval framework, and review cadence.",
        "annexure_hints": "Approval matrix table.",
    },

    # ══════════════════════════════════════════════════════════════════
    # FEMA / RBI — covering documents
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "fcgpr_covering", "doc_kind": "document",
        "label": "FC-GPR — Foreign Investment Reporting (covering)",
        "category": "FEMA / RBI",
        "subject_default": "Reporting of Foreign Direct Investment in Form FC-GPR",
        "keywords": ["fc-gpr", "fdi", "firms", "foreign investment", "allotment"],
        "body_hint": "Allottee (non-resident) details, instrument, amount, date of allotment, FIRC/KYC references, valuation certificate, and the entry-route/sectoral-cap confirmation; file on FIRMS within 30 days.",
        "annexure_hints": "Board resolution; valuation certificate; CS certificate; FIRC & KYC.",
    },
    {
        "id": "fla_return", "doc_kind": "document",
        "label": "FLA Return — Foreign Liabilities & Assets",
        "category": "FEMA / RBI",
        "subject_default": "Annual Return on Foreign Liabilities and Assets",
        "keywords": ["fla", "foreign liabilities and assets", "flair"],
        "body_hint": "Position as on 31 March of FDI received and overseas investment, paid-up capital, reserves; filed on the RBI FLAIR portal by 15 July.",
        "annexure_hints": "Financials summary table.",
    },

    # ══════════════════════════════════════════════════════════════════
    # COMPANIES ACT — STATUTORY REGISTERS (company-specific records)
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "reg_members", "doc_kind": "document",
        "label": "Register of Members (MGT-1, s.88)",
        "category": "Companies Act — Statutory Registers",
        "subject_default": "Register of Members maintained under Section 88 in Form MGT-1",
        "keywords": ["register of members", "mgt-1", "section 88"],
        "body_hint": "Tabular register: folio, member name & address, PAN, shares held (class/number/distinctive nos.), date of becoming/ceasing member, nominee. Maintained at the registered office.",
        "annexure_hints": "Members table (markdown pipe-table).",
    },
    {
        "id": "reg_directors", "doc_kind": "document",
        "label": "Register of Directors & KMP and their Shareholding (s.170)",
        "category": "Companies Act — Statutory Registers",
        "subject_default": "Register of Directors and Key Managerial Personnel under Section 170",
        "keywords": ["register of directors", "kmp", "section 170"],
        "body_hint": "Each director/KMP: DIN/PAN, name, designation, date of appointment/cessation, other directorships, and shareholding in the company & group.",
        "annexure_hints": "Directors/KMP table.",
    },
    {
        "id": "reg_charges", "doc_kind": "document",
        "label": "Register of Charges (CHG-7, s.85)",
        "category": "Companies Act — Statutory Registers",
        "subject_default": "Register of Charges maintained under Section 85 in Form CHG-7",
        "keywords": ["register of charges", "chg-7", "section 85"],
        "body_hint": "Each charge: charge ID, charge-holder, amount secured, date of creation/modification/satisfaction, property charged, terms.",
        "annexure_hints": "Charges table.",
    },
    {
        "id": "reg_rpt_contracts", "doc_kind": "document",
        "label": "Register of Contracts with Related Parties (MBP-4, s.189)",
        "category": "Companies Act — Statutory Registers",
        "subject_default": "Register of Contracts or Arrangements in which Directors are Interested (MBP-4)",
        "keywords": ["mbp-4", "register of contracts", "section 189", "related party"],
        "body_hint": "Each contract/arrangement under s.184/188: parties, nature, duration, value, the interested director, and date of board/members approval.",
        "annexure_hints": "Contracts table.",
    },
    {
        "id": "reg_loans_investments", "doc_kind": "document",
        "label": "Register of Loans, Guarantees & Investments (MBP-2, s.186)",
        "category": "Companies Act — Statutory Registers",
        "subject_default": "Register of Loans, Guarantee, Security and Investments under Section 186 (MBP-2)",
        "keywords": ["mbp-2", "register of loans", "section 186", "investments"],
        "body_hint": "Each loan/guarantee/security/investment: party, amount, date, purpose, rate, security, board/special-resolution authority.",
        "annexure_hints": "Loans & investments table.",
    },
    {
        "id": "reg_sbo", "doc_kind": "document",
        "label": "Register of Significant Beneficial Owners (BEN-3, s.90)",
        "category": "Companies Act — Statutory Registers",
        "subject_default": "Register of Significant Beneficial Owners under Section 90 in Form BEN-3",
        "keywords": ["ben-3", "register of sbo", "significant beneficial owner", "section 90"],
        "body_hint": "Each SBO: name, address, the nature/extent of beneficial interest (≥10%), date of declaration (BEN-1), and the registered owner through whom held.",
        "annexure_hints": "SBO table.",
    },
    {
        "id": "reg_share_transfer", "doc_kind": "document",
        "label": "Register of Renewed & Duplicate Share Certificates (SH-2)",
        "category": "Companies Act — Statutory Registers",
        "subject_default": "Register of Renewed and Duplicate Share Certificates in Form SH-2",
        "keywords": ["sh-2", "duplicate share certificate", "renewed certificate"],
        "body_hint": "Each renewed/duplicate certificate: original cert no., reason, new cert no., distinctive numbers, date, board approval reference.",
        "annexure_hints": "Certificates table.",
    },
    {
        "id": "reg_esop", "doc_kind": "document",
        "label": "Register of ESOP / Sweat Equity (SH-6 / SH-3)",
        "category": "Companies Act — Statutory Registers",
        "subject_default": "Register of Employee Stock Options in Form SH-6",
        "keywords": ["sh-6", "esop register", "sweat equity", "sh-3"],
        "body_hint": "Each grant: employee, options granted, exercise price, vesting schedule, options exercised/lapsed, shares allotted.",
        "annexure_hints": "ESOP grants table.",
    },

    # ══════════════════════════════════════════════════════════════════
    # COMPANIES ACT — more ROC e-forms (covering + resolution content)
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "dpt3_covering", "doc_kind": "document",
        "label": "DPT-3 — Return of Deposits (covering)",
        "category": "Companies Act — ROC Filings",
        "subject_default": "Return of Deposits and Particulars not considered as Deposits — DPT-3",
        "keywords": ["dpt-3", "return of deposits", "rule 16"],
        "body_hint": "Outstanding receipt of money/loan as on 31 March, the deposits vs exempted-deposits split, and the auditor's certificate reference; file by 30 June.",
        "annexure_hints": "Auditor's certificate; particulars schedule.",
    },
    {
        "id": "msme1_covering", "doc_kind": "document",
        "label": "MSME-1 — Half-yearly MSME Dues (covering)",
        "category": "Companies Act — ROC Filings",
        "subject_default": "Half-yearly Return of Outstanding Dues to MSME Suppliers — MSME-1",
        "keywords": ["msme-1", "msme dues", "section 405"],
        "body_hint": "Outstanding dues to MSME suppliers beyond 45 days as on the half-year, supplier-wise amount & period of delay, and reasons; due 30 Apr & 31 Oct.",
        "annexure_hints": "Supplier-wise outstanding table.",
    },
    {
        "id": "ben2_covering", "doc_kind": "document",
        "label": "BEN-2 — SBO Return (covering)",
        "category": "Companies Act — ROC Filings",
        "subject_default": "Return to the Registrar regarding Significant Beneficial Owners — BEN-2",
        "keywords": ["ben-2", "sbo return", "section 90"],
        "body_hint": "Particulars of each SBO from the BEN-1 declarations received, declaration dates; file within 30 days of receiving BEN-1.",
        "annexure_hints": "BEN-1 declarations summary.",
    },
    {
        "id": "pas3_covering", "doc_kind": "document",
        "label": "PAS-3 — Return of Allotment (covering)",
        "category": "Companies Act — ROC Filings",
        "subject_default": "Return of Allotment of Securities — PAS-3",
        "keywords": ["pas-3", "return of allotment", "section 39"],
        "body_hint": "Class & number of securities allotted, allottees, consideration, date of allotment, the enabling board/members resolution; file within 30 days (15 for some classes).",
        "annexure_hints": "List of allottees; board resolution; valuation report (if applicable).",
    },
    {
        "id": "chg1_covering", "doc_kind": "document",
        "label": "CHG-1 / CHG-4 — Charge Creation / Satisfaction (covering)",
        "category": "Companies Act — ROC Filings",
        "subject_default": "Registration of Charge / Satisfaction of Charge — CHG-1 / CHG-4",
        "keywords": ["chg-1", "chg-4", "charge", "section 77", "section 82"],
        "body_hint": "Charge-holder, amount secured, date & type of charge instrument, property charged (CHG-1) or date & confirmation of satisfaction (CHG-4); file within 30 days.",
        "annexure_hints": "Charge instrument; board resolution; NOC (for satisfaction).",
    },
    {
        "id": "mr1_kmp", "doc_kind": "document",
        "label": "MR-1 — Appointment of KMP / Managing Director (s.196/203)",
        "category": "Companies Act — ROC Filings",
        "subject_default": "Return of Appointment of Managing Director / KMP — MR-1",
        "keywords": ["mr-1", "kmp appointment", "managing director", "section 203"],
        "body_hint": "Particulars of the MD/WTD/Manager/KMP appointment, terms of remuneration, board & members approval references; file within 60 days.",
        "annexure_hints": "Board/members resolution; terms of appointment.",
    },
    {
        "id": "inc20a_covering", "doc_kind": "document",
        "label": "INC-20A — Commencement of Business",
        "category": "Companies Act — ROC Filings",
        "subject_default": "Declaration for Commencement of Business — INC-20A",
        "keywords": ["inc-20a", "commencement of business", "section 10a"],
        "body_hint": "Confirmation that subscribers have paid the subscription money and the company has filed verification of registered office; file within 180 days of incorporation.",
        "annexure_hints": "Proof of subscription receipt; bank statement.",
    },

    # ══════════════════════════════════════════════════════════════════
    # GOVERNANCE — mandatory policies (listed-company suite)
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "pol_vigil", "doc_kind": "document",
        "label": "Whistle-Blower / Vigil Mechanism Policy (s.177)",
        "category": "Governance — Policies",
        "subject_default": "Whistle-Blower Policy and Vigil Mechanism",
        "keywords": ["whistle blower", "vigil mechanism", "section 177", "regulation 22"],
        "body_hint": "Scope, reportable matters, reporting channels, protection against victimisation, audit-committee oversight, and direct access to the chairperson.",
        "annexure_hints": "Reporting form / escalation matrix.",
    },
    {
        "id": "pol_nrc", "doc_kind": "document",
        "label": "Nomination & Remuneration Policy (s.178)",
        "category": "Governance — Policies",
        "subject_default": "Nomination and Remuneration Policy",
        "keywords": ["nomination and remuneration", "nrc policy", "section 178", "regulation 19"],
        "body_hint": "Criteria for director/KMP/senior-management appointment, independence, board diversity, remuneration framework, and performance evaluation.",
        "annexure_hints": "Remuneration matrix.",
    },
    {
        "id": "pol_rpt", "doc_kind": "document",
        "label": "Policy on Related Party Transactions",
        "category": "Governance — Policies",
        "subject_default": "Policy on Related Party Transactions",
        "keywords": ["related party policy", "regulation 23", "section 188", "rpt policy"],
        "body_hint": "Definitions, materiality thresholds (2% / ₹1,000 cr), approval gates (audit committee / board / shareholders), omnibus framework, and review cadence.",
        "annexure_hints": "Approval matrix.",
    },
    {
        "id": "pol_material_subsidiary", "doc_kind": "document",
        "label": "Material Subsidiary Policy (Reg 16/24)",
        "category": "Governance — Policies",
        "subject_default": "Policy for Determining Material Subsidiaries",
        "keywords": ["material subsidiary", "regulation 16", "regulation 24"],
        "body_hint": "Definition of material subsidiary (10%/20% thresholds), governance over subsidiaries, and disposal/encumbrance safeguards.",
        "annexure_hints": "List of subsidiaries with materiality flag.",
    },
    {
        "id": "pol_dividend", "doc_kind": "document",
        "label": "Dividend Distribution Policy (Reg 43A)",
        "category": "Governance — Policies",
        "subject_default": "Dividend Distribution Policy",
        "keywords": ["dividend distribution policy", "regulation 43a"],
        "body_hint": "Circumstances for declaring dividend, financial/internal/external factors, retention policy, and utilisation of retained earnings (top 1,000 listed).",
        "annexure_hints": "None.",
    },
    {
        "id": "pol_risk", "doc_kind": "document",
        "label": "Risk Management Policy (Reg 21)",
        "category": "Governance — Policies",
        "subject_default": "Risk Management Policy",
        "keywords": ["risk management policy", "regulation 21", "rmc"],
        "body_hint": "Risk framework, categories (strategic/operational/financial/ESG/cyber), risk-management committee oversight, and monitoring cadence.",
        "annexure_hints": "Risk register template.",
    },
    {
        "id": "pol_preservation", "doc_kind": "document",
        "label": "Document Preservation & Archival Policy (Reg 9/30(8))",
        "category": "Governance — Policies",
        "subject_default": "Policy on Preservation of Documents and Archival",
        "keywords": ["preservation of documents", "archival policy", "regulation 9", "regulation 30(8)"],
        "body_hint": "Categorisation of documents (permanent vs ≥8 years), retention periods, mode of preservation, and website-archival of disclosures.",
        "annexure_hints": "Retention schedule.",
    },
    {
        "id": "pit_code_conduct", "doc_kind": "document",
        "label": "Code of Conduct for Insider Trading (PIT)",
        "category": "Governance — Policies",
        "subject_default": "Code of Conduct to Regulate, Monitor and Report Trading by Designated Persons",
        "keywords": ["pit code", "code of conduct", "insider trading", "trading window", "upsi"],
        "body_hint": "Designated persons, trading window/closure, pre-clearance, contra-trade restriction, UPSI handling, SDD, and the code for fair disclosure of UPSI.",
        "annexure_hints": "Pre-clearance form; designated-persons list.",
    },

    # ══════════════════════════════════════════════════════════════════
    # COMPANIES ACT — reports, certificates & meeting support
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "directors_report", "doc_kind": "document",
        "label": "Board's / Directors' Report (s.134)",
        "category": "Companies Act — Reports",
        "subject_default": "Board's Report for the Financial Year",
        "keywords": ["directors report", "boards report", "section 134"],
        "body_hint": "State of affairs, financial summary, dividend, reserves, changes in directors/KMP, s.134(3) disclosures (s.186 loans, s.188 RPTs, CSR, conservation of energy, risk management, board evaluation), and the responsibility statement.",
        "annexure_hints": "AOC-2 (RPTs); MGT-9/annual-return extract; secretarial audit report; CSR report; conservation-of-energy annexure.",
    },
    {
        "id": "secretarial_audit_mr3", "doc_kind": "document",
        "label": "Secretarial Audit Report (MR-3, s.204)",
        "category": "Companies Act — Reports",
        "subject_default": "Secretarial Audit Report in Form MR-3",
        "keywords": ["secretarial audit", "mr-3", "section 204"],
        "body_hint": "PCS report on compliance with the Companies Act, SEBI regs, FEMA and other applicable laws for the financial year, with qualifications/observations.",
        "annexure_hints": "List of laws examined; management representation.",
    },
    {
        "id": "cg_report", "doc_kind": "document",
        "label": "Corporate Governance Report (Reg 34(3), Sch V)",
        "category": "Companies Act — Reports",
        "subject_default": "Report on Corporate Governance",
        "keywords": ["corporate governance report", "schedule v", "regulation 34"],
        "body_hint": "Board composition & meetings, committee details, remuneration, general meetings, means of communication, shareholder information, and the compliance discussion per Schedule V.",
        "annexure_hints": "Board/committee attendance tables; PCS compliance certificate.",
    },
    {
        "id": "proxy_form", "doc_kind": "document",
        "label": "Proxy Form (MGT-11)",
        "category": "Companies Act — Meeting Support",
        "subject_default": "Proxy Form pursuant to Section 105(6) — Form MGT-11",
        "keywords": ["proxy form", "mgt-11", "section 105"],
        "body_hint": "Member & proxy particulars, meeting reference, resolution-wise for/against table, and the revenue-stamp/signature block.",
        "annexure_hints": "Resolution table.",
    },
    {
        "id": "scrutinizer_report", "doc_kind": "document",
        "label": "Scrutinizer's Report (e-voting / poll)",
        "category": "Companies Act — Meeting Support",
        "subject_default": "Scrutinizer's Report on Remote E-Voting and Voting at the Meeting",
        "keywords": ["scrutinizer report", "e-voting", "poll", "section 109"],
        "body_hint": "Resolution-wise votes (remote + at-meeting): votes for/against, invalid, percentage, and whether the resolution is carried with the requisite majority.",
        "annexure_hints": "Resolution-wise voting results table.",
    },
    {
        "id": "compliance_certificate_pcs", "doc_kind": "certificate",
        "label": "PCS Compliance Certificate (Reg 34(3)/Sch V)",
        "category": "Companies Act — Reports",
        "subject_default": "Certificate of Compliance with Conditions of Corporate Governance",
        "keywords": ["compliance certificate", "corporate governance certificate", "pcs"],
        "body_hint": "PCS certifies the company has complied with the corporate-governance conditions in the LODR for the financial year.",
        "annexure_hints": "None.",
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

# gemini-2.0-flash was retired by Google (404 NOT_FOUND); gemini-flash-latest
# tracks the current Flash so a future retirement does not break drafting.
_FREE_TIER_MODELS = {"gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest"}


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
    for fb in ("gemini-2.5-flash-lite", "gemini-flash-latest"):
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
    doc_kind = ftype.get("doc_kind", "") or "letter"

    # The document's voice changes with its kind. A board resolution is not a
    # letter to the exchange; minutes are not a notice. Steer the model.
    _KIND_INTRO = {
        "letter": (f"You are drafting a SEBI compliance letter to the stock exchanges "
                   f"for {company_name}. Letter type: {label}."),
        "resolution": (f"You are drafting a formal RESOLUTION for {company_name} "
                       f"({label}). Write it in proper resolution language — "
                       "'RESOLVED THAT …', and where apt 'RESOLVED FURTHER THAT …'. "
                       "Include the authorisation to a director/CS to give effect. "
                       "No salutation, no 'we wish to inform'."),
        "notice": (f"You are drafting a MEETING NOTICE for {company_name} ({label}). "
                   "Include the meeting date/time/venue/mode placeholders, the agenda "
                   "items as numbered resolutions (ordinary/special as appropriate), "
                   "and an Explanatory Statement under s.102 of the Companies Act for "
                   "special-business items."),
        "minutes": (f"You are drafting MINUTES for {company_name} ({label}), compliant "
                    "with Secretarial Standard SS-1 (board) / SS-2 (general). Record "
                    "attendance, quorum, items considered, decisions, and the resolutions "
                    "passed. Factual, past tense, no marketing language."),
        "document": (f"You are drafting a governance document for {company_name} ({label}). "
                     "Use formal, structured policy/certificate language as the type "
                     "requires. No exchange salutation."),
        "certificate": (f"You are drafting a CERTIFICATE for {company_name} ({label}). "
                        "Use precise certifying language ('I/We hereby certify that …') "
                        "with the basis and period covered."),
    }
    intro = _KIND_INTRO.get(doc_kind, _KIND_INTRO["letter"])

    prompt = (
        f"{intro}\n\n"
        f"Body guidance: {body_hint}\n"
        f"Typical annexures for this type: {annexure_hints}\n\n"
        f"What's NEW that this document must convey:\n{new_info.strip()}\n"
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


# Which doc kinds are addressed to the stock exchanges (BSE/NSE letter format).
_LETTER_KINDS = ("", "letter", "exchange_letter", None)
# Close phrase per doc kind.
_CLOSE_PHRASE = {
    "resolution": "By order of the Board",
    "notice": "By order of the Board",
    "minutes": "",
    "document": "",
    "certificate": "",
}


def doc_is_addressed(doc_kind):
    return doc_kind in _LETTER_KINDS


def build_letter_data(company_name: str, registered_office: str = "",
                      cin: str = "", subject: str = "", body: str = "",
                      signer_name: str = "", signer_designation: str = "",
                      bse_code: str = "", nse_symbol: str = "",
                      letter_date: str = "",
                      annexures: Optional[List[Dict]] = None,
                      doc_kind: str = "",
                      pdf_meta: Optional[Dict] = None,
                      house: Optional[Dict] = None) -> Dict:
    """Normalize all fields used by both the PDF and DOCX renderers.

    `doc_kind` decides the document shape:
      - letter / "" → SEBI exchange letter (addressed to BSE & NSE, greeting,
        'Yours faithfully').
      - resolution / notice / minutes / document / certificate → a standalone
        company document: centered title, no exchange address, no greeting.

    `annexures` is a list of {title, body}; each renders on its own new page."""
    house = house or {}
    if not letter_date:
        letter_date = datetime.now().strftime("%d %B, %Y")
        if house.get("date_format"):
            from draft_memory import format_date
            letter_date = format_date(datetime.now().date(), house["date_format"])
    cleaned_annexures = []
    for a in (annexures or []):
        title = (a.get("title") or "").strip()
        body_t = (a.get("body") or "").strip()
        if title and body_t:
            cleaned_annexures.append({"title": title, "body": body_t})
    addressed = doc_is_addressed(doc_kind)
    shell = _letter_shell(house, company_name, signer_name, bse_code, nse_symbol, addressed, doc_kind)
    return {
        **shell,
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
        "doc_kind": doc_kind or "letter",
        "addressed": addressed,
        "close_phrase": (shell["house_close"] or "Yours faithfully,") if addressed else _CLOSE_PHRASE.get(doc_kind, ""),
        # PDF Document Properties (what NSE/SEBI portals check). Defaults derive
        # from the letter; the CS can override them on export.
        "pdf_meta": _resolve_pdf_meta(pdf_meta, subject, company_name, cin),
    }


def _letter_shell(house: Dict, company_name: str, signer_name: str, bse_code: str, nse_symbol: str,
                  addressed: bool, doc_kind: str) -> Dict:
    """The parts of a letter around the body, in the company's own format
    when draft memory has learned it, otherwise Delta's defaults."""
    def _lines(key, default):
        v = house.get(key)
        return list(v) if isinstance(v, list) and len(v) >= 3 else list(default)

    bse_lines = _lines("bse_block", LISTING_ADDRESSES[0]["lines"])
    nse_lines = _lines("nse_block", LISTING_ADDRESSES[1]["lines"])
    code_lines = [l for l in (house.get("code_lines") or []) if isinstance(l, str)]
    # Use the learned stock-code block only when it is about this listing.
    if code_lines and not any((bse_code and bse_code in l) or (nse_symbol and nse_symbol.upper() in l.upper())
                              for l in code_lines):
        code_lines = []
    if not code_lines:
        if bse_code:
            bse_lines.append(f"Scrip Code: {bse_code}")
        if nse_symbol:
            nse_lines.append(f"Symbol: {nse_symbol}")
    columns = [bse_lines, nse_lines]
    if house.get("exchange_order") == "nse_first":
        columns.reverse()
    legal = (house.get("for_line") or "").strip()
    membership = ""
    if house.get("sig_membership") and signer_name and house.get("sig_name") and \
            re.sub(r"\W", "", signer_name.lower()) == re.sub(r"\W", "", house["sig_name"].lower()):
        membership = house["sig_membership"]
    label = house.get("date_label")
    return {
        "address_columns": columns,
        "code_lines": code_lines,
        "subject_label": (house.get("subject_label") or "Subject:") if addressed else "",
        "salutation": (house.get("salutation") or "Dear Sir / Madam,") if addressed else "",
        "thanks_line": house.get("thanks_line") or "",
        "house_close": house.get("close_phrase") or "",
        "for_line": legal or (f"For {(company_name or '').strip().upper()}" if company_name else ""),
        "signer_membership": membership,
        "date_label": "" if label == "none" else (label or "Date: "),
        "house_applied": bool(house),
    }


def _resolve_pdf_meta(override, subject, company_name, cin):
    """Build the PDF metadata dict, letting the caller override any field."""
    override = override or {}
    title = (override.get("title") or subject or "Filing").strip()[:200]
    author = (override.get("author") or company_name or "").strip()[:120]
    subj = (override.get("subject") or subject or "").strip()[:200]
    keywords = (override.get("keywords") or "").strip()[:300]
    if not keywords:
        kw = ["SEBI", "compliance", "filing"]
        if company_name:
            kw.insert(0, company_name)
        keywords = ", ".join(kw)
    return {"title": title, "author": author, "subject": subj,
            "keywords": keywords, "creator": "Delta Filings"}


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


def _x(text) -> str:
    """Escape text for a ReportLab Paragraph ("&" in a designation breaks it)."""
    from xml.sax.saxutils import escape
    return escape(str(text or ""))


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
    _meta = letter.get("pdf_meta") or {}
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        # PDF Document Properties — what the NSE/SEBI portal & reviewers read.
        title=(_meta.get("title") or letter["subject"][:80] or "Filing"),
        author=(_meta.get("author") or letter["company_name"]),
        subject=(_meta.get("subject") or letter["subject"]),
        keywords=(_meta.get("keywords") or ""),
        creator=(_meta.get("creator") or "Delta Filings"),
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

    _addressed = letter.get("addressed", True)
    doc_title_style = ParagraphStyle(
        "doctitle", parent=styles["Title"], alignment=TA_CENTER,
        fontSize=13, leading=18, spaceBefore=2, spaceAfter=14, textColor=colors.black,
    )

    if _addressed:
        # ── Date ──
        story.append(Paragraph(_x(f"{letter.get('date_label', 'Date: ')}{letter['letter_date']}"), body_style))
        # ── Two-column listing addresses (the company's own, when learned) ──
        left, right = letter.get("address_columns") or [LISTING_ADDRESSES[0]["lines"], LISTING_ADDRESSES[1]["lines"]]
        bse_paras = [Paragraph(_x(l), addr_style) for l in left]
        nse_paras = [Paragraph(_x(l), addr_style) for l in right]
        addr_table = Table([[bse_paras, nse_paras]], colWidths=[85 * mm, 85 * mm])
        addr_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(addr_table)
        story.append(Spacer(1, 6 * mm))
        for cl in letter.get("code_lines") or []:
            story.append(Paragraph(f"<b>{_x(cl)}</b>", addr_style))
        if letter.get("code_lines"):
            story.append(Spacer(1, 4 * mm))
        # ── Subject ──
        if letter["subject"]:
            story.append(Paragraph(
                f"<b><u>{_x(letter.get('subject_label') or 'Subject:')} {_x(letter['subject'])}</u></b>", subject_style))
        # ── Greeting ──
        story.append(Paragraph(_x(letter.get("salutation") or "Dear Sir / Madam,"), body_style))
    else:
        # Standalone company document (resolution / notice / minutes / etc.):
        # centered document title, no exchange address, no greeting.
        if letter["subject"]:
            story.append(Paragraph(f"<b><u>{letter['subject'].upper()}</u></b>", doc_title_style))
        story.append(Paragraph(f"Date: {letter['letter_date']}", body_style))

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
    if letter.get("addressed", True) and letter.get("thanks_line"):
        story.append(Paragraph(_x(letter["thanks_line"]), body_style))
    if letter.get("close_phrase"):
        story.append(Paragraph(_x(letter["close_phrase"]), body_style))
        story.append(Spacer(1, 2 * mm))
    if letter.get("for_line"):
        story.append(Paragraph(f"<b>{_x(letter['for_line'])}</b>", sig_style))
    story.append(Spacer(1, 14 * mm))   # space for physical signature
    if letter["signer_name"]:
        story.append(Paragraph(f"<b>{_x(letter['signer_name'])}</b>", sig_style))
    if letter["signer_designation"]:
        story.append(Paragraph(_x(letter["signer_designation"]), sig_style))
    if letter.get("signer_membership"):
        story.append(Paragraph(_x(letter["signer_membership"]), sig_style))

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
    # Document Properties (NSE/SEBI portals read these).
    _meta = letter.get("pdf_meta") or {}
    try:
        cp = doc.core_properties
        cp.title = _meta.get("title") or letter["subject"] or "Filing"
        cp.author = _meta.get("author") or letter["company_name"]
        cp.subject = _meta.get("subject") or letter["subject"]
        cp.keywords = _meta.get("keywords") or ""
    except Exception:
        pass
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

    _addressed = letter.get("addressed", True)

    if _addressed:
        # ── Date ──
        p_date = doc.add_paragraph()
        p_date.add_run(f"{letter.get('date_label', 'Date: ')}{letter['letter_date']}").font.size = Pt(11)
        # ── Two-column addresses via table (the company's own, when learned) ──
        bse_lines, nse_lines = letter.get("address_columns") or [LISTING_ADDRESSES[0]["lines"], LISTING_ADDRESSES[1]["lines"]]
        table = doc.add_table(rows=1, cols=2)
        table.autofit = True
        cells = table.rows[0].cells
        cells[0].text = ""
        cells[1].text = ""
        for i, lines in enumerate([bse_lines, nse_lines]):
            cell = cells[i]
            cell.paragraphs[0].add_run(lines[0]).font.size = Pt(10)
            for ln in lines[1:]:
                cp = cell.add_paragraph()
                cp.add_run(ln).font.size = Pt(10)
        doc.add_paragraph()
        for cl in letter.get("code_lines") or []:
            rc = doc.add_paragraph().add_run(cl)
            rc.bold = True
            rc.font.size = Pt(10)
        # ── Subject ──
        if letter["subject"]:
            p_subj = doc.add_paragraph()
            run = p_subj.add_run(f"{letter.get('subject_label') or 'Subject:'} {letter['subject']}")
            run.bold = True
            run.underline = True
            run.font.size = Pt(11)
        # ── Greeting ──
        doc.add_paragraph().add_run(letter.get("salutation") or "Dear Sir / Madam,").font.size = Pt(11)
    else:
        # Standalone company document: centered title, date, no address/greeting.
        if letter["subject"]:
            p_t = doc.add_paragraph()
            p_t.alignment = WD_ALIGN_PARAGRAPH.CENTER
            rt = p_t.add_run(letter["subject"].upper())
            rt.bold = True
            rt.underline = True
            rt.font.size = Pt(13)
        p_date = doc.add_paragraph()
        p_date.add_run(f"Date: {letter['letter_date']}").font.size = Pt(11)

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
    if letter.get("addressed", True) and letter.get("thanks_line"):
        doc.add_paragraph().add_run(letter["thanks_line"]).font.size = Pt(11)
    if letter.get("close_phrase"):
        doc.add_paragraph().add_run(letter["close_phrase"]).font.size = Pt(11)
    if letter.get("for_line"):
        p_for = doc.add_paragraph()
        rf = p_for.add_run(letter["for_line"])
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
    if letter.get("signer_membership"):
        doc.add_paragraph().add_run(letter["signer_membership"]).font.size = Pt(11)

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
