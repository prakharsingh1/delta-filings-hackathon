/* Draft desk: memory, brief, fields, rewrite, preview and Check with AI.
 *
 * Loaded after the page's inline script. It replaces the inline drafting
 * functions (marked "replaced by static/draft_desk.js" there) and talks to
 * /api/drafting/*. Exports still go through /api/draft/export/*, which now
 * renders the company's own letter format and records the letter in memory.
 */
const DD = {
    memory: null, memoryFor: null,
    reqs: null, reqsFor: null,
    fields: [],
    check: null, checkStamp: null, fixed: new Set(), exportAnyway: null,
    undo: null, editorHooked: false, prefilledFor: null, questions: [],
};

// ── Small helpers ───────────────────────────────────────────────────────
function ddEsc(t) { const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; }
function ddSym() { return ((STATE.company && STATE.company.symbol) || '').toUpperCase(); }
function ddFt() { return STATE.filing_type ? STATE.filing_type.id : ''; }
function ddFact(k) { const f = ((DD.memory && DD.memory.facts) || []).find(x => x.key === k); return f ? f.value : ''; }
function ddFactSrc(k) { const f = ((DD.memory && DD.memory.facts) || []).find(x => x.key === k); return f ? f.source : ''; }
function ddFmt(k) { const f = DD.memory && DD.memory.format && DD.memory.format[k]; return f ? f.value : null; }
function ddFmtEv(k) { const f = DD.memory && DD.memory.format && DD.memory.format[k]; return f && f.of ? `${f.count} of ${f.of} letters` : ''; }
function ddLegalName() {
    const el = document.getElementById('field-company');
    return (el && el.value.trim()) || ddFact('legal_name') || ((STATE.company && STATE.company.name) || ddSym());
}
function ddReplaceFirst(text, find, repl) {
    const i = text.indexOf(find);
    return i < 0 ? null : text.slice(0, i) + repl + text.slice(i + find.length);
}
const DD_SPARK = '<svg fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="m12 3 1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4Z"/></svg>';

async function ddApi(url, opts = {}) {
    const o = {method: opts.method || 'GET', headers: {}};
    if (opts.json !== undefined) { o.method = opts.method || 'POST'; o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.json); }
    if (opts.form) { o.method = 'POST'; o.body = opts.form; }
    let r;
    try { r = await fetch(url, o); } catch (e) {
        return {error: true, kind: 'network', message: 'Could not reach Delta. Check your connection and try again.'};
    }
    let d = null;
    try { d = await r.json(); } catch (e) { d = null; }
    if (r.status === 401) return {error: true, kind: 'auth', message: 'Your session has ended. Sign in again.'};
    if (!r.ok || !d || (d.error && !d.fills)) {
        return Object.assign({error: true, kind: 'server', message: `Something went wrong on the server (${r.status}). Try again.`}, d || {});
    }
    return d;
}

function ddErrorHtml(e, what) {
    const titles = {
        quota: 'Gemini quota used up.', no_key: 'AI is not set up.', invalid_key: 'The Gemini key was rejected.',
        overloaded: 'Gemini is busy.', timeout: 'Gemini took too long.', blocked: 'Gemini declined.',
        bad_output: 'Unusable answer.', network: 'No connection.', auth: 'Signed out.',
    };
    const t = titles[e.kind] || (what ? `${what} failed.` : 'Something went wrong.');
    return `<div class="dd-note dd-note-red"><span aria-hidden="true">!</span><div><b>${ddEsc(t)}</b> ${ddEsc(e.message || '')}</div></div>`;
}

