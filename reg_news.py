"""Regulatory news radar — SEBI and RBI updates, tagged by topic.

Sources are the regulators' own RSS feeds (SEBI's covers circulars, orders,
recovery proceedings and press releases; RBI publishes notifications and
press releases separately). MCA's site blocks automated access, so MCA
updates are not in the feed.

Each item gets a kind (circular, order, penalty…) and subject topics.
Tagging is keyword-based and deliberately conservative:

  * Topics come from the title, not the body. RBI circulars open with an
    addressee list ("Commercial Banks, … Non-Banking Financial Companies")
    that says who must comply, not what the circular is about.
  * RBI reference numbers carry the issuing department (DOR.AML, A.P. (DIR
    Series), DPSS…), which is a reliable subject signal, so it is read too.
  * Short keywords match whole words only ("aml" must not fire on
    "Kamleshkumar").
  * Recovery proceedings are tagged Enforcement only, and daily market
    operations (VRRR, OMO, money market figures) only Market operations.

`relevant_topics` decides what a user sees under "For you" from their role
and their entities' profiles. The stored SEBI orders double as a
counterparty screen in risk.py.
"""
from __future__ import annotations

import html
import re
import threading
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup

from database import get_connection
from demo_cache import frozen as _frozen

FEEDS = [
    {"source": "SEBI", "url": "https://www.sebi.gov.in/sebirss.xml"},
    {"source": "RBI", "url": "https://www.rbi.org.in/notifications_rss.xml", "kind": "notification"},
    {"source": "RBI", "url": "https://www.rbi.org.in/pressreleases_rss.xml", "kind": "press release"},
]
SOURCES = ("SEBI", "RBI")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REFRESH_EVERY = timedelta(hours=2)
RETRY_AFTER_FAILURE = timedelta(minutes=10)
# Bump when tagging rules change; init_news_db re-tags stored rows once.
TAGGER_VERSION = "4"

KIND_LABELS = {
    "circular": "Circular",
    "master circular": "Master circular",
    "regulation": "Regulation",
    "legal update": "Legal update",
    "notification": "Notification",
    "consultation": "Consultation",
    "order": "Order",
    "recovery": "Recovery",
    "penalty": "Penalty",
    "action": "Regulatory action",
    "press release": "Press release",
    "offer document": "Offer document",
    "report": "Report",
    "update": "Update",
}
CIRCULAR_KINDS = ("circular", "master circular", "regulation", "legal update", "notification")
ENFORCEMENT_KINDS = ("order", "recovery", "penalty", "action")

