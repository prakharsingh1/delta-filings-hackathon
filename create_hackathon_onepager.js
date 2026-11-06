/**
 * Delta Filings — GIFT IFSC Young Builders Hackathon, Round 1 one-slide pitch.
 * Output: Delta_Filings_Hackathon_OnePager.pptx
 */
const path = require("path");
const pptxgen = require("/opt/homebrew/lib/node_modules/pptxgenjs");

const OUT = path.join(
  "/Users/prakharsingh/Documents/Study Files/python things/delta_filings",
  "Delta_Filings_Hackathon_OnePager.pptx",
);

// Palette — ink navy dominant, signal green accent, amber for regulatory risk.
const BG      = "0A1020";
const CARD    = "141D33";
const CARD_HI = "16273F";
const ACCENT  = "3DDC97";
const AMBER   = "F5B14A";
const WHITE   = "FFFFFF";
const BODY    = "C7D3E4";
const MUTED   = "8194AF";
const F        = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.title = "Delta Filings — Young Builders Hackathon One-Pager";
pres.author = "Prakhar Singh";

const s = pres.addSlide();
s.background = { color: BG };

const card = (x, y, w, h, fill) =>
  s.addShape("roundRect", {
    x, y, w, h, rectRadius: 0.06,
    fill: { color: fill || CARD },
    line: { color: "22304C", width: 0.75 },
  });

// numbered section header inside a card
const cardHead = (n, title, x, y, tw, ts) => {
  s.addShape("ellipse", {
    x: x + 0.18, y: y + 0.16, w: 0.3, h: 0.3,
    fill: { color: ACCENT }, line: { color: ACCENT, width: 0 },
  });
  s.addText(String(n), {
    x: x + 0.18, y: y + 0.16, w: 0.3, h: 0.3,
    fontSize: 12, bold: true, color: "0A1020", fontFace: F,
    align: "center", valign: "middle", margin: 0,
  });
  s.addText(title, {
    x: x + 0.56, y: y + 0.14, w: tw || 3.2, h: 0.34,
    fontSize: ts || 15, bold: true, color: WHITE, fontFace: F, valign: "middle", margin: 0,
  });
};

const bullets = (items, x, y, w, h, size) =>
  s.addText(
    items.map((t, i) => ({
      text: t,
      options: { bullet: { code: "2022", indent: 12 }, breakLine: i !== items.length - 1 },
    })),
    {
      x, y, w, h, fontSize: size || 9.5, color: BODY, fontFace: F,
      lineSpacingMultiple: 0.94, paraSpaceAfter: 4, margin: 0, valign: "top",
    },
  );

const kicker = (text, x, y, w) =>
  s.addText(text, {
    x, y, w, h: 0.24, fontSize: 9.5, bold: true, italic: true,
    color: ACCENT, fontFace: F, margin: 0, valign: "middle",
  });

/* ------------------------------------------------ header */
s.addShape("roundRect", {
  x: 0.45, y: 0.36, w: 0.78, h: 0.78, rectRadius: 0.14,
  fill: { color: ACCENT }, line: { color: ACCENT, width: 0 },
});
s.addText("Δ", {
  x: 0.45, y: 0.36, w: 0.78, h: 0.78,
  fontSize: 40, bold: true, color: "0A1020", fontFace: F,
  align: "center", valign: "middle", margin: 0,
});
s.addText("Delta Filings", {
  x: 1.36, y: 0.34, w: 6.4, h: 0.5,
  fontSize: 30, bold: true, color: WHITE, fontFace: F, valign: "middle", margin: 0,
});
s.addText("AI governance intelligence infrastructure for India's listed companies.", {
  x: 1.38, y: 0.84, w: 6.6, h: 0.3,
  fontSize: 12.5, color: MUTED, fontFace: F, valign: "middle", margin: 0,
});

