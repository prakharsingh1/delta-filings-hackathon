"""Delta Filings desktop backend.

Runs the full Flask app on this computer for the native shell (Tauri) or for a
browser during development. Nothing here talks to Delta's servers: public
filings come straight from the exchanges, documents and the database stay in
the user's data folder, and AI goes wherever Settings, AI provider says
(a local model by default, private-network-only switched on).

Security model for the loopback server
  * binds 127.0.0.1 on a random port, never 0.0.0.0
  * a fresh secret token per launch; the shell opens /__desktop/enter?t=TOKEN,
    which sets an HttpOnly SameSite=Strict cookie. Every other request without
    it is refused, so other local programs and web pages in the user's normal
    browser cannot drive the app
  * the Host header must be 127.0.0.1:PORT or localhost:PORT (DNS rebinding)
  * a Content-Security-Policy stops page scripts from sending data anywhere
    but this server
  * CDN scripts, styles and fonts are served from the bundle, and analytics
    is removed, so pages render offline and nothing is reported to Google

Usage
  python3 desktop/server.py            print a ready line, serve until killed
  python3 desktop/server.py --open     also open the URL in the default browser
  python3 desktop/server.py --watch-stdin  exit when the parent closes stdin
"""
from __future__ import annotations

import argparse
import getpass
import hmac
import json
import os
import re
import secrets
import sys
import threading
from pathlib import Path

APP_NAME = "Delta Filings"
LOCAL_USER_ID = "local-desktop"
LOCAL_EMAIL = "owner@this-computer.local"
COOKIE = "delta_desktop"


def bundle_root() -> Path:
    """Folder holding main.py, templates and static (the PyInstaller bundle when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get("DELTA_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local")) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share")) / "delta-filings"


KEYCHAIN_SERVICE = "Delta Filings"


def install_secret(folder: Path, name: str) -> str:
    """A per-install secret, created once and never changed.

    Kept in the OS keychain (macOS Keychain, Windows Credential Manager,
    libsecret) so it does not sit next to the database it protects. Falls back
    to an owner-only file when no keychain is available.
    """
    account = f"{name}:{folder}"
    try:
        if os.environ.get("DELTA_KEYCHAIN", "1") == "0":  # tests and CI
            raise RuntimeError("keychain disabled")
        import keyring
        value = keyring.get_password(KEYCHAIN_SERVICE, account)
        if value:
            return value
        legacy = folder / f"{name}.key"
        value = legacy.read_text().strip() if legacy.exists() else secrets.token_hex(32)
        keyring.set_password(KEYCHAIN_SERVICE, account, value)
        if legacy.exists():
            legacy.unlink()
        return value
    except Exception:
        pass
    path = folder / f"{name}.key"
    if path.exists():
        return path.read_text().strip()
    value = secrets.token_hex(32)
    path.write_text(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def _profile_path(folder: Path) -> Path:
    return folder / "profile.json"


def _os_full_name() -> str:
    """The account's display name ("Prakhar Singh"), falling back to the login."""
    login = getpass.getuser() or "You"
    try:
        import pwd
        gecos = (pwd.getpwnam(login).pw_gecos or "").split(",")[0].strip()
        if gecos:
            return gecos
    except (ImportError, KeyError):
        pass
    return login.split(".")[0].title()


def load_profile(folder: Path) -> dict:
    base = {"id": LOCAL_USER_ID, "email": LOCAL_EMAIL, "onboarded": False, "name": _os_full_name()}
    try:
        base.update(json.loads(_profile_path(folder).read_text()))
    except (OSError, ValueError):
        pass
    base.update(id=LOCAL_USER_ID, email=LOCAL_EMAIL)
    return base


def save_profile(folder: Path, updates: dict) -> dict:
    profile = load_profile(folder)
    profile.update({k: v for k, v in (updates or {}).items() if k not in ("id", "email")})
    _profile_path(folder).write_text(json.dumps(profile, indent=2))
    return profile


def configure_environment(folder: Path) -> None:
    """Everything main.py reads at import time. Must run before importing it."""
    for sub in ("uploads", "pdf_cache", "logos", "webview"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "DELTA_DESKTOP": "1",
        "DATABASE_PATH": str(folder / "delta_filings.db"),
        "DELTA_UPLOAD_DIR": str(folder / "uploads"),
        "DELTA_PDF_CACHE_DIR": str(folder / "pdf_cache"),
        "DELTA_SECRET_KEY": install_secret(folder, "session"),
        # Seals the insider-trading record's hash chain (SEBI PIT Reg 3(5)).
        # Must never change once records exist.
        "DELTA_SDD_KEY": install_secret(folder, "sdd"),
        "DELTA_INSECURE_COOKIES": "1",       # plain http on loopback
        "DELTA_DEMO_MODE": "0",
        "DELTA_SKIP_PRELOAD": "1",
        "DELTA_ADMIN_EMAILS": LOCAL_EMAIL,   # the local user owns this install
        # No cloud account in the desktop app. The client library needs values.
        "SUPABASE_URL": "http://127.0.0.1:9",
        "SUPABASE_PUBLISHABLE_KEY": "desktop-local-only",
        "SUPABASE_SECRET_KEY": "",
    })
    # Keys are entered in Settings, AI provider; never inherited from a dev .env.
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "FIRECRAWL_API_KEY"):
        os.environ.setdefault(k, "")
    try:
        import dotenv
        dotenv.load_dotenv = lambda *a, **k: False
    except ImportError:
        pass


