/**
 * Norm Functions — what the client portal calls each event field, and the
 * fixed choices behind some of them. Customer wording: "Drinks", not
 * "beverages"; "Guests arrive", not "arrival_time". The keys and values are
 * the API's (services/functions/events.py, functions_models.py).
 */
import type { EventStatus, ListKind, SectionKey } from './types';

export const SECTION_LABELS: Record<SectionKey, string> = {
  details: 'Details',
  beverages: 'Drinks',
  food: 'Food',
};

export const SECTIONS: SectionKey[] = ['details', 'beverages', 'food'];

export const STATUS_LABELS: Record<EventStatus, string> = {
  booked: 'Booked',
  sent_to_client: 'Waiting for your details',
  in_progress: 'In progress',
  confirmed: 'Confirmed',
  cancelled: 'Cancelled',
};

/** How each client-editable field is edited (and converted for the API). */
export type FieldKind = 'int' | 'text' | 'time' | 'bool' | 'money' | 'choice';

export const FIELD_KINDS: Record<string, FieldKind> = {
  guests: 'int',
  notes: 'text',
  special_instructions: 'text',
  timing_preference: 'text',
  timing_notes: 'text',
  bar_tab_authorised_people: 'text',
  bar_tab_restrictions: 'text',
  bar_tab_notes: 'text',
  arrival_drinks: 'text',
  allergies: 'text',
  food_notes: 'text',
  arrival_time: 'time',
  seated_time: 'time',
  food_time: 'time',
  food_end_time: 'time',
  table_ordering: 'bool',
  table_ordering_limit_cents: 'money',
  food_timing_type: 'choice',
};

export const FIELD_LABELS: Record<string, string> = {
  guests: 'Guests',
  special_instructions: 'Special requests',
  notes: 'Notes for the venue',
  arrival_time: 'Guests arrive',
  seated_time: 'Seated at',
  timing_preference: 'Timing preferences',
  timing_notes: 'Timing notes',
  bar_tab_authorised_people: 'Who can add to the bar tab',
  bar_tab_restrictions: 'Drinks on the tab',
  bar_tab_notes: 'Drinks notes',
  arrival_drinks: 'Arrival drinks',
  table_ordering: 'Table ordering',
  table_ordering_limit_cents: 'Table ordering limit',
  allergies: 'Allergies and dietary requirements',
  food_timing_type: 'When should food come out?',
  food_time: 'Food starts at',
  food_end_time: 'Food finishes by',
  food_notes: 'Food notes',
  payment_method: 'Payment',
  deposit_cents: 'Deposit',
  bar_tab_cents: 'Bar tab',
  'contact.name': 'Your name',
  'contact.phone': 'Phone',
  'contact.company': 'Company',
};

export function fieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? key.replace(/_cents$/, '').replace(/_/g, ' ');
}

export const PAYMENT_LABELS: Record<string, string> = {
  paid_on_night: 'Paid on the night',
  invoice: 'Invoiced',
  to_be_invoiced: 'To be invoiced',
  pre_paid: 'Paid in advance',
};

export const DEPOSIT_LABELS: Record<string, string> = {
  not_invoiced: 'Not invoiced yet',
  invoiced: 'Invoiced',
  paid: 'Paid',
};

export const FOOD_TIMING: { value: string; label: string; detail: string; time: boolean; endTime: boolean }[] = [
  { value: 'all_at_once_arrival', label: 'All at once, on arrival', detail: 'Everything comes out together as guests arrive.', time: false, endTime: false },
  { value: 'staggered_arrival', label: 'Staggered, from arrival', detail: 'Food comes out in rounds once guests arrive.', time: false, endTime: true },
  { value: 'all_at_once_time', label: 'All at once, at a set time', detail: 'Everything comes out together at a time you choose.', time: true, endTime: false },
  { value: 'staggered_time', label: 'Staggered, from a set time', detail: 'Food comes out in rounds from a time you choose.', time: true, endTime: true },
];

export function foodTimingLabel(value: unknown): string | null {
  return FOOD_TIMING.find((o) => o.value === value)?.label ?? null;
}

/** Orbit's presets for what the bar tab covers (stored as a JSON list of strings). */
export const RESTRICTION_PRESETS = [
  'Tap beers',
  'House bottled beers',
  'House wines',
  'House spirits (no shots)',
  'House spirits (including shots)',
  'Cocktails',
  'No restrictions',
];
const NO_RESTRICTIONS = 'no restrictions';

/**
 * `bar_tab_restrictions` is free text: most rows hold a JSON list of strings
 * (Orbit's chips), the rest a sentence ("cash bar"). A list reads as chips;
 * anything else as one entry. Written back as a JSON list, or null when
 * empty (the Events page does the same).
 */
export function parseRestrictions(value: unknown): string[] {
  if (typeof value !== 'string' || !value.trim()) return [];
  const text = value.trim();
  if (text.startsWith('[')) {
    try {
      const list = JSON.parse(text);
      if (Array.isArray(list)) return list.filter((x): x is string => typeof x === 'string' && !!x.trim()).map((x) => x.trim());
    } catch {
      /* not a list: one free-text entry */
    }
  }
  return [text];
}

export function writeRestrictions(list: string[]): string | null {
  const clean = list.map((x) => x.trim()).filter((x, i, all) => x && all.findIndex((y) => y.toLowerCase() === x.toLowerCase()) === i);
  return clean.length ? JSON.stringify(clean) : null;
}

/** Toggle one restriction; "No restrictions" excludes the rest and vice versa. */
export function toggleRestriction(list: string[], item: string): string[] {
  const has = list.some((x) => x.toLowerCase() === item.toLowerCase());
  if (has) return list.filter((x) => x.toLowerCase() !== item.toLowerCase());
  if (item.toLowerCase() === NO_RESTRICTIONS) return [item];
  return [...list.filter((x) => x.toLowerCase() !== NO_RESTRICTIONS), item];
}

export const ENTERTAINMENT_LABELS: Record<string, string> = {
  dj: 'DJ',
  playlist: 'Your own playlist',
};

export const AV_LABELS: Record<string, { label: string; detail: string }> = {
  in_house_mic: { label: 'Microphone', detail: "The venue's microphone, for speeches." },
  in_house_tv: { label: 'Screens', detail: "The venue's TVs, for a slideshow or presentation." },
  external_av: { label: 'Your own A/V', detail: 'You are bringing your own A/V supplier.' },
};

export const LIST_LABELS: Record<ListKind, string> = {
  timings: 'Run sheet',
  security: 'Security',
  entertainment: 'Entertainment',
  av: 'A/V',
};

/** Fields shown on each tab (read-only ones included), in display order. */
export const SECTION_FIELDS: Record<SectionKey, string[]> = {
  details: ['guests', 'special_instructions', 'notes', 'arrival_time', 'seated_time', 'timing_preference', 'timing_notes'],
  beverages: ['bar_tab_cents', 'bar_tab_authorised_people', 'bar_tab_restrictions', 'arrival_drinks', 'table_ordering', 'table_ordering_limit_cents', 'bar_tab_notes'],
  food: ['food_timing_type', 'food_time', 'food_end_time', 'allergies', 'food_notes'],
};
