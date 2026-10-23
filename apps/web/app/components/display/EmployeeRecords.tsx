'use client';

/**
 * HR → Employees — the `employee_records` display component (the Employee
 * Records app, BambooHR). docs/plans/agents-and-apps-restructure.md §5.
 *
 * The group's staff directory from BambooHR, searchable and filterable by
 * venue, team, role and status; open a person for their work and contact
 * details. It reads one tool, `bamboohr.get_hr`:
 *
 *  - view 'employees' with no employee_id: the directory (current staff,
 *    every venue, in one call);
 *  - view 'employees' with employee_id: one person's record. The directory
 *    has no start date or employment type, so the page also reads the record
 *    of each person ON SCREEN (a few at a time, kept while the page is open).
 *
 * Connection: BambooHR is one account for the whole group. A venue is served
 * by its own BambooHR connection or else the organisation-wide one
 * (connect-info says which). The page reads through the app's venue when that
 * venue is connected, otherwise through any connected venue — the directory
 * is the same — and shows the connect card when none is.
 *
 * Privacy: only work and contact details are shown. Both readers in
 * employees/employeeLogic.ts copy an allow-list, so pay, tax, bank, date of
 * birth, home address and emergency contacts never reach the screen even if
 * the tool were to return them.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Mail, Phone, RefreshCw, Search, Store } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import Dialog from '../ui/Dialog';
import { Fields, Spinner, StatTile, TileRow } from '../functions/enquiries/bits';
import ConnectPrompt from './ConnectPrompt';
import {
  applyFilters, distinct, EMPTY_FILTERS, extractDirectory, extractPerson, formatDay, goesBy,
  isActive, managerName, matchLocation, NO_TEAM, readError, sentenceCase, sortRows, teamCounts,
  tenure, toolError,
  type Directory, type DirectoryRow, type Filters, type PersonDetail, type StatusFilter,
} from './employees/employeeLogic';

interface VenueOption { id: string; name: string }

/** connect-info, reduced to what the page needs. `readable: false` = the
 *  person may not read connection settings, so the page just tries. */
type ConnectState =
  | { readable: true; configured: boolean; venues: Map<string, { status: string; scope: string }> }
  | { readable: false };

/** A person's record: loaded, or why it couldn't be. */
type DetailEntry = PersonDetail | { failed: string };
const isFailed = (d: DetailEntry | undefined): d is { failed: string } => !!d && 'failed' in d;

/** A failure's reason, unless it only repeats the heading above it. */
const extraDetail = (why: string): string | undefined => (/^Couldn.t read /.test(why) ? undefined : why);

const PAGE = 30;
/** Records read at once while filling in start dates. */
const PARALLEL = 3;

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'active', label: 'Current staff' },
  { value: 'inactive', label: 'Former staff' },
  { value: 'all', label: 'Everyone' },
];

async function runGetHr(params: Record<string, unknown>): Promise<{ ok: boolean; status: number; body: unknown }> {
  const res = await apiFetch('/api/connectors/bamboohr/execute/get_hr', {
    method: 'POST',
    body: JSON.stringify({ params }),
  });
  const body = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, body };
}

