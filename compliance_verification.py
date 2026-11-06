"""Compliance Verification — multi-source cross-check for SEBI obligations.

Before flagging a SEBI obligation as "missed", we must be confident the filing
didn't happen. A false "missed" for a Nifty-50 bank is unacceptable.

This module queries multiple authoritative sources for each obligation:

  1. NSE Integrated Filing Results  (nseindia.com/api/integrated-filing-results)
       Post-2024 unified feed. Covers SEBI LODR quarterly filings (Governance,
       Financials) keyed by `qe_Date` and `creation_Date`. This is the
       authoritative ground truth for listed companies.

  2. NSE Corporate Governance Master (nseindia.com/api/corporate-governance-master)
       Legacy Reg 27 CG report feed — still valid for older quarters.

  3. NSE Shareholding Pattern Master (nseindia.com/api/corporate-share-holdings-master)
       Authoritative feed for Reg 31 shareholding pattern filings.

  4. NSE Financial Results Comparison (nseindia.com/api/results-comparision)
       Authoritative feed for Reg 33 financial results.

  5. BSE Announcements (api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w)
       Independent second exchange. Broad catch for any mandate.

  6. NSE Corporate Announcements (date-ranged)
       Last-resort fallback — catches event-based filings.

Decision rule:
  * If an authoritative feed (1-3) returns the filing → 'submitted' (or 'delayed' if late)
  * A match found only by keywords in announcements (5-6) is a candidate, not
    proof: words like "reg 31" or "shp" appear in unrelated announcements, and
    calling those filed is worse than saying nothing. → 'unverified', with the
    candidate kept in the evidence for the CS to check.
  * Else if an authoritative feed and one other source responded AND none had a match → 'missed'
  * Else → 'unverified' (we don't have enough signal to claim anything)
"""

import re
import requests
import time
from datetime import datetime, date, timedelta
from typing import Optional, Tuple, Dict, Any, List

# ── BSE scrip code map (watchlist companies) ──
# BSE uses numeric scrip codes instead of tickers.
BSE_SCRIP_MAP = {
    # Banking
    "HDFCBANK": "500180",
    "ICICIBANK": "532174",
    "SBIN": "500112",
    "AXISBANK": "532215",
    "KOTAKBANK": "500247",
    # FMCG
    "HINDUNILVR": "500696",
    "ITC": "500875",
    "NESTLEIND": "500790",
    "BRITANNIA": "500825",
    "DABUR": "500096",
    # Steel
    "TATASTEEL": "500470",
    "JSWSTEEL": "500228",
    "SAIL": "500113",
    "JINDALSTEL": "532286",
    # Pharma
    "SUNPHARMA": "524715",
    "DRREDDY": "500124",
    "CIPLA": "500087",
    "DIVISLAB": "532488",
    "LUPIN": "500257",
    # IT
    "TCS": "532540",
    "INFY": "500209",
    "WIPRO": "507685",
    "HCLTECH": "532281",
    "TECHM": "532755",
}

# ── Mandate → Integrated Filing type map ──
# Per SEBI Circular SEBI/HO/CFD/CFD-PoD-1/P/CIR/2024/185 (Dec 2024), the
# governance disclosures under Reg 13(3), 27(2), 31(1)(b), and 32(1) were
# consolidated into a single "Integrated Filing- Governance" package due
# within 30 days of quarter-end. So a single Integrated Filing- Governance
# upload satisfies all four obligations simultaneously.
INTEGRATED_FILING_TYPES = {
    "LODR-REG13": "Integrated Filing- Governance",
    "LODR-REG27": "Integrated Filing- Governance",
    "LODR-REG31": "Integrated Filing- Governance",
    "LODR-REG33": "Integrated Filing- Financials",
}

# ── Broader keyword patterns per mandate (for announcement scans) ──
MANDATE_KEYWORD_PATTERNS = {
    "LODR-REG27": [
        "corporate governance", "regulation 27", "reg. 27", "reg 27",
        "governance report", "cg report", "compliance report on corporate",
        "integrated filing- governance", "integrated filing-governance",
        "integrated filing governance",
    ],
    "LODR-REG31": [
        "shareholding pattern", "regulation 31", "reg. 31", "reg 31",
        "shp", "pattern of shareholding",
    ],
    "LODR-REG33": [
        "financial result", "quarterly result", "annual result",
        "regulation 33", "reg. 33", "reg 33", "audited standalone",
        "audited consolidated", "unaudited standalone", "unaudited consolidated",
        "integrated filing- financial", "integrated filing-financial",
    ],
    "LODR-REG13": [
        "investor complaint", "investor grievance", "regulation 13",
        "reg. 13", "reg 13",
    ],
    "LODR-REG74": [
        "regulation 74", "reg. 74", "reg 74", "rta certificate",
        "registrar certificate", "dematerialization",
    ],
    "LODR-REG29": [
        "board meeting intimation", "prior intimation", "regulation 29",
        "reg. 29", "reg 29", "intimation of board meeting",
        "notice of board meeting",
    ],
}

