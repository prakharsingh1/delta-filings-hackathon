"""Check with AI: the last look before a filing leaves the desk.

Two layers, so the check still works when Gemini is down or out of quota:

  rules   deterministic and explainable. Placeholders left in; the company's
          name, CIN, ISIN, scrip code and registered office against what its
          own filings (or the team) say; impossible dates and wrong weekdays;
          the regulation cited for the filing type; DIN, PAN and membership
          formats; common misspellings in compliance letters; annexures
          referred to but missing; the sign-off typed twice.
  AI      reads the letter against the verified requirement list for the
          filing type (does it answer what SEBI asks?), checks spelling,
          grammar and internal consistency, and compares facts with the brief.
          Every AI finding must quote text that is really in the draft, or it
          is discarded; a requirement marked "met" must quote its evidence.

A finding can carry a fix: exact text to find and replace in one place.
"""
import calendar
import difflib
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import draft_memory
import draft_requirements
from draft_ai import AIError

_SEV_ORDER = {"blocker": 0, "warning": 1, "suggestion": 2}

_PLACEHOLDER_RX = re.compile(
    r"\[\s*PLEASE\s*FILL[^\]]*\]|\{[a-z][a-z0-9_]{2,60}\}|\[\s*(?:insert|enter|add)\b[^\]]*\]|"
    r"\[[A-Z][A-Z /\-]{3,}\]|_{5,}|\bX{3,}\b|\bTBD\b|\[\s*\]", re.I)

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_MON = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"
_WEEKDAYS = [d.lower() for d in calendar.day_name]
_DATE_RXS = [
    re.compile(rf"(?:(?P<wd>monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+)?(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?(?P<m>{_MON}),?\s+(?P<y>(?:19|20)\d\d)", re.I),
    re.compile(rf"(?:(?P<wd>monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+)?(?P<m>{_MON})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>(?:19|20)\d\d)", re.I),
    re.compile(r"\b(?P<d>\d{1,2})[./-](?P<mn>\d{1,2})[./-](?P<y>(?:19|20)\d\d)\b"),
]

MISSPELLINGS = {
    "recieve": "receive", "recieved": "received", "seperate": "separate", "occured": "occurred",
    "occurence": "occurrence", "occurance": "occurrence", "accomodate": "accommodate",
    "aggrement": "agreement", "agreeement": "agreement", "agrement": "agreement", "dividened": "dividend",
    "divident": "dividend", "regulaton": "regulation", "regualtion": "regulation", "intimaton": "intimation",
    "intimtion": "intimation", "commited": "committed", "independant": "independent",
    "managment": "management", "comittee": "committee", "commitee": "committee", "committe": "committee",
    "reciept": "receipt", "refered": "referred", "tranfer": "transfer", "transfered": "transferred",
    "excercise": "exercise", "acquistion": "acquisition", "aquisition": "acquisition",
    "subsidary": "subsidiary", "subsidiery": "subsidiary", "disclousre": "disclosure", "disclosre": "disclosure",
    "complaince": "compliance", "compliace": "compliance", "secretery": "secretary", "secratary": "secretary",
    "alloted": "allotted", "allotement": "allotment", "beneficary": "beneficiary", "approvel": "approval",
    "regestered": "registered", "registerd": "registered", "adress": "address", "auditer": "auditor",
    "chiarman": "chairman", "chairmen": "chairman", "directer": "director", "appoinment": "appointment",
    "appointmnet": "appointment", "resignaton": "resignation", "intrest": "interest", "finacial": "financial",
    "financal": "financial", "statment": "statement", "accross": "across", "untill": "until",
    "thier": "their", "limted": "limited", "limitted": "limited", "exchnage": "exchange", "excahnge": "exchange",
    "pursant": "pursuant", "persuant": "pursuant", "herby": "hereby", "enclsoed": "enclosed",
    "enclose": None, "requirments": "requirements", "requirment": "requirement", "obligaton": "obligation",
    "sharholders": "shareholders", "shareholdres": "shareholders", "shareholdig": "shareholding",
    "remuneraton": "remuneration", "renumeration": "remuneration", "convertable": "convertible",
    "debenure": "debenture", "debentrue": "debenture", "preferencial": "preferential", "qualifed": "qualified",
    "institutonal": "institutional", "scrutinzer": "scrutinizer", "scruitinizer": "scrutinizer",
    "procceedings": "proceedings", "proceedigs": "proceedings", "resolutons": "resolutions",
    "unaudted": "unaudited", "unaudtied": "unaudited", "consolidted": "consolidated", "standlone": "standalone",
    "acknowledgment": None, "judgement": None,
}


