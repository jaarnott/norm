'use client';

import { useState, useEffect, useCallback } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  X, Play, LoaderCircle, ChevronDown, ChevronRight, ChevronUp, Plus, Trash2, Check, TriangleAlert,
  CircleCheck, CircleX, Table, ChartColumn, ChartColumnStacked, ChartLine, ChartPie, ChartScatter,
  Hash, Type, LayoutGrid, TextAlignStart, TextAlignCenter, TextAlignEnd,
  type LucideIcon,
} from 'lucide-react';
import { apiFetch } from '../../../lib/api';
import { DEFAULT_COLORS, STACK_COLORS } from '../Chart';
import type { SavedReportChart, ChartType } from '../../../types';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import VenueSelect from '../../ui/VenueSelect';

// --- Constants -----------------------------------------------------------

const CHART_TYPES: { type: ChartType; label: string; icon: LucideIcon }[] = [
  { type: 'table', label: 'Table', icon: Table },
  { type: 'bar', label: 'Bar', icon: ChartColumn },
  { type: 'stacked_bar', label: 'Stacked', icon: ChartColumnStacked },
  { type: 'line', label: 'Line', icon: ChartLine },
  { type: 'pie', label: 'Pie', icon: ChartPie },
  { type: 'scatter', label: 'Scatter', icon: ChartScatter },
  { type: 'kpi', label: 'KPI', icon: Hash },
  { type: 'text', label: 'Text', icon: Type },
  { type: 'component' as ChartType, label: 'Component', icon: LayoutGrid },
];

// Chart palette — literal hex on purpose: these are the colours the chart draws with.
// New series take the chart's own palette (Chart.tsx), so a chart built here
// saves the same validated colours a chart drawn without config would use.

const EMBEDDABLE_COMPONENTS = [
  { key: 'hiring_board', label: 'Hiring pipeline', needsProps: ['connector_name'] },
  { key: 'orders_dashboard', label: 'Orders', needsProps: [] },
  { key: 'roster_table', label: 'Roster', needsProps: [] },
  { key: 'automated_task_board', label: 'Tasks', needsProps: ['agent_slug'] },
  { key: 'generic_table', label: 'Data table', needsProps: [] },
  { key: 'saved_reports_board', label: 'Reports', needsProps: [] },
];

const DATE_PRESETS = [
  { value: 'now', label: 'Now' },
  { value: '12h_ago', label: '12 hours ago' },
  { value: 'today_start', label: 'Today (start)' },
  { value: 'today_end', label: 'Today (end)' },
  { value: 'yesterday_start', label: 'Yesterday (start)' },
  { value: 'yesterday_end', label: 'Yesterday (end)' },
  { value: 'tomorrow_start', label: 'Tomorrow (start)' },
  { value: 'tomorrow_end', label: 'Tomorrow (end)' },
  { value: 'week_start', label: 'This week (Mon)' },
  { value: 'month_start', label: 'This month (1st)' },
];

// --- Types ---------------------------------------------------------------

interface ChartConfigPanelProps {
  reportId: string;
  chart: SavedReportChart;
  venues: { id: string; name: string }[];
  onClose: () => void;
  onUpdated?: () => void;
}

interface TestResult {
  script?: Record<string, unknown>;
  accepted_params?: { name: string; required: boolean; description: string }[];
  resolved_params?: Record<string, unknown>;
  success?: boolean;
  error?: string;
  row_count?: number;
  response_preview?: Record<string, unknown>[] | Record<string, unknown>;
  rendered_request?: { method: string; url: string; headers: Record<string, string>; body: unknown };
  available_connectors?: string[];
  available_actions?: string[];
  has_credentials?: boolean;
  venues_queried?: number;
  venue_results?: {
    venue_id: string | null;
    venue_name: string | null;
    success: boolean;
    error: string | null;
    row_count: number;
    rendered_request?: { method: string; url: string };
  }[];
  logs?: string[];
}

interface Draft {
  title: string;
  chart_type: ChartType;
  script: { connector: string; action: string; params: Record<string, unknown> };
  chart_spec: Record<string, unknown>;
}

// --- Main Component ------------------------------------------------------