# ── HTTP session with cookie warm-up ──
_NSE_SESSION: Optional[requests.Session] = None
_NSE_LAST_WARMUP = 0
_BSE_SESSION: Optional[requests.Session] = None

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _warmup_nse_session(sess: requests.Session) -> None:
    try:
        sess.get("https://www.nseindia.com/companies-listing/corporate-filings-announcements", timeout=10)
        sess.get("https://www.nseindia.com/companies-listing/corporate-filings-governance", timeout=10)
    except Exception as e:
        print(f"[VERIFY] NSE warmup failed: {e}")


def _get_nse_session(force_refresh: bool = False) -> requests.Session:
    global _NSE_SESSION, _NSE_LAST_WARMUP
    now = time.time()
    if force_refresh or _NSE_SESSION is None or (now - _NSE_LAST_WARMUP) > 300:
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
        })
        _warmup_nse_session(sess)
        _NSE_SESSION = sess
        _NSE_LAST_WARMUP = now
    return _NSE_SESSION


# Simple per-URL cache (keyed by url+params) with a short TTL. NSE responses
# are stable per company over minutes; batching multiple mandates saves calls.
_NSE_CACHE: Dict[str, Tuple[float, Any]] = {}
_NSE_CACHE_TTL = 120  # seconds


def _cache_key(url: str, params: dict) -> str:
    return url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))


def _nse_get(url: str, params: dict, timeout: int = 15) -> Optional[requests.Response]:
    """GET with cookie re-warmup on 401/403 and short-TTL response caching.
    Returns a response-like object with a .json() method on success, else None."""
    key = _cache_key(url, params)
    now = time.time()
    cached = _NSE_CACHE.get(key)
    if cached and (now - cached[0]) < _NSE_CACHE_TTL:
        class _Cached:
            def __init__(self, payload):
                self._payload = payload
                self.status_code = 200
            def json(self):
                return self._payload
        return _Cached(cached[1])

    for attempt in range(3):
        sess = _get_nse_session(force_refresh=(attempt > 0))
        try:
            r = sess.get(url, params=params, timeout=timeout)
        except Exception as e:
            print(f"[VERIFY] NSE GET error on {url[-40:]}: {e}")
            time.sleep(1 + attempt)
            continue
        if r.status_code == 200:
            try:
                payload = r.json()
                _NSE_CACHE[key] = (now, payload)
            except Exception:
                pass
            return r
        if r.status_code in (401, 403, 429):
            print(f"[VERIFY] NSE {r.status_code} on {url[-40:]}, re-warming (attempt {attempt+1})")
            time.sleep(1 + attempt * 2)
            continue
        return None
    return None


def _get_bse_session() -> requests.Session:
    global _BSE_SESSION
    if _BSE_SESSION is None:
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.bseindia.com/corporates/ann.html",
            "Origin": "https://www.bseindia.com",
        })
        _BSE_SESSION = sess
    return _BSE_SESSION


# ── Date helpers ──

def _parse_nse_date(s: Optional[str]) -> Optional[date]:
    """Parse strings like '31-DEC-2025' or '26-Jan-2026 16:16:12'."""
    if not s:
        return None
    s = s.strip().split()[0]  # drop time portion
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _quarter_end_iso(d: date) -> str:
    return d.isoformat()


def _bse_date(d: date) -> str:
    return d.strftime("%Y%m%d")


# ── Individual source verifiers ──
# Each returns (responded: bool, match: Optional[dict], raw_hits: List[dict])
# where match = {'filed_date': date, 'title': str, 'source': str, 'url': str}


