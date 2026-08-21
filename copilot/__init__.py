"""Delta Copilot — a senior compliance professional sitting beside the user.

Not a page. A floating assistant present on every screen that already knows
where the user is and what they are looking at, so they never have to
re-explain context to it.

    context.py      what is the user looking at right now
    suggestions.py  what would a CS plausibly ask from here
    retrieval.py    grounding data pulled from Delta's own records
    handler.py      prompt construction, model call, response shaping
"""

from .context import build_context, PAGE_TYPES
from .suggestions import suggest_for
from .handler import answer

__all__ = ["build_context", "PAGE_TYPES", "suggest_for", "answer"]
