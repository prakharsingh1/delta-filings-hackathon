"""SEBI Enforcement History — scrape orders/penalties from sebi.gov.in by company name.

For investors and compliance teams, the most meaningful signal isn't whether a
blue-chip filed Reg 33 on time (they always do); it's whether SEBI has ever
taken enforcement action against the company. This module queries SEBI's
public orders archive across the substantive categories:

  - smid=2   Orders of Chairperson/Members          (substantive enforcement)
  - smid=3   Settlement Orders                      (settled enforcement)
  - smid=6   Orders of AO (Adjudicating Officer)    (penalty orders)
  - smid=133 Orders of ED / CGM (Quasi-Judicial)    (departmental orders)
  - smid=1   Orders of SAT                          (appellate, often originated from SEBI action)

The orders listing supports a `search` POST parameter that filters the table
server-side. We use the watchlist company name as the query, then store every
hit. Each row is tagged with a confidence level based on how the company name
appears in the order title:

  - 'high'   → company name is the named subject ("in respect of <NAME>" / "in the matter of <NAME>")
  - 'medium' → company name appears in the title but as context, not subject
  - 'low'    → query matched but title doesn't contain the full name (likely a substring hit)

The UI can then surface only `high` for the headline count and show
`medium`/`low` under "Possibly related".
"""

import os
import re
import time
import sqlite3
import threading
import requests
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple

from database import get_connection


_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# SEBI order categories worth tracking for company-level enforcement history.
SEBI_ORDER_CATEGORIES = [
    {"smid": 2,   "key": "chairperson",   "label": "Orders of Chairperson/Members"},
    {"smid": 3,   "key": "settlement",    "label": "Settlement Orders"},
    {"smid": 6,   "key": "adjudication",  "label": "Adjudication Orders (AO)"},
    {"smid": 133, "key": "quasi_judicial","label": "Orders of ED/CGM (Quasi-Judicial)"},
    {"smid": 1,   "key": "sat",           "label": "Orders of SAT"},
]

_SEBI_BASE = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do"

# Listing row regex — matches <tr><td>DATE</td><td><a href="URL" ... title="TITLE">
_ROW_PATTERN = re.compile(
    r"<tr[^>]*>\s*<td>([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})</td>\s*"
    r'<td><a href="([^"]+)"[^>]*title="([^"]+)"',
    re.DOTALL,
)
_TOTAL_PATTERN = re.compile(r"of (\d+) records", re.IGNORECASE)


# ── HTTP session ──

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.sebi.gov.in/enforcement/orders.html",
        })
        # Warm up by hitting the orders index — gets the JSESSIONID cookie.
        try:
            s.get("https://www.sebi.gov.in/enforcement/orders.html", timeout=15)
        except Exception as e:
            print(f"[SEBI] warmup failed: {e}")
        _session = s
    return _session


def _parse_sebi_date(s: str) -> Optional[date]:
    s = s.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ── Scraping ──

def _fetch_listing_page(smid: int, query: str, page: int) -> Tuple[List[Dict], int]:
    """Fetch one page of SEBI orders for a given category and search query.

    Returns (orders, total_records). `total_records` reflects the server-side
    filtered total for `query` — useful for deciding whether to paginate.
    """
    sess = _get_session()
    url = f"{_SEBI_BASE}?doListing=yes&sid=2&ssid=9&smid={smid}"
    try:
        r = sess.post(
            url,
            data={"search": query, "nextValue": str(page)},
            timeout=20,
        )
    except Exception as e:
        print(f"[SEBI] fetch failed smid={smid} q={query!r} page={page}: {e}")
        return ([], 0)

    if r.status_code != 200:
        print(f"[SEBI] non-200 (status={r.status_code}) smid={smid} q={query!r}")
        return ([], 0)

    body = r.text or ""
    rows = _ROW_PATTERN.findall(body)
    total_match = _TOTAL_PATTERN.search(body)
    total = int(total_match.group(1)) if total_match else 0

    orders = []
    for date_str, order_url, title in rows:
        d = _parse_sebi_date(date_str)
        orders.append({
            "order_date": d.isoformat() if d else None,
            "url": order_url.strip(),
            "title": _clean_title(title),
        })
    return (orders, total)


