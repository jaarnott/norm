/**
 * Report periods as plain dates in the company's time zone ("today" is the
 * company's today, not the browser's). The API counts; these only say which
 * days to ask about.
 */

import { addDays, todayIn } from '../shared/dates';

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
