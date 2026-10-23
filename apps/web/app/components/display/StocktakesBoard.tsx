'use client';

/**
 * Stocktakes — the `stocktakes_board` page (Procurement → Stocktakes, the
 * Stocktakes app; docs/plans/agents-and-apps-restructure.md §5).
 *
 *  - Stocktakes tab: the venue's counts from `loadedhub.get_stocktakes`
 *    (view 'list') — when, what was counted (a full category, an area list or
 *    an ad-hoc count), its value once known, and whether it was finished.
 *    Opening a count shows its value, value by category and biggest variances
 *    (stocktakes/StocktakeDetail.tsx).
 *  - Cost of goods tab: for a period, from the counts where the venue does
 *    full stocktakes, and from purchases against sales
 *    (stocktakes/CostOfGoods.tsx).
 *
 * The page loads itself through `POST /api/connectors/loadedhub/execute/…`,
 * like the Bills page. Loaded keeps its stocktake list behind its older login,
 * so the list needs the venue's Loaded Legacy connection; without it the
 * Stocktakes tab offers to connect and Cost of goods still works from
 * purchases.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, RefreshCw } from 'lucide-react';
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
import Icon from '../ui/Icon';
import Tabs from '../ui/Tabs';
import ConnectPrompt from './ConnectPrompt';
import StocktakeDetail from './stocktakes/StocktakeDetail';
import CostOfGoods from './stocktakes/CostOfGoods';
import { Note } from './stocktakes/parts';
import { readValues, rememberValues, runLoadedTool } from './stocktakes/api';
import {
  LIST_FILTERS, canOpen, countLabel, defaultFilter, filterRows, formatDay, isHubMissing,
  isLegacyMissing, previousCount, sortRows, stocktakedCategories,
  type ListFilter, type StocktakeListDoc, type StocktakeRow,
} from './stocktakes/logic';

interface VenueOption { id: string; name: string; timezone?: string | null }

type Tab = 'counts' | 'cogs';
type ListState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; rows: StocktakeRow[] }
  | { kind: 'legacy' }
  | { kind: 'error'; error: string };

const PAGE = 25;

async function connectedVenues(connector: string): Promise<Set<string> | null> {
  const info = await apiFetch(`/api/connectors/${connector}/connect-info`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  if (!info) return null;
  return new Set<string>(
    ((info.venues ?? []) as { venue_id: string; status: string }[])
      .filter((v) => v.status === 'connected')
      .map((v) => v.venue_id),
  );
}

export default function StocktakesBoard({ data, props }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const { isMobile, isTablet } = useBreakpoint();
  const stacked = isMobile || isTablet;

  const blockVenue = (props?.activeVenueId as string) || ((data as Record<string, unknown>)?.venue_id as string) || null;
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [selectedVenue, setSelectedVenue] = useState<string | null>(blockVenue || rememberedVenue || null);
  // Which venues hold each connection; null when connect-info couldn't be
  // read, in which case the page simply tries and reads the tool's answer.
  const [hub, setHub] = useState<Set<string> | null>(null);
  const [legacy, setLegacy] = useState<Set<string> | null>(null);
  const [connectInfoDone, setConnectInfoDone] = useState(false);

  const [tab, setTab] = useState<Tab>('counts');
  const [list, setList] = useState<ListState>({ kind: 'idle' });
  const [filter, setFilter] = useState<ListFilter | null>(null);
  const [shown, setShown] = useState(PAGE);
  const [openId, setOpenId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, number>>({});
  const [period, setPeriod] = useState('last month');
  const [listReload, setListReload] = useState(0);
  const [cogsRefresh, setCogsRefresh] = useState(0);

  const loadConnectInfo = useCallback(async () => {
    const [h, l] = await Promise.all([connectedVenues('loadedhub'), connectedVenues('loaded_legacy')]);
    setHub(h);
    setLegacy(l);
    setConnectInfoDone(true);
    return h;
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch('/api/venues').then((r) => (r.ok ? r.json() : null)).catch(() => null),
      loadConnectInfo(),
    ]).then(([d, h]) => {
      if (cancelled) return;
      const all: VenueOption[] = d ? (Array.isArray(d) ? d : (d.venues ?? [])) : [];
      setVenues(all);
      // Nothing chosen anywhere yet: start on the first Loaded venue.
      if (!selectedVenue && !propVenue && !rememberedVenue && all.length) {
        setSelectedVenue(all.find((v) => h?.has(v.id))?.id ?? all[0].id);
      }
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The page follows the app's venue until the person picks one here.
  const userPicked = useRef(false);
  const propVenue = (props?.activeVenueId as string) || null;
  useEffect(() => {
    if (!persistVenue || !propVenue || userPicked.current) return;
    if (propVenue !== selectedVenue) setSelectedVenue(propVenue);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propVenue, persistVenue]);

  // A new venue: back to its list, its remembered values, its default filter.
  useEffect(() => {
    setOpenId(null);
    setFilter(null);
    setShown(PAGE);
    setValues(selectedVenue ? readValues(selectedVenue) : {});
  }, [selectedVenue]);

  const hubConnected = !hub || !selectedVenue || hub.has(selectedVenue);
  const legacyConnected = !legacy || !selectedVenue || legacy.has(selectedVenue);

  // The list — only the latest request may land.
  const listSeq = useRef(0);
  useEffect(() => {
    if (!selectedVenue || !connectInfoDone) return;
    const seq = ++listSeq.current;
    if (!hubConnected) { setList({ kind: 'idle' }); return; }
    if (!legacyConnected) { setList({ kind: 'legacy' }); return; }
    setList({ kind: 'loading' });
    void runLoadedTool<StocktakeListDoc>(
      'get_stocktakes',
      { venue_id: selectedVenue, view: 'list', status: 'all', limit: 200 },
      listReload > 0,
    ).then(({ doc, error }) => {
      if (seq !== listSeq.current) return;
      if (error || !doc) {
        setList(isLegacyMissing(error) ? { kind: 'legacy' } : { kind: 'error', error: error || 'Loaded returned no stocktakes' });
        return;
      }
      setList({ kind: 'ready', rows: sortRows(Array.isArray(doc.stocktakes) ? doc.stocktakes : []) });
    });
  }, [selectedVenue, connectInfoDone, hubConnected, legacyConnected, listReload]);

  // The connect card's OAuth popup reports back here; re-read and reload.
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type !== 'oauth-complete') return;
      void loadConnectInfo().then(() => setListReload((k) => k + 1));
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [loadConnectInfo]);

  const handleVenueChange = (id: string) => {
    userPicked.current = true;
    setSelectedVenue(id);
    if (persistVenue) setActiveVenue(id);
  };

  const learnValues = useCallback((learned: Record<string, number>) => {
    if (!selectedVenue) return;
    setValues((v) => ({ ...v, ...learned }));
    rememberValues(selectedVenue, learned);
  }, [selectedVenue]);

  // Refresh re-reads the connections too: a Loaded Legacy login entered in the
  // connect card shows up here without leaving the page.
  const refresh = () => {
    void loadConnectInfo().then(() => {
      if (tab === 'counts') setListReload((k) => k + 1);
      else setCogsRefresh((k) => k + 1);
    });
  };

  const venue = venues.find((v) => v.id === selectedVenue) || null;
  const venueName = venue?.name || 'This venue';
  const timeZone = venue?.timezone || null;

  const rows = useMemo(() => (list.kind === 'ready' ? list.rows : []), [list]);
  const activeFilter: ListFilter = filter ?? defaultFilter(rows);
  const visible = useMemo(() => filterRows(rows, activeFilter), [rows, activeFilter]);
  const categories = useMemo(
    () => (list.kind === 'ready' ? stocktakedCategories(rows).map(({ template, category }) => ({ template, category })) : null),
    [list.kind, rows],
  );
  const openRow = openId ? rows.find((r) => r.id === openId) || null : null;

  // ── Pieces ───────────────────────────────────────────────────────────────
  const venuePicker = venues.length > 1 ? <VenueSelect venues={venues} value={selectedVenue} onChange={handleVenueChange} /> : null;
  const actions = (
    <>
      <Button size="sm" variant="quiet" icon={RefreshCw} onClick={refresh} aria-label="Refresh">Refresh</Button>
      {venuePicker}
    </>
  );

  const fullDone = filterRows(rows, 'full');
  const pendingCount = filterRows(rows, 'pending').length;
  const meta = list.kind === 'ready' && rows.length
    ? [
        fullDone.length ? `${fullDone.length} full count${fullDone.length === 1 ? '' : 's'}, the last completed ${formatDay(fullDone[0].completed, timeZone)}` : 'No full counts',
        pendingCount ? `${pendingCount} not finished` : null,
      ].filter(Boolean).join(' · ')
    : ' ';

  const statusBadge = (r: StocktakeRow) =>
    r.pending || !r.completed ? <Badge tone="warn">Not finished</Badge> : <Badge tone="ok">Completed</Badge>;

  const valueCell = (r: StocktakeRow) => {
    const v = values[r.id];
    if (typeof v === 'number') return <span style={{ fontWeight: 600 }}>{formatMoney(v, 0)}</span>;
    if (canOpen(r)) return <span style={{ color: 'var(--muted)' }} title="Open the count to work out its value">—</span>;
    return null;
  };

  const open = (r: StocktakeRow) => { if (canOpen(r)) setOpenId(r.id); };
  const rowKeys = (r: StocktakeRow) => (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(r); }
  };

  const page = visible.slice(0, shown);
  const table = (
    <div className="n-card" style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <table className="n-table">
        <thead>
          <tr>
            <th>Completed</th>
            <th>What was counted</th>
            <th className="num">Value</th>
            <th>Status</th>
            <th aria-label="Open" />
          </tr>
        </thead>
        <tbody>
          {page.map((r) => {
            const can = canOpen(r);
            return (
              <tr
                key={r.id}
                onClick={can ? () => open(r) : undefined}
                onKeyDown={can ? rowKeys(r) : undefined}
                tabIndex={can ? 0 : undefined}
                role={can ? 'button' : undefined}
                aria-label={can ? `Open ${countLabel(r)}, ${formatDay(r.completed, timeZone)}` : undefined}
                style={{ cursor: can ? 'pointer' : undefined }}
              >
                <td style={{ whiteSpace: 'nowrap' }}>{r.completed ? formatDay(r.completed, timeZone) : '—'}</td>
                <td>
                  <div style={{ fontWeight: 500 }}>{countLabel(r)}</div>
                  {r.title && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{r.title}</div>}
                </td>
                <td className="num">{valueCell(r)}</td>
                <td>{statusBadge(r)}</td>
                <td style={{ width: 28, textAlign: 'right' }}>{can && <Icon icon={ChevronRight} size="dense" tone="muted" />}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  const cards = (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {page.map((r) => {
        const can = canOpen(r);
        const body = (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
              <span style={{ fontWeight: 600, minWidth: 0 }}>{countLabel(r)}</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                {valueCell(r)}
                {can && <Icon icon={ChevronRight} size="dense" tone="muted" />}
              </span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 8px', marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)', alignItems: 'center' }}>
              {statusBadge(r)}
              {r.completed && <span>{formatDay(r.completed, timeZone)}</span>}
              {r.title && <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{r.title}</span>}
            </div>
          </>
        );
        return (
          <li key={r.id}>
            {can ? (
              <button type="button" className="n-card" onClick={() => open(r)} style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 12px', font: 'inherit', color: 'inherit', cursor: 'pointer' }}>
                {body}
              </button>
            ) : (
              <div className="n-card" style={{ padding: '10px 12px' }}>{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );

  const missingValues = page.some((r) => canOpen(r) && typeof values[r.id] !== 'number');
  const filterBar = (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
      <select
        className="n-select"
        value={activeFilter}
        onChange={(e) => { setFilter(e.target.value as ListFilter); setShown(PAGE); }}
        aria-label="Which counts"
      >
        {LIST_FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label} ({filterRows(rows, f.value).length})</option>)}
      </select>
      {activeFilter === 'adhoc' && (
        <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Ad-hoc counts cover different items each time, so they can&apos;t be compared.</span>
      )}
    </div>
  );

  const toCogs = <Button size="sm" variant="secondary" onClick={() => setTab('cogs')}>Cost of goods from purchases</Button>;

  let countsBody: React.ReactNode;
  if (list.kind === 'legacy') {
    countsBody = (
      <div className="n-card" style={{ paddingBottom: 16 }}>
        <ConnectPrompt
          connector="loaded_legacy"
          venueId={selectedVenue}
          title={`Connect Loaded Legacy to see ${venueName}'s stocktakes`}
          detail="Loaded keeps stocktakes behind its older sign-in. Connect it with a Loaded login that can see this venue's stock. Cost of goods from purchases works without it."
        />
        <div style={{ textAlign: 'center', marginTop: 12 }}>{toCogs}</div>
      </div>
    );
  } else if (list.kind === 'error') {
    countsBody = (
      <PageState
        kind="error"
        title="Couldn't read the stocktakes from Loaded."
        detail={list.error}
        action={<Button size="sm" variant="secondary" onClick={() => setListReload((k) => k + 1)}>Try again</Button>}
      />
    );
  } else if (list.kind !== 'ready') {
    countsBody = <div className="n-card"><PageState kind="loading" title="Reading stocktakes from Loaded…" /></div>;
  } else if (rows.length === 0) {
    countsBody = (
      <div className="n-card">
        <PageState
          kind="empty"
          title={`No stocktakes in Loaded for ${venueName}.`}
          detail="Without counts, cost of goods comes from purchases against sales."
          action={toCogs}
        />
      </div>
    );
  } else {
    countsBody = (
      <>
        {filterBar}
        {visible.length === 0 ? (
          <div className="n-card"><PageState kind="empty" title="No counts of this kind." /></div>
        ) : stacked ? cards : table}
        {visible.length > shown && (
          <div style={{ textAlign: 'center', marginTop: 10 }}>
            <Button size="sm" variant="secondary" onClick={() => setShown((n) => n + PAGE)}>
              Show more ({visible.length - shown} left)
            </Button>
          </div>
        )}
        {missingValues && (
          <Note>
            A count&apos;s value comes from Loaded&apos;s stocktake report, which takes up to a minute, so it shows here
            once the count has been opened.
          </Note>
        )}
      </>
    );
  }

  const unknownReason = list.kind === 'legacy'
    ? `Norm can't see ${venueName}'s stocktakes until Loaded Legacy is connected (Stocktakes tab), so cost of goods here is purchases against sales.`
    : list.kind === 'error'
      ? `Norm couldn't read ${venueName}'s stocktakes just now, so cost of goods here is purchases against sales.`
      : null;
  const listSettling = list.kind === 'idle' || list.kind === 'loading';

  // ── Page ─────────────────────────────────────────────────────────────────
  const settling = !connectInfoDone || (persistVenue && venues.length === 0) || !selectedVenue;
  if (settling) {
    return (
      <div>
        <PageHeader title="Stocktakes" meta=" " actions={actions} />
        <div className="n-card"><PageState kind="loading" title="Loading…" /></div>
      </div>
    );
  }

  if (!hubConnected || isHubMissing(list.kind === 'error' ? list.error : null)) {
    return (
      <div>
        <PageHeader title="Stocktakes" actions={actions} />
        <div className="n-card">
          <ConnectPrompt
            connector="loadedhub"
            venueId={selectedVenue}
            title={`${venueName} is not connected to Loaded`}
            detail="Stocktakes and cost of goods come from Loaded. Connect the venue below."
          />
        </div>
      </div>
    );
  }

  if (openRow && selectedVenue) {
    return (
      <StocktakeDetail
        key={openRow.id}
        venueId={selectedVenue}
        timeZone={timeZone}
        row={openRow}
        previous={previousCount(rows, openRow)}
        stacked={stacked}
        onBack={() => setOpenId(null)}
        onValues={learnValues}
      />
    );
  }

  return (
    <div>
      <PageHeader title="Stocktakes" meta={tab === 'counts' ? meta : ' '} actions={actions}>
        <Tabs
          label="Stocktakes"
          value={tab}
          onChange={(id) => setTab(id as Tab)}
          items={[{ id: 'counts', label: 'Stocktakes' }, { id: 'cogs', label: 'Cost of goods' }]}
        />
      </PageHeader>
      {tab === 'counts' ? countsBody : listSettling ? (
        <div className="n-card"><PageState kind="loading" title="Checking which counts Loaded has…" /></div>
      ) : (
        <CostOfGoods
          venueId={selectedVenue as string}
          venueName={venueName}
          timeZone={timeZone}
          categories={list.kind === 'ready' ? categories : null}
          unknownReason={unknownReason}
          period={period}
          onPeriod={setPeriod}
          stacked={stacked}
          refreshKey={cogsRefresh}
        />
      )}
    </div>
  );
}
