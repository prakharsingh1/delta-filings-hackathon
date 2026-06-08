/**
 * Delta Filings — Investor / Stakeholder Pitch Deck.
 * Output: Delta_Filings_Pitch_v2.pptx
 */
const path = require("path");
const pptxgen = require("/opt/homebrew/lib/node_modules/pptxgenjs");

const OUT = path.join(
  "/Users/prakharsingh/Documents/Study Files/python things/delta_filings",
  "Delta_Filings_Pitch_v2.pptx",
);

const BRAND = "1F4E79";
const BRAND_LIGHT = "D5E8F0";
const INK = "0F172A";
const MUTED = "64748B";
const ACCENT = "E85D75";
const WHITE = "FFFFFF";
const LIGHT_BG = "F8FAFC";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5 inches
pres.title = "Delta Filings — Investor Pitch (v2)";
pres.author = "Delta Filings Product Team";
pres.company = "Delta Filings";

// Master slides for consistent chrome
pres.defineSlideMaster({
  title: "STD",
  background: { color: WHITE },
  objects: [
    { rect: { x: 0, y: 7.15, w: 13.333, h: 0.35, fill: { color: BRAND } } },
    { text: { text: "\u0394  Delta Filings  \u2014  Confidential", options: {
      x: 0.4, y: 7.18, w: 6, h: 0.3, fontSize: 10, color: WHITE, fontFace: "Calibri",
    } } },
    { text: { text: "delta-filings.com", options: {
      x: 10.5, y: 7.18, w: 2.5, h: 0.3, fontSize: 10, color: WHITE, fontFace: "Calibri", align: "right",
    } } },
  ],
});

const addTitle = (slide, text, sub) => {
  slide.addText(text, {
    x: 0.6, y: 0.4, w: 12.1, h: 0.9,
    fontSize: 32, bold: true, color: BRAND, fontFace: "Calibri",
  });
  if (sub) {
    slide.addText(sub, {
      x: 0.6, y: 1.2, w: 12.1, h: 0.5,
      fontSize: 16, color: MUTED, fontFace: "Calibri", italic: true,
    });
  }
  slide.addShape("line", { x: 0.6, y: 1.75, w: 12.1, h: 0, line: { color: BRAND, width: 1.5 } });
};

// ---- Slide 1: Title ----
{
  const s = pres.addSlide("STD");
  s.background = { color: BRAND };
  s.addText("\u0394", {
    x: 0.6, y: 1.0, w: 3, h: 2.5, fontSize: 180, bold: true, color: WHITE, fontFace: "Calibri",
  });
  s.addText("Delta Filings", {
    x: 3.6, y: 1.4, w: 9.2, h: 1.2,
    fontSize: 60, bold: true, color: WHITE, fontFace: "Calibri",
  });
  s.addText("Corporate Governance Intelligence for Indian capital markets.", {
    x: 3.6, y: 2.6, w: 9.2, h: 0.8,
    fontSize: 22, color: BRAND_LIGHT, fontFace: "Calibri", italic: true,
  });
  s.addText("Investor Briefing  \u2022  v2.0  \u2022  April 2026", {
    x: 3.6, y: 5.5, w: 9.2, h: 0.4,
    fontSize: 16, color: WHITE, fontFace: "Calibri",
  });
  s.addText("Prepared by the Delta Filings Product Team", {
    x: 3.6, y: 6.0, w: 9.2, h: 0.4,
    fontSize: 13, color: BRAND_LIGHT, fontFace: "Calibri",
  });
}

// ---- Slide 2: Problem ----
{
  const s = pres.addSlide("STD");
  addTitle(s, "The problem", "Data that matters is scattered, unstructured, and slow.");
  const pts = [
    { k: "3", l: "portals", d: "BSE, NSE, MCA \u2014 three different UIs, three different data shapes." },
    { k: "1,000s", l: "weekly disclosures", d: "Manually tracked across PDFs and inconsistent tables." },
    { k: "60%", l: "analyst time wasted", d: "On collection instead of analysis. The real work is downstream." },
    { k: "0", l: "structured director graph", d: "No way to ask 'which boards share directors?' in under a minute." },
  ];
  pts.forEach((p, i) => {
    const x = 0.6 + (i % 2) * 6.1;
    const y = 2.1 + Math.floor(i / 2) * 2.4;
    s.addShape("roundRect", { x, y, w: 6, h: 2.2, fill: { color: LIGHT_BG }, line: { color: BRAND_LIGHT, width: 1 }, rectRadius: 0.1 });
    s.addText(p.k, { x: x + 0.25, y: y + 0.2, w: 2.3, h: 1.1, fontSize: 52, bold: true, color: BRAND, fontFace: "Calibri" });
    s.addText(p.l, { x: x + 0.25, y: y + 1.3, w: 2.3, h: 0.5, fontSize: 14, color: MUTED, fontFace: "Calibri" });
    s.addText(p.d, { x: x + 2.6, y: y + 0.3, w: 3.3, h: 1.7, fontSize: 14, color: INK, fontFace: "Calibri" });
  });
}

