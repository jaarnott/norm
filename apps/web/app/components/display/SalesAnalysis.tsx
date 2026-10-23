'use client';

/**
 * Sales Analysis — the Reports agent's Sales Analysis page (`sales_analysis`,
 * docs/plans/agents-and-apps-restructure.md §5).
 *
 * A period, a comparison and a venue, then three tabs:
 *  - Products: sales by revenue group, top and slowest sellers, every item;
 *  - Staff: what each person rang up;
 *  - Compare: the period against the one before, last year or the budget,
 *    day by day.
 *
 * Everything is `loadedhub.get_sales`, run through
 * `POST /api/connectors/loadedhub/execute/get_sales` with the venue's id (its
 * own Loaded login, strictly) and the period in plain English — the tool
 * resolves it on the venue's trading day. Answers are cached per question
 * (salesanalysis/useSalesDocs), so tabs and comparisons don't ask twice.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import { formatMoney } from '../../lib/format';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Tabs from '../ui/Tabs';
import { StatTile, TileRow } from '../functions/enquiries/bits';
import ConnectPrompt from './ConnectPrompt';
import ProductsTab from './salesanalysis/ProductsTab';
import StaffTab from './salesanalysis/StaffTab';
import CompareTab, { signedMoney } from './salesanalysis/CompareTab';
import { useSalesDocs } from './salesanalysis/useSalesDocs';
import {
  COMPARISON_OPTIONS, PERIODS, buildCompare, clockLabel, comparisonLabel, dayLabel, isConnectionError,
  periodTotal, previousPeriod, rangeLabel,
  type Comparison, type DailyDoc, type ItemsDoc, type Period, type StaffDoc,
} from './salesanalysis/salesLogic';

interface VenueOption { id: string; name: string }

/** Venues with a working Loaded connection (connect-info). */
async function connectedVenues(): Promise<Set<string>> {
  const info = await apiFetch('/api/connectors/loadedhub/connect-info')
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return new Set<string>(
    ((info?.venues ?? []) as { venue_id: string; status: string }[])
      .filter((v) => v.status === 'connected')
      .map((v) => v.venue_id),
  );
}

type Tab = 'products' | 'staff' | 'compare';

const TABS: { id: Tab; label: string }[] = [
  { id: 'products', label: 'Products' },
  { id: 'staff', label: 'Staff' },
  { id: 'compare', label: 'Compare' },
];

// Every daily ask carries budget and last year, so switching between those
// comparisons — or to a period that was another's "period before" — asks
// nothing new.
const DAILY = { breakdown: 'daily', compare: ['budget', 'last_year'] };
// All items (no "(others)" roll-up), so the groups and the slowest sellers
// are complete; a busy venue's month is a few hundred rows.
const ITEMS = { breakdown: 'items', top: 5000 };
const STAFF = { breakdown: 'staff' };

