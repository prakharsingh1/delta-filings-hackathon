"""AI summarization using Google Gemini + keyword-based tagging."""

import os
import re

# ── Tag detection (no AI, fast) ──

def detect_tags(title, text=""):
    combined = (title + " " + text).lower()
    tags = []

    tag_keywords = {
        "Board Change": ["board meeting", "director", "appointment", "resignation", "retire"],
        "Compliance": ["compliance", "regulation", "sebi", "lodr", "certificate", "rbi"],
        "Financial Results": ["financial result", "quarterly", "revenue", "profit", "dividend"],
        "Leadership": ["key managerial", "ceo", "cfo", "cro", "managing director", "personnel"],
        "Restructuring": ["scheme", "arrangement", "demerger", "merger", "acquisition"],
        "AGM/EGM": ["annual general", "extraordinary general", "agm", "egm", "resolution"],
        "Credit/Rating": ["credit rating", "rating update", "npa", "provisioning"],
        "Insider Trading": ["trading window", "insider trading", "prohibition"],
        "Investor Relations": ["analyst", "investor meet", "conference call", "investor presentation"],
        "Divestment": ["disinvestment", "divestment", "diversification", "stake sale"],
        "Stock Options": ["stock option", "esop", "performance share", "grant of"],
    }

    for tag, keywords in tag_keywords.items():
        if any(kw in combined for kw in keywords):
            tags.append(tag)

    return tags if tags else ["General"]


# ── Model definitions ──

GEMINI_MODELS = {
    "gemini-2.5-flash": {
        "name": "Gemini 2.5 Flash",
        "description": "Fast & efficient for straightforward data extraction.",
        "speed": "~3-5s per summary",
        "cost": "Free",
        "badge": "Free",
        "badge_color": "emerald",
    },
    "gemini-2.5-pro": {
        "name": "Gemini 2.5 Pro",
        "description": "Consulting-grade reasoning for complex filings.",
        "speed": "~10-15s per summary",
        "cost": "$1.25 / 1M tokens",
        "badge": "Pro",
        "badge_color": "indigo",
    },
}


# ── AI Summarization ──

SYSTEM_PROMPT = (
    "You are a corporate governance analyst specializing in Indian stock market filings (NSE/BSE). "
    "Summarize this corporate filing in 3-5 concise bullet points using • markers. Focus on:\n"
    "- Key decisions or outcomes (board approvals, resolutions, appointments)\n"
    "- Governance and leadership changes (new directors, resignations, committee changes)\n"
    "- Compliance and regulatory matters (SEBI regulations, LODR submissions)\n"
    "- Financial impact if mentioned (amounts in ₹, percentages, dividends)\n"
    "- Important dates and deadlines\n\n"
    "Rules:\n"
    "- Be specific with names, numbers, and dates. No vague statements.\n"
    "- Skip boilerplate like addresses, sign-offs, registrar details.\n"
    "- Write in clear, professional English. Each bullet should be a complete thought.\n"
    "- If the document is a routine compliance certificate with no material information, "
    "say so in one bullet point."
)


def summarize_text(text, title="", description="", api_key="", model="gemini-2.5-flash", temperature=0.3):
    """Summarize a corporate filing using Google Gemini.
    Returns a summary string, or a fallback if API is unavailable."""
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return _fallback_summary(text, title, description)

    try:
        from google import genai

        client = genai.Client(api_key=api_key)

        prompt_text = f"Filing Title: {title}\n"
        if description:
            prompt_text += f"Description: {description}\n"
        prompt_text += f"\nFull Document Text:\n{text[:8000]}"

        response = client.models.generate_content(
            model=model,
            contents=prompt_text,
            config={
                "system_instruction": SYSTEM_PROMPT,
                "temperature": temperature,
                "max_output_tokens": 4096,
                "thinking_config": {"thinking_budget": 0},
            },
        )
        summary = response.text.strip()
        print(f"[AI] Gemini ({model}) summarized: {title[:50]}")
        return summary

    except Exception as e:
        print(f"[AI] Gemini error: {e}")
        return _fallback_summary(text, title, description)