def _verify_integrated_filing(symbol: str, mandate_id: str, q_end: date) -> Tuple[bool, Optional[Dict], List[Dict]]:
    filing_type = INTEGRATED_FILING_TYPES.get(mandate_id)
    if not filing_type:
        return (False, None, [])

    r = _nse_get(
        "https://www.nseindia.com/api/integrated-filing-results",
        {"index": "equities", "symbol": symbol},
    )
    if r is None:
        return (False, None, [])
    try:
        data = r.json().get("data", [])
    except Exception as e:
        print(f"[VERIFY] integrated-filing parse failed for {symbol}: {e}")
        return (False, None, [])

    q_end_str = q_end.strftime("%d-%b-%Y").upper()
    hits = []
    for rec in data:
        qe = (rec.get("qe_Date") or "").upper()
        rtype = rec.get("type") or ""
        if qe == q_end_str and filing_type.lower() in rtype.lower():
            filed_dt = _parse_nse_date(rec.get("creation_Date") or rec.get("broadcast_Date") or rec.get("revised_Date"))
            hits.append({
                "filed_date": filed_dt,
                "title": rtype,
                "source": "NSE Integrated Filing",
                "url": rec.get("xbrl") or rec.get("ixbrl") or "",
            })

    if hits:
        # Pick earliest filed_date (original filing, not revision)
        hits_with_date = [h for h in hits if h["filed_date"]]
        match = min(hits_with_date, key=lambda x: x["filed_date"]) if hits_with_date else hits[0]
        return (True, match, hits)
    return (True, None, [])  # Source responded, no match for this quarter


def _verify_cg_master(symbol: str, q_end: date) -> Tuple[bool, Optional[Dict], List[Dict]]:
    r = _nse_get(
        "https://www.nseindia.com/api/corporate-governance-master",
        {"index": "equities", "symbol": symbol},
    )
    if r is None:
        return (False, None, [])
    try:
        data = r.json().get("data", [])
    except Exception as e:
        print(f"[VERIFY] cg-master parse failed for {symbol}: {e}")
        return (False, None, [])

    q_end_str = q_end.strftime("%d-%b-%Y").upper()
    hits = []
    for rec in data:
        qe = (rec.get("date") or "").upper()
        if qe == q_end_str:
            filed_dt = _parse_nse_date(rec.get("submissionDate") or rec.get("cgTimeStamp"))
            hits.append({
                "filed_date": filed_dt,
                "title": "Corporate Governance Report",
                "source": "NSE CG Master",
                "url": rec.get("xbrl", ""),
            })
    if hits:
        hits_with_date = [h for h in hits if h["filed_date"]]
        match = min(hits_with_date, key=lambda x: x["filed_date"]) if hits_with_date else hits[0]
        return (True, match, hits)
    return (True, None, [])


def _verify_shp_master(symbol: str, q_end: date) -> Tuple[bool, Optional[Dict], List[Dict]]:
    r = _nse_get(
        "https://www.nseindia.com/api/corporate-share-holdings-master",
        {"index": "equities", "symbol": symbol},
    )
    if r is None:
        return (False, None, [])
    try:
        data = r.json()
        if not isinstance(data, list):
            return (False, None, [])
    except Exception as e:
        print(f"[VERIFY] shp-master parse failed for {symbol}: {e}")
        return (False, None, [])

    q_end_str = q_end.strftime("%d-%b-%Y").upper()
    for rec in data:
        qe = (rec.get("date") or "").upper()
        if qe == q_end_str:
            # SHP xbrl url contains a filing timestamp in the path
            xbrl = rec.get("xbrl", "")
            filed_dt = _extract_date_from_xbrl_url(xbrl) or _parse_nse_date(rec.get("submissionDate"))
            match = {
                "filed_date": filed_dt,
                "title": "Shareholding Pattern",
                "source": "NSE SHP Master",
                "url": xbrl,
            }
            return (True, match, [match])
    return (True, None, [])


def _extract_date_from_xbrl_url(url: str) -> Optional[date]:
    """XBRL URLs embed filing timestamp: CG_1387418_10012025HHMMSS_WEB.xml"""
    if not url:
        return None
    import re
    m = re.search(r"_(\d{8})\d{6}_", url)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d%m%Y").date()
        except ValueError:
            return None
    return None


_BSE_SCRIP_LOOKUP_CACHE: Dict[str, Optional[str]] = {}


