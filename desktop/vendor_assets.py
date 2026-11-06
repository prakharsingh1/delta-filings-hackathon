"""Copy the CDN scripts, stylesheets and fonts the templates load into
desktop/vendor/, and write manifest.json mapping each CDN URL to its local
path. desktop/server.py rewrites pages with that map, so the desktop app works
offline and loads nothing from third-party servers.

Run at build time:  python3 desktop/vendor_assets.py
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "desktop" / "vendor"
PREFIX = "/__desktop/vendor/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")  # Google Fonts serves woff2 to this
URL_RE = re.compile(r'"(https://(?:cdn\.tailwindcss\.com[^"]*|cdn\.jsdelivr\.net/npm/[^"]+|'
                    r'unpkg\.com/[^"]+|fonts\.googleapis\.com/css2\?[^"]+))"')
SKIP = ("checkout.razorpay.com", "googletagmanager.com")


def _name(url: str, ext: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16] + ext


def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, timeout=60, headers={"User-Agent": UA}, **kw)
    r.raise_for_status()
    return r


def fetch_font_css(url: str) -> str:
    css = _get(url).text
    (OUT / "fonts").mkdir(parents=True, exist_ok=True)

    def swap(m):
        font_url = m.group(1)
        name = "fonts/" + _name(font_url, ".woff2")
        target = OUT / name
        if not target.exists():
            target.write_bytes(_get(font_url).content)
        return f"url({PREFIX}{name})"
    return re.sub(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", swap, css)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    urls = set()
    for tpl in (ROOT / "templates").rglob("*.html"):
        urls.update(URL_RE.findall(tpl.read_text(errors="ignore")))
    manifest = {}
    for url in sorted(urls):
        if any(s in url for s in SKIP):
            continue
        try:
            if "fonts.googleapis.com" in url:
                name = _name(url, ".css")
                (OUT / name).write_text(fetch_font_css(url.replace("&amp;", "&")))
            else:
                ext = ".css" if url.split("?")[0].endswith(".css") else ".js"
                name = _name(url, ext)
                (OUT / name).write_bytes(_get(url).content)
        except requests.RequestException as e:
            print(f"  ! {url}: {e}", file=sys.stderr)
            continue
        manifest[url] = PREFIX + name
        if "&" in url and "&amp;" not in url:
            manifest[url.replace("&", "&amp;")] = PREFIX + name
        print(f"  {name}  <-  {url}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"{len(manifest)} URLs vendored into {OUT} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
