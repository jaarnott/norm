import { describe, expect, it } from 'vitest';
import {
  afterMidnight,
  buildChanges,
  dirtyKeys,
  initialState,
  isBlankRow,
  menuValueCents,
  reducer,
  toApi,
  toDraft,
  windowNextDay,
  type PortalState,
} from './portalModel';
import { parseRestrictions, toggleRestriction, writeRestrictions } from './portalFields';
import type { PortalPayload } from './types';

function payload(over: Partial<PortalPayload> = {}, event: Record<string, unknown> = {}): PortalPayload {
  return {
    authenticated: true,
    email: 'pat@example.com',
    event: {
      id: 'ev1',
      version: 3,
      name: "Pat's farewell",
      booking_type: 'group_booking',
      booking_label: 'Group booking',
      event_date: '2026-12-04',
      start_time: '19:00:00',
      end_time: '01:00:00',
      ends_next_day: true,
      guests: 30,
      special_instructions: null,
      allergies: null,
      arrival_time: '18:30:00',
      table_ordering: false,
      table_ordering_limit_cents: 15050,
      ...event,
    },
    status: {
      status: 'sent_to_client',
      edited_since_confirmed: false,
      sections: {
        details: { state: null, at: null, by_client: false },
        beverages: { state: null, at: null, by_client: false },
        food: { state: null, at: null, by_client: false },
      },
    },
    editable: ['allergies', 'arrival_time', 'guests', 'special_instructions', 'table_ordering', 'table_ordering_limit_cents'],
    fields: {},
    lists: { timings: [{ at_time: '19:30:00', ends_next_day: false, label: null, activity: 'Speeches', is_heading: false }] },
    list_kinds: {},
    menus: [
      {
        id: 'm1',
        version: 2,
        name: 'Canapés',
        is_set_menu: false,
        price_per_person_cents: null,
        guest_count: 0,
        value_cents: 5400,
        items: [
          { id: 'h1', kind: 'header', name: 'Hot', description: null, quantity: 0, unit_price_cents: null, dietaries: [] },
          { id: 'i1', kind: 'item', name: 'Arancini', description: null, quantity: 12, unit_price_cents: 450, dietaries: ['GF'] },
        ],
      },
    ],
    min_spend: null,
    contact: { name: 'Pat Planner', phone: null, company: null, email: 'pat@example.com' },
    people: [],
    venue: { id: 'v1', name: 'Brew Bar', brand: null },
    money: { currency: 'NZD', tax_label: 'incl. GST' },
    ...over,
  };
}

const start = (p = payload()) => initialState(p);
const run = (s: PortalState, ...actions: Parameters<typeof reducer>[1][]) => actions.reduce(reducer, s);

describe('values', () => {
  it('shows API values the way the inputs hold them', () => {
    expect(toDraft('time', '18:30:00')).toBe('18:30');
    expect(toDraft('int', 30)).toBe('30');
    expect(toDraft('int', null)).toBe('');
    expect(toDraft('money', 15050)).toBe('150.50');
    expect(toDraft('money', 20000)).toBe('200');
    expect(toDraft('bool', true)).toBe(true);
  });

  it('turns inputs back into API values, or says what is wrong', () => {
    expect(toApi('int', ' 45 ')).toEqual({ ok: true, value: 45 });
    expect(toApi('int', '')).toEqual({ ok: true, value: null });
    expect(toApi('int', '4.5')).toEqual({ ok: false, error: 'Enter a whole number.' });
    expect(toApi('time', '7:30')).toEqual({ ok: false, error: 'Enter a time like 19:30.' });
    expect(toApi('time', '19:30')).toEqual({ ok: true, value: '19:30' });
    expect(toApi('money', '$1,250.5')).toEqual({ ok: true, value: 125050 });
    expect(toApi('choice', '')).toEqual({ ok: true, value: null });
  });

  it('knows which times fall after midnight', () => {
    const event = { start_time: '19:00:00', ends_next_day: true };
    expect(afterMidnight('00:30', event)).toBe(true);
    expect(afterMidnight('21:00', event)).toBe(false);
    expect(afterMidnight('00:30', { start_time: '19:00:00', ends_next_day: false })).toBe(false);
    expect(windowNextDay('21:00', '02:00')).toBe(true);
    expect(windowNextDay('18:00', '23:00')).toBe(false);
  });
});

