# IRMS Design System

## 1. Atmosphere & Identity

IRMS is a quiet industrial command center for recipe, weighing, blend, and production records. The interface should feel precise, durable, and fast to scan during shop-floor work. The signature is structured density: muted blue surfaces, compact controls, clear numeric tables, and orange only for operational emphasis.

## 2. Color

### Palette

| Role | Token | Light | Dark | Usage |
|------|-------|-------|------|-------|
| Surface/page | --bg-page | #f1f4f9 | #101827 | App background |
| Surface/base | --bg-base | #ffffff | #172033 | Inputs and table bodies |
| Surface/primary | --bg-surface-100 | #ffffff | #1f293d | Panels and top bars |
| Surface/secondary | --bg-surface-200 | #fafbfd | #26324a | Filter wells and inactive controls |
| Surface/tertiary | --bg-surface-300 | #eef2f8 | #303d57 | Hover wells |
| Text/primary | --text-primary | #15233f | #f5f7fb | Headings and body |
| Text/secondary | --text-secondary | #56627b | #b7c0d1 | Supporting copy |
| Text/tertiary | --text-tertiary | #8c97ad | #7f8ba1 | Hints and disabled text |
| Border/subtle | --border-subtle | #e7ebf2 | #303d57 | Soft separators |
| Border/strong | --border-strong | #d6dce7 | #46536a | Inputs and defined edges |
| Brand/primary | --brand | #1b4079 | #8fb7f2 | Sidebar, active states |
| Brand/strong | --brand-strong | #143160 | #b9d2f8 | Primary hover |
| Brand/mid | --brand-mid | #2c5d9b | #6fa0df | Secondary brand state |
| Brand/soft | --brand-soft | #eaf1fb | #20344f | Subtle brand fill |
| Accent/operations | --accent-secondary | #f47c26 | #ff9a50 | Operational emphasis |
| Status/success | --status-success | #1e9d6b | #4ed49c | Success |
| Status/warning | --status-warning | #c98212 | #e7ad3f | Warnings |
| Status/error | --status-error | #d8453f | #ff746f | Errors |
| Status/info | --status-info | #2c5d9b | #6fa0df | Informational |

### Rules

- Use brand blue for navigation and committed actions.
- Use orange only for operational emphasis or secondary active tabs.
- Use status colors only when the state is semantic.
- Add new colors here before using them in CSS.

## 3. Typography

### Scale

| Level | Size | Weight | Line Height | Tracking | Usage |
|-------|------|--------|-------------|----------|-------|
| Display | 30px / 1.875rem | 800 | 1.15 | 0 | Major dashboard numbers |
| H1 | 21px / 1.3125rem | 800 | 1.2 | 0 | Page headings |
| H2 | 20px / 1.25rem | 700 | 1.3 | 0 | Panel titles |
| H3 | 16px / 1rem | 700 | 1.35 | 0 | Modal and card titles |
| Body | 14px / 0.875rem | 500 | 1.5 | 0 | Default UI text |
| Body/sm | 13px / 0.8125rem | 500 | 1.45 | 0 | Dense table text |
| Caption | 12px / 0.75rem | 600 | 1.4 | 0.02em | Labels and metadata |
| Overline | 11px / 0.6875rem | 700 | 1.3 | 0.04em | Section labels |

### Font Stack

- Primary: Pretendard, system Korean sans-serif stack.
- Mono: JetBrains Mono, Fira Code, Consolas, monospace.

### Rules

- Body text should not drop below 13px in operational tables or 14px in forms.
- Use tabular numeric figures for metric values, quantities, dates, and table numbers.
- Keep Korean UI labels direct and action-oriented.

## 4. Spacing & Layout

### Base Unit

All spacing derives from a base of 4px.

| Token | Value | Usage |
|-------|-------|-------|
| --space-1 | 4px | Tight inline gaps |
| --space-2 | 8px | Compact controls |
| --space-3 | 12px | List rows and control groups |
| --space-4 | 16px | Default panel rhythm |
| --space-5 | 20px | Sidebar brand and modal padding |
| --space-6 | 24px | Panel padding |
| --space-7 | 28px | Page horizontal padding |
| --space-8 | 32px | Major groups |
| --space-10 | 40px | Large section separation |
| --space-12 | 48px | Empty-state spacing |

### Grid

- Max content width: 1320px.
- Primary shell: fixed sidebar plus fluid content on desktop.
- Mobile breakpoint: 900px for drawer navigation, 768px for compact forms.

### Rules

- Dense dashboards use compact spacing, but controls still need 40px minimum touch targets on mobile.
- Table containers own horizontal scrolling; the page should not.

## 5. Components

### App Shell

- **Structure**: sidebar navigation, sticky topbar, constrained main content.
- **Variants**: desktop fixed sidebar, mobile drawer.
- **Spacing**: --space-3 to --space-7.
- **States**: active navigation, hover, focus, mobile open and closed.
- **Accessibility**: skip link, labeled navigation, aria-expanded on drawer button.
- **Motion**: drawer transform, 220ms ease.

### Panel

- **Structure**: section or article with optional title and subtitle.
- **Variants**: standard, alert, chart panel.
- **Spacing**: --space-4 to --space-6.
- **States**: no interactive state unless the panel itself is clickable.
- **Accessibility**: headings describe panel purpose.
- **Motion**: none.

### Button

- **Structure**: button or anchor with .btn.
- **Variants**: default, accent, success, danger, compact, icon.
- **Spacing**: --space-2 to --space-4.
- **States**: hover, active, focus-visible, disabled, loading.
- **Accessibility**: visible focus ring and descriptive labels.
- **Motion**: transform and color transitions only.

### Empty State

- **Structure**: centered copy with optional action.
- **Variants**: compact table state, full panel state.
- **Spacing**: --space-6 to --space-12.
- **States**: static.
- **Accessibility**: plain text, no decorative emoji.
- **Motion**: none.

## 6. Motion & Interaction

### Timing

| Type | Duration | Easing | Usage |
|------|----------|--------|-------|
| Micro | 120ms | ease-out | Button press |
| Standard | 180-240ms | ease-in-out | Hover, drawer, focus |
| Emphasis | 300ms | cubic-bezier(0.16, 1, 0.3, 1) | Toast entry |

### Rules

- Animate transform and opacity only.
- Respect prefers-reduced-motion for non-essential animation.
- Every interactive element needs hover, active, focus-visible, and disabled treatment where applicable.

## 7. Depth & Surface

### Strategy

Mixed, with tonal shift as the default and restrained shadows only for overlays, toasts, and elevated panels.

| Level | Value | Usage |
|-------|-------|-------|
| Subtle | 0 1px 2px rgba(21, 35, 63, 0.05) | Dense metric cards |
| Default | 0 8px 24px rgba(21, 35, 63, 0.10) | Panels and dropdowns |
| Prominent | 0 18px 48px rgba(21, 35, 63, 0.22) | Modals and drawers |

Borders remain visible on inputs, tables, and panel edges because IRMS is an operational data tool.
