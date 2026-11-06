"""risk.py: name matching, bulk paste parsing, the filing classifier, IBBI
parsing, scoring, alerts, portfolio roll-ups and the director network."""
from tests import _util  # noqa: F401  (must come first)

import unittest
from datetime import date, timedelta
from unittest import mock

import entities as ents
import risk

TODAY = date.today()


def classify(title, description="", body=""):
    return risk.classify_filing(title, description, body)


class Names(unittest.TestCase):
    def test_norm_name(self):
        self.assertEqual(risk.norm_name("Tata Steel Limited"), "tata steel")
        self.assertEqual(risk.norm_name("The Bombay Dyeing & Mfg. Co. Ltd."), "bombay dyeing and mfg")
        self.assertEqual(risk.norm_name("ABC Pvt. Ltd."), "abc")
        self.assertEqual(risk.norm_name("  M/s.  Gamma   Corporation  "), "m s gamma")
        self.assertEqual(risk.norm_name(None), "")

    def test_name_match(self):
        self.assertEqual(risk.name_match("tata steel", "tata steel"), "exact")
        self.assertEqual(risk.name_match("tata steel", "tata steel bsl"), "partial")
        self.assertEqual(risk.name_match("tata steel", "corporate debtor tata steel bsl unit"), "partial")
        # One-word names never match partially, and partial needs whole words.
        self.assertIsNone(risk.name_match("tata", "tata steel"))
        self.assertIsNone(risk.name_match("tata steel", "tata steelworks"))
        self.assertIsNone(risk.name_match("tata steel", "jsw steel"))
        self.assertIsNone(risk.name_match("", "tata steel"))
        self.assertIsNone(risk.name_match("tata steel", ""))

    def test_mask_pan(self):
        self.assertEqual(risk.mask_pan("AAACA1234B"), "AA*****34B")
        self.assertEqual(risk.mask_pan(""), "")

    def test_sebi_match(self):
        m = lambda title: risk.sebi_match("Acme Widgets Limited", title)
        self.assertEqual(m("Adjudication order in respect of Acme Widgets Limited in the matter of XYZ Ltd")[0], "high")
        self.assertEqual(m("Settlement order in respect of Mr. Ravi Kumar in the matter of Acme Widgets Limited")[0], "medium")
        self.assertEqual(m("Order in the matter of insider trading in the shares of Acme Widgets Ltd")[0], "low")
        self.assertIsNone(m("Order in the matter of Beta Industries Limited"))


class ParseBulk(unittest.TestCase):
    def test_comma_line_with_known_symbol_category_and_crore(self):
        [r] = risk.parse_bulk("Tata Steel Limited, TATASTEEL, borrower, 50 cr")
        self.assertEqual(r, {"name": "Tata Steel Limited", "symbol": "TATASTEEL", "cin": "", "pan": "",
                             "category": "borrower", "exposure_inr": 5e8})

    def test_symbol_alone_takes_the_listed_name(self):
        [r] = risk.parse_bulk("ITC")
        self.assertEqual((r["name"], r["symbol"]), ("ITC Limited", "ITC"))

    def test_tab_separated_spreadsheet_row(self):
        [r] = risk.parse_bulk("Acme Widgets Pvt Ltd\tU12345MH2010PTC123456\taaaca1234b\tvendor\t1,50,00,000")
        self.assertEqual((r["name"], r["cin"], r["pan"], r["category"], r["exposure_inr"]),
                         ("Acme Widgets Pvt Ltd", "U12345MH2010PTC123456", "AAACA1234B", "vendor", 1.5e7))

    def test_pipes_semicolons_llpin_and_units(self):
        got = risk.parse_bulk("Beta Finance | 25 lakh | supplier\n"
                              "Gamma LLP, AAB-1234, Rs. 10 lakh\n"
                              "Delta Traders; ₹ 2.5 crore; guarantor\n"
                              "Epsilon Foods, 5 lacs, customer")
        self.assertEqual([(r["name"], r["cin"], r["category"], r["exposure_inr"]) for r in got], [
            ("Beta Finance", "", "vendor", 2.5e6),
            ("Gamma LLP", "AAB-1234", "other", 1e6),
            ("Delta Traders", "", "guarantor", 2.5e7),
            ("Epsilon Foods", "", "customer", 5e5),
        ])

    def test_blank_and_nameless_lines_are_skipped(self):
        self.assertEqual(risk.parse_bulk("\n   \nborrower, 10 cr\n"), [])
        self.assertEqual(risk.parse_bulk(""), [])
        self.assertEqual(risk.parse_bulk(None), [])

    def test_name_that_squeezes_to_a_symbol_stays_the_name(self):
        [r] = risk.parse_bulk("Tata Steel, TATASTEEL, borrower, 250 cr")
        self.assertEqual((r["name"], r["symbol"], r["category"], r["exposure_inr"]),
                         ("Tata Steel", "TATASTEEL", "borrower", 2.5e9))

    def test_cin_or_llpin_right_before_an_amount_stays_whole(self):
        # Guard for the Indian-grouping fix below: a CIN/LLPIN ends in digits,
        # so joining every "digit,digit" would glue the amount onto it.
        got = risk.parse_bulk("Acme Pvt Ltd,U12345MH2010PTC123456,50 cr\nGamma LLP,AAB-1234,25 lakh")
        self.assertEqual([(r["cin"], r["exposure_inr"]) for r in got],
                         [("U12345MH2010PTC123456", 5e8), ("AAB-1234", 2.5e6)])

    # BUG (risk.parse_bulk): on a comma-separated line an Indian-grouped amount
    # is split into digit groups ("1", "50", "00", "000") and the last group
    # wins, so the exposure is silently wrong (0 here; "12,500" becomes 500).
    def test_comma_line_with_indian_grouped_amount(self):
        [r] = risk.parse_bulk("Omega Ltd, 1,50,00,000, borrower")
        self.assertEqual(r["exposure_inr"], 1.5e7)


