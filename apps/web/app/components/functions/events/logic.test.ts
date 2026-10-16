import { describe, expect, it } from 'vitest';
import {
  addDays,
  addMonths,
  addRestriction,
  centsToInput,
  changedFields,
  changeFieldLabel,
  changeValueText,
  compactMoney,
  daysBetween,
  endsNextDay,
  filterByStatus,
  foodTimingNeeds,
  foodTimingText,
  groupByMonth,
  humaniseDetail,
  monthLabel,
  moveItem,
  newestFirst,
  normalisePayment,
  normaliseTime,
  pageOf,
  paymentLabel,
  parseRestrictions,
  presetRange,
  sectionBadge,
  serializeRestrictions,
  splitDay,
  splitEmails,
  statusCounts,
  stripIds,
  textToHtml,
  timingNextDay,
  todayIn,
} from './logic';
import type { EventChange, EventDetail, EventRow } from './types';

function row(over: Partial<EventRow>): EventRow {
  return {
    id: over.id ?? 'e1',
    version: 1,
    name: 'Party',
    enquiry_id: null,
    contact_id: 'c1',
    contact_name: 'Casey',
    venue_id: 'v1',
    venue_name: 'Brew Bar',
    area_id: null,
    area_name: null,
    booking_type: 'group_booking',
    event_date: '2026-10-17',
    start_time: '18:00',
    end_time: '23:00',
    ends_next_day: false,
    guests: 20,
    bar_tab_cents: 0,
    status: 'booked',
    edited_since_confirmed: false,
    min_spend_cents: null,
    food_cents: 0,
    expected_spend_cents: 0,
    cancelled_at: null,
    ...over,
  };
}

function change(over: Partial<EventChange>): EventChange {
  return {
    id: over.id ?? 'ch',
    kind: 'edited',
    section: 'details',
    changes: null,
    after_confirmation: false,
    note: null,
    actor_kind: 'staff',
    actor: null,
    occurred_at: '2026-10-01T00:00:00Z',
    ...over,
  };
}

type BadgeInput = Pick<EventDetail, 'sections' | 'changes' | 'portal_links'>;
const noSections: BadgeInput['sections'] = {
  details: { kind: null, done: false },
  beverages: { kind: null, done: false },
  food: { kind: null, done: false },
};

describe('plain dates never pass through the browser zone', () => {
  it('reads today in the company zone, not the machine zone', () => {
    // 11:30 UTC on 12 Oct is already 13 Oct in Auckland (NZDT, +13).
    const instant = new Date('2026-10-12T11:30:00Z');
    expect(todayIn('Pacific/Auckland', instant)).toBe('2026-10-13');
    expect(todayIn('America/New_York', instant)).toBe('2026-10-12');
  });

  it('adds days across month, year and DST boundaries', () => {
    expect(addDays('2026-10-30', 3)).toBe('2026-11-02');
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    // NZ DST starts 27 Sep 2026 — a calendar day is still a day.
    expect(addDays('2026-09-26', 2)).toBe('2026-09-28');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
    expect(daysBetween('2026-10-12', '2026-11-12')).toBe(31);
  });

  it('steps months and labels them', () => {
    expect(addMonths('2026-12', 1)).toBe('2027-01');
    expect(addMonths('2026-01', -1)).toBe('2025-12');
    expect(monthLabel('2026-10')).toBe('October 2026');
  });

  it('builds the preset ranges inclusively from today', () => {
    expect(presetRange('next14', '2026-10-12')).toEqual({ from: '2026-10-12', to: '2026-10-25' });
    expect(presetRange('next7', '2026-10-12')).toEqual({ from: '2026-10-12', to: '2026-10-18' });
    expect(presetRange('next42', '2026-10-12')).toEqual({ from: '2026-10-12', to: '2026-11-22' });
  });
});

describe('list grouping, counts and paging', () => {
  const rows = [
    row({ id: 'a', event_date: '2026-10-20', status: 'confirmed', expected_spend_cents: 100000, edited_since_confirmed: true }),
    row({ id: 'b', event_date: '2026-10-28', status: 'cancelled', expected_spend_cents: 50000 }),
    row({ id: 'c', event_date: '2026-11-02', status: 'sent_to_client', expected_spend_cents: 25000 }),
    row({ id: 'd', event_date: '2026-11-03', status: 'booked', expected_spend_cents: 0 }),
  ];

  it('groups consecutive months and leaves cancelled spend out of the total', () => {
    const groups = groupByMonth(rows);
    expect(groups.map((g) => [g.month, g.rows.length, g.expectedCents])).toEqual([
      ['2026-10', 2, 100000],
      ['2026-11', 2, 25000],
    ]);
    expect(groups[0].label).toBe('October 2026');
  });

  it('counts each status and the edited flag', () => {
    expect(statusCounts(rows)).toEqual({
      booked: 1,
      sent_to_client: 1,
      in_progress: 0,
      confirmed: 1,
      cancelled: 1,
      edited: 1,
    });
  });

  it('filters: none = everything but cancelled', () => {
    expect(filterByStatus(rows, null).map((r) => r.id)).toEqual(['a', 'c', 'd']);
    expect(filterByStatus(rows, 'cancelled').map((r) => r.id)).toEqual(['b']);
    expect(filterByStatus(rows, 'edited').map((r) => r.id)).toEqual(['a']);
  });

  it('pages and clamps the page number', () => {
    const items = Array.from({ length: 108 }, (_, i) => i);
    const p = pageOf(items, 2, 50);
    expect([p.first, p.last, p.total, p.pages, p.rows.length]).toEqual([101, 108, 108, 3, 8]);
    expect(pageOf(items, 9, 50).page).toBe(2);
    expect(pageOf([], 0, 50)).toMatchObject({ first: 0, last: 0, pages: 1 });
  });
});

