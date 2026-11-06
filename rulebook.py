"""Jurisdiction-aware compliance rulebook.

The listed-company catalog in compliance.py tracks SEBI obligations for NSE
symbols. This module is the general engine: it takes an *entity profile*
(company type, flags like "GST registered" or "has FDI", state, directors)
and answers two questions for any Indian entity, listed or not:

    1. Which obligations apply, and when is each one due?
    2. If it is late, roughly what does the delay cost?

Every rule carries a `jurisdiction` so other countries can be added as data
later without touching the scheduler. Only "IN" exists today.

Penalty figures are indicative. Late fees on Indian forms depend on details
we do not always know (share capital, turnover, tax amount), so each estimate
states its basis and the UI labels it "indicative — verify at source".
Rules last reviewed: 2026-09-28.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

RULEBOOK_REVIEWED = "2026-09-29"

JURISDICTIONS = {"IN": "India"}

ENTITY_TYPES = {
    "private": "Private limited company",
    "public_unlisted": "Public unlisted company",
    "listed": "Listed company",
    "opc": "One Person Company",
    "section8": "Section 8 company",
    "llp": "Limited Liability Partnership",
}
COMPANY_TYPES = ["private", "public_unlisted", "listed", "opc", "section8"]
ALL_TYPES = COMPANY_TYPES + ["llp"]

# Profile questions. Keys are stored on the entity; labels drive the form.
FLAGS = {
    "gst_registered": "GST registered",
    "gst_qrmp": "Files GST returns quarterly (QRMP scheme)",
    "gst_turnover_2cr": "Annual GST turnover above ₹2 crore",
    "gst_turnover_5cr": "Annual GST turnover above ₹5 crore",
    "tds_deductor": "Deducts TDS",
    "tax_audit": "Tax audit applies (s.44AB)",
    "transfer_pricing": "Has international / specified domestic transactions",
    "has_employees": "Has employees on payroll",
    "employees_10": "10 or more employees",
    "pf_covered": "Registered under EPF",
    "esi_covered": "Registered under ESI",
    "fdi": "Has foreign investment (FDI)",
    "odi": "Has overseas direct investment (ODI)",
    "ecb": "Has external commercial borrowings (ECB)",
    "msme_dues": "Owes MSME suppliers for more than 45 days",
    "csr": "CSR provisions apply (s.135)",
    "cost_audit": "Cost audit applies",
    "secretarial_audit": "Secretarial audit applies (s.204)",
    "top1000": "Among top 1,000 listed by market cap",
    "small_company": "Small company (paid-up capital up to ₹10 crore and turnover up to ₹100 crore)",
    "dormant_company": "Dormant company (s.455)",
    "startup_private": "DPIIT-recognised start-up (private company)",
    "lodr_small": "Exempt under LODR Reg 15(2) (paid-up equity up to ₹10 crore and net worth up to ₹25 crore)",
    "llp_audit": "Accounts audited under the LLP Act (turnover above ₹40 lakh and contribution above ₹25 lakh)",
    "small_llp": "Small LLP (contribution up to ₹5 crore and turnover up to ₹50 crore)",
    "sme_listed": "Listed on an SME exchange (BSE SME / NSE Emerge)",
    "top100": "Among top 100 listed by market cap",
    "cfs_required": "Has a subsidiary, associate or joint venture (consolidated statements, s.129(3))",
    "issue_proceeds_unutilised": "Has unused proceeds from a public, rights, preferential or QIP issue",
    "tcs_collector": "Collects TCS",
    "sft_reportable": "Has SFT-reportable transactions (e.g. ₹10 lakh+ from one person for shares or debentures)",
    "job_work": "Sends inputs or capital goods to job workers",
    "gst_lut_exports": "Exports or supplies to SEZ without paying IGST (under LUT)",
    "employees_5": "5 or more employees",
    "opi": "Makes overseas portfolio investment, or staff get overseas-group ESOPs",
}

# Grouping used for the calendar legend and filters.
REGIMES = ["Companies Act", "LLP Act", "SEBI", "Income Tax", "GST", "Labour", "FEMA / RBI"]

_SRC = {
    "mca": "https://www.mca.gov.in/content/mca/global/en/mca/e-filing/company-forms-download.html",
    "llp": "https://www.mca.gov.in/content/mca/global/en/mca/e-filing/llp-forms-download.html",
    "sebi": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=3&smid=0",
    "it": "https://www.incometax.gov.in/iec/foportal/",
    "gst": "https://www.gst.gov.in/",
    "epfo": "https://www.epfindia.gov.in/",
    "esic": "https://www.esic.gov.in/",
    "fema": "https://www.rbi.org.in/Scripts/BS_FemaNotifications.aspx",
    "firms": "https://firms.rbi.org.in/firms/",
    "posh": "https://wcd.nic.in/act/sexual-harassment-women-workplace-prevention-prohibition-and-redressal-act-2013",
    "pt_mh": "https://mahagst.gov.in/en/profession-tax",
    "pt_ka": "https://pt.kar.nic.in/",
    "dir3": "https://taxguru.in/company-law/dir-3-kyc-changed-31-march-2026-files-trap-avoid.html",
    "aoc4cfs": "https://www.mca.gov.in/content/mca/global/en/mca/e-filing/annual-filings/form-aoc4cfs.html",
    "itc04": "https://tutorial.gst.gov.in/userguide/inputtaxcredit/Manual_itc04.htm",
    "lut": "https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/rules/cgst_rules/active/chapter10/rule96a_v1.00.html",
    "lwf_mh": "https://public.mlwb.in/",
    "lwf_ka": "https://klwb.karnataka.gov.in/",
    "pt_mh_notif": "https://www.mahagst.gov.in/en/profession-tax-and-allied-acts-notifications",
    "sdd_cert": "https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx?page=20241018-44",
    "opi": "https://rbi.org.in/scripts/NotificationUser.aspx?Mode=0&Id=12710",
}

# ── Event catalogue ─────────────────────────────────────────────────────────
# Logging one of these on an entity creates the dated follow-up filings.
EVENT_TYPES = {
    "incorporated": "Company incorporated",
    "director_appointed": "Director appointed",
    "director_resigned": "Director resigned or removed",
    "kmp_appointed": "KMP appointed (CFO / CS / CEO)",
    "kmp_resigned": "KMP resigned",
    "md_appointed": "MD / whole-time director / manager appointed",
    "director_contact_changed": "Director's mobile, email or address changed",
    "shares_allotted": "Shares allotted",
    "fdi_received": "Foreign investment money received",
    "share_transfer_nr": "Share transfer with a non-resident",
    "charge_created": "Charge created or modified",
    "charge_satisfied": "Charge satisfied",
    "resolution_passed": "Special resolution or s.179(3) resolution passed",
    "registered_office_changed": "Registered office changed",
    "auditor_appointed": "Auditor appointed (casual vacancy)",
    "auditor_resigned": "Auditor resigned",
    "sbo_declaration": "BEN-1 declaration received",
    "capital_increased": "Authorised capital increased",
    "board_meeting_scheduled": "Board meeting scheduled",
    "board_meeting_held": "Board meeting held",
    "general_meeting_held": "General meeting held",
    "results_approved": "Board approved financial results",
    "llp_partner_changed": "LLP partner or designated partner joined, left or changed details",
    "llp_agreement_changed": "LLP agreement changed",
    "downstream_investment": "Downstream investment made (foreign-owned or controlled company)",
    "became_focc": "Became foreign-owned or controlled while holding shares in other Indian companies",
}


def _r(id, regime, name, form, law, types, schedule, penalty, source, description,
       flags=None, any_flags=None, not_flags=None, states=None, category="Filing"):
    return {
        "id": id, "jurisdiction": "IN", "regime": regime, "name": name, "form": form,
        "law": law, "types": types, "schedule": schedule, "penalty": penalty,
        "source": _SRC.get(source, source), "description": description,
        "flags": flags or [], "any_flags": any_flags or [], "not_flags": not_flags or [],
        "states": states or [], "category": category,
    }


_SLAB = {"type": "slab_fee"}
# Sections 139 and 157 (ADT-1, DIR-3C) keep a 1x band for the first 15 days;
# every other Rule 12 form pays 2x from day one.
_SLAB_139 = {"type": "slab_fee", "first_band_1x": True}
_LLP_SLAB = {"type": "llp_slab", "beyond_360_per_day": True}
# CGST Rule 61(1)(ii): quarterly GSTR-3B is due on the 22nd or the 24th by
# the state of the principal place of business. "" (state not set) takes the
# earlier date.
_GST_X_STATES = ['Chhattisgarh', 'Madhya Pradesh', 'Gujarat', 'Maharashtra', 'Karnataka', 'Goa', 'Kerala', 'Tamil Nadu', 'Telangana', 'Andhra Pradesh', 'Dadra and Nagar Haveli and Daman and Diu', 'Puducherry', 'Andaman and Nicobar Islands', 'Lakshadweep', '']
_GST_Y_STATES = ['Himachal Pradesh', 'Punjab', 'Uttarakhand', 'Haryana', 'Rajasthan', 'Uttar Pradesh', 'Bihar', 'Sikkim', 'Arunachal Pradesh', 'Nagaland', 'Manipur', 'Mizoram', 'Tripura', 'Meghalaya', 'Assam', 'West Bengal', 'Jharkhand', 'Odisha', 'Jammu and Kashmir', 'Ladakh', 'Chandigarh', 'Delhi']
_LSF = {"type": "text", "text": "Late Submission Fee under RBI's LSF framework (amount- and delay-based)."}

RULES = [
    # ════════ Companies Act ════════
    _r("IN-MCA-AGM", "Companies Act", "Annual General Meeting", "AGM",
       "Companies Act s.96", ["private", "public_unlisted", "listed", "section8"],
       {"kind": "annual", "month": 9, "day": 30},
       {"type": "text", "text": "Up to ₹1 lakh on the company and ₹5,000/day while it continues (s.99)."},
       "mca", "Hold the AGM within six months of the financial year end.", category="Meetings"),
    _r("IN-MCA-AOC4", "Companies Act", "Financial statements", "AOC-4 / AOC-4 XBRL",
       "Companies Act s.137", ["private", "public_unlisted", "listed", "section8"],
       {"kind": "annual", "month": 10, "day": 30, "after_agm": 30},
       {"type": "per_day", "amount": 100}, "mca",
       "File audited financial statements within 30 days of the AGM."),
    _r("IN-MCA-AOC4-OPC", "Companies Act", "Financial statements (OPC)", "AOC-4",
       "Companies Act s.137", ["opc"],
       {"kind": "annual", "month": 9, "day": 27},
       {"type": "per_day", "amount": 100}, "mca",
       "OPCs file within 180 days of the financial year end."),
    _r("IN-MCA-AOC4-CFS", "Companies Act", "Consolidated financial statements", "AOC-4 CFS (XBRL filers: inside AOC-4 XBRL)",
       "Companies Act s.129(3), s.137; Companies (Accounts) Rules r.6, r.12", ["private", "public_unlisted", "listed", "section8"],
       {"kind": "annual", "month": 10, "day": 30, "after_agm": 30},
       {"type": "per_day", "amount": 100, "note": "additional fee, no cap; s.137(3) penalty is separate"},
       "aoc4cfs", "File consolidated statements within 30 days of the AGM, as a linked form with AOC-4. Unlisted wholly-owned intermediate subsidiaries whose Indian holding company files consolidated statements may be exempt (r.6).",
       flags=["cfs_required"]),
    _r("IN-MCA-MGT7", "Companies Act", "Annual return", "MGT-7 / MGT-7A",
       "Companies Act s.92", COMPANY_TYPES,
       {"kind": "annual", "month": 11, "day": 29, "after_agm": 60},
       {"type": "per_day", "amount": 100}, "mca",
       "Annual return within 60 days of the AGM. OPCs and small companies use MGT-7A."),
    _r("IN-MCA-MGT15", "Companies Act", "Report on AGM", "MGT-15",
       "Companies Act s.121; Management and Administration Rules r.31(2)", ["listed"],
       {"kind": "annual", "month": 10, "day": 30, "after_agm": 30},
       {"type": "slab_fee", "note": "plus s.121(3) penalty: ₹1 lakh + ₹500/day (up to ₹5 lakh) on the company"},
       "mca", "Listed companies file a report on each AGM within 30 days of its conclusion (date assumes a 30 September AGM)."),
    _r("IN-MCA-ADT1", "Companies Act", "Auditor appointment", "ADT-1",
       "Companies Act s.139", COMPANY_TYPES,
       {"kind": "annual", "month": 10, "day": 15, "after_agm": 15},
       _SLAB_139, "mca",
       "Intimate the auditor's appointment within 15 days of the AGM. Due only in an appointment year; mark N/A otherwise."),
    _r("IN-MCA-DPT3", "Companies Act", "Return of deposits", "DPT-3",
       "Companies (Acceptance of Deposits) Rules r.16", COMPANY_TYPES,
       {"kind": "annual", "month": 6, "day": 30},
       _SLAB, "mca",
       "Return of deposits and outstanding money not treated as deposits, as of 31 March."),
    _r("IN-MCA-MSME1", "Companies Act", "MSME dues return", "MSME-1",
       "Companies Act s.405", COMPANY_TYPES,
       {"kind": "half_fixed", "dates": [(4, 30, "Oct–Mar"), (10, 31, "Apr–Sep")]},
       {"type": "text", "text": "Penalty under s.405(4) on the company and every officer in default."},
       "mca", "Half-yearly return of amounts owed to MSME suppliers for more than 45 days.",
       flags=["msme_dues"]),
    _r("IN-MCA-PAS6", "Companies Act", "Share capital reconciliation", "PAS-6",
       "Companies (Prospectus and Allotment) Rules r.9A(8)", ["public_unlisted"],
       {"kind": "half_fixed", "dates": [(5, 30, "Oct–Mar"), (11, 29, "Apr–Sep")]},
       _SLAB, "mca", "Half-yearly reconciliation of share capital audit, within 60 days of each half-year end."),
    _r("IN-MCA-PAS6-PVT", "Companies Act", "Share capital reconciliation", "PAS-6",
       "Companies (Prospectus and Allotment) Rules r.9B(5) read with r.9A(8)", ["private", "section8"],
       {"kind": "half_fixed", "dates": [(5, 30, "Oct–Mar"), (11, 29, "Apr–Sep")]},
       _SLAB, "mca",
       "Private companies other than small companies hold securities in demat form (from 30 Jun 2025) and file PAS-6 half-yearly. Tick 'Small company' in the profile if exempt.",
       not_flags=["small_company"]),
    _r("IN-MCA-CSR2", "Companies Act", "CSR report", "CSR-2",
       "Companies (Accounts) Rules r.12(1B) read with s.135", COMPANY_TYPES,
       {"kind": "annual", "month": 10, "day": 30, "after_agm": 30},
       _SLAB, "mca", "CSR report filed as a linked form with AOC-4, within 30 days of the AGM.", flags=["csr"]),
    _r("IN-MCA-CRA4", "Companies Act", "Cost audit report", "CRA-4",
       "Companies (Cost Records and Audit) Rules", COMPANY_TYPES,
       {"kind": "annual", "month": 10, "day": 27},
       _SLAB, "mca", "File the cost audit report within 30 days of receiving it.", flags=["cost_audit"]),
    _r("IN-MCA-CRA2", "Companies Act", "Cost auditor appointment", "CRA-2",
       "Companies Act s.148(3); Cost Records and Audit Rules r.6(2)", COMPANY_TYPES,
       {"kind": "fy_dates", "dates": [(9, 27, "Appointment")]},
       _SLAB, "mca",
       "Intimate this year's cost auditor appointment within 30 days of the board meeting, and in any case by 27 September (180 days from 1 April).",
       flags=["cost_audit"]),
    _r("IN-MCA-MR3", "Companies Act", "Secretarial audit report", "MR-3",
       "Companies Act s.204", COMPANY_TYPES,
       {"kind": "annual", "month": 9, "day": 30},
       {"type": "text", "text": "Penalty under s.204(4) on the company, officers and the PCS."},
       "mca", "Secretarial audit report, annexed to the board's report before the AGM.",
       any_flags=["secretarial_audit"]),
    _r("IN-MCA-BOARD", "Companies Act", "Board meeting this quarter", "Board meeting",
       "Companies Act s.173(1)", ["private", "public_unlisted", "listed"],
       {"kind": "quarterly_after", "days": 0},
       {"type": "text", "text": "Four meetings a year with no gap over 120 days. No specific penalty, so s.450 applies: ₹10,000 on the company and each officer in default plus ₹1,000/day (up to ₹2 lakh for the company, ₹50,000 per officer). Failing to give notice: ₹25,000 on the officer (s.173(4))."},
       "mca", "Hold at least one board meeting this quarter.", category="Meetings",
       not_flags=["small_company", "dormant_company", "startup_private"]),
    _r("IN-MCA-BOARD-HALF", "Companies Act", "Board meeting this half-year", "Board meeting",
       "Companies Act s.173(5); Section 8 exemption G.S.R. 466(E)", ["opc", "section8"],
       {"kind": "half_fixed", "dates": [(6, 30, "Jan–Jun"), (12, 31, "Jul–Dec")]},
       {"type": "text", "text": "No specific penalty, so s.450 applies: ₹10,000 plus ₹1,000/day, up to ₹2 lakh for the company."},
       "mca", "At least one board meeting in each half of the calendar year; for an OPC, at least 90 days apart. An OPC with one director is exempt; Section 8 companies need one meeting every six calendar months.",
       category="Meetings"),
    _r("IN-MCA-BOARD-HALF-SMALL", "Companies Act", "Board meeting this half-year", "Board meeting",
       "Companies Act s.173(5)", ["private", "public_unlisted"],
       {"kind": "half_fixed", "dates": [(6, 30, "Jan–Jun"), (12, 31, "Jul–Dec")]},
       {"type": "text", "text": "No specific penalty, so s.450 applies: ₹10,000 plus ₹1,000/day, up to ₹2 lakh for the company."},
       "mca", "Small, dormant and start-up private companies: at least one board meeting in each half of the calendar year, at least 90 days apart.",
       any_flags=["small_company", "dormant_company", "startup_private"], category="Meetings"),
    _r("IN-MCA-MBP1", "Companies Act", "Directors' disclosure of interest", "MBP-1",
       "Companies Act s.184(1); Meetings of Board Rules r.9", ["private", "public_unlisted", "listed"],
       {"kind": "fy_dates", "dates": [(6, 30, "First board meeting")]},
       {"type": "text", "text": "₹1 lakh on each director who fails to disclose (s.184(4)); half that for small companies, OPCs and start-ups (s.446B)."},
       "mca", "Every director gives Form MBP-1 at the first board meeting of the financial year (tracked by 30 June). Most companies take DIR-8 declarations at the same meeting.",
       category="Meetings"),
    _r("IN-MCA-BOARD-FIRST", "Companies Act", "First board meeting", "Board meeting",
       "Companies Act s.173(1)", COMPANY_TYPES,
       {"kind": "event", "triggers": ["incorporated"], "days": 30},
       {"type": "text", "text": "No specific penalty, so s.450 applies: ₹10,000 on the company and each officer in default plus ₹1,000/day, up to ₹2 lakh (company) and ₹50,000 (officer)."},
       "mca", "Hold the first board meeting within 30 days of incorporation. A sole-director OPC records the resolution in the minute book instead.",
       category="Meetings"),
    _r("IN-MCA-INC20A", "Companies Act", "Commencement of business", "INC-20A",
       "Companies Act s.10A", COMPANY_TYPES,
       {"kind": "event", "triggers": ["incorporated"], "days": 180},
       {"type": "flat", "amount": 50000, "note": "on the company; each officer in default also pays ₹1,000/day up to ₹1 lakh"}, "mca",
       "Declare receipt of subscription money within 180 days of incorporation. Every officer in default also faces ₹1,000/day, capped at ₹1 lakh."),
    _r("IN-MCA-DIR12", "Companies Act", "Change in directors / KMP", "DIR-12",
       "Companies Act s.170", COMPANY_TYPES,
       {"kind": "event", "triggers": ["director_appointed", "director_resigned", "kmp_appointed",
                                      "kmp_resigned", "md_appointed"], "days": 30},
       _SLAB, "mca", "File particulars of the appointment or cessation within 30 days."),
    _r("IN-MCA-MR1", "Companies Act", "MD / WTD appointment return", "MR-1",
       "Companies Act s.196", ["public_unlisted", "listed"],
       {"kind": "event", "triggers": ["md_appointed"], "days": 60},
       _SLAB, "mca", "Return of appointment of MD, WTD or manager within 60 days."),
    _r("IN-MCA-MGT14", "Companies Act", "Filing of resolutions", "MGT-14",
       "Companies Act s.117", COMPANY_TYPES,
       {"kind": "event", "triggers": ["resolution_passed"], "days": 30},
       _SLAB, "mca",
       "File special resolutions (and s.179(3) board resolutions, except for private companies) within 30 days."),
    _r("IN-MCA-PAS3", "Companies Act", "Return of allotment", "PAS-3",
       "Companies Act s.39 / s.42", COMPANY_TYPES,
       {"kind": "event", "triggers": ["shares_allotted"], "days": 15},
       {"type": "text", "text": "For private placement, ₹1,000/day up to ₹25 lakh (s.42(10))."},
       "mca", "File the return of allotment within 15 days (private placement) or 30 days (other allotments)."),
    _r("IN-MCA-CHG1", "Companies Act", "Charge creation / modification", "CHG-1",
       "Companies Act s.77", COMPANY_TYPES,
       {"kind": "event", "triggers": ["charge_created"], "days": 30},
       {"type": "text", "text": "Days 31–60: the Registrar may allow registration with an additional fee of 3× the normal fee (small companies and OPCs) or 6× (others). Days 61–120: on application, with an ad valorem fee on top (0.025% of the amount secured, up to ₹1 lakh, for small companies and OPCs; 0.05%, up to ₹5 lakh, for others). Beyond 120 days only the Regional Director can extend time (CHG-8, s.87)."},
       "mca", "Register the charge with the ROC within 30 days of creation."),
    _r("IN-MCA-CHG4", "Companies Act", "Charge satisfaction", "CHG-4",
       "Companies Act s.82", COMPANY_TYPES,
       {"kind": "event", "triggers": ["charge_satisfied"], "days": 30},
       {"type": "text", "text": "Additional fees under the charge table."},
       "mca", "Intimate satisfaction of the charge within 30 days."),
    _r("IN-MCA-INC22", "Companies Act", "Change of registered office", "INC-22",
       "Companies Act s.12", COMPANY_TYPES,
       {"kind": "event", "triggers": ["registered_office_changed"], "days": 30},
       {"type": "slab_fee", "note": "3× to 18× if filed late twice within 365 days"}, "mca", "Notify the ROC of the new registered office within 30 days."),
    _r("IN-MCA-ADT1-EVT", "Companies Act", "Auditor appointment (casual vacancy)", "ADT-1",
       "Companies Act s.139", COMPANY_TYPES,
       {"kind": "event", "triggers": ["auditor_appointed"], "days": 15},
       _SLAB_139, "mca", "Intimate the appointment within 15 days."),
    _r("IN-MCA-ADT3", "Companies Act", "Auditor resignation statement", "ADT-3",
       "Companies Act s.140(2); Audit and Auditors Rules r.8", COMPANY_TYPES,
       {"kind": "event", "triggers": ["auditor_resigned"], "days": 30},
       {"type": "text", "text": "On the resigning auditor, not the company: ₹50,000 or the audit fee, whichever is less, plus ₹500/day, up to ₹2 lakh (s.140(3))."},
       "mca", "The resigning auditor files ADT-3 within 30 days. The board fills the casual vacancy within the same 30 days (s.139(8)); log 'Auditor appointed' to raise the ADT-1."),
    _r("IN-MCA-BEN2", "Companies Act", "Significant beneficial owner return", "BEN-2",
       "Companies Act s.90", COMPANY_TYPES,
       {"kind": "event", "triggers": ["sbo_declaration"], "days": 30},
       _SLAB, "mca", "File BEN-2 within 30 days of receiving a BEN-1 declaration."),
    _r("IN-MCA-SH7", "Companies Act", "Increase in authorised capital", "SH-7",
       "Companies Act s.64", COMPANY_TYPES,
       {"kind": "event", "triggers": ["capital_increased"], "days": 30},
       {"type": "text", "text": "Additional fees on delay; penalty ₹500/day up to ₹5 lakh on the company (s.64(2))."},
       "mca", "Notify the ROC of the alteration within 30 days."),
    _r("IN-MCA-MINUTES", "Companies Act", "Sign board minutes", "Minutes (SS-1)",
       "Companies Act s.118 / SS-1", COMPANY_TYPES,
       {"kind": "event", "triggers": ["board_meeting_held"], "days": 30},
       {"type": "text", "text": "₹25,000 on the company and ₹5,000 on each officer in default (s.118(11))."},
       "mca", "Circulate draft minutes within 15 days and sign within 30 days of the meeting.",
       category="Meetings"),
    _r("IN-MCA-DIR3-UPDATE", "Companies Act", "Director KYC update", "DIR-3 KYC Web",
       "Appointment and Qualification of Directors Rules r.12A", COMPANY_TYPES + ["llp"],
       {"kind": "event", "triggers": ["director_contact_changed"], "days": 30},
       {"type": "flat", "amount": 500, "note": "fee for every update filing; ₹5,000 applies only to a late 3-yearly KYC or DIN reactivation"}, "dir3",
       "Update a director's mobile, email or address within 30 days of the change."),

    # ════════ LLP Act ════════
    _r("IN-LLP-F11", "LLP Act", "Annual return", "Form 11",
       "LLP Act s.35", ["llp"], {"kind": "annual", "month": 5, "day": 30},
       _LLP_SLAB, "llp", "Annual return within 60 days of the financial year end."),
    _r("IN-LLP-F8", "LLP Act", "Statement of account and solvency", "Form 8",
       "LLP Act s.34", ["llp"], {"kind": "annual", "month": 10, "day": 30},
       _LLP_SLAB, "llp", "Statement of account and solvency by 30 October."),
    _r("IN-LLP-F3F4", "LLP Act", "Change in partners or LLP agreement", "Form 4 / Form 3",
       "LLP Act s.23(2), s.25(2); LLP Rules r.21, r.22", ["llp"],
       {"kind": "event", "triggers": ["llp_partner_changed", "llp_agreement_changed"], "days": 30},
       {"type": "text", "text": "Additional fee from 1× the normal fee (up to 15 days late) rising to 25× (small LLP) or 50× (others) beyond 360 days; plus ₹10,000 each on the LLP and every designated partner for a Form 4 default (s.25(4))."},
       "llp", "File Form 4 within 30 days of a partner or designated partner joining, leaving or changing details, and Form 3 within 30 days of any change to the LLP agreement. Admitting a partner usually needs both."),

    # ════════ Income tax ════════
    _r("IN-IT-ADV", "Income Tax", "Advance tax", "Challan 280",
       "Income-tax Act 2025 ss.403–408 (1961 Act ss.207–211 up to FY 2025-26)", ALL_TYPES,
       {"kind": "fy_dates", "dates": [(6, 15, "Instalment 1 (15%)"), (9, 15, "Instalment 2 (45%)"),
                                      (12, 15, "Instalment 3 (75%)"), (3, 15, "Instalment 4 (100%)")]},
       {"type": "text", "text": "Interest at 1% per month under s.424 / s.425 of the Income-tax Act 2025 (formerly s.234B / s.234C)."},
       "it", "Pay advance tax in four instalments."),
    _r("IN-IT-TDSPAY", "Income Tax", "TDS deposit", "Challan 281",
       "Income-tax Act 2025 s.397(3)(a)", ALL_TYPES,
       {"kind": "monthly", "day": 7, "exceptions": {3: (4, 30)}},
       {"type": "text", "text": "Interest at 1.5% per month on tax deducted but not paid (s.398(3)(a)(ii); formerly s.201(1A))."},
       "it", "Deposit tax deducted by the 7th of the next month (30 April for March).",
       flags=["tds_deductor"]),
    _r("IN-IT-TDSRET", "Income Tax", "TDS return", "Form 138 / 140 / 144 (earlier 24Q / 26Q / 27Q)",
       "Income-tax Act 2025 s.397(3)(b) with Income-tax Rules 2026 r.219", ALL_TYPES,
       {"kind": "quarterly_fixed", "dates": {1: (7, 31), 2: (10, 31), 3: (1, 31), 4: (5, 31)}},
       {"type": "per_day", "amount": 200, "note": "capped at the TDS amount (s.427; s.234E for periods before April 2026)"},
       "it", "Quarterly TDS statements.", flags=["tds_deductor"]),
    _r("IN-IT-FORM131", "Income Tax", "Issue non-salary TDS certificates", "Form 131 (formerly Form 16A)",
       "Income-tax Act 2025 s.395(4)(a); Income-tax Rules 2026 r.215(1)", ALL_TYPES,
       {"kind": "quarterly_fixed", "dates": {1: (8, 15), 2: (11, 15), 3: (2, 15), 4: (6, 15)}},
       {"type": "per_day", "amount": 500, "note": "capped at the tax deductible"},
       "it", "Download Form 131 from TRACES and issue it to each deductee within 15 days of the quarterly statement due date.",
       flags=["tds_deductor"]),
    _r("IN-IT-TCSPAY", "Income Tax", "TCS deposit", "Challan 281",
       "Income-tax Act 2025 s.394; Income-tax Rules 2026 r.218(2)", ALL_TYPES,
       {"kind": "monthly", "day": 7, "exceptions": {3: (4, 30)}},
       {"type": "text", "text": "Interest at 1.5% per month from collection to payment, 1% per month where tax was not collected (s.398(3))."},
       "it", "Deposit tax collected at source by the 7th of the next month (30 April for March).", flags=["tcs_collector"]),
    _r("IN-IT-TCSRET", "Income Tax", "TCS return", "Form 143 (formerly 27EQ)",
       "Income-tax Act 2025 s.397(3)(b); Income-tax Rules 2026 r.219", ALL_TYPES,
       {"kind": "quarterly_fixed", "dates": {1: (7, 31), 2: (10, 31), 3: (1, 31), 4: (5, 31)}},
       {"type": "per_day", "amount": 200, "note": "capped at the TCS amount (s.427); penalty ₹10,000–₹1 lakh if more than a month late"},
       "it", "Quarterly TCS statement, due on the last day of the month after each quarter (31 May for Q4).", flags=["tcs_collector"]),
    _r("IN-IT-SFT", "Income Tax", "Statement of specified financial transactions", "Form 165 (SFT; formerly Form 61A)",
       "Income-tax Act 2025 s.508; Income-tax Rules 2026 r.237", ALL_TYPES,
       {"kind": "annual", "month": 5, "day": 31},
       {"type": "per_day", "amount": 200, "cap": 100000, "note": "fee under s.427(3) from the FY 2026-27 statement; a late FY 2025-26 Form 61A falls under old s.271FA at ₹500/day"},
       "it", "Annual statement of reportable transactions by 31 May, e.g. ₹10 lakh or more received from one person for shares or debentures, buy-backs, or cash over ₹2 lakh by audited entities. Mark N/A for a year without such transactions.",
       flags=["sft_reportable"]),
    _r("IN-IT-FORM16", "Income Tax", "Issue salary TDS certificates", "Form 130 (formerly Form 16)",
       "Income-tax Rules 2026 r.215(1) with Income-tax Act 2025 s.395(4)", ALL_TYPES,
       {"kind": "annual", "month": 6, "day": 15},
       {"type": "per_day", "amount": 500, "note": "per certificate, capped at the tax deductible on it"},
       "it", "Issue salary TDS certificates to employees by 15 June.", flags=["tds_deductor", "has_employees"]),
    _r("IN-IT-TAXAUDIT", "Income Tax", "Tax audit report", "Form 3CA/3CB-3CD (Form 26 from tax year 2026-27)",
       "Income-tax Act 1961 s.44AB / Income-tax Act 2025 s.63", ALL_TYPES,
       {"kind": "annual", "month": 9, "day": 30, "overrides": {"FY2025-26": "2026-10-21"}},
       {"type": "text", "text": "FY 2025-26 and earlier: 0.5% of turnover, up to ₹1.5 lakh (s.271B, 1961 Act). From tax year 2026-27: a fee of ₹75,000 if up to a month late, ₹1.5 lakh after that (s.428(c), Income-tax Act 2025)."},
       "it", "Upload the tax audit report by 30 September. FY 2025-26: extended to 21 October 2026 (CBDT press release, 28 Sep 2026).",
       flags=["tax_audit"], not_flags=["transfer_pricing"]),
    _r("IN-IT-TAXAUDIT-TP", "Income Tax", "Tax audit report (transfer pricing)", "Form 3CA/3CB-3CD (Form 26 from tax year 2026-27)",
       "Income-tax Act 1961 s.44AB / Income-tax Act 2025 s.63", ALL_TYPES,
       {"kind": "annual", "month": 10, "day": 31},
       {"type": "text", "text": "FY 2025-26 and earlier: 0.5% of turnover, up to ₹1.5 lakh (s.271B, 1961 Act). From tax year 2026-27: a fee of ₹75,000 if up to a month late, ₹1.5 lakh after that (s.428(c), Income-tax Act 2025)."},
       "it", "Tax audit report where Form 3CEB is also due: one month before the 30 November return date.",
       flags=["tax_audit", "transfer_pricing"]),
    _r("IN-IT-3CEB", "Income Tax", "Transfer pricing report", "Form 3CEB",
       "Income-tax Act s.92E", ALL_TYPES,
       {"kind": "annual", "month": 10, "day": 31},
       {"type": "flat", "amount": 100000}, "it",
       "Accountant's report on international transactions.", flags=["transfer_pricing"]),
    _r("IN-IT-ITR", "Income Tax", "Income tax return", "ITR-6",
       "Income-tax Act s.139(1)", COMPANY_TYPES,
       {"kind": "annual", "month": 10, "day": 31, "overrides": {"FY2025-26": "2026-11-21"}},
       {"type": "flat", "amount": 5000, "note": "s.234F, plus interest u/s 234A"},
       "it", "Company return of income. FY 2025-26: extended to 21 November 2026 (CBDT press release, 28 Sep 2026).", not_flags=["transfer_pricing"]),
    _r("IN-IT-ITR-TP", "Income Tax", "Income tax return (transfer pricing)", "ITR-6",
       "Income-tax Act s.139(1)", COMPANY_TYPES,
       {"kind": "annual", "month": 11, "day": 30},
       {"type": "flat", "amount": 5000, "note": "s.234F, plus interest u/s 234A"},
       "it", "Company return when Form 3CEB is required.", flags=["transfer_pricing"]),
    _r("IN-IT-ITR-LLP", "Income Tax", "Income tax return", "ITR-5",
       "Income-tax Act 1961 s.139(1) as amended by Finance Act 2026 (Income-tax Act 2025 s.263(1)(c))", ["llp"],
       {"kind": "annual", "month": 8, "day": 31},
       {"type": "flat", "amount": 5000, "note": "s.234F"},
       "it", "LLP return where no audit applies: 31 August from FY 2025-26 (Finance Act 2026).",
       not_flags=["tax_audit", "llp_audit", "transfer_pricing"]),
    _r("IN-IT-ITR-LLP-AUDIT", "Income Tax", "Income tax return (audited)", "ITR-5",
       "Income-tax Act s.139(1)", ["llp"],
       {"kind": "annual", "month": 10, "day": 31, "overrides": {"FY2025-26": "2026-11-21"}},
       {"type": "flat", "amount": 5000, "note": "s.234F"},
       "it", "LLP return where accounts are audited (tax audit or LLP Act audit). FY 2025-26: extended to 21 November 2026 (CBDT press release, 28 Sep 2026).",
       any_flags=["tax_audit", "llp_audit"], not_flags=["transfer_pricing"]),
    _r("IN-IT-ITR-LLP-TP", "Income Tax", "Income tax return (transfer pricing)", "ITR-5",
       "Income-tax Act s.139(1) Explanation 2(aa)", ["llp"],
       {"kind": "annual", "month": 11, "day": 30},
       {"type": "flat", "amount": 5000, "note": "s.234F"},
       "it", "LLP return when a Form 3CEB report is required.", flags=["transfer_pricing"]),

    # ════════ GST ════════
    _r("IN-GST-R1", "GST", "Outward supplies", "GSTR-1",
       "CGST Act s.37", ALL_TYPES, {"kind": "monthly", "day": 11},
       {"type": "per_day", "amount": 50, "cap": 10000, "note": "₹20/day for nil returns; lower caps for smaller turnover"},
       "gst", "Monthly statement of outward supplies.", flags=["gst_registered"], not_flags=["gst_qrmp"]),
    _r("IN-GST-R1-Q", "GST", "Outward supplies (QRMP)", "GSTR-1",
       "CGST Act s.37", ALL_TYPES, {"kind": "quarterly_nextmonth", "day": 13},
       {"type": "per_day", "amount": 50, "cap": 5000, "note": "₹2,000 cap if turnover up to ₹1.5 crore; nil returns ₹20/day up to ₹500"},
       "gst", "Quarterly statement under the QRMP scheme.", flags=["gst_registered", "gst_qrmp"]),
    _r("IN-GST-3B", "GST", "Summary return and tax payment", "GSTR-3B",
       "CGST Act s.39", ALL_TYPES, {"kind": "monthly", "day": 20},
       {"type": "per_day", "amount": 50, "cap": 10000, "note": "plus 18% interest on late tax"},
       "gst", "Monthly summary return with tax payment.", flags=["gst_registered"], not_flags=["gst_qrmp"]),
    _r("IN-GST-3B-Q", "GST", "Summary return (QRMP)", "GSTR-3B",
       "CGST Act s.39; CGST Rules r.61(1)(ii)", ALL_TYPES, {"kind": "quarterly_nextmonth", "day": 22},
       {"type": "per_day", "amount": 50, "cap": 5000, "note": "₹2,000 cap if turnover up to ₹1.5 crore; nil returns ₹20/day up to ₹500; plus 18% interest"},
       "gst", "Quarterly summary return under QRMP, by the 22nd (southern and western states; used when the state is not set).",
       flags=["gst_registered", "gst_qrmp"], states=_GST_X_STATES),
    _r("IN-GST-3B-Q-24", "GST", "Summary return (QRMP)", "GSTR-3B",
       "CGST Act s.39; CGST Rules r.61(1)(ii)", ALL_TYPES, {"kind": "quarterly_nextmonth", "day": 24},
       {"type": "per_day", "amount": 50, "cap": 5000, "note": "₹2,000 cap if turnover up to ₹1.5 crore; nil returns ₹20/day up to ₹500; plus 18% interest"},
       "gst", "Quarterly summary return under QRMP, by the 24th (northern and eastern states and UTs).",
       flags=["gst_registered", "gst_qrmp"], states=_GST_Y_STATES),
    _r("IN-GST-PMT06", "GST", "Monthly tax payment (QRMP)", "PMT-06",
       "CGST Act s.39(7)", ALL_TYPES,
       {"kind": "monthly", "day": 25, "skip_months": [3, 6, 9, 12]},
       {"type": "text", "text": "18% interest on tax paid late."},
       "gst", "Pay tax for the first two months of each quarter.", flags=["gst_registered", "gst_qrmp"]),
    _r("IN-GST-R9", "GST", "Annual return", "GSTR-9",
       "CGST Act s.44", ALL_TYPES, {"kind": "annual", "month": 12, "day": 31},
       {"type": "per_day", "amount": 50, "note": "capped at 0.04% of turnover in the State or UT (Notification 07/2023-CT)"},
       "gst", "Annual GST return (optional up to ₹2 crore turnover).", flags=["gst_registered", "gst_turnover_2cr"],
       not_flags=["gst_turnover_5cr"]),
    _r("IN-GST-R9-5CR", "GST", "Annual return", "GSTR-9",
       "CGST Act s.44", ALL_TYPES, {"kind": "annual", "month": 12, "day": 31},
       {"type": "per_day", "amount": 200, "note": "upper bound: ₹100/day capped at 0.04% of turnover up to ₹20 crore; ₹200/day capped at 0.5% above (Notification 07/2023-CT)"},
       "gst", "Annual GST return.", flags=["gst_registered", "gst_turnover_5cr"]),
    _r("IN-GST-R9C", "GST", "Reconciliation statement", "GSTR-9C",
       "CGST Act s.44", ALL_TYPES, {"kind": "annual", "month": 12, "day": 31},
       {"type": "text", "text": "Filed with GSTR-9; late fee as for the annual return."},
       "gst", "Self-certified reconciliation statement.", flags=["gst_registered", "gst_turnover_5cr"]),
    _r("IN-GST-ITC04-H", "GST", "Goods sent to job workers (half-yearly)", "ITC-04",
       "CGST Act s.143; CGST Rules r.45(3)", ALL_TYPES,
       {"kind": "half_fixed", "dates": [(4, 25, "Oct–Mar"), (10, 25, "Apr–Sep")]},
       {"type": "text", "text": "No late fee; general penalty up to ₹25,000 each under CGST and SGST (s.125). Inputs not back within a year (capital goods: 3 years) are treated as supplied."},
       "itc04", "Half-yearly declaration of goods sent to or received from job workers, where last year's turnover was above ₹5 crore.",
       flags=["gst_registered", "job_work", "gst_turnover_5cr"]),
    _r("IN-GST-ITC04-A", "GST", "Goods sent to job workers (annual)", "ITC-04",
       "CGST Act s.143; CGST Rules r.45(3)", ALL_TYPES,
       {"kind": "annual", "month": 4, "day": 25},
       {"type": "text", "text": "No late fee; general penalty up to ₹25,000 each under CGST and SGST (s.125)."},
       "itc04", "Annual declaration of goods sent to or received from job workers by 25 April, where last year's turnover was ₹5 crore or less.",
       flags=["gst_registered", "job_work"], not_flags=["gst_turnover_5cr"]),
    _r("IN-GST-LUT", "GST", "Letter of Undertaking for exports", "GST RFD-11 (LUT)",
       "CGST Rules r.96A; IGST Act s.16(3)", ALL_TYPES,
       {"kind": "fy_dates", "dates": [(3, 31, "LUT for the next year")]},
       {"type": "text", "text": "No late fee. Exports or SEZ supplies made before the LUT is filed can attract IGST plus 18% interest."},
       "lut", "File a fresh LUT for each financial year before the first export or SEZ supply without IGST; file by 31 March to cover exports from 1 April.",
       flags=["gst_registered", "gst_lut_exports"]),

    # ════════ Labour ════════
    _r("IN-LAB-PF", "Labour", "PF contribution and ECR", "ECR",
       "Code on Social Security 2020 and EPF Scheme 2026 (formerly EPF Act 1952 s.6)", ALL_TYPES, {"kind": "monthly", "day": 15},
       {"type": "text", "text": "Interest at 12% a year (Code s.127; EPF Act s.7Q for older defaults) plus damages of 1% of arrears a month (Code s.128; defaults before 14 Jun 2024 used the 5–25% slab under s.14B)."},
       "epfo", "Remit PF and file the electronic challan-cum-return by the 15th.", flags=["pf_covered"]),
    _r("IN-LAB-ESI", "Labour", "ESI contribution", "ESI challan",
       "Code on Social Security 2020 s.29 (ESI Act 1948 repealed 21 Nov 2025)", ALL_TYPES, {"kind": "monthly", "day": 15},
       {"type": "text", "text": "Interest at 12% a year plus damages on arrears."},
       "esic", "Pay ESI contributions by the 15th.", flags=["esi_covered"]),
    _r("IN-LAB-PT-MH", "Labour", "Professional tax (Maharashtra)", "PT return",
       "Maharashtra PT Act; PT Rules r.11(3)(c) as amended 28 Feb 2026", ALL_TYPES, {"kind": "monthly", "day": 15, "offset": 0},
       {"type": "text", "text": "Interest and late fee under the Maharashtra PT Act."},
       "pt_mh", "Monthly PT payment and return by the 15th of the month (employers whose previous-year liability was under ₹1 lakh file once a year, by 15 March).",
       flags=["has_employees"], states=["Maharashtra"]),
    _r("IN-LAB-PT-KA", "Labour", "Professional tax (Karnataka)", "PT payment",
       "Karnataka PT Act", ALL_TYPES, {"kind": "monthly", "day": 20},
       {"type": "text", "text": "Interest and penalty under the Karnataka PT Act."},
       "pt_ka", "Pay PT deducted by the 20th of the next month.",
       flags=["has_employees"], states=["Karnataka"]),
    _r("IN-LAB-PTEC-MH", "Labour", "Profession tax on the entity (Maharashtra)", "PTEC (MTR-6 challan)",
       "Maharashtra PT Act s.8(2)", ALL_TYPES,
       {"kind": "fy_dates", "dates": [(3, 31, "Annual")]},
       {"type": "text", "text": "Interest on the unpaid ₹2,500 (s.9(3)) and a discretionary penalty of 10% of the tax (s.10)."},
       "pt_mh_notif", "Pay the company's or LLP's own ₹2,500 profession tax for the year by 31 March.", states=["Maharashtra"]),
    _r("IN-LAB-LWF-MH", "Labour", "Labour welfare fund (Maharashtra)", "Form A-1 cum return",
       "Maharashtra Labour Welfare Fund Act s.6BB", ALL_TYPES,
       {"kind": "half_fixed", "dates": [(7, 15, "Jan–Jun, due Jul"), (1, 15, "Jul–Dec, due Jan")]},
       {"type": "text", "text": "Interest of 1.5% a month for the first three months and 2% a month after, once a demand notice goes unpaid (s.6B)."},
       "lwf_mh", "Pay ₹25 per employee plus the ₹75 employer share for everyone on the register on 30 June and 31 December (5 or more employees).",
       any_flags=["employees_5", "employees_10"], states=["Maharashtra"]),
    _r("IN-LAB-LWF-KA", "Labour", "Labour welfare fund (Karnataka)", "Form D",
       "Karnataka Labour Welfare Fund Act s.7A", ALL_TYPES,
       {"kind": "annual_calendar", "month": 1, "day": 15},
       {"type": "text", "text": "Interest of 12% a year for three months and 18% after, once a demand notice goes unpaid (s.7B)."},
       "lwf_ka", "Pay ₹50 per employee plus the ₹100 employer share for everyone on the register on 31 December, with Form D (10 or more persons since 7 January 2026).",
       flags=["employees_10"], states=["Karnataka"]),
    _r("IN-LAB-POSH", "Labour", "POSH annual report", "ICC annual report",
       "POSH Act s.21 / Rule 14", ALL_TYPES, {"kind": "annual_calendar", "month": 1, "day": 31},
       {"type": "text", "text": "Fine up to ₹50,000 on the employer (s.26(1)); on a repeat conviction up to twice that, plus cancellation of the licence or registration (s.26(2)). Report dates vary by state."},
       "posh", "Internal Committee's annual report to the employer and District Officer.",
       flags=["employees_10"]),

    # ════════ FEMA / RBI ════════
    _r("IN-FEMA-FLA", "FEMA / RBI", "Foreign liabilities and assets", "FLA return",
       "FEMA / RBI Master Direction", ALL_TYPES, {"kind": "annual", "month": 7, "day": 15},
       {"type": "flat", "amount": 7500, "note": "LSF per return (A.P. (DIR) Circular 16/2022); available up to 3 years from the due date"}, "firms", "Annual return on foreign liabilities and assets.", any_flags=["fdi", "odi"]),
    _r("IN-FEMA-APR", "FEMA / RBI", "ODI annual performance report", "APR",
       "FEMA (Overseas Investment) Rules", ALL_TYPES, {"kind": "annual", "month": 12, "day": 31},
       {"type": "flat", "amount": 7500, "note": "LSF per APR per foreign entity; available up to 3 years from the due date"}, "fema", "Annual performance report for each overseas entity.", flags=["odi"]),
    _r("IN-FEMA-ECB2", "FEMA / RBI", "ECB-2 return", "ECB-2",
       "FEM (Borrowing and Lending) Regulations 2018 Reg 16(1)(c), as amended Feb 2026", ALL_TYPES, {"kind": "monthly", "day": 7},
       _LSF, "fema", "File ECB-2 only for a month in which ECB money was drawn down or debt was serviced, within 7 calendar days of month end. Mark other months N/A.",
       flags=["ecb"]),
    _r("IN-FEMA-ALLOT", "FEMA / RBI", "Allot shares against foreign inflow", "Allotment",
       "FEMA (Non-debt Instruments) Rules", ALL_TYPES,
       {"kind": "event", "triggers": ["fdi_received"], "days": 60},
       {"type": "text", "text": "Unallotted money must be refunded within 15 days after the 60-day window."},
       "fema", "Issue shares within 60 days of receiving the inward remittance."),
    _r("IN-FEMA-FCGPR", "FEMA / RBI", "Reporting of FDI allotment", "FC-GPR",
       "FEMA (Non-debt Instruments) Rules", ALL_TYPES,
       {"kind": "event", "triggers": ["shares_allotted"], "days": 30},
       _LSF, "firms", "Report the allotment to a non-resident within 30 days.", flags=["fdi"]),
    _r("IN-FEMA-FCTRS", "FEMA / RBI", "Transfer with a non-resident", "FC-TRS",
       "FEMA (Non-debt Instruments) Rules", ALL_TYPES,
       {"kind": "event", "triggers": ["share_transfer_nr"], "days": 60},
       _LSF, "firms", "Report within 60 days of the transfer of equity instruments or the receipt or remittance of funds, whichever is earlier."),
    _r("IN-FEMA-OPI", "FEMA / RBI", "Overseas portfolio investment return", "Form OPI",
       "FEMA (Overseas Investment) Regulations 2022 reg 10(3)", ALL_TYPES,
       {"kind": "half_fixed", "dates": [(5, 30, "Oct–Mar"), (11, 29, "Apr–Sep")]},
       {"type": "flat", "amount": 7500, "note": "RBI late submission fee per return, up to 3 years from the due date"},
       "opi", "Report overseas portfolio investment made or sold in the half-year (including overseas-group ESOPs of staff) within 60 days of 30 Sep / 31 Mar. Mark N/A for a half-year without such transactions.",
       flags=["opi"]),
    _r("IN-FEMA-DI", "FEMA / RBI", "Reporting of downstream investment", "Form DI",
       "FEM (Mode of Payment and Reporting of NDI) Regulations reg 4(7); NDI Rules r.22-23", ALL_TYPES,
       {"kind": "event", "triggers": ["downstream_investment", "became_focc"], "days": 30},
       {"type": "text", "text": "Late submission fee: ₹7,500 + 0.025% of the amount × years of delay, capped at the amount."},
       "firms", "A foreign-owned or controlled company reports each downstream investment on FIRMS within 30 days.",
       flags=["fdi"]),

    # ════════ SEBI (listed only) ════════
    _r("IN-SEBI-REG33", "SEBI", "Financial results", "Reg 33 results",
       "SEBI LODR Reg 33", ["listed"], {"kind": "quarterly_after", "days": 45, "q4_days": 60},
       {"type": "per_day", "amount": 5000, "note": "per exchange (SEBI SOP)"},
       "sebi", "Quarterly results within 45 days; audited annual results within 60 days."),
    _r("IN-SEBI-REG32", "SEBI", "Statement of deviation in use of issue proceeds", "Reg 32(1)",
       "SEBI LODR Reg 32(1)", ["listed"], {"kind": "quarterly_after", "days": 45, "q4_days": 60},
       {"type": "per_day", "amount": 1000, "note": "per exchange (SEBI SOP)"},
       "sebi", "Quarterly statement of any deviation in how issue proceeds were used, filed in the Integrated Filing (Financial) until the proceeds are fully used.",
       flags=["issue_proceeds_unutilised"]),
    _r("IN-SEBI-REG47", "SEBI", "Newspaper publication of results", "Reg 47 advertisement",
       "SEBI LODR Reg 47", ["listed"],
       {"kind": "event", "triggers": ["results_approved"], "days": 2},
       {"type": "text", "text": "Not in the SEBI SOP fine schedule; exposure is SEBI adjudication."},
       "sebi", "Within 48 hours of the board approving results, advertise in one English national and one regional-language daily, with a QR code linking to the full results.",
       not_flags=["sme_listed"]),
    _r("IN-SEBI-REG31", "SEBI", "Shareholding pattern", "Reg 31",
       "SEBI LODR Reg 31", ["listed"], {"kind": "quarterly_after", "days": 21},
       {"type": "per_day", "amount": 2000, "note": "per exchange (SEBI SOP)"},
       "sebi", "Quarterly shareholding pattern within 21 days."),
    _r("IN-SEBI-REG27", "SEBI", "Integrated filing (governance)", "Reg 27",
       "SEBI LODR Reg 27", ["listed"], {"kind": "quarterly_after", "days": 30},
       {"type": "per_day", "amount": 2000, "note": "per exchange (SEBI SOP)"},
       "sebi", "Quarterly corporate governance report within 30 days."),
    _r("IN-SEBI-REG13", "SEBI", "Investor grievance report", "Reg 13(3)",
       "SEBI LODR Reg 13", ["listed"], {"kind": "quarterly_after", "days": 30},
       {"type": "per_day", "amount": 1000, "note": "per exchange (SEBI SOP)"},
       "sebi", "Quarterly statement on redressal of investor grievances, filed in the Integrated Filing (Governance) within 30 days of quarter end."),
    _r("IN-SEBI-REG74", "SEBI", "Depository compliance certificate", "Reg 74(5)",
       "SEBI Depositories Regulations Reg 74(5)", ["listed"], {"kind": "quarterly_after", "days": 15},
       {"type": "text", "text": "Action under the SEBI Act."}, "sebi",
       "RTA certificate on dematerialisation requests."),
    _r("IN-SEBI-REG76", "SEBI", "Reconciliation of share capital audit", "Reg 76",
       "SEBI Depositories Regulations Reg 76", ["listed"], {"kind": "quarterly_after", "days": 30},
       {"type": "text", "text": "Action under the SEBI Act."}, "sebi",
       "Quarterly reconciliation of share capital audit by a practising CA, CS or cost accountant, submitted within 30 days of quarter end."),
    _r("IN-SEBI-REG23", "SEBI", "Related party transactions", "Reg 23(9)",
       "SEBI LODR Reg 23(9)", ["listed"], {"kind": "half_fixed", "dates": [(5, 30, "Oct–Mar"), (11, 14, "Apr–Sep")]},
       {"type": "per_day", "amount": 5000, "note": "per exchange (SEBI SOP)"}, "sebi",
       "Half-yearly RPT disclosure, filed with the half-year results.", not_flags=["lodr_small"]),
    _r("IN-SEBI-REG24A", "SEBI", "Secretarial compliance report", "Reg 24A",
       "SEBI LODR Reg 24A", ["listed"], {"kind": "annual", "month": 5, "day": 30},
       {"type": "per_day", "amount": 2000, "note": "per exchange (SEBI SOP)"}, "sebi",
       "Annual secretarial compliance report within 60 days of year end.", not_flags=["lodr_small", "sme_listed"]),
    _r("IN-SEBI-REG34", "SEBI", "Annual report to exchanges", "Reg 34",
       "SEBI LODR Reg 34", ["listed"], {"kind": "annual", "month": 9, "day": 8},
       {"type": "per_day", "amount": 2000, "note": "per exchange (SEBI SOP)"}, "sebi",
       "Submit the annual report on or before starting dispatch to shareholders: at least 21 clear days before the AGM, so by 8 Sep for a 30 Sep AGM (top-100 companies hold the AGM by 31 Aug)."),
    _r("IN-SEBI-BRSR", "SEBI", "Business responsibility and sustainability report", "BRSR",
       "SEBI LODR Reg 34(2)(f)", ["listed"], {"kind": "annual", "month": 9, "day": 8},
       {"type": "text", "text": "Part of the annual report; SOP fines apply."}, "sebi",
       "BRSR for the top 1,000 listed companies.", flags=["top1000"]),
    _r("IN-SEBI-SAST31-4", "SEBI", "Annual no-encumbrance declaration (promoters)", "SAST Reg 31(4) declaration",
       "SEBI SAST Reg 31(4)-(5)", ["listed"], {"kind": "annual", "month": 4, "day": 9},
       {"type": "text", "text": "Action under the SEBI Act."}, "sebi",
       "Promoters (with persons acting in concert) declare to each stock exchange and the audit committee, within 7 working days of year end, that no encumbrance was made beyond those already disclosed. (The Reg 30 annual shareholding disclosure was omitted from 1 Apr 2022.)"),
    _r("IN-SEBI-PIT-TW", "SEBI", "Close trading window", "Trading window notice",
       "SEBI PIT Schedule B", ["listed"], {"kind": "quarterly_after", "days": 0},
       {"type": "text", "text": "Insider trading exposure for designated persons."}, "sebi",
       "Close the trading window from quarter end until 48 hours after results."),
    _r("IN-SEBI-REG30", "SEBI", "Material event disclosure", "Reg 30",
       "SEBI LODR Reg 30", ["listed"],
       {"kind": "event", "triggers": ["director_appointed", "director_resigned", "kmp_appointed",
                                      "kmp_resigned", "md_appointed", "auditor_appointed",
                                      "auditor_resigned", "shares_allotted", "general_meeting_held"], "days": 1},
       {"type": "text", "text": "Events arising within the company: 12 hours; from outside: 24 hours; Schedule III timelines where given (e.g. director or KMP resignation letter with reasons within 7 days). A late disclosure must explain the delay."},
       "sebi", "Disclose the event to the stock exchanges."),
    _r("IN-SEBI-REG30-BM", "SEBI", "Board meeting outcome", "Reg 30(6)",
       "SEBI LODR Reg 30(6)", ["listed"],
       {"kind": "event", "triggers": ["board_meeting_held"], "days": 0},
       {"type": "text", "text": "Within 30 minutes of the meeting closing; within 3 hours if it closes after market hours and more than 3 hours before the next session."},
       "sebi", "Disclose the board's decisions to the stock exchanges the same day."),
    _r("IN-SEBI-REG29", "SEBI", "Prior intimation of board meeting", "Reg 29",
       "SEBI LODR Reg 29", ["listed"],
       {"kind": "event", "triggers": ["board_meeting_scheduled"], "days": -3, "working_days": True},
       {"type": "flat", "amount": 10000, "note": "per instance per item, per exchange (SEBI SOP)"}, "sebi",
       "Intimate the exchanges at least two working days ahead (excluding the intimation and meeting dates) of a board meeting that will consider results, a buyback, voluntary delisting, fund raising, a dividend, a bonus issue, or changes to listed securities or their payment dates. Mark N/A for other meetings."),
    _r("IN-SEBI-REG44", "SEBI", "Voting results", "Reg 44(3)",
       "SEBI LODR Reg 44(3)", ["listed"],
       {"kind": "event", "triggers": ["general_meeting_held"], "days": 2, "working_days": True},
       {"type": "flat", "amount": 10000, "note": "per instance, per exchange (SEBI SOP)"}, "sebi",
       "Submit voting results within two working days of the general meeting."),
    _r("IN-SEBI-REG44-5", "SEBI", "AGM within five months (top 100)", "AGM (Reg 44(5))",
       "SEBI LODR Reg 44(5)", ["listed"], {"kind": "annual", "month": 8, "day": 31},
       {"type": "flat", "amount": 25000, "note": "per instance, per exchange (SEBI SOP)"},
       "sebi", "Top 100 listed companies hold the AGM within five months of year end (by 31 August), with a live webcast.",
       flags=["top100"]),
    _r("IN-SEBI-PIT-SDD", "SEBI", "SDD compliance certificate", "SDD certificate (PCS)",
       "SEBI PIT Reg 3(5)-(6); BSE 20241018-44 / NSE/CML/2024/31", ["listed"],
       {"kind": "annual", "month": 5, "day": 30},
       {"type": "text", "text": "No SOP fine; the exchange flags the company as SDD non-compliant and withholds most listing approvals until it files."},
       "sdd_cert", "Listed companies outside Reg 24A (SME-listed or below the Reg 15(2) size limits) file a PCS-certified SDD compliance certificate within 60 days of year end.",
       any_flags=["lodr_small", "sme_listed"]),
]

RULES_BY_ID = {r["id"]: r for r in RULES}

# DIR-3 KYC is per director, not per entity: every DIN holder files once every
# three financial years (from 31 March 2026), due 30 June after the third FY.
DIR3KYC_RULE = {
    "id": "IN-MCA-DIR3KYC", "jurisdiction": "IN", "regime": "Companies Act",
    "name": "Director KYC (3-year cycle)", "form": "DIR-3 KYC Web",
    "law": "Appointment and Qualification of Directors Rules r.12A",
    "types": ALL_TYPES, "schedule": {"kind": "per_din"},
    "penalty": {"type": "flat", "amount": 5000, "note": "DIN is deactivated until filed"},
    "source": _SRC["dir3"], "flags": [], "any_flags": [], "not_flags": [], "states": [],
    "category": "Filing",
    "description": "Each DIN holder files once every three financial years, by 30 June after the third year.",
}
RULES_BY_ID[DIR3KYC_RULE["id"]] = DIR3KYC_RULE


# ── Applicability ───────────────────────────────────────────────────────────

def applies(rule, entity):
    """True when `rule` applies to an entity profile dict
    ({entity_type, flags: {..}, state})."""
    if rule["jurisdiction"] != entity.get("jurisdiction", "IN"):
        return False
    if entity.get("entity_type") not in rule["types"]:
        return False
    flags = entity.get("flags") or {}
    if any(not flags.get(f) for f in rule["flags"]):
        return False
    if rule["any_flags"] and not any(flags.get(f) for f in rule["any_flags"]):
        return False
    if any(flags.get(f) for f in rule["not_flags"]):
        return False
    if rule["states"] and (entity.get("state") or "") not in rule["states"]:
        return False
    return True


def applicable_rules(entity, include_events=True):
    out = [r for r in RULES if applies(r, entity)]
    if not include_events:
        out = [r for r in out if r["schedule"]["kind"] != "event"]
    return out


# ── Dates ───────────────────────────────────────────────────────────────────

def fy_start(d):
    return d.year if d.month >= 4 else d.year - 1


def agm_fy_label(held_on):
    """Financial year an AGM held on `held_on` belongs to: the one that ended
    on the 31 March before it (an AGM in Aug 2026 is for FY2025-26)."""
    return fy_label(fy_start(held_on) - 1)


def fy_label(start_year):
    return f"FY{start_year}-{str(start_year + 1)[-2:]}"


def _last_day(y, m):
    return calendar.monthrange(y, m)[1]


def _add_months(y, m, n):
    idx = y * 12 + (m - 1) + n
    return idx // 12, idx % 12 + 1


def _fy_quarter_ends(start):
    return [(1, date(start, 6, 30)), (2, date(start, 9, 30)),
            (3, date(start, 12, 31)), (4, date(start + 1, 3, 31))]


def _next_occurrence(after, month, day):
    """First date (month, day) strictly after `after`."""
    for y in (after.year, after.year + 1):
        cand = date(y, month, min(day, _last_day(y, month)))
        if cand > after:
            return cand
    return date(after.year + 1, month, day)


def occurrences(rule, window_start, window_end):
    """Dated occurrences of a periodic rule falling inside the window.
    Returns [{period, due_date}]. Event and per-DIN rules return []."""
    s = rule["schedule"]
    kind = s["kind"]
    out = []
    first_fy = fy_start(window_start) - 2
    last_fy = fy_start(window_end) + 1

    if kind == "monthly":
        y, m = window_start.year, window_start.month
        y, m = _add_months(y, m, -2)
        while date(y, m, 1) <= window_end:
            if m not in s.get("skip_months", []):
                exc = s.get("exceptions", {}).get(m)
                if exc:
                    dy, dm = _add_months(y, m, 1)
                    due = date(dy, exc[0], exc[1])
                else:
                    dy, dm = _add_months(y, m, s.get("offset", 1))
                    day = _last_day(dy, dm) if s["day"] == "last" else min(s["day"], _last_day(dy, dm))
                    due = date(dy, dm, day)
                out.append({"period": f"{calendar.month_abbr[m]} {y}", "due_date": due})
            y, m = _add_months(y, m, 1)

    elif kind in ("quarterly_after", "quarterly_fixed", "quarterly_nextmonth"):
        for fy in range(first_fy, last_fy + 1):
            for q, qe in _fy_quarter_ends(fy):
                if kind == "quarterly_after":
                    days = s.get("q4_days", s["days"]) if q == 4 else s["days"]
                    due = qe + timedelta(days=days)
                elif kind == "quarterly_fixed":
                    mm, dd = s["dates"][q]
                    due = _next_occurrence(qe, mm, dd)
                else:
                    ny, nm = _add_months(qe.year, qe.month, 1)
                    due = date(ny, nm, s["day"])
                out.append({"period": f"Q{q} {fy_label(fy)}", "due_date": due})

    elif kind == "half_fixed":
        for y in range(window_start.year - 1, window_end.year + 2):
            for mm, dd, label in s["dates"]:
                due = date(y, mm, min(dd, _last_day(y, mm)))
                out.append({"period": f"{label} {due.year}", "due_date": due})

    elif kind == "annual":
        for fy in range(first_fy, last_fy + 1):
            y = fy + 1 if s["month"] >= 4 else fy + 2
            due = date(y, s["month"], min(s["day"], _last_day(y, s["month"])))
            out.append({"period": fy_label(fy), "due_date": due})

    elif kind == "annual_calendar":
        for y in range(window_start.year - 2, window_end.year + 1):
            out.append({"period": f"CY{y}", "due_date": date(y + 1, s["month"], s["day"])})

    elif kind == "fy_dates":
        for fy in range(first_fy, last_fy + 1):
            for mm, dd, label in s["dates"]:
                y = fy if mm >= 4 else fy + 1
                out.append({"period": f"{label} {fy_label(fy)}", "due_date": date(y, mm, dd)})

    # One-off extensions (e.g. a CBDT press release) replace a single period's date.
    for o in out:
        ext = (s.get("overrides") or {}).get(o["period"])
        if ext:
            o["due_date"] = date.fromisoformat(ext)
            o["extended"] = True
    return [o for o in out if window_start <= o["due_date"] <= window_end]


def add_working_days(d, n):
    """Move n weekdays forward (or back when n < 0). Exchange holidays are not
    modelled, so a deadline can be a day earlier than the exchange's own."""
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left:
        d += timedelta(days=step)
        if d.weekday() < 5:
            left -= 1
    return d


