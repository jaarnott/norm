'use client';

import { useCallback, useEffect, useState } from 'react';
import { Play, RefreshCw } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Button from '../ui/Button';
import PageState from '../ui/PageState';
import { UnitCheckLines, type Proposal } from './UnitCheckLines';

/**
 * Settings → Invoice Dojo → Daily unit check (owner, 18 Oct 2026; its own page
 * again in the Invoice Dojo menu, 19 Oct). Each morning's run, and the lines it
 * flagged: units that look wrong, and units Norm wasn't sure of. The same
 * lines show on the Activity page, filtered by its dates and venues.
 */

interface Run {
  id: string;
  window_start: string | null;
  window_end: string | null;
  started_at: string | null;
  ok: boolean | null;
  error: string | null;
  summary: {
    invoices?: number; lines?: number; proposals?: number;
    venues?: { name: string; invoices: number; lines: number; proposals: number; corrections?: number; to_confirm?: number; errors?: string[] }[];
  };
}

function when(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
}

const chip = (on: boolean): React.CSSProperties => ({
  padding: '4px 10px',
  borderRadius: 999,
  border: '1px solid var(--line)',
  background: on ? 'var(--surface-alt)' : 'transparent',
  color: on ? 'var(--text)' : 'var(--text-soft)',
  fontSize: 'var(--fs-sm)',
  fontWeight: on ? 600 : 500,
  cursor: 'pointer',
});

export default function UnitCheckPanel() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/dojo/unit-check${runId ? `?run_id=${runId}` : ''}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setRuns(data.runs || []);
      setProposals(data.proposals || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the unit check');
    } finally {
      setLoading(false);
    }
  }, [runId]);
  useEffect(() => { load(); }, [load]);

  const runNow = async () => {
    setStarted(true);
    try {
      await apiFetch('/api/supplier-invoice-specs/dojo/unit-check/run', { method: 'POST' });
    } finally {
      setTimeout(() => { setStarted(false); load(); }, 15000);
    }
  };

  const selected = runs.find((r) => r.id === runId) || null;

  return (
    <div style={{ lineHeight: 1.45, color: 'var(--text)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
        <h3 style={{ flex: 1, minWidth: 180, margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3 }}>Daily unit check</h3>
        <Button size="sm" icon={Play} onClick={runNow} disabled={started}
          title="Check what Norm has received since the last run, now">
          {started ? 'Running…' : 'Run now'}
        </Button>
        <Button size="sm" icon={RefreshCw} onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </Button>
      </div>
      <div style={{ marginBottom: 14, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        Each morning Norm compares every line it received since the last check with the invoice it read and with its
        own catalogue. It lists the lines it thinks are booked in the wrong unit, and the lines it booked on an answer
        it wasn&rsquo;t sure of. Nothing changes in Loaded until you act here, and only the line&rsquo;s unit changes:
        its quantity and price stay as received. When you confirm or change an unsure line, Norm keeps your answer for
        that product, so it isn&rsquo;t asked about again.
      </div>

      <div role="group" aria-label="Runs" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
        <button type="button" aria-pressed={runId === null} onClick={() => setRunId(null)} style={chip(runId === null)}>
          Everything to review
        </button>
        {runs.slice(0, 10).map((r) => (
          <button key={r.id} type="button" aria-pressed={runId === r.id} onClick={() => setRunId(r.id)} style={chip(runId === r.id)}
            title={r.ok === false ? `Didn't finish: ${r.error || ''}` : `${r.summary.lines ?? 0} lines on ${r.summary.invoices ?? 0} invoices`}>
            {when(r.window_end || r.started_at)}{r.ok === false ? ' · failed' : ` · ${r.summary.proposals ?? 0}`}
          </button>
        ))}
      </div>

      {selected && (
        <div className="n-card" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 'var(--fs-sm)' }}>
          {selected.ok === false ? (
            <span style={{ color: 'var(--error)' }}>This run didn&rsquo;t finish: {selected.error}</span>
          ) : (
            <>
              Checked {selected.summary.lines ?? 0} lines on {selected.summary.invoices ?? 0} invoices received
              {selected.window_start ? ` from ${when(selected.window_start)}` : ''} to {when(selected.window_end)}.
              {(selected.summary.venues || []).filter((v) => v.invoices).map((v) => (
                <div key={v.name} style={{ color: 'var(--text-soft)' }}>
                  {v.name}: {v.lines} lines on {v.invoices} invoices, {v.corrections ?? v.proposals} correction{(v.corrections ?? v.proposals) === 1 ? '' : 's'}, {v.to_confirm ?? 0} to confirm
                  {v.errors && v.errors.length ? ` (${v.errors.length} couldn't be read)` : ''}
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {error && <div style={{ marginBottom: 12 }}><PageState kind="error" title={error} /></div>}
      {loading && !proposals.length && <PageState kind="loading" title="Loading…" />}
      {!loading && !error && !proposals.length && (
        <PageState kind="empty" title={runId ? 'This run found nothing to correct' : 'Nothing to review'}
          detail={runs.length ? undefined : 'The first check runs tomorrow morning, or press Run now.'} />
      )}
      <UnitCheckLines proposals={proposals}
        onReplace={(np) => setProposals((list) => list.map((x) => (x.id === np.id ? np : x)))}
        onReload={load} />
    </div>
  );
}
