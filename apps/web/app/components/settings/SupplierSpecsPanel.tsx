'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../lib/api';
import DojoSampleView, { type DojoDiff, type ExtractionDoc } from './DojoSampleView';

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
  analysis_status?: string | null;
  analysis_green?: boolean;
}

// The analysis agent's stored proposal. Carries the agent's ground truth AND
// the candidate's own extraction so the admin can VERIFY the green gate —
// "the agent says it passed" is a claim; these are the values behind it.
interface DojoAnalysis {
  status: string;
  green?: boolean;
  rationale?: string;
  layout_facts?: string[];
  proposed_instructions?: string;
  // Same layout as an existing spec: Apply adds this supplier as an alias on
  // that spec (and moves the sample there) instead of keeping a duplicate.
  alias_of?: string | null;
  // The correction conversation: admin replies the agent has folded in.
  thread?: { role: string; text: string; at?: string }[];
  error?: string;
  ground_truth?: ExtractionDoc | null;
  candidate_results?: {
    own?: { status?: string; diffs?: DojoDiff[]; extraction?: ExtractionDoc | null };
    siblings?: { samples?: { id: string; label: string; status: string; diffs?: unknown[] }[]; passed?: number; failed?: number; errors?: number; new?: number };
  };
  model?: string;
  at?: string;
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
  const [uploading, setUploading] = useState(false);
  const [runningSample, setRunningSample] = useState<string | null>(null);
  const [dojoView, setDojoView] = useState<DojoView | null>(null);
  const [dojoRunning, setDojoRunning] = useState(false);
  const [dojoSummary, setDojoSummary] = useState<Record<string, DojoSummaryRow>>({});
  const fileInput = useRef<HTMLInputElement | null>(null);

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
  const uploadSample = async (file: File) => {
    if (!editing?.id) return;
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await apiFetch(`/api/supplier-invoice-specs/${editing.id}/samples`, {
        method: 'POST',
        body: form,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      }
      await loadSamples(editing.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = '';
    }
  };

  const runSample = async (sampleId: string) => {
    if (!editing?.id || runningSample) return;
    setRunningSample(sampleId);
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/run`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      if (data.error) setError(String(data.error));
      setDojoView({ sampleId, status: data.status, expected: data.expected ?? null, extraction: data.extraction ?? null, diffs: data.diffs ?? [] });
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
      setDojoView({ sampleId, status: data.status, expected: data.expected ?? null, extraction: data.extraction ?? null, diffs: data.diffs ?? [] });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the last run');
    }
  };

  const saveExpected = async (sampleId: string) => {
    if (!editing?.id) return;
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/expected`, { method: 'POST' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      }
      await loadSamples(editing.id);
      loadSummary();
      if (dojoView?.sampleId === sampleId) await viewSample(sampleId);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the baseline');
    }
  };

