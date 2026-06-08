"""Per-user stock price alerts with Telegram + in-browser delivery.

Design:
  - Alerts are evaluated server-side every time /api/prices is hit (i.e.
    while the user has the prices page open). The response includes any
    `fired_alerts` so the front-end can play a sound and pop a browser
    notification immediately.
  - Same fire path also sends a Telegram message if the user has linked
    their chat_id and TELEGRAM_BOT_TOKEN is configured on the server.
  - 60-minute per-alert cooldown so we don't spam if a stock hovers
    around the threshold.
  - Channel is intentionally Telegram-only (free, instant, no per-msg
    business approval like WhatsApp). In-browser is the second channel.

For always-on (site closed) delivery we'd need an external scheduler
hitting an evaluator endpoint — flagged as a phase-2 follow-up.
"""

import os
import hmac
import hashlib
import sqlite3
import requests
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple

from database import get_connection, get_user_telegram


_USER_AGENT = "Mozilla/5.0 (Delta Filings price-alert bot)"
COOLDOWN_MIN = 60   # don't re-fire the same alert within this window
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


# ── Database ──

def init_alerts_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            threshold_pct REAL NOT NULL,
            direction TEXT NOT NULL DEFAULT 'either',
            telegram_chat_id TEXT DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_triggered_at TEXT,
            last_trigger_price REAL,
            UNIQUE(user_id, symbol, threshold_pct, direction)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_alerts_user ON price_alerts(user_id, enabled)"
    )
    conn.commit()
    conn.close()


