#!/usr/bin/env bash
# Build the Delta Filings desktop app on macOS or Linux.
#   desktop/build.sh            # installers for this machine
#   desktop/build.sh app        # macOS .app only (fast check)
# Needs Python 3.12+, Node 20+, and Rust (rustup). Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."
BUNDLES="${1:-}"
[ -d /opt/homebrew/opt/rustup/bin ] && export PATH="/opt/homebrew/opt/rustup/bin:$PATH"

# iCloud Drive often syncs ~/Documents and ~/Desktop. When a build rewrites a
# file there, iCloud can leave a "name 2.ext" copy, and those copies get bundled
# into the app. Keep build output in *.nosync folders, which iCloud skips.
if [ "$(uname)" = Darwin ]; then
  for d in desktop/build desktop/shell/src-tauri/target; do
    [ -L "$d" ] && continue
    if [ -d "$d" ] && [ ! -e "$d.nosync" ]; then mv "$d" "$d.nosync"; else rm -rf "$d"; fi
    mkdir -p "$d.nosync"
    ln -s "$(basename "$d").nosync" "$d"
  done
fi

if [ ! -x desktop/.venv/bin/python ]; then
  python3 -m venv --system-site-packages desktop/.venv
  grep -viE '^(ocrmypdf|gunicorn)([^a-z0-9_-]|$)' requirements.txt > desktop/requirements-desktop.txt
  desktop/.venv/bin/pip install -q --prefer-binary -r desktop/requirements-desktop.txt "pyinstaller>=6.15" keyring "sqlcipher3>=0.6.5"
fi

echo "==> Bundling CDN assets for offline use"
desktop/.venv/bin/python desktop/vendor_assets.py

echo "==> Freezing the backend"
desktop/.venv/bin/pyinstaller desktop/delta-server.spec --noconfirm --distpath desktop/build --workpath desktop/build/work

echo "==> Smoke test"
desktop/.venv/bin/python desktop/smoke_test.py desktop/build/delta-server

echo "==> Building the app"
cd desktop/shell
npm install --no-fund --no-audit
if [ -n "$BUNDLES" ]; then npx tauri build --bundles "$BUNDLES"; else npx tauri build; fi
echo "==> Done: desktop/shell/src-tauri/target/release/bundle/"
