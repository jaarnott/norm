/**
 * The Loaded Payroll API's shapes (`payroll-api.md`, `xero-api.md` in the
 * build folder). Money is integer cents; hours are decimals (the API sends
 * numbers, sometimes as strings from Numeric columns); dates "YYYY-MM-DD";
 * instants ISO 8601 with an offset.
 */

export type PayrollMode = 'off' | 'shadow' | 'live';

export type IcoLeaveTax = 'account_default' | 'no_gst' | 'standard_gst';

/** The leave line's tax rule, in the words the accountant decides in. */
export const ICO_LEAVE_TAX_LABELS: Record<IcoLeaveTax, string> = {
  account_default: 'The leave account’s own tax rate in Xero',
  no_gst: 'No GST',
  standard_gst: 'GST at the standard rate',
};

export interface Entity {
  id: string;
  venue_id: string;
  venue_name: string;
  is_trading: boolean;
  loaded_company_id: string | null;
  pay_week_start_weekday: number;
  payroll_system: string;
  ace_pay_type_code: string;
  ace_gratuity_allowance_code: string | null;
  security_pay_code: string;
  uniform_bond_cents: number;
  ico_leave_uplift_bp: number;
  /** The tax rule on the leave line of an intercompany invoice or bill. */
  ico_leave_tax: IcoLeaveTax;
  sick_approver_name: string | null;
  payroll_contact_email: string | null;
  payroll_on: boolean;
  day_start: string;
  timezone: string;
  version: number;
}

export interface PayrollMeta {
  mode: PayrollMode;
  scopes: string[];
  can_write: boolean;
  organization: { id: string; name: string; payroll_contact_email: string | null; currency: string };
  entities: Entity[];
  expected_classes: Record<string, string>;
  this_week_start: string;
  last_week_start: string;
}

export interface Rule {
  id: string;
  organization_id: string | null;
  rule: string;
  effective_from: string;
  params: Record<string, unknown>;
  source: string | null;
  is_platform_default: boolean;
}

export interface SnapshotTotals {
  hours: number;
  cost_cents: number;
  ph_cost_cents: number;
  leave_hours: number;
  leave_cost_cents: number;
  security_cost_cents: number;
}

export interface Snapshot {
  id: string;
  week_id: string;
  taken_at: string;
  taken_by: string | null;
  source: 'monday' | 'close' | 'manual' | 'orbit';
  shift_count: number;
  dropped_deleted: number;
  dropped_unfinished: number;
  content_sha256: string;
  superseded_at: string | null;
  totals: SnapshotTotals;
}

export interface Fact {
  id: string;
  source_shift_id: string;
  staff_member_id: string;
  staff_name: string;
  pay_code: string | null;
  shift_type: string | null;
  remuneration_type: string | null;
  role_id: string | null;
  role_name: string | null;
  cost_group: 'bar_floor' | 'kitchen' | 'other' | null;
  worked_venue_loaded_id: string | null;
  worked_venue_id: string | null;
  worked_venue_name: string | null;
  is_from_other_venue: boolean;
  clockin_at: string | null;
  clockout_at: string | null;
  trading_date: string | null;
  hours: number | string;
  cost_cents: number;
  ph_hours: number | string;
  ph_cost_cents: number;
  leave_request_id: string | null;
  leave_type_name: string | null;
  is_approved: boolean;
}

export interface Diff {
  a: string;
  b: string;
  same: boolean;
  added: Fact[];
  removed: Fact[];
  changed: { source_shift_id: string; staff_name: string; fields: Record<string, [unknown, unknown]> }[];
  totals: { a: { hours: number; cost_cents: number }; b: { hours: number; cost_cents: number }; delta: { hours: number; cost_cents: number } };
}

export interface ExportRow {
  id: string;
  week_id: string;
  snapshot_id: string;
  kind: 'import' | 'leave' | 'gratuities' | 'entertainers';
  filename: string;
  content_sha256: string;
  line_count: number;
  totals: Record<string, unknown>;
  generated_by: string | null;
  generated_at: string;
  content?: string;
}

