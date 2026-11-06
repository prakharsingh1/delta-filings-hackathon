"""Company news desk: what the public is saying about one watchlist company,
where it is being said, what could move the price, and which filings the
news may trigger.

It composes engines the product already has rather than duplicating them:
price_intel (why the stock moved, research reads, rating), industry_intel
(sector drivers, risks, regulators and headlines), reg_news (SEBI and RBI),
the cached exchange filings, and agents.client for the structured AI brief.
What is new here: press across several countries, Reddit, a dated price
series for move correlation, and the rulebook-backed filing map.

Every source is public and read-only:

  official    the company's own exchange filings already cached for the symbol
  press       Google News search RSS across several editions, so the same
              story is seen from India (English and Hindi), the US, the UK,
              Singapore and the Gulf
  social      Reddit search RSS (no API key; rate limits are reported, not hidden)
  regulatory  SEBI and RBI items from reg_news that touch a listed company
  industry    industry_intel headlines (sector, deals, regulatory, global)
  x           only when X_BEARER_TOKEN is set; the X API is paid, so by default
              the lane says "not configured" rather than inventing posts

Nothing here writes to the product database except the shared response cache.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

import demo_cache
import reg_news
import rulebook as rb
from database import get_cached_filings, get_connection, get_setting

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 12
WINDOW_DAYS = 30
DESK_TTL = 30 * 60          # seconds a built desk is served without refetching
MOVE_THRESHOLD = 1.5        # % day move worth explaining

# Google News editions: (country code, language, hl, ceid, label)
EDITIONS = [
    ("IN", "en", "en-IN", "IN:en", "India"),
    ("IN", "hi", "hi", "IN:hi", "India · Hindi press"),
    ("US", "en", "en-US", "US:en", "United States"),
    ("GB", "en", "en-GB", "GB:en", "United Kingdom"),
    ("SG", "en", "en-SG", "SG:en", "Singapore"),
    ("AE", "en", "en-AE", "AE:en", "UAE"),
]
COUNTRY_NAMES = {"IN": "India", "US": "United States", "GB": "United Kingdom", "SG": "Singapore", "AE": "UAE"}

# Factor tags: the macro and micro drivers a reader scans for. Keyword rules on
# purpose, so every tag can be explained by the words in the headline.
FACTORS = [
    ("Results & guidance", r"\b(q[1-4]|quarter(?:ly)?|results?|earnings|net profit|revenue|margins?|guidance|ebitda|volume growth|topline|bottomline)\b"),
    ("Input costs & pricing", r"\b(price (?:hike|cut|increase)|input costs?|raw materials?|palm oil|crude|commodit(?:y|ies)|inflation|packaging)\b"),
    ("Demand & consumption", r"\b(demand|consumption|rural|urban|volumes?|festive|monsoon|household|footfall)\b"),
    ("Leadership & board", r"\b(ceo|cfo|managing director|chairman|chairperson|director|board|appoint(?:s|ed|ment)?|resign(?:s|ed|ation)?|succession|steps down)\b"),
    ("Deals & restructuring", r"\b(acqui(?:re|res|red|sition)|merger|demerger|stake|divest(?:s|ed|ment)?|joint venture|buyback|spin-?off|scheme of arrangement|takeover)\b"),
    ("Regulation & litigation", r"\b(sebi|rbi|mca|fssai|ccpa|court|tribunal|nclt|penalt(?:y|ies)|fine[sd]?|order|probe|regulator(?:y|s)?|misleading|ban(?:ned)?|notice)\b"),
    ("Tax & policy", r"\b(gst|budget|tax(?:es|ation)?|duty|tariffs?|policy|government|subsid(?:y|ies)|ministry)\b"),
    ("Rates & currency", r"\b(repo|rate (?:cut|hike)|rupee|dollar|forex|interest rates?|monetary|liquidity)\b"),
    ("Dividends & capital", r"\b(dividend|record date|bonus (?:issue|shares)|stock split|rights issue|qip|allot(?:s|ted|ment)?)\b"),
    ("Products & brands", r"\b(launch(?:es|ed)?|brands?|products?|portfolio|campaign|premium(?:isation)?)\b"),
    ("Analyst views", r"\b(target price|upgrade[sd]?|downgrade[sd]?|rating|brokerage|overweight|underweight|outperform)\b"),
    ("Shareholding", r"\b(promoters?|fii|dii|mutual funds?|stake sale|block deal|pledge[sd]?|shareholding)\b"),
]
_FACTOR_RES = [(name, re.compile(pattern, re.I)) for name, pattern in FACTORS]

# Headline wording -> rulebook event triggers. The rulebook decides which
# obligation each trigger starts (form, law, days); this only recognises the
# event in prose. Patterns are deliberately conservative: a miss is better
# than a false "file this".
TRIGGER_PATTERNS = [
    ("director_resigned", r"\b(director|chairman|chairperson)\b.{0,40}\b(resign(?:s|ed|ation)|steps? down|quits|cessation)\b|\b(resign(?:s|ed|ation)|steps? down)\b.{0,40}\b(director|chairman|chairperson)\b"),
    ("kmp_resigned", r"\b(cfo|chief financial officer|company secretary|compliance officer|ceo|chief executive)\b.{0,40}\b(resign(?:s|ed|ation)|steps? down|quits)\b|\b(resign(?:s|ed|ation)|steps? down)\b.{0,40}\b(cfo|chief financial officer|company secretary|ceo)\b"),
    ("md_appointed", r"\b(appoint(?:s|ed|ment of)?|names?|elevat(?:es|ed)|re-?appoint(?:s|ed|ment))\b.{0,50}\b(managing director|md & ceo|md and ceo|whole-?time director)\b|\b(managing director|md & ceo)\b.{0,30}\bappoint"),
    ("kmp_appointed", r"\b(appoint(?:s|ed|ment of)?|names?)\b.{0,40}\b(cfo|chief financial officer|company secretary|compliance officer|ceo|chief executive)\b|\bnew (cfo|ceo|company secretary)\b"),
    ("director_appointed", r"\b(appoint(?:s|ed|ment of)?|inducts?|joins? the board|names?)\b.{0,50}\b(independent director|director|to (?:the|its) board)\b"),
    ("auditor_resigned", r"\b(auditors?)\b.{0,40}\b(resign(?:s|ed|ation)|withdraws?|quits)\b|\b(resign(?:s|ed|ation))\b.{0,30}\bauditor"),
    ("auditor_appointed", r"\b(appoint(?:s|ed|ment of)?)\b.{0,40}\b(statutory auditors?|auditors?)\b"),
    ("shares_allotted", r"\b(allot(?:s|ted|ment)|preferential issue|qip|rights issue|bonus (?:issue|shares)|esop(?:s)? (?:allot|exercise))\b"),
    ("capital_increased", r"\b(authori[sz]ed (?:share )?capital)\b"),
    ("charge_created", r"\b(creat(?:es|ed|ion of) (?:a )?charge|mortgage|hypothecat|secured (?:ncds?|debentures?|loan))\b"),
    ("registered_office_changed", r"\b(registered office)\b.{0,30}\b(shift|chang|mov)"),
    ("results_approved", r"\b(q[1-4]|quarterly|half[- ]yearly|annual|fy\d{2}) (?:results?|earnings)\b|\b(net profit|posts? (?:a )?(?:profit|loss)|reports? (?:a )?(?:profit|loss)|profit (?:rises|falls|jumps|slips|up|down))\b"),
    ("board_meeting_scheduled", r"\b(board (?:meeting )?(?:to meet|scheduled|will meet|to consider)|to consider (?:and approve|dividend|results|fund ?rais))\b"),
    ("board_meeting_held", r"\b(board (?:approves|approved|has approved|clears|cleared|declares|declared|recommends)|outcome of (?:the )?board meeting|board meeting outcome|interim dividend|final dividend)\b"),
    ("general_meeting_held", r"\b(agm|annual general meeting|egm|extraordinary general meeting|postal ballot|shareholders (?:approve|approved|reject))\b"),
    ("fdi_received", r"\b(fdi|foreign direct investment|foreign investor[s]? (?:to )?(?:buy|pick|acquire)|overseas (?:investor|fund) (?:to )?(?:buy|pick))\b"),
    ("resolution_passed", r"\b(special resolution|ordinary resolution)\b"),
]
_TRIGGER_RES = [(trig, re.compile(pattern, re.I)) for trig, pattern in TRIGGER_PATTERNS]

# Events the rulebook has no named trigger for but which Reg 30 (material
# events) covers for a listed company. Surfaced as "check materiality".
MATERIAL_EVENT_RE = re.compile(
    r"\b(acqui(?:re|res|red|sition)|merger|demerger|amalgamation|joint venture|takeover|buyback|"
    r"scheme of arrangement|divest(?:s|ed|ment)|fund ?rais(?:e|ing)|fire|strike|lockout|plant (?:shut|closure)|"
    r"penalt(?:y|ies)|sebi order|tax demand|show[- ]cause|litigation|default|credit rating|rating (?:upgrade|downgrade|cut)|"
    r"fraud|data breach|cyber ?attack|recall|licen[cs]e (?:cancel|suspend)|insolvency|nclt)\b", re.I)

_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()


# ── Rulebook index ─────────────────────────────────────────────────────────

def _event_rules():
    """{trigger: [rule, ...]} for every event-driven rule that applies to a listed company."""
    out = {}
    for rule in rb.RULES:
        sched = rule["schedule"]
        if sched.get("kind") != "event" or "listed" not in rule["types"]:
            continue
        for trig in sched.get("triggers", []):
            out.setdefault(trig, []).append(rule)
    return out


_EVENT_RULES = _event_rules()
_REG30 = next((r for r in rb.RULES if r["id"] == "IN-SEBI-REG30"), None)


def _due_label(event_day, sched):
    days = sched.get("days", 0)
    working = sched.get("working_days")
    if days < 0:
        return f"{abs(days)} {'working ' if working else ''}days before the meeting"
    if days == 0:
        return "same day as the event"
    due = event_day + timedelta(days=days)
    unit = ("working day" if working else "day") + ("" if days == 1 else "s")
    return f"within {days} {unit} · by {due.day} {due:%b %Y}"


def filing_hints(text, event_day):
    """Rulebook obligations a headline may start, with the deadline counted
    from the day the story was published. Returns [] when nothing matches."""
    found, seen = [], set()
    for trig, regex in _TRIGGER_RES:
        if not regex.search(text):
            continue
        for rule in _EVENT_RULES.get(trig, []):
            if rule["id"] in seen:
                continue
            seen.add(rule["id"])
            found.append({"rule_id": rule["id"], "trigger": trig, "name": rule["name"], "form": rule["form"],
                          "law": rule["law"], "due": _due_label(event_day, rule["schedule"]), "certain": True})
    if _REG30 and _REG30["id"] not in seen and MATERIAL_EVENT_RE.search(text):
        found.append({"rule_id": _REG30["id"], "trigger": "material_event", "name": _REG30["name"], "form": _REG30["form"],
                      "law": _REG30["law"], "due": _due_label(event_day, _REG30["schedule"]) + " · check materiality",
                      "certain": False})
    return found


def factor_tags(text, limit=3):
    return [name for name, regex in _FACTOR_RES if regex.search(text)][:limit]


# ── Helpers ───────────────────────────────────────────────────────────────

def _norm_title(title):
    return re.sub(r"[^a-z0-9ऀ-ॿ]+", " ", (title or "").lower()).strip()


def _item_id(url, title):
    return hashlib.sha1((url or title).encode("utf-8")).hexdigest()[:12]


def _strip_html(text):
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


def _to_iso(dt):
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_rfc2822(text):
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None


def _parse_iso(text):
    try:
        return datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _date_label(iso):
    try:
        d = date.fromisoformat(iso[:10])
    except (ValueError, TypeError):
        return ""
    return f"{d.day} {d:%b}"


def _item(lane, source, title, url, published, excerpt="", country="", lang="en", extra=None):
    iso = _to_iso(published)
    item = {"id": _item_id(url, title), "lane": lane, "source": source, "title": title.strip(), "url": url,
            "excerpt": (excerpt or "")[:280], "published_at": iso, "date": iso[:10], "date_label": _date_label(iso),
            "country": country, "lang": lang, "countries": [country] if country else []}
    if extra:
        item.update(extra)
    return item


def clean_name(name):
    """'Hindustan Unilever Limited' -> 'Hindustan Unilever': the form the press uses."""
    cleaned = re.sub(r"\s+(?:pvt\.?\s*ltd\.?|private\s+limited|limited|ltd\.?)\s*$", "", (name or "").strip(), flags=re.I).strip()
    return cleaned or (name or "").strip()


def _gemini():
    """Same precedence as the price-intel route: per-workspace setting, then the server key."""
    key = (get_setting("gemini_api_key", "") or "").strip() or (os.environ.get("GEMINI_API_KEY", "") or "").strip()
    model = (get_setting("gemini_model", "gemini-2.5-flash") or "gemini-2.5-flash").strip()
    return key, model


# ── Sources ───────────────────────────────────────────────────────────────

def _google_news(query, country, lang, hl, ceid, days=WINDOW_DAYS):
    url = "https://news.google.com/rss/search"
    params = {"q": f"{query} when:{days}d", "hl": hl, "gl": country, "ceid": ceid}
    r = requests.get(url, params=params, headers={"User-Agent": BROWSER_UA}, timeout=TIMEOUT)
    r.raise_for_status()
    items = []
    for node in ET.fromstring(r.content).findall("./channel/item"):
        title = node.findtext("title") or ""
        source_node = node.find("source")
        source = (source_node.text or "").strip() if source_node is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        # Some publishers leak CMS placeholders ("%%sep%% %%sitename%%") into RSS titles.
        title = re.sub(r"\s*%%[^%]*%%\s*", " ", title).strip(" -|·")
        excerpt = _strip_html(node.findtext("description") or "")
        if excerpt.startswith(title[:40]):
            excerpt = ""  # Google often repeats the headline as the body
        items.append(_item("press", source or "Google News", title, node.findtext("link") or "",
                           _parse_rfc2822(node.findtext("pubDate")), excerpt, country, lang))
    return items


def _reddit(query, days=WINDOW_DAYS):
    r = requests.get("https://www.reddit.com/search.rss",
                     params={"q": query, "sort": "new", "t": "month" if days > 7 else "week"},
                     headers={"User-Agent": BROWSER_UA}, timeout=TIMEOUT)
    if r.status_code == 429:
        raise RuntimeError("Reddit is rate-limiting this address; try again in a few minutes.")
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    for entry in ET.fromstring(r.content).findall("a:entry", ns):
        cat = entry.find("a:category", ns)
        sub = cat.get("label") if cat is not None else "reddit"
        link = entry.find("a:link", ns)
        body = _strip_html(entry.findtext("a:content", default="", namespaces=ns))
        body = re.sub(r"submitted by\s+/u/\S+.*$", "", body, flags=re.S).strip()
        items.append(_item("social", sub or "reddit", entry.findtext("a:title", default="", namespaces=ns),
                           link.get("href") if link is not None else "", _parse_iso(entry.findtext("a:updated", default="", namespaces=ns)),
                           body, "", "en", {"author": (entry.findtext("a:author/a:name", default="", namespaces=ns) or "").strip()}))
    return items


def _x_posts(query, bearer_token):
    """Recent posts from the X API v2. Only called when a token is configured."""
    r = requests.get("https://api.x.com/2/tweets/search/recent",
                     params={"query": f'"{query}" -is:retweet lang:en', "max_results": 25,
                             "tweet.fields": "created_at,public_metrics", "expansions": "author_id",
                             "user.fields": "username,name"},
                     headers={"Authorization": f"Bearer {bearer_token}", "User-Agent": "delta-filings/1.0"}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    items = []
    for t in data.get("data", []):
        u = users.get(t.get("author_id"), {})
        handle = u.get("username", "")
        items.append(_item("x", f"@{handle}" if handle else "X", t.get("text", "")[:140],
                           f"https://x.com/{handle}/status/{t['id']}" if handle else f"https://x.com/i/status/{t['id']}",
                           _parse_iso(t.get("created_at")), t.get("text", ""), "", "en",
                           {"author": u.get("name", ""), "metrics": t.get("public_metrics", {})}))
    return items


# ── Price series: four upstreams and a local store ────────────────────────
# Every successful fetch and every live quote lands in price_history, so once
# any source has answered once the chart never depends on an upstream again.

_IST = timezone(timedelta(hours=5, minutes=30))


def _points_from(pairs):
    """[(date, close)] oldest first -> dated points with day-over-day %."""
    points, prev = [], None
    for day, close in sorted(pairs):
        if close is None or close <= 0:
            continue
        pct = round((close - prev) / prev * 100, 2) if prev else 0.0
        points.append({"date": day.isoformat(), "label": f"{day.day} {day:%b}", "close": round(float(close), 2), "pct": pct})
        prev = close
    return points


def _yahoo_series(symbol):
    """Yahoo rate-limits bursts, so this alternates hosts with a short back-off."""
    last = None
    for attempt, host in enumerate(("query2", "query1")):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}.NS",
                             params={"range": "1mo", "interval": "1d"},
                             headers={"User-Agent": BROWSER_UA, "Accept": "application/json"}, timeout=TIMEOUT)
            if r.status_code == 429:
                last = RuntimeError("rate-limited (429)")
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            closes = res["indicators"]["quote"][0].get("close") or []
            pairs = [(datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_IST).date(), c)
                     for ts, c in zip(res.get("timestamp") or [], closes) if c is not None]
            if pairs:
                return _points_from(pairs)
            last = RuntimeError("no closes")
        except Exception as e:
            last = e
    raise last or RuntimeError("no data")


def _moneycontrol_series(symbol, days=WINDOW_DAYS):
    """Moneycontrol's chart feed takes the NSE symbol and returns daily bars."""
    now = int(time.time())
    r = requests.get("https://priceapi.moneycontrol.com/techCharts/indianMarket/stock/history",
                     params={"symbol": symbol, "resolution": "1D", "from": now - (days + 10) * 86400, "to": now,
                             "countback": days, "currencyCode": "INR"},
                     headers={"User-Agent": BROWSER_UA, "Referer": "https://www.moneycontrol.com/"}, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if d.get("s") != "ok" or not d.get("c"):
        raise RuntimeError(f"no data ({d.get('s')})")
    pairs = [(datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_IST).date(), c) for ts, c in zip(d["t"], d["c"])]
    return _points_from(pairs)


