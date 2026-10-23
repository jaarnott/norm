'use client';

/**
 * Gratuities: the monthly pool a venue earned, split by the server pro-rata
 * on hours physically worked there (leave and security excluded; inactive
 * staff keep a share that is not paid; excluded staff are redistributed).
 * Shows the batches earned at this venue, the shares this entity pays this
 * week, and the shares earned here but paid by another entity's payroll.
 * The dialog previews the split before a batch is saved.
 */
import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import { ConfirmDialog } from '../../../ui/Dialog';
import { FormDialog } from '../../../functions/enquiries/bits';
import DataTable, { type Column } from '../../shared/DataTable';
import Money, { useMoney } from '../../shared/Money';
import MoneyInput from '../../shared/MoneyInput';
import { Field, FormGrid, Note, Section, Stack } from '../../shared/PageShell';
import { dateLabel, formatHours, personName, plural } from '../../shared/format';
import { weLabel } from '../../shared/weeks';
import type { FinanceApi } from '../../shared/useFinanceApi';
import type { Allocation, Batch, PayableShare, PayrunDoc } from '../types';

interface Preview {
  rows: Allocation[];
  warnings: string[];
  weeks: string[];
  weeks_without_snapshot: string[];
  payable_cents: number;
  forfeited_cents: number;
  by_payroll_venue: { venue_id: string; venue_name: string; amount_cents: number; hours: number }[];
}

function AddBatchDialog({ api, venueId, venueName, weekStart, rehearsalDefault, shadow, onClose, onSaved }: {
  api: FinanceApi;
  venueId: string;
  venueName: string;
  weekStart: string;
  rehearsalDefault: boolean;
  shadow: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const money = useMoney();
  const [period, setPeriod] = useState(() => weekStart.slice(0, 7));
  const [total, setTotal] = useState<number | null>(null);
  const [glCode, setGlCode] = useState('');
  const [notes, setNotes] = useState('');
  const [rehearsal, setRehearsal] = useState(rehearsalDefault);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const body = () => ({ venue_id: venueId, period, week_start: weekStart, total_cents: total ?? 0, excluded_staff_ids: [...excluded] });

  const workOut = async () => {
    setPreviewing(true);
    setPreviewError(null);
    try {
      setPreview(await api.post<Preview>('/gratuities/preview', body()));
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : 'Could not work out the split.');
    } finally {
      setPreviewing(false);
    }
  };

  const columns: Column<Allocation>[] = [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'hours', label: 'Hours here', align: 'right', value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'amount_cents', label: 'Share', align: 'right', render: (r) => <Money cents={r.amount_cents} />, csv: (r) => r.amount_cents / 100 },
    { key: 'payroll_venue_name', label: 'Paid by', hideOnPhone: true, render: (r) => r.payroll_venue_name || '—' },
    { key: 'is_payable', label: 'Payable', align: 'center', render: (r) => (r.is_payable ? <Badge tone="ok">Yes</Badge> : <Badge tone="warn">No — inactive</Badge>) },
    { key: 'exclude', label: 'Leave out', align: 'center', sortable: false, render: (r) => (
      <input type="checkbox" aria-label={`Leave ${personName(r.staff_name)} out`} checked={excluded.has(r.staff_member_id)} onChange={(e) => {
        setExcluded((s) => { const n = new Set(s); if (e.target.checked) n.add(r.staff_member_id); else n.delete(r.staff_member_id); return n; });
        setPreview(null);
      }} />
    ) },
  ];

  return (
    <FormDialog
      title={`Add a gratuities batch — ${venueName}`}
      width={720}
      submitLabel="Save batch"
      submitDisabled={!preview || !total}
      onSubmit={async () => {
        await api.post<Batch>('/gratuities', { ...body(), gl_code: glCode.trim() || null, notes: notes.trim() || null, rehearsal });
        onSaved();
      }}
      onClose={onClose}
    >
      <FormGrid min={160}>
        <Field id="grat-period" label="Month the pool is for">
          <input id="grat-period" className="n-input" type="month" value={period} onChange={(e) => { setPeriod(e.target.value); setPreview(null); }} style={{ width: '100%' }} required />
        </Field>
        <Field id="grat-total" label="Pool total" hint="One unit in the Ace file is $1">
          <MoneyInput id="grat-total" value={total} onChange={(v) => { setTotal(v); setPreview(null); }} />
        </Field>
        <Field id="grat-gl" label="GL code (optional)">
          <input id="grat-gl" className="n-input" value={glCode} onChange={(e) => setGlCode(e.target.value)} style={{ width: '100%' }} />
        </Field>
      </FormGrid>
      <Field id="grat-notes" label="Notes (optional)">
        <input id="grat-notes" className="n-input" value={notes} onChange={(e) => setNotes(e.target.value)} style={{ width: '100%' }} />
      </Field>
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        Paid in the pay week {weLabel(weekStart, true)}. The split is on hours physically worked at {venueName} across every pay week that touches the month; leave and security are left out; a person who is no longer active keeps a share that is not paid.
      </div>
      <div>
        <Button size="sm" onClick={workOut} disabled={previewing || !total || !period}>{previewing ? 'Working it out…' : preview ? 'Work out the split again' : 'Work out the split'}</Button>
        {previewError && <Note tone="error">{previewError}</Note>}
      </div>
      {preview && (
        <Stack gap={8}>
          {preview.weeks_without_snapshot.length > 0 && (
            <Note tone="warn">No snapshot yet for {preview.weeks_without_snapshot.map((w) => weLabel(w)).join(', ')} — those hours are missing from the split. Take the snapshots first.</Note>
          )}
          {preview.warnings.map((w) => <Note key={w} tone="warn">{w}</Note>)}
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            Payable {money(preview.payable_cents)} · not paid (inactive) {money(preview.forfeited_cents)}
            {preview.by_payroll_venue.length > 1 && <> · paid by {preview.by_payroll_venue.map((v) => `${v.venue_name} ${money(v.amount_cents)}`).join(', ')}</>}
          </div>
          <DataTable columns={columns} rows={preview.rows} rowKey={(r) => r.staff_member_id} dense maxHeight={320} initialSort={{ key: 'amount_cents', dir: 'desc' }} emptyTitle="No hours worked here in the month." />
        </Stack>
      )}
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)' }}>
        <input type="checkbox" checked={rehearsal} onChange={(e) => setRehearsal(e.target.checked)} />
        Rehearsal — a practice entry that never reaches an export{shadow ? ' (required while Orbit is the system of record)' : ''}
      </label>
    </FormDialog>
  );
}