export interface PushBrief {
  id: string;
  kind: string;
  xero_id?: string | null;
  xero_number: string | null;
  xero_status: string | null;
  idempotency_key?: string;
  pushed_at: string | null;
  voided_at: string | null;
}

export interface WeekInputs {
  ace_inputs: AceInputs | null;
  adjustments: Adjustment[];
  accruals: Accruals | null;
  payouts: { count: number; paid_cents: number; requested_cents: number };
  gratuities: { batches: number; payable_cents: number };
}

export interface Week {
  id: string;
  venue_id: string;
  venue_name: string;
  week_start: string;
  week_ending: string;
  window: { start: string; end: string; day_start: string; timezone: string };
  status: 'open' | 'snapshotted' | 'close_ready' | 'exported' | 'closed';
  notes: string | null;
  closed_at: string | null;
  closed_by: string | null;
  version: number;
  current_snapshot: Snapshot | null;
  close_snapshot: Snapshot | null;
  drift?: Diff | null;
  exports?: ExportRow[];
  inputs?: WeekInputs;
  pushes?: PushBrief[];
}

export interface StaffRow {
  staff_member_id: string;
  staff_name: string;
  pay_code: string | null;
  remuneration_type: string | null;
  shifts: number;
  hours: number | string;
  cost_cents: number;
  ph_hours: number | string;
  ph_cost_cents: number;
  other_venue_hours: number | string;
  all_approved: boolean;
  shift_ids: string[];
}

export interface LeaveRow {
  leave_request_id: string;
  staff_member_id: string;
  staff_name: string;
  pay_code: string | null;
  leave_type_name: string | null;
  dates: string[];
  hours: number | string;
  cost_cents: number;
  /** The Ace style from Loaded's leave type (or set in Norm for a company's own type); null when none. */
  ace_style: string | null;
  ace_style_from?: 'loaded' | 'override' | null;
  /** Loaded marks the type unpaid: left out of the leave file. */
  unpaid?: boolean;
}

export interface SalariedRow {
  staff_member_id: string;
  staff_name: string;
  pay_code: string | null;
  hours: number | string;
  cost_cents: number;
  by_shift_type: Record<string, unknown>;
}

export interface Counterparty {
  worked_venue_loaded_id: string | null;
  worked_venue_id: string | null;
  worked_venue_name: string | null;
  hours: number | string;
  cost_cents: number;
  shifts: number;
}

export interface Allocation {
  id: string;
  staff_member_id: string;
  staff_name: string;
  pay_code: string | null;
  hours: number | string;
  amount_cents: number;
  is_payable: boolean;
  payroll_venue_id: string | null;
  payroll_venue_name: string | null;
  excluded_at: string | null;
  excluded_by: string | null;
}

export interface Batch {
  id: string;
  venue_id: string;
  venue_name: string;
  week_start: string;
  period: string;
  total_cents: number;
  gl_code: string | null;
  notes: string | null;
  created_by: string | null;
  source: string;
  rehearsal: boolean;
  created_at: string;
  allocations?: Allocation[];
  payable_cents?: number;
  forfeited_cents?: number;
  intercompany_cents?: number;
}

/** A share this entity's payroll pays this week (the payrun route's shape). */
export interface PayableShare {
  batch_id: string;
  period: string;
  earned_at_venue_id: string;
  earned_at_venue_name: string | null;
  staff_name: string;
  pay_code: string | null;
  amount_cents: number;
  rehearsal: boolean;
}

export interface EntertainerLine {
  performance_id: string;
  pay_code: string | null;
  hours: number | string;
  rate_cents: number;
  amount_cents: number;
  performer?: string | null;
  payment_status?: string | null;
}

