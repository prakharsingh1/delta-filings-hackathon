# Delta Filings desktop

The full Delta Filings app, running on the user's own computer. Built for listed
companies that cannot upload unpublished price-sensitive information, board
papers or draft disclosures to a vendor's website. SEBI's insider-trading FAQ 8
treats vendor-hosted software where the vendor can see the structured digital
database as contrary to the regulations; this app keeps that data on the
company's own machine.

## What runs where

```
┌──────────────── Delta Filings.app / .exe / AppImage ────────────────┐
│ Tauri shell (Rust, system webview)                                   │
│   starts ─► delta-server (frozen Python: main.py + desktop/server.py) │
│             127.0.0.1:<random port>, per-launch token, Host check    │
│             SQLite + uploads in the user's data folder               │
└──────────────────────────────────────────────────────────────────────┘
        │ public filings                    │ AI (only if the user enables it)
        ▼                                   ▼
   NSE / BSE / SEBI sites          Ollama · LM Studio · llama.cpp · Jan on this
   (public data only)              computer, a company model server, or a cloud
                                   API with the user's own key
```

Nothing is sent to Delta's servers. No account is needed: the first launch
creates a local profile and runs the normal onboarding.

| Data | Location |
|---|---|
| macOS | `~/Library/Application Support/Delta Filings/` |
| Windows | `%LOCALAPPDATA%\Delta Filings\` |
| Linux | `~/.local/share/delta-filings/` |

`DELTA_DATA_DIR` overrides it (useful for a network share or an encrypted volume).

## AI providers

Settings, AI provider (`/settings/ai`). The desktop app starts on Ollama at
`http://127.0.0.1:11434` with **Private network only** switched on, which
refuses any AI request leaving this computer or the company's private network.

- On this computer: Ollama, LM Studio, llama.cpp server, Jan
- Company server: vLLM or Ollama on the intranet (private IP ranges only)
- Cloud with the user's key: Gemini, Anthropic Claude, OpenAI, Azure OpenAI, OpenRouter

Every existing AI feature (summaries, drafts, briefings, agents) uses the
chosen provider through `llm.py`, which wraps `google.genai.Client`.

## Security of the local server

- binds `127.0.0.1` on a random port, never all interfaces
- a new secret token each launch; the shell opens `/__desktop/enter?t=…`, which
  sets an HttpOnly, SameSite=Strict cookie; anything without it gets 403
- Host header must be `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding)
- page Content-Security-Policy allows network calls to this server only
- CDN scripts, styles and fonts are bundled (`vendor_assets.py`); analytics is
  stripped, so pages render offline and nothing reports to Google
- the backend exits when the shell closes its stdin

## Develop

```bash
python3 desktop/server.py --open        # backend + your browser, no shell
```

```bash
cd desktop/shell && npm install
DELTA_BACKEND_CMD="python3 $(pwd)/../server.py" npx tauri dev
```

## Build

```bash
desktop/build.sh            # macOS: .app + .dmg; Linux: .deb, .rpm, .AppImage
```

```powershell
powershell -ExecutionPolicy Bypass -File desktop\build.ps1   # Windows: .msi + setup.exe
```

CI builds all four targets: `.github/workflows/desktop.yml` (run it manually or
push a `desktop-v*` tag). Signing turns on when the secrets listed at the top of
that file exist.

## Before shipping to customers

- **Apple**: Developer ID certificate (US$99/year), hardened runtime with the
  entitlements in `macos/entitlements.plist`, notarisation. Sign the inner
  `delta-server` files as well as the app.
- **Windows**: an OV code-signing certificate on a hardware or cloud key store.
  Microsoft's Azure Artifact Signing is not offered to Indian businesses.
  SmartScreen warnings fade as download reputation builds.
- **Linux**: GPG-sign the packages and publish checksums.
- **Updates**: add `tauri-plugin-updater` with a signing key, and a policy switch
  so IT can disable it or point it at an internal mirror.
- **At-rest encryption**: SQLCipher for the database with its key in the OS
  keychain (`keyring`). Until then the data relies on FileVault or BitLocker.
- **Enterprise install**: per-machine MSI for Intune/SCCM, signed `.pkg` with an
  MDM configuration profile for managed settings (AI endpoint, local-only lock).
