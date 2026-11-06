"""Draft memory: what Delta knows about how a team and a company write.

Four things are remembered per account (the memory owner), so a Company
Secretary can teach the desk once and every later draft follows:

  instructions  free-text rules, team-wide ("" symbol) or for one company
  documents     reference letters the team uploads or pastes, past exchange
                filings they pin, and every draft they export (approved
                examples, the strongest signal of how they really write)
  facts         company particulars with where each came from: CIN, ISIN,
                scrip code, registered office, the CS and membership number
  house format  learned from the company's own letters: date format, how the
                Listing Regulations are cited, the closing request line, the
                salutation, sign-off and signature block, the exchange
                addressee blocks and the stock-code block

The house format is learned without AI. Exchange PDFs extract with broken
spacing ("faithfull y", "202 6"), so each recurring element is collected from
many letters, grouped by its letters-only form, and the cleanest instance of
the most common group wins. That makes the result stable and explainable:
"used in 11 of 12 letters".

Collaborators on a shared draft work from the draft owner's memory.
"""
import json
import re
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from database import get_connection

MAX_DOC_CHARS = 20000
MAX_INSTRUCTION_CHARS = 4000

FACT_FIELDS = [
    ("legal_name", "Company name"),
    ("cin", "CIN"),
    ("registered_office", "Registered office"),
    ("isin", "ISIN"),
    ("bse_code", "BSE scrip code"),
    ("nse_symbol", "NSE symbol"),
    ("phone", "Telephone"),
    ("email", "Investor / compliance email"),
    ("website", "Website"),
    ("cs_name", "Company Secretary"),
    ("cs_designation", "Signatory designation"),
    ("cs_membership", "Membership no."),
]
FACT_LABELS = dict(FACT_FIELDS)
_SOURCE_RANK = {"you": 4, "export": 3, "workspace": 2, "filings": 1}


# ── Storage ──────────────────────────────────────────────

def init_memory_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS draft_memory (
            owner_id TEXT NOT NULL,
            symbol TEXT NOT NULL DEFAULT '',
            instructions TEXT NOT NULL DEFAULT '',
            facts_json TEXT NOT NULL DEFAULT '{}',
            style_json TEXT NOT NULL DEFAULT '{}',
            learned_at TEXT,
            updated_at TEXT,
            updated_by TEXT DEFAULT '',
            PRIMARY KEY (owner_id, symbol)
        );
        CREATE TABLE IF NOT EXISTS draft_memory_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id TEXT NOT NULL,
            symbol TEXT NOT NULL DEFAULT '',
            filing_type TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            filename TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL,
            ref TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_draft_memory_docs_owner
            ON draft_memory_docs(owner_id, symbol);
    """)
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row(owner_id: str, symbol: str) -> Dict[str, Any]:
    init_memory_db()
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM draft_memory WHERE owner_id=? AND symbol=?",
                         (owner_id, symbol)).fetchone()
    finally:
        conn.close()
    if not r:
        return {"instructions": "", "facts": {}, "style": {}, "learned_at": None, "updated_at": None}
    d = dict(r)
    return {
        "instructions": d.get("instructions") or "",
        "facts": _loads(d.get("facts_json"), {}),
        "style": _loads(d.get("style_json"), {}),
        "learned_at": d.get("learned_at"),
        "updated_at": d.get("updated_at"),
    }


def _loads(s, default):
    try:
        v = json.loads(s or "")
        return v if isinstance(v, type(default)) else default
    except Exception:  # noqa: BLE001
        return default


def _upsert(owner_id: str, symbol: str, by: str = "", **cols):
    init_memory_db()
    current = _row(owner_id, symbol)
    vals = {
        "instructions": current["instructions"],
        "facts_json": json.dumps(current["facts"]),
        "style_json": json.dumps(current["style"]),
        "learned_at": current["learned_at"],
    }
    vals.update(cols)
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO draft_memory (owner_id, symbol, instructions, facts_json, style_json,
                                         learned_at, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(owner_id, symbol) DO UPDATE SET
                   instructions=excluded.instructions, facts_json=excluded.facts_json,
                   style_json=excluded.style_json, learned_at=excluded.learned_at,
                   updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
            (owner_id, symbol, vals["instructions"], vals["facts_json"], vals["style_json"],
             vals["learned_at"], _now(), by or ""))
        conn.commit()
    finally:
        conn.close()


def memory_owner(user_id: str, draft_id: Optional[int] = None) -> str:
    """Whose memory a request works from: the draft owner's when the user is
    a collaborator on that draft, otherwise the user's own."""
    if draft_id:
        try:
            from drafts_store import get_draft
            d = get_draft(int(draft_id), user_id)
            if d and d.get("user_id"):
                return d["user_id"]
        except Exception:  # noqa: BLE001
            pass
    return user_id


def set_instructions(owner_id: str, symbol: str, text: str, by: str = "") -> str:
    text = (text or "").strip()[:MAX_INSTRUCTION_CHARS]
    _upsert(owner_id, (symbol or "").upper(), by=by, instructions=text)
    return text


def add_instruction(owner_id: str, symbol: str, line: str, by: str = "") -> str:
    """Append one rule ("Remember this") without disturbing the others."""
    line = " ".join((line or "").split())[:500]
    if not line:
        return _row(owner_id, (symbol or "").upper())["instructions"]
    cur = _row(owner_id, (symbol or "").upper())["instructions"].rstrip()
    bullet = line if line.startswith(("-", "•")) else f"- {line}"
    return set_instructions(owner_id, symbol, (cur + "\n" + bullet).strip(), by=by)


