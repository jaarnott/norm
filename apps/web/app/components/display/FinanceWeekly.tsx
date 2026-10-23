'use client';

/**
 * Weekly summary — the `finance_weekly` display component.
 *
 * Renders the document `loadedhub.get_weekly_financials` returns: for each
 * venue the week and the month-to-date (sales, budget, orders, the food /
 * beverage split, wages and wage %, labour vs roster, voids and cash-up
 * variance), the group totals, and the week's daily rows. Two surfaces:
 *
 *  - in a conversation the block arrives with the consolidator's result as
 *    `data` and renders it as-is (zero tokens: the model read the summary);
 *  - on the Finance → Weekly Summary page (`persistVenue`) it loads itself
 *    through `POST /api/connectors/loadedhub/execute/get_weekly_financials`
 *    for the venues the person can see, with a week picker.
 *
 * Amounts are in the organisation's currency (`Organization.currency`, read
 * once from the company settings) — NZ and AU organisations sit side by
 * side. No chart library: tiles and tables carry the numbers; sections fold.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch, getStoredUser } from '../../lib/api';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import { StatTile, TileRow } from '../functions/enquiries/bits';
import { extractDoc, lastCompletedSunday, makeMoney, num, pct, shiftWeek } from './financeWeekly';
import type { DayRow, Figures, Money, WeeklyDoc } from './financeWeekly';

/** One line of the venue table: label and the two columns' values. */
interface Line {
  label: string;
  value: (f: Figures) => string;
  tone?: (f: Figures) => string | undefined;
  group?: string;
}

function lines(money: Money): Line[] {
  const signTone = (v: number | null | undefined, good: 'up' | 'down') => {
    if (v === null || v === undefined || v === 0) return undefined;
    const positive = v > 0;
    return positive === (good === 'up') ? 'var(--ok)' : 'var(--error)';
  };
  return [
    { group: 'Sales', label: 'Sales (incl GST)', value: (f) => money(f.sales) },
    { label: 'Sales (ex GST)', value: (f) => money(f.sales_ex_gst) },
    { label: 'Budget', value: (f) => money(f.budget) },
    { label: 'vs budget', value: (f) => (f.budget ? `${money(f.vs_budget)} (${pct(f.budget_pct)})` : '—'), tone: (f) => signTone(f.vs_budget, 'up') },
    { label: 'Orders · average', value: (f) => (f.orders ? `${num(f.orders)} · ${money(f.average_order)}` : '—') },
    { label: 'Discounts', value: (f) => money(f.discounts) },
    { label: 'Voids', value: (f) => (f.void_count ? `${money(f.voids)} (${num(f.void_count)})` : money(f.voids)) },
    { group: 'Food and beverage', label: 'Food sales', value: (f) => `${money(f.food_sales)} (${pct(f.food_share)})` },
    { label: 'Beverage sales', value: (f) => `${money(f.beverage_sales)} (${pct(f.beverage_share)})` },
    { label: 'Other', value: (f) => money(f.other_sales) },
    { label: 'Items vs sales', value: (f) => money(f.split_mismatch) },
    { group: 'Wages', label: 'Wages', value: (f) => `${money(f.wages)} (${pct(f.wage_pct)})` },
    { label: 'Food wages', value: (f) => `${money(f.food_wages)} (${pct(f.food_wage_pct)})` },
    { label: 'Beverage wages', value: (f) => `${money(f.beverage_wages)} (${pct(f.beverage_wage_pct)})` },
    { label: 'Hours · kitchen', value: (f) => `${num(f.hours, 1)} · ${num(f.kitchen_hours, 1)}` },
    { group: 'Roster vs actual', label: 'Rostered cost', value: (f) => money(f.rostered_cost) },
    { label: 'Actual cost', value: (f) => money(f.actual_cost) },
    { label: 'Cost variance', value: (f) => money(f.cost_variance), tone: (f) => signTone(f.cost_variance, 'down') },
    { label: 'Hours variance', value: (f) => num(f.hours_variance, 1), tone: (f) => signTone(f.hours_variance, 'down') },
    { group: 'Cash-ups', label: 'Cash variance', value: (f) => money(f.cash_variance), tone: (f) => signTone(f.cash_variance, 'up') },
    { label: 'Total variance', value: (f) => money(f.total_variance), tone: (f) => signTone(f.total_variance, 'up') },
  ];
}

function Fold({ title, open, onToggle, children, meta }: { title: string; open: boolean; onToggle: () => void; children: React.ReactNode; meta?: string }) {
  return (
    <section className="n-card" style={{ minWidth: 0, padding: 0, overflow: 'hidden' }}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', background: 'transparent', border: 'none', font: 'inherit', color: 'var(--text)', cursor: 'pointer', textAlign: 'left' }}
      >
        <Icon icon={open ? ChevronDown : ChevronRight} size="dense" label={open ? 'Collapse' : 'Expand'} />
        <span style={{ fontWeight: 600, flex: 1 }}>{title}</span>
        {meta && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</span>}
      </button>
      {open && <div style={{ padding: '0 12px 12px' }}>{children}</div>}
    </section>
  );
}