function ddBusy(btn, on, label) {
    if (!btn) return;
    if (on) { btn.dataset.ddLabel = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="dd-spin"></span> ${ddEsc(label || 'Working…')}`; }
    else { btn.disabled = false; if (btn.dataset.ddLabel) btn.innerHTML = btn.dataset.ddLabel; }
}

function ddFormatDate(d, style) {
    const day = d.getDate(), y = d.getFullYear();
    const month = d.toLocaleString('en-GB', {month: 'long'});
    const suf = (day % 100 >= 11 && day % 100 <= 13) ? 'th' : ({1: 'st', 2: 'nd', 3: 'rd'}[day % 10] || 'th');
    const p2 = n => String(n).padStart(2, '0');
    const m2 = p2(d.getMonth() + 1);
    return ({
        ordinal_comma: `${day}${suf} ${month}, ${y}`, ordinal: `${day}${suf} ${month} ${y}`,
        dmy_comma: `${day} ${month}, ${y}`, dmy: `${day} ${month} ${y}`, mdy: `${month} ${day}, ${y}`,
        numeric_dot: `${p2(day)}.${m2}.${y}`, numeric_slash: `${p2(day)}/${m2}/${y}`, numeric_dash: `${p2(day)}-${m2}-${y}`,
    })[style] || `${p2(day)} ${month}, ${y}`;
}

// ── Draft memory ────────────────────────────────────────────────────────
async function ddLoadMemory(force) {
    const sym = ddSym();
    if (!sym) return null;
    if (!force && DD.memory && DD.memoryFor === sym) { ddRenderMemory(); return DD.memory; }
    const d = await ddApi(`/api/drafting/memory?symbol=${encodeURIComponent(sym)}${STATE.draft_id ? '&draft_id=' + STATE.draft_id : ''}`);
    if (d.error) {
        const el = document.getElementById('dd-memory-summary');
        if (el) el.innerHTML = ddErrorHtml(d, 'Loading memory');
        return null;
    }
    DD.memory = d.memory; DD.memoryFor = sym;
    ddRenderMemory();
    ddPrefillDetails();
    return DD.memory;
}

function ddMemoryCounts() {
    const m = DD.memory || {};
    const docs = m.documents || [];
    const rules = ((m.team_instructions || '') + '\n' + (m.company_instructions || '')).split('\n').filter(l => l.trim()).length;
    return {letters: (m.sources || {}).letters || 0, docs: docs.length,
            approved: docs.filter(d => d.source === 'approved').length, rules,
            facts: (m.facts || []).length, missing: (m.missing_facts || []).length};
}

function ddRenderMemory() {
    const m = DD.memory;
    const btn = document.getElementById('dd-memory-btn');
    if (btn) btn.classList.toggle('hidden', !STATE.company);
    if (!m) return;
    const c = ddMemoryCounts();
    const n = document.getElementById('dd-memory-btn-n');
    if (n) n.textContent = c.letters ? `${c.letters} letters` : 'not learned';
    const co = ddEsc(ddFact('legal_name') || (STATE.company && STATE.company.name) || ddSym());
    ['dd-brief-co', 'dd-drawer-co'].forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = co; });

    let h = '';
    if (c.letters) {
        h += `<div class="flex items-center justify-between mb-1.5"><span class="dd-pill dd-pill-green"><span class="dd-dot" style="background:currentColor"></span>House style learned</span>
              <span class="dd-tiny">from ${c.letters} of its own letters</span></div>`;
        const rows = [
            ['Dates', ddFmt('date_format') ? (m.format.date_format.example || ddFmt('date_format')) : ''],
            ['Subject', ddFmt('subject_label') || ''],
            ['Closes with', ddFmt('request_line') || ''],
            ['Signed by', [ddFmt('sig_name'), ddFmt('sig_designation')].filter(Boolean).join(', ')],
        ].filter(r => r[1]);
        h += rows.map(r => `<div class="dd-mem-row"><span class="dd-mem-k">${r[0]}</span><span class="dd-mem-v">${ddEsc(r[1])}</span></div>`).join('');
    } else {
        h += `<div class="dd-note mb-2"><span aria-hidden="true">i</span><div>Delta hasn't read this company's letters yet. Learning takes a few seconds and makes every draft use its own format and phrasing.</div></div>
              <button class="dd-btn dd-btn-accent dd-btn-sm w-full" onclick="ddLearn(this)">${DD_SPARK} Learn from its exchange filings</button>`;
    }
    h += `<div class="grid grid-cols-3 gap-2 mt-3">
        <button class="text-left rounded-lg p-2" style="background:var(--dd-soft)" onclick="ddOpenMemory('rules')"><div class="dd-stat"><b>${c.rules}</b></div><div class="dd-tiny">rules</div></button>
        <button class="text-left rounded-lg p-2" style="background:var(--dd-soft)" onclick="ddOpenMemory('docs')"><div class="dd-stat"><b>${c.docs}</b></div><div class="dd-tiny">reference letters</div></button>
        <button class="text-left rounded-lg p-2" style="background:var(--dd-soft)" onclick="ddOpenMemory('facts')"><div class="dd-stat"><b>${c.facts}</b></div><div class="dd-tiny">particulars${c.missing ? ` · ${c.missing} missing` : ''}</div></button>
    </div>`;
    const keyFacts = ['cin', 'registered_office', 'bse_code', 'cs_name'].filter(k => ddFact(k));
    if (keyFacts.length) {
        h += '<div class="mt-3">' + keyFacts.map(k => {
            const f = m.facts.find(x => x.key === k);
            return `<div class="dd-mem-row"><span class="dd-mem-k">${ddEsc(f.label)}</span><span class="dd-mem-v">${ddEsc(f.value)}<span class="dd-src" title="${ddEsc(f.detail || '')}">${f.source === 'you' ? 'YOU' : f.source === 'export' ? 'EXPORT' : 'FILINGS'}</span></span></div>`;
        }).join('') + '</div>';
    }
    const sum = document.getElementById('dd-memory-summary');
    if (sum) sum.innerHTML = h;

    const desk = document.getElementById('dd-desk-memory');
    if (desk) {
        desk.innerHTML = `<p class="dd-sub mb-2">${c.letters ? `Writing in the house style learned from ${c.letters} letters` : 'House style not learned yet'}, with ${c.rules} rule${c.rules === 1 ? '' : 's'} and ${c.docs} reference letter${c.docs === 1 ? '' : 's'}.</p>
            ${(m.company_instructions || m.team_instructions) ? `<div class="rounded-lg p-2.5 dd-tiny" style="background:var(--dd-soft);white-space:pre-wrap;color:var(--dd-ink-2)">${ddEsc(((m.team_instructions || '') + '\n' + (m.company_instructions || '')).trim()).slice(0, 600)}</div>` : ''}
            <button class="dd-btn dd-btn-sm w-full mt-2" onclick="ddOpenMemory()">Open draft memory</button>`;
    }
    if (document.getElementById('dd-drawer').classList.contains('open')) ddRenderDrawer();
}

function ddPrefillDetails() {
    if (!DD.memory || DD.prefilledFor === `${ddSym()}#${STATE.draft_id || ''}`) return;
    const map = {'field-company': 'legal_name', 'field-regoffice': 'registered_office', 'field-cin': 'cin',
                 'field-bse': 'bse_code', 'field-nse': 'nse_symbol', 'field-signer': 'cs_name', 'field-designation': 'cs_designation'};
    let n = 0;
    for (const [id, key] of Object.entries(map)) {
        const el = document.getElementById(id);
        const v = ddFact(key);
        if (el && v && !el.value.trim()) { el.value = v; n++; }
    }
    const company = document.getElementById('field-company');
    if (company && !company.value.trim() && STATE.company) company.value = STATE.company.name || STATE.company.symbol;
    const src = document.getElementById('dd-details-src');
    if (src) src.textContent = n ? 'from the company profile' : '';
    DD.prefilledFor = `${ddSym()}#${STATE.draft_id || ''}`;
}

async function ddLearn(btn) {
    ddBusy(btn, true, 'Reading its letters…');
    const d = await ddApi('/api/drafting/memory/learn', {json: {symbol: ddSym(), filing_type: ddFt(), company_name: ddLegalName(), draft_id: STATE.draft_id}});
    ddBusy(btn, false);
    if (d.error) { alert(d.message || 'Learning failed.'); return; }
    DD.memory = d.memory; DD.memoryFor = ddSym(); DD.prefilledFor = null;
    ddRenderMemory(); ddPrefillDetails();
    if (d.ai_error) ddToast(`House format learned. ${d.ai_error.message}`);
    else ddToast(`Learned from ${((d.memory.sources || {}).letters) || 0} letters.`);
}

function ddToast(msg) {
    let t = document.getElementById('dd-toast');
    if (!t) {
        t = document.createElement('div'); t.id = 'dd-toast';
        t.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:90;max-width:min(560px,92vw);padding:10px 16px;border-radius:12px;background:#1b1a16;color:#fff;font-size:12.5px;box-shadow:0 14px 40px -12px rgba(0,0,0,.5);transition:opacity .2s';
        document.body.appendChild(t);
    }
    t.textContent = msg; t.style.opacity = '1';
    clearTimeout(t._h); t._h = setTimeout(() => { t.style.opacity = '0'; }, 4200);
}

// ── Memory drawer ───────────────────────────────────────────────────────
const DD_FORMAT_LABELS = [
    ['date_format', 'Date style', v => (DD.memory.format.date_format || {}).example || v],
    ['date_label', 'Date label', v => v === 'none' ? 'No “Date:” label' : v],
    ['subject_label', 'Subject label'], ['salutation', 'Salutation'], ['opening', 'Typical opening'],
    ['request_line', 'Closing line'], ['thanks_line', 'Thanks line'], ['close_phrase', 'Sign-off'],
    ['for_line', 'For line'], ['sig_name', 'Signatory'], ['sig_designation', 'Designation'], ['sig_membership', 'Membership'],
    ['lodr_cite', 'Citation style', v => v === 'full' ? 'Full name of the LODR Regulations' : 'SEBI (LODR) Regulations'],
    ['defined_term', 'Defined term'], ['currency', 'Currency'], ['self_reference', 'Refers to itself as'],
    ['code_lines', 'Stock-code block', v => (v || []).join(' · ')],
    ['bse_block', 'BSE addressee', v => (v || []).join(', ')], ['nse_block', 'NSE addressee', v => (v || []).join(', ')],
];

function ddOpenMemory(section) {
    if (!STATE.company) { alert('Choose a company first.'); return; }
    const dr = document.getElementById('dd-drawer');
    dr.classList.add('open'); dr.setAttribute('aria-hidden', 'false');
    document.getElementById('dd-drawer-back').classList.add('open');
    (DD.memory && DD.memoryFor === ddSym() ? Promise.resolve() : ddLoadMemory(true)).then(() => {
        ddRenderDrawer();
        if (section) { const el = document.getElementById('dd-sec-' + section); if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'}); }
    });
}
function ddCloseMemory() {
    const dr = document.getElementById('dd-drawer');
    dr.classList.remove('open'); dr.setAttribute('aria-hidden', 'true');
    document.getElementById('dd-drawer-back').classList.remove('open');
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') ddCloseMemory(); });

function ddRenderDrawer() {
    const m = DD.memory;
    const body = document.getElementById('dd-drawer-body');
    if (!m || !body) return;
    const c = ddMemoryCounts();
    const learnedAt = m.learned_at ? new Date(m.learned_at).toLocaleString() : '';
    const fmtRows = DD_FORMAT_LABELS.filter(([k]) => m.format && m.format[k] && m.format[k].value !== null && m.format[k].value !== '')
        .map(([k, label, fn]) => {
            const f = m.format[k];
            const v = fn ? fn(f.value) : f.value;
            return `<div class="dd-mem-row" style="grid-template-columns:130px 1fr"><span class="dd-mem-k">${label}</span><span class="dd-mem-v">${ddEsc(v)}${f.of ? `<span class="dd-src">${f.count}/${f.of}</span>` : ''}</span></div>`;
        }).join('');
    const examples = ((m.sources || {}).examples || []).slice(0, 12).map(e => `<li>${ddEsc(e.title || 'Letter')}${e.date ? ` <span class="dd-tiny">${ddEsc(e.date)}</span>` : ''}</li>`).join('');
    const srcLabel = {approved: ['Exported by your team', 'dd-pill-green'], upload: ['Uploaded', 'dd-pill-blue'], paste: ['Pasted', 'dd-pill-blue'], filing: ['Pinned filing', 'dd-pill-yellow']};
    const docs = (m.documents || []).map(d => {
        const [lab, cls] = srcLabel[d.source] || [d.source, ''];
        const scope = d.symbol ? '' : '<span class="dd-pill">All companies</span>';
        return `<div class="dd-doc"><div class="flex-1 min-w-0"><div class="flex items-center gap-1.5 flex-wrap"><span class="dd-h2 truncate">${ddEsc(d.title)}</span><span class="dd-pill ${cls}">${lab}</span>${scope}${d.filing_type ? `<span class="dd-pill">${ddEsc(d.filing_type)}</span>` : ''}</div>
                <p class="dd-tiny mt-0.5 truncate">${ddEsc(d.preview || '')}</p></div>
                <button class="dd-btn dd-btn-ghost dd-btn-sm" onclick="ddDeleteDoc(${d.id})" title="Remove from memory">Remove</button></div>`;
    }).join('') || '<p class="dd-tiny">No reference letters yet. Upload a few letters your team is happy with: Delta copies their structure and phrasing, never their facts.</p>';
    const allFacts = [...(m.facts || []), ...(m.missing_facts || []).map(f => ({...f, value: '', source: ''}))];
    const factOrder = ['legal_name', 'cin', 'registered_office', 'isin', 'bse_code', 'nse_symbol', 'cs_name', 'cs_designation', 'cs_membership', 'email', 'website', 'phone'];
    allFacts.sort((a, b) => factOrder.indexOf(a.key) - factOrder.indexOf(b.key));
    const facts = allFacts.map(f => `<label class="block"><span class="flex items-center justify-between dd-tiny mb-0.5"><span style="color:var(--dd-ink-2);font-weight:600">${ddEsc(f.label)}</span><span>${f.value ? ddEsc(f.detail || '') : '<span style="color:var(--dd-amber)">missing</span>'}</span></span>
        <input class="dd-input" data-ddfact="${f.key}" value="${ddEsc(f.value)}"></label>`).join('');

    body.innerHTML = `
    <section class="dd-card p-4" id="dd-sec-style">
        <div class="flex items-center justify-between mb-2"><div><p class="dd-h2">House style</p><p class="dd-tiny">${c.letters ? `Learned from ${c.letters} letters${learnedAt ? ' · ' + ddEsc(learnedAt) : ''}` : 'Not learned yet'}</p></div>
        <button class="dd-btn dd-btn-sm" onclick="ddLearn(this)">${DD_SPARK} ${c.letters ? 'Re-learn' : 'Learn now'}</button></div>
        ${fmtRows || '<p class="dd-sub">Delta reads the company\'s recent exchange letters (and anything you upload below) and learns its date style, citations, closing line, sign-off, signatory and addressee blocks.</p>'}
        ${examples ? `<details class="mt-2"><summary class="dd-tiny cursor-pointer">Letters it learned from</summary><ul class="dd-tiny mt-1 space-y-0.5 list-disc pl-4">${examples}</ul></details>` : ''}
        ${Object.keys(m.by_type || {}).length ? `<div class="mt-3 pt-3 dd-divider">${Object.entries(m.by_type).map(([t, v]) => `<p class="dd-h2">${ddEsc(t)}</p><p class="dd-tiny">${ddEsc(v.tone || '')}</p>${(v.phrases || []).slice(0, 5).map(p => `<p class="dd-tiny">“${ddEsc(p)}”</p>`).join('')}`).join('<div class="h-2"></div>')}</div>` : ''}
    </section>
    <section class="dd-card p-4" id="dd-sec-rules">
        <p class="dd-h2">Rules</p>
        <p class="dd-tiny mb-2">Plain instructions Delta follows on every draft. One per line.</p>
        <label class="dd-tiny font-semibold block mb-1" style="color:var(--dd-ink-2)">For ${ddEsc(ddSym())}</label>
        <textarea id="dd-rules-co" rows="4" class="dd-textarea" placeholder="- Refer to Radhika Shah as 'Company Secretary & Compliance Officer'\n- Always mention the Listing Regulations by their defined term">${ddEsc(m.company_instructions || '')}</textarea>
        <div class="flex justify-end mt-1.5"><button class="dd-btn dd-btn-sm" onclick="ddSaveRules('company', this)">Save company rules</button></div>
        <label class="dd-tiny font-semibold block mb-1 mt-2" style="color:var(--dd-ink-2)">For every company (team-wide)</label>
        <textarea id="dd-rules-team" rows="4" class="dd-textarea" placeholder="- Use 'Yours faithfully' for exchanges\n- Never use 'we are pleased to inform'\n- Indian digit grouping (1,49,982)">${ddEsc(m.team_instructions || '')}</textarea>
        <div class="flex justify-end mt-1.5"><button class="dd-btn dd-btn-sm" onclick="ddSaveRules('team', this)">Save team rules</button></div>
    </section>
    <section class="dd-card p-4" id="dd-sec-docs">
        <div class="flex items-center justify-between mb-2"><p class="dd-h2">Reference letters <span class="dd-tiny font-normal">${c.docs}</span></p></div>
        <div class="dd-drop" id="dd-drop" onclick="document.getElementById('dd-file').click()">Drop a PDF, Word or text file, or click to upload<br><span class="dd-tiny">Up to 15 MB. Scanned PDFs need pasting as text.</span></div>
        <input type="file" id="dd-file" accept=".pdf,.docx,.txt,.md" class="hidden" onchange="ddUploadDoc(this.files[0]); this.value='';">
        <div class="grid grid-cols-2 gap-2 mt-2">
            <select id="dd-doc-scope" class="dd-input" style="font-size:12px;padding:7px 8px"><option value="company">For ${ddEsc(ddSym())}</option><option value="team">For every company</option></select>
            <select id="dd-doc-type" class="dd-input" style="font-size:12px;padding:7px 8px"><option value="${ddEsc(ddFt())}">${ddEsc((STATE.filing_type || {}).label || 'This filing type')}</option><option value="">Any filing type</option></select>
        </div>
        <details class="mt-2"><summary class="dd-tiny cursor-pointer">Paste text instead</summary>
            <input id="dd-paste-title" class="dd-input mt-2" placeholder="Title, e.g. Reg 30 appointment letter, Aug 2026">
            <textarea id="dd-paste-text" rows="5" class="dd-textarea mt-2" placeholder="Paste the letter text"></textarea>
            <div class="flex justify-end mt-1.5"><button class="dd-btn dd-btn-sm" onclick="ddPasteDoc(this)">Add to memory</button></div>
        </details>
        <div class="mt-3">${docs}</div>
    </section>
    <section class="dd-card p-4" id="dd-sec-facts">
        <p class="dd-h2">Company particulars</p>
        <p class="dd-tiny mb-2">Used to fill letters and to check them. Values you save here override what Delta read from filings.</p>
        <div class="grid grid-cols-1 gap-2">${facts}</div>
        <div class="flex justify-end mt-2"><button class="dd-btn dd-btn-primary dd-btn-sm" onclick="ddSaveFacts(this)">Save particulars</button></div>
    </section>`;
    const drop = document.getElementById('dd-drop');
    if (drop) {
        drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
        drop.addEventListener('dragleave', () => drop.classList.remove('over'));
        drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files[0]) ddUploadDoc(e.dataTransfer.files[0]); });
    }
}