s.addText("TEAM DELTA FILINGS   ·   SOLO BUILDER: PRAKHAR SINGH", {
  x: 7.9, y: 0.34, w: 4.98, h: 0.26,
  fontSize: 9.5, bold: true, color: ACCENT, fontFace: F,
  align: "right", charSpacing: 0.8, valign: "middle", margin: 0,
});
s.addText("Track: AI in Governance, Risk & Compliance   ·   BFSI: Capital Markets / RegTech", {
  x: 7.4, y: 0.60, w: 5.48, h: 0.24,
  fontSize: 9.5, color: BODY, fontFace: F, align: "right", valign: "middle", margin: 0,
});
s.addText("Focus: SEBI LODR & MCA compliance for listed issuers", {
  x: 7.4, y: 0.82, w: 5.48, h: 0.24,
  fontSize: 9.5, color: BODY, fontFace: F, align: "right", valign: "middle", margin: 0,
});
s.addText(
  [
    { text: "Stage: ", options: { color: MUTED } },
    { text: "■ MVP — live prototype", options: { color: AMBER, bold: true } },
    { text: "     Commitment: ", options: { color: MUTED } },
    { text: "■ Full-time", options: { color: AMBER, bold: true } },
  ],
  { x: 7.4, y: 1.04, w: 5.48, h: 0.24, fontSize: 9.5, fontFace: F, align: "right", valign: "middle", margin: 0 },
);

/* ------------------------------------------------ row A */
const AY = 1.44, AH = 2.36, CW = 3.98;
const AX = [0.45, 4.68, 8.91];

card(AX[0], AY, CW, AH);
cardHead(1, "Problem", AX[0], AY);
bullets([
  "Who: Company Secretaries and compliance teams at 5,000+ NSE/BSE listed issuers",
  "Today: thousands of filings a quarter read by hand, deadlines tracked in Excel, ad-hoc legal opinions",
  "One missed LODR obligation \u2192 penalty, exchange notice and board-level exposure",
  "And it repeats: every issuer, every quarter, every regulation \u2014 no opt-out",
], AX[0] + 0.22, AY + 0.58, CW - 0.44, 1.42);
kicker("Access to filings is free. Interpretation isn't.", AX[0] + 0.22, AY + AH - 0.34, CW - 0.44);

card(AX[1], AY, CW, AH);
cardHead(2, "Solution", AX[1], AY);
bullets([
  "Three AI agents run the CS workflow: Monitor detects \u2192 Intelligence assesses \u2192 Action drafts",
  "Board, director, committee and corporate-action changes mapped to SEBI LODR / MCA obligations and deadlines",
  "Ships the disclosure draft, board note and checklist \u2014 a human approves and e-signs",
  "Why now: SEBI\u2019s Integrated Filing regime raises the load, and grounded LLMs are finally accurate enough to reason over it",
], AX[1] + 0.22, AY + 0.58, CW - 0.44, 1.42);
kicker("Delta Signal: what changed \u00b7 why it matters \u00b7 what to do next.", AX[1] + 0.22, AY + AH - 0.34, CW - 0.44);

card(AX[2], AY, CW, AH);
cardHead(3, "Validation", AX[2], AY);
bullets([
  "Practising Company Secretaries: the work is interpretation and drafting, not search",
  "Repeat ask \u2014 \u201ctell me what changed since the last report, and give me the draft\u201d",
  "Built from the regulation: 305-obligation LODR/MCA library, 86 SEBI enforcement actions parsed",
  "Ran live on 5 large-cap issuers \u2014 flagged 25 filings past their computed due date, up to 30 days late",
], AX[2] + 0.22, AY + 0.58, CW - 0.44, 1.42);
kicker("Live on real NSE/BSE data \u2014 full demo in Round 2.", AX[2] + 0.22, AY + AH - 0.34, CW - 0.44);

/* ------------------------------------------------ row B */
const BY = 3.90, BH = 1.84;

card(0.45, BY, 6.60, BH);
cardHead(4, "Regulatory Requirements (Domestic / IFSC)", 0.45, BY, 5.9, 14);
bullets([
  "In scope: SEBI (LODR, PIT, SAST) \u00b7 MCA / ROC \u00b7 NSE & BSE listing ops \u00b7 IFSCA for IFSC-listed issuers and GIFT-domiciled funds",
  "Delta is decision support \u2014 it drafts and evidences; an authorised signatory approves and files. No regulated activity on the issuer's behalf.",
  "Risk & mitigation: deterministic rules gate detection, every model output quotes its source filing, human sign-off is mandatory, public data only",
], 0.67, BY + 0.56, 6.16, 0.94, 9);
kicker("Compliance-grade by construction: evidence \u2192 approval \u2192 immutable audit log.", 0.67, BY + BH - 0.34, 6.16);

