"""Test isolation: a throwaway SQLite file and no outbound network.

Import this before any application module. database.py reads DATABASE_PATH
once, at import, so the variable has to be set first. If database was
already imported (and so may point at the real delta_filings.db) we stop
rather than risk writing to it.

Run from the project root:
    python3 -m unittest discover -s tests -t . -v
"""
import atexit
import contextlib
import importlib
import io
import os
import secrets
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if "database" in sys.modules:
    raise RuntimeError("tests/_util.py must be imported before database.py, "
                       "otherwise the tests would use the real database.")

_TMP_DIR = tempfile.mkdtemp(prefix="delta-tests-")
DB_PATH = os.path.join(_TMP_DIR, "test.db")
os.environ["DATABASE_PATH"] = DB_PATH
# Frozen demo mode: no background scan threads, no feed refreshes.
os.environ["DELTA_DEMO_FREEZE"] = "1"
os.environ.pop("DELTA_DEMO_MODE", None)
atexit.register(shutil.rmtree, _TMP_DIR, True)

# Any HTTP call that a test has not mocked fails loudly instead of reaching
# SEBI / RBI / IBBI / NSE. mock.patch("requests.get") still works on top.
import requests  # noqa: E402


def _no_network(*args, **kwargs):
    raise RuntimeError("Network access is disabled in tests; mock requests.get.")


requests.sessions.Session.request = _no_network

with contextlib.redirect_stdout(io.StringIO()):
    import database  # noqa: E402

    if os.path.abspath(database.DB_PATH) != os.path.abspath(DB_PATH):
        raise RuntimeError(f"database.DB_PATH is {database.DB_PATH}, expected the temp file {DB_PATH}")
    database.init_db()

# A module another agent is mid-way through editing may fail to import; that
# should fail its own tests, not every test in the suite.
INIT_ERRORS = {}
for _mod, _fn in (("entities", "init_entities_db"), ("sdd", "init_sdd_db"),
                  ("secretarial", "init_secretarial_db"), ("risk", "init_risk_db"),
                  ("reg_news", "init_news_db"), ("roles", "init_roles_db")):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            getattr(importlib.import_module(_mod), _fn)()
    except Exception as e:  # pragma: no cover - reported by the module's own tests
        INIT_ERRORS[_mod] = e


def uid(prefix="user"):
    """A user id no other test uses, so tests never see each other's rows."""
    return f"{prefix}-{secrets.token_hex(4)}"


def connect():
    return database.get_connection()


def clear(*tables):
    conn = connect()
    for t in tables:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()


def make_entity(user_id, **overrides):
    """Create an entity through the real backend and return its id."""
    import entities
    data = {"name": "Acme Widgets Private Limited", "entity_type": "private"}
    data.update(overrides)
    return entities.create_entity(user_id, data)


@contextlib.contextmanager
def quiet():
    """Swallow the modules' progress prints."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield
