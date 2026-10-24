'use client';

/**
 * What payroll keys for the week after the Ace run: the four ACE figures
 * (gross, net, ESCT, KiwiSaver — the server derives payroll taxes),
 * the other adjustments (signed lines with a Xero account and an "apply
 * to" for the Weekly Summary), and the accrued pay (opening defaults to
 * the prior week's closing on the server).
 */
import { useId, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import { ConfirmDialog } from '../../../ui/Dialog';
import { FormDialog } from '../../../functions/enquiries/bits';
import DataTable, { type Column } from '../../shared/DataTable';
import Money, { useMoney } from '../../shared/Money';
import MoneyInput from '../../shared/MoneyInput';
import { Field, FormGrid, Note, Section } from '../../shared/PageShell';
import { instantLabel } from '../../shared/format';
import type { FinanceApi } from '../../shared/useFinanceApi';
import type { Accruals, AceInputs, Adjustment, XeroAccount } from '../types';

function RehearsalCheck({ value, onChange, shadow }: { value: boolean; onChange: (v: boolean) => void; shadow: boolean }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)' }}>
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      Rehearsal — a practice entry the reconciliation and the journal ignore{shadow ? ' (required during the trial run)' : ''}
    </label>
  );
}

export function AceInputsCard({ api, weekId, value, canWrite, shadow, onChanged }: {
  api: FinanceApi;
  weekId: string;
  value: AceInputs | null;
  canWrite: boolean;
  shadow: boolean;
  onChanged: () => void;
}) {
  const ids = useId();
  const money = useMoney();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ gross: value?.gross_cents ?? null, net: value?.net_cents ?? null, esct: value?.esct_cents ?? null, kiwi: value?.kiwisaver_cents ?? null, rehearsal: value?.rehearsal ?? shadow });

  const open = () => {
    setForm({ gross: value?.gross_cents ?? null, net: value?.net_cents ?? null, esct: value?.esct_cents ?? null, kiwi: value?.kiwisaver_cents ?? null, rehearsal: value?.rehearsal ?? shadow });
    setEditing(true);
  };

  return (
    <Section title="ACE payroll figures" meta={value ? `entered ${instantLabel(value.entered_at)}${value.rehearsal ? ' · rehearsal' : ''}` : 'not entered yet'} tone={value ? undefined : 'warn'} actions={canWrite ? <Button size="sm" onClick={open}>{value ? 'Change' : 'Enter'}</Button> : undefined}>
      {value ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: '10px 18px' }}>
          {[['Gross wages', value.gross_cents], ['Net wages', value.net_cents], ['ESCT', value.esct_cents], ['KiwiSaver', value.kiwisaver_cents], ['Payroll taxes (worked out)', value.payroll_taxes_cents]].map(([label, cents]) => (
            <div key={label as string}>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{label as string}</div>
              <div style={{ fontWeight: 600 }}>{money(cents as number)}</div>
            </div>
          ))}
        </div>
      ) : <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>After the Ace run, type the four figures from Ace. The difference and the journal need them.</span>}
      {editing && (
        <FormDialog title="ACE payroll figures" submitLabel="Save" onSubmit={async () => {
          await api.put(`/weeks/${weekId}/ace-inputs`, { gross_cents: form.gross ?? 0, net_cents: form.net ?? 0, esct_cents: form.esct ?? 0, kiwisaver_cents: form.kiwi ?? 0, rehearsal: form.rehearsal, ...(value ? { version: value.version } : {}) });
          setEditing(false);
          onChanged();
        }} onClose={() => setEditing(false)}>
          <FormGrid min={160}>
            <Field id={`${ids}-g`} label="Gross wages"><MoneyInput id={`${ids}-g`} value={form.gross} onChange={(v) => setForm((f) => ({ ...f, gross: v }))} /></Field>
            <Field id={`${ids}-n`} label="Net wages"><MoneyInput id={`${ids}-n`} value={form.net} onChange={(v) => setForm((f) => ({ ...f, net: v }))} /></Field>
            <Field id={`${ids}-e`} label="ESCT"><MoneyInput id={`${ids}-e`} value={form.esct} onChange={(v) => setForm((f) => ({ ...f, esct: v }))} /></Field>
            <Field id={`${ids}-k`} label="KiwiSaver (employer)"><MoneyInput id={`${ids}-k`} value={form.kiwi} onChange={(v) => setForm((f) => ({ ...f, kiwi: v }))} /></Field>
          </FormGrid>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Payroll taxes payable = gross − net + ESCT + KiwiSaver; the server works it out.</div>
          <RehearsalCheck value={form.rehearsal} onChange={(v) => setForm((f) => ({ ...f, rehearsal: v }))} shadow={shadow} />
        </FormDialog>
      )}
    </Section>
  );
}

