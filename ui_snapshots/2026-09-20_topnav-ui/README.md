# UI snapshot — 2026-09-20 "top-nav" interface

Prakhar approved this UI/UX. Keep it as the reference look.

## What this is
Full copy of `templates/` and `static/` as deployed on 2026-09-20.

## Characteristics
- Horizontal fixed **top navigation** (64px) — replaced the old vertical sidebar.
  Nav: Dashboard · Draft · Disclosures · Stock Prices · DIN Search · Compliance · Compare,
  with Watchlist dropdown, theme toggle, settings and user menu on the right.
- Consistent **indigo/blue palette** (no amber/violet/rainbow accents).
- Dashboard: gradient KPI cards, 12-month disclosure-activity area chart,
  "Today's movers", watchlist overview table, sector pulse.
- Dark mode supported throughout. No emojis (professional/boardroom tone).
- Layout rule that makes it work: `.flex.h-screen { margin-top:64px; height:calc(100vh - 64px) }`
  in `templates/sidebar.html` — the shell is a BFC so the margin doesn't collapse,
  and `<main>` becomes the scroll container, so any inner `sticky top-0` sits BELOW the bar.

## To restore
    cp -R ui_snapshots/2026-09-20_topnav-ui/templates ./templates
    cp -R ui_snapshots/2026-09-20_topnav-ui/static ./static

## Related
`_backup_pre_redesign/` holds the OLDER vertical-sidebar version (pre-top-nav).
