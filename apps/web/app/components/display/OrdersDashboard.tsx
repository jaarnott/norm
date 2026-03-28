'use client';

import { useState, useMemo, useCallback } from 'react';
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
  unit: string;
  qtyOrdered: number;
  qtyReceived: number;
  unitCost: number;
  lineTotal: number;
}

function extractOrders(data: Record<string, unknown>): OrderSummary[] {
  if (Array.isArray(data)) return data as OrderSummary[];
  if (Array.isArray((data as Record<string, unknown>).data)) return (data as Record<string, unknown>).data as OrderSummary[];
  // Look for first array-valued key
  for (const key of Object.keys(data)) {
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
  return d.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short' });
}

function statusBadge(status: string): { bg: string; text: string } {
  const s = status.toLowerCase();
  if (s === 'outstanding') return { bg: '#fff3cd', text: '#856404' };
  if (s === 'acknowledged') return { bg: '#d4edda', text: '#155724' };
  return { bg: '#e2e3e5', text: '#383d41' };
}

export default function OrdersDashboard({ data, props }: DisplayBlockProps) {
  const orders = useMemo(() => {
    const raw = extractOrders(data);
    return [...raw].sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
  }, [data]);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detailLines, setDetailLines] = useState<OrderLine[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const venueId = (props?.activeVenueId as string) || '';

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
          venue_id: venueId,
        }),
      });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      const result = await res.json();
      const lines = extractOrders(result.data || result) as unknown as OrderLine[];
      setDetailLines(Array.isArray(lines) ? lines : []);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : 'Failed to load details');
    } finally {
      setDetailLoading(false);
    }
  }, [expandedId, venueId]);

  if (orders.length === 0) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>
        No purchase orders found.
      </div>
    );
  }

  const thStyle: React.CSSProperties = {
    padding: '8px 12px', textAlign: 'left', fontSize: '0.72rem', fontWeight: 600,
    color: colors.textSecondary, borderBottom: `2px solid ${colors.border}`,
    whiteSpace: 'nowrap',
  };
  const tdStyle: React.CSSProperties = {
    padding: '8px 12px', fontSize: '0.8rem', color: colors.textPrimary,
    borderBottom: `1px solid ${colors.borderLight}`,
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem' }}>
        <span style={{ fontSize: '0.9rem', fontWeight: 600, color: colors.textPrimary }}>Purchase Orders</span>
        <span style={{ fontSize: '0.75rem', color: colors.textMuted }}>{orders.length} orders</span>
      </div>

      <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 600 }}>
          <thead>
            <tr>
              <th style={thStyle}>Order #</th>
              <th style={thStyle}>Supplier</th>
              <th style={thStyle}>Ordered By</th>
              <th style={thStyle}>Status</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Total</th>
              <th style={thStyle}>Date</th>
            </tr>
          </thead>
          <tbody>
            {orders.map(order => {
              const badge = statusBadge(order.status);
              const isExpanded = expandedId === order.id;
              return (
                <tr key={order.id} style={{ cursor: 'pointer' }} onClick={() => toggleRow(order)}>
                  <td colSpan={6} style={{ padding: 0 }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <tbody>
                        <tr
                          style={{ backgroundColor: isExpanded ? colors.selectedBg : undefined }}
                          onMouseEnter={e => { if (!isExpanded) (e.currentTarget as HTMLElement).style.backgroundColor = colors.pageBg; }}
                          onMouseLeave={e => { if (!isExpanded) (e.currentTarget as HTMLElement).style.backgroundColor = ''; }}
                        >
                          <td style={{ ...tdStyle, fontWeight: 500 }}>{order.orderNumber}</td>
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
                          <td style={tdStyle}>{formatDate(order.createdAt)}</td>
                        </tr>
                        {isExpanded && (
                          <tr>
                            <td colSpan={6} style={{ padding: '8px 12px 16px 24px', backgroundColor: colors.selectedBg, borderBottom: `1px solid ${colors.borderLight}` }}>
                              {detailLoading && (
                                <div style={{ fontSize: '0.78rem', color: colors.textMuted, padding: '8px 0' }}>Loading order lines...</div>
                              )}
                              {detailError && (
                                <div style={{ fontSize: '0.78rem', color: colors.error, padding: '8px 0' }}>{detailError}</div>
                              )}
                              {!detailLoading && !detailError && detailLines.length === 0 && (
                                <div style={{ fontSize: '0.78rem', color: colors.textMuted, padding: '8px 0' }}>No line items.</div>
                              )}
                              {!detailLoading && detailLines.length > 0 && (
                                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                  <thead>
                                    <tr>
                                      {['Item', 'Unit', 'Qty Ordered', 'Qty Received', 'Unit Cost', 'Line Total'].map(h => (
                                        <th key={h} style={{
                                          padding: '4px 8px', textAlign: h === 'Unit Cost' || h === 'Line Total' ? 'right' : 'left',
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
                                        <td style={{ padding: '4px 8px', fontSize: '0.76rem' }}>{line.unit}</td>
                                        <td style={{ padding: '4px 8px', fontSize: '0.76rem' }}>{line.qtyOrdered}</td>
                                        <td style={{ padding: '4px 8px', fontSize: '0.76rem' }}>{line.qtyReceived}</td>
                                        <td style={{ padding: '4px 8px', fontSize: '0.76rem', textAlign: 'right' }}>{formatCurrency(line.unitCost)}</td>
                                        <td style={{ padding: '4px 8px', fontSize: '0.76rem', textAlign: 'right' }}>{formatCurrency(line.lineTotal)}</td>
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
    </div>
  );
}
