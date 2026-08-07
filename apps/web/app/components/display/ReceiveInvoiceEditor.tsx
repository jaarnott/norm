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
  // Unit COST disagreed with the copy — renders "use copy price" (common with
  // unpriced feeds where Loaded ingests lines at $0 and only the copy prices them).
  copy_unit_cost_mismatch?: boolean | null;
  // The delivered unit the review derived from the copy per the venue's unit
  // guidelines (e.g. "CREAM FRESH 2L" → "2L"), NOT the raw packaging word.
  recommended_unit?: string | null;
  // The review's decision that this is a redundant $0 duplicate of another line
  // (same code, empty total). The component renders a "strike" affordance from
  // it; it does not decide the duplicate itself.
  copy_duplicate?: boolean | null;
  // The review's decision that this line is NOT on the attached invoice copy.
  // The component renders a "remove" affordance (strike-style) from it.
  copy_missing?: boolean | null;
  // The copy carries unit/size info that can't be read (cut off / illegible /
  // ambiguous) — the component asks the user to CONFIRM the unit; no value is
  // ever proposed.
  unit_needs_confirmation?: boolean | null;
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

// One mismatch from a Supplier Spec Dojo run (expected baseline vs the
// current prompts' extraction). line is 1-based; null = a header field.
interface DojoDiff {
  field: string;
  line?: number | null;
  description?: string | null;
  expected?: unknown;
  actual?: unknown;
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
  // Tombstone: the draft was deleted from Loaded (statement/duplicate accept).
  // The doc row is kept so old chat cards can render "deleted" instead of
  // hanging on a missing document.
  is_deleted?: boolean;
  deleted_reason?: string | null;
  status?: string;
  lines: Line[];
  // PO lines whose stock code never appeared on the invoice — ordered, not
  // received. Read-only reference, never part of the receive payload.
  ordered_not_received?: OrderedNotReceived[];
  checks?: string;
  // Specific reasons the failed checks failed (e.g. a line-vs-copy mismatch).
  check_reasons?: string[];
  // Dojo (supplier-spec regression run) payloads only: run status + the
  // structured diffs vs the stored baseline. Rendered as the dojo banner.
  dojo_status?: string | null;
  dojo_diffs?: DojoDiff[] | null;
  // The buyer PO the review read off the invoice copy, and whether it (and
  // Loaded's own field) resolved to NO Loaded purchase order — drives the
  // "copy says X — no matching purchase order" note under the picker.
  copy_po?: string | null;
  po_unresolved?: boolean | null;
  // The copy's printed totals when Loaded's header disagrees (e.g. a feed
  // that left the invoice total $0) — drives the "Invoice total X → Y"
  // suggestion; accepted values are written to Loaded on receive.
  copy_total?: number | null;
  copy_subtotal?: number | null;
  copy_tax_amount?: number | null;
  copy_total_mismatch?: boolean | null;
  // Changes the review made/proposes (e.g. an auto-matched PO, a unit fix).
  suggestions?: Suggestion[];
  // Persisted record of suggestions the user APPLIED or DISMISSED (dismissed:
  // true = declined without applying — unblocks receiving): the ✓/⊘ rows in
  // Suggested Changes survive line edits, re-reviews and re-opens, so the
  // user can always see what was actioned. Keyed stably per suggestion.
  // undo_fields: the line fields to restore when the action is undone (item
  // links record their pre-link state here).
  actioned_suggestions?: { key: string; summary: string; dismissed?: boolean; undo_fields?: Partial<Line>; undo_header?: Record<string, unknown> }[];
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
  // The PO line's stock item — lets a LOCAL item link reconcile immediately
  // (substitute detection) instead of waiting for the next server reopen.
  item_id?: string | null;
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
  // add_line: the copy's line data — Accept appends it as a new draft line
  // (a local edit, applied on receive), pre-linked when matched_item resolved.
  code?: string | null;
  description?: string | null;
  quantity?: number | null;
  unit?: string | null;
  unit_price_ex_tax?: number | null;
  line_total_ex_tax?: number | null;
  sale_tax_rate?: number | null;
  matched_item?: { id: string; name: string | null; unit_id?: string | null; unit_cost?: number | null } | null;
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

// One venue-wide fetch of the editor's reference data, shared by every mounted
// editor — the chat batch flow renders N cards at once, and without this each
// card would fire the same five GETs. Promise-cached per venue for the page's
// lifetime (reference data changes rarely; a hard refresh renews it).
type ReferenceData = {
  units: Unit[];
  purchase_orders: PO[];
  suppliers: Supplier[];
  stock_items: StockItem[];
  groups: StockGroup[];
};
const _referenceCache = new Map<string, Promise<ReferenceData>>();
// Cross-card channel: a card that receives an invoice (or deletes a draft)
// announces it so sibling cards on the same page can refresh — the server has
// invalidated their cached reviews, and the shared PO list is stale. Follows
// the norm:connector-auth CustomEvent idiom in lib/api.ts.
export const INVOICE_ACTIONED_EVENT = 'norm:invoice-actioned';
function fetchReferenceData(venueId: string): Promise<ReferenceData> {
  const hit = _referenceCache.get(venueId);
  if (hit) return hit;
  const get = <T,>(path: string, key: string, fallback: T): Promise<T> =>
    apiFetch(`/api/invoice-fixes/${path}?venue_id=${venueId}`)
      .then((r) => (r.ok ? r.json() : {}))
      .then((d: Record<string, unknown>) => ((d?.[key] as T) ?? fallback))
      .catch(() => fallback);
  const p = Promise.all([
    get<Unit[]>('units', 'units', []),
    get<PO[]>('purchase-orders', 'purchase_orders', []),
    get<Supplier[]>('suppliers', 'suppliers', []),
    get<StockItem[]>('stock-items', 'stock_items', []),
    get<StockGroup[]>('stock-groups', 'groups', []),
  ]).then(([units, purchase_orders, suppliers, stock_items, groups]) => ({
    units, purchase_orders, suppliers, stock_items, groups,
  }));
  _referenceCache.set(venueId, p);
  return p;
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

// Unit-of-measure resolution — mirrors app/services/invoice_units.py so a
// guideline-derived unit like "2L" resolves to the
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
  // Dojo: a supplier-spec regression run rendered in this component. The data
  // is an ephemeral local sandbox (no venue, no working document — every
  // network effect below is already guarded off); suggestions/validation/
  // receive are replaced by the dojo banner (status + diffs vs baseline).
  const dojo = !!props?.dojo;
  // Compact (chat) rendering: starts collapsed — header strip + suggested
  // changes + footer only; "Show full invoice" expands to the complete editor.
  const compact = !!props?.compact;
  const [expandedFull, setExpandedFull] = useState(false);
  const collapsed = compact && !expandedFull;
  // Expanding a compact (chat) card opens the FULL editor in a centered
  // overlay — the conversation column is too narrow for the full table.
  const overlay = compact && expandedFull;
  const workingDocId = (data as Record<string, unknown>)?.working_document_id as string | undefined;
  const venueId = (props?.activeVenueId as string) || (data as Record<string, unknown>)?.venue_id as string | undefined;

  // Reference data: baked into the block when embedded, fetched on the web.
  // `lines` is coerced to an array up front: opened from the Invoices page the
  // block carries only { working_document_id }, so the draft (lines, totals,
  // invoice_id) only arrives once the fetch below resolves.
  const initial = data as unknown as DocData;
  const [doc, setDoc] = useState<DocData>(() => ({ ...initial, lines: initial.lines ?? [] }));
  // Mirror for callbacks that must read the CURRENT doc without re-creating
  // themselves per render (patch line-id injection).
  const docRef = useRef<DocData>(doc);
  docRef.current = doc;
  // Version lives in a ref: PATCHes are queued and each must use the LATEST
  // version, not the one captured when its closure was created (two accepts
  // in one tick used to make the second 409).
  const versionRef = useRef<number>(1);
  const setVersion = (v: number) => { versionRef.current = v; };
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
  const reviewInFlight = useRef(false);
  const reviewQueued = useRef(false);
  const [accepting, setAccepting] = useState<string | null>(null);
  // Terminal state after accepting a delete_invoice suggestion (statement).
  const [deletedDraft, setDeletedDraft] = useState(false);
  // The doc id from the block 404s — the draft was hard-deleted (pre-tombstone).
  const [missingDoc, setMissingDoc] = useState(false);
  // The NEW-stock-item line currently being created, and the form's group id.
  const [itemForm, setItemForm] = useState<{ lineId: string; name: string; groupId: string } | null>(null);
  const [creatingItem, setCreatingItem] = useState(false);
  const [linkingLine, setLinkingLine] = useState<string | null>(null);
  // Line whose copy-delivered unit is being CREATED in Loaded (create-unit).
  const [creatingUnitLine, setCreatingUnitLine] = useState<string | null>(null);
  // Second confirmation before creating a unit (like create-item's form): the
  // first Accept/click ARMS this line; only the explicit confirm click writes.
  const [confirmUnitLine, setConfirmUnitLine] = useState<string | null>(null);
  const [linkQuery, setLinkQuery] = useState('');
  // Substitute lines whose original ordered row is expanded.
  const [openSub, setOpenSub] = useState<Set<string>>(new Set());

  // Embedded uses a distinct URL so the sandbox routes it to the invoice-scoped
  // tools; the web uses the session-auth working-documents REST.
  const docBase = embedded ? 'invoice-documents' : 'working-documents';
  const docUrl = workingDocId
    ? (threadId ? `/api/threads/${threadId}/${docBase}/${workingDocId}` : `/api/${docBase}/${workingDocId}`)
    : null;

  // Bumped when a SIBLING card receives/deletes an invoice — refetches this
  // card's doc (the server may have cleared its cached review, which re-arms
  // the review effect below) and the reference data (PO list went stale).
  const [refreshTick, setRefreshTick] = useState(0);
  useEffect(() => {
    if (embedded) return;
    const onActioned = (e: Event) => {
      const d = (e as CustomEvent).detail || {};
      if (!venueId || d.venueId !== venueId) return;
      // Skip the acting card itself — but twin docs for the SAME invoice on
      // other cards must refresh, so exclusion is by doc id, not invoice id.
      if (d.sourceDocId && d.sourceDocId === workingDocId) return;
      _referenceCache.delete(venueId);
      setRefreshTick((t) => t + 1);
    };
    window.addEventListener(INVOICE_ACTIONED_EVENT, onActioned);
    return () => window.removeEventListener(INVOICE_ACTIONED_EVENT, onActioned);
  }, [embedded, venueId, workingDocId]);

  // Load the draft (both surfaces) — gives the latest lines + the version.
  useEffect(() => {
    if (!docUrl) return;
    let live = true;
    apiFetch(docUrl)
      .then((r) => {
        if (r.status === 404) {
          // Deleted before tombstones existed — render the deleted state
          // instead of "Opening the invoice…" forever.
          if (live) setMissingDoc(true);
          return null;
        }
        return r.ok ? r.json() : null;
      })
      .then((d) => {
        if (!live || !d) return;
        setDoc((prev) => ({ ...prev, ...(d.data as DocData) }));
        if (typeof d.version === 'number') setVersion(d.version);
      })
      .catch(() => {});
    return () => { live = false; };
  }, [docUrl, refreshTick]);

  // Web-only reference reads (embedded gets them pre-baked in the block).
  useEffect(() => {
    if (embedded || !venueId) return;
    fetchReferenceData(venueId).then((refs) => {
      setUnits(refs.units);
      setPos(refs.purchase_orders);
      setSuppliers(refs.suppliers);
      setStockItems(refs.stock_items);
      setStockGroups(refs.groups);
    });
  }, [embedded, venueId, refreshTick]);

  // Item-match suggestions arrive WITH the review below — the engine
  // (review_and_receive_invoices) runs norm.match_stock_items itself and
  // /review merges matched_item/suggested_name/suggested_group_id onto the
  // lines. The component renders them; it never fetches them separately.

  // The cached PDF review — "checks after". Web-only (the endpoint is session-
  // auth). Callable from the mount effect AND explicitly (reset validation) —
  // relying on effect dep changes alone left a card already showing "not yet
  // reviewed" permanently stuck: a reset kept checks undefined → undefined, no
  // dep change, no re-run. `explicit` surfaces failures a background run keeps
  // quiet.
  const runReview = async (explicit = false) => {
    if (embedded || !venueId || !doc.invoice_id || doc.is_deleted) return;
    // One review at a time — but NEVER a dead-end: if checks get cleared
    // while a run is in flight, queue exactly one follow-up run. (The old
    // plain early-return left cards stranded on "not yet reviewed": the ref
    // clears without a re-render, so nothing ever retried.)
    if (reviewInFlight.current) {
      reviewQueued.current = true;
      return;
    }
    reviewInFlight.current = true;
    setReviewing(true);
    try {
      const r = await apiFetch('/api/invoice-fixes/review', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, invoice_id: doc.invoice_id }),
      });
      if (!r.ok) {
        if (explicit) {
          setStatus('error');
          setMessage(`Validation could not run (${r.status}) — reload and try again`);
        }
        return;
      }
      // The review landed on ALL twin docs server-side — refetch THIS card's
      // own doc so state (and the version) match its identity exactly. The
      // response body may belong to a canonical twin; adopting its lines and
      // version wholesale was how validation could visibly "move" or vanish.
      let adopted = false;
      if (docUrl) {
        const own = await apiFetch(docUrl).then((r2) => (r2.ok ? r2.json() : null)).catch(() => null);
        if (own?.data) {
          setDoc(own.data as DocData);
          if (typeof own.version === 'number') setVersion(own.version);
          adopted = true;
        }
      }
      if (!adopted) {
        const d = await r.json();
        if (d?.data) setDoc((prev) => ({ ...prev, ...(d.data as DocData) }));
      }
    } catch {
      if (explicit) {
        setStatus('error');
        setMessage('Validation could not run — check the connection and try again');
      }
    } finally {
      reviewInFlight.current = false;
      setReviewing(false);
      if (reviewQueued.current) {
        reviewQueued.current = false;
        setTimeout(() => runReview(), 0);
      }
    }
  };
  useEffect(() => {
    if (embedded || !venueId || !doc.invoice_id) return;
    if (doc.is_deleted) return; // tombstone — nothing left to review
    // Re-review whenever checks are absent. "" is a REAL result (credit note /
    // no PDF: the review ran and produced no artifact) — treating it as
    // unreviewed used to re-run the engine on every open.
    if (doc.checks != null) return;
    runReview();
    // Fires when the invoice_id is known AND whenever `checks` is cleared (e.g.
    // after a server-applied fix reshapes the doc) — the guard above stops a
    // re-run once the review returns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embedded, venueId, doc.invoice_id, doc.checks]);

  // Serialized patches: one at a time, each using the LATEST version.
  // update_line ops get the target line's id injected so a server-side
  // reorder (reshape, review merge) can never land the edit on the wrong
  // line. A 409 is SURFACED (view refreshed + message), never swallowed —
  // silently dropping it left edits that evaporated on the next refetch.
  const patchQueue = useRef<Promise<void>>(Promise.resolve());
  const patchDoc = useCallback(
    (ops: Record<string, unknown>[]) => {
      if (!docUrl) return Promise.resolve();
      const withIds = ops.map((op) => {
        if (op.op === 'update_line' && op.line_id == null && typeof op.index === 'number') {
          const id = docRef.current.lines[op.index as number]?.id;
          return id ? { ...op, line_id: id } : op;
        }
        return op;
      });
      const run = async () => {
        try {
          const res = await apiFetch(docUrl, {
            method: 'PATCH',
            body: JSON.stringify({ ops: withIds, version: versionRef.current }),
          });
          if (res.status === 409) {
            const fresh = await apiFetch(docUrl).then((r) => (r.ok ? r.json() : null)).catch(() => null);
            if (fresh?.data) {
              setDoc(fresh.data as DocData);
              if (typeof fresh.version === 'number') setVersion(fresh.version);
            }
            setStatus('error');
            setMessage('This invoice was updated elsewhere — the view has been refreshed; please re-apply your last change.');
            return;
          }
          if (res.ok) {
            const updated = await res.json();
            if (updated?.data) setDoc((prev) => ({ ...prev, ...(updated.data as DocData) }));
            if (typeof updated?.version === 'number') setVersion(updated.version);
          }
        } catch { /* keep local state; the next patch retries */ }
      };
      const p = patchQueue.current.then(run);
      patchQueue.current = p;
      return p;
    },
    [docUrl],
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
  // The copy's delivered unit isn't in Loaded's catalogue at all (e.g. a
  // "49.5L" keg, a "5x3kg" multipack): create it in Loaded — the ONE write,
  // mirroring create-item — then the line takes it as a LOCAL edit via
  // applyUnitSuggestion, landing on the line + supplier variant at receive.
  const createUnitAndApply = async (idx: number) => {
    const l = doc.lines[idx];
    if (!l?.recommended_unit || !venueId || embedded || creatingUnitLine) return;
    // Two-step: first activation arms the confirmation (shown on the line AND
    // the suggestion row); only the second, explicit confirm creates the unit.
    if (confirmUnitLine !== l.id) {
      setConfirmUnitLine(l.id);
      return;
    }
    setConfirmUnitLine(null);
    setCreatingUnitLine(l.id);
    setMessage('');
    try {
      const res = await apiFetch('/api/invoice-fixes/create-unit', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, name: l.recommended_unit }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
      }
      const out = await res.json();
      if (!out?.unit_id) throw new Error('Loaded did not return the created unit');
      const rec: Unit = { id: out.unit_id, name: out.unit_name ?? l.recommended_unit, ratio: out.unit_ratio ?? undefined };
      // Into the dropdown (and future resolveUnit calls) before the line takes it.
      setUnits((prev) => (prev.some((u) => u.id === rec.id) ? prev : [...prev, rec]));
      applyUnitSuggestion(idx, rec);
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Could not create the unit in Loaded');
    } finally {
      setCreatingUnitLine(null);
    }
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
  const applyCostSuggestion = (idx: number) => {
    const l = doc.lines[idx];
    if (!l || l.copy_unit_price == null) return;
    const cost = l.copy_unit_price;
    const qty = l.quantity_received ?? 0;
    const log = withActioned(
      `cost:${l.id}`,
      `${l.display_code || l.code || '?'} · ${l.description ?? ''}: unit cost ${cur(l.unit_cost)} → ${cur(cost)}`,
    );
    setDoc((prev) => ({
      ...prev,
      actioned_suggestions: log,
      lines: prev.lines.map((x, i) => (i === idx ? { ...x, unit_cost: cost, total_cost: qty * cost } : x)),
    }));
    if (workingDocId) patchDoc([
      { op: 'update_line', index: idx, fields: { unit_cost: cost, total_cost: qty * cost } },
      { op: 'update_header', fields: { actioned_suggestions: log } },
    ]);
  };
  // Header totals from the invoice copy (Gate 11): Loaded's feed sometimes
  // leaves the invoice total/subtotal $0. A LOCAL edit like the line
  // suggestions — written to Loaded on receive, undoable via undo_header.
  const applyTotalSuggestion = () => {
    if (doc.copy_total == null) return;
    const key = `total:${doc.invoice_id}`;
    const fields: Partial<DocData> = { total: doc.copy_total };
    if (doc.copy_subtotal != null) fields.subtotal = doc.copy_subtotal;
    if (doc.copy_tax_amount != null) fields.tax_amount = doc.copy_tax_amount;
    const log = [
      ...(doc.actioned_suggestions || []).filter((a) => a.key !== key),
      {
        key,
        summary: `Invoice total ${cur(doc.total)} → ${cur(doc.copy_total)} (per the invoice copy)`,
        undo_header: {
          total: doc.total ?? null,
          subtotal: doc.subtotal ?? null,
          tax_amount: doc.tax_amount ?? null,
        },
      },
    ];
    setDoc((prev) => ({ ...prev, ...fields, actioned_suggestions: log }));
    if (workingDocId) patchDoc([
      { op: 'update_header', fields: { ...fields, actioned_suggestions: log } },
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
  const undoStrikeSuggestion = (idx: number, keyPrefix: 'strike' | 'remove' = 'strike') => {
    const l = doc.lines[idx];
    if (!l) return;
    const log = (doc.actioned_suggestions || []).filter((a) => a.key !== `${keyPrefix}:${l.id}`);
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
  // Remove-line suggestion (the review found the line on the draft but NOT on
  // the attached copy): strike-style — the line stays visible struck-through,
  // drops from the totals, and is soft-deleted in Loaded at receive. Undoable.
  const applyRemoveSuggestion = (idx: number) => {
    const l = doc.lines[idx];
    if (!l) return;
    const log = withActioned(
      `remove:${l.id}`,
      `${l.display_code || l.code || '?'} · ${l.description ?? ''}: not on the invoice copy — removed (excluded from receive)`,
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
  // Dismiss: decline a suggestion WITHOUT applying it. Logged (⊘ row, undoable)
  // so it stops blocking Accept & Receive and stays suppressed across
  // re-reviews. Never touches lines or Loaded.
  const dismissSuggestion = (key: string, summary: string) => {
    const log = [
      ...(doc.actioned_suggestions || []).filter((a) => a.key !== key),
      { key, summary, dismissed: true },
    ];
    setDoc((prev) => ({ ...prev, actioned_suggestions: log }));
    if (workingDocId) patchDoc([
      { op: 'update_header', fields: { actioned_suggestions: log } },
    ]);
  };
  const undoDismissSuggestion = (key: string) => {
    const log = (doc.actioned_suggestions || []).filter((a) => a.key !== key);
    setDoc((prev) => ({ ...prev, actioned_suggestions: log }));
    if (workingDocId) patchDoc([
      { op: 'update_header', fields: { actioned_suggestions: log } },
    ]);
  };
  // Stable key for an add_line suggestion: derived from the DOC line's own
  // content, never its position — a re-review after a reshape re-emits the
  // suggestion, and the actioned log must still recognize it as done.
  const addSuggestionKey = (s: Suggestion) =>
    `add:${`${s.code ?? ''}:${s.description ?? ''}:${s.line_total_ex_tax ?? ''}`
      .toLowerCase()
      .replace(/[^a-z0-9:.]/g, '')}`;
  // Add-line suggestion (a line on the attached copy with no matching draft
  // line): appends it like the manual Add Item flow. Pre-linked when the
  // engine's item matcher resolved a stock item; otherwise it lands unlinked
  // and the existing receive gate holds until the user links it.
  const applyAddSuggestion = (s: Suggestion) => {
    const key = addSuggestionKey(s);
    const matched = s.matched_item;
    const u = matched?.unit_id ? units.find((x) => x.id === matched.unit_id) : undefined;
    const qty = s.quantity ?? 1;
    const cost = s.unit_price_ex_tax ?? matched?.unit_cost ?? 0;
    const fields = {
      id: `new-${Date.now()}`,
      code: s.code ?? null,
      description: s.description ?? null,
      brand: null,
      // Always present: the add_line op defaults an ABSENT unit key to "case".
      unit: u?.name ?? s.unit ?? null,
      linked_unit_id: matched?.unit_id ?? null,
      unit_ratio: u?.ratio ?? 1,
      quantity_ordered: null,
      quantity_received: qty,
      unit_cost: cost,
      total_cost: s.line_total_ex_tax ?? qty * cost,
      sale_tax_rate: s.sale_tax_rate ?? null,
      linked_item_id: matched?.id ?? null,
    };
    const log = withActioned(
      key,
      `Added '${s.description ?? ''}' (${cur(s.line_total_ex_tax)}) from the invoice copy`,
    );
    setDoc((prev) => ({
      ...prev,
      actioned_suggestions: log,
      lines: [...prev.lines, fields as Line],
    }));
    if (workingDocId) patchDoc([
      { op: 'add_line', fields },
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
  // Loaded supplier whose name matches the invoice's printed supplier name
  // (normalized substring either way) — offered when the feed left the
  // invoice UNLINKED ("The Sawmill Brewing Company Limited" vs the Loaded
  // record "Sawmill"). Picking it is a local header edit, written on receive.
  const suggestedSupplier = useMemo(() => {
    if (doc.linked_supplier_id || !doc.supplier_name) return undefined;
    const nrm = (v: string) => v.toLowerCase().replace(/[^a-z0-9]/g, '');
    const target = nrm(doc.supplier_name);
    if (target.length < 3) return undefined;
    return suppliers.find((s) => {
      const c = nrm(s.name || '');
      return c.length >= 3 && (c === target || target.includes(c) || c.includes(target));
    });
  }, [doc.linked_supplier_id, doc.supplier_name, suppliers]);
  // ONE accept path for the supplier link — used by BOTH the inline note and
  // its Suggested Changes row (logged ✓, undoable via undo_header).
  const applySupplierSuggestion = (s: { id: string; name?: string | null }) => {
    const log = [
      ...(doc.actioned_suggestions || []).filter((a) => a.key !== `supplier:${s.id}`),
      {
        key: `supplier:${s.id}`,
        summary: `Supplier linked to '${s.name ?? ''}' — saved to Loaded on receive`,
        undo_header: {
          linked_supplier_id: doc.linked_supplier_id ?? null,
          supplier_name: doc.supplier_name ?? null,
        },
      },
    ];
    setDoc((prev) => ({
      ...prev,
      actioned_suggestions: log,
      linked_supplier_id: s.id,
      supplier_name: s.name ?? prev.supplier_name,
    }));
    if (workingDocId) patchDoc([
      { op: 'update_header', fields: { linked_supplier_id: s.id, supplier_name: s.name ?? null, actioned_suggestions: log } },
    ]);
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
  const done = status === 'done' || !!doc.is_received || !!doc.is_deleted;
  const draftDeleted = deletedDraft || !!doc.is_deleted;
  // Lines still pointing at a NEW (uncreated) stock item or unit — receiving is
  // blocked until each is explicitly created in Loaded. A struck line is
  // excluded from the receive entirely, so it never gates it.
  const unresolved = useMemo(
    () => doc.lines.filter((l) => !l.struck && (!l.linked_item_id || !l.linked_unit_id)),
    [doc.lines],
  );
  // The engine's "no NEW values" check is a snapshot of Loaded at review time.
  // The user's LOCAL edits (picking an existing unit, linking an item) resolve
  // it without any write until receive — reconcile the DISPLAY so it's clear
  // nothing NEW will be created. Purely presentational: the engine's cached
  // artifact is untouched, and the receive guard still runs off `unresolved`.
  const newValuesResolvedByEdits =
    !done && unresolved.length === 0 && checkByKey.items_matched?.state === 'fail';
  // Incremental validation ("dirty parts"): a failed check whose underlying
  // per-line mismatches were ALL addressed by local edits (accepted or
  // dismissed suggestions, struck lines, values now matching the copy) is
  // DISPLAYED resolved — no full re-review needed. Purely presentational,
  // exactly like newValuesResolvedByEdits: the engine's cached artifact is
  // never mutated, and full revalidation stays reserved for reset / a changed
  // invoice in Loaded / server-applied fixes. Dismissing counts as resolved —
  // the user made the decision.
  // The copy's printed total disagrees with the header and hasn't been taken
  // yet — drives the suggestion row and the amber Invoice Total input.
  const totalSuggestPending = !!doc.copy_total_mismatch && doc.copy_total != null
    && Math.abs((doc.total ?? 0) - doc.copy_total) > 0.02;
  const resolvedLocally = useMemo(() => {
    const out = new Set<string>();
    if (done || !doc.checks) return out;
    const logged = new Set((doc.actioned_suggestions || []).map((a) => a.key));
    const near = (a: number | null | undefined, b: number | null | undefined, tol: number) =>
      a != null && b != null && Math.abs(a - b) <= tol;
    // Totals accepted from the copy: the pdf_total check resolves when the
    // header total now matches the copy, and the internal-consistency check
    // resolves when the edited header arithmetic adds up again.
    if (checkByKey.pdf_total?.state === 'fail'
      && doc.copy_total != null && near(doc.total, doc.copy_total, 0.02)) {
      out.add('pdf_total');
    }
    if (checkByKey.totals?.state === 'fail') {
      const lineSum = doc.lines.filter((l) => !l.struck)
        .reduce((s, l) => s + (l.quantity_received ?? 0) * (l.unit_cost ?? 0), 0);
      if (near(lineSum, doc.subtotal, 0.02)
        && near((doc.subtotal ?? 0) + (doc.tax_amount ?? 0), doc.total, 0.02)) {
        out.add('totals');
      }
    }
    if (checkByKey.unit_of_measure?.state === 'fail') {
      const pending = doc.lines.some((l) => {
        if (l.struck) return false;
        if (recommendedFor[l.id]) return true; // derivable fix not yet taken
        // Unreadable unit on the copy: pending until the user CONFIRMS the
        // current unit or picks a different one.
        if (l.unit_needs_confirmation
          && l.linked_unit_id === l.original_unit_id
          && !logged.has(`confirm-unit:${l.id}`)) return true;
        // Flagged mismatch with no derivable fix: resolved once the user
        // changed the unit away from the reviewed snapshot, or dismissed.
        return !!l.copy_unit_mismatch
          && l.linked_unit_id === l.original_unit_id
          && !logged.has(`unit:${l.id}`);
      });
      if (!pending) out.add('unit_of_measure');
    }
    if (checkByKey.pdf_lines?.state === 'fail') {
      const linePending = doc.lines.some((l) => {
        if (l.struck) return false;
        if (l.copy_quantity_mismatch && l.copy_quantity != null
          && !near(l.quantity_received ?? 0, l.copy_quantity, 0.001)
          && !logged.has(`qty:${l.id}`)) return true;
        if (l.copy_unit_cost_mismatch && l.copy_unit_price != null
          && !near(l.unit_cost ?? 0, l.copy_unit_price, 0.005)
          && !logged.has(`cost:${l.id}`)) return true;
        if (l.copy_missing && !logged.has(`remove:${l.id}`)) return true;
        if (l.copy_duplicate && !logged.has(`strike:${l.id}`)) return true;
        return false;
      });
      const addPending = (doc.suggestions || []).some(
        (s) => s.type === 'add_line' && !logged.has(addSuggestionKey(s)),
      );
      if (!linePending && !addPending) out.add('pdf_lines');
    }
    if (checkByKey.po_linked?.state === 'fail' && doc.linked_purchase_order_id) {
      out.add('po_linked');
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done, doc.checks, doc.lines, doc.suggestions, doc.actioned_suggestions, doc.linked_purchase_order_id, doc.total, doc.subtotal, doc.tax_amount, doc.copy_total, checkByKey, recommendedFor]);
  const checkSummary = useMemo(() => {
    if (checks.length === 0) return reviewing ? 'checking against the copy…' : 'not yet reviewed';
    let fail = checks.filter((c) => c.state === 'fail').length;
    const suggest = checks.filter((c) => c.state === 'suggest').length;
    const skip = checks.filter((c) => c.state === 'skip').length;
    const resolved = (newValuesResolvedByEdits ? 1 : 0) + resolvedLocally.size;
    fail -= resolved;
    if (fail < 0) fail = 0;
    if (!fail && !skip && !suggest && !resolved) return 'all checks pass';
    return `${checks.filter((c) => c.state === 'pass').length} passed`
      + `${fail ? ` · ${fail} failed` : ''}`
      + `${suggest ? ` · ${suggest} suggested` : ''}`
      + `${resolved ? ` · ${resolved} resolved by your edits` : ''}`
      + `${skip ? ` · ${skip} not reached` : ''}`;
  }, [checks, reviewing, newValuesResolvedByEdits, resolvedLocally]);
  const failChecks = useMemo(
    () => checks.filter(
      (c) => c.state === 'fail'
        && !(c.key === 'items_matched' && newValuesResolvedByEdits)
        && !resolvedLocally.has(c.key),
    ),
    [checks, newValuesResolvedByEdits, resolvedLocally],
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
    state: 'pending' | 'applied' | 'dismissed';
    accept?: () => void;
    acceptLabel?: string; // e.g. the armed "Confirm — create '8x300m'" state
    dismiss?: () => void;
    dismissLabel?: string; // e.g. "Confirm" for confirm-unit rows
    undo?: () => void;
    engineFix?: Suggestion; // link_po rows accept via the server
  };
  const suggestionRows: SuggestionRow[] = (() => {
    const rows: SuggestionRow[] = [];
    const log = doc.actioned_suggestions || [];
    const logged = new Set(log.map((a) => a.key));
    (doc.suggestions || []).forEach((s, si) => {
      if (s.type === 'link_po' && !doc.linked_purchase_order_id) {
        const key = `po:${s.purchase_order_id || s.po_number || ''}:${si}`;
        if (!logged.has(key)) {
          const summary = s.summary || `Link purchase order ${s.po_number ?? ''}`;
          rows.push({
            key,
            summary,
            state: 'pending',
            engineFix: s.already_linked_elsewhere === true ? undefined : s,
            dismiss: s.already_linked_elsewhere === true ? undefined : () => dismissSuggestion(key, summary),
          });
        }
      } else if (s.type === 'delete_invoice') {
        // Statement or duplicate: Accept deletes the draft from Loaded
        // (server applier, verified DELETE endpoint). Keyed by index — the
        // engine dedupes, but two rows must never share a React key.
        const key = `delete_invoice:${si}`;
        if (!logged.has(key)) {
          const summary = s.summary || 'This document is a supplier statement, not an invoice — delete this draft in Loaded';
          rows.push({
            key,
            summary,
            state: 'pending',
            engineFix: s,
            dismiss: () => dismissSuggestion(key, summary),
          });
        }
      } else if (s.type === 'add_line') {
        // A line on the invoice copy with no matching draft line: Accept
        // appends it as a LOCAL draft edit (applied on receive) — never via
        // the server accept endpoint. Content-derived key: the log must
        // recognize the same doc line across re-reviews.
        const key = addSuggestionKey(s);
        if (!logged.has(key)) {
          const summary = s.summary || `Add document line '${s.description ?? ''}' from the invoice copy`;
          rows.push({
            key: `${key}:${si}`,
            summary,
            state: 'pending',
            accept: () => applyAddSuggestion(s),
            dismiss: () => dismissSuggestion(key, summary),
          });
        }
      }
    });
    // Per-line rows share one shape: stable key, accept applies, dismiss
    // declines (logged, undoable) — both suppressed once the key is logged.
    const pushLineRow = (key: string, summary: string, accept: () => void) => {
      if (logged.has(key)) return;
      rows.push({
        key,
        summary,
        state: 'pending',
        accept,
        dismiss: () => dismissSuggestion(key, summary),
      });
    };
    // Header-level: the invoice has NO linked supplier and a Loaded record
    // matches the printed name — same accept path as the inline note under
    // the Supplier dropdown. Receiving stays blocked (supplierBlocking) even
    // if dismissed: Loaded cannot receive a supplier-less invoice.
    if (doc.invoice_id && !doc.linked_supplier_id && suggestedSupplier) {
      pushLineRow(
        `supplier:${suggestedSupplier.id}`,
        `Supplier '${doc.supplier_name ?? ''}' is not linked in Loaded — link to '${suggestedSupplier.name}' (saved to Loaded on receive)`,
        () => applySupplierSuggestion(suggestedSupplier),
      );
    }
    // Header totals from the copy (Gate 11): one row, accepted as a local
    // header edit — written to Loaded on receive.
    if (totalSuggestPending && doc.copy_total != null) {
      pushLineRow(
        `total:${doc.invoice_id}`,
        `Invoice total ${cur(doc.total)} → ${cur(doc.copy_total)} (per the invoice copy)`,
        applyTotalSuggestion,
      );
    }
    doc.lines.forEach((l, idx) => {
      // A struck line is inert: no pending rows of any kind (its own strike /
      // remove action shows as an applied ✓ row from the log, with Undo).
      if (l.struck) return;
      const code = l.display_code || l.code || '?';
      const rec = recommendedFor[l.id];
      if (rec) {
        pushLineRow(
          `unit:${l.id}`,
          `${code} · ${l.description ?? ''}: unit ${l.unit || '—'} → ${rec.name} (per the invoice copy)`,
          () => applyUnitSuggestion(idx, rec),
        );
      } else if (l.copy_unit_mismatch && l.recommended_unit
        && l.linked_unit_id === l.original_unit_id) {
        // The copy's delivered unit doesn't exist in Loaded AT ALL: Accept
        // arms a second confirmation (create-item style), and the confirm
        // CREATES the unit (the one Loaded write), then the line takes it
        // locally — applied to the line + variant on receive.
        const unitKey = `unit:${l.id}`;
        if (!logged.has(unitKey)) {
          const summary = `${code} · ${l.description ?? ''}: unit ${l.unit || '—'} → ${l.recommended_unit} (new unit — created in Loaded on accept)`;
          rows.push({
            key: unitKey,
            summary,
            state: 'pending',
            accept: () => { void createUnitAndApply(idx); },
            acceptLabel: creatingUnitLine === l.id
              ? 'Creating…'
              : confirmUnitLine === l.id
                ? `Confirm — create '${l.recommended_unit}'`
                : undefined,
            dismiss: () => dismissSuggestion(unitKey, summary),
          });
        }
      }
      const qtyPending = l.copy_quantity_mismatch && l.copy_quantity != null
        && Math.abs((l.quantity_received ?? 0) - l.copy_quantity) > 0.001;
      if (qtyPending) {
        pushLineRow(
          `qty:${l.id}`,
          `${code} · ${l.description ?? ''}: Qty received ${l.quantity_received ?? 0} → ${l.copy_quantity} (per the invoice copy)`,
          () => applyQtySuggestion(idx),
        );
      }
      const costPending = l.copy_unit_cost_mismatch && l.copy_unit_price != null
        && Math.abs((l.unit_cost ?? 0) - l.copy_unit_price) > 0.005;
      if (costPending) {
        pushLineRow(
          `cost:${l.id}`,
          `${code} · ${l.description ?? ''}: unit cost ${cur(l.unit_cost)} → ${cur(l.copy_unit_price)} (per the invoice copy)`,
          () => applyCostSuggestion(idx),
        );
      }
      if (l.copy_duplicate && !l.struck) {
        pushLineRow(
          `strike:${l.id}`,
          `${code} · ${l.description ?? ''}: $0 duplicate line — strike it (excluded from receive)`,
          () => applyStrikeSuggestion(idx),
        );
      }
      if (l.copy_missing && !l.struck) {
        pushLineRow(
          `remove:${l.id}`,
          `${code} · ${l.description ?? ''}: not on the attached invoice copy — remove it (excluded from receive)`,
          () => applyRemoveSuggestion(idx),
        );
      }
      // Unreadable unit: NO proposed value — a Confirm-only row (dismiss-style)
      // that blocks Accept & Receive until the user confirms the current unit
      // or picks another. Changing the unit resolves it via resolvedLocally.
      if (l.unit_needs_confirmation && !l.struck
        && l.linked_unit_id === l.original_unit_id
        && !logged.has(`confirm-unit:${l.id}`)) {
        const key = `confirm-unit:${l.id}`;
        const summary = `${code} · ${l.description ?? ''}: the unit can't be read from the invoice copy — confirm the unit (currently '${l.unit ?? '—'}')`;
        rows.push({
          key,
          summary,
          state: 'pending',
          dismiss: () => dismissSuggestion(key, `${code} · ${l.description ?? ''}: unit confirmed as '${l.unit ?? '—'}'`),
          dismissLabel: 'Confirm',
        });
      }
      const matched = l.matched_item;
      if (!l.linked_item_id && matched?.id) {
        pushLineRow(
          `item:${l.id}`,
          `${code} · ${l.description ?? ''}: link to existing '${matched.name ?? ''}'`,
          () => linkItem(l.id, matched.id),
        );
      }
    });
    for (const a of log) {
      if (a.dismissed) {
        rows.push({
          key: `done:${a.key}`,
          summary: a.summary,
          state: 'dismissed',
          undo: () => undoDismissSuggestion(a.key),
        });
        continue;
      }
      const struckPrefix = a.key.startsWith('strike:') ? 'strike' : a.key.startsWith('remove:') ? 'remove' : null;
      rows.push({
        key: `done:${a.key}`,
        summary: a.summary,
        state: 'applied',
        undo: struckPrefix
          ? () => {
              const li = doc.lines.findIndex((l) => `${struckPrefix}:${l.id}` === a.key);
              if (li >= 0) undoStrikeSuggestion(li, struckPrefix);
            }
          : a.undo_fields || a.undo_header
            ? () => undoActionedFields(a)
            : undefined,
      });
    }
    return rows;
  })();
  // Pending rows with a real action block Accept & Receive until decided
  // (Accept or Dismiss). Rows with no action (e.g. a PO already invoiced on
  // another order) are informational and never block. Web only — embedded
  // cards hide the accept/dismiss buttons, so the gate must not apply there.
  const blockingSuggestions = suggestionRows.filter(
    (r) => r.state === 'pending' && (r.accept || r.engineFix || r.dismiss),
  );
  const suggestionsBlocking = !embedded && blockingSuggestions.length > 0;
  // No linked supplier: Loaded rejects the receive outright (500), so block
  // it here with a visible reason. Web only, once the draft has loaded.
  const supplierBlocking = !embedded && !done && !!doc.invoice_id && !doc.linked_supplier_id;
  // Nothing to receive: an empty draft (a statement/letter uploaded as an
  // invoice — e.g. a surcharge notice — or every line struck). Deleting the
  // draft is the action, never an empty receive; the server rejects it too.
  const noLines = !embedded && !done && !!doc.invoice_id && !doc.lines.some((l) => !l.struck);
  const receiveBlocked = status === 'saving' || unresolved.length > 0 || suggestionsBlocking || supplierBlocking || noLines;
  const sortedGroups = useMemo(
    () => [...stockGroups].sort((a, b) => (a.name || '').localeCompare(b.name || '')),
    [stockGroups],
  );

  // Retest support: wipe every cached validation artifact for this invoice
  // (server deletes the extraction cache and rebuilds the draft from Loaded),
  // reload THIS card's own doc (never the endpoint's — with twin docs its
  // returned version can belong to a sibling and would poison later PATCHes),
  // then run the review EXPLICITLY — a card already showing "not yet
  // reviewed" has no dep change to re-arm the effect. Twin cards refetch via
  // the actioned event.
  const resetValidation = async () => {
    if (embedded || !venueId || !doc.invoice_id) return;
    setReviewing(true);
    try {
      const r = await apiFetch('/api/invoice-fixes/reset-validation', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, invoice_id: doc.invoice_id }),
      });
      if (!r.ok) {
        setStatus('error');
        setMessage(`Reset failed (${r.status}) — try again`);
        return;
      }
      const out = await r.json();
      let fresh: { data?: DocData; version?: number } | null = null;
      if (docUrl) {
        fresh = await apiFetch(docUrl).then((r2) => (r2.ok ? r2.json() : null)).catch(() => null);
      }
      if (!fresh?.data) fresh = out?.document ?? null;
      if (fresh?.data) {
        setDoc(fresh.data as DocData);
        if (typeof fresh.version === 'number') setVersion(fresh.version);
      }
      window.dispatchEvent(new CustomEvent(INVOICE_ACTIONED_EVENT, {
        detail: { venueId, invoiceId: doc.invoice_id, sourceDocId: workingDocId },
      }));
      await runReview(true);
    } catch {
      setStatus('error');
      setMessage('Reset failed — check the connection and try again');
    } finally {
      setReviewing(false);
    }
  };

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
        // Siblings may reference this draft (duplicate pair) — tell them.
        if (venueId) window.dispatchEvent(new CustomEvent(INVOICE_ACTIONED_EVENT, {
          detail: { venueId, invoiceId: doc.invoice_id, sourceDocId: workingDocId },
        }));
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
  // Link a line to a stock item as a LOCAL draft edit. Nothing is written to
  // Loaded until Accept & Receive — the receive PUT carries the linkedItemId
  // and do_receive registers the supplier variant then. Logged (undoable via
  // undo_fields: the pre-link state) so reset-validation genuinely starts
  // from scratch.
  const applyLocalLink = (
    lineId: string,
    itemId: string,
    itemName: string | null,
  ) => {
    const idx = doc.lines.findIndex((l) => l.id === lineId);
    const l = doc.lines[idx];
    if (!l) return;
    // The link sets the ITEM only. The line's unit is its own decision — the
    // unit suggestion row / picker handles it explicitly; auto-filling it from
    // the item's default variant silently changed units the user never chose.
    const fields: Partial<Line> = { linked_item_id: itemId, item_name: itemName };
    const undoFields: Partial<Line> = { linked_item_id: null, item_name: l.item_name ?? null };
    // PO reconciliation, immediately: if the linked ITEM was ordered on the PO
    // (listed under "ordered, not delivered"), this delivery covers it — under
    // a different code it's a SUBSTITUTE (badge + expandable original), and
    // either way the entry leaves the not-delivered list. Mirrors the server's
    // _attach_po_reference, which recomputes the same thing on the next open.
    const normCode = (s: string | null | undefined) => String(s ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const onr = doc.ordered_not_received || [];
    const orderedEntry = onr.find((o) => o.item_id && o.item_id === itemId);
    let headerFields: Record<string, unknown> | null = null;
    let undoHeader: Record<string, unknown> | null = null;
    if (orderedEntry) {
      const isSub = !!l.code && normCode(l.code) !== normCode(orderedEntry.code);
      fields.on_order = true;
      fields.quantity_ordered = orderedEntry.quantity_ordered;
      fields.reference_cost = orderedEntry.unit_cost;
      fields.display_code = l.code ?? orderedEntry.code ?? null;
      if (isSub) {
        fields.substitute_for = {
          code: orderedEntry.code,
          description: orderedEntry.description,
          unit: orderedEntry.unit,
          quantity_ordered: orderedEntry.quantity_ordered,
          unit_cost: orderedEntry.unit_cost,
        };
      }
      undoFields.on_order = l.on_order ?? null;
      undoFields.quantity_ordered = l.quantity_ordered ?? null;
      undoFields.reference_cost = l.reference_cost ?? null;
      undoFields.display_code = l.display_code ?? null;
      undoFields.substitute_for = l.substitute_for ?? null;
      headerFields = { ordered_not_received: onr.filter((o) => o !== orderedEntry) };
      undoHeader = { ordered_not_received: onr };
    }
    const log = [
      ...(doc.actioned_suggestions || []).filter((a) => a.key !== `item:${l.id}`),
      {
        key: `item:${l.id}`,
        summary: `${l.display_code || l.code || '?'} · ${l.description ?? ''}: linked to '${itemName ?? ''}' — saved to Loaded on receive`,
        undo_fields: undoFields,
        ...(undoHeader ? { undo_header: undoHeader } : {}),
      },
    ];
    setDoc((prev) => ({
      ...prev,
      ...(headerFields ?? {}),
      actioned_suggestions: log,
      lines: prev.lines.map((x, i) => (i === idx ? { ...x, ...fields } : x)),
    }));
    if (workingDocId) patchDoc([
      { op: 'update_line', index: idx, fields },
      { op: 'update_header', fields: { ...(headerFields ?? {}), actioned_suggestions: log } },
    ]);
    setItemForm(null);
    setLinkQuery('');
  };
  const undoActionedFields = (a: { key: string; undo_fields?: Partial<Line>; undo_header?: Record<string, unknown> }) => {
    const lineId = a.key.slice(a.key.indexOf(':') + 1);
    const idx = doc.lines.findIndex((l) => l.id === lineId);
    const log = (doc.actioned_suggestions || []).filter((x) => x.key !== a.key);
    setDoc((prev) => ({
      ...prev,
      ...((a.undo_header as Partial<DocData>) ?? {}),
      actioned_suggestions: log,
      lines: idx >= 0 ? prev.lines.map((x, i) => (i === idx ? { ...x, ...a.undo_fields } : x)) : prev.lines,
    }));
    if (workingDocId) patchDoc([
      ...(idx >= 0 ? [{ op: 'update_line', index: idx, fields: a.undo_fields as Record<string, unknown> }] : []),
      { op: 'update_header', fields: { ...(a.undo_header ?? {}), actioned_suggestions: log } },
    ]);
  };

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
      // The item now exists in Loaded (with its supplier variant); the LINE
      // links locally and lands in Loaded on receive.
      const out = await res.json();
      if (out?.item_id) {
        applyLocalLink(itemForm.lineId, out.item_id, out.item_name ?? itemForm.name);
      }
      setItemForm(null);
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Could not create the stock item');
    } finally {
      setCreatingItem(false);
    }
  };

  // Link the line to an EXISTING Loaded item (the LLM's suggested match, or
  // one the user searched for). LOCAL — see applyLocalLink.
  const linkItem = (lineId: string, itemId: string) => {
    if (embedded) return;
    const l = doc.lines.find((x) => x.id === lineId);
    const cat = stockItems.find((s) => s.id === itemId);
    const matched = l?.matched_item?.id === itemId ? l.matched_item : null;
    applyLocalLink(lineId, itemId, cat?.name ?? matched?.name ?? null);
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
          subtotal: doc.subtotal,
          tax_amount: doc.tax_amount,
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
      // The server already marked this invoice's docs received (and bumped
      // their versions) as part of the receive — ADOPT that state rather than
      // patching it again: the old set_status patch raced the server's bump
      // and produced a false "updated elsewhere" conflict right after a
      // successful receive. Fallback patch only if the server didn't mark it.
      if (docUrl) {
        const own = await apiFetch(docUrl).then((r2) => (r2.ok ? r2.json() : null)).catch(() => null);
        if (own?.data) {
          setDoc(own.data as DocData);
          if (typeof own.version === 'number') setVersion(own.version);
          if (!own.data.is_received) patchDoc([{ op: 'set_status', value: 'received' }]);
        }
      } else if (workingDocId) {
        patchDoc([{ op: 'set_status', value: 'received' }]);
      }
      (props?.onReceived as ((id: string) => void) | undefined)?.(doc.invoice_id);
      // Received from the chat overlay: the job is done — close the modal and
      // let the in-flow card show the received state.
      setExpandedFull(false);
      // Tell sibling cards: their duplicate/PO checks may have just flipped
      // (the server cleared conflicting cached reviews) and twin docs of this
      // invoice are now received.
      if (!embedded && venueId) window.dispatchEvent(new CustomEvent(INVOICE_ACTIONED_EVENT, {
        detail: { venueId, invoiceId: doc.invoice_id, sourceDocId: workingDocId },
      }));
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Failed');
    }
  };

  // The document no longer exists (drafts deleted before tombstones were
  // introduced were removed outright) — say so instead of spinning forever.
  if (missingDoc) {
    return (
      <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, background: '#fbfaf8', padding: '1rem', fontSize: '0.8rem', color: '#888' }}>
        ✓ This invoice draft no longer exists — it was deleted from Loaded (a supplier statement or duplicate).
      </div>
    );
  }

  // The block carries only a working_document_id until the draft loads; show a
  // loading state rather than an empty card in that gap.
  if (docUrl && !doc.invoice_id) {
    return (
      <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, background: '#fff', padding: '1rem', fontSize: '0.8rem', color: '#888' }}>
        Opening the invoice…
      </div>
    );
  }

  const card = (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,0.04)', overflow: 'hidden', ...(overlay ? { width: 'min(1200px, 96vw)', margin: '0 auto' } : {}) }}>
      {/* Header — editable form (Loaded-parity) */}
      <div style={{ padding: '0.7rem 0.9rem', background: 'linear-gradient(#faf9f7,#f5f3ef)', borderBottom: '1px solid #eee' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
          <span style={{ fontSize: '0.9rem', fontWeight: 700, color: '#3a3a3a' }}>Receive Invoice</span>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            {compact && (
              <button type="button" onClick={() => setExpandedFull((v) => !v)}
                style={{ fontSize: '0.66rem', padding: '2px 9px', border: '1px solid #d8d4cc', borderRadius: 4, background: '#fff', color: '#6b6b6b', cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
                {expandedFull ? 'Hide details ▾' : 'Show full invoice ▸'}
              </button>
            )}
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
        </div>
        {collapsed && (
          <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap', fontSize: '0.8rem' }}>
            <strong style={{ color: '#3a3a3a' }}>{doc.supplier_name || '—'}</strong>
            <span style={{ color: '#666' }}>{doc.reference_number || '(no number)'}</span>
            {doc.issued_at && <span style={{ color: '#999' }}>{dateVal(doc.issued_at)}</span>}
            <span style={{ fontSize: '0.66rem', color: '#8a6d3b' }}>{checkSummary}</span>
            <strong style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums' }}>{cur(doc.total)}</strong>
          </div>
        )}
        {/* Two columns like Loaded: order/supplier on the left, invoice on the
            right. Collapses to one column when the card is narrow. */}
        {!collapsed && (<>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '0.5rem 2rem' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label style={fieldCol}>
              <span style={microLabel}>Supplier</span>
              {/* An unlinked supplier (Loaded's feed didn't match the printed
                  name to a supplier record) renders AMBER — the fallback
                  option showing the printed name used to mask it, and Loaded's
                  server 500s on receiving a supplier-less invoice. */}
              <select value={doc.linked_supplier_id || ''} disabled={done} onChange={(e) => onSupplier(e.target.value)}
                style={{ ...inputStyle, width: '100%', ...(!done && !doc.linked_supplier_id && doc.invoice_id ? { border: '1px solid #f0c88a', background: '#fff4e5' } : {}) }}>
                {!suppliers.some((s) => s.id === doc.linked_supplier_id) && (
                  <option value={doc.linked_supplier_id || ''}>{doc.supplier_name || 'Select supplier'}</option>
                )}
                {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
              {!done && !embedded && doc.invoice_id && !doc.linked_supplier_id && (
                <span style={{ fontSize: '0.6rem', color: '#c0392b', marginTop: 2 }}>
                  {suggestedSupplier ? (
                    <>
                      not linked to a Loaded supplier —{' '}
                      <button type="button" onClick={() => applySupplierSuggestion(suggestedSupplier)}
                        style={{ border: 'none', background: 'none', color: '#8a2f2f', textDecoration: 'underline', cursor: 'pointer', padding: 0, font: 'inherit' }}>
                        use {suggestedSupplier.name}
                      </button>
                      {' '}(saved to Loaded on receive)
                    </>
                  ) : (
                    'not linked to a Loaded supplier — pick one before receiving'
                  )}
                </span>
              )}
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
                  copy says {suggestedPo.po_number ?? '?'} — links on Accept or receive
                </span>
              )}
              {/* The review read a buyer PO off the copy but it matched NO
                  Loaded purchase order (cancelled / never raised / different
                  number). Show the number so the user can chase it — the raw
                  reason otherwise only carried the supplier's own ref. */}
              {!suggestedPo && !doc.linked_purchase_order_id && doc.po_unresolved && doc.copy_po && !done && (
                <span style={{ fontSize: '0.6rem', color: '#c0392b', marginTop: 2 }}
                  title="the order number printed on the invoice copy doesn't match any purchase order in Loaded — check the PO exists, or pick one manually">
                  copy says {doc.copy_po} — no matching purchase order found in Loaded
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
                style={{ ...inputStyle, width: '100%', fontWeight: 600, ...(!done && totalSuggestPending ? { border: '1px solid #b78a2f', background: '#fdf6e7' } : {}) }} />
              {!done && totalSuggestPending && (
                <span style={{ fontSize: '0.6rem', color: '#b78a2f', marginTop: 2 }}>
                  copy says {cur(doc.copy_total)} —{' '}
                  <button type="button" onClick={applyTotalSuggestion}
                    style={{ border: 'none', background: 'none', color: '#8a6d3b', textDecoration: 'underline', cursor: 'pointer', padding: 0, font: 'inherit' }}>
                    use
                  </button>
                </span>
              )}
            </label>
          </div>
        </div>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: '0.5rem', fontSize: '0.72rem', color: '#555' }}>
          <input type="checkbox" checked={includesTax} disabled={done}
            onChange={(e) => patchHeader({ unit_cost_includes_tax: e.target.checked })} />
          Line item costs include tax
        </label>
        </>)}
      </div>

      {/* Lines → Notes: the full-detail body, hidden while the compact card is
          collapsed ("Show full invoice" reveals it). */}
      {!collapsed && (<>
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
              // An ACCEPTED strike (or remove) keeps the row VISIBLE, crossed
              // out and inert: every suggestion, hint, badge and affordance on
              // the line is suppressed, it drops out of the totals, and it is
              // soft-deleted on receive. Undo lives on its ✓ suggestion row.
              const struck = !!l.struck;
              return (
              <Fragment key={l.id}>
              {/* A SUGGESTED strike (copy_duplicate) renders the row struck-
                  through as a preview; an ACCEPTED strike renders it greyed and
                  struck-through with no affordances. */}
              <tr style={{ borderTop: '1px solid #f3f3f3', ...(struck ? { opacity: 0.5, textDecoration: 'line-through', color: '#999' } : l.copy_duplicate ? { opacity: 0.6, textDecoration: 'line-through', background: '#fffdf5' } : {}) }}>
                <td style={{ padding: '0.4rem 0.6rem', color: '#666' }}>{l.display_code || l.code || '—'}</td>
                <td style={{ padding: '0.4rem 0.6rem' }}>
                  {l.item_name || l.description}
                  {!done && !struck && l.copy_duplicate && (
                    <button type="button"
                      onClick={() => applyStrikeSuggestion(idx)}
                      title="$0 duplicate of another line — accept to remove it from the receive"
                      style={{ marginLeft: 6, fontSize: '0.58rem', color: '#c0392b', background: '#fdecea', border: '1px solid #f5c6c0', borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap', fontFamily: 'inherit', fontWeight: 600, cursor: 'pointer', textDecoration: 'none' }}>
                      $0 duplicate — accept strike
                    </button>
                  )}
                  {!done && !struck && l.copy_missing && !l.copy_duplicate && (
                    <button type="button"
                      onClick={() => applyRemoveSuggestion(idx)}
                      title="This line is not on the attached invoice copy — accept to remove it from the receive"
                      style={{ marginLeft: 6, fontSize: '0.58rem', color: '#c0392b', background: '#fdecea', border: '1px solid #f5c6c0', borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap', fontFamily: 'inherit', fontWeight: 600, cursor: 'pointer', textDecoration: 'none' }}>
                      not on copy — remove
                    </button>
                  )}
                  {/* Delivered under a different code than ordered — a substitute.
                      Click the badge to expand the original ordered line below. */}
                  {l.substitute_for && !struck && (
                    <button type="button"
                      onClick={() => setOpenSub((prev) => { const n = new Set(prev); if (n.has(l.id)) n.delete(l.id); else n.add(l.id); return n; })}
                      title="delivered under a different stock code than ordered — click to show the ordered line"
                      style={{ marginLeft: 6, fontSize: '0.58rem', color: '#8a6d3b', background: '#fdf6e7', border: '1px solid #e6d3a3', borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap', fontFamily: 'inherit', fontWeight: 600, cursor: 'pointer' }}>
                      substitute {openSub.has(l.id) ? '▾' : '▸'}
                    </button>
                  )}
                  {/* (No "≠ copy" badge here: cost/total copy differences are
                      already explained in Needs Attention, and a badge on the
                      description that describes the totals only confused.) */}
                  {/* Stock item not linked in Loaded. The review engine's item
                      match rides in on the line (matched_item / suggested_name /
                      suggested_group_id): offer an existing item to LINK, else
                      CREATE it (with its group). Must be resolved to receive. */}
                  {!l.linked_item_id && !embedded && !done && !struck && !dojo && (() => {
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
                  {!l.linked_item_id && (embedded || done) && !struck && !dojo && (
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
                  {l.brand && !l.linked_brand_id && !struck && (
                    <span title="this brand doesn't exist in Loaded — it would be created as NEW on receive" style={newBadge}>NEW</span>
                  )}
                </td>
                <td style={{ padding: '0.4rem 0.6rem' }}>
                  <select value={l.linked_unit_id || ''} disabled={done || struck}
                    onChange={(e) => onUnit(idx, e.target.value)}
                    style={{ ...inputStyle, minWidth: 120, borderColor: struck ? '#e2e2e2' : recommended ? '#b78a2f' : (!l.linked_unit_id ? '#f0c88a' : (l.copy_unit_mismatch ? '#c0392b' : '#d1d5db')), background: struck ? '#fafafa' : recommended && !done ? '#fdf6e7' : (!l.linked_unit_id && !done ? '#fff4e5' : '#fff') }}>
                    {!units.some((u) => u.id === l.linked_unit_id) && (
                      <option value={l.linked_unit_id || ''}>{l.unit || 'Select unit'}</option>
                    )}
                    {sortedUnits.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                  {/* Only a genuine unit mismatch the review found — the copy's
                      delivered unit (guideline-derived) resolves to a different
                      Loaded unit than the line currently has. A 2L bottle already
                      on "2 L" shows nothing. */}
                  {recommended && !done && !struck && (
                    <div style={{ fontSize: '0.6rem', color: '#b78a2f', marginTop: 2 }}>
                      copy says {l.recommended_unit} —{' '}
                      <button type="button" onClick={() => applyUnitSuggestion(idx, recommended)}
                        style={{ border: 'none', background: 'none', color: '#8a6d3b', textDecoration: 'underline', cursor: 'pointer', padding: 0, font: 'inherit' }}>
                        use {recommended.name}
                      </button>
                    </div>
                  )}
                  {!l.linked_unit_id && !struck && !dojo && (
                    <div style={{ fontSize: '0.58rem', color: '#b45309', marginTop: 2 }}>NEW unit — not in Loaded</div>
                  )}
                  {/* The copy carries a size but it can't be read — never guess:
                      ask the user to confirm (the suggestion row's Confirm), or
                      pick a different unit here. */}
                  {l.unit_needs_confirmation && !done && !struck
                    && l.linked_unit_id === l.original_unit_id
                    && !(doc.actioned_suggestions || []).some((a) => a.key === `confirm-unit:${l.id}`) && (
                    <div style={{ fontSize: '0.58rem', color: '#c0392b', marginTop: 2 }} title="the unit on the invoice copy is cut off or unreadable — confirm the current unit or pick the right one">
                      unit unreadable on copy — confirm below
                    </div>
                  )}
                  {/* The copy's DELIVERED unit doesn't resolve to ANY Loaded
                      unit (e.g. "49.5L", a multipack like "4x6 pack") — offer
                      to CREATE it in Loaded and use it on the line (local
                      until receive), exactly like create-item. Also covers a
                      line with NO unit set at all (linked_unit_id null): the
                      copy's unit is then a straight suggestion. */}
                  {!recommended && !struck && l.copy_unit_mismatch && l.recommended_unit && (
                    <div style={{ fontSize: '0.58rem', color: '#c0392b', marginTop: 2 }} title="the copy's delivered unit doesn't exist in Loaded — accepting creates it, then it's applied to the line on receive">
                      copy delivered unit “{l.recommended_unit}” —{' '}
                      {!done && !embedded && l.linked_unit_id === l.original_unit_id ? (
                        creatingUnitLine === l.id ? (
                          <span style={{ color: '#8a2f2f' }}>creating unit…</span>
                        ) : confirmUnitLine === l.id ? (
                          <span style={{ color: '#8a2f2f' }}>
                            creates a NEW unit “{l.recommended_unit}” in Loaded —{' '}
                            <button type="button" onClick={() => createUnitAndApply(idx)}
                              style={{ border: 'none', background: 'none', color: '#8a2f2f', textDecoration: 'underline', cursor: 'pointer', padding: 0, font: 'inherit', fontWeight: 700 }}>
                              create it
                            </button>
                            {' · '}
                            <button type="button" onClick={() => setConfirmUnitLine(null)}
                              style={{ border: 'none', background: 'none', color: '#888', textDecoration: 'underline', cursor: 'pointer', padding: 0, font: 'inherit' }}>
                              cancel
                            </button>
                          </span>
                        ) : (
                          <button type="button" onClick={() => createUnitAndApply(idx)}
                            style={{ border: 'none', background: 'none', color: '#8a2f2f', textDecoration: 'underline', cursor: 'pointer', padding: 0, font: 'inherit' }}>
                            use {l.recommended_unit} (new unit)
                          </button>
                        )
                      ) : l.linked_unit_id ? (
                        'differs from Loaded'
                      ) : (
                        'no unit set in Loaded'
                      )}
                    </div>
                  )}
                </td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', color: '#888', fontVariantNumeric: 'tabular-nums' }}>
                  {l.quantity_ordered ?? '—'}
                </td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>
                  {/* Amber = a qty suggestion is pending (same treatment as the
                      unit dropdown); clears as soon as the qty matches the copy.
                      Full `border` shorthand — never mix with borderColor. */}
                  <input type="number" step="any" value={l.quantity_received ?? 0} disabled={done || struck}
                    onChange={(e) => onQty(idx, parseFloat(e.target.value) || 0)}
                    style={{ ...inputStyle, width: 70, textAlign: 'right', ...(!done && !struck && l.copy_quantity_mismatch && l.copy_quantity != null && Math.abs((l.quantity_received ?? 0) - l.copy_quantity) > 0.001 ? { border: '1px solid #b78a2f', background: '#fdf6e7' } : {}) }} />
                  {/* The review DECIDED the copy states a different received qty
                      (copy_quantity_mismatch) — the component only renders it as a
                      one-click edit, and hides it once the qty already matches. */}
                  {!done && !struck && l.copy_quantity_mismatch && l.copy_quantity != null && Math.abs((l.quantity_received ?? 0) - l.copy_quantity) > 0.001 && (
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
                  {!struck && l.reference_cost != null && (l.unit_cost ?? 0) > l.reference_cost + 0.001 && (
                    <span title={`up from ${cur(l.reference_cost)} on the order`} style={{ color: '#c0392b', marginRight: 3 }}>↑</span>
                  )}
                  <input type="number" step="any" value={l.unit_cost ?? 0} disabled={done || struck}
                    onChange={(e) => onCost(idx, parseFloat(e.target.value) || 0)}
                    style={{ ...inputStyle, width: 80, textAlign: 'right', ...(!done && !struck && l.copy_unit_cost_mismatch && l.copy_unit_price != null && Math.abs((l.unit_cost ?? 0) - l.copy_unit_price) > 0.005 ? { border: '1px solid #b78a2f', background: '#fdf6e7' } : {}) }} />
                  {/* The review DECIDED the copy prices this line differently —
                      one-click edit, hidden once the cost matches. */}
                  {!done && !struck && l.copy_unit_cost_mismatch && l.copy_unit_price != null && Math.abs((l.unit_cost ?? 0) - l.copy_unit_price) > 0.005 && (
                    <div style={{ fontSize: '0.6rem', color: '#b78a2f', marginTop: 2 }}>
                      copy: {cur(l.copy_unit_price)} —{' '}
                      <button type="button" onClick={() => applyCostSuggestion(idx)}
                        style={{ border: 'none', background: 'none', color: '#8a6d3b', textDecoration: 'underline', cursor: 'pointer', padding: 0, font: 'inherit' }}>
                        use
                      </button>
                    </div>
                  )}
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
              {l.substitute_for && !struck && openSub.has(l.id) && (
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
      </>)}

      {/* Dojo banner: run status + every mismatch vs the stored baseline
          (incl. header/missing/extra-line diffs that have no line row to
          light up). Replaces suggestions/validation/receive in dojo mode. */}
      {dojo && (
        <div style={{ padding: '0.55rem 0.9rem', borderTop: '1px solid #eee' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: (doc.dojo_diffs?.length ?? 0) > 0 ? 6 : 0 }}>
            {(() => {
              const st = doc.dojo_status || 'new';
              const palette: Record<string, { bg: string; fg: string; label: string; note: string }> = {
                pass: { bg: '#d1fae5', fg: '#065f46', label: 'PASS', note: 'matches the stored expected extraction' },
                fail: { bg: '#fee2e2', fg: '#991b1b', label: 'FAIL', note: `${doc.dojo_diffs?.length ?? 0} mismatch${(doc.dojo_diffs?.length ?? 0) === 1 ? '' : 'es'} vs the expected extraction` },
                error: { bg: '#fee2e2', fg: '#991b1b', label: 'ERROR', note: 'the extraction run failed' },
                new: { bg: '#fdf6e7', fg: '#8a6d3b', label: 'NO BASELINE', note: 'review the extracted values, then Save as expected' },
              };
              const p = palette[st] || palette.new;
              return (
                <>
                  <span style={{ fontSize: '0.62rem', fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: p.bg, color: p.fg }}>{p.label}</span>
                  <span style={{ fontSize: '0.7rem', color: '#777' }}>{p.note}</span>
                </>
              );
            })()}
          </div>
          {(doc.dojo_diffs || []).map((d, i) => (
            <div key={`dj-${i}`} style={{ fontSize: '0.66rem', color: '#c0392b', display: 'flex', gap: 6, padding: '1px 0' }}>
              <span>✗</span>
              <span>
                {d.line != null ? `Line ${d.line}${d.description ? ` · ${d.description}` : ''}: ` : ''}
                {d.field === 'line_missing' ? 'missing from this run'
                  : d.field === 'line_extra' ? 'extra line not in the baseline'
                  : `${d.field} — expected ${JSON.stringify(d.expected ?? null)}, got ${JSON.stringify(d.actual ?? null)}`}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Suggested changes + what needs attention — always visible (no expand),
          so the user sees non-validations and any fix we made at a glance.
          Rows come from suggestionRows — ONE derivation shared with the inline
          affordances — pending (●, Accept) first, then applied (✓, persisted). */}
      {!dojo && (suggestionRows.length > 0 || (!done && checks.length > 0 && failChecks.length > 0)) && (
        <div style={{ padding: '0.55rem 0.9rem', borderTop: '1px solid #eee' }}>
          {suggestionRows.length > 0 && (
            <div style={{ marginBottom: !done && failChecks.length ? 8 : 0 }}>
              <div style={{ ...microLabel, color: '#8a6d3b', marginBottom: 3 }}>Suggested changes</div>
              {suggestionRows.map((row) => {
                const applied = row.state === 'applied';
                const dismissed = row.state === 'dismissed';
                // Received/deleted: a still-pending row was NOT actioned — the
                // user received past it. Label it so the record reads as
                // actioned-vs-not, never as still awaiting a decision.
                const notActioned = row.state === 'pending' && done;
                const accept = row.engineFix ? () => acceptFix(row.key, row.engineFix!) : row.accept;
                return (
                  <div key={row.key} style={{ fontSize: '0.68rem', color: applied ? '#2e7d4f' : dismissed || notActioned ? '#9ca3af' : '#8a6d3b', display: 'flex', gap: 8, padding: '2px 0', alignItems: 'center' }}>
                    <span>{applied ? '✓' : dismissed ? '⊘' : notActioned ? '○' : '●'}</span>
                    <span style={{ flex: 1, ...(applied ? { textDecoration: 'line-through', color: '#9ca3af' } : {}), ...(dismissed || notActioned ? { color: '#9ca3af' } : {}) }}>
                      {row.summary}
                      {dismissed && !row.key.includes('confirm-unit:') && <span style={{ fontStyle: 'italic' }}> — dismissed</span>}
                      {notActioned && <span style={{ fontStyle: 'italic' }}> — not actioned</span>}
                    </span>
                    {(applied || dismissed) && row.undo && !done && !embedded && (
                      <button type="button" onClick={row.undo}
                        title={dismissed ? 'restore this suggestion' : 'undo this change'}
                        style={{ fontSize: '0.62rem', padding: '2px 10px', border: '1px solid #ccc', background: '#fff', color: '#666', borderRadius: 4, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                        Undo
                      </button>
                    )}
                    {row.state === 'pending' && !done && !embedded && (
                      <span style={{ display: 'flex', gap: 4 }}>
                        {accept && (
                          <button type="button" onClick={accept} disabled={accepting !== null}
                            style={{ fontSize: '0.62rem', padding: '2px 10px', border: '1px solid #b78a2f', background: accepting === row.key ? '#f0e6cc' : '#fff', color: '#8a6d3b', borderRadius: 4, cursor: accepting !== null ? 'default' : 'pointer', whiteSpace: 'nowrap', opacity: accepting !== null && accepting !== row.key ? 0.5 : 1 }}>
                            {accepting === row.key ? 'Applying…' : (row.acceptLabel ?? 'Accept')}
                          </button>
                        )}
                        {row.dismiss && (
                          <button type="button" onClick={row.dismiss} disabled={accepting !== null}
                            title={row.dismissLabel ? 'confirm and continue' : 'decline this suggestion without applying it'}
                            style={{ fontSize: '0.62rem', padding: '2px 10px', border: '1px solid #ccc', background: '#fff', color: '#888', borderRadius: 4, cursor: accepting !== null ? 'default' : 'pointer', whiteSpace: 'nowrap' }}>
                            {row.dismissLabel ?? 'Dismiss'}
                          </button>
                        )}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {/* Hidden once received/deleted: the failures were either fixed by
              the actioned changes or knowingly accepted at Receive — after
              that, the actioned-vs-not record above is the story. */}
          {!done && failChecks.length > 0 && (
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
                const resolved = newValuesResolvedByEdits
                  && (r.includes('would be created as NEW') || r.includes('not linked in Loaded yet'));
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

      {/* Validation — the collapsed strip already shows checkSummary */}
      {!collapsed && !dojo && (
      <details style={{ borderTop: '1px solid #eee' }}>
        <summary style={{ padding: '0.45rem 0.9rem', fontSize: '0.68rem', color: '#666', cursor: 'pointer', userSelect: 'none' }}>
          Validation ({checkSummary})
          {!embedded && !done && (
            <button type="button"
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); resetValidation(); }}
              disabled={reviewing}
              title="wipe the cached review, extraction and action log for this invoice and re-run validation from scratch"
              style={{ marginLeft: 8, fontSize: '0.6rem', padding: '1px 8px', border: '1px solid #d8d4cc', borderRadius: 4, background: '#fff', color: '#8a8a8a', cursor: reviewing ? 'default' : 'pointer', fontFamily: 'inherit' }}>
              {reviewing ? 'resetting…' : 'reset validation'}
            </button>
          )}
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
                  const resolvedByEdits = (key === 'items_matched' && newValuesResolvedByEdits) || resolvedLocally.has(key);
                  const state = resolvedByEdits ? 'pass' : c ? c.state : 'skip';
                  const color = state === 'pass' ? '#2e7d4f' : state === 'fail' ? '#c0392b' : state === 'suggest' ? '#b78a2f' : '#9ca3af';
                  const icon = state === 'pass' ? '✓' : state === 'fail' ? '✗' : state === 'suggest' ? '●' : '—';
                  return (
                    <div key={key} style={{ fontSize: '0.64rem', display: 'flex', gap: 6, color }}>
                      <span style={{ width: 8 }}>{icon}</span>
                      <span>
                        {CHECK_LABEL[key]}
                        {state === 'suggest' ? ' — suggested change' : ''}
                        {resolvedByEdits
                          ? key === 'items_matched'
                            ? ' — resolved by your edits (nothing NEW will be created)'
                            : ' — resolved by your edits (applied on receive)'
                          : ''}
                      </span>
                    </div>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </details>
      )}

      {/* Footer */}
      {!dojo && (
      <div style={{ padding: '0.6rem 0.9rem', borderTop: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.75rem' }}>
        <span style={{ flex: 1, fontSize: '0.72rem', color: status === 'error' ? '#c0392b' : done ? '#2e7d4f' : (!done && (unresolved.length > 0 || supplierBlocking)) ? '#b45309' : '#888' }}>
          {status === 'error' ? `✗ ${message}`
            : draftDeleted ? `✓ Draft deleted from Loaded${doc.deleted_reason ? ` — ${doc.deleted_reason}` : ' — this document was a supplier statement or duplicate.'}`
            : done ? '✓ Received in Loaded.'
            : status === 'saving' ? 'Receiving…'
            : noLines
              ? 'Nothing to receive — this draft has no line items. If it isn’t an invoice, accept the delete suggestion instead.'
            : supplierBlocking
              ? 'No Loaded supplier linked — pick the supplier before receiving.'
            : unresolved.length > 0
              ? `${unresolved.length} line${unresolved.length > 1 ? 's have' : ' has'} a NEW item or unit — link or pick an existing one on the line before receiving.`
              : suggestionsBlocking
                ? `${blockingSuggestions.length} suggested change${blockingSuggestions.length > 1 ? 's' : ''} need${blockingSuggestions.length > 1 ? '' : 's'} a decision — Accept or Dismiss each, then receive.`
                : newValuesResolvedByEdits
                  ? 'Your edits resolved the NEW values — receive writes the units/items you picked; nothing new is created in Loaded.'
                  : checkSummary === 'all checks pass'
                    ? 'All checks passed — ready to receive.'
                    : 'Review the changes, then accept to update Loaded and receive.'}
        </span>
        {overlay && (
          <button type="button" onClick={() => setExpandedFull(false)}
            style={{ padding: '0.4rem 1rem', fontSize: '0.78rem', border: '1px solid #d8d4cc', borderRadius: 6, background: '#fff', color: '#666', cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
            Close
          </button>
        )}
        {!done && (
          <button onClick={accept} disabled={receiveBlocked}
            title={
              noLines
                ? 'This draft has no line items — nothing to receive'
                : supplierBlocking
                ? 'Pick the supplier first — Loaded can’t receive a supplier-less invoice'
                : unresolved.length > 0
                  ? 'Create the NEW items/units in Loaded first'
                  : suggestionsBlocking
                    ? 'Resolve the suggested changes first — Accept or Dismiss each'
                    : undefined
            }
            style={{ padding: '0.4rem 1.1rem', fontSize: '0.78rem', fontWeight: 500, border: 'none', borderRadius: 6, cursor: receiveBlocked ? 'not-allowed' : 'pointer', background: '#2e7d4f', color: '#fff', fontFamily: 'inherit', opacity: receiveBlocked ? 0.5 : 1, whiteSpace: 'nowrap' }}>
            {status === 'saving' ? 'Receiving…' : 'Accept & Receive'}
          </button>
        )}
      </div>
      )}
    </div>
  );

  if (!overlay) return card;
  return (
    <>
      {/* In-flow stub keeps the thread from jumping while the full editor is
          open in the overlay. */}
      <div style={{ border: '1px dashed #d8d4cc', borderRadius: 10, padding: '0.55rem 0.9rem', fontSize: '0.72rem', color: '#8a8a8a', background: '#fbfaf8' }}>
        {doc.supplier_name || '—'} · {doc.reference_number || '(no number)'} — open in the expanded view
      </div>
      <div
        onClick={() => setExpandedFull(false)}
        style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(30,28,24,0.4)', overflowY: 'auto', padding: '2rem 1rem' }}>
        {/* Constrained to the card's width so clicks BESIDE the card hit the
            backdrop (a full-width block here used to swallow them — closing
            only worked below the card). */}
        <div onClick={(e) => e.stopPropagation()} style={{ maxWidth: 1100, margin: '0 auto' }}>{card}</div>
      </div>
    </>
  );
}
