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


def fetch_analyst(symbol: str) -> Optional[Dict]:
    """Yahoo analyst consensus for <symbol>.NS: mean recommendation, count,
    target prices, and the buy/hold/sell distribution. None if unavailable
    (no coverage, rate-limited, or crumb failure)."""
    cookies, crumb = _yahoo_session()
    if not crumb:
        return None
    try:
        url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}.NS"
               f"?modules=financialData,recommendationTrend&crumb={quote_plus(crumb)}")
        r = requests.get(url, cookies=cookies, headers={"User-Agent": _USER_AGENT}, timeout=10)
        if r.status_code != 200:
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


def compute_technical_signal(symbol: str) -> Optional[Dict]:
    """A transparent 1–10 buy/sell signal computed from 1y of daily closes
    (trend vs 50/200-DMA, 1-month momentum, RSI, 52-week position). Uses the
    same Yahoo chart endpoint as live prices, so it works from cloud IPs."""
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS",
                         params={"interval": "1d", "range": "1y"},
                         headers={"User-Agent": _USER_AGENT}, timeout=10)
        if r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        closes = [c for c in (res["indicators"]["quote"][0].get("close") or []) if c is not None]
        if len(closes) < 30:
            return None
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
    signal does. Returns None only if both sources fail."""
    analyst = fetch_analyst(symbol)
    technical = compute_technical_signal(symbol)
    if not analyst and not technical:
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

    return {
        "score": primary,
        "label": _score_label(primary) if primary is not None else None,
        "source": source,
        "analyst": analyst,
        "technical": technical,
        "upside_pct": upside,
    }


# ── AI summary (Gemini if configured, heuristic otherwise) ──

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


def _gemini_summary(quote: Dict, news: List[Dict],
                    filings: List[Dict], reddit: List[Dict],
                    api_key: str, model: str = _DEFAULT_INTEL_MODEL) -> Optional[str]:
    """Ask Gemini for a nuanced 'why this moved' summary that attributes
    the move across three layers: company-specific → sector → macro/global.
    Forces a free-tier model. Returns None on any failure so callers fall
    back to the heuristic."""
    try:
        from google import genai
    except Exception as e:
        print(f"[INTEL] genai import failed: {e}")
        return None

    # Hard guard against accidentally using paid Pro models.
    if model not in _FREE_TIER_MODELS:
        model = _DEFAULT_INTEL_MODEL

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

    prompt = (
        "You are a senior India equity research analyst writing a one-paragraph "
        "intraday note for an investor. In 3–4 crisp sentences, explain WHY "
        f"{sym} {direction} today. Attribute the move across three layers and "
        "be explicit about which dominates:\n"
        "  1. COMPANY-SPECIFIC — earnings, board change, M&A, rating, large filing\n"
        "  2. SECTOR — peers moving on sector news, regulation, demand shock\n"
        "  3. MACRO/GLOBAL — Nifty drift, FII flows, Fed/crude/USD-INR, China\n\n"
        "Rules:\n"
        " - Cite at most ONE concrete headline or filing if it actually explains the move.\n"
        " - If evidence is thin, say so plainly: 'No clear company-specific catalyst — "
        "appears to track the broader Nifty move' is a valid answer.\n"
        " - Distinguish 'caused by' (a specific event) from 'consistent with' (sector drift).\n"
        " - Plain prose, no bullets, no preamble, no 'not financial advice', no emojis.\n\n"
        "EVIDENCE:\n" + "\n".join(parts)
    )

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
    relevant_filings = [
        {"title": f.get("title", ""), "date": f.get("date", ""),
         "tags": f.get("tags", "")}
        for f in (filings or [])[:6]
    ]

    safe_model = gemini_model if gemini_model in _FREE_TIER_MODELS else _DEFAULT_INTEL_MODEL

    ai_summary = None
    if gemini_key:
        ai_summary = _gemini_summary(quote, news, relevant_filings, reddit,
                                     gemini_key, safe_model)
    summary = ai_summary or _heuristic_summary(quote, news, relevant_filings, reddit)

    payload = {
        "symbol": sym,
        "summary": summary,
        "ai_powered": bool(ai_summary),
        "model_used": safe_model if ai_summary else "heuristic",
        "news": news,
        "reddit": reddit,
        "rating": rating,
        "filings": relevant_filings,
        "fetched_at": datetime.now().isoformat(),
    }
    _INTEL_CACHE[sym] = (now, payload)
    return payload