  // ---- Analysis agent (proposals) ---------------------------------------
  const [analysisView, setAnalysisView] = useState<{ sampleId: string; analysis: DojoAnalysis } | null>(null);
  const [analysisFeedback, setAnalysisFeedback] = useState('');
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
      else setError('No analysis yet — run Analyse on the sample first.');
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
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/analyse`, {
        method: 'POST',
        ...(feedback?.trim() ? { body: JSON.stringify({ feedback: feedback.trim() }) } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      if (data.analysis) setAnalysisView({ sampleId, analysis: data.analysis });
      setAnalysisFeedback('');
      await loadSamples(editing.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Analysis failed');
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

  // Poll while any analysis is running (Add-to-Dojo kicks them in the
  // background) — the TestsPanel interval pattern.
  useEffect(() => {
    if (!editing?.id) return;
    if (!samples.some((s) => s.analysis_status === 'running')) return;
    const specId = editing.id;
    const t = setInterval(() => { loadSamples(specId); }, 5000);
    return () => clearInterval(t);
  }, [editing?.id, samples, loadSamples]);

  const viewPdf = async (sampleId: string) => {
    // The auth token lives in localStorage, not a cookie — a bare <a href>
    // would 401. Open the tab synchronously (keeps the click's
    // user-activation; window.open after the await gets popup-blocked),
    // then navigate it to the fetched blob.
    const w = window.open('about:blank', '_blank');
    const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/pdf`).catch(() => null);
    if (!res?.ok) {
      if (w && !w.closed) w.close();
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    if (w && !w.closed) w.location.replace(url);
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  };

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
      <div style={{ maxWidth: 720 }}>
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

        {/* ---- Dojo: sample invoices + regression runs ------------------- */}
        {!isNew && !main && (
          <div style={{ marginTop: 24, borderTop: '1px solid #eee', paddingTop: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <label style={{ ...labelStyle, marginBottom: 0 }}>Test invoices (Dojo)</label>
              <label style={{ fontSize: '0.75rem', color: '#2e7d4f', border: '1px solid #2e7d4f', borderRadius: 6, padding: '4px 10px', cursor: uploading ? 'wait' : 'pointer' }}>
                {uploading ? 'Uploading…' : '+ Upload invoice PDF'}
                <input ref={fileInput} type="file" accept="application/pdf,.pdf" style={{ display: 'none' }}
                  disabled={uploading}
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadSample(f); }} />
              </label>
            </div>
            <div style={{ fontSize: '0.7rem', color: '#999', marginBottom: 8 }}>
              Each run reads the PDF with the CURRENT prompts (main + this spec) and compares against the stored expected values. Review a run, then “Save as expected” to set the baseline.
            </div>
            {samples.length === 0 && (
              <div style={{ fontSize: '0.75rem', color: '#aaa', padding: '6px 0' }}>No sample invoices yet.</div>
            )}
            {samples.map((s) => (
              <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', borderBottom: '1px solid #f4f4f4', fontSize: '0.78rem' }}>
                <StatusBadge status={s.last_status} count={s.last_status === 'fail' ? s.diff_count : undefined} />
                {s.analysis_status === 'running' && (
                  <span style={{ fontSize: '0.62rem', color: '#1d4ed8', background: '#dbeafe', borderRadius: 4, padding: '1px 7px', whiteSpace: 'nowrap' }}>analysing…</span>
                )}
                {s.analysis_status === 'ready' && (
                  <button type="button" onClick={() => viewAnalysis(s.id)}
                    style={{ fontSize: '0.62rem', fontWeight: 700, color: '#065f46', background: '#d1fae5', border: '1px solid #a7dcc4', borderRadius: 4, padding: '1px 7px', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                    proposal ready
                  </button>
                )}
                {s.analysis_status === 'not_green' && (
                  <button type="button" onClick={() => viewAnalysis(s.id)}
                    style={{ fontSize: '0.62rem', fontWeight: 700, color: '#8a6d3b', background: '#fdf6e7', border: '1px solid #ecd9ac', borderRadius: 4, padding: '1px 7px', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                    analysis not green
                  </button>
                )}
                {s.analysis_status === 'failed' && (
                  <span title="the analysis run errored — try Analyse again"
                    style={{ fontSize: '0.62rem', color: '#991b1b', background: '#fee2e2', borderRadius: 4, padding: '1px 7px', whiteSpace: 'nowrap' }}>analysis failed</span>
                )}
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.label}</span>
                {s.last_run_at && <span style={{ fontSize: '0.65rem', color: '#aaa', whiteSpace: 'nowrap' }}>{new Date(s.last_run_at).toLocaleString()}</span>}
                <button onClick={() => runSample(s.id)} disabled={runningSample !== null}
                  title={'extract with the CURRENT prompts' + (s.analysis_status === 'ready' ? ' — the ready proposal is NOT used until you Apply it' : '')}
                  style={{ fontSize: '0.68rem', padding: '2px 10px', border: '1px solid #2e7d4f', borderRadius: 4, background: '#fff', color: '#2e7d4f', cursor: runningSample ? 'default' : 'pointer', whiteSpace: 'nowrap' }}>
                  {runningSample === s.id ? 'Running…' : 'Run'}
                </button>
                <button onClick={() => runAnalysis(s.id)} disabled={analysing !== null || s.analysis_status === 'running'}
                  title="the analysis agent studies this invoice with full context and drafts a spec update (1–2 min)"
                  style={{ fontSize: '0.68rem', padding: '2px 10px', border: '1px solid #b78a2f', borderRadius: 4, background: '#fff', color: '#8a6d3b', cursor: analysing ? 'default' : 'pointer', whiteSpace: 'nowrap' }}>
                  {analysing === s.id ? 'Analysing…' : 'Analyse'}
                </button>
                {s.last_run_at && (
                  <button onClick={() => viewSample(s.id)}
                    style={{ fontSize: '0.68rem', padding: '2px 10px', border: '1px solid #ccc', borderRadius: 4, background: dojoView?.sampleId === s.id ? '#f0f0ec' : '#fff', color: '#555', cursor: 'pointer' }}>
                    View
                  </button>
                )}
                {s.last_run_at && (!s.has_expected || s.last_status === 'fail') && (
                  <button onClick={() => saveExpected(s.id)}
                    title="store this run's values as the expected baseline for future runs"
                    style={{ fontSize: '0.68rem', padding: '2px 10px', border: '1px solid #b78a2f', borderRadius: 4, background: '#fff', color: '#8a6d3b', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                    Save as expected
                  </button>
                )}
                <button onClick={() => viewPdf(s.id)}
                  style={{ fontSize: '0.68rem', padding: '2px 8px', border: 'none', background: 'none', color: '#888', cursor: 'pointer', textDecoration: 'underline' }}>
                  PDF
                </button>
                <button onClick={() => deleteSample(s.id)}
                  style={{ fontSize: '0.68rem', padding: '2px 8px', border: 'none', background: 'none', color: '#c0392b', cursor: 'pointer' }}>
                  ✕
                </button>
              </div>
            ))}
            {/* The analysis agent's proposal: rationale + spec text +
                candidate verification. Apply = write the spec AND baseline
                the agent's ground truth; green means every dojo check held. */}
            {analysisView && (() => {
              const a = analysisView.analysis;
              const own = a.candidate_results?.own;
              const sib = a.candidate_results?.siblings;
              return (
                <div style={{ marginTop: 12, border: '1px solid #e6d9b8', borderRadius: 8, background: '#fffdf6', padding: '10px 14px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <strong style={{ fontSize: '0.82rem' }}>Analysis proposal</strong>
                    <StatusBadge status={a.status === 'ready' ? 'pass' : a.status === 'failed' ? 'error' : 'new'} />
                    {a.model && <span style={{ fontSize: '0.62rem', color: '#999' }}>{a.model}</span>}
                    <button type="button" onClick={() => setAnalysisView(null)}
                      style={{ marginLeft: 'auto', fontSize: '0.66rem', border: 'none', background: 'none', color: '#999', cursor: 'pointer' }}>✕</button>
                  </div>
                  {a.error && <div style={{ fontSize: '0.72rem', color: '#c0392b', marginBottom: 6 }}>{a.error}</div>}
                  {a.rationale && (
                    <div style={{ fontSize: '0.74rem', color: '#4a4a4a', marginBottom: 8, whiteSpace: 'pre-wrap' }}>{a.rationale}</div>
                  )}
                  {(a.layout_facts?.length ?? 0) > 0 && (
                    <ul style={{ margin: '0 0 8px', paddingLeft: 18, fontSize: '0.7rem', color: '#666' }}>
                      {a.layout_facts!.map((f, i) => <li key={i}>{f}</li>)}
                    </ul>
                  )}
                  {a.alias_of && (
                    <div style={{ fontSize: '0.72rem', color: '#1d4ed8', background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 6, padding: '6px 10px', marginBottom: 8 }}>
                      Same layout as existing spec <strong>{a.alias_of}</strong> — Apply adds this supplier as an alias on that spec
                      and moves this sample there. No new spec is created.
                    </div>
                  )}
                  {(a.proposed_instructions ?? '').trim() ? (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ ...labelStyle, marginBottom: 2 }}>
                        {a.alias_of ? `Proposed spec text for '${a.alias_of}' (replaces its current instructions)` : 'Proposed spec text (replaces the current instructions)'}
                      </div>
                      <pre style={{ fontSize: '0.7rem', whiteSpace: 'pre-wrap', background: '#fff', border: '1px solid #eee', borderRadius: 6, padding: '8px 10px', margin: 0, fontFamily: 'inherit' }}>{a.proposed_instructions}</pre>
                    </div>
                  ) : (
                    <div style={{ fontSize: '0.7rem', color: '#777', marginBottom: 8 }}>
                      {a.alias_of
                        ? `No text change — '${a.alias_of}' already covers this layout as written.`
                        : 'No spec change proposed — the agent corrected the expected values only.'}
                    </div>
                  )}
                  {own && (
                    <div style={{ fontSize: '0.7rem', color: '#555', marginBottom: 2, display: 'flex', gap: 6, alignItems: 'center' }}>
                      <StatusBadge status={own.status || 'new'} />
                      <span>this invoice vs the agent’s corrected values{own.status === 'fail' ? ` — ${own.diffs?.length ?? 0} mismatch(es)` : ''}</span>
                    </div>
                  )}
                  {/* The evidence behind that badge: the agent's corrected
                      values AND the raw extraction the proposed prompt
                      produced — check either against the PDF, don't take the
                      agent's word for it. Wrong corrected values? Fix them in
                      View → Expected, then Test against dojo re-checks. */}
                  {(a.ground_truth || own?.extraction) && (
                    <div style={{ margin: '8px 0' }}>
                      <div style={{ ...labelStyle, marginBottom: 4 }}>Verify the values yourself (against the PDF)</div>
                      <DojoSampleView
                        key={`${analysisView.sampleId}:${a.at ?? ''}`}
                        sampleId={analysisView.sampleId}
                        expected={a.ground_truth ?? null}
                        extraction={own?.extraction ?? null}
                        diffs={own?.diffs ?? []}
                        status={own?.status === 'pass' ? 'pass' : own?.status === 'fail' ? 'fail' : 'new'}
                        readOnly
                        labels={{
                          expected: 'Agent’s corrected values',
                          extracted: 'Extracted with proposed prompt',
                          expectedHint: 'what the agent read off the PDF — these become the sample’s expected values',
                          extractedHint: 'what the PROPOSED prompt actually pulled in the verification run — the pass/fail above compares exactly these two',
                        }}
                      />
                    </div>
                  )}
                  {sib?.samples?.map((s) => (
                    <div key={s.id} style={{ fontSize: '0.7rem', color: '#555', display: 'flex', gap: 6, alignItems: 'center', padding: '1px 0' }}>
                      <StatusBadge status={s.status} />
                      <span>{s.label} (existing baseline)</span>
                    </div>
                  ))}
                  {/* Reply to the thread: a wrong value in the proposal gets
                      corrected here — the agent re-reads the document with the
                      correction as authoritative and re-tests before
                      re-proposing. Never fix a wrong proposal by hand-editing
                      config; correct the agent so the spec text AND the
                      expected values move together. */}
                  {(a.thread?.filter((m) => m.role === 'admin').length ?? 0) > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <div style={{ ...labelStyle, marginBottom: 2 }}>Your corrections so far</div>
                      {a.thread!.filter((m) => m.role === 'admin').map((m, i) => (
                        <div key={i} style={{ fontSize: '0.7rem', color: '#555', padding: '1px 0' }}>↳ {m.text}</div>
                      ))}
                    </div>
                  )}
                  {a.status !== 'applied' && (
                    <div style={{ display: 'flex', gap: 6, marginTop: 8, alignItems: 'flex-start' }}>
                      <textarea value={analysisFeedback} onChange={(e) => setAnalysisFeedback(e.target.value)} rows={2}
                        placeholder={'Correct the agent — e.g. "line 4’s unit must stay ‘2x12 pack’, never flattened to ‘24 pack’" — it re-analyses with your correction as authoritative and re-tests'}
                        style={{ flex: 1, fontSize: '0.7rem', padding: '5px 8px', border: '1px solid #d8d4cc', borderRadius: 6, fontFamily: 'inherit', resize: 'vertical' }} />
                      <button type="button" onClick={() => runAnalysis(analysisView.sampleId, analysisFeedback)}
                        disabled={analysing !== null || !analysisFeedback.trim()}
                        style={{ fontSize: '0.72rem', padding: '5px 12px', border: '1px solid #b78a2f', borderRadius: 6, background: '#fff', color: '#8a6d3b', cursor: analysing || !analysisFeedback.trim() ? 'default' : 'pointer', whiteSpace: 'nowrap', opacity: analysisFeedback.trim() ? 1 : 0.5 }}>
                        {analysing === analysisView.sampleId ? 'Re-analysing…' : 'Send correction & re-analyse'}
                      </button>
                    </div>
                  )}
                  {a.status !== 'applied' && (
                    <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                      <button type="button" onClick={() => applyAnalysis(analysisView.sampleId)} disabled={applyingAnalysis}
                        title={a.status === 'ready'
                          ? (a.alias_of ? `add the alias to '${a.alias_of}' and move this sample there` : 'write the proposed spec text and baseline the corrected values')
                          : 'the candidate run was NOT fully green — applying anyway is your call'}
                        style={{ fontSize: '0.72rem', padding: '5px 14px', border: 'none', borderRadius: 6, background: a.status === 'ready' ? '#2e7d4f' : '#b78a2f', color: '#fff', cursor: applyingAnalysis ? 'wait' : 'pointer' }}>
                        {applyingAnalysis ? 'Applying…' : a.status === 'ready' ? (a.alias_of ? `Add alias to '${a.alias_of}'` : 'Apply spec update') : 'Apply anyway'}
                      </button>
                      <button type="button" onClick={() => dismissAnalysis(analysisView.sampleId)}
                        style={{ fontSize: '0.72rem', padding: '5px 12px', border: '1px solid #ccc', borderRadius: 6, background: '#fff', color: '#666', cursor: 'pointer' }}>
                        Dismiss proposal
                      </button>
                    </div>
                  )}
                </div>
              );
            })()}
            {dojoView && (
              <div style={{ marginTop: 12 }}>
                <DojoSampleView
                  key={dojoView.sampleId}
                  sampleId={dojoView.sampleId}
                  expected={dojoView.expected}
                  extraction={dojoView.extraction}
                  diffs={dojoView.diffs}
                  status={dojoView.status}
                  onSaved={(res) => {
                    setDojoView({
                      sampleId: dojoView.sampleId,
                      status: res.status,
                      expected: res.expected,
                      extraction: res.extraction,
                      diffs: res.diffs ?? [],
                    });
                    if (editing?.id) loadSamples(editing.id);
                    loadSummary();
                  }}
                />
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 860 }}>
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
