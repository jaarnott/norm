'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import type { ComponentApiConfig } from '../../types';

interface ComponentField { name: string; required: boolean }

const COMPONENTS: { key: string; label: string; description: string; internal: boolean; fields: ComponentField[] }[] = [
  {
    key: 'roster_editor', label: 'Roster Editor', internal: false,
    description: 'Drag-and-drop shift scheduling with week/day views. Syncs changes to external rostering systems.',
    fields: [
      { name: 'id', required: false }, { name: 'rosterId', required: false },
      { name: 'staffMemberId', required: true }, { name: 'staffMemberFirstName', required: true },
      { name: 'staffMemberLastName', required: false }, { name: 'roleId', required: false },
      { name: 'roleName', required: true }, { name: 'clockinTime', required: true },
      { name: 'clockoutTime', required: true }, { name: 'breaks', required: false },
      { name: 'datestampDeleted', required: false }, { name: 'venueId', required: false },
      { name: 'hourlyRate', required: false }, { name: 'totalHours', required: false },
      { name: 'totalCost', required: false }, { name: 'type', required: false },
      { name: 'remunerationType', required: false },
    ],
  },
  {
    key: 'purchase_order_editor', label: 'Purchase Order Editor', internal: false,
    description: 'Create and edit purchase orders with line items. Supports batch order creation grouped by supplier.',
    fields: [
      { name: 'id', required: false }, { name: 'stock_code', required: true },
      { name: 'product', required: true }, { name: 'supplier', required: false },
      { name: 'quantity', required: true }, { name: 'unit', required: false },
      { name: 'unit_price', required: false }, { name: 'itemId', required: false },
      { name: 'unitId', required: false }, { name: 'unitRatio', required: false },
      { name: 'unitCost', required: false }, { name: 'taxPercent', required: false },
      { name: 'supplierId', required: false }, { name: 'supplierName', required: false },
      { name: 'brandId', required: false },
    ],
  },
  {
    key: 'orders_dashboard', label: 'Orders Dashboard', internal: false,
    description: 'View and manage purchase orders. Lists orders by venue with detail expansion and send-to-supplier capability.',
    fields: [
      { name: 'id', required: true }, { name: 'orderNumber', required: false },
      { name: 'supplierName', required: true }, { name: 'orderedBy', required: false },
      { name: 'status', required: true }, { name: 'createdAt', required: false },
      { name: 'subtotal', required: false }, { name: 'tax', required: false },
      { name: 'total', required: false }, { name: 'isReceived', required: false },
    ],
  },
  {
    key: 'criteria_editor', label: 'Criteria Editor', internal: false,
    description: 'Edit screening criteria for job applications. Add, remove, and toggle required criteria.',
    fields: [
      { name: 'id', required: true }, { name: 'text', required: true },
      { name: 'required', required: false }, { name: 'category', required: false },
    ],
  },
  {
    key: 'hiring_board', label: 'Hiring Board', internal: false,
    description: 'Job listings with candidate management. View jobs, applications, and candidate details.',
    fields: [
      { name: 'id', required: true }, { name: 'title', required: true },
      { name: 'department', required: false }, { name: 'location', required: false },
      { name: 'status', required: true }, { name: 'candidate_count', required: false },
    ],
  },
  {
    key: 'generic_table', label: 'Data Table', internal: true,
    description: 'Renders tabular data from LLM tool responses. Auto-detects columns from the data.',
    fields: [],
  },
  {
    key: 'roster_table', label: 'Roster Table (Read-only)', internal: true,
    description: 'Read-only roster view displayed inline in conversations. Shows shifts in a simple table format.',
    fields: [],
  },
  {
    key: 'chart', label: 'Chart', internal: true,
    description: 'Visual chart component (bar, line, pie, etc.) rendered from LLM tool call data via render_chart.',
    fields: [],
  },
  {
    key: 'report_builder', label: 'Report Builder', internal: true,
    description: 'Drag-and-drop report layout with a 24-column grid. Users arrange charts into custom report layouts.',
    fields: [],
  },
  {
    key: 'saved_reports_board', label: 'Saved Reports', internal: true,
    description: 'Lists saved report layouts. Users can open, rename, or delete reports.',
    fields: [],
  },
  {
    key: 'automated_task_board', label: 'Automated Tasks', internal: true,
    description: 'Lists automated/scheduled tasks with status, schedule, and run history.',
    fields: [],
  },
  {
    key: 'automated_task_preview', label: 'Automated Task Preview', internal: true,
    description: 'Single automated task preview card shown inline in conversations.',
    fields: [],
  },
  {
    key: 'tool_approval', label: 'Tool Approval Card', internal: true,
    description: 'Inline approval UI for write tool actions. Shows action summary with approve/reject buttons.',
    fields: [],
  },
];

