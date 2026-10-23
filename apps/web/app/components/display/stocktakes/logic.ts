/**
 * Pure logic for the Stocktakes page (Procurement → Stocktakes). No React, no
 * fetching — what the page does with the documents the tools return:
 *
 *  - `loadedhub.get_stocktakes` view 'list'      → the counts
 *  - `loadedhub.get_stocktakes` view 'variance'  → one count against the one before
 *  - `loadedhub.get_stock` view 'on_hand'        → value by category at the count
 *  - `loadedhub.get_purchase_cogs`               → purchases against sales
 *
 * Field names here are the tools' real output (checked against live payloads,
 * Oct 2026), not Loaded's raw API.
 */

// ── Documents ──────────────────────────────────────────────────────────────

/** One row of get_stocktakes view 'list'. `completed` is null while pending. */
export interface StocktakeRow {
  id: string;
  title: string | null;
  /** "All Food" (a whole category), an area list ("DAILY KEGS"), or "Ad-hoc count". */
  template: string;
  completed: string | null;
  pending: boolean;
}

export interface StocktakeListDoc {
  view: 'list';
  stocktakes: StocktakeRow[];
  total: number;
  completed_by_template?: Record<string, number>;
}

export interface VarianceRow {
  item: string;
  group: string | null;
  unit: string | null;
  opening: number;
  received: number;
  closing: number;
  expected_use: number;
  actual_use: number;
  variance_qty: number;
  variance_value: number;
}

export interface VarianceDoc {
  view: 'variance';
  template: string | null;
  opening: StocktakeRow;
  closing: StocktakeRow;
  days: number | null;
  totals: {
    sales: number | null;
    sales_ex_tax: number | null;
    opening_value: number;
    purchases: number;
    closing_value: number;
    cost_of_goods: number;
    cogs_pct: number;
    total_variance: number;
    shortfall_value: number;
    surplus_value: number;
  };
  coverage?: {
    items: number;
    counted_both_ends: number;
    only_in_opening: number;
    only_in_closing: number;
    not_pos_linked: number;
  };
  variances: VarianceRow[];
  others?: { count: number; variance_value: number };
  not_pos_linked?: {
    note?: string;
    count: number;
    usage_value: number;
    top: { item: string; group: string | null; unit: string | null; actual_use: number; usage_value: number }[];
  };
  warning?: string;
}

/** A row of get_stock view 'on_hand' for a template. `category` is the stock group. */
export interface OnHandRow {
  item_id?: string;
  name: string;
  category: string | null;
  quantity_on_hand: number;
  counting_unit?: string | null;
  value_on_hand: number;
}

export interface OnHandDoc {
  template: string;
  as_of: string;
  lines: number;
  total_value_on_hand: number;
  rows: OnHandRow[];
  others?: { count: number; value_on_hand: number };
}

export interface PurchaseCogsRow {
  bucket: string;
  bucket_start: string;
  purchases: number;
  food: number;
  beverage: number;
  other: number;
  freight: number;
  sales: number;
  food_sales: number;
  beverage_sales: number;
  cogs_pct: number | null;
  food_cogs_pct: number | null;
  beverage_cogs_pct: number | null;
}

export interface PurchaseCogsDoc {
  window?: { label?: string; description?: string; start?: string; end?: string; timezone?: string };
  interval: 'month' | 'week';
  rows: PurchaseCogsRow[];
  summary: {
    purchases: number;
    food: number;
    beverage: number;
    other: number;
    freight: number;
    sales: number;
    food_sales: number;
    beverage_sales: number;
    cogs_pct: number | null;
    food_cogs_pct: number | null;
    beverage_cogs_pct: number | null;
    uncategorised_sales_pct?: number | null;
    previous?: { purchases: number; sales: number; cogs_pct: number | null; food_cogs_pct?: number | null; beverage_cogs_pct?: number | null };
    cogs_pct_change?: number | null;
  };
  warnings?: string[];
}

