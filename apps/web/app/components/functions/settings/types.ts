/**
 * Norm Functions Settings — the API shapes this page reads and writes
 * (scratchpad F3-api.md §10–12 venue profile / templates / uploads, F5-api.md
 * §6 form builder, F4-api.md mailboxes). Kept to what the page uses.
 */

export interface AreaPhoto {
  id: string;
  area_id: string;
  file_id: string;
  caption: string | null;
  sort_order: number;
}

export interface Area {
  id: string;
  version: number;
  venue_id: string;
  name: string;
  description: string | null;
  capacity_seated: number | null;
  capacity_standing: number | null;
  /** Empty = every booking type. */
  booking_types: string[];
  requires_approval: boolean;
  min_spend_notes: string | null;
  active: boolean;
  sort_order: number;
  photos?: AreaPhoto[];
}

export interface MenuLink {
  label: string;
  url: string;
  kind: string | null;
  description: string | null;
  active: boolean;
  sort: number;
}

/** enabled_fields[booking_type][field_key]; a key not set is ON. */
export type EnabledFields = Record<string, Record<string, boolean>>;

export interface VenueSettings {
  id: string;
  version: number;
  venue_id: string;
  approver_user_id: string | null;
  approver_name: string | null;
  override_approver_user_id: string | null;
  override_approver_name: string | null;
  policies: string | null;
  menu_links: MenuLink[];
  /** Stored overrides only. */
  enabled_fields: EnabledFields;
  notify_emails: string[];
}

export type BrandColourKey = 'primary' | 'accent' | 'panel' | 'text' | 'background';
export type BrandColours = Partial<Record<BrandColourKey, string>>;

/** A term is a line, or an Orbit-style `{title, body}` section. */
export type BrandTerm = string | { title: string | null; body: string };

export interface Brand {
  id: string;
  version: number;
  venue_id: string;
  logo_file_id: string | null;
  cover_file_id: string | null;
  closing_file_id: string | null;
  wordmark: string | null;
  tagline: string | null;
  colours: BrandColours | null;
  fonts: { heading?: string; body?: string } | null;
  terms: BrandTerm[];
  inclusions: string[];
  signoff: { title?: string; body?: string; contact?: string } | null;
  style: Record<string, unknown> | null;
  footer_contact: string | null;
  valid_days: number | null;
  template_html: string | null;
  template_css: string | null;
}

export interface LibraryImage {
  id: string;
  version: number;
  venue_id: string;
  file_id: string;
  name: string;
  caption: string | null;
  tags: string[];
  created_at: string | null;
}

export interface VenueProfile {
  venue: { id: string; name: string; timezone: string | null; day_start_time: string | null };
  settings: VenueSettings | null;
  /** Every catalogue key, per booking type (missing settings mean on). */
  enabled_fields: EnabledFields;
  /** May this caller change approvers / an area's approval flag. */
  can_govern_approvals: boolean;
  areas: Area[];
  brand: Brand | null;
  images: LibraryImage[];
}

export interface FnFile {
  id: string;
  sha256: string;
  filename: string | null;
  content_type: string;
  size_bytes: number;
  width: number | null;
  height: number | null;
  created_at: string | null;
}

export interface TemplateItem {
  id?: string;
  kind: 'header' | 'item';
  name: string;
  description: string | null;
  unit_price_cents: number | null;
  default_percent: number | null;
  dietaries: string[];
  loaded_recipe_id: string | null;
  loaded_stock_item_id: string | null;
}

export interface MenuTemplate {
  id: string;
  version: number;
  venue_id: string;
  name: string;
  description: string | null;
  is_set_menu: boolean;
  price_per_person_cents: number | null;
  loaded_menu_id: string | null;
  active: boolean;
  sort_order: number;
  items: TemplateItem[];
  warnings: string[];
}

export type ReplyStage = 'first_reply' | 'follow_up';

export interface ReplyTemplate {
  id: string;
  version: number;
  /** null = every venue (owner-only). */
  venue_id: string | null;
  /** null = any booking type. */
  booking_type: string | null;
  stage: ReplyStage;
  name: string;
  subject: string | null;
  body_html: string;
  active: boolean;
  sort_order: number;
}

export type FormFieldType =
  | 'name'
  | 'email'
  | 'phone'
  | 'text'
  | 'long_text'
  | 'number'
  | 'date'
  | 'dates'
  | 'time_range'
  | 'choice'
  | 'checkbox'
  | 'section';

export interface ChoiceOption {
  value: string;
  label: string;
}

export interface FormField {
  id?: string;
  key: string;
  label: string;
  type: FormFieldType;
  required: boolean;
  /** choice/checkbox: options; number: {min, max}; otherwise null. */
  options: ChoiceOption[] | { min?: number; max?: number } | null;
  placeholder: string | null;
  help_text: string | null;
  maps_to: string | null;
  sort_order?: number;
}

export interface WebForm {
  id: string;
  version: number;
  venue_id: string;
  venue_name?: string | null;
  slug: string;
  name: string;
  active: boolean;
  theme: Record<string, string | number> | null;
  intro_html: string | null;
  submit_label: string | null;
  success_message: string | null;
  recipient_email: string | null;
  subject_template: string | null;
  reply_to_field_key: string | null;
  honeypot_field: string | null;
  booking_type: string | null;
  embed: { url: string; iframe: string; script: string; tracker: string };
  created_at: string | null;
  updated_at: string | null;
  fields?: FormField[];
  submissions?: number;
  last_submitted_at?: string | null;
}

export interface FormSubmission {
  id: string;
  form_id: string;
  venue_id: string;
  payload: Record<string, unknown>;
  submitted_at: string | null;
  ref_code: string | null;
  attribution: Record<string, string>;
  spam_reason: string | null;
  delivery_status: 'pending' | 'sent' | 'failed' | 'skipped' | null;
  delivery_error: string | null;
  enquiry_id: string | null;
}

export interface MailboxHealth {
  state: 'connected' | 'syncing' | 'needs_reconnect' | 'not_connected' | 'disabled';
  connected: boolean;
  last_ok_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  lag_seconds: number | null;
  lagging: boolean;
}

export interface Mailbox {
  id: string;
  venue_id: string;
  venue_name: string | null;
  google_email: string;
  display_name: string | null;
  status: 'active' | 'resyncing' | 'needs_reconnect' | 'disabled';
  version: number;
  health: MailboxHealth;
  /** Which "Actioned - …" labels exist in the mailbox. */
  folders: Record<string, boolean>;
  has_signature: boolean;
}

export interface MailboxList {
  venues: { venue_id: string; venue_name: string; mailbox_ids: string[] }[];
  mailboxes: Mailbox[];
}