def _loc_texts(p: Dict[str, Any]) -> List[Tuple[str, str]]:
    out = [("subject", p.get("subject") or ""), ("body", p.get("body") or "")]
    for i, a in enumerate(p.get("annexures") or []):
        out.append((f"annexure:{i}", f"{a.get('title') or ''}\n{a.get('body') or ''}"))
    return out


def _where_label(loc: str, p: Dict[str, Any]) -> str:
    if loc.startswith("annexure:"):
        i = int(loc.split(":")[1])
        title = ((p.get("annexures") or [{}])[i] or {}).get("title") or f"Annexure {i + 1}"
        return title[:60]
    return {"subject": "Subject", "body": "Letter body", "details": "Letter details"}.get(loc, loc)


class _Findings:
    def __init__(self, payload):
        self.items: List[Dict[str, Any]] = []
        self.payload = payload

    def add(self, category, severity, title, detail, where="body", quote="", fix=None, source="rule"):
        key = (category, title, quote)
        if any((f["category"], f["title"], f["quote"]) == key for f in self.items):
            return
        self.items.append({
            "id": f"f{len(self.items) + 1}", "category": category, "severity": severity, "title": title,
            "detail": detail, "where": where, "where_label": _where_label(where, self.payload),
            "quote": quote, "fix": fix, "source": source,
        })


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _parse_date(m) -> Tuple[Optional[date], Optional[str]]:
    gd = m.groupdict()
    try:
        y, d = int(gd["y"]), int(gd["d"])
        mo = int(gd["mn"]) if gd.get("mn") else _MONTHS[gd["m"].lower().rstrip(".")]
    except (KeyError, ValueError, TypeError):
        return None, None
    try:
        return date(y, mo, d), None
    except ValueError:
        return None, f"{d} {calendar.month_name[mo] if 1 <= mo <= 12 else mo} {y}"


def _pins(s: str) -> List[str]:
    return [re.sub(r"\s", "", p) for p in re.findall(r"\b\d{3}\s?\d{3}\b", s or "")]


