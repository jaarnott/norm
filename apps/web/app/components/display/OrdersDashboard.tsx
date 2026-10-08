'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { ChevronDown, ChevronRight, LoaderCircle, Plus } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch, callComponentApi } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import { formatMoney } from '../../lib/format';
import PurchaseOrderEditor from './PurchaseOrderEditor';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import BackLink from '../ui/BackLink';
import Icon from '../ui/Icon';
import VenueSelect from '../ui/VenueSelect';

interface OrderSummary {
  id: string;
  orderNumber: string;
  supplierName: string;
  orderedBy: string;
  status: string;
  createdAt: string;
  subtotal: number;
  tax: number;
  total: number;
  isReceived: boolean;
}

interface OrderLine {
  itemName: string;
  unitName: string;
  quantityOrdered: number;
  quantityReceived: number;
  unitCost: number;
}

interface VenueOption { id: string; name: string }

function extractOrders(data: Record<string, unknown>): OrderSummary[] {
  if (Array.isArray(data)) return data as OrderSummary[];
  if (Array.isArray((data as Record<string, unknown>).data)) return (data as Record<string, unknown>).data as OrderSummary[];
  for (const key of Object.keys(data || {})) {
    if (key === 'working_document_id') continue;
    if (Array.isArray(data[key])) return data[key] as OrderSummary[];
  }
  return [];
}

function formatCurrency(n: number): string {
  return formatMoney(n);
}

/** "7 Oct" — with the year only when it isn't this year. */
function formatDay(d: Date, withYear = d.getFullYear() !== new Date().getFullYear()): string {
  return d.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short', ...(withYear ? { year: 'numeric' } : {}) });
}

/** "Wed 7 Oct, 2:38 pm" — an order's date in the table. */
function formatDate(iso: string): string {
  const d = new Date(iso);
  const weekday = d.toLocaleDateString('en-NZ', { weekday: 'short' });
  const time = d.toLocaleTimeString('en-NZ', { hour: 'numeric', minute: '2-digit', hour12: true });
  return `${weekday} ${formatDay(d)}, ${time}`;
}

/** "Wed 7 Oct" — an order's date on a phone card. */
function formatShortDate(iso: string): string {
  const d = new Date(iso);
  return `${d.toLocaleDateString('en-NZ', { weekday: 'short' })} ${formatDay(d)}`;
}

/** "25 Sept – 7 Oct": the days the listed orders span, for the header's meta line. */
function dateSpan(orders: OrderSummary[]): string {
  const times = orders.map(o => new Date(o.createdAt).getTime()).filter(t => !Number.isNaN(t));
  if (times.length === 0) return '';
  const first = new Date(Math.min(...times));
  const last = new Date(Math.max(...times));
  const thisYear = new Date().getFullYear();
  const withYear = first.getFullYear() !== thisYear || last.getFullYear() !== thisYear;
  const from = formatDay(first, withYear);
  const to = formatDay(last, withYear);
  return from === to ? from : `${from} – ${to}`;
}

/** Badge tone by what the status means to the venue: outstanding is still
 *  waiting on someone, sent is on its way, acknowledged/received is settled. */
function statusTone(status: string): BadgeTone {
  const s = (status || '').toLowerCase();
  if (s === 'outstanding' || s === 'pending') return 'warn';
  if (s === 'sent' || s === 'draft') return 'info';
  if (s === 'acknowledged' || s === 'received' || s === 'complete' || s === 'completed' || s === 'invoiced') return 'ok';
  return 'neutral';
}

