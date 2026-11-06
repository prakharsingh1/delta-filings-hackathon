"""Gemini access for the draft desk.

One place that knows which models this key can use, how long to wait, when
to move on to another model, and what to tell the Company Secretary when
nothing works. The older helpers in draft_engine.py retried a fixed list that
included a retired model, had no request timeout (a single call could hang
for minutes) and returned None for every failure, so the desk could only say
"AI couldn't fill any field". Every failure here becomes an AIError with a
kind and a sentence a person can act on.

Free-tier quotas are per model per day, so a chain of several current Flash
models keeps drafting alive long after the first one is exhausted.
"""
import hashlib
import json
import os
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional

# Best first. Filtered against the models this key can actually call, so a
# retired name is skipped instead of costing a round trip and a 404.
PREFERRED_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
]
# Models known to accept thinking_budget=0, which makes short structured
# calls (fill, check) noticeably faster. Others reject it with a 400.
_ZERO_THINKING_OK = ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.6-flash")

_MODELS_TTL = 6 * 3600

_lock = threading.Lock()
_model_cache: Dict[str, Dict[str, Any]] = {}      # key hash -> {"names": [...], "at": ts}
_cooldown: Dict[tuple, float] = {}                 # (key hash, model) -> unix time it may be retried
_no_thinking: set = set()                          # models that rejected a thinking config


class AIError(Exception):
    """A failure the desk can explain. `kind` is one of: no_key, invalid_key,
    quota, overloaded, timeout, blocked, bad_output, unavailable."""

    def __init__(self, kind: str, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "retry_after": self.retry_after}


def _key_hash(api_key: str) -> str:
    return hashlib.sha256((api_key or "").encode()).hexdigest()[:16]


def _client(api_key: str, timeout_s: float):
    from google import genai
    from google.genai import types
    return genai.Client(api_key=api_key,
                        http_options=types.HttpOptions(timeout=int(max(timeout_s, 3) * 1000)))


def available_models(api_key: str) -> List[str]:
    """Names this key can call generateContent on, cached for six hours.
    On any failure the preferred list is returned unfiltered."""
    kh = _key_hash(api_key)
    with _lock:
        hit = _model_cache.get(kh)
        if hit and time.time() - hit["at"] < _MODELS_TTL:
            return hit["names"]
    names: List[str] = []
    try:
        client = _client(api_key, 10)          # keep a reference: a closed client cannot send
        for m in client.models.list():
            acts = getattr(m, "supported_actions", None) or []
            if "generateContent" in acts:
                names.append(m.name.replace("models/", ""))
    except Exception as e:  # noqa: BLE001
        print(f"[DRAFT-AI] model list failed: {str(e)[:120]}")
        return list(PREFERRED_MODELS)
    with _lock:
        _model_cache[kh] = {"names": names, "at": time.time()}
    return names


def model_chain(api_key: str) -> List[str]:
    """The models to try, best first, minus any cooling down after a quota
    error. DELTA_DRAFT_MODELS (comma-separated) overrides the preference."""
    override = [m.strip() for m in os.environ.get("DELTA_DRAFT_MODELS", "").split(",") if m.strip()]
    preferred = override or PREFERRED_MODELS
    have = set(available_models(api_key))
    chain = [m for m in preferred if m in have] or list(preferred)
    kh, now = _key_hash(api_key), time.time()
    with _lock:
        ready = [m for m in chain if _cooldown.get((kh, m), 0) <= now]
    return ready


def _cool(api_key: str, model: str, seconds: float):
    with _lock:
        _cooldown[(_key_hash(api_key), model)] = time.time() + max(seconds, 1)


def soonest_retry(api_key: str) -> Optional[int]:
    """Seconds until the first cooled-down model is usable again."""
    kh, now = _key_hash(api_key), time.time()
    with _lock:
        waits = [until - now for (k, _m), until in _cooldown.items() if k == kh and until > now]
    return int(min(waits)) + 1 if waits else None


def _retry_delay(msg: str) -> Optional[float]:
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", msg)
    return float(m.group(1)) if m else None


def _status(msg: str) -> int:
    m = re.match(r"\s*(\d{3})\b", msg)
    return int(m.group(1)) if m else 0