# ── Facts ────────────────────────────────────────────────

def merge_facts(existing: Dict[str, Dict], new: Dict[str, Dict]) -> Dict[str, Dict]:
    """Higher-ranked sources win; a user-confirmed value is never replaced
    by anything learned."""
    out = dict(existing or {})
    for key, f in (new or {}).items():
        if key not in FACT_LABELS or not (f or {}).get("value"):
            continue
        old = out.get(key)
        if old and _SOURCE_RANK.get(old.get("source"), 0) > _SOURCE_RANK.get(f.get("source"), 0):
            continue
        out[key] = {**f, "updated_at": _now()}
    return out


def save_facts(owner_id: str, symbol: str, facts: Dict[str, str], by: str = "") -> Dict[str, Dict]:
    """Facts typed or confirmed by the user. Empty values clear the fact."""
    symbol = (symbol or "").upper()
    current = _row(owner_id, symbol)["facts"]
    for key, value in (facts or {}).items():
        if key not in FACT_LABELS:
            continue
        value = " ".join(str(value or "").split())[:400]
        if not value:
            current.pop(key, None)
            continue
        current[key] = {"value": value, "source": "you", "detail": "Saved by your team",
                        "updated_at": _now()}
    _upsert(owner_id, symbol, by=by, facts_json=json.dumps(current))
    return current


def record_export_facts(owner_id: str, symbol: str, meta: Dict[str, str]):
    """The particulars on a letter the user exported are a strong signal."""
    mapping = {"registered_office": "registered_office", "cin": "cin", "bse_code": "bse_code",
               "nse_symbol": "nse_symbol", "signer_name": "cs_name",
               "signer_designation": "cs_designation", "company_name": "legal_name"}
    new = {}
    for src, key in mapping.items():
        v = " ".join(str((meta or {}).get(src) or "").split())
        if v:
            new[key] = {"value": v, "source": "export", "detail": "From a letter you exported"}
    if not new:
        return
    symbol = (symbol or "").upper()
    current = _row(owner_id, symbol)["facts"]
    _upsert(owner_id, symbol, facts_json=json.dumps(merge_facts(current, new)))


def _workspace_facts(owner_id: str, symbol: str) -> Dict[str, Dict]:
    try:
        conn = get_connection()
        r = conn.execute("SELECT name, cin FROM entities WHERE user_id=? AND upper(symbol)=? "
                         "AND coalesce(archived,0)=0 LIMIT 1", (owner_id, symbol)).fetchone()
        conn.close()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    if r and r["cin"]:
        out["cin"] = {"value": r["cin"], "source": "workspace", "detail": "From your workspace entity"}
    if r and r["name"]:
        out["legal_name"] = {"value": r["name"], "source": "workspace", "detail": "From your workspace entity"}
    return out


def facts_for(owner_id: str, symbol: str) -> Dict[str, Dict]:
    symbol = (symbol or "").upper()
    facts = merge_facts(_workspace_facts(owner_id, symbol), {})
    facts = merge_facts(facts, {k: v for k, v in _row(owner_id, symbol)["facts"].items()})
    if symbol and "nse_symbol" not in facts:
        facts["nse_symbol"] = {"value": symbol, "source": "filings", "detail": "The symbol you are drafting for"}
    return facts


def fact_values(facts: Dict[str, Dict]) -> Dict[str, str]:
    return {k: (v or {}).get("value", "") for k, v in (facts or {}).items() if (v or {}).get("value")}


# ── Documents ────────────────────────────────────────────

def extract_upload_text(filename: str, data: bytes) -> str:
    """Text from an uploaded reference document: PDF, Word or plain text."""
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        import io
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                raise ValueError("That PDF is password-protected.")
        parts = []
        for page in reader.pages[:30]:
            parts.append(page.extract_text() or "")
            if sum(len(p) for p in parts) > MAX_DOC_CHARS:
                break
        text = "\n".join(parts)
        if len(text.strip()) < 40:
            raise ValueError("No text could be read from that PDF. It may be a scan; paste the text instead.")
        return text[:MAX_DOC_CHARS]
    if name.endswith(".docx"):
        import io
        import docx
        d = docx.Document(io.BytesIO(data))
        lines = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                lines.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(lines)[:MAX_DOC_CHARS]
    if name.endswith((".txt", ".md", ".text")) or not name:
        return data.decode("utf-8", errors="replace")[:MAX_DOC_CHARS]
    raise ValueError("Upload a PDF, a Word (.docx) file or a text file.")


