import { describe, it, expect } from 'vitest';
import {
  buildAttendance, parseClock, parseDayLabel, parseWindowStart, wallMinutes, windowLabel,
  minutesLabel, roleLabel, cleanName, spansLabel, shareLabel, type AttendanceDoc, type ShiftLine,
} from './model';

// Shapes exactly as loadedhub.get_labour (view attendance, group_by detail) returns them.
const line = (date: string, name: string, start: string, end: string, hours: number, cost = hours * 25, role = 'BAR TEAM'): ShiftLine =>
  ({ date, name, role, start, end, hours, cost });

const WEEK = { start: '2026-10-19T07:00:00+13:00', end: '2026-10-26T06:59:59+13:00', description: 'This week — Mon 19 Oct 07:00 → Mon 26 Oct 06:59 Pacific/Auckland (in progress — includes the current trading day, figures are partial)' };
const LAST_WEEK = { start: '2026-10-12T07:00:00+13:00', end: '2026-10-19T06:59:59+13:00', description: 'Last week — Mon 12 Oct 07:00 → Mon 19 Oct 06:59 Pacific/Auckland' };

/** Wall-clock minutes for a date and "h:mm AM" in the test's own terms. */
const at = (y: number, m: number, d: number, clock: string) => Date.UTC(y, m - 1, d) / 60000 + (parseClock(clock) as number);

describe('parsing what the tool sends', () => {
  it('reads 12-hour clock labels', () => {
    expect(parseClock('12:00 AM')).toBe(0);
    expect(parseClock('12:30 PM')).toBe(750);
    expect(parseClock('4:30 PM')).toBe(990);
    expect(parseClock('')).toBeNull();
    expect(parseClock('16:30')).toBeNull();
  });

  it('reads the window start: date, day start and offset', () => {
    expect(parseWindowStart(WEEK.start)).toEqual({ year: 2026, month: 9, day: 19, minutes: 420, offsetMins: 780 });
  });

  it('gives a day label the year that keeps it inside the window, across New Year', () => {
    expect(parseDayLabel('Fri 2 Jan', 2026, 11)).toBe(Date.UTC(2027, 0, 2) / 60000);
    expect(parseDayLabel('Wed 30 Dec', 2026, 11)).toBe(Date.UTC(2026, 11, 30) / 60000);
    expect(parseDayLabel('nonsense', 2026, 11)).toBeNull();
  });

  it('reads the venue wall clock from its timezone, else the window offset', () => {
    const now = new Date('2026-10-23T10:45:00Z'); // 23:45 in Auckland (NZDT)
    expect(wallMinutes(now, 'Pacific/Auckland', null)).toBe(at(2026, 10, 23, '11:45 PM'));
    expect(wallMinutes(now, null, 780)).toBe(at(2026, 10, 23, '11:45 PM'));
    expect(wallMinutes(now, null, null)).toBeNull();
  });

  it('says names and roles plainly', () => {
    expect(cleanName('Matthew  McGregor')).toBe('Matthew McGregor');
    expect(roleLabel('KITCHEN TEAM')).toBe('Kitchen team');
    expect(roleLabel('Engineers Security')).toBe('Engineers Security');
    expect(windowLabel(WEEK.description)).toBe('This week — Mon 19 Oct 07:00 to Mon 26 Oct 06:59');
    expect(minutesLabel(12)).toBe('12 min');
    expect(minutesLabel(75)).toBe('1 h 15 min');
    expect(minutesLabel(120)).toBe('2 h');
    expect(shareLabel(3283, 10000)).toBe('32.8%');
    expect(shareLabel(100, 0)).toBeNull();
  });
});

