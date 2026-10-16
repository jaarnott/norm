'use client';

/**
 * Conversion: of the enquiries received in a window that ended `lag` weeks
 * ago, how many became bookings — confirmed ÷ (confirmed + lost); open ones
 * are listed but not in the rate (`GET /reports/conversion`). Below it, the
 * enquiries counted (the report's own rows), each opening its enquiry.
 */
import { useState } from 'react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import type { FnMeta } from '../shared/meta';
import { Section, StatTile, TileRow } from '../enquiries/bits';
import { dayOf, pctLabel, stageLabel, stageTone } from '../enquiries/labels';
import { openFnPage } from '../enquiries/nav';
import { FilterRow, ReportBody, useReport } from './ReportBits';
import type { Conversion } from './types';

const WINDOWS = [2, 4, 6, 8, 12, 26, 52];
const LAGS = [0, 1, 2, 3, 4, 6, 8];
const SHOWN = 50;

export default function ConversionReport({ meta, venue }: { meta: FnMeta; venue: string }) {
  const tz = meta.company.default_timezone;
  const [weeks, setWeeks] = useState(4);
  const [lag, setLag] = useState(4);
  const state = useReport<Conversion>('/reports/conversion', { weeks, lag, venue: venue === 'all' ? undefined : venue });

  return (
    <div>
      <FilterRow>
        <select className="n-select" aria-label="Window" value={weeks} onChange={(e) => setWeeks(Number(e.target.value))}>
          {WINDOWS.map((w) => <option key={w} value={w}>{w} weeks of enquiries</option>)}
        </select>
        <select className="n-select" aria-label="Lag" value={lag} onChange={(e) => setLag(Number(e.target.value))}>
          {LAGS.map((l) => <option key={l} value={l}>{l === 0 ? 'No lag (up to today)' : `${l}-week lag`}</option>)}
        </select>
      </FilterRow>
      <ReportBody state={state} what="conversion">
        {(c) => (
          <>
            <p style={{ margin: '0 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
              Enquiries received {dayOf(c.cohort_from, tz)} – {dayOf(c.cohort_to, tz)}
              {c.lag_weeks ? `, counted ${c.lag_weeks} ${c.lag_weeks === 1 ? 'week' : 'weeks'} later so most have had time to close` : ''}.
              A cancelled booking counts as lost.
            </p>
            <TileRow min={150}>
              <StatTile label="Conversion" value={pctLabel(c.rate_pct)} sub="Confirmed of closed" />
              <StatTile label="Enquiries" value={c.enquiries} />
              <StatTile label="Confirmed" value={c.confirmed} />
              <StatTile label="Lost" value={c.lost} />
              <StatTile label="Still open" value={c.open} sub="Not in the rate" />
            </TileRow>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
              <Section title="By venue" padded={false}>
                <div style={{ overflowX: 'auto' }}>
                  <table className="n-table" aria-label="Conversion by venue">
                    <thead>
                      <tr>
                        <th scope="col">Venue</th>
                        <th scope="col" className="num">Enquiries</th>
                        <th scope="col" className="num">Confirmed</th>
                        <th scope="col" className="num">Lost</th>
                        <th scope="col" className="num">Open</th>
                        <th scope="col" className="num">Conversion</th>
                      </tr>
                    </thead>
                    <tbody>
                      {c.by_venue.map((v) => (
                        <tr key={v.venue_id ?? 'group'}>
                          <td>{v.venue_name ?? 'Group-wide'}</td>
                          <td className="num">{v.enquiries}</td>
                          <td className="num">{v.confirmed}</td>
                          <td className="num">{v.lost}</td>
                          <td className="num">{v.open}</td>
                          <td className="num" style={{ fontWeight: 600 }}>{pctLabel(v.rate_pct)}</td>
                        </tr>
                      ))}
                      {c.by_venue.length === 0 && <tr><td colSpan={6} style={{ color: 'var(--muted)' }}>No enquiries in this window.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </Section>
              <CohortTable key={`${c.cohort_from}|${venue}`} conversion={c} tz={tz} />
            </div>
          </>
        )}
      </ReportBody>
    </div>
  );
}

/** The enquiries the figures count — the report's own `rows`, so the table
 *  and the numbers above it are the same set. */
function CohortTable({ conversion: c, tz }: { conversion: Conversion; tz: string }) {
  const [shown, setShown] = useState(SHOWN);
  const rows = c.rows ?? [];
  return (
    <Section title="Enquiries counted" meta={`${rows.length}`} padded={rows.length === 0}>
      {rows.length === 0 ? <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>No enquiries in this window.</span> : (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table className="n-table" aria-label="Enquiries counted" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th scope="col">Received</th>
                  <th scope="col">Enquiry</th>
                  <th scope="col">Venue</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">In the rate</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, shown).map((r) => (
                  <tr key={r.id} onClick={() => openFnPage('enquiries', { enquiry: r.id })} style={{ cursor: 'pointer' }}>
                    <td style={{ whiteSpace: 'nowrap', color: 'var(--text-soft)' }}>{dayOf(r.received, tz)}</td>
                    <td>
                      <button type="button" onClick={(e) => { e.stopPropagation(); openFnPage('enquiries', { enquiry: r.id }); }}
                        style={{ padding: 0, border: 'none', background: 'none', font: 'inherit', fontWeight: 500, color: 'var(--text)', textAlign: 'left', cursor: 'pointer' }}>
                        {r.subject || r.contact || 'No subject'}
                      </button>
                      {r.subject && r.contact && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{r.contact}</div>}
                    </td>
                    <td style={{ color: 'var(--text-soft)' }}>{r.venue || 'Group-wide'}</td>
                    <td><Badge tone={stageTone(r.stage)}>{stageLabel(r.stage)}</Badge></td>
                    <td style={{ color: r.counted ? 'var(--text)' : 'var(--muted)' }}>{r.counted ? 'Yes' : 'No — still open'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > shown && (
            <div style={{ padding: '10px 16px' }}>
              <Button size="sm" variant="quiet" onClick={() => setShown((n) => n + SHOWN)}>Show {Math.min(SHOWN, rows.length - shown)} more</Button>
            </div>
          )}
        </>
      )}
    </Section>
  );
}