card(7.30, BY, 5.58, BH);
cardHead(5, "Team\u2013Market Fit", 7.30, BY, 4.9, 14);
bullets([
  "Prakhar Singh \u2014 solo builder: ingestion, multi-agent AI, compliance engine, drafting and e-sign, all shipped by one person",
  "Domain learned at the source: 6,276 real filings parsed, obligation model written from the regulation text itself",
  "Biggest achievement: a live end-to-end governance workflow \u2014 detect \u2192 assess \u2192 draft \u2192 approve \u2192 audit",
], 7.52, BY + 0.56, 5.14, 0.94, 9);
kicker("Full-stack + AI + regtech in one builder = ship speed.", 7.52, BY + BH - 0.34, 5.14);

/* ------------------------------------------------ live metrics band */
const MY = 5.84, MH = 0.70;
card(0.45, MY, 12.43, MH, CARD_HI);
s.addText("RUNNING\nTODAY", {
  x: 0.62, y: MY + 0.08, w: 1.0, h: MH - 0.16,
  fontSize: 9, bold: true, color: ACCENT, fontFace: F,
  charSpacing: 0.6, lineSpacingMultiple: 0.9, valign: "middle", margin: 0,
});
const stats = [
  ["6,276", "filings parsed"],
  ["305", "LODR / MCA obligations tracked"],
  ["53", "governance events detected"],
  ["124", "action documents generated"],
  ["100%", "evidence-linked & audit-logged"],
];
stats.forEach(([n, label], i) => {
  const x = 1.78 + i * 2.22;
  s.addText(n, {
    x, y: MY + 0.09, w: 2.1, h: 0.34,
    fontSize: 21, bold: true, color: WHITE, fontFace: F, valign: "middle", margin: 0,
  });
  s.addText(label, {
    x, y: MY + 0.43, w: 2.1, h: 0.26,
    fontSize: 8.5, color: MUTED, fontFace: F, valign: "middle", margin: 0,
  });
});

/* ------------------------------------------------ why GIFT IFIH */
const WY = 6.62;
card(0.45, WY, 12.43, 0.64, CARD);
s.addText("Why GIFT IFIH?", {
  x: 0.67, y: WY + 0.03, w: 2.0, h: 0.58,
  fontSize: 13, bold: true, color: ACCENT, fontFace: F, valign: "middle", margin: 0,
});
s.addText(
  "Pilots with listed issuers and IFSC entities, regulatory guidance from SEBI/IFSCA-facing mentors to harden the compliance engine, and partnerships with CS firms, registrars and exchanges — building India's governance intelligence layer inside the ecosystem that sets the standard.",
  { x: 2.62, y: WY + 0.03, w: 10.05, h: 0.58, fontSize: 10, color: BODY, fontFace: F, valign: "middle", margin: 0 },
);

s.addNotes(
  "30-second story: Listed companies do not have an information problem, they have an interpretation problem. " +
  "A Company Secretary at any of India's 5,000+ listed issuers reads every NSE/BSE filing by hand and then has to answer three questions: what changed, does SEBI LODR require me to act, and what do I file. " +
  "Delta Filings runs that workflow with three AI agents - one detects the governance change inside the filing, one maps it to the LODR/MCA obligation and deadline, one drafts the disclosure, board note and checklist for a human to approve and e-sign. " +
  "Everything is quoted from the source filing and written to an audit log, so it is compliance-grade, not a chatbot. " +
  "It is live today: 6,276 filings parsed, 305 obligations tracked, 53 governance events detected, 124 action documents generated. Full demo in Round 2."
);

pres.writeFile({ fileName: OUT }).then(() => console.log("wrote", OUT));
