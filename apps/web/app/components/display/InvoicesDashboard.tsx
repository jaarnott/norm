'use client';

/**
 * The procurement "Invoices" page — outstanding supplier invoices to receive.
 *
 * Mirrors OrdersDashboard, but a row expands into the working-document-backed
 * ReceiveInvoiceEditor rather than a read-only line view: opening an invoice
 * POSTs /invoice-fixes/draft (idempotent — it shapes a `received_invoice`
 * working document from the live Loaded invoice), then renders the editor on
 * that draft. Receiving drops the invoice from the list.
 *
 * Self-loading: the functional page carries no connector loadAction, so this
 * fetches the unreceived list from /invoice-fixes/outstanding directly (no
 * config-DB component-api row needed) and refreshes it on venue change.
 *
 * Looks: as a PAGE (props.persistVenue) it is the page header and a table
 * straight on the page, like Orders; on a phone or tablet the rows become
 * stacked cards. In a conversation it stays compact — a small title row and
 * the table in a card.
 */

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import { formatMoney } from '../../lib/format';
import ReceiveInvoiceEditor, { INVOICE_ACTIONED_EVENT } from './ReceiveInvoiceEditor';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import Badge from '../ui/Badge';
import Icon from '../ui/Icon';

interface OutstandingInvoice {
  id: string;
  referenceNumber: string | null;
  supplierName: string | null;
  issuedAt: string | null;
  total: number | null;
  purchaseOrderNumber: string | null;
  linkedPurchaseOrderId: string | null;
}

interface VenueOption { id: string; name: string }

function extractInvoices(data: Record<string, unknown>): OutstandingInvoice[] {
  if (Array.isArray(data)) return data as OutstandingInvoice[];
  const inner = (data as Record<string, unknown>)?.invoices ?? (data as Record<string, unknown>)?.data;
  if (Array.isArray(inner)) return inner as OutstandingInvoice[];
  return [];
}

const cur = (n: number | null | undefined) => formatMoney(n ?? 0);
function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short', year: 'numeric' });
}

// Loaded's own rule for a credit note is total < 0 — the row flags it so
// nobody opens one expecting goods.
const isCredit = (inv: OutstandingInvoice) => typeof inv.total === 'number' && inv.total < 0;

// The chevron + date at the start of a row: a real button, so the row can be
// reached and opened from the keyboard. It has no handler of its own — its
// click bubbles to the row's. Top-aligned: an inline-flex box otherwise sits a
// couple of pixels above the neighbouring cells' text.
const rowToggle: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 4, padding: 0, border: 'none',
  background: 'transparent', font: 'inherit', color: 'inherit', textAlign: 'left',
  whiteSpace: 'nowrap', cursor: 'pointer', verticalAlign: 'top',
};

// Phones and tablets: one white card per invoice (mock-MobilePage).
const mobileCard: React.CSSProperties = {
  display: 'block', width: '100%', boxSizing: 'border-box', padding: '14px 16px',
  border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', background: 'var(--bg)',
  font: 'inherit', fontVariantNumeric: 'tabular-nums', color: 'var(--text)', textAlign: 'left',
  cursor: 'pointer',
};

