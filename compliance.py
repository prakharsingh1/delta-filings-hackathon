"""Compliance OS — SEBI mandate tracking, deadline monitoring, and compliance scoring."""

import sqlite3
import os
import json
import calendar as _cal
from datetime import datetime, date, timedelta
from database import get_connection, get_watchlist, get_all_watchlist_symbols, get_cached_filings
from compliance_verification import verify_obligation
from sebi_link_resolver import get_regulation_url, SEBI_REGULATIONS_INDEX

# ── SEBI Mandates Registry ──
# Each mandate defines: what, when, source link, applicable regulations
# Stable source URLs for the non-SEBI regimes (SEBI links resolve dynamically
# via sebi_source_key in get_all_mandates).
_MCA_SRC = "https://www.mca.gov.in/content/mca/global/en/mca/e-filing/company-forms-download.html"
_RBI_FIRMS = "https://firms.rbi.org.in/firms/"
_RBI_FEMA = "https://www.rbi.org.in/Scripts/BS_FemaNotifications.aspx"
_IT_SRC = "https://www.incometax.gov.in/iec/foportal/"
_EPFO_SRC = "https://www.epfindia.gov.in/"
_POSH_SRC = "https://wcd.nic.in/act/sexual-harassment-women-workplace-prevention-prohibition-and-redressal-act-2013"

