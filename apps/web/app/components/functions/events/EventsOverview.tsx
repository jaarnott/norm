'use client';

/**
 * Events — the list and the month calendar, with their header and toolbar.
 * The list loads every event in its date range (cancelled ones included) so
 * the status tiles count the whole range; the calendar loads one month.
 */
import { useEffect, useId, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight, List, Plus, Search } from 'lucide-react';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import VenueSelect from '../../ui/VenueSelect';
import { fnGet } from '../shared/api';
import { dateLabel, money } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import EventsCalendar, { CalendarLegend } from './EventsCalendar';
import EventsList from './EventsList';
import NewBookingDialog from './NewBookingDialog';
import { RANGE_PRESETS, addMonths, daysBetween, filterByStatus, monthLabel, monthOf, presetRange } from './logic';
import type { RangePreset, StatusFilter } from './logic';
import { Hint, READ_ONLY_HINT } from './ui';
import { errorText, useLoad } from './useLoad';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import type { CalendarMonth, EventList, EventRow } from './types';

export type EventsView = 'list' | 'calendar';

const MAX_PAGES = 5;

/** Every event in the range, paging through the API (1000 a page). */
async function loadRange(params: Record<string, unknown>): Promise<{ rows: EventRow[]; capped: boolean }> {
  const rows: EventRow[] = [];
  for (let page = 0; page < MAX_PAGES; page++) {
    const res = await fnGet<EventList>('/events', { ...params, include_cancelled: true, sort: 'date_asc', limit: 1000, offset: rows.length });
    rows.push(...res.rows);
    if (!res.has_more) return { rows, capped: false };
  }
  return { rows, capped: true };
}

const chip = (active: boolean): CSSProperties => ({
  flex: '0 0 auto', padding: '5px 12px', borderRadius: 999, cursor: 'pointer', whiteSpace: 'nowrap',
  fontFamily: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: active ? 600 : 500,
  border: `1px solid ${active ? 'var(--brand-soft)' : 'var(--line)'}`,
  background: active ? 'var(--accent-soft)' : 'var(--surface-alt)',
  color: active ? 'var(--accent)' : 'var(--text)',
});

