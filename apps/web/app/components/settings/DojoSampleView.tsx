'use client';

import { useEffect, useMemo, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

// The Dojo's own sample view — deliberately NOT the Receive Invoice editor.
// A sample carries two extraction-shaped value sets and nothing else:
//   EXPECTED  — the baseline an ADMIN (or the analysis agent) authored: what
//               the LLM is EXPECTED to pull off the document. Editable here.
//               Never sourced from Loaded.
//   EXTRACTED — what the last run actually pulled under the current prompts.
// The toggle compares them; mismatched fields on the Extracted side are
// highlighted with the expected value alongside.
// An optional REPLICA view shows the Loaded-ready document the run built,
// with the unit trail surfaced per line: as printed → interpreted → resolved
// (replica lines map 1:1 to extraction lines by index — the builder emits
// one out-line per extraction line).

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
  customer_purchase_order_number?: string | null;
  lines?: ExtractionLine[];
  subtotal_ex_tax?: number | string | null;
  discount_amount?: number | string | null;
  tax_amount?: number | string | null;
  total_incl_tax?: number | string | null;
}

// The slice of a replica out-line this view renders (invoice_replica.py
// emits far more — linkage ids, tax, suggestions — all irrelevant here).
export interface ReplicaLine {
  code?: string | null;
  description?: string | null;
  quantity_received?: number | string | null;
  unit?: string | null;
  unit_cost?: number | string | null;
  total_cost?: number | string | null;
  linked_item_id?: string | null;
  item_name?: string | null;
  matched_by?: string | null;
}

export interface ReplicaDoc {
  reference_number?: string | null;
  supplier_name?: string | null;
  issued_at?: string | null;
  subtotal?: number | string | null;
  total?: number | string | null;
  lines?: ReplicaLine[];
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
  // The buyer's PO — the only order field the pipeline resolves. The
  // legacy catch-all purchase_order_number was retired 17 Aug 2026.
  { key: 'customer_purchase_order_number', label: 'PO number' },
  { key: 'subtotal_ex_tax', label: 'Subtotal ex tax' },
  { key: 'discount_amount', label: 'Discount' },
  { key: 'tax_amount', label: 'Tax' },
  { key: 'total_incl_tax', label: 'Total incl tax' },
];

const LINE_COLS: { key: keyof ExtractionLine; label: string; width?: number; numeric?: boolean; hint?: string }[] = [
  { key: 'code', label: 'Code', width: 90 },
  { key: 'description', label: 'Description' },
  { key: 'quantity', label: 'Qty', width: 70, numeric: true },
  // The raw unit column exactly as printed — NOT graded (the dojo compares
  // unit_of_measure only); shown so the extraction can be matched against
  // the replica's unit trail.
  { key: 'unit', label: 'Unit (printed)', width: 90, hint: 'the unit field exactly as printed on the document — informational, not graded' },
  { key: 'unit_of_measure', label: 'Unit of measure', width: 90, hint: 'the interpreted delivered unit of one item — the field the dojo grades' },
  { key: 'unit_price_ex_tax', label: 'Unit price', width: 85, numeric: true },
  { key: 'line_total_ex_tax', label: 'Line total', width: 85, numeric: true },
];

// A field's label over its value (meta role).
const microLabel: React.CSSProperties = { fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--muted)' };
// The line tables are .n-table, a little denser: nine columns share half the
// toolkit, so headers may wrap and cells align top (a value can carry its
// "expected" note underneath).
const thCell: React.CSSProperties = { padding: '8px', whiteSpace: 'normal', verticalAlign: 'bottom' };
const tdCell: React.CSSProperties = { padding: '8px', verticalAlign: 'top' };
// Editing: every cell is a field of the same height, so they centre.
const tdInputCell: React.CSSProperties = { padding: '4px', verticalAlign: 'middle' };
// Wide tables scroll sideways inside the card instead of spilling out of it.
const scrollBox: React.CSSProperties = { overflowX: 'auto' };
// The mismatch mark: the value on the warn tint, edged in warn.
const mismatchMark: React.CSSProperties = {
  background: 'var(--warn-bg)',
  border: '1px solid color-mix(in srgb, var(--warn) 40%, transparent)',
  borderRadius: 'var(--radius-sm)',
};

