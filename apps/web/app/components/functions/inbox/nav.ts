/**
 * Norm Functions Inbox — where the page is, kept in the address bar so a
 * thread or a proposal can be linked to and survives a reload:
 * `?page=functions-inbox&mail_thread=<account>:<thread>`,
 * `&view=proposal&enquiry=<id>`, `&view=compose[&enquiry=<id>]`.
 * Other pages open the way the app opens staff links: a fresh load of
 * `?page=<id>&…` (app/page.tsx reads `?page=` on load).
 */
import { mailThreadParam, parseMailThreadParam } from './text';

export type InboxRoute =
  | { view: 'list' }
  | { view: 'thread'; accountId: string; threadId: string }
  | { view: 'proposal'; enquiryId: string }
  | { view: 'compose'; enquiryId: string | null };

export const INBOX_PAGE = 'functions-inbox';
const OWN_PARAMS = ['mail_thread', 'view', 'enquiry'];

export function routeFromSearch(search: string): InboxRoute {
  const sp = new URLSearchParams(search);
  const mail = parseMailThreadParam(sp.get('mail_thread'));
  if (mail) return { view: 'thread', ...mail };
  const view = sp.get('view');
  const enquiry = sp.get('enquiry');
  if (view === 'proposal' && enquiry) return { view: 'proposal', enquiryId: enquiry };
  if (view === 'compose') return { view: 'compose', enquiryId: enquiry || null };
  return { view: 'list' };
}

export function searchForRoute(current: string, route: InboxRoute): string {
  const sp = new URLSearchParams(current);
  for (const k of OWN_PARAMS) sp.delete(k);
  sp.set('page', INBOX_PAGE);
  if (route.view === 'thread') sp.set('mail_thread', mailThreadParam(route.accountId, route.threadId));
  if (route.view === 'proposal') {
    sp.set('view', 'proposal');
    sp.set('enquiry', route.enquiryId);
  }
  if (route.view === 'compose') {
    sp.set('view', 'compose');
    if (route.enquiryId) sp.set('enquiry', route.enquiryId);
  }
  return `?${sp.toString()}`;
}

/** The address bar once the Inbox closes (another page opened from the menu):
 *  without its params, so a reload doesn't reopen the Inbox. */
export function searchWithoutInbox(current: string): string {
  const sp = new URLSearchParams(current);
  if (sp.get('page') !== INBOX_PAGE) return current;
  sp.delete('page');
  for (const k of OWN_PARAMS) sp.delete(k);
  const s = sp.toString();
  return s ? `?${s}` : '';
}

/** Open another Functions page (an enquiry, an event, Settings), by F6's
 *  contract: a cancelable `norm:open-page` event the app shell may handle in
 *  place, else a fresh load of the deep link (which always works). */
export function openPage(page: string, params: Record<string, string> = {}): void {
  if (typeof window === 'undefined') return;
  const event = new CustomEvent('norm:open-page', { detail: { page, params }, cancelable: true });
  if (window.dispatchEvent(event)) {
    window.location.assign(`/app?${new URLSearchParams({ page, ...params }).toString()}`);
  }
}

export const openEnquiry = (id: string) => openPage('functions-enquiries', { enquiry: id });
export const openEvent = (id: string) => openPage('functions-events', { event: id });
export const openMailboxSettings = () => openPage('functions-settings', { view: 'mailboxes' });
