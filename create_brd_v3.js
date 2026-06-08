/**
 * Delta Filings — Business Requirements Document (BRD) generator, v3.
 *
 * v3 supersedes v2. Where v2 described an aspirational FastAPI + Postgres +
 * Next.js architecture, v3 documents the platform as it is actually shipping
 * on Railway today: Flask + SQLite (WAL) + Jinja + Gunicorn, with Supabase
 * auth, Razorpay billing, Gemini AI, Telegram alerts and a Tailwind UI that
 * now ships with a full dark-mode + animation refresh.
 *
 * Run:    node create_brd_v3.js
 * Output: Delta_Filings_BRD_v3.docx
 */

const fs = require("fs");
const path = require("path");
const {
  Document,
  Packer,
  Paragraph,
  TextRun,
  Table,
  TableRow,
  TableCell,
  Header,
  Footer,
  AlignmentType,
  LevelFormat,
  HeadingLevel,
  BorderStyle,
  WidthType,
  ShadingType,
  PageNumber,
  PageBreak,
  TabStopType,
  TabStopPosition,
  TableOfContents,
  VerticalAlign,
} = require("/opt/homebrew/lib/node_modules/docx");

// ---------- Theme ----------
const BRAND = "1F4E79";
const GREY_BORDER = "BFBFBF";
const GREY_SOFT = "F2F2F2";
const TEXT_MUTED = "595959";

const border = { style: BorderStyle.SINGLE, size: 4, color: GREY_BORDER };
const cellBorders = { top: border, bottom: border, left: border, right: border };

// ---------- Helpers ----------
const p = (text, opts = {}) =>
  new Paragraph({
    spacing: { after: 120, line: 300 },
    ...opts,
    children: [new TextRun({ text, ...(opts.run || {}) })],
  });

const h1 = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 180 },
    children: [new TextRun({ text })],
  });

const h2 = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 280, after: 140 },
    children: [new TextRun({ text })],
  });

const h3 = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 200, after: 100 },
    children: [new TextRun({ text })],
  });

const bullet = (text, level = 0) =>
  new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { after: 80, line: 280 },
    children: [new TextRun(text)],
  });

const numbered = (text, level = 0) =>
  new Paragraph({
    numbering: { reference: "numbers", level },
    spacing: { after: 80, line: 280 },
    children: [new TextRun(text)],
  });

const cell = (content, opts = {}) => {
  const children = Array.isArray(content)
    ? content
    : [
        new Paragraph({
          spacing: { after: 40, line: 280 },
          children: [
            new TextRun({
              text: String(content ?? ""),
              bold: opts.bold,
              color: opts.color,
              size: opts.size,
            }),
          ],
        }),
      ];
  return new TableCell({
    borders: cellBorders,
    width: { size: opts.width || 0, type: WidthType.DXA },
    shading: opts.fill
      ? { fill: opts.fill, type: ShadingType.CLEAR, color: "auto" }
      : undefined,
    verticalAlign: VerticalAlign.CENTER,
    margins: { top: 80, bottom: 80, left: 140, right: 140 },
    children,
  });
};

const headerRow = (labels, widths) =>
  new TableRow({
    tableHeader: true,
    children: labels.map((label, i) =>
      cell(label, { bold: true, color: "FFFFFF", fill: BRAND, width: widths[i] }),
    ),
  });

const dataRow = (values, widths, alt = false) =>
  new TableRow({
    children: values.map((v, i) =>
      cell(v, { width: widths[i], fill: alt ? GREY_SOFT : undefined }),
    ),
  });

const table = (headers, rows, widths) => {
  const body = [headerRow(headers, widths)];
  rows.forEach((r, idx) => body.push(dataRow(r, widths, idx % 2 === 1)));
  return new Table({
    width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA },
    columnWidths: widths,
    rows: body,
  });
};

const spacer = () =>
  new Paragraph({ spacing: { after: 240 }, children: [new TextRun("")] });

// ---------- Content ----------

