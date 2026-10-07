# ORD AI Dashboard Design System

Scope: the local training dashboard served by `ord_ai.dashboard` (files in this folder).
Every color, size, radius and duration in `tokens.css` / `app.css` traces to this file.

## 0. Research Log

- Embedded refs: shortlisted `sentry.md`, `posthog.md`, `clickhouse.md` (data-dense dashboards) → picked
  `taste-skill.md` (Layer A, operational lane) + `sentry.md` (Layer B) because a dark, data-dense
  plum-black surface with one lime signal suits a monitor that sits beside a running game.
  taste-skill marks dashboards out of scope for its landing-page rules; only its color, contrast,
  anti-slop and reduced-motion rules apply here.
- Lazyweb: 3 queries (ML training metrics dashboard; game analytics live monitoring dark;
  experiment tracking loss curves), 4 screens viewed (MLflow experiment tracking, AppSignal metric
  dashboards, Langfuse metrics, Encord metric-impact bars) → grammar taken: a KPI number heads each
  card with a one-line caption; chart cards are titled by the metric with step on the x axis;
  categorical counts are sorted horizontal bars with labels on the left; the filter cluster (our run
  selector) sits at the top right of the header.
- StyleGallery: `sticky-header` (header stays above document scroll, no internal scroll container) +
  `ram-grid` (KPI tiles fill as many columns as fit) → the document owns scroll; the only nested
  scroller is the episode table (named job: long episode history, sticky column header).
- Imagen drafts: skipped, no image-generation tool in this session.
- ui-ux-db: not run; palette contrast was computed directly (Section 8).

Design read: operational monitoring dashboard for one developer running Warcraft III RL training on
their own machine, with a dark instrument-panel language, built with vanilla HTML/CSS/SVG and
Sentry-derived tokens. Dials: DESIGN_VARIANCE 3, MOTION_INTENSITY 2, VISUAL_DENSITY 7.

## 1. Atmosphere & Identity

A night-shift instrument panel. Deep plum-black surfaces separate by tone, hairline plum rules carry
the grid, numerals are set in a monospace face, and a single lime signal marks what is live. The
signature is the live minimap: a sunken well where lime unit blips sit on a faint map grid, the one
place the page looks like the game it watches.

## 2. Color

One theme (dark). Neutrals are one plum ramp from Sentry's purple-blacks to lavender white; state
washes are one ink (`--plum-50`) at rising alpha.

| Role | Token | Value | Usage |
|---|---|---|---|
| Ramp 950 | --plum-950 | #150f23 | Page canvas |
| Ramp 925 | --plum-925 | #120c1f | Sunken wells (charts, minimap) |
| Ramp 900 | --plum-900 | #1f1633 | Panel surface |
| Ramp 850 | --plum-850 | #271d40 | Raised: table head, select |
| Ramp 700 | --plum-700 | #362d59 | Hairlines, gridlines |
| Ramp 600 | --plum-600 | #4a3d73 | Strong rules, axes, control borders |
| Ramp 400 | --plum-400 | #8b7da8 | Faint text: axis ticks (4.6:1 on panel) |
| Ramp 300 | --plum-300 | #a99bc4 | Muted text: captions, labels (6.7:1) |
| Ramp 200 | --plum-200 | #cfc6e2 | Secondary text (10.5:1) |
| Ramp 50 | --plum-50 | #f5f2fa | Primary text, wash ink (15.6:1) |
| Signal | --lime-400 | #c2ef4e | Live state, policy-loss line, minimap blips, WIN (12.9:1) |
| Signal wash | --lime-950 | #26330f | Live badge fill |
| Series 2 | --violet-400 | #a89cf0 | Value-loss line, action bars, PLAYING, links (7.1:1) |
| Interactive | --violet-600 | #6a5fc1 | Control hover edge (non-text, 3.3:1) |
| Warm | --coral-400 | #ffb287 | Terminal updates, LOSS, offline (9.8:1) |
| Warm wash | --coral-950 | #3a2127 | Offline badge fill |
| Focus | --pink-400 | #fa7faa | `focus-visible` ring only |
| Wash hover | --wash-1 | rgb(245 242 250 / 0.05) | Row / control hover |
| Wash selected | --wash-2 | rgb(245 242 250 / 0.08) | Selected row (plus a text badge) |
| Wash active | --wash-3 | rgb(245 242 250 / 0.12) | Pressed control |

Semantic aliases (`--surface-*`, `--text-*`, `--line*`, `--status-*`, `--series-*`,
`--accent-wash` = lime-950, `--warn-wash` = coral-950, `--interactive-edge` = violet-600,
`--link`, `--focus`) map onto these in `tokens.css`. Status badges tint their own text color at
16% (`color-mix`) instead of adding a color.

