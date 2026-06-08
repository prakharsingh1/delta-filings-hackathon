import sqlite3
import os
from datetime import datetime

# DATABASE_PATH env var lets Railway (and other hosts) point SQLite at a
# persistent volume path, e.g. "/data/delta_filings.db".
DB_PATH = os.environ.get("DATABASE_PATH") or os.path.join(
    os.path.dirname(__file__), "delta_filings.db"
)
# Ensure the parent directory exists (important on first deploy to a volume).
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

# ── Topic clusters: group filing titles that talk about the same subject ──
TOPIC_CLUSTERS = {
    "board_meeting": [
        "outcome of board meeting", "board meeting", "board meeting intimation",
    ],
    "analyst_call": [
        "analysts/institutional investor meet", "con. call updates",
        "investor meet", "analyst meet", "conference call",
    ],
    "newspaper": [
        "copy of newspaper publication", "newspaper publication",
    ],
    "general_update": [
        "general updates", "updates",
    ],
    "trading_window": [
        "trading window", "closure of trading window",
    ],
    "acquisition": [
        "acquisition", "stake acquisition",
    ],
    "divestment": [
        "diversification/disinvestment", "disinvestment", "diversification",
    ],
    "investor_presentation": [
        "investor presentation", "presentation",
    ],
    "agm_egm": [
        "annual general meeting", "extraordinary general meeting", "agm", "egm",
        "postal ballot", "resolution",
    ],
    "financial_results": [
        "financial result", "quarterly result",
    ],
    "credit_rating": [
        "credit rating", "rating",
    ],
    "compliance_cert": [
        "compliance certificate", "certificate",
    ],
    "key_personnel": [
        "key managerial personnel", "change in director",
        "appointment", "resignation",
    ],
    "scheme_arrangement": [
        "scheme of arrangement", "demerger", "merger",
    ],
    "annual_report": [
        "annual report",
    ],
}


def _get_topic(title):
    t = title.lower().strip()
    for cluster_key, keywords in TOPIC_CLUSTERS.items():
        for kw in keywords:
            if kw in t:
                return cluster_key
    return None


def _tag_overlap_score(tags_a, tags_b):
    set_a = set(t.strip().lower() for t in (tags_a or "").split(",") if t.strip())
    set_b = set(t.strip().lower() for t in (tags_b or "").split(",") if t.strip())
    if not set_a or not set_b:
        return 0
    return len(set_a & set_b)


_WAL_INITIALIZED = False


def _set_wal_mode_once():
    """Enable WAL journal mode + sane pragmas the first time we touch the DB.
    WAL lets readers proceed while a writer is active, instead of blocking
    every connection — critical when we have background backfills inserting
    rows while the UI is reading. Idempotent: SQLite ignores re-application."""
    global _WAL_INITIALIZED
    if _WAL_INITIALIZED:
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL fsync only at checkpoint boundaries — safe under WAL and
        # ~10x faster than the default FULL on a network-mounted volume.
        conn.execute("PRAGMA synchronous=NORMAL")
        # Wait up to 10s for a busy lock instead of erroring instantly,
        # so brief writer overlap doesn't blow up read paths.
        conn.execute("PRAGMA busy_timeout=10000")
        conn.close()
        _WAL_INITIALIZED = True
        print("[DB] WAL mode enabled (synchronous=NORMAL, busy_timeout=10s).")
    except Exception as e:
        print(f"[DB] WAL setup failed: {e}")


