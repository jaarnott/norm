'use client';

import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../../lib/api';

// The Dojo's own sample view — deliberately NOT the Receive Invoice editor.
// A sample carries two extraction-shaped value sets and nothing else:
//   EXPECTED  — the baseline an ADMIN (or the analysis agent) authored: what
//               the LLM is EXPECTED to pull off the document. Editable here.
//               Never sourced from Loaded.
//   EXTRACTED — what the last run actually pulled under the current prompts.
// The toggle compares them; mismatched fields on the Extracted side are
// highlighted with the expected value alongside.

export interface ExtractionLine {
  code?: string | null;
  description?: string | null;
  quantity?: number | string | null;
  unit?: string | null;
  unit_of_measure?: string | null;
  unit_unrecognisable?: boolean | null;
  unit_price_ex_tax?: number | string | null;
  line_total_ex_tax?: number | string | null;
}

export interface ExtractionDoc {
  document_type?: string | null;
  supplier_name?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  purchase_order_number?: string | null;
  lines?: ExtractionLine[];
  subtotal_ex_tax?: number | string | null;
  tax_amount?: number | string | null;
  total_incl_tax?: number | string | null;
}

export interface DojoDiff {
  field: string;
  line?: number | null;
  description?: string | null;
  expected?: unknown;
  actual?: unknown;
}
type Diff = DojoDiff;

const HEADER_FIELDS: { key: keyof ExtractionDoc; label: string }[] = [
  { key: 'document_type', label: 'Document type' },
  { key: 'supplier_name', label: 'Supplier (as printed)' },
  { key: 'invoice_number', label: 'Invoice number' },
  { key: 'invoice_date', label: 'Invoice date' },
  { key: 'purchase_order_number', label: 'PO number' },
  { key: 'subtotal_ex_tax', label: 'Subtotal ex tax' },
  { key: 'tax_amount', label: 'Tax' },
  { key: 'total_incl_tax', label: 'Total incl tax' },
];

const LINE_COLS: { key: keyof ExtractionLine; label: string; width?: number; numeric?: boolean }[] = [
  { key: 'code', label: 'Code', width: 90 },
  { key: 'description', label: 'Description' },
  { key: 'quantity', label: 'Qty', width: 70, numeric: true },
  { key: 'unit_of_measure', label: 'Unit', width: 90 },
  { key: 'unit_price_ex_tax', label: 'Unit price', width: 85, numeric: true },
  { key: 'line_total_ex_tax', label: 'Line total', width: 85, numeric: true },
];

