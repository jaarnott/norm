'use client';

/**
 * Where enquiries came from (`GET /reports/lead-sources`): by channel, by
 * campaign (UTM source / medium / campaign, or Google when a click id came
 * with the lead), and the web-form leads — with how many became bookings and
 * what those bookings are worth.
 */
import { useState } from 'react';
import { dateLabel, money } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { Section } from '../enquiries/bits';
import { pctLabel, sourceLabel } from '../enquiries/labels';
import { periodFor, type Period } from './dates';
import { FilterRow, PeriodPicker, ReportBody, useReport } from './ReportBits';
import type { LeadRow, LeadSources } from './types';

export default function LeadSourcesReport({ meta, venue }: { meta: FnMeta; venue: string }) {
  const tz = meta.company.default_timezone;
  const currency = meta.company.currency;
  const [period, setPeriod] = useState<Period>(() => periodFor('90d', tz));
  const state = useReport<LeadSources>('/reports/lead-sources', { from: period.from, to: period.to, venue: venue === 'all' ? undefined : venue });

  const outcomeCells = (r: LeadRow) => (
    <>
      <td className="num">{r.enquiries}</td>
      <td className="num">{r.confirmed}</td>
      <td className="num">{r.lost}</td>
      <td className="num" style={{ fontWeight: 600 }}>{pctLabel(r.rate_pct)}</td>
      <td className="num">{r.confirmed_value_cents ? money(r.confirmed_value_cents, currency, 0) : '—'}</td>
    </>
  );
  const outcomeHeads = (
    <>
      <th scope="col" className="num">Enquiries</th>
      <th scope="col" className="num">Confirmed</th>
      <th scope="col" className="num">Lost</th>
      <th scope="col" className="num">Conversion</th>
      <th scope="col" className="num">Booked value</th>
    </>
  );

  return (
    <div>
      <FilterRow>
        <PeriodPicker period={period} onChange={setPeriod} timeZone={tz} presets={['28d', '90d', '365d', 'custom']} />
      </FilterRow>
      <ReportBody state={state} what="lead sources">
        {(d) => (
          <>
            <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
              <strong style={{ fontWeight: 600, color: 'var(--text)' }}>{d.enquiries}</strong> {d.enquiries === 1 ? 'enquiry' : 'enquiries'} received {dateLabel(d.from, { weekday: false })} – {dateLabel(d.to, { weekday: false })}. Spam form submissions are left out.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 12 }}>
              <Section title="By channel" padded={false}>
                <div style={{ overflowX: 'auto' }}>
                  <table className="n-table" aria-label="Enquiries by channel" style={{ minWidth: 620 }}>
                    <thead><tr><th scope="col">Channel</th>{outcomeHeads}</tr></thead>
                    <tbody>
                      {d.by_channel.map((r) => <tr key={r.source}><td>{sourceLabel(r.source)}</td>{outcomeCells(r)}</tr>)}
                      {d.by_channel.length === 0 && <tr><td colSpan={6} style={{ color: 'var(--muted)' }}>No enquiries in this period.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </Section>
              <Section title="By campaign" meta="UTM source / medium / campaign; Google when the lead carried a click id" padded={false}>
                <div style={{ overflowX: 'auto' }}>
                  <table className="n-table" aria-label="Enquiries by campaign" style={{ minWidth: 760 }}>
                    <thead><tr><th scope="col">Source</th><th scope="col">Medium</th><th scope="col">Campaign</th>{outcomeHeads}</tr></thead>
                    <tbody>
                      {d.by_campaign.map((r, i) => (
                        <tr key={`${r.source}|${r.medium}|${r.campaign}|${i}`}>
                          <td>{sourceLabel(r.source)}</td>
                          <td style={{ color: 'var(--text-soft)' }}>{r.medium}</td>
                          <td style={{ color: 'var(--text-soft)', overflowWrap: 'anywhere' }}>{r.campaign}</td>
                          {outcomeCells(r)}
                        </tr>
                      ))}
                      {d.by_campaign.length === 0 && <tr><td colSpan={8} style={{ color: 'var(--muted)' }}>No enquiries in this period.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </Section>
              <Section title="Web-form leads" meta="Submissions, and how many became enquiries" padded={false}>
                <div style={{ overflowX: 'auto' }}>
                  <table className="n-table" aria-label="Web-form leads" style={{ minWidth: 620 }}>
                    <thead>
                      <tr>
                        <th scope="col">Source</th>
                        <th scope="col">Medium</th>
                        <th scope="col">Campaign</th>
                        <th scope="col" className="num">Leads</th>
                        <th scope="col" className="num">Enquiries</th>
                        <th scope="col" className="num">With a click id</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.form_leads.map((r, i) => (
                        <tr key={`${r.source}|${r.medium}|${r.campaign}|${i}`}>
                          <td>{sourceLabel(r.source)}</td>
                          <td style={{ color: 'var(--text-soft)' }}>{r.medium}</td>
                          <td style={{ color: 'var(--text-soft)', overflowWrap: 'anywhere' }}>{r.campaign}</td>
                          <td className="num">{r.leads}</td>
                          <td className="num">{r.enquiries}</td>
                          <td className="num">{r.with_click_id}</td>
                        </tr>
                      ))}
                      {d.form_leads.length === 0 && <tr><td colSpan={6} style={{ color: 'var(--muted)' }}>No web-form leads in this period.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </Section>
            </div>
          </>
        )}
      </ReportBody>
    </div>
  );
}
