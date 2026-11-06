"""Draft desk: memory, house-style learning, AI fill guards, the export
check, the Gemini client's failure handling and the /api/drafting routes.
No test reaches Gemini or the network: draft_ai.generate is mocked."""
from tests import _util  # noqa: F401  (must come first)

import os
import unittest
from datetime import date
from unittest import mock

import draft_ai
import draft_assist
import draft_check
import draft_memory as dm
import draft_requirements
from draft_ai import AIError

with _util.quiet():
    dm.init_memory_db()

# Three letters in one company's format, with the spacing damage PDF
# extraction really produces ("faithfull y", "202 6").
LETTERS = [
    """21st August, 202 6
Stock Code: BSE: 500696
NSE: ACMECO
ISIN: INE030A01027
BSE Limited
Corporate Relationship Department
Phiroze Jeejeebhoy Towers, Dalal Street
Mumbai – 400 001
National Stock Exchange of India Limited
Exchange Plaza, Bandra Kurla Complex
Bandra (E), Mumbai – 400 051
Dear Sir / Madam,
Sub.: Disclosure under Regulation 30 of the SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015
Pursuant to Regulation 30 of the SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015, we hereby enclose a copy of the newspaper advertisement.
Please take the above information on record.
Thanking You.
Yours faithfull y,
For Acme Consumer Limited
Radhika Shah
Company Secretary & Compliance Officer
Membership No: A19308
Registered Office: Acme House, 8 Marine Drive, Mumbai 400 020 Tel: +91 22 5555 0101 CIN: L15140MH1933PLC002030 www.acme.example""",
    """4th August, 2026
Stock Code: BSE: 500696
NSE: ACMECO
BSE Limited
Corporate Relationship Department
Phiroze Jeejeebhoy Towers, Dalal Street
Mumbai – 400 001
National Stock Exchange of India Limited
Exchange Plaza, Bandra Kurla Complex
Bandra (E), Mumbai – 400 051
Dear Sir / Madam,
Sub.: Outcome of the Board Meeting
Pursuant to Regulation 30 of the SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015, we wish to inform you that the Board approved the results.
Please take the above information on record.
Thanking You.
Yours faithfully,
For Acme Consumer Limited
Radhika Shah
Company Secretary & Compliance Officer
Membership No: A19308
Registered Office: Acme House, 8 Marine Drive, Mumbai 400 020 Tel: +91 22 5555 0101 CIN: L15140MH1933PLC002030 www.acme.example""",
    """12th July, 2026
Stock Code: BSE: 500696
BSE Limited
Phiroze Jeejeebhoy Towers, Dalal Street
Mumbai – 400 001
Dear Sir / Madam,
Sub.: Intimation of Record Date
Pursuant to Regulation 42 of the SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015, the record date is fixed.
Please take the above information on record.
Thanking You.
Yours faithfully,
For Acme Consumer Limited
Radhika Shah
Company Secretary & Compliance Officer""",
]
CORPUS = [{"text": t, "title": f"Letter {i}", "date": "", "source": "filing", "ref": str(i)} for i, t in enumerate(LETTERS)]


def learned(owner, symbol="ACMECO"):
    with _util.quiet():
        return dm.learn(owner, symbol, company_name="Acme Consumer Limited", corpus=CORPUS)


class Validators(unittest.TestCase):
    def test_isin_check_digit(self):
        self.assertTrue(dm.isin_valid("INE030A01027"))
        self.assertTrue(dm.isin_valid("US0378331005"))
        self.assertFalse(dm.isin_valid("INE030A01028"))
        self.assertFalse(dm.isin_valid("INE030A0102"))

    def test_cin_format(self):
        self.assertTrue(dm.cin_valid("L15140MH1933PLC002030"))
        self.assertFalse(dm.cin_valid("L15140MH1933PLC00203"))
        self.assertFalse(dm.cin_valid("X15140MH1933PLC002030"))

    def test_house_date_styles(self):
        d = date(2026, 11, 21)
        self.assertEqual(dm.format_date(d, "ordinal_comma"), "21st November, 2026")
        self.assertEqual(dm.format_date(date(2026, 11, 12), "ordinal"), "12th November 2026")
        self.assertEqual(dm.format_date(d, "mdy"), "November 21, 2026")
        self.assertEqual(dm.format_date(d, "numeric_dot"), "21.11.2026")
        self.assertEqual(dm.classify_date("21st August, 2026"), "ordinal_comma")
        self.assertEqual(dm.classify_date("August 21, 2026"), "mdy")


