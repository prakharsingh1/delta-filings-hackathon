"""Scrape company-published brand images from their official websites.

We hit the company's homepage and look for, in order:
  1. <link rel="apple-touch-icon">  — iOS home-screen icon, typically a
     clean branded square at 180x180+. The closest thing most companies
     publish to a "profile picture".
  2. <meta property="og:image">     — the share-image. Sometimes the logo
     on brand color, sometimes a product shot. Decent fallback.
  3. <link rel="icon"> with explicit sizes >= 64 — high-res favicon.
  4. <link rel="icon" type="image/svg+xml"> — vector favicon.
  5. <link rel="shortcut icon">      — last resort favicon.

Results are cached in-memory for a week to avoid re-scraping. The /logo/<symbol>
endpoint redirects to the discovered URL so the browser fetches the image
directly (no proxying through our server)."""

import re
import struct
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

from company_logos import SYMBOL_DOMAIN_MAP, SYMBOL_LOGO_OVERRIDES


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Reject any raster image smaller than this on its longer edge — anything
# below is just a 16/32-px favicon, not a real logo.
_MIN_DIM = 48

# symbol -> (timestamp, meta-dict-or-empty-dict)
_CACHE: Dict[str, Tuple[float, Dict]] = {}
_CACHE_TTL = 7 * 24 * 3600         # success: re-check after a week
_NEGATIVE_TTL = 6 * 3600           # failure: re-try in 6h (site might come back)


def _attr(tag: str, name: str) -> Optional[str]:
    """Pull a quoted attribute value out of a tag fragment."""
    m = re.search(rf'{name}\s*=\s*"([^"]+)"', tag, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(rf"{name}\s*=\s*'([^']+)'", tag, re.I)
    if m:
        return m.group(1).strip()
    return None


def _parse_size(sizes: str) -> int:
    """Parse '192x192' or '180x180 152x152' into the biggest pixel dim."""
    if not sizes:
        return 0
    best = 0
    for tok in sizes.split():
        m = re.match(r"(\d+)x(\d+)", tok)
        if m:
            v = int(m.group(1))
            if v > best:
                best = v
    return best


_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.I)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_SVG_WH_RE = re.compile(rb'<svg\b[^>]*\bwidth="([\d.]+)[^"]*"[^>]*\bheight="([\d.]+)', re.I)
_SVG_VB_RE = re.compile(rb'<svg\b[^>]*\bviewBox="[\d.\-\s]*\s+([\d.]+)\s+([\d.]+)"', re.I)