/** Sentence case for a status as Loaded spells it ("PartiallyReceived" → "Partially received"). */
function statusLabel(status: string): string {
  const words = (status || '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const ELLIPSIS: React.CSSProperties = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };

// The date cell holds the row's expand toggle, so the row works from the
// keyboard too. It has no handler of its own: its click bubbles to the row.
// Block-level flex: inline-flex took its baseline from the chevron and sat
// the date 2px above the rest of the row.
const ROW_TOGGLE: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 2, width: '100%', padding: 0, border: 'none',
  background: 'transparent', font: 'inherit', color: 'inherit', textAlign: 'left', whiteSpace: 'nowrap', cursor: 'pointer',
};

// Nested lines table: it sits inside .n-table, so its cells override that
// table's padding (the header fill is the same --surface-alt as the panel).
const LINE_TH: React.CSSProperties = { padding: '6px 12px' };
const LINE_TD: React.CSSProperties = { padding: '7px 12px' };

// Phone cards: what sits inside a .n-card's 1px border rounds to match it.
const INNER_RADIUS = 'calc(var(--radius-lg) - 1px)';

// Below this width the table's fixed columns leave Supplier too little room.
const CARDS_BELOW = 700;

export default function OrdersDashboard({ data, props }: DisplayBlockProps) {
  // Venue selector — same pattern as RosterEditor
  // Shared page venue — only honoured when this dashboard is a PAGE instance
  // (persistVenue), never when it's embedded in a conversation.
  const persistVenue = !!props?.persistVenue;
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [selectedVenue, setSelectedVenue] = useState<string | null>((props?.activeVenueId as string) || rememberedVenue || null);
  const [creating, setCreating] = useState(false);
  const [orders, setOrders] = useState<OrderSummary[]>(() => extractOrders(data));
  const [loading, setLoading] = useState(false);

  // Cards instead of the table on phones, and wherever the pane is too narrow
  // for the table's columns — an iPad in portrait with the menu open leaves
  // this page about 350px, though the viewport is not a phone's.
  const { isMobile } = useBreakpoint();
  const [narrow, setNarrow] = useState(false);
  const observeWidth = useCallback((el: HTMLDivElement | null) => {
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(entries => setNarrow(entries[0].contentRect.width < CARDS_BELOW));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detailLines, setDetailLines] = useState<OrderLine[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Update from props data if provided (from FunctionalPage load)
  useEffect(() => {
    const extracted = extractOrders(data);
    if (extracted.length > 0) {
      setOrders(extracted);
    }
  }, [data]);

  // Fetch venues and auto-select first
  useEffect(() => {
    apiFetch('/api/venues')
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.venues?.length > 0) {
          setVenues(d.venues);
          if (!selectedVenue) {
            // Prefer the venue the user last picked (if still accessible),
            // else the first.
            const remembered = rememberedVenue && d.venues.some((v: VenueOption) => v.id === rememberedVenue) ? rememberedVenue : null;
            setSelectedVenue(remembered || d.venues[0].id);
          }
        }
      })
      .catch(() => {});
  }, []);

  // Load orders when venue changes
  const loadOrders = useCallback(async (venueId: string) => {
    setLoading(true);
    try {
      const result = await callComponentApi('orders_dashboard', 'get_orders_summary', {}, venueId);
      const extracted = extractOrders(result.data as Record<string, unknown>);
      setOrders(extracted);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  // Auto-load when venue is selected (and no data from props)
  useEffect(() => {
    if (selectedVenue && orders.length === 0) {
      loadOrders(selectedVenue);
    }
  }, [selectedVenue]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleVenueChange = useCallback((venueId: string) => {
    setSelectedVenue(venueId);
    if (persistVenue) setActiveVenue(venueId);
    setExpandedId(null);
    loadOrders(venueId);
  }, [persistVenue, loadOrders, setActiveVenue]);

  const sortedOrders = useMemo(() =>
    [...orders].sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()),
  [orders]);

  const toggleRow = useCallback(async (order: OrderSummary) => {
    if (expandedId === order.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(order.id);
    setDetailLines([]);
    setDetailError(null);
    setDetailLoading(true);
    try {
      const result = await callComponentApi('orders_dashboard', 'get_order_detail', { order_id: order.id }, selectedVenue || undefined);
      const d = (result.data || {}) as Record<string, unknown>;
      const lines = (d.lines || []) as OrderLine[];
      setDetailLines(lines);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : 'Failed to load details');
    } finally {
      setDetailLoading(false);
    }
  }, [expandedId, selectedVenue]);

  // Create a new purchase order using the same editor the agent uses. Starts
  // blank (no working document); it loads the venue's stock/suppliers itself and
  // submits via create_orders_batch.
  if (creating) {
    const backToOrders = () => setCreating(false);
    return (
      <div>
        {persistVenue ? (
          <PageHeader title="New purchase order" back={{ label: 'Back to orders', onClick: backToOrders }} />
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
            <BackLink label="Back to orders" onClick={backToOrders} />
            <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>New purchase order</span>
          </div>
        )}
        {/* onAction is what flips the editor into interactive mode (its add-item
            search only shows when interactive). A standalone new order has no
            working document or thread to sync line edits to — they live in the
            editor's own state and submit via create_orders_batch — so this is a
            no-op that exists only to enable editing. */}
        <PurchaseOrderEditor
          data={{}}
          props={{ activeVenueId: selectedVenue }}
          onAction={async () => {}}
        />
      </div>
    );
  }

  const count = sortedOrders.length;
  const meta = !loading && count > 0
    ? [`${count} ${count === 1 ? 'order' : 'orders'}`, dateSpan(sortedOrders)].filter(Boolean).join(' · ')
    : undefined;

  const venuePicker = venues.length > 1
    ? <VenueSelect venues={venues} value={selectedVenue} onChange={handleVenueChange} />
    : null;
  const newOrderButton = (
    <Button
      variant="primary"
      icon={Plus}
      onClick={() => setCreating(true)}
      disabled={!selectedVenue}
      title={selectedVenue ? 'Create a new purchase order' : 'Select a venue first'}
    >
      New order
    </Button>
  );

  // The expanded order's loading / failed / empty states (table and cards).
  const detailState = (
    <>
      {detailLoading && (
        <div role="status" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          <Icon icon={LoaderCircle} size="dense" tone="muted" style={{ animation: 'n-spin 1s linear infinite' }} />
          Loading order lines…
        </div>
      )}
      {detailError && <PageState kind="error" title="Couldn’t load this order’s lines" detail={detailError} />}
      {!detailLoading && !detailError && detailLines.length === 0 && (
        <div style={{ padding: '6px 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No line items.</div>
      )}
    </>
  );

  const table = (
    // Date and Total are a little wider than the mockup's 176/106 so "Wed 7 Oct,
    // 10:05 am" and a five-figure total still fit at the touch type sizes.
    <table className="n-table" aria-label="Purchase orders" style={{ tableLayout: 'fixed', minWidth: 640 }}>
      <thead>
        <tr>
          <th scope="col" style={{ width: 184 }}>Date</th>
          <th scope="col">Supplier</th>
          <th scope="col" style={{ width: 156 }}>Ordered by</th>
          <th scope="col" style={{ width: 126 }}>Status</th>
          <th scope="col" className="num" style={{ width: 112 }}>Total</th>
        </tr>
      </thead>
      <tbody>
        {sortedOrders.map(order => {
          const isExpanded = expandedId === order.id;
          const linesId = `po-lines-${order.id}`;
          // The open row is the selected row; the hover fill must not replace it.
          const cell: React.CSSProperties = isExpanded ? { background: 'var(--selected)', borderBottom: 'none' } : {};
          return (
            <React.Fragment key={order.id}>
              <tr onClick={() => toggleRow(order)} style={{ cursor: 'pointer' }}>
                <td style={{ ...cell, whiteSpace: 'nowrap', color: 'var(--text-soft)' }}>
                  <button type="button" aria-expanded={isExpanded} aria-controls={isExpanded ? linesId : undefined} style={ROW_TOGGLE}>
                    <Icon icon={isExpanded ? ChevronDown : ChevronRight} size="dense" tone={isExpanded ? 'inherit' : 'muted'} />
                    <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{formatDate(order.createdAt)}</span>
                  </button>
                </td>
                <td title={order.supplierName} style={{ ...cell, ...ELLIPSIS, fontWeight: 500 }}>{order.supplierName}</td>
                <td title={order.orderedBy} style={{ ...cell, ...ELLIPSIS, color: 'var(--text-soft)' }}>{order.orderedBy}</td>
                <td style={cell}><Badge tone={statusTone(order.status)}>{statusLabel(order.status)}</Badge></td>
                <td className="num" style={{ ...cell, fontWeight: 500 }}>{formatCurrency(order.total)}</td>
              </tr>
              {isExpanded && (
                <tr id={linesId}>
                  <td colSpan={5} style={{ padding: '8px 12px 14px 28px', background: 'var(--surface-alt)' }}>
                    {detailState}
                    {!detailLoading && detailLines.length > 0 && (
                      <table
                        aria-label={`Lines on the ${order.supplierName} order`}
                        style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed', fontSize: 'var(--fs-sm)' }}
                      >
                        <thead>
                          <tr>
                            <th scope="col" style={{ ...LINE_TH, paddingLeft: 0 }}>Item</th>
                            <th scope="col" style={{ ...LINE_TH, width: 96 }}>Unit</th>
                            <th scope="col" className="num" style={{ ...LINE_TH, width: 86 }}>Qty ordered</th>
                            <th scope="col" className="num" style={{ ...LINE_TH, width: 86 }}>Unit cost</th>
                            <th scope="col" className="num" style={{ ...LINE_TH, width: 108, paddingRight: 0 }}>Line total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detailLines.map((line, i) => {
                            const rule = i === detailLines.length - 1 ? { borderBottom: 'none' } : {};
                            return (
                              <tr key={i}>
                                <td style={{ ...LINE_TD, ...rule, paddingLeft: 0 }}>{line.itemName}</td>
                                <td style={{ ...LINE_TD, ...rule, color: 'var(--text-soft)' }}>{line.unitName}</td>
                                <td className="num" style={{ ...LINE_TD, ...rule }}>{line.quantityOrdered}</td>
                                <td className="num" style={{ ...LINE_TD, ...rule }}>{formatCurrency(line.unitCost)}</td>
                                <td className="num" style={{ ...LINE_TD, ...rule, paddingRight: 0 }}>{formatCurrency(line.quantityOrdered * line.unitCost)}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    )}
                  </td>
                </tr>
              )}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );

  // Phones: one card per order — supplier and total, then status, number, day.
  const cards = (
    <ul role="list" aria-label="Purchase orders" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {sortedOrders.map(order => {
        const isExpanded = expandedId === order.id;
        return (
          <li
            key={order.id}
            className="n-card"
            onClick={() => toggleRow(order)}
            style={{ cursor: 'pointer', ...(isExpanded ? { borderColor: 'var(--brand-soft)' } : {}) }}
          >
            {/* No overflow clip on the card: it would cut off this button's
                focus ring, so the button and the lines panel round themselves. */}
            <button
              type="button"
              aria-expanded={isExpanded}
              style={{
                display: 'block', width: '100%', padding: '14px 16px', border: 'none', borderRadius: INNER_RADIUS, background: 'transparent',
                font: 'inherit', fontVariantNumeric: 'tabular-nums', color: 'var(--text)', textAlign: 'left', cursor: 'pointer',
              }}
            >
              <span style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, fontSize: 'var(--fs-base)', fontWeight: 600 }}>
                <span style={{ minWidth: 0, ...ELLIPSIS }}>{order.supplierName}</span>
                <span style={{ flex: '0 0 auto' }}>{formatCurrency(order.total)}</span>
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                <Badge tone={statusTone(order.status)}>{statusLabel(order.status)}</Badge>
                <span>#{order.orderNumber}</span>
                <span style={{ minWidth: 0, ...ELLIPSIS }}>{formatShortDate(order.createdAt)}</span>
              </span>
            </button>
            {isExpanded && (
              <div style={{ padding: '8px 16px 10px', borderTop: '1px solid var(--line)', borderRadius: `0 0 ${INNER_RADIUS} ${INNER_RADIUS}`, background: 'var(--surface)', fontVariantNumeric: 'tabular-nums' }}>
                {detailState}
                {!detailLoading && detailLines.length > 0 && detailLines.map((line, li) => (
                  <div key={li} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '4px 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                    <span style={{ minWidth: 0 }}>{line.itemName} × {line.quantityOrdered}</span>
                    <span style={{ flex: '0 0 auto', color: 'var(--text)' }}>{formatCurrency(line.unitCost * line.quantityOrdered)}</span>
                  </div>
                ))}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );

  const compact = isMobile || narrow;

  // A page: the header, then the list straight on the page (no card around it).
  if (persistVenue) {
    return (
      <div ref={observeWidth}>
        <PageHeader title="Purchase Orders" meta={meta} actions={<>{venuePicker}{newOrderButton}</>} />
        {loading ? (
          <PageState kind="loading" title="Loading orders…" />
        ) : count === 0 ? (
          <PageState kind="empty" title="No purchase orders found." />
        ) : compact ? cards : (
          <div style={{ overflowX: 'auto' }}>{table}</div>
        )}
      </div>
    );
  }

  // In a conversation: a compact title row, and the list in a card.
  return (
    <div ref={observeWidth}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>Purchase Orders</div>
          {meta && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</div>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {venuePicker}
          {newOrderButton}
        </div>
      </div>
      {loading ? (
        <div className="n-card"><PageState kind="loading" title="Loading orders…" /></div>
      ) : count === 0 ? (
        <div className="n-card"><PageState kind="empty" title="No purchase orders found." /></div>
      ) : compact ? cards : (
        <div className="n-card" style={{ overflowX: 'auto' }}>{table}</div>
      )}
    </div>
  );
}
