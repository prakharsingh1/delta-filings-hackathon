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
import time
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


# ── Encrypted database ────────────────────────────────────────────────────
DB_NAME = "delta_filings.db"
_PLAIN_HEADER = b"SQLite format 3\x00"


def encryption_enabled() -> bool:
    return os.environ.get("DELTA_DB_ENCRYPTION", "1") != "0"


def _key_pragma(key_hex: str) -> str:
    return f"PRAGMA key = \"x'{key_hex}'\""


def install_encrypted_sqlite(key_hex: str) -> None:
    """Make every `import sqlite3` in the app use SQLCipher with this install's key.

    The app's modules open SQLite in eight places; swapping the module before
    they load encrypts all of them without touching their code.
    """
    import types

    import sqlcipher3.dbapi2 as sc
    shim = types.ModuleType("sqlite3")
    shim.__dict__.update({k: getattr(sc, k) for k in dir(sc) if not k.startswith("__")})
    pragma = _key_pragma(key_hex)

    def connect(database, *args, **kwargs):
        conn = sc.connect(database, *args, **kwargs)
        conn.execute(pragma)
        return conn

    shim.connect = connect
    shim.dbapi2 = shim
    shim.__file__ = getattr(sc, "__file__", "sqlcipher3")
    if "sqlite3" in sys.modules:
        print("[DESKTOP] sqlite3 was imported before encryption was installed", file=sys.stderr)
    sys.modules["sqlite3"] = shim
    sys.modules["sqlite3.dbapi2"] = shim


def encrypt_existing_database(path: Path, key_hex: str) -> bool:
    """Convert a plaintext database left by an earlier build. True if converted."""
    if not path.exists() or path.read_bytes()[:16] != _PLAIN_HEADER:
        return False
    import sqlcipher3.dbapi2 as sc
    tmp = path.with_suffix(".encrypting")
    tmp.unlink(missing_ok=True)
    src = sc.connect(str(path))
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        tables = src.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        src.execute(f"ATTACH DATABASE ? AS encrypted KEY \"x'{key_hex}'\"", (str(tmp),))
        src.execute("SELECT sqlcipher_export('encrypted')")
        src.execute("DETACH DATABASE encrypted")
    finally:
        src.close()
    check = sc.connect(str(tmp))
    try:
        check.execute(_key_pragma(key_hex))
        ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        same = check.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == tables
    finally:
        check.close()
    if not (ok and same):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Encrypting the existing database failed its integrity check; the original is untouched.")
    os.replace(tmp, path)
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    return True


# ── Backups ───────────────────────────────────────────────────────────────
BACKUP_KEEP = int(os.environ.get("DELTA_BACKUP_KEEP", "30") or 30)


def backup_dir(folder: Path) -> Path:
    custom = os.environ.get("DELTA_BACKUP_DIR", "").strip()
    return Path(custom).expanduser() if custom else folder / "backups"


def backup_now(folder: Path) -> Path:
    """A consistent copy of the database, encrypted with the same key."""
    import sqlite3  # the SQLCipher shim when encryption is on
    target_dir = backup_dir(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    target = target_dir / f"delta-filings-{stamp}.db"
    partial = target.with_suffix(".partial")
    partial.unlink(missing_ok=True)
    conn = sqlite3.connect(str(folder / DB_NAME), timeout=30)
    try:
        conn.execute("VACUUM INTO ?", (str(partial),))
    finally:
        conn.close()
    os.replace(partial, target)
    backups = sorted(target_dir.glob("delta-filings-*.db"))
    for old in backups[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)
    (target_dir / "last-backup.json").write_text(json.dumps(
        {"file": target.name, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "encrypted": encryption_enabled()}))
    return target


def _backup_loop(folder: Path) -> None:
    """Daily copy, the way the Companies Act expects electronic records backed up."""
    while True:
        try:
            today = backup_dir(folder) / f"delta-filings-{time.strftime('%Y-%m-%d')}.db"
            if not today.exists():
                print(f"[DESKTOP] backup written: {backup_now(folder)}")
        except Exception as e:  # never take the app down over a backup
            print(f"[DESKTOP] backup failed: {e}")
        time.sleep(3600)


# ── Logs ──────────────────────────────────────────────────────────────────
def redirect_logs(folder: Path) -> Path:
    """Send prints and request logs to logs/backend.log (rotated at 5 MB)."""
    logs = folder / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "backend.log"
    if path.exists() and path.stat().st_size > 5_000_000:
        os.replace(path, logs / "backend.1.log")
    stream = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stderr = stream
    import logging
    logging.basicConfig(stream=stream, level=logging.INFO, force=True)
    print(f"\n[DESKTOP] started {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return path


def configure_environment(folder: Path) -> None:
    """Everything main.py reads at import time. Must run before importing it."""
    for sub in ("uploads", "pdf_cache", "logos", "webview"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    if encryption_enabled():
        try:
            import sqlcipher3.dbapi2  # noqa: F401
        except ImportError:
            if getattr(sys, "frozen", False):
                raise RuntimeError("This build is missing its encryption library (sqlcipher3).")
            print("[DESKTOP] WARNING: sqlcipher3 is not installed, so the database is NOT encrypted. "
                  "Use desktop/.venv/bin/python, or pip install sqlcipher3.", file=sys.stderr)
            os.environ["DELTA_DB_ENCRYPTION"] = "0"
        else:
            key = install_secret(folder, "database")
            if encrypt_existing_database(folder / DB_NAME, key):
                print("[DESKTOP] existing database encrypted")
            install_encrypted_sqlite(key)
    os.environ.update({
        "DELTA_DESKTOP": "1",
        "DATABASE_PATH": str(folder / DB_NAME),
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
    parser.add_argument("--log-to-console", action="store_true", help="keep logs on the terminal")
    args = parser.parse_args(argv)

    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    ready_out = sys.stdout  # the shell reads the ready line here
    if not (args.log_to_console or sys.stdout.isatty()):
        redirect_logs(folder)
    try:
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
    except Exception as e:
        # Tell the shell why, so the window can say more than "it stopped".
        import traceback
        traceback.print_exc()
        ready_out.write(json.dumps({"event": "error", "message": f"{type(e).__name__}: {e}"[:600],
                                    "logs_dir": str(folder / "logs")}) + "\n")
        ready_out.flush()
        return 1

    url = f"http://127.0.0.1:{port}/__desktop/enter?t={token}"
    ready = {"event": "ready", "port": port, "url": url, "data_dir": str(folder),
             "logs_dir": str(folder / "logs"), "backups_dir": str(backup_dir(folder)),
             "encrypted": encryption_enabled()}
    ready_out.write(json.dumps(ready) + "\n")
    ready_out.flush()
    threading.Thread(target=_backup_loop, args=(folder,), daemon=True, name="daily-backup").start()
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