function AdjustmentDialog({ api, weekId, existing, accounts, shadow, onClose, onSaved }: {
  api: FinanceApi;
  weekId: string;
  existing: Adjustment | null;
  accounts: XeroAccount[];
  shadow: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const ids = useId();
  const [label, setLabel] = useState(existing?.label ?? '');
  const [amount, setAmount] = useState<number | null>(existing?.amount_cents ?? null);
  const [code, setCode] = useState(existing?.xero_account_code ?? '');
  const [target, setTarget] = useState<'wages' | 'leave' | ''>(existing?.report_target ?? '');
  const [notes, setNotes] = useState(existing?.notes ?? '');
  const [rehearsal, setRehearsal] = useState(existing?.rehearsal ?? shadow);
  const expense = accounts.filter((a) => a.code);
  return (
    <FormDialog title={existing ? 'Change the adjustment' : 'Add an adjustment'} submitLabel="Save" submitDisabled={!label.trim() || amount === null} onSubmit={async () => {
      const account = expense.find((a) => a.code === code);
      const body = { label: label.trim(), amount_cents: amount ?? 0, xero_account_code: code || null, xero_account_name: account?.name ?? null, report_target: target || null, notes: notes.trim() || null };
      if (existing) await api.put(`/adjustments/${existing.id}`, { ...body, version: existing.version });
      else await api.post(`/weeks/${weekId}/adjustments`, { ...body, rehearsal });
      onSaved();
    }} onClose={onClose}>
      <Field id={`${ids}-l`} label="What it is"><input id={`${ids}-l`} className="n-input" value={label} onChange={(e) => setLabel(e.target.value)} style={{ width: '100%' }} placeholder="e.g. Site manager bonus" /></Field>
      <FormGrid min={160}>
        <Field id={`${ids}-a`} label="Amount" hint="Negative takes it off the wages cost"><MoneyInput id={`${ids}-a`} value={amount} onChange={setAmount} allowNegative /></Field>
        <Field id={`${ids}-t`} label="Shows in the Weekly Summary as">
          <select id={`${ids}-t`} className="n-select" value={target} onChange={(e) => setTarget(e.target.value as typeof target)} style={{ width: '100%' }}>
            <option value="">Nothing (journal only)</option>
            <option value="wages">Total wages</option>
            <option value="leave">Leave and entitlements</option>
          </select>
        </Field>
      </FormGrid>
      <Field id={`${ids}-c`} label="Xero account" hint={expense.length ? 'Every adjustment needs one before the journal can build' : 'Connect Xero and map the accounts in Payroll settings to pick one'}>
        <select id={`${ids}-c`} className="n-select" value={code} onChange={(e) => setCode(e.target.value)} style={{ width: '100%' }}>
          <option value="">— none yet —</option>
          {expense.map((a) => <option key={a.code as string} value={a.code as string}>{a.code} · {a.name}{a.class ? ` (${a.class.toLowerCase()})` : ''}</option>)}
        </select>
      </Field>
      <Field id={`${ids}-n`} label="Notes (optional)"><input id={`${ids}-n`} className="n-input" value={notes} onChange={(e) => setNotes(e.target.value)} style={{ width: '100%' }} /></Field>
      {!existing && <RehearsalCheck value={rehearsal} onChange={setRehearsal} shadow={shadow} />}
    </FormDialog>
  );
}

export function AdjustmentsCard({ api, weekId, rows, accounts, canWrite, shadow, onChanged }: {
  api: FinanceApi;
  weekId: string;
  rows: Adjustment[];
  accounts: XeroAccount[];
  canWrite: boolean;
  shadow: boolean;
  onChanged: () => void;
}) {
  const money = useMoney();
  const [editing, setEditing] = useState<Adjustment | null | 'new'>(null);
  const [deleting, setDeleting] = useState<Adjustment | null>(null);
  const columns: Column<Adjustment>[] = [
    { key: 'label', label: 'Adjustment', render: (r) => <span>{r.label}{r.rehearsal && <> <Badge tone="warn">Rehearsal</Badge></>}{r.source !== 'norm' && <> <Badge tone="info">From Orbit</Badge></>}</span> },
    { key: 'amount_cents', label: 'Amount', align: 'right', render: (r) => <Money cents={r.amount_cents} signed tone={r.amount_cents < 0 ? 'error' : undefined} />, csv: (r) => r.amount_cents / 100 },
    { key: 'xero_account_code', label: 'Xero account', render: (r) => (r.xero_account_code ? `${r.xero_account_code} · ${r.xero_account_name ?? ''}` : <Badge tone="error">Missing</Badge>) },
    { key: 'report_target', label: 'In the Summary', hideOnPhone: true, render: (r) => (r.report_target === 'wages' ? 'Total wages' : r.report_target === 'leave' ? 'Leave & entitlements' : '—') },
    { key: 'notes', label: 'Notes', hideOnPhone: true, sortable: false },
    { key: 'actions', label: '', sortable: false, align: 'right', render: (r) => (canWrite ? (
      <span style={{ display: 'inline-flex', gap: 4 }}>
        <Button size="xs" variant="quiet" onClick={() => setEditing(r)}>Change</Button>
        <Button size="xs" variant="quiet" icon={Trash2} onClick={() => setDeleting(r)} aria-label="Delete" />
      </span>
    ) : null) },
  ];
  const total = rows.filter((r) => !r.rehearsal).reduce((s, r) => s + r.amount_cents, 0);
  return (
    <Section title="Other adjustments" meta={rows.length ? `${rows.length} · ${money(total)} counted` : undefined} actions={canWrite ? <Button size="sm" icon={Plus} onClick={() => setEditing('new')}>Add</Button> : undefined} padded={false}>
      <div style={{ padding: '8px 16px 12px' }}>
        <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} dense emptyTitle="No adjustments this week." footer={rows.length ? ['Total', money(total), null, null, null, null] : undefined} />
      </div>
      {editing && <AdjustmentDialog api={api} weekId={weekId} existing={editing === 'new' ? null : editing} accounts={accounts} shadow={shadow} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); onChanged(); }} />}
      {deleting && (
        <ConfirmDialog title="Delete this adjustment?" confirmLabel="Delete" danger onConfirm={async () => { await api.del(`/adjustments/${deleting.id}`); onChanged(); }} onClose={() => setDeleting(null)}>
          &ldquo;{deleting.label}&rdquo; ({money(deleting.amount_cents)}) is removed from the reconciliation and the journal.
        </ConfirmDialog>
      )}
    </Section>
  );
}

