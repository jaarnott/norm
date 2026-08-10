'use client';

import { useState, type CSSProperties, type ReactNode } from 'react';

/**
 * The dojo's replica view — three modes, all rendered as an actual INVOICE
 * (paper sheet, supplier header, line table, totals):
 *
 * - Extracted (default): what our extraction says the document is, fully
 *   resolved.
 * - Loaded: the same render from Loaded's document.
 * - Diff: the extracted invoice with every disagreement annotated in place —
 *   red on the differing value, Loaded's value in small print beneath.
 *
 * Data is the server-paired compare structure (`replica_compare`);
 * `replicaDoc` is the raw replica document, used to fill header fields for
 * runs stored before a field joined the compare payload. Read-only; the
 * bless button adjudicates "the replica is right and Loaded is wrong".
 */

export interface CompareHeaderRow {
  field: string;
  replica: unknown;
  loaded: unknown;
  differs: boolean;
}
export interface CompareLineSide {
  code?: string | null;
  description?: string | null;
  item_name?: string | null;
  linked_item_id?: string | null;
  unit?: string | null;
  linked_unit_id?: string | null;
  quantity_received?: number | null;
  unit_cost?: number | null;
  total_cost?: number | null;
  sale_tax_rate?: number | null;
  matched_by?: string | null;
}
export interface CompareLineRow {
  replica: CompareLineSide | null;
  loaded: CompareLineSide | null;
  diff_fields: string[];
}
export interface ReplicaCompare {
  header: CompareHeaderRow[];
  lines: CompareLineRow[];
}

type Mode = 'extracted' | 'loaded' | 'diff';

const MATCHED_BY_LABEL: Record<string, string> = {
  supplier_code: 'supplier code',
  code: 'code',
  description_exact: 'description',
  description_substring: 'description',
  llm: 'AI match',
};

const money = (v: unknown) =>
  typeof v === 'number' ? `$${v.toFixed(2)}` : v == null ? '—' : String(v);

function niceDate(v: unknown): string {
  if (typeof v !== 'string' || !v) return '—';
  const d = new Date(v.slice(0, 10) + 'T00:00:00');
  if (isNaN(d.getTime())) return v;
  return d.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short', year: 'numeric' });
}

const scrollBox: CSSProperties = { overflowX: 'auto', WebkitOverflowScrolling: 'touch' };
const diffMark: CSSProperties = { background: '#fdeaea', color: '#a02b2b', borderRadius: 3, padding: '0 3px' };
const loadedNote: CSSProperties = { fontSize: '0.6rem', color: '#8a8a8a', marginTop: 1, fontWeight: 400 };

const sheetTh: CSSProperties = {
  padding: '0.45rem 0.55rem',
  fontSize: '0.6rem',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  color: '#9a938a',
  textAlign: 'left',
  borderBottom: '2px solid #2c2c2c',
  whiteSpace: 'nowrap',
};
const sheetTd: CSSProperties = {
  padding: '0.55rem 0.55rem',
  fontSize: '0.78rem',
  color: '#2c2c2c',
  verticalAlign: 'top',
  borderBottom: '1px solid #efece7',
};
const num: CSSProperties = { textAlign: 'right', fontVariantNumeric: 'tabular-nums' };

/** A value with optional diff annotation: red mark + Loaded's value below. */
function DiffValue({ value, loadedValue, differs, render = (v) => (v == null ? '—' : String(v)) }: {
  value: unknown;
  loadedValue?: unknown;
  differs?: boolean;
  render?: (v: unknown) => ReactNode;
}) {
  if (!differs) return <>{render(value)}</>;
  return (
    <>
      <span style={diffMark}>{render(value)}</span>
      <div style={loadedNote}>Loaded: {render(loadedValue)}</div>
    </>
  );
}

