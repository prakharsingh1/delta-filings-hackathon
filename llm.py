"""Model provider layer: lets Delta Filings run its AI features on a model the
customer chooses instead of only Google Gemini.

Providers
  gemini         Google Gemini via google-genai (the original behaviour).
  openai_compat  Any OpenAI-compatible endpoint: Ollama, LM Studio, llama.cpp
                 server, Jan, vLLM on this computer or the company network, or
                 a cloud API such as OpenAI, Azure OpenAI or OpenRouter.
  anthropic      Anthropic's Messages API.

How it plugs in
  Every AI feature in the codebase builds `genai.Client(api_key=...)` at call
  time and calls `client.models.generate_content(...)`. `install()` swaps
  `google.genai.Client` for a compatible wrapper, so the configured provider
  serves those calls without editing each module. With the provider left on
  Gemini the wrapper hands every call to the real client unchanged.

Privacy guard
  With `ai_local_only` on, a request may only go to this computer or a private
  network address. Gemini, Anthropic and any public endpoint are refused at
  this single choke point, so no document text can leave the network through
  an AI call.

Configuration lives in the settings table (ai_provider, ai_preset, ai_base_url,
ai_model, ai_api_key, ai_local_only, ai_timeout). DELTA_AI_* environment
variables override it and lock the setting for the operator.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

PROVIDERS = ("gemini", "openai_compat", "anthropic")

# The key the existing call sites check for before they make an AI call. When
# a non-Gemini provider is active and no Gemini key exists, the environment
# carries this placeholder so those checks pass; it is never sent to Google.
SENTINEL_KEY = "delta-provider-configured"

ANTHROPIC_VERSION = "2023-06-01"

# Presets the settings page offers. `local` presets run on this computer.
PRESETS: List[Dict[str, Any]] = [
    {"id": "ollama", "label": "Ollama", "provider": "openai_compat", "local": True,
     "base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2",
     "hint": "Free, runs on this computer. Install from ollama.com, then `ollama pull qwen3:8b`."},
    {"id": "lmstudio", "label": "LM Studio", "provider": "openai_compat", "local": True,
     "base_url": "http://127.0.0.1:1234/v1", "model": "",
     "hint": "Start the local server from LM Studio's Developer tab."},
    {"id": "llamacpp", "label": "llama.cpp server", "provider": "openai_compat", "local": True,
     "base_url": "http://127.0.0.1:8080/v1", "model": "",
     "hint": "Run `llama-server -m model.gguf --port 8080`. Newer builds default to port 9931; Find models checks both."},
    {"id": "jan", "label": "Jan", "provider": "openai_compat", "local": True,
     "base_url": "http://127.0.0.1:1337/v1", "model": "",
     "hint": "Turn on Jan's Local API Server in Settings."},
    {"id": "vllm", "label": "vLLM or company GPU server", "provider": "openai_compat", "local": True,
     "base_url": "http://127.0.0.1:8000/v1", "model": "",
     "hint": "Point this at your organisation's private model server."},
    {"id": "openai", "label": "OpenAI API", "provider": "openai_compat", "local": False,
     "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini",
     "hint": "Cloud. Ask OpenAI for zero data retention before sending confidential text."},
    {"id": "azure", "label": "Azure OpenAI (your tenant)", "provider": "openai_compat", "local": False,
     "base_url": "https://YOUR-RESOURCE.openai.azure.com/openai/v1", "model": "",
     "hint": "Cloud, in your own Azure subscription and region. Use your deployment name as the model."},
    {"id": "openrouter", "label": "OpenRouter", "provider": "openai_compat", "local": False,
     "base_url": "https://openrouter.ai/api/v1", "model": "",
     "hint": "Cloud router to many model vendors."},
    {"id": "anthropic", "label": "Anthropic Claude", "provider": "anthropic", "local": False,
     "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5-5",
     "hint": "Cloud. API data is not used for training by default."},
    {"id": "gemini", "label": "Google Gemini", "provider": "gemini", "local": False,
     "base_url": "", "model": "gemini-2.5-flash",
     "hint": "Cloud. The original Delta Filings provider."},
]
PRESET_BY_ID = {p["id"]: p for p in PRESETS}

_SETTING_KEYS = ("ai_provider", "ai_preset", "ai_base_url", "ai_model", "ai_api_key",
                 "ai_local_only", "ai_timeout")
_ENV_FOR = {"ai_provider": "DELTA_AI_PROVIDER", "ai_preset": "DELTA_AI_PRESET",
            "ai_base_url": "DELTA_AI_BASE_URL", "ai_model": "DELTA_AI_MODEL",
            "ai_api_key": "DELTA_AI_API_KEY", "ai_local_only": "DELTA_AI_LOCAL_ONLY",
            "ai_timeout": "DELTA_AI_TIMEOUT"}

_ORIGINAL_CLIENT = None
_cache: Dict[str, Any] = {"at": 0.0, "cfg": None}
_cache_lock = threading.Lock()
# Local servers queue work anyway; two at a time keeps the app responsive.
_local_slots = threading.BoundedSemaphore(int(os.environ.get("DELTA_AI_LOCAL_CONCURRENCY", "2") or 2))
_breaker: Dict[str, Any] = {}
_BREAKER_SECONDS = 20
_no_json_mode: set = set()


class LLMError(RuntimeError):
    """A provider call failed. The message is safe to show to the user."""


# ── Configuration ─────────────────────────────────────────────────────────
def desktop_mode() -> bool:
    return os.environ.get("DELTA_DESKTOP", "").strip() in ("1", "true", "True")


def _truthy(v: Any) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _read_settings() -> Dict[str, str]:
    try:
        from database import get_setting
        return {k: (get_setting(k, "") or "") for k in _SETTING_KEYS}
    except Exception:
        return {k: "" for k in _SETTING_KEYS}


def _defaults() -> Dict[str, str]:
    if desktop_mode():
        p = PRESET_BY_ID["ollama"]
        return {"ai_provider": "openai_compat", "ai_preset": "ollama", "ai_base_url": p["base_url"],
                "ai_model": p["model"], "ai_local_only": "1", "ai_timeout": "600"}
    return {"ai_provider": "gemini", "ai_preset": "gemini", "ai_base_url": "", "ai_model": "",
            "ai_local_only": "0", "ai_timeout": "180"}


def locked_fields() -> List[str]:
    """Settings fixed by the operator through DELTA_AI_* environment variables."""
    return [k for k, env in _ENV_FOR.items() if os.environ.get(env, "").strip() != ""]


def active(refresh: bool = False) -> Dict[str, Any]:
    """The effective provider configuration: env > settings > defaults."""
    with _cache_lock:
        if not refresh and _cache["cfg"] is not None and time.time() - _cache["at"] < 3:
            return dict(_cache["cfg"])
    stored = _read_settings()
    base = _defaults()
    raw: Dict[str, str] = {}
    for k in _SETTING_KEYS:
        env_v = os.environ.get(_ENV_FOR[k], "").strip()
        raw[k] = env_v or stored.get(k, "") or base.get(k, "")
    provider = raw["ai_provider"] if raw["ai_provider"] in PROVIDERS else "gemini"
    try:
        timeout = max(10, min(1800, int(float(raw["ai_timeout"] or 180))))
    except ValueError:
        timeout = 180
    cfg = {
        "provider": provider,
        "preset": raw["ai_preset"] or provider,
        "base_url": (raw["ai_base_url"] or "").strip().rstrip("/"),
        "model": (raw["ai_model"] or "").strip(),
        "api_key": (raw["ai_api_key"] or "").strip(),
        "local_only": _truthy(raw["ai_local_only"]),
        "timeout": timeout,
    }
    if provider == "anthropic" and not cfg["base_url"]:
        cfg["base_url"] = "https://api.anthropic.com"
    with _cache_lock:
        _cache.update(at=time.time(), cfg=dict(cfg))
    _sync_key_gate(cfg)
    return cfg


def invalidate():
    with _cache_lock:
        _cache.update(at=0.0, cfg=None)
    _breaker.clear()
    active(refresh=True)


def save(values: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and store a configuration from the settings page."""
    from database import set_setting
    current = active(refresh=True)
    locked = set(locked_fields())
    provider = str(values.get("provider") or current["provider"]).strip()
    if provider not in PROVIDERS:
        raise ValueError("Choose Gemini, an OpenAI-compatible endpoint or Anthropic.")
    preset = str(values.get("preset") or "").strip()
    if preset and preset not in PRESET_BY_ID:
        raise ValueError("Unknown preset.")
    base_url = str(values.get("base_url") or "").strip().rstrip("/")
    model = str(values.get("model") or "").strip()[:200]
    local_only = _truthy(values.get("local_only"))
    if provider == "openai_compat":
        if not base_url:
            raise ValueError("Enter the endpoint address, for example http://127.0.0.1:11434/v1.")
        _check_url(base_url)
        if not model:
            raise ValueError("Choose a model.")
    if provider == "anthropic":
        base_url = base_url or "https://api.anthropic.com"
        _check_url(base_url)
        if not model:
            raise ValueError("Choose a Claude model.")
    if local_only and not endpoint_is_private(provider, base_url):
        raise ValueError("Private-network-only is on, so the endpoint must be this computer "
                         "or an address on your organisation's private network.")
    updates = {"ai_provider": provider, "ai_preset": preset or provider, "ai_base_url": base_url,
               "ai_model": model, "ai_local_only": "1" if local_only else "0"}
    if values.get("timeout") not in (None, ""):
        updates["ai_timeout"] = str(int(values["timeout"]))
    for k, v in updates.items():
        if k not in locked:
            set_setting(k, v)
    key = values.get("api_key")
    if isinstance(key, str) and key.strip() and "ai_api_key" not in locked:
        # Gemini keeps using the key the rest of the app already reads.
        set_setting("gemini_api_key" if provider == "gemini" else "ai_api_key", key.strip())
    if values.get("clear_api_key"):
        set_setting("ai_api_key", "")
    invalidate()
    return public_config()


