"""Fetch stock prices, pre-open data, sector analysis, and detect price alerts from NSE India."""
from concurrent.futures import ThreadPoolExecutor

import requests

from fetch import SESSION, _warm_session, BASE_URL

# Yahoo Finance is used as the primary live-price source: NSE's quote/index
# APIs block requests from datacenter/cloud IPs (Railway), so they time out or
# 403/404 in production. Yahoo's chart endpoint works from any IP, no key.
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}.NS"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}

# Cap on number of constituent stocks shown in any sector overview heatmap.
SECTOR_TOP_N = 20

INDEX_API = BASE_URL + "/api/equity-stockIndices"
FMCG_INDEX_API = INDEX_API  # legacy alias
QUOTE_API = BASE_URL + "/api/quote-equity"
PRE_OPEN_API = BASE_URL + "/api/market-data-pre-open"

# Map our industry keys -> NSE sector index names. Industries absent from
# this map have no native NSE sector index and get a synthetic sector built
# from the user's watchlist instead (see fetch_sector_data).
INDUSTRY_INDEX_MAP = {
    "FMCG": "NIFTY FMCG",
    "Banking": "NIFTY BANK",
    "IT": "NIFTY IT",
    "Pharma": "NIFTY PHARMA",
    "Steel": "NIFTY METAL",
    "Auto": "NIFTY AUTO",
    "Energy": "NIFTY OIL & GAS",
    "Power": "NIFTY ENERGY",
    "Real Estate": "NIFTY REALTY",
    "Finance (NBFC)": "NIFTY FINANCIAL SERVICES",
    "Insurance": "NIFTY FINANCIAL SERVICES",
    "Metals & Mining": "NIFTY METAL",
    "Consumer Durables": "NIFTY CONSUMER DURABLES",
    # Chemicals, Cement, Telecom: no native NSE sector index — synthesized.
}


# Curated top-N constituents (by market cap, NSE symbols) for industries that
# lack a dedicated NSE sector index. Used by _build_synthetic_sector so the
# overview shows ~20 sector peers instead of just the user's 5-stock watchlist.
SYNTHETIC_SECTOR_CONSTITUENTS = {
    "Chemicals": [
        "PIDILITIND", "SRF", "UPL", "LINDEINDIA", "PIIND",
        "SOLARINDS", "AARTIIND", "DEEPAKNTR", "ATUL", "NAVINFLUOR",
        "COROMANDEL", "CHAMBLFERT", "TATACHEM", "ALKYLAMINE", "CLEAN",
        "FINEORG", "GNFC", "VINATIORGA", "SUMICHEM", "BAYERCROP",
    ],
    "Cement": [
        "ULTRACEMCO", "SHREECEM", "AMBUJACEM", "ACC", "DALBHARAT",
        "JKCEMENT", "RAMCOCEM", "NUVOCO", "INDIACEM", "BIRLACORPN",
        "JKLAKSHMI", "HEIDELBERG", "ORIENTCEM", "STARCEMENT", "SAGCEM",
        "PRISMJOHNSN", "DECCANCE", "NCLIND", "MAGADHSUG", "SAURASHCEM",
    ],
    "Telecom": [
        "BHARTIARTL", "RELIANCE", "IDEA", "INDUSTOWER", "TATACOMM",
        "HFCL", "STERLITE", "ROUTE", "TANLA", "RAILTEL",
        "GTPL", "ITI", "ONMOBILE", "TEJASNET", "NELCO",
    ],
}


def resolve_index_name(industry):
    """Return the NSE index name for an industry key, or None if the industry
    has no native NSE sector index (caller falls back to a synthetic sector)."""
    if not industry:
        return "NIFTY FMCG"
    return INDUSTRY_INDEX_MAP.get(industry)


