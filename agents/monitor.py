"""Agent 1 — Compliance Monitoring Agent.

Persona: Junior Company Secretary Associate.
Question it answers: "What happened, and does a human need to look at it?"

Design rule: deterministic logic decides, the model interprets.

    1. Rule pass   — title/description/keyword match against a governance
                     event taxonomy. Cheap, reliable, never hallucinates.
    2. Delta pass  — governance.py board/committee comparison where two
                     governance reports exist. This is real structured
                     before/after, not model output.
    3. Model pass  — Gemini Flash reads the actual filing text to extract
                     entities, pull a verbatim evidence quote, and refine
                     importance. Constrained to the taxonomy from step 1.

If the model is unavailable the rule pass still produces a usable event, so
the pipeline degrades rather than dies.
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from .client import AGENT_MODELS, call_json, gemini_available

# ── Governance event taxonomy ────────────────────────────
# Closed list. The model may only choose an event_type from here.
EVENT_TAXONOMY = [
    {
        "type": "BOARD_CHANGE",
        "label": "Board / Director Change",
        "category": "Board Governance",
        "importance": "high",
        "requires_action": True,
        "keywords": ["appointment of director", "resignation of director", "cessation",
                     "ceased to be", "change in director", "board of director",
                     "independent director", "re-appointment", "retirement of director",
                     "director has resigned", "dir-12"],
        "draft_type": "board_change",
    },
    {
        "type": "KMP_CHANGE",
        "label": "Key Managerial Personnel Change",
        "category": "Board Governance",
        "importance": "high",
        "requires_action": True,
        "keywords": ["key managerial personnel", "chief executive", "chief financial",
                     "company secretary", "managing director", "kmp", "cfo", "ceo",
                     "compliance officer"],
        "draft_type": "board_change",
    },
    {
        "type": "AUDITOR_CHANGE",
        "label": "Auditor Change",
        "category": "Audit & Assurance",
        "importance": "high",
        "requires_action": True,
        "keywords": ["statutory auditor", "secretarial auditor", "resignation of auditor",
                     "appointment of auditor", "internal auditor", "cost auditor", "adt-1"],
        "draft_type": "auditor_change",
    },
    {
        "type": "RELATED_PARTY_TRANSACTION",
        "label": "Related Party Transaction",
        "category": "Related Party",
        "importance": "high",
        "requires_action": True,
        "keywords": ["related party", "rpt", "regulation 23", "material related party"],
        "draft_type": "rpt_disclosure",
    },
    {
        "type": "CREDIT_RATING_CHANGE",
        "label": "Credit Rating Action",
        "category": "Credit & Ratings",
        "importance": "medium",
        "requires_action": True,
        "keywords": ["credit rating", "rating action", "icra", "crisil", "care ratings",
                     "india ratings", "rating revision", "outlook revised"],
        "draft_type": "credit_rating",
    },
    {
        "type": "BOARD_MEETING_OUTCOME",
        "label": "Board Meeting Outcome",
        "category": "Board Governance",
        "importance": "medium",
        "requires_action": True,
        "keywords": ["outcome of board meeting", "outcome of the meeting",
                     "board meeting held", "considered and approved"],
        "draft_type": "outcome_board_meeting",
    },
    {
        "type": "BOARD_MEETING_INTIMATION",
        "label": "Board Meeting Intimation",
        "category": "Board Governance",
        "importance": "low",
        "requires_action": False,
        "keywords": ["intimation of board meeting", "prior intimation", "regulation 29",
                     "board meeting will be held", "notice of board meeting"],
        "draft_type": "reg29_board_meeting",
    },
    {
        "type": "FINANCIAL_RESULTS",
        "label": "Financial Results",
        "category": "Financial Reporting",
        "importance": "medium",
        "requires_action": True,
        "keywords": ["financial result", "quarterly result", "audited result",
                     "unaudited result", "regulation 33"],
        "draft_type": "reg33_results",
    },
    {
        "type": "SHAREHOLDING_CHANGE",
        "label": "Shareholding / Substantial Acquisition",
        "category": "Shareholding",
        "importance": "medium",
        "requires_action": True,
        "keywords": ["shareholding pattern", "takeover", "sast", "substantial acquisition",
                     "pledge", "encumbrance", "regulation 31"],
        "draft_type": "shareholding_pattern",
    },
    {
        "type": "RESTRUCTURING",
        "label": "Scheme / Restructuring",
        "category": "Corporate Structure",
        "importance": "high",
        "requires_action": True,
        "keywords": ["scheme of arrangement", "amalgamation", "demerger", "merger",
                     "acquisition", "slump sale", "divestment", "stake sale"],
        "draft_type": "acquisition_disclosure",
    },
    {
        "type": "REGULATORY_ACTION",
        "label": "Regulatory / Litigation Action",
        "category": "Regulatory",
        "importance": "high",
        "requires_action": True,
        "keywords": ["order passed", "show cause", "penalty", "adjudication",
                     "action taken", "regulatory action", "tribunal", "litigation"],
        "draft_type": "litigation_action",
    },
    {
        "type": "TRADING_WINDOW",
        "label": "Trading Window Closure",
        "category": "Insider Trading",
        "importance": "low",
        "requires_action": False,
        "keywords": ["trading window", "closure of trading window", "insider trading"],
        "draft_type": "trading_window_closure",
    },
    {
        "type": "DIVIDEND_CORPORATE_ACTION",
        "label": "Dividend / Corporate Action",
        "category": "Corporate Actions",
        "importance": "medium",
        "requires_action": True,
        "keywords": ["dividend", "record date", "book closure", "bonus issue",
                     "stock split", "buyback", "rights issue"],
        "draft_type": "dividend",
    },
    {
        "type": "AGM_EGM",
        "label": "General Meeting / Postal Ballot",
        "category": "Shareholder Meetings",
        "importance": "medium",
        "requires_action": True,
        "keywords": ["annual general meeting", "extraordinary general meeting", "agm",
                     "egm", "postal ballot", "voting result", "scrutinizer"],
        "draft_type": "agm_intimation",
    },
    {
        "type": "COMPLIANCE_CERTIFICATE",
        "label": "Periodic Compliance Certificate",
        "category": "Periodic Compliance",
        "importance": "low",
        "requires_action": False,
        "keywords": ["certificate under", "compliance certificate", "reconciliation of share capital",
                     "regulation 74", "regulation 7(3)", "pcs certificate",
                     "depositories and participants"],
        "draft_type": "depositories_certificate",
    },
    {
        "type": "DISCLOSURE_OTHER",
        "label": "Other Material Disclosure",
        "category": "General",
        "importance": "low",
        "requires_action": False,
        "keywords": [],
        "draft_type": "reg30_disclosure",
    },
]

TAXONOMY_BY_TYPE = {t["type"]: t for t in EVENT_TAXONOMY}
# Types too routine to put in front of a reviewer unless the model escalates.
_ROUTINE = {"COMPLIANCE_CERTIFICATE", "TRADING_WINDOW", "DISCLOSURE_OTHER",
            "BOARD_MEETING_INTIMATION"}


def classify_by_rules(title: str, description: str = "", text: str = "") -> Dict:
    """Deterministic first pass. Scores every taxonomy entry by keyword hits,
    weighting the title highest because NSE titles are already curated."""
    t, d = (title or "").lower(), (description or "").lower()
    body = (text or "")[:4000].lower()

    best, best_score = None, 0
    for entry in EVENT_TAXONOMY:
        score = 0
        for kw in entry["keywords"]:
            if kw in t:
                score += 5
            elif kw in d:
                score += 3
            elif kw in body:
                score += 1
        if score > best_score:
            best, best_score = entry, score

    if not best:
        best = TAXONOMY_BY_TYPE["DISCLOSURE_OTHER"]

    return {
        "event_type": best["type"],
        "event_label": best["label"],
        "category": best["category"],
        "importance": best["importance"],
        "requires_action": best["requires_action"],
        "draft_type": best["draft_type"],
        "rule_score": best_score,
        "rule_matched": best_score > 0,
    }


def _board_delta_from_governance(symbol: str) -> Optional[Dict]:
    """Structured before/after board data for this company.

    Prefers the local governance_snapshots cache. Going to NSE live here cost
    ~30s per board event and made a batch run unusable — and it was pointless,
    because the snapshot layer already holds the same XBRL-parsed data.
    Falls back to a live fetch only when nothing is cached.
    """
    try:
        import governance_snapshots as gs
        cached = gs.compute_delta(symbol)
        if cached and cached.get("has_board_change"):
            return cached
        if cached:
            # Snapshots exist and show no change — that is a real answer.
            return None
        if gs.list_snapshots(symbol):
            return None
    except Exception as e:
        print(f"[AGENT1] snapshot delta unavailable for {symbol}: {str(e)[:100]}")

    try:
        from governance import (fetch_governance_reports_list, fetch_governance_report,
                                parse_governance_report, compare_governance_reports)
    except Exception:
        return None

    try:
        reports = fetch_governance_reports_list(symbol) or []
        if len(reports) < 2:
            return None
        newest, previous = reports[0], reports[1]
        rep_b = parse_governance_report(fetch_governance_report(symbol, newest.get("rec_id")))
        rep_a = parse_governance_report(fetch_governance_report(symbol, previous.get("rec_id")))
        changes = compare_governance_reports(rep_a, rep_b) or []
    except Exception as e:
        print(f"[AGENT1] governance delta unavailable for {symbol}: {str(e)[:120]}")
        return None

    board = next((c for c in changes if c.get("section") == "Board of Directors"), None)
    if not board or board.get("type") != "change":
        return None

    before = after = None
    for item in board.get("items", []):
        m = re.search(r"Total directors:\s*(\d+).*?->\s*(\d+)", item)
        if m:
            before, after = int(m.group(1)), int(m.group(2))
            break

    return {
        "source": "governance_report_comparison",
        "before": {"total_directors": before} if before is not None else {},
        "after": {"total_directors": after} if after is not None else {},
        "changes": [i for i in board.get("items", []) if not i.startswith("Total directors")],
        "sections_changed": [c["section"] for c in changes if c.get("type") == "change"],
    }


_PROMPT = """You are a Junior Company Secretary Associate at an Indian listed company.
You are reading one corporate filing and logging it for your senior's morning review.

