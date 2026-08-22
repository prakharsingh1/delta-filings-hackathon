"""Response cache so a demo never waits on a third party.

Several endpoints reach out to NSE, BSE or a model on every request:
/api/price-intel took 24s, /api/sector 2.7s, /api/pre-open 1.2s. On a stage
laptop with conference wifi — and with the market closed, so the data is not
even changing — that is the difference between a demo and an apology.

Two modes:
  normal  serve from cache while fresh, refresh in the background after
  frozen  serve from cache always; never make an outbound call
          (DELTA_DEMO_FREEZE=1)

Frozen mode is the demo setting. It is deliberately absolute: a stale number
is recoverable on stage, a thirty-second spinner is not.
"""

import functools
import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from database import get_connection

_lock = threading.Lock()
_refreshing: set = set()


def frozen() -> bool:
    return os.environ.get("DELTA_DEMO_FREEZE", "").strip() in ("1", "true", "True")


def init_cache_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS response_cache (
            cache_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at REAL NOT NULL,
            hits INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def read(key: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute(
        "SELECT payload, created_at FROM response_cache WHERE cache_key = ?", (key,)
    ).fetchone()
    if row:
        conn.execute("UPDATE response_cache SET hits = hits + 1 WHERE cache_key = ?", (key,))
        conn.commit()
    conn.close()
    if not row:
        return None
    try:
        return {"value": json.loads(row["payload"]), "age": time.time() - row["created_at"]}
    except (ValueError, TypeError):
        return None


def write(key: str, value: Any):
    try:
        payload = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return
    conn = get_connection()
    conn.execute(
        "INSERT INTO response_cache (cache_key, payload, created_at, hits) VALUES (?, ?, ?, 0) "
        "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, created_at=excluded.created_at",
        (key, payload, time.time()))
    conn.commit()
    conn.close()


def _is_worth_caching(entry) -> bool:
    """Refuse to store failures.

    A transient outage cached under a long TTL becomes a permanent outage —
    "Couldn't fetch live prices" served from disk for a day. Errors and empty
    bodies are dropped so the next call retries instead.
    """
    if entry is None:
        return False
    if isinstance(entry, dict) and entry.get("__flask__"):
        body = entry.get("json")
        if body in (None, {}, []):
            return False
        if isinstance(body, dict):
            if body.get("error"):
                return False
            # A body whose only content is an error/message field is a failure.
            meaningful = {k: v for k, v in body.items()
                          if k not in ("error", "message", "status")}
            if not meaningful:
                return False
        return True
    if isinstance(entry, str):
        # A stringified object means serialisation fell through to default=str.
        return not entry.startswith("<")
    return entry not in (None, {}, [])


def _to_cacheable(value):
    """Normalise a view's return value into something JSON-serialisable.

    The endpoints being wrapped return Flask Response objects (and sometimes
    a (response, status) tuple). Caching those directly silently failed, so
    unwrap to the JSON body and remember the status.
    """
    status = 200
    if isinstance(value, tuple) and len(value) >= 2 and isinstance(value[1], int):
        value, status = value[0], value[1]
    if hasattr(value, "get_json"):
        try:
            return {"__flask__": True, "json": value.get_json(), "status": status}
        except Exception:
            return None
    return value


def _from_cacheable(value):
    """Rebuild a Flask response from a cached entry, or pass plain data back."""
    if isinstance(value, dict) and value.get("__flask__"):
        from flask import jsonify
        resp = jsonify(value.get("json"))
        return (resp, value.get("status", 200)) if value.get("status", 200) != 200 else resp
    return value


def cached(name: str, ttl: int = 900, key_args: bool = True):
    """Cache a function's return value.

    Fresh -> serve cache. Stale -> serve cache immediately and refresh in the
    background, so a caller never pays the network cost. Frozen -> serve cache
    whatever its age and never call through.
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = name
            if key_args and (args or kwargs):
                key += ":" + ":".join(
                    [str(a) for a in args] + [f"{k}={v}" for k, v in sorted(kwargs.items())])

            hit = read(key)
            if hit is not None:
                if not (frozen() or hit["age"] < ttl):
                    _refresh_async(key, fn, args, kwargs)   # stale-while-revalidate
                return _from_cacheable(hit["value"])

            if frozen():
                # Nothing cached and we must not call out. The caller decides
                # how to render an empty result; it will not hang.
                return None

            value = fn(*args, **kwargs)
            store = _to_cacheable(value)
            if _is_worth_caching(store):
                write(key, store)
            return value
        wrapper._cache_name = name
        return wrapper
    return decorator


def _refresh_async(key: str, fn: Callable, args, kwargs):
    with _lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def _work():
        try:
            value = fn(*args, **kwargs)
            store = _to_cacheable(value)
            if _is_worth_caching(store):
                write(key, store)
        except Exception as e:
            print(f"[CACHE] background refresh failed for {key}: {str(e)[:120]}")
        finally:
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=_work, name=f"cache-refresh-{key[:24]}", daemon=True).start()


def stats() -> Dict[str, Any]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT cache_key, created_at, hits, length(payload) AS bytes "
        "FROM response_cache ORDER BY cache_key").fetchall()
    conn.close()
    now = time.time()
    return {
        "frozen": frozen(),
        "entries": [{
            "key": r["cache_key"],
            "age_min": round((now - r["created_at"]) / 60, 1),
            "hits": r["hits"], "bytes": r["bytes"],
        } for r in rows],
        "total": len(rows),
    }


def clear(prefix: str = ""):
    conn = get_connection()
    if prefix:
        conn.execute("DELETE FROM response_cache WHERE cache_key LIKE ?", (prefix + "%",))
    else:
        conn.execute("DELETE FROM response_cache")
    conn.commit()
    conn.close()
