/**
 * The Weekly Report's figures — pure logic, no React.
 *
 * The page reads three Loaded tools for "last week" (each resolves the period
 * on the venue's trading day itself; nothing here works out a date):
 *
 *  - `get_daily_performance` (venues 'all', interval 'week') → one row per
 *    venue: sales incl. GST, budget, last year, labour cost and wage %;
 *  - `get_sales` (breakdown 'items', top 10) → the top sellers, merged across
 *    the venues asked for;
 *  - `get_cogs_detail_for_period` (one venue per call) → cost of goods, from
 *    its `summary.column_sums` (revenue ex GST and cost).
 *
 * The emailed report (schedule.ts → buildPrompt) asks for the same tables
 * with the same columns, so what the page shows is what the email holds.
 */

export interface TradingWindow {
  start?: string;
  end?: string;
  label?: string;
  timezone?: string;
  day_start?: string;
  description?: string;
}

/** One venue's week (a `by_venue` row) or the group's (`summary`). */
export interface PerfRow {
  venue?: string;
  sales?: number | null;
  sales_ex_gst?: number | null;
  budget?: number | null;
  last_year?: number | null;
  orders?: number | null;
  actual_hours?: number | null;
  actual_cost?: number | null;
  rostered_cost?: number | null;
  /** sales ÷ budget (0.93 = 93% of budget). */
  budget_pct?: number | null;
  /** sales ÷ last year − 1; null when the venue has no last-year figure. */
  vs_last_year_pct?: number | null;
  /** actual labour cost ÷ sales ex GST. */
  wage_pct?: number | null;
}

export interface PerfDoc {
  window?: TradingWindow;
  summary?: PerfRow & { venues?: string[] | null };
  by_venue?: PerfRow[];
  warnings?: unknown[];
  _note?: string;
}

export interface ItemRow {
  item: string;
  group?: string | null;
  category?: string | null;
  sales?: number | null;
  quantity?: number | null;
}

export interface ItemsDoc {
  window?: TradingWindow;
  rows?: ItemRow[];
  totals?: { row_count?: number; sales?: number; quantity?: number };
  note_amounts?: string;
}

export interface CogsDoc {
  window?: TradingWindow;
  summary?: { row_count?: number; column_sums?: { cost?: number; revenue?: number; discounts?: number; quantity?: number } };
}

/** A tool's document, wherever the caller put it (`body.data` or the body),
 *  recognised by a key it always carries. */
export function extractDoc<T>(body: unknown, key: string): T | null {
  if (!body || typeof body !== 'object') return null;
  const b = body as Record<string, unknown>;
  const inner = b.data;
  if (inner && typeof inner === 'object' && key in (inner as Record<string, unknown>)) return inner as T;
  if (key in b) return b as T;
  return null;
}

/** Why a tool call failed, in its own words, or null when it didn't. */
export function toolError(res: { ok: boolean; status: number }, body: unknown): string | null {
  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const inner = (b.data && typeof b.data === 'object' ? b.data : {}) as Record<string, unknown>;
  const msg = b.detail ?? b.error ?? inner.error;
  if (!res.ok) return typeof msg === 'string' && msg ? msg : `The request failed (${res.status})`;
  if (b.success === false) return typeof msg === 'string' && msg ? msg : 'Loaded returned nothing for last week';
  return null;
}

/** What a tool said instead of answering (`data.error` / `data.message`),
 *  for a 200 that carries no document. */
export function toolMessage(body: unknown): string | null {
  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const inner = (b.data && typeof b.data === 'object' ? b.data : {}) as Record<string, unknown>;
  for (const m of [inner.error, inner.message, b.error, b.message]) {
    if (typeof m === 'string' && m.trim()) return m.trim();
  }
  return null;
}

const num = (n: unknown): number | null => (typeof n === 'number' && Number.isFinite(n) ? n : null);

/** `actual` against `base` as a fraction (0.057 = 5.7% up); null without a base. */
export function change(actual: unknown, base: unknown): number | null {
  const a = num(actual);
  const b = num(base);
  if (a === null || b === null || b === 0) return null;
  return a / b - 1;
}

