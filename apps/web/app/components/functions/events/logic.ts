/**
 * Norm Functions — Events page logic that has no React in it: plain-date
 * maths, list grouping and counts, section badges, the calendar's day
 * layout, and the small conversions the edit forms need.
 *
 * Event dates and times are venue-local PLANS ("2026-10-17", "19:30"). They
 * are never put through `new Date()` in the browser's zone: date arithmetic
 * here works on the calendar date alone (Date.UTC), and "today" is asked of
 * the company's time zone, so a laptop in another zone sees the same list.
 */
import type { BadgeTone } from '../../ui/Badge';
import { money, timeLabel } from '../shared/format';
import type { EventChange, EventDetail, EventRow, EventStatus, SectionKey } from './types';

// ── plain dates ────────────────────────────────────────────────────────────

/** Today's calendar date in an IANA zone, as "YYYY-MM-DD". */
export function todayIn(timeZone: string, now: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(now);
    const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
    return `${get('year')}-${get('month')}-${get('day')}`;
  } catch {
    return now.toISOString().slice(0, 10);
  }
}

function parts(iso: string): [number, number, number] {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return [y, m, d];
}

function fromUtc(dt: Date): string {
  return dt.toISOString().slice(0, 10);
}

/** "2026-10-30" + 3 → "2026-11-02" (calendar days; no zone involved). */
export function addDays(iso: string, n: number): string {
  const [y, m, d] = parts(iso);
  return fromUtc(new Date(Date.UTC(y, m - 1, d + n)));
}

/** Whole days from `a` to `b` (b − a). */
export function daysBetween(a: string, b: string): number {
  const [y1, m1, d1] = parts(a);
  const [y2, m2, d2] = parts(b);
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86_400_000);
}

/** "2026-10-17" → "2026-10". */
export function monthOf(iso: string): string {
  return iso.slice(0, 7);
}

/** "2026-12" + 1 → "2027-01". */
export function addMonths(month: string, n: number): string {
  const [y, m] = month.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1 + n, 1));
  return fromUtc(dt).slice(0, 7);
}

/** "2026-10" → "October 2026". */
export function monthLabel(month: string): string {
  const [y, m] = month.split('-').map(Number);
  if (!y || !m) return month;
  return new Date(Date.UTC(y, m - 1, 15)).toLocaleDateString('en-NZ', { timeZone: 'UTC', month: 'long', year: 'numeric' });
}

/** Day of the month from a plain date: "2026-10-07" → 7. */
export function dayOfMonth(iso: string): number {
  return parts(iso)[2];
}

// ── the list's date range ──────────────────────────────────────────────────

export type RangePreset = 'next7' | 'next14' | 'next42' | 'custom';

export const RANGE_PRESETS: { id: Exclude<RangePreset, 'custom'>; label: string; days: number }[] = [
  { id: 'next7', label: 'Next 7 days', days: 7 },
  { id: 'next14', label: 'Next 14 days', days: 14 },
  { id: 'next42', label: 'Next 6 weeks', days: 42 },
];

/** A preset's inclusive range starting today ("next 14 days" = today + 13). */
export function presetRange(preset: Exclude<RangePreset, 'custom'>, today: string): { from: string; to: string } {
  const days = RANGE_PRESETS.find((p) => p.id === preset)?.days ?? 14;
  return { from: today, to: addDays(today, days - 1) };
}

// ── statuses ───────────────────────────────────────────────────────────────

export const STATUS_ORDER: EventStatus[] = ['booked', 'sent_to_client', 'in_progress', 'confirmed', 'cancelled'];

export const STATUS_LABEL: Record<EventStatus, string> = {
  booked: 'Booked',
  sent_to_client: 'Sent to client',
  in_progress: 'In progress',
  confirmed: 'Confirmed',
  cancelled: 'Cancelled',
};

/** Badge tones (DESIGN-CONTRACT): waiting on the client = warn, done = ok. */
export const STATUS_TONE: Record<EventStatus, BadgeTone> = {
  booked: 'neutral',
  sent_to_client: 'info',
  in_progress: 'warn',
  confirmed: 'ok',
  cancelled: 'error',
};

export const EDITED_LABEL = 'Edited since confirmed';