// ── Kinds of count ─────────────────────────────────────────────────────────

export type CountKind = 'full' | 'area' | 'adhoc';

/**
 * A whole-category count is Loaded's "All <category>" template (the tool names
 * the stock super-groups that way: All Food, All Beverage, All Other Stock).
 * Ad-hoc counts carry the tool's own "Ad-hoc count". Anything else is a list
 * a venue made for one area or a daily check ("DAILY KEGS", "Back Bar NEW").
 */
export function countKind(template: string | null | undefined): CountKind {
  const t = (template || '').trim();
  if (!t || /^ad-hoc count$/i.test(t)) return 'adhoc';
  if (/^all\s+\S/i.test(t)) return 'full';
  return 'area';
}

/** "All Food" → "Food". Null for area lists and ad-hoc counts. */
export function categoryOf(template: string | null | undefined): string | null {
  if (countKind(template) !== 'full') return null;
  return (template as string).trim().replace(/^all\s+/i, '');
}

/** What was counted, in words: "Food — full count", "DAILY KEGS", "Ad-hoc: Kegs". */
export function countLabel(row: Pick<StocktakeRow, 'template' | 'title'>): string {
  const kind = countKind(row.template);
  if (kind === 'full') return `${categoryOf(row.template)} — full count`;
  if (kind === 'area') return row.template;
  // Ad-hoc titles are "<date> - <what>"; "<date> - Ad-hoc count" says nothing more.
  const title = (row.title || '').trim();
  const what = title.includes(' - ') ? title.split(' - ').slice(1).join(' - ').trim() : '';
  return what && !/^ad-hoc count$/i.test(what) ? `Ad-hoc: ${what}` : 'Ad-hoc count';
}

/** Whether a count can be opened: a completed full or area count. Ad-hoc
 *  counts cover different items each time and the tool never pairs them. */
export function canOpen(row: StocktakeRow): boolean {
  return !row.pending && !!row.completed && countKind(row.template) !== 'adhoc';
}

function ts(iso: string | null | undefined): number {
  if (!iso) return NaN;
  return Date.parse(iso);
}

/** Completed counts newest first, then the unfinished ones (no date to sort by). */
export function sortRows(rows: StocktakeRow[]): StocktakeRow[] {
  const done = rows.filter((r) => !r.pending && r.completed);
  const open = rows.filter((r) => r.pending || !r.completed);
  done.sort((a, b) => ts(b.completed) - ts(a.completed));
  return [...done, ...open];
}

/**
 * The count a variance compares this one with: the latest completed count of
 * the SAME template before it. The tool refuses a mixed pair anyway; picking
 * the pair here lets the page open any count, not just the latest.
 */
export function previousCount(rows: StocktakeRow[], row: StocktakeRow): StocktakeRow | null {
  const at = ts(row.completed);
  if (Number.isNaN(at) || countKind(row.template) === 'adhoc') return null;
  let best: StocktakeRow | null = null;
  for (const r of rows) {
    if (r.id === row.id || r.pending || r.template !== row.template) continue;
    const t = ts(r.completed);
    if (Number.isNaN(t) || t >= at) continue;
    if (!best || t > ts(best.completed)) best = r;
  }
  return best;
}

// ── Filters ────────────────────────────────────────────────────────────────

export type ListFilter = 'full' | 'area' | 'adhoc' | 'completed' | 'pending';

export const LIST_FILTERS: { value: ListFilter; label: string }[] = [
  { value: 'full', label: 'Full counts' },
  { value: 'area', label: 'Area counts' },
  { value: 'adhoc', label: 'Ad-hoc counts' },
  { value: 'completed', label: 'All completed counts' },
  { value: 'pending', label: 'Not finished' },
];

