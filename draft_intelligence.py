"""Makes drafting smarter using what Delta already knows about the company.

Two things a generic document tool cannot do, because it does not hold the
company's governance record:

  suggest_signatories()  proposes who signs, with real names and DINs pulled
                         from the company's own filed governance report —
                         and picks the right people for the document type
  lint_draft()           checks the letter contains what the regulation and
                         the exchange actually require before it goes out

The lint is deliberately conservative: it reports what is missing, it does
not rewrite. A Company Secretary is accountable for the wording.
"""

import re
from typing import Any, Dict, List, Optional

# Documents signed by directors rather than by the CS.
_BOARD_SIGNED = {
    "board_resolution", "special_resolution", "board_minutes", "agm_minutes",
    "csr_resolution", "mgt14_resolution", "directors_report", "board_notice",
}
# Documents that go to MCA and therefore need a DSC.
_MCA_BOUND = {
    "dir12_covering", "adt1_covering", "mgt14_resolution", "pas3_covering",
    "chg1_covering", "dpt3_covering", "msme1_covering", "ben2_covering",
    "inc20a_covering", "mr1_kmp", "fcgpr_covering",
}


def destination_for(filing_type: str) -> str:
    if filing_type in _MCA_BOUND:
        return "mca"
    if filing_type in _BOARD_SIGNED:
        return "internal"
    return "exchange"


def method_for(filing_type: str) -> str:
    return "dsc" if filing_type in _MCA_BOUND else "electronic"


def suggest_signatories(symbol: str, filing_type: str = "",
                        limit: int = 4) -> List[Dict[str, Any]]:
    """Who should sign, drawn from the company's filed board composition.

    Exchange letters are signed by the Company Secretary / Compliance Officer.
    Board-level documents are signed by directors, and for those the real
    names and DINs come straight from the governance report — which is the
    detail that makes an MCA form actually fileable.
    """
    out: List[Dict[str, Any]] = []

    if filing_type not in _BOARD_SIGNED:
        out.append({
            "name": "", "designation": "Company Secretary & Compliance Officer",
            "din": "", "email": "", "sign_order": 1, "source": "role",
            "hint": "Exchange correspondence is signed by the CS / Compliance Officer.",
        })

    try:
        import governance_snapshots as gs
        import governance_analytics as ga
        snaps = gs.list_snapshots(symbol)
        if snaps:
            snap = gs.get_snapshot(symbol, snaps[0]["quarter"])
            board = [d for d in (snap or {}).get("board", []) if not d.get("ceased")]

            def rank(d):
                cat = (d.get("category") or "").lower()
                if "chairperson" in cat and "md" in cat:
                    return 0
                if "md" in cat or "ceo" in cat:
                    return 1
                if "chairperson" in cat:
                    return 2
                if ga._is_independent(d):
                    return 4
                return 3

            for d in sorted(board, key=rank)[:limit]:
                out.append({
                    "name": d.get("name", ""),
                    "designation": d.get("category", ""),
                    "din": d.get("din", ""),
                    "email": "",
                    "sign_order": len(out) + 1,
                    "source": f"governance report {snap['quarter']}",
                    "hint": "",
                })
    except Exception as e:
        print(f"[DRAFT-INTEL] signatory lookup failed for {symbol}: {str(e)[:120]}")

    return out


# ── Lint ──────────────────────────────────────────────────

def _has(body: str, *patterns: str) -> bool:
    return any(re.search(p, body, re.I) for p in patterns)


