"""Pull real NSE filing history for the monitored universe.

Adds the top-5 Indian metal & mining issuers alongside the existing FMCG /
IT set, and refreshes everything to a 3-year depth.

Run:  python3 backfill_filings.py            # all companies, 3 years
      python3 backfill_filings.py --metals    # metals only
      python3 backfill_filings.py --years 5

NSE rate-limits aggressive callers hard (we have been throttled mid-run
before), so companies are fetched one at a time with a pause between.
"""

import sys
import time

from database import get_connection, get_cached_filings

# Top 5 Indian metal & mining companies by market capitalisation, NSE symbols.
FMCG = {
    "HINDUNILVR": "Hindustan Unilever Limited",
    "ITC":        "ITC Limited",
    "NESTLEIND":  "Nestle India Limited",
    "BRITANNIA":  "Britannia Industries Limited",
    "DABUR":      "Dabur India Limited",
}
METALS = {
    "TATASTEEL":  "Tata Steel Limited",
    "HINDALCO":   "Hindalco Industries Limited",
    "JSWSTEEL":   "JSW Steel Limited",
    "VEDL":       "Vedanta Limited",
    "JINDALSTEL": "Jindal Steel & Power Limited",
}

EXISTING = {
    "HINDUNILVR": "Hindustan Unilever Limited",
    "RELIANCE":   "Reliance Industries Limited",
    "INFY":       "Infosys Limited",
    "ITC":        "ITC Limited",
    "NESTLEIND":  "Nestle India Limited",
}

DEMO_USER = "demo-judge"
PAUSE_BETWEEN = 8      # seconds between companies
YEARS = 5


def counts():
    conn = get_connection()
    rows = conn.execute(
        "SELECT company, COUNT(*) n, MAX(sort_date) newest, MIN(sort_date) oldest "
        "FROM filings GROUP BY company ORDER BY company"
    ).fetchall()
    conn.close()
    return rows


def add_to_demo_watchlist(symbols):
    from datetime import datetime
    conn = get_connection()
    now = datetime.now().isoformat()
    for sym, name in symbols.items():
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (user_id, symbol, name, added_at) "
            "VALUES (?, ?, ?, ?)", (DEMO_USER, sym, name, now))
    conn.commit()
    conn.close()


def backfill(symbols, years=YEARS):
    # Imported here so the module can be inspected without booting the app.
    from main import process_filings

    results = {}
    for i, (sym, name) in enumerate(symbols.items(), 1):
        before = len(get_cached_filings(sym) or [])
        print(f"\n[{i}/{len(symbols)}] {sym} — {name}", flush=True)
        try:
            new_ids, removed = process_filings(sym, years=years)
            after = len(get_cached_filings(sym) or [])
            results[sym] = {"before": before, "after": after, "added": after - before}
            print(f"    {before} -> {after} filings (+{after - before})", flush=True)
        except Exception as e:
            results[sym] = {"before": before, "after": before, "error": str(e)[:160]}
            print(f"    FAILED: {str(e)[:160]}", flush=True)

        if i < len(symbols):
            time.sleep(PAUSE_BETWEEN)
    return results


if __name__ == "__main__":
    years = YEARS
    if "--years" in sys.argv:
        years = int(sys.argv[sys.argv.index("--years") + 1])

    if "--fmcg" in sys.argv:
        target = FMCG
    elif "--metals" in sys.argv:
        target = METALS
    elif "--existing" in sys.argv:
        target = EXISTING
    else:
        target = FMCG

    print(f"Backfilling {len(target)} companies at {years}-year depth\n")
    add_to_demo_watchlist(FMCG)

    backfill(target, years=years)

    print("\n" + "=" * 64)
    print("FILING COVERAGE")
    print("=" * 64)
    total = 0
    for r in counts():
        total += r["n"]
        print(f"  {r['company']:12s} {r['n']:5d}   {r['oldest'][:10]} .. {r['newest'][:10]}")
    print(f"  {'TOTAL':12s} {total:5d}")
