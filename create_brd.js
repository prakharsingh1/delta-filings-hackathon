// Generate professional BRD for Delta Filings
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
  BorderStyle, WidthType, ShadingType, PageNumber, PageBreak,
  TabStopType, TabStopPosition, TableOfContents,
} = require("docx");

const ACCENT = "1F4E79";        // deep blue
const ACCENT_LIGHT = "D9E2F3";  // light blue
const GREY_BORDER = "BFBFBF";
const GREY_TEXT = "595959";

const border = { style: BorderStyle.SINGLE, size: 4, color: GREY_BORDER };
const cellBorders = { top: border, bottom: border, left: border, right: border };

// ---------- helpers ----------
function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 160 },
    children: [new TextRun({ text, bold: true, color: ACCENT, size: 32, font: "Calibri" })],
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 280, after: 120 },
    children: [new TextRun({ text, bold: true, color: ACCENT, size: 26, font: "Calibri" })],
  });
}
function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 200, after: 100 },
    children: [new TextRun({ text, bold: true, color: "333333", size: 22, font: "Calibri" })],
  });
}
function p(text, opts = {}) {
  return new Paragraph({
    spacing: { after: 120, line: 300 },
    alignment: opts.align || AlignmentType.JUSTIFIED,
    children: [new TextRun({ text, size: 22, font: "Calibri", ...opts })],
  });
}
function bullet(text, level = 0) {
  return new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { after: 80, line: 280 },
    children: [new TextRun({ text, size: 22, font: "Calibri" })],
  });
}
function cell(text, { bold = false, fill, width, align } = {}) {
  return new TableCell({
    borders: cellBorders,
    width: width ? { size: width, type: WidthType.DXA } : undefined,
    shading: fill ? { fill, type: ShadingType.CLEAR } : undefined,
    margins: { top: 100, bottom: 100, left: 140, right: 140 },
    children: [new Paragraph({
      alignment: align || AlignmentType.LEFT,
      children: [new TextRun({ text, bold, size: 22, font: "Calibri" })],
    })],
  });
}
function table(rows, columnWidths) {
  const totalWidth = columnWidths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: totalWidth, type: WidthType.DXA },
    columnWidths,
    rows,
  });
}
function headerCell(text, width) {
  return new TableCell({
    borders: cellBorders,
    width: { size: width, type: WidthType.DXA },
    shading: { fill: ACCENT, type: ShadingType.CLEAR },
    margins: { top: 120, bottom: 120, left: 140, right: 140 },
    children: [new Paragraph({
      children: [new TextRun({ text, bold: true, size: 22, color: "FFFFFF", font: "Calibri" })],
    })],
  });
}
function spacer(size = 120) {
  return new Paragraph({ spacing: { after: size }, children: [new TextRun("")] });
}

