'use client';

/**
 * The page's get_sales answers, cached by what was asked: switching tab,
 * comparison or back to a venue already seen doesn't ask Loaded again (each
 * call takes 3–25 s). Refresh drops the cache; an answer for a request made
 * before the refresh is ignored when it lands.
 */

import { useCallback, useRef, useState } from 'react';
import { apiFetch } from '../../../lib/api';
import { readDoc } from './salesLogic';

export interface Entry<T> {
  loading: boolean;
  doc: T | null;
  error: string | null;
}

const LOADING: Entry<never> = { loading: true, doc: null, error: null };

/** One get_sales call through the page route (venue_id picks the venue's own
 *  Loaded login, strictly). `field` is the array the answer must carry. */
export async function getSales<T>(venueId: string, params: Record<string, unknown>, field = 'rows'): Promise<Entry<T>> {
  try {
    const res = await apiFetch('/api/connectors/loadedhub/execute/get_sales', {
      method: 'POST',
      body: JSON.stringify({ params: { venue_id: venueId, ...params } }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { loading: false, doc: null, error: body?.detail || body?.error || `Loaded didn't answer (${res.status})` };
    }
    const { doc, error } = readDoc<T>(body, field);
    return { loading: false, doc, error: doc ? null : (error || (typeof body?.error === 'string' ? body.error : null)) };
  } catch (e) {
    return { loading: false, doc: null, error: e instanceof Error ? e.message : 'Could not reach Loaded' };
  }
}

export function useSalesDocs() {
  const [entries, setEntries] = useState<Record<string, Entry<unknown>>>({});
  const started = useRef<Set<string>>(new Set());
  const generation = useRef(0);

  /** Ask once per key; later calls with the same key are no-ops. */
  const load = useCallback((key: string, venueId: string, params: Record<string, unknown>) => {
    if (started.current.has(key)) return;
    started.current.add(key);
    const gen = generation.current;
    setEntries((e) => ({ ...e, [key]: LOADING }));
    void getSales(venueId, params).then((entry) => {
      if (gen !== generation.current) return;
      setEntries((e) => ({ ...e, [key]: entry }));
    });
  }, []);

  /** Forget one answer (after a change) or all of them (Refresh). */
  const forget = useCallback((key?: string) => {
    if (key) {
      started.current.delete(key);
      setEntries((e) => {
        const next = { ...e };
        delete next[key];
        return next;
      });
      return;
    }
    generation.current += 1;
    started.current = new Set();
    setEntries({});
  }, []);

  const get = useCallback(<T,>(key: string | null): Entry<T> | null => (key ? (entries[key] as Entry<T> | undefined) ?? null : null), [entries]);

  return { load, forget, get };
}
