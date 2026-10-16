import { describe, expect, it } from 'vitest';
import { DEFAULT_FILTERS, activePreset, extraFilterCount, filtersFromParams, listParams, nextSort, presetFilters, sortState } from './filters';
import { fnPageHref } from './nav';

const params = (q: Record<string, string>) => (name: string) => q[name] ?? null;

describe('enquiry list params', () => {
  it('sends only what is set, with the venue scope', () => {
    expect(listParams(DEFAULT_FILTERS, 'all', 0, 50)).toEqual({ sort: 'created_desc', limit: 50, offset: 0 });
    expect(listParams({ ...DEFAULT_FILTERS, q: '  goose ', stage: 'open' }, 'v1', 50, 50)).toEqual({
      sort: 'created_desc', limit: 50, offset: 50, venue: 'v1', q: 'goose', stage: ['new', 'replied', 'quoted'],
    });
    expect(listParams(DEFAULT_FILTERS, 'unassigned', 0, 50).venue).toBe('unassigned');
  });

  it('maps the next-action choices onto the API filters', () => {
    expect(listParams({ ...DEFAULT_FILTERS, nextAction: 'due' }, 'all', 0, 50).due_only).toBe(true);
    const followUps = listParams({ ...DEFAULT_FILTERS, nextAction: 'follow_ups_due' }, 'all', 0, 50);
    expect(followUps.next_action).toEqual(['requested', 'day_7', 'day_14']);
    expect(followUps.due_only).toBe(true);
    expect(listParams({ ...DEFAULT_FILTERS, nextAction: 'reply_overdue' }, 'all', 0, 50).next_action).toBe('reply_overdue');
  });
});

describe('column sorting', () => {
  it('sorts a new column its natural way, then flips it', () => {
    expect(nextSort('created_desc', 'created')).toBe('created_asc');
    expect(nextSort('created_asc', 'created')).toBe('created_desc');
    expect(nextSort('created_desc', 'contact')).toBe('contact_asc');
    expect(nextSort('contact_asc', 'contact')).toBe('contact_desc');
    expect(nextSort('created_desc', 'value')).toBe('value_desc');
    expect(nextSort('value_desc', 'value')).toBe('value_asc');
    expect(nextSort('created_desc', 'stage')).toBe('stage_asc');
  });

  it('turns a one-way column off again', () => {
    expect(nextSort('created_desc', 'due')).toBe('due');
    expect(nextSort('due', 'due')).toBe('created_desc');
    expect(nextSort('subject_asc', 'last_activity')).toBe('last_activity');
  });

  it('reports which way a column is sorted', () => {
    expect(sortState('guests_desc', 'guests')).toBe('desc');
    expect(sortState('guests_desc', 'value')).toBeNull();
    expect(sortState('preferred_date', 'preferred_date')).toBe('asc');
    expect(sortState('last_activity', 'last_activity')).toBe('desc');
  });

  it('accepts the new sorts from a deep link', () => {
    expect(filtersFromParams(params({ sort: 'value_desc' })).sort).toBe('value_desc');
    expect(filtersFromParams(params({ sort: 'bogus' })).sort).toBe('created_desc');
  });
});

describe('KPI tiles', () => {
  it('filter to exactly what each tile counts', () => {
    const open = presetFilters('open', { ...DEFAULT_FILTERS, source: 'email', q: 'x' });
    expect(open).toEqual({ ...DEFAULT_FILTERS, q: 'x', stage: 'open' });
    expect(presetFilters('needs_reply', DEFAULT_FILTERS)).toMatchObject({ stage: 'open', nextAction: 'reply_overdue' });
    expect(presetFilters('follow_ups_due', DEFAULT_FILTERS)).toMatchObject({ stage: 'open', nextAction: 'follow_ups_due' });
  });

  it('show as selected only when the filters are exactly theirs', () => {
    expect(activePreset(presetFilters('needs_reply', DEFAULT_FILTERS))).toBe('needs_reply');
    expect(activePreset({ ...presetFilters('open', DEFAULT_FILTERS), source: 'email' })).toBeNull();
    expect(activePreset(DEFAULT_FILTERS)).toBeNull();
  });

  it('counts the extra filters behind "More filters"', () => {
    expect(extraFilterCount({ ...DEFAULT_FILTERS, source: 'email', createdFrom: '2026-01-01', createdTo: '2026-02-01' })).toBe(2);
  });
});

describe('deep links', () => {
  it('reads list filters from a dashboard link', () => {
    expect(filtersFromParams(params({ stage: 'new,replied,quoted' })).stage).toBe('open');
    expect(filtersFromParams(params({ stage: 'quoted' })).stage).toBe('quoted');
    expect(filtersFromParams(params({ stage: 'bogus' })).stage).toBe('');
    expect(filtersFromParams(params({ next_action: 'requested,day_7,day_14', due_only: '1' })).nextAction).toBe('follow_ups_due');
    expect(filtersFromParams(params({ next_action: 'reply_overdue' })).nextAction).toBe('reply_overdue');
    expect(filtersFromParams(params({ due_only: 'true' })).nextAction).toBe('due');
    expect(filtersFromParams(params({ created_from: '2026-10-01', created_to: 'nope' }))).toMatchObject({ createdFrom: '2026-10-01', createdTo: '' });
  });

  it('builds page links, leaving out blank params', () => {
    expect(fnPageHref('enquiries', { enquiry: 'e1', new: null })).toBe('/app?page=functions-enquiries&enquiry=e1');
    expect(fnPageHref('inbox', { view: 'proposal', enquiry: 'e1' })).toBe('/app?page=functions-inbox&view=proposal&enquiry=e1');
    expect(fnPageHref('enquiries', { new: true, contact: 'c 1' })).toBe('/app?page=functions-enquiries&new=1&contact=c+1');
  });
});
