'use client';

import { useState, useMemo, memo } from 'react';
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import type { ChartType, ChartSpec } from '../../types';
import { apiFetch } from '../../lib/api';
import { Settings } from 'lucide-react';

const CHART_TYPES: { type: ChartType; label: string; icon: string }[] = [
  { type: 'table', label: 'Table', icon: '▤' },
  { type: 'bar', label: 'Bar', icon: '▮' },
  { type: 'stacked_bar', label: 'Stacked', icon: '▦' },
  { type: 'line', label: 'Line', icon: '⟋' },
  { type: 'pie', label: 'Pie', icon: '◔' },
  { type: 'scatter', label: 'Scatter', icon: '⁘' },
];

const DEFAULT_COLORS = ['#d4c4ae', '#a8cfc0', '#b8c8dc', '#e0c8a8', '#c8b8d4', '#a8d0b8', '#d8c0b8', '#b8d0d4'];

const BRAND_COLORS: { name: string; value: string }[] = [
  { name: 'Sand', value: '#d4c4ae' },
  { name: 'Mint', value: '#a8cfc0' },
  { name: 'Sky', value: '#b8c8dc' },
  { name: 'Cream', value: '#e0c8a8' },
  { name: 'Lavender', value: '#c8b8d4' },
  { name: 'Sage', value: '#a8d0b8' },
  { name: 'Blush', value: '#d8c0b8' },
  { name: 'Duck Egg', value: '#b8d0d4' },
  { name: 'Gold', value: '#c4a882' },
  { name: 'Taupe', value: '#a08060' },
];

/** Auto-format values for display — detects ISO dates, formats numbers. */
function formatValue(val: unknown): string {
  if (typeof val === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(val)) {
    const d = new Date(val);
    return d.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric', month: 'short' });
  }
  if (typeof val === 'number') return val.toLocaleString();
  return String(val ?? '');
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
  fillContainer?: boolean;
  hideBorder?: boolean;
  className?: string;
}

