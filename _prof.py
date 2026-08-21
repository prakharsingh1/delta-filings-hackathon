import time
from database import get_watchlist
syms = [w["symbol"] for w in get_watchlist("demo-judge")]
print("symbols:", len(syms))

t = time.time()
from compliance import get_compliance_dashboard
d = get_compliance_dashboard(symbols=syms)
print(f"  get_compliance_dashboard : {time.time()-t:7.1f}s")

t = time.time()
from compliance import get_company_compliance
for s in syms[:3]:
    t0 = time.time()
    get_company_compliance(s)
    print(f"    get_company_compliance({s:11s}) : {time.time()-t0:6.1f}s")
print(f"  3 companies total        : {time.time()-t:7.1f}s")
