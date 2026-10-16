import { describe, expect, it } from 'vitest';
import { attentionBadge, attentionGroup, attentionNote, attentionTarget, attentionWhen, groupCounts, isOrbitReview } from './attention';
import type { AttentionItem } from '../reports/types';

const item = (over: Partial<AttentionItem>): AttentionItem => ({
  kind: 'requested', label: 'Requested follow-up', enquiry_id: 'e1', event_id: null, title: 'Party', contact_name: 'Sam',
  venue_name: 'La Zeppa', date: '2026-10-12', overdue: true, detail: null, follow_up_reason: null, ...over,
});

describe('needs attention', () => {
  it('groups items the way the chips do', () => {
    expect(attentionGroup('reply_overdue')).toBe('replies');
    expect(attentionGroup('day_14')).toBe('follow_ups');
    expect(attentionGroup('client_edit')).toBe('events');
    expect(attentionGroup('quote_response')).toBe('quotes');
    expect(groupCounts([item({ kind: 'day_7' }), item({ kind: 'reply_overdue' }), item({ kind: 'requested' })]))
      .toEqual([{ group: 'replies', count: 1 }, { group: 'follow_ups', count: 2 }]);
  });

  it('opens events for event items and enquiries otherwise', () => {
    expect(attentionTarget(item({ kind: 'unconfirmed_event', event_id: 'v1' }))).toEqual({ page: 'events', params: { event: 'v1' } });
    expect(attentionTarget(item({ kind: 'quote_response', event_id: 'v2' }))).toEqual({ page: 'events', params: { event: 'v2' } });
    expect(attentionTarget(item({ kind: 'quote_response', event_id: null }))).toEqual({ page: 'enquiries', params: { enquiry: 'e1' } });
    expect(attentionTarget(item({ kind: 'day_7' }))).toEqual({ page: 'enquiries', params: { enquiry: 'e1' } });
    expect(attentionTarget(item({ enquiry_id: null, event_id: null }))).toBeNull();
  });

  it('calls out the importer’s "Orbit said confirmed" reviews', () => {
    const reason = 'Orbit marked this confirmed without a booking — record the customer’s confirmation (creates the event) or close it.';
    const review = item({ follow_up_reason: reason });
    expect(isOrbitReview(review)).toBe(true);
    expect(attentionBadge(review)).toEqual({ label: 'Review: Orbit said confirmed', tone: 'error' });
    expect(attentionNote(review)).toBe(reason);
    expect(isOrbitReview(item({ follow_up_reason: 'Call back after the board meeting' }))).toBe(false);
    expect(attentionNote(item({ detail: 'Status: booked.', follow_up_reason: null }))).toBe('Status: booked.');
    expect(attentionNote(item({}))).toBeNull();
  });

  it('badges overdue red and customer answers as needing you', () => {
    expect(attentionBadge(item({ overdue: false })).tone).toBe('warn');
    expect(attentionBadge(item({ kind: 'client_edit', label: 'Client edited after confirming', overdue: false }))).toEqual({ label: 'Client edited after confirming', tone: 'accent' });
  });

  it('says when, in the item’s own terms', () => {
    expect(attentionWhen(item({ date: '2026-10-09', overdue: true }), '2026-10-13')).toBe('Overdue since 9 Oct');
    expect(attentionWhen(item({ date: '2026-10-13', overdue: false }), '2026-10-13')).toBe('Due today');
    expect(attentionWhen(item({ kind: 'unconfirmed_event', date: '2026-10-22' }), '2026-10-13')).toBe('Thu 22 Oct');
    expect(attentionWhen(item({ kind: 'quote_response', date: '2026-10-11' }), '2026-10-13')).toBe('Answered 11 Oct');
  });
});
