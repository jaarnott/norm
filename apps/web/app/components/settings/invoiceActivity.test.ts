import { describe, expect, it } from 'vitest';
import { activityTags, deleteNote, filtersFromLink, lastDays, levelWords, modeShort, outcomeShort, outcomeWords, rowLevel, whereWords, type ActivityRow } from './invoiceActivity';

const row = (over: Partial<ActivityRow> = {}): ActivityRow => ({
  key: 'o:1',
  venue_id: 'v',
  venue_name: 'Bessie',
  invoice_id: 'i',
  reference_number: 'INV-1',
  supplier_name: 'Prime Fresh',
  total: 10,
  at: '2026-10-19T01:00:00Z',
  outcome: 'received',
  by: 'norm',
  where: 'norm',
  level: null,
  activity: {},
  ...over,
});

describe('mode', () => {
  it('names the level and the switches that were on', () => {
    expect(levelWords({ mode: 'autopilot', switches_on: ['auto_create_items', 'receive_without_po'] })).toEqual({
      label: 'Autopilot',
      switches: 'Switches on: create stock items, receive without an order',
    });
    expect(levelWords({ mode: 'approve_all', switches_on: [] })?.switches).toBe('No switches on');
    // Rows from before 19 Oct 2026 have no level.
    expect(levelWords(null)).toBeNull();
  });

  it('never claims "no switches on" for a row that did not record its level', () => {
    // Rows from before levels were recorded, and backfilled stops.
    expect(rowLevel({ level: null, run_mode: 'autopilot' })).toEqual({
      label: 'Autopilot', switches: 'Level not recorded',
    });
    expect(rowLevel({ level: { mode: 'autopilot', switches_on: [] } })?.switches).toBe('No switches on');
    expect(rowLevel({ level: null, run_mode: null })).toBeNull();
  });

  it('says where it was received', () => {
    expect(whereWords({ where: 'app' })).toBe("in Norm's app");
    expect(whereWords({ where: 'claude' })).toBe('on a card in Claude');
  });
});

describe('outcome', () => {
  it('counts a person’s changes and gives a delete its reason', () => {
    expect(outcomeWords(row())).toBe('Received by Norm');
    expect(outcomeWords(row({ by: 'person', person_changes: 1 }))).toBe('Received by a person, with 1 change');
    expect(outcomeWords(row({ by: 'person', person_changes: 0 }))).toBe('Received by a person, no changes');
    expect(outcomeWords(row({ outcome: 'deleted', delete_reason: 'not_an_invoice' }))).toBe('Deleted: not an invoice');
    expect(outcomeWords(row({ outcome: 'deleted', delete_reason: 'other', delete_note: 'a letter' }))).toBe('Deleted: a letter');
  });
});

describe('one-line row', () => {
  it('keeps the outcome and mode short', () => {
    expect(outcomeShort(row({ outcome: 'deleted', delete_reason: 'duplicate' }))).toBe('Deleted: duplicate');
    expect(outcomeShort(row({ by: 'person', where: 'app', person_changes: 2 }))).toBe('Received by a person, 2 changes');
    expect(modeShort(row({ by: 'person', where: 'app', level: { mode: 'autopilot' } }))).toBe("Autopilot · Norm's app");
    expect(modeShort(row({ by: 'person', where: 'claude' }))).toBe('Claude card');
    expect(modeShort(row({ run_mode: 'autopilot' }))).toBe('Autopilot');
  });
});

describe('tags', () => {
  it('says what Norm did along the way', () => {
    const tags = activityTags(row({
      activity: {
        layout: 'Prime Fresh & Co',
        reread: true,
        lines: 3,
        matched: { 'supplier code': 1, name: 1 },
        units: { 'the invoice': 2 },
        totals: 'worked out',
        created: { stock_items: ['Basil 100g'], suppliers: ['Prime Fresh & Co'] },
      },
      unit_check: { flagged: 2, open: 1 },
    })).map((t) => t.text);
    expect(tags).toEqual([
      'Re-read with “Prime Fresh & Co”',
      '2 of 3 lines matched',
      'Units from the invoice (2)',
      'Norm worked out the totals',
      'Created 1 stock item, 1 supplier',
      'Unit check: 2 lines flagged, 1 to look at',
    ]);
  });

  it('reads what older rows carry, never as created', () => {
    expect(activityTags(row({
      activity: { suggested_creates: { units: 2 } },
      changes: { supplier: 1, line_value: 8, strike: 1, add_line: 2 },
    })).map((t) => t.text)).toEqual([
      'Norm suggested creating 2 units',
      'Norm suggested: 8 line figures, 2 added lines, 1 supplier, …',
    ]);
    expect(activityTags(row({ outcome: 'deleted', changes: { line_value: 38 } }))).toEqual([]);
  });
});

describe('a person\'s delete', () => {
  it('says a person did it, unlike Norm\'s own', () => {
    const del = row({ outcome: 'deleted', delete_reason: 'statement' });
    expect(outcomeShort({ ...del, by: 'person' })).toBe('Deleted by a person: statement');
    expect(outcomeShort({ ...del, by: 'norm' })).toBe('Deleted: statement');
    expect(outcomeWords({ ...del, by: 'person' }).startsWith('Deleted by a person: ')).toBe(true);
  });
  it('says where a person received it before what they changed', () => {
    const got = row({ outcome: 'received', by: 'person', where: 'app', person_changes: 0 });
    expect(outcomeWords(got)).toBe("Received by a person in Norm's app, no changes");
    expect(outcomeWords({ ...got, where: 'claude', person_changes: 2 }))
      .toBe('Received by a person on a card in Claude, with 2 changes');
  });
  it('says where before the reason, which can name another receive', () => {
    const dup = row({ outcome: 'deleted', by: 'person', where: 'app', delete_reason: 'duplicate' });
    expect(outcomeWords(dup)).toBe("Deleted by a person in Norm's app: a duplicate of one already received");
    // A note is said once: as the reason when the reason has no words.
    const other = row({ outcome: 'deleted', by: 'person', where: 'app', delete_reason: 'other', delete_note: 'a letter' });
    expect(outcomeWords(other)).toBe("Deleted by a person in Norm's app: a letter");
    expect(deleteNote(other)).toBeNull();
    expect(deleteNote({ ...dup, delete_note: 'same as INV-9' })).toBe('same as INV-9');
  });
});

describe('period', () => {
  it('ends today and counts today as one of the days', () => {
    expect(lastDays(7, new Date(2026, 9, 19))).toEqual({ start: '2026-10-13', end: '2026-10-19' });
  });

  it('takes a link’s dates and venues, and ignores bad dates', () => {
    expect(filtersFromLink('from=2026-10-18&to=2026-10-19&venues=a,b')).toEqual({
      start: '2026-10-18', end: '2026-10-19', venues: ['a', 'b'],
    });
    expect(filtersFromLink('from=2026-10-19&to=2026-10-18')).toEqual({ venues: [] });
    expect(filtersFromLink(undefined)).toEqual({ venues: [] });
  });
});
