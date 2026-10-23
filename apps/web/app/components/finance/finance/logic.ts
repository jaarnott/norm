/**
 * Norm Finance — the pure logic behind the four pages (Weekly Summary,
 * Budgets, Budget vs Loaded, Forecasting), kept out of the components so it
 * is tested without a DOM.
 *
 * Money is integer cents, as the API sends it; the Weekly Summary's figures
 * are the one place dollars appear (they are Loaded's own numbers, passed
 * through). Dates are plain "YYYY-MM-DD" trading dates and never go through
 * `new Date()` in the browser's zone — the arithmetic is on the calendar
 * date alone (Date.UTC), as Functions' dates.ts does.
 */

import { toCsv, type CsvValue } from '../shared/csv';
import type { BudgetPlanPeriod, Figures, HolidaySync, ImportLayout, VenueSummary } from './types';

// ---------------------------------------------------------------------------
// Calendar
// ---------------------------------------------------------------------------

export interface YearMonth {
  year: number;
  month: number;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** "Oct 2026". */
export function monthLabel(ym: YearMonth): string {
  return `${MONTHS[ym.month - 1]} ${ym.year}`;
}

export function shortMonth(month: number): string {
  return MONTHS[month - 1] ?? String(month);
}

/** Days in a calendar month. */
export function daysInMonth(ym: YearMonth): number {
  return new Date(Date.UTC(ym.year, ym.month, 0)).getUTCDate();
}

export function addMonths(ym: YearMonth, n: number): YearMonth {
  const index = ym.year * 12 + (ym.month - 1) + n;
  return { year: Math.floor(index / 12), month: (index % 12) + 1 };
}

export function sameMonth(a: YearMonth, b: YearMonth): boolean {
  return a.year === b.year && a.month === b.month;
}

/** The year and month of a plain date. */
export function yearMonthOf(iso: string): YearMonth {
  const [y, m] = iso.slice(0, 10).split('-').map(Number);
  return { year: y, month: m };
}

/** "2026-10-30" + 3 → "2026-11-02" (calendar days; no zone involved). */
export function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

/** ISO weekday of a plain date: 1 = Monday … 7 = Sunday. */
export function isoWeekday(iso: string): number {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  const js = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0 = Sunday
  return js === 0 ? 7 : js;
}

/** "Mon" for an ISO weekday. */
export function weekdayShort(weekday: number): string {
  return WEEKDAYS[weekday - 1] ?? '';
}

/** "Mon 12" — the day grid's first column. */
export function dayCell(iso: string, weekday?: number): string {
  return `${weekdayShort(weekday ?? isoWeekday(iso))} ${Number(iso.slice(8, 10))}`;
}

/** "Mon 12 Oct 2026" (no zone shift). */
export function longDate(iso: string): string {
  const ym = yearMonthOf(iso);
  return `${weekdayShort(isoWeekday(iso))} ${Number(iso.slice(8, 10))} ${shortMonth(ym.month)} ${ym.year}`;
}

/** The toast after "Sync from Loaded": what changed, and how far Loaded's public holiday dates run. */
export function holidaySyncText(r: Pick<HolidaySync, 'inserted' | 'updated' | 'removed' | 'dates_until'>): string {
  const parts = [r.inserted && `${r.inserted} added`, r.updated && `${r.updated} updated`, r.removed && `${r.removed} removed`].filter(Boolean);
  const until = r.dates_until ? `Loaded holds public holidays up to ${longDate(r.dates_until)}.` : 'Loaded holds no public holidays for this venue.';
  return `Public holidays from Loaded: ${parts.length ? parts.join(', ') : 'nothing new'}. ${until}`;
}

/** "12 – 18 Oct 2026" / "28 Sep – 4 Oct 2026". */
export function rangeLabel(from: string, to: string): string {
  const a = yearMonthOf(from);
  const b = yearMonthOf(to);
  const dayA = Number(from.slice(8, 10));
  const dayB = Number(to.slice(8, 10));
  if (sameMonth(a, b)) return `${dayA} – ${dayB} ${shortMonth(a.month)} ${a.year}`;
  if (a.year === b.year) return `${dayA} ${shortMonth(a.month)} – ${dayB} ${shortMonth(b.month)} ${a.year}`;
  return `${dayA} ${shortMonth(a.month)} ${a.year} – ${dayB} ${shortMonth(b.month)} ${b.year}`;
}

/** The Monday of the week a date is in. */
export function mondayOf(iso: string): string {
  return addDays(iso, 1 - isoWeekday(iso));
}

// ---------------------------------------------------------------------------
// Fiscal years — named by the calendar year they END in (Orbit's rule, made
// general): with an April start, Apr 2026 – Mar 2027 is FY2027; with a July
// start (the AU organisation), Jul 2026 – Jun 2027 is FY2027; a January
// start is the calendar year.
// ---------------------------------------------------------------------------

export function fiscalYearOf(iso: string, startMonth: number): number {
  const { year, month } = yearMonthOf(iso);
  if (startMonth <= 1) return year;
  return month >= startMonth ? year + 1 : year;
}

/** The twelve months of a fiscal year, first to last. */
export function fiscalYearMonths(fy: number, startMonth: number): YearMonth[] {
  const first: YearMonth = startMonth <= 1 ? { year: fy, month: 1 } : { year: fy - 1, month: startMonth };
  return Array.from({ length: 12 }, (_, i) => addMonths(first, i));
}

/** "FY2027 · Apr 2026 – Mar 2027" (just "2026" for a calendar year). */
export function fiscalYearLabel(fy: number, startMonth: number): string {
  if (startMonth <= 1) return String(fy);
  const months = fiscalYearMonths(fy, startMonth);
  return `FY${fy} · ${monthLabel(months[0])} – ${monthLabel(months[11])}`;
}

/** The first and last date of a fiscal year. */
export function fiscalYearRange(fy: number, startMonth: number): { from: string; to: string } {
  const months = fiscalYearMonths(fy, startMonth);
  const last = months[11];
  const pad = (n: number) => String(n).padStart(2, '0');
  return {
    from: `${months[0].year}-${pad(months[0].month)}-01`,
    to: `${last.year}-${pad(last.month)}-${pad(daysInMonth(last))}`,
  };
}

/** The month to open a grid on: today's month when it is inside the fiscal
 *  year, else the year's first month. */
export function defaultMonth(fy: number, startMonth: number, today: string): YearMonth {
  const months = fiscalYearMonths(fy, startMonth);
  const now = yearMonthOf(today);
  return months.find((m) => sameMonth(m, now)) ?? months[0];
}

// ---------------------------------------------------------------------------
// Grid maths
// ---------------------------------------------------------------------------

/** Σ of a nullable cents column — null when no row had a value, so a column
 *  nobody has filled reads "—" rather than "$0". */
export function sumCents<T>(rows: T[], pick: (row: T) => number | null | undefined): number | null {
  let total = 0;
  let any = false;
  for (const row of rows) {
    const v = pick(row);
    if (v === null || v === undefined) continue;
    total += v;
    any = true;
  }
  return any ? total : null;
}

export interface WeekGroup<T> {
  /** The Monday of the week (may fall in the previous month). */
  week_start: string;
  rows: T[];
}

/** Rows grouped into Monday–Sunday weeks, in date order, for subtotals. */
export function groupIntoWeeks<T extends { date: string }>(rows: T[]): WeekGroup<T>[] {
  const groups: WeekGroup<T>[] = [];
  let current: WeekGroup<T> | null = null;
  for (const row of [...rows].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))) {
    const monday = mondayOf(row.date);
    if (!current || current.week_start !== monday) {
      current = { week_start: monday, rows: [] };
      groups.push(current);
    }
    current.rows.push(row);
  }
  return groups;
}

