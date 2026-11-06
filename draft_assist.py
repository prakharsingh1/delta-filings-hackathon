"""AI work on the draft desk: compose, fill, rewrite, learn a type's voice.

Everything here goes through draft_ai.generate (timeouts, model fallback,
honest errors) and draft_memory (the team's rules, the company's house
format and facts, approved examples).

Fill is deliberately strict. The old fill let Google Search answer
placeholders, and it produced a target company's turnover, a deal
consideration and a CEO quote the user never gave. Here a value is kept only
when it can be traced: company particulars come straight from memory without
a model, and every model answer must quote its evidence from the brief, the
company facts, the company's own filings or the document itself, and every
number in the value must appear in that material.
"""
import re
from datetime import date
from typing import Any, Dict, List, Optional

import draft_ai
import draft_memory
from draft_ai import AIError

SYSTEM = (
    "You are a senior Company Secretary in India drafting statutory correspondence for a listed "
    "company. Accuracy beats fluency. You never invent names, dates, amounts, DINs, addresses or "
    "quotations: when a fact is not in the material you are given, you leave a clearly marked "
    "placeholder. You write in the company's own house style when one is supplied."
)

PLACEHOLDER_RE = re.compile(r"\[\s*PLEASE\s*FILL[^\]]*\]|\{[a-z][a-z0-9_]{2,60}\}", re.I)

_KIND_GUIDE = {
    "letter": "a letter to the stock exchanges (BSE and NSE)",
    "resolution": "a formal resolution ('RESOLVED THAT …', 'RESOLVED FURTHER THAT …', with an "
                  "authorisation to give effect); no salutation",
    "notice": "a meeting notice with numbered agenda items and an explanatory statement under "
              "section 102 of the Companies Act for special business",
    "minutes": "minutes compliant with SS-1 / SS-2: attendance, quorum, items, decisions, in the past tense",
    "document": "a formal governance document in policy or certificate language; no exchange salutation",
    "certificate": "a certificate in precise certifying language ('I/We hereby certify that …') "
                   "stating the basis and period",
}


def _requirements(filing_type: str) -> Dict[str, Any]:
    try:
        import draft_requirements
        return draft_requirements.for_type(filing_type)
    except Exception:  # noqa: BLE001
        return {"items": [], "derived": True, "sources": []}


def _examples_block(examples: List[Dict]) -> str:
    out = []
    for i, ex in enumerate(examples, 1):
        out.append(f"--- EXAMPLE {i}: {ex['label']} — {ex.get('title', '')} ---\n{ex['text'].strip()}")
    return "\n\n".join(out)


# ── Compose ──────────────────────────────────────────────

COMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "annexures": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            "required": ["title", "body"]}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subject", "body", "annexures"],
}