function ViewToggle({ view, onView }: { view: EventsView; onView: (v: EventsView) => void }) {
  const item = (v: EventsView, label: string, icon: typeof List): ReactNode => (
    <button
      type="button"
      aria-pressed={view === v}
      onClick={() => onView(v)}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px', border: 'none', cursor: 'pointer',
        font: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: view === v ? 600 : 500,
        background: view === v ? 'var(--selected)' : 'var(--bg)', color: view === v ? 'var(--text)' : 'var(--text-soft)',
      }}
    >
      <Icon icon={icon} size={15} />
      {label}
    </button>
  );
  return (
    <span role="group" aria-label="View" style={{ display: 'inline-flex', border: '1px solid var(--line-strong)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
      {item('list', 'List', List)}
      <span aria-hidden="true" style={{ width: 1, background: 'var(--line-strong)' }} />
      {item('calendar', 'Calendar', CalendarDays)}
    </span>
  );
}

export default function EventsOverview({ meta, isPage, view, onView, today, onOpen }: {
  meta: FnMeta;
  isPage: boolean;
  view: EventsView;
  onView: (v: EventsView) => void;
  today: string;
  onOpen: (id: string) => void;
}) {
  const ids = { from: useId(), to: useId(), search: useId() };
  const { isMobile } = useBreakpoint();
  const cur = meta.company.currency;
  const [venue, setVenue] = useState<string>('all');
  const [query, setQuery] = useState('');
  const [q, setQ] = useState('');
  const [preset, setPreset] = useState<RangePreset>('next14');
  const [custom, setCustom] = useState(() => presetRange('next14', today));
  const [filter, setFilter] = useState<StatusFilter>(null);
  const [page, setPage] = useState(0);
  const [month, setMonth] = useState(() => monthOf(today));
  const [showCancelled, setShowCancelled] = useState(false);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  const range = preset === 'custom' ? custom : presetRange(preset, today);
  const rangeOk = !!range.from && !!range.to && range.from <= range.to && daysBetween(range.from, range.to) <= 731;
  const venueParam = venue === 'all' ? undefined : venue;

  const listKey = view === 'list' && rangeOk ? JSON.stringify({ range, venueParam, q }) : null;
  const list = useLoad(listKey, () => loadRange({ from: range.from, to: range.to, venue: venueParam, q: q || undefined }));
  const calKey = view === 'calendar' ? JSON.stringify({ month, venueParam, q, showCancelled }) : null;
  const cal = useLoad(calKey, () =>
    fnGet<CalendarMonth>('/events/calendar', { month, venue: venueParam, q: q || undefined, include_cancelled: showCancelled || undefined }),
  );

  // A new range, venue or search starts at the first page.
  const resetPage = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setPage(0);
  };

  const rows = list.data?.rows ?? [];
  const live = filterByStatus(rows, null);
  const liveSpend = live.reduce((s, r) => s + (r.expected_spend_cents || 0), 0);
  const rangeText = preset === 'custom' ? `${dateLabel(range.from, { year: false })} – ${dateLabel(range.to)}` : RANGE_PRESETS.find((p) => p.id === preset)?.label.toLowerCase();
  const metaLine = view === 'list'
    ? list.data
      ? `${live.length} ${live.length === 1 ? 'event' : 'events'} · ${money(liveSpend, cur, 0)} expected ${meta.company.tax_label} · ${rangeText}`
      : rangeText
    : cal.data
      ? `${cal.data.events} ${cal.data.events === 1 ? 'event' : 'events'} · ${money(cal.data.expected_spend_cents, cur, 0)} expected ${meta.company.tax_label}`
      : monthLabel(month);

  const venuePicker = meta.venues.length > 1 ? (
    <VenueSelect venues={meta.venues} value={venue} onChange={resetPage(setVenue)} allowAll />
  ) : null;
  const newButton = (
    <Button
      variant="primary"
      icon={Plus}
      onClick={() => setCreating(true)}
      disabled={!meta.can_write}
      title={meta.can_write ? undefined : READ_ONLY_HINT}
    >
      New booking
    </Button>
  );

  const search = (
    <span style={{ position: 'relative', display: 'inline-flex', flex: '1 1 200px', maxWidth: 320, minWidth: 0 }}>
      <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
      <input
        id={ids.search}
        type="search"
        aria-label="Search events"
        className="n-input"
        placeholder="Event or contact name"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setPage(0); }}
        style={{ width: '100%', paddingLeft: 32 }}
      />
    </span>
  );

  const toolbar = view === 'list' ? (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <ViewToggle view={view} onView={onView} />
        <div role="group" aria-label="Date range" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, maxWidth: '100%' }}>
          {RANGE_PRESETS.map((p) => (
            <button key={p.id} type="button" aria-pressed={preset === p.id} style={chip(preset === p.id)} onClick={() => resetPage(setPreset)(p.id)}>
              {p.label}
            </button>
          ))}
          <button type="button" aria-pressed={preset === 'custom'} style={chip(preset === 'custom')}
            onClick={() => { if (preset !== 'custom') setCustom(range); resetPage(setPreset)('custom'); }}>
            Custom
          </button>
        </div>
        {search}
      </div>
      {preset === 'custom' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <label htmlFor={ids.from} style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>From</label>
          <input id={ids.from} type="date" className="n-input" value={custom.from} onChange={(e) => resetPage(setCustom)({ ...custom, from: e.target.value })} />
          <label htmlFor={ids.to} style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>to</label>
          <input id={ids.to} type="date" className="n-input" value={custom.to} onChange={(e) => resetPage(setCustom)({ ...custom, to: e.target.value })} />
          {!rangeOk && <span role="alert" style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>Pick a start before the end, at most two years apart.</span>}
        </div>
      )}
    </div>
  ) : (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      <ViewToggle view={view} onView={onView} />
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
        <IconButton icon={ChevronLeft} label="Previous month" onClick={() => setMonth(addMonths(month, -1))} />
        <span aria-live="polite" style={{ minWidth: 128, textAlign: 'center', fontSize: 'var(--fs-base)', fontWeight: 600 }}>{monthLabel(month)}</span>
        <IconButton icon={ChevronRight} label="Next month" onClick={() => setMonth(addMonths(month, 1))} />
      </span>
      <Button size="sm" onClick={() => setMonth(monthOf(today))} disabled={month === monthOf(today)}>Today</Button>
      <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer' }}>
        <input type="checkbox" checked={showCancelled} onChange={(e) => setShowCancelled(e.target.checked)} style={{ margin: 0, accentColor: 'var(--accent)' }} />
        Show cancelled
      </label>
      {search}
    </div>
  );

  const venueName = venue === 'all' ? null : meta.venues.find((v) => v.id === venue)?.name;
  const body = view === 'list' ? (
    !rangeOk ? null : list.error !== undefined && !list.data ? (
      <PageState kind="error" title="Couldn’t load events" detail={errorText(list.error)} action={<Button size="sm" onClick={list.reload}>Try again</Button>} />
    ) : list.loading || !list.data ? (
      <PageState kind="loading" title="Loading events…" />
    ) : (
      <EventsList
        meta={meta}
        rows={rows}
        capped={list.data.capped}
        filter={filter}
        onFilter={(f) => { setFilter(f); setPage(0); }}
        page={page}
        onPage={setPage}
        showVenue={venue === 'all' && meta.venues.length > 1}
        onOpen={onOpen}
        emptyDetail={q ? `Nothing matches “${q}”.` : venueName ? `Nothing booked at ${venueName} in this range.` : 'Try a longer range.'}
      />
    )
  ) : (
    <>
      {!isMobile && <CalendarLegend />}
      <EventsCalendar
        month={cal.data}
        loading={cal.loading}
        error={cal.error !== undefined ? errorText(cal.error) : null}
        today={today}
        currency={cur}
        onOpen={onOpen}
      />
    </>
  );

  return (
    <div style={{ width: '100%' }}>
      {isPage ? (
        <PageHeader
          title="Events"
          meta={metaLine}
          actions={<>{venuePicker}{!meta.can_write && <Hint>{READ_ONLY_HINT}</Hint>}{newButton}</>}
        >
          {toolbar}
        </PageHeader>
      ) : (
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
            <div style={{ flex: '1 1 200px', minWidth: 0 }}>
              <h2 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600 }}>Events</h2>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{metaLine}</div>
            </div>
            {venuePicker}
          </div>
          {toolbar}
        </div>
      )}
      {body}
      {creating && (
        <NewBookingDialog
          meta={meta}
          defaultVenueId={venueParam ?? null}
          today={today}
          onClose={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            onOpen(id);
          }}
        />
      )}
    </div>
  );
}
