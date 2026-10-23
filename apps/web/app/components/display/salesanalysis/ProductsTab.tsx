'use client';

/**
 * Products: sales by revenue group, the top and slowest sellers, and every
 * item sold (get_sales breakdown 'items', all rows). Item sales are Loaded's
 * line prices — before discounts, GST included — so they add up to a little
 * more than the period's sales.
 */

import React, { useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { formatMoney, formatNumber } from '../../../lib/format';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageState from '../../ui/PageState';
import { Section, StatTile, TileRow } from '../../functions/enquiries/bits';
import RevenueGroupDialog, { GROUP_CHANGES_LIVE } from './RevenueGroupDialog';
import {
  bottomSellers, groupOf, groupSplit, searchItems, topSellers,
  type ItemRow, type ItemsDoc, type RankBy,
} from './salesLogic';

const LIST_SIZE = 10;
const PAGE = 25;

/** Groups carry no status, so they stay neutral; only an item with no group
 *  at all (one to sort) is marked. */
function groupTone(group: string): 'neutral' | 'warn' {
  return group === 'Unassigned' ? 'warn' : 'neutral';
}

const qty = (n: number) => formatNumber(n, Number.isInteger(n) ? 0 : 1);

function RankedList({ rows, by, start = 1 }: { rows: ItemRow[]; by: RankBy; start?: number }) {
  if (!rows.length) return <div style={{ padding: '12px 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Nothing to show.</div>;
  return (
    <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {rows.map((r, i) => (
        <li key={`${r.item}|${r.group}|${r.category}`} style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: '8px 16px', borderTop: i ? '1px solid var(--line-soft)' : undefined }}>
          <span style={{ width: 20, flex: '0 0 auto', textAlign: 'right', fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontVariantNumeric: 'tabular-nums' }}>{start + i}</span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', fontWeight: 500, color: 'var(--text)', overflowWrap: 'anywhere' }}>{r.item}</span>
            <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{[r.group, groupOf(r)].filter((x, j, a) => x && a.indexOf(x) === j).join(' · ')}</span>
          </span>
          <span style={{ flex: '0 0 auto', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
            <span style={{ display: 'block', fontWeight: 600, color: 'var(--text)' }}>{by === 'sales' ? formatMoney(r.sales, 0) : qty(r.quantity)}</span>
            <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{by === 'sales' ? `${qty(r.quantity)} sold` : formatMoney(r.sales, 0)}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

export default function ProductsTab({ doc, loading, error, stacked, venueId, onChanged }: {
  doc: ItemsDoc | null;
  loading: boolean;
  error: string | null;
  stacked: boolean;
  venueId: string;
  /** A group changed: reload the items. */
  onChanged: (message: string) => void;
}) {
  const [filter, setFilter] = useState<string>('all');
  const [by, setBy] = useState<RankBy>('sales');
  const [search, setSearch] = useState('');
  const [shown, setShown] = useState(PAGE);
  const [changing, setChanging] = useState<ItemRow | null>(null);

  const rows = useMemo(() => doc?.rows ?? [], [doc]);
  const split = useMemo(() => groupSplit(rows), [rows]);
  const total = useMemo(() => split.reduce((s, g) => s + g.sales, 0), [split]);
  const inGroup = useMemo(() => (filter === 'all' ? rows : rows.filter((r) => r.item !== '(others)' && groupOf(r) === filter)), [rows, filter]);
  const top = useMemo(() => topSellers(inGroup, by, LIST_SIZE), [inGroup, by]);
  const bottom = useMemo(() => bottomSellers(inGroup, by, LIST_SIZE), [inGroup, by]);
  const listed = useMemo(() => topSellers(searchItems(inGroup, search), by, Number.MAX_SAFE_INTEGER), [inGroup, search, by]);

  if (loading) return <div className="n-card"><PageState kind="loading" title="Loading product sales from Loaded…" detail="This can take up to half a minute." /></div>;
  if (error) return <PageState kind="error" title="Could not load product sales." detail={error} />;
  if (!doc || !rows.length) return <div className="n-card"><PageState kind="empty" title="No products sold in this period." /></div>;

  const groupLabel = filter === 'all' ? null : filter;
  const action = (r: ItemRow) => GROUP_CHANGES_LIVE ? (
    <Button size="xs" variant="quiet" onClick={() => setChanging(r)}>Change group</Button>
  ) : null;

  const table = (
    <table className="n-table">
      <thead>
        <tr>
          <th>Item</th>
          <th>POS group</th>
          <th>Revenue group</th>
          <th className="num">Sold</th>
          <th className="num">Sales</th>
          <th className="num">Share</th>
          {GROUP_CHANGES_LIVE && <th aria-label="Change group" />}
        </tr>
      </thead>
      <tbody>
        {listed.slice(0, shown).map((r) => (
          <tr key={`${r.item}|${r.group}|${r.category}`}>
            <td style={{ fontWeight: 500 }}>{r.item}</td>
            <td style={{ color: 'var(--text-soft)' }}>{r.group || '—'}</td>
            <td><Badge tone={groupTone(groupOf(r))}>{groupOf(r)}</Badge></td>
            <td className="num">{qty(r.quantity)}</td>
            <td className="num" style={{ fontWeight: 600 }}>{formatMoney(r.sales)}</td>
            <td className="num" style={{ color: 'var(--muted)' }}>{total ? `${((r.sales / total) * 100).toFixed(1)}%` : '—'}</td>
            {GROUP_CHANGES_LIVE && <td style={{ textAlign: 'right' }}>{action(r)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );

  const cards = (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {listed.slice(0, shown).map((r, i) => (
        <li key={`${r.item}|${r.group}|${r.category}`} style={{ padding: '10px 16px', borderTop: i ? '1px solid var(--line-soft)' : undefined }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
            <span style={{ fontWeight: 600, minWidth: 0, overflowWrap: 'anywhere' }}>{r.item}</span>
            <span style={{ fontWeight: 600, flex: '0 0 auto' }}>{formatMoney(r.sales)}</span>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 8px', marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)', alignItems: 'center' }}>
            <Badge tone={groupTone(groupOf(r))}>{groupOf(r)}</Badge>
            {r.group && r.group !== groupOf(r) && <span>{r.group}</span>}
            <span>{qty(r.quantity)} sold</span>
            {action(r)}
          </div>
        </li>
      ))}
    </ul>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <TileRow min={150}>
          <StatTile
            label="All products"
            value={formatMoney(total, 0)}
            sub={`${formatNumber(split.reduce((s, g) => s + g.items, 0))} items sold`}
            onClick={() => setFilter('all')}
            selected={filter === 'all'}
          />
          {split.map((g) => (
            <StatTile
              key={g.group}
              label={g.group}
              value={formatMoney(g.sales, 0)}
              sub={`${g.share}% · ${formatNumber(g.items)} item${g.items === 1 ? '' : 's'}`}
              onClick={() => setFilter(filter === g.group ? 'all' : g.group)}
              selected={filter === g.group}
              title={g.revenueGroup ? undefined : `Loaded reports these items under "${g.group}" — not one of the revenue groups Food, Beverage or Other`}
            />
          ))}
        </TileRow>
        <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          Sales by revenue group, as Loaded reports it: an item with no revenue group counts under its POS category.
          Item prices are before discounts and include GST.
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <label htmlFor="sa-rank" style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>Rank by</label>
        <select id="sa-rank" className="n-select" value={by} onChange={(e) => setBy(e.target.value as RankBy)}>
          <option value="sales">Sales ($)</option>
          <option value="quantity">Number sold</option>
        </select>
        {groupLabel && (
          <Button size="sm" variant="quiet" onClick={() => setFilter('all')}>Showing {groupLabel} only · show all</Button>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: stacked ? '1fr' : 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
        <Section title="Top sellers" meta={by === 'sales' ? 'by sales' : 'by number sold'} padded={false}>
          <RankedList rows={top} by={by} />
        </Section>
        <Section title="Slowest sellers" meta={`${by === 'sales' ? 'lowest sales' : 'fewest sold'}, of items that sold`} padded={false}>
          <RankedList rows={bottom} by={by} />
        </Section>
      </div>

      <Section
        title={groupLabel ? `${groupLabel} items` : 'All items'}
        meta={`${formatNumber(listed.length)} item${listed.length === 1 ? '' : 's'}`}
        padded={false}
        actions={(
          <span style={{ position: 'relative', display: 'inline-flex' }}>
            <Icon icon={Search} size={14} tone="muted" style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
            <input
              className="n-input"
              placeholder="Search items"
              aria-label="Search items"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setShown(PAGE); }}
              style={{ paddingLeft: 28, width: stacked ? 170 : 220 }}
            />
          </span>
        )}
      >
        {listed.length === 0 ? (
          <PageState kind="empty" title="No items match." />
        ) : stacked ? cards : (
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>{table}</div>
        )}
        {listed.length > shown && (
          <div style={{ padding: '10px 16px', borderTop: '1px solid var(--line-soft)' }}>
            <Button size="sm" variant="quiet" onClick={() => setShown((n) => n + PAGE * 4)}>
              Show more ({formatNumber(listed.length - shown)} left)
            </Button>
          </div>
        )}
      </Section>

      {changing && (
        <RevenueGroupDialog
          item={changing.item}
          current={groupOf(changing)}
          venueId={venueId}
          onClose={() => setChanging(null)}
          onDone={(message) => { setChanging(null); onChanged(message); }}
        />
      )}
    </div>
  );
}