def compose(api_key: str, owner_id: str, symbol: str, company_name: str, filing_type: str,
            brief: str, subject: str = "", past_examples: Optional[List[Dict]] = None) -> Dict[str, Any]:
    from draft_engine import get_filing_type
    ft = get_filing_type(filing_type) or {}
    kind = ft.get("doc_kind") or "letter"
    ctx = draft_memory.prompt_context(owner_id, symbol, filing_type, past_examples=past_examples)
    reqs = _requirements(filing_type)
    legal = ctx["facts_dict"].get("legal_name") or company_name

    req_lines = "\n".join(f"- {r['label']}: {r['requirement']}" + (f" (when: {r['applies_when']})" if r.get("applies_when") else "")
                          for r in reqs.get("items", []))
    parts = [
        f"Draft {_KIND_GUIDE.get(kind, _KIND_GUIDE['letter'])} for {legal} (NSE: {symbol}).",
        f"Filing type: {ft.get('label', filing_type)}.",
        f"Today is {date.today().strftime('%d %B %Y')}.",
    ]
    if ft.get("body_hint"):
        parts.append(f"What this filing usually covers: {ft['body_hint']}")
    if ft.get("annexure_hints"):
        parts.append(f"Usual annexures: {ft['annexure_hints']}")
    if req_lines:
        label = ("Items this filing must disclose (from the regulator's format)" if not reqs.get("derived")
                 else "Items this filing usually covers")
        parts.append(f"{label}. Cover every item that applies; where the brief lacks the fact, put a "
                     f"placeholder that names the item:\n{req_lines}")
    if ctx["rules"]:
        parts.append("The team's own drafting rules. These override anything else on wording:\n" + ctx["rules"])
    if ctx["format"]:
        parts.append(ctx["format"])
    if ctx["facts"]:
        parts.append("Company particulars (use them verbatim; never alter them):\n" + ctx["facts"])
    if ctx["examples"]:
        parts.append("Write like these letters from the same company and team. Reuse their standing "
                     "phrases and structure, never their facts:\n" + _examples_block(ctx["examples"]))
    parts.append("WHAT HAPPENED (the user's brief; the only source of event facts):\n" + (brief or "").strip())
    if subject:
        parts.append(f"Preferred subject: {subject}")
    parts.append(
        "Output rules:\n"
        "- subject: the subject line text only, without a 'Sub:' label.\n"
        "- body: the letter body only. Do NOT write the letterhead, date, addressee, subject, salutation, "
        "'Thanking you', the sign-off or the signature block: the template adds them in the company's format.\n"
        "- Use the company's name exactly as given in the particulars.\n"
        "- Missing facts: write [PLEASE FILL — <exactly what is needed>], e.g. "
        "[PLEASE FILL — DIN of the new director]. Never a bare [PLEASE FILL] in prose. Inside a table cell "
        "a bare [PLEASE FILL] is fine.\n"
        "- Tables: markdown pipe tables.\n"
        "- annexures: only those this filing actually needs, each with substantive content.\n"
        "- open_questions: short questions for the CS about facts the brief did not give (max 6)."
    )
    res = draft_ai.generate(api_key, "\n\n".join(parts), schema=COMPOSE_SCHEMA, system=SYSTEM,
                            max_output_tokens=12000, deadline_s=120, call_timeout_s=95, purpose="compose")
    data = res["data"] or {}
    body = (data.get("body") or "").strip()
    if not body:
        raise AIError("bad_output", "Gemini returned an empty draft. Try again, or add more detail to the brief.")
    annexures = [{"title": _clean_markup(a.get("title") or ""), "body": _clean_markup(a.get("body") or "")}
                 for a in (data.get("annexures") or []) if (a.get("title") or "").strip() and (a.get("body") or "").strip()]
    return {
        "subject": (data.get("subject") or subject or ft.get("subject_default") or "").strip(),
        "body": _strip_shell(_clean_markup(body), ctx["house"]),
        "annexures": annexures,
        "open_questions": [q for q in (data.get("open_questions") or []) if isinstance(q, str)][:6],
        "model": res["model"],
        "seconds": res["seconds"],
        "used": {
            "letters_learned": bool(ctx["format"]),
            "rules": bool(ctx["rules"]),
            "facts": len(ctx["facts_dict"]),
            "examples": [f"{e['label']}: {e.get('title', '')}" for e in ctx["examples"]],
            "requirements": len(reqs.get("items", [])),
        },
    }


def _clean_markup(text: str) -> str:
    """The letter renderers understand paragraphs and pipe tables only, so
    headings, bold markers and HTML line breaks would print literally."""
    text = re.sub(r"<br\s*/?>", "; ", text or "", flags=re.I)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return text.strip()


def _strip_shell(body: str, house: Dict[str, Any]) -> str:
    """Models sometimes add the salutation or sign-off the template already
    prints; remove them so the letter does not say them twice."""
    lines = body.strip().split("\n")
    while lines and re.match(r"^\s*(dear\s+sir|dear\s+madam|sir\s*/\s*madam|to,?\s*$)", lines[0], re.I):
        lines.pop(0)
    tail = re.compile(r"^\s*(thank(ing)?\s+you|yours\s+(faithfully|truly|sincerely)|for\s+.{3,80}(limited|ltd\.?)\s*$|"
                      r"\(?authori[sz]ed\s+signatory\)?|company\s+secretary|encl)", re.I)
    while lines and (not lines[-1].strip() or tail.match(lines[-1])):
        lines.pop()
    return "\n".join(lines).strip()


