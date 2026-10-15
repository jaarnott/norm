/**
 * Ask the page to fold the thread panel away, or hand it back.
 *
 * A view that needs the width — the invoice copy beside the receive card
 * (14 Oct 2026) — sits deep inside a thread, a dashboard or Settings, far
 * from the page that owns the panel; a window event reaches it from any of
 * them. The page only reopens a panel it folded for this, so a panel the
 * person had already closed stays closed.
 */

export const SIDE_PANEL_EVENT = 'norm:side-panel';

export type SidePanelRequest = { collapse: boolean };

export function requestSidePanel(collapse: boolean): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(
    new CustomEvent<SidePanelRequest>(SIDE_PANEL_EVENT, { detail: { collapse } }),
  );
}
