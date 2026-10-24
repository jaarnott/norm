'use client';

/**
 * Entertainers — the Roster: a month of performances at one venue or all
 * of the caller's venues, grouped by day, with each one's status and the
 * closing manager Loaded found for that night; booking (with weekly
 * repeats) and the approval dialog. Anyone with access to the venue books
 * and approves (D15).
 *
 * The Roster tab of the Entertainers page. The closing manager's email
 * links to `?page=entertainers&tab=roster&performance=<id>`: the tab reads
 * `performance` from the page's params and opens that performance's
 * approval dialog, moving the month to its date (and the venue to All
 * venues when it is at another venue). The page draws the header, the
 * venue picker and the Book performer button.
 */
import { useCallback, useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, TriangleAlert } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { entGet, errorText } from './api';
import { dateLabel, money, timeRangeLabel, todayIn } from './format';
import { CLOSING_SOURCE_LABEL, STATUS_LABEL, STATUS_ORDER, addMonths, groupByDate, monthBounds, monthLabel, monthOf, statusCounts } from './logic';
import { ChipRow, FilterChip, RowCard, StatusChip } from './ui';
import { invalidateEntMeta } from './useMeta';
import type { EntTabProps } from './workspace';
import BookingDialog from './roster/BookingDialog';
import PerformanceDialog from './roster/PerformanceDialog';
import type { Performance, PerformanceStatus, Performer } from './types';

interface RosterPayload {
  performances: Performance[];
  totals: { agreed_cents: number; actual_cents: number; count: number };
}

function ClosingManagerCell({ p }: { p: Performance }) {
  const cm = p.closing_manager;
  if (!cm || !cm.name) {
    return <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>{cm?.source === 'none' ? 'Nobody found' : '—'}</span>;
  }
  return (
    <span style={{ fontSize: 'var(--fs-sm)' }}>
      <span style={{ fontWeight: 500 }}>{cm.name}</span>
      <span style={{ color: 'var(--muted)' }}> · {CLOSING_SOURCE_LABEL[cm.source] ?? cm.source}</span>
      {!cm.user_id && (
        <span title="No Norm login under that email — the approval email can’t reach them" style={{ display: 'inline-flex', alignItems: 'center', gap: 3, marginLeft: 6, color: 'var(--warn)', fontSize: 'var(--fs-xs)', fontWeight: 600 }}>
          <Icon icon={TriangleAlert} size={12} /> no Norm login
        </span>
      )}
    </span>
  );
}

