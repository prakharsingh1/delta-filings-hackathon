"""Model provider layer: routing, privacy guard, wrapper compatibility.

A throwaway HTTP server stands in for Ollama and an OpenAI-compatible
endpoint, so these run offline.
"""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import urlparse

from tests import _util  # noqa: F401  (temp database, outbound network blocked)

import database  # noqa: E402
import llm  # noqa: E402
import requests  # noqa: E402


class _Resp:
    def __init__(self, code, body):
        self.status_code, self.text = code, body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _loopback(method):
    """The suite blocks requests; these tests may only talk to 127.0.0.1."""
    def call(url, json=None, headers=None, timeout=None, **_):
        if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
            raise RuntimeError("non-loopback request in a test")
        data = None if json is None else globals()["json"].dumps(json).encode()
        req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
        if data is not None:
            req.add_header("Content-Type", "application/json")
        t = timeout[1] if isinstance(timeout, tuple) else timeout
        try:
            with urllib.request.urlopen(req, timeout=t or 5) as r:
                return _Resp(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Resp(e.code, e.read())
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise requests.ConnectionError(str(e))
    return call


class _Fake(BaseHTTPRequestHandler):
    calls = []
    reject_json_mode = False
    force_status = 0
    reply_finish = "stop"
    reject_params = ()

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/v1/models":
            return self._send(200, {"data": [{"id": "tiny-model"}, {"id": "draft-14b"}]})
        if self.path == "/api/tags":
            return self._send(200, {"models": [{"name": "llama3.2:latest"}]})
        self._send(404, {"error": "nope"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Fake.calls.append((self.path, body, dict(self.headers)))
        if _Fake.force_status:
            return self._send(_Fake.force_status, {"error": {"message": "busy"}})
        for name in _Fake.reject_params:
            if name in body:
                return self._send(400, {"error": {"message": f"Unsupported parameter: '{name}' is not supported with this model."}})
        if self.path == "/v1/chat/completions":
            if _Fake.reject_json_mode and "response_format" in body:
                return self._send(400, {"error": "response_format.type must be json_schema"})
            prompt = body["messages"][-1]["content"]
            return self._send(200, {"choices": [{"finish_reason": _Fake.reply_finish,
                                                  "message": {"content": "<think>hmm</think>echo:" + prompt[:40]}}]})
        if self.path == "/api/chat":
            return self._send(200, {"message": {"content": '{"ok": true}'}, "done_reason": "stop"})
        self._send(404, {"error": "nope"})


class LLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._patches = [mock.patch.object(requests, "post", _loopback("POST")),
                        mock.patch.object(requests, "get", _loopback("GET"))]
        for p in cls._patches:
            p.start()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls._env = {k: os.environ.get(k) for k in ("GEMINI_API_KEY", "DELTA_DESKTOP")}
        os.environ.pop("DELTA_DESKTOP", None)
        os.environ.pop("GEMINI_API_KEY", None)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for p in cls._patches:
            p.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in llm._SETTING_KEYS:
            database.set_setting(k, "")
        llm.invalidate()

    def setUp(self):
        _Fake.calls.clear()
        _Fake.reject_json_mode = False
        _Fake.force_status = 0
        _Fake.reply_finish = "stop"
        _Fake.reject_params = ()
        llm._no_json_mode.clear()
        llm._param_fixes.clear()
        llm._ollama_no_think.clear()
        for k in llm._SETTING_KEYS:
            database.set_setting(k, "")
        llm.invalidate()

    def _use(self, preset="vllm", **kw):
        values = {"provider": "openai_compat", "preset": preset,
                  "base_url": f"http://127.0.0.1:{self.port}/v1", "model": "tiny-model", "local_only": True}
        values.update(kw)
        return llm.save(values)

    def test_default_is_gemini_on_server(self):
        self.assertEqual(llm.active(refresh=True)["provider"], "gemini")

    def test_desktop_defaults_to_local_ollama_private_only(self):
        os.environ["DELTA_DESKTOP"] = "1"
        try:
            cfg = llm.active(refresh=True)
            self.assertEqual((cfg["provider"], cfg["preset"]), ("openai_compat", "ollama"))
            self.assertTrue(cfg["local_only"])
        finally:
            os.environ.pop("DELTA_DESKTOP", None)
            llm.invalidate()

    def test_private_address_rules(self):
        self.assertTrue(llm.endpoint_is_private("openai_compat", "http://localhost:1234/v1"))
        self.assertTrue(llm.endpoint_is_private("openai_compat", "http://10.2.3.4:8000/v1"))
        self.assertTrue(llm.endpoint_is_private("openai_compat", "http://192.168.1.20:11434/v1"))
        self.assertFalse(llm.endpoint_is_private("openai_compat", "http://8.8.8.8/v1"))
        self.assertFalse(llm.endpoint_is_private("gemini", ""))

    def test_metadata_and_bad_urls_rejected(self):
        for bad in ("http://169.254.169.254/latest", "ftp://x/v1", "not a url"):
            with self.assertRaises(ValueError):
                llm._check_url(bad)

    def test_local_only_refuses_cloud_endpoint_on_save(self):
        with self.assertRaises(ValueError):
            llm.save({"provider": "openai_compat", "preset": "openai", "base_url": "https://8.8.8.8/v1",
                      "model": "x", "local_only": True})

    def test_local_only_blocks_gemini_calls(self):
        database.set_setting("ai_provider", "gemini")
        database.set_setting("ai_local_only", "1")
        llm.invalidate()
        with self.assertRaises(llm.LLMError):
            llm.Client(api_key="k").models.generate_content(model="gemini-2.5-flash", contents="hi")

    def test_wrapper_routes_to_openai_compatible_and_strips_thinking(self):
        self._use()
        resp = llm.Client(api_key="anything").models.generate_content(
            model="gemini-2.5-flash", contents=["Summarise", {"parts": [{"text": "this filing"}]}],
            config={"max_output_tokens": 50, "temperature": 0.1, "http_options": {"timeout": 5000}})
        self.assertTrue(resp.text.startswith("echo:Summarise"))
        self.assertNotIn("<think>", resp.text)
        self.assertEqual(resp.candidates[0].finish_reason, "STOP")
        self.assertIsNone(resp.candidates[0].grounding_metadata)
        path, body, _ = _Fake.calls[-1]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual((body["model"], body["max_tokens"], body["temperature"]), ("tiny-model", 50, 0.1))

    def test_json_mode_falls_back_when_server_rejects_response_format(self):
        self._use()
        _Fake.reject_json_mode = True
        resp = llm.Client(api_key="x").models.generate_content(
            model="m", contents="list the items", config={"response_mime_type": "application/json"})
        self.assertTrue(resp.text.startswith("echo:"))
        self.assertNotIn("response_format", _Fake.calls[-1][1])
        self.assertIn("JSON", _Fake.calls[-1][1]["messages"][-1]["content"])

    def test_ollama_uses_native_api_with_large_context(self):
        self._use(preset="ollama", base_url=f"http://127.0.0.1:{self.port}/v1", model="llama3.2")
        text = llm.complete("x" * 40000, json_mode=True, max_tokens=500)
        self.assertEqual(text, '{"ok": true}')
        path, body, _ = _Fake.calls[-1]
        self.assertEqual(path, "/api/chat")
        self.assertEqual(body["format"], "json")
        self.assertGreaterEqual(body["options"]["num_ctx"], 16384)

    def test_api_key_sent_as_bearer_and_azure_header(self):
        self._use(local_only=False, api_key="sk-test")
        llm.complete("hello")
        headers = {k.lower(): v for k, v in _Fake.calls[-1][2].items()}
        self.assertEqual(headers.get("authorization"), "Bearer sk-test")
        self.assertEqual(headers.get("api-key"), "sk-test")

    def test_key_gate_sentinel_set_and_cleared(self):
        self._use()
        self.assertEqual(os.environ.get("GEMINI_API_KEY"), llm.SENTINEL_KEY)
        database.set_setting("ai_provider", "gemini")
        database.set_setting("ai_local_only", "0")
        llm.invalidate()
        self.assertIsNone(os.environ.get("GEMINI_API_KEY"))

    def test_sentinel_never_reaches_google(self):
        database.set_setting("ai_provider", "gemini")
        llm.invalidate()
        with self.assertRaises(llm.LLMError):
            llm.Client(api_key=llm.SENTINEL_KEY)._real()

    def test_unreachable_server_fails_fast_afterwards(self):
        llm.save({"provider": "openai_compat", "preset": "vllm", "base_url": "http://127.0.0.1:9/v1",
                  "model": "m", "local_only": True})
        with self.assertRaises(llm.LLMError):
            llm.complete("one")
        import time
        t = time.time()
        with self.assertRaises(llm.LLMError):
            llm.complete("two")
        self.assertLess(time.time() - t, 0.2)

    def test_list_models_and_detect(self):
        self._use()
        self.assertEqual(llm.list_models(), ["draft-14b", "tiny-model"])
        self._use(preset="ollama", model="llama3.2")
        self.assertEqual(llm.list_models(), ["llama3.2:latest"])

    def test_install_wraps_google_genai_client(self):
        self.assertTrue(llm.install())
        from google import genai
        self.assertTrue(getattr(genai.Client, "_delta_wrapped", False))
        self._use()
        self.assertTrue(genai.Client(api_key="x").models.generate_content(model="m", contents="hi").text.startswith("echo:"))


    def test_errors_lead_with_status_code_like_google_genai(self):
        self._use()
        _Fake.force_status = 503
        with self.assertRaises(llm.LLMError) as ctx:
            llm.complete("hi")
        self.assertRegex(str(ctx.exception), r"^503 UNAVAILABLE")
        llm._breaker.clear()
        _Fake.force_status = 429
        with self.assertRaises(llm.LLMError) as ctx:
            llm.complete("hi")
        self.assertRegex(str(ctx.exception), r"^429 RESOURCE_EXHAUSTED")

    def test_max_tokens_finish_reason_is_reported(self):
        self._use()
        _Fake.reply_finish = "length"
        resp = llm.Client(api_key="x").models.generate_content(model="m", contents="long")
        self.assertEqual(resp.candidates[0].finish_reason, "MAX_TOKENS")

    def test_json_schema_reaches_openai_compatible_and_ollama(self):
        schema = {"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"]}
        self._use()
        llm.Client(api_key="x", http_options={"timeout": 5000}).models.generate_content(
            model="m", contents="draft it", config={"response_mime_type": "application/json",
                                                    "response_json_schema": schema,
                                                    "system_instruction": "You draft filings.",
                                                    "thinking_config": {"thinking_budget": 0}})
        body = _Fake.calls[-1][1]
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertEqual(body["response_format"]["json_schema"]["schema"], schema)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "You draft filings."})
        self._use(preset="ollama", model="llama3.2")
        llm.Client(api_key="x").models.generate_content(
            model="m", contents="draft it", config={"response_json_schema": schema})
        self.assertEqual(_Fake.calls[-1][1]["format"], schema)

    def test_models_list_raises_so_callers_use_their_fallback(self):
        self._use()
        with self.assertRaises(AttributeError):
            llm.Client(api_key="x").models.list()

    def test_saved_key_stays_with_its_endpoint(self):
        self._use(api_key="sk-secret")
        self._use(model="draft-14b")  # same endpoint, new model: key kept
        self.assertEqual(llm.active(refresh=True)["api_key"], "sk-secret")
        self._use(base_url=f"http://localhost:{self.port}/v1")  # another endpoint, no key given
        self.assertEqual(llm.active(refresh=True)["api_key"], "")
        llm.complete("hello")
        self.assertNotIn("authorization", {k.lower() for k in _Fake.calls[-1][2]})

    def test_model_list_does_not_send_the_key_to_a_new_endpoint(self):
        import flask
        import routes_ai
        self._use(api_key="sk-secret")
        app = flask.Flask(__name__)
        app.register_blueprint(routes_ai.create_blueprint(admin_required=lambda f: f))
        seen = []
        real = llm.list_models
        with mock.patch.object(llm, "list_models", lambda cfg=None, **k: seen.append(cfg["api_key"]) or real(cfg)):
            c = app.test_client()
            c.post("/api/ai/models", json={"provider": "openai_compat", "base_url": f"http://localhost:{self.port}/v1"})
            c.post("/api/ai/models", json={"provider": "openai_compat", "base_url": f"http://127.0.0.1:{self.port}/v1"})
        self.assertEqual(seen, ["", "sk-secret"])

    def test_openrouter_path_keeps_its_return_shape(self):
        import sys
        import types
        self._use()
        fake_ai = types.ModuleType("ai")
        fake_ai._or_call = lambda *a, **k: ("from openrouter", "or-model")
        with mock.patch.dict(sys.modules, {"ai": fake_ai}):
            llm._patch_openrouter_paths()
            text, model = fake_ai._or_call("sk-or-x", "m", "summarise this", 0.2)
        self.assertTrue(text.startswith("echo:summarise this"))
        self.assertEqual(model, "tiny-model")

    def test_adapts_to_servers_that_refuse_max_tokens_and_temperature(self):
        self._use()
        _Fake.reject_params = ("max_tokens", "temperature")
        self.assertTrue(llm.complete("hi").startswith("echo:hi"))
        sent = _Fake.calls[-1][1]
        self.assertNotIn("max_tokens", sent)
        self.assertNotIn("temperature", sent)
        self.assertIn("max_completion_tokens", sent)
        _Fake.calls.clear()
        llm.complete("again")  # remembered: one request, not three
        self.assertEqual(len(_Fake.calls), 1)

    def test_ollama_thinking_off_with_fallback(self):
        self._use(preset="ollama", base_url=f"http://127.0.0.1:{self.port}/v1", model="llama3.2")
        llm.complete("hi")
        self.assertIs(_Fake.calls[-1][1]["think"], False)
        _Fake.reject_params = ("think",)
        _Fake.calls.clear()
        self.assertEqual(llm.complete("hi"), '{"ok": true}')
        self.assertEqual(len(_Fake.calls), 2)
        self.assertNotIn("think", _Fake.calls[-1][1])

    def test_agent_health_asks_the_configured_provider_not_google(self):
        with mock.patch.dict(os.environ):  # its import loads .env; keep that out of other tests
            from agents import client
        self._use()
        client._health_cache.update(checked=False, ok=False, error="")
        with mock.patch.object(requests, "post", side_effect=AssertionError("probed Google")):
            self.assertTrue(client.gemini_healthy(force=True)["ok"])
        llm.save({"provider": "openai_compat", "preset": "vllm",
                                  "base_url": "http://127.0.0.1:9/v1", "model": "m", "local_only": True})
        status = client.gemini_healthy(force=True)
        self.assertFalse(status["ok"])
        self.assertIn("not answering", status["error"])

    def test_saving_settings_clears_draft_cooldowns(self):
        import draft_ai
        draft_ai._cooldown[("k", "gemini-2.5-flash")] = 9e12
        self._use()
        self.assertEqual(draft_ai._cooldown, {})


if __name__ == "__main__":
    unittest.main()
