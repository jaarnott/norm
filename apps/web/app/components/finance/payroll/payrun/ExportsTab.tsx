'use client';

/**
 * Exports: the four Ace files generated from the close snapshot and kept
 * (every generation, newest first), each downloadable; the diff against
 * the previous generation; and the fallback pack — the four files plus a
 * re-key sheet of everything Norm authored for the week (plan §15).
 */
import { useState } from 'react';
import { Download, FileText, Package } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import PageState from '../../../ui/PageState';
import DataTable, { type Column } from '../../shared/DataTable';
import { Note, Section, Stack } from '../../shared/PageShell';
import { instantLabel, plural } from '../../shared/format';
import { useFinQuery, type FinanceApi } from '../../shared/useFinanceApi';
import { errorText } from '../../shared/useFinanceApi';
import type { ExportRow, Week } from '../types';
import { EXPORT_KINDS, FALLBACK_FILES, type ExportKind } from './logic';

interface GenerateResult {
  exports: ExportRow[];
  diffs: Record<string, { added?: string[]; removed?: string[]; same?: boolean; [k: string]: unknown } | null>;
}

function totalsText(e: ExportRow): string {
  const t = e.totals || {};
  const parts: string[] = [];
  if (typeof t.hours === 'number') parts.push(`${t.hours.toFixed(2)} h`);
  if (typeof t.cost_cents === 'number') parts.push(`$${(t.cost_cents / 100).toFixed(2)}`);
  if (t.by_style && typeof t.by_style === 'object') parts.push(Object.entries(t.by_style as Record<string, number>).map(([s, n]) => `#${s}: ${n}`).join(', '));
  if (Array.isArray(t.skipped_zero_hours) && t.skipped_zero_hours.length) parts.push(`${t.skipped_zero_hours.length} zero-hour skipped`);
  if (Array.isArray(t.unsigned_styles) && t.unsigned_styles.length) parts.push(`styles not signed off: ${t.unsigned_styles.join(', ')}`);
  if (typeof t.amount_cents === 'number') parts.push(`$${(t.amount_cents / 100).toFixed(2)}`);
  return parts.join(' · ');
}