def _clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _classify_confidence(title: str, name: str, symbol: str) -> str:
    """Decide how confidently a SEBI order title refers to *this* company.

    The strongest signal is the phrase "in respect of <COMPANY>" or
    "in the matter of <COMPANY>", followed by the company name appearing
    anywhere in the title. The query already restricts results to titles
    containing the search string, so this is purely a re-ranking step.
    """
    t_low = title.lower()
    name_low = (name or "").lower().strip()
    sym_low = (symbol or "").lower().strip()

    # Strip common corporate suffixes for fuzzier matching
    name_root = re.sub(r"\b(limited|ltd\.?|pvt\.?|private)\b", "", name_low).strip()
    name_root = re.sub(r"\s+", " ", name_root)

    if not name_root:
        return "low"

    if (f"in respect of {name_root}" in t_low or
            f"in the matter of {name_root}" in t_low or
            f"against {name_root}" in t_low):
        return "high"
    if name_root in t_low:
        return "medium"
    if sym_low and sym_low in t_low:
        return "medium"
    return "low"


def fetch_orders_for_company(
    symbol: str,
    name: str,
    max_pages_per_category: int = 10,
) -> List[Dict]:
    """Hit every SEBI order category and return a deduplicated list of hits
    likely related to `name`. Each entry: {order_date, url, title, category,
    category_label, confidence}.

    `max_pages_per_category=10` is a hard cap (≈250 records per category).
    The loop exits early when the server-reported total has been fully
    paged through, so for typical blue-chips with <30 orders we stop after
    the first page anyway. The cap protects us from runaway pagination
    when a search query like "Tata" matches thousands of unrelated rows.
    """
    if not name:
        return []

    query = name.strip()
    # SEBI search is title-substring; trimming corporate suffixes broadens
    # the recall (e.g. "Marico" matches more rows than "Marico Limited").
    short_query = re.sub(
        r"\b(limited|ltd\.?|private|pvt\.?)\b",
        "",
        query,
        flags=re.IGNORECASE,
    ).strip()
    short_query = re.sub(r"\s+", " ", short_query) or query

    seen_urls = set()
    out: List[Dict] = []

    for cat in SEBI_ORDER_CATEGORIES:
        for page in range(1, max_pages_per_category + 1):
            try:
                orders, total = _fetch_listing_page(cat["smid"], short_query, page)
            except Exception as e:
                print(f"[SEBI] page error: {e}")
                break

            if not orders:
                break

            for o in orders:
                if o["url"] in seen_urls:
                    continue
                seen_urls.add(o["url"])
                o["category"] = cat["key"]
                o["category_label"] = cat["label"]
                o["confidence"] = _classify_confidence(o["title"], name, symbol)
                out.append(o)

            # Stop paginating the moment we've fetched everything available.
            if page * 25 >= total:
                break

            time.sleep(0.4)  # be polite to SEBI

    return out


# ── Database ──

def init_enforcement_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS sebi_enforcement_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            order_date TEXT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            category TEXT NOT NULL,
            category_label TEXT NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'low',
            fetched_at TEXT NOT NULL,
            UNIQUE(symbol, url)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_sebi_enf_symbol ON sebi_enforcement_actions(symbol)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sebi_enf_date ON sebi_enforcement_actions(order_date)")

    # Per-symbol "last fully checked" timestamp. Without this we can't tell
    # the difference between "never scraped" and "scraped and found zero",
    # which causes the dashboard to re-trigger the worker forever for clean
    # companies like MARICO that genuinely have no SEBI history.
    c.execute("""
        CREATE TABLE IF NOT EXISTS sebi_enforcement_meta (
            symbol TEXT PRIMARY KEY,
            last_checked_at TEXT NOT NULL,
            last_query TEXT,
            row_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def get_last_checked(symbol: str) -> Optional[str]:
    init_enforcement_db()
    conn = get_connection()
    row = conn.execute(
        "SELECT last_checked_at FROM sebi_enforcement_meta WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    conn.close()
    return row["last_checked_at"] if row else None


# Track in-flight background refreshes so the dashboard's auto-backfill loop
# doesn't kick off a second fetch while the first is still running.
_inflight_refreshes: set = set()
_inflight_lock = threading.Lock()


def refresh_enforcement_async(symbol: str, name: str) -> bool:
    """Fire-and-forget background refresh. Returns True if a worker was
    started, False if one was already in flight for this symbol."""
    sym = symbol.upper()
    with _inflight_lock:
        if sym in _inflight_refreshes:
            return False
        _inflight_refreshes.add(sym)

    def _worker():
        try:
            refresh_enforcement_for_company(sym, name or sym)
        except Exception as e:
            print(f"[SEBI] async refresh failed for {sym}: {e}")
        finally:
            with _inflight_lock:
                _inflight_refreshes.discard(sym)

    t = threading.Thread(target=_worker, name=f"sebi-refresh-{sym}", daemon=True)
    t.start()
    return True


def refresh_enforcement_for_company(symbol: str, name: str) -> int:
    """Fetch + persist SEBI enforcement actions for a company. Returns # new
    rows inserted. Always stamps `last_checked_at` so a clean record (zero
    hits) doesn't get re-scraped on every dashboard load."""
    init_enforcement_db()
    orders = fetch_orders_for_company(symbol, name)

    conn = get_connection()
    now = datetime.now().isoformat()
    inserted = 0
    for o in orders:
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO sebi_enforcement_actions
                   (symbol, order_date, title, url, category, category_label,
                    confidence, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol, o.get("order_date"), o["title"], o["url"],
                 o["category"], o["category_label"], o["confidence"], now),
            )
            if cur.rowcount:
                inserted += 1
        except sqlite3.Error as e:
            print(f"[SEBI] insert error for {symbol}: {e}")

    # Always record that we checked, even when there were zero hits.
    conn.execute(
        """INSERT INTO sebi_enforcement_meta (symbol, last_checked_at, last_query, row_count)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(symbol) DO UPDATE SET
               last_checked_at = excluded.last_checked_at,
               last_query = excluded.last_query,
               row_count = excluded.row_count""",
        (symbol, now, name, len(orders)),
    )
    conn.commit()
    conn.close()
    return inserted