# (title, description, body, expected signal_key or None, expected severity or None)
CLASSIFY_CASES = [
    # Insolvency
    ("Disclosure under Regulation 30", "Admission of application under Section 7 of IBC by Hon'ble NCLT against the Company", "",
     "insolvency", "critical"),
    ("Insolvency and Bankruptcy", "Intimation of filing of petition under Section 9 of the Insolvency and Bankruptcy Code by an operational creditor", "",
     "insolvency_petition", "high"),
    ("Insolvency and Bankruptcy", "NCLT dismissed the petition filed under Section 9 of the Insolvency and Bankruptcy Code", "",
     "insolvency_closed", "low"),
    ("Insolvency and Bankruptcy", "Initiation of corporate insolvency resolution process against wholly owned subsidiary", "",
     "insolvency_subsidiary", "high"),
    # NCLT schemes of arrangement are not insolvency
    ("Scheme of Arrangement", "Order of Hon'ble NCLT sanctioning the Scheme of Arrangement between the Company and its shareholders and creditors", "",
     None, None),
    ("Disclosure under Regulation 30", "Order of the NCLT approving the scheme of arrangement for demerger of the consumer business", "",
     None, None),
    ("Action(s) taken or orders passed", "Hon'ble NCLT, Mumbai Bench has sanctioned the Scheme of Amalgamation of ABC Limited with the Company", "",
     "order_disclosed", "low"),
    # Liquidation
    ("Disclosure under Regulation 30", "Order of liquidation passed by the NCLT against the Company", "", "liquidation", "critical"),
    ("Disclosure under Regulation 30", "Voluntary liquidation of step-down subsidiary", "", None, None),
    # Default and lender action
    ("Default", "Delay in payment of interest on Non-Convertible Debentures due on 15 September 2026", "", "default", "critical"),
    ("Disclosure of default", "Disclosure of default on payment of interest/repayment of principal amount on loans", "",
     "default", "high"),
    ("Disclosure of default", "Disclosure of default - Nil", "", None, None),
    ("Certificate under Regulation 57", "There is no default in payment of interest and principal on NCDs", "", None, None),
    ("Intimation", "There has been no delay in payment of interest on NCDs", "", None, None),
    ("Disclosure under Regulation 30", "Loan account of the Company classified as NPA by the consortium of lenders", "", "npa", "critical"),
    ("Disclosure under Regulation 30", "The Company has been declared a wilful defaulter by State Bank of India", "",
     "wilful_defaulter", "critical"),
    ("Disclosure under Regulation 30", "Initiation of forensic audit by lenders", "", "forensic_audit", "critical"),
    # Auditors
    ("Resignation of Statutory Auditors", "M/s ABC & Co. have resigned as statutory auditors of the Company", "",
     "auditor_resigned", "high"),
    ("Resignation of Secretarial Auditor", "M/s PQR Associates have resigned as secretarial auditor", "",
     "auditor_resigned_other", "low"),
    ("Change in Auditors", "Change in Auditors", "", "auditor_change", "low"),
    ("Change in Auditors", "Appointment of M/s XYZ & Co. as Statutory Auditors on completion of tenure of M/s ABC & Co.", "",
     None, None),
    # Ratings
    ("Credit Rating", "CARE Ratings has revised the rating on long-term bank facilities from CARE AA-; Stable to CARE A+; Stable", "",
     "rating_adverse", "high"),
    ("Credit Rating", "CARE Ratings has revised the rating from CARE A+; Stable to CARE AA-; Stable", "", None, None),
    ("Credit Rating", "Rating on commercial paper revised from CRISIL A1+ to CRISIL A1", "", "rating_adverse", "high"),
    ("Credit Rating", "Intimation of downgrade in credit rating by CRISIL", "", "rating_adverse", "high"),
    ("Credit Rating", "Credit rating downgraded to D by India Ratings", "", "rating_adverse", "high"),
    ("Credit Rating", "ICRA has reaffirmed the rating of [ICRA]AA+ (Stable) on the bank facilities", "", None, None),
    ("Credit Rating", "CRISIL has placed the rating on Rating Watch with Negative Implications", "", "rating_watch", "medium"),
    ("Credit Rating", "CRISIL reaffirmed the rating; outlook revised to Negative from Stable", "", "rating_watch", "medium"),
    ("Credit Rating", "ESG rating assigned by CRISIL ESG Ratings", "", None, None),
    # Pledges
    ("Disclosure of encumbrance", "Disclosure of invocation of pledge of shares held by promoter", "", "pledge_invoked", "high"),
    ("Disclosure of encumbrance", "Creation of pledge on shares held by promoter group entity", "", "pledge", "medium"),
    ("Disclosure of encumbrance", "Release of pledge on shares held by promoter", "", None, None),
    ("Disclosure", "Company pledges support to relief efforts in flood-affected districts", "", None, None),
    # Board and management
    ("Resignation of Independent Director", "Resignation of Mr. A as Independent Director",
     "He cited governance concerns and lack of information from management.", "id_resigned", "high"),
    ("Resignation of Independent Director", "Resignation of Mr. B as Independent Director", "Upon completion of his second term",
     "id_resigned_tenure", "low"),
    ("Resignation of Independent Director", "Resignation of Mr. C as Independent Director", "", "id_resigned", "medium"),
    ("Change in Management", "Resignation of Chief Financial Officer", "", "kmp_resigned", "medium"),
    # Regulatory
    ("Action(s) taken or orders passed", "Show cause notice issued by SEBI under the SEBI Act", "", "regulatory_action", "medium"),
    ("Action(s) taken or orders passed", "Order passed by the Assistant Commissioner of State Tax under GST raising a tax demand", "",
     "tax_order", "low"),
    ("Action(s) taken or orders passed", "Hon'ble High Court granted a stay on the GST demand order", "",
     "regulatory_update", "low"),
    # Routine
    ("Board Meeting Intimation", "Board meeting to consider financial results for the quarter ended 30 September 2026", "", None, None),
    ("Trading Window", "Closure of trading window", "", None, None),
]


