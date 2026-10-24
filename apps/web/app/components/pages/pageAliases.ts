/**
 * Old page ids → the page and tab that replaced them.
 *
 * The Finance menu went from 14 pages to four (Oct 2026): Payroll,
 * Entertainers and Budgets are now one page each, with tabs
 * (`?page=payroll&tab=leave`). Links to the old pages are already out there —
 * the closing manager's email (`?page=ent-roster&performance=<id>`), bookmarks,
 * Today items, cross-links between pages — so every place the app shell
 * resolves a page id (apps/web/app/app/page.tsx: `?page=` on load, the
 * `norm:open-page` event, Today items, findPage) runs it through here first.
 *
 * Rules: an unknown id passes through untouched; an explicit `tab` already in
 * the params wins; every other param (venue, week, performance, …) is carried.
 * The Time & Attendance page `roster` is NOT an old Entertainers id — only
 * `ent-roster` is. Budgets' own legacy `?view=` is translated inside the
 * Budgets page, because its id didn't change.
 */
import { legacyPayrunView } from '../finance/payroll/payrun/logic';

export interface PageAlias {
  page: string;
  params: Record<string, string>;
  aliased: boolean;
}

type Rule = (params: Record<string, string>) => { page: string; tab: string; set?: Record<string, string | undefined> };

/** venue='all' when no venue is given (the old page opened on All entities). */
const allEntitiesUnlessGiven = (p: Record<string, string>) => (p.venue ? {} : { venue: 'all' });

const ALIASES: Record<string, Rule> = {
  'payroll-payrun': (p) => {
    const { tab, view } = legacyPayrunView(p.view);
    return { page: 'payroll', tab, set: { view } };
  },
  'payroll-leave': () => ({ page: 'payroll', tab: 'leave' }),
  'payroll-intercompany': (p) => ({ page: 'payroll', tab: 'intercompany', set: allEntitiesUnlessGiven(p) }),
  'payroll-reconciliation': (p) => ({ page: 'payroll', tab: 'reconciliation', set: allEntitiesUnlessGiven(p) }),
  'payroll-settings': () => ({ page: 'payroll', tab: 'settings' }),
  'payroll-shadow': () => ({ page: 'payroll', tab: 'trial-run' }),
  'ent-roster': () => ({ page: 'entertainers', tab: 'roster' }),
  'ent-register': () => ({ page: 'entertainers', tab: 'performers' }),
  'ent-payments': () => ({ page: 'entertainers', tab: 'payments' }),
  'ent-emails': () => ({ page: 'entertainers', tab: 'emails' }),
  // Budget vs Loaded opened on its financial-year table.
  'finance-budget-vs-loaded': (p) => ({ page: 'finance-budgets', tab: 'daily', set: p.show ? {} : { show: 'year' } }),
  'finance-forecasting': () => ({ page: 'finance-budgets', tab: 'forecast' }),
};

/** The old page ids this table translates (for tests and docs). */
export const ALIASED_PAGE_IDS: readonly string[] = Object.keys(ALIASES);

function plain(params: URLSearchParams | Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  if (params instanceof URLSearchParams) params.forEach((v, k) => { out[k] = v; });
  else Object.assign(out, params);
  delete out.page;
  return out;
}

export function resolvePageAlias(id: string, params: URLSearchParams | Record<string, string> = {}): PageAlias {
  const carried = plain(params);
  const rule = Object.prototype.hasOwnProperty.call(ALIASES, id) ? ALIASES[id] : undefined;
  if (!rule) return { page: id, params: carried, aliased: false };
  const { page, tab, set } = rule(carried);
  const out: Record<string, string> = { ...carried };
  for (const [k, v] of Object.entries(set ?? {})) {
    if (v === undefined) delete out[k];
    else out[k] = v;
  }
  if (!carried.tab) out.tab = tab;
  return { page, params: out, aliased: true };
}

/** The page id an old id now opens (no params) — for lookups that only need the page. */
export function aliasPageId(id: string): string;
export function aliasPageId(id: string | null | undefined): string | null | undefined;
export function aliasPageId(id: string | null | undefined): string | null | undefined {
  return id ? resolvePageAlias(id).page : id;
}

/** `?page=new&tab=…&<carried params>` — page and tab first. */
export function canonicalSearch(alias: PageAlias): string {
  const sp = new URLSearchParams({ page: alias.page });
  if (alias.params.tab) sp.set('tab', alias.params.tab);
  for (const [k, v] of Object.entries(alias.params)) if (k !== 'tab') sp.set(k, v);
  return `?${sp.toString()}`;
}

/**
 * `?page=` on load: when the address bar names an old page, replace it with
 * the canonical URL (so the page's tabs read performance, run, view, venue,
 * week from it) and return the new id; any other id comes back unchanged.
 */
export function aliasPageInUrl(id: string | null): string | null {
  if (!id || typeof window === 'undefined') return id;
  const alias = resolvePageAlias(id, new URLSearchParams(window.location.search));
  if (!alias.aliased) return id;
  try {
    window.history.replaceState(window.history.state, '', `${window.location.pathname}${canonicalSearch(alias)}`);
  } catch {
    /* an unusual URL — the page still opens, on its default tab */
  }
  return alias.page;
}

/**
 * A `norm:open-page` {page, params} detail with an old page id translated:
 * the new page, with the alias's tab and params merged into the caller's.
 * Anything else comes back as it was.
 */
export function aliasOpenPageDetail<D extends { page?: string; params?: Record<string, unknown> }>(detail: D | undefined): D | undefined {
  if (!detail?.page) return detail;
  const given: Record<string, string> = {};
  for (const [k, v] of Object.entries(detail.params ?? {})) {
    if (v === null || v === undefined || v === '' || v === false) continue;
    given[k] = v === true ? '1' : String(v);
  }
  const alias = resolvePageAlias(detail.page, given);
  if (!alias.aliased) return detail;
  return { ...detail, page: alias.page, params: alias.params };
}
