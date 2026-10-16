'use client';

/**
 * The Enquiries list: the API's KPI tiles (each a filter), the toolbar, and a
 * sortable, paged table — cards on narrow panes. Sorting and filtering are
 * the server's (`GET /api/functions/enquiries`); only the five sorts it
 * offers are clickable.
 */
import { useEffect, useMemo, useState } from 'react';
import type { CSSProperties, KeyboardEvent } from 'react';
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Plus, Search, SlidersHorizontal, X } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageState from '../../ui/PageState';
import VenueSelect from '../../ui/VenueSelect';
import { dateLabel, instantLabel, money, relativeLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { FnHeader, READ_ONLY_HINT, StatTile, TileRow, useFnQuery, useWidth, Field, inputStyle } from './bits';
import {
  DEFAULT_FILTERS, activePreset, extraFilterCount, listParams, presetFilters,
  nextSort, sortState, type KpiPreset, type ListFilters, type SortColumn,
} from './filters';
import { dayOf, dueBadge, lastEmailOf, moneyAuto, sentence, sourceLabel, stageLabel, stageTone } from './labels';
import type { EnquiryList as EnquiryListData, EnquiryRow } from './types';
import { useVenueFilter } from './useVenueFilter';

const PAGE_SIZE = 50;
const CARDS_BELOW = 760;

const ELLIPSIS: CSSProperties = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };
const SUB: CSSProperties = { fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 2 };

/** Short plan date for a table cell: "Fri 20 Nov" (+ year when not this year). */
function shortPlanDate(start: string | null, end: string | null, thisYear: string): string {
  if (!start) return '—';
  const first = dateLabel(start, { year: start.slice(0, 4) !== thisYear });
  if (!end || end === start) return first;
  return `${first} – ${dateLabel(end, { weekday: false, year: end.slice(0, 4) !== thisYear })}`;
}

