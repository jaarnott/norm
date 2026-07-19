import { describe, it, expect } from 'vitest';
import type { Shift } from './shared';
import { venueTimePrefs } from '../../../lib/rosterTime';
import {
  overlappingShifts,
  openShifts,
  missingBreaks,
  overHours,
  emptyDays,
  computeWarnings,
  warningsByShift,
  summarise,
  leaveConflicts,
  unavailabilityConflicts,
} from './warnings';

const NZ = venueTimePrefs({ timezone: 'Pacific/Auckland', day_start_time: '07:00' });

function shift(over: Partial<Shift> = {}): Shift {
  return {
    id: 's1',
    staffMemberId: 'st1',
    staffMemberFirstName: 'Sam',
    staffMemberLastName: 'Ng',
    clockinTime: '2026-07-20T09:00:00+12:00',
    clockoutTime: '2026-07-20T13:00:00+12:00',
    ...over,
  } as Shift;
}

// Week columns exactly as venueWeekDays builds them: business dates anchored at
// local noon. Building them from an offset-carrying instant instead made these
// tests pass only in a timezone at or east of the venue — the same mistake the
// grid was making, which is why a UTC viewer lost the last day of the roster.
const week = ['2026-07-20', '2026-07-21'].map(d => {
  const [y, m, dd] = d.split('-').map(Number);
  return new Date(y, m - 1, dd, 12);
});

describe('overlapping shifts', () => {
  it('flags a double-booking for the same person', () => {
    const w = overlappingShifts([
      shift({ id: 'a', clockinTime: '2026-07-20T09:00:00+12:00', clockoutTime: '2026-07-20T15:00:00+12:00' }),
      shift({ id: 'b', clockinTime: '2026-07-20T14:00:00+12:00', clockoutTime: '2026-07-20T18:00:00+12:00' }),
    ]);
    expect(w).toHaveLength(1);
    expect(w[0].code).toBe('overlappingShifts');
    expect(w[0].severity).toBe('error');
    expect(w[0].message).toContain('Sam');
  });

  it('does not flag back-to-back shifts', () => {
    expect(overlappingShifts([
      shift({ id: 'a', clockinTime: '2026-07-20T09:00:00+12:00', clockoutTime: '2026-07-20T13:00:00+12:00' }),
      shift({ id: 'b', clockinTime: '2026-07-20T13:00:00+12:00', clockoutTime: '2026-07-20T17:00:00+12:00' }),
    ])).toHaveLength(0);
  });

  it('does not flag different people at the same time', () => {
    expect(overlappingShifts([
      shift({ id: 'a', staffMemberId: 'st1' }),
      shift({ id: 'b', staffMemberId: 'st2' }),
    ])).toHaveLength(0);
  });

  it('compares instants, so an overnight shift is handled', () => {
    // 22:00 -> 02:00 next day, then another starting 01:00. Same-day clock
    // comparison would miss this; instant comparison catches it.
    expect(overlappingShifts([
      shift({ id: 'a', clockinTime: '2026-07-20T22:00:00+12:00', clockoutTime: '2026-07-21T02:00:00+12:00' }),
      shift({ id: 'b', clockinTime: '2026-07-21T01:00:00+12:00', clockoutTime: '2026-07-21T05:00:00+12:00' }),
    ])).toHaveLength(1);
  });

  it('ignores deleted shifts', () => {
    expect(overlappingShifts([
      shift({ id: 'a', clockoutTime: '2026-07-20T18:00:00+12:00' }),
      shift({ id: 'b', clockinTime: '2026-07-20T10:00:00+12:00', datestampDeleted: '2026-07-19T00:00:00Z' }),
    ])).toHaveLength(0);
  });
});

describe('open shifts', () => {
  it('flags a shift with nobody assigned', () => {
    const w = openShifts([shift({ id: 'a', staffMemberId: undefined })]);
    expect(w).toHaveLength(1);
    expect(w[0].code).toBe('openShift');
    expect(w[0].scope.shiftId).toBe('a');
  });

  it('leaves assigned shifts alone', () => {
    expect(openShifts([shift()])).toHaveLength(0);
  });
});

describe('missing breaks', () => {
  it('flags a long shift with no break', () => {
    const w = missingBreaks([
      shift({ clockinTime: '2026-07-20T09:00:00+12:00', clockoutTime: '2026-07-20T18:00:00+12:00' }),
    ]);
    expect(w).toHaveLength(1);
    expect(w[0].message).toContain('9.0h');
  });

  it('accepts a shift that has one', () => {
    expect(missingBreaks([
      shift({
        clockinTime: '2026-07-20T09:00:00+12:00',
        clockoutTime: '2026-07-20T18:00:00+12:00',
        breaks: [{ breakStart: '2026-07-20T13:00:00+12:00', breakEnd: '2026-07-20T13:30:00+12:00', paid: false }],
      }),
    ])).toHaveLength(0);
  });

  it('ignores a deleted break', () => {
    expect(missingBreaks([
      shift({
        clockinTime: '2026-07-20T09:00:00+12:00',
        clockoutTime: '2026-07-20T18:00:00+12:00',
        breaks: [{ breakStart: '2026-07-20T13:00:00+12:00', breakEnd: '2026-07-20T13:30:00+12:00', paid: false, deletedAt: '2026-07-19T00:00:00Z' }],
      }),
    ])).toHaveLength(1);
  });

  it('leaves short shifts alone', () => {
    expect(missingBreaks([shift()])).toHaveLength(0);
  });
});

