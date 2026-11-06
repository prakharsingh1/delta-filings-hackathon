# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Delta Filings desktop backend (the Tauri sidecar).
# Build from the project root:
#     desktop/.venv/bin/pyinstaller desktop/delta-server.spec --noconfirm
# Output: dist/delta-server/ (one folder; the shell ships it as a resource).
import glob
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# main.py imports its blueprints by name, which PyInstaller cannot see.
hidden = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(ROOT, "routes_*.py")))
hidden += ["llm", "routes_ai", "main"]
# OS keychain backends are loaded by entry point, invisible to the analyser.
from PyInstaller.utils.hooks import collect_submodules
hidden += collect_submodules("keyring.backends")
hidden += ["sqlcipher3", "sqlcipher3.dbapi2"]  # encrypted SQLite for the local database

datas = [
    (os.path.join(ROOT, "templates"), "templates"),
    (os.path.join(ROOT, "static"), "static"),
    (os.path.join(ROOT, "desktop", "vendor"), os.path.join("desktop", "vendor")),
]

a = Analysis(
    [os.path.join(ROOT, "desktop", "server.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # Dev and test tooling never belongs in a customer install.
    # OCR needs Tesseract and Ghostscript, which are not shipped; the app
    # already reports OCR as unavailable when the library is missing.
    excludes=["tests", "pytest", "playwright", "IPython", "tkinter", "matplotlib", "notebook",
              "ocrmypdf", "pikepdf",
              # Optional extras of google-api-core (grpc) and fsspec/pyiceberg
              # (pandas, numpy, pyarrow): imported only when present, never used here.
              "grpc", "pandas", "numpy", "pyarrow", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="delta-server",
    console=True,          # the shell reads the ready line from stdout
    disable_windowed_traceback=False,
    codesign_identity=os.environ.get("APPLE_SIGNING_IDENTITY") or None,
    entitlements_file=os.path.join(ROOT, "desktop", "macos", "entitlements.plist") if os.environ.get("APPLE_SIGNING_IDENTITY") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="delta-server")