async function ddSaveRules(scope, btn) {
    const text = document.getElementById(scope === 'team' ? 'dd-rules-team' : 'dd-rules-co').value;
    ddBusy(btn, true, 'Saving…');
    const d = await ddApi('/api/drafting/memory/instructions', {json: {symbol: ddSym(), scope, text, draft_id: STATE.draft_id}});
    ddBusy(btn, false);
    if (d.error) { alert(d.message); return; }
    DD.memory = d.memory; ddRenderMemory(); ddToast('Rules saved. The next draft follows them.');
}
async function ddRememberFromDesk() {
    const el = document.getElementById('dd-remember');
    const line = (el.value || '').trim();
    const st = document.getElementById('dd-remember-status');
    if (!line) { el.focus(); return; }
    const scope = document.getElementById('dd-remember-scope').value;
    const d = await ddApi('/api/drafting/memory/instructions', {json: {symbol: ddSym(), scope, append: line, draft_id: STATE.draft_id}});
    if (d.error) { st.textContent = d.message; return; }
    el.value = ''; DD.memory = d.memory; ddRenderMemory();
    st.textContent = scope === 'team' ? 'Remembered for every company.' : `Remembered for ${ddSym()}.`;
}
async function ddUploadDoc(file) {
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file); fd.append('symbol', ddSym());
    fd.append('scope', (document.getElementById('dd-doc-scope') || {}).value || 'company');
    fd.append('filing_type', (document.getElementById('dd-doc-type') || {}).value || '');
    fd.append('title', file.name.replace(/\.[^.]+$/, ''));
    if (STATE.draft_id) fd.append('draft_id', STATE.draft_id);
    ddToast(`Reading ${file.name}…`);
    const d = await ddApi('/api/drafting/memory/docs', {form: fd});
    if (d.error) { alert(d.message); return; }
    DD.memory = d.memory; ddRenderMemory();
    ddToast(`Added ${file.name} (${Math.round(d.chars / 100) / 10}k characters). Re-learn to fold it into the house style.`);
}
async function ddPasteDoc(btn) {
    const text = document.getElementById('dd-paste-text').value;
    const title = document.getElementById('dd-paste-title').value || 'Pasted reference';
    ddBusy(btn, true, 'Adding…');
    const d = await ddApi('/api/drafting/memory/docs', {json: {symbol: ddSym(), text, title, draft_id: STATE.draft_id,
        scope: (document.getElementById('dd-doc-scope') || {}).value, filing_type: (document.getElementById('dd-doc-type') || {}).value}});
    ddBusy(btn, false);
    if (d.error) { alert(d.message); return; }
    DD.memory = d.memory; ddRenderMemory(); ddToast('Added to memory.');
}
async function ddDeleteDoc(id) {
    if (!confirm('Remove this letter from memory?')) return;
    const d = await ddApi(`/api/drafting/memory/docs/${id}`, {method: 'DELETE', json: {draft_id: STATE.draft_id}});
    if (d.error) { alert(d.message); return; }
    await ddLoadMemory(true);
}
async function ddPinFiling(filingId, btn) {
    ddBusy(btn, true, 'Pinning…');
    const d = await ddApi('/api/drafting/memory/docs/filing', {json: {symbol: ddSym(), filing_id: filingId, filing_type: ddFt(), draft_id: STATE.draft_id}});
    ddBusy(btn, false);
    if (d.error) { alert(d.message); return; }
    DD.memory = d.memory; ddRenderMemory();
    if (btn) { btn.textContent = 'Pinned'; btn.disabled = true; }
}
async function ddSaveFacts(btn) {
    const facts = {};
    document.querySelectorAll('[data-ddfact]').forEach(el => { facts[el.dataset.ddfact] = el.value; });
    ddBusy(btn, true, 'Saving…');
    const d = await ddApi('/api/drafting/memory/facts', {json: {symbol: ddSym(), facts, draft_id: STATE.draft_id}});
    ddBusy(btn, false);
    if (d.error) { alert(d.message); return; }
    DD.memory = d.memory; DD.prefilledFor = null; ddRenderMemory(); ddPrefillDetails();
    ddToast('Particulars saved.');
}

// ── Step 3: brief + requirements + past filings ─────────────────────────
function loadPastExamples() {
    const ft = STATE.filing_type || {};
    const typeEl = document.getElementById('dd-brief-type');
    if (typeEl) typeEl.textContent = ft.label || '';
    const brief = document.getElementById('new-info');
    if (brief && STATE.new_info && !brief.value) brief.value = STATE.new_info;
    document.getElementById('dd-compose-error').classList.add('hidden');
    ddLoadMemory();
    ddLoadRequirements();

    const wrap = document.getElementById('past-examples');
    const countEl = document.getElementById('past-count');
    wrap.innerHTML = '<div class="dd-skel mb-1.5"></div><div class="dd-skel" style="width:60%"></div>';
    countEl.textContent = '';
    fetch(`/api/draft/examples?symbol=${encodeURIComponent(ddSym())}&filing_type=${encodeURIComponent(ft.id || '')}`)
        .then(r => r.json()).then(data => {
            STATE.examples = data.examples || [];
            countEl.textContent = STATE.examples.length ? `· ${STATE.examples.length}` : '';
            if (!STATE.examples.length) {
                wrap.innerHTML = `<p class="dd-tiny">None found in ${ddEsc(ddSym())}'s archive. Delta will follow the house style and SEBI's format; upload a reference letter in Draft memory for a closer match.</p>`;
                return;
            }
            const pinned = new Set(((DD.memory || {}).documents || []).filter(d => d.source === 'filing').map(d => String(d.ref)));
            wrap.innerHTML = STATE.examples.map(ex => `
                <div class="flex items-center gap-2 py-1">
                    <div class="flex-1 min-w-0"><p class="text-[12px] font-medium truncate" style="color:var(--dd-ink)">${ddEsc(ex.title || 'Filing')}</p><p class="dd-tiny">${ddEsc(ex.date || '')}${ex.text ? '' : ' · text not available'}</p></div>
                    ${ex.pdf_link ? `<a href="${ddEsc(ex.pdf_link)}" target="_blank" rel="noopener" class="dd-tiny dd-link">PDF</a>` : ''}
                    ${ex.id ? `<button class="dd-btn dd-btn-sm" onclick="ddPinFiling(${ex.id}, this)" ${pinned.has(String(ex.id)) ? 'disabled' : ''} title="Use this letter as a reference for future drafts">${pinned.has(String(ex.id)) ? 'Pinned' : 'Pin'}</button>` : ''}
                </div>`).join('');
        }).catch(() => { wrap.innerHTML = '<p class="dd-tiny">Could not load past filings.</p>'; });
}