class HouseStyleLearning(unittest.TestCase):
    def setUp(self):
        self.owner = _util.uid()
        self.mem = learned(self.owner)
        self.fmt = {k: v["value"] for k, v in self.mem["format"].items()}

    def test_format_is_learned_from_the_company_letters(self):
        self.assertEqual(self.fmt["date_format"], "ordinal_comma")
        self.assertEqual(self.fmt["subject_label"], "Sub.:")
        self.assertEqual(self.fmt["salutation"], "Dear Sir / Madam,")
        self.assertEqual(self.fmt["request_line"], "Please take the above information on record.")
        self.assertEqual(self.fmt["thanks_line"], "Thanking You.")
        self.assertEqual(self.fmt["for_line"], "For Acme Consumer Limited")
        self.assertEqual(self.fmt["sig_name"], "Radhika Shah")
        self.assertEqual(self.fmt["exchange_order"], "bse_first")
        self.assertEqual(self.mem["format"]["date_format"]["count"], 3)

    def test_broken_pdf_spacing_does_not_win(self):
        self.assertEqual(self.fmt["close_phrase"], "Yours faithfully,")

    def test_addressee_block_excludes_the_signatory(self):
        self.assertEqual(self.fmt["bse_block"][:2], ["BSE Limited", "Corporate Relationship Department"])
        self.assertTrue(self.fmt["bse_block"][-1].endswith("400 001"))
        self.assertNotIn("Company Secretary", " ".join(self.fmt["bse_block"]))

    def test_facts_are_voted_and_validated(self):
        facts = {f["key"]: f["value"] for f in self.mem["facts"]}
        self.assertEqual(facts["cin"], "L15140MH1933PLC002030")
        self.assertEqual(facts["isin"], "INE030A01027")
        self.assertEqual(facts["bse_code"], "500696")
        self.assertEqual(facts["legal_name"], "Acme Consumer Limited")
        self.assertEqual(facts["cs_name"], "Radhika Shah")
        self.assertIn("400 020", facts["registered_office"])

    def test_user_facts_beat_learned_facts(self):
        dm.save_facts(self.owner, "ACMECO", {"cs_name": "Asha Rao"})
        learned(self.owner)
        facts = {f["key"]: f for f in dm.get_memory(self.owner, "ACMECO")["facts"]}
        self.assertEqual(facts["cs_name"]["value"], "Asha Rao")
        self.assertEqual(facts["cs_name"]["source"], "you")

    def test_memory_is_per_account(self):
        other = _util.uid("other")
        self.assertEqual(dm.get_memory(other, "ACMECO")["format"], {})

    def test_prompt_context_carries_rules_format_and_facts(self):
        dm.set_instructions(self.owner, "", "Never write 'we are pleased to inform'.")
        dm.add_instruction(self.owner, "ACMECO", "Refer to the Company as 'the Company'.")
        ctx = dm.prompt_context(self.owner, "ACMECO", "board_change")
        self.assertIn("we are pleased", ctx["rules"])
        self.assertIn("Refer to the Company", ctx["rules"])
        self.assertIn('"21st August, 2026"', ctx["format"])
        self.assertIn("Please take the above information on record.", ctx["format"])
        self.assertEqual(ctx["facts_dict"]["cin"], "L15140MH1933PLC002030")


