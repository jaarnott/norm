'use client';

import { useCallback, useEffect, useState } from 'react';
import { getStoredUser } from '../lib/api';

// The user's last-selected venue, shared across every page-level venue selector
// and remembered between visits. This is deliberately PAGE-scoped state: it is
// never wired into message sending, so a conversation keeps whatever venue it
// was started with, independent of what the page selector currently shows.
//
// Persisted in localStorage keyed by user id, so switching accounts on the same
// browser never inherits the previous user's venue (and a venue the user has
// since lost access to is filtered out by each selector against its own list).

const KEY = 'norm_active_venue';
// Same-tab notification: <select>s in different components share the value live.
// `storage` events only fire in *other* tabs, so we need our own event too.
const EVENT = 'norm:active-venue';

function read(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const { userId, venueId } = JSON.parse(raw);
    return userId && userId === getStoredUser()?.id ? (venueId ?? null) : null;
  } catch {
    return null;
  }
}

function write(venueId: string): void {
  if (typeof window === 'undefined') return;
  const userId = getStoredUser()?.id;
  if (!userId) return;
  try {
    localStorage.setItem(KEY, JSON.stringify({ userId, venueId }));
    window.dispatchEvent(new CustomEvent(EVENT, { detail: venueId }));
  } catch {
    /* storage unavailable (e.g. a sandboxed iframe) — persistence is best-effort */
  }
}

/**
 * `[activeVenueId, setActiveVenue]` for the shared, persisted page venue.
 *
 * `activeVenueId` starts from storage (so the last choice is there on first
 * paint, no flash) and stays in sync when any other selector — this tab or
 * another — changes it. `setActiveVenue` updates every subscriber and persists.
 */
export function useActiveVenue(): [string | null, (venueId: string) => void] {
  // Lazy init is safe here: these selectors only mount client-side behind the
  // auth gate, never during the server render of the login screen, so there is
  // no server/client first-paint to mismatch.
  const [venueId, setVenueId] = useState<string | null>(() => read());

  useEffect(() => {
    const onLocal = (e: Event) => setVenueId((e as CustomEvent).detail as string);
    const onStorage = (e: StorageEvent) => {
      if (e.key === KEY) setVenueId(read());
    };
    window.addEventListener(EVENT, onLocal);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(EVENT, onLocal);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const setActiveVenue = useCallback((id: string) => {
    setVenueId(id);
    write(id);
  }, []);

  return [venueId, setActiveVenue];
}
