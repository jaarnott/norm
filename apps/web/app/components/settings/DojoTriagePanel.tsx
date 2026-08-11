'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';
import InvoicePdfPane from './InvoicePdfPane';
import ReplicaCompareView, { type ReplicaCompare } from './ReplicaCompareView';
import SenseiProposalCard, { type DojoAnalysis } from './SenseiProposalCard';

/**
 * The Dojo page: triage ground for invoice extraction. Lists every venue's
 * outstanding invoices plus the in-dojo samples still awaiting review;
 * expanding a row stages a DRAFT sample (invisible to regression) and opens
 * the full toolkit — PDF beside the Extracted/Loaded/Diff invoice view, Run,
 * ask the sensei, apply spec updates on the spot. "Add to dojo" promotes the draft
 * into the per-supplier regression suite.
 */

interface OutstandingRow {
  venue_id: string;
  venue_name: string;
  invoice_id: string;
  reference: string | null;
  supplier_name: string | null;
  issued_at: string | null;
  total: number | null;
  has_file: boolean;
  sample_id: string | null;
  draft: boolean;
  in_dojo: boolean;
}
interface PendingRow {
  id: string;
  spec_id: string;
  spec_name: string;
  label: string;
  last_status: string;
  diff_count: number;
  has_expected: boolean;
  draft: boolean;
  replica_warning_count?: number;
  analysis_status?: string | null;
}
interface Overview {
  outstanding: OutstandingRow[];
  pending_review: PendingRow[];
  errors: { venue_name: string; error: string }[];
}

interface RunView {
  status: string;
  replica: Record<string, unknown> | null;
  replicaDiffs: unknown[];
  replicaCompare: ReplicaCompare | null;
  hasExpectedReplica: boolean;
}

interface OpenState {
  key: string; // invoice_id or sample id — one row open at a time
  sampleId: string | null;
  draft: boolean;
  phase: 'staging' | 'running' | 'ready' | 'error';
  note?: string;
  view?: RunView | null;
}

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  pass: { bg: '#d1fae5', fg: '#065f46' },
  fail: { bg: '#fee2e2', fg: '#991b1b' },
  error: { bg: '#fee2e2', fg: '#991b1b' },
  new: { bg: '#fdf6e7', fg: '#8a6d3b' },
};
function StatusBadge({ status }: { status: string }) {
  const c = STATUS_COLORS[status] || STATUS_COLORS.new;
  return (
    <span style={{ fontSize: '0.62rem', fontWeight: 700, padding: '1px 7px', borderRadius: 4, background: c.bg, color: c.fg, whiteSpace: 'nowrap' }}>
      {status.toUpperCase()}
    </span>
  );
}

const chip = (bg: string, fg: string, border: string): React.CSSProperties => ({
  fontSize: '0.62rem', fontWeight: 700, color: fg, background: bg, border: `1px solid ${border}`, borderRadius: 4, padding: '1px 7px', whiteSpace: 'nowrap',
});

const money = (v: number | null) => (typeof v === 'number' ? `$${v.toFixed(2)}` : '—');
const day = (v: string | null) => (v ? String(v).slice(0, 10) : '—');