# ───────────────────────────────────────────────────────────────────────────
# COMPLIANCE MANDATE CATALOG
# The full universe of statutory filings an Indian company may owe — SEBI
# (LODR / PIT / SAST), MCA / Companies Act (ROC e-forms), FEMA / RBI, and
# Tax & Labour. This is the reference a Company Secretary works from.
#
# Each mandate: id, name, regulation, form (e-form / mode), description,
# frequency, due rule, category, regime, applies_to, penalty, section_ref,
# and a source (sebi_source_key for SEBI, else a direct `source` URL).
# Frequencies that generate dated obligations: quarterly, half_yearly, annual.
# event_based / monthly / ongoing are catalog-only (no auto due date).
# ───────────────────────────────────────────────────────────────────────────
SEBI_MANDATES = [
    # ════════ SEBI · LODR (listed companies) ════════
    {
        "id": "LODR-REG27", "name": "Reg 27 — Corporate Governance Report",
        "regulation": "SEBI LODR Reg 27", "form": "Integrated Filing (Governance)",
        "description": "Quarterly corporate governance report, filed as part of the consolidated Integrated Filing-Governance within 30 days of quarter end (SEBI Dec-2024 circular merged Regs 13/27/31/32).",
        "frequency": "quarterly", "due_days_after_quarter": 30,
        "category": "Governance", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "₹2,000/day (per stock exchange) under SOP for non-compliance.",
        "filed_via": "Integrated Filing- Governance",
        "keywords": ["corporate governance", "regulation 27", "reg 27", "governance report", "cg report", "compliance certificate"],
        "sebi_source_key": "lodr", "sebi_circular": "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2024/185",
        "section_ref": "Chapter IV, Regulation 27",
    },
    {
        "id": "LODR-REG33", "name": "Reg 33 — Financial Results",
        "regulation": "SEBI LODR Reg 33", "form": "Quarterly/Annual results to exchanges",
        "description": "Quarterly results within 45 days of quarter end; audited annual results within 60 days of year end.",
        "frequency": "quarterly", "due_days_after_quarter": 45, "due_days_annual": 60,
        "category": "Financial", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "₹5,000/day per exchange (SOP).",
        "keywords": ["financial result", "quarterly result", "annual result", "regulation 33"],
        "sebi_source_key": "lodr", "sebi_circular": "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2023/167",
        "section_ref": "Chapter IV, Regulation 33",
    },
    {
        "id": "LODR-REG31", "name": "Reg 31 — Shareholding Pattern",
        "regulation": "SEBI LODR Reg 31", "form": "Integrated Filing (Governance)",
        "description": "Shareholding pattern within 21 days of quarter end (now part of the consolidated Integrated Filing-Governance).",
        "frequency": "quarterly", "due_days_after_quarter": 21,
        "category": "Periodic", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "₹2,000/day per exchange (SOP).",
        "filed_via": "Integrated Filing- Governance",
        "keywords": ["shareholding pattern", "regulation 31", "shareholding"],
        "sebi_source_key": "lodr", "sebi_circular": "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2024/185",
        "section_ref": "Chapter IV, Regulation 31",
    },
    {
        "id": "LODR-REG13", "name": "Reg 13(3) — Investor Grievance Report",
        "regulation": "SEBI LODR Reg 13", "form": "Integrated Filing (Governance)",
        "description": "Statement of investor complaints within 21 days of quarter end (part of Integrated Filing-Governance).",
        "frequency": "quarterly", "due_days_after_quarter": 21,
        "category": "Investor Relations", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "₹1,000/day per exchange (SOP).",
        "filed_via": "Integrated Filing- Governance",
        "keywords": ["investor complaint", "grievance", "regulation 13"],
        "sebi_source_key": "lodr", "sebi_circular": "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2024/185",
        "section_ref": "Chapter III, Regulation 13",
    },
    {
        "id": "LODR-REG74", "name": "Reg 74(5) — RTA / Demat Certificate",
        "regulation": "SEBI LODR Reg 74", "form": "RTA certificate to exchanges",
        "description": "Certificate from the Registrar & Transfer Agent on timely dematerialisation of shares, within 30 days of quarter end.",
        "frequency": "quarterly", "due_days_after_quarter": 30,
        "category": "Compliance", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "Advisory/penal action by exchange.",
        "keywords": ["regulation 74", "registrar", "dematerialization", "demat"],
        "sebi_source_key": "lodr", "section_ref": "Chapter IX, Regulation 74",
    },
    {
        "id": "LODR-REG76-RSCA", "name": "Reg 76 — Reconciliation of Share Capital Audit",
        "regulation": "SEBI (D&P) Reg 76", "form": "RSCA report (by PCS)",
        "description": "Reconciliation of Share Capital Audit report by a Practising Company Secretary, within 30 days of quarter end.",
        "frequency": "quarterly", "due_days_after_quarter": 30,
        "category": "Compliance", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "Exchange penal action.",
        "keywords": ["reconciliation of share capital", "rsca", "regulation 76"],
        "sebi_source_key": "lodr", "section_ref": "SEBI (D&P) Reg 76",
    },
    {
        "id": "LODR-REG23-RPT", "name": "Reg 23(9) — Related Party Transactions",
        "regulation": "SEBI LODR Reg 23(9)", "form": "RPT disclosure to exchanges",
        "description": "Disclosure of related-party transactions on a consolidated basis, within 15 days of the half-year (aligned to financial results from FY24).",
        "frequency": "half_yearly", "due_days_after_half": 15,
        "category": "Governance", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "₹2,000/day per exchange (SOP).",
        "keywords": ["related party", "rpt", "regulation 23"],
        "sebi_source_key": "lodr", "section_ref": "Chapter IV, Regulation 23(9)",
    },
    {
        "id": "LODR-REG24A", "name": "Reg 24A — Secretarial Compliance Report",
        "regulation": "SEBI LODR Reg 24A", "form": "ASCR + Secretarial Audit (MR-3)",
        "description": "Annual Secretarial Compliance Report by a PCS, within 60 days of year end; secretarial audit report annexed to the board's report.",
        "frequency": "annual", "due_month": 5, "due_day": 30,
        "category": "Governance", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "₹2,000/day per exchange (SOP).",
        "keywords": ["secretarial audit", "ascr", "regulation 24a", "mr-3"],
        "sebi_source_key": "lodr", "section_ref": "Chapter IV, Regulation 24A",
    },
    {
        "id": "LODR-REG34-AR", "name": "Reg 34 — Annual Report",
        "regulation": "SEBI LODR Reg 34", "form": "Annual report to exchanges + members",
        "description": "Annual report submitted to exchanges within 21 working days of the AGM, and sent to members ≥21 days before the AGM.",
        "frequency": "annual", "due_month": 9, "due_day": 30,
        "category": "Periodic", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "₹2,000/day per exchange (SOP).",
        "keywords": ["annual report", "regulation 34"],
        "sebi_source_key": "lodr", "section_ref": "Chapter IV, Regulation 34",
    },
    {
        "id": "LODR-REG30", "name": "Reg 30 — Material Event Disclosure",
        "regulation": "SEBI LODR Reg 30", "form": "Disclosure to exchanges",
        "description": "Disclosure of material events/information — within 30 min (board outcomes), 12 h, or 24 h depending on the event; materiality at 2% turnover / 2% net worth / 5% PAT.",
        "frequency": "event_based",
        "category": "Disclosure", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "₹10,000 per instance + ₹1,000/day continuing (SOP).",
        "keywords": ["regulation 30", "material event", "disclosure", "intimation"],
        "sebi_source_key": "lodr", "sebi_circular": "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2023/123",
        "section_ref": "Chapter IV, Regulation 30",
    },
    {
        "id": "LODR-REG29", "name": "Reg 29 — Board Meeting Prior Intimation",
        "regulation": "SEBI LODR Reg 29", "form": "Prior intimation to exchanges",
        "description": "Prior intimation of board meetings — ≥2 working days normally; ≥5 days when financial results are considered.",
        "frequency": "event_based",
        "category": "Disclosure", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "₹1,000/day per exchange (SOP).",
        "keywords": ["board meeting intimation", "regulation 29", "prior intimation"],
        "sebi_source_key": "lodr", "section_ref": "Chapter IV, Regulation 29",
    },
    {
        "id": "LODR-REG44", "name": "Reg 44(3) — Voting Results",
        "regulation": "SEBI LODR Reg 44", "form": "Voting results + scrutinizer report",
        "description": "Voting results with scrutinizer's report within 2 working days of an AGM/EGM/postal ballot.",
        "frequency": "event_based",
        "category": "Governance", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "Exchange penal action.",
        "keywords": ["voting result", "regulation 44", "scrutinizer", "e-voting"],
        "sebi_source_key": "lodr", "section_ref": "Chapter V, Regulation 44",
    },
    {
        "id": "LODR-AGM", "name": "Annual General Meeting (listed)",
        "regulation": "SEBI LODR / Companies Act s.96", "form": "AGM",
        "description": "Hold the AGM within 6 months of financial-year end (by 30 September for a March year-end).",
        "frequency": "annual", "due_month": 9, "due_day": 30,
        "category": "Governance", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "Tribunal action; company + officers liable under s.99 (up to ₹1 lakh + ₹5,000/day).",
        "keywords": ["annual general meeting", "agm"],
        "sebi_source_key": "lodr", "section_ref": "Reg 44 r/w Companies Act s.96",
    },

    {
        "id": "LODR-BRSR", "name": "BRSR — Business Responsibility & Sustainability Report",
        "regulation": "SEBI LODR Reg 34(2)(f)", "form": "BRSR (in annual report)",
        "description": "ESG disclosure across 9 NGRBC principles, mandatory for the top 1,000 listed companies by market cap, as part of the annual report.",
        "frequency": "annual", "due_month": 9, "due_day": 30,
        "category": "Governance", "regime": "SEBI", "applies_to": "Top 1,000 listed companies",
        "penalty": "₹2,000/day per exchange (SOP).",
        "keywords": ["brsr", "business responsibility", "sustainability", "esg"],
        "sebi_source_key": "lodr", "section_ref": "Reg 34(2)(f)",
    },
    {
        "id": "LODR-BRSR-CORE", "name": "BRSR Core — Reasonable Assurance",
        "regulation": "SEBI LODR (BRSR Core)", "form": "Assured BRSR Core KPIs",
        "description": "Reasonable assurance of 9 BRSR Core attributes (incl. GHG emissions intensity, water, energy, waste) — top 250 listed cos, expanding by market cap. Value-chain disclosure phased in.",
        "frequency": "annual", "due_month": 9, "due_day": 30,
        "category": "Governance", "regime": "SEBI", "applies_to": "Top 250 listed (phased)",
        "penalty": "₹2,000/day per exchange (SOP).",
        "keywords": ["brsr core", "assurance", "ghg", "esg", "value chain"],
        "sebi_source_key": "lodr", "section_ref": "BRSR Core framework (2023)",
    },

    # ════════ SEBI · PIT (insider trading) ════════
    {
        "id": "PIT-REG7", "name": "PIT Reg 7 — Insider Trade Disclosures",
        "regulation": "SEBI PIT Reg 7", "form": "Form C / system-driven disclosure",
        "description": "Disclosure of trades by designated persons & promoters above ₹10 lakh within 2 trading days; the company reports to exchanges within 2 trading days.",
        "frequency": "event_based",
        "category": "Insider Trading", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "Up to ₹10 lakh under s.15A SEBI Act.",
        "keywords": ["insider trading", "regulation 7", "pit", "form c"],
        "sebi_source_key": "pit", "sebi_circular": "SEBI/HO/ISD/ISD-SEC-4/P/CIR/2023/0136",
        "section_ref": "Chapter III, Regulation 7",
    },
    {
        "id": "PIT-SDD", "name": "PIT Reg 3 — Structured Digital Database",
        "regulation": "SEBI PIT Reg 3(5)/(6)", "form": "Internal SDD (tamper-proof)",
        "description": "Maintain a tamper-proof Structured Digital Database of UPSI sharing with PAN/identifiers; preserved 8 years. Audited under the annual PIT compliance review.",
        "frequency": "ongoing",
        "category": "Insider Trading", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "Up to ₹10 lakh; adverse PIT audit findings.",
        "keywords": ["structured digital database", "sdd", "upsi", "regulation 3"],
        "sebi_source_key": "pit", "section_ref": "Chapter II, Regulation 3(5)-(6)",
    },
    {
        "id": "PIT-TRADING-WINDOW", "name": "PIT — Trading Window Closure",
        "regulation": "SEBI PIT (Code of Conduct)", "form": "Window closure intimation",
        "description": "Close the trading window for designated persons from the quarter-end until 48 hours after results are made public; intimate the exchange/depository.",
        "frequency": "quarterly", "due_days_after_quarter": 0,
        "category": "Insider Trading", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "Contra-trade reversal + up to ₹10 lakh.",
        "keywords": ["trading window", "window closure", "code of conduct"],
        "sebi_source_key": "pit", "section_ref": "Schedule B, Code of Conduct",
    },

    # ════════ SEBI · SAST (takeover) ════════
    {
        "id": "SAST-REG30", "name": "SAST Reg 30(1) — Annual Shareholding Disclosure",
        "regulation": "SEBI SAST Reg 30", "form": "Disclosure to company + exchanges",
        "description": "Promoters/PACs holding ≥25% disclose aggregate shareholding & voting rights as on 31 March, within 7 working days of year end.",
        "frequency": "annual", "due_month": 4, "due_day": 7,
        "category": "Disclosure", "regime": "SEBI", "applies_to": "Listed companies",
        "penalty": "Up to ₹1 lakh/day, max ₹1 crore (s.15A).",
        "keywords": ["sast", "takeover", "regulation 30", "shareholding disclosure"],
        "sebi_source_key": "sast", "section_ref": "Chapter V, Regulation 30",
    },

    # ════════ MCA · Companies Act 2013 — ROC e-forms ════════
    {
        "id": "MCA-AOC4", "name": "AOC-4 — Financial Statements",
        "regulation": "Companies Act s.137", "form": "AOC-4 / AOC-4 XBRL / AOC-4 CFS",
        "description": "File audited financial statements with the ROC within 30 days of the AGM. Listed cos & prescribed classes file AOC-4 XBRL; companies with subsidiaries also file AOC-4 CFS.",
        "frequency": "annual", "due_month": 10, "due_day": 30,
        "category": "Financial", "regime": "MCA", "applies_to": "All companies",
        "penalty": "₹100/day (no cap) + officer penalty up to ₹50,000.",
        "keywords": ["aoc-4", "financial statement", "section 137"],
        "source": _MCA_SRC, "section_ref": "Section 137",
    },
    {
        "id": "MCA-MGT7", "name": "MGT-7 / MGT-7A — Annual Return",
        "regulation": "Companies Act s.92", "form": "MGT-7 (MGT-7A for OPC/small)",
        "description": "File the annual return with the ROC within 60 days of the AGM. OPCs & small companies file the abridged MGT-7A.",
        "frequency": "annual", "due_month": 11, "due_day": 29,
        "category": "Periodic", "regime": "MCA", "applies_to": "All companies",
        "penalty": "₹100/day (no cap) + officer penalty up to ₹5 lakh.",
        "keywords": ["mgt-7", "annual return", "section 92"],
        "source": _MCA_SRC, "section_ref": "Section 92",
    },
    {
        "id": "MCA-MGT8", "name": "MGT-8 — Annual Return Certification",
        "regulation": "Companies Act s.92(2)", "form": "MGT-8 (PCS certificate)",
        "description": "PCS certification of the annual return for listed cos and companies with paid-up capital ≥₹10 cr or turnover ≥₹50 cr; annexed to MGT-7.",
        "frequency": "annual", "due_month": 11, "due_day": 29,
        "category": "Governance", "regime": "MCA", "applies_to": "Listed / large companies",
        "penalty": "PCS penalty up to ₹2 lakh for false certification.",
        "keywords": ["mgt-8", "annual return certification"],
        "source": _MCA_SRC, "section_ref": "Section 92(2)",
    },
    {
        "id": "MCA-ADT1", "name": "ADT-1 — Auditor Appointment",
        "regulation": "Companies Act s.139", "form": "ADT-1",
        "description": "Intimate the ROC of the appointment/reappointment of statutory auditor within 15 days of the AGM.",
        "frequency": "annual", "due_month": 10, "due_day": 15,
        "category": "Compliance", "regime": "MCA", "applies_to": "All companies",
        "penalty": "₹100/day + officer penalty.",
        "keywords": ["adt-1", "auditor appointment", "section 139"],
        "source": _MCA_SRC, "section_ref": "Section 139",
    },
    {
        "id": "MCA-DIR3KYC", "name": "DIR-3 KYC — Director KYC",
        "regulation": "Companies Act / Rule 12A", "form": "DIR-3 KYC / KYC-Web",
        "description": "Every DIN holder files annual KYC by 30 September. DIN is deactivated on default (₹5,000 reactivation fee).",
        "frequency": "annual", "due_month": 9, "due_day": 30,
        "category": "Compliance", "regime": "MCA", "applies_to": "All DIN holders",
        "penalty": "₹5,000 per DIN to reactivate.",
        "keywords": ["dir-3 kyc", "director kyc", "din"],
        "source": _MCA_SRC, "section_ref": "Rule 12A",
    },
    {
        "id": "MCA-DPT3", "name": "DPT-3 — Return of Deposits",
        "regulation": "Companies Act / Rule 16", "form": "DPT-3",
        "description": "Annual return of deposits & outstanding loans/money not treated as deposits as on 31 March, filed by 30 June.",
        "frequency": "annual", "due_month": 6, "due_day": 30,
        "category": "Financial", "regime": "MCA", "applies_to": "All companies (except govt.)",
        "penalty": "Up to ₹5,000 + ₹500/day continuing.",
        "keywords": ["dpt-3", "return of deposits", "rule 16"],
        "source": _MCA_SRC, "section_ref": "Rule 16, Deposit Rules",
    },
    {
        "id": "MCA-MSME1", "name": "MSME-1 — Half-Yearly MSME Dues",
        "regulation": "Companies Act s.405", "form": "MSME-1",
        "description": "Half-yearly return of outstanding dues to MSME suppliers beyond 45 days — by 30 April (Oct-Mar) and 31 October (Apr-Sep).",
        "frequency": "half_yearly", "due_dates": [[4, 30], [10, 31]],
        "category": "Compliance", "regime": "MCA", "applies_to": "Companies with MSME dues",
        "penalty": "Up to ₹25,000 (company) + officer penalty.",
        "keywords": ["msme-1", "msme dues", "section 405"],
        "source": _MCA_SRC, "section_ref": "Section 405",
    },
    {
        "id": "MCA-PAS6", "name": "PAS-6 — Share Capital Reconciliation",
        "regulation": "Companies Act / Rule 9A", "form": "PAS-6 (PCS/PCA certified)",
        "description": "Half-yearly reconciliation of share capital audit for unlisted public companies, within 60 days of each half-year.",
        "frequency": "half_yearly", "due_dates": [[5, 30], [11, 29]],
        "category": "Compliance", "regime": "MCA", "applies_to": "Unlisted public companies",
        "penalty": "₹10,000 + ₹1,000/day continuing.",
        "keywords": ["pas-6", "share capital reconciliation", "rule 9a"],
        "source": _MCA_SRC, "section_ref": "Rule 9A, PAS Rules",
    },
    {
        "id": "MCA-CSR2", "name": "CSR-2 — CSR Report",
        "regulation": "Companies Act s.135", "form": "CSR-2",
        "description": "Annual report on CSR spending, filed with the ROC by 31 December (separate from AOC-4).",
        "frequency": "annual", "due_month": 12, "due_day": 31,
        "category": "Governance", "regime": "MCA", "applies_to": "CSR-applicable companies",
        "penalty": "2× unspent (max ₹1 cr) on company; officer up to ₹2 lakh (s.135(7)).",
        "keywords": ["csr-2", "corporate social responsibility", "section 135"],
        "source": _MCA_SRC, "section_ref": "Section 135",
    },
    {
        "id": "MCA-BOARD", "name": "Board Meetings (min. 4/year)",
        "regulation": "Companies Act s.173", "form": "Board meeting + minutes (SS-1)",
        "description": "Hold at least 4 board meetings a year with a gap of ≤120 days between two meetings; minutes per Secretarial Standard SS-1.",
        "frequency": "quarterly", "due_days_after_quarter": 30,
        "category": "Governance", "regime": "MCA", "applies_to": "All companies",
        "penalty": "₹25,000 per officer in default.",
        "keywords": ["board meeting", "section 173", "ss-1", "minutes"],
        "source": _MCA_SRC, "section_ref": "Section 173",
    },
    {
        "id": "MCA-DIR12", "name": "DIR-12 — Change in Directors",
        "regulation": "Companies Act s.168/170", "form": "DIR-12",
        "description": "File appointment/resignation/change in directors or KMP with the ROC within 30 days of the event.",
        "frequency": "event_based",
        "category": "Governance", "regime": "MCA", "applies_to": "All companies",
        "penalty": "₹100/day (no cap).",
        "keywords": ["dir-12", "change in directors", "kmp"],
        "source": _MCA_SRC, "section_ref": "Sections 168/170",
    },
    {
        "id": "MCA-MGT14", "name": "MGT-14 — Filing of Resolutions",
        "regulation": "Companies Act s.117", "form": "MGT-14",
        "description": "File specified board/special resolutions & agreements with the ROC within 30 days of passing.",
        "frequency": "event_based",
        "category": "Governance", "regime": "MCA", "applies_to": "All companies",
        "penalty": "₹10,000 + ₹100/day (max ₹2 lakh company / ₹50,000 officer).",
        "keywords": ["mgt-14", "resolutions", "section 117"],
        "source": _MCA_SRC, "section_ref": "Section 117",
    },
    {
        "id": "MCA-PAS3", "name": "PAS-3 — Return of Allotment",
        "regulation": "Companies Act s.39", "form": "PAS-3",
        "description": "File the return of allotment of shares with the ROC within 30 days (15 days for some classes) of allotment.",
        "frequency": "event_based",
        "category": "Financial", "regime": "MCA", "applies_to": "All companies",
        "penalty": "₹1,000/day (max ₹25 lakh) on company & officers.",
        "keywords": ["pas-3", "return of allotment", "section 39"],
        "source": _MCA_SRC, "section_ref": "Section 39",
    },
    {
        "id": "MCA-BEN2", "name": "BEN-2 — Significant Beneficial Owner",
        "regulation": "Companies Act s.90", "form": "BEN-2 (after BEN-1)",
        "description": "File the SBO return with the ROC within 30 days of receiving a BEN-1 declaration; maintain the BEN-3 register.",
        "frequency": "event_based",
        "category": "Governance", "regime": "MCA", "applies_to": "All companies",
        "penalty": "₹1 lakh + ₹500/day continuing.",
        "keywords": ["ben-2", "significant beneficial owner", "sbo", "section 90"],
        "source": _MCA_SRC, "section_ref": "Section 90",
    },
    {
        "id": "MCA-CHG1", "name": "CHG-1 / CHG-4 — Charge Creation / Satisfaction",
        "regulation": "Companies Act s.77/82", "form": "CHG-1 / CHG-4",
        "description": "Register creation/modification of a charge within 30 days (CHG-1); register satisfaction within 30 days (CHG-4).",
        "frequency": "event_based",
        "category": "Financial", "regime": "MCA", "applies_to": "All companies",
        "penalty": "Up to ₹5 lakh (company) / ₹50,000 (officer).",
        "keywords": ["chg-1", "chg-4", "charge", "section 77"],
        "source": _MCA_SRC, "section_ref": "Sections 77/82",
    },
    {
        "id": "MCA-SH7", "name": "SH-7 — Increase in Authorised Capital",
        "regulation": "Companies Act s.64", "form": "SH-7",
        "description": "File notice of alteration of share capital (e.g. increase in authorised capital) with the ROC within 30 days.",
        "frequency": "event_based",
        "category": "Financial", "regime": "MCA", "applies_to": "All companies",
        "penalty": "₹1,000/day (max ₹5 lakh).",
        "keywords": ["sh-7", "authorised capital", "section 64"],
        "source": _MCA_SRC, "section_ref": "Section 64",
    },
    {
        "id": "MCA-INC22", "name": "INC-22 — Change of Registered Office",
        "regulation": "Companies Act s.12", "form": "INC-22",
        "description": "File change of registered office with the ROC within 30 days of the change.",
        "frequency": "event_based",
        "category": "Compliance", "regime": "MCA", "applies_to": "All companies",
        "penalty": "₹1,000/day (max ₹1 lakh).",
        "keywords": ["inc-22", "registered office", "section 12"],
        "source": _MCA_SRC, "section_ref": "Section 12",
    },
    {
        "id": "MCA-CRA4", "name": "CRA-2 / CRA-4 — Cost Audit",
        "regulation": "Companies Act s.148", "form": "CRA-2 (appt.) / CRA-4 (report)",
        "description": "For specified industries above thresholds: appoint cost auditor (CRA-2) and file the cost audit report (CRA-4) within 30 days of receipt.",
        "frequency": "annual", "due_month": 9, "due_day": 30,
        "category": "Financial", "regime": "MCA", "applies_to": "Cost-audit-applicable companies",
        "penalty": "Up to ₹5 lakh (company) / ₹1 lakh (officer).",
        "keywords": ["cra-4", "cost audit", "section 148"],
        "source": _MCA_SRC, "section_ref": "Section 148",
    },

    # ════════ FEMA · RBI ════════
    {
        "id": "FEMA-FCGPR", "name": "FC-GPR — Foreign Investment Allotment",
        "regulation": "FEMA NDI Rules", "form": "FC-GPR (FIRMS portal)",
        "description": "Report allotment of shares to a non-resident on the RBI FIRMS portal within 30 days of allotment.",
        "frequency": "event_based",
        "category": "Compliance", "regime": "FEMA", "applies_to": "Companies with FDI",
        "penalty": "Up to 3× the amount involved + ₹5,000/day (compounding).",
        "keywords": ["fc-gpr", "fdi", "foreign investment", "firms"],
        "source": _RBI_FIRMS, "section_ref": "FEMA (NDI) Rules 2019",
    },
    {
        "id": "FEMA-FCTRS", "name": "FC-TRS — Transfer with Non-Resident",
        "regulation": "FEMA NDI Rules", "form": "FC-TRS (FIRMS portal)",
        "description": "Report a transfer of shares between a resident and a non-resident within 60 days of transfer/remittance.",
        "frequency": "event_based",
        "category": "Compliance", "regime": "FEMA", "applies_to": "Companies with FDI",
        "penalty": "Up to 3× the amount involved (compounding).",
        "keywords": ["fc-trs", "transfer of shares", "firms"],
        "source": _RBI_FIRMS, "section_ref": "FEMA (NDI) Rules 2019",
    },
    {
        "id": "FEMA-FLA", "name": "FLA Return — Foreign Liabilities & Assets",
        "regulation": "FEMA", "form": "FLA (RBI FLAIR portal)",
        "description": "Annual return of foreign liabilities & assets, by 15 July, by every company that has FDI or has invested abroad.",
        "frequency": "annual", "due_month": 7, "due_day": 15,
        "category": "Compliance", "regime": "FEMA", "applies_to": "Companies with FDI/ODI",
        "penalty": "Up to 3× the amount / ₹2 lakh (compounding).",
        "keywords": ["fla", "foreign liabilities and assets", "flair"],
        "source": "https://flair.rbi.org.in/", "section_ref": "FEMA",
    },
    {
        "id": "FEMA-APR", "name": "APR — Annual Performance Report (ODI)",
        "regulation": "FEMA OI Rules", "form": "APR (FIRMS portal)",
        "description": "Annual Performance Report for overseas investments, by 31 December each year.",
        "frequency": "annual", "due_month": 12, "due_day": 31,
        "category": "Compliance", "regime": "FEMA", "applies_to": "Companies with ODI",
        "penalty": "Up to 3× the amount (compounding).",
        "keywords": ["apr", "overseas investment", "odi"],
        "source": _RBI_FIRMS, "section_ref": "FEMA (OI) Rules 2022",
    },
    {
        "id": "FEMA-ECB2", "name": "ECB-2 — Monthly ECB Return",
        "regulation": "FEMA Borrowing Regs", "form": "ECB-2 (via AD bank)",
        "description": "Monthly return on external commercial borrowings, by the 7th of the following month, for companies with an ECB.",
        "frequency": "monthly",
        "category": "Financial", "regime": "FEMA", "applies_to": "Companies with ECB",
        "penalty": "Late submission fee + compounding.",
        "keywords": ["ecb-2", "external commercial borrowing"],
        "source": _RBI_FEMA, "section_ref": "FEMA Borrowing Regulations",
    },

    # ════════ Tax & Labour ════════
    {
        "id": "TAX-TDS", "name": "TDS Returns (24Q / 26Q / 27Q)",
        "regulation": "Income-tax Act s.200", "form": "24Q / 26Q / 27Q",
        "description": "Quarterly TDS returns by the last day of the month following the quarter (Q4 by 31 May).",
        "frequency": "quarterly", "due_days_after_quarter": 31,
        "category": "Financial", "regime": "Tax & Labour", "applies_to": "All deductors",
        "penalty": "₹200/day (s.234E) + penalty u/s 271H.",
        "keywords": ["tds", "24q", "26q", "tds return"],
        "source": _IT_SRC, "section_ref": "Section 200(3)",
    },
    {
        "id": "TAX-AUDIT", "name": "Tax Audit (Form 3CA/3CB-3CD)",
        "regulation": "Income-tax Act s.44AB", "form": "3CA/3CB + 3CD",
        "description": "Tax audit report for businesses above the turnover threshold, by 30 September (then ITR by 31 October).",
        "frequency": "annual", "due_month": 9, "due_day": 30,
        "category": "Financial", "regime": "Tax & Labour", "applies_to": "Cos above s.44AB threshold",
        "penalty": "0.5% of turnover (max ₹1.5 lakh) u/s 271B.",
        "keywords": ["tax audit", "3cd", "section 44ab"],
        "source": _IT_SRC, "section_ref": "Section 44AB",
    },
    {
        "id": "TAX-ITR", "name": "Income Tax Return (ITR-6)",
        "regulation": "Income-tax Act s.139", "form": "ITR-6",
        "description": "Corporate income-tax return — by 31 October for audited companies (30 November if transfer-pricing applies).",
        "frequency": "annual", "due_month": 10, "due_day": 31,
        "category": "Financial", "regime": "Tax & Labour", "applies_to": "All companies",
        "penalty": "Up to ₹10,000 (s.234F) + interest.",
        "keywords": ["itr", "income tax return", "itr-6"],
        "source": _IT_SRC, "section_ref": "Section 139",
    },
    {
        "id": "LABOUR-POSH", "name": "POSH Annual Return",
        "regulation": "POSH Act s.21", "form": "Annual report to District Officer",
        "description": "Internal Committee files its annual report on sexual-harassment complaints with the District Officer by 31 December (calendar year).",
        "frequency": "annual", "due_month": 12, "due_day": 31,
        "category": "Compliance", "regime": "Tax & Labour", "applies_to": "Employers with ≥10 staff",
        "penalty": "Up to ₹50,000; repeat → 2× + licence cancellation.",
        "keywords": ["posh", "annual return", "internal committee"],
        "source": _POSH_SRC, "section_ref": "Section 21, POSH Act 2013",
    },
    {
        "id": "LABOUR-PFESI", "name": "PF & ESI Monthly Returns",
        "regulation": "EPF & ESI Acts", "form": "ECR (EPFO) / ESI return",
        "description": "Monthly PF Electronic Challan-cum-Return and ESI contribution by the 15th of the following month.",
        "frequency": "monthly",
        "category": "Compliance", "regime": "Tax & Labour", "applies_to": "Covered employers",
        "penalty": "Interest @12% p.a. + damages up to 25% p.a.",
        "keywords": ["pf", "esi", "ecr", "provident fund"],
        "source": _EPFO_SRC, "section_ref": "EPF Act / ESI Act",
    },
]


