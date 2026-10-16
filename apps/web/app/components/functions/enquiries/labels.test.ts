import { describe, expect, it } from 'vitest';
import {
  dateRangeLabel, dayOf, decodeEntities, detailText, dueBadge, eventStatusBadge, lastEmailOf, moneyAuto, optionBadge,
  pctLabel, sourceLabel, stageLabel, stageTone,
} from './labels';

describe('stage and state labels', () => {
  it('uses the words and colours staff know from Orbit', () => {
    expect([stageLabel('new'), stageTone('new')]).toEqual(['New', 'neutral']);
    expect([stageLabel('replied'), stageTone('replied')]).toEqual(['Replied', 'warn']);
    expect([stageLabel('quoted'), stageTone('quoted')]).toEqual(['Quoted', 'info']);
    expect([stageLabel('confirmed'), stageTone('confirmed')]).toEqual(['Confirmed', 'ok']);
    expect([stageLabel('lost'), stageTone('lost')]).toEqual(['Lost', 'error']);
    expect(stageLabel('something_else')).toBe('Something else');
  });

  it('marks a proposed option in an approval area as needing approval', () => {
    expect(optionBadge({ state: 'proposed', area_requires_approval: true })).toEqual({ label: 'Needs approval', tone: 'warn' });
    expect(optionBadge({ state: 'proposed', area_requires_approval: false })).toEqual({ label: 'Proposed', tone: 'info' });
    expect(optionBadge({ state: 'approved', area_requires_approval: true }).tone).toBe('ok');
    expect(optionBadge({ state: 'withdrawn', area_requires_approval: true }).label).toBe('Withdrawn');
  });

  it('labels event statuses', () => {
    expect(eventStatusBadge('sent_to_client')).toEqual({ label: 'Sent to client', tone: 'warn' });
    expect(eventStatusBadge('confirmed').tone).toBe('ok');
  });

  it('names sources for people', () => {
    expect(sourceLabel('web_form')).toBe('Web form');
    expect(sourceLabel('agent')).toBe('Norm');
    expect(sourceLabel(null)).toBe('—');
  });
});

describe('due badge', () => {
  it('shows overdue, due today, a future due date, or nothing', () => {
    expect(dueBadge({ kind: 'reply_overdue', due_date: '2026-10-09', overdue: true, due_today: false }))
      .toEqual({ label: 'Overdue · 9 Oct', tone: 'error' });
    expect(dueBadge({ kind: 'day_7', due_date: '2026-10-13', overdue: false, due_today: true }))
      .toEqual({ label: 'Due today', tone: 'warn' });
    expect(dueBadge({ kind: 'requested', due_date: '2026-11-01', overdue: false, due_today: false }))
      .toEqual({ label: 'Due 1 Nov', tone: 'neutral' });
    expect(dueBadge({ kind: 'none', due_date: null, overdue: false, due_today: false })).toBeNull();
  });
});

describe('money and numbers', () => {
  it('drops cents only when there are none', () => {
    expect(moneyAuto(1400000)).toBe('$14,000');
    expect(moneyAuto(8250)).toBe('$82.50');
    expect(moneyAuto(null)).toBe('—');
  });

  it('shows a missing rate as a dash, not 0%', () => {
    expect(pctLabel(43)).toBe('43%');
    expect(pctLabel(0)).toBe('0%');
    expect(pctLabel(null)).toBe('—');
  });
});

describe('dates', () => {
  it('formats plan ranges without shifting days', () => {
    expect(dateRangeLabel('2026-11-26', '2026-11-26')).toBe('Thu 26 Nov 2026');
    expect(dateRangeLabel('2026-11-26', '2026-11-28')).toBe('Thu 26 Nov – Sat 28 Nov 2026');
    expect(dateRangeLabel('2026-12-31', '2027-01-01')).toBe('Thu 31 Dec 2026 – Fri 1 Jan 2027');
    expect(dateRangeLabel(null, null)).toBe('—');
  });

  it('reads a UTC instant as the day in the venue’s zone', () => {
    // 11:30 UTC on 12 Oct is already 13 Oct in Auckland (UTC+13).
    expect(dayOf('2026-10-12T11:30:00Z', 'Pacific/Auckland', 2026)).toBe('13 Oct');
    expect(dayOf('2025-10-12T00:00:00Z', 'Pacific/Auckland', 2026)).toBe('12 Oct 2025');
  });
});

describe('last email', () => {
  it('takes the later of the customer’s last email and our last reply', () => {
    expect(lastEmailOf({ last_inbound_at: '2026-10-08T04:00:00Z', last_outbound_at: '2026-10-08T03:00:00Z' }))
      .toEqual({ at: '2026-10-08T04:00:00Z', inbound: true, from: null });
    expect(lastEmailOf({ last_inbound_at: '2026-10-08T04:00:00Z', last_outbound_at: '2026-10-09T01:00:00Z' }))
      .toEqual({ at: '2026-10-09T01:00:00Z', inbound: false, from: null });
    expect(lastEmailOf({ last_inbound_at: null, last_outbound_at: null })).toBeNull();
  });

  it('names the sender of the newest message when the API sends one', () => {
    expect(lastEmailOf({ last_inbound_at: null, last_outbound_at: null, last_email_from: { name: 'Grania', email: 'g@x.nz', at: '2026-10-01T00:00:00Z' } }))
      .toEqual({ at: '2026-10-01T00:00:00Z', inbound: null, from: 'Grania' });
    expect(lastEmailOf({ last_inbound_at: null, last_outbound_at: null, last_email_from: { name: null, email: 'g@x.nz', at: '2026-10-01T00:00:00Z' } })?.from)
      .toBe('g@x.nz');
  });
});

describe('snippet text', () => {
  it('decodes the entities Gmail snippets carry, as text', () => {
    expect(decodeEntities('It&#39;s been passed &amp; noted &lt;3')).toBe('It\'s been passed & noted <3');
    expect(decodeEntities('caf&#xe9; &nbsp;x')).toBe('café  x');
    expect(decodeEntities('&bogus; stays')).toBe('&bogus; stays');
  });

  it('flattens a timeline detail object into one line', () => {
    expect(detailText({ guests: 80, area: 'Sundeck', empty: null })).toBe('Guests: 80 · Area: Sundeck');
    expect(detailText('Re: Hi &amp; bye')).toBe('Re: Hi & bye');
    expect(detailText(null)).toBe('');
  });
});