def lint_draft(body: str, subject: str = "", filing_type: str = "",
               company_name: str = "", symbol: str = "") -> Dict[str, Any]:
    """Check a draft against what the regulation and the exchange expect."""
    body = body or ""
    checks: List[Dict] = []

    def check(ok, label, detail, severity="warning"):
        checks.append({"ok": bool(ok), "label": label, "detail": detail,
                       "severity": "ok" if ok else severity})

    dest = destination_for(filing_type)

    # ── Structural, for anything going to an exchange ──
    if dest == "exchange":
        check(_has(body, r"\bBSE\b", r"Bombay Stock Exchange", r"\bNSE\b",
                   r"National Stock Exchange", r"Listing Department", r"Manager,?\s+Listing"),
              "Addressed to the exchange",
              "Exchange letters are addressed to the Listing Department of BSE and NSE.",
              "blocking")
        check(_has(body, r"scrip\s*code", r"symbol\s*[:\-]", r"\bISIN\b"),
              "Scrip code / symbol quoted",
              "Exchanges match correspondence on scrip code (BSE) and symbol (NSE).")

    check(_has(body, r"\bSub(ject)?\s*[:\-]", re.escape(subject[:24]) if subject else r"$^"),
          "Subject line present",
          "A subject line stating the regulation and the matter.")

    check(_has(body, r"Regulation\s+\d+", r"Section\s+\d+", r"\bLODR\b",
               r"Companies Act", r"\bSEBI\b"),
          "Regulatory basis cited",
          "State the provision the submission is made under.")

    check(_has(body, r"\b(19|20)\d{2}\b"),
          "Contains a date",
          "Effective date, board meeting date or date of the event.")

    if company_name:
        first = company_name.split()[0]
        check(_has(body, re.escape(first)),
              "Company identified",
              "The letter should name the company it is filed for.", "blocking")

    check(_has(body, r"Yours faithfully", r"Yours sincerely", r"Thanking you",
               r"For\s+[A-Z]", r"Company Secretary", r"Compliance Officer"),
          "Signature block",
          "Close with the signing authority — CS / Compliance Officer or a director.",
          "blocking")

    # ── Placeholders that must not survive to filing ──
    placeholders = re.findall(r"\[[A-Z][A-Z \-/]{3,}\]", body)
    check(not placeholders, "No unresolved placeholders",
          (f"{len(placeholders)} placeholder(s) still in the text: "
           f"{', '.join(sorted(set(placeholders))[:4])}") if placeholders
          else "No bracketed placeholders remain.",
          "blocking")

    # ── Type-specific expectations ──
    if filing_type in ("board_change", "reg30_disclosure"):
        check(_has(body, r"\bDIN\b", r"\b\d{8}\b"),
              "DIN quoted for each person",
              "Reg 30 read with Schedule III expects the DIN for a director change.")
        check(_has(body, r"effective", r"w\.e\.f", r"with effect from"),
              "Effective date stated",
              "State the date the appointment or cessation takes effect.")
        check(_has(body, r"reason", r"pursuant to", r"on account of", r"tenure",
                   r"appoint", r"cessa", r"resign"),
              "Nature of the change described",
              "Say what changed — appointment, cessation, redesignation.")

    if filing_type in ("auditor_change",):
        check(_has(body, r"firm registration", r"\bFRN\b"),
              "Auditor firm registration number",
              "SEBI's Reg 30 format asks for the auditor's FRN.")

    if filing_type in ("rpt_disclosure",):
        check(_has(body, r"related part", r"arm'?s length", r"ordinary course"),
              "Related-party basis described",
              "State whether the transaction is at arm's length and in the ordinary course.")

    if filing_type in ("reg33_results", "integrated_financial"):
        check(_has(body, r"quarter", r"half[- ]year", r"financial year"),
              "Reporting period stated",
              "Identify the quarter or period the results cover.")

    # ── Timeliness reminder, not a check on the text ──
    notes = []
    if filing_type in ("board_change", "reg30_disclosure", "auditor_change"):
        notes.append("Regulation 30 disclosures are due to the exchanges within 24 hours "
                     "of the decision (12 hours where the decision is at a board meeting).")
    if dest == "mca":
        notes.append("This document is bound for MCA and must be signed with a Class 3 DSC "
                     "registered against the signatory's DIN.")

    blocking = [c for c in checks if not c["ok"] and c["severity"] == "blocking"]
    warnings = [c for c in checks if not c["ok"] and c["severity"] == "warning"]
    passed = [c for c in checks if c["ok"]]

    return {
        "checks": checks,
        "passed": len(passed),
        "total": len(checks),
        "blocking": blocking,
        "warnings": warnings,
        "ready": not blocking,
        "score": round(len(passed) / len(checks) * 100) if checks else 0,
        "notes": notes,
        "destination": dest,
        "suggested_method": method_for(filing_type),
    }
