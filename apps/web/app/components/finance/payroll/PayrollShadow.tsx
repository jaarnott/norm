'use client';

/**
 * Loaded Payroll — Shadow. While Orbit is the system of record Norm
 * computes beside it; this page is where the comparison lives (plan §14):
 * the parity runs (weekly on Wednesday, or on demand), each run's items
 * with Norm's value beside Orbit's and a class — expected (a named kind of
 * difference) or unexplained — signed off by the payroll person; the
 * week's failed job runs; the importer's last run; "Run parity now"; and
 * the byte-check of Norm's Ace files against the owner's.
 *
 * Deep links: `?page=payroll-shadow&run=<id>`.
 */
import { useState } from 'react';
import { Play, RefreshCw, Upload } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import { FormDialog, StatTile, TileRow } from '../../functions/enquiries/bits';
import { readPageParam } from '../../functions/shared/meta';
import DataTable, { type Column } from '../shared/DataTable';
import PageShell, { Field, FormGrid, Note, Section, Stack } from '../shared/PageShell';
import WeekPicker from '../shared/WeekPicker';
import { instantLabel, plural } from '../shared/format';
import { useFinQuery, useFinanceApi } from '../shared/useFinanceApi';
import { shiftWeek, weLabel } from '../shared/weeks';
import { MetaGate } from './PayrollPayrun';
import type { Entity, JobRun, ParityItem, ParityRun, ShadowDoc, Week } from './types';
import { usePayrollPage } from './usePayrollMeta';

const MODULE_LABEL: Record<string, string> = { payroll: 'Payroll exports and totals', inputs: 'Keyed inputs', journal: 'Journals and ICO documents', entertainers: 'Entertainers', summary: 'Weekly Summary' };
const JOB_LABEL: Record<string, string> = {
  'payroll.snapshot': 'Monday snapshot', 'payroll.close_snapshot': 'Tuesday close snapshot', 'payroll.hygiene_email': 'Hygiene email',
  'payroll.gratuity_reminder': 'Gratuities reminder', 'payroll.retention': 'Retention', 'finance.parity_weekly': 'Weekly parity',
  'xero.status_refresh': 'Xero status refresh', 'importer.nightly': 'Nightly import from Orbit', 'importer.post_freeze_watch': 'Post-freeze watch',
};

/** A run's period is "<venue id>:<week start>" for a payroll or inputs run, else
 *  a plain period; show the entity's name and the week-ending label. */
function periodLabel(period: string, entities: Entity[]): string {
  const m = /^(.+):(\d{4}-\d{2}-\d{2})$/.exec(period);
  if (m) {
    const name = entities.find((e) => e.venue_id === m[1])?.venue_name ?? 'Entity';
    return `${name} · ${weLabel(m[2], true)}`;
  }
  return /^\d{4}-\d{2}-\d{2}$/.test(period) ? weLabel(period, true) : period;
}

function sinceIso(weeks: number): string {
  const d = new Date();
  d.setDate(d.getDate() - 7 * weeks);
  return d.toISOString();
}