def event_obligations(event_type, event_date, entity):
    """Follow-up filings created by logging an event on an entity."""
    out = []
    for r in RULES:
        s = r["schedule"]
        if s["kind"] != "event" or event_type not in s["triggers"]:
            continue
        if not applies(r, entity):
            continue
        due = (add_working_days(event_date, s["days"]) if s.get("working_days")
               else event_date + timedelta(days=s["days"]))
        out.append({"rule_id": r["id"], "due_date": due,
                    "period": f"{EVENT_TYPES.get(event_type, event_type)} · {event_date.isoformat()}"})
    return out


def dir3kyc_due(din_allotted_on, as_of):
    """Next DIR-3 KYC Web due date for a DIN under the 3-year cycle.

    DINs allotted up to 31 March 2025 anchor to 30 June 2028; a DIN allotted
    in FY y-(y+1) anchors to 30 June of y+4. Later cycles repeat every three
    years. Returns the first due date on or after as_of minus one year, so a
    missed deadline still shows up as overdue."""
    if din_allotted_on and din_allotted_on >= date(2025, 4, 1):
        anchor = fy_start(din_allotted_on) + 4
    else:
        anchor = 2028
    year = anchor
    floor = as_of - timedelta(days=365)
    while date(year, 6, 30) < floor:
        year += 3
    return date(year, 6, 30)