// ---- Slide 3: Solution ----
{
  const s = pres.addSlide("STD");
  addTitle(s, "Our solution", "One surface for filings, directors and compliance.");
  s.addText("Delta Filings ingests statutory disclosures in real time, summarises them with AI, enriches them with structured director data from MCA, and exposes everything through watchlists, search and compliance workflows.", {
    x: 0.6, y: 2.0, w: 12.1, h: 1.2, fontSize: 17, color: INK, fontFace: "Calibri",
  });

  const pillars = [
    { h: "Real-time filings", d: "BSE + NSE, under 15 min lag." },
    { h: "Director graph", d: "DIN lookup + Compare for up to three." },
    { h: "Compliance", d: "Obligations, runs, audit-ready PDFs." },
    { h: "Alerts", d: "Instant, daily or weekly digest." },
  ];
  pillars.forEach((p, i) => {
    const x = 0.6 + i * 3.05;
    const y = 3.7;
    s.addShape("roundRect", { x, y, w: 2.9, h: 2.6, fill: { color: BRAND }, line: { color: BRAND, width: 0 }, rectRadius: 0.12 });
    s.addText(p.h, { x: x + 0.2, y: y + 0.3, w: 2.5, h: 0.8, fontSize: 18, bold: true, color: WHITE, fontFace: "Calibri" });
    s.addText(p.d, { x: x + 0.2, y: y + 1.3, w: 2.5, h: 1.1, fontSize: 13, color: BRAND_LIGHT, fontFace: "Calibri" });
  });
}

// ---- Slide 4: Market ----
{
  const s = pres.addSlide("STD");
  addTitle(s, "Market", "A large, under-served category in Indian capital markets.");
  const rows = [
    ["Segment", "Approx. Audience (India)", "Who they are"],
    ["Retail investors (active)", "~40 M demat accounts", "Tracking portfolios, reacting to news."],
    ["Equity research analysts", "~8,000", "Sell-side and buy-side, desk coverage."],
    ["Compliance officers", "~5,000 (listed-company CS/compliance)", "LODR, Companies Act, SEBI."],
    ["Legal / audit", "~30,000 professionals", "Diligence, forensic, audit."],
  ];
  s.addTable(rows, {
    x: 0.6, y: 2.2, w: 12.1, fontSize: 14, color: INK, fontFace: "Calibri",
    border: { type: "solid", color: "DDDDDD", pt: 0.5 },
    colW: [3.5, 4.0, 4.6],
    rowH: 0.55,
    fill: { color: LIGHT_BG },
  });
  s.addText("Note: figures are directional estimates \u2014 final sizing to be refined ahead of pricing.", {
    x: 0.6, y: 6.4, w: 12.1, h: 0.4, fontSize: 12, italic: true, color: MUTED, fontFace: "Calibri",
  });
}

// ---- Slide 5: Product demo ----
{
  const s = pres.addSlide("STD");
  addTitle(s, "What it looks like", "Five surfaces, one workflow.");
  const cards = [
    { t: "Dashboard", d: "Latest 50 filings across your watchlists, summarised and sentiment-tagged." },
    { t: "Announcements", d: "Filterable feed with filing detail, source PDF and related filings." },
    { t: "DIN Search", d: "Sub-second lookup with curated snapshot fallback." },
    { t: "Compare", d: "Side-by-side view of up to three directors with common-board highlights." },
    { t: "Compliance", d: "Obligations \u2192 runs \u2192 evidence \u2192 audit-ready PDFs." },
  ];
  cards.forEach((c, i) => {
    const col = i % 3;
    const row = Math.floor(i / 3);
    const x = 0.6 + col * 4.1;
    const y = 2.1 + row * 2.5;
    s.addShape("roundRect", { x, y, w: 3.9, h: 2.3, fill: { color: LIGHT_BG }, line: { color: BRAND_LIGHT, width: 1 }, rectRadius: 0.08 });
    s.addText(c.t, { x: x + 0.2, y: y + 0.2, w: 3.6, h: 0.6, fontSize: 20, bold: true, color: BRAND, fontFace: "Calibri" });
    s.addText(c.d, { x: x + 0.2, y: y + 0.9, w: 3.6, h: 1.3, fontSize: 13, color: INK, fontFace: "Calibri" });
  });
}