describe('over hours', () => {
  it('totals a person across the week', () => {
    const days = Array.from({ length: 6 }, (_, i) =>
      shift({ id: `d${i}`, clockinTime: `2026-07-2${i}T09:00:00+12:00`, clockoutTime: `2026-07-2${i}T17:00:00+12:00` }));
    const w = overHours(days); // 6 x 8h = 48h
    expect(w).toHaveLength(1);
    expect(w[0].message).toContain('48.0h');
  });

  it('respects a custom limit', () => {
    expect(overHours([shift()], { maxWeeklyHours: 2 })).toHaveLength(1);
    expect(overHours([shift()], { maxWeeklyHours: 40 })).toHaveLength(0);
  });

  it('does not total open shifts against anyone', () => {
    expect(overHours([shift({ staffMemberId: undefined })], { maxWeeklyHours: 0 })).toHaveLength(0);
  });
});

describe('empty days', () => {
  it('flags a day with nobody on', () => {
    const w = emptyDays([shift()], week, NZ); // only the 20th is covered
    expect(w).toHaveLength(1);
    expect(w[0].scope.dayDate).toBe('2026-07-21');
  });

  it('counts a 2am finish against the day it started', () => {
    // 23:00 on the 21st runs into the 22nd, but belongs to the 21st.
    const late = shift({ clockinTime: '2026-07-21T23:00:00+12:00', clockoutTime: '2026-07-22T03:00:00+12:00' });
    expect(emptyDays([shift(), late], week, NZ)).toHaveLength(0);
  });
});

describe('aggregation', () => {
  it('collects every rule and groups by shift', () => {
    const all = computeWarnings(
      [shift({ id: 'a', staffMemberId: undefined, clockoutTime: '2026-07-20T20:00:00+12:00' })],
      week,
      NZ,
    );
    const codes = all.map(w => w.code);
    expect(codes).toContain('openShift');
    expect(codes).toContain('missingBreak');
    expect(codes).toContain('emptyDay');
    expect(warningsByShift(all).get('a')?.length).toBeGreaterThan(0);
  });

  it('summarises for the agent to say', () => {
    expect(summarise([])).toBe('No problems found');
    const s = summarise(computeWarnings([shift({ staffMemberId: undefined })], week, NZ));
    expect(s).toMatch(/to fix|to review/);
  });
});

describe('leave conflicts', () => {
  const onLeave = [{
    id: 'l1', staffMemberId: 'st1', status: 'Approved', reason: 'England trip',
    // Note: no offset — this is how the API returns it.
    startDateTime: '2026-07-20T00:00:00', endDateTime: '2026-07-24T23:59:59',
  }];

  it('flags a shift rostered over approved leave', () => {
    const w = leaveConflicts([shift()], onLeave, NZ);
    expect(w).toHaveLength(1);
    expect(w[0].severity).toBe('error');
    expect(w[0].message).toContain('England trip');
  });

  it('ignores cancelled leave', () => {
    expect(leaveConflicts([shift()], [{ ...onLeave[0], status: 'Cancelled' }], NZ)).toHaveLength(0);
  });

  it('ignores leave for a different person', () => {
    expect(leaveConflicts([shift()], [{ ...onLeave[0], staffMemberId: 'other' }], NZ)).toHaveLength(0);
  });

  it('ignores a shift outside the leave window', () => {
    const after = shift({ clockinTime: '2026-07-27T09:00:00+12:00', clockoutTime: '2026-07-27T13:00:00+12:00' });
    expect(leaveConflicts([after], onLeave, NZ)).toHaveLength(0);
  });
});

describe('unavailability conflicts', () => {
  it('flags a OneOff record covering the day', () => {
    const w = unavailabilityConflicts([shift()], [{
      id: 'u1', staffMemberId: 'st1', type: 'OneOff', status: 'Approved',
      from: '2026-07-19', to: '2026-07-21', note: 'study',
    }], NZ);
    expect(w).toHaveLength(1);
    expect(w[0].message).toContain('study');
  });

  it('matches a Weekly record only on its named weekday', () => {
    // 2026-07-20 is a Monday.
    const monday = [{
      id: 'u2', staffMemberId: 'st1', type: 'Weekly', status: 'Approved',
      from: '2026-07-01', to: null, times: [{ day: 'Monday', from: '09:00', to: '17:00' }],
    }];
    expect(unavailabilityConflicts([shift()], monday, NZ)).toHaveLength(1);
    const tuesdayShift = shift({ clockinTime: '2026-07-21T09:00:00+12:00', clockoutTime: '2026-07-21T13:00:00+12:00' });
    expect(unavailabilityConflicts([tuesdayShift], monday, NZ)).toHaveLength(0);
  });

  it('honours an open-ended record (null `to`)', () => {
    expect(unavailabilityConflicts([shift()], [{
      id: 'u3', staffMemberId: 'st1', type: 'OneOff', status: 'Approved',
      from: '2026-01-01', to: null,
    }], NZ)).toHaveLength(1);
  });

  it('ignores a record that expired before the shift', () => {
    expect(unavailabilityConflicts([shift()], [{
      id: 'u4', staffMemberId: 'st1', type: 'OneOff', status: 'Approved',
      from: '2026-01-01', to: '2026-02-01',
    }], NZ)).toHaveLength(0);
  });
});