const microLabel: React.CSSProperties = { fontSize: '0.58rem', fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.04em' };
const cellInput: React.CSSProperties = { width: '100%', padding: '3px 6px', border: '1px solid #ddd', borderRadius: 4, fontSize: '0.72rem', fontFamily: 'inherit', boxSizing: 'border-box' };

function deepCopy<T>(v: T): T {
  return JSON.parse(JSON.stringify(v ?? null)) as T;
}

export default function DojoSampleView({
  sampleId,
  expected,
  extraction,
  diffs,
  status,
  onSaved,
  readOnly,
  labels,
}: {
  sampleId: string;
  expected: ExtractionDoc | null;
  extraction: ExtractionDoc | null;
  diffs: Diff[];
  status: string;
  onSaved?: (res: { sample: unknown; status: string; diffs: Diff[]; expected: ExtractionDoc | null; extraction: ExtractionDoc | null }) => void;
  // Proposal-review mode: same comparison table, no editing/saving — used to
  // show the agent's corrected values vs the candidate extraction verbatim.
  readOnly?: boolean;
  labels?: { expected?: string; extracted?: string; expectedHint?: string; extractedHint?: string };
}) {
  const [mode, setMode] = useState<'extracted' | 'expected'>(extraction ? 'extracted' : 'expected');
  const [draft, setDraft] = useState<ExtractionDoc | null>(() => deepCopy(expected));
  const [dirty, setDirty] = useState(false);
  // Re-seed when the parent hands over a DIFFERENT value set — a corrected
  // proposal after a thread reply, or View switching samples. Without this
  // the expected side kept the values from first mount, so a correction
  // looked like it hadn't landed (and any unsaved run diffs read stale).
  useEffect(() => {
    setDraft(deepCopy(expected));
    setDirty(false);
  }, [expected]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mismatch lookup from the server's stored diffs: header by field name,
  // lines by (1-based line, field).
  const diffMap = useMemo(() => {
    const header = new Map<string, Diff>();
    const line = new Map<string, Diff>();
    for (const d of diffs || []) {
      if (d.line == null) header.set(d.field, d);
      else line.set(`${d.line}:${d.field}`, d);
    }
    return { header, line };
  }, [diffs]);

  const edit = (fn: (d: ExtractionDoc) => void) => {
    setDraft((prev) => {
      const next = deepCopy(prev ?? { lines: [] });
      fn(next);
      return next;
    });
    setDirty(true);
  };

  const save = async () => {
    if (!draft || saving) return;
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/samples/${sampleId}/expected-values`, {
        method: 'PUT',
        body: JSON.stringify({ expected: draft }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setDirty(false);
      onSaved?.(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the expected values');
    } finally {
      setSaving(false);
    }
  };

  const doc = mode === 'expected' ? draft : extraction;
  const editable = mode === 'expected' && !readOnly;

  const numOrNull = (v: string): number | string | null => {
    if (v.trim() === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : v;
  };

  const mismatchStyle = (d: Diff | undefined): React.CSSProperties =>
    d ? { background: '#fdf6e7', border: '1px solid #e0b95d', borderRadius: 4 } : {};

  const statusChip = (() => {
    const map: Record<string, { bg: string; fg: string; label: string }> = {
      pass: { bg: '#d1fae5', fg: '#065f46', label: 'PASS — extracted matches expected' },
      fail: { bg: '#fee2e2', fg: '#991b1b', label: `FAIL — ${diffs.length} mismatch${diffs.length === 1 ? '' : 'es'}` },
      error: { bg: '#fee2e2', fg: '#991b1b', label: 'ERROR — last run failed' },
      new: { bg: '#fdf6e7', fg: '#8a6d3b', label: 'NO BASELINE — set the expected values' },
    };
    const p = map[status] || map.new;
    return <span style={{ fontSize: '0.62rem', fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: p.bg, color: p.fg }}>{p.label}</span>;
  })();

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, background: '#fff', padding: '12px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        {/* The toggle: which value set the table below shows. */}
        <div style={{ display: 'inline-flex', border: '1px solid #d8d4cc', borderRadius: 6, overflow: 'hidden' }}>
          <button type="button" onClick={() => setMode('expected')}
            style={{ fontSize: '0.7rem', padding: '4px 12px', border: 'none', cursor: 'pointer', fontFamily: 'inherit', background: mode === 'expected' ? '#2e7d4f' : '#fff', color: mode === 'expected' ? '#fff' : '#666', fontWeight: 600 }}>
            {labels?.expected ?? (readOnly ? 'Expected' : 'Expected (editable)')}
          </button>
          <button type="button" onClick={() => setMode('extracted')} disabled={!extraction}
            title={extraction ? undefined : 'no extraction run stored yet — press Run'}
            style={{ fontSize: '0.7rem', padding: '4px 12px', border: 'none', borderLeft: '1px solid #d8d4cc', cursor: extraction ? 'pointer' : 'not-allowed', fontFamily: 'inherit', background: mode === 'extracted' ? '#4a5568' : '#fff', color: mode === 'extracted' ? '#fff' : extraction ? '#666' : '#bbb', fontWeight: 600 }}>
            {labels?.extracted ?? 'Extracted (last run)'}
          </button>
        </div>
        {statusChip}
        {mode === 'expected' && (
          <span style={{ fontSize: '0.64rem', color: '#8a6d3b' }}>
            {labels?.expectedHint ?? 'what the LLM SHOULD pull off this document — authored here (or by the analysis agent), never from Loaded'}
          </span>
        )}
        {mode === 'extracted' && (
          <span style={{ fontSize: '0.64rem', color: '#667' }}>
            {labels?.extractedHint ?? 'what the last run actually pulled — mismatches vs expected are highlighted'}
          </span>
        )}
      </div>
      {error && <div style={{ fontSize: '0.72rem', color: '#c0392b', marginBottom: 8 }}>{error}</div>}

      {mode === 'expected' && !draft && !readOnly && (
        <div style={{ padding: '10px 0' }}>
          <div style={{ fontSize: '0.74rem', color: '#777', marginBottom: 8 }}>
            No expected values stored yet.
          </div>
          {extraction ? (
            <button type="button" onClick={() => { setDraft(deepCopy(extraction)); setDirty(true); }}
              style={{ fontSize: '0.72rem', padding: '5px 12px', border: '1px solid #2e7d4f', borderRadius: 6, background: '#fff', color: '#2e7d4f', cursor: 'pointer' }}>
              Start from the extracted values
            </button>
          ) : (
            <button type="button" onClick={() => { setDraft({ document_type: 'invoice', lines: [{}] }); setDirty(true); }}
              style={{ fontSize: '0.72rem', padding: '5px 12px', border: '1px solid #2e7d4f', borderRadius: 6, background: '#fff', color: '#2e7d4f', cursor: 'pointer' }}>
              Start from scratch
            </button>
          )}
        </div>
      )}

      {doc && (
        <>
          {/* Header fields */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '6px 12px', marginBottom: 10 }}>
            {HEADER_FIELDS.map(({ key, label }) => {
              const d = mode === 'extracted' ? diffMap.header.get(key as string) : undefined;
              const val = doc[key];
              return (
                <label key={key as string} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <span style={microLabel}>{label}</span>
                  {editable ? (
                    <input style={cellInput} value={val == null ? '' : String(val)}
                      onChange={(e) => edit((x) => {
                        (x as Record<string, unknown>)[key as string] =
                          ['subtotal_ex_tax', 'tax_amount', 'total_incl_tax'].includes(key as string)
                            ? numOrNull(e.target.value)
                            : (e.target.value || null);
                      })} />
                  ) : (
                    <span style={{ fontSize: '0.74rem', color: '#333', padding: '3px 6px', minHeight: 18, ...mismatchStyle(d) }}
                      title={d ? `expected: ${JSON.stringify(d.expected ?? null)}` : undefined}>
                      {val == null || val === '' ? '—' : String(val)}
                      {d && <span style={{ color: '#8a6d3b', marginLeft: 6, fontSize: '0.62rem' }}>expected {JSON.stringify(d.expected ?? null)}</span>}
                    </span>
                  )}
                </label>
              );
            })}
          </div>

          {/* Lines */}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.72rem' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: '#999', fontSize: '0.58rem', textTransform: 'uppercase' }}>
                {LINE_COLS.map((c) => <th key={c.key as string} style={{ padding: '3px 6px', width: c.width }}>{c.label}</th>)}
                <th style={{ padding: '3px 6px', width: 86 }} title="the document shows size info that can't be read">Unreadable unit</th>
                {editable && <th style={{ width: 26 }} />}
              </tr>
            </thead>
            <tbody>
              {(doc.lines || []).map((l, i) => (
                <tr key={i} style={{ borderTop: '1px solid #f3f3f3', ...(mode === 'extracted' && diffMap.line.has(`${i + 1}:line_extra`) ? { background: '#fdf6e7' } : {}) }}
                  title={mode === 'extracted' && diffMap.line.has(`${i + 1}:line_extra`) ? 'extracted line not present in the expected values' : undefined}>
                  {LINE_COLS.map((c) => {
                    const d = mode === 'extracted' ? diffMap.line.get(`${i + 1}:${c.key as string}`) : undefined;
                    const val = l[c.key];
                    return (
                      <td key={c.key as string} style={{ padding: '3px 6px', verticalAlign: 'top' }}>
                        {editable ? (
                          <input style={{ ...cellInput, textAlign: c.numeric ? 'right' : 'left' }}
                            value={val == null ? '' : String(val)}
                            onChange={(e) => edit((x) => {
                              const ln = (x.lines || [])[i] as Record<string, unknown>;
                              ln[c.key as string] = c.numeric ? numOrNull(e.target.value) : (e.target.value || null);
                            })} />
                        ) : (
                          <span style={{ display: 'inline-block', padding: '2px 4px', textAlign: c.numeric ? 'right' : 'left', ...mismatchStyle(d) }}
                            title={d ? `expected: ${JSON.stringify(d.expected ?? null)}` : undefined}>
                            {val == null || val === '' ? '—' : String(val)}
                            {d && <div style={{ color: '#8a6d3b', fontSize: '0.6rem' }}>expected {JSON.stringify(d.expected ?? null)}</div>}
                          </span>
                        )}
                      </td>
                    );
                  })}
                  <td style={{ padding: '3px 6px', textAlign: 'center' }}>
                    {editable ? (
                      <input type="checkbox" checked={!!l.unit_unrecognisable}
                        onChange={(e) => edit((x) => { ((x.lines || [])[i] as Record<string, unknown>).unit_unrecognisable = e.target.checked || null; })} />
                    ) : (
                      (() => {
                        const d = diffMap.line.get(`${i + 1}:unit_unrecognisable`);
                        return <span style={{ ...mismatchStyle(d), padding: '1px 4px' }}>{l.unit_unrecognisable ? 'yes' : '—'}</span>;
                      })()
                    )}
                  </td>
                  {editable && (
                    <td style={{ padding: '3px 2px' }}>
                      <button type="button" onClick={() => edit((x) => { (x.lines || []).splice(i, 1); })}
                        title="remove this line from the expected values"
                        style={{ border: 'none', background: 'none', color: '#c0392b', cursor: 'pointer', fontSize: '0.72rem' }}>✕</button>
                    </td>
                  )}
                </tr>
              ))}
              {/* Lines the run pulled beyond the baseline, or vice versa */}
              {mode === 'extracted' && [...diffMap.line.entries()]
                .filter(([k]) => k.endsWith(':line_missing'))
                .map(([k, d]) => (
                  <tr key={k} style={{ borderTop: '1px solid #f3f3f3', color: '#991b1b', fontSize: '0.68rem' }}>
                    <td colSpan={LINE_COLS.length + 1} style={{ padding: '3px 6px' }}>
                      ✗ expected line {d.line} “{d.description}” was NOT extracted
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          {editable && (
            <button type="button" onClick={() => edit((x) => { x.lines = [...(x.lines || []), {}]; })}
              style={{ marginTop: 6, fontSize: '0.68rem', padding: '3px 10px', border: '1px dashed #bbb', borderRadius: 4, background: '#fff', color: '#777', cursor: 'pointer' }}>
              + Add line
            </button>
          )}


          {editable && (
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button type="button" onClick={save} disabled={!dirty || saving}
                style={{ fontSize: '0.72rem', padding: '5px 14px', border: 'none', borderRadius: 6, background: dirty ? '#2e7d4f' : '#a8c5b4', color: '#fff', cursor: dirty && !saving ? 'pointer' : 'default' }}>
                {saving ? 'Saving…' : 'Save expected values'}
              </button>
              {dirty && (
                <button type="button" onClick={() => { setDraft(deepCopy(expected)); setDirty(false); }}
                  style={{ fontSize: '0.72rem', padding: '5px 12px', border: '1px solid #ccc', borderRadius: 6, background: '#fff', color: '#666', cursor: 'pointer' }}>
                  Revert
                </button>
              )}
              {extraction && (
                <button type="button" onClick={() => { setDraft(deepCopy(extraction)); setDirty(true); }}
                  title="overwrite the draft with the last run's extracted values"
                  style={{ marginLeft: 'auto', fontSize: '0.68rem', padding: '5px 10px', border: '1px solid #d8d4cc', borderRadius: 6, background: '#fff', color: '#888', cursor: 'pointer' }}>
                  Copy from extracted
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
