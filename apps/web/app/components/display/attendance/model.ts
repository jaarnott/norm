/**
 * Attendance — the pure logic behind the Attendance screen.
 *
 * Input is `loadedhub.get_labour` with view 'attendance', group_by 'detail':
 * three lists of shift lines (rostered, clockins, leave), each
 * `{date: "Fri 23 Oct", name, role, start: "4:30 PM", end: "10:30 PM", hours, cost}`
 * in the venue's local time, and the trading-day window it covered.
 *
 * From those lines this builds one row per person per trading day (rostered
 * against clocked, with the flags the tool's description names: no-shows,
 * unrostered clock-ins, more than 20% over roster — plus late starts and
 * early finishes), and rolls them up by person and by day.
 *
 * Times stay in the venue's wall clock throughout: a line's absolute minute
 * is its date and clock time read as if UTC, so no timezone arithmetic is
 * needed. A line is filed under the trading day its start falls in, using the
 * day start the tool's own window opens on (07:00 for most venues) — a 3 AM
 * cleaning shift belongs to the night before, exactly as the window counts it.
 */

export interface ShiftLine {
  date: string;
  name: string;
  role?: string;
  job?: string;
  start: string;
  end: string;
  hours: number;
  cost: number;
}

export interface LabourWindow {
  start: string;
  end: string;
  description?: string | null;
}

export interface AttendanceDoc {
  view?: string;
  window: LabourWindow;
  rostered?: ShiftLine[];
  clockins?: ShiftLine[];
  leave?: ShiftLine[];
  totals?: Record<string, number>;
  error?: string;
}

/** One stretch of time: a rostered shift or a clock-in, contiguous pieces merged. */
export interface Span {
  start: number;
  /** null while still clocked in. */
  end: number | null;
  startLabel: string;
  endLabel: string;
}

export type DayStatus =
  | 'worked' // rostered and clocked
  | 'on_shift' // clocked in, not out yet
  | 'upcoming' // rostered, shift hasn't started
  | 'not_in' // rostered, shift under way, no clock-in
  | 'no_show' // rostered, shift over, never clocked in
  | 'unrostered' // clocked in with no shift rostered that day
  | 'leave'; // on booked leave (never counted as worked)

export interface Flags {
  late: number;
  early: number;
  noShow: number;
  unrostered: number;
  over: number;
}

export interface PersonDay {
  key: string;
  dayKey: string; // ISO date of the trading day
  dayLabel: string; // "Fri 23 Oct"
  personKey: string;
  name: string;
  role: string;
  rostered: Span[];
  clocked: Span[];
  rosteredHours: number;
  rosteredCost: number;
  workedHours: number;
  workedCost: number;
  leaveHours: number;
  leaveCost: number;
  status: DayStatus;
  /** Minutes after the rostered start the first clock-in came (only when past the grace). */
  lateMins: number | null;
  /** Minutes before the rostered end the last clock-out came (only when past the grace). */
  earlyMins: number | null;
  /** Rostered pieces that were over before the first clock-in (a split shift's morning missed). */
  missedBefore: Span[];
  /** Rostered pieces that hadn't started by the last clock-out, and have started by now. */
  missedAfter: Span[];
  /** Worked more than 20% over the rostered hours. */
  over: boolean;
  /** Clock-ins already in Loaded for a time that hasn't come yet (a pre-filled timesheet). */
  ahead: boolean;
  /** Whether the day's rostered hours have all finished by now (for "rostered so far"). */
  rosteredSoFarHours: number;
  rosteredSoFarCost: number;
  flags: Flags;
}

export interface Rollup {
  rosteredHours: number;
  rosteredCost: number;
  workedHours: number;
  workedCost: number;
  leaveHours: number;
  leaveCost: number;
  rosteredSoFarHours: number;
  rosteredSoFarCost: number;
  flags: Flags;
}

export interface PersonRow extends Rollup {
  key: string;
  name: string;
  role: string;
  days: PersonDay[];
}

