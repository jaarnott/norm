'use client';

/**
 * One stocktake, opened from the list: its value, what it cost (opening stock
 * + purchases − this count), its value by category, and the biggest variances
 * between what should have been on hand and what was counted.
 *
 * Two reads, side by side:
 *  - get_stocktakes view 'variance' with this count and the one before it of
 *    the same list (Loaded's stocktake report — up to a minute);
 *  - get_stock view 'on_hand' for the category at the moment the count was
 *    completed (about 12 s) — the value by category. Full counts only.
 */

import React, { useEffect, useMemo, useState } from 'react';
import Button from '../../ui/Button';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import { StatTile, TileRow } from '../../functions/enquiries/bits';
import { runLoadedTool } from './api';
import { CategoryBars, Note, Section, SignedMoney, money } from './parts';
import {
  categoryOf, cogsShareExGst, countKind, countLabel, expectedOnHand, formatDay, formatPct, formatQty,
  snapshotMatches, valueByCategory,
  type OnHandDoc, type StocktakeRow, type VarianceDoc,
} from './logic';

interface Props {
  venueId: string;
  timeZone: string | null;
  row: StocktakeRow;
  previous: StocktakeRow | null;
  stacked: boolean;
  onBack: () => void;
  /** Values learned from the report, by stocktake id — the list shows them. */
  onValues: (values: Record<string, number>) => void;
}

const TOP = 20;

