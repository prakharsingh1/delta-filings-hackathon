"""Fetch and process Corporate Governance reports from NSE."""
import json
import re
from datetime import datetime
from lxml import etree
from fetch import SESSION, _warm_session, BASE_URL

GOV_MASTER_API = BASE_URL + "/api/corporate-governance-master"
GOV_INTEGRATED_API = BASE_URL + "/api/integrated-filing-results"
GOV_DETAIL_API = BASE_URL + "/api/corporate-governance"

# Quarter date parsing for sorting
_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}


def _parse_quarter_date(date_str):
    """Parse '31-DEC-2024' to a sortable tuple (2024, 12, 31)."""
    try:
        parts = date_str.strip().split("-")
        return (int(parts[2]), _MONTH_MAP.get(parts[1].upper(), 0), int(parts[0]))
    except Exception:
        return (0, 0, 0)


def _fetch_integrated_reports(symbol):
    """Fetch governance reports from the new Integrated Filing system (Mar 2025+)."""
    try:
        resp = SESSION.get(GOV_INTEGRATED_API,
                           params={
                               "index": "equities",
                               "symbol": symbol,
                               "type": "Integrated Filing- Governance",
                               "size": "50",
                               "page": "0",
                           },
                           timeout=15)
        if resp.status_code != 200:
            print(f"[GOV] Integrated API returned {resp.status_code} for {symbol}")
            return []
        data = resp.json()
        records = data.get("data", [])
        if not isinstance(records, list):
            return []
        result = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            result.append({
                "recordId": str(rec.get("seq_Id", "")),
                "quarter": rec.get("qe_Date", ""),
                "submissionDate": rec.get("broadcast_Date", rec.get("creation_Date", "")),
                "xbrl": rec.get("xbrl", ""),
                "revised": rec.get("type_Sub", "").lower() in ("revision", "revised"),
                "source": "integrated",
            })
        return result
    except Exception as e:
        print(f"[GOV] Failed to get integrated reports for {symbol}: {e}")
        return []


def _fetch_legacy_reports(symbol):
    """Fetch governance reports from the old Master API (up to Dec 2024)."""
    try:
        resp = SESSION.get(GOV_MASTER_API,
                           params={"index": "equities", "symbol": symbol},
                           timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        records = data.get("data", [])
        if not isinstance(records, list):
            return []
        result = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            result.append({
                "recordId": str(rec.get("recordId", "")),
                "quarter": rec.get("date", ""),
                "submissionDate": rec.get("cgTimeStamp", rec.get("submissionDate", "")),
                "xbrl": rec.get("xbrl", ""),
                "revised": rec.get("revisedData", "N") == "R",
                "source": "legacy",
            })
        return result
    except Exception as e:
        print(f"[GOV] Failed to get legacy reports for {symbol}: {e}")
        return []


def fetch_governance_reports_list(symbol):
    """Get all available governance reports by merging both APIs.
    Returns list sorted by quarter date (newest first), deduplicated."""
    _warm_session()

    integrated = _fetch_integrated_reports(symbol)
    legacy = _fetch_legacy_reports(symbol)

    # Merge: integrated takes priority for duplicate quarters
    seen = {}
    for r in integrated:
        key = (r["quarter"], r["revised"])
        seen[key] = r
    for r in legacy:
        key = (r["quarter"], r["revised"])
        if key not in seen:
            seen[key] = r

    # Sort by quarter date descending
    merged = sorted(seen.values(), key=lambda r: _parse_quarter_date(r["quarter"]), reverse=True)
    return merged


def fetch_governance_report(symbol, rec_id):
    """Fetch a full governance report by recId.
    First tries JSON API; if data is empty, falls back to XBRL parsing."""
    _warm_session()
    try:
        resp = SESSION.get(GOV_DETAIL_API, params={"recId": rec_id}, timeout=15)
        if resp.status_code == 200:
            try:
                raw = resp.json()
                # Check if JSON has actual data (not empty arrays)
                if _has_governance_data(raw):
                    return raw
            except Exception:
                pass
    except Exception as e:
        print(f"[GOV] JSON API failed for {rec_id}: {e}")

    # Fallback: try XBRL parsing for integrated filings
    xbrl_url = _find_xbrl_url(symbol, rec_id)
    if xbrl_url:
        print(f"[GOV] JSON empty for {rec_id}, parsing XBRL...")
        xbrl_data = _parse_xbrl_governance(xbrl_url)
        if xbrl_data:
            return xbrl_data

    return None


def _has_governance_data(raw):
    """Check if the JSON governance response actually has content."""
    if not raw or not isinstance(raw, dict):
        return False
    for key in ["cobod", "coc", "bodmeeting"]:
        section = raw.get(key, [])
        if section and isinstance(section, list) and isinstance(section[0], dict):
            data = section[0].get("data", {})
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list) and v:
                        return True
            elif isinstance(data, list) and data:
                return True
    return False


# Cache of rec_id -> xbrl_url from integrated results
_xbrl_url_cache = {}