def get_mandate_by_id(mandate_id):
    for m in SEBI_MANDATES:
        if m["id"] == mandate_id:
            return m
    return None


# ───────────────────────────────────────────────────────────────────────────
# SECTOR-SPECIFIC COMPLIANCE
# Beyond the universal MCA + SEBI filings, each industry carries its own
# regulator-specific obligations (environmental, energy, financial-sector,
# food, telecom, etc.). Keyed by the industry label used in INDUSTRY_PROFILES.
# These are catalog/reference items (due dates vary by licence/state), shown in
# the per-company "By Company" view so a CS sees the A-to-Z picture.
# ───────────────────────────────────────────────────────────────────────────
_ENV_SRC = "https://cpcb.nic.in/"
_BEE_SRC = "https://beeindia.gov.in/"

# Reusable environmental block for manufacturing-heavy sectors.
_ENV_COMMON = [
    {"name": "Consent to Operate (CTO)", "form": "CTO renewal — SPCB",
     "regulation": "Water Act 1974 & Air Act 1981", "frequency": "annual",
     "description": "Consent to Operate from the State Pollution Control Board under the Water & Air Acts; renewed periodically (often annually/biennially) per plant.",
     "penalty": "Closure notice + prosecution under the Water/Air Acts.", "source": _ENV_SRC},
    {"name": "Hazardous Waste Annual Return (Form 4)", "form": "Form 4 — SPCB",
     "regulation": "Hazardous & Other Wastes Rules 2016", "frequency": "annual",
     "description": "Annual return of hazardous-waste generation, storage & disposal to the SPCB, by 30 June.",
     "penalty": "Prosecution under the Environment (Protection) Act.", "source": _ENV_SRC},
    {"name": "Environmental Statement (Form V)", "form": "Form V — SPCB",
     "regulation": "Environment (Protection) Rules 1986", "frequency": "annual",
     "description": "Annual environmental statement on water/energy/raw-material consumption & pollution, by 30 September.",
     "penalty": "EP Act penalties.", "source": _ENV_SRC},
    {"name": "Factories Act Annual Return", "form": "Form 21/22 — Factory Inspectorate",
     "regulation": "Factories Act 1948", "frequency": "annual",
     "description": "Annual & half-yearly returns on workers, hours, accidents; factory licence renewal.",
     "penalty": "Fine + imprisonment for occupier under s.92.", "source": "https://labour.gov.in/"},
]
_EPR_PLASTIC = {"name": "EPR — Plastic Packaging", "form": "EPR annual return — CPCB portal",
     "regulation": "Plastic Waste Management Rules 2016", "frequency": "annual",
     "description": "Extended Producer Responsibility targets & annual return for plastic packaging on the CPCB EPR portal.",
     "penalty": "Environmental compensation by CPCB.", "source": _ENV_SRC}
