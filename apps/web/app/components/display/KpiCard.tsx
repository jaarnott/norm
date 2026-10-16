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
    // A ratio (cost of goods % = purchases ÷ sales): the two parts are summed
    // across rows FIRST, then divided — summing percentages per venue would be
    // wrong. The previous-period pair gives the comparison, shown as a change
    // in points.
    numerator_key?: string;
    denominator_key?: string;
    previous_numerator_key?: string;
    previous_denominator_key?: string;
    // Small print under the value ("included in purchases").
    note?: string;
    // Cost of goods: a rise is the bad direction.
    lower_is_better?: boolean;
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
  const isRatio = !!(spec?.numerator_key && spec?.denominator_key);
  if ((!spec?.value_key && !countRows && !isRatio) || !rows || (rows.length === 0 && !countRows)) {
    return (
      <div style={{ ...centred, padding: '0.5rem', gap: 4, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        {title && <div style={{ fontWeight: 500 }}>{title}</div>}
        <div>No data</div>
      </div>
    );
  }

  const valueKey = isRatio ? (spec.numerator_key as string) : (spec?.value_key || '');
  const compKey = spec.comparison_key || spec.delta_key;
  const compLabel = spec.comparison_label || spec.delta_label;

  // Check if value_key actually exists in the data
  const keyExists = countRows || rows.some(r => valueKey in r || valueKey.includes('.'));
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

  // "previous.purchases" reads inside a nested object (a tool's summary).
  const pick = (row: Record<string, unknown>, key: string): unknown => {
    if (key in row) return row[key];
    let cur: unknown = row;
    for (const part of key.split('.')) {
      if (!cur || typeof cur !== 'object') return undefined;
      cur = (cur as Record<string, unknown>)[part];
    }
    return cur;
  };
  const sumOf = (key: string) => rows.reduce((sum, r) => sum + Number(pick(r, key) || 0), 0);
  const ratioOf = (num?: string, den?: string): number | null => {
    if (!num || !den) return null;
    const d = sumOf(den);
    return d === 0 ? null : sumOf(num) / d;
  };

  // Get the value — the sum across rows (one row is just itself), or a ratio
  // of two sums.
  const ratio = isRatio ? ratioOf(spec.numerator_key, spec.denominator_key) : null;
  if (isRatio && ratio === null) {
    return (
      <div style={{ ...centred, padding: '0.5rem', gap: 4, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        {title && <div style={{ fontWeight: 500 }}>{title}</div>}
        <div>No sales in this period</div>
      </div>
    );
  }
  const value = isRatio
    ? (ratio as number)
    : countRows
      ? rows.length
      : sumOf(valueKey);

  const comparison = isRatio
    ? ratioOf(spec.previous_numerator_key, spec.previous_denominator_key)
    : compKey
      ? sumOf(compKey)
      : null;

  // A ratio's change is in points (24.6% → 22.3% is −2.3 pts); anything else
  // is relative to the comparison.
  const delta = comparison === null
    ? null
    : isRatio
      ? (value - comparison) * 100
      : comparison !== 0
        ? ((value - comparison) / Math.abs(comparison)) * 100
        : null;
  const deltaUnit = isRatio ? ' pts' : '%';

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
            color: delta === 0 ? 'var(--muted)' : (delta > 0) !== !!spec.lower_is_better ? 'var(--ok)' : 'var(--error)',
          }}>
            <Icon
              icon={delta > 0 ? TrendingUp : delta < 0 ? TrendingDown : Minus}
              size="dense"
              label={delta > 0 ? 'Up' : delta < 0 ? 'Down' : 'No change'}
            />
            {Math.abs(delta).toFixed(1)}{deltaUnit}
          </span>
          {compLabel && <span style={{ color: 'var(--muted)' }}>{compLabel}</span>}
        </div>
      )}
      {spec.note && (
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 6, lineHeight: 1.3 }}>
          {spec.note}
        </div>
      )}
    </div>
  );
}
