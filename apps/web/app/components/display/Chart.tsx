'use client';

import { useState, useMemo, memo } from 'react';
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import type { ChartType, ChartSpec } from '../../types';
import { apiFetch } from '../../lib/api';
import { Maximize2 } from 'lucide-react';
import KpiCard from './KpiCard';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const DEFAULT_COLORS = ['#d4c4ae', '#a8cfc0', '#b8c8dc', '#e0c8a8', '#c8b8d4', '#a8d0b8', '#d8c0b8', '#b8d0d4'];

// More distinct palette for stacked/multi-series charts (venue breakdowns etc.)
const STACK_COLORS = ['#4f8a5e', '#5b8abd', '#c4a882', '#b07d4f', '#8b6caf', '#c75a5a', '#3d9e8f', '#d4a03c', '#7a8b5e', '#a05195'];

/** Auto-format values for display — detects ISO dates, formats numbers. */
interface FieldFormat {
  type?: string;       // time, date, datetime, currency, percent, number
  decimals?: number;   // decimal places (for number/currency)
  prefix?: string;
  suffix?: string;
  align?: 'left' | 'center' | 'right';
}

function formatValue(val: unknown, fmt?: string | FieldFormat): string {
  // Normalise: string shorthand → object
  const f: FieldFormat = typeof fmt === 'string' ? { type: fmt } : (fmt || {});
  const pre = f.prefix ?? '';
  const suf = f.suffix ?? '';

  if (typeof val === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(val)) {
    const d = new Date(val);
    if (f.type === 'time') return `${pre}${d.toLocaleTimeString('en-NZ', { hour: '2-digit', minute: '2-digit' })}${suf}`;
    if (f.type === 'datetime') return `${pre}${d.toLocaleString('en-NZ', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}${suf}`;
    if (f.type === 'date') return `${pre}${d.toLocaleDateString('en-NZ', { weekday: 'short', day: 'numeric', month: 'short' })}${suf}`;
    return d.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric', month: 'short' });
  }
  if (typeof val === 'number') {
    const dec = f.decimals;
    if (f.type === 'currency') {
      const opts = dec !== undefined
        ? { minimumFractionDigits: dec, maximumFractionDigits: dec }
        : { minimumFractionDigits: 2, maximumFractionDigits: 2 };
      return `${pre || '$'}${val.toLocaleString('en-NZ', opts)}${suf}`;
    }
    if (f.type === 'percent') {
      const pct = dec !== undefined ? (val * 100).toFixed(dec) : (val * 100).toFixed(1);
      return `${pre}${pct}${suf || '%'}`;
    }
    if (f.type === 'number' || dec !== undefined) {
      const opts = dec !== undefined
        ? { minimumFractionDigits: dec, maximumFractionDigits: dec }
        : {};
      return `${pre}${val.toLocaleString('en-NZ', opts)}${suf}`;
    }
    return `${pre}${val.toLocaleString()}${suf}`;
  }
  return `${pre}${String(val ?? '')}${suf}`;
}

/** Get a display label for a field, using field_labels if available. */
function getLabel(key: string, fieldLabels?: Record<string, string>): string {
  if (fieldLabels && fieldLabels[key]) return fieldLabels[key];
  return key;
}

interface Props {
  data: { rows?: Record<string, unknown>[]; script?: Record<string, unknown> };
  props?: Partial<ChartSpec> & { field_labels?: Record<string, string> };
  onAction?: (action: { connector_name: string; action: string; params: Record<string, unknown> }) => Promise<Record<string, unknown> | void>;
  threadId?: string;
  height?: number;
  hideAddToReport?: boolean;
  onRemove?: () => void;
  onExpand?: () => void;
  onDrillDown?: (payload: { label: string; value: number; field: string; row: Record<string, unknown> }) => void;
  fillContainer?: boolean;
  hideBorder?: boolean;
  className?: string;
}

