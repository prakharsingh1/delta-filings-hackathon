"""Shared Gemini client for the agent team.

Exists because ai.py and draft_engine.py each grew their own Gemini caller
with different fallback rules — draft_engine's silently rewrites any model
outside its free-tier set down to Flash, which would have quietly demoted
Agent 2 off Pro.

Everything here returns parsed JSON. No agent depends on free-form text.
"""

import json
import os
import re
import time
from typing import Any, Dict, Optional

# Per-role model assignment. Flash for extraction and drafting, Pro for the
# reasoning that carries the product.
AGENT_MODELS = {
    "monitor": "gemini-2.5-flash",
    "intelligence": "gemini-2.5-pro",
    "action": "gemini-2.5-flash",
}

# Ordered fallbacks per tier, used when the primary is overloaded.
# Verified against the live models list. gemini-2.0-flash was retired by
# Google and returns 404 NOT_FOUND, which silently cost us an Agent 2 run;
# the "-latest" aliases are here so a future retirement degrades instead of
# failing outright.
_FALLBACKS = {
    "gemini-2.5-pro": ["gemini-2.5-flash", "gemini-flash-latest"],
    "gemini-2.5-flash": ["gemini-2.5-flash-lite", "gemini-flash-latest"],
}


# .env is loaded by supabase_client on app boot, but the agents also run from
# scripts and the pipeline CLI, so make the key available standalone too.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def get_api_key() -> str:
    """Server-side key for the agent pipeline.

    The per-user key in settings still drives the interactive summarize/draft
    features; agents run unattended so they use the platform key.
    """
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if key:
        return key
    try:
        from database import get_setting
        return (get_setting("gemini_api_key", "") or "").strip()
    except Exception:
        return ""


def gemini_available() -> bool:
    return bool(get_api_key())


def _extract_json(text: str) -> Optional[Any]:
    """Parse a model response into JSON, tolerating ``` fences and preamble."""
    if not text:
        return None
    t = text.strip()

    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()

    try:
        return json.loads(t)
    except ValueError:
        pass

    # Last resort: first balanced {...} or [...] span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        end = t.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except ValueError:
                continue
    return None


def call_json(prompt: str, *, role: str = "monitor", model: Optional[str] = None,
              max_output_tokens: int = 2000, temperature: float = 0.2,
              thinking: bool = False) -> Dict[str, Any]:
    """One structured call. Never raises.

    Returns {"ok", "data", "model", "latency_ms", "error"} so callers can
    record model + latency into audit_log without a second code path.
    """
    started = time.time()
    model = model or AGENT_MODELS.get(role, "gemini-2.5-flash")
    result = {"ok": False, "data": None, "model": model, "latency_ms": 0, "error": ""}

    api_key = get_api_key()
    if not api_key:
        result["error"] = "no_api_key"
        return result

    try:
        from google import genai
    except Exception as e:
        result["error"] = f"genai_import_failed: {e}"
        return result

    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        result["error"] = f"client_init_failed: {e}"
        return result

    config: Dict[str, Any] = {
        "max_output_tokens": max_output_tokens,
        "temperature": temperature,
        "response_mime_type": "application/json",
    }
    # Agent 2 reasons; Agents 1 and 3 extract and format, so thinking is off
    # there to keep the pipeline fast enough to run live on stage.
    if not thinking:
        config["thinking_config"] = {"thinking_budget": 0}

    chain = [model] + [m for m in _FALLBACKS.get(model, []) if m != model]
    last_err = ""

    for m in chain:
        for attempt in range(2):
            try:
                resp = client.models.generate_content(model=m, contents=prompt, config=config)
                data = _extract_json((resp.text or "").strip())
                if data is not None:
                    result.update(ok=True, data=data, model=m,
                                  latency_ms=int((time.time() - started) * 1000))
                    return result
                last_err = "unparseable_json"
            except Exception as e:
                last_err = str(e)[:200]
                # Match the status code, not a bare "404" substring: quota
                # errors embed billing URLs and ids that can contain those
                # digits, which made a credit-exhaustion error report itself
                # as "model retired".
                if "NOT_FOUND" in last_err or re.search(r"'code':\s*404|\"code\":\s*404", last_err):
                    print(f"[AGENT] {role}/{m} not available — skipping to fallback")
                    break
                if "RESOURCE_EXHAUSTED" in last_err and "credits are depleted" in last_err:
                    print(f"[AGENT] Gemini credits depleted — no model can serve this request")
                    break
                if any(s in last_err for s in ("503", "UNAVAILABLE", "overloaded", "429", "RESOURCE_EXHAUSTED")):
                    if attempt == 0:
                        time.sleep(1.5)
                        continue
                print(f"[AGENT] {role}/{m} failed (try {attempt + 1}): {last_err[:160]}")
                break

    result["error"] = last_err or "exhausted"
    result["latency_ms"] = int((time.time() - started) * 1000)
    return result


# ── Key health ────────────────────────────────────────────
_health_cache: Dict[str, Any] = {"checked": False, "ok": False, "error": ""}


def gemini_healthy(force: bool = False) -> Dict[str, Any]:
    """Whether the configured key is actually accepted by Google.

    `gemini_available()` only reports that a key string exists. A revoked key
    is present but rejected, which would otherwise surface as every agent
    silently falling back. Probed once per process.
    """
    if _health_cache["checked"] and not force:
        return dict(_health_cache)

    _health_cache["checked"] = True
    key = get_api_key()
    if not key:
        _health_cache.update(ok=False, error="No GEMINI_API_KEY configured")
        return dict(_health_cache)

    try:
        import requests
        # Probe generateContent, not the models list. Listing models consumes
        # no credits and returns 200 even when generation is blocked, so the
        # old probe reported "ready" while every agent call was failing.
        r = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent",
            params={"key": key},
            json={"contents": [{"parts": [{"text": "ping"}]}],
                  "generationConfig": {"maxOutputTokens": 1}},
            timeout=12)
        if r.status_code == 200:
            _health_cache.update(ok=True, error="")
        else:
            msg = ""
            try:
                msg = r.json().get("error", {}).get("message", "")
            except Exception:
                msg = r.text[:120]
            if "credits are depleted" in msg or "spending cap" in msg:
                friendly = ("The Gemini account has no remaining credit. "
                            "Top up at ai.studio/projects to resume assessments.")
            elif "API key not valid" in msg:
                friendly = "The configured Gemini API key is not valid."
            else:
                friendly = f"AI provider unavailable (HTTP {r.status_code}). {msg[:120]}"
            _health_cache.update(ok=False, error=friendly)
    except Exception as e:
        _health_cache.update(ok=False, error=f"probe failed: {str(e)[:120]}")
    return dict(_health_cache)
