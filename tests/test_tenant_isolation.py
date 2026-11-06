"""One account must never change or see another account's data, and nobody
but the operator changes settings that apply to every account."""
from tests import _util  # noqa: F401  (must come first)

import os
import unittest
from unittest import mock

import main
import events_store as es
from database import get_setting


def client_for(user_id, email):
    c = main.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user_id
        s["user_cache"] = {"id": user_id, "email": email, "name": email, "onboarded": True}
    return c


class PlatformSettings(unittest.TestCase):
    def setUp(self):
        main.app.config["TESTING"] = True
        self.user = client_for(_util.uid(), "someone@example.com")
        self.admin = client_for(_util.uid(), "ops@example.com")

    def test_a_signed_in_user_cannot_replace_the_shared_ai_key(self):
        before = get_setting("gemini_api_key", "")
        with mock.patch.dict(os.environ, {"DELTA_ADMIN_EMAILS": "ops@example.com"}):
            r = self.user.post("/api/settings", json={"gemini_api_key": "AIza-attacker"})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(get_setting("gemini_api_key", ""), before)
            self.assertEqual(self.user.post("/api/settings", json={"filings_history_years": 15}).status_code, 403)
            self.assertEqual(self.user.post("/api/settings/test-key", json={"api_key": "x"}).status_code, 403)
            self.assertFalse(self.user.get("/api/settings").get_json()["can_edit_platform"])

    def test_the_operator_can(self):
        with mock.patch.dict(os.environ, {"DELTA_ADMIN_EMAILS": "Ops@Example.com, other@example.com"}):
            r = self.admin.post("/api/settings", json={"filings_history_years": 5})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(get_setting("filings_history_years", ""), "5")
            self.assertTrue(self.admin.get("/api/settings").get_json()["can_edit_platform"])

    def test_with_no_operator_configured_nobody_can(self):
        with mock.patch.dict(os.environ, {"DELTA_ADMIN_EMAILS": ""}):
            self.assertEqual(self.admin.post("/api/settings", json={"filings_history_years": 5}).status_code, 403)


class EventDecisions(unittest.TestCase):
    def setUp(self):
        main.app.config["TESTING"] = True
        with _util.quiet():
            es.init_events_db()
        self.event_id = es.upsert_event(
            dedupe_key=f"t-{_util.uid()}", symbol="TESTCO", event_type="director_change",
            event_label="Director change", category="Board", title="Change in directors",
            headline="A director resigned", importance="high")
        if isinstance(self.event_id, dict):
            self.event_id = self.event_id["id"]
        self.a, self.b = _util.uid("a"), _util.uid("b")
        self.ca = client_for(self.a, "a@example.com")
        self.cb = client_for(self.b, "b@example.com")

    def test_one_accounts_decision_is_not_anothers(self):
        r = self.ca.post(f"/api/event/{self.event_id}/decision",
                         json={"decision": "dismissed", "note": "private note from A"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(es.get_event(self.event_id, self.a)["status"], "dismissed")
        self.assertNotEqual(es.get_event(self.event_id, self.b)["status"], "dismissed")
        trail_b = es.get_audit_trail(self.event_id, self.b)
        self.assertFalse([t for t in trail_b if "a@example.com" in (t["actor"] or "")
                          or "private note" in (t["detail"] or "")])
        self.assertTrue([t for t in es.get_audit_trail(self.event_id, self.a) if t["detail"] == "private note from A"])

    def test_b_deciding_does_not_override_a(self):
        self.ca.post(f"/api/event/{self.event_id}/decision", json={"decision": "approved"})
        self.cb.post(f"/api/event/{self.event_id}/decision", json={"decision": "escalated"})
        self.assertEqual(es.get_event(self.event_id, self.a)["status"], "approved")
        self.assertEqual(es.get_event(self.event_id, self.b)["status"], "escalated")


class ResponseCache(unittest.TestCase):
    """Cached market data is shared; anything about the user is not cached."""

    def setUp(self):
        main.app.config["TESTING"] = True
        import demo_cache
        self.dc = demo_cache
        with _util.quiet():
            demo_cache.init_cache_db()
        demo_cache.clear("prices")
        self.env = mock.patch.dict(os.environ, {"DELTA_DEMO_FREEZE": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(demo_cache.clear, "prices")

    def test_one_users_fired_alerts_are_never_served_to_another(self):
        fired = lambda uid, prices: [{"alert": "for " + uid}]
        with mock.patch.object(main, "fetch_watchlist_prices", return_value={"TESTCO": {"pChange": 0.1}}), \
             mock.patch.object(main, "evaluate_user_alerts", side_effect=fired):
            a, b = _util.uid("a"), _util.uid("b")
            ra = client_for(a, "a@example.com").get("/api/prices").get_json()
            rb = client_for(b, "b@example.com").get("/api/prices").get_json()
        self.assertEqual(ra["fired_alerts"], [{"alert": "for " + a}])
        self.assertEqual(rb["fired_alerts"], [{"alert": "for " + b}])
        self.assertEqual(rb["prices"], {"TESTCO": {"pChange": 0.1}})

    def test_a_stale_entry_is_refreshed_in_the_background(self):
        import threading
        calls = []

        @self.dc.cached("prices_t", ttl=60)
        def compute(x):
            from flask import jsonify   # needs the app context the refresh must provide
            calls.append(x)
            return jsonify({"n": len(calls)})

        self.addCleanup(self.dc.clear, "prices_t")
        with main.app.test_request_context():
            compute("k")                        # miss: computed and stored
            conn = _util.connect()
            conn.execute("UPDATE response_cache SET created_at = created_at - 3600 WHERE cache_key = 'prices_t:k'")
            conn.commit(); conn.close()
            compute("k")                        # stale: served, refresh started
        for t in [t for t in threading.enumerate() if t.name.startswith("cache-refresh-prices_t")]:
            t.join(5)
        hit = self.dc.read("prices_t:k")
        self.assertLess(hit["age"], 60)
        self.assertEqual(hit["value"]["json"], {"n": 2})


class AnonymousApi(unittest.TestCase):
    def test_market_endpoints_need_a_session(self):
        main.app.config["TESTING"] = True
        c = main.app.test_client()
        for path in ("/api/prices", "/api/pre-open", "/api/sector", "/api/compliance"):
            with self.subTest(path=path):
                self.assertEqual(c.get(path).status_code, 401)


class WatchlistInput(unittest.TestCase):
    def test_a_symbol_must_look_like_one(self):
        main.app.config["TESTING"] = True
        c = client_for(_util.uid(), "w@example.com")
        r = c.post("/api/watchlist", json={"symbol": "<img src=x onerror=alert(1)>"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
