'use client';

/**
 * `GET /api/payroll/meta`, loaded once for the Payroll page: the mode, the
 * person's scopes, the entities (the venues they can see that run a pay
 * run), this week and last week. The page (PayrollPage.tsx) owns the
 * entity and week choice and hands them to every tab.
 */
import { useCallback, useEffect, useState } from 'react';
import { FinApiError, finGet, errorText } from '../shared/useFinanceApi';
import type { PayrollMeta } from './types';

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
  /** 403 = no access; 404 = Payroll is off for this organisation. */
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

/** The rehearsal rule a write form follows: during the trial run (`shadow`)
 *  a row must be flagged `rehearsal` to be stored at all; live honours the
 *  flag as a label. */
export function rehearsalDefault(meta: PayrollMeta | null): boolean {
  return meta?.mode === 'shadow';
}
