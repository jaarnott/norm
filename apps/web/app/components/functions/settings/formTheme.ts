/**
 * A web form's theme tokens and the builder's preview of them. Tokens are
 * plain values the embed applies (F5-api.md §5: no `;{}<>`, applied through
 * element.style, never a built stylesheet). forms.js reads snake_case names
 * (input_radius, button_background…) plus the shorthands primary,
 * brand_color, accent, text, font and radius; the forms imported from Orbit
 * still carry Orbit's camelCase names (inputRadius), which forms.js maps to
 * the same — so the preview reads both spellings.
 *
 * React style objects are element.style, so the preview obeys the same rule.
 */
import type { CSSProperties } from 'react';

export type Theme = Record<string, string | number>;

const NUMBER = /^-?\d+(\.\d+)?$/;

/** A size token: a bare number means pixels ("14" → "14px"). */
export function px(value: string | number | undefined | null): string | undefined {
  if (value === undefined || value === null || value === '') return undefined;
  const s = String(value).trim();
  return NUMBER.test(s) ? `${s}px` : s;
}

/** "inputRadius" → "input_radius". */
export function snake(key: string): string {
  return key.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase();
}

/** The theme with every key in forms.js's snake_case spelling (a snake_case
 *  key wins over its camelCase twin). */
function normalised(theme: Theme | null | undefined): Theme {
  const out: Theme = {};
  for (const [k, v] of Object.entries(theme ?? {})) if (snake(k) !== k) out[snake(k)] = v;
  for (const [k, v] of Object.entries(theme ?? {})) if (snake(k) === k) out[k] = v;
  return out;
}

function str(value: string | number | undefined): string | undefined {
  if (value === undefined || value === null) return undefined;
  const s = String(value).trim();
  return s || undefined;
}

function weight(value: string | number | undefined): CSSProperties['fontWeight'] {
  const s = str(value);
  if (!s) return undefined;
  return NUMBER.test(s) ? Number(s) : (s as CSSProperties['fontWeight']);
}

function transform(value: string | number | undefined): CSSProperties['textTransform'] {
  const s = str(value);
  return s && ['none', 'uppercase', 'lowercase', 'capitalize'].includes(s) ? (s as CSSProperties['textTransform']) : undefined;
}

/** "inputBorderColor" / "brand_color" → "Input border color" / "Brand color". */
export function tokenLabel(key: string): string {
  const words = key
    .replace(/_/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function looksLikeColour(value: string | number | undefined): boolean {
  return /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(String(value ?? '').trim());
}

/** A colour picker needs #rrggbb. */
export function sixDigitHex(value: string): string {
  const v = value.trim();
  if (/^#[0-9a-f]{3}$/i.test(v)) return `#${v.slice(1).split('').map((c) => c + c).join('')}`.toLowerCase();
  return v.toLowerCase();
}

/** Common tokens offered when a form has none of its own. */
export const SUGGESTED_TOKENS: { key: string; sample: string }[] = [
  { key: 'background', sample: '#ffffff' },
  { key: 'foreground', sample: '#1a1a1a' },
  { key: 'primary', sample: '#1a1a1a' },
  { key: 'heading_font', sample: 'Georgia, serif' },
  { key: 'body_font', sample: 'system-ui, sans-serif' },
  { key: 'label_color', sample: '#1a1a1a' },
  { key: 'input_border_color', sample: '#998e7f' },
  { key: 'input_radius', sample: '8' },
  { key: 'button_background', sample: '#1a1a1a' },
  { key: 'button_text_color', sample: '#ffffff' },
  { key: 'button_radius', sample: '8' },
];

export interface ThemeStyles {
  form: CSSProperties;
  heading: CSSProperties;
  section: CSSProperties;
  label: CSSProperties;
  help: CSSProperties;
  input: CSSProperties;
  textarea: CSSProperties;
  button: CSSProperties;
  gap: string;
}

/** Inline styles for the preview, from the tokens (unknown tokens ignored). */
export function themeStyles(theme: Theme | null | undefined): ThemeStyles {
  const t = normalised(theme);
  const accent = str(t.button_background) ?? str(t.primary) ?? str(t.brand_color) ?? str(t.accent) ?? '#1a1a1a';
  const fg = str(t.foreground) ?? str(t.text) ?? '#1a1a1a';
  const bodyFont = str(t.body_font) ?? str(t.font) ?? 'system-ui, sans-serif';
  const radius = px(t.input_radius) ?? px(t.radius) ?? '6px';
  const input: CSSProperties = {
    display: 'block',
    width: '100%',
    boxSizing: 'border-box',
    background: str(t.input_background) ?? '#ffffff',
    color: str(t.input_text_color) ?? fg,
    borderStyle: 'solid',
    borderColor: str(t.input_border_color) ?? '#998e7f',
    borderWidth: px(t.input_border_width) ?? '1px',
    borderRadius: radius,
    padding: `${px(t.input_padding_y) ?? '9px'} ${px(t.input_padding_x) ?? '12px'}`,
    font: 'inherit',
  };
  return {
    form: {
      background: str(t.background) ?? '#ffffff',
      color: fg,
      fontFamily: bodyFont,
      fontSize: px(t.body_size) ?? '15px',
      padding: 24,
      maxWidth: px(t.max_width),
    },
    heading: {
      fontFamily: str(t.heading_font) ?? bodyFont,
      color: str(t.heading_color) ?? fg,
      fontSize: px(t.heading_size) ?? '24px',
      fontWeight: weight(t.heading_weight) ?? 600,
      textTransform: transform(t.heading_transform),
      margin: '0 0 12px',
      lineHeight: 1.2,
    },
    section: {
      fontFamily: str(t.section_title_font) ?? str(t.heading_font) ?? bodyFont,
      color: str(t.section_title_color) ?? str(t.heading_color) ?? fg,
      fontSize: px(t.section_title_size) ?? '18px',
      fontWeight: weight(t.section_title_weight) ?? weight(t.heading_weight) ?? 600,
      margin: '8px 0 0',
    },
    label: {
      display: 'block',
      color: str(t.label_color) ?? fg,
      fontSize: px(t.label_size) ?? '14px',
      fontWeight: weight(t.label_weight) ?? 500,
      textTransform: transform(t.label_transform),
      marginBottom: 6,
    },
    help: { color: str(t.muted_foreground) ?? fg, opacity: str(t.muted_foreground) ? 1 : 0.75, fontSize: '0.85em', marginTop: 4 },
    input,
    textarea: { ...input, borderRadius: px(t.textarea_radius) ?? (radius === '999px' ? '18px' : radius), minHeight: 72, resize: 'none' },
    button: {
      background: accent,
      color: str(t.button_text_color) ?? '#ffffff',
      borderStyle: 'solid',
      borderColor: str(t.button_border_color) ?? accent,
      borderWidth: px(t.button_border_width) ?? '1px',
      borderRadius: px(t.button_radius) ?? px(t.radius) ?? '6px',
      padding: `${px(t.button_padding_y) ?? '10px'} ${px(t.button_padding_x) ?? '22px'}`,
      fontFamily: str(t.button_font) ?? bodyFont,
      fontWeight: weight(t.button_weight) ?? 600,
      textTransform: transform(t.button_transform),
      fontSize: 'inherit',
      cursor: 'default',
    },
    gap: px(t.field_gap) ?? '16px',
  };
}