// ---------- Cover Page ----------
const cover = [
  new Paragraph({ spacing: { before: 2400 }, children: [new TextRun("")] }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({ text: "BUSINESS REQUIREMENTS DOCUMENT", bold: true, size: 52, color: ACCENT, font: "Calibri" })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 120 },
    children: [new TextRun({ text: "Delta Filings", bold: true, size: 44, font: "Calibri" })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 2400 },
    children: [new TextRun({ text: "Corporate Governance Intelligence Platform", italics: true, size: 28, color: GREY_TEXT, font: "Calibri" })],
  }),
  table(
    [
      new TableRow({ children: [cell("Document Title", { bold: true, fill: ACCENT_LIGHT, width: 3000 }), cell("Delta Filings \u2014 Business Requirements Document", { width: 5400 })] }),
      new TableRow({ children: [cell("Version", { bold: true, fill: ACCENT_LIGHT, width: 3000 }), cell("1.0", { width: 5400 })] }),
      new TableRow({ children: [cell("Status", { bold: true, fill: ACCENT_LIGHT, width: 3000 }), cell("Baseline \u2014 Approved for Development", { width: 5400 })] }),
      new TableRow({ children: [cell("Date", { bold: true, fill: ACCENT_LIGHT, width: 3000 }), cell("April 2026", { width: 5400 })] }),
      new TableRow({ children: [cell("Owner", { bold: true, fill: ACCENT_LIGHT, width: 3000 }), cell("Product Management \u2014 Delta Filings", { width: 5400 })] }),
      new TableRow({ children: [cell("Classification", { bold: true, fill: ACCENT_LIGHT, width: 3000 }), cell("Internal \u2014 Stakeholder Review", { width: 5400 })] }),
    ],
    [3000, 5400],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- Document Control ----------
const documentControl = [
  h1("1. Document Control"),
  h2("1.1 Revision History"),
  table(
    [
      new TableRow({ children: [headerCell("Version", 1200), headerCell("Date", 1600), headerCell("Author", 2000), headerCell("Change Summary", 3600)] }),
      new TableRow({ children: [cell("0.1"), cell("Feb 2026"), cell("Product Team"), cell("Initial outline and scoping draft.")] }),
      new TableRow({ children: [cell("0.5"), cell("Mar 2026"), cell("Product Team"), cell("Consolidated stakeholder feedback; added compliance scope.")] }),
      new TableRow({ children: [cell("1.0"), cell("Apr 2026"), cell("Product Team"), cell("Baseline version approved for development.")] }),
    ],
    [1200, 1600, 2000, 3600],
  ),
  spacer(200),
  h2("1.2 Stakeholders and Approvers"),
  table(
    [
      new TableRow({ children: [headerCell("Role", 2400), headerCell("Responsibility", 4000), headerCell("Approval Required", 2000)] }),
      new TableRow({ children: [cell("Product Owner"), cell("Defines vision, priorities, and acceptance criteria."), cell("Yes", { align: AlignmentType.CENTER })] }),
      new TableRow({ children: [cell("Engineering Lead"), cell("Technical feasibility, architecture, and delivery."), cell("Yes", { align: AlignmentType.CENTER })] }),
      new TableRow({ children: [cell("Compliance SME"), cell("Validates SEBI LODR interpretation and rule coverage."), cell("Yes", { align: AlignmentType.CENTER })] }),
      new TableRow({ children: [cell("UX Lead"), cell("Information architecture, dashboard experience."), cell("Advisory", { align: AlignmentType.CENTER })] }),
      new TableRow({ children: [cell("End Users"), cell("Analysts, corporate lawyers, governance researchers."), cell("Reviewer", { align: AlignmentType.CENTER })] }),
    ],
    [2400, 4000, 2000],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 2. Executive Summary ----------
const execSummary = [
  h1("2. Executive Summary"),
  p("Delta Filings is a web-based Corporate Governance Intelligence Platform that transforms raw NSE corporate filings and XBRL governance reports into structured, searchable, and analyzable intelligence. It is designed for governance analysts, corporate lawyers, institutional investors, and compliance teams who need to monitor board composition, director movements, shareholding changes, and statutory compliance across Indian listed companies."),
  p("The platform automates the ingestion, parsing, and normalization of multi-format disclosures \u2014 PDFs, XBRL filings, and governance reports \u2014 and converts them into structured datasets. On top of this data layer, Delta Filings provides watchlists, diff engines for period-over-period change detection, director (DIN) lookups across companies, and SEBI LODR compliance checks with plain-language explanations."),
  p("This Business Requirements Document describes the business context, objectives, scope, stakeholder needs, functional and non-functional requirements, and success criteria for the baseline release of Delta Filings."),
  h2("2.1 Business Problem"),
  p("Indian listed companies publish voluminous governance disclosures across fragmented channels \u2014 NSE announcements, XBRL shareholding filings, quarterly corporate governance reports, and annual reports. Analysts currently spend hours manually downloading PDFs, reading board composition tables, and comparing quarter-over-quarter changes. Critical events \u2014 a director resignation, a pledge of promoter shares, a committee reconstitution \u2014 are easy to miss, surface late, or are buried in dense unstructured documents."),
  p("There is no affordable, India-focused tool that (a) aggregates these disclosures into one workspace, (b) detects material changes automatically, (c) allows cross-company director history lookup, and (d) performs SEBI LODR compliance checks with human-readable reasoning."),
  h2("2.2 Proposed Solution"),
  p("Delta Filings addresses this gap with a unified Flask-based platform that ingests NSE filings and XBRL governance reports, stores them in a normalized database, and exposes them through authenticated, multi-tenant dashboards. The system provides:"),
  bullet("Personalized watchlists per authenticated user, isolated through Supabase authentication."),
  bullet("Automated change detection across quarterly filings (board, committees, shareholding, KMP)."),
  bullet("Full-text and structured DIN search spanning every company in the platform's data lake."),
  bullet("Rule-based SEBI LODR compliance verification with pass/fail reasoning."),
  bullet("Downloadable, stakeholder-ready BRD/FRD generation for internal governance reviews."),
  h2("2.3 Business Value"),
  table(
    [
      new TableRow({ children: [headerCell("Value Driver", 3200), headerCell("Description", 6160)] }),
      new TableRow({ children: [cell("Analyst Productivity", { bold: true }), cell("Reduces manual disclosure review from hours per company to minutes through automated parsing and diffs.")] }),
      new TableRow({ children: [cell("Risk Detection", { bold: true }), cell("Early surfacing of governance red flags such as independent-director resignations or committee gaps.")] }),
      new TableRow({ children: [cell("Regulatory Assurance", { bold: true }), cell("Automated SEBI LODR checks reduce manual compliance review cycles and audit risk.")] }),
      new TableRow({ children: [cell("Institutional Memory", { bold: true }), cell("DIN-based search builds a cross-company director history impossible to reconstruct from PDFs.")] }),
      new TableRow({ children: [cell("Cost Efficiency", { bold: true }), cell("Displaces multiple paid data-vendor subscriptions with a single, focused governance workspace.")] }),
    ],
    [3200, 6160],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 3. Business Objectives ----------
const objectives = [
  h1("3. Business Objectives"),
  h2("3.1 Strategic Goals"),
  bullet("Become the default governance intelligence workspace for Indian listed-company analysts."),
  bullet("Achieve end-to-end coverage of NSE-listed companies for board, KMP, shareholding, and compliance data."),
  bullet("Deliver verifiable, audit-grade change detection that analysts can cite in research notes."),
  bullet("Build a sustainable SaaS product with tiered access (free watchlist, paid compliance analytics)."),
  h2("3.2 Measurable Objectives (First 12 Months)"),
  table(
    [
      new TableRow({ children: [headerCell("Objective", 4200), headerCell("Metric / Target", 5160)] }),
      new TableRow({ children: [cell("Company coverage"), cell("Ingest governance filings for \u2265 1,500 NSE-listed companies.")] }),
      new TableRow({ children: [cell("Change-detection latency"), cell("Surface new filings within 24 hours of NSE publication.")] }),
      new TableRow({ children: [cell("User adoption"), cell("\u2265 500 authenticated active users within 6 months of launch.")] }),
      new TableRow({ children: [cell("Accuracy"), cell("\u2265 98% parse accuracy on board-composition and KMP fields (sampled audit).")] }),
      new TableRow({ children: [cell("Compliance rule coverage"), cell("Automate \u2265 15 SEBI LODR checks by end of Year 1.")] }),
      new TableRow({ children: [cell("Availability"), cell("\u2265 99.5% monthly uptime on production hosting.")] }),
    ],
    [4200, 5160],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 4. Scope ----------
const scope = [
  h1("4. Scope"),
  h2("4.1 In Scope"),
  bullet("Ingestion of NSE announcements, shareholding filings (XBRL), and corporate governance reports."),
  bullet("PDF parsing and structured extraction of board composition, committees, KMP, and shareholding patterns."),
  bullet("Multi-user authentication via Supabase with per-user data isolation for watchlists and preferences."),
  bullet("Per-user watchlists, company dashboards, and filing history views."),
  bullet("Quarter-over-quarter diff engine for governance changes."),
  bullet("DIN-based director search across all companies present in the platform's database."),
  bullet("SEBI LODR compliance checks with pass/fail verdicts and human-readable explanations."),
  bullet("Export of BRD/FRD documents and compliance reports in Word format."),
  bullet("Deployment on Railway with SQLite on persistent volume and Supabase for authentication."),
  h2("4.2 Out of Scope (Baseline Release)"),
  bullet("BSE-listed companies not dual-listed on NSE."),
  bullet("Unlisted private company filings (MCA only)."),
  bullet("Investment advice, buy/sell recommendations, or portfolio management."),
  bullet("Automated execution of corporate actions or regulatory filings on behalf of users."),
  bullet("Real-time market data, price feeds, or trading functionality."),
  bullet("Non-English disclosure translation (platform operates in English)."),
  h2("4.3 Assumptions"),
  bullet("NSE continues to publish governance disclosures through its public announcement and XBRL APIs."),
  bullet("SEBI LODR regulations remain broadly stable; rule changes will be applied through configuration updates."),
  bullet("Users have stable broadband and a modern browser (Chrome, Edge, Safari, Firefox latest two versions)."),
  bullet("Supabase free / pro tiers provide sufficient auth throughput at target user scale."),
  h2("4.4 Constraints"),
  bullet("Dependent on public availability and stability of NSE and regulator endpoints."),
  bullet("Governance disclosures are published at varying formats and quality; parsing must be resilient to schema drift."),
  bullet("Initial infrastructure budget is constrained to Railway hobby / pro tiers and Supabase free / pro tiers."),
  bullet("Product must comply with Indian data-protection norms (DPDP Act) for any personal data processed."),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 5. Stakeholders and Users ----------
const stakeholders = [
  h1("5. Stakeholders and User Personas"),
  h2("5.1 Primary Personas"),
  h3("Governance Analyst (Priya)"),
  p("Works at a proxy-advisory or buy-side research firm. Tracks 40\u2013100 companies, needs to flag board changes, committee gaps, and promoter-pledge increases each quarter. Wants structured data she can cite, not PDFs she has to re-read."),
  h3("Corporate Lawyer (Rohan)"),
  p("Advises listed clients on SEBI LODR compliance. Uses Delta Filings to run pre-filing compliance checks, review a client's board independence ratios, and audit committee composition across quarters."),
  h3("Institutional Investor (Meera)"),
  p("Runs a mid-cap fund. Needs early signals on governance deterioration \u2014 independent-director resignations, auditor changes, KMP churn \u2014 to inform investment decisions."),
  h3("Compliance Officer (Arun)"),
  p("Employed in-house at a listed company. Uses the platform to benchmark his company's governance disclosures against peers and to validate his own quarterly filings before submission."),
  h2("5.2 Stakeholder Map"),
  table(
    [
      new TableRow({ children: [headerCell("Stakeholder", 2600), headerCell("Interest", 3600), headerCell("Influence", 1600), headerCell("Engagement", 1560)] }),
      new TableRow({ children: [cell("End Users"), cell("Accurate, timely governance intelligence."), cell("High", { align: AlignmentType.CENTER }), cell("Continuous feedback")] }),
      new TableRow({ children: [cell("Product Owner"), cell("Business value and roadmap delivery."), cell("High", { align: AlignmentType.CENTER }), cell("Weekly reviews")] }),
      new TableRow({ children: [cell("Engineering"), cell("Technical feasibility, quality, and scale."), cell("High", { align: AlignmentType.CENTER }), cell("Daily standups")] }),
      new TableRow({ children: [cell("Compliance SME"), cell("Regulatory accuracy of checks."), cell("Medium", { align: AlignmentType.CENTER }), cell("Per-rule review")] }),
      new TableRow({ children: [cell("Data Providers"), cell("Terms of use and rate limits."), cell("Medium", { align: AlignmentType.CENTER }), cell("Contractual")] }),
      new TableRow({ children: [cell("Regulators"), cell("No regulatory impact expected."), cell("Low", { align: AlignmentType.CENTER }), cell("Informational")] }),
    ],
    [2600, 3600, 1600, 1560],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 6. Current vs Future State ----------
const stateAnalysis = [
  h1("6. Current-State vs Future-State"),
  table(
    [
      new TableRow({ children: [headerCell("Capability", 2600), headerCell("Current State (Manual)", 3400), headerCell("Future State (Delta Filings)", 3360)] }),
      new TableRow({ children: [cell("Filing discovery"), cell("Analyst visits NSE portal daily, downloads PDFs."), cell("Filings auto-ingested and surfaced in a single watchlist feed.")] }),
      new TableRow({ children: [cell("Board composition"), cell("Re-read PDF every quarter."), cell("Structured board table with change markers versus previous period.")] }),
      new TableRow({ children: [cell("Director history"), cell("Impossible to reconstruct from PDFs."), cell("DIN-based search reveals every company a director serves on.")] }),
      new TableRow({ children: [cell("Compliance checks"), cell("Manual spreadsheet against LODR clauses."), cell("Rule-based engine with pass/fail verdicts and reasoning.")] }),
      new TableRow({ children: [cell("Change detection"), cell("Error-prone diffs by hand."), cell("Automated period-over-period diff engine for governance data.")] }),
      new TableRow({ children: [cell("Reporting"), cell("Analyst writes reports from scratch."), cell("One-click BRD/FRD/compliance Word exports.")] }),
    ],
    [2600, 3400, 3360],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 7. Business Requirements ----------
function br(id, title, desc, priority) {
  return new TableRow({
    children: [
      cell(id, { bold: true, width: 1200 }),
      cell(title, { width: 3000 }),
      cell(desc, { width: 4160 }),
      cell(priority, { width: 1000, align: AlignmentType.CENTER }),
    ],
  });
}
const businessReqs = [
  h1("7. Business Requirements"),
  p("Business requirements describe what the business needs the system to enable, independent of implementation. Priorities follow the MoSCoW convention (M = Must, S = Should, C = Could, W = Won't this release)."),
  h2("7.1 Data Acquisition and Coverage"),
  table(
    [
      new TableRow({ children: [headerCell("ID", 1200), headerCell("Requirement", 3000), headerCell("Description", 4160), headerCell("Priority", 1000)] }),
      br("BR-01", "NSE filings ingestion", "The system shall ingest NSE corporate filings and announcements on a scheduled basis.", "M"),
      br("BR-02", "XBRL governance ingestion", "The system shall ingest XBRL corporate governance reports for every covered company.", "M"),
      br("BR-03", "Shareholding pattern ingestion", "The system shall ingest quarterly shareholding pattern disclosures.", "M"),
      br("BR-04", "Historical backfill", "The system shall allow historical backfill of at least 8 prior quarters per company.", "S"),
      br("BR-05", "Coverage expansion", "The system should allow administrators to add new companies without code changes.", "S"),
    ],
    [1200, 3000, 4160, 1000],
  ),
  spacer(200),
  h2("7.2 User Experience"),
  table(
    [
      new TableRow({ children: [headerCell("ID", 1200), headerCell("Requirement", 3000), headerCell("Description", 4160), headerCell("Priority", 1000)] }),
      br("BR-06", "Authenticated access", "Only authenticated users shall access company data, watchlists, and compliance features.", "M"),
      br("BR-07", "Per-user watchlist", "Each user shall have a private watchlist, not visible or editable by any other user.", "M"),
      br("BR-08", "Dashboard view", "Users shall see a unified dashboard of filings, alerts, and compliance status for their watchlist.", "M"),
      br("BR-09", "Filing detail page", "Users shall be able to drill into any filing and see the full structured content.", "M"),
      br("BR-10", "Export to Word", "Users should be able to export key artifacts (BRD, FRD, compliance reports) in Word format.", "S"),
    ],
    [1200, 3000, 4160, 1000],
  ),
  spacer(200),
  h2("7.3 Analytics and Intelligence"),
  table(
    [
      new TableRow({ children: [headerCell("ID", 1200), headerCell("Requirement", 3000), headerCell("Description", 4160), headerCell("Priority", 1000)] }),
      br("BR-11", "Diff engine", "The system shall compute and display changes in board, committees, KMP and shareholding across periods.", "M"),
      br("BR-12", "DIN search (cross-company)", "The system shall allow a user to search a DIN and see every company in the database on whose board that director serves or has served.", "M"),
      br("BR-13", "Director history timeline", "The system should present a chronological history of a director's board memberships.", "S"),
      br("BR-14", "Peer benchmarking", "The system could allow users to compare governance metrics across selected peers.", "C"),
      br("BR-15", "Alerting", "The system should notify users when material governance changes occur in their watchlist.", "S"),
    ],
    [1200, 3000, 4160, 1000],
  ),
  spacer(200),
  h2("7.4 Compliance"),
  table(
    [
      new TableRow({ children: [headerCell("ID", 1200), headerCell("Requirement", 3000), headerCell("Description", 4160), headerCell("Priority", 1000)] }),
      br("BR-16", "LODR rule engine", "The system shall implement a rule engine for SEBI LODR corporate governance requirements.", "M"),
      br("BR-17", "Independence ratio check", "The system shall validate board independence ratios against LODR thresholds.", "M"),
      br("BR-18", "Committee composition", "The system shall validate audit, NRC, SRC and RMC committee composition against LODR rules.", "M"),
      br("BR-19", "Plain-language reasoning", "Every compliance verdict shall be accompanied by a human-readable explanation.", "M"),
      br("BR-20", "Rule versioning", "The rule engine shall support versioned rule sets to reflect regulatory updates over time.", "S"),
    ],
    [1200, 3000, 4160, 1000],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 8. Functional Requirements ----------
function fr(id, area, text) {
  return new TableRow({
    children: [
      cell(id, { bold: true, width: 1200 }),
      cell(area, { width: 2400 }),
      cell(text, { width: 5760 }),
    ],
  });
}
const functionalReqs = [
  h1("8. Functional Requirements (Summary)"),
  p("The following list captures the headline functional requirements. Detailed use-cases, data contracts, and acceptance criteria are maintained in the companion FRD."),
  table(
    [
      new TableRow({ children: [headerCell("ID", 1200), headerCell("Area", 2400), headerCell("Functional Requirement", 5760)] }),
      fr("FR-01", "Authentication", "Users sign up and sign in via Supabase email/password; sessions are stored in secure server-side cookies."),
      fr("FR-02", "Onboarding", "New users select an industry or companies of interest; the system seeds their watchlist accordingly."),
      fr("FR-03", "Watchlist", "Users can add, remove and reorder companies on their personal watchlist."),
      fr("FR-04", "Filing feed", "The feed lists filings for watchlist companies in reverse chronological order with filters by type and period."),
      fr("FR-05", "Company page", "Each company page shows board, committees, KMP, shareholding, and filing history."),
      fr("FR-06", "Diff view", "For each governance dimension, the UI shows added, removed and modified rows versus the prior period."),
      fr("FR-07", "DIN search", "A single-field search box accepts a DIN and returns all companies plus role history present in the database."),
      fr("FR-08", "Compliance dashboard", "A compliance tab shows rule status per company, drillable to rule-level reasoning."),
      fr("FR-09", "Exports", "Users can download BRD, FRD and compliance reports as Word documents."),
      fr("FR-10", "Admin refresh", "Administrators can trigger on-demand ingestion and re-computation of compliance for any company."),
    ],
    [1200, 2400, 5760],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 9. Non-Functional Requirements ----------
function nfr(id, category, text, target) {
  return new TableRow({
    children: [
      cell(id, { bold: true, width: 1200 }),
      cell(category, { width: 2000 }),
      cell(text, { width: 4000 }),
      cell(target, { width: 2160 }),
    ],
  });
}
const nfrSection = [
  h1("9. Non-Functional Requirements"),
  table(
    [
      new TableRow({ children: [headerCell("ID", 1200), headerCell("Category", 2000), headerCell("Requirement", 4000), headerCell("Target", 2160)] }),
      nfr("NFR-01", "Performance", "Dashboard and watchlist pages load within acceptable latency on broadband.", "P95 \u2264 2.5 s"),
      nfr("NFR-02", "Scalability", "Architecture supports horizontal scaling of the web tier behind a load balancer.", "\u2265 10k MAU"),
      nfr("NFR-03", "Availability", "Production platform remains available during business hours.", "\u2265 99.5% monthly"),
      nfr("NFR-04", "Security", "All traffic is served over HTTPS with modern TLS.", "TLS 1.2+ only"),
      nfr("NFR-05", "Data isolation", "Per-user data (watchlist, preferences) is strictly isolated between users.", "0 cross-user leaks"),
      nfr("NFR-06", "Auditability", "All ingestion, compliance and admin actions are logged with timestamps and actor IDs.", "Full audit trail"),
      nfr("NFR-07", "Backups", "The primary database is backed up on a defined schedule with tested restore.", "Daily, \u2264 24 h RPO"),
      nfr("NFR-08", "Observability", "Operational metrics and errors are streamed to a monitoring tool.", "Alerting on errors"),
      nfr("NFR-09", "Accessibility", "User interface meets WCAG 2.1 AA for core journeys.", "WCAG 2.1 AA"),
      nfr("NFR-10", "Privacy", "Processing of personal data complies with the Indian DPDP Act.", "DPDP-aligned"),
    ],
    [1200, 2000, 4000, 2160],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 10. Data and Integration ----------
const dataSection = [
  h1("10. Data and Integration"),
  h2("10.1 Data Sources"),
  bullet("NSE public announcements and corporate filings feeds."),
  bullet("NSE XBRL corporate governance reports and shareholding pattern filings."),
  bullet("Internal compliance rule catalog curated from SEBI LODR and related circulars."),
  h2("10.2 Core Data Entities"),
  table(
    [
      new TableRow({ children: [headerCell("Entity", 2400), headerCell("Description", 6960)] }),
      new TableRow({ children: [cell("Company"), cell("An NSE-listed entity identified by symbol, ISIN, and registered name.")] }),
      new TableRow({ children: [cell("Filing"), cell("A disclosure instance with type, period, source URL, ingestion metadata and parsed payload.")] }),
      new TableRow({ children: [cell("Director"), cell("A person identified by DIN, with role, category, and tenure dates per company.")] }),
      new TableRow({ children: [cell("Committee"), cell("A board committee (audit, NRC, SRC, RMC, etc.) with members and chair.")] }),
      new TableRow({ children: [cell("Shareholding"), cell("Period-snapshot of shareholding pattern across promoter and public categories.")] }),
      new TableRow({ children: [cell("Compliance Verdict"), cell("Pass/fail result of a LODR rule against a company and period, with reasoning.")] }),
      new TableRow({ children: [cell("User"), cell("Authenticated account managed via Supabase, with a private watchlist.")] }),
    ],
    [2400, 6960],
  ),
  spacer(200),
  h2("10.3 Integration Model"),
  bullet("Supabase provides user authentication, identity, and profile metadata."),
  bullet("Scheduled jobs poll NSE endpoints and enqueue new filings for parsing."),
  bullet("Parsed, normalized data is persisted to the relational store used by the Flask application."),
  bullet("Exports are generated on-demand using server-side document templating."),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 11. Risks and Mitigations ----------
function risk(id, desc, impact, likelihood, mit) {
  return new TableRow({
    children: [
      cell(id, { bold: true, width: 900 }),
      cell(desc, { width: 3400 }),
      cell(impact, { width: 1200, align: AlignmentType.CENTER }),
      cell(likelihood, { width: 1400, align: AlignmentType.CENTER }),
      cell(mit, { width: 2460 }),
    ],
  });
}
const risks = [
  h1("11. Risks, Assumptions and Mitigations"),
  table(
    [
      new TableRow({ children: [headerCell("ID", 900), headerCell("Risk", 3400), headerCell("Impact", 1200), headerCell("Likelihood", 1400), headerCell("Mitigation", 2460)] }),
      risk("R-01", "NSE endpoint changes break ingestion.", "High", "Medium", "Pluggable fetchers, alerting on parse failures, short SLA for fixes."),
      risk("R-02", "XBRL schema drift corrupts parsed data.", "High", "Medium", "Schema validation layer and golden-file regression tests per release."),
      risk("R-03", "Compliance-rule misinterpretation produces false verdicts.", "High", "Medium", "SME review on every rule; clear disclaimer; versioned rule set."),
      risk("R-04", "Personal data exposure across users.", "Critical", "Low", "Per-user isolation enforced in DB schema and covered by integration tests."),
      risk("R-05", "Hosting platform outage.", "Medium", "Low", "99.5% SLA target, backup restoration runbooks, status-page communication."),
      risk("R-06", "Supabase auth quota exceeded.", "Medium", "Medium", "Plan monitoring, tier upgrade path, graceful sign-in error handling."),
    ],
    [900, 3400, 1200, 1400, 2460],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 12. Success Criteria ----------
const success = [
  h1("12. Success Criteria and KPIs"),
  h2("12.1 Launch Criteria"),
  bullet("All Must-have business requirements (priority M) are implemented and accepted."),
  bullet("Security review passes, including verification of per-user data isolation."),
  bullet("Documentation: BRD, FRD, operational runbook and rule catalog are signed off."),
  bullet("Pilot with at least 10 representative users completes two successful quarterly cycles."),
  h2("12.2 Post-Launch KPIs"),
  table(
    [
      new TableRow({ children: [headerCell("KPI", 3600), headerCell("Target", 2600), headerCell("Review Cadence", 3160)] }),
      new TableRow({ children: [cell("Monthly active users"), cell("500 within 6 months"), cell("Monthly")] }),
      new TableRow({ children: [cell("Watchlist adoption"), cell("\u2265 70% of users add \u2265 5 companies"), cell("Monthly")] }),
      new TableRow({ children: [cell("Compliance accuracy"), cell("\u2265 98% on audited sample"), cell("Quarterly")] }),
      new TableRow({ children: [cell("Average session duration"), cell("\u2265 6 minutes"), cell("Monthly")] }),
      new TableRow({ children: [cell("Ingestion freshness"), cell("\u2264 24 hours from NSE publish"), cell("Weekly")] }),
      new TableRow({ children: [cell("Customer satisfaction (CSAT)"), cell("\u2265 4.3 / 5"), cell("Quarterly")] }),
    ],
    [3600, 2600, 3160],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 13. Implementation Roadmap ----------
const roadmap = [
  h1("13. Implementation Roadmap"),
  table(
    [
      new TableRow({ children: [headerCell("Phase", 1800), headerCell("Timeline", 1800), headerCell("Key Deliverables", 5760)] }),
      new TableRow({ children: [cell("Phase 1 \u2014 Foundation", { bold: true }), cell("Months 1\u20132"), cell("Auth, watchlist, ingestion framework, baseline company dashboard.")] }),
      new TableRow({ children: [cell("Phase 2 \u2014 Intelligence", { bold: true }), cell("Months 3\u20134"), cell("Diff engine, DIN cross-company search, director history timeline.")] }),
      new TableRow({ children: [cell("Phase 3 \u2014 Compliance", { bold: true }), cell("Months 5\u20136"), cell("SEBI LODR rule engine, compliance dashboard and exports.")] }),
      new TableRow({ children: [cell("Phase 4 \u2014 Scale", { bold: true }), cell("Months 7\u20139"), cell("Coverage expansion, alerting, peer benchmarking, accessibility polish.")] }),
      new TableRow({ children: [cell("Phase 5 \u2014 Hardening", { bold: true }), cell("Months 10\u201312"), cell("Performance, observability, DR drills, DPDP compliance review.")] }),
    ],
    [1800, 1800, 5760],
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 14. Governance ----------
const governance = [
  h1("14. Project Governance"),
  h2("14.1 Decision Rights"),
  bullet("Scope and prioritization: Product Owner, with Engineering Lead concurrence."),
  bullet("Architecture and technology: Engineering Lead."),
  bullet("Regulatory interpretation: Compliance SME."),
  bullet("Release readiness: Product Owner + Engineering Lead joint sign-off."),
  h2("14.2 Change Management"),
  p("Any change to baseline scope, schedule or budget must be raised as a change request and approved by the Product Owner and Engineering Lead. Material regulatory changes are incorporated through versioned rule-set updates without requiring a new baseline."),
  h2("14.3 Communication Plan"),
  bullet("Daily: engineering standup."),
  bullet("Weekly: product review with stakeholders."),
  bullet("Monthly: business review covering KPIs, risks and roadmap."),
  bullet("Quarterly: steering committee and formal governance report."),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 15. Approvals & Glossary ----------
const approvals = [
  h1("15. Approvals"),
  p("By signing below, stakeholders approve the scope, objectives and requirements captured in this Business Requirements Document."),
  spacer(160),
  table(
    [
      new TableRow({ children: [headerCell("Name", 2600), headerCell("Role", 2600), headerCell("Signature", 2400), headerCell("Date", 1760)] }),
      new TableRow({ children: [cell(" "), cell("Product Owner"), cell(" "), cell(" ")] }),
      new TableRow({ children: [cell(" "), cell("Engineering Lead"), cell(" "), cell(" ")] }),
      new TableRow({ children: [cell(" "), cell("Compliance SME"), cell(" "), cell(" ")] }),
      new TableRow({ children: [cell(" "), cell("UX Lead"), cell(" "), cell(" ")] }),
    ],
    [2600, 2600, 2400, 1760],
  ),
  new Paragraph({ children: [new PageBreak()] }),
  h1("16. Glossary"),
  table(
    [
      new TableRow({ children: [headerCell("Term", 2400), headerCell("Definition", 6960)] }),
      new TableRow({ children: [cell("BRD"), cell("Business Requirements Document \u2014 describes business needs independent of solution.")] }),
      new TableRow({ children: [cell("FRD"), cell("Functional Requirements Document \u2014 describes system behavior to satisfy the BRD.")] }),
      new TableRow({ children: [cell("DIN"), cell("Director Identification Number, a unique ID issued by MCA to company directors in India.")] }),
      new TableRow({ children: [cell("KMP"), cell("Key Managerial Personnel as defined under the Companies Act, 2013.")] }),
      new TableRow({ children: [cell("LODR"), cell("SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015.")] }),
      new TableRow({ children: [cell("NSE"), cell("National Stock Exchange of India Ltd.")] }),
      new TableRow({ children: [cell("XBRL"), cell("eXtensible Business Reporting Language, a machine-readable disclosure format.")] }),
      new TableRow({ children: [cell("Watchlist"), cell("A private, per-user list of companies followed within Delta Filings.")] }),
      new TableRow({ children: [cell("Diff Engine"), cell("Component that computes period-over-period changes across governance data.")] }),
      new TableRow({ children: [cell("DPDP Act"), cell("Digital Personal Data Protection Act, 2023 (India).")] }),
    ],
    [2400, 6960],
  ),
];

// ---------- Assemble ----------
const doc = new Document({
  creator: "Delta Filings",
  title: "Delta Filings \u2014 Business Requirements Document",
  description: "BRD for Delta Filings, a Corporate Governance Intelligence Platform.",
  styles: {
    default: { document: { run: { font: "Calibri", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Calibri", color: ACCENT },
        paragraph: { spacing: { before: 360, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Calibri", color: ACCENT },
        paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, font: "Calibri", color: "333333" },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
          { level: 1, format: LevelFormat.BULLET, text: "\u25E6", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 1440, hanging: 360 } } } },
        ] },
      { reference: "numbers",
        levels: [
          { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        ] },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440, header: 720, footer: 720 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: ACCENT, space: 4 } },
          children: [
            new TextRun({ text: "Delta Filings \u2014 Business Requirements Document", size: 18, color: GREY_TEXT, font: "Calibri" }),
            new TextRun({ text: "\tCorporate Governance Intelligence Platform", size: 18, color: GREY_TEXT, font: "Calibri", italics: true }),
          ],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
          children: [
            new TextRun({ text: "Confidential \u2014 Internal Use", size: 18, color: GREY_TEXT, font: "Calibri" }),
            new TextRun({ text: "\tPage ", size: 18, color: GREY_TEXT, font: "Calibri" }),
            new TextRun({ children: [PageNumber.CURRENT], size: 18, color: GREY_TEXT, font: "Calibri" }),
            new TextRun({ text: " of ", size: 18, color: GREY_TEXT, font: "Calibri" }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 18, color: GREY_TEXT, font: "Calibri" }),
          ],
        })],
      }),
    },
    children: [
      ...cover,
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 240 },
        children: [new TextRun({ text: "Table of Contents", bold: true, size: 32, color: ACCENT, font: "Calibri" })],
      }),
      new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
      new Paragraph({ children: [new PageBreak()] }),
      ...documentControl,
      ...execSummary,
      ...objectives,
      ...scope,
      ...stakeholders,
      ...stateAnalysis,
      ...businessReqs,
      ...functionalReqs,
      ...nfrSection,
      ...dataSection,
      ...risks,
      ...success,
      ...roadmap,
      ...governance,
      ...approvals,
    ],
  }],
});

Packer.toBuffer(doc).then((buffer) => {
  const outPath = "/Users/prakharsingh/Documents/Study Files/python things/delta_filings/Delta_Filings_BRD.docx";
  fs.writeFileSync(outPath, buffer);
  console.log("Wrote:", outPath, buffer.length, "bytes");
});
