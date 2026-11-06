"""entities.py: per-user scoping, calendar sync, people, events, tasks,
status changes and the workspace summary."""
from tests import _util  # noqa: F401  (must come first)

import unittest
from datetime import date, timedelta

import entities as ents
import rulebook as rb

TODAY = date.today()
FAR_FUTURE = "2099-01-01"  # tracking from here -> no periodic rows, so tests control every item


def rows(entity_id, **where):
    sql = "SELECT * FROM entity_obligations WHERE entity_id = ?"
    args = [entity_id]
    for k, v in where.items():
        sql += f" AND {k} = ?"
        args.append(v)
    conn = _util.connect()
    out = [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]
    conn.close()
    return out


def iso(d):
    return d.isoformat()


class EntityCrud(unittest.TestCase):
    def setUp(self):
        self.user, self.other = _util.uid(), _util.uid("other")

    def test_create_validates(self):
        with self.assertRaises(ValueError):
            ents.create_entity(self.user, {"name": "  ", "entity_type": "private"})
        with self.assertRaises(ValueError):
            ents.create_entity(self.user, {"name": "Acme", "entity_type": "partnership"})

    def test_create_cleans_fields_and_flags(self):
        eid = ents.create_entity(self.user, {
            "name": "  Acme Listed Limited ", "entity_type": "listed", "cin": " l12345mh2000plc123456 ",
            "pan": "aaaca1234b", "flags": {"gst_registered": True, "made_up_flag": True, "fdi": False},
        })
        e = ents.get_entity(self.user, eid)
        self.assertEqual(e["name"], "Acme Listed Limited")
        self.assertEqual(e["cin"], "L12345MH2000PLC123456")
        self.assertEqual(e["pan"], "AAACA1234B")
        # Unknown and false flags are dropped; listed companies always have secretarial audit.
        self.assertEqual(e["flags"], {"gst_registered": True, "secretarial_audit": True})
        self.assertEqual(e["type_label"], rb.ENTITY_TYPES["listed"])
        self.assertEqual(e["tracking_from"], iso(TODAY - timedelta(days=ents.DEFAULT_LOOKBACK_DAYS)))

    def test_scoped_by_user(self):
        eid = _util.make_entity(self.user)
        self.assertIsNone(ents.get_entity(self.other, eid))
        self.assertEqual(ents.list_entities(self.other), [])
        self.assertFalse(ents.update_entity(self.other, eid, {"name": "Hijacked"}))
        self.assertFalse(ents.archive_entity(self.other, eid))
        self.assertEqual(ents.get_entity(self.user, eid)["name"], "Acme Widgets Private Limited")
        self.assertIsNone(ents.entity_calendar(self.other, eid))
        self.assertEqual(ents.sync_obligations(self.other, eid), 0)
        self.assertEqual(ents.list_events(self.other, eid), [])

    def test_update_and_archive(self):
        eid = _util.make_entity(self.user)
        self.assertTrue(ents.update_entity(self.user, eid, {"name": "Acme Two", "entity_type": "bogus",
                                                            "share_capital": "500000", "state": "Karnataka"}))
        e = ents.get_entity(self.user, eid)
        self.assertEqual((e["name"], e["entity_type"], e["share_capital"], e["state"]),
                         ("Acme Two", "private", 500000, "Karnataka"))
        self.assertTrue(ents.update_entity(self.user, eid, {}))  # nothing to change is not an error
        self.assertTrue(ents.archive_entity(self.user, eid))
        self.assertIsNone(ents.get_entity(self.user, eid))
        self.assertEqual(ents.list_entities(self.user), [])
        self.assertFalse(ents.update_entity(self.user, eid, {"name": "Back"}))


