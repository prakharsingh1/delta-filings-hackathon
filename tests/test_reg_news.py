"""reg_news.py: URL clean-up, feed parsing (SEBI and RBI styles), topic
tagging, queries and the refresh loop (network mocked)."""
from tests import _util  # noqa: F401  (must come first)

import unittest
from datetime import datetime, timedelta
from unittest import mock

import reg_news as rn

SEBI_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>SEBI - Recent Developments</title><link>https://www.sebi.gov.in</link>
<item>
  <title>Master Circular for Stock Brokers</title>
  <link>https://www.sebi.gov.in/https://www.sebi.gov.in/legal/master-circulars/sep-2026/master-circular-for-stock-brokers_97001.html</link>
  <description>Master Circular for Stock Brokers</description>
  <pubDate>26 Sep, 2026 +0530</pubDate>
</item>
<item>
  <title>Adjudication order in respect of Mr. D&amp;#39;Souza in the matter of Acme &amp;amp; Sons Ltd</title>
  <link>https://www.sebi.gov.in/enforcement/orders/sep-2026/adjudication-order_97002.html</link>
  <description>&lt;p&gt;Adjudication order in respect of Mr. D&amp;#39;Souza&lt;/p&gt;</description>
  <pubDate>Thu, 25 Sep 2026 18:30:00 +0530</pubDate>
</item>
<item>
  <title>Recovery Certificate against Mr. Kamleshkumar Shah</title>
  <link>https://www.sebi.gov.in/enforcement/recovery-proceedings/sep-2026/recovery-certificate_97003.html</link>
  <description></description>
  <pubDate>24 Sep, 2026 +0530</pubDate>
</item>
<item>
  <title>Consultation paper on review of the Structured Digital Database under PIT Regulations</title>
  <link>https://www.sebi.gov.in/reports-and-statistics/reports/sep-2026/consultation_97004.html</link>
  <pubDate>23 Sep, 2026 +0530</pubDate>
</item>
<item>
  <title>Exemption order in the matter of Beta Industries Ltd</title>
  <link>https://www.sebi.gov.in/enforcement/orders/sep-2026/exemption-order_97005.html</link>
  <pubDate>22 Sep, 2026 +0530</pubDate>
</item>
<item><title>Item without a link</title><pubDate>21 Sep, 2026 +0530</pubDate></item>
<item><title>   </title><link>https://www.sebi.gov.in/blank-title.html</link></item>
<item><title>Relative link</title><link>/legal/circulars/relative.html</link></item>
</channel></rss>
"""

RBI_NOTIFICATIONS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>RBI Notifications</title>
<item>
  <title>Master Direction – Know Your Customer (KYC) Direction, 2016 – Amendment</title>
  <link>https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13001&amp;Mode=0</link>
  <description>&lt;p&gt;RBI/2026-27/88 DOR.AML.REC.45/14.01.001/2026-27 September 25, 2026&lt;/p&gt;&lt;p&gt;All Regulated Entities&lt;/p&gt;&lt;p&gt;Madam / Dear Sir,&lt;/p&gt;&lt;p&gt;Please refer to the Master Direction on KYC. The periodic updation timelines are revised as below.&lt;/p&gt;</description>
  <pubDate>Thu, 25 Sep 2026 17:00:00</pubDate>
</item>
</channel></rss>
"""

RBI_PRESS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>RBI Press Releases</title>
<item>
  <title>Reserve Bank of India imposes monetary penalty on Sample Co-operative Bank Ltd., Pune</title>
  <link>https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=61001</link>
  <description>The Reserve Bank of India has imposed a monetary penalty of ₹2.00 lakh on the bank for non-compliance with directions on KYC.</description>
  <pubDate>Fri, 26 Sep 2026 16:00:00</pubDate>
</item>
<item>
  <title>Money Market Operations as on September 25, 2026</title>
  <link>https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=61002</link>
  <description>(Amount in ₹ crore, Rate in Per cent) 1,23,456.78 6.50 5.25-6.75 2,34,567.89 6.48</description>
  <pubDate>Fri, 26 Sep 2026 09:00:00</pubDate>