export default function EnquiryList({ meta, page, initialFilters, refreshKey, onOpen, onCreate }: {
  meta: FnMeta;
  page: boolean;
  initialFilters: ListFilters;
  refreshKey: number;
  onOpen: (id: string) => void;
  onCreate: () => void;
}) {
  const [venue, setVenue, venueOptions] = useVenueFilter(meta);
  const [filters, setFilters] = useState<ListFilters>(initialFilters);
  const [qInput, setQInput] = useState(initialFilters.q);
  const [offset, setOffset] = useState(0);
  const [moreOpen, setMoreOpen] = useState(() => extraFilterCount(initialFilters) > 0);
  const [boxRef, width] = useWidth();
  const cards = width > 0 && width < CARDS_BELOW;
  const tz = meta.company.default_timezone;
  const currency = meta.company.currency;
  const thisYear = String(new Date().getFullYear());
  const bookingLabels = useMemo(() => new Map(meta.booking_types.map((b) => [b.value, b.label])), [meta]);

  // Search waits for a pause in typing; any filter change starts again at
  // the first page.
  useEffect(() => {
    if (qInput === filters.q) return;
    const t = setTimeout(() => {
      setFilters((f) => ({ ...f, q: qInput }));
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [qInput, filters.q]);

  const query = useFnQuery<EnquiryListData>('/enquiries', listParams(filters, venue, offset, PAGE_SIZE), refreshKey);
  const { data, error, loading } = query;

  const setAll = (next: ListFilters) => { setFilters(next); setOffset(0); };
  const set = (patch: Partial<ListFilters>) => setAll({ ...filters, ...patch });
  const chooseVenue = (v: string) => { setVenue(v); setOffset(0); };
  const applyPreset = (preset: KpiPreset) => {
    setAll(activePreset(filters) === preset ? { ...DEFAULT_FILTERS, q: filters.q, sort: filters.sort } : presetFilters(preset, filters));
  };
  const clearAll = () => {
    setQInput('');
    setAll({ ...DEFAULT_FILTERS, sort: filters.sort });
  };
  const preset = activePreset(filters);
  const kpis = data?.kpis;
  const rows = data?.rows ?? [];
  const extra = extraFilterCount(filters);
  const anyFilter = !!(filters.q || filters.stage || filters.nextAction || extra);


  const header = (
    <FnHeader
      page={page}
      title="Enquiries"
      meta={kpis ? `${kpis.open} open · ${money(kpis.open_value_cents, currency, 0)} open value` : undefined}
      actions={
        <>
          {venueOptions.length > 1 && (
            <VenueSelect venues={venueOptions} value={venue} onChange={chooseVenue} allowAll testId="fn-enquiries-venue" />
          )}
          <Button
            variant="primary"
            icon={Plus}
            onClick={onCreate}
            disabled={!meta.can_write}
            title={meta.can_write ? 'Record a new enquiry' : READ_ONLY_HINT}
            data-testid="fn-new-enquiry"
          >
            New enquiry
          </Button>
        </>
      }
    />
  );

  const tiles = (
    <TileRow min={160}>
      <StatTile
        label="Open"
        value={kpis ? kpis.open : '—'}
        sub="New, replied or quoted"
        onClick={() => applyPreset('open')}
        selected={preset === 'open'}
        testId="fn-kpi-open"
      />
      <StatTile
        label="Need a reply"
        value={kpis ? kpis.needs_reply : '—'}
        sub="The customer wrote last, 24h+ ago"
        onClick={() => applyPreset('needs_reply')}
        selected={preset === 'needs_reply'}
        testId="fn-kpi-needs-reply"
      />
      <StatTile
        label="Follow-ups due"
        value={kpis ? kpis.follow_ups_due : '—'}
        sub="Requested, day 7 or call and close"
        onClick={() => applyPreset('follow_ups_due')}
        selected={preset === 'follow_ups_due'}
        testId="fn-kpi-follow-ups"
      />
      <StatTile
        label="Open value"
        value={kpis ? money(kpis.open_value_cents, currency, 0) : '—'}
        sub="Best option per open enquiry"
        testId="fn-kpi-value"
      />
    </TileRow>
  );

  const toolbar = (
    <div style={{ marginTop: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ position: 'relative', flex: '1 1 220px', minWidth: 0, maxWidth: 360 }}>
          <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
          <input
            type="search"
            className="n-input"
            aria-label="Search enquiries"
            placeholder="Search name, company, email or event"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            style={{ width: '100%', paddingLeft: 32 }}
            data-testid="fn-enquiries-search"
          />
        </span>
        <select className="n-select" aria-label="Stage" value={filters.stage} onChange={(e) => set({ stage: e.target.value })} data-testid="fn-enquiries-stage">
          <option value="">All stages</option>
          <option value="open">Open (new, replied, quoted)</option>
          {(['new', 'replied', 'quoted', 'confirmed', 'lost'] as const).map((s) => <option key={s} value={s}>{stageLabel(s)}</option>)}
        </select>
        <select className="n-select" aria-label="Next action" value={filters.nextAction} onChange={(e) => set({ nextAction: e.target.value })} data-testid="fn-enquiries-next-action">
          <option value="">Any next action</option>
          <option value="due">Due today or overdue</option>
          <option value="follow_ups_due">Follow-ups due</option>
          {meta.next_actions.map((a) => <option key={a.kind} value={a.kind}>{a.label}</option>)}
        </select>
        <Button
          variant="quiet"
          icon={SlidersHorizontal}
          onClick={() => setMoreOpen((o) => !o)}
          aria-expanded={moreOpen}
        >
          {extra ? `More filters (${extra})` : 'More filters'}
        </Button>
        {anyFilter && <Button variant="quiet" icon={X} onClick={clearAll}>Clear</Button>}
      </div>
      {moreOpen && (
        <div style={{ marginTop: 10, padding: 12, borderRadius: 'var(--radius-lg)', background: 'var(--surface-alt)', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 12, alignItems: 'end' }}>
          <Field label="Source">
            {(id) => (
              <select id={id} className="n-select" style={inputStyle} value={filters.source} onChange={(e) => set({ source: e.target.value })}>
                <option value="">Any source</option>
                {(meta.enquiry_sources as string[]).map((s) => <option key={s} value={s}>{sourceLabel(s)}</option>)}
              </select>
            )}
          </Field>
          <Field label="Booking type">
            {(id) => (
              <select id={id} className="n-select" style={inputStyle} value={filters.bookingType} onChange={(e) => set({ bookingType: e.target.value })}>
                <option value="">Any type</option>
                {meta.booking_types.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
              </select>
            )}
          </Field>
          <Field label="Received from">
            {(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={filters.createdFrom} onChange={(e) => set({ createdFrom: e.target.value })} />}
          </Field>
          <Field label="Received to">
            {(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={filters.createdTo} onChange={(e) => set({ createdTo: e.target.value })} />}
          </Field>
          <Field label="Event date from">
            {(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={filters.preferredFrom} onChange={(e) => set({ preferredFrom: e.target.value })} />}
          </Field>
          <Field label="Event date to">
            {(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={filters.preferredTo} onChange={(e) => set({ preferredTo: e.target.value })} />}
          </Field>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', minHeight: 34 }}>
            <input type="checkbox" checked={filters.includeArchived} onChange={(e) => set({ includeArchived: e.target.checked })} />
            Include archived
          </label>
        </div>
      )}
    </div>
  );

  /** A header button that sorts by `column` (the arrow shows the direction). */
  const sortButtonFor = (label: string, column: SortColumn) => {
    const state = sortState(filters.sort, column);
    return (
      <button type="button" onClick={() => set({ sort: nextSort(filters.sort, column) })} style={sortButton} title={`Sort by ${label.toLowerCase()}`}>
        {label}
        {state && <Icon icon={state === 'desc' ? ArrowDown : ArrowUp} size={12} />}
      </button>
    );
  };
  /** A header cell with one or two sort buttons ("Event · Contact"). */
  const sortHeader = (columns: [string, SortColumn][], extraStyle?: CSSProperties, num = false) => {
    const active = columns.map(([, c]) => sortState(filters.sort, c)).find(Boolean) ?? null;
    return (
      <th scope="col" className={num ? 'num' : undefined} aria-sort={active ? (active === 'desc' ? 'descending' : 'ascending') : undefined} style={extraStyle}>
        {columns.map(([label, column], i) => (
          <span key={column}>
            {i > 0 && <span style={{ color: 'var(--muted)', margin: '0 6px' }}>·</span>}
            {sortButtonFor(label, column)}
          </span>
        ))}
      </th>
    );
  };

  const onRowKey = (e: KeyboardEvent<HTMLTableRowElement>, id: string) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onOpen(id);
    }
  };

  // Seven columns, so the list fits a laptop with the menu panel open: the
  // enquiry cell carries the contact, the date cell the venue and guests.
  const table = (
    <div style={{ overflowX: 'auto' }}>
      <table className="n-table" aria-label="Enquiries" style={{ minWidth: 900, tableLayout: 'fixed' }}>
        <thead>
          <tr>
            {sortHeader([['Received', 'created']], { width: 88 })}
            {sortHeader([['Event', 'subject'], ['Contact', 'contact']])}
            {sortHeader([['Date', 'preferred_date'], ['Guests', 'guests']], { width: 136 })}
            {sortHeader([['Value', 'value']], { width: 116 }, true)}
            {sortHeader([['Last email', 'last_activity']], { width: 120 })}
            {sortHeader([['Next action', 'due']], { width: 156 })}
            {sortHeader([['Stage', 'stage']], { width: 96 })}
          </tr>
        </thead>
        <tbody>
          {rows.map(renderRow)}
        </tbody>
      </table>
    </div>
  );

  function renderRow(r: EnquiryRow) {
    const last = lastEmailOf(r);
    const due = dueBadge(r.next_action);
    const booking = r.booking_type ? bookingLabels.get(r.booking_type) ?? sentence(r.booking_type) : null;
    const who = [r.subject ? r.contact_name : null, r.contact_company, r.contact_email, booking].filter(Boolean).join(' · ');
    const where = [venue === 'all' ? (r.venue_name || 'Group-wide') : null, r.guests ? `${r.guests} guests` : null].filter(Boolean).join(' · ');
    const ms = r.min_spend_summary;
    // The value above is the best option's total; the line under it counts the
    // minimum-spend options still on the table (largest minimum on hover).
    const minSpend = ms && ms.count > 0 ? (ms.count === 1 ? '1 min-spend option' : `${ms.count} min-spend options`) : null;
    const minSpendTitle = ms?.largest_amount_cents ? `Largest minimum spend: ${moneyAuto(ms.largest_amount_cents, currency)}` : undefined;
    return (
      <tr key={r.id} tabIndex={0} onClick={() => onOpen(r.id)} onKeyDown={(e) => onRowKey(e, r.id)} style={{ cursor: 'pointer', opacity: r.archived_at ? 0.65 : 1 }} data-testid="fn-enquiry-row">
        <td style={{ whiteSpace: 'nowrap', color: 'var(--text-soft)' }} title={instantLabel(r.created_at, tz, true)}>{dayOf(r.created_at, tz)}</td>
        <td>
          <div style={{ ...ELLIPSIS, fontWeight: 500 }} title={r.subject ?? r.contact_name ?? undefined}>
            {r.subject || r.contact_name || <span style={{ color: 'var(--muted)' }}>No subject</span>}
            {r.archived_at && <span style={{ marginLeft: 6 }}><Badge>Archived</Badge></span>}
          </div>
          {who && <div style={{ ...SUB, ...ELLIPSIS }} title={who}>{who}</div>}
        </td>
        <td>
          <div style={ELLIPSIS}>{shortPlanDate(r.preferred_date_start, r.preferred_date_end, thisYear)}</div>
          {where && <div style={{ ...SUB, ...ELLIPSIS }} title={where}>{where}</div>}
        </td>
        <td className="num">
          <div>{r.value_cents ? moneyAuto(r.value_cents, currency) : '—'}</div>
          {minSpend && <div style={SUB} title={minSpendTitle}>{minSpend}</div>}
          {r.min_spend_summary?.needs_approval && <div style={{ marginTop: 2 }}><Badge tone="warn">Needs approval</Badge></div>}
        </td>
        <td title={last ? instantLabel(last.at, tz, true) : undefined}>
          {last ? (
            <>
              <div style={{ fontSize: 'var(--fs-sm)', ...ELLIPSIS }} title={r.last_email_from?.email ?? undefined}>
                {last.from || (last.inbound ? 'Customer' : 'We replied')}
              </div>
              <div style={SUB}>{relativeLabel(last.at)}</div>
            </>
          ) : <span style={{ color: 'var(--muted)' }}>—</span>}
        </td>
        <td>
          {r.next_action.kind === 'none' ? (
            <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>Nothing due</span>
          ) : (
            <>
              <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 500 }}>{r.next_action.label}</div>
              {due && <div style={{ marginTop: 2 }}><Badge tone={due.tone}>{due.label}</Badge></div>}
            </>
          )}
        </td>
        <td><Badge tone={stageTone(r.stage)}>{stageLabel(r.stage)}</Badge></td>
      </tr>
    );
  }

  const cardList = (
    <ul role="list" aria-label="Enquiries" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {rows.map((r) => {
        const due = dueBadge(r.next_action);
        return (
          <li key={r.id} className="n-card">
            <button type="button" onClick={() => onOpen(r.id)} style={cardButton} data-testid="fn-enquiry-card">
              <span style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
                <span style={{ minWidth: 0, fontWeight: 600, overflowWrap: 'anywhere' }}>{r.subject || r.contact_name || 'No subject'}</span>
                <Badge tone={stageTone(r.stage)}>{stageLabel(r.stage)}</Badge>
              </span>
              <span style={{ display: 'block', marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', ...ELLIPSIS }}>
                {[r.contact_name, r.contact_company, r.venue_name || 'Group-wide'].filter(Boolean).join(' · ')}
              </span>
              <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                {[
                  r.preferred_date_start ? shortPlanDate(r.preferred_date_start, r.preferred_date_end, thisYear) : null,
                  r.guests ? `${r.guests} guests` : null,
                  r.value_cents ? moneyAuto(r.value_cents, currency) : null,
                  `received ${dayOf(r.created_at, tz)}`,
                ].filter(Boolean).join(' · ')}
              </span>
              {(due || r.min_spend_summary?.needs_approval) && (
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                  {due && <Badge tone={due.tone}>{due.label}</Badge>}
                  {due && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-soft)' }}>{r.next_action.label}</span>}
                  {r.min_spend_summary?.needs_approval && <Badge tone="warn">Needs approval</Badge>}
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );

  const pager = (rows.length > 0 || offset > 0) && (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
      <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        {rows.length ? `Showing ${offset + 1}–${offset + rows.length}` : 'No more enquiries'}
      </span>
      <span style={{ display: 'flex', gap: 6 }}>
        <Button size="sm" icon={ChevronLeft} disabled={offset === 0 || loading} onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}>Previous</Button>
        <Button size="sm" disabled={!data?.has_more || loading} onClick={() => setOffset((o) => o + PAGE_SIZE)}>
          Next
          <Icon icon={ChevronRight} size={14} />
        </Button>
      </span>
    </div>
  );

  let body;
  if (error && !data) {
    body = <PageState kind="error" title="Couldn’t load enquiries" detail={error} action={<Button size="sm" onClick={query.retry}>Try again</Button>} />;
  } else if (!data) {
    body = <PageState kind="loading" title="Loading enquiries…" />;
  } else if (rows.length === 0 && offset === 0) {
    body = (
      <PageState
        kind="empty"
        title={anyFilter ? 'No enquiries match these filters.' : 'No enquiries yet.'}
        action={anyFilter ? <Button size="sm" onClick={clearAll}>Clear filters</Button> : undefined}
      />
    );
  } else {
    body = (
      // The previous page stays on screen, dimmed, while the next one loads.
      <div style={{ opacity: loading ? 0.55 : 1, transition: 'opacity 0.15s' }} aria-busy={loading || undefined}>
        {error && <PageState kind="error" title="Couldn’t refresh the list" detail={error} />}
        {cards ? cardList : table}
        {pager}
      </div>
    );
  }

  return (
    <div ref={boxRef}>
      {header}
      {tiles}
      {toolbar}
      <div style={{ marginTop: 14 }}>{body}</div>
    </div>
  );
}

const sortButton: CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 4, padding: 0, border: 'none', background: 'none',
  font: 'inherit', color: 'inherit', cursor: 'pointer', whiteSpace: 'nowrap',
};

const cardButton: CSSProperties = {
  display: 'block', width: '100%', padding: '14px 16px', border: 'none', background: 'transparent',
  borderRadius: 'calc(var(--radius-lg) - 1px)', font: 'inherit', fontSize: 'var(--fs-base)',
  color: 'var(--text)', textAlign: 'left', cursor: 'pointer',
};
