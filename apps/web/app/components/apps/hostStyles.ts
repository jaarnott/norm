/**
 * The style contract an app gets from its host (AppRunner's srcdoc).
 *
 * An app runs in a sandbox on an opaque origin, so it cannot load Norm's
 * stylesheet or fonts. Instead the host injects, ahead of the app's markup:
 *
 *   - `--norm-*` tokens: Norm's palette, type scale and radii. Same values as
 *     styles/tokens.css (hostStyles.test.ts keeps them in step).
 *   - element defaults inside `:where()` — zero specificity, so any rule the
 *     app writes for itself wins.
 *   - opt-in `.n-*` classes (buttons, badges, tables, tabs, fields, cards),
 *     matching the web app's own.
 *   - `<html data-norm-variant="page|embedded|standalone">`: page = a menu
 *     page, embedded = a card in a conversation, standalone = /apps/<slug>.
 *     The host draws the app's title in every variant, so an app hides its
 *     own heading under `html[data-norm-variant]`.
 *
 * The body carries the page gutter, so an app needs no outer padding of its
 * own and lines up with the page header above it.
 */

/** Light palette and scale, keyed by the tokens.css name (without `--`). */
export const HOST_TOKENS: Record<string, string> = {
  'brand': '#a08060',
  'brand-soft': '#c4a882',
  'accent': '#7d6234',
  'accent-soft': '#f4e8d8',
  'accent-strong': '#5c4a26',
  'canvas': '#faf8f5',
  'bg': '#ffffff',
  'surface': '#fbf9f6',
  'surface-alt': '#f4efe8',
  'selected': '#ebe3d8',
  'line': '#e2ddd7',
  'line-soft': '#f0ebe5',
  'line-strong': '#d6cec3',
  'field': '#998e7f',
  'text': '#1a1a1a',
  'text-soft': '#5c554d',
  'muted': '#69615a',
  'muted-soft': '#b0aca4',
  'icon': '#878076',
  'primary': '#1a1a1a',
  'primary-hover': '#33302b',
  'on-primary': '#ffffff',
  'focus': '#7d6234',
  'ok': '#2f6b40', 'ok-bg': '#e8f2ea',
  'warn': '#8a5a1f', 'warn-bg': '#fbf1de',
  'error': '#a93a2a', 'error-bg': '#fbe9e5',
  'info': '#3f5f80', 'info-bg': '#ecf1f6',
  'radius-sm': '6px',
  'radius': '8px',
  'radius-lg': '12px',
  'fs-2xs': '0.6875rem',
  'fs-xs': '0.75rem',
  'fs-sm': '0.8125rem',
  'fs-base': '0.875rem',
  'fs-md': '1rem',
  'fs-lg': '1.125rem',
  'fs-xl': '1.25rem',
  'fs-2xl': '1.75rem',
};

const tokens = Object.entries(HOST_TOKENS).map(([k, v]) => `--norm-${k}: ${v};`).join('\n    ');