class Documents(unittest.TestCase):
    def setUp(self):
        self.owner = _util.uid()

    def test_add_list_delete_and_isolation(self):
        doc = dm.add_doc(self.owner, "ACMECO", "Ref", "Pursuant to Regulation 30 we enclose the outcome. " * 3, "paste")
        self.assertEqual(len(dm.list_docs(self.owner, "ACMECO")), 1)
        self.assertEqual(dm.list_docs(_util.uid("x"), "ACMECO"), [])
        self.assertFalse(dm.delete_doc(_util.uid("x"), doc))
        self.assertTrue(dm.delete_doc(self.owner, doc))

    def test_short_or_unknown_documents_are_refused(self):
        with self.assertRaises(ValueError):
            dm.add_doc(self.owner, "ACMECO", "Ref", "too short", "paste")
        with self.assertRaises(ValueError):
            dm.add_doc(self.owner, "ACMECO", "Ref", "x" * 100, "email")

    def test_exported_draft_becomes_an_example_once(self):
        body = "Pursuant to Regulation 30, the Board appointed Mr. A as a director with effect from 1 May 2026."
        a = dm.record_approved(self.owner, "ACMECO", "board_change", "Appointment", body, draft_id=7)
        b = dm.record_approved(self.owner, "ACMECO", "board_change", "Appointment", body + " Updated.", draft_id=7)
        self.assertEqual(a, b)
        self.assertEqual(len(dm.list_docs(self.owner, "ACMECO")), 1)

    def test_a_draft_with_gaps_is_not_an_example(self):
        body = "The Board appointed [PLEASE FILL — name] as a director with effect from 1 May 2026, as required."
        self.assertIsNone(dm.record_approved(self.owner, "ACMECO", "board_change", "x", body))

    def test_text_upload_and_unknown_type(self):
        self.assertIn("Regulation 30", dm.extract_upload_text("a.txt", b"Pursuant to Regulation 30 of the LODR."))
        with self.assertRaises(ValueError):
            dm.extract_upload_text("a.exe", b"MZ\x00\x00")


def _payload(**kw):
    p = {"symbol": "ACMECO", "filing_type": "board_change", "company_name": "Acme Consumer Limited",
         "cin": "L15140MH1933PLC002030", "registered_office": "Acme House, 8 Marine Drive, Mumbai 400 020",
         "bse_code": "500696", "nse_symbol": "ACMECO", "signer_name": "Radhika Shah",
         "signer_designation": "Company Secretary", "subject": "Appointment of a director",
         "body": ("Pursuant to Regulation 30 of the SEBI (Listing Obligations and Disclosure Requirements) "
                  "Regulations, 2015, the Board appointed Mr. Arun Mehta (DIN: 01234567) with effect from "
                  "14th November, 2026 for a term of five years.\n\nPlease take the above information on record."),
         "annexures": [], "new_info": ""}
    p.update(kw)
    return p