function RunDialog({ api, entities, defaultWeek, thisWeek, onClose, onDone }: { api: ReturnType<typeof useFinanceApi>; entities: Entity[]; defaultWeek: string; thisWeek: string | null; onClose: () => void; onDone: (run: ParityRun) => void }) {
  const [module, setModule] = useState<'payroll' | 'inputs'>('payroll');
  const [venue, setVenue] = useState(entities[0]?.venue_id ?? '');
  const [week, setWeek] = useState(defaultWeek);
  return (
    <FormDialog title="Run parity now" submitLabel="Run" busyLabel="Comparing…" submitDisabled={!venue} onSubmit={async () => { onDone(await api.post<ParityRun>('/shadow/runs', { module, venue_id: venue, week_start: week })); }} onClose={onClose}>
      <FormGrid min={200}>
        <Field id="parity-module" label="What to compare">
          <select id="parity-module" className="n-select" value={module} onChange={(e) => setModule(e.target.value as typeof module)} style={{ width: '100%' }}>
            <option value="payroll">Payroll — the close snapshot's totals against Loaded's pay summary, and the stored exports</option>
            <option value="inputs">Keyed inputs — Norm-keyed payouts, gratuities, ACE figures, adjustments and accruals against Orbit's</option>
          </select>
        </Field>
        <Field id="parity-venue" label="Entity">
          <select id="parity-venue" className="n-select" value={venue} onChange={(e) => setVenue(e.target.value)} style={{ width: '100%' }}>
            {entities.map((e) => <option key={e.venue_id} value={e.venue_id}>{e.venue_name}</option>)}
          </select>
        </Field>
      </FormGrid>
      <Field id="parity-week" label="Pay week">
        <WeekPicker value={week} onChange={setWeek} venue={entities.find((e) => e.venue_id === venue)} thisWeek={thisWeek} />
      </Field>
    </FormDialog>
  );
}

function AceFilesDialog({ api, entities, defaultWeek, thisWeek, onClose, onDone }: { api: ReturnType<typeof useFinanceApi>; entities: Entity[]; defaultWeek: string; thisWeek: string | null; onClose: () => void; onDone: (run: ParityRun) => void }) {
  const [venue, setVenue] = useState(entities[0]?.venue_id ?? '');
  const [week, setWeek] = useState(defaultWeek);
  const [files, setFiles] = useState<Record<string, File | null>>({ import: null, leave: null, gratuities: null, entertainers: null });
  const any = Object.values(files).some(Boolean);
  return (
    <FormDialog title="Check Norm's files against the Ace files" width={560} submitLabel="Compare" busyLabel="Comparing…" submitDisabled={!venue || !any} onSubmit={async () => {
      const w = await api.post<Week>('/weeks', { venue_id: venue, week_start: week });
      const form = new FormData();
      form.append('week_id', w.id);
      for (const [k, f] of Object.entries(files)) if (f) form.append(k, f);
      onDone(await api.upload<ParityRun>('/shadow/exports-check', form));
    }} onClose={onClose}>
      <Note tone="plain">Upload the files head office sent to Ace for the week. Norm byte-diffs its stored exports against them; a difference that is only a leave style or a cent of rounding is classed as expected.</Note>
      <FormGrid min={200}>
        <Field id="ace-venue" label="Entity">
          <select id="ace-venue" className="n-select" value={venue} onChange={(e) => setVenue(e.target.value)} style={{ width: '100%' }}>
            {entities.map((e) => <option key={e.venue_id} value={e.venue_id}>{e.venue_name}</option>)}
          </select>
        </Field>
        <Field id="ace-week" label="Pay week"><WeekPicker value={week} onChange={setWeek} venue={entities.find((e) => e.venue_id === venue)} thisWeek={thisWeek} /></Field>
      </FormGrid>
      {(['import', 'leave', 'gratuities', 'entertainers'] as const).map((k) => (
        <Field key={k} id={`ace-${k}`} label={{ import: 'IMPORT.TXT', leave: 'IMPORT_LEAVE.TXT', gratuities: 'GRATUITIES.TXT', entertainers: 'IMPORT_ENTERTAINERS.TXT' }[k]}>
          <input id={`ace-${k}`} type="file" accept=".txt,text/plain" onChange={(e) => setFiles((f) => ({ ...f, [k]: e.target.files?.[0] ?? null }))} />
        </Field>
      ))}
    </FormDialog>
  );
}