def _find_xbrl_url(symbol, rec_id):
    """Look up the XBRL URL for a given recId."""
    if rec_id in _xbrl_url_cache:
        return _xbrl_url_cache[rec_id]
    # Try fetching integrated list to find the URL
    try:
        resp = SESSION.get(GOV_INTEGRATED_API,
                           params={"index": "equities", "symbol": symbol,
                                   "type": "Integrated Filing- Governance",
                                   "size": "50", "page": "0"},
                           timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            for rec in data.get("data", []):
                rid = str(rec.get("seq_Id", ""))
                url = rec.get("xbrl", "")
                if url:
                    _xbrl_url_cache[rid] = url
    except Exception:
        pass
    return _xbrl_url_cache.get(rec_id)


def _parse_xbrl_governance(xbrl_url):
    """Parse XBRL XML into a format compatible with the legacy JSON structure."""
    try:
        resp = SESSION.get(xbrl_url, timeout=30)
        if resp.status_code != 200:
            return None
        root = etree.fromstring(resp.content)
    except Exception as e:
        print(f"[GOV] Failed to parse XBRL: {e}")
        return None

    def _group_by_context(root, pattern):
        """Group XBRL elements by context ID matching a regex pattern."""
        groups = {}
        for el in root.iter():
            ln = etree.QName(el.tag).localname
            ctx = el.get("contextRef", "")
            if not ctx or not el.text or not el.text.strip():
                continue
            m = re.match(pattern, ctx)
            if m:
                idx = m.group(1)
                if idx not in groups:
                    groups[idx] = {}
                groups[idx][ln] = el.text.strip()
        return groups

    result = {}

    # Board of Directors
    directors = _group_by_context(root, r"(?:D_)?CompBOD(\d+)")
    if directors:
        members = []
        for idx in sorted(directors.keys(), key=int):
            d = directors[idx]
            title = d.get("Title", "")
            name = d.get("NameOftheDirector", d.get("NameOfTheDirector", ""))
            positions = []
            for pk in ["PositionOfDirectorInBoardOne", "PositionOfDirectorInBoardTwo", "PositionOfDirectorInBoardThree"]:
                p = d.get(pk, "")
                if p and p not in ("Not Applicable", "NA", "-", ""):
                    positions.append(p)
            category = " & ".join(positions) if positions else "-"
            members.append({
                "title": title,
                "directorName": name,
                "din": d.get("DirectorIdentificationNumberOfDirector", "-"),
                "category": category,
                "dateOfAppointment": d.get("DateOfAppointmentOfDirector", "-"),
                "dateOfCessation": d.get("DateOfCessationOfDirector", "-"),
                "tenure": d.get("TenureOfDirector", "-"),
                "noOfDirShp": d.get("NumberOfDirectorshipInListedEntitiesIncludingThisListedEntity", "-"),
                "noOfMemberships": d.get("NumberOfMembershipsInAuditOrStakeholderCommitteesIncludingThisListedEntity", "-"),
            })
        result["cobod"] = [{"data": {"CompositionBOD": members}}]

    # Committees
    comm_members = _group_by_context(root, r"(?:D_)?CompComit(\d+)")
    if comm_members:
        committees = {}
        for idx in sorted(comm_members.keys(), key=int):
            m = comm_members[idx]
            comm_name = m.get("NameOfCommittee", "Unknown")
            if comm_name not in committees:
                committees[comm_name] = []
            roles = []
            for pk in ["PositionOfDirectorInCommitteeOne", "PositionOfDirectorInCommitteeTwo"]:
                p = m.get(pk, "")
                if p and p not in ("Not Applicable", "NA", "-", ""):
                    roles.append(p)
            committees[comm_name].append({
                "name": m.get("NameOfCommitteeMembers", "-"),
                "category": m.get("PositionOfDirectorInCommitteeOne", "-"),
                "chairPersonMember": "Chairperson" if "Chairperson" in " ".join(roles) else "Member",
                "appointmentDate": m.get("DateOfAppointmentOfDirectorInCommittee", "-"),
                "cessationDate": m.get("DateOfCessationOfDirectorInCommittee", "-"),
            })
        result["coc"] = [{"data": committees}]

    # Board Meetings
    board_meetings = _group_by_context(root, r"(?:D_)?MeetingBOD(\d+)")
    if board_meetings:
        meetings = []
        for idx in sorted(board_meetings.keys(), key=int):
            m = board_meetings[idx]
            date = m.get("DatesOfMeetingIfAnyInTheRelevantQuarter",
                         m.get("DatesOfMeetingInThePreviousQuarter", "-"))
            meetings.append({
                "datePrevCurrQuarter": date,
                "quorumMet": m.get("WhetherRequirementOfQuorumMet", "-"),
                "dirOnDateMeeting": m.get("TotalNumberOfDirectorsAsOnDateOfTheMeeting", "-"),
                "noOfdirector": m.get("NumberOfDirectorsPresentInMeetingOfBoardOfDirectors", "-"),
                "noOfIndptdir": m.get("NumberOfIndependentDirectorsAttendingTheMeeting", "-"),
                "maxGap": m.get("MaximumGapBetweenAnyTwoConsecutiveMeetings", "-"),
            })
        result["bodmeeting"] = [{"data": meetings}]

    # Committee Meetings
    comm_meetings = _group_by_context(root, r"(?:D_)?MeetingComit(\d+)")
    if comm_meetings:
        cm_list = []
        for idx in sorted(comm_meetings.keys(), key=int):
            m = comm_meetings[idx]
            date = m.get("DatesOfMeetingOfTheCommitteeInTheRelevantQuarter",
                         m.get("DatesOfMeetingOfTheCommitteeInThePreviousQuarter", "-"))
            cm_list.append({
                "commName": m.get("NameOfCommittee", "-"),
                "datePrevCurrQuarter": date,
                "quorumMetReq": m.get("WhetherRequirementOfQuorumMet", "-"),
                "dirOnDateMeeting": m.get("NumberOfDirectorPresentInMeetingOfCommitteeAllDirectorsIncludingIndependentDirector",
                                          m.get("TotalMembersOfCommitteeAsOnDateOfMeeting", "-")),
                "noOfMemAttend": m.get("NumberOfMemberAttendingMeetingOfCommittee", "-"),
                "noOfIndepdDir": m.get("NumberOfIndependentDirectorAttendingMeetingOfCommittee", "-"),
                "maxGap": m.get("MaximumGapBetweenAnyTwoConsecutiveMeetings", "-"),
            })
        result["meetingcomm"] = [{"data": cm_list}]

    # Related Party Transactions (from XBRL affirmation-style fields)
    rpt_data = []
    for el in root.iter():
        ln = etree.QName(el.tag).localname
        ctx = el.get("contextRef", "")
        if ctx in ("MainD", "MainI") and el.text and el.text.strip():
            if "relatedparty" in ln.lower() or "rpt" in ln.lower():
                rpt_data.append({"subject": ln, "compliance": el.text.strip()})
    if rpt_data:
        result["relpartytxns"] = [{"data": rpt_data}]

    # Fines, Penalties & Strictures
    fines_data = []
    fines_groups = _group_by_context(root, r"(?:D_)?(?:Penalty|Fine|Stricture)(\d+)")
    if fines_groups:
        for idx in sorted(fines_groups.keys(), key=int):
            f = fines_groups[idx]
            fines_data.append({
                "authority": f.get("NameOfAuthority", f.get("AuthorityRegulatorCourt", "-")),
                "details": f.get("DetailsOfPenaltyOrStrictureImposed", f.get("BriefDescriptionOfPenaltyOrStricture", "-")),
                "amount": f.get("AmountOfPenaltyOrFine", "-"),
                "status": f.get("StatusOfPenalty", f.get("ObservationsOfPracticingCompanySecretaryOrAuditor", "-")),
            })
    # Also scan for top-level penalty/fine fields
    for el in root.iter():
        ln = etree.QName(el.tag).localname
        ctx = el.get("contextRef", "")
        if ctx in ("MainD", "MainI") and el.text and el.text.strip():
            ln_lower = ln.lower()
            if any(kw in ln_lower for kw in ["penalty", "stricture", "fine", "penaltiesimposed"]):
                fines_data.append({"subject": ln, "compliance": el.text.strip()})
    if fines_data:
        result["fines"] = [{"data": fines_data}]

    # Litigations
    litigation_data = []
    lit_groups = _group_by_context(root, r"(?:D_)?(?:Litigation|Dispute|Case)(\d+)")
    if lit_groups:
        for idx in sorted(lit_groups.keys(), key=int):
            l = lit_groups[idx]
            litigation_data.append({
                "caseType": l.get("TypeOfLitigation", l.get("NatureOfLitigation", "-")),
                "details": l.get("DetailsOfLitigation", l.get("BriefDescriptionOfCase", "-")),
                "status": l.get("StatusOfLitigation", l.get("CurrentStatus", "-")),
                "amount": l.get("AmountInvolved", "-"),
                "authority": l.get("ForumWherePending", l.get("AuthorityCourt", "-")),
            })
    # Scan top-level litigation/dispute fields
    for el in root.iter():
        ln = etree.QName(el.tag).localname
        ctx = el.get("contextRef", "")
        if ctx in ("MainD", "MainI") and el.text and el.text.strip():
            ln_lower = ln.lower()
            if any(kw in ln_lower for kw in ["litigation", "dispute", "materialcase",
                                              "pendingproceeding", "significantmaterial"]):
                litigation_data.append({"subject": ln, "compliance": el.text.strip()})
    if litigation_data:
        result["litigations"] = [{"data": litigation_data}]

    # CSR-specific fields
    csr_data = {}
    for el in root.iter():
        ln = etree.QName(el.tag).localname
        ctx = el.get("contextRef", "")
        if ctx in ("MainD", "MainI") and el.text and el.text.strip():
            ln_lower = ln.lower()
            if any(kw in ln_lower for kw in ["csr", "corporatesocialresponsibility",
                                              "csrcommittee", "csrpolicy", "csrexpenditure",
                                              "csrobligation", "csramount"]):
                csr_data[ln] = el.text.strip()
    if csr_data:
        result["csr_details"] = [{"data": csr_data}]

    # Affirmations
    aff_data = {}
    for el in root.iter():
        ln = etree.QName(el.tag).localname
        ctx = el.get("contextRef", "")
        if ctx in ("MainD", "MainI") and el.text and el.text.strip():
            if any(kw in ln.lower() for kw in ["composition", "meetings", "sebiregulations",
                                                "thisreport", "placed", "whistle", "vigil",
                                                "policyondetermination", "sexualharassment",
                                                "disclosure", "codeofconduct"]):
                aff_data[ln] = el.text.strip()
    if aff_data:
        result["affirmations"] = [{"data": aff_data}]

    return result if result else None


def parse_governance_report(raw):
    """Parse a raw governance report into a clean structure for the UI."""
    if not raw:
        return None

    result = {"sections": []}

    # 1. Board of Directors
    cobod = raw.get("cobod", [])
    if cobod and isinstance(cobod, list) and isinstance(cobod[0], dict):
        data = cobod[0].get("data", {})
        members = data.get("CompositionBOD", [])
        if members and isinstance(members, list):
            board_members = []
            for m in members:
                if not isinstance(m, dict):
                    continue
                name = f"{m.get('title', '')} {m.get('directorName', '')}".strip()
                board_members.append({
                    "name": name,
                    "din": m.get("din", "-"),
                    "category": m.get("category", "-"),
                    "appointed": m.get("dateOfAppointment", m.get("date", "-")),
                    "ceased": m.get("dateOfCessation", "-"),
                    "tenure": m.get("tenure", "-"),
                    "directorships": m.get("noOfDirShp", "-"),
                    "memberships": m.get("noOfMemberships", "-"),
                })
            if board_members:
                result["sections"].append({
                    "id": "board",
                    "title": "Board of Directors",
                    "type": "table",
                    "columns": ["Name", "DIN", "Category", "Appointed", "Ceased", "Tenure", "Directorships", "Committee Memberships"],
                    "rows": [[m["name"], m["din"], m["category"], m["appointed"],
                              m["ceased"], m["tenure"], m["directorships"], m["memberships"]]
                             for m in board_members],
                    "summary": f"{len(board_members)} directors"
                })

    # 2. Committees — parse ALL committees found in the data
    coc = raw.get("coc", [])
    if coc and isinstance(coc, list) and isinstance(coc[0], dict):
        data = coc[0].get("data", {})
        if isinstance(data, dict):
            # Standard committees we always look for, plus any others in the data
            standard_comms = [
                "Audit Committee",
                "Nomination and Remuneration Committee",
                "Stakeholders Committee",
                "Stakeholders Relationship Committee",
                "Corporate Social Responsibility Committee",
                "CSR Committee",
                "Risk Management Committee",
            ]
            all_comm_names = list(dict.fromkeys(standard_comms + list(data.keys())))
            for comm_name in all_comm_names:
                members = data.get(comm_name, [])
                if members and isinstance(members, list):
                    rows = []
                    for m in members:
                        if not isinstance(m, dict):
                            continue
                        rows.append([
                            m.get("name", "-"),
                            m.get("category", "-"),
                            m.get("chairPersonMember", "-"),
                            m.get("appointmentDate", "-"),
                            m.get("cessationDate", "-"),
                        ])
                    if rows:
                        result["sections"].append({
                            "id": f"comm_{comm_name.lower().replace(' ', '_')}",
                            "title": comm_name,
                            "type": "table",
                            "columns": ["Name", "Category", "Role", "Appointed", "Ceased"],
                            "rows": rows,
                            "summary": f"{len(rows)} members"
                        })

    # 3. Board Meetings
    bodmeeting = raw.get("bodmeeting", [])
    if bodmeeting and isinstance(bodmeeting, list) and isinstance(bodmeeting[0], dict):
        data = bodmeeting[0].get("data", [])
        if data and isinstance(data, list):
            rows = []
            for m in data:
                if not isinstance(m, dict):
                    continue
                rows.append([
                    m.get("datePrevCurrQuarter", "-"),
                    m.get("quorumMet", "-"),
                    m.get("dirOnDateMeeting", "-"),
                    m.get("noOfdirector", "-"),
                    m.get("noOfIndptdir", "-"),
                    m.get("maxGap", "-"),
                ])
            if rows:
                result["sections"].append({
                    "id": "meetings",
                    "title": "Board Meetings",
                    "type": "table",
                    "columns": ["Period", "Quorum Met", "Total Directors", "Directors Present",
                                "Independent Directors", "Max Gap (Days)"],
                    "rows": rows,
                    "summary": f"{len(rows)} meeting(s)"
                })

    # 4. Committee Meetings
    meetingcomm = raw.get("meetingcomm", [])
    if meetingcomm and isinstance(meetingcomm, list) and isinstance(meetingcomm[0], dict):
        data = meetingcomm[0].get("data", [])
        if data and isinstance(data, list):
            rows = []
            for m in data:
                if not isinstance(m, dict):
                    continue
                rows.append([
                    m.get("commName", "-"),
                    m.get("datePrevCurrQuarter", "-"),
                    m.get("quorumMetReq", m.get("QUORUM_MET", "-")),
                    m.get("dirOnDateMeeting", "-"),
                    m.get("noOfMemAttend", "-"),
                    m.get("noOfIndepdDir", "-"),
                    m.get("maxGap", "-"),
                ])
            if rows:
                result["sections"].append({
                    "id": "committee_meetings",
                    "title": "Committee Meetings",
                    "type": "table",
                    "columns": ["Committee", "Period", "Quorum Met", "Total Members",
                                "Members Attended", "Independent Directors", "Max Gap (Days)"],
                    "rows": rows,
                    "summary": f"{len(rows)} meeting(s)"
                })

    # 5. Related Party Transactions
    rpt = raw.get("relpartytxns", [])
    if rpt and isinstance(rpt, list) and isinstance(rpt[0], dict):
        data = rpt[0].get("data", [])
        # data can be a list of {subject, compliance} dicts
        if isinstance(data, list) and data:
            rows = []
            for item in data:
                if isinstance(item, dict):
                    rows.append([
                        _humanize_xbrl_field(item.get("subject", "-")),
                        item.get("compliance", "-"),
                    ])
            if rows:
                result["sections"].append({
                    "id": "rpt",
                    "title": "Related Party Transactions",
                    "type": "table",
                    "columns": ["Subject", "Compliance Status"],
                    "rows": rows,
                    "summary": f"{len(rows)} item(s)"
                })
        elif isinstance(data, dict) and data:
            # Fallback if data is a dict
            items = []
            for k, v in data.items():
                if isinstance(v, str) and v.strip() and v.strip() != '-':
                    items.append({"field": k, "value": v})
            if items:
                result["sections"].append({
                    "id": "rpt",
                    "title": "Related Party Transactions",
                    "type": "key_value",
                    "items": items,
                    "summary": f"{len(items)} item(s)"
                })

    # 6. Affirmations
    aff = raw.get("affirmations", [])
    if aff and isinstance(aff, list) and isinstance(aff[0], dict):
        data = aff[0].get("data", aff[0])
        if isinstance(data, dict):
            text_parts = []
            for k, v in data.items():
                if k in ("affirmReportSubmitData",):
                    continue
                text_parts.append(f"{k}: {v}")
            if text_parts:
                result["sections"].append({
                    "id": "affirmations",
                    "title": "Compliance Affirmations",
                    "type": "key_value",
                    "items": [{"field": _humanize_xbrl_field(k), "value": str(v)} for k, v in data.items()
                              if k != "affirmReportSubmitData"],
                    "summary": f"{len(text_parts)} item(s)"
                })
        elif isinstance(data, str) and data.strip():
            lines = [l.strip() for l in data.split("\n") if l.strip()]
            result["sections"].append({
                "id": "affirmations",
                "title": "Compliance Affirmations",
                "type": "text",
                "text": data,
                "summary": f"{len(lines)} affirmation(s)"
            })

    # 7. Fines, Penalties & Strictures
    fines = raw.get("fines", [])
    if fines and isinstance(fines, list) and isinstance(fines[0], dict):
        data = fines[0].get("data", [])
        if isinstance(data, list) and data:
            rows = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                if "subject" in item:
                    rows.append([_humanize_xbrl_field(item.get("subject", "-")), item.get("compliance", "-")])
                else:
                    rows.append([
                        item.get("authority", "-"),
                        item.get("details", "-"),
                        item.get("amount", "-"),
                        item.get("status", "-"),
                    ])
            if rows:
                cols = ["Subject", "Compliance"] if len(rows[0]) == 2 else ["Authority", "Details", "Amount", "Status"]
                result["sections"].append({
                    "id": "fines",
                    "title": "Fines, Penalties & Strictures",
                    "type": "table",
                    "columns": cols,
                    "rows": rows,
                    "summary": f"{len(rows)} item(s)"
                })

    # 8. Litigations
    litigations = raw.get("litigations", [])
    if litigations and isinstance(litigations, list) and isinstance(litigations[0], dict):
        data = litigations[0].get("data", [])
        if isinstance(data, list) and data:
            rows = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                if "subject" in item:
                    rows.append([_humanize_xbrl_field(item.get("subject", "-")), item.get("compliance", "-")])
                else:
                    rows.append([
                        item.get("caseType", "-"),
                        item.get("details", "-"),
                        item.get("authority", "-"),
                        item.get("amount", "-"),
                        item.get("status", "-"),
                    ])
            if rows:
                cols = ["Subject", "Compliance"] if len(rows[0]) == 2 else ["Type", "Details", "Forum/Court", "Amount", "Status"]
                result["sections"].append({
                    "id": "litigations",
                    "title": "Litigations & Disputes",
                    "type": "table",
                    "columns": cols,
                    "rows": rows,
                    "summary": f"{len(rows)} item(s)"
                })

    # 9. CSR Details
    csr = raw.get("csr_details", [])
    if csr and isinstance(csr, list) and isinstance(csr[0], dict):
        data = csr[0].get("data", {})
        if isinstance(data, dict) and data:
            result["sections"].append({
                "id": "csr_details",
                "title": "Corporate Social Responsibility (CSR)",
                "type": "key_value",
                "items": [{"field": _humanize_xbrl_field(k), "value": str(v)} for k, v in data.items()],
                "summary": f"{len(data)} item(s)"
            })

    return result


def _humanize_xbrl_field(field_name):
    """Convert camelCase XBRL field name to human-readable label."""
    # Insert space before uppercase letters
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', field_name)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', s)
    return s


