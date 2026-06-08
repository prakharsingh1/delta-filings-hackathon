"""
Delta Filings — Calculator Tools data module.

Holds the static catalogue plus the regulatory rate tables that the
client-side calculators consume. Values are accurate as of May 2026 to the
best of our reference. Each calculator template renders a visible
"verify with your CS" disclaimer so a stale rate never becomes a liability.

Conventions:
- All rupee amounts in plain integers (not paise).
- All percentages as decimals (e.g. 0.18 for 18%).
- State-specific tables cover the top eight states by GDP and a default fallback.
"""

# -------------------------------------------------------------------
# 1. Tool catalogue — what shows on /tools index
# -------------------------------------------------------------------
TOOLS = [
    {
        "slug": "roc-late-fee",
        "title": "ROC Late Filing Fee Calculator",
        "tagline": "Compute MCA additional fees on AOC-4, MGT-7, DIR-3, INC-22 and other forms when filed past the due date.",
        "icon": "doc",
        "color": "indigo",
        "tags": ["MCA", "ROC", "Penalty"],
    },
    {
        "slug": "gst-late-fee",
        "title": "GST Late Fee & Interest Calculator",
        "tagline": "Late fee and interest on GSTR-3B, GSTR-1, GSTR-9 and GSTR-4 — broken down into CGST and SGST.",
        "icon": "rupee",
        "color": "emerald",
        "tags": ["GST", "Compliance"],
    },
    {
        "slug": "din-kyc-late-fee",
        "title": "DIN KYC Late Fee Calculator",
        "tagline": "Check the penalty for late DIR-3 KYC filing and whether your DIN will be deactivated.",
        "icon": "id",
        "color": "amber",
        "tags": ["MCA", "Director"],
    },
    {
        "slug": "msme-classification",
        "title": "MSME Classification Tool",
        "tagline": "Classify an enterprise as Micro, Small or Medium under the 2020 composite criteria of investment AND turnover.",
        "icon": "tag",
        "color": "purple",
        "tags": ["MSME", "Udyam"],
    },
    {
        "slug": "company-registration-cost",
        "title": "Company Registration Cost Estimator",
        "tagline": "Estimate the all-in cost of registering a Private Limited Company, OPC, or LLP — including MCA fees, stamp duty, DSC and DIN.",
        "icon": "cart",
        "color": "rose",
        "tags": ["Incorporation"],
    },
    {
        "slug": "tds-rate-finder",
        "title": "TDS Rate Finder & Calculator",
        "tagline": "Look up the applicable TDS rate by section (194A through 194Q) and compute the deduction with threshold validation.",
        "icon": "calc",
        "color": "blue",
        "tags": ["TDS", "Income Tax"],
    },
    {
        "slug": "stamp-duty",
        "title": "Stamp Duty Calculator",
        "tagline": "State-wise stamp duty on share transfers, MOA & AOA, loan agreements and lease deeds.",
        "icon": "stamp",
        "color": "indigo",
        "tags": ["Stamp Act", "State-specific"],
    },
    {
        "slug": "gst-calculator",
        "title": "GST Calculator",
        "tagline": "Add or remove GST at any slab and see the CGST / SGST (intra-state) or IGST (inter-state) split instantly.",
        "icon": "percent",
        "color": "emerald",
        "tags": ["GST", "Tax"],
    },
    {
        "slug": "gratuity",
        "title": "Gratuity Calculator",
        "tagline": "Compute gratuity payable under the Payment of Gratuity Act, 1972 — with the ₹20 lakh exemption cap.",
        "icon": "gift",
        "color": "rose",
        "tags": ["Gratuity", "Payroll"],
    },
    {
        "slug": "hra-exemption",
        "title": "HRA Exemption Calculator",
        "tagline": "Find your tax-exempt House Rent Allowance under Section 10(13A) — the least of the three statutory limits.",
        "icon": "home",
        "color": "blue",
        "tags": ["Income Tax", "Salary"],
    },
    {
        "slug": "income-tax",
        "title": "Income Tax Calculator (FY 2026-27)",
        "tagline": "Compare your tax under the Old vs New regime for AY 2027-28, with standard deduction, 87A rebate and 4% cess.",
        "icon": "chart",
        "color": "purple",
        "tags": ["Income Tax", "Old vs New"],
    },
]


def tool_by_slug(slug):
    return next((t for t in TOOLS if t["slug"] == slug), None)


