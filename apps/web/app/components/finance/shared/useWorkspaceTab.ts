'use client';

/**
 * The tab a Finance workspace page (Payroll, Entertainers, Budgets) shows,
 * kept in the address bar: `?page=payroll&tab=leave&venue=…&week=…`.
 *
 * Rules (inventory S06):
 * - `tab` is read only when `?page=` is this page's own id. A menu click
 *   never rewrites the URL, so another page's params can linger there; when
 *   the page mounts under a different `?page=`, the URL is replaced with
 *   `?page=<own id>` and every stale param is dropped (a stale
 *   `?performance=` must never reopen a dialog after a menu switch).
 * - Switching tab pushes a history entry, so Back and Forward move between
 *   tabs; a refresh keeps the tab.
 * - Switching tab deletes the tab-scoped params (`view`, `run`,
 *   `performance`, `performer`, `panel`, `show`, `section`, `settings`, plus
 *   any in `tabParams`) and keeps everything else — `venue` and `week` stay
 *   chosen across tabs.
 * - A requested tab that isn't in `tabs`, or not in `allowed`, falls back
 *   silently to `defaultTab` (or the first allowed tab). The URL is NOT
 *   rewritten on a fallback: `allowed` often grows once the page's meta has
 *   loaded (Trial run, Payments), and the deep link must still land then.
 * - `syncUrl: false` (the same component rendered inside a conversation)
 *   keeps all of this in local state and never touches the address bar.
 *
 * In-page cross-links go through `WorkspaceTabContext` (`useOpenTab`), which
 * falls back to `openNormPage` when the caller isn't inside that workspace —
 * a dialog shared between pages, or a page rendered in chat.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { setPageDocument } from '../../../lib/pageDocument';

/** Params that belong to one tab and are cleared when the tab changes. */
export const DEFAULT_TAB_PARAMS: readonly string[] = ['view', 'run', 'performance', 'performer', 'panel', 'show', 'section', 'settings'];

export type ParamUpdate = Record<string, string | undefined | null>;

function toParams(search: string | URLSearchParams): URLSearchParams {
  return new URLSearchParams(typeof search === 'string' ? search : search.toString());
}

/** `?a=1` style, or '' when there is nothing. */
function toSearch(sp: URLSearchParams): string {
  const s = sp.toString();
  return s ? `?${s}` : '';
}

/** The tab to show for this URL: honoured only on the page's own URL, and
 *  only when known and allowed; otherwise the default (or first allowed). */
export function resolveWorkspaceTab<T extends string>(
  search: string | URLSearchParams,
  opts: { pageId: string; tabs: readonly T[]; allowed?: readonly T[]; defaultTab: T },
): T {
  const sp = toParams(search);
  const usable = (opts.allowed ?? opts.tabs).filter((t) => opts.tabs.includes(t));
  const requested = sp.get('page') === opts.pageId ? sp.get('tab') : null;
  if (requested && usable.includes(requested as T)) return requested as T;
  if (usable.includes(opts.defaultTab)) return opts.defaultTab;
  return usable[0] ?? opts.defaultTab;
}

/** The search string to replace the URL with on mount: `?page=<own id>`
 *  when the URL belongs to another page (stale params dropped), else null. */
export function ownPageSearch(search: string | URLSearchParams, pageId: string): string | null {
  const sp = toParams(search);
  if (sp.get('page') === pageId) return null;
  return toSearch(new URLSearchParams({ page: pageId }));
}

/** Set or delete params (`undefined`, `null` or '' deletes). */
export function withParams(search: string | URLSearchParams, update: ParamUpdate): string {
  const sp = toParams(search);
  for (const [k, v] of Object.entries(update)) {
    if (v === undefined || v === null || v === '') sp.delete(k);
    else sp.set(k, v);
  }
  return toSearch(sp);
}

/** The search string after switching to `tab`: own page id, the tab, the
 *  tab-scoped params cleared, `params` applied, everything else kept. */
export function tabSwitchSearch(
  search: string | URLSearchParams,
  pageId: string,
  tab: string,
  opts: { tabParams?: readonly string[]; params?: ParamUpdate } = {},
): string {
  const sp = toParams(search);
  for (const k of new Set([...DEFAULT_TAB_PARAMS, ...(opts.tabParams ?? [])])) sp.delete(k);
  sp.set('page', pageId);
  sp.set('tab', tab);
  return withParams(sp, opts.params ?? {});
}

// ── navigation ─────────────────────────────────────────────────────────────

/** Open another Norm page: a cancelable `norm:open-page` the app shell
 *  handles in place (old page ids are translated there), else a full load of
 *  the deep link. */
export function openNormPage(page: string, params: Record<string, string | undefined> = {}): void {
  if (typeof window === 'undefined') return;
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') clean[k] = v;
  const event = new CustomEvent('norm:open-page', { detail: { page, params: clean }, cancelable: true });
  const handled = !window.dispatchEvent(event);
  if (!handled) window.location.assign(`/app?${new URLSearchParams({ page, ...clean }).toString()}`);
}

export interface WorkspaceTabContextValue {
  /** The workspace's page id ('payroll', 'entertainers', 'finance-budgets'). */
  pageId: string;
  /** Switch tab in place (pushes history; tab-scoped params cleared, then `params` applied). */
  openTab: (tab: string, params?: ParamUpdate) => void;
}