Rules:
- Lime marks live state and the primary series only; it never decorates.
- Status is never color alone: every colored bar or badge has a text label nearby or in the table.
- Sentry's "no lime with coral in one component" rule yields to data: an episode chart may show
  WIN (lime) and LOSS (coral) bars side by side.

## 3. Typography

| Level | Token | Size | Weight | Line height | Usage |
|---|---|---|---|---|---|
| KPI | --size-kpi | 1.75rem | 600 | 1.1 | KPI numbers (mono, tabular) |
| H2 | --size-h2 | 1.25rem | 600 | 1.3 | Section titles |
| H1 | --size-h1 | 1.125rem | 700 | 1.25 | Page title in the top bar |
| Value | --size-value | 1.125rem | 500 | 1.3 | Live fact values (mono) |
| H3 | --size-h3 | 0.9375rem | 600 | 1.35 | Panel and chart titles |
| Body | --size-body | 0.9375rem | 400 | 1.6 | Default text |
| Body/sm | --size-sm | 0.875rem | 400 | 1.5 | Tables, field values |
| Label | --size-label | 0.8125rem | 500 | 1.4 | Field labels, KPI captions |
| Caption | --size-caption | 0.75rem | 500 | 1.4 | Axis ticks, footnotes |

Font stack:
- Sans: "Pretendard", "Noto Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", "Segoe UI", system-ui, sans-serif
- Mono: "Cascadia Mono", "JetBrains Mono", "D2Coding", Consolas, ui-monospace, monospace

Rules: locally installed fonts only (no font downloads). Every number is mono with
`font-variant-numeric: tabular-nums`. Korean labels get no letter-spacing and no uppercase.

## 4. Spacing & Layout

Base unit 4px: --space-1 4px, -2 8px, -3 12px, -4 16px, -5 20px, -6 24px, -8 32px, -10 40px,
-12 48px.

- Content max width --content-max 82rem, centered; gutter `clamp(1rem, 3vw, 2rem)` (mechanics).
- Fixed chart and region sizes: --chart-bars 15rem, --chart-line 11rem, --map-max 28rem (square),
  --table-max 22rem (tall table regions). Wells have a fixed size so redrawing an SVG never
  resizes its own ResizeObserver target.
- Breakpoints: sm 640px, md 768px, lg 1024px.
- Scroll owner: the document. The top bar is sticky (`sticky-header`). The episode table wrapper
  owns its own vertical scroll (max 22rem) with a sticky column header; it is keyboard focusable.
- ≥1024px: episodes split chart | table (5 : 7); learning curves | action distribution side by
  side; live facts | minimap side by side. <1024px everything stacks in DOM order.
- KPI tiles use `ram-grid` (`repeat(auto-fit, minmax(min(8rem, 100%), 1fr))`): five across from
  768px, two across on phones, where the fifth tile spans the last row.
- DOM order = reading order = focus order: 수집 데이터, 에피소드, 학습 곡선, 행동 분포, 실시간 상태.

## 5. Components

### Top bar
- Structure: `header.topbar > .topbar__inner > .brand(mark + h1) + p.conn + label.field(select) + p.refresh`
- Spacing: block padding --space-3, inline gutter; gaps --space-2 / --space-4.
- States: sticky at all widths. Below 768px two rows (brand + status; selector + refresh time);
  from 768px one row, visual order brand, selector, status, time via `order` (DOM order unchanged,
  and the status is not focusable, so focus order is unaffected).
- Depth: frosted plum (`--surface-glass` + blur 12px saturate 160%) + bottom hairline + ambient shadow.
- Layout: `sticky-header`, wrapping flex row.

### Select (run selector)
- Structure: `label.field > span.field__label + select.select`
- States: default (plum-850, plum-600 edge), hover (violet-600 edge), focus-visible (pink ring),
  disabled (before first data, 55% opacity), active (wash-3).
- Accessibility: native select, visible label, keyboard native.

### Connection status
- Structure: `p.conn[role=status][data-state] > span.conn__dot + span.conn__text`
- States: loading ("연결 중", plum), online ("실시간", lime dot), offline ("오프라인", coral dot +
  coral wash), error ("오류 HTTP n", coral). Text changes only on state transitions, so the live
  region is announced only then; the refresh time sits outside it.

### Panel
- Structure: `div.panel > (.panel__head: h3 + .panel__meta) + body`
- Spacing: --space-4 (mobile) / --space-5 padding; radius --radius-md.
- Depth: plum-900 fill + 1px --line + top rim `inset 0 1px 0 --wash-1` + a 3% top sheen gradient.

### KPI tile
- Structure: `div.kpi > dt.kpi__label + dd.kpi__value + dd.kpi__note`
- States: value, empty ("-" in faint text).
- Layout: `ram-grid` inside `dl.kpis`.

### Data table
- Structure: `div.table-scroll[tabindex=0][role=region][aria-label] > table.table > thead + tbody`
- States: row hover (wash-1), selected run row (wash-2 + "보는 중" badge, `aria-current`), empty
  (single row with the empty message).