# -------------------------------------------------------------------
# 2. ROC Late Fee — MCA Additional Fee schedule
# -------------------------------------------------------------------
# As per Companies (Registration Offices and Fees) Rules, 2014 and
# subsequent MCA notifications. Most forms attract a flat ₹100/day of delay.
# Annual filings (AOC-4 + MGT-7) carry the same after 30-Jun-2018 amendment.
ROC_FORMS = [
    {"code": "AOC-4", "name": "AOC-4 — Annual Financial Statements", "due_rule": "Within 30 days of AGM",          "per_day_fee": 100},
    {"code": "MGT-7", "name": "MGT-7 — Annual Return",                "due_rule": "Within 60 days of AGM",          "per_day_fee": 100},
    {"code": "MGT-7A", "name": "MGT-7A — Annual Return (OPC & Small)", "due_rule": "Within 60 days of AGM",         "per_day_fee": 100},
    {"code": "DIR-3 KYC", "name": "DIR-3 KYC — Director KYC",          "due_rule": "By 30 September every year",     "per_day_fee": 0, "flat_fee": 5000, "note": "DIN gets deactivated and ₹5,000 flat penalty applies; per-day component is not used for DIR-3 KYC."},
    {"code": "INC-22", "name": "INC-22 — Notice of Change of Registered Office", "due_rule": "Within 30 days of change", "per_day_fee": 100},
    {"code": "INC-20A", "name": "INC-20A — Commencement of Business",  "due_rule": "Within 180 days of incorporation", "per_day_fee": 100},
    {"code": "DIR-12", "name": "DIR-12 — Director Appointment/Resignation", "due_rule": "Within 30 days of event", "per_day_fee": 100},
    {"code": "PAS-3", "name": "PAS-3 — Return of Allotment",           "due_rule": "Within 30 days of allotment",    "per_day_fee": 100},
    {"code": "ADT-1", "name": "ADT-1 — Auditor Appointment",            "due_rule": "Within 15 days of AGM",          "per_day_fee": 100},
    {"code": "MSME-1", "name": "MSME-1 — Outstanding Dues to MSME",    "due_rule": "Half-yearly (30-Apr / 31-Oct)",  "per_day_fee": 100},
]


# -------------------------------------------------------------------
# 3. GST Late Fee + Interest schedule
# -------------------------------------------------------------------
GST_RETURNS = [
    {
        "code": "GSTR-3B",
        "name": "GSTR-3B — Monthly summary return",
        "due_day": 20,            # 20th of following month for monthly filers
        "late_fee_normal": 50,    # ₹50/day (₹25 CGST + ₹25 SGST)
        "late_fee_nil": 20,       # ₹20/day for nil returns (₹10+₹10)
        "cap": 5000,
    },
    {
        "code": "GSTR-1",
        "name": "GSTR-1 — Monthly outward supplies",
        "due_day": 11,
        "late_fee_normal": 50,
        "late_fee_nil": 20,
        "cap": 5000,
    },
    {
        "code": "GSTR-9",
        "name": "GSTR-9 — Annual return",
        "due_day": None,
        "late_fee_normal": 200,   # ₹200/day (₹100+₹100)
        "late_fee_nil": 200,
        "cap_pct_turnover": 0.005, # 0.5% of turnover in state/UT
    },
    {
        "code": "GSTR-4",
        "name": "GSTR-4 — Composition annual return",
        "due_day": None,
        "late_fee_normal": 50,
        "late_fee_nil": 20,
        "cap_normal": 2000,
        "cap_nil": 500,
    },
]
GST_INTEREST_RATE = 0.18  # 18% per annum on tax liability under Section 50(1)


# -------------------------------------------------------------------
# 4. MSME Classification (2020 revised criteria — Notification S.O. 2119(E))
# -------------------------------------------------------------------
# Composite criteria: BOTH investment AND turnover must satisfy the cap.
# Amounts in ₹ crore.
MSME_LIMITS = [
    {"category": "Micro",   "investment_max_cr": 1,   "turnover_max_cr": 5},
    {"category": "Small",   "investment_max_cr": 10,  "turnover_max_cr": 50},
    {"category": "Medium",  "investment_max_cr": 50,  "turnover_max_cr": 250},
]


# -------------------------------------------------------------------
# 5. Company Registration cost estimate
# -------------------------------------------------------------------
# Govt-side fees are deterministic; professional fees are a typical range.
COMPANY_REG_COST = {
    "private-ltd": {
        "name": "Private Limited Company",
        "dsc_per_director": 1500,           # Class 3 DSC valid 2 yrs
        "dsc_default_directors": 2,
        "din_per_director": 500,            # included in SPICe+ for first 3 directors
        "din_max_in_spice": 3,
        "name_reservation_runner": 1000,    # RUN / SPICe+ part A
        "spice_filing_fee_zero_threshold_cr": 0.15,  # zero if authorised capital ≤ ₹15 lakh
        "spice_filing_fee_above": 200,      # nominal slab above ₹15 lakh
        "moa_stamp_duty_default": 2000,     # state-specific, default
        "professional_fee_range": [5000, 15000],
    },
    "opc": {
        "name": "One Person Company",
        "dsc_per_director": 1500,
        "dsc_default_directors": 1,
        "din_per_director": 500,
        "din_max_in_spice": 3,
        "name_reservation_runner": 1000,
        "spice_filing_fee_zero_threshold_cr": 0.15,
        "spice_filing_fee_above": 200,
        "moa_stamp_duty_default": 2000,
        "professional_fee_range": [4000, 10000],
    },
    "llp": {
        "name": "Limited Liability Partnership",
        "dsc_per_director": 1500,
        "dsc_default_directors": 2,
        "dpin_per_partner": 500,
        "name_reservation_run_llp": 200,
        "fillip_filing_fee_under_1l": 500,
        "fillip_filing_fee_1l_to_5l": 2000,
        "fillip_filing_fee_5l_to_10l": 4000,
        "llp_agreement_stamp_default": 1000,
        "professional_fee_range": [4000, 12000],
    },
}