export default function SalesAnalysis({ props }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const { isMobile, isTablet } = useBreakpoint();
  const stacked = isMobile || isTablet;

  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const propVenue = (props?.activeVenueId as string) || null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  // Which venues hold a Loaded connection. The page never asks Loaded for a
  // venue without one; it shows the connect card instead.
  const [connected, setConnected] = useState<Set<string> | null>(null);
  // The venue the person picked here; until then the page follows the app's
  // venue (FunctionalPage passes it once the app's venues have settled and
  // whenever it changes), then the remembered one, then the first connected.
  const [picked, setPicked] = useState<string | null>(null);

  const [period, setPeriod] = useState<Period>('last week');
  const [comparison, setComparison] = useState<Comparison>('previous');
  const [tab, setTab] = useState<Tab>('products');
  const [notice, setNotice] = useState<string | null>(null);
  const { load, forget, get } = useSalesDocs();

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch('/api/venues').then((r) => (r.ok ? r.json() : null)).catch(() => null),
      connectedVenues(),
    ]).then(([d, ok]) => {
      if (cancelled) return;
      setVenues(d ? (Array.isArray(d) ? d : (d.venues ?? [])) : []);
      setConnected(ok);
    });
    return () => { cancelled = true; };
  }, []);

  // The connect card runs the sign-in popup; its callback page posts
  // `oauth-complete` here. Re-read the connections so the page loads.
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type !== 'oauth-complete') return;
      if (!/loaded/i.test(String(event.data?.connector || ''))) return;
      void connectedVenues().then(setConnected);
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

  // Only venues in the person's own list count (a remembered venue they've
  // since lost falls through to the next choice).
  const venueId = useMemo(() => {
    const listed = (id: string | null) => (id && venues.some((v) => v.id === id) ? id : null);
    return listed(picked) ?? listed(propVenue) ?? listed(persistVenue ? sharedVenue : null)
      ?? venues.find((v) => connected?.has(v.id))?.id ?? venues[0]?.id ?? null;
  }, [picked, propVenue, persistVenue, sharedVenue, venues, connected]);

  const handleVenueChange = (id: string) => {
    setPicked(id);
    setNotice(null);
    if (persistVenue) setActiveVenue(id);
  };

  const venueConnected = !!venueId && !!connected && connected.has(venueId);

  // ── what to ask ──
  const dailyKey = venueConnected ? `daily|${venueId}|${period}` : null;
  const daily = get<DailyDoc>(dailyKey);
  const prevPhrase = comparison === 'previous' && daily?.doc ? previousPeriod(period, daily.doc.window.start) : null;
  const prevKey = venueConnected && prevPhrase ? `daily|${venueId}|${prevPhrase}` : null;
  const prev = get<DailyDoc>(prevKey);
  const itemsKey = venueConnected && tab === 'products' ? `items|${venueId}|${period}` : null;
  const items = get<ItemsDoc>(itemsKey);
  const staffKey = venueConnected && tab === 'staff' ? `staff|${venueId}|${period}` : null;
  const staff = get<StaffDoc>(staffKey);

  useEffect(() => {
    if (dailyKey && venueId) load(dailyKey, venueId, { ...DAILY, period });
  }, [dailyKey, venueId, period, load, daily]);
  useEffect(() => {
    if (prevKey && venueId && prevPhrase) load(prevKey, venueId, { ...DAILY, period: prevPhrase });
  }, [prevKey, venueId, prevPhrase, load, prev]);
  useEffect(() => {
    if (itemsKey && venueId) load(itemsKey, venueId, { ...ITEMS, period });
  }, [itemsKey, venueId, period, load, items]);
  useEffect(() => {
    if (staffKey && venueId) load(staffKey, venueId, { ...STAFF, period });
  }, [staffKey, venueId, period, load, staff]);

  // ── the comparison ──
  const compareLoading = !daily || daily.loading || (comparison === 'previous' && !daily.error && (!prev || prev.loading));
  const compareError = daily?.error || (comparison === 'previous' ? prev?.error : null) || null;
  const result = useMemo(() => {
    if (!daily?.doc) return null;
    if (comparison === 'previous') return prev?.doc ? buildCompare(daily.doc, 'previous', prev.doc) : null;
    return buildCompare(daily.doc, comparison);
  }, [daily, prev, comparison]);

  const otherLabel = comparisonLabel(comparison, period);
  const currentLabel = PERIODS.find((p) => p.value === period)?.label ?? 'This period';
  const otherEmptyText = comparison === 'budget'
    ? 'No budget is set in Loaded for these days.'
    : comparison === 'last_year'
      ? 'Loaded has no sales for these days last year (52 weeks back, same weekdays).'
      : 'Loaded has no sales for the period before.';
  const otherSub = comparison === 'last_year'
    ? 'Same weekdays, 52 weeks back'
    : comparison === 'budget'
      ? 'Budget in Loaded for the same days'
      : result?.otherRange ? rangeLabel(result.otherRange[0], result.otherRange[1]) : '';
  // Totals compare only the days both sides have (a week still in progress,
  // or a 31-day month against a 30-day one): say how many.
  const partial = !!result && result.days < result.rows.length;

  // ── header ──
  const win = daily?.doc?.window;
  const rows = daily?.doc?.rows ?? [];
  const inProgress = !!win?.incomplete;
  const meta = win
    ? [
      rangeLabel(rows[0]?.date ?? win.start.slice(0, 10), rows[rows.length - 1]?.date),
      clockLabel(win.day_start) ? `trading days start ${clockLabel(win.day_start)}` : null,
      'sales incl. GST',
    ].filter(Boolean).join(' · ')
    : '';

  const refresh = () => {
    setNotice(null);
    forget();
    void connectedVenues().then(setConnected);
  };

  const venuePicker = venues.length > 1 ? (
    <VenueSelect venues={venues} value={venueId} onChange={handleVenueChange} />
  ) : null;

  const actions = (
    <>
      <select className="n-select" aria-label="Period" value={period} onChange={(e) => { setPeriod(e.target.value as Period); setNotice(null); }}>
        {PERIODS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
      </select>
      <select className="n-select" aria-label="Compare with" value={comparison} onChange={(e) => setComparison(e.target.value as Comparison)}>
        {COMPARISON_OPTIONS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
      {venuePicker}
      <Button size="sm" variant="quiet" icon={RefreshCw} onClick={refresh} aria-label="Refresh">Refresh</Button>
    </>
  );

  // ── tiles ──
  const total = daily?.doc ? periodTotal(daily.doc) : null;
  const hasOther = !!result && !result.otherEmpty;
  const tiles = venueConnected && !daily?.error ? (
    <div style={{ marginBottom: 16 }}>
      <TileRow min={stacked ? 140 : 170}>
        <StatTile
          label={inProgress ? 'Sales so far' : 'Sales'}
          value={total === null ? '…' : formatMoney(total, 0)}
          changePct={hasOther ? result?.pct : null}
          changeLabel={`vs ${otherLabel.toLowerCase()}`}
          sub={total === null ? 'Loading…' : undefined}
        />
        <StatTile
          label={otherLabel}
          value={compareLoading ? '…' : hasOther && result ? formatMoney(result.other, 0) : '—'}
          sub={compareLoading ? 'Loading…' : hasOther && result ? (partial ? `${otherSub} · ${result.days} of ${result.rows.length} days` : otherSub) : otherEmptyText}
        />
        <StatTile
          label="Difference"
          value={compareLoading ? '…' : hasOther && result ? signedMoney(result.diff) : '—'}
          sub={hasOther && result
            ? `${result.diff >= 0 ? 'ahead of' : 'behind'} ${otherLabel.toLowerCase()}${partial ? `, over ${result.days} days` : ''}`
            : undefined}
        />
        <StatTile
          label="Best day"
          value={result?.best ? formatMoney(result.best.actual, 0) : compareLoading ? '…' : '—'}
          sub={result?.best ? dayLabel(result.best.date) : undefined}
        />
      </TileRow>
    </div>
  ) : null;

  // ── body ──
  const settling = connected === null || venues.length === 0;
  const venueName = venues.find((v) => v.id === venueId)?.name || 'This venue';
  const lostConnection = isConnectionError(daily?.error);

  let body: React.ReactNode;
  if (settling && connected === null) {
    body = <div className="n-card"><PageState kind="loading" title="Loading…" /></div>;
  } else if (!venueId) {
    body = <div className="n-card"><PageState kind="empty" title="No venues yet." detail="Add a venue in Settings, then connect it to Loaded." /></div>;
  } else if (!venueConnected || lostConnection) {
    body = (
      <ConnectPrompt
        connector="loadedhub"
        venueId={venueId}
        reconnect={lostConnection}
        title={`${venueName} is not connected to Loaded`}
        detail="Sales come from Loaded. Connect this venue to see its products, staff and comparisons."
      />
    );
  } else if (tab === 'products') {
    body = (
      <ProductsTab
        doc={items?.doc ?? null}
        loading={!items || items.loading}
        error={items?.error ?? null}
        stacked={stacked}
        venueId={venueId}
        onChanged={(message) => { setNotice(message); if (itemsKey) forget(itemsKey); }}
      />
    );
  } else if (tab === 'staff') {
    body = <StaffTab doc={staff?.doc ?? null} loading={!staff || staff.loading} error={staff?.error ?? null} stacked={stacked} />;
  } else {
    body = (
      <CompareTab
        result={result}
        loading={compareLoading}
        error={compareError}
        currentLabel={currentLabel}
        otherLabel={otherLabel}
        otherEmptyText={otherEmptyText}
        stacked={stacked}
      />
    );
  }

  return (
    <div>
      <PageHeader
        title="Sales Analysis"
        meta={meta || (venueConnected && daily?.loading ? 'Loading…' : undefined)}
        status={inProgress ? <Badge tone="info">In progress</Badge> : undefined}
        actions={actions}
      >
        {venueConnected && <Tabs items={TABS} value={tab} onChange={(id) => setTab(id as Tab)} label="Sales Analysis views" />}
      </PageHeader>
      {notice && (
        <div role="status" style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 'var(--radius)', background: 'var(--ok-bg)', color: 'var(--ok)', fontSize: 'var(--fs-sm)' }}>
          {notice}
        </div>
      )}
      {daily?.error && !lostConnection && venueConnected && (
        <div style={{ marginBottom: 12 }}>
          <PageState kind="error" title="Could not load the period's sales from Loaded." detail={daily.error} />
        </div>
      )}
      {tiles}
      {body}
    </div>
  );
}
