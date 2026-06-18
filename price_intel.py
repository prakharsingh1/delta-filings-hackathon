"""Deeper price-movement intelligence — news + Reddit + AI summary.

When a stock moves >3% the basic "could be due to broader market trends"
explanation isn't useful. This module pulls signals from independent free
sources and produces a structured, citation-backed summary:

  - Google News RSS — free, no auth, returns last ~30 articles per query.
  - Reddit search JSON — free, no auth, surfaces retail-investor chatter.
  - Recent filings (already cached in our DB)
  - Gemini summary that ties them together (graceful fallback if no key)

The expensive parts (Google News + Reddit + Gemini) are cached for 15
minutes per symbol — so successive opens of the same stock's detail
modal are instant and we don't hammer external services.
"""

import os
import re
import json
import time
import html
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# In-memory TTL cache. Key: symbol → (timestamp, payload). 15 minutes is
# long enough to keep external traffic low, short enough that fresh news
# during an actively-moving session still surfaces quickly.
_INTEL_CACHE: Dict[str, Tuple[float, Dict]] = {}
_INTEL_TTL_SEC = 15 * 60

# Ratings get their own much longer cache. Yahoo aggressively rate-limits
# cloud IPs (HTTP 429), so a successful analyst/technical fetch is precious:
# keep it for 24h and serve it (marked stale) whenever a re-fetch fails.
# Technical signals are computed from daily closes — they don't meaningfully
# change intra-day, so a day-old rating is far better than "not available".
_RATING_CACHE: Dict[str, Tuple[float, Dict]] = {}
_RATING_TTL_SEC = 24 * 3600


def _yget(path: str, params: Dict, cookies=None, tries: int = 3):
    """GET a Yahoo Finance API path with host rotation + backoff.
    Yahoo 429s individual hosts; query1/query2 are equivalent mirrors."""
    import time as _t
    import random as _rand
    hosts = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
    last = None
    for attempt in range(tries):
        host = hosts[attempt % len(hosts)]
        try:
            r = requests.get(f"https://{host}{path}", params=params,
                             cookies=cookies,
                             headers={"User-Agent": _USER_AGENT}, timeout=10)
            if r.status_code == 200:
                return r
            last = r.status_code
            if r.status_code != 429:
                return r          # non-retryable failure — let caller decide
        except Exception as e:    # noqa: BLE001
            last = str(e)
        _t.sleep(1.0 + attempt * 1.5 + _rand.random())
    print(f"[INTEL] Yahoo gave up after {tries} tries ({last}) for {path}")
    return None


# ── Google News RSS ──

def _gnews_rss(query: str, max_items: int, scope: str,
               locale: str = "hl=en-IN&gl=IN&ceid=IN:en") -> List[Dict]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&{locale}"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": _USER_AGENT})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
    except Exception as e:
        print(f"[INTEL] gnews fetch failed ({scope}): {e}")
        return []

    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source = ""
        if " - " in title:
            head, _, src = title.rpartition(" - ")
            if len(src) < 60:
                title = head.strip()
                source = src.strip()
        items.append({
            "title": html.unescape(title),
            "url": link,
            "published": pub_date,
            "source": source,
            "scope": scope,        # 'company' | 'sector' | 'macro' | 'global'
        })
        if len(items) >= max_items:
            break
    return items


# Sector-name normalization: NSE returns long industry strings, but news
# searches work better with shorter colloquial sector terms.
_SECTOR_QUERIES = {
    "personal care": "FMCG",
    "household & personal products": "FMCG",
    "food, beverages & tobacco": "FMCG",
    "fmcg": "FMCG",
    "automobile": "auto",
    "auto components": "auto",
    "banks": "banking",
    "private sector bank": "banking",
    "public sector bank": "banking",
    "financial services": "financial services",
    "insurance": "insurance",
    "pharmaceuticals & biotechnology": "pharma",
    "healthcare": "pharma",
    "it - software": "IT",
    "it services": "IT",
    "cement": "cement",
    "metals & mining": "metals",
    "steel": "steel",
    "iron & steel": "steel",
    "oil & gas": "oil and gas",
    "power": "power",
    "telecom": "telecom",
    "real estate": "real estate",
    "consumer durables": "consumer durables",
    "chemicals": "chemicals",
    "fertilisers": "fertiliser",
}


def _sector_query_term(industry: str) -> str:
    if not industry:
        return ""
    low = industry.strip().lower()
    for k, v in _SECTOR_QUERIES.items():
        if k in low:
            return v
    # Fallback: take the first 2 words of the raw industry string
    return " ".join(industry.split()[:2])