export interface PayrunDoc {
  snapshot: Snapshot | null;
  entity: Entity;
  staff: { rows: StaffRow[]; totals: Record<string, number> };
  security: { pay_code: string; rows: StaffRow[]; totals: Record<string, number> };
  /** `setup_error`: Loaded's leave types couldn't be read, so the rows carry no Ace style. */
  leave: { rows: LeaveRow[]; totals: Record<string, number>; setup_error?: string | null };
  salaried: { rows: SalariedRow[] };
  intercompany: { counterparties: Counterparty[]; rows: Fact[] };
  gratuities: { batches: Batch[]; payable_here: PayableShare[]; paid_elsewhere?: Allocation[] };
  entertainers: { lines: EntertainerLine[]; available: boolean };
  facts: Fact[];
}

export interface Hygiene {
  live: boolean;
  error?: string;
  week_start: string;
  unapproved_clockins: { staff_name: string; clockin_at: string | null; clockout_at: string | null }[];
  staff_without_pay_code: string[];
  unfinished_clockins: { staff_name: string; clockin_at: string | null }[];
  staff_without_timeline: string[];
  salaried_count: number;
  public_holiday_dates: string[];
  parity: { pay_summary_total_wages?: number; rows_wages?: number; difference?: number; within_tolerance?: boolean; [k: string]: unknown } | null;
  snapshot: { id?: string; taken_at: string; source: string; shift_count: number; dropped_deleted: number; dropped_unfinished: number } | null;
  snapshot_report: { taken: number; of: number; failed: { venue_id?: string; venue_name: string | null; error: string }[] };
  email: { last_sent_at: string | null; to: string | null; status?: string | null };
}

export type WorkflowKind = 'annual' | 'sick' | 'generic';

export interface SickInputs {
  qualification: {
    employment_start_date: string | null;
    six_months: boolean;
    avg_hours: boolean;
    continuous: boolean;
    override: boolean;
    override_notes: string | null;
    computed?: SickEligibility | null;
  };
  balance: { available_days: number | null; daily_hours: number | null; override: boolean; override_notes: string | null };
  certificate: { received: boolean; override: boolean; override_notes: string | null };
  payout: { day_hours: { date: string; hours: number }[]; hourly_rate_cents: number | null };
}

export interface AnnualInputs {
  holiday_pay_available_cents: number | null;
  uniform_bond_cents: number | null;
  bond_consent_file_id: string | null;
  bond_held_in_ace: boolean;
}

export interface Payout {
  id: string;
  venue_id: string;
  week_start: string;
  loaded_request_id: string;
  staff_member_id: string;
  staff_name: string;
  leave_type: string | null;
  workflow_kind: WorkflowKind;
  requested_cents: number;
  paid_cents: number;
  bond_deducted_cents: number;
  bond_consent_file_id: string | null;
  bond_consent: 'file' | 'held_in_ace' | null;
  workflow_inputs: Record<string, unknown> & { decision?: Record<string, unknown> };
  notes: string | null;
  decided_by: string | null;
  decided_at: string | null;
  source: 'norm' | 'orbit' | 'orbit_legacy';
  rehearsal: boolean;
  version: number;
}

export interface RequestedRow {
  leave_request_id: string;
  staff_member_id: string;
  staff_name: string;
  pay_code: string | null;
  leave_type_name: string | null;
  dates: string[];
  hours: number | string;
  requested_cents: number;
  is_paid_type: boolean;
  workflow_kind: WorkflowKind;
  payout: Payout | null;
}

export interface RequestedDoc {
  week_start: string;
  live?: boolean;
  error?: string | null;
  rows: RequestedRow[];
  totals: { requested_cents: number; paid_cents: number };
}

export interface SickEligibility {
  employment_start_date: string | null;
  start_date_source: 'loaded' | 'approver' | null;
  months_employed: number | null;
  lookback_weeks: number;
  weeks_with_hours: number;
  avg_hours_per_week: number;
  usual_daily_hours: number | null;
  qualifies_six_months: boolean;
  qualifies_avg_hours: boolean;
  qualifies_continuous: boolean;
  rule: Record<string, unknown>;
}