def public_config() -> Dict[str, Any]:
    cfg = active(refresh=True)
    gem_key = ""
    try:
        from database import get_setting
        gem_key = (get_setting("gemini_api_key", "") or "").strip()
    except Exception:
        pass
    env_gem = (os.environ.get("GEMINI_API_KEY", "") or "").strip()
    has_gem = bool(gem_key) or (bool(env_gem) and env_gem != SENTINEL_KEY)
    return {
        "provider": cfg["provider"], "preset": cfg["preset"], "base_url": cfg["base_url"],
        "model": cfg["model"], "local_only": cfg["local_only"], "timeout": cfg["timeout"],
        "has_api_key": bool(cfg["api_key"]) if cfg["provider"] != "gemini" else has_gem,
        "is_private": endpoint_is_private(cfg["provider"], cfg["base_url"]),
        "destination": destination_label(cfg),
        "locked": locked_fields(), "desktop": desktop_mode(), "presets": PRESETS,
    }


def destination_label(cfg: Dict[str, Any]) -> str:
    if cfg["provider"] == "gemini":
        return "Google Gemini (cloud)"
    host = urlparse(cfg["base_url"]).hostname or cfg["base_url"]
    if _is_loopback_host(host):
        return "This computer"
    if endpoint_is_private(cfg["provider"], cfg["base_url"]):
        return f"Your private network ({host})"
    return f"{host} (cloud)"