describe('one person, one day: rostered against clocked', () => {
  const build = (doc: Partial<AttendanceDoc>, nowWall: number | null = null) =>
    buildAttendance({ window: LAST_WEEK, ...doc }, { nowWall });

  it('joins split roster pieces and compares first start and last finish', () => {
    const a = build({
      rostered: [
        line('Mon 12 Oct', 'Ana  Cook', '10:30 AM', '11:30 AM', 1),
        line('Mon 12 Oct', 'Ana  Cook', '11:30 AM', '3:00 PM', 3.5),
        line('Mon 12 Oct', 'Ana  Cook', '5:00 PM', '9:15 PM', 4.25),
      ],
      clockins: [line('Mon 12 Oct', 'Ana  Cook', '10:30 AM', '8:48 PM', 8.43)],
    });
    expect(a.people).toHaveLength(1);
    const d = a.people[0].days[0];
    expect(d.name).toBe('Ana Cook');
    expect(spansLabel(d.rostered)).toBe('10:30 AM – 3:00 PM, 5:00 PM – 9:15 PM');
    expect(d.rosteredHours).toBe(8.75);
    expect(d.workedHours).toBe(8.43);
    expect(d.lateMins).toBeNull();
    expect(d.earlyMins).toBe(27); // 9:15 rostered, out at 8:48
    expect(d.flags).toEqual({ late: 0, early: 1, noShow: 0, unrostered: 0, over: 0 });
  });

  it('flags a late start only past the grace', () => {
    const a = build({
      rostered: [line('Tue 13 Oct', 'Bo', '2:30 PM', '10:00 PM', 7.5), line('Tue 13 Oct', 'Cy', '2:45 PM', '11:00 PM', 8)],
      clockins: [line('Tue 13 Oct', 'Bo', '2:32 PM', '10:04 PM', 7.5), line('Tue 13 Oct', 'Cy', '3:30 PM', '11:30 PM', 8)],
    });
    const bo = a.people.find((p) => p.name === 'Bo')!.days[0];
    const cy = a.people.find((p) => p.name === 'Cy')!.days[0];
    expect(bo.lateMins).toBeNull();
    expect(cy.lateMins).toBe(45);
    expect(cy.earlyMins).toBeNull();
  });

  it('calls a missed morning piece of a split shift a late start, not hours late', () => {
    const a = build({
      rostered: [
        line('Thu 15 Oct', 'Jon', '9:15 AM', '10:00 AM', 0.75),
        line('Thu 15 Oct', 'Jon', '4:30 PM', '10:30 PM', 6),
        line('Thu 15 Oct', 'Jon', '11:00 PM', '1:30 AM', 2.5),
      ],
      clockins: [line('Thu 15 Oct', 'Jon', '4:22 PM', '12:56 AM', 8.57)],
    });
    const d = a.people[0].days[0];
    expect(spansLabel(d.missedBefore)).toBe('9:15 AM – 10:00 AM');
    expect(d.lateMins).toBeNull();
    expect(d.earlyMins).toBe(34);
    expect(d.flags.late).toBe(1);
    expect(d.flags.early).toBe(1);
  });

  it('calls an evening piece never come back for an early finish', () => {
    const a = build({
      rostered: [line('Fri 16 Oct', 'Max', '10:30 AM', '2:00 PM', 3.5), line('Fri 16 Oct', 'Max', '2:30 PM', '8:45 PM', 6.25)],
      clockins: [line('Fri 16 Oct', 'Max', '10:30 AM', '2:30 PM', 4)],
    });
    const d = a.people[0].days[0];
    expect(spansLabel(d.missedAfter)).toBe('2:30 PM – 8:45 PM');
    expect(d.earlyMins).toBeNull();
    expect(d.flags.early).toBe(1);
  });

  it('flags rostered-but-never-clocked as a no-show, and clocked-but-not-rostered as unrostered', () => {
    const a = build({
      rostered: [line('Wed 14 Oct', 'Dee', '5:00 PM', '10:00 PM', 5)],
      clockins: [line('Wed 14 Oct', 'Eve', '6:00 PM', '11:15 PM', 5.25)],
    });
    expect(a.people.find((p) => p.name === 'Dee')!.days[0].status).toBe('no_show');
    expect(a.people.find((p) => p.name === 'Eve')!.days[0].status).toBe('unrostered');
    expect(a.totals.flags).toEqual({ late: 0, early: 0, noShow: 1, unrostered: 1, over: 0 });
    expect(a.flagCount).toBe(2);
  });

  it('flags hours more than 20% over roster', () => {
    const a = build({
      rostered: [line('Thu 15 Oct', 'Fay', '5:00 PM', '10:00 PM', 5), line('Thu 15 Oct', 'Gus', '5:00 PM', '10:00 PM', 5)],
      clockins: [line('Thu 15 Oct', 'Fay', '5:00 PM', '11:00 PM', 6), line('Thu 15 Oct', 'Gus', '5:00 PM', '11:05 PM', 6.08)],
    });
    expect(a.people.find((p) => p.name === 'Fay')!.days[0].over).toBe(false); // exactly 20%
    expect(a.people.find((p) => p.name === 'Gus')!.days[0].over).toBe(true);
  });

  it('keeps booked leave out of worked hours and never calls it a no-show', () => {
    const a = build({
      rostered: [line('Fri 16 Oct', 'Hal', '11:00 AM', '5:00 PM', 6)],
      leave: [line('Fri 16 Oct', 'Hal', '7:00 AM', '3:00 PM', 8, 248)],
    });
    const d = a.people[0].days[0];
    expect(d.status).toBe('leave');
    expect(d.workedHours).toBe(0);
    expect(d.leaveHours).toBe(8);
    expect(a.totals.flags.noShow).toBe(0);
  });
});

