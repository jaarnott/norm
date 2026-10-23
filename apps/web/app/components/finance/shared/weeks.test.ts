import { describe, expect, it } from 'vitest';
import {
  addDays,
  dayLabel,
  isMonday,
  lastWeekStart,
  mondayOf,
  shiftWeek,
  thisWeekStart,
  weDotted,
  weLabel,
  weekEnding,
  weekOptions,
  weekRangeLabel,
} from './weeks';

describe('pay week arithmetic', () => {
  it('finds the Monday of any date and keeps a Monday', () => {
    expect(mondayOf('2026-10-18')).toBe('2026-10-12'); // a Sunday
    expect(mondayOf('2026-10-12')).toBe('2026-10-12');
    expect(mondayOf('2026-10-15')).toBe('2026-10-12');
    expect(isMonday('2026-10-12')).toBe(true);
    expect(isMonday('2026-10-13')).toBe(false);
  });
  it('ends on the Sunday and shifts by whole weeks', () => {
    expect(weekEnding('2026-10-12')).toBe('2026-10-18');
    expect(shiftWeek('2026-10-12', -1)).toBe('2026-10-05');
    expect(shiftWeek('2026-10-12', 2)).toBe('2026-10-26');
    expect(addDays('2026-12-30', 3)).toBe('2027-01-02');
  });
  it('labels the week the way payroll says it', () => {
    expect(weLabel('2026-10-12')).toBe('WE 18 Oct');
    expect(weLabel('2026-10-12', true)).toBe('WE 18 Oct 2026');
    expect(weDotted('2026-10-12')).toBe('WE 18.10.2026');
    expect(dayLabel('2026-10-18')).toBe('Sun 18 Oct 2026');
    expect(weekRangeLabel('2026-10-12')).toBe('Mon 12 – Sun 18 Oct 2026');
    expect(weekRangeLabel('2026-09-28')).toBe('Mon 28 Sep – Sun 4 Oct 2026');
    expect(weekRangeLabel('2026-12-28')).toBe('Mon 28 Dec 2026 – Sun 3 Jan 2027');
  });
});

describe('this week at the venue', () => {
  const auckland = { timezone: 'Pacific/Auckland', day_start_time: '07:00' };
  it('is the business week, so 3am Monday is still last week', () => {
    // 2026-10-19 02:00 NZDT = 2026-10-18 13:00 UTC — before Monday's day start.
    expect(thisWeekStart(auckland, new Date('2026-10-18T13:00:00Z'))).toBe('2026-10-12');
    // 2026-10-19 08:00 NZDT — the new pay week has begun.
    expect(thisWeekStart(auckland, new Date('2026-10-18T19:00:00Z'))).toBe('2026-10-19');
    expect(lastWeekStart(auckland, new Date('2026-10-18T19:00:00Z'))).toBe('2026-10-12');
  });
  it('lists weeks newest first around an anchor', () => {
    expect(weekOptions('2026-10-12', 2, 1)).toEqual(['2026-10-19', '2026-10-12', '2026-10-05', '2026-09-28']);
  });
});
