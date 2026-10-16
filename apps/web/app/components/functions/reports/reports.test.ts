import { describe, expect, it } from 'vitest';
import { addDays, daysBetween, periodFor, todayIn } from './dates';

describe('report periods', () => {
  it('uses the company’s today, not the browser’s', () => {
    const lateUtc = new Date('2026-10-12T20:30:00Z'); // 9:30 am on the 13th in Auckland
    expect(todayIn('Pacific/Auckland', lateUtc)).toBe('2026-10-13');
    expect(todayIn('Europe/London', lateUtc)).toBe('2026-10-12');
  });

  it('counts back whole days, across month and DST changes', () => {
    expect(addDays('2026-10-13', -27)).toBe('2026-09-16');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
    expect(addDays('2026-04-05', 1)).toBe('2026-04-06');
    expect(daysBetween('2026-09-16', '2026-10-13')).toBe(28);
    expect(daysBetween('2026-10-13', '2026-10-01')).toBe(0);
  });

  it('builds the last-N-days presets ending today', () => {
    const now = new Date('2026-10-12T20:30:00Z');
    expect(periodFor('28d', 'Pacific/Auckland', undefined, now)).toEqual({ key: '28d', from: '2026-09-16', to: '2026-10-13' });
    expect(periodFor('7d', 'Pacific/Auckland', undefined, now)).toEqual({ key: '7d', from: '2026-10-07', to: '2026-10-13' });
    expect(periodFor('custom', 'Pacific/Auckland', { from: '2026-01-01', to: '2026-01-31' }, now))
      .toEqual({ key: 'custom', from: '2026-01-01', to: '2026-01-31' });
  });
});