class RuleChecks(unittest.TestCase):
    def setUp(self):
        self.owner = _util.uid()
        learned(self.owner)

    def check(self, **kw):
        return draft_check.run_check(_payload(**kw), self.owner, api_key=None, use_ai=False)

    def titles(self, report, severity=None):
        return [f["title"] for f in report["findings"] if severity is None or f["severity"] == severity]

    def test_a_clean_letter_has_no_blockers(self):
        r = self.check()
        self.assertEqual(self.titles(r, "blocker"), [])
        self.assertFalse(r["summary"]["ai_used"])

    def test_wrong_particulars_are_blockers_with_fixes(self):
        r = self.check(cin="L15140MH1933PLC002031", registered_office="Acme House, 8 Marine Drive, Mumbai 400 021")
        blockers = {f["title"]: f for f in r["findings"] if f["severity"] == "blocker"}
        self.assertIn("CIN does not match the company's records", blockers)
        self.assertEqual(blockers["CIN does not match the company's records"]["fix"]["replace"], "L15140MH1933PLC002030")
        self.assertIn("Registered office PIN code differs", blockers)

    def test_dates_weekdays_and_dins(self):
        body = ("Pursuant to Regulation 30, the Board met on Friday, 14 November 2026 and on 31 June 2026 "
                "appointed Mr. A (DIN 0123456).")
        r = self.check(body=body)
        t = self.titles(r, "blocker")
        self.assertIn("Weekday does not match the date", t)       # 14 Nov 2026 is a Saturday
        self.assertIn("Date does not exist", t)
        self.assertIn("DIN should have 8 digits", t)
        fix = next(f for f in r["findings"] if f["title"] == "Weekday does not match the date")["fix"]
        self.assertEqual(fix["replace"], "Saturday, 14 November 2026")

    def test_placeholders_missing_annexures_and_spelling(self):
        body = ("Pursuant to Regulation 30, the appointmnet of [PLEASE FILL — name] is enclosed as Annexure II.\n\n"
                "Yours faithfully,")
        r = self.check(body=body, subject="Under the SEBI (Listing Obligation and Disclosure Requirement) Regulations")
        t = self.titles(r)
        self.assertTrue(any("placeholder" in x for x in t))
        self.assertIn("Body refers to an annexure that is not attached", t)
        self.assertIn("Spelling: “appointmnet”", t)
        self.assertIn("Name of the regulations is misspelt", t)
        self.assertIn("Sign-off typed in the body", t)

    def test_citation_for_the_filing_type(self):
        r = self.check(filing_type="record_date", body="The record date is fixed as 21st November, 2026.")
        self.assertIn("Regulation 42 is not cited", self.titles(r))

    def test_house_style_dates(self):
        r = self.check(body="Pursuant to Regulation 30, effective November 14, 2026.\n\nPlease take the above information on record.")
        f = next(f for f in r["findings"] if f["title"] == "Dates differ from the company's house style")
        self.assertEqual(f["fix"]["replace"], "14th November, 2026")

    def test_ai_findings_must_quote_the_draft(self):
        fake = {"data": {"summary": "ok", "requirements": [
            {"id": "reason_for_change", "status": "met", "evidence": "the Board appointed Mr. Arun Mehta", "note": ""},
            {"id": "brief_profile", "status": "met", "evidence": "a text that is not in the draft", "note": ""}],
            "findings": [
                {"category": "spelling", "severity": "warning", "quote": "five years", "issue": "Term differs from brief", "replacement": "three years"},
                {"category": "accuracy", "severity": "blocker", "quote": "invented sentence", "issue": "Made up", "replacement": ""}]},
            "model": "test-model"}
        with mock.patch.object(draft_check, "_ai_check", return_value=fake):
            r = draft_check.run_check(_payload(), self.owner, api_key="k", use_ai=True)
        t = self.titles(r)
        self.assertIn("Term differs from brief", t)
        self.assertNotIn("Made up", t)
        req = {x["id"]: x["status"] for x in r["requirements"]}
        self.assertEqual(req["reason_for_change"], "met")
        self.assertEqual(req["brief_profile"], "unclear")

    def test_ai_failure_keeps_the_rule_checks(self):
        with mock.patch.object(draft_check, "_ai_check", side_effect=AIError("quota", "used up", 60)):
            r = draft_check.run_check(_payload(cin="L15140MH1933PLC002031"), self.owner, api_key="k", use_ai=True)
        self.assertEqual(r["summary"]["ai_error"]["kind"], "quota")
        self.assertIn("CIN does not match the company's records", self.titles(r))


class Requirements(unittest.TestCase):
    def test_verified_lists_and_aliases(self):
        r = draft_requirements.for_type("board_change")
        self.assertFalse(r["derived"])
        ids = {i["id"] for i in r["items"]}
        self.assertTrue({"reason_for_change", "date_and_term", "brief_profile", "director_relationships"} <= ids)
        self.assertEqual(draft_requirements.for_type("book_closure")["items"],
                         draft_requirements.for_type("record_date")["items"])
        self.assertIn("three working days", " ".join(draft_requirements.for_type("record_date")["notes"]))

    def test_unknown_types_are_flagged_as_derived(self):
        self.assertTrue(draft_requirements.for_type("brsr")["derived"])

    def test_every_hint_compiles(self):
        import re
        for key, r in draft_requirements.REQUIREMENTS.items():
            for item in r["items"]:
                if item.get("hint"):
                    re.compile(item["hint"])