def add_doc(owner_id: str, symbol: str, title: str, text: str, source: str,
            filing_type: str = "", filename: str = "", ref: str = "", by: str = "") -> int:
    text = (text or "").strip()
    if len(text) < 40:
        raise ValueError("That document is too short to learn from.")
    if source not in ("upload", "paste", "filing", "approved"):
        raise ValueError("Unknown document source.")
    init_memory_db()
    conn = get_connection()
    try:
        if ref and source in ("approved", "filing"):
            row = conn.execute("SELECT id FROM draft_memory_docs WHERE owner_id=? AND source=? AND ref=?",
                               (owner_id, source, ref)).fetchone()
            if row:
                conn.execute("UPDATE draft_memory_docs SET title=?, text=?, filing_type=?, active=1, "
                             "created_at=? WHERE id=?",
                             (title[:200], text[:MAX_DOC_CHARS], filing_type, _now(), row["id"]))
                conn.commit()
                return row["id"]
        cur = conn.execute(
            """INSERT INTO draft_memory_docs (owner_id, symbol, filing_type, source, title, filename,
                                              text, ref, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (owner_id, (symbol or "").upper(), filing_type or "", source, (title or "Reference")[:200],
             (filename or "")[:200], text[:MAX_DOC_CHARS], str(ref or ""), _now(), by or ""))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_docs(owner_id: str, symbol: str = "", include_team: bool = True,
              filing_type: str = "", with_text: bool = False) -> List[Dict]:
    init_memory_db()
    symbol = (symbol or "").upper()
    where, args = ["owner_id=?", "active=1"], [owner_id]
    if include_team:
        where.append("(symbol=? OR symbol='')")
    else:
        where.append("symbol=?")
    args.append(symbol)
    if filing_type:
        where.append("(filing_type=? OR filing_type='')")
        args.append(filing_type)
    conn = get_connection()
    try:
        rows = conn.execute(f"SELECT * FROM draft_memory_docs WHERE {' AND '.join(where)} "
                            "ORDER BY created_at DESC LIMIT 60", args).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["chars"] = len(d.get("text") or "")
        d["preview"] = " ".join((d.get("text") or "").split())[:220]
        if not with_text:
            d.pop("text", None)
        out.append(d)
    return out


def delete_doc(owner_id: str, doc_id: int) -> bool:
    init_memory_db()
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM draft_memory_docs WHERE id=? AND owner_id=?", (doc_id, owner_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def record_approved(owner_id: str, symbol: str, filing_type: str, subject: str, body: str,
                    annexures: Optional[List[Dict]] = None, draft_id: Optional[int] = None,
                    by: str = "") -> Optional[int]:
    """An exported letter becomes an example of how this team writes."""
    body = (body or "").strip()
    if len(body) < 80 or re.search(r"\[\s*PLEASE\s*FILL", body, re.I):
        return None
    text = (f"Subject: {subject}\n\n" if subject else "") + body
    for a in annexures or []:
        if a.get("title") and a.get("body"):
            text += f"\n\n{a['title']}\n{a['body']}"
    try:
        return add_doc(owner_id, symbol, subject or "Exported draft", text, "approved",
                       filing_type=filing_type, ref=f"draft:{draft_id}" if draft_id else "", by=by)
    except ValueError:
        return None


# ── Text clean-up for exchange PDFs ──────────────────────

_MONTHS = ("january february march april may june july august september october "
           "november december").split()
_MON_RE = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\r", "\n").replace(" ", " ").replace("\t", " ")
    text = re.sub(r"[  -​]+", " ", text)
    text = re.sub(r" +([,.;:)])", r"\1", text)
    text = re.sub(r"\( +", "(", text)
    # "202 6" -> "2026" after a month or a comma
    text = re.sub(rf"((?:{_MON_RE})\.?,?\s*)((?:19|20)\d)\s(\d)\b", r"\1\2\3", text, flags=re.I)
    return "\n".join(line.strip() for line in text.split("\n"))


def _vocab(texts: Iterable[str]) -> Counter:
    c = Counter()
    for t in texts:
        c.update(w.lower() for w in re.findall(r"[A-Za-z]+", t))
    return c


def _repair(line: str, vocab: Counter) -> str:
    """Join words the PDF split ("faithfull y", "cop ies") when the joined
    form is common in the same company's letters and a half is not."""
    toks = line.split(" ")
    out, i = [], 0
    while i < len(toks):
        a = toks[i]
        if i + 1 < len(toks):
            b = toks[i + 1]
            al, bl = re.sub(r"[^A-Za-z]", "", a).lower(), re.sub(r"[^A-Za-z]", "", b).lower()
            if (al and bl and re.search(r"[A-Za-z]$", a) and re.match(r"^[A-Za-z]", b)
                    and vocab.get(al + bl, 0) >= 2
                    and (vocab.get(al, 0) < 3 or vocab.get(bl, 0) < 3 or len(bl) <= 2)):
                toks[i + 1] = a + b
                i += 1
                continue
        out.append(a)
        i += 1
    return " ".join(out)


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _noise(s: str, vocab: Counter) -> int:
    words = re.findall(r"[A-Za-z]+", s)
    return sum(1 for w in words if vocab.get(w.lower(), 0) < 2) + len(re.findall(r" [,.;:]", s))


def _pick(instances: List[Tuple[str, float]], vocab: Counter, min_share: float = 0.0,
          total: int = 0) -> Optional[Dict[str, Any]]:
    """Group (text, weight) pairs by letters-only form; return the cleanest
    text of the heaviest group with its count."""
    groups: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    for text, w in instances:
        if text and _squash(text):
            groups[_squash(text)].append((text, w))
    if not groups:
        return None
    key, members = max(groups.items(), key=lambda kv: (sum(w for _, w in kv[1]), len(kv[1])))
    count = len(members)
    if total and min_share and count / total < min_share:
        return None
    best = min((t for t, _ in members), key=lambda t: (_noise(t, vocab), -len(t)))
    return {"value": best, "count": count, "of": total or count}


# ── Letter anatomy ───────────────────────────────────────

_SAL_RE = re.compile(r"^(dear\s+)?(sirs?|madam|sir\s*\(s\))(\s*(/|or|and)\s*(madam|sir))?\s*[,:;.]?$", re.I)
_SUBJ_RE = re.compile(r"^(sub(?:ject)?\.?\s*[:\-–—]+|sub\.?\s+(?=[A-Z])|re\s*[:\-])\s*(.*)$", re.I)
_THANKS_RE = re.compile(r"^(thank(?:ing)?\s+you|thanks)\b.{0,30}?[,.!]?$", re.I)
_CLOSE_RE = re.compile(r"\byours\s+(faithfully|truly|sincerely)\s*[,.]?", re.I)
_FOR_RE = re.compile(r"^for\s+(?:and\s+on\s+behalf\s+of\s+)?(.{3,90}?\b(?:limited|ltd\.?))\s*[,.]?$", re.I)
_MEMB_RE = re.compile(r"(membership\s*(?:no|number)\.?\s*[:\-]?\s*[AF]\s*-?\s*\d{3,6}|\b[AF]CS\s*[:\-]?\s*(?:no\.?\s*)?\d{3,6}|\bM\.?\s*No\.?\s*[:\-]?\s*[AF]\s*-?\s*\d{3,6})", re.I)
_DESIG_RE = re.compile(r"(secretary|compliance\s+officer|director|chief|officer|head|president|cfo|ceo|counsel|manager)", re.I)
_REQUEST_RE = re.compile(r"(take\s+(?:the\s+)?(?:same|above|this)?.{0,40}on\s+(?:your\s+)?records?|for\s+your\s+(?:information|records?)|kindly|request(?:ed)?\s+(?:you\s+)?to)", re.I)
_STOP_SIG = re.compile(r"(bse\s+limited|national\s+stock|encl|registered\s+office|regd\.?\s+office|\bcin\b|tel[.:]|www\.|e-?mail|cc\s*:)", re.I)

_DATE_PATTERNS = [
    ("ordinal_comma", re.compile(r"\b(\d{1,2})(st|nd|rd|th)\s+(" + "|".join(m.title() for m in _MONTHS) + r"),\s*((?:19|20)\d\d)\b"), "21st August, 2026"),
    ("ordinal", re.compile(r"\b(\d{1,2})(st|nd|rd|th)\s+(" + "|".join(m.title() for m in _MONTHS) + r")\s+((?:19|20)\d\d)\b"), "21st August 2026"),
    ("dmy_comma", re.compile(r"\b(\d{1,2})\s+(" + "|".join(m.title() for m in _MONTHS) + r"),\s*((?:19|20)\d\d)\b"), "21 August, 2026"),
    ("dmy", re.compile(r"\b(\d{1,2})\s+(" + "|".join(m.title() for m in _MONTHS) + r")\s+((?:19|20)\d\d)\b"), "21 August 2026"),
    ("mdy", re.compile(r"\b(" + "|".join(m.title() for m in _MONTHS) + r")\s+(\d{1,2}),\s*((?:19|20)\d\d)\b"), "August 21, 2026"),
    ("numeric_dot", re.compile(r"\b(\d{2})\.(\d{2})\.((?:19|20)\d\d)\b"), "21.08.2026"),
    ("numeric_slash", re.compile(r"\b(\d{2})/(\d{2})/((?:19|20)\d\d)\b"), "21/08/2026"),
    ("numeric_dash", re.compile(r"\b(\d{2})-(\d{2})-((?:19|20)\d\d)\b"), "21-08-2026"),
]


def format_date(d, style: str) -> str:
    """Render a date the way the company writes it."""
    day, month, year = d.day, d.strftime("%B"), d.year
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return {
        "ordinal_comma": f"{day}{suffix} {month}, {year}",
        "ordinal": f"{day}{suffix} {month} {year}",
        "dmy_comma": f"{day} {month}, {year}",
        "dmy": f"{day} {month} {year}",
        "mdy": f"{month} {day}, {year}",
        "numeric_dot": d.strftime("%d.%m.%Y"),
        "numeric_slash": d.strftime("%d/%m/%Y"),
        "numeric_dash": d.strftime("%d-%m-%Y"),
    }.get(style, f"{day:02d} {month}, {year}")


def classify_date(s: str) -> Optional[str]:
    for name, rx, _ in _DATE_PATTERNS:
        if rx.search(s or ""):
            return name
    return None


_ATTN_RE = re.compile(r"(department|dept\.?|^the\s+(general\s+)?manager|^the\s+secretary|^listing|^corporate)", re.I)
_CODE_RE = re.compile(r"(scrip|security|stock)\s*(code|id)|\bsymbol\b|\bISIN\b", re.I)


def _clean_code_line(line: str) -> str:
    """'BSE Scrip Code: 500096 Listing Department' -> 'BSE Scrip Code: 500096'."""
    line = re.sub(r"(\b(?:\d{6}|IN[A-Z0-9]{10})\b|:\s*[A-Z][A-Z0-9&\-]{1,19}\b)\s+[A-Z][a-z].*$", r"\1", line)
    return line.strip().rstrip(" /|,;-").strip()


def _exchange_block(lines: List[str], name_rx: str, pin_rx: str) -> Optional[List[str]]:
    for i, ln in enumerate(lines):
        if not re.search(name_rx, ln, re.I):
            continue
        block = []
        for j in range(max(0, i - 2), i):
            prev = lines[j]
            if (prev and len(prev) < 70 and _ATTN_RE.search(prev) and not _CODE_RE.search(prev)
                    and not re.search(r"company\s+secretary|compliance\s+officer", prev, re.I)):
                block.append(prev.rstrip(","))
        start = re.search(name_rx, ln, re.I).start()
        seg = [ln[start:]]
        for k in range(i + 1, min(i + 9, len(lines))):
            seg.append(lines[k])
            if re.search(pin_rx, lines[k]):
                break
        for s in seg:
            m = re.search(pin_rx, s)
            if m:
                block.append(s[:m.end()].strip().rstrip(","))
                block = [b for b in block if b and not _CODE_RE.search(b)]
                return block if len(block) >= 3 else None
            if s:
                block.append(s.rstrip(","))
        return None
    return None


def analyze_letter(text: str, vocab: Counter) -> Dict[str, Any]:
    """Pull the recurring elements out of one exchange letter."""
    lines = [_repair(ln, vocab) for ln in _normalize(text).split("\n")]
    lines = [ln for ln in lines if ln.strip()]
    out: Dict[str, Any] = {}
    head = lines[:25]

    for ln in head:
        for name, rx, _ in _DATE_PATTERNS:
            if rx.search(ln):
                out["date_format"] = name
                out["date_label"] = "Date: " if re.match(r"^dated?\s*[:\-.]", ln, re.I) else "none"
                break
        if "date_format" in out:
            break

    for i, ln in enumerate(lines):
        if _SAL_RE.match(ln) and "salutation" not in out:
            out["salutation"] = ln if ln.endswith((",", ":")) else ln + ","
            # first sentence after the salutation
            para = " ".join(lines[i + 1:i + 6])
            m = re.match(r"(.{25,320}?[.;])(\s|$)", para)
            if m:
                out["opening"] = m.group(1).strip()
        m = _SUBJ_RE.match(ln)
        if m and "subject_label" not in out and len(m.group(2) or "") > 6:
            lab = re.sub(r"\s+", "", m.group(1))
            lab = re.sub(r"^(re)[\-–—]$", r"\1:", lab, flags=re.I)
            out["subject_label"] = lab[0].upper() + lab[1:]
        if re.match(r"^ref(erence)?\.?\s*[:\-]", ln, re.I):
            out["has_ref_line"] = True

    close_idx = None
    for i, ln in enumerate(lines):
        if _THANKS_RE.match(ln) and "thanks_line" not in out:
            out["thanks_line"] = ln
            close_idx = close_idx if close_idx is not None else i
        m = _CLOSE_RE.search(ln)
        if m and "close_phrase" not in out:
            out["close_phrase"] = "Yours " + m.group(1).lower() + ","
            close_idx = close_idx if close_idx is not None else i
            before = ln[:m.start()].strip()
            if before and _THANKS_RE.match(before):
                out.setdefault("thanks_line", before)
        fm = _FOR_RE.match(ln)
        if fm and "for_line" not in out:
            out["for_line"] = "For " + fm.group(1).strip().rstrip(",.")
            out["legal_name"] = fm.group(1).strip().rstrip(",.")
            sig = []
            for k in range(i + 1, min(i + 9, len(lines))):
                s = lines[k].strip().strip("()")
                if not s or re.fullmatch(r"(sd/?-?|\(?signature\)?|-+|_+)", s, re.I):
                    continue
                if _STOP_SIG.search(s):
                    break
                sig.append(s)
            name = next((s for s in sig if 2 <= len(s.split()) <= 6 and not re.search(r"\d", s)
                         and not _DESIG_RE.search(s) and re.match(r"^[A-Z(]", s)), None)
            if name:
                out["sig_name"] = re.sub(r"^\(|\)$", "", name).strip()
            desig = [s for s in sig if _DESIG_RE.search(s) and not _MEMB_RE.search(s)]
            if desig:
                out["sig_designation"] = desig[0]
            memb = next((_MEMB_RE.search(s).group(0) for s in sig if _MEMB_RE.search(s)), None)
            if memb:
                out["sig_membership"] = re.sub(r"\s+", " ", memb).strip()

    if close_idx:
        para = " ".join(lines[max(0, close_idx - 4):close_idx])
        sentences = [s.strip() for s in re.split(r"(?<=[.])\s+", para) if s.strip()]
        req = [s for s in sentences if _REQUEST_RE.search(s) and len(s) < 220]
        if req:
            out["request_line"] = req[-1] if req[-1].endswith(".") else req[-1] + "."

    bse = _exchange_block(lines, r"\bBSE\s+(Limited|Ltd)", r"400\s?001")
    nse = _exchange_block(lines, r"National\s+Stock\s+Exchange\s+of\s+India\s+(Limited|Ltd)", r"400\s?051")
    if bse:
        out["bse_block"] = bse
    if nse:
        out["nse_block"] = nse
    flat = "\n".join(lines)
    ib, inn = re.search(r"\bBSE\s+Limited", flat, re.I), re.search(r"National\s+Stock\s+Exchange", flat, re.I)
    if ib and inn:
        out["exchange_order"] = "bse_first" if ib.start() < inn.start() else "nse_first"

    code_lines = [_clean_code_line(ln) for ln in head
                  if _CODE_RE.search(ln) or re.fullmatch(r"(NSE|BSE)\s*[:\-]\s*[A-Z0-9&\-]{3,20}", ln)]
    if code_lines:
        out["code_lines"] = code_lines[:4]
        lab = re.search(r"(scrip\s*code|security\s*code|stock\s*code|scrip\s*id)", " ".join(code_lines), re.I)
        if lab:
            out["bse_label"] = " ".join(w.capitalize() for w in lab.group(1).split())

    if re.search(r"Listing\s+Obligations\s+and\s+Disclosure\s+Requirements", flat, re.I):
        out["lodr_cite"] = "full"
    elif re.search(r"\bLODR\b", flat):
        out["lodr_cite"] = "lodr"
    dt = re.search(r"\(\s*[\"“”'‘’]+\s*((?:the\s+)?(?:SEBI\s+)?(?:Listing\s+Regulations|LODR\s+Regulations|SEBI\s+LODR|Listing\s+Obligations))\s*[\"“”'‘’]+\s*\)", flat, re.I)
    if dt:
        out["defined_term"] = dt.group(1)
    if "as amended" in flat.lower():
        out["as_amended"] = True
    out["currency"] = ("₹" if "₹" in flat else "Rs." if re.search(r"\bRs\.", flat) else
                       "INR" if re.search(r"\bINR\b", flat) else None)
    out["self_ref"] = ("the Company" if len(re.findall(r"\bthe Company\b", flat)) >= len(re.findall(r"\bwe\b", flat, re.I))
                       else "we")
    return out


# ── Facts from letters ───────────────────────────────────

_CIN_RE = re.compile(r"\b([LU])\s?(\d{5})\s?([A-Z]{2})\s?(\d{4})\s?([A-Z]{3})\s?(\d{6})\b")
_ISIN_RE = re.compile(r"\b(IN[A-Z0-9]{9}\d)\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_WEB_RE = re.compile(r"\bwww\.[a-z0-9-]+(?:\.[a-z0-9-]+)+\b", re.I)
_PHONE_RE = re.compile(r"(?:Tel|Phone|Ph)\s*(?:No\.?)?\s*[.:!]?\s*(\+?[\d][\d\s()\-]{7,20}\d)", re.I)
_EXCHANGE_DOMAINS = ("bseindia.com", "nse.co.in", "nseindia.com", "sebi.gov.in", "nsdl.co.in", "cdslindia.com")


def isin_valid(isin: str) -> bool:
    """ISO 6166 check digit (Luhn over the letter-expanded digits)."""
    isin = (isin or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - total % 10) % 10 == int(isin[-1])


def cin_valid(cin: str) -> bool:
    return bool(re.fullmatch(r"[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}", (cin or "").strip().upper()))


def extract_facts(text: str, symbol: str = "") -> Dict[str, str]:
    t = _normalize(text)
    out: Dict[str, str] = {}
    m = _CIN_RE.search(t)
    if m:
        out["cin"] = "".join(m.groups())
    for cand in _ISIN_RE.findall(t):
        if isin_valid(cand):
            out["isin"] = cand
            break
    m = re.search(r"(?:scrip|security|stock)\s*(?:code|id)[^\d\n]{0,25}?(\d{6})\b", t, re.I) or \
        re.search(r"\bBSE\s*(?:code)?\s*[:\-]\s*(\d{6})\b", t, re.I)
    if m:
        out["bse_code"] = m.group(1)
    for e in _EMAIL_RE.findall(t):
        if not e.lower().endswith(_EXCHANGE_DOMAINS):
            out["email"] = e
            break
    m = _WEB_RE.search(t)
    if m:
        out["website"] = m.group(0).lower()
    m = _PHONE_RE.search(t)
    if m:
        out["phone"] = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"(?:Registered|Regd\.?)\s+Office\s*[:\-]?\s*(.{12,180}?)(?=\s*(?:Tel|Phone|Ph[.:]|CIN|Web|E-?mail|www\.|\n\n|$))",
                  t.replace("\n", " ") + "\n\n", re.I)
    if m:
        out["registered_office"] = m.group(1).strip(" ,.;:|")
    return out


# ── Learning ─────────────────────────────────────────────

def _corpus_from_filings(symbol: str, limit: int = 12, prefer_type: str = "") -> List[Dict]:
    """Recent exchange letters for the company, with their text. Text that
    had to be downloaded is saved back to the archive for next time."""
    from database import get_cached_filings
    rows = get_cached_filings(symbol) or []
    picked: List[Dict] = []
    if prefer_type:
        try:
            from draft_engine import get_filing_type
            kws = [k.lower() for k in (get_filing_type(prefer_type) or {}).get("keywords", [])]
            picked = [f for f in rows if any(k in (f.get("title", "") + " " + (f.get("description") or "")).lower()
                                             for k in kws)][: limit // 2]
        except Exception:  # noqa: BLE001
            picked = []
    skip = re.compile(r"annual\s+report|integrated\s+filing|xbrl|business\s+responsibility", re.I)
    for f in rows:
        if len(picked) >= limit:
            break
        if f in picked or skip.search(f.get("title") or ""):
            continue
        if f.get("pdf_link") or len(f.get("raw_text") or "") > 300:
            picked.append(f)

    def _text(f):
        txt = f.get("raw_text") or ""
        if len(txt) > 300 or not f.get("pdf_link"):
            return f, txt
        try:
            from pdf_parser import download_and_extract
            txt = download_and_extract(f["pdf_link"]) or ""
        except Exception:  # noqa: BLE001
            txt = ""
        if len(txt) > 300 and f.get("id"):
            try:
                conn = get_connection()
                conn.execute("UPDATE filings SET raw_text=? WHERE id=? AND length(coalesce(raw_text,''))<300",
                             (txt[:60000], f["id"]))
                conn.commit()
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return f, txt

    out = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for f, txt in pool.map(_text, picked):
            if len(txt) > 300:
                out.append({"text": txt[:8000], "title": f.get("title", ""), "date": f.get("date", ""),
                            "source": "filing", "ref": str(f.get("id") or ""), "pdf_link": f.get("pdf_link", "")})
    return out


def learn(owner_id: str, symbol: str, filing_type: str = "", company_name: str = "",
          by: str = "", corpus: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Learn the house format and company facts and store them. `corpus`
    can be supplied (tests); otherwise it is the company's recent exchange
    letters plus the team's own documents and exported drafts."""
    symbol = (symbol or "").upper()
    docs = list_docs(owner_id, symbol, include_team=False, with_text=True)
    team_texts = [{"text": d["text"], "title": d["title"], "source": d["source"], "ref": str(d["id"])}
                  for d in docs if d["source"] in ("upload", "paste", "approved", "filing")]
    if corpus is None:
        corpus = _corpus_from_filings(symbol, prefer_type=filing_type)
    letters = team_texts + list(corpus)
    if not letters:
        raise ValueError("No letters to learn from yet: Delta has no filings with text for this "
                         "company. Upload a few of your past letters instead.")

    vocab = _vocab(l["text"] for l in letters)
    analyses = []
    for l in letters:
        a = analyze_letter(l["text"], vocab)
        a["_weight"] = 3.0 if l["source"] in ("upload", "paste", "approved") else 1.0
        a["_title"], a["_date"] = l.get("title", ""), l.get("date", "")
        analyses.append(a)
    n = len(analyses)

    def vote(key, min_share=0.0):
        inst = [(a[key], a["_weight"]) for a in analyses if isinstance(a.get(key), str)]
        return _pick(inst, vocab, min_share=min_share, total=n)

    def vote_list(key):
        inst = [(" | ".join(a[key]), a["_weight"]) for a in analyses if a.get(key)]
        p = _pick(inst, vocab, total=n)
        if p:
            p["value"] = p["value"].split(" | ")
        return p

    fmt: Dict[str, Any] = {}
    for key in ("date_format", "date_label", "salutation", "subject_label", "thanks_line", "close_phrase", "for_line",
                "request_line", "sig_name", "sig_designation", "sig_membership", "lodr_cite",
                "defined_term", "currency", "exchange_order", "bse_label", "opening"):
        p = vote(key)
        weak_ok = key not in ("opening", "currency", "defined_term") or p and p["count"] >= 2
        if p and weak_ok:
            fmt[key] = p
    for key in ("bse_block", "nse_block", "code_lines"):
        p = vote_list(key)
        if p:
            fmt[key] = p
    amended = sum(1 for a in analyses if a.get("as_amended"))
    fmt["as_amended"] = {"value": amended * 2 >= n, "count": amended, "of": n}
    self_ref = Counter(a.get("self_ref") for a in analyses if a.get("self_ref"))
    if self_ref:
        val, cnt = self_ref.most_common(1)[0]
        fmt["self_reference"] = {"value": val, "count": cnt, "of": n}
    if "date_format" in fmt:
        fmt["date_format"]["example"] = next(ex for name, _, ex in _DATE_PATTERNS if name == fmt["date_format"]["value"])

    # Facts: majority vote across letters, validated.
    fact_votes: Dict[str, Counter] = defaultdict(Counter)
    for l in letters:
        for k, v in extract_facts(l["text"], symbol).items():
            fact_votes[k][v] += 1
    learned: Dict[str, Dict] = {}
    for k, c in fact_votes.items():
        v, cnt = c.most_common(1)[0]
        if k == "cin" and not cin_valid(v):
            continue
        if k in ("phone", "email", "website", "registered_office") and cnt < 2 and n >= 4:
            continue
        if k == "registered_office":
            v = _repair(v, vocab).strip(" ,.;")
        learned[k] = {"value": v, "source": "filings", "detail": f"Seen in {cnt} of {n} letters"}
    for src_key, fact_key in (("sig_name", "cs_name"), ("sig_designation", "cs_designation"),
                              ("sig_membership", "cs_membership")):
        if src_key in fmt:
            learned[fact_key] = {"value": fmt[src_key]["value"], "source": "filings",
                                 "detail": f"Signed {fmt[src_key]['count']} of {n} letters"}
    if "for_line" in fmt:
        learned["legal_name"] = {"value": re.sub(r"^For\s+", "", fmt["for_line"]["value"]),
                                 "source": "filings", "detail": f"Signed off {fmt['for_line']['count']} of {n} letters"}
    elif company_name:
        learned["legal_name"] = {"value": company_name, "source": "filings", "detail": "Company name"}

    current = _row(owner_id, symbol)
    style = dict(current["style"])
    style["format"] = fmt
    style["sources"] = {
        "letters": n,
        "team_documents": len(team_texts),
        "filings": len(corpus),
        "examples": [{"title": a["_title"], "date": a["_date"]} for a in analyses[:12]],
    }
    _upsert(owner_id, symbol, by=by, style_json=json.dumps(style),
            facts_json=json.dumps(merge_facts(current["facts"], learned)), learned_at=_now())
    return get_memory(owner_id, symbol)


def save_type_summary(owner_id: str, symbol: str, filing_type: str, summary: Dict[str, Any]):
    symbol = (symbol or "").upper()
    style = _row(owner_id, symbol)["style"]
    by_type = style.setdefault("by_type", {})
    by_type[filing_type] = {**summary, "learned_at": _now()}
    _upsert(owner_id, symbol, style_json=json.dumps(style))


# ── Read model for the desk ──────────────────────────────

def get_memory(owner_id: str, symbol: str) -> Dict[str, Any]:
    symbol = (symbol or "").upper()
    team = _row(owner_id, "")
    company = _row(owner_id, symbol) if symbol else {"instructions": "", "style": {}, "learned_at": None}
    facts = facts_for(owner_id, symbol) if symbol else {}
    docs = list_docs(owner_id, symbol) if symbol else list_docs(owner_id, "", include_team=False)
    return {
        "symbol": symbol,
        "team_instructions": team["instructions"],
        "company_instructions": company["instructions"],
        "facts": [{"key": k, "label": lbl, **facts[k]} for k, lbl in FACT_FIELDS if k in facts],
        "missing_facts": [{"key": k, "label": lbl} for k, lbl in FACT_FIELDS if k not in facts],
        "format": (company.get("style") or {}).get("format", {}),
        "by_type": (company.get("style") or {}).get("by_type", {}),
        "sources": (company.get("style") or {}).get("sources", {}),
        "learned_at": company.get("learned_at"),
        "documents": docs,
    }


def house_format(owner_id: str, symbol: str) -> Dict[str, Any]:
    """The plain values the letter renderer needs (no counts)."""
    fmt = _row(owner_id, (symbol or "").upper())["style"].get("format", {})
    return {k: v.get("value") for k, v in fmt.items() if isinstance(v, dict) and v.get("value") not in (None, "")}


def prompt_context(owner_id: str, symbol: str, filing_type: str = "",
                   max_examples: int = 3, past_examples: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Everything a model needs to write like this team, as text blocks plus
    the facts dict for deterministic use."""
    mem = get_memory(owner_id, symbol)
    fmt = mem["format"]
    facts = facts_for(owner_id, symbol)

    rules = []
    if mem["team_instructions"]:
        rules.append("Team rules (apply to every company):\n" + mem["team_instructions"])
    if mem["company_instructions"]:
        rules.append(f"Rules for {symbol}:\n" + mem["company_instructions"])

    f_lines = []
    def g(k):
        return (fmt.get(k) or {}).get("value")
    n = (mem["sources"] or {}).get("letters") or 0
    if fmt:
        f_lines.append(f"House format learned from {n} of the company's own letters. Follow it exactly.")
        if g("date_format"):
            f_lines.append(f"- Write dates like \"{fmt['date_format'].get('example')}\".")
        if g("lodr_cite") == "full":
            f_lines.append("- Cite the regulations in full: \"Regulation <n> of the SEBI (Listing Obligations and "
                           "Disclosure Requirements) Regulations, 2015\"" +
                           (f", then refer to them as \"{g('defined_term')}\"." if g("defined_term") else "."))
        elif g("lodr_cite") == "lodr":
            f_lines.append("- Cite the regulations as \"SEBI (LODR) Regulations, 2015\".")
        if (fmt.get("as_amended") or {}).get("value"):
            f_lines.append("- Add \"as amended\" after the first citation of a regulation.")
        if g("opening"):
            f_lines.append(f"- Typical opening sentence: \"{g('opening')}\"")
        if g("request_line"):
            f_lines.append(f"- End the body with exactly: \"{g('request_line')}\"")
        if g("currency"):
            f_lines.append(f"- Write amounts with \"{g('currency')}\".")
        if g("self_reference"):
            f_lines.append(f"- Refer to the company as \"{g('self_reference')}\".")
    by_type = (mem["by_type"] or {}).get(filing_type) or {}
    if by_type.get("tone") or by_type.get("phrases"):
        f_lines.append("For this filing type the company typically writes:")
        if by_type.get("tone"):
            f_lines.append(f"- Tone: {by_type['tone']}")
        for p in (by_type.get("phrases") or [])[:8]:
            f_lines.append(f"- \"{p}\"")
        for s in (by_type.get("structure") or [])[:6]:
            f_lines.append(f"- Structure: {s}")

    fact_lines = [f"- {FACT_LABELS[k]}: {v['value']}" for k, v in facts.items() if k in FACT_LABELS]

    # Examples: the team's approved drafts and uploads of this type first,
    # then the company's own past filings of this type.
    examples = []
    docs = list_docs(owner_id, symbol, filing_type=filing_type, with_text=True)
    for d in sorted(docs, key=lambda d: (d["source"] != "approved", d["filing_type"] != filing_type)):
        if len(examples) >= max_examples:
            break
        examples.append({"label": {"approved": "a letter your team exported",
                                   "upload": "a reference your team uploaded",
                                   "paste": "a reference your team pasted",
                                   "filing": "a filing your team pinned"}[d["source"]],
                         "title": d["title"], "text": d["text"][:2500]})
    for ex in past_examples or []:
        if len(examples) >= max_examples:
            break
        if (ex.get("text") or "").strip():
            examples.append({"label": "a past filing by the company", "title": ex.get("title", ""),
                             "text": ex["text"][:2500]})

    return {
        "rules": "\n\n".join(rules),
        "format": "\n".join(f_lines),
        "facts": "\n".join(fact_lines),
        "facts_dict": fact_values(facts),
        "examples": examples,
        "house": house_format(owner_id, symbol),
    }
