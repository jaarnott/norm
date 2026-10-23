'use client';

/**
 * `GET /api/payroll/meta`, loaded once and shared by every payroll page:
 * the mode, the person's scopes, the entities (the venues they can see
 * that run a pay run), this week and last week. Plus the venue and week
 * choice every page makes the same way: a deep link (`?venue=&week=`), the
 * app's page venue, the remembered venue, else the first entity.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useActiveVenue } from '../../../hooks/useActiveVenue';
import { readPageParam } from '../../functions/shared/meta';
import { FinApiError, finGet, errorText } from '../shared/useFinanceApi';
import { isMonday, mondayOf } from '../shared/weeks';
import type { Entity, PayrollMeta } from './types';

let cached: Promise<PayrollMeta> | null = null;

export function loadPayrollMeta(force = false): Promise<PayrollMeta> {
  if (!cached || force) {
    cached = finGet<PayrollMeta>('/api/payroll/meta').catch((err) => {
      cached = null; // let the next caller retry
      throw err;
    });
  }
  return cached;
}

export interface MetaState {
  meta: PayrollMeta | null;
  error: string | null;
  /** 403 = no access; 404 = Loaded Payroll is off for this organisation. */
  status: number | null;
  loading: boolean;
  reload: () => void;
}

export function usePayrollMeta(): MetaState {
  const [meta, setMeta] = useState<PayrollMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    loadPayrollMeta(tick > 0)
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
  return { meta, error, status, loading: !meta && !error, reload };
}

export interface PayrollPageState extends MetaState {
  isPage: boolean;
  /** The entities as the venue switcher wants them. */
  venues: { id: string; name: string }[];
  entity: Entity | null;
  venueId: string | null;
  setVenue: (id: string) => void;
  /** The pay week's Monday. */
  weekStart: string;
  setWeekStart: (weekStart: string) => void;
  /** 'all' chosen on a page that offers a group view. */
  allVenues: boolean;
}

/**
 * One hook for a payroll page: the meta, the chosen entity and the chosen
 * week. `allowAll` lets the switcher offer "All venues" (the group pages).
 */
export function usePayrollPage(props: Record<string, unknown> | undefined, opts: { allowAll?: boolean; defaultAll?: boolean } = {}): PayrollPageState {
  const isPage = !!props?.persistVenue;
  const state = usePayrollMeta();
  const [link] = useState(() => ({ venue: readPageParam('venue'), week: readPageParam('week') }));
  const [chosen, setChosen] = useState<string | null>(() => link.venue ?? (opts.defaultAll && opts.allowAll ? 'all' : null));
  const [shared, setShared] = useActiveVenue();
  const [week, setWeek] = useState<string | null>(() => (link.week ? mondayOf(link.week) : null));

  const entities = useMemo(() => state.meta?.entities ?? [], [state.meta]);
  const venues = useMemo(() => entities.map((e) => ({ id: e.venue_id, name: e.venue_name })), [entities]);

  const candidates = [chosen, props?.activeVenueId as string | undefined, isPage ? shared : null];
  let venueId: string | null = null;
  let allVenues = false;
  if (chosen === 'all' && opts.allowAll) {
    allVenues = true;
  } else {
    venueId = candidates.map((id) => entities.find((e) => e.venue_id === id)?.venue_id).find(Boolean) ?? entities[0]?.venue_id ?? null;
  }
  const entity = entities.find((e) => e.venue_id === venueId) ?? null;

  const setVenue = useCallback((id: string) => {
    setChosen(id);
    if (isPage && id !== 'all') setShared(id);
  }, [isPage, setShared]);

  const weekStart = week && isMonday(week) ? week : (state.meta?.last_week_start ?? mondayOf(new Date().toISOString().slice(0, 10)));
  const setWeekStart = useCallback((ws: string) => setWeek(mondayOf(ws)), []);

  return { ...state, isPage, venues, entity, venueId, setVenue, weekStart, setWeekStart, allVenues };
}

/** The rehearsal rule a write form follows: in shadow a row must be flagged
 *  `rehearsal` to be stored at all; live honours the flag as a label. */
export function rehearsalDefault(meta: PayrollMeta | null): boolean {
  return meta?.mode === 'shadow';
}

/** Open another payroll page in the app at this venue and week (the
 *  Functions `norm:open-page` link; app/page.tsx puts the params in the
 *  address bar for the page to read on mount). */
export function openPayrollPage(page: 'payroll-payrun' | 'payroll-leave' | 'payroll-intercompany' | 'payroll-reconciliation' | 'payroll-settings' | 'payroll-shadow', params: Record<string, string | null | undefined> = {}): void {
  if (typeof window === 'undefined') return;
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) if (v) clean[k] = v;
  window.dispatchEvent(new CustomEvent('norm:open-page', { detail: { page, params: clean } }));
}
