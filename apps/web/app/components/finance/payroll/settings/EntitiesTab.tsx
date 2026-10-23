'use client';

/**
 * The pay entities — one per venue that runs its own pay run — and each
 * one's settings: the Ace codes, the uniform bond, the intercompany leave
 * uplift and the tax rule on its line, the sick-leave approver, the payroll
 * contact, and whether the Monday jobs run for it. Settings edits are allowed in shadow (they are
 * Norm's own, not a Loaded or Xero write).
 */
import { useId, useState } from 'react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import { FormDialog } from '../../../functions/enquiries/bits';
import DataTable, { type Column } from '../../shared/DataTable';
import Money from '../../shared/Money';
import MoneyInput from '../../shared/MoneyInput';
import { Field, FormGrid, Note, Stack } from '../../shared/PageShell';
import { bpLabel } from '../../shared/format';
import type { FinanceApi } from '../../shared/useFinanceApi';
import { ICO_LEAVE_TAX_LABELS, type Entity, type IcoLeaveTax } from '../types';

function EntityDialog({ api, entity, onClose, onSaved }: { api: FinanceApi; entity: Entity; onClose: () => void; onSaved: () => void }) {
  const ids = useId();
  const [f, setF] = useState({
    payroll_on: entity.payroll_on,
    ace_pay_type_code: entity.ace_pay_type_code ?? '',
    ace_gratuity_allowance_code: entity.ace_gratuity_allowance_code ?? '',
    security_pay_code: entity.security_pay_code ?? '',
    uniform_bond_cents: entity.uniform_bond_cents as number | null,
    ico_leave_uplift_pct: String(entity.ico_leave_uplift_bp / 100),
    ico_leave_tax: entity.ico_leave_tax,
    sick_approver_name: entity.sick_approver_name ?? '',
    payroll_contact_email: entity.payroll_contact_email ?? '',
  });
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));
  return (
    <FormDialog title={`Payroll settings — ${entity.venue_name}`} width={560} submitLabel="Save" onSubmit={async () => {
      await api.put(`/entities/${entity.venue_id}`, {
        version: entity.version,
        payroll_on: f.payroll_on,
        ace_pay_type_code: f.ace_pay_type_code.trim(),
        ace_gratuity_allowance_code: f.ace_gratuity_allowance_code.trim() || null,
        security_pay_code: f.security_pay_code.trim(),
        uniform_bond_cents: f.uniform_bond_cents ?? 0,
        ico_leave_uplift_bp: Math.round(Number(f.ico_leave_uplift_pct || 0) * 100),
        ico_leave_tax: f.ico_leave_tax,
        sick_approver_name: f.sick_approver_name.trim() || null,
        payroll_contact_email: f.payroll_contact_email.trim() || null,
      });
      onSaved();
    }} onClose={onClose}>
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)' }}>
        <input type="checkbox" checked={f.payroll_on} onChange={(e) => set('payroll_on', e.target.checked)} /> Runs a pay run — the Monday snapshot, hygiene email and Tuesday close happen for this entity
      </label>
      <FormGrid min={200}>
        <Field id={`${ids}-pt`} label="Ace pay type code" hint="The third field of every #52 wages line (50001 today)"><input id={`${ids}-pt`} className="n-input" value={f.ace_pay_type_code} onChange={(e) => set('ace_pay_type_code', e.target.value)} style={{ width: '100%' }} required /></Field>
        <Field id={`${ids}-ga`} label="Ace gratuities allowance code" hint="Needed before GRATUITIES.TXT can be generated"><input id={`${ids}-ga`} className="n-input" value={f.ace_gratuity_allowance_code} onChange={(e) => set('ace_gratuity_allowance_code', e.target.value)} style={{ width: '100%' }} /></Field>
        <Field id={`${ids}-sc`} label="Security contractors' pay code" hint="Staff with this code are paid separately and left out of the wages export"><input id={`${ids}-sc`} className="n-input" value={f.security_pay_code} onChange={(e) => set('security_pay_code', e.target.value)} style={{ width: '100%' }} required /></Field>
        <Field id={`${ids}-ub`} label="Uniform bond" hint="Deducted from holiday pay only with consent"><MoneyInput id={`${ids}-ub`} value={f.uniform_bond_cents} onChange={(v) => set('uniform_bond_cents', v)} /></Field>
        <Field id={`${ids}-up`} label="Intercompany leave & entitlements uplift (%)" hint="Added to recharged wages on the ICO documents"><input id={`${ids}-up`} className="n-input" type="number" step="0.01" min="0" max="100" value={f.ico_leave_uplift_pct} onChange={(e) => set('ico_leave_uplift_pct', e.target.value)} style={{ width: '100%' }} /></Field>
        <Field id={`${ids}-lt`} label="Tax on the intercompany leave line" hint="On an invoice the standard rate is GST on Income, on a bill GST on Expenses. Set it the same on every entity: each side of a recharge is built from its own entity's rule">
          <select id={`${ids}-lt`} className="n-select" value={f.ico_leave_tax} onChange={(e) => set('ico_leave_tax', e.target.value as IcoLeaveTax)} style={{ width: '100%' }}>
            {(Object.keys(ICO_LEAVE_TAX_LABELS) as IcoLeaveTax[]).map((k) => <option key={k} value={k}>{ICO_LEAVE_TAX_LABELS[k]}</option>)}
          </select>
        </Field>
        <Field id={`${ids}-ap`} label="Sick-leave approver" hint="Named in the special-approval line and the approver email"><input id={`${ids}-ap`} className="n-input" value={f.sick_approver_name} onChange={(e) => set('sick_approver_name', e.target.value)} style={{ width: '100%' }} /></Field>
        <Field id={`${ids}-pc`} label="Payroll contact email" hint="Gets this entity's hygiene and parity emails; blank uses the organisation's"><input id={`${ids}-pc`} className="n-input" type="email" value={f.payroll_contact_email} onChange={(e) => set('payroll_contact_email', e.target.value)} style={{ width: '100%' }} /></Field>
      </FormGrid>
      <Note tone="plain">Pay weeks start on Monday at the venue&rsquo;s day start ({entity.day_start}, {entity.timezone}); the payroll system is Ace. Neither can be changed in this version.</Note>
    </FormDialog>
  );
}