export default function InvoicesDashboard({ data, props }: DisplayBlockProps) {
  // Shared page venue — only honoured when this dashboard is a PAGE instance
  // (persistVenue), never when it's embedded in a conversation.
  const persistVenue = !!props?.persistVenue;
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [selectedVenue, setSelectedVenue] = useState<string | null>((props?.activeVenueId as string) || rememberedVenue || null);
  const [invoices, setInvoices] = useState<OutstandingInvoice[]>(() => extractInvoices(data));
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  // Phones and tablets get stacked cards: on an iPad the page column beside
  // the menu panel is ~350px, where the table would hide the totals off to
  // the right.
  const { isMobile, isTablet } = useBreakpoint();
  const stacked = isMobile || isTablet;

  useEffect(() => {
    apiFetch('/api/venues')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
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
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async (venueId: string) => {
    setLoading(true);
    try {
      const res = await apiFetch(`/api/invoice-fixes/outstanding?venue_id=${venueId}`);
      const d = res.ok ? await res.json() : {};
      setInvoices(extractInvoices(d));
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (selectedVenue) load(selectedVenue);
  }, [selectedVenue, load]);

  // Silent refetch — refresh the outstanding list WITHOUT the full-page loading
  // state, so an action elsewhere on the page updates it without a visible reload.
  const refreshInBackground = useCallback(async (venueId: string) => {
    try {
      const res = await apiFetch(`/api/invoice-fixes/outstanding?venue_id=${venueId}`);
      const d = res.ok ? await res.json() : {};
      setInvoices(extractInvoices(d));
    } catch { /* ignore */ }
  }, []);

  // When an invoice is received or deleted — e.g. accepting a "delete this
  // statement/duplicate" suggestion inside the editor — it announces itself via
  // INVOICE_ACTIONED_EVENT. Pull a fresh list in the background so the actioned
  // invoice drops off the page. The server list is authoritative: a deleted or
  // received invoice is gone from /outstanding, while a mere draft reset (which
  // fires the same event) leaves it in place — so we never wrongly remove a row.
  useEffect(() => {
    const onActioned = (e: Event) => {
      if (!selectedVenue) return;
      const detail = (e as CustomEvent).detail as { venueId?: string } | undefined;
      if (detail?.venueId && detail.venueId !== selectedVenue) return;
      refreshInBackground(selectedVenue);
    };
    window.addEventListener(INVOICE_ACTIONED_EVENT, onActioned);
    return () => window.removeEventListener(INVOICE_ACTIONED_EVENT, onActioned);
  }, [selectedVenue, refreshInBackground]);

  const handleVenueChange = useCallback((venueId: string) => {
    setSelectedVenue(venueId);
    if (persistVenue) setActiveVenue(venueId);
    setExpandedId(null);
    setDraftId(null);
  }, [persistVenue, setActiveVenue]);

  const sorted = useMemo(
    () => [...invoices].sort((a, b) => new Date(b.issuedAt || 0).getTime() - new Date(a.issuedAt || 0).getTime()),
    [invoices],
  );

  const toggleRow = useCallback(async (inv: OutstandingInvoice) => {
    if (expandedId === inv.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(inv.id);
    setDraftId(null);
    setDraftError(null);
    setDraftLoading(true);
    try {
      const res = await apiFetch('/api/invoice-fixes/draft', {
        method: 'POST',
        body: JSON.stringify({ venue_id: selectedVenue, invoice_id: inv.id }),
      });
      if (!res.ok) throw new Error(`Could not open the invoice (${res.status})`);
      const doc = await res.json();
      setDraftId(doc.id);
    } catch (e) {
      setDraftError(e instanceof Error ? e.message : 'Failed to open the invoice');
    } finally {
      setDraftLoading(false);
    }
  }, [expandedId, selectedVenue]);

  const onReceived = useCallback((invoiceId: string) => {
    // Received invoices drop off the outstanding list.
    setInvoices((prev) => prev.filter((i) => i.id !== invoiceId));
    setExpandedId(null);
    setDraftId(null);
  }, []);

  const venuePicker = venues.length > 1 ? (
    <VenueSelect venues={venues} value={selectedVenue} onChange={handleVenueChange} />
  ) : null;

  const creditBadge = (
    <Badge tone="error" title="Credit note — receiving it reverses stock and cost">Credit note</Badge>
  );

  // An opened invoice: the receive editor on its working-document draft.
  const renderEditor = (inv: OutstandingInvoice) => (
    <>
      {draftLoading && <PageState kind="loading" title="Opening the invoice…" />}
      {draftError && <PageState kind="error" title={draftError} />}
      {!draftLoading && !draftError && draftId && (
        <ReceiveInvoiceEditor
          data={{ working_document_id: draftId, venue_id: selectedVenue || undefined }}
          props={{ activeVenueId: selectedVenue, onReceived: () => onReceived(inv.id) }}
        />
      )}
    </>
  );

  const table = (
    <table className="n-table" aria-label="Outstanding invoices" style={{ minWidth: 640 }}>
      <thead>
        <tr>
          <th style={{ width: 150 }}>Date</th>
          <th>Supplier</th>
          <th style={{ width: 170 }}>Reference</th>
          <th style={{ width: 120 }}>PO</th>
          <th className="num" style={{ width: 120 }}>Total</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((inv) => {
          const isExpanded = expandedId === inv.id;
          const credit = isCredit(inv);
          // The open row keeps its selected fill under the pointer (inline
          // beats .n-table's row hover) and runs straight into its panel.
          const cell: React.CSSProperties = isExpanded ? { background: 'var(--selected)', borderBottom: 'none' } : {};
          const soft: React.CSSProperties = { ...cell, color: 'var(--text-soft)', whiteSpace: 'nowrap' };
          return (
            <React.Fragment key={inv.id}>
              <tr onClick={() => toggleRow(inv)} style={{ cursor: 'pointer' }}>
                <td style={soft}>
                  <button type="button" aria-expanded={isExpanded} style={rowToggle}>
                    <Icon icon={isExpanded ? ChevronDown : ChevronRight} size="dense" tone={isExpanded ? 'inherit' : 'muted'} />
                    {formatDate(inv.issuedAt)}
                  </button>
                </td>
                <td style={{ ...cell, fontWeight: 500 }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', flexWrap: 'wrap', gap: '2px 8px' }}>
                    {inv.supplierName || '—'}
                    {credit && creditBadge}
                  </span>
                </td>
                <td style={soft}>{inv.referenceNumber || '—'}</td>
                <td style={soft}>{inv.linkedPurchaseOrderId ? (inv.purchaseOrderNumber || 'linked') : '—'}</td>
                <td className="num" style={{ ...cell, fontWeight: 500, whiteSpace: 'nowrap', ...(credit ? { color: 'var(--error)' } : {}) }}>
                  {cur(inv.total)}
                </td>
              </tr>
              {isExpanded && (
                <tr>
                  {/* Inline background: the editor panel never takes the row hover. */}
                  <td colSpan={5} style={{ padding: '12px 12px 16px', background: 'var(--surface-alt)' }}>
                    {renderEditor(inv)}
                  </td>
                </tr>
              )}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );

  const cards = (
    <ul role="list" aria-label="Outstanding invoices" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {sorted.map((inv) => {
        const isExpanded = expandedId === inv.id;
        const credit = isCredit(inv);
        return (
          <li key={inv.id}>
            <button
              type="button"
              aria-expanded={isExpanded}
              onClick={() => toggleRow(inv)}
              style={isExpanded ? { ...mobileCard, background: 'var(--selected)', borderColor: 'var(--brand-soft)' } : mobileCard}
            >
              <span style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, fontSize: 'var(--fs-base)', fontWeight: 600 }}>
                <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{inv.supplierName || '—'}</span>
                <span style={{ flex: '0 0 auto', ...(credit ? { color: 'var(--error)' } : {}) }}>{cur(inv.total)}</span>
              </span>
              <span style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '4px 8px', marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                {credit && creditBadge}
                <span>{inv.referenceNumber || 'No reference'}</span>
                <span>{formatDate(inv.issuedAt)}</span>
                {inv.linkedPurchaseOrderId && <span>PO {inv.purchaseOrderNumber || 'linked'}</span>}
              </span>
            </button>
            {isExpanded && <div style={{ marginTop: 8 }}>{renderEditor(inv)}</div>}
          </li>
        );
      })}
    </ul>
  );

  // In a conversation the states sit in the same card the table would.
  const inCard = (node: React.ReactNode) => (persistVenue ? node : <div className="n-card">{node}</div>);
  const body = loading ? (
    inCard(<PageState kind="loading" title="Loading invoices…" />)
  ) : sorted.length === 0 ? (
    inCard(<PageState kind="empty" title="No outstanding invoices." />)
  ) : stacked ? (
    cards
  ) : (
    // Wide tables scroll sideways inside their own box, never the page.
    <div
      className={persistVenue ? undefined : 'n-card'}
      style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}
    >
      {table}
    </div>
  );

  if (persistVenue) {
    return (
      <div>
        <PageHeader
          title="Invoices"
          // A blank line while loading keeps the header from jumping; the
          // body says it is loading.
          meta={loading ? '\u00a0' : `${sorted.length} outstanding`}
          actions={venuePicker}
        />
        {body}
      </div>
    );
  }

  // In a conversation: compact — a small title row, the table in a card.
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>Invoices</h2>
          {!loading && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{sorted.length} outstanding</span>}
        </div>
        {venuePicker}
      </div>
      {body}
    </div>
  );
}
