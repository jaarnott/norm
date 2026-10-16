'use client';

/**
 * Small data hooks for the Events page.
 *
 * `useLoad` keys a fetch on a string: a new key fetches again, `reload()`
 * refetches the same key while the last data stays on screen, and `setData`
 * puts a write's response in place (every Functions write returns the fresh
 * object). State is set only in the fetch's callbacks, never synchronously in
 * the effect, so "loading" is derived (the key or reload tick we have data
 * for isn't the one asked for) rather than stored.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { FnApiError } from '../shared/api';

interface Loaded<T> {
  key: string;
  tick: number;
  data?: T;
  error?: unknown;
}

export function useLoad<T>(key: string | null, fetcher: () => Promise<T>) {
  const [state, setState] = useState<Loaded<T> | null>(null);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    if (key === null) return;
    let alive = true;
    fetcherRef.current().then(
      (data) => {
        if (alive) setState({ key, tick, data });
      },
      (error: unknown) => {
        // A failed reload keeps what was on screen; the error shows beside it.
        if (alive) setState((prev) => ({ key, tick, data: prev && prev.key === key ? prev.data : undefined, error }));
      },
    );
    return () => {
      alive = false;
    };
  }, [key, tick]);

  const current = state && state.key === key ? state : null;
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setData = useCallback(
    (data: T) => {
      if (key !== null) setState({ key, tick, data });
    },
    [key, tick],
  );

  return {
    data: current?.data,
    error: current && current.tick === tick ? current.error : undefined,
    /** Nothing for this key yet (first load or a new key). */
    loading: key !== null && !current,
    /** A reload is on its way while the last data is still shown. */
    refreshing: key !== null && !!current && current.tick !== tick,
    reload,
    setData,
  };
}

/** What to tell a person when a call failed: the API's own sentence (it is
 *  written for people), or the read-only reason in shadow mode. */
export function errorText(e: unknown): string {
  if (e instanceof FnApiError) {
    if (e.notLive) return 'Norm Functions is read-only while Orbit is live.';
    return e.message || `Something went wrong (${e.status}).`;
  }
  if (e instanceof Error && e.message) return e.message;
  return 'Something went wrong. Please try again.';
}

/** A 409 that a reload fixes (someone else saved first), not the read-only one. */
export function isStale(e: unknown): boolean {
  return e instanceof FnApiError && e.conflict;
}
