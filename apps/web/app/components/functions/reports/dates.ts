/**
 * Report periods as plain dates in the company's time zone ("today" is the
 * company's today, not the browser's). The API counts; these only say which
 * days to ask about.
 */

/** Today's date ("2026-10-13") in an IANA zone. */
export function todayIn(timeZone: string, now: Date = new Date()): string {
  try {
    // en-CA formats as YYYY-MM-DD.
    return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
  } catch {
    return now.toISOString().slice(0, 10);
  }
}

/** A plain date moved by whole days ("2026-10-13", -27 → "2026-09-16"). */
export function addDays(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

export type PeriodKey = '7d' | '28d' | '90d' | '365d' | 'custom';

export interface Period {
  key: PeriodKey;
  from: string;
  to: string;
}

export const PERIOD_LABELS: Record<PeriodKey, string> = {
  '7d': 'Last 7 days',
  '28d': 'Last 28 days',
  '90d': 'Last 90 days',
  '365d': 'Last 12 months',
  custom: 'Custom dates',
};

const LENGTHS: Record<Exclude<PeriodKey, 'custom'>, number> = { '7d': 7, '28d': 28, '90d': 90, '365d': 365 };

/** The last N days ending today (inclusive) for a preset; a custom period
 *  keeps its own dates. */
export function periodFor(key: PeriodKey, timeZone: string, custom?: { from: string; to: string }, now?: Date): Period {
  if (key === 'custom') {
    const today = todayIn(timeZone, now);
    return { key, from: custom?.from || addDays(today, -27), to: custom?.to || today };
  }
  const to = todayIn(timeZone, now);
  return { key, from: addDays(to, -(LENGTHS[key] - 1)), to };
}

/** Whole days from `from` to `to`, inclusive (0 when the range is backwards). */
export function daysBetween(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return 0;
  return Math.round((b - a) / 86_400_000) + 1;
}
