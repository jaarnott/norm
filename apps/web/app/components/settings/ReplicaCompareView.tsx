'use client';

import { type CSSProperties, type ReactNode } from 'react';
import { Check, TriangleAlert } from 'lucide-react';
import { formatMoney } from '../../lib/format';
import Button from '../ui/Button';
import Icon from '../ui/Icon';

/**
 * The dojo's replica view, rendered as an actual INVOICE (paper sheet,
 * supplier header, line table, totals): what our extraction says the
 * document is, fully resolved.
 *
 * Extracted-only since Aug 2026 — the replica is primary, so the Loaded and
 * Diff modes (and their tab slider) are gone from the UI. InvoiceSheet still
 * takes a mode because its render paths are shared, but this view never
 * leaves 'extracted'.
 *
 * Data is the server-paired compare structure (`replica_compare`);
 * `replicaDoc` is the raw replica document, used to fill header fields for
 * runs stored before a field joined the compare payload. Read-only.
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
  typeof v === 'number' ? formatMoney(v) : v == null ? '—' : String(v);

function niceDate(v: unknown): string {
  if (typeof v !== 'string' || !v) return '—';
  const d = new Date(v.slice(0, 10) + 'T00:00:00');
  if (isNaN(d.getTime())) return v;
  return d.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short', year: 'numeric' });
}

const scrollBox: CSSProperties = { overflowX: 'auto', WebkitOverflowScrolling: 'touch' };
// A small line under a value: codes, links, provenance.
const meta: CSSProperties = { fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 2 };
const diffMark: CSSProperties = { background: 'var(--error-bg)', color: 'var(--error)', borderRadius: 3, padding: '0 3px' };
const loadedNote: CSSProperties = { ...meta, fontWeight: 400 };
// The line table is an .n-table; cells hold several lines, so they align top.
const cellTop: CSSProperties = { verticalAlign: 'top' };
// The pane's header strip — the same height and look as InvoicePdfPane's, so
// the two panes line up side by side.
const paneHeader: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', minHeight: 46, boxSizing: 'border-box',
  padding: '6px 12px 6px 16px', borderBottom: '1px solid var(--line)',
};

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
    <div style={{ background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)', padding: 'clamp(14px, 3vw, 28px)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 'var(--fs-lg)', fontWeight: 600, color: 'var(--text)', lineHeight: 1.3 }}>
            <DiffValue value={hv('supplier_name')} {...hd('supplier_name')} />
          </div>
          <div style={meta}>
            {hv('linked_supplier_id') ? 'linked supplier record' : 'no supplier record linked'}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="n-eyebrow">Tax invoice</div>
          <div style={meta}>
            {diff ? 'extracted, differences vs Loaded marked' : side === 'replica' ? 'as extracted by Norm' : 'as held in Loaded'}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '8px 24px', flexWrap: 'wrap', margin: '16px 0 4px' }}>
        {(
          [
            ['Invoice no.', 'reference_number', (v: unknown) => (v == null ? '—' : String(v))],
            ['Date', 'issued_at', niceDate],
            ['Order no.', 'purchase_order_number', (v: unknown) => (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                {v == null ? '—' : String(v)}
                {hv('linked_purchase_order_id') ? (
                  <Icon icon={Check} size="dense" label="linked purchase order" style={{ color: 'var(--ok)' }} />
                ) : null}
              </span>
            )],
          ] as const
        ).map(([label, field, render]) => (
          <div key={label}>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{label}</div>
            <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', marginTop: 2 }}>
              <DiffValue value={hv(field)} {...hd(field)} render={render} />
            </div>
          </div>
        ))}
      </div>

      <div style={scrollBox}>
        <table className="n-table" style={{ marginTop: 16, minWidth: 480 }}>
          <thead>
            <tr>
              <th>Item</th>
              <th>Unit</th>
              <th className="num">Qty</th>
              <th className="num">Unit price</th>
              <th className="num">Amount</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((r, i) => {
              const l = (side === 'loaded' ? r.loaded : r.replica) ?? r.loaded ?? {};
              const other = r.loaded ?? {};
              const oneSided = diff && (!r.replica || !r.loaded);
              const amount = l.total_cost ?? (l.quantity_received != null && l.unit_cost != null ? l.quantity_received * l.unit_cost : null);
              return (
                <tr key={i} style={oneSided ? { background: 'var(--warn-bg)' } : undefined}>
                  <td style={cellTop}>
                    <div style={{ fontWeight: 500, color: 'var(--text)' }}>{l.description || l.item_name || '—'}</div>
                    <div style={meta}>
                      {l.code ? `${l.code} · ` : ''}
                      {flagged(r, 'linked_item_id') ? (
                        <>
                          <span style={diffMark}>{l.linked_item_id ? l.item_name || 'stock item' : 'no stock item'}</span>
                          <span>
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
                      <div style={{ ...meta, color: 'var(--warn)' }}>
                        {r.replica ? 'only in the extraction — Loaded has no such line' : 'only in Loaded — not on the extracted copy'}
                      </div>
                    )}
                  </td>
                  <td style={{ ...cellTop, whiteSpace: 'nowrap' }}>
                    <DiffValue value={l.unit} loadedValue={other.unit} differs={flagged(r, 'linked_unit_id')} />
                  </td>
                  <td className="num" style={cellTop}>
                    <DiffValue value={l.quantity_received} loadedValue={other.quantity_received} differs={flagged(r, 'quantity_received')} />
                  </td>
                  <td className="num" style={cellTop}>
                    <DiffValue value={l.unit_cost} loadedValue={other.unit_cost} differs={flagged(r, 'unit_cost')} render={money} />
                  </td>
                  <td className="num" style={{ ...cellTop, fontWeight: 500 }}>{money(amount)}</td>
                </tr>
              );
            })}
            {lines.length === 0 && (
              <tr><td colSpan={5} style={{ color: 'var(--muted)' }}>No lines</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Totals: the right padding matches a table cell's, so the figures
          line up under the Amount column. */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
        <div style={{ minWidth: 200, paddingRight: 12, fontVariantNumeric: 'tabular-nums' }}>
          {(
            [
              ['Subtotal', 'subtotal', false],
              [gstLabel, 'tax_amount', false],
              ['Total', 'total', true],
            ] as const
          ).map(([label, field, strong]) => (
            <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 24, padding: strong ? '8px 0 0' : '2px 0', borderTop: strong ? '1px solid var(--line-strong)' : 'none', marginTop: strong ? 6 : 0 }}>
              <span style={{ fontSize: strong ? 'var(--fs-base)' : 'var(--fs-sm)', color: strong ? 'var(--text)' : 'var(--text-soft)', fontWeight: strong ? 600 : 400 }}>{label}</span>
              <span style={{ fontSize: strong ? 'var(--fs-base)' : 'var(--fs-sm)', color: 'var(--text)', fontWeight: strong ? 700 : 400, textAlign: 'right' }}>
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
  onAnalyse,
  analysing,
}: {
  compare: ReplicaCompare;
  replicaDoc?: Record<string, unknown> | null;
  resolutionLog?: string[] | null;
  warnings?: string[] | null;
  onAnalyse?: () => void;
  analysing?: boolean;
}) {
  // Extracted-only since Aug 2026: the replica is primary, so Loaded's own
  // resolution (and the diff against it) is no longer shown here — the tab
  // slider went with it. InvoiceSheet keeps its mode prop because the render
  // paths are shared; this view simply never leaves 'extracted'.
  // The pane is a card: header strip, any warnings as a band, then the
  // invoice as a white sheet on a tinted desk (as InvoicePdfPane shows the
  // copy's pages), and the resolution log as a footer.
  return (
    <div className="n-card" style={{ overflow: 'hidden' }}>
      <div style={paneHeader}>
        <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>Invoice — as extracted by Norm</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {onAnalyse && (
            <Button size="sm" onClick={onAnalyse} disabled={!!analysing}
              title="the sensei studies this invoice with full context and drafts a spec update (1–2 min) — the proposal appears above this view">
              {analysing ? 'Sensei analysing…' : 'Ask the sensei'}
            </Button>
          )}
        </span>
      </div>

      {(warnings ?? []).length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '8px 16px', background: 'var(--warn-bg)', color: 'var(--warn)', fontSize: 'var(--fs-sm)', borderBottom: '1px solid var(--line)' }}>
          {warnings!.map((w, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <Icon icon={TriangleAlert} size="dense" style={{ marginTop: 2 }} />
              <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{w}</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ background: 'var(--surface-alt)', padding: 'clamp(8px, 2vw, 12px)' }}>
        <InvoiceSheet compare={compare} mode="extracted" replicaDoc={replicaDoc} />
      </div>

      {Array.isArray(resolutionLog) && resolutionLog.length > 0 && (
        <details style={{ padding: '10px 16px', borderTop: '1px solid var(--line)' }}>
          <summary style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text-soft)', cursor: 'pointer' }}>Resolution log</summary>
          <ul style={{ margin: '8px 0 0', padding: 0, listStyle: 'none', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--text-soft)', overflowWrap: 'anywhere' }}>
            {resolutionLog.map((e, i) => (
              <li key={i} style={{ padding: '1px 0' }}>{e}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