const coverPage = [
  new Paragraph({
    spacing: { before: 2400, after: 200 },
    alignment: AlignmentType.CENTER,
    children: [
      new TextRun({ text: "Δ  DELTA FILINGS", bold: true, size: 64, color: BRAND }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [
      new TextRun({
        text: "Corporate Governance Intelligence Platform",
        size: 30,
        color: TEXT_MUTED,
        italics: true,
      }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 2400, after: 120 },
    children: [
      new TextRun({ text: "Business Requirements Document", bold: true, size: 44 }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 2400 },
    children: [
      new TextRun({ text: "Version 3.0  —  May 2026", size: 24, color: TEXT_MUTED }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1200 },
    children: [
      new TextRun({ text: "Prepared by:  Delta Filings Product Team", size: 22, color: TEXT_MUTED }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [
      new TextRun({
        text: "Reflects the production platform live at https://delta-filings.up.railway.app",
        size: 20,
        italics: true,
        color: TEXT_MUTED,
      }),
    ],
  }),
  new Paragraph({ children: [new PageBreak()] }),
];

const docControl = [
  h1("Document Control"),

  h2("Revision History"),
  table(
    ["Version", "Date", "Author", "Summary of Changes"],
    [
      ["1.0", "2026-02-18", "Product Team", "Initial release — v1 platform on Flask + SQLite."],
      ["2.0", "2026-04-23", "Product Team", "Aspirational re-architecture spec (FastAPI + Postgres + Next.js)."],
      ["3.0", "2026-05-27", "Product Team",
        "Re-baselined to actual production state on Railway: Flask + SQLite stack, " +
        "Razorpay billing live, Draft Engine, Industry Intel, Telegram alerts, SEBI " +
        "enforcement scraper, MCA director history, XBRL parsing, Governance reports, " +
        "dark-mode + animation UX refresh."],
    ],
    [1100, 1400, 1800, 5060],
  ),

  spacer(),

  h2("Approvers"),
  table(
    ["Name", "Role", "Organisation", "Signed On"],
    [
      ["Prakhar Singh", "Product Owner", "Delta Filings", "2026-05-27"],
      ["Engineering Lead", "Technical Sign-off", "Delta Filings", "2026-05-27"],
      ["Compliance Advisor", "Domain Sign-off", "Delta Filings", "2026-05-27"],
    ],
    [2400, 2400, 2400, 2160],
  ),

  spacer(),

  h2("Distribution List"),
  table(
    ["Recipient", "Role", "Access Level"],
    [
      ["Executive Sponsor", "Strategic oversight", "Full"],
      ["Engineering Team", "Implementation", "Full"],
      ["QA Team", "Validation", "Full"],
      ["External Auditor", "Compliance review", "Read-only"],
    ],
    [3120, 3120, 3120],
  ),

  new Paragraph({ children: [new PageBreak()] }),
];

const tocPage = [
  h1("Table of Contents"),
  new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
  new Paragraph({ children: [new PageBreak()] }),
];

const section1 = [
  h1("1.  Executive Summary"),
  p(
    "Delta Filings is a corporate governance intelligence platform for Indian listed companies. It aggregates statutory disclosures from NSE, MCA and SEBI; layers AI-generated summaries, draft-letter generation and compliance tracking on top; and surfaces the result through a unified web application that any investor, compliance officer, analyst or legal professional can use without specialised training."
  ),
  p(
    "This Business Requirements Document (v3) re-baselines the product against the platform that is actually live in production at https://delta-filings.up.railway.app. It documents nine production modules — Dashboard, Filings, Filing Diff, Draft Engine, Stock Prices, Price Alerts, Compliance, Governance & XBRL, DIN Search and Industry Intelligence — along with the Razorpay-backed access model, Supabase-managed authentication, Gemini-powered AI workflows and Telegram alert delivery that support them."
  ),
  p(
    "v3 also formalises the recent user-experience refresh, in which the platform's dark mode was rebuilt to a near-black, low-blue palette, the Tailwind-based light surfaces were systematically overridden for dark contexts, and a global animation pass introduced staggered card entrances, hover lifts, frosted-glass headers, smooth theme transitions and a reduced-motion fallback."
  ),
];

const section2 = [
  h1("2.  Business Context and Problem Statement"),

  h2("2.1  The Problem"),
  p(
    "Indian listed companies generate thousands of regulatory disclosures every week across NSE, BSE and MCA. The data is fragmented across multiple portals, served as inconsistent PDFs, and offers no structured way to track directors across companies, monitor compliance obligations, or detect material changes between successive filings. Investors, compliance officers, analysts and legal teams routinely spend hours assembling information that should take minutes."
  ),

  h2("2.2  Who Is Affected"),
  bullet("Retail and institutional investors tracking corporate events."),
  bullet("Compliance officers managing LODR, Companies Act and SEBI obligations."),
  bullet("Equity research analysts preparing coverage notes and governance reviews."),
  bullet("Legal and audit teams conducting director and company due-diligence."),
  bullet("Corporate secretaries who need to draft routine SEBI letters efficiently."),

  h2("2.3  Current State (May 2026)"),
  p(
    "Delta Filings v3 is in paid production. The platform tracks 16 industries, ingests NSE corporate filings on demand, generates AI summaries, supports an end-to-end SEBI letter drafting workflow with team collaboration, runs a 60-second price-alert poller during IST market hours, and delivers alerts to users via Telegram. Access is gated behind a single ₹4,999 annual fee charged through Razorpay, with a referral-code fallback for invited users."
  ),

  h2("2.4  Opportunity"),
  p(
    "A structured, real-time governance intelligence layer for Indian capital markets remains under-served. Delta Filings has now demonstrated commercial viability at the individual-professional price point and is positioned to expand into team / enterprise tiers, additional asset classes, and adjacent regulated workflows (audit, internal compliance, M&A diligence)."
  ),
];

const section3 = [
  h1("3.  Business Objectives and Success Metrics"),

  h2("3.1  Strategic Objectives"),
  numbered("Establish Delta Filings as the default research surface for Indian corporate filings."),
  numbered("Reduce time-to-insight on any listed company from hours to minutes."),
  numbered("Build a defensible data layer combining filings, directors, compliance and enforcement that compounds over time."),
  numbered("Convert the existing single-user ₹4,999 tier into a scalable revenue base, with team / enterprise tiers introduced when justified by demand."),

  h2("3.2  Key Performance Indicators"),
  table(
    ["KPI", "Definition", "v3 Target", "Cadence"],
    [
      ["Monthly Active Users", "Unique signed-in users per month", "1,500", "Monthly"],
      ["Paid Conversion", "Free signups converting to paid in quarter", "≥ 6%", "Quarterly"],
      ["Filings Freshness", "Median lag between NSE publication and ingestion", "≤ 10 min", "Weekly"],
      ["Draft Time-to-Send", "Median time from \"new draft\" to \"export PDF\"", "≤ 8 min", "Monthly"],
      ["Alert Delivery (p95)", "Time from threshold trigger to Telegram receipt", "≤ 90 s", "Continuous"],
      ["Compliance Coverage", "% of watchlisted companies with refreshed obligations", "≥ 95%", "Weekly"],
      ["Week-4 Retention", "% of new paid users active in week 4", "≥ 50%", "Monthly"],
    ],
    [2000, 3400, 1960, 2000],
  ),
];

const section4 = [
  h1("4.  Scope"),

  h2("4.1  In Scope (v3)"),
  bullet("On-demand ingestion of NSE corporate filings and announcements per watchlisted company."),
  bullet("AI-generated filing summaries and tag classification (Gemini 2.5 Flash and Pro)."),
  bullet("Filing-to-filing diff engine (line-level, inline and side-by-side views)."),
  bullet("SEBI Letter Draft Engine — example-driven AI drafting, tone matching, PDF and DOCX export, with team collaboration via invite links."),
  bullet("DIN Search across filings text, governance XBRL, and MCA scraped director history."),
  bullet("Governance reports module — NSE Integrated Filing system fetch, XBRL parse, two-report compare."),
  bullet("Compliance OS — SEBI/LODR/Companies Act obligations catalogue, due dates, scoring, SEBI enforcement scraping."),
  bullet("Stock Prices — live quotes, 3% movers, pre-open data, sector view."),
  bullet("Price Alerts — user-defined threshold alerts delivered to Telegram and the browser, evaluated every 60 s during IST market hours."),
  bullet("Industry Intelligence — curated profiles for 16 industries with live Google News and Gemini-authored weekly briefings."),
  bullet("Watchlists, account management, Razorpay billing and referral redemption."),
  bullet("Light and dark themes with a complete dark palette, gradient overrides, and a global animation pass."),

  h2("4.2  Out of Scope (v3)"),
  bullet("Retail trading, order placement or brokerage integration."),
  bullet("Personalised investment advisory or tax advice."),
  bullet("Coverage of unlisted private companies beyond MCA director data."),
  bullet("Non-Indian capital markets."),
  bullet("Native mobile applications (web-responsive only in v3)."),
  bullet("Real-time tick-level price streaming (60 s polling cadence is the design choice)."),

  h2("4.3  Assumptions"),
  bullet("NSE, MCA and SEBI continue to expose their current public data channels."),
  bullet("Supabase remains the identity provider; Razorpay remains the payment provider; Gemini remains available."),
  bullet("Telegram Bot API remains free for the volumes Delta Filings will produce."),
  bullet("Users have modern evergreen browsers (Chrome, Edge, Safari, Firefox — latest two versions)."),

  h2("4.4  Constraints"),
  bullet("No storage of card numbers, bank credentials or government IDs."),
  bullet("All filings content must be attributed to its original NSE source URL."),
  bullet("Single-instance Flask/Gunicorn deployment on Railway; horizontal scaling out of scope for v3."),
  bullet("SQLite (WAL mode) is the system of record; database growth is expected to remain comfortably within single-host limits for the v3 user base."),
  bullet("AI-generated content must always carry a visible \"machine-generated\" label."),
];

const section5 = [
  h1("5.  Stakeholders"),
  p(
    "The table below captures the stakeholders involved in Delta Filings v3, their primary interests and the nature of their engagement."
  ),
  table(
    ["Stakeholder", "Interest", "Engagement"],
    [
      ["Product Owner", "Overall outcome, prioritisation, release decisions", "Decision-maker"],
      ["Engineering Lead", "Architecture, delivery, reliability", "Decision-maker"],
      ["Compliance Advisor", "Accuracy of regulatory content", "Reviewer"],
      ["Design Lead", "Usability, dark-mode contrast, motion", "Contributor"],
      ["QA Lead", "Functional and regression validation", "Reviewer"],
      ["Paid Customers", "Direct usage feedback", "Contributor"],
      ["Executive Sponsor", "Strategic fit and investment", "Informed"],
    ],
    [2600, 4500, 2260],
  ),
];

const section6 = [
  h1("6.  User Personas"),

  h3("6.1  Arjun — Equity Research Analyst"),
  bullet("Covers 25–40 mid-cap stocks across chemicals, pharma and FMCG."),
  bullet("Needs: filing summaries within minutes of publication, side-by-side compare of two filings, sector-level briefings."),
  bullet("Pain today: juggles NSE, MCA and SEBI tabs; manually compares results between filings."),

  h3("6.2  Meera — Compliance Officer"),
  bullet("Accountable for LODR and Companies Act filings for a listed company."),
  bullet("Needs: obligation tracker with due dates, SEBI enforcement watch on her own and peer companies, audit-ready drafts."),
  bullet("Pain today: spreadsheets, email threads, no single source of truth."),

  h3("6.3  Raghav — Retail Investor / Finfluencer"),
  bullet("Manages a personal portfolio and publishes a weekly newsletter."),
  bullet("Needs: price-movement alerts to Telegram, AI explanation of why a stock moved, industry briefings."),
  bullet("Pain today: information overload; struggles to separate signal from noise."),

  h3("6.4  Priya — Legal and Audit Professional"),
  bullet("Performs director-level due-diligence and prepares SEBI correspondence."),
  bullet("Needs: cross-company director associations, MCA history, fast SEBI letter drafting and team review."),
  bullet("Pain today: MCA portal usability is poor; letter drafting requires re-keying boilerplate."),
];

const section7 = [
  h1("7.  Business Requirements"),
  p(
    "Business requirements describe what the platform must do, irrespective of implementation. Each requirement has an identifier (BR-###) that maps forward to the Functional Requirements Document (FRD) v3."
  ),

  h2("7.1  Filings, Diffs and Summaries"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-001", "Users can browse the latest filings across their watchlist on a unified dashboard.", "Must"],
      ["BR-002", "Users can open any company's full filing history, paginated.", "Must"],
      ["BR-003", "Each filing has an AI-generated summary, on demand if not already cached.", "Must"],
      ["BR-004", "Each filing carries one or more tags (Board, Compliance, Financial, Leadership, etc.).", "Must"],
      ["BR-005", "Users can diff a filing against the previous matching filing (inline and side-by-side).", "Must"],
      ["BR-006", "Users can search a company's filings by free text and tag.", "Should"],
      ["BR-007", "Users can extract and view embedded XBRL where the source PDF contains it.", "Should"],
    ],
    [1100, 6800, 1460],
  ),

  h2("7.2  SEBI Letter Draft Engine"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-010", "Users can select from 20+ pre-defined SEBI filing types (periodic and event-based).", "Must"],
      ["BR-011", "The platform pulls past examples of the chosen filing type for the target company.", "Must"],
      ["BR-012", "Users can learn a company's tone from its past filings before drafting.", "Must"],
      ["BR-013", "Users can AI-fill placeholders using public knowledge about the company.", "Must"],
      ["BR-014", "Users can improve a draft via polish, tone-match, formal-rewrite or custom instructions.", "Must"],
      ["BR-015", "Users can export a finished draft to PDF and to DOCX.", "Must"],
      ["BR-016", "Users can save drafts, list past drafts and resume them.", "Must"],
      ["BR-017", "Users can invite collaborators to a draft via shareable, revocable links.", "Should"],
      ["BR-018", "Owners can change collaborator roles (editor / viewer) and remove collaborators.", "Should"],
    ],
    [1100, 6800, 1460],
  ),

  h2("7.3  Directors, Governance and DIN"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-020", "Users can search directors by name or DIN across filings and governance reports.", "Must"],
      ["BR-021", "Director results include MCA-scraped history of active and past companies.", "Must"],
      ["BR-022", "Users can list a company's governance reports and inspect parsed XBRL.", "Must"],
      ["BR-023", "Users can compare two governance reports for a company side-by-side.", "Should"],
    ],
    [1100, 6800, 1460],
  ),

  h2("7.4  Compliance and Enforcement"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-030", "The platform tracks the standard SEBI / LODR / Companies Act obligations for each watchlisted company.", "Must"],
      ["BR-031", "Users see upcoming, overdue and completed obligations with due dates.", "Must"],
      ["BR-032", "The platform scrapes SEBI public enforcement orders and matches them to watchlisted companies with a confidence rating.", "Must"],
      ["BR-033", "Each company surfaces a compliance score and health tag.", "Should"],
      ["BR-034", "Users can manually refresh compliance data per company or for the whole watchlist.", "Should"],
    ],
    [1100, 6800, 1460],
  ),

  h2("7.5  Prices, Alerts and Intel"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-040", "The platform shows live quotes for all watchlisted companies.", "Must"],
      ["BR-041", "The platform highlights movers exceeding 3% (intra-day) and 1.5% (pre-open).", "Must"],
      ["BR-042", "Users can create custom price alerts (threshold %, direction, enabled flag).", "Must"],
      ["BR-043", "Custom alerts fire to Telegram (if linked) and to the browser.", "Must"],
      ["BR-044", "Users can request a deep \"price intel\" explanation combining news, Reddit and recent filings.", "Should"],
      ["BR-045", "Users can read a Gemini-authored weekly briefing for their industry.", "Should"],
    ],
    [1100, 6800, 1460],
  ),

  h2("7.6  Watchlists, Accounts and Billing"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-050", "Users can add or remove companies from a watchlist.", "Must"],
      ["BR-051", "Switching industry re-populates the watchlist and backfills data for the new set.", "Must"],
      ["BR-052", "Users register and sign in via email and password through Supabase.", "Must"],
      ["BR-053", "New users are onboarded by capturing industry, company and designation before checkout.", "Must"],
      ["BR-054", "Access to all paid features is gated by a single ₹4,999 annual fee paid via Razorpay.", "Must"],
      ["BR-055", "Users may redeem a valid referral code in lieu of payment.", "Must"],
      ["BR-056", "Users can link and unlink a Telegram account for alert delivery.", "Should"],
    ],
    [1100, 6800, 1460],
  ),

  h2("7.7  Experience, Theme and Motion"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-060", "The application provides a light theme and a dark theme.", "Must"],
      ["BR-061", "The dark theme uses a near-black, low-blue palette with consistent contrast across all surfaces, tags, badges, gradients and modals.", "Must"],
      ["BR-062", "Theme choice persists across visits via local storage.", "Must"],
      ["BR-063", "Interactive surfaces (cards, buttons, nav, theme toggle) animate with consistent timing.", "Should"],
      ["BR-064", "The interface respects the user's prefers-reduced-motion setting.", "Must"],
    ],
    [1100, 6800, 1460],
  ),
];

