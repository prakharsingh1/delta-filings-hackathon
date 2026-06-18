import requests
import time

BASE_URL = "https://www.nseindia.com"
API_URL = BASE_URL + "/api/corporate-announcements"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
})

# Fallback sample data when NSE API is unreachable
FALLBACK_DATA = {
    "INFY": [
        {"title": "Board Meeting Outcome - Financial Results Q3 FY2026", "date": "15-Jan-2026", "pdf_link": ""},
        {"title": "Disclosure under Reg 30 - Change in Key Managerial Personnel", "date": "10-Jan-2026", "pdf_link": ""},
        {"title": "Compliance Certificate under Reg 7(3)", "date": "05-Jan-2026", "pdf_link": ""},
        {"title": "Board Meeting Intimation - Consider Quarterly Results", "date": "28-Dec-2025", "pdf_link": ""},
        {"title": "Annual Report FY2025 - Corporate Governance Section", "date": "20-Dec-2025", "pdf_link": ""},
    ],
    "TCS": [
        {"title": "Board Meeting Outcome - Dividend Declaration", "date": "12-Jan-2026", "pdf_link": ""},
        {"title": "Disclosure under Reg 30 - Acquisition of Subsidiary", "date": "08-Jan-2026", "pdf_link": ""},
        {"title": "Change in Directors - Appointment of Independent Director", "date": "02-Jan-2026", "pdf_link": ""},
        {"title": "Compliance Certificate - Related Party Transactions", "date": "22-Dec-2025", "pdf_link": ""},
        {"title": "Board Meeting Intimation - Strategic Review", "date": "15-Dec-2025", "pdf_link": ""},
    ],
    "RELIANCE": [
        {"title": "Scheme of Arrangement - Jio Financial Services Demerger Update", "date": "14-Jan-2026", "pdf_link": ""},
        {"title": "Board Meeting Outcome - Q3 Results & Special Dividend", "date": "09-Jan-2026", "pdf_link": ""},
        {"title": "Disclosure under Reg 30 - New Energy Business Update", "date": "03-Jan-2026", "pdf_link": ""},
        {"title": "Change in Key Managerial Personnel - CFO Appointment", "date": "26-Dec-2025", "pdf_link": ""},
        {"title": "Annual General Meeting - Resolution Results", "date": "18-Dec-2025", "pdf_link": ""},
    ],
    "HDFCBANK": [
        {"title": "Board Meeting Outcome - Q3 FY2026 Financial Results", "date": "13-Jan-2026", "pdf_link": ""},
        {"title": "Disclosure under Reg 30 - Branch Expansion Plan", "date": "07-Jan-2026", "pdf_link": ""},
        {"title": "RBI Compliance - Capital Adequacy Report", "date": "30-Dec-2025", "pdf_link": ""},
        {"title": "Change in Directors - Retirement of Non-Executive Director", "date": "23-Dec-2025", "pdf_link": ""},
        {"title": "HDFC Merger Integration - Final Status Report", "date": "16-Dec-2025", "pdf_link": ""},
    ],
    "ICICIBANK": [
        {"title": "Board Meeting Outcome - Quarterly Results Q3 FY2026", "date": "11-Jan-2026", "pdf_link": ""},
        {"title": "Disclosure under Reg 30 - Credit Rating Update", "date": "06-Jan-2026", "pdf_link": ""},
        {"title": "Compliance Certificate under Reg 13(3) - Investor Grievance", "date": "29-Dec-2025", "pdf_link": ""},
        {"title": "Change in Key Managerial Personnel - CRO Appointment", "date": "21-Dec-2025", "pdf_link": ""},
        {"title": "Board Meeting Intimation - NPA Review & Provisioning", "date": "14-Dec-2025", "pdf_link": ""},
    ],
}

