---
kind: frontend_style
name: Tailwind CSS + Multi-Theme Design System with Tokenized UI Primitives
category: frontend_style
scope:
    - '**'
source_files:
    - frontend/tailwind.config.js
    - frontend/src/styles/globals.css
    - frontend/src/stores/themeStore.ts
    - frontend/src/lib/ui-tokens.ts
    - frontend/src/components/ui/button.tsx
    - frontend/src/components/ui/input.tsx
    - frontend/src/components/ui/card.tsx
    - frontend/src/components/ui/table.tsx
    - frontend/sdk/src/theme.ts
    - frontend/sdk/src/styles/base.css
---

## Overview

The AI-DataHub frontend uses a **Tailwind CSS-first design system** layered on top of CSS custom properties (design tokens) to support multiple visual themes. The system is built around a shared token layer, a component primitive library, and reusable class-name bundles — plus an embeddable SDK that ships its own scoped theme.

## Core Stack

- **CSS framework**: Tailwind CSS (`tailwind.config.js`) with `@tailwindcss/typography` plugin.
- **Build tooling**: Vite (`vite.config.ts`, `postcss.config.js`).
- **State management for theming**: Zustand store (`src/stores/themeStore.ts`) persisted under the key `chatbi-theme`.
- **Component primitives**: A local shadcn-style UI kit in `frontend/src/components/ui/` (button, card, dialog, dropdown-menu, input, select, tabs, table, tooltip, etc.), each consuming Tailwind utility classes mapped to CSS variables.
- **Embeddable SDK**: A separate package under `frontend/sdk/` that exposes a Shadow DOM-based chat widget (`chatbi-chat.ts`, `chatbi-dashboard.ts`) with its own isolated styles (`sdk/src/styles/base.css`) and runtime theme injection (`sdk/src/theme.ts`).

## Design Tokens & Theme Architecture

### Token Layer (`frontend/src/styles/globals.css`)

All colors are defined as HSL CSS custom properties under `:root` and themed via descendant selectors. The default theme is Kibana-inspired dark; additional themes are applied by adding a class to `<html>`:

| Theme | Class | Primary hue | Notes |
|---|---|---|---|
| Default (dark) | none (default) | 217° blue | Kibana-inspired |
| Light | `.light` | 217° blue | High-key variant |
| Tech | `.tech` | 190° cyan/electric blue | Neon accents, glow effects |
| Finance | `.finance` | 38° gold/amber | Professional dark navy |
| Bento | `.bento` | 250° purple | Soft light cards, rounded grid |
| Glass | `.glass` | 200° blue | Frosted glass surfaces |
| AI-Native | `.ainative` | 180° teal / 270° purple | Deep-space neural style, glow animations |
| Medical | `.medical` | 186° teal | Clean medical-blue palette |
| DataFoundry | `.datafoundry` | near-black primary | Jewel-tone step/status palette |

Each theme defines the full set of tokens: `--background`, `--foreground`, `--card`, `--popover`, `--primary`, `--secondary`, `--muted`, `--accent`, `--destructive`, `--border`, `--input`, `--ring`, `--sidebar*`, `--chart-1..10`, `--radius`, plus theme-specific extras like `--kibana-*`, `--step-*`, `--ai-glow-*`, `--medical-*`, `--shadow-card`.

### Tailwind Mapping (`frontend/tailwind.config.js`)

Tailwind's color palette is extended to read from these CSS variables using `hsl(var(--name))`. This means every Tailwind color utility (`bg-primary`, `text-muted-foreground`, `border-border`, etc.) automatically adapts to the active theme. Border radius is also tokenized via `--radius` mapped to `lg/md/sm`.

### Theme Switching (`frontend/src/stores/themeStore.ts`)

A Zustand store exposes `useThemeStore` with `theme: ThemeId`, `isDark`, `setTheme()`, and `toggle()`. The `applyTheme()` function removes all known theme classes from `<html>` and adds the selected one (except `'dark'`, which is the default). Themes are persisted to localStorage under `chatbi-theme`.

