/**
 * Norm Functions — display formatting. Money is integer cents in the company's
 * currency; plans (event dates, start times, preferred dates) are venue-local
 * wall-clock values and must NOT be shifted through the browser's time zone;
 * facts (sent/viewed/confirmed) are UTC instants shown in the venue's zone.
 */

const moneyFormats = new Map<string, Intl.NumberFormat>();

/** 123456 → "$1,234.56" (or "$1,235" with dp 0) in the company currency. */
export function money(cents: number | null | undefined, currency = 'NZD', dp: 0 | 2 = 2): string {
  if (cents === null || cents === undefined || Number.isNaN(cents)) return '—';
  const key = `${currency}:${dp}`;
  let fmt = moneyFormats.get(key);
  if (!fmt) {
    fmt = new Intl.NumberFormat('en-NZ', {
      style: 'currency',
      currency,
      minimumFractionDigits: dp,
      maximumFractionDigits: dp,
    });
    moneyFormats.set(key, fmt);
  }
  return fmt.format(cents / 100);
}

/** "12.50" / "12" / "" → cents (null when blank or not a number). */
export function parseMoney(input: string): number | null {
  const cleaned = input.replace(/[^0-9.\-]/g, '');
  if (!cleaned) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? Math.round(n * 100) : null;
}

/** Plain date "2026-10-18" → "Sat 18 Oct 2026" (no time-zone shifting). */
export function dateLabel(isoDate: string | null | undefined, opts: { year?: boolean; weekday?: boolean } = {}): string {
  if (!isoDate) return '—';
  const [y, m, d] = isoDate.slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return isoDate;
  const dt = new Date(Date.UTC(y, m - 1, d, 12));
  return dt
    .toLocaleDateString('en-NZ', {
      timeZone: 'UTC',
      weekday: opts.weekday === false ? undefined : 'short',
      day: 'numeric',
      month: 'short',
      year: opts.year === false ? undefined : 'numeric',
    })
    .replace(',', ''); // en-NZ puts a comma after the weekday: "Sun, 18 Oct"

}

/** Local wall-clock "18:30" / "18:30:00" → "6:30 pm". */
export function timeLabel(t: string | null | undefined): string {
  if (!t) return '';
  const [hh, mm] = t.split(':').map(Number);
  if (Number.isNaN(hh)) return t;
  const suffix = hh >= 12 ? 'pm' : 'am';
  const h12 = hh % 12 === 0 ? 12 : hh % 12;
  return `${h12}:${String(mm || 0).padStart(2, '0')} ${suffix}`;
}

/** A plan's time range: "6:30 pm – 11:00 pm" (+ "(next day)" when it ends after midnight). */
export function timeRangeLabel(start?: string | null, end?: string | null, endsNextDay?: boolean): string {
  if (!start && !end) return '';
  const s = timeLabel(start);
  const e = timeLabel(end);
  return [s, e].filter(Boolean).join(' – ') + (endsNextDay && e ? ' (next day)' : '');
}

/** UTC instant → "Sat 18 Oct, 6:30 pm" in the given IANA zone (venue or company). */
export function instantLabel(iso: string | null | undefined, timeZone = 'Pacific/Auckland', withYear = false): string {
  if (!iso) return '—';
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleString('en-NZ', {
    timeZone,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: withYear ? 'numeric' : undefined,
    hour: 'numeric',
    minute: '2-digit',
  });
}

/** "3 days ago" / "in 2 days" / "today" for a UTC instant, relative to now. */
export function relativeLabel(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '';
  const dt = new Date(iso);
  const days = Math.round((dt.getTime() - now.getTime()) / 86_400_000);
  if (days === 0) return 'today';
  if (days === -1) return 'yesterday';
  if (days === 1) return 'tomorrow';
  return days < 0 ? `${-days} days ago` : `in ${days} days`;
}