async function ddLoadRequirements() {
    const ft = ddFt();
    if (!ft) return;
    if (DD.reqs && DD.reqsFor === ft) { ddRenderReqs(); ddRenderCover(); return; }
    const d = await ddApi(`/api/drafting/requirements/${encodeURIComponent(ft)}`);
    if (d.error) return;
    DD.reqs = d; DD.reqsFor = ft;
    ddRenderReqs(); ddRenderCover();
}
function ddRx(h) { try { return h ? new RegExp(h, 'i') : null; } catch (e) { return null; } }
function ddTextItems() { return ((DD.reqs || {}).items || []).filter(i => i.kind !== 'timing' && i.verified !== false); }

function ddRenderReqs() {
    const box = document.getElementById('dd-req-box');
    if (!box) return;
    const items = ddTextItems();
    if (!items.length) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden');
    const brief = (document.getElementById('new-info') || {}).value || '';
    let covered = 0, core = 0;
    const html = items.map(it => {
        const rx = ddRx(it.hint);
        const on = !!(rx && rx.test(brief));
        if (!it.applies_when) { core++; if (on) covered++; }
        return `<div class="dd-req"><span class="dd-check ${on ? 'on' : ''} ${it.applies_when ? 'cond' : ''}">${on ? '✓' : ''}</span>
            <div><span style="color:var(--dd-ink);font-weight:600">${ddEsc(it.label)}</span>${it.applies_when ? ` <span class="dd-req-when">· if ${ddEsc(it.applies_when)}</span>` : ''}
            <div class="dd-tiny">${ddEsc(it.requirement)}</div></div></div>`;
    }).join('');
    document.getElementById('dd-req-list').innerHTML = html;
    document.getElementById('dd-req-count').textContent = `${covered} of ${core} covered in your brief`;
    document.getElementById('dd-req-count').className = 'dd-pill ' + (covered === core ? 'dd-pill-green' : 'dd-pill-amber');
    const src = document.getElementById('dd-req-source');
    src.textContent = DD.reqs.derived ? "From Delta's template guidance for this filing type (not a verified SEBI list)."
        : `From the SEBI (LODR) Regulations and SEBI's Master Circular, reviewed ${DD.reqs.reviewed}. Dashed boxes apply only in some cases.`;
    const chips = items.filter(it => !it.applies_when && !(ddRx(it.hint) || {test: () => false}).test(brief)).slice(0, 6);
    document.getElementById('dd-req-chips').innerHTML = chips.map(it =>
        `<button type="button" class="dd-chip" onclick="ddAddToBrief(${JSON.stringify(it.label).replace(/"/g, '&quot;')})">+ ${ddEsc(it.label)}</button>`).join('');
}
let _ddBriefT = null;
function ddBriefChanged() { clearTimeout(_ddBriefT); _ddBriefT = setTimeout(ddRenderReqs, 250); }
function ddAddToBrief(label) {
    const el = document.getElementById('new-info');
    el.value = (el.value.replace(/\s+$/, '') + (el.value.trim() ? '\n' : '') + label + ': ');
    el.focus(); el.setSelectionRange(el.value.length, el.value.length);
    ddRenderReqs();
}

// ── Compose ─────────────────────────────────────────────────────────────
async function generateDraft() {
    const briefEl = document.getElementById('new-info');
    const brief = briefEl.value.trim();
    const errBox = document.getElementById('dd-compose-error');
    if (!brief) {
        errBox.innerHTML = '<div class="dd-note dd-note-amber"><span>!</span><div>Write a short brief first: who or what, the dates, the amounts, and the reason.</div></div>';
        errBox.classList.remove('hidden'); briefEl.focus(); return;
    }
    errBox.classList.add('hidden');
    STATE.new_info = brief;
    const btn = document.getElementById('generate-btn');
    const status = document.getElementById('dd-compose-status');
    ddBusy(btn, true, 'Writing…');
    const msgs = ["Reading the company's house style…", "Checking what SEBI requires…", 'Writing in the company\'s words…', 'Still writing. Long filings take up to a minute.'];
    let k = 0; status.textContent = msgs[0];
    const tick = setInterval(() => { k = Math.min(k + 1, msgs.length - 1); status.textContent = msgs[k]; }, 7000);
    const d = await ddApi('/api/drafting/compose', {json: {symbol: ddSym(), company_name: ddLegalName(), filing_type: ddFt(),
        brief, subject: STATE.subject, draft_id: STATE.draft_id}});
    clearInterval(tick); ddBusy(btn, false); status.textContent = '';
    if (d.error) { errBox.innerHTML = ddErrorHtml(d, 'Drafting'); errBox.classList.remove('hidden'); return; }
    STATE.subject = d.subject || STATE.subject;
    STATE.body = d.body || '';
    STATE.annexures = (d.annexures || []).map(a => ({title: a.title || '', body: a.body || ''}));
    STATE.draft_id = d.draft_id || STATE.draft_id;
    DD.check = null; DD.checkStamp = null; DD.fields = [];
    document.getElementById('field-subject').value = STATE.subject;
    showStep(4);
    _setEditorPlainText(STATE.body);
    renderAnnexureEditors();
    rescanPlaceholders();
    DD.questions = d.open_questions || [];
    ddRenderQuestions();
    const note = document.getElementById('dd-compose-note');
    const u = d.used || {};
    note.innerHTML = `Written by ${ddEsc(d.model)} in ${d.seconds}s, using ${u.letters_learned ? "the company's house style" : 'SEBI conventions (house style not learned yet)'}${u.rules ? ', your rules' : ''}, ${u.facts || 0} company particulars${(u.examples || []).length ? ` and ${(u.examples || []).length} example letter${u.examples.length === 1 ? '' : 's'}` : ''}${u.requirements ? `, against ${u.requirements} SEBI items` : ''}.`;
    note.classList.remove('hidden');
    if (STATE.draft_id) {
        const s = document.getElementById('save-draft-status');
        if (s) s.textContent = `Auto-saved as draft #${STATE.draft_id}`;
        loadMyDraftsCount(); loadTeam();
    }
}
function ddRenderQuestions() {
    const el = document.getElementById('dd-questions');
    if (!el) return;
    if (!DD.questions.length) { el.classList.add('hidden'); return; }
    el.innerHTML = `<p class="dd-h2 mb-1">Delta needs to know</p>` +
        DD.questions.map(q => `<p class="dd-tiny py-1" style="color:var(--dd-ink-2)">• ${ddEsc(q)}</p>`).join('') +
        '<p class="dd-tiny mt-1">Answer these in the fields on the left, or add them to the brief and write again.</p>';
    el.classList.remove('hidden');
}

// ── Fields (placeholders) ───────────────────────────────────────────────
const DD_PH = /\[\s*PLEASE\s*FILL[^\]]*\]|\{[a-z][a-z0-9_]{2,60}\}/gi;

function ddLocations() {
    _syncAnnexuresFromDOM();
    const out = [{loc: 'body', name: 'Letter body', text: _quill ? _getEditorPlainText() : (STATE.body || '')}];
    STATE.annexures.forEach((a, i) => {
        out.push({loc: `t${i}`, name: `Annexure ${_roman(i + 1)} title`, text: a.title || ''});
        out.push({loc: `a${i}`, name: a.title || `Annexure ${_roman(i + 1)}`, text: a.body || ''});
    });
    return out;
}
function ddCells(line) {
    let l = line.trim();
    if (l.startsWith('|')) l = l.slice(1);
    if (l.endsWith('|')) l = l.slice(0, -1);
    return l.split('|').map(c => c.trim());
}
function ddDescribe(token, text, idx) {
    const lineStart = text.lastIndexOf('\n', idx - 1) + 1;
    let lineEnd = text.indexOf('\n', idx); if (lineEnd < 0) lineEnd = text.length;
    const line = text.slice(lineStart, lineEnd);
    const sentStart = Math.max(text.lastIndexOf('. ', idx) + 2, lineStart, idx - 160);
    let sentEnd = text.indexOf('. ', idx + token.length); if (sentEnd < 0 || sentEnd > lineEnd) sentEnd = lineEnd;
    let context = text.slice(sentStart, Math.min(sentEnd + 1, idx + token.length + 140)).replace(/\s+/g, ' ').trim();
    const hint = (token.match(/^\[\s*PLEASE\s*FILL\s*[—–\-:]+\s*([^\]]+)\]$/i) || [])[1];
    if (hint) return {label: hint.trim(), context};
    const curly = (token.match(/^\{([a-z0-9_]+)\}$/i) || [])[1];
    if (curly) return {label: curly.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase()), context};
    if (line.includes('|')) {
        // Table cell: name it by its column header and its row.
        const lines = text.split('\n');
        const lineNo = text.slice(0, lineStart).split('\n').length - 1;
        let top = lineNo;
        while (top > 0 && lines[top - 1].includes('|')) top--;
        const header = ddCells(lines[top]);
        const before = text.slice(lineStart, idx);
        const col = (before.trim().startsWith('|') ? before.split('|').length - 2 : before.split('|').length - 1);
        const row = ddCells(line);
        const rowLabel = row.find(c => c && !/PLEASE\s*FILL/i.test(c) && !/^-+$/.test(c)) || `row ${lineNo - top}`;
        const colLabel = header[col] && !/PLEASE\s*FILL/i.test(header[col]) ? header[col] : `column ${col + 1}`;
        return {label: `${colLabel} · ${rowLabel.slice(0, 60)}`, context: row.filter(Boolean).join(' | ').slice(0, 200)};
    }
    const lead = text.slice(Math.max(lineStart, idx - 70), idx).replace(/\s+/g, ' ').trim().split(' ').slice(-6).join(' ');
    return {label: lead ? `…${lead}` : 'Missing detail', context};
}