# ── Fill ─────────────────────────────────────────────────

_FACT_FIELD_RX = [
    ("cin", r"\bCIN\b|corporate\s+identi"),
    ("isin", r"\bISIN\b"),
    ("bse_code", r"scrip\s*code|security\s*code|\bBSE\b.*code"),
    ("nse_symbol", r"\bNSE\b.*symbol|^symbol$"),
    ("registered_office", r"registered\s+office|regd\.?\s+office"),
    ("legal_name", r"^(company|name\s+of\s+the\s+(listed\s+)?(company|entity))$"),
    ("cs_membership", r"membership"),
    ("cs_name", r"name\s+of\s+(the\s+)?(company\s+secretary|compliance\s+officer)|^company\s+secretary$"),
    ("email", r"e-?mail"),
    ("website", r"website"),
    ("phone", r"telephone|phone"),
]
_OTHER_PARTY = re.compile(r"target|acqui|subsidiar|auditor|director|appointee|counterpart|scrutini[sz]er|"
                          r"agency|bank|trustee|registrar|proposed|new\b|other", re.I)

FILL_SCHEMA = {
    "type": "object",
    "properties": {
        "fills": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "value": {"type": "string"},
                "source": {"type": "string", "enum": ["brief", "company_facts", "filing", "document"]},
                "evidence": {"type": "string"},
            },
            "required": ["id", "value", "source", "evidence"]}},
        "unfilled": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["id", "reason"]}},
    },
    "required": ["fills", "unfilled"],
}

_REFUSALS = {"not specified", "unknown", "n/a", "na", "not available", "not provided", "not stated",
             "not mentioned", "to be determined", "tbd", "tba", "please specify", "none", "-", "—"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _numbers(s: str) -> List[str]:
    return [n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or "")]


def _company_material(symbol: str, limit: int = 3) -> str:
    """The company's latest filings with text: dates and names in a board
    outcome often answer the next letter's placeholders."""
    try:
        from database import get_cached_filings
        rows = [f for f in (get_cached_filings(symbol) or []) if len(f.get("raw_text") or "") > 300][:limit]
    except Exception:  # noqa: BLE001
        rows = []
    return "\n\n".join(f"--- FILING: {f.get('title', '')} ({f.get('date', '')}) ---\n{(f.get('raw_text') or '')[:1800]}"
                       for f in rows)


