'use client';

/**
 * Working-document-backed "Receive Invoice" editor.
 *
 * The invoice analogue of PurchaseOrderEditor: it reads and PATCHes a Norm
 * working document (doc_type "received_invoice") so every edit persists — a
 * reload or the agent refining the draft both survive. It is dual-surface:
 *
 * - **Web** (the Invoices page inline-expand, and web chat): doc I/O over the
 *   session-auth working-documents REST; reference data from /invoice-fixes/*.
 * - **Claude** (embedded MCP App): the build swaps lib/api for the sandbox
 *   shim, which routes the /api/invoice-documents URL to norm__update_invoice_
 *   document (scope mcp:invoices:draft) and the receive POST to
 *   norm__receive_invoice. Reference data is pre-baked into the block by
 *   receive_display.py, and the PDF viewer degrades to "open in Norm".
 *
 * The "proper receive invoice" is the working document itself — shaped from the
 * real Loaded invoice (received/qty/cost/unit and the linked PO), with the
 * PDF-copy review cached onto it. This editor is the approval surface over it.
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch } from '../../lib/api';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface Line {
  id: string;
  code: string | null;
  description: string | null;
  brand?: string | null;
  unit: string | null;
  linked_unit_id: string | null;
  original_unit_id: string | null;
  unit_ratio: number | null;
  quantity_ordered?: number | null;
  quantity_received: number | null;
  unit_cost: number | null;
  total_cost: number | null;
  tax_amount?: number | null;
  sale_tax_rate?: number | null;
  linked_item_id: string | null;
  linked_brand_id?: string | null;
  // Reference cost from the linked PO (attached on open) — a red ↑ shows when
  // the invoice unit cost is higher.
  reference_cost?: number | null;
  // Matched to a line on the linked PO by itemId (Loaded's own reconciliation).
  on_order?: boolean | null;
  // The LINKED stock item's name — what Loaded's "Stock Item Description"
  // column shows for linked lines (resolved server-side at draft open). The raw
  // supplier description stays in `description` (engine matching, create
  // prefill). item_name_for = the linked_item_id the name was resolved for.
  item_name?: string | null;
  item_name_for?: string | null;
  // The code Loaded shows: the invoice line's own code, else the linked item's
  // code from the PO line (e.g. "[F311849]") when the line carries none.
  display_code?: string | null;
  // Set when this delivery came in under a DIFFERENT code than ordered (a
  // substitute): the original ordered PO line, shown as an expandable row.
  substitute_for?: OrderedNotReceived | null;
  // PDF-copy comparison, present once the review has run.
  copy_unit?: string | null;
  copy_quantity?: number | null;
  copy_unit_price?: number | null;
  copy_line_total?: number | null;
  // The line's unit disagreed with the copy (authoritative review result) —
  // shown even when the copy's unit isn't a derivable one to suggest.
  copy_unit_mismatch?: boolean | null;
  // Qty received disagreed with the copy (the review's decision) — the component
  // renders "use copy qty" from this; it does not decide the mismatch itself.
  copy_quantity_mismatch?: boolean | null;
  // The delivered unit the review derived from the copy per the venue's unit
  // guidelines (e.g. "CREAM FRESH 2L" → "2L"), NOT the raw packaging word.
  recommended_unit?: string | null;
  // The review's decision that this is a redundant $0 duplicate of another line
  // (same code, empty total). The component renders a "strike" affordance from
  // it; it does not decide the duplicate itself.
  copy_duplicate?: boolean | null;
  // Applied state: the user struck this line (via the affordance above). A struck
  // line renders struck-through, is left out of the totals, and is dropped from
  // the receive PUT (soft-deleted in Loaded).
  struck?: boolean | null;
  // Item-match suggestions for a NEW (unlinked) line, set by the review engine's
  // norm.match_stock_items LLM function and merged onto the draft by /review —
  // the component renders Link / Create from them, it never derives them.
  matched_item?: { id: string; name: string | null; group?: string | null; unit_id?: string | null; unit_cost?: number | null } | null;
  suggested_name?: string | null;
  suggested_group_id?: string | null;
}

interface DocData {
  invoice_id: string;
  reference_number: string | null;
  supplier_name: string | null;
  linked_supplier_id: string | null;
  purchase_order_number: string | null;
  linked_purchase_order_id: string | null;
  issued_at: string | null;
  due_at?: string | null;
  received_at?: string | null;
  order_date?: string | null;
  subtotal: number | null;
  tax_amount: number | null;
  discount_amount?: number | null;
  total: number | null;
  unit_cost_includes_tax?: boolean;
  notes?: string | null;
  file_id: string | null;
  is_received?: boolean;
  status?: string;
  lines: Line[];
  // PO lines whose stock code never appeared on the invoice — ordered, not
  // received. Read-only reference, never part of the receive payload.
  ordered_not_received?: OrderedNotReceived[];
  checks?: string;
  // Specific reasons the failed checks failed (e.g. a line-vs-copy mismatch).
  check_reasons?: string[];
  // Changes the review made/proposes (e.g. an auto-matched PO, a unit fix).
  suggestions?: Suggestion[];
  // Persisted record of suggestions the user APPLIED (unit/qty/strike): the ✓
  // rows in Suggested Changes survive line edits, re-reviews and re-opens, so
  // the user can always see what was actioned. Keyed stably per suggestion.
  actioned_suggestions?: { key: string; summary: string }[];
  reviewed_at?: string;
  _units?: Unit[];
  _purchase_orders?: PO[];
  _suppliers?: Supplier[];
}

interface OrderedNotReceived {
  code: string | null;
  description: string | null;
  unit: string | null;
  quantity_ordered: number | null;
  unit_cost: number | null;
}
interface StockGroup { id: string; name: string | null; category?: string | null }
interface Suggestion {
  id?: string;
  type?: string;
  summary?: string;
  po_number?: string;
  // link_po: the Loaded PO id the engine resolved — lets the Order Number
  // picker show the suggestion in place (pre-filled, marked suggested).
  purchase_order_id?: string | null;
  // quantity: the copy's qty for the line — Accept sets Qty received to it
  // (a local draft edit, applied on receive).
  proposed_quantity?: number | null;
  current_quantity?: number | null;
  proposed_unit?: string;
  already_linked_elsewhere?: boolean;
  line_id?: string;
  invoice_id?: string;
  linked_item_id?: string | null;
  linked_supplier_id?: string | null;
  line_code?: string | null;
}
interface Unit { id: string; name: string; type?: string; ratio?: number }
interface Supplier { id: string; name: string | null }
interface StockItem {
  id: string;
  name: string | null;
  code?: string | null;
  unit_id?: string | null;
  unit_name?: string | null;
  unit_ratio?: number | null;
  unit_cost?: number | null;
}
interface PO {
  id: string;
  order_number: string | null;
  supplier_name?: string | null;
  supplier_id?: string | null;
  invoiced?: boolean;
  linked_invoice_id?: string | null;
}

// Position of each check in the packed `checks` string — MUST mirror the order
// of CHECK_LABELS in config/consolidators/review_and_receive_invoices.py.
const CHECK_ORDER = [
  'credit_note', 'pdf_present', 'po_linked', 'po_supplier', 'items_matched',
  'totals', 'pdf_readable', 'pdf_invoice_number', 'pdf_lines', 'unit_of_measure',
  'pdf_total', 'duplicate',
];
const CHECK_LABEL: Record<string, string> = {
  credit_note: 'Document is an invoice (not a credit note or statement)',
  pdf_present: 'Invoice copy attached',
  po_linked: 'Linked to a purchase order',
  po_supplier: 'Supplier matches the purchase order',
  items_matched: 'Stock items, brands and units all exist in Loaded (no NEW)',
  totals: 'Invoice totals consistent',
  pdf_readable: 'Invoice copy readable',
  pdf_invoice_number: 'Invoice number matches the copy',
  pdf_lines: 'Lines match the invoice copy',
  unit_of_measure: 'Unit of measure matches the copy',
  pdf_total: 'Total matches the invoice copy',
  duplicate: 'Not a duplicate of an already-received invoice',
};
// How the checks are grouped and ordered for display (independent of the packed
// string order above). Each check reads its state from the packed string by key.
const CHECK_SECTIONS: { title: string; keys: string[] }[] = [
  { title: 'Loaded Invoice', keys: ['credit_note', 'duplicate', 'items_matched', 'totals'] },
  { title: 'Purchase Order', keys: ['po_linked', 'po_supplier'] },
  { title: 'Invoice Copy', keys: ['pdf_present', 'pdf_readable', 'pdf_invoice_number', 'pdf_lines', 'unit_of_measure', 'pdf_total'] },
];

const cur = (n: number | null | undefined) => `$${(n ?? 0).toFixed(2)}`;

// Per-line tax, honouring the "line item costs include tax" toggle: costs
// exclude tax → tax is added on top; costs include tax → tax is the portion
// already inside the line total.
function lineTax(lineTotal: number, rate: number | null | undefined, includesTax: boolean): number {
  const r = rate ?? 0;
  if (!r) return 0;
  return includesTax ? lineTotal - lineTotal / (1 + r) : lineTotal * r;
}

// How a line differs from the invoice copy (the review's per-line comparison) —
// so the "Lines match the invoice copy" failure is visible right on the row.
// Mirrors the consolidator's line check: quantity/unit-price exact, line total
// within a cent.
function copyDiffs(l: Line): string[] {
  // Quantity is handled separately as an actionable "use copy qty" edit on the
  // Qty received cell, so it is deliberately NOT listed here as a passive note.
  const out: string[] = [];
  if (l.copy_unit_price != null && Math.abs((l.unit_cost ?? 0) - l.copy_unit_price) > 0.005) {
    out.push(`unit cost ${cur(l.unit_cost)} vs copy ${cur(l.copy_unit_price)}`);
  }
  if (l.copy_line_total != null && Math.abs((l.quantity_received ?? 0) * (l.unit_cost ?? 0) - l.copy_line_total) > 0.011) {
    out.push(`line total ${cur((l.quantity_received ?? 0) * (l.unit_cost ?? 0))} vs copy ${cur(l.copy_line_total)}`);
  }
  return out;
}
const inputStyle: React.CSSProperties = {
  padding: '4px 8px', border: '1px solid #d1d5db', borderRadius: 4,
  fontSize: '0.8rem', fontFamily: 'inherit', boxSizing: 'border-box', outline: 'none',
};
const microLabel: React.CSSProperties = {
  fontSize: '0.6rem', color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.04em',
};
// A value with no linked id in Loaded — it would be created NEW on receive.
const newBadge: React.CSSProperties = {
  marginLeft: 6, fontSize: '0.56rem', fontWeight: 700, color: '#b45309',
  background: '#fff4e5', border: '1px solid #f0c88a', borderRadius: 4,
  padding: '1px 5px', whiteSpace: 'nowrap',
};
const fieldCol: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 3 };
// ISO string → the YYYY-MM-DD a <input type="date"> wants (and back on change).
const dateVal = (s: string | null | undefined) => (s ? String(s).slice(0, 10) : '');

// Unit-of-measure resolution — mirrors app/services/invoice_units.py (and the
// old InvoiceFixesCard) so a guideline-derived unit like "2L" resolves to the
// real Loaded unit "2 L" by MAGNITUDE, not just an exact name. A bare packaging
// word (bottle/bag/box) is deliberately not comparable.
const UOM_WORDS: Record<string, [string, number]> = {
  kg: ['weight', 1000], kgs: ['weight', 1000], kilo: ['weight', 1000], kilos: ['weight', 1000],
  kilogram: ['weight', 1000], kilograms: ['weight', 1000],
  g: ['weight', 1], gm: ['weight', 1], gr: ['weight', 1], gram: ['weight', 1], grams: ['weight', 1],
  l: ['volume', 1000], lt: ['volume', 1000], ltr: ['volume', 1000], litre: ['volume', 1000],
  liter: ['volume', 1000], litres: ['volume', 1000], liters: ['volume', 1000],
  ml: ['volume', 1], mls: ['volume', 1],
  ea: ['count', 1], each: ['count', 1], pc: ['count', 1], pcs: ['count', 1],
  piece: ['count', 1], pieces: ['count', 1], pack: ['count', 1], pk: ['count', 1],
  doz: ['count', 12], dozen: ['count', 12], dz: ['count', 12], pair: ['count', 2],
};
const UOM_VAGUE = new Set(['pkt', 'packet', 'box', 'carton', 'ctn', 'outer', 'unit', 'case', 'cs', 'bx', 'un', 'btl', 'bottle', 'bag']);

function parseUnit(text: string | null | undefined): [string, number] | null {
  const s = String(text ?? '').trim().toLowerCase();
  if (!s) return null;
  let num = '', word = '';
  for (const ch of s) {
    if ((ch >= '0' && ch <= '9') || (ch === '.' && num && !num.includes('.'))) {
      if (word) return null;
      num += ch;
    } else if (/[a-z]/.test(ch)) word += ch;
    else if (ch === ' ' || ch === '-') continue;
    else return null;
  }
  if (UOM_VAGUE.has(word)) return null;
  const entry = UOM_WORDS[word];
  if (!entry) return null;
  const [type, factor] = entry;
  if (!num) return [type, factor];
  const n = parseFloat(num);
  return Number.isNaN(n) ? null : [type, n * factor];
}

/** The Loaded unit best matching `name` — exact name first, then guideline
 *  magnitude equivalence. undefined if none is confident. */
