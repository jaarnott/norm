'use client';

/**
 * The month calendar: a Monday-first grid from `GET /events/calendar`, each
 * day's events as chips coloured by status — with the status icon and a
 * legend, so colour never carries the meaning alone — the day's expected
 * spend, and "+N more" for a busy day. Phones get a compact grid (day,
 * count, total) and the chosen day's events listed underneath.
 */
import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { TriangleAlert, X } from 'lucide-react';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { dateLabel, money, timeRangeLabel } from '../shared/format';
import { EDITED_LABEL, STATUS_LABEL, STATUS_ORDER, compactMoney, dayOfMonth, splitDay } from './logic';
import { EditedFlag, STATUS_COLOURS, STATUS_ICON, StatusBadge } from './ui';
import type { CalendarDay, CalendarMonth, EventRow } from './types';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const SLOTS = 3;

export function CalendarLegend() {
  return (
    <ul aria-label="Status colours" style={{ listStyle: 'none', margin: '0 0 10px', padding: 0, display: 'flex', flexWrap: 'wrap', gap: '6px 14px' }}>
      {STATUS_ORDER.map((s) => (
        <li key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-xs)', color: 'var(--text-soft)' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18, borderRadius: 4, background: STATUS_COLOURS[s].bg, color: STATUS_COLOURS[s].fg }}>
            <Icon icon={STATUS_ICON[s]} size={12} />
          </span>
          {STATUS_LABEL[s]}
        </li>
      ))}
      <li style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-xs)', color: 'var(--text-soft)' }}>
        <Icon icon={TriangleAlert} size={12} style={{ color: 'var(--accent)' }} />
        {EDITED_LABEL}
      </li>
    </ul>
  );
}

function EventChip({ row, currency, onOpen, wide = false }: { row: EventRow; currency: string; onOpen: (id: string) => void; wide?: boolean }) {
  const c = STATUS_COLOURS[row.status];
  const spend = row.expected_spend_cents ? money(row.expected_spend_cents, currency, 0) : '';
  const label = `${row.name || 'Event'}${spend ? `, ${spend}` : ''} — ${STATUS_LABEL[row.status]}${row.edited_since_confirmed ? `, ${EDITED_LABEL.toLowerCase()}` : ''}`;
  return (
    <button
      type="button"
      onClick={() => onOpen(row.id)}
      title={`${label}${row.start_time ? ` · ${timeRangeLabel(row.start_time, row.end_time, row.ends_next_day)}` : ''}`}
      aria-label={label}
      style={{
        display: 'flex', alignItems: 'center', gap: 4, width: '100%', minWidth: 0, padding: wide ? '6px 8px' : '2px 5px',
        border: 'none', borderRadius: 4, background: c.bg, color: c.fg, font: 'inherit',
        fontSize: wide ? 'var(--fs-sm)' : 'var(--fs-2xs)', fontWeight: 500, textAlign: 'left', cursor: 'pointer',
        textDecoration: row.status === 'cancelled' ? 'line-through' : undefined,
      }}
    >
      <Icon icon={STATUS_ICON[row.status]} size={wide ? 14 : 11} />
      {row.edited_since_confirmed && <Icon icon={TriangleAlert} size={wide ? 14 : 11} style={{ color: 'var(--accent)' }} />}
      <span style={{ minWidth: 0, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text)' }}>{row.name || 'Event'}</span>
      {spend && <span style={{ flex: '0 0 auto', fontVariantNumeric: 'tabular-nums', color: 'var(--text-soft)' }}>{spend}</span>}
    </button>
  );
}

/** The "+N more" popover: every event that day. */
function DayPopover({ day, currency, onOpen, onClose, alignRight }: {
  day: CalendarDay;
  currency: string;
  onOpen: (id: string) => void;
  onClose: () => void;
  alignRight: boolean;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    boxRef.current?.focus();
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);
  return (
    <div
      ref={boxRef}
      role="dialog"
      aria-label={`Events on ${dateLabel(day.date)}`}
      tabIndex={-1}
      className="n-card"
      style={{
        position: 'absolute', top: 4, [alignRight ? 'right' : 'left']: 4, zIndex: 25, width: 280, maxWidth: '80vw',
        padding: 10, boxShadow: '0 8px 24px rgba(26, 26, 26, 0.14)', outline: 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600 }}>{dateLabel(day.date, { year: false })}</span>
        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginLeft: 'auto' }}>{money(day.expected_spend_cents, currency, 0)}</span>
        <IconButton icon={X} label="Close" iconSize={16} onClick={onClose} style={{ width: 26, height: 26 }} />
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 320, overflowY: 'auto' }}>
        {day.events.map((r) => <EventChip key={r.id} row={r} currency={currency} onOpen={onOpen} wide />)}
      </div>
    </div>
  );
}

