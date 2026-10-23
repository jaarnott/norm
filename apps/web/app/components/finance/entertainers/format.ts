/**
 * Entertainers — display formatting. One place to swap for the finance
 * shared `Money` once F8's `components/finance/shared/` carries it; until
 * then the Functions formatters (plain functions, no event code) do the
 * same job: cents in NZD, plain dates never shifted through the browser's
 * zone, wall-clock times, instants in the venue's zone.
 */
import { instantLabel } from '../../functions/shared/format';

export { dateLabel, instantLabel, relativeLabel, timeLabel, timeRangeLabel } from '../../functions/shared/format';
export { todayIn } from '../../functions/shared/dates';
// Money through the finance shared formatter (F8), so every finance page
// prints cents the same way; `Money`/`useMoney` carry the organisation's
// currency when a page needs AUD.
export { formatCents as money } from '../shared/money';

/** An instant as "Sat 18 Oct, 6:30 pm" or an em dash. */
export function when(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return '—';
  try {
    return instantLabel(iso, timeZone ?? 'Pacific/Auckland');
  } catch {
    return iso;
  }
}

/** "Fri 23 Oct" — the short date the roster and the portal use. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d, 12))
    .toLocaleDateString('en-NZ', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short' })
    .replace(',', '');
}
