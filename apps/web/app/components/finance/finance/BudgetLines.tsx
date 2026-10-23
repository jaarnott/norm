'use client';

/**
 * Budgets → Monthly lines: the line items carried from Orbit (D33) for a
 * venue-year, a row per item and a column per month of the fiscal year. A
 * percentage is typed as "29" (a ratio, 0.29, is stored); a dollar line as
 * dollars. Wages is one line: a month holds a % or a $, never both (saving
 * one clears the other, as Orbit's grid did). Each cell saves itself when
 * it is left.
 */
import { useEffect, useMemo, useState } from 'react';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import { Section } from '../shared/PageShell';
import { toast } from '../shared/Toasts';
import { errorText, finSend, useFinQuery } from '../shared/useFinanceApi';
import { Small } from './bits';
import { fiscalYearMonths, parseAmount, parseRatio, shortMonth } from './logic';
import type { FinMeta, LineItem, LineValue, LinesDoc } from './types';

const LINES: { item: LineItem; label: string; kind: 'ratio' | 'cents'; hint?: string }[] = [
  { item: 'wage_pct', label: 'Wages %', kind: 'ratio', hint: 'of sales ex GST' },
  { item: 'wage_cents', label: 'Wages $', kind: 'cents', hint: 'instead of a % (head office)' },
  { item: 'cogs_pct', label: 'Total COGS %', kind: 'ratio' },
  { item: 'food_cogs_pct', label: 'Food COGS %', kind: 'ratio' },
  { item: 'bev_cogs_pct', label: 'Beverage COGS %', kind: 'ratio' },
  { item: 'leave_cents', label: 'Leave and entitlements $', kind: 'cents' },
  { item: 'opex_cents', label: 'Operating expenses $', kind: 'cents' },
  { item: 'depreciation_cents', label: 'Depreciation $', kind: 'cents' },
  { item: 'rebates_pct', label: 'Rebates %', kind: 'ratio', hint: 'of beverage purchases' },
];

function shown(v: LineValue | undefined, kind: 'ratio' | 'cents'): string {
  if (!v) return '';
  if (kind === 'ratio') return v.value_ratio === null || v.value_ratio === undefined ? '' : String(Math.round(v.value_ratio * 10000) / 100);
  return v.value_cents === null || v.value_cents === undefined ? '' : String(v.value_cents / 100);
}

function Cell({ value, kind, disabled, label, onSave }: { value: LineValue | undefined; kind: 'ratio' | 'cents'; disabled: boolean; label: string; onSave: (next: LineValue | null) => Promise<void> }) {
  const initial = shown(value, kind);
  const [text, setText] = useState(initial);
  useEffect(() => { setText(shown(value, kind)); }, [value, kind]);
  const commit = () => {
    if (text.trim() === initial.trim()) return;
    if (!text.trim()) { void onSave(null); return; }
    if (kind === 'ratio') {
      const r = parseRatio(text);
      if (r === null) { setText(initial); return; }
      void onSave({ value_ratio: r });
    } else {
      const c = parseAmount(text);
      if (c === null) { setText(initial); return; }
      void onSave({ value_cents: c });
    }
  };
  if (disabled) return <span style={{ color: text ? undefined : 'var(--muted)' }}>{text ? (kind === 'ratio' ? `${text}%` : `$${Number(text).toLocaleString('en-NZ')}`) : '—'}</span>;
  return (
    <input
      className="n-input"
      inputMode="decimal"
      aria-label={label}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setText(initial); }}
      style={{ width: 76, textAlign: 'right', padding: '4px 6px', fontVariantNumeric: 'tabular-nums' }}
    />
  );
}

export default function BudgetLines({ meta, venueId, fy, canWrite }: { meta: FinMeta; venueId: string; fy: number; canWrite: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const q = useFinQuery<LinesDoc>('/api/finance/budgets/lines', { venue_id: venueId, fiscal_year: fy }, refresh);
  const months = useMemo(() => fiscalYearMonths(fy, meta.fiscal_year_start_month), [fy, meta.fiscal_year_start_month]);

  const save = async (month: number, item: LineItem, next: LineValue | null) => {
    try {
      const doc = await finSend<LinesDoc>('PUT', '/api/finance/budgets/lines', { venue_id: venueId, fiscal_year: fy, month, lines: { [item]: next } });
      q.setData(doc);
    } catch (e) {
      toast(errorText(e), 'error');
      setRefresh((n) => n + 1);
    }
  };

  return (
    <Section title={`Monthly lines · FY${fy}`} meta="a value or a percentage per month; saved as you leave each cell" padded={false}>
      {q.error ? (
        <div style={{ padding: 12 }}><PageState kind="error" title="Couldn’t load the lines." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} /></div>
      ) : !q.data ? (
        <PageState kind="loading" title="Loading…" />
      ) : (
        <>
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <table className="n-table" style={{ width: '100%', minWidth: 1100 }} aria-label="Monthly budget lines">
              <thead>
                <tr>
                  <th style={{ position: 'sticky', left: 0, zIndex: 2 }}>Line</th>
                  {months.map((m) => <th key={`${m.year}-${m.month}`} className="num">{shortMonth(m.month)} {String(m.year).slice(2)}</th>)}
                </tr>
              </thead>
              <tbody>
                {LINES.map((ln) => (
                  <tr key={ln.item}>
                    <td style={{ position: 'sticky', left: 0, background: 'var(--bg)', zIndex: 1, whiteSpace: 'nowrap' }}>
                      {ln.label}
                      {ln.hint && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{ln.hint}</div>}
                    </td>
                    {months.map((m) => (
                      <td key={`${m.year}-${m.month}`} className="num">
                        <Cell
                          value={q.data!.months[String(m.month)]?.[ln.item]}
                          kind={ln.kind}
                          disabled={!canWrite}
                          label={`${ln.label} for ${shortMonth(m.month)} ${m.year}`}
                          onSave={(next) => save(m.month, ln.item, next)}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '8px 12px 12px' }}>
            <Small style={{ marginTop: 0 }}>Percentages are typed as whole numbers (29 for 29%). Clear a cell to remove the line. Intercompany fees, finance costs and income tax are not budgeted; the Weekly Summary shows them from Xero where a venue is connected.</Small>
          </div>
        </>
      )}
    </Section>
  );
}
