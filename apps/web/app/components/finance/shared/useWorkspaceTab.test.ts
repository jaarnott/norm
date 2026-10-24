import { describe, expect, it } from 'vitest';
import { ownPageSearch, resolveWorkspaceTab, tabSwitchSearch, withParams } from './useWorkspaceTab';

const TABS = ['hours', 'checks', 'leave', 'trial-run'] as const;
const opts = { pageId: 'payroll', tabs: TABS, defaultTab: 'hours' as const };

describe('the tab a URL opens', () => {
  it('honours tab on the page’s own URL', () => {
    expect(resolveWorkspaceTab('?page=payroll&tab=leave', opts)).toBe('leave');
  });

  it('ignores a tab left behind by another page (a menu click never rewrites the URL)', () => {
    expect(resolveWorkspaceTab('?page=entertainers&tab=leave', opts)).toBe('hours');
    expect(resolveWorkspaceTab('?tab=leave', opts)).toBe('hours');
  });

  it('falls back silently for an unknown tab', () => {
    expect(resolveWorkspaceTab('?page=payroll&tab=nonsense', opts)).toBe('hours');
  });

  it('falls back for a tab the person may not use, and lands once it is allowed', () => {
    const url = '?page=payroll&tab=trial-run';
    expect(resolveWorkspaceTab(url, { ...opts, allowed: ['hours', 'checks', 'leave'] })).toBe('hours');
    expect(resolveWorkspaceTab(url, { ...opts, allowed: TABS })).toBe('trial-run');
  });

  it('uses the first allowed tab when the default itself is not allowed', () => {
    expect(resolveWorkspaceTab('?page=payroll', { ...opts, allowed: ['checks', 'leave'] })).toBe('checks');
  });
});

describe('taking over the address bar on mount', () => {
  it('drops another page’s params', () => {
    expect(ownPageSearch('?page=ent-roster&performance=p1&venue=v1', 'payroll')).toBe('?page=payroll');
    expect(ownPageSearch('', 'payroll')).toBe('?page=payroll');
  });

  it('leaves its own URL alone', () => {
    expect(ownPageSearch('?page=payroll&tab=leave&venue=v1', 'payroll')).toBeNull();
  });
});

describe('switching tab', () => {
  it('clears the tab-scoped params and keeps venue and week', () => {
    const next = new URLSearchParams(tabSwitchSearch('?page=payroll&tab=hours&view=security&run=r1&venue=v1&week=2026-10-12', 'payroll', 'leave'));
    expect(Object.fromEntries(next)).toEqual({ page: 'payroll', tab: 'leave', venue: 'v1', week: '2026-10-12' });
  });

  it('clears the extra params a page names', () => {
    const next = new URLSearchParams(tabSwitchSearch('?page=payroll&tab=hours&entity=e1', 'payroll', 'checks', { tabParams: ['entity'] }));
    expect(next.has('entity')).toBe(false);
  });

  it('applies the params handed with the switch after clearing', () => {
    const next = new URLSearchParams(tabSwitchSearch('?page=entertainers&tab=roster&performance=old', 'entertainers', 'payments', { params: { performance: 'p2', venue: undefined } }));
    expect(next.get('performance')).toBe('p2');
    expect(next.get('tab')).toBe('payments');
  });
});

describe('setting params', () => {
  it('sets, and deletes on undefined or empty', () => {
    expect(withParams('?page=payroll&venue=v1&week=w', { venue: 'v2', week: undefined, show: '' })).toBe('?page=payroll&venue=v2');
  });
});
