'use client';

/**
 * Payroll — the Leave tab. One entity, one pay week: every leave request
 * touching the week as Loaded holds it (requested), beside the decision
 * payroll has recorded (paid). The decision runs one of three workflows in
 * a dialog with every gate shown. After a sick-leave decision the three
 * emails (to the approver, to the staff member, a certificate request) go
 * from here.
 *
 * It also carries what the old Payrun Leave tab showed from the week's
 * pay-run snapshot, joined on the request and the person: each row's pay
 * code and the Ace style its leave type goes out as (which decides whether
 * IMPORT_LEAVE.TXT can be generated), the leave-types setup error, and any
 * snapshot row Loaded no longer lists. The requests are read live, so the
 * tab works before any snapshot exists.
 *
 * Link: `?page=payroll&tab=leave&venue=<id>&week=<monday>`.
 */
import { useMemo, useState, type ReactNode } from 'react';
import { Mail, Trash2 } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import { ConfirmDialog } from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import { FormDialog, StatTile, TileRow } from '../../functions/enquiries/bits';
import DataTable, { type Column } from '../shared/DataTable';
import Money, { useMoney } from '../shared/Money';
import PageShell, { Field, Note, Section, Stack } from '../shared/PageShell';
import { dateLabel, formatHours, instantLabel, personName, plural } from '../shared/format';
import { useFinQuery, useFinanceApi } from '../shared/useFinanceApi';
import { useOpenTab } from '../shared/useWorkspaceTab';
import { weLabel, weekRangeLabel } from '../shared/weeks';
import PayoutDialog from './leave/PayoutDialog';
import type { PayrollTabProps } from './tabProps';
import type { LeaveRow, Payout, PayrunDoc, RequestedDoc, RequestedRow } from './types';
import { aceStyleText, joinLeave, leaveKey } from './workspace';

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

function EmailDialog({ api, payout, defaultTo, shadow, onClose }: { api: ReturnType<typeof useFinanceApi>; payout: Payout; defaultTo: string; shadow: boolean; onClose: () => void }) {
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
      <Field id="email-to" label="To" hint={shadow ? 'During the trial run every email goes to the payroll contact instead.' : undefined}>
        <input id="email-to" className="n-input" type="email" value={to} onChange={(e) => setTo(e.target.value)} style={{ width: '100%' }} required />
      </Field>
      {sent && <Note tone="ok">{sent}</Note>}
    </FormDialog>
  );
}

function AceStyle({ row, setupError }: { row: LeaveRow | null | undefined; setupError?: string | null }) {
  const s = aceStyleText(row, setupError);
  if (!s) return <span style={{ color: 'var(--muted)' }}>—</span>;
  if (s.error) return <Badge tone="error">{s.text}</Badge>;
  return <span style={{ color: row?.unpaid ? 'var(--text-soft)' : undefined }}>{s.text}</span>;
}

