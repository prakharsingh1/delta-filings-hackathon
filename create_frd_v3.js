/**
 * Delta Filings — Functional Requirements Document (FRD) generator, v3.
 *
 * Companion to Delta_Filings_BRD_v3.docx. Decomposes the BRD's business
 * requirements into module-by-module functional specifications that match
 * the production platform on Railway (May 2026).
 *
 * Run:    node create_frd_v3.js
 * Output: Delta_Filings_FRD_v3.docx
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
    h2(`${id}  —  ${title}`),
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
    ...steps.map((s) =>
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
    children: [new TextRun({ text: "Δ  DELTA FILINGS", bold: true, size: 64, color: BRAND })],
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
    children: [new TextRun({ text: "Version 3.0  —  May 2026", size: 24, color: TEXT_MUTED })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1200 },
    children: [new TextRun({ text: "Companion to: Delta Filings BRD v3", size: 22, color: TEXT_MUTED })],
  }),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- Intro ----------
const intro = [
  h1("1.  Introduction"),

  h2("1.1  Purpose"),
  p(
    "This FRD decomposes the business requirements in BRD v3 into module-by-module functional specifications. Each feature block captures the purpose, actors, main flow, alternate flows and acceptance criteria required for implementation and verification."
  ),

  h2("1.2  Audience"),
  bullet("Engineering — implementation reference."),
  bullet("QA — test-case derivation."),
  bullet("Product — scope and acceptance alignment."),
  bullet("Design — flow-by-flow UX contract."),

  h2("1.3  Conventions"),
  bullet("Functional IDs follow the pattern FR-<module>-<sequence>."),
  bullet("Priority uses MoSCoW (Must / Should / Could)."),
  bullet("'System' refers to the Delta Filings Flask application running on Railway."),
  bullet("All timestamps are IST unless stated otherwise."),

  h2("1.4  Traceability to BRD"),
  table(
    ["FRD Module", "BRD Requirements"],
    [
      ["Dashboard (FR-D)", "BR-001"],
      ["Filings & Announcements (FR-A)", "BR-002, BR-003, BR-004, BR-006, BR-007"],
      ["Filing Diff (FR-FD)", "BR-005"],
      ["Draft Engine (FR-DR)", "BR-010 — BR-018"],
      ["DIN Search (FR-N)", "BR-020, BR-021"],
      ["Governance & XBRL (FR-G)", "BR-022, BR-023"],
      ["Compliance OS (FR-M)", "BR-030 — BR-034"],
      ["Stock Prices (FR-P)", "BR-040, BR-041"],
      ["Price Alerts (FR-AL)", "BR-042, BR-043, BR-044"],
      ["Industry Intelligence (FR-I)", "BR-045"],
      ["Watchlist (FR-W)", "BR-050, BR-051"],
      ["Authentication & Onboarding (FR-U)", "BR-052, BR-053, BR-056"],
      ["Payments & Access (FR-PY)", "BR-054, BR-055"],
      ["Experience & Theme (FR-UX)", "BR-060 — BR-064"],
    ],
    [3000, 6360],
  ),

  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- 2. Dashboard ----------
const DASH = [
  h1("2.  Dashboard Module (FR-D)"),
  ...feature({
    id: "FR-D-01",
    title: "Render the monthly filings calendar for the user's watchlist",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Give the user an at-a-glance view of all corporate filings across their watchlisted companies for a chosen month.",
    pre: "User is authenticated, has paid (or redeemed a referral) and has at least one watchlist entry.",
    post: "User sees a calendar grid with per-day filing pills, plus recent activity, top tags and stats.",
    steps: [
      "User navigates to /dashboard.",
      "System loads the user's watchlist and resolves the active month (defaults to the current IST month).",
      "System queries filings for those symbols within the month and groups them by date.",
      "System computes stat tiles (filings tracked, this-week count, last-filing freshness) and recent activity (last 10 filings).",
      "System renders the calendar, sector pulse card, and feature shortcuts.",
    ],
    alt: [
      "Empty watchlist — system shows an onboarding empty-state with a CTA to add a company.",
      "Filings for a watchlisted company are missing or older than 6 hours — system kicks off an async backfill thread; UI shows a refreshing indicator on that company.",
    ],
    acceptance: [
      "Calendar reflects timezone-correct (IST) day boundaries for every filing.",
      "Recent activity row always shows up to 10 of the most recent filings across the watchlist.",
      "Async backfill does not block the dashboard render.",
    ],
  }),
  ...feature({
    id: "FR-D-02",
    title: "Sector pulse card with weekly AI briefing",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Give the user a one-paragraph briefing on what happened in their industry this past week.",
    pre: "User has chosen an industry during onboarding.",
    post: "Card shows the briefing text plus the source-headline count tracked over the past 14 days.",
    steps: [
      "Dashboard initiates a request to /api/industry-intel/{industry}.",
      "System returns either the cached (≤ 30 min) result or recomputes it from Google News + curated profile + Gemini.",
      "Card renders the briefing text and a 'Full briefing →' link to /industry/{industry}.",
    ],
    alt: [
      "Industry not in the curated 16 — system shows a neutral fallback briefing with a 'limited coverage' note.",
      "Gemini unavailable — system falls back to a heuristic summary of the top headline.",
    ],
    acceptance: [
      "Briefing carries a visible 'machine-generated' label.",
      "The card is readable in both light and dark themes (dark-mode gradient override ensures legibility).",
    ],
  }),
];

// ---------- 3. Filings ----------
const ANN = [
  h1("3.  Filings and Announcements Module (FR-A)"),
  ...feature({
    id: "FR-A-01",
    title: "Per-company filings list with pagination",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Let the user browse the full filing history for any tracked company.",
    pre: "Company symbol exists in the user's watchlist (or is known to the system).",
    post: "User sees 15 filings per page, sorted newest-first, with tags and dates.",
    steps: [
      "User opens /company/{symbol}.",
      "System checks the local filings cache for the symbol.",
      "If the cache is sparse (< 50 rows) or stale (> 6 h), system spawns an async backfill via fetch.py.",
      "System paginates the available rows (15 per page) and renders them with tag badges.",
      "User can click a filing to open the detail modal.",
    ],
    alt: [
      "NSE returns 5xx — fetch.py falls back to a sample dataset; backfill error is logged.",
      "Concurrent backfill already in flight — in-process lock prevents duplicate scraping.",
    ],
    acceptance: [
      "First page renders even when backfill is still in progress.",
      "Tag pills use the same dark-mode tinted styling as the dashboard for visual consistency.",
    ],
  }),
  ...feature({
    id: "FR-A-02",
    title: "On-demand AI summary for a filing",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Reduce time-to-insight by summarising a filing on demand.",
    pre: "Filing exists; raw_text has been extracted (or can be) from its source PDF.",
    post: "Summary is generated, persisted on the filings row, and shown in the UI.",
    steps: [
      "User clicks 'Summarise' on a filing.",
      "System ensures raw_text is present (extracts via pdf_parser.py if needed).",
      "System calls Gemini (Flash for short text, Pro for long text) with a structured prompt.",
      "System stores the summary back on the filings row and returns it.",
    ],
    alt: [
      "Gemini API key missing — system returns a heuristic summary from the first ~200 characters of raw_text.",
      "Gemini quota exhausted — system surfaces a 'try again later' error and does not persist a stub.",
    ],
    acceptance: [
      "Summary carries a visible 'machine-generated' label.",
      "Summary persisted exactly once; subsequent views read from the database.",
      "Summary endpoint times out gracefully after 60 s.",
    ],
  }),
  ...feature({
    id: "FR-A-03",
    title: "Search and tag filter within a company",
    priority: "Should",
    actors: "Authenticated paid user",
    purpose: "Let the user narrow a company's filings by free text or category.",
    pre: "User is on /company/{symbol}.",
    post: "Filings list re-renders to match the active filters.",
    steps: [
      "User types a search query or clicks a tag chip.",
      "System calls /api/search/{symbol} or /api/tags/{symbol}.",
      "System returns matching filings; UI replaces the list, preserving pagination state.",
    ],
    alt: [
      "No matches — UI shows an empty-state with a 'clear filters' CTA.",
    ],
    acceptance: [
      "Search responds within 500 ms at p95 on a populated cache.",
      "Tag chip filtering and free-text search are composable.",
    ],
  }),
  ...feature({
    id: "FR-A-04",
    title: "Extract and view embedded XBRL",
    priority: "Should",
    actors: "Authenticated paid user",
    purpose: "Surface structured XBRL data when the source PDF embeds it.",
    pre: "Filing has has_xbrl = true.",
    post: "User sees parsed XBRL key-value pairs in a modal section.",
    steps: [
      "User opens a filing detail modal.",
      "System calls /api/xbrl/{id} which invokes pdf_parser.extract_xbrl().",
      "UI renders a tagged keyed view (e.g., 'Net Profit', 'Date').",
    ],
    alt: [
      "PDF has no embedded XBRL — endpoint returns 404; UI shows nothing in the XBRL section.",
      "Parser fails — endpoint returns the raw XML; UI shows a 'raw XBRL' fallback.",
    ],
    acceptance: [
      "XBRL extraction completes within 5 s at p95 for filings up to 5 MB.",
    ],
  }),
];

// ---------- 4. Filing Diff ----------
const DIFF = [
  h1("4.  Filing Diff Module (FR-FD)"),
  ...feature({
    id: "FR-FD-01",
    title: "Auto-match and diff a filing against the previous similar filing",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Let the user see what changed between successive filings of the same type.",
    pre: "Filing exists; at least one earlier filing of the same type and company exists in cache.",
    post: "User sees inline and side-by-side diff views.",
    steps: [
      "User clicks 'Compare' on a filing.",
      "System calls /api/compare/{id} which invokes find_best_match() from database.py.",
      "Match candidate selected by title similarity + date ordering.",
      "diff_engine.compute_diff() runs over the two raw_text bodies.",
      "diff_to_html() and side_by_side_html() return rendered HTML for the modal.",
    ],
    alt: [
      "No reasonable match found — system returns 404 with a 'no prior filing to compare' message.",
      "Filings are too large — system truncates each side to ~200 KB before diffing.",
    ],
    acceptance: [
      "Diff result reflects line-level additions, deletions and information markers.",
      "Diff colours and dark-mode styling render correctly in both themes.",
    ],
  }),
  ...feature({
    id: "FR-FD-02",
    title: "Diff two arbitrary filings",
    priority: "Should",
    actors: "Authenticated paid user",
    purpose: "Let the user explicitly compare any two filings of their choice.",
    pre: "Both filing IDs exist.",
    post: "User sees the diff in inline and side-by-side modes.",
    steps: [
      "User opens the filings picker, chooses two filings.",
      "System calls /api/compare/{id_a}/{id_b}.",
      "diff_engine renders the result; UI mounts it into the modal.",
    ],
    alt: [
      "Filings have no raw_text — system extracts via pdf_parser before diffing.",
    ],
    acceptance: [
      "Diff renders within 8 s at p95 for two filings up to 100 KB each.",
    ],
  }),
];

// ---------- 5. Draft Engine ----------
const DRAFT = [
  h1("5.  Draft Engine — SEBI Letter Wizard (FR-DR)"),
  ...feature({
    id: "FR-DR-01",
    title: "Select filing type and pull past examples",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Let the user pick a SEBI filing type and learn from past examples for the chosen company.",
    pre: "User is on /draft; watchlist contains the target company.",
    post: "UI shows the filing-type registry and a list of past examples for the chosen type.",
    steps: [
      "User opens /draft.",
      "UI calls /api/draft/filing-types to load the registry of 20+ supported types.",
      "User selects a type; UI calls /api/draft/examples for {company, type}.",
      "System returns the most recent five matching filings; if none cached, system kicks off an async fetch.",
    ],
    alt: [
      "No matches even after fetch — UI shows 'no past examples' and disables 'Learn style'.",
    ],
    acceptance: [
      "Filing-type registry covers all periodic and major event-based SEBI filings.",
      "Examples list reflects the watchlist company, not a global pool.",
    ],
  }),
  ...feature({
    id: "FR-DR-02",
    title: "Learn the company's writing style",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Capture the tone and structure of a company's prior filings so the AI draft sounds right.",
    pre: "Past examples are available.",
    post: "System returns a style fingerprint (tone, sentence length, formality markers).",
    steps: [
      "User clicks 'Learn style'.",
      "System sends the most recent past examples to Gemini with a structured prompt.",
      "Gemini returns a structured fingerprint object.",
      "UI persists the fingerprint into the draft session state.",
    ],
    alt: [
      "Gemini unavailable — UI proceeds with a generic 'formal corporate tone' fallback.",
    ],
    acceptance: [
      "Fingerprint never includes personally identifying information.",
      "Subsequent drafts use the fingerprint without re-prompting until the user changes the filing type.",
    ],
  }),
  ...feature({
    id: "FR-DR-03",
    title: "AI-generate the draft body",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Produce a first-draft SEBI letter combining the user's intent, the company's style and the chosen type's template.",
    pre: "Filing type selected; style fingerprint available (or skipped); user has filled the intent inputs.",
    post: "System returns a structured letter (subject, body paragraphs, optional annexures).",
    steps: [
      "User clicks 'Generate draft'.",
      "Backend builds the prompt: type template + fingerprint + user intent + boilerplate from past examples.",
      "Gemini returns the draft; system parses it into the editor schema.",
      "UI mounts the result into the editor.",
    ],
    alt: [
      "Gemini timeout — UI surfaces a retry CTA; no partial draft is committed.",
      "Output fails schema validation — system requests a single re-emission then surfaces a manual-fix CTA.",
    ],
    acceptance: [
      "Draft always includes a clearly labelled 'machine-generated' notice.",
      "Generation completes within 30 s at p95.",
    ],
  }),
  ...feature({
    id: "FR-DR-04",
    title: "AI-fill placeholders from public knowledge",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Fill repetitive placeholders (CIN, registered office, ISIN) using public knowledge.",
    pre: "Draft contains at least one placeholder.",
    post: "Placeholders are replaced with confident values; uncertain ones remain flagged.",
    steps: [
      "User clicks 'AI-fill'.",
      "System calls /api/draft/ai-fill with the placeholder list and target company.",
      "Gemini returns values with confidence ratings.",
      "UI updates the editor and highlights any low-confidence values for human review.",
    ],
    alt: [
      "Gemini cannot find a value — placeholder kept as-is; UI marks it as 'manual-fill required'.",
    ],
    acceptance: [
      "Low-confidence fills are visually distinguished and excluded from export until reviewed.",
    ],
  }),
  ...feature({
    id: "FR-DR-05",
    title: "Improve, tone-match, formalise or custom-rewrite",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Let the user iterate on the draft with one-click rewrites.",
    pre: "Draft has a non-empty body.",
    post: "Body is replaced with the rewritten version; previous version remains in the editor history.",
    steps: [
      "User clicks 'Polish' / 'Match tone' / 'Formal' / 'Custom'.",
      "UI calls /api/draft/improve with the action and (if custom) the user's instruction.",
      "Gemini returns the rewritten body; UI swaps it in.",
    ],
    alt: [
      "User undoes — UI restores the previous body from history.",
    ],
    acceptance: [
      "Each improvement action is logged in the editor history with timestamp.",
    ],
  }),
  ...feature({
    id: "FR-DR-06",
    title: "Export draft as PDF and DOCX",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Produce a print-ready deliverable.",
    pre: "Draft body is non-empty.",
    post: "Browser downloads the chosen format.",
    steps: [
      "User clicks 'Export PDF' or 'Export DOCX'.",
      "Backend (draft_engine.py) renders the document via reportlab or python-docx.",
      "Browser receives the file with the appropriate Content-Disposition.",
    ],
    alt: [
      "Render fails — UI surfaces the error; no partial file is delivered.",
    ],
    acceptance: [
      "PDF and DOCX outputs preserve subject, body, signature block and annexure list.",
      "Export completes within 6 s at p95.",
    ],
  }),
  ...feature({
    id: "FR-DR-07",
    title: "Save, list and resume drafts",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Persist drafts so the user can leave and return.",
    pre: "User is signed in.",
    post: "Draft row is created (or updated) in user_drafts.",
    steps: [
      "Editor autosaves on each meaningful change (debounced).",
      "User can list previous drafts via /api/drafts and re-open one via /api/drafts/{id}.",
      "User can delete via DELETE /api/drafts/{id}.",
    ],
    alt: [
      "Save fails — UI surfaces 'unsaved changes' badge until retry succeeds.",
    ],
    acceptance: [
      "Drafts are strictly scoped to the owning user_id at the database query layer.",
      "Listing returns only the user's own drafts and the drafts they collaborate on."
    ],
  }),
  ...feature({
    id: "FR-DR-08",
    title: "Team collaboration via invite links",
    priority: "Should",
    actors: "Draft owner; invited collaborator",
    purpose: "Allow another user to view or edit a draft via a shareable token.",
    pre: "Draft exists and current user is the owner.",
    post: "Token is generated; recipient gains the configured role on signin.",
    steps: [
      "Owner clicks 'Invite' and chooses editor or viewer role.",
      "System creates a row in draft_invites with a UUID token and expiry.",
      "Owner shares the /draft/invite/{token} URL.",
      "Recipient opens the URL; if signed in, system adds them to draft_teams with the configured role; if not, the token is stashed in the session and applied after signin.",
    ],
    alt: [
      "Token expired or revoked — landing page shows a neutral 'invite no longer valid' message.",
      "Owner revokes a pending invite — token deleted; URL returns 404.",
    ],
    acceptance: [
      "Role changes and removals are owner-only and audited.",
      "Public landing page never reveals draft contents before claim.",
    ],
  }),
];

// ---------- 6. DIN ----------
const DIN = [
  h1("6.  DIN Search Module (FR-N)"),
  ...feature({
    id: "FR-N-01",
    title: "Search directors across filings, governance reports and MCA history",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Return a comprehensive view of any director across all available sources.",
    pre: "User is on /din-search.",
    post: "Results section lists matches grouped by source.",
    steps: [
      "User submits a DIN or director name.",
      "System validates input (DIN must be 8 digits; name must be ≥ 3 chars).",
      "System runs search_din_across_companies() over filings text and governance XBRL.",
      "In parallel, system calls mca_scraper to fetch director history (cached 30 days).",
      "UI renders a unified results card with sources clearly labelled.",
    ],
    alt: [
      "Invalid input — inline field-level error; no requests issued.",
      "MCA scrape blocked — system falls back to filings and XBRL only with a 'partial' badge.",
    ],
    acceptance: [
      "Cached responses (≤ 30 days) are reused without network calls.",
      "Results are ordered by recency where date is available.",
    ],
  }),
];

// ---------- 7. Governance ----------
const GOV = [
  h1("7.  Governance and XBRL Module (FR-G)"),
  ...feature({
    id: "FR-G-01",
    title: "List a company's governance reports",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Surface the catalogue of governance disclosures filed through the NSE Integrated Filing system.",
    pre: "Symbol is known to the system.",
    post: "UI shows a list of governance reports with type and date.",
    steps: [
      "User opens the governance section on a company page.",
      "System calls /api/governance/{symbol} which delegates to governance.list_reports().",
      "List is rendered with select buttons for inspecting or comparing.",
    ],
    alt: [
      "NSE returns no reports — UI shows an empty-state placeholder.",
    ],
    acceptance: [
      "List is cached server-side for 6 hours per symbol.",
    ],
  }),
  ...feature({
    id: "FR-G-02",
    title: "Inspect a single governance report (XBRL parse)",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Show the structured contents of a governance report.",
    pre: "Report ID is valid.",
    post: "Parsed XBRL is rendered as a keyed list grouped by section.",
    steps: [
      "User clicks a report row.",
      "System calls /api/governance/{symbol}/{rec_id} which downloads the XBRL and runs the parser.",
      "UI renders the parsed contents.",
    ],
    alt: [
      "Parse fails — UI shows raw XML inside an expandable block.",
    ],
    acceptance: [
      "Parse completes within 8 s at p95.",
    ],
  }),
  ...feature({
    id: "FR-G-03",
    title: "Compare two governance reports",
    priority: "Should",
    actors: "Authenticated paid user",
    purpose: "Highlight changes between two governance reports of the same company.",
    pre: "Both report IDs are valid and belong to the same company.",
    post: "UI shows a side-by-side compare view of the two parsed reports.",
    steps: [
      "User selects two reports.",
      "System calls /api/governance/compare/{symbol}/{rec_a}/{rec_b}.",
      "Backend parses both and computes a structural diff.",
      "UI renders the comparison.",
    ],
    alt: [
      "One report fails to parse — UI shows it as 'unparseable' and renders the other side normally.",
    ],
    acceptance: [
      "Compare endpoint completes within 12 s at p95.",
    ],
  }),
];

// ---------- 8. Compliance ----------
const COMP = [
  h1("8.  Compliance OS Module (FR-M)"),
  ...feature({
    id: "FR-M-01",
    title: "Show obligations and scores for each watchlisted company",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Surface upcoming, overdue and completed regulatory obligations for the user's watchlist.",
    pre: "User is signed in and on /compliance.",
    post: "Dashboard renders per-company score cards plus a unified obligations list.",
    steps: [
      "User opens /compliance.",
      "System calls /api/compliance which aggregates obligations + enforcement orders.",
      "compliance.refresh_compliance is auto-triggered for any company that lacks recent data.",
      "UI renders score cards, upcoming/overdue/completed groupings and the SEBI enforcement summary.",
    ],
    alt: [
      "Watchlist is empty — UI prompts the user to add companies.",
    ],
    acceptance: [
      "All obligations link to their source statute or SEBI regulation (via sebi_link_resolver)."
    ],
  }),
  ...feature({
    id: "FR-M-02",
    title: "Manual refresh of compliance and enforcement data",
    priority: "Should",
    actors: "Authenticated paid user",
    purpose: "Let the user force a refresh when they suspect the cache is stale.",
    pre: "User is on /compliance.",
    post: "Database rows for obligations / enforcement orders are updated.",
    steps: [
      "User clicks 'Refresh' on a company card or globally.",
      "System calls /api/compliance/refresh?symbol=X (or without symbol for all).",
      "Refresh runs in a background thread; UI polls for completion.",
    ],
    alt: [
      "SEBI portal unavailable — endpoint returns partial success; UI surfaces the affected company.",
    ],
    acceptance: [
      "Refresh never blocks the request thread for more than 1 s.",
    ],
  }),
  ...feature({
    id: "FR-M-03",
    title: "SEBI enforcement matching with confidence ratings",
    priority: "Must",
    actors: "System",
    purpose: "Map SEBI public orders to the companies a user tracks.",
    pre: "User has added at least one company.",
    post: "enforcement_orders rows exist with a confidence rating (high / medium / low).",
    steps: [
      "sebi_enforcement.refresh_enforcement_async() runs in the background.",
      "For each company, scraper queries five SEBI order categories.",
      "Title and party fields are normalised; a similarity score against the company's legal name yields the confidence rating.",
      "Rows persisted with checked_at timestamp.",
    ],
    alt: [
      "SEBI rate-limits — scraper backs off and resumes.",
    ],
    acceptance: [
      "At least 70% of stored orders carry a 'high' confidence rating against a labelled sample.",
    ],
  }),
];

// ---------- 9. Stock Prices ----------
const PRC = [
  h1("9.  Stock Prices Module (FR-P)"),
  ...feature({
    id: "FR-P-01",
    title: "Live prices, 3% movers and pre-open",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Surface live quotes for the watchlist, highlight movers and expose pre-open data.",
    pre: "User is on /prices.",
    post: "UI shows live prices, a built-in 3% mover ribbon and pre-open alerts.",
    steps: [
      "Page polls /api/prices on an interval.",
      "stock_prices.py fetches NSE quotes for the watchlist plus the built-in 3% mover universe.",
      "System applies any fired custom alerts and returns a unified payload.",
      "Pre-open data is fetched separately on /api/pre-open.",
    ],
    alt: [
      "NSE unavailable — endpoint returns cached values with a 'stale' badge.",
    ],
    acceptance: [
      "Watchlist tile updates within 60 s of an underlying NSE quote change.",
    ],
  }),
  ...feature({
    id: "FR-P-02",
    title: "Per-symbol quote with movement explanation",
    priority: "Should",
    actors: "Authenticated paid user",
    purpose: "Combine quote, recent filings and AI reasoning on /api/price/{symbol}.",
    pre: "Symbol is known.",
    post: "UI renders the detailed card.",
    steps: [
      "User clicks a symbol on the prices grid.",
      "System calls /api/price/{symbol} for quote and recent filings.",
      "User can request 'Deep intel' which calls /api/price-intel/{symbol}.",
      "price_intel.py aggregates Google News, Reddit and recent filings, runs Gemini for a one-paragraph explanation, caches for 15 minutes.",
    ],
    alt: [
      "Gemini unavailable — UI shows the raw inputs without the AI explanation.",
    ],
    acceptance: [
      "Cached intel returns in under 600 ms; fresh intel under 20 s at p95.",
    ],
  }),
];

// ---------- 10. Price Alerts ----------
const ALERT = [
  h1("10.  Price Alerts Module (FR-AL)"),
  ...feature({
    id: "FR-AL-01",
    title: "Create, list, toggle and delete custom alerts",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Let the user define their own price-movement alerts.",
    pre: "User is signed in.",
    post: "price_alerts row created / updated / deleted.",
    steps: [
      "User opens the alerts panel on /prices.",
      "User fills symbol, threshold %, direction (up / down / either) and submits.",
      "System upserts the alert via POST /api/alerts.",
      "User can toggle, delete or test alerts inline.",
    ],
    alt: [
      "Invalid symbol — inline error.",
      "Duplicate alert — system updates the existing row rather than creating a second.",
    ],
    acceptance: [
      "Each alert has a 60-minute cooldown enforced server-side.",
      "Alerts API never reveals other users' rows."
    ],
  }),
  ...feature({
    id: "FR-AL-02",
    title: "Alert evaluation and Telegram delivery",
    priority: "Must",
    actors: "System (alert_poller)",
    purpose: "Evaluate alerts against live prices and dispatch them.",
    pre: "Background poller has a lease for this cycle.",
    post: "Eligible alerts fire (Telegram + in-browser); last_fired_at updated.",
    steps: [
      "alert_poller.py runs every 60 seconds during IST market hours.",
      "Worker acquires the cron_meta lease (CAS update); other workers no-op.",
      "Worker queries enabled alerts whose cooldown has elapsed.",
      "Worker fetches the latest prices via stock_prices and evaluates each alert.",
      "Eligible alerts dispatched to Telegram (if linked) and surfaced to /api/prices.",
    ],
    alt: [
      "Telegram dispatch fails — alert remains marked 'fired'; failure logged for the user to retry.",
    ],
    acceptance: [
      "Only one Gunicorn worker fires alerts per cycle.",
      "End-to-end delivery latency (threshold to Telegram) ≤ 90 s at p95.",
    ],
  }),
  ...feature({
    id: "FR-AL-03",
    title: "Link and unlink Telegram",
    priority: "Should",
    actors: "Authenticated paid user",
    purpose: "Verify the user's Telegram identity and bind a chat_id to their account.",
    pre: "User has a Telegram account; Bot token configured.",
    post: "user_telegram row created (or removed).",
    steps: [
      "User clicks 'Link Telegram'.",
      "Telegram Login Widget returns a signed payload to /api/alerts/link-telegram.",
      "System verifies the HMAC signature against the Bot token.",
      "Row persisted; alerts begin delivering to that chat_id.",
    ],
    alt: [
      "Signature invalid — system refuses the link and shows an error.",
    ],
    acceptance: [
      "Chat IDs are never stored without a valid signature.",
      "Unlink purges the row immediately.",
    ],
  }),
];

// ---------- 11. Industry Intelligence ----------
const IND = [
  h1("11.  Industry Intelligence Module (FR-I)"),
  ...feature({
    id: "FR-I-01",
    title: "Industry profile + live news + weekly briefing",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Give the user a curated and live view of their tracked industry.",
    pre: "User has chosen an industry (one of 16 curated).",
    post: "UI shows the profile, four news buckets and the AI briefing.",
    steps: [
      "User opens /industry or /industry/{industry}.",
      "System calls /api/industry-intel/{industry}.",
      "industry_intel.py returns a 30-min cached payload of: curated profile, four Google News RSS buckets (sector / M&A / policy / global), and a Gemini briefing.",
      "UI renders the result with source attribution.",
    ],
    alt: [
      "Industry not in the 16 curated — system serves a neutral fallback profile.",
    ],
    acceptance: [
      "Briefing is clearly labelled 'machine-generated'.",
      "News items link directly to the original sources.",
    ],
  }),
];

// ---------- 12. Watchlist ----------
const WATCH = [
  h1("12.  Watchlist Module (FR-W)"),
  ...feature({
    id: "FR-W-01",
    title: "Add and remove companies",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Let the user curate the companies they track.",
    pre: "User is signed in.",
    post: "watchlist table updated; backfills triggered for added companies.",
    steps: [
      "User adds a company via the sidebar add-company control.",
      "System validates the symbol via /api/symbol_search if needed.",
      "POST /api/watchlist inserts the row.",
      "System spawns background backfill threads for filings + compliance + enforcement for the new company.",
    ],
    alt: [
      "Duplicate symbol — request returns 200 with no-op (UNIQUE constraint).",
    ],
    acceptance: [
      "Backfill never blocks the watchlist API response.",
    ],
  }),
  ...feature({
    id: "FR-W-02",
    title: "Switch industry to replace watchlist",
    priority: "Must",
    actors: "Authenticated paid user",
    purpose: "Let the user reset their watchlist to the canonical set for a different industry.",
    pre: "User is signed in.",
    post: "Watchlist replaced; backfills triggered.",
    steps: [
      "User picks a new industry in settings.",
      "POST /api/settings/industry calls replace_watchlist_with_industry which writes the new symbols atomically.",
      "Backfills run in background for the new set.",
    ],
    alt: [
      "Industry unknown — request returns 400.",
    ],
    acceptance: [
      "Watchlist update is atomic (single transaction)."
    ],
  }),
];

// ---------- 13. Authentication & Onboarding ----------
const USR = [
  h1("13.  Authentication and Onboarding (FR-U)"),
  ...feature({
    id: "FR-U-01",
    title: "Sign up and sign in via Supabase",
    priority: "Must",
    actors: "Visitor",
    purpose: "Authenticate users without building a credential store.",
    pre: "Visitor has a valid email.",
    post: "Supabase issues a JWT; Flask session is established.",
    steps: [
      "Visitor opens /signup or /signin.",
      "Flask form posts credentials to Supabase.",
      "Supabase returns user metadata and a JWT.",
      "Flask sets a server-side session keyed by user_id.",
    ],
    alt: [
      "Invalid credentials — Flask flashes a neutral error.",
      "Supabase outage — login page shows a 'service unavailable' message.",
    ],
    acceptance: [
      "No raw passwords ever stored locally.",
      "Sign-in to dashboard round trip ≤ 2 s at p95.",
    ],
  }),
  ...feature({
    id: "FR-U-02",
    title: "Onboarding capture (industry, company, designation)",
    priority: "Must",
    actors: "New user",
    purpose: "Bootstrap the watchlist and personalise the dashboard for a new user.",
    pre: "User has just signed up and not completed onboarding.",
    post: "Supabase user metadata updated; default watchlist seeded; user redirected to checkout.",
    steps: [
      "User completes the /onboarding form.",
      "System writes industry, company and designation into Supabase metadata.",
      "System calls replace_watchlist_with_industry for the chosen industry.",
      "Backfills queued; user redirected to /checkout.",
    ],
    alt: [
      "User skips industry — system blocks submission with a field error.",
    ],
    acceptance: [
      "Onboarding can be completed in a single page submission.",
    ],
  }),
];

// ---------- 14. Payments & Access ----------
const PAY = [
  h1("14.  Payments and Access (FR-PY)"),
  ...feature({
    id: "FR-PY-01",
    title: "Razorpay order creation and signature verification",
    priority: "Must",
    actors: "Authenticated user (post-onboarding)",
    purpose: "Charge the ₹4,999 annual access fee.",
    pre: "User is on /checkout and Razorpay is configured.",
    post: "Verified payment is recorded; user is marked paid.",
    steps: [
      "Frontend calls POST /api/payment/create-order; backend issues a Razorpay order for 499900 paise.",
      "User completes the Razorpay checkout in the browser.",
      "Frontend posts the payment_id + signature to /api/payment/verify.",
      "Backend verifies the HMAC-SHA256 signature against the Razorpay secret.",
      "On success, payments.mark_paid writes user_payments and updates Supabase metadata."
    ],
    alt: [
      "Signature mismatch — backend rejects with 400; nothing persisted.",
      "User cancels — checkout page remains; nothing persisted.",
    ],
    acceptance: [
      "User cannot be marked paid without a verified signature.",
      "Marking paid is idempotent on payment_id."
    ],
  }),
  ...feature({
    id: "FR-PY-02",
    title: "Referral code redemption",
    priority: "Must",
    actors: "Authenticated user (post-onboarding)",
    purpose: "Allow invited users to gain access without payment.",
    pre: "User has a valid referral code.",
    post: "user_payments row written with method='referral'; user marked paid.",
    steps: [
      "User types the code on /checkout.",
      "POST /api/payment/redeem-referral validates the code and the user's eligibility.",
      "On success, the system writes a referral redemption row and marks the user paid.",
    ],
    alt: [
      "Code unknown or exhausted — UI shows a neutral error.",
      "User already redeemed — UI shows 'already redeemed' and proceeds.",
    ],
    acceptance: [
      "A single user cannot redeem the same code twice."
    ],
  }),
  ...feature({
    id: "FR-PY-03",
    title: "Access gating across all paid endpoints",
    priority: "Must",
    actors: "System",
    purpose: "Block all paid features for unpaid users.",
    pre: "User is signed in.",
    post: "Unpaid users see /checkout; paid users proceed.",
    steps: [
      "Flask wraps paid routes with require_paid decorator.",
      "Decorator checks the user's paid flag (cached in session, falls back to Supabase admin API).",
      "Unpaid users are redirected to /checkout.",
    ],
    alt: [
      "Session cache stale — decorator re-reads from Supabase admin API.",
    ],
    acceptance: [
      "No /api/* paid route can be invoked without the paid flag.",
      "Logout always succeeds regardless of paid state.",
    ],
  }),
];

// ---------- 15. Experience & Theme ----------
const UX = [
  h1("15.  Experience, Theme and Motion (FR-UX)"),
  ...feature({
    id: "FR-UX-01",
    title: "Theme toggle with localStorage persistence",
    priority: "Must",
    actors: "Any user",
    purpose: "Let the user switch between light and dark themes and have the choice remembered.",
    pre: "User loads any page with the theme toggle.",
    post: "HTML root carries 'dark' class (or not); preference stored in localStorage.",
    steps: [
      "Init script (in analytics.html) reads the stored preference and toggles the class on <html>.",
      "User clicks the toggle in the page header.",
      "Script flips the class and persists the new preference.",
    ],
    alt: [
      "localStorage unavailable — preference defaults to light, toggle still works for the session.",
    ],
    acceptance: [
      "First paint matches the stored preference; no flash of incorrect theme.",
    ],
  }),
  ...feature({
    id: "FR-UX-02",
    title: "Dark theme palette and surface overrides",
    priority: "Must",
    actors: "System",
    purpose: "Render every primary surface, card, badge, tag, modal, input and scrollbar legibly in dark mode.",
    pre: "html.dark is active.",
    post: "All Tailwind utility classes (bg-white, bg-slate-*, text-gray-*, border-gray-*, ring-gray-*, divide-gray-*) are overridden to the dark palette.",
    steps: [
      "styles.css declares the dark CSS variables and the html.dark utility overrides.",
      "Tag and badge classes (.tag-*, .company-badge-*, .company-bg-*) have dedicated dark rules with tinted backgrounds and bright readable text.",
      "Decorative gradients (e.g. Chemicals Pulse) carry dark overrides that preserve identity while remaining legible.",
      "Modal, diff, scrollbar and spinner styles all carry dark variants.",
    ],
    alt: [
      "A new template introduces an un-overridden utility — design review catches it; fix lands in styles.css.",
    ],
    acceptance: [
      "Every primary route passes a dark-mode contrast audit at AA on textual content.",
      "No element renders with a bright white background in dark mode.",
    ],
  }),
  ...feature({
    id: "FR-UX-03",
    title: "Animation pass — entrance, hover, theme transition",
    priority: "Should",
    actors: "System",
    purpose: "Make the application feel smooth and intentional without being distracting.",
    pre: "User is on any page; prefers-reduced-motion not set.",
    post: "Cards fade in and rise on load; hover states animate; theme toggle is smooth.",
    steps: [
      "Cards apply the fadeInUp keyframe with a 0.4 s cubic-bezier easing on initial load.",
      "Feature cards stagger their entrance by ~40 ms.",
      "Cards, buttons, tags and nav items have 0.25 s colour and transform transitions.",
      "Primary buttons gain a gradient + glow in dark mode and lift on hover.",
      "Theme toggle icons rotate gently on hover.",
    ],
    alt: [
      "User has prefers-reduced-motion: reduce — all animations collapse to ~0.01 ms via the media-query guard.",
    ],
    acceptance: [
      "No animation drops frames on a mid-tier laptop.",
      "Reduced-motion users see no motion."
    ],
  }),
  ...feature({
    id: "FR-UX-04",
    title: "Inputs, focus ring and accessibility",
    priority: "Must",
    actors: "Any user",
    purpose: "Keep interactive elements discoverable and keyboard-friendly in both themes.",
    pre: "User is on a page with form elements.",
    post: "Inputs have visible focus rings; keyboard users can traverse all controls.",
    steps: [
      "Inputs, textareas and selects use the dark surface with a 1px border in dark mode.",
      "Focused fields get a 3px indigo ring at 18% opacity.",
      ":focus-visible applies a 2px indigo outline to buttons and links.",
    ],
    alt: [
      "User has high-contrast preferences — focus indicators retain visibility under forced colours.",
    ],
    acceptance: [
      "All primary flows can be completed with the keyboard alone.",
      "Focus indicator is visible against every supported surface colour.",
    ],
  }),
];

// ---------- 16. NFR cross-reference ----------
const NFR = [
  h1("16.  Non-Functional Cross-Reference"),
  table(
    ["Non-Functional Area", "Applicable FRs"],
    [
      ["Performance", "FR-D-01, FR-A-02, FR-DR-03, FR-DR-06, FR-G-03, FR-AL-02"],
      ["Availability", "FR-A-01, FR-AL-02, FR-M-02"],
      ["Security", "FR-U-01, FR-PY-01, FR-PY-03, FR-AL-03, FR-DR-07"],
      ["Privacy", "FR-U-01, FR-AL-03, FR-DR-07"],
      ["Accessibility", "FR-UX-01 — FR-UX-04 and all user-facing flows"],
      ["Observability", "FR-A-01, FR-AL-02, FR-M-03, FR-PY-01"],
      ["Maintainability", "FR-UX-02 (centralised theme overrides)"],
    ],
    [3400, 5960],
  ),
];

// ---------- Doc assembly ----------
const doc = new Document({
  creator: "Delta Filings Product Team",
  title: "Delta Filings — Functional Requirements Document v3",
  description: "FRD v3 for the Delta Filings platform as it ships on Railway, May 2026.",
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
          { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
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
            new TextRun({ text: "Delta Filings  —  Functional Requirements Document", color: BRAND, bold: true, size: 18 }),
            new TextRun({ text: "\t" }),
            new TextRun({ text: "v3.0  |  May 2026", color: TEXT_MUTED, size: 18 }),
          ],
        })]}),
      },
      footers: {
        default: new Footer({ children: [ new Paragraph({
          tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
          children: [
            new TextRun({ text: "Confidential — For internal circulation only", italics: true, size: 18, color: TEXT_MUTED }),
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
        ...DIFF,
        ...DRAFT,
        ...DIN,
        ...GOV,
        ...COMP,
        ...PRC,
        ...ALERT,
        ...IND,
        ...WATCH,
        ...USR,
        ...PAY,
        ...UX,
        ...NFR,
      ],
    },
  ],
});

const OUT = path.join(
  "/Users/prakharsingh/Documents/Study Files/python things/delta_filings",
  "Delta_Filings_FRD_v3.docx",
);
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log(`Wrote ${OUT} (${buf.length} bytes)`);
});
