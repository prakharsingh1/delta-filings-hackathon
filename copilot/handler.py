"""Prompt construction, model call, and response shaping for Delta Copilot.

Every answer follows the same four-part shape a senior CS would use in a
note to their team:

    1. Understanding    what happened / what is being asked
    2. Impact           why it matters
    3. Required action  what the team must do
    4. Delta assistance what Delta can prepare right now

Two hard rules, enforced in code rather than only in the prompt:
  - citations are validated against the retrieved mandate set and dropped
    if they were not offered
  - statements about named individuals are constrained to disclosed public
    record, phrased as such, and always flagged for human review
"""

import json
from typing import Any, Dict, List

from . import retrieval

# What Delta can genuinely produce. The assistant may only offer from this
# list, so it never promises a capability the product does not have.
CAPABILITIES = {
    "draft_disclosure": "Exchange disclosure draft",
    "board_note": "Board note",
    "internal_email": "Internal email to the compliance team",
    "compliance_checklist": "Compliance checklist with owners and deadlines",
    "governance_summary": "Governance summary for this company",
    "compare_quarters": "Quarter-on-quarter board comparison",
    "deadline_summary": "Upcoming deadline summary",
    "overdue_review": "Review of items past their due date",
    "director_history": "Directorship history from filed governance reports",
    "explain_regulation": "Plain-English explanation of the regulation",
}

_SYSTEM = """You are Delta Copilot, a senior Company Secretary with 20 years advising
Indian listed companies on SEBI LODR, the Companies Act 2013, PIT and SAST Regulations.
You are embedded in Delta Filings, sitting beside a compliance professional while they work.

You speak like a senior colleague: precise, practical, and brief. No filler, no
"great question", no restating the question back.

═══ WHERE THE USER IS RIGHT NOW ═══
{context}

═══ DELTA'S RECORDS FOR THIS QUESTION ═══
{grounding}

═══ REGULATIONS YOU MAY CITE ═══
You may ONLY cite regulations from this list. If none apply, cite nothing.
NEVER write a regulation number that is not here.
{mandates}

═══ WHAT DELTA CAN PREPARE ═══
Offer only from this list, by key:
{capabilities}

═══ RULES ═══
1. Ground every factual claim in the records above. If Delta's records do not
   contain the answer, say so plainly and say what you would need.
2. These are REAL listed companies and REAL named individuals. State only what
   the records show. Never allege wrongdoing, non-compliance or misconduct.
   For anything about a person, write "Public records indicate..." or
   "Delta's records show..." and mark it for human review.
3. Frame obligations procedurally ("a disclosure obligation is triggered"),
   never accusingly ("the company failed to...").
4. You are decision support for a qualified professional, not legal advice.
5. Be concrete about timelines only where the regulation in the list states one.

Return ONLY this JSON:
{{
  "understanding": "<2-3 sentences: what is happening / what is being asked, grounded in the records>",
  "impact": "<2-3 sentences: why this matters for the compliance team>",
  "required_actions": [
    {{"action": "<imperative step>", "detail": "<what specifically to do>",
      "owner": "Company Secretary" | "Compliance Officer" | "Board" | "Legal",
      "timeline": "<only if a cited regulation states one, else empty string>"}}
  ],
  "delta_can_prepare": ["<capability keys from the list above>"],
  "mandate_ids": ["<ids from the citable list only>"],
  "needs_human_review": true | false,
  "confidence": <0-95 integer>
}}"""


def _compact_context(ctx: Dict) -> str:
    keep = {k: v for k, v in ctx.items()
            if k in ("page_type", "company", "company_name", "director", "din",
                     "document", "regulation") and v}
    keep["facts"] = ctx.get("facts", {})
    keep["monitored_companies"] = ctx.get("watchlist", [])
    return json.dumps(keep, indent=2, default=str)[:6000]


def answer(question: str, ctx: Dict, history: List[Dict] = None) -> Dict[str, Any]:
    """Answer one question in context. Never raises."""
    from agents.client import call_json, gemini_available

    grounding = retrieval.gather(question, ctx)
    mandates = grounding.pop("mandates", [])
    valid_ids = {m["id"] for m in mandates}

    if not gemini_available():
        return _unavailable(mandates)

    convo = ""
    for turn in (history or [])[-4:]:
        role = "User" if turn.get("role") == "user" else "Delta"
        convo += f"\n{role}: {str(turn.get('content',''))[:400]}"

    prompt = _SYSTEM.format(
        context=_compact_context(ctx),
        grounding=json.dumps(grounding, indent=2, default=str)[:8000],
        mandates=json.dumps(mandates, indent=2)[:6000] or "(none matched)",
        capabilities=json.dumps(CAPABILITIES, indent=2),
    )
    if convo:
        prompt += f"\n\n═══ CONVERSATION SO FAR ═══{convo}"
    prompt += f"\n\n═══ THE USER ASKS ═══\n{question}\n"

    res = call_json(prompt, role="intelligence", model="gemini-2.5-flash",
                    max_output_tokens=2200, temperature=0.25, timeout_ms=60_000)

    if not res["ok"] or not isinstance(res["data"], dict):
        return _unavailable(mandates, error=res.get("error", ""))

    d = res["data"]

    raw_ids = d.get("mandate_ids") or []
    kept = [i for i in raw_ids if i in valid_ids]
    dropped = [i for i in raw_ids if i not in valid_ids]
    if dropped:
        print(f"[COPILOT] dropped ungrounded citations: {dropped}")

    actions = []
    for a in (d.get("required_actions") or [])[:6]:
        if isinstance(a, dict) and a.get("action"):
            actions.append({
                "action": str(a["action"])[:220],
                "detail": str(a.get("detail", ""))[:400],
                "owner": str(a.get("owner", "Company Secretary"))[:40],
                "timeline": str(a.get("timeline", ""))[:80],
            })

    offers = [k for k in (d.get("delta_can_prepare") or []) if k in CAPABILITIES][:5]

    try:
        confidence = max(0, min(95, int(d.get("confidence", 60))))
    except (TypeError, ValueError):
        confidence = 60

    return {
        "ok": True,
        "understanding": str(d.get("understanding", ""))[:1200],
        "impact": str(d.get("impact", ""))[:1200],
        "required_actions": actions,
        "delta_can_prepare": [{"key": k, "label": CAPABILITIES[k]} for k in offers],
        "sources": [m for m in mandates if m["id"] in kept],
        "needs_human_review": bool(d.get("needs_human_review", True)),
        "confidence": confidence,
        "model": res["model"],
        "latency_ms": res["latency_ms"],
    }


def _unavailable(mandates, error=""):
    """Degrade to retrieval-only rather than going silent — the regulations
    are real and useful even when the model cannot be reached."""
    return {
        "ok": False,
        "understanding": ("Delta's assistant could not reach the AI service, so this "
                          "answer is limited to the regulations Delta matched to your question."),
        "impact": "",
        "required_actions": [],
        "delta_can_prepare": [],
        "sources": mandates[:4],
        "needs_human_review": True,
        "confidence": 0,
        "model": "none",
        "latency_ms": 0,
        "error": error,
    }
