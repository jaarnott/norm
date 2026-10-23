'use client';

/**
 * Staff: what each person rang up in the period (get_sales breakdown 'staff'
 * — Loaded's staff orders: orders taken and their value, GST included).
 */

import React, { useMemo } from 'react';
import { formatMoney, formatNumber } from '../../../lib/format';
import PageState from '../../ui/PageState';
import { Section, StatTile, TileRow } from '../../functions/enquiries/bits';
import { staffLines, type StaffDoc } from './salesLogic';

function ShareBar({ share, max }: { share: number; max: number }) {
  const width = max ? Math.max(2, (share / max) * 100) : 0;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, width: '100%', justifyContent: 'flex-end' }}>
      <span aria-hidden style={{ flex: '1 1 auto', maxWidth: 120, height: 6, borderRadius: 3, background: 'var(--surface-alt)', overflow: 'hidden' }}>
        <span style={{ display: 'block', height: '100%', width: `${width}%`, background: 'var(--brand-soft)', borderRadius: 3 }} />
      </span>
      <span style={{ minWidth: 44, textAlign: 'right' }}>{share.toFixed(1)}%</span>
    </span>
  );
}

export default function StaffTab({ doc, loading, error, stacked }: {
  doc: StaffDoc | null;
  loading: boolean;
  error: string | null;
  stacked: boolean;
}) {
  const lines = useMemo(() => staffLines(doc?.rows ?? []), [doc]);
  if (loading) return <div className="n-card"><PageState kind="loading" title="Loading staff sales from Loaded…" detail="This can take up to half a minute." /></div>;
  if (error) return <PageState kind="error" title="Could not load staff sales." detail={error} />;
  if (!doc || !lines.length) return <div className="n-card"><PageState kind="empty" title="Nobody rang up a sale in this period." /></div>;

  const sales = doc.totals?.sales ?? lines.reduce((s, l) => s + l.sales, 0);
  const orders = doc.totals?.orders ?? lines.reduce((s, l) => s + l.orders, 0);
  const maxShare = Math.max(...lines.map((l) => l.share));
  const top = lines[0];

  const table = (
    <table className="n-table">
      <thead>
        <tr>
          <th style={{ width: 32 }} aria-label="Rank" />
          <th>Staff member</th>
          <th className="num">Orders</th>
          <th className="num">Sales</th>
          <th className="num">Average order</th>
          <th className="num" style={{ width: 200 }}>Share of sales</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((l, i) => (
          <tr key={l.staff}>
            <td style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>{i + 1}</td>
            <td style={{ fontWeight: 500 }}>{l.staff}</td>
            <td className="num">{formatNumber(l.orders)}</td>
            <td className="num" style={{ fontWeight: 600 }}>{formatMoney(l.sales)}</td>
            <td className="num">{l.average === null ? '—' : formatMoney(l.average)}</td>
            <td className="num"><ShareBar share={l.share} max={maxShare} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  const cards = (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {lines.map((l, i) => (
        <li key={l.staff} style={{ padding: '10px 16px', borderTop: i ? '1px solid var(--line-soft)' : undefined }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
            <span style={{ fontWeight: 600 }}><span style={{ color: 'var(--muted)', fontWeight: 400, marginRight: 6 }}>{i + 1}</span>{l.staff}</span>
            <span style={{ fontWeight: 600 }}>{formatMoney(l.sales)}</span>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            <span>{formatNumber(l.orders)} orders</span>
            <span>Average {l.average === null ? '—' : formatMoney(l.average)}</span>
            <span>{l.share.toFixed(1)}% of sales</span>
          </div>
        </li>
      ))}
    </ul>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <TileRow min={150}>
        <StatTile label="Staff selling" value={formatNumber(lines.length)} sub="rang up at least one sale" />
        <StatTile label="Orders" value={formatNumber(orders)} />
        <StatTile label="Average order" value={orders ? formatMoney(sales / orders) : '—'} sub="across all staff" />
        {top && <StatTile label="Top seller" value={formatMoney(top.sales, 0)} sub={`${top.staff} · ${top.share.toFixed(0)}% of sales`} />}
      </TileRow>
      <Section title="Sales by staff member" meta={`${formatMoney(sales)} incl. GST`} padded={false}>
        {stacked ? cards : <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>{table}</div>}
      </Section>
    </div>
  );
}