def _linkedin_search_url(name):
    """Generate a LinkedIn search URL for a person's name."""
    clean = name.strip()
    # Remove common titles
    for t in ("Mr.", "Mrs.", "Ms.", "Dr.", "Shri", "Smt.", "Prof."):
        clean = clean.replace(t, "").strip()
    return f"https://www.linkedin.com/search/results/people/?keywords={clean.replace(' ', '%20')}"


def _build_director_map(board_section):
    """Build a dict of name -> row data from a board section."""
    cols = board_section.get("columns", [])
    result = {}
    for row in board_section.get("rows", []):
        if not row:
            continue
        entry = {}
        for i, col in enumerate(cols):
            if i < len(row):
                entry[col] = row[i]
        result[row[0]] = entry
    return result


def _build_committee_member_map(section):
    """Build name -> row data for a committee section."""
    cols = section.get("columns", [])
    result = {}
    for row in section.get("rows", []):
        if not row:
            continue
        entry = {}
        for i, col in enumerate(cols):
            if i < len(row):
                entry[col] = row[i]
        result[row[0]] = entry
    return result


def _compare_meetings(meetings_a, meetings_b, label="Board Meetings"):
    """Compare meeting sections and return detailed change info."""
    rows_a = meetings_a.get("rows", []) if meetings_a else []
    rows_b = meetings_b.get("rows", []) if meetings_b else []
    cols_a = meetings_a.get("columns", []) if meetings_a else []
    cols_b = meetings_b.get("columns", []) if meetings_b else []

    items = []
    count_a = len(rows_a)
    count_b = len(rows_b)

    items.append(f"Meetings held: {count_a} (Report A) vs {count_b} (Report B)")

    if count_b != count_a:
        diff = count_b - count_a
        items.append(f"{'Increased' if diff > 0 else 'Decreased'} by {abs(diff)} meeting(s)")

    # Parse attendance from each meeting
    for i, row in enumerate(rows_b):
        meeting_info = {}
        for j, col in enumerate(cols_b):
            if j < len(row):
                meeting_info[col] = row[j]

        period = meeting_info.get("Period", meeting_info.get("Committee", f"Meeting {i+1}"))
        total = meeting_info.get("Total Directors", meeting_info.get("Total Members", "-"))
        present = meeting_info.get("Directors Present", meeting_info.get("Members Attended", "-"))
        indep = meeting_info.get("Independent Directors", "-")
        quorum = meeting_info.get("Quorum Met", "-")

        detail = f"{period}: {present}/{total} directors present"
        if indep and indep != "-":
            detail += f" ({indep} independent)"
        if quorum and quorum != "-":
            detail += f" | Quorum: {quorum}"
        items.append(detail)

    change_type = "change" if count_a != count_b else "unchanged"
    return {"section": label, "type": change_type, "items": items}