def create_alert(user_id: str, symbol: str, threshold_pct: float,
                 direction: str = "either", telegram_chat_id: str = "") -> Dict:
    """Insert (or update) an alert. Direction: 'up' | 'down' | 'either'."""
    init_alerts_db()
    if direction not in ("up", "down", "either"):
        direction = "either"
    threshold_pct = float(threshold_pct)
    if threshold_pct <= 0 or threshold_pct > 50:
        raise ValueError("threshold_pct must be between 0 and 50")

    conn = get_connection()
    now = datetime.now().isoformat()
    try:
        cur = conn.execute(
            """INSERT INTO price_alerts
               (user_id, symbol, threshold_pct, direction, telegram_chat_id,
                enabled, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(user_id, symbol, threshold_pct, direction)
               DO UPDATE SET telegram_chat_id = excluded.telegram_chat_id,
                             enabled = 1""",
            (user_id, symbol.upper(), threshold_pct, direction,
             telegram_chat_id.strip(), now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM price_alerts WHERE user_id=? AND symbol=? "
            "AND threshold_pct=? AND direction=?",
            (user_id, symbol.upper(), threshold_pct, direction),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def list_alerts(user_id: str) -> List[Dict]:
    init_alerts_db()
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM price_alerts WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_alert(user_id: str, alert_id: int) -> bool:
    init_alerts_db()
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM price_alerts WHERE id = ? AND user_id = ?",
        (alert_id, user_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def toggle_alert(user_id: str, alert_id: int, enabled: bool) -> bool:
    init_alerts_db()
    conn = get_connection()
    cur = conn.execute(
        "UPDATE price_alerts SET enabled = ? WHERE id = ? AND user_id = ?",
        (1 if enabled else 0, alert_id, user_id),
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n > 0


# ── Telegram helper ──

def telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())


def telegram_bot_username() -> str:
    """Returns the configured bot's username (without @) for embedding in
    the Login Widget. Cached after first lookup."""
    global _BOT_USERNAME_CACHE
    if _BOT_USERNAME_CACHE is not None:
        return _BOT_USERNAME_CACHE
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        _BOT_USERNAME_CACHE = ""
        return ""
    try:
        r = requests.get(TELEGRAM_API.format(token=token, method="getMe"),
                         timeout=8, headers={"User-Agent": _USER_AGENT})
        if r.status_code == 200 and r.json().get("ok"):
            _BOT_USERNAME_CACHE = r.json()["result"].get("username", "")
            return _BOT_USERNAME_CACHE
    except Exception as e:
        print(f"[ALERT] getMe failed: {e}")
    _BOT_USERNAME_CACHE = ""
    return ""


_BOT_USERNAME_CACHE: Optional[str] = None


def verify_telegram_auth(payload: Dict) -> Tuple[bool, str]:
    """Validate the Telegram Login Widget callback payload.

    Telegram signs the auth payload with HMAC-SHA-256 using SHA-256(bot_token)
    as the key. We rebuild the data_check_string from every field except
    `hash`, sorted alphabetically and joined by newlines, then compare HMACs.
    Also rejects payloads older than 24 hours (replay protection).

    Reference: https://core.telegram.org/widgets/login#checking-authorization
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return False, "Server has no Telegram bot configured."
    received_hash = payload.get("hash", "")
    if not received_hash:
        return False, "Missing hash in Telegram payload."

    fields = {k: v for k, v in payload.items() if k != "hash" and v is not None}
    data_check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return False, "Telegram signature mismatch — payload tampered or wrong bot."

    # Replay protection: auth_date must be recent (≤24h).
    try:
        auth_dt = datetime.fromtimestamp(int(payload.get("auth_date", 0)))
    except (TypeError, ValueError):
        return False, "Bad auth_date in Telegram payload."
    if (datetime.now() - auth_dt) > timedelta(hours=24):
        return False, "Telegram authorization expired (>24h old). Please re-authorize."

    return True, "ok"


def send_telegram(chat_id: str, text: str) -> bool:
    """POST to Telegram's sendMessage. Returns True on 200 + ok=True.
    No-ops gracefully if the bot token isn't configured or chat_id is empty."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not (chat_id or "").strip():
        return False
    try:
        url = TELEGRAM_API.format(token=token, method="sendMessage")
        r = requests.post(
            url,
            json={"chat_id": chat_id.strip(), "text": text, "parse_mode": "HTML"},
            timeout=10,
            headers={"User-Agent": _USER_AGENT},
        )
        if r.status_code != 200:
            print(f"[ALERT] Telegram non-200 {r.status_code}: {r.text[:200]}")
            return False
        body = r.json()
        if not body.get("ok"):
            print(f"[ALERT] Telegram not ok: {body.get('description')}")
            return False
        return True
    except Exception as e:
        print(f"[ALERT] Telegram error: {e}")
        return False


# ── Evaluation ──

def _alert_breached(alert: Dict, p_change: float) -> bool:
    """Did this alert trigger given the stock's pChange today?"""
    threshold = float(alert["threshold_pct"])
    direction = alert["direction"]
    if direction == "up":
        return p_change >= threshold
    if direction == "down":
        return p_change <= -threshold
    return abs(p_change) >= threshold


def _on_cooldown(alert: Dict) -> bool:
    last = alert.get("last_triggered_at")
    if not last:
        return False
    try:
        dt = datetime.fromisoformat(last)
    except Exception:
        return False
    return (datetime.now() - dt) < timedelta(minutes=COOLDOWN_MIN)


def _format_alert_message(alert: Dict, price: Dict) -> str:
    sym = alert["symbol"]
    pc = float(price.get("pChange") or 0)
    last = float(price.get("lastPrice") or 0)
    arrow = "📈" if pc > 0 else "📉"
    return (
        f"{arrow} <b>{sym}</b> moved <b>{pc:+.2f}%</b> today\n"
        f"Last price: ₹{last:,.2f}\n"
        f"Threshold: ±{alert['threshold_pct']}% ({alert['direction']})\n"
        f"— Delta Filings"
    )


def _resolve_chat_id(user_id: str, alert: Dict) -> str:
    """Use the alert's chat_id if set; otherwise fall back to the user's
    profile-level Telegram link (set via the Login Widget). Lets users
    link Telegram once and have all alerts auto-deliver, while still
    allowing a per-alert override (e.g. group chat for a specific stock)."""
    explicit = (alert.get("telegram_chat_id") or "").strip()
    if explicit:
        return explicit
    link = get_user_telegram(user_id)
    return (link or {}).get("chat_id", "") or ""


def evaluate_user_alerts(user_id: str, prices_by_symbol: Dict[str, Dict]) -> List[Dict]:
    """Check every enabled alert this user has against the current price
    snapshot. For each fresh fire (not on cooldown), record it, send the
    Telegram message if linked, and return the list so the caller can
    surface them in the API response."""
    init_alerts_db()
    alerts = list_alerts(user_id)
    fired: List[Dict] = []
    if not alerts:
        return fired

    conn = get_connection()
    now_iso = datetime.now().isoformat()

    for a in alerts:
        if not a.get("enabled"):
            continue
        sym = a["symbol"]
        price = prices_by_symbol.get(sym)
        if not price:
            continue
        try:
            pc = float(price.get("pChange") or 0)
        except (TypeError, ValueError):
            continue
        if not _alert_breached(a, pc):
            continue
        if _on_cooldown(a):
            continue

        # Record fire
        last_price = float(price.get("lastPrice") or 0)
        try:
            conn.execute(
                "UPDATE price_alerts SET last_triggered_at = ?, "
                "last_trigger_price = ? WHERE id = ?",
                (now_iso, last_price, a["id"]),
            )
        except sqlite3.Error as e:
            print(f"[ALERT] update fire timestamp failed: {e}")

        # Send Telegram (best-effort) — alert-level chat_id wins, else
        # fall back to the user's profile-level Login Widget link.
        msg = _format_alert_message(a, price)
        chat_id = _resolve_chat_id(user_id, a)
        tele_ok = send_telegram(chat_id, msg) if chat_id else False

        fired.append({
            "alert_id": a["id"],
            "symbol": sym,
            "threshold_pct": a["threshold_pct"],
            "direction": a["direction"],
            "pChange": pc,
            "lastPrice": last_price,
            "companyName": price.get("companyName", sym),
            "telegram_sent": tele_ok,
            "message": msg,
        })

    conn.commit()
    conn.close()
    return fired


def fire_test_alert(user_id: str, symbol: str = "TEST",
                    chat_id: str = "", threshold_pct: float = 3.0,
                    fallback_to_user_link: bool = True) -> Dict:
    """Synthesize a fake fire for the UI's 'Test Alert' button. Sends a
    Telegram message if a chat_id is provided so users can verify their
    setup before depending on the real flow."""
    fake_price = {
        "symbol": symbol,
        "lastPrice": 1234.56,
        "pChange": threshold_pct + 0.1,
        "companyName": "Delta Filings — Test Alert",
    }
    fake_alert = {
        "symbol": symbol,
        "threshold_pct": threshold_pct,
        "direction": "either",
    }
    msg = (
        "🔔 <b>Test alert from Delta Filings</b>\n"
        f"This confirms your Telegram link works for <b>{symbol}</b>. "
        "Real alerts will look like this:\n\n"
        + _format_alert_message(fake_alert, fake_price)
    )
    if not chat_id and fallback_to_user_link:
        link = get_user_telegram(user_id)
        chat_id = (link or {}).get("chat_id", "") or ""
    tele_ok = send_telegram(chat_id, msg) if chat_id else False
    return {
        "is_test": True,
        "symbol": symbol,
        "threshold_pct": threshold_pct,
        "pChange": fake_price["pChange"],
        "lastPrice": fake_price["lastPrice"],
        "companyName": fake_price["companyName"],
        "telegram_sent": tele_ok,
        "telegram_configured": telegram_configured(),
        "message": msg,
    }
