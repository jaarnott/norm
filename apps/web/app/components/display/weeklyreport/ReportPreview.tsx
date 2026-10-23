'use client';

/**
 * Last week's report as the email holds it: four headline tiles, sales by
 * venue, top sellers and cost of goods — the tables the task's prompt asks
 * Norm to write (schedule.ts → buildPrompt), with the same columns.
 */

import React from 'react';
import { formatMoney, formatNumber } from '../../../lib/format';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import { Section, StatTile, TileRow } from '../../functions/enquiries/bits';
import {
  change,
  cogsTotal,
  formatPct,
  likeForLike,
  topSellers,
  type CogsLine,
  type ItemsDoc,
  type PerfDoc,
  type PerfRow,
} from './report';

export interface Loadable<T> {
  doc: T | null;
  loading: boolean;
  error: string | null;
}

const money = (n: number | null | undefined) => (n === null || n === undefined ? '—' : formatMoney(n, 0));

/** A signed change, green up / red down; "—" without a base. */
function Delta({ value, invert = false }: { value: number | null; invert?: boolean }) {
  if (value === null || !Number.isFinite(value)) return <span style={{ color: 'var(--muted)' }}>—</span>;
  const good = invert ? value < 0 : value > 0;
  const bad = invert ? value > 0 : value < 0;
  return (
    <span style={{ color: good ? 'var(--ok)' : bad ? 'var(--error)' : 'var(--text-soft)', fontWeight: 500 }}>
      {formatPct(value, { signed: true })}
    </span>
  );
}

/** Tile change: the StatTile shows the size with an arrow, so one decimal. */
const tileChange = (v: number | null) => (v === null ? null : Math.round(v * 1000) / 10);

const vsBudget = (r: PerfRow) =>
  typeof r.budget_pct === 'number' && r.budget ? r.budget_pct - 1 : change(r.sales, r.budget);
const vsLastYear = (r: PerfRow) =>
  r.last_year ? (typeof r.vs_last_year_pct === 'number' ? r.vs_last_year_pct : change(r.sales, r.last_year)) : null;

