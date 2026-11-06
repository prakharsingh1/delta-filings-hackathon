"""Public information pages: product, solutions, pricing, security, FAQ, privacy.

Everything here is static copy plus figures read from the rulebook, so the
numbers on the page cannot drift from what the product tracks. No login.
"""
from collections import Counter

from flask import Blueprint, Response, abort, render_template

import rulebook as rb

SOLUTIONS = {
    "company-secretary": {
        "label": "Company secretaries",
        "short": "In-house CS at one company or a small group",
        "headline": "Know what your company owes, and what being late costs.",
        "lede": "Answer a few questions about the company and the calendar builds itself: ROC, tax, GST, labour, FEMA, and SEBI if you are listed. Log a director's appointment and DIR-12 is dated from it.",
        "day": [
            ("Monday", "Open the calendar. Overdue items sit at the top with the late fee next to each one."),
            ("A board meeting is coming", "Pick the agenda items. Delta drafts the notice, the resolutions and a minutes outline as one Word file, and tells you the latest date the notice can go out."),
            ("A director joins", "Log the event. DIR-12, MBP-1 follow-ups and, for a listed company, the Reg 30 disclosure appear with dates."),
            ("Month end", "Tick off TDS, GST and PF as you pay them. Anything you forgot shows its estimated cost."),
        ],
        "modules": [("Workspace", "Compliance calendar per company, with events, tasks and a penalty meter."), ("Meetings and registers", "Notice, agenda, resolutions, minutes. Six statutory registers with version history."), ("Draft", "Exchange letters and Companies Act documents, in your company's own style."), ("Regulatory news", "SEBI and RBI updates tagged by topic. Add any item as a task."), ("Document requests", "Send a link to whoever holds the papers. They upload without an account.")],
        "steps": [("Create workspace", "Pick Company secretary. Name the company and its type."), ("Answer the profile", "GST registered? Deducts TDS? Employees? FDI? Each yes switches on the filings that apply."), ("Review the calendar", "Items before today appear as overdue. If they were filed before you started, clear them in one click."), ("Add directors", "Enter DINs and DSC expiry dates. DIR-3 KYC and DSC renewals join the calendar.")],
        "not": ["It does not file on the MCA portal for you. You prepare the form, upload it and sign it with your DSC as before. Delta tracks the SRN and the date.", "It does not replace your accounting or payroll software."],
    },
    "practising-cs": {
        "label": "Practising CS firms",
        "short": "Firms with a client list to keep on track",
        "headline": "Every client's calendar in one place, sorted by what is late.",
        "lede": "Add clients one at a time or paste a list. Each gets its own calendar built from its profile, and the home screen merges them so the most overdue item across the whole practice comes first.",
        "day": [
            ("First thing", "The practice view lists every open filing across every client, oldest first, with the estimated late fee."),
            ("Chasing papers", "Send a client a private link with a checklist. It goes by WhatsApp or email, and the reminder repeats only what is still missing."),
            ("Before a client's board meeting", "Build the meeting pack from the agenda library and see which items need MGT-14 for that company type."),
            ("Quarter end", "Filter by regime or by client and export the calendar to CSV for your own tracker."),
        ],
        "modules": [("Practice view", "All clients merged, with exposure and on-time rate per client."), ("Bulk import", "Paste name, type and CIN. State is read from the CIN."), ("Document requests", "Checklist templates for annual filing, director appointment, allotment, charge creation and more."), ("Meetings and registers", "Per client, with .docx export."), ("Draft", "Covering letters and resolutions for the common ROC forms.")],
        "steps": [("Create workspace", "Pick Practising CS or firm."), ("Import your clients", "One line each: name, type, CIN. Duplicates and bad CINs are flagged before anything is created."), ("Set each profile", "A handful of yes or no answers per client."), ("Send the first document request", "Choose a checklist and share the link.")],
        "not": ["It is not yet a team product. One login covers one practice; seats and roles for staff are not built.", "It does not file on the MCA portal. It does not yet export the MCA offline utility for AOC-4 or MGT-7."],
    },
    "listed-company": {
        "label": "Listed companies",
        "short": "Compliance officers and CS at NSE and BSE listed companies",
        "headline": "The LODR calendar, the insider-trading record and the drafts in one place.",
        "lede": "SEBI deadlines with the stock-exchange fine next to each, a structured digital database that shows tampering, and exchange letters drafted from your own past filings.",
        "day": [
            ("Quarter end", "Close the trading window from the quarter-end date. Pre-clearance requests are refused while it is closed."),
            ("Before results", "Record who received the results as UPSI, with PAN and time. The log is append-only and hash-chained."),
            ("Board meeting scheduled", "Reg 29 prior intimation is dated at least two clear working days before the meeting."),
            ("After the meeting", "Reg 30 is dated the same day, with the outcome letter ready to draft."),
        ],
        "modules": [("SEBI calendar", "Reg 33, 31, 27, 13, 23(9), 24A, 34, 44, BRSR, SAST Reg 31(4), PIT and depositories certificates."), ("Insider trading", "Designated persons, trading window, pre-clearance valid for seven trading days, and the SDD."), ("Disclosures", "Your exchange filings, fetched and compared with the last one."), ("Draft", "About 100 document types including Reg 30, 29, 33, 44 and PIT disclosures."), ("Regulatory news", "SEBI circulars and orders tagged by topic.")],
        "steps": [("Create workspace", "Pick Compliance officer and choose your sector."), ("Add your NSE symbol", "Delta fetches your filings and builds the LODR calendar."), ("Set the profile", "Top 100 or top 1,000, SME-listed, exempt under Reg 15(2): each changes which rules apply."), ("Set up insider trading", "Add designated persons and open the first UPSI item.")],
        "not": ["It does not yet confirm a filing from the exchange acknowledgement on the new workspace calendar. The older listed-company tracker does check NSE and BSE announcements; joining the two is in progress.", "It does not produce exchange XBRL."],
    },
    "risk-officer": {
        "label": "Risk officers",
        "short": "Credit and operational risk at banks, NBFCs and corporates",
        "headline": "Watch borrowers, vendors and investees for early warning signs.",
        "lede": "List the companies you are exposed to, with the amount. Delta checks each against exchange filings, SEBI orders, IBBI insolvency announcements and share-price shocks, and scores them from 0 to 100 with the reasons shown.",
        "day": [
            ("Morning", "Open the watch list. Exposure at risk, and names that moved since last month, are at the top."),
            ("An alert fires", "Acknowledge it, assign it, resolve or dismiss it with a note. Every action is logged."),
            ("A name looks odd", "Open it to see which signals add up to the score, with dates and sources."),
            ("Committee day", "Print the risk report: exposure by band and category, new signals, open alerts and the method."),
        ],
        "modules": [("Watch list", "Score, band, 30-day change and exposure per counterparty."), ("Alerts", "A queue with assignees, status and an audit log."), ("Director network", "Where one director sits on several of your counterparties' boards."), ("Committee report", "Printable, with CSV export."), ("Regulatory news", "SEBI orders naming a counterparty are matched automatically.")],
        "steps": [("Create workspace", "Pick Risk officer."), ("Paste your counterparties", "Name, NSE symbol or CIN, type and exposure. Commas or tabs both work."), ("Wait for the first scan", "Listed names read their filings; every name is checked against IBBI and SEBI."), ("Set an owner on the first alert", "Assign it and add a note.")],
        "not": ["It does not yet read MCA data on unlisted companies (charges, filing defaults, strike-off). That needs a licensed data source and is not included.", "The score ranks alerts for attention. It is not a credit rating."],
    },
}

