/**
 * Norm Functions — the dashboard and report payloads (F3-api.md §3). Every
 * figure on the Dashboard and Reports pages is one of these numbers as the
 * API sent it.
 */
import type { EnquiryKpis, EventRow } from '../enquiries/types';

export interface ConversionVenue {
  venue_id: string | null;
  venue_name: string | null;
  enquiries: number;
  confirmed: number;
  lost: number;
  open: number;
  rate_pct: number | null;
}

export interface Conversion {
  weeks: number;
  lag_weeks: number;
  cohort_from: string;
  cohort_to: string;
  enquiries: number;
  confirmed: number;
  lost: number;
  open: number;
  rate_pct: number | null;
  by_venue: ConversionVenue[];
  /** The cohort's enquiries (report endpoint only, not the dashboard's). */
  rows?: ConversionRow[];
}

export interface ConversionRow {
  id: string;
  subject: string | null;
  contact: string | null;
  venue_id: string | null;
  venue: string | null;
  received: string;
  stage: string;
  outcome: 'confirmed' | 'lost' | 'open';
  counted: boolean;
}

export interface SalesWeek {
  week_start: string;
  events: number;
  cents: number;
}

export interface AttentionItem {
  kind: string;
  label: string | null;
  enquiry_id: string | null;
  event_id: string | null;
  title: string | null;
  contact_name: string | null;
  venue_name: string | null;
  date: string | null;
  overdue: boolean;
  detail: string | null;
  /** The enquiry's follow-up reason (null on event items). */
  follow_up_reason?: string | null;
}

export interface Dashboard {
  today: string;
  timezone: string;
  enquiries_last_28_days: { count: number; prior: number; change_pct: number | null };
  open_enquiries: EnquiryKpis;
  booked_events: { count: number; expected_spend_cents: number };
  next_4_weeks: { from: string; to: string; events: number; expected_spend_cents: number; weeks: SalesWeek[] };
  conversion: Conversion;
  pipeline: { open: Record<string, number>; last_28_days: Record<string, number> };
  events_unconfirmed_14_days: number;
  needs_attention: AttentionItem[];
}

export interface EnquiriesOverTime {
  from: string;
  to: string;
  prior_from: string;
  prior_to: string;
  timezone: string;
  total: number;
  prior_total: number;
  change: number;
  change_pct: number | null;
  days: { date: string; count: number; prior_date: string; prior_count: number }[];
}

export interface UpcomingEvents {
  from: string;
  days: number | null;
  rows: EventRow[];
  totals: { events: number; guests: number; expected_spend_cents: number };
}

export interface Sales {
  from: string;
  to: string;
  events: number;
  expected_spend_cents: number;
  weeks: SalesWeek[];
}

export interface LeadRow {
  source: string;
  medium?: string;
  campaign?: string;
  enquiries: number;
  confirmed: number;
  lost: number;
  confirmed_value_cents: number;
  rate_pct: number | null;
}

export interface FormLeadRow {
  source: string;
  medium: string;
  campaign: string;
  leads: number;
  enquiries: number;
  with_click_id: number;
}

export interface LeadSources {
  from: string;
  to: string;
  enquiries: number;
  by_channel: LeadRow[];
  by_campaign: LeadRow[];
  form_leads: FormLeadRow[];
}

export interface ApprovalRow {
  id: string;
  occurred_at: string;
  kind: 'approve' | 'override' | 'revoke';
  automatic: boolean;
  actor: string | null;
  reason: string | null;
  amount_cents: number | null;
  min_spend_id: string;
  option_label: string | null;
  enquiry_id: string | null;
  enquiry_subject: string | null;
  contact: string | null;
  venue_id: string;
  venue_name: string | null;
  area_name: string | null;
}

export interface Approvals {
  from: string;
  to: string;
  rows: ApprovalRow[];
  totals: {
    approve: { count: number; cents: number };
    override: { count: number; cents: number };
    revoke: { count: number; automatic: number };
  };
}
