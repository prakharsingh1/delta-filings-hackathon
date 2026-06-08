/**
 * Delta Filings — Business Requirements Document (BRD) generator.
 *
 * Produces a professional, publication-ready BRD for the
 * "Delta Filings — Corporate Governance Intelligence Platform".
 *
 * Run: node create_brd_v2.js
 * Output: Delta_Filings_BRD.docx in the same directory.
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
const BRAND = "1F4E79";        // deep navy
const BRAND_LIGHT = "D5E8F0";  // cell header fill (reserved)
const GREY_BORDER = "BFBFBF";
const GREY_SOFT = "F2F2F2";
const TEXT_MUTED = "595959";

const border = { style: BorderStyle.SINGLE, size: 4, color: GREY_BORDER };
const cellBorders = { top: border, bottom: border, left: border, right: border };

// US Letter with 1" margins → content width = 9360 DXA
const CONTENT_WIDTH = 9360;

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
      cell(label, {
        bold: true,
        color: "FFFFFF",
        fill: BRAND,
        width: widths[i],
      }),
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

// ---------- Content ----------

const coverPage = [
  new Paragraph({
    spacing: { before: 2400, after: 200 },
    alignment: AlignmentType.CENTER,
    children: [
      new TextRun({
        text: "\u0394  DELTA FILINGS",
        bold: true,
        size: 64,
        color: BRAND,
      }),
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
      new TextRun({
        text: "Business Requirements Document",
        bold: true,
        size: 44,
      }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 2400 },
    children: [
      new TextRun({
        text: "Version 2.0  \u2014  April 2026",
        size: 24,
        color: TEXT_MUTED,
      }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1200 },
    children: [
      new TextRun({
        text: "Prepared by:  Delta Filings Product Team",
        size: 22,
        color: TEXT_MUTED,
      }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [
      new TextRun({
        text: "Confidential  \u2014  For internal circulation only",
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
      ["0.1", "2025-09-12", "Product Team", "Initial draft \u2014 scope and objectives."],
      ["0.5", "2025-12-04", "Product Team", "Added functional and non-functional requirements."],
      ["1.0", "2026-02-18", "Product Team", "Reviewed and approved for v1 release."],
      ["2.0", "2026-04-23", "Product Team", "v2 scope: Postgres + Redis + containerised stack, director compare, compliance module."],
    ],
    [1200, 1400, 1800, 4960],
  ),

  new Paragraph({ spacing: { after: 240 }, children: [new TextRun("")] }),

  h2("Approvers"),
  table(
    ["Name", "Role", "Organisation", "Signed On"],
    [
      ["Prakhar Singh", "Product Owner", "Delta Filings", "2026-04-23"],
      ["Engineering Lead", "Technical Sign-off", "Delta Filings", "2026-04-23"],
      ["Compliance Advisor", "Domain Sign-off", "Delta Filings", "2026-04-23"],
    ],
    [2400, 2400, 2400, 2160],
  ),

  new Paragraph({ spacing: { after: 240 }, children: [new TextRun("")] }),

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
  new TableOfContents("Table of Contents", {
    hyperlink: true,
    headingStyleRange: "1-3",
  }),
  new Paragraph({ children: [new PageBreak()] }),
];

const section1 = [
  h1("1.  Executive Summary"),
  p(
    "Delta Filings is a corporate governance intelligence platform that gives investors, compliance officers, analysts and researchers a single source of truth for filings, director records and compliance posture of Indian listed companies. The platform ingests data from statutory sources (BSE, NSE, MCA) and surfaces it through a modern web experience backed by a structured database and AI-driven summaries.",
  ),
  p(
    "This Business Requirements Document (BRD) formalises the scope, objectives and requirements of Delta Filings v2 \u2014 a re-architected release that moves from a single-process Flask application to a containerised, multi-service platform built on FastAPI, Next.js, PostgreSQL and Redis. The v2 scope also introduces a dedicated Compliance module, a Director Compare workflow and a Watchlist-driven alerting layer.",
  ),
  p(
    "The document is intended to align product, engineering, compliance and leadership stakeholders on what will be built, why it is being built and the criteria by which success will be measured.",
  ),
];

const section2 = [
  h1("2.  Business Context and Problem Statement"),

  h2("2.1  The Problem"),
  p(
    "Indian listed companies generate thousands of statutory disclosures every week across BSE, NSE and MCA. These disclosures are distributed across three portals, served as inconsistent PDFs, and lack any structured way to cross-reference directorships, historical filings or compliance events. As a result, investors and compliance teams spend disproportionate time on manual collection rather than analysis.",
  ),

  h2("2.2  Who Is Affected"),
  bullet("Retail and institutional investors tracking corporate events."),
  bullet("Compliance officers monitoring obligations under LODR, Companies Act and SEBI regulations."),
  bullet("Equity research analysts preparing coverage notes and governance reports."),
  bullet("Legal and audit teams conducting due-diligence on companies and directors."),

  h2("2.3  Current State"),
  p(
    "Delta Filings v1 (live since 2025) demonstrated strong product-market fit for filing aggregation and DIN lookup. However, it was built as a single Flask process with a SQLite backing store, making horizontal scaling, caching and background ingestion difficult. v1 also lacked a first-class compliance workflow and director comparison, both of which were the most-requested features from early users.",
  ),

  h2("2.4  Opportunity"),
  p(
    "A structured, real-time governance intelligence layer for Indian capital markets is an under-served category. Delta Filings v2 will convert the prototype learnings into a production-grade platform that can be extended into regulated verticals (audit, legal, banking) and white-labeled for enterprise customers.",
  ),
];

const section3 = [
  h1("3.  Business Objectives and Success Metrics"),

  h2("3.1  Strategic Objectives"),
  numbered("Establish Delta Filings as the default research surface for Indian corporate filings."),
  numbered("Reduce analyst time-to-insight on any listed company from hours to minutes."),
  numbered("Build a defensible data layer (filings + directors + compliance) that compounds over time."),
  numbered("Create an enterprise-ready architecture capable of supporting paid tiers within 12 months."),

  h2("3.2  Key Performance Indicators"),
  table(
    ["KPI", "Definition", "v2 Target", "Cadence"],
    [
      ["Monthly Active Users", "Unique signed-in users / month", "5,000", "Monthly"],
      ["Filings Coverage", "% of BSE/NSE top-500 filings captured within 15 min", "\u2265 98%", "Weekly"],
      ["DIN Lookup Latency (p95)", "Server time for a director lookup", "< 800 ms", "Continuous"],
      ["Compliance Run Success", "Completed runs / total runs", "\u2265 95%", "Weekly"],
      ["User Retention (Week 4)", "% of new users active in week 4", "\u2265 35%", "Monthly"],
      ["Paid Conversion", "Free to paid conversion, quarter", "\u2265 4%", "Quarterly"],
    ],
    [2000, 3400, 1960, 2000],
  ),
];

const section4 = [
  h1("4.  Scope"),

  h2("4.1  In Scope (v2)"),
  bullet("Real-time ingestion of BSE and NSE corporate announcements."),
  bullet("MCA director record scraping with cached DIN lookup."),
  bullet("User watchlists with price and filing alerts."),
  bullet("AI-generated filing summaries and materiality classification."),
  bullet("Director Compare \u2014 side-by-side view of up to three directors."),
  bullet("Compliance module \u2014 obligations dashboard, run history, audit trail."),
  bullet("Role-based authentication via Supabase (email and Google)."),
  bullet("Containerised deployment (Docker Compose for dev, orchestrated for prod)."),
  bullet("PostgreSQL as system of record; Redis as cache and job queue."),

  h2("4.2  Out of Scope (v2)"),
  bullet("Retail trading, order placement or brokerage integrations."),
  bullet("Personalised investment or tax advisory."),
  bullet("Coverage of unlisted private companies beyond MCA director data."),
  bullet("Non-Indian capital markets."),
  bullet("Mobile native applications (web-responsive only in v2)."),

  h2("4.3  Assumptions"),
  bullet("BSE, NSE and MCA continue to expose their current public data channels."),
  bullet("Supabase remains the identity provider for v2."),
  bullet("OpenAI API availability and pricing remain within budgeted limits."),
  bullet("Users have modern evergreen browsers (Chrome, Edge, Safari, Firefox \u2014 latest two versions)."),

  h2("4.4  Constraints"),
  bullet("No storage of card numbers, bank credentials or sensitive personal identifiers."),
  bullet("All filings content must be attributed to its original source URL."),
  bullet("Infrastructure must be reproducible via Docker Compose for local development."),
  bullet("Initial release must comply with Indian data-localisation expectations for user data."),
];

const section5 = [
  h1("5.  Stakeholders"),
  p(
    "The table below captures the stakeholders involved in Delta Filings v2, their primary interests and the nature of their engagement.",
  ),
  table(
    ["Stakeholder", "Interest", "Engagement"],
    [
      ["Product Owner", "Overall outcome, prioritisation, release decisions", "Decision-maker"],
      ["Engineering Lead", "Architecture, delivery, quality", "Decision-maker"],
      ["Compliance Advisor", "Accuracy of regulatory content", "Reviewer"],
      ["Design Lead", "Usability and visual system", "Contributor"],
      ["QA Lead", "Functional and regression validation", "Reviewer"],
      ["Pilot Customers", "Beta usage and feedback", "Contributor"],
      ["Executive Sponsor", "Strategic fit and investment", "Informed"],
    ],
    [2600, 4500, 2260],
  ),
];

const section6 = [
  h1("6.  User Personas"),

  h3("6.1  Arjun \u2014 Equity Research Analyst"),
  bullet("Covers 40+ mid-cap stocks across BFSI and pharma."),
  bullet("Needs: filing summaries within 10 minutes of announcement, director background in one click."),
  bullet("Pain today: juggles BSE, NSE and MCA tabs; loses time reconciling data."),

  h3("6.2  Meera \u2014 Compliance Officer"),
  bullet("Accountable for quarterly LODR and Companies Act filings for a listed company."),
  bullet("Needs: obligation tracker, evidence capture, audit-ready export."),
  bullet("Pain today: spreadsheets, email threads, no single source of truth."),

  h3("6.3  Raghav \u2014 Retail Investor / Finfluencer"),
  bullet("Manages a personal portfolio and publishes weekly newsletters."),
  bullet("Needs: alerts when tracked companies file something material."),
  bullet("Pain today: information overload; struggles to separate signal from noise."),

  h3("6.4  Priya \u2014 Legal and Audit Professional"),
  bullet("Performs due-diligence on directors and companies."),
  bullet("Needs: historical director associations, cross-company directorships, compliance history."),
  bullet("Pain today: MCA portal usability, poor search, no cross-company view."),
];

const section7 = [
  h1("7.  Business Requirements"),
  p(
    "Business requirements describe what the business needs the platform to do, irrespective of implementation. Each requirement has a unique identifier (BR-###) that maps forward to the Functional and Non-Functional Requirements.",
  ),

  h2("7.1  Filings and Announcements"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-001", "Users can search filings by company name, ticker or ISIN.", "Must"],
      ["BR-002", "Users can filter filings by date, category and exchange.", "Must"],
      ["BR-003", "Users receive AI-generated summaries of each filing.", "Must"],
      ["BR-004", "Users can save filings to a personal watchlist.", "Should"],
      ["BR-005", "Users can export filings metadata to CSV.", "Should"],
    ],
    [1100, 6800, 1460],
  ),

  h2("7.2  Directors and DIN Lookup"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-010", "Users can look up any director by DIN.", "Must"],
      ["BR-011", "The platform shows all companies a director is associated with.", "Must"],
      ["BR-012", "Users can compare up to three directors side-by-side.", "Must"],
      ["BR-013", "Directors flagged as disqualified are visually distinguished.", "Should"],
      ["BR-014", "Users can bookmark directors for monitoring.", "Could"],
    ],
    [1100, 6800, 1460],
  ),

  h2("7.3  Compliance Module"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-020", "Compliance officers can list obligations applicable to a company.", "Must"],
      ["BR-021", "Users can trigger a compliance run and view its status.", "Must"],
      ["BR-022", "Each run records evidence, timestamp and outcome for audit.", "Must"],
      ["BR-023", "The module supports export of a compliance report (PDF).", "Should"],
      ["BR-024", "Users receive reminders ahead of statutory deadlines.", "Should"],
    ],
    [1100, 6800, 1460],
  ),

  h2("7.4  Watchlists and Alerts"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-030", "Users can create and name multiple watchlists.", "Must"],
      ["BR-031", "Users receive email alerts on new filings in a watchlist.", "Must"],
      ["BR-032", "Alert frequency is configurable (instant, daily digest, weekly).", "Should"],
      ["BR-033", "Users can mute alerts for specific filing categories.", "Could"],
    ],
    [1100, 6800, 1460],
  ),

  h2("7.5  Accounts, Access and Billing"),
  table(
    ["ID", "Requirement", "Priority"],
    [
      ["BR-040", "Users can register via email or Google OAuth.", "Must"],
      ["BR-041", "Users can manage their profile and notification preferences.", "Must"],
      ["BR-042", "The platform supports Free, Pro and Enterprise tiers.", "Should"],
      ["BR-043", "Enterprise tier supports SSO and team seats.", "Could"],
    ],
    [1100, 6800, 1460],
  ),
];

const section8 = [
  h1("8.  Functional Requirements"),
  p(
    "Functional requirements translate the business requirements above into capabilities of the system. They are grouped by module. Priority uses the MoSCoW convention (Must, Should, Could, Won't in this release).",
  ),

  h2("8.1  Dashboard Module"),
  bullet("FR-D-01 \u2014 The dashboard shall display the latest 50 filings across the user's watchlists."),
  bullet("FR-D-02 \u2014 The dashboard shall display a sentiment-tagged AI summary for each filing."),
  bullet("FR-D-03 \u2014 The dashboard shall display the health status of the platform's ingestion pipelines."),

  h2("8.2  Announcements Module"),
  bullet("FR-A-01 \u2014 The system shall fetch new filings from BSE and NSE every 60 seconds during market hours."),
  bullet("FR-A-02 \u2014 The system shall de-duplicate identical filings cross-listed on both exchanges."),
  bullet("FR-A-03 \u2014 The system shall classify each filing into a materiality tier (High, Medium, Low)."),
  bullet("FR-A-04 \u2014 The system shall expose a filing-detail page with source PDF, summary and related filings."),

  h2("8.3  DIN Search Module"),
  bullet("FR-N-01 \u2014 The system shall accept an 8-digit DIN and return the director's name, DOB masked to year, and associations."),
  bullet("FR-N-02 \u2014 The system shall cache director lookups in Redis for 24 hours."),
  bullet("FR-N-03 \u2014 The system shall show a distinct 'curated snapshot' badge when data is served from the curated set rather than live MCA."),

  h2("8.4  Compare Module"),
  bullet("FR-C-01 \u2014 The user shall be able to add up to three directors to a comparison view."),
  bullet("FR-C-02 \u2014 The comparison view shall highlight common directorships."),
  bullet("FR-C-03 \u2014 The comparison view shall export to a shareable link (read-only)."),

  h2("8.5  Compliance Module"),
  bullet("FR-M-01 \u2014 The system shall present obligations relevant to the selected company based on its listing and sector."),
  bullet("FR-M-02 \u2014 The user shall be able to create a compliance run with a due date."),
  bullet("FR-M-03 \u2014 The system shall capture each evidence attachment against its obligation."),
  bullet("FR-M-04 \u2014 The user shall export a run summary as a PDF suitable for audit review."),

  h2("8.6  Watchlist and Alerts"),
  bullet("FR-W-01 \u2014 The user shall create, rename, and delete watchlists."),
  bullet("FR-W-02 \u2014 The system shall send email alerts via a transactional provider."),
  bullet("FR-W-03 \u2014 The user shall receive a weekly digest by default with an opt-out link."),

  h2("8.7  Account Management"),
  bullet("FR-U-01 \u2014 Authentication is delegated to Supabase using email and Google OAuth."),
  bullet("FR-U-02 \u2014 Profiles store display name, organisation and notification preferences."),
  bullet("FR-U-03 \u2014 The system shall honour account deletion requests within 30 days."),
];

const section9 = [
  h1("9.  Non-Functional Requirements"),

  h2("9.1  Performance"),
  table(
    ["Metric", "Target"],
    [
      ["Dashboard time-to-interactive (p95)", "< 2.5 seconds on 4G"],
      ["API latency (p95, authenticated endpoints)", "< 400 ms"],
      ["Filings ingestion lag from exchange publication", "< 15 minutes"],
      ["Concurrent user support (v2 launch)", "2,000"],
    ],
    [5900, 3460],
  ),

  h2("9.2  Availability and Reliability"),
  bullet("Platform uptime target: 99.5% monthly during market hours."),
  bullet("Planned maintenance windows outside market hours, announced 48 hours in advance."),
  bullet("Automated daily backups of the PostgreSQL database, retained for 30 days."),

  h2("9.3  Security"),
  bullet("TLS 1.2+ for all external traffic; HSTS enabled."),
  bullet("All secrets managed via environment variables; no secrets committed to source control."),
  bullet("JWT-based session tokens with 60-minute expiry and refresh rotation."),
  bullet("Principle of least privilege for database and service accounts."),
  bullet("Quarterly dependency vulnerability review (Dependabot, OSV)."),

  h2("9.4  Privacy and Data Protection"),
  bullet("Personally identifying information limited to email, display name and organisation."),
  bullet("No storage of card numbers, bank credentials or government IDs."),
  bullet("Data-subject access and deletion requests honoured within 30 days."),
  bullet("User data residency: Indian region for primary storage, subject to provider availability."),

  h2("9.5  Accessibility"),
  bullet("WCAG 2.1 AA as the baseline for all customer-facing surfaces."),
  bullet("Keyboard-only navigation across all primary flows."),
  bullet("Colour contrast ratios validated at design review."),

  h2("9.6  Observability"),
  bullet("Structured JSON logs shipped to a central log aggregator."),
  bullet("Per-endpoint latency and error-rate dashboards."),
  bullet("Synthetic probes for dashboard and DIN lookup every 60 seconds."),
  bullet("On-call rotation with 15-minute acknowledgement SLO for Sev-1 incidents."),
];

const section10 = [
  h1("10.  Solution Overview"),
  p(
    "Delta Filings v2 is a containerised multi-service application. The table below summarises the principal components. A more detailed technical design is maintained separately in the architecture document.",
  ),
  table(
    ["Component", "Technology", "Purpose"],
    [
      ["Frontend", "Next.js 15 (App Router), React 18, Tailwind CSS", "Server-rendered user interface and client interactions."],
      ["Backend API", "FastAPI (Python 3.12), SQLAlchemy 2 async", "Business logic and REST endpoints."],
      ["Database", "PostgreSQL 16", "System of record for users, filings, compliance runs."],
      ["Cache and Queue", "Redis 7", "Caching, dedup, background job queue."],
      ["Authentication", "Supabase", "Email and Google OAuth, JWT issuance."],
      ["AI Summaries", "OpenAI API", "Filing summarisation and materiality classification."],
      ["Ingestion Workers", "Python async scrapers (BSE, NSE, MCA)", "Fetch, normalise and persist filings."],
      ["Orchestration", "Docker Compose (dev), container platform (prod)", "Reproducible deployment."],
    ],
    [2000, 3160, 4200],
  ),
];

const section11 = [
  h1("11.  Data Requirements"),

  h2("11.1  Primary Entities"),
  bullet("Company \u2014 identified by symbol, ISIN and CIN."),
  bullet("Filing \u2014 timestamped disclosure with source URL and category."),
  bullet("Director \u2014 identified by DIN, with masked DOB."),
  bullet("DirectorAssociation \u2014 link between a director and a company with role and tenure."),
  bullet("User Profile \u2014 email, display name, organisation, preferences."),
  bullet("Watchlist \u2014 user-owned collection of companies."),
  bullet("Compliance Run \u2014 instance of an obligation with status and evidence."),

  h2("11.2  Data Sources"),
  bullet("BSE corporate announcements feed."),
  bullet("NSE corporate announcements feed."),
  bullet("MCA public director records (scraped, rate-limited)."),
  bullet("Curated director set maintained by Delta Filings editorial."),

  h2("11.3  Data Quality Targets"),
  bullet("Filing ingestion completeness: \u2265 98% of BSE/NSE top-500 filings captured."),
  bullet("Filing categorisation accuracy: \u2265 95% against a weekly spot-check sample."),
  bullet("Director association freshness: \u2264 7 days since last reconciliation."),

  h2("11.4  Data Retention"),
  bullet("Filings: retained indefinitely."),
  bullet("Audit logs: retained 18 months."),
  bullet("User activity telemetry (non-PII): retained 12 months."),
  bullet("Deleted user data: purged within 30 days of request."),
];

const section12 = [
  h1("12.  Regulatory and Compliance Considerations"),
  bullet("All filings content must link back to the originating BSE/NSE/MCA URL."),
  bullet("The platform shall not claim to replace statutory filing channels."),
  bullet("AI-generated summaries shall carry a visible 'machine-generated' label."),
  bullet("User data handling follows the expectations of the Digital Personal Data Protection Act, 2023."),
  bullet("Terms of Service and Privacy Policy shall be reviewed legally before v2 launch."),
];

const section13 = [
  h1("13.  Risks and Mitigations"),
  table(
    ["ID", "Risk", "Impact", "Likelihood", "Mitigation"],
    [
      ["R-01", "Upstream exchange portal changes break scrapers.", "High", "Medium", "Source abstraction layer; synthetic probes; 24-hour fix SLO."],
      ["R-02", "AI summary hallucination damages credibility.", "High", "Low", "Confidence threshold; 'machine-generated' label; editorial spot-checks."],
      ["R-03", "MCA rate-limiting blocks director lookups.", "Medium", "Medium", "Aggressive Redis caching; curated snapshot fallback."],
      ["R-04", "Database growth outpaces plan.", "Medium", "Low", "Partitioning on filings by year; archival tier for >2-year-old PDFs."],
      ["R-05", "Supabase outage prevents login.", "High", "Low", "Graceful degradation; public read surfaces remain available."],
      ["R-06", "Key team member attrition.", "Medium", "Medium", "Documentation-first culture; rotation of on-call duties."],
    ],
    [900, 2800, 1000, 1260, 3400],
  ),
];

const section14 = [
  h1("14.  Assumptions and Dependencies"),

  h2("14.1  External Dependencies"),
  bullet("Supabase (identity and user management)."),
  bullet("OpenAI (summarisation and classification)."),
  bullet("Transactional email provider (alerts and digests)."),
  bullet("BSE, NSE, MCA public data channels."),

  h2("14.2  Internal Dependencies"),
  bullet("Design system is complete before front-end implementation of each module."),
  bullet("Legal review of Terms of Service and Privacy Policy before v2 launch."),
  bullet("Editorial pipeline for curated director records staffed ahead of launch."),
];

const section15 = [
  h1("15.  Release Plan and Roadmap"),
  table(
    ["Milestone", "Target Date", "Scope"],
    [
      ["M1 \u2014 Platform scaffold", "2026-05-15", "Containerised stack, authentication, empty dashboard."],
      ["M2 \u2014 Filings live", "2026-06-15", "Live BSE/NSE ingestion, filing detail page, AI summaries."],
      ["M3 \u2014 Directors and DIN", "2026-07-10", "DIN lookup, director compare, curated snapshot."],
      ["M4 \u2014 Compliance module", "2026-08-15", "Obligations catalogue, runs, evidence, PDF export."],
      ["M5 \u2014 Watchlists and alerts", "2026-09-15", "Email alerts, digest cadence, mute controls."],
      ["M6 \u2014 Public launch", "2026-10-01", "Free tier launch, onboarding, documentation site."],
      ["M7 \u2014 Paid tiers", "2026-12-01", "Pro and Enterprise billing, SSO for Enterprise."],
    ],
    [2600, 1960, 4800],
  ),
];

const section16 = [
  h1("16.  Acceptance Criteria"),
  p(
    "The release is considered acceptable when all of the following are demonstrable on the staging environment for two consecutive weeks without regression:",
  ),
  numbered("All 'Must' priority functional requirements pass their acceptance tests."),
  numbered("Performance targets in Section 9.1 are met in load testing at 2x launch concurrency."),
  numbered("All 'High' severity security findings are remediated."),
  numbered("Accessibility audit produces no Level A or AA blocking issues."),
  numbered("On-call runbooks cover ingestion, auth, AI and database failure modes."),
  numbered("A customer dry-run with three pilot users completes end-to-end workflows without facilitator intervention."),
];

const glossary = [
  h1("Appendix A \u2014 Glossary"),
  table(
    ["Term", "Definition"],
    [
      ["BRD", "Business Requirements Document \u2014 this document."],
      ["BSE", "Bombay Stock Exchange."],
      ["CIN", "Corporate Identification Number issued by MCA."],
      ["DIN", "Director Identification Number issued by MCA."],
      ["DPDP Act", "Digital Personal Data Protection Act, 2023 (India)."],
      ["ISIN", "International Securities Identification Number."],
      ["LODR", "SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015."],
      ["MCA", "Ministry of Corporate Affairs, Government of India."],
      ["MoSCoW", "Prioritisation convention \u2014 Must, Should, Could, Won't."],
      ["NSE", "National Stock Exchange of India."],
      ["SLO", "Service Level Objective \u2014 target level for a reliability metric."],
      ["SSO", "Single Sign-On."],
      ["TLS", "Transport Layer Security, the cryptographic protocol for HTTPS."],
      ["WCAG", "Web Content Accessibility Guidelines."],
    ],
    [1800, 7560],
  ),
];

// ---------- Document assembly ----------
const doc = new Document({
  creator: "Delta Filings Product Team",
  title: "Delta Filings \u2014 Business Requirements Document",
  description: "BRD for the Delta Filings Corporate Governance Intelligence Platform (v2).",

  styles: {
    default: {
      document: { run: { font: "Calibri", size: 22 } }, // 11pt
    },
    paragraphStyles: [
      {
        id: "Heading1",
        name: "Heading 1",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 34, bold: true, font: "Calibri", color: BRAND },
        paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 },
      },
      {
        id: "Heading2",
        name: "Heading 2",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 26, bold: true, font: "Calibri", color: BRAND },
        paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 1 },
      },
      {
        id: "Heading3",
        name: "Heading 3",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
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
          {
            level: 0,
            format: LevelFormat.BULLET,
            text: "\u2022",
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } },
          },
          {
            level: 1,
            format: LevelFormat.BULLET,
            text: "\u25E6",
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 1440, hanging: 360 } } },
          },
        ],
      },
      {
        reference: "numbers",
        levels: [
          {
            level: 0,
            format: LevelFormat.DECIMAL,
            text: "%1.",
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } },
          },
        ],
      },
    ],
  },

  sections: [
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 }, // US Letter
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      headers: {
        default: new Header({
          children: [
            new Paragraph({
              tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
              border: {
                bottom: {
                  style: BorderStyle.SINGLE,
                  size: 6,
                  color: BRAND,
                  space: 4,
                },
              },
              children: [
                new TextRun({
                  text: "Delta Filings  \u2014  Business Requirements Document",
                  color: BRAND,
                  bold: true,
                  size: 18,
                }),
                new TextRun({ text: "\t" }),
                new TextRun({
                  text: "v2.0  |  April 2026",
                  color: TEXT_MUTED,
                  size: 18,
                }),
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
                new TextRun({
                  text: "Confidential \u2014 For internal circulation only",
                  italics: true,
                  size: 18,
                  color: TEXT_MUTED,
                }),
                new TextRun({ text: "\t" }),
                new TextRun({ text: "Page ", size: 18, color: TEXT_MUTED }),
                new TextRun({
                  children: [PageNumber.CURRENT],
                  size: 18,
                  color: TEXT_MUTED,
                }),
                new TextRun({ text: " of ", size: 18, color: TEXT_MUTED }),
                new TextRun({
                  children: [PageNumber.TOTAL_PAGES],
                  size: 18,
                  color: TEXT_MUTED,
                }),
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
      ],
    },
  ],
});

const OUT = path.join(
  "/Users/prakharsingh/Documents/Study Files/python things/delta_filings",
  "Delta_Filings_BRD.docx",
);

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log(`Wrote ${OUT} (${buf.length} bytes)`);
});
