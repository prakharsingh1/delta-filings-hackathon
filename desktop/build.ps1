# Build the Delta Filings desktop app on Windows (MSI and NSIS installers).
#   powershell -ExecutionPolicy Bypass -File desktop\build.ps1
# Needs Python 3.12+, Node 20+, Rust (rustup, MSVC toolchain) and WiX via Tauri.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path "desktop\.venv\Scripts\python.exe")) {
  py -3 -m venv --system-site-packages desktop\.venv
  Get-Content requirements.txt | Where-Object { $_ -notmatch '^(ocrmypdf|gunicorn)([^a-z0-9_-]|$)' } | Set-Content desktop\requirements-desktop.txt
  desktop\.venv\Scripts\pip install -q --prefer-binary -r desktop\requirements-desktop.txt "pyinstaller>=6.15" keyring "sqlcipher3>=0.6.5"
}

Write-Host "==> Bundling CDN assets for offline use"
desktop\.venv\Scripts\python desktop\vendor_assets.py

Write-Host "==> Freezing the backend"
desktop\.venv\Scripts\pyinstaller desktop\delta-server.spec --noconfirm --distpath desktop\build

Write-Host "==> Smoke test"
desktop\.venv\Scripts\python desktop\smoke_test.py desktop\build\delta-server

Write-Host "==> Building installers"
Set-Location desktop\shell
npm install --no-fund --no-audit
npx tauri build --bundles msi,nsis
Write-Host "==> Done: desktop\shell\src-tauri\target\release\bundle\"