def _nse_series(symbol, days=WINDOW_DAYS):
    """NSE's own archive through the app's warmed NSE session."""
    from fetch import SESSION, _warm_session
    _warm_session()
    today = date.today()
    r = SESSION.get("https://www.nseindia.com/api/historical/cm/equity",
                    params={"symbol": symbol, "series": '["EQ"]', "from": (today - timedelta(days=days + 5)).strftime("%d-%m-%Y"),
                            "to": today.strftime("%d-%m-%Y")}, timeout=TIMEOUT)
    r.raise_for_status()
    rows = r.json().get("data") or []
    pairs = [(date.fromisoformat(str(row["CH_TIMESTAMP"])[:10]), float(row["CH_CLOSING_PRICE"])) for row in rows if row.get("CH_TIMESTAMP")]
    if not pairs:
        raise RuntimeError("no rows")
    return _points_from(pairs)


def _bse_series(symbol, days=WINDOW_DAYS):
    """BSE's graph feed through the app's BSE session (works from cloud addresses)."""
    from compliance_verification import _get_bse_session, _resolve_bse_scrip
    scrip = _resolve_bse_scrip(symbol)
    if not scrip:
        raise RuntimeError("no BSE scrip code")
    r = _get_bse_session().get("https://api.bseindia.com/BseIndiaAPI/api/StockReachGraph/w",
                               params={"scripcode": scrip, "flag": "3M", "fromdate": "", "todate": "", "seriesid": ""},
                               headers={"Referer": "https://www.bseindia.com/"}, timeout=TIMEOUT)
    r.raise_for_status()
    pairs = []
    for row in json.loads((r.json() or {}).get("Data") or "[]"):
        raw = str(row.get("dttm") or "")
        day = None
        m = re.search(r"Date\((\d+)\)", raw)
        if m:
            day = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).date()
        else:
            for fmt in ("%a %b %d %Y %H:%M:%S", "%a %b %d %Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
                try:
                    day = datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt).date()
                    break
                except ValueError:
                    continue
        try:
            close = float(row.get("vale1") or 0)
        except (TypeError, ValueError):
            continue
        if day and close > 0 and day >= date.today() - timedelta(days=days + 2):
            pairs.append((day, close))
    if not pairs:
        raise RuntimeError("no dated closes")
    return _points_from(pairs)


