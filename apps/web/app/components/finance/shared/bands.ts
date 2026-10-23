/**
 * The colour bands of a reconciliation difference (plan §10, Orbit's rule):
 * under $1 is fine, under $50 is worth a look, anything else is wrong. The
 * server sends `band` with every reconciliation; these only turn it into a
 * tone and a label, and `bandOf` exists for a row the server sent without
 * one (and for the test that pins the thresholds).
 */
import type { BadgeTone } from '../../ui/Badge';

export type Band = 'ok' | 'near' | 'off';

export const BAND_OK_CENTS = 100;
export const BAND_NEAR_CENTS = 5000;

/** |difference| < $1 → ok, < $50 → near, else off; null for no difference. */
export function bandOf(differenceCents: number | null | undefined): Band | null {
  if (differenceCents === null || differenceCents === undefined || !Number.isFinite(differenceCents)) return null;
  const abs = Math.abs(differenceCents);
  if (abs < BAND_OK_CENTS) return 'ok';
  if (abs < BAND_NEAR_CENTS) return 'near';
  return 'off';
}

export function bandTone(band: Band | null | undefined): BadgeTone {
  if (band === 'ok') return 'ok';
  if (band === 'near') return 'warn';
  if (band === 'off') return 'error';
  return 'neutral';
}

/** The CSS colour a figure in this band is written in. */
export function bandColor(band: Band | null | undefined): string | undefined {
  if (band === 'ok') return 'var(--ok)';
  if (band === 'near') return 'var(--warn)';
  if (band === 'off') return 'var(--error)';
  return undefined;
}

export function bandLabel(band: Band | null | undefined): string {
  if (band === 'ok') return 'Matches';
  if (band === 'near') return 'Within $50';
  if (band === 'off') return 'Does not match';
  return 'Not entered';
}