def _compare_key_value_sections(sec_a, sec_b, label):
    """Compare two key_value or table sections generically."""
    items = []

    if not sec_a and sec_b:
        items.append(f"NEW in Report B: {sec_b.get('summary', 'present')}")
        if sec_b.get("type") == "key_value":
            for item in sec_b.get("items", [])[:5]:
                items.append(f"  {item.get('field', '?')}: {item.get('value', '-')}")
        elif sec_b.get("type") == "table":
            for row in sec_b.get("rows", [])[:5]:
                items.append(f"  {' | '.join(str(c) for c in row)}")
        return {"section": label, "type": "addition", "items": items}

    if sec_a and not sec_b:
        items.append(f"REMOVED in Report B (was: {sec_a.get('summary', 'present')})")
        return {"section": label, "type": "removal", "items": items}

    if not sec_a and not sec_b:
        return None

    # Both exist — compare
    if sec_a.get("type") == "key_value" and sec_b.get("type") == "key_value":
        items_a = {i["field"]: i["value"] for i in sec_a.get("items", [])}
        items_b = {i["field"]: i["value"] for i in sec_b.get("items", [])}
        all_keys = set(list(items_a.keys()) + list(items_b.keys()))
        changed = False
        for k in sorted(all_keys):
            va = items_a.get(k)
            vb = items_b.get(k)
            if va != vb:
                changed = True
                if va and not vb:
                    items.append(f"Removed: {k}")
                elif not va and vb:
                    items.append(f"Added: {k} = {vb}")
                else:
                    items.append(f"Changed: {k}: {va} -> {vb}")
        if not changed:
            items.append(f"No changes ({len(items_b)} items)")
        return {"section": label, "type": "change" if changed else "unchanged", "items": items}

    elif sec_a.get("type") == "table" and sec_b.get("type") == "table":
        rows_a_set = set(tuple(str(c) for c in r) for r in sec_a.get("rows", []))
        rows_b_set = set(tuple(str(c) for c in r) for r in sec_b.get("rows", []))
        added = rows_b_set - rows_a_set
        removed = rows_a_set - rows_b_set
        if added or removed:
            for r in sorted(added):
                items.append(f"Added: {' | '.join(r)}")
            for r in sorted(removed):
                items.append(f"Removed: {' | '.join(r)}")
            return {"section": label, "type": "change", "items": items}
        items.append(f"No changes ({len(rows_b_set)} items)")
        return {"section": label, "type": "unchanged", "items": items}

    return None