class FillGuards(unittest.TestCase):
    def setUp(self):
        self.owner = _util.uid()
        learned(self.owner)
        self.doc = "Registered office: ⟦F1⟧. The Board appointed ⟦F2⟧ on ⟦F3⟧ with a consideration of ⟦F4⟧."
        self.fields = [{"id": "F1", "label": "Registered office address", "context": ""},
                       {"id": "F2", "label": "Name of the new director", "context": ""},
                       {"id": "F3", "label": "Date of appointment", "context": ""},
                       {"id": "F4", "label": "Consideration", "context": ""}]
        self.brief = "The Board appointed Mr. Arun Mehta on 14 November 2026."

    def test_company_particulars_fill_without_ai(self):
        out = draft_assist.fill_fields(None, self.owner, "ACMECO", "Acme", "board_change", self.brief, self.doc, self.fields)
        fills = {f["id"]: f for f in out["fills"]}
        self.assertIn("400 020", fills["F1"]["value"])
        self.assertEqual(fills["F1"]["source_label"], "Company profile")
        self.assertEqual(out["error"]["kind"], "no_key")

    def test_untraceable_answers_are_dropped(self):
        answer = {"data": {"fills": [
            {"id": "F2", "value": "Mr. Arun Mehta", "source": "brief", "evidence": "appointed Mr. Arun Mehta"},
            {"id": "F3", "value": "14 November 2026", "source": "brief", "evidence": "on 14 November 2026"},
            {"id": "F4", "value": "₹34.98 billion in cash", "source": "brief", "evidence": "consideration of ₹34.98 billion"}],
            "unfilled": []}, "model": "m"}
        with mock.patch.object(draft_ai, "generate", return_value=answer):
            out = draft_assist.fill_fields("k", self.owner, "ACMECO", "Acme", "board_change", self.brief, self.doc, self.fields)
        fills = {f["id"]: f["value"] for f in out["fills"]}
        self.assertEqual(fills["F2"], "Mr. Arun Mehta")
        self.assertEqual(fills["F3"], "14 November 2026")
        self.assertNotIn("F4", fills)
        self.assertIn("F4", {u["id"] for u in out["unfilled"]})

    def test_provider_failure_still_returns_particulars(self):
        with mock.patch.object(draft_ai, "generate", side_effect=AIError("overloaded", "busy")):
            out = draft_assist.fill_fields("k", self.owner, "ACMECO", "Acme", "board_change", self.brief, self.doc, self.fields)
        self.assertEqual([f["id"] for f in out["fills"]], ["F1"])
        self.assertEqual(out["error"]["kind"], "overloaded")


class _FakeResp:
    def __init__(self, text):
        self.text = text
        self.candidates = []


