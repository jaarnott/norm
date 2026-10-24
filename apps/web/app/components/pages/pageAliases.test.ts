import { describe, expect, it } from 'vitest';
import { ALIASED_PAGE_IDS, aliasOpenPageDetail, aliasPageId, canonicalSearch, resolvePageAlias } from './pageAliases';
import { FUNCTIONAL_PAGES } from './pageRegistry';

const to = (id: string, params: Record<string, string> = {}): Record<string, string> => {
  const r = resolvePageAlias(id, params);
  return { page: r.page, ...r.params };
};

describe('old Finance page ids', () => {
  it('open the page and tab that replaced them', () => {
    expect(to('payroll-leave')).toEqual({ page: 'payroll', tab: 'leave' });
    expect(to('payroll-settings', { view: 'xero', venue: 'v1' })).toEqual({ page: 'payroll', tab: 'settings', view: 'xero', venue: 'v1' });
    expect(to('payroll-shadow', { run: 'r1' })).toEqual({ page: 'payroll', tab: 'trial-run', run: 'r1' });
    expect(to('ent-roster', { performance: 'p1' })).toEqual({ page: 'entertainers', tab: 'roster', performance: 'p1' });
    expect(to('ent-register', { performer: 'x' })).toEqual({ page: 'entertainers', tab: 'performers', performer: 'x' });
    expect(to('ent-payments', { performance: 'p1' })).toEqual({ page: 'entertainers', tab: 'payments', performance: 'p1' });
    expect(to('ent-emails', { performer: 'x' })).toEqual({ page: 'entertainers', tab: 'emails', performer: 'x' });
    expect(to('finance-forecasting')).toEqual({ page: 'finance-budgets', tab: 'forecast' });
  });

  it('open Budget vs Loaded on the year table, unless a view is named', () => {
    expect(to('finance-budget-vs-loaded')).toEqual({ page: 'finance-budgets', tab: 'daily', show: 'year' });
    expect(to('finance-budget-vs-loaded', { show: 'compare' })).toEqual({ page: 'finance-budgets', tab: 'daily', show: 'compare' });
  });

  it('open Intercompany and Reconciliation on All entities unless an entity is named', () => {
    expect(to('payroll-intercompany')).toEqual({ page: 'payroll', tab: 'intercompany', venue: 'all' });
    expect(to('payroll-reconciliation', { venue: 'v2' })).toEqual({ page: 'payroll', tab: 'reconciliation', venue: 'v2' });
  });

  it('map every legacy Payrun view', () => {
    expect(to('payroll-payrun')).toEqual({ page: 'payroll', tab: 'hours' });
    expect(to('payroll-payrun', { view: 'staff' })).toEqual({ page: 'payroll', tab: 'hours' });
    expect(to('payroll-payrun', { view: 'timeclock' })).toEqual({ page: 'payroll', tab: 'hours', view: 'timeclock' });
    expect(to('payroll-payrun', { view: 'security' })).toEqual({ page: 'payroll', tab: 'hours', view: 'security' });
    expect(to('payroll-payrun', { view: 'salaried' })).toEqual({ page: 'payroll', tab: 'hours', view: 'salaried' });
    expect(to('payroll-payrun', { view: 'leave' })).toEqual({ page: 'payroll', tab: 'leave' });
    expect(to('payroll-payrun', { view: 'gratuities' })).toEqual({ page: 'payroll', tab: 'gratuities' });
    expect(to('payroll-payrun', { view: 'entertainers' })).toEqual({ page: 'payroll', tab: 'entertainers' });
    expect(to('payroll-payrun', { view: 'hygiene' })).toEqual({ page: 'payroll', tab: 'checks' });
    expect(to('payroll-payrun', { view: 'exports' })).toEqual({ page: 'payroll', tab: 'ace-files' });
  });

  it('carry venue, week and every other param, and let an explicit tab win', () => {
    expect(to('payroll-leave', { venue: 'v1', week: '2026-10-12', foo: 'bar' }))
      .toEqual({ page: 'payroll', tab: 'leave', venue: 'v1', week: '2026-10-12', foo: 'bar' });
    expect(to('payroll-payrun', { tab: 'reconciliation' }).tab).toBe('reconciliation');
  });

  it('accept URLSearchParams and drop the old page param', () => {
    const r = resolvePageAlias('ent-roster', new URLSearchParams('page=ent-roster&performance=p1'));
    expect(r).toEqual({ page: 'entertainers', params: { tab: 'roster', performance: 'p1' }, aliased: true });
    expect(canonicalSearch(r)).toBe('?page=entertainers&tab=roster&performance=p1');
  });

  it('all land on pages that exist', () => {
    const ids = new Set(FUNCTIONAL_PAGES.map((p) => p.id));
    for (const old of ALIASED_PAGE_IDS) {
      expect(ids.has(resolvePageAlias(old).page), old).toBe(true);
      expect(ids.has(old), `${old} is still a page`).toBe(false);
    }
  });
});

describe('ids that are not old Finance pages', () => {
  it('pass through untouched — including the Time & Attendance roster', () => {
    expect(resolvePageAlias('roster', { week: 'w' })).toEqual({ page: 'roster', params: { week: 'w' }, aliased: false });
    expect(resolvePageAlias('functions-events', {})).toEqual({ page: 'functions-events', params: {}, aliased: false });
    expect(resolvePageAlias('payroll', { tab: 'leave' }).aliased).toBe(false);
    expect(resolvePageAlias('toString').aliased).toBe(false);
    expect(aliasPageId('roster')).toBe('roster');
    expect(aliasPageId(null)).toBeNull();
    expect(aliasPageId('payroll-leave')).toBe('payroll');
  });
});

describe('the open-page event', () => {
  it('translates an old page and merges the tab into the caller’s params', () => {
    expect(aliasOpenPageDetail({ page: 'payroll-leave', params: { venue: 'v1', open: true, skip: false } }))
      .toEqual({ page: 'payroll', params: { venue: 'v1', open: '1', tab: 'leave' } });
  });

  it('leaves new callers alone', () => {
    const detail = { page: 'entertainers', params: { tab: 'payments', performance: 'p1' } };
    expect(aliasOpenPageDetail(detail)).toBe(detail);
    expect(aliasOpenPageDetail(undefined)).toBeUndefined();
  });
});