/** A status tile's filter: one status, the "edited since confirmed" flag, or
 *  nothing (every event except cancelled ones). */
export type StatusFilter = EventStatus | 'edited' | null;

export type StatusCounts = Record<EventStatus | 'edited', number>;

export function statusCounts(rows: EventRow[]): StatusCounts {
  const counts: StatusCounts = { booked: 0, sent_to_client: 0, in_progress: 0, confirmed: 0, cancelled: 0, edited: 0 };
  for (const r of rows) {
    if (r.status in counts) counts[r.status] += 1;
    if (r.edited_since_confirmed && r.status !== 'cancelled') counts.edited += 1;
  }
  return counts;
}

export function filterByStatus(rows: EventRow[], filter: StatusFilter): EventRow[] {
  if (filter === null) return rows.filter((r) => r.status !== 'cancelled');
  if (filter === 'edited') return rows.filter((r) => r.edited_since_confirmed && r.status !== 'cancelled');
  return rows.filter((r) => r.status === filter);
}

// ── grouping and paging ────────────────────────────────────────────────────

export interface MonthGroup {
  month: string;
  label: string;
  rows: EventRow[];
  expectedCents: number;
}

/** Consecutive rows of the same month in one group (rows arrive date-sorted). */
export function groupByMonth(rows: EventRow[]): MonthGroup[] {
  const groups: MonthGroup[] = [];
  for (const r of rows) {
    const month = monthOf(r.event_date);
    let g = groups[groups.length - 1];
    if (!g || g.month !== month) {
      g = { month, label: monthLabel(month), rows: [], expectedCents: 0 };
      groups.push(g);
    }
    g.rows.push(r);
    if (r.status !== 'cancelled') g.expectedCents += r.expected_spend_cents || 0;
  }
  return groups;
}

export interface Page<T> {
  rows: T[];
  page: number;
  pages: number;
  /** 1-based positions for "21–40 of 108". */
  first: number;
  last: number;
  total: number;
}

export function pageOf<T>(rows: T[], page: number, size: number): Page<T> {
  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const p = Math.min(Math.max(0, page), pages - 1);
  const slice = rows.slice(p * size, p * size + size);
  return { rows: slice, page: p, pages, first: total ? p * size + 1 : 0, last: p * size + slice.length, total };
}

// ── calendar ───────────────────────────────────────────────────────────────

/** A day cell shows at most `slots` lines: every event when they fit, else
 *  `slots − 1` events and a "+N more" line in the last slot. */
export function splitDay<T>(events: T[], slots: number): { visible: T[]; more: number } {
  if (events.length <= slots) return { visible: events, more: 0 };
  const shown = Math.max(1, slots - 1);
  return { visible: events.slice(0, shown), more: events.length - shown };
}

/** Short money for tight spaces (phone calendar cells): "$4.2K". */
export function compactMoney(cents: number | null | undefined, currency = 'NZD'): string {
  if (cents === null || cents === undefined || Number.isNaN(cents)) return '';
  try {
    return new Intl.NumberFormat('en-NZ', {
      style: 'currency',
      currency,
      notation: 'compact',
      minimumFractionDigits: 0,
      maximumFractionDigits: 1,
    }).format(cents / 100);
  } catch {
    return money(cents, currency, 0);
  }
}

// ── section badges ─────────────────────────────────────────────────────────

export type SectionBadgeKind = 'not_sent' | 'sent' | 'client_edited' | 'confirmed' | 'skipped';

export const SECTION_BADGE: Record<SectionBadgeKind, { label: string; tone: BadgeTone }> = {
  not_sent: { label: 'Not sent', tone: 'neutral' },
  sent: { label: 'Sent', tone: 'info' },
  client_edited: { label: 'Client edited', tone: 'warn' },
  confirmed: { label: 'Confirmed', tone: 'ok' },
  skipped: { label: 'Skipped', tone: 'neutral' },
};

export const SECTION_LABEL: Record<SectionKey, string> = {
  details: 'Details',
  beverages: 'Beverages',
  food: 'Food',
};

/** Has the client been sent the portal link (by Norm or, imported, by Orbit)? */
export function linkSent(detail: Pick<EventDetail, 'changes' | 'portal_links'>): boolean {
  return detail.changes.some((c) => c.kind === 'link_sent') || detail.portal_links.some((l) => !l.revoked_at);
}