// The value-set toggle: the thread list's filter chip — the pressed one sits
// on --selected with a tan edge, the rest are outlined.
const toggle = (on: boolean, enabled = true): React.CSSProperties => ({
  flex: '0 0 auto',
  whiteSpace: 'nowrap',
  padding: '4px 10px',
  fontFamily: 'inherit',
  fontSize: 'var(--fs-xs)',
  fontWeight: on ? 600 : 500,
  color: on ? 'var(--text)' : 'var(--text-soft)',
  background: on ? 'var(--selected)' : 'var(--bg)',
  border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line)'}`,
  borderRadius: 999,
  cursor: enabled ? 'pointer' : 'not-allowed',
  opacity: enabled ? 1 : 0.45,
});

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
  current,
  currentDiffs,
  stored,
  replica,
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
  // Optional third value set: what the CURRENT prompts extract (the sample's
  // last run) — lets a proposal review compare before/after side by side.
  // undefined hides the toggle entirely; null shows it disabled (no run yet).
  current?: ExtractionDoc | null;
  currentDiffs?: Diff[];
  // Optional fourth value set: the baseline STORED on the sample today —
  // in a proposal review this is what regression currently tests against,
  // shown read-only beside the analysis's proposed values. undefined hides
  // the toggle; null shows it disabled (no baseline stored yet).
  stored?: ExtractionDoc | null;
  // Optional replica view: the Loaded-ready document the last run built.
  // Surfaces the unit trail (printed → interpreted → resolved) that the
  // extraction table hides. undefined hides the toggle; null shows it
  // disabled (no invoice view stored for this run).
  replica?: ReplicaDoc | null;
  labels?: { expected?: string; extracted?: string; expectedHint?: string; extractedHint?: string; current?: string; currentHint?: string; stored?: string; storedHint?: string };
}) {
  const [mode, setMode] = useState<'extracted' | 'expected' | 'current' | 'stored' | 'replica'>(extraction ? 'extracted' : 'expected');
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
  // lines by (1-based line, field). The current-prompt view highlights its
  // OWN diffs (current extraction vs the expected values); the stored
  // baseline is the reference set itself, so it carries no highlighting.
  const activeDiffs = mode === 'current' ? currentDiffs || [] : mode === 'stored' || mode === 'replica' ? [] : diffs;
  const diffMap = useMemo(() => {
    const header = new Map<string, Diff>();
    const line = new Map<string, Diff>();
    for (const d of activeDiffs || []) {
      if (d.line == null) header.set(d.field, d);
      else line.set(`${d.line}:${d.field}`, d);
    }
    return { header, line };
  }, [activeDiffs]);

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

  const doc =
    mode === 'expected' ? draft
    : mode === 'current' ? (current ?? null)
    : mode === 'stored' ? (stored ?? null)
    : mode === 'replica' ? null // the replica renders its own table below
    : extraction;
  const editable = mode === 'expected' && !readOnly;

  const numOrNull = (v: string): number | string | null => {
    if (v.trim() === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : v;
  };

  const mismatchStyle = (d: Diff | undefined): React.CSSProperties =>
    d ? mismatchMark : {};

  const statusChip = (() => {
    // The replica is a derived document, not a graded value set — its chip
    // says what it is.
    if (mode === 'replica') {
      return <Badge tone="info">Replica — the Loaded-ready document this run built</Badge>;
    }
    // The stored baseline is the reference set, not a verdict — its chip
    // says what it is rather than claiming any pass/fail.
    if (mode === 'stored') {
      return <Badge>Stored baseline — what regression tests against today</Badge>;
    }
    // The current-prompt view carries its own verdict — its diffs against the
    // expected values — so the chip must not claim the verification result.
    if (mode === 'current') {
      const n = (currentDiffs || []).length;
      const p: { tone: BadgeTone; label: string } = n === 0
        ? { tone: 'ok', label: 'Current prompt — matches the expected values' }
        : { tone: 'error', label: `Current prompt — ${n} mismatch${n === 1 ? '' : 'es'} vs expected` };
      return <Badge tone={p.tone}>{p.label}</Badge>;
    }
    // A fail with no field mismatches is a reconciliation failure: the numbers
    // match the baseline but the document's own arithmetic doesn't add up
    // (subtotal/lines/discount/tax vs total), so it must not read as a pass.
    const failLabel = diffs.length > 0
      ? `Fail — ${diffs.length} mismatch${diffs.length === 1 ? '' : 'es'}`
      : "Fail — totals don't reconcile";
    // "new" = no baseline yet, which an admin has to set: needs your input.
    const map: Record<string, { tone: BadgeTone; label: string }> = {
      pass: { tone: 'ok', label: 'Pass — extracted matches expected' },
      fail: { tone: 'error', label: failLabel },
      error: { tone: 'error', label: 'Error — last run failed' },
      new: { tone: 'accent', label: 'No baseline — set the expected values' },
    };
    const p = map[status] || map.new;
    return <Badge tone={p.tone}>{p.label}</Badge>;
  })();

  return (
    <div className="n-card" style={{ padding: '12px 14px', color: 'var(--text)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px 10px', marginBottom: 12, flexWrap: 'wrap' }}>
        {/* The toggle: which value set the table below shows. Stored baseline
            sits FIRST, then current-prompt (before → after reads left to
            right). */}
        <div role="group" aria-label="Value set" style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 6 }}>
          {stored !== undefined && (
            <button type="button" onClick={() => setMode('stored')} disabled={!stored} aria-pressed={mode === 'stored'}
              title={stored ? undefined : 'no baseline stored on this sample yet'}
              style={toggle(mode === 'stored', !!stored)}>
              {labels?.stored ?? 'Current expected values'}
            </button>
          )}
          {current !== undefined && (
            <button type="button" onClick={() => setMode('current')} disabled={!current} aria-pressed={mode === 'current'}
              title={current ? undefined : 'no current-prompt run stored yet — press Run on the sample'}
              style={toggle(mode === 'current', !!current)}>
              {labels?.current ?? 'Current prompt'}
            </button>
          )}
          <button type="button" onClick={() => setMode('expected')} aria-pressed={mode === 'expected'}
            style={toggle(mode === 'expected')}>
            {labels?.expected ?? (readOnly ? 'Expected' : 'Expected (editable)')}
          </button>
          <button type="button" onClick={() => setMode('extracted')} disabled={!extraction} aria-pressed={mode === 'extracted'}
            title={extraction ? undefined : 'no extraction run stored yet — press Run'}
            style={toggle(mode === 'extracted', !!extraction)}>
            {labels?.extracted ?? 'Extracted (last run)'}
          </button>
          {replica !== undefined && (
            <button type="button" onClick={() => setMode('replica')} disabled={!replica} aria-pressed={mode === 'replica'}
              title={replica ? undefined : 'no invoice view stored for this run — press Run to build it'}
              style={toggle(mode === 'replica', !!replica)}>
              Replica
            </button>
          )}
        </div>
        {statusChip}
        {mode === 'expected' && (
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            {labels?.expectedHint ?? 'what the LLM SHOULD pull off this document — authored here (or by the analysis agent), never from Loaded'}
          </span>
        )}
        {mode === 'extracted' && (
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            {labels?.extractedHint ?? 'what the last run actually pulled — mismatches vs expected are highlighted'}
          </span>
        )}
        {mode === 'current' && (
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            {labels?.currentHint ?? 'what the CURRENT prompts pull from this document — mismatches vs the expected values are highlighted'}
          </span>
        )}
        {mode === 'stored' && (
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            {labels?.storedHint ?? 'the baseline stored on the sample today — what regression currently tests against'}
          </span>
        )}
        {mode === 'replica' && (
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            the extraction resolved against the Loaded catalogue — each line shows the unit as printed, the interpreted delivered unit, and the unit the replica settled on
          </span>
        )}
      </div>
      {error && <div style={{ marginBottom: 10 }}><PageState kind="error" title={error} /></div>}

      {mode === 'expected' && !draft && !readOnly && (
        <div style={{ padding: '10px 0' }}>
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', marginBottom: 8 }}>
            No expected values stored yet.
          </div>
          {extraction ? (
            <Button size="sm" onClick={() => { setDraft(deepCopy(extraction)); setDirty(true); }}>
              Start from the extracted values
            </Button>
          ) : (
            <Button size="sm" onClick={() => { setDraft({ document_type: 'invoice', lines: [{}] }); setDirty(true); }}>
              Start from scratch
            </Button>
          )}
        </div>
      )}

      {doc && (
        <>
          {/* Header fields */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '10px 12px', marginBottom: 12 }}>
            {HEADER_FIELDS.map(({ key, label }) => {
              const d = mode !== 'expected' ? diffMap.header.get(key as string) : undefined;
              const val = doc[key];
              return (
                <label key={key as string} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <span style={microLabel}>{label}</span>
                  {editable ? (
                    <input className="n-input" style={{ width: '100%' }} value={val == null ? '' : String(val)}
                      onChange={(e) => edit((x) => {
                        (x as Record<string, unknown>)[key as string] =
                          ['subtotal_ex_tax', 'tax_amount', 'total_incl_tax'].includes(key as string)
                            ? numOrNull(e.target.value)
                            : (e.target.value || null);
                      })} />
                  ) : (
                    <span style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', padding: '2px 6px', margin: '0 -6px', minHeight: 18, ...mismatchStyle(d) }}
                      title={d ? `expected: ${JSON.stringify(d.expected ?? null)}` : undefined}>
                      {val == null || val === '' ? '—' : String(val)}
                      {d && <span style={{ color: 'var(--warn)', marginLeft: 6, fontSize: 'var(--fs-xs)' }}>expected {JSON.stringify(d.expected ?? null)}</span>}
                    </span>
                  )}
                </label>
              );
            })}
          </div>

          {/* Lines */}
          <div style={scrollBox}>
          <table className="n-table">
            <thead>
              <tr>
                {LINE_COLS.map((c) => <th key={c.key as string} className={c.numeric ? 'num' : undefined} style={{ ...thCell, width: c.width }} title={c.hint}>{c.label}</th>)}
                <th style={{ ...thCell, width: 86 }} title="the document shows size info that can't be read">Unreadable unit</th>
                {editable && <th style={{ ...thCell, width: 36 }} />}
              </tr>
            </thead>
            <tbody>
              {(doc.lines || []).map((l, i) => (
                <tr key={i} style={mode !== 'expected' && diffMap.line.has(`${i + 1}:line_extra`) ? { background: 'var(--warn-bg)' } : undefined}
                  title={mode !== 'expected' && diffMap.line.has(`${i + 1}:line_extra`) ? 'extracted line not present in the expected values' : undefined}>
                  {LINE_COLS.map((c) => {
                    const d = mode !== 'expected' ? diffMap.line.get(`${i + 1}:${c.key as string}`) : undefined;
                    const val = l[c.key];
                    return (
                      <td key={c.key as string} className={c.numeric ? 'num' : undefined} style={editable ? tdInputCell : tdCell}>
                        {editable ? (
                          <input className="n-input" style={{ width: '100%', minWidth: c.key === 'description' ? 160 : 56, textAlign: c.numeric ? 'right' : 'left' }}
                            value={val == null ? '' : String(val)}
                            onChange={(e) => edit((x) => {
                              const ln = (x.lines || [])[i] as Record<string, unknown>;
                              ln[c.key as string] = c.numeric ? numOrNull(e.target.value) : (e.target.value || null);
                            })} />
                        ) : (
                          <span style={{ display: 'inline-block', padding: '1px 4px', margin: '0 -4px', textAlign: c.numeric ? 'right' : 'left', ...mismatchStyle(d) }}
                            title={d ? `expected: ${JSON.stringify(d.expected ?? null)}` : undefined}>
                            {val == null || val === '' ? '—' : String(val)}
                            {d && <div style={{ color: 'var(--warn)', fontSize: 'var(--fs-xs)' }}>expected {JSON.stringify(d.expected ?? null)}</div>}
                          </span>
                        )}
                      </td>
                    );
                  })}
                  <td style={{ ...(editable ? tdInputCell : tdCell), textAlign: 'center' }}>
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
                    <td style={{ ...tdInputCell, padding: '4px 2px' }}>
                      <IconButton icon={X} iconSize={16} label="Remove this line from the expected values"
                        onClick={() => edit((x) => { (x.lines || []).splice(i, 1); })} />
                    </td>
                  )}
                </tr>
              ))}
              {/* Lines the run pulled beyond the baseline, or vice versa */}
              {mode !== 'expected' && [...diffMap.line.entries()]
                .filter(([k]) => k.endsWith(':line_missing'))
                .map(([k, d]) => (
                  <tr key={k}>
                    <td colSpan={LINE_COLS.length + 1} style={{ ...tdCell, fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <Icon icon={X} size="dense" />
                        expected line {d.line} “{d.description}” was NOT extracted
                      </span>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          </div>
          {editable && (
            <Button size="sm" variant="quiet" icon={Plus} style={{ marginTop: 6 }}
              onClick={() => edit((x) => { x.lines = [...(x.lines || []), {}]; })}>
              Add line
            </Button>
          )}


          {editable && (
            <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
              <Button size="sm" variant="primary" onClick={save} disabled={!dirty || saving}>
                {saving ? 'Saving…' : 'Save expected values'}
              </Button>
              {dirty && (
                <Button size="sm" onClick={() => { setDraft(deepCopy(expected)); setDirty(false); }}>
                  Revert
                </Button>
              )}
              {extraction && (
                <Button size="sm" variant="quiet" onClick={() => { setDraft(deepCopy(extraction)); setDirty(true); }}
                  title="overwrite the draft with the last run's extracted values"
                  style={{ marginLeft: 'auto' }}>
                  Copy from extracted
                </Button>
              )}
            </div>
          )}
        </>
      )}

      {mode === 'replica' && replica && (
        <>
          {/* Replica header — the document the run would hand to Loaded */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '10px 12px', marginBottom: 12 }}>
            {([
              ['Supplier (resolved)', replica.supplier_name],
              ['Invoice number', replica.reference_number],
              ['Invoice date', replica.issued_at],
              ['Subtotal ex tax', replica.subtotal],
              ['Total incl tax', replica.total],
            ] as [string, unknown][]).map(([label, val]) => (
              <label key={label} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={microLabel}>{label}</span>
                <span style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', padding: '2px 0', minHeight: 18 }}>
                  {val == null || val === '' ? '—' : String(val)}
                </span>
              </label>
            ))}
          </div>

          {/* Lines with the unit trail: printed → interpreted → resolved.
              Replica line i is built FROM extraction line i, so the printed
              and interpreted columns read straight from the extraction. */}
          <div style={scrollBox}>
          <table className="n-table">
            <thead>
              <tr>
                <th style={{ ...thCell, width: 90 }}>Code</th>
                <th style={thCell}>Description</th>
                <th className="num" style={{ ...thCell, width: 60 }}>Qty</th>
                <th style={{ ...thCell, width: 90 }} title="the unit field exactly as printed on the document">Unit (printed)</th>
                <th style={{ ...thCell, width: 90 }} title="the interpreted delivered unit of one item — the field the dojo grades">Unit of measure</th>
                <th style={{ ...thCell, width: 90 }} title="the Loaded unit the replica settled on">Resolved unit</th>
                <th className="num" style={{ ...thCell, width: 80 }}>Unit price</th>
                <th className="num" style={{ ...thCell, width: 80 }}>Line total</th>
                <th style={{ ...thCell, width: 140 }}>Stock item</th>
              </tr>
            </thead>
            <tbody>
              {(replica.lines || []).map((l, i) => {
                const el = extraction?.lines?.[i];
                const show = (v: unknown) => (v == null || v === '' ? '—' : String(v));
                // The case that bites: no interpreted unit, yet the replica
                // resolved one anyway (from the printed unit or the linked
                // variant) — flag it so the gap doesn't hide behind the
                // resolved value.
                const resolvedWithoutUom = (el?.unit_of_measure == null || el?.unit_of_measure === '') && l.unit != null && l.unit !== '';
                return (
                  <tr key={i}>
                    <td style={tdCell}>{show(l.code)}</td>
                    <td style={tdCell}>{show(l.description)}</td>
                    <td className="num" style={tdCell}>{show(l.quantity_received)}</td>
                    <td style={{ ...tdCell, color: 'var(--muted)' }}>{show(el?.unit)}</td>
                    <td style={tdCell}>{show(el?.unit_of_measure)}</td>
                    <td style={tdCell}>
                      <span style={resolvedWithoutUom ? { ...mismatchMark, padding: '1px 4px' } : {}}
                        title={resolvedWithoutUom ? 'the interpreted delivered unit was NOT extracted — this unit was resolved from the printed unit or the linked variant' : undefined}>
                        {show(l.unit)}
                      </span>
                    </td>
                    <td className="num" style={tdCell}>{show(l.unit_cost)}</td>
                    <td className="num" style={tdCell}>{show(l.total_cost)}</td>
                    <td style={tdCell} title={l.matched_by ? `matched by ${l.matched_by}` : undefined}>
                      {l.linked_item_id
                        ? show(l.item_name)
                        : <span style={{ color: 'var(--warn)' }}>new item — not in the catalogue</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 8 }}>
            Resolved unit precedence: the linked variant&apos;s default unit, else the unit of measure, else the unit as printed — each matched against the venue&apos;s Loaded unit list.
          </div>
        </>
      )}
    </div>
  );
}