function SignOffDialog({ api, item, onClose, onDone }: { api: ReturnType<typeof useFinanceApi>; item: ParityItem; onClose: () => void; onDone: () => void }) {
  const [explanation, setExplanation] = useState(item.explanation ?? '');
  return (
    <FormDialog title="Sign off this difference" submitLabel="Sign off" submitDisabled={!explanation.trim()} onSubmit={async () => { await api.post(`/shadow/items/${item.id}/sign-off`, { explanation: explanation.trim() }); onDone(); }} onClose={onClose}>
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{item.key} — Norm {item.norm_value ?? '—'} · Orbit {item.orbit_value ?? '—'}</div>
      <Field id="signoff-why" label="Why the figures differ">
        <textarea id="signoff-why" className="n-input" rows={3} value={explanation} onChange={(e) => setExplanation(e.target.value)} style={{ width: '100%' }} placeholder="e.g. Orbit dropped two staff with no timeline row; Norm takes Loaded's attribution" />
      </Field>
    </FormDialog>
  );
}

function RunDetail({ runId, api, canWrite, refresh, entities, onChanged }: { runId: string; api: ReturnType<typeof useFinanceApi>; canWrite: boolean; refresh: number; entities: Entity[]; onChanged: () => void }) {
  // The detail route sends `items` as the list itself (the runs list sends a count).
  const q = useFinQuery<Omit<ParityRun, 'items'> & { items: ParityItem[] }>(`/api/payroll/shadow/runs/${runId}`, {}, refresh);
  const [signing, setSigning] = useState<ParityItem | null>(null);
  if (q.error && !q.data) return <PageState kind="error" title="Could not load the run." detail={q.error} />;
  if (!q.data) return <PageState kind="loading" title="Loading…" />;
  const run = q.data;
  const columns: Column<ParityItem>[] = [
    { key: 'entity_name', label: 'Entity', render: (r) => r.entity_name || r.entity || '—', nowrap: true },
    { key: 'key', label: 'What', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)' }}>{r.key}</span> },
    { key: 'norm_value', label: 'Norm', render: (r) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{r.norm_value ?? '—'}</span> },
    { key: 'orbit_value', label: 'Orbit', render: (r) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{r.orbit_value ?? '—'}</span> },
    { key: 'class', label: 'Class', render: (r) => (r.class === 'expected' ? <Badge tone="info">Expected</Badge> : r.signed_off_at ? <Badge tone="ok">Signed off</Badge> : <Badge tone="error">Unexplained</Badge>) },
    { key: 'explanation', label: 'Explanation', sortable: false, hideOnPhone: true, render: (r) => <span style={{ fontSize: 'var(--fs-sm)' }}>{r.explanation}{r.signed_off_at && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>signed off {instantLabel(r.signed_off_at)}</div>}</span> },
    { key: 'actions', label: '', sortable: false, align: 'right', render: (r) => (canWrite && r.class === 'unexplained' && !r.signed_off_at ? <Button size="xs" onClick={() => setSigning(r)}>Sign off</Button> : null) },
  ];
  return (
    <Section title={`${MODULE_LABEL[run.module] ?? run.module} · ${periodLabel(run.period, entities)}`} meta={`${run.trigger === 'manual' ? 'run by hand' : 'weekly run'} ${instantLabel(run.ran_at)} · ${plural(run.items.length, 'item')} · ${run.unexplained} unexplained`} tone={run.unexplained ? 'error' : undefined} padded={false}>
      <div style={{ padding: '8px 16px 12px' }}>
        <DataTable columns={columns} rows={run.items} rowKey={(r) => r.id} csvName={`Parity ${run.module} ${run.period}`} dense initialSort={{ key: 'class', dir: 'desc' }} emptyTitle="No differences at all." rowTone={(r) => (r.class === 'unexplained' && !r.signed_off_at ? 'error' : undefined)} />
        {run.totals && Object.keys(run.totals).length > 0 && <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{Object.entries(run.totals).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' · ')}</div>}
      </div>
      {signing && <SignOffDialog api={api} item={signing} onClose={() => setSigning(null)} onDone={() => { setSigning(null); q.retry(); onChanged(); }} />}
    </Section>
  );
}