/** Which side of the line a variance falls — Orbit's convention kept:
 *  Loaded above the locked budget is "above" (green), below is "below"
 *  (red), zero or unknown is "level" (plain). Used for Δ cells and bands. */
export type Band = 'above' | 'below' | 'level';

export function band(variance: number | null | undefined): Band {
  if (variance === null || variance === undefined || variance === 0) return 'level';
  return variance > 0 ? 'above' : 'below';
}

/** The CSS colour for a band: status tokens, because above/below budget is a
 *  state, not a series. Text beside the sign carries the meaning too. */
export function bandColor(b: Band): string | undefined {
  if (b === 'above') return 'var(--ok)';
  if (b === 'below') return 'var(--error)';
  return undefined;
}

/** A variance as a share of the base: +6.2% / −3.0% / null when no base. */
export function variancePct(variance: number | null | undefined, base: number | null | undefined): number | null {
  if (variance === null || variance === undefined || !base) return null;
  return variance / base;
}

// ---------------------------------------------------------------------------
// Number formats — money and CSV come from components/finance/shared
// (F8's money.ts / csv.ts); only what the grids add is here.
// ---------------------------------------------------------------------------

/** A ratio as "28.9%", signed ("+6.2%" / "−3.0%") when asked; "—" for none. */
export function pct(v: number | null | undefined, dp = 1, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  const text = `${(Math.abs(v) * 100).toFixed(dp)}%`;
  if (v < 0) return `−${text}`;
  return signed && v > 0 ? `+${text}` : text;
}

