/**
 * Norm Functions — the staff API shapes the Dashboard, Enquiries, Contacts and
 * Reports pages read (scratchpad F3-api.md, routers/functions.py). Derived
 * state (stage, next action, option state, totals) is the server's: these
 * pages show it and never recompute it.
 */

export type Stage = 'new' | 'replied' | 'quoted' | 'confirmed' | 'lost';
export type NextActionKind = 'requested' | 'reply_overdue' | 'day_14' | 'day_7' | 'none';
export type OptionState = 'proposed' | 'approved' | 'confirmed' | 'declined' | 'withdrawn';
export type EventStatus = 'booked' | 'sent_to_client' | 'in_progress' | 'confirmed' | 'cancelled';
export type StaffChannel = 'email' | 'phone' | 'in_person';

export interface Page<T> {
  rows: T[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ContactEmail {
  email: string;
  is_primary: boolean;
  source: string | null;
}

export interface ContactCounts {
  enquiries: number;
  open: number;
  confirmed: number;
  lost: number;
  events: number;
  conversion_pct: number | null;
}

export interface Contact {
  id: string;
  version: number;
  name: string;
  company: string | null;
  phone: string | null;
  notes: string | null;
  archived_at: string | null;
  anonymised_at: string | null;
  created_at: string;
  emails?: ContactEmail[];
  email?: string | null;
  counts?: ContactCounts;
}

export interface NextAction {
  kind: NextActionKind;
  label: string | null;
  due_date: string | null;
  overdue: boolean;
  due_today: boolean;
  /** Detail only: a sentence saying why. */
  why?: string | null;
  source?: 'customer' | 'policy' | null;
}

export interface EnquiryRow {
  id: string;
  version: number;
  subject: string | null;
  contact_id: string;
  contact_name: string | null;
  contact_company: string | null;
  /** List endpoint only. */
  contact_email?: string | null;
  venue_id: string | null;
  venue_name: string | null;
  booking_type: string | null;
  guests: number | null;
  preferred_date_start: string | null;
  preferred_date_end: string | null;
  source: string | null;
  owner_user_id: string | null;
  stage: Stage;
  next_action: NextAction;
  last_inbound_at: string | null;
  last_outbound_at: string | null;
  quote_sent_at: string | null;
  follow_up_date: string | null;
  archived_at: string | null;
  created_at: string;
  value_cents: number | null;
  /** List endpoint only: options still on the table (proposed / approved /
   *  confirmed), the largest minimum, and whether any waits for approval. */
  min_spend_summary?: { count: number; largest_amount_cents: number | null; needs_approval: boolean } | null;
  /** List endpoint only: sender of the newest message on its linked threads. */
  last_email_from?: { name: string | null; email: string | null; at: string | null } | null;
}

export interface EnquiryKpis {
  open: number;
  needs_reply: number;
  follow_ups_due: number;
  open_value_cents: number;
}

export interface EnquiryList extends Page<EnquiryRow> {
  kpis: EnquiryKpis;
}

export interface Enquiry {
  id: string;
  version: number;
  contact_id: string;
  venue_id: string | null;
  booking_type: string | null;
  booking_label: string | null;
  subject: string | null;
  guests: number | null;
  preferred_date_start: string | null;
  preferred_date_end: string | null;
  preferred_start_time: string | null;
  preferred_end_time: string | null;
  preferred_ends_next_day: boolean;
  source: string | null;
  attribution: Record<string, string>;
  owner_user_id: string | null;
  follow_up_date: string | null;
  follow_up_reason: string | null;
  client_note: string | null;
  notes: string | null;
  archived_at: string | null;
  created_by_user_id: string | null;
  created_by_label: string | null;
  created_at: string;
  updated_at: string;
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

export interface MinSpendOption {
  id: string;
  version: number;
  enquiry_id: string | null;
  event_id: string | null;
  venue_id: string;
  area_id: string | null;
  area_name: string | null;
  area_requires_approval: boolean;
  label: string | null;
  amount_cents: number;
  food_cents: number;
  total_cents: number;
  event_date: string | null;
  start_time: string | null;
  end_time: string | null;
  ends_next_day: boolean;
  notes: string | null;
  sort_order: number;
  state: OptionState;
  confirmable: boolean;
  rights: { approve: boolean; override: boolean; revoke: boolean; lower: boolean; why: string | null };
  withdrawn_at: string | null;
  withdraw_reason: string | null;
  created_by: string | null;
  created_at: string;
  approvals: OptionApproval[];
  responses: OptionResponse[];
}

export interface SalesReference {
  venue_id: string;
  date: string | null;
  weekday: string | null;
  last_year_date: string | null;
  available: boolean;
  reason: string | null;
  budget_cents: number | null;
  last_year_cents: number | null;
  actual_cents: number | null;
  tax: string | null;
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

export interface PreorderTemplate {
  id: string;
  version: number;
  venue_id: string;
  name: string;
  description: string | null;
  is_set_menu: boolean;
  price_per_person_cents: number | null;
  active: boolean;
  items: unknown[];
}

export interface EventRow {
  id: string;
  version: number;
  name: string | null;
  enquiry_id: string | null;
  contact_id: string;
  contact_name: string | null;
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

export interface Closure {
  id: string;
  kind: 'lost' | 'reopened';
  reason: string | null;
  actor_label: string | null;
  actor_user_id: string | null;
  occurred_at: string;
}

export interface ProposalSummary {
  exists: boolean;
  id: string | null;
  version: number | null;
  updated_at: string | null;
  versions: number;
  last_sent_at: string | null;
  last_viewed_at: string | null;
  based_on_brand_version: number | null;
  brand_changed: boolean;
}

export interface PublicLink {
  id: string;
  kind: string;
  /** Null when the API can't show the link (only its hash is stored). */
  url: string | null;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface ThreadLink {
  link_id: string;
  account_id: string;
  mailbox: string;
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

export interface TimelineItem {
  at: string;
  kind: string;
  title: string;
  detail: unknown;
  actor: string | null;
  ref?: Record<string, string | null> | null;
}

export interface EnquiryDetail {
  enquiry: Enquiry;
  stage: Stage;
  next_action: NextAction | null;
  venue: { id: string; name: string; timezone: string | null } | null;
  owner_name: string | null;
  contact: Contact | null;
  contact_history: { enquiries: EnquiryRow[]; events: EventRow[] };
  options: MinSpendOption[];
  menus: Menu[];
  food_cents: number;
  value_cents: number | null;
  events: EventRow[];
  closures: Closure[];
  proposal: ProposalSummary;
  quote_links: PublicLink[];
  threads: ThreadLink[];
  timeline: TimelineItem[];
}

export interface ContactDetail extends Contact {
  enquiries: EnquiryRow[];
  events: EventRow[];
  counts: ContactCounts;
}

export interface VenueArea {
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

/** The parts of `GET /venues/{id}/profile` these pages use. */
export interface VenueProfile {
  venue: { id: string; name: string; timezone: string | null; day_start_time: string | null };
  settings: { approver_name: string | null; override_approver_name: string | null } | null;
  areas: VenueArea[];
}
