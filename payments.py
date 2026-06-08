"""Razorpay payment integration for the ₹4999 access fee.

Flow:
  1. Frontend hits /api/payment/create-order → server creates a Razorpay
     order via REST and returns {order_id, amount, currency, key_id}.
  2. Frontend opens Razorpay Checkout JS with that order_id.
  3. User pays. Razorpay returns {razorpay_payment_id, razorpay_order_id,
     razorpay_signature} to the frontend.
  4. Frontend POSTs that to /api/payment/verify.
  5. Server verifies the HMAC signature locally (HMAC-SHA256 of
     "<order_id>|<payment_id>" with key_secret) — that's the security
     invariant. If valid, mark the user paid in user_payments.

Env vars expected:
  RAZORPAY_KEY_ID — public key (used in checkout JS too)
  RAZORPAY_KEY_SECRET — private; used for the create-order auth + signature verify

Both must be set for live payment. If RAZORPAY_KEY_SECRET is missing, the
create-order route returns a 503 telling the user to use a referral code
instead — referral path still works in any env.
"""

import os
import hmac
import hashlib
import requests
from typing import Optional, Dict


ACCESS_FEE_PAISE = 499900   # ₹4999.00 in paise (Razorpay's smallest unit)
DEFAULT_CURRENCY = "INR"


def razorpay_configured() -> bool:
    return bool(os.environ.get("RAZORPAY_KEY_ID", "").strip()
                and os.environ.get("RAZORPAY_KEY_SECRET", "").strip())


def public_key_id() -> str:
    return os.environ.get("RAZORPAY_KEY_ID", "").strip()


def _auth_pair():
    return (os.environ.get("RAZORPAY_KEY_ID", "").strip(),
            os.environ.get("RAZORPAY_KEY_SECRET", "").strip())


def create_order(amount_paise: int = ACCESS_FEE_PAISE,
                 currency: str = DEFAULT_CURRENCY,
                 receipt: str = "",
                 notes: Optional[Dict] = None) -> Optional[Dict]:
    """Create a Razorpay Order. Returns the order dict on success
    (`id` is the order_id we hand to the frontend), None on failure."""
    if not razorpay_configured():
        return None
    key_id, key_secret = _auth_pair()
    payload = {
        "amount": int(amount_paise),
        "currency": currency,
        "receipt": (receipt or "")[:40],
        "payment_capture": 1,    # auto-capture
    }
    if notes:
        payload["notes"] = notes
    try:
        r = requests.post(
            "https://api.razorpay.com/v1/orders",
            json=payload,
            auth=(key_id, key_secret),
            timeout=15,
        )
        if r.status_code in (200, 201):
            return r.json()
        print(f"[PAY] Razorpay create_order failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[PAY] Razorpay create_order error: {e}")
    return None


def verify_payment_signature(razorpay_order_id: str,
                             razorpay_payment_id: str,
                             razorpay_signature: str) -> bool:
    """Verify the HMAC-SHA256 signature Razorpay returns after a successful
    payment. This is the only protection against a forged 'paid' POST."""
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    if not (key_secret and razorpay_order_id and razorpay_payment_id and razorpay_signature):
        return False
    try:
        msg = f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8")
        expected = hmac.new(
            key_secret.encode("utf-8"), msg, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, razorpay_signature)
    except Exception as e:
        print(f"[PAY] verify failed: {e}")
        return False
