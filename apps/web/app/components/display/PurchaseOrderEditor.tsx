'use client';

import { useState, useEffect, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';

// --- Types ---

interface LineItem {
  id?: string;
  product: string;
  supplier: string;
  quantity: number;
  unit: string;
  unit_price: number;
}

// --- Helpers ---

function extractOrder(data: Record<string, unknown>): {
  supplier: string; venue: string; reference: string; status: string; lines: LineItem[]; notes: string;
} {
  const supplier = String(data.supplier || data.supplierName || data.vendor || '');
  const venue = String(data.venue || data.venue_name || data.deliveryLocation || data.location || '');
  const reference = String(data.reference || data.orderReference || data.order_id || data.id || '');
  const status = String(data.status || 'draft');

  // Find lines array
  let rawLines: Record<string, unknown>[] = [];
  const candidates = ['lines', 'items', 'lineItems', 'line_items', 'products', 'orderLines'];
  for (const key of candidates) {
    const val = data[key];
    if (Array.isArray(val) && val.length > 0) {
      rawLines = val;
      break;
    }
  }
  if (rawLines.length === 0) {
    for (const val of Object.values(data)) {
      if (Array.isArray(val) && val.length > 0 && typeof val[0] === 'object') {
        rawLines = val;
        break;
      }
    }
  }

  let lines: LineItem[];
  if (rawLines.length > 0) {
    lines = rawLines.map((item, i) => ({
      id: String(item.id || item.productCode || i),
      product: String(item.product || item.description || item.productName || item.product_name || item.name || ''),
      supplier: String(item.supplier || item.supplierName || supplier || ''),
      quantity: Number(item.quantity || item.qty || 0),
      unit: String(item.unit || 'case'),
      unit_price: Number(item.unit_price || item.unitPrice || item.price || 0),
    }));
  } else if (data.product_name || data.productName || data.product) {
    lines = [{
      id: '0',
      product: String(data.product_name || data.productName || data.product || ''),
      supplier: supplier,
      quantity: Number(data.quantity || data.qty || 1),
      unit: String(data.unit || 'case'),
      unit_price: Number(data.unit_price || data.unitPrice || data.price || 0),
    }];
  } else {
    lines = [];
  }

  const notes = String(data.notes || '');

  return { supplier, venue, reference, status, lines, notes };
}

function formatCurrency(n: number): string {
  return `$${n.toFixed(2)}`;
}

// --- Component ---

export default function PurchaseOrderEditor({ data, props, onAction, taskId }: DisplayBlockProps) {
  // Working document mode
  const workingDocId = (data as Record<string, unknown>)?.working_document_id as string | undefined;

  const [orderData, setOrderData] = useState<Record<string, unknown> | null>(workingDocId ? null : data);
  const [docVersion, setDocVersion] = useState(1);
  const [syncStatus, setSyncStatus] = useState('synced');

  const initial = extractOrder(orderData || data);
  const [lines, setLines] = useState<LineItem[]>(initial.lines);
  const [notes, setNotes] = useState(initial.notes);
  const [adding, setAdding] = useState(false);
  const [newProduct, setNewProduct] = useState('');
  const [newSupplier, setNewSupplier] = useState('');
  const [newQty, setNewQty] = useState(1);
  const [newUnit, setNewUnit] = useState('case');
  const [newPrice, setNewPrice] = useState(0);
  const [saving, setSaving] = useState(false);
  const connectorName = (props?.connector_name as string) || '';

  // Fetch working document
  useEffect(() => {
    if (!workingDocId || !taskId) return;
    apiFetch(`/api/tasks/${taskId}/working-documents/${workingDocId}`)
      .then(res => res.ok ? res.json() : null)
      .then(doc => {
        if (doc) {
          setOrderData(doc.data);
          setDocVersion(doc.version);
          setSyncStatus(doc.sync_status);
          const parsed = extractOrder(doc.data);
          setLines(parsed.lines);
          setNotes(parsed.notes);
        }
      })
      .catch(() => {});
  }, [workingDocId, taskId]);

  // Update from props when not in working doc mode
  useEffect(() => {
    if (workingDocId) return;
    const parsed = extractOrder(data);
    setLines(parsed.lines);
    setNotes(parsed.notes);
  }, [data, workingDocId]);

  const title = (props?.title as string) || 'Purchase Order';
  const grandTotal = lines.reduce((sum, l) => sum + l.quantity * l.unit_price, 0);
  const interactive = !!onAction || !!workingDocId;

  // PATCH working document helper
  const patchDoc = useCallback(async (ops: Record<string, unknown>[]) => {
    if (!workingDocId || !taskId) return;
    try {
      const res = await apiFetch(`/api/tasks/${taskId}/working-documents/${workingDocId}`, {
        method: 'PATCH',
        body: JSON.stringify({ ops, version: docVersion }),
      });
      if (res.ok) {
        const updated = await res.json();
        setOrderData(updated.data);
        setDocVersion(updated.version);
        setSyncStatus(updated.sync_status);
        const parsed = extractOrder(updated.data);
        setLines(parsed.lines);
      }
    } catch { /* ignore */ }
  }, [workingDocId, taskId, docVersion]);

  const handleQtyChange = useCallback((idx: number, qty: number) => {
    setLines(prev => prev.map((l, i) => i === idx ? { ...l, quantity: Math.max(0, qty) } : l));
    if (workingDocId) {
      patchDoc([{ op: 'update_line', index: idx, fields: { quantity: qty } }]);
    } else if (onAction && connectorName) {
      onAction({ connector_name: connectorName, action: 'update_line', params: { index: idx, quantity: qty } });
    }
  }, [workingDocId, patchDoc, onAction, connectorName]);

  const handleRemove = useCallback((idx: number) => {
    setLines(prev => prev.filter((_, i) => i !== idx));
    if (workingDocId) {
      patchDoc([{ op: 'remove_line', index: idx }]);
    } else if (onAction && connectorName) {
      onAction({ connector_name: connectorName, action: 'remove_line', params: { index: idx } });
    }
  }, [workingDocId, patchDoc, onAction, connectorName]);

  const handleAdd = useCallback(() => {
    if (!newProduct.trim()) return;
    const line: LineItem = { id: String(Date.now()), product: newProduct, supplier: newSupplier, quantity: newQty, unit: newUnit, unit_price: newPrice };
    setLines(prev => [...prev, line]);
    setAdding(false);
    setNewProduct('');
    setNewSupplier('');
    setNewQty(1);
    setNewUnit('case');
    setNewPrice(0);
    if (workingDocId) {
      patchDoc([{ op: 'add_line', fields: { product: line.product, supplier: line.supplier, quantity: line.quantity, unit: line.unit, unit_price: line.unit_price } }]);
    } else if (onAction && connectorName) {
      onAction({ connector_name: connectorName, action: 'add_line', params: line });
    }
  }, [newProduct, newSupplier, newQty, newUnit, newPrice, workingDocId, patchDoc, onAction, connectorName]);

  const handleSubmit = useCallback(async () => {
    setSaving(true);
    try {
      if (workingDocId && taskId) {
        const res = await apiFetch(`/api/tasks/${taskId}/working-documents/${workingDocId}/submit`, { method: 'POST' });
        if (res.ok) {
          const result = await res.json();
          setSyncStatus(result.document?.sync_status || 'synced');
        }
      } else if (onAction) {
        await onAction({
          connector_name: connectorName,
          action: 'submit_order',
          params: { lines: lines.map(l => ({ product: l.product, quantity: l.quantity, unit: l.unit, unit_price: l.unit_price })) },
        });
      }
    } finally { setSaving(false); }
  }, [workingDocId, taskId, onAction, connectorName, lines]);

  const handleNotesChange = useCallback((value: string) => {
    setNotes(value);
    if (workingDocId) {
      patchDoc([{ op: 'update_notes', value }]);
    }
  }, [workingDocId, patchDoc]);

  const inputStyle: React.CSSProperties = {
    padding: '3px 6px', border: '1px solid #ddd', borderRadius: 4,
    fontSize: '0.82rem', fontFamily: 'inherit', boxSizing: 'border-box',
  };

  return (
    <div style={{
      border: '1px solid #e2e8f0', borderRadius: 8, padding: '0.75rem',
      backgroundColor: '#fff', marginBottom: '0.75rem',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.85rem', fontWeight: 600, color: '#333' }}>{title}</span>
        {initial.reference && (
          <span style={{
            fontSize: '0.72rem', fontWeight: 600, color: '#004085',
            backgroundColor: '#cce5ff', padding: '1px 6px', borderRadius: 3,
          }}>{initial.reference}</span>
        )}
        <span style={{
          fontSize: '0.68rem', fontWeight: 500, padding: '1px 6px', borderRadius: 3,
          color: initial.status === 'submitted' ? '#155724' : '#856404',
          backgroundColor: initial.status === 'submitted' ? '#d4edda' : '#fff3cd',
        }}>{initial.status}</span>
        {interactive && (
          <button onClick={handleSubmit} disabled={saving || lines.length === 0} style={{
            marginLeft: 'auto', padding: '4px 12px', fontSize: '0.75rem', fontWeight: 600,
            border: 'none', borderRadius: 4, backgroundColor: '#28a745', color: '#fff',
            cursor: saving ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
          }}>{saving ? '...' : 'Submit Order'}</button>
        )}
        {workingDocId && (
          <span title={syncStatus} style={{
            width: 8, height: 8, borderRadius: '50%', display: 'inline-block',
            backgroundColor: syncStatus === 'synced' ? '#28a745' : syncStatus === 'pending_submit' ? '#ffc107' : syncStatus === 'error' ? '#dc3545' : '#888',
          }} />
        )}
      </div>

      {/* Meta */}
      {(initial.supplier || initial.venue) && (
        <div style={{ fontSize: '0.78rem', color: '#666', marginBottom: '0.5rem' }}>
          {initial.supplier && <span>Supplier: <strong>{initial.supplier}</strong></span>}
          {initial.supplier && initial.venue && <span style={{ margin: '0 0.4rem' }}>&middot;</span>}
          {initial.venue && <span>Venue: <strong>{initial.venue}</strong></span>}
        </div>
      )}

      {/* Line items */}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem', lineHeight: 1.5 }}>
        <thead>
          <tr>
            {['Product', 'Supplier', 'Unit', 'Qty', ...(lines.some(l => l.unit_price > 0) ? ['Price', 'Total'] : []), ...(interactive ? [''] : [])].map((h, i) => (
              <th key={i} style={{
                textAlign: h === 'Qty' || h === 'Price' || h === 'Total' ? 'right' : 'left',
                padding: '0.4rem 0.5rem', borderBottom: '2px solid #e2e8f0',
                fontWeight: 600, color: '#555',
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lines.map((l, i) => {
            const hasPrice = lines.some(l => l.unit_price > 0);
            return (
              <tr key={l.id || i} style={{ backgroundColor: i % 2 === 1 ? '#fafafa' : '#fff' }}>
                <td style={{ padding: '0.35rem 0.5rem', borderBottom: '1px solid #eee', color: '#333' }}>{l.product}</td>
                <td style={{ padding: '0.35rem 0.5rem', borderBottom: '1px solid #eee', color: '#888' }}>{l.supplier}</td>
                <td style={{ padding: '0.35rem 0.5rem', borderBottom: '1px solid #eee', color: '#888' }}>{l.unit}</td>
                <td style={{ padding: '0.35rem 0.5rem', borderBottom: '1px solid #eee', textAlign: 'right' }}>
                  {interactive ? (
                    <input
                      type="number"
                      min={0}
                      value={l.quantity}
                      onChange={e => handleQtyChange(i, parseInt(e.target.value, 10) || 0)}
                      style={{ ...inputStyle, width: 50, textAlign: 'right' }}
                    />
                  ) : (
                    <span style={{ fontWeight: 500 }}>{l.quantity}</span>
                  )}
                </td>
                {hasPrice && (
                  <>
                    <td style={{ padding: '0.35rem 0.5rem', borderBottom: '1px solid #eee', textAlign: 'right', color: '#888' }}>
                      {formatCurrency(l.unit_price)}
                    </td>
                    <td style={{ padding: '0.35rem 0.5rem', borderBottom: '1px solid #eee', textAlign: 'right', fontWeight: 500 }}>
                      {formatCurrency(l.quantity * l.unit_price)}
                    </td>
                  </>
                )}
                {interactive && (
                  <td style={{ padding: '0.35rem 0.5rem', borderBottom: '1px solid #eee', textAlign: 'center' }}>
                    <button onClick={() => handleRemove(i)} style={{
                      border: 'none', background: 'none', cursor: 'pointer',
                      color: '#e53e3e', fontSize: '0.82rem', padding: '0 4px',
                    }}>&#10005;</button>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Totals row */}
      {lines.some(l => l.unit_price > 0) && (
        <div style={{
          display: 'flex', justifyContent: 'flex-end', padding: '0.5rem 0.5rem 0',
          fontSize: '0.85rem', fontWeight: 600, color: '#333',
        }}>
          Total: {formatCurrency(grandTotal)}
        </div>
      )}

      {/* Add line */}
      {interactive && !adding && (
        <button onClick={() => setAdding(true)} style={{
          marginTop: '0.4rem', padding: '4px 10px', fontSize: '0.75rem', fontWeight: 500,
          border: '1px solid #28a745', borderRadius: 4, backgroundColor: '#fff',
          color: '#28a745', cursor: 'pointer', fontFamily: 'inherit',
        }}>+ Add Item</button>
      )}

      {adding && (
        <div style={{
          marginTop: '0.4rem', padding: '0.5rem',
          border: '1px solid #d4e5f7', borderRadius: 6, backgroundColor: '#f8fbff',
          display: 'flex', gap: '0.4rem', alignItems: 'flex-end', flexWrap: 'wrap',
        }}>
          <div style={{ flex: 2, minWidth: 120 }}>
            <label style={{ fontSize: '0.68rem', color: '#666', fontWeight: 500 }}>Product</label>
            <input value={newProduct} onChange={e => setNewProduct(e.target.value)}
              placeholder="Product name" style={{ ...inputStyle, width: '100%' }} />
          </div>
          <div style={{ flex: 1, minWidth: 80 }}>
            <label style={{ fontSize: '0.68rem', color: '#666', fontWeight: 500 }}>Supplier</label>
            <input value={newSupplier} onChange={e => setNewSupplier(e.target.value)}
              placeholder="Supplier" style={{ ...inputStyle, width: '100%' }} />
          </div>
          <div style={{ flex: 0, minWidth: 60 }}>
            <label style={{ fontSize: '0.68rem', color: '#666', fontWeight: 500 }}>Unit</label>
            <input value={newUnit} onChange={e => setNewUnit(e.target.value)}
              style={{ ...inputStyle, width: 60 }} />
          </div>
          <div style={{ flex: 0, minWidth: 50 }}>
            <label style={{ fontSize: '0.68rem', color: '#666', fontWeight: 500 }}>Qty</label>
            <input type="number" min={1} value={newQty} onChange={e => setNewQty(parseInt(e.target.value, 10) || 1)}
              style={{ ...inputStyle, width: 50, textAlign: 'right' }} />
          </div>
          <div style={{ flex: 0, minWidth: 60 }}>
            <label style={{ fontSize: '0.68rem', color: '#666', fontWeight: 500 }}>Price</label>
            <input type="number" min={0} step={0.01} value={newPrice} onChange={e => setNewPrice(parseFloat(e.target.value) || 0)}
              style={{ ...inputStyle, width: 70, textAlign: 'right' }} />
          </div>
          <button onClick={handleAdd} style={{
            padding: '4px 10px', fontSize: '0.75rem', fontWeight: 600,
            backgroundColor: '#28a745', color: '#fff', border: 'none', borderRadius: 4,
            cursor: 'pointer', fontFamily: 'inherit',
          }}>Add</button>
          <button onClick={() => setAdding(false)} style={{
            padding: '4px 10px', fontSize: '0.75rem',
            backgroundColor: '#fff', color: '#666', border: '1px solid #ddd', borderRadius: 4,
            cursor: 'pointer', fontFamily: 'inherit',
          }}>Cancel</button>
        </div>
      )}

      {/* Notes / message to supplier */}
      <div style={{ marginTop: '0.5rem' }}>
        <label style={{ fontSize: '0.72rem', color: '#666', fontWeight: 500, display: 'block', marginBottom: 3 }}>Notes to supplier</label>
        {interactive ? (
          <textarea
            value={notes}
            onChange={e => handleNotesChange(e.target.value)}
            placeholder="Add any special instructions or notes for the supplier..."
            rows={2}
            style={{
              ...inputStyle, width: '100%', resize: 'vertical',
              fontSize: '0.82rem', lineHeight: 1.4,
            }}
          />
        ) : notes ? (
          <div style={{ fontSize: '0.82rem', color: '#555', fontStyle: 'italic' }}>{notes}</div>
        ) : null}
      </div>
    </div>
  );
}
