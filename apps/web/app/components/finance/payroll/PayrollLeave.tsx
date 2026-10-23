'use client';

/**
 * Loaded Payroll — Leave. One entity, one pay week: every paid leave
 * request touching the week as Loaded holds it (requested), beside the
 * decision payroll has recorded (paid). The decision runs one of three
 * workflows in a dialog with every gate shown. After a sick-leave decision
 * the three emails (to the approver, to the staff member, a certificate
 * request) go from here.
 *
 * Deep links: `?page=payroll-leave&venue=<id>&week=<monday>`.
 */
import { useMemo, useState } from 'react';
import { Mail, RefreshCw, Trash2 } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import { ConfirmDialog } from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import { FormDialog, StatTile, TileRow } from '../../functions/enquiries/bits';
import DataTable, { type Column } from '../shared/DataTable';
import Money, { useMoney } from '../shared/Money';
import PageShell, { Field, Note, Section, Stack } from '../shared/PageShell';
import WeekPicker from '../shared/WeekPicker';
import { dateLabel, formatHours, instantLabel, personName, plural } from '../shared/format';
import { useFinQuery, useFinanceApi } from '../shared/useFinanceApi';
import { weLabel, weekRangeLabel } from '../shared/weeks';
import { MetaGate } from './PayrollPayrun';
import PayoutDialog from './leave/PayoutDialog';
import type { Payout, RequestedDoc, RequestedRow } from './types';
import { usePayrollPage } from './usePayrollMeta';

const KIND_LABEL = { annual: 'Annual', sick: 'Sick', generic: 'Other' } as const;

function stateOf(p: Payout | null): { label: string; tone: 'ok' | 'warn' | 'neutral' | 'info' | 'error' } {
  if (!p) return { label: 'Not decided', tone: 'warn' };
  if (p.rehearsal) return { label: 'Rehearsal', tone: 'info' };
  if (p.source === 'orbit_legacy') return { label: 'From Orbit — week inferred', tone: 'info' };
  if (p.source === 'orbit') return { label: 'From Orbit', tone: 'info' };
  return { label: 'Decided', tone: 'ok' };
}

function gateSummary(p: Payout): string | null {
  const d = p.workflow_inputs?.decision as Record<string, unknown> | undefined;
  if (!d || p.workflow_kind !== 'sick') return null;
  const g = (k: string) => {
    const v = d[k] as { passed?: boolean; override?: boolean } | boolean | undefined;
    if (typeof v === 'boolean') return v;
    return v?.passed ?? null;
  };
  const parts = [['qualification', 'qualifies'], ['balance', 'balance'], ['certificate', 'certificate']].map(([k, label]) => {
    const r = g(k);
    return `${label}: ${r === true ? 'yes' : r === false ? 'no' : '—'}`;
  });
  return parts.join(' · ');
}

function EmailDialog({ api, payout, defaultTo, onClose }: { api: ReturnType<typeof useFinanceApi>; payout: Payout; defaultTo: string; onClose: () => void }) {
  const [kind, setKind] = useState<'approver' | 'staff' | 'certificate'>('approver');
  const [to, setTo] = useState(defaultTo);
  const [sent, setSent] = useState<string | null>(null);
  return (
    <FormDialog title={`Email about ${personName(payout.staff_name)}'s sick leave`} submitLabel="Send" onSubmit={async () => {
      const r = await api.post<{ status: string; to?: string }>(`/leave/payouts/${payout.id}/email`, { kind, to: to.trim() });
      setSent(r.status === 'sent' ? `Sent to ${r.to ?? to}.` : 'Suppressed — no email goes out from this environment.');
    }} onClose={onClose}>
      <Field id="email-kind" label="Which email">
        <select id="email-kind" className="n-select" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} style={{ width: '100%' }}>
          <option value="approver">To the approver — doesn&rsquo;t qualify, special approval needed</option>
          <option value="staff">To the staff member — cannot process without a certificate</option>
          <option value="certificate">To the staff member — please send a certificate</option>
        </select>
      </Field>
      <Field id="email-to" label="To" hint="While Orbit is the system of record every email goes to the payroll contact instead.">
        <input id="email-to" className="n-input" type="email" value={to} onChange={(e) => setTo(e.target.value)} style={{ width: '100%' }} required />
      </Field>
      {sent && <Note tone="ok">{sent}</Note>}
    </FormDialog>
  );
}

