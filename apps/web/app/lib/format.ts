// Shared number formatting so large figures read with thousands separators
// (1,234.56) consistently across Norm, instead of hand-rolled `$${n.toFixed(2)}`.
// en-NZ gives comma thousands separators and a dot decimal.

const LOCALE = 'en-NZ';

/** Currency, e.g. formatMoney(1234.5) -> "$1,234.50". `dp` sets decimal places. */
export function formatMoney(n: number, dp = 2): string {
  if (typeof n !== 'number' || !isFinite(n)) return '—';
  return `$${n.toLocaleString(LOCALE, { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;
}

/** Plain number with a thousands separator, e.g. formatNumber(1234) -> "1,234". */
export function formatNumber(n: number, dp = 0): string {
  if (typeof n !== 'number' || !isFinite(n)) return '—';
  return n.toLocaleString(LOCALE, { minimumFractionDigits: dp, maximumFractionDigits: dp });
}