def _yahoo_quote(symbol):
    """Fetch a single live quote from Yahoo Finance, returned in the same shape
    as the NSE quote/index parsers so downstream code is unchanged.
    Returns None on failure."""
    try:
        resp = requests.get(YAHOO_CHART.format(symbol),
                            params={"interval": "1d", "range": "1d"},
                            headers=YAHOO_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        result_obj = resp.json()["chart"]["result"][0]
        meta = result_obj["meta"]
        last = meta.get("regularMarketPrice") or 0
        prev = meta.get("chartPreviousClose") or meta.get("previousClose") or 0
        change = (last - prev) if (last and prev) else 0
        pchange = (change / prev * 100) if prev else 0

        # Day's opening price — Yahoo's meta has no open field, so read it from
        # the OHLC indicators (first non-null open in the 1d bar).
        day_open = 0
        try:
            opens = ((result_obj.get("indicators", {}).get("quote", [{}]) or [{}])[0] or {}).get("open") or []
            opens = [o for o in opens if o is not None]
            if opens:
                day_open = opens[0]
        except Exception:
            day_open = 0

        return {
            "symbol": symbol,
            "companyName": meta.get("longName") or meta.get("shortName") or symbol,
            "industry": "",
            "lastPrice": round(last, 2),
            "change": round(change, 2),
            "pChange": round(pchange, 2),
            "previousClose": round(prev, 2),
            "open": round(day_open, 2),
            "close": round(last, 2),
            "vwap": 0,
            "dayHigh": meta.get("regularMarketDayHigh", 0) or 0,
            "dayLow": meta.get("regularMarketDayLow", 0) or 0,
            "yearHigh": meta.get("fiftyTwoWeekHigh", 0) or 0,
            "yearHighDate": "",
            "yearLow": meta.get("fiftyTwoWeekLow", 0) or 0,
            "yearLowDate": "",
            "totalTradedVolume": meta.get("regularMarketVolume", 0) or 0,
            "totalTradedValue": 0,
            "lastUpdateTime": "",
            "perChange30d": None,
            "perChange365d": None,
        }
    except Exception as e:
        print(f"[PRICES] Yahoo quote failed for {symbol}: {e}")
        return None


def fetch_yahoo_prices(symbols):
    """Fetch live prices for many symbols from Yahoo Finance in parallel."""
    prices = {}
    if not symbols:
        return prices
    with ThreadPoolExecutor(max_workers=8) as pool:
        for sym, q in zip(symbols, pool.map(_yahoo_quote, symbols)):
            if q:
                prices[sym] = q
    return prices


def fetch_watchlist_prices(symbols, industry=None):
    """Fetch current prices for a list of symbols.

    Primary source is Yahoo Finance because NSE's quote/index APIs block
    requests coming from datacenter/cloud IPs (Railway) — they time out or
    return 403/404 in production. Any symbol Yahoo can't resolve falls back
    to NSE's per-symbol quote (works when running from a residential IP)."""
    prices = fetch_yahoo_prices(symbols)

    missing = [s for s in symbols if s not in prices]
    if missing:
        _warm_session()
        for sym in missing:
            quote = _quote_no_warm(sym)
            if quote:
                prices[sym] = quote

    return prices


def fetch_stock_quote(symbol):
    """Fetch a detailed quote for a single stock.

    Yahoo Finance first (works from cloud/Railway IPs); NSE's per-symbol quote
    as a fallback for anything Yahoo can't resolve."""
    q = _yahoo_quote(symbol)
    if q:
        return q
    _warm_session()
    return _quote_no_warm(symbol)


def _quote_no_warm(symbol):
    """Quote fetch without warming the session — caller must warm it once
    upstream. Used by parallel fetchers (synthetic sector builder)."""
    try:
        resp = SESSION.get(QUOTE_API,
                           params={"symbol": symbol},
                           timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        pi = data.get("priceInfo", {})
        meta = data.get("metadata", {})
        info = data.get("info", {})
        idhl = pi.get("intraDayHighLow", {})
        whl = pi.get("weekHighLow", {})
        pom = data.get("preOpenMarket", {})

        result = {
            "symbol": symbol,
            "companyName": info.get("companyName") or meta.get("companyName") or symbol,
            "industry": info.get("industry") or meta.get("industry", ""),
            "lastPrice": pi.get("lastPrice", 0),
            "change": pi.get("change", 0),
            "pChange": pi.get("pChange", 0),
            "previousClose": pi.get("previousClose", 0),
            "open": pi.get("open", 0),
            "close": pi.get("close", 0),
            "vwap": pi.get("vwap", 0),
            "dayHigh": idhl.get("max", 0),
            "dayLow": idhl.get("min", 0),
            "yearHigh": whl.get("max", 0),
            "yearHighDate": whl.get("maxDate", ""),
            "yearLow": whl.get("min", 0),
            "yearLowDate": whl.get("minDate", ""),
            "totalTradedVolume": 0,
            "totalTradedValue": 0,
            "lastUpdateTime": meta.get("lastUpdateTime", ""),
            "perChange30d": None,
            "perChange365d": None,
        }

        # Pre-open data
        if pom:
            iep = pom.get("IEP", 0) or 0
            prev_close = pom.get("prevClose", 0) or pi.get("previousClose", 0) or 0
            pre_change = pom.get("Change", 0) or 0
            pre_pchange = pom.get("perChange", 0) or 0
            if iep and prev_close and not pre_pchange:
                pre_pchange = ((iep - prev_close) / prev_close) * 100 if prev_close else 0
            result["preOpen"] = {
                "iep": iep,
                "change": pre_change,
                "pChange": round(pre_pchange, 2) if pre_pchange else 0,
                "prevClose": prev_close,
                "finalPrice": pom.get("finalPrice", 0),
                "totalTradedVolume": pom.get("totalTradedVolume", 0),
                "lastUpdateTime": pom.get("lastUpdateTime", ""),
            }

        return result
    except Exception as e:
        print(f"[PRICES] Quote fetch failed for {symbol}: {e}")
        return None


def _parse_index_item(item):
    """Parse a stock entry from the index API response."""
    return {
        "symbol": item.get("symbol", ""),
        "companyName": item.get("meta", {}).get("companyName", item.get("symbol", "")),
        "industry": item.get("meta", {}).get("industry", ""),
        "lastPrice": item.get("lastPrice", 0),
        "change": item.get("change", 0),
        "pChange": item.get("pChange", 0),
        "previousClose": item.get("previousClose", 0),
        "open": item.get("open", 0),
        "close": item.get("close", 0) or item.get("lastPrice", 0),
        "vwap": 0,
        "dayHigh": item.get("dayHigh", 0),
        "dayLow": item.get("dayLow", 0),
        "yearHigh": item.get("yearHigh", 0),
        "yearHighDate": "",
        "yearLow": item.get("yearLow", 0),
        "yearLowDate": "",
        "totalTradedVolume": item.get("totalTradedVolume", 0),
        "totalTradedValue": item.get("totalTradedValue", 0),
        "lastUpdateTime": item.get("lastUpdateTime", ""),
        "perChange30d": item.get("perChange30d"),
        "perChange365d": item.get("perChange365d"),
    }


# ── Pre-Open Market ──────────────────────────────────────

def fetch_pre_open_data(symbols):
    """Fetch pre-open session data for given symbols.
    Returns dict of symbol -> pre-open info with IEP, change from prev close."""
    _warm_session()
    result = {}
    try:
        resp = SESSION.get(PRE_OPEN_API, params={"key": "FO"}, timeout=15)
        if resp.status_code != 200:
            return result
        data = resp.json()
        for item in data.get("data", []):
            meta = item.get("metadata", {})
            sym = meta.get("symbol", "")
            if sym not in symbols:
                continue
            detail = item.get("detail", {})
            pom = detail.get("preOpenMarket", {})
            iep = pom.get("IEP", 0) or meta.get("iep", 0) or 0
            prev_close = pom.get("prevClose", 0) or meta.get("previousClose", 0) or 0
            change = pom.get("Change", 0) or 0
            pchange = pom.get("perChange", 0) or 0
            if iep and prev_close and not pchange:
                pchange = ((iep - prev_close) / prev_close) * 100
            result[sym] = {
                "iep": iep,
                "change": change,
                "pChange": round(pchange, 2),
                "prevClose": prev_close,
                "finalPrice": pom.get("finalPrice", 0),
                "totalTradedVolume": pom.get("totalTradedVolume", 0),
                "lastUpdateTime": pom.get("lastUpdateTime", ""),
            }
    except Exception as e:
        print(f"[PRICES] Pre-open fetch failed: {e}")
    return result


def detect_pre_open_alerts(pre_open_data, threshold=1.5):
    """Detect pre-open price movements >= threshold% from previous close."""
    alerts = []
    for sym, data in pre_open_data.items():
        pchange = data.get("pChange", 0)
        if pchange is None:
            continue
        if abs(pchange) >= threshold:
            direction = "up" if pchange > 0 else "down"
            alerts.append({
                "symbol": sym,
                "iep": data.get("iep", 0),
                "pChange": pchange,
                "prevClose": data.get("prevClose", 0),
                "direction": direction,
                "type": "pre_open",
            })
    alerts.sort(key=lambda x: abs(x["pChange"]), reverse=True)
    return alerts


# ── Sector Analysis ──────────────────────────────────────

def fetch_sector_data(industry=None, watchlist_symbols=None):
    """Fetch the sector index (matching the user's industry) and its constituent stocks.

    For industries with no native NSE sector index (Chemicals, Cement, Telecom),
    fall back to a synthetic sector built from the user's watchlist symbols so
    the heading reflects the chosen industry instead of being stuck on FMCG."""
    _warm_session()
    index_name = resolve_index_name(industry)

    if index_name:
        native = _fetch_native_sector(index_name)
        if native:
            return native

    if industry and watchlist_symbols:
        return _build_synthetic_sector(industry, watchlist_symbols)

    # Final fallback: FMCG, so anonymous / unmapped users still see something.
    return _fetch_native_sector("NIFTY FMCG")


def _fetch_native_sector(index_name):
    """Fetch a real NSE sector index and parse it into our sector shape."""
    try:
        resp = SESSION.get(INDEX_API,
                           params={"index": index_name},
                           timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()

        sector_meta = data.get("metadata", {})
        sector = {
            "indexName": sector_meta.get("indexName", index_name),
            "lastPrice": sector_meta.get("last", 0),
            "change": sector_meta.get("change", 0),
            "pChange": sector_meta.get("percChange", 0),
            "open": sector_meta.get("open", 0),
            "previousClose": sector_meta.get("previousClose", 0),
            "high": sector_meta.get("high", 0),
            "low": sector_meta.get("low", 0),
            "perChange30d": sector_meta.get("perChange30d"),
            "perChange365d": sector_meta.get("perChange365d"),
            "timestamp": sector_meta.get("timeVal", ""),
            "chartTodayPath": sector_meta.get("chartTodayPath", ""),
            "chart30dPath": sector_meta.get("chart30dPath", ""),
            "chart365dPath": sector_meta.get("chart365dPath", ""),
        }

        advance = data.get("advance", {})
        sector["advances"] = int(advance.get("advances", 0))
        sector["declines"] = int(advance.get("declines", 0))
        sector["unchanged"] = int(advance.get("unchanged", 0))

        stocks = []
        for item in data.get("data", []):
            sym = item.get("symbol", "")
            # Skip the index row itself (matches any NIFTY * header)
            if sym.startswith("NIFTY "):
                continue
            stocks.append({
                "symbol": sym,
                "companyName": item.get("meta", {}).get("companyName", sym),
                "lastPrice": item.get("lastPrice", 0),
                "change": item.get("change", 0),
                "pChange": item.get("pChange", 0),
                "previousClose": item.get("previousClose", 0),
                "totalTradedVolume": item.get("totalTradedVolume", 0),
                "perChange30d": item.get("perChange30d"),
                "perChange365d": item.get("perChange365d"),
            })

        # NSE returns constituents in index-weight order; cap at top N for the
        # heatmap so the layout stays consistent across sectors.
        stocks = stocks[:SECTOR_TOP_N]

        return {"sector": sector, "stocks": stocks}
    except Exception as e:
        print(f"[PRICES] Sector fetch failed for {index_name}: {e}")
        return None


def _build_synthetic_sector(industry, watchlist_symbols):
    """Build a sector overview for an industry that has no native NSE index.

    Uses the curated SYNTHETIC_SECTOR_CONSTITUENTS list (top ~20 by market cap)
    so the heatmap reflects the broader sector, not just the user's 5-stock
    watchlist. Watchlist symbols are unioned in (and ringed in the UI) so the
    user always sees the companies they track."""
    curated = SYNTHETIC_SECTOR_CONSTITUENTS.get(industry, [])
    seen = set()
    target = []
    for sym in list(curated) + list(watchlist_symbols or []):
        if sym and sym not in seen:
            seen.add(sym)
            target.append(sym)
        if len(target) >= SECTOR_TOP_N:
            break

    if not target:
        return None

    # Warm the NSE session once, then fetch quotes in parallel.
    _warm_session()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_quote_no_warm, target))

    stocks = []
    for sym, q in zip(target, results):
        if not q:
            continue
        stocks.append({
            "symbol": sym,
            "companyName": q.get("companyName", sym),
            "lastPrice": q.get("lastPrice", 0),
            "change": q.get("change", 0),
            "pChange": q.get("pChange", 0),
            "previousClose": q.get("previousClose", 0),
            "totalTradedVolume": q.get("totalTradedVolume", 0),
            "perChange30d": q.get("perChange30d"),
            "perChange365d": q.get("perChange365d"),
        })

    if not stocks:
        return None

    advances = sum(1 for s in stocks if (s["pChange"] or 0) > 0)
    declines = sum(1 for s in stocks if (s["pChange"] or 0) < 0)
    unchanged = len(stocks) - advances - declines
    avg_pchange = sum((s["pChange"] or 0) for s in stocks) / len(stocks)

    sector = {
        "indexName": industry,
        "lastPrice": 0,
        "change": 0,
        "pChange": avg_pchange,
        "open": 0,
        "previousClose": 0,
        "high": 0,
        "low": 0,
        "perChange30d": None,
        "perChange365d": None,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "synthetic": True,
    }
    return {"sector": sector, "stocks": stocks}


def detect_sector_alerts(sector_data, watchlist_symbols, threshold=3.0):
    """When a watchlist stock hits 3%+, check if it's a sector-wide move.
    Returns sector analysis with correlated movers."""
    if not sector_data:
        return []

    stocks = sector_data.get("stocks", [])
    sector = sector_data.get("sector", {})
    sector_alerts = []

    # Find all stocks in the sector moving 3%+
    big_movers = [s for s in stocks if abs(s.get("pChange", 0) or 0) >= threshold]

    if not big_movers:
        return []

    # Check if any watchlist stock is a big mover
    watchlist_movers = [s for s in big_movers if s["symbol"] in watchlist_symbols]
    other_movers = [s for s in big_movers if s["symbol"] not in watchlist_symbols]

    if not watchlist_movers:
        return []

    # Determine if this is a sector-wide move
    sector_pchange = sector.get("pChange", 0)
    is_sector_wide = abs(sector_pchange) >= 2.0 or len(big_movers) >= 3

    for wm in watchlist_movers:
        alert = {
            "symbol": wm["symbol"],
            "pChange": wm["pChange"],
            "lastPrice": wm["lastPrice"],
            "direction": "up" if wm["pChange"] > 0 else "down",
            "isSectorWide": is_sector_wide,
            "sectorChange": sector_pchange,
            "sectorName": sector.get("indexName", "Sector"),
            "correlatedMovers": [],
        }

        # Add correlated movers (same direction, 3%+)
        wm_dir = "up" if wm["pChange"] > 0 else "down"
        for om in other_movers:
            om_dir = "up" if om["pChange"] > 0 else "down"
            if om_dir == wm_dir:
                alert["correlatedMovers"].append({
                    "symbol": om["symbol"],
                    "companyName": om.get("companyName", om["symbol"]),
                    "pChange": om["pChange"],
                    "lastPrice": om["lastPrice"],
                })

        sector_alerts.append(alert)

    return sector_alerts


# ── Standard Alerts ──────────────────────────────────────

def detect_price_alerts(prices, threshold=3.0):
    """Detect stocks with price change >= threshold%.
    Returns list of alert dicts."""
    alerts = []
    for sym, data in prices.items():
        pchange = data.get("pChange", 0)
        if pchange is None:
            continue
        if abs(pchange) >= threshold:
            direction = "up" if pchange > 0 else "down"
            alerts.append({
                "symbol": sym,
                "companyName": data.get("companyName", sym),
                "lastPrice": data.get("lastPrice", 0),
                "change": data.get("change", 0),
                "pChange": pchange,
                "direction": direction,
            })
    alerts.sort(key=lambda x: abs(x["pChange"]), reverse=True)
    return alerts


def analyze_price_movement(symbol, direction, pchange, recent_filings=None):
    """Generate a brief analysis of why a stock might have moved.
    Uses recent corporate announcements as context."""
    reasons = []

    if recent_filings:
        for f in recent_filings[:10]:
            title = (f.get("title", "") or "").lower()
            tags = (f.get("tags", "") or "").lower()

            if "financial result" in title or "financial" in tags:
                reasons.append(f"Recent financial results filing: {f.get('title', '')}")
            elif "dividend" in title:
                reasons.append(f"Dividend announcement: {f.get('title', '')}")
            elif "board meeting" in title or "board" in tags:
                reasons.append(f"Board meeting outcome: {f.get('title', '')}")
            elif "acquisition" in title or "merger" in title or "divestment" in tags:
                reasons.append(f"M&A activity: {f.get('title', '')}")
            elif "rating" in title or "credit" in tags:
                reasons.append(f"Rating action: {f.get('title', '')}")
            elif "restructur" in title:
                reasons.append(f"Restructuring news: {f.get('title', '')}")
            elif "agm" in title or "egm" in title:
                reasons.append(f"General meeting: {f.get('title', '')}")
            elif "insider" in tags or "insider trading" in title:
                reasons.append(f"Insider activity: {f.get('title', '')}")

    if not reasons:
        if abs(pchange) >= 5:
            reasons.append(f"Significant {'rally' if direction == 'up' else 'decline'} of {abs(pchange):.1f}% — may be driven by market-wide sentiment or sector rotation.")
        else:
            reasons.append(f"Price moved {abs(pchange):.1f}% {'higher' if direction == 'up' else 'lower'} — could be due to broader market trends or institutional activity.")

    return reasons[:3]


def analyze_sector_movement(alert, correlated_filings=None):
    """Analyze why a sector-wide movement happened.
    correlated_filings: dict of symbol -> recent filings for correlated movers."""
    reasons = []

    if alert.get("isSectorWide"):
        sector_dir = "up" if alert["sectorChange"] > 0 else "down"
        reasons.append(
            f"Sector-wide move: {alert['sectorName']} index {'rose' if sector_dir == 'up' else 'fell'} "
            f"{abs(alert['sectorChange']):.1f}% — this appears to be a broad sector trend, not company-specific."
        )
        n_corr = len(alert.get("correlatedMovers", []))
        if n_corr:
            names = ", ".join(m["symbol"] for m in alert["correlatedMovers"][:4])
            sector_label = alert.get("sectorName") or "sector"
            reasons.append(f"{n_corr} other {sector_label} stocks also moved 3%+ in the same direction: {names}")

    # Check correlated movers' filings for common themes
    if correlated_filings:
        themes = set()
        for sym, filings in correlated_filings.items():
            for f in filings[:5]:
                title = (f.get("title", "") or "").lower()
                if "financial result" in title:
                    themes.add("earnings season")
                elif "dividend" in title:
                    themes.add("dividend announcements")
                elif "board meeting" in title:
                    themes.add("board activity")
                elif "regulatory" in title or "compliance" in title:
                    themes.add("regulatory developments")
        if themes:
            reasons.append(f"Common themes across sector filings: {', '.join(themes)}")

    if not reasons:
        reasons.append(f"Price moved {abs(alert['pChange']):.1f}% — analyzing recent filings for possible catalysts.")

    return reasons[:4]