function ddScanFields() {
    const old = {};
    DD.fields.forEach(f => { old[f.key] = f; });
    const seen = {};
    const out = [];
    let n = 0;
    for (const L of ddLocations()) {
        DD_PH.lastIndex = 0;
        let m;
        while ((m = DD_PH.exec(L.text))) {
            n++;
            const {label, context} = ddDescribe(m[0], L.text, m.index);
            const base = `${L.loc}|${label}|${m[0]}`;
            seen[base] = (seen[base] || 0) + 1;
            const key = `${base}|${seen[base]}`;
            const prev = old[key] || {};
            out.push({id: `F${n}`, key, loc: L.loc, where: L.name, start: m.index, end: m.index + m[0].length, token: m[0],
                      label, context, value: prev.value || '', source: prev.source || '', source_label: prev.source_label || '',
                      evidence: prev.evidence || '', reason: prev.reason || ''});
        }
    }
    DD.fields = out;
}

function rescanPlaceholders() {
    _initQuillIfNeeded();
    ddScanFields();
    ddRenderFields();
    ddHighlight();
    ddRenderCover();
}

function ddRenderFields() {
    const section = document.getElementById('placeholders-section');
    const list = document.getElementById('placeholder-fields');
    if (!section || !list) return;
    if (!DD.fields.length) {
        section.classList.add('hidden'); list.innerHTML = '';
        return;
    }
    section.classList.remove('hidden');
    let lastWhere = null;
    list.innerHTML = DD.fields.map(f => {
        const head = f.where !== lastWhere ? `<p class="dd-tiny font-semibold pt-1" style="color:var(--dd-ink-2)">${ddEsc(f.where)}</p>` : '';
        lastWhere = f.where;
        const cls = f.value ? (f.source && f.source !== 'you' ? 'ai' : 'filled') : '';
        const src = f.value && f.source_label
            ? `<div class="dd-field-src">From <b>${ddEsc(f.source_label)}</b>${f.evidence ? ` · “${ddEsc(f.evidence.slice(0, 90))}”` : ''}</div>`
            : (!f.value && f.reason ? `<div class="dd-field-src reason">${ddEsc(f.reason)}</div>` : '');
        return `${head}<div class="dd-field ${cls}" data-fid="${f.id}">
            <div class="dd-field-label"><span>${ddEsc(f.label)}</span><button type="button" class="dd-link text-[10.5px]" onclick="ddLocate('${f.id}')">Show</button></div>
            ${f.context && f.context !== f.label ? `<div class="dd-field-ctx">${ddEsc(f.context)}</div>` : ''}
            <input type="text" value="${ddEsc(f.value)}" placeholder="Type the value" oninput="ddFieldInput('${f.id}', this)"
                   onkeydown="if(event.key==='Enter'){event.preventDefault();applyPlaceholders();}">
            ${src}</div>`;
    }).join('');
    ddFieldCounts();
}
function ddFieldCounts() {
    const total = DD.fields.length, filled = DD.fields.filter(f => (f.value || '').trim()).length;
    const c = document.getElementById('dd-fields-count');
    if (c) c.textContent = `${filled} of ${total} ready`;
    const bar = document.getElementById('dd-fields-bar');
    if (bar) bar.style.width = total ? `${Math.round(filled / total * 100)}%` : '0%';
}
function ddFieldInput(id, input) {
    const f = DD.fields.find(x => x.id === id);
    if (!f) return;
    f.value = input.value; f.source = 'you'; f.source_label = ''; f.evidence = '';
    const card = input.closest('.dd-field');
    card.classList.toggle('filled', !!input.value.trim()); card.classList.remove('ai');
    const src = card.querySelector('.dd-field-src'); if (src) src.remove();
    ddFieldCounts();
}
function ddLocate(id) {
    const f = DD.fields.find(x => x.id === id);
    if (!f) return;
    if (f.loc === 'body' && _quill) {
        _quill.focus(); _quill.setSelection(f.start, f.end - f.start, 'user');
        const b = _quill.getBounds(f.start);
        const ed = document.querySelector('#editor-body .ql-editor');
        if (ed && b) ed.scrollTop = Math.max(0, b.top - 120);
        document.getElementById('editor-body').scrollIntoView({behavior: 'smooth', block: 'center'});
        return;
    }
    const i = parseInt(f.loc.slice(1), 10);
    const el = document.querySelector(f.loc[0] === 't' ? `[data-annx-title="${i}"]` : `[data-annx-body="${i}"]`);
    if (el) { el.focus(); el.setSelectionRange(f.start, f.end); el.scrollIntoView({behavior: 'smooth', block: 'center'}); }
}
function ddHighlight() {
    if (!_quill) return;
    _quill.formatText(0, _quill.getLength(), 'background', false, 'silent');
    DD.fields.filter(f => f.loc === 'body').forEach(f => _quill.formatText(f.start, f.end - f.start, 'background', '#fde68a', 'silent'));
}

async function aiFillPlaceholders() {
    _initQuillIfNeeded();
    ddScanFields(); ddRenderFields();
    const status = document.getElementById('apply-status');
    const todo = DD.fields.filter(f => !(f.value || '').trim());
    if (!todo.length) { status.innerHTML = 'Every field has a value. Click <b>Apply to draft</b>.'; return; }
    const btn = document.getElementById('ai-fill-btn');
    ddBusy(btn, true, 'Filling…');
    status.textContent = "Looking in your brief, the company's particulars and its filings…";
    const locs = ddLocations();
    const doc = locs.filter(L => L.text.trim()).map(L => {
        let t = L.text;
        DD.fields.filter(f => f.loc === L.loc).sort((a, b) => b.start - a.start)
            .forEach(f => { t = t.slice(0, f.start) + `⟦${f.id}⟧` + t.slice(f.end); });
        return `=== ${L.name} ===\n${t}`;
    }).join('\n\n');
    const brief = STATE.new_info || (document.getElementById('new-info') || {}).value || '';
    const d = await ddApi('/api/drafting/fill', {json: {symbol: ddSym(), company_name: ddLegalName(), filing_type: ddFt(), brief,
        document: doc, fields: todo.map(f => ({id: f.id, label: f.label, context: f.context})), draft_id: STATE.draft_id}});
    ddBusy(btn, false);
    if (d.error && !d.fills) { status.innerHTML = ddErrorHtml(d, 'Fill'); return; }
    const byId = {};
    DD.fields.forEach(f => { byId[f.id] = f; });
    (d.fills || []).forEach(x => {
        const f = byId[x.id];
        if (f && !(f.value || '').trim()) Object.assign(f, {value: x.value, source: x.source, source_label: x.source_label, evidence: x.evidence, reason: ''});
    });
    (d.unfilled || []).forEach(x => { const f = byId[x.id]; if (f && !f.value) f.reason = x.reason; });
    ddRenderFields();
    const got = (d.fills || []).length, left = todo.length - got;
    let msg = got ? `Filled <b>${got}</b> of ${todo.length}.` : 'Nothing could be traced to your brief or the company\'s records.';
    if (left > 0) msg += ` ${left} left for you; each says why.`;
    if (got) msg += ' Review, then <b>Apply to draft</b>.';
    status.innerHTML = msg + (d.error ? `<div class="mt-1.5">${ddErrorHtml(d.error, 'AI fill')}</div>` : '');
}

function applyPlaceholders() {
    _initQuillIfNeeded();
    const status = document.getElementById('apply-status');
    const withVal = DD.fields.filter(f => (f.value || '').trim());
    if (!withVal.length) { status.textContent = 'Type or fill at least one value first.'; return; }
    const locs = ddLocations();
    DD.undo = {body: locs[0].text, annexures: JSON.parse(JSON.stringify(STATE.annexures))};
    let applied = 0;
    for (const L of locs) {
        const list = withVal.filter(f => f.loc === L.loc).sort((a, b) => b.start - a.start);
        if (!list.length) continue;
        let t = L.text;
        for (const f of list) {
            if (t.slice(f.start, f.end) === f.token) { t = t.slice(0, f.start) + f.value.trim() + t.slice(f.end); applied++; }
        }
        if (L.loc === 'body') { _setEditorPlainText(t); STATE.body = t; }
        else {
            const i = parseInt(L.loc.slice(1), 10);
            if (L.loc[0] === 't') STATE.annexures[i].title = t; else STATE.annexures[i].body = t;
        }
    }
    renderAnnexureEditors();
    DD.fields = DD.fields.filter(f => !(f.value || '').trim());
    rescanPlaceholders();
    status.innerHTML = `Applied ${applied} value${applied === 1 ? '' : 's'} to the draft. <span class="dd-link" onclick="ddUndo()">Undo</span>`;
}

