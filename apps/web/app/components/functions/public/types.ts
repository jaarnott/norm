/**
 * Norm Functions — shapes of the customer-facing API (routers/public.py;
 * scratchpad F5-api.md §3–4). Plans (dates, times) are venue-local strings
 * and are never passed through `new Date()`; money is integer cents.
 */

export interface PublicBrand {
  logo_url: string | null;
  wordmark: string | null;
  tagline: string | null;
  colours: Record<string, string> | null;
  fonts: Record<string, string> | null;
  footer_contact: string | null;
}

export interface PublicVenue {
  id: string;
  name: string;
  brand: PublicBrand | null;
}

export interface Money {
  currency: string;
  tax_label: string;
}

// ── Quote page ──────────────────────────────────────────────────────────

export type OptionState = 'available' | 'unavailable' | 'confirmed' | 'declined' | 'withdrawn';

export interface QuoteOption {
  id: string;
  label: string | null;
  venue_name: string | null;
  area_name: string | null;
  event_date: string | null;
  start_time: string | null;
  end_time: string | null;
  ends_next_day: boolean;
  amount_cents: number | null;
  food_cents: number | null;
  total_cents: number | null;
  amount_text: string | null;
  total_text: string | null;
  state: OptionState;
  changed_since_sent: boolean;
  can_confirm: boolean;
  can_decline: boolean;
}

export interface QuotePayload {
  enquiry: {
    subject: string | null;
    guests: number | null;
    booking_type: string | null;
    booking_label: string | null;
    contact_first_name: string | null;
    open: boolean;
  };
  venue: PublicVenue | null;
  proposal: {
    version_number: number;
    sent_at: string | null;
    valid_until: string | null;
    document: string;
  } | null;
  options: QuoteOption[];
  booked: boolean;
  money: Money;
}

// ── Client portal ───────────────────────────────────────────────────────

export type SectionKey = 'details' | 'beverages' | 'food';
export type ListKind = 'timings' | 'security' | 'entertainment' | 'av';
export type EventStatus = 'booked' | 'sent_to_client' | 'in_progress' | 'confirmed' | 'cancelled';

export interface SectionState {
  state: 'confirmed' | 'skipped' | 'reopened' | null;
  at: string | null;
  by_client: boolean;
}

export interface MenuItem {
  id: string;
  kind: 'header' | 'item';
  name: string;
  description: string | null;
  quantity: number;
  unit_price_cents: number | null;
  dietaries: string[];
}

export interface PortalMenu {
  id: string;
  version: number;
  name: string;
  is_set_menu: boolean;
  price_per_person_cents: number | null;
  guest_count: number | null;
  value_cents: number;
  items: MenuItem[];
}

/** One run-sheet style row; which keys exist depends on the list. */
export type ListRow = Record<string, string | boolean | null>;

export interface PortalEvent {
  id: string;
  version: number;
  name: string;
  booking_type: string | null;
  booking_label: string | null;
  event_date: string;
  start_time: string | null;
  end_time: string | null;
  ends_next_day: boolean;
  guests: number | null;
  special_instructions: string | null;
  area_name?: string | null;
  /** Further fields appear only when the venue shows them for this booking type. */
  [field: string]: unknown;
}

export interface PortalPayload {
  authenticated: true;
  email: string;
  event: PortalEvent;
  status: {
    status: EventStatus;
    edited_since_confirmed: boolean;
    sections: Record<SectionKey, SectionState>;
  };
  editable: string[];
  fields: Record<string, boolean>;
  lists: Partial<Record<ListKind, ListRow[]>>;
  list_kinds: Partial<Record<'entertainment' | 'av', string[]>>;
  menus: PortalMenu[];
  min_spend: { amount_cents: number; amount_text: string; label: string | null } | null;
  contact: { name: string | null; phone: string | null; company: string | null; email: string | null };
  people: { email: string; verified: boolean; invited_by_email: string | null }[];
  venue: PublicVenue & {
    policies?: string | null;
    menu_links?: { label: string | null; url: string | null; kind: string | null; description: string | null }[];
  };
  money: Money;
}

export interface PortalSignedOut {
  authenticated: false;
  venue: PublicVenue | null;
}
