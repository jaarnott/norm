'use client';

import { useCallback, useEffect, useState } from 'react';
import { LoaderCircle, TriangleAlert } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Badge from '../ui/Badge';
import BackLink from '../ui/BackLink';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';
import DojoSampleView, { type DojoDiff, type ExtractionDoc, type ReplicaDoc } from './DojoSampleView';
import InvoicePdfPane from './InvoicePdfPane';
import ReplicaCompareView, { type ReplicaCompare } from './ReplicaCompareView';
import SenseiProposalCard, { type DojoAnalysis } from './SenseiProposalCard';
import { formatMoney } from '../../lib/format';

/**
 * The Dojo page: triage ground for invoice extraction. Lists every venue's
 * outstanding invoices plus the in-dojo samples still awaiting review;
 * expanding a row stages a DRAFT sample (invisible to regression) and opens
 * the full toolkit — PDF beside the Extracted/Loaded/Diff invoice view, Run,
 * ask the sensei, apply spec updates on the spot. "Keep as sample" promotes the
 * draft into the per-supplier regression suite. Intake is the invoice card's
 * "Can't receive" button (the one door into the dojo, any user).
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
  analysis_phase?: string | null;
  analysis_stale?: boolean;
  analysis_attempts?: number;
  analysis_error?: string | null;
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
  // The stored baseline (expected extraction values) and the last run's
  // extraction — what the sensei tests against, shown in its own section.
  expected: ExtractionDoc | null;
  extraction: ExtractionDoc | null;
  diffs: DojoDiff[];
}

interface OpenState {
  key: string; // invoice_id or sample id — one row open at a time
  sampleId: string | null;
  draft: boolean;
  phase: 'staging' | 'running' | 'ready' | 'error';
  note?: string;
  view?: RunView | null;
}

const sectionTitle: React.CSSProperties = { margin: '0 0 8px', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' };

// The lists are white cards of rows. `clip`, not `hidden`, rounds the open
// row's band into the card's corners without making the card a scroll
// container — that would unstick the PDF pane inside the toolkit.
const listCard: React.CSSProperties = { overflow: 'clip' };

// One row of a list. The open row and the toolkit under it share a tinted
// band, as an expanded order does on the Orders page, so the white panes
// inside read as belonging to that row.
const rowStyle = (first: boolean, isOpen: boolean): React.CSSProperties => ({
  display: 'flex',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: '6px 10px',
  padding: '10px 12px',
  borderTop: first ? 'none' : '1px solid var(--line)',
  fontSize: 'var(--fs-base)',
  color: 'var(--text)',
  cursor: 'pointer',
  background: isOpen ? 'var(--surface-alt)' : undefined,
});
const band: React.CSSProperties = { padding: '2px 12px 14px', background: 'var(--surface-alt)' };

const money = (v: number | null) => (typeof v === 'number' ? formatMoney(v) : '—');
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

  // Two fetches, deliberately split (16 Aug 2026): the awaiting-review list
  // is the part of the page people came for and is a cheap config-DB read —
  // it must render immediately. The outstanding sweep calls LoadedHub per
  // venue and takes seconds; it exists only to make adding invoices to the
  // dojo easier, so it loads underneath, afterwards.
  const loadPending = useCallback(async () => {
    try {
      const res = await apiFetch('/api/supplier-invoice-specs/dojo/pending');
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setOverview((o) => ({
        outstanding: o?.outstanding ?? [],
        errors: o?.errors ?? [],
        pending_review: data.pending_review || [],
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the dojo list');
    }
  }, []);
  const load = useCallback(async () => {
    loadPending();
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
  }, [loadPending]);
  useEffect(() => { load(); }, [load]);

  // Poll while any sample is queued or mid-analysis. The sensei queue runs
  // in the background, so without this the row would sit on a stale chip.
  //
  // Poll the per-sample ANALYSIS endpoint (config-DB read), not the full
  // overview: the overview calls LoadedHub for every venue, and stacking
  // that every 5 seconds froze the local site during a sensei run (16 Aug
  // 2026). Live state (phase, restarting, queued→running) updates the rows
  // in place; the full overview reloads once, when a run reaches a terminal
  // state.
  const pending = overview?.pending_review;
  useEffect(() => {
    const active = (pending ?? []).filter(
      (s) => s.analysis_status === 'running' || s.analysis_status === 'queued',
    );
    if (!active.length) return;
    const t = setInterval(async () => {
      try {
        let terminal = false;
        const updates = new Map<string, Partial<PendingRow>>();
        await Promise.all(active.map(async (s) => {
          const res = await apiFetch(`/api/supplier-invoice-specs/samples/${s.id}/analysis`);
          const a = res.ok ? (await res.json().catch(() => ({}))).analysis : null;
          if (!a) return;
          if (a.status !== 'running' && a.status !== 'queued') { terminal = true; return; }
          updates.set(s.id, {
            analysis_status: a.status,
            analysis_phase: a.phase ?? null,
            analysis_stale: !!a.stale,
            analysis_attempts: a.attempts ?? 0,
          });
        }));
        if (terminal) { loadPending(); return; }
        if (updates.size) {
          setOverview((o) => o && {
            ...o,
            pending_review: o.pending_review.map((row) =>
              updates.has(row.id) ? { ...row, ...updates.get(row.id) } : row),
          });
        }
      } catch { /* next tick tries again */ }
    }, 5000);
    return () => clearInterval(t);
  }, [pending, loadPending]);

  const toRunView = (data: Record<string, unknown>): RunView => ({
    status: String(data.status ?? 'new'),
    replica: (data.replica as Record<string, unknown>) ?? null,
    replicaDiffs: (data.replica_diffs as unknown[]) ?? [],
    replicaCompare: (data.replica_compare as ReplicaCompare) ?? null,
    expected: (data.expected as ExtractionDoc) ?? null,
    extraction: (data.extraction as ExtractionDoc) ?? null,
    diffs: (data.diffs as DojoDiff[]) ?? [],
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
      loadPending();
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
    try {
      // Enqueues and returns in milliseconds — the worker executes; the
      // chip poll tracks queued → analysing (phase) → terminal.
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/analyse`, {
        method: 'POST',
        ...(feedback?.trim() ? { body: JSON.stringify({ feedback: feedback.trim() }) } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      // A queued/running entry is not a proposal — never pop the view for it.
      const st = data.analysis?.status;
      if (data.analysis && (st === 'ready' || st === 'not_green' || st === 'applied')) {
        setAnalysisView({ sampleId, analysis: data.analysis });
      } else {
        setAnalysisView((v) => (v?.sampleId === sampleId ? null : v));
      }
      loadPending();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not queue the sensei — try again');
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
      loadPending();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not apply the proposal');
    } finally {
      setApplying(false);
    }
  };

  const dismissProposal = async (sampleId: string) => {
    await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/dismiss-analysis`, { method: 'POST' }).catch(() => {});
    setAnalysisView(null);
    loadPending();
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

  // Remove from the awaiting-review list — same DELETE as discard, but from
  // the row itself. The invoice stays cannot-receive in Loaded, so it shows
  // up under Outstanding invoices again on the next sweep.
  const removeSample = async (sampleId: string) => {
    if (!window.confirm('Remove this invoice from the dojo? The staged copy, its baseline and any sensei analysis are deleted. The invoice itself stays in Loaded and reappears under Outstanding invoices.')) return;
    setBusy(sampleId);
    try {
      await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}`, { method: 'DELETE' });
      setOpen((o) => (o && o.sampleId === sampleId ? null : o));
      setAnalysisView((v) => (v && v.sampleId === sampleId ? null : v));
      loadPending();
    } finally {
      setBusy(null);
    }
  };

  /* ---- The expanded toolkit: PDF | proposal + invoice view -------------- */
  const toolkit = (sampleId: string) => {
    const view = open?.view;
    return (
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
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
              onAnalyse={() => analyse(sampleId)}
              analysing={analysing === sampleId}
            />
          ) : (
            <div className="n-card" style={{ padding: 16, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
              {/* A replica that FAILED to build stores {replica: true, error}
                  — parrot the stored reason. The error is a snapshot of the
                  LAST run: a connection fixed since (or the env-portable
                  venue resolver, 16 Aug 2026) can make the next Run succeed,
                  so invite one rather than predicting failure. */}
              {(view?.replica as { error?: string } | null)?.error ? (
                <span style={{ color: 'var(--error)' }}>
                  The last run couldn’t build the invoice view: {(view!.replica as { error?: string }).error} — press Run to try a fresh build.
                </span>
              ) : (
                <>No invoice view for this run — press Run to rebuild it.</>
              )}
            </div>
          )}
          {/* The last run: stored baseline, extracted values and the built
              replica — always visible. The baseline is what the sensei tests
              against; editing here makes it admin-owned, which the sensei
              never overwrites. */}
          {view && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-soft)', marginBottom: 8 }}>
                Last run
              </div>
              <DojoSampleView
                key={`base-${sampleId}`}
                sampleId={sampleId}
                expected={view.expected}
                extraction={view.extraction}
                diffs={view.diffs}
                status={view.status}
                replica={(view.replicaCompare && view.replica ? view.replica : null) as ReplicaDoc | null}
                onSaved={(res) => {
                  setOpen((o) => o && o.view
                    ? { ...o, view: { ...o.view, status: res.status, diffs: res.diffs, expected: res.expected, extraction: res.extraction } }
                    : o);
                  loadPending();
                }}
              />
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <Button size="sm" onClick={() => open && runSample(sampleId, open.key, open.draft)}
              title="re-extract under the CURRENT prompts (e.g. after applying a spec update)">
              Run
            </Button>
            {open?.draft && (
              <Button size="sm" variant="primary" onClick={() => promote(sampleId)} disabled={busy === sampleId}
                title="keep this draft as a per-supplier regression sample — it joins Run Dojo from here on"
                style={busy ? { cursor: 'wait' } : undefined}>
                {busy === sampleId ? 'Keeping…' : 'Keep as sample'}
              </Button>
            )}
            {open?.draft && (
              <Button size="sm" variant="danger" onClick={() => discard(sampleId)} disabled={busy === sampleId}
                style={busy ? { cursor: 'wait' } : undefined}>
                Discard draft
              </Button>
            )}
            <Button size="sm" variant="quiet" onClick={() => { setOpen(null); setAnalysisView(null); }}>
              Close
            </Button>
          </div>
        </div>
      </div>
    );
  };

  // Staging/running shows a spinner; a failure reads as an error.
  const progress = (note?: string, isError?: boolean) => (
    <div role={isError ? 'alert' : 'status'}
      style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 'var(--fs-sm)', color: isError ? 'var(--error)' : 'var(--text-soft)' }}>
      <Icon icon={isError ? TriangleAlert : LoaderCircle} size="dense"
        style={{ marginTop: 2, ...(isError ? {} : { animation: 'n-spin 1s linear infinite' }) }} />
      <span>{note}</span>
    </div>
  );

  const byVenue = new Map<string, OutstandingRow[]>();
  for (const r of overview?.outstanding ?? []) {
    byVenue.set(r.venue_name, [...(byVenue.get(r.venue_name) ?? []), r]);
  }

  return (
    <div style={{ lineHeight: 1.45, color: 'var(--text)' }}>
      <div style={{ marginBottom: 8 }}>
        <BackLink label="Back to supplier specs" onClick={onBack} />
      </div>
      <h3 style={{ margin: '0 0 4px', fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>Dojo</h3>
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', marginBottom: 16, maxWidth: 720 }}>
        The testing ground: invoices arrive here when someone presses Can&rsquo;t receive on an
        invoice card. Open one side-by-side with what Norm extracts, run the sensei to tune the
        supplier spec, and apply its proposal to keep the invoice as a regression sample.
      </div>
      {error && <div style={{ marginBottom: 12 }}><PageState kind="error" title={error} /></div>}

      {/* ---- In the dojo, awaiting review -------------------------------- */}
      {(overview?.pending_review.length ?? 0) > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h4 style={sectionTitle}>
            In the dojo, awaiting review{' '}
            <span style={{ fontWeight: 400, color: 'var(--muted)' }}>({overview!.pending_review.length})</span>
          </h4>
          <div className="n-card" style={listCard}>
            {overview!.pending_review.map((s, i) => (
              <div key={s.id}>
                <div onClick={() => (open?.key === s.id ? setOpen(null) : openSample(s.id, s.id, false))}
                  style={rowStyle(i === 0, open?.key === s.id)}>
                  {/* Name first, state after — the row reads "which invoice,
                      then where it's at". The regression PASS/FAIL badge is
                      deliberately absent here: on a triage list it said
                      nothing actionable (16 Aug 2026). */}
                  <span style={{ fontWeight: 600 }}>{s.spec_name}</span>
                  <span style={{ minWidth: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.label}</span>
                  {s.analysis_status === 'queued' && (
                    <Badge tone="info" title="queued — the sensei worker picks it up within seconds">Sensei queued</Badge>
                  )}
                  {s.analysis_status === 'running' && !s.analysis_stale && (
                    <Badge tone="info">
                      Sensei analysing{s.analysis_phase ? ` — ${s.analysis_phase}` : '…'}
                    </Badge>
                  )}
                  {s.analysis_status === 'running' && s.analysis_stale && (
                    <Badge tone="warn" title="the executor died mid-run — the worker requeues and restarts it automatically">
                      Sensei restarting (attempt {(s.analysis_attempts ?? 0) + 1})…
                    </Badge>
                  )}
                  {s.analysis_status === 'ready' && <Badge tone="accent">Sensei proposal</Badge>}
                  {s.analysis_status === 'not_green' && <Badge tone="warn">Sensei not green</Badge>}
                  {s.analysis_status === 'failed' && (
                    <span title={s.analysis_error || 'the sensei run errored'} className="n-badge n-badge--error"
                      style={{ display: 'block', maxWidth: 340, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      Sensei failed{s.analysis_error ? ` — ${s.analysis_error}` : ''}
                    </span>
                  )}
                  {!s.has_expected && !s.analysis_status && (
                    <Badge title="no baseline yet — run the sensei (or set expected values by hand) to make this a regression sample">
                      Not processed
                    </Badge>
                  )}
                  {/* Run the sensei straight from the list — no need to open the
                      row. Enqueues instantly; hidden (with Remove) while a run
                      is queued or live. On rows the sensei has already worked,
                      it reads as a re-run. */}
                  {s.analysis_status !== 'queued' && (s.analysis_status !== 'running' || s.analysis_stale) && (
                    <span onClick={(e) => e.stopPropagation()}
                      style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <Button size="sm"
                        onClick={() => analyse(s.id)}
                        disabled={analysing === s.id}
                        title="Run the sensei on this invoice — it studies the extraction and drafts a supplier-spec update for review"
                        style={analysing === s.id ? { cursor: 'wait' } : undefined}>
                        {analysing === s.id ? 'Queueing…' : s.analysis_status ? 'Re-run sensei' : 'Run sensei'}
                      </Button>
                      <Button size="sm" variant="danger"
                        onClick={() => removeSample(s.id)}
                        disabled={busy === s.id}
                        title="Remove this invoice from the dojo — deletes the staged copy, its baseline and any sensei analysis"
                        style={busy === s.id ? { cursor: 'wait' } : undefined}>
                        {busy === s.id ? 'Removing…' : 'Remove'}
                      </Button>
                    </span>
                  )}
                </div>
                {open?.key === s.id && (
                  <div style={band}>
                    {open.phase !== 'ready' && progress(open.note, open.phase === 'error')}
                    {open.sampleId && open.phase === 'ready' && toolkit(open.sampleId)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ---- Outstanding invoices, all venues ---------------------------- */}
      <h4 style={sectionTitle}>Outstanding invoices</h4>
      {loading && <PageState kind="loading" title="Loading outstanding invoices from every venue…" />}
      {(overview?.errors ?? []).map((e, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginBottom: 6, fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>
          <Icon icon={TriangleAlert} size="dense" style={{ marginTop: 2 }} />
          <span>{e.venue_name}: {e.error}</span>
        </div>
      ))}
      {/* A failed load leaves `overview` null and reads as the error above, never
          as an empty list. (Not `error`: a failed action sets that too.) */}
      {!loading && overview && overview.outstanding.length === 0 && (
        <PageState kind="empty" title="No outstanding invoices anywhere." />
      )}
      {[...byVenue.entries()].map(([venueName, rows]) => (
        <div key={venueName} style={{ marginBottom: 16 }}>
          <div style={{ margin: '0 0 6px', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-soft)' }}>{venueName}</div>
          <div className="n-card" style={listCard}>
            {rows.map((r, i) => (
              <div key={r.invoice_id}>
                <div onClick={() => openOutstanding(r)} style={rowStyle(i === 0, open?.key === r.invoice_id)}>
                  <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{r.reference || r.invoice_id.slice(0, 8)}</span>
                  <span style={{ minWidth: 0, color: 'var(--text-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.supplier_name || '—'}</span>
                  <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>{day(r.issued_at)}</span>
                  <span style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{money(r.total)}</span>
                  {!r.has_file && <Badge>No copy</Badge>}
                  {r.in_dojo && <Badge tone="ok">In dojo</Badge>}
                  {r.draft && <Badge tone="info">Draft</Badge>}
                </div>
                {open?.key === r.invoice_id && (
                  <div style={band}>
                    {open.phase !== 'ready' && progress(open.note, open.phase === 'error')}
                    {open.sampleId && open.phase === 'ready' && toolkit(open.sampleId)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
