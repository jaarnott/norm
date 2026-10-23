/**
 * Pure logic for the Sales Analysis page (Reports → Sales Analysis). Every
 * figure comes from `loadedhub.get_sales`; this file only reads its documents,
 * ranks and sums rows, and lines two periods up day by day. It never works out
 * a trading day: periods go to the tool in plain English, and the one date
 * step here (the period before a week, month or day) is calendar-date only —
 * the "week beginning <date>" / "<Month YYYY>" / "<YYYY-MM-DD>" phrases the
 * tool resolves on the venue's own trading day.
 */

// ── the tool's documents (real field names, checked against get_sales) ──

export interface SalesWindow {
  start: string;
  end: string;
  label?: string;
  description?: string;
  day_start?: string;
  incomplete?: boolean;
  /** "now", when the window is still in progress. */
  through?: string;
}

/** breakdown 'daily' with compare ['budget', 'last_year']. */
export interface DailyRow {
  venue?: string;
  date: string;
  day: string;
  actual: number;
  budget?: number;
  last_year?: number;
}

export interface DailyDoc {
  window: SalesWindow;
  rows: DailyRow[];
  tax?: string;
  last_year_window?: { start: string; end: string; note?: string };
}

/** breakdown 'items': `group` is the POS group, `category` the revenue group
 *  (Loaded reports an item's category as its revenue group once one is set;
 *  until then it is the POS category). */
export interface ItemRow {
  item: string;
  group?: string;
  category?: string;
  sales: number;
  quantity: number;
}

export interface ItemsDoc {
  window: SalesWindow;
  rows: ItemRow[];
  totals?: { row_count?: number; sales?: number; quantity?: number };
  note?: string;
}

/** breakdown 'staff'. */
export interface StaffRow {
  staff: string;
  orders: number;
  sales: number;
}

export interface StaffDoc {
  window: SalesWindow;
  rows: StaffRow[];
  totals?: { sales?: number; orders?: number; staff_count?: number };
}

/** The document a tool call returned, wherever the response put it, or the
 *  tool's own error. `key` is a field only that document carries. */
export function readDoc<T>(body: unknown, key: string): { doc: T | null; error: string | null } {
  const outer = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const inner = (outer.data && typeof outer.data === 'object' ? outer.data : null) as Record<string, unknown> | null;
  for (const d of [inner, outer]) {
    if (d && typeof d.error === 'string' && d.error) return { doc: null, error: d.error };
  }
  for (const d of [inner, outer]) {
    if (d && Array.isArray(d[key])) return { doc: d as unknown as T, error: null };
  }
  const detail = typeof outer.detail === 'string' ? outer.detail : null;
  return { doc: null, error: detail || 'Loaded sent back nothing to show.' };
}

/** A load that failed because the venue has no working Loaded connection. */
export function isConnectionError(message: string | null | undefined): boolean {
  return !!message && /No credentials|not connected|needs_reconnect|reconnect/i.test(message);
}

// ── periods ──────────────────────────────────────────────────────────────

export type Period = 'yesterday' | 'this week' | 'last week' | 'this month' | 'last month';

export const PERIODS: { value: Period; label: string }[] = [
  { value: 'yesterday', label: 'Yesterday' },
  { value: 'this week', label: 'This week' },
  { value: 'last week', label: 'Last week' },
  { value: 'this month', label: 'This month' },
  { value: 'last month', label: 'Last month' },
];

export type Comparison = 'previous' | 'last_year' | 'budget';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

/** A calendar date `days` away (no clock, so no daylight-saving hazard). */
export function shiftDate(iso: string, days: number): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + days));
  return t.toISOString().slice(0, 10);
}

/** The plain-English period just before `period`, given the window the tool
 *  resolved it to (its start's calendar date anchors weeks, months and days). */
export function previousPeriod(period: Period, windowStart: string): string {
  const startDate = windowStart.slice(0, 10);
  switch (period) {
    case 'this week': return 'last week';
    case 'this month': return 'last month';
    case 'last week': return `week beginning ${shiftDate(startDate, -7)}`;
    case 'yesterday': return shiftDate(startDate, -1);
    case 'last month': {
      const [y, m] = startDate.split('-').map(Number);
      const prevMonth = m === 1 ? 12 : m - 1;
      const prevYear = m === 1 ? y - 1 : y;
      return `${MONTHS[prevMonth - 1]} ${prevYear}`;
    }
  }
}

/** What the comparison is called, for this period. */
export function comparisonLabel(comparison: Comparison, period: Period): string {
  if (comparison === 'budget') return 'Budget';
  if (comparison === 'last_year') return 'Last year';
  if (period === 'yesterday') return 'Day before';
  if (period.endsWith('week')) return 'Week before';
  return 'Month before';
}

