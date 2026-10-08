'use client';

import { useState, useEffect, useCallback, type CSSProperties } from 'react';
import { ArrowLeft, ArrowRight, ChevronRight, Play, Plus, X } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import type { ComponentApiConfig } from '../../types';
import { REGISTERED_COMPONENTS } from '../display/DisplayBlockRenderer';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

interface ComponentField { name: string; required: boolean }

// Curated metadata OVERLAY only — the component LIST derives from the render
// registry (DisplayBlockRenderer.REGISTERED_COMPONENTS), so a newly registered
// component always appears here even before it gets a description. `internal`
// defaults to true (no component-api template attachment) — set false only for
// components whose data calls are wired through component_api_configs.
const COMPONENT_META: Record<string, { label?: string; description?: string; internal?: boolean; fields?: ComponentField[] }> = {
  roster_editor: {
    label: 'Roster Editor', internal: false,
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
  purchase_order_editor: {
    label: 'Purchase Order Editor', internal: false,
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
  orders_dashboard: {
    label: 'Orders Dashboard', internal: false,
    description: 'View and manage purchase orders. Lists orders by venue with detail expansion and send-to-supplier capability.',
    fields: [
      { name: 'id', required: true }, { name: 'orderNumber', required: false },
      { name: 'supplierName', required: true }, { name: 'orderedBy', required: false },
      { name: 'status', required: true }, { name: 'createdAt', required: false },
      { name: 'subtotal', required: false }, { name: 'tax', required: false },
      { name: 'total', required: false }, { name: 'isReceived', required: false },
    ],
  },
  criteria_editor: {
    label: 'Criteria Editor', internal: false,
    description: 'Edit screening criteria for job applications. Add, remove, and toggle required criteria.',
    fields: [
      { name: 'id', required: true }, { name: 'text', required: true },
      { name: 'required', required: false }, { name: 'category', required: false },
    ],
  },
  hiring_board: {
    label: 'Hiring Board', internal: false,
    description: 'Job listings with candidate management. View jobs, applications, and candidate details.',
    fields: [
      { name: 'id', required: true }, { name: 'title', required: true },
      { name: 'department', required: false }, { name: 'location', required: false },
      { name: 'status', required: true }, { name: 'candidate_count', required: false },
    ],
  },
  generic_table: {
    label: 'Data Table',
    description: 'Renders tabular data from LLM tool responses. Auto-detects columns from the data.',
  },
  roster_table: {
    label: 'Roster Table (Read-only)',
    description: 'Read-only roster view displayed inline in conversations. Shows shifts in a simple table format.',
  },
  chart: {
    label: 'Chart',
    description: 'Visual chart component (bar, line, pie, etc.) rendered from LLM tool call data via render_chart.',
  },
  report_builder: {
    label: 'Report Builder',
    description: 'Drag-and-drop report layout with a 24-column grid. Users arrange charts into custom report layouts.',
  },
  saved_reports_board: {
    label: 'Saved Reports',
    description: 'Lists saved report layouts. Users can open, rename, or delete reports.',
  },
  automated_task_board: {
    label: 'Automated Tasks',
    description: 'Lists automated/scheduled tasks with status, schedule, and run history.',
  },
  automated_task_preview: {
    label: 'Automated Task Preview',
    description: 'Single automated task preview card shown inline in conversations.',
  },
  tool_approval: {
    label: 'Tool Approval Card',
    description: 'Inline approval UI for write tool actions. Shows action summary with approve/reject buttons.',
  },
  invoices_dashboard: {
    label: 'Invoices Dashboard',
    description: 'Outstanding supplier invoices for a venue (self-loading from /invoice-fixes/outstanding). Opens each invoice in the Receive Invoice Editor.',
  },
  receive_invoice_editor: {
    label: 'Receive Invoice Editor',
    description: 'Mirrors LoadedHub’s Receive Invoice screen from a working document, then renders the review engine’s checks and suggestions on top (copy comparisons, PO link, unit fixes, $0-duplicate strike, NEW-item link/create matches). Renders only — all suggestions come from the review_and_receive_invoices consolidator via /invoice-fixes/review; receiving is one PUT via /invoice-fixes/receive.',
  },
  venue_picker: {
    label: 'Venue Picker',
    description: 'Inline venue selection card shown when a conversation needs a venue.',
  },
  stock_picker: {
    label: 'Stock Picker',
    description: 'Inline stock-item selection card used by ordering conversations.',
  },
  dashboard_view: {
    label: 'Dashboard View',
    description: 'Per-domain dashboard page (HR, procurement, reports, marketing, time & attendance).',
  },
  mcp_embed: {
    label: 'MCP Embed',
    description: 'Embeds an external MCP App (connector-served UI) inside a Norm page.',
  },
  connector_connect: {
    label: 'Connector Connect Card',
    description: 'Prompts the user to (re)connect a connector when credentials are missing or expired.',
  },
};

const titleCase = (key: string) => key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

// Derived catalogue: every registered component, external (configurable) first.
const COMPONENTS: { key: string; label: string; description: string; internal: boolean; fields: ComponentField[] }[] =
  [...REGISTERED_COMPONENTS]
    .map((key) => ({
      key,
      label: COMPONENT_META[key]?.label ?? titleCase(key),
      description: COMPONENT_META[key]?.description
        ?? 'No description yet — add this component to COMPONENT_META in ComponentsPanel.tsx.',
      internal: COMPONENT_META[key]?.internal ?? true,
      fields: COMPONENT_META[key]?.fields ?? [],
    }))
    .sort((a, b) => Number(a.internal) - Number(b.internal));
// The component picker groups the two kinds instead of marking each option.
const EXTERNAL_COMPONENTS = COMPONENTS.filter((c) => !c.internal);
const INTERNAL_COMPONENTS = COMPONENTS.filter((c) => c.internal);

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

// Dense admin form: fields (.n-input / .n-select) at 13px; templates, params
// and field names in monospace.
const field: CSSProperties = { width: '100%', fontSize: 'var(--fs-sm)' };
const codeField: CSSProperties = { ...field, fontFamily: 'var(--font-mono)' };
const summaryStyle: CSSProperties = {
  padding: '4px 0', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-soft)', cursor: 'pointer',
};
const colHead: CSSProperties = { fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)' };
const paramName: CSSProperties = {
  fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-mono)', color: 'var(--muted)', overflowWrap: 'anywhere',
};
const codeBlock: CSSProperties = {
  margin: 0, padding: '10px 12px', borderRadius: 'var(--radius)',
  background: 'var(--code-bg)', color: 'var(--code-text)',
  fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', lineHeight: 1.5,
  overflow: 'auto', maxHeight: 300, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
};

