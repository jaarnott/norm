'use client';

/**
 * `GET /api/finance/meta`, loaded once and shared by the Budget app's pages:
 * the mode, the fiscal-year start, the currency, the viewer's scopes and the
 * venues they may see (the server decides — a page never guesses).
 */
import { useCallback, useEffect, useState } from 'react';
import { useActiveVenue } from '../../../hooks/useActiveVenue';
import { FinApiError, errorText, finGet } from '../shared/useFinanceApi';
import type { FinMeta } from './types';

let cached: Promise<FinMeta> | null = null;

export function loadFinanceMeta(force = false): Promise<FinMeta> {
  if (!cached || force) {
    cached = finGet<FinMeta>('/api/finance/meta').catch((err) => {
      cached = null; // let the next caller retry
      throw err;
    });
  }
  return cached;
}

export function useFinanceMeta(): { meta: FinMeta | null; error: string | null; status: number | null; reload: () => void } {
  const [meta, setMeta] = useState<FinMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    loadFinanceMeta(tick > 0)
      .then((m) => {
        if (alive) {
          setMeta(m);
          setError(null);
          setStatus(null);
        }
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(errorText(e));
        setStatus(e instanceof FinApiError ? e.status : null);
      });
    return () => {
      alive = false;
    };
  }, [tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { meta, error, status, reload };
}

/** Does the viewer hold an org scope (`budgets:write`)? From the meta's own
 *  list, so it agrees with what the API will refuse. */
export function holds(meta: FinMeta | null, scope: string): boolean {
  return !!meta?.scopes?.includes(scope);
}

/**
 * `[venueId, setVenue]` for a single-venue Finance page: the app's shared,
 * remembered page venue (hooks/useActiveVenue) when it is one the viewer may
 * see here, else their first venue. Returns null while there is none.
 */
export function useFinanceVenue(meta: FinMeta | null): [string | null, (id: string) => void] {
  const [active, setActive] = useActiveVenue();
  const venues = meta?.venues ?? [];
  const venueId = venues.some((v) => v.id === active) ? active : venues[0]?.id ?? null;
  return [venueId, setActive];
}
