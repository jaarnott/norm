'use client';

import { useState, useEffect, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';

// --- Types ---

interface LineItem {
  id?: string;
  stock_code: string;
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
      stock_code: String(item.stock_code || item.stockCode || item.sku || item.productCode || item.product_code || item.code || ''),
      product: String(item.product || item.description || item.productName || item.product_name || item.name || ''),
      supplier: String(item.supplier || item.supplierName || supplier || ''),
      quantity: Number(item.quantity || item.qty || 0),
      unit: String(item.unit || 'case'),
      unit_price: Number(item.unit_price || item.unitPrice || item.price || 0),
    }));
  } else if (data.product_name || data.productName || data.product) {
    lines = [{
      id: '0',
      stock_code: String(data.stock_code || data.stockCode || data.sku || data.productCode || data.product_code || ''),
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

const STATUS_CONFIG: Record<string, { label: string; bg: string; color: string; border: string }> = {
  draft: { label: 'Draft', bg: '#fffbeb', color: '#92400e', border: '#fde68a' },
  submitted: { label: 'Submitted', bg: '#ecfdf5', color: '#065f46', border: '#a7f3d0' },
  pending_submit: { label: 'Pending', bg: '#fff7ed', color: '#9a3412', border: '#fed7aa' },
  approved: { label: 'Approved', bg: '#ecfdf5', color: '#065f46', border: '#a7f3d0' },
  rejected: { label: 'Rejected', bg: '#fef2f2', color: '#991b1b', border: '#fecaca' },
};

// --- Component ---

export default function PurchaseOrderEditor({ data, props, onAction, threadId }: DisplayBlockProps) {
  const workingDocId = (data as Record<string, unknown>)?.working_document_id as string | undefined;

  const [orderData, setOrderData] = useState<Record<string, unknown> | null>(workingDocId ? null : data);
  const [docVersion, setDocVersion] = useState(1);
  const [syncStatus, setSyncStatus] = useState('synced');

  const initial = extractOrder(orderData || data);
  const [lines, setLines] = useState<LineItem[]>(initial.lines);
  const [notes, setNotes] = useState(initial.notes);
  const [adding, setAdding] = useState(false);
  const [newStockCode, setNewStockCode] = useState('');
  const [newProduct, setNewProduct] = useState('');
  const [newSupplier, setNewSupplier] = useState('');
  const [newQty, setNewQty] = useState(1);
  const [newUnit, setNewUnit] = useState('case');
  const [newPrice, setNewPrice] = useState(0);
  const [saving, setSaving] = useState(false);
  const connectorName = (props?.connector_name as string) || '';

  useEffect(() => {
    if (!workingDocId || !threadId) return;
    apiFetch(`/api/threads/${threadId}/working-documents/${workingDocId}`)
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
  }, [workingDocId, threadId]);

  useEffect(() => {
    if (workingDocId) return;
    const parsed = extractOrder(data);
    setLines(parsed.lines);
    setNotes(parsed.notes);
  }, [data, workingDocId]);

  const title = (props?.title as string) || 'Purchase Order';
  const grandTotal = lines.reduce((sum, l) => sum + l.quantity * l.unit_price, 0);
  const hasPrice = lines.some(l => l.unit_price > 0);
  const interactive = !!onAction || !!workingDocId;
  const isSubmitted = initial.status === 'submitted' || initial.status === 'approved';
  const statusCfg = STATUS_CONFIG[initial.status] || STATUS_CONFIG.draft;

  // PATCH working document helper
  const patchDoc = useCallback(async (ops: Record<string, unknown>[]) => {
    if (!workingDocId || !threadId) return;
    try {
      const res = await apiFetch(`/api/threads/${threadId}/working-documents/${workingDocId}`, {
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
    } catch (e) { console.error(e); }
  }, [workingDocId, threadId, docVersion]);

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
    const line: LineItem = { id: String(Date.now()), stock_code: newStockCode, product: newProduct, supplier: newSupplier, quantity: newQty, unit: newUnit, unit_price: newPrice };
    setLines(prev => [...prev, line]);
    setAdding(false);
    setNewStockCode('');
    setNewProduct('');
    setNewSupplier('');
    setNewQty(1);
    setNewUnit('case');
    setNewPrice(0);
    if (workingDocId) {
      patchDoc([{ op: 'add_line', fields: { stock_code: line.stock_code, product: line.product, supplier: line.supplier, quantity: line.quantity, unit: line.unit, unit_price: line.unit_price } }]);
    } else if (onAction && connectorName) {
      onAction({ connector_name: connectorName, action: 'add_line', params: line as unknown as Record<string, unknown> });
    }
  }, [newStockCode, newProduct, newSupplier, newQty, newUnit, newPrice, workingDocId, patchDoc, onAction, connectorName]);

  const handleSubmit = useCallback(async () => {
    setSaving(true);
    try {
      if (workingDocId && threadId) {
        const res = await apiFetch(`/api/threads/${threadId}/working-documents/${workingDocId}/submit`, { method: 'POST' });
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
  }, [workingDocId, threadId, onAction, connectorName, lines]);

  const handleNotesChange = useCallback((value: string) => {
    setNotes(value);
    if (workingDocId) {
      patchDoc([{ op: 'update_notes', value }]);
    }
  }, [workingDocId, patchDoc]);

  const inputStyle: React.CSSProperties = {
    padding: '4px 8px', border: '1px solid #d1d5db', borderRadius: 4,
    fontSize: '0.82rem', fontFamily: 'inherit', boxSizing: 'border-box',
    outline: 'none',
  };

  return (
    <div data-testid="po-editor" style={{
      border: '1px solid #e5e7eb', borderRadius: 10, overflow: 'hidden',
      backgroundColor: '#fff', marginBottom: '0.75rem',
      boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
    }}>
      {/* ── Document header ── */}
      <div style={{
        padding: '1rem 1.25rem',
        borderBottom: '1px solid #e5e7eb',
        background: 'linear-gradient(to bottom, #fafafa, #fff)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.6rem' }}>
          <span style={{ fontSize: '1rem', fontWeight: 700, color: '#111', letterSpacing: '-0.01em' }}>{title}</span>
          <span style={{
            fontSize: '0.68rem', fontWeight: 600,
            padding: '2px 8px', borderRadius: 10,
            color: statusCfg.color, backgroundColor: statusCfg.bg,
            border: `1px solid ${statusCfg.border}`,
          }}>{statusCfg.label}</span>
          {workingDocId && syncStatus !== 'synced' && (
            <span title={`Sync: ${syncStatus}`} style={{
              width: 7, height: 7, borderRadius: '50%', display: 'inline-block',
              backgroundColor: syncStatus === 'pending_submit' ? '#f59e0b' : syncStatus === 'error' ? '#ef4444' : '#6b7280',
            }} />
          )}
        </div>

        <div style={{ display: 'flex', gap: '2rem', fontSize: '0.8rem', color: '#555' }}>
          {initial.reference && (
            <div>
              <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 2 }}>Reference</div>
              <div style={{ fontWeight: 600, color: '#111', fontFamily: 'monospace', fontSize: '0.82rem' }}>{initial.reference}</div>
            </div>
          )}
          {initial.supplier && (
            <div>
              <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 2 }}>Supplier</div>
              <div style={{ fontWeight: 600, color: '#111' }}>{initial.supplier}</div>
            </div>
          )}
          {initial.venue && (
            <div>
              <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 2 }}>Deliver to</div>
              <div style={{ fontWeight: 600, color: '#111' }}>{initial.venue}</div>
            </div>
          )}
          <div>
            <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 2 }}>Items</div>
            <div style={{ fontWeight: 600, color: '#111' }}>{lines.length} line{lines.length !== 1 ? 's' : ''}</div>
          </div>
        </div>
      </div>

      {/* ── Line items ── */}
      <div style={{ padding: '0 1.25rem' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem', lineHeight: 1.6 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #e5e7eb' }}>
              <th style={{ textAlign: 'left', padding: '0.6rem 0.5rem', fontWeight: 600, color: '#6b7280', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Code</th>
              <th style={{ textAlign: 'left', padding: '0.6rem 0.5rem', fontWeight: 600, color: '#6b7280', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Product</th>
              <th style={{ textAlign: 'left', padding: '0.6rem 0.5rem', fontWeight: 600, color: '#6b7280', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Supplier</th>
              <th style={{ textAlign: 'center', padding: '0.6rem 0.5rem', fontWeight: 600, color: '#6b7280', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Unit</th>
              <th style={{ textAlign: 'right', padding: '0.6rem 0.5rem', fontWeight: 600, color: '#6b7280', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em', width: 70 }}>Qty</th>
              {hasPrice && (
                <>
                  <th style={{ textAlign: 'right', padding: '0.6rem 0.5rem', fontWeight: 600, color: '#6b7280', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Price</th>
                  <th style={{ textAlign: 'right', padding: '0.6rem 0.5rem', fontWeight: 600, color: '#6b7280', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Total</th>
                </>
              )}
              {interactive && !isSubmitted && <th style={{ width: 36 }} />}
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={l.id || i} style={{ borderBottom: '1px solid #f3f4f6' }}>
                <td style={{ padding: '0.5rem 0.5rem', color: '#6b7280', fontFamily: 'monospace', fontSize: '0.78rem' }}>{l.stock_code || '—'}</td>
                <td style={{ padding: '0.5rem 0.5rem', color: '#111', fontWeight: 500 }}>{l.product}</td>
                <td style={{ padding: '0.5rem 0.5rem', color: '#6b7280' }}>{l.supplier}</td>
                <td style={{ padding: '0.5rem 0.5rem', color: '#6b7280', textAlign: 'center' }}>{l.unit}</td>
                <td style={{ padding: '0.5rem 0.5rem', textAlign: 'right' }}>
                  {interactive && !isSubmitted ? (
                    <input
                      type="number"
                      min={0}
                      value={l.quantity}
                      onChange={e => handleQtyChange(i, parseInt(e.target.value, 10) || 0)}
                      style={{ ...inputStyle, width: 56, textAlign: 'right' }}
                    />
                  ) : (
                    <span style={{ fontWeight: 600, color: '#111' }}>{l.quantity}</span>
                  )}
                </td>
                {hasPrice && (
                  <>
                    <td style={{ padding: '0.5rem 0.5rem', textAlign: 'right', color: '#6b7280' }}>
                      {formatCurrency(l.unit_price)}
                    </td>
                    <td style={{ padding: '0.5rem 0.5rem', textAlign: 'right', fontWeight: 600, color: '#111' }}>
                      {formatCurrency(l.quantity * l.unit_price)}
                    </td>
                  </>
                )}
                {interactive && !isSubmitted && (
                  <td style={{ padding: '0.5rem 0.25rem', textAlign: 'center' }}>
                    <button onClick={() => handleRemove(i)} title="Remove line" style={{
                      border: 'none', background: 'none', cursor: 'pointer',
                      color: '#d1d5db', fontSize: '0.85rem', padding: '0 4px',
                      transition: 'color 0.15s',
                    }} onMouseEnter={e => (e.currentTarget.style.color = '#ef4444')} onMouseLeave={e => (e.currentTarget.style.color = '#d1d5db')}>&#10005;</button>
                  </td>
                )}
              </tr>
            ))}
            {lines.length === 0 && (
              <tr>
                <td colSpan={hasPrice ? 8 : 6} style={{ padding: '1.5rem', textAlign: 'center', color: '#9ca3af', fontSize: '0.82rem' }}>
                  No items yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ── Add line ── */}
      {interactive && !isSubmitted && (
        <div style={{ padding: '0 1.25rem' }}>
          {!adding ? (
            <button onClick={() => setAdding(true)} style={{
              margin: '0.5rem 0', padding: '5px 12px', fontSize: '0.75rem', fontWeight: 500,
              border: '1px dashed #d1d5db', borderRadius: 6, backgroundColor: 'transparent',
              color: '#6b7280', cursor: 'pointer', fontFamily: 'inherit',
              transition: 'border-color 0.15s, color 0.15s',
            }}>+ Add item</button>
          ) : (
            <div style={{
              margin: '0.5rem 0', padding: '0.6rem 0.75rem',
              border: '1px solid #dbeafe', borderRadius: 8, backgroundColor: '#f8fafc',
              display: 'flex', gap: '0.5rem', alignItems: 'flex-end', flexWrap: 'wrap',
            }}>
              <div style={{ flex: 0, minWidth: 80 }}>
                <label style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Code</label>
                <input value={newStockCode} onChange={e => setNewStockCode(e.target.value)}
                  placeholder="SKU" style={{ ...inputStyle, width: 80, fontFamily: 'monospace' }} autoFocus />
              </div>
              <div style={{ flex: 2, minWidth: 120 }}>
                <label style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Product</label>
                <input value={newProduct} onChange={e => setNewProduct(e.target.value)}
                  placeholder="Product name" style={{ ...inputStyle, width: '100%' }} />
              </div>
              <div style={{ flex: 1, minWidth: 80 }}>
                <label style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Supplier</label>
                <input value={newSupplier} onChange={e => setNewSupplier(e.target.value)}
                  placeholder="Supplier" style={{ ...inputStyle, width: '100%' }} />
              </div>
              <div style={{ flex: 0, minWidth: 60 }}>
                <label style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Unit</label>
                <input value={newUnit} onChange={e => setNewUnit(e.target.value)}
                  style={{ ...inputStyle, width: 60 }} />
              </div>
              <div style={{ flex: 0, minWidth: 50 }}>
                <label style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Qty</label>
                <input type="number" min={1} value={newQty} onChange={e => setNewQty(parseInt(e.target.value, 10) || 1)}
                  style={{ ...inputStyle, width: 50, textAlign: 'right' }} />
              </div>
              <div style={{ flex: 0, minWidth: 60 }}>
                <label style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Price</label>
                <input type="number" min={0} step={0.01} value={newPrice} onChange={e => setNewPrice(parseFloat(e.target.value) || 0)}
                  style={{ ...inputStyle, width: 70, textAlign: 'right' }} />
              </div>
              <button onClick={handleAdd} style={{
                padding: '5px 14px', fontSize: '0.75rem', fontWeight: 600,
                backgroundColor: '#111', color: '#fff', border: 'none', borderRadius: 6,
                cursor: 'pointer', fontFamily: 'inherit',
              }}>Add</button>
              <button onClick={() => setAdding(false)} style={{
                padding: '5px 12px', fontSize: '0.75rem',
                backgroundColor: 'transparent', color: '#6b7280', border: '1px solid #d1d5db', borderRadius: 6,
                cursor: 'pointer', fontFamily: 'inherit',
              }}>Cancel</button>
            </div>
          )}
        </div>
      )}

      {/* ── Footer: totals + notes + actions ── */}
      <div style={{
        padding: '0.75rem 1.25rem 1rem',
        borderTop: '1px solid #f3f4f6',
        marginTop: '0.25rem',
      }}>
        {/* Totals */}
        {hasPrice && (
          <div style={{
            display: 'flex', justifyContent: 'flex-end', marginBottom: '0.75rem',
            paddingBottom: '0.75rem', borderBottom: '1px solid #f3f4f6',
          }}>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: '0.72rem', color: '#9ca3af', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.03em', marginBottom: 2 }}>Order Total</div>
              <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#111' }}>{formatCurrency(grandTotal)}</div>
            </div>
          </div>
        )}

        {/* Notes */}
        <div style={{ marginBottom: interactive && !isSubmitted ? '0.75rem' : 0 }}>
          <label style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.04em', display: 'block', marginBottom: 4 }}>Notes to supplier</label>
          {interactive && !isSubmitted ? (
            <textarea
              value={notes}
              onChange={e => handleNotesChange(e.target.value)}
              placeholder="Add any special instructions or notes..."
              rows={2}
              style={{
                ...inputStyle, width: '100%', resize: 'vertical',
                fontSize: '0.82rem', lineHeight: 1.5,
              }}
            />
          ) : notes ? (
            <div style={{ fontSize: '0.82rem', color: '#6b7280', fontStyle: 'italic' }}>{notes}</div>
          ) : (
            <div style={{ fontSize: '0.82rem', color: '#d1d5db' }}>No notes</div>
          )}
        </div>

        {/* Submit */}
        {interactive && !isSubmitted && (
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button onClick={handleSubmit} disabled={saving || lines.length === 0} style={{
              padding: '8px 24px', fontSize: '0.82rem', fontWeight: 600,
              border: 'none', borderRadius: 8,
              backgroundColor: lines.length === 0 ? '#e5e7eb' : '#111',
              color: lines.length === 0 ? '#9ca3af' : '#fff',
              cursor: saving || lines.length === 0 ? 'not-allowed' : 'pointer',
              fontFamily: 'inherit',
              transition: 'background-color 0.15s',
            }}>{saving ? 'Placing order...' : 'Place Order'}</button>
          </div>
        )}
      </div>
    </div>
  );
}