describe('editing and saving', () => {
  it('marks edits dirty and builds a PUT with the version it was based on', () => {
    const s = run(
      start(),
      { type: 'field', key: 'guests', value: '45' },
      { type: 'field', key: 'arrival_time', value: '18:45' },
      { type: 'contact', key: 'phone', value: '021 555' },
      { type: 'quantity', menuId: 'm1', itemId: 'i1', value: 20 },
    );
    expect(s.save).toBe('pending');
    expect(buildChanges(s, s.dirty)).toEqual({
      expected_version: 3,
      fields: { guests: 45, arrival_time: '18:45' },
      contact: { phone: '021 555' },
      menus: [{ id: 'm1', expected_version: 2, quantities: { i1: 20 } }],
    });
  });

  it('keeps an invalid value out of the save, with its message', () => {
    const s = run(start(), { type: 'field', key: 'guests', value: 'forty' });
    expect(s.invalid.guests).toBe('Enter a whole number.');
    expect(s.dirty.fields).toEqual([]);
    const fixed = reducer(s, { type: 'field', key: 'guests', value: '40' });
    expect(fixed.invalid.guests).toBeUndefined();
    expect(fixed.dirty.fields).toEqual(['guests']);
  });

  it('sends whole lists without the rows left empty, times as HH:MM', () => {
    const s = run(start(), {
      type: 'list',
      kind: 'timings',
      rows: [
        { at_time: '19:30:00', ends_next_day: false, label: null, activity: 'Speeches', is_heading: false },
        { at_time: '', ends_next_day: false, label: '', activity: '  ', is_heading: false },
      ],
    });
    expect(buildChanges(s, s.dirty).lists).toEqual({
      timings: [{ at_time: '19:30', ends_next_day: false, label: null, activity: 'Speeches', is_heading: false }],
    });
  });

  it('keeps edits made while a save is in flight for the next save', () => {
    let s = run(start(), { type: 'field', key: 'guests', value: '45' }, { type: 'saveStart' });
    expect(s.save).toBe('saving');
    s = reducer(s, { type: 'field', key: 'allergies', value: 'Two coeliacs' });
    const saved = payload({}, { version: 4, guests: 45 });
    s = reducer(s, { type: 'saveDone', payload: saved });
    expect(s.server.event.version).toBe(4);
    expect(s.draft.fields.guests).toBe('45');
    expect(s.draft.fields.allergies).toBe('Two coeliacs');
    expect(s.dirty.fields).toEqual(['allergies']);
    expect(s.save).toBe('pending');
    expect(buildChanges(s, s.dirty)).toEqual({ expected_version: 4, fields: { allergies: 'Two coeliacs' } });
  });

  it('says saved once nothing is left', () => {
    const s = run(start(), { type: 'field', key: 'guests', value: '45' }, { type: 'saveStart' }, { type: 'saveDone', payload: payload({}, { version: 4, guests: 45 }) });
    expect(s.save).toBe('saved');
  });

  it('keeps changes to retry when the network fails', () => {
    const s = run(start(), { type: 'field', key: 'guests', value: '45' }, { type: 'saveStart' }, { type: 'saveFailed', outcome: 'retry', message: 'offline' });
    expect(s.save).toBe('retry');
    expect(s.dirty.fields).toEqual(['guests']);
    expect(s.draft.fields.guests).toBe('45');
  });

  it('undoes a refused change instead of retrying it forever', () => {
    const s = run(start(), { type: 'field', key: 'guests', value: '45' }, { type: 'saveStart' }, { type: 'saveFailed', outcome: 'rejected', message: "You can't change: guests." });
    expect(s.save).toBe('rejected');
    expect(s.error).toBe("You can't change: guests.");
    expect(s.draft.fields.guests).toBe('30');
    expect(dirtyKeys(s.dirty)).toEqual([]);
  });
});