describe('calendar cells', () => {
  it('shows every event that fits, else slots − 1 and a "+N more" line', () => {
    expect(splitDay([1, 2, 3], 3)).toEqual({ visible: [1, 2, 3], more: 0 });
    expect(splitDay([1, 2, 3, 4, 5], 3)).toEqual({ visible: [1, 2], more: 3 });
    expect(splitDay([1, 2], 1)).toEqual({ visible: [1], more: 1 });
  });

  it('shortens money for tight cells', () => {
    expect(compactMoney(420000)).toBe('$4.2K');
    expect(compactMoney(1250000000)).toBe('$12.5M');
    expect(compactMoney(95000)).toBe('$950');
    expect(compactMoney(27600)).toBe('$276');
    expect(compactMoney(null)).toBe('');
  });
});

describe('section badges', () => {
  it('is "not sent" before the portal link goes out', () => {
    expect(sectionBadge({ sections: noSections, changes: [], portal_links: [] }, 'food')).toBe('not_sent');
  });

  it('is "sent" once a link was sent (imported link_sent counts)', () => {
    const d: BadgeInput = { sections: noSections, changes: [change({ kind: 'link_sent', section: null })], portal_links: [] };
    expect(sectionBadge(d, 'beverages')).toBe('sent');
  });

  it('shows a client edit of that section, but not of another', () => {
    const d: BadgeInput = {
      sections: noSections,
      changes: [change({ kind: 'link_sent', section: null }), change({ actor_kind: 'client', section: 'food' })],
      portal_links: [],
    };
    expect(sectionBadge(d, 'food')).toBe('client_edited');
    expect(sectionBadge(d, 'beverages')).toBe('sent');
  });

  it('confirmed and skipped facts win; reopening forgets older edits', () => {
    const edits = [change({ actor_kind: 'client', section: 'details', occurred_at: '2026-10-01T00:00:00Z' })];
    expect(
      sectionBadge({ sections: { ...noSections, details: { kind: 'confirmed', done: true } }, changes: edits, portal_links: [] }, 'details'),
    ).toBe('confirmed');
    expect(
      sectionBadge({ sections: { ...noSections, details: { kind: 'skipped', done: true } }, changes: [], portal_links: [] }, 'details'),
    ).toBe('skipped');
    const reopened: BadgeInput = {
      sections: { ...noSections, details: { kind: 'reopened', done: false, occurred_at: '2026-10-05T00:00:00Z' } },
      changes: edits,
      portal_links: [
        { id: 'p', kind: 'portal', url: 'u', expires_at: null, last_used_at: null, revoked_at: null, created_at: '2026-09-01T00:00:00Z' },
      ],
    };
    expect(sectionBadge(reopened, 'details')).toBe('sent');
  });
});

describe('restrictions are stored as a JSON array string', () => {
  it('reads JSON arrays as chips and free text as one entry', () => {
    expect(parseRestrictions('["Cocktails","House Wines"]')).toEqual(['Cocktails', 'House Wines']);
    expect(parseRestrictions('[]')).toEqual([]);
    expect(parseRestrictions('cash bar')).toEqual(['cash bar']);
    expect(parseRestrictions('[not json')).toEqual(['[not json']);
    expect(parseRestrictions(null)).toEqual([]);
  });

  it('writes a JSON array, null when empty, without duplicates', () => {
    expect(serializeRestrictions(['Cocktails', ' cocktails ', 'House wines'])).toBe('["Cocktails","House wines"]');
    expect(serializeRestrictions([])).toBeNull();
  });

  it('"No restrictions" excludes the rest', () => {
    expect(addRestriction(['Cocktails'], 'No restrictions')).toEqual(['No restrictions']);
    expect(addRestriction(['No restrictions'], 'Cocktails')).toEqual(['Cocktails']);
    expect(addRestriction(['Cocktails'], 'cocktails')).toEqual(['Cocktails']);
  });
});