def run_rules(p: Dict[str, Any], facts: Dict[str, str], house: Dict[str, Any]) -> _Findings:
    F = _Findings(p)
    filing_type = p.get("filing_type") or ""
    texts = _loc_texts(p)
    letter_text = "\n".join(t for _, t in texts)

    # 1. Placeholders
    for loc, t in texts:
        hits = _PLACEHOLDER_RX.findall(t)
        if hits:
            uniq = list(dict.fromkeys(hits))
            F.add("placeholder", "blocker", f"{len(hits)} placeholder{'s' if len(hits) > 1 else ''} not filled",
                  "Fill or remove every placeholder before filing: " + ", ".join(uniq[:3]) + ("…" if len(uniq) > 3 else ""),
                  where=loc, quote=uniq[0])

    # 2. Company particulars against the company's own records
    def compare(field, label, given, expected, norm=lambda s: re.sub(r"\s", "", s or "").upper(), sev="blocker"):
        if not given:
            return
        if expected and norm(given) != norm(expected):
            F.add("accuracy", sev, f"{label} does not match the company's records",
                  f"This letter has “{given}”; {facts_src.get(field, 'the company profile')} has “{expected}”.",
                  where="details", quote=given, fix={"field": field, "find": given, "replace": expected})

    facts_src = p.get("_fact_sources") or {}
    legal = facts.get("legal_name", "")
    company = (p.get("company_name") or "").strip()
    if company and legal and _norm(company) != _norm(legal):
        sev = "warning" if difflib.SequenceMatcher(None, _norm(company), _norm(legal)).ratio() > 0.6 else "blocker"
        F.add("accuracy", sev, "Company name differs from its legal name",
              f"The letterhead says “{company}”; the company signs its own filings as “{legal}”.",
              where="details", quote=company, fix={"field": "company_name", "find": company, "replace": legal})
    cin = (p.get("cin") or "").strip()
    if cin and not draft_memory.cin_valid(cin):
        F.add("accuracy", "blocker", "CIN is not in a valid format",
              "A CIN is 21 characters: L or U, 5 digits, a 2-letter state code, a 4-digit year, 3 letters, "
              "6 digits.", where="details", quote=cin,
              fix={"field": "cin", "find": cin, "replace": facts["cin"]} if facts.get("cin") else None)
    else:
        compare("cin", "CIN", cin, facts.get("cin"))
    compare("bse_code", "BSE scrip code", (p.get("bse_code") or "").strip(), facts.get("bse_code"))
    compare("nse_symbol", "NSE symbol", (p.get("nse_symbol") or "").strip(), facts.get("nse_symbol"))
    office, exp_office = (p.get("registered_office") or "").strip(), facts.get("registered_office", "")
    if office and exp_office:
        po, pe = _pins(office), _pins(exp_office)
        a, b = set(re.findall(r"[a-z0-9]+", office.lower())), set(re.findall(r"[a-z0-9]+", exp_office.lower()))
        overlap = len(a & b) / max(len(a | b), 1)
        if po and pe and po[0] != pe[0]:
            F.add("accuracy", "blocker", "Registered office PIN code differs",
                  f"PIN {po[0]} on this letter, {pe[0]} in the company's records (“{exp_office}”).",
                  where="details", quote=office,
                  fix={"field": "registered_office", "find": office, "replace": exp_office})
        elif overlap < 0.55:
            F.add("accuracy", "warning", "Registered office differs from the company's records",
                  f"The company's filings give “{exp_office}”.", where="details", quote=office,
                  fix={"field": "registered_office", "find": office, "replace": exp_office})
    for cand in re.findall(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b", letter_text):
        if facts.get("cin") and cand != facts["cin"] and (not legal or legal.lower()[:12] in letter_text.lower()):
            loc = next((l for l, t in texts if cand in t), "body")
            F.add("accuracy", "warning", "A CIN in the text differs from the company's",
                  f"“{cand}” appears in the text; the company's CIN is {facts['cin']}. Ignore if it belongs to "
                  "another entity.", where=loc, quote=cand, fix={"field": loc, "find": cand, "replace": facts["cin"]})
    for cand in re.findall(r"\bIN[A-Z0-9]{9}\d\b", letter_text):
        if not draft_memory.isin_valid(cand):
            loc = next((l for l, t in texts if cand in t), "body")
            F.add("accuracy", "blocker", "ISIN fails its check digit", f"“{cand}” is not a valid ISIN.",
                  where=loc, quote=cand,
                  fix={"field": loc, "find": cand, "replace": facts["isin"]} if facts.get("isin") else None)

    # Signatory
    signer = (p.get("signer_name") or "").strip()
    if not signer and p.get("doc_kind", "letter") in ("", "letter"):
        F.add("format", "warning", "No signatory named",
              "Add the name of the person signing" +
              (f" (the company's letters are usually signed by {facts['cs_name']})." if facts.get("cs_name") else "."),
              where="details", fix={"field": "signer_name", "find": "", "replace": facts["cs_name"]} if facts.get("cs_name") else None)
    elif signer and facts.get("cs_name") and _norm(signer) != _norm(facts["cs_name"]):
        ratio = difflib.SequenceMatcher(None, _norm(signer), _norm(facts["cs_name"])).ratio()
        if ratio > 0.75:
            F.add("spelling", "warning", "Signatory's name may be misspelt",
                  f"“{signer}” is close to “{facts['cs_name']}”, who signs the company's letters.",
                  where="details", quote=signer, fix={"field": "signer_name", "find": signer, "replace": facts["cs_name"]})

    # 3. Dates
    house_style = house.get("date_format")
    off_style = []
    for loc, t in texts:
        for rx in _DATE_RXS:
            for m in rx.finditer(t):
                d, bad = _parse_date(m)
                if bad:
                    F.add("accuracy", "blocker", "Date does not exist", f"“{m.group(0)}”: {bad} is not a calendar date.",
                          where=loc, quote=m.group(0))
                    continue
                if not d:
                    continue
                wd = (m.groupdict().get("wd") or "").lower()
                if wd and _WEEKDAYS[d.weekday()] != wd:
                    right = m.group(0).replace(m.group("wd"), calendar.day_name[d.weekday()], 1)
                    F.add("accuracy", "blocker", "Weekday does not match the date",
                          f"{d.strftime('%d %B %Y')} is a {calendar.day_name[d.weekday()]}, not a {wd.title()}.",
                          where=loc, quote=m.group(0), fix={"field": loc, "find": m.group(0), "replace": right})
                style = draft_memory.classify_date(m.group(0))
                if house_style and style and style != house_style and not wd:
                    off_style.append((loc, m.group(0), draft_memory.format_date(d, house_style)))
    if off_style:
        loc, found, want = off_style[0]
        F.add("format", "suggestion", "Dates differ from the company's house style",
              f"{len(off_style)} date(s) are written differently from the company's letters, e.g. “{found}” "
              f"(house style: “{want}”).", where=loc, quote=found, fix={"field": loc, "find": found, "replace": want})

    # 4. Identifier formats
    for loc, t in texts:
        for m in re.finditer(r"\bDIN\s*(?:No\.?|:|-)?\s*(\d{5,10})\b", t):
            if len(m.group(1)) != 8:
                F.add("accuracy", "blocker", "DIN should have 8 digits", f"“{m.group(0)}” has {len(m.group(1))} digits.",
                      where=loc, quote=m.group(0))
        for m in re.finditer(r"\bPAN\s*(?:No\.?|:|-)?\s*([A-Z0-9]{8,12})\b", t):
            if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", m.group(1)):
                F.add("accuracy", "warning", "PAN is not in the usual format", f"“{m.group(1)}” (expected AAAAA9999A).",
                      where=loc, quote=m.group(0))
        for m in re.finditer(r"(\d{3,}(?:\.\d+)?)\s?%", t):
            if float(m.group(1)) > 100:
                F.add("accuracy", "warning", "Percentage above 100", f"“{m.group(0)}”.", where=loc, quote=m.group(0))

    # 5. Regulation cited
    expected = draft_requirements.CITES.get(filing_type)
    body = p.get("body") or ""
    if expected and body.strip():
        cited = set(re.findall(r"Regulations?\s+(\d+[A-Z]?)", (p.get("subject") or "") + " " + body))
        if not cited & set(expected):
            F.add("requirement", "warning", f"Regulation {expected[0]} is not cited",
                  f"This filing is made under Regulation {' / '.join(expected)} of the SEBI (LODR) Regulations, "
                  "2015; the body does not cite it.", where="body")
    for loc, t in texts:
        for m in re.finditer(r"Listing\s+Obligations?\s+(?:and|&)\s+Disclosures?\s+Requirements?", t, re.I):
            if m.group(0) != "Listing Obligations and Disclosure Requirements" and re.sub(r"\s+", " ", m.group(0)) != "Listing Obligations and Disclosure Requirements":
                F.add("spelling", "warning", "Name of the regulations is misspelt",
                      "The title is “Listing Obligations and Disclosure Requirements”.", where=loc,
                      quote=m.group(0), fix={"field": loc, "find": m.group(0),
                                             "replace": "Listing Obligations and Disclosure Requirements"})

    # 6. Spelling and typing slips
    for loc, t in texts:
        for m in re.finditer(r"\b[A-Za-z]{4,}\b", t):
            w = m.group(0)
            right = MISSPELLINGS.get(w.lower())
            if right:
                right = right.capitalize() if w[0].isupper() else right
                F.add("spelling", "warning", f"Spelling: “{w}”", f"Should be “{right}”.", where=loc, quote=w,
                      fix={"field": loc, "find": w, "replace": right})
        for m in re.finditer(r"\b(\w+)\s+\1\b", t, re.I):
            if m.group(1).lower() not in ("that", "had", "is") and not m.group(1).isdigit():
                F.add("spelling", "suggestion", "Repeated word", f"“{m.group(0)}”.", where=loc, quote=m.group(0),
                      fix={"field": loc, "find": m.group(0), "replace": m.group(1)})
        if legal:
            for tok in [w for w in re.findall(r"[A-Za-z]{5,}", legal) if w.lower() not in ("limited", "india", "industries")]:
                for w in set(re.findall(r"\b[A-Z][a-z]{4,}\b", t)):
                    r = difflib.SequenceMatcher(None, w.lower(), tok.lower()).ratio()
                    if 0.8 <= r < 1 and w.lower() not in MISSPELLINGS and abs(len(w) - len(tok)) <= 2:
                        F.add("spelling", "warning", f"Company name may be misspelt: “{w}”",
                              f"The company's name is spelt “{tok}”.", where=loc, quote=w,
                              fix={"field": loc, "find": w, "replace": tok})

    # 7. Annexures referred to vs attached
    attached = len([a for a in (p.get("annexures") or []) if (a.get("body") or "").strip()])
    roman = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
    refs = {int(r) if r.isdigit() else roman.get(r.upper(), 0)
            for r in re.findall(r"Annexure[\s\-–]*([IVX]+|\d+)\b", body, re.I)}
    if refs and max(refs) > attached:
        F.add("consistency", "blocker", "Body refers to an annexure that is not attached",
              f"The body mentions Annexure {max(refs)} but {attached} annexure(s) are attached.", where="body")
    if attached and not refs and not re.search(r"enclos|attach|annex", body, re.I):
        F.add("consistency", "suggestion", "Annexure is not referred to in the body",
              "Say in the body that the annexure is enclosed, e.g. “enclosed as Annexure I”.", where="body")

    # 8. Shell typed twice, and the house closing line
    if re.search(r"^\s*(dear\s+sir|dear\s+madam)", body, re.I | re.M):
        m = re.search(r"^\s*(dear\s+sir[^\n]*|dear\s+madam[^\n]*)", body, re.I | re.M)
        F.add("format", "warning", "Salutation typed in the body",
              "The template already prints the salutation, so the letter would show it twice.", where="body",
              quote=m.group(1).strip(), fix={"field": "body", "find": m.group(1).strip(), "replace": ""})
    m = re.search(r"^\s*(yours\s+(faithfully|truly|sincerely),?)\s*$", body, re.I | re.M)
    if m:
        F.add("format", "warning", "Sign-off typed in the body",
              "The template already prints the sign-off, so the letter would show it twice.", where="body",
              quote=m.group(1), fix={"field": "body", "find": m.group(1), "replace": ""})
    req_line = house.get("request_line")
    if req_line and body.strip() and p.get("doc_kind", "letter") in ("", "letter") and \
            _norm(req_line).rstrip(".") not in _norm(body):
        tail = body.strip().split("\n")[-1].strip()
        F.add("format", "suggestion", "Closing line differs from the company's usual",
              f"The company's letters end the body with “{req_line}”.", where="body", quote=tail[:160],
              fix={"field": "body", "find": tail, "replace": tail + "\n\n" + req_line} if tail else None)
    return F


# ── Requirements ─────────────────────────────────────────

def _rule_coverage(reqs: Dict[str, Any], text: str) -> List[Dict[str, Any]]:
    out = []
    for r in reqs.get("items", []):
        status = "unchecked"
        evidence = ""
        if r.get("hint"):
            m = re.search(r["hint"], text, re.I)
            if m:
                status = "likely"
                s = max(0, m.start() - 60)
                evidence = " ".join(text[s:m.end() + 80].split())
            else:
                status = "missing" if not r.get("applies_when") else "unclear"
        out.append({"id": r["id"], "label": r["label"], "requirement": r["requirement"],
                    "source_ref": r["source_ref"], "applies_when": r.get("applies_when"),
                    "verified": r.get("verified", True), "status": status, "evidence": evidence, "note": ""})
    return out


CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "status": {"type": "string", "enum": ["met", "partial", "missing", "not_applicable"]},
                "evidence": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["id", "status", "evidence", "note"]}},
        "findings": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["accuracy", "spelling", "grammar", "consistency",
                                                        "requirement", "clarity"]},
                "severity": {"type": "string", "enum": ["blocker", "warning", "suggestion"]},
                "quote": {"type": "string"},
                "issue": {"type": "string"},
                "replacement": {"type": "string"},
            },
            "required": ["category", "severity", "quote", "issue", "replacement"]}},
        "summary": {"type": "string"},
    },
    "required": ["requirements", "findings", "summary"],
}