function ddUndo() {
    if (!DD.undo) return;
    if (DD.undo.body !== undefined) { _setEditorPlainText(DD.undo.body); STATE.body = DD.undo.body; }
    if (DD.undo.annexures) { STATE.annexures = DD.undo.annexures; renderAnnexureEditors(); }
    DD.undo = null;
    rescanPlaceholders();
    ddToast('Undone.');
}

// ── Rewrite ─────────────────────────────────────────────────────────────
const DD_MODE_NAMES = {house: 'Matched to house style', polish: 'Polished', formal: 'Made more formal', shorter: 'Tightened', custom: 'Changed as asked'};

async function ddRewriteText(text, mode, instruction) {
    return ddApi('/api/drafting/rewrite', {json: {symbol: ddSym(), filing_type: ddFt(), text, mode, instruction, draft_id: STATE.draft_id}});
}
async function improveBody(mode) {
    _initQuillIfNeeded();
    if (mode === 'tone') mode = 'house';
    const text = _getEditorPlainText().trim();
    const st = document.getElementById('dd-rewrite-status');
    if (!text) return;
    ddTab('assist');
    document.querySelectorAll('.dd-mode').forEach(b => { b.disabled = true; });
    st.innerHTML = '<span class="dd-spin"></span> Rewriting…';
    const d = await ddRewriteText(text, mode, '');
    document.querySelectorAll('.dd-mode').forEach(b => { b.disabled = false; });
    if (d.error) { st.innerHTML = ddErrorHtml(d, 'Rewrite'); return; }
    DD.undo = {body: text};
    _setEditorPlainText(d.text); STATE.body = d.text;
    rescanPlaceholders();
    st.innerHTML = `${DD_MODE_NAMES[mode] || 'Rewritten'}. <span class="dd-link" onclick="ddUndo()">Undo</span>` +
        (d.warning ? `<div class="dd-note dd-note-amber mt-1.5"><span>!</span><div>${ddEsc(d.warning)}</div></div>` : '');
}
async function modifyDraft() {
    _initQuillIfNeeded();
    const box = document.getElementById('dd-instruction');
    const instruction = (box.value || '').trim();
    ddTab('assist');
    if (!instruction) { box.focus(); box.scrollIntoView({behavior: 'smooth', block: 'center'}); return; }
    const text = _getEditorPlainText().trim();
    if (!text) return;
    const btn = document.getElementById('dd-instruct-btn');
    const st = document.getElementById('dd-rewrite-status');
    ddBusy(btn, true, 'Applying…');
    const d = await ddRewriteText(text, 'custom', instruction);
    ddBusy(btn, false);
    if (d.error) { st.innerHTML = ddErrorHtml(d, 'Change'); return; }
    DD.undo = {body: text};
    _setEditorPlainText(d.text); STATE.body = d.text;
    rescanPlaceholders();
    box.value = '';
    st.innerHTML = `Changed as asked. <span class="dd-link" onclick="ddUndo()">Undo</span>`;
}
async function improveAnnexure(i, mode) {
    _syncAnnexuresFromDOM();
    const a = STATE.annexures[i];
    if (!a || !(a.body || '').trim()) return;
    if (mode === 'tone') mode = 'house';
    const ta = document.querySelector(`[data-annx-body="${i}"]`);
    if (ta) ta.style.opacity = '.5';
    const d = await ddRewriteText(a.body, mode, '');
    if (ta) ta.style.opacity = '1';
    if (d.error) { alert(d.message); return; }
    DD.undo = {annexures: JSON.parse(JSON.stringify(STATE.annexures))};
    STATE.annexures[i].body = d.text;
    renderAnnexureEditors();
    rescanPlaceholders();
    ddToast(`Annexure ${_roman(i + 1)}: ${(DD_MODE_NAMES[mode] || 'rewritten').toLowerCase()}.`);
}

