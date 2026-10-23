'use client';

/**
 * Time & Attendance → Attendance — the `attendance_board` display component.
 *
 * For a day or a week, each person's rostered against clocked hours, with
 * late starts, early finishes, no-shows, unrostered clock-ins and hours more
 * than 20% over roster flagged; totals for hours and labour cost, and labour
 * as a share of sales. Grouped by person (default) or by day.
 *
 * Two reads, run side by side through the connector execute route (the page
 * loader runs raw GET rows only, not consolidators):
 *   - loadedhub.get_labour {view: 'attendance', group_by: 'detail', period}
 *     — every rostered shift, clock-in and leave line; attendance/model.ts
 *     pairs them per person per trading day and rolls them up (one call
 *     serves both groupings, so switching costs nothing);
 *   - loadedhub.get_daily_performance {period} — sales ex GST per trading day,
 *     the base of "labour as a share of sales" (wage % on Norm's other
 *     screens: labour cost ÷ sales ex GST).
 *
 * The period goes to the tools in plain English; the venue's trading day
 * comes back in the tool's window and is never worked out here.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import Button from '../ui/Button';
import { StatTile, TileRow } from '../functions/enquiries/bits';
import ConnectPrompt from './ConnectPrompt';
import {
  buildAttendance, flagTotal, parseWindowStart, shareLabel, wallMinutes, windowLabel,
  GRACE_MINS, type AttendanceDoc, type Flags,
} from './attendance/model';
import { DayCards, DayTable, PersonCards, PersonTable, diffLabel, hours, money, type DaySales } from './attendance/parts';

interface VenueOption { id: string; name: string; timezone?: string | null }

type Period = 'today' | 'yesterday' | 'this week' | 'last week';
type Group = 'person' | 'day';

const PERIODS: { value: Period; label: string }[] = [
  { value: 'today', label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: 'this week', label: 'This week' },
  { value: 'last week', label: 'Last week' },
];

interface PerfRow { bucket?: string; bucket_start?: string; sales_ex_gst?: number | null }
interface PerfDoc { rows?: PerfRow[]; summary?: { sales_ex_gst?: number | null } }

interface Loaded {
  labour: AttendanceDoc | null;
  labourError: string | null;
  perf: PerfDoc | null;
  perfError: string | null;
  /** When the labour arrived — "now" for deciding which shifts are still to come. */
  at: number;
}

const CACHE_MS = 5 * 60_000;
const PERIOD_KEY = 'norm_attendance_period';

function readPeriod(): Period {
  try {
    const v = localStorage.getItem(PERIOD_KEY) as Period | null;
    if (v && PERIODS.some((p) => p.value === v)) return v;
  } catch { /* storage unavailable */ }
  return 'yesterday';
}

function writePeriod(p: Period) {
  try { localStorage.setItem(PERIOD_KEY, p); } catch { /* storage unavailable */ }
}

/** A get_labour detail document, wherever the caller put it. */
function asLabourDoc(data: unknown): AttendanceDoc | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  const looks = (x: Record<string, unknown>) => !!x.window && (Array.isArray(x.rostered) || Array.isArray(x.clockins));
  if (looks(d)) return d as unknown as AttendanceDoc;
  const inner = d.data;
  if (inner && typeof inner === 'object' && looks(inner as Record<string, unknown>)) return inner as AttendanceDoc;
  return null;
}

