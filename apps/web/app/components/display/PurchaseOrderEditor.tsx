'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Check, ChevronDown, ChevronRight, ChevronUp, LoaderCircle, Search, TriangleAlert, X } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch, callComponentApi, getStoredUser } from '../../lib/api';
import { formatMoney, formatNumber } from '../../lib/format';
import Icon from '../ui/Icon';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import Badge, { type BadgeTone } from '../ui/Badge';

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
  globalPrice: number;
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
  const candidates = ['lines', 'items', 'lineItems', 'line_items', 'products', 'orderLines', 'order_lines'];
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
  return formatMoney(n);
}

// Status pill tones: a draft is informational, a submit still waiting is warn,
// done is ok, failed/rejected is error.
const STATUS_CONFIG: Record<string, { label: string; tone: BadgeTone }> = {
  draft: { label: 'Draft', tone: 'info' },
  processing: { label: 'Processing…', tone: 'neutral' },
  submitted: { label: 'Submitted', tone: 'ok' },
  failed: { label: 'Failed', tone: 'error' },
  pending_submit: { label: 'Pending', tone: 'warn' },
  approved: { label: 'Approved', tone: 'ok' },
  rejected: { label: 'Rejected', tone: 'error' },
};

// The working document's sync dot beside the status pill.
const SYNC_DOT: Record<string, string> = { pending_submit: 'var(--warn)', error: 'var(--error)' };

// Below this card width (a phone, a narrow pane) the unit price folds under
// the line total so the row fits without scrolling sideways.
const COMPACT_BELOW = 560;

/** Secondary line under a list choice's main text (search results): meta. */
const SUB: React.CSSProperties = { marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)' };

/** Secondary line under an order line (supplier · unit · code, "@ $19.44"):
 *  staff check it before placing the order, so it reads at 13px. Its size is
 *  set per layout (see `lineSubSize`). */
const LINE_SUB: React.CSSProperties = { marginTop: 2, color: 'var(--muted)' };

/** A list choice (stock search result, supplier variant): .n-option laid out as a row. */
const CHOICE: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px' };

