/**
 * Proposal colours, checked for legibility. The brand's colours are used as
 * text on the page background (body text, headings, the accent's small
 * labels) and body text sits on the panel colour (menu and space boxes), so
 * each pair is held to WCAG AA: 4.5:1 for normal text, 3:1 for headings.
 */
import type { BrandColourKey, BrandColours } from './types';

/** What Norm's default proposal layout uses when a colour isn't set
 *  (proposals.default_css) — shown as placeholders. */
export const SERVER_DEFAULTS: Record<BrandColourKey, string> = {
  primary: '#1f2a44',
  accent: '#b08d57',
  panel: '#f6f3ee',
  text: '#1a1a1a',
  background: '#ffffff',
};

/** A starting palette that passes every check below (the server's accent
 *  is 3.1:1 on white — too faint for the small labels it colours). */
export const ACCESSIBLE_DEFAULTS: Record<BrandColourKey, string> = {
  primary: '#1f2a44',
  accent: '#8a6a35',
  panel: '#f6f3ee',
  text: '#1a1a1a',
  background: '#ffffff',
};

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

/** "#abc" / "#aabbcc" / "#aabbccdd" → RGB (alpha ignored); anything else null. */
export function parseHex(value: string | null | undefined): Rgb | null {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec((value ?? '').trim());
  if (!m) return null;
  let hex = m[1];
  if (hex.length === 3) hex = hex.split('').map((c) => c + c).join('');
  return {
    r: parseInt(hex.slice(0, 2), 16),
    g: parseInt(hex.slice(2, 4), 16),
    b: parseInt(hex.slice(4, 6), 16),
  };
}

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance({ r, g, b }: Rgb): number {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG contrast ratio of two hex colours (1–21), or null if either isn't hex. */
export function contrastRatio(a: string | null | undefined, b: string | null | undefined): number | null {
  const ca = parseHex(a);
  const cb = parseHex(b);
  if (!ca || !cb) return null;
  const la = luminance(ca);
  const lb = luminance(cb);
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

export interface ContrastIssue {
  fg: BrandColourKey;
  bg: BrandColourKey;
  ratio: number;
  needed: number;
  message: string;
}

const PAIRS: { fg: BrandColourKey; bg: BrandColourKey; needed: number; what: string }[] = [
  { fg: 'text', bg: 'background', needed: 4.5, what: 'Body text on the background' },
  { fg: 'primary', bg: 'background', needed: 3, what: 'Headings on the background' },
  { fg: 'accent', bg: 'background', needed: 4.5, what: 'Accent labels on the background' },
  { fg: 'text', bg: 'panel', needed: 4.5, what: 'Text in menu and space boxes' },
];

/** The pairs that fall short, with the colours a proposal would really use
 *  (an unset colour is the server default). */
export function contrastIssues(colours: BrandColours): ContrastIssue[] {
  const pick = (k: BrandColourKey) => (parseHex(colours[k]) ? (colours[k] as string) : SERVER_DEFAULTS[k]);
  const out: ContrastIssue[] = [];
  for (const p of PAIRS) {
    const ratio = contrastRatio(pick(p.fg), pick(p.bg));
    if (ratio !== null && ratio < p.needed) {
      out.push({
        fg: p.fg,
        bg: p.bg,
        ratio,
        needed: p.needed,
        message: `${p.what} is ${ratio.toFixed(1)}:1 — aim for at least ${p.needed}:1 so it’s easy to read.`,
      });
    }
  }
  return out;
}