describe('trading days and the clock', () => {
  it('files a 3 AM shift under the night before, as the trading-day window does', () => {
    const a = buildAttendance({
      window: { start: '2026-10-23T07:00:00+13:00', end: '2026-10-24T06:59:59+13:00', description: 'Today' },
      rostered: [line('Fri 23 Oct', 'Ivy', '6:00 PM', '3:00 AM', 8.5), line('Sat 24 Oct', 'Cleaner  2', '3:00 AM', '5:30 AM', 2.5)],
      clockins: [line('Fri 23 Oct', 'Ivy', '6:02 PM', '3:20 AM', 9.3)],
    }, { nowWall: null });
    expect(a.days).toHaveLength(1);
    expect(a.days[0].dayLabel).toBe('Fri 23 Oct');
    expect(a.days[0].dayKey).toBe('2026-10-23');
    const ivy = a.people.find((p) => p.name === 'Ivy')!.days[0];
    expect(ivy.lateMins).toBeNull();
    expect(ivy.earlyMins).toBeNull(); // out at 3:20 AM, past the 3:00 AM end
  });

  it('pairs a clock-in just before midnight with a shift rostered from midnight', () => {
    const a = buildAttendance({
      window: { start: '2026-10-23T07:00:00+13:00', end: '2026-10-24T06:59:59+13:00' },
      rostered: [line('Sat 24 Oct', 'Jo', '12:00 AM', '4:00 AM', 4)],
      clockins: [line('Fri 23 Oct', 'Jo', '11:55 PM', '4:00 AM', 4.08)],
    }, { nowWall: null });
    expect(a.people[0].days).toHaveLength(1);
    expect(a.people[0].days[0].status).toBe('worked');
  });

  it('does not call a shift that has not started a no-show, and counts rostered hours so far', () => {
    const now = at(2026, 10, 23, '11:45 PM');
    const a = buildAttendance({
      window: WEEK,
      rostered: [
        line('Fri 23 Oct', 'Kit', '4:30 PM', '10:30 PM', 6), // done, clocked
        line('Fri 23 Oct', 'Lu', '6:00 PM', '3:00 AM', 8.5), // under way, no clock-in
        line('Fri 23 Oct', 'Mo', '9:00 PM', '2:00 AM', 5), // under way, clocked in
        line('Sat 24 Oct', 'Ned', '5:00 PM', '11:00 PM', 6), // tomorrow
      ],
      clockins: [
        line('Fri 23 Oct', 'Kit', '4:28 PM', '10:41 PM', 6.22),
        line('Fri 23 Oct', 'Mo', '8:46 PM', '', 0, 0),
      ],
    }, { nowWall: now });
    const by = (n: string) => a.people.find((p) => p.name === n)!.days[0];
    expect(by('Kit').status).toBe('worked');
    expect(by('Lu').status).toBe('not_in');
    expect(by('Mo').status).toBe('on_shift');
    expect(spansLabel(by('Mo').clocked)).toBe('since 8:46 PM');
    expect(by('Mo').earlyMins).toBeNull();
    expect(by('Ned').status).toBe('upcoming');
    expect(by('Kit').ahead).toBe(false);
    expect(a.totals.flags.noShow).toBe(1);
    expect(a.inProgress).toBe(true);
    expect(a.totals.rosteredHours).toBe(25.5);
    expect(a.totals.rosteredSoFarHours).toBe(6); // only Kit's shift has finished
  });

  it('marks clock-ins already entered for a time still to come', () => {
    const a = buildAttendance({
      window: WEEK,
      rostered: [line('Sat 24 Oct', 'Lea', '4:30 PM', '11:00 PM', 6)],
      clockins: [line('Sat 24 Oct', 'Lea', '4:30 PM', '11:00 PM', 6)],
    }, { nowWall: at(2026, 10, 24, '12:40 AM') });
    expect(a.people[0].days[0].ahead).toBe(true);
    expect(a.flagCount).toBe(0);
  });

  it('rolls up by person and by day with the same totals', () => {
    const a = buildAttendance({
      window: LAST_WEEK,
      rostered: [
        line('Mon 12 Oct', 'Pat', '5:00 PM', '10:00 PM', 5, 120),
        line('Tue 13 Oct', 'Pat', '5:00 PM', '10:00 PM', 5, 120),
        line('Tue 13 Oct', 'Quin', '5:00 PM', '9:00 PM', 4, 100),
      ],
      clockins: [
        line('Mon 12 Oct', 'Pat', '5:20 PM', '10:00 PM', 4.67, 112),
        line('Tue 13 Oct', 'Pat', '5:00 PM', '10:00 PM', 5, 120),
        line('Tue 13 Oct', 'Quin', '5:00 PM', '9:00 PM', 4, 100),
      ],
    }, { nowWall: null });
    expect(a.days.map((d) => d.dayLabel)).toEqual(['Mon 12 Oct', 'Tue 13 Oct']);
    expect(a.people.map((p) => p.name)).toEqual(['Pat', 'Quin']);
    const pat = a.people[0];
    expect(pat.rosteredHours).toBe(10);
    expect(pat.workedHours).toBe(9.67);
    expect(pat.workedCost).toBe(232);
    expect(pat.flags.late).toBe(1);
    expect(a.totals.workedCost).toBe(332);
    expect(a.days[1].workedHours).toBe(9);
    expect(a.inProgress).toBe(false);
  });
});
