/**
 * Norm Functions — moving between Functions pages by deep link.
 *
 * Every page is reachable as `/app?page=functions-<x>&<its params>` (page.tsx
 * opens `?page=`; each page reads its own params). `openFnPage` first offers
 * the move to the app shell as a cancelable `norm:open-page` event — a
 * listener that handles it in place calls preventDefault — and otherwise
 * loads the deep link, which always works.
 *
 * `setUrlParams` keeps the open record in the address bar (so a refresh or a
 * copied link comes back to it) without navigating.
 */

export type FnPage = 'dashboard' | 'enquiries' | 'events' | 'inbox' | 'contacts' | 'reports' | 'settings';

export const OPEN_PAGE_EVENT = 'norm:open-page';

type Params = Record<string, string | number | boolean | null | undefined>;

/** `/app?page=functions-enquiries&enquiry=…` — blank values are left out. */
export function fnPageHref(page: FnPage, params: Params = {}): string {
  const sp = new URLSearchParams({ page: `functions-${page}` });
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '' || v === false) continue;
    sp.set(k, v === true ? '1' : String(v));
  }
  return `/app?${sp.toString()}`;
}

/** Open another Functions page (or this one afresh) at a deep link. */
export function openFnPage(page: FnPage, params: Params = {}): void {
  if (typeof window === 'undefined') return;
  const detail = { page: `functions-${page}`, params };
  const handled = !window.dispatchEvent(new CustomEvent(OPEN_PAGE_EVENT, { detail, cancelable: true }));
  if (!handled) window.location.assign(fnPageHref(page, params));
}

/** The inbox thread deep link for a mail thread linked to an enquiry. */
export function mailThreadParams(accountId: string, gmailThreadId: string): Params {
  return { mail_thread: `${accountId}:${gmailThreadId}` };
}

/**
 * Put this page and these params in the address bar (replacing, not adding a
 * history entry); a null value removes that param. Other params are kept
 * unless `only` is set, which drops every param but `page` first.
 */
export function setUrlParams(page: FnPage, params: Params, only = false): void {
  if (typeof window === 'undefined') return;
  try {
    const url = new URL(window.location.href);
    const sp = only ? new URLSearchParams() : url.searchParams;
    sp.set('page', `functions-${page}`);
    for (const [k, v] of Object.entries(params)) {
      if (v === null || v === undefined || v === '' || v === false) sp.delete(k);
      else sp.set(k, v === true ? '1' : String(v));
    }
    const qs = sp.toString();
    const next = `${url.pathname}${qs ? `?${qs}` : ''}${url.hash}`;
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(window.history.state, '', next);
    }
  } catch {
    /* the address bar is a convenience — never break the page over it */
  }
}
