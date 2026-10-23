'use client';

/**
 * Small pieces the four Norm Finance pages share on top of
 * components/finance/shared: the Finance mode banner (its own wording —
 * budgets are edited in Norm while Loaded is not written), the fiscal-year
 * and month controls, a page that could not open, a signed variance cell
 * and a driver chip.
 */
import type { CSSProperties, ReactNode } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import Button from '../../ui/Button';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import { formatSignedCents } from '../shared/money';
import { band, bandColor, fiscalYearLabel, monthLabel, pct, type YearMonth } from './logic';
import type { DriverChip, FinMeta, FinanceMode } from './types';

/** A URL search param from the page's address (a deep link such as
 *  `?page=finance-budgets&view=lines`), read once on mount. */
export function readPageParam(name: string): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return new URLSearchParams(window.location.search).get(name);
  } catch {
    return null;
  }
}

/** Keep a param in the address bar (replacing, never adding history) so a
 *  refresh — or the app shell remounting the page when the venue changes —
 *  comes back to the same tab. Null removes it. */
export function writePageParam(name: string, value: string | null): void {
  if (typeof window === 'undefined') return;
  try {
    const url = new URL(window.location.href);
    if (value === null || value === '') url.searchParams.delete(name);
    else url.searchParams.set(name, value);
    const next = `${url.pathname}${url.search}${url.hash}`;
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(window.history.state, '', next);
    }
  } catch {
    /* the address bar is a convenience — never break the page over it */
  }
}

/** Shadow: budgets are edited in Norm but Loaded is not written. */
export function FinanceModeBanner({ mode }: { mode: FinanceMode | undefined }) {
  if (mode !== 'shadow') return null;
  return (
    <div role="status" style={{ background: 'var(--warn-bg)', color: 'var(--warn)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: '8px 12px', fontSize: 'var(--fs-sm)', marginBottom: 12 }}>
      <strong>Shadow</strong> — budgets can be edited here, but nothing is sent to Loaded until Norm Finance goes live.
    </div>
  );
}

/** The page while its meta is loading or refused. A 404 is the App off. */
export function NotReady({ title, page, error, status, reload }: { title: string; page: boolean; error: string | null; status: number | null; reload: () => void }) {
  return (
    <div>
      {page && <PageHeader title={title} />}
      {error ? (
        <PageState
          kind="error"
          title={status === 404 ? 'Norm Finance is not on for this organisation.' : status === 403 ? 'You don’t have access to Norm Finance.' : 'Couldn’t open Norm Finance'}
          detail={status === 404 ? 'Its mode is off. An owner switches it on in Settings.' : status === 403 ? 'The Finance permission on your role opens these pages. Ask an owner if you should have it.' : error}
          action={status === 403 ? undefined : <Button size="sm" onClick={reload}>Try again</Button>}
        />
      ) : (
        <PageState kind="loading" title="Loading…" />
      )}
    </div>
  );
}

/** The venues for the shared PageShell's picker: non-trading ones named so. */
export function venueOptions(meta: FinMeta): { id: string; name: string }[] {
  return meta.venues.map((v) => ({ id: v.id, name: v.is_trading ? v.name : `${v.name} (non-trading)` }));
}

export function FiscalYearControl({ meta, value, onChange }: { meta: FinMeta; value: number; onChange: (fy: number) => void }) {
  const years = meta.fiscal_years.includes(value) ? meta.fiscal_years : [...meta.fiscal_years, value].sort();
  return (
    <select aria-label="Fiscal year" className="n-select" value={value} onChange={(e) => onChange(Number(e.target.value))}>
      {years.map((fy) => <option key={fy} value={fy}>{fiscalYearLabel(fy, meta.fiscal_year_start_month)}</option>)}
    </select>
  );
}

/** ‹ Oct 2026 › — steps within the months offered (a fiscal year). */
export function MonthNav({ months, value, onChange }: { months: YearMonth[]; value: YearMonth; onChange: (ym: YearMonth) => void }) {
  const at = months.findIndex((m) => m.year === value.year && m.month === value.month);
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <Button size="sm" variant="quiet" icon={ChevronLeft} aria-label="Previous month" disabled={at <= 0} onClick={() => onChange(months[at - 1])} />
      <select aria-label="Month" className="n-select" value={`${value.year}-${value.month}`} onChange={(e) => { const [y, m] = e.target.value.split('-').map(Number); onChange({ year: y, month: m }); }}>
        {months.map((m) => <option key={`${m.year}-${m.month}`} value={`${m.year}-${m.month}`}>{monthLabel(m)}</option>)}
      </select>
      <Button size="sm" variant="quiet" icon={ChevronRight} aria-label="Next month" disabled={at < 0 || at >= months.length - 1} onClick={() => onChange(months[at + 1])} />
    </div>
  );
}

/** A signed Loaded − locked variance with its share, coloured by band: the
 *  sign carries the meaning, the colour only underlines it. */
export function Variance({ cents, base, currency, style }: { cents: number | null | undefined; base?: number | null; currency: string; style?: CSSProperties }) {
  const b = band(cents);
  const share = base ? (cents ?? 0) / base : null;
  return (
    <span style={{ color: bandColor(b), whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums', ...style }}>
      {formatSignedCents(cents, currency, 0)}
      {share !== null && cents ? <span style={{ fontSize: 'var(--fs-xs)', marginLeft: 4 }}>({pct(share, 1, true)})</span> : null}
    </span>
  );
}

const CHIP_LABEL: Record<DriverChip['kind'], string> = { holiday: 'Holiday', event: 'Event', function: 'Function', manual: 'Driver' };

/** A driver on a day — a neutral chip with the kind in front; never a colour. */
export function Chip({ chip, onClick }: { chip: DriverChip; onClick?: () => void }) {
  const text = `${CHIP_LABEL[chip.kind] ?? chip.kind}${chip.label ? `: ${chip.label}` : ''}`;
  if (!onClick) return <span className="n-badge" title={text}>{text}</span>;
  return (
    <button type="button" onClick={onClick} title={text} style={{ background: 'none', border: 'none', padding: 0, font: 'inherit', cursor: 'pointer' }}>
      <span className="n-badge">{text}</span>
    </button>
  );
}

/** A compact line under a grid (totals, notes). */
export function Small({ children, tone = 'muted', style }: { children: ReactNode; tone?: 'muted' | 'warn'; style?: CSSProperties }) {
  return <div style={{ fontSize: 'var(--fs-xs)', color: tone === 'warn' ? 'var(--warn)' : 'var(--muted)', marginTop: 6, ...style }}>{children}</div>;
}

export const totalRow: CSSProperties = { fontWeight: 600, background: 'var(--surface)' };
export const subtotalRow: CSSProperties = { fontWeight: 500, color: 'var(--text-soft)', background: 'var(--surface)', fontSize: 'var(--fs-sm)' };
export const cellInput: CSSProperties = { width: 110, textAlign: 'right', padding: '4px 8px', fontVariantNumeric: 'tabular-nums' };