# Topic -> keyword patterns (regex fragments, matched against the lowercased
# title with a leading word boundary). A fragment without a trailing \b is a
# prefix match on purpose ("restructur" covers restructuring/restructured).
# Labels must not contain commas: topics are stored comma-separated.
TOPICS = {
    "Policy updates": [r"key decisions", r"sebi board meeting", r"statement on developmental and regulatory",
                       r"monetary policy statement", r"governor'?s statement"],
    "LODR": [r"listing obligations", r"lodr\b", r"listed entit", r"listed compan", r"minimum public shareholding",
             r"mps\b", r"corporate announcements?", r"shareholders?'? meetings?", r"general meetings?"],
    "Insider trading": [r"insider trading", r"prohibition of insider", r"unpublished price sensitive", r"upsi\b",
                        r"trading window", r"structured digital database", r"sdd\b", r"trading plans?\b", r"pit\b"],
    "Takeovers and M&A": [r"takeovers?\b", r"substantial acquisition", r"open offers?\b", r"sast\b",
                          r"schemes? of arrangement", r"amalgamation", r"mergers?\b", r"demergers?\b",
                          r"exemption orders?\b"],
    "Buybacks and delisting": [r"buy[- ]?backs?\b", r"delist"],
    "Capital raising": [r"icdr\b", r"issue of capital", r"public issues?\b", r"ipos?\b", r"initial public offer",
                        r"further public offer", r"rights issues?\b", r"qips?\b", r"qualified institutions? placement",
                        r"preferential (issue|allotment)", r"red herring", r"offer documents?\b", r"sme\b",
                        r"bonus issues?\b", r"anchor investors?\b", r"social stock exchange"],
    "Debt markets": [r"non-convertible", r"debt securities", r"commercial papers?\b", r"bonds?\b", r"debentures?\b",
                     r"municipal debt", r"securitised debt", r"ncs\b", r"certificates? of deposit"],
    "Mutual funds": [r"mutual funds?\b", r"asset management compan", r"amcs?\b", r"etfs?\b", r"index funds?\b",
                     r"specialized investment fund"],
    "AIF / REIT / InvIT": [r"alternative investment funds?\b", r"aifs?\b", r"reits?\b", r"invits?\b",
                           r"real estate investment trust", r"infrastructure investment trust", r"venture capital",
                           r"angel funds?\b", r"accredited investors?\b"],
    "Intermediaries": [r"stock ?brokers?\b", r"intermediar", r"depository participants?\b", r"investment advis[eo]rs?\b",
                       r"research analysts?\b", r"portfolio managers?\b", r"registrars? to an issue",
                       r"share transfer agents?\b", r"rtas?\b", r"credit rating agenc", r"merchant bankers?\b",
                       r"kyc registration agenc", r"custodians?\b", r"debenture trustees?\b", r"bankers to an issue"],
    "Exchanges and depositories": [r"stock exchanges?\b", r"depositories\b", r"depository system",
                                   r"clearing corporations?\b", r"market infrastructure institutions?\b", r"miis?\b"],
    "Trading and derivatives": [r"derivatives?\b", r"futures and options", r"f&o\b", r"algorithmic trading",
                                r"algo\b", r"trading hours", r"settlement cycle", r"t\+[01]\b", r"short selling",
                                r"securities lending", r"block deals?\b", r"margin trading", r"margin obligations",
                                r"position limits?\b", r"interest rate futures"],
    "Market abuse": [r"fraudulent and unfair", r"pfutp\b", r"front[- ]?running", r"manipulat", r"finfluencers?\b",
                     r"mule accounts?\b", r"pump and dump", r"price rigging", r"synchroni[sz]ed trad",
                     r"circular trad", r"unsolicited"],
    "ESG and BRSR": [r"brsr\b", r"business responsibility", r"esg\b", r"sustainab", r"green (debt|bonds?|deposits?)\b",
                     r"climate", r"carbon"],
    "Governance and audit": [r"corporate governance", r"independent directors?\b", r"board of directors",
                             r"statutory (central )?auditors?\b", r"statutory audit", r"auditors?\b",
                             r"audit committee", r"internal audit", r"related party", r"fit and proper",
                             r"key managerial", r"whole[- ]time directors?\b", r"chief compliance officer",
                             r"compliance function", r"chair and meetings of the board"],
    "Grievance redress": [r"grievances?\b", r"(?<!esg )scores\b",r"(online )?dispute resolution", r"resolution of disputes",
                          r"odr\b", r"ombudsman",
                          r"customer service", r"investor charter", r"complaints?\b", r"customer protection",
                          r"unauthori[sz]ed electronic banking", r"fair practices? code", r"investor protection"],
    "Enforcement": [r"order in the matter", r"adjudication orders?\b", r"settlement orders?\b",
                    r"recovery certificates?\b", r"prohibitory orders?\b", r"show cause", r"interim orders?\b",
                    r"final orders?\b", r"confirmatory orders?\b", r"remittance orders?\b", r"attachment",
                    r"debarred", r"restrain", r"monetary penalty", r"imposes (a )?penalty", r"penalty on\b"],
    "FEMA": [r"foreign exchange", r"fema\b", r"external commercial borrowings?\b", r"ecbs?\b",
             r"overseas (direct )?investment", r"odi\b", r"fdi\b", r"foreign direct investment",
             r"liberali[sz]ed remittance", r"lrs\b", r"foreign investment", r"foreign portfolio investors?\b",
             r"non-residents?\b", r"nris?\b", r"authori[sz]ed dealers?\b", r"rupee drawing arrangement",
             r"vostro", r"trade credits?\b", r"fcnr", r"line of credit", r"export of goods", r"import of goods"],
    "Banking": [r"banks?\b", r"banking\b", r"all india financial institutions?\b", r"aifis?\b"],
    "NBFC": [r"nbfcs?\b", r"nbfc-", r"nonbanking financial", r"housing finance compan", r"asset reconstruction",
             r"arcs?\b", r"core investment compan", r"cics?\b", r"microfinance institutions?\b"],
    "KYC / AML": [r"kyc\b", r"ckyc\b", r"know your customer", r"money laundering", r"pmla\b", r"aml\b", r"cft\b",
                  r"anti-money", r"terroris[tm]", r"unlawful activities", r"uapa\b", r"sanctions? lists?\b",
                  r"designated (individuals|entities|persons)", r"weapons of mass destruction", r"wmd\b",
                  r"fatf\b", r"customer due diligence", r"customer identification", r"beneficial owner"],
    "Payments": [r"payment systems?\b", r"payment and settlement", r"upi\b", r"prepaid payment", r"ppis?\b",
                 r"(credit|debit|prepaid) cards?\b", r"digital payments?\b", r"payment aggregators?\b",
                 r"payment gateways?\b", r"neft\b", r"rtgs\b", r"imps\b", r"bbps\b", r"bharat bill", r"e-mandates?\b",
                 r"tokeni[sz]ation", r"cross-border payments?\b", r"fastag", r"nach\b", r"cheque truncation",
                 r"positive pay"],
    "Lending": [r"(?<!securities )lending", r"loans?\b", r"advances\b", r"npas?\b", r"non-performing", r"restructur",
                r"wilful defaulters?\b", r"frauds?\b", r"priority sector", r"psl\b", r"credit information",
                r"gold loans?\b", r"microfinance", r"co-lending", r"stressed assets?\b", r"msmes?\b",
                r"recovery agents?\b", r"penal charges", r"credit facilit", r"securiti[sz]ation",
                r"transfer of loan exposures"],
    "Prudential norms": [r"capital adequacy", r"minimum capital requirements?\b", r"basel\b", r"market risk",
                         r"credit risk", r"operational risk", r"liquidity coverage", r"lcr\b", r"nsfr\b",
                         r"net stable funding", r"leverage ratio", r"investment portfolio", r"prudential",
                         r"provisioning", r"asset classification", r"large exposures?\b", r"exposure norms",
                         r"countercyclical", r"expected credit loss", r"ecl\b", r"income recognition",
                         r"risk weights?\b", r"capital (charge|buffer|conservation)"],
    "Monetary policy": [r"repo rate", r"monetary policy", r"cash reserve ratio", r"crr\b",
                        r"statutory liquidity ratio", r"slr\b", r"policy rate", r"mpc\b", r"bank rate"],
    "Market operations": [r"money market operations", r"variable rate (reverse )?repo", r"vrrr?\b",
                          r"open market operations?\b", r"omo\b", r"liquidity adjustment facility", r"laf\b",
                          r"treasury bills?\b", r"t-bills?\b", r"dated securities", r"auction of (government|state)",
                          r"sdls?\b", r"state development loans?\b", r"underwriting auction", r"switch operations?\b",
                          r"conversion/switch", r"buy-?back of government", r"sovereign gold bonds?\b",
                          r"weekly statistical supplement", r"reserve money", r"money supply", r"cut-offs?\b",
                          r"lending and deposit rates",
                          r"auction results?\b"],
    "Cyber and IT": [r"cyber", r"information technology", r"it (governance|outsourcing|services|risk|framework|systems?)\b",
                     r"cscrf\b", r"outsourcing", r"cloud services?\b", r"data locali[sz]ation", r"business continuity",
                     r"technology risk", r"operational resilience"],
    "Currency": [r"banknotes?\b", r"coins?\b", r"currency chests?\b", r"soiled notes?\b", r"legal tender",
                 r"currency management"],
}
_TOPIC_RE = {t: re.compile(r"\b(?:" + "|".join(words) + ")") for t, words in TOPICS.items()}

