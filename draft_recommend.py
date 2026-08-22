"""Smart 'what should this CS draft / file next?' engine for the Draft page.

Four layers of signal, grounded in data we already hold:
  1. DEADLINE — compliance obligations (SEBI + MCA + FEMA + Tax) coming due or
     overdue, mapped to a draftable document.
  2. EVENT    — a recent filing implies a follow-on (board-meeting intimation →
     outcome; allotment → PAS-3; director change → DIR-12; results → Reg 47…).
  3. SEASONAL — calendar-driven governance docs that aren't dated obligations
     (AGM season → AGM notice, Board's Report, Secretarial Audit; ROC season →
     AOC-4/MGT-7 coverings; Sep → DIR-3 KYC…).
  4. SECTOR   — industry-specific filings (Chemicals → hazardous-waste return,
     EPR; Energy → carbon credits / RPO; Banking → RBI returns…).

Each recommendation says WHAT, WHY, and either drafts it or opens Compliance.
"""

from datetime import datetime, date
from typing import Dict, List

# Compliance mandate id → draftable Draft-page filing-type id.
# Now spans SEBI **and** MCA/FEMA so deadlines across every regime surface.
_MANDATE_TO_TYPE = {
    # SEBI
    "LODR-REG27": "integrated_governance",
    "LODR-REG31": "shareholding_pattern",
    "LODR-REG13": "reg13_grievance",
    "LODR-REG33": "reg33_results",
    "LODR-REG74": "depositories_certificate",
    "LODR-REG76-RSCA": "reg74_recon",
    "LODR-REG23-RPT": "rpt_disclosure",
    "LODR-REG24A": "reg24a_ascr",
    "LODR-REG34-AR": "reg34_ar",
    "LODR-BRSR": "brsr",
    "LODR-BRSR-CORE": "brsr",
    "LODR-AGM": "agm_notice",
    "PIT-TRADING-WINDOW": "trading_window_closure",
    "SAST-REG30": "sast_30",
    # MCA / Companies Act
    "MCA-AOC4": "directors_report",
    "MCA-ADT1": "adt1_covering",
    "MCA-DPT3": "dpt3_covering",
    "MCA-MSME1": "msme1_covering",
    "MCA-BEN2": "ben2_covering",
    "MCA-DIR12": "dir12_covering",
    "MCA-PAS3": "pas3_covering",
    "MCA-MGT14": "mgt14_resolution",
    "MCA-CHG1": "chg1_covering",
    "MCA-BOARD": "board_notice",
    "MCA-CSR2": "csr_resolution",
    # FEMA
    "FEMA-FCGPR": "fcgpr_covering",
    "FEMA-FLA": "fla_return",
}

_EVENT_TRIGGERS = [
    {"match": lambda t: ("board meeting" in t and ("intimation" in t or "prior" in t) and "outcome" not in t),
     "type": "outcome_board_meeting",
     "reason": "You filed a board-meeting intimation — the Outcome of Board Meeting (Reg 30) is due within 30 minutes of the meeting close."},
    {"match": lambda t: "outcome" in t and ("result" in t or "financial" in t),
     "type": "reg47_newspaper",
     "reason": "Financial results were approved — newspaper publication (Reg 47) is due within 48 hours."},
    {"match": lambda t: "outcome" in t and ("result" in t or "financial" in t),
     "type": "mgt14_resolution",
     "reason": "Accounts approved by the Board — the resolution under s.179(3) must be filed with the ROC in MGT-14 within 30 days."},
    {"match": lambda t: (("agm" in t or "annual general meeting" in t or "egm" in t or "postal ballot" in t) and ("notice" in t or "intimation" in t)),
     "type": "voting_results",
     "reason": "A general-meeting notice was issued — Voting Results (Reg 44) are due within 2 working days of the meeting."},
    {"match": lambda t: (("agm" in t or "annual general meeting" in t) and ("notice" in t or "intimation" in t)),
     "type": "agm_minutes",
     "reason": "AGM convened — Minutes (SS-2) must be entered within 30 days; proceedings filed under Reg 30(6)."},
    {"match": lambda t: any(k in t for k in ("allotment", "preferential", "bonus", "rights issue", "qip")),
     "type": "pas3_covering",
     "reason": "A capital action was disclosed — file the Return of Allotment (PAS-3) with the ROC within 30 days."},
    {"match": lambda t: any(k in t for k in ("allotment", "preferential", "bonus", "rights issue", "qip", "stock split")),
     "type": "shareholding_pattern",
     "reason": "Capital structure changed — file the revised Shareholding Pattern (Reg 31)."},
    {"match": lambda t: (("resignation" in t or "appointment" in t or "cessation" in t) and ("director" in t or "kmp" in t or "officer" in t or "secretary" in t)),
     "type": "dir12_covering",
     "reason": "A change in directors/KMP was reported — file DIR-12 with the ROC within 30 days, plus the Reg 30 disclosure."},
    {"match": lambda t: "auditor" in t and ("resignation" in t or "appointment" in t or "change" in t),
     "type": "adt1_covering",
     "reason": "An auditor change appears in recent filings — file ADT-1 (and the Reg 30 disclosure with reasons)."},
    {"match": lambda t: "credit rating" in t or " rating" in t,
     "type": "credit_rating",
     "reason": "A credit-rating action appears in recent filings — file the Reg 30 rating disclosure promptly."},
    {"match": lambda t: "dividend" in t,
     "type": "record_date",
     "reason": "A dividend was announced — intimate the Record Date (Reg 42) at least 7 working days in advance."},
    {"match": lambda t: any(k in t for k in ("acquisition", "merger", "amalgamation", "scheme of arrangement", "demerger", "stake")),
     "type": "acquisition_disclosure",
     "reason": "An M&A / scheme event was disclosed — ensure the full Reg 30 disclosure (with SEBI's mandated particulars) is filed."},
    {"match": lambda t: any(k in t for k in ("charge", "debenture", "ncd", "loan facility")),
     "type": "chg1_covering",
     "reason": "A charge / borrowing appears in recent filings — register the charge in CHG-1 with the ROC within 30 days."},
]