_CCTS = {"name": "Carbon Credit Trading Scheme (CCTS)", "form": "CCTS / ESCerts — BEE & Grid India",
     "regulation": "CCTS 2023 / Energy Conservation Act", "frequency": "annual",
     "description": "Obligated/energy-intensive entities meet GHG-intensity targets and trade Carbon Credit Certificates; legacy PAT cycle issues Energy Saving Certificates (ESCerts).",
     "penalty": "Environmental compensation per CCTS rules.", "source": _BEE_SRC}

SECTOR_MANDATES = {
    "Chemicals": _ENV_COMMON + [
        _EPR_PLASTIC, _CCTS,
        {"name": "PESO Licence", "form": "Licence — PESO/Chief Controller of Explosives",
         "regulation": "Explosives Act / Petroleum Act", "frequency": "annual",
         "description": "Licence for storage of solvents, petroleum & explosive/flammable chemicals; periodic renewal & inspection.",
         "penalty": "Suspension of licence + prosecution.", "source": "https://peso.gov.in/"},
        {"name": "MSIHC Rules — Major Accident Hazard", "form": "Safety report / on-site plan — SPCB",
         "regulation": "MSIHC Rules 1989", "frequency": "annual",
         "description": "Manufacture, Storage & Import of Hazardous Chemicals — safety reports, on-site emergency plans for MAH installations.",
         "penalty": "EP Act prosecution.", "source": _ENV_SRC},
    ],
    "Energy": [_CCTS] + [
        {"name": "Renewable Purchase Obligation (RPO)", "form": "RPO compliance report — SERC",
         "regulation": "Electricity Act 2003 / SERC RPO Regs", "frequency": "annual",
         "description": "Obligated entities meet their renewable purchase quota via green power or RECs; annual compliance report to the State Electricity Regulatory Commission.",
         "penalty": "RPO regulatory charges / penalties by SERC.", "source": "https://cercind.gov.in/"},
        {"name": "Renewable Energy Certificates (REC)", "form": "REC issuance/redemption — Grid India",
         "regulation": "CERC REC Regulations", "frequency": "monthly",
         "description": "Issuance, trading & redemption of RECs on power exchanges for renewable generators/obligated entities.",
         "penalty": "Forfeiture / regulatory action.", "source": "https://cercind.gov.in/"},
        {"name": "CEA Returns & CERC/SERC Tariff Filings", "form": "Tariff petitions / CEA returns",
         "regulation": "Electricity Act 2003", "frequency": "annual",
         "description": "Tariff determination petitions, true-up filings, and Central Electricity Authority statistical/safety returns.",
         "penalty": "Regulatory disallowance.", "source": "https://cea.nic.in/"},
    ],
    "Power": "Energy",   # alias — resolved below
    "Cement": _ENV_COMMON + [
        _CCTS,
        {"name": "Mining Lease & Mineral Returns", "form": "Monthly/annual returns — IBM",
         "regulation": "MMDR Act / MCDR Rules", "frequency": "monthly",
         "description": "Limestone mining lease compliance and mineral-conservation returns to the Indian Bureau of Mines.",
         "penalty": "Lease suspension.", "source": "https://ibm.gov.in/"},
    ],
    "Steel": _ENV_COMMON + [
        _CCTS,
        {"name": "Mining & Mineral Conservation Returns", "form": "Returns — IBM / State Mining Dept",
         "regulation": "MMDR Act 1957 / MCDR Rules 2017", "frequency": "monthly",
         "description": "Mining lease compliance, mineral-dispatch & conservation returns to the Indian Bureau of Mines.",
         "penalty": "Lease suspension + penalty.", "source": "https://ibm.gov.in/"},
    ],
    "Metals & Mining": _ENV_COMMON + [
        _CCTS,
        {"name": "MMDR / Mines Act Compliance", "form": "Returns + safety — IBM / DGMS",
         "regulation": "MMDR Act / Mines Act 1952", "frequency": "monthly",
         "description": "Mining lease, mineral-conservation returns (IBM) and mine-safety returns to the DGMS.",
         "penalty": "Lease suspension; DGMS prosecution.", "source": "https://ibm.gov.in/"},
        {"name": "Environmental Clearance Compliance", "form": "Half-yearly EC report — MoEFCC",
         "regulation": "EIA Notification 2006", "frequency": "half_yearly",
         "description": "Half-yearly compliance report on environmental-clearance conditions to the MoEF&CC/SEIAA.",
         "penalty": "EC revocation + EP Act action.", "source": "https://parivesh.nic.in/"},
    ],
    "Banking": [
        {"name": "RBI Regulatory Returns (DSB / RCA)", "form": "DSB returns — RBI XBRL",
         "regulation": "Banking Regulation Act 1949", "frequency": "quarterly",
         "description": "Off-site surveillance (DSB) returns, CRAR/Basel III disclosures, and risk-based supervision data to the RBI.",
         "penalty": "Monetary penalty u/s 47A BR Act.", "source": "https://www.rbi.org.in/"},
        {"name": "CRR / SLR Maintenance Reporting", "form": "Form A / Form VIII — RBI",
         "regulation": "RBI Act / BR Act", "frequency": "monthly",
         "description": "Fortnightly Cash Reserve Ratio and Statutory Liquidity Ratio maintenance & reporting.",
         "penalty": "Penal interest on shortfall.", "source": "https://www.rbi.org.in/"},
        {"name": "CRILC — Large Credit Reporting", "form": "CRILC return — RBI",
         "regulation": "RBI Master Directions", "frequency": "quarterly",
         "description": "Central Repository of Information on Large Credits — reporting of exposures ≥₹5 crore.",
         "penalty": "Supervisory action.", "source": "https://www.rbi.org.in/"},
    ],
    "Finance (NBFC)": [
        {"name": "RBI NBFC Returns (DNBS)", "form": "DNBS-01 to 10 — RBI COSMOS/XBRL",
         "regulation": "RBI Master Directions (NBFC)", "frequency": "quarterly",
         "description": "Periodic DNBS returns on financials, prudential norms, ALM and large exposures, per scale-based regulation.",
         "penalty": "Monetary penalty + supervisory action.", "source": "https://www.rbi.org.in/"},
        {"name": "Scale-Based Regulation Compliance", "form": "SBR disclosures — RBI",
         "regulation": "RBI SBR Framework 2022", "frequency": "annual",
         "description": "Compliance with the layer-wise (Base/Middle/Upper) prudential, governance & disclosure norms.",
         "penalty": "Supervisory action.", "source": "https://www.rbi.org.in/"},
        {"name": "FIU-IND / PMLA Reporting", "form": "CTR / STR — FINnet",
         "regulation": "PMLA 2002", "frequency": "monthly",
         "description": "Cash & suspicious transaction reports to the Financial Intelligence Unit.",
         "penalty": "PMLA penalties.", "source": "https://fiuindia.gov.in/"},
    ],
    "Insurance": [
        {"name": "IRDAI Financial & Investment Returns", "form": "Periodic returns — IRDAI BAP",
         "regulation": "Insurance Act 1938 / IRDAI Regs", "frequency": "quarterly",
         "description": "Financial, solvency, investment and public-disclosure returns to the IRDAI.",
         "penalty": "Penalty u/s 102 Insurance Act.", "source": "https://irdai.gov.in/"},
        {"name": "Public Disclosures (Forms L)", "form": "L-forms — website & IRDAI",
         "regulation": "IRDAI Public Disclosure Regs", "frequency": "quarterly",
         "description": "Quarterly/annual L-series public disclosures of financials & solvency.",
         "penalty": "IRDAI directions/penalty.", "source": "https://irdai.gov.in/"},
    ],
    "Pharma": _ENV_COMMON + [
        {"name": "CDSCO Drug Licence & GMP (Schedule M)", "form": "Licence / GMP audit — CDSCO/SLA",
         "regulation": "Drugs & Cosmetics Act 1940", "frequency": "annual",
         "description": "Manufacturing licence, Schedule-M GMP compliance, product approvals & periodic inspections.",
         "penalty": "Licence cancellation + prosecution.", "source": "https://cdsco.gov.in/"},
        {"name": "NPPA / DPCO Price Compliance", "form": "Form II/V price intimations — NPPA",
         "regulation": "Drugs (Prices Control) Order 2013", "frequency": "monthly",
         "description": "Price-ceiling compliance, price-change intimations & quarterly returns to the National Pharmaceutical Pricing Authority.",
         "penalty": "Overcharged amount + 15% interest recovery.", "source": "https://www.nppaindia.nic.in/"},
    ],
    "FMCG": [
        {"name": "FSSAI Licence & Annual Return", "form": "Form D-1 — FoSCoS",
         "regulation": "FSS Act 2006", "frequency": "annual",
         "description": "Food business licence and annual return (Form D-1) by 31 May; licence renewal.",
         "penalty": "Up to ₹5 lakh + licence action.", "source": "https://foscos.fssai.gov.in/"},
        {"name": "Legal Metrology — Packaged Commodities", "form": "Registration / declarations",
         "regulation": "Legal Metrology Act 2009", "frequency": "annual",
         "description": "Packaged-commodity declarations (MRP, net qty), packer/importer registration & stamping.",
         "penalty": "Fine up to ₹25,000+.", "source": "https://consumeraffairs.nic.in/"},
        _EPR_PLASTIC,
    ],
    "Consumer Durables": [
        {"name": "BIS Certification (ISI / CRS)", "form": "BIS licence / CRS registration",
         "regulation": "BIS Act 2016", "frequency": "annual",
         "description": "Mandatory product certification under ISI mark / Compulsory Registration Scheme.",
         "penalty": "Fine + imprisonment under BIS Act.", "source": "https://www.bis.gov.in/"},
        {"name": "EPR — E-Waste", "form": "EPR return — CPCB portal",
         "regulation": "E-Waste Management Rules 2022", "frequency": "annual",
         "description": "Extended Producer Responsibility targets & annual return for electronic waste.",
         "penalty": "Environmental compensation.", "source": _ENV_SRC},
        _EPR_PLASTIC,
    ],
    "Auto": _ENV_COMMON + [
        {"name": "Type Approval / Homologation (CMVR)", "form": "AIS certification — ARAI/ICAT",
         "regulation": "CMVR 1989 / Motor Vehicles Act", "frequency": "annual",
         "description": "Vehicle type approval, conformity of production & AIS standards certification.",
         "penalty": "Sales prohibition.", "source": "https://morth.nic.in/"},
        {"name": "EPR — Batteries & ELV", "form": "EPR returns — CPCB portal",
         "regulation": "Battery Waste Rules 2022 / ELV Rules 2025", "frequency": "annual",
         "description": "Extended Producer Responsibility for batteries and end-of-life vehicles.",
         "penalty": "Environmental compensation.", "source": _ENV_SRC},
    ],
    "Telecom": [
        {"name": "DoT Licence Compliance & AGR", "form": "AGR statements / LF & SUC returns",
         "regulation": "Telegraph Act / UL Licence", "frequency": "quarterly",
         "description": "Adjusted Gross Revenue statements, licence-fee & spectrum-usage-charge returns to the DoT.",
         "penalty": "Penalty + licence action.", "source": "https://dot.gov.in/"},
        {"name": "TRAI Performance & QoS Returns", "form": "PMR / QoS reports — TRAI",
         "regulation": "TRAI Act 1997", "frequency": "quarterly",
         "description": "Performance Monitoring Reports & Quality-of-Service compliance to TRAI.",
         "penalty": "Financial disincentives.", "source": "https://www.trai.gov.in/"},
    ],
    "Real Estate": [
        {"name": "RERA Project Registration & Updates", "form": "Quarterly updates — State RERA",
         "regulation": "RERA Act 2016", "frequency": "quarterly",
         "description": "Project registration and quarterly progress/financial updates on the State RERA portal; annual audit (Form 5).",
         "penalty": "Up to 5% of project cost + deregistration.", "source": "https://rera.gov.in/"},
        {"name": "Environmental Clearance Compliance", "form": "Half-yearly EC report — SEIAA",
         "regulation": "EIA Notification 2006", "frequency": "half_yearly",
         "description": "Half-yearly compliance report on EC conditions for building/area-development projects.",
         "penalty": "EC revocation.", "source": "https://parivesh.nic.in/"},
    ],
    "IT": [
        {"name": "STPI / SEZ Returns", "form": "Monthly/Quarterly/Annual — STPI/SEZ Online",
         "regulation": "STP Scheme / SEZ Act 2005", "frequency": "monthly",
         "description": "Monthly Progress Reports, softex filings & annual performance reports for STPI/SEZ units.",
         "penalty": "Loss of benefits.", "source": "https://www.stpi.in/"},
        {"name": "DPDP Act — Data Protection", "form": "Consent/breach processes (rules awaited)",
         "regulation": "Digital Personal Data Protection Act 2023", "frequency": "ongoing",
         "description": "Consent management, breach notification to the Data Protection Board, and (for SDFs) DPO/audit obligations.",
         "penalty": "Up to ₹250 crore per breach.", "source": "https://www.meity.gov.in/"},
    ],
}


