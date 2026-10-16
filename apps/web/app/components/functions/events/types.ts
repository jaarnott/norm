/**
 * Norm Functions — Events page shapes, as `/api/functions/events*` returns
 * them (scratchpad F3-api.md §7, §8; F4-api.md for send-to-client). Money is
 * integer cents; event dates and times are venue-local plans ("YYYY-MM-DD",
 * "HH:MM"); `*_at` values are UTC instants.
 */

export type EventStatus = 'booked' | 'sent_to_client' | 'in_progress' | 'confirmed' | 'cancelled';
export type SectionKey = 'details' | 'beverages' | 'food';

export interface EventRow {
  id: string;
  version: number;
  name: string | null;
  enquiry_id: string | null;
  contact_id: string;
  contact_name: string | null;
  /** Not in the API yet (build-notes F7 request) — shown once rows carry it. */
  contact_company?: string | null;
  venue_id: string;
  venue_name: string | null;
  area_id: string | null;
  area_name: string | null;
  booking_type: string | null;
  event_date: string;
  start_time: string | null;
  end_time: string | null;
  ends_next_day: boolean;
  guests: number | null;
  bar_tab_cents: number | null;
  status: EventStatus;
  edited_since_confirmed: boolean;
  min_spend_cents: number | null;
  food_cents: number;
  expected_spend_cents: number;
  cancelled_at: string | null;
}