def dir3kyc_cycles(din_allotted_on, window_start, window_end):
    """Every DIR-3 KYC Web due date for a DIN inside the window, oldest first.
    Each cycle is its own obligation, so filing one never hides the next."""
    due = dir3kyc_due(din_allotted_on, date(2000, 1, 1))  # the first cycle
    out = []
    while due <= window_end:
        if due >= window_start:
            out.append(due)
        due = date(due.year + 3, 6, 30)
    return out


# ── Penalties ───────────────────────────────────────────────────────────────

def normal_fee(share_capital):
    """MCA normal filing fee by nominal share capital
    (Companies (Registration Offices and Fees) Rules, Annexure)."""
    if not share_capital:
        return 200
    if share_capital < 100000:
        return 200
    if share_capital < 500000:
        return 300
    if share_capital < 2500000:
        return 400
    if share_capital < 10000000:
        return 500
    return 600


def slab_multiple(days_late, first_band_1x=False):
    """Additional-fee multiple of the normal fee (Rule 12 as amended by
    G.S.R. 12(E), from 1 July 2022). Only forms under s.139 and s.157 get
    the 1x band for the first 15 days; every other form starts at 2x."""
    if days_late <= 0:
        return 0
    if days_late <= 15 and first_band_1x:
        return 1
    if days_late <= 30:
        return 2
    if days_late <= 60:
        return 4
    if days_late <= 90:
        return 6
    if days_late <= 180:
        return 10
    return 12