const section8 = [
  h1("8.  Summary of Functional Requirements"),
  p(
    "Functional requirements are specified in detail in the companion Functional Requirements Document v3. The table below summarises the modules and their identifier prefixes."
  ),
  table(
    ["Module", "FRD Prefix", "Coverage"],
    [
      ["Dashboard", "FR-D", "Calendar grid, recent activity, sector pulse, top tags, stats."],
      ["Filings & Announcements", "FR-A", "Per-company filing list, search, tag filter, on-demand backfill."],
      ["Filing Diff", "FR-FD", "Inline and side-by-side diff of two filings."],
      ["Draft Engine", "FR-DR", "SEBI letter wizard, AI drafting, exports, team collaboration."],
      ["DIN Search", "FR-N", "Search across filings, XBRL and MCA history."],
      ["Governance & XBRL", "FR-G", "List, parse and compare governance reports."],
      ["Compliance OS", "FR-M", "Obligations catalogue, scores, refresh, SEBI enforcement watch."],
      ["Stock Prices", "FR-P", "Live quotes, movers, pre-open, sector data."],
      ["Price Alerts", "FR-AL", "Custom alerts, Telegram delivery, 60 s poller."],
      ["Industry Intelligence", "FR-I", "16 industry profiles, news and weekly briefings."],
      ["Watchlist", "FR-W", "Add, remove, industry switch."],
      ["Authentication & Onboarding", "FR-U", "Signup, signin, onboarding, profile."],
      ["Payments & Access", "FR-PY", "Razorpay billing, referral redemption."],
      ["Experience & Theme", "FR-UX", "Dark mode, animations, accessibility."],
    ],
    [2600, 1400, 5360],
  ),
];

