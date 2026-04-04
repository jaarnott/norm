'use client';

interface KpiCardProps {
  rows: Record<string, unknown>[];
  spec?: {
    value_key: string;
    format?: 'number' | 'currency' | 'percent';
    prefix?: string;
    suffix?: string;
    comparison_key?: string;
    comparison_label?: string;
    // Aliases used by dashboard templates
    delta_key?: string;
    delta_label?: string;
    threshold?: { warning: number; danger: number; direction: 'above' | 'below' };
  };
  title?: string;
}

function formatValue(val: number, format?: string, prefix?: string, suffix?: string): string {
  let formatted: string;
  if (format === 'currency') {
    // Currency: 2 decimal places, thousand separators
    formatted = val.toLocaleString('en-NZ', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  } else if (format === 'percent') {
    formatted = (val * 100).toFixed(1);
  } else {
    // Number: no forced decimals — show up to 2 if present, none if whole
    formatted = Number.isInteger(val)
      ? val.toLocaleString('en-NZ')
      : val.toLocaleString('en-NZ', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  const pre = prefix ?? '';
  const suf = suffix ?? (format === 'percent' ? '%' : '');
  return `${pre}${formatted}${suf}`;
}

function getThresholdColor(val: number, threshold?: { warning: number; danger: number; direction: 'above' | 'below' }): string {
  if (!threshold) return '#1a1a1a';
  const { warning, danger, direction } = threshold;
  if (direction === 'above') {
    if (val >= danger) return '#dc3545';
    if (val >= warning) return '#f59e0b';
    return '#28a745';
  }
  // below
  if (val <= danger) return '#dc3545';
  if (val <= warning) return '#f59e0b';
  return '#28a745';
}

export default function KpiCard({ rows, spec, title }: KpiCardProps) {
  if (!spec?.value_key || !rows || rows.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#bbb', fontSize: '0.82rem' }}>
        No data
      </div>
    );
  }

  const valueKey = spec.value_key;
  const compKey = spec.comparison_key || spec.delta_key;
  const compLabel = spec.comparison_label || spec.delta_label;

  // Check if value_key actually exists in the data
  const keyExists = rows.some(r => valueKey in r);
  if (!keyExists) {
    const availableKeys = Object.keys(rows[0]).filter(k => !k.startsWith('_') && typeof rows[0][k] === 'number');
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', padding: '0.5rem', textAlign: 'center' }}>
        <div style={{ fontSize: '0.72rem', color: '#dc3545', fontWeight: 600, marginBottom: 4 }}>
          Field &quot;{valueKey}&quot; not found
        </div>
        <div style={{ fontSize: '0.62rem', color: '#999' }}>
          Available: {availableKeys.join(', ') || 'none'}
        </div>
      </div>
    );
  }

  // Get the value — use the last row (most recent) or sum if multiple
  const value = rows.length === 1
    ? Number(rows[0][valueKey] || 0)
    : rows.reduce((sum, r) => sum + Number(r[valueKey] || 0), 0);

  const comparison = compKey
    ? rows.length === 1
      ? Number(rows[0][compKey] || 0)
      : rows.reduce((sum, r) => sum + Number(r[compKey] || 0), 0)
    : null;

  const delta = comparison !== null && comparison !== 0
    ? ((value - comparison) / Math.abs(comparison)) * 100
    : null;

  const color = getThresholdColor(value, spec.threshold);

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center',
      height: '100%', padding: '0.75rem', textAlign: 'center',
    }}>
      {title && (
        <div style={{ fontSize: '0.7rem', fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '0.3rem' }}>
          {title}
        </div>
      )}
      <div style={{ fontSize: '2rem', fontWeight: 800, color, lineHeight: 1.1 }}>
        {formatValue(value, spec.format, spec.prefix, spec.suffix)}
      </div>
      {delta !== null && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 4, marginTop: '0.3rem',
          fontSize: '0.72rem', fontWeight: 500,
          color: delta > 0 ? '#28a745' : delta < 0 ? '#dc3545' : '#999',
        }}>
          <span>{delta > 0 ? '▲' : delta < 0 ? '▼' : '—'}</span>
          <span>{Math.abs(delta).toFixed(1)}%</span>
          {compLabel && <span style={{ color: '#bbb' }}>{compLabel}</span>}
        </div>
      )}
    </div>
  );
}
