"""secretarial.py: versioned statutory registers, the meeting kit (MGT-14
logic, notice checks, follow-ups when held) and the .docx meeting pack."""
from tests import _util  # noqa: F401  (must come first)

import io
import unittest
import zipfile
from datetime import date, timedelta

import docx

import entities as ents
import secretarial as sec
import rulebook as rb

TODAY = date.today()


def obligations(entity_id, **where):
    sql = "SELECT * FROM entity_obligations WHERE entity_id = ?"
    args = [entity_id]
    for k, v in where.items():
        sql += f" AND {k} = ?"
        args.append(v)
    conn = _util.connect()
    out = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return out


class Registers(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()
        self.eid = _util.make_entity(self.user)

    def test_versioning_keeps_history(self):
        first = sec.save_register_row(self.user, self.eid, "members",
                                      {"folio": "F-001", "name": "Asha Rao", "pan": "abcpe1234f", "shares": "1,000",
                                       "date_became_member": "2024-04-01"})
        [row] = sec.list_register(self.user, self.eid, "members")
        self.assertEqual((row["id"], row["version"], row["is_current"]), (first, 1, 1))
        self.assertEqual(row["data"]["pan"], "ABCPE1234F")
        self.assertEqual(row["data"]["shares"], "1000")
        key = row["row_key"]

        second = sec.save_register_row(self.user, self.eid, "members",
                                       {**row["data"], "shares": "1500"}, row_key=key)
        current = sec.list_register(self.user, self.eid, "members")
        self.assertEqual([(r["id"], r["version"], r["data"]["shares"]) for r in current], [(second, 2, "1500")])
        history = sec.list_register(self.user, self.eid, "members", include_history=True)
        self.assertEqual([(r["version"], r["is_current"], r["data"]["shares"]) for r in history],
                         [(1, 0, "1000"), (2, 1, "1500")])
        self.assertEqual(sec.get_register_row(self.user, self.eid, "members", key)["version"], 2)

        with self.assertRaises(ValueError):
            sec.save_register_row(self.user, self.eid, "members", {**row["data"], "shares": "1500"}, row_key=key)
        with self.assertRaises(ValueError):
            sec.save_register_row(self.user, self.eid, "members", {"name": "X"}, row_key="r-missing")

    def test_rows_are_separate_histories(self):
        sec.save_register_row(self.user, self.eid, "members", {"name": "Asha Rao"})
        sec.save_register_row(self.user, self.eid, "members", {"name": "Ravi Iyer"})
        rows = sec.list_register(self.user, self.eid, "members")
        self.assertEqual(len({r["row_key"] for r in rows}), 2)
        # Sorted: list_register orders by row_key (millisecond timestamp + random
        # hex), so two rows saved in the same millisecond come back in either order.
        self.assertEqual(sorted(r["data"]["name"] for r in rows), ["Asha Rao", "Ravi Iyer"])
        sec.save_register_row(self.user, self.eid, "charges", {"holder": "HDFC Bank", "amount": "5000000"})
        self.assertEqual(sec.register_counts(self.user, self.eid), {"members": 2, "charges": 1})

    def test_validation(self):
        bad = [
            ("members", {}),
            ("members", {"folio": "F-9"}),                          # name is required
            ("members", {"name": "X", "pan": "ABC"}),
            ("members", {"name": "X", "shares": "many"}),
            ("members", {"name": "X", "shares": "-10"}),
            ("members", {"name": "X", "date_became_member": "01/04/2024"}),
            ("members", {"name": "X", "email": "no-at-sign"}),
            ("directors_kmp", {"name": "X", "din_pan": "1234"}),
            ("transfers", {"transferor": "A"}),                     # both parties required
            ("no_such_register", {"name": "X"}),
        ]
        for key, data in bad:
            with self.subTest(register=key, data=data):
                with self.assertRaises(ValueError):
                    sec.save_register_row(self.user, self.eid, key, data)
        with self.assertRaises(ValueError):
            sec.save_register_row(self.user, self.eid, "members", ["not", "a", "dict"])
        ok = sec.save_register_row(self.user, self.eid, "directors_kmp", {"name": "Asha Rao", "din_pan": "01234567"})
        self.assertTrue(ok)

    def test_other_user(self):
        sec.save_register_row(self.user, self.eid, "members", {"name": "Asha Rao"})
        other = _util.uid("other")
        self.assertIsNone(sec.save_register_row(other, self.eid, "members", {"name": "Intruder"}))
        self.assertEqual(sec.list_register(other, self.eid, "members"), [])
        self.assertEqual(sec.register_counts(other, self.eid), {})
        self.assertEqual(len(sec.list_register(self.user, self.eid, "members")), 1)

    def test_register_meta(self):
        meta = {m["key"]: m for m in sec.register_meta()}
        self.assertEqual(set(meta), set(sec.REGISTERS))
        kinds = {f["key"]: f["kind"] for f in meta["members"]["fields"]}
        self.assertEqual((kinds["shares"], kinds["date_became_member"], kinds["name"]), ("number", "date", "text"))


class MeetingBase(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()
        self.private = _util.make_entity(self.user, name="Acme Widgets Private Limited", tracking_from="2099-01-01")
        self.public = _util.make_entity(self.user, name="Acme Public Limited", entity_type="public_unlisted",
                                        cin="U12345MH2010PLC123456", state="Maharashtra", tracking_from="2099-01-01")

    def meeting(self, entity_id, kind="board", items=(), when=None, **extra):
        when = when or (TODAY + timedelta(days=20)).isoformat() + "T11:00"
        return sec.create_meeting(self.user, entity_id, {"kind": kind, "scheduled_for": when,
                                                         "items": list(items), **extra})


class MeetingCreate(MeetingBase):
    def test_validation(self):
        for data in ({"kind": "party", "scheduled_for": "2026-10-20T11:00"},
                     {"kind": "board", "scheduled_for": "2026-10-20"},
                     {"kind": "board", "scheduled_for": "tomorrow at 11"},
                     {"kind": "board", "scheduled_for": "1999-10-20T11:00"},
                     {"kind": "board", "scheduled_for": "2026-10-20T11:00", "mode": "carrier pigeon"}):
            with self.subTest(**data):
                with self.assertRaises(ValueError):
                    sec.create_meeting(self.user, self.private, data)
        with self.assertRaises(ValueError):
            sec.create_meeting(self.user, self.private, "not a dict")
        self.assertIsNone(sec.create_meeting(_util.uid("other"), self.private,
                                             {"kind": "board", "scheduled_for": "2026-10-20T11:00"}))

    def test_items_filtered_by_kind_and_deduplicated(self):
        mid = self.meeting(self.private, items=["leave", "borrow", "leave", "adopt_fs_agm", "made_up", "fin_statements"],
                           when="2026-10-20T11:00")
        m = sec.get_meeting(self.user, mid)
        self.assertEqual([i["key"] for i in m["items"]], ["leave", "borrow", "fin_statements"])
        self.assertEqual(m["title"], "Board meeting, 20 Oct 2026")
        self.assertEqual((m["status"], m["mode"], m["scheduled_for"]), ("planned", "physical", "2026-10-20T11:00"))
        self.assertIsNone(sec.get_meeting(_util.uid("other"), mid))
        listed = sec.list_meetings(self.user, self.private)
        self.assertEqual(listed[0]["notice_by"], "2026-10-13")


class Mgt14(MeetingBase):
    def test_private_is_exempt_from_board_powers_filing(self):
        items = ["fin_statements", "borrow", "allotment", "kmp", "leave"]
        priv = sec.get_meeting(self.user, self.meeting(self.private, items=items))
        pub = sec.get_meeting(self.user, self.meeting(self.public, items=items))
        self.assertEqual(sec.mgt14_items(priv, ents.get_entity(self.user, self.private)), [])
        self.assertEqual(priv["mgt14_keys"], [])
        self.assertEqual(pub["mgt14_keys"], sorted(["fin_statements", "borrow", "allotment", "kmp"]))

    def test_special_resolution_needs_mgt14_for_everyone(self):
        m = sec.get_meeting(self.user, self.meeting(self.private, kind="egm", items=["special_resolution"]))
        self.assertEqual(m["mgt14_keys"], ["special_resolution"])
        self.assertTrue(sec.item_needs_mgt14("special_resolution", {"entity_type": "opc"}))
        self.assertFalse(sec.item_needs_mgt14("borrow", {"entity_type": "opc"}))
        self.assertTrue(sec.item_needs_mgt14("borrow", {"entity_type": "listed"}))
        self.assertFalse(sec.item_needs_mgt14("leave", {"entity_type": "listed"}))

    def test_agenda_library_marks_mgt14_per_entity(self):
        lib = {a["key"]: a for a in sec.agenda_library({"entity_type": "public_unlisted"}, "board")}
        self.assertTrue(lib["borrow"]["mgt14"])
        self.assertNotIn("adopt_fs_agm", lib)
        self.assertEqual(lib["borrow"]["blanks"], ["amount", "lender"])


class NoticeChecks(unittest.TestCase):
    def m(self, kind="board", when="2026-10-20T11:00", status="planned", items=(), held_on="", sched_event_id=None):
        return {"kind": kind, "scheduled_for": when, "status": status, "held_on": held_on,
                "sched_event_id": sched_event_id,
                "items": [{"key": k, "title": sec.AGENDA_BY_KEY[k]["title"],
                           "resolution": sec.AGENDA_BY_KEY[k]["resolution"]} for k in items]}

    def levels(self, checks):
        return [(c["level"], c["text"]) for c in checks]

    def test_notice_deadlines(self):
        self.assertEqual(sec.notice_deadline("board", date(2026, 10, 20)), date(2026, 10, 13))
        # 21 clear days: neither the day of sending nor the meeting day counts.
        self.assertEqual(sec.notice_deadline("agm", date(2026, 10, 30)), date(2026, 10, 8))

    def test_in_time(self):
        [c] = sec.meeting_checks(self.m(), {"entity_type": "private"}, today=date(2026, 10, 1))
        self.assertEqual(c["level"], "ok")
        self.assertIn("13 Oct 2026", c["text"])

    def test_notice_late_warns_with_short_notice_rule(self):
        [c] = sec.meeting_checks(self.m(), {"entity_type": "private"}, today=date(2026, 10, 15))
        self.assertEqual(c["level"], "warn")
        self.assertIn("should have gone out by 13 Oct 2026", c["text"])
        self.assertIn("s.173(3)", c["text"])
        [agm] = sec.meeting_checks(self.m(kind="agm", when="2026-10-30T11:00"), {"entity_type": "private"},
                                   today=date(2026, 10, 9))
        self.assertEqual(agm["level"], "warn")
        self.assertIn("21 clear days", agm["text"])
        self.assertIn("95%", agm["text"])

    def test_meeting_date_passed(self):
        checks = sec.meeting_checks(self.m(), {"entity_type": "private"}, today=date(2026, 10, 21))
        self.assertEqual(checks[0]["level"], "warn")
        self.assertIn("has passed", checks[0]["text"])

    def test_listed_board_gets_reg29_note_and_blanks_warning(self):
        # The note says the Reg 29 reminder "is on the calendar", so it shows
        # only when create_meeting actually logged one (sched_event_id).
        checks = sec.meeting_checks(self.m(items=["borrow"], sched_event_id=1), {"entity_type": "listed"},
                                    today=date(2026, 10, 1))
        texts = " | ".join(c["text"] for c in checks)
        self.assertIn("Reg 29", texts)
        self.assertIn("Fill before circulating: amount, lender.", texts)
        self.assertIn("1 item will need MGT-14", texts)
        no_reminder = sec.meeting_checks(self.m(items=["borrow"]), {"entity_type": "listed"}, today=date(2026, 10, 1))
        self.assertNotIn("Reg 29", " | ".join(c["text"] for c in no_reminder))

    def test_held_meeting_shows_mgt14_due_date(self):
        checks = sec.meeting_checks(self.m(status="held", held_on="2026-10-20", items=["borrow"]),
                                    {"entity_type": "public_unlisted"}, today=date(2026, 10, 25))
        texts = [c["text"] for c in checks]
        self.assertTrue(texts[0].startswith("Held on 20 Oct 2026"))
        self.assertIn("MGT-14 is due by 19 Nov 2026 for: Approve borrowings (s.179(3)(d)).", texts)


class MarkHeld(MeetingBase):
    def test_board_meeting_creates_follow_ups(self):
        mid = self.meeting(self.public, items=["fin_statements", "additional_director", "allotment", "leave"],
                           when=(TODAY - timedelta(days=1)).isoformat() + "T11:00")
        held_on = (TODAY - timedelta(days=1)).isoformat()
        res = sec.mark_held(self.user, mid, held_on)
        self.assertEqual(res["held_on"], held_on)
        self.assertEqual(sorted(res["created"]), sorted(["IN-MCA-MINUTES", "IN-MCA-MGT14", "IN-MCA-DIR12", "IN-MCA-PAS3"]))
        due = {o["rule_id"]: o["due_date"] for o in obligations(self.public, origin="event")}
        d = date.fromisoformat(held_on)
        self.assertEqual(due, {"IN-MCA-MINUTES": (d + timedelta(days=30)).isoformat(),
                               "IN-MCA-MGT14": (d + timedelta(days=30)).isoformat(),
                               "IN-MCA-DIR12": (d + timedelta(days=30)).isoformat(),
                               "IN-MCA-PAS3": (d + timedelta(days=15)).isoformat()})
        self.assertEqual(len(res["followups"]), 4)
        m = sec.get_meeting(self.user, mid)
        self.assertEqual(m["status"], "held")
        self.assertEqual(len(m["followups"]["events"]), 4)
        self.assertEqual(len(m["followup_items"]), 4)
        with self.assertRaises(ValueError):
            sec.mark_held(self.user, mid, held_on)
        with self.assertRaises(ValueError):
            sec.delete_meeting(self.user, mid)

    def test_private_agm_gets_minutes_task_and_mgt14_for_special_resolution(self):
        mid = self.meeting(self.private, kind="agm", items=["adopt_fs_agm", "special_resolution"],
                           when=(TODAY - timedelta(days=2)).isoformat() + "T11:00")
        res = sec.mark_held(self.user, mid, "")
        self.assertEqual(res["held_on"], TODAY.isoformat())
        self.assertEqual(res["created"], ["IN-MCA-MGT14"])
        [task] = obligations(self.private, origin="custom")
        self.assertEqual((task["title"], task["due_date"]),
                         ("Sign minutes of the general meeting", (TODAY + timedelta(days=30)).isoformat()))
        self.assertEqual(sec.get_meeting(self.user, mid)["followups"]["tasks"], [task["id"]])

    def test_validation(self):
        mid = self.meeting(self.private)
        with self.assertRaises(ValueError):
            sec.mark_held(self.user, mid, (TODAY + timedelta(days=1)).isoformat())
        with self.assertRaises(ValueError):
            sec.mark_held(self.user, mid, "last Tuesday")
        self.assertIsNone(sec.mark_held(_util.uid("other"), mid, TODAY.isoformat()))
        self.assertEqual(sec.get_meeting(self.user, mid)["status"], "planned")


class ListedBoardReg29(MeetingBase):
    # create_meeting only logs the Reg 29 reminder for a meeting dated today or
    # later, so the meeting date must move with the calendar.
    DAY = TODAY + timedelta(days=30)
    WHEN = DAY.isoformat() + "T15:00"

    def test_create_puts_reg29_on_calendar_and_delete_removes_it(self):
        listed = _util.make_entity(self.user, name="Acme Listed Limited", entity_type="listed", tracking_from="2099-01-01")
        mid = self.meeting(listed, when=self.WHEN)
        [ob] = obligations(listed, rule_id="IN-SEBI-REG29")
        self.assertEqual(ob["due_date"], rb.add_working_days(self.DAY, -3).isoformat())
        self.assertEqual(sec.delete_meeting(self.user, mid), listed)
        self.assertEqual(obligations(listed, rule_id="IN-SEBI-REG29"), [])
        self.assertEqual(ents.list_events(self.user, listed), [])
        self.assertIsNone(sec.get_meeting(self.user, mid))

    def test_backdated_meeting_gets_no_reminder(self):
        listed = _util.make_entity(self.user, name="Acme Listed Limited", entity_type="listed", tracking_from="2099-01-01")
        self.meeting(listed, when=(TODAY - timedelta(days=3)).isoformat() + "T15:00")
        self.assertEqual(obligations(listed, rule_id="IN-SEBI-REG29"), [])

    def test_filed_reg29_survives_delete(self):
        listed = _util.make_entity(self.user, name="Acme Listed Limited", entity_type="listed", tracking_from="2099-01-01")
        mid = self.meeting(listed, when=self.WHEN)
        [ob] = obligations(listed, rule_id="IN-SEBI-REG29")
        ents.update_obligation(self.user, ob["id"], {"status": "filed", "filed_on": TODAY.isoformat()})
        sec.delete_meeting(self.user, mid)
        self.assertEqual(len(obligations(listed, rule_id="IN-SEBI-REG29")), 1)
        self.assertEqual(len(ents.list_events(self.user, listed)), 1)

    def test_other_user_cannot_delete(self):
        mid = self.meeting(self.private)
        self.assertFalse(sec.delete_meeting(_util.uid("other"), mid))


class Docx(MeetingBase):
    def text_of(self, buf):
        d = docx.Document(buf)
        return [p.text for p in d.paragraphs], d

    def test_board_pack_is_a_valid_docx(self):
        ents.add_person(self.user, self.public, {"name": "Asha Rao", "din": "01234567", "designation": "Director"})
        ents.add_person(self.user, self.public, {"name": "Ravi Iyer", "designation": "Company Secretary"})
        mid = self.meeting(self.public, items=["leave", "borrow"], when="2026-10-20T11:00", venue="Registered office",
                           chair="Asha Rao")
        buf, name = sec.build_docx(self.user, mid)
        self.assertEqual(name, "Acme_Public_Limited_board_2026-10-20.docx")
        raw = buf.getvalue()
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(raw)))
        paras, d = self.text_of(io.BytesIO(raw))
        body = "\n".join(paras)
        self.assertIn("ACME PUBLIC LIMITED", body)
        self.assertIn("CIN: U12345MH2010PLC123456 · Maharashtra", body)
        self.assertIn("Notice of Board meeting", body)
        self.assertIn("Tuesday, 20 October 2026 at 11:00 a.m. at Registered office", body)
        self.assertIn("1. Leave of absence", body)
        self.assertIn("2. Approve borrowings (s.179(3)(d))", body)
        self.assertIn("File MGT-14 within 30 days of passing this resolution.", body)
        self.assertIn("Asha Rao (DIN 01234567), Director: present / leave of absence", body)
        self.assertIn("In attendance: Ravi Iyer (Company Secretary)", body)
        self.assertIn("Chairperson: Asha Rao", body)
        # Unfilled blanks keep their braces, bold and highlighted.
        blanks = [r for p in d.paragraphs for r in p.runs if r.text == "{amount}"]
        self.assertTrue(blanks)
        self.assertTrue(all(r.bold and r.font.highlight_color is not None for r in blanks))

    def test_general_meeting_pack_has_explanatory_statement(self):
        mid = self.meeting(self.private, kind="egm", items=["special_resolution"], when="2026-11-02T10:30", mode="vc")
        buf, name = sec.build_docx(self.user, mid)
        paras, _ = self.text_of(buf)
        body = "\n".join(paras)
        self.assertIn("Special business", body)
        self.assertIn("Explanatory statement under section 102", body)
        self.assertIn("Special resolution", body)
        self.assertIn("through video conferencing", body)
        self.assertNotIn("appoint a proxy", body)  # no proxies at a VC meeting
        self.assertTrue(name.endswith("_egm_2026-11-02.docx"))

    def test_other_user_gets_nothing(self):
        mid = self.meeting(self.private)
        self.assertIsNone(sec.build_docx(_util.uid("other"), mid))


if __name__ == "__main__":
    unittest.main()