FAQ = [
    ("Scope", [
        ("Does Delta file anything on the MCA portal for me?", "<p>No. MCA has no public filing API, and automating the portal would break its terms. You prepare the form, upload it and sign with your DSC as you do today. Delta dates the filing, tells you what lateness costs, and keeps the SRN and filing date.</p>"),
        ("Which laws does the calendar cover?", "<p>Companies Act, LLP Act, SEBI (LODR, PIT, SAST, depositories), income tax and TDS, GST, labour (PF, ESI, professional tax in Maharashtra and Karnataka, POSH, labour welfare in two states) and FEMA/RBI. The <a href=\"/product#coverage\">product page</a> lists the count and the main forms.</p>"),
        ("Does it work for unlisted companies?", "<p>Yes. Private, public unlisted, OPC, Section 8 and LLP all get a calendar. SEBI rules apply only when you mark the company as listed.</p>"),
        ("What about other states' professional tax or labour welfare?", "<p>Only Maharashtra and Karnataka are built. Other states are not in the calendar yet; add them as tasks until they are.</p>"),
    ]),
    ("Accuracy", [
        ("How accurate are the due dates?", "<p>Each rule carries its law, its source link and the date it was last reviewed. The rulebook was fact-checked against official sources on 29 September 2026, and every correction was checked a second time independently. Rules still change often, so each page says when it was last reviewed.</p>"),
        ("Are the late fees exact?", "<p>They are indicative. ROC additional fees depend on the form and your share capital, GST fees on turnover, and interest on the tax involved. The page shows its working: for example, 2× the normal fee of ₹300 for 15 days late. Where an amount depends on facts we do not have, it says so instead of guessing.</p>"),
        ("The AGM was held early. When does AOC-4 fall due?", "<p>Within 30 days of the AGM, and MGT-7 within 60. The calendar first assumes a 30 September AGM, so AOC-4 shows 30 October. Open the AGM row, choose <b>Record AGM</b> and enter the date it was held, and AOC-4, MGT-7, ADT-1, MGT-15, CSR-2 and AOC-4 CFS are re-dated from it. Marking an AGM meeting as held in the meetings module does the same.</p>"),
    ]),
    ("Data and trust", [
        ("Where is my data stored?", "<p>On a Railway server in US West (California). Hosting in an India region is not in place. The <a href=\"/security\">security page</a> lists what is and is not in place today.</p>"),
        ("Is the insider-trading record tamper-proof?", "<p>Tamper-evident, not tamper-proof. Entries cannot be edited or deleted through the app, and each stores a hash of the one before it, so a change anywhere breaks the chain and the page names the first broken entry. Someone with direct access to the database could rebuild the chain, so we advise recording the head hash in your board or audit-committee minutes.</p>"),
        ("Do you use my documents to train AI?", "<p>We do not train models. AI features send the text you ask about to a model provider (Google Gemini, or OpenRouter for some models). Those providers have their own terms; do not use AI features on text you may not share with them.</p>"),
    ]),
    ("Using it", [
        ("Can my whole team use it?", "<p>Not yet. Accounts are per person, and drafts can be shared with up to five collaborators. Seats and roles for a team are not built.</p>"),
        ("Can a client upload documents without an account?", "<p>Yes. A document request gives a private link. The client uploads against each checklist item; only you see the files.</p>"),
        ("What does it cost?", "<p>It is free during the beta. See <a href=\"/pricing\">pricing</a> for how we plan to charge: per company, with unlimited users.</p>"),
        ("Can I export my data?", "<p>Calendars, registers, insider-trading logs and risk lists export to CSV, and meeting packs to Word.</p>"),
    ]),
]


