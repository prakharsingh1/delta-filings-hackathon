"""E-signature workflow for compliance documents.

A Company Secretary does not stop at a draft. The letter has to be signed —
by the CS, often by a director, sometimes with a Digital Signature
Certificate because MCA forms will not accept anything else — and then filed.
Delta previously ended at "download as PDF", which is the point the real work
starts.

This module owns the signing workflow and talks to providers through an
adapter, so the same request object works whether it is routed to DocuSign,
to eMudhra (a CCA-licensed Indian CA, which is what DSC-bound MCA filings
actually need), or held locally.

Provider credentials are read from the environment. With none configured the
workflow still runs end to end in `local` mode: the document, signatory
routing and audit trail are all real and stored, and the packet is prepared
for signing — it is simply not transmitted. That state is labelled
explicitly everywhere it surfaces, never dressed up as a completed signature.
"""

import json
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from database import get_connection

# ── Lifecycle ─────────────────────────────────────────────
STATUSES = ("prepared", "sent", "partially_signed", "completed", "declined", "cancelled", "expired")
OPEN_STATUSES = ("prepared", "sent", "partially_signed")

# Signature methods a CS actually uses in India.
METHODS = {
    "aadhaar_esign": "Aadhaar eSign (OTP-based, eMudhra / NSDL gateway)",
    "dsc": "Digital Signature Certificate (Class 3 DSC token — required for MCA e-forms)",
    "electronic": "Electronic signature (DocuSign / click-to-sign)",
    "wet": "Physical signature (printed, signed and scanned)",
}