export interface DayRow extends Rollup {
  dayKey: string;
  dayLabel: string;
  people: PersonDay[];
}

export interface Attendance {
  people: PersonRow[];
  days: DayRow[];
  totals: Rollup;
  /** The window runs past now: rostered hours still to come aren't late, they're ahead. */
  inProgress: boolean;
  /** How many flags of each kind across every person-day. */
  flagCount: number;
}

export interface BuildOptions {
  /** Venue wall-clock "now" in minutes (see wallMinutes); null = treat everything as past. */
  nowWall: number | null;
  /** Minutes of slack before a start counts as late or a finish as early. */
  graceMins?: number;
  /** Worked over rostered by more than this share is flagged (0.2 = 20%). */
  overShare?: number;
}

export const GRACE_MINS = 10;
export const OVER_SHARE = 0.2;

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAY = 1440;

// ── parsing ───────────────────────────────────────────────────────────────

/** "4:30 PM" → 990; anything else → null. */
export function parseClock(label: string | null | undefined): number | null {
  const m = /^\s*(\d{1,2}):(\d{2})\s*([AP])M\s*$/i.exec(label || '');
  if (!m) return null;
  let h = Number(m[1]) % 12;
  if (m[3].toUpperCase() === 'P') h += 12;
  return h * 60 + Number(m[2]);
}

/** The window's own wall-clock date, time and UTC offset, read off its ISO string. */
export function parseWindowStart(iso: string | null | undefined): { year: number; month: number; day: number; minutes: number; offsetMins: number | null } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?/.exec(iso || '');
  if (!m) return null;
  let offsetMins: number | null = null;
  if (m[6] === 'Z') offsetMins = 0;
  else if (m[6]) {
    const sign = m[6][0] === '-' ? -1 : 1;
    const digits = m[6].slice(1).replace(':', '');
    offsetMins = sign * (Number(digits.slice(0, 2)) * 60 + Number(digits.slice(2, 4)));
  }
  return { year: Number(m[1]), month: Number(m[2]) - 1, day: Number(m[3]), minutes: Number(m[4]) * 60 + Number(m[5]), offsetMins };
}

/**
 * "Fri 23 Oct" → minutes since the epoch at that date's midnight (wall clock).
 * The label carries no year: take the window's, stepping over a New Year the
 * window spans.
 */
export function parseDayLabel(label: string, windowYear: number, windowMonth: number): number | null {
  const m = /(\d{1,2})\s+([A-Za-z]{3})/.exec(label || '');
  if (!m) return null;
  const month = MONTHS.findIndex((x) => x.toLowerCase() === m[2].toLowerCase());
  if (month < 0) return null;
  let year = windowYear;
  if (month < windowMonth - 6) year += 1;
  else if (month > windowMonth + 6) year -= 1;
  return Date.UTC(year, month, Number(m[1])) / 60000;
}

/** Minutes since the epoch → { ISO date, "Fri 23 Oct" } for a trading-day index. */
function dayFromIndex(index: number): { key: string; label: string } {
  const d = new Date(index * DAY * 60000);
  const key = d.toISOString().slice(0, 10);
  return { key, label: `${DOW[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}` };
}

/**
 * The venue's wall clock now, as minutes since the epoch (read as if UTC), so
 * it compares straight against shift times. Uses the venue's timezone when we
 * have it, else the window's own UTC offset.
 */
export function wallMinutes(now: Date, timeZone: string | null | undefined, fallbackOffsetMins: number | null): number | null {
  if (timeZone) {
    try {
      const parts = new Intl.DateTimeFormat('en-GB', {
        timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
      }).formatToParts(now);
      const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
      const v = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute')) / 60000;
      if (Number.isFinite(v)) return v;
    } catch {
      /* unknown zone — fall through */
    }
  }
  if (fallbackOffsetMins === null) return null;
  return Math.floor(now.getTime() / 60000) + fallbackOffsetMins;
}