export const WorkspaceTabContext = createContext<WorkspaceTabContextValue | null>(null);

/** A cross-link to a tab of `page`: in place when inside that workspace,
 *  else `openNormPage(page, { tab, ...params })`. */
export function useOpenTab(page: string): (tab: string, params?: Record<string, string | undefined>) => void {
  const ctx = useContext(WorkspaceTabContext);
  return useCallback((tab: string, params: Record<string, string | undefined> = {}) => {
    if (ctx && ctx.pageId === page) ctx.openTab(tab, params);
    else openNormPage(page, { ...params, tab });
  }, [ctx, page]);
}

// ── the hook ───────────────────────────────────────────────────────────────

export interface WorkspaceTab<T extends string> {
  tab: T;
  setTab: (t: T, params?: Record<string, string | undefined>) => void;
  /** A param of this page's URL (null when absent). */
  param: (name: string) => string | null;
  /** Set or delete params; `replace` (default true) uses replaceState, false pushes history. */
  setParams: (p: Record<string, string | undefined>, replace?: boolean) => void;
  /** Ready-made value for `<WorkspaceTabContext.Provider value={…}>`. */
  context: WorkspaceTabContextValue;
}

export function useWorkspaceTab<T extends string>(opts: {
  pageId: string;
  tabs: readonly T[];
  allowed?: readonly T[];
  defaultTab: T;
  /** Extra tab-scoped params, cleared on a tab switch (added to DEFAULT_TAB_PARAMS). */
  tabParams?: string[];
  /** false inside a conversation: state only, the address bar is untouched. Default true. */
  syncUrl?: boolean;
}): WorkspaceTab<T> {
  const { pageId, tabs, allowed, defaultTab, tabParams, syncUrl = true } = opts;
  const [search, setSearch] = useState<string>(() => {
    if (!syncUrl || typeof window === 'undefined') return toSearch(new URLSearchParams({ page: pageId }));
    return ownPageSearch(window.location.search, pageId) ?? window.location.search;
  });

  // The address bar belongs to this page from now on; Back/Forward move tabs.
  useEffect(() => {
    if (!syncUrl || typeof window === 'undefined') return;
    const own = ownPageSearch(window.location.search, pageId);
    if (own !== null) {
      try { window.history.replaceState(window.history.state, '', `${window.location.pathname}${own}`); } catch { /* unusual URL */ }
      setSearch(own);
    }
    const onPop = () => setSearch(window.location.search);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [syncUrl, pageId]);

  const write = useCallback((next: string, push: boolean) => {
    setSearch(next);
    if (!syncUrl || typeof window === 'undefined') return;
    if (next === window.location.search) return;
    try {
      const url = `${window.location.pathname}${next}`;
      if (push) window.history.pushState(window.history.state, '', url);
      else window.history.replaceState(window.history.state, '', url);
    } catch {
      /* an unusual URL — the tab still switches, it just isn't in the address bar */
    }
  }, [syncUrl]);

  const current = useCallback(() => (syncUrl && typeof window !== 'undefined' ? window.location.search : search), [syncUrl, search]);

  // Joined so a fresh array literal from the caller doesn't change identity every render.
  const tabsKey = tabs.join('|');
  const allowedKey = allowed ? allowed.join('|') : null;
  const tab = useMemo(
    () => resolveWorkspaceTab(search, { pageId, tabs, allowed, defaultTab }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [search, pageId, tabsKey, allowedKey, defaultTab],
  );

  const tabParamsKey = (tabParams ?? []).join('|');
  const setTab = useCallback((t: T, params: Record<string, string | undefined> = {}) => {
    write(tabSwitchSearch(current(), pageId, t, { tabParams, params }), true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [write, current, pageId, tabParamsKey]);

  const setParams = useCallback((p: Record<string, string | undefined>, replace = true) => {
    write(withParams(current(), p), !replace);
  }, [write, current]);

  const param = useCallback((name: string) => toParams(search).get(name), [search]);

  const context = useMemo<WorkspaceTabContextValue>(
    () => ({ pageId, openTab: (t, params) => setTab(t as T, (params ?? {}) as Record<string, string | undefined>) }),
    [pageId, setTab],
  );

  return { tab, setTab, param, setParams, context };
}

/**
 * Tell Norm which part of the job is open, so a chat message sent from here
 * carries it (`page_context`): `{kind: 'finance_tab', page, tab, label,
 * venue_id, week_start}`. Cleared on unmount. `enabled: false` (a page
 * rendered inside a conversation) publishes nothing.
 */
export function usePublishFinanceTab(doc: {
  page: string;
  tab: string;
  label: string;
  venue_id?: string | null;
  week_start?: string | null;
  enabled?: boolean;
}): void {
  const { page, tab, label, venue_id = null, week_start = null, enabled = true } = doc;
  useEffect(() => {
    if (!enabled) return;
    setPageDocument({ kind: 'finance_tab', page, tab, label, venue_id, week_start });
    return () => setPageDocument(null);
  }, [enabled, page, tab, label, venue_id, week_start]);
}