def _ai_check(api_key, p, facts, reqs, rule_findings) -> Dict[str, Any]:
    import draft_ai
    from draft_assist import SYSTEM
    texts = _loc_texts(p)
    doc = "\n\n".join(f"=== {_where_label(l, p).upper()} ===\n{t}" for l, t in texts if t.strip())
    details = "\n".join(f"{k}: {p.get(k)}" for k in ("company_name", "registered_office", "cin", "bse_code",
                                                      "nse_symbol", "signer_name", "signer_designation") if p.get(k))
    fact_text = "\n".join(f"{draft_memory.FACT_LABELS.get(k, k)}: {v}" for k, v in facts.items())
    req_text = "\n".join(f"- id {r['id']}: {r['label']} — {r['requirement']}" +
                         (f" [only when: {r['applies_when']}]" if r.get("applies_when") else "")
                         for r in reqs.get("items", []) if r.get("kind") != "timing")
    already = "\n".join(f"- {f['title']}" for f in rule_findings) or "(none)"
    prompt = (
        f"Review this draft before it is filed. Filing type: {p.get('filing_type')}. "
        f"Today is {date.today().strftime('%d %B %Y')}.\n\n"
        "1. For each REQUIREMENT, decide whether the draft answers it: met, partial, missing, or not_applicable "
        "(use not_applicable only when its condition clearly does not apply to this event). For met or partial, "
        "`evidence` must be an exact quote from the draft. `note` says what is missing, in a few words.\n"
        "2. List problems a careful Company Secretary would fix: misspellings, grammar, names or numbers "
        "inconsistent within the draft or with the BRIEF or the COMPANY FACTS, wrong or impossible dates, "
        "unclear sentences. `quote` must be copied exactly from the draft (a few words, enough to locate it). "
        "`replacement` is the corrected text for that quote, or empty if a person must decide. Do not flag "
        "style preferences, and do not repeat the issues already found below. Do not claim a fact is wrong "
        "unless the brief or the company facts contradict it.\n"
        "3. summary: one or two sentences.\n\n"
        f"REQUIREMENTS:\n{req_text or '(none)'}\n\n"
        f"ALREADY FOUND:\n{already}\n\n"
        f"COMPANY FACTS:\n{fact_text or '(none)'}\n\n"
        f"LETTER DETAILS:\n{details or '(none)'}\n\n"
        f"BRIEF:\n{(p.get('new_info') or '(none)').strip()}\n\n"
        f"DRAFT:\n{doc[:20000]}"
    )
    res = draft_ai.generate(api_key, prompt, schema=CHECK_SCHEMA, system=SYSTEM, max_output_tokens=8000,
                            deadline_s=90, call_timeout_s=75, purpose="check")
    return {"data": res["data"] or {}, "model": res["model"]}