/** A leave type as Loaded holds it (`GET /api/payroll/leave-types`), with the Ace style the leave export uses. */
export interface LeaveType {
  loaded_leave_type_id: string;
  name: string;
  /** Loaded's own (managed) type, the company's own (custom), or null for a set style whose type Loaded no longer lists. */
  kind: 'managed' | 'custom' | null;
  is_paid: boolean | null;
  active: boolean;
  ace_style: string | null;
  ace_style_from: 'loaded' | 'override' | null;
  override: { id: string; ace_style: string; set_by: string | null; set_at: string | null; version: number } | null;
}

export interface AceInputs {
  id: string;
  week_id: string;
  gross_cents: number;
  net_cents: number;
  esct_cents: number;
  kiwisaver_cents: number;
  payroll_taxes_cents: number;
  entered_by: string | null;
  entered_at: string | null;
  source: string;
  rehearsal: boolean;
  version: number;
}

export interface Adjustment {
  id: string;
  week_id: string;
  label: string;
  amount_cents: number;
  xero_account_code: string | null;
  xero_account_name: string | null;
  report_target: 'wages' | 'leave' | null;
  notes: string | null;
  sort_order: number;
  created_by: string | null;
  source: string;
  rehearsal: boolean;
  version: number;
}

export interface Accruals {
  id: string;
  week_id: string;
  opening_cents: number | null;
  opening_effective_cents?: number;
  accrued_cents: number;
  paid_cents: number;
  closing_cents: number;
  source: string;
}

export interface IcoLine {
  staff_member_id: string;
  staff_name: string;
  hours: number | string;
  amount_cents: number;
  fact_ids?: string[];
  allocation_id?: string;
  gratuity: boolean;
}

export interface IcoGroup {
  counterparty_venue_id: string | null;
  counterparty: string;
  contact?: { contact_id: string; name: string | null } | null;
  lines: IcoLine[];
  hours: number | string;
  amount_cents: number;
}

export interface IcoVenue {
  venue_id: string;
  venue: string;
  in: IcoGroup[];
  out: IcoGroup[];
  in_cents: number;
  out_cents: number;
  net_cents: number;
  warnings: string[];
}

export interface IcoPicture {
  week_start: string;
  week_ending: string;
  venues: IcoVenue[];
}

export interface Reconciliation {
  venue_id: string;
  venue: string;
  week_id?: string | null;
  week_start: string;
  week_ending: string;
  snapshot: { id: string; source: string; taken_at: string; shift_count: number; content_sha256?: string } | null;
  terms: {
    timeclock: { cents: number; hours: number; shifts: number; by_cost_group: Record<string, number>; fact_ids: string[]; unapproved?: number };
    intercompany: { cents: number; in_cents: number; out_cents: number; counterparties: IcoGroup[]; [k: string]: unknown };
    leave_paid: { cents: number; requested_cents: number; loaded_leave_cents?: number; rows: { id: string; staff_name: string; leave_type: string | null; requested_cents: number; paid_cents: number }[]; undecided_request_ids?: string[] };
    security: { cents: number; pay_code: string; fact_ids: string[] };
    accrued_additions: { cents: number; row_id: string | null };
    accrued_deductions: { cents: number; row_id: string | null };
    accrual: { opening_cents: number; closing_cents: number; opening_from: string };
    other_adjustments: { cents: number; rows: { id: string; label: string; amount_cents: number; xero_account_code: string | null; xero_account_name: string | null; report_target: string | null }[] };
    entertainers: { cents: number; rows: EntertainerLine[]; available: boolean };
    gratuities: { cents: number; rows: { allocation_id: string; batch_id: string; staff_name: string; amount_cents: number; payroll_venue_id: string | null }[] };
  };
  ace: { id: string; gross_cents: number; net_cents: number; esct_cents: number; kiwisaver_cents: number; version?: number; rehearsal?: boolean } | null;
  computed_cents: number;
  difference_cents: number | null;
  band: 'ok' | 'near' | 'off' | null;
  blockers: string[];
  warnings: string[];
  status: 'pushed' | 'draft' | 'none';
  live_push: PushBrief | null;
  notes?: string | null;
}

