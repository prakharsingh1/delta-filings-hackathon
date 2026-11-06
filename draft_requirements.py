"""What each filing must disclose, from the regulator's own text.

The check before export asks a simple question of every draft: does it answer
what SEBI (or the Companies Act) requires for this kind of filing? The lists
below were traced to the primary sources on 6 November 2026:

  LODR   SEBI (LODR) Regulations, 2015, consolidated, amended up to 15 Oct 2026
  MC     LODR Master Circular HO/49/14/14(7)2025-CFD-POD2/I/3762/2026, issued
         11 Jul 2023, last updated 30 Jan 2026 (Annexure 18 carries the
         Reg 30 event details from circular SEBI/HO/CFD/CFD-PoD-1/P/CIR/2023/123)
  PIT    SEBI (PIT) Regulations, 2015, amended up to 12 Mar 2025
  NSE    NSE/CML/2018/02 (20 Jun 2018), director not debarred

Each item says what must be in the text. `kind="timing"` items are deadlines,
shown as reminders rather than checked against the words. Items marked
`verified=False` could only be traced to a secondary reproduction and are
shown as "to confirm", never as failures. `hint` is a regex used to judge
coverage when no AI is available; without one the item needs the AI check.

Re-verify before relying on these after any SEBI amendment. The Industry
Standards Note on Reg 30 (made binding by SEBI/HO/CFD/CFD-PoD-2/P/CIR/2025/25)
was not reviewed and may add detail.
"""
from typing import Any, Dict, List

REVIEWED = "2026-11-06"

_LODR = {"title": "SEBI (LODR) Regulations, 2015 (amended up to 15 Oct 2026)", "date": "2026-10-15",
         "url": "https://www.sebi.gov.in/legal/regulations/oct-2026/securities-and-exchange-board-of-india-listing-obligations-and-disclosure-requirements-regulations-2015-last-amended-on-october-15-2026-_105365.html"}
_MC = {"title": "LODR Master Circular (last updated 30 Jan 2026)", "date": "2026-01-30",
       "url": "https://www.sebi.gov.in/legal/master-circulars/jan-2026/master-circular-for-compliance-with-the-provisions-of-the-securities-and-exchange-board-of-india-listing-obligations-and-disclosure-requirements-regulations-2015-by-listed-entities_99432.html"}
_PIT = {"title": "SEBI (PIT) Regulations, 2015 (amended up to 12 Mar 2025)", "date": "2025-03-12",
        "url": "https://www.sebi.gov.in/legal/regulations/mar-2025/securities-and-exchange-board-of-india-prohibition-of-insider-trading-regulations-2015-last-amended-on-march-12-2025-_92672.html"}
_NSE18 = {"title": "NSE circular NSE/CML/2018/02 (20 Jun 2018)", "date": "2018-06-20",
          "url": "https://nsearchives.nseindia.com/content/debt/NSE_Circular_20062018.pdf"}


def _i(id, label, requirement, ref, hint=None, when=None, kind="text", verified=True):
    return {"id": id, "label": label, "requirement": requirement, "source_ref": ref, "hint": hint,
            "applies_when": when, "kind": kind, "verified": verified}


_OMITTED = _i("missing_item_reasoning", "Reasons for any detail left out",
              "If a specified detail is not disclosed, the disclosure must state the reason.",
              "MC Section V-A, para 4", when="a specified detail is not disclosed")
_LATE = _i("delay_explanation", "Explanation if filed late",
           "A disclosure made after the Reg 30(6) timeline must explain the delay.",
           "LODR Reg 30(6), last proviso", hint=r"delay", when="filed after the deadline")
_BOARD_TIMING = ("Within 30 minutes of the close of the board meeting; 3 hours if it closes after trading "
                 "hours but more than 3 hours before the next trading day opens.")

REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "board_change": {"sources": [_LODR, _MC, _NSE18], "items": [
        _i("reason_for_change", "Reason for the change",
           "Reason for change: appointment, re-appointment, resignation, removal, death or otherwise.",
           "MC Annexure 18, A(7.1)", hint=r"appoint|resign|cessation|re-?appoint|retire|death|remov|vacat"),
        _i("date_and_term", "Effective date and term",
           "Date of appointment / re-appointment / cessation, and the term of appointment or re-appointment.",
           "MC Annexure 18, A(7.2)", hint=r"with effect from|w\.?e\.?f|effective|term of|period of|for a term"),
        _i("brief_profile", "Brief profile",
           "Brief profile of the person.", "MC Annexure 18, A(7.3)",
           hint=r"profile|experience|qualif|graduate|career", when="appointment"),
        _i("director_relationships", "Relationships between directors",
           "Disclosure of relationships between directors.", "MC Annexure 18, A(7.4)",
           hint=r"not related|related to|relationship|inter[- ]?se", when="appointment of a director"),
        _i("not_debarred_affirmation", "Not debarred by a SEBI order",
           "Confirmation that the director being appointed is not debarred from holding office by any SEBI "
           "order or other authority.", "NSE/CML/2018/02, para 2", hint=r"debarred",
           when="appointment of a director"),
        _i("id_resignation_letter", "Resignation letter with detailed reasons",
           "The independent director's resignation letter with the detailed reasons given by the director, "
           "within seven days of the resignation.", "LODR Sch III Pt A Para A(7B)(i)",
           hint=r"letter of resignation|resignation letter", when="resignation of an independent director"),
        _i("id_other_directorships", "Resigning director's other listed directorships",
           "Names of listed entities where the resigning director holds directorships, with category and "
           "committee memberships.", "LODR Sch III Pt A Para A(7B)(ia)",
           hint=r"directorships?|other listed", when="resignation of an independent director"),
        _i("id_no_other_reason", "Confirmation of no other material reason",
           "The independent director's confirmation that there is no material reason other than those given.",
           "LODR Sch III Pt A Para A(7B)(ii)", hint=r"no other material reason",
           when="resignation of an independent director"),
        _i("other_resignation_letter", "Resignation letter with reasons",
           "Resignation letter with detailed reasons from the KMP, senior management, Compliance Officer or "
           "non-independent director, within seven days of the resignation taking effect.",
           "LODR Sch III Pt A Para A(7C)", hint=r"letter of resignation|resignation letter|reason",
           when="resignation of KMP, senior management, compliance officer or non-independent director"),
        _OMITTED, _LATE,
    ], "notes": ["Due within 12 hours; 24 hours for a resignation. If the change comes from a board decision: "
                 + _BOARD_TIMING]},

    "outcome_board_meeting": {"sources": [_LODR, _MC], "items": [
        _i("meeting_start_end_time", "Time the meeting started and ended",
           "The outcome must state the time of commencement and conclusion of the meeting.",
           "MC Annexure 18, A(4), closing para",
           hint=r"commenced at|concluded at|commencement|conclusion|started at|ended at"),
        _i("dividend_decision", "Dividend and its payment date",
           "Dividend recommended, declared or passed, and the date it will be paid or dispatched.",
           "LODR Sch III Pt A Para A(4)(a)", hint=r"dividend", when="a dividend was considered"),
        _i("buyback_decision", "Buyback decision", "The decision on buyback of securities.",
           "LODR Sch III Pt A Para A(4)(c)", hint=r"buy-?back", when="a buyback was considered"),
        _i("fund_raising_decision", "Fund raising and its method",
           "The fund raising decided, with the method: FPO, rights, ADR/GDR/FCCB, QIP, debt, preferential issue "
           "or other.", "LODR Sch III Pt A Para A(4)(d)",
           hint=r"fund rais|qualified institutions|preferential|rights issue|debentures|QIP",
           when="fund raising was considered"),
        _i("bonus_decision", "Bonus issue and credit date",
           "Bonus issue by capitalisation, with the date the shares will be credited or dispatched.",
           "LODR Sch III Pt A Para A(4)(e)", hint=r"bonus", when="a bonus issue was considered"),
        _i("financial_results", "Financial results", "The financial results approved.",
           "LODR Sch III Pt A Para A(4)(h)", hint=r"financial results|results", when="results were considered"),
        _i("other_decisions", "Other capital decisions",
           "Reissue of forfeited shares, new securities or rights, other alterations of capital, or voluntary "
           "delisting, where decided.", "LODR Sch III Pt A Para A(4)(f), (g), (i)",
           when="any of these was decided"),
        _OMITTED, _LATE,
    ], "notes": [_BOARD_TIMING]},

    "reg29_board_meeting": {"sources": [_LODR], "items": [
        _i("meeting_date", "Date of the board meeting", "The intimation must mention the date of the meeting.",
           "LODR Reg 29(2)", hint=r"meeting.{0,80}(held|scheduled|on)\b"),
        _i("proposals_listed", "What the board will consider",
           "The proposal(s) due to be considered: results, buyback, delisting, fund raising and issue price, "
           "dividend, bonus, alteration of securities or rights, or change in interest or redemption dates.",
           "LODR Reg 29(1)(a)-(h)",
           hint=r"consider|approve|financial results|dividend|buy-?back|bonus|fund rais"),
        _i("gm_for_fund_raising", "General meeting for fund raising",
           "Any AGM, EGM or postal ballot proposed to approve further fund raising, with the type of issuance.",
           "LODR Reg 29(1)(d), first proviso", when="shareholder approval for fund raising"),
    ], "notes": ["At least two working days before the meeting, excluding the date of intimation and the date "
                 "of the meeting. This applies to every item, financial results included (LODR Reg 29(1), "
                 "amended 17 May 2024)."]},

    "dividend": {"sources": [_LODR], "items": [
        _i("dividend_per_share", "Dividend stated per share",
           "The dividend must be declared and disclosed on a per-share basis only.", "LODR Reg 43(1)",
           hint=r"per (equity )?share"),
        _i("dividend_and_payment_date", "Dividend and payment date",
           "Dividend recommended, declared or passed, and the date it will be paid or dispatched.",
           "LODR Sch III Pt A Para A(4)(a)", hint=r"paid|payment|dispatch|credited"),
        _i("cancellation_reasons", "Reasons for cancellation", "Any cancellation of dividend, with reasons.",
           "LODR Sch III Pt A Para A(4)(b)", hint=r"cancel", when="a dividend was cancelled"),
    ], "notes": ["Board decisions: " + _BOARD_TIMING,
                 "The board meeting needs Reg 29 prior intimation, two working days ahead."]},

    "record_date": {"sources": [_LODR], "items": [
        _i("record_date_stated", "The record date", "The record date itself.", "LODR Reg 42(1)",
           hint=r"record date"),
        _i("purpose_stated", "Purpose of the record date",
           "The purpose: dividend, rights or bonus, conversion, rights attached to convertibles, a corporate "
           "action such as a merger, demerger or split, or another purpose.", "LODR Reg 42(1)(a)-(f), 42(2)",
           hint=r"purpose|for the purpose|entitle|dividend|bonus|rights|split"),
    ], "notes": ["Notice at least three working days in advance, excluding the date of intimation and the record "
                 "date (LODR Reg 42(2), 'three' replaced 'seven' on 13 Dec 2024). Seven working days for "
                 "corporate actions under a scheme of arrangement.",
                 "At least five working days between two record dates (Reg 42(4))."]},

    "acquisition_disclosure": {"sources": [_MC, _LODR], "items": [
        _i("target_name_size", "Target: name, size, turnover", "Name of the target entity and details such as "
           "size and turnover.", "MC Annexure 18, A(1.1)(a)", hint=r"turnover|size"),
        _i("related_party", "Related party and promoter interest",
           "Whether it is a related party transaction, whether the promoter group has any interest, the nature "
           "of that interest and whether it is at arm's length.", "MC Annexure 18, A(1.1)(b)",
           hint=r"related party|arm'?s length|promoter"),
        _i("industry", "Target's industry", "Industry the target belongs to.", "MC Annexure 18, A(1.1)(c)",
           hint=r"industry|sector"),
        _i("objects_impact", "Objects and impact", "Objects and impact of the acquisition, with reasons if "
           "outside the main line of business.", "MC Annexure 18, A(1.1)(d)", hint=r"object|rationale|impact"),
        _i("approvals", "Approvals required", "Governmental or regulatory approvals required.",
           "MC Annexure 18, A(1.1)(e)", hint=r"approval"),
        _i("time_period", "Time to complete", "Indicative time period for completion.",
           "MC Annexure 18, A(1.1)(f)", hint=r"complet|time ?period|timeline|by \w+ \d{4}"),
        _i("consideration", "Nature of consideration", "Consideration: cash, share swap or other, with details.",
           "MC Annexure 18, A(1.1)(g)", hint=r"consideration|cash|share swap"),
        _i("cost", "Cost or price per share", "Cost of acquisition or the price at which the shares are acquired.",
           "MC Annexure 18, A(1.1)(h)", hint=r"cost of acquisition|price|₹|rs\.?|inr|crore|lakh"),
        _i("shareholding_pct", "Stake acquired", "Percentage of shareholding or control acquired, or the number "
           "of shares acquired.", "MC Annexure 18, A(1.1)(i)", hint=r"\d+(\.\d+)?\s*%|per ?cent|shares"),
        _i("background", "Target background", "Products or business, date of incorporation, last three years' "
           "turnover, countries of presence, other significant information.", "MC Annexure 18, A(1.1)(j)",
           hint=r"incorporat|background|line of business"),
        _OMITTED, _LATE,
    ], "notes": ["Within 12 hours. If it flows from a board decision: " + _BOARD_TIMING]},

    "divestment_disclosure": {"sources": [_MC, _LODR], "items": [
        _i("contribution", "Turnover and net worth contributed", "Amount and percentage of turnover or income "
           "and net worth contributed by the unit or subsidiary in the last financial year.",
           "MC Annexure 18, A(1.4)(a)", hint=r"turnover|net worth|revenue"),
        _i("agreement_date", "Date of the sale agreement", "Date the agreement for sale was entered into.",
           "MC Annexure 18, A(1.4)(b)", hint=r"agreement"),
        _i("completion_date", "Expected completion date", "Expected date of completion of the sale.",
           "MC Annexure 18, A(1.4)(c)", hint=r"complet"),
        _i("consideration", "Consideration received", "Consideration received from the sale.",
           "MC Annexure 18, A(1.4)(d)", hint=r"consideration"),
        _i("buyers", "Buyers and promoter-group link", "Brief details of the buyers and whether any belongs to "
           "the promoter group.", "MC Annexure 18, A(1.4)(e)", hint=r"buyer|purchaser|acquirer"),
        _i("related_party", "Related party and arm's length", "Whether it is a related party transaction and, "
           "if so, whether at arm's length.", "MC Annexure 18, A(1.4)(f)", hint=r"related party|arm'?s length"),
        _i("outside_scheme", "Outside a scheme; Reg 37A", "Whether the sale is outside a scheme of arrangement "
           "and, if so, compliance with Reg 37A.", "MC Annexure 18, A(1.4)(g)", hint=r"scheme|37A"),
        _OMITTED, _LATE,
    ], "notes": ["Within 12 hours. If it flows from a board decision: " + _BOARD_TIMING]},

    "credit_rating": {"sources": [_MC, _LODR], "items": [
        _i("rating_details", "New or revised rating", "Details of the new or revised rating, the instrument or "
           "programme it covers and the credit rating agency.", "MC Annexure 18, A(3)",
           hint=r"rating|CRISIL|ICRA|CARE|India Ratings|Acuit|Brickwork|Infomerics"),
        _i("downgrade_reasons", "Agency's reasons for a downgrade", "For a downward revision, the reasons given "
           "by the rating agency.", "MC Annexure 18, A(3)", hint=r"reason", when="a downgrade"),
        _i("outlook_revision", "Outlook revision", "A change in outlook is disclosable even without a change in "
           "the rating.", "MC Annexure 18, A(3)(b)", hint=r"outlook", when="the outlook changed"),
    ], "notes": ["Within 24 hours.",
                 "Since 2 Aug 2025 NSE also publishes ratings from the agencies' own uploads (NSE/CML/2025/33); "
                 "whether the company's filing is still needed was not confirmed."]},

    "auditor_change": {"sources": [_LODR, _MC], "items": [
        _i("reason_for_change", "Reason for the change", "Reason for change: appointment, re-appointment, "
           "resignation, removal, death or otherwise.", "MC Annexure 18, A(7.1)",
           hint=r"appoint|resign|cessation|re-?appoint|remov"),
        _i("date_and_term", "Effective date and term", "Date of appointment or cessation and the term.",
           "MC Annexure 18, A(7.2)", hint=r"with effect from|w\.?e\.?f|effective|term|conclusion of the"),
        _i("brief_profile", "Auditor's brief profile", "Brief profile of the audit firm.",
           "MC Annexure 18, A(7.3)", hint=r"profile|firm registration|FRN|established|partners",
           when="appointment"),
        _i("resignation_reasons", "Auditor's detailed reasons", "The detailed reasons for resignation given by "
           "the auditor, within 24 hours of receiving them, in the Annexure 21 format (auditor details, "
           "association, reasons, concerns raised, information not provided, declaration).",
           "LODR Sch III Pt A Para A(7A); MC Annexure 21", hint=r"reason", when="auditor resignation"),
        _OMITTED, _LATE,
    ], "notes": ["Within 12 hours; 24 hours for a resignation. If from a board decision: " + _BOARD_TIMING,
                 "After a resignation, the Audit Committee's views go to the exchanges within 24 hours of the "
                 "committee meeting (MC Section V-D, 9.3.1)."]},

    "litigation_action": {"sources": [_LODR, _MC], "items": [
        _i("authority_name", "Name of the authority, court or opposing party",
           "For regulatory action: the authority. For litigation: the opposing party and the court or tribunal.",
           "LODR Sch III Pt A Para A(19)(i)/A(20)(i); MC Annexure 18, B(8.1)(a)",
           hint=r"court|tribunal|authority|SEBI|NCLT|commissioner|department|versus|vs\.?|petition"),
        _i("action_details", "Nature and details of the action or dispute",
           "Nature and details of the action taken or order passed, or brief details of the dispute.",
           "LODR Sch III Pt A Para A(19)(ii)/A(20)(ii); MC Annexure 18, B(8.1)(a)",
           hint=r"order|notice|demand|penalty|dispute|claim|show cause"),
        _i("receipt_date", "Date the order or notice was received",
           "Date of receipt of the direction, order or communication.", "LODR Sch III Pt A Para A(19)(iii)/A(20)(iii)",
           hint=r"receiv"),
        _i("violation_details", "Violation alleged", "Details of the violation or contravention committed or "
           "alleged.", "LODR Sch III Pt A Para A(19)(iv)/A(20)(iv)", hint=r"violat|contraven|alleg|section"),
        _i("impact", "Impact, in money where possible", "Impact on financial, operational or other activities, "
           "quantified in money to the extent possible.", "LODR Sch III Pt A Para A(19)(v)/A(20)(v); "
           "MC Annexure 18, B(8.1)(b)-(c)", hint=r"impact|₹|rs\.?|inr|crore|lakh|no material"),
        _i("authority_communication", "Authority's communication attached",
           "Where the disclosure follows a communication from an authority, that communication must be "
           "disclosed with it, unless the authority prohibits it.", "LODR Reg 30(13)",
           hint=r"enclos|attach|copy of"),
    ], "notes": ["Regulatory actions (Para A(19)/(20)): within 24 hours. Penalties of ₹1 lakh or more from a "
                 "sectoral regulator, or ₹10 lakh or more from any other authority, are disclosed within 24 "
                 "hours; smaller ones quarterly in the Integrated Filing (Governance).",
                 "Litigation (Para B(8)): within 24 hours; 72 hours for non-tax claims if all information is in "
                 "the structured digital database."]},

    "agm_intimation": {"sources": [_MC, _LODR], "items": [
        _i("notice_date", "Date of the notice", "Date of the notice, call letters or resolutions.",
           "MC Annexure 18, A(12.1)", hint=r"notice dated|dated", when="notice of a general meeting"),
        _i("agenda_manner", "Agenda and manner of approval", "Agenda proposed, resolutions to be passed and the "
           "manner of approval.", "MC Annexure 18, A(12.2)",
           hint=r"agenda|resolution|business|e-?voting|postal ballot", when="notice of a general meeting"),
        _i("proceedings_date", "Date of the meeting", "Date of the meeting.", "MC Annexure 18, A(13.1)",
           hint=r"held on|meeting.{0,40}\d{4}", when="proceedings of a general meeting"),
        _i("proceedings_items_results", "Items and results", "Brief details of the items deliberated and the "
           "results.", "MC Annexure 18, A(13.2)", hint=r"result|passed|approved|carried",
           when="proceedings of a general meeting"),
    ], "notes": ["Within 12 hours (30 minutes / 3 hours if it flows from a board decision)."]},

    "voting_results": {"sources": [_LODR, _MC], "items": [
        _i("meeting_header", "Meeting date, shareholders and attendance",
           "Date of the meeting; total shareholders on the record date; shareholders present in person or by "
           "proxy and through video conference, split promoter group / public.", "MC Annexure 22",
           hint=r"record date|present|proxy|video conferenc"),
        _i("resolution_type", "Ordinary or special, per item", "For each item, whether an ordinary or special "
           "resolution.", "MC Annexure 22", hint=r"ordinary|special"),
        _i("promoter_interest", "Promoter interest in each item", "Whether the promoter group is interested in "
           "the agenda item.", "MC Annexure 22", hint=r"promoter.{0,40}interest|interested"),
        _i("voting_table", "Voting table by category and mode", "For promoter group, public institutions and "
           "public non-institutions, by e-voting, poll and postal ballot: shares held, votes polled, % polled, "
           "votes for and against, and their percentages.", "MC Annexure 22",
           hint=r"in favour|against|votes polled"),
        _i("scrutiniser_report", "Scrutiniser's report", "Results declared with the scrutiniser's report and "
           "forwarded to the exchanges.", "Companies (Management and Administration) Rules, 2014, Rule 20(4)(xvi)",
           hint=r"scrutini[sz]er", verified=False),
    ], "notes": ["Within two working days of the conclusion of the general meeting (LODR Reg 44(3))."]},

    "reg47_newspaper": {"sources": [_LODR, _MC], "items": [
        _i("qr_code", "Advertisement with a QR code", "The advertisement must contain a QR code.",
           "LODR Reg 47(1)", hint=r"QR|quick response"),
        _i("webpage_details", "Webpage with the full results", "Details of the webpage where the complete "
           "results, with any auditor's modified opinion, can be accessed.", "LODR Reg 47(1)",
           hint=r"www\.|website|webpage|https?://"),
        _i("newspapers", "English national and regional-language daily", "At least one English national daily "
           "and one daily in the language of the region of the registered office.", "LODR Reg 47(4)",
           hint=r"edition|newspaper|daily"),
    ], "notes": ["Within 48 hours of the board meeting that approved the results (LODR Reg 47(1))."]},

    "trading_window_closure": {"sources": [_PIT], "items": [
        _i("closure_period", "Closure period",
           "For financial results: from the end of the quarter until 48 hours after the results are declared.",
           "PIT Schedule B, cl. 4(2)", hint=r"quarter|48 hours|closed|closure"),
        _i("who_is_barred", "Who may not trade", "Designated persons and their immediate relatives may not trade "
           "while the window is closed.", "PIT Schedule B, cl. 4(1)", hint=r"designated person|immediate relative"),
        _i("reopening", "Reopening", "The window reopens no earlier than 48 hours after the information becomes "
           "generally available.", "PIT Schedule B, cl. 5", hint=r"48 hours|re-?open"),
    ], "notes": []},

    "general_updates": {"sources": [_LODR], "items": [
        _i("schedule_advance", "Schedule of the meet", "Schedule of the analyst or institutional investor meet, "
           "at least two working days in advance (excluding the date of intimation and of the meet). Names "
           "of participants are optional.", "LODR Sch III Pt A Para A(15)(a)(i)",
           hint=r"schedule|meet|conference call|analyst|investor", when="an analyst or institutional investor meet"),
        _i("presentation_before_event", "Presentation filed before the event", "Presentations for the meet "
           "must reach the exchanges before it begins.", "LODR Sch III Pt A Para A(15)(a)(ii)",
           hint=r"presentation", when="a presentation is used"),
    ], "notes": ["After an earnings call: audio on the website before the next trading day or within 24 hours; "
                 "video within 48 hours; transcript within five working days, also to the exchanges "
                 "(LODR Sch III Para A(15)(b))."]},

    "buyback_announcement": {"sources": [_MC, _LODR], "items": [
        _i("decision", "Board decision on buyback", "The decision on buyback of securities.",
           "LODR Sch III Pt A Para A(4)(c)", hint=r"buy-?back"),
        _i("number_proposed", "Number proposed", "Number of securities proposed for buyback.",
           "MC Annexure 18, A(2.3)(a)", hint=r"number of|shares"),
        _i("pct_paid_up", "As % of paid-up capital", "Number proposed as a percentage of existing paid-up "
           "capital.", "MC Annexure 18, A(2.3)(b)", hint=r"%|per ?cent"),
        _i("price", "Buyback price", "Buyback price.", "MC Annexure 18, A(2.3)(c)", hint=r"price|₹|rs\.?"),
        _i("shareholding_pattern", "Pre and post shareholding", "Pre and post shareholding pattern.",
           "MC Annexure 18, A(2.3)(e)", hint=r"pre|post|shareholding pattern"),
    ], "notes": ["Board decisions: " + _BOARD_TIMING, "Prior intimation under Reg 29(1)(b), two working days."]},

    "bonus_issue": {"sources": [_MC, _LODR], "items": [
        _i("security_type_number", "Type and number of securities", "Type of securities, type of issuance and "
           "the total number proposed.", "MC Annexure 18, A(2.1)(a)-(c)", hint=r"equity shares|number"),
        _i("reserve_source", "Free reserves or share premium", "Whether out of free reserves from profits or the "
           "share premium account.", "MC Annexure 18, A(2.1)(e)(i)", hint=r"free reserves|securities premium|share premium"),
        _i("ratio", "Bonus ratio", "Bonus ratio.", "MC Annexure 18, A(2.1)(e)(ii)", hint=r"\d+\s*:\s*\d+|ratio"),
        _i("capital_pre_post", "Share capital pre and post", "Share capital before and after the bonus issue.",
           "MC Annexure 18, A(2.1)(e)(iii)", hint=r"pre|post"),
        _i("reserves_required_available", "Reserves required and available",
           "Reserves or premium required, the amount available and the date it is available as on.",
           "MC Annexure 18, A(2.1)(e)(iv)-(v)", hint=r"available|required"),
        _i("audited", "Whether the figures are audited", "Whether the figures are audited.",
           "MC Annexure 18, A(2.1)(e)(vi)", hint=r"audited"),
        _i("credit_date_estimate", "Estimated credit date", "Estimated date by which the bonus shares will be "
           "credited or dispatched.", "MC Annexure 18, A(2.1)(e)(vii); LODR Para A(4)(e)",
           hint=r"credit|dispatch"),
    ], "notes": ["Board decisions: " + _BOARD_TIMING, "Record date under Reg 42(1)(b)."]},

    "stock_split": {"sources": [_MC, _LODR], "items": [
        _i("ratio", "Split or consolidation ratio", "Split or consolidation ratio.", "MC Annexure 18, A(2.2)(a)",
           hint=r"ratio|sub-?divi|split|face value"),
        _i("rationale", "Rationale", "Rationale for the split or consolidation.", "MC Annexure 18, A(2.2)(b)",
           hint=r"rational|liquidity|affordab|reason"),
        _i("capital_pre_post", "Capital pre and post", "Authorised, paid-up and subscribed capital before and "
           "after.", "MC Annexure 18, A(2.2)(c)", hint=r"authori[sz]ed|paid-?up"),
        _i("completion_time", "Expected completion", "Expected time of completion.", "MC Annexure 18, A(2.2)(d)",
           hint=r"complet|expected"),
        _i("share_class", "Class of shares", "Class of shares split or consolidated.", "MC Annexure 18, A(2.2)(e)",
           hint=r"equity shares|class"),
        _i("shares_pre_post", "Shares per class pre and post", "Number of shares of each class before and after.",
           "MC Annexure 18, A(2.2)(f)", hint=r"pre|post|before|after"),
    ], "notes": ["Within 12 hours (30 minutes / 3 hours if a board decision). Record date under Reg 42(1)(e)."]},

    "rpt_disclosure": {"sources": [_LODR, _MC], "items": [
        _i("reg23_9_format", "Half-yearly disclosure in the specified format",
           "Related party transactions are disclosed every six months, on the date the results are published, "
           "in the format SEBI specifies, as part of the Integrated Filing (Financial).",
           "LODR Reg 23(9); MC Section III-B para 1", hint=r"related party"),
        _i("arms_length", "Arm's length and ordinary course",
           "Where an RPT appears inside another event (acquisition, sale, agreement), state whether it is at "
           "arm's length.", "MC Annexure 18 (e.g. A(1.1)(b), A(1.4)(f))", hint=r"arm'?s length|ordinary course"),
    ], "notes": ["There is no stand-alone Reg 30 event for an RPT; it is disclosed half-yearly and inside other "
                 "events' details."]},
}

