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


def search_nse_symbols(query, max_results=8):
    """Search NSE for company symbols matching the query.
    Returns list of {symbol, name, type} dicts."""
    try:
        _warm_session()
        resp = SESSION.get(
            f"{BASE_URL}/api/search/autocomplete",
            params={"q": query},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[FETCH] Symbol search failed: {resp.status_code}")
            return []

        data = resp.json()
        results = []
        for item in data.get("symbols", [])[:max_results]:
            sub_type = item.get("result_sub_type", "")
            # Only show equities
            if sub_type != "equity":
                continue
            results.append({
                "symbol": item.get("symbol", ""),
                "name": item.get("symbol_info", ""),
            })
        return results
    except Exception as e:
        print(f"[FETCH] Symbol search error: {e}")
        return []


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


def fetch_filings(symbol, max_results=1000, allow_fallback=True):
    """Fetch corporate announcements for a given NSE symbol.

    Strategy:
      1. Hit the no-date-range endpoint (returns the company's full history,
         typically 500–1000 rows for an active mid/large cap).
      2. If that returns suspiciously few rows (<50), try once more with an
         explicit 5-year date window — sometimes NSE serves a thin response
         when the cookie is fresh.
      3. Only fall back to the static `FALLBACK_DATA` sample if both calls
         fail (`allow_fallback=False` lets callers like background refreshers
         skip the polluting sample data entirely).
    """

    def _try(params):
        try:
            _warm_session()
            resp = SESSION.get(API_URL, params=params, timeout=20)
            if resp.status_code != 200:
                print(f"[FETCH] NSE non-200 ({resp.status_code}) for {symbol} {params}")
                return None
            data = resp.json()
            if not isinstance(data, list):
                return None
            return data
        except Exception as e:
            print(f"[FETCH] NSE call failed for {symbol} {params}: {e}")
            return None

    # 1) full history (no date range)
    data = _try({"index": "equities", "symbol": symbol})

    # 2) if NSE served a thin response, retry with explicit date window
    if data is not None and len(data) < 50:
        from datetime import date, timedelta
        today = date.today()
        five_y_ago = today - timedelta(days=365 * 5)
        params2 = {
            "index": "equities",
            "symbol": symbol,
            "from_date": five_y_ago.strftime("%d-%m-%Y"),
            "to_date": today.strftime("%d-%m-%Y"),
        }
        retry = _try(params2)
        if retry and len(retry) > len(data):
            print(f"[FETCH] retry with date range got {len(retry)} (vs {len(data)} initial) for {symbol}")
            data = retry

    if data:
        filings = [_normalize_filing(item) for item in data[:max_results]]
        print(f"[FETCH] Got {len(filings)} live filings for {symbol}")
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