describe('times and windows (venue-local wall clock)', () => {
  it('normalises clock strings', () => {
    expect(normaliseTime('19:30:00')).toBe('19:30');
    expect(normaliseTime('7:05')).toBe('07:05');
    expect(normaliseTime('')).toBeNull();
    expect(normaliseTime('soon')).toBeNull();
  });

  it('ends the next day when the end is at or before the start', () => {
    expect(endsNextDay('19:00', '01:00')).toBe(true);
    expect(endsNextDay('18:00', '23:00')).toBe(false);
    expect(endsNextDay('18:00', null)).toBe(false);
  });

  it('puts a run-sheet time after midnight only inside the after-midnight part', () => {
    expect(timingNextDay('00:30', '19:00', '01:00', true)).toBe(true);
    expect(timingNextDay('17:00', '19:00', '01:00', true)).toBe(false); // set-up before the start
    expect(timingNextDay('21:00', '19:00', '01:00', true)).toBe(false);
    expect(timingNextDay('00:30', '19:00', '23:00', false)).toBe(false);
  });

  it('knows which food timing types need which times', () => {
    expect(foodTimingNeeds('staggered_time')).toEqual({ time: true, end: true });
    expect(foodTimingNeeds('all_at_once_time')).toEqual({ time: true, end: false });
    expect(foodTimingNeeds('staggered_arrival')).toEqual({ time: false, end: false });
    expect(foodTimingText('staggered_time', '19:30', '20:00')).toBe('Staggered from 7:30 pm to 8:00 pm');
    expect(foodTimingText('all_at_once_arrival', null, null)).toBe('All at once on arrival');
  });
});

describe('forms', () => {
  it('shows cents in money inputs without trailing zeros', () => {
    expect(centsToInput(120000)).toBe('1200');
    expect(centsToInput(12345)).toBe('123.45');
    expect(centsToInput(null)).toBe('');
  });

  it('sends only changed fields, treating blank and null alike', () => {
    const original = { guests: 20, notes: null as string | null, name: 'Party' };
    expect(changedFields(original, { guests: 20, notes: '', name: 'Big party' })).toEqual({ name: 'Big party' });
    expect(changedFields(original, { notes: 'VIP' })).toEqual({ notes: 'VIP' });
  });

  it('maps the imported "invoice" payment to "To be invoiced"', () => {
    expect(normalisePayment('invoice')).toBe('to_be_invoiced');
    expect(paymentLabel('invoice')).toBe('To be invoiced');
    expect(paymentLabel('paid_on_night')).toBe('Paid on the night');
    expect(paymentLabel('cash')).toBe('cash');
  });

  it('reorders rows and ignores moves off the ends', () => {
    expect(moveItem(['a', 'b', 'c'], 0, 1)).toEqual(['b', 'a', 'c']);
    expect(moveItem(['a', 'b', 'c'], 2, 3)).toEqual(['a', 'b', 'c']);
    expect(stripIds([{ id: 'x', at_time: '19:00' }])).toEqual([{ at_time: '19:00' }]);
  });

  it('splits emails and escapes typed text into paragraphs', () => {
    expect(splitEmails('a@b.com, c@d.com;e@f.com')).toEqual(['a@b.com', 'c@d.com', 'e@f.com']);
    expect(textToHtml('Hi <Sam>\nsee you\n\nThanks')).toBe('<p>Hi &lt;Sam&gt;<br>see you</p><p>Thanks</p>');
  });
});

describe('the changes log', () => {
  it('labels fields and formats values for people', () => {
    expect(changeFieldLabel('bar_tab_cents')).toBe('Bar tab');
    expect(changeFieldLabel('menu.guest_count')).toBe('Menu guest count');
    expect(changeFieldLabel('Set menu: Fish')).toBe('Set menu: Fish');
    expect(changeValueText('bar_tab_cents', 150000)).toBe('$1,500.00');
    expect(changeValueText('start_time', '19:30')).toBe('7:30 pm');
    expect(changeValueText('timings', [1, 2])).toBe('2 rows');
    expect(changeValueText('menus', { added: 'Set menu' })).toBe('added Set menu');
    expect(changeValueText('area_id', 'x')).toBe('set');
    expect(changeValueText('notes', null)).toBe('—');
  });

  it('turns the timeline detail\'s field keys into labels', () => {
    expect(humaniseDetail('bar_tab_cents, bar_tab_restrictions')).toBe('Bar tab, Restrictions');
    expect(humaniseDetail('guests — Two more guests, agreed by phone')).toBe('Guests — Two more guests, agreed by phone');
    expect(humaniseDetail('contact.phone')).toBe('Contact phone');
    expect(humaniseDetail('Confirmed by phone — F7 test')).toBe('Confirmed by phone — F7 test');
    expect(humaniseDetail(null)).toBe('');
  });

  it('lists changes newest first', () => {
    const list = [change({ id: 'old', occurred_at: '2026-01-01T00:00:00Z' }), change({ id: 'new', occurred_at: '2026-02-01T00:00:00Z' })];
    expect(newestFirst(list).map((c) => c.id)).toEqual(['new', 'old']);
  });
});