class ClassifyFiling(unittest.TestCase):
    def test_table(self):
        for title, desc, body, key, sev in CLASSIFY_CASES:
            with self.subTest(description=desc[:70]):
                got = classify(title, desc, body)
                if key is None:
                    self.assertIsNone(got)
                else:
                    self.assertIsNotNone(got)
                    self.assertEqual(got[:2], (key, sev))
                    self.assertTrue(got[2])

    def test_body_settles_nil_default_disclosure(self):
        title, desc = "Disclosure of default", "Disclosure of default on payment of interest/repayment of principal amount on loans"
        self.assertIsNone(classify(title, desc, "Total amount outstanding: Nil. No default as on date."))

    def test_body_alone_never_raises_a_signal(self):
        self.assertIsNone(classify("Press Release", "Company launches new product",
                                   "The lender has filed for insolvency under Section 7 of the IBC"))

    # BUG (risk.classify_filing): "has not defaulted" is a negation, but
    # _DEFAULT_NEGATION only knows "no default"/"not in default", so a Reg 57
    # certificate confirming payment raises a critical default alert.
    def test_not_defaulted_is_not_a_default(self):
        self.assertIsNone(classify("Certificate under Regulation 57",
                                   "The Company has not defaulted in payment of interest on NCDs due during the quarter"))

    # BUG (risk.classify_filing): "financial creditor(s)" / "operational
    # creditor(s)" are IBC context words, so an NCLT scheme of arrangement that
    # mentions a creditors' meeting reads as an insolvency petition (high).
    def test_scheme_with_creditor_meetings_is_not_insolvency(self):
        cases = [
            ("Disclosure under Regulation 30", "Outcome of the meeting of equity shareholders and secured financial "
                                               "creditors convened by NCLT to approve the Scheme of Arrangement"),
            ("Updates", "NCLT directed the Company to convene meetings of its financial creditors and operational "
                        "creditors for the Composite Scheme of Arrangement"),
        ]
        for title, desc in cases:
            got = classify(title, desc)
            self.assertFalse(got and got[0].startswith("insolvency"), (desc, got))

    # BUG (risk._classify_rating): the rating-context pattern matches "care" as
    # a bare word, so "health care ... revised" becomes "Credit rating revised".
    def test_health_care_is_not_a_rating_agency(self):
        self.assertIsNone(classify("Disclosure under Regulation 30", "Health care division guidance revised for FY27"))


