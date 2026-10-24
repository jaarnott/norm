/**
 * Entertainers — what the workspace page (EntertainersPage) hands each tab
 * body: the one meta, the shared venue, this page's URL params and the
 * header's primary action. The tab bodies draw no header, banner or toast
 * host of their own; the page draws one of each.
 */
import type { EntMeta } from './types';

export const ENT_TABS = ['roster', 'performers', 'payments', 'emails'] as const;
export type EntTab = (typeof ENT_TABS)[number];

/** The tab names people read (the strip and the page document). */
export const ENT_TAB_LABEL: Record<EntTab, string> = {
  roster: 'Roster',
  performers: 'Performers',
  payments: 'Payments',
  emails: 'Emails',
};

/** The tabs this person may open: Payments only with the payroll permission. */
export function entTabsAllowed(meta: Pick<EntMeta, 'can_pay'> | null): EntTab[] {
  return ENT_TABS.filter((t) => t !== 'payments' || !!meta?.can_pay);
}

export interface EntTabProps {
  meta: EntMeta;
  /** Re-read the meta (counts, settings) after a write that moves them. */
  reload: () => void;
  /** A menu page (`persistVenue`), not the same screen inside a conversation. */
  isPage: boolean;
  /** The shared venue: a venue id, or 'all'. Performers ignores it. */
  venueId: string;
  setVenueId: (venueId: string) => void;
  /** One of this page's URL params (`performance`, `performer`). */
  param: (name: string) => string | null;
  /** Set or delete this page's URL params (replaceState). */
  setParams: (p: Record<string, string | undefined>) => void;
  /** The header's primary action (Book performer, Add performer) was pressed. */
  primaryOpen: boolean;
  closePrimary: () => void;
}