export function filterRows(rows: StocktakeRow[], filter: ListFilter): StocktakeRow[] {
  return rows.filter((r) => {
    const done = !r.pending && !!r.completed;
    if (filter === 'pending') return !done;
    if (!done) return false;
    if (filter === 'completed') return true;
    return countKind(r.template) === filter;
  });
}

/** Full counts when the venue does them — they carry the value and the cost
 *  of goods; otherwise everything completed; otherwise the unfinished ones. */
export function defaultFilter(rows: StocktakeRow[]): ListFilter {
  if (filterRows(rows, 'full').length) return 'full';
  if (filterRows(rows, 'completed').length) return 'completed';
  if (filterRows(rows, 'pending').length) return 'pending';
  return 'full';
}

/** Whole-category templates with at least two completed counts — the ones a
 *  stock-based cost of goods can be worked out for. Newest-counted first. */
export function stocktakedCategories(rows: StocktakeRow[]): { template: string; category: string; counts: number; latest: string | null }[] {
  const by = new Map<string, { template: string; category: string; counts: number; latest: string | null }>();
  for (const r of rows) {
    if (r.pending || !r.completed || countKind(r.template) !== 'full') continue;
    const cur = by.get(r.template) ?? { template: r.template, category: categoryOf(r.template) as string, counts: 0, latest: null };
    cur.counts += 1;
    if (!cur.latest || ts(r.completed) > ts(cur.latest)) cur.latest = r.completed;
    by.set(r.template, cur);
  }
  return [...by.values()].filter((c) => c.counts >= 2).sort((a, b) => ts(b.latest) - ts(a.latest));
}

// ── One count ──────────────────────────────────────────────────────────────

/** What should have been on hand at the closing count: opening + received − expected use. */
export function expectedOnHand(v: Pick<VarianceRow, 'opening' | 'received' | 'expected_use'>): number {
  return v.opening + v.received - v.expected_use;
}

export interface CategoryValue { category: string; value: number; share: number; items: number }

/**
 * Value by stock category (Loaded's stock group: Dry Goods, Meats, …) from the
 * on-hand rows. Rows rolled into "(others)" by the tool have no category, so
 * the page asks for every row (top 1000) and they never arise in practice;
 * if they do they are kept as "Other items" so the total still adds up.
 */
export function valueByCategory(doc: Pick<OnHandDoc, 'rows' | 'others'>): CategoryValue[] {
  const by = new Map<string, { value: number; items: number }>();
  for (const r of doc.rows || []) {
    const v = Number(r.value_on_hand) || 0;
    if (!v) continue;
    const key = (r.category || '').trim() || 'No category';
    const cur = by.get(key) ?? { value: 0, items: 0 };
    cur.value += v;
    cur.items += 1;
    by.set(key, cur);
  }
  if (doc.others && doc.others.value_on_hand) {
    by.set('Other items', { value: doc.others.value_on_hand, items: doc.others.count });
  }
  const total = [...by.values()].reduce((s, c) => s + c.value, 0);
  return [...by.entries()]
    .map(([category, c]) => ({ category, value: c.value, items: c.items, share: total ? c.value / total : 0 }))
    .sort((a, b) => b.value - a.value);
}

/**
 * Does the on-hand snapshot at the count match the count's own value? A count
 * finished soon after it was taken matches to the cent (Dunedin Social Club,
 * 17 Aug 2026: $2,843.83 both ways). One finished long after it was taken may
 * not — the snapshot can then include the trading in between — so the page
 * checks, and says so instead of passing the snapshot off as the count.
 */
export function snapshotMatches(snapshotTotal: number, countValue: number): boolean {
  const diff = Math.abs(snapshotTotal - countValue);
  return diff <= Math.max(1, Math.abs(countValue) * 0.005);
}

/** Cost of goods as a share of sales ex GST. Loaded's own percentage is
 *  against sales INCLUDING GST; purchases and stock are ex GST, so this page
 *  uses ex GST on both sides (the same basis as get_purchase_cogs). */
