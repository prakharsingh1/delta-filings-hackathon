"""Quarter-over-quarter comparison of reported financial results.

The figures live in the results PDF attached to the board-meeting outcome —
not in the announcement title and not in the cached text, which stops at the
covering letter. This module pulls the PDF, extracts the reported line items,
caches them, and lines the quarters up so a Company Secretary can see both
the movement and the disclosure timing against SEBI LODR Reg 33.

Extraction is AI-assisted because the table layout differs by issuer and by
year, but every figure is written back with the filing it came from and the
raw snippet it was read out of, so anything on screen can be traced to a
source document.
"""

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from database import get_connection

# Announcement titles that carry a results table.
_RESULTS_TITLES = (
    "outcome of board meeting", "financial result", "integrated filing",
    "newspaper publication",
)
# Language that confirms a results table is actually present in the text.
_RESULTS_MARKERS = re.compile(
    r"(revenue from operations|total income|profit for the period|"
    r"profit before tax|earnings per (equity )?share)", re.I)

QUARTER_ENDS = {3: "Q4", 6: "Q1", 9: "Q2", 12: "Q3"}


def init_fin_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_extracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            filing_id INTEGER NOT NULL UNIQUE,
            filing_date TEXT DEFAULT '',
            period_label TEXT DEFAULT '',
            period_end TEXT DEFAULT '',
            basis TEXT DEFAULT '',
            unit TEXT DEFAULT '',
            figures_json TEXT DEFAULT '{}',
            evidence TEXT DEFAULT '',
            model TEXT DEFAULT '',
            extracted_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fin_symbol ON financial_extracts(symbol, period_end DESC)")
    conn.commit()
    conn.close()


def find_results_filings(symbol: str, limit: int = 12) -> List[Dict]:
    """Announcements that plausibly carry a results table, newest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, title, date, sort_date, pdf_link, raw_text FROM filings "
        "WHERE company = ? AND pdf_link != '' ORDER BY sort_date DESC LIMIT 1200",
        (symbol.upper(),)).fetchall()
    conn.close()

    out, seen_periods = [], set()
    for r in rows:
        title = (r["title"] or "").lower()
        if not any(t in title for t in _RESULTS_TITLES):
            continue
        # Prefer filings whose cached text already shows results language; keep
        # board-meeting outcomes regardless since the table is in the PDF.
        text_hit = bool(_RESULTS_MARKERS.search(r["raw_text"] or ""))
        if not text_hit and "outcome of board meeting" not in title:
            continue
        period = _period_from_date(r["sort_date"][:10])
        # One candidate per (period, month) rather than per period: a quarter
        # produces several announcements and the richest one is not always the
        # first seen, but two filings a week apart are the same event.
        key = (period, r["sort_date"][:7])
        if key in seen_periods:
            continue
        seen_periods.add(key)
        out.append({"filing_id": r["id"], "title": r["title"], "date": r["date"],
                    "sort_date": r["sort_date"][:10], "pdf_link": r["pdf_link"],
                    "period_guess": period, "text_has_table": text_hit})
        if len(out) >= limit:
            break
    return out


def _period_from_date(d: str) -> str:
    """Which quarter a results filing most likely reports on — results are
    filed after the quarter closes, so step back to the prior quarter end."""
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""
    m = dt.month
    qe_month = ((m - 1) // 3) * 3          # last month of the PREVIOUS quarter
    if qe_month == 0:                      # Jan-Mar filing reports Oct-Dec
        return f"Q3 FY{dt.year - 1}-{str(dt.year)[2:]}"
    # Last day of qe_month, not the day before its first.
    end = (date(dt.year, qe_month + 1, 1) - timedelta(days=1)) if qe_month < 12 \
        else date(dt.year, 12, 31)
    q = QUARTER_ENDS.get(end.month, "")
    fy_start = end.year if end.month > 3 else end.year - 1
    return f"{q} FY{fy_start}-{str(fy_start + 1)[2:]}"


_PROMPT = """You are reading an Indian listed company's quarterly financial results.

COMPANY: {company}
FILING DATE: {filing_date}

DOCUMENT TEXT (extracted from the filed PDF):
\"\"\"
{text}
\"\"\"

Extract the headline reported figures for the MOST RECENT period shown.