# Fallback raw text for AI summarization (simulates PDF content)
FALLBACK_RAW_TEXT = {
    "Board Meeting Outcome": (
        "The Board of Directors at its meeting held today has approved the unaudited financial results "
        "for the quarter. Revenue from operations grew 8.2% year-over-year. The Board has recommended "
        "an interim dividend of Rs 18 per share. The Board also reviewed the company's ESG commitments "
        "and approved the updated Corporate Governance policy. A new Audit Committee charter was adopted "
        "in line with SEBI LODR amendments. The company's net profit margin improved to 22.4%. "
        "The Chief Financial Officer presented the cash flow position and capital expenditure plans for "
        "the upcoming quarter. The Board noted compliance with all applicable regulations."
    ),
    "Change in Key Managerial Personnel": (
        "Pursuant to Regulation 30 of SEBI (LODR) Regulations, 2015, the company hereby informs that "
        "the Board of Directors has approved the appointment of a new executive effective from next month. "
        "The outgoing personnel has tendered resignation citing personal reasons. The incoming appointee "
        "brings over 20 years of experience in the financial services industry. The Nomination and "
        "Remuneration Committee had recommended this appointment after a thorough evaluation process. "
        "The Board has ensured compliance with Section 203 of the Companies Act, 2013. "
        "All necessary regulatory filings will be completed within the stipulated timeline."
    ),
    "Change in Directors": (
        "The Board of Directors has approved the appointment of a new Independent Director, subject to "
        "shareholder approval at the next General Meeting. The appointee meets all criteria under Section 149(6) "
        "of the Companies Act, 2013 and Regulation 16(1)(b) of SEBI LODR. The Board Diversity Policy has been "
        "reviewed and the new composition meets the recommended diversity benchmarks. The outgoing director "
        "completed the maximum permissible tenure. The Nomination Committee conducted due diligence including "
        "background verification and conflict of interest checks."
    ),
    "Compliance Certificate": (
        "This is to certify that the company has complied with all applicable provisions of SEBI (LODR) "
        "Regulations, 2015 for the quarter ended December 2025. The Compliance Officer has verified that "
        "all periodic filings were made within stipulated timelines. The share transfer agent has confirmed "
        "that investor grievances were resolved within 15 days. The internal audit team has reviewed related "
        "party transactions and confirmed arm's length pricing. The company maintains adequate internal "
        "financial controls as required under Section 134(5)(e) of the Companies Act, 2013."
    ),
    "Disclosure under Reg 30": (
        "Pursuant to Regulation 30 read with Schedule III of SEBI LODR Regulations, the company wishes to "
        "inform about a material development. The Board has reviewed and approved the strategic initiative "
        "after detailed deliberation. This is expected to have a positive impact on the company's long-term "
        "growth trajectory. The company has obtained all necessary internal approvals and regulatory clearances. "
        "The financial impact assessment has been reviewed by the Audit Committee. Detailed disclosures as "
        "required under the applicable regulations will follow. The Company Secretary has confirmed compliance "
        "with the disclosure timelines mandated by SEBI."
    ),
    "Scheme of Arrangement": (
        "The Board of Directors has approved the Scheme of Arrangement involving reorganization of business "
        "verticals. The appointed date for the scheme shall be determined post regulatory approvals. The "
        "scheme is subject to approval by NCLT, SEBI, stock exchanges, and shareholders. An independent "
        "valuation report has been obtained from a SEBI-registered valuer. The share exchange ratio has been "
        "determined based on the relative valuations. The scheme aims to unlock value for shareholders and "
        "create focused business entities. All listed obligations will be maintained post-demerger."
    ),
    "Annual": (
        "The Annual General Meeting was held and all resolutions were passed with requisite majority. "
        "The audited financial statements for FY2025 were adopted. The Board's Report including the Corporate "
        "Governance Report was approved. Special resolutions for continuation of directors and revision of "
        "remuneration were passed. The statutory auditors were re-appointed for a second term. The company's "
        "CSR spending exceeded the mandatory 2% threshold. Related party transactions requiring shareholder "
        "approval were ratified. The Secretarial Audit Report confirmed no qualifications or adverse remarks."
    ),
    "RBI Compliance": (
        "As per RBI guidelines and SEBI LODR requirements, the bank hereby discloses its capital adequacy "
        "position. The Common Equity Tier 1 ratio stands at 16.8%, well above the regulatory minimum. "
        "The bank's provision coverage ratio is at 78.2%. Net NPA ratio improved to 0.31% from 0.42% in "
        "the previous quarter. The Board Risk Management Committee has reviewed all material risk exposures. "
        "Liquidity Coverage Ratio stands at 128%, above the 100% minimum requirement. The bank continues "
        "to maintain compliance with all RBI master directions and circulars."
    ),
    "default": (
        "This filing is submitted pursuant to applicable SEBI regulations and the Companies Act, 2013. "
        "The Board of Directors has reviewed and approved the matters contained herein. All necessary "
        "regulatory compliances have been ensured. The Company Secretary has verified the accuracy of the "
        "disclosures made. The filing has been made within the prescribed timelines. Further details will "
        "be available on the company's website and the stock exchange portals."
    ),
}


