'use client';

/**
 * One performance: the booking, the performer's confirmation, the
 * manager's approval with the pay Norm worked out (rate × real venue-local
 * hours, DST-aware — the API's figure, never the browser's), did-not-
 * perform, reset, "use the performer's times" on a mismatch, the closing
 * manager Loaded found, the emails sent about it, "open as performer", and
 * the field-level audit trail. The closing manager's email lands here via
 * `?page=entertainers&tab=roster&performance=<id>`.
 */
import { useCallback, useEffect, useId, useState } from 'react';
import type { ReactNode } from 'react';
import { ExternalLink, Pencil, RefreshCw, Send, TriangleAlert, UserRound } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import Dialog, { ConfirmDialog } from '../../../ui/Dialog';
import Icon from '../../../ui/Icon';
import PageState from '../../../ui/PageState';
import { entGet, entSend, errorText, isStale } from '../api';
import { dateLabel, money, timeRangeLabel, when } from '../format';
import { CLOSING_SOURCE_LABEL, approvalDefaults, emailKindLabel, hoursLabel } from '../logic';
import { CheckChip, CopyButton, DialogActions, EmailStatusBadge, Fact, FactGrid, Field, FieldGrid, Hint, LaneBadge, Notice, Section, StatusChip, Toggle, WriteError, inputStyle } from '../ui';
import { invalidateEntMeta } from '../useMeta';
import { useOpenTab } from '../../shared/useWorkspaceTab';
import BookingDialog from './BookingDialog';
import type { EntMeta, Performance, PerformanceDetail, Performer } from '../types';

const SOURCE_LABEL: Record<string, string> = { manager: 'manager', performer: 'performer', system: 'Norm', import: 'Orbit import' };

const FIELD_LABEL: Record<string, string> = {
  actual_start_time: 'Manager start',
  actual_end_time: 'Manager end',
  actual_ends_next_day: 'Manager end next day',
  actual_cents: 'Approved amount',
  performance_status: 'Status',
  dnp_reason: 'Did-not-perform reason',
  performer_actual_start_time: 'Performer start',
  performer_actual_end_time: 'Performer end',
  performer_dnp: 'Performer did not perform',
  performer_dnp_reason: 'Performer reason',
  payment_status: 'Payment',
  paid_reference: 'Payment reference',
  start_time: 'Booked start',
  end_time: 'Booked end',
  rate_cents: 'Rate',
  performance_date: 'Date',
  performer_id: 'Performer',
  notes: 'Notes',
};

function changeValue(field: string, value: string | null): string {
  if (value === null || value === '') return '—';
  if (field.endsWith('_cents')) {
    const n = Number(value);
    return Number.isFinite(n) ? money(n) : value;
  }
  return value;
}

