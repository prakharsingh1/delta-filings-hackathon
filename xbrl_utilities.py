"""Track the latest NSE XBRL filing utilities / taxonomies.

NSE silently updates the XBRL utilities at
https://www.nseindia.com/static/companies-listing/xbrl-information with no
notification. Companies that file using a stale utility get objections from the
exchange. This module scrapes that page, parses each taxonomy's download link
(which encodes its publish timestamp), and exposes the current versions with a
"recently updated" flag so a CS always files with the latest format.
"""

import re
import time
from datetime import datetime
from typing import Dict, List

import requests

_PAGE = "https://www.nseindia.com/static/companies-listing/xbrl-information"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_LINK_RE = re.compile(
    r'https?://nsearchives\.nseindia\.com/+web/mediaattachment/[^\s"\'<>]+\.zip', re.I)
_TS_RE = re.compile(r'(\d{14})')          # YYYYMMDDHHMMSS upload stamp
_DATEPART_RE = re.compile(r'_?\d{8,14}')  # any 8-14 digit run in the filename

# Cache — utilities change infrequently; 6h keeps NSE traffic minimal.
_CACHE: Dict = {"at": 0.0, "data": None}
_TTL = 6 * 3600
# A utility updated within this many days is flagged "NEW".
_NEW_WINDOW_DAYS = 45


def _categorise(name: str) -> str:
    n = name.lower()
    if "integrated filing" in n:
        return "Integrated Filing"
    if "buyback" in n:
        return "Buyback"
    if "shareholding" in n or n.startswith("shp"):
        return "Shareholding"
    if any(k in n for k in ("board meeting", "prior intimation", "outcome", "reg 30", "reg60", "reg 60")):
        return "Board Meeting / Events"
    if any(k in n for k in ("rating", "default", "interest payment", "redemption", "debt", "reg 50", "regulation 50", "ncd")):
        return "Debt / NCD"
    if any(k in n for k in ("reit", "invit")):
        return "REIT / InvIT"
    if any(k in n for k in ("ipo", "preferential", "allotment", "fccb", "adr", "gdr", "rights", "listing")):
        return "Issuance / Listing"
    if any(k in n for k in ("trading window", "insider", "sast", "pledge")):
        return "Insider / Trading Window"
    if any(k in n for k in ("governance", "corp", "secretarial", "investor complaint", "rpt", "related party", "voting")):
        return "Governance"
    return "Other"


def _parse(url: str) -> Dict:
    fname = url.rsplit("/", 1)[-1][:-4]                # strip .zip
    stamps = _TS_RE.findall(fname)
    published = None
    if stamps:
        try:
            published = datetime.strptime(stamps[-1], "%Y%m%d%H%M%S")
        except ValueError:
            published = None
    name = _DATEPART_RE.sub("", fname).replace("_", " ").strip()
    name = re.sub(r"\s+", " ", name)
    return {
        "name": name or fname,
        "category": _categorise(name),
        "published": published.strftime("%Y-%m-%d") if published else "",
        "published_label": published.strftime("%d %b %Y") if published else "—",
        "url": url.replace("//web/", "/web/"),
        "_dt": published,
    }


def _fetch_page() -> str:
    """GET the XBRL information page. Plain request first; on failure, retry via
    the NSE-warmed session (fetch.py) which carries cookies for cloud IPs."""
    try:
        r = requests.get(_PAGE, headers={"User-Agent": _UA,
                                         "Accept": "text/html"}, timeout=15)
        if r.status_code == 200 and "nsearchives" in r.text:
            return r.text
    except Exception as e:  # noqa: BLE001
        print(f"[XBRL] plain fetch failed: {e}")
    try:
        from fetch import SESSION, _warm_session
        _warm_session()
        r = SESSION.get(_PAGE, timeout=20)
        if r.status_code == 200:
            return r.text
    except Exception as e:  # noqa: BLE001
        print(f"[XBRL] session fetch failed: {e}")
    return ""


def get_xbrl_utilities(force: bool = False) -> Dict:
    """Return the current NSE XBRL utilities, newest first, grouped by category.
    Cached 6h. Each item: name, category, published date, url, is_new."""
    now = time.time()
    if not force and _CACHE["data"] and (now - _CACHE["at"]) < _TTL:
        return _CACHE["data"]

    html = _fetch_page()
    links = sorted(set(_LINK_RE.findall(html)))
    items = [_parse(u) for u in links]

    # Dedupe by name → keep the most recently published version.
    best: Dict[str, Dict] = {}
    for it in items:
        key = it["name"].lower()
        cur = best.get(key)
        if not cur or (it["_dt"] and (not cur["_dt"] or it["_dt"] > cur["_dt"])):
            best[key] = it
    deduped = list(best.values())
    deduped.sort(key=lambda x: (x["_dt"] or datetime.min), reverse=True)

    today = datetime.now()
    for it in deduped:
        it["is_new"] = bool(it["_dt"] and (today - it["_dt"]).days <= _NEW_WINDOW_DAYS)
        it.pop("_dt", None)

    latest_label = deduped[0]["published_label"] if deduped else "—"
    payload = {
        "utilities": deduped,
        "count": len(deduped),
        "new_count": sum(1 for it in deduped if it["is_new"]),
        "latest_update": latest_label,
        "source": _PAGE,
        "fetched_at": datetime.now().isoformat(),
        "ok": bool(deduped),
    }
    if deduped:                       # only cache a good result
        _CACHE.update(at=now, data=payload)
    return payload