export default function PayrollShadow({ props }: DisplayBlockProps) {
  const page = usePayrollPage(props);
  const api = useFinanceApi();
  const [refresh, setRefresh] = useState(0);
  const [weeks, setWeeks] = useState(4);
  const [selected, setSelected] = useState<string | null>(() => readPageParam('run'));
  const [running, setRunning] = useState<'parity' | 'ace' | null>(null);
  const since = sinceIso(weeks);
  const shadow = useFinQuery<ShadowDoc>(page.meta ? '/api/payroll/shadow' : null, { since }, refresh);
  const jobs = useFinQuery<{ runs: JobRun[] }>(page.meta ? '/api/payroll/jobs' : null, { since }, refresh);
  const canWrite = !!page.meta?.can_write;
  const bump = () => setRefresh((n) => n + 1);
  const entities = page.meta?.entities ?? [];
  const defaultWeek = page.meta?.last_week_start ?? shiftWeek(page.weekStart, 0);

  const runColumns: Column<ParityRun>[] = [
    { key: 'ran_at', label: 'Ran', render: (r) => instantLabel(r.ran_at), nowrap: true },
    { key: 'module', label: 'Compared', render: (r) => MODULE_LABEL[r.module] ?? r.module },
    { key: 'period', label: 'Entity · week', render: (r) => periodLabel(r.period, entities), nowrap: true },
    { key: 'trigger', label: 'How', hideOnPhone: true, render: (r) => (r.trigger === 'manual' ? 'By hand' : 'Weekly job') },
    { key: 'items', label: 'Items', align: 'right' },
    { key: 'unexplained', label: 'Unexplained', align: 'right', render: (r) => (r.unexplained ? <Badge tone="error">{r.unexplained}</Badge> : <Badge tone="ok">0</Badge>) },
  ];
  const jobColumns: Column<JobRun>[] = [
    { key: 'job', label: 'Job', render: (r) => JOB_LABEL[r.job] ?? r.job, nowrap: true },
    { key: 'venue_name', label: 'Entity', render: (r) => r.venue_name || (r.scope_id ? 'organisation' : '—') },
    { key: 'slot', label: 'For', hideOnPhone: true },
    { key: 'started_at', label: 'Started', render: (r) => instantLabel(r.started_at), nowrap: true },
    { key: 'finished_at', label: 'Finished', hideOnPhone: true, render: (r) => (r.finished_at ? instantLabel(r.finished_at) : <Badge tone="warn">still running or died</Badge>) },
    { key: 'error', label: 'Error', sortable: false, render: (r) => <span style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)' }}>{r.error}</span> },
  ];

  const importerRuns = (jobs.data?.runs ?? []).filter((r) => r.job.startsWith('importer.')).sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)));
  const lastImport = importerRuns[0] ?? null;
  const runs = shadow.data?.runs ?? [];
  const unexplained = runs.reduce((s, r) => s + r.unexplained, 0);
  const failed = shadow.data?.failed_jobs ?? [];

  const body = shadow.error && !shadow.data ? <PageState kind="error" title="Could not load the parity runs." detail={shadow.error} action={<Button size="sm" onClick={shadow.retry}>Try again</Button>} />
    : !shadow.data ? <PageState kind="loading" title="Loading…" />
    : (
      <Stack>
        {page.meta?.mode === 'live' && <Note tone="ok">Loaded Payroll is live. Parity runs keep comparing with the imported Orbit rows for as long as the importer runs.</Note>}
        <TileRow min={150}>
          <StatTile label="Parity runs" value={String(runs.length)} sub={`last ${weeks} weeks`} />
          <StatTile label="Unexplained differences" value={String(unexplained)} sub={unexplained ? 'need a sign-off or a fix' : 'every difference explained'} />
          <StatTile label="Failed jobs" value={String(failed.length)} sub={failed.length ? 'in the parity email' : 'none'} />
          <StatTile label="Last import from Orbit" value={lastImport ? (lastImport.error ? 'Failed' : lastImport.finished_at ? 'Done' : 'Running') : 'None yet'} sub={lastImport ? `${instantLabel(lastImport.started_at)}${lastImport.error ? ` — ${lastImport.error}` : ''}` : 'nightly 02:30 NZ until live'} />
        </TileRow>
        <Section title="Parity runs" actions={(
          <>
            <select className="n-select" value={weeks} onChange={(e) => setWeeks(Number(e.target.value))} aria-label="How far back" style={{ height: 30 }}>
              {[2, 4, 8, 13, 26].map((w) => <option key={w} value={w}>Last {w} weeks</option>)}
            </select>
            {canWrite && <Button size="sm" icon={Upload} onClick={() => setRunning('ace')}>Check Ace files</Button>}
            {canWrite && <Button size="sm" variant="primary" icon={Play} onClick={() => setRunning('parity')}>Run parity now</Button>}
            <Button size="xs" variant="quiet" icon={RefreshCw} onClick={bump}>Refresh</Button>
          </>
        )} padded={false}>
          <div style={{ padding: '8px 16px 12px' }}>
            <DataTable columns={runColumns} rows={runs} rowKey={(r) => r.id} dense csvName="Parity runs" initialSort={{ key: 'ran_at', dir: 'desc' }} onRowClick={(r) => setSelected(r.id)} selectedKey={selected} emptyTitle="No parity runs yet." emptyDetail="The weekly job runs on Wednesday 04:00 after the nightly import; or run one now." />
          </div>
        </Section>
        {selected && <RunDetail runId={selected} api={api} canWrite={canWrite} refresh={refresh} entities={entities} onChanged={bump} />}
        <Section title="Failed job runs" meta={`last ${weeks} weeks`} tone={failed.length ? 'error' : undefined} padded={false}>
          <div style={{ padding: '8px 16px 12px' }}>
            <DataTable columns={jobColumns} rows={failed} rowKey={(r) => r.id} dense emptyTitle="Every job finished." />
          </div>
        </Section>
        <Section title="Every job run" meta={`last ${weeks} weeks · snapshots, close, hygiene, parity, Xero status, the importer`} padded={false}>
          <div style={{ padding: '8px 16px 12px' }}>
            <DataTable columns={jobColumns} rows={jobs.data?.runs ?? []} rowKey={(r) => r.id} dense maxHeight={360} initialSort={{ key: 'started_at', dir: 'desc' }} emptyTitle="No job runs recorded yet." />
          </div>
        </Section>
        <Section title="Expected kinds of difference" meta="a run classes a difference as expected when it matches one of these">
          <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '8px 20px', fontSize: 'var(--fs-sm)' }}>
            {Object.entries(shadow.data.expected_classes).map(([code, text]) => (
              <div key={code}><dt style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{code}</dt><dd style={{ margin: 0 }}>{text}</dd></div>
            ))}
          </dl>
        </Section>
      </Stack>
    );

  return (
    <PageShell isPage={page.isPage} title="Shadow" meta="Norm beside Orbit — what differs, and why" mode={page.meta?.mode} testId="payroll-shadow">
      <MetaGate status={page.status} error={page.error} loading={page.loading}>{body}</MetaGate>
      {running === 'parity' && <RunDialog api={api} entities={entities} defaultWeek={defaultWeek} thisWeek={page.meta?.this_week_start ?? null} onClose={() => setRunning(null)} onDone={(run) => { setRunning(null); setSelected(run.id); bump(); }} />}
      {running === 'ace' && <AceFilesDialog api={api} entities={entities} defaultWeek={defaultWeek} thisWeek={page.meta?.this_week_start ?? null} onClose={() => setRunning(null)} onDone={(run) => { setRunning(null); setSelected(run.id); bump(); }} />}
    </PageShell>
  );
}
