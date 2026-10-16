'use client';

/**
 * Pieces the report tabs share: loading a report (keeping the last one on
 * screen, dimmed, while the next loads), the period picker, and the frame
 * around a report's body.
 */
import type { ReactNode } from 'react';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import { useFnQuery, type FnQuery } from '../enquiries/bits';
import { PERIOD_LABELS, type Period, type PeriodKey, periodFor } from './dates';

export type Loaded<T> = FnQuery<T>;

/** GET a report; `path` null = don't load yet. Refetches when the params change. */
export function useReport<T>(path: string | null, params: Record<string, unknown>): Loaded<T> {
  return useFnQuery<T>(path, params);
}

/** Loading / failed / the report (dimmed while a new one loads). */
export function ReportBody<T>({ state, what, children }: { state: Loaded<T>; what: string; children: (data: T) => ReactNode }) {
  if (state.error && !state.data) {
    return <PageState kind="error" title={`Couldn’t load ${what}`} detail={state.error} action={<Button size="sm" onClick={state.retry}>Try again</Button>} />;
  }
  if (!state.data) return <PageState kind="loading" title={`Loading ${what}…`} />;
  return (
    <div style={{ opacity: state.loading ? 0.55 : 1, transition: 'opacity 0.15s' }} aria-busy={state.loading || undefined}>
      {state.error && <PageState kind="error" title={`Couldn’t refresh ${what}`} detail={state.error} />}
      {children(state.data)}
    </div>
  );
}

/** Preset periods plus custom dates, as one row of controls. */
export function PeriodPicker({ period, onChange, timeZone, presets = ['7d', '28d', '90d', '365d', 'custom'] }: {
  period: Period;
  onChange: (p: Period) => void;
  timeZone: string;
  presets?: PeriodKey[];
}) {
  return (
    <>
      <select
        className="n-select"
        aria-label="Period"
        value={period.key}
        onChange={(e) => onChange(periodFor(e.target.value as PeriodKey, timeZone, { from: period.from, to: period.to }))}
      >
        {presets.map((k) => <option key={k} value={k}>{PERIOD_LABELS[k]}</option>)}
      </select>
      {period.key === 'custom' && (
        <>
          <input type="date" className="n-input" aria-label="From" value={period.from} max={period.to}
            onChange={(e) => e.target.value && onChange({ ...period, from: e.target.value })} />
          <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>to</span>
          <input type="date" className="n-input" aria-label="To" value={period.to} min={period.from}
            onChange={(e) => e.target.value && onChange({ ...period, to: e.target.value })} />
        </>
      )}
    </>
  );
}

/** The one filter row above a report. */
export function FilterRow({ children }: { children: ReactNode }) {
  return <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>{children}</div>;
}
