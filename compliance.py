"""Compliance OS — SEBI mandate tracking, deadline monitoring, and compliance scoring."""

import sqlite3
import os
import json
from datetime import datetime, date, timedelta
from database import get_connection, get_watchlist, get_all_watchlist_symbols, get_cached_filings
from compliance_verification import verify_obligation
from sebi_link_resolver import get_regulation_url, SEBI_REGULATIONS_INDEX

# ── SEBI Mandates Registry ──
# Each mandate defines: what, when, source link, applicable regulations
SEBI_MANDATES = [
    {
        "id": "LODR-REG27",
        "name": "Regulation 27 — Corporate Governance Report",
        "regulation": "SEBI LODR Regulation 27",
        "description": "Filed as part of the consolidated Integrated Filing- Governance within 30 days of quarter end (SEBI Circular Dec 2024 merged Regs 13/27/31/32).",
        "frequency": "quarterly",
        "due_days_after_quarter": 30,
        "category": "Governance",
        "filed_via": "Integrated Filing- Governance",
        "keywords": ["corporate governance", "regulation 27", "reg. 27", "reg 27", "governance report", "cg report", "compliance report", "compliance certificate"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2024/185",
        "section_ref": "Chapter IV, Regulation 27",
    },
    {
        "id": "LODR-REG30",
        "name": "Regulation 30 — Material Events Disclosure",
        "regulation": "SEBI LODR Regulation 30",
        "description": "Disclosure of material events or information to stock exchanges within 24 hours of occurrence.",
        "frequency": "event_based",
        "category": "Disclosure",
        "keywords": ["regulation 30", "material event", "disclosure", "intimation"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2023/123",
        "section_ref": "Chapter IV, Regulation 30",
    },
    {
        "id": "LODR-REG31",
        "name": "Regulation 31 — Shareholding Pattern",
        "regulation": "SEBI LODR Regulation 31",
        "description": "Filed as part of the consolidated Integrated Filing- Governance within 30 days of quarter end (SEBI Dec 2024 consolidation).",
        "frequency": "quarterly",
        "due_days_after_quarter": 30,
        "category": "Periodic",
        "filed_via": "Integrated Filing- Governance",
        "keywords": ["shareholding pattern", "regulation 31", "shareholding"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2024/185",
        "section_ref": "Chapter IV, Regulation 31",
    },
    {
        "id": "LODR-REG33",
        "name": "Regulation 33 — Financial Results",
        "regulation": "SEBI LODR Regulation 33",
        "description": "Submission of quarterly/annual financial results within 45/60 days of quarter/year end.",
        "frequency": "quarterly",
        "due_days_after_quarter": 45,
        "due_days_annual": 60,
        "category": "Financial",
        "keywords": ["financial result", "quarterly result", "annual result", "regulation 33"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2023/167",
        "section_ref": "Chapter IV, Regulation 33",
    },
    {
        "id": "LODR-REG13",
        "name": "Regulation 13 — Investor Grievance Statement",
        "regulation": "SEBI LODR Regulation 13",
        "description": "Statement of investor complaints, filed as part of the consolidated Integrated Filing- Governance within 30 days of quarter end (SEBI Dec 2024 consolidation).",
        "frequency": "quarterly",
        "due_days_after_quarter": 30,
        "category": "Investor Relations",
        "filed_via": "Integrated Filing- Governance",
        "keywords": ["investor complaint", "grievance", "regulation 13", "investor grievance"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "SEBI/HO/CFD/CFD-PoD-1/P/CIR/2024/185",
        "section_ref": "Chapter III, Regulation 13",
    },
    {
        "id": "LODR-REG74",
        "name": "Regulation 74 — Registrar Certificate",
        "regulation": "SEBI LODR Regulation 74",
        "description": "Certificate from RTA regarding timely dematerialization of shares, within 30 days of quarter end.",
        "frequency": "quarterly",
        "due_days_after_quarter": 30,
        "category": "Compliance",
        "keywords": ["regulation 74", "registrar", "certificate", "dematerialization", "demat"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "",
        "section_ref": "Chapter IX, Regulation 74",
    },
    {
        "id": "LODR-REG29",
        "name": "Regulation 29 — Board Meeting Intimation",
        "regulation": "SEBI LODR Regulation 29",
        "description": "Prior intimation of board meetings at least 2 working days before (5 days for financial results).",
        "frequency": "event_based",
        "category": "Disclosure",
        "keywords": ["board meeting intimation", "regulation 29", "prior intimation", "intimation of board"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "",
        "section_ref": "Chapter IV, Regulation 29",
    },
    {
        "id": "LODR-REG39",
        "name": "Regulation 39 — Loss of Share Certificate",
        "regulation": "SEBI LODR Regulation 39",
        "description": "Disclosure of information regarding loss of share certificates and issue of duplicate certificates.",
        "frequency": "event_based",
        "category": "Compliance",
        "keywords": ["loss of share", "duplicate certificate", "regulation 39"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "",
        "section_ref": "Chapter V, Regulation 39",
    },
    {
        "id": "LODR-REG44",
        "name": "Regulation 44 — Voting Results",
        "regulation": "SEBI LODR Regulation 44",
        "description": "Disclosure of voting results within 2 working days of AGM/EGM/postal ballot.",
        "frequency": "event_based",
        "category": "Governance",
        "keywords": ["voting result", "regulation 44", "scrutinizer report", "e-voting", "postal ballot result"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "",
        "section_ref": "Chapter V, Regulation 44",
    },
    {
        "id": "INSIDER-REG7",
        "name": "Regulation 7 — Insider Trading Disclosures",
        "regulation": "SEBI PIT Regulation 7",
        "description": "Disclosure of trading by insiders within 2 trading days of the trade.",
        "frequency": "event_based",
        "category": "Insider Trading",
        "keywords": ["insider trading", "trading window", "regulation 7", "pit regulation"],
        "applies_to": "all_listed",
        "sebi_source_key": "pit",
        "sebi_circular": "SEBI/HO/ISD/ISD-SEC-4/P/CIR/2023/0136",
        "section_ref": "Chapter III, Regulation 7",
    },
    {
        "id": "LODR-AGM",
        "name": "Annual General Meeting",
        "regulation": "SEBI LODR / Companies Act 2013",
        "description": "Annual General Meeting to be held within 6 months of financial year end (by September 30).",
        "frequency": "annual",
        "due_month": 9,
        "due_day": 30,
        "category": "Governance",
        "keywords": ["annual general meeting", "agm", "notice of agm"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "",
        "section_ref": "Regulation 44 read with Companies Act Section 96",
    },
    {
        "id": "LODR-AR",
        "name": "Annual Report Filing",
        "regulation": "SEBI LODR Regulation 34",
        "description": "Annual report to be sent to shareholders within 21 days before AGM.",
        "frequency": "annual",
        "due_month": 9,
        "due_day": 9,
        "category": "Periodic",
        "keywords": ["annual report", "regulation 34"],
        "applies_to": "all_listed",
        "sebi_source_key": "lodr",
        "sebi_circular": "",
        "section_ref": "Chapter IV, Regulation 34",
    },
]


def get_mandate_by_id(mandate_id):
    for m in SEBI_MANDATES:
        if m["id"] == mandate_id:
            return m
    return None


# ── Database Setup ──

def init_compliance_db():
    """Create compliance tracking tables."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS compliance_obligations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            mandate_id TEXT NOT NULL,
            period TEXT NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            filing_id INTEGER,
            filed_date TEXT,
            delay_days INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(symbol, mandate_id, period)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_compliance_symbol ON compliance_obligations(symbol)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_compliance_status ON compliance_obligations(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_compliance_due ON compliance_obligations(due_date)")

    # ── Migrations: add verification columns if missing ──
    existing_cols = {r[1] for r in c.execute("PRAGMA table_info(compliance_obligations)")}
    if "evidence_json" not in existing_cols:
        c.execute("ALTER TABLE compliance_obligations ADD COLUMN evidence_json TEXT DEFAULT ''")
    if "confidence" not in existing_cols:
        c.execute("ALTER TABLE compliance_obligations ADD COLUMN confidence TEXT DEFAULT ''")
    if "verified_at" not in existing_cols:
        c.execute("ALTER TABLE compliance_obligations ADD COLUMN verified_at TEXT DEFAULT ''")
    if "filing_url" not in existing_cols:
        c.execute("ALTER TABLE compliance_obligations ADD COLUMN filing_url TEXT DEFAULT ''")

    conn.commit()
    conn.close()


# ── Quarter Utilities ──

def get_quarter_end_dates(year):
    """Return quarter-end dates for a given financial year (Apr-Mar)."""
    return [
        date(year, 6, 30),      # Q1 (Apr-Jun)
        date(year, 9, 30),      # Q2 (Jul-Sep)
        date(year, 12, 31),     # Q3 (Oct-Dec)
        date(year + 1, 3, 31),  # Q4 (Jan-Mar)
    ]


def get_current_quarter():
    """Return current quarter info: (quarter_num, quarter_end_date, fy_year)."""
    today = date.today()
    m = today.month
    if m <= 3:
        return 4, date(today.year, 3, 31), today.year - 1
    elif m <= 6:
        return 1, date(today.year, 6, 30), today.year
    elif m <= 9:
        return 2, date(today.year, 9, 30), today.year
    else:
        return 3, date(today.year, 12, 31), today.year


def get_period_label(quarter_num, fy_year):
    """Return human-readable period label like 'Q1 FY2026-27'."""
    return f"Q{quarter_num} FY{fy_year}-{str(fy_year + 1)[-2:]}"


# ── Obligation Generation ──

def generate_obligations_for_company(symbol):
    """Generate compliance obligations for a watchlisted company for the current and recent quarters."""
    today = date.today()
    now_iso = datetime.now().isoformat()
    conn = get_connection()

    q_num, q_end, fy_year = get_current_quarter()

    # Generate for current quarter + previous 2 quarters
    quarters_to_check = []
    for offset in range(2, -1, -1):
        adjusted_q = q_num - offset
        adjusted_fy = fy_year
        while adjusted_q <= 0:
            adjusted_q += 4
            adjusted_fy -= 1
        q_ends = get_quarter_end_dates(adjusted_fy)
        q_end_date = q_ends[adjusted_q - 1]
        period = get_period_label(adjusted_q, adjusted_fy)
        quarters_to_check.append((adjusted_q, adjusted_fy, q_end_date, period))

    for mandate in SEBI_MANDATES:
        if mandate["frequency"] == "quarterly":
            days_after = mandate.get("due_days_after_quarter", 30)
            for q, fy, q_end_dt, period in quarters_to_check:
                due = q_end_dt + timedelta(days=days_after)
                _upsert_obligation(conn, symbol, mandate["id"], period, due.isoformat(), now_iso)

        elif mandate["frequency"] == "annual":
            due_month = mandate.get("due_month", 9)
            due_day = mandate.get("due_day", 30)
            if today.month > 3:
                due = date(today.year, due_month, due_day)
                period = f"FY{today.year - 1}-{str(today.year)[-2:]}"
            else:
                due = date(today.year - 1, due_month, due_day) if due_month > 3 else date(today.year, due_month, due_day)
                period = f"FY{today.year - 2}-{str(today.year - 1)[-2:]}"
            _upsert_obligation(conn, symbol, mandate["id"], period, due.isoformat(), now_iso)

        # Event-based mandates: not generated upfront, tracked via filing linkage

    conn.commit()
    conn.close()


def _upsert_obligation(conn, symbol, mandate_id, period, due_date, now_iso):
    """Insert obligation; also patch due_date if mandate rule was updated."""
    try:
        conn.execute(
            """INSERT OR IGNORE INTO compliance_obligations
               (symbol, mandate_id, period, due_date, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (symbol, mandate_id, period, due_date, now_iso, now_iso),
        )
        # If the mandate's due-days rule changed, correct existing rows.
        conn.execute(
            """UPDATE compliance_obligations
               SET due_date = ?, updated_at = ?
               WHERE symbol = ? AND mandate_id = ? AND period = ? AND due_date != ?""",
            (due_date, now_iso, symbol, mandate_id, period, due_date),
        )
    except sqlite3.IntegrityError:
        pass


# ── Filing Linkage ──

def link_filings_to_obligations(symbol):
    """Scan existing filings and link them to matching obligations."""
    conn = get_connection()
    filings = get_cached_filings(symbol)
    now_iso = datetime.now().isoformat()
    today = date.today()

    obligations = conn.execute(
        "SELECT * FROM compliance_obligations WHERE symbol = ?", (symbol,)
    ).fetchall()

    for ob in obligations:
        mandate = get_mandate_by_id(ob["mandate_id"])
        if not mandate:
            continue

        # Skip if already linked to a filing
        if ob["filing_id"]:
            continue

        keywords = mandate.get("keywords", [])
        due_date = ob["due_date"]

        try:
            due_dt = datetime.strptime(due_date, "%Y-%m-%d").date()
        except ValueError:
            continue

        # Search window: from 60 days before due date to 30 days after
        search_start = (due_dt - timedelta(days=60)).isoformat()
        search_end = (due_dt + timedelta(days=30)).isoformat()

        best_match = None
        best_score = 0

        for f in filings:
            sd = f.get("sort_date", f.get("date", ""))[:10]
            if not sd or sd < search_start or sd > search_end:
                continue
            title_lower = (f.get("title", "") + " " + f.get("description", "")).lower()
            score = sum(1 for kw in keywords if kw in title_lower)
            if score > best_score:
                best_score = score
                best_match = f

        if best_match and best_score >= 1:
            filed_date = best_match.get("sort_date", best_match.get("date", ""))[:10]
            try:
                filed_dt = datetime.strptime(filed_date, "%Y-%m-%d").date()
                delay = (filed_dt - due_dt).days
            except ValueError:
                delay = 0

            if delay <= 0:
                status = "submitted"
                delay = 0
            else:
                status = "delayed"

            conn.execute(
                """UPDATE compliance_obligations
                   SET status = ?, filing_id = ?, filed_date = ?, delay_days = ?, updated_at = ?
                   WHERE id = ?""",
                (status, best_match["id"], filed_date, delay, now_iso, ob["id"]),
            )

    # ── Multi-source cross-verification for past-due, unlinked obligations ──
    # Rather than relying on our shallow NSE announcement cache alone, we query
    # authoritative per-mandate NSE endpoints (integrated-filing-results, CG master,
    # SHP master) plus BSE announcements. An obligation is only marked "missed"
    # when multiple independent sources confirm the filing does not exist.
    # Otherwise it is "submitted" / "delayed" (if found anywhere) or "unverified".
    for ob in obligations:
        if ob["filing_id"]:
            continue
        if ob["status"] not in ("pending", "missed", "unverified"):
            continue
        try:
            due_dt = datetime.strptime(ob["due_date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if due_dt >= today:
            # Due date may have been patched forward by a mandate rule update.
            # Reset any stale past-due statuses back to pending.
            if ob["status"] in ("missed", "unverified", "delayed"):
                conn.execute(
                    "UPDATE compliance_obligations SET status='pending', filed_date=NULL, "
                    "delay_days=0, evidence_json='', confidence='', filing_url='', "
                    "updated_at=? WHERE id=?",
                    (now_iso, ob["id"]),
                )
            continue

        # Derive quarter-end from mandate + due_date
        mandate = get_mandate_by_id(ob["mandate_id"])
        if not mandate:
            continue
        if mandate.get("frequency") == "quarterly":
            days_after = mandate.get("due_days_after_quarter", 30)
            q_end = due_dt - timedelta(days=days_after)
        else:
            q_end = due_dt

        try:
            result = verify_obligation(symbol, ob["mandate_id"], q_end, due_dt)
        except Exception as e:
            print(f"[COMPLIANCE] verify_obligation failed for {symbol}/{ob['mandate_id']}: {e}")
            continue

        new_status = result["status"]
        filed_date = result.get("filed_date")
        delay_days = result.get("delay_days", 0)
        evidence_json = json.dumps(result.get("evidence", []))[:8000]
        confidence = result.get("confidence", "")
        # Extract a URL from evidence for UI display
        filing_url = ""
        for ev in result.get("evidence", []):
            m = ev.get("match") or {}
            if m.get("url"):
                filing_url = m["url"]
                break

        conn.execute(
            """UPDATE compliance_obligations
               SET status = ?, filed_date = ?, delay_days = ?,
                   evidence_json = ?, confidence = ?, verified_at = ?,
                   filing_url = ?, updated_at = ?
               WHERE id = ?""",
            (new_status, filed_date, delay_days, evidence_json, confidence,
             now_iso, filing_url, now_iso, ob["id"]),
        )

    conn.commit()
    conn.close()


# ── Compliance Refresh ──

_last_synced = None


def refresh_compliance(symbol=None):
    """Refresh compliance tracking. Compliance data (SEBI obligations) is public
    per-company, so we aggregate across ALL users' watchlists when refreshing.
    """
    global _last_synced
    init_compliance_db()
    symbols = [symbol] if symbol else get_all_watchlist_symbols()

    for sym in symbols:
        generate_obligations_for_company(sym)
        link_filings_to_obligations(sym)

    _last_synced = datetime.now().isoformat()


def get_last_synced():
    return _last_synced


# ── Queries ──

def get_compliance_dashboard(symbols=None):
    """Return data for the compliance dashboard.

    If `symbols` is provided, only obligations for those symbols are returned —
    use this to scope the dashboard to a single user's watchlist. When `symbols`
    is None, all obligations across all users are returned (admin/backfill view).
    An empty list returns an empty dashboard.

    Metric reconciliation:
      Total = Upcoming + Overdue + Completed + Future (pending, due > 30 days)
    All four buckets are mutually exclusive and exhaustive.
    """
    today = date.today()
    today_iso = today.isoformat()
    conn = get_connection()

    # Ensure table exists
    try:
        conn.execute("SELECT 1 FROM compliance_obligations LIMIT 1")
    except sqlite3.OperationalError:
        conn.close()
        init_compliance_db()
        conn = get_connection()

    if symbols is None:
        all_obligations = conn.execute(
            "SELECT * FROM compliance_obligations ORDER BY due_date DESC"
        ).fetchall()
    elif len(symbols) == 0:
        all_obligations = []
    else:
        placeholders = ",".join("?" for _ in symbols)
        all_obligations = conn.execute(
            f"SELECT * FROM compliance_obligations WHERE symbol IN ({placeholders}) "
            f"ORDER BY due_date DESC",
            tuple(symbols),
        ).fetchall()

    conn.close()

    # Enrich all
    all_enriched = [_enrich_obligation(dict(o)) for o in all_obligations]

    # ── Mutually exclusive buckets ──
    upcoming_cutoff = (today + timedelta(days=30)).isoformat()

    upcoming = []    # pending, due today through next 30 days
    overdue = []     # missed OR pending with due_date < today AND we have filing coverage
    completed = []   # submitted or delayed (any time)
    future = []      # pending, due > 30 days from now
    unverified = []  # past due but no filing coverage — can't honestly call it missed

    for ob in all_enriched:
        status = ob.get("status", "pending")
        due = ob.get("due_date", "")

        if status in ("submitted", "delayed"):
            completed.append(ob)
        elif status == "missed":
            overdue.append(ob)
        elif status == "unverified":
            unverified.append(ob)
        elif status == "pending":
            if due < today_iso:
                # Pending but past due → overdue
                overdue.append(ob)
            elif due <= upcoming_cutoff:
                upcoming.append(ob)
            else:
                future.append(ob)

    # Sort
    upcoming.sort(key=lambda x: x.get("due_date", ""))
    overdue.sort(key=lambda x: x.get("due_date", ""))
    completed.sort(key=lambda x: x.get("filed_date", "") or x.get("due_date", ""), reverse=True)
    unverified.sort(key=lambda x: x.get("due_date", ""))

    # ── Validation ──
    validation = _validate_dashboard(all_enriched, upcoming, overdue, completed, future, unverified)

    return {
        "upcoming": upcoming,
        "overdue": overdue,
        "completed": completed,
        "future": future,
        "unverified": unverified,
        "all": all_enriched,
        "total": len(all_enriched),
        "validation": validation,
        "last_synced": _last_synced,
    }


def _validate_dashboard(all_obs, upcoming, overdue, completed, future, unverified=None):
    """Validate that all metrics reconcile. Returns validation result dict."""
    unverified = unverified or []
    total = len(all_obs)
    bucket_sum = len(upcoming) + len(overdue) + len(completed) + len(future) + len(unverified)
    is_valid = (total == bucket_sum)

    # Check every obligation appears in exactly one bucket
    bucket_ids = set()
    for lst in [upcoming, overdue, completed, future, unverified]:
        for ob in lst:
            oid = ob.get("id")
            if oid in bucket_ids:
                is_valid = False  # duplicate
            bucket_ids.add(oid)

    if len(bucket_ids) != total:
        is_valid = False

    return {
        "is_valid": is_valid,
        "total": total,
        "bucket_sum": bucket_sum,
        "upcoming_count": len(upcoming),
        "overdue_count": len(overdue),
        "completed_count": len(completed),
        "future_count": len(future),
        "unverified_count": len(unverified),
    }


def get_company_compliance(symbol):
    """Return compliance data for a specific company with reconciled metrics."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM compliance_obligations WHERE symbol = ? ORDER BY due_date DESC",
            (symbol,),
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return {"obligations": [], "score": None, "history": {}, "health_tag": "unknown"}

    conn.close()
    obligations = [_enrich_obligation(dict(r)) for r in rows]

    # Compute score and history
    score = compute_compliance_score(obligations)
    history = compute_compliance_history(obligations)
    health_tag = compute_health_tag(obligations, score)

    return {
        "obligations": obligations,
        "score": score,
        "history": history,
        "health_tag": health_tag,
    }


def _enrich_obligation(ob):
    """Add mandate details, computed fields, and SEBI source to an obligation dict."""
    mandate = get_mandate_by_id(ob.get("mandate_id", ""))
    if mandate:
        ob["mandate_name"] = mandate["name"]
        ob["regulation"] = mandate.get("regulation", "")
        ob["mandate_category"] = mandate.get("category", "")
        ob["mandate_description"] = mandate.get("description", "")
        # SEBI rebuilds regulation URLs on every amendment. Resolve live so we
        # never serve a dead link; falls back to the regulations index.
        key = mandate.get("sebi_source_key")
        ob["sebi_source"] = get_regulation_url(key) if key else SEBI_REGULATIONS_INDEX
        ob["sebi_circular"] = mandate.get("sebi_circular", "")
        ob["section_ref"] = mandate.get("section_ref", "")
        ob["filed_via"] = mandate.get("filed_via", "")
    else:
        ob["mandate_name"] = ob.get("mandate_id", "Unknown")
        ob["regulation"] = ""
        ob["mandate_category"] = ""
        ob["mandate_description"] = ""
        ob["sebi_source"] = SEBI_REGULATIONS_INDEX
        ob["sebi_circular"] = ""
        ob["section_ref"] = ""
        ob["filed_via"] = ""

    # Countdown
    try:
        due_dt = datetime.strptime(ob["due_date"], "%Y-%m-%d").date()
        days_remaining = (due_dt - date.today()).days
        ob["days_remaining"] = days_remaining
        ob["is_urgent"] = 0 < days_remaining <= 7
        ob["is_overdue"] = days_remaining < 0
    except (ValueError, KeyError):
        ob["days_remaining"] = None
        ob["is_urgent"] = False
        ob["is_overdue"] = False

    # Parse verification evidence JSON for UI
    ev = ob.get("evidence_json")
    if ev:
        try:
            ob["evidence"] = json.loads(ev)
        except Exception:
            ob["evidence"] = []
    else:
        ob["evidence"] = []

    return ob


# ── Compliance Score ──

def compute_compliance_score(obligations):
    """Compute compliance score (0-100) for a set of obligations.
    Only scores resolved obligations (submitted, delayed, missed).
    Returns None if no resolved obligations exist."""
    if not obligations:
        return None

    scored = [o for o in obligations if o.get("status") in ("submitted", "delayed", "missed")]
    if not scored:
        return None

    total_score = 0
    for o in scored:
        status = o.get("status")
        if status == "submitted":
            total_score += 100
        elif status == "delayed":
            delay = o.get("delay_days", 0) or 0
            total_score += max(30, 100 - (delay * 5))
        elif status == "missed":
            total_score += 0

    return round(total_score / len(scored))


def compute_compliance_history(obligations):
    """Compute compliance history stats. All counts must sum to total."""
    total = len(obligations)
    if not total:
        return {"total": 0, "submitted": 0, "delayed": 0, "missed": 0,
                "pending": 0, "unverified": 0, "resolved": 0, "on_time_rate": 0}

    counts = {"submitted": 0, "delayed": 0, "missed": 0, "pending": 0, "unverified": 0}
    for o in obligations:
        s = o.get("status", "pending")
        if s in counts:
            counts[s] += 1
        else:
            counts["pending"] += 1  # safety: never lose a row from the checksum

    resolved = counts["submitted"] + counts["delayed"] + counts["missed"]
    on_time_rate = round(counts["submitted"] / resolved * 100) if resolved > 0 else 0

    return {
        "total": total,
        **counts,
        "resolved": resolved,
        "on_time_rate": on_time_rate,
    }


def compute_health_tag(obligations, score):
    """Compute health status tag for a company.

    Tags:
      - "critical"  → score < 40 OR >50% obligations missed
      - "at_risk"   → score 40-69 OR any upcoming with no filing + some delays
      - "stable"    → score >= 70 and mostly on-time
      - "unknown"   → no resolved obligations to judge
    """
    if not obligations:
        return "unknown"

    history = compute_compliance_history(obligations)
    resolved = history["resolved"]

    if resolved == 0:
        return "unknown"

    missed_pct = history["missed"] / resolved * 100 if resolved > 0 else 0

    if score is None:
        return "unknown"
    elif score < 40 or missed_pct > 50:
        return "critical"
    elif score < 70 or (history["delayed"] > 0 and missed_pct > 20):
        return "at_risk"
    else:
        return "stable"


# ── All Mandates List ──

def get_all_mandates():
    """Return the full mandates registry with SEBI source links resolved
    against SEBI's current legal index (so links survive amendments)."""
    out = []
    for m in SEBI_MANDATES:
        m_copy = dict(m)
        key = m_copy.pop("sebi_source_key", None)
        m_copy["sebi_source"] = get_regulation_url(key) if key else SEBI_REGULATIONS_INDEX
        out.append(m_copy)
    return out