export default function StocktakeDetail({ venueId, timeZone, row, previous, stacked, onBack, onValues }: Props) {
  const kind = countKind(row.template);
  const category = categoryOf(row.template);

  const [variance, setVariance] = useState<VarianceDoc | null>(null);
  const [varianceError, setVarianceError] = useState<string | null>(null);
  const [varianceLoading, setVarianceLoading] = useState<boolean>(!!previous);
  const [onHand, setOnHand] = useState<OnHandDoc | null>(null);
  const [onHandError, setOnHandError] = useState<string | null>(null);
  const [onHandLoading, setOnHandLoading] = useState<boolean>(kind === 'full');
  // Try again: failures are never cached, so only what failed is read again.
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    if (previous) {
      setVarianceLoading(true);
      void runLoadedTool<VarianceDoc>('get_stocktakes', {
        venue_id: venueId, view: 'variance', opening_id: previous.id, closing_id: row.id, top: TOP,
      }).then(({ doc, error }) => {
        if (cancelled) return;
        setVariance(doc);
        setVarianceError(error);
        setVarianceLoading(false);
        if (doc?.totals) {
          const t = doc.totals;
          // Area lists can come back with no values at all — don't remember a $0.
          if (t.closing_value || t.opening_value) {
            onValues({ [row.id]: t.closing_value, [previous.id]: t.opening_value });
          }
        }
      });
    }
    if (kind === 'full' && category && row.completed) {
      setOnHandLoading(true);
      void runLoadedTool<OnHandDoc>('get_stock', {
        venue_id: venueId, view: 'on_hand', template: category, as_of: row.completed, top: 1000,
      }).then(({ doc, error }) => {
        if (cancelled) return;
        setOnHand(doc);
        setOnHandError(error);
        setOnHandLoading(false);
      });
    }
    return () => { cancelled = true; };
    // onValues is a fresh closure each render; the reads depend only on the count.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [venueId, row.id, previous?.id, kind, category, retry]);

  const tryAgain = <Button size="sm" variant="secondary" onClick={() => setRetry((n) => n + 1)}>Try again</Button>;

  const t = variance?.totals;
  const hasValues = !!t && (!!t.closing_value || !!t.opening_value);
  const share = t ? cogsShareExGst(t.cost_of_goods, t.sales_ex_tax) : null;

  const categories = useMemo(() => (onHand ? valueByCategory(onHand) : []), [onHand]);
  const snapshotTotal = onHand?.total_value_on_hand ?? 0;
  const matches = hasValues && onHand ? snapshotMatches(snapshotTotal, t!.closing_value) : null;

  const title = kind === 'full' ? `${category} count` : countLabel(row);
  const meta = [
    `Completed ${formatDay(row.completed, timeZone)}`,
    row.title && row.title !== title ? row.title : null,
    previous ? `compared with the count completed ${formatDay(previous.completed, timeZone)}${variance?.days ? ` (${formatQty(variance.days)} days)` : ''}` : null,
  ].filter(Boolean).join(' · ');

  // ── Tiles ────────────────────────────────────────────────────────────────
  const tiles = hasValues && t ? (
    <TileRow min={stacked ? 140 : 160}>
      <StatTile label="Stock value" value={money(t.closing_value, 0)} sub="at this count" />
      <StatTile label="Previous count" value={money(t.opening_value, 0)} sub={formatDay(previous?.completed, timeZone)} />
      <StatTile label="Purchases" value={money(t.purchases, 0)} sub="between the two counts" />
      <StatTile
        label="Cost of goods"
        value={money(t.cost_of_goods, 0)}
        sub={share !== null ? `${formatPct(share)} of ${money(t.sales_ex_tax ?? 0, 0)} sales ex GST` : 'No sales recorded'}
      />
      <StatTile
        label="Variance"
        value={<SignedMoney value={t.total_variance} />}
        sub={`Short ${money(Math.abs(t.shortfall_value), 0)} · over ${money(t.surplus_value, 0)}`}
      />
    </TileRow>
  ) : null;

  // ── Value by category ───────────────────────────────────────────────────
  let categorySection: React.ReactNode = null;
  if (kind === 'full') {
    categorySection = (
      <Section
        title="Value by category"
        sub={hasValues && matches ? 'What was counted, by stock group' : 'Stock on hand by stock group when the count was completed'}
      >
        {onHandLoading ? (
          <div className="n-card"><PageState kind="loading" title="Reading stock on hand…" /></div>
        ) : onHandError ? (
          <PageState kind="error" title="Couldn't read the value by category." detail={onHandError} action={tryAgain} />
        ) : !categories.length ? (
          <div className="n-card"><PageState kind="empty" title="Nothing with a value on hand." /></div>
        ) : (
          <>
            <CategoryBars rows={categories} total={snapshotTotal} compact={stacked} />
            {matches === false && t && (
              <Note tone="warn">
                This split is stock on hand when the count was completed ({money(snapshotTotal, 0)}), which
                differs from the count itself ({money(t.closing_value, 0)}) — usually because the count was
                finished some time after it was taken.
              </Note>
            )}
          </>
        )}
      </Section>
    );
  }

  // ── Variances ───────────────────────────────────────────────────────────
  const rows = variance?.variances ?? [];
  const varianceTable = (
    <div className="n-card" style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <table className="n-table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Group</th>
            <th className="num">Expected</th>
            <th className="num">Counted</th>
            <th className="num">Difference</th>
            <th className="num">Value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((v, i) => (
            <tr key={`${v.item}-${i}`}>
              <td style={{ fontWeight: 500 }}>{v.item}</td>
              <td style={{ color: 'var(--text-soft)' }}>{v.group || '—'}</td>
              <td className="num">{formatQty(expectedOnHand(v))}</td>
              <td className="num">{formatQty(v.closing)}</td>
              <td className="num" style={{ color: 'var(--text-soft)', whiteSpace: 'nowrap' }}>
                {v.variance_qty > 0 ? '+' : ''}{formatQty(v.variance_qty)}{v.unit ? ` ${v.unit}` : ''}
              </td>
              <td className="num"><SignedMoney value={v.variance_value} dp={2} strong /></td>
            </tr>
          ))}
          {variance?.others && (
            <tr>
              <td colSpan={5} style={{ color: 'var(--muted)' }}>{variance.others.count} more item{variance.others.count === 1 ? '' : 's'} with smaller differences</td>
              <td className="num"><SignedMoney value={variance.others.variance_value} dp={2} /></td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
  const varianceCards = (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {rows.map((v, i) => (
        <li key={`${v.item}-${i}`} className="n-card" style={{ padding: '10px 12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
            <span style={{ fontWeight: 600, minWidth: 0 }}>{v.item}</span>
            <SignedMoney value={v.variance_value} dp={2} strong />
          </div>
          <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)', display: 'flex', flexWrap: 'wrap', gap: '2px 10px' }}>
            {v.group && <span>{v.group}</span>}
            <span>Expected {formatQty(expectedOnHand(v))}</span>
            <span>Counted {formatQty(v.closing)}{v.unit ? ` ${v.unit}` : ''}</span>
          </div>
        </li>
      ))}
      {variance?.others && (
        <li style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '4px 2px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          <span>{variance.others.count} more with smaller differences</span>
          <SignedMoney value={variance.others.variance_value} dp={2} />
        </li>
      )}
    </ul>
  );

  let varianceSection: React.ReactNode;
  if (!previous) {
    varianceSection = (
      <Section title="Biggest variances">
        <div className="n-card">
          <PageState
            kind="empty"
            title={`No earlier ${kind === 'full' ? `${category} count` : 'count of this list'} to compare with.`}
            detail="A variance needs this count and the one before it of the same list."
          />
        </div>
      </Section>
    );
  } else if (varianceLoading) {
    varianceSection = null; // the loading card above the tiles covers it
  } else if (varianceError) {
    varianceSection = (
      <Section title="Biggest variances">
        <PageState kind="error" title="Loaded couldn't compare these two counts." detail={varianceError} action={tryAgain} />
      </Section>
    );
  } else if (variance) {
    varianceSection = (
      <Section
        title="Biggest variances"
        sub="What should have been on hand (last count + received − what sales say was used) against what was counted, largest in dollars first"
      >
        {rows.length === 0 ? (
          <div className="n-card"><PageState kind="empty" title="No differences between expected and counted stock." /></div>
        ) : stacked ? varianceCards : varianceTable}
        {variance.not_pos_linked && variance.not_pos_linked.count > 0 && (
          <Note>
            {variance.not_pos_linked.count} item{variance.not_pos_linked.count === 1 ? ' isn’t' : 's aren’t'} linked to a menu item
            in the POS, so Loaded can&apos;t say how much should have been used — they used {money(variance.not_pos_linked.usage_value)} between
            the counts
            {variance.not_pos_linked.top.length > 0 && <> (most: {variance.not_pos_linked.top.slice(0, 3).map((u) => `${u.item} ${money(u.usage_value)}`).join(', ')})</>}.
          </Note>
        )}
        {variance.coverage && (
          <div style={{ marginTop: 8, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            {variance.coverage.items} items on the list · {variance.coverage.counted_both_ends} counted both times
          </div>
        )}
        {variance.warning && <Note tone="warn">{variance.warning}</Note>}
      </Section>
    );
  }

  return (
    <div>
      <PageHeader title={title} meta={meta} back={{ label: 'All stocktakes', onClick: onBack }} />

      {previous && varianceLoading && (
        <div className="n-card">
          <PageState
            kind="loading"
            title="Loaded is building the stocktake report…"
            detail="It goes through every line of both counts, so it can take up to a minute."
          />
        </div>
      )}
      {tiles}
      {variance && !hasValues && (
        <Note>
          Loaded&apos;s stocktake report has no stock values for this list — area and daily lists usually cover only part
          of the stock. The differences below are still item by item.
        </Note>
      )}
      {categorySection}
      {varianceSection}
    </div>
  );
}
