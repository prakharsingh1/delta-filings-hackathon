/* Ivory homepage: navigation, product stage, filing diff, obligation mapper,
   governance-year timeline, FAQ. Progressive: the page reads without it. */
(() => {
  'use strict';
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── Header + mobile navigation ─────────────────────────────────────── */
  const header = $('[data-header]');
  const burger = $('.burger');
  const mobileNav = $('#mobile-nav');
  if (header) {
    const onScroll = () => header.classList.toggle('is-stuck', window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }
  if (burger && mobileNav) {
    const setOpen = (open) => {
      mobileNav.hidden = !open;
      burger.setAttribute('aria-expanded', String(open));
      burger.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
      document.body.classList.toggle('nav-open', open);
    };
    burger.addEventListener('click', () => setOpen(mobileNav.hidden));
    $$('a', mobileNav).forEach((a) => a.addEventListener('click', () => setOpen(false)));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !mobileNav.hidden) { setOpen(false); burger.focus(); }
    });
    window.matchMedia('(min-width: 981px)').addEventListener('change', (e) => { if (e.matches) setOpen(false); });
  }

  /* ── Product stage tabs ─────────────────────────────────────────────── */
  const stage = $('[data-stage]');
  if (stage) {
    const tabs = $$('[role="tab"]', stage);
    const panels = $$('[role="tabpanel"]', stage);
    const select = (tab, focus = false) => {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute('aria-selected', String(on));
        t.tabIndex = on ? 0 : -1;
      });
      panels.forEach((p) => { p.hidden = p.id !== tab.getAttribute('aria-controls'); });
      if (focus) tab.focus();
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener('click', () => select(tab));
      tab.addEventListener('pointerenter', () => { const img = new Image(); img.src = tab.dataset.src; });
      tab.addEventListener('keydown', (e) => {
        const dir = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (dir) { e.preventDefault(); select(tabs[(i + dir + tabs.length) % tabs.length], true); }
        if (e.key === 'Home') { e.preventDefault(); select(tabs[0], true); }
        if (e.key === 'End') { e.preventDefault(); select(tabs[tabs.length - 1], true); }
      });
    });
  }

  /* ── Filing diff: previous / changes / current ──────────────────────── */
  const diff = $('[data-diff]');
  if (diff) {
    const buttons = $$('[data-view]', diff);
    buttons.forEach((b) => b.addEventListener('click', () => {
      buttons.forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      diff.dataset.view = b.dataset.view;
    }));
    diff.dataset.view = 'diff';
  }

  /* ── Obligation mapper: replays rulebook.applies() in the browser ───── */
  const mapper = $('[data-mapper]');
  const rulesNode = $('#rules-data');
  if (mapper && rulesNode) {
    let rules = [];
    try { rules = JSON.parse(rulesNode.textContent); } catch (e) { rules = []; }
    const ORDER = ['Companies Act', 'SEBI', 'Income Tax', 'GST', 'Labour', 'FEMA / RBI', 'LLP Act'];
    const CLASS = { 'Companies Act': 'r-ca', 'SEBI': 'r-sebi', 'Income Tax': 'r-it', 'GST': 'r-gst', 'Labour': 'r-lab', 'FEMA / RBI': 'r-fema', 'LLP Act': 'r-llp' };
    const typeButtons = $$('[data-type]', mapper);
    const stateSelect = $('select[name="state"]', mapper);
    const countOut = $('[data-count-out]', mapper);
    const splitOut = $('[data-split-out]', mapper);
    const barOut = $('[data-bar-out]', mapper);
    const legendOut = $('[data-legend-out]', mapper);
    let entityType = 'listed';

    const applies = (rule, entity) => {
      if (!rule.types.includes(entity.type)) return false;
      const f = entity.flags;
      if (rule.flags.some((k) => !f[k])) return false;
      if (rule.any.length && !rule.any.some((k) => f[k])) return false;
      if (rule.not.some((k) => f[k])) return false;
      if (rule.states.length && !rule.states.includes(entity.state)) return false;
      return true;
    };

    const render = () => {
      const flags = {};
      $$('input[type="checkbox"]', mapper).forEach((c) => { flags[c.name] = c.checked; });
      const entity = { type: entityType, state: stateSelect ? stateSelect.value : 'Maharashtra', flags };
      const hits = rules.filter((r) => applies(r, entity));
      const byRegime = {};
      hits.forEach((r) => { byRegime[r.regime] = (byRegime[r.regime] || 0) + 1; });
      const events = hits.filter((r) => r.kind === 'event').length;
      countOut.textContent = String(hits.length);
      splitOut.textContent = `${hits.length - events} recurring · ${events} event-driven`;
      barOut.innerHTML = '';
      legendOut.innerHTML = '';
      ORDER.forEach((name) => {
        const n = byRegime[name];
        if (!n) return;
        const seg = document.createElement('i');
        seg.className = CLASS[name];
        seg.style.flexGrow = String(n);
        seg.title = `${name}: ${n}`;
        barOut.appendChild(seg);
        const li = document.createElement('li');
        li.innerHTML = `<i class="${CLASS[name]}" aria-hidden="true"></i><span>${name}</span><b>${n}</b>`;
        legendOut.appendChild(li);
      });
      if (!reduceMotion) {
        countOut.classList.remove('tick');
        void countOut.offsetWidth;
        countOut.classList.add('tick');
      }
    };

    typeButtons.forEach((b) => b.addEventListener('click', () => {
      entityType = b.dataset.type;
      typeButtons.forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      render();
    }));
    mapper.addEventListener('change', render);
    render();
  }

  /* ── Governance-year timeline: regime filter + tooltip ──────────────── */
  const gantt = $('[data-gantt]');
  const filter = $('[data-year-filter]');
  if (gantt && filter) {
    const rows = $$('.gantt-row', gantt);
    const tip = $('.gantt-tip', gantt);
    $$('button', filter).forEach((b) => b.addEventListener('click', () => {
      $$('button', filter).forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      const regime = b.dataset.regime;
      rows.forEach((r) => { r.hidden = regime !== 'all' && r.dataset.regime !== regime; });
    }));
    const show = (mark) => {
      tip.innerHTML = `<b>${mark.dataset.name}</b><span>${mark.dataset.form} · ${mark.dataset.law}</span><em>${mark.dataset.tip}</em>`;
      tip.hidden = false;
      const g = gantt.getBoundingClientRect();
      const m = mark.getBoundingClientRect();
      const left = Math.min(Math.max(m.left + m.width / 2 - g.left, 110), g.width - 110);
      tip.style.left = `${left}px`;
      tip.style.top = `${m.top - g.top}px`;
    };
    const hide = () => { tip.hidden = true; };
    $$('.mark', gantt).forEach((mark) => {
      mark.addEventListener('pointerenter', () => show(mark));
      mark.addEventListener('focus', () => show(mark));
      mark.addEventListener('pointerleave', hide);
      mark.addEventListener('blur', hide);
      mark.addEventListener('click', (e) => { e.preventDefault(); show(mark); });
    });
    gantt.addEventListener('scroll', hide, true);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
  }

  /* ── FAQ: one answer open at a time ─────────────────────────────────── */
  const faq = $('[data-faq]');
  if (faq) {
    const items = $$('.qa', faq);
    items.forEach((item) => {
      const q = $('.q', item);
      const a = $('.a', item);
      q.addEventListener('click', () => {
        const open = q.getAttribute('aria-expanded') === 'true';
        items.forEach((other) => {
          const oq = $('.q', other), oa = $('.a', other);
          oq.setAttribute('aria-expanded', 'false'); oa.hidden = true; other.classList.remove('open');
        });
        if (!open) { q.setAttribute('aria-expanded', 'true'); a.hidden = false; item.classList.add('open'); }
      });
    });
  }

  /* ── Big-number count-up + section reveal ───────────────────────────── */
  const bigNumber = $('[data-count]');
  if (bigNumber && !reduceMotion && 'IntersectionObserver' in window) {
    const target = Number(bigNumber.dataset.count) || 0;
    const fmt = (n) => n.toLocaleString('en-IN');
    const io = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      io.disconnect();
      const start = performance.now(), dur = 1100;
      const frame = (t) => {
        const p = Math.min(1, (t - start) / dur);
        const eased = 1 - Math.pow(1 - p, 3);
        bigNumber.textContent = fmt(Math.round(target * eased));
        if (p < 1) requestAnimationFrame(frame);
      };
      requestAnimationFrame(frame);
    }, { threshold: 0.6 });
    io.observe(bigNumber);
  }
  if (!reduceMotion && 'IntersectionObserver' in window) {
    const targets = $$('.step, .panel, .callout, .role, .year-feats article, .proof-numbers, .proof-statement, .what-copy, .what-art');
    targets.forEach((el) => el.classList.add('reveal'));
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
    targets.forEach((el) => io.observe(el));
  }
})();
