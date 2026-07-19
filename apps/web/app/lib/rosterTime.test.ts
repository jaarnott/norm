import { describe, it, expect } from 'vitest';
import {
  venueTimePrefs,
  parseDayStart,
  tzOffsetMinutes,
  formatOffset,
  formatInTz,
  companyDayDate,
  minutesIntoDay,
  offsetToISO,
  dayStartISO,
  formatClock,
  formatHourLabel,
  venueOffset,
  DEFAULT_TIME_PREFS,
} from './rosterTime';

const NZ = venueTimePrefs({ timezone: 'Pacific/Auckland', day_start_time: '07:00' });

describe('venue prefs', () => {
  it('parses a day start', () => {
    expect(parseDayStart('07:00')).toBe(420);
    expect(parseDayStart('06:30')).toBe(390);
  });

  it("falls back to Norm's 7am business day when unset or junk", () => {
    expect(parseDayStart(null)).toBe(DEFAULT_TIME_PREFS.dayStartMinutes);
    expect(parseDayStart('')).toBe(420);
    expect(parseDayStart('99:99')).toBe(420);
  });

  it('falls back to the default zone when the venue has none', () => {
    expect(venueTimePrefs(null).timeZone).toBe('Pacific/Auckland');
    expect(venueTimePrefs({ timezone: 'Australia/Sydney' }).timeZone).toBe('Australia/Sydney');
  });
});

describe('daylight saving', () => {
  // The bug this guards: times used to be stamped with a hardcoded +13:00,
  // which is NZ *daylight* time and wrong for roughly five months a year.
  it('tracks NZ moving between NZDT and NZST', () => {
    expect(formatOffset(tzOffsetMinutes(new Date('2026-01-15T00:00:00Z'), 'Pacific/Auckland'))).toBe('+13:00');
    expect(formatOffset(tzOffsetMinutes(new Date('2026-07-15T00:00:00Z'), 'Pacific/Auckland'))).toBe('+12:00');
  });

  it('stamps a July shift +12:00, not +13:00', () => {
    expect(formatInTz(new Date('2026-07-19T21:00:00Z'), 'Pacific/Auckland')).toBe('2026-07-20T09:00:00+12:00');
  });

  it('lands the day start on 07:00 local across a spring-forward', () => {
    // NZ clocks jump 2am -> 3am on 27 Sep 2026.
    expect(dayStartISO('2026-09-26', NZ)).toBe('2026-09-26T07:00:00+12:00');
    expect(dayStartISO('2026-09-27', NZ)).toBe('2026-09-27T07:00:00+13:00');
  });
});

describe('business day boundary', () => {
  // Norm's business day runs 07:00 -> 06:59 (see resolve_dates). A late shift
  // belongs to the day it started, not the calendar date it finishes on.
  it('puts a 2am finish on the previous business day', () => {
    expect(companyDayDate('2026-07-18T14:00:00Z', NZ)).toBe('2026-07-18'); // 02:00 on the 19th
  });

  it('flips at 07:00 local, not midnight', () => {
    expect(companyDayDate('2026-07-18T18:59:00Z', NZ)).toBe('2026-07-18'); // 06:59
    expect(companyDayDate('2026-07-18T19:00:00Z', NZ)).toBe('2026-07-19'); // 07:00
  });

  it('honours a venue that opens at a different hour', () => {
    const early = venueTimePrefs({ timezone: 'Pacific/Auckland', day_start_time: '05:00' });
    expect(dayStartISO('2026-07-17', early)).toBe('2026-07-17T05:00:00+12:00');
  });
});

describe('grid coordinates', () => {
  it('measures minutes from the venue day start', () => {
    expect(minutesIntoDay('2026-07-16T21:00:00Z', '2026-07-17', NZ)).toBe(120); // 09:00 = 2h after 07:00
    expect(dayStartISO('2026-07-17', NZ)).toBe('2026-07-17T07:00:00+12:00');
  });

  it('round-trips a grid offset back to the same instant', () => {
    const iso = offsetToISO('2026-07-17', 120, NZ);
    expect(iso).toBe('2026-07-17T09:00:00+12:00');
    expect(minutesIntoDay(iso, '2026-07-17', NZ)).toBe(120);
  });
});

describe('labels', () => {
  it('shows clock times in the venue zone, not the viewer’s', () => {
    // 21:00 UTC is 09:00 the next day in Auckland.
    expect(formatClock('2026-07-16T21:00:00Z', NZ)).toBe('9:00');
    const nyc = venueTimePrefs({ timezone: 'America/New_York' });
    expect(formatClock('2026-07-16T21:00:00Z', nyc)).toBe('17:00');
  });

  it('is blank for missing or unparseable values', () => {
    expect(formatClock('', NZ)).toBe('');
    expect(formatClock('nonsense', NZ)).toBe('');
  });

  it('labels grid hours from minutes after midnight', () => {
    expect(formatHourLabel(420)).toBe('7am');
    expect(formatHourLabel(720)).toBe('12pm');
    expect(formatHourLabel(0)).toBe('12am');
    expect(formatHourLabel(1380)).toBe('11pm');
    expect(formatHourLabel(1470)).toBe('12:30am'); // wraps past midnight
    expect(formatHourLabel(330)).toBe('5:30am');
  });

  it('reports the venue offset at an instant', () => {
    expect(venueOffset(new Date('2026-07-15T00:00:00Z'), NZ)).toBe('+12:00');
    expect(venueOffset(new Date('2026-01-15T00:00:00Z'), NZ)).toBe('+13:00');
  });
});

describe('venues outside New Zealand', () => {
  it('works for Sydney', () => {
    const syd = venueTimePrefs({ timezone: 'Australia/Sydney', day_start_time: '06:00' });
    expect(dayStartISO('2026-07-17', syd)).toBe('2026-07-17T06:00:00+10:00');
  });

  it('works for a negative offset with a half-hour day start', () => {
    const nyc = venueTimePrefs({ timezone: 'America/New_York', day_start_time: '05:30' });
    expect(dayStartISO('2026-07-17', nyc)).toBe('2026-07-17T05:30:00-04:00');
  });
});
