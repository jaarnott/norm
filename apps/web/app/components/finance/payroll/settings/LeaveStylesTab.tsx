'use client';

/**
 * Which Ace leave style each Loaded leave type exports as (#70 annual,
 * #60 sick, #63 bereavement, or another number), with the salaried and
 * hourly line shapes, signed off per entity. The export refuses a type
 * with no style — never defaults to #70 as Orbit did.
 */
import { useState } from 'react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import PageState from '../../../ui/PageState';
import DataTable, { type Column } from '../../shared/DataTable';
import { Note, Stack } from '../../shared/PageShell';
import { instantLabel } from '../../shared/format';
import { useFinQuery, type FinanceApi } from '../../shared/useFinanceApi';
import type { Entity, LeaveStyle } from '../types';

const STYLES = [
  { value: '70', label: '#70 Annual leave' },
  { value: '60', label: '#60 Sick leave' },
  { value: '63', label: '#63 Bereavement' },
];
const SALARIED = [
  { value: 'days_daily_rate', label: 'Days × daily rate (#70)' },
  { value: 'days_only', label: 'Days only (#60/#63)' },
  { value: 'hours_rate', label: 'Hours × rate' },
];
const HOURLY = [
  { value: 'days_hours_rate', label: 'Days, hours × rate (#70)' },
  { value: 'hours_rate_days', label: 'Hours × rate, days (#60/#63)' },
  { value: 'hours_rate', label: 'Hours × rate' },
];

