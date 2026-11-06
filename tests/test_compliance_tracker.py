"""The listed-company tracker: what counts as proof that a filing was made,
and deadlines that match the rulebook."""
from tests import _util  # noqa: F401  (must come first)

import unittest
from datetime import date
from unittest import mock

import compliance as c
import compliance_verification as cv
import rulebook as rb


def _no_source(*a, **k):
    return False, None, []


class WhatCountsAsProof(unittest.TestCase):
    def verify(self, **sources):
        names = {"integrated": "_verify_integrated_filing", "cg": "_verify_cg_master", "shp": "_verify_shp_master",
                 "bse": "_verify_bse_announcements", "nse": "_verify_nse_announcements_ranged"}
        patches = [mock.patch.object(cv, fn, sources.get(k, _no_source)) for k, fn in names.items()]
        patches.append(mock.patch.object(cv.time, "sleep", lambda s: None))
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return cv.verify_obligation("TESTCO", "LODR-REG31", date(2026, 6, 30), date(2026, 7, 21))

    def test_a_keyword_hit_in_announcements_is_not_proof(self):
        hit = lambda *a, **k: (True, {"filed_date": date(2026, 7, 10), "title": "Disclosure under SEBI Takeover Regulations",
                                      "url": "https://example.org"}, [{}])
        r = self.verify(bse=hit, nse=hit)
        self.assertEqual((r["status"], r["confidence"]), ("unverified", "low"))

    def test_the_authoritative_feed_is(self):
        shp = lambda *a, **k: (True, {"filed_date": date(2026, 7, 25), "title": "Shareholding Pattern", "url": ""}, [{}])
        r = self.verify(shp=shp)
        self.assertEqual((r["status"], r["filed_date"], r["delay_days"]), ("delayed", "2026-07-25", 4))


class LinkingAnnouncements(unittest.TestCase):
    SYM = "TRACKCO"

    def setUp(self):
        with _util.quiet():
            c.init_compliance_db()
        conn = _util.connect()
        conn.execute("DELETE FROM filings WHERE company = ?", (self.SYM,))
        conn.execute("DELETE FROM compliance_obligations WHERE symbol = ?", (self.SYM,))
        conn.execute("""INSERT INTO compliance_obligations (symbol, mandate_id, period, due_date, status, created_at, updated_at)
                        VALUES (?, 'LODR-REG33', 'Q1 FY2026-27', '2026-08-14', 'pending', 'x', 'x')""", (self.SYM,))
        conn.commit(); conn.close()
        p = mock.patch.object(c, "verify_obligation", lambda *a, **k: {
            "status": "unverified", "filed_date": None, "delay_days": 0, "evidence": [], "confidence": "low"})
        p.start(); self.addCleanup(p.stop)

    def add(self, title, day):
        conn = _util.connect()
        conn.execute("INSERT INTO filings (company, title, description, date, sort_date, pdf_link, first_seen) "
                     "VALUES (?,?,?,?,?,?,?)", (self.SYM, title, "", day, day, "https://example.org/x.pdf", day))
        conn.commit(); conn.close()

    def row(self):
        conn = _util.connect()
        r = dict(conn.execute("SELECT * FROM compliance_obligations WHERE symbol = ?", (self.SYM,)).fetchone())
        conn.close()
        return r

    def test_unrelated_announcements_do_not_close_results(self):
        for t in ("General Updates", "Copy of Newspaper Publication", "Outcome of Board Meeting", "Updates"):
            self.add(t, "2026-08-05")
        with _util.quiet():
            c.link_filings_to_obligations(self.SYM)
        self.assertEqual(self.row()["status"], "unverified")
        self.assertIsNone(self.row()["filing_id"])

    def test_the_results_category_does(self):
        self.add("General Updates", "2026-07-20")
        self.add("Financial Result Updates", "2026-08-05")
        with _util.quiet():
            c.link_filings_to_obligations(self.SYM)
        r = self.row()
        self.assertEqual((r["status"], r["filed_date"]), ("submitted", "2026-08-05"))


class DeadlinesMatchTheRulebook(unittest.TestCase):
    def due(self, mandate_id, period):
        return c._due_for_period(c.get_mandate_by_id(mandate_id), period)

    def test_quarterly_and_half_yearly_dates(self):
        cases = {("LODR-REG13", "Q3 FY2025-26"): date(2026, 1, 30),
                 ("LODR-REG27", "Q3 FY2025-26"): date(2026, 1, 30),
                 ("LODR-REG31", "Q3 FY2025-26"): date(2026, 1, 21),
                 ("LODR-REG74", "Q3 FY2025-26"): date(2026, 1, 15),
                 ("LODR-REG33", "Q4 FY2025-26"): date(2026, 5, 30),
                 ("LODR-REG23-RPT", "H1 FY2026-27"): date(2026, 11, 14),
                 ("LODR-REG23-RPT", "H2 FY2025-26"): date(2026, 5, 30),
                 ("LODR-REG34-AR", "FY2025-26"): date(2026, 9, 8),
                 ("MCA-CSR2", "FY2025-26"): date(2026, 10, 30),
                 ("MCA-CRA4", "FY2025-26"): date(2026, 10, 27)}
        for (mid, period), want in cases.items():
            with self.subTest(mandate=mid, period=period):
                self.assertEqual(self.due(mid, period), want)

    def test_tracker_and_rulebook_agree_on_days_after_quarter(self):
        pairs = {"LODR-REG13": "IN-SEBI-REG13", "LODR-REG27": "IN-SEBI-REG27", "LODR-REG31": "IN-SEBI-REG31",
                 "LODR-REG74": "IN-SEBI-REG74", "LODR-REG76-RSCA": "IN-SEBI-REG76"}
        for mid, rid in pairs.items():
            with self.subTest(mandate=mid):
                self.assertEqual(c.get_mandate_by_id(mid)["due_days_after_quarter"], rb.RULES_BY_ID[rid]["schedule"]["days"])

    def test_omitted_sast_disclosure_is_gone(self):
        self.assertIsNone(c.get_mandate_by_id("SAST-REG30"))

    def test_a_corrected_deadline_also_corrects_late_flags(self):
        with _util.quiet():
            c.init_compliance_db()
        conn = _util.connect()
        conn.execute("DELETE FROM compliance_obligations WHERE symbol = 'REDATECO'")
        conn.execute("""INSERT INTO compliance_obligations (symbol, mandate_id, period, due_date, status, filed_date,
                        delay_days, created_at, updated_at) VALUES ('REDATECO', 'LODR-REG13', 'Q3 FY2025-26', '2026-01-21',
                        'delayed', '2026-01-28', 7, 'x', 'x')""")
        c._redate_all(conn)
        r = conn.execute("SELECT due_date, status, delay_days FROM compliance_obligations WHERE symbol = 'REDATECO'").fetchone()
        conn.close()
        self.assertEqual(tuple(r), ("2026-01-30", "submitted", 0))


if __name__ == "__main__":
    unittest.main()
