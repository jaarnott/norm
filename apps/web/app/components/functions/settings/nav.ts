/**
 * Links out of Functions Settings, on the same contract as the other
 * Functions pages: a cancelable `norm:open-page` / `norm:open-settings`
 * event the app shell may handle in place (it calls preventDefault),
 * otherwise a plain deep link.
 */

type Params = Record<string, string | null | undefined>;

/** `/app?page=functions-<page>&…` (blank values left out). */
export function fnPageHref(page: string, params: Params = {}): string {
  const sp = new URLSearchParams({ page: `functions-${page}` });
  for (const [k, v] of Object.entries(params)) if (v) sp.set(k, v);
  return `/app?${sp.toString()}`;
}

/** Open another Functions page (the enquiry a web form created, say). */
export function openFnPage(page: string, params: Params = {}): void {
  if (typeof window === 'undefined') return;
  const detail = { page: `functions-${page}`, params };
  const handled = !window.dispatchEvent(new CustomEvent('norm:open-page', { detail, cancelable: true }));
  if (!handled) window.location.assign(fnPageHref(page, params));
}

/**
 * Ask the app to open Norm's own Settings at a tab (Company, Venues).
 * Returns false when nothing handled it — the caller then says where to go.
 */
export function openNormSettings(tab: 'company' | 'venues'): boolean {
  if (typeof window === 'undefined') return false;
  return !window.dispatchEvent(new CustomEvent('norm:open-settings', { detail: { tab }, cancelable: true }));
}

/** Keep `?page=functions-settings&view=<tab>` in the address bar (replacing
 *  the entry, never navigating) so a refresh or a copied link comes back. */
export function setSettingsUrl(params: Params): void {
  if (typeof window === 'undefined') return;
  try {
    const url = new URL(window.location.href);
    url.searchParams.set('page', 'functions-settings');
    for (const [k, v] of Object.entries(params)) {
      if (v) url.searchParams.set(k, v);
      else url.searchParams.delete(k);
    }
    const next = `${url.pathname}?${url.searchParams.toString()}${url.hash}`;
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(window.history.state, '', next);
    }
  } catch {
    /* the address bar is a convenience — never break the page over it */
  }
}
