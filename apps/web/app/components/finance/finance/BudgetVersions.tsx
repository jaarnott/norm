'use client';

/**
 * Budgets → Versions: every version of a venue-year's daily budget
 * (import / manual / forecast), locked or a draft. "Locked at the start of
 * the year" is a mechanism here: the current budget is the latest locked
 * version, and a locked version's days cannot be edited — unlock it, or
 * start a new version (optionally copied from one) and lock that.
 */
import { useState } from 'react';
import { Lock, LockOpen, Plus } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import DataTable, { type Column } from '../shared/DataTable';
import { Field, FormGrid, Section } from '../shared/PageShell';
import { formatCents } from '../shared/money';
import { dateLabel } from '../shared/format';
import { errorText, finSend, useFinQuery } from '../shared/useFinanceApi';
import { FormDialog } from '../../functions/enquiries/bits';
import { toast } from '../shared/Toasts';
import type { BudgetVersion, FinMeta, VersionsDoc } from './types';

const SOURCE_LABEL: Record<BudgetVersion['source'], string> = { import: 'Imported', manual: 'Typed in', forecast: 'From the forecast' };

function NewVersionDialog({ venueId, fy, versions, onClose, onCreated }: { venueId: string; fy: number; versions: BudgetVersion[]; onClose: () => void; onCreated: () => void }) {
  const [label, setLabel] = useState(`FY${fy} budget`);
  const [copyFrom, setCopyFrom] = useState<string>('');
  const [notes, setNotes] = useState('');
  return (
    <FormDialog
      title="New budget version"
      submitLabel="Create"
      onClose={onClose}
      onSubmit={async () => {
        await finSend('POST', '/api/finance/budgets/versions', { venue_id: venueId, fiscal_year: fy, label: label.trim() || null, source: 'manual', notes: notes.trim() || null, copy_from_version_id: copyFrom || undefined });
        toast('Version created — it is a draft until you lock it.', 'ok');
        onCreated();
      }}
    >
      <FormGrid>
        <Field label="Label" id="fin-version-label">
          <input id="fin-version-label" className="n-input" value={label} onChange={(e) => setLabel(e.target.value)} style={{ width: '100%' }} />
        </Field>
        <Field label="Start from" id="fin-version-copy" hint="Copies that version's days into the new one.">
          <select id="fin-version-copy" className="n-select" value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)} style={{ width: '100%' }}>
            <option value="">An empty version</option>
            {versions.map((v) => <option key={v.id} value={v.id}>{v.label || SOURCE_LABEL[v.source]} · {v.days} days</option>)}
          </select>
        </Field>
      </FormGrid>
      <Field label="Notes" id="fin-version-notes">
        <textarea id="fin-version-notes" className="n-input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} style={{ width: '100%' }} />
      </Field>
    </FormDialog>
  );
}

export default function BudgetVersions({ meta, venueId, fy, canWrite }: { meta: FinMeta; venueId: string; fy: number; canWrite: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const q = useFinQuery<VersionsDoc>('/api/finance/budgets/versions', { venue_id: venueId, fiscal_year: fy }, refresh);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const currency = meta.currency;

  const toggleLock = async (v: BudgetVersion) => {
    setBusy(v.id);
    try {
      await finSend('POST', `/api/finance/budgets/versions/${encodeURIComponent(v.id)}/${v.locked_at ? 'unlock' : 'lock'}`);
      toast(v.locked_at ? 'Unlocked — it is a draft again.' : 'Locked — this is now the current budget.', 'ok');
      setRefresh((n) => n + 1);
    } catch (e) {
      toast(errorText(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  const current = q.data?.current_version_id ?? null;
  const columns: Column<BudgetVersion>[] = [
    { key: 'label', label: 'Version', value: (v) => v.label || SOURCE_LABEL[v.source], render: (v) => (
      <span>
        <span style={{ fontWeight: 500 }}>{v.label || SOURCE_LABEL[v.source]}</span>
        {v.id === current && <Badge tone="ok" title="The latest locked version — what the Summary and the day grid use">Current</Badge>}
        {v.notes && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{v.notes}</div>}
      </span>
    ) },
    { key: 'source', label: 'Source', value: (v) => SOURCE_LABEL[v.source], hideOnPhone: true },
    { key: 'days', label: 'Days', align: 'right' },
    { key: 'total_cents', label: 'Total', align: 'right', value: (v) => v.total_cents, csv: (v) => v.total_cents / 100, csvLabel: 'Total ($)', render: (v) => formatCents(v.total_cents, currency, 0) },
    { key: 'locked_at', label: 'Status', value: (v) => (v.locked_at ? `locked ${v.locked_at.slice(0, 10)}` : 'draft'), render: (v) => (v.locked_at ? <Badge tone="info" title={`Locked ${dateLabel(v.locked_at.slice(0, 10))}`}>Locked</Badge> : <Badge tone="neutral">Draft</Badge>) },
    { key: 'created_at', label: 'Created', value: (v) => v.created_at.slice(0, 10), render: (v) => dateLabel(v.created_at.slice(0, 10)), hideOnPhone: true },
    ...(canWrite ? [{
      key: 'actions', label: '', sortable: false, align: 'right' as const, value: () => '', csv: () => null,
      render: (v: BudgetVersion) => (
        <Button size="xs" variant="quiet" icon={v.locked_at ? LockOpen : Lock} onClick={() => void toggleLock(v)} disabled={busy === v.id}>
          {v.locked_at ? 'Unlock' : 'Lock'}
        </Button>
      ),
    }] : []),
  ];

  return (
    <Section
      title={`Versions · FY${fy}`}
      meta={q.data ? `${q.data.versions.length} version${q.data.versions.length === 1 ? '' : 's'}` : undefined}
      actions={canWrite ? <Button size="sm" variant="primary" icon={Plus} onClick={() => setCreating(true)}>New version</Button> : undefined}
    >
      {q.error ? (
        <PageState kind="error" title="Couldn’t load the versions." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} />
      ) : !q.data ? (
        <PageState kind="loading" title="Loading…" />
      ) : (
        <DataTable
          columns={columns}
          rows={q.data.versions}
          rowKey={(v) => v.id}
          csvName={`budget versions FY${fy}`}
          emptyTitle="No budget versions for this year."
          emptyDetail={canWrite ? 'Import a budget on the Import tab, or create a version and type it in on the day grid.' : undefined}
        />
      )}
      {creating && <NewVersionDialog venueId={venueId} fy={fy} versions={q.data?.versions ?? []} onClose={() => setCreating(false)} onCreated={() => setRefresh((n) => n + 1)} />}
    </Section>
  );
}