# -------------------------------------------------------------------
# 6. TDS Rate Finder — Sections 192 through 194Q (FY 2026-27 / AY 2027-28)
# -------------------------------------------------------------------
# 'threshold' is the annual amount above which TDS applies. None => from rupee 1.
TDS_SECTIONS = [
    {"section": "192",  "label": "Salary",                                   "rate_ind": "Slab",  "rate_other": "Slab", "threshold": "Basic exemption"},
    {"section": "192A", "label": "Premature EPF withdrawal",                 "rate_ind": 0.10,    "rate_other": 0.10,   "threshold": 50000},
    {"section": "193",  "label": "Interest on securities",                   "rate_ind": 0.10,    "rate_other": 0.10,   "threshold": 10000},
    {"section": "194",  "label": "Dividend",                                 "rate_ind": 0.10,    "rate_other": 0.10,   "threshold": 5000},
    {"section": "194A", "label": "Interest other than securities (bank)",    "rate_ind": 0.10,    "rate_other": 0.10,   "threshold": 50000},
    {"section": "194A", "label": "Interest other than securities (others)",  "rate_ind": 0.10,    "rate_other": 0.10,   "threshold": 5000},
    {"section": "194B", "label": "Winnings from lottery / crossword",        "rate_ind": 0.30,    "rate_other": 0.30,   "threshold": 10000},
    {"section": "194C", "label": "Payment to contractor — Individual / HUF", "rate_ind": 0.01,    "rate_other": 0.01,   "threshold": "30000 single / 100000 aggregate"},
    {"section": "194C", "label": "Payment to contractor — Other",            "rate_ind": 0.02,    "rate_other": 0.02,   "threshold": "30000 single / 100000 aggregate"},
    {"section": "194D", "label": "Insurance commission",                     "rate_ind": 0.05,    "rate_other": 0.10,   "threshold": 15000},
    {"section": "194DA","label": "Life insurance maturity (taxable)",        "rate_ind": 0.02,    "rate_other": 0.02,   "threshold": 100000},
    {"section": "194H", "label": "Commission or brokerage",                  "rate_ind": 0.02,    "rate_other": 0.02,   "threshold": 15000},
    {"section": "194I", "label": "Rent — Plant & Machinery",                 "rate_ind": 0.02,    "rate_other": 0.02,   "threshold": 240000},
    {"section": "194I", "label": "Rent — Land / Building / Furniture",       "rate_ind": 0.10,    "rate_other": 0.10,   "threshold": 240000},
    {"section": "194IA","label": "Sale of immovable property",               "rate_ind": 0.01,    "rate_other": 0.01,   "threshold": 5000000},
    {"section": "194IB","label": "Rent by Individual/HUF (no audit)",        "rate_ind": 0.02,    "rate_other": 0.02,   "threshold": "50000/month"},
    {"section": "194J", "label": "Professional fees",                        "rate_ind": 0.10,    "rate_other": 0.10,   "threshold": 30000},
    {"section": "194J", "label": "Technical fees / royalty for cinema",      "rate_ind": 0.02,    "rate_other": 0.02,   "threshold": 30000},
    {"section": "194K", "label": "Income on mutual fund units",              "rate_ind": 0.10,    "rate_other": 0.10,   "threshold": 5000},
    {"section": "194N", "label": "Cash withdrawal over ₹1 crore",            "rate_ind": 0.02,    "rate_other": 0.02,   "threshold": 10000000},
    {"section": "194O", "label": "E-commerce participant payment",           "rate_ind": 0.001,   "rate_other": 0.001,  "threshold": 500000},
    {"section": "194Q", "label": "Purchase of goods (buyer with turnover > ₹10 cr)", "rate_ind": 0.001, "rate_other": 0.001, "threshold": 5000000},
    {"section": "194R", "label": "Benefit or perquisite to business",        "rate_ind": 0.10,    "rate_other": 0.10,   "threshold": 20000},
    {"section": "194S", "label": "Virtual digital asset transfer",           "rate_ind": 0.01,    "rate_other": 0.01,   "threshold": "10000 / 50000"},
    {"section": "194T", "label": "Partnership remuneration / interest",      "rate_ind": 0.10,    "rate_other": 0.10,   "threshold": 20000},
]