export default function ChartConfigPanel({ reportId, chart, venues, onClose, onUpdated }: ChartConfigPanelProps) {
  const initScript = (chart.script as unknown as Record<string, unknown>) || {};
  const [draft, setDraft] = useState<Draft>({
    title: chart.title || '',
    chart_type: chart.chart_type as ChartType || 'bar',
    script: {
      connector: String(initScript.connector || ''),
      action: String(initScript.action || ''),
      params: (initScript.params as Record<string, unknown>) || {},
    },
    chart_spec: (() => {
      const raw = { ...(chart.chart_spec as unknown as Record<string, unknown> || {}) };
      // Flatten legacy kpi_spec into root — root-level fields take priority
      if (raw.kpi_spec && typeof raw.kpi_spec === 'object') {
        const nested = raw.kpi_spec as Record<string, unknown>;
        for (const [k, v] of Object.entries(nested)) {
          if (!(k in raw)) raw[k] = v;
        }
        delete raw.kpi_spec;
      }
      // Clean legacy chart_type echo (it's on the chart model, not the spec)
      delete raw.chart_type;
      // Tables use hidden_fields/field_labels/field_formats — x_axis and series are redundant
      const ct = (chart.chart_type as string) || 'bar';
      if (ct === 'table') {
        delete raw.x_axis;
        delete raw.series;
      }
      return raw;
    })(),
  });
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [responseFields, setResponseFields] = useState<string[]>([]);
  const [numericFields, setNumericFields] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [selectedVenue, setSelectedVenue] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [connectorList, setConnectorList] = useState<string[]>([]);
  const [availableTools, setAvailableTools] = useState<{
    action: string; method: string; path: string; description: string;
    required_fields: string[]; field_descriptions: Record<string, string>;
  }[]>([]);

  // Fetch available connectors on mount
  useEffect(() => {
    apiFetch('/api/reports/connector-list')
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.connectors) setConnectorList(d.connectors); })
      .catch(() => {});
  }, []);

  // Fetch available tools when connector changes
  useEffect(() => {
    if (!draft.script.connector) { setAvailableTools([]); return; }
    apiFetch(`/api/reports/connector-tools/${draft.script.connector}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.tools) setAvailableTools(d.tools); })
      .catch(() => {});
  }, [draft.script.connector]);

  // Dirty check
  const origJson = JSON.stringify({ title: chart.title, chart_type: chart.chart_type, script: initScript, chart_spec: chart.chart_spec });
  const draftJson = JSON.stringify({ title: draft.title, chart_type: draft.chart_type, script: draft.script, chart_spec: draft.chart_spec });
  const dirty = origJson !== draftJson;

  // Helpers to update nested draft
  const updateSpec = useCallback((patch: Record<string, unknown>) => {
    setDraft(d => ({ ...d, chart_spec: { ...d.chart_spec, ...patch } }));
  }, []);
  const updateScript = useCallback((patch: Partial<Draft['script']>) => {
    setDraft(d => ({ ...d, script: { ...d.script, ...patch } }));
  }, []);

  const runTest = async () => {
    setLoading(true);
    try {
      const body: Record<string, unknown> = {};
      if (selectedVenue) body.venue_id = selectedVenue;
      const res = await apiFetch(`/api/reports/${reportId}/charts/${chart.id}/test`, {
        method: 'POST', body: JSON.stringify(body),
      });
      if (res.ok) {
        const result: TestResult = await res.json();
        setTestResult(result);
        // Extract fields from response preview
        const preview = result.response_preview;
        const firstRow = Array.isArray(preview) ? preview[0] : preview;
        if (firstRow && typeof firstRow === 'object') {
          const keys = Object.keys(firstRow).filter(k => !k.startsWith('_'));
          setResponseFields(keys);
          setNumericFields(keys.filter(k => typeof (firstRow as Record<string, unknown>)[k] === 'number'));
        }
      }
    } catch { /* ignore */ }
    setLoading(false);
  };

  // Auto-test on mount
  useEffect(() => { runTest(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSave = async () => {
    setSaving(true);
    try {
      // Clean up spec before saving
      const cleanSpec = { ...draft.chart_spec };
      delete cleanSpec.chart_type; // chart_type lives on the model, not the spec
      // Tables use hidden_fields/field_labels/field_formats — x_axis and series are redundant
      if (draft.chart_type === 'table') {
        delete cleanSpec.x_axis;
        delete cleanSpec.series;
      }

      const res = await apiFetch(`/api/reports/${reportId}/charts/${chart.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          title: draft.title,
          chart_type: draft.chart_type,
          chart_spec: cleanSpec,
          script: draft.script,
        }),
      });
      if (res.ok && onUpdated) onUpdated();
    } catch { /* ignore */ }
    setSaving(false);
  };

  // ESC to close
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const visibleParams = Object.keys(draft.script.params).filter(k => !k.startsWith('_') && !/^(venue|venue_name|venue_id)$/.test(k));

  return createPortal(
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 9999, backgroundColor: 'rgba(26, 26, 26, 0.35)', display: 'flex', justifyContent: 'flex-end' }}>
      <div role="dialog" aria-modal="true" aria-label="Chart settings" onClick={e => e.stopPropagation()} style={{
        width: 'clamp(360px, 55vw, 700px)', maxWidth: '100vw', height: '100%',
        backgroundColor: 'var(--bg)', borderLeft: '1px solid var(--line)', boxShadow: '-8px 0 32px rgba(26, 26, 26, 0.12)',
        display: 'flex', flexDirection: 'column', animation: 'slideIn 0.2s ease-out', color: 'var(--text)', lineHeight: 1.45,
      }}>
        {/* ---- Header ---- */}
        <div style={{ padding: '14px 16px 12px', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <input
              className="n-keep-size"
              aria-label="Chart title"
              placeholder="Untitled chart"
              value={draft.title}
              onChange={e => setDraft(d => ({ ...d, title: e.target.value }))}
              style={{ flex: 1, minWidth: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, color: 'var(--text)', background: 'transparent', border: 'none', borderBottom: '1px solid transparent', borderRadius: 0, padding: '4px 0', fontFamily: 'inherit' }}
              onFocus={e => (e.currentTarget.style.borderBottomColor = 'var(--brand-soft)')}
              onBlur={e => (e.currentTarget.style.borderBottomColor = 'transparent')}
            />
            <Button variant="secondary" size="sm" onClick={handleSave} disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save'}</Button>
            <IconButton icon={X} label="Close" onClick={onClose} />
          </div>
          {/* Chart type selector */}
          <div role="group" aria-label="Chart type" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {CHART_TYPES.map(ct => {
              const on = draft.chart_type === ct.type;
              return (
                <button key={ct.type} type="button" aria-pressed={on} onClick={() => setDraft(d => ({ ...d, chart_type: ct.type }))} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6, height: 30, padding: '0 10px',
                  fontSize: 'var(--fs-sm)', fontWeight: on ? 600 : 500, fontFamily: 'inherit', whiteSpace: 'nowrap',
                  border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line)'}`, borderRadius: 999,
                  backgroundColor: on ? 'var(--accent-soft)' : 'var(--bg)',
                  color: on ? 'var(--accent)' : 'var(--text-soft)', cursor: 'pointer',
                }}>
                  <Icon icon={ct.icon} size="dense" />
                  {ct.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* ---- Scrollable Content ---- */}
        <div className="scroll-quiet" style={{ flex: 1, minHeight: 0, overflow: 'auto', overscrollBehavior: 'contain', padding: 16 }}>

          {/* ==== Component Settings (when chart_type is component) ==== */}
          {draft.chart_type === ('component' as ChartType) && (
            <ComponentSettings
              spec={draft.chart_spec}
              onChange={updateSpec}
            />
          )}

          {/* ==== Section 1: Data Source ==== */}
          {draft.chart_type !== ('component' as ChartType) && <Section title="Data source">
            <FieldGroup label="Connector">
              <select className="n-select" value={draft.script.connector} onChange={e => updateScript({ connector: e.target.value })} style={fieldStyle}>
                <option value="">Select connector…</option>
                {connectorList.map(c => <option key={c} value={c}>{c}</option>)}
                {draft.script.connector && !connectorList.includes(draft.script.connector) && (
                  <option value={draft.script.connector}>{draft.script.connector}</option>
                )}
              </select>
            </FieldGroup>

            {/* Endpoint picker */}
            {availableTools.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="n-label">Endpoint</div>
                <div className="scroll-quiet" style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 196, padding: 4, border: '1px solid var(--line)', borderRadius: 'var(--radius)', backgroundColor: 'var(--bg)' }}>
                  {availableTools.map(tool => {
                    const selected = draft.script.action === tool.action;
                    return (
                      <button
                        key={tool.action}
                        type="button"
                        aria-pressed={selected}
                        className="n-option"
                        onClick={() => {
                          // Set action and pre-populate params from field_descriptions
                          const venueKeys = new Set(['venue', 'venue_name', 'venue_id']);
                          const params: Record<string, unknown> = {};
                          for (const f of tool.required_fields) {
                            if (!venueKeys.has(f)) params[f] = draft.script.params[f] || '';
                          }
                          for (const f of Object.keys(tool.field_descriptions)) {
                            if (!(f in params) && !venueKeys.has(f)) params[f] = draft.script.params[f] || '';
                          }
                          // Preserve _all_venues flag if it was set
                          if (draft.script.params._all_venues) params._all_venues = true;
                          updateScript({ action: tool.action, params });
                        }}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0, borderRadius: 'var(--radius-sm)',
                          ...(selected ? { backgroundColor: 'var(--selected)' } : {}),
                        }}
                      >
                        <Badge tone={tool.method === 'GET' ? 'ok' : 'warn'}>{tool.method}</Badge>
                        <span style={{ flex: 1, minWidth: 0, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {tool.path || tool.action}
                        </span>
                        <span style={{ flexShrink: 1, minWidth: 0, maxWidth: '45%', fontSize: 'var(--fs-xs)', color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tool.action}</span>
                        {selected && <Icon icon={Check} size="dense" tone="accent" />}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Fallback: manual action input when no tools loaded */}
            {availableTools.length === 0 && (
              <div style={{ marginTop: 12 }}>
                <FieldGroup label="Action">
                  <input className="n-input" value={draft.script.action} onChange={e => updateScript({ action: e.target.value })} style={fieldStyle} placeholder="e.g. get_pos_sales" />
                </FieldGroup>
              </div>
            )}

            {/* Params */}
            {visibleParams.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="n-label">Parameters</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {Object.entries(draft.script.params).filter(([k]) => !k.startsWith('_')).map(([key, val]) => {
                    const isDateField = /date|time|start|end|from|to|since|until|period/i.test(key);
                    const strVal = String(val ?? '');
                    return (
                      <div key={key} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <span title={key} style={{ flex: '0 0 128px', minWidth: 0, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--text-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{key}</span>
                        {isDateField ? (
                          <div style={{ display: 'flex', gap: 6, flex: '1 1 260px', minWidth: 0, flexWrap: 'wrap' }}>
                            <select
                              className="n-select"
                              aria-label={`${key} preset`}
                              value={DATE_PRESETS.some(p => p.value === strVal) ? strVal : '__custom__'}
                              onChange={e => {
                                if (e.target.value !== '__custom__') {
                                  updateScript({ params: { ...draft.script.params, [key]: e.target.value } });
                                }
                              }}
                              style={{ flex: '0 0 auto', maxWidth: '100%' }}
                            >
                              {DATE_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                              {!DATE_PRESETS.some(p => p.value === strVal) && (
                                <option value="__custom__">Custom</option>
                              )}
                            </select>
                            <input
                              className="n-input"
                              aria-label={key}
                              value={strVal}
                              onChange={e => updateScript({ params: { ...draft.script.params, [key]: e.target.value } })}
                              style={{ flex: '1 1 120px', minWidth: 0, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}
                              placeholder="Or type a value"
                            />
                          </div>
                        ) : (
                          <input
                            className="n-input"
                            aria-label={key}
                            value={strVal}
                            onChange={e => updateScript({ params: { ...draft.script.params, [key]: e.target.value } })}
                            style={{ flex: '1 1 260px', minWidth: 0 }}
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {/* Round to 30m checkbox */}
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={!!draft.script.params._round_30}
                onChange={e => updateScript({ params: { ...draft.script.params, _round_30: e.target.checked || undefined } })}
                style={checkboxStyle}
              />
              Round times to the nearest 30 minutes
            </label>
          </Section>}

          {/* ==== Section 2: Test ==== */}
          {draft.chart_type !== ('component' as ChartType) && <Section title="Test">
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
              {venues.length > 0 && (
                <VenueSelect
                  venues={venues}
                  value={selectedVenue || 'all'}
                  onChange={v => setSelectedVenue(v === 'all' ? '' : v)}
                  allowAll
                  label="Venue to test"
                />
              )}
              <Button variant="secondary" icon={loading ? undefined : Play} onClick={runTest} disabled={loading}>
                {loading && <Icon icon={LoaderCircle} size={16} style={{ animation: 'n-spin 1s linear infinite' }} />}
                {loading ? 'Testing…' : 'Run test'}
              </Button>
            </div>

            {testResult && (
              <>
                {/* Status summary */}
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', fontSize: 'var(--fs-sm)', color: 'var(--muted)', marginBottom: 10, fontVariantNumeric: 'tabular-nums' }}>
                  <Badge tone={testResult.success ? 'ok' : 'error'}>{testResult.success ? 'Success' : 'Failed'}</Badge>
                  {testResult.venues_queried !== undefined && testResult.venues_queried > 1 && (
                    <span>{testResult.venues_queried} venue{testResult.venues_queried !== 1 ? 's' : ''}</span>
                  )}
                  {testResult.row_count !== undefined && <span>{testResult.row_count} row{testResult.row_count !== 1 ? 's' : ''} total</span>}
                </div>

                {/* Single error (single venue) */}
                {testResult.error && (
                  <div role="alert" style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '8px 12px', marginBottom: 10, borderRadius: 'var(--radius)', backgroundColor: 'var(--error-bg)', color: 'var(--error)', fontSize: 'var(--fs-sm)', overflowWrap: 'anywhere' }}>
                    <Icon icon={TriangleAlert} size="dense" style={{ marginTop: 2 }} />
                    <span>{testResult.error}</span>
                  </div>
                )}

                {/* Per-venue results */}
                {testResult.venue_results && testResult.venue_results.length > 1 && (
                  <div style={{ marginBottom: 10, border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
                    {testResult.venue_results.map((vr, i) => (
                      <div key={i} style={{
                        display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px',
                        borderTop: i > 0 ? '1px solid var(--line-soft)' : 'none',
                        fontSize: 'var(--fs-sm)',
                      }}>
                        <Icon icon={vr.success ? CircleCheck : CircleX} size="dense" label={vr.success ? 'Succeeded' : 'Failed'} style={{ color: vr.success ? 'var(--ok)' : 'var(--error)' }} />
                        <span style={{ flex: 1, minWidth: 0, color: 'var(--text)', fontWeight: 500 }}>{vr.venue_name || 'Unknown'}</span>
                        <span style={{ minWidth: 0, textAlign: 'right', fontSize: 'var(--fs-xs)', color: vr.success ? 'var(--muted)' : 'var(--error)', fontVariantNumeric: 'tabular-nums' }}>
                          {vr.success ? `${vr.row_count} row${vr.row_count !== 1 ? 's' : ''}` : vr.error?.slice(0, 60) || 'Failed'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Rendered request */}
                {testResult.rendered_request && (
                  <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 10 }}>
                    <Badge tone={testResult.rendered_request.method === 'GET' ? 'ok' : 'warn'}>{testResult.rendered_request.method}</Badge>
                    <span style={{ minWidth: 0, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--text)', wordBreak: 'break-all', lineHeight: 1.5 }}>{testResult.rendered_request.url}</span>
                  </div>
                )}

                {/* Execution logs (consolidators) */}
                {testResult.logs && testResult.logs.length > 0 && (
                  <div style={{ marginBottom: 10, padding: '8px 10px', borderRadius: 'var(--radius)', backgroundColor: 'var(--surface)', border: '1px solid var(--line-soft)' }}>
                    {testResult.logs.map((log, i) => (
                      <div key={i} style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-soft)', fontFamily: 'var(--font-mono)', padding: '1px 0', overflowWrap: 'anywhere' }}>
                        {log}
                      </div>
                    ))}
                  </div>
                )}

                {/* Response preview */}
                {testResult.response_preview && (
                  <details style={{ marginBottom: 4 }}>
                    <summary style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-soft)', cursor: 'pointer' }}>Response preview ({testResult.row_count} row{testResult.row_count !== 1 ? 's' : ''})</summary>
                    <CodeBlock>{JSON.stringify(testResult.response_preview, null, 2)}</CodeBlock>
                  </details>
                )}
              </>
            )}
          </Section>}

          {/* ==== Section 3: Chart Settings ==== */}
          {draft.chart_type !== ('component' as ChartType) && <Section title="Chart settings">
            {draft.chart_type === 'kpi' && (
              <KpiSettings spec={draft.chart_spec} numericFields={numericFields} responseFields={responseFields} onChange={updateSpec} />
            )}
            {(draft.chart_type === 'bar' || draft.chart_type === 'line' || draft.chart_type === 'stacked_bar') && (
              <SeriesSettings spec={draft.chart_spec} numericFields={numericFields} responseFields={responseFields} chartType={draft.chart_type} onChange={updateSpec} testPreview={testResult?.response_preview} />
            )}
            {draft.chart_type === 'pie' && (
              <PieSettings spec={draft.chart_spec} numericFields={numericFields} responseFields={responseFields} onChange={updateSpec} />
            )}
            {draft.chart_type === 'scatter' && (
              <ScatterSettings spec={draft.chart_spec} numericFields={numericFields} onChange={updateSpec} />
            )}
            {draft.chart_type === 'table' && (
              <TableSettings spec={draft.chart_spec} responseFields={responseFields} onChange={updateSpec} />
            )}
            {draft.chart_type === 'text' && (
              <div>
                <FieldGroup label="Text content">
                  <textarea className="n-input" value={String(draft.chart_spec.text_content || '')} onChange={e => updateSpec({ text_content: e.target.value })}
                    style={{ ...fieldStyle, minHeight: 96 }} />
                </FieldGroup>
              </div>
            )}
          </Section>}

          {/* ==== Section 4: Advanced ==== */}
          <div style={{ marginBottom: 16 }}>
            <button type="button" aria-expanded={advancedOpen} onClick={() => setAdvancedOpen(!advancedOpen)} style={{
              display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 0', border: 'none', background: 'none',
              cursor: 'pointer', fontFamily: 'inherit', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)',
            }}>
              <Icon icon={advancedOpen ? ChevronDown : ChevronRight} size="dense" tone="muted" />
              Advanced
            </button>
            {advancedOpen && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
                <FieldGroup label="Chart spec (JSON)">
                  <textarea
                    className="n-input"
                    value={JSON.stringify(draft.chart_spec, null, 2)}
                    onChange={e => { try { setDraft(d => ({ ...d, chart_spec: JSON.parse(e.target.value) })); } catch { /* invalid json */ } }}
                    spellCheck={false}
                    style={{ ...fieldStyle, ...jsonFieldStyle, minHeight: 120 }}
                  />
                </FieldGroup>
                <FieldGroup label="Script (JSON)">
                  <textarea
                    className="n-input"
                    value={JSON.stringify(draft.script, null, 2)}
                    onChange={e => { try { const s = JSON.parse(e.target.value); setDraft(d => ({ ...d, script: { connector: s.connector || '', action: s.action || '', params: s.params || {} } })); } catch { /* invalid json */ } }}
                    spellCheck={false}
                    style={{ ...fieldStyle, ...jsonFieldStyle, minHeight: 100 }}
                  />
                </FieldGroup>
              </div>
            )}
          </div>
        </div>

        {/* ---- Footer ---- */}
        <div style={{
          padding: '12px 16px', paddingBottom: 'max(12px, env(safe-area-inset-bottom, 0px))',
          borderTop: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexShrink: 0,
        }}>
          {dirty ? <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--warn)', fontWeight: 500 }}>Unsaved changes</span> : <span />}
          <Button variant="primary" onClick={handleSave} disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save changes'}</Button>
        </div>
      </div>

      <style>{`
        @keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }
      `}</style>
    </div>,
    document.body,
  );
}

// --- Type-specific settings sub-components --------------------------------

function KpiSettings({ spec, numericFields, responseFields, onChange }: {
  spec: Record<string, unknown>; numericFields: string[]; responseFields: string[];
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <FieldGroup label="Value field *">
        <FieldSelect value={String(spec.value_key || '')} options={numericFields} allFields={responseFields} onChange={v => onChange({ value_key: v })} />
      </FieldGroup>
      <div style={rowStyle}>
        <FieldGroup label="Format" flex={1}>
          <select className="n-select" value={String(spec.format || 'number')} onChange={e => onChange({ format: e.target.value })} style={fieldStyle}>
            <option value="number">Number</option>
            <option value="currency">Currency</option>
            <option value="percent">Percent</option>
          </select>
        </FieldGroup>
        <FieldGroup label="Prefix" flex={1}>
          <input className="n-input" value={String(spec.prefix || '')} onChange={e => onChange({ prefix: e.target.value })} style={fieldStyle} placeholder="e.g. $" />
        </FieldGroup>
        <FieldGroup label="Suffix" flex={1}>
          <input className="n-input" value={String(spec.suffix || '')} onChange={e => onChange({ suffix: e.target.value })} style={fieldStyle} placeholder="e.g. %" />
        </FieldGroup>
      </div>
      <div style={rowStyle}>
        <FieldGroup label="Comparison field" flex={1}>
          <FieldSelect value={String(spec.comparison_key || spec.delta_key || '')} options={numericFields} allFields={responseFields}
            onChange={v => onChange({ comparison_key: v, delta_key: undefined })} allowEmpty emptyLabel="None" />
        </FieldGroup>
        <FieldGroup label="Comparison label" flex={1}>
          <input className="n-input" value={String(spec.comparison_label || spec.delta_label || '')} onChange={e => onChange({ comparison_label: e.target.value, delta_label: undefined })} style={fieldStyle} placeholder="e.g. vs yesterday" />
        </FieldGroup>
      </div>
    </div>
  );
}

// Series palette — literal hex on purpose (chart colours, and the colour pickers' defaults).

function SeriesSettings({ spec, numericFields, responseFields, chartType, onChange, testPreview }: {
  spec: Record<string, unknown>; numericFields: string[]; responseFields: string[]; chartType: string;
  onChange: (patch: Record<string, unknown>) => void;
  testPreview?: Record<string, unknown>[] | Record<string, unknown>;
}) {
  const xAxis = (spec.x_axis as { key?: string; label?: string; format?: string }) || {};
  const series = (spec.series as { key: string; label: string; color: string }[]) || [];
  const groupBy = String(spec.group_by || '');

  // Extract unique group values from test data for auto-populating stacked series
  const groupFields = responseFields.filter(k => /^venue$|^venue_name$|^location$|^category$|^team$|^group$/i.test(k));
  const previewRows = Array.isArray(testPreview) ? testPreview : (testPreview ? [testPreview] : []);

  const autoPopulateSeries = (field: string, valueKey: string) => {
    const uniqueGroups = [...new Set(previewRows.map(r => String(r[field] ?? 'Other')))];
    const newSeries = uniqueGroups.map((g, i) => ({
      key: g, label: g, color: STACK_COLORS[i % STACK_COLORS.length],
    }));
    onChange({ group_by: field, value_key: valueKey, series: newSeries });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={rowStyle}>
        <FieldGroup label="X-axis field" flex={1}>
          <FieldSelect value={xAxis.key || ''} options={responseFields} allFields={responseFields} onChange={v => onChange({ x_axis: { ...xAxis, key: v } })} />
        </FieldGroup>
        <FieldGroup label="X-axis label" flex={1}>
          <input className="n-input" value={xAxis.label || ''} onChange={e => onChange({ x_axis: { ...xAxis, label: e.target.value } })} style={fieldStyle} />
        </FieldGroup>
        <FieldGroup label="X-axis format" flex={1}>
          <select className="n-select" value={String(xAxis.format || '')} onChange={e => onChange({ x_axis: { ...xAxis, format: e.target.value || undefined } })} style={fieldStyle}>
            <option value="">Auto</option>
            <option value="time">Time (09:30)</option>
            <option value="date">Date (Sat, 4 Apr)</option>
            <option value="datetime">Date and time</option>
            <option value="currency">Currency ($)</option>
            <option value="percent">Percent (%)</option>
          </select>
        </FieldGroup>
      </div>
      <div style={rowStyle}>
        <FieldGroup label="Y-axis format" flex={1}>
          <select className="n-select" value={String((spec.y_axis as Record<string, unknown> || {}).format || '')} onChange={e => onChange({ y_axis: { ...((spec.y_axis as Record<string, unknown>) || {}), format: e.target.value || undefined } })} style={fieldStyle}>
            <option value="">Auto</option>
            <option value="currency">Currency ($)</option>
            <option value="percent">Percent (%)</option>
          </select>
        </FieldGroup>
        {chartType === 'bar' && (
          <FieldGroup label="Orientation" flex={1}>
            <select className="n-select" value={String(spec.orientation || 'vertical')} onChange={e => onChange({ orientation: e.target.value })} style={fieldStyle}>
              <option value="vertical">Vertical</option>
              <option value="horizontal">Horizontal</option>
            </select>
          </FieldGroup>
        )}
      </div>

      {/* Stacked bar: Group By + Value + auto-populate */}
      {chartType === 'stacked_bar' && (
        <>
          <div style={rowStyle}>
            <FieldGroup label="Group by (stack)" flex={1}>
              <select className="n-select" value={groupBy} onChange={e => {
                const field = e.target.value;
                onChange({ group_by: field });
                // Auto-populate series if we have test data
                if (field && previewRows.length > 0) {
                  const vk = String(spec.value_key || numericFields[0] || '');
                  autoPopulateSeries(field, vk);
                }
              }} style={fieldStyle}>
                <option value="">None (manual series)</option>
                {(groupFields.length > 0 ? groupFields : responseFields.filter(k => !numericFields.includes(k) && k !== xAxis.key)).map(f => (
                  <option key={f} value={f}>{f}</option>
                ))}
              </select>
            </FieldGroup>
            {groupBy && (
              <FieldGroup label="Value field" flex={1}>
                <select className="n-select" value={String(spec.value_key || '')} onChange={e => {
                  onChange({ value_key: e.target.value });
                  if (groupBy && previewRows.length > 0) autoPopulateSeries(groupBy, e.target.value);
                }} style={fieldStyle}>
                  <option value="">Select…</option>
                  {numericFields.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
              </FieldGroup>
            )}
          </div>
          {groupBy && previewRows.length === 0 && (
            <div style={hintStyle}>Run a test to fill in the series from the data.</div>
          )}
        </>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>Series</div>
        {series.map((s, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {groupBy ? (
              <span style={{ flex: 1, minWidth: 0, fontSize: 'var(--fs-base)', color: 'var(--text)' }}>{s.label || s.key}</span>
            ) : (
              <>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <FieldSelect value={s.key} options={numericFields} allFields={responseFields} onChange={v => {
                    const next = [...series]; next[i] = { ...s, key: v }; onChange({ series: next });
                  }} />
                </div>
                <input className="n-input" value={s.label} onChange={e => { const next = [...series]; next[i] = { ...s, label: e.target.value }; onChange({ series: next }); }}
                  style={{ flex: 1, minWidth: 0 }} placeholder="Label" aria-label="Series label" />
              </>
            )}
            <input type="color" aria-label="Series colour" value={s.color || STACK_COLORS[i % STACK_COLORS.length]}
              onChange={e => { const next = [...series]; next[i] = { ...s, color: e.target.value }; onChange({ series: next }); }}
              style={{ flexShrink: 0, width: 34, height: 34, padding: 2, border: '1px solid var(--line-strong)', borderRadius: 'var(--radius-sm)', backgroundColor: 'var(--bg)', cursor: 'pointer' }} />
            <IconButton icon={Trash2} label="Remove series" iconSize={16} onClick={() => { const next = series.filter((_, j) => j !== i); onChange({ series: next }); }} />
          </div>
        ))}
        <Button variant="quiet" size="sm" icon={Plus} style={{ alignSelf: 'flex-start' }}
          onClick={() => onChange({ series: [...series, { key: '', label: '', color: DEFAULT_COLORS[series.length % DEFAULT_COLORS.length] }] })}>
          Add series
        </Button>
      </div>
    </div>
  );
}

function PieSettings({ spec, numericFields, responseFields, onChange }: {
  spec: Record<string, unknown>; numericFields: string[]; responseFields: string[];
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const xAxis = (spec.x_axis as { key?: string; label?: string; format?: string }) || {};
  const series = (spec.series as { key: string }[]) || [];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <FieldGroup label="Category field">
        <FieldSelect value={xAxis.key || ''} options={responseFields} allFields={responseFields} onChange={v => onChange({ x_axis: { ...xAxis, key: v } })} />
      </FieldGroup>
      <FieldGroup label="Value field">
        <FieldSelect value={series[0]?.key || ''} options={numericFields} allFields={responseFields}
          onChange={v => onChange({ series: [{ key: v, label: v, color: DEFAULT_COLORS[0] }] })} />
      </FieldGroup>
    </div>
  );
}

function ComponentSettings({ spec, onChange }: {
  spec: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const componentKey = String(spec.component_key || '');
  const componentProps = (spec.component_props as Record<string, unknown>) || {};
  const componentDef = EMBEDDABLE_COMPONENTS.find(c => c.key === componentKey);

  const updateProps = (patch: Record<string, unknown>) => {
    onChange({ component_props: { ...componentProps, ...patch } });
  };

  return (
    <Section title="Component">
      <FieldGroup label="Component type">
        <select
          className="n-select"
          value={componentKey}
          onChange={e => onChange({ component_key: e.target.value })}
          style={fieldStyle}
        >
          <option value="">Select component…</option>
          {EMBEDDABLE_COMPONENTS.map(c => (
            <option key={c.key} value={c.key}>{c.label}</option>
          ))}
        </select>
      </FieldGroup>

      {componentKey && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
          {componentDef && componentDef.needsProps.includes('connector_name') && (
            <FieldGroup label="Connector">
              <input
                className="n-input"
                value={String(componentProps.connector_name || '')}
                onChange={e => updateProps({ connector_name: e.target.value })}
                style={fieldStyle}
                placeholder="e.g. bamboohr"
              />
            </FieldGroup>
          )}

          {componentDef && componentDef.needsProps.includes('agent_slug') && (
            <FieldGroup label="Agent">
              <select
                className="n-select"
                value={String(componentProps.agent_slug || '')}
                onChange={e => updateProps({ agent_slug: e.target.value })}
                style={fieldStyle}
              >
                <option value="">Select agent…</option>
                <option value="hr">HR</option>
                <option value="procurement">Procurement</option>
                <option value="reports">Reports</option>
              </select>
            </FieldGroup>
          )}

          {componentDef && componentDef.needsProps.length === 0 && (
            <div style={hintStyle}>
              This component loads its own data — no additional configuration needed.
            </div>
          )}
        </div>
      )}
    </Section>
  );
}

function ScatterSettings({ spec, numericFields, onChange }: {
  spec: Record<string, unknown>; numericFields: string[];
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const xAxis = (spec.x_axis as { key?: string; label?: string; format?: string }) || {};
  const series = (spec.series as { key: string; label: string }[]) || [];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <FieldGroup label="X axis">
        <FieldSelect value={xAxis.key || ''} options={numericFields} allFields={numericFields} onChange={v => onChange({ x_axis: { ...xAxis, key: v } })} />
      </FieldGroup>
      <FieldGroup label="Y axis">
        <FieldSelect value={series[0]?.key || ''} options={numericFields} allFields={numericFields}
          onChange={v => onChange({ series: [{ key: v, label: v, color: DEFAULT_COLORS[0] }] })} />
      </FieldGroup>
    </div>
  );
}

const ALIGN_OPTIONS: { value: 'left' | 'center' | 'right'; icon: LucideIcon }[] = [
  { value: 'left', icon: TextAlignStart },
  { value: 'center', icon: TextAlignCenter },
  { value: 'right', icon: TextAlignEnd },
];

function TableSettings({ spec, responseFields, onChange }: {
  spec: Record<string, unknown>; responseFields: string[];
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const labels = (spec.field_labels as Record<string, string>) || {};
  const rawFormats = (spec.field_formats || {}) as Record<string, string | { type?: string; decimals?: number; prefix?: string; suffix?: string; align?: string }>;
  // Normalise: string shorthand → object
  const getFormat = (f: string) => {
    const v = rawFormats[f];
    if (!v) return { type: '' };
    if (typeof v === 'string') return { type: v };
    return v;
  };
  const setFormat = (field: string, patch: Record<string, unknown>) => {
    const current = getFormat(field);
    const updated = { ...current, ...patch };
    // Clean empty values
    if (!updated.type) delete updated.type;
    if (updated.decimals === undefined || updated.decimals === null) delete updated.decimals;
    if (!updated.prefix) delete updated.prefix;
    if (!updated.suffix) delete updated.suffix;
    const isEmpty = Object.keys(updated).length === 0;
    onChange({ field_formats: { ...rawFormats, [field]: isEmpty ? undefined : updated } });
  };
  const hidden = new Set((spec.hidden_fields as string[]) || []);
  const fieldOrder = (spec.field_order as string[]) || [];

  // Build ordered field list: field_order first, then any new fields from response
  const rawFields = responseFields.length > 0 ? responseFields : Object.keys(labels);
  const visibleFields = rawFields.filter(f => !f.startsWith('_'));
  const orderedFields = (() => {
    const ordered: string[] = [];
    for (const f of fieldOrder) {
      if (visibleFields.includes(f)) ordered.push(f);
    }
    for (const f of visibleFields) {
      if (!ordered.includes(f)) ordered.push(f);
    }
    return ordered;
  })();

  const moveField = (from: number, to: number) => {
    if (to < 0 || to >= orderedFields.length) return;
    const next = [...orderedFields];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange({ field_order: next });
  };

  // Small up/down arrows: the .n-icon-btn hover, at a size that stacks two in one row.
  const reorderBtn: CSSProperties = { width: 24, height: 17, borderRadius: 4 };
  const smallLabel: CSSProperties = { display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-xs)', color: 'var(--text-soft)' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {orderedFields.map((f, i) => {
        const ff = getFormat(f);
        const hasFormat = !!(ff.type || ff.decimals !== undefined || ff.prefix || ff.suffix);
        const showDecimals = ff.type === 'number' || ff.type === 'currency' || ff.type === 'percent';
        return (
          <div key={f} style={{ padding: '8px 0', borderBottom: '1px solid var(--line-soft)' }}>
            {/* Main row: reorder + checkbox + field + label + format type */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '1 1 240px', minWidth: 0 }}>
                <div style={{ display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
                  <button type="button" className="n-icon-btn" onClick={() => moveField(i, i - 1)} disabled={i === 0}
                    style={reorderBtn} title="Move up" aria-label={`Move ${f} up`}>
                    <Icon icon={ChevronUp} size="dense" />
                  </button>
                  <button type="button" className="n-icon-btn" onClick={() => moveField(i, i + 1)} disabled={i === orderedFields.length - 1}
                    style={reorderBtn} title="Move down" aria-label={`Move ${f} down`}>
                    <Icon icon={ChevronDown} size="dense" />
                  </button>
                </div>
                <input type="checkbox" aria-label={`Show ${f}`} checked={!hidden.has(f)} onChange={e => {
                  const next = new Set(hidden);
                  e.target.checked ? next.delete(f) : next.add(f);
                  onChange({ hidden_fields: Array.from(next) });
                }} style={checkboxStyle} />
                <span title={f} style={{ flex: '0 1 auto', minWidth: 60, maxWidth: 150, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--text-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f}</span>
                <input className="n-input" aria-label={`Heading for ${f}`} value={labels[f] || ''} onChange={e => onChange({ field_labels: { ...labels, [f]: e.target.value } })} style={{ flex: 1, minWidth: 80 }} placeholder="Label" />
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0, marginLeft: 'auto' }}>
                <select className="n-select" aria-label={`Format for ${f}`} value={ff.type || ''} onChange={e => setFormat(f, { type: e.target.value || undefined })} style={{ width: 140 }}>
                  <option value="">Auto</option>
                  <option value="number">Number</option>
                  <option value="currency">Currency</option>
                  <option value="percent">Percent</option>
                  <option value="time">Time</option>
                  <option value="date">Date</option>
                  <option value="datetime">Date and time</option>
                </select>
                <div role="group" aria-label={`Alignment for ${f}`} style={{ display: 'flex', flexShrink: 0, border: '1px solid var(--line-strong)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
                  {ALIGN_OPTIONS.map(({ value: a, icon }) => {
                    const on = (ff.align || 'left') === a;
                    return (
                      <button key={a} type="button" aria-pressed={on} onClick={() => setFormat(f, { align: a === 'left' ? undefined : a })}
                        style={{
                          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 32,
                          border: 'none', padding: 0, cursor: 'pointer',
                          backgroundColor: on ? 'var(--selected)' : 'var(--bg)',
                          color: on ? 'var(--accent)' : 'var(--icon)',
                        }}
                        title={`Align ${a}`}
                        aria-label={`Align ${a}`}
                      ><Icon icon={icon} size="dense" /></button>
                    );
                  })}
                </div>
              </div>
            </div>
            {/* Format details row — shown when format type is set */}
            {hasFormat && (showDecimals || ff.prefix || ff.suffix) && (
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', margin: '8px 0 0 56px' }}>
                {showDecimals && (
                  <label style={smallLabel}>
                    Decimals
                    <input className="n-input" type="number" min="0" max="6" value={ff.decimals ?? ''} onChange={e => setFormat(f, { decimals: e.target.value ? Number(e.target.value) : undefined })}
                      style={{ width: 64, textAlign: 'center' }} placeholder="Auto" />
                  </label>
                )}
                <label style={smallLabel}>
                  Prefix
                  <input className="n-input" value={ff.prefix || ''} onChange={e => setFormat(f, { prefix: e.target.value || undefined })}
                    style={{ width: 64, textAlign: 'center' }} placeholder="e.g. $" />
                </label>
                <label style={smallLabel}>
                  Suffix
                  <input className="n-input" value={ff.suffix || ''} onChange={e => setFormat(f, { suffix: e.target.value || undefined })}
                    style={{ width: 64, textAlign: 'center' }} placeholder="e.g. hrs" />
                </label>
              </div>
            )}
          </div>
        );
      })}
      {orderedFields.length === 0 && <span style={hintStyle}>Run a test to see the available fields.</span>}
    </div>
  );
}

// --- Shared UI primitives -------------------------------------------------

function FieldSelect({ value, options, allFields, onChange, allowEmpty, emptyLabel }: {
  value: string; options: string[]; allFields?: string[];
  onChange: (v: string) => void; allowEmpty?: boolean; emptyLabel?: string;
}) {
  // Ensure current value is always in the list
  const opts = [...new Set([...(value && !allowEmpty ? [value] : []), ...options])];
  const hasOptions = options.length > 0;
  return (
    <select className="n-select" value={value} onChange={e => onChange(e.target.value)} style={fieldStyle}>
      {allowEmpty && <option value="">{emptyLabel || 'None'}</option>}
      {!hasOptions && !value && <option value="" disabled>Run a test to see fields</option>}
      {opts.map(o => <option key={o} value={o}>{(allFields || options).includes(o) ? o : `${o} (not in data)`}</option>)}
    </select>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ marginBottom: 20, paddingBottom: 20, borderBottom: '1px solid var(--line-soft)' }}>
      <h3 style={{ margin: '0 0 12px', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{title}</h3>
      {children}
    </section>
  );
}

/** A label over one control. `flex` lets groups share a row and wrap on narrow screens. */
function FieldGroup({ label, children, flex }: { label: string; children: ReactNode; flex?: number }) {
  return (
    <label style={{ display: 'block', flex: flex !== undefined ? `${flex} 1 150px` : undefined, minWidth: 0 }}>
      <span className="n-label">{label}</span>
      {children}
    </label>
  );
}

function CodeBlock({ children }: { children: string }) {
  return (
    <pre style={{
      fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-mono)', color: 'var(--code-text)', backgroundColor: 'var(--code-bg)',
      padding: '10px 12px', borderRadius: 'var(--radius)', overflow: 'auto', maxHeight: 240,
      whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: '8px 0 0', lineHeight: 1.5,
    }}>{children}</pre>
  );
}

// --- Shared styles --------------------------------------------------------

/** Inputs and selects take their look from .n-input / .n-select; this only sizes them. */
const fieldStyle: CSSProperties = { width: '100%', minWidth: 0 };

const jsonFieldStyle: CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', backgroundColor: 'var(--surface)' };

/** A row of field groups that wraps onto two lines on a phone. */
const rowStyle: CSSProperties = { display: 'flex', gap: '12px 8px', flexWrap: 'wrap' };

const checkboxStyle: CSSProperties = { width: 16, height: 16, margin: 0, flexShrink: 0, accentColor: 'var(--accent)', cursor: 'pointer' };

const hintStyle: CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
