'use client';

/**
 * The minimum-spend approvals log (`GET /reports/approvals`): every approve,
 * override and revoke at the caller's venues, newest first, with who did it
 * and why. Norm's own automatic revokes (the option changed after approval)
 * read "Norm (option changed)".
 */
import { useState } from 'react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import { instantLabel, money } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { Section, StatTile, TileRow } from '../enquiries/bits';
import { openFnPage } from '../enquiries/nav';
import { periodFor, type Period } from './dates';
import { FilterRow, PeriodPicker, ReportBody, useReport } from './ReportBits';
import type { ApprovalRow, Approvals } from './types';

const SHOWN = 100;

const KIND_BADGE: Record<ApprovalRow['kind'], { label: string; tone: 'ok' | 'warn' | 'neutral' }> = {
  approve: { label: 'Approved', tone: 'ok' },
  override: { label: 'Overridden', tone: 'warn' },
  revoke: { label: 'Revoked', tone: 'neutral' },
};

export default function ApprovalsReport({ meta, venue }: { meta: FnMeta; venue: string }) {
  const tz = meta.company.default_timezone;
  const currency = meta.company.currency;
  const [period, setPeriod] = useState<Period>(() => periodFor('90d', tz));
  const [kind, setKind] = useState('');
  const [shown, setShown] = useState(SHOWN);
  const state = useReport<Approvals>('/reports/approvals', {
    from: period.from, to: period.to, venue: venue === 'all' ? undefined : venue, kind: kind || undefined,
  });

  return (
    <div>
      <FilterRow>
        <PeriodPicker period={period} onChange={setPeriod} timeZone={tz} presets={['28d', '90d', '365d', 'custom']} />
        <select className="n-select" aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Approvals, overrides and revokes</option>
          <option value="approve">Approvals only</option>
          <option value="override">Overrides only</option>
          <option value="revoke">Revokes only</option>
        </select>
      </FilterRow>
      <ReportBody state={state} what="the approvals log">
        {(d) => (
          <>
            <TileRow min={170}>
              <StatTile label="Approved" value={d.totals.approve.count} sub={money(d.totals.approve.cents, currency, 0)} />
              <StatTile label="Overridden" value={d.totals.override.count} sub={money(d.totals.override.cents, currency, 0)} />
              <StatTile label="Revoked" value={d.totals.revoke.count} sub={`${d.totals.revoke.automatic} automatically, after a change`} />
            </TileRow>
            <div style={{ marginTop: 16 }}>
              <Section title="Log" meta={`${d.rows.length}`} padded={d.rows.length === 0}>
                {d.rows.length === 0 ? <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>Nothing in this period.</span> : (
                  <div style={{ overflowX: 'auto' }}>
                    <table className="n-table" aria-label="Approvals log" style={{ minWidth: 820 }}>
                      <thead>
                        <tr>
                          <th scope="col">When</th>
                          <th scope="col">What</th>
                          <th scope="col">Enquiry</th>
                          <th scope="col">Venue and area</th>
                          <th scope="col" className="num">Amount</th>
                          <th scope="col">By</th>
                          <th scope="col">Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.rows.slice(0, shown).map((r) => {
                          const b = KIND_BADGE[r.kind] ?? { label: r.kind, tone: 'neutral' as const };
                          return (
                            <tr key={r.id} onClick={() => r.enquiry_id && openFnPage('enquiries', { enquiry: r.enquiry_id })} style={{ cursor: r.enquiry_id ? 'pointer' : undefined }}>
                              <td style={{ whiteSpace: 'nowrap', color: 'var(--text-soft)' }}>{instantLabel(r.occurred_at, tz)}</td>
                              <td><Badge tone={b.tone}>{b.label}</Badge></td>
                              <td>
                                <div style={{ fontWeight: 500 }}>{r.enquiry_subject || r.option_label || 'Minimum spend'}</div>
                                {r.contact && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{r.contact}</div>}
                              </td>
                              <td style={{ color: 'var(--text-soft)' }}>
                                {r.venue_name}
                                {r.area_name && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{r.area_name}</div>}
                              </td>
                              <td className="num">{r.amount_cents !== null ? money(r.amount_cents, currency, 0) : '—'}</td>
                              <td style={{ color: 'var(--text-soft)' }}>{r.actor || '—'}</td>
                              <td style={{ color: 'var(--text-soft)', maxWidth: 260, overflowWrap: 'anywhere' }}>{r.reason || '—'}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    {d.rows.length > shown && (
                      <div style={{ padding: '10px 16px' }}>
                        <Button size="sm" variant="quiet" onClick={() => setShown((n) => n + SHOWN)}>Show {Math.min(SHOWN, d.rows.length - shown)} more</Button>
                      </div>
                    )}
                  </div>
                )}
              </Section>
            </div>
          </>
        )}
      </ReportBody>
    </div>
  );
}
