/**
 * Money on the finance pages is integer cents in the organisation's currency
 * (NZD and AUD organisations sit side by side). Pure, so it is tested without
 * a DOM; the `Money` component and the inputs call these.
 */

const formats = new Map<string, Intl.NumberFormat>();

function formatter(currency: string, dp: 0 | 2): Intl.NumberFormat | null {
  const key = `${currency}:${dp}`;
  let fmt = formats.get(key) ?? null;
  if (!fmt && !formats.has(key)) {
    try {
      fmt = new Intl.NumberFormat('en-NZ', {
        style: 'currency',
        currency,
        currencyDisplay: 'narrowSymbol',
        minimumFractionDigits: dp,
        maximumFractionDigits: dp,
      });
    } catch {
      fmt = null;
    }
    formats.set(key, fmt as Intl.NumberFormat);
  }
  return fmt;
}

/** 123456 → "$1,234.56" (dp 0 → "$1,235"); null → an em dash. A negative
 *  amount keeps its sign in front of the symbol: "-$12.00". */
export function formatCents(cents: number | null | undefined, currency = 'NZD', dp: 0 | 2 = 2): string {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return '—';
  const fmt = formatter(currency || 'NZD', dp);
  if (fmt) return fmt.format(cents / 100);
  return `${currency} ${(cents / 100).toLocaleString('en-NZ', { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;
}

/** As `formatCents`, with an explicit "+" on a positive amount (differences, adjustments). */
export function formatSignedCents(cents: number | null | undefined, currency = 'NZD', dp: 0 | 2 = 2): string {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return '—';
  const text = formatCents(Math.abs(cents), currency, dp);
  if (cents > 0) return `+${text}`;
  if (cents < 0) return `-${text}`;
  return text;
}

/** "12.50" / "$1,234" / "-3" / "" → cents (null when blank or not a number). */
export function parseCents(input: string | null | undefined): number | null {
  if (input === null || input === undefined) return null;
  const cleaned = String(input).replace(/[^0-9.\-]/g, '');
  if (!cleaned || cleaned === '-' || cleaned === '.') return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? Math.round(n * 100) : null;
}

/** Cents → the text an input shows ("12.50"; "" for null). */
export function centsToInput(cents: number | null | undefined): string {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return '';
  return (cents / 100).toFixed(2);
}

/** 6.48 → "6.48 h"; null → an em dash. */
export function formatHours(hours: number | string | null | undefined, dp = 2): string {
  if (hours === null || hours === undefined || hours === '') return '—';
  const n = typeof hours === 'string' ? Number(hours) : hours;
  if (!Number.isFinite(n)) return '—';
  return `${n.toLocaleString('en-NZ', { minimumFractionDigits: dp, maximumFractionDigits: dp })} h`;
}