def _counts():
    rules = rb.RULES + [rb.DIR3KYC_RULE]
    c = Counter(r["regime"] for r in rules)
    return {"total": len(rules), "by_regime": dict(c), "reviewed": rb.RULEBOOK_REVIEWED}


def create_blueprint(login_required, current_user_id, current_user):
    bp = Blueprint("site", __name__)

    def _page(tpl, nav, **ctx):
        return render_template(tpl, nav_active=nav, counts=_counts(), **ctx)

    @bp.route("/product")
    def product():
        return _page("site_product.html", "product")

    @bp.route("/solutions")
    def solutions_index():
        return _page("site_solutions.html", "solutions", solutions=SOLUTIONS)

    @bp.route("/solutions/<slug>")
    def solution(slug):
        s = SOLUTIONS.get(slug)
        if not s:
            abort(404)
        return _page("site_solution.html", "solutions", s=s, slug=slug, others={k: v for k, v in SOLUTIONS.items() if k != slug})

    @bp.route("/pricing")
    def pricing():
        return _page("site_pricing.html", "pricing")

    @bp.route("/security")
    def security():
        return _page("site_security.html", "security")

    @bp.route("/faq")
    def faq():
        return _page("site_faq.html", "faq", faq=FAQ)

    @bp.route("/privacy")
    def privacy():
        return _page("site_privacy.html", "privacy")

    @bp.route("/sitemap.xml")
    def sitemap():
        """Every public page, built from the same lists the pages render from."""
        from homepage_data import SITE_URL
        import blog_posts, tools_data
        paths = (["/", "/product", "/solutions"] + [f"/solutions/{k}" for k in SOLUTIONS]
                 + ["/pricing", "/security", "/faq", "/privacy", "/about", "/blog"]
                 + [f"/blog/{p['slug']}" for p in blog_posts.POSTS]
                 + ["/tools"] + [f"/tools/{t['slug']}" for t in tools_data.TOOLS])
        body = "".join(f"<url><loc>{SITE_URL}{path}</loc></url>" for path in paths)
        xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
        return Response(xml, mimetype="application/xml")

    @bp.route("/robots.txt")
    def robots():
        from homepage_data import SITE_URL
        lines = ["User-agent: *", "Allow: /"] + [f"Disallow: {p}" for p in (
            "/api/", "/dashboard", "/workspace", "/draft", "/sdd", "/risk", "/requests", "/news",
            "/company/", "/prices", "/watchlist", "/intelligence", "/onboarding", "/checkout", "/demo",
            "/signin", "/signup", "/_preview/")] + [f"Sitemap: {SITE_URL}/sitemap.xml", ""]
        return Response("\n".join(lines), mimetype="text/plain")

    return bp