function resolveUnit(name: string | null | undefined, units: Unit[]): Unit | undefined {
  if (!name) return undefined;
  const lc = name.trim().toLowerCase();
  const exact = units.find((u) => (u.name || '').toLowerCase() === lc);
  if (exact) return exact;
  const target = parseUnit(name);
  if (!target) return undefined;
  return units.find((u) => {
    const pu = parseUnit(u.name);
    return pu && pu[0] === target[0] && Math.abs(pu[1] - target[1]) < 0.001;
  });
}

export default function ReceiveInvoiceEditor({ data, props, threadId }: DisplayBlockProps) {
  const embedded = !!props?.embedded;
  const workingDocId = (data as Record<string, unknown>)?.working_document_id as string | undefined;
  const venueId = (props?.activeVenueId as string) || (data as Record<string, unknown>)?.venue_id as string | undefined;

  // Reference data: baked into the block when embedded, fetched on the web.
  // `lines` is coerced to an array up front: opened from the Invoices page the
  // block carries only { working_document_id }, so the draft (lines, totals,
  // invoice_id) only arrives once the fetch below resolves.
  const initial = data as unknown as DocData;
  const [doc, setDoc] = useState<DocData>(() => ({ ...initial, lines: initial.lines ?? [] }));
  const [version, setVersion] = useState<number>(1);
  const [units, setUnits] = useState<Unit[]>(initial._units || []);
  const [pos, setPos] = useState<PO[]>(initial._purchase_orders || []);
  const [suppliers, setSuppliers] = useState<Supplier[]>(initial._suppliers || []);
  const [stockItems, setStockItems] = useState<StockItem[]>([]);
  const [stockGroups, setStockGroups] = useState<StockGroup[]>([]);
  const [addQuery, setAddQuery] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [status, setStatus] = useState<'idle' | 'saving' | 'done' | 'error'>('idle');
  const [message, setMessage] = useState('');
  const [reviewing, setReviewing] = useState(false);
  const [accepting, setAccepting] = useState<string | null>(null);
  // Terminal state after accepting a delete_invoice suggestion (statement).
  const [deletedDraft, setDeletedDraft] = useState(false);
  // The NEW-stock-item line currently being created, and the form's group id.
  const [itemForm, setItemForm] = useState<{ lineId: string; name: string; groupId: string } | null>(null);
  const [creatingItem, setCreatingItem] = useState(false);
  const [linkingLine, setLinkingLine] = useState<string | null>(null);
  const [linkQuery, setLinkQuery] = useState('');
  // Substitute lines whose original ordered row is expanded.
  const [openSub, setOpenSub] = useState<Set<string>>(new Set());

  // Embedded uses a distinct URL so the sandbox routes it to the invoice-scoped
  // tools; the web uses the session-auth working-documents REST.
  const docBase = embedded ? 'invoice-documents' : 'working-documents';
  const docUrl = workingDocId
    ? (threadId ? `/api/threads/${threadId}/${docBase}/${workingDocId}` : `/api/${docBase}/${workingDocId}`)
    : null;

  // Load the draft (both surfaces) — gives the latest lines + the version.
  useEffect(() => {
    if (!docUrl) return;
    let live = true;
    apiFetch(docUrl)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!live || !d) return;
        setDoc((prev) => ({ ...prev, ...(d.data as DocData) }));
        if (typeof d.version === 'number') setVersion(d.version);
      })
      .catch(() => {});
    return () => { live = false; };
  }, [docUrl]);

  // Web-only reference reads (embedded gets them pre-baked in the block).
  useEffect(() => {
    if (embedded || !venueId) return;
    apiFetch(`/api/invoice-fixes/units?venue_id=${venueId}`)
      .then((r) => (r.ok ? r.json() : { units: [] }))
      .then((d) => setUnits(d.units || []))
      .catch(() => {});
    apiFetch(`/api/invoice-fixes/purchase-orders?venue_id=${venueId}`)
      .then((r) => (r.ok ? r.json() : { purchase_orders: [] }))
      .then((d) => setPos(d.purchase_orders || []))
      .catch(() => {});
    apiFetch(`/api/invoice-fixes/suppliers?venue_id=${venueId}`)
      .then((r) => (r.ok ? r.json() : { suppliers: [] }))
      .then((d) => setSuppliers(d.suppliers || []))
      .catch(() => {});
    apiFetch(`/api/invoice-fixes/stock-items?venue_id=${venueId}`)
      .then((r) => (r.ok ? r.json() : { stock_items: [] }))
      .then((d) => setStockItems(d.stock_items || []))
      .catch(() => {});
    apiFetch(`/api/invoice-fixes/stock-groups?venue_id=${venueId}`)
      .then((r) => (r.ok ? r.json() : { groups: [] }))
      .then((d) => setStockGroups(d.groups || []))
      .catch(() => {});
  }, [embedded, venueId]);

  // Item-match suggestions arrive WITH the review below — the engine
  // (review_and_receive_invoices) runs norm.match_stock_items itself and
  // /review merges matched_item/suggested_name/suggested_group_id onto the
  // lines. The component renders them; it never fetches them separately.

  // The cached PDF review — "checks after". Web-only (the endpoint is session-
  // auth); runs once the draft has loaded (needs its invoice_id), then the
  // draft carries `checks` and a re-open skips it.
  useEffect(() => {
    if (embedded || !venueId || !doc.invoice_id) return;
    // Re-review whenever there are no checks yet — not gated on reviewed_at, so
    // a draft reviewed before the single-invoice card fix (which recorded an
    // empty checklist) gets a real review on its next open. The endpoint caches
    // on a non-empty `checks`, so once populated this stops firing.
    if (doc.checks) return;
    setReviewing(true);
    apiFetch('/api/invoice-fixes/review', {
      method: 'POST',
      body: JSON.stringify({ venue_id: venueId, invoice_id: doc.invoice_id }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d?.data) {
          setDoc((prev) => ({ ...prev, ...(d.data as DocData) }));
          if (typeof d.version === 'number') setVersion(d.version);
        }
      })
      .catch(() => {})
      .finally(() => setReviewing(false));
    // Fires when the invoice_id is known AND whenever `checks` is cleared (e.g.
    // after accepting a suggested change) — the guard above stops a re-run once
    // the review returns.
  }, [embedded, venueId, doc.invoice_id, doc.checks]);

  const patchDoc = useCallback(
    async (ops: Record<string, unknown>[]) => {
      if (!docUrl) return;
      try {
        const res = await apiFetch(docUrl, {
          method: 'PATCH',
          body: JSON.stringify({ ops, version }),
        });
        if (res.ok) {
          const updated = await res.json();
          if (updated?.data) setDoc((prev) => ({ ...prev, ...(updated.data as DocData) }));
          if (typeof updated?.version === 'number') setVersion(updated.version);
        }
      } catch { /* keep local state; next patch retries with the stale version */ }
    },
    [docUrl, version],
  );

  const setLine = (idx: number, patch: Partial<Line>, ops: Record<string, unknown>[]) => {
    setDoc((prev) => {
      const lines = prev.lines.map((l, i) => (i === idx ? { ...l, ...patch } : l));
      return { ...prev, lines };
    });
    if (workingDocId) patchDoc(ops);
  };

  const onUnit = (idx: number, unitId: string) => {
    const u = units.find((x) => x.id === unitId);
    setLine(
      idx,
      { linked_unit_id: unitId, unit: u?.name ?? null, unit_ratio: u?.ratio ?? null },
      [{ op: 'update_line', index: idx, fields: { linked_unit_id: unitId, unit: u?.name, unit_ratio: u?.ratio } }],
    );
  };
  const onQty = (idx: number, qty: number) => {
    const cost = doc.lines[idx]?.unit_cost ?? 0;
    setLine(
      idx,
      { quantity_received: qty, total_cost: qty * cost },
      [{ op: 'update_line', index: idx, fields: { quantity_received: qty, total_cost: qty * cost } }],
    );
  };
  const onCost = (idx: number, cost: number) => {
    const qty = doc.lines[idx]?.quantity_received ?? 0;
    setLine(
      idx,
      { unit_cost: cost, total_cost: qty * cost },
      [{ op: 'update_line', index: idx, fields: { unit_cost: cost, total_cost: qty * cost } }],
    );
  };
  // Strike / un-strike a redundant $0 duplicate the review flagged. The review
  // decides (copy_duplicate); the user applies it here — a struck line stays on
  // screen struck-through, drops out of the totals, and is soft-deleted from the
  // receive PUT.
  const onStrike = (idx: number, struck: boolean) => {
    setLine(
      idx,
      { struck },
      [{ op: 'update_line', index: idx, fields: { struck } }],
    );
  };

  // ---- Suggestion apply paths -------------------------------------------
  // ONE handler per suggestion type, used by BOTH the inline line affordance
  // and the Suggested Changes list — the two surfaces can never diverge. Each
  // apply is a single PATCH (line edit + action-log entry together, so the doc
  // version can't race) and records itself in doc.actioned_suggestions, which
  // keeps a ✓ row visible even after the line has changed.
  const withActioned = (key: string, summary: string) =>
    [...(doc.actioned_suggestions || []).filter((a) => a.key !== key), { key, summary }];
  const applyUnitSuggestion = (idx: number, rec: Unit) => {
    const l = doc.lines[idx];
    if (!l) return;
    const log = withActioned(
      `unit:${l.id}`,
      `${l.display_code || l.code || '?'} · ${l.description ?? ''}: unit ${l.unit || '—'} → ${rec.name}`,
    );
    setDoc((prev) => ({
      ...prev,
      actioned_suggestions: log,
      lines: prev.lines.map((x, i) => (i === idx ? { ...x, linked_unit_id: rec.id, unit: rec.name, unit_ratio: rec.ratio ?? null } : x)),
    }));
    if (workingDocId) patchDoc([
      { op: 'update_line', index: idx, fields: { linked_unit_id: rec.id, unit: rec.name, unit_ratio: rec.ratio ?? null } },
      { op: 'update_header', fields: { actioned_suggestions: log } },
    ]);
  };
  const applyQtySuggestion = (idx: number) => {
    const l = doc.lines[idx];
    if (!l || l.copy_quantity == null) return;
    const qty = l.copy_quantity;
    const cost = l.unit_cost ?? 0;
    const log = withActioned(
      `qty:${l.id}`,
      `${l.display_code || l.code || '?'} · ${l.description ?? ''}: Qty received ${l.quantity_received ?? 0} → ${qty}`,
    );
    setDoc((prev) => ({
      ...prev,
      actioned_suggestions: log,
      lines: prev.lines.map((x, i) => (i === idx ? { ...x, quantity_received: qty, total_cost: qty * cost } : x)),
    }));
    if (workingDocId) patchDoc([
      { op: 'update_line', index: idx, fields: { quantity_received: qty, total_cost: qty * cost } },
      { op: 'update_header', fields: { actioned_suggestions: log } },
    ]);
  };
  const applyStrikeSuggestion = (idx: number) => {
    const l = doc.lines[idx];
    if (!l) return;
    const log = withActioned(
      `strike:${l.id}`,
      `${l.display_code || l.code || '?'} · ${l.description ?? ''}: $0 duplicate struck (excluded from receive)`,
    );
    setDoc((prev) => ({
      ...prev,
      actioned_suggestions: log,
      lines: prev.lines.map((x, i) => (i === idx ? { ...x, struck: true } : x)),
    }));
    if (workingDocId) patchDoc([
      { op: 'update_line', index: idx, fields: { struck: true } },
      { op: 'update_header', fields: { actioned_suggestions: log } },
    ]);
  };
  const undoStrikeSuggestion = (idx: number) => {
    const l = doc.lines[idx];
    if (!l) return;
    const log = (doc.actioned_suggestions || []).filter((a) => a.key !== `strike:${l.id}`);
    setDoc((prev) => ({
      ...prev,
      actioned_suggestions: log,
      lines: prev.lines.map((x, i) => (i === idx ? { ...x, struck: false } : x)),
    }));
    if (workingDocId) patchDoc([
      { op: 'update_line', index: idx, fields: { struck: false } },
      { op: 'update_header', fields: { actioned_suggestions: log } },
    ]);
  };
  const onPo = (poId: string) => {
    const po = pos.find((p) => p.id === poId);
    setDoc((prev) => ({
      ...prev,
      linked_purchase_order_id: poId || null,
      purchase_order_number: po?.order_number ?? prev.purchase_order_number,
    }));
    if (workingDocId) {
      patchDoc([{ op: 'update_header', fields: { linked_purchase_order_id: poId || null, purchase_order_number: po?.order_number } }]);
    }
  };

  // Header field edits (invoice #, dates, total, supplier, tax toggle) — merge
  // locally and persist via the update_header op.
  const patchHeader = (fields: Partial<DocData>) => {
    setDoc((prev) => ({ ...prev, ...fields }));
    if (workingDocId) patchDoc([{ op: 'update_header', fields }]);
  };
  const onSupplier = (id: string) => {
    const s = suppliers.find((x) => x.id === id);
    patchHeader({ linked_supplier_id: id || null, supplier_name: s?.name ?? null });
  };

  // Notes persist on a debounce so a keystroke isn't a PATCH each.
  const notesTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onNotes = (value: string) => {
    setDoc((prev) => ({ ...prev, notes: value }));
    if (!workingDocId) return;
    if (notesTimer.current) clearTimeout(notesTimer.current);
    notesTimer.current = setTimeout(
      () => patchDoc([{ op: 'update_notes', value }]),
      500,
    );
  };

  // Add Item — append a new line from the stock catalogue. A temporary id lets
  // update_line/remove_line address it locally; do_receive drops it and Loaded
  // assigns the real id on receive.
  const addItem = (item: StockItem) => {
    const u = units.find((x) => x.id === item.unit_id);
    const fields = {
      id: `new-${Date.now()}`,
      code: item.code ?? null,
      description: item.name ?? null,
      brand: null,
      unit: u?.name ?? item.unit_name ?? null,
      linked_unit_id: item.unit_id ?? null,
      unit_ratio: u?.ratio ?? item.unit_ratio ?? 1,
      quantity_ordered: null,
      quantity_received: 1,
      unit_cost: item.unit_cost ?? 0,
      total_cost: item.unit_cost ?? 0,
      linked_item_id: item.id,
    };
    setDoc((prev) => ({ ...prev, lines: [...prev.lines, fields as Line] }));
    if (workingDocId) patchDoc([{ op: 'add_line', fields }]);
    setAddQuery('');
    setAddOpen(false);
  };

  const candidatePos = useMemo(
    () => pos.filter((p) => !p.supplier_id || p.supplier_id === doc.linked_supplier_id),
    [pos, doc.linked_supplier_id],
  );
  const sortedUnits = useMemo(
    () => [...units].sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { numeric: true })),
    [units],
  );

  // The Loaded unit the review recommends for each line, resolved from the
  // guideline-derived `recommended_unit` (e.g. "2L" → the "2 L" unit). Only a
  // recommendation that resolves AND differs from the line's current unit is a
  // real suggestion — a 2L bottle already on "2 L" is not flagged.
  const recommendedFor = useMemo(() => {
    const m: Record<string, Unit | undefined> = {};
    for (const l of doc.lines) {
      const rec = l.recommended_unit ? resolveUnit(l.recommended_unit, units) : undefined;
      m[l.id] = rec && rec.id !== l.linked_unit_id ? rec : undefined;
    }
    return m;
  }, [doc.lines, units]);

  const includesTax = !!doc.unit_cost_includes_tax;
  const totals = useMemo(() => {
    let excl = 0;
    let tax = 0;
    for (const l of doc.lines) {
      if (l.struck) continue; // struck lines are dropped from the receive
      const lt = (l.quantity_received ?? 0) * (l.unit_cost ?? 0);
      const t = lineTax(lt, l.sale_tax_rate, includesTax);
      tax += t;
      excl += includesTax ? lt - t : lt;
    }
    const discount = doc.discount_amount ?? 0;
    return { excl, tax, discount, incl: excl + tax - discount };
  }, [doc.lines, includesTax, doc.discount_amount]);
  // The header's stated Invoice Total vs what the lines add up to.
  const totalMismatch =
    doc.total != null && Math.abs((doc.total ?? 0) - totals.incl) > 0.01;

  const filteredStock = useMemo(() => {
    const q = addQuery.trim().toLowerCase();
    if (!q) return [] as StockItem[];
    return stockItems
      .filter((i) => (i.name || '').toLowerCase().includes(q) || (i.code || '').toLowerCase().includes(q))
      .slice(0, 20);
  }, [addQuery, stockItems]);

  // Manual "search & link an existing item" inside the create form — the fallback
  // when the LLM's single suggestion is wrong but the product does already exist.
  const linkMatches = useMemo(() => {
    const q = linkQuery.trim().toLowerCase();
    if (!q) return [] as StockItem[];
    return stockItems
      .filter((i) => (i.name || '').toLowerCase().includes(q) || (i.code || '').toLowerCase().includes(q))
      .slice(0, 8);
  }, [linkQuery, stockItems]);

  const checks = useMemo(() => {
    if (!doc.checks) return [] as { key: string; label: string; state: string }[];
    const decode = (ch: string) =>
      ch === 'p' ? 'pass' : ch === 'f' ? 'fail' : ch === 's' ? 'suggest' : 'skip';
    // Emit in section/display order (not packed order), reading each state from
    // the packed string by the check's fixed position.
    return CHECK_SECTIONS.flatMap((sec) =>
      sec.keys.map((key) => ({
        key,
        label: CHECK_LABEL[key],
        state: decode(doc.checks![CHECK_ORDER.indexOf(key)]),
      })),
    );
  }, [doc.checks]);
  const checkByKey = useMemo(
    () => Object.fromEntries(checks.map((c) => [c.key, c])) as Record<string, { key: string; label: string; state: string }>,
    [checks],
  );
  const done = status === 'done' || !!doc.is_received;
  // Lines still pointing at a NEW (uncreated) stock item or unit — receiving is
  // blocked until each is explicitly created in Loaded.
  const unresolved = useMemo(
    () => doc.lines.filter((l) => !l.linked_item_id || !l.linked_unit_id),
    [doc.lines],
  );
  // The engine's "no NEW values" check is a snapshot of Loaded at review time.
  // The user's LOCAL edits (picking an existing unit, linking an item) resolve
  // it without any write until receive — reconcile the DISPLAY so it's clear
  // nothing NEW will be created. Purely presentational: the engine's cached
  // artifact is untouched, and the receive guard still runs off `unresolved`.
  const newValuesResolvedByEdits =
    !done && unresolved.length === 0 && checkByKey.items_matched?.state === 'fail';
  const checkSummary = useMemo(() => {
    if (checks.length === 0) return reviewing ? 'checking against the copy…' : 'not yet reviewed';
    let fail = checks.filter((c) => c.state === 'fail').length;
    const suggest = checks.filter((c) => c.state === 'suggest').length;
    const skip = checks.filter((c) => c.state === 'skip').length;
    const resolved = newValuesResolvedByEdits ? 1 : 0;
    fail -= resolved;
    if (!fail && !skip && !suggest && !resolved) return 'all checks pass';
    return `${checks.filter((c) => c.state === 'pass').length} passed`
      + `${fail ? ` · ${fail} failed` : ''}`
      + `${suggest ? ` · ${suggest} suggested` : ''}`
      + `${resolved ? ' · 1 resolved by your edits' : ''}`
      + `${skip ? ` · ${skip} not reached` : ''}`;
  }, [checks, reviewing, newValuesResolvedByEdits]);
  const failChecks = useMemo(
    () => checks.filter((c) => c.state === 'fail' && !(c.key === 'items_matched' && newValuesResolvedByEdits)),
    [checks, newValuesResolvedByEdits],
  );

  // The engine's link_po suggestion, shown IN the Order Number picker as a
  // suggested value (strict mirror: the doc's own linked_purchase_order_id
  // stays exactly as Loaded has it until accepted/received). A split PO
  // (already invoiced elsewhere) is informational only — never offered.
  const suggestedPo =
    !done && !doc.linked_purchase_order_id
      ? (doc.suggestions || []).find(
          (s) => s.type === 'link_po' && s.purchase_order_id && s.already_linked_elsewhere !== true,
        )
      : undefined;

  // ---- Suggested Changes: ONE derivation --------------------------------
  // The list is computed from the SAME per-line engine fields that drive the
  // inline affordances (recommended_unit, copy_quantity_mismatch,
  // copy_duplicate, matched_item), so an inline suggestion can never be
  // missing from the list. Engine entries are used only for server-write /
  // informational types (link_po, split-PO). Applied entries come from the
  // persisted action log and stay visible (✓, struck-through) even after the
  // lines they touched were edited, re-reviewed or re-opened.
  type SuggestionRow = {
    key: string;
    summary: string;
    state: 'pending' | 'applied';
    accept?: () => void;
    undo?: () => void;
    engineFix?: Suggestion; // link_po rows accept via the server
  };
  const suggestionRows: SuggestionRow[] = (() => {
    const rows: SuggestionRow[] = [];
    const log = doc.actioned_suggestions || [];
    const logged = new Set(log.map((a) => a.key));
    for (const s of doc.suggestions || []) {
      if (s.type === 'link_po' && !doc.linked_purchase_order_id) {
        rows.push({
          key: `po:${s.purchase_order_id || s.po_number || ''}`,
          summary: s.summary || `Link purchase order ${s.po_number ?? ''}`,
          state: 'pending',
          engineFix: s.already_linked_elsewhere === true ? undefined : s,
        });
      } else if (s.type === 'delete_invoice') {
        // The document is a statement (not an invoice): Accept deletes the
        // draft from Loaded (server applier, verified DELETE endpoint).
        rows.push({
          key: 'delete_invoice',
          summary: s.summary || 'This document is a supplier statement, not an invoice — delete this draft in Loaded',
          state: 'pending',
          engineFix: s,
        });
      }
    }
    doc.lines.forEach((l, idx) => {
      const code = l.display_code || l.code || '?';
      const rec = recommendedFor[l.id];
      if (rec && !logged.has(`unit:${l.id}`)) {
        rows.push({
          key: `unit:${l.id}`,
          summary: `${code} · ${l.description ?? ''}: unit ${l.unit || '—'} → ${rec.name} (per the invoice copy)`,
          state: 'pending',
          accept: () => applyUnitSuggestion(idx, rec),
        });
      }
      const qtyPending = l.copy_quantity_mismatch && l.copy_quantity != null
        && Math.abs((l.quantity_received ?? 0) - l.copy_quantity) > 0.001;
      if (qtyPending && !logged.has(`qty:${l.id}`)) {
        rows.push({
          key: `qty:${l.id}`,
          summary: `${code} · ${l.description ?? ''}: Qty received ${l.quantity_received ?? 0} → ${l.copy_quantity} (per the invoice copy)`,
          state: 'pending',
          accept: () => applyQtySuggestion(idx),
        });
      }
      if (l.copy_duplicate && !l.struck && !logged.has(`strike:${l.id}`)) {
        rows.push({
          key: `strike:${l.id}`,
          summary: `${code} · ${l.description ?? ''}: $0 duplicate line — strike it (excluded from receive)`,
          state: 'pending',
          accept: () => applyStrikeSuggestion(idx),
        });
      }
      const matched = l.matched_item;
      if (!l.linked_item_id && matched?.id) {
        rows.push({
          key: `item:${l.id}`,
          summary: `${code} · ${l.description ?? ''}: link to existing '${matched.name ?? ''}'`,
          state: 'pending',
          accept: () => linkItem(l.id, matched.id),
        });
      }
    });
    for (const a of log) {
      rows.push({
        key: `done:${a.key}`,
        summary: a.summary,
        state: 'applied',
        undo: a.key.startsWith('strike:')
          ? () => {
              const li = doc.lines.findIndex((l) => `strike:${l.id}` === a.key);
              if (li >= 0) undoStrikeSuggestion(li);
            }
          : undefined,
      });
    }
    return rows;
  })();
  const sortedGroups = useMemo(
    () => [...stockGroups].sort((a, b) => (a.name || '').localeCompare(b.name || '')),
    [stockGroups],
  );

  const openCopy = async () => {
    if (embedded || !venueId) return;
    try {
      const r = await apiFetch(`/api/invoice-fixes/file?venue_id=${venueId}&invoice_id=${doc.invoice_id}`);
      if (!r.ok) throw new Error(r.status === 404 ? 'No copy attached' : `Error ${r.status}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener');
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Could not open copy');
      setStatus('error');
    }
  };

  // Accept ONE server-write suggestion (link_po): write it to Loaded (without
  // receiving) and refresh the draft. Local suggestion types (unit/qty/strike)
  // never come here — they apply through applyXSuggestion above.
  const acceptFix = async (key: string, fix: Suggestion) => {
    if (embedded || !venueId) return;
    setAccepting(key);
    setMessage('');
    try {
      const res = await apiFetch('/api/invoice-fixes/accept', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, invoice_id: doc.invoice_id, fix }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
      }
      const out = await res.json();
      if (out?.deleted) {
        // The draft was deleted from Loaded (a statement) — terminal state.
        setDeletedDraft(true);
        setStatus('done');
        return;
      }
      if (out?.document?.data) {
        setDoc((prev) => ({ ...prev, ...(out.document.data as DocData), lines: (out.document.data.lines ?? prev.lines) as Line[] }));
        if (typeof out.document.version === 'number') setVersion(out.document.version);
      }
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Could not apply the change');
    } finally {
      setAccepting(null);
    }
  };

  // Explicitly create a NEW stock item (+ its supplier variant) in Loaded and
  // link the line — a deliberate, controlled action, never silent on receive.
  const createItem = async () => {
    if (!itemForm || !venueId || embedded) return;
    const line = doc.lines.find((l) => l.id === itemForm.lineId);
    if (!line?.linked_unit_id) {
      setStatus('error');
      setMessage('Resolve this line’s unit before creating the stock item.');
      return;
    }
    setCreatingItem(true);
    setMessage('');
    try {
      const res = await apiFetch('/api/invoice-fixes/create-item', {
        method: 'POST',
        body: JSON.stringify({
          venue_id: venueId,
          invoice_id: doc.invoice_id,
          line_id: itemForm.lineId,
          name: itemForm.name,
          group_id: itemForm.groupId,
          unit_id: line.linked_unit_id,
          brand_id: line.linked_brand_id ?? null,
        }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
      }
      const out = await res.json();
      if (out?.document?.data) {
        setDoc((prev) => ({ ...prev, ...(out.document.data as DocData), lines: (out.document.data.lines ?? prev.lines) as Line[] }));
        if (typeof out.document.version === 'number') setVersion(out.document.version);
      }
      setItemForm(null);
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Could not create the stock item');
    } finally {
      setCreatingItem(false);
    }
  };

  // Link the line to an EXISTING Loaded item (the LLM's suggested match, or one the
  // user searched for) — registers the supplier variant so future invoices match.
  const linkItem = async (lineId: string, itemId: string) => {
    if (!venueId || embedded) return;
    setLinkingLine(lineId);
    setMessage('');
    try {
      const res = await apiFetch('/api/invoice-fixes/link-item', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, invoice_id: doc.invoice_id, line_id: lineId, item_id: itemId }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
      }
      const out = await res.json();
      if (out?.document?.data) {
        setDoc((prev) => ({ ...prev, ...(out.document.data as DocData), lines: (out.document.data.lines ?? prev.lines) as Line[] }));
        if (typeof out.document.version === 'number') setVersion(out.document.version);
      }
      setItemForm(null);
      setLinkQuery('');
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Could not link the stock item');
    } finally {
      setLinkingLine(null);
    }
  };

  // Open the create-item form for a line, prefilled from the review's suggestion.
  const openItemForm = (l: Line) => {
    setItemForm(
      itemForm?.lineId === l.id
        ? null
        : { lineId: l.id, name: l.suggested_name || l.description || l.code || '', groupId: l.suggested_group_id || '' },
    );
    setLinkQuery('');
  };

  const accept = async () => {
    setStatus('saving');
    setMessage('');
    try {
      const lines = doc.lines.map((l) => ({
        id: l.id,
        // code/description/linked_item_id let do_receive APPEND lines the user
        // added (Add Item) — a line whose id is not on the Loaded invoice.
        code: l.code,
        description: l.description,
        linked_item_id: l.linked_item_id,
        unit: l.unit,
        linked_unit_id: l.linked_unit_id,
        unit_ratio: l.unit_ratio,
        quantity_received: l.quantity_received,
        unit_cost: l.unit_cost,
        total_cost: Number(((l.quantity_received ?? 0) * (l.unit_cost ?? 0)).toFixed(4)),
        // struck lines are soft-deleted in Loaded by do_receive (a redundant $0
        // duplicate), so they never enter the received invoice.
        struck: l.struck ?? false,
      }));
      const variant_updates = doc.lines
        .filter((l) => l.code && l.linked_unit_id && l.linked_unit_id !== l.original_unit_id)
        .map((l) => ({ linked_item_id: l.linked_item_id, line_code: l.code, unit_id: l.linked_unit_id }));
      const res = await apiFetch('/api/invoice-fixes/receive', {
        method: 'POST',
        body: JSON.stringify({
          venue_id: venueId,
          invoice_id: doc.invoice_id,
          // Loaded's own link, else the engine's suggested PO shown in the
          // picker — receiving is the approval that writes the link.
          linked_purchase_order_id: doc.linked_purchase_order_id ?? suggestedPo?.purchase_order_id ?? null,
          po_number: null,
          lines,
          variant_updates,
          // Editable header fields — sent so a change persists to Loaded.
          reference_number: doc.reference_number,
          issued_at: doc.issued_at,
          due_at: doc.due_at ?? null,
          received_at: doc.received_at ?? null,
          total: doc.total,
          linked_supplier_id: doc.linked_supplier_id,
          unit_cost_includes_tax: !!doc.unit_cost_includes_tax,
          notes: doc.notes ?? '',
          receive: true,
        }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
      }
      const body = await res.json().catch(() => ({}));
      if (body && body.submitted === false) throw new Error(String(body.detail ?? 'Could not receive'));
      setStatus('done');
      setMessage('Received');
      if (workingDocId) patchDoc([{ op: 'set_status', value: 'received' }]);
      (props?.onReceived as ((id: string) => void) | undefined)?.(doc.invoice_id);
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Failed');
    }
  };

  // The block carries only a working_document_id until the draft loads; show a
  // loading state rather than an empty card in that gap.
  if (docUrl && !doc.invoice_id) {
    return (
      <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, background: '#fff', padding: '1rem', fontSize: '0.8rem', color: '#888' }}>
        Opening the invoice…
      </div>
    );
  }

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,0.04)', overflow: 'hidden' }}>
      {/* Header — editable form (Loaded-parity) */}
      <div style={{ padding: '0.7rem 0.9rem', background: 'linear-gradient(#faf9f7,#f5f3ef)', borderBottom: '1px solid #eee' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
          <span style={{ fontSize: '0.9rem', fontWeight: 700, color: '#3a3a3a' }}>Receive Invoice</span>
          {!embedded && doc.file_id && (
            <button type="button" onClick={openCopy} title="View invoice copy" aria-label="View invoice copy"
              style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 22, height: 22, padding: 0, border: '1px solid #d8d4cc', borderRadius: 4, background: '#fff', color: '#6b6b6b', cursor: 'pointer' }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
            </button>
          )}
        </div>
        {/* Two columns like Loaded: order/supplier on the left, invoice on the
            right. Collapses to one column when the card is narrow. */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '0.5rem 2rem' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label style={fieldCol}>
              <span style={microLabel}>Supplier</span>
              <select value={doc.linked_supplier_id || ''} disabled={done} onChange={(e) => onSupplier(e.target.value)} style={{ ...inputStyle, width: '100%' }}>
                {!suppliers.some((s) => s.id === doc.linked_supplier_id) && (
                  <option value={doc.linked_supplier_id || ''}>{doc.supplier_name || 'Select supplier'}</option>
                )}
                {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
            <label style={fieldCol}>
              <span style={microLabel}>Order Number</span>
              {/* Shows the engine's link_po suggestion IN PLACE when Loaded has
                  no linked PO: pre-filled + amber "suggested". Picking another
                  option overrides it; Accept (below) links it now; otherwise it
                  links on receive (the receive body falls back to it). */}
              <select value={doc.linked_purchase_order_id || suggestedPo?.purchase_order_id || ''} disabled={done} onChange={(e) => onPo(e.target.value)}
                title={suggestedPo
                  ? `Suggested — Norm matched purchase order ${suggestedPo.po_number ?? ''} from the invoice. Accept it below to link now, or it links when you receive.`
                  : undefined}
                style={{ ...inputStyle, width: '100%', ...(suggestedPo ? { border: '1px solid #b78a2f', background: '#fdf6e7' } : {}) }}>
                <option value="">Not linked</option>
                {/* A linked/suggested PO may be an older, already-received order
                    that isn't in the open-PO picker list — keep it shown as the
                    current value rather than reading as "Not linked". */}
                {doc.linked_purchase_order_id && !candidatePos.some((p) => p.id === doc.linked_purchase_order_id) && (
                  <option value={doc.linked_purchase_order_id}>{doc.purchase_order_number || '(linked)'}</option>
                )}
                {!doc.linked_purchase_order_id && suggestedPo?.purchase_order_id && !candidatePos.some((p) => p.id === suggestedPo.purchase_order_id) && (
                  <option value={suggestedPo.purchase_order_id}>{suggestedPo.po_number || '(suggested)'}</option>
                )}
                {candidatePos.map((p) => (
                  <option key={p.id} value={p.id}>{(p.order_number || '(linked)')}{p.supplier_name ? ` — ${p.supplier_name}` : ''}</option>
                ))}
              </select>
              {suggestedPo && (
                <span style={{ fontSize: '0.6rem', color: '#8a6d3b', marginTop: 2 }}>
                  suggested — not linked in Loaded yet; links on Accept or receive
                </span>
              )}
            </label>
            {doc.order_date && (
              <label style={fieldCol}>
                <span style={microLabel}>Order Date</span>
                <span style={{ fontSize: '0.8rem', color: '#555', padding: '4px 0' }}>{dateVal(doc.order_date)}</span>
              </label>
            )}
            <label style={fieldCol}>
              <span style={microLabel}>Received Date</span>
              <input type="date" value={dateVal(doc.received_at)} disabled={done}
                onChange={(e) => patchHeader({ received_at: e.target.value || null })} style={{ ...inputStyle, width: '100%' }} />
            </label>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label style={fieldCol}>
              <span style={microLabel}>Invoice Number</span>
              <input type="text" value={doc.reference_number || ''} disabled={done}
                onChange={(e) => patchHeader({ reference_number: e.target.value })} style={{ ...inputStyle, width: '100%' }} />
            </label>
            <label style={fieldCol}>
              <span style={microLabel}>Invoice Date</span>
              <input type="date" value={dateVal(doc.issued_at)} disabled={done}
                onChange={(e) => patchHeader({ issued_at: e.target.value || null })} style={{ ...inputStyle, width: '100%' }} />
            </label>
            <label style={fieldCol}>
              <span style={microLabel}>Due Date</span>
              <input type="date" value={dateVal(doc.due_at)} disabled={done}
                onChange={(e) => patchHeader({ due_at: e.target.value || null })} style={{ ...inputStyle, width: '100%' }} />
            </label>
            <label style={fieldCol}>
              <span style={microLabel}>Invoice Total</span>
              <input type="number" step="any" value={doc.total ?? 0} disabled={done}
                onChange={(e) => patchHeader({ total: parseFloat(e.target.value) || 0 })}
                style={{ ...inputStyle, width: '100%', fontWeight: 600 }} />
            </label>
          </div>
        </div>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: '0.5rem', fontSize: '0.72rem', color: '#555' }}>
          <input type="checkbox" checked={includesTax} disabled={done}
            onChange={(e) => patchHeader({ unit_cost_includes_tax: e.target.checked })} />
          Line item costs include tax
        </label>
      </div>

      {/* Lines */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: '#9ca3af', fontSize: '0.62rem', textTransform: 'uppercase' }}>
              <th style={{ padding: '0.4rem 0.6rem' }}>Code</th>
              <th style={{ padding: '0.4rem 0.6rem' }}>Description</th>
              <th style={{ padding: '0.4rem 0.6rem' }}>Brand</th>
              <th style={{ padding: '0.4rem 0.6rem' }}>Unit</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Qty ordered</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Qty received</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Unit cost</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Tax</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Total cost</th>
            </tr>
          </thead>
          <tbody>
            {doc.lines.map((l, idx) => {
              const recommended = recommendedFor[l.id];
              // An ACCEPTED strike removes the row from view entirely (it is
              // excluded from totals and soft-deleted on receive); undo lives on
              // the suggestion row below the table.
              if (l.struck) return null;
              return (
              <Fragment key={l.id}>
              {/* A SUGGESTED strike (copy_duplicate) renders the row struck-
                  through — the strikethrough IS the suggestion preview; Accept
                  (badge or the suggestion row) removes the row. */}
              <tr style={{ borderTop: '1px solid #f3f3f3', ...(l.copy_duplicate ? { opacity: 0.6, textDecoration: 'line-through', background: '#fffdf5' } : {}) }}>
                <td style={{ padding: '0.4rem 0.6rem', color: '#666' }}>{l.display_code || l.code || '—'}</td>
                <td style={{ padding: '0.4rem 0.6rem' }}>
                  {l.item_name || l.description}
                  {!done && l.copy_duplicate && (
                    <button type="button"
                      onClick={() => applyStrikeSuggestion(idx)}
                      title="$0 duplicate of another line — accept to remove it from the receive"
                      style={{ marginLeft: 6, fontSize: '0.58rem', color: '#c0392b', background: '#fdecea', border: '1px solid #f5c6c0', borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap', fontFamily: 'inherit', fontWeight: 600, cursor: 'pointer', textDecoration: 'none' }}>
                      $0 duplicate — accept strike
                    </button>
                  )}
                  {/* Delivered under a different code than ordered — a substitute.
                      Click the badge to expand the original ordered line below. */}
                  {l.substitute_for && (
                    <button type="button"
                      onClick={() => setOpenSub((prev) => { const n = new Set(prev); if (n.has(l.id)) n.delete(l.id); else n.add(l.id); return n; })}
                      title="delivered under a different stock code than ordered — click to show the ordered line"
                      style={{ marginLeft: 6, fontSize: '0.58rem', color: '#8a6d3b', background: '#fdf6e7', border: '1px solid #e6d3a3', borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap', fontFamily: 'inherit', fontWeight: 600, cursor: 'pointer' }}>
                      substitute {openSub.has(l.id) ? '▾' : '▸'}
                    </button>
                  )}
                  {/* Differs from the invoice copy — explains the "Lines match
                      the invoice copy" failure, right on the line. Suppressed
                      while a QUANTITY suggestion is pending: the cost/total
                      diffs are then consequences of the qty the suggestion
                      fixes, and the "copy: N — use" affordance is the signal.
                      Once the qty is corrected, only genuine residual diffs
                      (recomputed from the live values) still badge. */}
                  {(() => {
                    const qtyPending = !done && l.copy_quantity_mismatch && l.copy_quantity != null
                      && Math.abs((l.quantity_received ?? 0) - l.copy_quantity) > 0.001;
                    if (qtyPending) return null;
                    const diffs = copyDiffs(l);
                    return diffs.length ? (
                      <span title={`differs from the invoice copy — ${diffs.join('; ')}`}
                        style={{ marginLeft: 6, fontSize: '0.58rem', color: '#c0392b', background: '#fdecea', border: '1px solid #f5c6c0', borderRadius: 4, padding: '1px 5px', whiteSpace: 'nowrap' }}>
                        ≠ copy
                      </span>
                    ) : null;
                  })()}
                  {/* Stock item not linked in Loaded. The review engine's item
                      match rides in on the line (matched_item / suggested_name /
                      suggested_group_id): offer an existing item to LINK, else
                      CREATE it (with its group). Must be resolved to receive. */}
                  {!l.linked_item_id && !embedded && !done && (() => {
                    if (itemForm?.lineId === l.id) return null; // the form below is open
                    const matched = l.matched_item;
                    if (reviewing && !matched && !l.suggested_name) {
                      return <span style={{ ...newBadge, background: '#eef1f8', color: '#556', border: '1px solid #ccd3e6' }}>NEW item — checking Loaded…</span>;
                    }
                    if (matched) {
                      return (
                        <span style={{ display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: 5 }}>
                          <span title="Norm found a likely existing item — Link to use it, or create a new one" style={{ fontSize: '0.6rem', color: '#2d6a4f', background: '#e7f5ec', border: '1px solid #b7e0c6', borderRadius: 4, padding: '1px 6px' }}>
                            match: {matched.name}{matched.group ? ` · ${matched.group}` : ''}
                          </span>
                          <button type="button" onClick={() => linkItem(l.id, matched.id)} disabled={linkingLine === l.id}
                            style={{ fontSize: '0.62rem', padding: '2px 9px', border: '1px solid #2d6a4f', borderRadius: 4, background: linkingLine === l.id ? '#dfeee6' : '#fff', color: '#2d6a4f', cursor: linkingLine === l.id ? 'wait' : 'pointer', fontWeight: 700 }}>
                            {linkingLine === l.id ? 'Linking…' : 'Link'}
                          </button>
                          <button type="button" onClick={() => openItemForm(l)}
                            style={{ fontSize: '0.6rem', padding: '2px 8px', border: '1px solid #ddd', borderRadius: 4, background: '#fff', color: '#888', cursor: 'pointer' }}>
                            Create new instead
                          </button>
                        </span>
                      );
                    }
                    return (
                      <button type="button" onClick={() => openItemForm(l)}
                        title="this stock item isn't linked in Loaded — link an existing item or create it before receiving"
                        style={{ ...newBadge, cursor: 'pointer', font: 'inherit', fontWeight: 700 }}>
                        NEW item — create
                      </button>
                    );
                  })()}
                  {!l.linked_item_id && (embedded || done) && (
                    <span title="this stock item doesn't exist in Loaded" style={newBadge}>NEW item</span>
                  )}
                  {itemForm?.lineId === l.id && (
                    <div style={{ marginTop: 5, padding: 7, border: '1px solid #f0c88a', background: '#fff9f0', borderRadius: 5, display: 'flex', flexDirection: 'column', gap: 5, maxWidth: 340 }}>
                      <div style={{ ...microLabel, color: '#8a6d3b' }}>Create stock item in Loaded</div>
                      <input type="text" value={itemForm.name} placeholder="Item name"
                        onChange={(e) => setItemForm({ ...itemForm, name: e.target.value })}
                        style={{ ...inputStyle, fontSize: '0.72rem', width: '100%' }} />
                      <select value={itemForm.groupId} onChange={(e) => setItemForm({ ...itemForm, groupId: e.target.value })}
                        style={{ ...inputStyle, fontSize: '0.72rem', width: '100%' }}>
                        <option value="">Select stock group…</option>
                        {sortedGroups.map((g) => <option key={g.id} value={g.id}>{g.name}{g.category ? ` · ${g.category}` : ''}</option>)}
                      </select>
                      {!l.linked_unit_id && (
                        <div style={{ fontSize: '0.58rem', color: '#c0392b' }}>Resolve this line’s unit first (in the Unit column).</div>
                      )}
                      {/* Fallback: the product may already exist under a different
                          name — search and link it instead of creating a duplicate. */}
                      <div style={{ borderTop: '1px dashed #e6d3ad', paddingTop: 5 }}>
                        <input type="text" value={linkQuery} placeholder="Already in Loaded? search to link…"
                          onChange={(e) => setLinkQuery(e.target.value)}
                          style={{ ...inputStyle, fontSize: '0.7rem', width: '100%' }} />
                        {linkMatches.length > 0 && (
                          <div style={{ marginTop: 3, display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 130, overflowY: 'auto' }}>
                            {linkMatches.map((it) => (
                              <button key={it.id} type="button" onClick={() => linkItem(itemForm.lineId, it.id)} disabled={linkingLine === itemForm.lineId}
                                style={{ textAlign: 'left', fontSize: '0.66rem', padding: '3px 6px', border: '1px solid #cfe6d8', borderRadius: 4, background: '#fff', color: '#2d6a4f', cursor: 'pointer' }}>
                                {it.name}{it.code ? ` · ${it.code}` : ''}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button type="button" onClick={createItem}
                          disabled={creatingItem || !itemForm.name.trim() || !itemForm.groupId || !l.linked_unit_id}
                          style={{ fontSize: '0.66rem', padding: '3px 10px', border: '1px solid #b45309', borderRadius: 4, background: creatingItem ? '#f0e0c8' : '#fff', color: '#b45309', cursor: (creatingItem || !itemForm.name.trim() || !itemForm.groupId || !l.linked_unit_id) ? 'not-allowed' : 'pointer', opacity: (!itemForm.name.trim() || !itemForm.groupId || !l.linked_unit_id) ? 0.5 : 1 }}>
                          {creatingItem ? 'Creating…' : 'Create & link'}
                        </button>
                        <button type="button" onClick={() => setItemForm(null)}
                          style={{ fontSize: '0.66rem', padding: '3px 10px', border: '1px solid #ddd', borderRadius: 4, background: '#fff', color: '#888', cursor: 'pointer' }}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </td>
                <td style={{ padding: '0.4rem 0.6rem', color: l.brand ? '#555' : '#b0b0b0' }}>
                  {l.brand || 'Not Set'}
                  {l.brand && !l.linked_brand_id && (
                    <span title="this brand doesn't exist in Loaded — it would be created as NEW on receive" style={newBadge}>NEW</span>
                  )}
                </td>
                <td style={{ padding: '0.4rem 0.6rem' }}>
                  <select value={l.linked_unit_id || ''} disabled={done}
                    onChange={(e) => onUnit(idx, e.target.value)}
                    style={{ ...inputStyle, minWidth: 120, borderColor: recommended ? '#b78a2f' : (!l.linked_unit_id ? '#f0c88a' : (l.copy_unit_mismatch ? '#c0392b' : '#d1d5db')), background: recommended && !done ? '#fdf6e7' : (!l.linked_unit_id && !done ? '#fff4e5' : '#fff') }}>
                    {!units.some((u) => u.id === l.linked_unit_id) && (
                      <option value={l.linked_unit_id || ''}>{l.unit || 'Select unit'}</option>
                    )}
                    {sortedUnits.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                  {/* Only a genuine unit mismatch the review found — the copy's
                      delivered unit (guideline-derived) resolves to a different
                      Loaded unit than the line currently has. A 2L bottle already
                      on "2 L" shows nothing. */}
                  {recommended && !done && (
                    <div style={{ fontSize: '0.6rem', color: '#b78a2f', marginTop: 2 }}>
                      copy says {l.recommended_unit} —{' '}
                      <button type="button" onClick={() => applyUnitSuggestion(idx, recommended)}
                        style={{ border: 'none', background: 'none', color: '#8a6d3b', textDecoration: 'underline', cursor: 'pointer', padding: 0, font: 'inherit' }}>
                        use {recommended.name}
                      </button>
                    </div>
                  )}
                  {!l.linked_unit_id && (
                    <div style={{ fontSize: '0.58rem', color: '#b45309', marginTop: 2 }}>NEW unit — not in Loaded</div>
                  )}
                  {/* The copy's DELIVERED unit differs from Loaded's but doesn't
                      resolve to a Loaded unit we can switch to (e.g. a multipack
                      like "5x3kg") — flag it with the delivered unit itself, never
                      the printed packaging word. */}
                  {!recommended && l.linked_unit_id && l.copy_unit_mismatch && l.recommended_unit && (
                    <div style={{ fontSize: '0.58rem', color: '#c0392b', marginTop: 2 }} title="the copy's delivered unit doesn't match Loaded's — check the unit on the line or the stock item">
                      copy delivered unit “{l.recommended_unit}” — differs from Loaded
                    </div>
                  )}
                </td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', color: '#888', fontVariantNumeric: 'tabular-nums' }}>
                  {l.quantity_ordered ?? '—'}
                </td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>
                  <input type="number" step="any" value={l.quantity_received ?? 0} disabled={done}
                    onChange={(e) => onQty(idx, parseFloat(e.target.value) || 0)}
                    style={{ ...inputStyle, width: 70, textAlign: 'right' }} />
                  {/* The review DECIDED the copy states a different received qty
                      (copy_quantity_mismatch) — the component only renders it as a
                      one-click edit, and hides it once the qty already matches. */}
                  {!done && l.copy_quantity_mismatch && l.copy_quantity != null && Math.abs((l.quantity_received ?? 0) - l.copy_quantity) > 0.001 && (
                    <div style={{ fontSize: '0.6rem', color: '#b78a2f', marginTop: 2 }}>
                      copy: {l.copy_quantity} —{' '}
                      <button type="button" onClick={() => applyQtySuggestion(idx)}
                        style={{ border: 'none', background: 'none', color: '#8a6d3b', textDecoration: 'underline', cursor: 'pointer', padding: 0, font: 'inherit' }}>
                        use
                      </button>
                    </div>
                  )}
                </td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', whiteSpace: 'nowrap' }}>
                  {/* Red ↑ when the invoice cost is above the linked PO's cost. */}
                  {l.reference_cost != null && (l.unit_cost ?? 0) > l.reference_cost + 0.001 && (
                    <span title={`up from ${cur(l.reference_cost)} on the order`} style={{ color: '#c0392b', marginRight: 3 }}>↑</span>
                  )}
                  <input type="number" step="any" value={l.unit_cost ?? 0} disabled={done}
                    onChange={(e) => onCost(idx, parseFloat(e.target.value) || 0)}
                    style={{ ...inputStyle, width: 80, textAlign: 'right' }} />
                </td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', color: '#888', fontVariantNumeric: 'tabular-nums' }}>
                  {cur(lineTax((l.quantity_received ?? 0) * (l.unit_cost ?? 0), l.sale_tax_rate, includesTax))}
                </td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                  {cur((l.quantity_received ?? 0) * (l.unit_cost ?? 0))}
                </td>
              </tr>
              {/* The original ordered line this delivery stood in for — a full,
                  read-only row shown when the substitute badge is expanded. */}
              {l.substitute_for && openSub.has(l.id) && (
                <tr style={{ background: '#fdf6e7', color: '#6b5626', fontSize: '0.82rem' }}>
                  <td style={{ padding: '0.35rem 0.6rem', paddingLeft: '1.4rem' }}>{l.substitute_for.code || '—'}</td>
                  <td style={{ padding: '0.35rem 0.6rem' }}>
                    <span style={{ fontSize: '0.56rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: '#a8843a', marginRight: 6 }}>ordered</span>
                    {l.substitute_for.description}
                  </td>
                  <td style={{ padding: '0.35rem 0.6rem', color: '#b0a074' }}>—</td>
                  <td style={{ padding: '0.35rem 0.6rem' }}>{l.substitute_for.unit || '—'}</td>
                  <td style={{ padding: '0.35rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{l.substitute_for.quantity_ordered ?? '—'}</td>
                  <td style={{ padding: '0.35rem 0.6rem', textAlign: 'right', color: '#b0a074' }}>—</td>
                  <td style={{ padding: '0.35rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{l.substitute_for.unit_cost != null ? cur(l.substitute_for.unit_cost) : '—'}</td>
                  <td style={{ padding: '0.35rem 0.6rem', textAlign: 'right', color: '#b0a074' }}>—</td>
                  <td style={{ padding: '0.35rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{l.substitute_for.quantity_ordered != null && l.substitute_for.unit_cost != null ? cur(l.substitute_for.quantity_ordered * l.substitute_for.unit_cost) : '—'}</td>
                </tr>
              )}
              </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Ordered, not delivered — PO items with NO invoice line at all (neither by
          code nor as a substitute). An item delivered under a different code shows
          as its substitute line above, not here. Read-only; never sent on receive. */}
      {(doc.ordered_not_received?.length ?? 0) > 0 && (
        <div style={{ padding: '0.5rem 0.9rem', borderTop: '1px solid #eee', background: '#fafafa' }}>
          <div style={{ ...microLabel, marginBottom: 4 }}>
            Ordered, not delivered ({doc.ordered_not_received!.length})
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.72rem', color: '#8a8a8a' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: '#b0b0b0', fontSize: '0.6rem', textTransform: 'uppercase' }}>
                <th style={{ padding: '0.25rem 0.6rem' }}>Code</th>
                <th style={{ padding: '0.25rem 0.6rem' }}>Description</th>
                <th style={{ padding: '0.25rem 0.6rem' }}>Unit</th>
                <th style={{ padding: '0.25rem 0.6rem', textAlign: 'right' }}>Qty ordered</th>
                <th style={{ padding: '0.25rem 0.6rem', textAlign: 'right' }}>Unit cost</th>
              </tr>
            </thead>
            <tbody>
              {doc.ordered_not_received!.map((o, i) => (
                <tr key={`${o.code || 'onr'}-${i}`} style={{ borderTop: '1px solid #f0f0f0' }}>
                  <td style={{ padding: '0.25rem 0.6rem' }}>{o.code || '—'}</td>
                  <td style={{ padding: '0.25rem 0.6rem' }}>{o.description || '—'}</td>
                  <td style={{ padding: '0.25rem 0.6rem' }}>{o.unit || '—'}</td>
                  <td style={{ padding: '0.25rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{o.quantity_ordered ?? '—'}</td>
                  <td style={{ padding: '0.25rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{o.unit_cost != null ? cur(o.unit_cost) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Item */}
      {!done && (
        <div style={{ position: 'relative', padding: '0.4rem 0.9rem', borderTop: '1px solid #f3f3f3' }}>
          <input type="text" placeholder="+ Add item…" value={addQuery}
            onChange={(e) => { setAddQuery(e.target.value); setAddOpen(true); }}
            onFocus={() => setAddOpen(true)}
            onBlur={() => setTimeout(() => setAddOpen(false), 150)}
            style={{ ...inputStyle, width: 240 }} />
          {addOpen && filteredStock.length > 0 && (
            <div style={{ position: 'absolute', zIndex: 20, background: '#fff', border: '1px solid #d1d5db', borderRadius: 6, marginTop: 2, maxHeight: 220, overflowY: 'auto', minWidth: 280, boxShadow: '0 6px 18px rgba(0,0,0,0.12)' }}>
              {filteredStock.map((i) => (
                <div key={i.id} onMouseDown={() => addItem(i)}
                  style={{ padding: '6px 10px', fontSize: '0.78rem', cursor: 'pointer' }}>
                  {i.code ? <span style={{ color: '#999' }}>{i.code} · </span> : null}{i.name}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Totals block (bottom, Loaded layout) */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '0.5rem 0.9rem', borderTop: '1px solid #eee' }}>
        <div style={{ minWidth: 220, fontSize: '0.78rem', fontVariantNumeric: 'tabular-nums' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: '#555' }}>
            <span>Total excl Tax</span><span>{cur(totals.excl)}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: '#555' }}>
            <span>Tax</span><span>{cur(totals.tax)}</span>
          </div>
          {!!totals.discount && (
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: '#555' }}>
              <span>Discount</span><span>−{cur(totals.discount)}</span>
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid #eee', marginTop: 4, paddingTop: 4, fontWeight: 700 }}>
            <span>Total incl Tax</span><span>{cur(totals.incl)}</span>
          </div>
          {totalMismatch && (
            <div style={{ fontSize: '0.6rem', color: '#c0392b', marginTop: 3, textAlign: 'right' }}>
              differs from the stated invoice total {cur(doc.total)}
            </div>
          )}
        </div>
      </div>

      {/* Notes */}
      <div style={{ padding: '0.5rem 0.9rem', borderTop: '1px solid #eee' }}>
        <div style={{ ...microLabel, marginBottom: 3 }}>Notes</div>
        <textarea value={doc.notes || ''} disabled={done} onChange={(e) => onNotes(e.target.value)}
          placeholder="Notes on the received goods…" rows={2}
          style={{ ...inputStyle, width: '100%', resize: 'vertical', minHeight: 40 }} />
      </div>

      {/* Suggested changes + what needs attention — always visible (no expand),
          so the user sees non-validations and any fix we made at a glance.
          Rows come from suggestionRows — ONE derivation shared with the inline
          affordances — pending (●, Accept) first, then applied (✓, persisted). */}
      {(suggestionRows.length > 0 || (checks.length > 0 && failChecks.length > 0)) && (
        <div style={{ padding: '0.55rem 0.9rem', borderTop: '1px solid #eee' }}>
          {suggestionRows.length > 0 && (
            <div style={{ marginBottom: failChecks.length ? 8 : 0 }}>
              <div style={{ ...microLabel, color: '#8a6d3b', marginBottom: 3 }}>Suggested changes</div>
              {suggestionRows.map((row) => {
                const applied = row.state === 'applied';
                const accept = row.engineFix ? () => acceptFix(row.key, row.engineFix!) : row.accept;
                return (
                  <div key={row.key} style={{ fontSize: '0.68rem', color: applied ? '#2e7d4f' : '#8a6d3b', display: 'flex', gap: 8, padding: '2px 0', alignItems: 'center' }}>
                    <span>{applied ? '✓' : '●'}</span>
                    <span style={{ flex: 1, ...(applied ? { textDecoration: 'line-through', color: '#9ca3af' } : {}) }}>{row.summary}</span>
                    {applied && row.undo && !done && !embedded && (
                      <button type="button" onClick={row.undo}
                        title="undo this change"
                        style={{ fontSize: '0.62rem', padding: '2px 10px', border: '1px solid #ccc', background: '#fff', color: '#666', borderRadius: 4, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                        Undo
                      </button>
                    )}
                    {!applied && accept && !done && !embedded && (
                      <button type="button" onClick={accept} disabled={accepting !== null}
                        style={{ fontSize: '0.62rem', padding: '2px 10px', border: '1px solid #b78a2f', background: accepting === row.key ? '#f0e6cc' : '#fff', color: '#8a6d3b', borderRadius: 4, cursor: accepting !== null ? 'default' : 'pointer', whiteSpace: 'nowrap', opacity: accepting !== null && accepting !== row.key ? 0.5 : 1 }}>
                        {accepting === row.key ? 'Applying…' : 'Accept'}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {failChecks.length > 0 && (
            <div>
              <div style={{ ...microLabel, color: '#c0392b', marginBottom: 3 }}>Needs attention</div>
              {failChecks.map((c) => (
                <div key={`fc-${c.label}`} style={{ fontSize: '0.68rem', color: '#c0392b', display: 'flex', gap: 6, padding: '1px 0' }}>
                  <span>✗</span><span>{c.label}</span>
                </div>
              ))}
              {(doc.check_reasons || []).map((r, i) => {
                // The stale "would be created as NEW" reason once the user's
                // edits resolved every NEW value — struck through with the
                // outcome, so what receive will send is unambiguous.
                const resolved = newValuesResolvedByEdits && r.includes('would be created as NEW');
                return (
                  <div key={`rn-${i}`} style={{ fontSize: '0.64rem', color: resolved ? '#9ca3af' : '#a04a3d', display: 'flex', gap: 6, padding: '1px 0 1px 14px' }}>
                    <span>–</span>
                    <span>
                      <span style={resolved ? { textDecoration: 'line-through' } : undefined}>{r}</span>
                      {resolved && (
                        <span style={{ color: '#2e7d4f', marginLeft: 6 }}>
                          resolved by your edits — receive writes the values you picked; nothing NEW is created
                        </span>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Validation */}
      <details style={{ borderTop: '1px solid #eee' }}>
        <summary style={{ padding: '0.45rem 0.9rem', fontSize: '0.68rem', color: '#666', cursor: 'pointer', userSelect: 'none' }}>
          Validation ({checkSummary})
        </summary>
        <div style={{ padding: '0 0.9rem 0.6rem 0.9rem' }}>
          {checks.length === 0 ? (
            <div style={{ fontSize: '0.64rem', color: '#9ca3af' }}>
              {reviewing ? 'Checking the lines against the attached invoice copy…' : 'No review checks yet.'}
            </div>
          ) : (
            CHECK_SECTIONS.map((sec) => (
              <div key={sec.title} style={{ marginTop: 6 }}>
                <div style={{ ...microLabel, marginBottom: 2 }}>{sec.title}</div>
                {sec.keys.map((key) => {
                  const c = checkByKey[key];
                  const resolvedByEdits = key === 'items_matched' && newValuesResolvedByEdits;
                  const state = resolvedByEdits ? 'pass' : c ? c.state : 'skip';
                  const color = state === 'pass' ? '#2e7d4f' : state === 'fail' ? '#c0392b' : state === 'suggest' ? '#b78a2f' : '#9ca3af';
                  const icon = state === 'pass' ? '✓' : state === 'fail' ? '✗' : state === 'suggest' ? '●' : '—';
                  return (
                    <div key={key} style={{ fontSize: '0.64rem', display: 'flex', gap: 6, color }}>
                      <span style={{ width: 8 }}>{icon}</span>
                      <span>
                        {CHECK_LABEL[key]}
                        {state === 'suggest' ? ' — suggested change' : ''}
                        {resolvedByEdits ? ' — resolved by your edits (nothing NEW will be created)' : ''}
                      </span>
                    </div>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </details>

      {/* Footer */}
      <div style={{ padding: '0.6rem 0.9rem', borderTop: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.75rem' }}>
        <span style={{ fontSize: '0.72rem', color: status === 'error' ? '#c0392b' : done ? '#2e7d4f' : (!done && unresolved.length > 0) ? '#b45309' : '#888' }}>
          {status === 'error' ? `✗ ${message}`
            : deletedDraft ? '✓ Draft deleted from Loaded — this document was a supplier statement.'
            : done ? '✓ Received in Loaded.'
            : status === 'saving' ? 'Receiving…'
            : unresolved.length > 0
              ? `${unresolved.length} line${unresolved.length > 1 ? 's have' : ' has'} a NEW item or unit — link or pick an existing one on the line before receiving.`
              : newValuesResolvedByEdits
                ? 'Your edits resolved the NEW values — receive writes the units/items you picked; nothing new is created in Loaded.'
                : 'Review the changes, then accept to update Loaded and receive.'}
        </span>
        {!done && (
          <button onClick={accept} disabled={status === 'saving' || unresolved.length > 0}
            title={unresolved.length > 0 ? 'Create the NEW items/units in Loaded first' : undefined}
            style={{ padding: '0.4rem 1.1rem', fontSize: '0.78rem', fontWeight: 500, border: 'none', borderRadius: 6, cursor: (status === 'saving' || unresolved.length > 0) ? 'not-allowed' : 'pointer', background: '#2e7d4f', color: '#fff', fontFamily: 'inherit', opacity: (status === 'saving' || unresolved.length > 0) ? 0.5 : 1, whiteSpace: 'nowrap' }}>
            {status === 'saving' ? 'Receiving…' : 'Accept & Receive'}
          </button>
        )}
      </div>
    </div>
  );
}
