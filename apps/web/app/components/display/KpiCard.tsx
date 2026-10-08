'use client';

import { Minus, TrendingDown, TrendingUp, TriangleAlert } from 'lucide-react';
import Icon from '../ui/Icon';

interface KpiCardProps {
  rows: Record<string, unknown>[];
  spec?: {
    value_key: string;
    // 'count' shows how many rows there are (open jobs, outstanding orders)
    // instead of summing value_key. Default 'sum'.
    aggregate?: 'sum' | 'count';
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
  if (!threshold) return 'var(--text)';
  const { warning, danger, direction } = threshold;
  if (direction === 'above') {
    if (val >= danger) return 'var(--error)';
    if (val >= warning) return 'var(--warn)';
    return 'var(--ok)';
  }
  // below
  if (val <= danger) return 'var(--error)';
  if (val <= warning) return 'var(--warn)';
  return 'var(--ok)';
}

const centred = {
  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
  height: '100%', textAlign: 'center',
} as const;

export default function KpiCard({ rows, spec, title }: KpiCardProps) {
  const countRows = spec?.aggregate === 'count';
  if ((!spec?.value_key && !countRows) || !rows || (rows.length === 0 && !countRows)) {
    return (
      <div style={{ ...centred, padding: '0.5rem', gap: 4, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        {title && <div style={{ fontWeight: 500 }}>{title}</div>}
        <div>No data</div>
      </div>
    );
  }

  const valueKey = spec?.value_key || '';
  const compKey = spec.comparison_key || spec.delta_key;
  const compLabel = spec.comparison_label || spec.delta_label;

  // Check if value_key actually exists in the data
  const keyExists = countRows || rows.some(r => valueKey in r);
  if (!keyExists) {
    const availableKeys = Object.keys(rows[0]).filter(k => !k.startsWith('_') && typeof rows[0][k] === 'number');
    // A plain sentence first; the field names under it are what whoever set
    // the dashboard up needs to fix it.
    return (
      <div style={{ ...centred, padding: '0.5rem 0.75rem', gap: 4 }}>
        {title && <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--muted)' }}>{title}</div>}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          <Icon icon={TriangleAlert} size="dense" tone="muted" />
          This figure can&apos;t be shown right now.
        </div>
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          The data has no &ldquo;{valueKey}&rdquo; field. Number fields: {availableKeys.join(', ') || 'none'}.
        </div>
      </div>
    );
  }

  // Get the value — use the last row (most recent) or sum if multiple
  const value = countRows
    ? rows.length
    : rows.length === 1
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
    <div style={{ ...centred, padding: '0.75rem' }}>
      {title && (
        <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--muted)', lineHeight: 1.3, marginBottom: 4 }}>
          {title}
        </div>
      )}
      <div style={{ fontSize: 'var(--fs-2xl)', fontWeight: 700, color, lineHeight: 1.2, fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.01em' }}>
        {formatValue(value, spec.format, spec.prefix, spec.suffix)}
      </div>
      {delta !== null && (
        <div style={{
          display: 'flex', alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center', gap: 4, marginTop: 6,
          fontSize: 'var(--fs-sm)', fontVariantNumeric: 'tabular-nums',
        }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 3, fontWeight: 600,
            color: delta > 0 ? 'var(--ok)' : delta < 0 ? 'var(--error)' : 'var(--muted)',
          }}>
            <Icon
              icon={delta > 0 ? TrendingUp : delta < 0 ? TrendingDown : Minus}
              size="dense"
              label={delta > 0 ? 'Up' : delta < 0 ? 'Down' : 'No change'}
            />
            {Math.abs(delta).toFixed(1)}%
          </span>
          {compLabel && <span style={{ color: 'var(--muted)' }}>{compLabel}</span>}
        </div>
      )}
    </div>
  );
}
