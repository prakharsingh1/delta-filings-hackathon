/**
 * Delta Filings — One-page Executive Summary.
 * Output: Delta_Filings_ExecSummary_v2.docx
 */
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, Header, Footer,
  AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType, ShadingType,
  VerticalAlign,
} = require("/opt/homebrew/lib/node_modules/docx");

const BRAND = "1F4E79";
const GREY_BORDER = "BFBFBF";
const TEXT_MUTED = "595959";

const border = { style: BorderStyle.SINGLE, size: 4, color: GREY_BORDER };
const cellBorders = { top: border, bottom: border, left: border, right: border };

const h1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 160, after: 80 }, children: [new TextRun({ text: t })] });
const h2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 120, after: 60 }, children: [new TextRun({ text: t })] });
const p = (t) => new Paragraph({ spacing: { after: 80, line: 280 }, children: [new TextRun({ text: t })] });
const bullet = (t) => new Paragraph({ numbering: { reference: "bullets", level: 0 },
  spacing: { after: 40, line: 260 }, children: [new TextRun(t)] });

const cell = (c, opts = {}) => new TableCell({
  borders: cellBorders,
  width: { size: opts.width || 0, type: WidthType.DXA },
  shading: opts.fill ? { fill: opts.fill, type: ShadingType.CLEAR, color: "auto" } : undefined,
  verticalAlign: VerticalAlign.CENTER,
  margins: { top: 60, bottom: 60, left: 120, right: 120 },
  children: [new Paragraph({ spacing: { after: 20, line: 260 },
    children: [new TextRun({ text: String(c ?? ""), bold: opts.bold, color: opts.color, size: opts.size })] })],
});

const kpiTable = new Table({
  width: { size: 9360, type: WidthType.DXA },
  columnWidths: [3120, 3120, 3120],
  rows: [
    new TableRow({ tableHeader: true, children: [
      cell("Monthly Active Users", { bold: true, color: "FFFFFF", fill: BRAND, width: 3120 }),
      cell("Filings Coverage", { bold: true, color: "FFFFFF", fill: BRAND, width: 3120 }),
      cell("DIN Lookup p95", { bold: true, color: "FFFFFF", fill: BRAND, width: 3120 }),
    ]}),
    new TableRow({ children: [
      cell("5,000", { bold: true, size: 32, color: BRAND, width: 3120 }),
      cell("\u2265 98%", { bold: true, size: 32, color: BRAND, width: 3120 }),
      cell("< 800 ms", { bold: true, size: 32, color: BRAND, width: 3120 }),
    ]}),
  ],
});

const body = [
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 },
    children: [new TextRun({ text: "\u0394  DELTA FILINGS", bold: true, size: 36, color: BRAND })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
    children: [new TextRun({ text: "Corporate Governance Intelligence Platform  \u2014  Executive Summary (v2)", size: 20, italics: true, color: TEXT_MUTED })] }),

  h2("The opportunity"),
  p("Indian listed companies produce thousands of statutory disclosures every week across BSE, NSE and MCA. Investors, compliance officers and analysts spend the majority of their time collecting this data rather than acting on it. Delta Filings collapses that workflow into a single surface."),

  h2("What v2 delivers"),
  bullet("Real-time filings from BSE + NSE with AI-generated summaries and materiality tags."),
  bullet("Director records by DIN, with side-by-side Compare for relationship analysis."),
  bullet("Compliance module \u2014 obligations, runs, evidence, audit-ready PDF export."),
  bullet("Watchlists and alerts \u2014 instant, daily or weekly digest."),
  bullet("Containerised stack (FastAPI + Next.js + Postgres + Redis) ready for paid tiers."),

  h2("Launch targets (M6, Oct 2026)"),
  kpiTable,

  h2("Why now"),
  p("v1 shipped a prototype in 2025 that validated demand. v2 hardens the stack, adds the two most-requested modules (Compare + Compliance), and prepares for billing in Dec 2026. The architecture is horizontally scalable and ready for enterprise customers."),

  h2("Ask"),
  bullet("Engineering sign-off on the v2 scope below."),
  bullet("Budget commitment for managed Postgres, Redis and email infrastructure through Q4 2026."),
  bullet("Two pilot customers committed to a 4-week UAT ahead of M6."),
];

const doc = new Document({
  creator: "Delta Filings Product Team",
  title: "Delta Filings \u2014 Executive Summary (v2)",
  description: "One-page executive summary for the Delta Filings v2 launch.",
  styles: {
    default: { document: { run: { font: "Calibri", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: "Calibri", color: BRAND },
        paragraph: { spacing: { before: 160, after: 80 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Calibri", color: BRAND },
        paragraph: { spacing: { before: 120, after: 60 }, outlineLevel: 1 } },
    ],
  },
  numbering: { config: [ { reference: "bullets",
    levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 540, hanging: 300 } } } }] } ] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 },
      margin: { top: 1080, right: 1440, bottom: 1080, left: 1440 } } },
    children: body,
  }],
});

const OUT = path.join("/Users/prakharsingh/Documents/Study Files/python things/delta_filings", "Delta_Filings_ExecSummary_v2.docx");
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(OUT, buf); console.log(`Wrote ${OUT} (${buf.length} bytes)`); });
