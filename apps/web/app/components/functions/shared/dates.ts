/**
 * Norm Functions — plain calendar dates ("2026-10-17"), shared by the Events
 * page and the Reports periods.
 *
 * Event dates and report periods are venue-local PLANS. They are never put
 * through `new Date()` in the browser's zone: date arithmetic works on the
 * calendar date alone (Date.UTC), and "today" is asked of the company's time
 * zone, so a laptop in another zone sees the same dates.
 */

/** Today's calendar date in an IANA zone, as "YYYY-MM-DD". */
export function todayIn(timeZone: string, now: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(now);
    const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
    return `${get('year')}-${get('month')}-${get('day')}`;
  } catch {
    return now.toISOString().slice(0, 10);
  }
}

/** "2026-10-30" + 3 → "2026-11-02" (calendar days; no zone involved). */
export function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}
