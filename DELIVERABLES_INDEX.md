# Delta Filings — Documentation Index (v2)

Generated April 24, 2026. All documents live in this folder alongside the v1 code.

## Read-me-first

| For | Open this |
|---|---|
| Exec / investor audience | `Delta_Filings_ExecSummary_v2.pdf` (one page) |
| Pitching the product | `Delta_Filings_Pitch_v2.pptx` / `.pdf` (11 slides) |
| Stakeholder alignment on scope | `Delta_Filings_BRD.pdf` |
| Engineering implementation | `Delta_Filings_FRD_v2.pdf` + `Delta_Filings_TAD_v2.pdf` |
| QA sign-off & release gates | `Delta_Filings_TestPlan_v2.pdf` |
| Local runbook & troubleshooting | `../delta_filings_grant/README.md` |

## All documents

| File | Size | Purpose |
|---|---|---|
| `Delta_Filings_BRD.docx` / `.pdf` | ~24 KB / 300 KB | Business Requirements Document — 16 sections, KPIs, scope, personas, requirements, risks, roadmap, glossary. |
| `Delta_Filings_FRD_v2.docx` / `.pdf` | ~19 KB / 280 KB | Functional Requirements — feature blocks with purpose, actors, main/alt flow, acceptance criteria. Traces to BRD. |
| `Delta_Filings_TAD_v2.docx` / `.pdf` | ~18 KB / 167 KB | Technical Architecture — components, data model, API surface, ingestion pipeline, security, observability, trade-offs. |
| `Delta_Filings_TestPlan_v2.docx` / `.pdf` | ~15 KB / 121 KB | Test levels, environments, entry/exit gates, sample test cases, defect policy, tooling, schedule. |
| `Delta_Filings_ExecSummary_v2.docx` / `.pdf` | ~10 KB / 50 KB | One-page brief for board/exec/investor rooms. |
| `Delta_Filings_Pitch_v2.pptx` / `.pdf` | 243 KB / 113 KB | 11-slide deck: Title, Problem, Solution, Market, Product, Architecture, Traction, Roadmap, Business Model, Ask, Thanks. |

## Generator scripts (re-run after edits)

| Script | Regenerates |
|---|---|
| `create_brd_v2.js` | Delta_Filings_BRD.docx |
| `create_frd_v2.js` | Delta_Filings_FRD_v2.docx |
| `create_tad_v2.js` | Delta_Filings_TAD_v2.docx |
| `create_testplan_v2.js` | Delta_Filings_TestPlan_v2.docx |
| `create_execsummary.js` | Delta_Filings_ExecSummary_v2.docx |
| `create_pitch.js` | Delta_Filings_Pitch_v2.pptx |

Run any with `node <script>.js` from this folder.

## Regenerate PDFs after editing .docx / .pptx

```bash
/Applications/LibreOffice.app/Contents/MacOS/soffice \
  --headless --convert-to pdf \
  --outdir "/Users/prakharsingh/Documents/Study Files/python things/delta_filings" \
  Delta_Filings_*.docx Delta_Filings_*.pptx
```

## When opening .docx files in Word

Press **F9** (or right-click the Table of Contents → *Update Field*) to populate the TOC on first open. The .pdf versions already have the TOC rendered.

## v2 stack (separate folder)

The v2 codebase lives at `../delta_filings_grant/`. It boots via:

```bash
cd ../delta_filings_grant
docker compose up --build
open http://localhost:3000
```

See `../delta_filings_grant/README.md` for the full runbook and troubleshooting table.
