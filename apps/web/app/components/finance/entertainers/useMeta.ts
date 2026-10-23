'use client';

/**
 * Entertainers — `GET /api/entertainers/meta`, loaded once and shared by the
 * four pages: the module's mode, whether the caller may pay (payroll:write),
 * the caller's venues, the vocabularies, the settings and the counts the
 * headers show. `off` answers 404 ("not switched on"), which the pages show
 * as a plain message rather than an error.
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

/** A URL search param from the current location (`?page=ent-roster&performance=<id>`). */
export function readPageParam(name: string): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return new URLSearchParams(window.location.search).get(name);
  } catch {
    return null;
  }
}

/** Set or remove one of this page's own params in the address bar. */
export function setPageParam(name: string, value: string | null): void {
  if (typeof window === 'undefined') return;
  try {
    const url = new URL(window.location.href);
    if (value === null || value === '') url.searchParams.delete(name);
    else url.searchParams.set(name, value);
    if (url.href !== window.location.href) window.history.replaceState(window.history.state, '', url);
  } catch {
    /* an unusual URL — the view still works */
  }
}

/** Open another Norm page in place (the app shell handles `norm:open-page`),
 *  else load the deep link. */
export function openNormPage(page: string, params: Record<string, string> = {}): void {
  if (typeof window === 'undefined') return;
  const event = new CustomEvent('norm:open-page', { detail: { page, params }, cancelable: true });
  const handled = !window.dispatchEvent(event);
  if (!handled) {
    const search = new URLSearchParams({ page, ...params }).toString();
    window.location.assign(`/app?${search}`);
  }
}