def get_enforcement_for_company(symbol: str) -> Dict:
    """Return enforcement summary + full list for one company.

    Summary fields: total, high_confidence, last_action_date.
    The UI uses `high_confidence` for the headline count and shows the rest
    as "Possibly related" so we never claim a company is implicated when the
    name match is ambiguous.
    """
    init_enforcement_db()
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM sebi_enforcement_actions WHERE symbol = ? "
        "ORDER BY order_date DESC, id DESC",
        (symbol,),
    ).fetchall()
    conn.close()

    actions = [dict(r) for r in rows]
    high = [a for a in actions if a.get("confidence") == "high"]
    medium = [a for a in actions if a.get("confidence") == "medium"]
    low = [a for a in actions if a.get("confidence") == "low"]

    last_action_date = high[0]["order_date"] if high else None

    by_category: Dict[str, int] = {}
    for a in high:
        by_category[a.get("category", "")] = by_category.get(a.get("category", ""), 0) + 1

    return {
        "symbol": symbol,
        "total": len(actions),
        "high_confidence": len(high),
        "medium_confidence": len(medium),
        "low_confidence": len(low),
        "last_action_date": last_action_date,
        "by_category": by_category,
        "actions": actions,
    }


def get_enforcement_summaries(symbols: List[str]) -> Dict[str, Dict]:
    """Return per-symbol summaries for the dashboard (no full action list).

    `last_checked_at` distinguishes "never scraped" (None) from "scraped and
    found zero" (a timestamp). The dashboard uses this to avoid re-firing a
    background refresh for clean companies on every poll."""
    if not symbols:
        return {}
    init_enforcement_db()
    conn = get_connection()
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"""SELECT symbol, confidence, COUNT(*) AS cnt, MAX(order_date) AS last_dt
            FROM sebi_enforcement_actions
            WHERE symbol IN ({placeholders})
            GROUP BY symbol, confidence""",
        tuple(symbols),
    ).fetchall()
    meta_rows = conn.execute(
        f"SELECT symbol, last_checked_at FROM sebi_enforcement_meta "
        f"WHERE symbol IN ({placeholders})",
        tuple(symbols),
    ).fetchall()
    conn.close()

    out: Dict[str, Dict] = {s: {"total": 0, "high": 0, "medium": 0, "low": 0,
                                "last_action_date": None,
                                "last_checked_at": None} for s in symbols}
    for r in rows:
        sym = r["symbol"]
        conf = r["confidence"] or "low"
        cnt = r["cnt"] or 0
        out[sym]["total"] += cnt
        if conf in ("high", "medium", "low"):
            out[sym][conf] = cnt
        if conf == "high" and r["last_dt"]:
            prev = out[sym]["last_action_date"]
            if not prev or r["last_dt"] > prev:
                out[sym]["last_action_date"] = r["last_dt"]
    for r in meta_rows:
        out[r["symbol"]]["last_checked_at"] = r["last_checked_at"]
    return out