</item>
</channel></rss>
"""


def parsed_all():
    return (rn.parse_feed(SEBI_RSS, "SEBI") + rn.parse_feed(RBI_NOTIFICATIONS, "RBI", "notification")
            + rn.parse_feed(RBI_PRESS, "RBI", "press release"))


class FixUrl(unittest.TestCase):
    def test_doubled_host(self):
        self.assertEqual(rn._fix_url("https://www.sebi.gov.in/https://www.sebi.gov.in/legal/circulars/sep-2026/x.html"),
                         "https://www.sebi.gov.in/legal/circulars/sep-2026/x.html")
        self.assertEqual(rn._fix_url("https://www.sebi.gov.in/http://www.sebi.gov.in/x.html"),
                         "http://www.sebi.gov.in/x.html")

    def test_normal_urls_unchanged(self):
        self.assertEqual(rn._fix_url("  https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=1&Mode=0 "),
                         "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=1&Mode=0")
        self.assertEqual(rn._fix_url(None), "")
        self.assertEqual(rn._fix_url(""), "")


class ParseFeed(unittest.TestCase):
    def test_sebi_feed(self):
        items = rn.parse_feed(SEBI_RSS, "SEBI")
        self.assertEqual(len(items), 5)  # no link, blank title and relative link are dropped
        by_kind = {i["kind"]: i for i in items}
        mc = items[0]
        self.assertEqual(mc["url"], "https://www.sebi.gov.in/legal/master-circulars/sep-2026/master-circular-for-stock-brokers_97001.html")
        self.assertEqual((mc["kind"], mc["topics"], mc["published_at"]),
                         ("master circular", "Intermediaries", "2026-09-26T00:00:00"))
        adj = items[1]
        # SEBI double-escapes entities; titles must read as plain text.
        self.assertEqual(adj["title"], "Adjudication order in respect of Mr. D'Souza in the matter of Acme & Sons Ltd")
        self.assertEqual(adj["summary"], "Adjudication order in respect of Mr. D'Souza")
        self.assertEqual((adj["kind"], adj["topics"], adj["published_at"]), ("order", "Enforcement", "2026-09-25T18:30:00"))
        self.assertEqual((by_kind["recovery"]["topics"], by_kind["recovery"]["source"]), ("Enforcement", "SEBI"))
        self.assertEqual((by_kind["consultation"]["topics"]), "Insider trading")
        exemption = [i for i in items if i["title"].startswith("Exemption")][0]
        self.assertEqual((exemption["kind"], exemption["topics"]), ("order", "Takeovers and M&A"))

    def test_rbi_feeds(self):
        [kyc] = rn.parse_feed(RBI_NOTIFICATIONS, "RBI", "notification")
        self.assertEqual((kyc["kind"], kyc["topics"]), ("notification", "KYC / AML"))
        self.assertTrue(kyc["summary"].startswith("RBI/2026-27/88 DOR.AML.REC.45"))
        self.assertNotIn("<p>", kyc["summary"])
        self.assertEqual(kyc["url"], "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13001&Mode=0")
        penalty, mmo = rn.parse_feed(RBI_PRESS, "RBI", "press release")
        self.assertEqual((penalty["kind"], penalty["topics"]), ("penalty", "Enforcement,Banking"))
        self.assertEqual((mmo["kind"], mmo["topics"]), ("press release", "Market operations"))
        self.assertEqual(mmo["published_at"], "2026-09-26T09:00:00")

    def test_garbage_feed(self):
        self.assertEqual(rn.parse_feed("<html>not a feed</html>", "SEBI"), [])
        self.assertEqual(rn.parse_feed("", "RBI"), [])


class Tagging(unittest.TestCase):
    def test_tag_topics(self):
        self.assertEqual(rn.tag_topics("Framework for Structured Digital Database under PIT"), ["Insider trading"])
        self.assertIn("LODR", rn.tag_topics("Amendments to SEBI (Listing Obligations and Disclosure Requirements) Regulations"))
        self.assertIn("Buybacks and delisting", rn.tag_topics("Review of the framework for buy-back of securities"))
        self.assertEqual(rn.tag_topics("Order in the matter of Kamleshkumar Traders"), ["Enforcement"])  # no AML
        self.assertIn("KYC / AML", rn.tag_topics("Prevention of Money Laundering (PMLA) - AML obligations"))
        # The issuer's name is not a subject.
        self.assertNotIn("Banking", rn.tag_topics("Reserve Bank of India releases annual report"))
        self.assertEqual(rn.tag_topics("Non-Banking Financial Companies - scale based regulation"), ["NBFC"])
        self.assertEqual(rn.tag_topics(""), [])

    def test_tag_item_rules(self):
        self.assertEqual(rn.tag_item("SEBI", "recovery", "Recovery certificate - insider trading in shares of X"),
                         ["Enforcement"])
        self.assertEqual(rn.tag_item("SEBI", "order", "Order in the matter of front running by dealers"),
                         ["Market abuse", "Enforcement"])
        self.assertNotIn("Enforcement", rn.tag_item("SEBI", "order", "Exemption order in the matter of an open offer"))
        self.assertEqual(rn.tag_item("RBI", "press release", "Auction of Treasury Bills and dated securities"),
                         ["Market operations"])
        # RBI: the department code in the reference line is a subject signal.
        self.assertIn("Payments", rn.tag_item("RBI", "notification", "Amendment to directions",
                                              "RBI/2026-27/12 CO.DPSS.POLC.No.S-123/02.14.003/2026-27"))

    def test_relevant_topics(self):
        self.assertEqual(rn.relevant_topics("cro", []), rn.ROLE_TOPICS["cro"])
        cs_new = rn.relevant_topics("cs", [])
        self.assertIn("LODR", cs_new)   # nothing hidden on day one
        cs_private = rn.relevant_topics("cs", [{"entity_type": "private", "flags": {"fdi": True}}])
        self.assertNotIn("LODR", cs_private)
        self.assertIn("FEMA", cs_private)
        pcs_listed = rn.relevant_topics("pcs", [{"entity_type": "private", "flags": {}},
                                                {"entity_type": "listed", "flags": {"top1000": True}}])
        self.assertTrue({"LODR", "Insider trading", "ESG and BRSR", "Governance and audit"} <= pcs_listed)
        self.assertEqual(rn.relevant_topics("nonsense", []), cs_new)
        cro_fdi = rn.relevant_topics("cro", [{"entity_type": "private", "flags": {"ecb": True}}])
        self.assertIn("FEMA", cro_fdi)

    def test_every_topic_label_is_storable(self):
        for t in rn.TOPICS:
            self.assertNotIn(",", t)


class Queries(unittest.TestCase):
    def setUp(self):
        _util.clear("regulatory_news", "regulatory_news_meta")
        self.assertEqual(rn.store_items(parsed_all()), 8)

    def titles(self, **kw):
        return [i["title"][:20] for i in rn.list_news(**kw)]

    def test_store_is_idempotent(self):
        self.assertEqual(rn.store_items(parsed_all()), 0)
        self.assertEqual(rn.count_all(), 8)

    def test_order_and_paging(self):
        items = rn.list_news()
        self.assertEqual([i["published_at"] for i in items], sorted((i["published_at"] for i in items), reverse=True))
        self.assertEqual(items[0]["kind"], "penalty")
        page = rn.list_news(limit=2, offset=1)
        self.assertEqual([i["id"] for i in page], [i["id"] for i in items[1:3]])

    def test_filters(self):
        n = lambda **kw: len(rn.list_news(**kw))
        self.assertEqual(n(source="RBI"), 3)
        self.assertEqual(n(source="SEBI"), 5)
        self.assertEqual(n(kind="order"), 2)
        self.assertEqual(n(topic="Enforcement"), 3)
        self.assertEqual(n(topic="Enforce"), 0)                   # whole topic only
        self.assertEqual(self.titles(q="monetary"), ["Reserve Bank of Indi"])
        self.assertEqual(n(q="%"), 0)                             # LIKE wildcards are escaped
        self.assertEqual(n(q="_"), 0)
        self.assertEqual(n(topics_any=[]), 0)
        self.assertEqual(n(topics_any=["KYC / AML", "Insider trading"]), 2)
        self.assertEqual(n(kinds=rn.CIRCULAR_KINDS), 2)
        self.assertEqual(n(exclude_kinds=rn.ENFORCEMENT_KINDS), 4)
        self.assertEqual(n(source="SEBI", kind="order", topic="Enforcement"), 1)

    def test_decoration(self):
        items = {i["kind"]: i for i in rn.list_news()}
        self.assertEqual(items["master circular"]["kind_label"], "Master circular")
        self.assertEqual(items["penalty"]["topic_list"], ["Enforcement", "Banking"])
        self.assertEqual(items["press release"]["excerpt"], "")   # market operations table
        self.assertEqual(items["notification"]["excerpt"][:30], "Please refer to the Master Dir")
        self.assertIsNotNone(rn.get_item(items["penalty"]["id"]))
        self.assertIsNone(rn.get_item(99_999_999))

    def test_topic_counts(self):
        self.assertEqual(rn.topic_counts(), {"Insider trading": 1, "Takeovers and M&A": 1, "Intermediaries": 1,
                                             "Enforcement": 3, "Banking": 1, "KYC / AML": 1, "Market operations": 1})
        self.assertEqual(rn.topic_counts(source="RBI"), {"Enforcement": 1, "Banking": 1, "KYC / AML": 1,
                                                         "Market operations": 1})

    def test_enforcement_items(self):
        got = sorted(i["kind"] for i in rn.enforcement_items(since_days=36500))
        self.assertEqual(got, ["order", "recovery"])            # the exemption order is left out
        _util.clear("regulatory_news")
        old = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
        new = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
        rn.store_items([
            {"source": "SEBI", "kind": "order", "title": "Order in the matter of Old Co", "url": "https://x/old",
             "published_at": old},
            {"source": "SEBI", "kind": "order", "title": "Order in the matter of New Co", "url": "https://x/new",
             "published_at": new},
            {"source": "RBI", "kind": "penalty", "title": "Penalty on a bank", "url": "https://x/rbi",
             "published_at": new},
        ])
        self.assertEqual([i["url"] for i in rn.enforcement_items(since_days=365)], ["https://x/new"])


def fake_get(url, **kwargs):
    r = mock.Mock()
    body = {rn.FEEDS[0]["url"]: SEBI_RSS, rn.FEEDS[1]["url"]: RBI_NOTIFICATIONS}.get(url)
    r.status_code = 200 if body else 500
    r.content = (body or "").encode("utf-8")
    return r


class Refresh(unittest.TestCase):
    def setUp(self):
        _util.clear("regulatory_news", "regulatory_news_meta")

    def test_refresh_with_mocked_feeds(self):
        with mock.patch("reg_news._frozen", return_value=False), \
                mock.patch("requests.get", side_effect=fake_get) as get, _util.quiet():
            self.assertEqual(rn.refresh(force=True), 6)          # 5 SEBI + 1 RBI; press feed returns 500
            self.assertEqual(get.call_count, 3)
            self.assertIsNotNone(rn.last_refresh())
            self.assertEqual(rn.refresh(), 0)                    # not stale yet
            self.assertEqual(get.call_count, 3)
            self.assertEqual(rn.refresh(force=True), 0)          # nothing new
        self.assertFalse(rn.refreshing())

    def test_failed_fetch_does_not_count_as_refresh(self):
        with mock.patch("reg_news._frozen", return_value=False), \
                mock.patch("requests.get", side_effect=OSError("offline")), _util.quiet():
            self.assertEqual(rn.refresh(force=True), 0)
        self.assertIsNone(rn.last_refresh())

    def test_frozen_never_calls_out(self):
        with mock.patch("requests.get") as get:
            self.assertEqual(rn.refresh(force=True), 0)
            get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