def fill_fields(api_key: Optional[str], owner_id: str, symbol: str, company_name: str, filing_type: str,
                brief: str, document: str, fields: List[Dict]) -> Dict[str, Any]:
    """Fill placeholder fields by id.

    `fields`: [{id, label, context}] — one per placeholder occurrence.
    `document`: the full text with each placeholder replaced by ⟦<id>⟧.
    Returns {"fills": [{id, value, source, source_label, evidence}],
             "unfilled": [{id, reason}], "error": AIError dict or None}."""
    facts = draft_memory.facts_for(owner_id, symbol)
    fvals = draft_memory.fact_values(facts)
    fills: List[Dict] = []
    remaining: List[Dict] = []

    for f in fields:
        label = (f.get("label") or "").strip()
        key = None
        if not _OTHER_PARTY.search(label):
            for k, rx in _FACT_FIELD_RX:
                if re.search(rx, label, re.I) and fvals.get(k):
                    key = k
                    break
        if key:
            fills.append({"id": f["id"], "value": fvals[key], "source": "company_facts",
                          "source_label": "Company profile",
                          "evidence": f"{draft_memory.FACT_LABELS[key]}: {facts[key].get('detail', '')}"})
        else:
            remaining.append(f)

    out = {"fills": fills, "unfilled": [], "error": None, "model": None}
    if not remaining:
        return out
    if not api_key:
        out["unfilled"] = [{"id": f["id"], "reason": "AI is not configured"} for f in remaining]
        out["error"] = AIError("no_key", "No Gemini API key is configured, so only company particulars "
                                         "were filled.").to_dict()
        return out

    material = _company_material(symbol)
    fact_text = "\n".join(f"{draft_memory.FACT_LABELS[k]}: {v}" for k, v in fvals.items())
    field_lines = "\n".join(f"- id {f['id']}: {f.get('label') or 'value'}" +
                            (f"  | context: {f['context'][:220]}" if f.get("context") else "")
                            for f in remaining[:80])
    prompt = (
        f"Fill placeholder fields in a draft for {fvals.get('legal_name') or company_name} (NSE: {symbol}). "
        f"Filing type: {filing_type}.\n\n"
        "Each placeholder in the DOCUMENT is shown as ⟦id⟧. Fill a field ONLY when the answer is stated in "
        "the BRIEF, the COMPANY FACTS, the COMPANY FILINGS, or elsewhere in the DOCUMENT. For every fill give "
        "`evidence`: a short exact quote from that material that contains the answer. If the answer is not "
        "there, put the id in `unfilled` with a one-line reason (e.g. 'Not in your brief: the date of "
        "appointment'). Do not guess, estimate, look anything up or write quotations for people. Values are "
        "short and match the field: a date, a name, a number, a phrase. Write dates in the same style as "
        "the document.\n\n"
        f"FIELDS:\n{field_lines}\n\n"
        f"BRIEF:\n{(brief or '(none)').strip()}\n\n"
        f"COMPANY FACTS:\n{fact_text or '(none)'}\n\n"
        f"COMPANY FILINGS:\n{material or '(none)'}\n\n"
        f"DOCUMENT:\n{document[:14000]}"
    )
    try:
        res = draft_ai.generate(api_key, prompt, schema=FILL_SCHEMA, system=SYSTEM, max_output_tokens=6000,
                                deadline_s=70, call_timeout_s=55, fast=True, purpose="fill")
    except AIError as e:
        out["unfilled"] = [{"id": f["id"], "reason": "Not attempted"} for f in remaining]
        out["error"] = e.to_dict()
        return out
    out["model"] = res["model"]

    sources = {"brief": brief or "", "company_facts": fact_text, "filing": material,
               "document": re.sub(r"⟦[^⟧]+⟧", " ", document)}
    everything = _norm(" ".join(sources.values()))
    every_number = set(_numbers(" ".join(sources.values())))
    wanted = {str(f["id"]) for f in remaining}
    labels = {"brief": "Your brief", "company_facts": "Company profile", "filing": "Company filing",
              "document": "Elsewhere in the draft"}
    got = set()
    for item in (res["data"] or {}).get("fills") or []:
        fid, value = str(item.get("id", "")), " ".join(str(item.get("value", "")).split())
        if fid not in wanted or fid in got or not value or value.lower().strip(" .") in _REFUSALS:
            continue
        evidence = " ".join(str(item.get("evidence", "")).split())
        src = item.get("source") if item.get("source") in sources else "brief"
        ev_ok = bool(evidence) and (_norm(evidence) in _norm(sources[src]) or _norm(evidence) in everything)
        nums_ok = all(n in every_number for n in _numbers(value)
                      if len(n) > 2 or not re.search(r"\b(19|20)\d\d\b", value))
        if not (ev_ok and nums_ok):
            out["unfilled"].append({"id": fid, "reason": "The answer could not be traced to your brief or "
                                                         "the company's records, so it was left for you"})
            got.add(fid)
            continue
        fills.append({"id": fid, "value": value[:400], "source": src, "source_label": labels[src],
                      "evidence": evidence[:240]})
        got.add(fid)
    for item in (res["data"] or {}).get("unfilled") or []:
        fid = str(item.get("id", ""))
        if fid in wanted and fid not in got:
            out["unfilled"].append({"id": fid, "reason": str(item.get("reason") or "Not in your material")[:200]})
            got.add(fid)
    for f in remaining:
        if str(f["id"]) not in got:
            out["unfilled"].append({"id": f["id"], "reason": "Not in your brief or the company's records"})
    return out


# ── Rewrite ──────────────────────────────────────────────