/** 0.0572 → "+5.7%" (signed) or "5.7%". */
export function formatPct(fraction: number | null | undefined, { signed = false, dp = 1 } = {}): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return '—';
  const v = Math.round(fraction * 100 * 10 ** dp) / 10 ** dp;
  const text = Math.abs(v).toFixed(dp);
  if (!signed) return `${v < 0 ? '−' : ''}${text}%`;
  if (v === 0) return `${text}%`;
  return `${v > 0 ? '+' : '−'}${text}%`;
}

/** Group sales against last year counting only venues that have a last-year
 *  figure — a venue that wasn't trading a year ago would otherwise read as
 *  growth (local data, Oct 2026: +36% for the group, −11% like for like). */
export function likeForLike(rows: PerfRow[]): { sales: number; lastYear: number; change: number | null; venues: number; of: number } {
  let sales = 0;
  let lastYear = 0;
  let venues = 0;
  for (const r of rows) {
    const ly = num(r.last_year);
    const s = num(r.sales);
    if (ly === null || ly <= 0 || s === null) continue;
    sales += s;
    lastYear += ly;
    venues += 1;
  }
  return { sales, lastYear, change: change(sales, lastYear), venues, of: rows.length };
}

export interface CogsLine {
  venueId: string;
  venue: string;
  revenue: number | null;
  cost: number | null;
  /** cost ÷ revenue (ex GST). */
  pct: number | null;
  error?: string | null;
}

export function cogsLine(venueId: string, venue: string, doc: CogsDoc | null, error?: string | null): CogsLine {
  const sums = doc?.summary?.column_sums;
  const revenue = num(sums?.revenue);
  const cost = num(sums?.cost);
  return {
    venueId,
    venue,
    revenue,
    cost,
    pct: revenue && cost !== null ? cost / revenue : null,
    error: error ?? null,
  };
}

/** The group's cost of goods: the venues that answered, added up. */
export function cogsTotal(lines: CogsLine[]): { revenue: number; cost: number; pct: number | null; venues: number } {
  let revenue = 0;
  let cost = 0;
  let venues = 0;
  for (const l of lines) {
    if (l.revenue === null || l.cost === null) continue;
    revenue += l.revenue;
    cost += l.cost;
    venues += 1;
  }
  return { revenue, cost, pct: revenue ? cost / revenue : null, venues };
}

/** The top sellers without the "(others)" roll-up row. */
export function topSellers(doc: ItemsDoc | null, limit = 10): ItemRow[] {
  return (doc?.rows ?? []).filter((r) => r && r.item && r.item !== '(others)').slice(0, limit);
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-10-12" → "Mon 12 Oct". Calendar arithmetic in UTC so the browser's
 *  own timezone can never shift the day. */
function dayLabel(ymd: string, minusDays = 0): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd);
  if (!m) return null;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] - minusDays));
  return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

/**
 * The trading days a window covers, as the tool returned it:
 * `{start: "2026-10-12T07:00:00+13:00", end: "2026-10-19T06:59:59+13:00",
 * day_start: "07:00"}` → "Mon 12 Oct to Sun 18 Oct". The end instant falls
 * before the trading day starts, so it belongs to the day before. The
 * timestamps carry the venue's own offset, so their date part is local.
 */
export function tradingDays(w: TradingWindow | null | undefined): string | null {
  if (!w?.start || !w?.end) return null;
  const first = dayLabel(w.start);
  const endTime = w.end.slice(11, 16);
  const dayStart = (w.day_start || '00:00').slice(0, 5);
  const last = dayLabel(w.end, endTime && endTime < dayStart ? 1 : 0);
  if (!first || !last) return null;
  return first === last ? first : `${first} to ${last}`;
}

/** "07:00" → "7:00am". */
export function clockLabel(hhmm: string | null | undefined): string | null {
  const m = /^(\d{1,2}):(\d{2})/.exec(hhmm || '');
  if (!m) return null;
  const h = +m[1];
  const suffix = h < 12 ? 'am' : 'pm';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${m[2]}${suffix}`;
}