def llp_normal_fee(contribution):
    """LLP normal filing fee by contribution (LLP Rules, Annexure A)."""
    c = contribution or 0
    for limit, fee in ((100000, 50), (500000, 100), (1000000, 150), (2500000, 200), (10000000, 400)):
        if c <= limit:
            return fee
    return 600


def llp_multiple(days_late, small):
    """Additional-fee multiple for LLP forms (LLP (Amendment) Rules 2022,
    from 1 April 2022), for delays up to 15/30/60/90/180/360 days."""
    bands = (15, 30, 60, 90, 180, 360)
    mult = (1, 2, 4, 6, 10, 15) if small else (1, 4, 8, 12, 20, 30)
    for limit, m in zip(bands, mult):
        if days_late <= limit:
            return m
    return mult[-1]


def estimate_penalty(rule, due_date, as_of, entity=None, filed_on=None):
    """Indicative cost of lateness. Returns None when not late, else
    {days_late, amount (int or None), basis}."""
    end = filed_on or as_of
    days = (end - due_date).days
    if days <= 0:
        return None
    p = rule["penalty"]
    t = p["type"]
    note = p.get("note")
    if t == "per_day":
        amt = p["amount"] * days
        basis = f"₹{p['amount']:,}/day × {days} days"
        if p.get("cap") and amt > p["cap"]:
            amt = p["cap"]
            basis += f", capped at ₹{p['cap']:,}"
        if note:
            basis += f" ({note})"
        return {"days_late": days, "amount": amt, "basis": basis}
    if t == "flat":
        basis = f"₹{p['amount']:,} flat" + (f" ({note})" if note else "")
        return {"days_late": days, "amount": p["amount"], "basis": basis}
    if t == "slab_fee":
        fee = normal_fee((entity or {}).get("share_capital"))
        mult = slab_multiple(days, p.get("first_band_1x", False))
        basis = f"Additional fee {mult}× normal fee of ₹{fee} ({days} days late)"
        if note:
            basis += f"; {note}"
        return {"days_late": days, "amount": fee * mult, "basis": basis}
    if t == "llp_slab":
        ent = entity or {}
        small = bool((ent.get("flags") or {}).get("small_llp"))
        fee = llp_normal_fee(ent.get("share_capital"))
        mult = llp_multiple(days, small)
        amt = fee * mult
        basis = f"Additional fee {mult}× normal fee of ₹{fee} ({'small LLP, ' if small else ''}{days} days late)"
        if p.get("beyond_360_per_day") and days > 360:
            per = 10 if small else 20
            amt += per * (days - 360)
            basis += f" plus ₹{per}/day beyond 360 days"
        return {"days_late": days, "amount": amt, "basis": basis}
    return {"days_late": days, "amount": None, "basis": p.get("text", "")}


