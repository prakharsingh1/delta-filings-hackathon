import os
import tempfile
import requests
from PyPDF2 import PdfReader

MAX_CHARS = 15000  # Extract more text for thorough word-for-word view

# Cache downloaded PDFs to avoid re-downloading
PDF_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".pdf_cache")
os.makedirs(PDF_CACHE_DIR, exist_ok=True)


def _cache_path(pdf_url):
    """Generate a cache file path for a given URL."""
    import hashlib
    url_hash = hashlib.md5(pdf_url.encode()).hexdigest()
    return os.path.join(PDF_CACHE_DIR, f"{url_hash}.pdf")


def download_and_extract(pdf_url):
    """Download a PDF and extract full text. Uses local cache to avoid re-downloading."""
    if not pdf_url:
        print("[PDF] No PDF URL provided.")
        return ""

    # Check cache first
    cached = _cache_path(pdf_url)
    if os.path.exists(cached):
        print(f"[PDF] Using cached: {cached}")
        return extract_text_from_file(cached)

    try:
        print(f"[PDF] Downloading: {pdf_url}")
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://www.nseindia.com/",
        }
        resp = requests.get(pdf_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"[PDF] Download failed with status {resp.status_code}")
            return ""

        # Save to cache
        with open(cached, "wb") as f:
            f.write(resp.content)

        return extract_text_from_file(cached)

    except Exception as e:
        print(f"[PDF] Error processing PDF: {e}")
        return ""


def extract_text_from_file(file_path):
    """Extract full text from a local PDF file."""
    try:
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
            if len(text) >= MAX_CHARS:
                break
        result = text[:MAX_CHARS].strip()
        print(f"[PDF] Extracted {len(result)} characters from {os.path.basename(file_path)}")
        return result
    except Exception as e:
        print(f"[PDF] Text extraction failed: {e}")
        return ""


def extract_xbrl_from_pdf(file_path):
    """Attempt to extract embedded XBRL/XML data from a PDF.
    Many NSE filings embed XBRL as attached files within the PDF."""
    try:
        reader = PdfReader(file_path)
        # Check for embedded files (XBRL is often attached)
        if "/Names" in reader.trailer.get("/Root", {}):
            pass  # PyPDF2 doesn't easily access embedded files

        # Try catalog for embedded files
        catalog = reader.trailer["/Root"]
        if hasattr(catalog, "get_object"):
            catalog = catalog.get_object()

        names = catalog.get("/Names", {})
        if hasattr(names, "get_object"):
            names = names.get_object()

        ef = names.get("/EmbeddedFiles", {})
        if hasattr(ef, "get_object"):
            ef = ef.get_object()

        name_list = ef.get("/Names", [])
        xbrl_data = []
        for i in range(0, len(name_list), 2):
            name = str(name_list[i])
            if any(ext in name.lower() for ext in [".xml", ".xbrl", ".xsd"]):
                file_obj = name_list[i + 1].get_object()
                stream = file_obj.get("/EF", {}).get("/F", None)
                if stream:
                    data = stream.get_object().get_data().decode("utf-8", errors="replace")
                    xbrl_data.append({"name": name, "content": data[:5000]})

        return xbrl_data
    except Exception as e:
        print(f"[XBRL] Extraction failed: {e}")
        return []


def parse_xbrl_content(xml_text):
    """Parse XBRL XML and extract key-value facts."""
    try:
        from lxml import etree
        root = etree.fromstring(xml_text.encode("utf-8"))

        facts = []
        for elem in root.iter():
            # XBRL facts have text content and a tag
            if elem.text and elem.text.strip():
                tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                # Skip generic wrapper elements
                if tag_name.lower() in ("xbrl", "html", "body", "head", "title"):
                    continue
                context = elem.get("contextRef", "")
                facts.append({
                    "field": tag_name,
                    "value": elem.text.strip()[:200],
                    "context": context,
                })

        return facts[:50]  # Limit to 50 most relevant facts
    except Exception as e:
        print(f"[XBRL] Parse failed: {e}")
        return []