export default function DojoTriagePanel({ onBack }: { onBack: () => void }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<OpenState | null>(null);
  const [analysisView, setAnalysisView] = useState<{ sampleId: string; analysis: DojoAnalysis } | null>(null);
  const [analysing, setAnalysing] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [busy, setBusy] = useState<string | null>(null); // promote/discard in flight

  const load = useCallback(async () => {
    try {
      const res = await apiFetch('/api/supplier-invoice-specs/dojo/overview');
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setOverview({ outstanding: data.outstanding || [], pending_review: data.pending_review || [], errors: data.errors || [] });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the dojo overview');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  // Poll while any sample is mid-analysis. Add-to-dojo and the sensei both
  // run in the background, so without this the row keeps showing "sensei
  // analysing…" long after the proposal landed (Trents 206688357 finished at
  // 09:38 and still read as running, 10 Aug 2026). The server reports an
  // interrupted run as failed, so this loop always terminates.
  const pending = overview?.pending_review;
  useEffect(() => {
    if (!pending?.some((s) => s.analysis_status === 'running')) return;
    const t = setInterval(() => { load(); }, 5000);
    return () => clearInterval(t);
  }, [pending, load]);

  const toRunView = (data: Record<string, unknown>): RunView => ({
    status: String(data.status ?? 'new'),
    replica: (data.replica as Record<string, unknown>) ?? null,
    replicaDiffs: (data.replica_diffs as unknown[]) ?? [],
    replicaCompare: (data.replica_compare as ReplicaCompare) ?? null,
    hasExpectedReplica: !!data.expected_replica,
  });

  // Always ask — the endpoint answers {analysis: null} when there is none.
  // This used to be gated on a status the caller had to supply, and the
  // outstanding-invoice rows have no status to supply: a draft sample with a
  // ready proposal was therefore unreachable from the only page that lists
  // it. The sensei's answer existed on the server and nothing ever fetched it.
  const loadAnalysis = async (sampleId: string) => {
    if (analysisView?.sampleId !== sampleId) setAnalysisView(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/analysis`);
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.analysis) setAnalysisView({ sampleId, analysis: data.analysis });
    } catch { /* proposal stays hidden */ }
  };

  const runSample = async (sampleId: string, key: string, draft: boolean) => {
    setOpen({ key, sampleId, draft, phase: 'running', note: 'Reading the invoice copy — takes ~30-60 seconds…' });
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/run`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setOpen({ key, sampleId, draft, phase: 'ready', view: toRunView(data) });
      load();
    } catch (e) {
      setOpen({ key, sampleId, draft, phase: 'error', note: e instanceof Error ? e.message : 'Run failed' });
    }
  };

  const openSample = async (sampleId: string, key: string, draft: boolean) => {
    setOpen({ key, sampleId, draft, phase: 'running', note: 'Loading the stored run…' });
    loadAnalysis(sampleId);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/last-run`);
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.replica) {
        setOpen({ key, sampleId, draft, phase: 'ready', view: toRunView(data) });
        return;
      }
      // No stored run (or no replica) — build one now.
      await runSample(sampleId, key, draft);
    } catch {
      await runSample(sampleId, key, draft);
    }
  };

  const openOutstanding = async (row: OutstandingRow) => {
    if (open?.key === row.invoice_id) { setOpen(null); return; }
    if (!row.has_file) {
      setOpen({ key: row.invoice_id, sampleId: null, draft: true, phase: 'error', note: 'No invoice copy attached in Loaded — nothing to extract.' });
      return;
    }
    if (row.sample_id) {
      await openSample(row.sample_id, row.invoice_id, row.draft);
      return;
    }
    setOpen({ key: row.invoice_id, sampleId: null, draft: true, phase: 'staging', note: 'Staging the invoice…' });
    try {
      const res = await apiFetch('/api/supplier-invoice-specs/dojo/stage', {
        method: 'POST',
        body: JSON.stringify({ venue_id: row.venue_id, invoice_id: row.invoice_id }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      await runSample(data.sample_id, row.invoice_id, true);
    } catch (e) {
      setOpen({ key: row.invoice_id, sampleId: null, draft: true, phase: 'error', note: e instanceof Error ? e.message : 'Could not stage the invoice' });
    }
  };

  const analyse = async (sampleId: string, feedback?: string) => {
    if (analysing) return;
    setAnalysing(sampleId);
    setError(null);
    setTimeout(() => { load(); }, 1500); // server marks 'running' — chips update
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/analyse`, {
        method: 'POST',
        ...(feedback?.trim() ? { body: JSON.stringify({ feedback: feedback.trim() }) } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      if (data.analysis) setAnalysisView({ sampleId, analysis: data.analysis });
      load();
    } catch (e) {
      setError(
        e instanceof Error && !/fetch|network/i.test(e.message)
          ? e.message
          : 'Lost the connection to the sensei — it keeps working on the server; refresh in a minute.',
      );
      load();
    } finally {
      setAnalysing(null);
    }
  };

  const applyProposal = async (sampleId: string) => {
    if (applying) return;
    setApplying(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/apply-analysis`, {
        method: 'POST',
        body: JSON.stringify({ apply_spec: true, save_expected: true }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setAnalysisView(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not apply the proposal');
    } finally {
      setApplying(false);
    }
  };

  const dismissProposal = async (sampleId: string) => {
    await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/dismiss-analysis`, { method: 'POST' }).catch(() => {});
    setAnalysisView(null);
    load();
  };

  const promote = async (sampleId: string) => {
    setBusy(sampleId);
    try {
      await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/promote`, { method: 'POST' });
      setOpen((o) => (o && o.sampleId === sampleId ? { ...o, draft: false } : o));
      load();
    } finally {
      setBusy(null);
    }
  };

  const discard = async (sampleId: string) => {
    if (!window.confirm('Discard this draft? The staged copy and any analysis on it are deleted.')) return;
    setBusy(sampleId);
    try {
      await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}`, { method: 'DELETE' });
      setOpen(null);
      setAnalysisView(null);
      load();
    } finally {
      setBusy(null);
    }
  };

  const blessReplica = async (sampleId: string) => {
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/replica-expected`, { method: 'POST' });
      if (res.ok && open?.sampleId === sampleId) {
        setOpen((o) => (o && o.view ? { ...o, view: { ...o.view, hasExpectedReplica: true } } : o));
      }
    } catch { /* stays unblessed */ }
  };

  /* ---- The expanded toolkit: PDF | proposal + invoice view -------------- */
  const toolkit = (sampleId: string) => {
    const view = open?.view;
    return (
      <div style={{ marginTop: 10, display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 420px', minWidth: 320, position: 'sticky', top: 8 }}>
          <InvoicePdfPane sampleId={sampleId} />
        </div>
        <div style={{ flex: '1 1 420px', minWidth: 320 }}>
          {analysisView && analysisView.sampleId === sampleId && (
            <div style={{ marginBottom: 12 }}>
              <SenseiProposalCard
                sampleId={sampleId}
                analysis={analysisView.analysis}
                analysing={analysing === sampleId}
                applying={applying}
                onReanalyse={(fb) => analyse(sampleId, fb)}
                onApply={() => applyProposal(sampleId)}
                onDismiss={() => dismissProposal(sampleId)}
                onClose={() => setAnalysisView(null)}
              />
            </div>
          )}
          {view?.replicaCompare && view.replica ? (
            <ReplicaCompareView
              compare={view.replicaCompare}
              replicaDoc={view.replica}
              resolutionLog={(view.replica.resolution_log as string[]) ?? undefined}
              warnings={(view.replica.warnings as string[]) ?? undefined}
              onBless={() => blessReplica(sampleId)}
              blessed={view.hasExpectedReplica}
              onAnalyse={() => analyse(sampleId)}
              analysing={analysing === sampleId}
            />
          ) : (
            <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, background: '#faf9f7', padding: '1rem', fontSize: '0.74rem', color: '#6b655c' }}>
              No invoice view for this run — press Run to rebuild it.
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <button type="button" onClick={() => open && runSample(sampleId, open.key, open.draft)}
              title="re-extract under the CURRENT prompts (e.g. after applying a spec update)"
              style={{ fontSize: '0.72rem', padding: '4px 14px', border: '1px solid #2e7d4f', borderRadius: 6, background: '#fff', color: '#2e7d4f', cursor: 'pointer' }}>
              Run
            </button>
            {open?.draft && (
              <button type="button" onClick={() => promote(sampleId)} disabled={busy === sampleId}
                title="promote into the per-supplier dojo — it joins Run Dojo regression from here on"
                style={{ fontSize: '0.72rem', padding: '4px 14px', border: 'none', borderRadius: 6, background: '#2e7d4f', color: '#fff', cursor: busy ? 'wait' : 'pointer' }}>
                {busy === sampleId ? 'Adding…' : 'Add to dojo'}
              </button>
            )}
            {open?.draft && (
              <button type="button" onClick={() => discard(sampleId)} disabled={busy === sampleId}
                style={{ fontSize: '0.72rem', padding: '4px 14px', border: '1px solid #f0c0ba', borderRadius: 6, background: '#fff', color: '#c0392b', cursor: busy ? 'wait' : 'pointer' }}>
                Discard draft
              </button>
            )}
            <button type="button" onClick={() => { setOpen(null); setAnalysisView(null); }}
              style={{ fontSize: '0.72rem', padding: '4px 14px', border: '1px solid #ccc', borderRadius: 6, background: '#fff', color: '#555', cursor: 'pointer' }}>
              Close
            </button>
          </div>
        </div>
      </div>
    );
  };

  const progress = (note?: string, isError?: boolean) => (
    <div style={{ marginTop: 8, fontSize: '0.74rem', color: isError ? '#c0392b' : '#8a6d3b' }}>{note}</div>
  );

  const byVenue = new Map<string, OutstandingRow[]>();
  for (const r of overview?.outstanding ?? []) {
    byVenue.set(r.venue_name, [...(byVenue.get(r.venue_name) ?? []), r]);
  }

  return (
    <div>
      <button type="button" onClick={onBack}
        style={{ border: 'none', background: 'none', padding: 0, marginBottom: 8, fontSize: '0.74rem', color: '#8a6d3b', cursor: 'pointer', fontFamily: 'inherit' }}>
        ← Back to supplier specs
      </button>
      <h3 style={{ margin: '0 0 4px', fontSize: '1rem' }}>Dojo</h3>
      <div style={{ fontSize: '0.74rem', color: '#777', marginBottom: 14, maxWidth: 720 }}>
        The testing ground: open any outstanding invoice side-by-side with what Norm extracts,
        let the sensei tune the supplier spec, then Add to dojo to keep it as a regression sample.
      </div>
      {error && <div style={{ color: '#c0392b', fontSize: '0.78rem', marginBottom: 10 }}>{error}</div>}
      {loading && <div style={{ fontSize: '0.78rem', color: '#999' }}>Loading venues and invoices…</div>}

      {/* ---- In the dojo, awaiting review -------------------------------- */}
      {(overview?.pending_review.length ?? 0) > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', marginBottom: 6 }}>
            In the dojo, awaiting review ({overview!.pending_review.length})
          </div>
          {overview!.pending_review.map((s) => (
            <div key={s.id}>
              <div onClick={() => (open?.key === s.id ? setOpen(null) : openSample(s.id, s.id, false))}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderBottom: '1px solid #f4f4f4', fontSize: '0.78rem', cursor: 'pointer', background: open?.key === s.id ? '#faf8f4' : undefined }}>
                <StatusBadge status={s.last_status} />
                {s.analysis_status === 'running' && <span style={chip('#dbeafe', '#1d4ed8', '#bfdbfe')}>sensei analysing…</span>}
                {s.analysis_status === 'ready' && <span style={chip('#d1fae5', '#065f46', '#a7dcc4')}>sensei proposal</span>}
                {s.analysis_status === 'not_green' && <span style={chip('#fdf6e7', '#8a6d3b', '#ecd9ac')}>sensei not green</span>}
                {s.analysis_status === 'failed' && <span style={chip('#fee2e2', '#991b1b', '#f5c6c6')}>sensei failed</span>}
                {!s.has_expected && <span style={chip('#f4f4f4', '#777', '#e2e2e2')}>no baseline</span>}
                <span style={{ fontWeight: 600 }}>{s.spec_name}</span>
                <span style={{ color: '#777', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.label}</span>
              </div>
              {open?.key === s.id && (
                <div style={{ padding: '0 0 14px' }}>
                  {open.phase !== 'ready' && progress(open.note, open.phase === 'error')}
                  {open.sampleId && open.phase === 'ready' && toolkit(open.sampleId)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ---- Outstanding invoices, all venues ---------------------------- */}
      <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', marginBottom: 6 }}>
        Outstanding invoices
      </div>
      {(overview?.errors ?? []).map((e, i) => (
        <div key={i} style={{ fontSize: '0.7rem', color: '#a02b2b', marginBottom: 4 }}>⚠ {e.venue_name}: {e.error}</div>
      ))}
      {!loading && (overview?.outstanding.length ?? 0) === 0 && (
        <div style={{ fontSize: '0.75rem', color: '#aaa', padding: '6px 0' }}>No outstanding invoices anywhere. 🎉</div>
      )}
      {[...byVenue.entries()].map(([venueName, rows]) => (
        <div key={venueName} style={{ marginBottom: 14 }}>
          <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#555', margin: '8px 0 2px' }}>{venueName}</div>
          {rows.map((r) => (
            <div key={r.invoice_id}>
              <div onClick={() => openOutstanding(r)}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderBottom: '1px solid #f4f4f4', fontSize: '0.78rem', cursor: 'pointer', background: open?.key === r.invoice_id ? '#faf8f4' : undefined }}>
                <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{r.reference || r.invoice_id.slice(0, 8)}</span>
                <span style={{ color: '#555', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.supplier_name || '—'}</span>
                <span style={{ color: '#999', whiteSpace: 'nowrap' }}>{day(r.issued_at)}</span>
                <span style={{ marginLeft: 'auto', color: '#555', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{money(r.total)}</span>
                {!r.has_file && <span style={chip('#f4f4f4', '#999', '#e2e2e2')}>no copy</span>}
                {r.in_dojo && <span style={chip('#d1fae5', '#065f46', '#a7dcc4')}>in dojo</span>}
                {r.draft && <span style={chip('#fdf6e7', '#8a6d3b', '#ecd9ac')}>draft</span>}
              </div>
              {open?.key === r.invoice_id && (
                <div style={{ padding: '0 0 14px' }}>
                  {open.phase !== 'ready' && progress(open.note, open.phase === 'error')}
                  {open.sampleId && open.phase === 'ready' && toolkit(open.sampleId)}
                </div>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
