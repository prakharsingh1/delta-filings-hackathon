"""Agent 3 — Action Execution Agent.

Persona: Company Secretary's Documentation Assistant.
Question it answers: "What do we actually send, and to whom?"

Drafting is NOT rebuilt here. draft_engine.py already knows 101 filing types,
how each letter is structured, how to learn a company's house style from its
past filings, and how to render PDF/DOCX. This agent's job is narrower:

    1. pick the right filing type for the event (from the closed FILING_TYPES list)
    2. hand draft_engine the event context as `new_info`
    3. produce the supporting internal artifacts (board note, email, checklist)
    4. persist everything against the event, and into user_drafts so the
       existing /draft editor and export pipeline can open it

Every generated document carries a review banner. These are first drafts for a
qualified professional to check, and they say so.
"""

import json
from typing import Any, Dict, List, Optional

from .client import call_json, gemini_available

REVIEW_BANNER = (
    "AI-GENERATED FIRST DRAFT — requires Company Secretary review before filing. "
    "Verify all facts, dates, names and regulatory references against source records."
)

ARTIFACT_LABELS = {
    "disclosure_letter": "Stock Exchange Disclosure",
    "board_note": "Board Note",
    "internal_email": "Internal Email",
    "compliance_checklist": "Compliance Checklist",
}


def select_filing_type(event: Dict, analysis: Dict) -> str:
    """Map the event to a draft_engine filing type.

    The taxonomy already carries a default; we only confirm it exists in the
    registry so a taxonomy edit can never produce an unknown type.
    """
    from draft_engine import get_filing_type

    candidate = event.get("draft_type") or ""
    if candidate and get_filing_type(candidate):
        return candidate

    from .monitor import TAXONOMY_BY_TYPE
    entry = TAXONOMY_BY_TYPE.get(event.get("event_type", ""), {})
    fallback = entry.get("draft_type", "reg30_disclosure")
    return fallback if get_filing_type(fallback) else "reg30_disclosure"


def _build_new_info(event: Dict, analysis: Dict) -> str:
    """The factual brief handed to draft_engine. Only grounded facts go in."""
    parts = [
        f"Company: {event.get('company_name') or event.get('symbol')}",
        f"Event: {event.get('event_label')}",
        f"What the filing states: {event.get('headline', '')}",
    ]
    if event.get("effective_date"):
        parts.append(f"Effective date: {event['effective_date']}")

    ents = event.get("entities") or {}
    people = ents.get("people") or []
    if people:
        who = "; ".join(
            f"{p.get('name','')} ({p.get('role','')}{', DIN ' + p['din'] if p.get('din') else ''}) — {p.get('change','')}"
            for p in people if isinstance(p, dict) and p.get("name")
        )
        if who:
            parts.append(f"Persons concerned: {who}")

    delta = event.get("delta") or {}
    if delta.get("before") or delta.get("after"):
        parts.append(f"Change detected: {json.dumps(delta.get('before'))} -> {json.dumps(delta.get('after'))}")

    if analysis.get("regulatory_area"):
        parts.append(f"Regulatory area: {analysis['regulatory_area']}")

    quote = (event.get("source_ref") or {}).get("quote")
    if quote:
        parts.append(f"Source extract: \"{quote}\"")

    # The draft engine writes fluent regulatory prose, which means it will
    # happily supply a plausible-sounding REASON for a cessation that the
    # source never stated. Attributing an invented reason to a named real
    # director in a document formatted as an exchange filing is the single
    # worst output this product could produce, so the constraint is explicit.
    parts.append(
        "\n--- STRICT FACTUAL CONSTRAINTS (do not violate) ---\n"
        "Use ONLY the facts listed above. In particular:\n"
        "- Do NOT state or imply a REASON for any appointment, cessation, "
        "resignation or retirement unless a reason is explicitly given above. "
        "Do not write 'due to completion of tenure', 'on account of personal "
        "reasons', 'upon retirement' or any similar phrase unless it appears above.\n"
        "- Do NOT invent dates, DINs, designations, meeting numbers or figures. "
        "Where a required fact is not given above, write a bracketed placeholder "
        "such as [REASON TO BE CONFIRMED] or [DATE TO BE CONFIRMED].\n"
        "- Do NOT characterise any person or the company favourably or "
        "unfavourably. State only what occurred."
    )
    return "\n".join(parts)


# Phrases that assert a reason for a board change. Flagged when the source
# never supplied one, so a reviewer sees exactly what needs verifying.
_REASON_PATTERNS = [
    r"due to completion of (?:his|her|their) tenure",
    r"upon completion of (?:his|her|their) (?:tenure|term)",
    r"on account of personal reasons",
    r"due to personal reasons",
    r"(?:has|have) retired",
    r"upon (?:his|her|their) retirement",
    r"pursuant to (?:his|her|their) resignation",
    r"consequent to (?:his|her|their) (?:resignation|retirement)",
    r"in view of (?:his|her|their) (?:other commitments|pre-occupation)",
]