/** Extract {{ placeholder }} names from a Jinja2 template string */
function extractPlaceholders(template: string | null | undefined): string[] {
  if (!template) return [];
  const matches = template.matchAll(/\{\{\s*(\w+)\s*\}\}/g);
  const names = new Set<string>();
  for (const m of matches) {
    if (m[1] !== 'creds') names.add(m[1]); // exclude credential refs
  }
  return Array.from(names);
}

/** Extract all unique field names from the first item of an API response */
function extractApiFields(data: unknown): string[] {
  let items: Record<string, unknown>[] = [];
  if (Array.isArray(data)) {
    items = data.filter(d => d && typeof d === 'object') as Record<string, unknown>[];
  } else if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>;
    for (const key of ['data', 'items', 'results']) {
      if (Array.isArray(d[key])) {
        items = (d[key] as Record<string, unknown>[]).filter(i => i && typeof i === 'object');
        break;
      }
    }
    if (items.length === 0) items = [d];
  }
  if (items.length === 0) return [];
  // Get all keys from first item, recursing into nested objects/arrays
  const fields = new Set<string>();
  const walk = (obj: Record<string, unknown>, prefix: string) => {
    for (const [key, val] of Object.entries(obj)) {
      const path = prefix ? `${prefix}.${key}` : key;
      fields.add(path);
      if (Array.isArray(val) && val.length > 0 && val[0] && typeof val[0] === 'object') {
        walk(val[0] as Record<string, unknown>, `${path}[]`);
      } else if (val && typeof val === 'object' && !Array.isArray(val)) {
        walk(val as Record<string, unknown>, path);
      }
    }
  };
  walk(items[0], '');
  return Array.from(fields).sort();
}

interface ConnectorOption { connector_name: string; display_name: string }

const inputStyle: React.CSSProperties = {
  padding: '6px 8px', border: '1px solid #e2ddd7', borderRadius: 6,
  fontSize: '0.78rem', fontFamily: 'inherit', outline: 'none', width: '100%',
  boxSizing: 'border-box',
};

const methodColors: Record<string, { bg: string; color: string }> = {
  GET: { bg: '#e8f0fe', color: '#1a56db' },
  POST: { bg: '#d4edda', color: '#155724' },
  PUT: { bg: '#fff3cd', color: '#856404' },
  DELETE: { bg: '#f8d7da', color: '#721c24' },
  PATCH: { bg: '#e2e3e5', color: '#383d41' },
};

