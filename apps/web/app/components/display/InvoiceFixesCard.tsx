'use client';

import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
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
  referenced_po?: string;
  copy_po?: string | null;
  line_id?: string;
  current_unit?: string;
  proposed_unit?: string;
}

interface FixInvoice {
  invoice_id: string;
  reference_number: string;
  supplier_name: string | null;
  linked_supplier_id: string | null;
  purchase_order_number: string | null;
  linked_purchase_order_id: string | null;
  issued_at: string | null;
  due_at: string | null;
  subtotal: number | null;
  tax_amount: number | null;
  total: number | null;
  lines: RawLine[];
  suggestions: Suggestion[];
  // The review's authoritative checklist, one char per check in CHECK_ORDER:
  // 'p' pass, 'f' fail, '-' not reached (an earlier check failed and the review
  // short-circuits). Compact so it fits the LLM slim cap. Absent on older
  // payloads.
  checks?: string;
}

interface ReviewCheck { key: string; result: 'pass' | 'fail' | 'skip' }

// Fixed check order + labels — must mirror CHECK_LABELS in
// config/consolidators/review_and_receive_invoices.py. The order decodes the
// packed `checks` string; the labels aren't sent, to keep the payload small.
const CHECK_ORDER: { key: string; label: string }[] = [
  { key: 'credit_note', label: 'Not a credit note' },
  { key: 'pdf_present', label: 'Invoice copy attached' },
  { key: 'po_linked', label: 'Linked to a purchase order' },
  { key: 'po_supplier', label: 'Supplier matches the purchase order' },
  { key: 'items_matched', label: 'Stock items, brands and units all exist in Loaded (no NEW)' },
  { key: 'totals', label: 'Invoice totals consistent' },
  { key: 'pdf_readable', label: 'Invoice copy readable' },
  { key: 'pdf_invoice_number', label: 'Invoice number matches the copy' },
  { key: 'pdf_lines', label: 'Lines match the invoice copy' },
  { key: 'unit_of_measure', label: 'Unit of measure matches the copy' },
  { key: 'pdf_total', label: 'Total matches the invoice copy' },
];

interface InvoiceStatus {
  is_received: boolean;
  received_at: string | null;
  reference_number: string | null;
  linked_purchase_order_id: string | null;
  linked_purchase_order_number: string | null;
  purchase_order_number: string | null;
}

interface Unit { id: string; name: string; type: string; ratio: number }
interface PO {
  id: string;
  order_number: string;
  supplier_name: string | null;
  supplier_id: string | null;
  created_at: string | null;
  linked_invoice_id: string | null;
  invoiced: boolean;
  received: boolean;
  status: string | null;
}

// "PO#1520987" — the number-only form shown in the collapsed box (like Loaded).
function poNumber(p: PO): string {
  return /^po/i.test(p.order_number) ? p.order_number : `PO#${p.order_number}`;
}

