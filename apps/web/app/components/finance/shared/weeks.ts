/**
 * Pay weeks. A pay week starts on a Monday at the venue's day start and ends
 * the next Monday at day start, less a second; the API keys everything by
 * `week_start` (a Monday date) and shows `week_ending` (the Sunday). All
 * arithmetic is on calendar dates (Date.UTC), never through the browser's
 * zone; "this week" asks the venue's zone and day start through
 * lib/rosterTime (3am on a Monday is still last week).
 */
import { tradingMonday, venueTimePrefs } from '../../../lib/rosterTime';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function parts(iso: string): [number, number, number] {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return [y, m, d];
}

function toIso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** "2026-10-30" + 3 → "2026-11-02" (calendar days; no zone involved). */
export function addDays(iso: string, n: number): string {
  const [y, m, d] = parts(iso);
  return toIso(new Date(Date.UTC(y, m - 1, d + n)));
}

/** 0 = Sunday … 6 = Saturday, from the calendar date alone. */
export function weekday(iso: string): number {
  const [y, m, d] = parts(iso);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

/** The Monday of the week a date falls in (a Monday is its own). */
export function mondayOf(iso: string): string {
  return addDays(iso, -((weekday(iso) + 6) % 7));
}

export function isMonday(iso: string): boolean {
  return weekday(iso) === 1;
}

/** The Sunday that ends the pay week starting `weekStart`. */
export function weekEnding(weekStart: string): string {
  return addDays(weekStart, 6);
}

/** `n` weeks on from a week start (negative for back). */
export function shiftWeek(weekStart: string, n: number): string {
  return addDays(weekStart, 7 * n);
}

/** "18 Oct" / "18 Oct 2026" — a plain date without the weekday. */
export function shortDate(iso: string, year = false): string {
  const [y, m, d] = parts(iso);
  return `${d} ${MONTHS[m - 1]}${year ? ` ${y}` : ''}`;
}

/** "Sun 18 Oct 2026". */
export function dayLabel(iso: string, year = true): string {
  return `${DAYS[weekday(iso)]} ${shortDate(iso, year)}`;
}

/** "WE 18 Oct" (the week-ending label every payroll screen uses); `year` adds it. */
export function weLabel(weekStart: string, year = false): string {
  return `WE ${shortDate(weekEnding(weekStart), year)}`;
}

/** "Mon 12 – Sun 18 Oct 2026" (both months when they differ). */
export function weekRangeLabel(weekStart: string): string {
  const end = weekEnding(weekStart);
  const [sy, sm] = parts(weekStart);
  const [ey, em] = parts(end);
  const startText = sm === em && sy === ey ? `${DAYS[1]} ${parts(weekStart)[2]}` : `${DAYS[1]} ${shortDate(weekStart, sy !== ey)}`;
  return `${startText} – ${DAYS[0]} ${shortDate(end, true)}`;
}

/** "WE dd.mm.yyyy" as Xero narrations and references carry it. */
export function weDotted(weekStart: string): string {
  const [y, m, d] = parts(weekEnding(weekStart));
  return `WE ${String(d).padStart(2, '0')}.${String(m).padStart(2, '0')}.${y}`;
}

export interface VenueLike {
  timezone?: string | null;
  day_start_time?: string | null;
  day_start?: string | null;
}

/** The Monday of the pay week we are in now, at this venue (day-start aware). */
export function thisWeekStart(venue?: VenueLike | null, now: Date = new Date()): string {
  const prefs = venueTimePrefs({ timezone: venue?.timezone, day_start_time: venue?.day_start_time ?? venue?.day_start });
  return tradingMonday(now, prefs);
}

/** The Monday of the last completed pay week. */
export function lastWeekStart(venue?: VenueLike | null, now: Date = new Date()): string {
  return shiftWeek(thisWeekStart(venue, now), -1);
}

/** The week starts a picker offers: `back` weeks before the anchor and
 *  `ahead` after it, newest first. */
export function weekOptions(anchor: string, back = 52, ahead = 1): string[] {
  const out: string[] = [];
  for (let i = ahead; i >= -back; i -= 1) out.push(shiftWeek(anchor, i));
  return out;
}

/** Any date in a week → that week's Monday; a Monday is kept as it is. */
export function snapToWeekStart(iso: string): string {
  return mondayOf(iso);
}
