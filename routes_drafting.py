"""Draft desk API: memory, compose, fill, rewrite and the pre-export check.

Mounted under /api/drafting so it never collides with the older
/api/draft/* routes in main.py. Every AI failure comes back as
{"error": true, "kind": ..., "message": ...} with a status the desk can act
on (503 no key, 429 quota, 502/504 provider trouble), never as an empty
success.
"""
import os

from flask import Blueprint, jsonify, request

import draft_assist
import draft_check
import draft_memory
import draft_requirements
from draft_ai import AIError

_STATUS = {"no_key": 503, "invalid_key": 503, "quota": 429, "overloaded": 503, "timeout": 504,
           "blocked": 422, "bad_output": 502, "unavailable": 502}
MAX_UPLOAD = 15 * 1024 * 1024


def _gemini_key():
    from database import get_setting
    key = (get_setting("gemini_api_key", "") or "").strip()
    return key or (os.environ.get("GEMINI_API_KEY", "") or "").strip()


def _ai_error(e: AIError):
    return jsonify({"error": True, **e.to_dict()}), _STATUS.get(e.kind, 502)


def _bad(msg, code=400):
    return jsonify({"error": True, "kind": "invalid", "message": msg}), code


def create_blueprint(login_required, current_user_id, current_user):
    bp = Blueprint("drafting", __name__)
    draft_memory.init_memory_db()

    def _who():
        return current_user_id(), ((current_user() or {}).get("email") or "")

    def _draft_id(d=None):
        raw = (d or {}).get("draft_id") if d is not None else request.args.get("draft_id")
        try:
            return int(raw) if raw not in (None, "", "null") else None
        except (TypeError, ValueError):
            return None

    def _owner_for_read(d=None):
        uid, _ = _who()
        return draft_memory.memory_owner(uid, _draft_id(d))

    def _owner_for_write(d):
        """Teaching changes the memory the draft is written from, so only the
        owner or an editor of a shared draft may do it."""
        uid, email = _who()
        owner = draft_memory.memory_owner(uid, _draft_id(d))
        if owner != uid:
            from drafts_store import get_effective_role
            if get_effective_role(_draft_id(d), uid) not in ("owner", "editor"):
                return None, email
        return owner, email

    def _symbol(d):
        return ((d or {}).get("symbol") or "").strip().upper()[:20]

    # ── Memory ───────────────────────────────────────────

    @bp.route("/api/drafting/memory")
    @login_required
    def memory_get():
        symbol = (request.args.get("symbol") or "").strip().upper()[:20]
        return jsonify({"memory": draft_memory.get_memory(_owner_for_read(), symbol)})

    @bp.route("/api/drafting/memory/instructions", methods=["POST"])
    @login_required
    def memory_instructions():
        d = request.get_json(silent=True) or {}
        owner, email = _owner_for_write(d)
        if not owner:
            return _bad("Only the draft's owner or an editor can change its memory.", 403)
        scope_symbol = "" if d.get("scope") == "team" else _symbol(d)
        if d.get("scope") != "team" and not scope_symbol:
            return _bad("Choose a company first.")
        if d.get("append"):
            text = draft_memory.add_instruction(owner, scope_symbol, d["append"], by=email)
        else:
            text = draft_memory.set_instructions(owner, scope_symbol, d.get("text") or "", by=email)
        return jsonify({"status": "ok", "text": text,
                        "memory": draft_memory.get_memory(owner, _symbol(d))})

    @bp.route("/api/drafting/memory/facts", methods=["POST"])
    @login_required
    def memory_facts():
        d = request.get_json(silent=True) or {}
        owner, email = _owner_for_write(d)
        if not owner:
            return _bad("Only the draft's owner or an editor can change its memory.", 403)
        if not _symbol(d):
            return _bad("Choose a company first.")
        facts = d.get("facts") or {}
        if not isinstance(facts, dict):
            return _bad("facts must be an object.")
        draft_memory.save_facts(owner, _symbol(d), facts, by=email)
        return jsonify({"status": "ok", "memory": draft_memory.get_memory(owner, _symbol(d))})

    @bp.route("/api/drafting/memory/docs", methods=["POST"])
    @login_required
    def memory_doc_add():
        if request.files.get("file"):
            form = request.form
            d = {k: form.get(k) for k in ("symbol", "filing_type", "title", "draft_id", "scope")}
            f = request.files["file"]
            data = f.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                return _bad("That file is over 15 MB.")
            try:
                text = draft_memory.extract_upload_text(f.filename or "", data)
            except ValueError as e:
                return _bad(str(e))
            except Exception:  # noqa: BLE001
                return _bad("That file could not be read. Try a PDF, Word (.docx) or text file.")
            source, filename, title = "upload", f.filename or "", d.get("title") or f.filename or "Reference"
        else:
            d = request.get_json(silent=True) or {}
            text, source, filename = d.get("text") or "", "paste", ""
            title = d.get("title") or "Pasted reference"
        owner, email = _owner_for_write(d)
        if not owner:
            return _bad("Only the draft's owner or an editor can change its memory.", 403)
        symbol = "" if d.get("scope") == "team" else _symbol(d)
        try:
            doc_id = draft_memory.add_doc(owner, symbol, title, text, source,
                                          filing_type=(d.get("filing_type") or "")[:60],
                                          filename=filename, by=email)
        except ValueError as e:
            return _bad(str(e))
        return jsonify({"status": "ok", "id": doc_id, "chars": len(text),
                        "memory": draft_memory.get_memory(owner, _symbol(d))})

    @bp.route("/api/drafting/memory/docs/filing", methods=["POST"])
    @login_required
    def memory_doc_from_filing():
        d = request.get_json(silent=True) or {}
        owner, email = _owner_for_write(d)
        if not owner:
            return _bad("Only the draft's owner or an editor can change its memory.", 403)
        from database import get_filing_by_id
        try:
            filing = get_filing_by_id(int(d.get("filing_id")))
        except (TypeError, ValueError):
            filing = None
        if not filing or (filing.get("company") or "").upper() != _symbol(d):
            return _bad("That filing was not found for this company.", 404)
        text = filing.get("raw_text") or ""
        if len(text) < 300 and filing.get("pdf_link"):
            from pdf_parser import download_and_extract
            text = download_and_extract(filing["pdf_link"]) or ""
        try:
            doc_id = draft_memory.add_doc(owner, _symbol(d), filing.get("title") or "Filing", text, "filing",
                                          filing_type=(d.get("filing_type") or "")[:60],
                                          ref=str(filing["id"]), by=email)
        except ValueError:
            return _bad("No text could be read from that filing.")
        return jsonify({"status": "ok", "id": doc_id, "memory": draft_memory.get_memory(owner, _symbol(d))})

    @bp.route("/api/drafting/memory/docs/<int:doc_id>", methods=["DELETE"])
    @login_required
    def memory_doc_delete(doc_id):
        d = request.get_json(silent=True) or {"draft_id": request.args.get("draft_id")}
        owner, _ = _owner_for_write(d)
        if not owner or not draft_memory.delete_doc(owner, doc_id):
            return _bad("Document not found.", 404)
        return jsonify({"status": "ok"})

    @bp.route("/api/drafting/memory/learn", methods=["POST"])
    @login_required
    def memory_learn():
        d = request.get_json(silent=True) or {}
        owner, email = _owner_for_write(d)
        if not owner:
            return _bad("Only the draft's owner or an editor can change its memory.", 403)
        symbol, filing_type = _symbol(d), (d.get("filing_type") or "")[:60]
        if not symbol:
            return _bad("Choose a company first.")
        try:
            mem = draft_memory.learn(owner, symbol, filing_type=filing_type,
                                     company_name=d.get("company_name") or "", by=email)
        except ValueError as e:
            return _bad(str(e), 404)
        ai_error, voice = None, None
        if filing_type:
            key = _gemini_key()
            try:
                from draft_engine import find_past_examples
                letters = find_past_examples(symbol, filing_type, max_examples=6)
                letters += [{"text": x["text"], "date": ""} for x in
                            draft_memory.list_docs(owner, symbol, filing_type=filing_type, with_text=True)]
                voice = draft_assist.learn_type_voice(key, owner, symbol, filing_type, letters) if key else None
                if not key:
                    ai_error = AIError("no_key", "House format learned. Add a Gemini key to also learn the "
                                                 "wording of each filing type.").to_dict()
            except AIError as e:
                ai_error = e.to_dict()
            mem = draft_memory.get_memory(owner, symbol)
        return jsonify({"status": "ok", "memory": mem, "type_voice": voice, "ai_error": ai_error})

    @bp.route("/api/drafting/requirements/<filing_type>")
    @login_required
    def requirements(filing_type):
        return jsonify(draft_requirements.for_type(filing_type[:60]))

    # ── AI work ──────────────────────────────────────────

    @bp.route("/api/drafting/compose", methods=["POST"])
    @login_required
    def compose():
        d = request.get_json(silent=True) or {}
        symbol, filing_type = _symbol(d), (d.get("filing_type") or "")[:60]
        brief = (d.get("brief") or "").strip()[:8000]
        company_name = (d.get("company_name") or symbol).strip()[:200]
        if not filing_type or not brief:
            return _bad("Choose a filing type and describe what happened.")
        owner = _owner_for_read(d)
        try:
            from draft_engine import find_past_examples
            examples = find_past_examples(symbol, filing_type, max_examples=3) if symbol else []
        except Exception:  # noqa: BLE001
            examples = []
        try:
            result = draft_assist.compose(_gemini_key(), owner, symbol, company_name, filing_type, brief,
                                          subject=(d.get("subject") or "").strip()[:300],
                                          past_examples=examples)
        except AIError as e:
            return _ai_error(e)
        uid, _ = _who()
        draft_id = None
        try:
            from drafts_store import save_draft
            draft_id = save_draft(uid, symbol, company_name, filing_type, subject=result["subject"],
                                  body=result["body"], annexures=result["annexures"],
                                  metadata={"composed_with": result["model"]}, new_info=brief)
        except Exception as e:  # noqa: BLE001
            print(f"[DRAFTING] auto-save failed: {e}")
        return jsonify({"status": "ok", "draft_id": draft_id, **result})

    @bp.route("/api/drafting/fill", methods=["POST"])
    @login_required
    def fill():
        d = request.get_json(silent=True) or {}
        fields = [f for f in (d.get("fields") or []) if isinstance(f, dict) and f.get("id") is not None][:120]
        if not fields:
            return _bad("No fields to fill.")
        for f in fields:
            f["id"] = str(f["id"])[:20]
            f["label"] = str(f.get("label") or "")[:200]
            f["context"] = str(f.get("context") or "")[:400]
        out = draft_assist.fill_fields(_gemini_key() or None, _owner_for_read(d), _symbol(d),
                                       (d.get("company_name") or "")[:200], (d.get("filing_type") or "")[:60],
                                       (d.get("brief") or "")[:8000], (d.get("document") or "")[:30000], fields)
        return jsonify({"status": "ok", **out})

    @bp.route("/api/drafting/rewrite", methods=["POST"])
    @login_required
    def rewrite():
        d = request.get_json(silent=True) or {}
        text = (d.get("text") or "").strip()
        if not text:
            return _bad("Nothing to rewrite.")
        mode = d.get("mode") or "polish"
        if mode == "custom" and not (d.get("instruction") or "").strip():
            return _bad("Say what to change.")
        try:
            out = draft_assist.rewrite(_gemini_key(), _owner_for_read(d), _symbol(d),
                                       (d.get("filing_type") or "")[:60], text[:30000], mode,
                                       (d.get("instruction") or "")[:1000])
        except AIError as e:
            return _ai_error(e)
        return jsonify({"status": "ok", **out})

    @bp.route("/api/drafting/check", methods=["POST"])
    @login_required
    def check():
        d = request.get_json(silent=True) or {}
        payload = {k: d.get(k) for k in ("symbol", "filing_type", "company_name", "registered_office", "cin",
                                          "bse_code", "nse_symbol", "signer_name", "signer_designation",
                                          "subject", "body", "new_info")}
        payload = {k: (str(v)[:30000] if v is not None else "") for k, v in payload.items()}
        payload["annexures"] = [{"title": str(a.get("title") or "")[:300], "body": str(a.get("body") or "")[:30000]}
                                for a in (d.get("annexures") or []) if isinstance(a, dict)][:12]
        payload["symbol"] = (payload.get("symbol") or payload.get("nse_symbol") or "").upper()
        report = draft_check.run_check(payload, _owner_for_read(d), api_key=_gemini_key() or None,
                                       use_ai=bool(d.get("use_ai", True)))
        return jsonify({"status": "ok", "report": report})

    return bp