PRICE_SOURCES = [("Yahoo Finance", _yahoo_series), ("Moneycontrol", _moneycontrol_series), ("NSE", _nse_series), ("BSE", _bse_series)]


def _init_price_table():
    conn = get_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS price_history (symbol TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL, "
                 "source TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (symbol, date))")
    conn.commit()
    conn.close()


def store_points(symbol, points, source):
    """Upsert closes. A live quote never overwrites a real close for the same day."""
    _init_price_table()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    conn = get_connection()
    for p in points:
        if source == "quote":
            conn.execute("INSERT INTO price_history (symbol, date, close, source, updated_at) VALUES (?, ?, ?, 'quote', ?) "
                         "ON CONFLICT(symbol, date) DO UPDATE SET close = excluded.close, updated_at = excluded.updated_at "
                         "WHERE price_history.source = 'quote'", (symbol, p["date"], p["close"], now))
        else:
            conn.execute("INSERT INTO price_history (symbol, date, close, source, updated_at) VALUES (?, ?, ?, ?, ?) "
                         "ON CONFLICT(symbol, date) DO UPDATE SET close = excluded.close, source = excluded.source, updated_at = excluded.updated_at",
                         (symbol, p["date"], p["close"], source, now))
    conn.commit()
    conn.close()


def stored_points(symbol, days=WINDOW_DAYS):
    _init_price_table()
    since = (date.today() - timedelta(days=days)).isoformat()
    conn = get_connection()
    rows = conn.execute("SELECT date, close FROM price_history WHERE symbol = ? AND date >= ? ORDER BY date", (symbol, since)).fetchall()
    conn.close()
    return _points_from([(date.fromisoformat(r["date"]), r["close"]) for r in rows])