export function AccrualsCard({ api, weekId, value, canWrite, shadow, onChanged }: {
  api: FinanceApi;
  weekId: string;
  value: Accruals | null;
  canWrite: boolean;
  shadow: boolean;
  onChanged: () => void;
}) {
  const ids = useId();
  const money = useMoney();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ opening: value?.opening_cents ?? null, accrued: value?.accrued_cents ?? null, paid: value?.paid_cents ?? null, rehearsal: shadow });
  return (
    <Section title="Accrued pay" meta="wages earned but not yet paid" actions={canWrite ? <Button size="sm" onClick={() => { setForm({ opening: value?.opening_cents ?? null, accrued: value?.accrued_cents ?? null, paid: value?.paid_cents ?? null, rehearsal: shadow }); setEditing(true); }}>{value ? 'Change' : 'Enter'}</Button> : undefined}>
      {value ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: '10px 18px' }}>
          {[['Opening', value.opening_effective_cents ?? value.opening_cents, value.opening_cents === null ? 'from the prior week' : 'entered'], ['Accrued this week', value.accrued_cents, 'comes off computed'], ['Paid out this week', value.paid_cents, 'adds to computed'], ['Closing', value.closing_cents, 'carried forward']].map(([label, cents, hint]) => (
            <div key={label as string}>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{label as string}</div>
              <div style={{ fontWeight: 600 }}>{money(cents as number | null)}</div>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{hint as string}</div>
            </div>
          ))}
        </div>
      ) : <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>Nothing accrued this week. Enter it only when wages earned this week are paid in a later one, or earlier accruals are paid now.</span>}
      {editing && (
        <FormDialog title="Accrued pay" submitLabel="Save" onSubmit={async () => {
          await api.put(`/weeks/${weekId}/accruals`, { opening_cents: form.opening, accrued_cents: form.accrued ?? 0, paid_cents: form.paid ?? 0, rehearsal: form.rehearsal });
          setEditing(false);
          onChanged();
        }} onClose={() => setEditing(false)}>
          <FormGrid min={160}>
            <Field id={`${ids}-o`} label="Opening" hint="Leave blank to carry the prior week's closing"><MoneyInput id={`${ids}-o`} value={form.opening} onChange={(v) => setForm((f) => ({ ...f, opening: v }))} placeholder="prior week" /></Field>
            <Field id={`${ids}-a`} label="Accrued this week"><MoneyInput id={`${ids}-a`} value={form.accrued} onChange={(v) => setForm((f) => ({ ...f, accrued: v }))} /></Field>
            <Field id={`${ids}-p`} label="Paid out this week"><MoneyInput id={`${ids}-p`} value={form.paid} onChange={(v) => setForm((f) => ({ ...f, paid: v }))} /></Field>
          </FormGrid>
          <Note tone="plain">Closing = opening + accrued − paid; the server works it out and carries it to next week.</Note>
          <RehearsalCheck value={form.rehearsal} onChange={(v) => setForm((f) => ({ ...f, rehearsal: v }))} shadow={shadow} />
        </FormDialog>
      )}
    </Section>
  );
}
