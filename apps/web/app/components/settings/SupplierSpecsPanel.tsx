'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';
import DojoSampleView, { type DojoDiff, type ExtractionDoc } from './DojoSampleView';
import ReceiveInvoiceEditor from '../display/ReceiveInvoiceEditor';
import ReplicaCompareView, { type ReplicaCompare } from './ReplicaCompareView';
import InvoicePdfPane from './InvoicePdfPane';
import SenseiProposalCard, { type DojoAnalysis } from './SenseiProposalCard';
import AutopilotReportPanel from './AutopilotReportPanel';
import DojoTriagePanel from './DojoTriagePanel';

// Per-supplier invoice-extraction instructions + name aliases, matched by the
// review engine against the invoice's supplierName (normalized substring).
// Data maintenance only — the matching and injection live in the engine.
interface SupplierSpec {
  id: string;
  name: string;
  aliases: string[];
  instructions: string;
  enabled: boolean;
  updated_at: string | null;
}

const EMPTY: SupplierSpec = { id: '', name: '', aliases: [], instructions: '', enabled: true, updated_at: null };

// Reserved row: the MAIN extraction prompt applied to every supplier
// (supplier rows append to it). Server-guarded against rename/delete.
const MAIN_PROMPT_NAME = 'Main prompt';
const isMainPrompt = (s: SupplierSpec) => s.name === MAIN_PROMPT_NAME;

// Dojo: sample invoices stored per spec; each run re-extracts with the
// CURRENT prompts and diffs against the admin-accepted baseline.
interface DojoSample {
  id: string;
  spec_id: string;
  label: string;
  has_expected: boolean;
  last_status: string;
  last_run_at: string | null;
  diff_count: number;
  replica_warning_count?: number;
  source_venue_id?: string | null;
  analysis_status?: string | null;
  analysis_phase?: string | null;
  analysis_stale?: boolean;
  analysis_attempts?: number;
  analysis_error?: string | null;
  analysis_green?: boolean;
}

interface CandidateResult {
  samples: { id: string; label: string; status: string; diffs: unknown[] }[];
  passed: number;
  failed: number;
  errors: number;
  new: number;
}
// The two extraction-shaped value sets a sample carries: the admin/agent
// authored EXPECTED baseline and the last run's EXTRACTED values — never
// Loaded data. Rendered/edited by DojoSampleView.
interface DojoView {
  sampleId: string;
  status: string;
  expected: ExtractionDoc | null;
  extraction: ExtractionDoc | null;
  diffs: DojoDiff[];
  // The replica: our extraction resolved into a full working document,
  // scored against Loaded's own resolution (invoice-intake samples only).
  replica?: Record<string, unknown> | null;
  replicaDiffs?: DojoDiff[];
  replicaCompare?: ReplicaCompare | null;
}
interface DojoSummaryRow { spec_id: string; total: number; pass: number; fail: number; error: number; new: number }

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  pass: { bg: '#d1fae5', fg: '#065f46' },
  fail: { bg: '#fee2e2', fg: '#991b1b' },
  error: { bg: '#fee2e2', fg: '#991b1b' },
  new: { bg: '#fdf6e7', fg: '#8a6d3b' },
};

function StatusBadge({ status, count }: { status: string; count?: number }) {
  const c = STATUS_COLORS[status] || STATUS_COLORS.new;
  return (
    <span style={{ fontSize: '0.62rem', fontWeight: 700, padding: '1px 7px', borderRadius: 4, background: c.bg, color: c.fg, whiteSpace: 'nowrap' }}>
      {status.toUpperCase()}{count != null ? ` ${count}` : ''}
    </span>
  );
}

const labelStyle: React.CSSProperties = { fontSize: '0.75rem', fontWeight: 600, color: '#888', textTransform: 'uppercase' as const, marginBottom: 4, display: 'block' };
const inputStyle: React.CSSProperties = { width: '100%', padding: '6px 8px', border: '1px solid #ddd', borderRadius: 6, fontSize: '0.85rem', fontFamily: 'inherit', boxSizing: 'border-box' as const };

