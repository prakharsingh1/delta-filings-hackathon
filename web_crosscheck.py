"""Public-web cross-check before Delta asserts a company missed a filing.

Delta's own matcher is imperfect: an obligation with no linked filing usually
means we failed to find it, not that a listed issuer breached a statutory
deadline. Saying "overdue — file immediately" on that basis is a false
statement about a real company.

This module goes to the public record before any such claim is made. It uses
Gemini with Google Search grounding to look for evidence the filing happened,
and returns what it found with sources.

Discipline, deliberately one-sided:
  - Evidence that a filing HAPPENED is actionable — it clears the flag.
  - Absence of evidence is NOT evidence of a breach. The worst this returns
    is "no public confirmation found", never "the company did not file".
  - Nothing here produces an allegation. Wording is fixed in code, not left
    to the model.
"""

import json
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from database import get_connection

CACHE_TTL_HOURS = 24 * 7   # public filing evidence does not change often


def init_crosscheck_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filing_crosschecks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            mandate_id TEXT NOT NULL,
            period TEXT NOT NULL,
            verdict TEXT NOT NULL,
            summary TEXT DEFAULT '',
            sources_json TEXT DEFAULT '[]',
            confidence TEXT DEFAULT 'low',
            model TEXT DEFAULT '',
            checked_at TEXT NOT NULL,
            UNIQUE(symbol, mandate_id, period)
        )
    """)
    conn.commit()
    conn.close()


_PROMPT = """You are checking the PUBLIC RECORD for evidence that an Indian listed
company made a specific regulatory filing. You are not judging the company.

COMPANY: {company} (NSE: {symbol})
FILING:  {mandate_name}
REGULATION: {regulation}
PERIOD:  {period}
DUE DATE: {due_date}

Search the public web — NSE and BSE announcement pages, the company's own
investor-relations site, SEBI, MCA, and credible financial press — for evidence
that this filing was submitted for this period.

RULES, and they are strict:
1. You are looking for evidence the filing HAPPENED. Report what you find.
2. If you find nothing, that means the search found nothing. It does NOT mean
   the company failed to file. Large listed issuers file routinely and search
   coverage of individual submissions is patchy. Say "not_found", never
   "not filed".
3. NEVER state or imply that the company breached a deadline, was penalised,
   or is non-compliant, unless you find a specific published regulatory order
   saying exactly that — and then quote its reference.
4. Only cite URLs you actually retrieved.

Return ONLY this JSON:
{{
  "verdict": "evidence_found" | "not_found" | "contradicted",
  "summary": "<one or two factual sentences on what the public record shows>",
  "filed_on": "<YYYY-MM-DD if a filing date is visible, else empty>",
  "sources": [{{"title": "<page title>", "url": "<url>"}}],
  "confidence": "high" | "medium" | "low"
}}