def _sector_mandates(industry):
    """Resolve sector-specific mandates for an industry label (handles aliases
    like Power→Energy). Returns a list of normalised mandate dicts."""
    block = SECTOR_MANDATES.get(industry)
    if isinstance(block, str):           # alias
        block = SECTOR_MANDATES.get(block, [])
    out = []
    for i, m in enumerate(block or []):
        mm = dict(m)
        mm.setdefault("id", f"SEC-{industry[:4].upper()}-{i+1}")
        mm.setdefault("category", "Sector / Environmental")
        mm["regime"] = "Sector-specific"
        mm.setdefault("applies_to", f"{industry} companies")
        mm.setdefault("section_ref", mm.get("regulation", ""))
        mm.setdefault("sebi_source", mm.get("source") or "")
        mm["due_label"] = _due_label(mm)
        mm["frequency_label"] = {
            "quarterly": "Quarterly", "half_yearly": "Half-yearly", "annual": "Annual",
            "monthly": "Monthly", "event_based": "Event-based", "ongoing": "Ongoing",
        }.get(mm.get("frequency"), "Periodic")
        out.append(mm)
    return out


def get_company_compliance_profile(symbol, industry=""):
    """A-to-Z compliance picture for ONE company: every filing it must track,
    grouped into Listed (SEBI), Companies-Act (MCA), Sector-specific, and
    Cross-border / Conditional — merged with its live dated obligations so the
    CS sees both 'what applies' and 'what's pending'."""
    catalog = get_all_mandates()

    # Map each mandate id → its live obligation status for this company.
    comp = get_company_compliance(symbol)
    by_mandate = {}
    for ob in comp.get("obligations", []):
        mid = ob.get("mandate_id")
        # Keep the most urgent / nearest obligation per mandate.
        cur = by_mandate.get(mid)
        if not cur or (ob.get("due_date", "") and ob["due_date"] < cur.get("due_date", "9999")):
            by_mandate[mid] = ob

    def _attach(m):
        ob = by_mandate.get(m["id"])
        return {**m,
                "status": ob.get("status") if ob else None,
                "next_due": ob.get("due_date") if ob else None,
                "days_remaining": ob.get("days_remaining") if ob else None}

    sebi = [_attach(m) for m in catalog if m["regime"] == "SEBI"]
    mca = [_attach(m) for m in catalog if m["regime"] == "MCA"]
    crossborder = [_attach(m) for m in catalog
                   if m["regime"] in ("FEMA", "Tax & Labour")]
    sector = _sector_mandates(industry)

    return {
        "symbol": symbol,
        "industry": industry,
        "groups": [
            {"key": "sebi", "label": "Listed-Company (SEBI)", "color": "#4f46e5",
             "sub": "LODR · PIT · SAST · BRSR — filed with the exchanges", "items": sebi},
            {"key": "mca", "label": "Companies Act (MCA / ROC)", "color": "#0d9488",
             "sub": "Annual returns, event forms & registers", "items": mca},
            {"key": "sector", "label": f"Sector-Specific — {industry or 'General'}", "color": "#ea580c",
             "sub": "Regulator-specific filings for this industry", "items": sector},
            {"key": "crossborder", "label": "Cross-border, Tax & Labour", "color": "#db2777",
             "sub": "FEMA/RBI, income-tax, PF/ESI & POSH", "items": crossborder},
        ],
        "total": len(sebi) + len(mca) + len(sector) + len(crossborder),
    }