def _parse_d(s):
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _seasonal(today: date) -> List[Dict]:
    """Calendar-driven governance documents a CS prepares each season that
    aren't tracked as dated obligations (assumes a March financial year)."""
    m = today.month
    out = []
    def add(ftype, reason):
        out.append({"filing_type": ftype, "reason": reason, "urgency": "seasonal",
                    "bucket": "seasonal", "action": "draft", "due_date": None,
                    "days_remaining": None})
    if m in (6, 7, 8):           # AGM season build-up (AGM by 30 Sep)
        add("agm_notice", "AGM season — the AGM notice (with s.102 explanatory statement) and 21-day dispatch are due before the 30 September AGM deadline.")
        add("directors_report", "Annual cycle — the Board's Report (s.134) with all annexures (AOC-2, CSR, secretarial audit) must accompany the financial statements.")
        add("secretarial_audit_mr3", "Annual cycle — obtain the Secretarial Audit Report (MR-3) from the PCS for the board's report.")
        add("cg_report", "Annual cycle — prepare the Corporate Governance Report (Sch V) for the annual report.")
    if m in (9, 10, 11):         # post-AGM ROC season
        add("adt1_covering", "Post-AGM — file ADT-1 (auditor appointment) within 15 days, AOC-4 within 30 days, and MGT-7 within 60 days of the AGM.")
        add("agm_minutes", "Post-AGM — enter the General Meeting Minutes (SS-2) within 30 days and file proceedings under Reg 30(6).")
    if m == 9:
        add("dir12_covering", "DIR-3 KYC for every director is due by 30 September — confirm all KYCs are filed to avoid DIN deactivation.")
    if m in (3, 6, 9, 12):       # quarter-end
        add("trading_window_closure", "Quarter-end — close the trading window for designated persons until 48 hours after results are published (PIT Code).")
    return out


