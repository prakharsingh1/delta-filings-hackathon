"""Company news desk: the headline-to-filing map, de-duplication across
editions, and the News tab routes. No network: _util freezes demo mode."""
from tests import _util  # noqa: F401  (must come first)

import unittest
from datetime import date
from unittest import mock

import company_news as cn
import database
import main


class HeadlineClassifier(unittest.TestCase):
    def test_leadership_change_starts_dir12_and_reg30(self):
        hints = cn.filing_hints("HUL appoints Priya Nair as new CEO and Managing Director", date(2026, 11, 5))
        forms = {h["form"] for h in hints}
        self.assertIn("DIR-12", forms)
        self.assertIn("Reg 30", forms)
        self.assertTrue(all(h["certain"] for h in hints))

    def test_deadline_counts_from_the_story_date(self):
        hints = cn.filing_hints("Company allots 1.2 lakh shares under ESOP scheme", date(2026, 11, 5))
        pas3 = next(h for h in hints if h["form"] == "PAS-3")
        self.assertIn("by 20 Nov 2026", pas3["due"])

    def test_price_commentary_starts_nothing(self):
        self.assertEqual(cn.filing_hints("Shares fall 2% on weak rural demand commentary", date(2026, 11, 5)), [])

    def test_material_event_is_flagged_as_uncertain(self):
        hints = cn.filing_hints("Britannia to acquire 51% stake in a bakery start-up", date(2026, 11, 5))
        self.assertEqual([h["form"] for h in hints], ["Reg 30"])
        self.assertFalse(hints[0]["certain"])
        self.assertIn("check materiality", hints[0]["due"])

    def test_factor_tags_are_explainable(self):
        self.assertEqual(cn.factor_tags("Weak rural demand hits volumes"), ["Demand & consumption"])
        self.assertEqual(cn.factor_tags("Plain headline"), [])

    def test_clean_name_keeps_india_but_drops_the_suffix(self):
        self.assertEqual(cn.clean_name("Nestle India Limited"), "Nestle India")
        self.assertEqual(cn.clean_name("ITC Ltd."), "ITC")
        self.assertEqual(cn.clean_name("Britannia Industries"), "Britannia Industries")


