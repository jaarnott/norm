/**
 * Norm Functions — words and badge tones for the server's derived states, and
 * the small display rules the Dashboard, Enquiries, Contacts and Reports pages
 * share. Pure functions (vitest covers them); nothing here computes a stage,
 * a next action or a total — those come from the API.
 */
import type { BadgeTone } from '../../ui/Badge';
import { dateLabel, money } from '../shared/format';
import type { EnquiryRow, EventStatus, MinSpendOption, NextAction, Stage } from './types';

export const OPEN_STAGES: Stage[] = ['new', 'replied', 'quoted'];

// Orbit's colours, which staff know: new grey, replied amber, quoted blue,
// confirmed green, lost red.
const STAGES: Record<Stage, { label: string; tone: BadgeTone }> = {
  new: { label: 'New', tone: 'neutral' },
  replied: { label: 'Replied', tone: 'warn' },
  quoted: { label: 'Quoted', tone: 'info' },
  confirmed: { label: 'Confirmed', tone: 'ok' },
  lost: { label: 'Lost', tone: 'error' },
};

export function stageLabel(stage: string | null | undefined): string {
  return STAGES[stage as Stage]?.label ?? (stage ? sentence(stage) : '—');
}

export function stageTone(stage: string | null | undefined): BadgeTone {
  return STAGES[stage as Stage]?.tone ?? 'neutral';
}

/** The due badge beside a next action: null when nothing is due. */
export function dueBadge(action: Pick<NextAction, 'kind' | 'due_date' | 'overdue' | 'due_today'> | null | undefined):
  { label: string; tone: BadgeTone } | null {
  if (!action || action.kind === 'none' || !action.due_date) return null;
  const day = dateLabel(action.due_date, { weekday: false, year: false });
  if (action.overdue) return { label: `Overdue · ${day}`, tone: 'error' };
  if (action.due_today) return { label: 'Due today', tone: 'warn' };
  return { label: `Due ${day}`, tone: 'neutral' };
}

/** An option's state badge. A proposed option in an area that needs approval
 *  is the one a manager must act on ("Needs approval"). */
export function optionBadge(option: Pick<MinSpendOption, 'state' | 'area_requires_approval'>): { label: string; tone: BadgeTone } {
  switch (option.state) {
    case 'proposed':
      return option.area_requires_approval ? { label: 'Needs approval', tone: 'warn' } : { label: 'Proposed', tone: 'info' };
    case 'approved':
      return { label: 'Approved', tone: 'ok' };
    case 'confirmed':
      return { label: 'Confirmed', tone: 'ok' };
    case 'declined':
      return { label: 'Declined', tone: 'error' };
    case 'withdrawn':
      return { label: 'Withdrawn', tone: 'neutral' };
    default:
      return { label: sentence(String(option.state)), tone: 'neutral' };
  }
}

/** Options still on offer (the ones a customer could still say yes to). */
export function isLiveOption(option: Pick<MinSpendOption, 'state'>): boolean {
  return option.state === 'proposed' || option.state === 'approved';
}

