'use client';

/**
 * The events list: status tiles that filter, then the events grouped by
 * month — a table on wide screens, one card per event on narrow ones — and
 * paging. Rows are already loaded for the chosen range; filtering and paging
 * happen here so the tile counts always describe the whole range.
 */
import type { CSSProperties } from 'react';
import { ChevronLeft, ChevronRight, TriangleAlert } from 'lucide-react';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageState from '../../ui/PageState';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { dateLabel, money, timeRangeLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { STATUS_LABEL, STATUS_ORDER, EDITED_LABEL, filterByStatus, groupByMonth, pageOf, statusCounts } from './logic';
import type { StatusFilter } from './logic';
import { EditedFlag, STATUS_COLOURS, STATUS_ICON, StatusBadge } from './ui';
import type { EventRow } from './types';

export const PAGE_SIZE = 50;

const rowButton: CSSProperties = {
  display: 'block', padding: 0, border: 'none', background: 'transparent', font: 'inherit', color: 'inherit',
  textAlign: 'left', cursor: 'pointer', fontWeight: 600,
};
const soft: CSSProperties = { color: 'var(--text-soft)' };
const nowrap: CSSProperties = { whiteSpace: 'nowrap' };

function StatusTiles({ rows, filter, onFilter, compact }: { rows: EventRow[]; filter: StatusFilter; onFilter: (f: StatusFilter) => void; compact: boolean }) {
  const counts = statusCounts(rows);
  const tiles: { id: Exclude<StatusFilter, null>; label: string }[] = [
    ...STATUS_ORDER.map((s) => ({ id: s, label: STATUS_LABEL[s] })),
    { id: 'edited', label: EDITED_LABEL },
  ];
  if (compact) {
    // Phones: the same filters as a wrapping row of counted chips.
    return (
      <div role="group" aria-label="Filter by status" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 14 }}>
        {tiles.map((t) => {
          const active = filter === t.id;
          const glyph = t.id === 'edited' ? TriangleAlert : STATUS_ICON[t.id];
          const colour = t.id === 'edited' ? 'var(--accent)' : STATUS_COLOURS[t.id].fg;
          return (
            <button
              key={t.id}
              type="button"
              aria-pressed={active}
              onClick={() => onFilter(active ? null : t.id)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 999, cursor: 'pointer',
                font: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: active ? 600 : 500,
                border: `1px solid ${active ? 'var(--brand-soft)' : 'var(--line)'}`,
                background: active ? 'var(--accent-soft)' : 'var(--bg)', color: active ? 'var(--accent)' : 'var(--text)',
              }}
            >
              <Icon icon={glyph} size={14} style={{ color: colour }} />
              {t.id === 'edited' ? 'Edited' : t.label}
              <span style={{ fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{counts[t.id]}</span>
            </button>
          );
        })}
      </div>
    );
  }
  return (
    <div role="group" aria-label="Filter by status" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 150px), 1fr))', gap: 8, marginBottom: 16 }}>
      {tiles.map((t) => {
        const active = filter === t.id;
        const glyph = t.id === 'edited' ? TriangleAlert : STATUS_ICON[t.id];
        const colour = t.id === 'edited' ? 'var(--accent)' : STATUS_COLOURS[t.id].fg;
        return (
          <button
            key={t.id}
            type="button"
            aria-pressed={active}
            onClick={() => onFilter(active ? null : t.id)}
            style={{
              display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4, padding: '10px 12px',
              borderRadius: 'var(--radius-lg)', cursor: 'pointer', font: 'inherit', textAlign: 'left',
              border: `1px solid ${active ? 'var(--brand-soft)' : 'var(--line)'}`,
              background: active ? 'var(--accent-soft)' : 'var(--bg)',
            }}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', fontWeight: 500, color: active ? 'var(--accent)' : 'var(--text-soft)' }}>
              <Icon icon={glyph} size={14} style={{ color: colour }} />
              {t.label}
            </span>
            <span style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: 'var(--text)' }}>
              {counts[t.id]}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function bookingLabel(meta: FnMeta, value: string | null): string {
  if (!value) return '—';
  return meta.booking_types.find((b) => b.value === value)?.label ?? value;
}

export default function EventsList({ meta, rows, capped, filter, onFilter, page, onPage, showVenue, onOpen, emptyDetail }: {
  meta: FnMeta;
  rows: EventRow[];
  capped: boolean;
  filter: StatusFilter;
  onFilter: (f: StatusFilter) => void;
  page: number;
  onPage: (p: number) => void;
  showVenue: boolean;
  onOpen: (id: string) => void;
  emptyDetail: string;
}) {
  const { isMobile, isTablet } = useBreakpoint();
  const narrow = isMobile || isTablet;
  const cur = meta.company.currency;
  const shown = filterByStatus(rows, filter);
  const paged = pageOf(shown, page, PAGE_SIZE);
  const groups = groupByMonth(paged.rows);
  // The company shows once the API sends it (build-notes F7 request).
  const hasCompany = rows.some((r) => 'contact_company' in r);

  const pager = paged.pages > 1 && (
    <nav aria-label="Pages" style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', marginRight: 'auto' }}>
        {paged.first}–{paged.last} of {paged.total}
      </span>
      <Button size="sm" icon={ChevronLeft} disabled={paged.page === 0} onClick={() => onPage(paged.page - 1)}>Previous</Button>
      <Button size="sm" disabled={paged.page >= paged.pages - 1} onClick={() => onPage(paged.page + 1)}>
        Next <Icon icon={ChevronRight} size={14} />
      </Button>
    </nav>
  );

  const body = !shown.length ? (
    <PageState
      kind="empty"
      title={filter ? `No ${filter === 'edited' ? 'edited' : STATUS_LABEL[filter].toLowerCase()} events in this range` : 'No events in this range'}
      detail={emptyDetail}
    />
  ) : narrow ? (
    <div>
      {groups.map((g) => (
        <section key={g.month} aria-label={g.label} style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, margin: '4px 2px 8px' }}>
            <span className="n-eyebrow">{g.label}</span>
            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontVariantNumeric: 'tabular-nums' }}>{money(g.expectedCents, cur, 0)}</span>
          </div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {g.rows.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => onOpen(r.id)} style={{
                  display: 'block', width: '100%', padding: '12px 14px', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)',
                  background: 'var(--bg)', font: 'inherit', color: 'var(--text)', textAlign: 'left', cursor: 'pointer', fontVariantNumeric: 'tabular-nums',
                }}>
                  <span style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
                    <span style={{ minWidth: 0, fontWeight: 600, overflowWrap: 'anywhere' }}>{r.name || 'Event'}</span>
                    <span style={{ flex: '0 0 auto', fontWeight: 600 }}>{r.expected_spend_cents ? money(r.expected_spend_cents, cur, 0) : '—'}</span>
                  </span>
                  <span style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', columnGap: 8, rowGap: 4, marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                    <StatusBadge status={r.status} />
                    {r.edited_since_confirmed && <EditedFlag compact />}
                    <span style={nowrap}>{dateLabel(r.event_date, { year: false })}</span>
                    {r.start_time && <span style={nowrap}>{timeRangeLabel(r.start_time, r.end_time, r.ends_next_day)}</span>}
                  </span>
                  <span style={{ display: 'block', marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                    {[r.contact_name, r.contact_company, showVenue ? r.venue_name : null, r.guests ? `${r.guests} guests` : null, bookingLabel(meta, r.booking_type)]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  ) : (
    <div style={{ overflowX: 'auto' }}>
      {/* Venue (and area) sit under the event name and the company under the
          contact, so the whole row fits beside the menu panel. */}
      <table className="n-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Event{showVenue ? ' · venue' : ''}</th>
            <th>Contact{hasCompany ? ' · company' : ''}</th>
            <th>Type</th>
            <th className="num">Guests</th>
            <th className="num">Expected spend</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => [
            <tr key={`m-${g.month}`} style={{ background: 'var(--surface)' }}>
              <td colSpan={7} style={{ padding: '8px 12px', background: 'var(--surface)' }}>
                <span className="n-eyebrow">{g.label}</span>
                <span style={{ marginLeft: 10, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                  {g.rows.length} {g.rows.length === 1 ? 'event' : 'events'} · {money(g.expectedCents, cur, 0)} expected
                </span>
              </td>
            </tr>,
            ...g.rows.map((r) => {
              const where = [showVenue ? r.venue_name : null, r.area_name].filter(Boolean).join(' · ');
              return (
                <tr key={r.id} onClick={() => onOpen(r.id)} style={{ cursor: 'pointer' }}>
                  <td style={nowrap}>
                    <div>{dateLabel(r.event_date, { year: false })}</div>
                    {r.start_time && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{timeRangeLabel(r.start_time, r.end_time, r.ends_next_day)}</div>}
                  </td>
                  <td style={{ minWidth: 180 }}>
                    <button type="button" style={rowButton}>{r.name || 'Event'}</button>
                    {where && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{where}</div>}
                  </td>
                  <td style={{ minWidth: 130 }}>
                    {r.contact_name || '—'}
                    {r.contact_company && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{r.contact_company}</div>}
                  </td>
                  <td style={soft}>{bookingLabel(meta, r.booking_type)}</td>
                  <td className="num">{r.guests ?? '—'}</td>
                  <td className="num" style={{ fontWeight: 500, whiteSpace: 'nowrap' }}>{r.expected_spend_cents ? money(r.expected_spend_cents, cur, 0) : '—'}</td>
                  <td>
                    <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
                      <StatusBadge status={r.status} />
                      {r.edited_since_confirmed && <EditedFlag compact />}
                    </span>
                  </td>
                </tr>
              );
            }),
          ])}
        </tbody>
      </table>
    </div>
  );

  return (
    <>
      <StatusTiles rows={rows} filter={filter} onFilter={onFilter} compact={isMobile} />
      {capped && (
        <div role="status" style={{ marginBottom: 12, fontSize: 'var(--fs-sm)', color: 'var(--warn)' }}>
          This range holds more events than one view loads — showing the first {rows.length}. Pick a shorter range.
        </div>
      )}
      {body}
      {pager}
    </>
  );
}