const section9 = [
  h1("9.  Non-Functional Requirements"),

  h2("9.1  Performance"),
  table(
    ["Metric", "Target"],
    [
      ["Dashboard time-to-interactive (p95)", "< 3.0 s on broadband"],
      ["Watchlist API latency (p95)", "< 400 ms"],
      ["Filing summary generation (Gemini Flash)", "< 12 s end-to-end"],
      ["Price alert dispatch latency (threshold to Telegram)", "< 90 s at p95"],
      ["Concurrent paid users (v3 envelope)", "300"],
    ],
    [5900, 3460],
  ),

  h2("9.2  Availability and Reliability"),
  bullet("Platform uptime target: 99% monthly during IST market hours."),
  bullet("Background pollers use a database-backed lease so only one Gunicorn worker performs each cycle."),
  bullet("Filings cache is refreshed lazily on company view if older than 6 hours."),
  bullet("Daily backup of the SQLite WAL database, retained for 14 days."),

  h2("9.3  Security"),
  bullet("All external traffic served over TLS by Railway."),
  bullet("Secrets supplied via Railway environment variables — never committed."),
  bullet("Supabase issues JWTs; Flask validates session state on every request."),
  bullet("Razorpay payment signatures verified via HMAC-SHA256 before marking a user paid."),
  bullet("All draft mutations are scoped to the owning user_id at the database query layer."),

  h2("9.4  Privacy and Data Protection"),
  bullet("Personal data limited to email, name, organisation, designation, industry, paid flag."),
  bullet("No storage of card numbers, bank credentials or government IDs."),
  bullet("Telegram chat IDs stored only after explicit Login Widget verification."),
  bullet("Data subject access and deletion honoured within 30 days of request."),

  h2("9.5  Accessibility"),
  bullet("WCAG 2.1 AA is the baseline for primary surfaces (dashboard, filings, draft editor)."),
  bullet("All interactive elements reachable via keyboard."),
  bullet("Dark mode contrast verified at design review; all tag colours retested for AA against #141414 surface."),
  bullet("Animations honour prefers-reduced-motion, collapsing to near-zero durations."),

  h2("9.6  Observability"),
  bullet("Gunicorn access and error logs streamed to Railway."),
  bullet("Background poller emits structured lines tagged [ALERT-POLLER] for filtering."),
  bullet("Razorpay webhooks logged with order_id for reconciliation."),
];