// ---- Slide 6: Architecture ----
{
  const s = pres.addSlide("STD");
  addTitle(s, "Under the hood", "Containerised, horizontally scalable, production-grade.");
  const stack = [
    { k: "Frontend", v: "Next.js 15 \u00b7 React 18 \u00b7 Tailwind" },
    { k: "Backend", v: "FastAPI \u00b7 Python 3.12 \u00b7 async SQLAlchemy" },
    { k: "Database", v: "PostgreSQL 16" },
    { k: "Cache / Queue", v: "Redis 7 (streams for jobs)" },
    { k: "Auth", v: "Supabase (email + Google)" },
    { k: "AI", v: "OpenAI (summarisation + classification)" },
    { k: "Deploy", v: "Docker Compose (dev) \u00b7 container platform (prod)" },
  ];
  stack.forEach((r, i) => {
    const y = 2.1 + i * 0.65;
    s.addShape("rect", { x: 0.6, y, w: 3.0, h: 0.55, fill: { color: BRAND } });
    s.addText(r.k, { x: 0.75, y, w: 2.85, h: 0.55, fontSize: 15, bold: true, color: WHITE, fontFace: "Calibri", valign: "middle" });
    s.addShape("rect", { x: 3.6, y, w: 9.1, h: 0.55, fill: { color: LIGHT_BG }, line: { color: BRAND_LIGHT, width: 0.5 } });
    s.addText(r.v, { x: 3.8, y, w: 8.9, h: 0.55, fontSize: 14, color: INK, fontFace: "Calibri", valign: "middle" });
  });
}

// ---- Slide 7: Traction ----
{
  const s = pres.addSlide("STD");
  addTitle(s, "Traction to date (v1)", "A working prototype that proved demand.");
  const stats = [
    { k: "1,200+", l: "sign-ups on v1" },
    { k: "45k", l: "DIN lookups served" },
    { k: "~ 300", l: "companies watchlisted" },
    { k: "4.6 / 5", l: "pilot satisfaction score" },
  ];
  stats.forEach((p, i) => {
    const x = 0.6 + i * 3.05;
    const y = 2.3;
    s.addShape("roundRect", { x, y, w: 2.9, h: 2.8, fill: { color: BRAND_LIGHT }, line: { color: BRAND, width: 1 }, rectRadius: 0.12 });
    s.addText(p.k, { x, y: y + 0.4, w: 2.9, h: 1.4, fontSize: 52, bold: true, color: BRAND, fontFace: "Calibri", align: "center" });
    s.addText(p.l, { x, y: y + 2.0, w: 2.9, h: 0.6, fontSize: 14, color: INK, fontFace: "Calibri", align: "center" });
  });
  s.addText("v1 ran as a single-process Flask app. v2 is built for scale \u2014 the next numbers are ahead, not behind us.", {
    x: 0.6, y: 5.5, w: 12.1, h: 0.6, fontSize: 14, italic: true, color: MUTED, fontFace: "Calibri", align: "center",
  });
}

// ---- Slide 8: Roadmap ----
{
  const s = pres.addSlide("STD");
  addTitle(s, "Roadmap", "From scaffold to paid tiers in eight months.");
  const items = [
    ["M1", "May 2026", "Platform scaffold", "Stack up, auth, empty dashboard."],
    ["M2", "Jun 2026", "Filings live", "BSE/NSE ingestion, AI summaries."],
    ["M3", "Jul 2026", "Directors + DIN", "Lookup and Compare."],
    ["M4", "Aug 2026", "Compliance", "Obligations, runs, evidence."],
    ["M5", "Sep 2026", "Watchlists + alerts", "Email cadences, mute controls."],
    ["M6", "Oct 2026", "Public launch", "Free tier open to all."],
    ["M7", "Dec 2026", "Paid tiers", "Pro + Enterprise billing + SSO."],
  ];
  items.forEach((r, i) => {
    const y = 2.0 + i * 0.62;
    s.addShape("rect", { x: 0.6, y, w: 0.8, h: 0.5, fill: { color: BRAND } });
    s.addText(r[0], { x: 0.6, y, w: 0.8, h: 0.5, fontSize: 14, bold: true, color: WHITE, fontFace: "Calibri", align: "center", valign: "middle" });
    s.addText(r[1], { x: 1.6, y, w: 1.6, h: 0.5, fontSize: 13, color: MUTED, fontFace: "Calibri", valign: "middle" });
    s.addText(r[2], { x: 3.3, y, w: 3.2, h: 0.5, fontSize: 14, bold: true, color: BRAND, fontFace: "Calibri", valign: "middle" });
    s.addText(r[3], { x: 6.6, y, w: 6.1, h: 0.5, fontSize: 13, color: INK, fontFace: "Calibri", valign: "middle" });
  });
}