export default function SupplierSpecsPanel() {
  const [specs, setSpecs] = useState<SupplierSpec[]>([]);
  const [editing, setEditing] = useState<SupplierSpec | null>(null);
  const [aliasesText, setAliasesText] = useState('');
  const [isNew, setIsNew] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // ---- Dojo state ----
  const [samples, setSamples] = useState<DojoSample[]>([]);
  const [runningSample, setRunningSample] = useState<string | null>(null);
  const [dojoView, setDojoView] = useState<DojoView | null>(null);
  const [showDojo, setShowDojo] = useState(false);
  const [showReport, setShowReport] = useState(false);
  // Sample view tab: the extraction compare vs the resolved replica.
  const [dojoRunning, setDojoRunning] = useState(false);
  const [dojoSummary, setDojoSummary] = useState<Record<string, DojoSummaryRow>>({});

  const load = useCallback(async () => {
    try {
      const res = await apiFetch('/api/supplier-invoice-specs');
      if (res.ok) {
        const data = await res.json();
        setSpecs(data.specs || []);
      }
    } catch { /* transient — list stays as-is */ }
  }, []);
  const loadSummary = useCallback(async () => {
    try {
      const res = await apiFetch('/api/supplier-invoice-specs/dojo/summary');
      if (res.ok) {
        const data = await res.json();
        const map: Record<string, DojoSummaryRow> = {};
        for (const row of data.specs || []) map[row.spec_id] = row;
        setDojoSummary(map);
      }
    } catch { /* chips stay as-is */ }
  }, []);
  useEffect(() => { load(); loadSummary(); }, [load, loadSummary]);

  const loadSamples = useCallback(async (specId: string) => {
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/${specId}/samples`);
      if (res.ok) setSamples((await res.json()).samples || []);
    } catch { /* keep current list */ }
  }, []);

  const openEdit = (spec: SupplierSpec, fresh: boolean) => {
    setEditing({ ...spec });
    setAliasesText((spec.aliases || []).join('\n'));
    setIsNew(fresh);
    setError(null);
    setSamples([]);
    setDojoView(null);
    if (!fresh && spec.id) loadSamples(spec.id);
  };

  // ---- Dojo actions -----------------------------------------------------
  const runSample = async (sampleId: string) => {
    if (!editing?.id || runningSample) return;
    setRunningSample(sampleId);
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/run`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      if (data.error) setError(String(data.error));
      setDojoView({
        sampleId,
        status: data.status,
        expected: data.expected ?? null,
        extraction: data.extraction ?? null,
        diffs: data.diffs ?? [],
        replica: data.replica ?? null,
        replicaDiffs: data.replica_diffs ?? [],
        replicaCompare: data.replica_compare ?? null,
      });
      await loadSamples(editing.id);
      loadSummary();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Run failed');
    } finally {
      setRunningSample(null);
    }
  };

  const viewSample = async (sampleId: string) => {
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/last-run`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setDojoView({
        sampleId,
        status: data.status,
        expected: data.expected ?? null,
        extraction: data.extraction ?? null,
        diffs: data.diffs ?? [],
        replica: data.replica ?? null,
        replicaDiffs: data.replica_diffs ?? [],
        replicaCompare: data.replica_compare ?? null,
      });
      // A waiting proposal displays at the top of the invoice view — load it
      // with the sample; anything from another sample is stale.
      if (analysisView?.sampleId !== sampleId) setAnalysisView(null);
      const meta = samples.find((x) => x.id === sampleId);
      if (meta && (meta.analysis_status === 'ready' || meta.analysis_status === 'not_green')) {
        viewAnalysis(sampleId);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the last run');
    }
  };

  // ---- Analysis agent (proposals) ---------------------------------------
  const [analysisView, setAnalysisView] = useState<{ sampleId: string; analysis: DojoAnalysis } | null>(null);
  const [analysing, setAnalysing] = useState<string | null>(null);
  const [applyingAnalysis, setApplyingAnalysis] = useState(false);
  // ---- Candidate runs (Test against dojo) -------------------------------
  const [candidateRunning, setCandidateRunning] = useState(false);
  const [candidateResult, setCandidateResult] = useState<CandidateResult | null>(null);

  const viewAnalysis = async (sampleId: string) => {
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/analysis`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      if (data.analysis) setAnalysisView({ sampleId, analysis: data.analysis });
      else setError('No sensei proposal yet — ask the sensei from the sample view first.');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the analysis');
    }
  };

  // With `feedback` this is the admin REPLYING to the proposal thread: the
  // correction goes to the agent as authoritative and the whole loop
  // (re-analysis + candidate verification) runs again.
  const runAnalysis = async (sampleId: string, feedback?: string) => {
    if (!editing?.id || analysing) return;
    setAnalysing(sampleId);
    setError(null);
    const specId = editing.id;
    try {
      // Enqueues and returns in milliseconds; the worker executes and the
      // running-poll flips the chip queued → analysing → proposal ready.
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/analyse`, {
        method: 'POST',
        ...(feedback?.trim() ? { body: JSON.stringify({ feedback: feedback.trim() }) } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      // A queued/running entry is not a proposal — leave the view alone.
      const st = data.analysis?.status;
      if (data.analysis && (st === 'ready' || st === 'not_green' || st === 'applied')) {
        setAnalysisView({ sampleId, analysis: data.analysis });
      }
      await loadSamples(specId);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not queue the sensei — try again');
      loadSamples(specId);
    } finally {
      setAnalysing(null);
    }
  };

  const applyAnalysis = async (sampleId: string) => {
    if (!editing?.id || applyingAnalysis) return;
    setApplyingAnalysis(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/apply-analysis`, {
        method: 'POST',
        body: JSON.stringify({ apply_spec: true, save_expected: true }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      // The spec text changed server-side — reflect it in the open editor.
      if (typeof data.spec_instructions === 'string') {
        setEditing((prev) => (prev ? { ...prev, instructions: data.spec_instructions } : prev));
      }
      setAnalysisView(null);
      await loadSamples(editing.id);
      loadSummary();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not apply the proposal');
    } finally {
      setApplyingAnalysis(false);
    }
  };

  const dismissAnalysis = async (sampleId: string) => {
    if (!editing?.id) return;
    await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/dismiss-analysis`, { method: 'POST' }).catch(() => {});
    setAnalysisView(null);
    await loadSamples(editing.id);
  };

  const testAgainstDojo = async () => {
    if (!editing?.id || candidateRunning) return;
    setCandidateRunning(true);
    setCandidateResult(null);
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/${editing.id}/candidate-run`, {
        method: 'POST',
        body: JSON.stringify({ instructions: editing.instructions }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setCandidateResult(data as CandidateResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Candidate run failed');
    } finally {
      setCandidateRunning(false);
    }
  };

  // Poll while any analysis is queued or running (the sensei queue executes
  // in the background) — the TestsPanel interval pattern. loadSamples is a
  // cheap config-DB read, safe on an interval.
  useEffect(() => {
    if (!editing?.id) return;
    if (!samples.some((s) => s.analysis_status === 'running' || s.analysis_status === 'queued')) return;
    const specId = editing.id;
    const t = setInterval(() => { loadSamples(specId); }, 5000);
    return () => clearInterval(t);
  }, [editing?.id, samples, loadSamples]);

  const deleteSample = async (sampleId: string) => {
    if (!editing?.id) return;
    if (!window.confirm('Delete this sample invoice?')) return;
    await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}`, { method: 'DELETE' }).catch(() => {});
    if (dojoView?.sampleId === sampleId) setDojoView(null);
    await loadSamples(editing.id);
    loadSummary();
  };

  const runDojo = async () => {
    if (dojoRunning) return;
    setDojoRunning(true);
    setError(null);
    try {
      const res = await apiFetch('/api/supplier-invoice-specs/dojo/run', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      }
      await loadSummary();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Dojo run failed');
    } finally {
      setDojoRunning(false);
    }
  };

  const handleSave = async () => {
    if (!editing) return;
    setSaving(true);
    setError(null);
    const aliases = aliasesText.split(/[\n,]/).map((a) => a.trim()).filter(Boolean);
    const body = { name: editing.name, aliases, instructions: editing.instructions, enabled: editing.enabled };
    try {
      const res = await apiFetch(isNew ? '/api/supplier-invoice-specs' : `/api/supplier-invoice-specs/${editing.id}`, {
        method: isNew ? 'POST' : 'PUT',
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      }
      await load();
      setEditing(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (spec: SupplierSpec) => {
    if (!window.confirm(`Delete the spec for ${spec.name}?`)) return;
    await apiFetch(`/api/supplier-invoice-specs/${spec.id}`, { method: 'DELETE' }).catch(() => {});
    await load();
  };

  if (editing) {
    const main = isMainPrompt(editing);
    return (
      <div>
        {/* The form stays narrow; the sample viewer below goes full width. */}
        <div style={{ maxWidth: 720 }}>
        <button type="button" onClick={() => setEditing(null)}
          style={{ border: 'none', background: 'none', padding: 0, marginBottom: 8, fontSize: '0.74rem', color: '#8a6d3b', cursor: 'pointer', fontFamily: 'inherit' }}>
          ← Back to supplier specs
        </button>
        <h3 style={{ margin: '0 0 12px', fontSize: '1rem' }}>{isNew ? 'New supplier spec' : main ? 'Edit — Main prompt (all suppliers)' : `Edit — ${editing.name}`}</h3>
        {error && <div style={{ color: '#c0392b', fontSize: '0.8rem', marginBottom: 10 }}>{error}</div>}
        {!main && (
          <div style={{ marginBottom: 12 }}>
            <label style={labelStyle}>Supplier name</label>
            <input style={inputStyle} value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              placeholder="e.g. Service Foods" />
          </div>
        )}
        {!main && (
          <div style={{ marginBottom: 12 }}>
            <label style={labelStyle}>Aliases (one per line — other names this supplier appears under)</label>
            <textarea style={{ ...inputStyle, minHeight: 64, resize: 'vertical' }} value={aliasesText}
              onChange={(e) => setAliasesText(e.target.value)}
              placeholder={'Service Foods Auckland\nService Foods Ltd'} />
          </div>
        )}
        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>{main ? 'Main extraction prompt' : 'Extraction instructions'}</label>
          <textarea style={{ ...inputStyle, minHeight: main ? 320 : 140, resize: 'vertical' }} value={editing.instructions}
            onChange={(e) => setEditing({ ...editing, instructions: e.target.value })}
            placeholder={'e.g. This supplier prints quantities split across CTN and UNIT columns; the billed quantity is cartons × pack size + singles. The unit price is per single unit.'} />
          <div style={{ fontSize: '0.7rem', color: '#999', marginTop: 4 }}>
            {main
              ? 'The base prompt used to read EVERY invoice copy; a matching supplier spec is appended to it. Emptying or disabling this row falls back to the built-in prompt. Edits apply to all environments immediately and re-read each invoice once.'
              : 'Appended to the invoice-copy reading prompt whenever an invoice’s supplier matches the name or an alias. Affects how the copy is read only — the validation checks stay the same for every supplier.'}
          </div>
          {/* Test-before-commit: run the textarea's CURRENT text against the
              dojo without saving. Main prompt → every sample; supplier spec →
              its own samples. */}
          {!isNew && (
            <div style={{ marginTop: 6 }}>
              <button type="button" onClick={testAgainstDojo} disabled={candidateRunning}
                title={main
                  ? 'run EVERY dojo sample under this draft main prompt (nothing is saved)'
                  : 'run this supplier’s dojo samples under this draft spec text (nothing is saved)'}
                style={{ fontSize: '0.72rem', padding: '4px 12px', border: '1px solid #b78a2f', borderRadius: 6, background: '#fff', color: '#8a6d3b', cursor: candidateRunning ? 'wait' : 'pointer' }}>
                {candidateRunning ? 'Testing against dojo…' : 'Test against dojo'}
              </button>
              {candidateResult && (
                <div style={{ marginTop: 6, padding: '6px 10px', border: '1px solid #eee', borderRadius: 6, background: '#fbfaf8' }}>
                  <div style={{ fontSize: '0.7rem', color: '#555', marginBottom: 3 }}>
                    Candidate result (nothing saved): {candidateResult.passed} pass · {candidateResult.failed} fail · {candidateResult.errors} error · {candidateResult.new} no-baseline
                  </div>
                  {candidateResult.samples.map((s) => (
                    <div key={s.id} style={{ fontSize: '0.68rem', display: 'flex', gap: 6, alignItems: 'center', padding: '1px 0' }}>
                      <StatusBadge status={s.status} count={s.status === 'fail' ? (s.diffs?.length ?? 0) : undefined} />
                      <span>{s.label}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginBottom: 16, fontSize: '0.85rem' }}>
          <input type="checkbox" checked={editing.enabled}
            onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} /> Enabled
        </label>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={handleSave} disabled={saving || !editing.name.trim()}
            style={{ padding: '8px 18px', border: 'none', borderRadius: 6, background: '#2e7d4f', color: '#fff', cursor: 'pointer', fontSize: '0.85rem' }}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button onClick={() => setEditing(null)}
            style={{ padding: '8px 14px', border: '1px solid #ddd', borderRadius: 6, background: '#fff', color: '#555', cursor: 'pointer', fontSize: '0.85rem' }}>
            Cancel
          </button>
          {!isNew && !main && (
            <button onClick={() => handleDelete(editing)}
              style={{ marginLeft: 'auto', padding: '8px 14px', border: '1px solid #f0c0ba', borderRadius: 6, background: '#fff', color: '#c0392b', cursor: 'pointer', fontSize: '0.85rem' }}>
              Delete
            </button>
          )}
        </div>
        </div>

        {/* ---- Dojo: sample invoices + regression runs ------------------- */}
        {!isNew && !main && (
          <div style={{ marginTop: 24, borderTop: '1px solid #eee', paddingTop: 14 }}>
            <div style={{ maxWidth: 720 }}>
            <label style={{ ...labelStyle, marginBottom: 6 }}>Test invoices (Dojo)</label>
            <div style={{ fontSize: '0.7rem', color: '#999', marginBottom: 8 }}>
              Each run reads the PDF with the CURRENT prompts (main + this spec) and compares against the stored expected values. Open a sample with View; ask the sensei there to review and baseline it.
            </div>
            {samples.length === 0 && (
              <div style={{ fontSize: '0.75rem', color: '#aaa', padding: '6px 0' }}>
                No sample invoices yet — file one with <strong>Can&rsquo;t receive</strong> on an invoice card.
              </div>
            )}
            {samples.map((s) => (
              <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', borderBottom: '1px solid #f4f4f4', fontSize: '0.78rem' }}>
                <StatusBadge status={s.last_status} count={s.last_status === 'fail' ? s.diff_count : undefined} />
                {(s.replica_warning_count ?? 0) > 0 && (
                  <span title="the replica raised warnings — open the sample to see them"
                    style={{ fontSize: '0.62rem', color: '#8a6d3b', background: '#fdf6e7', border: '1px solid #ecd9ac', borderRadius: 4, padding: '1px 7px', whiteSpace: 'nowrap' }}>
                    ⚠ {s.replica_warning_count}
                  </span>
                )}
                {s.analysis_status === 'queued' && (
                  <span title="queued — the sensei worker picks it up within seconds"
                    style={{ fontSize: '0.62rem', color: '#4c3d8f', background: '#e8e6f5', borderRadius: 4, padding: '1px 7px', whiteSpace: 'nowrap' }}>sensei queued</span>
                )}
                {s.analysis_status === 'running' && !s.analysis_stale && (
                  <span style={{ fontSize: '0.62rem', color: '#1d4ed8', background: '#dbeafe', borderRadius: 4, padding: '1px 7px', whiteSpace: 'nowrap' }}>
                    sensei analysing{s.analysis_phase ? ` — ${s.analysis_phase}` : '…'}
                  </span>
                )}
                {s.analysis_status === 'running' && s.analysis_stale && (
                  <span title="the executor died mid-run — the worker requeues and restarts it automatically"
                    style={{ fontSize: '0.62rem', color: '#8a6d3b', background: '#fdf6e7', borderRadius: 4, padding: '1px 7px', whiteSpace: 'nowrap' }}>
                    sensei restarting (attempt {(s.analysis_attempts ?? 0) + 1})…
                  </span>
                )}
                {s.analysis_status === 'ready' && (
                  <button type="button" onClick={() => viewSample(s.id)}
                    title="open the sample — the sensei's proposal shows at the top of the invoice view"
                    style={{ fontSize: '0.62rem', fontWeight: 700, color: '#065f46', background: '#d1fae5', border: '1px solid #a7dcc4', borderRadius: 4, padding: '1px 7px', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                    sensei proposal
                  </button>
                )}
                {s.analysis_status === 'not_green' && (
                  <button type="button" onClick={() => viewSample(s.id)}
                    title="open the sample — the sensei's proposal shows at the top of the invoice view"
                    style={{ fontSize: '0.62rem', fontWeight: 700, color: '#8a6d3b', background: '#fdf6e7', border: '1px solid #ecd9ac', borderRadius: 4, padding: '1px 7px', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                    sensei not green
                  </button>
                )}
                {s.analysis_status === 'failed' && (
                  <span title={s.analysis_error || 'the sensei run errored — ask it again from the sample view'}
                    style={{ fontSize: '0.62rem', color: '#991b1b', background: '#fee2e2', borderRadius: 4, padding: '1px 7px', whiteSpace: 'nowrap', maxWidth: 340, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    sensei failed{s.analysis_error ? ` — ${s.analysis_error}` : ''}
                  </span>
                )}
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.label}</span>
                {s.last_run_at && <span style={{ fontSize: '0.65rem', color: '#aaa', whiteSpace: 'nowrap' }}>{new Date(s.last_run_at).toLocaleString()}</span>}
                <button onClick={() => runSample(s.id)} disabled={runningSample !== null}
                  title={'extract with the CURRENT prompts' + (s.analysis_status === 'ready' ? ' — the ready proposal is NOT used until you Apply it' : '')}
                  style={{ fontSize: '0.68rem', padding: '2px 10px', border: '1px solid #2e7d4f', borderRadius: 4, background: '#fff', color: '#2e7d4f', cursor: runningSample ? 'default' : 'pointer', whiteSpace: 'nowrap' }}>
                  {runningSample === s.id ? 'Running…' : 'Run'}
                </button>
                {s.last_run_at && (
                  <button onClick={() => (dojoView?.sampleId === s.id ? setDojoView(null) : viewSample(s.id))}
                    style={{ fontSize: '0.68rem', padding: '2px 10px', border: '1px solid #ccc', borderRadius: 4, background: dojoView?.sampleId === s.id ? '#f0f0ec' : '#fff', color: '#555', cursor: 'pointer' }}>
                    {dojoView?.sampleId === s.id ? 'Close' : 'View'}
                  </button>
                )}
                <button onClick={() => deleteSample(s.id)}
                  style={{ fontSize: '0.68rem', padding: '2px 8px', border: 'none', background: 'none', color: '#c0392b', cursor: 'pointer' }}>
                  ✕
                </button>
              </div>
            ))}
            </div>
            {dojoView && (
              <div style={{ marginTop: 12, display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                {/* Side by side by side: the invoice COPY (left, sticky) next
                    to what was extracted vs what Loaded holds (right). Each
                    pane takes half the page; they stack on narrow screens. */}
                <div style={{ flex: '1 1 420px', minWidth: 320, position: 'sticky', top: 8 }}>
                  <InvoicePdfPane sampleId={dojoView.sampleId} />
                </div>
                <div style={{ flex: '1 1 420px', minWidth: 320 }}>
            {/* The analysis agent's proposal, shown at the TOP of the invoice
                view: rationale + spec text + candidate verification. Apply =
                write the spec AND baseline the agent's ground truth; green
                means every dojo check held. */}
            {analysisView && analysisView.sampleId === dojoView.sampleId && (
              <div style={{ marginBottom: 12 }}>
                <SenseiProposalCard
                  sampleId={analysisView.sampleId}
                  analysis={analysisView.analysis}
                  analysing={analysing === analysisView.sampleId}
                  applying={applyingAnalysis}
                  onReanalyse={(fb) => runAnalysis(analysisView.sampleId, fb)}
                  onApply={() => applyAnalysis(analysisView.sampleId)}
                  onDismiss={() => dismissAnalysis(analysisView.sampleId)}
                  onClose={() => setAnalysisView(null)}
                />
              </div>
            )}
                {/* The replica IS the invoice view (extracted-only since Aug
                    2026). A sample with no replica (uploaded by hand, so no
                    source venue/invoice to resolve against) gets an
                    explanation instead. The stored BASELINE lives in its own
                    section below, independent of the replica. */}
                {!dojoView.replica && (
                  <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, background: '#faf9f7', padding: '1rem', fontSize: '0.74rem', color: '#6b655c' }}>
                    {samples.find((x) => x.id === dojoView.sampleId)?.source_venue_id ? (
                      <>
                        No replica stored for this run — press <strong>Run</strong> on
                        the sample to build the invoice view (the stored run predates
                        the replica, or its build failed).
                      </>
                    ) : (
                      <>
                        No invoice view for this sample — it was uploaded by hand,
                        so there is no source venue or Loaded invoice to resolve
                        against. Extraction pass/fail still runs against its
                        expected values (see the status badge; baseline via
                        the sensei below). To get the full invoice view, file this
                        supplier&rsquo;s next real invoice via
                        <strong> Can&rsquo;t receive</strong> from the invoice card.
                      </>
                    )}
                    <div style={{ marginTop: 8 }}>
                      <button type="button" onClick={() => runAnalysis(dojoView.sampleId)} disabled={analysing !== null}
                        style={{ fontSize: '0.68rem', padding: '3px 12px', border: '1px solid #b78a2f', borderRadius: 4, background: '#fff', color: '#8a6d3b', cursor: analysing ? 'default' : 'pointer' }}>
                        {analysing === dojoView.sampleId ? 'Sensei analysing…' : 'Ask the sensei'}
                      </button>
                    </div>
                  </div>
                )}
                {dojoView.replica && (
                  <div>
                    {dojoView.replicaCompare ? (
                      <ReplicaCompareView
                        compare={dojoView.replicaCompare}
                        replicaDoc={dojoView.replica as Record<string, unknown>}
                        resolutionLog={(dojoView.replica as Record<string, unknown>).resolution_log as string[] | undefined}
                        warnings={(dojoView.replica as Record<string, unknown>).warnings as string[] | undefined}
                        onAnalyse={() => runAnalysis(dojoView.sampleId)}
                        analysing={analysing === dojoView.sampleId}
                      />
                    ) : (
                      <div style={{ fontSize: '0.72rem', color: '#8a8a8a' }}>
                        {(dojoView.replica as { error?: string })?.error ? (
                          <span style={{ color: '#a02b2b' }}>
                            The last run couldn’t build the invoice view: {(dojoView.replica as { error?: string }).error} — run the sample to try a fresh build.
                          </span>
                        ) : (
                          <>No comparison stored for this run — re-run the sample.</>
                        )}
                      </div>
                    )}
                    {/* The replica rendered as a real Receive Invoice card —
                        secondary, collapsed by default. */}
                    <details style={{ marginTop: 10 }}>
                      <summary style={{ fontSize: '0.72rem', color: '#666', cursor: 'pointer' }}>
                        Card view (replica as a Receive Invoice card)
                      </summary>
                      <div style={{ marginTop: 8 }}>
                        <ReceiveInvoiceEditor
                          key={`rep-${dojoView.sampleId}`}
                          data={{
                            ...dojoView.replica,
                            dojo_status: (dojoView.replicaDiffs ?? []).length ? 'fail' : 'pass',
                            dojo_diffs: dojoView.replicaDiffs ?? [],
                          }}
                          props={{ dojo: true }}
                        />
                      </div>
                    </details>
                  </div>
                )}
                {/* The stored BASELINE — what the sensei tests against, and
                    the only values a regression run can pass or fail on.
                    Visible for every sample, proposal or not; editing here
                    makes the baseline admin-owned, which the sensei never
                    overwrites. Open by default when a baseline exists. */}
                <details open={!!dojoView.expected} style={{ marginTop: 10 }}>
                  <summary style={{ fontSize: '0.72rem', color: '#666', cursor: 'pointer' }}>
                    Baseline values (expected extraction)
                  </summary>
                  <div style={{ marginTop: 8 }}>
                    <DojoSampleView
                      key={`base-${dojoView.sampleId}`}
                      sampleId={dojoView.sampleId}
                      expected={dojoView.expected}
                      extraction={dojoView.extraction}
                      diffs={dojoView.diffs}
                      status={dojoView.status}
                      onSaved={(res) => {
                        setDojoView((v) => v && v.sampleId === dojoView.sampleId
                          ? { ...v, status: res.status, diffs: res.diffs, expected: res.expected, extraction: res.extraction }
                          : v);
                        if (editing?.id) loadSamples(editing.id);
                        loadSummary();
                      }}
                    />
                  </div>
                </details>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  if (showDojo) {
    return <DojoTriagePanel onBack={() => { setShowDojo(false); loadSummary(); }} />;
  }

  if (showReport) {
    return <AutopilotReportPanel onBack={() => setShowReport(false)} />;
  }

  return (
    <div style={{ maxWidth: 860 }}>
      {/* The Dojo: triage every venue's outstanding invoices before they
          join the per-supplier regression suite below. */}
      <div onClick={() => setShowDojo(true)}
        style={{ border: '1px solid #e6d9b8', borderLeft: '4px solid #b78a2f', borderRadius: 8, background: '#fffdf6', padding: '12px 16px', marginBottom: 14, cursor: 'pointer' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <strong style={{ fontSize: '0.9rem', color: '#1e1c18' }}>🥋 Dojo</strong>
          <span style={{ marginLeft: 'auto', fontSize: '0.72rem', color: '#8a6d3b' }}>Enter →</span>
        </div>
        <div style={{ fontSize: '0.74rem', color: '#6b655c', marginTop: 4 }}>
          Review outstanding invoices from every venue side-by-side with what Norm
          extracts, let the sensei tune supplier specs, then promote keepers into
          regression testing.
        </div>
      </div>
      {/* The measurement half: every human receive is evidence for (or
          against) letting autopilot run unattended. */}
      <div onClick={() => setShowReport(true)}
        style={{ border: '1px solid #cfe0d6', borderLeft: '4px solid #2e7d4f', borderRadius: 8, background: '#f8fdfa', padding: '12px 16px', marginBottom: 14, cursor: 'pointer' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <strong style={{ fontSize: '0.9rem', color: '#1e1c18' }}>📊 Autopilot readiness</strong>
          <span style={{ marginLeft: 'auto', fontSize: '0.72rem', color: '#2e7d4f' }}>Open →</span>
        </div>
        <div style={{ fontSize: '0.74rem', color: '#6b655c', marginTop: 4 }}>
          How often accepting Norm&rsquo;s suggestions was enough to receive an invoice
          with no hand edits — per supplier, plus what Norm keeps missing.
        </div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, gap: 10 }}>
        <div style={{ fontSize: '0.8rem', color: '#777' }}>
          Per-supplier notes for reading invoice copies — matched by supplier name or alias during the invoice review.
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {Object.keys(dojoSummary).length > 0 && (
            <button onClick={runDojo} disabled={dojoRunning}
              title="re-run every stored sample invoice under the current prompts and compare against the expected values"
              style={{ padding: '7px 14px', border: '1px solid #b78a2f', borderRadius: 6, background: '#fff', color: '#8a6d3b', cursor: dojoRunning ? 'wait' : 'pointer', fontSize: '0.82rem', whiteSpace: 'nowrap' }}>
              {dojoRunning ? 'Running Dojo…' : 'Run Dojo'}
            </button>
          )}
          <button onClick={() => openEdit({ ...EMPTY }, true)}
            style={{ padding: '7px 14px', border: '1px solid #2e7d4f', borderRadius: 6, background: '#fff', color: '#2e7d4f', cursor: 'pointer', fontSize: '0.82rem', whiteSpace: 'nowrap' }}>
            + New Spec
          </button>
        </div>
      </div>
      {error && <div style={{ color: '#c0392b', fontSize: '0.8rem', marginBottom: 10 }}>{error}</div>}
      {specs.length === 0 && (
        <div style={{ padding: '2rem', textAlign: 'center', color: '#999', fontSize: '0.85rem' }}>
          No supplier specs yet.
        </div>
      )}
      {/* Main prompt pinned first; supplier rows keep the server's name order. */}
      {[...specs.filter(isMainPrompt), ...specs.filter((s) => !isMainPrompt(s))].map((s) => (
        <div key={s.id} onClick={() => openEdit(s, false)}
          style={{ border: isMainPrompt(s) ? '1px solid #cfe0d6' : '1px solid #eee', borderRadius: 8, padding: '10px 14px', marginBottom: 8, cursor: 'pointer', background: isMainPrompt(s) ? '#f6faf7' : '#fff' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <strong style={{ fontSize: '0.9rem' }}>{s.name}</strong>
            {isMainPrompt(s) && <span style={{ fontSize: '0.65rem', color: '#2e7d4f', border: '1px solid #b7d5c2', borderRadius: 4, padding: '1px 6px' }}>applies to every supplier</span>}
            {!s.enabled && <span style={{ fontSize: '0.65rem', color: '#999', border: '1px solid #ddd', borderRadius: 4, padding: '1px 6px' }}>disabled</span>}
            {(s.aliases || []).map((a) => (
              <span key={a} style={{ fontSize: '0.68rem', color: '#666', background: '#f4f2ee', borderRadius: 4, padding: '1px 7px' }}>{a}</span>
            ))}
            {(() => {
              const sum = dojoSummary[s.id];
              if (!sum) return null;
              return (
                <span style={{ marginLeft: 'auto', display: 'flex', gap: 4, alignItems: 'center' }}>
                  {sum.pass > 0 && <StatusBadge status="pass" count={sum.pass} />}
                  {sum.fail > 0 && <StatusBadge status="fail" count={sum.fail} />}
                  {sum.error > 0 && <StatusBadge status="error" count={sum.error} />}
                  {sum.new > 0 && <StatusBadge status="new" count={sum.new} />}
                </span>
              );
            })()}
          </div>
          {s.instructions && (
            <div style={{ fontSize: '0.75rem', color: '#888', marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {s.instructions}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