COMPANY: {company}
FILING TITLE: {title}
FILING DATE: {date}
DESCRIPTION: {description}

FILING TEXT (may be truncated):
\"\"\"
{text}
\"\"\"

A rule-based classifier has already tagged this as: {rule_type} ({rule_label}).

Your job is to READ THE TEXT and report only what it actually says.

STRICT RULES — you are logging a regulated record:
- Report ONLY facts stated in the filing text above. Do not infer, speculate,
  or add context from outside knowledge.
- `evidence_quote` MUST be copied verbatim from the filing text. If the text
  is empty or unusable, return an empty string. NEVER invent a quote.
- Do not characterise the company or the event as good, bad, risky or
  troubling. You are recording what happened, not judging it.
- If the text does not support the rule classification, choose a better
  event_type from the allowed list.
- If you cannot tell what happened, use DISCLOSURE_OTHER and set
  requires_action to false.

ALLOWED event_type VALUES (choose exactly one):
{allowed}

Return ONLY this JSON:
{{
  "event_type": "<one allowed value>",
  "headline": "<one factual sentence, max 140 chars, describing what the filing states>",
  "importance": "high" | "medium" | "low",
  "requires_action": true | false,
  "effective_date": "<YYYY-MM-DD if the filing states one, else empty string>",
  "entities": {{
    "people": [{{"name": "", "role": "", "din": "", "change": "appointed|resigned|ceased|redesignated|mentioned"}}],
    "organisations": [""],
    "amounts": [""]
  }},
  "evidence_quote": "<verbatim sentence from the filing text, or empty string>",
  "confidence": <0-100 integer: how well the text supports this classification>
}}

