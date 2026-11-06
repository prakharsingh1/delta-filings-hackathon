"""Read-only data for the Ivory homepage.

Everything here is derived from the existing rulebook and filing archive so the
public page cannot drift from what the product actually tracks. Nothing is
written back.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import rulebook as rb
from dashboard_view import build_dashboard_data
from demo import demo_mode_enabled

REGIME_ORDER = ["Companies Act", "SEBI", "Income Tax", "GST", "Labour", "FEMA / RBI", "LLP Act"]

# Canonical origin for the public site; metadata and the sitemap are built from it.
SITE_URL = "https://deltafiling.tech"
HOME_TITLE = "Delta Filings — One desk for every governance obligation"
HOME_DESCRIPTION = ("Delta Filings follows public disclosures at the source, maps the obligations that apply to each "
                    "company and prepares the drafts that follow. Built for Indian company secretaries, legal and finance teams.")

# Ivory keeps the preserved explorations reachable by name where they exist.
HOME_TEMPLATES = {"ivory": "home_ivory.html", "lemon": "home_lemon.html"}
_TEMPLATES = Path(__file__).resolve().parent / "templates"

# The financial year the timeline is drawn against. Only used for day maths.
_FY_START = date(2026, 4, 1)
_FY_DAYS = 365

# Curated rows for the governance-year timeline, in display order. Each entry is
# a rulebook id plus a note on how the schedule is drawn. Names, forms, laws and
# dates all come from the rulebook itself.
_TIMELINE = [
    ("IN-SEBI-REG33", "quarter"),
    ("IN-SEBI-REG31", "quarter"),
    ("IN-SEBI-REG24A", "point"),
    ("IN-SEBI-REG23", "point"),
    ("IN-SEBI-REG34", "point"),
    ("IN-MCA-AGM", "window"),
    ("IN-MCA-AOC4", "after_agm"),
    ("IN-MCA-MGT7", "after_agm"),
    ("IN-MCA-DPT3", "point"),
    ("IN-IT-ADV", "point"),
    ("IN-IT-TDSRET", "point"),
    ("IN-IT-ITR", "point"),
    ("IN-GST-R9", "point"),
    ("IN-GST-3B", "monthly"),
    ("IN-LAB-PF", "monthly"),
]


def _all_rules():
    return rb.RULES + [rb.DIR3KYC_RULE]


def _fy_date(month, day):
    return date(_FY_START.year if month >= 4 else _FY_START.year + 1, month, day)


def _pos(d):
    return round(max(0.0, min(1.0, (d - _FY_START).days / _FY_DAYS)), 4)


def _label(d):
    return d.strftime("%-d %b")


def compact_rules():
    """The fields the browser needs to replay `rulebook.applies` for the mapper."""
    out = []
    for r in _all_rules():
        out.append({
            "id": r["id"], "regime": r["regime"], "name": r["name"], "form": r["form"],
            "kind": r["schedule"]["kind"], "types": r["types"], "flags": r["flags"],
            "any": r["any_flags"], "not": r["not_flags"], "states": r["states"],
        })
    return out


def timeline_rows():
    rules = {r["id"]: r for r in _all_rules()}
    quarter_ends = [date(_FY_START.year, 3, 31), date(_FY_START.year, 6, 30),
                    date(_FY_START.year, 9, 30), date(_FY_START.year, 12, 31)]
    quarter_names = ["Q4 (prior year)", "Q1", "Q2", "Q3"]
    agm = _fy_date(9, 30)
    rows = []
    for rule_id, mode in _TIMELINE:
        r = rules[rule_id]
        s = r["schedule"]
        marks = []
        if mode == "quarter":
            for q_end, q_name in zip(quarter_ends, quarter_names):
                days = s.get("q4_days", s["days"]) if q_end.month == 3 else s["days"]
                due = q_end + timedelta(days=days)
                marks.append({"type": "bar", "start": _pos(q_end), "end": _pos(due),
                              "title": f"{q_name} · due {_label(due)}"})
        elif mode == "window":
            marks.append({"type": "bar", "start": 0.0, "end": _pos(_fy_date(s["month"], s["day"])),
                          "title": f"Within six months of year end · by {_label(_fy_date(s['month'], s['day']))}"})
        elif mode == "after_agm":
            due = _fy_date(s["month"], s["day"])
            marks.append({"type": "bar", "start": _pos(agm), "end": _pos(due),
                          "title": f"{s['after_agm']} days after the AGM · by {_label(due)}"})
        elif mode == "monthly":
            for month in list(range(4, 13)) + [1, 2, 3]:
                due = _fy_date(month, s["day"])
                marks.append({"type": "tick", "start": _pos(due), "end": _pos(due),
                              "title": f"Every month · {_label(due)}"})
        elif mode == "point":
            kind = s["kind"]
            if kind == "annual":
                due = _fy_date(s["month"], s["day"])
                marks.append({"type": "point", "start": _pos(due), "end": _pos(due), "title": f"Due {_label(due)}"})
            elif kind == "half_fixed":
                for month, day, period in s["dates"]:
                    due = _fy_date(month, day)
                    marks.append({"type": "point", "start": _pos(due), "end": _pos(due), "title": f"{period} · {_label(due)}"})
            elif kind == "fy_dates":
                for month, day, period in s["dates"]:
                    due = _fy_date(month, day)
                    marks.append({"type": "point", "start": _pos(due), "end": _pos(due), "title": f"{period} · {_label(due)}"})
            elif kind == "quarterly_fixed":
                for q, (month, day) in sorted(s["dates"].items(), key=lambda item: (_fy_date(*item[1]))):
                    due = _fy_date(month, day)
                    marks.append({"type": "point", "start": _pos(due), "end": _pos(due), "title": f"Q{q} · {_label(due)}"})
        note = ""
        overrides = s.get("overrides") or {}
        if overrides:
            fy, when = next(iter(overrides.items()))
            note = f"{fy}: extended to {date.fromisoformat(when).strftime('%-d %b %Y')}"
        rows.append({"id": r["id"], "regime": r["regime"], "name": r["name"], "form": r["form"],
                     "law": r["law"], "marks": marks, "note": note})
    return rows


def rulebook_payload():
    rules = _all_rules()
    counts = {}
    for r in rules:
        counts[r["regime"]] = counts.get(r["regime"], 0) + 1
    reviewed = date.fromisoformat(rb.RULEBOOK_REVIEWED)
    months = [date(_FY_START.year if m >= 4 else _FY_START.year + 1, m, 1).strftime("%b")
              for m in list(range(4, 13)) + [1, 2, 3]]
    return {
        "rule_count": len(rules),
        "event_count": sum(1 for r in rules if r["schedule"]["kind"] == "event"),
        "regimes": [{"name": name, "count": counts.get(name, 0)} for name in REGIME_ORDER if counts.get(name)],
        "reviewed_label": f"{reviewed.day} {reviewed.strftime('%B %Y')}",
        # Rendered inside a <script> block, so a closing tag can never terminate it early.
        "rules_json": json.dumps(compact_rules(), separators=(",", ":")).replace("</", "<\\/"),
        "timeline": timeline_rows(),
        "months": months,
        "fy_label": f"FY {_FY_START.year}–{str(_FY_START.year + 1)[2:]}",
        "today_pos": _pos(date.today()),
    }


# Real screens of the existing workspace, captured locally at 1440 by 816.
STAGE_TABS = [
    {"key": "dashboard", "label": "Dashboard", "icon": "grid", "src": "/static/redesign/ivory-dashboard.png", "webp": "/static/redesign/ivory-dashboard.webp",
     "alt": "Delta Filings dashboard: filings tracked, new this week, companies watched, disclosure activity and the FMCG sector pulse",
     "caption": "Your watchlist, disclosure activity and sector pulse at a glance.", "href": "/dashboard"},
    {"key": "disclosures", "label": "Disclosures", "icon": "file", "src": "/static/redesign/ivory-disclosures.png", "webp": "/static/redesign/ivory-disclosures.webp",
     "alt": "Disclosure library for Hindustan Unilever: 1,712 filings across 21.8 years with tag filters, summaries and change views",
     "caption": "Every exchange filing for a company, tagged, searchable and compared with the one before.", "href": "/company/HINDUNILVR?tab=announcements"},
    {"key": "rulebook", "label": "Rulebook", "icon": "book", "src": "/static/redesign/ivory-rulebook.png", "webp": "/static/redesign/ivory-rulebook.webp",
     "alt": "Rulebook: 102 obligations with form, law, due date, penalty and the entity types each applies to",
     "caption": "Every recurring and event-based obligation the workspace tracks, with form, section and penalty.", "href": "/workspace/rulebook"},
    {"key": "drafts", "label": "Filing desk", "icon": "pen", "src": "/static/redesign/ivory-filing-desk.png", "webp": "/static/redesign/ivory-filing-desk.webp",
     "alt": "Filing desk: choose a company, then a filing type, reference and input, edit and preview; previous drafts listed below",
     "caption": "Prepare, review and share statutory correspondence, then sign it off in your own words.", "href": "/draft"},
]


def _featured(filings, limit=6):
    """A short, varied slice of the newest filings for the mini windows."""
    seen, out = set(), []
    for row in filings:
        key = (row["company"], row["title"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) == limit:
            break
    return out


def seo_payload():
    """Canonical, social-card and structured-data values for the homepage."""
    org = {"@type": "Organization", "@id": f"{SITE_URL}/#organization", "name": "Delta Filings",
           "url": f"{SITE_URL}/", "logo": f"{SITE_URL}/static/redesign/ivory-logo-512.png",
           "email": "hi@deltafiling.tech"}
    graph = {"@context": "https://schema.org", "@graph": [
        org,
        {"@type": "WebSite", "@id": f"{SITE_URL}/#website", "url": f"{SITE_URL}/", "name": "Delta Filings",
         "publisher": {"@id": org["@id"]}},
        {"@type": "SoftwareApplication", "name": "Delta Filings", "url": f"{SITE_URL}/",
         "applicationCategory": "BusinessApplication", "operatingSystem": "Web browser",
         "description": HOME_DESCRIPTION,
         "offers": {"@type": "Offer", "price": "0", "priceCurrency": "INR", "description": "Free during the beta"},
         "publisher": {"@id": org["@id"]}},
    ]}
    return {"url": f"{SITE_URL}/", "title": HOME_TITLE, "description": HOME_DESCRIPTION,
            "image": f"{SITE_URL}/static/redesign/ivory-og.png",
            "image_alt": "Delta Filings: one desk for every governance obligation",
            "jsonld": json.dumps(graph, separators=(",", ":")).replace("</", "<\\/")}


def select_homepage(theme):
    """Template name and extra context for the public homepage.

    `theme` is the raw query value; unknown or missing values fall back to Ivory
    so the preserved explorations stay reachable only by name.
    """
    template = HOME_TEMPLATES.get(theme or "ivory", "home_studio.html")
    if not (_TEMPLATES / template).exists():
        template = "home_ivory.html"
    extra = {}
    if template in ("home_ivory.html", "home_lemon.html"):
        extra["archive"] = build_dashboard_data()
    if template == "home_ivory.html":
        extra["rulebook"] = rulebook_payload()
        # With demo mode on, anonymous visitors can open the real screens. With it
        # off (production), every "open the product" link leads to the free sign-up
        # or the public product page instead of a sign-in wall or a 404.
        demo = demo_mode_enabled()
        extra["demo"] = demo
        extra["cta"] = ({"url": "/dashboard", "label": "Open the dashboard"} if demo
                        else {"url": "/signup", "label": "Create your workspace"})
        extra["links"] = ({"workspace": "/dashboard", "library": "/company/HINDUNILVR?tab=announcements", "drafts": "/draft"} if demo
                          else {"workspace": "/product", "library": "/product", "drafts": "/product"})
        extra["stage_tabs"] = [dict(tab, href=tab["href"] if demo else "/signup") for tab in STAGE_TABS]
        extra["seo"] = seo_payload()
        extra["company_names"] = {c["symbol"]: c["name"] for c in extra["archive"]["companies"]}
        extra["company_logos"] = {c["symbol"]: c["logo"] for c in extra["archive"]["companies"]}
        extra["featured_filings"] = _featured(extra["archive"]["filings"])
        # The local comparison bar only makes sense where the other explorations exist.
        extra["comparisons"] = (_TEMPLATES / "home_lemon.html").exists()
    return template, extra