def rule_summary(rule):
    """Flat dict for JSON/templates."""
    s = rule["schedule"]
    return {
        "id": rule["id"], "jurisdiction": rule["jurisdiction"], "regime": rule["regime"],
        "name": rule["name"], "form": rule["form"], "law": rule["law"],
        "description": rule["description"], "source": rule["source"],
        "category": rule["category"], "kind": s["kind"],
        "penalty_text": _penalty_text(rule["penalty"]),
        "schedule": schedule_text(rule), "frequency": frequency(rule),
    }


# ── Plain-words schedules ───────────────────────────────────────────────────

def _ordinal(n):
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _day_mon(month, day):
    return f"{day} {calendar.month_abbr[month]}"


def frequency(rule):
    """Coarse bucket for filtering: monthly, quarterly, half-yearly, annual,
    event or per-director."""
    kind = rule["schedule"]["kind"]
    if kind == "monthly":
        return "monthly"
    if kind in ("quarterly_after", "quarterly_fixed", "quarterly_nextmonth", "fy_dates"):
        return "quarterly"
    if kind == "half_fixed":
        return "half-yearly"
    if kind in ("annual", "annual_calendar"):
        return "annual"
    if kind == "event":
        return "event"
    if kind in ("per_din", "per_person"):
        return "per-director"
    return "other"