const section10 = [
  h1("10.  Solution Overview"),
  p(
    "Delta Filings v3 is a single-process Flask application deployed on Railway, fronted by Gunicorn. It uses SQLite (WAL mode) as the system of record, integrates with Supabase for authentication, Razorpay for billing, Gemini for AI workflows, Telegram for alert delivery, and live HTTP endpoints from NSE, MCA, SEBI and Google News for data. The frontend is Jinja2 templates with Tailwind CSS, custom CSS for the design system, and vanilla JavaScript."
  ),
  table(
    ["Component", "Technology", "Purpose"],
    [
      ["Web framework", "Flask 3 + Jinja2", "HTTP routing and server-side rendering."],
      ["Application server", "Gunicorn (2 workers, 120 s timeout)", "Production server on Railway."],
      ["Database", "SQLite (WAL mode)", "Filings, watchlist, drafts, compliance, alerts, payments."],
      ["Auth provider", "Supabase (publishable + admin keys)", "Email signup/signin, JWT sessions, user metadata."],
      ["Payments", "Razorpay (₹4,999 access fee)", "Order creation and HMAC-verified payments."],
      ["AI", "Google Gemini (2.5 Flash + Pro)", "Summaries, drafting, tone matching, briefings."],
      ["Alerts", "Telegram Bot API", "Custom price-alert delivery."],
      ["Data sources", "NSE, MCA (via ZaubaCorp / Tofler), SEBI, Google News, Reddit", "Filings, directors, enforcement, sector news."],
      ["Frontend styling", "Tailwind CSS + custom design system (styles.css)", "Light + dark themes, animations."],
      ["Hosting", "Railway (Procfile-driven)", "Build via nixpacks; deploy via railway up."],
    ],
    [2000, 3460, 3900],
  ),
];