# Routine market-operations items keep only these topics, so a VRRR result
# never shows up as "Debt markets" or "Buybacks" (RBI buys back G-secs).
_MARKET_OPS_KEEP = {"Market operations", "Monetary policy", "Policy updates"}

# RBI reference numbers name the issuing department — a cleaner subject
# signal than the body text. Matched on the first part of the summary.
_RBI_DEPT_TOPICS = [
    (re.compile(r"\bdor\.aml\b|\.aml\.rec\b"), "KYC / AML"),
    (re.compile(r"a\.\s?p\.\s?\(dir series\)|\bfed\.|notification no\. fema"), "FEMA"),
    (re.compile(r"\bdor\.(mrg|cap)\b"), "Prudential norms"),
    (re.compile(r"\bdor\.cre\b|\bfidd\b"), "Lending"),
    (re.compile(r"dpss"), "Payments"),
    (re.compile(r"\bfmrd\b"), "Trading and derivatives"),
    (re.compile(r"\bcepd\b"), "Grievance redress"),
    (re.compile(r"\bdcm\b"), "Currency"),
    (re.compile(r"\bdor\.nbfc\b|\bdnbr\b|\bdnbs\b|/03\.10\.\d"), "NBFC"),
]
_RBI_SALUTATION = re.compile(r"(madam\s*/\s*dear sir|dear sir\s*/\s*madam|madam\s*/\s*sir|sir\s*/\s*madam|dear sir)\s*[,:]?",
                             re.I)