export default function EntertainersRoster({ meta, reload, venueId, setVenueId, param, setParams, primaryOpen, closePrimary }: EntTabProps) {
  const { isMobile } = useBreakpoint();
  const [month, setMonth] = useState<string>(() => monthOf(todayIn('Pacific/Auckland')));
  const [status, setStatus] = useState<PerformanceStatus | 'all'>('all');
  const [data, setData] = useState<RosterPayload | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [performers, setPerformers] = useState<Performer[]>([]);
  // The open performance is the page's `performance` param (the deep link).
  const openId = param('performance');
  const [tick, setTick] = useState(0);
  // The deep-linked performance may sit outside the month first shown.
  const [jumped, setJumped] = useState(false);

  useEffect(() => {
    let alive = true;
    entGet<{ performers: Performer[] }>('/performers').then(
      (res) => { if (alive) setPerformers(res.performers); },
      () => { /* booking just shows no names; the roster still loads */ },
    );
    return () => { alive = false; };
  }, [meta, tick]);

  useEffect(() => {
    let alive = true;
    const [from, to] = monthBounds(month);
    entGet<RosterPayload>('/roster', { venue_id: venueId === 'all' ? undefined : venueId, from, to }).then(
      (res) => { if (alive) { setData(res); setListError(null); } },
      (e: unknown) => { if (alive) setListError(errorText(e)); },
    );
    return () => { alive = false; };
  }, [meta, venueId, month, tick]);

  // The deep link: move the month to that performance's date once it is known.
  useEffect(() => {
    if (!openId || jumped) return;
    let alive = true;
    entGet<{ performance: Performance }>(`/performances/${openId}`).then(
      (res) => {
        if (!alive) return;
        setJumped(true);
        setMonth(monthOf(res.performance.performance_date));
        if (venueId !== 'all' && res.performance.venue_id !== venueId) setVenueId('all');
      },
      () => { if (alive) setJumped(true); },
    );
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on the first open
  }, [openId, jumped]);

  const refresh = useCallback(() => { setTick((t) => t + 1); invalidateEntMeta(); reload(); }, [reload]);

  const open = (id: string | null) => setParams({ performance: id ?? undefined });

  const replaceRow = (next: Performance) => {
    setData((d) => (d ? { ...d, performances: d.performances.map((p) => (p.id === next.id ? { ...p, ...next } : p)) } : d));
  };

  const rows = data?.performances ?? [];
  const counts = statusCounts(rows);
  const shown = status === 'all' ? rows : rows.filter((p) => p.status === status);
  const groups = groupByDate(shown);
  const today = todayIn(meta.venues[0]?.timezone ?? 'Pacific/Auckland');

  return (
    <div style={{ width: '100%', minWidth: 0 }}>
      {/* The tab's toolbar: the month, then the status chips (a row of their
          own on a phone), then the month's totals. */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
          <IconButton icon={ChevronLeft} label="Previous month" onClick={() => setMonth(addMonths(month, -1))} />
          <span style={{ minWidth: 140, textAlign: 'center', fontWeight: 600, fontSize: 'var(--fs-base)' }}>{monthLabel(month)}</span>
          <IconButton icon={ChevronRight} label="Next month" onClick={() => setMonth(addMonths(month, 1))} />
        </div>
        <Button size="sm" variant="quiet" onClick={() => setMonth(monthOf(today))}>This month</Button>
        <div style={{ flex: isMobile ? '1 1 100%' : '0 1 auto', minWidth: 0 }}>
          <ChipRow label="Status">
            <FilterChip active={status === 'all'} onClick={() => setStatus('all')}>All ({rows.length})</FilterChip>
            {STATUS_ORDER.filter((s) => counts[s] > 0).map((s) => (
              <FilterChip key={s} active={status === s} onClick={() => setStatus(s)}>{STATUS_LABEL[s]} ({counts[s]})</FilterChip>
            ))}
          </ChipRow>
        </div>
        {data && (
          <span style={{ marginLeft: isMobile ? undefined : 'auto', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            {data.totals.count} performance{data.totals.count === 1 ? '' : 's'} · {money(data.totals.agreed_cents)} agreed{data.totals.actual_cents ? ` · ${money(data.totals.actual_cents)} approved` : ''}
          </span>
        )}
      </div>

      {meta.venues.length === 0 ? (
        <PageState kind="empty" title="You don’t have access to any venue" detail="The roster shows the venues on your Norm account." />
      ) : listError ? (
        <PageState kind="error" title="Couldn’t load the roster" detail={listError} action={<Button size="sm" onClick={refresh}>Try again</Button>} />
      ) : data === null ? (
        <PageState kind="loading" title="Loading the month…" />
      ) : groups.length === 0 ? (
        <PageState kind="empty" title={rows.length === 0 ? `Nothing booked in ${monthLabel(month)}` : 'Nothing with that status'} detail={rows.length === 0 ? 'Book a performer to start the month.' : undefined} />
      ) : isMobile ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {groups.map((g) => (
            <div key={g.date}>
              <div className="n-eyebrow" style={{ marginBottom: 6 }}>{dateLabel(g.date)}{g.date === today ? ' · today' : ''}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {g.rows.map((p) => (
                  <RowCard key={p.id} onClick={() => open(p.id)} ariaLabel={`Open ${p.performer_name} on ${dateLabel(p.performance_date)}`}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ fontWeight: 600, flex: '1 1 auto' }}>{p.performer_name}</span>
                      <StatusChip status={p.status} />
                    </div>
                    <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginTop: 2 }}>
                      {timeRangeLabel(p.start_time, p.end_time, p.ends_next_day)}{venueId === 'all' ? ` · ${p.venue_name}` : ''} · {money(p.actual_cents ?? p.agreed_cents)}
                    </div>
                    <div style={{ marginTop: 4 }}><span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Closing: </span><ClosingManagerCell p={p} /></div>
                  </RowCard>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="n-card" style={{ overflowX: 'auto' }}>
          <table className="n-table" style={{ minWidth: 860 }}>
            <thead>
              <tr>
                <th>Date</th>
                <th>Times</th>
                <th>Performer</th>
                {venueId === 'all' && <th>Venue</th>}
                <th className="num">Agreed</th>
                <th className="num">Approved</th>
                <th>Status</th>
                <th>Closing manager</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) =>
                g.rows.map((p, i) => (
                  <tr key={p.id} onClick={() => open(p.id)} style={{ cursor: 'pointer' }}>
                    <td style={{ whiteSpace: 'nowrap', fontWeight: i === 0 ? 600 : 400, color: i === 0 ? 'var(--text)' : 'var(--muted)' }}>
                      {i === 0 ? dateLabel(g.date, { year: false }) : ''}{i === 0 && g.date === today ? <Badge tone="accent">today</Badge> : null}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>{timeRangeLabel(p.start_time, p.end_time, p.ends_next_day)}</td>
                    <td style={{ fontWeight: 500 }}>{p.performer_name}</td>
                    {venueId === 'all' && <td style={{ color: 'var(--text-soft)' }}>{p.venue_name}</td>}
                    <td className="num">{money(p.agreed_cents)}</td>
                    <td className="num">{p.actual_cents !== null ? money(p.actual_cents) : <span style={{ color: 'var(--muted)' }}>—</span>}</td>
                    <td><StatusChip status={p.status} /></td>
                    <td><ClosingManagerCell p={p} /></td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      )}

      {primaryOpen && (
        <BookingDialog
          meta={meta}
          performers={performers}
          defaultVenueId={venueId === 'all' ? meta.venues[0]?.id : venueId}
          defaultDate={monthOf(today) === month ? today : `${month}-01`}
          onSaved={() => refresh()}
          onClose={closePrimary}
        />
      )}
      {openId && (
        <PerformanceDialog
          performanceId={openId}
          meta={meta}
          performers={performers}
          onChanged={(next) => { replaceRow(next); invalidateEntMeta(); }}
          onDeleted={() => { open(null); refresh(); }}
          onClose={() => { open(null); refresh(); }}
        />
      )}
    </div>
  );
}