/** "Matthew  McGregor" → "Matthew McGregor". */
export function cleanName(name: string | null | undefined): string {
  return String(name || '').replace(/\s+/g, ' ').trim();
}

/** Loaded's role names are shouted ("KITCHEN TEAM"); say them like words. */
export function roleLabel(role: string | null | undefined): string {
  const r = cleanName(role);
  if (!r) return '';
  if (r !== r.toUpperCase()) return r;
  return r.charAt(0) + r.slice(1).toLowerCase();
}

// ── building ──────────────────────────────────────────────────────────────

interface Timed {
  line: ShiftLine;
  start: number;
  end: number | null;
  tradingIndex: number;
}

function timeLine(line: ShiftLine, year: number, month: number, dayStart: number): Timed | null {
  const date = parseDayLabel(line.date, year, month);
  const clockStart = parseClock(line.start);
  if (date === null || clockStart === null) return null;
  const start = date + clockStart;
  const clockEnd = parseClock(line.end);
  let end: number | null = null;
  if (clockEnd !== null) {
    end = date + clockEnd;
    if (end <= start) end += DAY; // ran past midnight
  }
  return { line, start, end, tradingIndex: Math.floor((start - dayStart) / DAY) };
}

/** Sorted, with touching pieces (1:00–1:15, 1:15–4:15) joined into one span. */
function mergeSpans(items: Timed[]): Span[] {
  const sorted = [...items].sort((a, b) => a.start - b.start);
  const out: Span[] = [];
  for (const t of sorted) {
    const last = out[out.length - 1];
    if (last && last.end !== null && t.start <= last.end) {
      if (t.end === null) {
        last.end = null;
        last.endLabel = '';
      } else if (t.end > last.end) {
        last.end = t.end;
        last.endLabel = t.line.end;
      }
      continue;
    }
    out.push({ start: t.start, end: t.end, startLabel: t.line.start, endLabel: t.end === null ? '' : t.line.end });
  }
  return out;
}

const round2 = (n: number) => Math.round(n * 100) / 100;
const sum = (xs: Timed[], k: 'hours' | 'cost') => round2(xs.reduce((a, t) => a + (Number(t.line[k]) || 0), 0));
const noFlags = (): Flags => ({ late: 0, early: 0, noShow: 0, unrostered: 0, over: 0 });

export function flagTotal(f: Flags): number {
  return f.late + f.early + f.noShow + f.unrostered + f.over;
}

function emptyRollup(): Rollup {
  return {
    rosteredHours: 0, rosteredCost: 0, workedHours: 0, workedCost: 0,
    leaveHours: 0, leaveCost: 0, rosteredSoFarHours: 0, rosteredSoFarCost: 0, flags: noFlags(),
  };
}

function addInto(into: Rollup, d: PersonDay): void {
  into.rosteredHours = round2(into.rosteredHours + d.rosteredHours);
  into.rosteredCost = round2(into.rosteredCost + d.rosteredCost);
  into.workedHours = round2(into.workedHours + d.workedHours);
  into.workedCost = round2(into.workedCost + d.workedCost);
  into.leaveHours = round2(into.leaveHours + d.leaveHours);
  into.leaveCost = round2(into.leaveCost + d.leaveCost);
  into.rosteredSoFarHours = round2(into.rosteredSoFarHours + d.rosteredSoFarHours);
  into.rosteredSoFarCost = round2(into.rosteredSoFarCost + d.rosteredSoFarCost);
  into.flags.late += d.flags.late;
  into.flags.early += d.flags.early;
  into.flags.noShow += d.flags.noShow;
  into.flags.unrostered += d.flags.unrostered;
  into.flags.over += d.flags.over;
}

