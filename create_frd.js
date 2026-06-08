const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
  BorderStyle, WidthType, ShadingType, PageNumber, PageBreak,
  TableOfContents,
} = require("docx");

// ── Design tokens ──
const INDIGO = "4F46E5";
const INDIGO_LIGHT = "EEF2FF";
const DARK = "0F172A";
const GRAY = "475569";
const LIGHT_GRAY = "F1F5F9";
const WHITE = "FFFFFF";
const GREEN = "10B981";
const GREEN_LIGHT = "ECFDF5";
const AMBER = "F59E0B";
const AMBER_LIGHT = "FFFBEB";
const RED = "EF4444";
const RED_LIGHT = "FEF2F2";

const border = { style: BorderStyle.SINGLE, size: 1, color: "D1D5DB" };
const borders = { top: border, bottom: border, left: border, right: border };
const noBorders = {
  top: { style: BorderStyle.NONE }, bottom: { style: BorderStyle.NONE },
  left: { style: BorderStyle.NONE }, right: { style: BorderStyle.NONE },
};
const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };

const TABLE_WIDTH = 9360;
const COL_20 = 1872;
const COL_25 = 2340;
const COL_30 = 2808;
const COL_35 = 3276;
const COL_40 = 3744;
const COL_50 = 4680;
const COL_60 = 5616;
const COL_70 = 6552;
const COL_80 = 7488;

// ── Helpers ──
function heading1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    children: [new TextRun({ text, bold: true, size: 36, font: "Arial", color: INDIGO })],
    spacing: { before: 400, after: 200 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: INDIGO, space: 8 } },
  });
}
function heading2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    children: [new TextRun({ text, bold: true, size: 28, font: "Arial", color: DARK })],
    spacing: { before: 300, after: 150 },
  });
}
function heading3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    children: [new TextRun({ text, bold: true, size: 24, font: "Arial", color: GRAY })],
    spacing: { before: 200, after: 100 },
  });
}
function para(text, opts = {}) {
  return new Paragraph({
    children: [new TextRun({ text, size: 22, font: "Arial", color: opts.color || GRAY, ...opts })],
    spacing: { after: 120 },
    alignment: opts.align,
  });
}
function boldPara(label, value) {
  return new Paragraph({
    children: [
      new TextRun({ text: label, bold: true, size: 22, font: "Arial", color: DARK }),
      new TextRun({ text: value, size: 22, font: "Arial", color: GRAY }),
    ],
    spacing: { after: 80 },
  });
}
function bullet(text, ref = "bullets") {
  return new Paragraph({
    numbering: { reference: ref, level: 0 },
    children: [new TextRun({ text, size: 22, font: "Arial", color: GRAY })],
    spacing: { after: 60 },
  });
}
function bulletBold(label, desc) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    children: [
      new TextRun({ text: label + " ", bold: true, size: 22, font: "Arial", color: DARK }),
      new TextRun({ text: desc, size: 22, font: "Arial", color: GRAY }),
    ],
    spacing: { after: 60 },
  });
}
function numberedItem(text) {
  return new Paragraph({
    numbering: { reference: "numbers", level: 0 },
    children: [new TextRun({ text, size: 22, font: "Arial", color: GRAY })],
    spacing: { after: 60 },
  });
}

function reqRow(cells, headerRow = false) {
  const widths = cells.length === 4 ? [COL_20, COL_20, COL_40, COL_20] :
                 cells.length === 3 ? [COL_25, COL_50, COL_25] :
                 cells.map(() => Math.floor(TABLE_WIDTH / cells.length));
  return new TableRow({
    children: cells.map((text, i) =>
      new TableCell({
        borders, width: { size: widths[i], type: WidthType.DXA },
        shading: { fill: headerRow ? INDIGO : (i === 0 && !headerRow ? INDIGO_LIGHT : WHITE), type: ShadingType.CLEAR },
        margins: cellMargins,
        children: [new Paragraph({
          children: [new TextRun({ text, bold: headerRow || i === 0, size: 20, font: "Arial", color: headerRow ? WHITE : (i === 0 ? INDIGO : GRAY) })],
        })],
      })
    ),
  });
}

function reqTable4(rows) {
  return new Table({
    width: { size: TABLE_WIDTH, type: WidthType.DXA },
    columnWidths: [COL_20, COL_20, COL_40, COL_20],
    rows: [reqRow(["Req ID", "Priority", "Requirement", "Status"], true), ...rows.map(r => reqRow(r))],
  });
}

function reqTable3(rows) {
  return new Table({
    width: { size: TABLE_WIDTH, type: WidthType.DXA },
    columnWidths: [COL_25, COL_50, COL_25],
    rows: [reqRow(["Req ID", "Requirement", "Priority"], true), ...rows.map(r => reqRow(r))],
  });
}

function featureRow(feature, detail, headerRow = false) {
  return new TableRow({
    children: [
      new TableCell({
        borders, width: { size: COL_30, type: WidthType.DXA },
        shading: { fill: headerRow ? INDIGO : INDIGO_LIGHT, type: ShadingType.CLEAR },
        margins: cellMargins,
        children: [new Paragraph({ children: [new TextRun({ text: feature, bold: true, size: 20, font: "Arial", color: headerRow ? WHITE : INDIGO })] })],
      }),
      new TableCell({
        borders, width: { size: COL_70, type: WidthType.DXA },
        shading: { fill: headerRow ? INDIGO : WHITE, type: ShadingType.CLEAR },
        margins: cellMargins,
        children: [new Paragraph({ children: [new TextRun({ text: detail, bold: headerRow, size: 20, font: "Arial", color: headerRow ? WHITE : GRAY })] })],
      }),
    ],
  });
}