export default function GratuitiesTab({ doc, api, canWrite, weekStart, shadow, onChanged }: {
  doc: PayrunDoc;
  api: FinanceApi;
  canWrite: boolean;
  weekStart: string;
  shadow: boolean;
  onChanged: () => void;
}) {
  const money = useMoney();
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<Batch | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const venueName = doc.entity.venue_name;

  const allocationColumns = (withExclude: boolean, batchId?: string): Column<Allocation>[] => [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'pay_code', label: 'Pay code', render: (r) => r.pay_code || <Badge tone="error">Missing</Badge> },
    { key: 'hours', label: 'Hours here', align: 'right', value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'amount_cents', label: 'Share', align: 'right', render: (r) => <Money cents={r.amount_cents} tone={r.excluded_at ? 'muted' : undefined} />, csv: (r) => r.amount_cents / 100 },
    { key: 'payroll_venue_name', label: 'Paid by', hideOnPhone: true, render: (r) => r.payroll_venue_name || '—' },
    { key: 'state', label: 'State', sortable: false, render: (r) => (r.excluded_at ? <Badge tone="neutral">Left out</Badge> : r.is_payable ? <Badge tone="ok">Payable</Badge> : <Badge tone="warn">Not paid — inactive</Badge>) },
    ...(withExclude && canWrite && batchId ? [{
      key: 'actions', label: '', sortable: false, align: 'right' as const, render: (r: Allocation) => (r.excluded_at ? null : (
        <Button size="xs" variant="quiet" onClick={() => api.run(`exclude:${r.id}`, async () => { await api.post(`/gratuities/${batchId}/allocations/${r.id}/exclude`); onChanged(); }, 'Share left out and redistributed.')} disabled={!!api.busy}>Leave out</Button>
      )),
    }] : []),
  ];

  const payableColumns: Column<PayableShare>[] = [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'pay_code', label: 'Pay code', render: (r) => r.pay_code || <Badge tone="error">Missing</Badge> },
    { key: 'period', label: 'Pool', render: (r) => `${r.period} · ${r.earned_at_venue_name || '—'}` },
    { key: 'amount_cents', label: 'Share', align: 'right', render: (r) => <Money cents={r.amount_cents} />, csv: (r) => r.amount_cents / 100 },
    { key: 'rehearsal', label: 'State', sortable: false, render: (r) => (r.rehearsal ? <Badge tone="warn">Rehearsal</Badge> : <Badge tone="ok">Payable</Badge>) },
  ];
  // The route sends no `paid_elsewhere` list (build note); the batches'
  // own allocations say which shares another entity's payroll carries.
  const paidElsewhere: Allocation[] = doc.gratuities.paid_elsewhere
    ?? doc.gratuities.batches.flatMap((b) => (b.allocations ?? []).filter((a) => a.payroll_venue_id && a.payroll_venue_id !== doc.entity.venue_id && !a.excluded_at));

  return (
    <Stack>
      <Section
        title="Batches earned at this venue"
        meta={doc.gratuities.batches.length ? plural(doc.gratuities.batches.length, 'batch', 'batches') : undefined}
        actions={canWrite ? <Button size="sm" icon={Plus} onClick={() => setAdding(true)}>Add a batch</Button> : undefined}
        padded={false}
      >
        {doc.gratuities.batches.length === 0 ? (
          <div style={{ padding: '12px 16px', color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>No gratuities batch is paid in this week.</div>
        ) : doc.gratuities.batches.map((b) => {
          const isOpen = open[b.id] ?? false;
          const allocations = b.allocations ?? [];
          return (
            <div key={b.id} style={{ borderBottom: '1px solid var(--line-soft)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', flexWrap: 'wrap' }}>
                <button type="button" onClick={() => setOpen((o) => ({ ...o, [b.id]: !isOpen }))} aria-expanded={isOpen} style={{ background: 'none', border: 'none', padding: 0, font: 'inherit', fontWeight: 600, color: 'var(--text)', cursor: 'pointer' }}>
                  {b.period} pool · {money(b.total_cents)}
                </button>
                {b.rehearsal && <Badge tone="warn">Rehearsal</Badge>}
                {b.source !== 'norm' && <Badge tone="info">From Orbit</Badge>}
                <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                  payable {money(b.payable_cents)} · not paid {money(b.forfeited_cents)}{b.intercompany_cents ? ` · paid by other entities ${money(b.intercompany_cents)}` : ''}
                </span>
                <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{dateLabel(b.created_at)}{b.gl_code ? ` · GL ${b.gl_code}` : ''}{b.notes ? ` · ${b.notes}` : ''}</span>
                {canWrite && <Button size="xs" variant="quiet" icon={Trash2} onClick={() => setDeleting(b)} style={{ marginLeft: 'auto' }}>Delete</Button>}
              </div>
              {isOpen && (
                <div style={{ padding: '0 16px 12px' }}>
                  {b.source !== 'norm' && <div style={{ marginBottom: 6 }}><Note tone="plain">Carried over from Orbit as it was keyed there. Its shares may not add up to the pool exactly.</Note></div>}
                  <DataTable columns={allocationColumns(true, b.id)} rows={allocations} rowKey={(r) => r.id} csvName={`Gratuities ${b.period} ${venueName}`} dense initialSort={{ key: 'amount_cents', dir: 'desc' }} emptyTitle="No shares in this batch." />
                </div>
              )}
            </div>
          );
        })}
      </Section>

      <Section title="Paid through this entity's payroll this week" meta={doc.gratuities.payable_here.length ? `${plural(doc.gratuities.payable_here.length, 'share')} · ${money(doc.gratuities.payable_here.reduce((s, a) => s + a.amount_cents, 0))}` : undefined} padded={false}>
        <div style={{ padding: '8px 16px 12px' }}>
          <DataTable columns={payableColumns} rows={doc.gratuities.payable_here} rowKey={(r) => `${r.batch_id}:${r.staff_name}:${r.pay_code ?? ''}`} csvName={`Gratuities payable ${venueName} ${weLabel(weekStart)}`} dense emptyTitle="Nothing to pay this week." emptyDetail="Shares earned here and paid by this payroll, plus shares earned at another venue by people this entity pays." />
        </div>
      </Section>

      <Section title="Earned here, paid by another entity" meta={paidElsewhere.length ? plural(paidElsewhere.length, 'share') : undefined} padded={false}>
        <div style={{ padding: '8px 16px 12px' }}>
          <DataTable columns={allocationColumns(false)} rows={paidElsewhere} rowKey={(r) => r.id} csvName={`Gratuities recharged ${venueName} ${weLabel(weekStart)}`} dense emptyTitle="None." emptyDetail="Shares of a batch earned here whose person is paid by another entity's payroll. They become intercompany recharges on the Intercompany page." />
        </div>
      </Section>

      {adding && (
        <AddBatchDialog api={api} venueId={doc.entity.venue_id} venueName={venueName} weekStart={weekStart} rehearsalDefault={shadow} shadow={shadow} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); onChanged(); }} />
      )}
      {deleting && (
        <ConfirmDialog title="Delete this batch?" confirmLabel="Delete batch" danger onConfirm={async () => { await api.del(`/gratuities/${deleting.id}`); onChanged(); }} onClose={() => setDeleting(null)}>
          The {deleting.period} pool of {money(deleting.total_cents)} and every share in it are removed. An export already generated keeps the lines it had.
        </ConfirmDialog>
      )}
    </Stack>
  );
}
