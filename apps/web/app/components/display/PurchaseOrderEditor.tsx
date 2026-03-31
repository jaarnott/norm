'use client';

import { useState, useEffect, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch, callComponentApi } from '../../lib/api';

// --- Types ---

interface SupplierVariant {
  id: string;
  supplierId: string;
  supplierName: string;
  unitId: string;
  unitName: string;
  unitRatio: number;
  unitCost: number;
  stockCode: string;
  brandId: string | null;
  defaultForSupplier: boolean;
}

interface StockItem {
  id: string;
  name: string;
  groupName: string;
  defaultSupplierId: string;
  globalSalesTaxSortOrder: number;
  orderingUnitId: string;
  orderingUnitName: string;
  orderingUnitRatio: number;
  suppliers: SupplierVariant[];
}

interface LineItem {
  id?: string;
  stock_code: string;
  product: string;
  supplier: string;
  quantity: number;
  unit: string;
  unit_price: number;
  // LoadedHub enrichment fields
  itemId?: string;
  unitId?: string;
  unitRatio?: number;
  unitCost?: number;
  taxPercent?: number;
  supplierId?: string;
  supplierName?: string;
  brandId?: string | null;
  variantId?: string;
  variants?: SupplierVariant[];
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
      stock_code: String(item.stock_code || item.stockCode || item.sku || item.productCode || item.product_code || item.code || item.itemCode || ''),
      product: String(item.product || item.description || item.productName || item.product_name || item.name || ''),
      supplier: String(item.supplier || item.supplierName || item.defaultSupplierName || supplier || ''),
      quantity: Number(item.quantity || item.qty || item.quantityOrdered || 0),
      unit: String(item.unit || item.orderingUnitName || 'case'),
      unit_price: Number(item.unit_price || item.unitPrice || item.price || item.currentPrice || 0),
      // LoadedHub enrichment fields
      itemId: (item.itemId || item.id || '') as string,
      unitId: (item.unitId || item.orderingUnitId || '') as string,
      unitRatio: Number(item.unitRatio || item.orderingUnitRatio || 1),
      unitCost: Number(item.unitCost || item.currentPrice || item.unit_price || item.unitPrice || 0),
      taxPercent: Number(item.taxPercent || item.globalSalesTaxRate || 0.15),
      supplierId: (item.supplierId || item.defaultSupplierId || '') as string,
      supplierName: (item.supplierName || item.defaultSupplierName || supplier || '') as string,
      brandId: (item.brandId || item.defaultBrandId || null) as string | null,
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
  processing: { label: 'Processing...', bg: '#fff7ed', color: '#9a3412', border: '#fed7aa' },
  submitted: { label: 'Submitted', bg: '#ecfdf5', color: '#065f46', border: '#a7f3d0' },
  failed: { label: 'Failed', bg: '#fef2f2', color: '#991b1b', border: '#fecaca' },
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
  const [status, setStatus] = useState(initial.status);
  const [notes, setNotes] = useState(initial.notes);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [stockItems, setStockItems] = useState<StockItem[]>([]);
  const [supplierMap, setSupplierMap] = useState<Record<string, string>>({});
  const [unitMap, setUnitMap] = useState<Record<string, { name: string; ratio: number }>>({});
  const [refLoading, setRefLoading] = useState(false);
  const [variantDropdown, setVariantDropdown] = useState<number | null>(null);
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

