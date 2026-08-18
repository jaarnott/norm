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
 */

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { colors } from '../../lib/theme';
import { formatMoney } from '../../lib/format';
import ReceiveInvoiceEditor, { INVOICE_ACTIONED_EVENT } from './ReceiveInvoiceEditor';

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

  const thStyle: React.CSSProperties = {
    padding: '8px 12px', textAlign: 'left', fontSize: '0.72rem', fontWeight: 600,
    color: colors.textSecondary, borderBottom: `2px solid ${colors.border}`, whiteSpace: 'nowrap',
  };
  const tdStyle: React.CSSProperties = {
    padding: '8px 12px', fontSize: '0.8rem', color: colors.textPrimary,
    borderBottom: `1px solid ${colors.borderLight}`,
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem', flexWrap: 'wrap', gap: '0.5rem' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: '#1a1a1a' }}>Invoices</h2>
          <span style={{ fontSize: '0.75rem', color: colors.textMuted }}>
            {loading ? 'Loading...' : `${sorted.length} outstanding`}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {venues.length > 1 && (
            <select value={selectedVenue || ''} onChange={(e) => handleVenueChange(e.target.value)}
              style={{ padding: '3px 8px', fontSize: '0.75rem', border: `1px solid ${colors.border}`, borderRadius: 6, fontFamily: 'inherit', color: colors.textSecondary, backgroundColor: '#fff' }}>
              {!selectedVenue && <option value="">Select venue</option>}
              {venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          )}
        </div>
      </div>

      {loading && (
        <div style={{ padding: '2rem', textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>Loading invoices...</div>
      )}
      {!loading && sorted.length === 0 && (
        <div style={{ padding: '2rem', textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>No outstanding invoices.</div>
      )}

      {!loading && sorted.length > 0 && (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
            <thead>
              <tr>
                <th style={{ ...thStyle, width: 130 }}>Date</th>
                <th style={thStyle}>Supplier</th>
                <th style={{ ...thStyle, width: 160 }}>Reference</th>
                <th style={{ ...thStyle, width: 130 }}>PO</th>
                <th style={{ ...thStyle, width: 120, textAlign: 'right' }}>Total</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((inv) => {
                const isExpanded = expandedId === inv.id;
                return (
                  <React.Fragment key={inv.id}>
                    <tr
                      onClick={() => toggleRow(inv)}
                      style={{ cursor: 'pointer', backgroundColor: isExpanded ? colors.selectedBg : undefined }}
                      onMouseEnter={(e) => { if (!isExpanded) (e.currentTarget as HTMLElement).style.backgroundColor = colors.pageBg; }}
                      onMouseLeave={(e) => { if (!isExpanded) (e.currentTarget as HTMLElement).style.backgroundColor = ''; }}
                    >
                      <td style={tdStyle}>{formatDate(inv.issuedAt)}</td>
                      <td style={tdStyle}>{inv.supplierName}</td>
                      <td style={tdStyle}>
                        {inv.referenceNumber || '—'}
                        {/* Loaded's own rule for a credit note is total < 0 —
                            flag it here so nobody opens one expecting goods. */}
                        {typeof inv.total === 'number' && inv.total < 0 && (
                          <span title="credit note — receiving it reverses stock and cost"
                            style={{ marginLeft: 6, fontSize: '0.58rem', fontWeight: 700, letterSpacing: '0.04em', padding: '1px 5px', borderRadius: 3, background: '#fdecea', color: '#a4322a', border: '1px solid #f0c2bc', whiteSpace: 'nowrap' }}>
                            CREDIT
                          </span>
                        )}
                      </td>
                      <td style={tdStyle}>{inv.linkedPurchaseOrderId ? (inv.purchaseOrderNumber || 'linked') : '—'}</td>
                      <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 500, ...(typeof inv.total === 'number' && inv.total < 0 ? { color: '#a4322a' } : {}) }}>{cur(inv.total)}</td>
                    </tr>
                    {isExpanded && (
                      <tr>
                        <td colSpan={5} style={{ padding: '8px 12px 16px', backgroundColor: colors.selectedBg, borderBottom: `1px solid ${colors.borderLight}` }}>
                          {draftLoading && <div style={{ fontSize: '0.78rem', color: colors.textMuted, padding: '8px 0' }}>Opening the invoice…</div>}
                          {draftError && <div style={{ fontSize: '0.78rem', color: colors.error, padding: '8px 0' }}>{draftError}</div>}
                          {!draftLoading && !draftError && draftId && (
                            <ReceiveInvoiceEditor
                              data={{ working_document_id: draftId, venue_id: selectedVenue || undefined }}
                              props={{ activeVenueId: selectedVenue, onReceived: () => onReceived(inv.id) }}
                            />
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