# ── Network guard ─────────────────────────────────────────────────────────
_BLOCKED_HOSTS = {"metadata.google.internal", "metadata", "169.254.169.254"}


def _is_loopback_host(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host in ("localhost",) or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_url(url: str) -> None:
    """Reject malformed URLs and cloud metadata addresses (SSRF)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("The endpoint must start with http:// or https://.")
    host = p.hostname.lower()
    if host in _BLOCKED_HOSTS:
        raise ValueError("That address is not allowed.")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ValueError("That address is not allowed.")
    except ValueError as e:
        if "not allowed" in str(e):
            raise


def endpoint_is_private(provider: str, base_url: str) -> bool:
    """True when the endpoint resolves only to loopback or private addresses."""
    if provider in ("gemini",):
        return False
    host = urlparse(base_url or "").hostname
    if not host:
        return False
    if _is_loopback_host(host):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addrs = {i[4][0] for i in infos}
    if not addrs:
        return False
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not (ip.is_private or ip.is_loopback) or ip.is_link_local:
            return False
    return True


def _guard(cfg: Dict[str, Any]) -> None:
    if cfg["local_only"] and not endpoint_is_private(cfg["provider"], cfg["base_url"]):
        raise LLMError("AI is limited to this computer and your private network, and the "
                       f"configured endpoint ({destination_label(cfg)}) is outside it. "
                       "Change it in Settings, AI provider.")


# ── Key gate for existing call sites ──────────────────────────────────────
def _sync_key_gate(cfg: Dict[str, Any]) -> None:
    env = os.environ.get("GEMINI_API_KEY", "")
    if cfg["provider"] != "gemini":
        if not env.strip():
            os.environ["GEMINI_API_KEY"] = SENTINEL_KEY
    elif env == SENTINEL_KEY:
        os.environ.pop("GEMINI_API_KEY", None)


# ── Calls ─────────────────────────────────────────────────────────────────
_THINK = re.compile(r"<think>.*?</think>\s*", re.S | re.I)


def _clean(text: str) -> str:
    text = _THINK.sub("", text or "")
    if "<think>" in text and "</think>" not in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def _is_ollama(cfg: Dict[str, Any]) -> bool:
    p = urlparse(cfg["base_url"])
    return cfg["preset"] == "ollama" or p.port == 11434


def _ollama_root(base_url: str) -> str:
    return re.sub(r"/v1/?$", "", base_url.rstrip("/"))


def _breaker_check(cfg: Dict[str, Any]) -> None:
    hit = _breaker.get(cfg["base_url"] or cfg["provider"])
    if hit and time.time() - hit[0] < _BREAKER_SECONDS:
        raise LLMError(hit[1])


def _breaker_trip(cfg: Dict[str, Any], msg: str) -> None:
    _breaker[cfg["base_url"] or cfg["provider"]] = (time.time(), msg)


def _unreachable(cfg: Dict[str, Any], e: Exception) -> LLMError:
    where = destination_label(cfg)
    if isinstance(e, requests.Timeout):
        msg = (f"The model at {where} did not answer within {cfg['timeout']} seconds. "
               "Try a smaller model or raise the time limit in Settings, AI provider.")
    elif _is_ollama(cfg):
        msg = f"Could not reach Ollama at {cfg['base_url']}. Open the Ollama app and try again."
    else:
        msg = f"Could not reach the model at {where} ({cfg['base_url']})."
    _breaker_trip(cfg, msg)
    return LLMError(msg)


def _http_error(cfg: Dict[str, Any], r: requests.Response) -> LLMError:
    try:
        body = r.json()
        detail = body.get("error") or body.get("message") or body
        if isinstance(detail, dict):
            detail = detail.get("message") or json.dumps(detail)[:200]
    except Exception:
        detail = (r.text or "")[:200]
    if r.status_code in (401, 403):
        return LLMError(f"The model service rejected the API key (HTTP {r.status_code}).")
    if r.status_code == 404:
        return LLMError(f"Model or endpoint not found at {cfg['base_url']}: {detail}")
    # Existing callers retry on these markers.
    marker = " 503 UNAVAILABLE" if r.status_code in (502, 503, 504) else (" 429" if r.status_code == 429 else "")
    return LLMError(f"Model service error (HTTP {r.status_code}){marker}: {detail}")


def _post(cfg: Dict[str, Any], url: str, payload: Dict[str, Any], headers: Dict[str, str],
          timeout: float) -> requests.Response:
    try:
        return requests.post(url, json=payload, headers=headers, timeout=(5, timeout))
    except (requests.ConnectionError, requests.Timeout) as e:
        raise _unreachable(cfg, e)


def _openai_compat(cfg, prompt, system, max_tokens, temperature, json_mode, timeout) -> str:
    if _is_ollama(cfg):
        return _ollama_native(cfg, prompt, system, max_tokens, temperature, json_mode, timeout)
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    payload: Dict[str, Any] = {"model": cfg["model"], "messages": msgs, "max_tokens": int(max_tokens),
                               "temperature": float(temperature), "stream": False}
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
        headers["api-key"] = cfg["api_key"]  # Azure OpenAI reads this header
    url = cfg["base_url"] + "/chat/completions"
    want_json = json_mode and cfg["base_url"] not in _no_json_mode
    if want_json:
        payload["response_format"] = {"type": "json_object"}
    r = _post(cfg, url, payload, headers, timeout)
    if r.status_code in (400, 422) and want_json and "response_format" in (r.text or ""):
        # Some servers (LM Studio, older llama.cpp) only take json_schema.
        _no_json_mode.add(cfg["base_url"])
        payload.pop("response_format", None)
        r = _post(cfg, url, payload, headers, timeout)
    if r.status_code != 200:
        raise _http_error(cfg, r)
    data = r.json()
    try:
        msg = data["choices"][0]["message"]
        return _clean(msg.get("content") or "")
    except (KeyError, IndexError, TypeError):
        raise LLMError("The model service returned an unexpected reply.")


def _ollama_native(cfg, prompt, system, max_tokens, temperature, json_mode, timeout) -> str:
    # Ollama's OpenAI endpoint cannot raise the context window, and its
    # default silently truncates long filings. The native API can.
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    approx_tokens = (len(prompt) + len(system or "")) // 3 + int(max_tokens) + 512
    num_ctx = max(8192, min(int(os.environ.get("DELTA_AI_MAX_CONTEXT", "32768")), 1 << (approx_tokens - 1).bit_length()))
    payload: Dict[str, Any] = {"model": cfg["model"], "messages": msgs, "stream": False,
                               "options": {"temperature": float(temperature), "num_predict": int(max_tokens),
                                           "num_ctx": num_ctx}}
    if json_mode:
        payload["format"] = "json"
    r = _post(cfg, _ollama_root(cfg["base_url"]) + "/api/chat", payload,
              {"Content-Type": "application/json"}, timeout)
    if r.status_code != 200:
        raise _http_error(cfg, r)
    data = r.json()
    return _clean(((data.get("message") or {}).get("content")) or "")


def _anthropic(cfg, prompt, system, max_tokens, temperature, json_mode, timeout) -> str:
    if json_mode:
        prompt = prompt + "\n\nRespond with a single JSON object and nothing else."
    payload: Dict[str, Any] = {"model": cfg["model"], "max_tokens": int(max_tokens),
                               "temperature": float(temperature),
                               "messages": [{"role": "user", "content": prompt}]}
    if system:
        payload["system"] = system
    headers = {"Content-Type": "application/json", "x-api-key": cfg["api_key"],
               "anthropic-version": ANTHROPIC_VERSION}
    r = _post(cfg, cfg["base_url"].rstrip("/") + "/v1/messages", payload, headers, timeout)
    if r.status_code != 200:
        raise _http_error(cfg, r)
    data = r.json()
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return _clean("".join(parts))


def complete(prompt: str, *, system: Optional[str] = None, max_tokens: int = 1500,
             temperature: float = 0.3, json_mode: bool = False,
             timeout: Optional[float] = None, cfg: Optional[Dict[str, Any]] = None) -> str:
    """One completion on the configured non-Gemini provider. Raises LLMError."""
    cfg = cfg or active()
    if cfg["provider"] == "gemini":
        raise LLMError("complete() serves non-Gemini providers; Gemini runs through google-genai.")
    _guard(cfg)
    if not cfg["model"]:
        raise LLMError("No model is selected. Choose one in Settings, AI provider.")
    if cfg["provider"] == "anthropic" and not cfg["api_key"]:
        raise LLMError("Anthropic needs an API key. Add it in Settings, AI provider.")
    _breaker_check(cfg)
    timeout = float(timeout or cfg["timeout"])
    local = endpoint_is_private(cfg["provider"], cfg["base_url"])
    if json_mode and "json" not in prompt.lower()[-400:]:
        prompt = prompt + "\n\nReturn valid JSON only."
    fn = _anthropic if cfg["provider"] == "anthropic" else _openai_compat
    if local:
        with _local_slots:
            return fn(cfg, prompt, system, max_tokens, temperature, json_mode, timeout)
    return fn(cfg, prompt, system, max_tokens, temperature, json_mode, timeout)


def list_models(cfg: Optional[Dict[str, Any]] = None, timeout: float = 4) -> List[str]:
    cfg = cfg or active()
    if cfg["provider"] == "gemini":
        return ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"]
    _check_url(cfg["base_url"])
    _guard(cfg)
    try:
        if cfg["provider"] == "anthropic":
            r = requests.get(cfg["base_url"].rstrip("/") + "/v1/models", timeout=timeout,
                             headers={"x-api-key": cfg["api_key"], "anthropic-version": ANTHROPIC_VERSION})
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        if _is_ollama(cfg):
            r = requests.get(_ollama_root(cfg["base_url"]) + "/api/tags", timeout=timeout)
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
        headers = {"Authorization": f"Bearer {cfg['api_key']}", "api-key": cfg["api_key"]} if cfg["api_key"] else {}
        r = requests.get(cfg["base_url"] + "/models", timeout=timeout, headers=headers)
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
    except requests.RequestException as e:
        raise LLMError(f"Could not list models at {cfg['base_url']}: {str(e)[:160]}")


def detect_local(timeout: float = 0.6) -> List[Dict[str, Any]]:
    """Probe the local presets; return the ones answering, with their models."""
    found: List[Dict[str, Any]] = []
    own_port = os.environ.get("DELTA_SELF_PORT", "")

    def probe(p):
        if own_port and urlparse(p["base_url"]).port == int(own_port):
            return
        cfg = {"provider": "openai_compat", "preset": p["id"], "base_url": p["base_url"],
               "model": "", "api_key": "", "local_only": False, "timeout": 10}
        try:
            models = list_models(cfg, timeout=timeout)
        except Exception:
            return
        found.append({"preset": p["id"], "label": p["label"], "base_url": p["base_url"], "models": models})

    candidates = [p for p in PRESETS if p["local"]]
    # Newer llama.cpp builds default to port 9931; older guides say 8080.
    candidates.append(dict(PRESET_BY_ID["llamacpp"], base_url="http://127.0.0.1:9931/v1"))
    threads = [threading.Thread(target=probe, args=(p,), daemon=True) for p in candidates]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 1)
    order = [p["id"] for p in PRESETS]
    return sorted(found, key=lambda f: order.index(f["preset"]))


def test_connection() -> Dict[str, Any]:
    cfg = active(refresh=True)
    started = time.time()
    try:
        if cfg["provider"] == "gemini":
            if cfg["local_only"]:
                _guard(cfg)
            from google import genai
            client = genai.Client(api_key=_gemini_key())
            resp = client.models.generate_content(model=cfg["model"] or "gemini-2.5-flash",
                                                  contents="Reply with the single word OK.",
                                                  config={"max_output_tokens": 10})
            reply = (resp.text or "").strip()
        else:
            _breaker.pop(cfg["base_url"] or cfg["provider"], None)
            reply = complete("Reply with the single word OK.", max_tokens=16, temperature=0, timeout=min(cfg["timeout"], 180))
        return {"ok": True, "reply": reply[:80], "latency_ms": int((time.time() - started) * 1000),
                "destination": destination_label(cfg), "model": cfg["model"]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "destination": destination_label(cfg)}


def _gemini_key() -> str:
    try:
        from database import get_setting
        key = (get_setting("gemini_api_key", "") or "").strip()
    except Exception:
        key = ""
    env = (os.environ.get("GEMINI_API_KEY", "") or "").strip()
    return key or (env if env != SENTINEL_KEY else "")


# ── google-genai compatible wrapper ───────────────────────────────────────
def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _flatten(contents: Any) -> str:
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, (list, tuple)):
        return "\n\n".join(t for t in (_flatten(c) for c in contents) if t)
    text = _get(contents, "text")
    if isinstance(text, str) and text:
        return text
    parts = _get(contents, "parts")
    if parts:
        return _flatten(list(parts))
    return ""


class _Response:
    """Enough of google.genai's GenerateContentResponse for the call sites."""

    def __init__(self, text: str):
        self.text = text
        self.candidates: List[Any] = []
        self.usage_metadata = None
        self.prompt_feedback = None
        self.parsed = None


class _Models:
    def __init__(self, owner: "Client"):
        self._owner = owner

    def generate_content(self, model: Any = None, contents: Any = None, config: Any = None, **kwargs):
        cfg = active()
        if cfg["provider"] == "gemini":
            if cfg["local_only"]:
                _guard(cfg)
            return self._owner._real().models.generate_content(model=model, contents=contents,
                                                               config=config, **kwargs)
        http = _get(config, "http_options")
        t_ms = _get(http, "timeout")
        system = _flatten(_get(config, "system_instruction")) or None
        json_mode = str(_get(config, "response_mime_type") or "").lower() == "application/json"
        text = complete(_flatten(contents), system=system,
                        max_tokens=int(_get(config, "max_output_tokens") or 2048),
                        temperature=float(_get(config, "temperature") if _get(config, "temperature") is not None else 0.3),
                        json_mode=json_mode,
                        timeout=max(float(t_ms) / 1000.0, cfg["timeout"]) if t_ms else None,
                        cfg=cfg)
        return _Response(text)

    def __getattr__(self, name: str):
        cfg = active()
        if cfg["provider"] == "gemini":
            return getattr(self._owner._real().models, name)
        raise AttributeError(f"models.{name} is only available with the Gemini provider")


class Client:
    """Drop-in for google.genai.Client that honours the configured provider."""

    _delta_wrapped = True

    def __init__(self, *args: Any, **kwargs: Any):
        self._args, self._kwargs = args, dict(kwargs)
        self._client = None
        self.models = _Models(self)

    def _real(self):
        if self._client is None:
            if self._kwargs.get("api_key") == SENTINEL_KEY:
                real = _gemini_key()
                if not real:
                    raise LLMError("No Gemini API key is configured. Add one in Settings, AI provider.")
                self._kwargs["api_key"] = real
            self._client = _ORIGINAL_CLIENT(*self._args, **self._kwargs)
        return self._client

    def __getattr__(self, name: str):
        return getattr(self._real(), name)


def install() -> bool:
    """Route google.genai.Client through the configured provider. Idempotent."""
    global _ORIGINAL_CLIENT
    try:
        from google import genai
    except Exception:
        return False
    if not getattr(genai.Client, "_delta_wrapped", False):
        _ORIGINAL_CLIENT = genai.Client
        genai.Client = Client
    try:
        active(refresh=True)
    except Exception:
        pass
    return True


def _patch_openrouter_paths() -> None:
    """Direct OpenRouter calls (sk-or- keys) bypass google-genai. When another
    provider is configured they use it instead, so the privacy guard holds."""
    import sys
    ai = sys.modules.get("ai")
    if ai is not None and hasattr(ai, "_or_call") and not getattr(ai._or_call, "_delta", False):
        original = ai._or_call

        def _or_call(api_key, model, prompt_text, temperature, *a, **k):
            if active()["provider"] != "gemini":
                return complete(prompt_text, temperature=temperature, max_tokens=900)
            if active()["local_only"]:
                _guard(active())
            return original(api_key, model, prompt_text, temperature, *a, **k)
        _or_call._delta = True
        ai._or_call = _or_call
    pi = sys.modules.get("price_intel")
    if pi is not None and hasattr(pi, "_openrouter_why") and not getattr(pi._openrouter_why, "_delta", False):
        original_pi = pi._openrouter_why

        def _openrouter_why(prompt, api_key, *a, **k):
            if active()["provider"] != "gemini":
                try:
                    return complete(prompt, max_tokens=1200)
                except LLMError as e:
                    print(f"[AI] {e}")
                    return None
            if active()["local_only"]:
                return None
            return original_pi(prompt, api_key, *a, **k)
        _openrouter_why._delta = True
        pi._openrouter_why = _openrouter_why
    di = sys.modules.get("director_intel")
    if di is not None and pi is not None and hasattr(di, "_openrouter_why"):
        di._openrouter_why = pi._openrouter_why


def register(app, admin_required=None) -> None:
    """Attach the AI provider settings page and API. Call after blueprints."""
    install()
    _patch_openrouter_paths()
    from routes_ai import create_blueprint
    if "ai_settings" not in app.blueprints:
        app.register_blueprint(create_blueprint(admin_required))
