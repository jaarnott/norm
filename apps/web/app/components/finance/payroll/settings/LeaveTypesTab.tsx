'use client';

/**
 * The leave types Loaded holds for the entity, read now, and the Ace style
 * the leave export (IMPORT_LEAVE.TXT) uses for each (owner, 23 Oct 2026:
 * "leave types should come from Loaded"). Loaded's own New Zealand annual,
 * sick and bereavement leave go out as #70, #60 and #63; a type Loaded marks
 * unpaid is left out. A company's own type means nothing to payroll in
 * Loaded, so its style is set here — until it is, the export refuses it by
 * name (never defaulted to #70 as Orbit did).
 */
import { useState } from 'react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import PageState from '../../../ui/PageState';
import DataTable, { type Column } from '../../shared/DataTable';
import { Note, Stack } from '../../shared/PageShell';
import { useFinQuery, type FinanceApi } from '../../shared/useFinanceApi';
import type { Entity, LeaveType } from '../types';

/** Ace's import styles as Loaded lists them (its AcePayroll pay types). */
const STYLES = [
  { value: '62', label: '#62 Public Holidays' },
  { value: '61', label: '#61 Lieu Days Taken' },
  { value: '60', label: '#60 Sick & Special Leave' },
  { value: '63', label: '#63 Bereavement Leave' },
  { value: '70', label: '#70 Holiday Pay' },
];

function styleLabel(v: string): string {
  return STYLES.find((s) => s.value === v)?.label ?? `#${v}`;
}

export default function LeaveTypesTab({ api, entity, canWrite }: { api: FinanceApi; entity: Entity; canWrite: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const q = useFinQuery<{ leave_types: LeaveType[] }>('/api/payroll/leave-types', { venue_id: entity.venue_id }, refresh);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const reload = () => { setEdits({}); setRefresh((n) => n + 1); };

  const save = (row: LeaveType, value: string) => api.run(`style:${row.loaded_leave_type_id}`, async () => {
    await api.put(`/leave-types/${encodeURIComponent(row.loaded_leave_type_id)}/ace-style`, { venue_id: entity.venue_id, ace_style: value || null, version: row.override?.version ?? null });
    reload();
  }, value ? 'Ace style saved.' : 'Ace style cleared.');

  const editable = (r: LeaveType) => canWrite && r.is_paid !== false && r.ace_style_from !== 'loaded' && r.kind !== null;

  const columns: Column<LeaveType>[] = [
    { key: 'name', label: 'Loaded leave type', nowrap: true, render: (r) => (
      <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
        <span style={{ fontWeight: 500 }}>{r.name}</span>
        {r.kind === 'managed' ? <Badge tone="neutral">Loaded&rsquo;s own</Badge> : r.kind === 'custom' ? <Badge tone="info">Company&rsquo;s own</Badge> : <Badge tone="warn">No longer in Loaded</Badge>}
      </span>
    ) },
    { key: 'is_paid', label: 'Paid', render: (r) => (r.is_paid === false ? 'Unpaid' : r.is_paid ? 'Paid' : '—') },
    { key: 'active', label: 'Switched on', hideOnPhone: true, render: (r) => (r.active ? 'Yes' : 'No') },
    { key: 'ace_style', label: 'Ace style', sortable: false, render: (r) => {
      if (r.is_paid === false) return <span style={{ color: 'var(--text-soft)' }}>Left out — unpaid in Loaded</span>;
      if (r.ace_style_from === 'loaded') return <span>{styleLabel(r.ace_style!)} <span style={{ color: 'var(--text-soft)' }}>· from Loaded</span></span>;
      if (!editable(r)) return r.ace_style ? <span>{styleLabel(r.ace_style)} <span style={{ color: 'var(--text-soft)' }}>· set in Norm</span></span> : <Badge tone="error">Not set</Badge>;
      const v = edits[r.loaded_leave_type_id] ?? r.ace_style ?? '';
      const known = STYLES.some((s) => s.value === v);
      return (
        <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          <select className="n-select" value={known ? v : v ? 'other' : ''} aria-label={`Ace style for ${r.name}`} onChange={(e) => setEdits((s) => ({ ...s, [r.loaded_leave_type_id]: e.target.value === 'other' ? '' : e.target.value }))} style={{ height: 30 }}>
            <option value="">Not set</option>
            {STYLES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            <option value="other">Other number…</option>
          </select>
          {!known && <input className="n-input" value={v} placeholder="##" aria-label="Style number" onChange={(e) => setEdits((s) => ({ ...s, [r.loaded_leave_type_id]: e.target.value.replace(/\D/g, '') }))} style={{ width: 60, height: 30 }} />}
        </span>
      );
    } },
    { key: 'actions', label: '', sortable: false, align: 'right', render: (r) => {
      if (!editable(r)) return null;
      const v = edits[r.loaded_leave_type_id];
      const dirty = v !== undefined && v !== (r.ace_style ?? '');
      return dirty ? <Button size="xs" variant="primary" onClick={() => save(r, v)} disabled={!!api.busy}>{v ? 'Save' : 'Clear'}</Button> : null;
    } },
  ];

  if (q.error && !q.data) return <PageState kind="error" title="Could not read the leave types from Loaded." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} />;
  if (!q.data) return <PageState kind="loading" title="Reading Loaded…" />;
  const rows = q.data.leave_types;
  return (
    <Stack>
      <Note tone="plain">Read from Loaded just now. Loaded&rsquo;s own New Zealand annual, sick and bereavement leave go to Ace as #70, #60 and #63, and leave Loaded marks unpaid is left out of the file. A company&rsquo;s own leave type means nothing to payroll in Loaded — give it an Ace style here, or the leave export refuses it by name.</Note>
      <div style={{ display: 'flex', gap: 8 }}>
        <Button size="sm" onClick={reload} disabled={q.loading}>Read again from Loaded</Button>
      </div>
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.loaded_leave_type_id} csvName={`Leave types ${entity.venue_name}`} emptyTitle="Loaded holds no leave types for this entity." rowTone={(r) => (r.is_paid !== false && !r.ace_style ? 'warn' : undefined)} />
    </Stack>
  );
}