def _locate(quote: str, p: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Find a quote in the draft, tolerating whitespace differences. Returns
    (location, exact text as it appears)."""
    q = " ".join((quote or "").split())
    if len(q) < 3:
        return None
    for loc, t in _loc_texts(p):
        if q in t:
            return loc, q
        rx = re.compile(r"\s+".join(re.escape(w) for w in q.split()), re.I)
        m = rx.search(t)
        if m:
            return loc, m.group(0)
    return None


def run_check(payload: Dict[str, Any], owner_id: str, api_key: Optional[str] = None,
              use_ai: bool = True) -> Dict[str, Any]:
    symbol = (payload.get("symbol") or payload.get("nse_symbol") or "").upper()
    facts_full = draft_memory.facts_for(owner_id, symbol) if symbol else {}
    facts = draft_memory.fact_values(facts_full)
    payload = dict(payload)
    payload["_fact_sources"] = {k: (v.get("detail") or "the company profile") for k, v in facts_full.items()}
    try:
        from draft_engine import get_filing_type
        payload.setdefault("doc_kind", (get_filing_type(payload.get("filing_type", "")) or {}).get("doc_kind") or "letter")
    except Exception:  # noqa: BLE001
        payload.setdefault("doc_kind", "letter")
    house = draft_memory.house_format(owner_id, symbol) if symbol else {}
    reqs = draft_requirements.for_type(payload.get("filing_type") or "")

    F = run_rules(payload, facts, house)
    full_text = "\n".join(t for _, t in _loc_texts(payload))
    requirements = _rule_coverage(reqs, full_text)

    ai_used, ai_error, model, summary = False, None, None, ""
    if use_ai and api_key and full_text.strip():
        try:
            out = _ai_check(api_key, payload, facts, reqs, F.items)
            ai_used, model = True, out["model"]
            data = out["data"]
            summary = str(data.get("summary") or "")[:400]
            by_id = {r["id"]: r for r in requirements}
            for r in data.get("requirements") or []:
                cur = by_id.get(str(r.get("id")))
                if not cur:
                    continue
                status = r.get("status")
                ev = str(r.get("evidence") or "")
                if status in ("met", "partial") and not _locate(ev, payload):
                    status = "unclear"            # claimed but not quotable
                cur["status"] = status or cur["status"]
                cur["evidence"] = " ".join(ev.split())[:240] if status in ("met", "partial") else ""
                cur["note"] = str(r.get("note") or "")[:200]
            for f in data.get("findings") or []:
                hit = _locate(str(f.get("quote") or ""), payload)
                if not hit:
                    continue                      # cannot point at it: drop
                loc, exact = hit
                repl = str(f.get("replacement") or "")
                fix = {"field": loc, "find": exact, "replace": repl} if repl and repl != exact else None
                F.add(f.get("category") or "clarity",
                      f.get("severity") if f.get("severity") in _SEV_ORDER else "suggestion",
                      (str(f.get("issue") or "Issue"))[:90], str(f.get("issue") or "")[:300],
                      where=loc, quote=exact, fix=fix, source="ai")
        except AIError as e:
            ai_error = e.to_dict()

    for r in requirements:
        if r["status"] in ("missing", "partial") and r.get("verified", True):
            sev = "blocker" if r["status"] == "missing" and not r.get("applies_when") and ai_used else "warning"
            F.add("requirement", sev, f"Requirement {'missing' if r['status'] == 'missing' else 'partly covered'}: "
                  f"{r['label']}", f"{r['requirement']} ({r['source_ref']})" + (f" — {r['note']}" if r.get("note") else ""),
                  where="body")

    findings = sorted(F.items, key=lambda f: (_SEV_ORDER.get(f["severity"], 3), f["where"] != "details"))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in _SEV_ORDER}
    score = max(0, 100 - 22 * counts["blocker"] - 7 * counts["warning"] - 2 * counts["suggestion"])
    fact_rows = []
    for key, field in (("legal_name", "company_name"), ("cin", "cin"), ("registered_office", "registered_office"),
                       ("bse_code", "bse_code"), ("nse_symbol", "nse_symbol"), ("cs_name", "signer_name")):
        given, exp = (payload.get(field) or "").strip(), facts.get(key, "")
        if not given and not exp:
            continue
        status = ("unverified" if not exp else "missing" if not given else
                  "match" if re.sub(r"\W", "", given.lower()) == re.sub(r"\W", "", exp.lower()) else "differs")
        fact_rows.append({"label": draft_memory.FACT_LABELS[key], "field": field, "in_draft": given,
                          "expected": exp, "status": status,
                          "source": (facts_full.get(key) or {}).get("detail", "")})
    return {
        "summary": {"blockers": counts["blocker"], "warnings": counts["warning"],
                    "suggestions": counts["suggestion"], "score": score, "ready": counts["blocker"] == 0,
                    "ai_used": ai_used, "ai_error": ai_error, "model": model, "text": summary,
                    "checked_at": datetime.now().isoformat(timespec="seconds")},
        "findings": findings,
        "requirements": requirements,
        "requirement_sources": reqs.get("sources", []),
        "requirements_derived": reqs.get("derived", False),
        "notes": reqs.get("notes", []),
        "facts": fact_rows,
    }
