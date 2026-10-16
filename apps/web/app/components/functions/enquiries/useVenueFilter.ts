'use client';

/**
 * The venue filter the Functions pages share (Dashboard, Enquiries, Reports):
 * 'all', 'unassigned' (group-wide enquiries, which have no venue) or one
 * venue id — remembered per person in this browser, so choosing La Zeppa on
 * the dashboard carries over to the enquiries and reports.
 *
 * Kept apart from the app-wide page venue (hooks/useActiveVenue): that one is
 * always a single venue, while Functions is read across venues by default.
 */
import { useCallback, useMemo, useState } from 'react';
import { getStoredUser } from '../../../lib/api';
import type { FnMeta } from '../shared/meta';

const KEY = 'norm_fn_venue';

export const UNASSIGNED = 'unassigned';

function read(): string | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const { userId, venue } = JSON.parse(raw);
    return userId && userId === getStoredUser()?.id && typeof venue === 'string' ? venue : null;
  } catch {
    return null;
  }
}

function write(venue: string): void {
  try {
    const userId = getStoredUser()?.id;
    if (userId) localStorage.setItem(KEY, JSON.stringify({ userId, venue }));
  } catch {
    /* storage unavailable — the filter still works for this visit */
  }
}

/** `[venue, setVenue, options]` — `options` are the picker's venues (the
 *  caller's, plus "Group-wide enquiries" when `withUnassigned`). */
export function useVenueFilter(meta: FnMeta | null, withUnassigned = true): [string, (v: string) => void, { id: string; name: string }[]] {
  const [chosen, setChosen] = useState<string | null>(() => (typeof window === 'undefined' ? null : read()));
  const options = useMemo(() => {
    const venues = (meta?.venues ?? []).map((v) => ({ id: v.id, name: v.name }));
    return withUnassigned ? [...venues, { id: UNASSIGNED, name: 'Group-wide enquiries' }] : venues;
  }, [meta, withUnassigned]);
  // A remembered venue the person no longer has falls back to all venues.
  const venue = chosen && (chosen === 'all' || options.some((o) => o.id === chosen)) ? chosen : 'all';
  const setVenue = useCallback((v: string) => {
    setChosen(v);
    write(v);
  }, []);
  return [venue, setVenue, options];
}