const section11 = [
  h1("11.  Data Requirements"),

  h2("11.1  Primary Entities (SQLite)"),
  bullet("filings — the corporate filing record (company, title, description, date, pdf_link, raw_text, summary, tags, has_xbrl)."),
  bullet("snapshots — list of filing IDs captured at a point in time for diff detection."),
  bullet("watchlist — (user_id, symbol) with display name and timestamp."),
  bullet("settings — key-value (Gemini key, model choice)."),
  bullet("user_telegram — (user_id, chat_id, first_name, username)."),
  bullet("referral_codes / referral_redemptions — code registry and per-user redemption record."),
  bullet("user_payments — Razorpay payment / order IDs, amount, method, timestamp."),
  bullet("user_drafts — SEBI letter draft owned by a user."),
  bullet("draft_teams / draft_invites — collaborator roles and shareable invite tokens."),
  bullet("price_alerts — custom alert with threshold, direction, cooldown and last_fired_at."),
  bullet("compliance_obligations — SEBI mandate tracking per company."),
  bullet("enforcement_orders — SEBI public order with confidence rating."),
  bullet("cron_meta — database-backed lease for background pollers."),
  bullet("filings_meta — per-symbol last_fetched_at and count."),

  h2("11.2  Data Sources"),
  bullet("NSE corporate announcements and integrated filing system."),
  bullet("MCA director records (via ZaubaCorp / Tofler scraping, cached 30 days)."),
  bullet("SEBI public enforcement orders (scraped across five order categories)."),
  bullet("Google News RSS (per industry / company)."),
  bullet("Reddit JSON search endpoint (retail sentiment)."),
  bullet("Razorpay (payment data)."),
  bullet("Supabase (user identity and metadata)."),

  h2("11.3  Data Quality Targets"),
  bullet("Filings ingestion completeness: ≥ 95% of NSE filings for watchlisted companies."),
  bullet("Filing tag accuracy: ≥ 90% against weekly spot-check sample."),
  bullet("Enforcement match confidence: ≥ 70% of matched orders rated 'high' confidence."),
  bullet("Director association freshness: refreshed at least every 30 days for queried DINs."),

  h2("11.4  Data Retention"),
  bullet("Filings: retained indefinitely; only summary regenerated on user request."),
  bullet("Drafts: retained until the owner deletes them."),
  bullet("Background poller logs: retained 14 days via Railway."),
  bullet("Payment records: retained indefinitely for audit."),
  bullet("Deleted user data: purged within 30 days of request."),
];

const section12 = [
  h1("12.  Regulatory and Compliance Considerations"),
  bullet("All filings content links back to the originating NSE URL."),
  bullet("The platform does not claim to replace statutory filing channels and clearly identifies AI-generated content."),
  bullet("Draft Engine outputs are explicitly machine-generated; users are responsible for legal review before submission."),
  bullet("Razorpay handles all card / UPI data; Delta Filings never sees raw payment credentials."),
  bullet("User data handling follows the expectations of the Digital Personal Data Protection Act, 2023."),
  bullet("Terms of Service and Privacy Policy are reviewed annually."),
];

const section13 = [
  h1("13.  Risks and Mitigations"),
  table(
    ["ID", "Risk", "Impact", "Likelihood", "Mitigation"],
    [
      ["R-01", "NSE API layout changes break ingestion or symbol search.", "High", "Medium",
        "Per-source abstraction in fetch.py and stock_prices.py; sample-data fallback; 24-hour fix SLO."],
      ["R-02", "Gemini outage or quota exhaustion blocks summaries and drafting.", "High", "Low",
        "Per-user API key support; heuristic-only tag fallback; on-demand re-summarise."],
      ["R-03", "Telegram bot token revoked or rate-limited.", "Medium", "Low",
        "Graceful per-user error handling; in-browser alert remains as backup channel."],
      ["R-04", "SQLite contention as user base grows.", "Medium", "Medium",
        "WAL mode; query indexing; planned migration to Postgres at ≥ 1,000 paid users."],
      ["R-05", "Razorpay webhook miss or signature mismatch.", "High", "Low",
        "Signature verified on the synchronous /api/payment/verify endpoint before user is marked paid."],
      ["R-06", "Dark-mode contrast regressions after future template changes.", "Medium", "Medium",
        "Centralised dark overrides in styles.css; design review checklist enforces AA contrast."],
      ["R-07", "Railway region or platform outage.", "High", "Low",
        "Cached static surfaces; documented manual restore from SQLite backup."],
    ],
    [900, 2700, 1000, 1260, 3500],
  ),
];

