"""rulebook.py: applicability, due-date schedules, event follow-ups,
DIR-3 KYC cycles and penalty estimates. Pure functions, no database."""
from tests import _util  # noqa: F401  (must come first)

import unittest
from datetime import date

import rulebook as rb


def occ(rule_id, start, end):
    """{period: due_date} for one rule inside a window."""
    return {o["period"]: o["due_date"] for o in rb.occurrences(rb.RULES_BY_ID[rule_id], start, end)}


PRIVATE = {"entity_type": "private", "flags": {}, "state": ""}
LISTED = {"entity_type": "listed", "flags": {"secretarial_audit": True}, "state": ""}


class CatalogueIntegrity(unittest.TestCase):
    """Data mistakes in RULES would silently drop obligations from calendars."""

    def test_rule_ids_unique(self):
        ids = [r["id"] for r in rb.RULES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_flags_types_and_triggers_are_known(self):
        for r in rb.RULES:
            with self.subTest(rule=r["id"]):
                self.assertEqual(r["jurisdiction"], "IN")
                self.assertTrue(set(r["types"]) <= set(rb.ALL_TYPES))
                for f in r["flags"] + r["any_flags"] + r["not_flags"]:
                    self.assertIn(f, rb.FLAGS)
                if r["schedule"]["kind"] == "event":
                    for t in r["schedule"]["triggers"]:
                        self.assertIn(t, rb.EVENT_TYPES)
                self.assertIn(r["regime"], rb.REGIMES)

    def test_every_periodic_rule_has_dates_and_a_summary(self):
        for r in rb.RULES:
            with self.subTest(rule=r["id"]):
                s = rb.rule_summary(r)
                self.assertTrue(s["schedule"])
                self.assertNotEqual(s["frequency"], "other")
                if r["schedule"]["kind"] != "event":
                    self.assertTrue(rb.occurrences(r, date(2026, 1, 1), date(2027, 12, 31)))


class Applies(unittest.TestCase):
    def test_entity_type(self):
        agm = rb.RULES_BY_ID["IN-MCA-AGM"]
        self.assertTrue(rb.applies(agm, PRIVATE))
        self.assertFalse(rb.applies(agm, {"entity_type": "opc"}))
        self.assertFalse(rb.applies(agm, {"entity_type": "llp"}))
        self.assertTrue(rb.applies(rb.RULES_BY_ID["IN-LLP-F11"], {"entity_type": "llp"}))
        self.assertFalse(rb.applies(rb.RULES_BY_ID["IN-SEBI-REG33"], PRIVATE))
        self.assertTrue(rb.applies(rb.RULES_BY_ID["IN-SEBI-REG33"], LISTED))

    def test_required_flags(self):
        r1 = rb.RULES_BY_ID["IN-GST-R1"]
        self.assertFalse(rb.applies(r1, PRIVATE))
        self.assertTrue(rb.applies(r1, {"entity_type": "private", "flags": {"gst_registered": True}}))
        # Falsey flag values count as off.
        self.assertFalse(rb.applies(r1, {"entity_type": "private", "flags": {"gst_registered": False}}))
        form16 = rb.RULES_BY_ID["IN-IT-FORM16"]  # needs both flags
        self.assertFalse(rb.applies(form16, {"entity_type": "private", "flags": {"tds_deductor": True}}))
        self.assertTrue(rb.applies(form16, {"entity_type": "private",
                                            "flags": {"tds_deductor": True, "has_employees": True}}))

    def test_not_flags(self):
        monthly = rb.RULES_BY_ID["IN-GST-R1"]
        qrmp = rb.RULES_BY_ID["IN-GST-R1-Q"]
        on_qrmp = {"entity_type": "private", "flags": {"gst_registered": True, "gst_qrmp": True}}
        self.assertFalse(rb.applies(monthly, on_qrmp))
        self.assertTrue(rb.applies(qrmp, on_qrmp))
        # ITR due date moves when transfer pricing applies.
        tp = {"entity_type": "private", "flags": {"transfer_pricing": True}}
        self.assertFalse(rb.applies(rb.RULES_BY_ID["IN-IT-ITR"], tp))
        self.assertTrue(rb.applies(rb.RULES_BY_ID["IN-IT-ITR-TP"], tp))
        self.assertTrue(rb.applies(rb.RULES_BY_ID["IN-IT-ITR"], PRIVATE))

    def test_any_flags(self):
        fla = rb.RULES_BY_ID["IN-FEMA-FLA"]
        self.assertFalse(rb.applies(fla, PRIVATE))
        self.assertTrue(rb.applies(fla, {"entity_type": "private", "flags": {"odi": True}}))
        self.assertTrue(rb.applies(fla, {"entity_type": "private", "flags": {"fdi": True}}))

    def test_states(self):
        pt = rb.RULES_BY_ID["IN-LAB-PT-MH"]
        emp = {"has_employees": True}
        self.assertTrue(rb.applies(pt, {"entity_type": "private", "flags": emp, "state": "Maharashtra"}))
        self.assertFalse(rb.applies(pt, {"entity_type": "private", "flags": emp, "state": "Karnataka"}))
        self.assertFalse(rb.applies(pt, {"entity_type": "private", "flags": emp}))
        self.assertFalse(rb.applies(pt, {"entity_type": "private", "flags": {}, "state": "Maharashtra"}))

    def test_other_jurisdiction(self):
        self.assertFalse(rb.applies(rb.RULES_BY_ID["IN-MCA-AGM"], {**PRIVATE, "jurisdiction": "US"}))

    def test_applicable_rules_can_skip_events(self):
        with_events = rb.applicable_rules(LISTED)
        periodic = rb.applicable_rules(LISTED, include_events=False)
        self.assertTrue(any(r["schedule"]["kind"] == "event" for r in with_events))
        self.assertFalse(any(r["schedule"]["kind"] == "event" for r in periodic))
        self.assertIn("IN-SEBI-REG33", {r["id"] for r in periodic})


class Occurrences(unittest.TestCase):
    def test_monthly_tds_march_exception(self):
        got = occ("IN-IT-TDSPAY", date(2026, 3, 1), date(2026, 5, 31))
        self.assertEqual(got, {"Feb 2026": date(2026, 3, 7), "Mar 2026": date(2026, 4, 30),
                               "Apr 2026": date(2026, 5, 7)})

    def test_window_bounds_are_inclusive(self):
        self.assertEqual(occ("IN-IT-TDSPAY", date(2026, 3, 7), date(2026, 3, 7)),
                         {"Feb 2026": date(2026, 3, 7)})
        self.assertEqual(occ("IN-IT-TDSPAY", date(2026, 3, 8), date(2026, 4, 29)), {})

    def test_monthly_last_day_same_month(self):
        rule = {**rb.RULES_BY_ID["IN-LAB-PT-MH"], "schedule": {"kind": "monthly", "day": "last", "offset": 0}}
        got = {o["period"]: o["due_date"] for o in rb.occurrences(rule, date(2028, 2, 1), date(2028, 4, 30))}
        self.assertEqual(got, {"Feb 2028": date(2028, 2, 29), "Mar 2028": date(2028, 3, 31),
                               "Apr 2028": date(2028, 4, 30)})

    def test_maharashtra_pt_is_the_15th_of_the_same_month(self):
        self.assertEqual(occ("IN-LAB-PT-MH", date(2027, 2, 1), date(2027, 3, 31)),
                         {"Feb 2027": date(2027, 2, 15), "Mar 2027": date(2027, 3, 15)})

    def test_monthly_next_month_and_year_rollover(self):
        got = occ("IN-GST-R1", date(2026, 12, 1), date(2027, 1, 31))
        self.assertEqual(got, {"Nov 2026": date(2026, 12, 11), "Dec 2026": date(2027, 1, 11)})

    def test_monthly_skip_months(self):
        got = occ("IN-GST-PMT06", date(2026, 4, 1), date(2026, 7, 31))
        self.assertEqual(got, {"Apr 2026": date(2026, 5, 25), "May 2026": date(2026, 6, 25)})

    def test_quarterly_after_with_q4_days(self):
        got = occ("IN-SEBI-REG33", date(2026, 4, 1), date(2027, 6, 30))
        self.assertEqual(got, {
            "Q4 FY2025-26": date(2026, 5, 30),   # 31 Mar + 60
            "Q1 FY2026-27": date(2026, 8, 14),   # 30 Jun + 45
            "Q2 FY2026-27": date(2026, 11, 14),
            "Q3 FY2026-27": date(2027, 2, 14),
            "Q4 FY2026-27": date(2027, 5, 30),
        })

    def test_quarterly_after_same_day(self):
        got = occ("IN-MCA-BOARD", date(2026, 6, 1), date(2026, 12, 31))
        self.assertEqual(got, {"Q1 FY2026-27": date(2026, 6, 30), "Q2 FY2026-27": date(2026, 9, 30),
                               "Q3 FY2026-27": date(2026, 12, 31)})

    def test_quarterly_fixed_tds_q4_is_31_may_next_year(self):
        got = occ("IN-IT-TDSRET", date(2025, 4, 1), date(2026, 6, 30))
        self.assertEqual(got, {
            "Q4 FY2024-25": date(2025, 5, 31),
            "Q1 FY2025-26": date(2025, 7, 31),
            "Q2 FY2025-26": date(2025, 10, 31),
            "Q3 FY2025-26": date(2026, 1, 31),
            "Q4 FY2025-26": date(2026, 5, 31),
        })

    def test_quarterly_nextmonth(self):
        got = occ("IN-GST-3B-Q", date(2026, 4, 1), date(2027, 4, 30))
        self.assertEqual(got, {
            "Q4 FY2025-26": date(2026, 4, 22),
            "Q1 FY2026-27": date(2026, 7, 22),
            "Q2 FY2026-27": date(2026, 10, 22),
            "Q3 FY2026-27": date(2027, 1, 22),   # next calendar year
            "Q4 FY2026-27": date(2027, 4, 22),
        })

    def test_half_fixed(self):
        rule = rb.RULES_BY_ID["IN-SEBI-REG23"]
        (m1, d1, lab1), (m2, d2, lab2) = rule["schedule"]["dates"]
        got = occ("IN-SEBI-REG23", date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(got, {f"{lab1} 2026": date(2026, 5, 30), f"{lab2} 2026": date(2026, 11, 14)})
        msme = occ("IN-MCA-MSME1", date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(sorted(msme.values()), [date(2026, 4, 30), date(2026, 10, 31)])

    def test_annual_month_from_april(self):
        got = occ("IN-MCA-AOC4", date(2026, 1, 1), date(2027, 12, 31))
        self.assertEqual(got, {"FY2025-26": date(2026, 10, 30), "FY2026-27": date(2027, 10, 30)})

    def test_annual_month_before_april_is_two_years_on(self):
        rule = {**rb.RULES_BY_ID["IN-MCA-CSR2"], "schedule": {"kind": "annual", "month": 3, "day": 31}}
        got = {o["period"]: o["due_date"] for o in rb.occurrences(rule, date(2027, 1, 1), date(2027, 12, 31))}
        self.assertEqual(got, {"FY2025-26": date(2027, 3, 31)})

    def test_csr2_goes_with_aoc4(self):
        self.assertEqual(occ("IN-MCA-CSR2", date(2026, 1, 1), date(2026, 12, 31)), {"FY2025-26": date(2026, 10, 30)})

    def test_one_off_extension_overrides_a_single_period(self):
        got = occ("IN-IT-TAXAUDIT", date(2026, 9, 1), date(2027, 12, 31))
        self.assertEqual(got, {"FY2025-26": date(2026, 10, 21), "FY2026-27": date(2027, 9, 30)})

    def test_annual_calendar(self):
        got = occ("IN-LAB-POSH", date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(got, {"CY2025": date(2026, 1, 31)})

    def test_fy_dates(self):
        got = occ("IN-IT-ADV", date(2026, 4, 1), date(2027, 3, 31))
        self.assertEqual(sorted(got.values()),
                         [date(2026, 6, 15), date(2026, 9, 15), date(2026, 12, 15), date(2027, 3, 15)])
        self.assertIn("Instalment 4 (100%) FY2026-27", got)
        self.assertEqual(got["Instalment 4 (100%) FY2026-27"], date(2027, 3, 15))

    def test_event_and_per_din_rules_have_no_periodic_dates(self):
        self.assertEqual(rb.occurrences(rb.RULES_BY_ID["IN-SEBI-REG29"], date(2026, 1, 1), date(2027, 1, 1)), [])
        self.assertEqual(rb.occurrences(rb.DIR3KYC_RULE, date(2026, 1, 1), date(2030, 1, 1)), [])

    def test_fy_helpers(self):
        self.assertEqual(rb.fy_start(date(2026, 3, 31)), 2025)
        self.assertEqual(rb.fy_start(date(2026, 4, 1)), 2026)
        self.assertEqual(rb.fy_label(2026), "FY2026-27")
        self.assertEqual(rb.fy_label(2099), "FY2099-00")


class GstQuarterlyByState(unittest.TestCase):
    def ent(self, state):
        return {"entity_type": "private", "state": state, "flags": {"gst_registered": True, "gst_qrmp": True}}

    def test_state_decides_22nd_or_24th(self):
        ids = lambda st: sorted(r["id"] for r in rb.applicable_rules(self.ent(st), include_events=False)
                                if r["id"].startswith("IN-GST-3B-Q"))
        self.assertEqual(ids("Maharashtra"), ["IN-GST-3B-Q"])
        self.assertEqual(ids("Delhi"), ["IN-GST-3B-Q-24"])
        self.assertEqual(ids(""), ["IN-GST-3B-Q"])  # state not set: the earlier date
        self.assertEqual(occ("IN-GST-3B-Q-24", date(2026, 10, 1), date(2026, 10, 31)), {"Q2 FY2026-27": date(2026, 10, 24)})

    def test_every_state_has_exactly_one_rule(self):
        import routes_workspace as rw
        for st in rw.INDIAN_STATES:
            n = sum(1 for r in rb.applicable_rules(self.ent(st), include_events=False) if r["id"].startswith("IN-GST-3B-Q"))
            self.assertEqual(n, 1, st)


class EventObligations(unittest.TestCase):
    def test_reg44_counts_working_days(self):
        # A Friday meeting's voting results are due the next Tuesday.
        got = {o["rule_id"]: o["due_date"] for o in rb.event_obligations("general_meeting_held", date(2026, 10, 2), LISTED)}
        self.assertEqual(got["IN-SEBI-REG44"], date(2026, 10, 6))
        self.assertEqual(got["IN-SEBI-REG30"], date(2026, 10, 3))

    def test_reg29_is_before_the_meeting(self):
        # Two clear working days (Fri 16, Mon 19) before a Tuesday meeting: Thursday 15.
        got = rb.event_obligations("board_meeting_scheduled", date(2026, 10, 20), LISTED)
        self.assertEqual([(o["rule_id"], o["due_date"]) for o in got], [("IN-SEBI-REG29", date(2026, 10, 15))])
        self.assertEqual(got[0]["period"], "Board meeting scheduled · 2026-10-20")

    def test_listed_only_rules_skip_private(self):
        self.assertEqual(rb.event_obligations("board_meeting_scheduled", date(2026, 10, 20), PRIVATE), [])

    def test_director_appointed(self):
        priv = {o["rule_id"]: o["due_date"] for o in
                rb.event_obligations("director_appointed", date(2026, 10, 1), PRIVATE)}
        self.assertEqual(priv, {"IN-MCA-DIR12": date(2026, 10, 31)})
        listed = {o["rule_id"]: o["due_date"] for o in
                  rb.event_obligations("director_appointed", date(2026, 10, 1), LISTED)}
        self.assertEqual(listed, {"IN-MCA-DIR12": date(2026, 10, 31), "IN-SEBI-REG30": date(2026, 10, 2)})

    def test_flags_apply_to_events(self):
        base = {o["rule_id"] for o in rb.event_obligations("shares_allotted", date(2026, 10, 1), PRIVATE)}
        self.assertEqual(base, {"IN-MCA-PAS3"})
        fdi = {"entity_type": "private", "flags": {"fdi": True}}
        got = {o["rule_id"]: o["due_date"] for o in rb.event_obligations("shares_allotted", date(2026, 10, 1), fdi)}
        self.assertEqual(got, {"IN-MCA-PAS3": date(2026, 10, 16), "IN-FEMA-FCGPR": date(2026, 10, 31)})

    def test_company_only_event_on_llp(self):
        self.assertEqual(rb.event_obligations("incorporated", date(2026, 1, 1), {"entity_type": "llp"}), [])


class Dir3Kyc(unittest.TestCase):
    def test_dins_before_april_2025_anchor_to_2028(self):
        as_of = date(2026, 9, 29)
        for allotted in (None, date(2010, 5, 1), date(2025, 3, 31)):
            with self.subTest(allotted=allotted):
                self.assertEqual(rb.dir3kyc_due(allotted, as_of), date(2028, 6, 30))

    def test_later_dins_anchor_to_fy_plus_four(self):
        as_of = date(2026, 9, 29)
        self.assertEqual(rb.dir3kyc_due(date(2025, 4, 1), as_of), date(2029, 6, 30))
        self.assertEqual(rb.dir3kyc_due(date(2026, 3, 31), as_of), date(2029, 6, 30))
        self.assertEqual(rb.dir3kyc_due(date(2026, 4, 1), as_of), date(2030, 6, 30))

    def test_missed_deadline_stays_visible_for_a_year_then_rolls(self):
        self.assertEqual(rb.dir3kyc_due(None, date(2029, 6, 1)), date(2028, 6, 30))
        self.assertEqual(rb.dir3kyc_due(None, date(2029, 6, 30)), date(2028, 6, 30))
        self.assertEqual(rb.dir3kyc_due(None, date(2029, 7, 1)), date(2031, 6, 30))

    def test_cycles(self):
        self.assertEqual(rb.dir3kyc_cycles(None, date(2026, 1, 1), date(2035, 12, 31)),
                         [date(2028, 6, 30), date(2031, 6, 30), date(2034, 6, 30)])
        self.assertEqual(rb.dir3kyc_cycles(date(2025, 4, 1), date(2029, 1, 1), date(2033, 12, 31)),
                         [date(2029, 6, 30), date(2032, 6, 30)])
        self.assertEqual(rb.dir3kyc_cycles(None, date(2026, 1, 1), date(2028, 6, 29)), [])


class Penalties(unittest.TestCase):
    def test_not_late(self):
        rule = rb.RULES_BY_ID["IN-MCA-AOC4"]
        self.assertIsNone(rb.estimate_penalty(rule, date(2026, 10, 30), date(2026, 10, 30)))
        self.assertIsNone(rb.estimate_penalty(rule, date(2026, 10, 30), date(2026, 10, 1)))
        # Filed on time, looked at much later.
        self.assertIsNone(rb.estimate_penalty(rule, date(2026, 10, 30), date(2027, 3, 1), filed_on=date(2026, 10, 29)))

    def test_per_day(self):
        got = rb.estimate_penalty(rb.RULES_BY_ID["IN-MCA-AOC4"], date(2026, 10, 30), date(2026, 12, 14))
        self.assertEqual((got["days_late"], got["amount"]), (45, 4500))
        self.assertIn("₹100/day × 45 days", got["basis"])

    def test_per_day_cap(self):
        rule = rb.RULES_BY_ID["IN-GST-R1"]
        under = rb.estimate_penalty(rule, date(2026, 1, 11), date(2026, 1, 21))
        self.assertEqual(under["amount"], 500)
        self.assertNotIn("capped", under["basis"])
        over = rb.estimate_penalty(rule, date(2026, 1, 11), date(2026, 11, 7))
        self.assertEqual(over["days_late"], 300)
        self.assertEqual(over["amount"], 10000)
        self.assertIn("capped at ₹10,000", over["basis"])

    def test_filed_late_uses_filing_date(self):
        got = rb.estimate_penalty(rb.RULES_BY_ID["IN-MCA-AOC4"], date(2026, 10, 30), date(2027, 6, 1),
                                  filed_on=date(2026, 11, 4))
        self.assertEqual((got["days_late"], got["amount"]), (5, 500))

    def test_flat(self):
        got = rb.estimate_penalty(rb.RULES_BY_ID["IN-MCA-INC20A"], date(2026, 1, 1), date(2026, 1, 3))
        self.assertEqual((got["days_late"], got["amount"]), (2, 50000))
        self.assertIn("flat", got["basis"])

    def test_slab_fee_multiples(self):
        rule = rb.RULES_BY_ID["IN-MCA-DIR12"]
        due = date(2026, 1, 1)
        # G.S.R. 12(E): forms other than s.139/157 start at 2x.
        expected = {1: 2, 15: 2, 16: 2, 30: 2, 31: 4, 60: 4, 61: 6, 90: 6, 91: 10, 180: 10, 181: 12, 400: 12}
        for days, mult in expected.items():
            with self.subTest(days=days):
                got = rb.estimate_penalty(rule, due, date.fromordinal(due.toordinal() + days))
                self.assertEqual(got["days_late"], days)
                self.assertEqual(got["amount"], 200 * mult)  # no share capital -> ₹200 normal fee
                self.assertIn(f"{mult}×", got["basis"])

    def test_adt1_keeps_the_1x_band_for_15_days(self):
        rule = rb.RULES_BY_ID["IN-MCA-ADT1"]
        due = date(2026, 1, 1)
        for days, mult in {15: 1, 16: 2, 31: 4}.items():
            got = rb.estimate_penalty(rule, due, date.fromordinal(due.toordinal() + days))
            self.assertEqual(got["amount"], 200 * mult)

    def test_llp_slab(self):
        rule = rb.RULES_BY_ID["IN-LLP-F11"]
        due = date(2025, 5, 30)
        small = {"share_capital": 300000, "flags": {"small_llp": True}}
        other = {"share_capital": 300000, "flags": {}}
        got = rb.estimate_penalty(rule, due, date(2025, 6, 20), small)   # 21 days: 2x of ₹100
        self.assertEqual(got["amount"], 200)
        got = rb.estimate_penalty(rule, due, date(2025, 6, 20), other)   # 4x
        self.assertEqual(got["amount"], 400)
        got = rb.estimate_penalty(rule, due, date.fromordinal(due.toordinal() + 400), other)
        self.assertEqual(got["amount"], 30 * 100 + 20 * 40)

    def test_slab_fee_uses_share_capital(self):
        rule = rb.RULES_BY_ID["IN-MCA-DIR12"]
        got = rb.estimate_penalty(rule, date(2026, 1, 1), date(2026, 2, 1), {"share_capital": 1_000_000})
        self.assertEqual(got["amount"], 400 * 4)  # 31 days late, ₹5-25 lakh capital

    def test_text_penalty_has_no_amount(self):
        rule = rb.RULES_BY_ID["IN-IT-TDSPAY"]
        got = rb.estimate_penalty(rule, date(2026, 1, 7), date(2026, 2, 7))
        self.assertIsNone(got["amount"])
        self.assertEqual(got["basis"], rule["penalty"]["text"])

    def test_normal_fee_bands(self):
        cases = {None: 200, 0: 200, 99_999: 200, 100_000: 300, 499_999: 300, 500_000: 400,
                 2_499_999: 400, 2_500_000: 500, 9_999_999: 500, 10_000_000: 600, 10**10: 600}
        for capital, fee in cases.items():
            with self.subTest(capital=capital):
                self.assertEqual(rb.normal_fee(capital), fee)

    def test_slab_multiple_not_late(self):
        self.assertEqual(rb.slab_multiple(0), 0)
        self.assertEqual(rb.slab_multiple(-3), 0)


class PlainWords(unittest.TestCase):
    def test_schedule_text(self):
        t = lambda rid: rb.schedule_text(rb.RULES_BY_ID[rid])
        self.assertEqual(t("IN-IT-TDSPAY"), "Monthly, by the 7th of the next month (30 April for March)")
        self.assertEqual(t("IN-LAB-PT-MH"), "Monthly, by the 15th of the same month")
        self.assertEqual(t("IN-GST-PMT06"), "Monthly, by the 25th of the next month (first two months of each quarter)")
        self.assertEqual(t("IN-SEBI-REG33"), "Quarterly, 45 days after quarter end (60 for Q4)")
        self.assertEqual(t("IN-MCA-BOARD"), "Quarterly, by quarter end")
        self.assertEqual(t("IN-SEBI-REG29"), "At least 2 clear working days before: Board meeting scheduled")
        self.assertEqual(t("IN-SEBI-REG30-BM"), "Same day as: Board meeting held")
        self.assertTrue(t("IN-SEBI-REG30").startswith("Within 24 hours of: "))
        self.assertEqual(t("IN-GST-R1-Q"), "Quarterly, by the 13th of the month after quarter end")
        self.assertEqual(rb.schedule_text(rb.DIR3KYC_RULE), "Every 3 years per DIN, by 30 June")

    def test_frequency(self):
        f = lambda rid: rb.frequency(rb.RULES_BY_ID[rid])
        self.assertEqual(f("IN-GST-R1"), "monthly")
        self.assertEqual(f("IN-IT-ADV"), "quarterly")
        self.assertEqual(f("IN-MCA-MSME1"), "half-yearly")
        self.assertEqual(f("IN-LAB-POSH"), "annual")
        self.assertEqual(f("IN-MCA-DIR12"), "event")
        self.assertEqual(rb.frequency(rb.DIR3KYC_RULE), "per-director")


if __name__ == "__main__":
    unittest.main()