def _resolve_bse_scrip(symbol: str) -> Optional[str]:
    """Resolve a ticker to its BSE scrip code. Tries the hardcoded map first,
    falls back to BSE's PeerSmartSearch endpoint, and caches the result.
    Returns None only if BSE genuinely has no listing for the symbol."""
    sym = symbol.upper().strip()
    if sym in BSE_SCRIP_MAP:
        return BSE_SCRIP_MAP[sym]
    if sym in _BSE_SCRIP_LOOKUP_CACHE:
        return _BSE_SCRIP_LOOKUP_CACHE[sym]

    try:
        sess = _get_bse_session()
        r = sess.get(
            "https://api.bseindia.com/BseIndiaAPI/api/PeerSmartSearch/w",
            params={"Type": "SS", "text": sym},
            timeout=10,
        )
        if r.status_code != 200:
            _BSE_SCRIP_LOOKUP_CACHE[sym] = None
            return None
        body = r.text or ""
    except Exception as e:
        print(f"[VERIFY] BSE scrip lookup failed for {sym}: {e}")
        _BSE_SCRIP_LOOKUP_CACHE[sym] = None
        return None

    # Response is a string of <li> entries. Each entry contains
    # liclick('SCRIP_CODE','COMPANY NAME') and <strong>SYMBOL</strong>.
    # Prefer the entry whose <strong> matches our symbol exactly.
    entries = re.findall(
        r"liclick\('(\d{5,7})','[^']*'\).*?<span>(.*?)</span>",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    scrip: Optional[str] = None
    for code, span_html in entries:
        m = re.search(r"<strong>([^<]+)</strong>", span_html, flags=re.IGNORECASE)
        if m and m.group(1).strip().upper() == sym:
            scrip = code
            break
    if scrip is None and entries:
        scrip = entries[0][0]  # best-effort: first hit

    _BSE_SCRIP_LOOKUP_CACHE[sym] = scrip
    if scrip:
        print(f"[VERIFY] Resolved BSE scrip for {sym}: {scrip}")
    return scrip


def _verify_bse_announcements(symbol: str, mandate_id: str, q_end: date, due_dt: date) -> Tuple[bool, Optional[Dict], List[Dict]]:
    scrip = _resolve_bse_scrip(symbol)
    if not scrip:
        return (False, None, [])

    keywords = MANDATE_KEYWORD_PATTERNS.get(mandate_id, [])
    # Search window: 30 days before quarter-end to 30 days after due date
    win_start = q_end - timedelta(days=30)
    win_end = due_dt + timedelta(days=30)

    try:
        sess = _get_bse_session()
        r = sess.get(
            "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
            params={
                "pageno": 1, "strCat": "-1", "strPrevDate": _bse_date(win_start),
                "strScrip": scrip, "strSearch": "P", "strToDate": _bse_date(win_end),
                "strType": "C", "subcategory": "-1",
            },
            timeout=20,
        )
        if r.status_code != 200:
            return (False, None, [])
        items = r.json().get("Table", [])
    except Exception as e:
        print(f"[VERIFY] BSE failed for {symbol}: {e}")
        return (False, None, [])

    hits = []
    for it in items:
        text = ((it.get("HEADLINE") or "") + " " + (it.get("SUBCATNAME") or "")).lower()
        if any(kw in text for kw in keywords):
            news_dt = (it.get("NEWS_DT") or "")[:10]
            try:
                filed_dt = datetime.strptime(news_dt, "%Y-%m-%d").date()
            except ValueError:
                filed_dt = None
            hits.append({
                "filed_date": filed_dt,
                "title": it.get("HEADLINE", ""),
                "source": "BSE Announcements",
                "url": it.get("ATTACHMENTNAME", ""),
            })
    if hits:
        hits_with_date = [h for h in hits if h["filed_date"]]
        match = min(hits_with_date, key=lambda x: x["filed_date"]) if hits_with_date else hits[0]
        return (True, match, hits)
    return (True, None, [])  # BSE responded, no keyword match


def _verify_nse_announcements_ranged(symbol: str, mandate_id: str, q_end: date, due_dt: date) -> Tuple[bool, Optional[Dict], List[Dict]]:
    keywords = MANDATE_KEYWORD_PATTERNS.get(mandate_id, [])
    win_start = q_end - timedelta(days=30)
    win_end = due_dt + timedelta(days=30)

    r = _nse_get(
        "https://www.nseindia.com/api/corporate-announcements",
        {
            "index": "equities", "symbol": symbol,
            "from_date": win_start.strftime("%d-%m-%Y"),
            "to_date": win_end.strftime("%d-%m-%Y"),
        },
    )
    if r is None:
        return (False, None, [])
    try:
        items = r.json()
        if not isinstance(items, list):
            return (False, None, [])
    except Exception as e:
        print(f"[VERIFY] NSE ranged parse failed for {symbol}: {e}")
        return (False, None, [])

    hits = []
    for it in items:
        text = ((it.get("desc") or "") + " " + (it.get("attchmntText") or "") + " " + (it.get("smIndustry") or "")).lower()
        if any(kw in text for kw in keywords):
            sd = (it.get("sort_date") or it.get("an_dt") or "")[:10]
            try:
                filed_dt = datetime.strptime(sd, "%Y-%m-%d").date()
            except ValueError:
                filed_dt = None
            hits.append({
                "filed_date": filed_dt,
                "title": it.get("desc", ""),
                "source": "NSE Announcements",
                "url": it.get("attchmntFile", ""),
            })
    if hits:
        hits_with_date = [h for h in hits if h["filed_date"]]
        match = min(hits_with_date, key=lambda x: x["filed_date"]) if hits_with_date else hits[0]
        return (True, match, hits)
    return (True, None, [])


# ── Main entry point ──

def verify_obligation(symbol: str, mandate_id: str, q_end: date, due_dt: date) -> Dict[str, Any]:
    """Cross-check whether a compliance obligation was actually filed.

    Returns:
      {
        'status': 'submitted' | 'delayed' | 'missed' | 'unverified',
        'filed_date': date or None,
        'delay_days': int,
        'evidence': [ { source, responded, match, ... }, ... ],
        'confidence': 'high' | 'medium' | 'low',
      }
    """
    verifiers = []

    # 1. Authoritative mandate-specific
    if mandate_id in INTEGRATED_FILING_TYPES:
        verifiers.append(("nse_integrated_filing", lambda: _verify_integrated_filing(symbol, mandate_id, q_end)))
    if mandate_id == "LODR-REG27":
        verifiers.append(("nse_cg_master", lambda: _verify_cg_master(symbol, q_end)))
    if mandate_id == "LODR-REG31":
        verifiers.append(("nse_shp_master", lambda: _verify_shp_master(symbol, q_end)))

    # 2. BSE cross-exchange
    verifiers.append(("bse_announcements", lambda: _verify_bse_announcements(symbol, mandate_id, q_end, due_dt)))

    # 3. NSE announcements fallback
    verifiers.append(("nse_announcements_ranged", lambda: _verify_nse_announcements_ranged(symbol, mandate_id, q_end, due_dt)))

    evidence = []
    matches = []
    sources_responded = 0

    for name, fn in verifiers:
        try:
            responded, match, hits = fn()
        except Exception as e:
            print(f"[VERIFY] {name} crashed for {symbol}/{mandate_id}: {e}")
            responded, match, hits = False, None, []

        evidence.append({
            "source": name,
            "responded": responded,
            "hit_count": len(hits),
            "match": (
                {
                    "filed_date": match["filed_date"].isoformat() if match and match.get("filed_date") else None,
                    "title": match.get("title") if match else None,
                    "url": match.get("url") if match else None,
                }
                if match else None
            ),
        })
        if responded:
            sources_responded += 1
        if match:
            matches.append((name, match))
        time.sleep(0.4)  # rate-limit: be polite

    authoritative_names = {"nse_integrated_filing", "nse_cg_master", "nse_shp_master"}
    if matches and not any(n in authoritative_names for n, _ in matches):
        return {
            "status": "unverified",
            "filed_date": None,
            "delay_days": 0,
            "evidence": evidence,
            "confidence": "low",
        }
    matches = [(n, m) for n, m in matches if n in authoritative_names]

    if matches:
        # Pick earliest filed_date across the authoritative matches
        dated = [(n, m) for n, m in matches if m.get("filed_date")]
        if dated:
            _, best = min(dated, key=lambda x: x[1]["filed_date"])
        else:
            _, best = matches[0]
        filed_dt = best.get("filed_date")
        if filed_dt:
            delay = (filed_dt - due_dt).days
            status = "delayed" if delay > 0 else "submitted"
            return {
                "status": status,
                "filed_date": filed_dt.isoformat(),
                "delay_days": max(0, delay),
                "evidence": evidence,
                "confidence": "high",
            }
        return {
            "status": "submitted",
            "filed_date": None,
            "delay_days": 0,
            "evidence": evidence,
            "confidence": "medium",
        }

    # No matches anywhere. Was the check reliable?
    # Require: an authoritative source and at least one other responded.
    authoritative_responded = sum(
        1 for e in evidence if e["source"] in authoritative_names and e["responded"]
    )

    if authoritative_responded >= 1 and sources_responded >= 2:
        return {
            "status": "missed",
            "filed_date": None,
            "delay_days": 0,
            "evidence": evidence,
            "confidence": "high",
        }

    return {
        "status": "unverified",
        "filed_date": None,
        "delay_days": 0,
        "evidence": evidence,
        "confidence": "low",
    }