export default function LeaveStylesTab({ api, entity, canWrite }: { api: FinanceApi; entity: Entity; canWrite: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const q = useFinQuery<{ styles: LeaveStyle[] }>('/api/payroll/leave-styles', { venue_id: entity.venue_id }, refresh);
  const [edits, setEdits] = useState<Record<string, Partial<LeaveStyle>>>({});
  const bump = () => { setEdits({}); setRefresh((n) => n + 1); };

  const seed = () => api.run('seed', async () => { const r = await api.post<{ created: number }>('/leave-styles/seed', { venue_id: entity.venue_id }); bump(); return r; }, 'Leave types read from Loaded.');
  const save = (row: LeaveStyle) => api.run(`save:${row.id}`, async () => {
    const e = edits[row.id] ?? {};
    const custom = e.ace_style !== undefined ? e.ace_style : row.ace_style;
    await api.put(`/leave-styles/${row.id}`, { version: row.version, ace_style: custom || null, salaried_method: e.salaried_method !== undefined ? e.salaried_method : row.salaried_method, hourly_method: e.hourly_method !== undefined ? e.hourly_method : row.hourly_method });
    bump();
  }, 'Leave style saved.');
  const signOff = (row: LeaveStyle) => api.run(`sign:${row.id}`, async () => { await api.post(`/leave-styles/${row.id}/sign-off`, { version: row.version }); bump(); }, 'Signed off.');

  const value = <K extends keyof LeaveStyle>(row: LeaveStyle, k: K): LeaveStyle[K] => (edits[row.id]?.[k] !== undefined ? (edits[row.id][k] as LeaveStyle[K]) : row[k]);
  const dirty = (row: LeaveStyle) => !!edits[row.id];

  const columns: Column<LeaveStyle>[] = [
    { key: 'name', label: 'Loaded leave type', render: (r) => <span style={{ fontWeight: 500 }}>{r.name}</span>, nowrap: true },
    { key: 'ace_style', label: 'Ace style', sortable: false, render: (r) => {
      const v = value(r, 'ace_style') ?? '';
      const known = STYLES.some((s) => s.value === v);
      return canWrite ? (
        <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          <select className="n-select" value={known ? v : v ? 'other' : ''} aria-label={`Ace style for ${r.name}`} onChange={(e) => setEdits((s) => ({ ...s, [r.id]: { ...s[r.id], ace_style: e.target.value === 'other' ? (known ? '' : v) : e.target.value || null } }))} style={{ height: 30 }}>
            <option value="">Not mapped</option>
            {STYLES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            <option value="other">Other number…</option>
          </select>
          {!known && v !== '' && <input className="n-input" value={v} aria-label="Style number" onChange={(e) => setEdits((s) => ({ ...s, [r.id]: { ...s[r.id], ace_style: e.target.value.replace(/\D/g, '') } }))} style={{ width: 60, height: 30 }} />}
          {!known && v === '' && (edits[r.id]?.ace_style === '' ) && <input className="n-input" placeholder="##" aria-label="Style number" onChange={(e) => setEdits((s) => ({ ...s, [r.id]: { ...s[r.id], ace_style: e.target.value.replace(/\D/g, '') } }))} style={{ width: 60, height: 30 }} />}
        </span>
      ) : (v ? `#${v}` : <Badge tone="error">Not mapped</Badge>);
    } },
    { key: 'salaried_method', label: 'Salaried line', sortable: false, hideOnPhone: true, render: (r) => (canWrite ? (
      <select className="n-select" value={value(r, 'salaried_method') ?? ''} aria-label={`Salaried line for ${r.name}`} onChange={(e) => setEdits((s) => ({ ...s, [r.id]: { ...s[r.id], salaried_method: e.target.value || null } }))} style={{ height: 30 }}>
        <option value="">Default for the style</option>
        {SALARIED.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
      </select>
    ) : (SALARIED.find((s) => s.value === r.salaried_method)?.label ?? r.salaried_method ?? 'default')) },
    { key: 'hourly_method', label: 'Hourly line', sortable: false, hideOnPhone: true, render: (r) => (canWrite ? (
      <select className="n-select" value={value(r, 'hourly_method') ?? ''} aria-label={`Hourly line for ${r.name}`} onChange={(e) => setEdits((s) => ({ ...s, [r.id]: { ...s[r.id], hourly_method: e.target.value || null } }))} style={{ height: 30 }}>
        <option value="">Default for the style</option>
        {HOURLY.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
      </select>
    ) : (HOURLY.find((s) => s.value === r.hourly_method)?.label ?? r.hourly_method ?? 'default')) },
    { key: 'signed', label: 'Signed off', sortable: false, render: (r) => (r.signed_off_at ? <Badge tone="ok" title={`by ${r.signed_off_by ?? '—'}`}>{instantLabel(r.signed_off_at)}</Badge> : <Badge tone="warn">Not yet</Badge>) },
    { key: 'actions', label: '', sortable: false, align: 'right', render: (r) => (canWrite ? (
      <span style={{ display: 'inline-flex', gap: 4 }}>
        {dirty(r) && <Button size="xs" variant="primary" onClick={() => save(r)} disabled={!!api.busy}>Save</Button>}
        {!dirty(r) && !r.signed_off_at && r.ace_style && <Button size="xs" onClick={() => signOff(r)} disabled={!!api.busy}>Sign off</Button>}
      </span>
    ) : null) },
  ];

  if (q.error && !q.data) return <PageState kind="error" title="Could not load the leave styles." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} />;
  if (!q.data) return <PageState kind="loading" title="Loading…" />;
  const rows = q.data.styles;
  return (
    <Stack>
      <Note tone="plain">The leave export (IMPORT_LEAVE.TXT) refuses a leave type with no Ace style; family violence, alternative holiday, public holiday not worked, unpaid and parental leave must never go out as annual leave. The accountant or payroll provider signs each row off before the cut-over.</Note>
      <div style={{ display: 'flex', gap: 8 }}>
        {canWrite && <Button size="sm" onClick={seed} disabled={api.busy === 'seed'}>{api.busy === 'seed' ? 'Reading Loaded…' : rows.length ? 'Read new leave types from Loaded' : 'Read the leave types from Loaded'}</Button>}
      </div>
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} csvName={`Leave styles ${entity.venue_name}`} emptyTitle="No leave types yet." emptyDetail="Read them from Loaded to start; annual, sick and bereavement are prefilled as Orbit mapped them." rowTone={(r) => (r.ace_style ? undefined : 'warn')} />
    </Stack>
  );
}