export const HOST_CSS = `
  :root {
    color-scheme: light;
    --norm-font: 'Figtree', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    ${tokens}
    --norm-gutter: 24px;
  }
  @media (pointer: coarse) {
    :root { --norm-fs-2xs: 0.75rem; --norm-fs-xs: 0.8125rem; --norm-fs-sm: 0.875rem; --norm-fs-base: 0.9375rem; }
  }
  @media (max-width: 640px) { :root { --norm-gutter: 16px; } }
  html[data-norm-variant="embedded"] { --norm-gutter: 14px; }

  :where(*, *::before, *::after) { box-sizing: border-box; }
  :where(html) { -webkit-text-size-adjust: 100%; }
  :where(body) {
    margin: 0;
    padding: 0 var(--norm-gutter) var(--norm-gutter);
    font-family: var(--norm-font);
    font-size: var(--norm-fs-base);
    line-height: 1.45;
    color: var(--norm-text);
    background: transparent;
    -webkit-font-smoothing: antialiased;
  }
  html[data-norm-variant="embedded"] :where(body) { padding-top: 12px; }
  :where(button, input, select, textarea) { font: inherit; color: inherit; }
  :where(a) { color: var(--norm-accent); text-underline-offset: 2px; }
  :where(h1, h2, h3, h4) { color: var(--norm-text); line-height: 1.25; }
  :where(table) { border-collapse: collapse; }
  :where(:focus-visible) { outline: 2px solid var(--norm-focus); outline-offset: 2px; }

  .n-btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 34px; padding: 0 14px;
    border: 1px solid transparent; border-radius: var(--norm-radius); font: inherit; font-size: var(--norm-fs-base);
    font-weight: 500; line-height: 1; white-space: nowrap; cursor: pointer; text-decoration: none; }
  .n-btn:disabled { opacity: 0.45; cursor: default; }
  .n-btn--sm { height: 30px; padding: 0 12px; font-size: var(--norm-fs-sm); }
  .n-btn--primary { background: var(--norm-primary); color: var(--norm-on-primary); font-weight: 600; }
  .n-btn--primary:hover:not(:disabled) { background: var(--norm-primary-hover); }
  .n-btn--secondary { background: var(--norm-bg); color: var(--norm-text); border-color: var(--norm-line-strong); }
  .n-btn--secondary:hover:not(:disabled) { background: var(--norm-surface-alt); }
  .n-btn--quiet { background: transparent; color: var(--norm-text-soft); }
  .n-btn--quiet:hover:not(:disabled) { background: var(--norm-surface-alt); color: var(--norm-text); }
  .n-btn--danger { background: var(--norm-bg); color: var(--norm-error); border-color: #e8c5bd; }
  .n-btn--danger:hover:not(:disabled) { background: var(--norm-error-bg); }
  .n-btn--link { height: auto; padding: 0; border: none; background: none; color: var(--norm-accent); text-decoration: underline; }
  @media (pointer: coarse) { .n-btn:not(.n-btn--link) { min-height: 40px; } }

  .n-badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px;
    font-size: var(--norm-fs-2xs); font-weight: 600; line-height: 16px; white-space: nowrap;
    background: var(--norm-line-soft); color: var(--norm-text-soft); }
  .n-badge--ok { background: var(--norm-ok-bg); color: var(--norm-ok); }
  .n-badge--warn { background: var(--norm-warn-bg); color: var(--norm-warn); }
  .n-badge--error { background: var(--norm-error-bg); color: var(--norm-error); }
  .n-badge--info { background: var(--norm-info-bg); color: var(--norm-info); }
  .n-badge--accent { background: var(--norm-accent-soft); color: var(--norm-accent); }

  .n-card { background: var(--norm-bg); border: 1px solid var(--norm-line); border-radius: var(--norm-radius-lg); }

  .n-table { width: 100%; border-collapse: collapse; font-size: var(--norm-fs-base); font-variant-numeric: tabular-nums; }
  .n-table th { padding: 10px 12px; background: var(--norm-surface-alt); border-bottom: 1px solid var(--norm-line-strong);
    text-align: left; font-size: var(--norm-fs-xs); font-weight: 600; color: var(--norm-text-soft); white-space: nowrap; }
  .n-table td { padding: 10px 12px; border-bottom: 1px solid var(--norm-line); }
  .n-table tbody tr:hover > td { background: var(--norm-surface-alt); }
  .n-table .num { text-align: right; }

  .n-tabs { display: flex; gap: 22px; overflow-x: auto; white-space: nowrap; box-shadow: inset 0 -1px 0 var(--norm-line); scrollbar-width: none; }
  .n-tabs::-webkit-scrollbar { display: none; }
  .n-tab { flex: 0 0 auto; padding: 10px 0; border: none; background: none; font: inherit; font-size: var(--norm-fs-base);
    font-weight: 500; color: var(--norm-text-soft); cursor: pointer; }
  .n-tab:hover { color: var(--norm-text); }
  .n-tab[aria-selected="true"], .n-tab.is-active { color: var(--norm-text); font-weight: 600; box-shadow: inset 0 -2px 0 var(--norm-brand-soft); }

  .n-input, .n-select { height: 34px; padding: 0 10px; border: 1px solid var(--norm-field); border-radius: var(--norm-radius);
    background: var(--norm-bg); color: var(--norm-text); font: inherit; font-size: var(--norm-fs-base); }
  textarea.n-input { height: auto; padding: 8px 10px; resize: vertical; }
  .n-input::placeholder { color: var(--norm-muted); }
  .n-label { display: block; margin-bottom: 4px; font-size: var(--norm-fs-sm); font-weight: 500; color: var(--norm-text-soft); }
  @media (pointer: coarse) { .n-input:not(textarea), .n-select { height: 40px; } }
`;