importance guidance:
  high   - board/KMP/auditor change, related party transaction, restructuring,
           regulatory action against the company
  medium - financial results, rating action, corporate action, general meeting
  low    - routine periodic certificates, trading window notices, intimations
"""


def analyse_filing(filing: Dict, symbol: str = "", company_name: str = "",
                   use_model: bool = True) -> Dict:
    """Produce a detection record for one filing. Never raises."""
    symbol = (symbol or filing.get("company") or "").upper()
    title = filing.get("title", "") or ""
    description = filing.get("description", "") or ""
    text = filing.get("raw_text", "") or ""

    rules = classify_by_rules(title, description, text)

    detection = {
        "symbol": symbol,
        "company_name": company_name or symbol,
        "filing_id": filing.get("id"),
        "event_type": rules["event_type"],
        "event_label": rules["event_label"],
        "category": rules["category"],
        "importance": rules["importance"],
        "requires_action": rules["requires_action"],
        "draft_type": rules["draft_type"],
        "title": title,
        "headline": title,
        "effective_date": "",
        "entities": {},
        "delta": {},
        "source_ref": {
            "document": title,
            "url": filing.get("pdf_link", "") or "",
            "date": filing.get("date", ""),
            "page": None,
            "quote": "",
        },
        "confidence": 60 if rules["rule_matched"] else 30,
        "model": "rules",
        "latency_ms": 0,
        "method": "rules",
    }

    # ── Delta pass: structured before/after for board events ──
    if rules["event_type"] in ("BOARD_CHANGE", "KMP_CHANGE") and symbol:
        delta = _board_delta_from_governance(symbol)
        if delta:
            detection["delta"] = delta

    # ── Model pass ──
    if not use_model or not gemini_available():
        return detection
    if not (text.strip() or description.strip()):
        # Nothing for the model to read — title-only, trust the rules.
        return detection

    prompt = _PROMPT.format(
        company=company_name or symbol,
        title=title,
        date=filing.get("date", ""),
        description=description[:1500],
        text=text[:12000] if text.strip() else "(no extracted text available)",
        rule_type=rules["event_type"],
        rule_label=rules["event_label"],
        allowed=", ".join(t["type"] for t in EVENT_TAXONOMY),
    )

    res = call_json(prompt, role="monitor", max_output_tokens=1500)
    if not res["ok"] or not isinstance(res["data"], dict):
        detection["model_error"] = res.get("error", "")
        return detection

    data = res["data"]
    etype = data.get("event_type")
    if etype in TAXONOMY_BY_TYPE:
        entry = TAXONOMY_BY_TYPE[etype]
        detection.update(
            event_type=etype,
            event_label=entry["label"],
            category=entry["category"],
            draft_type=entry["draft_type"],
        )

    if data.get("importance") in ("high", "medium", "low"):
        detection["importance"] = data["importance"]
    if isinstance(data.get("requires_action"), bool):
        detection["requires_action"] = data["requires_action"]
    if data.get("headline"):
        detection["headline"] = str(data["headline"])[:200]
    if data.get("effective_date"):
        detection["effective_date"] = str(data["effective_date"])[:10]
    if isinstance(data.get("entities"), dict):
        detection["entities"] = data["entities"]

    # Evidence quote is only trustworthy if it actually appears in the source.
    quote = (data.get("evidence_quote") or "").strip()
    if quote and text and _quote_is_grounded(quote, text):
        detection["source_ref"]["quote"] = quote
        detection["source_ref"]["page"] = _locate_page(quote, text)
    elif quote:
        detection["source_ref"]["quote"] = ""
        detection["source_ref"]["quote_rejected"] = True

    try:
        detection["confidence"] = max(0, min(100, int(data.get("confidence", 60))))
    except (TypeError, ValueError):
        pass

    detection["model"] = res["model"]
    detection["latency_ms"] = res["latency_ms"]
    detection["method"] = "rules+model"
    return detection


def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _quote_is_grounded(quote: str, text: str) -> bool:
    """Reject any 'verbatim' quote that isn't actually in the document.

    This is the guard that keeps evidence trustworthy: a fabricated quote
    attributed to a real company's filing is the worst failure this product
    could have, so an ungrounded quote is dropped rather than shown.
    """
    nq, nt = _normalise(quote), _normalise(text)
    if len(nq) < 15:
        return False
    if nq in nt:
        return True
    # Tolerate minor whitespace/OCR drift: require a long contiguous run.
    head = nq[:60]
    return head in nt


def _locate_page(quote: str, text: str, chars_per_page: int = 3000) -> Optional[int]:
    idx = _normalise(text).find(_normalise(quote)[:60])
    if idx < 0:
        return None
    return idx // chars_per_page + 1


def should_surface(detection: Dict) -> bool:
    """Whether this belongs in the reviewer's queue.

    Routine periodic paperwork is still recorded but doesn't demand attention
    unless the model flagged it as requiring action.
    """
    if detection.get("requires_action"):
        return True
    return detection.get("event_type") not in _ROUTINE