def scan_unsupported_claims(body: str, event: Dict) -> List[str]:
    """Flag reason-attributions the source evidence does not support.

    Not censorship — the draft still renders. The reviewer is told which
    sentences were not derived from the filing so they can verify or strike
    them before anything is submitted.
    """
    import re

    source_blob = " ".join([
        str(event.get("headline", "")),
        str((event.get("source_ref") or {}).get("quote", "")),
        json.dumps(event.get("entities") or {}),
        json.dumps(event.get("delta") or {}),
    ]).lower()

    flags = []
    for pat in _REASON_PATTERNS:
        for m in re.finditer(pat, body, re.I):
            phrase = m.group(0)
            if phrase.lower() not in source_blob:
                flags.append(phrase)
    return sorted(set(flags))


def generate_disclosure(event: Dict, analysis: Dict, api_key: str = "") -> Optional[Dict]:
    """Delegate to the existing draft engine for the exchange disclosure."""
    from draft_engine import generate_draft, find_past_examples, get_filing_type
    from .client import get_api_key

    api_key = api_key or get_api_key()
    if not api_key:
        return None

    symbol = event.get("symbol", "")
    filing_type = select_filing_type(event, analysis)
    ftype = get_filing_type(filing_type) or {}

    # Past examples give the draft the company's own house style.
    try:
        examples = find_past_examples(symbol, filing_type, max_examples=3, extract_text=True)
    except Exception as e:
        print(f"[AGENT3] past-example lookup failed for {symbol}/{filing_type}: {str(e)[:120]}")
        examples = []

    try:
        draft = generate_draft(
            api_key=api_key,
            company_name=event.get("company_name") or symbol,
            filing_type_id=filing_type,
            new_info=_build_new_info(event, analysis),
            past_examples=examples,
            symbol=symbol,
        )
    except Exception as e:
        print(f"[AGENT3] generate_draft failed: {str(e)[:160]}")
        return None

    if not draft or not draft.get("body"):
        return None

    return {
        "artifact_type": "disclosure_letter",
        "title": draft.get("subject") or ftype.get("subject_default") or ARTIFACT_LABELS["disclosure_letter"],
        "body": draft.get("body", ""),
        "filing_type": filing_type,
        "metadata": {
            "subject": draft.get("subject", ""),
            "annexures": draft.get("annexures", []),
            "filing_type_label": ftype.get("label", ""),
            "examples_used": len(examples),
            "review_required": True,
            "banner": REVIEW_BANNER,
        },
    }


_SUPPORT_PROMPT = """You are a Company Secretary's documentation assistant at an Indian
listed company. A governance event has been assessed by the senior CS. Prepare the
internal working documents the team needs tomorrow morning.

═══ EVENT ═══
Company: {company} ({symbol})
Event:   {event_label}
States:  {headline}
Effective date: {effective_date}

═══ SENIOR CS ASSESSMENT ═══
Finding:        {finding}
Why it matters: {why}
Regulatory area:{reg_area}
Risk level:     {risk}
Affected areas: {affected}

Applicable requirements (already verified — cite these exactly, add no others):
{mandates}

Recommended actions from the senior CS:
{actions}

═══ RULES ═══
- Use ONLY the facts above. Invent no names, dates, figures or regulation numbers.
- Reference regulations only from the verified list.
- These are internal working drafts for professional review — factual and procedural
  in tone, never alleging wrongdoing.
- Where a fact is needed but unknown, write a bracketed placeholder like
  [DATE TO BE CONFIRMED] rather than guessing.

Return ONLY this JSON:
{{
  "board_note": {{
    "title": "<agenda item title>",
    "body": "<a concise board/committee note: background, what changed, the compliance requirement triggered, and what the board is being asked to note or approve. 150-250 words. Plain paragraphs.>"
  }},
  "internal_email": {{
    "subject": "<email subject line>",
    "body": "<a short internal email from the CS team to the compliance group: what happened, what is required, who must do what, by when. 80-140 words.>"
  }},
  "compliance_checklist": {{
    "title": "<checklist title>",
    "items": [
      {{"task": "<specific verifiable task>", "regulation": "<id from the verified list, or empty>", "owner": "<role>", "due": "<e.g. 'Within 24 hours of the event' or 'Before next board meeting'>"}}
    ]
  }}
}}
"""