export default function PayrollLeave({ meta, entity, weekStart, refresh, onChanged, doc, docNote, snapshotToggle }: PayrollTabProps & {
  /** The week's pay-run snapshot (the doc the Hours tab shows); null before one exists or while it loads. */
  doc: PayrunDoc | null;
  /** Why there is no snapshot doc (no snapshot yet, still loading, an error), in words. */
  docNote?: ReactNode;
  /** The Current / Close snapshot switch, when the week has both. */
  snapshotToggle?: ReactNode;
}) {
  const api = useFinanceApi();
  const money = useMoney();
  const openPayroll = useOpenTab('payroll');
  const [hideZero, setHideZero] = useState(false);
  const [deciding, setDeciding] = useState<RequestedRow | null>(null);
  const [deleting, setDeleting] = useState<Payout | null>(null);
  const [emailing, setEmailing] = useState<Payout | null>(null);
  const venueId = entity?.venue_id ?? null;
  const q = useFinQuery<RequestedDoc>(venueId ? '/api/payroll/leave/requested' : null, { venue_id: venueId, week_start: weekStart }, refresh);
  const all = useFinQuery<{ payouts: Payout[] }>(venueId ? '/api/payroll/leave/payouts' : null, { venue_id: venueId, week_start: weekStart }, refresh);
  const canWrite = !!meta.can_write;
  const shadow = meta.mode === 'shadow';
  const setupError = doc?.leave.setup_error ?? null;

  const snapshotRows = useMemo(() => doc?.leave.rows ?? [], [doc]);
  const joined = useMemo(() => joinLeave(snapshotRows, q.data?.rows ?? []), [snapshotRows, q.data]);
  const rows = useMemo(() => (q.data?.rows ?? []).filter((r) => !hideZero || r.requested_cents !== 0 || (r.payout && r.payout.paid_cents !== 0)), [q.data, hideZero]);
  const undecided = (q.data?.rows ?? []).filter((r) => r.is_paid_type && !r.payout).length;
  const matched = new Set((q.data?.rows ?? []).map((r) => r.payout?.id).filter(Boolean));
  const other = (all.data?.payouts ?? []).filter((p) => !matched.has(p.id));
  const tz = entity?.timezone || 'Pacific/Auckland';
  const styleError = (r: RequestedRow) => r.is_paid_type && !!aceStyleText(joined.byKey.get(leaveKey(r)), setupError)?.error;

  const columns: Column<RequestedRow>[] = [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'pay_code', label: 'Pay code', hideOnPhone: true, value: (r) => r.pay_code || joined.byKey.get(leaveKey(r))?.pay_code || '', render: (r) => r.pay_code || joined.byKey.get(leaveKey(r))?.pay_code || <Badge tone="error">Missing</Badge> },
    { key: 'leave_type_name', label: 'Leave', render: (r) => <span>{r.leave_type_name || '—'} <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>{KIND_LABEL[r.workflow_kind]}</span></span> },
    { key: 'ace_style', label: 'Ace style', hideOnPhone: true, sortable: false, value: (r) => aceStyleText(joined.byKey.get(leaveKey(r)), setupError)?.text ?? '', render: (r) => <AceStyle row={joined.byKey.get(leaveKey(r))} setupError={setupError} /> },
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

  // Snapshot rows Loaded no longer lists (the old Payrun Leave tab's columns).
  const snapshotOnlyColumns: Column<LeaveRow>[] = [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'pay_code', label: 'Pay code', render: (r) => r.pay_code || <Badge tone="error">Missing</Badge> },
    { key: 'leave_type_name', label: 'Leave type' },
    { key: 'dates', label: 'Days', sortable: false, hideOnPhone: true, value: (r) => r.dates.join(', '), render: (r) => r.dates.map((d) => dateLabel(d, { year: false })).join(', ') },
    { key: 'hours', label: 'Hours', align: 'right', hideOnPhone: true, value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'cost_cents', label: 'Cost (Loaded)', align: 'right', render: (r) => <Money cents={r.cost_cents} />, csv: (r) => r.cost_cents / 100 },
    { key: 'ace_style', label: 'Ace style', value: (r) => aceStyleText(r, setupError)?.text ?? '', render: (r) => <AceStyle row={r} setupError={setupError} /> },
  ];

  let body: ReactNode;
  if (!entity) {
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
        {setupError && <Note tone="warn">Could not read the leave types from Loaded: {setupError}. Without them no row has an Ace style, and the leave file can&rsquo;t be generated.</Note>}
        <TileRow min={150}>
          <StatTile label="Requests" value={String(d.rows.length)} sub={`${plural(new Set(d.rows.map((r) => r.staff_member_id)).size, 'person', 'people')} on leave`} />
          <StatTile label="Requested (Loaded)" value={money(d.totals.requested_cents)} />
          <StatTile label="Paid (decided)" value={money(d.totals.paid_cents)} />
          <StatTile label="Not yet decided" value={String(undecided)} sub={undecided ? 'needed before the leave export' : 'all decided'} />
        </TileRow>
        <Section title="Requests touching this week" meta={weekRangeLabel(weekStart)} actions={(
          <>
            {snapshotToggle}
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)' }}>
              <input type="checkbox" checked={hideZero} onChange={(e) => setHideZero(e.target.checked)} /> Hide $0 requests
            </label>
          </>
        )} padded={false}>
          <div style={{ padding: '8px 16px 12px' }}>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={leaveKey}
              csvName={`Leave ${entity.venue_name} ${weLabel(weekStart)}`}
              initialSort={{ key: 'staff_name', dir: 'asc' }}
              onRowClick={canWrite ? (r) => setDeciding(r) : undefined}
              emptyTitle="No leave requests touch this week."
              rowTone={(r) => (styleError(r) ? 'error' : !r.is_paid_type ? 'muted' : r.payout ? undefined : 'warn')}
            />
            <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
              The pay code and Ace style come from the week&rsquo;s pay-run snapshot{docNote ? <> ({docNote})</> : null}. The Ace style is how each leave type goes out in the leave file: Loaded&rsquo;s own types take it from Loaded, a company&rsquo;s own type from{' '}
              <Button size="xs" variant="link" onClick={() => openPayroll('settings', { view: 'leave', venue: entity.venue_id })}>Payroll settings → Leave types</Button>.
            </div>
          </div>
        </Section>
        {joined.unmatched.length > 0 && (
          <Section title="Other leave in the snapshot" meta="in this week’s pay-run snapshot, but not in Loaded’s list now" padded={false}>
            <div style={{ padding: '8px 16px 12px' }}>
              <DataTable columns={snapshotOnlyColumns} rows={joined.unmatched} rowKey={leaveKey} dense csvName={`Leave in the snapshot ${entity.venue_name} ${weLabel(weekStart)}`} emptyTitle="None." rowTone={(r) => (aceStyleText(r, setupError)?.error ? 'error' : undefined)} />
            </div>
          </Section>
        )}
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
    <PageShell isPage embedded title="Leave" testId="payroll-leave">
      {body}
      {deciding && entity && (
        <PayoutDialog api={api} entity={entity} weekStart={weekStart} row={deciding} existing={deciding.payout} rehearsalDefault={shadow} shadow={shadow} onClose={() => setDeciding(null)} onSaved={() => { setDeciding(null); onChanged(); }} />
      )}
      {deleting && (
        <ConfirmDialog title="Remove this decision?" confirmLabel="Remove" danger onConfirm={async () => { await api.del(`/leave/payouts/${deleting.id}`); onChanged(); }} onClose={() => setDeleting(null)}>
          {personName(deleting.staff_name)}&rsquo;s {deleting.leave_type || 'leave'} goes back to &ldquo;not decided&rdquo; for {weLabel(deleting.week_start, true)}.
        </ConfirmDialog>
      )}
      {emailing && <EmailDialog api={api} payout={emailing} defaultTo={meta.organization.payroll_contact_email || entity?.payroll_contact_email || ''} shadow={shadow} onClose={() => setEmailing(null)} />}
    </PageShell>
  );
}