/** Where one portal section stands: skipped / confirmed facts first; then a
 *  client edit since the section was last reopened; then whether the link
 *  has gone out at all. */
export function sectionBadge(
  detail: Pick<EventDetail, 'sections' | 'changes' | 'portal_links'>,
  section: SectionKey,
): SectionBadgeKind {
  const state = detail.sections[section];
  if (state?.kind === 'skipped') return 'skipped';
  if (state?.kind === 'confirmed') return 'confirmed';
  const since = state?.kind === 'reopened' ? state.occurred_at ?? null : null;
  const edited = detail.changes.some(
    (c) =>
      c.kind === 'edited' &&
      c.actor_kind === 'client' &&
      (c.section === section || (section === 'details' && c.section === null)) &&
      (!since || c.occurred_at > since),
  );
  if (edited) return 'client_edited';
  return linkSent(detail) ? 'sent' : 'not_sent';
}

// ── field vocabularies ─────────────────────────────────────────────────────

/** Orbit's bar-tab restriction presets. */
export const RESTRICTION_PRESETS = [
  'Tap beers',
  'House bottled beers',
  'House wines',
  'House spirits (no shots)',
  'House spirits (including shots)',
  'Cocktails',
  'No restrictions',
];

/** `bar_tab_restrictions` is a free string: a JSON array of strings (most
 *  rows) reads as chips, anything else as one custom entry. */
export function parseRestrictions(raw: string | null | undefined): string[] {
  const text = (raw ?? '').trim();
  if (!text) return [];
  if (text.startsWith('[')) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (Array.isArray(parsed)) {
        return parsed.map((x) => String(x ?? '').trim()).filter(Boolean);
      }
    } catch {
      /* not JSON — fall through to one free-text entry */
    }
  }
  return [text];
}

/** Chips back to the stored string (a JSON array; null when empty). "No
 *  restrictions" excludes every other choice. */
export function serializeRestrictions(list: string[]): string | null {
  const clean = list.map((x) => x.trim()).filter((x, i, all) => x && all.findIndex((y) => y.toLowerCase() === x.toLowerCase()) === i);
  return clean.length ? JSON.stringify(clean) : null;
}

/** Add a restriction the way Orbit did: "No restrictions" replaces the rest,
 *  anything else replaces "No restrictions". Case-insensitive de-duplication. */
export function addRestriction(list: string[], value: string): string[] {
  const v = value.trim();
  if (!v) return list;
  const isNone = v.toLowerCase() === 'no restrictions';
  if (isNone) return [v];
  const rest = list.filter((x) => x.toLowerCase() !== 'no restrictions');
  return rest.some((x) => x.toLowerCase() === v.toLowerCase()) ? rest : [...rest, v];
}

export const PAYMENT_OPTIONS = [
  { value: 'paid_on_night', label: 'Paid on the night' },
  { value: 'to_be_invoiced', label: 'To be invoiced' },
  { value: 'pre_paid', label: 'Pre-paid' },
];

/** Imported rows also say `invoice` (Orbit's proposal default) for "to be invoiced". */
export function normalisePayment(value: string | null | undefined): string {
  if (!value) return '';
  return value === 'invoice' ? 'to_be_invoiced' : value;
}

export function paymentLabel(value: string | null | undefined): string {
  const v = normalisePayment(value);
  if (!v) return '';
  return PAYMENT_OPTIONS.find((o) => o.value === v)?.label ?? v;
}

export const DEPOSIT_STATUSES = [
  { value: 'not_invoiced', label: 'Not invoiced' },
  { value: 'invoiced', label: 'Invoiced' },
  { value: 'paid', label: 'Paid' },
];

export function depositStatusLabel(value: string | null | undefined): string {
  if (!value) return '';
  return DEPOSIT_STATUSES.find((o) => o.value === value)?.label ?? value;
}

export const ENTERTAINMENT_LABEL: Record<string, string> = { dj: 'DJ', playlist: 'Playlist' };

export const AV_LABEL: Record<string, string> = {
  in_house_mic: 'In-house mic',
  in_house_tv: 'In-house TVs',
  external_av: 'External A/V',
};