def get_connection():
    _set_wal_mode_once()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL mode is DB-wide and persists, but synchronous + busy_timeout are
    # connection-scoped and reset to defaults on every new connection.
    # Re-applying them here is what makes WAL actually fast under writes.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS filings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            date TEXT NOT NULL,
            sort_date TEXT NOT NULL DEFAULT '',
            pdf_link TEXT,
            raw_text TEXT,
            summary TEXT DEFAULT '',
            tags TEXT,
            has_xbrl INTEGER DEFAULT 0,
            first_seen TEXT NOT NULL,
            UNIQUE(company, title, date)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            taken_at TEXT NOT NULL,
            filing_ids TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT '',
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            added_at TEXT NOT NULL,
            UNIQUE(user_id, symbol)
        )
    """)

    # Migration: if an older table exists without user_id (pre-multiuser schema),
    # drop and recreate. Must run BEFORE creating the user_id index.
    cols = [r["name"] for r in c.execute("PRAGMA table_info(watchlist)").fetchall()]
    if "user_id" not in cols:
        print("[DB] Migrating watchlist to per-user schema (dropping old shared table)...")
        c.execute("DROP TABLE watchlist")
        c.execute("""
            CREATE TABLE watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL,
                name TEXT DEFAULT '',
                added_at TEXT NOT NULL,
                UNIQUE(user_id, symbol)
            )
        """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)")
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            industry TEXT DEFAULT '',
            onboarded INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    # Add columns if upgrading from older schema
    for col, coltype, default in [
        ("description", "TEXT", "''"),
        ("summary", "TEXT", "''"),
        ("has_xbrl", "INTEGER", "0"),
    ]:
        try:
            c.execute(f"ALTER TABLE filings ADD COLUMN {col} {coltype} DEFAULT {default}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()
    print("[DB] Database initialized.")


# ── Settings ──

def get_setting(key, default=""):
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_connection()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )
    conn.commit()
    conn.close()


def get_all_settings():
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


# ── Watchlist ──

DEFAULT_WATCHLIST = [
    ("HINDUNILVR", "Hindustan Unilever Limited"),
    ("ITC", "ITC Limited"),
    ("NESTLEIND", "Nestle India Limited"),
    ("BRITANNIA", "Britannia Industries Limited"),
    ("DABUR", "Dabur India Limited"),
]


def init_watchlist():
    """No-op kept for backward compatibility. Per-user watchlists are
    populated on onboarding via replace_watchlist_with_industry(user_id, ...)."""
    pass


def get_watchlist(user_id=None):
    """Return watchlist entries. If user_id is provided, only that user's rows.
    If user_id is None, returns ALL rows across users (used by back-end jobs
    like compliance refresh that aggregate symbols across the platform)."""
    conn = get_connection()
    if user_id:
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE user_id = ? ORDER BY added_at ASC",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM watchlist ORDER BY added_at ASC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_watchlist_symbols():
    """Return the unique set of symbols across ALL users' watchlists.
    Used by compliance refresh to know which symbols need data."""
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT symbol FROM watchlist").fetchall()
    conn.close()
    return [r["symbol"] for r in rows]


def get_all_known_companies():
    """Return the union of every company the platform has ever touched:
    - companies present in the filings table (i.e. we have local data for them)
    - companies present on any user's watchlist

    This is the search surface used by cross-company DIN lookup so that a
    director's history isn't restricted to the current user's watchlist."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT DISTINCT company AS symbol FROM filings
           UNION
           SELECT DISTINCT symbol FROM watchlist"""
    ).fetchall()
    conn.close()
    return sorted({r["symbol"] for r in rows if r["symbol"]})


def add_to_watchlist(user_id, symbol, name=""):
    """Add a company to a user's watchlist. Returns True if added, False if already exists."""
    if not user_id:
        return False
    conn = get_connection()
    now = datetime.now().isoformat()
    try:
        conn.execute(
            "INSERT INTO watchlist (user_id, symbol, name, added_at) VALUES (?, ?, ?, ?)",
            (user_id, symbol.upper(), name, now),
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def remove_from_watchlist(user_id, symbol):
    """Remove a company from a user's watchlist."""
    if not user_id:
        return
    conn = get_connection()
    conn.execute(
        "DELETE FROM watchlist WHERE user_id = ? AND symbol = ?",
        (user_id, symbol.upper()),
    )
    conn.commit()
    conn.close()


def get_cached_filings(company):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM filings WHERE company = ? ORDER BY sort_date DESC, date DESC", (company,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_filings(company):
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM filings WHERE company = ?", (company,)).fetchone()
    conn.close()
    return row["cnt"]


# ── Filings fetch metadata ────────────────────────────────
# Tracks the last time we successfully pulled filings from NSE for a symbol,
# so the company page can decide whether to trigger a background backfill
# without hammering NSE on every view.
def _ensure_filings_meta_table():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filings_meta (
            symbol TEXT PRIMARY KEY,
            last_fetched_at TEXT NOT NULL,
            last_fetch_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def _ensure_user_telegram_table():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_telegram (
            user_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            first_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            linked_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# ── Referral codes (gates the ₹4999 payment) ──────────────
# These bypass the Razorpay step. Owner/admin seeds codes manually for
# launch partners, beta testers, journalists, etc. Each code has an
# optional usage cap; once `used_count >= max_uses` the code expires.

def _ensure_referral_table():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referral_codes (
            code TEXT PRIMARY KEY,
            description TEXT DEFAULT '',
            max_uses INTEGER,                 -- NULL = unlimited
            used_count INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referral_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            code TEXT NOT NULL,
            redeemed_at TEXT NOT NULL,
            UNIQUE(user_id)                   -- one redemption per user
        )
    """)
    conn.commit()
    conn.close()


def seed_default_referral_codes():
    """Idempotent: insert a couple of high-trust launch codes if they
    don't already exist. Owner can add more via direct SQL or admin UI."""
    _ensure_referral_table()
    conn = get_connection()
    now = datetime.now().isoformat()
    seeds = [
        # WELCOME2026 is the public access code — anyone with this gets in
        # without paying. Unlimited uses, gives full platform access.
        ("WELCOME2026",    "Welcome / public access code",       None),
        ("LAUNCH2026",     "Launch partner / founder seed code", None),
        ("DELTAFOUNDER",   "Founder bypass code",                None),
        ("BETA50",         "First 50 beta users",                50),
    ]
    for code, desc, max_uses in seeds:
        conn.execute(
            "INSERT OR IGNORE INTO referral_codes (code, description, max_uses, used_count, active, created_at) "
            "VALUES (?, ?, ?, 0, 1, ?)",
            (code, desc, max_uses, now),
        )
    conn.commit()
    conn.close()


def get_referral_code(code: str):
    if not code:
        return None
    _ensure_referral_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM referral_codes WHERE code = ?",
            ((code or "").strip().upper(),),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def can_redeem_referral(code: str) -> bool:
    rc = get_referral_code(code)
    if not rc:
        return False
    if not rc.get("active"):
        return False
    max_uses = rc.get("max_uses")
    if max_uses is not None and rc.get("used_count", 0) >= max_uses:
        return False
    return True


def redeem_referral(user_id: str, code: str) -> bool:
    """Atomic-ish (single-conn) redeem: re-checks limits inside the txn,
    increments used_count, records the redemption. Returns False if the
    code is invalid, exhausted, or the user has already redeemed any code."""
    if not can_redeem_referral(code):
        return False
    code = code.strip().upper()
    conn = get_connection()
    try:
        # One redemption per user (UNIQUE on user_id)
        existing = conn.execute(
            "SELECT 1 FROM referral_redemptions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if existing:
            return False
        # Re-check inside the connection
        rc = conn.execute(
            "SELECT used_count, max_uses, active FROM referral_codes WHERE code = ?",
            (code,),
        ).fetchone()
        if not rc or not rc["active"]:
            return False
        if rc["max_uses"] is not None and rc["used_count"] >= rc["max_uses"]:
            return False
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO referral_redemptions (user_id, code, redeemed_at) VALUES (?, ?, ?)",
            (user_id, code, now),
        )
        conn.execute(
            "UPDATE referral_codes SET used_count = used_count + 1 WHERE code = ?",
            (code,),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


# ── Payment records (Razorpay) ────────────────────────────

def _ensure_payments_table():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_payments (
            user_id TEXT PRIMARY KEY,
            order_id TEXT,
            payment_id TEXT,
            amount INTEGER,                   -- in paise
            currency TEXT DEFAULT 'INR',
            status TEXT DEFAULT 'pending',    -- 'pending' | 'paid' | 'failed'
            paid_at TEXT,
            referral_code TEXT,               -- if paid via referral
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def record_payment_intent(user_id: str, order_id: str, amount: int,
                         currency: str = "INR"):
    _ensure_payments_table()
    now = datetime.now().isoformat()
    conn = get_connection()
    conn.execute(
        """INSERT INTO user_payments (user_id, order_id, amount, currency,
                                       status, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             order_id = excluded.order_id,
             amount = excluded.amount,
             currency = excluded.currency,
             updated_at = excluded.updated_at""",
        (user_id, order_id, amount, currency, now, now),
    )
    conn.commit()
    conn.close()


def mark_paid(user_id: str, payment_id: str = "", order_id: str = "",
              amount: int = 499900, referral_code: str = ""):
    _ensure_payments_table()
    now = datetime.now().isoformat()
    conn = get_connection()
    conn.execute(
        """INSERT INTO user_payments (user_id, order_id, payment_id, amount,
                                       status, paid_at, referral_code,
                                       created_at, updated_at)
           VALUES (?, ?, ?, ?, 'paid', ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             order_id = COALESCE(NULLIF(excluded.order_id, ''), user_payments.order_id),
             payment_id = COALESCE(NULLIF(excluded.payment_id, ''), user_payments.payment_id),
             amount = excluded.amount,
             status = 'paid',
             paid_at = excluded.paid_at,
             referral_code = COALESCE(NULLIF(excluded.referral_code, ''), user_payments.referral_code),
             updated_at = excluded.updated_at""",
        (user_id, order_id, payment_id, amount, now, referral_code, now, now),
    )
    conn.commit()
    conn.close()


def get_payment_status(user_id: str):
    if not user_id:
        return None
    _ensure_payments_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM user_payments WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def is_user_paid(user_id: str) -> bool:
    p = get_payment_status(user_id)
    return bool(p and p.get("status") == "paid")


def set_user_telegram(user_id, chat_id, first_name="", username=""):
    """Persist a user's verified Telegram link. One row per user — re-linking
    just overwrites. We store first_name/username so the UI can show
    'Linked as @prakhar' instead of a bare numeric ID."""
    _ensure_user_telegram_table()
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO user_telegram (user_id, chat_id, first_name, username, linked_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               chat_id = excluded.chat_id,
               first_name = excluded.first_name,
               username = excluded.username,
               linked_at = excluded.linked_at""",
        (user_id, str(chat_id), first_name or "", username or "", now),
    )
    conn.commit()
    conn.close()


def get_user_telegram(user_id):
    _ensure_user_telegram_table()
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM user_telegram WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def unlink_user_telegram(user_id):
    _ensure_user_telegram_table()
    conn = get_connection()
    conn.execute("DELETE FROM user_telegram WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_filings_last_fetched(symbol):
    _ensure_filings_meta_table()
    conn = get_connection()
    row = conn.execute(
        "SELECT last_fetched_at, last_fetch_count FROM filings_meta WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_filings_last_fetched(symbol, count):
    _ensure_filings_meta_table()
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO filings_meta (symbol, last_fetched_at, last_fetch_count)
           VALUES (?, ?, ?)
           ON CONFLICT(symbol) DO UPDATE SET
               last_fetched_at = excluded.last_fetched_at,
               last_fetch_count = excluded.last_fetch_count""",
        (symbol, now, count),
    )
    conn.commit()
    conn.close()


def get_paged_filings(company, page=1, per_page=15):
    conn = get_connection()
    offset = (page - 1) * per_page
    rows = conn.execute(
        "SELECT * FROM filings WHERE company = ? ORDER BY sort_date DESC, date DESC LIMIT ? OFFSET ?",
        (company, per_page, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_filings(company, query, tag=None, page=1, per_page=15):
    """Search filings by title, description, tags, or raw_text content. With pagination."""
    conn = get_connection()
    like = f"%{query}%"

    base_where = "company = ? AND (title LIKE ? OR description LIKE ? OR tags LIKE ? OR raw_text LIKE ?)"
    params = [company, like, like, like, like]

    if tag:
        base_where += " AND tags LIKE ?"
        params.append(f"%{tag}%")

    # Count total
    count_row = conn.execute(f"SELECT COUNT(*) as cnt FROM filings WHERE {base_where}", params).fetchone()
    total = count_row["cnt"]

    # Paged results
    offset = (page - 1) * per_page
    params_paged = params + [per_page, offset]
    rows = conn.execute(
        f"SELECT * FROM filings WHERE {base_where} ORDER BY sort_date DESC LIMIT ? OFFSET ?",
        params_paged,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def filter_filings_by_tag(company, tag, page=1, per_page=15):
    """Filter filings by tag with pagination."""
    conn = get_connection()
    like_tag = f"%{tag}%"

    count_row = conn.execute(
        "SELECT COUNT(*) as cnt FROM filings WHERE company = ? AND tags LIKE ?",
        (company, like_tag),
    ).fetchone()
    total = count_row["cnt"]

    offset = (page - 1) * per_page
    rows = conn.execute(
        "SELECT * FROM filings WHERE company = ? AND tags LIKE ? ORDER BY sort_date DESC LIMIT ? OFFSET ?",
        (company, like_tag, per_page, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def search_din_across_companies(din, year_filter="all"):
    """Search for a DIN number across ALL companies in the database.
    year_filter: 'current', 'previous', or 'all'."""
    conn = get_connection()
    like = f"%{din}%"

    # Build year filter
    year_clause = ""
    params = [like, like, like]
    if year_filter == "current":
        from datetime import date as dt_date
        current_year = dt_date.today().year
        year_clause = " AND sort_date >= ?"
        params.append(f"{current_year}-01-01")
    elif year_filter == "previous":
        from datetime import date as dt_date
        current_year = dt_date.today().year
        year_clause = " AND sort_date >= ? AND sort_date < ?"
        params.append(f"{current_year - 1}-01-01")
        params.append(f"{current_year}-01-01")

    rows = conn.execute(
        f"""SELECT id, company, title, description, date, sort_date, tags, has_xbrl
            FROM filings
            WHERE (title LIKE ? OR description LIKE ? OR raw_text LIKE ?)
            {year_clause}
            ORDER BY sort_date DESC
            LIMIT 100""",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def filing_exists(company, title, date):
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM filings WHERE company = ? AND title = ? AND date = ?",
        (company, title, date),
    ).fetchone()
    conn.close()
    return row is not None


def insert_filing(company, title, date, sort_date, pdf_link, raw_text,
                  tags="", description="", has_xbrl=False):
    conn = get_connection()
    now = datetime.now().isoformat()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO filings
               (company, title, description, date, sort_date, pdf_link, raw_text, tags, has_xbrl, first_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (company, title, description, date, sort_date, pdf_link, raw_text, tags,
             1 if has_xbrl else 0, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()


def batch_insert_filings(rows):
    """Insert many filings in a single transaction. `rows` is a list of dicts
    with keys: company, title, description, date, sort_date, pdf_link,
    raw_text, tags, has_xbrl. Returns count of rows actually inserted
    (excludes duplicates ignored by the UNIQUE constraint).

    Inserting one filing at a time (open/insert/commit/close × 1000) is
    catastrophically slow on a mounted SQLite volume — each commit is an
    fsync, and SQLite's writer lock blocks every other request for the
    duration. One batch transaction does it in a single fsync."""
    if not rows:
        return 0
    now = datetime.now().isoformat()
    payload = [
        (
            r["company"], r["title"], r.get("description", ""), r["date"],
            r.get("sort_date", r["date"]), r.get("pdf_link", ""),
            r.get("raw_text", ""), r.get("tags", ""),
            1 if r.get("has_xbrl") else 0, now,
        )
        for r in rows
    ]
    conn = get_connection()
    try:
        before = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
        conn.executemany(
            """INSERT OR IGNORE INTO filings
               (company, title, description, date, sort_date, pdf_link,
                raw_text, tags, has_xbrl, first_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            payload,
        )
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
        return after - before
    finally:
        conn.close()


def get_existing_filing_keys(company):
    """Return a set of (title, date) tuples already on file for a company.
    Used by process_filings to dedupe in memory before batch insert,
    avoiding 1000 separate filing_exists() round-trips."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT title, date FROM filings WHERE company = ?", (company,)
    ).fetchall()
    conn.close()
    return {(r["title"], r["date"]) for r in rows}


def update_filing_summary(filing_id, summary):
    conn = get_connection()
    conn.execute("UPDATE filings SET summary = ? WHERE id = ?", (summary, filing_id))
    conn.commit()
    conn.close()


def get_previous_snapshot_ids(company):
    conn = get_connection()
    row = conn.execute(
        "SELECT filing_ids FROM snapshots WHERE company = ? ORDER BY taken_at DESC LIMIT 1",
        (company,),
    ).fetchone()
    conn.close()
    if not row:
        return set()
    return set(int(x) for x in row["filing_ids"].split(",") if x.strip())


def save_snapshot(company):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id FROM filings WHERE company = ?", (company,)
    ).fetchall()
    ids_str = ",".join(str(r["id"]) for r in rows)
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO snapshots (company, taken_at, filing_ids) VALUES (?, ?, ?)",
        (company, now, ids_str),
    )
    conn.commit()
    conn.close()


def get_filing_by_id(filing_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM filings WHERE id = ?", (filing_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def find_best_match(filing_id):
    """Smart matching: find the best previous filing to compare against.

    Priority:
      1. Same topic cluster + older date
      2. Same exact title + older date
      3. Best tag overlap + older date
      4. Any older filing from same company
    """
    filing = get_filing_by_id(filing_id)
    if not filing:
        return None, None

    conn = get_connection()
    company = filing["company"]
    title = filing["title"]
    sort_date = filing["sort_date"]
    fid = filing["id"]
    tags = filing.get("tags", "")
    topic = _get_topic(title)

    # Strategy 1: Same topic cluster, older date
    if topic:
        candidates = conn.execute(
            """SELECT * FROM filings
               WHERE company = ? AND id != ? AND sort_date < ?
               ORDER BY sort_date DESC""",
            (company, fid, sort_date),
        ).fetchall()

        for row in candidates:
            if _get_topic(row["title"]) == topic:
                conn.close()
                return dict(row), f"Same topic: {topic.replace('_', ' ').title()}"

    # Strategy 2: Exact same title, older date
    row = conn.execute(
        """SELECT * FROM filings
           WHERE company = ? AND title = ? AND id != ? AND sort_date < ?
           ORDER BY sort_date DESC LIMIT 1""",
        (company, title, fid, sort_date),
    ).fetchone()
    if row:
        conn.close()
        return dict(row), "Same filing type"

    # Strategy 3: Best tag overlap, older date
    if tags:
        candidates = conn.execute(
            """SELECT * FROM filings
               WHERE company = ? AND id != ? AND sort_date < ?
               ORDER BY sort_date DESC""",
            (company, fid, sort_date),
        ).fetchall()

        best_row = None
        best_score = 0
        for row in candidates:
            score = _tag_overlap_score(tags, row["tags"])
            if score > best_score:
                best_score = score
                best_row = row

        if best_row and best_score >= 2:
            conn.close()
            return dict(best_row), f"Similar tags ({best_score} shared)"
        if best_row and best_score >= 1:
            conn.close()
            return dict(best_row), f"Related topic ({best_score} shared tag)"

    # Strategy 4: Any older filing
    row = conn.execute(
        """SELECT * FROM filings
           WHERE company = ? AND id != ? AND sort_date < ?
           ORDER BY sort_date DESC LIMIT 1""",
        (company, fid, sort_date),
    ).fetchone()
    if row:
        conn.close()
        return dict(row), "Closest previous filing"

    # Strategy 5: Any other filing
    row = conn.execute(
        """SELECT * FROM filings
           WHERE company = ? AND id != ?
           ORDER BY sort_date DESC LIMIT 1""",
        (company, fid),
    ).fetchone()
    conn.close()
    if row:
        return dict(row), "Only available comparison"
    return None, None


# ── Users & Auth ──

import hashlib
import secrets

# Default watchlist per industry = top 5 NSE-listed companies by market cap
# (as of April 2026). Re-ranked periodically by the data team.
INDUSTRY_WATCHLISTS = {
    "FMCG": [
        ("HINDUNILVR", "Hindustan Unilever Limited"),
        ("ITC", "ITC Limited"),
        ("NESTLEIND", "Nestle India Limited"),
        ("BRITANNIA", "Britannia Industries Limited"),
        ("DABUR", "Dabur India Limited"),
    ],
    "Steel": [
        ("TATASTEEL", "Tata Steel Limited"),
        ("JSWSTEEL", "JSW Steel Limited"),
        ("JINDALSTEL", "Jindal Steel & Power Limited"),
        ("SAIL", "Steel Authority of India Limited"),
        ("JSL", "Jindal Stainless Limited"),
    ],
    "Banking": [
        ("HDFCBANK", "HDFC Bank Limited"),
        ("ICICIBANK", "ICICI Bank Limited"),
        ("SBIN", "State Bank of India"),
        ("KOTAKBANK", "Kotak Mahindra Bank Limited"),
        ("AXISBANK", "Axis Bank Limited"),
    ],
    "Pharma": [
        ("SUNPHARMA", "Sun Pharmaceutical Industries Limited"),
        ("DIVISLAB", "Divi's Laboratories Limited"),
        ("CIPLA", "Cipla Limited"),
        ("DRREDDY", "Dr. Reddy's Laboratories Limited"),
        ("LUPIN", "Lupin Limited"),
    ],
    "IT": [
        ("TCS", "Tata Consultancy Services Limited"),
        ("INFY", "Infosys Limited"),
        ("HCLTECH", "HCL Technologies Limited"),
        ("WIPRO", "Wipro Limited"),
        ("TECHM", "Tech Mahindra Limited"),
    ],
    "Auto": [
        ("MARUTI", "Maruti Suzuki India Limited"),
        ("M&M", "Mahindra & Mahindra Limited"),
        ("TATAMOTORS", "Tata Motors Limited"),
        ("BAJAJ-AUTO", "Bajaj Auto Limited"),
        ("EICHERMOT", "Eicher Motors Limited"),
    ],
    "Energy": [
        ("RELIANCE", "Reliance Industries Limited"),
        ("ONGC", "Oil & Natural Gas Corporation Limited"),
        ("COALINDIA", "Coal India Limited"),
        ("IOC", "Indian Oil Corporation Limited"),
        ("BPCL", "Bharat Petroleum Corporation Limited"),
    ],
    "Power": [
        ("NTPC", "NTPC Limited"),
        ("POWERGRID", "Power Grid Corporation of India Limited"),
        ("ADANIPOWER", "Adani Power Limited"),
        ("TATAPOWER", "Tata Power Company Limited"),
        ("JSWENERGY", "JSW Energy Limited"),
    ],
    "Cement": [
        ("ULTRACEMCO", "UltraTech Cement Limited"),
        ("AMBUJACEM", "Ambuja Cements Limited"),
        ("SHREECEM", "Shree Cement Limited"),
        ("ACC", "ACC Limited"),
        ("DALBHARAT", "Dalmia Bharat Limited"),
    ],
    "Telecom": [
        ("BHARTIARTL", "Bharti Airtel Limited"),
        ("RELIANCE", "Reliance Industries Limited"),
        ("IDEA", "Vodafone Idea Limited"),
        ("INDUSTOWER", "Indus Towers Limited"),
        ("TATACOMM", "Tata Communications Limited"),
    ],
    "Metals & Mining": [
        ("HINDALCO", "Hindalco Industries Limited"),
        ("VEDL", "Vedanta Limited"),
        ("COALINDIA", "Coal India Limited"),
        ("NMDC", "NMDC Limited"),
        ("NATIONALUM", "National Aluminium Company Limited"),
    ],
    "Real Estate": [
        ("DLF", "DLF Limited"),
        ("LODHA", "Macrotech Developers Limited"),
        ("GODREJPROP", "Godrej Properties Limited"),
        ("OBEROIRLTY", "Oberoi Realty Limited"),
        ("PRESTIGE", "Prestige Estates Projects Limited"),
    ],
    "Finance (NBFC)": [
        ("BAJFINANCE", "Bajaj Finance Limited"),
        ("BAJAJFINSV", "Bajaj Finserv Limited"),
        ("CHOLAFIN", "Cholamandalam Investment & Finance Company Limited"),
        ("SHRIRAMFIN", "Shriram Finance Limited"),
        ("MUTHOOTFIN", "Muthoot Finance Limited"),
    ],
    "Insurance": [
        ("HDFCLIFE", "HDFC Life Insurance Company Limited"),
        ("SBILIFE", "SBI Life Insurance Company Limited"),
        ("ICICIGI", "ICICI Lombard General Insurance Company Limited"),
        ("ICICIPRULI", "ICICI Prudential Life Insurance Company Limited"),
        ("LICI", "Life Insurance Corporation of India"),
    ],
    "Consumer Durables": [
        ("TITAN", "Titan Company Limited"),
        ("HAVELLS", "Havells India Limited"),
        ("VOLTAS", "Voltas Limited"),
        ("CROMPTON", "Crompton Greaves Consumer Electricals Limited"),
        ("BLUESTARCO", "Blue Star Limited"),
    ],
    "Chemicals": [
        ("PIDILITIND", "Pidilite Industries Limited"),
        ("SRF", "SRF Limited"),
        ("UPL", "UPL Limited"),
        ("DEEPAKNTR", "Deepak Nitrite Limited"),
        ("AARTIIND", "Aarti Industries Limited"),
    ],
}


def _hash_password(password: str, salt: str = None) -> str:
    """Return salt$hash hex digest."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return _hash_password(password, salt) == stored


def create_user(name: str, email: str, password: str):
    """Create a user. Returns (user_dict, None) on success or (None, error_msg)."""
    name = (name or "").strip()
    email = (email or "").strip().lower()
    password = password or ""
    if not name or not email or not password:
        return None, "All fields are required."
    if "@" not in email or "." not in email.split("@")[-1]:
        return None, "Please enter a valid email address."
    if len(password) < 6:
        return None, "Password must be at least 6 characters."

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (name, email, _hash_password(password), datetime.now().isoformat()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        return dict(row), None
    except sqlite3.IntegrityError:
        conn.close()
        return None, "An account with that email already exists."


def authenticate_user(email: str, password: str):
    """Return user dict if credentials match, else None."""
    email = (email or "").strip().lower()
    if not email or not password:
        return None
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not row:
        return None
    user = dict(row)
    if _verify_password(password, user["password_hash"]):
        return user
    return None


def get_user_by_id(user_id):
    if not user_id:
        return None
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_user_industry(user_id, industry):
    conn = get_connection()
    conn.execute(
        "UPDATE users SET industry = ?, onboarded = 1 WHERE id = ?",
        (industry, user_id),
    )
    conn.commit()
    conn.close()


def replace_watchlist_with_industry(user_id: str, industry: str):
    """Replace a specific user's watchlist with the industry's default companies.
    Does NOT affect other users' watchlists."""
    if not user_id:
        return []
    companies = INDUSTRY_WATCHLISTS.get(industry)
    if not companies:
        return []
    conn = get_connection()
    conn.execute("DELETE FROM watchlist WHERE user_id = ?", (user_id,))
    now = datetime.now().isoformat()
    for sym, name in companies:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (user_id, symbol, name, added_at) VALUES (?, ?, ?, ?)",
            (user_id, sym, name, now),
        )
    conn.commit()
    conn.close()
    return [sym for sym, _ in companies]
