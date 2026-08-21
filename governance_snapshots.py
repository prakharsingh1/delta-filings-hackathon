"""Governance snapshot layer — makes DELTA reliable.

`governance.py` fetches and parses board composition live from NSE. That is
correct for exploration but wrong for the core product: "what changed" cannot
depend on a third-party API being up, cookie-warmed, and un-rate-limited at
the moment someone opens the page.

This module captures each parsed quarterly governance report ONCE and stores
the normalised board and committee composition. Deltas are then computed from
local rows, so the before/after comparison is instant and always available.

Everything stored here is real, publicly disclosed data from the company's own
SEBI Integrated Filing (Governance). The XBRL source URL is kept on every
snapshot so any figure on screen can be traced back to the filing it came from.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from database import get_connection

_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

# Category strings NSE uses; we bucket them for the composition summary.
_INDEPENDENT = "independent"
_EXECUTIVE = "executive"


def init_snapshots_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS governance_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            quarter TEXT NOT NULL,
            quarter_date TEXT NOT NULL,
            record_id TEXT DEFAULT '',
            submission_date TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            board_json TEXT DEFAULT '[]',
            committees_json TEXT DEFAULT '{}',
            summary_json TEXT DEFAULT '{}',
            fetched_at TEXT NOT NULL,
            UNIQUE(symbol, quarter)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gov_snap_symbol "
                 "ON governance_snapshots(symbol, quarter_date DESC)")
    conn.commit()
    conn.close()


def _quarter_to_date(quarter: str) -> str:
    """'30-JUN-2026' -> '2026-06-30' so quarters sort chronologically."""
    try:
        d, m, y = quarter.strip().upper().split("-")
        return f"{int(y):04d}-{_MONTHS.get(m[:3], 1):02d}-{int(d):02d}"
    except Exception:
        return "0000-00-00"


def _normalise_board(section: Dict) -> List[Dict]:
    cols = section.get("columns") or []
    idx = {c.strip().lower(): i for i, c in enumerate(cols)}

    def pick(row, key, default=""):
        i = idx.get(key)
        if i is None or i >= len(row):
            return default
        v = (row[i] or "").strip()
        return "" if v == "-" else v

    out = []
    for row in section.get("rows") or []:
        name = pick(row, "name")
        if not name:
            continue
        out.append({
            "name": name,
            "din": pick(row, "din"),
            "category": pick(row, "category"),
            "appointed": pick(row, "appointed"),
            "ceased": pick(row, "ceased"),
            "tenure": pick(row, "tenure"),
            "directorships": pick(row, "directorships"),
            "committee_memberships": pick(row, "committee memberships"),
        })
    return out


def _normalise_committee(section: Dict) -> List[Dict]:
    cols = section.get("columns") or []
    idx = {c.strip().lower(): i for i, c in enumerate(cols)}

    def pick(row, key):
        i = idx.get(key)
        if i is None or i >= len(row):
            return ""
        v = (row[i] or "").strip()
        return "" if v == "-" else v

    out = []
    for row in section.get("rows") or []:
        name = pick(row, "name")
        if not name:
            continue
        out.append({"name": name, "category": pick(row, "category"),
                    "role": pick(row, "role"), "appointed": pick(row, "appointed"),
                    "ceased": pick(row, "ceased")})
    return out


def _summarise(board: List[Dict], committees: Dict[str, List[Dict]]) -> Dict:
    """Composition counters. These are the numbers the delta screen shows."""
    active = [d for d in board if not d.get("ceased")]
    independent = [d for d in active if _INDEPENDENT in (d.get("category") or "").lower()]
    executive = [d for d in active if _EXECUTIVE in (d.get("category") or "").lower()
                 and _INDEPENDENT not in (d.get("category") or "").lower()]
    total = len(active)
    return {
        "total_directors": total,
        "independent_directors": len(independent),
        "executive_directors": len(executive),
        "non_executive_directors": total - len(executive),
        "independent_ratio_pct": round(len(independent) / total * 100) if total else 0,
        "committees": {k: len([m for m in v if not m.get("ceased")])
                       for k, v in committees.items()},
    }


def capture(symbol: str, record: Dict) -> Optional[int]:
    """Parse and store one governance report. Returns the snapshot id.

    Sourced from the record's own XBRL URL, never from the recId JSON endpoint.
    NSE's `recId` is a GLOBAL identifier and `fetch_governance_reports_list`
    merges ids from the integrated and legacy namespaces, so querying
    /api/corporate-governance?recId=N can return a DIFFERENT COMPANY's board.
    That actually happened here: a Nestle India query returned Bata India's
    directors. The XBRL URL is per-filing and unambiguous.
    """
    from governance import _parse_xbrl_governance, parse_governance_report

    symbol = symbol.upper()
    quarter = (record.get("quarter") or "").strip()
    xbrl_url = (record.get("xbrl") or "").strip()
    if not quarter or not xbrl_url:
        return None

    try:
        parsed = parse_governance_report(_parse_xbrl_governance(xbrl_url))
    except Exception as e:
        print(f"[GOVSNAP] {symbol} {quarter}: parse failed — {str(e)[:120]}")
        return None
    if not parsed:
        return None

    sections = {s.get("id"): s for s in parsed.get("sections", [])}
    board_sec = sections.get("board")
    if not board_sec or not board_sec.get("rows"):
        return None

    board = _normalise_board(board_sec)
    committees = {
        sid.replace("comm_", ""): _normalise_committee(sec)
        for sid, sec in sections.items() if sid.startswith("comm_")
    }
    summary = _summarise(board, committees)

    suspect = _looks_contaminated(symbol, quarter, board)
    if suspect:
        print(f"[GOVSNAP] {symbol} {quarter}: REJECTED — {suspect}")
        return None

    conn = get_connection()
    conn.execute("""
        INSERT INTO governance_snapshots
            (symbol, quarter, quarter_date, record_id, submission_date, source_url,
             board_json, committees_json, summary_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, quarter) DO UPDATE SET
            record_id=excluded.record_id, submission_date=excluded.submission_date,
            source_url=excluded.source_url, board_json=excluded.board_json,
            committees_json=excluded.committees_json, summary_json=excluded.summary_json,
            fetched_at=excluded.fetched_at
    """, (symbol, quarter, _quarter_to_date(quarter),
          str(record.get("recordId") or record.get("rec_id") or ""),
          record.get("submissionDate") or "", xbrl_url,
          json.dumps(board), json.dumps(committees), json.dumps(summary),
          datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    sid = conn.execute("SELECT id FROM governance_snapshots WHERE symbol=? AND quarter=?",
                       (symbol, quarter)).fetchone()["id"]
    conn.close()
    return sid


def backfill(symbol: str, max_reports: int = 8, skip_existing: bool = True) -> Dict:
    """Capture the most recent N governance reports for one company.

    Run once, offline. After this the delta screen never touches the network.
    """
    from governance import fetch_governance_reports_list

    symbol = symbol.upper()
    init_snapshots_db()
    try:
        reports = fetch_governance_reports_list(symbol) or []
    except Exception as e:
        return {"symbol": symbol, "error": str(e)[:200], "captured": 0}

    have = {r["quarter"] for r in list_snapshots(symbol)} if skip_existing else set()

    captured, skipped, failed = 0, 0, 0
    for rec in reports[:max_reports]:
        q = (rec.get("quarter") or "").strip()
        if q in have:
            skipped += 1
            continue
        if capture(symbol, rec):
            captured += 1
            print(f"[GOVSNAP] {symbol} {q}: captured")
        else:
            failed += 1
            print(f"[GOVSNAP] {symbol} {q}: no board data")

    return {"symbol": symbol, "available": len(reports), "captured": captured,
            "skipped": skipped, "failed": failed}


def list_snapshots(symbol: str) -> List[Dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, symbol, quarter, quarter_date, record_id, submission_date, "
        "source_url, summary_json, fetched_at FROM governance_snapshots "
        "WHERE symbol = ? ORDER BY quarter_date DESC", (symbol.upper(),)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = json.loads(d.pop("summary_json") or "{}")
        out.append(d)
    return out


def get_snapshot(symbol: str, quarter: str) -> Optional[Dict]:
    conn = get_connection()
    r = conn.execute("SELECT * FROM governance_snapshots WHERE symbol=? AND quarter=?",
                     (symbol.upper(), quarter)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r)
    d["board"] = json.loads(d.pop("board_json") or "[]")
    d["committees"] = json.loads(d.pop("committees_json") or "{}")
    d["summary"] = json.loads(d.pop("summary_json") or "{}")
    return d


def _by_din(board: List[Dict]) -> Dict[str, Dict]:
    """Key directors by DIN where present, falling back to normalised name."""
    out = {}
    for d in board:
        key = (d.get("din") or "").strip() or "name:" + " ".join(
            (d.get("name") or "").lower().split())
        out[key] = d
    return out


def compute_delta(symbol: str, older_quarter: str = "", newer_quarter: str = "") -> Optional[Dict]:
    """Structured before/after between two stored snapshots.

    Defaults to the two most recent. Returns None if fewer than two exist.
    """
    snaps = list_snapshots(symbol)
    if len(snaps) < 2:
        return None

    if newer_quarter and older_quarter:
        newer = get_snapshot(symbol, newer_quarter)
        older = get_snapshot(symbol, older_quarter)
    else:
        newer = get_snapshot(symbol, snaps[0]["quarter"])
        older = get_snapshot(symbol, snaps[1]["quarter"])
    if not newer or not older:
        return None

    a, b = _by_din(older["board"]), _by_din(newer["board"])

    # Classify by ACTIVE status, not mere presence. A director listed in both
    # quarters but carrying a cessation date in the newer one has departed —
    # counting them as "unchanged" made the summary contradict the headline
    # counts (e.g. "board size 17 -> 16 ... 1 joined" with nobody leaving).
    def active(d):
        return not (d or {}).get("ceased")

    active_a = {k for k, d in a.items() if active(d)}
    active_b = {k for k, d in b.items() if active(d)}

    joined = [b[k] for k in sorted(active_b - active_a)]
    departed = [(b.get(k) or a[k]) for k in sorted(active_a - active_b)]

    changed = []
    for k in sorted(active_a & active_b):
        da, db = a[k], b[k]
        diffs = []
        for field, label in (("category", "Category"),
                             ("committee_memberships", "Committee memberships"),
                             ("directorships", "Other directorships")):
            va, vb = da.get(field, ""), db.get(field, "")
            if va != vb and vb:
                diffs.append({"field": label, "before": va or "—", "after": vb})
        if diffs:
            changed.append({"name": db.get("name"), "din": db.get("din"), "changes": diffs})

    # Committee-level movement, keyed by committee id.
    committee_changes = {}
    structural_changes = {}
    for cid in set(older["committees"]) | set(newer["committees"]):
        ca = {m["name"] for m in older["committees"].get(cid, []) if not m.get("ceased")}
        cb = {m["name"] for m in newer["committees"].get(cid, []) if not m.get("ceased")}
        if ca == cb:
            continue
        entry = {"joined": sorted(cb - ca), "left": sorted(ca - cb),
                 "before_count": len(ca), "after_count": len(cb)}
        # A committee that appears in one report and is entirely absent from
        # the other is a change in how the company structured its XBRL, not
        # five people resigning. Reporting that as departures would be a false
        # statement about named individuals, so it is bucketed separately.
        if not ca or not cb:
            structural_changes[cid] = entry
        else:
            committee_changes[cid] = entry

    sa, sb = older["summary"], newer["summary"]
    metrics = []
    for key, label in (("total_directors", "Total directors"),
                       ("independent_directors", "Independent directors"),
                       ("executive_directors", "Executive directors"),
                       ("independent_ratio_pct", "Independent ratio (%)")):
        metrics.append({"label": label, "before": sa.get(key), "after": sb.get(key),
                        "changed": sa.get(key) != sb.get(key)})

    return {
        "source": "governance_snapshot_comparison",
        "symbol": symbol.upper(),
        "before_period": older["quarter"],
        "after_period": newer["quarter"],
        "before_source_url": older.get("source_url", ""),
        "after_source_url": newer.get("source_url", ""),
        "before": sa,
        "after": sb,
        "metrics": metrics,
        "directors_joined": joined,
        "directors_departed": departed,
        "directors_changed": changed,
        "committee_changes": committee_changes,
        "committee_structural_changes": structural_changes,
        "has_board_change": bool(joined or departed or changed or committee_changes),
        "summary_line": _summary_line(sa, sb, joined, departed, changed, committee_changes),
    }


def _summary_line(sa: Dict, sb: Dict, joined: List, departed: List,
                  changed: Optional[List] = None,
                  committee_changes: Optional[Dict] = None) -> str:
    bits = []
    if sa.get("total_directors") != sb.get("total_directors"):
        bits.append(f"Board size {sa.get('total_directors')} → {sb.get('total_directors')}")
    if sa.get("independent_directors") != sb.get("independent_directors"):
        bits.append(f"independent {sa.get('independent_directors')} → {sb.get('independent_directors')}")
    if joined:
        bits.append(f"{len(joined)} joined")
    if departed:
        bits.append(f"{len(departed)} departed")
    if changed:
        bits.append(f"{len(changed)} role/committee change{'s' if len(changed) != 1 else ''}")
    if not bits and committee_changes:
        bits.append(f"{len(committee_changes)} committee composition change"
                    f"{'s' if len(committee_changes) != 1 else ''}")
    return "; ".join(bits) or "No change in board composition"


def find_change_periods(symbol: str) -> List[Dict]:
    """Every consecutive quarter pair where board composition actually moved.

    Used to pick the most interesting real delta to feature, instead of
    hoping the two latest quarters happen to differ.
    """
    snaps = list_snapshots(symbol)
    out = []
    for newer, older in zip(snaps, snaps[1:]):
        d = compute_delta(symbol, older["quarter"], newer["quarter"])
        if d and d["has_board_change"]:
            out.append({
                "before_period": d["before_period"], "after_period": d["after_period"],
                "summary": d["summary_line"],
                "joined": len(d["directors_joined"]),
                "departed": len(d["directors_departed"]),
                "changed": len(d["directors_changed"]),
            })
    return out


def _looks_contaminated(symbol: str, quarter: str, board: List[Dict]) -> str:
    """Reject a snapshot whose board bears no resemblance to its neighbours.

    Boards do not turn over completely in one quarter. If a candidate shares
    almost no DINs with an adjacent stored quarter, the far likelier
    explanation is that we fetched the wrong company. Showing that to a
    compliance professional would be worse than showing nothing.
    """
    if len(board) < 4:
        return ""
    neighbours = list_snapshots(symbol)
    if not neighbours:
        return ""

    qd = _quarter_to_date(quarter)
    nearest = min(neighbours, key=lambda s: abs(
        (datetime.fromisoformat(s["quarter_date"]) - datetime.fromisoformat(qd)).days
    ) if s["quarter_date"] != "0000-00-00" else 10 ** 6)

    other = get_snapshot(symbol, nearest["quarter"])
    if not other or len(other["board"]) < 4:
        return ""

    dins_new = {d["din"] for d in board if d.get("din")}
    dins_old = {d["din"] for d in other["board"] if d.get("din")}
    if not dins_new or not dins_old:
        return ""

    overlap = len(dins_new & dins_old) / max(len(dins_new | dins_old), 1)
    if overlap < 0.20:
        return (f"board shares only {overlap:.0%} of DINs with {nearest['quarter']} "
                f"— probable wrong-company data")
    return ""
