"""sdd.py: the append-only Structured Digital Database (hash chain, tamper
detection, corrections), trading window and pre-clearance."""
from tests import _util  # noqa: F401  (must come first)

import unittest
from datetime import date, datetime, timedelta

import sdd

PAN_A = "ABCPE1234F"
PAN_B = "BCDPF2345G"


def shared_at(days_ago=2, hour=11):
    return (datetime.now() - timedelta(days=days_ago)).replace(hour=hour, minute=30).strftime("%Y-%m-%dT%H:%M")


class SddBase(unittest.TestCase):
    def setUp(self):
        self.user, self.other = _util.uid(), _util.uid("other")
        self.eid = _util.make_entity(self.user, name="Acme Listed Limited", entity_type="listed")
        self.upsi = sdd.create_upsi(self.user, self.eid, "Q2 FY27 results", "Financial results")

    def entry(self, **over):
        data = {"upsi_id": self.upsi, "recipient_type": "external", "shared_by_name": "Priya Menon (CFO)",
                "shared_by_pan": PAN_A, "shared_with_name": "Rahul Shah", "shared_with_pan": PAN_B,
                "shared_with_org": "Shah & Co, statutory auditors", "purpose": "Limited review",
                "shared_at": shared_at()}
        data.update(over)
        return data

    def record(self, **over):
        return sdd.record_entry(self.user, self.eid, self.entry(**over))

    def db(self, sql, *args):
        conn = _util.connect()
        conn.execute(sql, args)
        conn.commit()
        conn.close()