# Filing types that share a list.
ALIASES = {
    "book_closure": "record_date",
    "egm_intimation": "agm_intimation",
    "postal_ballot_notice": "agm_intimation",
    "proceedings_agm": "agm_intimation",
}

# The provision each filing type is made under, for the citation check.
CITES = {
    "reg29_board_meeting": ["29"], "record_date": ["42"], "book_closure": ["42"], "reg33_results": ["33"],
    "voting_results": ["44"], "reg47_newspaper": ["47"], "shareholding_pattern": ["31"],
    "reg13_grievance": ["13"], "reg34_ar": ["34"], "reg24a_ascr": ["24A"], "dividend": ["30", "43", "42"],
    "board_change": ["30"], "outcome_board_meeting": ["30"], "credit_rating": ["30"],
    "acquisition_disclosure": ["30"], "divestment_disclosure": ["30"], "litigation_action": ["30"],
    "auditor_change": ["30"], "general_updates": ["30"], "buyback_announcement": ["30"],
    "bonus_issue": ["30"], "stock_split": ["30"], "agm_intimation": ["30"], "egm_intimation": ["30"],
}


def for_type(filing_type: str) -> Dict[str, Any]:
    """The requirement list for a filing type. Types without a verified list
    get one derived from Delta's own template guidance, flagged as such."""
    key = ALIASES.get(filing_type, filing_type)
    if key in REQUIREMENTS:
        r = REQUIREMENTS[key]
        return {"filing_type": filing_type, "derived": False, "reviewed": REVIEWED,
                "sources": r["sources"], "items": r["items"], "notes": r.get("notes", [])}
    try:
        from draft_engine import get_filing_type
        ft = get_filing_type(filing_type) or {}
    except Exception:  # noqa: BLE001
        ft = {}
    items: List[Dict] = []
    for n, part in enumerate([p.strip() for p in (ft.get("body_hint") or "").split(",") if len(p.strip()) > 6][:6], 1):
        items.append(_i(f"template_{n}", part[:60].rstrip("."), part, "Delta template guidance"))
    return {"filing_type": filing_type, "derived": True, "reviewed": None, "sources": [],
            "items": items, "notes": []}