export interface GroupRow {
  venue_id: string;
  venue: string;
  timeclock_cents: number;
  adjustments_cents: number;
  ace_gross_cents: number | null;
  difference_cents: number | null;
  band: 'ok' | 'near' | 'off' | null;
  status: 'pushed' | 'draft' | 'none';
  blockers: number;
}

export interface GroupOverview {
  week_start: string;
  week_ending: string;
  venues: GroupRow[];
  group: { timeclock_cents: number; adjustments_cents: number; ace_gross_cents: number | null; difference_cents: number | null; [k: string]: unknown };
}

export interface JournalLine {
  key: string;
  description: string;
  account_code: string | null;
  account_name: string | null;
  side: 'DR' | 'CR';
  amount_cents: number;
  source?: Record<string, unknown>;
}

export interface JournalDraft {
  id: string;
  week_id: string;
  snapshot_id: string | null;
  kind: 'journal' | 'ico_invoice' | 'ico_bill';
  counterparty_venue_id: string | null;
  counterparty?: string | null;
  lines: JournalLine[] | (IcoLine | { key: string; bp?: number; amount_cents: number })[];
  debit_cents: number;
  credit_cents: number;
  residual_cents: number;
  basis_sha256: string;
  built_at: string;
  built_by: string | null;
  live_push?: PushBrief | null;
  [extra: string]: unknown;
}

export interface JournalDoc {
  venue_id: string;
  week_start: string;
  week_ending: string;
  journal: JournalDraft | null;
  ico: JournalDraft[];
  drafts?: JournalDraft[];
  pushes: PushBrief[];
  blockers: string[];
  warnings?: string[];
  [extra: string]: unknown;
}

export interface Push extends PushBrief {
  venue_id: string;
  week_id: string | null;
  week_start: string | null;
  draft_id: string | null;
  counterparty_venue_id: string | null;
  counterparty?: string | null;
  entertainer_performance_id: string | null;
  xero_tenant_id: string | null;
  pushed_by: string | null;
  voided_by: string | null;
  source: string | null;
}

export interface XeroAccount {
  code: string | null;
  name: string;
  type: string | null;
  class: string | null;
}

export interface MappingDoc {
  venue_id: string;
  connected: boolean;
  tenant: string | null;
  organisation?: string | null;
  payroll: Record<string, string>;
  intercompany: { wages_account?: string | null; leave_account?: string | null; contacts: Record<string, { contact_id: string; name: string | null }> };
  tracking: Record<string, string>;
  account_names?: Record<string, string>;
  accounts: XeroAccount[];
  chart_error?: string | null;
  missing_for_journal: string[];
  keys: { payroll: string[]; intercompany: string[] };
}

export interface MappingIssue {
  level: 'error' | 'warning';
  key: string;
  message: string;
}

export interface ParityRun {
  id: string;
  module: string;
  period: string;
  ran_at: string;
  trigger: string;
  totals: Record<string, unknown>;
  ran_by: string | null;
  unexplained: number;
  items: number;
}

export interface ParityItem {
  id: string;
  entity: string | null;
  entity_name: string | null;
  key: string;
  norm_value: string | null;
  orbit_value: string | null;
  class: 'expected' | 'unexplained';
  explanation: string | null;
  signed_off_by: string | null;
  signed_off_at: string | null;
}

export interface JobRun {
  id: string;
  job: string;
  scope_id: string | null;
  venue_name: string | null;
  slot: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface ShadowDoc {
  runs: ParityRun[];
  failed_jobs: JobRun[];
  expected_classes: Record<string, string>;
}
