/**
 * Moving between Functions pages, and this page's own URL.
 *
 * `openFnPage` follows F6's contract (build-notes): a cancelable
 * `norm:open-page` event the app shell may handle in place, else a full load
 * of the deep link (the app reads `?page=` on load). Inside the Events page,
 * the open event and the list/calendar choice live in the URL so a refresh or
 * a shared link lands on the same view.
 */

export function openFnPage(page: string, params: Record<string, string> = {}): void {
  if (typeof window === 'undefined') return;
  const event = new CustomEvent('norm:open-page', { detail: { page, params }, cancelable: true });
  const handled = !window.dispatchEvent(event);
  if (!handled) {
    const search = new URLSearchParams({ page, ...params }).toString();
    window.location.assign(`/app?${search}`);
  }
}

export const enquiryLink = (enquiryId: string) => openFnPage('functions-enquiries', { enquiry: enquiryId });
export const contactLink = (contactId: string) => openFnPage('functions-contacts', { contact: contactId });
export const threadLink = (accountId: string, gmailThreadId: string) =>
  openFnPage('functions-inbox', { mail_thread: `${accountId}:${gmailThreadId}` });
export const mailboxesLink = () => openFnPage('functions-settings', { view: 'mailboxes' });

/** Set or remove this page's own params (`event`, `view`) in the address bar.
 *  `push` adds a history entry, so the browser's Back closes an event. */
export function setPageParams(update: Record<string, string | null>, push = false): void {
  if (typeof window === 'undefined') return;
  try {
    const url = new URL(window.location.href);
    for (const [k, v] of Object.entries(update)) {
      if (v === null || v === '') url.searchParams.delete(k);
      else url.searchParams.set(k, v);
    }
    if (url.href === window.location.href) return;
    const state = { ...(window.history.state ?? {}), fnEvents: true };
    if (push) window.history.pushState(state, '', url);
    else window.history.replaceState(window.history.state, '', url);
  } catch {
    /* an unusual URL — the view still works, it just isn't in the address bar */
  }
}