// "PO#1520987 - Akaroa Salmon - 5 July 2026" — the full form shown in the list.
function poLabel(p: PO): string {
  const parts = [poNumber(p)];
  if (p.supplier_name) parts.push(p.supplier_name);
  if (p.created_at) {
    const d = new Date(p.created_at);
    if (!Number.isNaN(d.getTime())) {
      parts.push(d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' }));
    }
  }
  return parts.join(' - ');
}

/**
 * Dropdown panel rendered in a portal, positioned against its anchor.
 *
 * The card clips its own content (rounded corners use `overflow: hidden`, and
 * the line table scrolls horizontally), so an absolutely-positioned panel gets
 * cut off at the card edge. Portalling to <body> with fixed positioning escapes
 * every clipping context. It also flips above the field when there isn't room
 * below, and tracks scroll/resize so it stays attached.
 */
function AnchoredPanel({
  anchorRef, panelRef, open, minWidth, maxWidth, children,
}: {
  anchorRef: React.RefObject<HTMLDivElement | null>;
  panelRef: React.RefObject<HTMLDivElement | null>;
  open: boolean;
  minWidth?: number;
  maxWidth?: number;
  children: React.ReactNode;
}) {
  const [style, setStyle] = useState<React.CSSProperties | null>(null);

  useEffect(() => {
    if (!open) return;
    const update = () => {
      const el = anchorRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const below = window.innerHeight - r.bottom - 8;
      const above = r.top - 8;
      const flipUp = below < 180 && above > below;
      setStyle({
        position: 'fixed',
        left: Math.max(8, Math.min(r.left, window.innerWidth - (maxWidth ?? r.width) - 8)),
        minWidth: Math.max(r.width, minWidth ?? 0),
        maxWidth,
        zIndex: 1000,
        ...(flipUp
          ? { bottom: window.innerHeight - r.top + 2, maxHeight: above }
          : { top: r.bottom + 2, maxHeight: below }),
      });
    };
    update();
    window.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    return () => {
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
      // Drop the stale position so the next open measures fresh rather than
      // painting one frame at wherever the field used to be.
      setStyle(null);
    };
  }, [open, anchorRef, minWidth, maxWidth]);

  if (!open || !style || typeof document === 'undefined') return null;
  return createPortal(
    <div
      ref={panelRef}
      style={{
        ...style, display: 'flex', flexDirection: 'column', overflow: 'hidden',
        background: '#fff', border: '1px solid #d1d5db', borderRadius: 6,
        boxShadow: '0 6px 18px rgba(0,0,0,0.12)',
      }}
    >
      {children}
    </div>,
    document.body,
  );
}

/** Close on any mousedown outside both the field and its portalled panel. */
function useCloseOnOutside(
  open: boolean,
  close: () => void,
  refs: React.RefObject<HTMLElement | null>[],
) {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (refs.some((r) => r.current?.contains(t))) return;
      close();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}

// Custom PO dropdown: collapsed box shows the PO number only; the open list
// shows the full "number - supplier - date" detail — mirrors Loaded's own
// Order Number picker (a native <select> can't differ box text from list text).
function PoDropdown({
  value, options, disabled, highlight, onChange,
}: {
  value: string;
  options: PO[];
  disabled: boolean;
  highlight: boolean;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  useCloseOnOutside(open, () => setOpen(false), [ref, panelRef]);

  const selected = options.find((p) => p.id === value);
  const boxText = selected ? poNumber(selected) : 'Not linked';

  return (
    <div ref={ref} style={{ position: 'relative', width: 220 }}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => !disabled && setOpen((o) => !o)}
        style={{
          ...inputStyle, width: '100%', textAlign: 'left', cursor: disabled ? 'default' : 'pointer',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6,
          color: selected ? '#1f2937' : '#9ca3af',
          borderColor: highlight ? '#b78a2f' : '#d1d5db',
          background: disabled ? '#f9fafb' : highlight ? '#fdf6e7' : '#fff',
        }}
      >
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{boxText}</span>
        <span style={{ color: '#9ca3af', fontSize: '0.7rem' }}>▾</span>
      </button>
      <AnchoredPanel anchorRef={ref} panelRef={panelRef} open={open} maxWidth={420}>
        <div style={{ overflowY: 'auto' }}>
          <Row label="Not linked" muted selected={!selected} onClick={() => { onChange(''); setOpen(false); }} />
          {options.map((p) => (
            <Row key={p.id} label={poLabel(p)} selected={p.id === value}
              onClick={() => { onChange(p.id); setOpen(false); }} />
          ))}
        </div>
      </AnchoredPanel>
    </div>
  );
}

function Row({ label, selected, muted, onClick }: {
  label: string; selected: boolean; muted?: boolean; onClick: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={onClick}
      style={{
        padding: '6px 10px', fontSize: '0.78rem', cursor: 'pointer', whiteSpace: 'nowrap',
        color: muted ? '#9ca3af' : '#1f2937',
        background: hover ? '#f3f4f6' : selected ? '#fdf6e7' : '#fff',
      }}
    >
      {label}
    </div>
  );
}

// Natural alphanumeric order: "2 Pack" before "10 Pack", case-insensitive.
// Compare a leading number as a real number — plain `numeric` collation
// compares digit runs separately and so mis-sorts decimals ("1.01" vs "1.1").
// Numbered units sort ahead of purely textual ones ("990 Grams" then "Gram").
const leadingNumber = (s: string) => {
  const m = /^\s*(\d+(?:\.\d+)?)/.exec(s);
  return m ? parseFloat(m[1]) : NaN;
};
const textCompare = (a: string, b: string) =>
  a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
const byName = (a: Unit, b: Unit) => {
  const na = leadingNumber(a.name);
  const nb = leadingNumber(b.name);
  const aNum = !Number.isNaN(na);
  const bNum = !Number.isNaN(nb);
  if (aNum && bNum) return na === nb ? textCompare(a.name, b.name) : na - nb;
  if (aNum !== bNum) return aNum ? -1 : 1;
  return textCompare(a.name, b.name);
};

// Searchable unit picker. Loaded has a long unit catalogue, so the list is
// sorted alphanumerically and filtered as you type.
function UnitDropdown({
  value, fallbackLabel, units, disabled, highlight, onChange,
}: {
  value: string | null;
  fallbackLabel: string;
  units: Unit[];
  disabled: boolean;
  highlight: boolean;
  onChange: (u: Unit | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useCloseOnOutside(open, () => setOpen(false), [ref, panelRef]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const sorted = useMemo(() => [...units].sort(byName), [units]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? sorted.filter((u) => u.name.toLowerCase().includes(q)) : sorted;
  }, [sorted, query]);

  const selected = units.find((u) => u.id === value);
  const boxText = selected?.name || fallbackLabel;

  const pick = (u: Unit | null) => { onChange(u); setOpen(false); };

  return (
    <div ref={ref} style={{ position: 'relative', minWidth: 130 }}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          if (disabled) return;
          if (!open) setQuery('');
          setOpen(!open);
        }}
        style={{
          ...inputStyle, width: '100%', textAlign: 'left', cursor: disabled ? 'default' : 'pointer',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6,
          color: selected ? '#1f2937' : '#9ca3af',
          borderColor: highlight ? '#b78a2f' : '#d1d5db',
          background: disabled ? '#f9fafb' : highlight ? '#fdf6e7' : '#fff',
        }}
      >
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{boxText}</span>
        <span style={{ color: '#9ca3af', fontSize: '0.7rem' }}>▾</span>
      </button>
      <AnchoredPanel anchorRef={ref} panelRef={panelRef} open={open} maxWidth={260}>
        <input
          ref={inputRef}
          value={query}
          placeholder="Search units…"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') { e.preventDefault(); setOpen(false); }
            if (e.key === 'Enter') { e.preventDefault(); if (filtered.length) pick(filtered[0]); }
          }}
          style={{
            ...inputStyle, width: '100%', border: 'none', borderBottom: '1px solid #eee',
            borderRadius: '6px 6px 0 0', flexShrink: 0,
          }}
        />
        <div style={{ overflowY: 'auto' }}>
          {filtered.length === 0 && (
            <div style={{ padding: '6px 10px', fontSize: '0.75rem', color: '#9ca3af' }}>No match</div>
          )}
          {filtered.map((u) => (
            <Row key={u.id} label={u.name} selected={u.id === value} onClick={() => pick(u)} />
          ))}
        </div>
      </AnchoredPanel>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: '4px 8px', border: '1px solid #d1d5db', borderRadius: 4,
  fontSize: '0.8rem', fontFamily: 'inherit', boxSizing: 'border-box', outline: 'none',
};
function CheckRow({ check, indent }: { check: Check; indent?: boolean }) {
  const [symbol, colour] =
    check.state === 'pass' ? ['✓', '#2e7d4f']
      : check.state === 'fail' ? ['✗', '#c0392b']
        : ['—', '#9ca3af'];
  return (
    <div style={{
      fontSize: '0.64rem', color: colour, paddingLeft: indent ? '0.75rem' : 0,
      display: 'flex', gap: 6,
    }}>
      <span style={{ width: 8 }}>{symbol}</span>
      <span>
        {check.label}
        {check.detail && <span style={{ color: '#8a8a8a' }}> — {check.detail}</span>}
      </span>
    </div>
  );
}