/** Typed dollars → cents for a grid cell: "1,978" / "$1,978.50" / "(200)" →
 *  197800 / 197850 / −20000 (parentheses negate, as Orbit's paste did);
 *  null for blank or nonsense. */
export function parseAmount(input: string): number | null {
  const raw = input.trim();
  if (!raw) return null;
  const negative = /^\(.*\)$/.test(raw) || raw.startsWith('-') || raw.startsWith('−');
  const cleaned = raw.replace(/[^0-9.]/g, '');
  if (!cleaned) return null;
  const n = Number(cleaned);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 100) * (negative ? -1 : 1);
}

/** Cents as the text a budget input shows: whole dollars when there are no
 *  cents (Loaded keeps budgets to the dollar), else two places. */
export function centsInput(v: number | null | undefined): string {
  if (v === null || v === undefined) return '';
  return v % 100 === 0 ? String(v / 100) : (v / 100).toFixed(2);
}

/** A typed percentage → a ratio: "29" / "29%" / "0.29" → 0.29. Values above
 *  1 are read as percentages, as Orbit's grid did. */
export function parseRatio(input: string): number | null {
  const cleaned = input.replace(/[^0-9.\-]/g, '');
  if (!cleaned) return null;
  const n = Number(cleaned);
  if (!Number.isFinite(n)) return null;
  return input.includes('%') || Math.abs(n) > 1 ? n / 100 : n;
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

/** Which parser a pasted budget wants. Orbit's FY27 spreadsheet tab is 12
 *  month blocks across, 5 columns each (~60 cells a row); a generic import
 *  is `date, amount[, note]` — three or so. The person can still switch. */
export function detectImportLayout(text: string): ImportLayout {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length === 0) return 'generic';
  const widths = lines.slice(0, 40).map((l) => l.split(/\t|,/).length);
  const wide = widths.filter((w) => w >= 30).length;
  return wide >= Math.min(3, lines.length) ? 'orbit' : 'generic';
}

// ---------------------------------------------------------------------------
// The Weekly Summary's budget P&L (04 §7, D33): the locked budget and the
// monthly lines beside the week's actuals, for the week and month to date.
// Dollars, as the summary figures are.
// ---------------------------------------------------------------------------

export interface PnlRow {
  label: string;
  budget: number | null;
  actual: number | null;
  /** 'money' | 'pct' */
  kind: 'money' | 'pct';
  emphasis?: 'bold' | 'subtotal' | 'italic';
  /** Why the actual is blank, when it is. */
  note?: string;
}

function nz(v: number | null | undefined): number | null {
  return v === null || v === undefined || !Number.isFinite(v) ? null : v;
}

function minus(...parts: (number | null)[]): number | null {
  if (parts.some((p) => p === null)) return null;
  const [first, ...rest] = parts as number[];
  return rest.reduce((acc, p) => acc - p, first);
}

/** The P&L rows for one period. `plan` is `budget_plan.week` / `.mtd`;
 *  `xero` are the rows `xero.get_profit_and_loss` contributed when a venue
 *  is connected (label → dollars), which the API sends only then. */