function Chart({ data, props: chartProps, onAction, threadId, height: chartHeight = 280, hideAddToReport, onRemove, onExpand, onDrillDown, fillContainer, hideBorder, className }: Props) {
  const [chartType, setChartType] = useState<ChartType>(chartProps?.chart_type || 'bar');
  const [addingToReport, setAddingToReport] = useState(false);
  const fieldLabels = chartProps?.field_labels || {};

  const rows = useMemo(() => {
    // Try data.rows first (standard chart format)
    if (Array.isArray(data?.rows) && data.rows.length > 0) return data.rows as Record<string, unknown>[];
    // Fallback: look for any array in the data object
    if (data && typeof data === 'object') {
      for (const key of ['data', 'items', 'lines', 'results', 'rows']) {
        const val = (data as Record<string, unknown>)[key];
        if (Array.isArray(val) && val.length > 0) return val as Record<string, unknown>[];
      }
    }
    return [];
  }, [data]);
  const series = useMemo(() => {
    const s = chartProps?.series || [];
    return s.map((item, i) => ({
      ...item,
      color: item.color || DEFAULT_COLORS[i % DEFAULT_COLORS.length],
    }));
  }, [chartProps]);

  // Auto-detect keys if configured keys don't exist in the data
  const dataKeys = rows.length > 0 ? Object.keys(rows[0]) : [];

  let xKey = chartProps?.x_axis?.key || '';
  if (xKey && rows.length > 0 && !(xKey in rows[0])) {
    // Configured x key doesn't exist — try to find a date/time/name column
    const fallback = dataKeys.find(k => /date|time|day|month|year|name|label|period/i.test(k));
    if (fallback) xKey = fallback;
  }
  if (!xKey && dataKeys.length > 0) xKey = dataKeys[0];

  const xLabel = chartProps?.x_axis?.label || getLabel(xKey, fieldLabels);
  const xFormat = chartProps?.x_axis?.format as string | undefined;
  const yFormat = chartProps?.y_axis?.format as string | undefined;
  const title = chartProps?.title ?? 'Chart';

  // If configured series keys don't match data, auto-detect numeric columns
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const effectiveSeries = useMemo(() => {
    let result: { key: string; label: string; color: string }[];
    if (series.length > 0 && rows.length > 0) {
      const validSeries = series.filter(s => s.key in rows[0]);
      result = validSeries.length > 0 ? validSeries : [];
    } else {
      result = [];
    }
    // Fallback: use all numeric columns except the x key
    if (result.length === 0 && rows.length > 0) {
      result = dataKeys
        .filter(k => k !== xKey && typeof rows[0][k] === 'number')
        .map((k, i) => ({ key: k, label: getLabel(k, fieldLabels), color: DEFAULT_COLORS[i % DEFAULT_COLORS.length] }));
    }
    // Apply field labels
    result = result.map(s => ({
      ...s,
      label: s.label && s.label !== s.key ? s.label : getLabel(s.key, fieldLabels),
    }));
    return result;
  }, [series, rows, dataKeys, xKey, fieldLabels]);

  // Aggregate rows when there are duplicate x-axis values (e.g., multi-venue data).
  // - bar/line: group by xKey, sum numeric series values
  // - stacked_bar: pivot a grouping column (e.g. "venue") into separate series
  const { chartRows, chartSeries } = useMemo(() => {
    if (rows.length === 0 || !xKey) return { chartRows: rows, chartSeries: effectiveSeries };

    // Check if there are duplicate x-axis values
    const xValues = rows.map(r => String(r[xKey] ?? ''));
    const hasDuplicates = new Set(xValues).size < xValues.length;
    if (!hasDuplicates) return { chartRows: rows, chartSeries: effectiveSeries };

    // Use explicit group_by from chart spec, or auto-detect a grouping column
    const specGroupBy = chartProps?.group_by as string | undefined;
    const groupCol = specGroupBy || dataKeys.find(k => /^venue$|^venue_name$|^location$/i.test(k));
    const specValueKey = chartProps?.value_key as string | undefined;

    if (chartType === 'stacked_bar' && groupCol) {
      // Pivot: one row per x-value, one series column per group
      const groups = [...new Set(rows.map(r => String(r[groupCol] ?? 'Other')))];
      const valueKey = specValueKey || effectiveSeries[0]?.key || '';
      const grouped = new Map<string, Record<string, unknown>>();
      for (const row of rows) {
        const xVal = String(row[xKey] ?? '');
        const group = String(row[groupCol] ?? 'Other');
        if (!grouped.has(xVal)) grouped.set(xVal, { [xKey]: row[xKey] });
        const entry = grouped.get(xVal)!;
        entry[group] = Number(entry[group] || 0) + Number(row[valueKey] || 0);
      }
      // Use configured series colours if they match, otherwise generate
      const configuredSeries = (chartProps?.series as { key: string; label: string; color: string }[]) || [];
      const configMap = new Map(configuredSeries.map(s => [s.key, s]));
      const pivotedSeries = groups.map((g, i) => {
        const existing = configMap.get(g);
        return {
          key: g,
          label: existing?.label || g,
          color: existing?.color || STACK_COLORS[i % STACK_COLORS.length],
        };
      });
      return { chartRows: Array.from(grouped.values()), chartSeries: pivotedSeries };
    }

    // Default: aggregate by summing numeric values per x-key
    const grouped = new Map<string, Record<string, unknown>>();
    for (const row of rows) {
      const xVal = String(row[xKey] ?? '');
      if (!grouped.has(xVal)) {
        grouped.set(xVal, { ...row });
      } else {
        const entry = grouped.get(xVal)!;
        for (const s of effectiveSeries) {
          entry[s.key] = Number(entry[s.key] || 0) + Number(row[s.key] || 0);
        }
      }
    }
    return { chartRows: Array.from(grouped.values()), chartSeries: effectiveSeries };
  }, [rows, xKey, chartType, effectiveSeries, dataKeys]);

  const handleAddToReport = async () => {
    setAddingToReport(true);
    try {
      // Check if a report builder is already open
      let reportId: string | null = null;
      if (onAction) {
        try {
          const result = await onAction({
            connector_name: 'norm_reports',
            action: 'get_active_report',
            params: {},
          });
          if (result && (result as Record<string, unknown>).report_id) {
            reportId = (result as Record<string, unknown>).report_id as string;
          }
        } catch { /* ignore */ }
      }

      // No active report open — create a new one
      if (!reportId) {
        const createRes = await apiFetch('/api/reports', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: title || 'New Report' }),
        });
        const report = await createRes.json();
        reportId = report.id;
      }

      // Add this chart to the report
      await apiFetch(`/api/reports/${reportId}/charts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          chart_type: chartType,
          chart_spec: { x_axis: chartProps?.x_axis, series: effectiveSeries, orientation: chartProps?.orientation },
          data: rows,
          script: data?.script || {},
          source_thread_id: threadId,
        }),
      });

      // Tell parent to open the Report Builder
      if (onAction && reportId) {
        await onAction({
          connector_name: 'norm_reports',
          action: 'open_report_builder',
          params: { report_id: reportId },
        });
      }
    } catch (e) {
      console.error('Failed to add to report:', e);
    }
    setAddingToReport(false);
  };

  return (
    <div className={className} style={{
      border: hideBorder ? '1px solid transparent' : '1px solid #e2e8f0',
      borderRadius: 10, overflow: 'hidden', backgroundColor: '#fff',
      transition: 'border-color 0.15s',
      ...(fillContainer
        ? { height: '100%', display: 'flex', flexDirection: 'column' as const }
        : { marginTop: '0.5rem' }
      ),
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', padding: '0.5rem 0.75rem',
        justifyContent: fillContainer ? 'center' : 'space-between',
        position: fillContainer ? 'relative' : undefined,
      }}>
        <span style={{ fontSize: '0.82rem', fontWeight: 600, color: '#333' }}>{title}</span>
        <div className={fillContainer ? 'cell-chart-actions' : undefined} style={{
          display: 'flex', gap: 3, alignItems: 'center',
          ...(fillContainer ? { position: 'absolute', right: '0.75rem' } : {}),
        }}>
          {!hideAddToReport && (
            <button
              onClick={handleAddToReport}
              disabled={addingToReport}
              onMouseEnter={e => (e.currentTarget.style.color = '#999')}
              onMouseLeave={e => (e.currentTarget.style.color = '#ccc')}
              style={{
                padding: '3px 10px', fontSize: '0.72rem', fontWeight: 600,
                border: 'none', borderRadius: 4, backgroundColor: 'transparent',
                color: '#ccc', cursor: addingToReport ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
                transition: 'color 0.15s',
              }}
            >{addingToReport ? 'Adding...' : '+ Report'}</button>
          )}
          {onExpand && fillContainer && (
            <button
              onClick={onExpand}
              onMouseEnter={e => (e.currentTarget.style.color = '#999')}
              onMouseLeave={e => (e.currentTarget.style.color = '#ccc')}
              style={{
                padding: '3px 6px', border: 'none', borderRadius: 4, backgroundColor: 'transparent',
                color: '#ccc', cursor: 'pointer', lineHeight: 1, display: 'flex', alignItems: 'center',
                transition: 'color 0.15s',
              }}
              title="Full screen"
            ><Maximize2 size={14} strokeWidth={1.75} /></button>
          )}
          {onRemove && (
            <button
              onClick={onRemove}
              onMouseEnter={e => (e.currentTarget.style.color = '#e53e3e')}
              onMouseLeave={e => (e.currentTarget.style.color = '#ccc')}
              style={{
                padding: '3px 6px', fontSize: '0.85rem',
                border: 'none', borderRadius: 4, backgroundColor: 'transparent',
                color: '#ccc', cursor: 'pointer', lineHeight: 1,
                transition: 'color 0.15s',
              }}
              title="Remove from report"
            >&times;</button>
          )}
        </div>
      </div>


      {/* Chart area */}
      <div style={{ padding: '0.75rem', ...(fillContainer ? { flex: 1, minHeight: 0 } : {}) }}>
        {rows.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '2rem', color: '#999', fontSize: '0.85rem' }}>
            No data to display. Open the inspector <span style={{ fontFamily: 'monospace' }}>{'{}'}</span> to debug.
          </div>
        ) : (
          <>
            {chartType === 'table' && <TableView rows={rows} series={effectiveSeries} xKey={xKey}
              fieldLabels={chartProps?.field_labels as Record<string, string> | undefined}
              hiddenFields={chartProps?.hidden_fields ? new Set(chartProps.hidden_fields as string[]) : undefined}
              fieldFormats={chartProps?.field_formats as Record<string, string | FieldFormat> | undefined}
              fieldOrder={chartProps?.field_order as string[] | undefined}
            />}
            {chartType === 'bar' && <BarView rows={chartRows} series={chartSeries} xKey={xKey} xLabel={xLabel} xFormat={xFormat} yFormat={yFormat} chartHeight={fillContainer ? '100%' : chartHeight} onBarClick={onDrillDown ? (data, field) => onDrillDown({ label: String(data[xKey] || ''), value: Number(data[field] || 0), field, row: data }) : undefined} />}
            {chartType === 'stacked_bar' && <BarView rows={chartRows} series={chartSeries} xKey={xKey} xLabel={xLabel} xFormat={xFormat} yFormat={yFormat} stacked chartHeight={fillContainer ? '100%' : chartHeight} onBarClick={onDrillDown ? (data, field) => onDrillDown({ label: String(data[xKey] || ''), value: Number(data[field] || 0), field, row: data }) : undefined} />}
            {chartType === 'line' && <LineView rows={chartRows} series={chartSeries} xKey={xKey} xLabel={xLabel} xFormat={xFormat} yFormat={yFormat} chartHeight={fillContainer ? '100%' : chartHeight} onDotClick={onDrillDown ? (data, field) => onDrillDown({ label: String(data[xKey] || ''), value: Number(data[field] || 0), field, row: data }) : undefined} />}
            {chartType === 'pie' && <PieView rows={rows} series={effectiveSeries} xKey={xKey} chartHeight={fillContainer ? '100%' : chartHeight} onSliceClick={onDrillDown ? (label, value, row) => onDrillDown({ label, value, field: effectiveSeries[0]?.key || '', row }) : undefined} />}
            {chartType === 'scatter' && <ScatterView rows={rows} series={effectiveSeries} xKey={xKey} xLabel={xLabel} chartHeight={fillContainer ? '100%' : chartHeight} />}
            {chartType === 'kpi' && <KpiCard rows={rows} spec={(() => {
              const cp = chartProps as Record<string, unknown>;
              const nested = cp?.kpi_spec as Record<string, unknown> | undefined;
              // Merge: root-level fields win over nested kpi_spec
              return { ...(nested || {}), ...cp } as Parameters<typeof KpiCard>[0]['spec'];
            })()} title={title} />}
            {chartType === 'text' && (
              <div className="markdown-message" style={{ padding: '0.5rem', fontSize: '0.85rem', lineHeight: 1.6 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{String((chartProps as Record<string, unknown>)?.text_content || rows[0]?.text || '')}</ReactMarkdown>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Color picker — brand palette dropdown + custom option
// ---------------------------------------------------------------------------
// Sub-views
// ---------------------------------------------------------------------------

function TableView({ rows, series, xKey, fieldLabels, hiddenFields, fieldFormats, fieldOrder }: {
  rows: Record<string, unknown>[]; series: { key: string; label: string }[]; xKey: string;
  fieldLabels?: Record<string, string>; hiddenFields?: Set<string>;
  fieldFormats?: Record<string, string | FieldFormat>; fieldOrder?: string[];
}) {
  if (rows.length === 0) return <div style={{ color: '#999', fontSize: '0.8rem' }}>No data</div>;
  // Use all data keys, filtering out hidden ones, respecting field_order
  const allKeys = rows.length > 0 ? Object.keys(rows[0]) : [];
  const visible = allKeys.filter(k => !k.startsWith('_') && !(hiddenFields?.has(k)));
  const columns = fieldOrder && fieldOrder.length > 0
    ? [...fieldOrder.filter(k => visible.includes(k)), ...visible.filter(k => !fieldOrder.includes(k))]
    : visible;
  const labels: Record<string, string> = {};
  columns.forEach(c => {
    labels[c] = fieldLabels?.[c] || series.find(s => s.key === c)?.label || c;
  });
  const getAlign = (c: string): 'left' | 'center' | 'right' => {
    const ff = fieldFormats?.[c];
    if (ff && typeof ff === 'object' && ff.align) return ff.align;
    return 'left';
  };
  return (
    <div style={{ overflow: 'auto', maxHeight: 300 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
        <thead>
          <tr>{columns.map(c => <th key={c} style={{ textAlign: getAlign(c), padding: '4px 8px', borderBottom: '1px solid #eee', fontWeight: 600, color: '#555' }}>{labels[c]}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>{columns.map(c => <td key={c} style={{ textAlign: getAlign(c), padding: '4px 8px', borderBottom: '1px solid #f5f5f5', color: '#333' }}>{formatValue(row[c], fieldFormats?.[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default memo(Chart);

function BarView({ rows, series, xKey, xLabel, xFormat, yFormat, stacked, chartHeight = 280, onBarClick }: { rows: Record<string, unknown>[]; series: { key: string; label: string; color: string }[]; xKey: string; xLabel: string; xFormat?: string; yFormat?: string; stacked?: boolean; chartHeight?: number | string; onBarClick?: (data: Record<string, unknown>, seriesKey: string) => void }) {
  return (
    <ResponsiveContainer width="100%" height={chartHeight as number}>
      <BarChart data={rows}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis dataKey={xKey} tickFormatter={v => formatValue(v, xFormat)} tick={{ fontSize: 11 }} />
        <YAxis tickFormatter={v => formatValue(v, yFormat)} tick={{ fontSize: 11 }} />
        <Tooltip formatter={(v) => formatValue(v as number, yFormat)} labelFormatter={v => formatValue(v, xFormat)} contentStyle={{ fontSize: '0.78rem' }} />
        <Legend wrapperStyle={{ fontSize: '0.75rem' }} />
        {series.map(s => (
          <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color} stackId={stacked ? 'stack' : undefined} radius={stacked ? 0 : [3, 3, 0, 0]}
            cursor={onBarClick ? 'pointer' : undefined}
            onClick={onBarClick ? (data: unknown) => onBarClick(data as Record<string, unknown>, s.key) : undefined}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function LineView({ rows, series, xKey, xLabel, xFormat, yFormat, chartHeight = 280, onDotClick }: { rows: Record<string, unknown>[]; series: { key: string; label: string; color: string }[]; xKey: string; xLabel: string; xFormat?: string; yFormat?: string; chartHeight?: number | string; onDotClick?: (data: Record<string, unknown>, field: string) => void }) {
  return (
    <ResponsiveContainer width="100%" height={chartHeight as number}>
      <LineChart
        data={rows}
        onClick={onDotClick ? (state: unknown) => {
          const s = state as { activePayload?: { payload: Record<string, unknown>; dataKey: string }[] } | null;
          if (s?.activePayload?.[0]?.payload) {
            onDotClick(s.activePayload[0].payload, s.activePayload[0].dataKey);
          }
        } : undefined}
        style={onDotClick ? { cursor: 'pointer' } : undefined}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis dataKey={xKey} tickFormatter={v => formatValue(v, xFormat)} tick={{ fontSize: 11 }} />
        <YAxis tickFormatter={v => formatValue(v, yFormat)} tick={{ fontSize: 11 }} />
        <Tooltip formatter={(v) => formatValue(v as number, yFormat)} labelFormatter={v => formatValue(v, xFormat)} contentStyle={{ fontSize: '0.78rem' }} />
        <Legend wrapperStyle={{ fontSize: '0.75rem' }} />
        {series.map(s => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2} dot={{ r: 3 }} activeDot={onDotClick ? { r: 5, cursor: 'pointer' } : { r: 4 }} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function PieView({ rows, series, xKey, chartHeight = 280, onSliceClick }: { rows: Record<string, unknown>[]; series: { key: string; label: string; color: string }[]; xKey: string; chartHeight?: number | string; onSliceClick?: (label: string, value: number, row: Record<string, unknown>) => void }) {
  const dataKey = series[0]?.key || '';
  // Aggregate rows by x-axis key (e.g., sum all "Food" rows and all "Beverage" rows)
  const aggregated = new Map<string, number>();
  for (const r of rows) {
    const key = formatValue(r[xKey] || 'Other');
    aggregated.set(key, (aggregated.get(key) || 0) + Number(r[dataKey] || 0));
  }
  const pieData = Array.from(aggregated, ([name, value]) => ({ name, value }));
  return (
    <ResponsiveContainer width="100%" height={chartHeight as number}>
      <PieChart>
        <Pie
          data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius="70%"
          label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`} labelLine={false}
          style={onSliceClick ? { cursor: 'pointer' } : undefined}
          onClick={onSliceClick ? (entry) => {
            if (entry?.name != null) {
              onSliceClick(String(entry.name), Number(entry.value || 0), entry as unknown as Record<string, unknown>);
            }
          } : undefined}
        >
          {pieData.map((_, i) => <Cell key={i} fill={DEFAULT_COLORS[i % DEFAULT_COLORS.length]} />)}
        </Pie>
        <Tooltip contentStyle={{ fontSize: '0.78rem' }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

function ScatterView({ rows, series, xKey, xLabel, chartHeight = 280 }: { rows: Record<string, unknown>[]; series: { key: string; label: string; color: string }[]; xKey: string; xLabel: string; chartHeight?: number | string }) {
  const yKey = series[0]?.key || '';
  return (
    <ResponsiveContainer width="100%" height={chartHeight as number}>
      <ScatterChart>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis dataKey={xKey} name={xLabel} tick={{ fontSize: 11 }} />
        <YAxis dataKey={yKey} name={series[0]?.label || yKey} tick={{ fontSize: 11 }} />
        <Tooltip contentStyle={{ fontSize: '0.78rem' }} />
        <Scatter data={rows} fill={series[0]?.color || DEFAULT_COLORS[0]} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}