  // Load reference data (stock items with variants, suppliers, units)
  useEffect(() => {
    setRefLoading(true);
    const venueId = (props?.activeVenueId as string) || undefined;
    Promise.all([
      callComponentApi('purchase_order_editor', 'get_stock_items_detail', {}, venueId).catch(() => ({ data: [] })),
      callComponentApi('purchase_order_editor', 'get_suppliers', {}, venueId).catch(() => ({ data: [] })),
      callComponentApi('purchase_order_editor', 'get_units', {}, venueId).catch(() => ({ data: [] })),
    ]).then(([itemsRes, suppliersRes, unitsRes]) => {
      // Build supplier name map (handle both raw and mapped field names)
      const suppliers = Array.isArray(suppliersRes.data) ? suppliersRes.data as Record<string, unknown>[] : [];
      const sMap: Record<string, string> = {};
      for (const s of suppliers) sMap[String(s.id)] = String(s.name || s.supplierName || s.supplier || '');
      setSupplierMap(sMap);

      // Build unit map (handle both raw and mapped field names)
      const units = Array.isArray(unitsRes.data) ? unitsRes.data as Record<string, unknown>[] : [];
      const uMap: Record<string, { name: string; ratio: number }> = {};
      for (const u of units) uMap[String(u.id)] = { name: String(u.name || u.unitName || ''), ratio: Number(u.ratio || u.unitRatio || 1) };
      setUnitMap(uMap);

      // Build stock items with enriched supplier variants
      const rawItems = Array.isArray(itemsRes.data) ? itemsRes.data as Record<string, unknown>[] : [];
      const items: StockItem[] = rawItems.map(item => {
        const rawSuppliers = Array.isArray(item.suppliers) ? item.suppliers as Record<string, unknown>[] : [];
        return {
          id: String(item.id),
          name: String(item.name),
          groupName: String(item.groupName || ''),
          defaultSupplierId: String(item.defaultSupplierId || ''),
          globalSalesTaxSortOrder: Number(item.globalSalesTaxSortOrder || 0),
          orderingUnitId: String(item.orderingUnitId || ''),
          orderingUnitName: String(item.orderingUnitName || ''),
          orderingUnitRatio: Number(item.orderingUnitRatio || 1),
          suppliers: rawSuppliers.map(s => ({
            id: String(s.id),
            supplierId: String(s.supplierId),
            supplierName: sMap[String(s.supplierId)] || 'Unknown',
            unitId: String(s.unitId),
            unitName: uMap[String(s.unitId)]?.name || '',
            unitRatio: uMap[String(s.unitId)]?.ratio || Number(item.orderingUnitRatio || 1),
            unitCost: Number(s.unitCost || 0),
            stockCode: String(s.stockCode || ''),
            brandId: s.brandId ? String(s.brandId) : null,
            defaultForSupplier: Boolean(s.defaultForSupplier),
          })),
        };
      });
      setStockItems(items);
    }).finally(() => setRefLoading(false));
  }, [props?.activeVenueId]); // eslint-disable-line react-hooks/exhaustive-deps

  const title = (props?.title as string) || 'Purchase Order';
  const grandTotal = lines.reduce((sum, l) => sum + l.quantity * l.unit_price, 0);
  const hasPrice = lines.some(l => l.unit_price > 0);
  const interactive = !!onAction || !!workingDocId;
  const isSubmitted = status === 'submitted' || status === 'approved' || status === 'processing';
  const statusCfg = STATUS_CONFIG[status] || STATUS_CONFIG.draft;

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
        // Don't reset lines here — the optimistic update from the caller
        // already has the correct state with enrichment fields (variants,
        // supplierName, etc.) that extractOrder would strip.
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

  const handleAddFromSearch = useCallback((item: StockItem) => {
    // Find default variant: default supplier + defaultForSupplier=true
    const defaultVariant = item.suppliers.find(
      s => s.supplierId === item.defaultSupplierId && s.defaultForSupplier
    ) || item.suppliers.find(s => s.defaultForSupplier) || item.suppliers[0];

    const line: LineItem = {
      id: String(Date.now()),
      stock_code: defaultVariant?.stockCode || '',
      product: item.name,
      supplier: defaultVariant?.supplierName || '',
      quantity: 1,
      unit: defaultVariant?.unitName || item.orderingUnitName || 'each',
      unit_price: defaultVariant?.unitCost || 0,
      itemId: item.id,
      unitId: defaultVariant?.unitId || item.orderingUnitId,
      unitRatio: defaultVariant?.unitRatio || item.orderingUnitRatio || 1,
      unitCost: defaultVariant?.unitCost || 0,
      taxPercent: item.globalSalesTaxSortOrder === 1 ? 0.15 : 0,
      supplierId: defaultVariant?.supplierId || item.defaultSupplierId,
      supplierName: defaultVariant?.supplierName || '',
      brandId: defaultVariant?.brandId || null,
      variantId: defaultVariant?.id,
      variants: item.suppliers,
    };

    setLines(prev => [...prev, line]);
    setSearchQuery('');
    setSearchOpen(false);

    if (workingDocId) {
      patchDoc([{ op: 'add_line', fields: line as unknown as Record<string, unknown> }]);
    } else if (onAction && connectorName) {
      onAction({ connector_name: connectorName, action: 'add_line', params: line as unknown as Record<string, unknown> });
    }
  }, [workingDocId, patchDoc, onAction, connectorName]);