describe('a conflict, then reload', () => {
  const conflicted = () =>
    run(
      start(),
      { type: 'field', key: 'guests', value: '45' },
      { type: 'field', key: 'allergies', value: 'Two coeliacs' },
      { type: 'saveStart' },
      { type: 'saveFailed', outcome: 'conflict', message: 'This was changed by someone else since you opened it — reload and try again.' },
    );

  it('stops saving and keeps every unsaved edit', () => {
    const s = conflicted();
    expect(s.save).toBe('conflict');
    expect(dirtyKeys(s.dirty).sort()).toEqual(['allergies', 'guests']);
    // Further typing doesn't restart saving behind the banner.
    expect(reducer(s, { type: 'field', key: 'guests', value: '46' }).save).toBe('conflict');
  });

  it('keeps edits the venue did not touch, and reports the ones it did', () => {
    const theirs = payload({}, { version: 5, guests: 50, special_instructions: 'Cake at 9' });
    const s = reducer(conflicted(), { type: 'refreshed', payload: theirs });
    expect(s.server.event.version).toBe(5);
    expect(s.draft.fields.guests).toBe('50'); // the venue's change wins…
    expect(s.overridden).toEqual(['guests']); // …and is reported
    expect(s.draft.fields.allergies).toBe('Two coeliacs'); // untouched by the venue: still ours
    expect(s.draft.fields.special_instructions).toBe('Cake at 9');
    expect(s.dirty.fields).toEqual(['allergies']);
    expect(s.save).toBe('pending');
    expect(buildChanges(s, s.dirty)).toEqual({ expected_version: 5, fields: { allergies: 'Two coeliacs' } });
  });

  it('drops an edit to a field the venue has since hidden', () => {
    const theirs = payload({ editable: ['guests'] }, { version: 5 });
    const s = reducer(conflicted(), { type: 'refreshed', payload: theirs });
    expect(s.dirty.fields).toEqual(['guests']);
    expect(s.draft.fields.allergies).toBeUndefined();
  });
});

describe('menus and restrictions', () => {
  it('values a menu from the draft quantities like money.menu_food_cents', () => {
    const p = payload();
    expect(menuValueCents(p.menus[0], { i1: 20 })).toBe(9000);
    expect(menuValueCents({ ...p.menus[0], is_set_menu: true, price_per_person_cents: 6500, guest_count: 30 }, { i1: 0 })).toBe(195000);
  });

  it('reads JSON lists and free text alike, and writes a JSON list or null', () => {
    expect(parseRestrictions('["Cocktails","House wines"]')).toEqual(['Cocktails', 'House wines']);
    expect(parseRestrictions('cash bar')).toEqual(['cash bar']);
    expect(parseRestrictions('[]')).toEqual([]);
    expect(parseRestrictions(null)).toEqual([]);
    expect(writeRestrictions(['Cocktails', ' cocktails ', ''])).toBe('["Cocktails"]');
    expect(writeRestrictions([])).toBeNull();
  });

  it('treats "No restrictions" as excluding the rest', () => {
    expect(toggleRestriction(['Cocktails'], 'No restrictions')).toEqual(['No restrictions']);
    expect(toggleRestriction(['No restrictions'], 'Cocktails')).toEqual(['Cocktails']);
    expect(toggleRestriction(['Cocktails', 'Tap beers'], 'cocktails')).toEqual(['Tap beers']);
  });

  it('ignores rows the client added but never filled in', () => {
    expect(isBlankRow('security', { name: ' ', start_time: '', end_time: null, notes: '' })).toBe(true);
    expect(isBlankRow('av', { kind: 'in_house_mic', details: '' })).toBe(false);
  });
});
