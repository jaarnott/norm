'use client';

/**
 * Cost of goods for a period, two ways:
 *
 *  - from stocktakes, where the venue does full counts: opening stock +
 *    purchases − closing stock, per category (get_stocktakes view 'variance'
 *    with the category's list and the period — the tool picks the last count
 *    before the period and the last one in it);
 *  - from purchases (get_purchase_cogs): what was bought against what was
 *    sold, food and beverage split. For a venue that doesn't stocktake this is
 *    the only figure, and the page says plainly that it is purchases-based.
 */

import React, { useEffect, useRef, useState } from 'react';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import { StatTile, TileRow } from '../../functions/enquiries/bits';
import { runLoadedTool, type ToolResult } from './api';
import { Note, Section, SignedMoney, money } from './parts';
import {
  PERIODS, bucketLabel, bucketsWithoutSales, cogsShareExGst, formatDay, formatPct, formatPoints,
  intervalFor, type PurchaseCogsDoc, type VarianceDoc,
} from './logic';

export interface StocktakedCategory { template: string; category: string }

interface Props {
  venueId: string;
  venueName: string;
  timeZone: string | null;
  /** Full-count lists with at least two counts; [] = the venue doesn't stocktake;
   *  null = unknown (the stocktake list couldn't be read). */
  categories: StocktakedCategory[] | null;
  /** Why the stocktakes are unknown, in words, when `categories` is null. */
  unknownReason: string | null;
  period: string;
  onPeriod: (period: string) => void;
  stacked: boolean;
  /** Bumped by the page's Refresh button: read again, skipping the cache. */
  refreshKey: number;
}

type StockResult = ToolResult<VarianceDoc> & { loading: boolean };