const cellBase: CSSProperties = { position: 'relative', minWidth: 0, background: 'var(--bg)' };

export default function EventsCalendar({ month, loading, error, today, currency, onOpen }: {
  month: CalendarMonth | undefined;
  loading: boolean;
  error: string | null;
  today: string;
  currency: string;
  onOpen: (id: string) => void;
}) {
  const { isMobile } = useBreakpoint();
  const [moreFor, setMoreFor] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);

  if (error) return <PageState kind="error" title="Couldn’t load the calendar" detail={error} />;
  if (loading || !month) return <PageState kind="loading" title="Loading the calendar…" />;

  const weeks: CalendarDay[][] = [];
  for (let i = 0; i < month.days.length; i += 7) weeks.push(month.days.slice(i, i + 7));

  if (isMobile) {
    // The chosen day: today when it is in this month, else the first busy day.
    const inMonth = month.days.filter((d) => d.in_month);
    const chosen =
      month.days.find((d) => d.date === picked) ??
      inMonth.find((d) => d.date === today) ??
      inMonth.find((d) => d.events.length) ??
      inMonth[0];
    return (
      <div>
        <div role="grid" aria-label="Month" style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 2 }}>
          {WEEKDAYS.map((w) => (
            <div key={w} role="columnheader" style={{ textAlign: 'center', fontSize: 'var(--fs-2xs)', fontWeight: 600, color: 'var(--muted)', padding: '4px 0' }}>{w.slice(0, 2)}</div>
          ))}
          {month.days.map((d) => {
            const isChosen = chosen?.date === d.date;
            const isToday = d.date === today;
            const n = d.events.length;
            return (
              <button
                key={d.date}
                type="button"
                role="gridcell"
                aria-selected={isChosen}
                aria-label={`${dateLabel(d.date)}: ${n} ${n === 1 ? 'event' : 'events'}${n ? `, ${money(d.expected_spend_cents, currency, 0)}` : ''}`}
                onClick={() => setPicked(d.date)}
                style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, minWidth: 0, minHeight: 58, padding: '4px 1px',
                  border: `1px solid ${isChosen ? 'var(--brand-soft)' : 'var(--line)'}`, borderRadius: 6, font: 'inherit', cursor: 'pointer',
                  background: isChosen ? 'var(--accent-soft)' : d.in_month ? 'var(--bg)' : 'var(--surface)',
                  color: d.in_month ? 'var(--text)' : 'var(--muted)',
                }}
              >
                <span style={{
                  fontSize: 'var(--fs-sm)', fontWeight: isToday ? 700 : 500, lineHeight: '20px', minWidth: 22, borderRadius: 999,
                  background: isToday ? 'var(--primary)' : undefined, color: isToday ? 'var(--on-primary)' : undefined,
                }}>{dayOfMonth(d.date)}</span>
                {n > 0 && <span className="n-badge" style={{ padding: '0 6px', lineHeight: '15px' }}>{n}</span>}
                {n > 0 && d.expected_spend_cents > 0 && (
                  <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-soft)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                    {compactMoney(d.expected_spend_cents, currency)}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        {chosen && (
          <section aria-label={`Events on ${dateLabel(chosen.date)}`} style={{ marginTop: 14 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600 }}>{dateLabel(chosen.date)}</span>
              {chosen.events.length > 0 && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{money(chosen.expected_spend_cents, currency, 0)} expected</span>}
            </div>
            {chosen.events.length ? (
              <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {chosen.events.map((r) => (
                  <li key={r.id}>
                    <button type="button" onClick={() => onOpen(r.id)} style={{
                      display: 'block', width: '100%', padding: '10px 12px', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)',
                      background: 'var(--bg)', font: 'inherit', color: 'var(--text)', textAlign: 'left', cursor: 'pointer',
                    }}>
                      <span style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                        <span style={{ fontWeight: 600, minWidth: 0, overflowWrap: 'anywhere' }}>{r.name || 'Event'}</span>
                        <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{r.expected_spend_cents ? money(r.expected_spend_cents, currency, 0) : '—'}</span>
                      </span>
                      <span style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                        <StatusBadge status={r.status} />
                        {r.edited_since_confirmed && <EditedFlag compact />}
                        {r.start_time && <span>{timeRangeLabel(r.start_time, r.end_time, r.ends_next_day)}</span>}
                        <span>{[r.venue_name, r.guests ? `${r.guests} guests` : null].filter(Boolean).join(' · ')}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No events this day.</div>
            )}
          </section>
        )}
      </div>
    );
  }

  return (
    <div role="grid" aria-label="Month" style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', overflow: 'visible', background: 'var(--line)' }}>
      <div role="row" style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 1 }}>
        {WEEKDAYS.map((w, i) => (
          <div key={w} role="columnheader" style={{
            padding: '7px 8px', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)', background: 'var(--surface-alt)',
            borderTopLeftRadius: i === 0 ? 'var(--radius-lg)' : undefined, borderTopRightRadius: i === 6 ? 'var(--radius-lg)' : undefined,
          }}>{w}</div>
        ))}
      </div>
      {weeks.map((week, wi) => (
        <div key={week[0].date} role="row" style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 1, marginTop: 1 }}>
          {week.map((d, di) => {
            const { visible, more } = splitDay(d.events, SLOTS);
            const isToday = d.date === today;
            const last = wi === weeks.length - 1;
            return (
              <div key={d.date} role="gridcell" aria-label={dateLabel(d.date)} style={{
                ...cellBase, minHeight: 112, padding: 6, background: d.in_month ? 'var(--bg)' : 'var(--surface)',
                borderBottomLeftRadius: last && di === 0 ? 'var(--radius-lg)' : undefined,
                borderBottomRightRadius: last && di === 6 ? 'var(--radius-lg)' : undefined,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4, marginBottom: 4 }}>
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', justifyContent: 'center', minWidth: 22, height: 22, padding: '0 4px', borderRadius: 999,
                    fontSize: 'var(--fs-xs)', fontWeight: isToday ? 700 : 500,
                    background: isToday ? 'var(--primary)' : undefined,
                    color: isToday ? 'var(--on-primary)' : d.in_month ? 'var(--text-soft)' : 'var(--muted)',
                  }}>
                    {dayOfMonth(d.date)}
                  </span>
                  {d.expected_spend_cents > 0 && (
                    <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--muted)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {money(d.expected_spend_cents, currency, 0)}
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  {visible.map((r) => <EventChip key={r.id} row={r} currency={currency} onOpen={onOpen} />)}
                  {more > 0 && (
                    <button
                      type="button"
                      aria-haspopup="dialog"
                      aria-expanded={moreFor === d.date}
                      onClick={() => setMoreFor(d.date)}
                      style={{ border: 'none', background: 'none', padding: '1px 5px', textAlign: 'left', font: 'inherit', fontSize: 'var(--fs-2xs)', fontWeight: 600, color: 'var(--accent)', cursor: 'pointer' }}
                    >
                      +{more} more
                    </button>
                  )}
                </div>
                {moreFor === d.date && (
                  <DayPopover day={d} currency={currency} onOpen={onOpen} onClose={() => setMoreFor(null)} alignRight={di >= 4} />
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