def _read_image_dims(content: bytes, content_type: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse the first chunk of an image to extract width/height. Supports
    PNG, JPEG, WebP, GIF, and SVG. Returns (None, None) if unparseable."""
    ct = (content_type or "").lower()

    # PNG: 8-byte signature + IHDR chunk (length=4, type=4, w=4, h=4)
    if content[:8] == b"\x89PNG\r\n\x1a\n" and len(content) >= 24:
        w, h = struct.unpack(">II", content[16:24])
        return w, h

    # GIF: header GIF87a/GIF89a + 2-byte LE width + 2-byte LE height at offset 6
    if content[:6] in (b"GIF87a", b"GIF89a") and len(content) >= 10:
        w, h = struct.unpack("<HH", content[6:10])
        return w, h

    # WebP (VP8/VP8L/VP8X). Layout: RIFF...WEBP at 0..11, then chunk.
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP" and len(content) >= 30:
        chunk = content[12:16]
        if chunk == b"VP8 ":
            # Lossy: width/height at offset 26..30 (14 bits each, LE)
            w = struct.unpack("<H", content[26:28])[0] & 0x3FFF
            h = struct.unpack("<H", content[28:30])[0] & 0x3FFF
            return w, h
        if chunk == b"VP8L":
            b1, b2, b3, b4 = content[21], content[22], content[23], content[24]
            w = 1 + (((b2 & 0x3F) << 8) | b1)
            h = 1 + (((b4 & 0x0F) << 10) | (b3 << 2) | ((b2 & 0xC0) >> 6))
            return w, h
        if chunk == b"VP8X" and len(content) >= 30:
            w = 1 + (content[24] | (content[25] << 8) | (content[26] << 16))
            h = 1 + (content[27] | (content[28] << 8) | (content[29] << 16))
            return w, h

    # JPEG: scan SOF markers
    if content[:2] == b"\xff\xd8":
        i = 2
        while i < len(content) - 9:
            if content[i] != 0xFF:
                i += 1
                continue
            marker = content[i + 1]
            i += 2
            # Standalone markers (no payload): D0..D9, 01
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
                continue
            if i + 1 >= len(content):
                break
            seg_len = struct.unpack(">H", content[i:i + 2])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                if i + 7 > len(content):
                    break
                h = struct.unpack(">H", content[i + 3:i + 5])[0]
                w = struct.unpack(">H", content[i + 5:i + 7])[0]
                return w, h
            i += seg_len

    # SVG: explicit width/height in pixels, or viewBox dims
    if "svg" in ct or content[:5].lstrip().startswith(b"<svg") or b"<svg" in content[:200]:
        m = _SVG_WH_RE.search(content)
        if m:
            try:
                return int(float(m.group(1))), int(float(m.group(2)))
            except ValueError:
                pass
        m = _SVG_VB_RE.search(content)
        if m:
            try:
                return int(float(m.group(1))), int(float(m.group(2)))
            except ValueError:
                pass
        # Vector — treat as effectively unlimited resolution
        return 9999, 9999

    return None, None


def _validate_image(url: str) -> Optional[Dict]:
    """Fetch enough of the image to confirm it's real and big enough.
    Returns a dict with type/width/height/bytes if valid, None otherwise."""
    try:
        r = requests.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=8,
            allow_redirects=True, stream=True,
        )
        if r.status_code != 200:
            return None
        ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ct and not ct.startswith("image/"):
            # Some CDNs serve images as application/octet-stream — only reject
            # explicit non-image types (text/html, application/json, etc).
            if ct.startswith(("text/", "application/json")):
                return None
        # Read just enough to read headers — most formats reveal dims in <40KB.
        chunk = b""
        for piece in r.iter_content(8192):
            chunk += piece
            if len(chunk) >= 65536:
                break
    except requests.RequestException:
        return None

    if not chunk:
        return None

    w, h = _read_image_dims(chunk, ct)
    if w is None:
        # Couldn't parse — accept defensively if content-type was image/*
        if ct.startswith("image/"):
            return {"ok": True, "type": ct, "width": None, "height": None, "bytes": len(chunk)}
        return None

    longer = max(w, h)
    return {
        "ok": longer >= _MIN_DIM,
        "type": ct or "image/?",
        "width": w,
        "height": h,
        "bytes": len(chunk),
    }


def _candidates(html: str, base_url: str) -> list:
    """Pull all logo candidates from the page, in our priority order.
    Returns a list of (source_label, url) tuples so callers can iterate
    and validate each — letting us skip the apple-touch-icon if it's
    actually a 16x16 favicon, for example."""
    apple_icons = []   # (size, url)
    plain_icons = []   # (size, url, is_svg)
    og_image: Optional[str] = None

    for tag in _LINK_TAG_RE.findall(html):
        rel = (_attr(tag, "rel") or "").lower()
        href = _attr(tag, "href")
        if not href:
            continue
        sizes = _parse_size(_attr(tag, "sizes") or "")
        href_abs = urljoin(base_url, href)
        is_svg = "svg" in (_attr(tag, "type") or "").lower() or href_abs.lower().endswith(".svg")

        if "apple-touch-icon" in rel:
            apple_icons.append((sizes or 180, href_abs))
        elif rel in ("icon", "shortcut icon"):
            plain_icons.append((sizes, href_abs, is_svg))

    for tag in _META_TAG_RE.findall(html):
        prop = (_attr(tag, "property") or _attr(tag, "name") or "").lower()
        if prop in ("og:image", "og:image:url", "og:image:secure_url"):
            content = _attr(tag, "content")
            if content:
                og_image = urljoin(base_url, content)
                break

    out = []
    apple_icons.sort(key=lambda x: -x[0])
    for _, url in apple_icons:
        out.append(("apple-touch-icon", url))
    if og_image:
        out.append(("og:image", og_image))
    for _, url, is_svg in plain_icons:
        if is_svg:
            out.append(("icon-svg", url))
            break
    plain_icons_raster = [(s, u) for s, u, sv in plain_icons if not sv]
    plain_icons_raster.sort(key=lambda x: -(x[0] or 0))
    for _, url in plain_icons_raster:
        out.append(("icon", url))
    return out


def discover_logo_with_meta(symbol: str) -> Dict:
    """Return the best brand-image we can find for a symbol's website,
    along with its provenance and pixel dimensions so the caller (or a
    diagnostic page) can verify quality. Cached."""
    if not symbol:
        return {}
    sym = symbol.upper()

    # Manual overrides win over everything — hand-curated URLs for symbols
    # where scraping picked a wrong/poor image (factory photos, tiny icons).
    if sym in SYMBOL_LOGO_OVERRIDES:
        return {
            "symbol": sym,
            "domain": SYMBOL_DOMAIN_MAP.get(sym, ""),
            "url": SYMBOL_LOGO_OVERRIDES[sym],
            "source": "override",
        }

    now = time.time()
    cached = _CACHE.get(sym)
    if cached:
        ts, meta = cached
        ttl = _CACHE_TTL if meta.get("url") else _NEGATIVE_TTL
        if now - ts < ttl:
            return meta

    domain = SYMBOL_DOMAIN_MAP.get(sym)
    if not domain:
        meta = {"symbol": sym, "domain": "", "url": "", "error": "no-domain-mapped"}
        _CACHE[sym] = (now, meta)
        return meta

    home = f"https://{domain}"
    try:
        r = requests.get(
            home,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            timeout=8,
            allow_redirects=True,
        )
        if r.status_code != 200 or not r.text:
            meta = {"symbol": sym, "domain": domain, "url": "",
                    "error": f"home-status-{r.status_code}"}
            _CACHE[sym] = (now, meta)
            return meta
        html_head = r.text[:200_000]
        base = r.url or home
    except requests.RequestException as e:
        meta = {"symbol": sym, "domain": domain, "url": "",
                "error": f"home-fetch-{type(e).__name__}"}
        _CACHE[sym] = (now, meta)
        return meta

    candidates = _candidates(html_head, base)
    rejected = []
    for source, url in candidates:
        v = _validate_image(url)
        if v and v.get("ok"):
            meta = {
                "symbol": sym, "domain": domain, "url": url,
                "source": source, "type": v.get("type"),
                "width": v.get("width"), "height": v.get("height"),
                "bytes": v.get("bytes"), "rejected": rejected,
            }
            _CACHE[sym] = (now, meta)
            return meta
        if v:
            rejected.append({"source": source, "url": url,
                             "width": v.get("width"), "height": v.get("height"),
                             "reason": "too-small"})
        else:
            rejected.append({"source": source, "url": url, "reason": "fetch-or-parse-failed"})

    meta = {"symbol": sym, "domain": domain, "url": "",
            "error": "no-valid-candidate", "rejected": rejected}
    _CACHE[sym] = (now, meta)
    return meta


def discover_logo(symbol: str) -> Optional[str]:
    """Convenience wrapper — just the URL string, or None."""
    meta = discover_logo_with_meta(symbol)
    return meta.get("url") or None
