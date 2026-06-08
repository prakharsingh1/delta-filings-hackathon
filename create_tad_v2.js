/**
 * Delta Filings — Technical Architecture Document (TAD).
 * Output: Delta_Filings_TAD_v2.docx
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
const MONO = "0B0B0B";

const border = { style: BorderStyle.SINGLE, size: 4, color: GREY_BORDER };
const cellBorders = { top: border, bottom: border, left: border, right: border };

const p = (text) => new Paragraph({ spacing: { after: 120, line: 300 }, children: [new TextRun({ text })] });
const h1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 180 }, children: [new TextRun({ text })] });
const h2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 280, after: 140 }, children: [new TextRun({ text })] });
const h3 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_3, spacing: { before: 200, after: 100 }, children: [new TextRun({ text })] });
const bullet = (text) => new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80, line: 280 }, children: [new TextRun(text)] });

const code = (text) =>
  new Paragraph({
    spacing: { after: 120, line: 280 },
    shading: { fill: "F4F4F4", type: ShadingType.CLEAR, color: "auto" },
    border: { left: { style: BorderStyle.SINGLE, size: 8, color: BRAND, space: 6 } },
    children: [new TextRun({ text, font: "Menlo", size: 20, color: MONO })],
  });

const cell = (content, opts = {}) => new TableCell({
  borders: cellBorders,
  width: { size: opts.width || 0, type: WidthType.DXA },
  shading: opts.fill ? { fill: opts.fill, type: ShadingType.CLEAR, color: "auto" } : undefined,
  verticalAlign: VerticalAlign.CENTER,
  margins: { top: 80, bottom: 80, left: 140, right: 140 },
  children: Array.isArray(content) ? content : [
    new Paragraph({ spacing: { after: 40, line: 280 },
      children: [new TextRun({ text: String(content ?? ""), bold: opts.bold, color: opts.color })] }),
  ],
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
  return new Table({
    width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA },
    columnWidths: widths, rows: body,
  });
};

// ---------- Content ----------
const cover = [
  new Paragraph({ spacing: { before: 2400, after: 200 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "\u0394  DELTA FILINGS", bold: true, size: 64, color: BRAND })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
    children: [new TextRun({ text: "Corporate Governance Intelligence Platform", size: 30, color: TEXT_MUTED, italics: true })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 2400, after: 120 },
    children: [new TextRun({ text: "Technical Architecture Document", bold: true, size: 44 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 2400 },
    children: [new TextRun({ text: "Version 2.0  \u2014  April 2026", size: 24, color: TEXT_MUTED })] }),
  new Paragraph({ children: [new PageBreak()] }),
];

const sections = [
  h1("1.  Overview"),
  p("Delta Filings v2 is a multi-service web platform built on a containerised stack. This Technical Architecture Document describes the services, data flows, deployment topology and non-functional guarantees of the system. It pairs with the BRD and FRD to close the loop between business intent and runtime design."),

  h1("2.  Architectural Principles"),
  bullet("Separation of concerns: UI, API, ingestion and data are separate services."),
  bullet("Statelessness: application tiers are stateless and horizontally scalable."),
  bullet("Cache by default: Redis sits in front of expensive reads and external calls."),
  bullet("Reproducibility: any environment can be brought up from a single Docker Compose file."),
  bullet("Observability before features: structured logs and metrics accompany every new endpoint."),
  bullet("Security by default: authenticated by default, explicit allow-lists for public endpoints."),

  h1("3.  System Context"),
  p("Delta Filings interacts with three external categories of systems: statutory data sources (BSE, NSE, MCA), identity and analytics providers (Supabase, OpenAI), and end-user browsers. The platform itself is an internally-structured monolith of four principal services."),
  table(
    ["External System", "Direction", "Protocol", "Purpose"],
    [
      ["BSE announcements feed", "Inbound pull", "HTTPS / HTML", "Filing ingestion."],
      ["NSE announcements feed", "Inbound pull", "HTTPS / JSON", "Filing ingestion."],
      ["MCA director portal", "Inbound pull", "HTTPS / HTML", "Director records for DIN lookup."],
      ["Supabase", "Bidirectional", "HTTPS / JWT", "Authentication and user store."],
      ["OpenAI", "Outbound", "HTTPS / REST", "Filing summarisation and classification."],
      ["Transactional email provider", "Outbound", "HTTPS / REST", "Alerts and digests."],
    ],
    [2400, 1600, 1800, 3560],
  ),

  h1("4.  Logical Components"),
  table(
    ["Component", "Runtime", "Responsibilities"],
    [
      ["Frontend", "Next.js 15 on Node 20", "SSR, hydration, UI, session cookie handling."],
      ["API Gateway / Backend", "FastAPI (Python 3.12)", "REST endpoints, authz, business logic."],
      ["Ingestion Workers", "Python async tasks", "Scrape BSE/NSE/MCA, normalise, persist."],
      ["Summary Worker", "Python async tasks", "Fetch filings from queue, call OpenAI, persist summary."],
      ["Alerts Worker", "Python async tasks", "Watchlist alert generation and dispatch."],
      ["Postgres", "PostgreSQL 16", "System of record."],
      ["Redis", "Redis 7", "Cache, dedup, job queue (streams)."],
      ["Object Storage", "S3-compatible", "Evidence uploads, exported PDFs."],
    ],
    [2200, 2400, 4760],
  ),

  h1("5.  Deployment Topology"),
  h2("5.1  Development"),
  p("All services brought up locally via Docker Compose. Bind mounts enable live reload for frontend and backend."),
  code([
    "services:",
    "  postgres:  postgres:16-alpine        (port 5432)",
    "  redis:     redis:7-alpine             (port 6379)",
    "  backend:   fastapi + uvicorn --reload (port 8000)",
    "  frontend:  next dev                   (port 3000)",
  ].join("\n")),

  h2("5.2  Production"),
  bullet("Frontend: Next.js standalone build served behind a CDN."),
  bullet("Backend: container orchestrator (Fly/Render/ECS) with at least 2 replicas behind a load balancer."),
  bullet("Workers: separate deployment per worker type; auto-scaled by queue depth."),
  bullet("Postgres: managed instance with HA + daily backups, retained 30 days."),
  bullet("Redis: managed instance with eviction policy 'allkeys-lru' and persistence disabled (cache-only usage)."),
  bullet("Secrets: managed vault; no secrets in images or environment-less config."),

  h1("6.  Data Model (Simplified)"),
  p("The table below captures the core relational schema. Full DDL lives in the Alembic migration history."),
  table(
    ["Table", "Key Columns", "Notes"],
    [
      ["user_profiles", "id (uuid, PK), email, display_name", "Mirrors Supabase user; 1:1 on id."],
      ["companies", "id, symbol, isin, cin, sector, listed_on", "Unique(symbol, exchange)."],
      ["filings", "id, company_id, category, published_at, source_url, summary, materiality", "Indexed on (company_id, published_at desc)."],
      ["directors", "din (PK), name, dob_year, is_disqualified, updated_at", ""],
      ["director_associations", "director_din, company_id, role, start_date, end_date", "Composite PK."],
      ["watchlists", "id, user_id, name, created_at", "Unique(user_id, name)."],
      ["watchlist_members", "watchlist_id, company_id", "Composite PK."],
      ["obligations", "id, statute, title, frequency, applicability_json", ""],
      ["compliance_runs", "id, obligation_id, company_id, status, due_date, assignee_id, evidence_json, hash", "Immutable once status = 'complete'."],
      ["audit_log", "id, actor_id, action, target_type, target_id, created_at, payload_json", "Append-only."],
    ],
    [2200, 3200, 3960],
  ),

  h1("7.  API Surface"),
  p("All endpoints are prefixed with /api. Authenticated endpoints require a Supabase JWT either as a cookie or bearer token."),
  table(
    ["Method", "Path", "Purpose"],
    [
      ["GET", "/api/health", "Liveness probe."],
      ["GET", "/api/filings", "List filings with filters."],
      ["GET", "/api/filings/{id}", "Filing detail."],
      ["GET", "/api/directors/{din}", "Director lookup."],
      ["POST", "/api/compare", "Create a director comparison."],
      ["GET", "/api/watchlists", "List the user's watchlists."],
      ["POST", "/api/watchlists", "Create a watchlist."],
      ["GET", "/api/compliance/obligations", "List obligations for a company."],
      ["POST", "/api/compliance/runs", "Create a compliance run."],
      ["GET", "/api/compliance/runs/{id}/export", "Export run as PDF."],
    ],
    [1200, 3600, 4560],
  ),

  h1("8.  Ingestion Pipeline"),
  p("Each ingestion worker follows the same pipeline pattern: fetch, normalise, dedup, persist, enqueue downstream work."),
  code([
    "[Scheduler] --tick every 60s during market hours-->",
    "[Scraper]  --parse HTML/JSON--> [Normaliser]",
    "[Normaliser] --sha256(source_url+timestamp)--> [Dedup via Redis]",
    "[Dedup miss] --insert--> [Postgres filings]",
    "               --enqueue--> [Redis stream: summary_jobs]",
    "[Summary Worker] --consume--> [OpenAI chat.completions]",
    "                 --update filings.summary, filings.materiality-->",
    "                 --enqueue--> [Redis stream: alert_jobs]",
  ].join("\n")),

  h1("9.  Security"),
  h2("9.1  AuthN / AuthZ"),
  bullet("Supabase issues RS256 JWTs; backend verifies signature using the Supabase public JWKS."),
  bullet("Session cookie is httpOnly, Secure, SameSite=Lax; Expiry 60 minutes with refresh rotation."),
  bullet("Per-endpoint scope enforcement (public / user / compliance-write / admin)."),

  h2("9.2  Network"),
  bullet("TLS 1.2+ for all external traffic; internal service-to-service over private network."),
  bullet("CORS allow-list is environment-configured; no wildcards in production."),
  bullet("Rate limits at the API layer keyed by user_id and IP."),

  h2("9.3  Data"),
  bullet("No card numbers, bank accounts, government IDs stored."),
  bullet("Evidence uploads scanned for MIME validity; size cap at 25 MB per file."),
  bullet("Backups encrypted at rest; access gated behind on-call role."),

  h1("10.  Observability"),
  bullet("Logs: structured JSON, trace-id propagated across services, shipped to a central log store."),
  bullet("Metrics: Prometheus-compatible, per-endpoint latency histograms and error counters."),
  bullet("Tracing: OpenTelemetry spans across frontend-fetch, backend-request and worker-job."),
  bullet("Synthetic probes: /api/health, /dashboard and /din every 60s."),
  bullet("Alerts: Pager duty routes for Sev-1 with 15-minute acknowledgement SLO."),

  h1("11.  Performance Targets"),
  table(
    ["Surface", "Metric", "Target"],
    [
      ["Dashboard", "Time to interactive (p95, 4G)", "< 2.5s"],
      ["API", "Latency (p95, authenticated)", "< 400ms"],
      ["DIN lookup", "Latency (p95, cache hit)", "< 250ms"],
      ["Ingestion", "Lag from source publication (p95)", "< 15m"],
      ["Alerts", "Dispatch latency after ingestion (p95)", "< 2m"],
    ],
    [2400, 4000, 2960],
  ),

  h1("12.  Reliability and Backups"),
  bullet("RPO: 24 hours. Backups run daily with 30-day retention."),
  bullet("RTO: 4 hours for Sev-1 incidents during market hours."),
  bullet("Monthly restore-drill on a non-production environment."),
  bullet("Stateless services recovered by redeploying last healthy image."),

  h1("13.  Trade-offs and Alternatives Considered"),
  table(
    ["Decision", "Chosen", "Alternative", "Reason"],
    [
      ["Backend framework", "FastAPI", "Django REST, Flask", "Async-first, strong typing, light footprint."],
      ["Frontend framework", "Next.js 15 App Router", "Remix, Vite + React Router", "Mature SSR, strong ecosystem, easy deploy."],
      ["Primary DB", "PostgreSQL", "MySQL, DynamoDB", "Rich SQL, extensions (JSONB, pg_trgm)."],
      ["Queue", "Redis Streams", "RabbitMQ, SQS", "Reuses cache infra; adequate throughput at v2 scale."],
      ["Auth", "Supabase", "Auth0, custom", "Fast onboarding; JWT model interoperable."],
    ],
    [1900, 1900, 1900, 3660],
  ),

  h1("14.  Roadmap Dependencies"),
  bullet("M2 blocks on ingestion pipeline + summary worker."),
  bullet("M3 blocks on director scraper hardening + curated snapshot pipeline."),
  bullet("M4 blocks on object storage integration + PDF export service."),
  bullet("M5 blocks on transactional email integration and preference UI."),
  bullet("M7 blocks on billing (Stripe/Razorpay) integration and SSO support."),

  h1("15.  Open Questions"),
  bullet("Do we introduce a search index (OpenSearch) in v2 or defer to v3?"),
  bullet("Will compliance runs require digital signatures for audit-level legal weight?"),
  bullet("Residency \u2014 which managed Postgres provider has a true Indian region option by M6?"),
];

const doc = new Document({
  creator: "Delta Filings Engineering",
  title: "Delta Filings \u2014 Technical Architecture Document",
  description: "TAD for the Delta Filings v2 platform.",
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
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ],
  },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 },
      margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    headers: { default: new Header({ children: [new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: BRAND, space: 4 } },
      children: [
        new TextRun({ text: "Delta Filings  \u2014  Technical Architecture", color: BRAND, bold: true, size: 18 }),
        new TextRun({ text: "\t" }),
        new TextRun({ text: "v2.0  |  April 2026", color: TEXT_MUTED, size: 18 }),
      ],
    })]}) },
    footers: { default: new Footer({ children: [new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      children: [
        new TextRun({ text: "Confidential \u2014 Engineering", italics: true, size: 18, color: TEXT_MUTED }),
        new TextRun({ text: "\t" }),
        new TextRun({ text: "Page ", size: 18, color: TEXT_MUTED }),
        new TextRun({ children: [PageNumber.CURRENT], size: 18, color: TEXT_MUTED }),
        new TextRun({ text: " of ", size: 18, color: TEXT_MUTED }),
        new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 18, color: TEXT_MUTED }),
      ],
    })]}) },
    children: [
      ...cover,
      h1("Table of Contents"),
      new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
      new Paragraph({ children: [new PageBreak()] }),
      ...sections,
    ],
  }],
});

const OUT = path.join("/Users/prakharsingh/Documents/Study Files/python things/delta_filings", "Delta_Filings_TAD_v2.docx");
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(OUT, buf); console.log(`Wrote ${OUT} (${buf.length} bytes)`); });