const section14 = [
  h1("14.  Assumptions and Dependencies"),

  h2("14.1  External Dependencies"),
  bullet("Supabase — identity and user metadata."),
  bullet("Razorpay — payment processing."),
  bullet("Google Gemini — summarisation, drafting, briefings."),
  bullet("Telegram Bot API — alert delivery."),
  bullet("NSE, MCA, SEBI public data channels."),
  bullet("Google News RSS, Reddit JSON — supplementary intel."),
  bullet("Railway — hosting, build, runtime, logs."),

  h2("14.2  Internal Dependencies"),
  bullet("Design system (styles.css) maintains parity between light and dark themes for any new surface."),
  bullet("Editorial review of curated industry profiles before any addition / change."),
  bullet("Legal review of Terms of Service and Privacy Policy at least annually."),
];

const section15 = [
  h1("15.  Release Plan and Roadmap"),

  h2("15.1  Releases Delivered"),
  table(
    ["Release", "Date", "Highlights"],
    [
      ["v1", "2026-02", "Dashboard, filings list, AI summaries, diff engine, DIN search, Supabase auth."],
      ["v1.5", "2026-03", "Draft Engine MVP, watchlist persistence, compliance obligations catalogue."],
      ["v2 (BRD only)", "2026-04",
        "Aspirational spec for a FastAPI + Postgres + Next.js re-architecture (not adopted)."],
      ["v2.5", "2026-04", "Razorpay billing, referral system, onboarding flow."],
      ["v2.8", "2026-05", "Industry Intelligence, SEBI enforcement scraper, price intel, Telegram alerts, governance compare."],
      ["v3", "2026-05",
        "UX refresh — near-black dark palette, gradient overrides for Chemicals Pulse and similar cards, " +
        "icon-tile tint redesign, animation pass (fadeInUp, hover lifts, frosted page header, theme-toggle " +
        "rotation, button glow), focused inputs, reduced-motion fallback."],
    ],
    [1600, 1400, 6360],
  ),

  h2("15.2  Forward-Looking Roadmap"),
  table(
    ["Milestone", "Target Date", "Scope"],
    [
      ["v3.1", "2026-07", "BSE ingestion parity, more industries, draft template library."],
      ["v3.2", "2026-09", "Team tier (multi-seat), shared watchlists, SSO option."],
      ["v3.5", "2026-12", "Postgres migration; daemon-based background workers; queue."],
      ["v4", "2027-02", "Public API and partner integrations."],
    ],
    [1600, 1500, 6260],
  ),
];

const section16 = [
  h1("16.  Acceptance Criteria"),
  p(
    "A release is considered acceptable when all of the following are demonstrable on production for two consecutive weeks without regression:"
  ),
  numbered("All 'Must' business requirements pass their corresponding FRD acceptance tests."),
  numbered("Performance targets in Section 9.1 are met under representative load."),
  numbered("Dark mode passes a contrast and consistency audit across every primary route."),
  numbered("Animation pass exhibits no jank on a mid-tier laptop and is fully neutralised under prefers-reduced-motion."),
  numbered("Background poller successfully fires synthetic Telegram alerts end-to-end."),
  numbered("Razorpay test mode and live mode both succeed in marking a user paid and unlocking gated routes."),
  numbered("No High-severity security findings remain open."),
];

const glossary = [
  h1("Appendix A — Glossary"),
  table(
    ["Term", "Definition"],
    [
      ["BRD", "Business Requirements Document — this document."],
      ["BSE", "Bombay Stock Exchange."],
      ["CIN", "Corporate Identification Number issued by MCA."],
      ["DIN", "Director Identification Number issued by MCA."],
      ["DPDP Act", "Digital Personal Data Protection Act, 2023 (India)."],
      ["FRD", "Functional Requirements Document — companion document to this BRD."],
      ["Gemini", "Google's family of large language models used for summaries and drafting."],
      ["IST", "Indian Standard Time (UTC+05:30)."],
      ["LODR", "SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015."],
      ["MCA", "Ministry of Corporate Affairs, Government of India."],
      ["MoSCoW", "Prioritisation convention — Must, Should, Could, Won't."],
      ["NSE", "National Stock Exchange of India."],
      ["Razorpay", "Indian payment gateway used by Delta Filings to charge access fees."],
      ["SEBI", "Securities and Exchange Board of India."],
      ["SLO", "Service Level Objective — target level for a reliability metric."],
      ["Supabase", "Identity and user-metadata provider used by Delta Filings."],
      ["Telegram", "Messaging service used to deliver price alerts to linked users."],
      ["TLS", "Transport Layer Security, the cryptographic protocol for HTTPS."],
      ["WAL", "Write-Ahead Logging — SQLite journal mode used in production."],
      ["WCAG", "Web Content Accessibility Guidelines."],
      ["XBRL", "eXtensible Business Reporting Language — structured filings format."],
    ],
    [1800, 7560],
  ),
];

