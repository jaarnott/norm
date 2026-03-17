'use client';

import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface LineItem {
  product: string;
  quantity: string;
  unit: string;
}

function extractLines(data: Record<string, unknown>): LineItem[] {
  // Look for a lines/items array
  const candidates = ['lines', 'items', 'lineItems', 'line_items', 'products'];
  let items: Record<string, unknown>[] = [];
  for (const key of candidates) {
    const val = data[key];
    if (Array.isArray(val) && val.length > 0) {
      items = val;
      break;
    }
  }
  // Fallback: find first array in data
  if (items.length === 0) {
    for (const val of Object.values(data)) {
      if (Array.isArray(val) && val.length > 0 && typeof val[0] === 'object') {
        items = val;
        break;
      }
    }
  }

  return items.map(item => ({
    product: String(item.description || item.productName || item.product_name || item.name || item.productCode || ''),
    quantity: String(item.qty || item.quantity || ''),
    unit: String(item.unit || 'case'),
  }));
}

export default function PurchaseOrder({ data, props }: DisplayBlockProps) {
  const title = (props?.title as string) || 'Order';
  const reference = String(data.orderReference || data.reference || data.order_id || data.id || '');
  const status = String(data.status || '');
  const supplier = String(data.supplier || data.supplierName || '');
  const venue = String(data.deliveryLocation || data.venue || data.location || '');
  const lines = extractLines(data);

  // For stock check results (no lines), show a simpler card
  const available = data.available;
  const productName = String(data.productName || data.product_name || data.description || '');

  if (!lines.length && available === undefined) return null;

  return (
    <div style={{
      marginBottom: '0.75rem',
      border: '1px solid #eee',
      borderRadius: 8,
      padding: '0.75rem',
      backgroundColor: '#fafafa',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'baseline',
        gap: '0.5rem',
        marginBottom: '0.5rem',
      }}>
        <span style={{
          fontSize: '0.72rem',
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          color: '#888',
        }}>
          {title}
        </span>
        {reference && (
          <span style={{
            fontSize: '0.75rem',
            fontWeight: 600,
            color: '#004085',
            backgroundColor: '#cce5ff',
            padding: '1px 6px',
            borderRadius: 3,
          }}>
            {String(reference)}
          </span>
        )}
        {status && (
          <span style={{
            fontSize: '0.7rem',
            fontWeight: 500,
            color: '#155724',
            backgroundColor: '#d4edda',
            padding: '1px 6px',
            borderRadius: 3,
          }}>
            {String(status)}
          </span>
        )}
      </div>

      {/* Meta */}
      {(supplier || venue) && (
        <div style={{ fontSize: '0.8rem', color: '#666', marginBottom: '0.4rem' }}>
          {supplier && <span>Supplier: <strong>{String(supplier)}</strong></span>}
          {supplier && venue && <span style={{ margin: '0 0.5rem' }}>|</span>}
          {venue && <span>Delivery: <strong>{String(venue)}</strong></span>}
        </div>
      )}

      {/* Stock check result */}
      {available !== undefined && (
        <div style={{ fontSize: '0.85rem', color: '#333' }}>
          {productName && <span style={{ fontWeight: 500 }}>{String(productName)}: </span>}
          <span style={{
            fontWeight: 600,
            color: available ? '#155724' : '#721c24',
          }}>
            {available ? 'In Stock' : 'Out of Stock'}
          </span>
        </div>
      )}

      {/* Line items */}
      {lines.length > 0 && (
        <table style={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: '0.82rem',
          lineHeight: 1.5,
        }}>
          <thead>
            <tr>
              {['Product', 'Qty', 'Unit'].map(h => (
                <th key={h} style={{
                  textAlign: 'left',
                  padding: '0.4rem 0.5rem',
                  borderBottom: '2px solid #e2e8f0',
                  fontWeight: 600,
                  color: '#555',
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={i}>
                <td style={{ padding: '0.35rem 0.5rem', borderBottom: '1px solid #eee', color: '#333' }}>{l.product}</td>
                <td style={{ padding: '0.35rem 0.5rem', borderBottom: '1px solid #eee', color: '#333', fontWeight: 500 }}>{l.quantity}</td>
                <td style={{ padding: '0.35rem 0.5rem', borderBottom: '1px solid #eee', color: '#888' }}>{l.unit}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
