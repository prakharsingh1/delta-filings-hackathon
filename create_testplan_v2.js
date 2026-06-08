/**
 * Delta Filings — Test Plan.
 * Output: Delta_Filings_TestPlan_v2.docx
 */

const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, Header, Footer,
  AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType, ShadingType,
  PageNumber, PageBreak, TabStopType, TabStopPosition, TableOfContents, VerticalAlign,
} = require("/opt/homebrew/lib/node_modules/docx");

const BRAND = "1F4E79";
const GREY_BORDER = "BFBFBF";
const GREY_SOFT = "F2F2F2";
const TEXT_MUTED = "595959";

const border = { style: BorderStyle.SINGLE, size: 4, color: GREY_BORDER };
const cellBorders = { top: border, bottom: border, left: border, right: border };

const p = (t) => new Paragraph({ spacing: { after: 120, line: 300 }, children: [new TextRun({ text: t })] });
const h1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 180 }, children: [new TextRun({ text: t })] });
const h2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 280, after: 140 }, children: [new TextRun({ text: t })] });
const bullet = (t) => new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80, line: 280 }, children: [new TextRun(t)] });

const cell = (content, opts = {}) => new TableCell({
  borders: cellBorders,
  width: { size: opts.width || 0, type: WidthType.DXA },
  shading: opts.fill ? { fill: opts.fill, type: ShadingType.CLEAR, color: "auto" } : undefined,
  verticalAlign: VerticalAlign.CENTER,
  margins: { top: 80, bottom: 80, left: 140, right: 140 },
  children: Array.isArray(content) ? content : [new Paragraph({ spacing: { after: 40, line: 280 },
    children: [new TextRun({ text: String(content ?? ""), bold: opts.bold, color: opts.color })] })],
});

const headerRow = (labels, widths) => new TableRow({
  tableHeader: true,
  children: labels.map((l, i) => cell(l, { bold: true, color: "FFFFFF", fill: BRAND, width: widths[i] })),
});
const dataRow = (vals, widths, alt = false) => new TableRow({
  children: vals.map((v, i) => cell(v, { width: widths[i], fill: alt ? GREY_SOFT : undefined })),
});
const table = (headers, rows, widths) => {
  const body = [headerRow(headers, widths)];
  rows.forEach((r, i) => body.push(dataRow(r, widths, i % 2 === 1)));
  return new Table({ width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA }, columnWidths: widths, rows: body });
};