def _parse_json(text: str) -> Optional[Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        return None
    for end in range(len(text), start, -1):
        if text[end - 1] in "}]":
            try:
                return json.loads(text[start:end])
            except Exception:  # noqa: BLE001
                continue
    return None


def _human_hours(seconds: Optional[int]) -> str:
    if not seconds:
        return "later today"
    if seconds < 90:
        return "in about a minute"
    if seconds < 3600:
        return f"in about {round(seconds / 60)} minutes"
    return f"in about {round(seconds / 3600)} hours"


def generate(api_key: str, prompt: str, *, schema: Optional[Dict] = None,
             system: str = "", max_output_tokens: int = 8192,
             temperature: float = 0.25, deadline_s: float = 75,
             call_timeout_s: float = 45, fast: bool = False,
             purpose: str = "draft") -> Dict[str, Any]:
    """Run one generation across the model chain.

    Returns {"text", "data" (parsed JSON when `schema` is given), "model",
    "seconds"}. Raises AIError when no model produced a usable answer within
    `deadline_s`. `fast=True` turns thinking off where the model allows it.
    """
    if not (api_key or "").strip():
        raise AIError("no_key", "No Gemini API key is configured. Add GEMINI_API_KEY on the "
                                "server, or a key under Settings.")
    started = time.monotonic()
    chain = model_chain(api_key)
    if not chain:
        wait = soonest_retry(api_key)
        raise AIError("quota", "Every Gemini model Delta uses has hit this key's free-tier quota. "
                               f"It resets {_human_hours(wait)}; a paid key under Settings removes the limit.",
                      retry_after=wait)

    seen: List[str] = []   # error kinds, for the final message

    for model in chain:
        tokens = max_output_tokens
        attempt = 0
        while attempt < 3:
            remaining = deadline_s - (time.monotonic() - started)
            if remaining < 4:
                break
            config: Dict[str, Any] = {"max_output_tokens": tokens, "temperature": temperature}
            if system:
                config["system_instruction"] = system
            if schema:
                config["response_mime_type"] = "application/json"
                config["response_json_schema"] = schema
            use_thinking_off = fast and model.startswith(_ZERO_THINKING_OK) and model not in _no_thinking
            if use_thinking_off:
                config["thinking_config"] = {"thinking_budget": 0}
            try:
                client = _client(api_key, min(call_timeout_s, remaining))
                resp = client.models.generate_content(model=model, contents=prompt, config=config)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                code = _status(msg)
                low = msg.lower()
                if code in (401, 403) or "api key not valid" in low or "api_key_invalid" in low:
                    raise AIError("invalid_key", "The Gemini API key was rejected. Check it under Settings "
                                                 "or in GEMINI_API_KEY.")
                if code == 400 and use_thinking_off:
                    _no_thinking.add(model)          # retry the same model without it
                    continue
                if code == 429 or "resource_exhausted" in low:
                    delay = _retry_delay(msg) or 60
                    per_day = "perday" in low.replace("_", "")
                    if not per_day and delay <= min(8, remaining - 6) and attempt == 0:
                        time.sleep(delay)
                        attempt += 1
                        continue
                    _cool(api_key, model, delay if delay > 1 else 60)
                    seen.append("quota")
                    print(f"[DRAFT-AI] {purpose}: {model} quota ({'day' if per_day else 'minute'}), next model")
                    break
                if code == 404 or "not_found" in low:
                    _cool(api_key, model, 24 * 3600)
                    seen.append("unavailable")
                    break
                timeout = ("timed out" in low or "timeout" in low or "deadline" in low or code == 504)
                if code in (500, 502, 503) or timeout:
                    seen.append("timeout" if timeout else "overloaded")
                    attempt += 1
                    if attempt < 2 and remaining > 12:
                        time.sleep(1.2 * attempt + random.random())
                        continue
                    print(f"[DRAFT-AI] {purpose}: {model} {code or 'timeout'}, next model")
                    break
                seen.append("unavailable")
                print(f"[DRAFT-AI] {purpose}: {model} failed: {msg[:160]}")
                break

            text = ""
            try:
                text = (resp.text or "").strip()
            except Exception:  # noqa: BLE001
                text = ""
            if not text:
                finish = ""
                try:
                    finish = str(resp.candidates[0].finish_reason or "")
                except Exception:  # noqa: BLE001
                    pass
                if "MAX_TOKENS" in finish and attempt == 0:
                    tokens = min(tokens * 2, 32768)
                    attempt += 1
                    continue
                if any(k in finish for k in ("SAFETY", "BLOCK", "PROHIBITED")):
                    raise AIError("blocked", "Gemini declined to answer this request. Rephrase the brief "
                                             "or remove unusual content and try again.")
                seen.append("bad_output")
                break
            data = None
            if schema:
                data = _parse_json(text)
                if data is None:
                    seen.append("bad_output")
                    print(f"[DRAFT-AI] {purpose}: {model} returned unparseable JSON, next model")
                    break
            return {"text": text, "data": data, "model": model,
                    "seconds": round(time.monotonic() - started, 1)}

    if seen and all(k == "quota" for k in seen):
        wait = soonest_retry(api_key)
        raise AIError("quota", "This Gemini key has used its free-tier quota on every model Delta tried. "
                               f"It resets {_human_hours(wait)}; a paid key under Settings removes the limit.",
                      retry_after=wait)
    if "timeout" in seen or "overloaded" in seen:
        raise AIError("overloaded", "Gemini is overloaded or slow right now. Nothing was changed; "
                                    "try again in a minute.", retry_after=60)
    if "bad_output" in seen:
        raise AIError("bad_output", "Gemini answered, but not in a form Delta could use. Try again.")
    raise AIError("unavailable", "Gemini could not be reached. Nothing was changed; try again shortly.")