export function budgetPnl(f: Figures | null, plan: BudgetPlanPeriod | null, xero?: Record<string, number> | null): PnlRow[] {
  const lines = plan?.lines ?? {};
  const adj = plan?.adjustments ?? {};
  const bSalesIncl = nz(plan?.budget);
  const bSalesEx = nz(plan?.budget_ex_gst);
  const bWages = nz(lines.wages);
  const bLeave = nz(lines.leave);
  const bCogs = nz(lines.cogs);
  const bOpex = nz(lines.opex);
  const bDep = nz(lines.depreciation);
  const aSalesIncl = nz(f?.sales);
  const aSalesEx = nz(f?.sales_ex_gst);
  const aWages = f ? nz(f.wages) : null;
  const aWagesAdj = aWages === null ? null : aWages + (nz(adj.wages) ?? 0);
  const aLeave = nz(adj.leave);
  const aCogs = nz(xero?.cost_of_goods ?? null);
  const aOpex = nz(xero?.operating_expenses ?? null);
  const aDep = nz(xero?.depreciation ?? null);
  const bGp = minus(bSalesEx, bWages, bLeave, bCogs);
  const aGp = minus(aSalesEx, aWagesAdj, aLeave ?? 0, aCogs);
  const bOp = minus(bGp, bOpex);
  const aOp = minus(aGp, aOpex);
  const bNet = minus(bOp, bDep);
  const aNet = minus(aOp, aDep);
  const share = (num: number | null, den: number | null) => (num === null || !den ? null : num / den);
  const rows: PnlRow[] = [
    { label: 'Sales (incl GST)', budget: bSalesIncl, actual: aSalesIncl, kind: 'money' },
    { label: 'Sales (ex GST)', budget: bSalesEx, actual: aSalesEx, kind: 'money' },
    { label: 'Wages', budget: bWages, actual: aWagesAdj, kind: 'money', emphasis: 'bold', note: nz(adj.wages) ? `includes ${adj.wages! >= 0 ? '+' : ''}${adj.wages} of adjustments` : undefined },
    { label: 'as % of sales', budget: nz(lines.wage_pct) ?? share(bWages, bSalesEx), actual: share(aWagesAdj, aSalesEx), kind: 'pct', emphasis: 'italic' },
    { label: 'Leave and entitlements', budget: bLeave, actual: aLeave, kind: 'money', note: aLeave === null ? 'from the payroll reconciliation' : 'leave adjustments this week' },
    { label: 'Cost of goods', budget: bCogs, actual: aCogs, kind: 'money', note: aCogs === null ? 'from Xero where connected' : undefined },
    { label: 'as % of sales', budget: nz(lines.cogs_pct) ?? share(bCogs, bSalesEx), actual: share(aCogs, aSalesEx), kind: 'pct', emphasis: 'italic' },
    { label: 'Gross profit (before rebates)', budget: bGp, actual: aGp, kind: 'money', emphasis: 'bold' },
    { label: 'as % of sales', budget: share(bGp, bSalesEx), actual: share(aGp, aSalesEx), kind: 'pct', emphasis: 'italic' },
    { label: 'Operating expenses', budget: bOpex, actual: aOpex, kind: 'money', note: aOpex === null ? 'from Xero where connected' : undefined },
    { label: 'Operating profit', budget: bOp, actual: aOp, kind: 'money', emphasis: 'subtotal' },
    { label: 'Depreciation', budget: bDep, actual: aDep, kind: 'money' },
    { label: 'Net profit', budget: bNet, actual: aNet, kind: 'money', emphasis: 'subtotal' },
  ];
  if (nz(lines.rebates_pct) !== null) {
    rows.splice(7, 0, { label: 'Rebates (% of beverage purchases)', budget: lines.rebates_pct!, actual: null, kind: 'pct', emphasis: 'italic' });
  }
  // Any other Xero line the API sent (intercompany fees, finance costs, tax —
  // not budgeted, D33) sits under the net, actual only.
  for (const [key, value] of Object.entries(xero ?? {})) {
    if (['cost_of_goods', 'operating_expenses', 'depreciation'].includes(key)) continue;
    const label = key.replace(/_/g, ' ');
    rows.push({ label: label[0].toUpperCase() + label.slice(1), budget: null, actual: nz(value), kind: 'money' });
  }
  return rows;
}

// ---------------------------------------------------------------------------
// CSV (the shared `toCsv` quotes and defuses the cells)
// ---------------------------------------------------------------------------