/** Which times a food timing type needs: a set time, and an end for staggered. */
export function foodTimingNeeds(type: string | null | undefined): { time: boolean; end: boolean } {
  return { time: type === 'all_at_once_time' || type === 'staggered_time', end: type === 'staggered_time' };
}

/** "Staggered from 7:30 pm to 8:00 pm" — Orbit's print wording. */
export function foodTimingText(
  type: string | null | undefined,
  time: string | null | undefined,
  end: string | null | undefined,
): string {
  switch (type) {
    case 'all_at_once_arrival':
      return 'All at once on arrival';
    case 'staggered_arrival':
      return 'Staggered from arrival';
    case 'all_at_once_time':
      return time ? `All at once at ${timeLabel(time)}` : 'All at once at a set time';
    case 'staggered_time':
      if (!time) return 'Staggered from a set time';
      return end ? `Staggered from ${timeLabel(time)} to ${timeLabel(end)}` : `Staggered from ${timeLabel(time)}`;
    default:
      return type ?? '';
  }
}

// ── times ──────────────────────────────────────────────────────────────────

/** "19:30:00" / "19:30" / "" → "19:30" / null. */
export function normaliseTime(t: string | null | undefined): string | null {
  if (!t) return null;
  const m = /^(\d{1,2}):(\d{2})/.exec(t.trim());
  if (!m) return null;
  return `${m[1].padStart(2, '0')}:${m[2]}`;
}

/** A window ends the next day when its end is at or before its start
 *  (19:00–01:00): nothing runs for 24 hours, so this is never ambiguous. */
export function endsNextDay(start: string | null | undefined, end: string | null | undefined): boolean {
  const s = normaliseTime(start);
  const e = normaliseTime(end);
  return !!(s && e && e <= s);
}

/** Is a run-sheet time after midnight? Only for an event that runs past
 *  midnight, and only for a time before the start that is also inside the
 *  after-midnight part (00:30 at a 19:00–01:00 event yes; a 17:00 set-up no). */
export function timingNextDay(
  at: string | null | undefined,
  eventStart: string | null | undefined,
  eventEnd: string | null | undefined,
  eventEndsNextDay: boolean,
): boolean {
  const t = normaliseTime(at);
  const s = normaliseTime(eventStart);
  const e = normaliseTime(eventEnd);
  if (!t || !s || !e || !eventEndsNextDay) return false;
  return t < s && t <= e;
}

// ── forms ──────────────────────────────────────────────────────────────────

/** Cents → what a money input shows: 120000 → "1200", 12345 → "123.45". */
export function centsToInput(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return '';
  return cents % 100 === 0 ? String(cents / 100) : (cents / 100).toFixed(2);
}

/** Blank text → null, so clearing a field clears it on the server. */
export function textOrNull(value: string | null | undefined): string | null {
  const t = (value ?? '').trim();
  return t ? t : null;
}

function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if ((a === null || a === undefined || a === '') && (b === null || b === undefined || b === '')) return true;
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Only the fields that changed — a PATCH sends nothing else. */
export function changedFields<T extends object>(original: T, draft: Partial<T>): Partial<T> {
  const out: Partial<T> = {};
  for (const key of Object.keys(draft) as (keyof T)[]) {
    if (!same(original[key], draft[key])) out[key] = draft[key];
  }
  return out;
}

/** Child rows without their ids, for comparing what a list save would send. */
export function stripIds<T extends { id?: string }>(rows: T[]): Omit<T, 'id'>[] {
  return rows.map((row) => {
    const copy = { ...row };
    delete copy.id;
    return copy;
  });
}