function FiguresTable({ week, mtd, money, weekLabel, mtdLabel }: { week: Figures; mtd: Figures; money: Money; weekLabel: string; mtdLabel: string }) {
  const rows = lines(money);
  return (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <table className="n-table" style={{ width: '100%' }}>
        <thead>
          <tr>
            <th />
            <th className="num">{weekLabel}</th>
            <th className="num">{mtdLabel}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((ln) => (
            <React.Fragment key={ln.label}>
              {ln.group && (
                <tr>
                  <td colSpan={3} style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)', textTransform: 'uppercase', letterSpacing: '0.04em', paddingTop: 10 }}>
                    {ln.group}
                  </td>
                </tr>
              )}
              <tr>
                <td style={{ color: 'var(--text-soft)' }}>{ln.label}</td>
                <td className="num" style={{ fontWeight: 500, color: ln.tone?.(week) }}>{ln.value(week)}</td>
                <td className="num" style={{ color: ln.tone?.(mtd) }}>{ln.value(mtd)}</td>
              </tr>
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DaysTable({ days, money }: { days: DayRow[]; money: Money }) {
  return (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch', marginTop: 8 }}>
      <table className="n-table" style={{ width: '100%' }}>
        <thead>
          <tr>
            <th>Day</th>
            <th className="num">Sales</th>
            <th className="num">Budget</th>
            <th className="num">vs budget</th>
            <th className="num">Orders</th>
            <th className="num">Wages</th>
            <th className="num">Wage %</th>
            <th className="num">Voids</th>
          </tr>
        </thead>
        <tbody>
          {days.map((d) => (
            <tr key={d.date}>
              <td>{d.day} {d.date.slice(8)}</td>
              <td className="num" style={{ fontWeight: 500 }}>{money(d.sales)}</td>
              <td className="num">{money(d.budget)}</td>
              <td className="num" style={{ color: d.budget_pct === null ? undefined : d.budget_pct >= 1 ? 'var(--ok)' : 'var(--error)' }}>{pct(d.budget_pct)}</td>
              <td className="num">{num(d.orders)}</td>
              <td className="num">{money(d.wages)}</td>
              <td className="num">{pct(d.wage_pct)}</td>
              <td className="num">{money(d.voids)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Tiles({ f, money, label }: { f: Figures; money: Money; label: string }) {
  const vsBudget = f.budget_pct === null ? null : Math.round((f.budget_pct - 1) * 1000) / 10;
  return (
    <TileRow min={150}>
      <StatTile label={`Sales · ${label}`} value={money(f.sales)} sub={`ex GST ${money(f.sales_ex_gst)}`} changePct={vsBudget} changeLabel="vs budget" />
      <StatTile label="Wages" value={money(f.wages)} sub={`${pct(f.wage_pct)} of sales ex GST`} />
      <StatTile label="Food / beverage" value={`${pct(f.food_share)} / ${pct(f.beverage_share)}`} sub={`wages ${pct(f.food_wage_pct)} / ${pct(f.beverage_wage_pct)}`} />
      <StatTile label="Orders" value={num(f.orders)} sub={f.average_order !== null ? `average ${money(f.average_order)}` : undefined} />
      <StatTile label="Cash variance" value={money(f.cash_variance)} sub={`${num(f.cashup_buckets)} cash-up${f.cashup_buckets === 1 ? '' : 's'}`} />
    </TileRow>
  );
}

export default function FinanceWeekly({ data, props }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const title = (props?.title as string) || 'Weekly summary';
  const { isMobile } = useBreakpoint();

  const initial = useMemo(() => extractDoc(data), [data]);
  const [doc, setDoc] = useState<WeeklyDoc | null>(initial);
  const [loading, setLoading] = useState<boolean>(persistVenue && !initial);
  const [error, setError] = useState<string | null>(initial?.error || null);
  const [weekEnding, setWeekEnding] = useState<string>(initial?.week?.week_ending || lastCompletedSunday());
  const [currency, setCurrency] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({ group: true });
  const [period, setPeriod] = useState<'week' | 'mtd'>('week');

  // The organisation's currency, once: NZD and AUD organisations differ.
  useEffect(() => {
    const orgId = getStoredUser()?.active_organization_id;
    if (!orgId) return;
    let cancelled = false;
    apiFetch(`/api/organizations/${orgId}/company-settings`)
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => { if (!cancelled && s?.currency) setCurrency(String(s.currency)); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  const load = useCallback(async (we: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch('/api/connectors/loadedhub/execute/get_weekly_financials', {
        method: 'POST',
        body: JSON.stringify({ params: { venues: 'all', week_ending: we } }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        setError((body && (body.detail || body.error)) || `Could not load the weekly summary (${res.status})`);
        return;
      }
      const next = extractDoc(body?.data ?? body);
      if (!next || next.error) {
        setError(next?.error || 'The summary came back empty.');
        return;
      }
      setDoc(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the weekly summary.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (persistVenue && !initial) void load(weekEnding);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persistVenue]);

  const money = useMemo(() => makeMoney(currency), [currency]);
  const toggle = (key: string) => setOpen((o) => ({ ...o, [key]: !(o[key] ?? false) }));

  const weekLabel = doc?.week?.label?.replace('Week ending ', 'WE ') || 'Week';
  const mtdLabel = doc?.mtd?.label ? `MTD ${doc.mtd.label}` : 'Month to date';

  const picker = persistVenue ? (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <Button size="sm" variant="quiet" onClick={() => { const w = shiftWeek(weekEnding, -1); setWeekEnding(w); void load(w); }} aria-label="Previous week">‹</Button>
      <input
        type="date"
        className="n-input"
        value={weekEnding}
        onChange={(e) => { const v = e.target.value; if (v) { setWeekEnding(v); void load(v); } }}
        aria-label="Week ending"
        style={{ width: 160 }}
      />
      <Button size="sm" variant="quiet" onClick={() => { const w = shiftWeek(weekEnding, 1); setWeekEnding(w); void load(w); }} aria-label="Next week">›</Button>
      <Button size="sm" variant="quiet" icon={RefreshCw} onClick={() => void load(weekEnding)} aria-label="Refresh">Refresh</Button>
    </div>
  ) : null;

  const periodToggle = (
    <div role="tablist" aria-label="Period" style={{ display: 'inline-flex', gap: 4 }}>
      {(['week', 'mtd'] as const).map((p) => (
        <Button key={p} size="xs" variant={period === p ? 'primary' : 'quiet'} onClick={() => setPeriod(p)} role="tab" aria-selected={period === p}>
          {p === 'week' ? 'Week' : 'Month to date'}
        </Button>
      ))}
    </div>
  );

  const body = loading ? (
    <PageState kind="loading" title="Loading the weekly summary…" />
  ) : error ? (
    <PageState kind="error" title="Could not load the weekly summary." detail={error} />
  ) : !doc || !doc.group ? (
    <PageState kind="empty" title="No weekly summary." />
  ) : (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          {doc.week?.label}{doc.mtd?.label ? ` · month to date ${doc.mtd.label}` : ''}
          {doc.group.venues > 1 ? ` · ${doc.group.venues} venues` : ''}
        </span>
        {periodToggle}
      </div>
      <Tiles f={period === 'week' ? doc.group.week : doc.group.mtd} money={money} label={period === 'week' ? 'week' : 'month to date'} />
      {(doc.venues?.length ?? 0) > 1 && (
        <Fold title="Group" open={open.group ?? true} onToggle={() => toggle('group')} meta={`${doc.group.venues} venue${doc.group.venues === 1 ? '' : 's'} added`}>
          <FiguresTable week={doc.group.week} mtd={doc.group.mtd} money={money} weekLabel={weekLabel} mtdLabel={mtdLabel} />
        </Fold>
      )}
      {(doc.venues || []).map((v, i) => {
        const key = v.venue_id || v.venue;
        const isOpen = open[key] ?? (doc.venues!.length === 1 || i === 0);
        if (v.error || !v.week || !v.mtd) {
          return (
            <Fold key={key} title={v.venue} open={isOpen} onToggle={() => toggle(key)} meta="unavailable">
              <PageState kind="error" title={`${v.venue}: not available.`} detail={v.error || 'No figures came back.'} />
            </Fold>
          );
        }
        return (
          <Fold key={key} title={v.venue} open={isOpen} onToggle={() => toggle(key)} meta={`${money(v.week.sales)} · wages ${pct(v.week.wage_pct)}`}>
            {!isMobile && <div style={{ marginBottom: 10 }}><Tiles f={period === 'week' ? v.week : v.mtd} money={money} label={period === 'week' ? 'week' : 'MTD'} /></div>}
            <FiguresTable week={v.week} mtd={v.mtd} money={money} weekLabel={weekLabel} mtdLabel={mtdLabel} />
            {v.week.days && v.week.days.length > 0 && (
              <Fold title="The week, day by day" open={open[`${key}:days`] ?? false} onToggle={() => toggle(`${key}:days`)}>
                <DaysTable days={v.week.days} money={money} />
              </Fold>
            )}
          </Fold>
        );
      })}
      {(doc.warnings?.length || doc.notes?.length) ? (
        <Fold title="Notes" open={open.notes ?? false} onToggle={() => toggle('notes')} meta={doc.warnings?.length ? `${doc.warnings.length} warning${doc.warnings.length === 1 ? '' : 's'}` : undefined}>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            {(doc.warnings || []).map((w) => <li key={w} style={{ color: 'var(--warn)' }}>{w}</li>)}
            {(doc.notes || []).map((n) => <li key={n}>{n}</li>)}
          </ul>
        </Fold>
      ) : null}
    </div>
  );

  if (!persistVenue) {
    return (
      <div className="n-card" style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: 10 }}>
          <span style={{ fontWeight: 600 }}>{title}</span>
        </div>
        {body}
      </div>
    );
  }

  return (
    <div style={{ minWidth: 0 }}>
      <PageHeader title={title} meta={doc?.week?.label} actions={picker} />
      {body}
    </div>
  );
}
