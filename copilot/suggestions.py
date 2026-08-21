"""What a Company Secretary would plausibly ask from this screen.

Suggestions are the assistant's opening move. Generic prompts ("How can I
help?") waste the one moment the user is willing to try it, so every
suggestion here is phrased as real CS work and is answerable from the
context the page already supplies.
"""

from typing import Dict, List

_BY_PAGE: Dict[str, List[str]] = {
    "dashboard": [
        "What requires my attention today?",
        "Which filings are due this month?",
        "Summarise this week's disclosures",
        "Which company needs review first?",
    ],
    "command_center": [
        "What should I complete first?",
        "Why is this item high priority?",
        "Which items have documents ready?",
        "Summarise my open compliance work",
    ],
    "compliance": [
        "What filings are due this month?",
        "Which obligations are overdue?",
        "What happens if this filing is delayed?",
        "Prepare a compliance checklist",
    ],
    "company": [
        "Summarise recent filings",
        "What governance changes happened?",
        "Any compliance risks?",
        "Compare this quarter with the previous quarter",
    ],
    "filing": [
        "What does this filing mean?",
        "What action is required?",
        "Is any further filing needed?",
        "Draft the required disclosure",
    ],
    "event": [
        "What action is required?",
        "Which regulations apply here?",
        "What documents should we file?",
        "Draft the exchange disclosure",
    ],
    "director": [
        "Show director history",
        "Which companies is this person associated with?",
        "Any public regulatory disclosures?",
        "Create a governance summary",
    ],
    "draft": [
        "Review this draft for compliance gaps",
        "What annexures are required?",
        "Which regulation governs this letter?",
        "Suggest a stronger opening paragraph",
    ],
    "prices": [
        "Did any filing explain this movement?",
        "Which disclosures went out this week?",
        "Is a Regulation 30 disclosure required?",
        "Summarise recent material events",
    ],
    "generic": [
        "What filings are triggered by a director resignation?",
        "Explain SEBI LODR Regulation 30",
        "What are this quarter's key deadlines?",
        "Prepare a compliance checklist",
    ],
}


def suggest_for(ctx: Dict) -> List[str]:
    """Suggestions for the current screen, specialised with real names where
    the context supplies them."""
    page = ctx.get("page_type", "generic")
    out = list(_BY_PAGE.get(page, _BY_PAGE["generic"]))

    company = ctx.get("company")
    facts = ctx.get("facts") or {}

    if page == "company" and company:
        out[0] = f"Summarise {company}'s recent filings"
        if facts.get("latest_board_delta"):
            out[1] = f"What changed on {company}'s board?"

    if page == "event":
        ev = facts.get("event") or {}
        if ev.get("label"):
            out[0] = f"What must we do about this {ev['label'].lower()}?"
        if facts.get("documents_prepared"):
            out[2] = "Review the documents Delta prepared"

    if page == "director" and ctx.get("director"):
        out[0] = f"Show {ctx['director']}'s directorship history"

    if page == "compliance":
        n = (facts.get("portfolio") or {}).get("obligations_due_60d")
        if n:
            out[0] = f"Walk me through the {n} obligations due in 60 days"

    return out[:4]
