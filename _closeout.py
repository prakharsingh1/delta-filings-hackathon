import time
import web_crosscheck as wc
from database import get_connection
from verification_routes import route_for_mandate

NAMES = {"HINDUNILVR":"Hindustan Unilever Limited","ITC":"ITC Limited",
         "NESTLEIND":"Nestle India Limited","BRITANNIA":"Britannia Industries Limited",
         "DABUR":"Dabur India Limited"}
c = get_connection()
rows = [dict(r) for r in c.execute(
    """SELECT symbol, mandate_id, period, due_date FROM compliance_obligations
       WHERE status IN ('pending','unverified') AND due_date < date('now')
       ORDER BY symbol, due_date""")]
c.close()
todo = [r for r in rows if route_for_mandate(r["mandate_id"])["auto"]]
print(f"{len(todo)} exchange-verifiable items\n", flush=True)
cleared = 0
for i, r in enumerate(todo, 1):
    res = wc.crosscheck(r["symbol"], r["mandate_id"], r["period"], r["due_date"],
                        company_name=NAMES.get(r["symbol"], r["symbol"]), force=True)
    ok = wc.apply_verdict(r["symbol"], r["mandate_id"], r["period"], res)
    cleared += bool(ok)
    print(f"  [{i:>2}/{len(todo)}] {r['symbol']:11s} {r['mandate_id']:19s} "
          f"{'CONFIRMED' if ok else res['verdict']}", flush=True)
    time.sleep(0.2)
print(f"\nconfirmed {cleared} of {len(todo)}")
