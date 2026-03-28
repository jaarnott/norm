'use client';

import { useState, useEffect, useMemo, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { colors } from '../../lib/theme';

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
  return `$${n.toFixed(2)}`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString('en-NZ', { weekday: 'long' });
  const rest = d.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short', year: 'numeric' });
  const time = d.toLocaleTimeString('en-NZ', { hour: '2-digit', minute: '2-digit', hour12: false });
  return `${day}, ${rest} ${time}`;
}

function statusBadge(status: string): { bg: string; text: string } {
  const s = status.toLowerCase();
  if (s === 'outstanding') return { bg: '#fff3cd', text: '#856404' };
  if (s === 'acknowledged') return { bg: '#d4edda', text: '#155724' };
  return { bg: '#e2e3e5', text: '#383d41' };
}

export default function OrdersDashboard({ data, props }: DisplayBlockProps) {
  // Venue selector — same pattern as RosterEditor
  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [selectedVenue, setSelectedVenue] = useState<string | null>((props?.activeVenueId as string) || null);
  const [orders, setOrders] = useState<OrderSummary[]>(() => extractOrders(data));
  const [loading, setLoading] = useState(false);

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
            setSelectedVenue(d.venues[0].id);
          }
        }
      })
      .catch(() => {});
  }, []);

  // Load orders when venue changes
  const loadOrders = useCallback(async (venueId: string) => {
    setLoading(true);
    try {
      const res = await apiFetch('/api/working-documents/from-connector', {
        method: 'POST',
        body: JSON.stringify({
          connector_name: 'loadedhub',
          action: 'get_purchase_orders_summary',
          params: {},
          doc_type: 'orders',
          venue_id: venueId,
        }),
      });
      if (res.ok) {
        const result = await res.json();
        const extracted = extractOrders(result.data || result);
        setOrders(extracted);
      }
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
    setExpandedId(null);
    loadOrders(venueId);
  }, [loadOrders]);

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
      const res = await apiFetch('/api/working-documents/from-connector', {
        method: 'POST',
        body: JSON.stringify({
          connector_name: 'loadedhub',
          action: 'get_purchase_order_detail',
          params: { order_id: order.id },
          venue_id: selectedVenue,
        }),
      });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      const result = await res.json();
      const d = result.data || result;
      const lines = d.lines || [];
      setDetailLines(lines);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : 'Failed to load details');
    } finally {
      setDetailLoading(false);
    }
  }, [expandedId, selectedVenue]);

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
      {/* Header with venue selector */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.9rem', fontWeight: 600, color: colors.textPrimary }}>Purchase Orders</span>
        {venues.length > 1 && (
          <select
            value={selectedVenue || ''}
            onChange={e => handleVenueChange(e.target.value)}
            style={{
              padding: '3px 8px', fontSize: '0.75rem', border: `1px solid ${colors.border}`,
              borderRadius: 6, fontFamily: 'inherit', color: colors.textSecondary, backgroundColor: '#fff',
            }}
          >
            {!selectedVenue && <option value="">Select venue</option>}
            {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
        )}
        <span style={{ fontSize: '0.75rem', color: colors.textMuted }}>
          {loading ? 'Loading...' : `${sortedOrders.length} orders`}
        </span>
      </div>

      {loading && (
        <div style={{ padding: '2rem', textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>
          Loading orders...
        </div>
      )}

      {!loading && sortedOrders.length === 0 && (
        <div style={{ padding: '2rem', textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>
          No purchase orders found.
        </div>
      )}

      {!loading && sortedOrders.length > 0 && (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 600, tableLayout: 'fixed' }}>
            <thead>
              <tr>
                <th style={{ ...thStyle, width: 150 }}>Date</th>
                <th style={thStyle}>Supplier</th>
                <th style={{ ...thStyle, width: 150 }}>Ordered By</th>
                <th style={{ ...thStyle, width: 150 }}>Status</th>
                <th style={{ ...thStyle, width: 150, textAlign: 'right' }}>Total</th>
              </tr>
            </thead>
            <tbody>
              {sortedOrders.map(order => {
                const badge = statusBadge(order.status);
                const isExpanded = expandedId === order.id;
                return (
                  <tr key={order.id} onClick={() => toggleRow(order)} style={{ cursor: 'pointer' }}>
                    <td colSpan={5} style={{ padding: 0 }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <tbody>
                          <tr
                            style={{ backgroundColor: isExpanded ? colors.selectedBg : undefined }}
                            onMouseEnter={e => { if (!isExpanded) (e.currentTarget as HTMLElement).style.backgroundColor = colors.pageBg; }}
                            onMouseLeave={e => { if (!isExpanded) (e.currentTarget as HTMLElement).style.backgroundColor = ''; }}
                          >
                            <td style={tdStyle}>{formatDate(order.createdAt)}</td>
                            <td style={tdStyle}>{order.supplierName}</td>
                            <td style={tdStyle}>{order.orderedBy}</td>
                            <td style={tdStyle}>
                              <span style={{
                                display: 'inline-block', padding: '2px 8px', borderRadius: 10,
                                fontSize: '0.7rem', fontWeight: 500, backgroundColor: badge.bg, color: badge.text,
                              }}>
                                {order.status}
                              </span>
                            </td>
                            <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 500 }}>{formatCurrency(order.total)}</td>
                          </tr>
                          {isExpanded && (
                            <tr>
                              <td colSpan={5} style={{ padding: '8px 12px 16px 24px', backgroundColor: colors.selectedBg, borderBottom: `1px solid ${colors.borderLight}` }}>
                                {detailLoading && <div style={{ fontSize: '0.78rem', color: colors.textMuted, padding: '8px 0' }}>Loading order lines...</div>}
                                {detailError && <div style={{ fontSize: '0.78rem', color: colors.error, padding: '8px 0' }}>{detailError}</div>}
                                {!detailLoading && !detailError && detailLines.length === 0 && (
                                  <div style={{ fontSize: '0.78rem', color: colors.textMuted, padding: '8px 0' }}>No line items.</div>
                                )}
                                {!detailLoading && detailLines.length > 0 && (
                                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                    <thead>
                                      <tr>
                                        {['Item', 'Unit', 'Qty Ordered', 'Qty Received', 'Unit Cost', 'Line Total'].map(h => (
                                          <th key={h} style={{
                                            padding: '4px 8px', textAlign: h.includes('Cost') || h.includes('Total') ? 'right' : 'left',
                                            fontSize: '0.68rem', fontWeight: 600, color: colors.textSecondary,
                                            borderBottom: `1px solid ${colors.border}`,
                                          }}>{h}</th>
                                        ))}
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {detailLines.map((line, i) => (
                                        <tr key={i}>
                                          <td style={{ padding: '4px 8px', fontSize: '0.76rem' }}>{line.itemName}</td>
                                          <td style={{ padding: '4px 8px', fontSize: '0.76rem' }}>{line.unitName}</td>
                                          <td style={{ padding: '4px 8px', fontSize: '0.76rem' }}>{line.quantityOrdered}</td>
                                          <td style={{ padding: '4px 8px', fontSize: '0.76rem' }}>{line.quantityReceived}</td>
                                          <td style={{ padding: '4px 8px', fontSize: '0.76rem', textAlign: 'right' }}>{formatCurrency(line.unitCost)}</td>
                                          <td style={{ padding: '4px 8px', fontSize: '0.76rem', textAlign: 'right' }}>{formatCurrency(line.quantityOrdered * line.unitCost)}</td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                )}
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