const cover = [
  new Paragraph({ spacing: { before: 2400, after: 200 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "\u0394  DELTA FILINGS", bold: true, size: 64, color: BRAND })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
    children: [new TextRun({ text: "Corporate Governance Intelligence Platform", size: 30, color: TEXT_MUTED, italics: true })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 2400, after: 120 },
    children: [new TextRun({ text: "Test Plan", bold: true, size: 44 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 2400 },
    children: [new TextRun({ text: "Version 2.0  \u2014  April 2026", size: 24, color: TEXT_MUTED })] }),
  new Paragraph({ children: [new PageBreak()] }),
];

const body = [
  h1("1.  Purpose"),
  p("This document describes the testing strategy for Delta Filings v2. It defines what will be tested, how, by whom, and the exit criteria for release."),

  h1("2.  Scope"),
  h2("2.1  In Scope"),
  bullet("Dashboard, Announcements, DIN Search, Compare, Compliance, Watchlists, Accounts."),
  bullet("Ingestion pipeline (BSE, NSE, MCA)."),
  bullet("Summary and alert workers."),
  bullet("API endpoints, authN/authZ, and rate limiting."),
  bullet("Accessibility (WCAG 2.1 AA) for all user-facing surfaces."),

  h2("2.2  Out of Scope"),
  bullet("Load testing beyond 2x launch concurrency."),
  bullet("Penetration testing by external vendor (tracked separately)."),
  bullet("Localisation / i18n (not planned for v2)."),

  h1("3.  Test Levels"),
  table(
    ["Level", "Owner", "Coverage Target"],
    [
      ["Unit", "Engineering", "\u2265 80% line coverage on backend; \u2265 70% on frontend."],
      ["Integration", "Engineering", "Every API endpoint + each ingestion worker."],
      ["End-to-end", "QA", "All 'Must' user journeys from BRD."],
      ["Performance", "QA + Engineering", "All performance targets from TAD \u00a711."],
      ["Security", "Engineering", "OWASP Top 10 checklist + SCA."],
      ["Accessibility", "QA", "Pa11y + axe + manual keyboard pass."],
      ["Exploratory", "Product + QA", "Each release candidate, time-boxed 2 days."],
    ],
    [2200, 3200, 3960],
  ),

  h1("4.  Test Environments"),
  bullet("Local: Docker Compose with seeded data."),
  bullet("Staging: full production topology, synthetic data only."),
  bullet("Pre-prod dry-run: 24h with live ingestion but no public users."),
  bullet("Production: canary (5%) \u2192 full rollout with auto-rollback on error budget breach."),

  h1("5.  Entry and Exit Criteria"),
  h2("5.1  Entry"),
  bullet("All Must-priority FRs have acceptance criteria."),
  bullet("Test data seeded in staging."),
  bullet("Build is green on main with no merged skipped tests."),

  h2("5.2  Exit"),
  bullet("Zero Severity-1 open defects."),
  bullet("\u2264 3 Severity-2 open defects, all with mitigations."),
  bullet("All performance targets met in staging load test at 2x."),
  bullet("Accessibility: no Level A / AA blocker on any Must-priority surface."),

  h1("6.  Traceability (Sample)"),
  table(
    ["Requirement", "Test Case ID", "Type"],
    [
      ["FR-D-01", "TC-D-01-01, TC-D-01-02", "E2E + Integration"],
      ["FR-A-01", "TC-A-01-01..04", "Integration + Performance"],
      ["FR-N-01", "TC-N-01-01..03", "Unit + Integration + E2E"],
      ["FR-C-01", "TC-C-01-01..02", "E2E + Accessibility"],
      ["FR-M-02", "TC-M-02-01..05", "Integration + Security"],
      ["FR-W-02", "TC-W-02-01..03", "Integration + Performance"],
      ["FR-U-01", "TC-U-01-01..03", "E2E + Security"],
    ],
    [2400, 3600, 3360],
  ),

  h1("7.  Sample Test Cases"),

  h2("7.1  TC-N-01-01 \u2014 Valid DIN returns director"),
  table(
    ["Step", "Action", "Expected"],
    [
      ["1", "Navigate to /din.", "Search page loads."],
      ["2", "Enter DIN 00123456 (known curated).", "Input is accepted."],
      ["3", "Submit.", "Director profile renders in < 800 ms with 'curated snapshot' badge."],
      ["4", "Inspect response headers.", "Cache-Control is present with max-age > 0."],
    ],
    [700, 4000, 4660],
  ),

  h2("7.2  TC-M-02-01 \u2014 Compliance run evidence immutability"),
  table(
    ["Step", "Action", "Expected"],
    [
      ["1", "Create a compliance run for a Must obligation.", "Run saved as 'draft'."],
      ["2", "Upload two PDF evidence files.", "Both files listed with SHA-256 hashes."],
      ["3", "Mark run as 'complete'.", "Status moves to 'complete'; UI shows immutable badge."],
      ["4", "Attempt to remove an evidence file via API.", "HTTP 409 returned with 'run is immutable'."],
    ],
    [700, 4400, 4260],
  ),

  h1("8.  Defect Management"),
  table(
    ["Severity", "Definition", "Target Resolution"],
    [
      ["S1", "Production down or data loss; no workaround.", "2 hours."],
      ["S2", "Major feature broken; workaround exists.", "1 business day."],
      ["S3", "Minor feature broken or cosmetic.", "Next release."],
      ["S4", "Cosmetic / minor improvement.", "Backlog."],
    ],
    [1500, 5500, 2360],
  ),

  h1("9.  Tooling"),
  bullet("Pytest + httpx for backend unit and integration tests."),
  bullet("Vitest + React Testing Library for frontend unit tests."),
  bullet("Playwright for cross-browser E2E."),
  bullet("k6 for load testing (authenticated and anonymous)."),
  bullet("axe-core and Pa11y for accessibility."),
  bullet("OWASP ZAP in a weekly DAST scan on staging."),

  h1("10.  Schedule"),
  table(
    ["Phase", "Window", "Exit Gate"],
    [
      ["Unit + Integration (continuous)", "Every PR", "CI green."],
      ["E2E regression", "Nightly on main", "No Sev-1 or Sev-2 failures."],
      ["Load test", "Pre-milestone", "Performance targets met at 2x."],
      ["Security scan", "Weekly (staging)", "No High-severity open findings."],
      ["UAT with pilot users", "M6-2 weeks", "\u2265 3 users sign off."],
    ],
    [3400, 2800, 3160],
  ),

  h1("11.  Risks"),
  bullet("Upstream data variability may cause flaky ingestion tests \u2014 use recorded fixtures in CI."),
  bullet("OpenAI latency variance may make E2E flaky \u2014 stub out in CI, run live against staging nightly."),
  bullet("Pilot users may delay sign-off \u2014 keep two backup users on standby."),
];

const doc = new Document({
  creator: "Delta Filings QA",
  title: "Delta Filings \u2014 Test Plan",
  description: "Test plan for the Delta Filings v2 platform.",
  styles: {
    default: { document: { run: { font: "Calibri", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 34, bold: true, font: "Calibri", color: BRAND },
        paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Calibri", color: BRAND },
        paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 1 } },
    ],
  },
  numbering: { config: [ { reference: "bullets",
    levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] } ] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 },
      margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    headers: { default: new Header({ children: [new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: BRAND, space: 4 } },
      children: [
        new TextRun({ text: "Delta Filings  \u2014  Test Plan", color: BRAND, bold: true, size: 18 }),
        new TextRun({ text: "\t" }),
        new TextRun({ text: "v2.0  |  April 2026", color: TEXT_MUTED, size: 18 }),
      ],
    })]})},
    footers: { default: new Footer({ children: [new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      children: [
        new TextRun({ text: "Confidential \u2014 QA", italics: true, size: 18, color: TEXT_MUTED }),
        new TextRun({ text: "\t" }),
        new TextRun({ text: "Page ", size: 18, color: TEXT_MUTED }),
        new TextRun({ children: [PageNumber.CURRENT], size: 18, color: TEXT_MUTED }),
        new TextRun({ text: " of ", size: 18, color: TEXT_MUTED }),
        new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 18, color: TEXT_MUTED }),
      ],
    })]})},
    children: [
      ...cover,
      h1("Table of Contents"),
      new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
      new Paragraph({ children: [new PageBreak()] }),
      ...body,
    ],
  }],
});

const OUT = path.join("/Users/prakharsingh/Documents/Study Files/python things/delta_filings", "Delta_Filings_TestPlan_v2.docx");
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(OUT, buf); console.log(`Wrote ${OUT} (${buf.length} bytes)`); });
