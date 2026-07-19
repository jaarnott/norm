import { describe, it, expect } from 'vitest';
import { extractShifts, extractRosterMeta, venueWeekDays, dateKey } from './shared';
import { venueTimePrefs } from '../../../lib/rosterTime';

/**
 * The shapes these have to survive, taken from real payloads.
 *
 * The envelope case is the one that broke in production: `show_roster` hands the
 * component the *_for_period consolidator's `{window, data, venue_id}` wrapper,
 * and the old "first array of objects" fallback matched the wrapper's own
 * one-element `data` list. A week with 115 shifts rendered as "1 shift · 1 open"
 * — an unassigned shift that was really the roster object itself.
 */
const roster = (n: number) => ({
  id: 'r1',
  startDateTime: '2026-07-20T00:00:00+12:00',
  endDateTime: '2026-07-26T23:59:59+12:00',
  totalHours: 812.5,
  datestampPublished: null,
  datestampLocked: null,
  rosteredShifts: Array.from({ length: n }, (_, i) => ({
    id: `s${i}`,
    staffMemberId: `st${i}`,
    clockinTime: '2026-07-20T09:00:00+12:00',
    clockoutTime: '2026-07-20T17:00:00+12:00',
  })),
});

describe('extractShifts', () => {
  it('unwraps the for_period envelope rather than reading the wrapper as shifts', () => {
    const envelope = {
      window: { start: '2026-07-20T07:00:00+12:00', kind: 'trading_week' },
      data: [roster(115)],
      venue_id: '13dac930-434b-4947-b2cf-521e530b56c1',
    };
    expect(extractShifts(envelope)).toHaveLength(115);
  });

  it('reads a bare roster list', () => {
    expect(extractShifts([roster(3)] as unknown as Record<string, unknown>)).toHaveLength(3);
  });

  it('reads a single roster object', () => {
    expect(extractShifts(roster(4))).toHaveLength(4);
  });

  it('returns nothing for an empty week rather than throwing', () => {
    expect(extractShifts({ window: {}, data: [], venue_id: 'v' })).toHaveLength(0);
    expect(extractShifts({})).toHaveLength(0);
  });
});

describe('extractRosterMeta', () => {
  it('finds the roster through the envelope', () => {
    const meta = extractRosterMeta({ window: {}, data: [roster(115)], venue_id: 'v' });
    // Without unwrapping, startDate came back null — so the week had no columns
    // and every shift was dropped from the grid.
    expect(meta.startDate).not.toBeNull();
    expect(meta.rosterId).toBe('r1');
    expect(meta.publishedAt).toBeNull();
  });

  it('still reads an unwrapped roster', () => {
    expect(extractRosterMeta(roster(1)).rosterId).toBe('r1');
  });
});

describe('venueWeekDays', () => {
  const NZ = venueTimePrefs({ timezone: 'Pacific/Auckland', day_start_time: '07:00' });

  it('reads the roster start in the venue clock, not the viewer\'s', () => {
    // What LoadedHub actually returns for a Mon 20 Jul roster: 07:00 NZ
    // expressed as UTC. Read locally by a UTC viewer this is Sunday the 19th,
    // which shifted the whole grid a day early and dropped Sun 26 entirely.
    const start = new Date('2026-07-19T19:00:00+00:00');
    const days = venueWeekDays(start, NZ).map(dateKey);
    expect(days[0]).toBe('2026-07-20');
    expect(days[6]).toBe('2026-07-26');
    expect(days).toHaveLength(7);
  });

  it('returns nothing without a start date', () => {
    expect(venueWeekDays(null, NZ)).toHaveLength(0);
  });
});