export default function ReportPreview({ perf, items, cogs, oneVenue, stacked, onRetry }: {
  perf: Loadable<PerfDoc>;
  items: Loadable<ItemsDoc>;
  cogs: { lines: CogsLine[]; loading: boolean };
  oneVenue: boolean;
  stacked: boolean;
  onRetry: () => void;
}) {
  const doc = perf.doc;
  const rows = doc?.by_venue ?? [];
  const summary = doc?.summary;
  const lfl = likeForLike(rows);
  const groupVsLy = oneVenue ? (rows[0] ? vsLastYear(rows[0]) : null) : lfl.change;
  const cogsAll = cogsTotal(cogs.lines);
  const cogsDone = cogs.lines.filter((l) => l.error || l.revenue !== null || l.cost !== null).length;
  const showGroup = !oneVenue && rows.length > 1;

  const tiles = summary ? (
    <TileRow min={170}>
      <StatTile
        label="Sales"
        value={money(summary.sales)}
        changePct={summary.budget ? tileChange(vsBudget(summary)) : null}
        changeLabel="vs budget"
        sub={summary.budget ? `Budget ${money(summary.budget)}` : 'No budget set'}
      />
      <StatTile
        label="Against last year"
        value={formatPct(groupVsLy, { signed: true })}
        sub={
          oneVenue
            ? rows[0]?.last_year ? `Last year ${money(rows[0].last_year)}` : 'No figures for last year'
            : lfl.venues === 0
              ? 'No venue has figures for last year'
              : lfl.venues < lfl.of
                ? `${lfl.venues} of ${lfl.of} venues traded a year ago: ${money(lfl.sales)} against ${money(lfl.lastYear)}`
                : `${money(lfl.sales)} against ${money(lfl.lastYear)}`
        }
      />
      <StatTile
        label="Wages"
        value={formatPct(summary.wage_pct)}
        sub={`${money(summary.actual_cost)} of labour on ${money(summary.sales_ex_gst)} ex GST`}
      />
      {/* The group figure only once every venue has answered: a part-sum
          would read as the group's. */}
      <StatTile
        label="Cost of goods"
        value={cogs.loading ? '…' : formatPct(cogsAll.pct)}
        sub={
          cogs.loading
            ? `Loading from Loaded: ${cogsDone} of ${cogs.lines.length} venue${cogs.lines.length === 1 ? '' : 's'}`
            : cogsAll.venues
              ? `${money(cogsAll.cost)} on ${money(cogsAll.revenue)} ex GST${cogsAll.venues < cogs.lines.length ? ` (${cogsAll.venues} of ${cogs.lines.length} venues)` : ''}`
              : 'No cost of goods from Loaded'
        }
      />
    </TileRow>
  ) : null;

  // ---- Sales by venue ------------------------------------------------------
  const groupRow: PerfRow | null = showGroup && summary ? { ...summary, venue: 'Group' } : null;

  const salesTable = (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <table className="n-table" aria-label="Sales by venue">
        <thead>
          <tr>
            <th>Venue</th>
            <th className="num">Sales</th>
            <th className="num">Budget</th>
            <th className="num">vs budget</th>
            <th className="num">Last year</th>
            <th className="num">vs last year</th>
            <th className="num">Wages</th>
            <th className="num">Wage %</th>
          </tr>
        </thead>
        <tbody>
          {[...rows, ...(groupRow ? [groupRow] : [])].map((r) => {
            const group = r === groupRow;
            return (
              <tr key={r.venue} style={group ? { fontWeight: 600 } : undefined}>
                <td style={{ fontWeight: group ? 700 : 500 }}>{r.venue}</td>
                <td className="num">{money(r.sales)}</td>
                <td className="num">{r.budget ? money(r.budget) : '—'}</td>
                <td className="num"><Delta value={r.budget ? vsBudget(r) : null} /></td>
                <td className="num">{group ? (lfl.venues ? money(lfl.lastYear) : '—') : r.last_year ? money(r.last_year) : '—'}</td>
                <td className="num"><Delta value={group ? lfl.change : vsLastYear(r)} /></td>
                <td className="num">{money(r.actual_cost)}</td>
                <td className="num">{formatPct(r.wage_pct)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  const salesCards = (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column' }}>
      {[...rows, ...(groupRow ? [groupRow] : [])].map((r, i) => {
        const group = r === groupRow;
        const ly = group ? (lfl.venues ? lfl.lastYear : null) : r.last_year || null;
        return (
          <li key={r.venue} style={{ padding: '10px 16px', borderTop: i ? '1px solid var(--line-soft)' : undefined }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
              <span style={{ fontWeight: group ? 700 : 600, minWidth: 0 }}>{r.venue}</span>
              <span style={{ fontWeight: 600 }}>{money(r.sales)}</span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 12px', marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              <span>{r.budget ? <>Budget {money(r.budget)} <Delta value={vsBudget(r)} /></> : 'No budget'}</span>
              <span>{ly ? <>Last year {money(ly)} <Delta value={group ? lfl.change : vsLastYear(r)} /></> : 'No figures for last year'}</span>
              <span>Wages {money(r.actual_cost)} · {formatPct(r.wage_pct)}</span>
            </div>
          </li>
        );
      })}
    </ul>
  );

  // ---- Top sellers -----------------------------------------------------------
  const sellers = topSellers(items.doc);
  const sellersBody = items.loading ? (
    <PageState kind="loading" title="Loading top sellers…" />
  ) : items.error ? (
    <div style={{ padding: 12 }}><PageState kind="error" title="Could not load top sellers." detail={items.error} /></div>
  ) : sellers.length === 0 ? (
    <PageState kind="empty" title="No items sold last week." />
  ) : (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <table className="n-table" aria-label="Top sellers">
        <thead>
          <tr>
            <th className="num" style={{ width: 28 }}>#</th>
            <th>Item</th>
            <th className="num">Qty</th>
            <th className="num">Sales</th>
          </tr>
        </thead>
        <tbody>
          {sellers.map((r, i) => (
            <tr key={`${r.item}-${r.group ?? ''}-${i}`}>
              <td className="num" style={{ color: 'var(--muted)' }}>{i + 1}</td>
              <td style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 500 }}>{r.item}</div>
                {r.group && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{r.group}</div>}
              </td>
              <td className="num">{formatNumber(r.quantity ?? 0)}</td>
              <td className="num">{money(r.sales)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  // ---- Cost of goods ---------------------------------------------------------
  const cogsRows = cogs.lines;
  const cogsBody = cogs.loading && cogsRows.length === 0 ? (
    <PageState kind="loading" title="Loading cost of goods…" />
  ) : cogsRows.length === 0 ? (
    <PageState kind="empty" title="No cost of goods from Loaded." />
  ) : (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <table className="n-table" aria-label="Cost of goods">
        <thead>
          <tr>
            <th>Venue</th>
            <th className="num">Sales ex GST</th>
            <th className="num">Cost</th>
            <th className="num">Cost %</th>
          </tr>
        </thead>
        <tbody>
          {cogsRows.map((l) => (
            <tr key={l.venueId}>
              <td style={{ fontWeight: 500 }}>{l.venue}</td>
              {l.error ? (
                <td colSpan={3} style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)' }}>{l.error}</td>
              ) : l.revenue === null && l.cost === null ? (
                <td colSpan={3} style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>Loading…</td>
              ) : (
                <>
                  <td className="num">{money(l.revenue)}</td>
                  <td className="num">{money(l.cost)}</td>
                  <td className="num">{formatPct(l.pct)}</td>
                </>
              )}
            </tr>
          ))}
          {cogsRows.length > 1 && !cogs.loading && (
            <tr style={{ fontWeight: 600 }}>
              <td style={{ fontWeight: 700 }}>Group</td>
              <td className="num">{money(cogsAll.revenue)}</td>
              <td className="num">{money(cogsAll.cost)}</td>
              <td className="num">{formatPct(cogsAll.pct)}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );

  if (perf.loading) {
    return <div className="n-card"><PageState kind="loading" title="Loading last week from Loaded…" detail="Sales, budgets and wages for every venue. This can take a few seconds." /></div>;
  }
  if (perf.error) {
    return <PageState kind="error" title="Could not load last week's figures." detail={perf.error} action={<Button size="sm" onClick={onRetry}>Try again</Button>} />;
  }
  if (!doc || rows.length === 0) {
    return <div className="n-card"><PageState kind="empty" title="No trading figures for last week." /></div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {tiles}
      <Section title="Sales by venue" meta="Sales, budget and last year include GST" padded={false}>
        {stacked ? salesCards : salesTable}
      </Section>
      <div style={{ display: 'grid', gridTemplateColumns: stacked ? '1fr' : 'repeat(auto-fit, minmax(360px, 1fr))', gap: 12, alignItems: 'start' }}>
        <Section title="Top sellers" meta={oneVenue ? 'Before discounts' : 'All venues, before discounts'} padded={false}>
          {sellersBody}
        </Section>
        <Section title="Cost of goods" meta="Cost of what was sold, against sales ex GST" padded={false}>
          {cogsBody}
        </Section>
      </div>
      {Array.isArray(doc.warnings) && doc.warnings.length > 0 && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--warn)' }}>
          {doc.warnings.map((w, i) => <div key={i}>{typeof w === 'string' ? w : JSON.stringify(w)}</div>)}
        </div>
      )}
      <p style={{ margin: 0, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
        Wage % is labour cost over sales ex GST. Against last year compares the same trading days 52 weeks back{oneVenue ? '' : ', counting only venues that were trading then'}.
      </p>
    </div>
  );
}
