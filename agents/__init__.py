"""The Delta Filings AI Company Secretary team.

Three agents, each modelled on a real role in a CS department:

    monitor       Junior CS Associate      "What happened?"
    intelligence  Senior CS Professional   "Why does it matter?"
    action        CS Documentation Asst.   "What should we do?"

House rule for all three: deterministic logic decides, the model interprets.
Regulations are retrieved from compliance.SEBI_MANDATES and filing types from
draft_engine.FILING_TYPES — the model picks from closed lists, it never
invents a citation.
"""

from .client import AGENT_MODELS, call_json, gemini_available

__all__ = ["AGENT_MODELS", "call_json", "gemini_available"]