# ── CDN assets served from the bundle ─────────────────────────────────────
def vendor_dir() -> Path:
    return bundle_root() / "desktop" / "vendor"


def load_vendor_map() -> dict:
    try:
        return json.loads((vendor_dir() / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}


_ANALYTICS = re.compile(r'<script[^>]+googletagmanager\.com[^>]*>\s*</script>', re.I)


def install_desktop_layer(app, folder: Path, token: str, port: int) -> None:
    from flask import abort, request, send_from_directory, session

    import main

    # Profiles live in a local file instead of the hosted account service.
    def sb_get_user_by_id(uid):
        return load_profile(folder) if uid == LOCAL_USER_ID else None

    def sb_update_user_metadata(uid, meta):
        if uid == LOCAL_USER_ID:
            save_profile(folder, meta)
        return True

    main.sb_get_user_by_id = sb_get_user_by_id
    main.sb_update_user_metadata = sb_update_user_metadata
    main.LOGO_VOLUME_DIR = str(folder / "logos")

    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    vendor = load_vendor_map()

    @app.before_request
    def _desktop_gate():
        if request.host not in allowed_hosts:
            abort(400)
        if request.path == "/__desktop/enter":
            if not hmac.compare_digest(request.args.get("t", ""), token):
                abort(403)
            nxt = request.args.get("next") or "/"
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = "/"
            # The shell navigates here from its own origin, which the webview
            # treats as cross-site, so a 302 would arrive without the Strict
            # cookie. A same-origin page that forwards itself avoids that.
            target = json.dumps(nxt)
            resp = app.response_class(
                "<!doctype html><meta charset=utf-8><title>Delta Filings</title>"
                f"<script>location.replace({target})</script>", mimetype="text/html")
            resp.set_cookie(COOKIE, token, httponly=True, samesite="Strict", path="/")
            return resp
        if request.path.startswith(("/static/", "/__desktop/vendor/")) or request.path == "/favicon.ico":
            return None
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), token):
            return ("Open Delta Filings from its app window.", 403)
        origin = request.headers.get("Origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in (
                f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            abort(403)
        if session.get("user_id") != LOCAL_USER_ID:
            session.clear()
            session["user_id"] = LOCAL_USER_ID
            session["user_cache"] = load_profile(folder)
        return None

    @app.route("/__desktop/vendor/<path:name>")
    def _desktop_vendor(name):
        return send_from_directory(vendor_dir(), name, max_age=31536000)

    @app.after_request
    def _desktop_headers(resp):
        if resp.mimetype == "text/html" and not resp.direct_passthrough:
            html = resp.get_data(as_text=True)
            for url, local in vendor.items():
                html = html.replace(url, local)
            html = _ANALYTICS.sub("", html)
            resp.set_data(html)
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
            "img-src 'self' data: blob: https:; connect-src 'self'; "
            "frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")
        resp.headers["Cache-Control"] = resp.headers.get("Cache-Control") or "no-store"
        return resp


def _watch_stdin():
    """The shell holds our stdin open; when it exits the pipe closes and so do we."""
    try:
        while sys.stdin.read(1):
            pass
    finally:
        os._exit(0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Delta Filings desktop backend")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--open", action="store_true", help="open the app in the default browser")
    parser.add_argument("--watch-stdin", action="store_true")
    args = parser.parse_args(argv)

    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    configure_environment(folder)
    root = bundle_root()
    sys.path.insert(0, str(root))
    os.chdir(folder)

    from werkzeug.serving import make_server

    import main as app_main
    app = app_main.app

    token = secrets.token_urlsafe(32)
    server = make_server("127.0.0.1", args.port, app, threaded=True)
    port = server.server_port
    os.environ["DELTA_SELF_PORT"] = str(port)
    install_desktop_layer(app, folder, token, port)

    url = f"http://127.0.0.1:{port}/__desktop/enter?t={token}"
    print(json.dumps({"event": "ready", "port": port, "url": url, "data_dir": str(folder)}), flush=True)
    if args.watch_stdin:
        threading.Thread(target=_watch_stdin, daemon=True).start()
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