export default function PerformanceDialog({ performanceId, meta, performers, onChanged, onDeleted, onClose }: {
  performanceId: string;
  meta: EntMeta;
  performers: Performer[];
  onChanged: (performance: Performance) => void;
  onDeleted?: (id: string) => void;
  onClose: () => void;
}) {
  // "Open in Payments": in place on the Entertainers page, else opens it.
  const openTab = useOpenTab('entertainers');
  const base = useId();
  const id = (k: string) => `${base}-${k}`;
  const [detail, setDetail] = useState<PerformanceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [nextDay, setNextDay] = useState(false);
  const [note, setNote] = useState('');
  const [dnpOpen, setDnpOpen] = useState(false);
  const [dnpReason, setDnpReason] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [writeError, setWriteError] = useState<{ text: string; stale: boolean } | null>(null);
  const [notice, setNotice] = useState<ReactNode>(null);
  const [portalUrl, setPortalUrl] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);

  useEffect(() => {
    let alive = true;
    entGet<PerformanceDetail>(`/performances/${performanceId}`).then(
      (d) => {
        if (!alive) return;
        setDetail(d);
        setError(null);
        const def = approvalDefaults(d.performance);
        setStart(def.start);
        setEnd(def.end);
        setNextDay(def.nextDay);
        setDnpReason(d.performance.dnp_reason ?? '');
      },
      (e: unknown) => { if (alive) setError(errorText(e)); },
    );
    return () => { alive = false; };
  }, [performanceId, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const p = detail?.performance;
  const venue = p ? meta.venues.find((v) => v.id === p.venue_id) : undefined;
  const tz = venue?.timezone ?? undefined;

  const apply = (next: Performance) => {
    setDetail((d) => (d ? { ...d, performance: next } : d));
    onChanged(next);
    invalidateEntMeta();
    reload();
  };

  const run = async (key: string, path: string, body?: unknown, after?: (res: unknown) => void) => {
    if (!p) return;
    setBusy(key);
    setWriteError(null);
    setNotice(null);
    try {
      const res = await entSend<unknown>('POST', `/performances/${p.id}${path}`, body);
      if (after) after(res);
      else apply(((res as { performance?: Performance }).performance ?? res) as Performance);
    } catch (e) {
      setWriteError({ text: errorText(e), stale: isStale(e) });
    }
    setBusy(null);
  };

  const approve = () => run('approve', '/approve', { actual_start_time: start, actual_end_time: end, actual_ends_next_day: nextDay, note: note.trim() || undefined, version: p?.version });
  const dnp = () => {
    if (!dnpReason.trim()) return setWriteError({ text: 'Say why they didn’t perform.', stale: false });
    return run('dnp', '/did-not-perform', { reason: dnpReason.trim(), version: p?.version });
  };

  const title = p ? `${p.performer_name} · ${dateLabel(p.performance_date, { year: false })}` : 'Performance';
  const paid = p?.payment_status === 'paid';
  const approved = p?.performance_status === 'approved';
  const cm = p?.closing_manager ?? null;

  return (
    <Dialog title={title} width={820} onClose={() => { if (!busy) onClose(); }} closeButton>
      {error ? (
        <PageState kind="error" title="Couldn’t load this performance" detail={error} action={<Button size="sm" onClick={reload}>Try again</Button>} />
      ) : !detail || !p ? (
        <PageState kind="loading" title="Loading…" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: -6 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <StatusChip status={p.status} />
            <LaneBadge lane={p.lane} />
            {p.series_id && <Badge tone="neutral">Repeats weekly</Badge>}
            <span style={{ flex: 1 }} />
            {!paid && <Button size="sm" icon={Pencil} onClick={() => setEditing(true)} disabled={!!busy}>Edit booking</Button>}
          </div>

          <Section title="Booking">
            <FactGrid min={150}>
              <Fact label="Venue">{p.venue_name}</Fact>
              <Fact label="Date">{dateLabel(p.performance_date)}</Fact>
              <Fact label="Booked times">{timeRangeLabel(p.start_time, p.end_time, p.ends_next_day)}</Fact>
              <Fact label="Rate">{money(p.rate_cents)} / hour</Fact>
              <Fact label="Agreed">{money(p.agreed_cents)} <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>({hoursLabel(p.minutes.booked)})</span></Fact>
              {p.notes && <Fact label="Notes" wide>{p.notes}</Fact>}
            </FactGrid>
          </Section>

          {p.status === 'mismatch' && (
            <Notice tone="error" action={<Button size="sm" onClick={() => run('use', '/use-performer-times', { version: p.version })} disabled={!!busy}>{busy === 'use' ? 'Applying…' : 'Use the performer’s times'}</Button>}>
              The performer&rsquo;s times differ from the approved times by more than {meta.settings.mismatch_tolerance_minutes} minutes. Payment waits until one side is corrected.
            </Notice>
          )}

          <div className="fp-two" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 320px), 1fr))', gap: 12 }}>
            <Section
              title="Performer’s confirmation"
              badge={p.performer_dnp ? <Badge tone="warn">Did not perform</Badge> : p.performer_confirmed_at ? <Badge tone="ok">Confirmed</Badge> : <Badge tone="info">Not yet</Badge>}
            >
              {p.performer_dnp ? (
                <div style={{ fontSize: 'var(--fs-base)' }}>They said they didn&rsquo;t perform{p.performer_dnp_reason ? `: ${p.performer_dnp_reason}` : '.'}</div>
              ) : p.performer_confirmed_at ? (
                <FactGrid min={130}>
                  <Fact label="Their times">{timeRangeLabel(p.performer_actual_start_time, p.performer_actual_end_time, !!p.performer_actual_ends_next_day)}</Fact>
                  <Fact label="Confirmed">{when(p.performer_confirmed_at, tz)}</Fact>
                </FactGrid>
              ) : (
                <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                  {p.emails.confirm_requests
                    ? `Asked ${p.emails.confirm_requests} time${p.emails.confirm_requests === 1 ? '' : 's'}, last ${when(p.emails.last_confirm_request_at, tz)}.`
                    : 'Norm asks them to confirm once the manager approves.'}
                </div>
              )}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
                {!paid && p.has_ended && !p.performer_confirmed_at && (
                  <Button size="sm" icon={Send} disabled={!!busy} onClick={() => run('resend', '/resend-confirmation', undefined, (res) => { setNotice((res as { sent: boolean }).sent ? 'Confirmation request sent.' : 'Not sent — inside the reminder throttle, or no email on file.'); reload(); })}>
                    {busy === 'resend' ? 'Sending…' : 'Ask them to confirm'}
                  </Button>
                )}
                <Button size="sm" icon={UserRound} disabled={!!busy} onClick={() => run('portal', '/portal-link', undefined, (res) => setPortalUrl((res as { url: string }).url))}>
                  {busy === 'portal' ? 'Opening…' : 'Open as performer'}
                </Button>
              </div>
              {portalUrl && (
                <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <a href={portalUrl} target="_blank" rel="noreferrer noopener" className="n-btn n-btn--link" style={{ fontSize: 'var(--fs-sm)', display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                    Open their portal <Icon icon={ExternalLink} size={12} />
                  </a>
                  <CopyButton text={portalUrl} size="xs" />
                  <Hint>Shows what they see — no amounts.</Hint>
                </div>
              )}
            </Section>

            <Section
              title="Manager approval"
              badge={p.performance_status === 'did_not_perform' ? <Badge tone="warn">Did not perform</Badge> : approved ? <Badge tone="ok">Approved</Badge> : <Badge tone={p.has_ended ? 'warn' : 'neutral'}>{p.has_ended ? 'Needs approval' : 'Not ended yet'}</Badge>}
            >
              {p.performance_status === 'did_not_perform' ? (
                <>
                  <div style={{ fontSize: 'var(--fs-base)' }}>Marked as did not perform{p.dnp_reason ? `: ${p.dnp_reason}` : '.'}</div>
                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 4 }}>{p.approved_by_name ? `by ${p.approved_by_name}, ` : ''}{when(p.approved_at, tz)}</div>
                </>
              ) : approved ? (
                <>
                  <FactGrid min={130}>
                    <Fact label="Actual times">{timeRangeLabel(p.actual_start_time, p.actual_end_time, !!p.actual_ends_next_day)}</Fact>
                    <Fact label="Pay">{money(p.actual_cents)} <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>({hoursLabel(p.minutes.actual)} × {money(p.rate_cents)})</span></Fact>
                    <Fact label="Approved">{p.approved_by_name ? `${p.approved_by_name}, ` : ''}{when(p.approved_at, tz)}</Fact>
                  </FactGrid>
                  {!!p.minutes.dst_adjustment && (
                    <Hint style={{ marginTop: 6 }}>Daylight saving changed during this performance: {Math.abs(p.minutes.dst_adjustment)} minutes {p.minutes.dst_adjustment < 0 ? 'fewer' : 'more'} were worked than the clock shows, and the pay follows the real hours.</Hint>
                  )}
                </>
              ) : (
                <>
                  {!p.has_ended && <div style={{ marginBottom: 8 }}><Notice tone="neutral">Approval opens once the booked end time has passed.</Notice></div>}
                  {!dnpOpen && (
                    <FieldGrid min={120}>
                      <Field label="Actual start" htmlFor={id('start')}>
                        <input id={id('start')} className="n-input" style={inputStyle} type="time" value={start} onChange={(e) => setStart(e.target.value)} disabled={!p.has_ended} />
                      </Field>
                      <Field label="Actual end" htmlFor={id('end')}>
                        <input id={id('end')} className="n-input" style={inputStyle} type="time" value={end} onChange={(e) => setEnd(e.target.value)} disabled={!p.has_ended} />
                      </Field>
                      <Field label="&nbsp;" htmlFor={id('next')}>
                        <div style={{ paddingTop: 6 }}><Toggle id={id('next')} checked={nextDay} onChange={setNextDay} disabled={!p.has_ended}>Ended next day</Toggle></div>
                      </Field>
                      {p.performer_confirmed_at && !p.performer_dnp && (
                        <div style={{ gridColumn: '1 / -1' }}>
                          <Button size="sm" onClick={() => { setStart(p.performer_actual_start_time ?? start); setEnd(p.performer_actual_end_time ?? end); setNextDay(!!p.performer_actual_ends_next_day); }} disabled={!p.has_ended}>
                            Use the performer&rsquo;s times
                          </Button>
                        </div>
                      )}
                      <Field label="Note (optional)" htmlFor={id('note')} wide>
                        <input id={id('note')} className="n-input" style={inputStyle} value={note} onChange={(e) => setNote(e.target.value)} disabled={!p.has_ended} />
                      </Field>
                    </FieldGrid>
                  )}
                  {dnpOpen && (
                    <Field label="Why didn’t they perform?" htmlFor={id('dnp')}>
                      <textarea id={id('dnp')} className="n-input" style={inputStyle} rows={2} value={dnpReason} onChange={(e) => setDnpReason(e.target.value)} autoFocus />
                    </Field>
                  )}
                  <Hint style={{ marginTop: 8 }}>Pay is rate × the real hours in the venue&rsquo;s zone, worked out by Norm when you approve.</Hint>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
                    {dnpOpen ? (
                      <>
                        <Button size="sm" onClick={() => setDnpOpen(false)} disabled={!!busy}>Back</Button>
                        <Button size="sm" variant="danger" onClick={dnp} disabled={!!busy || !p.has_ended}>{busy === 'dnp' ? 'Saving…' : 'Confirm did not perform'}</Button>
                      </>
                    ) : (
                      <>
                        <Button size="sm" variant="primary" onClick={approve} disabled={!!busy || !p.has_ended}>{busy === 'approve' ? 'Approving…' : 'Approve'}</Button>
                        <Button size="sm" onClick={() => setDnpOpen(true)} disabled={!!busy || !p.has_ended}>Did not perform</Button>
                      </>
                    )}
                  </div>
                </>
              )}
              {(approved || p.performance_status === 'did_not_perform') && !paid && (
                <div style={{ marginTop: 10 }}>
                  <Button size="sm" variant="quiet" onClick={() => setConfirmReset(true)} disabled={!!busy}>Reset to scheduled</Button>
                </div>
              )}
            </Section>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 320px), 1fr))', gap: 12 }}>
            <Section
              title="Closing manager"
              badge={cm && !cm.user_id && cm.name ? <Badge tone="warn"><Icon icon={TriangleAlert} size={12} /> No Norm login</Badge> : null}
              actions={<Button size="sm" icon={RefreshCw} disabled={!!busy} onClick={() => run('cm', '/resolve-closing-manager')}>{busy === 'cm' ? 'Looking up…' : 'Look up now'}</Button>}
            >
              {cm && cm.name ? (
                <FactGrid min={130}>
                  <Fact label="Name">{cm.name}</Fact>
                  <Fact label="Found">{CLOSING_SOURCE_LABEL[cm.source] ?? cm.source}{cm.resolved_at ? `, ${when(cm.resolved_at, tz)}` : ''}</Fact>
                  <Fact label="Email">{cm.email}</Fact>
                </FactGrid>
              ) : (
                <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                  {cm?.source === 'none' ? `Nobody with a closing role (${meta.settings.closing_manager_roles.join(', ')}) was rostered or clocked in that night.` : 'Not looked up yet — Norm runs the lookup each morning for performances awaiting approval.'}
                </div>
              )}
              {cm && !cm.user_id && cm.name && (
                <Hint style={{ marginTop: 6 }}>They have no Norm login under that email, so the approval email can&rsquo;t reach them; the venue digest names them instead.</Hint>
              )}
              {p.emails.manager_requests > 0 && (
                <Hint style={{ marginTop: 6 }}>Approval email sent {p.emails.manager_requests} time{p.emails.manager_requests === 1 ? '' : 's'}, last {when(p.emails.last_manager_request_at, tz)}.</Hint>
              )}
            </Section>

            <Section title="Payment" badge={paid ? <Badge tone="ok">Paid</Badge> : <Badge tone="neutral">Pending</Badge>}>
              <FactGrid min={130}>
                {paid && <Fact label="Paid">{when(p.paid_at, tz)}{p.paid_by ? ` by ${p.paid_by}` : ''}</Fact>}
                {paid && <Fact label="Reference">{p.paid_reference}</Fact>}
                {p.payroll_week_start && <Fact label="Payroll week">w/c {dateLabel(p.payroll_week_start)}</Fact>}
                {(p.lane === 'invoice' || p.lane === 'exemption') && <Fact label="Invoice"><CheckChip status={p.invoice_check_status} />{!p.invoice_file_id && <span style={{ color: 'var(--muted)' }}>Not uploaded</span>}</Fact>}
                {detail.shares && detail.shares.length > 0 && (
                  <Fact label="Shares" wide>
                    {detail.shares.map((s) => <div key={s.id}>{s.member_name}: {money(s.amount_cents)}{s.paid_at ? ' (paid)' : ''}</div>)}
                  </Fact>
                )}
              </FactGrid>
              {meta.can_pay && (
                <div style={{ marginTop: 8 }}>
                  <Button size="sm" onClick={() => openTab('payments', { performance: p.id })}>Open in Payments</Button>
                </div>
              )}
            </Section>
          </div>

          <Section title="Emails about this performance">
            {detail.emails.length === 0 ? (
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>None yet.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {detail.emails.map((m) => (
                  <div key={m.id} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 'var(--fs-sm)' }}>
                    <span style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{when(m.sent_at, tz)}</span>
                    <span>{emailKindLabel(m.kind)} → {m.recipient_email ?? m.recipient_kind}</span>
                    {m.source === 'orbit' ? <span style={{ color: 'var(--muted)' }}>(Orbit, date only)</span> : <EmailStatusBadge status={m.status} />}
                  </div>
                ))}
              </div>
            )}
          </Section>

          <Section title="Changes">
            {detail.changes.length === 0 ? (
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No changes recorded.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {detail.changes.map((c) => (
                  <div key={c.id} style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap', fontSize: 'var(--fs-sm)' }}>
                    <span style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{when(c.changed_at, tz)}</span>
                    <span style={{ fontWeight: 500 }}>{FIELD_LABEL[c.field] ?? c.field.replace(/_/g, ' ')}</span>
                    <span style={{ color: 'var(--text-soft)' }}>{changeValue(c.field, c.old_value)} → {changeValue(c.field, c.new_value)}</span>
                    <span style={{ color: 'var(--muted)' }}>by {c.changed_by_name ?? SOURCE_LABEL[c.changed_by_source] ?? c.changed_by_source}{c.note ? ` — ${c.note}` : ''}</span>
                  </div>
                ))}
              </div>
            )}
          </Section>

          {notice && <Notice tone="info">{notice}</Notice>}
          <WriteError message={writeError?.text ?? null} stale={writeError?.stale} onReload={reload} />
          <DialogActions><Button variant="primary" onClick={onClose} disabled={!!busy}>Close</Button></DialogActions>
        </div>
      )}

      {editing && p && (
        <BookingDialog
          meta={meta}
          performers={performers}
          editing={p}
          onSaved={(next) => apply(next)}
          onDeleted={(pid) => { onDeleted?.(pid); onClose(); }}
          onClose={() => setEditing(false)}
        />
      )}
      {confirmReset && p && (
        <ConfirmDialog title="Reset to scheduled?" confirmLabel="Reset" onClose={() => setConfirmReset(false)} onConfirm={async () => { await run('reset', '/reset', { version: p.version }); }}>
          The approval (or did-not-perform) is removed and the performance waits for approval again. A paid performance can&rsquo;t be reset.
        </ConfirmDialog>
      )}
    </Dialog>
  );
}
