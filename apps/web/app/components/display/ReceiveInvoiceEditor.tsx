'use client';

/**
 * Working-document-backed "Receive Invoice" editor — replica_v1.
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
 * The SERVER computes everything (app/services/invoice_review.py): the doc's
 * top-level working values are Loaded's draft + accepted suggestions + manual
 * edits; `suggestions` / `issues` / `confidence` / the `replica` sidecar are
 * the review's verdicts. This component is a pure renderer + value-applier —
 * it evaluates the tiny clears_when predicate and recomputes a line total on
 * accept (plain arithmetic), and derives NOTHING else.
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch, getStoredUser } from '../../lib/api';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface Line {
  id: string;
  code: string | null;
  description: string | null;
  brand?: string | null;
  unit: string | null;
  // Extracted x-ray only: the derived delivered unit of ONE item (the field
  // the dojo grades), shown beside the printed unit in its own column.
  unit_of_measure?: string | null;
  unit_unrecognisable?: boolean | null;
  linked_unit_id: string | null;
  original_unit_id?: string | null;
  unit_ratio: number | null;
  quantity_ordered?: number | null;
  // LOADED-mirror only (loaded_snapshot lines): what Loaded's own Receive
  // Invoice screen resolves against the linked order — its API returns none
  // of it. Server-filled; absent on working lines.
  unit_name?: string | null;
  item_is_new?: boolean | null;
  unit_is_new?: boolean | null;
  quantity_received: number | null;
  unit_cost: number | null;
  total_cost: number | null;
  tax_amount?: number | null;
  sale_tax_rate?: number | null;
  linked_item_id: string | null;
  linked_brand_id?: string | null;
  item_type?: string | null;
  // Reference cost from the linked PO (attached on open) — a red ↑ shows when
  // the invoice unit cost is higher.
  reference_cost?: number | null;
  // Matched to a line on the linked PO (Loaded's own reconciliation).
  on_order?: boolean | null;
  // The LINKED stock item's name — what Loaded's "Stock Item Description"
  // column shows for linked lines (resolved server-side at draft open).
  item_name?: string | null;
  item_name_for?: string | null;
  // The code Loaded shows: the line's own code, else the linked item's code
  // from the PO line when the line carries none.
  display_code?: string | null;
  // Set when this delivery came in under a DIFFERENT code than ordered (a
  // substitute): the original ordered PO line, shown as an expandable row.
  substitute_for?: OrderedNotReceived | null;
  // Applied state: the line is excluded from the totals and soft-deleted from
  // the receive (a $0 artifact / a line the copy doesn't bill).
  struck?: boolean | null;
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

// The server's review verdicts (invoice_review.py) — rendered, never derived.
interface Suggestion {
  id: string;
  kind: 'line_value' | 'add_line' | 'strike' | 'header_value' | 'supplier'
    | 'link_po' | 'unlink_po' | 'split_reference' | 'delete_invoice'
    | 'create_unit' | 'create_item' | 'create_brand' | string;
  field?: string | null;
  line_id?: string | null;
  current?: unknown;
  proposed?: unknown;
  explanation: string;
  // Field patch to accept: line_id present → the line, else the header.
  apply?: Record<string, unknown>;
  // add_line: the full line to append. delete_invoice: the /accept fix body.
  payload?: Record<string, unknown>;
  // Set when accepting this suggestion resolves a blocking ISSUE (e.g. a
  // totals correction): accepting the LAST pending suggestion carrying the
  // same value records that issue accepted too.
  resolves?: string | null;
}
interface ClearsWhen {
  scope: 'line' | 'header';
  line_id?: string;
  field: string;
  op: 'not_null' | 'truthy' | 'equals';
  value?: unknown;
}
// The button on a blocker row, and the venue toggle each one maps to. Kept
// beside the Issue type because they describe the same contract: a blocker
// says what is missing, offers the one action that supplies it, and names the
// setting that would let Norm do it without being asked.
const ISSUE_ACTION_LABELS: Record<string, string> = {
  create_item: 'Create item',
  create_unit: 'Create unit',
  create_brand: 'Create brand',
  create_supplier: 'Create supplier',
  guess_unit: 'Choose a unit',
  // Record-only decisions: Accept writes nothing to Loaded, it records the
  // call the row describes (runIssueAction's fall-through does exactly that).
  receive_without_po: 'Receive without a PO',
  confirm_unit: 'Confirm this unit',
  receive_unreconciled_totals: 'Receive on the values as read',
  strike: 'Strike this line',
  delete_invoice: 'Delete this draft',
  delete_non_invoice: 'Delete this draft',
  delete_unreadable: 'Delete this draft',
};
const gateLabels: Record<string, string> = {
  auto_create_units: 'auto-create units',
  auto_create_items: 'auto-create stock items',
  auto_create_brands: 'auto-create brands',
  auto_create_suppliers: 'auto-create suppliers',
  receive_without_unit: 'receive when no unit can be found',
  receive_with_unconfirmed_unit: 'receive when the unit came from Loaded rather than the copy',
  receive_without_po: 'receive without a valid purchase order',
  receive_unreconciled_totals: "receive when the copy's totals don't reconcile",
  auto_strike_phantom_lines: "strike lines the copy doesn't bill",
  auto_delete_duplicates: 'delete a duplicate invoice draft',
  auto_delete_non_invoices: "delete drafts that aren't invoices",
  auto_delete_unreadable: 'delete drafts with no readable invoice copy',
};

interface Issue {
  id: string;
  code: string;
  blocking: boolean;
  line_id?: string | null;
  message: string;
  data?: Record<string, unknown> | null;
  clears_when?: ClearsWhen;
  // What clears this blocker, and which venue toggle lets Norm do it alone.
  // The create actions used to ride as SUGGESTIONS as well, so one
  // un-catalogued line showed a suggestion row, a blocking issue, a NEW badge
  // and a disabled button — four things for one decision. Now the blocker is
  // the single place it appears, and it carries the button.
  action?: { kind: string; payload?: Record<string, string | undefined> } | null;
  gate?: string | null;
}
// THE RECORD: every accept/dismiss/undo, human or autopilot (by: "norm").
interface SuggestionAction {
  suggestion_id: string;
  action: 'accepted' | 'dismissed' | 'undone';
  by: 'user' | 'norm';
  at?: string;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
}

interface DocData {
  invoice_id: string;
  reference_number: string | null;
  supplier_name: string | null;
  linked_supplier_id: string | null;
  purchase_order_number: string | null;
  linked_purchase_order_id: string | null;
  // Split order: the referenced PO is linked to a SIBLING invoice (Loaded is
  // 1:1) — the reference rides the receive without linking.
  split_po_id?: string | null;
  split_sibling_invoice_id?: string | null;
  // Accepted "unlink this order": sent as unlink_purchase_order on receive.
  po_unlinked?: boolean | null;
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
  // Server-derived: this document CREDITS the supplier — receiving it reverses
  // stock and cost, and every quantity/total on it is negative.
  is_credit_note?: boolean;
  // Tombstone: the draft was deleted from Loaded (statement/duplicate accept).
  is_deleted?: boolean;
  deleted_reason?: string | null;
  status?: string;
  lines: Line[];
  // PO lines whose stock code never appeared on the invoice — ordered, not
  // received. Read-only reference, never part of the receive payload.
  ordered_not_received?: OrderedNotReceived[];
  // Split order: PO items that arrived on the SIBLING delivery.
  ordered_received_elsewhere?: OrderedNotReceived[];
  // The cached raw rows of ONE order — the projection's only input. The review
  // caches the order it suggests linking, so accepting is instant.
  po_reference?: { po_id?: string | null };
  // ---- The replica_v1 review contract ----
  doc_schema?: string;
  reviewed_at?: string | null;
  confidence?: 'ready' | 'needs_review';
  suggestions?: Suggestion[];
  issues?: Issue[];
  suggestion_actions?: SuggestionAction[];
  // Sidecars (read-only, admin X-ray): the replica shadow (doc-shaped, our
  // full resolution of the copy), Loaded's pristine mirror, and the verbatim
  // extraction.
  replica?: (Partial<DocData> & { resolution_log?: string[]; supplier_name?: string | null }) | null;
  loaded_snapshot?: { header?: Partial<DocData>; lines?: Line[] } | null;
  extracted_snapshot?: {
    header?: {
      document_type?: string | null;
      invoice_number?: string | null;
      supplier_name?: string | null;
      customer_purchase_order_number?: string | null;
      // Legacy — present only on snapshots stored before the field was
      // retired from the extraction schema (17 Aug 2026); read as fallback.
      purchase_order_number?: string | null;
      subtotal_ex_tax?: number | null;
      tax_amount?: number | null;
      total_incl_tax?: number | null;
      supplier_differs?: boolean | null;
    } | null;
    lines?: {
      code?: string | null;
      description?: string | null;
      quantity?: number | null;
      unit?: string | null;
      unit_of_measure?: string | null;
      unit_unrecognisable?: boolean | null;
      unit_price_ex_tax?: number | null;
      line_total_ex_tax?: number | null;
    }[] | null;
  } | null;
  // Dojo (supplier-spec regression run) payloads only: run status + the
  // structured diffs vs the stored baseline. Rendered as the dojo banner.
  dojo_status?: string | null;
  dojo_diffs?: DojoDiff[] | null;
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
  // Split order: the qty the SIBLING invoice received (ordered_received_elsewhere).
  quantity_received?: number | null;
  item_id?: string | null;
}
interface StockGroup { id: string; name: string | null; category?: string | null }
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

const cur = (n: number | null | undefined) => `$${(n ?? 0).toFixed(2)}`;
const round2 = (n: number) => Math.round(n * 100) / 100;
const round4 = (n: number) => Math.round(n * 10000) / 10000;

// Per-line tax, honouring the "line item costs include tax" toggle: costs
// exclude tax → tax is added on top; costs include tax → tax is the portion
// already inside the line total.
function lineTax(lineTotal: number, rate: number | null | undefined, includesTax: boolean): number {
  const r = rate ?? 0;
  if (!r) return 0;
  return includesTax ? lineTotal - lineTotal / (1 + r) : lineTotal * r;
}

// Compact display of a suggestion's current/proposed values.
const fmtVal = (v: unknown): string => {
  if (v == null || v === '') return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(round4(v));
  return String(v);
};

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

// Loaded's web app — deep links open in the user's own Loaded session (no
// token involved). The LIVE app is the legacy hash-routed one under /App/#
// (routes confirmed in its bundle: '/invoices/:invoiceId' and
// '/ordering/purchase-orders/:purchaseOrderId' under /stock); the newer /ui
// bundle's routes do not resolve for real users.
const LOADED_APP_URL = 'https://loadedhub.com/App/#';
const loadedInvoiceUrl = (id: string) => `${LOADED_APP_URL}/stock/invoices/${id}`;
const loadedPoUrl = (id: string) => `${LOADED_APP_URL}/stock/ordering/purchase-orders/${id}`;
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
// Inline suggestion chip + its tiny accept/dismiss buttons.
const chipStyle: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: '0.6rem',
  color: '#8a6d3b', background: '#fdf6e7', border: '1px solid #e6d3a3',
  borderRadius: 4, padding: '1px 6px', marginTop: 2, whiteSpace: 'nowrap',
};
const chipBtn: React.CSSProperties = {
  border: 'none', background: 'none', cursor: 'pointer', padding: 0,
  font: 'inherit', fontWeight: 700,
};
const linkBtn: React.CSSProperties = {
  border: 'none', background: 'none', color: '#8a6d3b', textDecoration: 'underline',
  cursor: 'pointer', padding: 0, font: 'inherit',
};
// ISO string → the YYYY-MM-DD a <input type="date"> wants (and back on change).
const dateVal = (s: string | null | undefined) => (s ? String(s).slice(0, 10) : '');

// The tiny clears_when evaluator — the SAME dumb predicate the server runs
// (invoice_review._clears): scope line → find the line, else the header;
// then not_null / truthy / equals. Nothing more.
function evalClears(data: DocData, issue: Issue): boolean {
  const cw = issue.clears_when;
  if (!cw) return false;
  let v: unknown;
  if (cw.scope === 'line') {
    const ln = (data.lines || []).find((l) => String(l.id) === String(cw.line_id));
    v = ln ? (ln as unknown as Record<string, unknown>)[cw.field] : undefined;
  } else {
    v = (data as unknown as Record<string, unknown>)[cw.field];
  }
  if (cw.op === 'not_null') return v !== null && v !== undefined;
  if (cw.op === 'truthy') return !!v;
  if (cw.op === 'equals') return v === cw.value;
  return false;
}

export default function ReceiveInvoiceEditor({ data, props, threadId }: DisplayBlockProps) {
  const embedded = !!props?.embedded;
  // Dojo: a supplier-spec regression run rendered in this component. The data
  // is an ephemeral local sandbox (no venue, no working document — every
  // network effect below is already guarded off); suggestions/issues/receive
  // are replaced by the dojo banner (status + diffs vs baseline).
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
  const [docLive, setDoc] = useState<DocData>(() => ({ ...initial, lines: initial.lines ?? [] }));
  // Admin X-ray (the "Norm | Extracted | Loaded | Replica" slider): flip every
  // field to the PDF extraction (what we READ from the copy), Loaded's
  // pristine snapshot (refreshed server-side on every open), or the replica
  // sidecar (our full resolution). Display-only — the whole body renders
  // read-only while a non-Norm view is on; edits always target the live draft.
  const [viewMode, setViewMode] = useState<'norm' | 'extracted' | 'loaded' | 'replica'>('norm');
  const viewLoaded = viewMode !== 'norm';
  const doc = useMemo<DocData>(() => {
    if (viewMode === 'loaded' && docLive.loaded_snapshot) {
      const snap = docLive.loaded_snapshot;
      return {
        ...docLive,
        ...(snap.header || {}),
        lines: (snap.lines || []) as Line[],
      };
    }
    if (viewMode === 'replica' && docLive.replica) {
      // The replica is already doc-shaped (build_received_invoice_data
      // parity) — overlay it wholesale; keep live identity fields.
      return {
        ...docLive,
        ...docLive.replica,
        invoice_id: docLive.invoice_id,
        file_id: docLive.file_id,
        is_received: docLive.is_received,
        lines: (docLive.replica.lines || []) as Line[],
      } as DocData;
    }
    if (viewMode === 'extracted' && docLive.extracted_snapshot) {
      const ext = docLive.extracted_snapshot;
      const h = ext.header || {};
      return {
        ...docLive,
        reference_number: h.invoice_number ?? null,
        supplier_name: h.supplier_name ?? null,
        linked_supplier_id: null,
        purchase_order_number: h.customer_purchase_order_number ?? h.purchase_order_number ?? null,
        linked_purchase_order_id: null,
        subtotal: h.subtotal_ex_tax ?? null,
        tax_amount: h.tax_amount ?? null,
        total: h.total_incl_tax ?? null,
        lines: (ext.lines || []).map((el, i) => ({
          id: `ext-${i}`,
          code: el.code ?? null,
          description: el.description ?? null,
          brand: null,
          // Two unit FACTS, two columns (the dojo's labels): the unit as
          // printed on the copy, and the derived delivered unit of one item.
          unit: el.unit ?? null,
          unit_of_measure: el.unit_of_measure ?? null,
          unit_unrecognisable: el.unit_unrecognisable ?? null,
          linked_unit_id: null,
          unit_ratio: null,
          quantity_ordered: null,
          quantity_received: el.quantity ?? null,
          unit_cost: el.unit_price_ex_tax ?? null,
          total_cost: el.line_total_ex_tax ?? null,
          tax_amount: null,
          sale_tax_rate: null,
          linked_item_id: null,
        })) as Line[],
      };
    }
    return docLive;
  }, [docLive, viewMode]);
  // Mirror for callbacks that must read the CURRENT (live) doc without
  // re-creating themselves per render (patch line-id injection, the
  // suggestion appliers' before-capture and action-log appends).
  const docRef = useRef<DocData>(docLive);
  docRef.current = docLive;
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
  // Line whose (unlinked) unit name is being CREATED in Loaded (create-unit).
  const [creatingUnitLine, setCreatingUnitLine] = useState<string | null>(null);
  // Line whose (unlinked) BRAND is being created in Loaded (create-brand).
  const [creatingBrandLine, setCreatingBrandLine] = useState<string | null>(null);
  // Second confirmation before creating a unit (like create-item's form): the
  // first click ARMS this line; only the explicit confirm click writes.
  const [confirmUnitLine, setConfirmUnitLine] = useState<string | null>(null);
  // Same two-step for creating the copy's supplier in Loaded (create-supplier).
  const [confirmSupplier, setConfirmSupplier] = useState(false);
  const [creatingSupplier, setCreatingSupplier] = useState(false);
  const [linkQuery, setLinkQuery] = useState('');
  // Substitute lines whose original ordered row is expanded.
  const [openSub, setOpenSub] = useState<Set<string>>(new Set());
  // Per-mount uid so line anchors never collide across sibling cards.
  const uid = useRef(Math.random().toString(36).slice(2, 8)).current;
  const isPlatformAdmin = getStoredUser()?.role === 'admin';
  const [cannotState, setCannotState] = useState<'sending' | 'filed' | null>(null);

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

  // Fetch this card's OWN doc and adopt it (state + version). The review and
  // receive endpoints land on ALL twin docs server-side, and their response
  // bodies may belong to a canonical twin — adopting a twin's lines and
  // version wholesale was how validation could visibly "move" or vanish.
  const refetchOwnDoc = async (): Promise<boolean> => {
    if (!docUrl) return false;
    const own = await apiFetch(docUrl).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (own?.data) {
      setDoc(own.data as DocData);
      if (typeof own.version === 'number') setVersion(own.version);
      return true;
    }
    return false;
  };

  // The server review (POST /invoice-fixes/review) — builds/rebuilds the
  // replica_v1 payload. Web-only (embedded cards are pre-reviewed at block
  // build). force=true recomputes AND SQUASHES local edits + the
  // accept/dismiss record (Re-analyse confirms before calling it).
  const runReview = async (explicit = false, force = false) => {
    if (embedded || !venueId || !doc.invoice_id || doc.is_deleted) return;
    // One review at a time — but NEVER a dead-end: if the review state gets
    // cleared while a run is in flight, queue exactly one follow-up run. (A
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
        body: JSON.stringify({ venue_id: venueId, invoice_id: doc.invoice_id, ...(force ? { force: true } : {}) }),
      });
      if (!r.ok) {
        if (explicit) {
          setStatus('error');
          setMessage(`The review could not run (${r.status}) — reload and try again`);
        }
        return;
      }
      // The review landed on ALL twin docs server-side — refetch THIS card's
      // own doc so state (and the version) match its identity exactly.
      const adopted = await refetchOwnDoc();
      if (!adopted) {
        const d = await r.json();
        if (d?.data) setDoc((prev) => ({ ...prev, ...(d.data as DocData) }));
      }
      if (force) {
        window.dispatchEvent(new CustomEvent(INVOICE_ACTIONED_EVENT, {
          detail: { venueId, invoiceId: doc.invoice_id, sourceDocId: workingDocId },
        }));
      }
    } catch {
      if (explicit) {
        setStatus('error');
        setMessage('The review could not run — check the connection and try again');
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
  const reviewed = docLive.doc_schema === 'replica_v1' && !!docLive.reviewed_at;
  useEffect(() => {
    if (embedded || !venueId || !doc.invoice_id) return;
    if (doc.is_deleted) return; // tombstone — nothing left to review
    // (Re)build whenever the doc is not a reviewed replica_v1 payload — a
    // legacy doc, or one whose cached review the server invalidated.
    if (reviewed) return;
    runReview();
    // Fires when the invoice_id is known AND whenever the review state is
    // cleared (server invalidation) — the guard above stops a re-run once
    // the review returns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embedded, venueId, doc.invoice_id, reviewed]);


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
      { quantity_received: qty, total_cost: round4(qty * cost) },
      [{ op: 'update_line', index: idx, fields: { quantity_received: qty, total_cost: round4(qty * cost) } }],
    );
  };
  const onCost = (idx: number, cost: number) => {
    const qty = doc.lines[idx]?.quantity_received ?? 0;
    setLine(
      idx,
      { unit_cost: cost, total_cost: round4(qty * cost) },
      [{ op: 'update_line', index: idx, fields: { unit_cost: cost, total_cost: round4(qty * cost) } }],
    );
  };
  // Strike / un-strike a line. Striking normally happens by accepting the
  // server's strike suggestion; this is the plain toggle (restore, and the
  // manual escape hatch).
  const onStrike = (idx: number, struck: boolean) => {
    setLine(idx, { struck }, [{ op: 'update_line', index: idx, fields: { struck } }]);
  };

  // Linking an order writes only its ID; the order's ROWS live in Loaded, and
  // the projection built from them (order date, per-line quantity ordered,
  // "ordered, not delivered") is server-owned — a different order means the
  // cached rows no longer apply, so the server drops the projection rather
  // than show another order's numbers. Re-opening the draft re-fetches and
  // re-projects. EVERY path that changes the link must call this, not just the
  // dropdown: accepting Norm's own "link it" suggestion left the number in the
  // header with no order date and "—" in every Qty Ordered cell.
  const refreshOrderReference = async () => {
    if (embedded || !venueId || !workingDocId || !docRef.current.invoice_id) return;
    try {
      const res = await apiFetch('/api/invoice-fixes/draft', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, invoice_id: docRef.current.invoice_id }),
      });
      if (!res.ok) return;
      const fresh = await res.json();
      if (fresh?.data) setDoc((prev) => ({ ...prev, ...(fresh.data as DocData) }));
      if (typeof fresh?.version === 'number') setVersion(fresh.version);
    } catch { /* reference data is an enhancement — the next open re-attaches */ }
  };
  // The suggestion kinds that move the order link, and so need the re-project.
  const PO_LINK_KINDS = ['link_po', 'unlink_po', 'split_reference'];
  // The kinds whose accept performs a write in LOADED, not just a doc edit.
  const LOADED_WRITE_KINDS = ['create_brand', 'create_unit', 'create_item', 'delete_invoice'];
  // True when the doc already carries the rows of the order this suggestion
  // links to — the review pre-caches exactly that order, so the projection
  // recomputes on the accept patch and there is nothing to fetch.
  const ordersCached = (s: Suggestion) => {
    const apply = (s.apply || {}) as Record<string, unknown>;
    const target = (apply.linked_purchase_order_id ?? apply.split_po_id) as string | undefined;
    if (!target) return false;
    return String(docRef.current.po_reference?.po_id ?? '') === String(target);
  };

  const onPo = async (poId: string) => {
    const po = pos.find((p) => p.id === poId);
    setDoc((prev) => ({
      ...prev,
      linked_purchase_order_id: poId || null,
      purchase_order_number: po?.order_number ?? prev.purchase_order_number,
    }));
    if (!workingDocId) return;
    await patchDoc([{ op: 'update_header', fields: { linked_purchase_order_id: poId || null, purchase_order_number: po?.order_number } }]);
    if (poId) await refreshOrderReference();
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

  // ---- The suggestion/issue action engine ------------------------------
  // ONE generic applier for every server suggestion, used by BOTH the inline
  // chips and the summary list — the two surfaces can never diverge. Each
  // accept is a single PATCH (the value edit + the action-log entry together,
  // so the doc version can't race), and every decision lands in
  // doc.suggestion_actions — THE RECORD, shared with autopilot (by: "norm").
  const nowIso = () => new Date().toISOString();
  const actionsList = useMemo(() => docLive.suggestion_actions || [], [docLive.suggestion_actions]);
  // Last action per suggestion_id wins (also covers autopilot's entries).
  const lastAction = useMemo(() => {
    const m = new Map<string, SuggestionAction>();
    for (const a of actionsList) if (a?.suggestion_id) m.set(a.suggestion_id, a);
    return m;
  }, [actionsList]);
  const stateOf = (id: string): 'pending' | 'accepted' | 'dismissed' => {
    const a = lastAction.get(id);
    if (!a || a.action === 'undone') return 'pending';
    return a.action;
  };
  const recordOp = (entry: SuggestionAction): [SuggestionAction[], Record<string, unknown>] => {
    const log = [...(docRef.current.suggestion_actions || []), entry];
    return [log, { op: 'update_header', fields: { suggestion_actions: log } }];
  };

  // The ONE place a suggestion becomes ops. Single-accept and Accept-all MUST
  // apply identically: the autopilot metric reads the action log, not which
  // button was pressed, so a divergence here would describe an invoice that
  // never existed. Pure — folds against a given state and returns the next.
  const foldSuggestion = (
    state: DocData,
    s: Suggestion,
  ): { ops: Record<string, unknown>[]; entry: SuggestionAction; next: DocData } | null => {
    const at = nowIso();
    if (s.kind === 'add_line') {
      const payload = { ...(s.payload || {}) } as Record<string, unknown>;
      if (!Object.keys(payload).length) return null;
      return {
        ops: [{ op: 'add_line', fields: payload }],
        entry: {
          suggestion_id: s.id, action: 'accepted', by: 'user', at,
          before: { added_line_id: payload.id ?? null }, after: { added: true },
        },
        next: { ...state, lines: [...state.lines, payload as unknown as Line] },
      };
    }
    const apply = s.apply;
    if (!apply || !Object.keys(apply).length) return null;
    if (s.line_id) {
      const idx = state.lines.findIndex((l) => String(l.id) === String(s.line_id));
      const ln = state.lines[idx];
      if (!ln) return null;
      const fields: Record<string, unknown> = { ...apply };
      // Total follows qty × cost — the ONLY client math (the receive
      // recomputes anyway; this keeps the display honest).
      if ('quantity_received' in apply || 'unit_cost' in apply) {
        const q = Number(fields.quantity_received ?? ln.quantity_received);
        const c = Number(fields.unit_cost ?? ln.unit_cost);
        if (Number.isFinite(q) && Number.isFinite(c)) fields.total_cost = round4(q * c);
      }
      const before: Record<string, unknown> = {};
      for (const k of Object.keys(fields)) before[k] = (ln as unknown as Record<string, unknown>)[k] ?? null;
      return {
        ops: [{ op: 'update_line', line_id: ln.id, index: idx, fields }],
        entry: { suggestion_id: s.id, action: 'accepted', by: 'user', at, before, after: apply },
        next: { ...state, lines: state.lines.map((l, i) => (i === idx ? { ...l, ...(fields as Partial<Line>) } : l)) },
      };
    }
    const before: Record<string, unknown> = {};
    for (const k of Object.keys(apply)) before[k] = (state as unknown as Record<string, unknown>)[k] ?? null;
    return {
      ops: [{ op: 'update_header', fields: apply }],
      entry: { suggestion_id: s.id, action: 'accepted', by: 'user', at, before, after: apply },
      next: { ...state, ...(apply as Partial<DocData>) },
    };
  };

  // Accept every pending suggestion in ONE patch. Not a loop over
  // acceptSuggestion: each call reads docRef.current and sends
  // versionRef.current, and docRef only refreshes after a render — so N calls
  // in a tick would build N action logs from stale state (last write wins,
  // losing all but one) and 409 on the 2nd..Nth PATCH.
  const acceptAllSuggestions = async () => {
    if (doneState) return;
    // delete_invoice is a destructive Loaded write; autopilot skips it too.
    const batch = pendingSuggestions.filter((s) => s.kind !== 'delete_invoice');
    if (!batch.length) return;
    // create_unit has no applyable value — the unit must be CREATED in Loaded
    // first — so foldSuggestion returns null for it. Folding it into the batch
    // would silently drop it while the button counted it ("Accept all (5)"
    // accepting three). It runs after the batch instead, through the same
    // single-accept path, sequentially: each is a Loaded write, and patchDoc
    // serializes on its own queue so the version can't go stale.
    // Units BEFORE items: Loaded refuses to create a stock item without a
    // resolved unit, so an item creation on a NEW-unit line only works once
    // its unit exists.
    const creates = [
      ...batch.filter((s) => s.kind === 'create_unit'),
      ...batch.filter((s) => s.kind === 'create_item'),
      ...batch.filter((s) => s.kind === 'create_brand'),
    ];
    let state = docRef.current;
    const ops: Record<string, unknown>[] = [];
    const entries: SuggestionAction[] = [];
    for (const s of batch) {
      const folded = foldSuggestion(state, s);
      if (!folded) continue;
      ops.push(...folded.ops);
      entries.push(folded.entry);
      state = folded.next;
    }
    if (entries.length) {
      const log = [...(docRef.current.suggestion_actions || []), ...entries];
      const nextState = state;
      setDoc(() => ({ ...nextState, suggestion_actions: log }));
      if (workingDocId) {
        await patchDoc([...ops, { op: 'update_header', fields: { suggestion_actions: log } }]);
      }
      if (batch.some((s) => PO_LINK_KINDS.includes(s.kind))) await refreshOrderReference();
    }
    // The batch above is doc-only and applies anywhere; these need Loaded.
    // In the card they would each no-op, so the count on the button would be
    // a lie — accept what can be accepted and name what could not.
    if (embedded && creates.length) {
      setStatus('error');
      setMessage(
        `${creates.length} suggestion${creates.length > 1 ? 's need' : ' needs'} `
        + 'something created in Loaded — open the invoice in Norm to apply '
        + `${creates.length > 1 ? 'those' : 'that'}.`,
      );
      return;
    }
    for (const s of creates) {
      const idx = docRef.current.lines.findIndex((l) => String(l.id) === String(s.line_id));
      if (idx < 0) continue;
      if (s.kind === 'create_unit') {
        const name = (s.payload?.unit_name as string | undefined)
          || (typeof s.proposed === 'string' ? s.proposed : undefined);
        if (name) await createUnitAndApply(idx, name, s);
      } else if (s.kind === 'create_brand') {
        const p = (s.payload || {}) as Record<string, string | undefined>;
        await createBrandAndApply(String(s.line_id), p.brand_name || String(s.proposed ?? ''), s);
      } else {
        const p = (s.payload || {}) as Record<string, string | undefined>;
        const name = p.name || (typeof s.proposed === 'string' ? s.proposed : '');
        await createItemAndApply(String(s.line_id), name, p.group_id || '', s);
      }
    }
  };

  // "Norm can't do this one": file the invoice into the training dojo and
  // record the verdict. Never receives, never edits the draft — the invoice
  // stays exactly as it is, and receiving it later is still fine.
  const cannotReceive = async () => {
    if (cannotState || !venueId || !doc.invoice_id) return;
    setCannotState('sending');
    try {
      const res = await apiFetch('/api/invoice-fixes/cannot-receive', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, invoice_id: doc.invoice_id }),
      });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      // Report what HAPPENED, not what we asked for. The endpoint answers 200
      // in three different situations and they are not the same news: filed,
      // already filed (staging is idempotent, so a second press changes
      // nothing), and couldn't file at all because Loaded holds no copy of
      // the invoice. Claiming "filed" for all three is how this button spent
      // a day looking like it worked while nothing reached the Dojo.
      const out = await res.json().catch(() => ({}));
      setCannotState('filed');
      setStatus('idle');
      if (!out.staged) {
        setMessage(
          out.reason
            ? `Recorded, but it can't be filed for training: ${out.reason}`
            : "Recorded, but there's no invoice copy in Loaded to train on",
        );
      } else if (out.already_in_dojo) {
        // Saying "already there, nothing to do" was wrong: a second press
        // means the last training didn't solve it, so it goes back to the
        // sensei rather than being treated as a duplicate.
        setMessage('Already in the Dojo — sent back to the sensei for another look');
      } else {
        setMessage('Filed for training — the sensei is analysing it now');
      }
    } catch (e) {
      setCannotState(null);
      setMessage(e instanceof Error ? e.message : 'Could not file this invoice');
      setStatus('error');
    }
  };

  // Every kind below that needs a Loaded WRITE (create the brand/unit/item,
  // delete the draft) is web-only — each applier early-returns on `embedded`.
  // Silently: the ✓ did nothing, said nothing, and recorded no acceptance, so
  // inside Claude's card accepting a create looked like it had worked. Say so
  // instead. (Same honesty rule as the Can't-receive button: report what
  // happened, not what was asked.)
  const acceptSuggestion = (s: Suggestion) => {
    if (doneState) return;
    if (embedded && LOADED_WRITE_KINDS.includes(s.kind)) {
      setStatus('error');
      setMessage('This one has to be done in Norm — open the invoice there to apply it.');
      return;
    }
    if (s.kind === 'delete_invoice') {
      // A Loaded write (verified DELETE endpoint) — server applier, web only.
      void acceptFix(s);
      return;
    }
    if (s.kind === 'create_unit') {
      // A Loaded write: create the copy's delivered unit and link it. The name
      // rides in the suggestion payload (no issue backs it); the ✓ is the
      // confirm, so createUnitAndApply skips its own two-step when given a name.
      const name = (s.payload?.unit_name as string | undefined)
        || (typeof s.proposed === 'string' ? s.proposed : undefined);
      const idx = docRef.current.lines.findIndex((l) => String(l.id) === String(s.line_id));
      if (idx >= 0 && name) void createUnitAndApply(idx, name, s);
      return;
    }
    if (s.kind === 'create_brand') {
      // A Loaded write: create the brand Loaded named but has no record for.
      const p = (s.payload || {}) as Record<string, string | undefined>;
      const name = p.brand_name || (typeof s.proposed === 'string' ? s.proposed : '');
      void createBrandAndApply(String(s.line_id), name, s);
      return;
    }
    if (s.kind === 'create_item') {
      // A Loaded write: create the stock item (+ its supplier variant) and
      // link the line. Name and stock group ride in the payload; the line's
      // unit must already be resolved (Loaded requires it), which is why
      // Accept all does the unit creations first.
      const p = (s.payload || {}) as Record<string, string | undefined>;
      const name = p.name || (typeof s.proposed === 'string' ? s.proposed : '');
      void createItemAndApply(String(s.line_id), name, p.group_id || '', s);
      return;
    }
    const folded = foldSuggestion(docRef.current, s);
    if (!folded) return;
    // Accepting a unit suggestion IS the explicit unit decision, so it also
    // clears the unit blocker on the same line — the mirror of the server
    // gate walk's record (unit blockers carry no clears_when by design).
    const entries: SuggestionAction[] = [folded.entry];
    if (s.kind === 'line_value' && s.field === 'unit') {
      const unitIssue = (docRef.current.issues || []).find((i) =>
        !!i.blocking
        && ['unit_missing', 'unit_unknown', 'unit_unconfirmed'].includes(String(i.code))
        && String(i.line_id) === String(s.line_id));
      if (unitIssue) entries.push({ suggestion_id: unitIssue.id, action: 'accepted', by: 'user', at: nowIso() });
    }
    // Suggestions tagged `resolves` (e.g. the totals corrections): accepting
    // the LAST pending one for an issue records that issue accepted too —
    // a partial set of corrections must not unblock the receive.
    if (s.resolves) {
      const otherPending = suggestions.some((o) =>
        o.id !== s.id && o.resolves === s.resolves && stateOf(o.id) === 'pending');
      if (!otherPending) {
        entries.push({ suggestion_id: String(s.resolves), action: 'accepted', by: 'user', at: nowIso() });
      }
    }
    const log = [...(docRef.current.suggestion_actions || []), ...entries];
    const logOp = { op: 'update_header', fields: { suggestion_actions: log } };
    const nextState = folded.next;
    setDoc(() => ({ ...nextState, suggestion_actions: log }));
    if (workingDocId) {
      const patched = patchDoc([...folded.ops, logOp]);
      // The review pre-caches the rows of the order it suggests, so the
      // projection recomputes on this very patch — no wait, no re-analysis.
      // Only fetch when the accepted order ISN'T the cached one.
      if (PO_LINK_KINDS.includes(s.kind) && !ordersCached(s)) {
        void patched.then(refreshOrderReference);
      }
    }
  };

  // Dismiss: record-only ("declined without applying"). Also how a blocking
  // ISSUE is waved through ("I've checked this") — same record, the issue id.
  const dismissAction = (id: string) => {
    if (doneState) return;
    const entry: SuggestionAction = { suggestion_id: id, action: 'dismissed', by: 'user', at: nowIso() };
    const [log, logOp] = recordOp(entry);
    setDoc((prev) => ({ ...prev, suggestion_actions: log }));
    if (workingDocId) patchDoc([logOp]);
  };

  // Undo: apply the recorded `before` back (accepted), or just clear the
  // record (dismissed). Always appends an "undone" entry — the record is
  // append-only, so the trail stays honest.
  const undoAction = (s: Suggestion | null, id: string) => {
    if (doneState) return;
    const a = lastAction.get(id);
    if (!a || a.action === 'undone') return;
    const entry: SuggestionAction = { suggestion_id: id, action: 'undone', by: 'user', at: nowIso() };
    const [log, logOp] = recordOp(entry);
    if (a.action === 'dismissed' || !a.before) {
      setDoc((prev) => ({ ...prev, suggestion_actions: log }));
      if (workingDocId) patchDoc([logOp]);
      return;
    }
    if (s?.kind === 'add_line') {
      const lineId = a.before.added_line_id;
      const idx = docRef.current.lines.findIndex((l) => String(l.id) === String(lineId));
      setDoc((prev) => ({
        ...prev,
        suggestion_actions: log,
        lines: idx >= 0 ? prev.lines.filter((_, i) => i !== idx) : prev.lines,
      }));
      if (workingDocId) patchDoc([...(idx >= 0 ? [{ op: 'remove_line', index: idx }] : []), logOp]);
      return;
    }
    if (s?.line_id) {
      const idx = docRef.current.lines.findIndex((l) => String(l.id) === String(s.line_id));
      const ln = docRef.current.lines[idx];
      if (ln) {
        setDoc((prev) => ({
          ...prev,
          suggestion_actions: log,
          lines: prev.lines.map((l, i) => (i === idx ? { ...l, ...(a.before as Partial<Line>) } : l)),
        }));
        if (workingDocId) patchDoc([{ op: 'update_line', line_id: ln.id, index: idx, fields: a.before }, logOp]);
        return;
      }
      // The line is gone — record the undo anyway.
      setDoc((prev) => ({ ...prev, suggestion_actions: log }));
      if (workingDocId) patchDoc([logOp]);
      return;
    }
    setDoc((prev) => ({ ...prev, ...(a.before as Partial<DocData>), suggestion_actions: log }));
    if (workingDocId) patchDoc([{ op: 'update_header', fields: a.before }, logOp]);
  };

  // ---- Derived review state (rendering only — the server decided) -------
  // Legacy stored docs carry old-shape suggestions ({type, summary}) — those
  // are not rendered; only replica_v1 entries ({id, kind}) are.
  const suggestions = useMemo(
    () => (docLive.suggestions || []).filter(
      (s): s is Suggestion => !!s && typeof (s as { kind?: unknown }).kind === 'string' && !!(s as { id?: unknown }).id,
    ),
    [docLive.suggestions],
  );
  // A credit note: the server derives the flag, but a negative total is
  // Loaded's own definition and covers a doc saved before the flag existed.
  const isCredit = !!docLive.is_credit_note
    || (typeof docLive.total === 'number' && docLive.total < 0);
  const issues = useMemo(
    () => (docLive.issues || []).filter((i) => !!i && !!i.id && !!i.message),
    [docLive.issues],
  );
  // An issue is CLEARED when its clears_when predicate holds against the
  // current working values ('resolved'), or when it was explicitly accepted/
  // dismissed — "I've checked this" ('checked'). Otherwise it is open, and a
  // blocking open issue gates the Receive button on BOTH surfaces.
  type IssueState = 'open' | 'resolved' | 'checked';
  const issueStateOf = (i: Issue): IssueState => {
    if (evalClears(docLive, i)) return 'resolved';
    return stateOf(i.id) === 'pending' ? 'open' : 'checked';
  };
  const blockingIssues = issues.filter((i) => i.blocking);
  const warningIssues = issues.filter((i) => !i.blocking);
  const blockingOpen = blockingIssues.filter((i) => issueStateOf(i) === 'open');
  const liveConfidence: 'ready' | 'needs_review' | null =
    reviewed ? (blockingOpen.length ? 'needs_review' : 'ready') : null;
  const pendingSuggestions = suggestions.filter((s) => stateOf(s.id) === 'pending');
  // Inline chip lookups — the same suggestion objects the summary list
  // renders, so accepting from either surface is the same action.
  const suggFor = (lineId: string, field: string) =>
    pendingSuggestions.find((s) => s.kind === 'line_value' && String(s.line_id) === String(lineId) && s.field === field);
  const strikeSuggFor = (lineId: string) =>
    pendingSuggestions.find((s) => s.kind === 'strike' && String(s.line_id) === String(lineId));
  // Like the unit column: a swap to an item Loaded already has (line_value),
  // or "it isn't in the catalogue — create it" (create_item, a Loaded write).
  const itemSuggFor = (lineId: string) =>
    pendingSuggestions.find((s) => (s.kind === 'line_value' || s.kind === 'create_item')
      && String(s.line_id) === String(lineId) && s.field === 'linked_item_id');
  // The unit column carries TWO kinds: a plain swap to a unit Loaded already
  // has (line_value), and "the copy's delivered unit doesn't exist in Loaded —
  // create it" (create_unit, which is a Loaded write). Both are line-level
  // proposals and belong on the line, not only in the summary list; ✓ routes
  // to the same handler either way.
  const brandSuggFor = (lineId: string) =>
    pendingSuggestions.find((s) => s.kind === 'create_brand' && String(s.line_id) === String(lineId));
  const unitSuggFor = (lineId: string) =>
    pendingSuggestions.find((s) => (s.kind === 'line_value' || s.kind === 'create_unit')
      && String(s.line_id) === String(lineId) && s.field === 'unit');
  const headerValueSugg = (field: string) =>
    pendingSuggestions.find((s) => s.kind === 'header_value' && s.field === field);
  const supplierSugg = pendingSuggestions.find((s) => s.kind === 'supplier');
  const poSuggs = pendingSuggestions.filter((s) => ['link_po', 'unlink_po', 'split_reference'].includes(s.kind));
  const moneySuggs = ['subtotal', 'tax_amount', 'discount_amount', 'total']
    .map((f) => headerValueSugg(f))
    .filter((s): s is Suggestion => !!s);
  const deleteSugg = suggestions.find((s) => s.kind === 'delete_invoice');
  // What "Accept all" would actually apply — delete_invoice is excluded, so
  // the count must never promise it.
  const acceptAllCount = pendingSuggestions.filter((s) => s.kind !== 'delete_invoice').length;

  const doneState = status === 'done' || !!doc.is_received || !!doc.is_deleted;
  const draftDeleted = deletedDraft || !!doc.is_deleted;
  // Lines still pointing at a NEW (uncreated) stock item or unit — receiving is
  // blocked until each is explicitly created in Loaded. A struck line is
  // excluded from the receive entirely, so it never gates it.
  const unresolved = useMemo(
    // Mirrors do_receive's own guard, brand included: Loaded refuses a line
    // naming a brand it has no record for, and finding that out on submit as
    // a 400 is worse than a greyed button that says why.
    () => doc.lines.filter((l) => !l.struck && (!l.linked_item_id || !l.linked_unit_id
      || (!!l.brand && !l.linked_brand_id))),
    [doc.lines],
  );
  // No linked supplier: Loaded rejects the receive outright (500), so block
  // it here with a visible reason — on BOTH surfaces (same gating).
  const supplierBlocking = !doneState && !!doc.invoice_id && !doc.linked_supplier_id;
  // Nothing to receive: an empty draft (a statement/letter uploaded as an
  // invoice, or every line struck). Deleting the draft is the action.
  const noLines = !doneState && !!doc.invoice_id && !doc.lines.some((l) => !l.struck);
  // Everything standing between the user and a receive, counted ONCE — the
  // per-line NEW badges and the blocked list are two views of the same set.
  const blockedCount = useMemo(
    () => new Set([...unresolved.map((l) => String(l.id)), ...blockingOpen.map((i) => i.id)]).size,
    [unresolved, blockingOpen],
  );
  const receiveBlocked = status === 'saving' || unresolved.length > 0
    || supplierBlocking || noLines || blockingOpen.length > 0;

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
    const excl2 = round2(excl);
    const tax2 = round2(tax);
    return { excl: excl2, tax: tax2, discount, incl: round2(excl2 + tax2 - discount) };
  }, [doc.lines, includesTax, doc.discount_amount]);

  const candidatePos = useMemo(
    () => pos.filter((p) => !p.supplier_id || p.supplier_id === doc.linked_supplier_id),
    [pos, doc.linked_supplier_id],
  );
  const sortedUnits = useMemo(
    () => [...units].sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { numeric: true })),
    [units],
  );
  const sortedGroups = useMemo(
    () => [...stockGroups].sort((a, b) => (a.name || '').localeCompare(b.name || '')),
    [stockGroups],
  );
  const filteredStock = useMemo(() => {
    const q = addQuery.trim().toLowerCase();
    if (!q) return [] as StockItem[];
    return stockItems
      .filter((i) => (i.name || '').toLowerCase().includes(q) || (i.code || '').toLowerCase().includes(q))
      .slice(0, 20);
  }, [addQuery, stockItems]);
  // Manual "search & link an existing item" inside the create form — the fallback
  // when the server's match is wrong but the product does already exist.
  const linkMatches = useMemo(() => {
    const q = linkQuery.trim().toLowerCase();
    if (!q) return [] as StockItem[];
    return stockItems
      .filter((i) => (i.name || '').toLowerCase().includes(q) || (i.code || '').toLowerCase().includes(q))
      .slice(0, 8);
  }, [linkQuery, stockItems]);

  // The action record, effective state (last action per suggestion wins):
  // "Norm applied 3 · you applied 2 · dismissed 1" + expandable trail.
  const effectiveActions = useMemo(() => [...lastAction.values()].filter((a) => a.action !== 'undone'), [lastAction]);
  const recordCounts = useMemo(() => ({
    norm: effectiveActions.filter((a) => a.action === 'accepted' && a.by === 'norm').length,
    user: effectiveActions.filter((a) => a.action === 'accepted' && a.by !== 'norm').length,
    dismissed: effectiveActions.filter((a) => a.action === 'dismissed').length,
  }), [effectiveActions]);
  const explanationFor = (sid: string) =>
    suggestions.find((s) => s.id === sid)?.explanation
    ?? issues.find((i) => i.id === sid)?.message
    ?? sid;

  // Re-analyse — the ONE recovery control (it replaced the confusing
  // "Re-run replica" / "reset validation" pair, which differed only in how
  // much cache they kept). Wipes every cached artifact for this invoice
  // (extraction cache included), rebuilds the draft from Loaded, then runs
  // the review from scratch. No confirm (removed 18 Aug 2026 — it nagged on
  // every press): the reset is recoverable by re-accepting, and the button
  // is pressed deliberately. Twin cards refetch via the actioned event.
  const reanalyse = async () => {
    if (embedded || !venueId || !doc.invoice_id || reviewing) return;
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
      const adopted = await refetchOwnDoc();
      if (!adopted && out?.document?.data) {
        setDoc(out.document.data as DocData);
        if (typeof out.document.version === 'number') setVersion(out.document.version);
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

  // Opens THIS draft's copy by default. Pass fileId to open another invoice's
  // copy (the already-received duplicate sibling — its file id rides on the
  // delete suggestion because received invoices can't be resolved by id).
  const openCopy = async (opts?: { fileId?: string; nameHint?: string }) => {
    if (embedded || !venueId) return;
    const query = opts?.fileId
      ? `venue_id=${venueId}&file_id=${encodeURIComponent(opts.fileId)}`
      : `venue_id=${venueId}&invoice_id=${doc.invoice_id}`;
    // Open the tab SYNCHRONOUSLY inside the click — window.open AFTER the
    // fetch has lost the user-activation, so popup blockers silently eat it.
    // Navigate the pre-opened tab once the blob arrives.
    const w = window.open('about:blank', '_blank');
    try {
      const r = await apiFetch(`/api/invoice-fixes/file?${query}`);
      if (!r.ok) throw new Error(r.status === 404 ? 'No copy attached' : `Error ${r.status}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      if (w && !w.closed) {
        w.location.replace(url);
      } else {
        // Hard-blocked popup: download instead — no new window needed.
        const a = document.createElement('a');
        a.href = url;
        a.download = `invoice-${opts?.nameHint || doc.reference_number || doc.invoice_id}.pdf`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) {
      if (w && !w.closed) w.close();
      setMessage(e instanceof Error ? e.message : 'Could not open copy');
      setStatus('error');
    }
  };

  // Accept the delete_invoice suggestion: the ONE server-applied fix left —
  // POST /invoice-fixes/accept with the suggestion's payload (web only).
  const acceptFix = async (s: Suggestion) => {
    if (embedded || !venueId) return;
    setAccepting(s.id);
    setMessage('');
    try {
      const res = await apiFetch('/api/invoice-fixes/accept', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, invoice_id: doc.invoice_id, fix: s.payload }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
      }
      const out = await res.json();
      if (out?.deleted) {
        // The draft was deleted from Loaded — terminal state (tombstone).
        setDeletedDraft(true);
        setStatus('done');
        // Siblings may reference this draft (duplicate pair) — tell them.
        window.dispatchEvent(new CustomEvent(INVOICE_ACTIONED_EVENT, {
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

  // ---- Loaded creates (web-only writes) + local link --------------------
  // Link a line to a stock item as a LOCAL draft edit. Nothing is written to
  // Loaded until Accept & Receive — the receive request carries the item id
  // and do_receive registers the supplier variant then. The item_unmatched
  // issue clears via its clears_when (linked_item_id not_null); the PO
  // reference re-attaches server-side on the next open.
  // The PO reconciliation beside the lines (ordered qty, "ordered, not
  // delivered", substitutes) is SERVER-owned derived state: the patch
  // response carries it back recomputed, and the merge at patchDoc paints it.
  // Never patch those fields from here — two writers is exactly how the list
  // drifted empty on undo (INV-958).
  const applyLocalLink = (
    lineId: string,
    itemId: string,
    itemName: string | null,
    sugg: Suggestion | null = null,
  ) => {
    const idx = docRef.current.lines.findIndex((l) => l.id === lineId);
    if (idx < 0) return;
    const fields: Partial<Line> = { linked_item_id: itemId, item_name: itemName };
    const ops: Record<string, unknown>[] = [
      { op: 'update_line', line_id: lineId, index: idx, fields },
    ];
    // Driven by an accepted suggestion: record it in the SAME patch, or the
    // suggestion stays pending forever and the click looks like it did
    // nothing — the create_unit bug, exactly (11 Aug 2026).
    let log: SuggestionAction[] | null = null;
    if (sugg) {
      const prev = docRef.current.lines[idx];
      const [nextLog, logOp] = recordOp({
        suggestion_id: sugg.id,
        action: 'accepted',
        by: 'user',
        at: nowIso(),
        before: {
          linked_item_id: prev?.linked_item_id ?? null,
          item_name: prev?.item_name ?? null,
        },
        after: { ...fields },
      });
      log = nextLog;
      ops.push(logOp);
    }
    setDoc((prev) => {
      const lines = prev.lines.map((l, i) => (i === idx ? { ...l, ...fields } : l));
      return log ? { ...prev, lines, suggestion_actions: log } : { ...prev, lines };
    });
    if (workingDocId) patchDoc(ops);
    setItemForm(null);
    setLinkQuery('');
  };
  // Link the line to an EXISTING Loaded item (a server linked_item_id
  // suggestion goes through acceptSuggestion instead — this is the manual
  // pick from the create form's search).
  const linkItem = (lineId: string, itemId: string) => {
    if (embedded) return;
    const cat = stockItems.find((x) => x.id === itemId);
    applyLocalLink(lineId, itemId, cat?.name ?? null);
  };

  // A BLOCKING unit_missing issue carries the copy's confidently-read unit name
  // (never a bare unlinked string Loaded's OCR left behind): offer to create it
  // inline to clear the blocker. The "doesn't exist in Loaded" case is now a
  // create_unit SUGGESTION instead — accepted via createUnitAndApply(idx, name).
  const replicaUnitName = (lineId: string): string | null => {
    for (const i of issues) {
      if (String(i.line_id ?? '') === String(lineId) && i.code === 'unit_missing') {
        const n = i.data?.unit_name;
        if (typeof n === 'string' && n.trim()) return n.trim();
      }
    }
    return null;
  };
  // explicitName (from an accepted create_unit suggestion) skips the inline
  // two-step confirm — accepting the suggestion IS the confirmation.
  const createUnitAndApply = async (
    idx: number,
    explicitName: string | null = null,
    sugg: Suggestion | null = null,
  ) => {
    const l = doc.lines[idx];
    const name = explicitName ?? (l ? replicaUnitName(String(l.id)) : null);
    if (!name || !venueId || embedded || creatingUnitLine) return;
    if (!explicitName && confirmUnitLine !== l.id) {
      setConfirmUnitLine(l.id);
      return;
    }
    setConfirmUnitLine(null);
    setCreatingUnitLine(l.id);
    setMessage('');
    try {
      const res = await apiFetch('/api/invoice-fixes/create-unit', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, name }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
      }
      const out = await res.json();
      if (!out?.unit_id) throw new Error('Loaded did not return the created unit');
      const rec: Unit = { id: out.unit_id, name: out.unit_name ?? name, ratio: out.unit_ratio ?? undefined };
      // Into the dropdown before the line takes it.
      setUnits((prev) => (prev.some((u) => u.id === rec.id) ? prev : [...prev, rec]));
      const fields = { linked_unit_id: rec.id, unit: rec.name, unit_ratio: rec.ratio ?? null };
      const ops: Record<string, unknown>[] = [
        { op: 'update_line', line_id: l.id, index: idx, fields },
      ];
      // Record the accept in the SAME patch as the line. Without this the
      // suggestion stayed pending forever: the unit landed, the chip never
      // cleared, and every further click created the unit again (eleven
      // create-unit POSTs in 90 seconds, 11 Aug 2026). Undo needs the
      // before/after too, and the autopilot metric reads this log — an
      // unrecorded accept reads as a silent rejection.
      let log: SuggestionAction[] | null = null;
      if (sugg) {
        const prev = docRef.current.lines[idx];
        const [nextLog, logOp] = recordOp({
          suggestion_id: sugg.id,
          action: 'accepted',
          by: 'user',
          at: nowIso(),
          before: {
            linked_unit_id: prev?.linked_unit_id ?? null,
            unit: prev?.unit ?? null,
            unit_ratio: prev?.unit_ratio ?? null,
          },
          after: { ...fields },
        });
        log = nextLog;
        ops.push(logOp);
      }
      setDoc((prev) => {
        const lines = prev.lines.map((x, i) => (i === idx ? { ...x, ...fields } : x));
        return log ? { ...prev, lines, suggestion_actions: log } : { ...prev, lines };
      });
      if (workingDocId) patchDoc(ops);
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Could not create the unit in Loaded');
    } finally {
      setCreatingUnitLine(null);
    }
  };

  // The copy names a supplier no Loaded record covers (supplier_unresolved,
  // with no supplier suggestion): create it and link — resolve-first
  // server-side (an existing record is returned, never duplicated), two-step
  // confirm. The CREATE is the one Loaded write; the invoice takes the
  // supplier locally (written at receive), clearing the issue via clears_when.
  const unresolvedSupplierName =
    (docLive.replica?.supplier_name || doc.supplier_name || '').trim() || null;
  const createSupplierAndApply = async () => {
    const name = unresolvedSupplierName;
    if (!name || !venueId || embedded || creatingSupplier) return;
    if (!confirmSupplier) {
      setConfirmSupplier(true);
      return;
    }
    setConfirmSupplier(false);
    setCreatingSupplier(true);
    setMessage('');
    try {
      const res = await apiFetch('/api/invoice-fixes/create-supplier', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, name }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
      }
      const out = await res.json();
      if (!out?.supplier_id) throw new Error('Loaded did not return the created supplier');
      const rec = { id: out.supplier_id as string, name: (out.supplier_name as string) ?? name };
      // Into the dropdown before the header takes it.
      setSuppliers((prev) => (prev.some((s) => s.id === rec.id) ? prev : [...prev, rec]));
      patchHeader({ linked_supplier_id: rec.id, supplier_name: rec.name });
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Could not create the supplier in Loaded');
    } finally {
      setCreatingSupplier(false);
    }
  };

  // Explicitly create a NEW stock item (+ its supplier variant) in Loaded and
  // link the line — a deliberate, controlled action, never silent on receive.
  // ONE implementation for both entry points: the inline form and an accepted
  // `create_item` suggestion (which carries the replica's name + stock group).
  // Loaded needs the line's UNIT to create the item, so the unit is resolved
  // first — that is why Accept all runs unit creations ahead of item ones.
  const createItemAndApply = async (
    lineId: string,
    name: string,
    groupId: string,
    sugg: Suggestion | null = null,
  ) => {
    if (!venueId || embedded || creatingItem) return;
    const line = docRef.current.lines.find((l) => l.id === lineId);
    if (!line?.linked_unit_id) {
      setStatus('error');
      setMessage('Resolve this line’s unit before creating the stock item.');
      return;
    }
    if (!name || !groupId) {
      setStatus('error');
      setMessage('A name and a stock group are required to create the item.');
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
          line_id: lineId,
          name,
          group_id: groupId,
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
        applyLocalLink(lineId, out.item_id, out.item_name ?? name, sugg);
      }
      setItemForm(null);
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Could not create the stock item');
    } finally {
      setCreatingItem(false);
    }
  };
  const createItem = () => {
    if (!itemForm) return;
    void createItemAndApply(itemForm.lineId, itemForm.name, itemForm.groupId);
  };

  // Loaded names a brand on the line but has no record for it, and refuses to
  // receive the line until it does. Resolve-first server-side, so a repeated
  // click returns the existing record instead of duplicating it.
  const createBrandAndApply = async (
    lineId: string,
    name: string,
    sugg: Suggestion | null = null,
  ) => {
    if (!venueId || embedded || !name || creatingBrandLine) return;
    const idx = docRef.current.lines.findIndex((l) => String(l.id) === String(lineId));
    if (idx < 0) return;
    setCreatingBrandLine(lineId);
    setMessage('');
    try {
      const res = await apiFetch('/api/invoice-fixes/create-brand', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, name }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
      }
      const out = await res.json();
      if (!out?.brand_id) throw new Error('Loaded did not return the created brand');
      const fields: Partial<Line> = {
        linked_brand_id: out.brand_id,
        brand: out.brand_name ?? name,
      };
      const ops: Record<string, unknown>[] = [
        { op: 'update_line', line_id: lineId, index: idx, fields },
      ];
      let log: SuggestionAction[] | null = null;
      if (sugg) {
        const prev = docRef.current.lines[idx];
        const [nextLog, logOp] = recordOp({
          suggestion_id: sugg.id,
          action: 'accepted',
          by: 'user',
          at: nowIso(),
          before: {
            linked_brand_id: prev?.linked_brand_id ?? null,
            brand: prev?.brand ?? null,
          },
          after: { ...fields },
        });
        log = nextLog;
        ops.push(logOp);
      }
      setDoc((prev) => {
        const lines = prev.lines.map((l, i) => (i === idx ? { ...l, ...fields } : l));
        return log ? { ...prev, lines, suggestion_actions: log } : { ...prev, lines };
      });
      if (workingDocId) patchDoc(ops);
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Could not create the brand');
    } finally {
      setCreatingBrandLine(null);
    }
  };

  // Open the create-item form for a line, prefilled from the item_unmatched
  // issue's suggested name/group when the replica's matcher proposed one.
  const openItemForm = (l: Line) => {
    const iss = issues.find((i) => i.code === 'item_unmatched' && String(i.line_id) === String(l.id));
    const d = (iss?.data || {}) as { suggested_name?: string; suggested_group_id?: string };
    setItemForm(
      itemForm?.lineId === l.id
        ? null
        : { lineId: l.id, name: d.suggested_name || l.description || l.code || '', groupId: d.suggested_group_id || '' },
    );
    setLinkQuery('');
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
  // update_line/remove_line address it locally; do_receive appends it and
  // Loaded assigns the real id on receive.
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

  // Accept & Receive: the SERVER builds the receive request from the doc
  // (working values incl. accepted suggestions + manual edits) — the body is
  // just the identity pair. A 409 means the invoice changed in Loaded since
  // the review: refetch and say so.
  const receive = async () => {
    setStatus('saving');
    setMessage('');
    try {
      const res = await apiFetch('/api/invoice-fixes/receive', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, invoice_id: doc.invoice_id }),
      });
      if (res.status === 409) {
        const b = await res.json().catch(() => ({}));
        await refetchOwnDoc();
        throw new Error(typeof b.detail === 'string' ? b.detail
          : 'This invoice changed in Loaded since it was reviewed — reopen it to re-review before receiving.');
      }
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
      // patching it again (a local set_status patch used to race the server's
      // bump into a false conflict). Fallback patch only if it didn't mark it.
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
      // Tell sibling cards: their duplicate/PO issues may have just flipped
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

  // ---- Shared renderers (chips + rows read the SAME suggestion objects) --
  const suggChip = (s: Suggestion | undefined, label?: string) => {
    if (!s || doneState || viewLoaded) return null;
    return (
      <div>
        <span style={chipStyle} title={s.explanation}>
          <span>{label ?? `${fmtVal(s.current)} → ${fmtVal(s.proposed)}`}</span>
          <button type="button" onClick={() => acceptSuggestion(s)}
            title={`Accept — ${s.explanation}`} aria-label="Accept suggestion"
            style={{ ...chipBtn, color: '#2e7d4f' }}>✓</button>
          <button type="button" onClick={() => dismissAction(s.id)}
            title="Dismiss this suggestion" aria-label="Dismiss suggestion"
            style={{ ...chipBtn, color: '#c0392b' }}>✕</button>
        </span>
      </div>
    );
  };
  const deleteLinks = (s: Suggestion) => {
    const p = (s.payload || {}) as Record<string, string | undefined>;
    const links: { label: string; href?: string; onClick?: () => void }[] = [];
    if (p.duplicate_of_invoice_id) links.push({ label: 'Received invoice in Loaded', href: loadedInvoiceUrl(p.duplicate_of_invoice_id) });
    if (!p.duplicate_of_invoice_id && p.duplicate_of_purchase_order_id) links.push({ label: 'Received order in Loaded', href: loadedPoUrl(p.duplicate_of_purchase_order_id) });
    if (p.duplicate_of_file_id) links.push({ label: 'View received copy', onClick: () => openCopy({ fileId: p.duplicate_of_file_id, nameHint: `${doc.reference_number || 'duplicate'}-received` }) });
    if (p.duplicate_of_invoice_id && !p.duplicate_of_file_id) links.push({ label: '(received without a copy attached)' });
    return links;
  };
  const suggRow = (s: Suggestion) => {
    const st = stateOf(s.id);
    const a = lastAction.get(s.id);
    const byNorm = a?.by === 'norm';
    const isDelete = s.kind === 'delete_invoice';
    // Received/deleted with a still-pending row: the user went past it — the
    // record must read actioned-vs-not, never "still awaiting a decision".
    const notActioned = st === 'pending' && doneState;
    const valuePair = s.current != null || s.proposed != null
      ? ` (${fmtVal(s.current)} → ${fmtVal(s.proposed)})` : '';
    const links = isDelete ? deleteLinks(s) : [];
    return (
      <div key={s.id} style={{ fontSize: '0.68rem', color: st === 'accepted' ? '#2e7d4f' : st === 'dismissed' || notActioned ? '#9ca3af' : '#8a6d3b', display: 'flex', gap: 8, padding: '2px 0', alignItems: 'center' }}>
        <span>{st === 'accepted' ? '✓' : st === 'dismissed' ? '⊘' : notActioned ? '○' : '●'}</span>
        <span style={{ flex: 1, ...(st === 'accepted' ? { textDecoration: 'line-through', color: '#9ca3af' } : {}) }}>
          {s.explanation}{st === 'pending' && !notActioned ? valuePair : ''}
          {st === 'dismissed' && <span style={{ fontStyle: 'italic' }}> — dismissed</span>}
          {notActioned && <span style={{ fontStyle: 'italic' }}> — not actioned</span>}
        </span>
        {byNorm && st !== 'pending' && (
          <span title={a?.at ? `by Norm at ${a.at}` : 'by Norm'}
            style={{ fontSize: '0.58rem', color: '#5a5a8a', background: '#eef1f8', border: '1px solid #ccd3e6', borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap' }}>
            {st === 'accepted' ? 'applied by Norm' : 'dismissed by Norm'}
          </span>
        )}
        {!embedded && links.length > 0 && (
          <span style={{ display: 'flex', gap: 8, whiteSpace: 'nowrap' }}>
            {links.map((lk) =>
              lk.href ? (
                <a key={lk.label} href={lk.href} target="_blank" rel="noreferrer"
                  style={{ fontSize: '0.62rem', color: '#2563a8', textDecoration: 'underline' }}>
                  {lk.label} ↗
                </a>
              ) : lk.onClick ? (
                <button key={lk.label} type="button" onClick={lk.onClick}
                  style={{ fontSize: '0.62rem', padding: 0, border: 'none', background: 'none', color: '#2563a8', textDecoration: 'underline', cursor: 'pointer' }}>
                  {lk.label}
                </button>
              ) : (
                <span key={lk.label} style={{ fontSize: '0.62rem', color: '#9ca3af', fontStyle: 'italic' }}>{lk.label}</span>
              ),
            )}
          </span>
        )}
        {st !== 'pending' && !doneState && !isDelete && (
          <button type="button" onClick={() => undoAction(s, s.id)}
            title={st === 'dismissed' ? 'restore this suggestion' : 'undo this change'}
            style={{ fontSize: '0.62rem', padding: '2px 10px', border: '1px solid #ccc', background: '#fff', color: '#666', borderRadius: 4, cursor: 'pointer', whiteSpace: 'nowrap' }}>
            Undo
          </button>
        )}
        {/* One button, one decision: the user accepts the change or leaves
            it (18 Aug 2026 — Dismiss removed; not-accepting IS declining,
            and suggestions never block the receive). */}
        {st === 'pending' && !doneState && !(isDelete && embedded) && (
          <button type="button" onClick={() => acceptSuggestion(s)} disabled={accepting !== null}
            style={{ fontSize: '0.62rem', padding: '2px 10px', border: '1px solid #b78a2f', background: accepting === s.id ? '#f0e6cc' : '#fff', color: '#8a6d3b', borderRadius: 4, cursor: accepting !== null ? 'default' : 'pointer', whiteSpace: 'nowrap', opacity: accepting !== null && accepting !== s.id ? 0.5 : 1 }}>
            {accepting === s.id ? 'Applying…' : 'Accept'}
          </button>
        )}
      </div>
    );
  };
  // Each blocker's remedy, run from the blocker itself. These are the same
  // Loaded writes the old create SUGGESTIONS performed — moved here so a
  // decision appears once, on the row that says why it matters.
  const [actioningIssue, setActioningIssue] = useState<string | null>(null);
  const runIssueAction = async (i: Issue) => {
    if (!i.action || actioningIssue || doneState) return;
    const p = (i.action.payload || {}) as Record<string, string | undefined>;
    const lineId = String(i.line_id || '');
    setActioningIssue(i.id);
    try {
      if (['delete_invoice', 'delete_non_invoice', 'delete_unreadable'].includes(String(i.action.kind))) {
        // A Loaded write through the same verified /accept applier the
        // duplicate suggestion has always used; the tombstone handling
        // mirrors acceptFix. Web only (the button is !embedded already).
        const res = await apiFetch('/api/invoice-fixes/accept', {
          method: 'POST',
          body: JSON.stringify({ venue_id: venueId, invoice_id: doc.invoice_id, fix: i.action.payload }),
        });
        if (!res.ok) {
          const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
          throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${res.status}`);
        }
        const out = await res.json();
        if (out?.deleted) {
          setDeletedDraft(true);
          setStatus('done');
          // Siblings may reference this draft (duplicate pair) — tell them.
          window.dispatchEvent(new CustomEvent(INVOICE_ACTIONED_EVENT, {
            detail: { venueId, invoiceId: doc.invoice_id, sourceDocId: workingDocId },
          }));
        }
      } else if (i.action.kind === 'strike' && lineId) {
        // Same op Accept applies from the suggestion row: cross the line out.
        const idx = docRef.current.lines.findIndex((l) => String(l.id) === lineId);
        if (idx >= 0) onStrike(idx, true);
      } else if (i.action.kind === 'create_brand') {
        await createBrandAndApply(lineId, p.brand_name || '');
      } else if (i.action.kind === 'create_item') {
        await createItemAndApply(lineId, p.name || '', p.group_id || '');
      } else if (i.action.kind === 'create_unit') {
        const idx = docRef.current.lines.findIndex((l) => String(l.id) === lineId);
        if (idx >= 0) await createUnitAndApply(idx, p.unit_name || '');
      } else if (i.action.kind === 'create_supplier') {
        await createSupplierAndApply();
      }
      // Record it against the ISSUE, exactly as the server's own gate walk
      // does. Most blockers also clear themselves through clears_when once the
      // link lands, but `unit_missing` deliberately has none — a unit already
      // on Loaded's line is Loaded's OCR of the same paper — so without this
      // the blocker would outlive the unit that resolved it.
      const [log, logOp] = recordOp({
        suggestion_id: i.id, action: 'accepted', by: 'user', at: nowIso(),
      });
      setDoc((prev) => ({ ...prev, suggestion_actions: log }));
      if (workingDocId) patchDoc([logOp]);
    } finally {
      setActioningIssue(null);
    }
  };

  // The unit the copy names, when Loaded has no such unit yet. Read off the
  // blocker that already carries it — one source, so the chip and the blocked
  // row can never disagree about which unit is meant.
  const pendingUnitIssue = (lineId: string | number): Issue | undefined =>
    blockingIssues.find(
      (i) => String(i.line_id) === String(lineId)
        && i.action?.kind === 'create_unit'
        && issueStateOf(i) === 'open',
    );
  const pendingUnitName = (lineId: string | number): string | null =>
    (pendingUnitIssue(lineId)?.action?.payload?.unit_name as string | undefined) || null;
  const runPendingUnit = (lineId: string | number) => {
    const issue = pendingUnitIssue(lineId);
    if (issue) void runIssueAction(issue);
  };

  const issueRow = (i: Issue) => {
    const st = issueStateOf(i);
    const open = st === 'open';
    const color = !i.blocking ? '#8a6d3b' : open ? '#c0392b' : '#9ca3af';
    const gateLabel = i.gate ? gateLabels[i.gate] : undefined;
    return (
      <div key={i.id} style={{ fontSize: '0.66rem', color, padding: '1px 0' }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
        <span>{i.blocking ? (open ? '✗' : '✓') : '•'}</span>
        <span style={{ flex: 1, ...(st !== 'open' ? { textDecoration: 'line-through' } : {}) }}>
          {i.message}
          {st === 'checked' && <span style={{ fontStyle: 'italic', textDecoration: 'none' }}> — checked</span>}
        </span>
        {/* One button, one decision — the same Accept as the suggestion
            rows (18 Aug 2026: show-line / I've-checked-this / Dismiss
            removed; a blocker clears by actually fixing it — this action,
            the line's own controls, or the venue gate that authorises
            autopilot past it). The title keeps naming what Accept does. */}
        {open && i.action && !doneState && !embedded && (
          <button type="button" onClick={() => runIssueAction(i)}
            disabled={actioningIssue === i.id}
            title={ISSUE_ACTION_LABELS[i.action.kind]
              ? `${ISSUE_ACTION_LABELS[i.action.kind]}${gateLabel ? ` — Norm can do this unattended once "${gateLabel}" is on for this venue` : ''}`
              : undefined}
            style={{ fontSize: '0.62rem', padding: '2px 10px', border: '1px solid #b78a2f', background: actioningIssue === i.id ? '#f0e6cc' : '#fff', color: '#8a6d3b', borderRadius: 4, cursor: actioningIssue === i.id ? 'default' : 'pointer', whiteSpace: 'nowrap', fontFamily: 'inherit' }}>
            {actioningIssue === i.id ? 'Applying…' : 'Accept'}
          </button>
        )}
      </div>
      {/* Why autopilot stopped, in the words of the setting that would change
          it — so "why didn't this receive?" is answered on the row itself. */}
      {open && gateLabel && (
        <div style={{ marginLeft: 14, fontSize: '0.6rem', color: '#9ca3af', fontStyle: 'italic' }}>
          Auto-receive needs “{gateLabel}” switched on for this venue
        </div>
      )}
      </div>
    );
  };
  const confidenceChip = !dojo && liveConfidence && !draftDeleted ? (
    <span style={{
      fontSize: '0.6rem', fontWeight: 700, padding: '1px 8px', borderRadius: 8, whiteSpace: 'nowrap',
      background: liveConfidence === 'ready' ? '#e7f5ec' : '#fdf6e7',
      color: liveConfidence === 'ready' ? '#2e7d4f' : '#8a6d3b',
      border: `1px solid ${liveConfidence === 'ready' ? '#b7e0c6' : '#e6d3a3'}`,
    }}>
      {liveConfidence === 'ready' ? 'Ready to receive' : 'Needs review'}
    </span>
  ) : null;
  const reviewSummaryText = !reviewed
    ? (reviewing ? 'reviewing against the copy…' : 'not yet reviewed')
    : (liveConfidence === 'ready' ? 'ready to receive' : `needs review · ${blockingOpen.length} blocking`)
      + (pendingSuggestions.length ? ` · ${pendingSuggestions.length} suggested` : '');

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
    {/* Admin X-ray: the whole body renders read-only while a non-Norm view is
        on (the slider itself is span-based, so it stays clickable inside the
        disabled fieldset). */}
    <fieldset disabled={viewLoaded} style={{ border: 'none', margin: 0, padding: 0, minWidth: 0 }}>
      {/* A credit note stays receivable, but the consequence is surfaced as a
          visible banner (not a review "note"): receiving it reverses stock. */}
      {isCredit && !dojo && (
        <div style={{ padding: '5px 10px', background: '#fdecea', color: '#a4322a', borderBottom: '1px solid #f0c2bc', fontSize: '0.66rem', fontWeight: 600 }}>
          Credit note — receiving this reverses stock and cost (quantities are negative).
        </div>
      )}
      {/* Header — editable form (Loaded-parity) */}
      <div style={{ padding: '0.7rem 0.9rem', background: 'linear-gradient(#faf9f7,#f5f3ef)', borderBottom: '1px solid #eee' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: '0.9rem', fontWeight: 700, color: '#3a3a3a' }}>
              {isCredit ? 'Receive Credit Note' : 'Receive Invoice'}
            </span>
            {/* Never let a credit be mistaken for a delivery: it REVERSES
                stock and cost, and every quantity on it is negative. */}
            {isCredit && (
              <span title="this document credits the supplier — receiving it reverses stock and cost"
                style={{ fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.04em', padding: '2px 7px', borderRadius: 4, background: '#fdecea', color: '#a4322a', border: '1px solid #f0c2bc', whiteSpace: 'nowrap' }}>
                CREDIT NOTE
              </span>
            )}
            {confidenceChip}
          </span>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            {/* Admin X-ray slider: Norm's editable draft | the verbatim PDF
                extraction | Loaded's pristine snapshot | the replica (our full
                resolution, read-only). */}
            {!embedded && isPlatformAdmin && docLive.loaded_snapshot && (
              <span role="group" aria-label="Data source"
                title="Admin: flip every field between Norm's draft, what we extracted from the copy, what Loaded currently holds, and the replica"
                style={{ display: 'inline-flex', height: 22, border: `1px solid ${viewLoaded ? '#b78a2f' : '#d8d4cc'}`, borderRadius: 4, overflow: 'hidden', fontSize: '0.62rem', userSelect: 'none' }}>
                {([
                  ['norm', 'Norm', true],
                  ['extracted', 'Extracted', !!docLive.extracted_snapshot],
                  ['loaded', 'Loaded', true],
                  ['replica', 'Replica', !!docLive.replica],
                ] as const).map(([mode, label, available]) => (
                  <span key={mode} role="button" tabIndex={available ? 0 : -1}
                    onClick={() => { if (available) setViewMode(mode); }}
                    onKeyDown={(e) => { if (available && (e.key === 'Enter' || e.key === ' ')) setViewMode(mode); }}
                    title={mode === 'extracted' && !available ? 'No extraction for this invoice (no readable copy)' : mode === 'replica' && !available ? 'No replica — the review has not produced one yet (re-run the replica)' : mode === 'replica' ? "Norm's own full resolution of the copy against the catalogue — the suggestions' source" : undefined}
                    style={{ display: 'inline-flex', alignItems: 'center', padding: '0 8px', cursor: available ? 'pointer' : 'not-allowed', background: viewMode === mode ? (mode === 'norm' ? '#3a3a3a' : '#b78a2f') : '#fff', color: viewMode === mode ? '#fff' : available ? '#8a8a8a' : '#d0d0d0', whiteSpace: 'nowrap' }}>
                    {label}
                  </span>
                ))}
              </span>
            )}
            {viewLoaded && (
              <span style={{ fontSize: '0.6rem', color: '#8a6d3b', whiteSpace: 'nowrap' }}>
                {viewMode === 'extracted' ? "showing the copy's extraction — read-only" : viewMode === 'replica' ? 'showing the replica (our full resolution) — read-only' : "showing Loaded's data — read-only"}
              </span>
            )}
            {compact && (
              <button type="button" onClick={() => setExpandedFull((v) => !v)}
                style={{ fontSize: '0.66rem', padding: '2px 9px', border: '1px solid #d8d4cc', borderRadius: 4, background: '#fff', color: '#6b6b6b', cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
                {expandedFull ? 'Hide details ▾' : 'Show full invoice ▸'}
              </button>
            )}
            {/* Deep-link to Loaded's own UI (the user's Loaded session
                authenticates) — the escape hatch for everything the card
                can't fix (attach/replace a copy, credit notes…). Received
                invoices resolve directly; unreceived drafts don't resolve on
                the invoice route, so they land on the invoices list. */}
            {!embedded && !doc.is_deleted && (
              <a href={doc.is_received ? loadedInvoiceUrl(doc.invoice_id) : `${LOADED_APP_URL}/stock/invoices`}
                target="_blank" rel="noreferrer"
                title={doc.is_received ? 'Open this invoice in Loaded' : 'Open Loaded invoices'}
                aria-label={doc.is_received ? 'Open this invoice in Loaded' : 'Open Loaded invoices'}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 4, height: 22, padding: '0 7px', border: '1px solid #d8d4cc', borderRadius: 4, background: '#fff', color: '#6b6b6b', fontSize: '0.62rem', textDecoration: 'none', whiteSpace: 'nowrap' }}>
                Open in Loaded ↗
              </a>
            )}
            {!embedded && doc.file_id && (
              <button type="button" onClick={() => openCopy()} title="View invoice copy" aria-label="View invoice copy"
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 22, height: 22, padding: 0, border: '1px solid #d8d4cc', borderRadius: 4, background: '#fff', color: '#6b6b6b', cursor: 'pointer' }}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                </svg>
              </button>
            )}
            {/* Dojo intake is the "Can't receive" button below — one intake
                for admin and non-admin alike; it files the PDF AND kicks the
                sensei on every press (the admin-only Add-to-dojo button was
                removed Aug 2026). */}
          </div>
        </div>
        {collapsed && (
          <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap', fontSize: '0.8rem' }}>
            <strong style={{ color: '#3a3a3a' }}>{doc.supplier_name || '—'}</strong>
            <span style={{ color: '#666' }}>{doc.reference_number || '(no number)'}</span>
            {doc.issued_at && <span style={{ color: '#999' }}>{dateVal(doc.issued_at)}</span>}
            <span style={{ fontSize: '0.66rem', color: '#8a6d3b' }}>{reviewSummaryText}</span>
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
              {/* X-ray modes show the VALUE (the extraction's printed name /
                  the replica's resolution) as text — the select below can
                  only display ids from the venue's list, which is how the
                  Extracted tab showed "[Unnamed Supplier]" while the copy
                  read 'BIDFOOD FSV DUNEDIN' (110016259, 19 Aug 2026). */}
              {viewLoaded ? (
                <span style={{ ...inputStyle, width: '100%', display: 'inline-block', background: '#f7f5f1', color: '#444' }}>
                  {doc.supplier_name || '—'}
                </span>
              ) : (<>
              {/* An unlinked supplier renders AMBER — Loaded's server 500s on
                  receiving a supplier-less invoice. */}
              <select value={doc.linked_supplier_id || ''} disabled={doneState} onChange={(e) => onSupplier(e.target.value)}
                style={{ ...inputStyle, width: '100%', ...(!doneState && !doc.linked_supplier_id && doc.invoice_id ? { border: '1px solid #f0c88a', background: '#fff4e5' } : {}) }}>
                {!suppliers.some((s) => s.id === doc.linked_supplier_id) && (
                  <option value={doc.linked_supplier_id || ''}>{doc.supplier_name || 'Select supplier'}</option>
                )}
                {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
              {/* The server's supplier suggestion (the copy resolved to a
                  different/missing Loaded record) — same object as its row in
                  the summary list. */}
              {suggChip(supplierSugg, supplierSugg ? `${fmtVal(supplierSugg.current)} → ${fmtVal(supplierSugg.proposed)}` : undefined)}
              {/* No Loaded record covers the printed supplier (the
                  supplier_unresolved issue): create it — two-step confirm. */}
              {!doneState && !embedded && !supplierSugg && unresolvedSupplierName
                && issues.some((i) => i.code === 'supplier_unresolved' && issueStateOf(i) === 'open') && (
                <span style={{ fontSize: '0.6rem', color: '#c0392b', marginTop: 2 }}>
                  no Loaded supplier matches ‘{unresolvedSupplierName}’ — pick one, or{' '}
                  <button type="button" onClick={() => { void createSupplierAndApply(); }} disabled={creatingSupplier}
                    style={{ ...linkBtn, color: '#8a2f2f' }}>
                    {creatingSupplier ? 'creating…' : confirmSupplier ? `confirm — create '${unresolvedSupplierName}'` : 'create it'}
                  </button>
                </span>
              )}
              </>)}
            </label>
            <label style={fieldCol}>
              <span style={microLabel}>Order Number</span>
              {/* Same x-ray rule: show the order number the view holds (the
                  copy's printed reference in Extracted mode). */}
              {viewLoaded ? (
                <span style={{ ...inputStyle, width: '100%', display: 'inline-block', background: '#f7f5f1', color: '#444' }}>
                  {doc.purchase_order_number || '—'}
                </span>
              ) : (<>
              <select value={doc.linked_purchase_order_id || ''} disabled={doneState} onChange={(e) => onPo(e.target.value)}
                style={{ ...inputStyle, width: '100%', ...(poSuggs.length && !doneState ? { border: '1px solid #b78a2f', background: '#fdf6e7' } : {}) }}>
                {/* Split order (reference accepted): the field shows the order
                    REFERENCE — Loaded's 1:1 link stays with the sibling, so
                    the select value remains '' (never a link). */}
                <option value="">
                  {doc.split_po_id && !doc.linked_purchase_order_id && doc.purchase_order_number
                    ? `${doc.purchase_order_number} — split order (reference only)`
                    : 'Not linked'}
                </option>
                {/* A linked PO may be an older, already-received order that
                    isn't in the open-PO picker list — keep it shown as the
                    current value rather than reading as "Not linked". */}
                {doc.linked_purchase_order_id && !candidatePos.some((p) => p.id === doc.linked_purchase_order_id) && (
                  <option value={doc.linked_purchase_order_id}>{doc.purchase_order_number || '(linked)'}</option>
                )}
                {candidatePos.map((p) => (
                  <option key={p.id} value={p.id}>{(p.order_number || '(linked)')}{p.supplier_name ? ` — ${p.supplier_name}` : ''}</option>
                ))}
              </select>
              {/* PO suggestions (link / unlink / keep-split-reference) — one
                  chip each, the same objects as the summary rows. */}
              {poSuggs.map((s) => (
                <Fragment key={s.id}>
                  {suggChip(s, s.kind === 'link_po'
                    ? `link order ${fmtVal(s.proposed)}`
                    : s.kind === 'unlink_po'
                      ? `remove order ${fmtVal(s.current)}`
                      : `keep reference ${fmtVal(s.proposed)} (split order)`)}
                </Fragment>
              ))}
              {/* Split order (accepted): where the rest of the order went. */}
              {doc.split_po_id && !doc.linked_purchase_order_id && doc.split_sibling_invoice_id && (
                <span style={{ fontSize: '0.6rem', color: '#8a6d3b', marginTop: 2 }}>
                  part of a split order — also invoiced on{' '}
                  <a href={loadedInvoiceUrl(doc.split_sibling_invoice_id)} target="_blank" rel="noreferrer" style={{ color: '#8a6d3b' }}>
                    the sibling invoice
                  </a>
                </span>
              )}
              </>)}
            </label>
            {doc.order_date && (
              <label style={fieldCol}>
                <span style={microLabel}>Order Date</span>
                <span style={{ fontSize: '0.8rem', color: '#555', padding: '4px 0' }}>{dateVal(doc.order_date)}</span>
              </label>
            )}
            <label style={fieldCol}>
              <span style={microLabel}>Received Date</span>
              <input type="date" value={dateVal(doc.received_at)} disabled={doneState}
                onChange={(e) => patchHeader({ received_at: e.target.value || null })} style={{ ...inputStyle, width: '100%' }} />
            </label>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label style={fieldCol}>
              <span style={microLabel}>Invoice Number</span>
              <input type="text" value={doc.reference_number || ''} disabled={doneState}
                onChange={(e) => patchHeader({ reference_number: e.target.value })}
                style={{ ...inputStyle, width: '100%', ...(!doneState && headerValueSugg('reference_number') ? { border: '1px solid #b78a2f', background: '#fdf6e7' } : {}) }} />
              {suggChip(headerValueSugg('reference_number'))}
            </label>
            <label style={fieldCol}>
              <span style={microLabel}>Invoice Date</span>
              <input type="date" value={dateVal(doc.issued_at)} disabled={doneState}
                onChange={(e) => patchHeader({ issued_at: e.target.value || null })} style={{ ...inputStyle, width: '100%' }} />
              {suggChip(headerValueSugg('issued_at'))}
            </label>
            <label style={fieldCol}>
              <span style={microLabel}>Due Date</span>
              <input type="date" value={dateVal(doc.due_at)} disabled={doneState}
                onChange={(e) => patchHeader({ due_at: e.target.value || null })} style={{ ...inputStyle, width: '100%' }} />
            </label>
            <label style={fieldCol}>
              <span style={microLabel}>Invoice Total</span>
              <input type="number" step="any" value={doc.total ?? 0} disabled={doneState}
                onChange={(e) => patchHeader({ total: parseFloat(e.target.value) || 0 })}
                style={{ ...inputStyle, width: '100%', fontWeight: 600, ...(!doneState && moneySuggs.length ? { border: '1px solid #b78a2f', background: '#fdf6e7' } : {}) }} />
              {/* Header money suggestions (total / subtotal / tax / discount)
                  live together under the total field. */}
              {moneySuggs.map((s) => (
                <Fragment key={s.id}>
                  {suggChip(s, `${s.field}: ${fmtVal(s.current)} → ${fmtVal(s.proposed)}`)}
                </Fragment>
              ))}
            </label>
          </div>
        </div>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: '0.5rem', fontSize: '0.72rem', color: '#555' }}>
          <input type="checkbox" checked={includesTax} disabled={doneState}
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
              {viewMode === 'extracted' ? (<>
                <th style={{ padding: '0.4rem 0.6rem' }} title="the unit column exactly as printed — often how the line is CHARGED (EA/CTN), not the pack size">Unit (printed)</th>
                <th style={{ padding: '0.4rem 0.6rem' }} title="the delivered unit of ONE item, derived from the document — what recipe costing and stock need">Unit of measure</th>
              </>) : (
              <th style={{ padding: '0.4rem 0.6rem' }}>Unit</th>
              )}
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Qty ordered</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Qty received</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Unit cost</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Tax</th>
              <th style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>Total cost</th>
            </tr>
          </thead>
          <tbody>
            {doc.lines.map((l, idx) => {
              // An accepted strike keeps the row VISIBLE, crossed out and
              // inert: it drops out of the totals and is soft-deleted on
              // receive. Restore lives on the row (and on the record's Undo).
              const struck = !!l.struck;
              const strikeSugg = strikeSuggFor(l.id);
              const itemSugg = itemSuggFor(l.id);
              return (
              <Fragment key={l.id}>
              <tr id={`riv-${uid}-${l.id}`}
                style={{ borderTop: '1px solid #f3f3f3', ...(struck ? { opacity: 0.5, textDecoration: 'line-through', color: '#999' } : {}) }}>
                <td style={{ padding: '0.4rem 0.6rem', color: '#666' }}>{l.display_code || l.code || '—'}</td>
                <td style={{ padding: '0.4rem 0.6rem' }}>
                  {l.item_name || l.description}
                  {/* Unlinked item: NEW rides at the end of the name, the
                      same convention as the unit dropdown's "— NEW". In the
                      LOADED mirror the server decides (item_is_new): Loaded's
                      screen resolves the stock item against the linked order,
                      so a line with no linkedItemId is only NEW when that
                      lookup also came up empty. */}
                  {!struck && (viewMode === 'norm' ? !l.linked_item_id : viewMode === 'loaded' && l.item_is_new) && (
                    <span style={{ color: '#b45309', fontWeight: 600 }}> — NEW</span>
                  )}
                  {/* The server's strike suggestion (the copy doesn't bill
                      this line) — same object as its summary row. */}
                  {!struck && suggChip(strikeSugg, 'not billed on the copy — strike')}
                  {!doneState && struck && viewMode === 'norm' && (
                    <button type="button" onClick={() => onStrike(idx, false)}
                      title="restore this line (it will be received again)"
                      style={{ marginLeft: 6, fontSize: '0.58rem', color: '#666', background: '#fff', border: '1px solid #ccc', borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap', fontFamily: 'inherit', cursor: 'pointer', textDecoration: 'none' }}>
                      restore
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
                  {/* The server's item suggestion. NOT gated on "unlinked":
                      the draft now opens on Loaded's own code match, so the
                      common case is a line that IS linked and the copy (or
                      the order) names a different item — one supplier code
                      covering several cuts. Gating this on !linked_item_id
                      left those suggestions visible only in the summary. */}
                  {!struck && !dojo && viewMode === 'norm' && itemSugg && suggChip(
                    itemSugg,
                    itemSugg.kind === 'create_item'
                      ? `create '${fmtVal(itemSugg.proposed)}'`
                      : itemSugg.current
                        ? `${fmtVal(itemSugg.current)} → ${fmtVal(itemSugg.proposed)}`
                        : `link to '${fmtVal(itemSugg.proposed)}'`,
                  )}
                  {/* No stock item at all — link an existing one or CREATE it;
                      must be resolved before receiving. */}
                  {!l.linked_item_id && !struck && !dojo && viewMode === 'norm'
                    && !itemSugg && !(embedded || doneState || itemForm?.lineId === l.id) && (
                      <button type="button" onClick={() => openItemForm(l)}
                        title="this stock item isn't linked in Loaded — link an existing item or create it before receiving"
                        style={{ ...newBadge, cursor: 'pointer', font: 'inherit', fontWeight: 700 }}>
                        link or create
                      </button>
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
                              <button key={it.id} type="button" onClick={() => linkItem(itemForm.lineId, it.id)}
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
                    <span title="Loaded has no record for this brand and won't receive the line until it does" style={newBadge}>NEW</span>
                  )}
                  {!struck && viewMode === 'norm' && suggChip(
                    brandSuggFor(l.id), `create '${fmtVal(l.brand)}'`,
                  )}
                </td>
                {viewMode === 'extracted' && (
                  <td style={{ padding: '0.4rem 0.6rem', color: '#777' }}>
                    {l.unit || '—'}
                  </td>
                )}
                <td style={{ padding: '0.4rem 0.6rem' }}>
                  {viewMode === 'extracted' ? (
                    <span style={{ fontSize: '0.8rem', color: '#555' }}>
                      {l.unit_of_measure || '—'}
                      {l.unit_unrecognisable && (
                        <span style={{ color: '#b45309' }}> (unreadable)</span>
                      )}
                    </span>
                  ) : viewMode !== 'norm' ? (
                    // X-ray modes are read-only — the unit renders as text
                    // (extracted lines carry no Loaded unit records at all).
                    <span style={{ fontSize: '0.8rem', color: '#555' }}>
                      {l.unit_name || l.unit || '—'}
                      {viewMode === 'loaded' && l.unit_is_new && (
                        <span style={{ color: '#b45309', fontWeight: 600 }}> — NEW</span>
                      )}
                    </span>
                  ) : (
                  <select value={l.linked_unit_id || ''} disabled={doneState || struck}
                    onChange={(e) => onUnit(idx, e.target.value)}
                    style={{ ...inputStyle, minWidth: 120, borderColor: struck ? '#e2e2e2' : unitSuggFor(l.id) ? '#b78a2f' : (!l.linked_unit_id ? '#f0c88a' : '#d1d5db'), background: struck ? '#fafafa' : unitSuggFor(l.id) && !doneState ? '#fdf6e7' : (!l.linked_unit_id && !doneState ? '#fff4e5' : '#fff') }}>
                    {/* The unresolved state lives INSIDE the dropdown: an
                        unlinked unit string renders as the selected option,
                        marked NEW — picking a real unit replaces it. */}
                    {!units.some((u) => u.id === l.linked_unit_id) && (
                      <option value={l.linked_unit_id || ''}>
                        {l.unit ? `${l.unit} — NEW` : 'Select unit'}
                      </option>
                    )}
                    {sortedUnits.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                  )}
                  {/* The server's unit suggestion (the copy's delivered unit
                      resolves to a different Loaded unit). */}
                  {!struck && viewMode === 'norm' && suggChip(unitSuggFor(l.id))}
                  {/* Same delta, for a unit Loaded does not have yet. The
                      Norm view always shows what LOADED holds and puts the
                      change in a chip beneath it — so this reads
                      "6 X 750ML" with "→ 12x375ml (new)" under it, exactly
                      like the quantity and tax chips, rather than the row
                      quietly displaying a unit that does not exist. */}
                  {!struck && viewMode === 'norm' && pendingUnitName(l.id) && (
                    <div>
                      <span style={chipStyle}
                        title={`the copy's delivered unit '${pendingUnitName(l.id)}' doesn't exist in Loaded — create it`}>
                        <span>{`→ ${pendingUnitName(l.id)} (new)`}</span>
                        <button type="button" onClick={() => runPendingUnit(l.id)}
                          disabled={!!actioningIssue}
                          title={`Create '${pendingUnitName(l.id)}' in Loaded and use it`}
                          aria-label="Create this unit"
                          style={{ ...chipBtn, color: '#2e7d4f' }}>✓</button>
                      </span>
                    </div>
                  )}
                  {/* Create-in-Loaded is offered ONLY for a unit the REPLICA
                      read off the copy (unit_not_in_loaded / unit_missing
                      with a unit_name) — never for a bare unlinked string
                      that came from Loaded. Two-step confirm, the one write. */}
                  {!struck && !dojo && viewMode === 'norm' && !doneState && !embedded && (() => {
                    const createName = replicaUnitName(String(l.id));
                    if (!createName) return null;
                    return (
                      <div style={{ fontSize: '0.58rem', color: '#b45309', marginTop: 2 }}>
                        {creatingUnitLine === l.id ? (
                          <span style={{ color: '#8a2f2f' }}>creating unit…</span>
                        ) : confirmUnitLine === l.id ? (
                          <span style={{ color: '#8a2f2f' }}>
                            creates a NEW unit “{createName}” in Loaded —{' '}
                            <button type="button" onClick={() => createUnitAndApply(idx)}
                              style={{ ...linkBtn, color: '#8a2f2f', fontWeight: 700 }}>
                              create it
                            </button>
                            {' · '}
                            <button type="button" onClick={() => setConfirmUnitLine(null)}
                              style={{ ...linkBtn, color: '#888' }}>
                              cancel
                            </button>
                          </span>
                        ) : (
                          <button type="button" onClick={() => createUnitAndApply(idx)}
                            style={{ ...linkBtn, color: '#8a2f2f' }}>
                            the copy says “{createName}” — create it in Loaded
                          </button>
                        )}
                      </div>
                    );
                  })()}
                </td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', color: '#888', fontVariantNumeric: 'tabular-nums' }}>
                  {/* Loaded prints a literal 0 where nothing on the order is
                      left to claim (a second line sharing a code); every other
                      view says "—", which is the honest reading of "no order
                      row for this line". */}
                  {l.quantity_ordered ?? (viewMode === 'loaded' ? 0 : '—')}
                </td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>
                  {/* Amber = a qty suggestion is pending (same treatment as the
                      unit dropdown). Full `border` shorthand — never mix with
                      borderColor. */}
                  <input type="number" step="any" value={l.quantity_received ?? 0} disabled={doneState || struck}
                    onChange={(e) => onQty(idx, parseFloat(e.target.value) || 0)}
                    style={{ ...inputStyle, width: 70, textAlign: 'right', ...(!doneState && !struck && suggFor(l.id, 'quantity_received') ? { border: '1px solid #b78a2f', background: '#fdf6e7' } : {}) }} />
                  {!struck && viewMode === 'norm' && suggChip(suggFor(l.id, 'quantity_received'))}
                </td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', whiteSpace: 'nowrap' }}>
                  {/* Red ↑ when the invoice cost is above the linked PO's cost. */}
                  {!struck && l.reference_cost != null && (l.unit_cost ?? 0) > l.reference_cost + 0.001 && (
                    <span title={`up from ${cur(l.reference_cost)} on the order`} style={{ color: '#c0392b', marginRight: 3 }}>↑</span>
                  )}
                  <input type="number" step="any" value={l.unit_cost ?? 0} disabled={doneState || struck}
                    onChange={(e) => onCost(idx, parseFloat(e.target.value) || 0)}
                    style={{ ...inputStyle, width: 80, textAlign: 'right', ...(!doneState && !struck && suggFor(l.id, 'unit_cost') ? { border: '1px solid #b78a2f', background: '#fdf6e7' } : {}) }} />
                  {!struck && viewMode === 'norm' && suggChip(suggFor(l.id, 'unit_cost'))}
                  {!struck && viewMode === 'norm' && suggChip(suggFor(l.id, 'sale_tax_rate'), 'set the tax rate from the catalogue')}
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
            {/* Loaded lists un-delivered order rows INLINE, in the same table,
                received 0 — so the X-ray does too. Every other view keeps them
                in the "Ordered, not delivered" section below (they are not
                invoice lines and are never sent on receive). */}
            {viewMode === 'loaded' && (doc.ordered_not_received || []).map((o, i) => (
              <tr key={`onr-inline-${o.code || i}-${i}`} style={{ borderTop: '1px solid #f3f3f3', color: '#9a9a9a' }}>
                <td style={{ padding: '0.4rem 0.6rem' }}>{o.code || '—'}</td>
                <td style={{ padding: '0.4rem 0.6rem' }}>{o.description || '—'}</td>
                <td style={{ padding: '0.4rem 0.6rem' }}>—</td>
                <td style={{ padding: '0.4rem 0.6rem' }}>{o.unit || '—'}</td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{o.quantity_ordered ?? '—'}</td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>0</td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{o.unit_cost != null ? cur(o.unit_cost) : '—'}</td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right' }}>—</td>
                <td style={{ padding: '0.4rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{cur(0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Replica X-ray: the resolution log — every decision the replica made,
          the troubleshooting record. */}
      {viewMode === 'replica' && (docLive.replica?.resolution_log?.length ?? 0) > 0 && (
        <div style={{ padding: '0.5rem 0.9rem', borderTop: '1px solid #eee', background: '#fbfaf8' }}>
          <div style={{ ...microLabel, marginBottom: 4 }}>Resolution log</div>
          {docLive.replica!.resolution_log!.map((r, i) => (
            <div key={`rl-${i}`} style={{ fontFamily: 'ui-monospace, monospace', fontSize: '0.62rem', color: '#666', padding: '1px 0' }}>{r}</div>
          ))}
        </div>
      )}

      {/* Split order: PO items that arrived on the SIBLING delivery — they were
          received, just on the other invoice. Read-only; never sent on receive. */}
      {(doc.ordered_received_elsewhere?.length ?? 0) > 0 && (
        <div style={{ padding: '0.5rem 0.9rem', borderTop: '1px solid #eee', background: '#fafafa' }}>
          <div style={{ ...microLabel, marginBottom: 4 }}>
            Ordered, received on the sibling delivery ({doc.ordered_received_elsewhere!.length})
            {doc.split_sibling_invoice_id && (
              <>
                {' · '}
                <a href={loadedInvoiceUrl(doc.split_sibling_invoice_id)} target="_blank" rel="noreferrer" style={{ color: '#8a6d3b' }}>
                  its invoice ↗
                </a>
              </>
            )}
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.72rem', color: '#8a8a8a' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: '#b0b0b0', fontSize: '0.6rem', textTransform: 'uppercase' }}>
                <th style={{ padding: '0.25rem 0.6rem' }}>Code</th>
                <th style={{ padding: '0.25rem 0.6rem' }}>Description</th>
                <th style={{ padding: '0.25rem 0.6rem' }}>Unit</th>
                <th style={{ padding: '0.25rem 0.6rem', textAlign: 'right' }}>Qty ordered</th>
                <th style={{ padding: '0.25rem 0.6rem', textAlign: 'right' }}>Qty received</th>
                <th style={{ padding: '0.25rem 0.6rem', textAlign: 'right' }}>Unit cost</th>
              </tr>
            </thead>
            <tbody>
              {doc.ordered_received_elsewhere!.map((o, i) => (
                <tr key={`${o.code || 'ore'}-${i}`} style={{ borderTop: '1px solid #f0f0f0' }}>
                  <td style={{ padding: '0.25rem 0.6rem' }}>{o.code || '—'}</td>
                  <td style={{ padding: '0.25rem 0.6rem' }}>{o.description || '—'}</td>
                  <td style={{ padding: '0.25rem 0.6rem' }}>{o.unit || '—'}</td>
                  <td style={{ padding: '0.25rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{o.quantity_ordered ?? '—'}</td>
                  <td style={{ padding: '0.25rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{o.quantity_received ?? '—'}</td>
                  <td style={{ padding: '0.25rem 0.6rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{o.unit_cost != null ? cur(o.unit_cost) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Ordered, not delivered — PO items with NO invoice line at all (neither by
          code nor as a substitute). Read-only; never sent on receive. The Loaded
          X-ray shows these inline instead, the way Loaded's own screen does. */}
      {viewMode !== 'loaded' && (doc.ordered_not_received?.length ?? 0) > 0 && (
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
      {!doneState && (
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

      {/* Totals block (bottom, Loaded layout). Display sums only — the stated
          total is a working value the server reconciles; a real drift rides
          in as a header_value suggestion, never derived here. */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '0.5rem 0.9rem', borderTop: '1px solid #eee' }}>
        <div style={{ minWidth: 220, fontSize: '0.78rem', fontVariantNumeric: 'tabular-nums' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: '#555' }}>
            <span>Total excl Tax</span><span>{cur(totals.excl)}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: '#555' }}>
            <span>Tax</span><span>{cur(totals.tax)}</span>
          </div>
          {/* The Loaded mirror shows the discount row even at zero, and a
              Rounding row reconciling the derived total to Loaded's stated
              one — both exactly as Loaded's own screen does. */}
          {(viewMode === 'loaded' || !!totals.discount) && (
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: '#555' }}>
              <span>{viewMode === 'loaded' ? 'Discount incl tax' : 'Discount'}</span>
              <span>{totals.discount ? `−${cur(totals.discount)}` : cur(0)}</span>
            </div>
          )}
          {viewMode === 'loaded' && (() => {
            const stated = typeof doc.total === 'number' ? doc.total : null;
            if (stated === null) return null;
            const rounding = round2(stated - totals.incl);
            // Loaded's Rounding field absorbs its ±10c entry band and nothing
            // more. A larger gap is a real discrepancy between Loaded's own
            // header and its own lines — calling that "rounding" would be a
            // lie, and the Norm view already raises it as a suggestion.
            if (!rounding || Math.abs(rounding) > 0.1) return null;
            return (
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: '#555' }}>
                <span>Rounding</span><span>{rounding < 0 ? `−${cur(-rounding)}` : cur(rounding)}</span>
              </div>
            );
          })()}
          <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid #eee', marginTop: 4, paddingTop: 4, fontWeight: 700 }}>
            <span>Total incl Tax</span><span>{cur(totals.incl)}</span>
          </div>
        </div>
      </div>

      {/* Notes */}
      <div style={{ padding: '0.5rem 0.9rem', borderTop: '1px solid #eee' }}>
        <div style={{ ...microLabel, marginBottom: 3 }}>Notes</div>
        <textarea value={doc.notes || ''} disabled={doneState} onChange={(e) => onNotes(e.target.value)}
          placeholder="Notes on the received goods…" rows={2}
          style={{ ...inputStyle, width: '100%', resize: 'vertical', minHeight: 40 }} />
      </div>
      </>)}

      {/* Dojo banner: run status + every mismatch vs the stored baseline.
          Replaces suggestions/issues/receive in dojo mode. */}
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

      {/* Suggested changes — every server suggestion with its explanation,
          pending first; the SAME objects as the inline chips, so the two
          surfaces can never diverge. The action record renders compactly
          underneath. */}
      {!dojo && viewMode === 'norm' && (suggestions.length > 0 || (reviewed && !doneState)) && (
        <div style={{ padding: '0.55rem 0.9rem', borderTop: '1px solid #eee' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
            <div style={{ ...microLabel, color: '#8a6d3b' }}>
              {suggestions.length === 0
                ? 'No changes suggested'
                : `Suggested changes (${pendingSuggestions.length ? `${pendingSuggestions.length} pending` : 'all decided'})`}
            </div>
            {!doneState && !embedded && (
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                {acceptAllCount > 0 && (
                  <button type="button" onClick={acceptAllSuggestions} disabled={accepting !== null}
                    title={deleteSugg && stateOf(deleteSugg.id) === 'pending'
                      ? 'accepts every change EXCEPT deleting the draft — that one stays a deliberate click'
                      : 'accept every pending change at once'}
                    style={{ fontSize: '0.66rem', padding: '3px 10px', border: '1px solid #b78a2f', borderRadius: 4, background: '#fff', color: '#8a6d3b', cursor: accepting !== null ? 'default' : 'pointer', whiteSpace: 'nowrap', fontFamily: 'inherit' }}>
                    Accept all ({acceptAllCount})
                  </button>
                )}
                {/* The measurement half of the loop: this invoice defeated
                    Norm, so file it for training rather than fixing it by
                    hand and leaving no trace. */}
                <button type="button" onClick={cannotReceive} disabled={cannotState === 'sending' || cannotState === 'filed'}
                  title="Norm can't get this invoice right — file it for training. Nothing is received."
                  style={{ fontSize: '0.66rem', padding: '3px 10px', border: '1px solid #d8d4cc', borderRadius: 4, background: '#fff', color: cannotState === 'filed' ? '#2e7d4f' : '#777', cursor: cannotState ? 'default' : 'pointer', whiteSpace: 'nowrap', fontFamily: 'inherit' }}>
                  {cannotState === 'sending' ? 'Filing…' : cannotState === 'filed' ? '✓ Filed for training' : "Can't receive"}
                </button>
              </div>
            )}
          </div>
          {[...suggestions].sort((a, b) => Number(stateOf(a.id) !== 'pending') - Number(stateOf(b.id) !== 'pending')).map(suggRow)}
          {effectiveActions.length > 0 && (
            <details style={{ marginTop: 4 }}>
              <summary style={{ fontSize: '0.62rem', color: '#9ca3af', cursor: 'pointer', userSelect: 'none' }}>
                {[
                  recordCounts.norm ? `Norm applied ${recordCounts.norm} change${recordCounts.norm > 1 ? 's' : ''}` : null,
                  recordCounts.user ? `you applied ${recordCounts.user}` : null,
                  recordCounts.dismissed ? `dismissed ${recordCounts.dismissed}` : null,
                ].filter(Boolean).join(' · ') || 'action record'}
              </summary>
              <div style={{ marginTop: 3 }}>
                {actionsList.map((a, i) => (
                  <div key={`ar-${i}`} style={{ fontSize: '0.6rem', color: '#9ca3af', display: 'flex', gap: 6, padding: '1px 0' }}>
                    <span style={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>{a.at ? a.at.replace('T', ' ').slice(0, 16) : '—'}</span>
                    <span style={{ whiteSpace: 'nowrap', fontWeight: 600 }}>{a.by === 'norm' ? 'Norm' : 'you'} {a.action}</span>
                    <span style={{ flex: 1 }}>{explanationFor(a.suggestion_id)}</span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {/* Issues — every finding is now either a suggestion or a blocker, so
          there is no "notes" bucket. Blocking issues gate the Receive button
          until their clears_when predicate holds against the working values, or
          the user marks them checked. */}
      {!dojo && viewMode === 'norm' && !draftDeleted && (issues.length > 0 || reviewing || !reviewed) && (
        <div style={{ padding: '0.55rem 0.9rem', borderTop: '1px solid #eee' }}>
          {blockingIssues.length > 0 && (
            <div>
              <div style={{ ...microLabel, color: '#c0392b', marginBottom: 3 }}>Blocked from auto receive</div>
              {blockingIssues.map(issueRow)}
            </div>
          )}
          {/* Non-blocking issues had nowhere to render at all — only
              `blockingIssues` was ever mapped — so making something a warning
              instead of a blocker made it disappear rather than soften. These
              are the things worth knowing that are nobody's fault: an order
              belonging to the supplier who sells through this one, a credit
              note that will reverse stock. */}
          {warningIssues.length > 0 && (
            <div style={{ marginTop: blockingIssues.length > 0 ? 8 : 0 }}>
              <div style={{ ...microLabel, color: '#8a6d3b', marginBottom: 3 }}>Worth knowing</div>
              {warningIssues.map(issueRow)}
            </div>
          )}
          {issues.length === 0 && (
            <div style={{ fontSize: '0.64rem', color: '#9ca3af' }}>
              {reviewing ? 'Reviewing the invoice against the attached copy…' : 'Not yet reviewed.'}
            </div>
          )}
        </div>
      )}

      {/* The one recovery control: re-analyse from scratch (confirmed —
          discards local edits and the accept record). */}
      {!dojo && !collapsed && !embedded && !doneState && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0.4rem 0.9rem', borderTop: '1px solid #eee' }}>
          <span style={{ fontSize: '0.62rem', color: '#9ca3af' }}>
            {reviewing ? 'reviewing…' : reviewed ? `reviewed ${String(docLive.reviewed_at).replace('T', ' ').slice(0, 16)}` : 'not yet reviewed'}
          </span>
          <button type="button" onClick={() => { void reanalyse(); }} disabled={reviewing}
            title="rebuild this invoice from Loaded and the copy and review it from scratch — resets your edits and accepted suggestions"
            style={{ fontSize: '0.6rem', padding: '1px 8px', border: '1px solid #d8d4cc', borderRadius: 4, background: '#fff', color: '#8a8a8a', cursor: reviewing ? 'default' : 'pointer', fontFamily: 'inherit' }}>
            {reviewing ? 'running…' : 'Re-analyse'}
          </button>
        </div>
      )}

      {/* Footer */}
      {!dojo && (
      <div style={{ padding: '0.6rem 0.9rem', borderTop: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.75rem' }}>
        {/* Only what has nowhere else to appear. Every "you can't receive
            because..." line this used to carry is now said once, in its own
            words, on the row that owns it: Suggested changes, Blocked from
            auto receive, Worth knowing. Repeating the count down here made
            the card say the same thing three times and taught people to skim
            past the part that actually names the fix.

            What stays is the transient and the terminal — a failed receive,
            a receive in flight, a receive that happened. None of those has a
            section of its own. */}
        <span style={{ flex: 1, fontSize: '0.72rem', color: status === 'error' ? '#c0392b' : doneState ? '#2e7d4f' : '#888' }}>
          {status === 'error' ? `✗ ${message}`
            : draftDeleted ? `✓ Draft deleted from Loaded${doc.deleted_reason ? ` — ${doc.deleted_reason}` : ' — this document was a supplier statement or duplicate.'}`
            : doneState ? '✓ Received in Loaded.'
            : status === 'saving' ? 'Receiving…'
            : ''}
        </span>
        {overlay && (
          <button type="button" onClick={() => setExpandedFull(false)}
            style={{ padding: '0.4rem 1rem', fontSize: '0.78rem', border: '1px solid #d8d4cc', borderRadius: 6, background: '#fff', color: '#666', cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
            Close
          </button>
        )}
        {!doneState && (
          <button onClick={receive} disabled={receiveBlocked}
            title={
              noLines
                ? 'This draft has no line items — nothing to receive'
                : supplierBlocking
                ? 'Pick the supplier first — Loaded can’t receive a supplier-less invoice'
                // It used to say "items/units" whatever was actually wrong —
                // including a brand, which is neither.
                : blockedCount > 0
                  ? `${blockedCount} thing${blockedCount > 1 ? 's' : ''} to sort out above — each row says what it needs`
                  : undefined
            }
            style={{ padding: '0.4rem 1.1rem', fontSize: '0.78rem', fontWeight: 500, border: 'none', borderRadius: 6, cursor: receiveBlocked ? 'not-allowed' : 'pointer', background: '#2e7d4f', color: '#fff', fontFamily: 'inherit', opacity: receiveBlocked ? 0.5 : 1, whiteSpace: 'nowrap' }}>
            {status === 'saving' ? 'Receiving…' : isCredit ? 'Accept & Receive credit' : 'Accept & Receive'}
          </button>
        )}
      </div>
      )}
    </fieldset>
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