def compare_governance_reports(report_a, report_b):
    """Compare two parsed governance reports and return structured changes.
    Produces detailed comparison of board, all committees, meetings, fines, litigations."""
    changes = []

    if not report_a or not report_b:
        return changes

    sections_a = {s["id"]: s for s in report_a.get("sections", [])}
    sections_b = {s["id"]: s for s in report_b.get("sections", [])}

    # ── 1. Board of Directors (detailed) ──
    board_a = sections_a.get("board", {})
    board_b = sections_b.get("board", {})

    if board_a or board_b:
        dir_map_a = _build_director_map(board_a) if board_a else {}
        dir_map_b = _build_director_map(board_b) if board_b else {}
        names_a = set(dir_map_a.keys())
        names_b = set(dir_map_b.keys())

        added = names_b - names_a
        removed = names_a - names_b
        retained = names_a & names_b

        board_items = []
        board_items.append(f"Total directors: {len(names_a)} (A) -> {len(names_b)} (B)")

        # New directors with full detail
        for name in sorted(added):
            d = dir_map_b[name]
            category = d.get("Category", "-")
            appointed = d.get("Appointed", "-")
            din = d.get("DIN", "-")
            linkedin = _linkedin_search_url(name)
            board_items.append(
                f"NEW DIRECTOR: {name} | Position: {category} | "
                f"Date of Appointment: {appointed} | DIN: {din} | "
                f"LinkedIn: {linkedin}"
            )

        # Departed directors with full detail
        for name in sorted(removed):
            d = dir_map_a[name]
            category = d.get("Category", "-")
            ceased = d.get("Ceased", "-")
            din = d.get("DIN", "-")
            linkedin = _linkedin_search_url(name)
            board_items.append(
                f"DEPARTED: {name} | Position held: {category} | "
                f"Effective date of cessation: {ceased} | DIN: {din} | "
                f"LinkedIn: {linkedin}"
            )

        # Check for role changes in retained directors
        for name in sorted(retained):
            da = dir_map_a[name]
            db = dir_map_b[name]
            role_changes = []
            for key in ["Category", "Tenure", "Directorships", "Committee Memberships"]:
                va = da.get(key, "-")
                vb = db.get(key, "-")
                if va != vb and vb != "-":
                    role_changes.append(f"{key}: {va} -> {vb}")
            if role_changes:
                board_items.append(f"CHANGED: {name} | {' | '.join(role_changes)}")

        if not added and not removed:
            board_items.append("No director additions or departures")

        change_type = "change" if (added or removed) else "unchanged"
        changes.append({
            "section": "Board of Directors",
            "type": change_type,
            "items": board_items,
        })

    # ── 2. All Committees (NRC, CSR, SRC, Audit, Risk, etc.) ──
    all_section_ids = set(list(sections_a.keys()) + list(sections_b.keys()))
    for sid in sorted(all_section_ids):
        if not sid.startswith("comm_"):
            continue
        ca = sections_a.get(sid, {})
        cb = sections_b.get(sid, {})
        title = (cb or ca).get("title", sid)

        mem_a = _build_committee_member_map(ca) if ca else {}
        mem_b = _build_committee_member_map(cb) if cb else {}
        names_a = set(mem_a.keys())
        names_b = set(mem_b.keys())

        added = names_b - names_a
        removed = names_a - names_b
        retained = names_a & names_b

        items = []
        items.append(f"Members: {len(names_a)} (A) -> {len(names_b)} (B)")

        for name in sorted(added):
            m = mem_b[name]
            role = m.get("Role", "-")
            cat = m.get("Category", "-")
            appt = m.get("Appointed", "-")
            linkedin = _linkedin_search_url(name)
            items.append(
                f"NEW MEMBER: {name} | Role: {role} | Category: {cat} | "
                f"Appointed: {appt} | LinkedIn: {linkedin}"
            )

        for name in sorted(removed):
            m = mem_a[name]
            role = m.get("Role", "-")
            cat = m.get("Category", "-")
            ceased = m.get("Ceased", "-")
            linkedin = _linkedin_search_url(name)
            items.append(
                f"REMOVED: {name} | Role held: {role} | Category: {cat} | "
                f"Cessation: {ceased} | LinkedIn: {linkedin}"
            )

        # Check role changes
        for name in sorted(retained):
            ma = mem_a[name]
            mb = mem_b[name]
            role_changes = []
            for key in ["Role", "Category"]:
                va = ma.get(key, "-")
                vb = mb.get(key, "-")
                if va != vb and vb != "-":
                    role_changes.append(f"{key}: {va} -> {vb}")
            if role_changes:
                items.append(f"ROLE CHANGED: {name} | {' | '.join(role_changes)}")

        if not added and not removed:
            items.append("No membership changes")

        change_type = "change" if (added or removed) else "unchanged"
        if not ca and cb:
            change_type = "addition"
            items.insert(0, f"New committee section in Report B")
        elif ca and not cb:
            change_type = "removal"
            items.insert(0, f"Committee section removed in Report B")

        changes.append({"section": title, "type": change_type, "items": items})

    # ── 3. Board Meetings ──
    meetings_a = sections_a.get("meetings")
    meetings_b = sections_b.get("meetings")
    if meetings_a or meetings_b:
        changes.append(_compare_meetings(meetings_a, meetings_b, "Board Meetings"))

    # ── 4. Committee Meetings ──
    cmeetings_a = sections_a.get("committee_meetings")
    cmeetings_b = sections_b.get("committee_meetings")
    if cmeetings_a or cmeetings_b:
        changes.append(_compare_meetings(cmeetings_a, cmeetings_b, "Committee Meetings"))

    # ── 5. Fines, Penalties & Strictures ──
    fines_change = _compare_key_value_sections(
        sections_a.get("fines"), sections_b.get("fines"),
        "Fines, Penalties & Strictures"
    )
    if fines_change:
        changes.append(fines_change)

    # ── 6. Litigations ──
    lit_change = _compare_key_value_sections(
        sections_a.get("litigations"), sections_b.get("litigations"),
        "Litigations & Disputes"
    )
    if lit_change:
        changes.append(lit_change)

    # ── 7. Related Party Transactions ──
    rpt_change = _compare_key_value_sections(
        sections_a.get("rpt"), sections_b.get("rpt"),
        "Related Party Transactions"
    )
    if rpt_change:
        changes.append(rpt_change)

    # ── 8. CSR Details ──
    csr_change = _compare_key_value_sections(
        sections_a.get("csr_details"), sections_b.get("csr_details"),
        "Corporate Social Responsibility (CSR)"
    )
    if csr_change:
        changes.append(csr_change)

    # ── 9. Affirmations ──
    aff_change = _compare_key_value_sections(
        sections_a.get("affirmations"), sections_b.get("affirmations"),
        "Compliance Affirmations"
    )
    if aff_change:
        changes.append(aff_change)

    return changes