export default function EmployeeRecords({ data, props }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const title = (props?.title as string) || 'Employees';
  const { isMobile, isTablet } = useBreakpoint();
  const stacked = isMobile || isTablet;

  const initial = useMemo(() => extractDirectory(data), [data]);
  const [sharedVenue] = useActiveVenue();
  const propVenue = (props?.activeVenueId as string) || null;
  // The app's venue: what FunctionalPage passes, else the remembered page venue.
  const appVenue = propVenue || (persistVenue ? sharedVenue : null);

  const [venues, setVenues] = useState<VenueOption[] | null>(null);
  const [connect, setConnect] = useState<ConnectState | null>(null);
  const [doc, setDoc] = useState<Directory | null>(initial);
  const [loading, setLoading] = useState<boolean>(!initial);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  /** The venue picked here; null = follow the app's venue. */
  const [locationPick, setLocationPick] = useState<string | null>(null);
  const [shown, setShown] = useState(PAGE);
  const [openId, setOpenId] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [details, setDetails] = useState<Record<string, DetailEntry>>({});

  // ---------------------------------------------------------- connection --

  const loadConnect = useCallback(async () => {
    const res = await apiFetch('/api/connectors/bamboohr/connect-info').catch(() => null);
    if (!res || !res.ok) {
      setConnect({ readable: false });
      return;
    }
    const info = await res.json().catch(() => null);
    const map = new Map<string, { status: string; scope: string }>();
    for (const v of (info?.venues ?? []) as { venue_id: string; status: string; scope: string }[]) {
      map.set(v.venue_id, { status: v.status, scope: v.scope });
    }
    setConnect({ readable: true, configured: !!info?.configured, venues: map });
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiFetch('/api/venues')
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((d) => {
        if (!cancelled) setVenues(d ? (Array.isArray(d) ? d : (d.venues ?? [])) : []);
      });
    void loadConnect();
    return () => { cancelled = true; };
  }, [loadConnect]);

  /**
   * Which venue's connection to read through, and a key naming that
   * connection (a venue's own, or the organisation's). `undefined` = still
   * working it out; `null` = BambooHR isn't connected for this person.
   */
  const cred = useMemo((): { venueId: string | null; key: string } | null | undefined => {
    if (!connect || venues === null) return undefined;
    if (!connect.readable) {
      const v = appVenue || venues[0]?.id || null;
      return { venueId: v, key: v || 'none' };
    }
    const ok = (id: string | null | undefined) => !!id && connect.venues.get(id)?.status === 'connected';
    const venueId = ok(appVenue) ? appVenue : [...connect.venues.keys()].find((id) => ok(id)) ?? null;
    if (venueId) {
      return { venueId, key: connect.venues.get(venueId)?.scope === 'venue' ? `venue:${venueId}` : 'organisation' };
    }
    // No venues of their own but an organisation-wide connection: read without one.
    if (connect.configured && connect.venues.size === 0) return { venueId: null, key: 'organisation' };
    return null;
  }, [connect, appVenue, venues]);

  const credKey = cred === undefined ? undefined : cred === null ? null : cred.key;
  const credVenue = cred ? cred.venueId : null;
  // For the record reads, which run outside render. Declared before the
  // effects below so it is current when they run.
  const credVenueRef = useRef<string | null>(null);
  useEffect(() => { credVenueRef.current = credVenue; }, [credVenue]);

  // ----------------------------------------------------------- directory --

  const loadSeq = useRef(0);
  const load = useCallback(async (venueId: string | null) => {
    const seq = ++loadSeq.current;
    const stale = () => seq !== loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      const base: Record<string, unknown> = { view: 'employees', limit: 100, ...(venueId ? { venue_id: venueId } : {}) };
      const first = await runGetHr(base);
      if (stale()) return;
      const firstDoc = first.ok ? extractDirectory(first.body) : null;
      if (!firstDoc) {
        setDoc(null);
        setError(toolError(first.body) || `Could not load staff (${first.status})`);
        return;
      }
      // get_hr returns at most 100 people a call. A bigger group: read each
      // venue on its own and put them together, so nobody silently drops off.
      let merged = firstDoc;
      if (firstDoc.current_staff > firstDoc.employees.length) {
        const seen = new Map(firstDoc.employees.map((e) => [e.id, e]));
        for (const loc of Object.keys(firstDoc.by_venue)) {
          if (loc === '—') continue;
          const part = await runGetHr({ ...base, location: loc });
          if (stale()) return;
          for (const e of extractDirectory(part.body)?.employees ?? []) seen.set(e.id, e);
        }
        merged = { ...firstDoc, employees: [...seen.values()] };
      }
      setDoc(merged);
    } catch (e) {
      if (stale()) return;
      setError(e instanceof Error ? e.message : 'Could not load staff');
    } finally {
      if (!stale()) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (credKey === undefined) return;
    if (credKey === null) {
      loadSeq.current += 1; // a load in flight may not land
      setLoading(false);
      setDoc(null);
      return;
    }
    // A chat block that arrived with its directory needs no first load.
    if (initial && reloadKey === 0 && !persistVenue) return;
    void load(credVenue);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [credKey, reloadKey]);

  const rows = useMemo(() => sortRows(doc?.employees ?? []), [doc]);
  const locations = useMemo(() => distinct(rows, (r) => r.venue), [rows]);
  const teams = useMemo(() => distinct(rows, (r) => r.division), [rows]);
  const roles = useMemo(() => distinct(rows, (r) => r.job_title), [rows]);
  const hasNoTeam = rows.some((r) => !r.division);

  // The venue filter follows the app's venue (when BambooHR has a location of
  // that name) until the person picks one here.
  const appVenueName = venues?.find((v) => v.id === appVenue)?.name ?? null;
  const location = locationPick ?? (persistVenue ? matchLocation(appVenueName, locations) ?? '' : '');
  const active: Filters = useMemo(() => ({ ...filters, location }), [filters, location]);

  const setFilter = <K extends keyof Filters>(key: K, value: Filters[K]) => {
    if (key === 'location') setLocationPick(value as string);
    else setFilters((f) => ({ ...f, [key]: value }));
    setShown(PAGE);
  };
  const clearFilters = () => {
    setFilters({ ...EMPTY_FILTERS });
    setLocationPick('');
    setShown(PAGE);
  };

  // ------------------------------------------------ records, a few at once --

  const statusOf = useCallback((r: DirectoryRow) => {
    if (r.status !== undefined) return r.status;
    const d = details[r.id];
    return d && !isFailed(d) ? d.status : null;
  }, [details]);

  const filtered = useMemo(() => applyFilters(rows, active, statusOf), [rows, active, statusOf]);
  const visible = useMemo(() => filtered.slice(0, shown), [filtered, shown]);
  /** The directory doesn't carry start dates (today it never does). */
  const needsRecords = rows.some((r) => r.hire_date === undefined);

  // Every change to `details` goes through this ref first, so the reads
  // below always see what has already been asked for.
  const detailsRef = useRef<Record<string, DetailEntry>>({});
  const gen = useRef(0);
  const inflight = useRef(new Set<string>());
  const queue = useRef<string[]>([]);

  const pump = useCallback(() => {
    const myGen = gen.current;
    while (inflight.current.size < PARALLEL && queue.current.length) {
      const id = queue.current.shift() as string;
      if (inflight.current.has(id) || detailsRef.current[id]) continue;
      inflight.current.add(id);
      const venueId = credVenueRef.current;
      runGetHr({ view: 'employees', employee_id: id, ...(venueId ? { venue_id: venueId } : {}) })
        .then(({ ok, status, body }): DetailEntry => {
          const p = ok ? extractPerson(body) : null;
          if (p) return p;
          return { failed: readError(toolError(body) || `BambooHR didn’t answer (${status})`).text };
        })
        .catch((e): DetailEntry => ({ failed: e instanceof Error ? e.message : 'Could not load this record' }))
        .then((entry) => {
          if (myGen !== gen.current) return; // a newer connection or a Refresh
          inflight.current.delete(id);
          detailsRef.current = { ...detailsRef.current, [id]: entry };
          setDetails(detailsRef.current);
          pump();
        });
    }
  }, []);

  // A different connection, or Refresh: start the records over.
  useEffect(() => {
    gen.current += 1;
    inflight.current = new Set();
    queue.current = [];
    detailsRef.current = {};
    setDetails({});
  }, [credKey, reloadKey]);

  // Read the records of the people on screen (the open one first).
  const visibleIds = visible.map((r) => r.id).join(',');
  useEffect(() => {
    if (!doc || !credKey) return;
    const want = [
      ...(openId ? [openId] : []),
      ...(needsRecords ? visibleIds.split(',').filter(Boolean) : []),
    ].filter((id) => !detailsRef.current[id] && !inflight.current.has(id));
    queue.current = [...new Set(want)];
    pump();
  }, [visibleIds, openId, doc, credKey, needsRecords, pump]);

  const retryRecord = (id: string) => {
    const next = { ...detailsRef.current };
    delete next[id];
    detailsRef.current = next;
    setDetails(next);
    queue.current = [id, ...queue.current.filter((x) => x !== id)];
    pump();
  };

  const filling = needsRecords && visible.some((r) => !details[r.id]);

  const refresh = () => {
    void loadConnect().then(() => setReloadKey((k) => k + 1));
  };

  // ------------------------------------------------------------- render --

  const startOf = (r: DirectoryRow): { text: string; pending: boolean } => {
    if (r.hire_date !== undefined) return { text: formatDay(r.hire_date), pending: false };
    const d = details[r.id];
    if (!d) return { text: '', pending: true };
    if (isFailed(d)) return { text: '—', pending: false };
    return { text: d.hireDate ? formatDay(d.hireDate) : '—', pending: false };
  };
  const employmentOf = (r: DirectoryRow): string | null => {
    const d = details[r.id];
    return d && !isFailed(d) ? sentenceCase(d.employmentHistoryStatus) : null;
  };
  const statusBadge = (r: DirectoryRow) => {
    const on = isActive(statusOf(r));
    return <Badge tone={on ? 'ok' : 'neutral'}>{on ? 'Active' : 'Inactive'}</Badge>;
  };

  // The tiles count across teams: the team tiles ARE the team filter.
  const acrossTeams = useMemo(() => applyFilters(rows, active, statusOf, { team: true }), [rows, active, statusOf]);
  const teamTiles = useMemo(() => teamCounts(acrossTeams).slice(0, 3), [acrossTeams]);
  const venueCount = useMemo(() => new Set(acrossTeams.map((r) => r.venue).filter(Boolean)).size, [acrossTeams]);

  const tiles = doc && rows.length > 0 && acrossTeams.length > 0 ? (
    <div style={{ marginBottom: 12 }}>
      <TileRow min={stacked ? 140 : 160}>
        <StatTile
          label={active.status === 'inactive' ? 'Former staff' : active.status === 'all' ? 'Everyone' : 'Current staff'}
          value={acrossTeams.length}
          sub={active.location || `across ${venueCount} venue${venueCount === 1 ? '' : 's'}`}
          onClick={() => setFilter('team', '')}
          selected={!active.team}
        />
        {teamTiles.map((t) => (
          <StatTile
            key={t.team}
            label={t.team}
            value={t.count}
            sub={`${Math.round((t.count / acrossTeams.length) * 100)}% of staff`}
            onClick={() => setFilter('team', active.team === t.team ? '' : t.team)}
            selected={active.team === t.team}
          />
        ))}
      </TileRow>
    </div>
  ) : null;

  // Desktop: one row. Phone: the search on its own line, then the four
  // pickers two to a row, each filling its half.
  const pick: React.CSSProperties = stacked ? { width: '100%' } : {};
  const pickBox: React.CSSProperties = stacked ? { position: 'relative', display: 'flex', minWidth: 0 } : { position: 'relative', display: 'inline-flex', maxWidth: '100%' };
  const toolbar = doc && rows.length > 0 ? (
    <div style={stacked
      ? { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }
      : { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}
    >
      <span style={{ position: 'relative', display: 'inline-flex', flex: '1 1 220px', maxWidth: stacked ? undefined : 300, gridColumn: stacked ? '1 / -1' : undefined }}>
        <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
        <input
          className="n-input"
          type="search"
          value={active.query}
          onChange={(e) => setFilter('query', e.target.value)}
          placeholder="Search name or role…"
          aria-label="Search staff"
          style={{ width: '100%', paddingLeft: 34 }}
        />
      </span>
      {locations.length > 1 && (
        // BambooHR's own locations (it has some Norm doesn't, like a head
        // office), drawn like the VenueSelect every page header uses.
        <span style={pickBox}>
          <Icon icon={Store} size={16} tone="muted" style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
          <select
            className="n-select n-select--with-icon"
            value={active.location}
            onChange={(e) => setFilter('location', e.target.value)}
            aria-label="Venue"
            style={{ maxWidth: '100%', ...pick }}
          >
            <option value="">All venues</option>
            {locations.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </span>
      )}
      <select className="n-select" value={active.team} onChange={(e) => setFilter('team', e.target.value)} aria-label="Team" style={pick}>
        <option value="">All teams</option>
        {teams.map((t) => <option key={t} value={t}>{t}</option>)}
        {hasNoTeam && <option value={NO_TEAM}>No team set</option>}
      </select>
      <select className="n-select" value={active.role} onChange={(e) => setFilter('role', e.target.value)} aria-label="Role" style={stacked ? pick : { maxWidth: 220 }}>
        <option value="">All roles</option>
        {roles.map((r) => <option key={r} value={r}>{r}</option>)}
      </select>
      <select className="n-select" value={active.status} onChange={(e) => setFilter('status', e.target.value as StatusFilter)} aria-label="Status" style={pick}>
        {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  ) : null;

  const nameButton = (r: DirectoryRow) => {
    const alias = goesBy(r.name, r.preferred);
    return (
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpenId(r.id); }}
        style={{ background: 'none', border: 0, padding: 0, margin: 0, font: 'inherit', fontWeight: 600, color: 'var(--text)', textAlign: 'left', cursor: 'pointer', overflowWrap: 'anywhere' }}
      >
        {r.name}
        {alias && <span style={{ fontWeight: 400, color: 'var(--muted)' }}> ({alias})</span>}
      </button>
    );
  };

  const startCell = (r: DirectoryRow) => {
    const s = startOf(r);
    return s.pending ? <span style={{ color: 'var(--muted)' }} aria-label="Loading">…</span> : s.text;
  };

  const table = (
    <table className="n-table" style={{ width: '100%' }}>
      <thead>
        <tr>
          <th>Name</th>
          <th>Job title</th>
          <th>Team</th>
          <th>Venue</th>
          <th>Started</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {visible.map((r) => {
          const employment = employmentOf(r);
          return (
            <tr key={r.id} onClick={() => setOpenId(r.id)} style={{ cursor: 'pointer' }}>
              <td>{nameButton(r)}</td>
              <td>{r.job_title || '—'}</td>
              <td style={{ color: r.division ? undefined : 'var(--muted)' }}>{r.division || '—'}</td>
              <td>{r.venue || '—'}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{startCell(r)}</td>
              <td style={{ whiteSpace: 'nowrap' }}>
                {statusBadge(r)}
                {employment && <span style={{ marginLeft: 8, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{employment}</span>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );

  const cards = (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {visible.map((r) => {
        const s = startOf(r);
        const employment = employmentOf(r);
        return (
          <li key={r.id} className="n-card" style={{ padding: '10px 12px', cursor: 'pointer' }} onClick={() => setOpenId(r.id)}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
              <span style={{ minWidth: 0 }}>{nameButton(r)}</span>
              {statusBadge(r)}
            </div>
            <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
              {[r.job_title, r.venue].filter(Boolean).join(' · ') || '—'}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 10px', marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
              {r.division && <span>{r.division}</span>}
              <span>{s.pending ? 'Started …' : `Started ${s.text}`}</span>
              {employment && <span>{employment}</span>}
            </div>
          </li>
        );
      })}
    </ul>
  );

  const errorKind = error ? readError(error).kind : null;
  const canConnect = connect?.readable === true;

  const connectHere = (reconnect: boolean) => canConnect ? (
    <ConnectPrompt
      connector="bamboohr"
      venueId={appVenue}
      reconnect={reconnect}
      compact={!persistVenue}
      title={reconnect ? 'BambooHR refused Norm’s API key' : 'BambooHR is not connected'}
      detail={reconnect
        ? 'An admin needs to re-enter the BambooHR subdomain and API key below.'
        : 'Norm reads your staff directory from BambooHR. Enter your BambooHR subdomain and API key below; one connection covers every venue.'}
      onAction={async (a) => {
        // The card says "I've connected BambooHR" once the key is saved.
        if (a.action === 'send_message') refresh();
      }}
    />
  ) : (
    <PageState
      kind={reconnect ? 'error' : 'empty'}
      title={reconnect ? 'BambooHR refused Norm’s API key.' : 'BambooHR is not connected.'}
      detail="Ask an owner or admin to connect BambooHR in Settings → Connections."
    />
  );

  const inCard = (node: React.ReactNode) => (persistVenue ? node : <div className="n-card">{node}</div>);

  let body: React.ReactNode;
  if (loading || cred === undefined) {
    body = inCard(<PageState kind="loading" title="Loading staff from BambooHR…" />);
  } else if (cred === null || errorKind === 'not_connected') {
    body = inCard(connectHere(false));
  } else if (errorKind === 'refused') {
    body = inCard(connectHere(true));
  } else if (error) {
    body = inCard(
      <PageState
        kind="error"
        title="Couldn’t load staff from BambooHR."
        detail={extraDetail(readError(error).text)}
        action={<Button size="sm" variant="secondary" onClick={refresh}>Try again</Button>}
      />,
    );
  } else if (!doc || rows.length === 0) {
    body = inCard(<PageState kind="empty" title="BambooHR lists no current staff." detail={doc?.note || undefined} />);
  } else if (filtered.length === 0) {
    const onlyCurrent = active.status === 'inactive' && rows.every((r) => r.status === undefined);
    body = inCard(
      <PageState
        kind="empty"
        title={onlyCurrent ? 'Former staff aren’t available here yet.' : 'Nobody matches these filters.'}
        detail={onlyCurrent ? 'BambooHR’s staff directory, which Norm reads, lists current staff only.' : undefined}
        action={<Button size="sm" variant="secondary" onClick={clearFilters}>Clear filters</Button>}
      />,
    );
  } else {
    body = (
      <>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 8, minHeight: 20, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          <span>{filtered.length === rows.length ? `${rows.length} people` : `${filtered.length} of ${rows.length} people`}</span>
          {filling && <Spinner label="Reading start dates…" />}
        </div>
        {stacked ? cards : (
          <div className="n-card" style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            {table}
          </div>
        )}
        {filtered.length > visible.length && (
          <div style={{ textAlign: 'center', marginTop: 12 }}>
            <Button size="sm" variant="secondary" onClick={() => setShown((n) => n + PAGE)}>
              Show {Math.min(PAGE, filtered.length - visible.length)} more
            </Button>
          </div>
        )}
      </>
    );
  }

  const venueTotal = doc ? Object.keys(doc.by_venue).filter((k) => k !== '—').length : 0;
  const meta = doc && !loading
    ? `${doc.current_staff} current staff across ${venueTotal} venue${venueTotal === 1 ? '' : 's'}, from BambooHR`
    : ' ';

  const actions = (
    <Button size="sm" variant="quiet" icon={RefreshCw} onClick={refresh} disabled={loading} aria-label="Refresh">
      Refresh
    </Button>
  );

  const person = openId ? (
    <PersonDialog
      narrow={stacked}
      row={rows.find((r) => r.id === openId) ?? null}
      detail={details[openId]}
      directory={rows}
      onOpen={setOpenId}
      onRetry={() => retryRecord(openId)}
      onClose={() => setOpenId(null)}
    />
  ) : null;

  if (persistVenue) {
    return (
      <div>
        <PageHeader title={title} meta={meta} actions={actions}>{toolbar}</PageHeader>
        {tiles}
        {body}
        {person}
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>{title}</h2>
          {meta.trim() && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</span>}
        </div>
        {actions}
      </div>
      {toolbar && <div style={{ marginBottom: 10 }}>{toolbar}</div>}
      {tiles}
      {body}
      {person}
    </div>
  );
}

// ---------------------------------------------------------------- person --

/** One person's work and contact details — nothing more (see the privacy note above). */
function PersonDialog({ narrow, row, detail, directory, onOpen, onRetry, onClose }: {
  narrow: boolean;
  row: DirectoryRow | null;
  detail: DetailEntry | undefined;
  directory: DirectoryRow[];
  onOpen: (id: string) => void;
  onRetry: () => void;
  onClose: () => void;
}) {
  const d = detail && !isFailed(detail) ? detail : null;
  const pending = !detail;
  const name = d?.name || row?.name || 'Employee';
  const preferred = goesBy(name, d?.preferredName ?? row?.preferred);
  const on = isActive(d?.status ?? row?.status);
  const manager = managerName(d?.supervisor);
  const managerRow = manager ? directory.find((r) => r.name.toLowerCase() === manager.toLowerCase()) : undefined;
  const started = d?.hireDate ?? row?.hire_date ?? null;
  const since = tenure(started, new Date());
  const email = d?.workEmail ?? row?.work_email ?? null;
  const phone = d?.mobilePhone ?? row?.mobile ?? null;

  const dash = <span style={{ color: 'var(--muted)' }}>—</span>;
  const waiting = <span style={{ color: 'var(--muted)' }}>…</span>;
  const value = (v: string | null | undefined, needsRecord = false) => (v ? v : needsRecord && pending ? waiting : dash);
  const link: React.CSSProperties = { color: 'var(--accent)', display: 'inline-flex', alignItems: 'center', gap: 6, overflowWrap: 'anywhere' };

  const work = [
    { label: 'Job title', value: value(d?.jobTitle ?? row?.job_title) },
    { label: 'Team', value: value(d?.division ?? row?.division) },
    { label: 'Venue', value: value(d?.location ?? row?.venue) },
    {
      label: 'Manager',
      value: managerRow ? (
        <Button variant="link" size="sm" onClick={() => onOpen(managerRow.id)}>{manager}</Button>
      ) : value(manager, true),
    },
    { label: 'Employment', value: value(sentenceCase(d?.employmentHistoryStatus), true) },
    { label: 'Status', value: pending ? waiting : <Badge tone={on ? 'ok' : 'neutral'}>{d?.status || (on ? 'Active' : 'Inactive')}</Badge> },
    {
      label: 'Started',
      value: started ? (
        <span>{formatDay(started)}{since && <span style={{ color: 'var(--muted)' }}> · {since}</span>}</span>
      ) : value(null, true),
    },
    ...(d?.originalHireDate && d.originalHireDate !== d.hireDate ? [{ label: 'First started', value: formatDay(d.originalHireDate) }] : []),
    ...(d?.terminationDate ? [{ label: 'Finished', value: formatDay(d.terminationDate) }] : []),
    { label: 'Employing company', value: value(d?.department ?? row?.company) },
  ];

  const contact = [
    {
      label: 'Work email',
      value: email ? <a href={`mailto:${email}`} style={link}><Icon icon={Mail} size="dense" tone="muted" />{email}</a> : value(null, true),
    },
    {
      label: 'Phone',
      value: phone ? <a href={`tel:${phone.replace(/\s+/g, '')}`} style={link}><Icon icon={Phone} size="dense" tone="muted" />{phone}</a> : value(null, true),
    },
    ...(d?.workPhone ? [{ label: 'Work phone', value: d.workPhone }] : []),
    ...(preferred ? [{ label: 'Goes by', value: preferred }] : []),
  ];

  const heading = (text: string) => (
    <h4 style={{ margin: '0 0 10px', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-soft)' }}>{text}</h4>
  );

  return (
    <Dialog title={name} width={600} onClose={onClose} closeButton>
      <div data-testid="employee-record">
        <div style={{ marginTop: -10, marginBottom: 16, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          {[d?.jobTitle ?? row?.job_title, d?.location ?? row?.venue].filter(Boolean).join(' · ')}
        </div>
        {isFailed(detail) && (
          <div style={{ marginBottom: 14 }}>
            <PageState
              kind="error"
              title="Couldn’t read this person’s record from BambooHR."
              detail={extraDetail(detail.failed)}
              action={<Button size="sm" variant="secondary" onClick={onRetry}>Try again</Button>}
            />
          </div>
        )}
        {heading('Work')}
        <Fields items={work} min={narrow ? 120 : 160} />
        <div style={{ height: 18 }} />
        {heading('Contact')}
        <Fields items={contact} min={narrow ? 120 : 200} />
        <p style={{ margin: '18px 0 0', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          Pay, tax and bank details, date of birth, home address and emergency contacts stay in BambooHR and aren’t shown in Norm.
        </p>
      </div>
    </Dialog>
  );
}