# Announcement dates move with the calendar: scan_counterparty only reads IBBI
# rows from the last LOOKBACK_DAYS (365), so fixed dates would stop matching.
CIRP_DAY = TODAY - timedelta(days=14)
LIQ_DAY = TODAY - timedelta(days=59)

IBBI_HTML = f"""
<html><body>
<table class="table">
  <tr><th>Type of PA</th><th>Date of Announcement</th><th>Last date of Submission</th>
      <th>Name of Corporate Debtor</th><th>Name of Applicant</th><th>Name of Insolvency Professional</th>
      <th>Public Announcement</th></tr>
  <tr><td>Corporate Insolvency Resolution Process</td><td>{CIRP_DAY:%d-%m-%Y}</td><td>29-09-2026</td>
      <td>Acme Widgets Private Limited</td><td>State Bank of India</td><td>Ramesh Kumar</td>
      <td><a href="https://ibbi.gov.in//uploads/announcement/acme.pdf">View</a></td></tr>
  <tr><td>Liquidation</td><td>{LIQ_DAY:%d-%m-%Y}</td><td>31-08-2026</td>
      <td>Acme Widgets Exports Private Limited</td><td>Resolution Professional</td><td>Sunita Rao</td>
      <td></td></tr>
  <tr><td>Corporate Insolvency Resolution Process</td><td>not a date</td><td></td><td>Broken Row Ltd</td>
      <td>X</td><td>Y</td><td></td></tr>
  <tr><td>Too</td><td>few cells</td></tr>
</table>
</body></html>
"""


def fake_response(status=200, text=""):
    r = mock.Mock()
    r.status_code = status
    r.text = text
    return r


class Ibbi(unittest.TestCase):
    def setUp(self):
        # scan_counterparty also reads SEBI orders from regulatory_news, which
        # test_reg_news leaves populated.
        _util.clear("ibbi_announcements", "regulatory_news")
        self.user = _util.uid()

    def test_parse_fixture(self):
        got = risk._parse_ibbi(IBBI_HTML)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], {
            "pa_type": "Corporate Insolvency Resolution Process", "announce_date": CIRP_DAY.isoformat(),
            "last_submission": "29-09-2026", "debtor_name": "Acme Widgets Private Limited",
            "applicant": "State Bank of India", "ip_name": "Ramesh Kumar",
            "pdf_url": "https://ibbi.gov.in/uploads/announcement/acme.pdf",
        })
        self.assertEqual((got[1]["pa_type"], got[1]["announce_date"], got[1]["pdf_url"]), ("Liquidation", LIQ_DAY.isoformat(), ""))
        self.assertEqual(risk._parse_ibbi("<html><p>No announcements</p></html>"), [])

    def test_search_stores_rows_without_network(self):
        with mock.patch("requests.get", return_value=fake_response(200, IBBI_HTML)) as get:
            self.assertEqual(risk.ibbi_search("Acme Widgets Pvt. Ltd."), 2)
            self.assertEqual(get.call_args.kwargs["params"], {"title": "acme widgets"})
            self.assertEqual(risk.ibbi_search("Acme Widgets Pvt. Ltd."), 0)  # already stored
        with mock.patch("requests.get") as get:
            self.assertEqual(risk.ibbi_search("Co Ltd"), 0)  # too short once suffixes go
            get.assert_not_called()
        with mock.patch("requests.get", return_value=fake_response(503)), _util.quiet():
            self.assertEqual(risk.ibbi_search("Beta Industries"), 0)
        with mock.patch("requests.get", side_effect=OSError("timeout")), _util.quiet():
            self.assertEqual(risk.ibbi_search("Beta Industries"), 0)
        conn = _util.connect()
        norms = sorted(r["norm_name"] for r in conn.execute("SELECT norm_name FROM ibbi_announcements"))
        conn.close()
        self.assertEqual(norms, ["acme widgets", "acme widgets exports"])

    def test_scan_raises_ibbi_signals(self):
        with mock.patch("requests.get", return_value=fake_response(200, IBBI_HTML)):
            risk.ibbi_search("Acme Widgets")
        exact = risk.add_counterparty(self.user, {"name": "Acme Widgets Private Limited"}, scan=False)
        broad = risk.add_counterparty(self.user, {"name": "Widgets Exports"}, scan=False)
        with _util.quiet():
            res = risk.scan_counterparty(exact)
        sigs = risk.list_signals(self.user, exact)
        by_title = {s["title"]: s for s in sigs}
        self.assertEqual(res["new"], len(sigs))
        # The debtor with the same name is exact (critical); the longer name is a partial match (high).
        self.assertEqual(by_title["IBBI: insolvency (CIRP) announced"]["severity"], "critical")
        self.assertEqual(by_title["IBBI: liquidation announced (name match, verify)"]["severity"], "high")
        self.assertEqual(by_title["IBBI: insolvency (CIRP) announced"]["source_url"],
                         "https://ibbi.gov.in/uploads/announcement/acme.pdf")
        with _util.quiet():
            again = risk.scan_counterparty(exact)
        self.assertEqual(again["new"], 0)
        self.assertEqual(len(risk.list_signals(self.user, exact)), len(sigs))
        # "Widgets Exports" is a whole-word run inside "Acme Widgets Exports", so partial.
        with _util.quiet():
            risk.scan_counterparty(broad)
        self.assertEqual([s["severity"] for s in risk.list_signals(self.user, broad)], ["high"])


