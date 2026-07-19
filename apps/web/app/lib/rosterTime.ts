/**
 * Venue-local time for roster grids.
 *
 * The rule, borrowed from Loaded's own rostering implementation: **work in
 * instants, and only become venue-local at the render/parse boundary.** The
 * browser's timezone is never consulted — a manager in Auckland and one in
 * Sydney looking at the same venue must see the same grid.
 *
 * Two venue facts drive everything, both already stored on `Venue` and returned
 * by `/api/venues`:
 *   - `timezone`        IANA zone, e.g. "Pacific/Auckland"
 *   - `day_start_time`  when the business day starts, e.g. "07:00"
 *
 * The day-start matters more than it looks. Norm's business day runs 7:00am to
 * 6:59am (see `resolve_dates`), so a shift that finishes at 2am belongs to the
 * *previous* business day — put it on today's row and the roster disagrees with
 * every report the platform produces. Grid positions are therefore expressed as
 * minutes from the venue's day start, never as clock hours.
 *
 * Implemented on `Intl.DateTimeFormat` rather than a date library: it is the
 * only thing in the browser that knows IANA rules, and it costs no dependency.
 */

export interface VenueTimePrefs {
  /** IANA timezone, e.g. "Pacific/Auckland". */
  timeZone: string;
  /** Minutes after midnight that the business day starts (07:00 -> 420). */
  dayStartMinutes: number;
}

/** Matches Norm's stated business day (7:00am) and its default venue region. */
export const DEFAULT_TIME_PREFS: VenueTimePrefs = {
  timeZone: 'Pacific/Auckland',
  dayStartMinutes: 420,
};

/** Read prefs off a venue payload, falling back to Norm's defaults. */
export function venueTimePrefs(
  venue?: { timezone?: string | null; day_start_time?: string | null } | null,
): VenueTimePrefs {
  return {
    timeZone: venue?.timezone || DEFAULT_TIME_PREFS.timeZone,
    dayStartMinutes: parseDayStart(venue?.day_start_time),
  };
}

/** "07:00" -> 420. Anything unparseable falls back to the 7am default. */
export function parseDayStart(value?: string | null): number {
  const m = String(value ?? '').match(/^(\d{1,2}):(\d{2})/);
  if (!m) return DEFAULT_TIME_PREFS.dayStartMinutes;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return DEFAULT_TIME_PREFS.dayStartMinutes;
  return h * 60 + min;
}

const _dtfCache = new Map<string, Intl.DateTimeFormat>();

function dtf(timeZone: string): Intl.DateTimeFormat {
  let f = _dtfCache.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hour12: false,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    _dtfCache.set(timeZone, f);
  }
  return f;
}

interface TzParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
}

function partsInTz(instant: Date, timeZone: string): TzParts {
  const raw: Record<string, string> = {};
  for (const p of dtf(timeZone).formatToParts(instant)) {
    if (p.type !== 'literal') raw[p.type] = p.value;
  }
  // Some engines render midnight as hour 24 under hour12:false.
  const hour = Number(raw.hour) % 24;
  return {
    year: Number(raw.year),
    month: Number(raw.month),
    day: Number(raw.day),
    hour,
    minute: Number(raw.minute),
    second: Number(raw.second),
  };
}

/** The venue's UTC offset, in minutes, at that instant (DST-aware). */
export function tzOffsetMinutes(instant: Date, timeZone: string): number {
  const p = partsInTz(instant, timeZone);
  const asUTC = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return Math.round((asUTC - instant.getTime()) / 60000);
}

/** 720 -> "+12:00", -330 -> "-05:30". */
export function formatOffset(minutes: number): string {
  const sign = minutes >= 0 ? '+' : '-';
  const abs = Math.abs(minutes);
  return `${sign}${pad2(Math.floor(abs / 60))}:${pad2(abs % 60)}`;
}

/**
 * Render an instant as the venue's local ISO string *with its offset* —
 * `2026-07-19T09:00:00+12:00` — which is the shape connectors expect back.
 */
