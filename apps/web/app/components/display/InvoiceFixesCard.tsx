'use client';

import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../../lib/api';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface RawLine {
  id: string;
  code: string | null;
  description: string | null;
  brand: string | null;
  linked_item_id: string | null;
  linked_unit_id: string | null;
  unit: string | null;
  quantity_received: number | null;
  unit_cost: number | null;
  total_cost: number | null;
  copy_unit: string | null;
  copy_quantity: number | null;
  copy_unit_price: number | null;
  recommended_unit: string | null;
}

interface Suggestion {
  type: 'link_po' | 'unit';
  po_number?: string;
  line_id?: string;
  current_unit?: string;
  proposed_unit?: string;
}

interface FixInvoice {
  invoice_id: string;
  reference_number: string;
  supplier_name: string | null;
  purchase_order_number: string | null;
  linked_purchase_order_id: string | null;
  issued_at: string | null;
  due_at: string | null;
  subtotal: number | null;
  tax_amount: number | null;
  total: number | null;
  lines: RawLine[];
  suggestions: Suggestion[];
}

interface Unit { id: string; name: string; type: string; ratio: number }
interface PO { id: string; order_number: string; supplier_name: string | null }

const inputStyle: React.CSSProperties = {
  padding: '4px 8px', border: '1px solid #d1d5db', borderRadius: 4,
  fontSize: '0.8rem', fontFamily: 'inherit', boxSizing: 'border-box', outline: 'none',
};
const cur = (n: number | null | undefined) => `$${(n ?? 0).toFixed(2)}`;
const microLabel: React.CSSProperties = {
  fontSize: '0.6rem', color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.04em',
};

// Local editable state for one invoice's lines, keyed by line id.
interface LineEdit { unit_id: string | null; unit_name: string | null; qty: number; cost: number }

