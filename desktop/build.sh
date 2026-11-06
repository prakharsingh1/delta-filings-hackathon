#!/usr/bin/env bash
# Build the Delta Filings desktop app on macOS or Linux.
#   desktop/build.sh            # installers for this machine
#   desktop/build.sh app        # macOS .app only (fast check)
# Needs Python 3.12+, Node 20+, and Rust (rustup). Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."
BUNDLES="${1:-}"
[ -d /opt/homebrew/opt/rustup/bin ] && export PATH="/opt/homebrew/opt/rustup/bin:$PATH"

if [ ! -x desktop/.venv/bin/python ]; then
  python3 -m venv --system-site-packages desktop/.venv
  grep -viE '^(ocrmypdf|gunicorn)([^a-z0-9_-]|$)' requirements.txt > desktop/requirements-desktop.txt
  desktop/.venv/bin/pip install -q --prefer-binary -r desktop/requirements-desktop.txt "pyinstaller>=6.15" keyring "sqlcipher3>=0.6.5"
fi

echo "==> Bundling CDN assets for offline use"
desktop/.venv/bin/python desktop/vendor_assets.py

echo "==> Freezing the backend"
desktop/.venv/bin/pyinstaller desktop/delta-server.spec --noconfirm --distpath desktop/build

echo "==> Smoke test"
desktop/.venv/bin/python desktop/smoke_test.py desktop/build/delta-server

echo "==> Building the app"
cd desktop/shell
npm install --no-fund --no-audit
if [ -n "$BUNDLES" ]; then npx tauri build --bundles "$BUNDLES"; else npx tauri build; fi
echo "==> Done: desktop/shell/src-tauri/target/release/bundle/"
