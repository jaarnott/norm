'use client';

/**
 * Upcoming sales: the live events from today over a chosen window — their
 * expected spend by week (`GET /reports/sales`) and the events themselves
 * with totals (`GET /reports/upcoming-events`), both for the same days.
 * Expected spend per event is the API's rule (max of the booked minimum
 * spend and food + bar tab).
 */
import { useState } from 'react';
import Chart, { DEFAULT_COLORS } from '../../display/Chart';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import { dateLabel, money, timeRangeLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { Section, StatTile, TileRow } from '../enquiries/bits';
import { eventStatusBadge, moneyAuto } from '../enquiries/labels';
import { openFnPage } from '../enquiries/nav';
import { FilterRow, useReport } from './ReportBits';
import type { Sales, UpcomingEvents } from './types';

const WINDOWS: { days: number; label: string }[] = [
  { days: 7, label: 'Next 7 days' },
  { days: 14, label: 'Next 14 days' },
  { days: 28, label: 'Next 4 weeks' },
  { days: 56, label: 'Next 8 weeks' },
  { days: 91, label: 'Next 3 months' },
  { days: 182, label: 'Next 6 months' },
  { days: 365, label: 'Next 12 months' },
];

export default function SalesReport({ meta, venue }: { meta: FnMeta; venue: string }) {
  const currency = meta.company.currency;
  const [days, setDays] = useState(28);
  const scope = { venue: venue === 'all' ? undefined : venue };
  const sales = useReport<Sales>('/reports/sales', { days, ...scope });
  const events = useReport<UpcomingEvents>('/reports/upcoming-events', { days, ...scope });
  const failed = (events.error && !events.data) || (sales.error && !sales.data);
  const busy = events.loading || sales.loading;

  return (
    <div>
      <FilterRow>
        <select className="n-select" aria-label="Window" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {WINDOWS.map((w) => <option key={w.days} value={w.days}>{w.label}</option>)}
        </select>
      </FilterRow>
      {failed ? (
        <PageState
          kind="error"
          title="Couldn’t load upcoming sales"
          detail={events.error || sales.error}
          action={<Button size="sm" onClick={() => { events.retry(); sales.retry(); }}>Try again</Button>}
        />
      ) : !events.data || !sales.data ? (
        <PageState kind="loading" title="Loading upcoming sales…" />
      ) : (
        // The last answer stays on screen, dimmed, while a new window loads.
        <div style={{ opacity: busy ? 0.55 : 1, transition: 'opacity 0.15s' }} aria-busy={busy || undefined}>
          <TileRow min={170}>
            <StatTile label="Expected sales" value={money(events.data.totals.expected_spend_cents, currency, 0)} sub={`From ${dateLabel(events.data.from, { year: false })}`} />
            <StatTile label="Events" value={events.data.totals.events} />
            <StatTile label="Guests" value={events.data.totals.guests} />
          </TileRow>
          <div style={{ marginTop: 16 }}>
            <Chart
              data={{ rows: sales.data.weeks.map((w) => ({ week: `w/c ${dateLabel(w.week_start, { weekday: false, year: false })}`, sales: w.cents / 100 })) }}
              props={{
                chart_type: 'bar',
                title: `Expected sales by week, ${dateLabel(sales.data.from, { weekday: false, year: false })} – ${dateLabel(sales.data.to, { weekday: false })}`,
                x_axis: { key: 'week', label: 'Week' },
                // The chart's formatter takes a format object; the spec types it as a string.
                y_axis: { key: 'sales', label: 'Expected sales', format: { type: 'currency', decimals: 0 } as unknown as string },
                series: [{ key: 'sales', label: 'Expected sales', color: DEFAULT_COLORS[0] }],
              }}
              hideAddToReport
              height={260}
            />
          </div>
          <div style={{ marginTop: 16 }}>
            <EventsTable events={events.data} currency={currency} />
          </div>
        </div>
      )}
    </div>
  );
}

const SHOWN = 100;

function EventsTable({ events: ev, currency }: { events: UpcomingEvents; currency: string }) {
  const [shown, setShown] = useState(SHOWN);
  return (
    <Section title="Events" meta={`${ev.rows.length}`} padded={ev.rows.length === 0}>
      {ev.rows.length === 0 ? <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>No events booked in this window.</span> : (
        <div style={{ overflowX: 'auto' }}>
          <table className="n-table" aria-label="Upcoming events" style={{ minWidth: 760 }}>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Event</th>
                <th scope="col">Venue</th>
                <th scope="col" className="num">Guests</th>
                <th scope="col">Status</th>
                <th scope="col" className="num">Expected</th>
              </tr>
            </thead>
            <tbody>
              {ev.rows.slice(0, shown).map((r) => {
                const st = eventStatusBadge(r.status);
                return (
                  <tr key={r.id} onClick={() => openFnPage('events', { event: r.id })} style={{ cursor: 'pointer' }}>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <div>{dateLabel(r.event_date, { year: false })}</div>
                      {r.start_time && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{timeRangeLabel(r.start_time, r.end_time, r.ends_next_day)}</div>}
                    </td>
                    <td>
                      <button type="button" onClick={(e) => { e.stopPropagation(); openFnPage('events', { event: r.id }); }}
                        style={{ padding: 0, border: 'none', background: 'none', font: 'inherit', fontWeight: 500, color: 'var(--text)', textAlign: 'left', cursor: 'pointer' }}>
                        {r.name || 'Event'}
                      </button>
                      {r.contact_name && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{r.contact_name}</div>}
                    </td>
                    <td style={{ color: 'var(--text-soft)' }}>
                      {r.venue_name}
                      {r.area_name && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{r.area_name}</div>}
                    </td>
                    <td className="num">{r.guests ?? '—'}</td>
                    <td>
                      <Badge tone={st.tone}>{st.label}</Badge>
                      {r.edited_since_confirmed && <div style={{ marginTop: 2 }}><Badge tone="accent">Edited since confirmed</Badge></div>}
                    </td>
                    <td className="num">{moneyAuto(r.expected_spend_cents, currency)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {ev.rows.length > shown && (
            <div style={{ padding: '10px 16px' }}>
              <Button size="sm" variant="quiet" onClick={() => setShown((n) => n + SHOWN)}>Show {Math.min(SHOWN, ev.rows.length - shown)} more</Button>
            </div>
          )}
        </div>
      )}
    </Section>
  );
}