export interface EventList {
  rows: EventRow[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface CalendarDay {
  date: string;
  in_month: boolean;
  expected_spend_cents: number;
  events: EventRow[];
}

export interface CalendarMonth {
  month: string;
  from: string;
  to: string;
  expected_spend_cents: number;
  events: number;
  days: CalendarDay[];
}

export interface ContactEmail {
  email: string;
  is_primary: boolean;
  source: string | null;
}

export interface Contact {
  id: string;
  version: number;
  name: string | null;
  company: string | null;
  phone: string | null;
  notes: string | null;
  archived_at: string | null;
  anonymised_at: string | null;
  created_at: string;
  emails?: ContactEmail[];
  email?: string | null;
}

/** Every editable event field (EventFields in routers/functions.py). */
export interface EventFields {
  name: string | null;
  contact_id: string;
  invoice_contact_id: string | null;
  booking_type: string | null;
  area_id: string | null;
  event_date: string;
  start_time: string | null;
  end_time: string | null;
  ends_next_day: boolean;
  guests: number | null;
  description: string | null;
  notes: string | null;
  special_instructions: string | null;
  payment_method: string | null;
  deposit_cents: number | null;
  deposit_status: string | null;
  table_plan: string | null;
  table_plan_file_id: string | null;
  arrival_time: string | null;
  seated_time: string | null;
  timing_preference: string | null;
  timing_notes: string | null;
  bar_tab_cents: number | null;
  bar_tab_authorised_people: string | null;
  bar_tab_restrictions: string | null;
  bar_tab_notes: string | null;
  arrival_drinks: string | null;
  table_ordering: boolean | null;
  table_ordering_limit_cents: number | null;
  /** Health data — never log it or put it in a toast. */
  allergies: string | null;
  food_timing_type: string | null;
  food_time: string | null;
  food_end_time: string | null;
  food_notes: string | null;
}

export interface FnEvent extends EventFields {
  id: string;
  version: number;
  enquiry_id: string | null;
  venue_id: string;
  booking_label: string | null;
  created_via: string | null;
  confirmation_channel: string | null;
  created_by_user_id: string | null;
  created_by_label: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
  created_at: string;
  updated_at: string;
  /** `/api/functions/files/<id>` (bearer-token fetch) or null. */
  table_plan_file_url?: string | null;
}

export interface TimingRow {
  id?: string;
  at_time: string | null;
  ends_next_day: boolean;
  label: string | null;
  activity: string | null;
  is_heading: boolean;
}

export interface SecurityRow {
  id?: string;
  name: string | null;
  start_time: string | null;
  end_time: string | null;
  ends_next_day: boolean;
  notes: string | null;
}

export interface EntertainmentRow {
  id?: string;
  kind: 'dj' | 'playlist';
  name: string | null;
  link: string | null;
  start_time: string | null;
  end_time: string | null;
  ends_next_day: boolean;
  notes: string | null;
}

export interface AvRow {
  id?: string;
  kind: 'in_house_mic' | 'in_house_tv' | 'external_av';
  details: string | null;
}

export interface MenuItem {
  id: string;
  template_item_id: string | null;
  kind: 'header' | 'item';
  name: string;
  description: string | null;
  quantity: number;
  unit_price_cents: number | null;
  line_total_cents: number | null;
  default_percent: number | null;
  suggested_quantity: number | null;
  dietaries: string[];
  loaded_recipe_id: string | null;
  loaded_stock_item_id: string | null;
}

export interface Menu {
  id: string;
  version: number;
  enquiry_id: string | null;
  event_id: string | null;
  venue_id: string;
  template_id: string | null;
  name: string;
  is_set_menu: boolean;
  price_per_person_cents: number | null;
  guest_count: number;
  notes: string | null;
  sort_order: number;
  total_cents: number;
  items: MenuItem[];
}

export interface OptionApproval {
  id: string;
  kind: 'approve' | 'override' | 'revoke';
  automatic: boolean;
  reason: string | null;
  amount_cents: number | null;
  actor: string | null;
  occurred_at: string;
}

export interface OptionResponse {
  id: string;
  kind: 'confirm' | 'decline';
  channel: string;
  customer_email: string | null;
  note: string | null;
  recorded_by: string | null;
  occurred_at: string;
}

/** A minimum-spend option (only the booked one appears on an event). */
export interface MinSpendOption {
  id: string;
  version: number;
  enquiry_id: string;
  event_id: string | null;
  venue_id: string;
  area_id: string | null;
  area_name: string | null;
  label: string | null;
  amount_cents: number;
  food_cents: number;
  total_cents: number;
  event_date: string | null;
  start_time: string | null;
  end_time: string | null;
  ends_next_day: boolean;
  notes: string | null;
  state: string;
  approvals: OptionApproval[];
  responses: OptionResponse[];
}

export interface SectionState {
  kind: 'confirmed' | 'skipped' | 'reopened' | null;
  done: boolean;
  actor_kind?: string | null;
  actor?: string | null;
  note?: string | null;
  occurred_at?: string | null;
}

export interface EventChange {
  id: string;
  kind: 'created' | 'edited' | 'link_sent' | 'cancelled' | 'reinstated';
  section: string | null;
  changes: Record<string, [unknown, unknown]> | null;
  after_confirmation: boolean;
  note: string | null;
  actor_kind: 'staff' | 'client' | 'system' | 'import';
  actor: string | null;
  occurred_at: string;
}

export interface TimelineItem {
  at: string;
  kind: string;
  title: string;
  detail: string | null;
  actor: string | null;
  ref: Record<string, string>;
}

export interface Area {
  id: string;
  version: number;
  venue_id: string;
  name: string;
  description: string | null;
  capacity_seated: number | null;
  capacity_standing: number | null;
  booking_types: string[];
  requires_approval: boolean;
  min_spend_notes: string | null;
  active: boolean;
  sort_order: number;
}

export interface PortalLink {
  id: string;
  kind: 'portal';
  url: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

/** `GET /events/{id}/portal-access` — who may sign in to the client portal. */
export interface PortalPerson {
  email: string;
  allowed: boolean;
  verified_at: string | null;
  invited_by_email: string | null;
  removed_at: string | null;
  locked_for_wrong_codes: boolean;
}

export interface LinkedThread {
  link_id: string;
  account_id: string;
  mailbox: string | null;
  venue_id: string | null;
  gmail_thread_id: string;
  linked_at: string;
  mirrored: boolean;
  subject: string | null;
  snippet: string | null;
  last_message_at: string | null;
  last_from_email: string | null;
  last_from_name: string | null;
  message_count: number | null;
  unread_count: number | null;
}

export interface EventDetail {
  event: FnEvent;
  status: {
    status: EventStatus;
    confirmed_at: string | null;
    edited_since_confirmed: boolean;
    sections_done: SectionKey[];
  };
  money: {
    min_spend_cents: number | null;
    food_cents: number;
    bar_tab_cents: number;
    expected_spend_cents: number;
  };
  contact: Contact | null;
  invoice_contact: Contact | null;
  venue: { id: string; name: string; timezone: string | null } | null;
  area: Area | null;
  enquiry: { id: string; subject: string | null; stage: string } | null;
  children: {
    timings: TimingRow[];
    security: SecurityRow[];
    entertainment: EntertainmentRow[];
    av: AvRow[];
  };
  menus: Menu[];
  options: MinSpendOption[];
  sections: Record<SectionKey, SectionState>;
  changes: EventChange[];
  enabled_fields: Record<string, boolean>;
  threads: LinkedThread[];
  portal_links: PortalLink[];
}

export interface PreorderTemplate {
  id: string;
  version: number;
  venue_id: string;
  name: string;
  description: string | null;
  is_set_menu: boolean;
  price_per_person_cents: number | null;
  active: boolean;
  items: { id: string; kind: 'header' | 'item'; name: string }[];
}

/** `POST /events/{id}/send-to-client` → OutboxResult + the portal link. */
export interface SendResult {
  outbox_id: string;
  status: 'sent' | 'sending' | 'failed';
  rfc822_message_id: string | null;
  gmail_message_id: string | null;
  gmail_thread_id: string | null;
  duplicate: boolean;
  error: string | null;
  portal_url?: string;
  account_id?: string;
}

// ── print payloads (GET /events/{id}/print/{kind}) ──────────────────────────

export type PrintKind = 'full' | 'run_sheet' | 'food';

export interface PrintLine {
  kind: 'header' | 'item';
  name: string;
  dietaries?: string[];
  quantity?: number;
  unit_price_cents?: number | null;
  line_total_cents?: number | null;
}

export interface PrintSheet {
  kind: PrintKind;
  generated_at: string;
  currency: string;
  tax_label: string;
  event: {
    id: string;
    name: string | null;
    event_date: string;
    start_time: string | null;
    end_time: string | null;
    ends_next_day: boolean;
    guests: number | null;
    status: EventStatus;
    edited_since_confirmed: boolean;
    booking_type: string | null;
    booking_label: string | null;
    venue_name: string | null;
    area_name: string | null;
    contact: { name: string | null; company: string | null; email: string | null; phone: string | null };
  };
  details: {
    min_spend_cents: number | null;
    payment_method: string | null;
    deposit: { cents: number | null; status: string | null } | null;
    security: { name: string | null; start_time: string | null; end_time: string | null; notes: string | null }[] | null;
    entertainment:
      | { kind: string; name: string | null; link: string | null; start_time: string | null; end_time: string | null; notes: string | null }[]
      | null;
    av: { kind: string; details: string | null }[] | null;
    notes: string | null;
    special_instructions: string | null;
    description: string | null;
    table_plan: string | null;
  } | null;
  timings: {
    arrival_time: string | null;
    seated_time: string | null;
    preference: string | null;
    notes: string | null;
    schedule: { at_time: string | null; ends_next_day: boolean; label: string | null; activity: string | null; is_heading: boolean }[] | null;
  } | null;
  beverages: {
    bar_tab_cents: number | null;
    authorised_people: string | null;
    restrictions: string | null;
    arrival_drinks: string | null;
    table_ordering: { on: boolean; limit_cents: number | null } | null;
    notes: string | null;
  } | null;
  food: {
    menus: {
      id: string;
      name: string;
      is_set_menu: boolean;
      guest_count: number;
      price_per_person_cents: number | null;
      lines: PrintLine[];
      total_cents: number;
    }[];
    kitchen_counts: { name: string; dietaries: string[]; quantity: number }[];
    food_cents: number;
    allergies: string | null;
    timing: { type: string | null; label: string | null; time: string | null; end_time: string | null } | null;
    notes: string | null;
  } | null;
  money?: { min_spend_cents: number | null; food_cents: number; bar_tab_cents: number; expected_spend_cents: number };
  skips: Partial<Record<SectionKey, { note: string | null; at: string | null; by: string | null }>>;
  cancelled?: { at: string | null; reason: string | null };
}
