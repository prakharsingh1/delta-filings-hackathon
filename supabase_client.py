"""
Supabase client initialization for Delta Filings.

Two clients are exposed:
  - `supabase`       — uses the publishable key, for normal auth calls (sign up, sign in, sign out).
  - `supabase_admin` — uses the secret key, for privileged server operations
                       (e.g. updating any user's metadata). May be None if
                       SUPABASE_SECRET_KEY is not set (fine for local dev).

Environment variables are loaded from .env via python-dotenv if present.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed yet; Railway env vars still work.
    pass

from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
    raise RuntimeError(
        "SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be set. "
        "Copy .env.example to .env and fill in values."
    )

# Regular client — uses publishable key. Safe for auth flows.
supabase: Client = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)

# Admin client — uses secret key. Used for reading/updating user metadata
# on behalf of the server. Only created if secret key is present.
supabase_admin: Client | None = (
    create_client(SUPABASE_URL, SUPABASE_SECRET_KEY) if SUPABASE_SECRET_KEY else None
)


def get_user_by_id(user_id: str) -> dict | None:
    """Fetch a Supabase user by id using the admin client. Returns a dict with
    fields compatible with the old SQLite user row: id, email, name, industry,
    onboarded. Returns None if not found or admin client unavailable."""
    if not user_id or not supabase_admin:
        return None
    try:
        result = supabase_admin.auth.admin.get_user_by_id(user_id)
        user = getattr(result, "user", None)
        if not user:
            return None
        meta = user.user_metadata or {}
        return {
            "id": user.id,
            "email": user.email,
            "name": meta.get("name", ""),
            "industry": meta.get("industry", ""),
            "onboarded": bool(meta.get("onboarded", False)),
        }
    except Exception as e:
        print(f"[SUPABASE] get_user_by_id failed: {e}")
        return None


def update_user_metadata(user_id: str, metadata: dict) -> bool:
    """Merge-update a user's metadata via admin API. Returns True on success."""
    if not supabase_admin:
        return False
    try:
        # Fetch existing metadata so we merge instead of overwrite.
        existing = supabase_admin.auth.admin.get_user_by_id(user_id)
        current = (existing.user.user_metadata or {}) if existing and existing.user else {}
        merged = {**current, **metadata}
        supabase_admin.auth.admin.update_user_by_id(user_id, {"user_metadata": merged})
        return True
    except Exception as e:
        print(f"[SUPABASE] update_user_metadata failed: {e}")
        return False