const SPIN: React.CSSProperties = { animation: 'n-spin 1s linear infinite' };

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
  const [debugOpen, setDebugOpen] = useState(false);
  const [debugSearch, setDebugSearch] = useState('');
  const [debugExpanded, setDebugExpanded] = useState<Set<string>>(new Set());
  const [livePrices, setLivePrices] = useState<Record<string, { cost: number; unitName: string; documentNumber: string }>>({});
  const connectorName = (props?.connector_name as string) || '';
  const isAdmin = getStoredUser()?.role === 'admin';
  const embedded = !!props?.embedded;

  // Embedded (MCP App) only: the card renders the instant the tool returns, but
  // the agent may keep refining the draft afterwards — resolving an ambiguous
  // product ("which Corona?"), converting bottles to packs. With no session to
  // push updates into the iframe, `userTookOver` guards a poll (below) that
  // pulls the working document until the user starts editing, after which their
  // state wins. Inert in Norm, where the page already reflects live edits.
  const [userTookOver, setUserTookOver] = useState(false);
  const [pollExhausted, setPollExhausted] = useState(false);
  const lastVersionRef = useRef(0);

  // Layout only: the card's own width picks the compact table (see COMPACT_BELOW).
  const rootRef = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(false);
  const notesId = React.useId();
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect.width;
      if (w) setCompact(w < COMPACT_BELOW);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Embedded-only: a raw draft can carry a not-yet-resolved line — a bare
  // {itemId, quantity} the sandbox can't expand, or an ambiguous item parked
  // in needs_selection — which extractOrder turns into a phantom row (a
  // quantity with no product). In Norm the on-mount reference fetch resolves
  // those; the sandbox can't, so drop phantoms and let the "preparing" state
  // and the poll show through until a real, named line exists.
  const usableLines = useCallback(
    (ls: LineItem[]) =>
      embedded ? ls.filter(l => (l.product || '').trim().length > 0) : ls,
    [embedded],
  );

  useEffect(() => {
    if (!workingDocId || !threadId) return;
    apiFetch(`/api/threads/${threadId}/working-documents/${workingDocId}`)
      .then(res => res.ok ? res.json() : null)
      .then(doc => {
        if (doc) {
          setOrderData(doc.data);
          setDocVersion(doc.version);
          lastVersionRef.current = doc.version;
          setSyncStatus(doc.sync_status);
          const parsed = extractOrder(doc.data);
          setLines(usableLines(parsed.lines));
          setNotes(parsed.notes);
        }
      })
      .catch(() => {});
  }, [workingDocId, threadId, usableLines]);

  // The catch-up poll. Runs only while embedded and untouched; a bumped version
  // means the agent changed the draft, so re-derive from it. Stops on submit,
  // on user takeover, or after ~110s (matches the playbook's own timeout).
  useEffect(() => {
    if (!embedded || !workingDocId || !threadId || userTookOver) return;
    if (status === 'submitted' || status === 'approved') return;
    let polls = 0;
    const id = setInterval(async () => {
      if (++polls > 45) { clearInterval(id); setPollExhausted(true); return; }
      try {
        const res = await apiFetch(`/api/threads/${threadId}/working-documents/${workingDocId}`);
        if (!res.ok) return;
        const doc = await res.json();
        if (!doc || typeof doc.version !== 'number' || doc.version <= lastVersionRef.current) return;
        lastVersionRef.current = doc.version;
        setOrderData(doc.data);
        setDocVersion(doc.version);
        setSyncStatus(doc.sync_status);
        const parsed = extractOrder(doc.data);
        setLines(usableLines(parsed.lines));
        setNotes(parsed.notes);
        if (parsed.status) setStatus(parsed.status);
      } catch { /* transient — try again next tick */ }
    }, 2500);
    return () => clearInterval(id);
  }, [embedded, workingDocId, threadId, userTookOver, status, usableLines]);

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
          globalPrice: Number(item.globalPrice || 0),
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

  // Fetch live prices for order line items and update their unit_price
  const _fetchLivePricesForLines = useCallback(async (targetLines: LineItem[]) => {
    const venueId = (props?.activeVenueId as string) || undefined;
    const itemIds = targetLines.map(l => l.itemId).filter(Boolean);
    if (itemIds.length === 0) return;

    const now = new Date();
    const offset = '+13:00';
    const ts = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}T${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:00.000${offset}`;
    const qs = itemIds.map(id => `itemIdTimeStrings=${encodeURIComponent(`${id},${ts}`)}`).join('&');

    try {
      const res = await callComponentApi('purchase_order_editor', 'get_live_prices', { query_string: qs }, venueId);
      const costs = (res.data as Record<string, unknown>)?.itemCosts as Record<string, Array<{ cost: number; unitName: string }>> | undefined;
      if (costs) {
        setLines(prev => prev.map(line => {
          if (!line.itemId) return line;
          const entry = costs[line.itemId];
          if (entry && entry.length > 0) {
            return { ...line, unit_price: entry[0].cost, unitCost: entry[0].cost };
          }
          return line;
        }));
        // Also update livePrices state for the debug panel
        const prices: Record<string, { cost: number; unitName: string; documentNumber: string }> = {};
        for (const [itemId, entries] of Object.entries(costs)) {
          if (Array.isArray(entries) && entries.length > 0) {
            prices[itemId] = { cost: entries[0].cost, unitName: entries[0].unitName, documentNumber: (entries[0] as Record<string, unknown>).documentNumber as string || '' };
          }
        }
        setLivePrices(prev => ({ ...prev, ...prices }));
      }
    } catch { /* ignore price fetch errors */ }
  }, [props?.activeVenueId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-resolve order_lines from LLM when both stock items and order data are available
  useEffect(() => {
    if (stockItems.length === 0) return;

    // Get order_lines from working document data or direct data prop
    const sourceData = (orderData || data) as Record<string, unknown>;
    const orderLines = sourceData?.order_lines;
    if (!Array.isArray(orderLines) || orderLines.length === 0) return;

    // Only resolve once — skip if lines already have stock codes
    if (lines.some(l => l.stock_code && l.stock_code !== '—' && l.stock_code !== '')) return;

    const resolved: LineItem[] = [];
    for (const ol of orderLines as Record<string, unknown>[]) {
      const itemId = String(ol.itemId || '');
      if (!itemId) continue;
      const stockItem = stockItems.find(si => si.id === itemId);
      if (!stockItem) continue;

      let variant: typeof stockItem.suppliers[0] | undefined;
      const reqSupplierId = String(ol.supplierId || '');
      const reqVariantId = String(ol.variantId || '');

      if (reqVariantId) {
        variant = stockItem.suppliers.find(v => v.id === reqVariantId);
      } else if (reqSupplierId) {
        variant = stockItem.suppliers.find(v => v.supplierId === reqSupplierId && v.defaultForSupplier)
          || stockItem.suppliers.find(v => v.supplierId === reqSupplierId);
      } else {
        variant = stockItem.suppliers.find(v => v.supplierId === stockItem.defaultSupplierId && v.defaultForSupplier)
          || stockItem.suppliers.find(v => v.defaultForSupplier)
          || stockItem.suppliers[0];
      }

      resolved.push({
        id: String(Date.now()) + Math.random(),
        stock_code: variant?.stockCode || '',
        product: stockItem.name,
        supplier: variant?.supplierName || '',
        quantity: Number(ol.quantity || ol.orderQty || 1),
        unit: variant?.unitName || stockItem.orderingUnitName || 'each',
        unit_price: variant?.unitCost || 0,
        itemId: stockItem.id,
        unitId: variant?.unitId || stockItem.orderingUnitId,
        unitRatio: variant?.unitRatio || stockItem.orderingUnitRatio || 1,
        unitCost: variant?.unitCost || 0,
        taxPercent: stockItem.globalSalesTaxSortOrder === 1 ? 0.15 : 0,
        supplierId: variant?.supplierId || stockItem.defaultSupplierId,
        supplierName: variant?.supplierName || '',
        brandId: variant?.brandId || null,
        variantId: variant?.id,
        variants: stockItem.suppliers,
      });
    }
    if (resolved.length > 0) {
      setLines(resolved);
      // Fetch live prices for the resolved items
      _fetchLivePricesForLines(resolved);
    }
  }, [stockItems, orderData]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch live prices for all stock items (admin debug)
  useEffect(() => {
    if (!isAdmin || stockItems.length === 0) return;
    const venueId = (props?.activeVenueId as string) || undefined;
    const now = new Date();
    const offset = '+13:00';
    const ts = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}T${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:00.000${offset}`;

    // Batch in groups of 20
    const batchSize = 20;
    const batches: string[][] = [];
    for (let i = 0; i < stockItems.length; i += batchSize) {
      batches.push(stockItems.slice(i, i + batchSize).map(s => s.id));
    }

    const fetchBatch = async (ids: string[]) => {
      const qs = ids.map(id => `itemIdTimeStrings=${encodeURIComponent(`${id},${ts}`)}`).join('&');
      try {
        const res = await callComponentApi('purchase_order_editor', 'get_live_prices', { query_string: qs }, venueId);
        const costs = (res.data as Record<string, unknown>)?.itemCosts as Record<string, Array<{ cost: number; unitName: string; documentNumber: string }>> | undefined;
        if (costs) {
          const prices: Record<string, { cost: number; unitName: string; documentNumber: string }> = {};
          for (const [itemId, entries] of Object.entries(costs)) {
            if (Array.isArray(entries) && entries.length > 0) {
              prices[itemId] = { cost: entries[0].cost, unitName: entries[0].unitName, documentNumber: entries[0].documentNumber };
            }
          }
          setLivePrices(prev => ({ ...prev, ...prices }));
        }
      } catch { /* ignore price fetch errors */ }
    };

    // Fetch first batch immediately, rest with delay to avoid hammering API
    if (batches.length > 0) {
      fetchBatch(batches[0]);
      batches.slice(1).forEach((batch, i) => {
        setTimeout(() => fetchBatch(batch), (i + 1) * 500);
      });
    }
  }, [stockItems, isAdmin]); // eslint-disable-line react-hooks/exhaustive-deps

  const title = (props?.title as string) || 'Purchase order';
  const grandTotal = lines.reduce((sum, l) => sum + l.quantity * l.unit_price, 0);
  const hasPrice = lines.some(l => l.unit_price > 0);
  const interactive = !!onAction || !!workingDocId;
  const isSubmitted = status === 'submitted' || status === 'approved';
  const statusCfg = STATUS_CONFIG[status] || STATUS_CONFIG.draft;

  // Header: the supplier names the order when there is exactly one (a generic
  // title becomes "Purchase order — Bidfood"); the meta line carries the rest.
  const suppliers = Array.from(new Set(lines.map(l => (l.supplier || '').trim()).filter(Boolean)));
  const singleSupplier = suppliers.length === 1 ? suppliers[0] : '';
  const heading = singleSupplier && /^purchase order$/i.test(title.trim())
    ? `Purchase order — ${singleSupplier}`
    : title.replace(/^Purchase Order\b/, 'Purchase order');
  const metaParts = [
    initial.venue && !heading.includes(initial.venue) ? initial.venue : '',
    singleSupplier
      ? (heading.includes(singleSupplier) ? '' : singleSupplier)
      : suppliers.length > 1 ? `${suppliers.length} suppliers` : '',
    `${lines.length} line${lines.length !== 1 ? 's' : ''}`,
    initial.reference ? `Ref ${initial.reference}` : '',
  ].filter(Boolean);

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
    setUserTookOver(true);
    setLines(prev => prev.map((l, i) => i === idx ? { ...l, quantity: Math.max(0, qty) } : l));
    if (workingDocId) {
      patchDoc([{ op: 'update_line', index: idx, fields: { quantity: qty } }]);
    } else if (onAction && connectorName) {
      onAction({ connector_name: connectorName, action: 'update_line', params: { index: idx, quantity: qty } });
    }
  }, [workingDocId, patchDoc, onAction, connectorName]);

  const handleRemove = useCallback((idx: number) => {
    setUserTookOver(true);
    setLines(prev => prev.filter((_, i) => i !== idx));
    if (workingDocId) {
      patchDoc([{ op: 'remove_line', index: idx }]);
    } else if (onAction && connectorName) {
      onAction({ connector_name: connectorName, action: 'remove_line', params: { index: idx } });
    }
  }, [workingDocId, patchDoc, onAction, connectorName]);

  const handleAddFromSearch = useCallback((item: StockItem) => {
    setUserTookOver(true);
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

    // Fetch live price for the added item
    _fetchLivePricesForLines([line]);

    if (workingDocId) {
      patchDoc([{ op: 'add_line', fields: line as unknown as Record<string, unknown> }]);
    } else if (onAction && connectorName) {
      onAction({ connector_name: connectorName, action: 'add_line', params: line as unknown as Record<string, unknown> });
    }
  }, [workingDocId, patchDoc, onAction, connectorName]);

  const handleVariantChange = useCallback((lineIndex: number, variant: SupplierVariant) => {
    setUserTookOver(true);
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
        // Persist status to working document so it survives page refresh
        if (workingDocId && threadId) {
          patchDoc([{ op: 'set_status', value: 'submitted' }]);
        }
      }
    } catch (e) {
      console.error('Submit failed:', e);
      setStatus('failed');
    } finally { setSaving(false); }
  }, [buildBatchPayload, props]);

  const handleNotesChange = useCallback((value: string) => {
    setUserTookOver(true);
    setNotes(value);
    if (workingDocId) {
      patchDoc([{ op: 'update_notes', value }]);
    }
  }, [workingDocId, patchDoc]);

  // ── Layout ── Columns: Item · Qty · Unit price · Line total · remove. In a
  // compact card the unit price moves under the line total ("@ $19.44").
  const editable = interactive && !isSubmitted;
  const colCount = 2 + (hasPrice ? (compact ? 1 : 2) : 0) + (editable ? 1 : 0);
  const padX = compact ? 8 : 12;
  const edge = compact ? 12 : 16; // first and last cell line up with the card's gutter
  const cell = (first = false, last = false): React.CSSProperties => ({
    paddingLeft: first ? edge : padX,
    paddingRight: last ? edge : padX,
  });
  const totalRule: React.CSSProperties = { borderTop: '1px solid var(--line-strong)', borderBottom: 'none' };
  // 13px under a line: --fs-sm, or in a compact card (a phone) --fs-xs, which
  // is 13px on touch screens and wraps less in the narrow item column.
  const lineSubSize = compact ? 'var(--fs-xs)' : 'var(--fs-sm)';

  return (
    <div ref={rootRef} data-testid="po-editor" className="n-card" style={{ marginBottom: 12 }}>
      {/* ── Header: title, venue · supplier · lines, status ── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: `14px ${edge}px 12px` }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.35, color: 'var(--text)', overflowWrap: 'anywhere' }}>
            {heading}
          </div>
          <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)', fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere' }}>
            {metaParts.join(' · ')}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '0 0 auto', marginTop: 2 }}>
          {workingDocId && syncStatus !== 'synced' && (
            <span
              role="img"
              aria-label={`Sync: ${syncStatus}`}
              title={`Sync: ${syncStatus}`}
              style={{ width: 8, height: 8, borderRadius: '50%', display: 'inline-block', backgroundColor: SYNC_DOT[syncStatus] || 'var(--icon)' }}
            />
          )}
          <Badge tone={statusCfg.tone}>{statusCfg.label}</Badge>
        </div>
      </div>

      {/* ── Line items ── */}
      <div style={{ overflowX: 'auto' }}>
        <table className="n-table">
          <thead>
            <tr>
              <th style={cell(true)}>Item</th>
              <th className="num" style={cell(false, !hasPrice && !editable)}>Qty</th>
              {hasPrice && !compact && <th className="num" style={cell()}>Unit price</th>}
              {hasPrice && <th className="num" style={cell(false, !editable)}>{compact ? 'Total' : 'Line total'}</th>}
              {editable && <th style={{ width: 1, padding: 0 }} />}
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => {
              // Resolve variants: use stored variants or look up from reference data
              const variants = l.variants || (l.itemId ? stockItems.find(si => si.id === l.itemId)?.suppliers : undefined) || [];
              const canPick = variants.length > 1;
              const open = variantDropdown === i;
              const variantText = [l.supplier, l.unit, l.stock_code].filter(Boolean).join(' · ');
              return (
                <React.Fragment key={l.id || i}>
                  <tr>
                    <td style={cell(true)}>
                      <div style={{ fontWeight: 500, color: 'var(--text)', overflowWrap: 'anywhere' }}>{l.product || '—'}</div>
                      {canPick ? (
                        <button
                          type="button"
                          onClick={() => setVariantDropdown(open ? null : i)}
                          aria-expanded={open}
                          title="Change supplier or variant"
                          style={{
                            display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 2, padding: 0,
                            border: 'none', background: 'none', cursor: 'pointer', textAlign: 'left',
                            fontFamily: 'inherit', fontSize: lineSubSize, fontWeight: 500, color: 'var(--accent)',
                          }}
                        >
                          <span style={{ overflowWrap: 'anywhere' }}>{variantText || 'Choose a supplier'}</span>
                          <Icon icon={open ? ChevronUp : ChevronDown} size="meta" />
                        </button>
                      ) : variantText ? (
                        <div style={{ ...LINE_SUB, fontSize: lineSubSize, overflowWrap: 'anywhere' }}>{variantText}</div>
                      ) : null}
                    </td>
                    <td className="num" style={cell(false, !hasPrice && !editable)}>
                      {editable ? (
                        <input
                          type="number"
                          min={0}
                          value={l.quantity}
                          onChange={e => handleQtyChange(i, parseInt(e.target.value, 10) || 0)}
                          aria-label={`Quantity of ${l.product || 'this line'}`}
                          className="n-input"
                          style={{ width: compact ? 60 : 72, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
                        />
                      ) : (
                        l.quantity
                      )}
                    </td>
                    {hasPrice && !compact && (
                      <td className="num" style={{ ...cell(), whiteSpace: 'nowrap' }}>{formatCurrency(l.unit_price)}</td>
                    )}
                    {hasPrice && (
                      <td className="num" style={{ ...cell(false, !editable), whiteSpace: 'nowrap' }}>
                        {formatCurrency(l.quantity * l.unit_price)}
                        {compact && <div style={{ ...LINE_SUB, fontSize: lineSubSize }}>@ {formatCurrency(l.unit_price)}</div>}
                      </td>
                    )}
                    {editable && (
                      <td style={{ width: 1, padding: `0 ${edge - 10}px 0 0` }}>
                        <IconButton icon={X} label="Remove line" iconSize={16} onClick={() => handleRemove(i)} />
                      </td>
                    )}
                  </tr>
                  {/* Supplier variants: an inline list under the line, so it is
                      never clipped by the table's scroll box or Claude's frame. */}
                  {open && canPick && (
                    <tr>
                      <td colSpan={colCount} style={{ padding: `0 ${edge}px 12px`, background: 'var(--surface)' }}>
                        <div style={{ maxWidth: 520, border: '1px solid var(--line)', borderRadius: 'var(--radius)', background: 'var(--bg)', overflow: 'hidden' }}>
                          {variants.map((v, vi) => {
                            const current = v.id === l.variantId;
                            return (
                              <button
                                key={v.id}
                                type="button"
                                className="n-option"
                                aria-current={current || undefined}
                                onClick={() => handleVariantChange(i, v)}
                                style={{
                                  ...CHOICE,
                                  borderTop: vi ? '1px solid var(--line-soft)' : 'none',
                                  ...(current ? { background: 'var(--selected)' } : {}),
                                }}
                              >
                                <span style={{ display: 'inline-flex', width: 16, flex: '0 0 auto' }}>
                                  {current && <Icon icon={Check} size={16} tone="accent" />}
                                </span>
                                <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
                                  {[v.supplierName, v.unitName, v.stockCode || '—'].filter(Boolean).join(' · ')}
                                </span>
                                <span style={{ flex: '0 0 auto', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                                  {formatCurrency(v.unitCost)}
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
            {lines.length === 0 && (
              <tr>
                <td colSpan={colCount} style={{ padding: `20px ${edge}px`, textAlign: 'center', color: 'var(--muted)', fontSize: 'var(--fs-sm)', background: 'var(--bg)' }}>
                  {embedded && !userTookOver && !pollExhausted ? (
                    <span role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                      <Icon icon={LoaderCircle} size={16} tone="muted" style={SPIN} />
                      Norm is preparing this order&hellip;
                    </span>
                  ) : embedded && pollExhausted ? (
                    <span role="alert" style={{
                      display: 'inline-flex', alignItems: 'flex-start', gap: 8, padding: '10px 12px', textAlign: 'left',
                      borderRadius: 'var(--radius)', background: 'var(--warn-bg)', color: 'var(--warn)',
                    }}>
                      <Icon icon={TriangleAlert} size={16} style={{ marginTop: 1 }} />
                      Still preparing — open in Norm to finish this order.
                    </span>
                  ) : 'No items yet'}
                </td>
              </tr>
            )}
            {/* ── Add an item: search the venue's stock list ── */}
            {editable && (
              <tr>
                <td colSpan={colCount} style={{ padding: `10px ${edge}px`, background: 'var(--bg)' }}>
                  <div style={{ maxWidth: 480 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <div style={{ position: 'relative', flex: '1 1 auto', minWidth: 0 }}>
                        <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
                        <input
                          value={searchQuery}
                          onChange={e => { setSearchQuery(e.target.value); setSearchOpen(true); }}
                          onFocus={() => searchQuery.length >= 2 && setSearchOpen(true)}
                          placeholder={refLoading ? 'Loading stock items…' : `Search ${formatNumber(stockItems.length)} items to add…`}
                          aria-label="Search stock items to add"
                          disabled={refLoading}
                          className="n-input"
                          style={{ width: '100%', paddingLeft: 34 }}
                        />
                      </div>
                      {searchQuery && (
                        <IconButton icon={X} label="Clear search" iconSize={16} onClick={() => { setSearchQuery(''); setSearchOpen(false); }} />
                      )}
                    </div>
                    {/* Results sit in the flow (not floating), so a narrow card or
                        Claude's frame grows to show them instead of clipping. */}
                    {searchOpen && searchResults.length > 0 && (
                      <div className="scroll-quiet" style={{ marginTop: 6, maxHeight: 300, border: '1px solid var(--line)', borderRadius: 'var(--radius)', background: 'var(--bg)' }}>
                        {searchResults.map((item, ri) => {
                          const defaultVariant = item.suppliers.find(
                            s => s.supplierId === item.defaultSupplierId && s.defaultForSupplier
                          ) || item.suppliers.find(s => s.defaultForSupplier) || item.suppliers[0];
                          const detail = [
                            item.groupName,
                            ...(defaultVariant ? [defaultVariant.supplierName, defaultVariant.unitName, defaultVariant.stockCode || 'no code'] : []),
                          ].filter(Boolean).join(' · ');
                          return (
                            <button
                              key={item.id}
                              type="button"
                              className="n-option"
                              onClick={() => handleAddFromSearch(item)}
                              style={{ ...CHOICE, justifyContent: 'space-between', borderTop: ri ? '1px solid var(--line-soft)' : 'none' }}
                            >
                              <span style={{ minWidth: 0 }}>
                                <span style={{ display: 'block', fontSize: 'var(--fs-base)', fontWeight: 500, overflowWrap: 'anywhere' }}>{item.name}</span>
                                {detail && <span style={{ ...SUB, display: 'block', overflowWrap: 'anywhere' }}>{detail}</span>}
                              </span>
                              {defaultVariant && (
                                <span style={{ flex: '0 0 auto', textAlign: 'right' }}>
                                  <span style={{ display: 'block', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                                    {formatCurrency(defaultVariant.unitCost)}
                                  </span>
                                  <span style={{ ...SUB, display: 'block' }}>{defaultVariant.unitName || item.orderingUnitName}</span>
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    )}
                    {searchOpen && searchQuery.length >= 2 && searchResults.length === 0 && (
                      <div style={{ marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                        No items found for &ldquo;{searchQuery}&rdquo;
                      </div>
                    )}
                  </div>
                </td>
              </tr>
            )}
          </tbody>
          {hasPrice && (
            <tfoot>
              <tr>
                <td colSpan={compact ? 2 : 3} style={{ ...cell(true), ...totalRule, fontWeight: 600 }}>Order total</td>
                <td className="num" style={{ ...cell(false, !editable), ...totalRule, fontWeight: 600, whiteSpace: 'nowrap' }}>
                  {formatCurrency(grandTotal)}
                </td>
                {editable && <td style={{ ...totalRule, padding: 0 }} />}
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      {/* ── Notes ── */}
      <div style={{ padding: `12px ${edge}px`, borderTop: '1px solid var(--line)' }}>
        <label className="n-label" htmlFor={editable ? notesId : undefined}>Notes to supplier</label>
        {editable ? (
          <textarea
            id={notesId}
            value={notes}
            onChange={e => handleNotesChange(e.target.value)}
            placeholder="Add any special instructions or notes…"
            rows={2}
            className="n-input"
            style={{ display: 'block', width: '100%' }}
          />
        ) : (
          <div style={{ fontSize: 'var(--fs-sm)', color: notes ? 'var(--text)' : 'var(--muted)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
            {notes || 'No notes'}
          </div>
        )}
      </div>

      {/* ── Failed submit ── */}
      {status === 'failed' && (
        <div role="alert" style={{
          display: 'flex', alignItems: 'flex-start', gap: 8, margin: `0 ${edge}px 12px`, padding: '10px 12px',
          borderRadius: 'var(--radius)', background: 'var(--error-bg)', color: 'var(--error)', fontSize: 'var(--fs-sm)',
        }}>
          <Icon icon={TriangleAlert} size={16} style={{ marginTop: 1 }} />
          <span>This order couldn&rsquo;t be placed. Check the lines and try again.</span>
        </div>
      )}

      {/* ── Actions ── */}
      {interactive && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8, padding: `12px ${edge}px`, borderTop: '1px solid var(--line-soft)' }}>
          {isSubmitted ? (
            <span role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minHeight: 34, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--ok)' }}>
              <Icon icon={Check} size={16} strokeWidth={2} />
              Sent
            </span>
          ) : (
            <Button variant="primary" onClick={handleSubmit} disabled={saving || isSubmitted || lines.length === 0}>
              {saving && <Icon icon={LoaderCircle} size={16} strokeWidth={2} style={SPIN} />}
              {saving ? 'Sending…' : 'Place Order'}
            </Button>
          )}
        </div>
      )}

      {/* Admin Debug Panel — stock items with variants */}
      {isAdmin && (
        <div style={{ borderTop: '1px solid var(--line)' }}>
          <button
            type="button"
            onClick={() => setDebugOpen(!debugOpen)}
            aria-expanded={debugOpen}
            style={{
              width: '100%', padding: `10px ${edge}px`, display: 'flex', alignItems: 'center', gap: 6,
              background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit',
              fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--muted)', textAlign: 'left',
            }}
          >
            <Icon icon={debugOpen ? ChevronDown : ChevronRight} size="dense" tone="muted" />
            Debug: stock items ({formatNumber(stockItems.length)} loaded)
          </button>

          {debugOpen && (
            <div style={{ padding: `0 ${edge}px 14px` }}>
              <input
                value={debugSearch}
                onChange={e => setDebugSearch(e.target.value)}
                placeholder="Search items or stock codes…"
                aria-label="Search debug stock items"
                className="n-input"
                style={{ width: '100%', marginBottom: 8 }}
              />

              {(() => {
                const query = debugSearch.toLowerCase();
                const filtered = query.length >= 1
                  ? stockItems.filter(item =>
                      item.name.toLowerCase().includes(query) ||
                      item.id.toLowerCase().includes(query) ||
                      item.suppliers.some(s => s.stockCode.toLowerCase().includes(query))
                    )
                  : stockItems;
                const display = filtered.slice(0, 50);
                const dc: React.CSSProperties = { padding: '6px 10px' };
                const sticky: React.CSSProperties = { ...dc, position: 'sticky', top: 0, zIndex: 1 };

                return (
                  <>
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 6 }}>
                      Showing {display.length} of {filtered.length}{filtered.length !== stockItems.length ? ` (${stockItems.length} total)` : ''}
                    </div>
                    <div className="scroll-quiet" style={{ maxHeight: 400, border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
                      <table className="n-table" style={{ fontSize: 'var(--fs-xs)' }}>
                        <thead>
                          <tr>
                            <th style={sticky}>Item</th>
                            <th style={sticky}>Group</th>
                            <th style={sticky}>Default supplier</th>
                            <th className="num" style={sticky}>Price</th>
                            <th style={{ ...sticky, textAlign: 'center' }}>Variants</th>
                          </tr>
                        </thead>
                        <tbody>
                          {display.map(item => {
                            const isExpanded = debugExpanded.has(item.id);
                            const defaultSupplier = supplierMap[item.defaultSupplierId] || item.defaultSupplierId.slice(0, 8);
                            const sub: React.CSSProperties = { ...dc, background: 'var(--surface)', color: 'var(--text-soft)' };
                            return (
                              <React.Fragment key={item.id}>
                                <tr
                                  onClick={() => {
                                    const next = new Set(debugExpanded);
                                    if (next.has(item.id)) next.delete(item.id); else next.add(item.id);
                                    setDebugExpanded(next);
                                  }}
                                  style={{ cursor: 'pointer' }}
                                >
                                  <td style={{ ...dc, ...(isExpanded ? { background: 'var(--selected)' } : {}) }}>
                                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                      <Icon icon={isExpanded ? ChevronDown : ChevronRight} size="meta" tone="muted" />
                                      {item.name}
                                    </span>
                                  </td>
                                  <td style={{ ...dc, color: 'var(--text-soft)', ...(isExpanded ? { background: 'var(--selected)' } : {}) }}>{item.groupName}</td>
                                  <td style={{ ...dc, color: 'var(--text-soft)', ...(isExpanded ? { background: 'var(--selected)' } : {}) }}>{defaultSupplier}</td>
                                  <td className="num" style={{ ...dc, whiteSpace: 'nowrap', ...(isExpanded ? { background: 'var(--selected)' } : {}) }}>
                                    {livePrices[item.id]
                                      ? <span style={{ color: 'var(--ok)', fontWeight: 500 }}>{formatCurrency(livePrices[item.id].cost)}<span style={{ color: 'var(--muted)', fontWeight: 400, marginLeft: 3 }}>/{livePrices[item.id].unitName}</span></span>
                                      : <span style={{ color: 'var(--muted)' }}>—</span>
                                    }
                                  </td>
                                  <td style={{ ...dc, textAlign: 'center', color: 'var(--muted)', ...(isExpanded ? { background: 'var(--selected)' } : {}) }}>{item.suppliers.length}</td>
                                </tr>
                                {isExpanded && item.suppliers.map((v, vi) => (
                                  <tr key={vi}>
                                    <td style={{ ...sub, paddingLeft: 28 }}>{v.stockCode || '—'}</td>
                                    <td style={sub}>{v.supplierName}</td>
                                    <td style={sub}>{v.unitName} {v.unitRatio !== 1 ? `(×${v.unitRatio})` : ''}</td>
                                    <td className="num" style={{ ...sub, color: 'var(--ok)', fontWeight: 500, whiteSpace: 'nowrap' }}>{formatCurrency(v.unitCost)}</td>
                                    <td style={{ ...sub, textAlign: 'center' }}>
                                      {v.defaultForSupplier && (
                                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                                          <Icon icon={Check} size="meta" tone="accent" />default
                                        </span>
                                      )}
                                    </td>
                                  </tr>
                                ))}
                                {isExpanded && (
                                  <tr>
                                    <td colSpan={5} style={{ ...sub, padding: '4px 10px 8px 28px', color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
                                      id: {item.id} | orderingUnit: {item.orderingUnitName} (×{item.orderingUnitRatio}) | tax: {item.globalSalesTaxSortOrder === 1 ? 'yes' : 'no'}
                                    </td>
                                  </tr>
                                )}
                              </React.Fragment>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </>
                );
              })()}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