// ── Assistant tabs and live coverage ────────────────────────────────────
function ddTab(name) {
    document.querySelectorAll('[data-ddtab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.ddtab === name)));
    document.querySelectorAll('[data-ddpanel]').forEach(p => p.classList.toggle('hidden', p.dataset.ddpanel !== name));
    if (name === 'cover') ddRenderCover();
}
function ddRenderCover() {
    const el = document.getElementById('dd-cover-list');
    if (!el || !DD.reqs) return;
    const text = ddLocations().map(L => L.text).join('\n');
    const items = ddTextItems();
    el.innerHTML = items.map(it => {
        const rx = ddRx(it.hint);
        const st = !rx ? 'unchecked' : rx.test(text) ? 'likely' : (it.applies_when ? 'unclear' : 'missing');
        const icon = {likely: '✓', missing: '✕', unclear: '–', unchecked: '?'}[st];
        return `<div class="dd-reqrow"><span class="dd-reqicon ${st}">${icon}</span><div><p class="text-[12px] font-semibold" style="color:var(--dd-ink)">${ddEsc(it.label)}</p>
            <p class="dd-tiny">${it.applies_when ? `If ${ddEsc(it.applies_when)} · ` : ''}${ddEsc(it.source_ref)}</p></div></div>`;
    }).join('') + ((DD.reqs.notes || []).length ? `<div class="dd-note mt-2"><span>⏱</span><div>${DD.reqs.notes.map(ddEsc).join('<br>')}</div></div>` : '');
}

// ── Preview in the company's format ─────────────────────────────────────
function ddHouse() {
    const f = (DD.memory && DD.memoryFor === ddSym() && DD.memory.format) || {};
    const v = k => (f[k] ? f[k].value : null);
    return {date_format: v('date_format'), date_label: v('date_label'), subject_label: v('subject_label'), salutation: v('salutation'),
            thanks_line: v('thanks_line'), close_phrase: v('close_phrase'), for_line: v('for_line'), sig_name: v('sig_name'),
            sig_membership: v('sig_membership'), bse_block: v('bse_block'), nse_block: v('nse_block'), code_lines: v('code_lines'),
            exchange_order: v('exchange_order')};
}
function ddRenderPreview() {
    const L = _gatherLetter();
    const H = ddHouse();
    const today = H.date_format ? ddFormatDate(new Date(), H.date_format)
        : new Date().toLocaleDateString('en-IN', {day: '2-digit', month: 'long', year: 'numeric'});
    const dateLabel = H.date_label === 'none' ? '' : (H.date_label || 'Date: ');
    const LETTER_KINDS = ['', 'letter', 'exchange_letter'];
    const docKind = (STATE.filing_type && STATE.filing_type.doc_kind) || 'letter';
    const addressed = LETTER_KINDS.includes(docKind);
    let bse = (H.bse_block && H.bse_block.length >= 3) ? [...H.bse_block] : ['The Manager', 'Listing Department', 'BSE Limited', 'Phiroze Jeejeebhoy Towers, Dalal Street', 'Mumbai – 400 001'];
    let nse = (H.nse_block && H.nse_block.length >= 3) ? [...H.nse_block] : ['The Manager', 'Listing Department', 'National Stock Exchange of India Limited', 'Exchange Plaza, Bandra Kurla Complex', 'Bandra (East), Mumbai – 400 051'];
    let codes = (H.code_lines || []).filter(c => (L.bse_code && c.includes(L.bse_code)) || (L.nse_symbol && c.toUpperCase().includes(L.nse_symbol.toUpperCase())));
    if (!codes.length) {
        if (L.bse_code) bse.push(`<b>Scrip Code:</b> ${ddEsc(L.bse_code)}`);
        if (L.nse_symbol) nse.push(`<b>Symbol:</b> ${ddEsc(L.nse_symbol)}`);
    }
    const esc = l => (l.startsWith('<b>') ? l : ddEsc(l));
    let cols = [bse, nse];
    if (H.exchange_order === 'nse_first') cols = cols.reverse();
    const forLine = H.for_line || (L.company_name ? `For ${L.company_name.toUpperCase()}` : '');
    const membership = H.sig_membership && H.sig_name && L.signer_name &&
        H.sig_name.toLowerCase().replace(/\W/g, '') === L.signer_name.toLowerCase().replace(/\W/g, '') ? H.sig_membership : '';
    const annexurePages = (L.annexures || []).filter(a => a.title || a.body).map(a => `
        <div style="page-break-before: always; border-top: 2px dashed #c3c0b4; margin-top: 32px; padding-top: 28px;">
            <p style="text-align:center; font-size:11px; color:#666; margin:0 0 8px 0;">${ddEsc(L.company_name || '')}</p>
            <h2 style="text-align:center; font-size:15px; font-weight:bold; text-decoration:underline; margin:6px 0 18px 0;">${ddEsc(a.title || 'Annexure')}</h2>
            ${_renderBodyHtml(a.body)}
        </div>`).join('');
    const head = `<div style="text-align:center; margin-bottom:6px;">
            <h1 style="font-size:18px; font-weight:bold; margin:0; letter-spacing:0.5px;">${ddEsc((L.company_name || '').toUpperCase())}</h1>
            ${L.registered_office ? `<p style="font-size:11px; color:#444; margin:4px 0 0 0;">${ddEsc(L.registered_office)}</p>` : ''}
            ${L.cin ? `<p style="font-size:11px; color:#444; margin:2px 0 0 0;">CIN: ${ddEsc(L.cin)}</p>` : ''}
        </div><hr style="border:none; border-top:1px solid #000; margin:10px 0 24px 0;">`;
    let top;
    if (addressed) {
        top = `<p style="margin:0 0 18px 0;">${ddEsc(dateLabel + today)}</p>
            <table style="width:100%; margin:0 0 14px 0; border-collapse:collapse;"><tr style="vertical-align:top;">
                ${cols.map((c, i) => `<td style="width:50%; ${i ? 'padding-left' : 'padding-right'}:10px; font-size:13px; line-height:1.45;">${c.map(esc).join('<br>')}</td>`).join('')}
            </tr></table>
            ${codes.length ? `<p style="margin:0 0 16px 0; font-size:13px; line-height:1.45;"><b>${codes.map(ddEsc).join('<br>')}</b></p>` : ''}
            ${L.subject ? `<p style="margin:0 0 16px 0;"><b><u>${ddEsc(H.subject_label || 'Subject:')} ${ddEsc(L.subject)}</u></b></p>` : ''}
            <p style="margin:0 0 14px 0;">${ddEsc(H.salutation || 'Dear Sir / Madam,')}</p>`;
    } else {
        top = `${L.subject ? `<p style="text-align:center; margin:0 0 16px 0;"><b><u>${ddEsc((L.subject || '').toUpperCase())}</u></b></p>` : ''}
            <p style="margin:0 0 16px 0;">${ddEsc(dateLabel + today)}</p>`;
    }
    const CLOSE = {resolution: 'By order of the Board', notice: 'By order of the Board'};
    const close = addressed ? (H.close_phrase || 'Yours faithfully,') : (CLOSE[docKind] || '');
    document.getElementById('preview').innerHTML = `${head}${top}${_renderBodyHtml(L.body)}
        ${addressed && H.thanks_line ? `<p style="margin:24px 0 0 0;">${ddEsc(H.thanks_line)}</p>` : ''}
        ${close ? `<p style="margin:${addressed && H.thanks_line ? '8px' : '24px'} 0 0 0;">${ddEsc(close)}</p>` : '<div style="height:18px;"></div>'}
        ${forLine ? `<p style="margin:8px 0 0 0;"><b>${ddEsc(forLine)}</b></p>` : ''}
        <div style="height:50px;"></div>
        ${L.signer_name ? `<p style="margin:0;"><b>${ddEsc(L.signer_name)}</b></p>` : ''}
        ${L.signer_designation ? `<p style="margin:2px 0 0 0;">${ddEsc(L.signer_designation)}</p>` : ''}
        ${membership ? `<p style="margin:2px 0 0 0;">${ddEsc(membership)}</p>` : ''}
        ${annexurePages}`;
}
function goToPreview() {
    STATE.body = _quill ? _getEditorPlainText() : (STATE.body || '');
    STATE.subject = document.getElementById('field-subject').value;
    _seedPdfMeta();
    ddRenderPreview();
    showStep(5);
    ddRenderCheck();
}

// ── Check with AI ───────────────────────────────────────────────────────
function ddLetterStamp() {
    const L = _gatherLetter();
    return JSON.stringify([L.company_name, L.registered_office, L.cin, L.subject, L.body, L.annexures, L.signer_name,
                           L.signer_designation, L.bse_code, L.nse_symbol]);
}
async function ddRunCheck() {
    const btn = document.getElementById('dd-check-btn');
    const box = document.getElementById('dd-check');
    const L = _gatherLetter();
    ddBusy(btn, true, 'Checking…');
    box.innerHTML = `<div class="dd-card p-5"><div class="flex items-center gap-3"><span class="dd-spin" style="color:var(--dd-accent)"></span>
        <div><p class="dd-h2">Checking the letter</p><p class="dd-tiny">Particulars against the company's records, dates, spelling, and every item SEBI requires for this filing. About 15 seconds.</p></div></div></div>`;
    const d = await ddApi('/api/drafting/check', {json: Object.assign({}, L, {use_ai: true, new_info: STATE.new_info || ''})});
    ddBusy(btn, false);
    if (d.error) { box.innerHTML = ddErrorHtml(d, 'The check'); return null; }
    DD.check = d.report; DD.checkStamp = ddLetterStamp(); DD.fixed = new Set(); DD.exportAnyway = null;
    ddRenderCheck('issues');
    return DD.check;
}

function ddRenderCheck(tab) {
    const box = document.getElementById('dd-check');
    if (!box) return;
    const R = DD.check;
    if (!R) {
        box.innerHTML = `<div class="dd-card p-5 flex flex-wrap items-center justify-between gap-3">
            <div><p class="dd-h2">Not checked yet</p><p class="dd-sub">Run the check before you export. It compares the CIN, address, codes and signatory with the company's own filings, catches impossible dates and misspellings, and reads the letter against SEBI's list for this filing.</p></div>
            <button class="dd-btn dd-btn-accent" onclick="ddRunCheck()">Check with AI</button></div>`;
        return;
    }
    const S = R.summary;
    const stale = DD.checkStamp !== ddLetterStamp();
    const scoreColor = S.blockers ? 'var(--dd-red)' : S.warnings ? 'var(--dd-amber)' : 'var(--dd-green)';
    const reqs = R.requirements || [];
    const applicable = reqs.filter(r => !['not_applicable'].includes(r.status) && r.verified !== false);
    const metN = applicable.filter(r => r.status === 'met' || r.status === 'likely').length;
    const active = tab || box.dataset.tab || 'issues';
    box.dataset.tab = active;
    const head = `<div class="flex flex-wrap items-center gap-4">
        <div class="dd-score" style="--dd-score:${S.score};--dd-score-c:${scoreColor}"><div>${S.score}</div></div>
        <div class="flex-1 min-w-[220px]">
            <p class="dd-h">${S.ready ? 'Ready to file' : `${S.blockers} thing${S.blockers === 1 ? '' : 's'} to fix before filing`}</p>
            <div class="flex flex-wrap gap-1.5 mt-1.5">
                <span class="dd-pill dd-pill-red">${S.blockers} blocking</span><span class="dd-pill dd-pill-amber">${S.warnings} warnings</span><span class="dd-pill dd-pill-blue">${S.suggestions} suggestions</span>
                ${applicable.length ? `<span class="dd-pill ${metN === applicable.length ? 'dd-pill-green' : ''}">SEBI items ${metN}/${applicable.length}</span>` : ''}
            </div>
            ${S.text ? `<p class="dd-sub mt-1.5">${ddEsc(S.text)}</p>` : ''}
            <p class="dd-tiny mt-1">${S.ai_used ? `AI review by ${ddEsc(S.model)}` : 'Rule checks only'} · ${new Date(S.checked_at).toLocaleTimeString()}</p>
        </div>
        <button class="dd-btn dd-btn-sm" onclick="ddRunCheck()">Re-check</button>
    </div>
    ${S.ai_error ? `<div class="mt-3">${ddErrorHtml(S.ai_error, 'AI review')}<p class="dd-tiny mt-1">The rule checks below still ran.</p></div>` : ''}
    ${stale ? `<div class="dd-note dd-note-amber mt-3"><span>!</span><div>The letter changed after this check. <span class="dd-link" onclick="ddRunCheck()">Re-check</span> before exporting.</div></div>` : ''}
    ${DD.exportAnyway ? `<div class="dd-note dd-note-red mt-3"><span>!</span><div><b>${S.blockers} blocking issue${S.blockers === 1 ? '' : 's'} remain.</b> Fix them first, or <span class="dd-link" onclick="ddExport('${DD.exportAnyway}', true)">export anyway</span>.</div></div>` : ''}`;

    const tabs = `<div class="dd-tabs my-4" role="tablist" style="max-width:520px">
        <button class="dd-tab" aria-selected="${active === 'issues'}" onclick="ddRenderCheck('issues')">Issues (${R.findings.length})</button>
        <button class="dd-tab" aria-selected="${active === 'reqs'}" onclick="ddRenderCheck('reqs')">SEBI requirements (${metN}/${applicable.length})</button>
        <button class="dd-tab" aria-selected="${active === 'facts'}" onclick="ddRenderCheck('facts')">Particulars</button></div>`;

    let content = '';
    if (active === 'issues') {
        content = R.findings.length ? R.findings.map(f => {
            const fixed = DD.fixed.has(f.id);
            const fix = f.fix && f.fix.replace !== undefined ? `<div class="dd-fix">${f.fix.find ? `<del>${ddEsc(f.fix.find.slice(0, 140))}</del> → ` : ''}<ins>${ddEsc((f.fix.replace || '(remove)').slice(0, 140))}</ins></div>` : '';
            return `<div class="dd-find ${f.severity} ${fixed ? 'fixed' : ''}">
                <div class="flex items-start justify-between gap-3"><div class="min-w-0">
                    <div class="flex flex-wrap items-center gap-1.5"><span class="dd-find-title">${ddEsc(f.title)}</span>
                    <span class="dd-pill">${ddEsc(f.where_label)}</span>${f.source === 'ai' ? '<span class="dd-pill dd-pill-yellow">AI</span>' : ''}</div>
                    ${f.detail && f.detail !== f.title ? `<p class="dd-find-detail">${ddEsc(f.detail)}</p>` : ''}
                    ${f.quote && !f.fix ? `<p class="dd-fix">In the letter: <span class="dd-quote">${ddEsc(f.quote.slice(0, 160))}</span></p>` : ''}${fix}
                </div>
                <div class="flex flex-col gap-1.5 flex-none">${f.fix && !fixed ? `<button class="dd-btn dd-btn-primary dd-btn-sm" onclick="ddApplyFix('${f.id}')">Apply fix</button>` : ''}${fixed ? '<span class="dd-pill dd-pill-green">Fixed</span>' : ''}
                    ${f.where !== 'details' ? `<button class="dd-btn dd-btn-sm" onclick="ddShowFinding('${f.id}')">Show</button>` : ''}</div></div></div>`;
        }).join('<div class="h-2"></div>') : '<div class="dd-note dd-note-green"><span>✓</span><div>No issues found.</div></div>';
    } else if (active === 'reqs') {
        const icon = {met: '✓', likely: '✓', partial: '!', missing: '✕', not_applicable: '–', unclear: '?', unchecked: '?'};
        const label = {met: 'Answered', likely: 'Looks covered (rule check)', partial: 'Partly answered', missing: 'Missing', not_applicable: 'Does not apply here', unclear: 'Could not confirm', unchecked: 'Needs the AI review'};
        content = `${R.requirements_derived ? '<div class="dd-note dd-note-amber mb-2"><span>!</span><div>No verified SEBI list for this filing type yet; these items come from Delta\'s template guidance.</div></div>' : ''}
            <div class="dd-card p-4">${reqs.map(r => `<div class="dd-reqrow"><span class="dd-reqicon ${r.status === 'not_applicable' ? 'na' : r.status}">${icon[r.status] || '?'}</span>
                <div><div class="flex flex-wrap items-center gap-1.5"><span class="text-[12.5px] font-semibold" style="color:var(--dd-ink)">${ddEsc(r.label)}</span><span class="dd-tiny">${label[r.status] || r.status}</span>${r.verified === false ? '<span class="dd-pill">to confirm</span>' : ''}</div>
                <p class="dd-tiny" style="color:var(--dd-ink-2)">${ddEsc(r.requirement)}</p>
                ${r.evidence ? `<p class="dd-fix">Draft says: <span class="dd-quote">${ddEsc(r.evidence.slice(0, 180))}</span></p>` : ''}
                ${r.note ? `<p class="dd-fix">${ddEsc(r.note)}</p>` : ''}
                <p class="dd-tiny mt-0.5">${ddEsc(r.source_ref)}${r.applies_when ? ` · applies if ${ddEsc(r.applies_when)}` : ''}</p></div></div>`).join('')}</div>
            ${(R.notes || []).length ? `<div class="dd-note mt-3"><span>⏱</span><div><b>Deadlines.</b> ${R.notes.map(ddEsc).join(' ')}</div></div>` : ''}
            ${(R.requirement_sources || []).length ? `<p class="dd-tiny mt-2">Sources: ${R.requirement_sources.map(s => `<a class="dd-link" href="${ddEsc(s.url)}" target="_blank" rel="noopener">${ddEsc(s.title)}</a>`).join(' · ')}</p>` : ''}`;
    } else {
        const pill = {match: ['Matches', 'dd-pill-green'], differs: ['Differs', 'dd-pill-red'], missing: ['Not on letter', 'dd-pill-amber'], unverified: ['No record to compare', '']};
        content = (R.facts || []).length ? `<div class="dd-card p-2 overflow-x-auto"><table class="dd-facts"><thead><tr><th>Particular</th><th>On this letter</th><th>Company records</th><th></th></tr></thead><tbody>
            ${R.facts.map(f => `<tr><td style="font-weight:600;color:var(--dd-ink)">${ddEsc(f.label)}</td><td>${ddEsc(f.in_draft || '—')}</td><td>${ddEsc(f.expected || '—')}${f.source ? `<div class="dd-tiny">${ddEsc(f.source)}</div>` : ''}</td>
                <td><span class="dd-pill ${(pill[f.status] || ['', ''])[1]}">${(pill[f.status] || [f.status])[0]}</span></td></tr>`).join('')}</tbody></table></div>`
            : '<p class="dd-sub">No company particulars on record yet. Learn the house style or add them in Draft memory, and the check will compare them.</p>';
    }
    box.innerHTML = `<div class="dd-card p-5">${head}${tabs}${content}</div>`;
}

function ddApplyFix(fid) {
    const f = DD.check && DD.check.findings.find(x => x.id === fid);
    if (!f || !f.fix) return;
    const {field, find, replace} = f.fix;
    const inputs = {cin: 'field-cin', registered_office: 'field-regoffice', company_name: 'field-company', signer_name: 'field-signer',
                    bse_code: 'field-bse', nse_symbol: 'field-nse', signer_designation: 'field-designation'};
    let ok = false;
    if (inputs[field]) {
        const el = document.getElementById(inputs[field]);
        if (el) { el.value = replace; ok = true; }
    } else if (field === 'subject') {
        const el = document.getElementById('field-subject');
        const v = ddReplaceFirst(el.value, find, replace);
        if (v !== null) { el.value = v; STATE.subject = v; ok = true; }
    } else if (field === 'body') {
        const t = _getEditorPlainText();
        let v = find ? ddReplaceFirst(t, find, replace) : null;
        if (v !== null) { v = v.replace(/\n{3,}/g, '\n\n'); _setEditorPlainText(v); STATE.body = v; ok = true; }
    } else if (field.startsWith('annexure:')) {
        _syncAnnexuresFromDOM();
        const i = parseInt(field.split(':')[1], 10);
        const a = STATE.annexures[i];
        if (a) {
            const vb = ddReplaceFirst(a.body || '', find, replace);
            const vt = vb === null ? ddReplaceFirst(a.title || '', find, replace) : null;
            if (vb !== null) { a.body = vb; ok = true; } else if (vt !== null) { a.title = vt; ok = true; }
            if (ok) renderAnnexureEditors();
        }
    }
    if (!ok) { ddToast('That text has changed since the check. Fix it in the editor, then re-check.'); return; }
    DD.fixed.add(fid);
    DD.checkStamp = ddLetterStamp();       // a fix the check proposed does not make the check stale
    ddScanFields(); ddHighlight();
    ddRenderPreview();
    ddRenderCheck();
}
function ddShowFinding(fid) {
    const f = DD.check && DD.check.findings.find(x => x.id === fid);
    if (!f) return;
    showStep(4);
    setTimeout(() => {
        if (f.where === 'body' && _quill && f.quote) {
            const i = _getEditorPlainText().indexOf(f.quote);
            if (i >= 0) { _quill.focus(); _quill.setSelection(i, f.quote.length, 'user'); }
        } else if (f.where.startsWith('annexure:')) {
            const el = document.querySelector(`[data-annx-body="${f.where.split(':')[1]}"]`);
            if (el) { el.focus(); const i = el.value.indexOf(f.quote); if (i >= 0) el.setSelectionRange(i, i + f.quote.length); el.scrollIntoView({block: 'center'}); }
        } else if (f.where === 'subject') {
            document.getElementById('field-subject').focus();
        }
    }, 120);
}

// ── Export ──────────────────────────────────────────────────────────────
async function downloadLetter(format) {
    if (!DD.check || DD.checkStamp !== ddLetterStamp()) {
        const r = await ddRunCheck();
        if (!r) return;
    }
    if (DD.check.summary.blockers > 0) {
        DD.exportAnyway = format;
        ddRenderCheck('issues');
        document.getElementById('dd-check').scrollIntoView({behavior: 'smooth', block: 'start'});
        return;
    }
    ddExport(format);
}
async function ddExport(format) {
    const L = _gatherLetter();
    const btn = document.getElementById(format === 'pdf' ? 'dl-pdf' : 'dl-docx');
    ddBusy(btn, true, 'Exporting…');
    let r;
    try {
        r = await fetch(`/api/draft/export/${format}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(L)});
    } catch (e) { ddBusy(btn, false); ddToast('Network error. Try again.'); return; }
    ddBusy(btn, false);
    if (!r.ok) { let m = 'Export failed.'; try { m = (await r.json()).message || m; } catch (e) {} ddToast(m); return; }
    const blob = await r.blob();
    const cd = r.headers.get('Content-Disposition') || '';
    const fname = (cd.match(/filename="([^"]+)"/) || [])[1] ||
        ((L.company_name || 'letter') + '_' + (L.subject || 'filing')).replace(/[^A-Za-z0-9_-]+/g, '_').slice(0, 80) + '.' + format;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = fname;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    DD.exportAnyway = null;
    ddRenderCheck();
    ddToast('Exported. Delta saved it as an example of how your team writes this filing.');
    ddLoadMemory(true);
}

// ── Wiring into the wizard ──────────────────────────────────────────────
function ddHookEditor() {
    if (DD.editorHooked || !_quill) return;
    DD.editorHooked = true;
    let t = null;
    _quill.on('text-change', (delta, old, source) => {
        if (source !== 'user') return;
        clearTimeout(t);
        t = setTimeout(() => { ddScanFields(); ddRenderFields(); ddHighlight(); ddRenderCover(); }, 700);
    });
}

const _ddShowStep = showStep;
showStep = function (n) {
    _ddShowStep(n);
    const btn = document.getElementById('dd-memory-btn');
    if (btn) btn.classList.toggle('hidden', !STATE.company);
    if (STATE.company && n >= 3) ddLoadMemory();
    if (n === 4) {
        _initQuillIfNeeded();
        ddHookEditor();
        ddLoadRequirements();
        ddPrefillDetails();
        const co = document.getElementById('field-company');
        if (co && !co.value.trim() && STATE.company) co.value = ddFact('legal_name') || STATE.company.name || STATE.company.symbol;
    }
};

const _ddSelectCompany = _selectCompany;
_selectCompany = function (symbol, name) {
    if (ddSym() && ddSym() !== (symbol || '').toUpperCase()) {
        ['field-company', 'field-regoffice', 'field-cin', 'field-bse', 'field-signer', 'field-designation'].forEach(id => {
            const el = document.getElementById(id); if (el) el.value = '';
        });
        DD.prefilledFor = null; DD.check = null;
    }
    _ddSelectCompany(symbol, name);
    ddLoadMemory();
};