def sig(sid, key, severity, days_old, status="open", today=date(2026, 9, 29)):
    return {"id": sid, "signal_key": key, "severity": severity, "status": status,
            "event_date": (today - timedelta(days=days_old)).isoformat(), "title": key, "source": ""}


class Scoring(unittest.TestCase):
    T = date(2026, 9, 29)

    def score(self, *rows):
        return risk._score_rows(list(rows), self.T)[0]

    def test_decay(self):
        cases = {0: 1.0, 30: 1.0, 31: 0.7, 90: 0.7, 91: 0.45, 180: 0.45, 181: 0.25, 365: 0.25, 366: 0, -1: 0}
        for age, factor in cases.items():
            with self.subTest(age=age):
                self.assertEqual(risk._decay((self.T - timedelta(days=age)).isoformat(), self.T), factor)
        self.assertEqual(risk._decay("garbage", self.T), 0)

    def test_bands(self):
        for score, band in {100: "Severe", 60: "Severe", 59: "High", 35: "High", 34: "Moderate",
                            15: "Moderate", 14: "Low", 0: "Low"}.items():
            with self.subTest(score=score):
                self.assertEqual(risk.band_for(score), band)

    def test_weights_and_decay(self):
        # An open critical signal inside 90 days is floored at Severe (60).
        self.assertEqual(self.score(sig(1, "default", "critical", 0)), 60)
        self.assertEqual(self.score(sig(1, "default", "critical", 31)), 60)
        self.assertEqual(self.score(sig(1, "default", "critical", 100)), 18)     # past 90 days: weight x 0.45
        self.assertEqual(self.score(sig(1, "pledge", "high", 100)), 9)
        self.assertEqual(self.score(sig(1, "auditor_change", "low", 200)), 0)   # 0.5 rounds down
        self.assertEqual(self.score(sig(1, "default", "critical", 400)), 0)
        self.assertEqual(self.score(sig(1, "default", "critical", -3)), 0)      # future-dated

    def test_repeats_count_for_less(self):
        three = [sig(i, "tax_order", "high", 0) for i in range(3)]
        self.assertEqual(self.score(*three), 35)                        # 20 + 10 + 5
        self.assertEqual(self.score(*three, sig(9, "tax_order", "high", 0)), 40)
        # The strongest repeat counts in full, whatever order the rows come in.
        self.assertEqual(self.score(sig(1, "pledge", "high", 60), sig(2, "pledge", "high", 0)), 27)

    def test_critical_floor_needs_an_open_recent_signal(self):
        self.assertEqual(risk.band_for(self.score(sig(1, "insolvency", "critical", 5))), "Severe")
        self.assertEqual(self.score(sig(1, "insolvency", "critical", 5, status="resolved")), 20)
        self.assertEqual(self.score(sig(1, "insolvency", "critical", 5, status="acknowledged")), 60)

    def test_manual_signals_are_distinct_issues(self):
        self.assertEqual(self.score(sig(1, "manual", "high", 0), sig(2, "manual", "high", 0)), 40)

    def test_resolved_halves_and_cap(self):
        self.assertEqual(self.score(sig(1, "default", "critical", 0, status="resolved")), 20)
        self.assertEqual(self.score(sig(1, "a", "critical", 0), sig(2, "b", "critical", 0), sig(3, "c", "critical", 0)), 100)

    def test_reasons_are_ranked(self):
        _, contrib = risk._score_rows([sig(1, "a", "low", 0), sig(2, "b", "critical", 0), sig(3, "c", "high", 0)], self.T)
        self.assertEqual([s["id"] for _, s in contrib], [2, 3, 1])


