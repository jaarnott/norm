'use client';

/**
 * Working-document-backed "Receive Invoice" editor — replica_v2.
 *
 * The invoice analogue of PurchaseOrderEditor: it reads and PATCHes a Norm
 * working document (doc_type "received_invoice") so every edit persists — a
 * reload or the agent refining the draft both survive. Web only — the
 * Invoices page, the activity page's record view and web chat: doc I/O over
 * the session-auth working-documents REST; reference data from
 * /invoice-fixes/*. (Its copy inside Claude was removed on 20 Oct 2026 —
 * unused for 90 days.)
 *
 * The SERVER computes everything (app/services/invoice_review.py): the doc's
 * top-level working values are Loaded's draft + accepted suggestions + manual
 * edits; `suggestions` / `issues` / `confidence` / the `replica` sidecar are
 * the review's verdicts. This component is a pure renderer + value-applier —
 * it evaluates the tiny clears_when predicate and recomputes a line total on
 * accept (plain arithmetic), and derives NOTHING else.
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUp, Check, ChevronDown, ChevronRight, CircleDot, ExternalLink,
  FileText, Hourglass, Plus, RefreshCw, TriangleAlert, X,
} from 'lucide-react';
import { apiFetch, getStoredUser } from '../../lib/api';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import InvoicePdfPane from '../settings/InvoicePdfPane';
import { requestSidePanel } from '../../lib/sidePanel';
import PageState from '../ui/PageState';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { autoFocusField, PickerDialog, PickerList, type PickerOption } from './CatalogPicker';
import {
  buildReviewList, needWords, sameLine, type HandEdit, type ReviewRow,
} from './invoiceReview';

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
  // The person picked (or cleared) the brand here — it replaces Loaded's line
  // brand, name and record, on receive (received_invoice._apply_brand_choice).
  brand_set?: boolean | null;
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

// The server's review verdicts (invoice_review.py, replica_v2) — rendered,
// never derived. A suggestion is a place the Replica differs from Loaded.
interface Suggestion {
  id: string;
  kind: 'line_value' | 'add_line' | 'strike' | 'header_value' | 'supplier'
    | 'link_po' | 'unlink_po' | 'split_reference' | 'delete_invoice' | 'confirm'
    | 'create_unit' | 'create_item' | 'create_brand' | 'create_supplier' | string;
  field?: string | null;
  line_id?: string | null;
  current?: unknown;
  proposed?: unknown;
  explanation: string;
  // Field patch to accept: line_id present → the line, else the header.
  apply?: Record<string, unknown>;
  // add_line: the full line to append. delete_invoice: the /accept fix body.
  // create_*: what to create.
  payload?: Record<string, unknown>;
  // False: the Replica's guess — Norm never applies it unattended unless
  // `gate` (a venue switch) is on.
  sure?: boolean;
  gate?: string | null;
  // The blocker a rule set this value to clear.
  fixes?: string | null;
}
// The review payload this card renders (invoice_review.DOC_SCHEMA). A draft
// reviewed under an older shape re-reviews once when opened.
const REVIEW_SCHEMA = 'replica_v2';

// What Accept does, by suggestion kind — the button's tooltip.
const ACCEPT_LABELS: Record<string, string> = {
  create_item: 'Create the stock item',
  create_unit: 'Create the unit',
  create_brand: 'Create the brand',
  create_supplier: 'Create the supplier',
  confirm: 'Keep it as it is',
  strike: 'Strike this line',
  delete_invoice: 'Delete this draft',
  add_line: 'Add this line',
  link_po: 'Link this order',
  supplier: 'Link this supplier',
};
// The venue switch that lets Norm do it without being asked.
const gateLabels: Record<string, string> = {
  auto_create_units: 'auto-create units',
  auto_create_items: 'auto-create stock items',
  auto_create_brands: 'auto-create brands',
  auto_create_suppliers: 'auto-create suppliers',
  receive_with_unconfirmed_unit: 'receive when the invoice\'s unit can\'t be read and Norm\'s answer is used',
  receive_without_po: 'receive without a valid purchase order',
  receive_unit_kind_mismatch: 'receive in a unit of a different kind from the stocktake unit',
  auto_strike_phantom_lines: "strike lines the invoice doesn't bill",
  auto_delete_duplicates: 'delete a duplicate invoice draft',
  auto_delete_non_invoices: "delete drafts that aren't invoices",
  auto_delete_unreadable: 'delete drafts with no readable invoice',
};

interface Issue {
  id: string;
  code: string;
  blocking: boolean;
  line_id?: string | null;
  message: string;
  data?: Record<string, unknown> | null;
  // The suggestion(s) that clear this blocker — it stands down once all are
  // accepted. A blocker is never a decision of its own.
  fix?: string[] | null;
  // The venue switch that would let Norm get past it alone.
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
  // ---- The replica_v2 review contract ----
  doc_schema?: string;
  reviewed_at?: string | null;
  confidence?: 'ready' | 'needs_review';
  // The supplier has no reading-instructions yet, so Norm has queued a
  // background dojo study of this invoice; a spec appears once it finishes.
  sensei_studying?: boolean;
  suggestions?: Suggestion[];
  issues?: Issue[];
  suggestion_actions?: SuggestionAction[];
  // A person's own changes, kept by the server so a rebuild from Loaded
  // replays them (services/invoice_edits). Read back in record mode.
  edits?: HandEdit[];
  // Readiness, worked out on the server (invoice_decisions.annotate) and
  // sent with the document: each stop's state, each suggestion's, and
  // whether Autopilot would receive it / this person can.
  issue_states?: Record<string, { state: 'open' | 'settled'; how?: string | null }>;
  suggestion_states?: Record<string, { state: 'pending' | 'accepted' | 'dismissed'; open: boolean }>;
  receive?: { ready: boolean; person_can_receive: boolean; blocked_by: string[] };
  // A person's unit of a different kind from the item's: shown on the row.
  warnings?: { line_id?: string | null; code: string; text: string }[];
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
      brand?: string | null;
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
interface Brand { id: string; name: string }
// The new-unit form's type → the base unit its size is counted in.
const UNIT_TYPE_SIZE: Record<string, string> = { Weight: 'kg', Volume: 'litres', Count: 'items' };
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
  brands: Brand[];
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
    get<Brand[]>('brands', 'brands', []),
  ]).then(([units, purchase_orders, suppliers, stock_items, groups, brands]) => ({
    units, purchase_orders, suppliers, stock_items, groups, brands,
  }));
  _referenceCache.set(venueId, p);
  return p;
}

// ---- Looks (tokens.css classes do most of the work: .n-card, .n-table,
// .n-input, .n-select, .n-label, .n-option). Bundled into Claude too, so
// nothing here may rely on the web app's globals.
const fieldCol: React.CSSProperties = { display: 'flex', flexDirection: 'column', minWidth: 0 };
// One section of the card: a hairline above, the card's gutters.
const section: React.CSSProperties = { padding: '12px 16px', borderTop: '1px solid var(--line)' };
const sectionTitle: React.CSSProperties = { fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' };
// Field states. backgroundColor, never `background`: the .n-select chevron is
// a background-image. Suggested = Norm proposes a change here (accent, "needs
// your input"); missing = a value Loaded can't receive as it stands (warn).
const fieldSuggested: React.CSSProperties = { borderColor: 'var(--brand-soft)', backgroundColor: 'var(--accent-soft)' };
const fieldMissing: React.CSSProperties = { borderColor: 'var(--warn)', backgroundColor: 'var(--warn-bg)' };
// Numbers in inputs line up like the columns they sit in.
const numInput: React.CSSProperties = { textAlign: 'right', fontVariantNumeric: 'tabular-nums', padding: '0 8px' };
// The stock item / brand / unit boxes: the dropdown look (n-select) on a
// button that opens the picker, one line, ellipsis for long names.
const pickerBox: React.CSSProperties = {
  display: 'block', width: '100%', textAlign: 'left', overflow: 'hidden',
  textOverflow: 'ellipsis', whiteSpace: 'nowrap',
};
// A section heading that folds its table away: the heading's look, as a button.
const foldButton: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 4, padding: 0, border: 'none', background: 'none',
  font: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)', cursor: 'pointer',
};
// X-ray modes show a field's VALUE as read-only text in the field's place.
const readonlyField: React.CSSProperties = {
  display: 'flex', alignItems: 'center', minHeight: 34, padding: '4px 10px', boxSizing: 'border-box',
  border: '1px solid var(--line)', borderRadius: 'var(--radius)', background: 'var(--surface)',
  color: 'var(--text-soft)', fontSize: 'var(--fs-base)',
};
// Inline suggestion chip: the proposed value + accept (and dismiss).
// Small on purpose: a chip sits under a field or inside a table cell, so it
// must not stretch the row it is in (12 Oct 2026). Its tick and cross are the
// compact icon buttons (tokens.css .n-icon-btn--xs).
const chipStyle: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 0, maxWidth: '100%', marginTop: 3,
  padding: '0 1px 0 6px', borderRadius: 'var(--radius-sm)', background: 'var(--accent-soft)',
  color: 'var(--accent)', fontSize: 'var(--fs-xs)', fontWeight: 500, lineHeight: 1.25,
  textDecoration: 'none',
};
const chipLabel: React.CSSProperties = { padding: '2px 4px 2px 0', overflowWrap: 'break-word' };
// A sentence-sized message with its icon (errors, warnings, notes).
const iconLine: React.CSSProperties = { display: 'flex', alignItems: 'flex-start', gap: 6 };
const iconNudge: React.CSSProperties = { marginTop: 2 };
const totalRow: React.CSSProperties = { display: 'flex', justifyContent: 'space-between', gap: 24, padding: '2px 0' };
// Inline text actions inside a sentence: the link button, at the sentence's size.
const inlineLink: React.CSSProperties = {
  display: 'inline', fontSize: 'inherit', lineHeight: 'inherit', whiteSpace: 'normal', textAlign: 'left',
};
// The header money fields as the total chips name them (not the raw keys).
const moneyFieldLabels: Record<string, string> = {
  subtotal: 'Subtotal', tax_amount: 'Tax', discount_amount: 'Discount', total: 'Total',
};
// ISO string → the YYYY-MM-DD a <input type="date"> wants (and back on change).
const dateVal = (s: string | null | undefined) => (s ? String(s).slice(0, 10) : '');

// Loaded's arithmetic (its calculateFooterTotals): lines, tax at each line's
// rate, less the discount. The totals the card shows and the totals blocker
// both use it, as the server does (replica_finish._totals_view).
function docTotals(doc: DocData): { excl: number; tax: number; discount: number; incl: number } {
  const includesTax = !!doc.unit_cost_includes_tax;
  let excl = 0;
  let tax = 0;
  for (const l of doc.lines || []) {
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
}

export default function ReceiveInvoiceEditor({ data, props, threadId }: DisplayBlockProps) {
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
  // The invoice copy beside the card (web only). Opening it folds the thread
  // panel away for the width, and closing — or leaving the card — hands it back.
  const [copyOpen, setCopyOpen] = useState(false);
  // `record`: show what is kept, as it is — never start a review on opening
  // (the invoice activity page reads back what Norm did).
  const record = !!props?.record;
  useEffect(() => {
    if (!copyOpen) return;
    requestSidePanel(true);
    return () => requestSidePanel(false);
  }, [copyOpen]);
  // Side by side needs room for both panes; narrower, the copy stacks above
  // the card (and must not stick, or it would cover the lines beneath).
  const copyRowRef = useRef<HTMLDivElement>(null);
  const [copySideBySide, setCopySideBySide] = useState(true);
  useEffect(() => {
    const el = copyRowRef.current;
    if (!copyOpen || !el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([e]) => setCopySideBySide(e.contentRect.width >= 2 * 420 + 12));
    ro.observe(el);
    return () => ro.disconnect();
  }, [copyOpen]);
  const workingDocId = (data as Record<string, unknown>)?.working_document_id as string | undefined;
  const venueId = (props?.activeVenueId as string) || (data as Record<string, unknown>)?.venue_id as string | undefined;

  // Reference data (the pickers' lists) is fetched once the venue is known.
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
          brand: el.brand ?? null,
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
  const [brands, setBrands] = useState<Brand[]>([]);
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
  // The line whose link-or-create stock item form is open. Its unit and brand
  // are the LINE's own (the form's pickers set them on the line), so the form
  // holds only what is the new item's alone.
  const [itemForm, setItemForm] = useState<{ lineId: string; name: string; groupId: string } | null>(null);
  const [creatingItem, setCreatingItem] = useState(false);
  // Line whose unit is being CREATED in Loaded (create-unit).
  const [creatingUnitLine, setCreatingUnitLine] = useState<string | null>(null);
  // Line whose BRAND is being created in Loaded (create-brand).
  const [creatingBrandLine, setCreatingBrandLine] = useState<string | null>(null);
  // The new-unit form: the name, and the type and size the person confirms —
  // prefilled from Loaded's existing unit for the name, else Norm's reading.
  // The form IS the confirmation; nothing is created until its button.
  const [unitForm, setUnitForm] = useState<{
    lineId: string; name: string; type: string; size: string;
    existing: Unit | null; checking: boolean;
  } | null>(null);
  // The new brand's name, in the picker's create step.
  const [brandName, setBrandName] = useState('');
  // The line's stock item / unit / brand picker (CatalogPicker): which field
  // of which line, picking or creating, its search text, whether a create
  // returns to the stock-item form, and (inside Claude) where to pin it.
  const [picker, setPicker] = useState<{
    kind: 'item' | 'unit' | 'brand'; lineId: string; mode: 'pick' | 'create';
    query: string; back?: boolean;
  } | null>(null);
  const [linkQuery, setLinkQuery] = useState('');
  // The order sections under the lines ("ordered, not delivered", "received
  // on the sibling delivery") start folded — their count is in the heading.
  const [openOrderSection, setOpenOrderSection] = useState<Set<string>>(new Set());
  const toggleOrderSection = (key: string) => setOpenOrderSection((prev) => {
    const n = new Set(prev);
    if (n.has(key)) n.delete(key); else n.add(key);
    return n;
  });
  // Substitute lines whose original ordered row is expanded.
  const [openSub, setOpenSub] = useState<Set<string>>(new Set());
  // Per-mount uid so line anchors never collide across sibling cards.
  const uid = useRef(Math.random().toString(36).slice(2, 8)).current;
  const isPlatformAdmin = getStoredUser()?.role === 'admin';

  const docUrl = workingDocId
    ? (threadId ? `/api/threads/${threadId}/working-documents/${workingDocId}` : `/api/working-documents/${workingDocId}`)
    : null;

  // Bumped when a SIBLING card receives/deletes an invoice — refetches this
  // card's doc (the server may have cleared its cached review, which re-arms
  // the review effect below) and the reference data (PO list went stale).
  const [refreshTick, setRefreshTick] = useState(0);
  useEffect(() => {
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
  }, [venueId, workingDocId]);

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

  // Reference reads for the pickers, once the invoice is known.
  const refsInvoiceId = doc.invoice_id ? String(doc.invoice_id) : '';
  const refsWanted = !!venueId && !!refsInvoiceId;
  useEffect(() => {
    if (!refsWanted || !venueId) return;
    fetchReferenceData(venueId).then((refs) => {
      setUnits(refs.units);
      setPos(refs.purchase_orders);
      setSuppliers(refs.suppliers);
      setStockItems(refs.stock_items);
      setStockGroups(refs.groups);
      setBrands(refs.brands);
    });
  }, [venueId, refsInvoiceId, refsWanted, refreshTick]);

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

  // A spec-less supplier's invoice is reviewed under the main prompt while the
  // background dojo study runs (~1-2 min); when it finishes it auto-applies the
  // spec and re-reviews this invoice's stored draft (spec_dojo._heal_source_review
  // → heal_review). This open card has no other trigger to see that, so while the
  // "studying" flag is set we poll our own doc and adopt the healed version the
  // moment it lands — the flag clears with it, ending the poll. Capped so a
  // stuck/failed study never polls forever (the note + Re-analyse still work).
  const studyPollsRef = useRef(0);
  useEffect(() => {
    if (!docLive.sensei_studying) { studyPollsRef.current = 0; return; }
    const id = setInterval(() => {
      studyPollsRef.current += 1;
      if (studyPollsRef.current > 20) { clearInterval(id); return; } // ~4 min cap
      refetchOwnDoc();
    }, 12000);
    return () => clearInterval(id);
    // refetchOwnDoc reads only stable values (docUrl + setters); re-running on
    // its identity would needlessly reset the interval, so it is left out.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docLive.sensei_studying]);

  // The server review (POST /invoice-fixes/review) — builds/rebuilds the
  // REVIEW_SCHEMA payload. force=true recomputes it; either way the card is
  // rebuilt from Loaded and what was decided on it — accepted suggestions,
  // then a person's own edits — is replayed (services/invoice_edits). Only
  // Re-analyse (reset validation) starts the card from scratch.
  const runReview = async (explicit = false, force = false) => {
    if (!venueId || !doc.invoice_id || doc.is_deleted) return;
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
  const reviewed = docLive.doc_schema === REVIEW_SCHEMA && !!docLive.reviewed_at;
  // While the background dojo study runs, the on-screen review is the main-prompt
  // ("dud") read — a spec will replace it shortly. Don't dress it up as a finished
  // review: suppress the confidence chip, the suggestions block, the reviewed
  // timestamp and the resolved units, and say "studying" instead.
  const studying = !!docLive.sensei_studying;
  useEffect(() => {
    if (record || !venueId || !doc.invoice_id) return;
    if (doc.is_deleted) return; // tombstone — nothing left to review
    // (Re)build whenever the doc is not a reviewed REVIEW_SCHEMA payload — an
    // older shape (re-reviewed once, its decisions carried over), or one
    // whose cached review the server invalidated.
    if (reviewed) return;
    runReview();
    // Fires when the invoice_id is known AND whenever the review state is
    // cleared (server invalidation) — the guard above stops a re-run once
    // the review returns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [record, venueId, doc.invoice_id, reviewed]);


  // Serialized patches: one at a time, each using the LATEST version. Every
  // change the card makes is a decision the SERVER makes
  // (services/invoice_decisions, through the `decide` op): the card shows
  // the change at once, sends it, and takes the server's answer — the
  // document comes back with its readiness worked out — as the truth. A
  // refused decision says why. Receive waits (with a spinner) while any
  // save is still out.
  const patchQueue = useRef<Promise<unknown>>(Promise.resolve());
  const [pendingSaves, setPendingSaves] = useState(0);
  type Decided = {
    ok: boolean; detail?: string | null; created?: boolean; name?: string | null; id?: string | null;
    ratio?: number | null;
  };
  const patchDoc = useCallback(
    (ops: Record<string, unknown>[]): Promise<Decided[]> => {
      if (!docUrl) return Promise.resolve([]);
      const withIds = ops.map((op) => {
        if (op.op === 'update_line' && op.line_id == null && typeof op.index === 'number') {
          const id = docRef.current.lines[op.index as number]?.id;
          return id ? { ...op, line_id: id } : op;
        }
        return op;
      });
      setPendingSaves((n) => n + 1);
      // The server's copy replaces what the card showed, with a sentence why:
      // a change shown at once is corrected when it didn't save.
      const resync = async (why: string): Promise<Decided[]> => {
        const fresh = await apiFetch(docUrl).then((r) => (r.ok ? r.json() : null)).catch(() => null);
        if (fresh?.data) {
          setDoc(fresh.data as DocData);
          if (typeof fresh.version === 'number') setVersion(fresh.version);
        }
        setStatus('error');
        setMessage(why);
        return [];
      };
      const run = async (): Promise<Decided[]> => {
        try {
          const res = await apiFetch(docUrl, {
            method: 'PATCH',
            body: JSON.stringify({ ops: withIds, version: versionRef.current }),
          });
          if (res.status === 409) {
            return await resync('This invoice was updated elsewhere — the view has been refreshed; please re-apply your last change.');
          }
          if (res.ok) {
            const updated = await res.json();
            if (updated?.data) setDoc((prev) => ({ ...prev, ...(updated.data as DocData) }));
            if (typeof updated?.version === 'number') setVersion(updated.version);
            const decided = (updated?.decisions ?? []) as Decided[];
            const refused = decided.find((d) => d && d.ok === false && d.detail);
            if (refused) {
              setStatus('error');
              setMessage(String(refused.detail));
            }
            return decided;
          }
          return await resync('That change could not be saved — the invoice has been refreshed; please try it again.');
        } catch {
          return await resync('That change could not be saved (no connection) — the invoice has been refreshed; please try it again.');
        } finally {
          setPendingSaves((n) => Math.max(0, n - 1));
        }
      };
      const p = patchQueue.current.then(run);
      patchQueue.current = p;
      return p;
    },
    [docUrl],
  );
  // One decision, or several that the server applies in its order of work.
  const decideOps = (decisions: Record<string, unknown>[]): Promise<Decided[]> =>
    workingDocId ? patchDoc(decisions.map((decision) => ({ op: 'decide', decision }))) : Promise.resolve([]);

  const setLine = (idx: number, patch: Partial<Line>) => {
    setDoc((prev) => {
      const lines = prev.lines.map((l, i) => (i === idx ? { ...l, ...patch } : l));
      return { ...prev, lines };
    });
  };
  // A person's own value on a line: shown at once, decided on the server —
  // recorded as their edit, answering Norm's pending suggestion for that
  // field, and settling the line's stop when it is an answer Loaded can take.
  const editLine = (idx: number, fields: Partial<Line>): Promise<Decided[]> => {
    const l = docRef.current.lines[idx];
    if (!l) return Promise.resolve([]);
    setLine(idx, fields);
    return decideOps([{ edit: { line_id: l.id, fields } }]);
  };
  const onUnit = (idx: number, unitId: string) => {
    const u = units.find((x) => x.id === unitId);
    void editLine(idx, { linked_unit_id: unitId, unit: u?.name ?? null, unit_ratio: u?.ratio ?? null });
  };
  // The total follows qty × cost on screen; the server works it out too.
  const onQty = (idx: number, qty: number) => {
    const l = docRef.current.lines[idx];
    if (!l) return;
    setLine(idx, { quantity_received: qty, total_cost: round4(qty * (l.unit_cost ?? 0)) });
    void decideOps([{ edit: { line_id: l.id, fields: { quantity_received: qty } } }]);
  };
  const onCost = (idx: number, cost: number) => {
    const l = docRef.current.lines[idx];
    if (!l) return;
    setLine(idx, { unit_cost: cost, total_cost: round4((l.quantity_received ?? 0) * cost) });
    void decideOps([{ edit: { line_id: l.id, fields: { unit_cost: cost } } }]);
  };
  // Strike / un-strike a line. Striking normally happens by accepting the
  // server's strike suggestion; this is the plain toggle (restore, and the
  // manual escape hatch).
  const onStrike = (idx: number, struck: boolean) => {
    void editLine(idx, { struck });
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
    if (!venueId || !workingDocId || !docRef.current.invoice_id) return;
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
    const fields = {
      linked_purchase_order_id: poId || null,
      purchase_order_number: po?.order_number ?? docRef.current.purchase_order_number,
    };
    setDoc((prev) => ({ ...prev, ...fields }));
    if (!workingDocId) return;
    await decideOps([{ edit: { fields } }]);
    if (poId) await refreshOrderReference();
  };

  // Header field edits (invoice #, dates, total, supplier, tax toggle) —
  // shown at once, decided on the server as the person's edit.
  const patchHeader = (fields: Partial<DocData>) => {
    setDoc((prev) => ({ ...prev, ...fields }));
    void decideOps([{ edit: { fields } }]);
  };
  const onSupplier = (id: string) => {
    const s = suppliers.find((x) => x.id === id);
    patchHeader({ linked_supplier_id: id || null, supplier_name: s?.name ?? null });
  };

  // ---- Decisions on Norm's suggestions -----------------------------------
  // THE RECORD (doc.suggestion_actions, shared with Autopilot) and the states
  // the server worked out from it (suggestion_states, issue_states, receive)
  // are read here, never derived. Accept, Decline and Undo send the decision;
  // the card shows the obvious change at once and takes the server's
  // document when it comes back.
  const actionsList = useMemo(() => docLive.suggestion_actions || [], [docLive.suggestion_actions]);
  // Last action per suggestion_id (also covers autopilot's entries): who did
  // it and when, for the record and the Undo button.
  const lastAction = useMemo(() => {
    const m = new Map<string, SuggestionAction>();
    for (const a of actionsList) if (a?.suggestion_id) m.set(a.suggestion_id, a);
    return m;
  }, [actionsList]);
  const stateOf = (id: string): 'pending' | 'accepted' | 'dismissed' => {
    const st = docLive.suggestion_states?.[id]?.state;
    if (st === 'accepted' || st === 'dismissed' || st === 'pending') return st;
    const a = lastAction.get(id);
    if (!a || a.action === 'undone') return 'pending';
    return a.action;
  };
  // Still to decide: pending, or declined while its field is still New (the
  // server keeps that row open until the field is settled).
  const rowOpen = (id: string): boolean =>
    docLive.suggestion_states?.[id]?.open ?? (stateOf(id) === 'pending');
  // A decision's outcome, shown before the server answers.
  const noteState = (id: string, state: 'accepted' | 'dismissed' | 'pending') =>
    setDoc((prev) => ({
      ...prev,
      suggestion_states: { ...(prev.suggestion_states || {}), [id]: { state, open: state === 'pending' } },
    }));
  // What a suggestion changes on the draft, shown at once (the server
  // applies the same values); a create shows nothing until Loaded answers.
  const showApplied = (s: Suggestion) => {
    if (s.kind === 'add_line') {
      const payload = s.payload as unknown as Line | undefined;
      if (!payload || !Object.keys(payload).length) return;
      setDoc((prev) => (prev.lines.some((l) => String(l.id) === String(payload.id))
        ? prev
        : { ...prev, lines: [...prev.lines, payload] }));
      return;
    }
    const apply = s.apply;
    if (!apply || !Object.keys(apply).length) return;
    if (s.line_id) {
      setDoc((prev) => ({
        ...prev,
        lines: prev.lines.map((l) => {
          if (String(l.id) !== String(s.line_id)) return l;
          const next = { ...l, ...(apply as Partial<Line>) };
          // The server recomputes the total from quantity and price too.
          if (('quantity_received' in apply || 'unit_cost' in apply) && !('total_cost' in apply)
            && next.quantity_received != null && next.unit_cost != null) {
            next.total_cost = round4(Number(next.quantity_received) * Number(next.unit_cost));
          }
          return next;
        }),
      }));
    } else {
      setDoc((prev) => ({ ...prev, ...(apply as Partial<DocData>) }));
    }
  };
  const acceptSuggestion = (s: Suggestion) => {
    if (doneState) return;
    if (s.kind === 'delete_invoice') {
      // A Loaded write (verified DELETE endpoint) with a route of its own:
      // it tombstones every copy of the draft and records the outcome.
      void acceptFix(s);
      return;
    }
    void acceptRows([{ key: s.id, fixes: [s], lineId: s.line_id ? String(s.line_id) : null, open: true }]);
  };
  // Decline: record-only ("declined without applying").
  const dismissAction = (id: string) => {
    if (doneState) return;
    noteState(id, 'dismissed');
    void decideOps([{ decline: id }]);
  };
  // Undo: the server puts back what the decision replaced (and brings back
  // the person's edits it had retired); the card shows the recorded
  // `before` at once.
  const undoAction = (s: Suggestion | null, id: string) => {
    if (doneState) return;
    const a = lastAction.get(id);
    if (!a || a.action === 'undone') return;
    noteState(id, 'pending');
    if (a.action === 'accepted' && a.before) {
      const before = a.before;
      if (s?.kind === 'add_line') {
        const lineId = before.added_line_id;
        setDoc((prev) => ({ ...prev, lines: prev.lines.filter((l) => String(l.id) !== String(lineId)) }));
      } else if (s?.line_id) {
        setDoc((prev) => ({
          ...prev,
          lines: prev.lines.map((l) => (String(l.id) === String(s.line_id) ? { ...l, ...(before as Partial<Line>) } : l)),
        }));
      } else {
        setDoc((prev) => ({ ...prev, ...(before as Partial<DocData>) }));
      }
    }
    void decideOps([{ undo: id }]);
  };

  // ---- Derived review state (rendering only — the server decided) -------
  // Legacy stored docs carry old-shape suggestions ({type, summary}) — those
  // are not rendered; only replica entries ({id, kind}) are.
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
  // A stop's state is the server's answer (invoice_decisions.annotate): it
  // stood down by its predicate, by the person's own value, or by its fix.
  type IssueState = 'open' | 'resolved';
  const issueStateOf = (i: Issue): IssueState =>
    (docLive.issue_states?.[i.id]?.state === 'settled' ? 'resolved' : 'open');
  const blockingOpen = issues.filter((i) => i.blocking && issueStateOf(i) === 'open');
  const liveConfidence: 'ready' | 'needs_review' | null =
    reviewed ? (docLive.confidence === 'ready' ? 'ready' : 'needs_review') : null;
  const pendingSuggestions = suggestions.filter((s) => rowOpen(s.id));
  // What each field's chip shows: the suggestions still to decide, or in
  // `record` mode every suggestion, with what happened to it.
  const fieldSuggestions = record ? suggestions : pendingSuggestions;

  // ---- The review list: one row per decision (invoiceReview.ts) ----------
  const review = buildReviewList({
    issues,
    suggestions,
    lineOrder: doc.lines.map((l) => String(l.id)),
    issueOpen: (i) => issueStateOf(i as Issue) === 'open',
    suggestionPending: (sg) => rowOpen(sg.id),
    hideSuggestions: studying,
  });
  // While Norm says "delete this draft" (a statement, a duplicate, no copy),
  // everything else is moot — shown only on request.
  const [showBehindDelete, setShowBehindDelete] = useState(false);
  const visibleRows = review.deleteRow && !showBehindDelete ? [review.deleteRow] : review.rows;
  const openRows = visibleRows.filter((r) => r.open);
  const [acceptingRows, setAcceptingRows] = useState(false);

  // Accept for one row, and Accept all, through ONE path: every fix goes to
  // the server as a decision in one patch, and the server applies them in
  // its order of work — lines to add, then values, then what is made in
  // Loaded — each recording itself. A draft to delete is accepted alone.
  const acceptRows = async (rows: ReviewRow[]) => {
    if (doneState || acceptingRows) return;
    const open = rows.filter((r) => r.open);
    if (!open.length) return;
    const fixes = open.flatMap((r) => r.fixes).filter((sg) => rowOpen(sg.id)) as Suggestion[];
    const del = fixes.find((sg) => sg.kind === 'delete_invoice');
    if (del) {
      void acceptFix(del);
      return;
    }
    if (!fixes.length) return;
    setAcceptingRows(true);
    setMessage('');
    try {
      for (const sg of fixes) {
        showApplied(sg);
        noteState(sg.id, 'accepted');
      }
      await decideOps(fixes.map((sg) => ({ accept: sg.id })));
      // The review pre-caches the rows of the order it suggests, so the
      // projection recomputes on this very patch — no wait, no re-analysis.
      // Only fetch when the accepted order ISN'T the cached one.
      if (fixes.some((sg) => PO_LINK_KINDS.includes(sg.kind) && !ordersCached(sg))) await refreshOrderReference();
    } finally {
      setAcceptingRows(false);
    }
  };
  // Inline chip lookups — the same suggestion objects the summary list
  // renders, so accepting from either surface is the same action.
  const suggFor = (lineId: string, field: string) =>
    fieldSuggestions.find((s) => s.kind === 'line_value' && String(s.line_id) === String(lineId) && s.field === field);
  const strikeSuggFor = (lineId: string) =>
    fieldSuggestions.find((s) => s.kind === 'strike' && String(s.line_id) === String(lineId));
  // Like the unit column: a swap to an item Loaded already has (line_value),
  // or "it isn't in the catalogue — create it" (create_item, a Loaded write).
  const itemSuggFor = (lineId: string) =>
    fieldSuggestions.find((s) => (s.kind === 'line_value' || s.kind === 'create_item')
      && String(s.line_id) === String(lineId) && s.field === 'linked_item_id');
  // The unit column carries TWO kinds: a plain swap to a unit Loaded already
  // has (line_value), and "the copy's delivered unit doesn't exist in Loaded —
  // create it" (create_unit, which is a Loaded write). Both are line-level
  // proposals and belong on the line, not only in the summary list; ✓ routes
  // to the same handler either way.
  // The brand the invoice prints (owner, 19 Oct 2026): link the one Loaded has
  // or remove Loaded's when it prints none (line_value), or create it.
  const brandSuggFor = (lineId: string) =>
    fieldSuggestions.find((s) => (s.kind === 'create_brand'
      || (s.kind === 'line_value' && s.field === 'linked_brand_id'))
      && String(s.line_id) === String(lineId));
  const brandLabel = (s: Suggestion | undefined) => {
    if (!s) return undefined;
    if (s.kind === 'create_brand') return `Create '${fmtVal(s.proposed)}'`;
    if (s.proposed == null) return s.current ? `Remove '${fmtVal(s.current)}'` : 'No brand';
    return s.current ? `${fmtVal(s.current)} → ${fmtVal(s.proposed)}` : `Link to '${fmtVal(s.proposed)}'`;
  };
  const unitSuggFor = (lineId: string) =>
    fieldSuggestions.find((s) => (s.kind === 'line_value' || s.kind === 'create_unit')
      && String(s.line_id) === String(lineId) && s.field === 'unit');
  const headerValueSugg = (field: string) =>
    fieldSuggestions.find((s) => s.kind === 'header_value' && s.field === field);
  const supplierSugg = fieldSuggestions.find((s) => s.kind === 'supplier');
  const createSupplierSugg = fieldSuggestions.find((s) => s.kind === 'create_supplier');
  const poSuggs = fieldSuggestions.filter((s) => ['link_po', 'unlink_po', 'split_reference'].includes(s.kind));
  const moneySuggs = ['subtotal', 'tax_amount', 'discount_amount', 'total']
    .map((f) => headerValueSugg(f))
    .filter((s): s is Suggestion => !!s);

  const doneState = status === 'done' || !!doc.is_received || !!doc.is_deleted;
  const draftDeleted = deletedDraft || !!doc.is_deleted;
  // Whether this person can receive, and why not: the server's answer
  // (invoice_decisions.annotate) — what Loaded would refuse, and the stops no
  // switch waves through. A gated stop never greys out a person's Receive:
  // the person is the authority the switch defers to.
  const receiveState = docLive.receive;
  const receiveBlocked = status === 'saving' || pendingSaves > 0 || !receiveState?.person_can_receive;
  const receiveTitle = status !== 'saving' && pendingSaves > 0
    ? 'Saving your changes…'
    : receiveState && !receiveState.person_can_receive
      ? receiveState.blocked_by.join(' · ')
      : undefined;

  const includesTax = !!doc.unit_cost_includes_tax;
  const totals = useMemo(() => docTotals(doc), [doc]);

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
  const sortedBrands = useMemo(
    () => [...brands].sort((a, b) => (a.name || '').localeCompare(b.name || '')),
    [brands],
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
  // Stock items by WORDS, best first. The picker's search opens empty, and
  // until something is typed the list ranks against the invoice's own
  // description ("SAN PELLEGRINO 24X250ML") — as one string that matches no
  // catalogue name ("San Pellegrino Sparkling 250ml"), so it is split into
  // words or the likely items would never show.
  const matchItems = (text: string): StockItem[] => {
    const words = text.toLowerCase().split(/\s+/).filter((w) => w.length > 1);
    if (!words.length) return [];
    return stockItems
      .map((i) => {
        const hay = `${i.name || ''} ${i.code || ''}`.toLowerCase();
        return { i, hits: words.filter((w) => hay.includes(w)).length };
      })
      .filter((m) => m.hits > 0)
      .sort((a, b) => b.hits - a.hits)
      .slice(0, 30)
      .map((m) => m.i);
  };

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
    if (!venueId || !doc.invoice_id || reviewing) return;
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
    if (!venueId) return;
    const query = opts?.fileId
      ? `venue_id=${venueId}&file_id=${encodeURIComponent(opts.fileId)}`
      : `venue_id=${venueId}&invoice_id=${doc.invoice_id}`;
    // Open the tab SYNCHRONOUSLY inside the click — window.open AFTER the
    // fetch has lost the user-activation, so popup blockers silently eat it.
    // Navigate the pre-opened tab once the blob arrives.
    const w = window.open('about:blank', '_blank');
    try {
      const r = await apiFetch(`/api/invoice-fixes/file?${query}`);
      if (!r.ok) throw new Error(r.status === 404 ? 'No invoice attached' : `Error ${r.status}`);
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
      setMessage(e instanceof Error ? e.message : 'Could not open the invoice');
      setStatus('error');
    }
  };

  // Accept the delete_invoice suggestion: the ONE server-applied fix left —
  // POST /invoice-fixes/accept with the suggestion's payload.
  const acceptFix = async (s: Suggestion) => {
    if (!venueId) return;
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

  // ---- Loaded creates + linking --------------------------------------------
  // Link a line to a stock item: the person's own edit. Nothing is written to
  // Loaded until Accept & Receive — the receive request carries the item id
  // and do_receive registers the supplier variant then. The PO reconciliation
  // beside the lines is SERVER-owned derived state: the patch response
  // carries it back recomputed, and the merge at patchDoc paints it.
  const linkItem = (lineId: string, itemId: string) => {
    const idx = docRef.current.lines.findIndex((l) => l.id === lineId);
    if (idx < 0) return;
    const cat = stockItems.find((x) => x.id === itemId);
    void editLine(idx, { linked_item_id: itemId, item_name: cat?.name ?? null });
    setPicker(null);
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
  // Create (or find) a unit in Loaded and give it to the line, from the
  // new-unit form with the person's own type and size: their own edit (an
  // accepted create_unit suggestion goes through acceptRows). Resolve-first
  // server-side: a unit Loaded already has comes back instead of a duplicate.
  const createUnitAndApply = async (
    idx: number,
    name: string,
    spec: { ratio: number; unit_type: string } | null = null,
  ): Promise<boolean> => {
    const l = docRef.current.lines[idx];
    if (!l || !name || !venueId || creatingUnitLine) return false;
    setCreatingUnitLine(l.id);
    setMessage('');
    try {
      const [out] = await decideOps([{ create: { kind: 'unit', line_id: l.id, name, ...(spec ?? {}) } }]);
      if (!out?.ok) return false;
      if (out.created === false && spec) setMessage(`Loaded already has ‘${out.name ?? name}’ — using that.`);
      // Into the dropdown too (the server put it on the line).
      if (out.id) {
        const rec: Unit = { id: out.id, name: out.name ?? name, ...(out.ratio != null ? { ratio: out.ratio } : {}) };
        setUnits((prev) => (prev.some((u) => u.id === rec.id) ? prev : [...prev, rec]));
      }
      return true;
    } finally {
      setCreatingUnitLine(null);
    }
  };

  // Open the new-unit form for a line, prefilled with the unit the copy
  // names (else Loaded's unlinked string), and ask what Loaded has for it.
  const checkUnitName = async (lineId: string, name: string) => {
    const n = name.trim();
    if (!n || !venueId) return;
    setUnitForm((f) => (f && f.lineId === lineId ? { ...f, checking: true } : f));
    const out = await apiFetch(
      `/api/invoice-fixes/unit-spec?venue_id=${venueId}&invoice_id=${encodeURIComponent(String(doc.invoice_id || ''))}&name=${encodeURIComponent(n)}`,
    ).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    setUnitForm((f) => {
      // The person may have typed on (or closed the form) while this ran.
      if (!f || f.lineId !== lineId || f.name.trim() !== n) return f;
      const ex = out?.existing as Unit | null | undefined;
      const spec = out?.spec as { ratio?: number; stock_unit_type?: string } | null | undefined;
      return {
        ...f,
        checking: false,
        existing: ex?.id ? ex : null,
        type: spec?.stock_unit_type || f.type,
        size: spec?.ratio != null ? String(spec.ratio) : f.size,
      };
    });
  };
  const openUnitForm = (l: Line, typed = '') => {
    const create = unitSuggFor(l.id);
    const proposed = create?.kind === 'create_unit' && typeof create.proposed === 'string' ? create.proposed : '';
    const name = typed || replicaUnitName(String(l.id)) || proposed || l.unit || '';
    setUnitForm({ lineId: l.id, name, type: '', size: '', existing: null, checking: false });
    if (name) void checkUnitName(l.id, name);
  };
  const submitUnitForm = async () => {
    if (!unitForm) return;
    const idx = docRef.current.lines.findIndex((x) => x.id === unitForm.lineId);
    if (idx < 0) return;
    if (unitForm.existing) {
      // Loaded has it already: the line simply takes it.
      onUnit(idx, unitForm.existing.id);
      setUnits((prev) => (prev.some((u) => u.id === unitForm.existing!.id) ? prev : [...prev, unitForm.existing!]));
      setUnitForm(null);
      afterCreate();
      return;
    }
    const ratio = parseFloat(unitForm.size);
    const ok = await createUnitAndApply(idx, unitForm.name.trim(), { ratio, unit_type: unitForm.type });
    if (ok) {
      setUnitForm(null);
      afterCreate();
    }
  };

  // Create a NEW stock item (+ its supplier variant) in Loaded from the inline
  // form and link the line — the person's own edit (an accepted create_item
  // suggestion goes through acceptRows like any decision). The item takes the
  // LINE's unit, brand and the card's supplier — Loaded needs the unit to
  // create it. A same-named item Loaded already has is linked instead.
  const createItemAndApply = async (lineId: string, name: string, groupId: string): Promise<boolean> => {
    if (!venueId || creatingItem) return false;
    const line = docRef.current.lines.find((l) => l.id === lineId);
    if (!line?.linked_unit_id) {
      setStatus('error');
      setMessage('Choose the unit it comes in before creating the stock item.');
      return false;
    }
    if (!name || !groupId) {
      setStatus('error');
      setMessage('A name and a stock group are required to create the item.');
      return false;
    }
    setCreatingItem(true);
    setMessage('');
    try {
      const [out] = await decideOps([{ create: { kind: 'item', line_id: lineId, name, group_id: groupId } }]);
      if (!out?.ok) return false;
      if (out.id) {
        const rec: StockItem = { id: out.id, name: out.name ?? name, code: line.code, unit_id: line.linked_unit_id };
        setStockItems((prev) => (prev.some((x) => x.id === rec.id) ? prev : [...prev, rec]));
        if (out.created === false) setMessage(`Loaded already has ‘${rec.name}’ — linked that instead.`);
      }
      setItemForm(null);
      return true;
    } finally {
      setCreatingItem(false);
    }
  };
  const createItem = async () => {
    if (!itemForm) return;
    if (await createItemAndApply(itemForm.lineId, itemForm.name, itemForm.groupId)) setPicker(null);
  };

  // Loaded names a brand on the line but has no record for it, and refuses to
  // receive the line until it does. Made from the picker: the person's own
  // edit (an accepted create_brand suggestion goes through acceptRows).
  const createBrandAndApply = async (lineId: string, name: string): Promise<boolean> => {
    if (!venueId || !name || creatingBrandLine) return false;
    setCreatingBrandLine(lineId);
    setMessage('');
    try {
      const [out] = await decideOps([{ create: { kind: 'brand', line_id: lineId, name } }]);
      if (!out?.ok) return false;
      if (out.id) {
        const rec: Brand = { id: out.id, name: out.name ?? name };
        setBrands((prev) => (prev.some((x) => x.id === rec.id) ? prev : [...prev, rec]));
      }
      return true;
    } finally {
      setCreatingBrandLine(null);
    }
  };

  // Pick a brand Loaded already has for the line — or none at all, the way out
  // when Loaded's scan read a supplier's name as a brand. The person's edit;
  // brand_set carries it to Loaded on receive.
  const onBrand = (idx: number, brandId: string) => {
    const b = brands.find((x) => x.id === brandId);
    void editLine(idx, { linked_brand_id: b?.id ?? null, brand: b?.name ?? null, brand_set: true });
  };
  const submitNewBrand = async () => {
    if (!picker || !brandName.trim()) return;
    const ok = await createBrandAndApply(picker.lineId, brandName.trim());
    if (ok) afterCreate();
  };

  // Prefill the create-item form from Norm's proposal for the line (the
  // matcher's suggested name and stock group), else the line's own words.
  const openItemForm = (l: Line) => {
    const iss = issues.find((i) => i.code === 'item_unmatched' && String(i.line_id) === String(l.id));
    const d = (iss?.data || {}) as { suggested_name?: string; suggested_group_id?: string };
    const create = itemSuggFor(l.id);
    const p = (create?.kind === 'create_item' ? create.payload || {} : {}) as { name?: string; group_id?: string };
    if (itemForm?.lineId === l.id) return;
    setItemForm({
      lineId: l.id,
      name: p.name || d.suggested_name || l.description || l.code || '',
      groupId: p.group_id || d.suggested_group_id || '',
    });
  };

  // ---- The line's pickers (CatalogPicker) ---------------------------------
  // Tap the stock item, unit or brand box → search what Loaded has, pick, or
  // create a new one. One dialog for all three, so they behave alike.
  const openPicker = (kind: 'item' | 'unit' | 'brand', l: Line) => {
    // The search opens empty for all three; the stock-item list meanwhile
    // shows the best matches for the line (matchItems), so an item Loaded
    // already has shows up before anyone makes a second one.
    setLinkQuery('');
    setPicker({ kind, lineId: l.id, mode: 'pick', query: '' });
  };
  // Switch the open picker to creating a new one. `back`: the create was
  // started from the stock-item form (its unit or brand), so it returns there.
  const startCreate = (kind: 'item' | 'unit' | 'brand', back = false) => {
    const l = picker ? docRef.current.lines.find((x) => x.id === picker.lineId) : undefined;
    if (!picker || !l) return;
    if (kind === 'item') openItemForm(l);
    if (kind === 'unit') openUnitForm(l, picker.kind === 'unit' && picker.mode === 'pick' ? picker.query.trim() : '');
    if (kind === 'brand') setBrandName(picker.kind === 'brand' && picker.query.trim() ? picker.query.trim() : (l.brand || ''));
    setPicker({ ...picker, kind, mode: 'create', back });
  };
  // A unit or brand created (or found): back to the stock-item form when it
  // was started from there, else done.
  const afterCreate = () => {
    setPicker((p) => (p?.back ? { ...p, kind: 'item', mode: 'create', back: false } : null));
  };

  // Notes persist on a debounce so a keystroke isn't a PATCH each.
  const notesTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onNotes = (value: string) => {
    setDoc((prev) => ({ ...prev, notes: value }));
    if (!workingDocId) return;
    // The wait counts as a save in progress, so Receive waits for the notes.
    if (notesTimer.current) clearTimeout(notesTimer.current);
    else setPendingSaves((n) => n + 1);
    notesTimer.current = setTimeout(() => {
      notesTimer.current = null;
      setPendingSaves((n) => Math.max(0, n - 1));
      void patchDoc([{ op: 'update_notes', value }]);
    }, 500);
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
      if (venueId) window.dispatchEvent(new CustomEvent(INVOICE_ACTIONED_EVENT, {
        detail: { venueId, invoiceId: doc.invoice_id, sourceDocId: workingDocId },
      }));
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Failed');
    }
  };

  // ---- Shared renderers (chips + rows read the SAME suggestion objects) --
  // The chip's look, shared by the suggestion chips and the blocker-backed
  // create chips: the proposed value, a ✓ that accepts, an optional ✕.
  const chipBox = (
    label: string,
    title: string,
    accept: { onClick: () => void; label: string; title: string; disabled?: boolean },
    dismiss?: { onClick: () => void; label: string; title: string },
  ) => (
    <div>
      <span style={chipStyle} title={title}>
        <span style={chipLabel}>{label}</span>
        <IconButton icon={Check} iconSize={14} className="n-icon-btn--xs" label={accept.label} title={accept.title}
          onClick={accept.onClick} disabled={accept.disabled} style={{ color: 'var(--ok)' }} />
        {dismiss && (
          <IconButton icon={X} iconSize={14} className="n-icon-btn--xs" label={dismiss.label} title={dismiss.title}
            onClick={dismiss.onClick} />
        )}
      </span>
    </div>
  );
  const suggChip = (s: Suggestion | undefined, label?: string) => {
    if (!s || viewLoaded) return null;
    const state = stateOf(s.id);
    if (record && (state !== 'pending' || doneState)) {
      // Read back, not acted on here: the suggestion and what happened to it.
      const by = lastAction.get(s.id)?.by === 'norm' ? ' by Norm' : '';
      const said = state === 'accepted' ? `Accepted${by}` : state === 'dismissed' ? `Dismissed${by}` : 'Not acted on';
      return (
        <div>
          <span style={{ ...chipStyle, ...(state === 'accepted' ? {} : { opacity: 0.75 }) }} title={s.explanation}>
            <span style={{ ...chipLabel, textDecoration: state === 'dismissed' ? 'line-through' : undefined }}>
              {label ?? `${fmtVal(s.current)} → ${fmtVal(s.proposed)}`}
            </span>
            <span style={{ fontSize: 'var(--fs-xs, 11px)', fontWeight: 600, whiteSpace: 'nowrap', padding: '0 4px', color: state === 'accepted' ? 'var(--ok)' : 'var(--muted)' }}>
              {said}
            </span>
          </span>
        </div>
      );
    }
    if (doneState) return null;
    return chipBox(
      label ?? `${fmtVal(s.current)} → ${fmtVal(s.proposed)}`,
      s.explanation,
      { onClick: () => acceptSuggestion(s), label: 'Accept suggestion', title: `Accept — ${s.explanation}` },
      { onClick: () => dismissAction(s.id), label: 'Dismiss suggestion', title: 'Dismiss this suggestion' },
    );
  };
  // Record mode: what a person changed by hand on a field, the last change
  // standing — not one an Undo cancelled or an accepted suggestion replaced.
  // Norm's suggestions show their own outcomes beside it.
  const handChip = (lineId: string, field: string) => {
    if (!record) return null;
    // Only on the invoice line it was made on (a line Norm adds can take
    // another line's place in a later review).
    const line = docLive.lines.find((l) => String(l.id) === String(lineId)) as Record<string, unknown> | undefined;
    const e = [...(docLive.edits || [])].reverse()
      .find((x) => String(x.line_id ?? '') === String(lineId) && x.field === field
        && !x.undone_at && !x.superseded_by && !!line && sameLine(x, line));
    if (!e) return null;
    const said = `By hand: ${fmtVal(e.before)} → ${fmtVal(e.after)}`;
    return (
      <div>
        <span style={chipStyle} title={e.at ? `Changed ${String(e.at).replace('T', ' ').slice(0, 16)}` : undefined}>
          <span style={chipLabel}>{said}</span>
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
  // A line blocker with no one-click remedy (no item Norm could propose, no
  // unit it could read, a brand to pick): open that line's own form instead,
  // so every "must be created in Loaded" row has a way forward.
  const FIX_FORMS: Record<string, 'item' | 'unit' | 'brand'> = {
    item_unmatched: 'item',
    item_answer_missing: 'item',
    unit_missing: 'unit',
    unit_answer_missing: 'unit',
    brand_unknown: 'brand',
  };
  const openFix = (i: Issue) => {
    const form = FIX_FORMS[i.code];
    const l = docRef.current.lines.find((x) => String(x.id) === String(i.line_id ?? ''));
    if (!form || !l) return;
    if (collapsed) setExpandedFull(true);
    openPicker(form, l);
  };

  // The open picker: pick what Loaded has, or create a new one. The forms
  // share their submit logic with Accept (createUnitAndApply & co.).
  const pickerView = () => {
    if (!picker) return null;
    const idx = doc.lines.findIndex((x) => x.id === picker.lineId);
    const l = doc.lines[idx];
    if (!l) return null;
    const close = () => setPicker(null);
    const back = () => setPicker(picker.back
      ? { ...picker, kind: 'item', mode: 'create', back: false }
      : { ...picker, mode: 'pick' });
    const q = picker.query.trim().toLowerCase();
    const setQuery = (query: string) => setPicker({ ...picker, query });
    const subtitle = l.description || l.code || null;
    const actions = (busy: boolean, disabled: boolean, label: string, busyLabel: string, onGo: () => void, title?: string) => (
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 'auto' }}>
        <Button size="sm" variant="quiet" onClick={back}>Back</Button>
        <Button size="sm" variant="primary" onClick={onGo} disabled={busy || disabled} title={title}>
          {busy ? busyLabel : label}
        </Button>
      </div>
    );
    let title = '';
    let body: React.ReactNode = null;
    // The current choice first (ticked) while nothing is typed, then the rest
    // — the same for all three lists, so what the line holds is always the
    // first thing you see.
    const currentFirst = (opts: PickerOption[], currentId?: string | null): PickerOption[] => {
      if (q || !currentId) return opts;
      const cur = opts.find((o) => o.id === currentId);
      return cur ? [cur, ...opts.filter((o) => o.id !== currentId)] : opts;
    };
    // The stock item list holds only the best matches, so the linked item is
    // added from the catalogue (or the line) when it isn't among them.
    const itemOptions = (ln: Line): PickerOption[] => {
      const typed = linkQuery.trim();
      const found = matchItems(typed || (ln.description || ln.code || ''))
        .map((it): PickerOption => ({ id: it.id, name: it.name || '', sub: it.code }));
      if (typed || !ln.linked_item_id) return found;
      const cur = stockItems.find((it) => it.id === ln.linked_item_id);
      const current: PickerOption = { id: ln.linked_item_id, name: cur?.name || ln.item_name || 'Current item', sub: cur?.code };
      return [current, ...found.filter((o) => o.id !== current.id)];
    };
    if (picker.kind === 'item' && picker.mode === 'pick') {
      title = 'Stock item';
      body = (
        <PickerList query={linkQuery} onQuery={(v) => { setLinkQuery(v); setQuery(v); }}
          placeholder="Search Loaded’s stock items…"
          options={itemOptions(l)}
          hint={linkQuery.trim() ? null : 'Best matches for this line'}
          currentId={l.linked_item_id} onPick={(o) => linkItem(l.id, o.id)}
          createLabel="Create a new stock item" onCreate={() => startCreate('item')}
          emptyText={stockItems.length ? 'Nothing matches — try other words, or create it.' : 'Loading stock items…'} />
      );
    } else if (picker.kind === 'unit' && picker.mode === 'pick') {
      title = 'Unit';
      const opts = sortedUnits.filter((u) => !q || (u.name || '').toLowerCase().includes(q));
      body = (
        <PickerList query={picker.query} onQuery={setQuery} placeholder="Search units…"
          options={currentFirst(opts.map((u): PickerOption => ({ id: u.id, name: u.name, sub: u.type ?? null })), l.linked_unit_id)}
          currentId={l.linked_unit_id}
          onPick={(o) => { onUnit(idx, o.id); close(); }}
          createLabel="Create a new unit" onCreate={() => startCreate('unit')}
          emptyText="No unit matches — create it." />
      );
    } else if (picker.kind === 'brand' && picker.mode === 'pick') {
      title = 'Brand';
      const opts = sortedBrands.filter((b) => !q || (b.name || '').toLowerCase().includes(q));
      body = (
        <PickerList query={picker.query} onQuery={setQuery} placeholder="Search brands…"
          options={currentFirst(opts.map((b): PickerOption => ({ id: b.id, name: b.name })), l.linked_brand_id)}
          currentId={l.linked_brand_id ?? null}
          onPick={(o) => { onBrand(idx, o.id); close(); }}
          noneLabel="No brand" onNone={() => { onBrand(idx, ''); close(); }}
          createLabel="Create a new brand" onCreate={() => startCreate('brand')}
          emptyText={brands.length ? 'No brand matches — create it.' : 'Loading brands…'} />
      );
    } else if (picker.kind === 'item' && itemForm) {
      title = 'New stock item';
      body = (
        <>
          <label style={fieldCol}>
            <span className="n-label">Name</span>
            <input type="text" className="n-input" value={itemForm.name} autoFocus={autoFocusField()}
              onChange={(e) => setItemForm({ ...itemForm, name: e.target.value })} style={{ width: '100%' }} />
          </label>
          <label style={fieldCol}>
            <span className="n-label">Stock group</span>
            <select className="n-select" value={itemForm.groupId} onChange={(e) => setItemForm({ ...itemForm, groupId: e.target.value })}
              style={{ width: '100%' }}>
              <option value="">Choose a stock group…</option>
              {sortedGroups.map((g) => <option key={g.id} value={g.id}>{g.name}{g.category ? ` · ${g.category}` : ''}</option>)}
            </select>
          </label>
          {/* The LINE's unit and brand — set here, they are set on the line. */}
          <label style={fieldCol}>
            <span className="n-label">Unit it comes in</span>
            <span style={{ display: 'flex', gap: 8 }}>
              <select className="n-select" value={l.linked_unit_id || ''} onChange={(e) => onUnit(idx, e.target.value)}
                style={{ flex: 1, minWidth: 0, ...(!l.linked_unit_id ? fieldMissing : {}) }}>
                {!units.some((u) => u.id === l.linked_unit_id) && <option value={l.linked_unit_id || ''}>Choose a unit…</option>}
                {sortedUnits.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
              <Button size="sm" variant="quiet" onClick={() => startCreate('unit', true)}>New unit…</Button>
            </span>
          </label>
          <label style={fieldCol}>
            <span className="n-label">Brand</span>
            <span style={{ display: 'flex', gap: 8 }}>
              <select className="n-select" value={l.linked_brand_id || ''} onChange={(e) => onBrand(idx, e.target.value)}
                style={{ flex: 1, minWidth: 0 }}>
                <option value="">No brand</option>
                {l.linked_brand_id && !brands.some((b) => b.id === l.linked_brand_id) && (
                  <option value={l.linked_brand_id}>{l.brand || 'Current brand'}</option>
                )}
                {sortedBrands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
              <Button size="sm" variant="quiet" onClick={() => startCreate('brand', true)}>New brand…</Button>
            </span>
          </label>
          {!doc.linked_supplier_id && (
            <div style={{ ...iconLine, fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>
              <Icon icon={TriangleAlert} size={14} style={iconNudge} />
              Choose the invoice’s supplier first (at the top of the card).
            </div>
          )}
          {actions(creatingItem, !itemForm.name.trim() || !itemForm.groupId || !l.linked_unit_id || !doc.linked_supplier_id,
            'Create & link', 'Creating…', () => { void createItem(); },
            "creates this stock item in Loaded, with this supplier's code and cost, and links the line")}
        </>
      );
    } else if (picker.kind === 'unit' && unitForm) {
      title = 'New unit';
      body = (
        <>
          <label style={fieldCol}>
            <span className="n-label">Name</span>
            <input type="text" className="n-input" value={unitForm.name} placeholder="e.g. 6x700ml" autoFocus={autoFocusField()}
              onChange={(e) => setUnitForm({ ...unitForm, name: e.target.value, existing: null })}
              onBlur={() => { void checkUnitName(unitForm.lineId, unitForm.name); }}
              style={{ width: '100%' }} />
          </label>
          {unitForm.existing ? (
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
              Loaded already has ‘{unitForm.existing.name}’ — the line will use that.
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
              <label style={{ ...fieldCol, flex: 1 }}>
                <span className="n-label">Type</span>
                <select className="n-select" value={unitForm.type}
                  onChange={(e) => setUnitForm({ ...unitForm, type: e.target.value })} style={{ width: '100%' }}>
                  <option value="">Choose…</option>
                  <option value="Weight">Weight</option>
                  <option value="Volume">Volume</option>
                  <option value="Count">Count</option>
                </select>
              </label>
              <label style={{ ...fieldCol, width: 140 }}>
                <span className="n-label">Size{UNIT_TYPE_SIZE[unitForm.type] ? ` (${UNIT_TYPE_SIZE[unitForm.type]})` : ''}</span>
                <input type="number" step="any" min="0" className="n-input" value={unitForm.size}
                  onChange={(e) => setUnitForm({ ...unitForm, size: e.target.value })}
                  style={{ ...numInput, width: '100%' }} />
              </label>
            </div>
          )}
          {unitForm.checking && (
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Checking Loaded…</div>
          )}
          {actions(creatingUnitLine === l.id,
            unitForm.checking || !unitForm.name.trim() || (!unitForm.existing && (!unitForm.type || !(parseFloat(unitForm.size) > 0))),
            unitForm.existing ? 'Use it' : 'Create & use', 'Creating…', () => { void submitUnitForm(); },
            unitForm.existing ? 'use the unit Loaded already has' : 'creates this unit in Loaded and uses it on this line')}
        </>
      );
    } else if (picker.kind === 'brand') {
      title = 'New brand';
      body = (
        <>
          <label style={fieldCol}>
            <span className="n-label">Name</span>
            <input type="text" className="n-input" value={brandName} autoFocus={autoFocusField()}
              onChange={(e) => setBrandName(e.target.value)} style={{ width: '100%' }} />
          </label>
          {actions(creatingBrandLine === l.id, !brandName.trim(), 'Create & use', 'Creating…',
            () => { void submitNewBrand(); }, 'creates this brand in Loaded and uses it on this line')}
        </>
      );
    }
    return (
      <PickerDialog title={title} subtitle={subtitle} onClose={close}>
        {body}
      </PickerDialog>
    );
  };

  // A fix's change in a few words ("unit cost 22.35 → 28.69"), for a row
  // that carries several of them (totals).
  const fixSummary = (sg: Suggestion) => {
    const ln = sg.line_id ? doc.lines.find((l) => String(l.id) === String(sg.line_id)) : undefined;
    const what = (sg.field || '').replace(/_/g, ' ');
    const where = ln ? `${ln.item_name || ln.description || ln.code || 'line'}: ` : '';
    return `${where}${what} ${fmtVal(sg.current)} → ${fmtVal(sg.proposed)}`;
  };
  // One row of the review: what Norm noticed, said as what Accept will do,
  // and the one Accept. Clicking the sentence goes to its line.
  const reviewRowView = (r: ReviewRow) => {
    const i = r.issue as Issue | undefined;
    const fixes = r.fixes as Suggestion[];
    const lead = fixes[0] ?? null;
    const single = fixes.length === 1 ? lead : null;
    const isAdd = lead?.kind === 'add_line';
    const valuePair = single && (single.kind === 'line_value' || single.kind === 'header_value')
      && (single.current != null || single.proposed != null)
      ? ` (${fmtVal(single.current)} → ${fmtVal(single.proposed)})` : '';
    // An added line says what it arrives as — the item and unit Norm matched,
    // or will create / pick on the same Accept.
    let addsAs = '';
    if (isAdd && lead) {
      const p = (lead.payload || {}) as Record<string, unknown>;
      const itemFix = fixes.find((f) => f.field === 'linked_item_id');
      const unitFix = fixes.find((f) => f.field === 'unit');
      const item = p.item_name || itemFix?.proposed;
      const unit = unitFix?.proposed ?? p.unit;
      if (item || unit) {
        addsAs = ` — add it as ${[item ? `'${item}'` : null, unit ? `(${unit})` : null].filter(Boolean).join(' ')}`;
      }
    }
    const text = lead ? `${lead.explanation}${valuePair}${addsAs}` : (i?.message ?? '');
    const needs = r.needs?.length ? needWords(r.needs) : [];
    const decided = lastAction.get(lead ? lead.id : (i?.id ?? ''));
    const byNorm = decided?.by === 'norm' && decided.action === 'accepted';
    const gate = lead?.gate || i?.gate;
    const gateLabel = gate ? gateLabels[gate] : undefined;
    const destructive = lead?.kind === 'delete_invoice';
    const live = r.open && !doneState;
    const notActioned = r.open && doneState;
    const hasFix = fixes.length > 0;
    const byHand = !!i && !r.open && docLive.issue_states?.[i.id]?.how === 'by_hand';
    const undoTarget: Suggestion | null = single;
    const onLine = !!r.lineId && doc.lines.some((l) => String(l.id) === r.lineId);
    const goToLine = onLine
      ? () => document.getElementById(`riv-${uid}-${r.lineId}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      : undefined;
    const busy = acceptingRows || accepting !== null;
    const links = destructive && lead ? deleteLinks(lead) : [];
    const acceptTitle = lead ? ACCEPT_LABELS[lead.kind] : undefined;
    // One glance per row: done, blocking autopilot, or a change to decide.
    const rowIcon = !r.open ? Check : i?.blocking ? TriangleAlert : CircleDot;
    const rowIconColor = !r.open ? 'var(--ok)' : !live ? 'var(--icon)' : i?.blocking ? 'var(--error)' : 'var(--accent)';
    return (
      <div key={r.key} style={{ padding: '3px 0', fontSize: 'var(--fs-sm)', lineHeight: 1.35, color: live ? 'var(--text)' : 'var(--muted)' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <Icon icon={rowIcon} size={14} style={{ position: 'relative', top: 2, flex: '0 0 auto', color: rowIconColor }} />
          <span onClick={goToLine} title={goToLine ? 'show this line' : undefined}
            style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere', cursor: goToLine ? 'pointer' : undefined, ...(!r.open ? { textDecoration: 'line-through' } : {}) }}>
            {text}
            {needs.length > 0 && ` — once it's added, it still needs ${needs.join(' and ')}`}
            {byHand && <span style={{ fontStyle: 'italic' }}> — your pick</span>}
            {notActioned && <span style={{ fontStyle: 'italic' }}> — not actioned</span>}
          </span>
          {byNorm && !r.open && (
            <Badge title={decided?.at ? `by Norm at ${decided.at}` : 'by Norm'}>Applied by Norm</Badge>
          )}
          {links.length > 0 && (
            <span style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px' }}>
              {links.map((lk) =>
                lk.href ? (
                  <a key={lk.label} href={lk.href} target="_blank" rel="noreferrer"
                    className="n-btn n-btn--link" style={{ fontSize: 'var(--fs-sm)' }}>
                    {lk.label}<Icon icon={ExternalLink} size={12} />
                  </a>
                ) : lk.onClick ? (
                  <Button key={lk.label} variant="link" onClick={lk.onClick} style={{ fontSize: 'var(--fs-sm)' }}>
                    {lk.label}
                  </Button>
                ) : (
                  <span key={lk.label} style={{ color: 'var(--muted)', fontStyle: 'italic' }}>{lk.label}</span>
                ),
              )}
            </span>
          )}
          {!r.open && !doneState && !destructive && undoTarget && lastAction.get(undoTarget.id)?.action === 'accepted'
            && !lastAction.get(undoTarget.id)?.after?.by_hand && (
            <Button variant="quiet" size="xs" onClick={() => undoAction(undoTarget, undoTarget.id)} title="undo this change">
              Undo
            </Button>
          )}
          {live && hasFix && (
            <Button variant={destructive ? 'danger' : 'secondary'} size="xs" onClick={() => { void acceptRows([r]); }}
              disabled={busy} style={busy ? { opacity: 1 } : undefined} title={acceptTitle}>
              {busy ? 'Applying…' : 'Accept'}
            </Button>
          )}
          {/* Nothing could fix it (the server logs these): open the line's own
              picker so there is still a way forward. */}
          {live && !hasFix && i && FIX_FORMS[i.code] && i.line_id && (
            <Button variant="secondary" size="xs" onClick={() => openFix(i)}
              title="open this line's picker to choose or create what it needs">
              Fix…
            </Button>
          )}
        </div>
        {fixes.length > 1 && !isAdd && (
          <ul style={{ margin: '2px 0 0 22px', paddingLeft: 16, color: 'var(--text-soft)' }}>
            {fixes.map((sg) => (
              <li key={sg.id} style={stateOf(sg.id) !== 'pending' ? { textDecoration: 'line-through' } : undefined}>
                {fixSummary(sg)}
              </li>
            ))}
          </ul>
        )}
        {/* Which venue setting would let Norm do this on its own. */}
        {live && gateLabel && (
          <div style={{ marginLeft: 22, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            Auto-receive needs “{gateLabel}” switched on for this venue
          </div>
        )}
      </div>
    );
  };
  // The header's status pill. A received (or deleted) invoice says so — its
  // review verdict ("Ready to receive") no longer describes it.
  const confidenceChip = dojo ? null
    : draftDeleted ? <Badge>Deleted</Badge>
    : doneState ? <Badge tone="ok">Received</Badge>
    : liveConfidence && !studying ? (
      <Badge tone={liveConfidence === 'ready' ? 'ok' : 'accent'}>
        {liveConfidence === 'ready' ? 'Ready to receive' : 'Needs review'}
      </Badge>
    ) : null;
  const reviewSummaryText = !reviewed
    ? (reviewing ? 'reviewing against the invoice…' : 'not yet reviewed')
    : (liveConfidence === 'ready' ? 'ready to receive' : `needs review · ${blockingOpen.length} blocking`)
      + (pendingSuggestions.length ? ` · ${pendingSuggestions.length} suggested` : '');

  // The document no longer exists (drafts deleted before tombstones were
  // introduced were removed outright) — say so instead of spinning forever.
  if (missingDoc) {
    return (
      <div className="n-card" style={{ ...iconLine, padding: '12px 16px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        <Icon icon={Check} size={16} tone="muted" style={iconNudge} />
        This invoice draft no longer exists — it was deleted from Loaded (a supplier statement or duplicate).
      </div>
    );
  }

  // The block carries only a working_document_id until the draft loads; show a
  // loading state rather than an empty card in that gap.
  if (docUrl && !doc.invoice_id) {
    return (
      <div className="n-card">
        <PageState kind="loading" title="Opening the invoice…" />
      </div>
    );
  }

  const card = (
    // In the overlay the card fills its 1200px-capped wrapper; a 96vw width
    // used to overrun the wrapper's 16px gutters on a phone.
    <div className="n-card" style={{ overflow: 'hidden', position: 'relative' }}>
    {pickerView()}
    {/* Admin X-ray: the whole body renders read-only while a non-Norm view is
        on (the slider itself is span-based, so it stays clickable inside the
        disabled fieldset). */}
    <fieldset disabled={viewLoaded} style={{ border: 'none', margin: 0, padding: 0, minWidth: 0 }}>
      {/* A credit note stays receivable, but the consequence is surfaced as a
          visible banner (not a review "note"): receiving it reverses stock. */}
      {isCredit && !dojo && (
        <div style={{ ...iconLine, padding: '8px 16px', background: 'var(--error-bg)', color: 'var(--error)', fontSize: 'var(--fs-sm)', fontWeight: 600 }}>
          <Icon icon={TriangleAlert} size={16} style={iconNudge} />
          Credit note — receiving this reverses stock and cost (quantities are negative).
        </div>
      )}
      {/* This supplier has no reading-instructions yet — Norm is studying the
          invoice in the background to write them (async; a spec appears when the
          study finishes). Set by the review's auto-spec trigger. */}
      {docLive.sensei_studying && !dojo && (
        <div style={{ ...iconLine, padding: '8px 16px', background: 'var(--info-bg)', color: 'var(--info)', fontSize: 'var(--fs-sm)', fontWeight: 500 }}>
          <Icon icon={Hourglass} size={16} style={iconNudge} />
          Norm is studying this supplier to create reading instructions. This may take 2 - 3 minutes.
        </div>
      )}
      {/* Header — editable form (Loaded-parity) */}
      <div style={{ padding: '14px 16px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: '8px 12px' }}>
          <span style={{ display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span style={{ fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>
              {isCredit ? 'Receive credit note' : 'Receive invoice'}
            </span>
            {/* Never let a credit be mistaken for a delivery: it REVERSES
                stock and cost, and every quantity on it is negative. */}
            {isCredit && (
              <Badge tone="error" title="this document credits the supplier — receiving it reverses stock and cost">
                Credit note
              </Badge>
            )}
            {confidenceChip}
          </span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
            {/* Admin X-ray slider: Norm's editable draft | the verbatim PDF
                extraction | Loaded's pristine snapshot | the replica (our full
                resolution, read-only). */}
            {isPlatformAdmin && docLive.loaded_snapshot && (
              <span role="group" aria-label="Data source"
                title="Admin: flip every field between Norm's draft, what the AI read off the invoice, what Loaded currently holds, and the replica"
                style={{ display: 'inline-flex', height: 30, padding: 2, gap: 2, boxSizing: 'border-box', border: `1px solid ${viewLoaded ? 'var(--brand-soft)' : 'var(--line-strong)'}`, borderRadius: 'var(--radius)', background: 'var(--bg)', fontSize: 'var(--fs-sm)', userSelect: 'none' }}>
                {([
                  ['norm', 'Norm', true],
                  ['extracted', 'Extracted', !!docLive.extracted_snapshot],
                  ['loaded', 'Loaded', true],
                  ['replica', 'Replica', !!docLive.replica],
                ] as const).map(([mode, label, available]) => (
                  <span key={mode} role="button" tabIndex={available ? 0 : -1} aria-pressed={viewMode === mode}
                    onClick={() => { if (available) setViewMode(mode); }}
                    onKeyDown={(e) => { if (available && (e.key === 'Enter' || e.key === ' ')) setViewMode(mode); }}
                    title={mode === 'extracted' && !available ? 'No extraction for this invoice (no readable invoice attached)' : mode === 'replica' && !available ? 'No replica — the review has not produced one yet (re-run the replica)' : mode === 'replica' ? "What Norm thinks the invoice says — every rule applied; the suggestions are where it differs from Loaded" : undefined}
                    style={{
                      display: 'inline-flex', alignItems: 'center', padding: '0 8px', borderRadius: 'var(--radius-sm)', whiteSpace: 'nowrap',
                      cursor: available ? 'pointer' : 'not-allowed', opacity: available ? 1 : 0.45,
                      background: viewMode === mode ? (mode === 'norm' ? 'var(--selected)' : 'var(--accent-soft)') : 'transparent',
                      color: viewMode === mode ? (mode === 'norm' ? 'var(--text)' : 'var(--accent)') : 'var(--text-soft)',
                      fontWeight: viewMode === mode ? 600 : 500,
                    }}>
                    {label}
                  </span>
                ))}
              </span>
            )}
            {viewLoaded && (
              <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--accent)' }}>
                {viewMode === 'extracted' ? 'showing what the AI read off the invoice — read-only' : viewMode === 'replica' ? 'showing what Norm thinks the invoice says — read-only' : "showing Loaded's data — read-only"}
              </span>
            )}
            {compact && (
              <Button size="sm" onClick={() => setExpandedFull((v) => !v)}>
                {expandedFull
                  ? <>Hide details<Icon icon={ChevronDown} size={14} /></>
                  : <>Show full invoice<Icon icon={ChevronRight} size={14} /></>}
              </Button>
            )}
            {/* Deep-link to Loaded's own UI (the user's Loaded session
                authenticates) — the escape hatch for everything the card
                can't fix (attach/replace a copy, credit notes…). Received
                invoices resolve directly; unreceived drafts don't resolve on
                the invoice route, so they land on the invoices list. */}
            {!doc.is_deleted && (
              <a href={doc.is_received ? loadedInvoiceUrl(doc.invoice_id) : `${LOADED_APP_URL}/stock/invoices`}
                target="_blank" rel="noreferrer"
                title={doc.is_received ? 'Open this invoice in Loaded' : 'Open Loaded invoices'}
                aria-label={doc.is_received ? 'Open this invoice in Loaded' : 'Open Loaded invoices'}
                className="n-btn n-btn--secondary n-btn--sm">
                Open in Loaded<Icon icon={ExternalLink} size={14} />
              </a>
            )}
            {doc.file_id && (
              <IconButton
                icon={FileText}
                label={copyOpen ? 'Hide the invoice copy' : 'View invoice copy'}
                aria-pressed={copyOpen}
                onClick={() => setCopyOpen((v) => !v)}
              />
            )}
          </div>
        </div>
        {collapsed && (
          <div style={{ display: 'flex', gap: '2px 10px', alignItems: 'baseline', flexWrap: 'wrap', marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{doc.supplier_name || '—'}</span>
            <span>
              {/* Once received/deleted the status pill says so; the review
                  summary ("ready to receive") would contradict it. */}
              {[doc.reference_number || '(no number)', doc.issued_at ? dateVal(doc.issued_at) : null, doneState ? null : reviewSummaryText]
                .filter(Boolean).join(' · ')}
            </span>
            <span style={{ marginLeft: 'auto', fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>
              {/* A copy that prints no total (a sales order): what the lines
                  come to — the figure the receive sends. */}
              {cur(typeof doc.total === 'number' ? doc.total : totals.incl)}
            </span>
          </div>
        )}
        {/* Two columns like Loaded: order/supplier on the left, invoice on the
            right. Collapses to one column when the card is narrow. */}
        {!collapsed && (<>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '12px 24px', marginTop: 14 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <label style={fieldCol}>
              <span className="n-label">Supplier</span>
              {/* X-ray modes show the VALUE (the extraction's printed name /
                  the replica's resolution) as text — the select below can
                  only display ids from the venue's list, which is how the
                  Extracted tab showed "[Unnamed Supplier]" while the copy
                  read 'BIDFOOD FSV DUNEDIN' (110016259, 19 Aug 2026). */}
              {viewLoaded ? (
                <span style={readonlyField}>
                  {doc.supplier_name || '—'}
                </span>
              ) : (<>
              {/* An unlinked supplier renders AMBER — Loaded's server 500s on
                  receiving a supplier-less invoice. */}
              <select className="n-select" value={doc.linked_supplier_id || ''} disabled={doneState} onChange={(e) => onSupplier(e.target.value)}
                style={{ width: '100%', ...(!doneState && !doc.linked_supplier_id && doc.invoice_id ? fieldMissing : {}) }}>
                {!suppliers.some((s) => s.id === doc.linked_supplier_id) && (
                  <option value={doc.linked_supplier_id || ''}>{doc.supplier_name || 'Select supplier'}</option>
                )}
                {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
              {/* The Replica's supplier where it differs from Loaded's — a
                  record to link, or one to create — the same object as its
                  row in the summary list. */}
              {suggChip(supplierSugg, supplierSugg ? `${fmtVal(supplierSugg.current)} → ${fmtVal(supplierSugg.proposed)}` : undefined)}
              {suggChip(createSupplierSugg, createSupplierSugg ? `Create '${fmtVal(createSupplierSugg.proposed)}'` : undefined)}
              </>)}
            </label>
            <label style={fieldCol}>
              <span className="n-label">Order number</span>
              {/* Same x-ray rule: show the order number the view holds (the
                  copy's printed reference in Extracted mode). */}
              {viewLoaded ? (
                <span style={readonlyField}>
                  {doc.purchase_order_number || '—'}
                </span>
              ) : (<>
              <select className="n-select" value={doc.linked_purchase_order_id || ''} disabled={doneState} onChange={(e) => onPo(e.target.value)}
                style={{ width: '100%', ...(poSuggs.length && !doneState ? fieldSuggested : {}) }}>
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
                    ? `Link order ${fmtVal(s.proposed)}`
                    : s.kind === 'unlink_po'
                      ? `Remove order ${fmtVal(s.current)}`
                      : `Keep reference ${fmtVal(s.proposed)} (split order)`)}
                </Fragment>
              ))}
              {/* Split order (accepted): where the rest of the order went. */}
              {doc.split_po_id && !doc.linked_purchase_order_id && doc.split_sibling_invoice_id && (
                <span style={{ marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                  Part of a split order — also invoiced on{' '}
                  <a href={loadedInvoiceUrl(doc.split_sibling_invoice_id)} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
                    the sibling invoice
                  </a>
                </span>
              )}
              </>)}
            </label>
            {doc.order_date && (
              <label style={fieldCol}>
                <span className="n-label">Order date</span>
                <span style={{ display: 'flex', alignItems: 'center', minHeight: 34, fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>{dateVal(doc.order_date)}</span>
              </label>
            )}
            <label style={fieldCol}>
              <span className="n-label">Received date</span>
              {/* Show the invoice date when Loaded holds no received date —
                  mirroring do_receive, which writes issuedAt on receive when
                  this field is untouched. The box used to render empty, so the
                  date was retyped by hand on almost every invoice even after
                  the server default shipped: 22 of the manual edits across 183
                  human receives were header.received_at, more than twice any
                  other field.

                  Display-only on purpose. Writing the default into the draft
                  would put it in the header that `manual_edits` diffs against
                  `loaded_snapshot`, and every untouched receive would be
                  recorded as hand-edited — the phantom-edit bug that made
                  autopilot readiness read 0% from the day it shipped. A user
                  who accepts this date types nothing, so `received_at` stays
                  null and the server writes the same value. */}
              <input type="date" className="n-input" value={dateVal(doc.received_at || doc.issued_at)} disabled={doneState}
                onChange={(e) => patchHeader({ received_at: e.target.value || null })} style={{ width: '100%' }} />
            </label>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <label style={fieldCol}>
              <span className="n-label">Invoice number</span>
              <input type="text" className="n-input" value={doc.reference_number || ''} disabled={doneState}
                onChange={(e) => patchHeader({ reference_number: e.target.value })}
                style={{ width: '100%', ...(!doneState && headerValueSugg('reference_number') ? fieldSuggested : {}) }} />
              {suggChip(headerValueSugg('reference_number'))}
            </label>
            <label style={fieldCol}>
              <span className="n-label">Invoice date</span>
              <input type="date" className="n-input" value={dateVal(doc.issued_at)} disabled={doneState}
                onChange={(e) => patchHeader({ issued_at: e.target.value || null })} style={{ width: '100%' }} />
              {suggChip(headerValueSugg('issued_at'))}
            </label>
            <label style={fieldCol}>
              <span className="n-label">Due date</span>
              <input type="date" className="n-input" value={dateVal(doc.due_at)} disabled={doneState}
                onChange={(e) => patchHeader({ due_at: e.target.value || null })} style={{ width: '100%' }} />
            </label>
            <label style={fieldCol}>
              <span className="n-label">Invoice total</span>
              <input type="number" step="any" className="n-input" value={doc.total ?? 0} disabled={doneState}
                onChange={(e) => patchHeader({ total: parseFloat(e.target.value) || 0 })}
                style={{ width: '100%', fontWeight: 600, textAlign: 'right', fontVariantNumeric: 'tabular-nums', ...(!doneState && moneySuggs.length ? fieldSuggested : {}) }} />
              {/* Header money suggestions (total / subtotal / tax / discount)
                  live together under the total field. */}
              {moneySuggs.map((s) => (
                <Fragment key={s.id}>
                  {suggChip(s, `${moneyFieldLabels[s.field ?? ''] ?? s.field}: ${fmtVal(s.current)} → ${fmtVal(s.proposed)}`)}
                </Fragment>
              ))}
            </label>
          </div>
        </div>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginTop: 14, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: doneState ? 'default' : 'pointer' }}>
          <input type="checkbox" checked={includesTax} disabled={doneState}
            onChange={(e) => patchHeader({ unit_cost_includes_tax: e.target.checked })}
            style={{ width: 16, height: 16, margin: 0, accentColor: 'var(--accent)' }} />
          Line item costs include tax
        </label>
        </>)}
      </div>

      {/* Lines → Notes: the full-detail body, hidden while the compact card is
          collapsed ("Show full invoice" reveals it). */}
      {!collapsed && (<>
      {/* Lines */}
      <div style={{ overflowX: 'auto', borderTop: '1px solid var(--line)' }}>
        {/* A floor width: on a phone the table scrolls sideways rather than
            squeezing the description and chips into a column of letters. */}
        <table className="n-table" style={{ minWidth: 900 }}>
          <thead>
            <tr>
              <th style={{ paddingLeft: 16 }}>Code</th>
              <th>{viewMode === 'norm' ? 'Stock item' : 'Description'}</th>
              <th>Brand</th>
              {viewMode === 'extracted' ? (<>
                <th title="the unit column exactly as printed — often how the line is CHARGED (EA/CTN), not the pack size">Unit (printed)</th>
                <th title="the delivered unit of ONE item, derived from the document — what recipe costing and stock need">Unit of measure</th>
              </>) : (
              <th>Unit</th>
              )}
              <th className="num">Qty ordered</th>
              <th className="num">Qty received</th>
              <th className="num">Unit cost</th>
              <th className="num">Tax</th>
              <th className="num" style={{ paddingRight: 16 }}>Total cost</th>
            </tr>
          </thead>
          <tbody>
            {doc.lines.map((l, idx) => {
              // An accepted strike keeps the row VISIBLE, crossed out and
              // inert: it drops out of the totals and is soft-deleted on
              // receive. Restore lives on the row (and on the record's Undo).
              const struck = !!l.struck;
              // The item / brand / unit boxes open the picker only here.
              const editable = viewMode === 'norm' && !doneState && !struck && !dojo;
              const strikeSugg = strikeSuggFor(l.id);
              const itemSugg = itemSuggFor(l.id);
              // Unit dropdown state: a pending suggestion (accent) or a value
              // Loaded can't receive (warn); the tint only while still editable.
              const unitField: React.CSSProperties = struck
                ? { borderColor: 'var(--line)', backgroundColor: 'var(--surface)', color: 'var(--muted)' }
                : unitSuggFor(l.id)
                  ? (doneState ? { borderColor: fieldSuggested.borderColor } : fieldSuggested)
                  : !l.linked_unit_id
                    ? (doneState ? { borderColor: fieldMissing.borderColor } : fieldMissing)
                    : {};
              return (
              <Fragment key={l.id}>
              {/* Struck = muted + crossed out, its fields disabled. No row
                  opacity: it would fade Restore too, the one control here. */}
              <tr id={`riv-${uid}-${l.id}`}
                style={struck ? { textDecoration: 'line-through', color: 'var(--muted)' } : undefined}>
                <td style={{ paddingLeft: 16, color: 'var(--muted)', fontSize: 'var(--fs-sm)', whiteSpace: 'nowrap' }}>{l.display_code || l.code || '—'}</td>
                <td style={{ minWidth: 200 }}>
                  {/* The stock item: a box that opens the picker (search,
                      pick, or create), like unit and brand beside it. It shows
                      what Loaded shows — the supplier's own wording is on the
                      Extracted tab. */}
                  {editable ? (
                    <>
                      <button type="button" className="n-select" onClick={() => openPicker('item', l)}
                        title={l.item_name ? `${l.item_name} — change the stock item` : 'choose or create the stock item'}
                        style={{ ...pickerBox, ...(!l.linked_item_id ? fieldMissing : {}) }}>
                        {l.item_name || (l.linked_item_id ? l.description : 'Choose stock item')}
                      </button>
                    </>
                  ) : (<>
                  {l.item_name || l.description}
                  {/* In the LOADED mirror the server decides (item_is_new):
                      Loaded's screen resolves the stock item against the
                      linked order, so a line with no linkedItemId is only NEW
                      when that lookup also came up empty. */}
                  {!struck && (viewMode === 'norm' ? !l.linked_item_id : viewMode === 'loaded' && l.item_is_new) && (
                    <>{' '}<Badge tone="warn">New</Badge></>
                  )}
                  </>)}
                  {/* The server's strike suggestion (the copy doesn't bill
                      this line) — same object as its summary row. */}
                  {(!struck || record) && suggChip(strikeSugg, 'Not billed on the invoice — strike')}
                  {handChip(l.id, 'item_name')}
                  {handChip(l.id, 'struck')}
                  {!doneState && struck && viewMode === 'norm' && (
                    <Button size="sm" onClick={() => onStrike(idx, false)}
                      title="restore this line (it will be received again)"
                      style={{ marginLeft: 8 }}>
                      Restore
                    </Button>
                  )}
                  {/* Delivered under a different code than ordered — a substitute.
                      Click the badge to expand the original ordered line below. */}
                  {l.substitute_for && !struck && (
                    <button type="button" className="n-badge n-badge--accent" aria-expanded={openSub.has(l.id)}
                      onClick={() => setOpenSub((prev) => { const n = new Set(prev); if (n.has(l.id)) n.delete(l.id); else n.add(l.id); return n; })}
                      title="delivered under a different stock code than ordered — click to show the ordered line"
                      style={{ marginLeft: 8, border: 'none', cursor: 'pointer', fontFamily: 'inherit', fontSize: 'var(--fs-sm)', verticalAlign: 'middle' }}>
                      Substitute<Icon icon={openSub.has(l.id) ? ChevronDown : ChevronRight} size={12} />
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
                      ? `Create '${fmtVal(itemSugg.proposed)}'`
                      : itemSugg.current
                        ? `${fmtVal(itemSugg.current)} → ${fmtVal(itemSugg.proposed)}`
                        : `Link to '${fmtVal(itemSugg.proposed)}'`,
                  )}
                </td>
                <td style={{ color: l.brand && !struck ? 'var(--text-soft)' : 'var(--muted)', ...(l.brand ? {} : { whiteSpace: 'nowrap' }) }}>
                  {editable ? (
                    <button type="button" className="n-select" onClick={() => openPicker('brand', l)}
                      title={l.brand && !l.linked_brand_id
                        ? "Loaded has no record for this brand and won't receive the line until it does"
                        : 'choose a brand, no brand, or create one'}
                      style={{ ...pickerBox, minWidth: 130, ...(l.brand && !l.linked_brand_id ? fieldMissing : {}), ...(l.brand ? {} : { color: 'var(--muted)', fontWeight: 400 }) }}>
                      {l.brand || 'No brand'}
                    </button>
                  ) : (<>
                    {l.brand || 'Not set'}
                    {l.brand && !l.linked_brand_id && !struck && (
                      <>{' '}<Badge tone="warn" title="Loaded has no record for this brand and won't receive the line until it does">New</Badge></>
                    )}
                  </>)}
                  {!struck && viewMode === 'norm' && suggChip(
                    brandSuggFor(l.id), brandLabel(brandSuggFor(l.id)),
                  )}
                  {handChip(l.id, 'brand')}
                </td>
                {viewMode === 'extracted' && (
                  <td style={{ color: 'var(--text-soft)' }}>
                    {l.unit || '—'}
                  </td>
                )}
                <td>
                  {viewMode === 'extracted' ? (
                    <span style={{ color: 'var(--text-soft)' }}>
                      {l.unit_of_measure || '—'}
                      {l.unit_unrecognisable && (
                        <span style={{ color: 'var(--warn)' }}> (unreadable)</span>
                      )}
                    </span>
                  ) : viewMode !== 'norm' ? (
                    // X-ray modes are read-only — the unit renders as text
                    // (extracted lines carry no Loaded unit records at all).
                    <span style={{ color: 'var(--text-soft)' }}>
                      {l.unit_name || l.unit || '—'}
                      {viewMode === 'loaded' && l.unit_is_new && (
                        <>{' '}<Badge tone="warn">New</Badge></>
                      )}
                    </span>
                  ) : studying ? (
                    // The study will re-resolve the unit; don't show the dud one.
                    <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', fontStyle: 'italic' }}>Studying…</span>
                  ) : (
                  <button type="button" className="n-select" disabled={!editable}
                    onClick={() => openPicker('unit', l)}
                    title={l.linked_unit_id ? 'change the unit' : 'choose or create the unit'}
                    style={{ ...pickerBox, minWidth: 120, ...unitField }}>
                    {/* The unresolved state lives INSIDE the box: an unlinked
                        unit string shows marked new — picking a real unit
                        replaces it. */}
                    {units.find((u) => u.id === l.linked_unit_id)?.name
                      || (l.unit ? `${l.unit}${l.linked_unit_id ? '' : ' — new'}` : 'Choose unit')}
                  </button>
                  )}
                  {/* The Replica's unit where it differs from Loaded's: a swap
                      to a unit Loaded has, or one to create. The Norm view
                      shows what LOADED holds and puts the change beneath it —
                      "6 X 750ML" with "→ 12x375ml (new)" — rather than the row
                      quietly displaying a unit that does not exist. */}
                  {!struck && !studying && viewMode === 'norm' && (() => {
                    const us = unitSuggFor(l.id);
                    return suggChip(us, us?.kind === 'create_unit' ? `→ ${fmtVal(us.proposed)} (new)` : undefined);
                  })()}
                  {handChip(l.id, 'unit')}
                  {/* A unit the person picked of a different kind from what
                      stocktakes count the item in: a warning, never a stop —
                      their pick stands (owner, 20 Oct 2026). */}
                  {(docLive.warnings || []).filter((w) => String(w.line_id ?? '') === String(l.id)).map((w) => (
                    <div key={w.code} style={{ ...iconLine, marginTop: 3, fontSize: 'var(--fs-xs)', color: 'var(--warn)' }}>
                      <Icon icon={TriangleAlert} size={12} style={iconNudge} />
                      <span>{w.text}</span>
                    </div>
                  ))}
                </td>
                <td className="num" style={{ color: 'var(--muted)' }}>
                  {/* Loaded prints a literal 0 where nothing on the order is
                      left to claim (a second line sharing a code); every other
                      view says "—", which is the honest reading of "no order
                      row for this line". */}
                  {l.quantity_ordered ?? (viewMode === 'loaded' ? 0 : '—')}
                </td>
                <td className="num">
                  {/* Accent = a qty suggestion is pending (same treatment as the
                      unit dropdown). Longhands only (borderColor /
                      backgroundColor) — never mix with the `border` shorthand. */}
                  <input type="number" step="any" className="n-input" value={l.quantity_received ?? 0} disabled={doneState || struck}
                    onChange={(e) => onQty(idx, parseFloat(e.target.value) || 0)}
                    style={{ ...numInput, width: 70, ...(!doneState && !struck && suggFor(l.id, 'quantity_received') ? fieldSuggested : {}) }} />
                  {!struck && viewMode === 'norm' && suggChip(suggFor(l.id, 'quantity_received'))}
                  {handChip(l.id, 'quantity_received')}
                </td>
                <td className="num" style={{ whiteSpace: 'nowrap' }}>
                  {/* Red ↑ when the invoice cost is above the linked PO's cost. */}
                  {!struck && l.reference_cost != null && (l.unit_cost ?? 0) > l.reference_cost + 0.001 && (
                    <span title={`up from ${cur(l.reference_cost)} on the order`}
                      style={{ display: 'inline-flex', verticalAlign: 'middle', marginRight: 4, color: 'var(--error)' }}>
                      <Icon icon={ArrowUp} size={14} strokeWidth={2} label={`up from ${cur(l.reference_cost)} on the order`} />
                    </span>
                  )}
                  <input type="number" step="any" className="n-input" value={l.unit_cost ?? 0} disabled={doneState || struck}
                    onChange={(e) => onCost(idx, parseFloat(e.target.value) || 0)}
                    style={{ ...numInput, width: 80, ...(!doneState && !struck && suggFor(l.id, 'unit_cost') ? fieldSuggested : {}) }} />
                  {!struck && viewMode === 'norm' && suggChip(suggFor(l.id, 'unit_cost'))}
                  {!struck && viewMode === 'norm' && suggChip(suggFor(l.id, 'sale_tax_rate'), 'Set the tax rate from the catalogue')}
                  {handChip(l.id, 'unit_cost')}
                </td>
                <td className="num" style={{ color: 'var(--muted)' }}>
                  {cur(lineTax((l.quantity_received ?? 0) * (l.unit_cost ?? 0), l.sale_tax_rate, includesTax))}
                </td>
                <td className="num" style={{ paddingRight: 16, fontWeight: 500 }}>
                  {cur((l.quantity_received ?? 0) * (l.unit_cost ?? 0))}
                </td>
              </tr>
              {/* The original ordered line this delivery stood in for — a full,
                  read-only row shown when the substitute badge is expanded. */}
              {l.substitute_for && !struck && openSub.has(l.id) && (
                <tr style={{ background: 'var(--surface)', color: 'var(--text-soft)', fontSize: 'var(--fs-sm)' }}>
                  <td style={{ paddingLeft: 28, whiteSpace: 'nowrap' }}>{l.substitute_for.code || '—'}</td>
                  <td>
                    <Badge>Ordered</Badge>{' '}
                    {l.substitute_for.description}
                  </td>
                  <td style={{ color: 'var(--muted)' }}>—</td>
                  <td>{l.substitute_for.unit || '—'}</td>
                  <td className="num">{l.substitute_for.quantity_ordered ?? '—'}</td>
                  <td className="num" style={{ color: 'var(--muted)' }}>—</td>
                  <td className="num">{l.substitute_for.unit_cost != null ? cur(l.substitute_for.unit_cost) : '—'}</td>
                  <td className="num" style={{ color: 'var(--muted)' }}>—</td>
                  <td className="num" style={{ paddingRight: 16 }}>{l.substitute_for.quantity_ordered != null && l.substitute_for.unit_cost != null ? cur(l.substitute_for.quantity_ordered * l.substitute_for.unit_cost) : '—'}</td>
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
              <tr key={`onr-inline-${o.code || i}-${i}`} style={{ color: 'var(--muted)' }}>
                <td style={{ paddingLeft: 16, whiteSpace: 'nowrap' }}>{o.code || '—'}</td>
                <td>{o.description || '—'}</td>
                <td>—</td>
                <td>{o.unit || '—'}</td>
                <td className="num">{o.quantity_ordered ?? '—'}</td>
                <td className="num">0</td>
                <td className="num">{o.unit_cost != null ? cur(o.unit_cost) : '—'}</td>
                <td className="num">—</td>
                <td className="num" style={{ paddingRight: 16 }}>{cur(0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Replica X-ray: the resolution log — every decision the replica made,
          the troubleshooting record. */}
      {viewMode === 'replica' && (docLive.replica?.resolution_log?.length ?? 0) > 0 && (
        <div style={{ ...section, background: 'var(--surface)' }}>
          <div style={{ ...sectionTitle, marginBottom: 6 }}>Resolution log</div>
          {docLive.replica!.resolution_log!.map((r, i) => (
            <div key={`rl-${i}`} style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--text-soft)', padding: '1px 0', overflowWrap: 'anywhere' }}>{r}</div>
          ))}
        </div>
      )}

      {/* Add Item — right under the lines it adds to, above the order
          sections (which fold away below it). */}
      {!doneState && (
        <div style={{ ...section, position: 'relative' }}>
          <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', width: 240, maxWidth: '100%' }}>
            <Icon icon={Plus} size={16} tone="muted" style={{ position: 'absolute', left: 10, pointerEvents: 'none' }} />
            <input type="text" className="n-input" placeholder="Add item…" value={addQuery}
              onChange={(e) => { setAddQuery(e.target.value); setAddOpen(true); }}
              onFocus={() => setAddOpen(true)}
              onBlur={() => setTimeout(() => setAddOpen(false), 150)}
              style={{ width: '100%', paddingLeft: 32 }} />
          </span>
          {addOpen && filteredStock.length > 0 && (
            <div className="scroll-quiet" style={{ position: 'absolute', zIndex: 20, marginTop: 4, padding: '4px 0', maxHeight: 220, minWidth: 280, background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', boxShadow: '0 6px 18px rgba(26,26,26,0.12)' }}>
              {filteredStock.map((i) => (
                <div key={i.id} className="n-option" onMouseDown={() => addItem(i)}>
                  {i.code ? <span style={{ color: 'var(--muted)' }}>{i.code} · </span> : null}{i.name}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Split order: PO items that arrived on the SIBLING delivery — they were
          received, just on the other invoice. Read-only; never sent on receive. */}
      {(doc.ordered_received_elsewhere?.length ?? 0) > 0 && (
        <div style={section}>
          <div style={{ ...sectionTitle, display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>
            <button type="button" onClick={() => toggleOrderSection('sibling')} aria-expanded={openOrderSection.has('sibling')}
              style={foldButton}>
              <Icon icon={openOrderSection.has('sibling') ? ChevronDown : ChevronRight} size={14} />
              Ordered, received on the sibling delivery ({doc.ordered_received_elsewhere!.length})
            </button>
            {doc.split_sibling_invoice_id && (
              <>
                <span style={{ color: 'var(--muted)', fontWeight: 400 }}>{' · '}</span>
                <a href={loadedInvoiceUrl(doc.split_sibling_invoice_id)} target="_blank" rel="noreferrer"
                  className="n-btn n-btn--link" style={{ fontSize: 'var(--fs-sm)', fontWeight: 500 }}>
                  its invoice<Icon icon={ExternalLink} size={12} />
                </a>
              </>
            )}
          </div>
          {openOrderSection.has('sibling') && (
          <div style={{ overflowX: 'auto', marginTop: 8 }}>
            <table className="n-table" style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Description</th>
                  <th>Unit</th>
                  <th className="num">Qty ordered</th>
                  <th className="num">Qty received</th>
                  <th className="num">Unit cost</th>
                </tr>
              </thead>
              <tbody>
                {doc.ordered_received_elsewhere!.map((o, i) => (
                  <tr key={`${o.code || 'ore'}-${i}`}>
                    <td style={{ whiteSpace: 'nowrap' }}>{o.code || '—'}</td>
                    <td>{o.description || '—'}</td>
                    <td>{o.unit || '—'}</td>
                    <td className="num">{o.quantity_ordered ?? '—'}</td>
                    <td className="num">{o.quantity_received ?? '—'}</td>
                    <td className="num">{o.unit_cost != null ? cur(o.unit_cost) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}
        </div>
      )}

      {/* Ordered, not delivered — PO items with NO invoice line at all (neither by
          code nor as a substitute). Read-only; never sent on receive. The Loaded
          X-ray shows these inline instead, the way Loaded's own screen does. */}
      {viewMode !== 'loaded' && (doc.ordered_not_received?.length ?? 0) > 0 && (
        <div style={section}>
          <button type="button" onClick={() => toggleOrderSection('not_delivered')} aria-expanded={openOrderSection.has('not_delivered')}
            style={{ ...sectionTitle, ...foldButton }}>
            <Icon icon={openOrderSection.has('not_delivered') ? ChevronDown : ChevronRight} size={14} />
            Ordered, not delivered ({doc.ordered_not_received!.length})
          </button>
          {/* No unit cost: what an undelivered item costs isn't known until
              it arrives and is invoiced. */}
          {openOrderSection.has('not_delivered') && (
          <div style={{ overflowX: 'auto', marginTop: 8 }}>
            <table className="n-table" style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Description</th>
                  <th>Unit</th>
                  <th className="num">Qty ordered</th>
                </tr>
              </thead>
              <tbody>
                {doc.ordered_not_received!.map((o, i) => (
                  <tr key={`${o.code || 'onr'}-${i}`}>
                    <td style={{ whiteSpace: 'nowrap' }}>{o.code || '—'}</td>
                    <td>{o.description || '—'}</td>
                    <td>{o.unit || '—'}</td>
                    <td className="num">{o.quantity_ordered ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}
        </div>
      )}

      {/* Totals block (bottom, Loaded layout). Display sums only — the stated
          total is a working value the server reconciles; a real drift rides
          in as a header_value suggestion, never derived here. */}
      <div style={{ ...section, display: 'flex', justifyContent: 'flex-end' }}>
        <div style={{ minWidth: 240, fontSize: 'var(--fs-base)', fontVariantNumeric: 'tabular-nums', color: 'var(--text-soft)' }}>
          <div style={totalRow}>
            <span>Total excl tax</span><span>{cur(totals.excl)}</span>
          </div>
          <div style={totalRow}>
            <span>Tax</span><span>{cur(totals.tax)}</span>
          </div>
          {/* The Loaded mirror shows the discount row even at zero, and a
              Rounding row reconciling the derived total to Loaded's stated
              one — both exactly as Loaded's own screen does. */}
          {(viewMode === 'loaded' || !!totals.discount) && (
            <div style={totalRow}>
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
              <div style={totalRow}>
                <span>Rounding</span><span>{rounding < 0 ? `−${cur(-rounding)}` : cur(rounding)}</span>
              </div>
            );
          })()}
          <div style={{ ...totalRow, borderTop: '1px solid var(--line)', marginTop: 6, paddingTop: 8, fontWeight: 600, color: 'var(--text)' }}>
            <span>Total incl tax</span><span>{cur(totals.incl)}</span>
          </div>
        </div>
      </div>

      {/* Notes */}
      <div style={section}>
        <label style={fieldCol}>
          <span className="n-label">Notes</span>
          <textarea className="n-input" value={doc.notes || ''} disabled={doneState} onChange={(e) => onNotes(e.target.value)}
            placeholder="Notes on the received goods…" rows={2}
            style={{ width: '100%', minHeight: 40 }} />
        </label>
      </div>
      </>)}

      {/* Dojo banner: run status + every mismatch vs the stored baseline.
          Replaces suggestions/issues/receive in dojo mode. */}
      {dojo && (
        <div style={section}>
          <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: (doc.dojo_diffs?.length ?? 0) > 0 ? 8 : 0 }}>
            {(() => {
              const st = doc.dojo_status || 'new';
              const palette: Record<string, { tone: BadgeTone; label: string; note: string }> = {
                pass: { tone: 'ok', label: 'Pass', note: 'Matches the stored expected extraction' },
                fail: { tone: 'error', label: 'Fail', note: `${doc.dojo_diffs?.length ?? 0} mismatch${(doc.dojo_diffs?.length ?? 0) === 1 ? '' : 'es'} vs the expected extraction` },
                error: { tone: 'error', label: 'Error', note: 'The extraction run failed' },
                new: { tone: 'accent', label: 'No baseline', note: 'Review the extracted values, then save as expected' },
              };
              const p = palette[st] || palette.new;
              return (
                <>
                  <Badge tone={p.tone}>{p.label}</Badge>
                  <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{p.note}</span>
                </>
              );
            })()}
          </div>
          {(doc.dojo_diffs || []).map((d, i) => (
            <div key={`dj-${i}`} style={{ ...iconLine, padding: '2px 0', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>
              <Icon icon={X} size={14} style={iconNudge} />
              <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                {d.line != null ? `Line ${d.line}${d.description ? ` · ${d.description}` : ''}: ` : ''}
                {d.field === 'line_missing' ? 'missing from this run'
                  : d.field === 'line_extra' ? 'extra line not in the baseline'
                  : `${d.field} — expected ${JSON.stringify(d.expected ?? null)}, got ${JSON.stringify(d.actual ?? null)}`}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* The review: ONE list, one row per thing Norm noticed, each with the
          fix its Accept applies (invoiceReview.ts pairs a blocker with the
          suggestion that answers it). Notes with nothing to decide — a credit
          note — sit above it as plain sentences. */}
      {!dojo && viewMode === 'norm' && !draftDeleted
        && (review.rows.length > 0 || review.notes.length > 0 || reviewing || !reviewed || !doneState) && (
        <div style={{ ...section, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {review.notes.map((n) => (
            <div key={n.id} style={{ fontSize: 'var(--fs-sm)', color: doneState ? 'var(--muted)' : 'var(--warn)' }}>
              {n.message}
            </div>
          ))}
          {/* The list sits on the "needs you" tint while anything is open;
              once all of it is decided (or the invoice is received) it goes
              quiet and reads as the record. */}
          <div role="group" aria-label="Review" style={{
            display: 'flex', flexDirection: 'column', padding: '8px 12px', borderRadius: 'var(--radius)',
            background: openRows.length && !doneState ? 'var(--accent-soft)' : 'var(--surface)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: review.rows.length ? 4 : 0 }}>
              <div style={sectionTitle}>
                {review.rows.length === 0
                  ? (reviewing ? 'Reviewing against the attached invoice…' : reviewed ? 'Nothing to review — Norm checked it against the invoice' : 'Not yet reviewed.')
                  : openRows.length ? `To review (${openRows.length})` : 'Reviewed'}
              </div>
              {/* "Can't receive" was removed 19 Oct 2026 (owner): training
                  starts on its own when the supplier has no layout. */}
              {!doneState && openRows.length > 1 && (
                <div style={{ marginLeft: 'auto', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  <Button size="xs" onClick={() => { void acceptRows(openRows); }} disabled={acceptingRows}
                    title="apply every fix Norm suggests, including creating what Loaded is missing">
                    {acceptingRows ? 'Applying…' : `Accept all (${openRows.length})`}
                  </Button>
                </div>
              )}
            </div>
            {visibleRows.map(reviewRowView)}
            {review.deleteRow && review.rows.length > 1 && (
              <Button variant="link" onClick={() => setShowBehindDelete((v) => !v)}
                style={{ ...inlineLink, alignSelf: 'flex-start', marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                {showBehindDelete
                  ? 'Hide the rest'
                  : `Not ${review.deleteRow.issue?.code === 'duplicate_invoice' ? 'a duplicate' : 'right'}? Show the other ${review.rows.length - 1} thing${review.rows.length - 1 > 1 ? 's' : ''} Norm noticed`}
              </Button>
            )}
          </div>
          {effectiveActions.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', cursor: 'pointer', userSelect: 'none' }}>
                {[
                  recordCounts.norm ? `Norm applied ${recordCounts.norm} change${recordCounts.norm > 1 ? 's' : ''}` : null,
                  recordCounts.user ? `you applied ${recordCounts.user}` : null,
                  recordCounts.dismissed ? `dismissed ${recordCounts.dismissed}` : null,
                ].filter(Boolean).join(' · ') || 'action record'}
              </summary>
              <div style={{ marginTop: 4 }}>
                {actionsList.map((a, i) => (
                  <div key={`ar-${i}`} style={{ display: 'flex', gap: 8, padding: '2px 0', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                    <span style={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>{a.at ? a.at.replace('T', ' ').slice(0, 16) : '—'}</span>
                    <span style={{ whiteSpace: 'nowrap', fontWeight: 600, color: 'var(--text-soft)' }}>{a.by === 'norm' ? 'Norm' : 'you'} {a.action}</span>
                    <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{explanationFor(a.suggestion_id)}</span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {/* The one recovery control: re-analyse from scratch (confirmed —
          discards local edits and the accept record). */}
      {!dojo && !collapsed && !doneState && (
        <div style={{ ...section, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, paddingTop: 8, paddingBottom: 8 }}>
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            {reviewing ? 'Reviewing…' : studying ? 'Norm is studying this supplier…' : reviewed ? `Reviewed ${String(docLive.reviewed_at).replace('T', ' ').slice(0, 16)}` : 'Not yet reviewed'}
          </span>
          <Button size="sm" variant="quiet" icon={RefreshCw} onClick={() => { void reanalyse(); }} disabled={reviewing}
            title="rebuild this invoice from Loaded and the attached invoice and review it from scratch — resets your edits and accepted suggestions">
            {reviewing ? 'Running…' : 'Re-analyse'}
          </Button>
        </div>
      )}

      {/* Footer */}
      {!dojo && (
      <div style={{ ...section, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px 12px' }}>
        {/* Only what has nowhere else to appear. Every "you can't receive
            because..." line this used to carry is now said once, in its own
            words, on the row that owns it: Suggested changes, Blocked from
            auto receive, Worth knowing. Repeating the count down here made
            the card say the same thing three times and taught people to skim
            past the part that actually names the fix.

            What stays is the transient and the terminal — a failed receive,
            a receive in flight, a receive that happened. None of those has a
            section of its own. */}
        {(status === 'error' || doneState || status === 'saving') && (
          <span style={{ ...iconLine, flex: '1 1 220px', minWidth: 0, fontSize: 'var(--fs-sm)', fontWeight: 500, color: status === 'error' ? 'var(--error)' : doneState ? 'var(--ok)' : 'var(--muted)' }}>
            {status === 'error' ? (
              <><Icon icon={TriangleAlert} size={15} style={iconNudge} /><span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{message}</span></>
            ) : draftDeleted ? (
              <><Icon icon={Check} size={15} style={iconNudge} /><span>{`Draft deleted from Loaded${doc.deleted_reason ? ` — ${doc.deleted_reason}` : ' — this document was a supplier statement or duplicate.'}`}</span></>
            ) : doneState ? (
              <><Icon icon={Check} size={15} style={iconNudge} /><span>Received in Loaded.</span></>
            ) : 'Receiving…'}
          </span>
        )}
        {(overlay || !doneState) && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginLeft: 'auto' }}>
            {overlay && (
              <Button onClick={() => setExpandedFull(false)}>
                Close
              </Button>
            )}
            {!doneState && (
              <Button variant="primary" icon={Check} onClick={receive} disabled={receiveBlocked} title={receiveTitle}>
                {status === 'saving' ? 'Receiving…' : pendingSaves > 0 ? 'Saving…' : isCredit ? 'Accept & Receive credit' : 'Accept & Receive'}
              </Button>
            )}
          </div>
        )}
      </div>
      )}
    </fieldset>
    </div>
  );

  // The copy and the card side by side, half the width each — the dojo's
  // pane in the dojo's layout (Settings → Supplier specs).
  const copySrc =
    copyOpen && venueId && doc.file_id
      ? `/api/invoice-fixes/file?venue_id=${venueId}&file_id=${encodeURIComponent(doc.file_id)}`
      : null;
  const view = copySrc ? (
    <div ref={copyRowRef} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <div style={{ flex: '1 1 420px', minWidth: 320, ...(copySideBySide ? { position: 'sticky' as const, top: 8 } : {}) }}>
        <InvoicePdfPane src={copySrc} onClose={() => setCopyOpen(false)} onPopOut={() => openCopy()} />
      </div>
      <div style={{ flex: '1 1 420px', minWidth: 320 }}>{card}</div>
    </div>
  ) : card;

  if (!overlay) return view;
  return (
    <>
      {/* In-flow stub keeps the thread from jumping while the full editor is
          open in the overlay. */}
      <div style={{ border: '1px dashed var(--line-strong)', borderRadius: 'var(--radius-lg)', padding: '12px 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)', background: 'var(--surface)' }}>
        {doc.supplier_name || '—'} · {doc.reference_number || '(no number)'} — open in the expanded view
      </div>
      <div
        onClick={() => setExpandedFull(false)}
        style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(30,28,24,0.4)', overflowY: 'auto', padding: '32px 16px' }}>
        {/* Constrained to the card's width so clicks BESIDE the card hit the
            backdrop (a full-width block here used to swallow them — closing
            only worked below the card). */}
        <div onClick={(e) => e.stopPropagation()} style={{ maxWidth: copySrc ? 1800 : 1200, margin: '0 auto' }}>{view}</div>
      </div>
    </>
  );
}