function featureTable(rows) {
  return new Table({
    width: { size: TABLE_WIDTH, type: WidthType.DXA },
    columnWidths: [COL_30, COL_70],
    rows: [featureRow("Component", "Specification", true), ...rows.map(r => featureRow(r[0], r[1]))],
  });
}

function spacer() {
  return new Paragraph({ spacing: { after: 100 }, children: [] });
}

function priorityBadge(text, color) {
  return new Paragraph({
    children: [new TextRun({ text, bold: true, size: 20, font: "Arial", color })],
    spacing: { after: 60 },
  });
}

// ── Build Document ──
const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, font: "Arial", color: INDIGO },
        paragraph: { spacing: { before: 400, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: "Arial", color: DARK },
        paragraph: { spacing: { before: 300, after: 150 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: GRAY },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbers",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ],
  },
  sections: [
    // ═══════════════ COVER PAGE ═══════════════
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      children: [
        spacer(), spacer(), spacer(), spacer(), spacer(), spacer(),
        new Paragraph({
          alignment: AlignmentType.CENTER, spacing: { after: 100 },
          children: [new TextRun({ text: "DELTA FILINGS", size: 72, bold: true, font: "Arial", color: INDIGO })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER, spacing: { after: 60 },
          children: [new TextRun({ text: "Corporate Governance Intelligence Platform", size: 32, font: "Arial", color: GRAY })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER, spacing: { after: 400 },
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: INDIGO, space: 20 } },
          children: [],
        }),
        spacer(),
        new Paragraph({
          alignment: AlignmentType.CENTER, spacing: { after: 80 },
          children: [new TextRun({ text: "Functional Requirements Document", size: 36, bold: true, font: "Arial", color: DARK })],
        }),
        spacer(), spacer(),
        new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Version 1.0", size: 24, font: "Arial", color: GRAY })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 }, children: [new TextRun({ text: "April 2026", size: 24, font: "Arial", color: GRAY })] }),
        spacer(), spacer(), spacer(), spacer(),
        new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "CONFIDENTIAL", size: 20, bold: true, font: "Arial", color: INDIGO })] }),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ DOCUMENT INFO ═══════════════
        heading1("Document Information"),
        new Table({
          width: { size: TABLE_WIDTH, type: WidthType.DXA },
          columnWidths: [COL_30, COL_70],
          rows: [
            ["Document Title", "Functional Requirements Document - Delta Filings"],
            ["Version", "1.0"],
            ["Date", "14 April 2026"],
            ["Author", "Delta Filings Product Team"],
            ["Status", "Draft"],
            ["Classification", "Confidential"],
            ["Related Documents", "Business Requirements Document (BRD) v1.0"],
          ].map(([k, v]) => new TableRow({
            children: [
              new TableCell({ borders, width: { size: COL_30, type: WidthType.DXA }, shading: { fill: INDIGO_LIGHT, type: ShadingType.CLEAR }, margins: cellMargins,
                children: [new Paragraph({ children: [new TextRun({ text: k, bold: true, size: 20, font: "Arial", color: INDIGO })] })] }),
              new TableCell({ borders, width: { size: COL_70, type: WidthType.DXA }, margins: cellMargins,
                children: [new Paragraph({ children: [new TextRun({ text: v, size: 20, font: "Arial", color: GRAY })] })] }),
            ],
          })),
        }),

        spacer(),
        heading1("Revision History"),
        new Table({
          width: { size: TABLE_WIDTH, type: WidthType.DXA },
          columnWidths: [COL_20, COL_20, COL_40, COL_20],
          rows: [
            reqRow(["Version", "Date", "Description", "Author"], true),
            reqRow(["1.0", "14 Apr 2026", "Initial FRD - Complete functional specifications", "Product Team"]),
          ],
        }),

        spacer(),
        new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
        new Paragraph({ children: [new PageBreak()] }),
      ],
    },

    // ═══════════════ MAIN CONTENT ═══════════════
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: INDIGO, space: 4 } },
            children: [
              new TextRun({ text: "Delta Filings  ", bold: true, size: 18, font: "Arial", color: INDIGO }),
              new TextRun({ text: "|  Functional Requirements Document", size: 18, font: "Arial", color: GRAY }),
            ],
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.RIGHT,
            border: { top: { style: BorderStyle.SINGLE, size: 1, color: "D1D5DB", space: 4 } },
            children: [
              new TextRun({ text: "Confidential  |  Page ", size: 16, font: "Arial", color: GRAY }),
              new TextRun({ children: [PageNumber.CURRENT], size: 16, font: "Arial", color: INDIGO }),
            ],
          })],
        }),
      },
      children: [

        // ═══════════════ 1. INTRODUCTION ═══════════════
        heading1("1. Introduction"),

        heading2("1.1 Purpose"),
        para("This Functional Requirements Document (FRD) defines the detailed functional specifications for Delta Filings, a Corporate Governance Intelligence Platform. It translates the business requirements from the BRD into specific, testable system behaviors, user interactions, and acceptance criteria that guide development, testing, and validation."),

        heading2("1.2 Scope"),
        para("This document covers all functional modules of the Delta Filings platform:"),
        bullet("Real-time corporate filing ingestion and display from NSE India"),
        bullet("AI-powered document summarization using Google Gemini models"),
        bullet("Corporate governance data extraction and XBRL parsing"),
        bullet("Market data integration (stock prices, pre-open data, sector performance)"),
        bullet("Director Intelligence Network (DIN search and cross-referencing)"),
        bullet("Company watchlist management and monitoring"),
        bullet("Advanced search, filtering, and tag-based classification"),
        bullet("Settings and configuration management"),

        heading2("1.3 Intended Audience"),
        featureTable([
          ["Developers", "Detailed implementation specifications, API contracts, data models"],
          ["QA Engineers", "Acceptance criteria, test scenarios, edge case definitions"],
          ["Product Managers", "Feature scope, user flows, priority classifications"],
          ["Company Secretaries", "Governance workflows, compliance feature specifications"],
          ["Stakeholders", "Functional scope verification against business requirements"],
        ]),
        spacer(),

        heading2("1.4 Definitions and Conventions"),
        para("Priority levels used throughout this document:"),
        featureTable([
          ["P0 - Critical", "Core functionality. System cannot launch without this feature."],
          ["P1 - High", "Important feature required for production readiness."],
          ["P2 - Medium", "Enhances user experience. Can be delivered in subsequent release."],
          ["P3 - Low", "Nice-to-have. Planned for future roadmap."],
        ]),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 2. SYSTEM OVERVIEW ═══════════════
        heading1("2. System Overview"),

        heading2("2.1 Architecture Summary"),
        featureTable([
          ["Backend Framework", "Python Flask with Jinja2 templating engine"],
          ["Database", "SQLite with WAL mode for concurrent read access"],
          ["AI Engine", "Google Gemini API (2.5 Flash / 2.5 Pro) with fallback extractive summarizer"],
          ["Data Sources", "NSE India APIs (filings, governance, prices, pre-open, sectors)"],
          ["Frontend", "Server-rendered HTML with Tailwind CSS, vanilla JavaScript"],
          ["Deployment", "Single-server deployment, port 8080, no external dependencies"],
        ]),
        spacer(),

        heading2("2.2 User Roles"),
        para("The system currently operates as a single-user application with the following assumed personas:"),
        bulletBold("Company Secretary:", "Monitors board-level filings, governance changes, compliance submissions across multiple companies."),
        bulletBold("Compliance Officer:", "Tracks regulatory filings (SEBI LODR, Regulation 30/31/74), monitors deadlines."),
        bulletBold("Investment Analyst:", "Researches company filings, compares governance structures, tracks director movements."),
        bulletBold("Retail Investor:", "Monitors watchlisted companies, reads AI summaries, tracks stock prices alongside filings."),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 3. FUNCTIONAL REQUIREMENTS - DASHBOARD ═══════════════
        heading1("3. Dashboard Module"),

        heading2("3.1 Overview"),
        para("The dashboard is the primary landing page providing a real-time overview of corporate filing activity across the Indian stock market. It serves as the command center for governance intelligence."),

        heading2("3.2 Functional Requirements"),
        reqTable4([
          ["FR-DASH-001", "P0", "Display total filing count for current date with visual counter", "Implemented"],
          ["FR-DASH-002", "P0", "Show time-based greeting (Good Morning/Afternoon/Evening) based on server time", "Implemented"],
          ["FR-DASH-003", "P0", "List all companies with filings today, sorted by filing count descending", "Implemented"],
          ["FR-DASH-004", "P1", "Display filing count badge next to each company name", "Implemented"],
          ["FR-DASH-005", "P1", "Provide navigation to individual company filing views on click", "Implemented"],
          ["FR-DASH-006", "P2", "Show top categories breakdown with visual indicators", "Implemented"],
          ["FR-DASH-007", "P1", "Display market activity summary (total companies, total filings, top filers)", "Implemented"],
          ["FR-DASH-008", "P2", "Auto-refresh capability to pull latest filings without page reload", "Planned"],
        ]),
        spacer(),

        heading2("3.3 Dashboard Data Flow"),
        numberedItem("On page load, Flask route queries SQLite for all filings where date = today."),
        numberedItem("Filings are grouped by company symbol and counted."),
        numberedItem("Companies are sorted by filing count (descending) and rendered in card grid."),
        numberedItem("Category breakdown is computed using keyword-based tag detection (ai.detect_tags)."),
        numberedItem("Greeting is determined from datetime.now().hour on the server."),

        heading2("3.4 Acceptance Criteria"),
        bullet("Dashboard loads within 2 seconds with up to 500 filings."),
        bullet("Filing counts match the actual number of filings in the database for today."),
        bullet("Company cards are clickable and navigate to /company/<symbol> view."),
        bullet("Category tags accurately reflect filing content based on keyword matching."),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 4. COMPANY FILING VIEW ═══════════════
        heading1("4. Company Filing View Module"),

        heading2("4.1 Overview"),
        para("The company filing view provides a detailed, chronological display of all corporate announcements for a specific company. It is the primary workspace for analyzing individual company filings with AI-powered summarization, XBRL governance data, and document comparison tools."),

        heading2("4.2 Filing List Requirements"),
        reqTable4([
          ["FR-COMP-001", "P0", "Display all filings for a company in reverse chronological order", "Implemented"],
          ["FR-COMP-002", "P0", "Show filing title, date, time, exchange source (NSE/BSE), and category tags", "Implemented"],
          ["FR-COMP-003", "P0", "Expand/collapse filing details on click with smooth animation", "Implemented"],
          ["FR-COMP-004", "P1", "Display filing description and attachment links when expanded", "Implemented"],
          ["FR-COMP-005", "P0", "Link to original PDF/document on NSE India website", "Implemented"],
          ["FR-COMP-006", "P1", "Tag-based visual categorization using color-coded badges", "Implemented"],
          ["FR-COMP-007", "P1", "Refresh filings button to fetch latest from NSE API", "Implemented"],
          ["FR-COMP-008", "P2", "Infinite scroll or pagination for companies with 100+ filings", "Planned"],
        ]),
        spacer(),

        heading2("4.3 AI Summarization Requirements"),
        reqTable4([
          ["FR-SUM-001", "P0", "Generate AI summary on demand when user clicks Summarize button", "Implemented"],
          ["FR-SUM-002", "P0", "Support Google Gemini 2.5 Flash (free tier) as default summarization model", "Implemented"],
          ["FR-SUM-003", "P1", "Support Google Gemini 2.5 Pro as premium summarization option", "Implemented"],
          ["FR-SUM-004", "P0", "Cache summaries in SQLite to avoid redundant API calls", "Implemented"],
          ["FR-SUM-005", "P1", "Regenerate summary with higher temperature (0.7) on user request", "Implemented"],
          ["FR-SUM-006", "P0", "Fall back to extractive summarization when no API key is configured", "Implemented"],
          ["FR-SUM-007", "P1", "Display AI model badge (e.g., Gemini 2.5-flash) next to summary", "Implemented"],
          ["FR-SUM-008", "P1", "Auto-regenerate fallback summaries when API key is later configured", "Implemented"],
          ["FR-SUM-009", "P0", "Summarization prompt tuned for Indian corporate governance filings (SEBI, LODR, NSE/BSE)", "Implemented"],
          ["FR-SUM-010", "P2", "Batch summarize all filings for a company in one action", "Planned"],
        ]),
        spacer(),

        heading3("4.3.1 Summarization Prompt Specification"),
        para("The system prompt instructs the AI to act as a corporate governance analyst specializing in Indian stock market filings. Key directives:"),
        bullet("Produce 3-5 concise bullet points using the bullet marker character."),
        bullet("Focus on key decisions, governance changes, compliance matters, financial impact, and deadlines."),
        bullet("Be specific with names, numbers, and dates. No vague statements."),
        bullet("Skip boilerplate (addresses, sign-offs, registrar details)."),
        bullet("Identify routine compliance certificates and summarize in one bullet."),

        heading3("4.3.2 Fallback Extractive Summarizer"),
        para("When no API key is available, the system uses a rule-based extractive summarizer:"),
        bullet("Sentence scoring based on high-value governance keywords (appointed, resigned, dividend, board meeting, etc.)."),
        bullet("Noise filtering to remove boilerplate (Dear Sir/Madam, Yours faithfully, registrar addresses)."),
        bullet("Action-verb detection to prioritize substantive sentences."),
        bullet("Deduplication using word-overlap similarity (threshold: 0.7)."),
        bullet("Returns top 3 scored sentences as bullet points."),

        heading2("4.4 XBRL Data Extraction"),
        reqTable4([
          ["FR-XBRL-001", "P1", "Parse XBRL XML attachments from filings to extract structured governance data", "Implemented"],
          ["FR-XBRL-002", "P1", "Extract director information: name, DIN, designation, date of appointment", "Implemented"],
          ["FR-XBRL-003", "P1", "Extract committee membership details from XBRL corporate governance reports", "Implemented"],
          ["FR-XBRL-004", "P2", "Display XBRL data in formatted card layout within filing detail view", "Implemented"],
          ["FR-XBRL-005", "P2", "Handle missing or malformed XBRL gracefully with appropriate error messages", "Implemented"],
        ]),
        spacer(),

        heading2("4.5 Filing Comparison"),
        reqTable4([
          ["FR-CMP-001", "P2", "Compare two filings side-by-side showing text differences", "Implemented"],
          ["FR-CMP-002", "P2", "List comparable filings (same company) for user selection", "Implemented"],
          ["FR-CMP-003", "P2", "Highlight additions and deletions between filing versions", "Implemented"],
        ]),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 5. SEARCH & FILTERING ═══════════════
        heading1("5. Search and Filtering Module"),

        heading2("5.1 Overview"),
        para("The search module enables users to find specific filings within a company using full-text search, tag-based filtering, and date range queries. It operates within the company filing view context."),

        heading2("5.2 Functional Requirements"),
        reqTable4([
          ["FR-SRCH-001", "P1", "Full-text search across filing titles and descriptions within a company", "Implemented"],
          ["FR-SRCH-002", "P1", "Filter filings by tag category (Board Change, Compliance, Financial Results, etc.)", "Implemented"],
          ["FR-SRCH-003", "P2", "Date range filtering with from/to date pickers", "Implemented"],
          ["FR-SRCH-004", "P1", "Real-time search results as user types (debounced 300ms)", "Implemented"],
          ["FR-SRCH-005", "P1", "Company symbol search with autocomplete for navigation", "Implemented"],
          ["FR-SRCH-006", "P2", "Combine multiple filters (text + tag + date) in a single query", "Implemented"],
          ["FR-SRCH-007", "P3", "Search across all companies simultaneously (global search)", "Planned"],
        ]),
        spacer(),

        heading2("5.3 Tag Classification System"),
        para("Filings are automatically classified using a keyword-based tag detection system. The following tags are supported:"),
        featureTable([
          ["Board Change", "Keywords: board meeting, director, appointment, resignation, retire"],
          ["Compliance", "Keywords: compliance, regulation, sebi, lodr, certificate, rbi"],
          ["Financial Results", "Keywords: financial result, quarterly, revenue, profit, dividend"],
          ["Leadership", "Keywords: key managerial, ceo, cfo, cro, managing director, personnel"],
          ["Restructuring", "Keywords: scheme, arrangement, demerger, merger, acquisition"],
          ["AGM/EGM", "Keywords: annual general, extraordinary general, agm, egm, resolution"],
          ["Credit/Rating", "Keywords: credit rating, rating update, npa, provisioning"],
          ["Insider Trading", "Keywords: trading window, insider trading, prohibition"],
          ["Investor Relations", "Keywords: analyst, investor meet, conference call, investor presentation"],
          ["Divestment", "Keywords: disinvestment, divestment, diversification, stake sale"],
          ["Stock Options", "Keywords: stock option, esop, performance share, grant of"],
          ["General", "Default tag when no specific keywords match"],
        ]),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 6. WATCHLIST ═══════════════
        heading1("6. Watchlist Module"),

        heading2("6.1 Overview"),
        para("The watchlist enables users to monitor specific companies of interest. Watchlisted companies appear in a dedicated sidebar section for quick access and are tracked across sessions using persistent SQLite storage."),

        heading2("6.2 Functional Requirements"),
        reqTable4([
          ["FR-WL-001", "P1", "Add any company to watchlist via star/bookmark toggle on company page", "Implemented"],
          ["FR-WL-002", "P1", "Remove company from watchlist via toggle or dedicated remove action", "Implemented"],
          ["FR-WL-003", "P1", "Display watchlisted companies in sidebar with filing count badges", "Implemented"],
          ["FR-WL-004", "P1", "Persist watchlist across browser sessions in SQLite database", "Implemented"],
          ["FR-WL-005", "P2", "Show latest filing date/time for each watchlisted company", "Implemented"],
          ["FR-WL-006", "P2", "Optional notes field when adding company to watchlist", "Implemented"],
          ["FR-WL-007", "P3", "Email/push notifications when watchlisted company files new announcement", "Planned"],
        ]),
        spacer(),

        heading2("6.3 API Specification"),
        featureTable([
          ["GET /api/watchlist", "Returns array of watchlisted companies with symbol, name, notes, and added_at timestamp"],
          ["POST /api/watchlist", "Adds company to watchlist. Body: {symbol, name, notes?}. Returns 201 on success."],
          ["DELETE /api/watchlist/<symbol>", "Removes company from watchlist. Returns 200 on success, 404 if not found."],
        ]),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 7. MARKET DATA ═══════════════
        heading1("7. Market Data Module"),

        heading2("7.1 Stock Prices Page"),
        reqTable4([
          ["FR-MKT-001", "P1", "Display real-time stock prices from NSE India pre-open and market data", "Implemented"],
          ["FR-MKT-002", "P1", "Show OHLC (Open, High, Low, Close), volume, and change percentage", "Implemented"],
          ["FR-MKT-003", "P1", "Color-coded price changes (green for positive, red for negative)", "Implemented"],
          ["FR-MKT-004", "P2", "Search/filter stocks by symbol on the prices page", "Implemented"],
          ["FR-MKT-005", "P2", "Link from price row to company filing view for quick navigation", "Implemented"],
        ]),
        spacer(),

        heading2("7.2 Pre-Open Market Data"),
        reqTable4([
          ["FR-PRE-001", "P2", "Fetch and display NSE pre-open market data for NIFTY 50 stocks", "Implemented"],
          ["FR-PRE-002", "P2", "Show pre-open price, change, previous close, and total trade quantity", "Implemented"],
          ["FR-PRE-003", "P2", "Auto-available during pre-open session (9:00-9:15 AM IST)", "Implemented"],
        ]),
        spacer(),

        heading2("7.3 Sector Performance"),
        reqTable4([
          ["FR-SEC-001", "P2", "Display sector-wise market performance indices from NSE", "Implemented"],
          ["FR-SEC-002", "P2", "Show sector name, current value, change, and percentage change", "Implemented"],
          ["FR-SEC-003", "P3", "Historical sector trend comparison", "Planned"],
        ]),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 8. CORPORATE GOVERNANCE ═══════════════
        heading1("8. Corporate Governance Module"),

        heading2("8.1 Overview"),
        para("The governance module provides deep analysis of corporate governance structures by parsing XBRL filings submitted to NSE under SEBI LODR regulations. This is a key differentiator positioning Delta Filings as a governance intelligence tool rather than a simple filing aggregator."),

        heading2("8.2 Functional Requirements"),
        reqTable4([
          ["FR-GOV-001", "P1", "Retrieve corporate governance XBRL reports for any listed company", "Implemented"],
          ["FR-GOV-002", "P1", "Parse board composition: executive, non-executive, independent directors", "Implemented"],
          ["FR-GOV-003", "P1", "Extract committee details: Audit, Nomination, Stakeholder, CSR committees", "Implemented"],
          ["FR-GOV-004", "P1", "Display director profiles with DIN, designation, appointment date", "Implemented"],
          ["FR-GOV-005", "P2", "Compare governance reports across two periods to identify changes", "Implemented"],
          ["FR-GOV-006", "P2", "Identify board composition compliance (ratio of independent directors)", "Implemented"],
          ["FR-GOV-007", "P3", "Cross-reference directors across multiple companies via DIN", "Implemented"],
          ["FR-GOV-008", "P3", "Flag governance anomalies (missing committees, overdue appointments)", "Planned"],
        ]),
        spacer(),

        heading2("8.3 XBRL Parsing Specification"),
        para("The system parses XBRL XML documents using Python lxml library with the following data extraction rules:"),
        bullet("Namespace handling for in-bse-cgr and in-nse-cgr XBRL taxonomies."),
        bullet("Director extraction: iterates xbrli:context elements to find director-scoped data."),
        bullet("Committee extraction: parses committee membership elements linked to director contexts."),
        bullet("Date normalization: converts all date formats to ISO 8601 (YYYY-MM-DD)."),
        bullet("Graceful degradation: returns partial data if some XBRL fields are missing or malformed."),

        heading2("8.4 Governance Comparison"),
        para("Users can compare two governance reports to track changes over time:"),
        bullet("Board composition changes: new appointments, resignations, re-designations."),
        bullet("Committee restructuring: member additions/removals, new committee formation."),
        bullet("Director tenure tracking: identify directors approaching term limits."),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 9. DIN SEARCH ═══════════════
        heading1("9. Director Intelligence Network (DIN Search)"),

        heading2("9.1 Overview"),
        para("The DIN Search module allows users to search for directors across all listed companies using their Director Identification Number. This enables cross-company analysis of director networks and interlocking directorates."),

        heading2("9.2 Functional Requirements"),
        reqTable4([
          ["FR-DIN-001", "P2", "Search directors by DIN number across all governance filings", "Implemented"],
          ["FR-DIN-002", "P2", "Display all companies where a director holds positions", "Implemented"],
          ["FR-DIN-003", "P2", "Show director designation and appointment date at each company", "Implemented"],
          ["FR-DIN-004", "P3", "Director network visualization showing interconnected boards", "Planned"],
          ["FR-DIN-005", "P3", "Alert when a tracked director joins or leaves a company board", "Planned"],
        ]),
        spacer(),

        heading2("9.3 Data Flow"),
        numberedItem("User enters DIN number on the DIN Search page (/din-search)."),
        numberedItem("System calls /api/din-search with the DIN as query parameter."),
        numberedItem("Backend searches cached XBRL governance data for matching director records."),
        numberedItem("Results are aggregated across all companies and returned with company name, designation, and dates."),
        numberedItem("Frontend renders results in a card-based layout grouped by company."),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 10. SETTINGS & CONFIGURATION ═══════════════
        heading1("10. Settings and Configuration Module"),

        heading2("10.1 Overview"),
        para("The settings module provides a user interface for configuring AI summarization preferences, including API key management and model selection. Settings are persisted in the SQLite database."),

        heading2("10.2 Functional Requirements"),
        reqTable4([
          ["FR-SET-001", "P1", "Settings modal accessible from sidebar navigation (gear icon)", "Implemented"],
          ["FR-SET-002", "P1", "Input field for Google Gemini API key with masked display", "Implemented"],
          ["FR-SET-003", "P1", "Test API key functionality to validate before saving", "Implemented"],
          ["FR-SET-004", "P1", "Model selection between Gemini 2.5 Flash (free) and Gemini 2.5 Pro (paid)", "Implemented"],
          ["FR-SET-005", "P1", "Display model cards with name, description, speed, and cost information", "Implemented"],
          ["FR-SET-006", "P1", "Visual indicator (status dot) showing whether AI is configured", "Implemented"],
          ["FR-SET-007", "P1", "Persist settings across sessions in SQLite settings table", "Implemented"],
          ["FR-SET-008", "P2", "Link to Google AI Studio for API key generation", "Implemented"],
        ]),
        spacer(),

        heading2("10.3 Settings Data Model"),
        featureTable([
          ["Table", "settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"],
          ["gemini_api_key", "Encrypted/stored API key for Google Gemini service"],
          ["gemini_model", "Selected model identifier (gemini-2.5-flash or gemini-2.5-pro)"],
          ["Upsert Logic", "INSERT ... ON CONFLICT(key) DO UPDATE SET value = ? (SQLite upsert)"],
        ]),
        spacer(),

        heading2("10.4 API Key Validation Flow"),
        numberedItem("User enters API key in the settings modal input field."),
        numberedItem("User clicks Test Key button, triggering POST /api/settings/test-key."),
        numberedItem("Backend sends a minimal test request to Gemini API with the provided key."),
        numberedItem("On success: green checkmark and success message displayed."),
        numberedItem("On failure: red error message with details (invalid key, quota exceeded, etc.)."),
        numberedItem("User clicks Save Settings to persist key and selected model."),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 11. API REFERENCE ═══════════════
        heading1("11. Complete API Reference"),

        heading2("11.1 Page Routes"),
        featureTable([
          ["GET /", "Redirects to /dashboard"],
          ["GET /dashboard", "Renders main dashboard with today's filings overview"],
          ["GET /company/<symbol>", "Renders company filing view with sidebar watchlist"],
          ["GET /prices", "Renders stock prices page with market data"],
          ["GET /din-search", "Renders Director Intelligence Network search page"],
        ]),
        spacer(),

        heading2("11.2 Filing APIs"),
        featureTable([
          ["GET /api/filing/<id>", "Returns full filing details including text content, attachments, metadata"],
          ["GET /api/tags/<symbol>", "Returns available tags and counts for a company's filings"],
          ["POST /api/summarize/<id>", "Generates or retrieves cached AI summary. Query: force=true to regenerate"],
          ["GET /api/xbrl/<id>", "Parses and returns XBRL governance data from filing attachments"],
          ["GET /api/search/<symbol>", "Search filings by text query, tag, and date range. Query: q, tag, from, to"],
          ["GET /api/compare/<id>", "Lists comparable filings for the given filing's company"],
          ["GET /api/compare/<id_a>/<id_b>", "Returns side-by-side text comparison of two filings"],
          ["GET /api/filings_list/<symbol>", "Returns paginated filing list for a company"],
          ["POST /api/refresh/<symbol>", "Fetches latest filings from NSE API and updates database"],
          ["GET /api/symbol_search", "Autocomplete search for company symbols. Query: q=<search term>"],
        ]),
        spacer(),

        heading2("11.3 Watchlist APIs"),
        featureTable([
          ["GET /api/watchlist", "Returns all watchlisted companies with metadata"],
          ["POST /api/watchlist", "Add company to watchlist. Body: {symbol, name, notes?}"],
          ["DELETE /api/watchlist/<symbol>", "Remove company from watchlist"],
        ]),
        spacer(),

        heading2("11.4 Settings APIs"),
        featureTable([
          ["GET /api/settings", "Returns current settings (masked API key, selected model, available models)"],
          ["POST /api/settings", "Update settings. Body: {gemini_api_key?, gemini_model?}"],
          ["POST /api/settings/test-key", "Validate API key. Body: {api_key}. Returns {valid, message}"],
        ]),
        spacer(),

        heading2("11.5 Market Data APIs"),
        featureTable([
          ["GET /api/prices", "Returns stock prices for all available securities"],
          ["GET /api/price/<symbol>", "Returns detailed price data for a specific stock"],
          ["GET /api/pre-open", "Returns pre-open market data for NIFTY 50 constituents"],
          ["GET /api/sector", "Returns sector-wise index performance data"],
        ]),
        spacer(),

        heading2("11.6 Governance APIs"),
        featureTable([
          ["GET /api/governance/<symbol>", "Returns list of available governance reports for a company"],
          ["GET /api/governance/<symbol>/<rec_id>", "Returns parsed XBRL governance data for specific report"],
          ["GET /api/governance/compare/<symbol>/<id_a>/<id_b>", "Compares two governance reports showing changes"],
          ["GET /api/din-search", "Search directors by DIN. Query: din=<DIN number>"],
        ]),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 12. DATA MODEL ═══════════════
        heading1("12. Database Schema"),

        heading2("12.1 Tables"),

        heading3("12.1.1 filings"),
        featureTable([
          ["id", "INTEGER PRIMARY KEY AUTOINCREMENT"],
          ["symbol", "TEXT NOT NULL - Company trading symbol (e.g., RELIANCE)"],
          ["company_name", "TEXT - Full company name"],
          ["title", "TEXT NOT NULL - Filing announcement title"],
          ["description", "TEXT - Filing description/subtitle"],
          ["date", "TEXT NOT NULL - Filing date (YYYY-MM-DD)"],
          ["time", "TEXT - Filing time (HH:MM:SS)"],
          ["source", "TEXT - Exchange source (NSE/BSE)"],
          ["category", "TEXT - Filing category from exchange"],
          ["text_content", "TEXT - Full extracted text content of the document"],
          ["summary", "TEXT - AI-generated or extractive summary (cached)"],
          ["attachments", "TEXT - JSON array of attachment URLs"],
          ["xbrl_data", "TEXT - Cached parsed XBRL data (JSON)"],
        ]),
        spacer(),

        heading3("12.1.2 watchlist"),
        featureTable([
          ["id", "INTEGER PRIMARY KEY AUTOINCREMENT"],
          ["symbol", "TEXT UNIQUE NOT NULL - Company trading symbol"],
          ["name", "TEXT - Company display name"],
          ["notes", "TEXT - User notes about the company"],
          ["added_at", "TEXT - ISO timestamp when added"],
        ]),
        spacer(),

        heading3("12.1.3 settings"),
        featureTable([
          ["key", "TEXT PRIMARY KEY - Setting identifier"],
          ["value", "TEXT NOT NULL - Setting value"],
        ]),
        spacer(),

        heading2("12.2 Indexes"),
        bullet("filings: composite index on (symbol, date) for fast company + date queries."),
        bullet("filings: index on date for dashboard day-level aggregation."),
        bullet("watchlist: unique constraint on symbol prevents duplicate entries."),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 13. UI/UX SPECIFICATIONS ═══════════════
        heading1("13. User Interface Specifications"),

        heading2("13.1 Design System"),
        featureTable([
          ["Color Scheme", "Indigo (#4F46E5) primary, Slate grays for text, white backgrounds"],
          ["Typography", "System font stack via Tailwind CSS defaults, 14-16px body text"],
          ["Layout", "Fixed sidebar (280px) + scrollable main content area"],
          ["CSS Framework", "Tailwind CSS v3 via CDN with custom utility classes"],
          ["Icons", "Heroicons (outline set) via inline SVG"],
          ["Responsiveness", "Desktop-optimized with min-width: 1024px recommended"],
        ]),
        spacer(),

        heading2("13.2 Navigation Structure"),
        featureTable([
          ["Sidebar - Top", "Logo, company search input with autocomplete"],
          ["Sidebar - Navigation", "Dashboard, Prices, DIN Search links with active state indication"],
          ["Sidebar - Watchlist", "List of watchlisted companies with filing count badges, add/remove toggle"],
          ["Sidebar - Bottom", "AI Settings button with status dot (green = configured, gray = not configured)"],
          ["Main Content", "Route-dependent content area with breadcrumb context"],
        ]),
        spacer(),

        heading2("13.3 Key UI Components"),

        heading3("13.3.1 Filing Card"),
        bullet("Collapsed: title, date/time, source badge, category tags, expand chevron."),
        bullet("Expanded: full description, AI summary section, XBRL data (if available), document links, compare button."),
        bullet("Summary section: Summarize button, loading spinner during API call, AI model badge, regenerate button."),

        heading3("13.3.2 Settings Modal"),
        bullet("Overlay modal triggered from sidebar AI Settings button."),
        bullet("Model selection: card-based selector with radio behavior (one active at a time)."),
        bullet("API key input: password field with show/hide toggle, Test Key button, validation feedback."),
        bullet("Save button with success/error status message."),
        bullet("Light theme (bg-slate-50) matching overall site aesthetic."),

        heading3("13.3.3 Dashboard Cards"),
        bullet("Company card: symbol, company name, filing count badge, click to navigate."),
        bullet("Stat cards: total filings, active companies, top categories with visual counters."),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 14. NON-FUNCTIONAL REQUIREMENTS ═══════════════
        heading1("14. Non-Functional Requirements"),

        heading2("14.1 Performance"),
        reqTable3([
          ["NFR-PERF-001", "Dashboard page load time under 2 seconds for up to 500 filings", "P1"],
          ["NFR-PERF-002", "AI summarization response within 15 seconds (Gemini 2.5 Flash)", "P1"],
          ["NFR-PERF-003", "Search results returned within 500ms for text queries", "P1"],
          ["NFR-PERF-004", "XBRL parsing completed within 5 seconds per document", "P2"],
          ["NFR-PERF-005", "Database queries optimized with indexes for sub-100ms response", "P1"],
        ]),
        spacer(),

        heading2("14.2 Reliability"),
        reqTable3([
          ["NFR-REL-001", "Graceful degradation when NSE APIs are unavailable (show cached data)", "P1"],
          ["NFR-REL-002", "Fallback summarization when Gemini API fails or quota is exhausted", "P0"],
          ["NFR-REL-003", "SQLite WAL mode for concurrent read access without blocking", "P1"],
          ["NFR-REL-004", "Error messages displayed to user for all failed operations", "P1"],
        ]),
        spacer(),

        heading2("14.3 Security"),
        reqTable3([
          ["NFR-SEC-001", "API keys stored server-side, never exposed to frontend in plain text", "P0"],
          ["NFR-SEC-002", "API key display masked in settings UI (show last 4 characters only)", "P1"],
          ["NFR-SEC-003", "Input sanitization for all user-provided search queries", "P1"],
          ["NFR-SEC-004", "No authentication required (single-user local deployment)", "P1"],
        ]),
        spacer(),

        heading2("14.4 Usability"),
        reqTable3([
          ["NFR-USE-001", "All primary actions accessible within 3 clicks from dashboard", "P1"],
          ["NFR-USE-002", "Visual feedback for all async operations (loading spinners, status messages)", "P1"],
          ["NFR-USE-003", "Consistent color coding for tags across all views", "P2"],
          ["NFR-USE-004", "Keyboard-accessible modal dialogs (Escape to close)", "P2"],
        ]),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 15. ERROR HANDLING ═══════════════
        heading1("15. Error Handling Specifications"),

        featureTable([
          ["NSE API Timeout", "Display cached data with stale indicator. Retry button available."],
          ["NSE API Rate Limit", "Queue requests with exponential backoff. Show rate limit message to user."],
          ["Gemini API Key Invalid", "Test Key returns error. Red message in settings modal. Fallback summarizer active."],
          ["Gemini API Quota Exceeded", "Log warning, use fallback summarizer. Show info banner on summary."],
          ["XBRL Parse Failure", "Return empty governance data with error message. Filing still accessible."],
          ["Database Locked", "WAL mode prevents most locks. Retry logic with 3 attempts on write operations."],
          ["Empty Filing Content", "Display message: No content available. Summarize button disabled."],
          ["Network Offline", "Cached data shown. Refresh buttons show offline indicator."],
        ]),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 16. TESTING REQUIREMENTS ═══════════════
        heading1("16. Testing Requirements"),

        heading2("16.1 Unit Test Scenarios"),
        bullet("Tag detection: verify all 11 tag categories match expected keywords."),
        bullet("Fallback summarizer: verify noise filtering, sentence scoring, deduplication."),
        bullet("Similarity function: verify threshold behavior for near-duplicate detection."),
        bullet("Database functions: get_setting, set_setting upsert behavior."),
        bullet("Date/time parsing: verify format normalization for filing timestamps."),

        heading2("16.2 Integration Test Scenarios"),
        bullet("Filing fetch: mock NSE API, verify filings stored correctly in SQLite."),
        bullet("AI summarization: mock Gemini API, verify summary cached and returned."),
        bullet("XBRL parsing: test with real XBRL samples, verify director/committee extraction."),
        bullet("Watchlist CRUD: add, retrieve, delete companies, verify persistence."),
        bullet("Settings persistence: save API key, reload settings, verify masked display."),

        heading2("16.3 End-to-End Test Scenarios"),
        bullet("User navigates to dashboard, sees today's filings, clicks company, expands filing, generates summary."),
        bullet("User configures API key in settings, tests key, saves, returns to filing, generates AI summary."),
        bullet("User adds company to watchlist, verifies sidebar updates, removes company."),
        bullet("User searches filings by keyword, filters by tag, verifies results."),
        bullet("User views governance data, compares two periods, identifies board changes."),

        new Paragraph({ children: [new PageBreak()] }),

        // ═══════════════ 17. ASSUMPTIONS & CONSTRAINTS ═══════════════
        heading1("17. Assumptions and Constraints"),

        heading2("17.1 Assumptions"),
        bullet("NSE India public APIs remain accessible without authentication for filing data."),
        bullet("Google Gemini API maintains backwards compatibility for the models used."),
        bullet("Users have internet connectivity to access NSE APIs and Gemini API."),
        bullet("Single-user deployment; no concurrent user conflict resolution needed."),
        bullet("Filing text content is extractable from PDF attachments hosted on NSE."),

        heading2("17.2 Constraints"),
        bullet("NSE API rate limits may restrict the frequency of data refresh operations."),
        bullet("Gemini API free tier (2.5 Flash) has daily request quota limitations."),
        bullet("SQLite is suitable for single-user deployment but not horizontal scaling."),
        bullet("XBRL taxonomy changes by SEBI may require parser updates."),
        bullet("Pre-open data is only available during the 9:00-9:15 AM IST window."),

        heading2("17.3 Dependencies"),
        featureTable([
          ["NSE India APIs", "Primary data source for filings, prices, governance reports. No SLA guarantee."],
          ["Google Gemini API", "AI summarization engine. Requires user-provided API key."],
          ["Python 3.8+", "Runtime environment with Flask, lxml, requests, sqlite3 standard library."],
          ["google-genai SDK", "v1.73.0+ for Gemini API integration (NOT deprecated google-generativeai)."],
          ["Modern Browser", "Chrome/Edge/Firefox/Safari with ES6+ JavaScript support."],
        ]),

        spacer(), spacer(),

        // ═══════════════ END MARKER ═══════════════
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 400 },
          border: { top: { style: BorderStyle.SINGLE, size: 4, color: INDIGO, space: 20 } },
          children: [],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER, spacing: { after: 80 },
          children: [new TextRun({ text: "End of Functional Requirements Document", size: 24, bold: true, font: "Arial", color: INDIGO })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Delta Filings v1.0  |  April 2026  |  Confidential", size: 20, font: "Arial", color: GRAY })],
        }),
      ],
    },
  ],
});

// ── Write file ──
const OUTPUT = "/Users/prakharsingh/Documents/Study Files/python things/delta_filings/Delta_Filings_FRD.docx";
Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(OUTPUT, buf);
  console.log("FRD created:", OUTPUT);
});
