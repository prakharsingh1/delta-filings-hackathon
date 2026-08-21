"""Thin Firecrawl wrapper for the sources Delta cannot reach directly.

SEBI's own site times out from here (15s, every attempt), NSE rate-limits
aggressive callers, and several company sites publish nothing usable. Firecrawl
fetches those reliably, so this is used where a direct request has already
proven unreliable — not as a general replacement for the existing scrapers.
"""

import json
import os
from typing import Any, Dict, List, Optional

import requests

BASE = "https://api.firecrawl.dev/v2"
DEFAULT_TIMEOUT = 90


def api_key() -> str:
    key = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()
    if key:
        return key
    try:
        from dotenv import dotenv_values
        return (dotenv_values(os.path.join(os.path.dirname(__file__), ".env"))
                .get("FIRECRAWL_API_KEY") or "").strip()
    except Exception:
        return ""


def available() -> bool:
    return bool(api_key())


def _post(path: str, payload: Dict, timeout: int = DEFAULT_TIMEOUT) -> Optional[Dict]:
    key = api_key()
    if not key:
        return None
    try:
        r = requests.post(f"{BASE}{path}", json=payload, timeout=timeout,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"})
    except Exception as e:
        print(f"[FIRECRAWL] {path} failed: {str(e)[:140]}")
        return None
    if r.status_code != 200:
        print(f"[FIRECRAWL] {path} HTTP {r.status_code}: {r.text[:160]}")
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    return body.get("data") if body.get("success") else None


def scrape(url: str, formats: Optional[List[str]] = None,
           only_main: bool = True, timeout: int = DEFAULT_TIMEOUT) -> Optional[Dict]:
    """Fetch one URL as clean markdown (plus links/metadata when asked)."""
    return _post("/scrape", {
        "url": url,
        "formats": formats or ["markdown"],
        "onlyMainContent": only_main,
    }, timeout=timeout)


def search(query: str, limit: int = 5, scrape_results: bool = False,
           timeout: int = DEFAULT_TIMEOUT) -> List[Dict]:
    """Web search. Returns [{title, url, description, markdown?}]."""
    payload: Dict[str, Any] = {"query": query, "limit": max(1, min(limit, 10))}
    if scrape_results:
        payload["scrapeOptions"] = {"formats": ["markdown"], "onlyMainContent": True}
    data = _post("/search", payload, timeout=timeout)
    if not data:
        return []
    # The API has returned either a bare list or {web: [...]} across versions.
    results = data.get("web") if isinstance(data, dict) else data
    out = []
    for r in (results or [])[:limit]:
        if not isinstance(r, dict) or not r.get("url"):
            continue
        out.append({
            "title": str(r.get("title", ""))[:200],
            "url": str(r.get("url", ""))[:500],
            "description": str(r.get("description", ""))[:400],
            "markdown": (r.get("markdown") or "")[:4000],
        })
    return out