export default function ExportsTab({ week, api, canWrite, refresh, onChanged }: { week: Week; api: FinanceApi; canWrite: boolean; refresh: number; onChanged: () => void }) {
  const q = useFinQuery<{ exports: ExportRow[] }>(`/api/payroll/weeks/${week.id}/exports`, {}, refresh);
  const [kinds, setKinds] = useState<Set<ExportKind>>(() => new Set(EXPORT_KINDS.map((k) => k.kind)));
  const [result, setResult] = useState<GenerateResult | null>(null);
  const [genError, setGenError] = useState<string | null>(null);
  const [pack, setPack] = useState<{ files: Record<string, string>; notes: string[] } | null>(null);
  const [viewing, setViewing] = useState<ExportRow | null>(null);
  const tz = week.window?.timezone || 'Pacific/Auckland';

  // The refusal (a staff member without a pay code, a leave type with no
  // style) is shown here under the button, not as a toast.
  const generate = () => api.run('generate', async () => {
    setGenError(null);
    try {
      const r = await api.post<GenerateResult>(`/weeks/${week.id}/exports`, { kinds: [...kinds] });
      setResult(r);
      q.retry();
      onChanged();
    } catch (e) {
      setGenError(errorText(e));
    }
  });

  const view = async (e: ExportRow) => {
    const full = await api.get<ExportRow>(`/exports/${e.id}`);
    setViewing(full);
  };

  const columns: Column<ExportRow>[] = [
    { key: 'filename', label: 'File', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' }}>{r.filename}</span>, nowrap: true },
    { key: 'generated_at', label: 'Generated', render: (r) => instantLabel(r.generated_at, tz), nowrap: true },
    { key: 'line_count', label: 'Lines', align: 'right' },
    { key: 'totals', label: 'Totals', sortable: false, hideOnPhone: true, value: (r) => totalsText(r) },
    { key: 'content_sha256', label: 'Hash', hideOnPhone: true, sortable: false, render: (r) => <span title={r.content_sha256} style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{r.content_sha256.slice(0, 12)}</span> },
    { key: 'actions', label: '', sortable: false, align: 'right', render: (r) => (
      <span style={{ display: 'inline-flex', gap: 4 }}>
        <Button size="xs" variant="quiet" icon={FileText} onClick={() => void view(r)}>View</Button>
        <Button size="xs" variant="quiet" icon={Download} onClick={() => void api.download(`/exports/${r.id}/download`, r.filename)}>Download</Button>
      </span>
    ) },
  ];

  const exportsList = q.data?.exports ?? [];
  const latestByKind = new Map<string, ExportRow>();
  for (const e of exportsList) if (!latestByKind.has(e.kind)) latestByKind.set(e.kind, e);

  return (
    <Stack>
      <Section title="Generate the Ace files" meta={week.close_snapshot ? `from the close snapshot taken ${instantLabel(week.close_snapshot.taken_at, tz)}` : undefined}>
        {!week.close_snapshot && <Note tone="warn">No close snapshot yet. Start the close on this page first — exports are built from it, never from a live read.</Note>}
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', margin: '8px 0' }}>
          {EXPORT_KINDS.map((k) => (
            <label key={k.kind} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)' }}>
              <input type="checkbox" checked={kinds.has(k.kind)} onChange={(e) => setKinds((s) => { const n = new Set(s); if (e.target.checked) n.add(k.kind); else n.delete(k.kind); return n; })} />
              {k.label}
            </label>
          ))}
        </div>
        {canWrite && (
          <Button size="sm" variant="primary" onClick={generate} disabled={!week.close_snapshot || kinds.size === 0 || api.busy === 'generate'}>
            {api.busy === 'generate' ? 'Generating…' : 'Generate'}
          </Button>
        )}
        {genError && <div style={{ marginTop: 8 }}><Note tone="error">{genError}</Note></div>}
        {result && (
          <div style={{ marginTop: 10 }}>
            <Note tone="ok">
              Generated {plural(result.exports.length, 'file')}.{' '}
              {Object.entries(result.diffs || {}).map(([kind, d]) => {
                if (!d) return `${kind}: first generation`;
                if (d.same) return `${kind}: same as before`;
                const a = d.added?.length ?? 0;
                const r = d.removed?.length ?? 0;
                return `${kind}: ${a} line${a === 1 ? '' : 's'} added, ${r} removed since the previous file`;
              }).join(' · ')}
            </Note>
          </div>
        )}
      </Section>

      <Section title="Latest files" padded={false}>
        <div style={{ padding: '8px 16px 12px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {EXPORT_KINDS.map((k) => {
            const e = latestByKind.get(k.kind);
            return (
              <Button key={k.kind} size="sm" icon={Download} disabled={!e} onClick={() => e && void api.download(`/exports/${e.id}/download`, e.filename)} title={e ? `Generated ${instantLabel(e.generated_at, tz)}` : 'Not generated yet'}>
                {k.filename}{e ? '' : ' — not yet'}
              </Button>
            );
          })}
        </div>
      </Section>

      <Section title="Every generation" meta={exportsList.length ? plural(exportsList.length, 'file') : undefined} padded={false}>
        <div style={{ padding: '8px 16px 12px' }}>
          {q.loading && !q.data ? <PageState kind="loading" title="Loading…" /> : q.error && !q.data ? <PageState kind="error" title="Could not load the exports." detail={q.error} /> : (
            <DataTable columns={columns} rows={exportsList} rowKey={(r) => r.id} dense emptyTitle="No files generated for this week yet." />
          )}
        </div>
      </Section>

      <Section title="Fallback pack" meta="the four files plus a re-key sheet of everything keyed in Norm for this week" actions={(
        <Button size="sm" icon={Package} onClick={() => api.run('pack', async () => { const p = await api.get<{ files: Record<string, string>; notes?: string[] }>(`/weeks/${week.id}/fallback-pack`); setPack({ files: p.files, notes: p.notes ?? [] }); })} disabled={api.busy === 'pack'}>
          {api.busy === 'pack' ? 'Building…' : pack ? 'Build again' : 'Build the pack'}
        </Button>
      )}>
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          If a Norm file is wrong on a live week, head office takes this pack and re-keys the week in Orbit (until 3 Feb) or Ace, and voids the Norm draft journal on the Reconciliation page.
        </div>
        {pack && (
          <>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
              {FALLBACK_FILES.map((name) => (
                <Button key={name} size="sm" icon={Download} onClick={() => void api.download(`/weeks/${week.id}/fallback-pack/${name}`, name)}>
                  {name} <Badge tone={pack.files[name] ? 'neutral' : 'warn'}>{pack.files[name] ? `${pack.files[name].split(/\r?\n/).filter(Boolean).length} lines` : 'empty'}</Badge>
                </Button>
              ))}
            </div>
            {pack.notes.length > 0 && (
              <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 'var(--fs-sm)', color: 'var(--warn)' }}>
                {pack.notes.map((n) => <li key={n}>{n}</li>)}
              </ul>
            )}
          </>
        )}
      </Section>

      {viewing && (
        <Section title={viewing.filename} meta={`${viewing.line_count} lines`} actions={<Button size="xs" variant="quiet" onClick={() => setViewing(null)}>Close</Button>}>
          <pre style={{ margin: 0, padding: 10, background: 'var(--code-bg)', color: 'var(--code-text)', borderRadius: 'var(--radius)', fontSize: 'var(--fs-xs)', overflowX: 'auto', maxHeight: 360 }}>{viewing.content || '(empty file)'}</pre>
        </Section>
      )}
    </Stack>
  );
}