# ── Database Setup ──

def init_compliance_db():
    """Create compliance tracking tables."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS compliance_obligations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            mandate_id TEXT NOT NULL,
            period TEXT NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            filing_id INTEGER,
            filed_date TEXT,
            delay_days INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(symbol, mandate_id, period)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_compliance_symbol ON compliance_obligations(symbol)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_compliance_status ON compliance_obligations(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_compliance_due ON compliance_obligations(due_date)")

    # ── Migrations: add verification columns if missing ──
    existing_cols = {r[1] for r in c.execute("PRAGMA table_info(compliance_obligations)")}
    if "evidence_json" not in existing_cols:
        c.execute("ALTER TABLE compliance_obligations ADD COLUMN evidence_json TEXT DEFAULT ''")
    if "confidence" not in existing_cols:
        c.execute("ALTER TABLE compliance_obligations ADD COLUMN confidence TEXT DEFAULT ''")
    if "verified_at" not in existing_cols:
        c.execute("ALTER TABLE compliance_obligations ADD COLUMN verified_at TEXT DEFAULT ''")
    if "filing_url" not in existing_cols:
        c.execute("ALTER TABLE compliance_obligations ADD COLUMN filing_url TEXT DEFAULT ''")

    conn.commit()
    conn.close()


# ── Quarter Utilities ──

def get_quarter_end_dates(year):
    """Return quarter-end dates for a given financial year (Apr-Mar)."""
    return [
        date(year, 6, 30),      # Q1 (Apr-Jun)
        date(year, 9, 30),      # Q2 (Jul-Sep)
        date(year, 12, 31),     # Q3 (Oct-Dec)
        date(year + 1, 3, 31),  # Q4 (Jan-Mar)
    ]


def get_current_quarter():
    """Return current quarter info: (quarter_num, quarter_end_date, fy_year)."""
    today = date.today()
    m = today.month
    if m <= 3:
        return 4, date(today.year, 3, 31), today.year - 1
    elif m <= 6:
        return 1, date(today.year, 6, 30), today.year
    elif m <= 9:
        return 2, date(today.year, 9, 30), today.year
    else:
        return 3, date(today.year, 12, 31), today.year


def get_period_label(quarter_num, fy_year):
    """Return human-readable period label like 'Q1 FY2026-27'."""
    return f"Q{quarter_num} FY{fy_year}-{str(fy_year + 1)[-2:]}"


# ── Obligation Generation ──

def generate_obligations_for_company(symbol):
    """Generate compliance obligations for a watchlisted company for the current and recent quarters."""
    today = date.today()
    now_iso = datetime.now().isoformat()
    conn = get_connection()

    q_num, q_end, fy_year = get_current_quarter()

    # Generate for current quarter + previous 2 quarters
    quarters_to_check = []
    for offset in range(2, -1, -1):
        adjusted_q = q_num - offset
        adjusted_fy = fy_year
        while adjusted_q <= 0:
            adjusted_q += 4
            adjusted_fy -= 1
        q_ends = get_quarter_end_dates(adjusted_fy)
        q_end_date = q_ends[adjusted_q - 1]
        period = get_period_label(adjusted_q, adjusted_fy)
        quarters_to_check.append((adjusted_q, adjusted_fy, q_end_date, period))

    for mandate in SEBI_MANDATES:
        freq = mandate["frequency"]
        if freq == "quarterly":
            days_after = mandate.get("due_days_after_quarter", 30)
            for q, fy, q_end_dt, period in quarters_to_check:
                due = q_end_dt + timedelta(days=days_after)
                _upsert_obligation(conn, symbol, mandate["id"], period, due.isoformat(), now_iso)

        elif freq == "half_yearly":
            # Two filings a year. Either explicit calendar dates (`due_dates`)
            # or N days after each half-year end (Sep 30 / Mar 31).
            due_list = []
            if mandate.get("due_dates"):
                for mo, dy in mandate["due_dates"]:
                    # Place each on the most recent past/near occurrence.
                    yr = today.year if (mo, dy) <= (today.month, today.day) else today.year - 1
                    due_list.append((date(yr, mo, dy), f"{_cal.month_abbr[mo]} {yr}"))
                    # also the upcoming one this year
                    if yr == today.year - 1:
                        due_list.append((date(today.year, mo, dy), f"{_cal.month_abbr[mo]} {today.year}"))
            else:
                dh = mandate.get("due_days_after_half", 15)
                for he, lbl in ((date(today.year - 1, 9, 30), f"H1 FY{today.year-1}-{str(today.year)[-2:]}"),
                                (date(today.year, 3, 31), f"H2 FY{today.year-1}-{str(today.year)[-2:]}"),
                                (date(today.year, 9, 30), f"H1 FY{today.year}-{str(today.year+1)[-2:]}")):
                    due_list.append((he + timedelta(days=dh), lbl))
            for due, period in due_list:
                _upsert_obligation(conn, symbol, mandate["id"], period, due.isoformat(), now_iso)

        elif freq == "annual":
            due_month = mandate.get("due_month", 9)
            due_day = mandate.get("due_day", 30)
            if today.month > 3:
                due = date(today.year, due_month, due_day)
                period = f"FY{today.year - 1}-{str(today.year)[-2:]}"
            else:
                due = date(today.year - 1, due_month, due_day) if due_month > 3 else date(today.year, due_month, due_day)
                period = f"FY{today.year - 2}-{str(today.year - 1)[-2:]}"
            _upsert_obligation(conn, symbol, mandate["id"], period, due.isoformat(), now_iso)

        # event_based / monthly / ongoing: catalog-only (no dated obligation).

    conn.commit()
    conn.close()


def _upsert_obligation(conn, symbol, mandate_id, period, due_date, now_iso):
    """Insert obligation; also patch due_date if mandate rule was updated."""
    try:
        conn.execute(
            """INSERT OR IGNORE INTO compliance_obligations
               (symbol, mandate_id, period, due_date, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (symbol, mandate_id, period, due_date, now_iso, now_iso),
        )
        # If the mandate's due-days rule changed, correct existing rows.
        conn.execute(
            """UPDATE compliance_obligations
               SET due_date = ?, updated_at = ?
               WHERE symbol = ? AND mandate_id = ? AND period = ? AND due_date != ?""",
            (due_date, now_iso, symbol, mandate_id, period, due_date),
        )
    except sqlite3.IntegrityError:
        pass