/** The Weekly Summary grid as CSV: one row per figure, a column pair (week,
 *  month to date) per venue and for the group, then the budget P&L rows. */
export function summaryCsv(venues: VenueSummary[], group: { week: Figures; mtd: Figures } | null, weekLabel: string, mtdLabel: string): string {
  const blocks: { name: string; week: Figures; mtd: Figures; plan: { week?: BudgetPlanPeriod | null; mtd?: BudgetPlanPeriod | null } | null }[] = [];
  for (const v of venues) {
    if (!v.week || !v.mtd) continue;
    blocks.push({ name: v.venue, week: v.week, mtd: v.mtd, plan: (v.week.budget_plan as { week?: BudgetPlanPeriod | null; mtd?: BudgetPlanPeriod | null } | null) ?? null });
  }
  if (group) blocks.push({ name: 'Group', week: group.week, mtd: group.mtd, plan: null });
  const header: CsvValue[] = ['Line'];
  for (const b of blocks) header.push(`${b.name} · ${weekLabel}`, `${b.name} · ${mtdLabel}`);
  const figureKeys: [keyof Figures, string][] = [
    ['sales', 'Sales (incl GST)'], ['sales_ex_gst', 'Sales (ex GST)'], ['budget', 'Loaded budget'], ['vs_budget', 'vs budget'],
    ['orders', 'Orders'], ['average_order', 'Average order'], ['discounts', 'Discounts'], ['voids', 'Voids'],
    ['food_sales', 'Food sales'], ['beverage_sales', 'Beverage sales'], ['other_sales', 'Other sales'],
    ['wages', 'Wages'], ['wage_pct', 'Wage %'], ['food_wages', 'Food wages'], ['beverage_wages', 'Beverage wages'],
    ['hours', 'Hours'], ['kitchen_hours', 'Kitchen hours'], ['rostered_cost', 'Rostered cost'], ['actual_cost', 'Actual cost'],
    ['cost_variance', 'Cost variance'], ['hours_variance', 'Hours variance'], ['cash_variance', 'Cash variance'], ['total_variance', 'Total variance'],
  ];
  const rows: CsvValue[][] = [];
  const cell = (v: unknown): CsvValue => (typeof v === 'number' || typeof v === 'string' ? v : null);
  for (const [key, label] of figureKeys) {
    const row: CsvValue[] = [label];
    for (const b of blocks) row.push(cell(b.week[key]), cell(b.mtd[key]));
    rows.push(row);
  }
  const pnlLabels = budgetPnl(null, null).map((r) => r.label);
  for (const label of pnlLabels) {
    const row: CsvValue[] = [`Budget: ${label}`];
    const actualRow: CsvValue[] = [`Actual: ${label}`];
    for (const b of blocks) {
      const w = budgetPnl(b.week, b.plan?.week ?? null).find((r) => r.label === label);
      const m = budgetPnl(b.mtd, b.plan?.mtd ?? null).find((r) => r.label === label);
      row.push(w?.budget ?? null, m?.budget ?? null);
      actualRow.push(w?.actual ?? null, m?.actual ?? null);
    }
    rows.push(row, actualRow);
  }
  return toCsv(header, rows);
}

// ---------------------------------------------------------------------------
// The outcome of a Loaded push, in plain words
// ---------------------------------------------------------------------------

export interface PushData {
  updated?: number;
  created?: number;
  unchanged?: number;
  skipped_zero?: string[];
  failed?: string[];
  ledger?: string;
  note?: string;
}

/** "6 updated, 1 created, 2 at $0 skipped." — what the toast says. */
export function pushOutcome(data: PushData | null | undefined): string {
  if (!data) return 'Done.';
  const parts: string[] = [];
  if (data.updated) parts.push(`${data.updated} updated`);
  if (data.created) parts.push(`${data.created} created`);
  if (data.unchanged) parts.push(`${data.unchanged} unchanged`);
  if (data.skipped_zero?.length) parts.push(`${data.skipped_zero.length} at $0 skipped`);
  if (data.failed?.length) parts.push(`${data.failed.length} failed`);
  return parts.length ? `${parts.join(', ')}.` : data.note || 'Nothing to change.';
}