export function formatInTz(instant: Date, timeZone: string): string {
  const p = partsInTz(instant, timeZone);
  const off = formatOffset(tzOffsetMinutes(instant, timeZone));
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)}T${pad2(p.hour)}:${pad2(p.minute)}:${pad2(p.second)}${off}`;
}

/**
 * Resolve a venue wall-clock time to the instant it refers to.
 *
 * Done in two passes: guess using the offset at the naive instant, then correct
 * if that guess landed on the other side of a DST transition.
 */
export function wallClockToInstant(
  date: string,
  minutesAfterMidnight: number,
  timeZone: string,
): Date {
  const [y, m, d] = date.split('-').map(Number);
  const naiveUTC = Date.UTC(y, (m || 1) - 1, d || 1) + minutesAfterMidnight * 60000;
  const firstGuess = tzOffsetMinutes(new Date(naiveUTC), timeZone);
  let instant = new Date(naiveUTC - firstGuess * 60000);
  const settled = tzOffsetMinutes(instant, timeZone);
  if (settled !== firstGuess) instant = new Date(naiveUTC - settled * 60000);
  return instant;
}

/** The instant the business day `date` (YYYY-MM-DD) begins at this venue. */
export function dayStartInstant(date: string, prefs: VenueTimePrefs): Date {
  return wallClockToInstant(date, prefs.dayStartMinutes, prefs.timeZone);
}

/** ISO form of {@link dayStartInstant}, carrying the venue's offset. */
export function dayStartISO(date: string, prefs: VenueTimePrefs): string {
  return formatInTz(dayStartInstant(date, prefs), prefs.timeZone);
}

/**
 * Which business day an instant falls in. A 2am finish belongs to the day
 * before, because the business day starts at `dayStartMinutes`.
 */
export function companyDayDate(iso: string | Date, prefs: VenueTimePrefs): string {
  const instant = iso instanceof Date ? iso : new Date(iso);
  const shifted = new Date(instant.getTime() - prefs.dayStartMinutes * 60000);
  const p = partsInTz(shifted, prefs.timeZone);
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)}`;
}

/** Minutes from the start of business day `date` — the grid's x coordinate. */
export function minutesIntoDay(iso: string | Date, date: string, prefs: VenueTimePrefs): number {
  const instant = iso instanceof Date ? iso : new Date(iso);
  return Math.round((instant.getTime() - dayStartInstant(date, prefs).getTime()) / 60000);
}

/** Inverse of {@link minutesIntoDay}: a grid position back to a real timestamp. */
export function offsetToISO(date: string, offsetMinutes: number, prefs: VenueTimePrefs): string {
  const instant = new Date(dayStartInstant(date, prefs).getTime() + offsetMinutes * 60000);
  return formatInTz(instant, prefs.timeZone);
}

/** The venue's offset at an instant, e.g. "+12:00" — for building timestamps. */
export function venueOffset(instant: Date, prefs: VenueTimePrefs): string {
  return formatOffset(tzOffsetMinutes(instant, prefs.timeZone));
}

/**
 * Clock label in the venue's zone, e.g. "9:30". Use this for anything a user
 * reads off the grid — formatting with the browser's clock would show a Sydney
 * manager the wrong times for an Auckland venue.
 */
export function formatClock(iso: string | Date, prefs: VenueTimePrefs): string {
  if (!iso) return '';
  const instant = iso instanceof Date ? iso : new Date(iso);
  if (isNaN(instant.getTime())) return '';
  const p = partsInTz(instant, prefs.timeZone);
  return `${p.hour}:${pad2(p.minute)}`;
}

/** Grid header label for a minutes-after-midnight position, e.g. 420 -> "7am". */
export function formatHourLabel(minutesAfterMidnight: number): string {
  const total = ((minutesAfterMidnight % 1440) + 1440) % 1440;
  const h = Math.floor(total / 60);
  const m = total % 60;
  const suffix = h < 12 || h === 24 ? 'am' : 'pm';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m ? `${h12}:${pad2(m)}${suffix}` : `${h12}${suffix}`;
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}
