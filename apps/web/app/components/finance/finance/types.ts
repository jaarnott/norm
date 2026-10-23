/**
 * Norm Finance — the shapes `/api/finance/*` returns (F6's `finance-api.md`)
 * and the Weekly Summary figures F2's `get_weekly_financials` produces.
 * Money is integer cents wherever the name says `_cents`; the summary's
 * figures are dollars (Loaded's own numbers, passed through).
 */

import type { Figures as WeeklyFigures } from '../../display/financeWeekly';

export type Figures = WeeklyFigures;

export type FinanceMode = 'off' | 'shadow' | 'live';

export interface FinVenue {
  id: string;
  name: string;
  is_trading: boolean;
  timezone: string | null;
  day_start: string | null;
  holiday_region: string | null;
  closed_weekdays: number[];
  level_correction_on: boolean;
  /** Four weeks of nightly scores exist — "Set Loaded budget" may show. */
  forecast_push_ready: boolean;
  weeks_of_scores: number;
}

export interface FinMeta {
  organization_id: string;
  finance_mode: FinanceMode;
  fiscal_year_start_month: number;
  currency: string;
  gst_rate: number;
  scopes: string[];
  can_see_group: boolean;
  venues: FinVenue[];
  fiscal_years: number[];
  today: string;
}

// --- Weekly Summary ---------------------------------------------------------

export interface BudgetPlanPeriod {
  start: string;
  end: string;
  days_with_budget: number;
  budget: number;
  budget_cents: number;
  budget_ex_gst: number;
  lines: {
    wages?: number | null;
    wage_pct?: number | null;
    cogs_pct?: number | null;
    cogs?: number | null;
    leave?: number | null;
    opex?: number | null;
    depreciation?: number | null;
    rebates_pct?: number | null;
  };
  adjustments: { wages?: number | null; leave?: number | null };
}

export interface BudgetPlan {
  venue_id: string;
  fiscal_year: number;
  week_ending: string;
  version: { id: string; label: string | null; locked_at: string | null } | null;
  week: BudgetPlanPeriod | null;
  mtd: BudgetPlanPeriod | null;
  notes?: string[];
}

export interface VenueSummary {
  venue: string;
  venue_id: string | null;
  error?: string;
  week?: Figures;
  mtd?: Figures;
  warnings?: string[];
  /** Rows from `xero.get_profit_and_loss`, only when the venue is connected. */
  xero?: { week?: Record<string, number>; mtd?: Record<string, number> } | null;
}

export interface SummaryDoc {
  week_ending: string;
  week?: { week_ending: string; week_start?: string; label: string };
  mtd?: { label: string; from?: string; to?: string };
  venues: VenueSummary[];
  group: { week: Figures; mtd: Figures; venues?: number } | null;
  warnings?: string[];
  notes?: string[];
}

// --- Budgets ----------------------------------------------------------------

export type BudgetSource = 'import' | 'manual' | 'forecast';

export interface BudgetVersion {
  id: string;
  fiscal_year: number;
  label: string | null;
  source: BudgetSource;
  locked_at: string | null;
  locked_by: string | null;
  created_by: string | null;
  created_at: string;
  notes: string | null;
  days: number;
  total_cents: number;
}

export interface VersionsDoc {
  venue_id: string;
  fiscal_year: number;
  current_version_id: string | null;
  versions: BudgetVersion[];
}

export interface BudgetDay {
  date: string;
  weekday: number;
  amount_cents: number | null;
  note: string | null;
  loaded_cents: number | null;
  actual_cents: number | null;
  holiday: string | null;
}

export interface DaysDoc {
  venue_id: string;
  year: number;
  month: number;
  version_id: string | null;
  locked: boolean;
  fiscal_year: number;
  days: BudgetDay[];
  totals: { amount_cents: number | null; loaded_cents: number | null; actual_cents: number | null };
  warnings: string[];
}

export type LineItem = 'wage_pct' | 'wage_cents' | 'cogs_pct' | 'food_cogs_pct' | 'bev_cogs_pct' | 'leave_cents' | 'opex_cents' | 'depreciation_cents' | 'rebates_pct';

export interface LineValue {
  value_cents?: number | null;
  value_ratio?: number | null;
  version?: number;
}

export interface LinesDoc {
  venue_id: string;
  fiscal_year: number;
  months: Record<string, Partial<Record<LineItem, LineValue>>>;
}

export type ImportLayout = 'generic' | 'orbit';

export interface ImportPreview {
  days: number;
  total_cents: number;
  first: string | null;
  last: string | null;
  notes: number;
  fiscal_year: number | null;
  outside_fiscal_year: string[];
  duplicates: string[];
  zero_days: string[];
  months: { year: number; month: number; days: number; total_cents: number }[];
  problems: string[];
  rows: { date: string; amount_cents: number; note: string | null }[];
}

export interface SyncMonthDoc {
  tool: string;
  venue_id: string;
  params: { venue: string; venue_id: string; days: { date: string; amount: number }[]; allow_zero: boolean };
  zero_days: string[];
  days_without_budget: string[];
}

// --- Budget vs Loaded -------------------------------------------------------

export interface VsLoadedMonth {
  year: number;
  month: number;
  locked_cents: number | null;
  loaded_cents: number | null;
  actual_cents: number | null;
  variance_cents: number | null;
  variance_pct: number | null;
}