/** Run one Loaded tool; its document, or the reason it failed. */
async function runTool(action: string, params: Record<string, unknown>, signal: AbortSignal): Promise<{ doc: Record<string, unknown> | null; error: string | null }> {
  try {
    const res = await apiFetch(`/api/connectors/loadedhub/execute/${action}`, {
      method: 'POST',
      body: JSON.stringify({ params }),
      signal,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return { doc: null, error: body?.detail || body?.error || `Norm couldn't get this from Loaded (error ${res.status}). Try again in a moment.` };
    const doc = (body?.data ?? body) as Record<string, unknown> | null;
    const err = (doc && typeof doc.error === 'string' ? doc.error : null) || (typeof body?.error === 'string' ? body.error : null);
    if (err) return { doc: null, error: err };
    if (!doc || typeof doc !== 'object') return { doc: null, error: 'Loaded sent nothing back' };
    return { doc, error: null };
  } catch (e) {
    return { doc: null, error: e instanceof Error ? e.message : 'Could not reach Loaded' };
  }
}

function flagWords(f: Flags): string {
  const parts = [
    f.late ? `${f.late} late` : null,
    f.early ? `${f.early} left early` : null,
    f.noShow ? `${f.noShow} no-show${f.noShow === 1 ? '' : 's'}` : null,
    f.unrostered ? `${f.unrostered} not rostered` : null,
    f.over ? `${f.over} over roster` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : 'Nothing flagged';
}

const chip = (active: boolean): React.CSSProperties => ({
  padding: '5px 12px', fontSize: 'var(--fs-sm)', fontWeight: active ? 600 : 500, fontFamily: 'inherit',
  lineHeight: 1.4, whiteSpace: 'nowrap', borderRadius: 999, cursor: 'pointer',
  border: `1px solid ${active ? 'var(--brand-soft)' : 'var(--line-strong)'}`,
  background: active ? 'var(--accent-soft)' : 'var(--bg)', color: active ? 'var(--accent)' : 'var(--text-soft)',
});

export default function AttendanceBoard({ data, props }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const { isMobile, isTablet } = useBreakpoint();
  const stacked = isMobile || isTablet;

  const initialDoc = useMemo(() => asLabourDoc(data), [data]);
  const blockVenue = (props?.activeVenueId as string) || ((data as Record<string, unknown>)?.venue_id as string) || null;
  const propVenue = (props?.activeVenueId as string) || null;

  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [venuesLoaded, setVenuesLoaded] = useState(false);
  // Which venues hold a Loaded connection. The executor falls back to another
  // venue's login inside the organisation when a venue has none — that would
  // be another venue's staff — so the page never asks for an unconnected one.
  const [connected, setConnected] = useState<Set<string> | null>(null);
  const [selectedVenue, setSelectedVenue] = useState<string | null>(blockVenue || rememberedVenue || null);

  const [period, setPeriod] = useState<Period>(() => (persistVenue ? readPeriod() : 'yesterday'));
  const [group, setGroup] = useState<Group>('person');
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());

  const [loaded, setLoaded] = useState<Loaded | null>(
    initialDoc ? { labour: initialDoc, labourError: null, perf: null, perfError: null, at: Date.now() } : null,
  );
  const [loadingLabour, setLoadingLabour] = useState<boolean>(!initialDoc);
  const [loadingPerf, setLoadingPerf] = useState<boolean>(false);
  const [reloadKey, setReloadKey] = useState(0);
  const cache = useRef(new Map<string, Loaded>());
  const loadSeq = useRef(0);
  // A superseded load is cancelled, not just ignored: Loaded reads can take
  // half a minute, and the browser only runs a few requests per host at once.
  const inFlight = useRef<AbortController | null>(null);
  const forceNext = useRef(false);
  const userPicked = useRef(false);

  const loadConnectInfo = useCallback(async (): Promise<Set<string>> => {
    const info = await apiFetch('/api/connectors/loadedhub/connect-info')
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
    const ok = new Set<string>(
      ((info?.venues ?? []) as { venue_id: string; status: string }[])
        .filter((v) => v.status === 'connected')
        .map((v) => v.venue_id),
    );
    setConnected(ok);
    return ok;
  }, []);

  useEffect(() => {
    let cancelled = false;
    // The picker shows as soon as the venues arrive; connect-info can be slow.
    const venuesCall = apiFetch('/api/venues')
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((d) => {
        const list: VenueOption[] = d ? (Array.isArray(d) ? d : (d.venues ?? [])) : [];
        if (!cancelled) {
          setVenues(list);
          setVenuesLoaded(true);
        }
        return list;
      });
    Promise.all([venuesCall, loadConnectInfo()]).then(([list, ok]) => {
      if (cancelled) return;
      if (!selectedVenue && !propVenue && !rememberedVenue && list.length) {
        setSelectedVenue(list.find((v) => ok.has(v.id))?.id ?? list[0].id);
      }
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The page follows the app's venue until the person picks one here.
  useEffect(() => {
    if (!persistVenue || !propVenue || userPicked.current) return;
    if (propVenue !== selectedVenue) setSelectedVenue(propVenue);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propVenue, persistVenue]);

  // Only the latest request may land: a venue or period switched mid-load must
  // never leave the previous one's figures under the new heading.
  const cancelInFlight = useCallback(() => {
    loadSeq.current += 1;
    inFlight.current?.abort();
    inFlight.current = null;
  }, []);
  useEffect(() => cancelInFlight, [cancelInFlight]);

  const load = useCallback(async (venueId: string, p: Period, force: boolean) => {
    const key = `${venueId}|${p}`;
    const hit = cache.current.get(key);
    if (!force && hit && Date.now() - hit.at < CACHE_MS) {
      cancelInFlight();
      setLoaded(hit);
      setLoadingLabour(false);
      setLoadingPerf(false);
      setOpen(new Set());
      return;
    }
    cancelInFlight();
    const seq = loadSeq.current;
    const stale = () => seq !== loadSeq.current;
    const controller = new AbortController();
    inFlight.current = controller;
    setLoaded(null);
    setLoadingLabour(true);
    setLoadingPerf(true);
    setOpen(new Set());

    let current: Loaded = { labour: null, labourError: null, perf: null, perfError: null, at: Date.now() };
    const labourCall = runTool('get_labour', { venue_id: venueId, period: p, view: 'attendance', group_by: 'detail' }, controller.signal)
      .then(({ doc, error }) => {
        if (stale()) return;
        const labour = asLabourDoc(doc);
        current = { ...current, labour, labourError: labour ? null : (error || 'Loaded sent no attendance'), at: Date.now() };
        setLoaded(current);
        setLoadingLabour(false);
      });
    const perfCall = runTool('get_daily_performance', { venue_id: venueId, period: p }, controller.signal)
      .then(({ doc, error }) => {
        if (stale()) return;
        current = { ...current, perf: (doc as PerfDoc) || null, perfError: doc ? null : error };
        setLoaded(current);
        setLoadingPerf(false);
      });
    await Promise.all([labourCall, perfCall]);
    if (stale()) return;
    inFlight.current = null;
    if (current.labour) cache.current.set(key, current);
  }, [cancelInFlight]);

  const venueConnected = !connected || !selectedVenue || connected.has(selectedVenue);
  // Whether the chosen venue is connected (null = not known yet). The effect
  // below follows this, not the Set: connect-info answering a second time
  // must not start the same load again.
  const chosenConnected = connected === null || !selectedVenue ? null : connected.has(selectedVenue);
  useEffect(() => {
    if (!selectedVenue) return;
    if (!persistVenue && initialDoc && reloadKey === 0) return; // the block came with its document
    // Wait for connect-info before asking (see `connected`).
    if (chosenConnected === null) return;
    if (!chosenConnected) {
      cancelInFlight();
      setLoaded(null);
      setLoadingLabour(false);
      setLoadingPerf(false);
      return;
    }
    const force = forceNext.current;
    forceNext.current = false;
    void load(selectedVenue, period, force);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVenue, period, reloadKey, chosenConnected]);

  // After connecting Loaded from the prompt below, pick up the new connection.
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type !== 'oauth-complete') return;
      if (!/loaded/i.test(String(event.data?.connector || ''))) return;
      void loadConnectInfo().then(() => { forceNext.current = true; setReloadKey((k) => k + 1); });
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [loadConnectInfo]);

  const handleVenueChange = (id: string) => {
    userPicked.current = true;
    setSelectedVenue(id);
    if (persistVenue) setActiveVenue(id);
  };
  const handlePeriod = (p: Period) => {
    setPeriod(p);
    if (persistVenue) writePeriod(p);
  };
  const refresh = () => {
    forceNext.current = true;
    void loadConnectInfo().then(() => setReloadKey((k) => k + 1));
  };
  const toggle = (key: string) => setOpen((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  // ── the figures ──────────────────────────────────────────────────────────
  const venue = venues.find((v) => v.id === selectedVenue) || null;
  const labour = loaded?.labour ?? null;
  const fetchedAt = loaded?.at ?? null;
  const attendance = useMemo(() => {
    if (!labour) return null;
    const win = parseWindowStart(labour.window?.start);
    const nowWall = wallMinutes(new Date(fetchedAt ?? 0), venue?.timezone, win?.offsetMins ?? null);
    return buildAttendance(labour, { nowWall });
  }, [labour, fetchedAt, venue?.timezone]);

  const perf = loaded?.perf ?? null;
  const salesByDay = useMemo(() => {
    const m = new Map<string, DaySales>();
    for (const r of perf?.rows || []) {
      if (r.bucket_start) m.set(r.bucket_start.slice(0, 10), { sales_ex_gst: typeof r.sales_ex_gst === 'number' ? r.sales_ex_gst : null });
    }
    return m;
  }, [perf]);
  const salesExGst = typeof perf?.summary?.sales_ex_gst === 'number' ? perf.summary.sales_ex_gst : null;

  const singleDay = (attendance?.days.length ?? 0) <= 1 && (period === 'today' || period === 'yesterday');
  const people = useMemo(() => {
    const rows = attendance?.people ?? [];
    return onlyFlagged ? rows.filter((p) => flagTotal(p.flags) > 0) : rows;
  }, [attendance, onlyFlagged]);
  const days = useMemo(() => {
    const rows = attendance?.days ?? [];
    return onlyFlagged ? rows.filter((d) => flagTotal(d.flags) > 0) : rows;
  }, [attendance, onlyFlagged]);
  // A single day opens itself in the by-day view: there's nothing else to pick.
  const lone = group === 'day' && days.length === 1 ? days[0].dayKey : null;
  const openDays = useMemo(() => {
    if (lone && !open.has(`closed:${lone}`)) return new Set([...open, lone]);
    return open;
  }, [lone, open]);
  const toggleDay = (key: string) => toggle(key === lone ? `closed:${key}` : key);

  // ── tiles ────────────────────────────────────────────────────────────────
  const t = attendance?.totals;
  const inProgress = !!attendance?.inProgress;
  const perfError = loaded?.perfError ?? null;
  const tiles = attendance && t ? (
    <div style={{ marginBottom: 16 }}>
      <TileRow min={stacked ? 150 : 190}>
        <StatTile
          label="Hours worked"
          value={`${hours(t.workedHours)} h`}
          sub={inProgress
            ? `of ${hours(t.rosteredSoFarHours)} h rostered so far (${diffLabel(t.workedHours, t.rosteredSoFarHours)}) · ${hours(t.rosteredHours)} h in all`
            : `of ${hours(t.rosteredHours)} h rostered (${diffLabel(t.workedHours, t.rosteredHours)})${t.leaveHours ? ` · ${hours(t.leaveHours)} h leave` : ''}`}
        />
        <StatTile
          label="Labour cost"
          value={money(t.workedCost)}
          sub={inProgress
            ? `${money(t.rosteredSoFarCost)} rostered so far · ${money(t.rosteredCost)} in all`
            : `${money(t.rosteredCost)} rostered${t.leaveCost ? ` · ${money(t.leaveCost)} leave not included` : ''}`}
        />
        <StatTile
          label="Labour % of sales"
          value={loadingPerf ? '…' : (shareLabel(t.workedCost, salesExGst) ?? '—')}
          sub={loadingPerf
            ? 'Loading sales'
            : perfError || !perf
              ? 'Sales not available right now'
              : salesExGst
                ? `of ${money(salesExGst)} sales ex GST`
                : 'No sales yet'}
        />
        <StatTile
          label="Flags"
          value={String(attendance.flagCount)}
          sub={flagWords(t.flags)}
        />
      </TileRow>
    </div>
  ) : null;

  // ── header ───────────────────────────────────────────────────────────────
  const venuePicker = venues.length > 1 ? (
    <VenueSelect venues={venues} value={selectedVenue} onChange={handleVenueChange} />
  ) : null;
  const actions = (
    <>
      {venuePicker}
      <Button size="sm" variant="quiet" icon={RefreshCw} onClick={refresh} aria-label="Refresh" disabled={loadingLabour}>
        {stacked ? null : 'Refresh'}
      </Button>
    </>
  );
  const periodChips = (
    <div role="group" aria-label="Period" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {PERIODS.map((p) => (
        <button key={p.value} type="button" aria-pressed={period === p.value} onClick={() => handlePeriod(p.value)} style={chip(period === p.value)}>
          {p.label}
        </button>
      ))}
    </div>
  );
  const meta = labour?.window?.description
    ? `${windowLabel(labour.window.description)}${inProgress ? ' · so far' : ''}`
    : loadingLabour ? ' ' : '';

  // ── body ─────────────────────────────────────────────────────────────────
  const inCard = (node: React.ReactNode) => (persistVenue ? node : <div className="n-card">{node}</div>);
  const labourError = loaded?.labourError ?? null;
  const notConnected = !venueConnected || (!!labourError && /No credentials|not connected|needs_reconnect|reconnect/i.test(labourError));
  const settling = connected === null || (persistVenue && !venuesLoaded);
  const periodWord = PERIODS.find((p) => p.value === period)?.label.toLowerCase() || period;
  const nobody = !!attendance && attendance.people.length === 0;
  const sales = loadingPerf ? null : salesByDay;

  const groupTabs = (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
      <div className="n-tabs" role="tablist" aria-label="Group by" style={{ flex: '1 1 auto' }}>
        <button type="button" role="tab" className="n-tab" aria-selected={group === 'person'} onClick={() => setGroup('person')}>By person</button>
        <button type="button" role="tab" className="n-tab" aria-selected={group === 'day'} onClick={() => setGroup('day')}>By day</button>
      </div>
      {attendance && attendance.flagCount > 0 && (
        <button type="button" aria-pressed={onlyFlagged} onClick={() => setOnlyFlagged((v) => !v)} style={chip(onlyFlagged)}>
          Only flagged ({attendance.flagCount})
        </button>
      )}
    </div>
  );

  const scroll = (node: React.ReactNode) => <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>{node}</div>;
  const list = !attendance ? null : group === 'person' ? (
    people.length === 0 ? <PageState kind="empty" title="Nobody flagged." /> : stacked
      ? <PersonCards rows={people} singleDay={singleDay} open={open} onToggle={toggle} onlyFlagged={onlyFlagged} />
      : scroll(<PersonTable rows={people} singleDay={singleDay} open={open} onToggle={toggle} onlyFlagged={onlyFlagged} />)
  ) : (
    days.length === 0 ? <PageState kind="empty" title="No day has anything flagged." /> : stacked
      ? <DayCards rows={days} sales={sales} open={openDays} onToggle={toggleDay} onlyFlagged={onlyFlagged} />
      : scroll(<DayTable rows={days} sales={sales} open={openDays} onToggle={toggleDay} onlyFlagged={onlyFlagged} />)
  );

  const footnote = attendance && !nobody ? (
    <p style={{ margin: '14px 0 0', fontSize: 'var(--fs-xs)', color: 'var(--muted)', lineHeight: 1.5 }}>
      Late or early means more than {GRACE_MINS} minutes off the roster. Over roster means more than 20% more hours worked than rostered.
      Booked leave is shown but never counted as hours worked. Labour % is labour cost over sales excluding GST.
      {inProgress ? ' Shifts that haven’t started yet aren’t flagged.' : ''}
    </p>
  ) : null;

  const body = settling || (loadingLabour && !attendance) ? (
    inCard(<PageState kind="loading" title="Loading attendance from Loaded…" detail="Rosters and clock-ins can take a few seconds." />)
  ) : notConnected ? (
    inCard(
      <ConnectPrompt
        connector="loadedhub"
        venueId={selectedVenue}
        compact={!persistVenue}
        title={`${venue?.name || 'This venue'} is not connected to Loaded`}
        detail="Attendance comes from the venue's roster and clock-ins in Loaded."
      />,
    )
  ) : labourError ? (
    inCard(<PageState kind="error" title="Could not load attendance." detail={labourError} action={<Button size="sm" onClick={refresh}>Try again</Button>} />)
  ) : !attendance ? (
    inCard(<PageState kind="empty" title={venues.length ? 'Pick a venue to see attendance.' : 'No venues yet.'} />)
  ) : nobody ? (
    inCard(<PageState kind="empty" title={`Nobody was rostered or clocked in ${periodWord}.`} />)
  ) : (
    <>
      {tiles}
      {groupTabs}
      {list}
      {footnote}
    </>
  );

  if (persistVenue) {
    return (
      <div>
        <PageHeader title="Attendance" meta={meta} actions={actions}>
          {periodChips}
        </PageHeader>
        {body}
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>{(props?.title as string) || 'Attendance'}</h2>
          {meta && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</span>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>{actions}</div>
      </div>
      <div style={{ marginBottom: 12 }}>{periodChips}</div>
      {body}
    </div>
  );
}