export function buildAttendance(doc: AttendanceDoc, opts: BuildOptions): Attendance {
  const grace = opts.graceMins ?? GRACE_MINS;
  const overShare = opts.overShare ?? OVER_SHARE;
  const now = opts.nowWall;
  const win = parseWindowStart(doc.window?.start);
  const winEnd = parseWindowStart(doc.window?.end);
  const year = win?.year ?? new Date().getFullYear();
  const month = win?.month ?? new Date().getMonth();
  const dayStart = win?.minutes ?? 0;

  // person|trading day → the three kinds of line
  const groups = new Map<string, { personKey: string; name: string; index: number; rostered: Timed[]; clocked: Timed[]; leave: Timed[] }>();
  const add = (kind: 'rostered' | 'clocked' | 'leave', lines: ShiftLine[] | undefined) => {
    for (const line of lines || []) {
      const t = timeLine(line, year, month, dayStart);
      if (!t) continue;
      const name = cleanName(line.name) || 'Unassigned';
      const personKey = name.toLowerCase();
      const key = `${personKey}|${t.tradingIndex}`;
      let g = groups.get(key);
      if (!g) {
        g = { personKey, name, index: t.tradingIndex, rostered: [], clocked: [], leave: [] };
        groups.set(key, g);
      }
      g[kind].push(t);
    }
  };
  add('rostered', doc.rostered);
  add('clocked', doc.clockins);
  add('leave', doc.leave);

  const personDays: PersonDay[] = [];
  for (const [key, g] of groups) {
    const day = dayFromIndex(g.index);
    const rostered = mergeSpans(g.rostered);
    const clocked = mergeSpans(g.clocked);
    const role = roleLabel(g.rostered[0]?.line.role || g.clocked[0]?.line.role || g.leave[0]?.line.role);
    const rosteredHours = sum(g.rostered, 'hours');
    const workedHours = sum(g.clocked, 'hours');
    const soFar = g.rostered.filter((t) => now === null || (t.end ?? t.start) <= now);
    const pd: PersonDay = {
      key,
      dayKey: day.key,
      dayLabel: day.label,
      personKey: g.personKey,
      name: g.name,
      role,
      rostered,
      clocked,
      rosteredHours,
      rosteredCost: sum(g.rostered, 'cost'),
      workedHours,
      workedCost: sum(g.clocked, 'cost'),
      leaveHours: sum(g.leave, 'hours'),
      leaveCost: sum(g.leave, 'cost'),
      status: 'worked',
      lateMins: null,
      earlyMins: null,
      missedBefore: [],
      missedAfter: [],
      over: false,
      ahead: now !== null && clocked.some((c) => c.start > now),
      rosteredSoFarHours: sum(soFar, 'hours'),
      rosteredSoFarCost: sum(soFar, 'cost'),
      flags: noFlags(),
    };

    const firstRoster = rostered[0];
    const lastRoster = rostered[rostered.length - 1];
    const onShift = clocked.some((s) => s.end === null);

    if (clocked.length === 0) {
      if (rostered.length === 0) {
        pd.status = 'leave';
      } else if (g.leave.length > 0) {
        pd.status = 'leave';
      } else if (now !== null && firstRoster.start > now) {
        pd.status = 'upcoming';
      } else if (now !== null && lastRoster.end !== null && lastRoster.end > now) {
        // Under way with no clock-in. Inside the grace it's simply not yet late.
        pd.status = now - firstRoster.start > grace ? 'not_in' : 'upcoming';
        if (pd.status === 'not_in') pd.flags.noShow = 1;
      } else {
        pd.status = 'no_show';
        pd.flags.noShow = 1;
      }
    } else if (rostered.length === 0) {
      pd.status = onShift ? 'on_shift' : 'unrostered';
      pd.flags.unrostered = 1;
    } else {
      pd.status = onShift ? 'on_shift' : 'worked';
      // Start: measured against the first rostered piece still running when
      // they clocked in. Pieces already over by then (a split shift's morning)
      // were missed — that is the late start, not "7 hours late".
      const firstIn = clocked[0].start;
      pd.missedBefore = rostered.filter((r) => r.end !== null && r.end <= firstIn);
      const startPiece = rostered.find((r) => r.end === null || r.end > firstIn);
      if (startPiece && firstIn - startPiece.start > grace) pd.lateMins = firstIn - startPiece.start;
      if (pd.lateMins !== null || pd.missedBefore.length) pd.flags.late = 1;
      // Finish: the same from the other end, once they've clocked out.
      const lastOut = clocked[clocked.length - 1].end;
      if (!onShift && lastOut !== null) {
        pd.missedAfter = rostered.filter((r) => r.start >= lastOut && (now === null || r.start + grace < now));
        const endPiece = [...rostered].reverse().find((r) => r.start < lastOut);
        if (endPiece && endPiece.end !== null && endPiece.end - lastOut > grace) pd.earlyMins = endPiece.end - lastOut;
        if (pd.earlyMins !== null || pd.missedAfter.length) pd.flags.early = 1;
      }
      if (rosteredHours > 0 && workedHours > rosteredHours * (1 + overShare)) {
        pd.over = true;
        pd.flags.over = 1;
      }
    }
    personDays.push(pd);
  }

  personDays.sort((a, b) => (a.dayKey < b.dayKey ? -1 : a.dayKey > b.dayKey ? 1 : 0)
    || ((a.rostered[0] ?? a.clocked[0])?.start ?? 0) - ((b.rostered[0] ?? b.clocked[0])?.start ?? 0)
    || a.name.localeCompare(b.name));

  const people = new Map<string, PersonRow>();
  const days = new Map<string, DayRow>();
  const totals = emptyRollup();
  for (const d of personDays) {
    let p = people.get(d.personKey);
    if (!p) {
      p = { key: d.personKey, name: d.name, role: d.role, days: [], ...emptyRollup() };
      people.set(d.personKey, p);
    }
    if (!p.role && d.role) p.role = d.role;
    p.days.push(d);
    addInto(p, d);

    let day = days.get(d.dayKey);
    if (!day) {
      day = { dayKey: d.dayKey, dayLabel: d.dayLabel, people: [], ...emptyRollup() };
      days.set(d.dayKey, day);
    }
    day.people.push(d);
    addInto(day, d);
    addInto(totals, d);
  }

  const peopleRows = [...people.values()].sort((a, b) => b.workedHours - a.workedHours || b.rosteredHours - a.rosteredHours || a.name.localeCompare(b.name));
  const dayRows = [...days.values()].sort((a, b) => (a.dayKey < b.dayKey ? -1 : 1));

  const endWall = winEnd ? Date.UTC(winEnd.year, winEnd.month, winEnd.day) / 60000 + winEnd.minutes : null;
  const inProgress = /in progress/i.test(doc.window?.description || '') || (now !== null && endWall !== null && endWall > now);

  return { people: peopleRows, days: dayRows, totals, inProgress, flagCount: flagTotal(totals.flags) };
}

// ── words ─────────────────────────────────────────────────────────────────

/** 75 → "1 h 15 min", 12 → "12 min". */
export function minutesLabel(mins: number): string {
  const m = Math.round(Math.abs(mins));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}

/** "4:30 PM – 10:30 PM, 11:00 PM – 2:00 AM"; an open clock-in reads "since 7:00 PM". */
export function spansLabel(spans: Span[]): string {
  return spans.map((s) => (s.end === null ? `since ${s.startLabel}` : `${s.startLabel} – ${s.endLabel}`)).join(', ');
}

/** The window's description without the timezone and the in-progress aside. */
export function windowLabel(description: string | null | undefined): string {
  if (!description) return '';
  return description
    .replace(/\s*\(.*\)\s*$/, '')
    .replace(/\s+[A-Za-z_]+\/[A-Za-z_]+(?:\/[A-Za-z_]+)?\s*$/, '')
    .replace(/\s*→\s*/, ' to ')
    .trim();
}

/** Share of sales as "12.3%", or null when there were no sales to share. */
export function shareLabel(cost: number | null | undefined, sales: number | null | undefined): string | null {
  if (cost === null || cost === undefined || !sales || sales <= 0) return null;
  return `${((cost / sales) * 100).toFixed(1)}%`;
}