class CounterpartiesAndAlerts(unittest.TestCase):
    def setUp(self):
        self.user, self.other = _util.uid(), _util.uid("other")

    def add(self, name, **data):
        return risk.add_counterparty(self.user, {"name": name, **data}, scan=False)

    def manual(self, cid, severity, days_old=0, **extra):
        return risk.add_manual_signal(self.user, cid, {"severity": severity, "title": f"{severity} issue",
                                                       "event_date": (TODAY - timedelta(days=days_old)).isoformat(),
                                                       **extra})

    def test_validation_and_duplicates(self):
        for bad in ({"name": ""}, {"name": "X", "pan": "123"}, {"name": "X", "cin": "L123"},
                    {"name": "X", "symbol": "BAD SYMBOL!"}, {"name": "X", "exposure_inr": "lots"},
                    {"name": "X", "exposure_inr": -5}):
            with self.subTest(**{k: str(v) for k, v in bad.items()}):
                with self.assertRaises(ValueError):
                    risk.add_counterparty(self.user, bad, scan=False)
        cid = self.add("Acme Widgets Ltd", symbol="acme", exposure_inr="1,00,000", category="nonsense")
        cp = risk.get_counterparty(self.user, cid)
        self.assertEqual((cp["symbol"], cp["exposure_inr"], cp["category"]), ("ACME", 100000.0, "other"))
        self.assertEqual(self.add("acme widgets ltd"), cid)          # same name, any case
        self.assertEqual(self.add("Different Name", symbol="ACME"), cid)
        self.assertNotEqual(risk.add_counterparty(self.other, {"name": "Acme Widgets Ltd"}, scan=False), cid)

    def test_scoping(self):
        cid = self.add("Acme Widgets Ltd")
        sid = self.manual(cid, "high")
        o = self.other
        self.assertIsNone(risk.get_counterparty(o, cid))
        self.assertFalse(risk.update_counterparty(o, cid, {"name": "Mine now"}))
        self.assertFalse(risk.archive_counterparty(o, cid))
        self.assertIsNone(risk.add_person(o, cid, {"name": "X"}))
        self.assertFalse(risk.remove_person(o, cid, 1))
        self.assertIsNone(risk.add_manual_signal(o, cid, {"title": "x"}))
        self.assertFalse(risk.act_on_signal(o, sid, "dismissed"))
        self.assertIsNone(risk.get_signal(o, sid))
        self.assertEqual(risk.list_signals(o), [])
        self.assertEqual(risk.signal_log(o, sid), [])
        self.assertEqual(risk.bulk_act(o, [sid], "resolved"), 0)
        self.assertEqual(risk.get_signal(self.user, sid)["status"], "open")

    def test_update_and_archive(self):
        cid = self.add("Acme Widgets Ltd")
        self.assertTrue(risk.update_counterparty(self.user, cid, {"exposure_inr": "2500000", "category": "borrower",
                                                                   "pan": "aaaca1234b"}))
        cp = risk.get_counterparty(self.user, cid)
        self.assertEqual((cp["exposure_inr"], cp["category"], cp["pan"]), (2500000.0, "borrower", "AAACA1234B"))
        with self.assertRaises(ValueError):
            risk.update_counterparty(self.user, cid, {"name": " "})
        self.assertTrue(risk.archive_counterparty(self.user, cid))
        self.assertFalse(risk.archive_counterparty(self.user, cid))
        self.assertIsNone(risk.get_counterparty(self.user, cid))

    def test_manual_signal_validation(self):
        cid = self.add("Acme Widgets Ltd")
        with self.assertRaises(ValueError):
            risk.add_manual_signal(self.user, cid, {"title": " "})
        with self.assertRaises(ValueError):
            risk.add_manual_signal(self.user, cid, {"title": "Late payment", "event_date": "12/09/2026"})
        with self.assertRaises(ValueError):
            risk.add_manual_signal(self.user, cid, {"title": "Late payment",
                                                    "event_date": (TODAY + timedelta(days=1)).isoformat()})
        with self.assertRaises(ValueError):
            risk.add_manual_signal(self.user, cid, {"title": "Late payment", "url": "javascript:alert(1)"})
        sid = risk.add_manual_signal(self.user, cid, {"title": "Late payment", "severity": "extreme"})
        s = risk.get_signal(self.user, sid)
        self.assertEqual((s["severity"], s["source"], s["event_date"]), ("medium", "Logged by risk team", TODAY.isoformat()))
        self.assertEqual([l["action"] for l in risk.signal_log(self.user, sid)], ["logged"])

    def test_actions_and_score(self):
        cid = self.add("Acme Widgets Ltd")
        crit = self.manual(cid, "critical")
        self.manual(cid, "high")
        self.assertEqual(risk.score_counterparty(cid)["score"], 60)
        with self.assertRaises(ValueError):
            risk.act_on_signal(self.user, crit, "escalate")
        self.assertEqual(risk.act_on_signal(self.user, crit, "resolved", note="Paid"), cid)
        self.assertEqual(risk.score_counterparty(cid)["score"], 40)   # 20 (halved) + 20
        risk.act_on_signal(self.user, crit, "dismissed")
        sc = risk.score_counterparty(cid)
        self.assertEqual((sc["score"], sc["band"]), (20, "Moderate"))
        self.assertEqual([r["id"] for r in sc["reasons"]], [s["id"] for s in risk.list_signals(self.user, cid, status="active")])
        risk.act_on_signal(self.user, crit, "assign", assignee="Credit team")
        risk.act_on_signal(self.user, crit, "open")
        log = [l["action"] for l in risk.signal_log(self.user, crit)]
        self.assertEqual(log, ["logged", "resolved", "dismissed", "assigned to Credit team", "reopened"])
        self.assertEqual(risk.get_signal(self.user, crit)["assignee"], "Credit team")
        with self.assertRaises(ValueError):
            risk.bulk_act(self.user, [crit], "assign")

    def test_list_signals_filters(self):
        a = self.add("Acme Widgets Ltd")
        b = self.add("Beta Industries Ltd")
        s1 = self.manual(a, "critical", 2, detail="Missed EMI")
        s2 = self.manual(b, "low", 1)
        s3 = self.manual(b, "high", 0)
        risk.act_on_signal(self.user, s2, "resolved")
        ids = lambda **kw: [s["id"] for s in risk.list_signals(self.user, **kw)]
        self.assertEqual(ids(), [s3, s2, s1])                 # newest event first
        self.assertEqual(ids(cid=b), [s3, s2])
        self.assertEqual(ids(status="active"), [s3, s1])
        self.assertEqual(ids(status="resolved"), [s2])
        self.assertEqual(ids(severity="critical"), [s1])
        self.assertEqual(ids(source="manual"), [s3, s2, s1])
        self.assertEqual(ids(source="ibbi"), [])
        self.assertEqual(ids(q="EMI"), [s1])
        self.assertEqual(ids(q="Beta"), [s3, s2])
        self.assertEqual(ids(limit=1), [s3])
        self.assertEqual(risk.bulk_act(self.user, [s1, s3, 99_999_999], "acknowledged"), 2)
        self.assertEqual(ids(status="acknowledged"), [s3, s1])