  const handleVariantChange = useCallback((lineIndex: number, variant: SupplierVariant) => {
    setLines(prev => prev.map((l, i) => {
      if (i !== lineIndex) return l;
      return {
        ...l,
        stock_code: variant.stockCode,
        supplier: variant.supplierName,
        supplierId: variant.supplierId,
        supplierName: variant.supplierName,
        unit: variant.unitName,
        unit_price: variant.unitCost,
        unitId: variant.unitId,
        unitRatio: variant.unitRatio,
        unitCost: variant.unitCost,
        brandId: variant.brandId,
        variantId: variant.id,
      };
    }));
    setVariantDropdown(null);
    // TODO: sync to working document if needed
  }, []);

  const searchResults = searchQuery.length >= 2
    ? stockItems.filter(item =>
        item.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        item.suppliers.some(s => s.stockCode.toLowerCase().includes(searchQuery.toLowerCase()))
      ).slice(0, 10)
    : [];

  const buildBatchPayload = useCallback(() => {
    // Group lines by supplierId for LoadedHub batch API
    const groups = new Map<string, LineItem[]>();
    for (const line of lines) {
      const key = line.supplierId || line.supplier || 'unknown';
      groups.set(key, [...(groups.get(key) || []), line]);
    }
    return Array.from(groups.entries()).map(([supplierId, groupLines]) => {
      const subtotal = groupLines.reduce((sum, l) => sum + (l.unitCost || l.unit_price) * l.quantity, 0);
      const taxRate = groupLines[0]?.taxPercent || 0.15;
      const tax = subtotal * taxRate;
      return {
        createdAt: new Date().toISOString(),
        isReceived: false,
        supplierId,
        lines: groupLines.map(l => ({
          itemId: l.itemId || '',
          itemCode: l.stock_code,
          brandId: l.brandId || null,
          unitId: l.unitId || '',
          unitRatio: l.unitRatio || 1,
          unitCost: Math.round(((l.unitCost || l.unit_price) * (l.unitRatio || 1)) * 100) / 100,
          quantityReceived: 0,
          taxPercent: l.taxPercent || 0.15,
          quantityOrdered: l.quantity,
          unitCostOrdered: Math.round((l.unitCost || l.unit_price) * 100) / 100,
        })),
        orderedBy: 'Norm',
        subtotal: Math.round(subtotal * 100) / 100,
        total: Math.round((subtotal + tax) * 100) / 100,
        tax: Math.round(tax * 100) / 100,
        status: 'Outstanding',
        creditRequest: false,
      };
    });
  }, [lines]);