export default function PayrollLeave({ props }: DisplayBlockProps) {
  const page = usePayrollPage(props);
  const api = useFinanceApi();
  const money = useMoney();
  const [refresh, setRefresh] = useState(0);
  const [hideZero, setHideZero] = useState(false);
  const [deciding, setDeciding] = useState<RequestedRow | null>(null);
  const [deleting, setDeleting] = useState<Payout | null>(null);
  const [emailing, setEmailing] = useState<Payout | null>(null);
  const venueId = page.entity?.venue_id ?? null;
  const q = useFinQuery<RequestedDoc>(venueId ? '/api/payroll/leave/requested' : null, { venue_id: venueId, week_start: page.weekStart }, refresh);
  const all = useFinQuery<{ payouts: Payout[] }>(venueId ? '/api/payroll/leave/payouts' : null, { venue_id: venueId, week_start: page.weekStart }, refresh);
  const canWrite = !!page.meta?.can_write;
  const shadow = page.meta?.mode === 'shadow';
  const bump = () => setRefresh((n) => n + 1);

  const rows = useMemo(() => (q.data?.rows ?? []).filter((r) => !hideZero || r.requested_cents !== 0 || (r.payout && r.payout.paid_cents !== 0)), [q.data, hideZero]);
  const undecided = (q.data?.rows ?? []).filter((r) => r.is_paid_type && !r.payout).length;
  const matched = new Set((q.data?.rows ?? []).map((r) => r.payout?.id).filter(Boolean));
  const other = (all.data?.payouts ?? []).filter((p) => !matched.has(p.id));
  const tz = page.entity?.timezone || 'Pacific/Auckland';

  const columns: Column<RequestedRow>[] = [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'leave_type_name', label: 'Leave', render: (r) => <span>{r.leave_type_name || '—'} <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>{KIND_LABEL[r.workflow_kind]}</span></span> },
    { key: 'dates', label: 'Days', sortable: false, hideOnPhone: true, value: (r) => r.dates.join(', '), render: (r) => r.dates.map((d) => dateLabel(d, { year: false })).join(', ') },
    { key: 'hours', label: 'Hours', align: 'right', hideOnPhone: true, value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'requested_cents', label: 'Requested', align: 'right', render: (r) => <Money cents={r.requested_cents} />, csv: (r) => r.requested_cents / 100 },
    { key: 'paid', label: 'Paid', align: 'right', value: (r) => (r.payout ? r.payout.paid_cents : null), render: (r) => (r.payout ? <Money cents={r.payout.paid_cents} tone={r.payout.paid_cents === 0 ? 'error' : undefined} /> : <span style={{ color: 'var(--muted)' }}>—</span>), csv: (r) => (r.payout ? r.payout.paid_cents / 100 : null) },
    { key: 'state', label: 'State', sortable: false, render: (r) => { const s = stateOf(r.payout); return <Badge tone={r.is_paid_type ? s.tone : 'neutral'}>{r.is_paid_type ? s.label : 'Unpaid leave'}</Badge>; } },
    { key: 'notes', label: 'Notes', hideOnPhone: true, sortable: false, value: (r) => r.payout?.notes ?? null, render: (r) => (
      <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        {r.payout?.notes}
        {r.payout && gateSummary(r.payout) && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{gateSummary(r.payout)}</div>}
        {r.payout?.bond_deducted_cents ? <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>bond deducted {money(r.payout.bond_deducted_cents)} ({r.payout.bond_consent === 'file' ? 'consent on file' : 'held in Ace'})</div> : null}
      </span>
    ) },
    { key: 'actions', label: '', sortable: false, align: 'right', render: (r) => (
      <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        {canWrite && <Button size="xs" variant={r.payout ? 'quiet' : 'primary'} onClick={(e) => { e.stopPropagation(); setDeciding(r); }}>{r.payout ? 'Change' : 'Decide'}</Button>}
        {canWrite && r.payout && r.workflow_kind === 'sick' && <Button size="xs" variant="quiet" icon={Mail} onClick={(e) => { e.stopPropagation(); setEmailing(r.payout); }}>Email</Button>}
        {canWrite && r.payout && <Button size="xs" variant="quiet" icon={Trash2} onClick={(e) => { e.stopPropagation(); setDeleting(r.payout); }} aria-label="Remove the decision" />}
      </span>
    ) },
  ];

  const otherColumns: Column<Payout>[] = [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'leave_type', label: 'Leave' },
    { key: 'requested_cents', label: 'Requested', align: 'right', render: (r) => <Money cents={r.requested_cents} />, csv: (r) => r.requested_cents / 100 },
    { key: 'paid_cents', label: 'Paid', align: 'right', render: (r) => <Money cents={r.paid_cents} />, csv: (r) => r.paid_cents / 100 },
    { key: 'state', label: 'State', sortable: false, render: (r) => { const s = stateOf(r); return <Badge tone={s.tone}>{s.label}</Badge>; } },
    { key: 'decided_at', label: 'Decided', hideOnPhone: true, render: (r) => (r.decided_at ? instantLabel(r.decided_at, tz) : '—') },
    { key: 'notes', label: 'Notes', hideOnPhone: true, sortable: false },
    { key: 'actions', label: '', sortable: false, align: 'right', render: (r) => (canWrite ? <Button size="xs" variant="quiet" icon={Trash2} onClick={() => setDeleting(r)} aria-label="Remove the decision" /> : null) },
  ];

  let body: React.ReactNode;
  if (!page.entity) {
    body = <PageState kind="empty" title="No payroll entities." detail="None of the venues you can see runs a pay run yet." />;
  } else if (q.error && !q.data) {
    body = <PageState kind="error" title="Could not read the leave requests." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} />;
  } else if (!q.data) {
    body = <PageState kind="loading" title="Reading leave requests from Loaded…" />;
  } else {
    const d = q.data;
    body = (
      <Stack>
        {d.live === false && <Note tone="warn">Loaded could not be read just now{d.error ? ` (${d.error})` : ''}; the requests shown come from the snapshot.</Note>}
        <TileRow min={150}>
          <StatTile label="Requests" value={String(d.rows.length)} sub={`${plural(new Set(d.rows.map((r) => r.staff_member_id)).size, 'person', 'people')} on leave`} />
          <StatTile label="Requested (Loaded)" value={money(d.totals.requested_cents)} />
          <StatTile label="Paid (decided)" value={money(d.totals.paid_cents)} />
          <StatTile label="Not yet decided" value={String(undecided)} sub={undecided ? 'needed before the leave export' : 'all decided'} />
        </TileRow>
        <Section title="Requests touching this week" meta={weekRangeLabel(page.weekStart)} actions={(
          <>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)' }}>
              <input type="checkbox" checked={hideZero} onChange={(e) => setHideZero(e.target.checked)} /> Hide $0 requests
            </label>
            <Button size="xs" variant="quiet" icon={RefreshCw} onClick={bump}>Refresh</Button>
          </>
        )} padded={false}>
          <div style={{ padding: '8px 16px 12px' }}>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(r) => `${r.leave_request_id}:${r.staff_member_id}`}
              csvName={`Leave ${page.entity.venue_name} ${weLabel(page.weekStart)}`}
              initialSort={{ key: 'staff_name', dir: 'asc' }}
              onRowClick={canWrite ? (r) => setDeciding(r) : undefined}
              emptyTitle="No leave requests touch this week."
              rowTone={(r) => (!r.is_paid_type ? 'muted' : r.payout ? undefined : 'warn')}
            />
          </div>
        </Section>
        {(other.length > 0 || all.loading) && (
          <Section title="Other decisions on record for this week" meta="not matched to a request Loaded shows now (imported from Orbit, or the request moved)" padded={false}>
            <div style={{ padding: '8px 16px 12px' }}>
              <DataTable columns={otherColumns} rows={other} rowKey={(r) => r.id} dense emptyTitle="None." />
            </div>
          </Section>
        )}
      </Stack>
    );
  }

  return (
    <PageShell
      isPage={page.isPage}
      title="Leave"
      meta={page.entity ? `${page.entity.venue_name} · requested in Loaded, paid as decided here` : undefined}
      mode={page.meta?.mode}
      venues={page.venues}
      venueId={page.venueId}
      onVenueChange={page.setVenue}
      venueTestId="payroll-leave-venue"
      toolbar={page.meta ? <WeekPicker value={page.weekStart} onChange={page.setWeekStart} venue={page.entity} thisWeek={page.meta.this_week_start} /> : undefined}
      testId="payroll-leave"
    >
      <MetaGate status={page.status} error={page.error} loading={page.loading}>{body}</MetaGate>
      {deciding && page.entity && (
        <PayoutDialog api={api} entity={page.entity} weekStart={page.weekStart} row={deciding} existing={deciding.payout} rehearsalDefault={shadow} shadow={shadow} onClose={() => setDeciding(null)} onSaved={() => { setDeciding(null); bump(); }} />
      )}
      {deleting && (
        <ConfirmDialog title="Remove this decision?" confirmLabel="Remove" danger onConfirm={async () => { await api.del(`/leave/payouts/${deleting.id}`); bump(); }} onClose={() => setDeleting(null)}>
          {personName(deleting.staff_name)}&rsquo;s {deleting.leave_type || 'leave'} goes back to &ldquo;not decided&rdquo; for {weLabel(deleting.week_start, true)}.
        </ConfirmDialog>
      )}
      {emailing && <EmailDialog api={api} payout={emailing} defaultTo={page.meta?.organization.payroll_contact_email || page.entity?.payroll_contact_email || ''} onClose={() => setEmailing(null)} />}
    </PageShell>
  );
}