RULES:
- Report only figures that appear in the text. Never estimate or infer.
- Indian results are usually in Rs crore or Rs lakh and use Indian digit
  grouping (17,149 means seventeen thousand one hundred forty-nine).
- If the statement shows both standalone and consolidated, prefer
  CONSOLIDATED and say so in `basis`.
- If a line item is absent, use null. Do not substitute a similar item.
- `evidence` must quote the line you read the revenue figure from, verbatim.

Return ONLY this JSON:
{{
  "period_label": "<e.g. Q1 FY2026-27, as printed>",
  "period_end": "<YYYY-MM-DD of the period end if stated, else empty>",
  "basis": "consolidated" | "standalone" | "unknown",
  "unit": "<Rs crore | Rs lakh | Rs million, as printed>",
  "figures": {{
    "revenue_from_operations": <number or null>,
    "total_income": <number or null>,
    "profit_before_tax": <number or null>,
    "profit_after_tax": <number or null>,
    "eps_basic": <number or null>
  }},
  "evidence": "<verbatim line the revenue figure was read from>"
}}"""


def extract_figures(symbol: str, filing: Dict, company_name: str = "",
                    force: bool = False) -> Optional[Dict]:
    """Pull one filing's reported figures. Cached per filing."""
    init_fin_db()
    fid = filing["filing_id"]

    if not force:
        conn = get_connection()
        row = conn.execute("SELECT * FROM financial_extracts WHERE filing_id = ?", (fid,)).fetchone()
        conn.close()
        if row:
            d = dict(row)
            d["figures"] = json.loads(d.pop("figures_json") or "{}")
            d["cached"] = True
            return d

    from pdf_parser import download_and_extract
    try:
        text = download_and_extract(filing["pdf_link"]) or ""
    except Exception as e:
        print(f"[FIN] pdf fetch failed for {fid}: {str(e)[:110]}")
        return None
    if not _RESULTS_MARKERS.search(text):
        return None

    from agents.client import call_json
    res = call_json(
        _PROMPT.format(company=company_name or symbol,
                       filing_date=filing.get("date", ""), text=text[:14000]),
        role="monitor", max_output_tokens=900, temperature=0.1, timeout_ms=60_000)
    if not res["ok"] or not isinstance(res["data"], dict):
        return None

    d = res["data"]
    figures = {k: v for k, v in (d.get("figures") or {}).items()
               if isinstance(v, (int, float))}
    if not figures:
        return None

    record = {
        "symbol": symbol.upper(), "filing_id": fid,
        "filing_date": filing.get("sort_date", ""),
        "period_label": str(d.get("period_label", ""))[:40] or filing.get("period_guess", ""),
        "period_end": str(d.get("period_end", ""))[:10],
        "basis": d.get("basis", "unknown"), "unit": str(d.get("unit", ""))[:24],
        "figures": figures, "evidence": str(d.get("evidence", ""))[:300],
        "model": res["model"],
    }

    conn = get_connection()
    conn.execute("""
        INSERT INTO financial_extracts
            (symbol, filing_id, filing_date, period_label, period_end, basis, unit,
             figures_json, evidence, model, extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(filing_id) DO UPDATE SET
            period_label=excluded.period_label, period_end=excluded.period_end,
            basis=excluded.basis, unit=excluded.unit, figures_json=excluded.figures_json,
            evidence=excluded.evidence, model=excluded.model, extracted_at=excluded.extracted_at
    """, (record["symbol"], fid, record["filing_date"], record["period_label"],
          record["period_end"], record["basis"], record["unit"],
          json.dumps(figures), record["evidence"], record["model"],
          datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()
    record["cached"] = False
    return record


_ANNUAL_RE = re.compile(r"(financial year|year ended|full year|annual|FY\s*\d)", re.I)
_QUARTER_RE = re.compile(r"(quarter|Q[1-4])", re.I)


def _period_type(label: str, period_end: str) -> str:
    """Quarterly or annual. Comparing a full year against a quarter produces a
    nonsense percentage — a +290% 'growth' that is really twelve months set
    against three — so periods are only ever compared like for like.
    A results statement can say both ('quarter and year ended'); the annual
    column is the one printed, so annual wins.
    """
    text = label or ""
    if _ANNUAL_RE.search(text):
        return "annual"
    if _QUARTER_RE.search(text):
        return "quarter"
    return "quarter" if (period_end or "")[5:7] not in ("03",) else "annual"


def _tidy_label(label: str, period_end: str, ptype: str) -> str:
    """Normalise the printed period text into something scannable."""
    label = re.sub(r"\s+", " ", (label or "")).strip()
    m = re.search(r"(Q[1-4])\s*(FY)?\s*(\d{4})", label, re.I)
    if m:
        return f"{m.group(1).upper()} {m.group(3)}"
    try:
        pe = datetime.strptime((period_end or "")[:10], "%Y-%m-%d").date()
        if ptype == "annual":
            return f"FY{pe.year - 1}-{str(pe.year)[2:]}"
        return f"{QUARTER_ENDS.get(pe.month, 'Q?')} {pe.strftime('%b %Y')}"
    except (ValueError, TypeError):
        return label[:28] or "period"


# Issuers print results in whichever unit they please — "Rs. In Lakhs",
# "crores", "Rs in Crores" all appear in the same company's filings. Left
# unconverted, a lakh figure sitting beside a crore figure in the same row is
# wrong by a factor of 100. Everything is normalised to Rs crore.
_UNIT_TO_CRORE = [
    (re.compile(r"lakh|lac", re.I), 0.01),
    (re.compile(r"crore|cr\b", re.I), 1.0),
    (re.compile(r"million|mn\b", re.I), 0.1),
    (re.compile(r"billion|bn\b", re.I), 100.0),
    (re.compile(r"thousand", re.I), 0.0001),
]
DISPLAY_UNIT = "Rs crore"


def _unit_factor(unit: str):
    """Multiplier to Rs crore, or None when the unit cannot be read."""
    for rx, f in _UNIT_TO_CRORE:
        if rx.search(unit or ""):
            return f
    return None


def _to_crore(figures: Dict, unit: str) -> Optional[Dict]:
    f = _unit_factor(unit)
    if f is None:
        return None
    return {k: (v * f if isinstance(v, (int, float)) and not k.startswith("eps") else v)
            for k, v in figures.items()}


def _drop_magnitude_outliers(periods: List[Dict], anchor: str = "total_income"):
    """Remove periods whose scale is implausible against their own peers.

    Unit extraction is the weakest link: one HUL filing printed its unit
    ambiguously and was read as lakh when the figures were already crore,
    which converted a 16,000 crore quarter into 200. A figure wrong by 100x is
    far worse than a missing one, so a period more than an order of magnitude
    away from the median of its siblings is withheld and reported separately.
    """
    vals = [(p, p.get("figures", {}).get(anchor)) for p in periods]
    known = sorted(v for _, v in vals if isinstance(v, (int, float)) and v)
    if len(known) < 3:
        return periods, []
    median = known[len(known) // 2]
    keep, dropped = [], []
    for p, v in vals:
        if isinstance(v, (int, float)) and v and (v > median * 10 or v < median / 10):
            dropped.append({"period": p.get("period_label"),
                            "filing_id": (p.get("source_filing") or {}).get("id"),
                            "reason": f"reported scale ({v:,.0f}) is an order of magnitude "
                                      f"from the other quarters — the unit on this filing "
                                      f"could not be read reliably"})
        else:
            keep.append(p)
    return keep, dropped


def _adjacent(a: Dict, b: Dict) -> bool:
    """Whether two quarters sit next to each other. Without this the series
    happily compared Q1 2026 against Q3 2024 — an eighteen-month gap — and
    printed the result as quarter-on-quarter movement."""
    try:
        da = datetime.strptime(a["filing_date"][:10], "%Y-%m-%d").date()
        db = datetime.strptime(b["filing_date"][:10], "%Y-%m-%d").date()
    except (ValueError, TypeError, KeyError):
        return False
    gap = abs((da - db).days)
    return 60 <= gap <= 135          # one quarter apart, with slack for filing dates


LINE_ITEMS = [
    ("revenue_from_operations", "Revenue from operations"),
    ("total_income", "Total income"),
    ("profit_before_tax", "Profit before tax"),
    ("profit_after_tax", "Profit after tax"),
    ("eps_basic", "EPS (basic)"),
]


def compare(symbol: str, company_name: str = "", quarters: int = 4,
            extract_missing: bool = True) -> Dict[str, Any]:
    """Line the reported quarters up, with movement and Reg 33 timing."""
    init_fin_db()
    candidates = find_results_filings(symbol, limit=quarters * 2 + 4)

    periods = []
    for f in candidates:
        rec = extract_figures(symbol, f, company_name) if extract_missing else None
        if not rec:
            conn = get_connection()
            row = conn.execute("SELECT * FROM financial_extracts WHERE filing_id=?",
                               (f["filing_id"],)).fetchone()
            conn.close()
            if not row:
                continue
            rec = dict(row)
            rec["figures"] = json.loads(rec.pop("figures_json") or "{}")
        rec["timing"] = _reg33_timing(f["sort_date"], rec.get("period_end"))
        rec["source_filing"] = {"id": f["filing_id"], "title": f["title"],
                                "date": f["date"], "pdf_link": f["pdf_link"]}
        periods.append(rec)
        if len(periods) >= quarters:
            break

    for p in periods:
        p["period_type"] = _period_type(p.get("period_label", ""), p.get("period_end", ""))
        # Normalise to one unit before anything is compared or displayed.
        converted = _to_crore(p.get("figures") or {}, p.get("unit", ""))
        if converted is None:
            p["unit_unknown"] = True          # keep it out of the series
        else:
            p["figures"] = converted
            p["unit"] = DISPLAY_UNIT
        p["period_label"] = _tidy_label(p.get("period_label", ""),
                                        p.get("period_end", ""), p["period_type"])

    # Quarter-over-quarter means quarters. Annual statements are kept out of
    # the series rather than silently compared against three-month figures.
    periods = [p for p in periods
               if p["period_type"] == "quarter" and not p.get("unit_unknown")]
    periods.sort(key=lambda p: p.get("filing_date", ""), reverse=True)
    periods, suspect = _drop_magnitude_outliers(periods)

    rows = []
    for key, label in LINE_ITEMS:
        series = []
        for i, p in enumerate(periods):
            cur = p["figures"].get(key)
            prev = periods[i + 1]["figures"].get(key) if i + 1 < len(periods) else None
            change = None
            # Only compare when both sides are quarters in the same unit.
            if (isinstance(cur, (int, float)) and isinstance(prev, (int, float)) and prev
                    and periods[i + 1].get("unit") == p.get("unit")
                    and _adjacent(p, periods[i + 1])):
                change = round((cur - prev) / abs(prev) * 100, 1)
            series.append({"period": p.get("period_label"), "value": cur, "change_pct": change})
        if any(s["value"] is not None for s in series):
            rows.append({"key": key, "label": label, "series": series})

    return {
        "symbol": symbol.upper(),
        "periods": [{"period_label": p.get("period_label"), "basis": p.get("basis"),
                     "period_type": p.get("period_type"),
                     "unit": p.get("unit"), "filing_date": p.get("filing_date"),
                     "evidence": p.get("evidence"), "timing": p.get("timing"),
                     "source_filing": p.get("source_filing")} for p in periods],
        "rows": rows,
        "units": DISPLAY_UNIT if periods else "",
        "unit_note": "All figures converted to Rs crore; EPS left as reported.",
        "excluded": suspect,
        "basis": periods[0].get("basis") if periods else "",
    }


def _reg33_timing(filed_on: str, period_end: str) -> Dict[str, Any]:
    """Reg 33: quarterly results within 45 days of quarter end, annual within 60."""
    try:
        filed = datetime.strptime(filed_on[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {}
    pe = None
    try:
        pe = datetime.strptime((period_end or "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        m = ((filed.month - 1) // 3) * 3
        if m:
            pe = date(filed.year, m, 1) - timedelta(days=1)
    if not pe:
        return {}
    limit = 60 if pe.month == 3 else 45
    due = pe + timedelta(days=limit)
    days = (filed - pe).days
    return {"period_end": pe.isoformat(), "filed_on": filed.isoformat(),
            "days_after_period_end": days, "limit_days": limit,
            "due_by": due.isoformat(), "within_limit": filed <= due,
            "headroom_days": (due - filed).days}