def schedule_text(rule):
    """The rule's due-date pattern in plain words, read straight off its
    schedule so the text can never drift from the dates we compute."""
    s = rule["schedule"]
    kind = s["kind"]
    if kind == "monthly":
        if s["day"] == "last":
            base = "Monthly, by the last day of the " + ("month" if s.get("offset", 1) == 0 else "next month")
        else:
            base = f"Monthly, by the {_ordinal(s['day'])} of the " + ("same month" if s.get("offset", 1) == 0 else "next month")
        extras = [f"{d} {calendar.month_name[mm]} for {calendar.month_name[m]}"
                  for m, (mm, d) in sorted(s.get("exceptions", {}).items())]
        skip = s.get("skip_months") or []
        if sorted(skip) == [3, 6, 9, 12]:
            extras.append("first two months of each quarter")
        elif skip:
            extras.append("not for " + ", ".join(calendar.month_abbr[m] for m in skip))
        return base + (f" ({'; '.join(extras)})" if extras else "")
    if kind == "quarterly_after":
        if not s["days"]:
            return "Quarterly, by quarter end"
        txt = f"Quarterly, {s['days']} days after quarter end"
        q4 = s.get("q4_days")
        return txt + (f" ({q4} for Q4)" if q4 and q4 != s["days"] else "")
    if kind == "quarterly_fixed":
        return "Quarterly: " + ", ".join(_day_mon(*s["dates"][q]) for q in sorted(s["dates"]))
    if kind == "quarterly_nextmonth":
        return f"Quarterly, by the {_ordinal(s['day'])} of the month after quarter end"
    if kind == "half_fixed":
        return "Half-yearly: " + " and ".join(_day_mon(m, d) for m, d, _ in s["dates"])
    if kind == "annual":
        txt = f"Every year by {s['day']} {calendar.month_name[s['month']]}"
        if s.get("after_agm"):
            txt = f"{s['after_agm']} days after the AGM (by {s['day']} {calendar.month_name[s['month']]} for a 30 September AGM)"
        ov = s.get("overrides") or {}
        if ov:
            txt += " (" + "; ".join(f"{p}: extended to {date.fromisoformat(d).day} {calendar.month_name[date.fromisoformat(d).month]} {date.fromisoformat(d).year}" for p, d in ov.items()) + ")"
        return txt
    if kind == "annual_calendar":
        return f"Every year by {s['day']} {calendar.month_name[s['month']]}, for the previous calendar year"
    if kind == "fy_dates":
        return ", ".join(_day_mon(m, d) for m, d, _ in s["dates"])
    if kind == "event":
        labels = ", ".join(EVENT_TYPES.get(t, t) for t in s["triggers"])
        days = s["days"]
        if s.get("working_days"):
            if days < 0:
                return f"At least {-days - 1} clear working days before: {labels}"
            return f"Within {days} working days of: {labels}"
        if days == 0:
            return f"Same day as: {labels}"
        if days < 0:
            return f"At least {-days} days before: {labels}"
        if days == 1:
            return f"Within 24 hours of: {labels}"
        return f"Within {days} days of: {labels}"
    if kind == "per_din":
        return "Every 3 years per DIN, by 30 June"
    if kind == "per_person":
        return "Before each director's DSC expires"
    return "One-off task"


def _penalty_text(p):
    if p["type"] == "per_day":
        txt = f"₹{p['amount']:,} per day"
        if p.get("cap"):
            txt += f", up to ₹{p['cap']:,}"
        return txt + (f" ({p['note']})" if p.get("note") else "")
    if p["type"] == "flat":
        return f"₹{p['amount']:,}" + (f" ({p['note']})" if p.get("note") else "")
    if p["type"] == "slab_fee":
        lo = "1×" if p.get("first_band_1x") else "2×"
        txt = f"Additional fee of {lo} to 12× the normal filing fee, by length of delay"
        return txt + (f" ({p['note']})" if p.get("note") else "")
    if p["type"] == "llp_slab":
        return ("Additional fee of 1× to 30× the normal filing fee by length of delay (1× to 15× for small LLPs), "
                "plus ₹20/day beyond 360 days (₹10 for small LLPs)")
    return p.get("text", "")