const uxAppendix = [
  h1("Appendix B — UX Refresh (v3) Changelog"),
  p(
    "This appendix consolidates the user-experience changes shipped as part of the v3 refresh in May 2026."
  ),

  h2("B.1  Dark Mode Palette"),
  bullet("Surface colours moved off slate-blue ('0f172a' / '020617') to a near-black neutral set: body #" + "0a0a0a, surface #0" + "0#1" + "41414, secondary #1c1c1c, border #262626."),
  bullet("Text neutrals re-balanced (#f5f5f5 / #d4d4d4 / #a3a3a3) for cleaner grayscale."),
  bullet("Tailwind utility overrides extended to bg-white, bg-gray-*, bg-slate-*, text-gray-*, border-gray-*, ring-gray-*, divide-gray-*."),

  h2("B.2  Surface Treatments"),
  bullet("Icon tiles (bg-*-100) re-coloured to tinted darks with a 1px inset ring for definition."),
  bullet("Decorative gradients (e.g. Chemicals Pulse — from-indigo-50 via-white to-purple-50) replaced with a subtle indigo → #141414 → purple gradient in dark mode."),
  bullet("Modals, diff views, scrollbars, inputs and the spinner all received dark variants."),

  h2("B.3  Animation and Motion"),
  bullet("Cards fade-in-and-rise on load (fadeInUp, 0.4 s cubic-bezier); feature cards staggered."),
  bullet("Feature and stat cards lift on hover with a subtle indigo halo in dark mode."),
  bullet("Primary buttons use a gradient + glow in dark mode and lift on hover."),
  bullet("Theme toggle icons rotate gently on hover."),
  bullet("Active sidebar nav item gains an indigo gradient bar and glow."),
  bullet("Page header receives a frosted backdrop-blur in dark mode for depth."),
  bullet("Smooth 0.25 s colour transitions across surfaces so theme toggling no longer snaps."),

  h2("B.4  Inputs and Focus"),
  bullet("Dark input surfaces #" + "1c1c1c with #" + "2a2a2a border and #f5f5f5 text."),
  bullet("Indigo focus ring (3px, 18% opacity) for inputs, textareas and selects."),
  bullet("Global :focus-visible outline (2px indigo, 60% opacity) for buttons and links."),

  h2("B.5  Accessibility"),
  bullet("All animations honour the user's prefers-reduced-motion setting and reduce to near-zero durations."),
  bullet("Dark-mode tag and badge colours re-checked for AA contrast against the #" + "141414 surface."),

  h2("B.6  Header Cleanup"),
  bullet("Removed the redundant company ticker chips from the dashboard top bar."),
  bullet("Theme toggle moved alongside the user profile cluster for a single, cohesive header strip."),
];

// ---------- Document assembly ----------
const doc = new Document({
  creator: "Delta Filings Product Team",
  title: "Delta Filings — Business Requirements Document v3",
  description: "BRD v3 for the Delta Filings platform as it ships on Railway, May 2026.",

  styles: {
    default: { document: { run: { font: "Calibri", size: 22 } } },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 34, bold: true, font: "Calibri", color: BRAND },
        paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 },
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Calibri", color: BRAND },
        paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 1 },
      },
      {
        id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 23, bold: true, font: "Calibri", color: "333333" },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 2 },
      },
    ],
  },

  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
          { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 1440, hanging: 360 } } } },
        ],
      },
      {
        reference: "numbers",
        levels: [
          { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        ],
      },
    ],
  },

  sections: [
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      headers: {
        default: new Header({
          children: [
            new Paragraph({
              tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
              border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: BRAND, space: 4 } },
              children: [
                new TextRun({ text: "Delta Filings  —  Business Requirements Document", color: BRAND, bold: true, size: 18 }),
                new TextRun({ text: "\t" }),
                new TextRun({ text: "v3.0  |  May 2026", color: TEXT_MUTED, size: 18 }),
              ],
            }),
          ],
        }),
      },
      footers: {
        default: new Footer({
          children: [
            new Paragraph({
              tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
              children: [
                new TextRun({ text: "Confidential — For internal circulation only", italics: true, size: 18, color: TEXT_MUTED }),
                new TextRun({ text: "\t" }),
                new TextRun({ text: "Page ", size: 18, color: TEXT_MUTED }),
                new TextRun({ children: [PageNumber.CURRENT], size: 18, color: TEXT_MUTED }),
                new TextRun({ text: " of ", size: 18, color: TEXT_MUTED }),
                new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 18, color: TEXT_MUTED }),
              ],
            }),
          ],
        }),
      },
      children: [
        ...coverPage,
        ...docControl,
        ...tocPage,
        ...section1,
        ...section2,
        ...section3,
        ...section4,
        ...section5,
        ...section6,
        ...section7,
        ...section8,
        ...section9,
        ...section10,
        ...section11,
        ...section12,
        ...section13,
        ...section14,
        ...section15,
        ...section16,
        ...glossary,
        ...uxAppendix,
      ],
    },
  ],
});

const OUT = path.join(
  "/Users/prakharsingh/Documents/Study Files/python things/delta_filings",
  "Delta_Filings_BRD_v3.docx",
);

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log(`Wrote ${OUT} (${buf.length} bytes)`);
});