def _scan_company_for_din(symbol, din):
    """Scan a single company's governance reports for the given DIN.
    Returns (director_info_or_None, list_of_appearance_dicts).
    Designed to be called concurrently across many symbols.
    """
    local_director = None
    local_appearances = []
    try:
        reports = fetch_governance_reports_list(symbol)
    except Exception:
        return (None, [])

    for report_meta in reports[:4]:
        rec_id = report_meta.get("recordId", "")
        quarter = report_meta.get("quarter", "")
        if not rec_id:
            continue

        try:
            raw = fetch_governance_report(symbol, rec_id)
        except Exception:
            continue
        if not raw:
            continue

        # Board of Directors
        cobod = raw.get("cobod", [])
        if cobod and isinstance(cobod, list) and isinstance(cobod[0], dict):
            data = cobod[0].get("data", {})
            members = data.get("CompositionBOD", []) or []
            for m in members:
                if not isinstance(m, dict):
                    continue
                member_din = str(m.get("din", ""))
                if member_din == str(din):
                    name = f"{m.get('title', '')} {m.get('directorName', '')}".strip()
                    if not local_director:
                        local_director = {
                            "din": din,
                            "name": name,
                            "category": m.get("category", "-"),
                            "dateOfAppointment": m.get("dateOfAppointment", "-"),
                            "dateOfCessation": m.get("dateOfCessation", "-"),
                            "tenure": m.get("tenure", "-"),
                            "directorships": m.get("noOfDirShp", "-"),
                            "memberships": m.get("noOfMemberships", "-"),
                            "linkedin": _linkedin_search_url(name),
                        }
                    local_appearances.append({
                        "symbol": symbol,
                        "quarter": quarter,
                        "recordId": rec_id,
                        "role": "Board of Directors",
                        "position": m.get("category", "-"),
                        "source": report_meta.get("source", ""),
                    })
                    break

        # Committees — only meaningful if we already identified the director
        coc = raw.get("coc", [])
        if coc and isinstance(coc, list) and isinstance(coc[0], dict) and local_director:
            cdata = coc[0].get("data", {})
            if isinstance(cdata, dict):
                for comm_name, cmembers in cdata.items():
                    if not isinstance(cmembers, list):
                        continue
                    for m in cmembers:
                        if not isinstance(m, dict):
                            continue
                        mname = m.get("name", "")
                        dir_parts = local_director["name"].lower().split()
                        m_parts = mname.lower().split()
                        if len(dir_parts) > 1 and len(m_parts) > 0:
                            if dir_parts[-1] in m_parts or m_parts[-1] in dir_parts:
                                local_appearances.append({
                                    "symbol": symbol,
                                    "quarter": quarter,
                                    "recordId": rec_id,
                                    "role": comm_name,
                                    "position": m.get("chairPersonMember", "Member"),
                                    "source": report_meta.get("source", ""),
                                })

    return (local_director, local_appearances)