"contradicted" is ONLY for a published regulatory order or exchange notice
specifically recording non-compliance for this filing and period."""

# Fixed wording. The model supplies findings; it does not get to phrase the
# verdict, because that is where an accusation would creep in.
_VERDICT_TEXT = {
    "evidence_found": "Public records indicate this filing was made.",
    "not_found": ("No public confirmation found. This is not evidence of a missed "
                  "deadline — it means the search did not surface the submission. "
                  "Requires human verification."),
    "contradicted": ("A published regulatory record was found referencing this filing. "
                     "Requires human review before any conclusion is drawn."),
    "unavailable": "Cross-check could not be completed. Requires human verification.",
}


def _cached(symbol: str, mandate_id: str, period: str) -> Optional[Dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM filing_crosschecks WHERE symbol=? AND mandate_id=? AND period=?",
        (symbol.upper(), mandate_id, period)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        age = (datetime.now() - datetime.fromisoformat(row["checked_at"])).total_seconds() / 3600
        if age > CACHE_TTL_HOURS:
            return None
    except (ValueError, TypeError):
        return None
    d = dict(row)
    d["sources"] = json.loads(d.pop("sources_json") or "[]")
    d["verdict_text"] = _VERDICT_TEXT.get(d["verdict"], _VERDICT_TEXT["unavailable"])
    d["cached"] = True
    return d


def _store(symbol, mandate_id, period, verdict, summary, sources, confidence, model):
    conn = get_connection()
    conn.execute("""
        INSERT INTO filing_crosschecks
            (symbol, mandate_id, period, verdict, summary, sources_json,
             confidence, model, checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, mandate_id, period) DO UPDATE SET
            verdict=excluded.verdict, summary=excluded.summary,
            sources_json=excluded.sources_json, confidence=excluded.confidence,
            model=excluded.model, checked_at=excluded.checked_at
    """, (symbol.upper(), mandate_id, period, verdict, summary,
          json.dumps(sources), confidence, model,
          datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()


def _extract_json(text: str) -> Optional[Dict]:
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def crosscheck(symbol: str, mandate_id: str, period: str, due_date: str,
               company_name: str = "", force: bool = False) -> Dict[str, Any]:
    """Look for public evidence that one filing was made. Never raises."""
    init_crosscheck_db()
    symbol = symbol.upper()

    if not force:
        hit = _cached(symbol, mandate_id, period)
        if hit:
            return hit

    try:
        from compliance import get_mandate_by_id
        mandate = get_mandate_by_id(mandate_id) or {}
    except Exception:
        mandate = {}

    from agents.client import get_api_key
    api_key = get_api_key()
    if not api_key:
        return {"verdict": "unavailable", "summary": "", "sources": [],
                "confidence": "low", "model": "none",
                "verdict_text": _VERDICT_TEXT["unavailable"], "cached": False}

    prompt = _PROMPT.format(
        company=company_name or symbol, symbol=symbol,
        mandate_name=mandate.get("name", mandate_id),
        regulation=mandate.get("regulation", ""),
        period=period, due_date=due_date,
    )

    try:
        from draft_engine import _gemini_call_grounded
        raw = _gemini_call_grounded(api_key, prompt, max_output_tokens=1400)
    except Exception as e:
        print(f"[CROSSCHECK] grounded call failed for {symbol}/{mandate_id}: {str(e)[:120]}")
        raw = None

    data = _extract_json(raw or "")
    if not data:
        return {"verdict": "unavailable", "summary": "", "sources": [],
                "confidence": "low", "model": "gemini-2.5-flash",
                "verdict_text": _VERDICT_TEXT["unavailable"], "cached": False}

    verdict = data.get("verdict")
    if verdict not in ("evidence_found", "not_found", "contradicted"):
        verdict = "not_found"

    sources = [
        {"title": str(s.get("title", ""))[:160], "url": str(s.get("url", ""))[:400]}
        for s in (data.get("sources") or [])[:5]
        if isinstance(s, dict) and s.get("url", "").startswith("http")
    ]
    # A verdict with no retrievable source is not evidence.
    if verdict == "evidence_found" and not sources:
        verdict = "not_found"

    summary = str(data.get("summary", ""))[:600]
    confidence = data.get("confidence") if data.get("confidence") in ("high", "medium", "low") else "low"

    _store(symbol, mandate_id, period, verdict, summary, sources, confidence, "gemini-2.5-flash")

    return {"verdict": verdict, "summary": summary, "sources": sources,
            "confidence": confidence, "model": "gemini-2.5-flash",
            "filed_on": str(data.get("filed_on", ""))[:10],
            "verdict_text": _VERDICT_TEXT[verdict], "cached": False}


def crosscheck_unmatched(symbol: str, limit: int = 6, company_name: str = "") -> List[Dict]:
    """Cross-check this company's past-due, unmatched obligations."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT mandate_id, period, due_date FROM compliance_obligations
        WHERE symbol = ? AND status IN ('pending','unverified')
          AND due_date < date('now') AND filing_id IS NULL
        ORDER BY due_date LIMIT ?""", (symbol.upper(), limit)).fetchall()
    conn.close()

    out = []
    for r in rows:
        res = crosscheck(symbol, r["mandate_id"], r["period"], r["due_date"],
                         company_name=company_name)
        res.update(mandate_id=r["mandate_id"], period=r["period"], due_date=r["due_date"])
        out.append(res)
        time.sleep(0.4)
    return out


def apply_verdict(symbol: str, mandate_id: str, period: str, result: Dict) -> bool:
    """Write a cross-check result back onto the obligation.

    Only `evidence_found` changes status, and it records that the evidence is
    web-sourced rather than exchange-authoritative — confidence stays
    'medium', never 'high'. A `not_found` verdict deliberately changes
    nothing: absence of search evidence is not proof of a breach.
    """
    if result.get("verdict") != "evidence_found":
        return False

    filed = (result.get("filed_on") or "").strip()
    evidence = {
        "source": "web_crosscheck",
        "summary": result.get("summary", ""),
        "urls": [s["url"] for s in result.get("sources", [])],
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "note": "Confirmed from public sources, not from an exchange feed. "
                "Verify against the company's own records before relying on it.",
    }

    conn = get_connection()
    conn.execute("""
        UPDATE compliance_obligations
        SET status = 'submitted', filed_date = ?, confidence = 'medium',
            evidence_json = ?, verified_at = ?, notes = ?
        WHERE symbol = ? AND mandate_id = ? AND period = ?
    """, (filed or None, json.dumps([evidence]),
          datetime.now().isoformat(timespec="seconds"),
          "Confirmed by public-web cross-check",
          symbol.upper(), mandate_id, period))
    conn.commit()
    changed = conn.total_changes > 0
    conn.close()
    return changed