function Chart({ data, props: chartProps, onAction, threadId, height: chartHeight = 280, hideAddToReport, onRemove, fillContainer, hideBorder, className }: Props) {
  const [chartType, setChartType] = useState<ChartType>(chartProps?.chart_type || 'bar');
  const [addingToReport, setAddingToReport] = useState(false);
  const [showInspector, setShowInspector] = useState(false);
  const [showEditor, setShowEditor] = useState(false);
  const [editTitle, setEditTitle] = useState<string | null>(null);
  const [editLabels, setEditLabels] = useState<Record<string, string>>(chartProps?.field_labels || {});
  const [hiddenFields, setHiddenFields] = useState<Set<string>>(new Set());
  const [editColors, setEditColors] = useState<Record<string, string>>({});
  const fieldLabels = editLabels;

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
  const title = editTitle ?? chartProps?.title ?? 'Chart';

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
    // Apply field labels and custom colors
    result = result.map(s => ({
      ...s,
      label: s.label && s.label !== s.key ? s.label : getLabel(s.key, fieldLabels),
      color: editColors[s.key] || s.color,
    }));
    // Filter out hidden fields
    if (hiddenFields.size > 0) {
      result = result.filter(s => !hiddenFields.has(s.key));
    }
    return result;
  }, [series, rows, dataKeys, xKey, fieldLabels, hiddenFields, editColors]);

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

  const btnStyle = (active: boolean): React.CSSProperties => ({
    padding: '3px 8px', fontSize: '0.72rem', fontWeight: active ? 700 : 500,
    border: active ? '1px solid #4d65ff' : '1px solid #ddd', borderRadius: 4,
    backgroundColor: active ? '#eef' : '#fff', color: active ? '#4d65ff' : '#888',
    cursor: 'pointer', fontFamily: 'inherit',
  });

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
          <button
            onClick={() => setShowEditor(!showEditor)}
            onMouseEnter={e => { if (!showEditor) e.currentTarget.style.color = '#999'; }}
            onMouseLeave={e => { if (!showEditor) e.currentTarget.style.color = '#ccc'; }}
            style={{
              padding: '3px 6px',
              border: 'none', borderRadius: 4, backgroundColor: 'transparent',
              color: showEditor ? '#c4a882' : '#ccc',
              cursor: 'pointer', lineHeight: 1, display: 'flex', alignItems: 'center',
              transition: 'color 0.15s',
            }}
            title="Edit chart"
          ><Settings size={15} strokeWidth={1.75} /></button>
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

      {/* Editor panel */}
      {showEditor && (
        <div style={{ borderBottom: '1px solid #f0f0f0', padding: '0.5rem 0.75rem', backgroundColor: '#f9fdf9', fontSize: '0.75rem', position: 'relative' }}>
          {/* Inspector toggle — top right of edit panel */}
          <button
            onClick={() => setShowInspector(!showInspector)}
            style={{
              position: 'absolute', top: 6, right: 8,
              padding: '2px 6px', fontSize: '0.68rem', fontWeight: showInspector ? 700 : 500,
              border: showInspector ? '1px solid #ed8936' : '1px solid #ddd', borderRadius: 3,
              backgroundColor: showInspector ? '#fffaf0' : '#fff', color: showInspector ? '#ed8936' : '#aaa',
              cursor: 'pointer', fontFamily: 'inherit',
            }}
            title="Inspect data"
          >{'{}'}</button>
          <div style={{ marginBottom: 6 }}>
            <label style={{ fontWeight: 600, color: '#555', fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Chart Type</label>
            <div style={{ display: 'flex', gap: 3, marginTop: 3 }}>
              {CHART_TYPES.map(ct => (
                <button key={ct.type} onClick={() => setChartType(ct.type)} style={btnStyle(chartType === ct.type)} title={ct.label}>
                  {ct.icon} <span style={{ marginLeft: 2 }}>{ct.label}</span>
                </button>
              ))}
            </div>
          </div>
          <div style={{ marginBottom: 6 }}>
            <label style={{ fontWeight: 600, color: '#555', fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Title</label>
            <input
              value={title}
              onChange={e => setEditTitle(e.target.value)}
              style={{ display: 'block', width: '100%', padding: '3px 6px', border: '1px solid #ddd', borderRadius: 4, fontSize: '0.78rem', fontFamily: 'inherit', marginTop: 2 }}
            />
          </div>
          <div>
            <label style={{ fontWeight: 600, color: '#555', fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Fields</label>
            <div style={{ marginTop: 4 }}>
              {dataKeys.map(key => {
                const isX = key === xKey;
                const isHidden = hiddenFields.has(key);
                return (
                  <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                    <input
                      type="checkbox"
                      checked={!isHidden}
                      onChange={e => {
                        const next = new Set(hiddenFields);
                        if (e.target.checked) next.delete(key); else next.add(key);
                        setHiddenFields(next);
                      }}
                      style={{ margin: 0 }}
                      disabled={isX}
                    />
                    <span style={{ fontFamily: 'monospace', color: '#888', fontSize: '0.72rem', width: 100 }}>{key}{isX ? ' (x)' : ''}</span>
                    <input
                      value={editLabels[key] || ''}
                      onChange={e => setEditLabels(prev => ({ ...prev, [key]: e.target.value }))}
                      placeholder={key}
                      style={{ flex: 1, padding: '2px 4px', border: '1px solid #e2e8f0', borderRadius: 3, fontSize: '0.72rem', fontFamily: 'inherit' }}
                    />
                    {!isX && (
                      <ColorPicker
                        value={editColors[key] || effectiveSeries.find(s => s.key === key)?.color || DEFAULT_COLORS[0]}
                        onChange={color => setEditColors(prev => ({ ...prev, [key]: color }))}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Data inspector */}
      {showInspector && (
        <div style={{ borderBottom: '1px solid #f0f0f0', padding: '0.5rem 0.75rem', backgroundColor: '#fafafa', maxHeight: 300, overflow: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', fontSize: '0.72rem' }}>
            <div>
              <div style={{ fontWeight: 600, color: '#888', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                Chart Config
              </div>
              <pre style={{ margin: 0, backgroundColor: '#1e1e2e', color: '#cdd6f4', padding: '0.4rem', borderRadius: 4, fontSize: '0.7rem', overflow: 'auto', maxHeight: 120 }}>
                {JSON.stringify({
                  chart_type: chartType,
                  x_axis: { key: xKey, label: xLabel },
                  configured_series: chartProps?.series,
                  effective_series: effectiveSeries,
                  data_columns: dataKeys,
                  orientation: chartProps?.orientation,
                }, null, 2)}
              </pre>
              {data?.script && Object.keys(data.script).length > 0 && (
                <>
                  <div style={{ fontWeight: 600, color: '#888', marginBottom: 3, marginTop: 6, textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                    Script (refresh recipe)
                  </div>
                  <pre style={{ margin: 0, backgroundColor: '#1e1e2e', color: '#cdd6f4', padding: '0.4rem', borderRadius: 4, fontSize: '0.7rem', overflow: 'auto', maxHeight: 80 }}>
                    {JSON.stringify(data.script, null, 2)}
                  </pre>
                </>
              )}
            </div>
            <div>
              <div style={{ fontWeight: 600, color: '#888', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                Data ({rows.length} rows){rows.length === 0 && <span style={{ color: '#e53e3e', fontWeight: 400 }}> — no data returned</span>}
              </div>
              <pre style={{ margin: 0, backgroundColor: '#1e1e2e', color: '#cdd6f4', padding: '0.4rem', borderRadius: 4, fontSize: '0.7rem', overflow: 'auto', maxHeight: 200 }}>
                {JSON.stringify(rows.length > 10 ? [...rows.slice(0, 10), `... ${rows.length - 10} more`] : rows, null, 2)}
              </pre>
            </div>
          </div>
          <div style={{ marginTop: 4, fontSize: '0.65rem', color: '#bbb' }}>
            Raw display block: data keys = [{Object.keys(data || {}).join(', ')}], props keys = [{Object.keys(chartProps || {}).join(', ')}]
          </div>
        </div>
      )}

      {/* Chart area */}
      <div style={{ padding: '0.75rem', ...(fillContainer ? { flex: 1, minHeight: 0 } : {}) }}>
        {rows.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '2rem', color: '#999', fontSize: '0.85rem' }}>
            No data to display. Open the inspector <span style={{ fontFamily: 'monospace' }}>{'{}'}</span> to debug.
          </div>
        ) : (
          <>
            {chartType === 'table' && <TableView rows={rows} series={effectiveSeries} xKey={xKey} />}
            {chartType === 'bar' && <BarView rows={rows} series={effectiveSeries} xKey={xKey} xLabel={xLabel} chartHeight={fillContainer ? '100%' : chartHeight} />}
            {chartType === 'stacked_bar' && <BarView rows={rows} series={effectiveSeries} xKey={xKey} xLabel={xLabel} stacked chartHeight={fillContainer ? '100%' : chartHeight} />}
            {chartType === 'line' && <LineView rows={rows} series={effectiveSeries} xKey={xKey} xLabel={xLabel} chartHeight={fillContainer ? '100%' : chartHeight} />}
            {chartType === 'pie' && <PieView rows={rows} series={effectiveSeries} xKey={xKey} chartHeight={fillContainer ? '100%' : chartHeight} />}
            {chartType === 'scatter' && <ScatterView rows={rows} series={effectiveSeries} xKey={xKey} xLabel={xLabel} chartHeight={fillContainer ? '100%' : chartHeight} />}
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Color picker — brand palette dropdown + custom option
// ---------------------------------------------------------------------------

function ColorPicker({ value, onChange }: { value: string; onChange: (color: string) => void }) {
  const isBrandColor = BRAND_COLORS.some(c => c.value === value);
  const [showCustom, setShowCustom] = useState(!isBrandColor && value !== DEFAULT_COLORS[0]);

  return (
    <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
      <select
        value={showCustom ? '_custom' : value}
        onChange={e => {
          if (e.target.value === '_custom') {
            setShowCustom(true);
          } else {
            setShowCustom(false);
            onChange(e.target.value);
          }
        }}
        style={{
          fontSize: '0.68rem', border: '1px solid #e2e8f0', borderRadius: 3,
          padding: '2px 4px', fontFamily: 'inherit', color: '#666',
          backgroundColor: '#fff', cursor: 'pointer',
        }}
      >
        {BRAND_COLORS.map(c => (
          <option key={c.value} value={c.value}>{c.name}</option>
        ))}
        <option value="_custom">Custom...</option>
      </select>
      {showCustom && (
        <input
          type="color"
          value={value}
          onChange={e => onChange(e.target.value)}
          style={{ width: 22, height: 22, padding: 0, border: '1px solid #e2e8f0', borderRadius: 3, cursor: 'pointer' }}
        />
      )}
      <div style={{ width: 14, height: 14, borderRadius: 2, backgroundColor: value, border: '1px solid #ddd', flexShrink: 0 }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-views
// ---------------------------------------------------------------------------

function TableView({ rows, series, xKey }: { rows: Record<string, unknown>[]; series: { key: string; label: string }[]; xKey: string }) {
  if (rows.length === 0) return <div style={{ color: '#999', fontSize: '0.8rem' }}>No data</div>;
  const columns = [xKey, ...series.map(s => s.key)];
  const labels: Record<string, string> = { [xKey]: xKey };
  series.forEach(s => { labels[s.key] = s.label; });
  return (
    <div style={{ overflow: 'auto', maxHeight: 300 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
        <thead>
          <tr>{columns.map(c => <th key={c} style={{ textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid #eee', fontWeight: 600, color: '#555' }}>{labels[c] || c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>{columns.map(c => <td key={c} style={{ padding: '4px 8px', borderBottom: '1px solid #f5f5f5', color: '#333' }}>{formatValue(row[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default memo(Chart);

function BarView({ rows, series, xKey, xLabel, stacked, chartHeight = 280 }: { rows: Record<string, unknown>[]; series: { key: string; label: string; color: string }[]; xKey: string; xLabel: string; stacked?: boolean; chartHeight?: number | string }) {
  return (
    <ResponsiveContainer width="100%" height={chartHeight as number}>
      <BarChart data={rows}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis dataKey={xKey} tickFormatter={formatValue} tick={{ fontSize: 11 }} />
        <YAxis tickFormatter={v => formatValue(v)} tick={{ fontSize: 11 }} />
        <Tooltip formatter={(v) => formatValue(v as number)} labelFormatter={formatValue} contentStyle={{ fontSize: '0.78rem' }} />
        <Legend wrapperStyle={{ fontSize: '0.75rem' }} />
        {series.map(s => (
          <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color} stackId={stacked ? 'stack' : undefined} radius={stacked ? 0 : [3, 3, 0, 0]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function LineView({ rows, series, xKey, xLabel, chartHeight = 280 }: { rows: Record<string, unknown>[]; series: { key: string; label: string; color: string }[]; xKey: string; xLabel: string; chartHeight?: number | string }) {
  return (
    <ResponsiveContainer width="100%" height={chartHeight as number}>
      <LineChart data={rows}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis dataKey={xKey} tickFormatter={formatValue} tick={{ fontSize: 11 }} />
        <YAxis tickFormatter={v => formatValue(v)} tick={{ fontSize: 11 }} />
        <Tooltip formatter={(v) => formatValue(v as number)} labelFormatter={formatValue} contentStyle={{ fontSize: '0.78rem' }} />
        <Legend wrapperStyle={{ fontSize: '0.75rem' }} />
        {series.map(s => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2} dot={{ r: 3 }} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function PieView({ rows, series, xKey, chartHeight = 280 }: { rows: Record<string, unknown>[]; series: { key: string; label: string; color: string }[]; xKey: string; chartHeight?: number | string }) {
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
        <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius="70%" label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`} labelLine={false}>
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