export default function CostOfGoods({ venueId, venueName, timeZone, categories, unknownReason, period, onPeriod, stacked, refreshKey }: Props) {
  const [purchases, setPurchases] = useState<ToolResult<PurchaseCogsDoc> | null>(null);
  const [stock, setStock] = useState<Record<string, StockResult>>({});
  const templates = (categories ?? []).map((c) => c.template).join('|');

  // Try again re-reads only what failed: failures are never cached, so the
  // reports that worked come straight back. Refresh re-reads everything.
  const [retry, setRetry] = useState(0);
  const lastRefresh = useRef(refreshKey);

  useEffect(() => {
    let cancelled = false;
    const fresh = refreshKey !== lastRefresh.current;
    lastRefresh.current = refreshKey;
    setPurchases(null);
    void runLoadedTool<PurchaseCogsDoc>('get_purchase_cogs', { venue_id: venueId, period, interval: intervalFor(period) }, fresh)
      .then((r) => { if (!cancelled) setPurchases(r); });

    const initial: Record<string, StockResult> = {};
    for (const c of categories ?? []) initial[c.template] = { doc: null, error: null, loading: true };
    setStock(initial);
    for (const c of categories ?? []) {
      void runLoadedTool<VarianceDoc>('get_stocktakes', { venue_id: venueId, view: 'variance', template: c.template, period, top: 5 }, fresh)
        .then((r) => { if (!cancelled) setStock((s) => ({ ...s, [c.template]: { ...r, loading: false } })); });
    }
    return () => { cancelled = true; };
    // `templates` stands for `categories` (a new array each render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [venueId, period, templates, refreshKey, retry]);

  const tryAgain = <Button size="sm" variant="secondary" onClick={() => setRetry((n) => n + 1)}>Try again</Button>;

  const periodLabel = PERIODS.find((p) => p.value === period)?.label.toLowerCase() ?? period;

  const toolbar = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <select className="n-select" value={period} onChange={(e) => onPeriod(e.target.value)} aria-label="Period">
        {PERIODS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
      </select>
    </div>
  );

  // ── From stocktakes ──────────────────────────────────────────────────────
  const stockCard = (c: StocktakedCategory) => {
    const r = stock[c.template];
    if (!r || r.loading) {
      return (
        <div key={c.template} className="n-card">
          <PageState kind="loading" title={`Working out ${c.category.toLowerCase()} from the counts…`} detail="Loaded builds a stocktake report for this — up to a minute." />
        </div>
      );
    }
    if (r.error || !r.doc) {
      return (
        <div key={c.template} className="n-card" style={{ padding: '14px 16px' }}>
          <div style={{ fontWeight: 600 }}>{c.category}</div>
          <div style={{ marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            {/Need two completed/i.test(r.error || '')
              ? `There isn't a full ${c.category.toLowerCase()} count at both ends of ${periodLabel}, so it can't be worked out from stock. The purchases figure below still applies.`
              : `Loaded couldn't work this out: ${r.error}`}
          </div>
          {!/Need two completed/i.test(r.error || '') && <div style={{ marginTop: 8 }}>{tryAgain}</div>}
        </div>
      );
    }
    const d = r.doc;
    const t = d.totals;
    const share = cogsShareExGst(t.cost_of_goods, t.sales_ex_tax);
    const line = (label: React.ReactNode, value: React.ReactNode, strong = false) => (
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '5px 0', fontWeight: strong ? 600 : undefined, borderTop: strong ? '1px solid var(--line)' : undefined, marginTop: strong ? 4 : 0, paddingTop: strong ? 8 : 5 }}>
        <span style={{ color: strong ? 'var(--text)' : 'var(--text-soft)', minWidth: 0 }}>{label}</span>
        <span style={{ fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{value}</span>
      </div>
    );
    return (
      <div key={c.template} className="n-card" style={{ padding: '14px 16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--fs-md)' }}>{c.category}</span>
          <span style={{ fontSize: 'var(--fs-2xl)', fontWeight: 700, letterSpacing: '-0.01em' }}>{formatPct(share)}</span>
        </div>
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 8 }}>
          of {money(t.sales_ex_tax ?? 0, 0)} {c.category.toLowerCase()} sales ex GST · {formatDay(d.opening.completed, timeZone, false)} to {formatDay(d.closing.completed, timeZone)}
        </div>
        <div style={{ fontSize: 'var(--fs-base)' }}>
          {line(`Opening stock (${formatDay(d.opening.completed, timeZone, false)})`, money(t.opening_value, 0))}
          {line('+ Purchases', money(t.purchases, 0))}
          {line(`− Closing stock (${formatDay(d.closing.completed, timeZone, false)})`, money(t.closing_value, 0))}
          {line('Cost of goods', money(t.cost_of_goods, 0), true)}
          {line('Variance in the counts', <SignedMoney value={t.total_variance} />)}
        </div>
      </div>
    );
  };

  let stockSection: React.ReactNode;
  if (categories === null) {
    stockSection = unknownReason ? <Note>{unknownReason}</Note> : null;
  } else if (categories.length === 0) {
    stockSection = (
      <Note>
        {venueName} doesn&apos;t do full stocktakes in Loaded (at least two full Food or Beverage counts), so cost of goods
        here is purchases against sales.
      </Note>
    );
  } else {
    stockSection = (
      <Section
        title="From your stocktakes"
        sub="Opening stock + purchases − closing stock, between the last full count before the period and the last one in it"
      >
        <div style={{ display: 'grid', gridTemplateColumns: stacked ? '1fr' : 'repeat(auto-fit, minmax(300px, 1fr))', gap: 10, alignItems: 'start' }}>
          {(categories ?? []).map(stockCard)}
        </div>
      </Section>
    );
  }

  // ── From purchases ───────────────────────────────────────────────────────
  let purchaseBody: React.ReactNode;
  if (!purchases) {
    purchaseBody = <div className="n-card"><PageState kind="loading" title="Adding up purchases and sales…" /></div>;
  } else if (purchases.error || !purchases.doc) {
    purchaseBody = <PageState kind="error" title="Couldn't work out purchases against sales." detail={purchases.error || undefined} action={tryAgain} />;
  } else {
    const d = purchases.doc;
    const s = d.summary;
    const prev = s.previous;
    const change = formatPoints(s.cogs_pct_change);
    const noSales = bucketsWithoutSales(d.rows || []);
    const interval = d.interval || intervalFor(period);
    purchaseBody = (
      <>
        <TileRow min={stacked ? 140 : 160}>
          <StatTile
            label="Cost of goods"
            value={formatPct(s.cogs_pct)}
            sub={prev && prev.cogs_pct !== null && prev.cogs_pct !== undefined ? `${formatPct(prev.cogs_pct)} the period before${change ? ` (${change})` : ''}` : 'purchases ÷ sales'}
          />
          <StatTile label="Purchases" value={money(s.purchases, 0)} sub="ex GST, credits taken off" />
          <StatTile label="Sales" value={money(s.sales, 0)} sub="ex GST" />
          <StatTile label="Food" value={formatPct(s.food_cogs_pct)} sub={`${money(s.food, 0)} on ${money(s.food_sales, 0)} sales`} />
          <StatTile label="Beverage" value={formatPct(s.beverage_cogs_pct)} sub={`${money(s.beverage, 0)} on ${money(s.beverage_sales, 0)} sales`} />
        </TileRow>
        {(d.rows || []).length > 1 && (
          stacked ? (
            <ul style={{ listStyle: 'none', margin: '10px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {d.rows.map((r) => (
                <li key={r.bucket} className="n-card" style={{ padding: '10px 12px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ fontWeight: 600 }}>{bucketLabel(r, interval)}</span>
                    <span style={{ fontWeight: 600 }}>{r.sales ? formatPct(r.cogs_pct) : 'No sales'}</span>
                  </div>
                  <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)', display: 'flex', flexWrap: 'wrap', gap: '2px 10px' }}>
                    <span>Bought {money(r.purchases, 0)}</span>
                    <span>Sold {money(r.sales, 0)}</span>
                    <span>Food {formatPct(r.food_cogs_pct)}</span>
                    <span>Beverage {formatPct(r.beverage_cogs_pct)}</span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="n-card" style={{ overflowX: 'auto', marginTop: 10 }}>
              <table className="n-table">
                <thead>
                  <tr>
                    <th>{interval === 'week' ? 'Week' : 'Month'}</th>
                    <th className="num">Purchases</th>
                    <th className="num">Sales</th>
                    <th className="num">Cost of goods</th>
                    <th className="num">Food</th>
                    <th className="num">Beverage</th>
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((r) => (
                    <tr key={r.bucket}>
                      <td style={{ fontWeight: 500 }}>{bucketLabel(r, interval)}</td>
                      <td className="num">{money(r.purchases, 0)}</td>
                      <td className="num">{r.sales ? money(r.sales, 0) : <span style={{ color: 'var(--muted)' }}>No sales</span>}</td>
                      <td className="num" style={{ fontWeight: 600 }}>{formatPct(r.cogs_pct)}</td>
                      <td className="num">{formatPct(r.food_cogs_pct)}</td>
                      <td className="num">{formatPct(r.beverage_cogs_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
        <Note>
          Purchases-based: what was bought in the period against what was sold, not what was used — stock left on the
          shelves isn&apos;t counted, so a big delivery near the end of a period pushes it up.
        </Note>
        {noSales.length > 0 && (
          <Note tone="warn">
            Loaded has no sales for {noSales.join(', ')}, so those purchases have nothing to set against and the total
            percentage reads high.
          </Note>
        )}
        {!!s.uncategorised_sales_pct && s.uncategorised_sales_pct > 0.005 && (
          <Note tone="warn">
            {formatPct(s.uncategorised_sales_pct, 0)} of sales have no food or beverage category in the POS, so the food and
            beverage figures leave them out.
          </Note>
        )}
      </>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Cost of goods for {periodLabel} at {venueName}</div>
        {toolbar}
      </div>
      {stockSection}
      <Section
        title="Purchases against sales"
        sub={categories && categories.length ? 'The same period from invoices instead of counts' : undefined}
      >
        {purchaseBody}
      </Section>
    </div>
  );
}
