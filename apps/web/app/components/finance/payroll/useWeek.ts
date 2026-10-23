'use client';

/**
 * The pay-week record for an entity and a Monday: `POST /weeks` gets or
 * creates it (writing nothing to Loaded), then `GET /weeks/{id}` reads the
 * full detail (snapshots, drift, exports, inputs, pushes).
 */
import { useCallback, useEffect, useState } from 'react';
import { FinApiError, finGet, finSend, errorText } from '../shared/useFinanceApi';
import type { Week } from './types';

export interface WeekState {
  week: Week | null;
  error: string | null;
  status: number | null;
  loading: boolean;
  reload: () => void;
  /** Replace the record after a write that returned it. */
  setWeek: (w: Week) => void;
}

export function useWeek(venueId: string | null, weekStart: string | null, refresh = 0): WeekState {
  const [tick, setTick] = useState(0);
  const key = venueId && weekStart ? `${venueId}|${weekStart}|${refresh}|${tick}` : null;
  const [result, setResult] = useState<{ key: string; week: Week | null; error: string | null; status: number | null } | null>(null);

  useEffect(() => {
    if (!key || !venueId || !weekStart) return;
    let alive = true;
    (async () => {
      try {
        const created = await finSend<Week>('POST', '/api/payroll/weeks', { venue_id: venueId, week_start: weekStart });
        const week = await finGet<Week>(`/api/payroll/weeks/${created.id}`);
        if (alive) setResult({ key, week, error: null, status: null });
      } catch (e) {
        if (!alive) return;
        setResult((prev) => ({ key, week: prev?.week ?? null, error: errorText(e), status: e instanceof FinApiError ? e.status : null }));
      }
    })();
    return () => { alive = false; };
  }, [key, venueId, weekStart]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setWeek = useCallback((w: Week) => setResult((prev) => ({ key: prev?.key ?? key ?? '', week: w, error: null, status: null })), [key]);
  const settled = !!key && result?.key === key;
  return {
    week: result?.week ?? null,
    error: settled ? result?.error ?? null : null,
    status: settled ? result?.status ?? null : null,
    loading: !!key && !settled,
    reload,
    setWeek,
  };
}
