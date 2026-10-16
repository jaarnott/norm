'use client';

/**
 * The venue's Functions profile (`GET /venues/{id}/profile`: settings, areas
 * with photos, brand, image library) — shared by the Venues and Brand tabs.
 * Writes patch it in place from their responses (`setProfile`); `reload`
 * refetches quietly, keeping what's on screen until the new copy arrives.
 */
import { useCallback, useEffect, useState } from 'react';
import { fnGet } from '../shared/api';
import { errorText } from './errors';
import type { VenueProfile } from './types';

interface State {
  venueId: string;
  profile: VenueProfile | null;
  error: string | null;
}

export function useVenueProfile(venueId: string | null) {
  const [state, setState] = useState<State | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!venueId) return;
    let alive = true;
    fnGet<VenueProfile>(`/venues/${venueId}/profile`).then(
      (profile) => alive && setState({ venueId, profile, error: null }),
      (e) => alive && setState((s) => ({ venueId, profile: s?.venueId === venueId ? s.profile : null, error: errorText(e) })),
    );
    return () => {
      alive = false;
    };
  }, [venueId, tick]);

  const current = state && state.venueId === venueId ? state : null;
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setProfile = useCallback(
    (update: (p: VenueProfile) => VenueProfile) =>
      setState((s) => (s && s.profile ? { ...s, profile: update(s.profile), error: null } : s)),
    [],
  );
  return {
    profile: current?.profile ?? null,
    error: current && !current.profile ? current.error : null,
    loading: !!venueId && !current,
    reload,
    setProfile,
  };
}