function InvoiceSheet({ compare, mode, replicaDoc }: {
  compare: ReplicaCompare;
  mode: Mode;
  replicaDoc?: Record<string, unknown> | null;
}) {
  const side: 'replica' | 'loaded' = mode === 'loaded' ? 'loaded' : 'replica';
  const diff = mode === 'diff';
  const row = (field: string) => compare.header.find((h) => h.field === field);
  const hv = (field: string): unknown => {
    const r = row(field);
    if (r) return r[side];
    // Runs stored before a field joined the compare payload: fall back to
    // the raw replica document.
    if (side === 'replica' && replicaDoc) return replicaDoc[field];
    return undefined;
  };
  const hd = (field: string) => (diff ? { loadedValue: row(field)?.loaded, differs: row(field)?.differs } : {});

  const lines = diff
    ? compare.lines
    : compare.lines.filter((r) => r[side] != null).map((r) => ({ ...r, diff_fields: [] as string[] }));
  const rates = new Set(
    lines.map((l) => (l.replica ?? l.loaded)?.sale_tax_rate).filter((r) => r != null),
  );
  const gstLabel = rates.size === 1 ? `GST ${(([...rates][0] as number) * 100).toFixed(0)}%` : 'GST';

  const flagged = (l: CompareLineRow, f: string) => diff && l.diff_fields.includes(f);

  return (
    <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 6px rgba(30,25,15,0.10)', padding: 'clamp(14px, 3vw, 28px)', margin: '10px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: '1.05rem', fontWeight: 700, color: '#1e1c18' }}>
            <DiffValue value={hv('supplier_name')} {...hd('supplier_name')} />
          </div>
          <div style={{ fontSize: '0.66rem', color: '#9a938a', marginTop: 2 }}>
            {hv('linked_supplier_id') ? 'linked supplier record' : 'no supplier record linked'}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: '0.68rem', letterSpacing: '0.18em', color: '#b78a2f', fontWeight: 700 }}>TAX INVOICE</div>
          <div style={{ fontSize: '0.62rem', color: '#9a938a', marginTop: 2 }}>
            {diff ? 'extracted, differences vs Loaded marked' : side === 'replica' ? 'as extracted by Norm' : 'as held in Loaded'}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '1.6rem', flexWrap: 'wrap', margin: '14px 0 4px' }}>
        {(
          [
            ['Invoice no.', 'reference_number', (v: unknown) => (v == null ? '—' : String(v))],
            ['Date', 'issued_at', niceDate],
            ['Order no.', 'purchase_order_number', (v: unknown) => `${v == null ? '—' : String(v)}${hv('linked_purchase_order_id') ? ' ✓' : ''}`],
          ] as const
        ).map(([label, field, render]) => (
          <div key={label}>
            <div style={{ fontSize: '0.58rem', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#9a938a' }}>{label}</div>
            <div style={{ fontSize: '0.78rem', color: '#2c2c2c', marginTop: 1 }}>
              <DiffValue value={hv(field)} {...hd(field)} render={render} />
            </div>
          </div>
        ))}
      </div>

      <div style={scrollBox}>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10, minWidth: 480 }}>
          <thead>
            <tr>
              <th style={sheetTh}>Item</th>
              <th style={sheetTh}>Unit</th>
              <th style={{ ...sheetTh, ...num }}>Qty</th>
              <th style={{ ...sheetTh, ...num }}>Unit price</th>
              <th style={{ ...sheetTh, ...num }}>Amount</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((r, i) => {
              const l = (side === 'loaded' ? r.loaded : r.replica) ?? r.loaded ?? {};
              const other = r.loaded ?? {};
              const oneSided = diff && (!r.replica || !r.loaded);
              const amount = l.total_cost ?? (l.quantity_received != null && l.unit_cost != null ? l.quantity_received * l.unit_cost : null);
              return (
                <tr key={i} style={oneSided ? { background: '#fdf6e7' } : undefined}>
                  <td style={sheetTd}>
                    <div style={{ fontWeight: 600 }}>{l.description || l.item_name || '—'}</div>
                    <div style={{ fontSize: '0.62rem', color: '#9a938a', marginTop: 1 }}>
                      {l.code ? `${l.code} · ` : ''}
                      {flagged(r, 'linked_item_id') ? (
                        <>
                          <span style={diffMark}>{l.linked_item_id ? l.item_name || 'stock item' : 'no stock item'}</span>
                          <span style={{ color: '#8a8a8a' }}>
                            {' '}· Loaded: {other.linked_item_id ? other.item_name || 'stock item' : 'no stock item linked'}
                          </span>
                        </>
                      ) : l.linked_item_id ? (
                        `${l.item_name || 'stock item'}${l.matched_by ? ` (${MATCHED_BY_LABEL[l.matched_by] ?? l.matched_by})` : ''}`
                      ) : side === 'replica' && !oneSided ? (
                        'new item — not in the catalogue'
                      ) : (
                        'no stock item linked'
                      )}
                    </div>
                    {flagged(r, 'sale_tax_rate') && (
                      <div style={loadedNote}>
                        GST <span style={diffMark}>{l.sale_tax_rate != null ? `${(l.sale_tax_rate * 100).toFixed(0)}%` : '—'}</span>
                        {' '}· Loaded: {other.sale_tax_rate != null ? `${(other.sale_tax_rate * 100).toFixed(0)}%` : '—'}
                      </div>
                    )}
                    {oneSided && (
                      <div style={{ fontSize: '0.6rem', color: '#8a6d3b', marginTop: 1 }}>
                        {r.replica ? 'only in the extraction — Loaded has no such line' : 'only in Loaded — not on the extracted copy'}
                      </div>
                    )}
                  </td>
                  <td style={{ ...sheetTd, whiteSpace: 'nowrap' }}>
                    <DiffValue value={l.unit} loadedValue={other.unit} differs={flagged(r, 'linked_unit_id')} />
                  </td>
                  <td style={{ ...sheetTd, ...num }}>
                    <DiffValue value={l.quantity_received} loadedValue={other.quantity_received} differs={flagged(r, 'quantity_received')} />
                  </td>
                  <td style={{ ...sheetTd, ...num }}>
                    <DiffValue value={l.unit_cost} loadedValue={other.unit_cost} differs={flagged(r, 'unit_cost')} render={money} />
                  </td>
                  <td style={{ ...sheetTd, ...num, fontWeight: 600 }}>{money(amount)}</td>
                </tr>
              );
            })}
            {lines.length === 0 && (
              <tr><td colSpan={5} style={{ ...sheetTd, color: '#9a938a' }}>no lines</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
        <div style={{ minWidth: 190 }}>
          {(
            [
              ['Subtotal', 'subtotal', false],
              [gstLabel, 'tax_amount', false],
              ['Total', 'total', true],
            ] as const
          ).map(([label, field, strong]) => (
            <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: 24, padding: strong ? '7px 0 0' : '2px 0', borderTop: strong ? '2px solid #2c2c2c' : 'none', marginTop: strong ? 6 : 0 }}>
              <span style={{ fontSize: strong ? '0.82rem' : '0.72rem', color: strong ? '#1e1c18' : '#6b655c', fontWeight: strong ? 700 : 400 }}>{label}</span>
              <span style={{ fontSize: strong ? '0.92rem' : '0.74rem', color: '#1e1c18', fontWeight: strong ? 700 : 500, fontVariantNumeric: 'tabular-nums', textAlign: 'right' }}>
                <DiffValue value={hv(field)} {...hd(field)} render={money} />
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------ */

export default function ReplicaCompareView({
  compare,
  replicaDoc,
  resolutionLog,
  warnings,
  onBless,
  blessed,
  onAnalyse,
  analysing,
}: {
  compare: ReplicaCompare;
  replicaDoc?: Record<string, unknown> | null;
  resolutionLog?: string[] | null;
  warnings?: string[] | null;
  onBless?: () => void;
  blessed?: boolean;
  onAnalyse?: () => void;
  analysing?: boolean;
}) {
  const [mode, setMode] = useState<Mode>('extracted');

  const totalDiffs =
    compare.header.filter((h) => h.differs).length +
    compare.lines.reduce(
      (n, r) => n + r.diff_fields.length + (!r.replica || !r.loaded ? 1 : 0),
      0,
    );

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, background: '#f2f0ec', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0.5rem 0.7rem', background: '#faf9f7', borderBottom: '1px solid #eee', flexWrap: 'wrap' }}>
        <span style={{ display: 'inline-flex', border: '1px solid #ccc', borderRadius: 6, overflow: 'hidden', fontSize: '0.7rem' }}>
          {(
            [
              ['extracted', 'Extracted'],
              ['loaded', 'Loaded'],
              ['diff', `Diff${totalDiffs ? ` (${totalDiffs})` : ''}`],
            ] as const
          ).map(([m, label]) => (
            <button key={m} type="button" onClick={() => setMode(m)}
              style={{ padding: '3px 10px', border: 'none', cursor: 'pointer', fontFamily: 'inherit', background: mode === m ? (m === 'diff' ? '#b78a2f' : '#3a3a3a') : '#fff', color: mode === m ? '#fff' : '#666', whiteSpace: 'nowrap' }}>
              {label}
            </button>
          ))}
        </span>
        <span style={{ fontSize: '0.66rem', color: totalDiffs ? '#a02b2b' : '#2e7d4f' }}>
          {totalDiffs ? `${totalDiffs} difference${totalDiffs === 1 ? '' : 's'}` : '✓ full resolution parity'}
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {blessed && <span style={{ fontSize: '0.64rem', color: '#2e7d4f' }}>✓ adjudicated</span>}
          {onAnalyse && (
            <button type="button" onClick={onAnalyse} disabled={!!analysing}
              title="the sensei studies this invoice with full context and drafts a spec update (1–2 min) — the proposal appears above this view"
              style={{ fontSize: '0.64rem', padding: '2px 9px', border: '1px solid #b78a2f', borderRadius: 4, background: '#fff', color: '#8a6d3b', cursor: analysing ? 'default' : 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
              {analysing ? 'Sensei analysing…' : 'Ask the sensei'}
            </button>
          )}
          {onBless && (
            <button type="button" onClick={onBless}
              title="Adjudicate: the replica is right and Loaded is wrong — later runs score against these values"
              style={{ fontSize: '0.64rem', padding: '2px 9px', border: '1px solid #b78a2f', borderRadius: 4, background: '#fff', color: '#8a6d3b', cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
              Bless replica
            </button>
          )}
        </span>
      </div>

      {(warnings ?? []).length > 0 && (
        <div style={{ margin: '0.5rem 0.7rem 0', padding: '6px 10px', background: '#fdf6e7', border: '1px solid #e6cf9a', borderRadius: 6, fontSize: '0.7rem', color: '#8a6d3b' }}>
          {warnings!.map((w, i) => (
            <div key={i}>⚠ {w}</div>
          ))}
        </div>
      )}

      <InvoiceSheet compare={compare} mode={mode} replicaDoc={replicaDoc} />

      {Array.isArray(resolutionLog) && resolutionLog.length > 0 && (
        <details style={{ padding: '0.3rem 0.7rem 0.6rem' }}>
          <summary style={{ fontSize: '0.7rem', color: '#666', cursor: 'pointer' }}>Resolution log</summary>
          <ul style={{ margin: '6px 0 0 0', paddingLeft: 18, fontSize: '0.66rem', color: '#555' }}>
            {resolutionLog.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
