'use client';

import { useState, useMemo, memo } from 'react';
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import type { ChartType, ChartSpec } from '../../types';
import { apiFetch } from '../../lib/api';
import { Maximize2, Plus, X } from 'lucide-react';
import KpiCard from './KpiCard';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';

// Series colours: one categorical palette, assigned in this order. Literal hex
// on purpose (chart palettes are the one place tokens don't apply). Warm-led to
// sit with the brand, and checked with the data-viz palette validator on the
// white chart surface: every colour >= 3:1 against white, OKLCH lightness and
// chroma in band, neighbours (including last -> first, for pie slices) apart by
// CVD dE >= 13 and normal-vision dE >= 18, and the first three apart from each
// other for 2–3-series charts. Re-run the validator before changing any value.
export const DEFAULT_COLORS = ['#b77f39', '#437eb9', '#246e3a', '#ab6595', '#846305', '#725195', '#179a8e', '#903f4f'];

// Stacked / venue breakdowns: the same palette, plus two more (also validated
// as neighbours) so a large group still gets a colour per venue.
export const STACK_COLORS = [...DEFAULT_COLORS, '#788a32', '#20609b'];

// Chart chrome in tokens. Recharts writes these into SVG attributes and inline
// styles, where CSS variables resolve.
const AXIS_STROKE = 'var(--line-strong)';
const AXIS_TICK = { fontSize: 'var(--fs-xs)', fill: 'var(--muted)' };
const GRID_STROKE = 'var(--line)';
const TOOLTIP_STYLE = {
  backgroundColor: 'var(--bg)', border: '1px solid var(--line-strong)', borderRadius: 'var(--radius)',
  padding: '8px 10px', fontSize: 'var(--fs-sm)', color: 'var(--text)',
};
const TOOLTIP_LABEL_STYLE = { color: 'var(--text)', fontWeight: 600, marginBottom: 2 };
// Item text stays in text colour (series colours are for marks, not words).
const TOOLTIP_ITEM_STYLE = { color: 'var(--text-soft)', paddingTop: 2, paddingBottom: 2 };
const LEGEND_STYLE = { fontSize: 'var(--fs-xs)' };
const legendLabel = (value: string) => <span style={{ color: 'var(--text-soft)' }}>{value}</span>;

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
      border: hideBorder ? '1px solid transparent' : '1px solid var(--line)',
      borderRadius: 'var(--radius-lg)', overflow: 'hidden', backgroundColor: 'var(--bg)',
      transition: 'border-color 0.15s',
      ...(fillContainer
        ? { height: '100%', display: 'flex', flexDirection: 'column' as const }
        : { marginTop: '0.5rem' }
      ),
    }}>
      {/* Header — hidden for KPI (KpiCard has its own title) */}
      {chartType !== 'kpi' && <div style={{
        display: 'flex', alignItems: 'center', gap: 8, minHeight: 32, padding: fillContainer ? '6px 14px 0' : '6px 8px 0 14px',
        justifyContent: fillContainer ? 'center' : 'space-between',
        position: fillContainer ? 'relative' : undefined,
      }}>
        <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)', lineHeight: 1.3, minWidth: 0 }}>{title}</span>
        <div className={fillContainer ? 'cell-chart-actions' : undefined} style={{
          display: 'flex', gap: 2, alignItems: 'center', flex: '0 0 auto',
          ...(fillContainer ? { position: 'absolute', right: '0.75rem' } : {}),
        }}>
          {!hideAddToReport && (
            <Button
              variant="quiet"
              size="sm"
              icon={addingToReport ? undefined : Plus}
              onClick={handleAddToReport}
              disabled={addingToReport}
              title="Add this chart to a report"
            >{addingToReport ? 'Adding…' : 'Report'}</Button>
          )}
          {onExpand && fillContainer && (
            <IconButton icon={Maximize2} label="Full screen" iconSize={16} onClick={onExpand} />
          )}
          {onRemove && (
            <IconButton icon={X} label="Remove from report" iconSize={16} onClick={onRemove} />
          )}
        </div>
      </div>}


      {/* Chart area */}
      <div style={{
        padding: '0.75rem',
        ...(fillContainer ? { flex: 1, minHeight: 0, overflow: 'hidden' } : {}),
      }}>
        {rows.length === 0 ? (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4,
            textAlign: 'center', padding: fillContainer ? '0.5rem' : '2rem 1rem', height: fillContainer ? '100%' : undefined,
            fontSize: 'var(--fs-base)', color: 'var(--text-soft)',
          }}>
            {/* A KPI tile has no header, so name the figure here. */}
            {chartType === 'kpi' && title && <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--muted)' }}>{title}</div>}
            <div>No data to show.</div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              {fillContainer
                ? 'The data source returned no rows — check the chart’s settings.'
                : 'The data source returned no rows — check the tool call in this conversation.'}
            </div>
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
              <div className="markdown-message" style={{ padding: '0.5rem', fontSize: 'var(--fs-base)', lineHeight: 1.6, color: 'var(--text)' }}>
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
  if (rows.length === 0) return <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>No data</div>;
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
    // Numbers line up on the right unless the field says otherwise.
    return typeof rows[0]?.[c] === 'number' ? 'right' : 'left';
  };
  return (
    <div style={{ overflow: 'auto', maxHeight: 300 }}>
      <table className="n-table">
        <thead>
          <tr>{columns.map(c => <th key={c} style={{ textAlign: getAlign(c) }}>{labels[c]}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>{columns.map(c => <td key={c} style={{ textAlign: getAlign(c) }}>{formatValue(row[c], fieldFormats?.[c])}</td>)}</tr>
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
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
        <XAxis dataKey={xKey} tickFormatter={v => formatValue(v, xFormat)} stroke={AXIS_STROKE} tick={AXIS_TICK} />
        <YAxis tickFormatter={v => formatValue(v, yFormat)} stroke={AXIS_STROKE} tick={AXIS_TICK} width="auto" />
        <Tooltip formatter={(v) => formatValue(v as number, yFormat)} labelFormatter={v => formatValue(v, xFormat)} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} itemStyle={TOOLTIP_ITEM_STYLE} cursor={{ fill: 'var(--surface-alt)' }} />
        <Legend wrapperStyle={LEGEND_STYLE} formatter={legendLabel} />
        {series.map(s => (
          <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color} stackId={stacked ? 'stack' : undefined} radius={stacked ? 0 : [4, 4, 0, 0]}
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
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
        <XAxis dataKey={xKey} tickFormatter={v => formatValue(v, xFormat)} stroke={AXIS_STROKE} tick={AXIS_TICK} />
        <YAxis tickFormatter={v => formatValue(v, yFormat)} stroke={AXIS_STROKE} tick={AXIS_TICK} width="auto" />
        <Tooltip formatter={(v) => formatValue(v as number, yFormat)} labelFormatter={v => formatValue(v, xFormat)} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} itemStyle={TOOLTIP_ITEM_STYLE} cursor={{ stroke: AXIS_STROKE }} />
        <Legend wrapperStyle={LEGEND_STYLE} formatter={legendLabel} />
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
          label={({ x, y, textAnchor, name, percent }) => (
            // Slice labels in text colour, not the slice's own colour.
            <text x={x} y={y} textAnchor={textAnchor} dominantBaseline="central" style={{ fill: 'var(--text-soft)', fontSize: 'var(--fs-xs)' }}>
              {`${name} ${((percent ?? 0) * 100).toFixed(0)}%`}
            </text>
          )}
          labelLine={false} stroke="var(--bg)"
          style={onSliceClick ? { cursor: 'pointer' } : undefined}
          onClick={onSliceClick ? (entry) => {
            if (entry?.name != null) {
              onSliceClick(String(entry.name), Number(entry.value || 0), entry as unknown as Record<string, unknown>);
            }
          } : undefined}
        >
          {pieData.map((_, i) => <Cell key={i} fill={DEFAULT_COLORS[i % DEFAULT_COLORS.length]} />)}
        </Pie>
        <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} itemStyle={TOOLTIP_ITEM_STYLE} />
      </PieChart>
    </ResponsiveContainer>
  );
}

function ScatterView({ rows, series, xKey, xLabel, chartHeight = 280 }: { rows: Record<string, unknown>[]; series: { key: string; label: string; color: string }[]; xKey: string; xLabel: string; chartHeight?: number | string }) {
  const yKey = series[0]?.key || '';
  return (
    <ResponsiveContainer width="100%" height={chartHeight as number}>
      <ScatterChart>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
        <XAxis dataKey={xKey} name={xLabel} stroke={AXIS_STROKE} tick={AXIS_TICK} />
        <YAxis dataKey={yKey} name={series[0]?.label || yKey} stroke={AXIS_STROKE} tick={AXIS_TICK} width="auto" />
        <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} itemStyle={TOOLTIP_ITEM_STYLE} cursor={{ stroke: AXIS_STROKE, strokeDasharray: '3 3' }} />
        <Scatter data={rows} fill={series[0]?.color || DEFAULT_COLORS[0]} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}
