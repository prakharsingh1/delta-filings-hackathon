/**
 * Delta Filings — Functional Requirements Document (FRD) generator (v2).
 * Produces Delta_Filings_FRD_v2.docx — a detailed, per-feature functional spec
 * that pairs with the BRD.
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

const BRAND = "1F4E79";
const GREY_BORDER = "BFBFBF";
const GREY_SOFT = "F2F2F2";
const TEXT_MUTED = "595959";

const border = { style: BorderStyle.SINGLE, size: 4, color: GREY_BORDER };
const cellBorders = { top: border, bottom: border, left: border, right: border };

const p = (text) =>
  new Paragraph({
    spacing: { after: 120, line: 300 },
    children: [new TextRun({ text })],
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

// Standard "feature block" builder
const feature = ({ id, title, purpose, actors, pre, steps, post, alt, acceptance, priority }) => {
  const out = [
    h2(`${id}  \u2014  ${title}`),
    table(
      ["Attribute", "Value"],
      [
        ["ID", id],
        ["Priority", priority],
        ["Primary Actor(s)", actors],
        ["Purpose", purpose],
        ["Preconditions", pre],
        ["Postconditions", post],
      ],
      [2200, 7160],
    ),
    new Paragraph({ spacing: { after: 120 }, children: [new TextRun("")] }),
    h3("Main Flow"),
    ...steps.map((s, i) =>
      new Paragraph({
        numbering: { reference: "numbers", level: 0 },
        spacing: { after: 60, line: 280 },
        children: [new TextRun(s)],
      }),
    ),
  ];
  if (alt && alt.length) {
    out.push(h3("Alternate / Error Flows"));
    alt.forEach((a) => out.push(bullet(a)));
  }
  out.push(h3("Acceptance Criteria"));
  acceptance.forEach((a) => out.push(bullet(a)));
  return out;
};

// ---------- Cover ----------
const cover = [
  new Paragraph({
    spacing: { before: 2400, after: 200 },
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "\u0394  DELTA FILINGS", bold: true, size: 64, color: BRAND })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({ text: "Corporate Governance Intelligence Platform", size: 30, color: TEXT_MUTED, italics: true })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 2400, after: 120 },
    children: [new TextRun({ text: "Functional Requirements Document", bold: true, size: 44 })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 2400 },
    children: [new TextRun({ text: "Version 2.0  \u2014  April 2026", size: 24, color: TEXT_MUTED })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1200 },
    children: [new TextRun({ text: "Companion to: Delta Filings Business Requirements Document", size: 22, color: TEXT_MUTED })],
  }),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- Intro ----------
const intro = [
  h1("1.  Introduction"),

  h2("1.1  Purpose"),
  p("This Functional Requirements Document (FRD) decomposes the business requirements from the Delta Filings BRD into detailed functional specifications. Each feature block captures the purpose, actors, flow, alternate flows and acceptance criteria necessary for implementation and verification."),

  h2("1.2  Audience"),
  bullet("Engineering \u2014 implementation reference."),
  bullet("QA \u2014 test-case derivation."),
  bullet("Product \u2014 scope and acceptance alignment."),
  bullet("Design \u2014 flow-by-flow UX contract."),

  h2("1.3  Conventions"),
  bullet("IDs follow the pattern FR-<module>-<sequence>."),
  bullet("Priority uses MoSCoW (Must / Should / Could)."),
  bullet("'System' refers to the Delta Filings platform as a whole."),
  bullet("All timestamps are in IST unless stated otherwise."),

  h2("1.4  Traceability to BRD"),
  table(
    ["FRD Module", "BRD Requirements"],
    [
      ["Dashboard (FR-D)", "BR-001 \u2014 BR-005 (Filings)"],
      ["Announcements (FR-A)", "BR-001 \u2014 BR-005"],
      ["DIN Search (FR-N)", "BR-010 \u2014 BR-014"],
      ["Compare (FR-C)", "BR-012, BR-014"],
      ["Compliance (FR-M)", "BR-020 \u2014 BR-024"],
      ["Watchlist (FR-W)", "BR-030 \u2014 BR-033"],
      ["Accounts (FR-U)", "BR-040 \u2014 BR-043"],
    ],
    [3000, 6360],
  ),

  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- Features ----------
const DASH = [
  h1("2.  Dashboard Module (FR-D)"),
  ...feature({
    id: "FR-D-01",
    title: "Display latest filings across user's watchlists",
    priority: "Must",
    actors: "Authenticated user",
    purpose: "Give the user an at-a-glance view of the most recent disclosures for companies they track.",
    pre: "User is authenticated; at least one watchlist exists.",
    post: "User sees up to 50 filings, newest first.",
    steps: [
      "User navigates to /dashboard.",
      "System loads watchlists and associated companies for the user.",
      "System queries the filings service for the latest 50 filings across those companies.",
      "System renders a card list with company, category, timestamp and AI summary.",
      "User can click a card to open the filing detail page.",
    ],
    alt: [
      "No watchlists yet \u2014 system shows an empty-state CTA to create a watchlist.",
      "Filings service unavailable \u2014 system shows a cached snapshot with a 'stale data' badge.",
    ],
    acceptance: [
      "Dashboard renders within 2.5s on a 4G connection (p95).",
      "Dashboard respects watchlist membership changes within 60s.",
      "No filings older than the most recent 50 are shown.",
    ],
  }),
  ...feature({
    id: "FR-D-02",
    title: "Show AI-generated summary with sentiment tag",
    priority: "Must",
    actors: "Authenticated user",
    purpose: "Reduce time-to-insight by pre-summarising each filing.",
    pre: "A filing has been ingested and summary pipeline has completed.",
    post: "User sees 1\u20133 sentence summary with a Positive/Neutral/Negative badge.",
    steps: [
      "System reads the cached summary from the filings row.",
      "System renders the summary text with a sentiment badge.",
      "System displays a 'machine-generated' label for transparency.",
    ],
    alt: [
      "Summary not yet generated \u2014 show 'Summary pending' placeholder and enqueue a summary job.",
      "Summary generation failed \u2014 show raw first 200 chars of filing body with 'AI unavailable' note.",
    ],
    acceptance: [
      "All summaries carry a visible 'machine-generated' label.",
      "Sentiment badge uses an accessible colour contrast (\u2265 4.5:1).",
    ],
  }),
];

const ANN = [
  h1("3.  Announcements Module (FR-A)"),
  ...feature({
    id: "FR-A-01",
    title: "Ingest BSE and NSE filings every 60 seconds",
    priority: "Must",
    actors: "Ingestion worker (system actor)",
    purpose: "Keep the filings corpus within 15 minutes of source publication.",
    pre: "Worker has network access to BSE and NSE public feeds; Redis available.",
    post: "New filings persisted to Postgres with de-duplication; summary jobs enqueued.",
    steps: [
      "Scheduler triggers the scraper every 60 seconds during market hours.",
      "Scraper fetches the latest N announcements per exchange.",
      "Scraper normalises the payload into the canonical Filing schema.",
      "System checks Redis for a dedup key (sha256 of source URL + timestamp).",
      "If not seen, system inserts into Postgres and enqueues a summarisation job.",
    ],
    alt: [
      "Source returns 5xx \u2014 back off with exponential delay up to 5 minutes.",
      "Source layout changes \u2014 raise parse-error alert and continue with last known structure.",
    ],
    acceptance: [
      "Lag between source publication and Delta ingestion is < 15 minutes at p95.",
      "Zero duplicate filings persisted over any 24-hour sample window.",
    ],
  }),
  ...feature({
    id: "FR-A-02",
    title: "Filing detail page",
    priority: "Must",
    actors: "Any user",
    purpose: "Give users a canonical view of a single filing.",
    pre: "Filing row exists in Postgres.",
    post: "User sees structured filing with source link and related filings.",
    steps: [
      "User opens /announcements/{filingId}.",
      "System fetches filing, summary and related filings (same company, last 5).",
      "Page renders metadata, summary, embedded PDF viewer and related list.",
    ],
    alt: [
      "Filing not found \u2014 show 404 with a 'back to announcements' CTA.",
      "PDF unavailable \u2014 show link to original source with explanatory note.",
    ],
    acceptance: [
      "Filing detail page fully loads within 3s at p95.",
      "Source URL always matches original BSE/NSE publication URL.",
    ],
  }),
];

const DIN = [
  h1("4.  DIN Search Module (FR-N)"),
  ...feature({
    id: "FR-N-01",
    title: "Look up director by 8-digit DIN",
    priority: "Must",
    actors: "Any user",
    purpose: "Return structured director details quickly and reliably.",
    pre: "User has an 8-digit DIN.",
    post: "User sees the director's name, DOB (year only), and company associations.",
    steps: [
      "User types DIN into /din search box.",
      "System validates DIN is exactly 8 digits.",
      "System checks Redis cache for a fresh (\u2264 24h) response.",
      "If cache miss, system calls MCA scraper or returns curated snapshot.",
      "System renders director profile with association list.",
    ],
    alt: [
      "Invalid DIN \u2014 inline field-level error, no request sent.",
      "MCA unavailable \u2014 fall back to curated snapshot with 'curated snapshot' badge.",
    ],
    acceptance: [
      "DIN lookup p95 latency < 800ms on cache hits.",
      "Curated-snapshot badge visible whenever data did not come from live MCA.",
    ],
  }),
  ...feature({
    id: "FR-N-02",
    title: "Redis caching for DIN responses",
    priority: "Must",
    actors: "System",
    purpose: "Absorb load and protect the MCA source from rate limits.",
    pre: "Redis reachable.",
    post: "Director payload cached for 24h keyed by DIN.",
    steps: [
      "System writes successful MCA payload to Redis with TTL of 24h.",
      "Subsequent lookups within TTL return from cache.",
      "Cache invalidation runs daily at 02:00 IST for curated snapshots.",
    ],
    alt: [
      "Redis unreachable \u2014 system logs a warning and serves direct MCA/curated data.",
    ],
    acceptance: [
      "Cache hit ratio \u2265 85% in production over a 30-day sample.",
    ],
  }),
];

const CMP = [
  h1("5.  Compare Module (FR-C)"),
  ...feature({
    id: "FR-C-01",
    title: "Compare up to three directors",
    priority: "Must",
    actors: "Authenticated user",
    purpose: "Expose relationships between directors at a glance.",
    pre: "User is authenticated.",
    post: "User sees a side-by-side grid of up to three directors.",
    steps: [
      "User navigates to /compare.",
      "User adds directors by DIN or name search.",
      "System renders a three-column grid with name, DOB (year), associations, flags.",
      "System highlights common directorships across columns.",
    ],
    alt: [
      "User tries to add a fourth \u2014 system disables the 'add' control with tooltip.",
      "Director not found \u2014 inline error on the add control.",
    ],
    acceptance: [
      "Common directorships visually distinguished (badge + colour).",
      "Grid remains readable at 1280px viewport width.",
    ],
  }),
  ...feature({
    id: "FR-C-02",
    title: "Shareable read-only comparison link",
    priority: "Should",
    actors: "Authenticated user",
    purpose: "Let users share a compare view with collaborators without login.",
    pre: "User has created a comparison.",
    post: "Public read-only URL is generated.",
    steps: [
      "User clicks 'Share'.",
      "System generates a UUIDv4 token stored server-side with a 30-day expiry.",
      "System returns a URL of the form /share/compare/<token>.",
    ],
    alt: [
      "User revokes the link \u2014 token deleted and URL returns 404.",
    ],
    acceptance: [
      "Shared URL is read-only; no edit affordances rendered.",
      "Token expiry enforced server-side, not just in the UI.",
    ],
  }),
];

const COMP = [
  h1("6.  Compliance Module (FR-M)"),
  ...feature({
    id: "FR-M-01",
    title: "Show obligations relevant to a company",
    priority: "Must",
    actors: "Compliance officer (authenticated user)",
    purpose: "Surface the applicable regulatory obligations for a given company and its sector.",
    pre: "User is authenticated and has selected a company.",
    post: "User sees a filtered obligations list with due dates.",
    steps: [
      "User navigates to /compliance?companyId=...",
      "System fetches obligations where applicability matches the company's sector and listing.",
      "System renders the list grouped by statute (LODR, Companies Act, SEBI circulars).",
    ],
    alt: [
      "Unknown sector \u2014 system shows all obligations marked 'review applicability manually'.",
    ],
    acceptance: [
      "Obligation list load < 1s at p95.",
      "Each obligation links to its source statute / circular.",
    ],
  }),
  ...feature({
    id: "FR-M-02",
    title: "Create a compliance run with evidence capture",
    priority: "Must",
    actors: "Compliance officer",
    purpose: "Record the execution of a compliance obligation for audit.",
    pre: "Obligation selected; user has 'compliance-write' permission.",
    post: "Run row persisted with evidence attachments and timestamp.",
    steps: [
      "User clicks 'Create run' on an obligation.",
      "User fills due date, assignee and notes.",
      "User uploads one or more evidence files (PDF, PNG).",
      "System stores files in object storage and persists the run.",
      "System generates an audit log entry.",
    ],
    alt: [
      "Upload fails \u2014 show per-file error; run remains draft until resolved.",
      "File > 25 MB \u2014 rejected with inline error.",
    ],
    acceptance: [
      "Run is immutable once marked 'complete' (evidence hash recorded).",
      "Audit log entries never lose their chain when a run is updated.",
    ],
  }),
  ...feature({
    id: "FR-M-03",
    title: "Export compliance run as PDF",
    priority: "Should",
    actors: "Compliance officer, external auditor",
    purpose: "Produce an audit-ready artefact.",
    pre: "Run exists with at least one piece of evidence.",
    post: "A PDF is generated and delivered to the user.",
    steps: [
      "User clicks 'Export PDF' on a run.",
      "System renders a server-side PDF with metadata, evidence list, and hashes.",
      "System returns a signed download URL (expires in 10 minutes).",
    ],
    alt: [
      "PDF generation fails \u2014 show retry CTA; log to observability stack.",
    ],
    acceptance: [
      "PDF includes SHA-256 hash of each evidence file.",
      "PDF export latency < 10s at p95 for runs with up to 20 evidence files.",
    ],
  }),
];

const WATCH = [
  h1("7.  Watchlist and Alerts Module (FR-W)"),
  ...feature({
    id: "FR-W-01",
    title: "Create and manage watchlists",
    priority: "Must",
    actors: "Authenticated user",
    purpose: "Let users group companies they care about.",
    pre: "User is authenticated.",
    post: "Watchlist persisted under the user's account.",
    steps: [
      "User opens /watchlists.",
      "User clicks 'New watchlist' and provides a name.",
      "User adds companies by ticker or name search.",
    ],
    alt: [
      "Duplicate name \u2014 system shows inline validation error.",
    ],
    acceptance: [
      "User can create up to 20 watchlists on the Free tier.",
      "Watchlist membership changes reflect on the dashboard within 60s.",
    ],
  }),
  ...feature({
    id: "FR-W-02",
    title: "Email alerts on new filings",
    priority: "Must",
    actors: "System",
    purpose: "Notify the user of material filings in their watchlists.",
    pre: "User has verified email; watchlist has \u2265 1 company.",
    post: "Alert email dispatched.",
    steps: [
      "Background job polls for new filings within user's watchlists.",
      "System applies user's cadence preference (instant / daily / weekly).",
      "System dispatches an email via transactional provider with unsubscribe link.",
    ],
    alt: [
      "Email bounces \u2014 system marks email 'bounced', pauses future alerts until user action.",
    ],
    acceptance: [
      "Instant alerts delivered within 2 minutes of ingestion at p95.",
      "Every alert includes a working one-click unsubscribe.",
    ],
  }),
];

const USR = [
  h1("8.  Account Management (FR-U)"),
  ...feature({
    id: "FR-U-01",
    title: "Register and sign in via Supabase",
    priority: "Must",
    actors: "Visitor",
    purpose: "Authenticate users securely without building credential storage.",
    pre: "Visitor has a valid email or Google account.",
    post: "Supabase issues a JWT; session cookie set on the Next.js domain.",
    steps: [
      "Visitor clicks 'Sign in' and chooses email or Google.",
      "Supabase handles the flow and returns a signed JWT.",
      "Next.js middleware validates the JWT and sets a secure httpOnly cookie.",
    ],
    alt: [
      "OAuth cancelled \u2014 return to /login with a neutral state.",
      "Supabase unavailable \u2014 show status-page link and graceful error.",
    ],
    acceptance: [
      "All sessions use httpOnly, Secure, SameSite=Lax cookies.",
      "Sign-in-to-dashboard round-trip < 2s at p95.",
    ],
  }),
  ...feature({
    id: "FR-U-02",
    title: "Delete account within 30 days",
    priority: "Must",
    actors: "Authenticated user",
    purpose: "Honour the user's right to deletion.",
    pre: "User is authenticated.",
    post: "User data purged from Postgres, Redis and object storage.",
    steps: [
      "User opens /account/delete.",
      "User confirms by typing their email.",
      "System enqueues a delete job with a 7-day grace period.",
      "Job removes rows and blobs; audit log entry retained.",
    ],
    alt: [
      "User cancels within grace period \u2014 job cancelled; no data removed.",
    ],
    acceptance: [
      "All PII fields hard-deleted within 30 days of initial request.",
      "Audit log entry remains and carries the pseudonymous hash only.",
    ],
  }),
];

// ---------- NFR cross-reference ----------
const nfr = [
  h1("9.  Non-Functional Cross-Reference"),
  table(
    ["Non-Functional Area", "Applicable FRs"],
    [
      ["Performance", "FR-D-01, FR-A-02, FR-N-01, FR-M-03, FR-U-01"],
      ["Availability", "FR-A-01, FR-W-02"],
      ["Security", "FR-U-01, FR-U-02, FR-M-02"],
      ["Accessibility", "All user-facing FR-D / FR-A / FR-N / FR-C / FR-M / FR-W"],
      ["Observability", "FR-A-01, FR-W-02, FR-M-03"],
    ],
    [3400, 5960],
  ),
];

const doc = new Document({
  creator: "Delta Filings Product Team",
  title: "Delta Filings \u2014 Functional Requirements Document",
  description: "FRD for the Delta Filings platform (v2).",
  styles: {
    default: { document: { run: { font: "Calibri", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 34, bold: true, font: "Calibri", color: BRAND },
        paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Calibri", color: BRAND },
        paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 23, bold: true, font: "Calibri", color: "333333" },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        ] },
      { reference: "numbers",
        levels: [
          { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        ] },
    ],
  },
  sections: [
    {
      properties: {
        page: { size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } },
      },
      headers: {
        default: new Header({ children: [ new Paragraph({
          tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: BRAND, space: 4 } },
          children: [
            new TextRun({ text: "Delta Filings  \u2014  Functional Requirements Document", color: BRAND, bold: true, size: 18 }),
            new TextRun({ text: "\t" }),
            new TextRun({ text: "v2.0  |  April 2026", color: TEXT_MUTED, size: 18 }),
          ],
        })]}),
      },
      footers: {
        default: new Footer({ children: [ new Paragraph({
          tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
          children: [
            new TextRun({ text: "Confidential \u2014 For internal circulation only", italics: true, size: 18, color: TEXT_MUTED }),
            new TextRun({ text: "\t" }),
            new TextRun({ text: "Page ", size: 18, color: TEXT_MUTED }),
            new TextRun({ children: [PageNumber.CURRENT], size: 18, color: TEXT_MUTED }),
            new TextRun({ text: " of ", size: 18, color: TEXT_MUTED }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 18, color: TEXT_MUTED }),
          ],
        })]}),
      },
      children: [
        ...cover,
        h1("Table of Contents"),
        new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
        new Paragraph({ children: [new PageBreak()] }),
        ...intro,
        ...DASH,
        ...ANN,
        ...DIN,
        ...CMP,
        ...COMP,
        ...WATCH,
        ...USR,
        ...nfr,
      ],
    },
  ],
});

const OUT = path.join("/Users/prakharsingh/Documents/Study Files/python things/delta_filings", "Delta_Filings_FRD_v2.docx");
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log(`Wrote ${OUT} (${buf.length} bytes)`);
});
