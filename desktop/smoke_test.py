"""Start a frozen backend and prove it serves the app safely.

    python desktop/smoke_test.py desktop/build/delta-server

Checks: it prints a ready line, refuses requests without the launch cookie,
refuses a foreign Host header, and serves the dashboard and AI settings once
the launch token is presented. Uses a throwaway data folder. Exit code 0 on
success.
"""
import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def status(opener, url, headers=None):
    try:
        return opener.open(urllib.request.Request(url, headers=headers or {}), timeout=30).status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> int:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "desktop/build/delta-server")
    exe = folder / ("delta-server.exe" if os.name == "nt" else "delta-server")
    data = tempfile.mkdtemp(prefix="delta-smoke-")
    env = dict(os.environ, DELTA_DATA_DIR=data, DELTA_DEMO_FREEZE="1", DELTA_KEYCHAIN="0")
    proc = subprocess.Popen([str(exe)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
    ready, started = None, time.time()
    try:
        for line in proc.stdout:
            print("  |", line.rstrip()[:160])
            if '"event": "ready"' in line:
                ready = json.loads(line)
                break
            if time.time() - started > 180:
                break
        if not ready:
            print("FAIL: backend never became ready")
            log = Path(data) / "logs" / "backend.log"
            if log.exists():
                print("---- backend.log (last 60 lines) ----")
                print("\n".join(log.read_text(errors="replace").splitlines()[-60:]))
            return 1
        port, url = ready["port"], ready["url"]
        plain = urllib.request.build_opener()
        checks = {
            "refuses requests without the launch cookie": status(plain, f"http://127.0.0.1:{port}/dashboard") == 403,
            "refuses a foreign Host header": status(plain, f"http://127.0.0.1:{port}/", {"Host": f"evil.example:{port}"}) == 400,
            "ignores a spoofed X-Forwarded-Host": status(plain, f"http://127.0.0.1:{port}/", {
                "Host": f"evil.example:{port}", "X-Forwarded-Host": f"127.0.0.1:{port}"}) == 400,
        }
        jar = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        checks["accepts the launch token"] = status(jar, url) == 200
        checks["serves the AI provider page"] = status(jar, f"http://127.0.0.1:{port}/settings/ai") == 200
        checks["serves bundled CDN assets"] = status(jar, f"http://127.0.0.1:{port}/__desktop/vendor/manifest.json") == 200
        db = Path(data) / "delta_filings.db"
        checks["database is encrypted at rest"] = db.exists() and db.read_bytes()[:16] != b"SQLite format 3\x00"
        backups = Path(ready.get("backups_dir", Path(data) / "backups"))
        for _ in range(20):
            if list(backups.glob("delta-filings-*.db")):
                break
            time.sleep(0.5)
        checks["writes a daily backup"] = bool(list(backups.glob("delta-filings-*.db")))
        checks["writes a log file"] = (Path(data) / "logs" / "backend.log").exists()
        for name, ok in checks.items():
            print(("PASS " if ok else "FAIL ") + name)
        print(f"ready in {time.time() - started:.1f}s")
        return 0 if all(checks.values()) else 1
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(main())