const cur = (n: number | null | undefined) => `$${(n ?? 0).toFixed(2)}`;
// Money/quantity comparison tolerant of float representation, not of real
// differences — half a cent is well below any genuine discrepancy.
const near = (a: number | null | undefined, b: number | null | undefined) =>
  Math.abs((a ?? 0) - (b ?? 0)) < 0.005;
const microLabel: React.CSSProperties = {
  fontSize: '0.6rem', color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.04em',
};

// Local editable state for one invoice's lines, keyed by line id.
interface LineEdit { unit_id: string | null; unit_name: string | null; qty: number; cost: number }

type CheckState = 'pass' | 'fail' | 'skip';
interface Check { label: string; state: CheckState; detail?: string }
interface LineChecks { lineId: string; description: string; checks: Check[] }

// Unit-of-measure parsing — mirrors app/services/invoice_units.py so the card
// resolves a recommended/current unit to a real Loaded unit by magnitude
// (e.g. "100 piece" ≡ Loaded "100 Pack", "Kilo" ≡ "1kg"), not just exact name.
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
const UOM_VAGUE = new Set(['pkt', 'packet', 'box', 'carton', 'ctn', 'outer', 'unit', 'case', 'cs', 'bx', 'un']);

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

// Find the Loaded unit that best matches `name` — exact name first, then
// guideline-magnitude equivalence. Returns undefined if none is confident.
function resolveUnit(name: string | null | undefined, units: Unit[]): Unit | undefined {
  if (!name) return undefined;
  const lc = name.trim().toLowerCase();
  const exact = units.find((u) => u.name.toLowerCase() === lc);
  if (exact) return exact;
  const target = parseUnit(name);
  if (!target) return undefined;
  return units.find((u) => {
    const pu = parseUnit(u.name);
    return pu && pu[0] === target[0] && Math.abs(pu[1] - target[1]) < 0.001;
  });
}