class GeminiClient(unittest.TestCase):
    def setUp(self):
        draft_ai._cooldown.clear()
        draft_ai._no_thinking.clear()
        self.models = mock.patch.object(draft_ai, "available_models",
                                        return_value=["gemini-3.5-flash", "gemini-3.6-flash", "gemini-flash-lite-latest"])
        self.models.start()

    def tearDown(self):
        self.models.stop()

    def _client_with(self, behaviours):
        calls = []

        def make(api_key, timeout):
            client = mock.Mock()

            def gen(model, contents, config):
                calls.append((model, dict(config)))
                b = behaviours[min(len(calls) - 1, len(behaviours) - 1)]
                if isinstance(b, Exception):
                    raise b
                return _FakeResp(b)
            client.models.generate_content.side_effect = gen
            return client
        return make, calls

    def test_no_key(self):
        with self.assertRaises(AIError) as cm:
            draft_ai.generate("", "x")
        self.assertEqual(cm.exception.kind, "no_key")

    def test_daily_quota_moves_to_the_next_model(self):
        q = Exception("429 RESOURCE_EXHAUSTED. {'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'retryDelay': '41071s'}")
        make, calls = self._client_with([q, '{"a": 1}'])
        with mock.patch.object(draft_ai, "_client", side_effect=make):
            out = draft_ai.generate("k", "x", schema={"type": "object"})
        self.assertEqual(out["model"], "gemini-3.6-flash")
        self.assertEqual(out["data"], {"a": 1})
        self.assertNotIn("gemini-3.5-flash", draft_ai.model_chain("k"))

    def test_all_quota_is_reported_as_quota(self):
        q = Exception("429 RESOURCE_EXHAUSTED. {'quotaId': 'PerDay', 'retryDelay': '3600s'}")
        make, _ = self._client_with([q])
        with mock.patch.object(draft_ai, "_client", side_effect=make):
            with self.assertRaises(AIError) as cm:
                draft_ai.generate("k", "x")
        self.assertEqual(cm.exception.kind, "quota")
        self.assertTrue(cm.exception.retry_after)

    def test_thinking_config_rejection_is_retried_without_it(self):
        make, calls = self._client_with([Exception("400 INVALID_ARGUMENT. bad"), "ok"])
        with mock.patch.object(draft_ai, "_client", side_effect=make):
            out = draft_ai.generate("k", "x", fast=True)
        self.assertEqual(out["text"], "ok")
        self.assertIn("thinking_config", calls[0][1])
        self.assertNotIn("thinking_config", calls[1][1])
        self.assertEqual(calls[0][0], calls[1][0])

    def test_rejected_key_stops_immediately(self):
        make, calls = self._client_with([Exception("400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key.")])
        with mock.patch.object(draft_ai, "_client", side_effect=make):
            with self.assertRaises(AIError) as cm:
                draft_ai.generate("k", "x")
        self.assertEqual(cm.exception.kind, "invalid_key")
        self.assertEqual(len(calls), 1)

    def test_unparseable_json_tries_another_model(self):
        make, _ = self._client_with(["not json", '{"ok": true}'])
        with mock.patch.object(draft_ai, "_client", side_effect=make):
            out = draft_ai.generate("k", "x", schema={"type": "object"})
        self.assertEqual(out["data"], {"ok": True})


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with _util.quiet():
            import main
        cls.main = main
        main.app.config["TESTING"] = True

    def client(self, uid):
        c = self.main.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["user_cache"] = {"id": uid, "email": f"{uid}@example.com", "name": uid, "onboarded": True}
        return c

    def test_memory_round_trip_and_isolation(self):
        a, b = _util.uid(), _util.uid("b")
        ca, cb = self.client(a), self.client(b)
        r = ca.post("/api/drafting/memory/instructions", json={"symbol": "ACMECO", "scope": "company", "append": "Use 'the Company'."})
        self.assertEqual(r.status_code, 200)
        r = ca.post("/api/drafting/memory/facts", json={"symbol": "ACMECO", "facts": {"cin": "L15140MH1933PLC002030"}})
        self.assertEqual(r.status_code, 200)
        r = ca.post("/api/drafting/memory/docs", json={"symbol": "ACMECO", "title": "Ref", "text": "Pursuant to Regulation 30 we enclose the outcome of the meeting held today."})
        self.assertEqual(r.status_code, 200)
        mem = ca.get("/api/drafting/memory?symbol=ACMECO").get_json()["memory"]
        self.assertIn("the Company", mem["company_instructions"])
        self.assertEqual(len(mem["documents"]), 1)
        other = cb.get("/api/drafting/memory?symbol=ACMECO").get_json()["memory"]
        self.assertEqual(other["company_instructions"], "")
        self.assertEqual(other["documents"], [])
        doc_id = mem["documents"][0]["id"]
        self.assertEqual(cb.delete(f"/api/drafting/memory/docs/{doc_id}").status_code, 404)

    def test_signed_out_is_refused(self):
        r = self.main.app.test_client().get("/api/drafting/memory?symbol=ACMECO")
        self.assertIn(r.status_code, (302, 401))

    def test_compose_reports_ai_errors_honestly(self):
        c = self.client(_util.uid())
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(draft_assist, "compose", side_effect=AIError("quota", "used up", 120)), \
                mock.patch("draft_engine.find_past_examples", return_value=[]):
            r = c.post("/api/drafting/compose", json={"symbol": "ACMECO", "filing_type": "board_change", "brief": "x"})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.get_json()["kind"], "quota")

    def test_check_route_runs_without_ai(self):
        c = self.client(_util.uid())
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}), mock.patch("database.get_setting", return_value=""):
            r = c.post("/api/drafting/check", json={**_payload(cin="BAD"), "use_ai": True})
        self.assertEqual(r.status_code, 200)
        rep = r.get_json()["report"]
        self.assertIn("CIN is not in a valid format", [f["title"] for f in rep["findings"]])

    def test_requirements_route(self):
        r = self.client(_util.uid()).get("/api/drafting/requirements/record_date")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["derived"])


if __name__ == "__main__":
    unittest.main()