def _fallback_summary(text, title="", description=""):
    """Generate a clean extractive summary without AI."""
    raw = (text or "").strip()
    if not raw and not description:
        return "No summary available — document text is too short or empty."

    # Clean up the raw text: collapse whitespace, remove headers/footers junk
    clean = re.sub(r'\s+', ' ', raw)
    clean = re.sub(r'(Dear Sir\s*/\s*Madam,?|To Whomsoever It May Concern,?)', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'(Yours faithfully|Yours truly|Thanking you|For and on behalf of).*', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'(Sd/-|sd/-).*?(?=\.|$)', '', clean)
    clean = re.sub(r'com Security Code:\s*\d+\s*Sub:', '', clean)

    sentences = re.split(r'(?<=[.!?])\s+', clean)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 25]

    high_value = [
        "appointed", "resigned", "cessation", "ceased", "joined",
        "approved", "declared", "dividend", "interim",
        "board of directors", "board meeting", "outcome",
        "disclosure", "regulation 30", "regulation 31", "regulation 74",
        "acquired", "divestment", "transfer", "allot",
        "financial year", "quarter ended", "half year",
        "trading window", "closure", "opening",
        "scheme of arrangement", "merger", "demerger",
        "complian", "certificate", "confirmation",
        "grant of", "stock option", "esop",
    ]
    noise = [
        "dear sir", "madam", "pursuant to", "sub:", "ref:", "com security",
        "enclos", "attached herewith", "kindly take", "please find",
        "we hereby", "this is to inform", "acknowledged",
        "this is for your information", "for your record", "take on record",
        "noted and taken", "yours sincerely", "warm regards",
    ]
    address_noise = [
        "cin:", "phone :", "e-mail:", "website :", "regd", "registered office",
        "fax:", "tel:", "pin code", "pincode",
        "registrar and share transfer", "registrar and transfer agent",
        "kfin technologies", "link intime", "karvy", "alankit",
        "company secretary", "compliance officer",
    ]

    scored = []
    seen_prefixes = set()
    for s in sentences:
        lower = s.lower()
        if any(n in lower for n in noise):
            continue
        if any(n in lower for n in address_noise):
            continue
        if len(s) < 30 or len(s) > 300:
            continue
        words = s.split()
        if len(words) < 6:
            continue
        prefix = lower[:50]
        if prefix in seen_prefixes:
            continue
        seen_prefixes.add(prefix)

        score = sum(1 for kw in high_value if kw in lower)
        idx = sentences.index(s)

        has_action = any(v in lower for v in [
            "has ", "have ", "was ", "were ", "is ", "are ", "will ",
            "shall ", "informed", "submitted", "approved", "declared",
            "appointed", "resigned", "granted", "issued", "noted",
            "decided", "resolved", "disclosed", "acquired", "transferred",
            "conducted", "held", "convened", "closed", "opened",
        ])
        if not has_action and score < 2:
            continue
        if idx < 5:
            score += 1
        if "limited" in lower and ("house" in lower or "marg" in lower or "road" in lower):
            score -= 2
        scored.append((score, idx, s))

    scored.sort(key=lambda x: (-x[0], x[1]))

    points = []
    if description and description.strip().lower() != title.strip().lower():
        desc_clean = description.strip()
        if len(desc_clean) > 20 and not any(n in desc_clean.lower() for n in noise):
            points.append(desc_clean)

    for score, idx, sent in scored:
        if len(points) >= 3:
            break
        sent = sent.strip().rstrip('.')
        if any(_similar(sent, p) for p in points):
            continue
        points.append(sent)

    if not points:
        for s in sentences[:3]:
            s = s.strip().rstrip('.')
            if len(s) > 25 and not any(_similar(s, p) for p in points):
                points.append(s)

    if not points:
        return f"{title}" if title else "No summary available."

    return "\n".join(f"• {p}." for p in points)


def _similar(a, b):
    """Check if two strings are roughly the same content."""
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
    return overlap > 0.7
