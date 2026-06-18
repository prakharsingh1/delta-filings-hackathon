"""Director intelligence — for the DIN search page.

Given a director's name (resolved from a DIN), this builds a compact dossier:
  • a short "who they are" bio,
  • a summary of recent news with the underlying headlines,
  • legal / regulatory exposure: SEBI enforcement orders that name them, plus
    any litigation surfaced in recent press.

It reuses the news-research + AI plumbing from price_intel (Bing News, article
fetching, the OpenRouter/Gemini prompt runners with the reasoning-leak guard)
and the SEBI order search from sebi_enforcement. Results are cached per name
for 24h so repeat lookups are instant.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional

from price_intel import (
    _bing_news, _fetch_article_text,
    _openrouter_why, _gemini_why,
    _FREE_TIER_MODELS, _DEFAULT_INTEL_MODEL,
)

_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 24 * 3600

_LEGAL_WORDS = (
    "lawsuit", "litigation", "court", "tribunal", "sebi", "fraud", "probe",
    "investigation", "fir", "charge", "arrest", "bail", "penalty", "fine",
    "ban", "disqualif", "insider trading", "ed ", "enforcement directorate",
    "cbi", "summon", "prosecut", "settlement", "show cause", "nclt",
)


_WIKI_UA = {"User-Agent": "DeltaFilings/1.0 (corporate governance research)"}


def _wikipedia_summary(name: str) -> Optional[Dict]:
    """Fetch an encyclopedic summary for a person from Wikipedia. Returns
    {extract, description, url, title} or None. Skips disambiguation pages and
    obviously-irrelevant matches so we don't attach the wrong person's bio."""
    import requests
    try:
        # 1) Resolve the best page title.
        r = requests.get("https://en.wikipedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": name,
            "format": "json", "srlimit": 3}, headers=_WIKI_UA, timeout=10)
        hits = r.json().get("query", {}).get("search", [])
        if not hits:
            return None
        # Prefer a title that overlaps the searched surname.
        last = name.lower().split()[-1] if name.split() else ""
        title = next((h["title"] for h in hits if last and last in h["title"].lower()),
                     hits[0]["title"])

        # 2) Fetch the REST summary extract.
        s = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}",
            headers=_WIKI_UA, timeout=10)
        if s.status_code != 200:
            return None
        j = s.json()
        if j.get("type") == "disambiguation":
            return None
        extract = (j.get("extract") or "").strip()
        if len(extract) < 40:
            return None
        # Relevance guard: the entry should look like a business/leadership bio.
        low = extract.lower()
        if not any(w in low for w in (
            "business", "entrepreneur", "founder", "chairman", "chairperson",
            "director", "ceo", "executive", "industrialist", "company", "limited",
            "co-founder", "managing", "billionaire", "philanthrop")):
            return None
        return {
            "extract": extract,
            "description": j.get("description", ""),
            "title": title,
            "url": (j.get("content_urls", {}).get("desktop", {}).get("page")
                    or f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"),
        }
    except Exception as e:
        print(f"[DIN-INTEL] wikipedia lookup failed for {name}: {e}")
        return None


def _ai_run(prompt: str, api_key: str, model: str) -> Optional[str]:
    """Provider-aware single-prompt completion (OpenRouter for sk-or-… keys,
    Gemini otherwise). Both paths strip reasoning leaks."""
    if not api_key:
        return None
    if api_key.startswith("sk-or-"):
        return _openrouter_why(prompt, api_key)
    safe = model if model in _FREE_TIER_MODELS else _DEFAULT_INTEL_MODEL
    return _gemini_why(prompt, api_key, safe)


def _research_person(name: str, max_articles: int = 5) -> List[Dict]:
    """Find + read recent articles about a director. Returns
    [{title, url, source, published, excerpt}]."""
    headlines = _bing_news(f'"{name}" director', 10)
    headlines += _bing_news(f'{name} board company', 6)

    seen, ranked = set(), []
    nm = name.lower()
    for h in headlines:
        if h["url"] in seen:
            continue
        seen.add(h["url"])
        t = h["title"].lower()
        score = 1
        if nm.split()[0] in t and (len(nm.split()) < 2 or nm.split()[-1] in t):
            score += 4  # likely actually about this person
        if any(w in t for w in _LEGAL_WORDS):
            score += 2
        ranked.append((score, h))
    ranked.sort(key=lambda x: -x[0])
    candidates = [h for s, h in ranked][:max_articles * 2]

    results = []
    if not candidates:
        return results
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_fetch_article_text, h["url"]): h for h in candidates}
        try:
            for fut in as_completed(futs, timeout=14):
                h = futs[fut]
                try:
                    txt = fut.result()
                except Exception:
                    txt = None
                if txt:
                    results.append({**h, "excerpt": txt})
                if len(results) >= max_articles:
                    break
        except Exception:
            pass  # timeout — return whatever we have
    return results


def _build_prompt(name: str, roles: List[str], research: List[Dict],
                  wiki: Optional[Dict] = None) -> str:
    parts = [f"SUBJECT: {name}, an Indian company director."]
    if roles:
        parts.append("KNOWN BOARD ROLES (from filings): " + "; ".join(roles[:8]))
    if wiki:
        parts.append(f"\nENCYCLOPEDIA ENTRY (Wikipedia — authoritative for the bio):"
                     f"\n  {wiki['description']}\n  {wiki['extract']}")
    if research:
        parts.append("\nRECENT NEWS ARTICLES (use for the NEWS section):")
        for i, a in enumerate(research, 1):
            parts.append(f"\n  ARTICLE {i}: {a['title']} — {a.get('source','')} ({a.get('published','')})")
            parts.append(f"  {a['excerpt']}")
    else:
        parts.append("\n(No recent news articles could be retrieved.)")

    return (
        "You are writing a concise, factual mini-profile of the Indian company "
        f"director {name}, in the style of a short encyclopedia entry. Base the "
        "bio on the ENCYCLOPEDIA ENTRY when present; use the news only for recent "
        "developments. Do not invent facts.\n\n"
        "Return EXACTLY these three labelled sections, plain prose, no markdown, "
        "no preamble:\n"
        "WHO: 3-4 sentences — a crisp biographical profile: who they are, what "
        "they founded or lead, their field, and notable achievements/recognition. "
        "Write confidently if the encyclopedia entry is present. Only say "
        "'identification uncertain' if there is genuinely no reliable evidence.\n"
        "NEWS: 2-3 sentences on the most relevant recent developments from the "
        "articles. If none, write 'No notable recent coverage found.'\n"
        "LEGAL: Any litigation, SEBI/regulatory action, investigation or "
        "controversy in the evidence, with the source. If none, write exactly "
        "'No legal matters found in recent coverage.'\n\n"
        "EVIDENCE:\n" + "\n".join(parts)
    )


def _parse_sections(text: str) -> Dict[str, str]:
    """Split the AI output into WHO / NEWS / LEGAL sections."""
    out = {"who": "", "news": "", "legal": ""}
    if not text:
        return out
    # Match 'LABEL: ...' up to the next label or end.
    for key, label in (("who", "WHO"), ("news", "NEWS"), ("legal", "LEGAL")):
        m = re.search(rf"{label}\s*:\s*(.+?)(?=\n\s*(?:WHO|NEWS|LEGAL)\s*:|\Z)",
                      text, re.IGNORECASE | re.DOTALL)
        if m:
            out[key] = m.group(1).strip()
    # Fallback: if nothing parsed, dump everything into 'who'.
    if not any(out.values()):
        out["who"] = text.strip()
    return out


def get_director_intel(name: str, din: str = "", roles: Optional[List[str]] = None,
                       api_key: str = "", model: str = _DEFAULT_INTEL_MODEL) -> Dict:
    """Build the director dossier. Cached 24h per (name, din)."""
    name = (name or "").strip()
    if not name:
        return {"error": "no name", "available": False}

    cache_key = f"{din}|{name.lower()}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    # 0) Encyclopedic backbone for the bio.
    wiki = _wikipedia_summary(name)

    # 1) Read recent press.
    research = _research_person(name)

    # 2) SEBI enforcement orders naming this person (authoritative legal signal).
    sebi_orders = []
    try:
        from sebi_enforcement import fetch_orders_for_company
        raw = fetch_orders_for_company("", name, max_pages_per_category=2) or []
        # Keep only reasonably-confident hits; SEBI search is substring-noisy.
        for o in raw[:8]:
            sebi_orders.append({
                "title": o.get("title", ""),
                "url": o.get("url", ""),
                "date": o.get("order_date", ""),
                "category": o.get("category_label", o.get("category", "")),
                "confidence": o.get("confidence", "low"),
            })
    except Exception as e:
        print(f"[DIN-INTEL] SEBI search failed for {name}: {e}")

    # 3) AI dossier (bio + news + legal-from-news).
    sections = {"who": "", "news": "", "legal": ""}
    ai_used = False
    if api_key and (research or wiki):
        out = _ai_run(_build_prompt(name, roles or [], research, wiki), api_key, model)
        if out:
            sections = _parse_sections(out)
            ai_used = True

    # Non-AI fallback so the panel is never empty.
    if not ai_used:
        # Wikipedia extract is itself a great "small wiki" bio without any AI.
        if wiki:
            sections["who"] = wiki["extract"][:600]
        else:
            sections["who"] = (f"{name} appears in recent corporate coverage. "
                               "Add an AI key in Settings for a full bio.") if research \
                else f"No public encyclopedia entry or recent coverage found for {name}."
        if research:
            sections["news"] = (f'Latest: "{research[0]["title"]}" '
                                f'({research[0].get("source","")}).')
        # Heuristic legal flag from headlines.
        legal_hits = [a for a in research
                      if any(w in (a["title"] + a.get("excerpt", "")).lower()
                             for w in _LEGAL_WORDS)]
        sections["legal"] = (
            f'Possible legal mention: "{legal_hits[0]["title"]}".' if legal_hits
            else "No legal matters found in recent coverage."
        )

    has_legal = bool(sebi_orders) or (
        sections.get("legal", "").strip().lower()
        not in ("", "no legal matters found in recent coverage.",
                "none found in recent coverage.")
    )

    payload = {
        "available": True,
        "name": name,
        "din": din,
        "bio": sections.get("who", ""),
        "news_summary": sections.get("news", ""),
        "legal_summary": sections.get("legal", ""),
        "news": [{"title": a["title"], "url": a["url"], "source": a.get("source", ""),
                  "published": a.get("published", "")} for a in research],
        "wiki_url": wiki["url"] if wiki else "",
        "wiki_desc": wiki["description"] if wiki else "",
        "sebi_orders": sebi_orders,
        "has_legal": has_legal,
        "ai_powered": ai_used,
        "fetched_at": datetime.now().isoformat(),
    }
    _CACHE[cache_key] = (now, payload)
    return payload