class Sync(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()

    def test_sync_is_idempotent(self):
        eid = _util.make_entity(self.user, flags={"gst_registered": True, "tds_deductor": True})
        before = rows(eid)
        self.assertTrue(before)
        n1 = ents.sync_obligations(self.user, eid)
        n2 = ents.sync_obligations(self.user, eid)
        after = rows(eid)
        self.assertEqual(n1, n2)
        self.assertEqual(n1, len(after))
        self.assertEqual([(r["id"], r["rule_id"], r["period"], r["due_date"]) for r in before],
                         [(r["id"], r["rule_id"], r["period"], r["due_date"]) for r in after])
        self.assertTrue(all(r["origin"] == "rule" and r["status"] == "pending" for r in after))

    def test_rows_respect_profile(self):
        eid = _util.make_entity(self.user)
        rules = {r["rule_id"] for r in rows(eid)}
        self.assertIn("IN-MCA-BOARD", rules)
        self.assertNotIn("IN-GST-R1", rules)
        self.assertFalse(any(r.startswith("IN-SEBI") for r in rules))

    def test_flag_off_retires_pending_rows_only(self):
        eid = _util.make_entity(self.user, flags={"gst_registered": True},
                                tracking_from=iso(TODAY - timedelta(days=120)))
        gst = rows(eid, rule_id="IN-GST-3B")
        self.assertGreaterEqual(len(gst), 3)
        past = [r for r in gst if r["due_date"] <= iso(TODAY)]
        filed = past[0]
        self.assertTrue(ents.update_obligation(self.user, filed["id"], {"status": "filed", "filed_on": filed["due_date"]}))
        na = past[1]
        self.assertTrue(ents.update_obligation(self.user, na["id"], {"status": "na"}))
        ev = ents.log_event(self.user, eid, "director_appointed", iso(TODAY - timedelta(days=3)))
        task = ents.add_task(self.user, eid, "Renew trade licence", iso(TODAY + timedelta(days=20)))

        self.assertTrue(ents.update_entity(self.user, eid, {"flags": {}}))
        left = {r["id"]: r for r in rows(eid)}
        remaining_gst = [r for r in left.values() if r["rule_id"].startswith("IN-GST")]
        self.assertEqual({r["id"] for r in remaining_gst}, {filed["id"], na["id"]})
        self.assertEqual(left[filed["id"]]["status"], "filed")
        self.assertTrue(any(r["event_id"] == ev["event_id"] for r in left.values()))
        self.assertIn(task, left)
        self.assertTrue(any(r["rule_id"] == "IN-MCA-BOARD" for r in left.values()))

        # Switching the flag back brings the pending rows back without touching the filed one.
        ents.update_entity(self.user, eid, {"flags": {"gst_registered": True}})
        again = rows(eid, rule_id="IN-GST-3B")
        self.assertEqual(len(again), len(gst))
        self.assertEqual([r for r in again if r["id"] == filed["id"]][0]["status"], "filed")

    def test_incorporation_after_tracking_start_trims_window(self):
        inc = TODAY - timedelta(days=10)
        eid = _util.make_entity(self.user, incorporation_date=iso(inc),
                                tracking_from=iso(TODAY - timedelta(days=200)))
        got = rows(eid)
        self.assertTrue(got)  # otherwise all() below passes on nothing
        self.assertTrue(all(r["due_date"] >= iso(inc) for r in got))


class People(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()
        # A fixed tracking start keeps the 2028/2029 DIR-3 KYC cycles in the
        # window whatever year the suite runs in.
        self.eid = _util.make_entity(self.user, tracking_from="2026-01-01")

    def test_validation_and_scoping(self):
        with self.assertRaises(ValueError):
            ents.add_person(self.user, self.eid, {"name": " "})
        self.assertIsNone(ents.add_person(_util.uid("other"), self.eid, {"name": "Asha Rao"}))
        self.assertFalse(ents.remove_person(_util.uid("other"), self.eid, 1))

    def test_dsc_row_and_din_cleanup(self):
        dsc = TODAY + timedelta(days=30)
        pid = ents.add_person(self.user, self.eid, {"name": "Asha Rao", "din": "0123-4567",
                                                    "dsc_expiry": iso(dsc), "designation": "Director"})
        person = [p for p in ents.list_people(self.eid) if p["id"] == pid][0]
        self.assertEqual(person["din"], "01234567")
        dsc_rows = rows(self.eid, rule_id=ents.DSC_RULE["id"])
        self.assertEqual([(r["period"], r["due_date"], r["origin"]) for r in dsc_rows],
                         [(f"Asha Rao · #{pid}", iso(dsc), "person")])

    def test_dir3kyc_rows_per_cycle(self):
        ents.add_person(self.user, self.eid, {"name": "Asha Rao", "din": "01234567"})
        ents.add_person(self.user, self.eid, {"name": "New Director", "din": "09876543",
                                              "din_allotted_on": "2025-06-01"})
        if TODAY + timedelta(days=ents.HORIZON_DAYS) < date(2028, 6, 30):
            # The first cycle (30 June 2028) is past today's 180-day horizon.
            self.assertEqual(rows(self.eid, rule_id=rb.DIR3KYC_RULE["id"]), [])
        ents.sync_obligations(self.user, self.eid, today=date(2028, 3, 1))
        kyc = rows(self.eid, rule_id=rb.DIR3KYC_RULE["id"])
        self.assertEqual([(r["period"], r["due_date"]) for r in kyc],
                         [("Asha Rao · DIN 01234567 · 2028", "2028-06-30")])
        # A DIN allotted in FY2025-26 is first due on 30 June 2029.
        ents.sync_obligations(self.user, self.eid, today=date(2029, 3, 1))
        kyc = {r["period"]: r["due_date"] for r in rows(self.eid, rule_id=rb.DIR3KYC_RULE["id"])}
        self.assertEqual(kyc.get("New Director · DIN 09876543 · 2029"), "2029-06-30")

    def test_filed_cycle_does_not_hide_the_next(self):
        ents.add_person(self.user, self.eid, {"name": "Asha Rao", "din": "01234567"})
        ents.sync_obligations(self.user, self.eid, today=date(2028, 3, 1))
        first = rows(self.eid, rule_id=rb.DIR3KYC_RULE["id"])[0]
        conn = _util.connect()
        conn.execute("UPDATE entity_obligations SET status = 'filed', filed_on = '2028-06-01' WHERE id = ?", (first["id"],))
        conn.commit()
        conn.close()
        ents.sync_obligations(self.user, self.eid, today=date(2031, 3, 1))
        kyc = {r["period"]: r["status"] for r in rows(self.eid, rule_id=rb.DIR3KYC_RULE["id"])}
        self.assertEqual(kyc, {"Asha Rao · DIN 01234567 · 2028": "filed",
                               "Asha Rao · DIN 01234567 · 2031": "pending"})

    def test_ceased_people_get_no_rows(self):
        ents.add_person(self.user, self.eid, {"name": "Former Director", "din": "01111111",
                                              "ceased_on": iso(TODAY - timedelta(days=5)),
                                              "dsc_expiry": iso(TODAY + timedelta(days=10))})
        ents.sync_obligations(self.user, self.eid, today=date(2028, 3, 1))
        self.assertEqual(rows(self.eid, origin="person"), [])
        self.assertNotIn("01111111", ents.all_dins_for_user(self.user))

    def test_remove_person_drops_pending_reminders(self):
        pid = ents.add_person(self.user, self.eid, {"name": "Asha Rao", "din": "01234567",
                                                    "dsc_expiry": "2027-12-31"})
        keep = ents.add_person(self.user, self.eid, {"name": "Ravi Iyer", "din": "07654321",
                                                     "dsc_expiry": "2028-01-31"})
        ents.sync_obligations(self.user, self.eid, today=date(2028, 3, 1))
        self.assertEqual(len(rows(self.eid, origin="person")), 4)
        self.assertTrue(ents.remove_person(self.user, self.eid, pid))
        left = rows(self.eid, origin="person")
        self.assertEqual(len(left), 2)
        self.assertTrue(all("Ravi Iyer" in r["period"] for r in left))
        self.assertEqual([p["id"] for p in ents.list_people(self.eid)], [keep])

    def test_all_dins_for_user(self):
        ents.add_person(self.user, self.eid, {"name": "Asha Rao", "din": "01234567", "designation": "Director"})
        other = _util.uid("other")
        oeid = _util.make_entity(other)
        ents.add_person(other, oeid, {"name": "Someone Else", "din": "05555555"})
        got = ents.all_dins_for_user(self.user)
        self.assertEqual(list(got), ["01234567"])
        self.assertEqual(got["01234567"][0]["entity_id"], self.eid)
        self.assertEqual(got["01234567"][0]["person"], "Asha Rao")


class EventsAndTasks(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()
        self.eid = _util.make_entity(self.user, tracking_from=FAR_FUTURE)

    def test_log_event_validation(self):
        with self.assertRaises(ValueError):
            ents.log_event(self.user, self.eid, "party_held", "2026-01-01")
        with self.assertRaises(ValueError):
            ents.log_event(self.user, self.eid, "director_appointed", "not a date")
        self.assertIsNone(ents.log_event(_util.uid("other"), self.eid, "director_appointed", "2026-01-01"))

    def test_log_event_creates_follow_ups(self):
        r = ents.log_event(self.user, self.eid, "director_appointed", "2026-09-01", "Appointed Asha Rao")
        self.assertEqual(r["created"], ["IN-MCA-DIR12"])
        [ob] = rows(self.eid, origin="event")
        self.assertEqual((ob["rule_id"], ob["due_date"], ob["event_id"], ob["notes"]),
                         ("IN-MCA-DIR12", "2026-10-01", r["event_id"], "Appointed Asha Rao"))
        # The same event type again is a separate filing.
        r2 = ents.log_event(self.user, self.eid, "director_appointed", "2026-09-01")
        self.assertEqual(len(rows(self.eid, origin="event")), 2)
        self.assertNotEqual(r["event_id"], r2["event_id"])
        events = ents.list_events(self.user, self.eid)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["label"], rb.EVENT_TYPES["director_appointed"])

    def test_listed_board_meeting_scheduled_gives_reg29_before_the_date(self):
        eid = _util.make_entity(self.user, entity_type="listed", tracking_from=FAR_FUTURE)
        r = ents.log_event(self.user, eid, "board_meeting_scheduled", "2026-11-12")
        self.assertEqual(r["created"], ["IN-SEBI-REG29"])
        # Two clear working days before a Thursday meeting: the Monday.
        self.assertEqual(rows(eid, origin="event")[0]["due_date"], "2026-11-09")

    def test_event_with_no_follow_up(self):
        r = ents.log_event(self.user, self.eid, "board_meeting_scheduled", "2026-11-12")
        self.assertEqual(r["created"], [])
        self.assertEqual(rows(self.eid, origin="event"), [])

    def test_add_task(self):
        with self.assertRaises(ValueError):
            ents.add_task(self.user, self.eid, "", "2026-10-01")
        with self.assertRaises(ValueError):
            ents.add_task(self.user, self.eid, "File something", "someday")
        self.assertIsNone(ents.add_task(_util.uid("other"), self.eid, "Task", "2026-10-01"))
        tid = ents.add_task(self.user, self.eid, "  Renew shop licence ", "2026-10-01", notes="Ward office")
        [t] = rows(self.eid, id=tid)
        self.assertEqual((t["rule_id"], t["title"], t["due_date"], t["origin"], t["notes"]),
                         ("CUSTOM", "Renew shop licence", "2026-10-01", "custom", "Ward office"))
        cal = ents.entity_calendar(self.user, self.eid, TODAY)
        item = [i for i in cal["items"] if i["id"] == tid][0]
        self.assertEqual(item["name"], "Renew shop licence")


class UpdateObligation(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()
        self.eid = _util.make_entity(self.user, tracking_from=FAR_FUTURE)
        self.tid = ents.add_task(self.user, self.eid, "Board pack", iso(TODAY - timedelta(days=3)))

    def get(self):
        return rows(self.eid, id=self.tid)[0]

    def test_transitions(self):
        self.assertTrue(ents.update_obligation(self.user, self.tid, {"status": "filed", "srn": "AB1234567",
                                                                      "filed_on": iso(TODAY - timedelta(days=1))}))
        r = self.get()
        self.assertEqual((r["status"], r["filed_on"], r["srn"]), ("filed", iso(TODAY - timedelta(days=1)), "AB1234567"))
        # Back to pending clears the filing date but keeps the SRN note.
        self.assertTrue(ents.update_obligation(self.user, self.tid, {"status": "pending"}))
        r = self.get()
        self.assertEqual((r["status"], r["filed_on"], r["srn"]), ("pending", "", "AB1234567"))
        self.assertTrue(ents.update_obligation(self.user, self.tid, {"status": "na", "notes": "Not applicable this year"}))
        r = self.get()
        self.assertEqual((r["status"], r["filed_on"], r["notes"]), ("na", "", "Not applicable this year"))
        # Filed with no date means today.
        self.assertTrue(ents.update_obligation(self.user, self.tid, {"status": "filed"}))
        self.assertEqual(self.get()["filed_on"], iso(TODAY))

    def test_notes_only_keeps_status(self):
        ents.update_obligation(self.user, self.tid, {"status": "pending"})
        ents.update_obligation(self.user, self.tid, {"notes": "Waiting for the auditor"})
        r = self.get()
        self.assertEqual((r["status"], r["notes"]), ("pending", "Waiting for the auditor"))

    # Regression: a partial PATCH that omits filed_on (adding the SRN a few
    # days later) used to reset filed_on to today, turning on-time filings late.
    def test_adding_srn_later_keeps_filing_date(self):
        filed_on = iso(TODAY - timedelta(days=3))  # the due date, so on time
        ents.update_obligation(self.user, self.tid, {"status": "filed", "filed_on": filed_on})
        ents.update_obligation(self.user, self.tid, {"srn": "AB1234567"})
        r = self.get()
        self.assertEqual((r["status"], r["srn"]), ("filed", "AB1234567"))
        self.assertEqual(r["filed_on"], filed_on)

    def test_validation(self):
        with self.assertRaises(ValueError):
            ents.update_obligation(self.user, self.tid, {"status": "done"})
        with self.assertRaises(ValueError):
            ents.update_obligation(self.user, self.tid, {"status": "filed", "filed_on": "31/12/2026"})
        with self.assertRaises(ValueError):
            ents.update_obligation(self.user, self.tid, {"status": "filed", "filed_on": iso(TODAY + timedelta(days=1))})
        self.assertEqual(self.get()["status"], "pending")

    def test_other_users_obligation(self):
        self.assertFalse(ents.update_obligation(_util.uid("other"), self.tid, {"status": "filed"}))
        self.assertFalse(ents.update_obligation(self.user, 99_999_999, {"status": "filed"}))
        self.assertEqual(self.get()["status"], "pending")


class AgmDatesFollowTheRealAgm(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()
        self.eid = _util.make_entity(self.user, tracking_from="2026-06-01", incorporation_date="2014-01-01")

    def due(self, rule_id, period="FY2025-26"):
        [r] = [r for r in rows(self.eid) if r["rule_id"] == rule_id and r["period"] == period]
        return r["due_date"]

    def test_default_assumes_a_30_september_agm(self):
        ents.sync_obligations(self.user, self.eid, today=date(2026, 9, 29))
        self.assertEqual((self.due("IN-MCA-AOC4"), self.due("IN-MCA-MGT7"), self.due("IN-MCA-ADT1")),
                         ("2026-10-30", "2026-11-29", "2026-10-15"))

    def test_recording_the_agm_redates_the_forms(self):
        ents.sync_obligations(self.user, self.eid, today=date(2026, 9, 29))
        agm = [r for r in rows(self.eid) if r["rule_id"] == "IN-MCA-AGM"][0]
        ents.update_obligation(self.user, agm["id"], {"status": "filed", "filed_on": "2026-08-20"})
        self.assertEqual((self.due("IN-MCA-AOC4"), self.due("IN-MCA-MGT7"), self.due("IN-MCA-ADT1")),
                         ("2026-09-19", "2026-10-19", "2026-09-04"))

    def test_filed_forms_are_not_moved(self):
        ents.sync_obligations(self.user, self.eid, today=date(2026, 9, 29))
        aoc = [r for r in rows(self.eid) if r["rule_id"] == "IN-MCA-AOC4"][0]
        ents.update_obligation(self.user, aoc["id"], {"status": "filed", "filed_on": "2026-09-10"})
        agm = [r for r in rows(self.eid) if r["rule_id"] == "IN-MCA-AGM"][0]
        ents.update_obligation(self.user, agm["id"], {"status": "filed", "filed_on": "2026-08-20"})
        self.assertEqual(self.due("IN-MCA-AOC4"), "2026-10-30")

    def test_record_agm_creates_the_row_and_is_user_scoped(self):
        self.assertIsNone(ents.record_agm(_util.uid("other"), self.eid, "2026-08-20"))
        self.assertEqual(ents.record_agm(self.user, self.eid, "2026-08-20"), "FY2025-26")
        self.assertEqual(self.due("IN-MCA-AOC4"), "2026-09-19")


class HalfYearPeriods(unittest.TestCase):
    """Half-year rows are dated from the half they cover, so a company
    incorporated mid-year still owes the half it was alive for."""

    def test_period_end_for_every_half_year_label(self):
        rule = {"schedule": {"kind": "half_fixed"}}
        cases = [("Jan–Jun 2026", date(2026, 6, 30), date(2026, 6, 30)),
                 ("Jul–Dec 2026", date(2026, 12, 31), date(2026, 12, 31)),
                 ("Apr–Sep 2026", date(2026, 10, 31), date(2026, 9, 30)),
                 ("Oct–Mar 2027", date(2027, 4, 30), date(2027, 3, 31)),
                 ("Jan–Jun, due Jul 2026", date(2026, 7, 15), date(2026, 6, 30)),
                 ("Jul–Dec, due Jan 2027", date(2027, 1, 15), date(2026, 12, 31))]
        for label, due, end in cases:
            with self.subTest(label=label):
                self.assertEqual(ents._period_end(rule, {"period": label, "due_date": due}), end)

    def test_new_opc_keeps_its_first_half_year_board_meeting(self):
        user = _util.uid()
        eid = _util.make_entity(user, entity_type="opc", incorporation_date="2026-08-10", tracking_from="2026-08-10")
        ents.sync_obligations(user, eid, today=date(2026, 10, 1))
        periods = {r["period"] for r in rows(eid) if r["rule_id"] == "IN-MCA-BOARD-HALF"}
        self.assertIn("Jul–Dec 2026", periods)
        self.assertNotIn("Jan–Jun 2026", periods)

    def test_new_maharashtra_employer_keeps_its_first_lwf_return(self):
        user = _util.uid()
        eid = _util.make_entity(user, state="Maharashtra", incorporation_date="2026-03-01", tracking_from="2026-03-01",
                                flags={"employees_5": True})
        ents.sync_obligations(user, eid, today=date(2026, 6, 1))
        periods = {r["period"] for r in rows(eid) if r["rule_id"] == "IN-LAB-LWF-MH"}
        self.assertIn("Jan–Jun, due Jul 2026", periods)


class ExchangeSuggestions(unittest.TestCase):
    """A listed company's NSE filings are offered as possible matches for its
    SEBI rows. Nothing is ever closed without the CS confirming it."""

    def setUp(self):
        from database import get_connection
        conn = get_connection(); conn.execute("DELETE FROM filings WHERE company = 'VERIFYCO'"); conn.commit(); conn.close()
        self.user = _util.uid()
        self.eid = _util.make_entity(self.user, entity_type="listed", symbol="VERIFYCO", tracking_from="2026-01-01",
                                     incorporation_date="2000-01-01")
        self.sync()

    def sync(self):
        ents.sync_obligations(self.user, self.eid, today=date(2026, 10, 1))

    def row(self, rule_id, period_prefix):
        return [r for r in rows(self.eid) if r["rule_id"] == rule_id and r["period"].startswith(period_prefix)][0]

    def add_filings(self, *items):
        from database import get_connection
        conn = get_connection()
        conn.execute("DELETE FROM filings WHERE company = 'VERIFYCO'")
        for title, day in items:
            conn.execute("INSERT INTO filings (company, title, description, date, sort_date, pdf_link, first_seen) VALUES (?,?,?,?,?,?,?)",
                         ("VERIFYCO", title, "", day, day, "https://example.org/x.pdf", "2026-10-01"))
        conn.commit(); conn.close()

    def test_results_filing_is_suggested_not_closed(self):
        self.add_filings(("Financial Result Updates", "2026-08-10"))
        self.sync()
        r = self.row("IN-SEBI-REG33", "Q1")
        self.assertEqual(r["status"], "pending")
        self.assertEqual((r["suggest_date"], r["suggest_text"]), ("2026-08-10", "Financial Result Updates"))
        self.assertEqual(r["evidence_text"], "")

    def test_confirming_keeps_the_filing_as_evidence_and_reopening_clears_it(self):
        self.add_filings(("Financial Result Updates", "2026-08-25"))   # due 14 Aug
        self.sync()
        r = self.row("IN-SEBI-REG33", "Q1")
        self.assertTrue(ents.update_obligation(self.user, r["id"], {"status": "filed", "filed_on": "2026-08-25",
                                                                    "accept_suggestion": True}))
        r = self.row("IN-SEBI-REG33", "Q1")
        self.assertEqual((r["status"], r["evidence_text"]), ("filed", "Financial Result Updates"))
        item = [i for i in ents.entity_calendar(self.user, self.eid, date(2026, 10, 1))["items"] if i["id"] == r["id"]][0]
        self.assertEqual(item["penalty"]["days_late"], 11)
        ents.update_obligation(self.user, r["id"], {"status": "pending"})
        r = self.row("IN-SEBI-REG33", "Q1")
        self.assertEqual((r["status"], r["evidence_text"], r["evidence_url"]), ("pending", "", ""))

    def test_announcements_the_audit_caught_are_not_matched(self):
        # Real NSE categories that the old keyword match took for filings.
        self.add_filings(("General Updates", "2026-08-05"), ("Copy of Newspaper Publication", "2026-08-06"),
                         ("Analysts/Institutional Investor Meet/Con. Call Updates", "2026-08-07"),
                         ("Disclosure under SEBI Takeover Regulations", "2026-07-10"),
                         ("Sale or disposal", "2026-07-12"), ("Change in Management", "2026-05-20"),
                         ("Updates", "2026-07-15"))
        self.sync()
        for r in rows(self.eid):
            if r["rule_id"].startswith("IN-SEBI-"):
                self.assertEqual((r["status"], r["suggest_text"]), ("pending", ""), r["rule_id"] + " " + r["period"])

    def test_shareholding_pattern_is_never_matched_from_announcements(self):
        self.add_filings(("Shareholding Pattern", "2026-07-15"), ("Financial Result Updates", "2026-07-15"))
        self.sync()
        self.assertEqual(self.row("IN-SEBI-REG31", "Q1")["suggest_text"], "")

    def test_filing_from_before_the_period_ended_is_not_this_periods(self):
        self.add_filings(("Financial Result Updates", "2026-05-20"))   # Q4 results, filed before Q1 ended
        self.sync()
        self.assertEqual(self.row("IN-SEBI-REG33", "Q1")["suggest_text"], "")

    def test_rows_the_old_version_closed_go_back_to_pending(self):
        from database import get_connection
        r = self.row("IN-SEBI-REG31", "Q1")
        conn = get_connection()
        conn.execute("UPDATE entity_obligations SET status='filed', filed_on='2026-07-10', evidence_text='Sale or disposal', "
                     "evidence_url='https://example.org/x.pdf' WHERE id = ?", (r["id"],))
        conn.execute("DELETE FROM settings WHERE key = 'migr_exchange_autoclose_undone'")
        conn.commit(); conn.close()
        ents.init_entities_db()
        r = self.row("IN-SEBI-REG31", "Q1")
        self.assertEqual((r["status"], r["filed_on"], r["evidence_text"]), ("pending", "", ""))
        self.sync()
        self.assertEqual(self.row("IN-SEBI-REG31", "Q1")["suggest_text"], "")


class MarkPastFiled(unittest.TestCase):
    def test_marks_only_open_items_before_the_cutoff_and_only_for_the_owner(self):
        user = _util.uid()
        eid = _util.make_entity(user, tracking_from=FAR_FUTURE)
        past = ents.add_task(user, eid, "Old", iso(TODAY - timedelta(days=20)))
        future = ents.add_task(user, eid, "New", iso(TODAY + timedelta(days=5)))
        self.assertIsNone(ents.mark_past_filed(_util.uid("other"), eid))
        self.assertEqual(ents.mark_past_filed(user, eid), 1)
        got = {r["id"]: r for r in rows(eid)}
        self.assertEqual((got[past]["status"], got[past]["filed_on"]), ("filed", iso(TODAY - timedelta(days=20))))
        self.assertEqual(got[future]["status"], "pending")
        # Filed on the due date, so it counts as on time and carries no penalty.
        item = [i for i in ents.entity_calendar(user, eid, TODAY)["items"] if i["id"] == past][0]
        self.assertIsNone(item["penalty"])


class WorkspaceSummary(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()
        self.eid = _util.make_entity(self.user, tracking_from=FAR_FUTURE)
        d = lambda n: iso(TODAY + timedelta(days=n))
        u, e = self.user, self.eid
        self.task_overdue = ents.add_task(u, e, "Board pack", d(-10))                       # overdue, no penalty text
        ents.log_event(u, e, "director_appointed", d(-61))                                  # DIR-12 due d(-31): 4 x 200
        ents.log_event(u, e, "incorporated", d(-200))                                       # INC-20A due d(-20): 50,000 flat;
                                                                                            # first board meeting due d(-170): text only
        ents.log_event(u, e, "charge_created", d(-40))                                      # CHG-1 due d(-10): text only
        on_time = ents.add_task(u, e, "Circulate minutes", d(-5))
        ents.update_obligation(u, on_time, {"status": "filed", "filed_on": d(-6)})
        late = ents.log_event(u, e, "director_appointed", d(-50))                           # DIR-12 due d(-20)
        late_row = [r for r in rows(e) if r["event_id"] == late["event_id"]][0]
        ents.update_obligation(u, late_row["id"], {"status": "filed", "filed_on": d(-5)})   # 15 days late: 2x
        ents.add_task(u, e, "Due soon", d(10))
        ents.add_task(u, e, "Upcoming", d(30))

    def test_summary_math(self):
        ov = ents.workspace_overview(self.user, TODAY)
        s = ov["summary"]
        self.assertEqual(s["overdue"], 5)
        self.assertEqual(s["due_soon"], 1)
        self.assertEqual(s["upcoming"], 1)
        self.assertEqual(s["filed"], 2)
        # Only computable amounts on overdue items: 800 (DIR-12) + 50,000 (INC-20A).
        self.assertEqual(s["exposure"], 50_800)
        # CHG-1 and the first board meeting have stated penalties with no amount;
        # the custom task has none at all.
        self.assertEqual(s["exposure_uncomputed"], 2)
        # 1 on time out of 2 filed + 5 overdue.
        self.assertEqual(s["on_time_rate"], round(100 / 7))
        self.assertEqual(ov["today"], iso(TODAY))

    def test_items_are_open_and_overdue_first(self):
        ov = ents.workspace_overview(self.user, TODAY, view="all")
        states = [i["state"] for i in ov["items"]]
        self.assertEqual(states, ["overdue"] * 5 + ["due_soon", "upcoming"])
        overdue_dues = [i["due_date"] for i in ov["items"][:5]]
        self.assertEqual(overdue_dues, sorted(overdue_dues))
        dir12 = [i for i in ov["items"] if i["rule_id"] == "IN-MCA-DIR12"][0]
        self.assertEqual((dir12["penalty"]["days_late"], dir12["penalty"]["amount"]), (31, 800))
        self.assertEqual(dir12["entity_id"], self.eid)
        self.assertEqual(dir12["form"], "DIR-12")

    def test_filed_late_is_not_on_time_but_not_exposure(self):
        cal = ents.entity_calendar(self.user, self.eid, TODAY)
        filed = [i for i in cal["items"] if i["status"] == "filed"]
        self.assertEqual(sorted(bool(i["penalty"]) for i in filed), [False, True])
        late = [i for i in filed if i["penalty"]][0]
        # DIR-12 is not a s.139/157 form, so the first band is 2x (₹400 on a ₹200 normal fee).
        self.assertEqual((late["penalty"]["days_late"], late["penalty"]["amount"]), (15, 400))
        self.assertEqual(late["state"], "filed")

    def test_sums_across_entities_and_scoping(self):
        second = _util.make_entity(self.user, name="Second Co Private Limited", tracking_from=FAR_FUTURE)
        ents.log_event(self.user, second, "incorporated", iso(TODAY - timedelta(days=190)))  # 50,000
        ov = ents.workspace_overview(self.user, TODAY)
        self.assertEqual(len(ov["entities"]), 2)
        self.assertEqual(ov["summary"]["overdue"], 7)
        self.assertEqual(ov["summary"]["exposure"], 100_800)
        per = {e["entity"]["id"]: e["summary"]["exposure"] for e in ov["entities"]}
        self.assertEqual(per, {self.eid: 50_800, second: 50_000})
        empty = ents.workspace_overview(_util.uid("other"), TODAY)
        self.assertEqual((empty["entities"], empty["items"]), ([], []))
        self.assertIsNone(empty["summary"]["on_time_rate"])
        self.assertEqual(empty["summary"]["exposure"], 0)

    def test_views_counts_and_limit_are_computed_on_the_server(self):
        ov = ents.workspace_overview(self.user, TODAY)
        self.assertEqual(ov["view"], "overdue")              # first non-empty view
        self.assertEqual(ov["counts"], {"overdue": 5, "next15": 1, "next90": 2, "all": 7})
        self.assertEqual([i["state"] for i in ov["items"]], ["overdue"] * 5)
        nxt = ents.workspace_overview(self.user, TODAY, view="next90")
        self.assertEqual([i["state"] for i in nxt["items"]], ["due_soon", "upcoming"])
        self.assertEqual(nxt["next_due"]["name"], "Due soon")
        capped = ents.workspace_overview(self.user, TODAY, view="all", limit=3)
        self.assertEqual((len(capped["items"]), capped["total"]), (3, 7))
        custom = ents.workspace_overview(self.user, TODAY, view="all", regime="Custom")
        self.assertTrue(custom["items"] and all(i["regime"] == "Custom" for i in custom["items"]))
        self.assertIn("Custom", ov["regimes"])
        none = ents.workspace_overview(self.user, TODAY, entity_id=self.eid + 10_000)
        self.assertEqual((none["view"], none["items"], none["total"]), ("all", [], 0))

    def test_on_time_rate_none_when_nothing_due(self):
        u = _util.uid()
        e = _util.make_entity(u, tracking_from=FAR_FUTURE)
        ents.add_task(u, e, "Later", iso(TODAY + timedelta(days=40)))
        self.assertIsNone(ents.workspace_overview(u, TODAY)["summary"]["on_time_rate"])


if __name__ == "__main__":
    unittest.main()