// HTTP method pill: reads → info, creates → ok, updates → warn, deletes → error.
const methodTones: Record<string, BadgeTone> = {
  GET: 'info',
  POST: 'ok',
  PUT: 'warn',
  DELETE: 'error',
  PATCH: 'neutral',
};

function EndpointForm({
  data, onChange, onSave, onDelete, onCancel, saving, componentFields, allConfigs,
}: {
  data: Partial<ComponentApiConfig>;
  onChange: (patch: Partial<ComponentApiConfig>) => void;
  onSave: () => void;
  onDelete?: () => void;
  /** A new endpoint's Cancel, shown beside Create. */
  onCancel?: () => void;
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
    <div style={{ padding: '12px 14px 14px', borderTop: '1px solid var(--line)', background: 'var(--surface)' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <input className="n-input" aria-label="Action name" value={data.action_name || ''} onChange={e => onChange({ action_name: e.target.value })} placeholder="action_name" style={{ ...field, flex: '1 1 160px', width: 'auto', minWidth: 0 }} />
        <input className="n-input" aria-label="Label" value={data.display_label || ''} onChange={e => onChange({ display_label: e.target.value })} placeholder="Label (optional)" style={{ ...field, flex: '1 1 160px', width: 'auto', minWidth: 0 }} />
        <select className="n-select" aria-label="Method" value={data.method || 'GET'} onChange={e => onChange({ method: e.target.value })} style={{ ...field, flex: '0 0 112px', width: 112 }}>
          {['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>

      <input className="n-input" aria-label="URL template" value={data.path_template || ''} onChange={e => onChange({ path_template: e.target.value })} placeholder="URL template" style={{ ...codeField, display: 'block', marginBottom: 8 }} />

      {data.method !== 'GET' && data.method !== 'DELETE' && (
        <textarea className="n-input" aria-label="Request body template" value={data.request_body_template || ''} onChange={e => onChange({ request_body_template: e.target.value })} placeholder="Request body template (Jinja2)" rows={2} style={{ ...codeField, display: 'block', marginBottom: 8 }} />
      )}

      {/* Response field mapping (for GET/load endpoints) */}
      {(data.method === 'GET') && (
        <details style={{ marginBottom: 8 }} open={apiFields.length > 0 || Object.values(data.response_field_mapping || {}).some(v => !!v)}>
          <summary style={summaryStyle}>Response field mapping (component ← API)</summary>
          <div style={{ padding: '6px 0 4px 14px' }}>
            {/* Venue selector for fetch */}
            {venues.length > 0 && (
              <div style={{ marginBottom: 10, maxWidth: 360 }}>
                <span className="n-label">Venue</span>
                <select className="n-select" aria-label="Venue" value={fetchVenue} onChange={e => setFetchVenue(e.target.value)} style={field}>
                  {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
                </select>
              </div>
            )}

            {/* Params needed for fetch */}
            {requiredParams.length > 0 && (
              <div style={{ marginBottom: 10 }}>
                <span className="n-label">Parameters</span>
                {requiredParams.map(p => (
                  <div key={p} style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '4px 8px', marginTop: 4 }}>
                    <span style={{ ...paramName, minWidth: 120 }}>{p}</span>
                    <input
                      className="n-input"
                      aria-label={p}
                      value={fetchParams[p] || ''}
                      onChange={e => setFetchParams(prev => ({ ...prev, [p]: e.target.value }))}
                      placeholder={p.includes('date') || p.includes('time') || p.includes('Time') ? 'ISO 8601 datetime' : 'value'}
                      style={{ ...codeField, flex: '1 1 220px', width: 'auto', minWidth: 0 }}
                    />
                  </div>
                ))}
              </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
              <Button size="sm" onClick={handleFetchSample} disabled={fetching}>{fetching ? 'Fetching...' : 'Fetch API fields'}</Button>
              {apiFields.length > 0 && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{apiFields.length} fields discovered</span>}
            </div>

            {/* Header */}
            {componentFields.length > 0 && (
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 20px minmax(0, 1fr)', gap: 6, marginBottom: 4 }}>
                <span style={colHead}>Component field</span>
                <span />
                <span style={colHead}>API field (from response)</span>
              </div>
            )}

            {componentFields.map((cf) => {
              const mapped = (data.response_field_mapping || {})[cf.name] || '';
              const isMapped = !!mapped;
              return (
                <div key={cf.name} style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 20px minmax(0, 1fr)', gap: 6, alignItems: 'center', marginBottom: 4 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 0', minWidth: 0 }}>
                    <span style={{
                      fontSize: 'var(--fs-sm)', fontFamily: 'var(--font-mono)', overflowWrap: 'anywhere',
                      color: cf.required ? 'var(--text)' : 'var(--muted)',
                      fontWeight: cf.required ? 600 : 400,
                    }}>
                      {cf.name}
                    </span>
                    {cf.required && (
                      <span title="Required" style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--error)' }}>*</span>
                    )}
                  </div>
                  <Icon icon={ArrowLeft} size="dense" tone="muted" style={{ justifySelf: 'center' }} />
                  {(() => {
                    // Build dropdown options: fetched API fields + any saved mapped values
                    const savedValues = Object.values(data.response_field_mapping || {}).filter(v => !!v) as string[];
                    const allOptions = Array.from(new Set([...apiFields, ...savedValues])).sort();
                    return (
                      <select
                        className="n-select"
                        aria-label={`API field for ${cf.name}`}
                        value={mapped}
                        onChange={e => {
                          const next = { ...(data.response_field_mapping || {}) };
                          next[cf.name] = e.target.value;
                          onChange({ response_field_mapping: next });
                        }}
                        style={{
                          ...field,
                          color: isMapped ? 'var(--text)' : 'var(--muted)',
                          borderColor: cf.required && !isMapped ? 'var(--error)' : undefined,
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
              <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', margin: '6px 0 0' }}>Click &quot;Fetch API fields&quot; to populate the API field dropdowns.</p>
            )}
          </div>
        </details>
      )}

      {/* Outbound field mapping (for write endpoints) */}
      {(data.method !== 'GET') && (
        <details style={{ marginBottom: 8 }}>
          <summary style={summaryStyle}>Field mapping (component → API)</summary>
          <div style={{ padding: '6px 0 4px 14px' }}>
            <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', margin: '0 0 8px' }}>Map component field names to API parameter names for document sync.</p>
            {Object.entries(data.field_mapping || {}).map(([k, v], i) => (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 20px minmax(0, 1fr) auto', gap: 6, alignItems: 'center', marginBottom: 4 }}>
                <input className="n-input" aria-label="Component field" value={k} onChange={e => {
                  const entries = Object.entries(data.field_mapping || {});
                  entries[i] = [e.target.value, v];
                  onChange({ field_mapping: Object.fromEntries(entries) });
                }} placeholder="component field" style={codeField} />
                <Icon icon={ArrowRight} size="dense" tone="muted" style={{ justifySelf: 'center' }} />
                <input className="n-input" aria-label="API parameter" value={v as string} onChange={e => {
                  const entries = Object.entries(data.field_mapping || {});
                  entries[i] = [k, e.target.value];
                  onChange({ field_mapping: Object.fromEntries(entries) });
                }} placeholder="API param" style={codeField} />
                <IconButton icon={X} label="Remove field" iconSize={14} onClick={() => {
                  const next = { ...(data.field_mapping || {}) };
                  delete next[k];
                  onChange({ field_mapping: next });
                }} />
              </div>
            ))}
            <Button variant="quiet" size="sm" icon={Plus} onClick={() => onChange({ field_mapping: { ...(data.field_mapping || {}), '': '' } })} style={{ marginLeft: -12 }}>Add field</Button>
            <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
              <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text-soft)' }}>ID field</span>
              <input className="n-input" aria-label="ID field" value={data.id_field || ''} onChange={e => onChange({ id_field: e.target.value || null })} placeholder="e.g. shift_id" style={{ ...codeField, width: 160 }} />
            </div>
          </div>
        </details>
      )}

      {/* Test endpoint */}
      {data.id && (
        <TestSection configId={data.id as string} method={data.method || 'GET'} pathTemplate={data.path_template || ''} bodyTemplate={data.request_body_template || ''} />
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginTop: 12 }}>
        <Button variant="primary" size="sm" disabled={saving || !data.action_name || !data.path_template} onClick={onSave}>
          {saving ? 'Saving…' : data.id ? 'Update' : 'Create'}
        </Button>
        {onCancel && <Button variant="quiet" size="sm" onClick={onCancel}>Cancel</Button>}
        {onDelete && (
          <Button variant="danger" size="sm" onClick={onDelete} style={{ marginLeft: 'auto' }}>Delete</Button>
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
    <details style={{ marginBottom: 8 }}>
      <summary style={summaryStyle}>Test endpoint</summary>
      <div style={{ padding: '6px 0 4px 14px' }}>
        {/* Venue */}
        {venues.length > 0 && (
          <div style={{ marginBottom: 10, maxWidth: 360 }}>
            <span className="n-label">Venue</span>
            <select className="n-select" aria-label="Venue" value={venueId} onChange={e => setVenueId(e.target.value)} style={field}>
              {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          </div>
        )}

        {/* Params */}
        {allPlaceholders.length > 0 && (
          <div style={{ marginBottom: 10 }}>
            <span className="n-label">Parameters</span>
            {allPlaceholders.map(p => (
              <div key={p} style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '4px 8px', marginTop: 4 }}>
                <span style={{ ...paramName, minWidth: 130 }}>{p}</span>
                <input
                  className="n-input"
                  aria-label={p}
                  value={testParams[p] || ''}
                  onChange={e => setTestParams(prev => ({ ...prev, [p]: e.target.value }))}
                  placeholder="value"
                  style={{ ...codeField, flex: '1 1 220px', width: 'auto', minWidth: 0 }}
                />
              </div>
            ))}
          </div>
        )}

        {/* Buttons */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
          <Button size="sm" onClick={handlePreview} disabled={loading}>{loading ? '…' : 'Preview request'}</Button>
          {preview && (
            <Button size="sm" icon={Play} onClick={handleExecute} disabled={loading}>{loading ? '…' : 'Execute'}</Button>
          )}
        </div>

        {error && <div role="alert" style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)', marginBottom: 8 }}>{error}</div>}

        {/* Preview */}
        {preview && (
          <div style={{ marginBottom: 10 }}>
            <span className="n-label">Request preview</span>
            <pre style={codeBlock}>
              <span style={{ fontWeight: 600 }}>{preview.method}</span> {preview.url}{'\n\n'}
              {Object.entries(preview.headers).map(([k, v]) => `${k}: ${v}`).join('\n')}{'\n\n'}
              {preview.body ? JSON.stringify(preview.body, null, 2) : '(no body)'}
            </pre>
          </div>
        )}

        {/* Response */}
        {response && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <span className="n-label" style={{ marginBottom: 0 }}>Response</span>
              <Badge tone={response.status_code < 400 ? 'ok' : 'error'}>{response.status_code}</Badge>
            </div>
            <pre style={codeBlock}>
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
  // component key -> owning marketplace App name (the app lens)
  // The marketplace catalog is the authority for app-owned components (owning
  // app + description); COMPONENT_META covers platform chrome and the fields
  // contract, which only the web implementation knows.
  const [catalogInfo, setCatalogInfo] = useState<Record<string, { app: string; description?: string }>>({});

  useEffect(() => {
    apiFetch('/api/marketplace')
      .then(r => (r.ok ? r.json() : { apps: [] }))
      .then((d: { apps?: { name: string; composition?: { components?: { key: string; description?: string }[] } }[] }) => {
        const map: Record<string, { app: string; description?: string }> = {};
        for (const a of d.apps ?? []) {
          for (const c of a.composition?.components ?? []) {
            map[c.key] = { app: a.name, description: c.description || undefined };
          }
        }
        setCatalogInfo(map);
      })
      .catch(() => {});
  }, []);

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
      <div style={{ marginBottom: 16 }}>
        <h3 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>
          Components
        </h3>
        <p style={{ margin: '2px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)', lineHeight: 1.5 }}>
          Configure external API endpoints for each component. Field mappings define how component data maps to API parameters for real-time sync.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div style={{ width: 280, maxWidth: '100%' }}>
          <label className="n-label" htmlFor="components-panel-component">Component</label>
          <select id="components-panel-component" className="n-select" value={selectedComponent} onChange={e => { setSelectedComponent(e.target.value); setExpandedId(null); }} style={{ width: '100%' }}>
            <optgroup label="External">
              {EXTERNAL_COMPONENTS.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
            </optgroup>
            <optgroup label="Internal">
              {INTERNAL_COMPONENTS.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
            </optgroup>
          </select>
        </div>
        {!selectedComponentDef?.internal && (
          <div style={{ width: 220, maxWidth: '100%' }}>
            <label className="n-label" htmlFor="components-panel-connector">Connector</label>
            <select id="components-panel-connector" className="n-select" value={selectedConnector || ''} onChange={e => { setSelectedConnector(e.target.value); setExpandedId(null); }} style={{ width: '100%' }}>
              {connectors.map(c => <option key={c.connector_name} value={c.connector_name}>{c.display_name || c.connector_name}</option>)}
            </select>
          </div>
        )}
      </div>

      {/* Component info */}
      {selectedComponentDef && (
        <div className="n-card" style={{ padding: '14px 16px', marginBottom: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '4px 8px', marginBottom: 4 }}>
            <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{selectedComponentDef.label}</span>
            {catalogInfo[selectedComponentDef.key] && (
              <Badge title="the marketplace App this component belongs to">
                App: {catalogInfo[selectedComponentDef.key].app}
              </Badge>
            )}
            <Badge tone={selectedComponentDef.internal ? 'neutral' : 'info'}>{selectedComponentDef.internal ? 'Internal' : 'External'}</Badge>
            <span style={{ fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-mono)', color: 'var(--muted)' }}>{selectedComponentDef.key}</span>
          </div>
          <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', margin: 0, lineHeight: 1.5 }}>
            {catalogInfo[selectedComponentDef.key]?.description ?? selectedComponentDef.description}
          </p>
        </div>
      )}

      {/* Endpoints section — only for external components */}
      {selectedComponentDef && !selectedComponentDef.internal && (<>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 10 }}>
        <h4 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>Endpoints</h4>
        <Button
          size="sm"
          icon={Plus}
          onClick={() => setAddingNew({
            component_key: selectedComponent,
            connector_name: selectedConnector || '',
            action_name: '', method: 'GET', path_template: '', enabled: true,
          })}
        >Add endpoint</Button>
      </div>

      {filtered.length === 0 && !addingNew && (
        <div className="n-card" style={{ marginBottom: 16 }}>
          <PageState kind="empty" title="No endpoints configured" />
        </div>
      )}

      {filtered.map(cfg => {
        const tone = methodTones[cfg.method] || methodTones.GET;
        const hasMapping = cfg.field_mapping && Object.keys(cfg.field_mapping).length > 0;
        const isExpanded = expandedId === cfg.id;

        return (
          <div key={cfg.id} className="n-card" style={{
            borderColor: isExpanded ? 'var(--brand-soft)' : undefined,
            marginBottom: 8, overflow: 'hidden',
          }}>
            {/* Card header */}
            <button
              type="button"
              aria-expanded={isExpanded}
              onClick={() => toggleExpand(cfg.id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: '10px 14px',
                border: 'none', background: 'none', fontFamily: 'inherit', textAlign: 'left', color: 'var(--text)',
                cursor: 'pointer', outlineOffset: -2,
              }}
            >
              <Badge tone={tone}>{cfg.method}</Badge>
              <span style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: '2px 8px' }}>
                <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)', overflowWrap: 'anywhere' }}>{cfg.action_name}</span>
                {cfg.display_label && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{cfg.display_label}</span>}
              </span>
              {hasMapping && <Badge>Sync</Badge>}
              <Icon
                icon={ChevronRight}
                size="dense"
                tone="muted"
                style={{ transition: 'transform 0.15s', transform: isExpanded ? 'rotate(90deg)' : 'none' }}
              />
            </button>

            {/* URL preview (collapsed) */}
            {!isExpanded && (
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontFamily: 'var(--font-mono)', padding: '0 14px 10px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
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
        <div className="n-card" style={{ borderColor: 'var(--brand-soft)', marginBottom: 8, overflow: 'hidden' }}>
          <div style={{ padding: '10px 14px', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>New endpoint</div>
          <EndpointForm
            data={addingNew}
            onChange={patch => setAddingNew(prev => prev ? { ...prev, ...patch } : prev)}
            onSave={handleSaveNew}
            onCancel={() => setAddingNew(null)}
            saving={saving}
            componentFields={componentFields}
            allConfigs={configs}
          />
        </div>
      )}
      </>)}
    </div>
  );
}
