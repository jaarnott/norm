/**
 * Pure geometry for the day timeline.
 *
 * Kept separate from the component (as Loaded do with their `grid/timeMath`)
 * so the arithmetic that decides *where a shift lands* can be tested without
 * rendering React or simulating a drag.
 *
 * Everything is measured in minutes from the venue's business-day start — never
 * clock hours, and never the browser's clock. See `app/lib/rosterTime.ts`.
 */

import type { Shift } from './shared';
import type { VenueTimePrefs } from '../../../lib/rosterTime';
import { companyDayDate, minutesIntoDay } from '../../../lib/rosterTime';

export const HOUR_W = 60;
/** Window length: the venue's day start, plus this many hours. */
export const DAY_HOURS = 20;
export const TIMELINE_W = DAY_HOURS * HOUR_W;
export const SNAP_MINUTES = 15;

export const minutesToPx = (min: number) => (min * HOUR_W) / 60;
export const pxToMinutes = (px: number) => (px * 60) / HOUR_W;
export const clampPx = (px: number) => Math.max(0, Math.min(px, TIMELINE_W));

/** Snap a pixel position to the grid interval. */
export const snapPx = (px: number) =>
  minutesToPx(Math.round(pxToMinutes(px) / SNAP_MINUTES) * SNAP_MINUTES);

/** Horizontal position of a timestamp on the day `dayDate` (YYYY-MM-DD). */
export function timeToOffset(time: string | undefined, dayDate: string, prefs: VenueTimePrefs): number {
  if (!time) return 0;
  const d = new Date(time);
  if (isNaN(d.getTime())) return 0;
  return clampPx(minutesToPx(minutesIntoDay(d, dayDate, prefs)));
}

/**
 * The shifts belonging to a business day.
 *
 * Membership is by *business* day, so a shift that starts at 11pm and one that
 * starts at 2am the following morning both belong to the same day's roster.
 * Deleted shifts are excluded.
 */
export function shiftsForDay(shifts: Shift[], dayDate: string, prefs: VenueTimePrefs): Shift[] {
  return shifts.filter(
    s => !s.datestampDeleted && s.clockinTime && companyDayDate(s.clockinTime as string, prefs) === dayDate,
  );
}

/** Position of the "now" marker, or null when now isn't inside this day. */
export function nowOffset(dayDate: string, prefs: VenueTimePrefs, now: Date = new Date()): number | null {
  const mins = minutesIntoDay(now, dayDate, prefs);
  if (mins < 0 || mins > DAY_HOURS * 60) return null;
  return minutesToPx(mins);
}
