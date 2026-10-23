/**
 * Entertainers — the shapes `/api/entertainers/*` and the two public routers
 * return (build/entertainers-api.md is authoritative). Money is integer
 * cents; dates are `YYYY-MM-DD` and times `HH:MM`, venue-local plans that are
 * never put through `new Date()`; instants are ISO 8601 UTC.
 *
 * Bank account, IRD number and date of birth never appear here in full: the
 * register gets `bank_account_ending` / `ird_ending` for `payroll:write`
 * holders only, and the public pages get nothing stored back at all.
 */

export type EntMode = 'off' | 'shadow' | 'live';
export type Lane = 'payroll_employee' | 'payroll_contractor' | 'invoice' | 'exemption';
export type PerformerType = 'solo' | 'band' | 'agent';
export type BandMode = 'leader' | 'members_split';
export type OnboardingStatus = 'invited' | 'submitted' | 'accepted';
export type PerformanceStatus =
  | 'scheduled'
  | 'awaiting_approvals'
  | 'awaiting_manager'
  | 'awaiting_performer'
  | 'mismatch'
  | 'ready_for_payment'
  | 'did_not_perform'
  | 'paid';
export type CheckStatus = 'pending' | 'matched' | 'query' | 'unreadable' | 'waived';
export type EmailStatus = 'sent' | 'delivered' | 'bounced' | 'complained' | 'failed' | 'suppressed' | 'queued';
export type RecipientKind = 'performer' | 'band_member' | 'closing_manager' | 'venue_manager' | 'finance';
export type ClosingManagerSource = 'clockin' | 'both' | 'rostered_only' | 'none' | 'orbit';

export interface EntVenue {
  id: string;
  name: string;
  timezone: string | null;
}

export interface EntSettings {
  closing_manager_roles: string[];
  mismatch_tolerance_minutes: number;
  reminder_interval_days: number;
  reminder_max: number;
  version: number;
}

export interface EntMeta {
  mode: EntMode;
  /** The caller holds `payroll:write`: Payments, accept, shares, bank/IRD. */
  can_pay: boolean;
  venues: EntVenue[];
  lanes: Lane[];
  performer_types: PerformerType[];
  settings: EntSettings;
  is_owner?: boolean;
  counts: {
    awaiting_approvals: number;
    awaiting_performer: number;
    awaiting_manager: number;
    mismatch: number;
    ready_for_payment: number;
    needs_review: number;
    missing_declarations: number;
  };
}

export interface Performer {
  id: string;
  name: string;
  legal_name: string | null;
  trading_name: string | null;
  email: string | null;
  phone: string | null;
  notes: string | null;
  is_active: boolean;
  lane: Lane | null;
  performer_type: PerformerType | null;
  band_mode: BandMode | null;
  onboarding_status: OnboardingStatus;
  submitted_at: string | null;
  accepted_at: string | null;
  accepted_by: string | null;
  bank_changed_at: string | null;
  needs_review: boolean;
  review_reason: string | null;
  gst_registered: boolean | null;
  gst_number: string | null;
  company_name: string | null;
  company_number: string | null;
  exemption_certificate_number: string | null;
  exemption_expiry_date: string | null;
  exemption_expiring: boolean;
  exemption_expired: boolean;
  has_declaration: boolean;
  declaration_method: 'form' | 'upload' | null;
  declaration_signed_at: string | null;
  band_leader_declaration_at: string | null;
  contractor_confirmations: Record<string, boolean> | null;
  invite_sent_at: string | null;
  invite_reminders_sent: number;
  last_reminder_at: string | null;
  payable: boolean;
  payable_reason: string | null;
  version: number;
  // payroll:write only — absent otherwise, and never rendered when absent.
  bank_account_ending?: string | null;
  ird_ending?: string | null;
  date_of_birth?: string | null;
  tax_code?: string | null;
  kiwisaver_rate?: number | null;
  payroll_code?: string | null;
  wt_rate_type?: 'standard' | 'self_selected' | 'tailored' | null;
  wt_rate_bp?: number | null;
  nz_tax_resident?: boolean | null;
}

export interface BandMember {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  share_bp: number;
  invite_status: string | null;
  member_performer_id: string | null;
  invite_sent_at: string | null;
  reminders_sent: number;
  last_reminder_at: string | null;
  sort_order: number;
  accepted: boolean;
}

export interface Finding {
  code: 'total' | 'gst' | 'duplicate_number' | 'payee' | 'dates' | 'unreadable';
  message: string;
  expected?: string | number | null;
  found?: string | number | null;
}

export interface EntFile {
  id: string;
  kind: 'invoice' | 'exemption_certificate' | 'ir330c' | 'company_document';
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  invoice_number: string | null;
  invoice_cents: number | null;
  check_status: CheckStatus | null;
  checked_at: string | null;
  waived_by: string | null;
  waived_at: string | null;
  findings: Finding[];
  /** The reader's output — `payroll:write` only. */
  extracted?: Record<string, unknown> | null;
  performance_ids: string[];
  created_at?: string | null;
}

export interface EmailLink {
  id: string;
  kind: string;
  recipient_kind: RecipientKind;
  recipient_email: string | null;
  subject: string | null;
  sent_at: string | null;
  status: EmailStatus | null;
  performer_id: string | null;
  performer_name: string | null;
  band_member_id: string | null;
  venue_id: string | null;
  venue_name: string | null;
  performance_ids: string[];
  source: 'norm' | 'orbit';
  link_opened_at: string | null;
  is_money: boolean;
  shadow_redirected: boolean;
}

export interface EmailBody extends EmailLink {
  html_body: string;
}

export interface ClosingManager {
  name: string | null;
  staff_id: string | null;
  email: string | null;
  user_id: string | null;
  source: ClosingManagerSource;
  resolved_at: string | null;
}

