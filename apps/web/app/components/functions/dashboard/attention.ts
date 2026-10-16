/**
 * The dashboard's "needs attention" list: which group each item belongs to,
 * where it opens, and how it is badged. The list itself — what's due and in
 * what order — is the API's (reports.needs_attention).
 */
import type { BadgeTone } from '../../ui/Badge';
import { dateLabel } from '../shared/format';
import type { FnPage } from '../enquiries/nav';
import type { AttentionItem } from '../reports/types';

export type AttentionGroup = 'replies' | 'follow_ups' | 'events' | 'quotes';

export const GROUP_LABELS: Record<AttentionGroup, string> = {
  replies: 'Replies owed',
  follow_ups: 'Follow-ups',
  events: 'Events',
  quotes: 'Quote responses',
};

export function attentionGroup(kind: string): AttentionGroup {
  if (kind === 'reply_overdue') return 'replies';
  if (kind === 'unconfirmed_event' || kind === 'client_edit') return 'events';
  if (kind === 'quote_response') return 'quotes';
  return 'follow_ups';
}

/** Counts per group, in display order, leaving out empty groups. */
export function groupCounts(items: AttentionItem[]): { group: AttentionGroup; count: number }[] {
  const order: AttentionGroup[] = ['replies', 'follow_ups', 'events', 'quotes'];
  const counts = new Map<AttentionGroup, number>();
  for (const it of items) counts.set(attentionGroup(it.kind), (counts.get(attentionGroup(it.kind)) ?? 0) + 1);
  return order.filter((g) => counts.get(g)).map((g) => ({ group: g, count: counts.get(g) as number }));
}

/** The importer's review follow-ups: Orbit had these confirmed with no
 *  booking behind them (their follow-up reason says so). */
export function isOrbitReview(item: Pick<AttentionItem, 'kind' | 'follow_up_reason'>): boolean {
  return item.kind === 'requested' && !!item.follow_up_reason && /orbit marked this confirmed/i.test(item.follow_up_reason);
}

/** The line under an item: the API's detail, else the enquiry's follow-up
 *  reason (why someone asked to be reminded). */
export function attentionNote(item: Pick<AttentionItem, 'detail' | 'follow_up_reason'>): string | null {
  return item.detail || item.follow_up_reason || null;
}

/** The page an item opens: events for event items (and a new booking from the
 *  quote page), otherwise its enquiry. */
export function attentionTarget(item: Pick<AttentionItem, 'kind' | 'enquiry_id' | 'event_id'>):
  { page: FnPage; params: Record<string, string> } | null {
  const eventKinds = item.kind === 'unconfirmed_event' || item.kind === 'client_edit' || item.kind === 'quote_response';
  if (item.event_id && eventKinds) return { page: 'events', params: { event: item.event_id } };
  if (item.enquiry_id) return { page: 'enquiries', params: { enquiry: item.enquiry_id } };
  if (item.event_id) return { page: 'events', params: { event: item.event_id } };
  return null;
}

/** The kind badge: overdue reads red; a customer's answer or edit is "needs
 *  you"; the rest are waiting on us. */
export function attentionBadge(item: Pick<AttentionItem, 'kind' | 'label' | 'follow_up_reason' | 'overdue'>): { label: string; tone: BadgeTone } {
  const label = isOrbitReview(item) ? 'Review: Orbit said confirmed' : item.label || 'Needs attention';
  if (item.overdue) return { label, tone: 'error' };
  if (item.kind === 'quote_response' || item.kind === 'client_edit') return { label, tone: 'accent' };
  return { label, tone: 'warn' };
}

/** The date beside an item: when it was due (enquiries) or the event date. */
export function attentionWhen(item: Pick<AttentionItem, 'kind' | 'date' | 'overdue'>, today: string | null): string {
  if (!item.date) return '';
  const group = attentionGroup(item.kind);
  if (group === 'events') return dateLabel(item.date, { year: false });
  if (group === 'quotes') return `Answered ${dateLabel(item.date, { weekday: false, year: false })}`;
  if (today && item.date === today) return 'Due today';
  return `${item.overdue ? 'Overdue since' : 'Due'} ${dateLabel(item.date, { weekday: false, year: false })}`;
}
