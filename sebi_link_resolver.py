"""SEBI Legal Link Resolver.

SEBI rebuilds the URL of each regulation PDF/HTML page every time the
regulation is amended (the slug changes from `...-last-amended-on-june-14-2023-_34259.html`
to `...-last-amended-on-january-22-2026-_99375.html`). Hardcoding any one
URL guarantees a 404 the next time SEBI amends.

This module scrapes SEBI's "Acts & Regulations" index once, finds the
current URL for each regulation by matching a stable slug substring, and
caches the result in-memory. If discovery ever fails, callers get the
stable index page URL — which always works — instead of a 404.
"""

import re
import time
import threading
import requests
from typing import Dict, Optional


SEBI_REGULATIONS_INDEX = (
    "https://www.sebi.gov.in/sebiweb/home/HomeAction.do"
    "?doListing=yes&sid=1&ssid=3&smid=0"
)

# Stable substring → canonical mandate-source key. We match on slug fragments
# that survive across amendments (the regulation name itself doesn't change,
# only the dated suffix).
REGULATION_SLUGS = {
    "lodr": "listing-obligations-and-disclosure-requirements-regulations-2015",
    "pit":  "prohibition-of-insider-trading-regulations-2015",
}

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_cache: Dict[str, str] = {}
_cache_at: float = 0.0
_cache_ttl = 24 * 60 * 60  # refresh once per day
# Failures are remembered too. Without this, an unreachable SEBI meant every
# single caller re-attempted a 15s fetch: enriching 514 obligations cost
# 2.2 hours and hung /api/compliance outright.
_last_attempt_at: float = 0.0
_failure_backoff = 10 * 60  # after a failed scrape, don't retry for 10 min
_lock = threading.Lock()


def _scrape_index() -> Dict[str, str]:
    """Hit SEBI's regulations index and pull the latest URL for each known slug.

    Direct requests to sebi.gov.in time out from most hosts we run on, which
    left this permanently falling back to the index URL. Firecrawl reaches it
    reliably, so it is tried first when configured; the direct request stays as
    the fallback so the module still works without Firecrawl.
    """
    body = ""

    try:
        import firecrawl_client as fc
        if fc.available():
            data = fc.scrape(SEBI_REGULATIONS_INDEX, formats=["markdown", "links"],
                             only_main=False, timeout=90)
            if data:
                # `links` is already-resolved absolute URLs — better than
                # regexing hrefs out of rendered markdown.
                found = [u for u in (data.get("links") or [])
                         if "/legal/regulations/" in u and u.endswith(".html")]
                if found:
                    return _pick_by_slug(found)
                body = data.get("markdown") or ""
    except Exception as e:
        print(f"[SEBI-LINK] firecrawl attempt failed: {str(e)[:120]}")

    if not body:
        try:
            r = requests.get(
                SEBI_REGULATIONS_INDEX,
                headers={"User-Agent": _USER_AGENT, "Referer": "https://www.sebi.gov.in/"},
                timeout=15,
            )
        except Exception as e:
            print(f"[SEBI-LINK] index fetch failed: {e}")
            return {}

        if r.status_code != 200:
            print(f"[SEBI-LINK] index returned {r.status_code}")
            return {}
        body = r.text or ""
    urls = re.findall(
        r'href="(https://www\.sebi\.gov\.in/legal/regulations/[^"]+\.html)"',
        body,
    )

    out: Dict[str, str] = {}
    for key, slug in REGULATION_SLUGS.items():
        # Pick the URL containing the slug; if multiple (unlikely), take the
        # most recent by lexicographic comparison of the year-month folder.
        candidates = [u for u in urls if slug in u]
        if not candidates:
            continue
        out[key] = sorted(candidates, reverse=True)[0]
    return out


def _pick_by_slug(urls) -> Dict[str, str]:
    """Choose the most recent URL for each known regulation slug."""
    out: Dict[str, str] = {}
    for key, slug in REGULATION_SLUGS.items():
        candidates = [u for u in urls if slug in u]
        if candidates:
            out[key] = sorted(candidates, reverse=True)[0]
    return out


def _refresh_cache_locked():
    global _cache, _cache_at, _last_attempt_at
    _last_attempt_at = time.time()          # recorded whether or not we succeed
    fresh = _scrape_index()
    if fresh:
        _cache = fresh
        _cache_at = time.time()
        print(f"[SEBI-LINK] cache refreshed: {list(fresh.keys())}")
    else:
        print(f"[SEBI-LINK] discovery failed — serving the index URL for the "
              f"next {_failure_backoff // 60} minutes")


def _should_attempt(now: float) -> bool:
    """Whether a (re)discovery is worth trying right now."""
    if _cache and (now - _cache_at) <= _cache_ttl:
        return False                                   # fresh cache, nothing to do
    return (now - _last_attempt_at) > _failure_backoff  # else respect the backoff


def get_regulation_url(key: str) -> str:
    """Return the current URL for a regulation by stable key (e.g. 'lodr', 'pit').

    Falls back to SEBI's regulations index page — which is always valid — when
    discovery fails. A failed attempt is remembered, so callers in a loop pay
    the network cost at most once per backoff window rather than once each.
    """
    now = time.time()
    if _should_attempt(now):
        with _lock:
            if _should_attempt(time.time()):
                _refresh_cache_locked()
    return _cache.get(key) or SEBI_REGULATIONS_INDEX


def warm_cache_async():
    """Fire the resolver in a daemon thread on app startup so the first
    /api/compliance request doesn't pay the SEBI round-trip."""
    def _w():
        try:
            with _lock:
                _refresh_cache_locked()
        except Exception as e:
            print(f"[SEBI-LINK] warmup failed: {e}")
    threading.Thread(target=_w, name="sebi-link-warmup", daemon=True).start()