- Numbers right-aligned mono; text left; header cells stay sans. Action names wrap; a fusion's
  rarity (`- 안흔함`) becomes a muted caption tag after the unit name that wraps as one piece.
- Scroll edges: `background-attachment: local` covers over `scroll` shades (`--scroll-shade`), so a
  shadow shows only where more table is hidden. Flush panels use a flat fill to match the covers.

### Badge
- Structure: `span.badge.badge--{live|idle|loss|win|playing|waiting}`
- Pill (--radius-full), caption type, tinted fill for live/offline only; others text + outline.

### Chart frame
- Structure: `figure.chart > figcaption (h3 + summary text) + div.well > svg[role=img][aria-label]`
  + `details.alt > summary + table` (text alternative).
- SVG is rebuilt at the container's pixel width (ResizeObserver) so tick text stays at caption size.
- Marks: bars `rect.mark`, lines `path.line`, terminal points `circle.mark--terminal`; each mark has
  an SVG `<title>` for pointer users.
- States: data, empty (`p.empty` in place of the well).

### Minimap
- Structure: chart frame with a square well; equal x/y scale; faint grid; `circle.blip` per unit.
- States: units, no units in the latest row, no telemetry.

### Empty state
- Structure: `p.empty` (muted text, dashed hairline box) saying what is missing and where it would
  come from.

## 6. Motion & Interaction

| Type | Duration | Easing | Usage |
|---|---|---|---|
| Micro | --dur-micro 120ms | ease-out | Wash and edge color changes on hover/press |
| Scroll | smooth | - | Skip link and in-page anchor jumps |

Rules:
- Only color/opacity transitions; nothing moves or resizes. Data updates swap in place without
  animation, and a section is re-rendered only when its data changed.
- `prefers-reduced-motion: reduce` turns smooth scrolling off; color fades stay.
- No loading spinner: the first fetch is local and sub-second.

## 7. Depth & Surface

Strategy: mixed. Tonal layering (canvas 950 → panel 900 → raised 850) with 1px hairlines; wells sink
with an inset shadow; only the sticky top bar casts a shadow.

| Level | Value | Usage |
|---|---|---|
| Sunken | inset 0 1px 3px rgb(8 5 15 / 0.6) | Chart and minimap wells |
| Rim | inset 0 1px 0 var(--wash-1) | Panel top edge |
| Ambient | 0 8px 24px rgb(8 5 15 / 0.45) | Sticky top bar |
| Scroll shade | --scroll-shade rgb(8 5 15 / 0.7) | Edge of a table that scrolls further |

Page atmosphere: two fixed radial glows (violet top-left, faint lime top-right) on a
`pointer-events: none` layer behind content.

| Token | Value | Usage |
|---|---|---|
| --radius-xs | 2px | Legend swatches, bar ends (marks, not surfaces) |
| --radius-sm | 6px | Select, badges' inner chips, table-scroll corners |
| --radius-md | 12px | Panels, KPI tiles |
| --radius-full | 999px | Badges, status dot |

Wells inside a 12px panel with 16-20px padding are square (concentric rule: 12 - 16 < 0).

## 8. Accessibility Constraints & Accepted Debt

### Constraints
- WCAG 2.2 AA: text ≥ 4.5:1 (lowest used: --plum-400 axis ticks 4.6:1 on panel), non-text marks ≥ 3:1.
- Landmarks: banner, main, contentinfo; each section is a region labelled by its h2.
- Keyboard: skip link, run select, episode table region (tabindex 0), every `details` summary.
- Every chart: `role="img"` + `aria-label` summary + a `details` table with the same numbers.
- Visible `focus-visible` ring (2px --focus, 2px offset) on every interactive element.
- Connection changes are announced via `role="status"`; the 5-second clock is not.
- Responsive to 320px with no horizontal page scroll; tables scroll inside their own region.

### Accepted Debt
| Item | Location | Why accepted | Owner / Exit |
|---|---|---|---|
| Dark theme only | whole page | Operational tool used beside the game at night; one theme halves QA | Lead; add light tokens if requested |
| Screen-reader pass inferred from markup, not exercised with NVDA | whole page | No assistive tech in this session | Lead; run NVDA before sharing wider |
| Independent visual-QA oracle not run | QA | No delegation tool in the build session | Lead; run /visual-qa with reviewers |
| Full re-parse of transitions.jsonl per poll | dashboard.py | 1 MB parses in ~8 ms today | Add tail-offset reading past ~100k rows |
| Lighthouse not measured | whole page | lighthouse + chrome-launcher are not installed and a global npm install is outside the build task | Lead; run the perfection audit once installed |
| Layout shift when the first summary arrives | all sections | The skeleton is empty until the first local fetch (tens of ms) | Measure CLS with the audit above |