// ---- Slide 9: Business model ----
{
  const s = pres.addSlide("STD");
  addTitle(s, "Business model", "Freemium \u2192 Pro \u2192 Enterprise.");
  const tiers = [
    { t: "Free", p: "\u20b9 0", f: ["3 watchlists", "Daily digest", "Basic DIN search"], c: LIGHT_BG, tc: INK },
    { t: "Pro", p: "\u20b9 999 / month", f: ["Unlimited watchlists", "Instant alerts", "Compare + exports"], c: BRAND, tc: WHITE },
    { t: "Enterprise", p: "Custom", f: ["SSO + team seats", "Compliance workflows", "API access + SLA"], c: INK, tc: WHITE },
  ];
  tiers.forEach((tier, i) => {
    const x = 0.6 + i * 4.1;
    const y = 2.2;
    s.addShape("roundRect", { x, y, w: 3.9, h: 4.3, fill: { color: tier.c }, line: { color: tier.c, width: 0 }, rectRadius: 0.12 });
    s.addText(tier.t, { x: x + 0.3, y: y + 0.3, w: 3.5, h: 0.7, fontSize: 24, bold: true, color: tier.tc, fontFace: "Calibri" });
    s.addText(tier.p, { x: x + 0.3, y: y + 1.0, w: 3.5, h: 0.8, fontSize: 28, bold: true, color: tier.tc, fontFace: "Calibri" });
    tier.f.forEach((f, j) => {
      s.addText("\u2022  " + f, { x: x + 0.3, y: y + 2.0 + j * 0.5, w: 3.5, h: 0.45, fontSize: 14, color: tier.tc, fontFace: "Calibri" });
    });
  });
}

// ---- Slide 10: Ask ----
{
  const s = pres.addSlide("STD");
  addTitle(s, "The ask", "What we need from this room.");
  const asks = [
    { t: "Capital", d: "Seed round to fund v2 build-out through M7 (Dec 2026) and ramp GTM." },
    { t: "Introductions", d: "Two to five pilot customers in listed-company compliance teams." },
    { t: "Advisory", d: "Board advisor with regulatory / capital markets background." },
  ];
  asks.forEach((a, i) => {
    const y = 2.2 + i * 1.5;
    s.addShape("rect", { x: 0.6, y, w: 0.2, h: 1.3, fill: { color: ACCENT } });
    s.addText(a.t, { x: 1.0, y, w: 11.7, h: 0.5, fontSize: 22, bold: true, color: BRAND, fontFace: "Calibri" });
    s.addText(a.d, { x: 1.0, y: y + 0.6, w: 11.7, h: 0.7, fontSize: 15, color: INK, fontFace: "Calibri" });
  });
}

// ---- Slide 11: Thanks ----
{
  const s = pres.addSlide("STD");
  s.background = { color: BRAND };
  s.addText("Thank you.", {
    x: 0.6, y: 2.4, w: 12.1, h: 1.6, fontSize: 80, bold: true, color: WHITE, fontFace: "Calibri", align: "center",
  });
  s.addText("Let's build the governance layer for Indian capital markets.", {
    x: 0.6, y: 4.0, w: 12.1, h: 0.8, fontSize: 22, italic: true, color: BRAND_LIGHT, fontFace: "Calibri", align: "center",
  });
  s.addText("hello@delta-filings.com   \u2022   delta-filings.com", {
    x: 0.6, y: 5.2, w: 12.1, h: 0.5, fontSize: 16, color: WHITE, fontFace: "Calibri", align: "center",
  });
}

pres.writeFile({ fileName: OUT }).then((f) => console.log("Wrote " + f));