class Dedupe(unittest.TestCase):
    def test_same_story_from_two_editions_keeps_both_countries(self):
        a = cn._item("press", "Bloomberg", "Nestle, Unilever face India action", "https://x/1", None, "", "IN")
        b = cn._item("press", "Bloomberg", "Nestle, Unilever face India action", "https://x/2", None, "", "US")
        out = cn._dedupe([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(sorted(out[0]["countries"]), ["IN", "US"])

    def test_signals_group_by_obligation(self):
        items = cn._annotate([
            cn._item("press", "Mint", "Board approves interim dividend of Rs 6", "https://x/3", None, "", "IN"),
            cn._item("press", "ET", "Outcome of board meeting: interim dividend declared", "https://x/4", None, "", "IN"),
        ])
        groups = cn._signals(items)
        reg30_6 = next(g for g in groups if g["form"] == "Reg 30(6)")
        self.assertEqual(len(reg30_6["stories"]), 2)


class PriceStore(unittest.TestCase):
    def test_store_round_trip_and_quote_does_not_overwrite_a_real_close(self):
        cn.store_points("TESTCO", [{"date": "2026-11-03", "close": 100.0}, {"date": "2026-11-04", "close": 102.0}], "Moneycontrol")
        cn.store_points("TESTCO", [{"date": "2026-11-04", "close": 999.0}], "quote")
        pts = cn.stored_points("TESTCO", days=3650)
        self.assertEqual([p["close"] for p in pts], [100.0, 102.0])
        self.assertEqual(pts[1]["pct"], 2.0)

    def test_quote_seeds_the_store(self):
        cn.seed_quote("SEEDCO", {"lastPrice": 50.5, "lastUpdateTime": "05-Nov-2026 15:28 IST"})
        self.assertEqual([(p["date"], p["close"]) for p in cn.stored_points("SEEDCO", days=3650)], [("2026-11-05", 50.5)])

    def test_series_falls_back_to_the_store_when_every_feed_fails(self):
        cn.store_points("FALLCO", [{"date": "2026-11-03", "close": 10.0}, {"date": "2026-11-04", "close": 11.0}], "Yahoo Finance")
        failing = [(name, lambda s: (_ for _ in ()).throw(RuntimeError("down"))) for name, _ in cn.PRICE_SOURCES]
        with mock.patch.object(cn, "PRICE_SOURCES", failing), mock.patch.object(cn.demo_cache, "frozen", return_value=False):
            pts, source, note, checks, verified = cn.price_series("FALLCO", quote=None)
        self.assertEqual(source, "stored closes")
        self.assertEqual(len(pts), 2)
        self.assertFalse(verified)
        self.assertIn("Moneycontrol: down", note)

    def test_two_sources_are_cross_checked_and_the_quote_breaks_ties(self):
        good = cn._points_from([(date(2026, 11, 3), 100.0), (date(2026, 11, 4), 102.0)])
        bad = cn._points_from([(date(2026, 11, 3), 100.0), (date(2026, 11, 4), 130.0)])  # a stale or wrong feed
        sources = [("Feed A", lambda s: bad), ("Feed B", lambda s: good)]
        quote = {"lastPrice": 103.0, "previousClose": 102.0, "lastUpdateTime": "05-Nov-2026 15:28 IST"}
        with mock.patch.object(cn, "PRICE_SOURCES", sources), mock.patch.object(cn.demo_cache, "frozen", return_value=False), \
             mock.patch.object(cn.demo_cache, "read", return_value=None), mock.patch.object(cn.demo_cache, "write"):
            pts, source, note, checks, verified = cn.price_series("XCHECK", quote=quote)
        self.assertEqual(source, "Feed B")
        self.assertEqual(pts[-1]["close"], 102.0)
        self.assertFalse(verified)  # the two feeds still disagree on 4 Nov, and that is reported
        self.assertTrue(any("switched to Feed B" in c["check"] for c in checks))
        self.assertTrue(any(c["gap_pct"] and c["gap_pct"] > 20 for c in checks if "vs" in c["check"]))

    def test_agreeing_sources_verify(self):
        good = cn._points_from([(date(2026, 11, 3), 100.0), (date(2026, 11, 4), 102.0)])
        sources = [("Feed A", lambda s: good), ("Feed B", lambda s: [dict(p) for p in good])]
        quote = {"lastPrice": 103.0, "previousClose": 102.0, "lastUpdateTime": "05-Nov-2026 15:28 IST"}
        with mock.patch.object(cn, "PRICE_SOURCES", sources), mock.patch.object(cn.demo_cache, "frozen", return_value=False), \
             mock.patch.object(cn.demo_cache, "read", return_value=None), mock.patch.object(cn.demo_cache, "write"):
            pts, source, note, checks, verified = cn.price_series("AGREE", quote=quote)
        self.assertTrue(verified)
        self.assertEqual(source, "Feed A")


class NewsRoutes(unittest.TestCase):
    def setUp(self):
        main.app.config["TESTING"] = True
        self.c = main.app.test_client()
        self.uid = "news-test-user"
        with self.c.session_transaction() as s:
            s["user_id"] = self.uid
            s["user_cache"] = {"id": self.uid, "email": "news@example.com", "name": "News", "onboarded": True}
        database.add_to_watchlist(self.uid, "ITC", "ITC Limited")

    def test_news_tab_opens_the_company_desk(self):
        r = self.c.get("/news")
        self.assertEqual(r.status_code, 302)  # the URL names the company so Copilot can read it
        self.assertTrue(r.headers["Location"].endswith("/news?symbol=ITC"))
        r = self.c.get("/news", follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("cn-page", body)
        self.assertIn('data-symbol="ITC"', body)
        for bad in ("Traceback", "Undefined", "{{", "{%"):
            self.assertNotIn(bad, body)

    def test_regulatory_feed_still_reachable(self):
        r = self.c.get("/news?view=regulatory")
        self.assertEqual(r.status_code, 200)
        self.assertIn("nw-page", r.get_data(as_text=True))

    def test_unknown_symbol_falls_back_to_the_watchlist(self):
        r = self.c.get("/news?symbol=NOPE", follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('data-symbol="ITC"', r.get_data(as_text=True))

    def test_api_refuses_symbols_outside_the_watchlist(self):
        self.assertEqual(self.c.get("/api/news/company/INFY").status_code, 404)

    def test_api_frozen_without_cache_makes_no_fetch(self):
        r = self.c.get("/api/news/company/ITC")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["frozen"])


if __name__ == "__main__":
    unittest.main()
