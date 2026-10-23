/**
 * Pure logic behind the `finance_weekly` display component — the document
 * shape `loadedhub.get_weekly_financials` returns, how it is found in a
 * block's data, the week arithmetic of the picker, and the number formats.
 * Kept out of the component so it is testable without a DOM (the
 * approvalCard.ts precedent).
 */

export interface DayRow {
  date: string;
  day: string;
  sales: number;
  sales_ex_gst: number;
  budget: number | null;
  orders: number;
  discounts: number;
  voids: number;
  rostered_cost: number | null;
  wages: number | null;
  wage_pct: number | null;
  budget_pct: number | null;
}

export interface Figures {
  sales: number;
  gst: number;
  sales_ex_gst: number;
  budget: number;
  budget_pct: number | null;
  vs_budget: number | null;
  orders: number;
  order_value: number;
  average_order: number | null;
  bills: number;
  discounts: number;
  voids: number;
  void_count: number;
  waste: number;
  food_sales: number;
  beverage_sales: number;
  other_sales: number;
  food_share: number | null;
  beverage_share: number | null;
  split_mismatch: number;
  wages: number;
  wage_pct: number | null;
  food_wages: number;
  food_wage_pct: number | null;
  beverage_wages: number;
  beverage_wage_pct: number | null;
  other_wages: number;
  hours: number;
  kitchen_hours: number;
  rostered_cost: number;
  rostered_hours: number;
  actual_cost: number;
  actual_hours: number;
  hours_variance: number;
  cost_variance: number;
  cash_variance: number;
  total_variance: number;
  cashup_buckets: number;
  budget_plan?: Record<string, unknown> | null;
  days?: DayRow[];
}

export interface VenueBlock {
  venue: string;
  venue_id: string | null;
  error?: string;
  week?: Figures;
  mtd?: Figures;
}

export interface WeeklyDoc {
  week?: { week_ending: string; week_start: string; label: string };
  mtd?: { from: string; to: string; label: string };
  venues?: VenueBlock[];
  group?: { week: Figures; mtd: Figures; venues: number };
  warnings?: string[];
  notes?: string[];
  budget_plan_available?: boolean;
  error?: string;
}

export type Money = (v: number | null | undefined) => string;

/** The consolidator's document, wherever the caller put it: the block's
 *  data itself, under `data`, or an error alone. */
export function extractDoc(data: unknown): WeeklyDoc | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (Array.isArray(d.venues) && d.group) return d as WeeklyDoc;
  const inner = d.data;
  if (inner && typeof inner === 'object' && Array.isArray((inner as Record<string, unknown>).venues)) {
    return inner as WeeklyDoc;
  }
  if (typeof d.error === 'string') return { error: d.error };
  return null;
}

function toIso(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

/** Sunday of the last completed Monday–Sunday week, as YYYY-MM-DD. On a
 *  Monday that is yesterday; on a Sunday it is the Sunday before (the
 *  current week is not complete until it ends). */
export function lastCompletedSunday(today = new Date()): string {
  const d = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const back = ((d.getDay() + 6) % 7) + 1; // Mon → 1 … Sun → 7
  d.setDate(d.getDate() - back);
  return toIso(d);
}

/** The Sunday `weeks` weeks on from a week-ending date (negative for back). */
export function shiftWeek(weekEnding: string, weeks: number): string {
  const d = new Date(`${weekEnding}T12:00:00`);
  d.setDate(d.getDate() + 7 * weeks);
  return toIso(d);
}

/** A ratio as "28.9%", or an em dash when there is none. */
export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

/** A count or hours figure with thousands separators. */
export function num(v: number | null | undefined, dp = 0): string {
  if (v === null || v === undefined || !isFinite(v)) return '—';
  return v.toLocaleString('en-NZ', { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

/** Money in the organisation's currency: "$1,234" for NZD and AUD (whole
 *  dollars — the summary reads at that grain), the ISO code in front for a
 *  currency the browser has no narrow symbol for. */
export function makeMoney(currency: string | null): Money {
  const code = currency || 'NZD';
  let fmt: Intl.NumberFormat | null = null;
  try {
    fmt = new Intl.NumberFormat('en-NZ', {
      style: 'currency',
      currency: code,
      currencyDisplay: 'narrowSymbol',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    });
  } catch {
    fmt = null;
  }
  return (v) => {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    if (fmt) return fmt.format(v);
    return `${code} ${v.toLocaleString('en-NZ', { maximumFractionDigits: 0 })}`;
  };
}