export interface VsLoadedYearDoc {
  venue_id: string;
  fiscal_year: number;
  months: VsLoadedMonth[];
  totals: { locked_cents: number | null; loaded_cents: number | null; actual_cents: number | null; variance_cents: number | null; variance_pct: number | null };
  warnings?: string[];
}

export interface VsLoadedDay {
  date: string;
  weekday?: number;
  locked_cents: number | null;
  loaded_cents: number | null;
  actual_cents: number | null;
  variance_cents?: number | null;
  variance_pct?: number | null;
  note?: string | null;
  holiday?: string | null;
}

export interface VsLoadedMonthDoc {
  venue_id: string;
  year: number;
  month: number;
  days: VsLoadedDay[];
  totals?: { locked_cents: number | null; loaded_cents: number | null; actual_cents: number | null; variance_cents?: number | null };
  warnings?: string[];
}

export interface PushRow {
  id: string;
  venue_id: string;
  date: string;
  before_cents: number | null;
  after_cents: number;
  method: 'PUT' | 'POST';
  source: 'manual' | 'chat' | 'forecast' | 'restore';
  pushed_by: string | null;
  pushed_by_name: string | null;
  pushed_at: string;
  ok: boolean;
  loaded_id: string | null;
  result: unknown;
}

export interface RestorePlan {
  days: { date: string; amount_cents: number; amount: number }[];
  cannot_restore: string[];
}

// --- The Loaded push (the page helper, wrapped by /api/finance/push) --------

export type PushKind = 'typed' | 'forecast' | 'restore';

export interface CardChange {
  field: string;
  before?: string;
  after: string;
}

export interface PushCardDoc {
  title?: string;
  target?: string | null;
  venue?: string | null;
  changes?: CardChange[];
  warnings?: string[];
  note?: string | null;
  fingerprint?: string | null;
  more_changes?: number;
}

export interface PushPreview {
  card: PushCardDoc | null;
  fingerprint: string | null;
  /** Nothing would change (or the person's standing permission ran it): the result itself. */
  done?: unknown;
}

export interface PushResult {
  success: boolean;
  data?: {
    updated?: number;
    created?: number;
    unchanged?: number;
    skipped_zero?: string[];
    failed?: string[];
    ledger?: string;
    note?: string;
  } | null;
}

// --- Forecasting ------------------------------------------------------------

export interface DriverChip {
  kind: 'holiday' | 'event' | 'function' | 'manual';
  label: string;
  cents: number | null;
}

export interface ForecastDay {
  date: string;
  weekday: number;
  actual_cents: number | null;
  forecast_cents: number | null;
  baseline_cents: number | null;
  uplift_cents: number | null;
  drivers: DriverChip[];
  loaded_cents: number | null;
  locked_cents: number | null;
  override_cents: number | null;
  final_cents: number | null;
  delta_cents: number | null;
  holiday: string | null;
  flags: string[];
}

export interface ScoreBlock {
  n: number;
  mape: number | null;
  median_ape: number | null;
  bias_pct: number | null;
  within_10: number | null;
  within_20: number | null;
  monthly_sum_error_pct: number | null;
}

export interface ScoresDoc {
  venue_id: string;
  year: number;
  month: number;
  horizons: Record<string, ScoreBlock>;
  benchmarks: Record<string, ScoreBlock>;
}

export interface ForecastDoc {
  venue_id: string;
  year: number;
  month: number;
  vintage: { id: string; as_of: string; horizon_days: number; method: string } | null;
  forecast_push_ready: boolean;
  weeks_of_scores: number;
  days: ForecastDay[];
  weeks: { week_start: string; actual_cents: number | null; forecast_cents: number | null; loaded_cents: number | null; locked_cents: number | null }[];
  totals: { actual_cents: number | null; forecast_cents: number | null; loaded_cents: number | null; locked_cents: number | null };
  accuracy: ScoresDoc | null;
  warnings: string[];
}

export interface Sample {
  date: string;
  actual_cents: number | null;
  event_spend_cents: number | null;
  used_cents: number | null;
  reason: 'ok' | 'closed' | 'holiday' | 'driver' | 'no_data' | 'deflated' | string;
}

export interface Driver {
  id: string;
  venue_id: string;
  date: string;
  kind: 'holiday' | 'event' | 'function' | 'manual';
  label: string;
  uplift_cents: number | null;
  uplift_bp: number | null;
  created_by: string | null;
  archived_at: string | null;
}

export interface Override {
  amount_cents: number;
  reason: string | null;
  created_by: string | null;
  created_at: string;
}

export interface DayDoc {
  date: string;
  vintage_id: string | null;
  forecast_cents: number | null;
  baseline_cents: number | null;
  components: {
    samples: Sample[];
    median_of: number;
    level_correction: { ratio: number; factor: number } | null;
    uplifts: { kind: string; label: string; cents: number }[];
    override: Override | null;
    last_year: { same_weekday: { date: string; actual_cents: number | null } | null; same_date: { date: string; actual_cents: number | null } | null } | null;
    flags: string[];
  } | null;
  why: string | null;
  /** The day's chips as the vintage saw them (the same facts as `components.uplifts`). */
  drivers: DriverChip[];
  override: Override | null;
  holiday: string | null;
  loaded_cents: number | null;
  locked_cents: number | null;
  actual_cents: number | null;
}

export interface VenueSettings {
  venue_id: string;
  holiday_region: string | null;
  closed_weekdays: number[];
  level_correction_on: boolean;
  version: number;
}