export default function InvoiceFixesCard({ data, props }: DisplayBlockProps) {
  const invoices = useMemo(() => (data.fix_invoices as FixInvoice[]) || [], [data.fix_invoices]);
  const venueId =
    (props?.activeVenueId as string) || (data.venue_id as string) || undefined;
  const autoSubmit = !!data.auto_submit; // autopilot mode

  const [units, setUnits] = useState<Unit[]>([]);
  const [pos, setPos] = useState<PO[]>([]);
  // Live state from Loaded. The thread only stores the consolidator's snapshot,
  // so on reload we ask the system of record which invoices are already
  // received rather than showing every card as still outstanding.
  const [statuses, setStatuses] = useState<Record<string, InvoiceStatus>>({});
  const [statusLoaded, setStatusLoaded] = useState(false);

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

  const invoiceIds = useMemo(() => invoices.map((i) => i.invoice_id).join(','), [invoices]);
  useEffect(() => {
    if (!venueId || !invoiceIds) return;
    apiFetch('/api/invoice-fixes/status', {
      method: 'POST',
      body: JSON.stringify({ venue_id: venueId, invoice_ids: invoiceIds.split(',') }),
    })
      .then((r) => (r.ok ? r.json() : { statuses: {} }))
      .then((d) => setStatuses(d.statuses || {}))
      .catch(() => {})
      .finally(() => setStatusLoaded(true));
  }, [venueId, invoiceIds]);

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
          autoSubmit={autoSubmit}
          live={statuses[inv.invoice_id]}
          statusLoaded={statusLoaded}
        />
      ))}
    </div>
  );
}

