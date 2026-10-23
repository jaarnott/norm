'use client';

/**
 * The week's snapshot state at the top of the Payrun page: the current
 * snapshot (what the page shows), the close snapshot (what the exports and
 * the journal use), the drift between them, and the three actions — take a
 * snapshot now, start the close (a close snapshot now, ahead of Tuesday
 * 10:00), close the week. Plus the week's notes.
 */
import { useState } from 'react';
import { Camera, Lock, Play } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import { ConfirmDialog } from '../../../ui/Dialog';
import DataTable, { type Column } from '../../shared/DataTable';
import Money, { useMoney } from '../../shared/Money';
import { Facts, Section } from '../../shared/PageShell';
import { formatHours, instantLabel, personName, plural } from '../../shared/format';
import type { FinanceApi } from '../../shared/useFinanceApi';
import type { Diff, Snapshot, Week } from '../types';
import { sourceLabel, statusLabel } from './logic';

function SnapshotFacts({ snapshot, timeZone }: { snapshot: Snapshot | null; timeZone: string }) {
  const money = useMoney();
  if (!snapshot) return <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>Not taken yet.</span>;
  const dropped = snapshot.dropped_deleted + snapshot.dropped_unfinished;
  return (
    <Facts
      min={130}
      items={[
        { label: 'Taken', value: `${instantLabel(snapshot.taken_at, timeZone)} · ${sourceLabel(snapshot.source)}` },
        { label: 'Shifts', value: `${snapshot.shift_count}${dropped ? ` (${snapshot.dropped_deleted} deleted, ${snapshot.dropped_unfinished} unfinished left out)` : ''}` },
        { label: 'Hours', value: formatHours(snapshot.totals?.hours) },
        { label: 'Cost', value: money(snapshot.totals?.cost_cents) },
        { label: 'Leave', value: `${formatHours(snapshot.totals?.leave_hours)} · ${money(snapshot.totals?.leave_cost_cents)}` },
        { label: 'Public holiday pay', value: money(snapshot.totals?.ph_cost_cents) },
      ]}
    />
  );
}

interface DriftRow { kind: 'added' | 'removed' | 'changed'; name: string; detail: string; cents: number | null }

function driftRows(diff: Diff, money: (c: number | null | undefined) => string): DriftRow[] {
  const rows: DriftRow[] = [];
  for (const f of diff.added) rows.push({ kind: 'added', name: personName(f.staff_name), detail: `${f.trading_date ?? ''} ${formatHours(f.hours)}`.trim(), cents: f.cost_cents });
  for (const f of diff.removed) rows.push({ kind: 'removed', name: personName(f.staff_name), detail: `${f.trading_date ?? ''} ${formatHours(f.hours)}`.trim(), cents: -f.cost_cents });
  for (const c of diff.changed) {
    const parts = Object.entries(c.fields).map(([k, [a, b]]) => {
      const show = (v: unknown) => (k.endsWith('_cents') ? money(typeof v === 'number' ? v : Number(v)) : String(v ?? '—'));
      return `${k.replace(/_cents$/, '').replace(/_/g, ' ')}: ${show(a)} → ${show(b)}`;
    });
    const cost = c.fields.cost_cents;
    rows.push({ kind: 'changed', name: personName(c.staff_name), detail: parts.join('; '), cents: cost ? Number(cost[1]) - Number(cost[0]) : null });
  }
  return rows;
}