export default function InvoiceFixesCard({ data, props }: DisplayBlockProps) {
  const invoices = useMemo(() => (data.fix_invoices as FixInvoice[]) || [], [data.fix_invoices]);
  const venueId =
    (props?.activeVenueId as string) || (data.venue_id as string) || undefined;

  const [units, setUnits] = useState<Unit[]>([]);
  const [pos, setPos] = useState<PO[]>([]);

  useEffect(() => {
    if (!venueId) return;
    apiFetch(`/api/invoice-fixes/units?venue_id=${venueId}`)
      .then((r) => (r.ok ? r.json() : { units: [] }))
      .then((d) => setUnits(d.units || []))
      .catch(() => {});
    apiFetch(`/api/invoice-fixes/purchase-orders?venue_id=${venueId}`)
      .then((r) => (r.ok ? r.json() : { purchase_orders: [] }))
      .then((d) => setPos(d.purchase_orders || []))
      .catch(() => {});
  }, [venueId]);

  if (invoices.length === 0) return null;

  return (
    <div style={{ marginTop: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {invoices.map((inv) => (
        <ReceiveInvoiceView
          key={inv.invoice_id}
          inv={inv}
          units={units}
          pos={pos}
          venueId={venueId}
        />
      ))}
    </div>
  );
}

function ReceiveInvoiceView({
  inv, units, pos, venueId,
}: { inv: FixInvoice; units: Unit[]; pos: PO[]; venueId?: string }) {
  // Suggested unit per line id (from unit suggestions).
  const suggestedUnit = useMemo(() => {
    const m: Record<string, string> = {};
    for (const s of inv.suggestions) if (s.type === 'unit' && s.line_id && s.proposed_unit) m[s.line_id] = s.proposed_unit;
    return m;
  }, [inv.suggestions]);
  const poSuggestion = inv.suggestions.find((s) => s.type === 'link_po')?.po_number;

  // Resolve a unit name to a Loaded unit (exact, case-insensitive).
  const unitByName = useMemo(() => {
    const m: Record<string, Unit> = {};
    for (const u of units) m[u.name.toLowerCase()] = u;
    return m;
  }, [units]);

  // Initial editable line state — pre-select the recommended unit when it
  // resolves to a real Loaded unit; otherwise keep the current unit.
  const [lineEdits, setLineEdits] = useState<Record<string, LineEdit>>({});
  useEffect(() => {
    const init: Record<string, LineEdit> = {};
    for (const ln of inv.lines) {
      const rec = suggestedUnit[ln.id];
      const recUnit = rec ? unitByName[rec.toLowerCase()] : undefined;
      init[ln.id] = {
        unit_id: recUnit?.id ?? ln.linked_unit_id,
        unit_name: recUnit?.name ?? ln.unit,
        qty: ln.quantity_received ?? 0,
        cost: ln.unit_cost ?? 0,
      };
    }
    setLineEdits(init);
  }, [inv.lines, suggestedUnit, unitByName]);

  const [poNumber, setPoNumber] = useState<string>(inv.purchase_order_number || poSuggestion || '');
  const [status, setStatus] = useState<'idle' | 'saving' | 'done' | 'error'>('idle');
  const [message, setMessage] = useState<string>('');

  const setLine = (id: string, patch: Partial<LineEdit>) =>
    setLineEdits((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));

  const total = inv.lines.reduce((s, ln) => {
    const e = lineEdits[ln.id];
    return s + (e ? e.qty * e.cost : ln.total_cost ?? 0);
  }, 0);

  const accept = async () => {
    setStatus('saving');
    setMessage('');
    try {
      const lines = inv.lines.map((ln) => {
        const e = lineEdits[ln.id];
        const u = units.find((x) => x.id === e?.unit_id);
        return {
          id: ln.id,
          unit: u?.name ?? e?.unit_name,
          linked_unit_id: e?.unit_id,
          unit_ratio: u?.ratio,
          quantity_received: e?.qty,
          unit_cost: e?.cost,
          total_cost: e ? Number((e.qty * e.cost).toFixed(4)) : ln.total_cost,
        };
      });
      // Variant updates: lines whose unit changed from the original and that
      // carry a stock code (matched to a variant).
      const variant_updates = inv.lines
        .filter((ln) => {
          const e = lineEdits[ln.id];
          return ln.code && e?.unit_id && e.unit_id !== ln.linked_unit_id;
        })
        .map((ln) => ({
          linked_item_id: ln.linked_item_id,
          line_code: ln.code,
          unit_id: lineEdits[ln.id].unit_id,
        }));
      const matchedPo = pos.find((p) => (p.order_number || '').replace(/\W/g, '').toLowerCase() === poNumber.replace(/\W/g, '').toLowerCase().replace(/^po/, ''));
      const res = await apiFetch('/api/invoice-fixes/receive', {
        method: 'POST',
        body: JSON.stringify({
          venue_id: venueId,
          invoice_id: inv.invoice_id,
          linked_purchase_order_id: matchedPo?.id || inv.linked_purchase_order_id || null,
          po_number: matchedPo ? null : (poNumber || null),
          lines,
          variant_updates,
          receive: true,
        }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(b.detail || `Error ${res.status}`);
      }
      setStatus('done');
      setMessage('Received');
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Failed');
    }
  };

  const done = status === 'done';

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,0.04)', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ padding: '0.7rem 0.9rem', background: 'linear-gradient(#faf9f7,#f5f3ef)', borderBottom: '1px solid #eee' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span style={{ fontSize: '0.9rem', fontWeight: 600, color: '#3a3a3a' }}>
            Receive Invoice · {inv.reference_number}
          </span>
          <span style={{ fontSize: '0.8rem', color: '#666' }}>{inv.supplier_name}</span>
        </div>
        <div style={{ display: 'flex', gap: '1.5rem', marginTop: '0.55rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <span style={microLabel}>Order Number (PO){poSuggestion && !inv.linked_purchase_order_id ? ' · suggested' : ''}</span>
            <input
              list={`po-${inv.invoice_id}`}
              value={poNumber}
              disabled={done}
              onChange={(e) => setPoNumber(e.target.value)}
              placeholder="Not linked"
              style={{ ...inputStyle, width: 200,
                borderColor: poSuggestion && poNumber === poSuggestion && !inv.linked_purchase_order_id ? '#b78a2f' : '#d1d5db',
                background: poSuggestion && poNumber === poSuggestion && !inv.linked_purchase_order_id ? '#fdf6e7' : '#fff' }}
            />
            <datalist id={`po-${inv.invoice_id}`}>
              {pos.map((p) => <option key={p.id} value={p.order_number} />)}
            </datalist>
          </label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <span style={microLabel}>Invoice total</span>
            <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>{cur(total)}</span>
          </div>
          {inv.issued_at && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={microLabel}>Invoice date</span>
              <span style={{ fontSize: '0.8rem', color: '#555' }}>{inv.issued_at}</span>
            </div>
          )}
        </div>
      </div>

      {/* Lines */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: '#9ca3af', fontSize: '0.62rem', textTransform: 'uppercase' }}>
              <th style={{ padding: '0.4rem 0.6rem' }}>Code</th>
              <th style={{ padding: '0.4rem 0.6rem' }}>Description</th>
              <th style={{ padding: '0.4rem 0.6rem' }}>Unit</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Qty</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Unit cost</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Line total</th>
            </tr>
          </thead>
          <tbody>
            {inv.lines.map((ln) => {
              const e = lineEdits[ln.id];
              const changedUnit = !!suggestedUnit[ln.id];
              return (
                <tr key={ln.id} style={{ borderTop: '1px solid #f3f3f3' }}>
                  <td style={{ padding: '0.4rem 0.6rem', color: '#666' }}>{ln.code || '—'}</td>
                  <td style={{ padding: '0.4rem 0.6rem' }}>{ln.description}</td>
                  <td style={{ padding: '0.4rem 0.6rem' }}>
                    <select
                      value={e?.unit_id ?? ''}
                      disabled={done}
                      onChange={(ev) => {
                        const u = units.find((x) => x.id === ev.target.value);
                        setLine(ln.id, { unit_id: u?.id ?? null, unit_name: u?.name ?? null });
                      }}
                      style={{ ...inputStyle, minWidth: 110,
                        borderColor: changedUnit ? '#b78a2f' : '#d1d5db',
                        background: changedUnit ? '#fdf6e7' : '#fff' }}
                    >
                      {!e?.unit_id && <option value="">{ln.unit || 'Select'}</option>}
                      {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                    </select>
                    {changedUnit && (
                      <div style={{ fontSize: '0.6rem', color: '#b78a2f', marginTop: 2 }}>
                        was {ln.unit} · copy {ln.copy_unit}
                      </div>
                    )}
                  </td>
                  <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>
                    <input type="number" step="any" value={e?.qty ?? 0} disabled={done}
                      onChange={(ev) => setLine(ln.id, { qty: parseFloat(ev.target.value) || 0 })}
                      style={{ ...inputStyle, width: 70, textAlign: 'right' }} />
                  </td>
                  <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>
                    <input type="number" step="any" value={e?.cost ?? 0} disabled={done}
                      onChange={(ev) => setLine(ln.id, { cost: parseFloat(ev.target.value) || 0 })}
                      style={{ ...inputStyle, width: 80, textAlign: 'right' }} />
                  </td>
                  <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {cur(e ? e.qty * e.cost : ln.total_cost)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div style={{ padding: '0.6rem 0.9rem', borderTop: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.75rem' }}>
        <span style={{ fontSize: '0.72rem', color: done ? '#2e7d4f' : status === 'error' ? '#c0392b' : '#888' }}>
          {status === 'done' && '✓ Received — re-run the review to refresh the list.'}
          {status === 'error' && `✗ ${message}`}
          {status === 'idle' && 'Review the changes, then accept to update Loaded and receive.'}
          {status === 'saving' && 'Applying…'}
        </span>
        {!done && (
          <button onClick={accept} disabled={status === 'saving'}
            style={{ padding: '0.4rem 1.1rem', fontSize: '0.78rem', fontWeight: 500, border: 'none', borderRadius: 6,
              cursor: status === 'saving' ? 'not-allowed' : 'pointer', background: '#2e7d4f', color: '#fff',
              fontFamily: 'inherit', opacity: status === 'saving' ? 0.6 : 1, whiteSpace: 'nowrap' }}>
            {status === 'saving' ? 'Receiving…' : 'Accept & Receive'}
          </button>
        )}
      </div>
    </div>
  );
}