/** Move one item (reorder buttons). Out-of-range moves return the list as is. */
export function moveItem<T>(list: T[], from: number, to: number): T[] {
  if (from < 0 || from >= list.length || to < 0 || to >= list.length || from === to) return list;
  const next = list.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

/** "a@b.com, c@d.com" → ["a@b.com", "c@d.com"]. */
export function splitEmails(text: string): string[] {
  return text
    .split(/[,;\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Plain text a person typed → simple escaped HTML paragraphs. */
export function textToHtml(text: string): string {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  return text
    .trim()
    .split(/\n{2,}/)
    .map((para) => `<p>${esc(para).replace(/\n/g, '<br>')}</p>`)
    .join('');
}

// ── the changes log ────────────────────────────────────────────────────────

const FIELD_LABELS: Record<string, string> = {
  name: 'Event name',
  contact_id: 'Contact',
  invoice_contact_id: 'Invoice contact',
  booking_type: 'Booking type',
  area_id: 'Area',
  event_date: 'Date',
  start_time: 'Start',
  end_time: 'Finish',
  ends_next_day: 'Ends after midnight',
  guests: 'Guests',
  description: 'Description',
  notes: 'Notes',
  special_instructions: 'Special instructions',
  payment_method: 'Payment',
  deposit_cents: 'Deposit',
  deposit_status: 'Deposit status',
  table_plan: 'Table plan',
  table_plan_file_id: 'Table plan image',
  arrival_time: 'Arrival',
  seated_time: 'Seated',
  timing_preference: 'Timing preference',
  timing_notes: 'Timing notes',
  bar_tab_cents: 'Bar tab',
  bar_tab_authorised_people: 'Authorised people',
  bar_tab_restrictions: 'Restrictions',
  bar_tab_notes: 'Beverage notes',
  arrival_drinks: 'Arrival drinks',
  table_ordering: 'Table ordering',
  table_ordering_limit_cents: 'Table ordering limit',
  allergies: 'Allergies',
  food_timing_type: 'Food timing',
  food_time: 'Food time',
  food_end_time: 'Food until',
  food_notes: 'Food notes',
  timings: 'Run sheet',
  security: 'Security',
  entertainment: 'Entertainment',
  av: 'A/V',
  menus: 'Menus',
  'contact.name': 'Contact name',
  'contact.phone': 'Contact phone',
  'contact.company': 'Company',
};

export function changeFieldLabel(key: string): string {
  if (FIELD_LABELS[key]) return FIELD_LABELS[key];
  if (key.startsWith('menu.')) {
    const rest = key.slice(5);
    return FIELD_LABELS[rest] ? `Menu ${FIELD_LABELS[rest].toLowerCase()}` : `Menu ${rest.replace(/_/g, ' ')}`;
  }
  // Quantity changes read "<menu>: <dish>" already.
  if (/[\s:]/.test(key)) return key;
  const words = key.replace(/_cents$/, '').replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** One side of a change, for people: money, times, yes/no, list sizes. */
export function changeValueText(key: string, value: unknown, currency = 'NZD'): string {
  if (value === null || value === undefined || value === '') return '—';
  if (key.endsWith('_id')) return 'set';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (key.endsWith('_cents') && typeof value === 'number') return money(value, currency);
  if (key.endsWith('_time') && typeof value === 'string') return timeLabel(value) || value;
  if (key === 'bar_tab_restrictions' && typeof value === 'string') return parseRestrictions(value).join(', ') || '—';
  if (key === 'payment_method' && typeof value === 'string') return paymentLabel(value);
  if (Array.isArray(value)) return `${value.length} ${value.length === 1 ? 'row' : 'rows'}`;
  if (typeof value === 'object') {
    const v = value as Record<string, unknown>;
    if (typeof v.added === 'string') return `added ${v.added}`;
    if (typeof v.removed === 'string') return `removed ${v.removed}`;
    return 'changed';
  }
  const text = String(value);
  return text.length > 80 ? `${text.slice(0, 77)}…` : text;
}

/** The timeline's detail lists changed fields as keys ("bar_tab_cents,
 *  bar_tab_restrictions — the note"): show them as labels, keep the rest. */
export function humaniseDetail(detail: string | null | undefined): string {
  if (!detail) return '';
  const [head, ...rest] = detail.split(' — ');
  const keys = head.split(',').map((k) => k.trim());
  if (!keys.length || !keys.every((k) => /^[a-z][a-z0-9_.]*$/.test(k))) return detail;
  return [keys.map(changeFieldLabel).join(', '), ...rest].join(' — ');
}

/** The changes rows newest first (the API lists them oldest first). */
export function newestFirst(changes: EventChange[]): EventChange[] {
  return changes.slice().sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : a.occurred_at > b.occurred_at ? -1 : 0));
}