def fetch_news(symbol: str, company_name: str,
               industry: str = "", direction: str = "",
               max_company: int = 6, max_sector: int = 4,
               max_macro: int = 3, max_global: int = 2) -> List[Dict]:
    """Return news from THREE concentric scopes — company-specific, sector,
    and macro/global — so the AI can attribute the move to the right driver.

    - **company**: precise headlines naming the company
    - **sector**: ribbon-level news about the industry (e.g. "Indian FMCG stocks")
    - **macro**: India-wide market mood (e.g. "Nifty 50 fall today")
    - **global**: world events that bleed into Indian equities (Fed, crude, China)

    Each item carries a `scope` so the prompt can tell the model which level
    of attribution the headline supports."""
    name = (company_name or "").strip()
    if not name and not symbol:
        return []

    out: List[Dict] = []
    seen_urls = set()

    # 1. Company-specific
    parts = []
    if name:
        parts.append(f'"{name}"')
    if symbol and symbol.upper() not in (name or "").upper():
        parts.append(symbol)
    company_q = " OR ".join(parts) + " stock"
    for it in _gnews_rss(company_q, max_company, "company"):
        if it["url"] in seen_urls:
            continue
        seen_urls.add(it["url"])
        out.append(it)

    # 2. Sector
    sector_term = _sector_query_term(industry)
    if sector_term:
        sector_q = f'Indian {sector_term} stocks'
        for it in _gnews_rss(sector_q, max_sector, "sector"):
            if it["url"] in seen_urls:
                continue
            seen_urls.add(it["url"])
            out.append(it)

    # 3. India macro / market mood — bias toward the move direction so we
    #    catch articles describing a broader rally/decline that day.
    macro_q = "Nifty 50 today"
    if direction == "up":
        macro_q = "Indian stock market rally today"
    elif direction == "down":
        macro_q = "Indian stock market fall today"
    for it in _gnews_rss(macro_q, max_macro, "macro"):
        if it["url"] in seen_urls:
            continue
        seen_urls.add(it["url"])
        out.append(it)

    # 4. Global signals that frequently move Indian equities — Fed, crude,
    #    USD/INR, China. We don't tailor to sector here (kept broad on
    #    purpose so Gemini can spot cross-asset spillover).
    global_q = "Fed rate crude oil USD INR today"
    for it in _gnews_rss(global_q, max_global, "global"):
        if it["url"] in seen_urls:
            continue
        seen_urls.add(it["url"])
        out.append(it)

    return out


# ── Reddit JSON search ──

def fetch_reddit_mentions(symbol: str, company_name: str,
                          max_items: int = 6) -> List[Dict]:
    """Search Reddit's public JSON endpoint for recent posts about the
    stock across the major Indian-investor subs. Anonymous, no auth.

    `t=week` keeps results fresh; the search is OR'd between the symbol
    and the trimmed company name root so we don't miss tickers that
    don't appear by name (and vice versa)."""
    name_root = re.sub(r"\b(limited|ltd\.?|pvt\.?|private)\b", "",
                       (company_name or ""), flags=re.IGNORECASE).strip()
    name_root = re.sub(r"\s+", " ", name_root)
    query_parts = [p for p in [symbol, f'"{name_root}"' if name_root else None] if p]
    if not query_parts:
        return []
    query = " OR ".join(query_parts)
    subreddits = "IndianStockMarket+IndiaInvestments+IndianStreetBets+StockMarketIndia"
    url = (
        f"https://www.reddit.com/r/{subreddits}/search.json"
        f"?q={quote_plus(query)}&sort=new&t=week&restrict_sr=on&limit={max_items}"
    )
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": _USER_AGENT})
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception as e:
        print(f"[INTEL] reddit fetch failed for {symbol}: {e}")
        return []

    out = []
    for child in (data.get("data", {}).get("children") or [])[:max_items]:
        d = child.get("data") or {}
        out.append({
            "title": d.get("title", ""),
            "url": "https://www.reddit.com" + d.get("permalink", ""),
            "subreddit": d.get("subreddit", ""),
            "score": d.get("score", 0),
            "num_comments": d.get("num_comments", 0),
            "created": d.get("created_utc", 0),
        })
    return out


# ── Analyst consensus + technical signal ──

def _num(v):
    """Yahoo quoteSummary returns numbers as {'raw':..,'fmt':..} or bare."""
    if isinstance(v, dict):
        v = v.get("raw")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Process-wide Yahoo cookie/crumb cache (the crumb endpoint is rate-limited,
# so we reuse one crumb for ~30 minutes across all symbols).
_YH = {"crumb": None, "cookies": None, "ts": 0.0}


def _yahoo_session():
    now = time.time()
    if _YH["crumb"] and (now - _YH["ts"]) < 1800:
        return _YH["cookies"], _YH["crumb"]
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": _USER_AGENT})
        s.get("https://query1.finance.yahoo.com/v8/finance/chart/INFY.NS", timeout=8)
        crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=8).text.strip()
        if crumb and "Too Many" not in crumb and "<" not in crumb and len(crumb) < 40:
            _YH.update({"crumb": crumb, "cookies": s.cookies, "ts": now})
            return s.cookies, crumb
    except Exception as e:
        print(f"[INTEL] yahoo crumb failed: {e}")
    return None, None


