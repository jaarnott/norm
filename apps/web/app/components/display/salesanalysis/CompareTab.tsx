'use client';

/**
 * Compare: the period against the one before, the same days last year or
 * the budget — day by day, then in total. Every figure is get_sales
 * breakdown 'daily' (budget and last year computed by the tool itself; the
 * period before is a second call on its own trading days).
 */

import React, { useMemo } from 'react';
import { formatMoney } from '../../../lib/format';
import Chart, { DEFAULT_COLORS } from '../Chart';
import PageState from '../../ui/PageState';
import { Section } from '../../functions/enquiries/bits';
import { dayLabel, type CompareResult, type CompareRow } from './salesLogic';

/** "+$1,234" / "−$1,234" (a real minus sign). */
export function signedMoney(n: number | null, dp = 0): string {
  if (n === null) return '—';
  if (n === 0) return formatMoney(0, dp);
  return `${n > 0 ? '+' : '−'}${formatMoney(Math.abs(n), dp)}`;
}

export function signedPct(n: number | null): string {
  if (n === null) return '—';
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toFixed(1)}%`;
}

const tone = (n: number | null) => (n === null || n === 0 ? undefined : n > 0 ? 'var(--ok)' : 'var(--error)');

export default function CompareTab({ result, loading, error, currentLabel, otherLabel, otherEmptyText, stacked }: {
  result: CompareResult | null;
  loading: boolean;
  error: string | null;
  /** "Last week" */
  currentLabel: string;
  /** "Week before" / "Budget" / "Last year" */
  otherLabel: string;
  /** Said when the comparison has no figures at all. */
  otherEmptyText: string;
  stacked: boolean;
}) {
  const chartRows = useMemo(() => (result?.rows ?? []).map((r) => ({
    day: dayLabel(r.date).replace(/ \w+$/, ''), // "Mon 12"
    current: r.actual ?? 0,
    other: r.other ?? 0,
  })), [result]);

  if (loading) return <div className="n-card"><PageState kind="loading" title="Loading the comparison from Loaded…" /></div>;
  if (error) return <PageState kind="error" title="Could not load the comparison." detail={error} />;
  if (!result || !result.rows.length) return <div className="n-card"><PageState kind="empty" title="No sales in this period." /></div>;

  const otherCell = (r: CompareRow) => (
    <>
      {r.other === null ? '—' : formatMoney(r.other, 0)}
      {r.otherDate && <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{dayLabel(r.otherDate)}</span>}
    </>
  );

  const table = (
    <table className="n-table">
      <thead>
        <tr>
          <th>Day</th>
          <th className="num">{currentLabel}</th>
          <th className="num">{otherLabel}</th>
          <th className="num">Difference</th>
          <th className="num">Change</th>
        </tr>
      </thead>
      <tbody>
        {result.rows.map((r) => (
          <tr key={r.date} style={r.future ? { color: 'var(--muted)' } : undefined}>
            <td style={{ whiteSpace: 'nowrap', fontWeight: 500 }}>{dayLabel(r.date)}</td>
            <td className="num" style={{ fontWeight: 600 }}>{r.future ? 'Not traded yet' : formatMoney(r.actual ?? 0, 0)}</td>
            <td className="num">{otherCell(r)}</td>
            <td className="num" style={{ color: tone(r.diff) }}>{signedMoney(r.diff)}</td>
            <td className="num" style={{ color: tone(r.pct) }}>{signedPct(r.pct)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <td style={{ fontWeight: 700, padding: '10px 12px', borderTop: '1px solid var(--line-strong)' }}>
            Total{result.days < result.rows.length ? ` (${result.days} days)` : ''}
          </td>
          <td className="num" style={{ fontWeight: 700, padding: '10px 12px', borderTop: '1px solid var(--line-strong)' }}>{formatMoney(result.actual, 0)}</td>
          <td className="num" style={{ fontWeight: 700, padding: '10px 12px', borderTop: '1px solid var(--line-strong)' }}>{result.otherEmpty ? '—' : formatMoney(result.other, 0)}</td>
          <td className="num" style={{ fontWeight: 700, padding: '10px 12px', borderTop: '1px solid var(--line-strong)', color: result.otherEmpty ? undefined : tone(result.diff) }}>{result.otherEmpty ? '—' : signedMoney(result.diff)}</td>
          <td className="num" style={{ fontWeight: 700, padding: '10px 12px', borderTop: '1px solid var(--line-strong)', color: tone(result.pct) }}>{signedPct(result.pct)}</td>
        </tr>
      </tfoot>
    </table>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {result.otherEmpty && (
        <div role="note" style={{ padding: '10px 14px', borderRadius: 'var(--radius)', background: 'var(--info-bg)', color: 'var(--info)', fontSize: 'var(--fs-sm)' }}>
          {otherEmptyText}
        </div>
      )}
      <Chart
        data={{ rows: chartRows }}
        props={{
          chart_type: 'bar',
          title: `${currentLabel} against ${otherLabel.toLowerCase()}, day by day`,
          x_axis: { key: 'day', label: 'Day' },
          // The chart's formatter takes a format object; the spec types it as a string.
          y_axis: { key: 'current', label: 'Sales', format: { type: 'currency', decimals: 0 } as unknown as string },
          series: [
            { key: 'current', label: currentLabel, color: DEFAULT_COLORS[0] },
            { key: 'other', label: otherLabel, color: DEFAULT_COLORS[1] },
          ],
        }}
        hideAddToReport
        height={stacked ? 220 : 280}
      />
      <Section title="Day by day" meta="sales incl. GST" padded={false}>
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>{table}</div>
      </Section>
    </div>
  );
}