export const COMPARISON_OPTIONS: { value: Comparison; label: string }[] = [
  { value: 'previous', label: 'vs the period before' },
  { value: 'last_year', label: 'vs same time last year' },
  { value: 'budget', label: 'vs budget' },
];

// ── dates for people ─────────────────────────────────────────────────────

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** "Mon 12 Oct" from "2026-10-12" (a calendar date, shown as written). */
export function dayLabel(iso: string, withYear = false): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  if (Number.isNaN(t.getTime())) return iso;
  const text = `${WEEKDAYS[t.getUTCDay()]} ${d} ${MONTHS[m - 1].slice(0, 3)}`;
  return withYear ? `${text} ${y}` : text;
}

/** "Mon 12 Oct – Sun 18 Oct 2026" from the first and last trading dates. */
export function rangeLabel(first: string | undefined, last: string | undefined): string {
  if (!first) return '';
  if (!last || last === first) return dayLabel(first, true);
  return `${dayLabel(first)} – ${dayLabel(last, true)}`;
}

/** "7:00am" from "07:00". */
export function clockLabel(hhmm: string | undefined): string | null {
  if (!hhmm) return null;
  const [h, m] = hhmm.split(':').map(Number);
  if (Number.isNaN(h)) return null;
  const suffix = h < 12 ? 'am' : 'pm';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${String(m || 0).padStart(2, '0')}${suffix}`;
}

/** The trading date `through` falls on: before the day start it is still the
 *  previous trading day. Null when the window is complete. */
export function tradingDateOf(window: SalesWindow): string | null {
  if (!window.incomplete || !window.through) return null;
  const date = window.through.slice(0, 10);
  const time = window.through.slice(11, 16);
  const dayStart = (window.day_start || '00:00').slice(0, 5);
  return time && time < dayStart ? shiftDate(date, -1) : date;
}

// ── compare: two periods, day by day ─────────────────────────────────────

export interface CompareRow {
  date: string;
  day: string;
  actual: number | null;
  /** The comparison's figure for the matching day (null: none to compare). */
  other: number | null;
  /** The comparison's own date, when it is a different period. */
  otherDate?: string;
  diff: number | null;
  pct: number | null;
  /** Not traded yet (later in a period still in progress). */
  future: boolean;
}

export interface CompareResult {
  rows: CompareRow[];
  /** Totals over the days both sides have traded. */
  actual: number;
  other: number;
  diff: number;
  pct: number | null;
  /** Days counted in the totals (both sides traded). */
  days: number;
  /** The comparison's own first and last date among the days counted (the
   *  period before only). */
  otherRange: [string, string] | null;
  /** The comparison has no figures at all (no budget set / no sales a year ago). */
  otherEmpty: boolean;
  best: { date: string; day: string; actual: number } | null;
}

export function pctChange(actual: number, other: number): number | null {
  if (!other) return null;
  return Math.round(((actual - other) / Math.abs(other)) * 1000) / 10;
}

const round2 = (n: number) => Math.round(n * 100) / 100;

/**
 * Line the period up against its comparison. budget / last_year come on the
 * same rows; the period before is its own document, matched day for day in
 * order (Monday with Monday, the 1st with the 1st).
 */
export function buildCompare(current: DailyDoc, comparison: Comparison, previous?: DailyDoc | null): CompareResult {
  const through = tradingDateOf(current.window);
  const prevRows = previous?.rows ?? [];
  const rows: CompareRow[] = current.rows.map((r, i) => {
    const future = !!through && r.date > through;
    let other: number | null;
    let otherDate: string | undefined;
    if (comparison === 'previous') {
      const p = prevRows[i];
      other = p ? p.actual : null;
      otherDate = p?.date;
    } else {
      const v = comparison === 'budget' ? r.budget : r.last_year;
      other = typeof v === 'number' ? v : null;
    }
    const actual = future ? null : r.actual;
    const diff = actual !== null && other !== null ? round2(actual - other) : null;
    const pct = actual !== null && other !== null ? pctChange(actual, other) : null;
    return { date: r.date, day: r.day, actual, other, otherDate, diff, pct, future };
  });
  const counted = rows.filter((r) => !r.future && r.actual !== null && r.other !== null);
  const actual = round2(counted.reduce((s, r) => s + (r.actual ?? 0), 0));
  const other = round2(counted.reduce((s, r) => s + (r.other ?? 0), 0));
  const traded = rows.filter((r) => !r.future && r.actual !== null);
  const best = traded.reduce<CompareRow | null>((b, r) => (b === null || (r.actual ?? 0) > (b.actual ?? 0) ? r : b), null);
  return {
    rows,
    actual,
    other,
    diff: round2(actual - other),
    pct: pctChange(actual, other),
    days: counted.length,
    otherRange: counted.length && counted[0].otherDate && counted[counted.length - 1].otherDate
      ? [counted[0].otherDate, counted[counted.length - 1].otherDate as string]
      : null,
    otherEmpty: rows.every((r) => !r.other),
    best: best && (best.actual ?? 0) > 0 ? { date: best.date, day: best.day, actual: best.actual ?? 0 } : null,
  };
}

/** Sales in the period, every traded day (the headline figure). */
export function periodTotal(current: DailyDoc): number {
  const through = tradingDateOf(current.window);
  return round2(current.rows.filter((r) => !through || r.date <= through).reduce((s, r) => s + (r.actual || 0), 0));
}

// ── products ─────────────────────────────────────────────────────────────

export const REVENUE_GROUPS = ['Food', 'Beverage', 'Other'] as const;

export interface GroupShare {
  group: string;
  sales: number;
  quantity: number;
  items: number;
  /** Share of item sales, 0–100. */
  share: number;
  /** One of Loaded's revenue groups (Food, Beverage, Other). */
  revenueGroup: boolean;
}

/** The revenue group an item row reports under ('Unassigned' when blank). */
export function groupOf(row: ItemRow): string {
  const c = (row.category || '').trim();
  if (!c || /^uncategori[sz]ed$/i.test(c)) return 'Unassigned';
  const known = REVENUE_GROUPS.find((g) => g.toLowerCase() === c.toLowerCase());
  return known ?? c;
}

/** Item sales by revenue group: Food, Beverage, Other first, then anything
 *  else Loaded reported, then Unassigned. Rows only (no "(others)" roll-up). */
export function groupSplit(rows: ItemRow[]): GroupShare[] {
  const real = rows.filter((r) => r.item !== '(others)');
  const total = real.reduce((s, r) => s + (r.sales || 0), 0);
  const by = new Map<string, GroupShare>();
  for (const r of real) {
    const g = groupOf(r);
    const cur = by.get(g) ?? { group: g, sales: 0, quantity: 0, items: 0, share: 0, revenueGroup: (REVENUE_GROUPS as readonly string[]).includes(g) };
    cur.sales += r.sales || 0;
    cur.quantity += r.quantity || 0;
    cur.items += 1;
    by.set(g, cur);
  }
  const rank = (g: string) => {
    const i = (REVENUE_GROUPS as readonly string[]).indexOf(g);
    if (i >= 0) return i;
    return g === 'Unassigned' ? 99 : 50;
  };
  return [...by.values()]
    .map((g) => ({ ...g, sales: round2(g.sales), quantity: round2(g.quantity), share: total ? Math.round((g.sales / total) * 1000) / 10 : 0 }))
    .sort((a, b) => rank(a.group) - rank(b.group) || b.sales - a.sales);
}

export type RankBy = 'sales' | 'quantity';

/** Best sellers first. */
export function topSellers(rows: ItemRow[], by: RankBy, n: number): ItemRow[] {
  return rows
    .filter((r) => r.item !== '(others)')
    .slice()
    .sort((a, b) => (b[by] || 0) - (a[by] || 0) || a.item.localeCompare(b.item))
    .slice(0, n);
}

/** Slowest sellers first — only items that took money (by sales) or sold at
 *  least one (by quantity): $0 modifiers and free items would crowd the list. */
export function bottomSellers(rows: ItemRow[], by: RankBy, n: number): ItemRow[] {
  return rows
    .filter((r) => r.item !== '(others)' && (r[by] || 0) > 0)
    .slice()
    .sort((a, b) => (a[by] || 0) - (b[by] || 0) || a.item.localeCompare(b.item))
    .slice(0, n);
}

/** Items whose name or POS group contains the text (case-insensitive). */
export function searchItems(rows: ItemRow[], text: string): ItemRow[] {
  const q = text.trim().toLowerCase();
  const real = rows.filter((r) => r.item !== '(others)');
  if (!q) return real;
  return real.filter((r) => r.item.toLowerCase().includes(q) || (r.group || '').toLowerCase().includes(q));
}

// ── staff ────────────────────────────────────────────────────────────────

export interface StaffLine extends StaffRow {
  average: number | null;
  /** Share of all staff sales, 0–100. */
  share: number;
}

export function staffLines(rows: StaffRow[]): StaffLine[] {
  const total = rows.reduce((s, r) => s + (r.sales || 0), 0);
  return rows
    .filter((r) => r.staff !== '(others)' || r.sales > 0)
    .slice()
    .sort((a, b) => b.sales - a.sales)
    .map((r) => ({
      ...r,
      average: r.orders ? round2(r.sales / r.orders) : null,
      share: total ? Math.round((r.sales / total) * 1000) / 10 : 0,
    }));
}