export default function EntitiesTab({ api, entities, canWrite, onChanged }: { api: FinanceApi; entities: Entity[]; canWrite: boolean; onChanged: () => void }) {
  const [editing, setEditing] = useState<Entity | null>(null);
  const columns: Column<Entity>[] = [
    { key: 'venue_name', label: 'Entity', render: (r) => <span style={{ fontWeight: 500 }}>{r.venue_name}{!r.is_trading && <> <Badge tone="neutral">Non-trading</Badge></>}</span>, nowrap: true },
    { key: 'payroll_on', label: 'Pay run', sortable: false, render: (r) => (r.payroll_on ? <Badge tone="ok">On</Badge> : <Badge tone="neutral">Off</Badge>) },
    { key: 'loaded_company_id', label: 'Loaded', sortable: false, render: (r) => (r.loaded_company_id ? <Badge tone="ok">Connected</Badge> : <Badge tone="error">Not connected</Badge>) },
    { key: 'ace_pay_type_code', label: 'Pay type', hideOnPhone: true },
    { key: 'ace_gratuity_allowance_code', label: 'Gratuities code', hideOnPhone: true, render: (r) => r.ace_gratuity_allowance_code || <Badge tone="warn">None</Badge> },
    { key: 'security_pay_code', label: 'Security code', hideOnPhone: true },
    { key: 'uniform_bond_cents', label: 'Bond', align: 'right', hideOnPhone: true, render: (r) => <Money cents={r.uniform_bond_cents} dp={0} />, csv: (r) => r.uniform_bond_cents / 100 },
    { key: 'ico_leave_uplift_bp', label: 'Leave uplift', align: 'right', hideOnPhone: true, render: (r) => bpLabel(r.ico_leave_uplift_bp) },
    { key: 'ico_leave_tax', label: 'Leave line tax', hideOnPhone: true, render: (r) => ICO_LEAVE_TAX_LABELS[r.ico_leave_tax] ?? r.ico_leave_tax, csv: (r) => r.ico_leave_tax },
    { key: 'sick_approver_name', label: 'Sick approver', hideOnPhone: true },
    { key: 'payroll_contact_email', label: 'Payroll contact', hideOnPhone: true },
    { key: 'actions', label: '', sortable: false, align: 'right', render: (r) => (canWrite ? <Button size="xs" onClick={() => setEditing(r)}>Change</Button> : null) },
  ];
  return (
    <Stack>
      <Note tone="plain">An entity is a venue that runs its own pay run in Ace, with its own Loaded and Xero connections. Head office (a non-trading venue) is one too. The codes here are what the Ace files carry.</Note>
      <DataTable columns={columns} rows={entities} rowKey={(r) => r.venue_id} csvName="Payroll entities" emptyTitle="No entities." onRowClick={canWrite ? (r) => setEditing(r) : undefined} />
      {editing && <EntityDialog api={api} entity={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); onChanged(); }} />}
    </Stack>
  );
}
