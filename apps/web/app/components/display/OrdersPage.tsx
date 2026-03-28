'use client';

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Search, Plus, Minus, Trash2, ShoppingCart, Package, CheckCircle } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { getStoredUser } from '../../lib/api';
import { colors } from '../../lib/theme';

interface StockItem {
  id: string;
  name: string;
  defaultSupplierId: string;
  defaultSupplierName: string;
  defaultBrandId: string | null;
  orderingUnitId: string;
  orderingUnitName: string;
  orderingUnitRatio: number;
  currentPrice: number;
  globalSalesTaxRate: number;
  countingUnitName: string;
}

interface OrderItem extends StockItem {
  quantity: number;
}

interface SupplierGroup {
  supplierId: string;
  supplierName: string;
  items: OrderItem[];
  subtotal: number;
  tax: number;
  total: number;
}

export default function OrdersPage({ props }: DisplayBlockProps) {
  const activeVenueId = (props?.activeVenueId as string) || null;

  const [searchQuery, setSearchQuery] = useState('');
  const [allStockItems, setAllStockItems] = useState<StockItem[] | null>(null);
  const [searchResults, setSearchResults] = useState<StockItem[]>([]);
  const [orderItems, setOrderItems] = useState<OrderItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load all stock items once on mount
  useEffect(() => {
    if (!activeVenueId) return;
    setSearching(true);
    setError(null);
    apiFetch('/api/working-documents/from-connector', {
      method: 'POST',
      body: JSON.stringify({
        connector_name: 'loadedhub',
        action: 'get_stock_items',
        params: {},
        venue_id: activeVenueId,
      }),
    })
      .then(async (res) => {
        const result = await res.json();
        if (res.ok) {
          const items: StockItem[] = result.data?.stock_items || result.stock_items || [];
          setAllStockItems(items);
        } else {
          setError(result.error || result.detail || 'Failed to load stock items');
        }
      })
      .catch((err) => setError(err.message))
      .finally(() => setSearching(false));
  }, [activeVenueId]);

  // Filter results client-side with debounce
  useEffect(() => {
    if (!allStockItems) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      if (!searchQuery.trim()) {
        setSearchResults([]);
        return;
      }
      const q = searchQuery.toLowerCase();
      const filtered = allStockItems.filter((item) =>
        item.name.toLowerCase().includes(q)
      );
      setSearchResults(filtered.slice(0, 50));
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchQuery, allStockItems]);

  const addToOrder = useCallback((item: StockItem) => {
    setOrderItems((prev) => {
      const existing = prev.find((o) => o.id === item.id);
      if (existing) {
        return prev.map((o) =>
          o.id === item.id ? { ...o, quantity: o.quantity + 1 } : o
        );
      }
      return [...prev, { ...item, quantity: 1 }];
    });
  }, []);

  const updateQuantity = useCallback((itemId: string, quantity: number) => {
    if (quantity < 1) return;
    setOrderItems((prev) =>
      prev.map((o) => (o.id === itemId ? { ...o, quantity } : o))
    );
  }, []);

  const removeItem = useCallback((itemId: string) => {
    setOrderItems((prev) => prev.filter((o) => o.id !== itemId));
  }, []);

  // Group order items by supplier
  const supplierGroups: SupplierGroup[] = useMemo(() => {
    const groups: Record<string, OrderItem[]> = {};
    for (const item of orderItems) {
      if (!groups[item.defaultSupplierId]) {
        groups[item.defaultSupplierId] = [];
      }
      groups[item.defaultSupplierId].push(item);
    }
    return Object.entries(groups).map(([supplierId, items]) => {
      const subtotal = items.reduce(
        (sum, item) => sum + item.currentPrice * item.quantity,
        0
      );
      const tax = items.reduce(
        (sum, item) =>
          sum + item.currentPrice * item.quantity * (item.globalSalesTaxRate / 100),
        0
      );
      return {
        supplierId,
        supplierName: items[0].defaultSupplierName,
        items,
        subtotal,
        tax,
        total: subtotal + tax,
      };
    });
  }, [orderItems]);

  const grandTotal = useMemo(
    () => supplierGroups.reduce((sum, g) => sum + g.total, 0),
    [supplierGroups]
  );

  const handleSubmit = useCallback(async () => {
    if (supplierGroups.length === 0) return;
    setSubmitting(true);
    setError(null);
    setSuccess(null);

    const user = getStoredUser();
    const userName = user?.full_name || 'Unknown';

    const payload = supplierGroups.map((group) => ({
      createdAt: new Date().toISOString(),
      isReceived: false,
      supplierId: group.supplierId,
      orderedBy: userName,
      status: 'Outstanding',
      creditRequest: false,
      subtotal: group.subtotal,
      total: group.total,
      tax: group.tax,
      lines: group.items.map((item) => ({
        itemId: item.id,
        itemCode: '',
        brandId: item.defaultBrandId,
        unitId: item.orderingUnitId,
        unitRatio: item.orderingUnitRatio,
        unitCost: item.currentPrice,
        quantityReceived: 0,
        taxPercent: item.globalSalesTaxRate,
        quantityOrdered: item.quantity,
        unitCostOrdered: item.currentPrice / item.orderingUnitRatio,
      })),
    }));

    try {
      const res = await apiFetch('/api/working-documents/from-connector', {
        method: 'POST',
        body: JSON.stringify({
          connector_name: 'loadedhub',
          action: 'create_purchase_orders',
          params: { orders: payload },
          venue_id: activeVenueId,
        }),
      });
      const result = await res.json();
      if (res.ok) {
        setSuccess(
          `Successfully submitted ${supplierGroups.length} purchase order${supplierGroups.length > 1 ? 's' : ''}`
        );
        setOrderItems([]);
        setSubmitted(true);
      } else {
        setError(result.error || result.detail || 'Failed to submit orders');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit orders');
    } finally {
      setSubmitting(false);
    }
  }, [supplierGroups, activeVenueId]);

  const formatCurrency = (n: number) =>
    `$${n.toFixed(2)}`;

  if (!activeVenueId) {
    return (
      <div style={{ padding: '3rem 2rem', textAlign: 'center', color: colors.textMuted }}>
        <ShoppingCart size={48} style={{ marginBottom: '1rem', opacity: 0.4 }} />
        <p style={{ fontSize: '1.1rem' }}>Select a venue to create purchase orders</p>
      </div>
    );
  }

  if (submitted && success) {
    return (
      <div style={{ padding: '3rem 2rem', textAlign: 'center' }}>
        <CheckCircle size={48} color={colors.success} style={{ marginBottom: '1rem' }} />
        <p style={{ fontSize: '1.1rem', color: colors.textPrimary, marginBottom: '1rem' }}>
          {success}
        </p>
        <button
          onClick={() => {
            setSubmitted(false);
            setSuccess(null);
          }}
          style={{
            padding: '10px 24px',
            fontSize: '0.9rem',
            fontWeight: 600,
            backgroundColor: colors.primary,
            color: colors.textOnPrimary,
            border: 'none',
            borderRadius: 8,
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          Create another order
        </button>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto' }}>
      {/* Search section */}
      <div style={{ marginBottom: '1.5rem' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 14px',
            border: `1px solid ${colors.border}`,
            borderRadius: 10,
            backgroundColor: colors.inputBg,
          }}
        >
          <Search size={18} color={colors.textMuted} />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={
              allStockItems
                ? `Search ${allStockItems.length} stock items...`
                : 'Loading stock items...'
            }
            disabled={!allStockItems}
            style={{
              flex: 1,
              border: 'none',
              outline: 'none',
              fontSize: '0.95rem',
              fontFamily: 'inherit',
              backgroundColor: 'transparent',
              color: colors.textPrimary,
            }}
          />
          {searching && (
            <span style={{ fontSize: '0.8rem', color: colors.textMuted }}>Loading...</span>
          )}
        </div>

        {/* Search results */}
        {searchResults.length > 0 && (
          <div
            style={{
              marginTop: 4,
              border: `1px solid ${colors.border}`,
              borderRadius: 10,
              backgroundColor: colors.cardBg,
              maxHeight: 320,
              overflowY: 'auto',
            }}
          >
            {searchResults.map((item) => {
              const inOrder = orderItems.find((o) => o.id === item.id);
              return (
                <div
                  key={item.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 14px',
                    borderBottom: `1px solid ${colors.borderLight}`,
                    fontSize: '0.88rem',
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontWeight: 500,
                        color: colors.textPrimary,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {item.name}
                    </div>
                    <div style={{ fontSize: '0.78rem', color: colors.textMuted }}>
                      {item.defaultSupplierName} &middot; {item.orderingUnitName} &middot;{' '}
                      {formatCurrency(item.currentPrice)}
                    </div>
                  </div>
                  <button
                    onClick={() => addToOrder(item)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                      padding: '4px 10px',
                      fontSize: '0.78rem',
                      fontWeight: 600,
                      backgroundColor: inOrder ? colors.selectedBg : colors.primary,
                      color: inOrder ? colors.textPrimary : colors.textOnPrimary,
                      border: 'none',
                      borderRadius: 6,
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                      flexShrink: 0,
                    }}
                  >
                    <Plus size={14} />
                    {inOrder ? `(${inOrder.quantity})` : 'Add'}
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {searchQuery && allStockItems && searchResults.length === 0 && !searching && (
          <div style={{ padding: '12px 14px', color: colors.textMuted, fontSize: '0.88rem' }}>
            No items matching &quot;{searchQuery}&quot;
          </div>
        )}
      </div>

      {/* Error/Success messages */}
      {error && (
        <div
          style={{
            padding: '10px 14px',
            marginBottom: '1rem',
            backgroundColor: '#fef2f2',
            color: colors.error,
            borderRadius: 8,
            fontSize: '0.88rem',
          }}
        >
          {error}
        </div>
      )}

      {/* Order items grouped by supplier */}
      {orderItems.length === 0 ? (
        <div
          style={{
            padding: '3rem 2rem',
            textAlign: 'center',
            color: colors.textMuted,
            border: `2px dashed ${colors.border}`,
            borderRadius: 12,
          }}
        >
          <Package size={36} style={{ marginBottom: '0.75rem', opacity: 0.4 }} />
          <p style={{ fontSize: '0.95rem', margin: 0 }}>
            Search for items above and add them to your order
          </p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {supplierGroups.map((group) => (
            <div
              key={group.supplierId}
              style={{
                border: `1px solid ${colors.border}`,
                borderRadius: 12,
                backgroundColor: colors.cardBg,
                overflow: 'hidden',
              }}
            >
              {/* Supplier header */}
              <div
                style={{
                  padding: '10px 14px',
                  backgroundColor: colors.selectedBg,
                  borderBottom: `1px solid ${colors.border}`,
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <span
                  style={{
                    fontWeight: 600,
                    fontSize: '0.9rem',
                    color: colors.textPrimary,
                  }}
                >
                  {group.supplierName}
                </span>
                <span style={{ fontSize: '0.78rem', color: colors.textMuted }}>
                  {group.items.length} item{group.items.length !== 1 ? 's' : ''}
                </span>
              </div>

              {/* Line items */}
              {group.items.map((item) => (
                <div
                  key={item.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    padding: '8px 14px',
                    borderBottom: `1px solid ${colors.borderLight}`,
                    gap: 10,
                    fontSize: '0.88rem',
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontWeight: 500,
                        color: colors.textPrimary,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {item.name}
                    </div>
                    <div style={{ fontSize: '0.75rem', color: colors.textMuted }}>
                      {item.orderingUnitName} @ {formatCurrency(item.currentPrice)}
                    </div>
                  </div>

                  {/* Quantity controls */}
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                      flexShrink: 0,
                    }}
                  >
                    <button
                      onClick={() => updateQuantity(item.id, item.quantity - 1)}
                      disabled={item.quantity <= 1}
                      style={{
                        width: 28,
                        height: 28,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        border: `1px solid ${colors.border}`,
                        borderRadius: 6,
                        backgroundColor: colors.cardBg,
                        cursor: item.quantity <= 1 ? 'not-allowed' : 'pointer',
                        opacity: item.quantity <= 1 ? 0.4 : 1,
                        color: colors.textPrimary,
                      }}
                    >
                      <Minus size={14} />
                    </button>
                    <input
                      type="number"
                      value={item.quantity}
                      onChange={(e) => {
                        const val = parseInt(e.target.value, 10);
                        if (!isNaN(val) && val >= 1) updateQuantity(item.id, val);
                      }}
                      style={{
                        width: 48,
                        height: 28,
                        textAlign: 'center',
                        border: `1px solid ${colors.border}`,
                        borderRadius: 6,
                        fontSize: '0.85rem',
                        fontFamily: 'inherit',
                        color: colors.textPrimary,
                        outline: 'none',
                      }}
                    />
                    <button
                      onClick={() => updateQuantity(item.id, item.quantity + 1)}
                      style={{
                        width: 28,
                        height: 28,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        border: `1px solid ${colors.border}`,
                        borderRadius: 6,
                        backgroundColor: colors.cardBg,
                        cursor: 'pointer',
                        color: colors.textPrimary,
                      }}
                    >
                      <Plus size={14} />
                    </button>
                  </div>

                  {/* Line total */}
                  <span
                    style={{
                      width: 80,
                      textAlign: 'right',
                      fontWeight: 500,
                      color: colors.textPrimary,
                      flexShrink: 0,
                    }}
                  >
                    {formatCurrency(item.currentPrice * item.quantity)}
                  </span>

                  {/* Remove button */}
                  <button
                    onClick={() => removeItem(item.id)}
                    style={{
                      width: 28,
                      height: 28,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      border: 'none',
                      borderRadius: 6,
                      backgroundColor: 'transparent',
                      cursor: 'pointer',
                      color: colors.textMuted,
                      flexShrink: 0,
                    }}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}

              {/* Supplier footer totals */}
              <div
                style={{
                  padding: '10px 14px',
                  backgroundColor: colors.pageBg,
                  fontSize: '0.82rem',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 2,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: colors.textSecondary }}>Subtotal</span>
                  <span style={{ color: colors.textPrimary }}>{formatCurrency(group.subtotal)}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: colors.textSecondary }}>Tax</span>
                  <span style={{ color: colors.textPrimary }}>{formatCurrency(group.tax)}</span>
                </div>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    fontWeight: 600,
                    paddingTop: 4,
                    borderTop: `1px solid ${colors.border}`,
                  }}
                >
                  <span style={{ color: colors.textPrimary }}>Total</span>
                  <span style={{ color: colors.textPrimary }}>{formatCurrency(group.total)}</span>
                </div>
              </div>
            </div>
          ))}

          {/* Grand total and submit */}
          <div
            style={{
              border: `1px solid ${colors.border}`,
              borderRadius: 12,
              backgroundColor: colors.cardBg,
              padding: '14px',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <div>
              <div style={{ fontSize: '0.82rem', color: colors.textMuted }}>
                Grand Total ({orderItems.length} item{orderItems.length !== 1 ? 's' : ''} across{' '}
                {supplierGroups.length} supplier{supplierGroups.length !== 1 ? 's' : ''})
              </div>
              <div
                style={{
                  fontSize: '1.25rem',
                  fontWeight: 700,
                  color: colors.textPrimary,
                }}
              >
                {formatCurrency(grandTotal)}
              </div>
            </div>
            <button
              onClick={handleSubmit}
              disabled={submitting}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '12px 24px',
                fontSize: '0.95rem',
                fontWeight: 600,
                backgroundColor: submitting ? colors.textMuted : colors.primary,
                color: colors.textOnPrimary,
                border: 'none',
                borderRadius: 10,
                cursor: submitting ? 'not-allowed' : 'pointer',
                fontFamily: 'inherit',
              }}
            >
              <ShoppingCart size={18} />
              {submitting ? 'Submitting...' : 'Submit Orders'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