export default function SnapshotPanel({ week, api, canWrite, onWeek, onReload }: {
  week: Week;
  api: FinanceApi;
  canWrite: boolean;
  onWeek: (w: Week) => void;
  onReload: () => void;
}) {
  const money = useMoney();
  const [confirm, setConfirm] = useState<'close' | 'start' | null>(null);
  const [notes, setNotes] = useState(week.notes ?? '');
  const tz = week.window?.timezone || 'Pacific/Auckland';
  const status = statusLabel(week.status);
  const closed = !!week.closed_at;
  const drift = week.drift && !week.drift.same ? week.drift : null;

  const takeSnapshot = () => api.run('snapshot', async () => {
    await api.post(`/weeks/${week.id}/snapshots`, { source: 'manual' });
    onReload();
  }, 'Snapshot taken from Loaded.');

  const startClose = async () => {
    const w = await api.post<Week>(`/weeks/${week.id}/start-close`);
    onWeek(w);
    onReload();
  };

  const closeWeek = async () => {
    const w = await api.post<Week>(`/weeks/${week.id}/close`, { version: week.version });
    onWeek(w);
    onReload();
  };

  const saveNotes = () => api.run('notes', async () => {
    const w = await api.put<Week>(`/weeks/${week.id}`, { notes: notes.trim() || null, version: week.version });
    onWeek(w);
  }, 'Notes saved.');

  const driftColumns: Column<DriftRow>[] = [
    { key: 'kind', label: 'Change', render: (r) => <Badge tone={r.kind === 'added' ? 'info' : r.kind === 'removed' ? 'error' : 'warn'}>{r.kind === 'added' ? 'Added' : r.kind === 'removed' ? 'Removed' : 'Changed'}</Badge> },
    { key: 'name', label: 'Person' },
    { key: 'detail', label: 'What changed' },
    { key: 'cents', label: 'Cost difference', align: 'right', render: (r) => <Money cents={r.cents} signed />, csv: (r) => (r.cents ?? 0) / 100 },
  ];

  return (
    <>
      <Section
        title="Snapshot"
        meta={closed ? `Closed ${instantLabel(week.closed_at, tz)}` : undefined}
        actions={canWrite && !closed ? (
          <>
            <Button size="sm" icon={Camera} onClick={takeSnapshot} disabled={api.busy === 'snapshot'}>{api.busy === 'snapshot' ? 'Taking…' : 'Take snapshot'}</Button>
            <Button size="sm" icon={Play} onClick={() => setConfirm('start')} disabled={!!api.busy}>Start close</Button>
            <Button size="sm" variant="primary" icon={Lock} onClick={() => setConfirm('close')} disabled={!week.close_snapshot || !!api.busy} title={week.close_snapshot ? undefined : 'Start the close first: closing needs a close snapshot.'}>Close week</Button>
          </>
        ) : undefined}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>Current snapshot</span>
              <Badge tone={status.tone}>{status.label}</Badge>
            </div>
            <SnapshotFacts snapshot={week.current_snapshot} timeZone={tz} />
          </div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', marginBottom: 6 }}>Close snapshot <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— what the exports and the journal use</span></div>
            <SnapshotFacts snapshot={week.close_snapshot} timeZone={tz} />
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <label className="n-label" htmlFor={`notes-${week.id}`}>Notes for this week</label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <textarea id={`notes-${week.id}`} className="n-input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} disabled={!canWrite} style={{ flex: '1 1 260px' }} placeholder="Anything the next person should know about this week" />
            {canWrite && notes !== (week.notes ?? '') && <Button size="sm" onClick={saveNotes} disabled={api.busy === 'notes'}>Save notes</Button>}
          </div>
        </div>
      </Section>

      {drift && (
        <Section title="Changed in Loaded since the close snapshot" tone="warn" meta={`${plural(drift.added.length, 'shift')} added · ${plural(drift.removed.length, 'shift')} removed · ${plural(drift.changed.length, 'shift')} changed · cost ${money(drift.totals.delta.cost_cents)}`} padded={false}>
          <div style={{ padding: '8px 16px 12px' }}>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginBottom: 8 }}>
              The exports and the journal were built from the close snapshot. If these changes belong in the pay run, start the close again to take a fresh close snapshot.
            </div>
            <DataTable columns={driftColumns} rows={driftRows(drift, money)} rowKey={(r, ) => `${r.kind}:${r.name}:${r.detail}`} csvName={`Drift ${week.venue_name} ${week.week_ending}`} dense />
          </div>
        </Section>
      )}

      {confirm === 'start' && (
        <ConfirmDialog title="Start the close now?" confirmLabel="Take the close snapshot" busyLabel="Reading Loaded…" onConfirm={startClose} onClose={() => setConfirm(null)}>
          Norm reads this week's clock-ins from Loaded again and keeps that copy as the close snapshot. The exports and the journal build from it. Anything fixed in Loaded after this needs another close.
        </ConfirmDialog>
      )}
      {confirm === 'close' && (
        <ConfirmDialog title="Close this week?" confirmLabel="Close week" onConfirm={closeWeek} onClose={() => setConfirm(null)}>
          Records that the week is done for {week.venue_name}. The snapshots, exports and pushes stay as they are.
        </ConfirmDialog>
      )}
    </>
  );
}