class RecordEntry(SddBase):
    def test_validation(self):
        bad = [
            {"recipient_type": "friend"},
            {"shared_by_name": " "},
            {"shared_with_name": ""},
            {"shared_by_pan": "ABC123"},
            {"shared_with_pan": "abcde12345"},
            {"shared_with_pan": "", "identifier_note": ""},
            {"shared_at": "yesterday"},
            {"shared_at": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")},
            {"upsi_id": 999_999},
            {"upsi_id": "abc"},
            {"corrects_id": 999_999},
            {"corrects_id": "x"},
        ]
        for over in bad:
            with self.subTest(**{k: str(v) for k, v in over.items()}):
                with self.assertRaises(ValueError):
                    self.record(**over)
        self.assertEqual(sdd.list_entries(self.user, self.eid), [])

    def test_upsi_must_belong_to_the_entity(self):
        other_eid = _util.make_entity(self.user, name="Sister Co Limited", entity_type="listed")
        foreign_upsi = sdd.create_upsi(self.user, other_eid, "Merger talks")
        with self.assertRaises(ValueError):
            self.record(upsi_id=foreign_upsi)

    def test_pan_is_normalised_and_masked_in_lists(self):
        eid = self.record(shared_with_pan=" bcdpf 2345g ")
        full = sdd.get_entry(self.user, self.eid, eid)
        self.assertEqual(full["shared_with_pan"], PAN_B)
        [listed] = sdd.list_entries(self.user, self.eid)
        self.assertEqual(listed["shared_with_pan"], "BC•••••45G")
        self.assertEqual(listed["shared_by_pan"], "AB•••••34F")
        self.assertEqual(sdd.export_rows(self.user, self.eid)[0]["shared_with_pan"], PAN_B)

    def test_alternative_identifier_when_no_pan(self):
        eid = self.record(shared_with_pan="", identifier_note="Passport  Z1234567")
        full = sdd.get_entry(self.user, self.eid, eid)
        self.assertTrue(full["no_pan"])
        self.assertEqual(full["identifier_note"], "Passport Z1234567")
        self.assertEqual(full["shared_with_pan"], "")
        [listed] = sdd.list_entries(self.user, self.eid)
        self.assertTrue(listed["shared_with_pan"].startswith(sdd.ID_PREFIX))
        self.assertNotIn("Z1234567", listed["shared_with_pan"])

    def test_upsi_entry_counts_and_public_marking(self):
        self.record()
        self.record()
        [u] = sdd.list_upsi(self.user, self.eid)
        self.assertEqual(u["entries"], 2)
        with self.assertRaises(ValueError):
            sdd.mark_upsi_public(self.user, self.eid, self.upsi, (sdd.today_ist() + timedelta(days=1)).isoformat())
        self.assertTrue(sdd.mark_upsi_public(self.user, self.eid, self.upsi, sdd.today_ist().isoformat()))
        with self.assertRaises(ValueError):
            sdd.mark_upsi_public(self.user, self.eid, self.upsi, "")
        with self.assertRaises(ValueError):
            sdd.create_upsi(self.user, self.eid, "  ")


class HashChain(SddBase):
    def test_empty_chain_is_ok(self):
        v = sdd.verify_chain(self.user, self.eid)
        self.assertEqual((v["ok"], v["entries"], v["broken_at"], v["head"]), (True, 0, None, sdd.GENESIS))

    def test_chain_links_and_verifies(self):
        ids = [self.record(purpose=f"Review {i}") for i in range(3)]
        conn = _util.connect()
        rows = [dict(r) for r in conn.execute("SELECT id, prev_hash, hash FROM sdd_entries WHERE entity_id = ? ORDER BY id",
                                              (self.eid,)).fetchall()]
        conn.close()
        self.assertEqual([r["id"] for r in rows], ids)
        self.assertEqual(rows[0]["prev_hash"], sdd.GENESIS)
        self.assertEqual(rows[1]["prev_hash"], rows[0]["hash"])
        self.assertEqual(rows[2]["prev_hash"], rows[1]["hash"])
        v = sdd.verify_chain(self.user, self.eid)
        self.assertEqual((v["ok"], v["entries"], v["broken_at"], v["head"]), (True, 3, None, rows[2]["hash"]))

    def test_edited_row_is_named(self):
        ids = [self.record(purpose=f"Review {i}") for i in range(3)]
        self.db("UPDATE sdd_entries SET purpose = 'Nothing to see' WHERE id = ?", ids[1])
        v = sdd.verify_chain(self.user, self.eid)
        self.assertFalse(v["ok"])
        self.assertEqual(v["broken_at"], ids[1])
        self.assertIsNone(v["head"])

    def test_edit_with_recomputed_hash_breaks_the_next_link(self):
        ids = [self.record(purpose=f"Review {i}") for i in range(3)]
        conn = _util.connect()
        row = dict(conn.execute("SELECT * FROM sdd_entries WHERE id = ?", (ids[1],)).fetchone())
        conn.close()
        row["shared_with_name"] = "Someone else"
        forged = sdd._entry_hash(row["prev_hash"], row)
        self.db("UPDATE sdd_entries SET shared_with_name = ?, hash = ? WHERE id = ?", "Someone else", forged, ids[1])
        self.assertEqual(sdd.verify_chain(self.user, self.eid)["broken_at"], ids[2])

    def test_deleted_row_is_detected(self):
        ids = [self.record(purpose=f"Review {i}") for i in range(3)]
        self.db("DELETE FROM sdd_entries WHERE id = ?", ids[1])
        v = sdd.verify_chain(self.user, self.eid)
        self.assertEqual((v["ok"], v["broken_at"]), (False, ids[2]))

    def test_chains_are_per_entity(self):
        other_eid = _util.make_entity(self.user, name="Sister Co Limited", entity_type="listed")
        other_upsi = sdd.create_upsi(self.user, other_eid, "Fund raise")
        first = self.record()
        o1 = sdd.record_entry(self.user, other_eid, self.entry(upsi_id=other_upsi))
        conn = _util.connect()
        prev = conn.execute("SELECT prev_hash FROM sdd_entries WHERE id = ?", (o1,)).fetchone()["prev_hash"]
        conn.close()
        self.assertEqual(prev, sdd.GENESIS)
        self.db("UPDATE sdd_entries SET purpose = 'x' WHERE id = ?", first)
        self.assertFalse(sdd.verify_chain(self.user, self.eid)["ok"])
        self.assertTrue(sdd.verify_chain(self.user, other_eid)["ok"])


class TamperEvidence(SddBase):
    """What the audit found the chain alone could not show."""

    def test_deleting_the_newest_entry_is_detected(self):
        ids = [self.record(purpose=f"Review {i}") for i in range(3)]
        self.db("DELETE FROM sdd_entries WHERE id = ?", ids[2])
        v = sdd.verify_chain(self.user, self.eid)
        self.assertFalse(v["ok"])
        self.assertIn("3 entries but 2 remain", v["problem"])

    def test_editing_a_upsi_record_is_detected(self):
        self.record()
        self.db("UPDATE sdd_upsi SET title = 'Nothing important' WHERE id = ?", self.upsi)
        v = sdd.verify_chain(self.user, self.eid)
        self.assertFalse(v["ok"])
        self.assertIn("Q2 FY27 results", v["problem"])

    def test_backdating_when_a_upsi_became_public_is_detected(self):
        sdd.mark_upsi_public(self.user, self.eid, self.upsi, date.today().isoformat())
        self.assertTrue(sdd.verify_chain(self.user, self.eid)["ok"])
        self.db("UPDATE sdd_upsi SET public_on = '2020-01-01' WHERE id = ?", self.upsi)
        self.assertFalse(sdd.verify_chain(self.user, self.eid)["ok"])

    def test_deleting_a_upsi_record_is_detected(self):
        self.db("DELETE FROM sdd_upsi WHERE id = ?", self.upsi)
        self.assertFalse(sdd.verify_chain(self.user, self.eid)["ok"])

    def test_activity_log_covers_windows_and_preclearance(self):
        sdd.close_window(self.user, self.eid, (date.today() + timedelta(days=30)).isoformat())
        conn = _util.connect()
        kinds = [r["kind"] for r in conn.execute("SELECT kind FROM pit_log WHERE entity_id = ? ORDER BY id", (self.eid,))]
        conn.close()
        self.assertEqual(kinds, ["upsi_opened", "window_closed"])
        self.assertTrue(sdd.verify_chain(self.user, self.eid)["ok"])
        self.db("DELETE FROM pit_log WHERE entity_id = ? AND kind = 'window_closed'", self.eid)
        self.assertFalse(sdd.verify_chain(self.user, self.eid)["ok"])

    def test_rewritten_anchor_is_caught_by_its_key(self):
        from unittest import mock
        import os
        with mock.patch.dict(os.environ, {"DELTA_SDD_KEY": "k" * 32}):
            ids = [self.record(purpose=f"Review {i}") for i in range(2)]
            self.db("DELETE FROM sdd_entries WHERE id = ?", ids[1])
            conn = _util.connect()
            head = conn.execute("SELECT hash FROM sdd_entries WHERE id = ?", (ids[0],)).fetchone()["hash"]
            conn.close()
            # Someone with database access also edits the anchor to match.
            self.db("UPDATE sdd_anchor SET entries = 1, entries_head = ? WHERE entity_id = ?", head, self.eid)
            v = sdd.verify_chain(self.user, self.eid)
            self.assertFalse(v["ok"])
            self.assertIn("anchor", v["problem"])

    def test_company_with_an_sdd_cannot_be_archived(self):
        import entities as ents
        with self.assertRaises(ValueError):
            ents.archive_entity(self.user, self.eid)
        self.assertIsNotNone(ents.get_entity(self.user, self.eid))
        bare = _util.make_entity(self.user, name="No SDD Limited")
        self.assertTrue(ents.archive_entity(self.user, bare))


class Corrections(SddBase):
    def test_correction_points_at_the_original(self):
        orig = self.record(shared_with_name="Rahul Sha")
        fix = self.record(shared_with_name="Rahul Shah", corrects_id=orig)
        by_id = {e["id"]: e for e in sdd.list_entries(self.user, self.eid)}
        self.assertEqual(by_id[fix]["action"], "correction")
        self.assertEqual(by_id[fix]["corrects_id"], orig)
        self.assertEqual(by_id[orig]["action"], "share")
        self.assertEqual(by_id[orig]["corrected_by"], [fix])
        self.assertTrue(sdd.verify_chain(self.user, self.eid)["ok"])
        # The original is never changed.
        self.assertEqual(sdd.get_entry(self.user, self.eid, orig)["shared_with_name"], "Rahul Sha")

    def test_cannot_correct_another_entitys_entry(self):
        other_eid = _util.make_entity(self.user, name="Sister Co Limited", entity_type="listed")
        other_upsi = sdd.create_upsi(self.user, other_eid, "Fund raise")
        foreign = sdd.record_entry(self.user, other_eid, self.entry(upsi_id=other_upsi))
        with self.assertRaises(ValueError):
            self.record(corrects_id=foreign)

    def test_no_edit_or_delete_path(self):
        public = [n for n in dir(sdd) if not n.startswith("_")]
        self.assertFalse([n for n in public if n.startswith(("update_entry", "delete_entry", "edit_entry"))])


class CrossUser(SddBase):
    def test_other_user_sees_and_writes_nothing(self):
        eid = self.record()
        o = self.other
        self.assertIsNone(sdd.record_entry(o, self.eid, self.entry()))
        self.assertIsNone(sdd.create_upsi(o, self.eid, "Snooping"))
        self.assertEqual(sdd.list_upsi(o, self.eid), [])
        self.assertEqual(sdd.list_entries(o, self.eid), [])
        self.assertIsNone(sdd.get_entry(o, self.eid, eid))
        self.assertIsNone(sdd.verify_chain(o, self.eid))
        self.assertIsNone(sdd.desk_summary(o, self.eid))
        self.assertFalse(sdd.mark_upsi_public(o, self.eid, self.upsi, ""))
        self.assertIsNone(sdd.add_person(o, self.eid, {"name": "X"}))
        self.assertIsNone(sdd.close_window(o, self.eid, "2026-10-01"))
        self.assertEqual(sdd.list_windows(o, self.eid), [])
        self.assertIsNone(sdd.request_preclearance(o, self.eid, {}))
        self.assertIsNone(sdd.decide_preclearance(o, self.eid, 1, True))
        self.assertEqual(len(sdd.list_entries(self.user, self.eid)), 1)

    def test_entry_id_from_another_entity_is_not_found(self):
        other_eid = _util.make_entity(self.user, name="Sister Co Limited", entity_type="listed")
        eid = self.record()
        self.assertIsNone(sdd.get_entry(self.user, other_eid, eid))


class TradingWindow(SddBase):
    def test_window_closed_on_boundaries(self):
        sdd.close_window(self.user, self.eid, "2026-10-01", "2026-10-10", "Q2 results")
        closed = lambda d: sdd.window_closed_on(self.eid, date.fromisoformat(d))
        self.assertFalse(closed("2026-09-30"))
        self.assertTrue(closed("2026-10-01"))
        self.assertTrue(closed("2026-10-09"))
        self.assertFalse(closed("2026-10-10"))  # reopens that day

    def test_open_ended_window_stays_closed(self):
        sdd.close_window(self.user, self.eid, "2026-12-01")
        self.assertTrue(sdd.window_closed_on(self.eid, date(2027, 5, 1)))
        self.assertFalse(sdd.window_closed_on(self.eid, date(2026, 11, 30)))

    def test_close_window_validation(self):
        with self.assertRaises(ValueError):
            sdd.close_window(self.user, self.eid, "")
        with self.assertRaises(ValueError):
            sdd.close_window(self.user, self.eid, "2026-10-10", "2026-10-10")
        with self.assertRaises(ValueError):
            sdd.close_window(self.user, self.eid, "2026-10-10", "soon")
        sdd.close_window(self.user, self.eid, "2026-10-01", "2026-10-10")
        with self.assertRaises(ValueError):
            sdd.close_window(self.user, self.eid, "2026-10-05", "2026-10-20")  # overlaps
        sdd.close_window(self.user, self.eid, "2027-01-01")
        with self.assertRaises(ValueError):
            sdd.close_window(self.user, self.eid, "2027-04-01", "2027-04-10")  # the open one blocks
        self.assertEqual(len(sdd.list_windows(self.user, self.eid)), 2)

    def test_window_states(self):
        sdd.close_window(self.user, self.eid, "2026-07-01", "2026-07-20")
        sdd.close_window(self.user, self.eid, "2026-10-01")
        sdd.close_window(self.user, self.eid, "2026-06-01", "2026-06-10")
        today = date(2026, 10, 5)
        states = {w["closes_on"]: w["state"] for w in sdd.list_windows(self.user, self.eid, today)}
        self.assertEqual(states, {"2026-06-01": "reopened", "2026-07-01": "reopened", "2026-10-01": "closed"})
        st = sdd.window_status(self.user, self.eid, today)
        self.assertTrue(st["closed"])
        self.assertEqual(st["current"]["closes_on"], "2026-10-01")


class PreClearance(SddBase):
    def setUp(self):
        super().setUp()
        self.person = sdd.add_person(self.user, self.eid, {"name": "Priya Menon", "pan": PAN_A,
                                                            "designation": "CFO", "email": "priya@acme.example"})

    def request(self, **over):
        data = {"person_id": self.person, "txn_type": "sell", "quantity": 500, "est_value": "250000"}
        data.update(over)
        return sdd.request_preclearance(self.user, self.eid, data)

    def test_request_validation(self):
        for over in ({"txn_type": "gift"}, {"quantity": 0}, {"quantity": "ten"}, {"est_value": "-5"},
                     {"person_id": 999_999}):
            with self.subTest(**{k: str(v) for k, v in over.items()}):
                with self.assertRaises(ValueError):
                    self.request(**over)

    def test_blocked_while_window_closed(self):
        rid = self.request()
        sdd.close_window(self.user, self.eid, "2026-10-01", "2026-10-10")
        with self.assertRaises(ValueError):
            sdd.decide_preclearance(self.user, self.eid, rid, True, today=date(2026, 10, 5))
        # Still pending, and rejection is allowed with a reason.
        self.assertEqual(sdd.list_preclearance(self.user, self.eid, date(2026, 10, 5))[0]["status"], "pending")
        with self.assertRaises(ValueError):
            sdd.decide_preclearance(self.user, self.eid, rid, False, notes="", today=date(2026, 10, 5))
        got = sdd.decide_preclearance(self.user, self.eid, rid, False, notes="Window closed", today=date(2026, 10, 5))
        self.assertEqual(got["status"], "rejected")
        with self.assertRaises(ValueError):
            sdd.decide_preclearance(self.user, self.eid, rid, True, today=date(2026, 10, 12))

    def test_valid_until_counts_trading_days_only(self):
        self.assertEqual(sdd._add_trading_days(date(2026, 10, 2), 7), date(2026, 10, 13))  # Fri -> Tue
        self.assertEqual(sdd._add_trading_days(date(2026, 10, 3), 1), date(2026, 10, 5))   # Sat -> Mon
        rid = self.request()
        got = sdd.decide_preclearance(self.user, self.eid, rid, True, today=date(2026, 10, 2))
        self.assertEqual(got, {"status": "approved", "valid_until": "2026-10-13", "cut_by_closure": ""})
        items = sdd.list_preclearance(self.user, self.eid, date(2026, 10, 14))
        self.assertEqual(items[0]["state"], "expired")
        self.assertEqual(sdd.list_preclearance(self.user, self.eid, date(2026, 10, 13))[0]["state"], "approved")

    def test_validity_stops_before_a_scheduled_closure(self):
        sdd.close_window(self.user, self.eid, "2026-11-06", "2026-11-20")
        rid = self.request()
        got = sdd.decide_preclearance(self.user, self.eid, rid, True, today=date(2026, 11, 2))
        self.assertEqual(got, {"status": "approved", "valid_until": "2026-11-05", "cut_by_closure": "2026-11-06"})

    def test_removed_person_cannot_be_approved(self):
        rid = self.request()
        self.assertTrue(sdd.remove_person(self.user, self.eid, self.person))
        with self.assertRaises(ValueError):
            sdd.decide_preclearance(self.user, self.eid, rid, True, today=date(2026, 10, 2))

    def test_unknown_request(self):
        self.assertFalse(sdd.decide_preclearance(self.user, self.eid, 999_999, True, today=date(2026, 10, 2)))


class Persons(SddBase):
    def test_add_validation_and_relatives(self):
        with self.assertRaises(ValueError):
            sdd.add_person(self.user, self.eid, {"name": ""})
        with self.assertRaises(ValueError):
            sdd.add_person(self.user, self.eid, {"name": "X", "email": "not-an-email"})
        with self.assertRaises(ValueError):
            sdd.add_person(self.user, self.eid, {"name": "X", "relation": "immediate_relative"})
        head = sdd.add_person(self.user, self.eid, {"name": "Priya Menon", "pan": PAN_A})
        with self.assertRaises(ValueError):
            sdd.add_person(self.user, self.eid, {"name": "Duplicate", "pan": PAN_A.lower()})
        rel = sdd.add_person(self.user, self.eid, {"name": "Arjun Menon", "relation": "immediate_relative",
                                                   "related_to": head, "pan": PAN_B})
        people = sdd.list_persons(self.user, self.eid)
        self.assertEqual([p["id"] for p in people], [head, rel])
        self.assertEqual(people[0]["pan_masked"], "AB•••••34F")
        # Removing the designated person removes their relatives too.
        sdd.remove_person(self.user, self.eid, head)
        self.assertEqual(sdd.list_persons(self.user, self.eid), [])


if __name__ == "__main__":
    unittest.main()
