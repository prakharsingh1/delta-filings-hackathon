"""Fill every cache before a demo, so nothing is fetched live on stage.

Run this once with the network available:

    python3 prewarm_demo.py

Then start the server with DELTA_DEMO_FREEZE=1 and no page will make an
outbound call — every expensive endpoint is served from SQLite.
"""

import os
import sys
import time

os.environ.pop("DELTA_DEMO_FREEZE", None)   # warming must be allowed to fetch

import demo_cache as dc
from database import get_watchlist

SYMBOLS = [w["symbol"] for w in get_watchlist("demo-judge")] or [
    "HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR"]


def warm(label, fn, *a, **kw):
    t = time.time()
    try:
        fn(*a, **kw)
        print(f"  ✓ {label:44s} {time.time() - t:5.1f}s")
        return True
    except Exception as e:
        print(f"  ✗ {label:44s} {str(e)[:60]}")
        return False


def main():
    dc.init_cache_db()
    print(f"Pre-warming for {len(SYMBOLS)} companies: {', '.join(SYMBOLS)}\n")

    import main as app_module
    client = app_module.app.test_client()

    def hit(path):
        r = client.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")

    # Sign in as the demo user so authed endpoints resolve the watchlist.
    client.get("/demo")

    print("Market data")
    warm("/api/prices", hit, "/api/prices")
    warm("/api/pre-open", hit, "/api/pre-open")
    warm("/api/sector", hit, "/api/sector")
    for s in SYMBOLS:
        warm(f"/api/price-intel/{s}", hit, f"/api/price-intel/{s}")

    print("\nSector & compliance")
    warm("/api/industry-intel/FMCG", hit, "/api/industry-intel/FMCG")
    warm("/api/compliance", hit, "/api/compliance")
    warm("/api/morning-brief", hit, "/api/morning-brief")

    print("\nRegulation links")
    try:
        from sebi_link_resolver import warm_cache_async, get_regulation_url
        warm("SEBI lodr", get_regulation_url, "lodr")
        warm("SEBI pit", get_regulation_url, "pit")
    except Exception as e:
        print(f"  ✗ SEBI links: {str(e)[:70]}")

    print("\nPortfolio briefing")
    try:
        from agents.briefing import generate
        warm("portfolio briefing", generate, SYMBOLS)
    except Exception as e:
        print(f"  ✗ briefing: {str(e)[:70]}")

    print("\nPages (populates any per-page caches)")
    for p in ("/dashboard", "/intelligence", "/compliance", "/draft", "/prices",
              "/watchlist", "/din-search"):
        warm(p, hit, p)
    for s in SYMBOLS:
        warm(f"/company/{s}", hit, f"/company/{s}")
        warm(f"/api/draft/recommendations?symbol={s}", hit,
             f"/api/draft/recommendations?symbol={s}")

    st = dc.stats()
    print(f"\n{st['total']} cache entries written")
    for e in st["entries"]:
        print(f"   {e['key'][:52]:54s} {e['bytes']:>9,}b")

    print("\nNow start the demo server with:")
    print("   DELTA_DEMO_FREEZE=1 DELTA_SKIP_PRELOAD=1 DELTA_DEMO_MODE=1 python3 main.py")


if __name__ == "__main__":
    main()