# ── Filing Linkage ──

def link_filings_to_obligations(symbol):
    """Scan existing filings and link them to matching obligations."""
    conn = get_connection()
    filings = get_cached_filings(symbol)
    now_iso = datetime.now().isoformat()
    today = date.today()

    obligations = conn.execute(
        "SELECT * FROM compliance_obligations WHERE symbol = ?", (symbol,)
    ).fetchall()

    for ob in obligations:
        mandate = get_mandate_by_id(ob["mandate_id"])
        if not mandate:
            continue

        # Skip if already linked to a filing
        if ob["filing_id"]:
            continue

        keywords = mandate.get("keywords", [])
        due_date = ob["due_date"]

        try:
            due_dt = datetime.strptime(due_date, "%Y-%m-%d").date()
        except ValueError:
            continue

        # Search window: from 60 days before due date to 30 days after
        search_start = (due_dt - timedelta(days=60)).isoformat()
        search_end = (due_dt + timedelta(days=30)).isoformat()

        best_match = None
        best_score = 0

        for f in filings:
            sd = f.get("sort_date", f.get("date", ""))[:10]
            if not sd or sd < search_start or sd > search_end:
                continue
            title_lower = (f.get("title", "") + " " + f.get("description", "")).lower()
            score = sum(1 for kw in keywords if kw in title_lower)
            if score > best_score:
                best_score = score
                best_match = f

        if best_match and best_score >= 1:
            filed_date = best_match.get("sort_date", best_match.get("date", ""))[:10]
            try:
                filed_dt = datetime.strptime(filed_date, "%Y-%m-%d").date()
                delay = (filed_dt - due_dt).days
            except ValueError:
                delay = 0

            if delay <= 0:
                status = "submitted"
                delay = 0
            else:
                status = "delayed"

            conn.execute(
                """UPDATE compliance_obligations
                   SET status = ?, filing_id = ?, filed_date = ?, delay_days = ?, updated_at = ?
                   WHERE id = ?""",
                (status, best_match["id"], filed_date, delay, now_iso, ob["id"]),
            )

    # ── Multi-source cross-verification for past-due, unlinked obligations ──
    # Rather than relying on our shallow NSE announcement cache alone, we query
    # authoritative per-mandate NSE endpoints (integrated-filing-results, CG master,
    # SHP master) plus BSE announcements. An obligation is only marked "missed"
    # when multiple independent sources confirm the filing does not exist.
    # Otherwise it is "submitted" / "delayed" (if found anywhere) or "unverified".
    for ob in obligations:
        if ob["filing_id"]:
            continue
        if ob["status"] not in ("pending", "missed", "unverified"):
            continue
        try:
            due_dt = datetime.strptime(ob["due_date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if due_dt >= today:
            # Due date may have been patched forward by a mandate rule update.
            # Reset any stale past-due statuses back to pending.
            if ob["status"] in ("missed", "unverified", "delayed"):
                conn.execute(
                    "UPDATE compliance_obligations SET status='pending', filed_date=NULL, "
                    "delay_days=0, evidence_json='', confidence='', filing_url='', "
                    "updated_at=? WHERE id=?",
                    (now_iso, ob["id"]),
                )
            continue

        # Derive quarter-end from mandate + due_date
        mandate = get_mandate_by_id(ob["mandate_id"])
        if not mandate:
            continue
        if mandate.get("frequency") == "quarterly":
            days_after = mandate.get("due_days_after_quarter", 30)
            q_end = due_dt - timedelta(days=days_after)
        else:
            q_end = due_dt

        try:
            result = verify_obligation(symbol, ob["mandate_id"], q_end, due_dt)
        except Exception as e:
            print(f"[COMPLIANCE] verify_obligation failed for {symbol}/{ob['mandate_id']}: {e}")
            continue

        new_status = result["status"]
        filed_date = result.get("filed_date")
        delay_days = result.get("delay_days", 0)
        evidence_json = json.dumps(result.get("evidence", []))[:8000]
        confidence = result.get("confidence", "")
        # Extract a URL from evidence for UI display
        filing_url = ""
        for ev in result.get("evidence", []):
            m = ev.get("match") or {}
            if m.get("url"):
                filing_url = m["url"]
                break

        conn.execute(
            """UPDATE compliance_obligations
               SET status = ?, filed_date = ?, delay_days = ?,
                   evidence_json = ?, confidence = ?, verified_at = ?,
                   filing_url = ?, updated_at = ?
               WHERE id = ?""",
            (new_status, filed_date, delay_days, evidence_json, confidence,
             now_iso, filing_url, now_iso, ob["id"]),
        )

    conn.commit()
    conn.close()


# ── Compliance Refresh ──

_last_synced = None


def refresh_compliance(symbol=None):
    """Refresh compliance tracking. Compliance data (SEBI obligations) is public
    per-company, so we aggregate across ALL users' watchlists when refreshing.
    """
    global _last_synced
    init_compliance_db()
    symbols = [symbol] if symbol else get_all_watchlist_symbols()

    for sym in symbols:
        generate_obligations_for_company(sym)
        link_filings_to_obligations(sym)

    _last_synced = datetime.now().isoformat()


def get_last_synced():
    return _last_synced


# ── Queries ──

def get_compliance_dashboard(symbols=None):
    """Return data for the compliance dashboard.

    If `symbols` is provided, only obligations for those symbols are returned —
    use this to scope the dashboard to a single user's watchlist. When `symbols`
    is None, all obligations across all users are returned (admin/backfill view).
    An empty list returns an empty dashboard.

    Metric reconciliation:
      Total = Upcoming + Overdue + Completed + Future (pending, due > 30 days)
    All four buckets are mutually exclusive and exhaustive.
    """
    today = date.today()
    today_iso = today.isoformat()
    conn = get_connection()

    # Ensure table exists
    try:
        conn.execute("SELECT 1 FROM compliance_obligations LIMIT 1")
    except sqlite3.OperationalError:
        conn.close()
        init_compliance_db()
        conn = get_connection()

    if symbols is None:
        all_obligations = conn.execute(
            "SELECT * FROM compliance_obligations ORDER BY due_date DESC"
        ).fetchall()
    elif len(symbols) == 0:
        all_obligations = []
    else:
        placeholders = ",".join("?" for _ in symbols)
        all_obligations = conn.execute(
            f"SELECT * FROM compliance_obligations WHERE symbol IN ({placeholders}) "
            f"ORDER BY due_date DESC",
            tuple(symbols),
        ).fetchall()

    conn.close()

    # Enrich all
    all_enriched = [_enrich_obligation(dict(o)) for o in all_obligations]

    # ── Mutually exclusive buckets ──
    upcoming_cutoff = (today + timedelta(days=30)).isoformat()

    upcoming = []    # pending, due today through next 30 days
    overdue = []     # missed OR pending with due_date < today AND we have filing coverage
    completed = []   # submitted or delayed (any time)
    future = []      # pending, due > 30 days from now
    unverified = []  # past due but no filing coverage — can't honestly call it missed

    for ob in all_enriched:
        status = ob.get("status", "pending")
        due = ob.get("due_date", "")

        if status in ("submitted", "delayed"):
            completed.append(ob)
        elif status == "missed":
            overdue.append(ob)
        elif status == "unverified":
            unverified.append(ob)
        elif status == "pending":
            if due < today_iso:
                # Pending but past due → overdue
                overdue.append(ob)
            elif due <= upcoming_cutoff:
                upcoming.append(ob)
            else:
                future.append(ob)

    # Sort
    upcoming.sort(key=lambda x: x.get("due_date", ""))
    overdue.sort(key=lambda x: x.get("due_date", ""))
    completed.sort(key=lambda x: x.get("filed_date", "") or x.get("due_date", ""), reverse=True)
    unverified.sort(key=lambda x: x.get("due_date", ""))

    # ── Validation ──
    validation = _validate_dashboard(all_enriched, upcoming, overdue, completed, future, unverified)

    return {
        "upcoming": upcoming,
        "overdue": overdue,
        "completed": completed,
        "future": future,
        "unverified": unverified,
        "all": all_enriched,
        "total": len(all_enriched),
        "validation": validation,
        "last_synced": _last_synced,
    }


def _validate_dashboard(all_obs, upcoming, overdue, completed, future, unverified=None):
    """Validate that all metrics reconcile. Returns validation result dict."""
    unverified = unverified or []
    total = len(all_obs)
    bucket_sum = len(upcoming) + len(overdue) + len(completed) + len(future) + len(unverified)
    is_valid = (total == bucket_sum)

    # Check every obligation appears in exactly one bucket
    bucket_ids = set()
    for lst in [upcoming, overdue, completed, future, unverified]:
        for ob in lst:
            oid = ob.get("id")
            if oid in bucket_ids:
                is_valid = False  # duplicate
            bucket_ids.add(oid)

    if len(bucket_ids) != total:
        is_valid = False

    return {
        "is_valid": is_valid,
        "total": total,
        "bucket_sum": bucket_sum,
        "upcoming_count": len(upcoming),
        "overdue_count": len(overdue),
        "completed_count": len(completed),
        "future_count": len(future),
        "unverified_count": len(unverified),
    }


def get_company_compliance(symbol):
    """Return compliance data for a specific company with reconciled metrics."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM compliance_obligations WHERE symbol = ? ORDER BY due_date DESC",
            (symbol,),
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return {"obligations": [], "score": None, "history": {}, "health_tag": "unknown"}

    conn.close()
    obligations = [_enrich_obligation(dict(r)) for r in rows]

    # Compute score and history
    score = compute_compliance_score(obligations)
    history = compute_compliance_history(obligations)
    health_tag = compute_health_tag(obligations, score)

    return {
        "obligations": obligations,
        "score": score,
        "history": history,
        "health_tag": health_tag,
    }


def _enrich_obligation(ob):
    """Add mandate details, computed fields, and SEBI source to an obligation dict."""
    mandate = get_mandate_by_id(ob.get("mandate_id", ""))
    if mandate:
        ob["mandate_name"] = mandate["name"]
        ob["regulation"] = mandate.get("regulation", "")
        ob["mandate_category"] = mandate.get("category", "")
        ob["mandate_description"] = mandate.get("description", "")
        # SEBI rebuilds regulation URLs on every amendment. Resolve live so we
        # never serve a dead link; falls back to the regulations index.
        key = mandate.get("sebi_source_key")
        ob["sebi_source"] = get_regulation_url(key) if key else SEBI_REGULATIONS_INDEX
        ob["sebi_circular"] = mandate.get("sebi_circular", "")
        ob["section_ref"] = mandate.get("section_ref", "")
        ob["filed_via"] = mandate.get("filed_via", "")
    else:
        ob["mandate_name"] = ob.get("mandate_id", "Unknown")
        ob["regulation"] = ""
        ob["mandate_category"] = ""
        ob["mandate_description"] = ""
        ob["sebi_source"] = SEBI_REGULATIONS_INDEX
        ob["sebi_circular"] = ""
        ob["section_ref"] = ""
        ob["filed_via"] = ""

    # Countdown
    try:
        due_dt = datetime.strptime(ob["due_date"], "%Y-%m-%d").date()
        days_remaining = (due_dt - date.today()).days
        ob["days_remaining"] = days_remaining
        ob["is_urgent"] = 0 < days_remaining <= 7
        ob["is_overdue"] = days_remaining < 0
    except (ValueError, KeyError):
        ob["days_remaining"] = None
        ob["is_urgent"] = False
        ob["is_overdue"] = False

    # Parse verification evidence JSON for UI
    ev = ob.get("evidence_json")
    if ev:
        try:
            ob["evidence"] = json.loads(ev)
        except Exception:
            ob["evidence"] = []
    else:
        ob["evidence"] = []

    return ob


# ── Compliance Score ──

def compute_compliance_score(obligations):
    """Compute compliance score (0-100) for a set of obligations.
    Only scores resolved obligations (submitted, delayed, missed).
    Returns None if no resolved obligations exist."""
    if not obligations:
        return None

    scored = [o for o in obligations if o.get("status") in ("submitted", "delayed", "missed")]
    if not scored:
        return None

    total_score = 0
    for o in scored:
        status = o.get("status")
        if status == "submitted":
            total_score += 100
        elif status == "delayed":
            delay = o.get("delay_days", 0) or 0
            total_score += max(30, 100 - (delay * 5))
        elif status == "missed":
            total_score += 0

    return round(total_score / len(scored))


def compute_compliance_history(obligations):
    """Compute compliance history stats. All counts must sum to total."""
    total = len(obligations)
    if not total:
        return {"total": 0, "submitted": 0, "delayed": 0, "missed": 0,
                "pending": 0, "unverified": 0, "resolved": 0, "on_time_rate": 0}

    counts = {"submitted": 0, "delayed": 0, "missed": 0, "pending": 0, "unverified": 0}
    for o in obligations:
        s = o.get("status", "pending")
        if s in counts:
            counts[s] += 1
        else:
            counts["pending"] += 1  # safety: never lose a row from the checksum

    resolved = counts["submitted"] + counts["delayed"] + counts["missed"]
    on_time_rate = round(counts["submitted"] / resolved * 100) if resolved > 0 else 0

    return {
        "total": total,
        **counts,
        "resolved": resolved,
        "on_time_rate": on_time_rate,
    }


def compute_health_tag(obligations, score):
    """Compute health status tag for a company.

    Tags:
      - "critical"  → score < 40 OR >50% obligations missed
      - "at_risk"   → score 40-69 OR any upcoming with no filing + some delays
      - "stable"    → score >= 70 and mostly on-time
      - "unknown"   → no resolved obligations to judge
    """
    if not obligations:
        return "unknown"

    history = compute_compliance_history(obligations)
    resolved = history["resolved"]

    if resolved == 0:
        return "unknown"

    missed_pct = history["missed"] / resolved * 100 if resolved > 0 else 0

    if score is None:
        return "unknown"
    elif score < 40 or missed_pct > 50:
        return "critical"
    elif score < 70 or (history["delayed"] > 0 and missed_pct > 20):
        return "at_risk"
    else:
        return "stable"


# ── All Mandates List ──

def _due_label(m):
    """Human-readable 'when it's due' for the catalog card."""
    freq = m.get("frequency")
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if freq == "quarterly":
        d = m.get("due_days_after_quarter")
        return f"Within {d} days of quarter end" if d else "Each quarter"
    if freq == "half_yearly":
        if m.get("due_dates"):
            return " & ".join(f"{months[mo]} {dy}" for mo, dy in m["due_dates"])
        d = m.get("due_days_after_half")
        return f"Within {d} days of half-year" if d else "Half-yearly"
    if freq == "annual":
        mo, dy = m.get("due_month"), m.get("due_day")
        return f"By {months[mo]} {dy}" if mo else "Annual"
    if freq == "monthly":
        return "Monthly"
    if freq == "event_based":
        return "On occurrence"
    if freq == "ongoing":
        return "Continuous"
    return "—"


def get_all_mandates():
    """Return the full mandate catalog with source links resolved. SEBI links
    resolve dynamically against SEBI's legal index; other regimes use their
    direct source URL. Adds a human `due_label` and `frequency_label`."""
    freq_labels = {
        "quarterly": "Quarterly", "half_yearly": "Half-yearly", "annual": "Annual",
        "monthly": "Monthly", "event_based": "Event-based", "ongoing": "Ongoing",
    }
    out = []
    for m in SEBI_MANDATES:
        m_copy = dict(m)
        key = m_copy.pop("sebi_source_key", None)
        if key:
            m_copy["sebi_source"] = get_regulation_url(key)
        else:
            m_copy["sebi_source"] = m_copy.get("source") or SEBI_REGULATIONS_INDEX
        m_copy.setdefault("regime", "SEBI")
        m_copy["due_label"] = _due_label(m)
        m_copy["frequency_label"] = freq_labels.get(m.get("frequency"), "—")
        out.append(m_copy)
    return out