  const handleSubmit = useCallback(async () => {
    setSaving(true);
    setStatus('processing');
    try {
      const batchPayload = buildBatchPayload();
      if (batchPayload.length === 0) {
        setStatus('draft');
        setSaving(false);
        return;
      }
      const venueId = (props?.activeVenueId as string) || undefined;
      const result = await callComponentApi('purchase_order_editor', 'create_orders_batch', batchPayload as unknown as Record<string, unknown>, venueId);
      if (result.error) {
        console.error('Submit failed:', result.data);
        setStatus('failed');
      } else {
        setStatus('submitted');
      }
    } catch (e) {
      console.error('Submit failed:', e);
      setStatus('failed');
    } finally { setSaving(false); }
  }, [buildBatchPayload, props]);

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
      border: '1px solid #e5e7eb', borderRadius: 10,
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
            {lines.map((l, i) => {
              // Resolve variants: use stored variants or look up from reference data
              const variants = l.variants || (l.itemId ? stockItems.find(si => si.id === l.itemId)?.suppliers : undefined) || [];
              return (
              <tr key={l.id || i} style={{ borderBottom: '1px solid #f3f4f6' }}>
                <td style={{ padding: '0.5rem 0.5rem', position: 'relative' }}>
                  {variants.length > 1 ? (
                    <>
                      <span
                        onClick={() => setVariantDropdown(variantDropdown === i ? null : i)}
                        style={{ color: '#2563eb', fontFamily: 'monospace', fontSize: '0.78rem', cursor: 'pointer', textDecoration: 'underline', textDecorationStyle: 'dotted' }}
                        title="Click to change supplier/variant"
                      >
                        {l.stock_code || '—'}
                      </span>
                      {variantDropdown === i && (
                        <div style={{
                          position: 'absolute', top: '100%', left: 0, zIndex: 50, minWidth: 320,
                          backgroundColor: '#fff', border: '1px solid #e2ddd7', borderRadius: 8,
                          boxShadow: '0 4px 16px rgba(0,0,0,0.1)', maxHeight: 200, overflowY: 'auto',
                        }}>
                          {variants.map(v => (
                            <div
                              key={v.id}
                              onClick={() => handleVariantChange(i, v)}
                              style={{
                                padding: '0.4rem 0.6rem', cursor: 'pointer', fontSize: '0.72rem',
                                borderBottom: '1px solid #f3f4f6',
                                backgroundColor: v.id === l.variantId ? '#f0f8ff' : '#fff',
                                display: 'flex', justifyContent: 'space-between', gap: 8,
                              }}
                              onMouseEnter={e => (e.currentTarget.style.backgroundColor = v.id === l.variantId ? '#f0f8ff' : '#fafafa')}
                              onMouseLeave={e => (e.currentTarget.style.backgroundColor = v.id === l.variantId ? '#f0f8ff' : '#fff')}
                            >
                              <span style={{ color: '#333' }}>
                                {v.supplierName} · {v.unitName} · <span style={{ fontFamily: 'monospace' }}>{v.stockCode || '—'}</span>
                              </span>
                              <span style={{ fontWeight: 600, color: '#333' }}>${v.unitCost.toFixed(2)}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  ) : (
                    <span style={{ color: '#6b7280', fontFamily: 'monospace', fontSize: '0.78rem' }}>{l.stock_code || '—'}</span>
                  )}
                </td>
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
              );
            })}
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

      {/* ── Add item search ── */}
      {interactive && !isSubmitted && (
        <div style={{ padding: '0 1.25rem', position: 'relative' }}>
          <div style={{ margin: '0.5rem 0', display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              value={searchQuery}
              onChange={e => { setSearchQuery(e.target.value); setSearchOpen(true); }}
              onFocus={() => searchQuery.length >= 2 && setSearchOpen(true)}
              placeholder={refLoading ? 'Loading stock items...' : `Search ${stockItems.length} items...`}
              disabled={refLoading}
              style={{ ...inputStyle, flex: 1, maxWidth: 400 }}
            />
            {searchQuery && (
              <button onClick={() => { setSearchQuery(''); setSearchOpen(false); }} style={{
                border: 'none', background: 'none', color: '#aaa', cursor: 'pointer', fontSize: '0.8rem',
              }}>&#10005;</button>
            )}
          </div>
          {/* Search results dropdown */}
          {searchOpen && searchResults.length > 0 && (
            <div style={{
              position: 'absolute', left: '1.25rem', right: '1.25rem', zIndex: 50,
              backgroundColor: '#fff', border: '1px solid #e2ddd7', borderRadius: 8,
              boxShadow: '0 4px 16px rgba(0,0,0,0.1)', maxHeight: 300, overflow: 'auto',
            }}>
              {searchResults.map(item => {
                const defaultVariant = item.suppliers.find(
                  s => s.supplierId === item.defaultSupplierId && s.defaultForSupplier
                ) || item.suppliers.find(s => s.defaultForSupplier) || item.suppliers[0];
                return (
                  <div
                    key={item.id}
                    onClick={() => handleAddFromSearch(item)}
                    style={{
                      padding: '0.5rem 0.75rem', cursor: 'pointer',
                      borderBottom: '1px solid #f3f4f6',
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.backgroundColor = '#f8f8f5')}
                    onMouseLeave={e => (e.currentTarget.style.backgroundColor = '#fff')}
                  >
                    <div>
                      <div style={{ fontSize: '0.82rem', fontWeight: 500, color: '#333' }}>{item.name}</div>
                      <div style={{ fontSize: '0.68rem', color: '#999' }}>
                        {item.groupName}
                        {defaultVariant && ` · ${defaultVariant.supplierName} · ${defaultVariant.unitName || 'unit'} · ${defaultVariant.stockCode || 'no code'}`}
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      {defaultVariant && (
                        <>
                          <div style={{ fontSize: '0.78rem', fontWeight: 600, color: '#333' }}>
                            ${defaultVariant.unitCost.toFixed(2)}
                          </div>
                          <div style={{ fontSize: '0.62rem', color: '#aaa' }}>
                            {defaultVariant.unitName || item.orderingUnitName}
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {searchOpen && searchQuery.length >= 2 && searchResults.length === 0 && (
            <div style={{
              position: 'absolute', left: '1.25rem', right: '1.25rem', zIndex: 50,
              backgroundColor: '#fff', border: '1px solid #e2ddd7', borderRadius: 8,
              padding: '0.75rem', textAlign: 'center', color: '#999', fontSize: '0.78rem',
            }}>
              No items found for &quot;{searchQuery}&quot;
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