def _quote_day(quote):
    """The trading day a quote describes, from its own timestamp; today if unknown."""
    raw = str((quote or {}).get("lastUpdateTime") or "").replace(" IST", "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt).date()
        except ValueError:
            continue
    return datetime.now(_IST).date()


def seed_quote(symbol, quote):
    """Record the live quote as that day's last price so the store grows even
    when every history feed is down."""
    if not quote or quote.get("lastPrice") in (None, 0):
        return
    store_points(symbol, [{"date": _quote_day(quote).isoformat(), "close": float(quote["lastPrice"])}], "quote")


TOLERANCE_PCT = 1.0  # closes from two sources, or a close and the live quote, must agree within this


def _compare(a, b):
    """Largest % gap between two series on the days they share: (gap, days)."""
    by_date = {p["date"]: p["close"] for p in a}
    gaps = [abs(p["close"] - by_date[p["date"]]) / by_date[p["date"]] * 100 for p in b if p["date"] in by_date and by_date[p["date"]]]
    return (round(max(gaps), 2), len(gaps)) if gaps else (None, 0)


def _quote_check(points, quote):
    """Does the series end where the live quote says the market is? The last
    bar is compared with today's last price when it is today's bar, otherwise
    with the quote's previous close."""
    if not quote or not points:
        return None
    last = points[-1]
    quote_day = _quote_day(quote).isoformat()
    if last["date"] == quote_day:
        ref, label = quote.get("lastPrice"), "last price"
    elif last["date"] < quote_day:
        ref, label = quote.get("previousClose"), "previous close"
    else:
        return None  # the series is newer than the quote; nothing to compare
    try:
        ref = float(ref)
    except (TypeError, ValueError):
        return None
    if ref <= 0:
        return None
    gap = round(abs(last["close"] - ref) / ref * 100, 2)
    return {"check": f"last close ({last['label']}) vs the quote's {label} ({quote_day[8:10]} {date.fromisoformat(quote_day):%b})",
            "ok": gap <= TOLERANCE_PCT, "gap_pct": gap}


def price_series(symbol, quote=None):
    """(points, source, note, checks, verified).

    Fresh cache first. Otherwise ask the upstreams in order and keep the first
    two that answer: the first is the candidate, the second is the cross-check.
    The candidate must also agree with the live quote; if it does not but the
    second source does, the second source wins. Whatever is chosen is stored
    locally, so after one success the chart never depends on an upstream.
    Frozen demo mode never goes outbound."""
    key = f"news.series:{symbol}"
    hit = demo_cache.read(key)
    cached = hit["value"] if hit and isinstance(hit["value"], dict) else None
    if cached and hit["age"] < 6 * 3600 and "checks" in cached:
        return cached["points"], cached["source"], "", cached["checks"], cached.get("verified", False)
    if demo_cache.frozen():
        if cached:
            return cached["points"], cached["source"], "", cached.get("checks", []), cached.get("verified", False)
        pts = stored_points(symbol)
        return pts, "stored closes", "" if pts else "frozen preview, nothing stored", [], False
    seed_quote(symbol, quote)
    answered, errors = [], []
    for name, fn in PRICE_SOURCES:
        try:
            points = fn(symbol)
        except Exception as e:
            errors.append(f"{name}: {str(e)[:70]}")
            continue
        if points:
            answered.append((name, points))
        if len(answered) == 2:
            break
    note = "; ".join(errors)
    if not answered:
        points = stored_points(symbol)
        if cached and len(cached["points"]) > len(points):
            return cached["points"], cached["source"] + " (cached)", note, cached.get("checks", []), False
        return points, "stored closes", note, [], False

    source, points = answered[0]
    checks = []
    qc = _quote_check(points, quote)
    if qc:
        checks.append(qc)
    if len(answered) == 2:
        other_name, other = answered[1]
        gap, days = _compare(points, other)
        checks.append({"check": f"{source} vs {other_name} on {days} shared days", "ok": gap is not None and gap <= TOLERANCE_PCT,
                       "gap_pct": gap})
        if qc and not qc["ok"]:
            other_qc = _quote_check(other, quote)
            if other_qc and other_qc["ok"]:
                checks.append({"check": f"switched to {other_name}: it agrees with the live quote, {source} does not", "ok": True, "gap_pct": other_qc["gap_pct"]})
                source, points = other_name, other
                checks[0] = other_qc
    verified = bool(checks) and all(c["ok"] for c in checks)
    store_points(symbol, points, source)
    demo_cache.write(key, {"points": points, "source": source, "checks": checks, "verified": verified})
    return points, source, note, checks, verified


def _official(symbol, days=WINDOW_DAYS):
    since = (date.today() - timedelta(days=days)).isoformat()
    items = []
    for f in get_cached_filings(symbol):
        when = (f.get("sort_date") or f.get("date") or "")[:19]
        if when[:10] < since:
            continue
        tags = [t.strip() for t in (f.get("tags") or "").split(",") if t.strip()]
        items.append(_item("official", "NSE filing", f.get("title") or "Filing", f.get("pdf_link") or "",
                           _parse_iso(when.replace(" ", "T")), (f.get("description") or "").strip(), "IN", "en",
                           {"filing_id": f.get("id"), "filing_tags": tags[:4]}))
    return items


def _regulatory(limit=8):
    topics = sorted(reg_news.relevant_topics("compliance", []))
    items = []
    for d in reg_news.list_news(topics_any=topics, exclude_kinds=("recovery",), limit=limit):
        items.append(_item("regulatory", d["source"], d["title"], d["url"], _parse_iso(d["published_at"]),
                           d.get("excerpt") or "", "IN", "en", {"kind_label": d.get("kind_label", ""), "topics": d.get("topic_list", [])[:3]}))
    return items


def _dedupe(items):
    """Collapse the same story seen from several editions; remember every
    country it ran in."""
    by_key, order = {}, []
    for it in sorted(items, key=lambda i: i["published_at"], reverse=True):
        key = _norm_title(it["title"])[:90] or it["url"]
        if key in by_key:
            kept = by_key[key]
            for c in it["countries"]:
                if c not in kept["countries"]:
                    kept["countries"].append(c)
            continue
        by_key[key] = it
        order.append(it)
    return order


def _annotate(items, with_signals=True):
    today = date.today()
    for it in items:
        text = f"{it['title']} {it.get('excerpt', '')}"
        it["tags"] = factor_tags(text)
        try:
            event_day = date.fromisoformat(it["date"]) if it["date"] else today
        except ValueError:
            event_day = today
        it["signals"] = filing_hints(text, event_day) if with_signals else []
        it["reach"] = [COUNTRY_NAMES.get(c, c) for c in it["countries"]]
    return items


def gather(symbol, company_name, days=WINDOW_DAYS, x_bearer_token=""):
    """Fetch the live lanes in parallel. Each lane reports its own status so
    the page can say what was and was not reachable."""
    query = f'"{clean_name(company_name)}"'
    jobs, status = {}, {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for country, lang, hl, ceid, label in EDITIONS:
            # Outside India a short name is ambiguous ("ITC" is also a US trade
            # body), so foreign editions search for the Indian company explicitly.
            q = query if country == "IN" else f"{query} India"
            jobs[("press", label)] = pool.submit(_google_news, q, country, lang, hl, ceid, days)
        jobs[("social", "Reddit")] = pool.submit(_reddit, query, days)
        if x_bearer_token:
            jobs[("x", "X")] = pool.submit(_x_posts, company_name, x_bearer_token)
    lanes = {"press": [], "social": [], "x": []}
    for (lane, label), fut in jobs.items():
        try:
            got = fut.result()
            status[f"{lane}:{label}"] = {"ok": True, "count": len(got)}
            lanes[lane].extend(got)
        except Exception as e:  # one dead feed must not empty the page
            status[f"{lane}:{label}"] = {"ok": False, "error": str(e)[:140]}
    if not x_bearer_token:
        status["x:X"] = {"ok": False, "error": "Not configured. The X API is paid; set X_BEARER_TOKEN to enable this lane."}
    lanes["press"] = _dedupe(lanes["press"])
    return {k: _annotate(v) for k, v in lanes.items()}, status


# ── Composition ───────────────────────────────────────────────────────────

def industry_for(symbol, quote=None):
    """Industry from the curated profiles (so the sector brief exists), else the quote."""
    try:
        import industry_intel as ii
        for name, profile in ii.INDUSTRY_PROFILES.items():
            if any(l.get("symbol") == symbol for l in profile.get("leaders", [])):
                return name
    except Exception:
        pass
    return ((quote or {}).get("industry") or "").strip()


def _moves(points, items):
    """Days the stock moved more than the threshold, with the stories from
    that day and the day before (press and the company's own filings)."""
    by_day = {}
    for it in items:
        if it["lane"] in ("press", "official") and it["date"]:
            by_day.setdefault(it["date"], []).append(it)
    out = []
    for p in points:
        if abs(p["pct"]) < MOVE_THRESHOLD:
            continue
        day = date.fromisoformat(p["date"])
        stories = by_day.get(p["date"], []) + by_day.get((day - timedelta(days=1)).isoformat(), [])
        stories = sorted(stories, key=lambda i: (i["lane"] != "official", -len(i["countries"])))[:3]
        out.append({**p, "stories": [{"id": s["id"], "title": s["title"], "source": s["source"], "lane": s["lane"]} for s in stories]})
    return sorted(out, key=lambda m: m["date"], reverse=True)


def _signals(items):
    """Filing hints across every story, grouped by obligation."""
    groups = {}
    for it in items:
        for s in it.get("signals", []):
            g = groups.setdefault(s["rule_id"], {"rule_id": s["rule_id"], "name": s["name"], "form": s["form"], "law": s["law"],
                                                  "certain": s["certain"], "due": s["due"], "latest": it["date"], "stories": []})
            g["certain"] = g["certain"] or s["certain"]
            if it["date"] > g["latest"]:
                g["latest"], g["due"] = it["date"], s["due"]
            if len(g["stories"]) < 4:
                g["stories"].append({"id": it["id"], "title": it["title"], "source": it["source"], "date_label": it["date_label"], "lane": it["lane"]})
    return sorted(groups.values(), key=lambda g: (not g["certain"], g["latest"]), reverse=False)[:8]


def _brief(company, quote, moves, press, official, industry, signals, key, model):
    """A structured AI brief grounded only in the items passed in. Returns a
    dict, or {"error": reason} when no key or the model fails."""
    if not key:
        return {"error": "AI brief needs a Gemini key (GEMINI_API_KEY or the workspace setting)."}
    from agents.client import call_json
    rows = []
    for it in (official[:6] + press[:14]):
        rows.append(f"[{it['id']}] {it['date_label']} · {it['source']} · {it['title']}")
    move_lines = [f"{m['label']}: {m['pct']:+.1f}% (close {m['close']})" + (f" — {m['stories'][0]['title']}" if m["stories"] else "")
                  for m in moves[:6]]
    drivers = (industry.get("profile") or {}).get("drivers") or []
    risks = (industry.get("profile") or {}).get("risks") or []
    filings = [f"{s['form']} ({s['name']}, {s['due']})" for s in signals[:5]]
    q = quote or {}
    prompt = f"""You are a research analyst writing for a company secretary and the finance team of {company['name']} (NSE: {company['symbol']}), an Indian listed company in {company.get('industry') or 'its sector'}.
Use ONLY the material below. Cite stories by their bracketed ids. Do not invent numbers, events or sources. Prefer the company's own filings over press.

Price: last {q.get('lastPrice')} ({q.get('pChange')}% today; 30-day {q.get('perChange30d')}%; 52-week high {q.get('yearHigh')}, low {q.get('yearLow')}).
Notable daily moves in the last 30 days:
{chr(10).join(move_lines) or 'none above threshold'}

Stories (id · date · source · headline):
{chr(10).join(rows)}

Sector drivers (curated): {'; '.join(drivers[:6])}
Sector risks (curated): {'; '.join(risks[:6])}
Filings the news may trigger (from the rulebook): {'; '.join(filings) or 'none detected'}

Return JSON with exactly these keys:
{{"headline": "one sentence on what is driving the stock right now",
 "why_moved": [{{"point": "...", "refs": ["id"]}}],
 "could_move": [{{"factor": "short label", "direction": "up|down|either", "point": "...", "refs": ["id"]}}],
 "watch_next": [{{"when": "date or window", "what": "..."}}],
 "filing_watch": [{{"form": "...", "why": "one sentence tying a story to the filing", "refs": ["id"]}}]}}
Keep each list to 3-5 items, each point under 30 words, plain English, no hype."""
    res = call_json(prompt, role="monitor", model=model, max_output_tokens=1800, temperature=0.2)
    if not res.get("ok") or not isinstance(res.get("data"), dict):
        return {"error": f"AI brief unavailable ({res.get('error') or 'bad response'})."}
    data = res["data"]
    data["model"] = res.get("model", model)
    return data


def _quote(symbol):
    try:
        from stock_prices import fetch_stock_quote
        return fetch_stock_quote(symbol) or {}
    except Exception as e:
        print(f"[NEWS] quote failed for {symbol}: {e}")
        return {}


def build_desk(symbol, company_name):
    """Everything the page needs, assembled from parallel fetches."""
    symbol = symbol.upper()
    key, model = _gemini()
    quote = _quote(symbol)
    industry = industry_for(symbol, quote)
    company = {"symbol": symbol, "name": clean_name(company_name or quote.get("companyName") or symbol), "industry": industry}
    filings_rows = get_cached_filings(symbol)[:10]

    # The series goes first and alone: the price-intelligence engine also calls
    # Yahoo, and two bursts at once get rate-limited.
    series, series_source, series_note, series_checks, series_verified = price_series(symbol, quote)
    series_status = {"ok": bool(series), "count": len(series), "source": series_source, "error": series_note, "verified": series_verified}
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_lanes = pool.submit(gather, symbol, company["name"], WINDOW_DAYS, os.environ.get("X_BEARER_TOKEN", "").strip())
        f_intel = pool.submit(_safe_intel, symbol, company["name"], quote, filings_rows, key, model)
        f_industry = pool.submit(_safe_industry, industry, key)
    lanes, status = f_lanes.result()
    status["price:" + (series_source or "history")] = series_status
    intel = f_intel.result()
    industry_brief = f_industry.result()

    official = _annotate(_official(symbol))
    regulatory = _annotate(_regulatory(), with_signals=False)
    # Sector, macro and global headlines from the two existing engines, minus
    # anything already in the press lane.
    seen = {_norm_title(p["title"])[:90] for p in lanes["press"]}
    industry_items = []
    for bucket, label in (("sector", "Sector"), ("ma", "Deals"), ("regulatory", "Regulatory"), ("global", "Global")):
        for n in (industry_brief.get("news") or {}).get(bucket, []) or []:
            k = _norm_title(n.get("title"))[:90]
            if k in seen:
                continue
            seen.add(k)
            industry_items.append(_item("industry", n.get("source") or "Google News", n.get("title") or "", n.get("url") or "",
                                        _parse_rfc2822(n.get("published")) or _parse_iso(n.get("published")), "", "", "en", {"scope": label}))
    for n in intel.get("news") or []:
        if n.get("scope") in ("sector", "macro", "global"):
            k = _norm_title(n.get("title"))[:90]
            if k in seen:
                continue
            seen.add(k)
            industry_items.append(_item("industry", n.get("source") or "Google News", n.get("title") or "", n.get("url") or "",
                                        _parse_rfc2822(n.get("published")) or _parse_iso(n.get("published")), "", "", "en",
                                        {"scope": n["scope"].capitalize()}))
    industry_items = _annotate(sorted(industry_items, key=lambda i: i["published_at"], reverse=True)[:20], with_signals=False)

    everything = official + lanes["press"] + lanes["social"] + lanes["x"]
    moves = _moves(series, everything)
    signals = _signals(everything)
    brief = _brief(company, quote, moves, lanes["press"], official, industry_brief, signals, key, model)
    profile = industry_brief.get("profile") or {}
    return {
        "symbol": symbol, "company": company,
        "quote": {k: quote.get(k) for k in ("lastPrice", "change", "pChange", "previousClose", "dayHigh", "dayLow",
                                            "yearHigh", "yearLow", "perChange30d", "perChange365d", "totalTradedVolume", "lastUpdateTime")},
        "series": series, "series_source": series_source, "series_note": series_note, "series_checks": series_checks,
        "series_verified": series_verified, "moves": moves,
        "lanes": {"official": official, "press": lanes["press"], "social": lanes["social"], "x": lanes["x"],
                  "regulatory": regulatory, "industry": industry_items},
        "intel": {"summary": intel.get("summary", ""), "ai_powered": bool(intel.get("ai_powered")), "model_used": intel.get("model_used", ""),
                  "rating": intel.get("rating") or {}, "research": (intel.get("research") or [])[:4]},
        "industry": {"name": industry, "tagline": profile.get("tagline", ""), "drivers": (profile.get("drivers") or [])[:6],
                     "risks": (profile.get("risks") or [])[:6], "regulators": (profile.get("regulators") or [])[:6],
                     "summary": industry_brief.get("summary", ""), "ai_powered": bool(industry_brief.get("ai_powered"))},
        "signals": signals, "brief": brief, "status": status,
        "counts": {k: len(v) for k, v in (("official", official), ("press", lanes["press"]), ("social", lanes["social"]),
                                           ("x", lanes["x"]), ("regulatory", regulatory), ("industry", industry_items))},
        "fetched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _safe_intel(symbol, name, quote, filings, key, model):
    if not quote:
        return {}
    try:
        from price_intel import get_price_intel
        return get_price_intel(symbol, name, quote, filings, gemini_key=key, gemini_model=model) or {}
    except Exception as e:
        print(f"[NEWS] price intel failed for {symbol}: {e}")
        return {}


def _safe_industry(industry, key):
    if not industry:
        return {}
    try:
        from industry_intel import get_industry_intel
        out = get_industry_intel(industry, gemini_key=key) or {}
        return {} if out.get("error") else out
    except Exception as e:
        print(f"[NEWS] industry intel failed for {industry}: {e}")
        return {}


# ── Cache front door ──────────────────────────────────────────────────────

def desk(symbol, company_name, refresh=False):
    """Serve the desk from the shared response cache; build it when missing,
    stale or explicitly refreshed. In frozen demo mode never go outbound:
    return what is cached, or None."""
    symbol = symbol.upper()
    key = f"news.desk:{symbol}"
    hit = demo_cache.read(key)
    if hit and not refresh and hit["age"] < DESK_TTL:
        return {**hit["value"], "cached": True, "age": int(hit["age"])}
    if demo_cache.frozen():
        return {**hit["value"], "cached": True, "age": int(hit["age"]), "frozen": True} if hit else None
    with _build_locks_guard:
        lock = _build_locks.setdefault(symbol, threading.Lock())
    with lock:  # two tabs refreshing the same company share one build
        hit = demo_cache.read(key)
        if hit and not refresh and hit["age"] < DESK_TTL:
            return {**hit["value"], "cached": True, "age": int(hit["age"])}
        value = build_desk(symbol, company_name)
        demo_cache.write(key, value)
        return {**value, "cached": False, "age": 0}


if __name__ == "__main__":  # quick manual check: python3 company_news.py HINDUNILVR "Hindustan Unilever"
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "HINDUNILVR"
    name = sys.argv[2] if len(sys.argv) > 2 else "Hindustan Unilever"
    out = build_desk(sym, name)
    print(json.dumps({k: out[k] for k in ("company", "quote", "counts", "status")}, indent=1, default=str))
    print("moves:", [(m["label"], m["pct"], len(m["stories"])) for m in out["moves"]])
    print("signals:", [(s["form"], s["due"], len(s["stories"])) for s in out["signals"]])
    print("brief:", json.dumps(out["brief"], indent=1)[:1500])
    print("industry:", out["industry"]["name"], out["industry"]["drivers"][:3])
