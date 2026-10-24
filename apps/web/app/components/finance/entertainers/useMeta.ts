'use client';

/**
 * Entertainers — `GET /api/entertainers/meta`, loaded once by the
 * Entertainers page and handed to its four tabs: the module's mode,
 * whether the caller may pay (payroll:write), the caller's venues, the
 * vocabularies, the settings and the counts the header shows. `off` answers 404 ("not switched on"), which the page shows
 * as a plain message rather than an error. The page's URL params (tab,
 * venue, `performance`, `performer`) live in useWorkspaceTab.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { EntApiError, entGet } from './api';
import type { EntMeta } from './types';

let cached: Promise<EntMeta> | null = null;

export function loadEntMeta(force = false): Promise<EntMeta> {
  if (!cached || force) {
    cached = entGet<EntMeta>('/meta').catch((err) => {
      cached = null; // let the next caller retry
      throw err;
    });
  }
  return cached;
}

/** Drop the cached counts after a write that moves them (approve, accept…). */
export function invalidateEntMeta(): void {
  cached = null;
}

export function useEntMeta(): {
  meta: EntMeta | null;
  error: string | null;
  /** The organisation has entertainers switched off (404 from the API). */
  off: boolean;
  loading: boolean;
  reload: () => void;
} {
  const [meta, setMeta] = useState<EntMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [off, setOff] = useState(false);
  const [tick, setTick] = useState(0);
  const first = useRef(true);

  useEffect(() => {
    let alive = true;
    const force = !first.current;
    first.current = false;
    loadEntMeta(force)
      .then((m) => {
        if (alive) {
          setMeta(m);
          setError(null);
          setOff(false);
        }
      })
      .catch((e: unknown) => {
        if (!alive) return;
        if (e instanceof EntApiError && e.status === 404) setOff(true);
        else setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, [tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { meta, error, off, loading: !meta && !error && !off, reload };
}