_RBI_PENALTY = re.compile(r"monetary penalty|imposes (a )?penalty|penalty on\b")
_RBI_ACTION = re.compile(r"cancels? (the )?(certificate of registration|licen[cs]e)|"
                         r"cancellation of (the )?(certificate of registration|licen[cs]e)|section 35\s?a\b|"
                         r"all[- ]inclusive directions|business restrictions|supersedes? the board")
_CONSULTATION = re.compile(r"\bdraft\b|consultation paper|discussion paper|comments? (are )?invited|"
                           r"seeks? (public )?comments")

_fetch_lock = threading.Lock()
_last_failure = None  # when a refresh last failed on every feed


def init_news_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS regulatory_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            summary TEXT DEFAULT '',
            topics TEXT DEFAULT '',
            published_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_news_pub ON regulatory_news(published_at DESC);
        CREATE TABLE IF NOT EXISTS regulatory_news_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    row = conn.execute("SELECT value FROM regulatory_news_meta WHERE key = 'tagger_version'").fetchone()
    conn.commit()
    conn.close()
    if not row or row["value"] != TAGGER_VERSION:
        retag_all()


def retag_all():
    """Re-derive kind and topics for every stored item with the current rules."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, source, kind, title, url, summary, topics FROM regulatory_news").fetchall()
        for r in rows:
            kind = classify_kind(r["source"], r["url"], r["title"], r["kind"])
            topics = ",".join(tag_item(r["source"], kind, r["title"], r["summary"]))
            if kind != r["kind"] or topics != (r["topics"] or ""):
                conn.execute("UPDATE regulatory_news SET kind = ?, topics = ? WHERE id = ?", (kind, topics, r["id"]))
        conn.execute("INSERT OR REPLACE INTO regulatory_news_meta (key, value) VALUES ('tagger_version', ?)",
                     (TAGGER_VERSION,))
        conn.commit()
    finally:
        conn.close()


def _fix_url(url):
    # SEBI's feed sometimes doubles the host: https://www.sebi.gov.in/https://www.sebi.gov.in/...
    url = (url or "").strip()
    m = re.search(r"https?://[^ ]*?(https?://.*)$", url)
    return m.group(1) if m else url


# ── Kinds ───────────────────────────────────────────────────────────────────

def _sebi_kind(url, title):
    u = url.lower()
    t = title.lower()
    if "consultation paper" in t:
        return "consultation"
    for frag, kind in (("/legal/master-circulars/", "master circular"), ("/legal/circulars/", "circular"),
                       ("/legal/regulations/", "regulation"), ("/enforcement/orders/", "order"),
                       ("/enforcement/recovery-proceedings/", "recovery"), ("/press-releases/", "press release"),
                       ("/filings/", "offer document"), ("/reports", "report"),
                       ("/legal/", "legal update")):
        if frag in u:
            return kind
    # Attachments (PDFs under /sebi_data/attachdocs/) carry no section in the URL.
    if "recovery certificate" in t:
        return "recovery"
    if re.search(r"\border\b", t):
        return "order"
    if "master circular" in t:
        return "master circular"
    if "circular" in t:
        return "circular"
    if "press release" in t or "key decisions" in t:
        return "press release"
    return "update"


def _rbi_kind(url, title, feed_kind=None):
    t = title.lower()
    if _RBI_PENALTY.search(t):
        return "penalty"
    if _RBI_ACTION.search(t):
        return "action"
    if _CONSULTATION.search(t):
        return "consultation"
    u = url.lower()
    if "notificationuser" in u:
        return "notification"
    if "pressrelease" in u:
        return "press release"
    return feed_kind if feed_kind in ("notification", "press release") else "update"


def classify_kind(source, url, title, feed_kind=None):
    if source == "SEBI":
        return _sebi_kind(url or "", title or "")
    if source == "RBI":
        return _rbi_kind(url or "", title or "", feed_kind)
    return feed_kind or "update"


# ── Topics ──────────────────────────────────────────────────────────────────

def _prep(text):
    t = (text or "").lower()
    t = t.replace("’", "'").replace("–", "-").replace("—", "-")
    # "Reserve Bank of India (Payments Banks - …)" is the issuer, not the subject.
    t = re.sub(r"\breserve bank of india\b|\breserve bank\b|\brbi\b", " ", t)
    t = re.sub(r"\bnon[- ]banking\b", "nonbanking", t)
    t = re.sub(r"\bbank notes?\b", "banknotes", t)
    t = re.sub(r"\bexim bank\b", "exim", t)
    return t


def tag_topics(text):
    """Topics whose keywords appear in `text`, in TOPICS order."""
    t = _prep(text)
    return [topic for topic, rx in _TOPIC_RE.items() if rx.search(t)]


def _rbi_body(summary):
    """RBI summary minus the reference line and addressee list."""
    s = summary or ""
    m = _RBI_SALUTATION.search(s[:600])
    return s[m.end():] if m else s


def tag_item(source, kind, title, summary=""):
    """Subject topics for one item. See the module docstring for the rules."""
    if kind == "recovery":
        return ["Enforcement"]
    found = set(tag_topics(title))
    if source == "RBI":
        head = (summary or "")[:260].lower()
        for rx, topic in _RBI_DEPT_TOPICS:
            if rx.search(head):
                found.add(topic)
        if not found:
            found.update(tag_topics(_rbi_body(summary)[:320]))
    exemption = bool(re.match(r"\s*exemption orders?\b", (title or "").lower()))
    if kind in ENFORCEMENT_KINDS and not exemption:
        found.add("Enforcement")
    if exemption:
        found.discard("Enforcement")
    if "Market operations" in found:
        found &= _MARKET_OPS_KEEP
    return [t for t in TOPICS if t in found]


def clean_excerpt(title, summary):
    """A readable one- or two-line excerpt, or '' when the summary adds nothing
    (SEBI repeats the title; RBI market data is a table flattened to text)."""
    s = re.sub(r"\s+", " ", _rbi_body(summary or "")).strip()
    t = re.sub(r"\s+", " ", title or "").strip()
    # RBI bodies restate the title after the reference line; start after it.
    i = s.lower().find(t.lower()) if t else -1
    if 0 <= i < 600:
        s = s[i + len(t):]
    s = s.lstrip(" .,:;-\u2013\u2014")
    if len(s) < 40 or s.lower().startswith("table of contents"):
        return ""
    digits = sum(ch.isdigit() for ch in s[:300])
    if digits > 0.18 * min(len(s), 300):
        return ""
    return s[:320]


# ── Fetch ───────────────────────────────────────────────────────────────────

def _parse_date(s):
    s = (s or "").strip()
    for fn in (lambda x: parsedate_to_datetime(x),
               lambda x: datetime.strptime(x.split(" +")[0], "%d %b, %Y"),
               lambda x: datetime.strptime(x[:19], "%Y-%m-%dT%H:%M:%S")):
        try:
            d = fn(s)
            return d.replace(tzinfo=None)
        except Exception:
            continue
    return datetime.now()


def parse_feed(xml_text, source, default_kind=None):
    soup = BeautifulSoup(xml_text, "xml")
    out = []
    for it in soup.find_all("item"):
        title = re.sub(r"\s+", " ", html.unescape(it.title.get_text(strip=True) if it.title else "")).strip()
        link = _fix_url(it.link.get_text(strip=True) if it.link else "")
        if not title or not re.match(r"https?://", link, re.I):
            continue
        desc = BeautifulSoup(it.description.get_text() if it.description else "", "html.parser").get_text(" ", strip=True)
        kind = classify_kind(source, link, title, default_kind)
        out.append({
            "source": source, "kind": kind, "title": title[:400], "url": link[:600],
            "summary": desc[:1000], "topics": ",".join(tag_item(source, kind, title, desc)),
            "published_at": _parse_date(it.pubDate.get_text() if it.pubDate else "").isoformat(timespec="seconds"),
        })
    return out


def _insert(conn, items, fetched_at):
    new = 0
    for i in items:
        cur = conn.execute(
            """INSERT OR IGNORE INTO regulatory_news (source, kind, title, url, summary, topics,
                   published_at, fetched_at) VALUES (?,?,?,?,?,?,?,?)""",
            (i["source"], i["kind"], i["title"], i["url"], i.get("summary", ""), i.get("topics", ""),
             i["published_at"], fetched_at))
        new += cur.rowcount
    return new


def store_items(items):
    """Insert parsed items (see parse_feed); returns how many were new."""
    conn = get_connection()
    try:
        new = _insert(conn, items, datetime.now().isoformat(timespec="seconds"))
        conn.commit()
    finally:
        conn.close()
    return new


def refresh(force=False):
    """Pull every feed. Skips if refreshed in the last two hours unless forced.
    Returns number of new items. Never calls out in frozen demo mode."""
    global _last_failure
    if _frozen():
        return 0
    if not _fetch_lock.acquire(blocking=False):
        return 0
    try:
        if not force and not _stale():
            return 0
        new = ok = 0
        now = datetime.now().isoformat(timespec="seconds")
        conn = get_connection()
        try:
            for f in FEEDS:
                try:
                    r = requests.get(f["url"], headers={"User-Agent": _UA}, timeout=20)
                    if r.status_code != 200:
                        print(f"[NEWS] {f['url']} -> {r.status_code}")
                        continue
                    items = parse_feed(r.content, f["source"], f.get("kind"))
                except Exception as e:
                    print(f"[NEWS] {f['url']} failed: {str(e)[:120]}")
                    continue
                new += _insert(conn, items, now)
                conn.commit()  # release the write lock before the next download
                ok += 1
            # Only a successful fetch counts as a refresh; otherwise the next
            # page load after RETRY_AFTER_FAILURE tries again instead of
            # waiting out REFRESH_EVERY.
            if ok:
                conn.execute("INSERT OR REPLACE INTO regulatory_news_meta (key, value) VALUES ('last_refresh', ?)",
                             (now,))
            conn.commit()
        finally:
            conn.close()
        _last_failure = None if ok else datetime.now()
        print(f"[NEWS] refresh complete: {new} new items")
        return new
    finally:
        _fetch_lock.release()


def refreshing():
    return _fetch_lock.locked()


def _stale():
    # After a refresh where every feed failed, wait before the next automatic
    # try rather than hitting SEBI and RBI on every page load. Forced
    # refreshes (the Refresh button) skip this check.
    if _last_failure and datetime.now() - _last_failure < RETRY_AFTER_FAILURE:
        return False
    conn = get_connection()
    row = conn.execute("SELECT value FROM regulatory_news_meta WHERE key = 'last_refresh'").fetchone()
    conn.close()
    if not row:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(row["value"]) > REFRESH_EVERY
    except ValueError:
        return True


def refresh_async():
    """Start a background refresh if the feed is stale. True if one started."""
    if not _frozen() and not refreshing() and _stale():
        threading.Thread(target=refresh, name="news-refresh", daemon=True).start()
        return True
    return False


def last_refresh():
    conn = get_connection()
    row = conn.execute("SELECT value FROM regulatory_news_meta WHERE key = 'last_refresh'").fetchone()
    conn.close()
    return row["value"] if row else None


# ── Audience ────────────────────────────────────────────────────────────────

_LISTED_TOPICS = {"LODR", "Insider trading", "Takeovers and M&A", "Buybacks and delisting", "ESG and BRSR"}

# Which topics each audience cares about. Listed-company topics are added for
# a CS or practising CS only when they look after a listed entity (or have not
# added any entities yet, so nothing is hidden on day one).
ROLE_TOPICS = {
    "cro": {"Policy updates", "Enforcement", "Banking", "NBFC", "KYC / AML", "Lending", "Prudential norms",
            "Monetary policy", "Debt markets", "Cyber and IT"},
    "compliance": {"Policy updates", "Governance and audit", "Capital raising", "Debt markets",
                   "Grievance redress", "Market abuse"} | _LISTED_TOPICS,
    "cs": {"Policy updates", "Governance and audit", "Capital raising", "Debt markets", "FEMA",
           "Takeovers and M&A"},
    "pcs": {"Policy updates", "Governance and audit", "Capital raising", "Debt markets", "FEMA",
            "Takeovers and M&A", "Enforcement"},
}


def relevant_topics(role, entity_list):
    role = role if role in ROLE_TOPICS else "cs"
    topics = set(ROLE_TOPICS[role])
    entity_list = entity_list or []
    if role in ("cs", "pcs") and not entity_list:
        topics |= _LISTED_TOPICS
    for e in entity_list:
        flags = e.get("flags") or {}
        if e.get("entity_type") == "listed":
            topics |= _LISTED_TOPICS | {"Governance and audit"}
        if flags.get("top1000"):
            topics.add("ESG and BRSR")
        if flags.get("fdi") or flags.get("odi") or flags.get("ecb"):
            topics.add("FEMA")
    return topics


# ── Queries ─────────────────────────────────────────────────────────────────

def _where(source=None, kind=None, topic=None, q=None, topics_any=None, kinds=None, exclude_kinds=None):
    sql, args = " WHERE 1=1", []
    if source:
        sql += " AND source = ?"
        args.append(source)
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if kinds:
        sql += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
        args += list(kinds)
    if exclude_kinds:
        sql += " AND kind NOT IN (" + ",".join("?" for _ in exclude_kinds) + ")"
        args += list(exclude_kinds)
    if topic:
        sql += " AND (',' || topics || ',') LIKE ?"
        args.append(f"%,{topic},%")
    if q:
        esc = q[:80].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += " AND title LIKE ? ESCAPE '\\'"
        args.append(f"%{esc}%")
    if topics_any is not None:
        topics_any = list(topics_any)
        if not topics_any:
            sql += " AND 0"
        else:
            sql += " AND (" + " OR ".join("(',' || topics || ',') LIKE ?" for _ in topics_any) + ")"
            args += [f"%,{t},%" for t in topics_any]
    return sql, args


def list_news(source=None, kind=None, topic=None, q=None, topics_any=None, limit=80, offset=0,
              kinds=None, exclude_kinds=None):
    where, args = _where(source, kind, topic, q, topics_any, kinds, exclude_kinds)
    sql = "SELECT * FROM regulatory_news" + where + " ORDER BY published_at DESC, id DESC LIMIT ? OFFSET ?"
    conn = get_connection()
    rows = conn.execute(sql, args + [limit, offset]).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["topic_list"] = [t for t in (d["topics"] or "").split(",") if t]
        d["kind_label"] = KIND_LABELS.get(d["kind"], d["kind"].capitalize())
        # Market-operations bodies are flattened tables; the title says it all.
        d["excerpt"] = "" if "Market operations" in d["topic_list"] else clean_excerpt(d["title"], d["summary"])
        out.append(d)
    return out


def topic_counts(source=None, kind=None, q=None, topics_any=None, kinds=None, exclude_kinds=None):
    """{topic: count} over the items matching the filters (topic filter excluded)."""
    where, args = _where(source, kind, None, q, topics_any, kinds, exclude_kinds)
    conn = get_connection()
    rows = conn.execute("SELECT topics FROM regulatory_news" + where, args).fetchall()
    conn.close()
    counts = {}
    for r in rows:
        for t in (r["topics"] or "").split(","):
            if t:
                counts[t] = counts.get(t, 0) + 1
    return {t: counts[t] for t in TOPICS if counts.get(t)}


def count_all():
    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) AS n FROM regulatory_news").fetchone()["n"]
    conn.close()
    return n


def get_item(news_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM regulatory_news WHERE id = ?", (news_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def enforcement_items(since_days=365):
    """SEBI orders and recovery proceedings, for counterparty screening.
    Exemption orders (usually from open-offer requirements) are not adverse
    and are left out."""
    since = (datetime.now() - timedelta(days=since_days)).isoformat()
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM regulatory_news WHERE source = 'SEBI' AND kind IN ('order', 'recovery') AND published_at >= ?",
        (since,)).fetchall()
    conn.close()
    return [dict(r) for r in rows if not re.match(r"\s*exemption orders?\b", (r["title"] or "").lower())]