def recommend_filings(symbol: str, industry: str = "", max_items: int = 24) -> List[Dict]:
    """Return ranked, multi-layer draft recommendations for a company."""
    symbol = (symbol or "").upper()
    recs: Dict[str, Dict] = {}     # keyed by a unique key → best rec

    def _put(key, rec):
        cur = recs.get(key)
        if not cur:
            recs[key] = rec
        elif rec.get("days_remaining") is not None and (
                cur.get("days_remaining") is None or rec["days_remaining"] < cur["days_remaining"]):
            recs[key] = rec

    today = date.today()

    # ── 1) DEADLINE — across all regimes ──
    try:
        from compliance import get_company_compliance, get_mandate_by_id
        comp = get_company_compliance(symbol)
        for ob in comp.get("obligations", []):
            if ob.get("status") not in ("pending", "unverified", "missed"):
                continue
            ftype = _MANDATE_TO_TYPE.get(ob.get("mandate_id"))
            if not ftype:
                continue
            due = _parse_d(ob.get("due_date"))
            if not due:
                continue
            days = (due - today).days
            if days > 75:
                continue
            mandate = get_mandate_by_id(ob.get("mandate_id")) or {}
            mname = mandate.get("name", ob.get("mandate_id"))
            regime = mandate.get("regime", "SEBI")
            if days < 0:
                # This page answers "what do I need to draft next". A deadline
                # that passed months ago is not a drafting task — and for
                # MCA / Tax / FEMA obligations Delta has no feed that could
                # ever confirm them: they go to the Registrar, the tax
                # department or RBI, never to the exchange announcement feed
                # we read. Listing them filled the page with items the user
                # cannot action from here.
                if ob.get("status") != "missed":
                    continue
                # A confirmed miss: two independent sources responded and
                # neither had the filing.
                urg = "overdue"
                reason = (f"{mname} ({regime}) was due {abs(days)} days ago "
                          f"({ob['due_date']}) and no filing was found on NSE or BSE "
                          f"— verify and file.")
            elif days == 0:
                urg, reason = "due_today", f"{mname} ({regime}) is due today ({ob['due_date']})."
            elif days <= 7:
                urg, reason = "due_soon", f"{mname} ({regime}) is due in {days} days ({ob['due_date']})."
            else:
                urg, reason = "upcoming", f"{mname} ({regime}) is due in {days} days ({ob['due_date']})."
            _put(f"d:{ftype}", {"filing_type": ftype, "reason": reason, "urgency": urg,
                                "bucket": "deadline", "action": "draft",
                                "due_date": ob.get("due_date"), "days_remaining": days})
    except Exception as e:  # noqa: BLE001
        print(f"[DRAFT-REC] deadline pass failed for {symbol}: {e}")

    # ── 2) EVENT — recent filings (last 45 days) ──
    try:
        from database import get_cached_filings
        recents = sorted(get_cached_filings(symbol),
                         key=lambda f: f.get("sort_date", ""), reverse=True)[:60]
        for f in recents:
            d = _parse_d(f.get("sort_date") or f.get("date"))
            if d and (today - d).days > 45:
                break
            title = (f.get("title") or "").lower()
            for trig in _EVENT_TRIGGERS:
                if trig["match"](title):
                    key = f"e:{trig['type']}"
                    if key not in recs:
                        _put(key, {"filing_type": trig["type"], "reason": trig["reason"],
                                   "urgency": "event", "bucket": "event", "action": "draft",
                                   "due_date": None, "days_remaining": None,
                                   "source_filing": (f.get("title") or "")[:90],
                                   "source_date": f.get("date", "")})
    except Exception as e:  # noqa: BLE001
        print(f"[DRAFT-REC] event pass failed for {symbol}: {e}")

    # ── 3) SEASONAL ──
    for rec in _seasonal(today):
        _put(f"s:{rec['filing_type']}", rec)

    # ── 4) SECTOR-SPECIFIC ──
    try:
        if industry:
            from compliance import _sector_mandates
            for sm in _sector_mandates(industry)[:8]:
                key = f"sec:{sm.get('id', sm['name'])}"
                _put(key, {
                    "filing_type": "",           # regulator filing, not a letter
                    "label": sm["name"],
                    "reason": f"{industry}-sector obligation — {sm.get('description','')[:150]}",
                    "urgency": "sector", "bucket": "sector", "action": "compliance",
                    "due_date": None, "days_remaining": None,
                    "regulation": sm.get("regulation", ""),
                    "freq": sm.get("frequency_label", ""),
                })
    except Exception as e:  # noqa: BLE001
        print(f"[DRAFT-REC] sector pass failed for {symbol}: {e}")

    # ── Enrich draftable items with label/category ──
    try:
        from draft_engine import get_filing_type
    except Exception:
        get_filing_type = lambda x: None  # noqa: E731

    out = []
    for rec in recs.values():
        if rec.get("filing_type"):
            ft = get_filing_type(rec["filing_type"]) or {}
            rec.setdefault("label", ft.get("label", rec["filing_type"].replace("_", " ").title()))
            rec["category"] = ft.get("category", "")
        out.append(rec)

    rank = {"overdue": 0, "due_today": 1, "due_soon": 2, "event": 3,
            "upcoming": 4, "seasonal": 5, "sector": 6}
    out.sort(key=lambda r: (rank.get(r["urgency"], 9),
                            r["days_remaining"] if r.get("days_remaining") is not None else 999))
    return out[:max_items]
