import { describe, expect, it } from 'vitest';
import { addDays, todayIn } from './dates';

describe('plain dates never pass through the browser zone', () => {
  it('reads today in the company zone, not the machine zone', () => {
    // 11:30 UTC on 12 Oct is already 13 Oct in Auckland (NZDT, +13).
    const instant = new Date('2026-10-12T11:30:00Z');
    expect(todayIn('Pacific/Auckland', instant)).toBe('2026-10-13');
    expect(todayIn('America/New_York', instant)).toBe('2026-10-12');
    // A report's "today" is the company's: 9:30 am on the 13th in Auckland.
    const lateUtc = new Date('2026-10-12T20:30:00Z');
    expect(todayIn('Pacific/Auckland', lateUtc)).toBe('2026-10-13');
    expect(todayIn('Europe/London', lateUtc)).toBe('2026-10-12');
  });

  it('adds whole days across month, year and DST boundaries', () => {
    expect(addDays('2026-10-30', 3)).toBe('2026-11-02');
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    // NZ DST starts 27 Sep 2026 — a calendar day is still a day.
    expect(addDays('2026-09-26', 2)).toBe('2026-09-28');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
    // Report periods count back: the last 28 days ending 13 Oct.
    expect(addDays('2026-10-13', -27)).toBe('2026-09-16');
    expect(addDays('2026-04-05', 1)).toBe('2026-04-06');
  });
});
