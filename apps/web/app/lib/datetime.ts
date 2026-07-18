/**
 * Timezone-offset helpers.
 *
 * Connector timestamps carry an explicit `+HH:MM` offset, and anything we send
 * back has to sit in the same zone as what we were given. Several components
 * used to hardcode `+13:00` (NZ *daylight* time), which is wrong for roughly
 * five months of the year — NZ standard time is `+12:00` — and wrong for any
 * venue outside New Zealand.
 *
 * Rule of thumb: take the offset from the data when you have it
 * (`tzOffsetOf`), and only fall back to the viewer's own offset for that date
 * (`localTzOffset`) when there is no data to read it from. Never hardcode.
 */

/**
 * Read the timezone off an ISO timestamp: a `+HH:MM` / `-HH:MM` suffix, or a
 * trailing `Z` (which is simply `+00:00`). Returns null when the timestamp is
 * zone-less, so callers can decide their own fallback.
 */
export function tzOffsetOf(value: unknown): string | null {
  const s = String(value ?? '');
  if (/\dT[\d:.]+Z$/.test(s)) return '+00:00';
  const m = s.match(/([+-]\d{2}:\d{2})$/);
  return m ? m[1] : null;
}

/**
 * The browser's UTC offset *for that specific date*, so daylight saving is
 * applied correctly rather than assumed.
 */
export function localTzOffset(date: Date): string {
  const mins = -date.getTimezoneOffset();
  const sign = mins >= 0 ? '+' : '-';
  const abs = Math.abs(mins);
  return `${sign}${pad2(Math.floor(abs / 60))}:${pad2(abs % 60)}`;
}

/**
 * Format a Date as a connector-style local ISO string with an explicit offset.
 * Pass the offset you want (usually from `tzOffsetOf`); defaults to the
 * viewer's offset for that date.
 */
export function formatWithOffset(d: Date, tzOffset?: string, withSeconds = true): string {
  const tz = tzOffset || localTzOffset(d);
  const time = withSeconds
    ? `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
    : `${pad2(d.getHours())}:${pad2(d.getMinutes())}:00`;
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${time}${tz}`;
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}
