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
    "risk": [
        "Which counterparties moved to High this month?",
        "Explain why this company's score rose",
        "What should the risk committee see first?",
        "What does an IBBI CIRP admission mean for our exposure?",
    ],
    "news": [
        "Which of these updates affect my companies?",
        "Summarise this week's SEBI circulars",
        "What changed for listed companies this month?",
        "Which RBI notifications matter for NBFCs?",
    ],
    "insider": [
        "When should the trading window close this quarter?",
        "Who must be recorded in the structured digital database?",
        "Can a designated person trade after pre-clearance?",
        "What goes into a PIT Reg 7 disclosure?",
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

    if page == "news" and company:
        name = ctx.get("company_name") or company
        desk = facts.get("news_desk") or {}
        moves = desk.get("notable_moves") or []
        out = [f"Why did {name} move this month?",
               "Which filings does this news trigger, and by when?",
               "What could move the price next?",
               f"What is Reddit saying about {name}?"]
        if moves:
            m = moves[0]
            out[0] = f"Why did {name} move {'up' if (m.get('pct') or 0) > 0 else 'down'} on {m.get('date')}?"

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