export function cogsShareExGst(costOfGoods: number, salesExTax: number | null | undefined): number | null {
  if (!salesExTax) return null;
  return costOfGoods / salesExTax;
}

// ── Purchases-based cost of goods ──────────────────────────────────────────

/** Buckets that bought stock but have no sales — their purchases inflate the
 *  period's percentage (a POS history gap, or a venue not trading). */
export function bucketsWithoutSales(rows: PurchaseCogsRow[]): string[] {
  return rows.filter((r) => (r.purchases || 0) > 0 && !(r.sales || 0)).map((r) => r.bucket);
}

/** Interval for a period: weekly buckets for a week, monthly otherwise. */
export function intervalFor(period: string): 'week' | 'month' {
  return /week/i.test(period) ? 'week' : 'month';
}

export const PERIODS: { value: string; label: string }[] = [
  { value: 'last week', label: 'Last week' },
  { value: 'this month', label: 'This month' },
  { value: 'last month', label: 'Last month' },
  { value: 'last 3 months', label: 'Last 3 months' },
];

// ── Errors ─────────────────────────────────────────────────────────────────

/** The list comes through Loaded Legacy (Loaded's older login); the tool says
 *  "… isn't a venue you can use loaded_legacy at … or connect it first". */
export function isLegacyMissing(error: string | null | undefined): boolean {
  return !!error && /loaded_legacy|loaded legacy/i.test(error) && /connect|isn't a venue|not connected/i.test(error);
}

export function isHubMissing(error: string | null | undefined): boolean {
  return !!error && !isLegacyMissing(error) && /No credentials|not connected|needs_reconnect|reconnect/i.test(error);
}

// ── Formatting ─────────────────────────────────────────────────────────────

/** 0.2559 → "25.6%". */
export function formatPct(fraction: number | null | undefined, dp = 1): string {
  if (fraction === null || fraction === undefined || !isFinite(fraction)) return '—';
  return `${(fraction * 100).toFixed(dp)}%`;
}

/** A change in percentage points, signed: 0.0674 → "+6.7 points". */
export function formatPoints(fraction: number | null | undefined): string | null {
  if (fraction === null || fraction === undefined || !isFinite(fraction)) return null;
  const p = Math.round(fraction * 1000) / 10;
  if (p === 0) return 'no change';
  return `${p > 0 ? '+' : '−'}${Math.abs(p).toFixed(1)} points`;
}

/** A day in the venue's own timezone: "17 Aug 2026". Date-only strings stay that date. */
export function formatDay(iso: string | null | undefined, timeZone?: string | null, withYear = true): string {
  if (!iso) return '—';
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(iso);
  const d = new Date(dateOnly ? `${iso}T12:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  const opts: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'short', ...(withYear ? { year: 'numeric' } : {}) };
  if (dateOnly) opts.timeZone = 'UTC';
  else if (timeZone) opts.timeZone = timeZone;
  try {
    return d.toLocaleDateString('en-NZ', opts);
  } catch {
    // An unknown zone name: fall back to the browser's own.
    delete opts.timeZone;
    return d.toLocaleDateString('en-NZ', opts);
  }
}

/** A quantity as Loaded reports it: up to 2 decimals, no trailing zeros. */
export function formatQty(n: number | null | undefined): string {
  if (n === null || n === undefined || !isFinite(n)) return '—';
  return n.toLocaleString('en-NZ', { maximumFractionDigits: Math.abs(n) >= 100 ? 0 : 2 });
}

/** A week bucket's label from its start date ("Week of 12 Oct"); months keep theirs. */
export function bucketLabel(row: Pick<PurchaseCogsRow, 'bucket' | 'bucket_start'>, interval: 'week' | 'month'): string {
  if (interval === 'week' && row.bucket_start) return `Week of ${formatDay(row.bucket_start, null, false)}`;
  return row.bucket;
}