const EVENT_STATUSES: Record<EventStatus, { label: string; tone: BadgeTone }> = {
  booked: { label: 'Booked', tone: 'info' },
  sent_to_client: { label: 'Sent to client', tone: 'warn' },
  in_progress: { label: 'Client editing', tone: 'warn' },
  confirmed: { label: 'Confirmed', tone: 'ok' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
};

export function eventStatusBadge(status: string | null | undefined): { label: string; tone: BadgeTone } {
  return EVENT_STATUSES[status as EventStatus] ?? { label: status ? sentence(status) : '—', tone: 'neutral' };
}

const SOURCES: Record<string, string> = {
  email: 'Email',
  web_form: 'Web form',
  phone: 'Phone',
  walk_in: 'Walk-in',
  manual: 'Added by staff',
  agent: 'Norm',
  import: 'Imported',
  other: 'Other',
};

export function sourceLabel(source: string | null | undefined): string {
  if (!source) return '—';
  return SOURCES[source] ?? source;
}

const CHANNELS: Record<string, string> = {
  email: 'Email',
  phone: 'Phone',
  in_person: 'In person',
  quote_page: 'Quote page',
  import: 'Imported',
};

export function channelLabel(channel: string | null | undefined): string {
  if (!channel) return '—';
  return CHANNELS[channel] ?? sentence(channel);
}

/** "seated_dining" → "Seated dining" when meta has no label for it. */
export function sentence(value: string): string {
  const words = value.replace(/[_-]+/g, ' ').trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Whole dollars when there are no cents ("$14,000"), cents otherwise ("$82.50"). */
export function moneyAuto(cents: number | null | undefined, currency = 'NZD'): string {
  if (cents === null || cents === undefined || Number.isNaN(cents)) return '—';
  return money(cents, currency, cents % 100 === 0 ? 0 : 2);
}

/** "42%" or "—" when there is no rate (nothing closed yet). */
export function pctLabel(pct: number | null | undefined): string {
  return pct === null || pct === undefined ? '—' : `${pct}%`;
}

/** A plain preferred-date range: "Fri 20 Nov 2026", or "Fri 20 Nov – Sun 22 Nov 2026". */
export function dateRangeLabel(start: string | null | undefined, end: string | null | undefined): string {
  if (!start && !end) return '—';
  if (!start || !end || start === end) return dateLabel(start || end);
  const sameYear = start.slice(0, 4) === end.slice(0, 4);
  return `${dateLabel(start, { year: !sameYear })} – ${dateLabel(end)}`;
}

/** A UTC instant as a short day in a zone: "13 Oct" (with the year when it
 *  isn't `nowYear`). */
export function dayOf(iso: string | null | undefined, timeZone: string, nowYear = new Date().getFullYear()): string {
  if (!iso) return '—';
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  const year = Number(dt.toLocaleString('en-NZ', { timeZone, year: 'numeric' }));
  return dt.toLocaleDateString('en-NZ', {
    timeZone,
    day: 'numeric',
    month: 'short',
    year: year === nowYear ? undefined : 'numeric',
  });
}

/** The list's "Last email": who sent the newest message on the linked threads
 *  (the API's `last_email_from`), else the later of the customer's last email
 *  and our last reply. */
export function lastEmailOf(row: Pick<EnquiryRow, 'last_inbound_at' | 'last_outbound_at' | 'last_email_from'>):
  { at: string; inbound: boolean | null; from: string | null } | null {
  const sender = row.last_email_from;
  if (sender?.at) return { at: sender.at, inbound: null, from: sender.name || sender.email || null };
  const inbound = row.last_inbound_at ? Date.parse(row.last_inbound_at) : NaN;
  const outbound = row.last_outbound_at ? Date.parse(row.last_outbound_at) : NaN;
  if (Number.isNaN(inbound) && Number.isNaN(outbound)) return null;
  if (Number.isNaN(outbound) || (!Number.isNaN(inbound) && inbound >= outbound)) {
    return { at: row.last_inbound_at as string, inbound: true, from: null };
  }
  return { at: row.last_outbound_at as string, inbound: false, from: null };
}

const NAMED_ENTITIES: Record<string, string> = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', ndash: '–', mdash: '—', hellip: '…',
  rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“',
};

/** Gmail snippets arrive HTML-escaped ("It&#39;s"). Decode for display as
 *  TEXT — never rendered as HTML. */
export function decodeEntities(text: string | null | undefined): string {
  if (!text) return '';
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, body: string) => {
    if (body[0] === '#') {
      const code = body[1] === 'x' || body[1] === 'X' ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
    }
    return NAMED_ENTITIES[body.toLowerCase()] ?? whole;
  });
}

/** A timeline item's detail as one line of text (the API sends a string, or
 *  occasionally a small object of fields). */
export function detailText(detail: unknown): string {
  if (detail === null || detail === undefined) return '';
  if (typeof detail === 'string') return decodeEntities(detail);
  if (typeof detail === 'number' || typeof detail === 'boolean') return String(detail);
  if (Array.isArray(detail)) return detail.map(detailText).filter(Boolean).join(', ');
  if (typeof detail === 'object') {
    return Object.entries(detail as Record<string, unknown>)
      .filter(([, v]) => v !== null && v !== undefined && v !== '')
      .map(([k, v]) => `${sentence(k)}: ${detailText(v)}`)
      .join(' · ');
  }
  return '';
}

/** A contact's name for a heading, with a fallback when it is blank (an
 *  anonymised contact has no name). */
export function displayName(name: string | null | undefined, fallback = 'Unnamed contact'): string {
  const n = (name || '').trim();
  return n || fallback;
}
