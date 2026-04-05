'use client';

import { useState, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X, Play, Loader2, ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react';
import { apiFetch } from '../../../lib/api';
import type { SavedReportChart, ChartType } from '../../../types';

// --- Constants -----------------------------------------------------------

const CHART_TYPES: { type: ChartType; label: string; icon: string }[] = [
  { type: 'table', label: 'Table', icon: '\u25A4' },
  { type: 'bar', label: 'Bar', icon: '\u25AE' },
  { type: 'stacked_bar', label: 'Stacked', icon: '\u25A6' },
  { type: 'line', label: 'Line', icon: '\u27CB' },
  { type: 'pie', label: 'Pie', icon: '\u25D4' },
  { type: 'scatter', label: 'Scatter', icon: '\u2058' },
  { type: 'kpi', label: 'KPI', icon: '#' },
  { type: 'text', label: 'Text', icon: 'T' },
  { type: 'component' as ChartType, label: 'Component', icon: '\u25A3' },
];

const DEFAULT_COLORS = ['#d4c4ae', '#a8cfc0', '#b8c8dc', '#e0c8a8', '#c8b8d4', '#a8d0b8', '#d8c0b8', '#b8d0d4'];

const EMBEDDABLE_COMPONENTS = [
  { key: 'hiring_board', label: 'Hiring Pipeline', needsProps: ['connector_name'] },
  { key: 'orders_dashboard', label: 'Orders', needsProps: [] },
  { key: 'roster_table', label: 'Roster', needsProps: [] },
  { key: 'automated_task_board', label: 'Tasks', needsProps: ['agent_slug'] },
  { key: 'generic_table', label: 'Data Table', needsProps: [] },
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

  return createPortal(
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 9999, backgroundColor: 'rgba(0,0,0,0.5)', display: 'flex', justifyContent: 'flex-end' }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: '55vw', maxWidth: 700, minWidth: 360, height: '100vh',
        backgroundColor: '#fff', boxShadow: '-4px 0 20px rgba(0,0,0,0.1)',
        display: 'flex', flexDirection: 'column', animation: 'slideIn 0.2s ease-out',
      }}>
        {/* ---- Header ---- */}
        <div style={{ padding: '0.75rem 1rem', borderBottom: '1px solid #f0ebe5', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
            <input
              value={draft.title}
              onChange={e => setDraft(d => ({ ...d, title: e.target.value }))}
              style={{ fontSize: '0.9rem', fontWeight: 600, color: '#333', border: 'none', borderBottom: '1px solid transparent', padding: '2px 0', fontFamily: 'inherit', flex: 1, marginRight: 8 }}
              onFocus={e => (e.currentTarget.style.borderBottomColor = '#c4a882')}
              onBlur={e => (e.currentTarget.style.borderBottomColor = 'transparent')}
            />
            <div style={{ display: 'flex', gap: 6 }}>
              <button onClick={handleSave} disabled={!dirty || saving} style={{
                padding: '4px 14px', fontSize: '0.72rem', fontWeight: 600, border: 'none', borderRadius: 6,
                backgroundColor: dirty ? '#c4a882' : '#e8e3dd', color: dirty ? '#fff' : '#bbb',
                cursor: dirty && !saving ? 'pointer' : 'default', fontFamily: 'inherit',
              }}>{saving ? 'Saving...' : 'Save'}</button>
              <button onClick={onClose} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 28, border: 'none', borderRadius: 6, backgroundColor: '#f5f5f5', cursor: 'pointer' }}>
                <X size={14} strokeWidth={2} />
              </button>
            </div>
          </div>
          {/* Chart type selector */}
          <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
            {CHART_TYPES.map(ct => (
              <button key={ct.type} onClick={() => setDraft(d => ({ ...d, chart_type: ct.type }))} style={{
                padding: '3px 8px', fontSize: '0.65rem', fontWeight: draft.chart_type === ct.type ? 700 : 500,
                border: `1px solid ${draft.chart_type === ct.type ? '#c4a882' : '#eee'}`,
                borderRadius: 4, backgroundColor: draft.chart_type === ct.type ? '#faf6f0' : '#fff',
                color: draft.chart_type === ct.type ? '#c4a882' : '#999', cursor: 'pointer', fontFamily: 'inherit',
              }}>{ct.icon} {ct.label}</button>
            ))}
          </div>
        </div>

        {/* ---- Scrollable Content ---- */}
        <div style={{ flex: 1, overflow: 'auto', padding: '1rem' }}>

          {/* ==== Component Settings (when chart_type is component) ==== */}
          {draft.chart_type === ('component' as ChartType) && (
            <ComponentSettings
              spec={draft.chart_spec}
              onChange={updateSpec}
            />
          )}

          {/* ==== Section 1: Data Source ==== */}
          {draft.chart_type !== ('component' as ChartType) && <Section title="Data Source">
            <FieldGroup label="Connector">
              <select value={draft.script.connector} onChange={e => updateScript({ connector: e.target.value })} style={selectStyle}>
                <option value="">Select connector...</option>
                {connectorList.map(c => <option key={c} value={c}>{c}</option>)}
                {draft.script.connector && !connectorList.includes(draft.script.connector) && (
                  <option value={draft.script.connector}>{draft.script.connector}</option>
                )}
              </select>
            </FieldGroup>

            {/* Endpoint picker */}
            {availableTools.length > 0 && (
              <div style={{ marginTop: 8, marginBottom: 8 }}>
                <div style={{ fontSize: '0.62rem', fontWeight: 600, color: '#aaa', marginBottom: 4 }}>Endpoint</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 180, overflow: 'auto' }}>
                  {availableTools.map(tool => {
                    const selected = draft.script.action === tool.action;
                    return (
                      <button
                        key={tool.action}
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
                          display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px',
                          border: `1px solid ${selected ? '#c4a882' : '#f0ebe5'}`,
                          borderRadius: 6, backgroundColor: selected ? '#faf6f0' : '#fff',
                          cursor: 'pointer', fontFamily: 'inherit', textAlign: 'left',
                          transition: 'border-color 0.1s',
                        }}
                      >
                        <span style={{
                          padding: '1px 5px', borderRadius: 3, fontSize: '0.58rem', fontWeight: 700, flexShrink: 0,
                          backgroundColor: tool.method === 'GET' ? '#e6f4ea' : '#fff3e0',
                          color: tool.method === 'GET' ? '#1b5e20' : '#e65100',
                        }}>{tool.method}</span>
                        <span style={{ fontSize: '0.68rem', color: '#555', fontFamily: 'monospace', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {tool.path || tool.action}
                        </span>
                        <span style={{ fontSize: '0.6rem', color: '#bbb', flexShrink: 0 }}>{tool.action}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Fallback: manual action input when no tools loaded */}
            {availableTools.length === 0 && (
              <FieldGroup label="Action">
                <input value={draft.script.action} onChange={e => updateScript({ action: e.target.value })} style={inputStyle} placeholder="e.g. get_pos_sales" />
              </FieldGroup>
            )}

            {/* Params */}
            {Object.keys(draft.script.params).filter(k => !k.startsWith('_') && !/^(venue|venue_name|venue_id)$/.test(k)).length > 0 && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: '0.62rem', fontWeight: 600, color: '#aaa', marginBottom: 4 }}>Parameters</div>
                {Object.entries(draft.script.params).filter(([k]) => !k.startsWith('_')).map(([key, val]) => {
                  const isDateField = /date|time|start|end|from|to|since|until|period/i.test(key);
                  const strVal = String(val ?? '');
                  return (
                    <div key={key} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4 }}>
                      <span style={{ fontSize: '0.68rem', color: '#888', minWidth: 100, fontFamily: 'monospace' }}>{key}</span>
                      {isDateField ? (
                        <div style={{ display: 'flex', gap: 4, flex: 1 }}>
                          <select
                            value={DATE_PRESETS.some(p => p.value === strVal) ? strVal : '__custom__'}
                            onChange={e => {
                              if (e.target.value !== '__custom__') {
                                updateScript({ params: { ...draft.script.params, [key]: e.target.value } });
                              }
                            }}
                            style={{ ...selectStyle, flex: 'none', width: 'auto', minWidth: 160 }}
                          >
                            {DATE_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                            {!DATE_PRESETS.some(p => p.value === strVal) && (
                              <option value="__custom__">Custom</option>
                            )}
                          </select>
                          <input
                            value={strVal}
                            onChange={e => updateScript({ params: { ...draft.script.params, [key]: e.target.value } })}
                            style={{ ...inputStyle, flex: 1, fontSize: '0.65rem', color: '#999' }}
                            placeholder="or enter value..."
                          />
                        </div>
                      ) : (
                        <input
                          value={strVal}
                          onChange={e => updateScript({ params: { ...draft.script.params, [key]: e.target.value } })}
                          style={{ ...inputStyle, flex: 1 }}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
            {/* Round to 30m checkbox */}
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, fontSize: '0.7rem', color: '#888', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={!!draft.script.params._round_30}
                onChange={e => updateScript({ params: { ...draft.script.params, _round_30: e.target.checked || undefined } })}
              />
              Round times to nearest 30 minutes
            </label>
          </Section>}

          {/* ==== Section 2: Test ==== */}
          {draft.chart_type !== ('component' as ChartType) && <Section title="Test">
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
              {venues.length > 0 && (
                <select value={selectedVenue} onChange={e => setSelectedVenue(e.target.value)} style={selectStyle}>
                  <option value="">All venues</option>
                  {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
                </select>
              )}
              <button onClick={runTest} disabled={loading} style={{
                display: 'flex', alignItems: 'center', gap: 4, padding: '4px 12px', fontSize: '0.75rem', fontWeight: 600,
                border: 'none', borderRadius: 6, backgroundColor: '#c4a882', color: '#fff',
                cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
              }}>
                {loading ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Play size={14} />}
                {loading ? 'Testing...' : 'Run Test'}
              </button>
            </div>

            {testResult && (
              <>
                {/* Status summary */}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: '0.75rem', marginBottom: 8 }}>
                  <Badge ok={testResult.success} label={testResult.success ? 'Success' : 'Failed'} />
                  {testResult.venues_queried !== undefined && testResult.venues_queried > 1 && (
                    <span style={{ color: '#666' }}>{testResult.venues_queried} venue{testResult.venues_queried !== 1 ? 's' : ''}</span>
                  )}
                  {testResult.row_count !== undefined && <span style={{ color: '#666' }}>{testResult.row_count} row{testResult.row_count !== 1 ? 's' : ''} total</span>}
                </div>

                {/* Single error (single venue) */}
                {testResult.error && (
                  <div style={{ padding: '6px 10px', backgroundColor: '#fff5f5', border: '1px solid #fed7d7', borderRadius: 6, fontSize: '0.72rem', color: '#c53030', marginBottom: 8 }}>
                    {testResult.error}
                  </div>
                )}

                {/* Per-venue results */}
                {testResult.venue_results && testResult.venue_results.length > 1 && (
                  <div style={{ marginBottom: 8 }}>
                    {testResult.venue_results.map((vr, i) => (
                      <div key={i} style={{
                        display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
                        borderBottom: i < testResult.venue_results!.length - 1 ? '1px solid #f8f8f5' : 'none',
                        fontSize: '0.72rem',
                      }}>
                        <span style={{
                          width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                          backgroundColor: vr.success ? '#4f8a5e' : '#dc3545',
                        }} />
                        <span style={{ flex: 1, color: '#555', fontWeight: 500 }}>{vr.venue_name || 'Unknown'}</span>
                        <span style={{ color: '#999', fontSize: '0.65rem' }}>
                          {vr.success ? `${vr.row_count} row${vr.row_count !== 1 ? 's' : ''}` : vr.error?.slice(0, 60) || 'Failed'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Rendered request */}
                {testResult.rendered_request && (
                  <div style={{ marginBottom: 8 }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4 }}>
                      <span style={{
                        padding: '1px 6px', borderRadius: 3, fontSize: '0.65rem', fontWeight: 700,
                        backgroundColor: testResult.rendered_request.method === 'GET' ? '#e6f4ea' : '#fff3e0',
                        color: testResult.rendered_request.method === 'GET' ? '#1b5e20' : '#e65100',
                      }}>{testResult.rendered_request.method}</span>
                      <span style={{ fontSize: '0.68rem', color: '#333', wordBreak: 'break-all' }}>{testResult.rendered_request.url}</span>
                    </div>
                  </div>
                )}

                {/* Execution logs (consolidators) */}
                {testResult.logs && testResult.logs.length > 0 && (
                  <div style={{ marginBottom: 8 }}>
                    {testResult.logs.map((log, i) => (
                      <div key={i} style={{ fontSize: '0.65rem', color: '#888', fontFamily: 'monospace', padding: '1px 0' }}>
                        {log}
                      </div>
                    ))}
                  </div>
                )}

                {/* Response preview */}
                {testResult.response_preview && (
                  <details style={{ marginBottom: 4 }}>
                    <summary style={{ fontSize: '0.65rem', fontWeight: 600, color: '#999', cursor: 'pointer' }}>Response Preview ({testResult.row_count} row{testResult.row_count !== 1 ? 's' : ''})</summary>
                    <CodeBlock>{JSON.stringify(testResult.response_preview, null, 2)}</CodeBlock>
                  </details>
                )}
              </>
            )}
          </Section>}

          {/* ==== Section 3: Chart Settings ==== */}
          {draft.chart_type !== ('component' as ChartType) && <Section title="Chart Settings">
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
                <FieldGroup label="Text Content">
                  <textarea value={String(draft.chart_spec.text_content || '')} onChange={e => updateSpec({ text_content: e.target.value })}
                    style={{ ...inputStyle, minHeight: 80, resize: 'vertical' }} />
                </FieldGroup>
              </div>
            )}
          </Section>}

          {/* ==== Section 4: Advanced ==== */}
          <div style={{ marginBottom: '1rem' }}>
            <button onClick={() => setAdvancedOpen(!advancedOpen)} style={{
              display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.7rem', fontWeight: 600,
              color: '#888', border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit',
              textTransform: 'uppercase', letterSpacing: '0.04em', padding: 0,
            }}>
              {advancedOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              Advanced
            </button>
            {advancedOpen && (
              <div style={{ marginTop: 8 }}>
                <FieldGroup label="Chart Spec (JSON)">
                  <textarea
                    value={JSON.stringify(draft.chart_spec, null, 2)}
                    onChange={e => { try { setDraft(d => ({ ...d, chart_spec: JSON.parse(e.target.value) })); } catch { /* invalid json */ } }}
                    spellCheck={false}
                    style={{ width: '100%', minHeight: 120, padding: '0.5rem', fontSize: '0.68rem', fontFamily: 'monospace', border: '1px solid #e2ddd7', borderRadius: 6, backgroundColor: '#fafafa', resize: 'vertical' }}
                  />
                </FieldGroup>
                <FieldGroup label="Script (JSON)">
                  <textarea
                    value={JSON.stringify(draft.script, null, 2)}
                    onChange={e => { try { const s = JSON.parse(e.target.value); setDraft(d => ({ ...d, script: { connector: s.connector || '', action: s.action || '', params: s.params || {} } })); } catch { /* invalid json */ } }}
                    spellCheck={false}
                    style={{ width: '100%', minHeight: 100, padding: '0.5rem', fontSize: '0.68rem', fontFamily: 'monospace', border: '1px solid #e2ddd7', borderRadius: 6, backgroundColor: '#fafafa', resize: 'vertical' }}
                  />
                </FieldGroup>
              </div>
            )}
          </div>
        </div>

        {/* ---- Footer ---- */}
        <div style={{ padding: '0.6rem 1rem', borderTop: '1px solid #f0ebe5', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
          {dirty ? <span style={{ fontSize: '0.65rem', color: '#c4a882', fontWeight: 500 }}>Unsaved changes</span> : <span />}
          <button onClick={handleSave} disabled={!dirty || saving} style={{
            padding: '6px 20px', fontSize: '0.75rem', fontWeight: 600, border: 'none', borderRadius: 6,
            backgroundColor: dirty ? '#c4a882' : '#e8e3dd', color: dirty ? '#fff' : '#bbb',
            cursor: dirty && !saving ? 'pointer' : 'default', fontFamily: 'inherit',
          }}>{saving ? 'Saving...' : 'Save Changes'}</button>
        </div>
      </div>

      <style>{`
        @keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <FieldGroup label="Value Field *">
        <FieldSelect value={String(spec.value_key || '')} options={numericFields} allFields={responseFields} onChange={v => onChange({ value_key: v })} />
      </FieldGroup>
      <div style={{ display: 'flex', gap: 8 }}>
        <FieldGroup label="Format" flex={1}>
          <select value={String(spec.format || 'number')} onChange={e => onChange({ format: e.target.value })} style={selectStyle}>
            <option value="number">Number</option>
            <option value="currency">Currency</option>
            <option value="percent">Percent</option>
          </select>
        </FieldGroup>
        <FieldGroup label="Prefix" flex={1}>
          <input value={String(spec.prefix || '')} onChange={e => onChange({ prefix: e.target.value })} style={inputStyle} placeholder="e.g. $" />
        </FieldGroup>
        <FieldGroup label="Suffix" flex={1}>
          <input value={String(spec.suffix || '')} onChange={e => onChange({ suffix: e.target.value })} style={inputStyle} placeholder="e.g. %" />
        </FieldGroup>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <FieldGroup label="Comparison Field" flex={1}>
          <FieldSelect value={String(spec.comparison_key || spec.delta_key || '')} options={numericFields} allFields={responseFields}
            onChange={v => onChange({ comparison_key: v, delta_key: undefined })} allowEmpty emptyLabel="None" />
        </FieldGroup>
        <FieldGroup label="Comparison Label" flex={1}>
          <input value={String(spec.comparison_label || spec.delta_label || '')} onChange={e => onChange({ comparison_label: e.target.value, delta_label: undefined })} style={inputStyle} placeholder="e.g. vs yesterday" />
        </FieldGroup>
      </div>
    </div>
  );
}

const STACK_COLORS = ['#4f8a5e', '#5b8abd', '#c4a882', '#b07d4f', '#8b6caf', '#c75a5a', '#3d9e8f', '#d4a03c', '#7a8b5e', '#a05195'];

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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', gap: 8 }}>
        <FieldGroup label="X Axis Field" flex={1}>
          <FieldSelect value={xAxis.key || ''} options={responseFields} allFields={responseFields} onChange={v => onChange({ x_axis: { ...xAxis, key: v } })} />
        </FieldGroup>
        <FieldGroup label="X Axis Label" flex={1}>
          <input value={xAxis.label || ''} onChange={e => onChange({ x_axis: { ...xAxis, label: e.target.value } })} style={inputStyle} />
        </FieldGroup>
        <FieldGroup label="X Format" flex={1}>
          <select value={String(xAxis.format || '')} onChange={e => onChange({ x_axis: { ...xAxis, format: e.target.value || undefined } })} style={selectStyle}>
            <option value="">Auto</option>
            <option value="time">Time (09:30)</option>
            <option value="date">Date (Sat, 4 Apr)</option>
            <option value="datetime">Date + Time</option>
            <option value="currency">Currency ($)</option>
            <option value="percent">Percent (%)</option>
          </select>
        </FieldGroup>
      </div>
      <FieldGroup label="Y Axis Format">
        <select value={String((spec.y_axis as Record<string, unknown> || {}).format || '')} onChange={e => onChange({ y_axis: { ...((spec.y_axis as Record<string, unknown>) || {}), format: e.target.value || undefined } })} style={selectStyle}>
          <option value="">Auto</option>
          <option value="currency">Currency ($)</option>
          <option value="percent">Percent (%)</option>
        </select>
      </FieldGroup>
      {chartType === 'bar' && (
        <FieldGroup label="Orientation">
          <select value={String(spec.orientation || 'vertical')} onChange={e => onChange({ orientation: e.target.value })} style={selectStyle}>
            <option value="vertical">Vertical</option>
            <option value="horizontal">Horizontal</option>
          </select>
        </FieldGroup>
      )}

      {/* Stacked bar: Group By + Value + auto-populate */}
      {chartType === 'stacked_bar' && (
        <>
          <div style={{ display: 'flex', gap: 8 }}>
            <FieldGroup label="Group By (stack)" flex={1}>
              <select value={groupBy} onChange={e => {
                const field = e.target.value;
                onChange({ group_by: field });
                // Auto-populate series if we have test data
                if (field && previewRows.length > 0) {
                  const vk = String(spec.value_key || numericFields[0] || '');
                  autoPopulateSeries(field, vk);
                }
              }} style={selectStyle}>
                <option value="">None (manual series)</option>
                {(groupFields.length > 0 ? groupFields : responseFields.filter(k => !numericFields.includes(k) && k !== xAxis.key)).map(f => (
                  <option key={f} value={f}>{f}</option>
                ))}
              </select>
            </FieldGroup>
            {groupBy && (
              <FieldGroup label="Value Field" flex={1}>
                <select value={String(spec.value_key || '')} onChange={e => {
                  onChange({ value_key: e.target.value });
                  if (groupBy && previewRows.length > 0) autoPopulateSeries(groupBy, e.target.value);
                }} style={selectStyle}>
                  <option value="">Select...</option>
                  {numericFields.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
              </FieldGroup>
            )}
          </div>
          {groupBy && previewRows.length === 0 && (
            <div style={{ fontSize: '0.65rem', color: '#bbb', fontStyle: 'italic' }}>Run test to auto-populate series from data</div>
          )}
        </>
      )}

      <div style={{ fontSize: '0.68rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Series</div>
      {series.map((s, i) => (
        <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {groupBy ? (
            <span style={{ fontSize: '0.72rem', color: '#555', flex: 1 }}>{s.label || s.key}</span>
          ) : (
            <>
              <FieldSelect value={s.key} options={numericFields} allFields={responseFields} onChange={v => {
                const next = [...series]; next[i] = { ...s, key: v }; onChange({ series: next });
              }} />
              <input value={s.label} onChange={e => { const next = [...series]; next[i] = { ...s, label: e.target.value }; onChange({ series: next }); }}
                style={{ ...inputStyle, flex: 1 }} placeholder="Label" />
            </>
          )}
          <input type="color" value={s.color || STACK_COLORS[i % STACK_COLORS.length]}
            onChange={e => { const next = [...series]; next[i] = { ...s, color: e.target.value }; onChange({ series: next }); }}
            style={{ width: 28, height: 24, padding: 0, border: '1px solid #ddd', borderRadius: 4, cursor: 'pointer' }} />
          <button onClick={() => { const next = series.filter((_, j) => j !== i); onChange({ series: next }); }}
            style={{ border: 'none', background: 'none', color: '#ccc', cursor: 'pointer', padding: 2 }}><Trash2 size={12} /></button>
        </div>
      ))}
      <button onClick={() => onChange({ series: [...series, { key: '', label: '', color: DEFAULT_COLORS[series.length % DEFAULT_COLORS.length] }] })}
        style={{ fontSize: '0.65rem', color: '#999', border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit', display: 'flex', alignItems: 'center', gap: 3, padding: '2px 0' }}>
        <Plus size={12} /> Add series
      </button>
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <FieldGroup label="Category Field">
        <FieldSelect value={xAxis.key || ''} options={responseFields} allFields={responseFields} onChange={v => onChange({ x_axis: { ...xAxis, key: v } })} />
      </FieldGroup>
      <FieldGroup label="Value Field">
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
      <FieldGroup label="Component Type">
        <select
          value={componentKey}
          onChange={e => onChange({ component_key: e.target.value })}
          style={selectStyle}
        >
          <option value="">Select component...</option>
          {EMBEDDABLE_COMPONENTS.map(c => (
            <option key={c.key} value={c.key}>{c.label}</option>
          ))}
        </select>
      </FieldGroup>

      {componentKey && (
        <div style={{ marginTop: 8 }}>
          {componentDef && componentDef.needsProps.includes('connector_name') && (
            <FieldGroup label="Connector">
              <input
                value={String(componentProps.connector_name || '')}
                onChange={e => updateProps({ connector_name: e.target.value })}
                style={inputStyle}
                placeholder="e.g. bamboohr"
              />
            </FieldGroup>
          )}

          {componentDef && componentDef.needsProps.includes('agent_slug') && (
            <FieldGroup label="Agent">
              <select
                value={String(componentProps.agent_slug || '')}
                onChange={e => updateProps({ agent_slug: e.target.value })}
                style={selectStyle}
              >
                <option value="">Select agent...</option>
                <option value="hr">HR</option>
                <option value="procurement">Procurement</option>
                <option value="reports">Reports</option>
              </select>
            </FieldGroup>
          )}

          {componentDef && componentDef.needsProps.length === 0 && (
            <div style={{ fontSize: '0.72rem', color: '#bbb', marginTop: 4 }}>
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <FieldGroup label="X Axis">
        <FieldSelect value={xAxis.key || ''} options={numericFields} allFields={numericFields} onChange={v => onChange({ x_axis: { ...xAxis, key: v } })} />
      </FieldGroup>
      <FieldGroup label="Y Axis">
        <FieldSelect value={series[0]?.key || ''} options={numericFields} allFields={numericFields}
          onChange={v => onChange({ series: [{ key: v, label: v, color: DEFAULT_COLORS[0] }] })} />
      </FieldGroup>
    </div>
  );
}

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

  const arrowBtn: React.CSSProperties = {
    border: 'none', background: 'none', padding: 0, cursor: 'pointer',
    fontSize: '0.6rem', lineHeight: 1, color: '#ccc', fontFamily: 'inherit',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {orderedFields.map((f, i) => {
        const ff = getFormat(f);
        const hasFormat = !!(ff.type || ff.decimals !== undefined || ff.prefix || ff.suffix);
        const showDecimals = ff.type === 'number' || ff.type === 'currency' || ff.type === 'percent';
        return (
          <div key={f} style={{ borderBottom: '1px solid #f8f8f5', paddingBottom: 4, marginBottom: 4 }}>
            {/* Main row: reorder + checkbox + field + label + format type */}
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 0, width: 16, alignItems: 'center' }}>
                <button onClick={() => moveField(i, i - 1)} disabled={i === 0}
                  style={{ ...arrowBtn, opacity: i === 0 ? 0.3 : 1 }} title="Move up"
                  onMouseEnter={e => { if (i > 0) e.currentTarget.style.color = '#888'; }}
                  onMouseLeave={e => (e.currentTarget.style.color = '#ccc')}
                >&#9650;</button>
                <button onClick={() => moveField(i, i + 1)} disabled={i === orderedFields.length - 1}
                  style={{ ...arrowBtn, opacity: i === orderedFields.length - 1 ? 0.3 : 1 }} title="Move down"
                  onMouseEnter={e => { if (i < orderedFields.length - 1) e.currentTarget.style.color = '#888'; }}
                  onMouseLeave={e => (e.currentTarget.style.color = '#ccc')}
                >&#9660;</button>
              </div>
              <input type="checkbox" checked={!hidden.has(f)} onChange={e => {
                const next = new Set(hidden);
                e.target.checked ? next.delete(f) : next.add(f);
                onChange({ hidden_fields: Array.from(next) });
              }} />
              <span style={{ fontSize: '0.68rem', fontFamily: 'monospace', color: '#888', minWidth: 70 }}>{f}</span>
              <input value={labels[f] || ''} onChange={e => onChange({ field_labels: { ...labels, [f]: e.target.value } })} style={{ ...inputStyle, flex: 1 }} placeholder="Label" />
              <select value={ff.type || ''} onChange={e => setFormat(f, { type: e.target.value || undefined })} style={{ ...selectStyle, width: 90, flex: 'none', fontSize: '0.68rem' }}>
                <option value="">Auto</option>
                <option value="number">Number</option>
                <option value="currency">Currency</option>
                <option value="percent">Percent</option>
                <option value="time">Time</option>
                <option value="date">Date</option>
                <option value="datetime">Date+Time</option>
              </select>
              <div style={{ display: 'flex', border: '1px solid #e2ddd7', borderRadius: 4, overflow: 'hidden', flexShrink: 0 }}>
                {(['left', 'center', 'right'] as const).map(a => (
                  <button key={a} onClick={() => setFormat(f, { align: a === 'left' ? undefined : a })}
                    style={{
                      border: 'none', padding: '2px 5px', fontSize: '0.6rem', cursor: 'pointer', fontFamily: 'inherit',
                      backgroundColor: (ff.align || 'left') === a ? '#f0ebe5' : '#fff',
                      color: (ff.align || 'left') === a ? '#a08060' : '#ccc',
                    }}
                    title={`Align ${a}`}
                  >{a === 'left' ? '\u2190' : a === 'center' ? '\u2194' : '\u2192'}</button>
                ))}
              </div>
            </div>
            {/* Format details row — shown when format type is set */}
            {hasFormat && (showDecimals || ff.prefix || ff.suffix) && (
              <div style={{ display: 'flex', gap: 8, marginLeft: 40, marginTop: 3, alignItems: 'center' }}>
                {showDecimals && (
                  <label style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: '0.62rem', color: '#999' }}>
                    Decimals
                    <input type="number" min="0" max="6" value={ff.decimals ?? ''} onChange={e => setFormat(f, { decimals: e.target.value ? Number(e.target.value) : undefined })}
                      style={{ ...inputStyle, width: 40, fontSize: '0.65rem', textAlign: 'center' }} placeholder="auto" />
                  </label>
                )}
                <label style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: '0.62rem', color: '#999' }}>
                  Prefix
                  <input value={ff.prefix || ''} onChange={e => setFormat(f, { prefix: e.target.value || undefined })}
                    style={{ ...inputStyle, width: 44, fontSize: '0.65rem', textAlign: 'center' }} placeholder="e.g. $" />
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: '0.62rem', color: '#999' }}>
                  Suffix
                  <input value={ff.suffix || ''} onChange={e => setFormat(f, { suffix: e.target.value || undefined })}
                    style={{ ...inputStyle, width: 44, fontSize: '0.65rem', textAlign: 'center' }} placeholder="e.g. hrs" />
                </label>
              </div>
            )}
          </div>
        );
      })}
      {orderedFields.length === 0 && <span style={{ fontSize: '0.72rem', color: '#bbb' }}>Run test to see available fields</span>}
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
    <select value={value} onChange={e => onChange(e.target.value)} style={selectStyle}>
      {allowEmpty && <option value="">{emptyLabel || 'None'}</option>}
      {!hasOptions && !value && <option value="" disabled>(Run test to see fields)</option>}
      {opts.map(o => <option key={o} value={o}>{(allFields || options).includes(o) ? o : `${o} (not in data)`}</option>)}
    </select>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '1.25rem' }}>
      <div style={{ fontSize: '0.7rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '0.5rem' }}>{title}</div>
      {children}
    </div>
  );
}

function FieldGroup({ label, children, flex }: { label: string; children: React.ReactNode; flex?: number }) {
  return (
    <div style={{ flex, minWidth: 0 }}>
      <div style={{ fontSize: '0.62rem', fontWeight: 600, color: '#aaa', marginBottom: 2 }}>{label}</div>
      {children}
    </div>
  );
}

function CodeBlock({ children }: { children: string }) {
  return (
    <pre style={{
      fontSize: '0.65rem', color: '#e2e8f0', backgroundColor: '#1a1a2e',
      padding: '0.5rem', borderRadius: 6, overflow: 'auto', maxHeight: 200,
      whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: '4px 0 0', lineHeight: 1.5,
    }}>{children}</pre>
  );
}

function Badge({ ok, label }: { ok?: boolean; label: string }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 3, padding: '2px 8px', borderRadius: 4,
      fontSize: '0.68rem', fontWeight: 600, backgroundColor: ok ? '#f0faf2' : '#fff5f5', color: ok ? '#4f8a5e' : '#c53030',
    }}>{label}</span>
  );
}

// --- Shared styles --------------------------------------------------------

const inputStyle: React.CSSProperties = {
  padding: '4px 8px', fontSize: '0.75rem', border: '1px solid #e2ddd7', borderRadius: 5, fontFamily: 'inherit', width: '100%',
};

const selectStyle: React.CSSProperties = {
  padding: '4px 8px', fontSize: '0.75rem', border: '1px solid #e2ddd7', borderRadius: 5, fontFamily: 'inherit', width: '100%',
};
