import { describe, it, expect } from 'vitest';
import type { Shift } from './shared';
import { venueTimePrefs } from '../../../lib/rosterTime';
import {
  HOUR_W,
  TIMELINE_W,
  minutesToPx,
  pxToMinutes,
  clampPx,
  snapPx,
  timeToOffset,
  shiftsForDay,
  nowOffset,
} from './grid';

// A venue on NZ standard time (+12:00) opening at 7am.
const NZ = venueTimePrefs({ timezone: 'Pacific/Auckland', day_start_time: '07:00' });
const DAY = '2026-07-17';

/** 09:00 NZST on 17 Jul = 21:00 UTC on the 16th. */
const at = (utc: string) => new Date(utc).toISOString();

function shift(clockinTime: string, extra: Partial<Shift> = {}): Shift {
  return { id: 's1', staffMemberId: 'st1', clockinTime, ...extra } as Shift;
}

describe('pixel/minute conversion', () => {
  it('round-trips', () => {
    expect(pxToMinutes(minutesToPx(135))).toBe(135);
    expect(minutesToPx(60)).toBe(HOUR_W);
  });

  it('clamps to the visible window', () => {
    expect(clampPx(-40)).toBe(0);
    expect(clampPx(TIMELINE_W + 500)).toBe(TIMELINE_W);
  });

  it('snaps to the grid interval', () => {
    expect(pxToMinutes(snapPx(minutesToPx(7)))).toBe(0);
    expect(pxToMinutes(snapPx(minutesToPx(8)))).toBe(15);
    expect(pxToMinutes(snapPx(minutesToPx(38)))).toBe(45);
  });
});

describe('positioning', () => {
  it('puts the venue day start at x=0', () => {
    expect(timeToOffset(at('2026-07-16T19:00:00Z'), DAY, NZ)).toBe(0); // 07:00 NZST
  });

  it('places 9am two hours along', () => {
    expect(timeToOffset(at('2026-07-16T21:00:00Z'), DAY, NZ)).toBe(2 * HOUR_W);
  });

  it('does not use the browser clock', () => {
    // Same instant, expressed with a different offset — must land identically.
    const a = timeToOffset('2026-07-17T09:00:00+12:00', DAY, NZ);
    const b = timeToOffset('2026-07-16T21:00:00Z', DAY, NZ);
    expect(a).toBe(b);
  });

  it('is forgiving of missing or unparseable times', () => {
    expect(timeToOffset(undefined, DAY, NZ)).toBe(0);
    expect(timeToOffset('not a date', DAY, NZ)).toBe(0);
  });
});

describe('which shifts belong to a business day', () => {
  // The behaviour that was broken: membership used the browser-local calendar
  // date, so a shift finishing after midnight jumped to the next day's roster.
  it('keeps a 2am shift on the day it started', () => {
    const lateNight = shift('2026-07-18T02:00:00+12:00'); // 2am on the 18th
    expect(shiftsForDay([lateNight], '2026-07-17', NZ)).toHaveLength(1);
    expect(shiftsForDay([lateNight], '2026-07-18', NZ)).toHaveLength(0);
  });

  it('flips at the venue day start, not midnight', () => {
    const before = shift('2026-07-18T06:59:00+12:00');
    const after = shift('2026-07-18T07:00:00+12:00');
    expect(shiftsForDay([before], '2026-07-17', NZ)).toHaveLength(1);
    expect(shiftsForDay([after], '2026-07-18', NZ)).toHaveLength(1);
  });

  it('excludes deleted shifts', () => {
    const deleted = shift('2026-07-17T09:00:00+12:00', { datestampDeleted: '2026-07-16T00:00:00Z' });
    expect(shiftsForDay([deleted], DAY, NZ)).toHaveLength(0);
  });

  it('respects a venue that opens at a different hour', () => {
    const early = venueTimePrefs({ timezone: 'Pacific/Auckland', day_start_time: '05:00' });
    const s = shift('2026-07-18T06:00:00+12:00'); // 6am
    expect(shiftsForDay([s], '2026-07-17', early)).toHaveLength(0); // after 5am start -> the 18th
    expect(shiftsForDay([s], '2026-07-18', early)).toHaveLength(1);
  });
});

describe('now marker', () => {
  it('sits inside the day when now is within the window', () => {
    const now = new Date('2026-07-16T21:00:00Z'); // 09:00 NZST
    expect(nowOffset(DAY, NZ, now)).toBe(2 * HOUR_W);
  });

  it('is null on a different day', () => {
    expect(nowOffset(DAY, NZ, new Date('2026-07-20T21:00:00Z'))).toBeNull();
    expect(nowOffset(DAY, NZ, new Date('2026-07-10T21:00:00Z'))).toBeNull();
  });
});
