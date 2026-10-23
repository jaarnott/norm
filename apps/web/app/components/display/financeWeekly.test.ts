import { describe, expect, it } from 'vitest';
import { extractDoc, lastCompletedSunday, makeMoney, num, pct, shiftWeek } from './financeWeekly';

const figures = { sales: 1 } as unknown as Record<string, unknown>;
const doc = { venues: [{ venue: 'La Zeppa', venue_id: 'v' }], group: { week: figures, mtd: figures, venues: 1 } };

describe('extractDoc', () => {
  it('takes the document as the block data, or under data', () => {
    expect(extractDoc(doc)).toBe(doc);
    expect(extractDoc({ data: doc })).toBe(doc);
  });
  it('keeps an error alone and refuses anything else', () => {
    expect(extractDoc({ error: 'no venue' })).toEqual({ error: 'no venue' });
    expect(extractDoc({ rows: [] })).toBeNull();
    expect(extractDoc('text')).toBeNull();
    expect(extractDoc(null)).toBeNull();
  });
});

describe('the week picker arithmetic', () => {
  it('finds the last completed Monday–Sunday week', () => {
    // Tue 20 Oct 2026 → the week ending Sun 18 Oct.
    expect(lastCompletedSunday(new Date(2026, 9, 20))).toBe('2026-10-18');
    // A Monday: yesterday was the end of a complete week.
    expect(lastCompletedSunday(new Date(2026, 9, 19))).toBe('2026-10-18');
    // A Sunday: this week is not over, so the Sunday before.
    expect(lastCompletedSunday(new Date(2026, 9, 18))).toBe('2026-10-11');
  });
  it('steps whole weeks, across a month end and the clock change', () => {
    expect(shiftWeek('2026-10-18', 1)).toBe('2026-10-25');
    expect(shiftWeek('2026-10-04', -1)).toBe('2026-09-27');
    expect(shiftWeek('2026-09-27', -1)).toBe('2026-09-20');
  });
});

describe('formats', () => {
  it('reads ratios and counts', () => {
    expect(pct(0.2892)).toBe('28.9%');
    expect(pct(null)).toBe('—');
    expect(num(1810)).toBe('1,810');
    expect(num(77.25, 1)).toBe('77.3');
    expect(num(undefined)).toBe('—');
  });
  it('uses the organisation currency, whole dollars', () => {
    const nzd = makeMoney('NZD');
    expect(nzd(61234.5)).toBe('$61,235');
    expect(nzd(-12.4)).toBe('-$12');
    expect(nzd(null)).toBe('—');
    expect(makeMoney(null)(5)).toBe('$5');
    // AUD has a narrow symbol too; an unknown code falls back to the code.
    expect(makeMoney('AUD')(5)).toBe('$5');
    expect(makeMoney('XXX')(5)).toMatch(/5/);
  });
});
