"""AI provider settings: where Delta Filings sends AI requests.

Page:  /settings/ai
API:   GET  /api/ai/config     current provider (API key never returned)
       POST /api/ai/config     save provider, endpoint, model, key, privacy mode
       POST /api/ai/test       one short request to the configured provider
       POST /api/ai/models     models available at an endpoint
       GET  /api/ai/detect     local runtimes answering on this computer

Operator-only on the hosted app (DELTA_ADMIN_EMAILS). In the desktop app the
local user is the operator.
"""
from functools import wraps

from flask import Blueprint, jsonify, render_template, request, session

import llm


def create_blueprint(admin_required=None):
    bp = Blueprint("ai_settings", __name__)

    def guard(f):
        if admin_required is not None:
            return admin_required(f)

        @wraps(f)
        def wrapper(*a, **k):
            if not llm.desktop_mode():
                return jsonify({"error": "Not found"}), 404
            return f(*a, **k)
        return wrapper

    def json_only(f):
        @wraps(f)
        def wrapper(*a, **k):
            if not request.is_json:
                return jsonify({"error": "JSON body required"}), 415
            return f(*a, **k)
        return wrapper

    @bp.route("/settings/ai")
    @guard
    def ai_settings_page():
        try:
            from database import get_watchlist
            watchlist = get_watchlist(session.get("user_id"))
        except Exception:
            watchlist = []
        return render_template("ai_settings.html", active_page="ai_settings",
                               watchlist=watchlist, cfg=llm.public_config())

    @bp.route("/api/ai/config", methods=["GET"])
    @guard
    def ai_config_get():
        return jsonify(llm.public_config())

    @bp.route("/api/ai/config", methods=["POST"])
    @guard
    @json_only
    def ai_config_save():
        try:
            return jsonify({"ok": True, "config": llm.save(request.get_json(silent=True) or {})})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @bp.route("/api/ai/test", methods=["POST"])
    @guard
    @json_only
    def ai_test():
        return jsonify(llm.test_connection())

    @bp.route("/api/ai/models", methods=["POST"])
    @guard
    @json_only
    def ai_models():
        data = request.get_json(silent=True) or {}
        cfg = dict(llm.active(refresh=True))
        provider = data.get("provider") or cfg["provider"]
        if provider not in llm.PROVIDERS:
            return jsonify({"error": "Unknown provider"}), 400
        base_url = (data.get("base_url") or cfg["base_url"] or "").rstrip("/")
        # The stored key is only for the endpoint it was saved with.
        same_endpoint = (provider, base_url) == (cfg["provider"], (cfg["base_url"] or "").rstrip("/"))
        cfg.update(provider=provider, preset=data.get("preset") or cfg["preset"], base_url=base_url,
                   local_only=bool(data.get("local_only", cfg["local_only"])))
        if data.get("api_key"):
            cfg["api_key"] = data["api_key"].strip()
        elif not same_endpoint:
            cfg["api_key"] = ""
        try:
            return jsonify({"models": llm.list_models(cfg)})
        except (ValueError, llm.LLMError) as e:
            return jsonify({"error": str(e), "models": []}), 200

    @bp.route("/api/ai/detect", methods=["GET"])
    @guard
    def ai_detect():
        return jsonify({"found": llm.detect_local()})

    return bp