# -------------------------------------------------------------------
# 7. Stamp Duty — top 8 states + default
# -------------------------------------------------------------------
# Share transfer is uniform under Indian Stamp Act amendment (1-Jul-2020):
SHARE_TRANSFER_RATE = 0.00015  # 0.015% of consideration

# State-specific stamp duty on MOA/AOA, loan agreements, lease deeds.
# Values are typical/headline rates; exact computation may include slabs
# based on authorised share capital / loan amount / annual rent.
STAMP_DUTY_STATES = {
    "Maharashtra":     {"moa": 1000, "aoa_pct_of_capital": 0.002,  "loan_pct": 0.002, "lease_pct_annual_rent_yr": 0.0025},
    "Delhi":           {"moa": 200,  "aoa_pct_of_capital": 0.0015, "loan_pct": 0.001, "lease_pct_annual_rent_yr": 0.002},
    "Karnataka":       {"moa": 1000, "aoa_pct_of_capital": 0.001,  "loan_pct": 0.001, "lease_pct_annual_rent_yr": 0.002},
    "Tamil Nadu":      {"moa": 200,  "aoa_pct_of_capital": 0.0007, "loan_pct": 0.0005,"lease_pct_annual_rent_yr": 0.001},
    "Gujarat":         {"moa": 200,  "aoa_pct_of_capital": 0.005,  "loan_pct": 0.003, "lease_pct_annual_rent_yr": 0.003},
    "West Bengal":     {"moa": 60,   "aoa_pct_of_capital": 0.0004, "loan_pct": 0.001, "lease_pct_annual_rent_yr": 0.002},
    "Telangana":       {"moa": 500,  "aoa_pct_of_capital": 0.0015, "loan_pct": 0.005, "lease_pct_annual_rent_yr": 0.003},
    "Uttar Pradesh":   {"moa": 500,  "aoa_pct_of_capital": 0.005,  "loan_pct": 0.005, "lease_pct_annual_rent_yr": 0.004},
    "Haryana":         {"moa": 60,   "aoa_pct_of_capital": 0.0015, "loan_pct": 0.0015,"lease_pct_annual_rent_yr": 0.0015},
    "Default":         {"moa": 500,  "aoa_pct_of_capital": 0.002,  "loan_pct": 0.002, "lease_pct_annual_rent_yr": 0.002},
}


# -------------------------------------------------------------------
# 8. GST rate slabs (decimals)
# -------------------------------------------------------------------
GST_RATES = [0.0, 0.05, 0.12, 0.18, 0.28]


# -------------------------------------------------------------------
# 9. Gratuity — Payment of Gratuity Act, 1972
# -------------------------------------------------------------------
# Gratuity = (15/26) × last drawn monthly salary (basic + DA) × completed years.
# A year of service > 6 months counts as a full year. Exempt up to ₹20 lakh.
GRATUITY = {
    "numerator": 15,
    "denominator": 26,
    "max_exempt": 2000000,
    "min_years_eligible": 5,
}


# -------------------------------------------------------------------
# 10. HRA exemption — Section 10(13A) / Rule 2A
# -------------------------------------------------------------------
# Exempt HRA = least of: (a) actual HRA received, (b) rent paid − 10% of salary,
# (c) 50% of salary (metro) or 40% (non-metro). Salary = basic + DA.
HRA = {
    "metro_pct": 0.50,
    "non_metro_pct": 0.40,
    "rent_excess_pct_of_salary": 0.10,
    "metros": ["Delhi", "Mumbai", "Kolkata", "Chennai"],
}


# -------------------------------------------------------------------
# 11. Income Tax slabs — FY 2026-27 / AY 2027-28
# -------------------------------------------------------------------
# Slabs are [upper_limit_or_None, marginal_rate]. Cess 4% on tax. 87A rebate
# makes tax nil up to the listed taxable-income limit. Surcharge on very high
# incomes is NOT modelled (flagged in the on-page disclaimer).
INCOME_TAX = {
    "cess": 0.04,
    "new_regime": {
        "std_deduction": 75000,
        "rebate_income_limit": 1200000,
        "slabs": [
            [400000, 0.0], [800000, 0.05], [1200000, 0.10],
            [1600000, 0.15], [2000000, 0.20], [2400000, 0.25],
            [None, 0.30],
        ],
    },
    "old_regime": {
        "std_deduction": 50000,
        "rebate_income_limit": 500000,
        "slabs": [
            [250000, 0.0], [500000, 0.05], [1000000, 0.20], [None, 0.30],
        ],
    },
}