function ReceiveInvoiceView({
  inv, units, pos, venueId, autoSubmit, live, statusLoaded,
}: {
  inv: FixInvoice; units: Unit[]; pos: PO[]; venueId?: string; autoSubmit: boolean;
  live?: InvoiceStatus; statusLoaded: boolean;
}) {
  const alreadyReceived = !!live?.is_received;
  // Loaded's PO list only contains *open* orders, so a PO that's already been
  // consumed by this invoice won't be in `pos`. Trust the live link instead of
  // trying to re-derive it from the candidate list.
  const liveLinkedPoId = live?.linked_purchase_order_id || null;
  // Linked per Loaded now, or per the consolidator's snapshot.
  const linkedAlready = !!(liveLinkedPoId || inv.linked_purchase_order_id);
  // Suggested unit per line id (from unit suggestions).
  const suggestedUnit = useMemo(() => {
    const m: Record<string, string> = {};
    for (const s of inv.suggestions) if (s.type === 'unit' && s.line_id && s.proposed_unit) m[s.line_id] = s.proposed_unit;
    return m;
  }, [inv.suggestions]);
  const poSuggestion = inv.suggestions.find((s) => s.type === 'link_po')?.po_number;

  // Candidate POs — copy Loaded's own receive screen: only this invoice's
  // supplier, and only POs not already invoiced or linked to another invoice.
  const poKey = (s: string) => s.replace(/\W/g, '').toLowerCase().replace(/^po/, '');
  const candidatePos = useMemo(() => {
    const open = pos.filter(
      (p) =>
        p.supplier_id === inv.linked_supplier_id &&
        !p.invoiced &&
        (!p.linked_invoice_id || p.linked_invoice_id === inv.invoice_id),
    );
    // A PO already consumed by this invoice is absent from Loaded's open-PO
    // list, so add it back — otherwise the field can't name what it's linked
    // to and wrongly reads "Not linked".
    if (liveLinkedPoId && !open.some((p) => p.id === liveLinkedPoId)) {
      open.unshift({
        id: liveLinkedPoId,
        order_number: live?.linked_purchase_order_number || '(linked)',
        supplier_name: inv.supplier_name,
        supplier_id: inv.linked_supplier_id,
        created_at: null,
        linked_invoice_id: inv.invoice_id,
        invoiced: false,
        received: true,
        status: 'Received',
      });
    }
    return open;
  }, [
    pos, inv.linked_supplier_id, inv.invoice_id, inv.supplier_name,
    liveLinkedPoId, live?.linked_purchase_order_number,
  ]);
  // The candidate PO to pre-select. The consolidator has already read the
  // buyer's PO off the copy and put it in the suggestion's po_number, so this
  // matches instantly (no per-card extraction). `referenced_po` is Loaded's
  // own field, shown only if it differs (often the supplier's order number).
  const referencedPo = inv.suggestions.find((s) => s.type === 'link_po')?.referenced_po;
  const suggestedPo = useMemo(() => {
    const tryMatch = (n: string | null | undefined) =>
      n ? candidatePos.find((p) => poKey(p.order_number) === poKey(n)) : undefined;
    return tryMatch(poSuggestion);
  }, [candidatePos, poSuggestion]);

  // The Loaded unit each line should show pre-selected: the resolved
  // recommendation when we're suggesting a change, otherwise the line's
  // current unit resolved against the Loaded catalog. Every line (suggested
  // or not) gets a real, editable Loaded unit selected.
  const resolvedFor = useMemo(() => {
    const m: Record<string, { current?: Unit; recommended?: Unit }> = {};
    for (const ln of inv.lines) {
      const current =
        units.find((u) => u.id === ln.linked_unit_id) || resolveUnit(ln.unit, units);
      const rec = suggestedUnit[ln.id];
      const recommended = rec ? resolveUnit(rec, units) : undefined;
      m[ln.id] = { current, recommended };
    }
    return m;
  }, [inv.lines, suggestedUnit, units]);

  const [lineEdits, setLineEdits] = useState<Record<string, LineEdit>>({});
  useEffect(() => {
    if (units.length === 0) return; // wait for the catalog before pre-selecting
    const init: Record<string, LineEdit> = {};
    for (const ln of inv.lines) {
      const r = resolvedFor[ln.id];
      const pick = r?.recommended ?? r?.current;
      init[ln.id] = {
        unit_id: pick?.id ?? ln.linked_unit_id,
        unit_name: pick?.name ?? ln.unit,
        qty: ln.quantity_received ?? 0,
        cost: ln.unit_cost ?? 0,
      };
    }
    setLineEdits(init);
  }, [inv.lines, resolvedFor, units.length]);

  // Selected PO id: whatever Loaded actually has linked (authoritative, and
  // survives reload), else the already-linked PO from the snapshot, else the
  // suggestion once the candidate list has loaded.
  const [poId, setPoId] = useState<string>(inv.linked_purchase_order_id || '');
  useEffect(() => {
    if (liveLinkedPoId) { setPoId(liveLinkedPoId); return; }
    if (!poId && suggestedPo) setPoId(suggestedPo.id);
  }, [liveLinkedPoId, suggestedPo, poId]);
  const [status, setStatus] = useState<'idle' | 'saving' | 'done' | 'error'>('idle');
  const [message, setMessage] = useState<string>('');

  // ── Validation ──────────────────────────────────────────────────────────
  // The same checks the review runs, but against the card's CURRENT state so
  // they clear as you fix things. The review's own reasons are a snapshot from
  // when it ran and would never clear, so they can't gate the button.
  //
  // The review evaluates its gates in short-circuiting layers: if an invoice
  // fails an early one (e.g. no PO linked) it stops before the line-level copy
  // comparison, so the payload carries no copy figures at all. That is "not
  // compared", NOT "line missing from the copy" — treating it as a failure
  // wrongly flags every line. Detect it and report those checks as skipped.
  const copyCompared = useMemo(
    () =>
      inv.lines.some(
        (l) => l.copy_unit != null || l.copy_quantity != null || l.copy_unit_price != null,
      ),
    [inv.lines],
  );

  const validation = useMemo(() => {
    const invoiceChecks: Check[] = [];
    const ref = referencedPo || poSuggestion;
    invoiceChecks.push(
      !linkedAlready && !poId
        ? {
            label: 'Purchase order linked',
            state: 'fail',
            detail: ref ? `the invoice references ${ref}` : undefined,
          }
        : { label: 'Purchase order linked', state: 'pass' },
    );

    const lines: LineChecks[] = [];
    for (const ln of inv.lines) {
      const e = lineEdits[ln.id];
      if (!e) continue; // edits not initialised yet
      const hasCopy =
        ln.copy_unit != null || ln.copy_quantity != null || ln.copy_unit_price != null;
      const checks: Check[] = [];

      checks.push(
        !copyCompared
          ? { label: 'On the invoice copy', state: 'skip', detail: 'copy not compared' }
          : hasCopy
            ? { label: 'On the invoice copy', state: 'pass' }
            : { label: 'On the invoice copy', state: 'fail', detail: 'no matching copy line' },
      );

      const rec = resolvedFor[ln.id]?.recommended;
      if (!e.unit_id) {
        checks.push({ label: 'Unit', state: 'fail', detail: 'no Loaded unit selected' });
      } else if (copyCompared && hasCopy && rec && e.unit_id !== rec.id) {
        checks.push({
          label: 'Unit',
          state: 'fail',
          detail: `should be ${rec.name} to match the copy (${ln.copy_unit})`,
        });
      } else if (copyCompared && hasCopy) {
        checks.push({ label: 'Unit', state: 'pass' });
      } else {
        checks.push({ label: 'Unit', state: 'skip', detail: 'copy not compared' });
      }

      checks.push(
        ln.copy_quantity == null
          ? { label: 'Quantity', state: 'skip', detail: 'copy not compared' }
          : near(e.qty, ln.copy_quantity)
            ? { label: 'Quantity', state: 'pass' }
            : {
                label: 'Quantity',
                state: 'fail',
                detail: `${e.qty} vs copy ${ln.copy_quantity}`,
              },
      );

      checks.push(
        ln.copy_unit_price == null
          ? { label: 'Unit cost', state: 'skip', detail: 'copy not compared' }
          : near(e.cost, ln.copy_unit_price)
            ? { label: 'Unit cost', state: 'pass' }
            : {
                label: 'Unit cost',
                state: 'fail',
                detail: `${cur(e.cost)} vs copy ${cur(ln.copy_unit_price)}`,
              },
      );

      lines.push({ lineId: ln.id, description: ln.description || ln.code || 'Line', checks });
    }
    return { invoiceChecks, lines, copyCompared };
  }, [
    inv.lines, lineEdits, resolvedFor, linkedAlready, poId, referencedPo, poSuggestion,
    copyCompared,
  ]);

  // The review's authoritative checklist from the payload (what it actually
  // found), decoded from the packed string, separate from the live
  // re-derivation above.
  const reviewChecks = useMemo<ReviewCheck[]>(() => {
    const packed = inv.checks;
    if (!packed) return [];
    return CHECK_ORDER.map((c, i) => ({
      key: c.key,
      result: packed[i] === 'p' ? 'pass' : packed[i] === 'f' ? 'fail' : 'skip',
    }));
  }, [inv.checks]);
  const reviewSummary = useMemo(() => {
    if (reviewChecks.length === 0) return 'no review checks';
    const pass = reviewChecks.filter((c) => c.result === 'pass').length;
    const fail = reviewChecks.filter((c) => c.result === 'fail').length;
    const skip = reviewChecks.filter((c) => c.result === 'skip').length;
    if (fail === 0 && skip === 0) return 'all checks pass';
    const parts = [`${pass} passed`];
    if (fail) parts.push(`${fail} failed`);
    if (skip) parts.push(`${skip} not reached`);
    return parts.join(' · ');
  }, [reviewChecks]);

  const issues = useMemo(() => {
    const out: { lineId?: string; message: string }[] = [];
    for (const c of validation.invoiceChecks) {
      if (c.state === 'fail') {
        out.push({ message: c.detail ? `${c.label} — ${c.detail}` : c.label });
      }
    }
    for (const l of validation.lines) {
      for (const c of l.checks) {
        if (c.state === 'fail') {
          out.push({ lineId: l.lineId, message: c.detail ? `${c.label}: ${c.detail}` : c.label });
        }
      }
    }
    return out;
  }, [validation]);
  const issuesByLine = useMemo(() => {
    const m: Record<string, string[]> = {};
    for (const i of issues) if (i.lineId) (m[i.lineId] ||= []).push(i.message);
    return m;
  }, [issues]);
  const invoiceIssues = useMemo(() => issues.filter((i) => !i.lineId), [issues]);
  // Don't gate on validation until the line edits have initialised, or the
  // button would flicker disabled on first paint.
  const editsReady = Object.keys(lineEdits).length > 0;
  const blocked = editsReady && issues.length > 0;

  // View the attached supplier copy. Fetch with auth (apiFetch adds the bearer
  // token), then open the blob — a raw link can't carry the auth header.
  const [copyBusy, setCopyBusy] = useState(false);
  const openCopy = async () => {
    if (copyBusy || !venueId) return;
    setCopyBusy(true);
    try {
      const r = await apiFetch(
        `/api/invoice-fixes/file?venue_id=${venueId}&invoice_id=${inv.invoice_id}`,
      );
      if (!r.ok) throw new Error(r.status === 404 ? 'No copy attached' : `Error ${r.status}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener');
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Could not open copy');
      setStatus('error');
    } finally {
      setCopyBusy(false);
    }
  };

  const setLine = (id: string, patch: Partial<LineEdit>) =>
    setLineEdits((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));

  const total = inv.lines.reduce((s, ln) => {
    const e = lineEdits[ln.id];
    return s + (e ? e.qty * e.cost : ln.total_cost ?? 0);
  }, 0);

  const accept = async (overridePoId?: string | null) => {
    setStatus('saving');
    setMessage('');
    try {
      // Resolve the PO to link from the freshest source — autopilot can fire
      // before the poId state effect has committed, so fall back to the
      // matched candidate rather than a stale empty poId.
      const linkedPoId =
        overridePoId ??
        (poId || liveLinkedPoId || suggestedPo?.id || inv.linked_purchase_order_id || null);
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
      const res = await apiFetch('/api/invoice-fixes/receive', {
        method: 'POST',
        body: JSON.stringify({
          venue_id: venueId,
          invoice_id: inv.invoice_id,
          linked_purchase_order_id: linkedPoId,
          po_number: null,
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

  // Received either just now (local) or already, per Loaded (survives reload).
  const done = status === 'done' || alreadyReceived;

  // Autopilot: auto-apply only when every suggested change is confidently
  // resolved — the referenced PO matched a real Loaded PO, and every suggested
  // unit resolved to a real Loaded unit and is the one selected. Anything
  // ambiguous stays a manual card (autopilot never guesses on a real write).
  const confident = useMemo(() => {
    if (poSuggestion && !linkedAlready && !suggestedPo) return false;
    for (const [lineId] of Object.entries(suggestedUnit)) {
      const rec = resolvedFor[lineId]?.recommended;
      if (!rec || lineEdits[lineId]?.unit_id !== rec.id) return false;
    }
    return true;
  }, [poSuggestion, linkedAlready, suggestedPo, suggestedUnit, resolvedFor, lineEdits]);

  const autoFiredRef = useState({ fired: false })[0];
  useEffect(() => {
    // Wait for the live status before auto-firing, and never re-receive an
    // invoice Loaded already has as received (e.g. after a page reload).
    if (
      autoSubmit && confident && status === 'idle' &&
      statusLoaded && !alreadyReceived && !blocked &&
      units.length > 0 && Object.keys(lineEdits).length > 0 && !autoFiredRef.fired
    ) {
      autoFiredRef.fired = true;
      // Pass the matched PO explicitly — poId state may not have committed yet.
      accept(liveLinkedPoId || suggestedPo?.id || inv.linked_purchase_order_id || null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSubmit, confident, status, statusLoaded, alreadyReceived, blocked, units.length, lineEdits]);

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,0.04)', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ padding: '0.7rem 0.9rem', background: 'linear-gradient(#faf9f7,#f5f3ef)', borderBottom: '1px solid #eee' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span style={{ fontSize: '0.9rem', fontWeight: 600, color: '#3a3a3a', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            Receive Invoice · {inv.reference_number}
            <button
              type="button"
              onClick={openCopy}
              disabled={copyBusy}
              title="View invoice copy"
              aria-label="View invoice copy"
              style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 22, height: 22, padding: 0, border: '1px solid #d8d4cc', borderRadius: 4,
                background: '#fff', color: '#6b6b6b', cursor: copyBusy ? 'default' : 'pointer',
                opacity: copyBusy ? 0.5 : 1,
              }}
            >
              {/* document icon */}
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
            </button>
          </span>
          <span style={{ fontSize: '0.8rem', color: '#666' }}>{inv.supplier_name}</span>
        </div>
        <div style={{ display: 'flex', gap: '1.5rem', marginTop: '0.55rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <span style={microLabel}>
              Order Number (PO)
              {suggestedPo && !linkedAlready ? ' · suggested' : ''}
            </span>
            <PoDropdown
              value={poId}
              options={candidatePos}
              disabled={done}
              highlight={!!suggestedPo && poId === suggestedPo.id && !linkedAlready}
              onChange={setPoId}
            />
            {/* Only prompt for a PO when one genuinely isn't linked — an
                invoice already linked in Loaded must not read as unlinked. */}
            {!linkedAlready && (
              <span style={{ fontSize: '0.6rem', color: suggestedPo ? '#b78a2f' : '#c0392b', maxWidth: 240 }}>
                {suggestedPo
                  ? `matched to PO ${suggestedPo.order_number}${referencedPo && poKey(referencedPo) !== poKey(suggestedPo.order_number) ? ' (from the invoice copy; Loaded had ' + referencedPo + ')' : ''}`
                  : `references ${referencedPo || poSuggestion} — no matching PO for ${inv.supplier_name}; pick one`}
              </span>
            )}
          </label>
          <div style={{ display: 'flex', gap: '1.75rem', marginLeft: 'auto', alignItems: 'flex-end' }}>
            {inv.issued_at && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3, textAlign: 'right' }}>
                <span style={microLabel}>Invoice date</span>
                <span style={{ fontSize: '0.8rem', color: '#555' }}>{inv.issued_at}</span>
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3, textAlign: 'right' }}>
              <span style={microLabel}>Invoice total</span>
              <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>{cur(total)}</span>
            </div>
          </div>
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
              const r = resolvedFor[ln.id];
              const suggested = suggestedUnit[ln.id];
              const recommended = r?.recommended;
              // Highlight while the field still reflects the recommendation.
              const showRec = !!suggested;
              const highlight = showRec && (!e?.unit_id || e.unit_id === recommended?.id);
              const lineIssues = issuesByLine[ln.id] || [];
              return (
                <Fragment key={ln.id}>
                <tr style={{ borderTop: '1px solid #f3f3f3', background: lineIssues.length && !done ? '#fdf5f4' : undefined }}>
                  <td style={{ padding: '0.4rem 0.6rem', color: '#666' }}>{ln.code || '—'}</td>
                  <td style={{ padding: '0.4rem 0.6rem' }}>{ln.description}</td>
                  <td style={{ padding: '0.4rem 0.6rem' }}>
                    <UnitDropdown
                      value={e?.unit_id ?? null}
                      // Covers both "nothing picked yet" and a unit that isn't
                      // in Loaded's catalogue — show what the line already has.
                      fallbackLabel={e?.unit_name || ln.unit || 'Select unit'}
                      units={units}
                      disabled={done}
                      highlight={highlight}
                      onChange={(u) =>
                        setLine(ln.id, { unit_id: u?.id ?? null, unit_name: u?.name ?? null })
                      }
                    />
                    {showRec && (
                      <div style={{ fontSize: '0.6rem', color: '#b78a2f', marginTop: 2 }}>
                        was {ln.unit} · copy {ln.copy_unit}
                        {recommended
                          ? ` · recommends ${suggested} → ${recommended.name}`
                          : ` · recommends ${suggested} (no exact Loaded unit — pick one)`}
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
                {lineIssues.length > 0 && !done && (
                  <tr style={{ background: '#fdf5f4' }}>
                    <td colSpan={6} style={{ padding: '0 0.6rem 0.45rem 0.6rem' }}>
                      {lineIssues.map((m, i) => (
                        <div key={i} style={{ fontSize: '0.62rem', color: '#c0392b' }}>✗ {m}</div>
                      ))}
                    </td>
                  </tr>
                )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Invoice-level validation (line-level issues render under their line) */}
      {!done && invoiceIssues.length > 0 && (
        <div style={{ padding: '0.5rem 0.9rem', background: '#fdf5f4', borderTop: '1px solid #f3e0dd' }}>
          {invoiceIssues.map((i, n) => (
            <div key={n} style={{ fontSize: '0.68rem', color: '#c0392b' }}>✗ {i.message}</div>
          ))}
        </div>
      )}

      {/* Full validation, on demand */}
      <details style={{ borderTop: '1px solid #eee' }}>
        <summary style={{
          padding: '0.45rem 0.9rem', fontSize: '0.68rem', color: '#666',
          cursor: 'pointer', userSelect: 'none', listStyle: 'revert',
        }}>
          Validation ({reviewSummary})
        </summary>
        <div style={{ padding: '0 0.9rem 0.6rem 0.9rem' }}>
          {/* The review's full checklist — every check it ran, straight from
              the last review, so all the invoice-level checks are visible. */}
          {reviewChecks.length > 0 ? (
            <>
              <div style={{ fontSize: '0.62rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.03em', marginBottom: 3 }}>
                Review checks
              </div>
              {reviewChecks.map((c) => (
                <CheckRow key={c.key} check={{
                  label: CHECK_ORDER.find((o) => o.key === c.key)?.label || c.key,
                  state: c.result,
                  detail: c.result === 'skip' ? 'not reached' : undefined,
                }} />
              ))}
              {reviewChecks.some((c) => c.result === 'skip') && (
                <div style={{ fontSize: '0.6rem', color: '#9a7b3a', marginTop: 3 }}>
                  “—” checks didn’t run: the review stops at the first failure, so later checks aren’t reached.
                </div>
              )}
            </>
          ) : (
            <div style={{ fontSize: '0.64rem', color: '#9ca3af' }}>
              No review checks on this card (produced before checks were recorded).
            </div>
          )}

          {/* Live re-check of the editable fields — clears as you fix things. */}
          <div style={{ fontSize: '0.62rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.03em', margin: '0.5rem 0 3px' }}>
            Current edits
          </div>
          {validation.invoiceChecks.map((c, i) => <CheckRow key={`inv-${i}`} check={c} />)}
          {validation.lines.map((l) => (
            <div key={l.lineId} style={{ marginTop: '0.4rem' }}>
              <div style={{ fontSize: '0.64rem', fontWeight: 600, color: '#444' }}>{l.description}</div>
              {l.checks.map((c, i) => <CheckRow key={i} check={c} indent />)}
            </div>
          ))}
        </div>
      </details>

      {/* Footer */}
      <div style={{ padding: '0.6rem 0.9rem', borderTop: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.75rem' }}>
        <span style={{ fontSize: '0.72rem', color: status === 'error' || blocked ? '#c0392b' : done ? '#2e7d4f' : '#888' }}>
          {status === 'error'
            ? `✗ ${message}`
            : done
              ? (status === 'done'
                  ? `✓ Received${autoSubmit ? ' (autopilot)' : ''} — re-run the review to refresh the list.`
                  : '✓ Received — already receipted in Loaded.')
              : status === 'saving'
                ? 'Applying…'
                : blocked
                  ? `${issues.length} issue${issues.length === 1 ? '' : 's'} to resolve before this invoice can be received.`
                  : autoSubmit
                    ? (confident
                        ? 'Autopilot: applying automatically…'
                        : 'Autopilot couldn’t auto-apply this one — review and accept.')
                    : 'Review the changes, then accept to update Loaded and receive.'}
        </span>
        {!done && (
          <button
            onClick={() => accept()}
            disabled={status === 'saving' || blocked}
            title={blocked ? 'Resolve the issues above before receiving' : undefined}
            style={{ padding: '0.4rem 1.1rem', fontSize: '0.78rem', fontWeight: 500, border: 'none', borderRadius: 6,
              cursor: status === 'saving' || blocked ? 'not-allowed' : 'pointer',
              background: blocked ? '#c7c7c7' : '#2e7d4f', color: '#fff',
              fontFamily: 'inherit', opacity: status === 'saving' ? 0.6 : 1, whiteSpace: 'nowrap' }}>
            {status === 'saving' ? 'Receiving…' : 'Accept & Receive'}
          </button>
        )}
      </div>
    </div>
  );
}