def generate_support_artifacts(event: Dict, analysis: Dict) -> List[Dict]:
    """Board note, internal email and compliance checklist in one Flash call."""
    if not gemini_available():
        return []

    from .intelligence import resolve_mandates
    mandates = resolve_mandates(analysis.get("mandate_ids") or [])
    mandate_brief = [
        {"id": m["id"], "regulation": m.get("regulation"), "name": m.get("name"),
         "penalty": m.get("penalty", "")}
        for m in mandates
    ]

    prompt = _SUPPORT_PROMPT.format(
        company=event.get("company_name") or event.get("symbol"),
        symbol=event.get("symbol", ""),
        event_label=event.get("event_label", ""),
        headline=event.get("headline", ""),
        effective_date=event.get("effective_date") or "not stated",
        finding=analysis.get("finding", ""),
        why=analysis.get("why_it_matters", ""),
        reg_area=analysis.get("regulatory_area", ""),
        risk=analysis.get("risk_level", ""),
        affected=", ".join(analysis.get("affected_areas") or []) or "not specified",
        mandates=json.dumps(mandate_brief, indent=2) if mandate_brief else "(none identified)",
        actions=json.dumps(analysis.get("recommended_actions") or [], indent=2),
    )

    res = call_json(prompt, role="action", max_output_tokens=2500, temperature=0.3)
    if not res["ok"] or not isinstance(res["data"], dict):
        print(f"[AGENT3] support artifacts failed: {res.get('error','')[:120]}")
        return []

    d = res["data"]
    out: List[Dict] = []

    bn = d.get("board_note") or {}
    if bn.get("body"):
        out.append({
            "artifact_type": "board_note",
            "title": str(bn.get("title") or ARTIFACT_LABELS["board_note"])[:200],
            "body": str(bn["body"])[:6000],
            "metadata": {"review_required": True, "banner": REVIEW_BANNER},
            "model": res["model"],
        })

    em = d.get("internal_email") or {}
    if em.get("body"):
        out.append({
            "artifact_type": "internal_email",
            "title": str(em.get("subject") or ARTIFACT_LABELS["internal_email"])[:200],
            "body": str(em["body"])[:4000],
            "metadata": {"subject": em.get("subject", ""), "review_required": True,
                         "banner": REVIEW_BANNER},
            "model": res["model"],
        })

    cl = d.get("compliance_checklist") or {}
    items = [i for i in (cl.get("items") or []) if isinstance(i, dict) and i.get("task")][:12]
    if items:
        lines = []
        for i in items:
            reg = f" [{i['regulation']}]" if i.get("regulation") else ""
            due = f" — due: {i['due']}" if i.get("due") else ""
            owner = f" ({i['owner']})" if i.get("owner") else ""
            lines.append(f"- {i['task']}{reg}{owner}{due}")
        out.append({
            "artifact_type": "compliance_checklist",
            "title": str(cl.get("title") or ARTIFACT_LABELS["compliance_checklist"])[:200],
            "body": "\n".join(lines),
            "metadata": {"items": items, "review_required": True, "banner": REVIEW_BANNER},
            "model": res["model"],
        })

    return out


def run(event: Dict, analysis: Dict, user_id: str = "", persist_draft: bool = True) -> List[Dict]:
    """Produce every artifact for one event and persist them. Never raises."""
    import events_store as es

    artifacts: List[Dict] = []

    disclosure = None
    try:
        disclosure = generate_disclosure(event, analysis)
    except Exception as e:
        print(f"[AGENT3] disclosure generation error: {str(e)[:160]}")

    if disclosure:
        artifacts.append(disclosure)
    artifacts.extend(generate_support_artifacts(event, analysis))

    if not artifacts:
        return []

    for a in artifacts:
        flags = scan_unsupported_claims(a.get("body", ""), event)
        if flags:
            a.setdefault("metadata", {})["unsupported_claims"] = flags
            print(f"[AGENT3] unsupported reason-attribution in {a['artifact_type']}: {flags}")

    # Replace rather than append, so a re-run doesn't stack duplicates.
    es.clear_artifacts(event["id"])

    saved = []
    for a in artifacts:
        draft_id = None
        # The exchange disclosure also lands in user_drafts so it opens in the
        # existing /draft editor with PDF/DOCX export already wired up.
        if persist_draft and user_id and a["artifact_type"] == "disclosure_letter":
            try:
                from drafts_store import save_draft
                draft_id = save_draft(
                    user_id=user_id,
                    company_symbol=event.get("symbol", ""),
                    company_name=event.get("company_name") or event.get("symbol", ""),
                    filing_type=a.get("filing_type", ""),
                    subject=a.get("metadata", {}).get("subject", "") or a["title"],
                    body=a["body"],
                    annexures=a.get("metadata", {}).get("annexures", []),
                    metadata={"source": "delta_action_agent", "event_id": event["id"]},
                    new_info=_build_new_info(event, analysis),
                )
            except Exception as e:
                print(f"[AGENT3] save_draft failed: {str(e)[:160]}")

        aid = es.save_artifact(
            event["id"],
            artifact_type=a["artifact_type"],
            title=a["title"],
            body=a["body"],
            filing_type=a.get("filing_type", ""),
            draft_id=draft_id,
            metadata=a.get("metadata", {}),
            model=a.get("model", ""),
        )
        a["id"] = aid
        a["draft_id"] = draft_id
        saved.append(a)

    return saved