export interface Performance {
  id: string;
  venue_id: string;
  venue_name: string;
  performer_id: string;
  performer_name: string;
  lane: Lane | null;
  series_id: string | null;
  performance_date: string;
  start_time: string;
  end_time: string;
  ends_next_day: boolean;
  rate_cents: number;
  agreed_cents: number;
  actual_start_time: string | null;
  actual_end_time: string | null;
  actual_ends_next_day: boolean | null;
  actual_cents: number | null;
  minutes: { booked: number; actual: number | null; dst_adjustment: number | null };
  performance_status: 'scheduled' | 'approved' | 'did_not_perform';
  dnp_reason: string | null;
  approved_at: string | null;
  approved_by: string | null;
  approved_by_name: string | null;
  performer_actual_start_time: string | null;
  performer_actual_end_time: string | null;
  performer_actual_ends_next_day: boolean | null;
  performer_confirmed_at: string | null;
  performer_dnp: boolean;
  performer_dnp_reason: string | null;
  mismatch: boolean | null;
  closing_manager: ClosingManager | null;
  payment_status: 'pending' | 'paid';
  paid_at: string | null;
  paid_by: string | null;
  paid_reference: string | null;
  payroll_week_start: string | null;
  invoice_file_id: string | null;
  invoice_check_status: CheckStatus | null;
  status: PerformanceStatus;
  has_ended: boolean;
  emails: {
    confirm_requests: number;
    last_confirm_request_at: string | null;
    manager_requests: number;
    last_manager_request_at: string | null;
  };
  notes: string | null;
  version: number;
}

export interface PerformanceChange {
  id: string;
  changed_at: string;
  changed_by_source: 'manager' | 'performer' | 'system' | 'import';
  changed_by_name: string | null;
  field: string;
  old_value: string | null;
  new_value: string | null;
  note: string | null;
}

export interface Series {
  id: string;
  weekday: number;
  ends_on: string | null;
  [extra: string]: unknown;
}

export interface Push {
  id: string;
  kind: 'entertainer_bill';
  xero_tenant_id: string | null;
  xero_id: string | null;
  xero_number: string | null;
  xero_status: string | null;
  idempotency_key: string;
  pushed_at: string | null;
  pushed_by: string | null;
  voided_at: string | null;
}

export interface Share {
  id: string;
  band_member_id: string;
  member_name: string;
  member_performer_id: string | null;
  amount_cents: number;
  paid_at: string | null;
  paid_by: string | null;
  paid_reference: string | null;
  bill: Push | null;
}

export interface PaymentRow extends Performance {
  performer: { lane: Lane | null; gst_registered: boolean | null; payable: boolean; payable_reason: string | null };
  invoice: EntFile | null;
  shares: Share[] | null;
  bill: Push | null;
}

export interface PerformerDetail {
  performer: Performer;
  band_members: BandMember[] | null;
  documents: EntFile[];
  emails: EmailLink[];
  performances: Performance[];
}

export interface PerformanceDetail {
  performance: Performance;
  changes: PerformanceChange[];
  emails: EmailLink[];
  shares: Share[] | null;
  invoice: EntFile | null;
}

export interface InviteResult {
  sent: boolean;
  url?: string;
  expires_at?: string;
}

/** `page_writes.preview`'s answer: the card the person confirms, and the
 *  fingerprint of what they looked at (a 409 on confirm carries fresh ones). */
export interface BillPreview {
  card: Record<string, unknown> | null;
  fingerprint: string;
  run_params: Record<string, unknown> | null;
}

// ── Public: the performer's portal ───────────────────────────────────────

export type PortalStatus = 'scheduled' | 'awaiting_your_confirmation' | 'confirmed' | 'did_not_perform' | 'paid' | 'times_differ';

export interface PortalRow {
  id: string;
  venue_name: string;
  performance_date: string;
  start_time: string;
  end_time: string;
  ends_next_day: boolean;
  status: PortalStatus;
  can_confirm: boolean;
  approved?: boolean;
  confirmed_at: string | null;
  your_times: { start: string; end: string; ends_next_day: boolean } | null;
  did_not_perform?: boolean;
  invoice: { uploaded: boolean; number: string | null };
}

export interface PortalPage {
  performer_name: string;
  upcoming: PortalRow[];
  recent: PortalRow[];
  can_upload_invoice: boolean;
  lane_needs_invoice: boolean;
  uploads_left_today: number;
}

// ── Public: onboarding ───────────────────────────────────────────────────

export type JoinPurpose = 'onboarding' | 'band_member' | 'declaration';
export type JoinStep = 'about' | 'performing' | 'paid' | 'band' | 'bank_tax' | 'review' | 'code' | 'declaration';
export type PaidAs = 'individual' | 'sole_trader' | 'company' | 'agency' | 'band_leader' | 'partnership' | 'exemption' | 'not_sure';

export interface JoinLane {
  key: PaidAs;
  label: string;
  lane: Lane | null;
  blurb: string;
}

export interface JoinPage {
  purpose: JoinPurpose;
  state: 'open' | 'submitted';
  organisation: string;
  band_name: string | null;
  prefill: { name: string | null; email: string | null; phone: string | null };
  /** An accepted performer re-submitting their details ("Request updated details"). */
  update?: boolean;
  steps: JoinStep[];
  code: { required_before: JoinStep | null; verified: boolean; sent_to_hint: string | null };
  lanes: JoinLane[];
  performer_types?: PerformerType[];
  confirmations: { key: string; label: string }[];
  declaration: { wording_version: string; paragraphs: string[]; understand: string[]; confirm_label: string; standard_rate_percent?: number };
  expires_at: string | null;
}