function EndpointForm({
  data, onChange, onSave, onDelete, saving, componentFields, allConfigs,
}: {
  data: Partial<ComponentApiConfig>;
  onChange: (patch: Partial<ComponentApiConfig>) => void;
  onSave: () => void;
  onDelete?: () => void;
  saving: boolean;
  componentFields: ComponentField[];
  allConfigs: ComponentApiConfig[];
}) {
  const [apiFields, setApiFields] = useState<string[]>([]);
  const [fetching, setFetching] = useState(false);
  const [venues, setVenues] = useState<{ id: string; name: string }[]>([]);
  const [fetchVenue, setFetchVenue] = useState<string>('');

  // Load venues for fetch
  useEffect(() => {
    apiFetch('/api/venues').then(r => r.ok ? r.json() : null).then(d => {
      if (d?.venues?.length) {
        setVenues(d.venues);
        if (!fetchVenue) setFetchVenue(d.venues[0].id);
      }
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Extract required params from path template placeholders
  const requiredParams = extractPlaceholders(data.path_template);
  const [fetchParams, setFetchParams] = useState<Record<string, string>>(() => {
    // Pre-populate date params with current week
    const defaults: Record<string, string> = {};
    const now = new Date();
    const day = now.getDay();
    const monday = new Date(now);
    monday.setDate(now.getDate() - (day === 0 ? 6 : day - 1));
    monday.setHours(0, 0, 0, 0);
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    sunday.setHours(23, 59, 59, 0);
    const pad = (n: number) => String(n).padStart(2, '0');
    const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}+13:00`;
    for (const p of requiredParams) {
      if (p.includes('start') || p.includes('Start')) defaults[p] = fmt(monday);
      else if (p.includes('end') || p.includes('End')) defaults[p] = fmt(sunday);
    }
    return defaults;
  });

  // Build target field options for write endpoint outbound mapping
  const componentFieldNames = componentFields.map(f => f.name);
  const jinjaFields = allConfigs
    .filter(c => c.component_key === data.component_key && c.connector_name === data.connector_name && c.method !== 'GET')
    .flatMap(c => [...extractPlaceholders(c.path_template), ...extractPlaceholders(c.request_body_template)]);
  const targetOptions = Array.from(new Set([...componentFieldNames, ...jinjaFields])).sort();

  const handleFetchSample = async () => {
    if (!data.component_key || !data.action_name) return;
    setFetching(true);
    try {
      const res = await apiFetch(`/api/component-api/${data.component_key}/${data.action_name}`, {
        method: 'POST',
        body: JSON.stringify({ venue_id: fetchVenue || undefined, params: fetchParams }),
      });
      if (res.ok) {
        const result = await res.json();
        const fields = extractApiFields(result.data);
        setApiFields(fields);
        // Build mapping keyed by component field → API field
        const existing = data.response_field_mapping || {};
        const mapping: Record<string, string> = {};
        for (const cf of componentFields) {
          if (cf.name in existing) {
            mapping[cf.name] = existing[cf.name]; // keep existing
          } else {
            // Auto-match: if API has a field with the same name, use it
            mapping[cf.name] = fields.includes(cf.name) ? cf.name : '';
          }
        }
        onChange({ response_field_mapping: mapping });
      }
    } catch { /* ignore */ }
    setFetching(false);
  };
  return (
    <div style={{ padding: '0.6rem 0.75rem 0.75rem', borderTop: '1px solid #eee', backgroundColor: '#fdf8f3' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 80px', gap: 6, marginBottom: '0.4rem' }}>
        <input value={data.action_name || ''} onChange={e => onChange({ action_name: e.target.value })} placeholder="action_name" style={{ ...inputStyle, fontSize: '0.75rem' }} />
        <input value={data.display_label || ''} onChange={e => onChange({ display_label: e.target.value })} placeholder="Label (optional)" style={{ ...inputStyle, fontSize: '0.75rem' }} />
        <select value={data.method || 'GET'} onChange={e => onChange({ method: e.target.value })} style={{ ...inputStyle, fontSize: '0.75rem' }}>
          {['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>

      <input value={data.path_template || ''} onChange={e => onChange({ path_template: e.target.value })} placeholder="URL template" style={{ ...inputStyle, fontSize: '0.72rem', fontFamily: 'monospace', marginBottom: '0.4rem' }} />

      {data.method !== 'GET' && data.method !== 'DELETE' && (
        <textarea value={data.request_body_template || ''} onChange={e => onChange({ request_body_template: e.target.value })} placeholder="Request body template (Jinja2)" rows={2} style={{ ...inputStyle, fontSize: '0.72rem', fontFamily: 'monospace', marginBottom: '0.4rem', resize: 'vertical' }} />
      )}

      {/* Response field mapping (for GET/load endpoints) */}
      {(data.method === 'GET') && (
        <details style={{ marginBottom: '0.4rem' }} open={apiFields.length > 0 || Object.values(data.response_field_mapping || {}).some(v => !!v)}>
          <summary style={{ fontSize: '0.68rem', fontWeight: 600, color: '#888', cursor: 'pointer', marginBottom: 4 }}>Response Field Mapping (Component ← API)</summary>
          <div style={{ paddingLeft: 8 }}>
            {/* Venue selector for fetch */}
            {venues.length > 0 && (
              <div style={{ marginBottom: 6 }}>
                <span style={{ fontSize: '0.62rem', fontWeight: 600, color: '#aaa', textTransform: 'uppercase' }}>Venue</span>
                <select value={fetchVenue} onChange={e => setFetchVenue(e.target.value)} style={{ ...inputStyle, fontSize: '0.68rem', marginTop: 2 }}>
                  {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
                </select>
              </div>
            )}

            {/* Params needed for fetch */}
            {requiredParams.length > 0 && (
              <div style={{ marginBottom: 6 }}>
                <span style={{ fontSize: '0.62rem', fontWeight: 600, color: '#aaa', textTransform: 'uppercase' }}>Parameters</span>
                {requiredParams.map(p => (
                  <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
                    <span style={{ fontSize: '0.68rem', fontFamily: 'monospace', color: '#888', minWidth: 120 }}>{p}</span>
                    <input
                      value={fetchParams[p] || ''}
                      onChange={e => setFetchParams(prev => ({ ...prev, [p]: e.target.value }))}
                      placeholder={p.includes('date') || p.includes('time') || p.includes('Time') ? 'ISO 8601 datetime' : 'value'}
                      style={{ ...inputStyle, fontSize: '0.68rem', fontFamily: 'monospace' }}
                    />
                  </div>
                ))}
              </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <button onClick={handleFetchSample} disabled={fetching} style={{
                padding: '3px 10px', fontSize: '0.68rem', border: '1px solid #c4a882', borderRadius: 4,
                backgroundColor: '#fff', color: '#a08060', cursor: fetching ? 'not-allowed' : 'pointer', fontFamily: 'inherit', fontWeight: 500,
              }}>{fetching ? 'Fetching...' : 'Fetch API Fields'}</button>
              {apiFields.length > 0 && <span style={{ fontSize: '0.62rem', color: '#999' }}>{apiFields.length} fields discovered</span>}
            </div>

            {/* Header */}
            {componentFields.length > 0 && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 20px 1fr', gap: 4, marginBottom: 4 }}>
                <span style={{ fontSize: '0.6rem', fontWeight: 600, color: '#aaa', textTransform: 'uppercase' }}>Component Field</span>
                <span />
                <span style={{ fontSize: '0.6rem', fontWeight: 600, color: '#aaa', textTransform: 'uppercase' }}>API Field (from response)</span>
              </div>
            )}

            {componentFields.map((cf) => {
              const mapped = (data.response_field_mapping || {})[cf.name] || '';
              const isMapped = !!mapped;
              return (
                <div key={cf.name} style={{ display: 'grid', gridTemplateColumns: '1fr 20px 1fr', gap: 4, alignItems: 'center', marginBottom: 2 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 0' }}>
                    <span style={{
                      fontSize: '0.7rem', fontFamily: 'monospace',
                      color: cf.required ? '#333' : '#888',
                      fontWeight: cf.required ? 600 : 400,
                    }}>
                      {cf.name}
                    </span>
                    {cf.required && (
                      <span style={{ fontSize: '0.55rem', fontWeight: 700, color: '#e53e3e' }}>*</span>
                    )}
                  </div>
                  <span style={{ textAlign: 'center', color: '#ccc', fontSize: '0.7rem' }}>←</span>
                  {(() => {
                    // Build dropdown options: fetched API fields + any saved mapped values
                    const savedValues = Object.values(data.response_field_mapping || {}).filter(v => !!v) as string[];
                    const allOptions = Array.from(new Set([...apiFields, ...savedValues])).sort();
                    return (
                      <select
                        value={mapped}
                        onChange={e => {
                          const next = { ...(data.response_field_mapping || {}) };
                          next[cf.name] = e.target.value;
                          onChange({ response_field_mapping: next });
                        }}
                        style={{
                          ...inputStyle, fontSize: '0.7rem',
                          color: isMapped ? '#333' : '#bbb',
                          borderColor: cf.required && !isMapped ? '#e53e3e' : '#e2ddd7',
                        }}
                      >
                        <option value="">(unmapped)</option>
                        {allOptions.map(f => <option key={f} value={f}>{f}</option>)}
                      </select>
                    );
                  })()}
                </div>
              );
            })}

            {apiFields.length === 0 && (
              <p style={{ fontSize: '0.62rem', color: '#bbb', fontStyle: 'italic', margin: '4px 0 0' }}>Click &quot;Fetch API Fields&quot; to populate the API field dropdowns.</p>
            )}
          </div>
        </details>
      )}

      {/* Outbound field mapping (for write endpoints) */}
      {(data.method !== 'GET') && (
        <details style={{ marginBottom: '0.4rem' }}>
          <summary style={{ fontSize: '0.68rem', fontWeight: 600, color: '#888', cursor: 'pointer', marginBottom: 4 }}>Field Mapping (Component → API)</summary>
          <div style={{ paddingLeft: 8 }}>
            <p style={{ fontSize: '0.62rem', color: '#aaa', margin: '0 0 4px' }}>Map component field names to API parameter names for document sync.</p>
            {Object.entries(data.field_mapping || {}).map(([k, v], i) => (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 20px 1fr auto', gap: 4, alignItems: 'center', marginBottom: 2 }}>
                <input value={k} onChange={e => {
                  const entries = Object.entries(data.field_mapping || {});
                  entries[i] = [e.target.value, v];
                  onChange({ field_mapping: Object.fromEntries(entries) });
                }} placeholder="component field" style={{ ...inputStyle, fontSize: '0.7rem', fontFamily: 'monospace' }} />
                <span style={{ textAlign: 'center', color: '#ccc', fontSize: '0.7rem' }}>→</span>
                <input value={v as string} onChange={e => {
                  const entries = Object.entries(data.field_mapping || {});
                  entries[i] = [k, e.target.value];
                  onChange({ field_mapping: Object.fromEntries(entries) });
                }} placeholder="API param" style={{ ...inputStyle, fontSize: '0.7rem', fontFamily: 'monospace' }} />
                <button onClick={() => {
                  const next = { ...(data.field_mapping || {}) };
                  delete next[k];
                  onChange({ field_mapping: next });
                }} style={{ border: 'none', background: 'none', color: '#ddd', cursor: 'pointer', fontSize: '0.7rem' }}>✕</button>
              </div>
            ))}
            <button onClick={() => onChange({ field_mapping: { ...(data.field_mapping || {}), '': '' } })} style={{ border: 'none', background: 'none', fontSize: '0.62rem', color: '#999', cursor: 'pointer' }}>+ field</button>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
              <span style={{ fontSize: '0.62rem', fontWeight: 600, color: '#888' }}>ID Field:</span>
              <input value={data.id_field || ''} onChange={e => onChange({ id_field: e.target.value || null })} placeholder="e.g. shift_id" style={{ ...inputStyle, fontSize: '0.7rem', fontFamily: 'monospace', width: 130 }} />
            </div>
          </div>
        </details>
      )}

      {/* Test endpoint */}
      {data.id && (
        <TestSection configId={data.id as string} method={data.method || 'GET'} pathTemplate={data.path_template || ''} bodyTemplate={data.request_body_template || ''} />
      )}

      <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
        <button disabled={saving || !data.action_name || !data.path_template} onClick={onSave} style={{
          padding: '4px 12px', fontSize: '0.75rem', fontWeight: 500, border: 'none', borderRadius: 6,
          backgroundColor: '#c4a882', color: '#fff', cursor: saving ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
        }}>{saving ? '...' : data.id ? 'Update' : 'Create'}</button>
        {onDelete && (
          <button onClick={onDelete} style={{ padding: '4px 12px', fontSize: '0.75rem', border: '1px solid #e53e3e', borderRadius: 6, backgroundColor: '#fff', color: '#e53e3e', cursor: 'pointer', fontFamily: 'inherit' }}>Delete</button>
        )}
      </div>
    </div>
  );
}

function TestSection({ configId, method, pathTemplate, bodyTemplate }: {
  configId: string; method: string; pathTemplate: string; bodyTemplate: string;
}) {
  const [venues, setVenues] = useState<{ id: string; name: string }[]>([]);
  const [venueId, setVenueId] = useState('');
  const [testParams, setTestParams] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<{ method: string; url: string; headers: Record<string, string>; body: unknown } | null>(null);
  const [response, setResponse] = useState<{ data: unknown; status_code: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load venues
  useEffect(() => {
    apiFetch('/api/venues').then(r => r.ok ? r.json() : null).then(d => {
      if (d?.venues?.length) { setVenues(d.venues); setVenueId(d.venues[0].id); }
    }).catch(() => {});
  }, []);

  // Extract placeholders from path + body templates
  const allPlaceholders = Array.from(new Set([
    ...extractPlaceholders(pathTemplate),
    ...extractPlaceholders(bodyTemplate),
  ]));

  // Pre-populate date params
  useEffect(() => {
    const defaults: Record<string, string> = {};
    const now = new Date();
    const day = now.getDay();
    const monday = new Date(now);
    monday.setDate(now.getDate() - (day === 0 ? 6 : day - 1));
    monday.setHours(0, 0, 0, 0);
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    sunday.setHours(23, 59, 59, 0);
    const pad = (n: number) => String(n).padStart(2, '0');
    const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}+13:00`;
    for (const p of allPlaceholders) {
      if (p.includes('start') || p.includes('Start')) defaults[p] = fmt(monday);
      else if (p.includes('end') || p.includes('End')) defaults[p] = fmt(sunday);
    }
    setTestParams(prev => ({ ...defaults, ...prev }));
  }, [pathTemplate, bodyTemplate]); // eslint-disable-line react-hooks/exhaustive-deps

  const handlePreview = async () => {
    setLoading(true); setError(null); setPreview(null); setResponse(null);
    try {
      const res = await apiFetch('/api/component-api-configs/preview-request', {
        method: 'POST',
        body: JSON.stringify({ config_id: configId, venue_id: venueId || undefined, params: testParams }),
      });
      if (res.ok) {
        setPreview(await res.json());
      } else {
        const d = await res.json().catch(() => ({}));
        setError(d.detail || `Error ${res.status}`);
      }
    } catch (e) { setError(String(e)); }
    setLoading(false);
  };

  const handleExecute = async () => {
    setLoading(true); setError(null); setResponse(null);
    try {
      const res = await apiFetch('/api/component-api-configs/preview-request', {
        method: 'POST',
        body: JSON.stringify({ config_id: configId, venue_id: venueId || undefined, params: testParams }),
      });
      if (!res.ok) { setError('Preview failed'); setLoading(false); return; }
      const prev = await res.json();

      // Now actually execute via the rendered request
      const execRes = await fetch(prev.url, {
        method: prev.method,
        headers: prev.headers,
        body: prev.body ? JSON.stringify(prev.body) : undefined,
      });
      const data = await execRes.json().catch(() => execRes.text());
      setResponse({ data, status_code: execRes.status });
    } catch (e) { setError(String(e)); }
    setLoading(false);
  };

  return (
    <details style={{ marginBottom: '0.4rem' }}>
      <summary style={{ fontSize: '0.68rem', fontWeight: 600, color: '#888', cursor: 'pointer', marginBottom: 4 }}>Test Endpoint</summary>
      <div style={{ paddingLeft: 8 }}>
        {/* Venue */}
        {venues.length > 0 && (
          <div style={{ marginBottom: 6 }}>
            <span style={{ fontSize: '0.62rem', fontWeight: 600, color: '#aaa', textTransform: 'uppercase' }}>Venue</span>
            <select value={venueId} onChange={e => setVenueId(e.target.value)} style={{ ...inputStyle, fontSize: '0.68rem', marginTop: 2 }}>
              {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          </div>
        )}

        {/* Params */}
        {allPlaceholders.length > 0 && (
          <div style={{ marginBottom: 6 }}>
            <span style={{ fontSize: '0.62rem', fontWeight: 600, color: '#aaa', textTransform: 'uppercase' }}>Parameters</span>
            {allPlaceholders.map(p => (
              <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
                <span style={{ fontSize: '0.68rem', fontFamily: 'monospace', color: '#888', minWidth: 130 }}>{p}</span>
                <input
                  value={testParams[p] || ''}
                  onChange={e => setTestParams(prev => ({ ...prev, [p]: e.target.value }))}
                  placeholder="value"
                  style={{ ...inputStyle, fontSize: '0.68rem', fontFamily: 'monospace' }}
                />
              </div>
            ))}
          </div>
        )}

        {/* Buttons */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          <button onClick={handlePreview} disabled={loading} style={{
            padding: '3px 10px', fontSize: '0.68rem', border: '1px solid #c4a882', borderRadius: 4,
            backgroundColor: '#fff', color: '#a08060', cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'inherit', fontWeight: 500,
          }}>{loading ? '...' : 'Preview Request'}</button>
          {preview && (
            <button onClick={handleExecute} disabled={loading} style={{
              padding: '3px 10px', fontSize: '0.68rem', border: '1px solid #28a745', borderRadius: 4,
              backgroundColor: '#fff', color: '#28a745', cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'inherit', fontWeight: 500,
            }}>{loading ? '...' : 'Execute'}</button>
          )}
        </div>

        {error && <div style={{ fontSize: '0.68rem', color: '#e53e3e', marginBottom: 4 }}>{error}</div>}

        {/* Preview */}
        {preview && (
          <div style={{ marginBottom: 6 }}>
            <div style={{ fontSize: '0.62rem', fontWeight: 600, color: '#aaa', textTransform: 'uppercase', marginBottom: 2 }}>Request Preview</div>
            <pre style={{
              fontSize: '0.65rem', backgroundColor: '#1a202c', color: '#e2e8f0', padding: '0.6rem',
              borderRadius: 6, overflow: 'auto', maxHeight: 300, lineHeight: 1.4, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              <span style={{ color: '#68d391' }}>{preview.method}</span> {preview.url}{'\n\n'}
              {Object.entries(preview.headers).map(([k, v]) => `${k}: ${v}`).join('\n')}{'\n\n'}
              {preview.body ? JSON.stringify(preview.body, null, 2) : '(no body)'}
            </pre>
          </div>
        )}

        {/* Response */}
        {response && (
          <div>
            <div style={{ fontSize: '0.62rem', fontWeight: 600, color: '#aaa', textTransform: 'uppercase', marginBottom: 2 }}>
              Response <span style={{ color: response.status_code < 400 ? '#28a745' : '#e53e3e' }}>{response.status_code}</span>
            </div>
            <pre style={{
              fontSize: '0.65rem', backgroundColor: '#f7fafc', color: '#333', padding: '0.6rem',
              borderRadius: 6, overflow: 'auto', maxHeight: 300, lineHeight: 1.4, border: '1px solid #e2e8f0',
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {typeof response.data === 'string' ? response.data : JSON.stringify(response.data, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </details>
  );
}

export default function ComponentsPanel() {
  const [connectors, setConnectors] = useState<ConnectorOption[]>([]);
  const [selectedComponent, setSelectedComponent] = useState(COMPONENTS[0].key);
  const [selectedConnector, setSelectedConnector] = useState<string | null>(null);
  const [configs, setConfigs] = useState<ComponentApiConfig[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editDrafts, setEditDrafts] = useState<Record<string, Partial<ComponentApiConfig>>>({});
  const [addingNew, setAddingNew] = useState<Partial<ComponentApiConfig> | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiFetch('/api/connector-specs')
      .then(r => r.ok ? r.json() : { specs: [] })
      .then(data => {
        const list = (data.specs || []).map((s: { connector_name: string; display_name: string }) => ({
          connector_name: s.connector_name, display_name: s.display_name,
        }));
        setConnectors(list);
        if (list.length > 0 && !selectedConnector) setSelectedConnector(list[0].connector_name);
      })
      .catch(() => {});
    apiFetch('/api/component-api-configs')
      .then(r => r.ok ? r.json() : { configs: [] })
      .then(data => setConfigs(data.configs || []))
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = configs.filter(c => c.component_key === selectedComponent && c.connector_name === selectedConnector);
  const selectedComponentDef = COMPONENTS.find(c => c.key === selectedComponent);
  const componentFields = selectedComponentDef?.fields || [];

  const toggleExpand = useCallback((id: string) => {
    setExpandedId(prev => {
      if (prev === id) return null;
      const cfg = configs.find(c => c.id === id);
      if (cfg) setEditDrafts(d => ({ ...d, [id]: { ...cfg } }));
      return id;
    });
  }, [configs]);

  const updateDraft = useCallback((id: string, patch: Partial<ComponentApiConfig>) => {
    setEditDrafts(prev => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }, []);

  const handleSaveExisting = useCallback(async (id: string) => {
    const draft = editDrafts[id];
    if (!draft) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/component-api-configs/${id}`, { method: 'PUT', body: JSON.stringify(draft) });
      if (res.ok) {
        const saved = await res.json();
        setConfigs(prev => prev.map(c => c.id === id ? saved : c));
        setExpandedId(null);
      }
    } catch { /* ignore */ }
    setSaving(false);
  }, [editDrafts]);

  const handleSaveNew = useCallback(async () => {
    if (!addingNew?.action_name) return;
    setSaving(true);
    try {
      const res = await apiFetch('/api/component-api-configs', { method: 'POST', body: JSON.stringify(addingNew) });
      if (res.ok) {
        const saved = await res.json();
        setConfigs(prev => [...prev, saved]);
        setAddingNew(null);
      }
    } catch { /* ignore */ }
    setSaving(false);
  }, [addingNew]);

  const handleDelete = useCallback(async (id: string) => {
    await apiFetch(`/api/component-api-configs/${id}`, { method: 'DELETE' });
    setConfigs(prev => prev.filter(c => c.id !== id));
    setExpandedId(null);
  }, []);

  return (
    <div>
      <h3 style={{ margin: '0 0 0.5rem', fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Components
      </h3>
      <p style={{ color: '#999', fontSize: '0.78rem', margin: '0 0 1rem', lineHeight: 1.5 }}>
        Configure external API endpoints for each component. Field mappings define how component data maps to API parameters for real-time sync.
      </p>

      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '0.75rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <label style={{ display: 'block', fontSize: '0.7rem', fontWeight: 600, color: '#888', marginBottom: 4, textTransform: 'uppercase' }}>Component</label>
          <select value={selectedComponent} onChange={e => { setSelectedComponent(e.target.value); setExpandedId(null); }} style={{ ...inputStyle, width: 220 }}>
            {COMPONENTS.map(c => <option key={c.key} value={c.key}>{c.internal ? '○ ' : '● '}{c.label}</option>)}
          </select>
        </div>
        {!selectedComponentDef?.internal && (
          <div>
            <label style={{ display: 'block', fontSize: '0.7rem', fontWeight: 600, color: '#888', marginBottom: 4, textTransform: 'uppercase' }}>Connector</label>
            <select value={selectedConnector || ''} onChange={e => { setSelectedConnector(e.target.value); setExpandedId(null); }} style={{ ...inputStyle, width: 200 }}>
              {connectors.map(c => <option key={c.connector_name} value={c.connector_name}>{c.display_name || c.connector_name}</option>)}
            </select>
          </div>
        )}
      </div>

      {/* Component info */}
      {selectedComponentDef && (
        <div style={{
          padding: '0.6rem 0.75rem', marginBottom: '1rem', borderRadius: 8,
          backgroundColor: selectedComponentDef.internal ? '#f7f7f8' : '#fafafa',
          border: `1px solid ${selectedComponentDef.internal ? '#e2e3e5' : '#e8e4de'}`,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.25rem' }}>
            <span style={{ fontSize: '0.78rem', fontWeight: 600, color: '#333' }}>{selectedComponentDef.label}</span>
            <span style={{
              fontSize: '0.58rem', fontWeight: 600, padding: '1px 5px', borderRadius: 3,
              backgroundColor: selectedComponentDef.internal ? '#e2e3e5' : '#d4edda',
              color: selectedComponentDef.internal ? '#666' : '#155724',
            }}>{selectedComponentDef.internal ? 'Internal' : 'External'}</span>
            <span style={{ fontSize: '0.62rem', fontFamily: 'monospace', color: '#aaa' }}>{selectedComponentDef.key}</span>
          </div>
          <p style={{ fontSize: '0.72rem', color: '#888', margin: 0, lineHeight: 1.5 }}>{selectedComponentDef.description}</p>
        </div>
      )}

      {/* Endpoints section — only for external components */}
      {selectedComponentDef && !selectedComponentDef.internal && (<>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
        <h4 style={{ margin: 0, fontSize: '0.78rem', fontWeight: 600, color: '#555' }}>Endpoints</h4>
        <button
          onClick={() => setAddingNew({
            component_key: selectedComponent,
            connector_name: selectedConnector || '',
            action_name: '', method: 'GET', path_template: '', enabled: true,
          })}
          style={{ padding: '3px 10px', fontSize: '0.75rem', border: '1px solid #ddd', borderRadius: 4, backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit' }}
        >+ Add Endpoint</button>
      </div>

      {filtered.length === 0 && !addingNew && (
        <p style={{ color: '#bbb', fontSize: '0.78rem', fontStyle: 'italic', margin: '0 0 1rem' }}>No endpoints configured.</p>
      )}

      {filtered.map(cfg => {
        const mc = methodColors[cfg.method] || methodColors.GET;
        const hasMapping = cfg.field_mapping && Object.keys(cfg.field_mapping).length > 0;
        const isExpanded = expandedId === cfg.id;

        return (
          <div key={cfg.id} style={{
            border: `1px solid ${isExpanded ? '#c4a882' : '#e8e4de'}`,
            borderRadius: 8, marginBottom: '0.4rem', backgroundColor: '#fafafa', overflow: 'hidden',
          }}>
            {/* Card header */}
            <div
              onClick={() => toggleExpand(cfg.id)}
              style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.6rem 0.75rem', cursor: 'pointer' }}
            >
              <span style={{ fontSize: '0.6rem', fontWeight: 600, padding: '1px 6px', borderRadius: 4, backgroundColor: mc.bg, color: mc.color }}>{cfg.method}</span>
              <span style={{ fontSize: '0.78rem', fontWeight: 600, color: '#333' }}>{cfg.action_name}</span>
              {cfg.display_label && <span style={{ fontSize: '0.72rem', color: '#999' }}>— {cfg.display_label}</span>}
              {hasMapping && (
                <span style={{ fontSize: '0.58rem', fontWeight: 600, padding: '1px 5px', borderRadius: 3, backgroundColor: '#e8daef', color: '#6c3483' }}>sync</span>
              )}
              <span style={{ flex: 1 }} />
              <span style={{
                display: 'inline-block', fontSize: '0.65rem', color: '#aaa',
                transition: 'transform 0.15s', transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
              }}>&#9654;</span>
            </div>

            {/* URL preview (collapsed) */}
            {!isExpanded && (
              <div style={{ fontSize: '0.68rem', color: '#bbb', fontFamily: 'monospace', padding: '0 0.75rem 0.5rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {cfg.path_template}
              </div>
            )}

            {/* Inline edit form (expanded) */}
            {isExpanded && editDrafts[cfg.id] && (
              <EndpointForm
                data={editDrafts[cfg.id]}
                onChange={patch => updateDraft(cfg.id, patch)}
                onSave={() => handleSaveExisting(cfg.id)}
                onDelete={() => handleDelete(cfg.id)}
                saving={saving}
                componentFields={componentFields}
                allConfigs={configs}
              />
            )}
          </div>
        );
      })}

      {/* New endpoint form */}
      {addingNew && (
        <div style={{ border: '1px solid #c4a882', borderRadius: 8, marginBottom: '0.4rem', overflow: 'hidden' }}>
          <div style={{ padding: '0.5rem 0.75rem', fontSize: '0.75rem', fontWeight: 600, color: '#a08060' }}>New Endpoint</div>
          <EndpointForm
            data={addingNew}
            onChange={patch => setAddingNew(prev => prev ? { ...prev, ...patch } : prev)}
            onSave={handleSaveNew}
            saving={saving}
            componentFields={componentFields}
            allConfigs={configs}
          />
          <div style={{ padding: '0 0.75rem 0.5rem' }}>
            <button onClick={() => setAddingNew(null)} style={{ border: 'none', background: 'none', fontSize: '0.7rem', color: '#999', cursor: 'pointer' }}>Cancel</button>
          </div>
        </div>
      )}
      </>)}
    </div>
  );
}
