"""Download company logos once, in the best resolution available.

The UI resolves logos in this order: static/logos/<SYM>.<ext> first, then the
runtime volume, then a live scrape of the company's site. Anything not in the
first tier is re-fetched over the network, which is why logos appeared to
reload. This puts every monitored company in the first tier.

Candidate sources are tried in order and the largest valid image wins.
Run once: python3 download_logos.py
"""

import io
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

import requests

LOGO_DIR = os.path.join(os.path.dirname(__file__), "static", "logos")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Wikimedia serves high-resolution logos for listed issuers and is stable.
COMPANIES: Dict[str, Dict] = {
    "HINDUNILVR": {"name": "Hindustan Unilever", "site": "https://www.hul.co.in",
                   "wiki": "Hindustan_Unilever"},
    "ITC":        {"name": "ITC Limited", "site": "https://www.itcportal.com",
                   "wiki": "ITC_Limited"},
    "NESTLEIND":  {"name": "Nestle India", "site": "https://www.nestle.in",
                   "wiki": "Nestlé"},
    "BRITANNIA":  {"name": "Britannia Industries", "site": "https://www.britannia.co.in",
                   "wiki": "Britannia_Industries"},
    "DABUR":      {"name": "Dabur", "site": "https://www.dabur.com",
                   "wiki": "Dabur"},
}

MIN_PX = 128          # anything smaller is a favicon, not a logo
TARGET_PX = 512       # upscale ceiling for the stored copy


def _dims(content: bytes) -> Optional[Tuple[int, int]]:
    try:
        from PIL import Image
        return Image.open(io.BytesIO(content)).size
    except Exception:
        return None


def _fetch(url: str, timeout: int = 25) -> Optional[bytes]:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        if r.status_code == 200 and len(r.content) > 500:
            return r.content
    except Exception:
        pass
    return None


def wikimedia_candidates(wiki_title: str, name: str = "") -> List[str]:
    """Search Wikimedia Commons for the company's LOGO file.

    The pageimages API returns a page's lead image, which for Nestle is a
    photograph of its headquarters — a building, not a mark. Searching the
    File namespace for "<name> logo" returns the actual logo asset.
    """
    urls: List[str] = []
    query = f"{name or wiki_title} logo"
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={"action": "query", "generator": "search",
                    "gsrsearch": f"intitle:logo {name or wiki_title}",
                    "gsrnamespace": 6, "gsrlimit": 8,
                    "prop": "imageinfo", "iiprop": "url|size",
                    "iiurlwidth": TARGET_PX, "format": "json"},
            headers={"User-Agent": UA}, timeout=25)
        pages = (r.json().get("query", {}) or {}).get("pages") or {}
        scored = []
        for pg in pages.values():
            info = (pg.get("imageinfo") or [{}])[0]
            src = info.get("thumburl") or info.get("url")
            title = (pg.get("title") or "").lower()
            if not src:
                continue
            # Prefer SVG, and files whose name actually matches the company.
            score = 0
            if src.lower().endswith(".svg") or ".svg" in src.lower():
                score -= 2
            if all(w.lower() in title for w in (name or wiki_title).split()[:1]):
                score -= 1
            scored.append((score, src))
        scored.sort()
        urls = [u for _, u in scored]
    except Exception as e:
        print(f"    commons lookup failed: {str(e)[:90]}")
    return urls


def site_candidates(site: str) -> List[str]:
    """Pull image candidates off the company's own site via Firecrawl."""
    try:
        import firecrawl_client as fc
        if not fc.available():
            return []
        data = fc.scrape(site, formats=["markdown", "links"], only_main=False, timeout=60)
        if not data:
            return []
        html = (data.get("markdown") or "")
        found = re.findall(r"https?://[^\s\)\"']+\.(?:png|svg|webp|jpg|jpeg)", html, re.I)
        links = [u for u in (data.get("links") or [])
                 if re.search(r"\.(png|svg|webp|jpg|jpeg)$", u, re.I)]
        cands = found + links
        # Prefer anything that names itself a logo.
        cands.sort(key=lambda u: (0 if "logo" in u.lower() else 1, -len(u)))
        return cands[:8]
    except Exception as e:
        print(f"    site scrape failed: {str(e)[:90]}")
        return []


def best_logo(sym: str, meta: Dict) -> Optional[Tuple[bytes, str, Tuple[int, int]]]:
    tried = []
    for url in (site_candidates(meta["site"])
                + wikimedia_candidates(meta["wiki"], meta["name"])):
        if url in tried:
            continue
        tried.append(url)
        content = _fetch(url)
        if not content:
            continue
        ext = (re.search(r"\.(png|svg|webp|jpg|jpeg)", url, re.I) or [None, "png"])[1].lower()
        if ext == "svg":
            if b"<svg" in content[:2000]:
                print(f"    ✓ svg  {url[:78]}")
                return content, "svg", (0, 0)
            continue
        size = _dims(content)
        if not size or min(size) < MIN_PX:
            continue
        ratio = max(size) / max(min(size), 1)
        if ratio > 6:
            print(f"    · skipped {size[0]}x{size[1]} (banner-shaped, not a mark)")
            continue
        print(f"    ✓ {size[0]}x{size[1]}  {url[:70]}")
        return content, ext, size
    return None


def save(sym: str, content: bytes, ext: str, size) -> str:
    os.makedirs(LOGO_DIR, exist_ok=True)
    # Clear other extensions so resolution is unambiguous.
    for e in ("png", "webp", "jpg", "jpeg"):
        p = os.path.join(LOGO_DIR, f"{sym}.{e}")
        if os.path.exists(p) and e != ext:
            os.remove(p)

    path = os.path.join(LOGO_DIR, f"{sym}.{ext}")
    if ext != "svg" and size and max(size) > TARGET_PX:
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(content))
            im.thumbnail((TARGET_PX, TARGET_PX), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            content, path = buf.getvalue(), os.path.join(LOGO_DIR, f"{sym}.png")
        except Exception:
            pass
    with open(path, "wb") as f:
        f.write(content)
    return path


if __name__ == "__main__":
    only = sys.argv[1].upper() if len(sys.argv) > 1 else None
    for sym, meta in COMPANIES.items():
        if only and sym != only:
            continue
        existing = [e for e in ("svg", "png", "webp", "jpg", "jpeg")
                    if os.path.exists(os.path.join(LOGO_DIR, f"{sym}.{e}"))]
        print(f"\n{sym} — {meta['name']}" + (f"  (have: {existing})" if existing else ""))
        got = best_logo(sym, meta)
        if not got:
            print("    no suitable logo found; leaving existing/fallback in place")
            continue
        content, ext, size = got
        path = save(sym, content, ext, size)
        print(f"    saved {os.path.basename(path)}  {os.path.getsize(path):,} bytes")

    print("\nlocal logos now:")
    for f in sorted(os.listdir(LOGO_DIR)):
        print(f"  {f:22s} {os.path.getsize(os.path.join(LOGO_DIR, f)):>8,} bytes")