def search_din_in_governance(din, symbols, max_workers=12, max_symbols=1500):
    """Search for a DIN across governance reports of MANY companies.

    Runs fetches in a thread pool so that scanning the full known-companies
    universe completes in a reasonable time window for a web request.

    Args:
        din: DIN to search for (string or int).
        symbols: iterable of NSE symbols to search.
        max_workers: concurrency for outbound NSE calls.
        max_symbols: hard cap to protect against runaway scans.

    Returns dict with keys: din, director, appearances.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _warm_session()

    symbols = list(dict.fromkeys([s for s in (symbols or []) if s]))[:max_symbols]
    if not symbols:
        return {"din": din, "director": None, "appearances": []}

    director_info = None
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scan_company_for_din, sym, din): sym for sym in symbols}
        for fut in as_completed(futures):
            try:
                local_director, local_appearances = fut.result()
            except Exception:
                continue
            if local_director and not director_info:
                director_info = local_director
            if local_appearances:
                results.extend(local_appearances)

    seen = set()
    unique_results = []
    for r in results:
        key = (r["symbol"], r["quarter"], r["role"])
        if key not in seen:
            seen.add(key)
            unique_results.append(r)

    # Present most recent quarter first within each company for readability.
    unique_results.sort(key=lambda r: (r["symbol"], r.get("quarter", "")), reverse=False)

    return {
        "din": din,
        "director": director_info,
        "appearances": unique_results,
    }