class Portfolio(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()
        add = lambda name, exp, cat: risk.add_counterparty(self.user, {"name": name, "exposure_inr": exp,
                                                                        "category": cat}, scan=False)
        self.a = add("Acme Steel Ltd", 10_000_000, "borrower")
        self.b = add("Beta Logistics Ltd", 5_000_000, "vendor")
        self.c = add("Gamma Textiles Ltd", 2_000_000, "borrower")
        self.d = add("Delta Retail Ltd", 1_000_000, "customer")
        m = lambda cid, sev: risk.add_manual_signal(self.user, cid, {"severity": sev, "title": f"{sev} issue"})
        self.a_crit = m(self.a, "critical")
        m(self.a, "high")          # A: 60 Severe
        m(self.b, "high")
        m(self.b, "high")          # B: 40 High
        m(self.d, "high")          # D: 20 Moderate; C: 0 Low

    def test_aggregates(self):
        p = risk.portfolio(self.user)
        self.assertEqual(p["count"], 4)
        self.assertEqual([(r["id"], r["score"], r["band"]) for r in p["rows"]],
                         [(self.a, 60, "Severe"), (self.b, 40, "High"), (self.d, 20, "Moderate"), (self.c, 0, "Low")])
        self.assertEqual(p["total_exposure"], 18_000_000)
        self.assertEqual(p["exposure_at_risk"], 15_000_000)
        self.assertEqual(p["by_band"], {"Severe": 1, "High": 1, "Moderate": 1, "Low": 1})
        self.assertEqual(p["exposure_by_band"], {"Severe": 10_000_000, "High": 5_000_000, "Moderate": 1_000_000,
                                                 "Low": 2_000_000})
        self.assertEqual(p["by_category"], {
            "borrower": {"count": 2, "exposure": 12_000_000, "at_risk": 10_000_000},
            "vendor": {"count": 1, "exposure": 5_000_000, "at_risk": 5_000_000},
            "customer": {"count": 1, "exposure": 1_000_000, "at_risk": 0},
        })
        self.assertEqual(p["active_alerts"], 5)
        self.assertEqual(p["alerts_by_severity"], {"critical": 1, "high": 4, "medium": 0, "low": 0})
        a = p["rows"][0]
        self.assertEqual((a["active_alerts"], a["critical_alerts"], a["change_30d"]), (2, 1, 60))

    def test_dismissal_moves_the_totals(self):
        risk.act_on_signal(self.user, self.a_crit, "dismissed")
        p = risk.portfolio(self.user)
        self.assertEqual(p["rows"][0]["id"], self.b)
        self.assertEqual(p["exposure_at_risk"], 5_000_000)
        self.assertEqual(p["active_alerts"], 4)

    def test_other_user_portfolio_is_empty(self):
        p = risk.portfolio(_util.uid("other"))
        self.assertEqual((p["count"], p["total_exposure"], p["active_alerts"]), (0, 0, 0))


class DirectorNetwork(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()
        add = lambda name: risk.add_counterparty(self.user, {"name": name}, scan=False)
        self.a, self.b, self.c = add("Acme Steel Ltd"), add("Beta Logistics Ltd"), add("Gamma Textiles Ltd")
        risk.add_person(self.user, self.a, {"name": "Rohan Mehta", "din": "01234567", "designation": "Director"})
        risk.add_person(self.user, self.b, {"name": "R. Mehta", "din": "0123 4567"})
        risk.add_person(self.user, self.c, {"name": "Only Here", "din": "09999999"})
        risk.add_person(self.user, self.c, {"name": "No DIN on file"})
        self.eid = _util.make_entity(self.user, name="Own Holdings Private Limited", tracking_from="2099-01-01")
        # Entered without the leading zero; the network pads DINs to 8 digits.
        ents.add_person(self.user, self.eid, {"name": "Rohan Mehta", "din": "1234567"})

    def test_person_validation(self):
        with self.assertRaises(ValueError):
            risk.add_person(self.user, self.a, {"name": "X", "din": "12345"})
        with self.assertRaises(ValueError):
            risk.add_person(self.user, self.a, {"name": ""})

    def test_overlaps_across_counterparties_and_own_entities(self):
        net = risk.director_network(self.user)
        self.assertEqual(net["org_count"], 4)
        self.assertEqual(net["director_count"], 2)
        [ov] = net["overlaps"]
        self.assertEqual(ov["din"], "01234567")
        self.assertEqual(ov["name"], "Rohan Mehta")
        self.assertEqual(sorted(o["id"] for o in ov["orgs"]), sorted([f"cp-{self.a}", f"cp-{self.b}", f"en-{self.eid}"]))
        kinds = {o["id"]: o["kind"] for o in ov["orgs"]}
        self.assertEqual(kinds[f"en-{self.eid}"], "entity")
        self.assertEqual(len(net["edges"]), 3)
        self.assertEqual(len(net["nodes"]), 3)
        self.assertNotIn(f"cp-{self.c}", {n["id"] for n in net["nodes"]})

    def test_board_rows_from_filings_count_too(self):
        conn = _util.connect()
        conn.execute("INSERT INTO cp_people (counterparty_id, name, din, designation, source, added_at) "
                     "VALUES (?, 'Rohan Mehta', '01234567', 'Independent Director', 'governance', '2026-09-01')", (self.c,))
        conn.commit()
        conn.close()
        [ov] = risk.director_network(self.user)["overlaps"]
        self.assertEqual(len(ov["orgs"]), 4)
        # Board rows from filings cannot be removed by hand.
        gov_id = [p["id"] for p in risk.list_people(self.c) if p["source"] == "governance"][0]
        self.assertFalse(risk.remove_person(self.user, self.c, gov_id))

    def test_other_users_boards_are_not_linked(self):
        other = _util.uid("other")
        ocid = risk.add_counterparty(other, {"name": "Acme Steel Ltd"}, scan=False)
        risk.add_person(other, ocid, {"name": "Rohan Mehta", "din": "01234567"})
        net = risk.director_network(self.user)
        self.assertEqual(net["org_count"], 4)
        self.assertNotIn(f"cp-{ocid}", {o["id"] for o in net["overlaps"][0]["orgs"]})
        self.assertEqual(risk.director_network(other)["overlaps"], [])


if __name__ == "__main__":
    unittest.main()