_MODES = {
    "polish": "Fix grammar, punctuation and awkward phrasing. Keep the meaning and every fact.",
    "house": "Rewrite so it reads exactly like the company's own letters: their standing phrases, citation "
             "style, date format and closing line. Keep every fact.",
    "formal": "Make it more formal and SEBI-conventional ('pursuant to', 'we hereby inform'). Keep every fact.",
    "shorter": "Make it tighter: cut repetition and filler. Keep every fact and every required disclosure.",
    "custom": "Follow the user's instruction.",
}


def rewrite(api_key: str, owner_id: str, symbol: str, filing_type: str, text: str,
            mode: str = "polish", instruction: str = "") -> Dict[str, Any]:
    if mode not in _MODES:
        mode = "polish"
    ctx = draft_memory.prompt_context(owner_id, symbol, filing_type, max_examples=2)
    parts = [f"Task: {_MODES[mode]}"]
    if mode == "custom":
        parts.append(f"User instruction: {instruction.strip()}")
    if ctx["rules"]:
        parts.append("The team's drafting rules:\n" + ctx["rules"])
    if ctx["format"]:
        parts.append(ctx["format"])
    if mode == "house" and ctx["examples"]:
        parts.append("Letters to imitate (style only, never their facts):\n" + _examples_block(ctx["examples"]))
    parts.append("Keep every [PLEASE FILL …] placeholder exactly as written unless the instruction fills it. "
                 "Keep markdown tables as tables. Return only the rewritten text: no greeting, no sign-off, "
                 "no commentary.")
    parts.append("--- TEXT ---\n" + text.strip())
    res = draft_ai.generate(api_key, "\n\n".join(parts), system=SYSTEM, max_output_tokens=8000,
                            deadline_s=90, call_timeout_s=70, purpose="rewrite")
    new = res["text"].strip()
    new = re.sub(r"^```\w*\s*|\s*```$", "", new).strip()
    before = len(PLACEHOLDER_RE.findall(text))
    after = len(PLACEHOLDER_RE.findall(new))
    warning = None
    if mode != "custom" and after < before:
        warning = f"The rewrite dropped {before - after} placeholder(s). Review before keeping it."
    return {"text": new, "model": res["model"], "warning": warning}


# ── Learn a filing type's voice ──────────────────────────

TYPE_SCHEMA = {
    "type": "object",
    "properties": {
        "tone": {"type": "string"},
        "phrases": {"type": "array", "items": {"type": "string"}},
        "structure": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tone", "phrases", "structure"],
}


def learn_type_voice(api_key: str, owner_id: str, symbol: str, filing_type: str,
                     letters: List[Dict]) -> Optional[Dict[str, Any]]:
    """From past letters of one filing type: tone, standing phrases quoted
    verbatim, and the order things are said in."""
    texts = [l for l in letters if len((l.get("text") or "").strip()) > 200][:8]
    if len(texts) < 2:
        return None
    body = "\n\n".join(f"--- LETTER {i} ({l.get('date', '')}) ---\n{l['text'][:2500]}" for i, l in enumerate(texts, 1))
    prompt = (
        f"These are {len(texts)} past letters of one type from {symbol}. Describe how this company writes "
        "this type of letter, so a new one can match it.\n"
        "- tone: one sentence.\n"
        "- phrases: 4 to 10 standing phrases the company actually repeats, quoted exactly as written "
        "(fix only broken spacing from PDF extraction). No facts, names or dates.\n"
        "- structure: 3 to 6 short steps describing the order of the body.\n\n" + body
    )
    res = draft_ai.generate(api_key, prompt, schema=TYPE_SCHEMA, max_output_tokens=3000, deadline_s=60,
                            call_timeout_s=50, fast=True, purpose="learn-type")
    data = res["data"] or {}
    summary = {"tone": str(data.get("tone") or "")[:300],
               "phrases": [str(p)[:200] for p in (data.get("phrases") or [])][:10],
               "structure": [str(p)[:200] for p in (data.get("structure") or [])][:6],
               "letters": len(texts)}
    draft_memory.save_type_summary(owner_id, symbol, filing_type, summary)
    return summary