# Which method a filing destination will actually accept. This is the bit a
# generic e-sign integration gets wrong: an MCA e-form will not accept a
# click-to-sign image, and routing one there wastes a filing window.
DESTINATION_RULES = {
    "mca": {
        "label": "MCA / Registrar of Companies",
        "accepted": ["dsc"],
        "note": "MCA e-forms require a Class 3 DSC registered against the signatory's DIN/PAN. "
                "Electronic click-to-sign is not accepted.",
    },
    "exchange": {
        "label": "Stock exchange (NSE / BSE)",
        "accepted": ["dsc", "aadhaar_esign", "electronic", "wet"],
        "note": "Exchange submissions are made through the listing portal; the letter is "
                "signed by the Company Secretary or Compliance Officer.",
    },
    "internal": {
        "label": "Internal record",
        "accepted": ["aadhaar_esign", "electronic", "wet"],
        "note": "Board notes and internal memoranda — no statutory signing method prescribed.",
    },
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Schema ────────────────────────────────────────────────

def init_esign_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS esign_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            draft_id INTEGER,
            event_id INTEGER,
            company_symbol TEXT DEFAULT '',
            company_name TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            filing_type TEXT DEFAULT '',
            destination TEXT DEFAULT 'exchange',
            method TEXT DEFAULT 'electronic',
            provider TEXT DEFAULT 'local',
            provider_envelope_id TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'prepared',
            document_sha256 TEXT DEFAULT '',
            expires_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_esign_user ON esign_requests(user_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_esign_draft ON esign_requests(draft_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS esign_signatories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            designation TEXT DEFAULT '',
            din TEXT DEFAULT '',
            email TEXT DEFAULT '',
            sign_order INTEGER DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            signed_at TEXT DEFAULT '',
            signature_ref TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_esign_sig ON esign_signatories(request_id, sign_order)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS esign_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


def _log(request_id: int, actor: str, action: str, detail: str = ""):
    conn = get_connection()
    conn.execute("INSERT INTO esign_audit (request_id, ts, actor, action, detail) VALUES (?,?,?,?,?)",
                 (request_id, _now(), actor, action, detail))
    conn.commit()
    conn.close()


# ── Providers ─────────────────────────────────────────────

def available_providers() -> Dict[str, Dict]:
    """Which providers are actually usable, based on configured credentials."""
    return {
        "docusign": {
            "label": "DocuSign",
            "configured": bool(os.environ.get("DOCUSIGN_ACCOUNT_ID")
                               and os.environ.get("DOCUSIGN_ACCESS_TOKEN")),
            "env": ["DOCUSIGN_ACCOUNT_ID", "DOCUSIGN_ACCESS_TOKEN", "DOCUSIGN_BASE_URI"],
            "methods": ["electronic"],
            "note": "Click-to-sign envelopes. Not accepted for MCA e-forms.",
        },
        "emudhra": {
            "label": "eMudhra",
            "configured": bool(os.environ.get("EMUDHRA_API_KEY")),
            "env": ["EMUDHRA_API_KEY", "EMUDHRA_CLIENT_ID", "EMUDHRA_BASE_URL"],
            "methods": ["aadhaar_esign", "dsc"],
            "note": "CCA-licensed Indian CA. Aadhaar eSign and Class 3 DSC — the route "
                    "for MCA e-forms.",
        },
        "local": {
            "label": "Prepare locally",
            "configured": True,
            "env": [],
            "methods": list(METHODS),
            "note": "Builds the signature packet and tracks the workflow inside Delta. "
                    "Nothing is transmitted to a signing service.",
        },
    }


def pick_provider(method: str) -> str:
    """Cheapest correct route for a signature method, given what is configured."""
    providers = available_providers()
    if method in ("dsc", "aadhaar_esign") and providers["emudhra"]["configured"]:
        return "emudhra"
    if method == "electronic" and providers["docusign"]["configured"]:
        return "docusign"
    return "local"


def _send_via_provider(provider: str, request: Dict, signatories: List[Dict]) -> Dict[str, Any]:
    """Hand the packet to the provider. Returns {ok, envelope_id, message}.

    The two live adapters are deliberately thin: they fail loudly when
    credentials are absent rather than pretending to have sent something.
    """
    if provider == "docusign":
        return _send_docusign(request, signatories)
    if provider == "emudhra":
        return _send_emudhra(request, signatories)
    return {
        "ok": True,
        "envelope_id": f"LOCAL-{secrets.token_hex(6).upper()}",
        "message": "Prepared in Delta. Not transmitted — no signing provider is configured.",
        "transmitted": False,
    }


def _send_docusign(request: Dict, signatories: List[Dict]) -> Dict[str, Any]:
    token = os.environ.get("DOCUSIGN_ACCESS_TOKEN", "").strip()
    account = os.environ.get("DOCUSIGN_ACCOUNT_ID", "").strip()
    base = os.environ.get("DOCUSIGN_BASE_URI", "https://demo.docusign.net/restapi").rstrip("/")
    if not (token and account):
        return {"ok": False, "envelope_id": "", "transmitted": False,
                "message": "DocuSign is not configured (DOCUSIGN_ACCESS_TOKEN, DOCUSIGN_ACCOUNT_ID)."}

    import base64
    import requests
    from draft_engine import build_letter_data, render_pdf

    try:
        pdf_bytes = _document_bytes(request)
        envelope = {
            "emailSubject": request.get("subject") or "Document for signature",
            "documents": [{
                "documentBase64": base64.b64encode(pdf_bytes).decode(),
                "name": (request.get("subject") or "document")[:80] + ".pdf",
                "fileExtension": "pdf", "documentId": "1",
            }],
            "recipients": {"signers": [
                {"email": s["email"], "name": s["name"], "recipientId": str(i + 1),
                 "routingOrder": str(s.get("sign_order", i + 1)),
                 "tabs": {"signHereTabs": [
                     {"anchorString": "/sig" + str(i + 1) + "/", "anchorUnits": "pixels",
                      "anchorXOffset": "0", "anchorYOffset": "0"}]}}
                for i, s in enumerate(signatories)]},
            "status": "sent",
        }
        r = requests.post(f"{base}/v2.1/accounts/{account}/envelopes",
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"},
                          json=envelope, timeout=30)
        if r.status_code in (200, 201):
            return {"ok": True, "envelope_id": r.json().get("envelopeId", ""),
                    "transmitted": True, "message": "Envelope sent via DocuSign."}
        return {"ok": False, "envelope_id": "", "transmitted": False,
                "message": f"DocuSign returned HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "envelope_id": "", "transmitted": False,
                "message": f"DocuSign call failed: {str(e)[:200]}"}


def _send_emudhra(request: Dict, signatories: List[Dict]) -> Dict[str, Any]:
    api_key = os.environ.get("EMUDHRA_API_KEY", "").strip()
    base = os.environ.get("EMUDHRA_BASE_URL", "").rstrip("/")
    if not (api_key and base):
        return {"ok": False, "envelope_id": "", "transmitted": False,
                "message": "eMudhra is not configured (EMUDHRA_API_KEY, EMUDHRA_BASE_URL)."}

    import base64
    import requests
    try:
        pdf_bytes = _document_bytes(request)
        payload = {
            "clientId": os.environ.get("EMUDHRA_CLIENT_ID", ""),
            "documentName": (request.get("subject") or "document")[:80],
            "documentContent": base64.b64encode(pdf_bytes).decode(),
            "signatureType": "DSC" if request.get("method") == "dsc" else "AADHAAR_ESIGN",
            "signers": [{"name": s["name"], "email": s["email"],
                         "identifier": s.get("din") or s.get("email"),
                         "order": s.get("sign_order", 1)} for s in signatories],
        }
        r = requests.post(f"{base}/v1/signature-requests",
                          headers={"Authorization": f"Bearer {api_key}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=30)
        if r.status_code in (200, 201):
            d = r.json()
            return {"ok": True, "envelope_id": d.get("requestId") or d.get("id", ""),
                    "transmitted": True, "message": "Signature request created with eMudhra."}
        return {"ok": False, "envelope_id": "", "transmitted": False,
                "message": f"eMudhra returned HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "envelope_id": "", "transmitted": False,
                "message": f"eMudhra call failed: {str(e)[:200]}"}


def _document_bytes(request: Dict) -> bytes:
    """Render the request's draft to PDF using the existing draft engine."""
    from drafts_store import get_draft
    from draft_engine import build_letter_data, render_pdf

    draft = get_draft(request["draft_id"], request["user_id"]) if request.get("draft_id") else None
    if not draft:
        raise ValueError("draft not found for this signature request")

    data = build_letter_data(
        company_name=draft.get("company_name", ""),
        filing_type_id=draft.get("filing_type", ""),
        subject=draft.get("subject", ""),
        body=draft.get("body", ""),
        annexures=draft.get("annexures", []),
    )
    return render_pdf(data)


# ── Workflow ──────────────────────────────────────────────

def create_request(user_id: str, draft: Dict, signatories: List[Dict],
                   destination: str = "exchange", method: str = "electronic",
                   event_id: Optional[int] = None, expires_days: int = 14) -> Dict:
    """Prepare a signature request. Does not transmit — see send()."""
    init_esign_db()
    import hashlib

    if destination not in DESTINATION_RULES:
        destination = "exchange"
    if method not in METHODS:
        method = "electronic"

    reference = f"DS-{datetime.now():%Y%m%d}-{secrets.token_hex(3).upper()}"
    body_hash = hashlib.sha256((draft.get("body") or "").encode()).hexdigest()

    conn = get_connection()
    conn.execute("""
        INSERT INTO esign_requests
            (reference, user_id, draft_id, event_id, company_symbol, company_name,
             subject, filing_type, destination, method, provider, status,
             document_sha256, expires_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'prepared',?,?,?,?)
    """, (reference, user_id, draft.get("id"), event_id,
          draft.get("company_symbol", ""), draft.get("company_name", ""),
          draft.get("subject", ""), draft.get("filing_type", ""),
          destination, method, pick_provider(method), body_hash,
          (datetime.now() + timedelta(days=expires_days)).date().isoformat(),
          _now(), _now()))
    rid = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

    for i, s in enumerate(signatories):
        conn.execute("""INSERT INTO esign_signatories
                        (request_id, name, designation, din, email, sign_order, status)
                        VALUES (?,?,?,?,?,?, 'pending')""",
                     (rid, s.get("name", ""), s.get("designation", ""), s.get("din", ""),
                      s.get("email", ""), s.get("sign_order", i + 1)))
    conn.commit()
    conn.close()

    _log(rid, user_id, "prepared",
         f"{len(signatories)} signatory(ies), {METHODS[method]}, "
         f"routed to {DESTINATION_RULES[destination]['label']}")
    return get_request(rid, user_id)


def validate(request: Dict) -> List[Dict]:
    """Checks that stop a request going out wrong. Returns blocking issues
    and warnings — the CS decides, but not blindly."""
    issues = []
    rule = DESTINATION_RULES.get(request.get("destination"), {})
    method = request.get("method")

    if method and rule and method not in rule.get("accepted", []):
        issues.append({
            "level": "blocking",
            "message": f"{METHODS.get(method, method)} is not accepted for "
                       f"{rule.get('label')}. {rule.get('note', '')}",
        })

    sigs = request.get("signatories") or []
    if not sigs:
        issues.append({"level": "blocking", "message": "No signatories added."})
    for s in sigs:
        if not s.get("email"):
            issues.append({"level": "blocking",
                           "message": f"{s.get('name') or 'A signatory'} has no email address."})
        if method == "dsc" and not s.get("din"):
            issues.append({"level": "warning",
                           "message": f"{s.get('name')} has no DIN recorded. A Class 3 DSC for an "
                                      f"MCA filing is registered against the signatory's DIN."})

    provider = request.get("provider")
    if provider == "local":
        issues.append({
            "level": "warning",
            "message": "No signing provider is configured, so this will be prepared inside "
                       "Delta and not transmitted. Configure DocuSign or eMudhra to send.",
        })
    return issues


def send(request_id: int, user_id: str, actor: str = "") -> Dict:
    req = get_request(request_id, user_id)
    if not req:
        return {"ok": False, "message": "request not found"}
    if req["status"] not in ("prepared",):
        return {"ok": False, "message": f"request is already {req['status']}"}

    blocking = [i for i in validate(req) if i["level"] == "blocking"]
    if blocking:
        return {"ok": False, "message": blocking[0]["message"], "issues": blocking}

    result = _send_via_provider(req["provider"], req, req["signatories"])

    conn = get_connection()
    conn.execute("UPDATE esign_requests SET status=?, provider_envelope_id=?, updated_at=? WHERE id=?",
                 ("sent" if result["ok"] else "prepared",
                  result.get("envelope_id", ""), _now(), request_id))
    conn.commit()
    conn.close()

    _log(request_id, actor or user_id,
         "sent" if result["ok"] else "send_failed", result.get("message", ""))
    return {**result, "request": get_request(request_id, user_id)}


def record_signature(request_id: int, signatory_id: int, user_id: str,
                     signature_ref: str = "", actor: str = "") -> Dict:
    """Mark one signatory as signed and advance the request."""
    conn = get_connection()
    conn.execute("""UPDATE esign_signatories SET status='signed', signed_at=?, signature_ref=?
                    WHERE id=? AND request_id=?""",
                 (_now(), signature_ref or f"SIG-{secrets.token_hex(4).upper()}",
                  signatory_id, request_id))
    rows = conn.execute("SELECT status FROM esign_signatories WHERE request_id=?",
                        (request_id,)).fetchall()
    signed = sum(1 for r in rows if r["status"] == "signed")
    status = "completed" if signed == len(rows) else "partially_signed"
    conn.execute("UPDATE esign_requests SET status=?, updated_at=? WHERE id=?",
                 (status, _now(), request_id))
    conn.commit()
    conn.close()
    _log(request_id, actor or user_id, f"signature:{status}", f"{signed}/{len(rows)} signed")
    return get_request(request_id, user_id)


def _hydrate(row) -> Dict:
    r = dict(row)
    conn = get_connection()
    sigs = conn.execute("SELECT * FROM esign_signatories WHERE request_id=? ORDER BY sign_order, id",
                        (r["id"],)).fetchall()
    audit = conn.execute("SELECT * FROM esign_audit WHERE request_id=? ORDER BY id",
                         (r["id"],)).fetchall()
    conn.close()
    r["signatories"] = [dict(s) for s in sigs]
    r["audit"] = [dict(a) for a in audit]
    r["method_label"] = METHODS.get(r["method"], r["method"])
    r["destination_label"] = DESTINATION_RULES.get(r["destination"], {}).get("label", r["destination"])
    r["signed_count"] = sum(1 for s in r["signatories"] if s["status"] == "signed")
    r["is_open"] = r["status"] in OPEN_STATUSES
    r["transmitted"] = bool(r["provider_envelope_id"]) and r["provider"] != "local"
    return r


def get_request(request_id: int, user_id: str) -> Optional[Dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM esign_requests WHERE id=? AND user_id=?",
                       (request_id, user_id)).fetchone()
    conn.close()
    return _hydrate(row) if row else None


def list_requests(user_id: str, limit: int = 50) -> List[Dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM esign_requests WHERE user_id=? "
                        "ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()
    conn.close()
    return [_hydrate(r) for r in rows]