## Component-Level Styling Conventions

### Primitive Components (`frontend/src/components/ui/*`)

Each primitive is a small React component that composes Tailwind utilities. For example, buttons use `bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-xs font-semibold hover:opacity-90 transition-colors duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-ring/20 disabled:opacity-50 disabled:cursor-not-allowed`. The same pattern applies across input, select, table, tabs, dialog, popover, skeleton, spinner, switch, badge, avatar, card, separator, and scroll-area.

### Reusable Class Bundles (`frontend/src/lib/ui-tokens.ts`)

To avoid repeating long Tailwind strings, the project centralizes common class combinations as exported constants:

- Buttons: `btnPrimaryClass`, `btnSecondaryClass`, `btnGhostClass`, `btnDangerClass`
- Panels/Cards: `panelShellClass`, `panelHoverClass`, `cardClass`
- Tables: `tableShellClass`, `tableHeaderClass`, `tableCellClass`, `tableNumericCellClass`
- Chips/Badges: `chipClass`, `badgeClass`
- Forms: `inputClass`, `textareaClass`, `selectClass`
- Typography/Layout: `labelClass`, `descriptionClass`, `sectionTitleClass`, `pageHeaderClass`, `dividerClass`

Status and agent-step coloring is provided via helper functions:
- `statusTone(status)` returns `{ bg, border, text }` classes using `--step-success/warning/error/inspect` tokens.
- `statusChipClass(status)` combines chip styling with status tones.
- `stepKindTone(kind)` maps agent step kinds (`inspect`, `query`, `transform`, `fetch`, `visualize`, `knowledge`, `success`, `warning`, `error`) to jewel-tone classes.

This file explicitly references "DataFoundry's ui-tokens.ts pattern" as inspiration.

### Global Styles (`frontend/src/styles/globals.css`)

Beyond tokens, this file contains:
- Base resets via `@layer base` (`border-border` applied to all elements, body typography).
- Scrollbar styling using theme variables.
- Dashboard canvas styles (grid background, chart cell layout, resize handles, pan cursors, selection highlights).
- Animation utilities (`fadeIn`, `slideUp`, `spin`, shimmer skeleton).
- Theme-specific overrides (e.g., `.tech .dashboard-chart-cell` gets glow shadows; `.glass` uses `backdrop-filter: blur`; `.bento` increases border-radius).
- Reduced-motion media query (`prefers-reduced-motion: reduce`).

### SDK Isolation (`frontend/sdk/`)

The SDK is a standalone distributable bundle. It does not depend on the main app's Tailwind setup:
- `sdk/src/styles/base.css` defines a self-contained stylesheet scoped to `:host` (Shadow DOM), using `--chatbi-*` custom properties.
- `sdk/src/theme.ts` exports a `ChatBITheme` interface and `defaultTheme`, plus an `applyTheme(host, theme?)` function that injects a `<style id="chatbi-theme">` into the host's ShadowRoot, reading any existing `--chatbi-*` variables from the page's root element as overrides.

## Responsive Strategy

Responsiveness is handled entirely through Tailwind's responsive prefixes (e.g., `sm:`, `md:`, `lg:`) — no custom media queries beyond reduced-motion. The dashboard canvas and layout components rely on flexbox/grid and percentage sizing rather than fixed breakpoints.

## Constraints Observed in Code

- All semantic colors must come from the CSS variable token layer (`--primary`, `--card`, `--muted`, etc.); hard-coded color values should be avoided in new components.
- New themes are added by defining a new CSS class selector in `globals.css` that redeclares the full token set, then registering it in the `ThemeId` union and `IS_DARK` map in `themeStore.ts`.
- Button and form primitives consistently apply `focus-visible:ring-2 focus-visible:ring-ring/20` for keyboard accessibility.
- Disabled states consistently use `disabled:opacity-50 disabled:cursor-not-allowed`.
- The SDK's Shadow DOM isolation means embedded widgets cannot leak styles into the host page and vice versa; theming is strictly via `--chatbi-*` variables.