def _warm_session():
    """Visit NSE homepage to get cookies before making API calls."""
    try:
        SESSION.get(BASE_URL, timeout=10)
        time.sleep(1)
    except Exception as e:
        print(f"[FETCH] Session warm-up failed: {e}")


import re as _re


def _search_tradingview(query, max_results=8):
    """Search NSE equities via TradingView's public symbol-search endpoint.
    Works from datacenter/cloud IPs (Railway) where NSE's own autocomplete
    is blocked. Returns {symbol, name, logoid} dicts — logoid lets us pull a
    company logo from TradingView's CDN later."""
    try:
        resp = requests.get(
            "https://symbol-search.tradingview.com/symbol_search/",
            params={"text": query, "exchange": "NSE", "type": "stock",
                    "hl": "true", "lang": "en"},
            headers={
                "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36"),
                "Origin": "https://www.tradingview.com",
                "Referer": "https://www.tradingview.com/",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[FETCH] TradingView symbol search failed: {resp.status_code}")
            return []
        out = []
        for item in (resp.json() or [])[:max_results]:
            # Strip the <em> highlight markup TradingView wraps matches in.
            sym = _re.sub(r"</?em>", "", item.get("symbol", "")).strip().upper()
            name = _re.sub(r"</?em>", "", item.get("description", "")).strip()
            if not sym:
                continue
            out.append({"symbol": sym, "name": name,
                        "logoid": item.get("logoid", "")})
        return out
    except Exception as e:
        print(f"[FETCH] TradingView symbol search error: {e}")
        return []


def _search_nse_autocomplete(query, max_results=8):
    """NSE's own autocomplete — works only from residential IPs (NSE blocks
    datacenter/cloud). Kept as a fallback for local/dev environments."""
    try:
        _warm_session()
        resp = SESSION.get(
            f"{BASE_URL}/api/search/autocomplete",
            params={"q": query},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []
        for item in data.get("symbols", [])[:max_results]:
            if item.get("result_sub_type", "") != "equity":
                continue
            results.append({
                "symbol": item.get("symbol", ""),
                "name": item.get("symbol_info", ""),
            })
        return results
    except Exception as e:
        print(f"[FETCH] NSE autocomplete error: {e}")
        return []


def search_nse_symbols(query, max_results=8):
    """Search for NSE company symbols matching the query.
    Returns list of {symbol, name[, logoid]} dicts.

    TradingView first (works from cloud IPs), NSE autocomplete as a fallback
    for residential IPs / when TradingView returns nothing."""
    results = _search_tradingview(query, max_results)
    if results:
        return results
    return _search_nse_autocomplete(query, max_results)


def _get_raw_text_for_title(title):
    """Match a fallback raw text based on keywords in the filing title."""
    for keyword, text in FALLBACK_RAW_TEXT.items():
        if keyword.lower() in title.lower():
            return text
    return FALLBACK_RAW_TEXT["default"]


def _normalize_filing(item):
    """Convert a raw NSE corporate-announcement payload row to our internal shape."""
    attachment = item.get("attchmntFile", "")
    pdf_link = ""
    if attachment:
        pdf_link = f"https://www.nseindia.com{attachment}" if attachment.startswith("/") else attachment
    return {
        "title": item.get("desc", "Untitled"),
        "date": item.get("an_dt", item.get("dt", "Unknown")),
        "sort_date": item.get("sort_date", item.get("an_dt", "")),
        "pdf_link": pdf_link,
        "description": item.get("attchmntText", ""),
        "has_xbrl": bool(item.get("hasXbrl")),
    }


# Window size used to page around NSE's response-truncation. NSE caps a single
# no-date-range response at ~1000 most-recent rows for datacenter/cloud IPs
# (Railway), silently dropping older filings. Fetching in narrow date windows
# keeps every response under that cap, so the full requested span comes back
# complete even from Railway.
FILINGS_WINDOW_DAYS = 182          # ~6 months per window
DEFAULT_HISTORY_YEARS = 3
MAX_HISTORY_YEARS = 15


def fetch_filings(symbol, years=DEFAULT_HISTORY_YEARS, max_results=12000,
                  allow_fallback=True):
    """Fetch corporate announcements for an NSE symbol over the last `years`.

    Strategy:
      1. Page the requested span in ~6-month windows and accumulate. Each
         window is small enough to dodge NSE's ~1000-row truncation on cloud
         IPs, so the full history comes back even from Railway.
      2. Also hit the no-date-range endpoint once and merge — it catches the
         very latest filings and, on residential IPs, deep history too.
      3. Dedupe by (title, date); only fall back to the static sample if every
         call fails (`allow_fallback=False` skips the sample entirely).
    """
    try:
        years = max(1, min(int(years or DEFAULT_HISTORY_YEARS), MAX_HISTORY_YEARS))
    except (TypeError, ValueError):
        years = DEFAULT_HISTORY_YEARS
    history_days = 365 * years

    def _try(params, retries=1):
        """GET with one retry — a transient drop must not silently lose a
        whole 6-month window of history."""
        for attempt in range(retries + 1):
            try:
                _warm_session()
                resp = SESSION.get(API_URL, params=params, timeout=25)
                if resp.status_code != 200:
                    print(f"[FETCH] NSE non-200 ({resp.status_code}) for {symbol} {params}")
                elif isinstance(resp.json(), list):
                    return resp.json()
            except Exception as e:
                print(f"[FETCH] NSE call failed for {symbol} {params} "
                      f"(attempt {attempt+1}): {e}")
            if attempt < retries:
                time.sleep(1.0)
        return None

    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=history_days)

    # Accumulate raw rows, deduped by a stable key, across all windows.
    merged = {}
    any_response = False

    def _absorb(rows):
        nonlocal any_response
        if rows is None:
            return
        any_response = True
        for item in rows:
            norm = _normalize_filing(item)
            key = (norm["title"], norm["date"])
            if key not in merged:
                merged[key] = norm

    # 1) Windowed 5-year sweep (newest window first).
    win_end = today
    windows = 0
    while win_end > start:
        win_start = max(start, win_end - timedelta(days=FILINGS_WINDOW_DAYS))
        rows = _try({
            "index": "equities", "symbol": symbol,
            "from_date": win_start.strftime("%d-%m-%Y"),
            "to_date": win_end.strftime("%d-%m-%Y"),
        })
        _absorb(rows)
        windows += 1
        # Step back one day past the window start to avoid overlap dupes.
        win_end = win_start - timedelta(days=1)

    # 2) No-date-range call — latest filings + deep history on residential IPs.
    _absorb(_try({"index": "equities", "symbol": symbol}))

    if any_response and merged:
        filings = list(merged.values())
        # Newest first by sort_date (falls back to date string).
        filings.sort(key=lambda f: f.get("sort_date") or f.get("date") or "", reverse=True)
        filings = filings[:max_results]
        print(f"[FETCH] Got {len(filings)} live filings for {symbol} "
              f"({windows} windows + no-range, {years}y)")
        return filings

    if not allow_fallback:
        return []

    # Fallback to sample data — only used for demo/offline; do NOT pollute
    # real watchlist DBs. process_filings() should pass allow_fallback=False
    # for production refresh paths once we trust NSE availability.
    print(f"[FETCH] Using fallback data for {symbol}")
    fallback = FALLBACK_DATA.get(symbol, FALLBACK_DATA["INFY"])
    result = []
    for item in fallback:
        result.append({
            "title": item["title"],
            "date": item["date"],
            "sort_date": item.get("sort_date", item["date"]),
            "pdf_link": item["pdf_link"],
            "raw_text": _get_raw_text_for_title(item["title"]),
            "description": item.get("description", ""),
            "has_xbrl": item.get("has_xbrl", False),
        })
    return result