def _fetch_analyst_tradingview(symbol: str) -> Optional[Dict]:
    """Analyst consensus from TradingView's public scanner API — reliable
    from cloud IPs where Yahoo 429s. Same 1–5 mean scale as Yahoo.
    TradingView's buy/hold/sell counts exclude the strong-buy/strong-sell
    buckets, so the remainder vs total is attributed to the strong side
    that matches the mean (approximate, only affects the distribution bar)."""
    try:
        r = requests.post(
            "https://scanner.tradingview.com/india/scan",
            json={"symbols": {"tickers": [f"NSE:{symbol}"]},
                  "columns": ["close", "recommendation_mark", "price_target_average",
                              "price_target_high", "price_target_low",
                              "recommendation_total", "recommendation_buy",
                              "recommendation_hold", "recommendation_sell"]},
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        rows = (r.json() or {}).get("data") or []
        if not rows:
            return None
        d = rows[0]["d"]
        close, mean, t_avg, t_hi, t_lo, n, buy, hold, sell = (list(d) + [None] * 9)[:9]
        if not n or mean is None:
            return None
        buy, hold, sell = int(buy or 0), int(hold or 0), int(sell or 0)
        remainder = max(0, int(n) - (buy + hold + sell))
        strong_buy = remainder if mean <= 3 else 0
        strong_sell = remainder if mean > 3 else 0
        print(f"[INTEL] analyst consensus via TradingView for {symbol} ({int(n)} analysts)")
        return {
            "mean": float(mean),
            "key": None,
            "count": int(n),
            "current": float(close) if close else None,
            "target_mean": float(t_avg) if t_avg else None,
            "target_high": float(t_hi) if t_hi else None,
            "target_low": float(t_lo) if t_lo else None,
            "via": "tradingview",
            "dist": {
                "strongBuy": strong_buy,
                "buy": buy,
                "hold": hold,
                "sell": sell,
                "strongSell": strong_sell,
            },
        }
    except Exception as e:
        print(f"[INTEL] TradingView analyst fetch failed for {symbol}: {e}")
        return None


def fetch_analyst(symbol: str) -> Optional[Dict]:
    """Analyst consensus: Yahoo first (richest data), TradingView's public
    scanner as the fallback — it serves NSE analyst data reliably from
    cloud IPs where Yahoo rate-limits. None only if both fail."""
    y = _fetch_analyst_yahoo(symbol)
    if y:
        y["via"] = "yahoo"
        return y
    return _fetch_analyst_tradingview(symbol)


def _fetch_analyst_yahoo(symbol: str) -> Optional[Dict]:
    """Yahoo analyst consensus for <symbol>.NS: mean recommendation, count,
    target prices, and the buy/hold/sell distribution. None if unavailable
    (no coverage, rate-limited, or crumb failure)."""
    cookies, crumb = _yahoo_session()
    if not crumb:
        return None
    try:
        r = _yget(f"/v10/finance/quoteSummary/{symbol}.NS",
                  {"modules": "financialData,recommendationTrend", "crumb": crumb},
                  cookies=cookies)
        if r is None or r.status_code != 200:
            return None
        res = (r.json().get("finance") or {}).get("result")
        if not res:
            return None
        fd = res[0].get("financialData") or {}
        trend = (res[0].get("recommendationTrend") or {}).get("trend") or []
        n = _num(fd.get("numberOfAnalystOpinions"))
        mean = _num(fd.get("recommendationMean"))
        if not n or not mean:
            return None
        t0 = trend[0] if trend else {}
        return {
            "mean": mean,
            "key": fd.get("recommendationKey"),
            "count": int(n),
            "current": _num(fd.get("currentPrice")),
            "target_mean": _num(fd.get("targetMeanPrice")),
            "target_high": _num(fd.get("targetHighPrice")),
            "target_low": _num(fd.get("targetLowPrice")),
            "dist": {
                "strongBuy": int(t0.get("strongBuy", 0) or 0),
                "buy": int(t0.get("buy", 0) or 0),
                "hold": int(t0.get("hold", 0) or 0),
                "sell": int(t0.get("sell", 0) or 0),
                "strongSell": int(t0.get("strongSell", 0) or 0),
            },
        }
    except Exception as e:
        print(f"[INTEL] analyst fetch failed for {symbol}: {e}")
        return None


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(-period, 0):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


def _yahoo_closes_1y(symbol: str) -> Optional[List[float]]:
    """1y of daily closes from Yahoo. None when rate-limited/unavailable."""
    try:
        r = _yget(f"/v8/finance/chart/{symbol}.NS",
                  {"interval": "1d", "range": "1y"})
        if r is None or r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        closes = [c for c in (res["indicators"]["quote"][0].get("close") or []) if c is not None]
        return closes if len(closes) >= 30 else None
    except Exception as e:
        print(f"[INTEL] yahoo closes failed for {symbol}: {e}")
        return None


def _bse_closes_1y(symbol: str) -> Optional[List[float]]:
    """1y of daily closes from BSE's StockReachGraph API (flag=12M →
    ~248 trading days, oldest→newest). BSE doesn't rate-limit cloud IPs
    the way Yahoo does, so this is the reliable fallback in production."""
    try:
        from compliance_verification import _resolve_bse_scrip
        scrip = _resolve_bse_scrip(symbol)
        if not scrip:
            return None
        r = requests.get(
            "https://api.bseindia.com/BseIndiaAPI/api/StockReachGraph/w",
            params={"scripcode": scrip, "flag": "12M",
                    "fromdate": "", "todate": "", "seriesid": ""},
            headers={"User-Agent": _USER_AGENT,
                     "Referer": "https://www.bseindia.com/"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        pts = json.loads((r.json() or {}).get("Data") or "[]")
        closes = []
        for p in pts:
            try:
                v = float(p.get("vale1") or 0)
                if v > 0:
                    closes.append(v)
            except (TypeError, ValueError):
                continue
        if len(closes) >= 30:
            print(f"[INTEL] technical closes via BSE for {symbol} ({len(closes)} days)")
            return closes
        return None
    except Exception as e:
        print(f"[INTEL] BSE closes failed for {symbol}: {e}")
        return None


def compute_technical_signal(symbol: str) -> Optional[Dict]:
    """A transparent 1–10 buy/sell signal computed from 1y of daily closes
    (trend vs 50/200-DMA, 1-month momentum, RSI, 52-week position).
    Sources closes from Yahoo first, then BSE as the fallback — Yahoo
    rate-limits cloud IPs, BSE doesn't."""
    closes = _yahoo_closes_1y(symbol) or _bse_closes_1y(symbol)
    if not closes:
        return None
    try:
        last = closes[-1]
        dma50 = sum(closes[-50:]) / min(50, len(closes))
        dma200 = sum(closes[-200:]) / min(200, len(closes)) if len(closes) >= 100 else None
        window = closes[-252:]
        hi, lo = max(window), min(window)
        pos52 = (last - lo) / (hi - lo) if hi > lo else 0.5
        mom = (last / closes[-21] - 1) if len(closes) > 21 else 0.0
        rsi = _rsi(closes, 14)

        score = 5.0
        score += 1.8 if last > dma50 else -1.3
        if dma200 is not None:
            score += 1.8 if last > dma200 else -1.3
        score += max(-1.5, min(1.5, mom * 10))
        if rsi is not None:
            if rsi > 75:
                score -= 0.8
            elif rsi < 30:
                score += 0.6
        score += (pos52 - 0.5) * 1.2
        score = max(1.0, min(10.0, score))

        return {
            "score": round(score, 1),
            "rsi": round(rsi, 1) if rsi is not None else None,
            "dma50": round(dma50, 1),
            "dma200": round(dma200, 1) if dma200 else None,
            "pos52": round(pos52 * 100),
            "mom1m": round(mom * 100, 1),
            "last": round(last, 1),
        }
    except Exception as e:
        print(f"[INTEL] technical signal failed for {symbol}: {e}")
        return None


def _lite_rating_from_quote(quote: Dict) -> Optional[Dict]:
    """Degraded 1-10 signal from a live quote alone (no historical closes):
    52-week position + day momentum. Used only when the full analyst +
    technical pipeline is unavailable, so the panel never goes blank for a
    stock whose live quote works."""
    try:
        last = float(quote.get("lastPrice") or 0)
        hi = float(quote.get("yearHigh") or 0)
        lo = float(quote.get("yearLow") or 0)
        pchg = float(quote.get("pChange") or 0)
        if not last or hi <= lo:
            return None
        pos52 = (last - lo) / (hi - lo)
        score = 5.0 + (pos52 - 0.5) * 4.0 + max(-1.0, min(1.0, pchg / 3.0))
        score = max(1.0, min(10.0, round(score, 1)))
        return {
            "score": score,
            "label": _score_label(score),
            "source": "lite",
            "analyst": None,
            "technical": {
                "pos52": round(pos52 * 100),
                "last": round(last, 1),
                "rsi": None, "dma50": None, "dma200": None,
                "mom1m": None,
            },
            "upside_pct": None,
        }
    except Exception:
        return None


def _score_label(score: float) -> str:
    if score >= 8.5:
        return "Strong Buy"
    if score >= 7.0:
        return "Buy"
    if score >= 5.0:
        return "Hold"
    if score >= 3.5:
        return "Reduce"
    return "Sell"


def build_rating(symbol: str) -> Optional[Dict]:
    """Combine Yahoo analyst consensus (if available) with a computed
    technical signal into one rating object for the UI. Analyst consensus,
    when present, drives the headline 1–10 score; otherwise the technical
    signal does.

    A successful rating is cached for 24h. When a re-fetch fails (Yahoo
    429s cloud IPs aggressively), the last good rating is served instead,
    marked stale with its original timestamp. Returns None only when both
    sources fail AND no prior rating exists."""
    import time as _t
    from datetime import datetime as _dt

    now = _t.time()
    cached = _RATING_CACHE.get(symbol)
    if cached and (now - cached[0]) < _RATING_TTL_SEC:
        return cached[1]

    analyst = fetch_analyst(symbol)
    technical = compute_technical_signal(symbol)
    if not analyst and not technical:
        # Total failure — serve the last good rating (even past TTL) marked stale.
        if cached:
            stale = {**cached[1], "stale": True,
                     "as_of": _dt.fromtimestamp(cached[0]).strftime("%d %b, %H:%M")}
            print(f"[INTEL] rating fetch failed for {symbol} — serving stale ({stale['as_of']})")
            return stale
        return None

    if analyst and analyst.get("mean"):
        # Yahoo mean: 1 = Strong Buy … 5 = Sell → map to a 1–10 buy score.
        analyst_score = round((5 - analyst["mean"]) / 4 * 9 + 1, 1)
        analyst_score = max(1.0, min(10.0, analyst_score))
        primary = analyst_score
        source = "analyst"
    else:
        primary = technical["score"] if technical else None
        source = "technical"

    upside = None
    if analyst and analyst.get("target_mean") and analyst.get("current"):
        upside = round((analyst["target_mean"] / analyst["current"] - 1) * 100, 1)

    rating = {
        "score": primary,
        "label": _score_label(primary) if primary is not None else None,
        "source": source,
        "analyst": analyst,
        "technical": technical,
        "upside_pct": upside,
    }
    _RATING_CACHE[symbol] = (now, rating)
    return rating


# ── AI summary (Gemini if configured, heuristic otherwise) ──

# ── Deep research: read the actual articles ──
# Google News RSS links are un-resolvable JS redirects, so headline research
# uses Bing News RSS (direct publisher URLs behind a decodable apiclick
# redirect). Articles are fetched in parallel and their text extracted with
# BeautifulSoup; excerpts feed the AI so it explains WHY, not just THAT.

_CAUSE_WORDS = (
    "why", "surge", "jump", "fall", "decline", "drop", "rally", "soar", "slump",
    "result", "earnings", "q1", "q2", "q3", "q4", "profit", "loss", "margin",
    "order", "deal", "acquisition", "stake", "block deal", "downgrade",
    "upgrade", "target", "brokerage", "capex", "guidance", "dividend",
    "buyback", "losers", "gainers",
)


def _bing_news(query: str, max_items: int = 8) -> List[Dict]:
    """Bing News RSS — returns items with DIRECT publisher URLs."""
    from urllib.parse import urlparse, parse_qs, unquote
    try:
        r = requests.get("https://www.bing.com/news/search",
                         params={"q": query, "format": "rss"},
                         headers={"User-Agent": _USER_AGENT}, timeout=12)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
    except Exception as e:
        print(f"[INTEL] bing news failed: {e}")
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not title or not link:
            continue
        # Unwrap bing.com/news/apiclick.aspx?...&url=<real>&...
        if "apiclick" in link:
            qs = parse_qs(urlparse(link).query)
            real = (qs.get("url") or [""])[0]
            if real:
                link = unquote(real)
        if "bing.com" in link:
            continue          # couldn't unwrap — skip
        try:
            source = urlparse(link).netloc.replace("www.", "")
        except Exception:
            source = ""
        out.append({"title": title, "url": link, "source": source,
                    "published": pub, "scope": "company"})
        if len(out) >= max_items:
            break
    return out


def _fetch_article_text(url: str, char_cap: int = 1400) -> Optional[str]:
    """Fetch a news article and extract its main text. Returns None for
    paywalls / consent walls / non-articles (too little extractable text)."""
    try:
        from bs4 import BeautifulSoup
        r = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=8)
        if r.status_code != 200 or not r.text:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        # Keep substantive paragraphs only (skip nav crumbs / disclaimers).
        paras = [p for p in paras if len(p) > 60]
        text = " ".join(paras)
        if len(text) < 200:
            return None
        return text[:char_cap]
    except Exception:
        return None


def _research_articles(symbol: str, company_name: str,
                       max_articles: int = 4) -> List[Dict]:
    """Find the most causally-relevant recent articles about the company and
    READ them: returns [{title, url, source, excerpt}] with extracted text.
    Time-boxed via parallel fetches so the modal stays responsive."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    name = (company_name or symbol or "").strip()
    headlines = _bing_news(f"{name} stock", 10)
    if symbol and symbol.upper() != name.upper():
        headlines += _bing_news(f"{symbol} share price", 6)

    # Dedupe by URL, rank by causal signal in the title.
    seen, ranked = set(), []
    for h in headlines:
        if h["url"] in seen:
            continue
        seen.add(h["url"])
        t = h["title"].lower()
        score = sum(2 for w in _CAUSE_WORDS if w in t)
        if symbol.lower() in t or (name and name.lower().split()[0] in t):
            score += 3
        ranked.append((score, h))
    ranked.sort(key=lambda x: -x[0])
    candidates = [h for s, h in ranked if s > 0][:max_articles * 2]

    results = []
    if not candidates:
        return results
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_fetch_article_text, h["url"]): h for h in candidates}
        for fut in as_completed(futs, timeout=15):
            h = futs[fut]
            try:
                text = fut.result()
            except Exception:
                text = None
            if text:
                results.append({**h, "excerpt": text})
            if len(results) >= max_articles:
                break
    print(f"[INTEL] research: read {len(results)}/{len(candidates)} articles for {symbol}")
    return results


def _heuristic_summary(quote: Dict, news: List[Dict],
                       filings: List[Dict], reddit: List[Dict]) -> str:
    pc = float(quote.get("pChange") or 0)
    direction = "rallied" if pc > 0 else "declined"
    parts = [f"{quote.get('symbol','')} {direction} {abs(pc):.2f}% today."]

    # Look for high-signal filing keywords
    filing_themes = []
    for f in (filings or [])[:5]:
        t = (f.get("title", "") or "").lower()
        if "result" in t or "earning" in t:
            filing_themes.append("earnings")
        elif "dividend" in t or "buyback" in t:
            filing_themes.append("capital return announcement")
        elif "acquisition" in t or "merger" in t or "demerger" in t:
            filing_themes.append("M&A activity")
        elif "rating" in t:
            filing_themes.append("credit rating action")
    if filing_themes:
        parts.append("Recent filings reference " + ", ".join(sorted(set(filing_themes))) + ".")

    if news:
        parts.append(f"{len(news)} news articles published in the last few days.")
    if reddit:
        parts.append(f"{len(reddit)} retail-investor posts on Reddit this week.")

    if not (filing_themes or news or reddit):
        if abs(pc) >= 5:
            parts.append("No company-specific catalyst found — likely sector rotation or broader market sentiment.")
        else:
            parts.append("No clear company-specific trigger detected in public sources.")
    return " ".join(parts)


# Free-tier–safe Gemini models. We force one of these regardless of what
# the user has selected for filing summaries — the intel summary is small
# and shouldn't burn paid Pro tokens.
_FREE_TIER_MODELS = {"gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"}
_DEFAULT_INTEL_MODEL = "gemini-2.5-flash"


def _build_why_prompt(quote: Dict, news: List[Dict], filings: List[Dict],
                      reddit: List[Dict], research: List[Dict]) -> str:
    """Build the 'why did this move' evidence prompt, including FULL ARTICLE
    EXCERPTS from the deep-research pass so the model reasons from article
    content rather than guessing off headlines."""
    pc = float(quote.get("pChange") or 0)
    direction = "rose" if pc > 0 else "fell"
    today = datetime.now().strftime("%d %b %Y")
    sym = quote.get("symbol", "")
    company = quote.get("companyName", "")
    industry = quote.get("industry", "")

    parts = [
        f"DATE: {today}",
        f"STOCK: {sym} ({company})",
        f"SECTOR: {industry or 'unknown'}",
        f"MOVE: {direction} {abs(pc):.2f}% today, last ₹{quote.get('lastPrice', 0):,.2f} "
        f"(prev close ₹{quote.get('previousClose', 0):,.2f}, "
        f"day range ₹{quote.get('dayLow', 0):,.2f}–₹{quote.get('dayHigh', 0):,.2f})",
    ]

    # Group news by scope so Gemini can attribute correctly.
    by_scope: Dict[str, List[Dict]] = {"company": [], "sector": [], "macro": [], "global": []}
    for n in news or []:
        by_scope.setdefault(n.get("scope", "company"), []).append(n)

    if by_scope.get("company"):
        parts.append("\nCOMPANY-SPECIFIC NEWS (most likely cause if relevant):")
        for n in by_scope["company"][:6]:
            parts.append(f"  - {n['title']} ({n.get('source') or 'news'})")
    if by_scope.get("sector"):
        parts.append(f"\nSECTOR NEWS ({industry or 'sector'}):")
        for n in by_scope["sector"][:4]:
            parts.append(f"  - {n['title']} ({n.get('source') or 'news'})")
    if by_scope.get("macro"):
        parts.append("\nINDIA MACRO / MARKET MOOD:")
        for n in by_scope["macro"][:3]:
            parts.append(f"  - {n['title']} ({n.get('source') or 'news'})")
    if by_scope.get("global"):
        parts.append("\nGLOBAL SIGNALS (Fed, crude, USD/INR, China):")
        for n in by_scope["global"][:3]:
            parts.append(f"  - {n['title']} ({n.get('source') or 'news'})")

    if filings:
        parts.append("\nRECENT CORPORATE FILINGS (last 30 days):")
        for f in filings[:5]:
            parts.append(f"  - {f.get('title','')} ({f.get('date','')})")
    if reddit:
        parts.append("\nREDDIT CHATTER (Indian retail subs, last 7 days):")
        for p in reddit[:4]:
            parts.append(f"  - {p['title']} (r/{p['subreddit']}, {p['score']} upvotes)")

    if research:
        parts.append("\nFULL ARTICLE EXCERPTS (read carefully — these contain the actual reasons):")
        for i, a in enumerate(research, 1):
            parts.append(f"\n  ARTICLE {i}: {a['title']} — {a.get('source','')}")
            parts.append(f"  {a['excerpt']}")

    return (
        "You are a senior India equity research analyst writing a one-paragraph "
        "intraday note for an investor. In 3–4 crisp sentences, explain WHY "
        f"{sym} {direction} today. Attribute the move across three layers and "
        "be explicit about which dominates:\n"
        "  1. COMPANY-SPECIFIC — earnings, board change, M&A, rating, large filing\n"
        "  2. SECTOR — peers moving on sector news, regulation, demand shock\n"
        "  3. MACRO/GLOBAL — Nifty drift, FII flows, Fed/crude/USD-INR, China\n\n"
        "Rules:\n"
        " - The FULL ARTICLE EXCERPTS are your primary evidence — quote the specific "
        "driver they identify (e.g. 'Q4 results beat estimates', 'brokerage downgrade', "
        "'block deal'), and name the source.\n"
        " - Note article dates: an article from months ago explains the trend, not today's move.\n"
        " - If evidence is thin, say so plainly: 'No clear company-specific catalyst — "
        "appears to track the broader Nifty move' is a valid answer.\n"
        " - Distinguish 'caused by' (a specific event) from 'consistent with' (sector drift).\n"
        " - Plain prose, no bullets, no preamble, no 'not financial advice', no emojis.\n\n"
        "EVIDENCE:\n" + "\n".join(parts)
    )


def _gemini_why(prompt: str, api_key: str,
                model: str = _DEFAULT_INTEL_MODEL) -> Optional[str]:
    """Run the why-prompt through Gemini (free-tier models only)."""
    try:
        from google import genai
    except Exception as e:
        print(f"[INTEL] genai import failed: {e}")
        return None
    if model not in _FREE_TIER_MODELS:
        model = _DEFAULT_INTEL_MODEL
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[INTEL] gemini client init failed: {e}")
        return None

    # Try the requested model first, then a free-tier fallback if it's
    # overloaded (Gemini frequently returns 503 UNAVAILABLE on flash).
    model_chain = [model]
    for fallback in ("gemini-2.0-flash", "gemini-2.5-flash-lite"):
        if fallback != model and fallback not in model_chain:
            model_chain.append(fallback)

    last_error = None
    for m in model_chain:
        for attempt in range(2):
            try:
                # `thinking_config.thinking_budget=0` disables 2.5-flash's
                # internal reasoning trace which would otherwise eat our
                # output budget and truncate the user-visible summary.
                resp = client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config={
                        "max_output_tokens": 600,
                        "temperature": 0.25,
                        "thinking_config": {"thinking_budget": 0},
                    },
                )
                text = (resp.text or "").strip()
                if text:
                    return text
            except Exception as e:
                last_error = e
                msg = str(e)
                # 503 / UNAVAILABLE / 429 are retryable; 4xx (auth/quota
                # exhausted) usually aren't, so break to the next model.
                if "503" in msg or "UNAVAILABLE" in msg or "overloaded" in msg.lower():
                    if attempt == 0:
                        time.sleep(1.5)
                        continue
                print(f"[INTEL] gemini {m} failed (attempt {attempt+1}): {msg[:160]}")
                break
    if last_error:
        print(f"[INTEL] gemini exhausted all models: {last_error}")
    return None


_REASONING_LEAK_STARTS = (
    "we need to", "we must", "let's", "lets ", "the user wants", "the user asked",
    "i need to", "okay, ", "ok, so", "first, i", "thinking:", "to answer this",
    "we have to write", "we should",
)


def _clean_llm_output(text: str) -> Optional[str]:
    """Strip <think> blocks and reject chain-of-thought leaks. Reasoning
    models on the free router sometimes emit their thinking as the answer
    ('We need to write a one-paragraph note…'); that must never reach the
    user. Returns cleaned text, or None if the output is a leaked trace."""
    if not text:
        return None
    # Remove explicit think/reasoning blocks if the model wrapped them.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = text.strip()
    low = text.lower()
    if any(low.startswith(p) for p in _REASONING_LEAK_STARTS):
        return None
    # Mid-text meta-reasoning markers ⇒ the whole thing is a trace.
    if "the user wants" in low or "we need to write" in low or "let's craft" in low:
        return None
    return text or None


def _openrouter_why(prompt: str, api_key: str) -> Optional[str]:
    """Run the why-prompt through OpenRouter's free router, with a quality
    gate (the router can land on specialty/reasoning models that return
    junk or leak their thinking) and a pinned free model as the final
    attempt."""
    for model in ("openrouter/free", "openrouter/free", "google/gemma-4-31b-it:free"):
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json",
                         "HTTP-Referer": "https://delta-filings.up.railway.app",
                         "X-Title": "Delta Filings"},
                json={"model": model, "temperature": 0.25, "max_tokens": 900,
                      # Keep reasoning internal — never in the content field.
                      "reasoning": {"exclude": True},
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=45,
            )
            data = r.json()
            if "choices" not in data:
                continue
            raw = (data["choices"][0]["message"]["content"] or "").strip()
            text = _clean_llm_output(raw)
            routed = data.get("model") or model
            if text is None:
                print(f"[INTEL] reasoning leak from {routed} — retrying")
                continue
            # Quality gate: a real why-note is a paragraph, not a verdict line,
            # and must end on a finished sentence (truncation guard).
            if len(text) >= 120 and text.rstrip()[-1] in ".!?\"'":
                print(f"[INTEL] why-summary via OpenRouter ({routed})")
                return text
            print(f"[INTEL] low-quality output from {routed} ({len(text)} chars) — retrying")
        except Exception as e:
            print(f"[INTEL] openrouter why failed ({model}): {e}")
    return None


def _ai_why_summary(quote: Dict, news: List[Dict], filings: List[Dict],
                    reddit: List[Dict], research: List[Dict],
                    api_key: str, model: str) -> Optional[str]:
    """Provider-aware 'why did this move' summary: OpenRouter for sk-or-…
    keys, Gemini otherwise. Both read the same deep-research prompt."""
    prompt = _build_why_prompt(quote, news, filings, reddit, research)
    if api_key.startswith("sk-or-"):
        return _openrouter_why(prompt, api_key)
    return _gemini_why(prompt, api_key, model)


def _research_summary(quote: Dict, research: List[Dict],
                      news: List[Dict], filings: List[Dict]) -> str:
    """No-AI fallback that still answers WHY: cite the most causally-relevant
    article we actually read, with its key sentence — instead of just
    counting headlines."""
    pc = float(quote.get("pChange") or 0)
    direction = "rallied" if pc > 0 else "declined"
    sym = quote.get("symbol", "")
    parts = [f"{sym} {direction} {abs(pc):.2f}% today."]

    if research:
        top = research[0]
        # First sentence of the excerpt usually carries the lede.
        first_sent = re.split(r"(?<=[.!?])\s+", top["excerpt"].strip())[0][:220]
        parts.append(f'Likely driver per {top.get("source") or "press"}: '
                     f'"{top["title"]}" — {first_sent}')
        if len(research) > 1:
            parts.append(f'Also reported: "{research[1]["title"]}" ({research[1].get("source","")}).')
    else:
        return _heuristic_summary(quote, news, filings, [])
    return " ".join(parts)


# ── Public orchestrator ──

def get_price_intel(symbol: str, company_name: str, quote: Dict,
                    filings: List[Dict], gemini_key: str = "",
                    gemini_model: str = _DEFAULT_INTEL_MODEL) -> Dict:
    """Build the full deep-analysis payload for one stock. Cached per
    symbol for 15 minutes so back-to-back opens are instant.

    Always forces a free-tier Gemini model regardless of the caller's
    choice — intel summaries are cheap and shouldn't burn Pro quota."""
    sym = (symbol or "").upper()
    now = time.time()
    cached = _INTEL_CACHE.get(sym)
    if cached and (now - cached[0]) < _INTEL_TTL_SEC:
        return cached[1]

    pc = float(quote.get("pChange") or 0)
    direction = "up" if pc > 0 else "down"
    industry = quote.get("industry", "") or ""

    news = fetch_news(sym, company_name, industry=industry, direction=direction)
    # Reddit's public search.json now blocks unauthenticated/datacenter
    # requests (HTTP 403), so it never returns data in production — skip it.
    reddit = []
    rating = build_rating(sym)
    if rating is None:
        # Last resort: a degraded "lite" technical reading from the quote we
        # already hold (works whenever the prices grid works). Not cached —
        # the full rating replaces it as soon as a Yahoo fetch succeeds.
        rating = _lite_rating_from_quote(quote)
    relevant_filings = [
        {"title": f.get("title", ""), "date": f.get("date", ""),
         "tags": f.get("tags", "")}
        for f in (filings or [])[:6]
    ]

    # Deep research: find the most causally-relevant articles and READ them.
    research = _research_articles(sym, company_name)

    safe_model = gemini_model if gemini_model in _FREE_TIER_MODELS else _DEFAULT_INTEL_MODEL

    ai_summary = None
    if gemini_key:
        ai_summary = _ai_why_summary(quote, news, relevant_filings, reddit,
                                     research, gemini_key, safe_model)
    summary = ai_summary or _research_summary(quote, research, news, relevant_filings)

    payload = {
        "symbol": sym,
        "summary": summary,
        "ai_powered": bool(ai_summary),
        "model_used": ("openrouter" if (ai_summary and gemini_key.startswith("sk-or-"))
                       else safe_model if ai_summary else "heuristic"),
        "news": news,
        "reddit": reddit,
        "rating": rating,
        "filings": relevant_filings,
        "research": [{"title": a["title"], "url": a["url"], "source": a.get("source", "")}
                     for a in research],
        "fetched_at": datetime.now().isoformat(),
    }
    _INTEL_CACHE[sym] = (now, payload)
    return payload
