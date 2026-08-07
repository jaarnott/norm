'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../lib/api';
import ReceiveInvoiceEditor from '../display/ReceiveInvoiceEditor';

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
}
interface DojoView {
  sampleId: string;
  status: string;
  editor_data: Record<string, unknown>;
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
      if (data.editor_data) setDojoView({ sampleId, status: data.status, editor_data: data.editor_data });
      else if (data.error) setError(String(data.error));
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
      setDojoView({ sampleId, status: data.status, editor_data: data.editor_data });
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

  const viewPdf = async (sampleId: string) => {
    // The auth token lives in localStorage, not a cookie — a bare <a href>
    // would 401. Fetch with the token and open a blob URL instead.
    const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/pdf`).catch(() => null);
    if (!res?.ok) return;
    const blob = await res.blob();
    window.open(URL.createObjectURL(blob), '_blank');
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
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.label}</span>
                {s.last_run_at && <span style={{ fontSize: '0.65rem', color: '#aaa', whiteSpace: 'nowrap' }}>{new Date(s.last_run_at).toLocaleString()}</span>}
                <button onClick={() => runSample(s.id)} disabled={runningSample !== null}
                  style={{ fontSize: '0.68rem', padding: '2px 10px', border: '1px solid #2e7d4f', borderRadius: 4, background: '#fff', color: '#2e7d4f', cursor: runningSample ? 'default' : 'pointer', whiteSpace: 'nowrap' }}>
                  {runningSample === s.id ? 'Running…' : 'Run'}
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
            {dojoView && (
              <div style={{ marginTop: 12 }}>
                <ReceiveInvoiceEditor data={dojoView.editor_data} props={{ dojo: true }} />
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
