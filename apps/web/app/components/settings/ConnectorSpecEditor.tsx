'use client';

import { useState, useMemo, useEffect, useRef } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';
import type { ConnectorSpecFull, ConnectorSpecTool, TestRequest } from '../../types';
import { apiFetch, getToken } from '../../lib/api';

interface Props {
  spec: ConnectorSpecFull | null;
  isNew: boolean;
  onSave: (spec: ConnectorSpecFull, isNew: boolean) => void;
  onCancel: () => void;
}

const EMPTY_TOOL: ConnectorSpecTool = {
  action: '',
  method: 'POST',
  path_template: '',
  headers: {},
  required_fields: [],
  field_mapping: {},
  field_descriptions: {},
  field_schema: null,
  request_body_template: null,
  success_status_codes: [200, 201],
  response_ref_path: null,
  timeout_seconds: 30,
  display_component: null,
  display_props: null,
  working_document: null,
  summary_fields: null,
  response_transform: null,
  consolidator_config: null,
};

const EMPTY_SPEC: ConnectorSpecFull = {
  id: '',
  connector_name: '',
  display_name: '',
  category: null,
  execution_mode: 'template',
  auth_type: 'bearer',
  auth_config: {},
  base_url_template: null,
  version: 1,
  enabled: true,
  tools: [],
  api_documentation: null,
  example_requests: [],
  credential_fields: [],
  oauth_config: null,
  test_request: null,
  created_at: '',
  updated_at: null,
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  border: '1px solid #ddd',
  borderRadius: 6,
  fontSize: '0.85rem',
  fontFamily: 'inherit',
  boxSizing: 'border-box',
  outline: 'none',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: '0.78rem',
  fontWeight: 500,
  color: '#555',
  marginBottom: 4,
};

const sectionStyle: React.CSSProperties = {
  marginBottom: '1.25rem',
  padding: '1rem',
  border: '1px solid #edf2f7',
  borderRadius: 8,
  backgroundColor: '#fafafa',
};

/** Textarea for JSON values that keeps a local draft so you can type invalid intermediate JSON. */
function JsonTextarea({ value, onChange, rows, placeholder, style, autoResize }: {
  value: unknown;
  onChange: (parsed: unknown) => void;
  rows?: number;
  placeholder?: string;
  style?: React.CSSProperties;
  autoResize?: boolean;
}) {
  const serialized = value != null ? JSON.stringify(value, null, 2) : '';
  const [draft, setDraft] = useState(serialized);
  const [valid, setValid] = useState(true);
  const ref = useRef<HTMLTextAreaElement>(null);

  // Sync draft when the external value changes (e.g. after save/reload)
  useEffect(() => {
    setDraft(serialized);
    setValid(true);
  }, [serialized]);

  // Auto-resize to content
  useEffect(() => {
    if (autoResize && ref.current) {
      ref.current.style.height = 'auto';
      ref.current.style.height = ref.current.scrollHeight + 'px';
    }
  }, [draft, autoResize]);

  return (
    <textarea
      ref={ref}
      value={draft}
      onChange={e => {
        const raw = e.target.value;
        setDraft(raw);
        if (autoResize && ref.current) {
          ref.current.style.height = 'auto';
          ref.current.style.height = ref.current.scrollHeight + 'px';
        }
        if (!raw.trim()) {
          setValid(true);
          onChange(null);
          return;
        }
        try {
          const parsed = JSON.parse(raw);
          setValid(true);
          onChange(parsed);
        } catch (e) { console.error(e); setValid(false); }
      }}
      rows={autoResize ? 1 : rows}
      placeholder={placeholder}
      style={{
        ...style,
        outline: valid ? undefined : '2px solid #e53e3e',
        ...(autoResize ? { overflow: 'hidden', resize: 'none' } : {}),
      }}
    />
  );
}

/** Plain textarea that auto-expands to fit content. */
function AutoResizeTextarea({ value, onChange, placeholder, style }: {
  value: string;
  onChange: (e: { target: { value: string } }) => void;
  placeholder?: string;
  style?: React.CSSProperties;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (ref.current) {
      ref.current.style.height = 'auto';
      ref.current.style.height = ref.current.scrollHeight + 'px';
    }
  }, [value]);

  return (
    <textarea
      ref={ref}
      value={value}
      onChange={e => {
        onChange(e);
        if (ref.current) {
          ref.current.style.height = 'auto';
          ref.current.style.height = ref.current.scrollHeight + 'px';
        }
      }}
      rows={1}
      placeholder={placeholder}
      style={{ ...style, overflow: 'hidden', resize: 'none' }}
    />
  );
}

// ---------------------------------------------------------------------------
// Response Transform helpers
// ---------------------------------------------------------------------------

function resolveDotPath(obj: Record<string, unknown>, path: string): unknown {
  let current: unknown = obj;
  for (const part of path.split('.')) {
    if (current && typeof current === 'object' && !Array.isArray(current)) {
      current = (current as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return current;
}

function findArray(payload: unknown): unknown[] | null {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === 'object') {
    const p = payload as Record<string, unknown>;
    // Try common keys first
    for (const key of ['data', 'items', 'lines', 'results']) {
      const val = p[key];
      if (Array.isArray(val)) return val;
      if (key === 'data' && val && typeof val === 'object') {
        for (const inner of ['items', 'lines', 'results', 'data']) {
          const iv = (val as Record<string, unknown>)[inner];
          if (Array.isArray(iv)) return iv;
        }
      }
    }
    // Fallback: find the first array value (for consolidator step results)
    for (const val of Object.values(p)) {
      if (Array.isArray(val) && val.length > 0) return val;
    }
  }
  return null;
}

function transformItem(
  item: Record<string, unknown>,
  fields: Record<string, string>,
  flatten: string[],
): Record<string, unknown>[] | Record<string, unknown> {
  // Separate regular fields from array sub-fields
  const regular: Record<string, string> = {};
  const arrayFields: Record<string, Record<string, string>> = {};
  for (const [src, dest] of Object.entries(fields)) {
    if (!dest) continue;
    if (src.includes('[].')) {
      const [arrName, subPath] = src.split('[].', 2);
      if (!arrayFields[arrName]) arrayFields[arrName] = {};
      arrayFields[arrName][subPath] = dest;
    } else {
      regular[src] = dest;
    }
  }

  // Base object from regular fields
  const base: Record<string, unknown> = {};
  for (const [src, dest] of Object.entries(regular)) {
    const val = resolveDotPath(item, src);
    if (val !== undefined) base[dest] = val;
  }

  // Array fields
  for (const [arrName, subFields] of Object.entries(arrayFields)) {
    const arrData = item[arrName];
    if (!Array.isArray(arrData)) continue;
    const transformed = arrData
      .filter(el => el && typeof el === 'object')
      .map(el => {
        const row: Record<string, unknown> = {};
        for (const [subSrc, subDest] of Object.entries(subFields)) {
          const val = resolveDotPath(el as Record<string, unknown>, subSrc);
          if (val !== undefined) row[subDest] = val;
        }
        return row;
      })
      .filter(row => Object.keys(row).length > 0);
    if (flatten.includes(arrName)) {
      return transformed.map(row => ({ ...base, ...row }));
    }
    base[arrName] = transformed;
  }
  return base;
}

function evaluateFilters(item: Record<string, unknown>, filters: { field: string; operator: string; value: string }[]): boolean {
  for (const f of filters) {
    const fieldVal = resolveDotPath(item, f.field);
    const op = f.operator;
    const target = f.value || '';
    if (op === 'is_empty') {
      if (fieldVal != null && fieldVal !== '' && !(Array.isArray(fieldVal) && fieldVal.length === 0)) return false;
    } else if (op === 'is_not_empty') {
      if (fieldVal == null || fieldVal === '' || (Array.isArray(fieldVal) && fieldVal.length === 0)) return false;
    } else if (op === 'equals') {
      if (String(fieldVal ?? '').toLowerCase() !== target.toLowerCase()) return false;
    } else if (op === 'not_equals') {
      if (String(fieldVal ?? '').toLowerCase() === target.toLowerCase()) return false;
    } else if (op === 'contains') {
      if (!String(fieldVal ?? '').toLowerCase().includes(target.toLowerCase())) return false;
    } else if (op === 'gt') {
      if (Number(fieldVal ?? 0) <= Number(target)) return false;
    } else if (op === 'lt') {
      if (Number(fieldVal ?? 0) >= Number(target)) return false;
    }
  }
  return true;
}

function applyTransformPreview(
  payload: unknown,
  fields: Record<string, string>,
  flatten: string[] = [],
  filters: { field: string; operator: string; value: string }[] = [],
): unknown {
  if (!payload || !fields || Object.keys(fields).length === 0) return payload;
  let arr = findArray(payload);
  // Apply filters before field mapping
  if (arr && filters.length > 0) {
    arr = arr.filter(item => typeof item === 'object' && item != null && evaluateFilters(item as Record<string, unknown>, filters));
  }
  if (arr && arr.length > 0 && typeof arr[0] === 'object') {
    const out: Record<string, unknown>[] = [];
    for (const item of arr) {
      const result = transformItem(item as Record<string, unknown>, fields, flatten);
      if (Array.isArray(result)) out.push(...result);
      else out.push(result);
    }
    return out;
  }
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    return transformItem(payload as Record<string, unknown>, fields, flatten);
  }
  return payload;
}

/** Extract all leaf paths from an object, recursing into nested objects and arrays. */
function extractLeafPaths(obj: Record<string, unknown>, prefix = ''): string[] {
  const paths: string[] = [];
  for (const [key, val] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (Array.isArray(val)) {
      // Array — peek at first element
      if (val.length > 0 && val[0] && typeof val[0] === 'object' && !Array.isArray(val[0])) {
        paths.push(...extractLeafPaths(val[0] as Record<string, unknown>, `${path}[]`));
      } else {
        paths.push(path); // array of primitives
      }
    } else if (val && typeof val === 'object') {
      paths.push(...extractLeafPaths(val as Record<string, unknown>, path));
    } else {
      paths.push(path);
    }
  }
  return paths;
}

// ---------------------------------------------------------------------------
// FieldMappingEditor — renders fields with nesting and array expand/flatten
// ---------------------------------------------------------------------------

function FieldMappingEditor({
  fieldEntries,
  flattenList,
  updateField,
  toggleFlatten,
}: {
  fieldEntries: [string, string][];
  flattenList: string[];
  updateField: (oldSrc: string, newSrc: string, newDest: string) => void;
  toggleFlatten: (arrName: string) => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // Identify array parent names (e.g. "lines" from "lines[].stockCode")
  const arrayParents = new Set<string>();
  for (const [src] of fieldEntries) {
    const bracketIdx = src.indexOf('[].');
    if (bracketIdx >= 0) arrayParents.add(src.slice(0, bracketIdx));
  }

  // Group: top-level fields, then array groups
  const topLevel = fieldEntries.filter(([src]) => !src.includes('[].') && !src.includes('.'));
  const nestedObj = fieldEntries.filter(([src]) => !src.includes('[]') && src.includes('.'));
  const arrayEntries = fieldEntries.filter(([src]) => src.includes('[].'));

  // Group array entries by parent
  const arrayGroups: Record<string, [string, string][]> = {};
  for (const entry of arrayEntries) {
    const arrName = entry[0].split('[].')[0];
    if (!arrayGroups[arrName]) arrayGroups[arrName] = [];
    arrayGroups[arrName].push(entry);
  }

  const toggleCollapse = (name: string) => {
    setCollapsed(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  };

  const renderFieldRow = (src: string, dest: string, indent: number) => {
    const included = dest !== '';
    const leaf = src.includes('[].') ? src.split('[].').pop() || src : (src.split('.').pop() || src);
    return (
      <div key={src} style={{
        display: 'grid', gridTemplateColumns: 'auto 1fr 1fr', gap: 6, marginBottom: 2,
        paddingLeft: indent, opacity: included ? 1 : 0.4,
      }}>
        <label style={{ display: 'flex', alignItems: 'center', width: 20, justifyContent: 'center' }}>
          <input
            type="checkbox"
            checked={included}
            onChange={e => updateField(src, src, e.target.checked ? leaf : '')}
            style={{ margin: 0 }}
          />
        </label>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          {indent > 0 && <span style={{ color: '#ccc', fontSize: '0.7rem', marginRight: 4 }}>└</span>}
          <span style={{ fontSize: '0.75rem', fontFamily: 'monospace', color: '#555' }}>
            {src.includes('[].') ? src.split('[].').pop() : src}
          </span>
        </div>
        <input
          value={dest}
          onChange={e => updateField(src, src, e.target.value)}
          placeholder="(excluded)"
          disabled={!included}
          style={{ ...inputStyle, fontSize: '0.78rem', opacity: included ? 1 : 0.5 }}
        />
      </div>
    );
  };

  return (
    <div style={{ marginBottom: '0.5rem' }}>
      <div style={{ fontSize: '0.7rem', color: '#999', marginBottom: 4 }}>
        Fetch a sample response to generate field mappings. Toggle fields to include/exclude, and edit output names.
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr 1fr', gap: '0 6px', marginBottom: 4 }}>
        <div style={{ width: 20 }} />
        <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.03em', padding: '0 2px' }}>Source Field</div>
        <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.03em', padding: '0 2px' }}>Output Name</div>
      </div>

      {/* Top-level fields */}
      {topLevel.map(([src, dest]) => renderFieldRow(src, dest, 0))}

      {/* Nested object fields */}
      {nestedObj.length > 0 && (() => {
        let lastParent = '';
        return nestedObj.map(([src, dest]) => {
          const parent = src.split('.')[0];
          const showHeader = parent !== lastParent;
          lastParent = parent;
          return (
            <div key={src}>
              {showHeader && (
                <div style={{ fontSize: '0.68rem', fontWeight: 600, color: '#888', padding: '4px 0 2px 0', marginTop: 4, borderTop: '1px solid #f0f0f0', paddingLeft: 26 }}>
                  {parent}
                </div>
              )}
              {renderFieldRow(src, dest, 16)}
            </div>
          );
        });
      })()}

      {/* Array groups */}
      {Object.entries(arrayGroups).map(([arrName, entries]) => {
        const isCollapsed = collapsed.has(arrName);
        const isFlattened = flattenList.includes(arrName);
        return (
          <div key={arrName} style={{ marginTop: 4, borderTop: '1px solid #f0f0f0', paddingTop: 4 }}>
            {/* Array parent header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
              <button
                onClick={() => toggleCollapse(arrName)}
                style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0, fontSize: '0.7rem', color: '#888', width: 20, textAlign: 'center' }}
              >{isCollapsed ? '▶' : '▼'}</button>
              <span style={{ fontSize: '0.75rem', fontWeight: 600, fontFamily: 'monospace', color: '#555' }}>
                {arrName}[]
              </span>
              <span style={{ fontSize: '0.62rem', color: '#aaa' }}>{entries.length} fields</span>
              <button
                onClick={() => toggleFlatten(arrName)}
                title={isFlattened ? 'Flattened into parent rows — click to keep nested' : 'Click to flatten into parent rows'}
                style={{
                  border: '1px solid ' + (isFlattened ? '#2563eb' : '#cbd5e1'),
                  borderRadius: 3,
                  backgroundColor: isFlattened ? '#eff6ff' : '#fff',
                  color: isFlattened ? '#2563eb' : '#888',
                  padding: '1px 6px',
                  fontSize: '0.62rem',
                  fontWeight: 600,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >{isFlattened ? '⊟ Flattened' : '⊞ Flatten'}</button>
            </div>
            {/* Array sub-fields */}
            {!isCollapsed && entries.map(([src, dest]) => renderFieldRow(src, dest, 24))}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ResponseTransformSection component
// ---------------------------------------------------------------------------

function ResponseTransformSection({
  op, idx, updateTool, connectorName, isNew, externalSample,
}: {
  op: ConnectorSpecTool;
  idx: number;
  updateTool: (index: number, field: keyof ConnectorSpecTool, value: unknown) => void;
  connectorName: string;
  isNew: boolean;
  externalSample?: unknown;
}) {
  const [sampleResponse, setSampleResponse] = useState<unknown>(externalSample ?? null);
  const [fetchingSample, setFetchingSample] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Sync with external sample when it changes (e.g., from manual test)
  useEffect(() => {
    if (externalSample) {
      setSampleResponse(externalSample);
      // Auto-map fields if no existing transform
      if (!transform.enabled || Object.keys(transform.fields || {}).length === 0) {
        const arr = findArray(externalSample);
        if (arr && arr.length > 0 && typeof arr[0] === 'object') {
          const paths = extractLeafPaths(arr[0] as Record<string, unknown>);
          const merged: Record<string, string> = {};
          for (const p of paths) {
            merged[p] = p.split('.').pop() || p;
          }
          updateTool(idx, 'response_transform', { enabled: true, fields: merged });
        }
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalSample]);
  const [sampleFields, setSampleFields] = useState<Record<string, string>>(() => {
    // Pre-fill from field_descriptions examples
    const init: Record<string, string> = {};
    for (const [key, desc] of Object.entries(op.field_descriptions || {})) {
      const match = String(desc).match(/\(e\.g\.?,?\s*(.+?)\)\s*$/);
      if (match) init[key] = match[1];
    }
    return init;
  });

  const transform = op.response_transform || { enabled: false, fields: {} };
  const fields = transform.fields || {};
  // Sort entries: top-level first, then grouped by parent for nesting display
  const fieldEntries = Object.entries(fields).sort(([a], [b]) => {
    const aParts = a.split('.');
    const bParts = b.split('.');
    // Top-level fields come first
    if (aParts.length === 1 && bParts.length > 1) return -1;
    if (aParts.length > 1 && bParts.length === 1) return 1;
    // Group by parent, then alphabetical within group
    if (aParts.length > 1 && bParts.length > 1) {
      const parentCmp = aParts[0].localeCompare(bParts[0]);
      if (parentCmp !== 0) return parentCmp;
    }
    return a.localeCompare(b);
  });

  const setTransform = (patch: Partial<typeof transform>) => {
    updateTool(idx, 'response_transform', { ...transform, ...patch });
  };

  const updateField = (oldSrc: string, newSrc: string, newDest: string) => {
    const next = { ...fields };
    if (oldSrc !== newSrc) delete next[oldSrc];
    next[newSrc] = newDest;
    setTransform({ fields: next });
  };

  const flattenList: string[] = transform.flatten || [];
  const filterList: { field: string; operator: string; value: string }[] = transform.filters || [];

  // Compute filter stats for display
  const filterStats = useMemo(() => {
    if (!sampleResponse || filterList.length === 0) return null;
    const arr = findArray(sampleResponse);
    if (!arr) return null;
    const total = arr.length;
    const passing = arr.filter(item => typeof item === 'object' && item != null && evaluateFilters(item as Record<string, unknown>, filterList)).length;
    return { total, passing, filtered: total - passing };
  }, [sampleResponse, filterList]);

  const preview = useMemo(() => {
    if (!sampleResponse || !transform.enabled) return null;
    const included = Object.fromEntries(Object.entries(fields).filter(([, v]) => v));
    if (Object.keys(included).length === 0) return null;
    return applyTransformPreview(sampleResponse, included, flattenList, filterList);
  }, [sampleResponse, fields, transform.enabled, flattenList, filterList]);

  const handleFetchSample = async () => {
    setFetchingSample(true);
    setFetchError(null);
    try {
      let res: Response;
      if (op.consolidator_config) {
        // Consolidator tools: test via the consolidator endpoint
        res = await apiFetch('/api/connector-specs/norm/test-consolidator', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ consolidator_config: op.consolidator_config, params: sampleFields }),
        });
      } else {
        res = await apiFetch(`/api/connector-specs/${connectorName}/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ extracted_fields: sampleFields, tool_action: op.action }),
        });
      }
      const data = await res.json();
      if (!res.ok) {
        setFetchError(`Test failed (${res.status}): ${data.detail || JSON.stringify(data)}`);
        return;
      }
      const payload = op.consolidator_config ? (data.data || data) : (data.response_payload || data);
      setSampleResponse(payload);

      // Auto-map all leaf fields from the first array item
      const arr = findArray(payload);
      if (arr && arr.length > 0 && typeof arr[0] === 'object') {
        const paths = extractLeafPaths(arr[0] as Record<string, unknown>);
        // Merge with existing mappings — keep user edits, add new paths
        const existing = { ...fields };
        const merged: Record<string, string> = {};
        for (const p of paths) {
          merged[p] = existing[p] !== undefined ? existing[p] : (p.split('.').pop() || p);
        }
        setTransform({ fields: merged, enabled: true });
      }
    } catch (e) {
      setFetchError(String(e));
    } finally {
      setFetchingSample(false);
    }
  };

  const [previewHeight, setPreviewHeight] = useState(260);
  const resizeRef = useRef<{ startY: number; startH: number } | null>(null);

  const handleResizeMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    resizeRef.current = { startY: e.clientY, startH: previewHeight };
    const onMove = (ev: MouseEvent) => {
      if (!resizeRef.current) return;
      const delta = ev.clientY - resizeRef.current.startY;
      setPreviewHeight(Math.max(120, resizeRef.current.startH + delta));
    };
    const onUp = () => {
      resizeRef.current = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  const rawJson = sampleResponse ? JSON.stringify(sampleResponse, null, 2) : '';
  const previewJson = preview ? JSON.stringify(preview, null, 2) : '';
  const rawSize = sampleResponse ? JSON.stringify(sampleResponse).length : 0;
  const previewSize = preview ? JSON.stringify(preview).length : 0;

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    return `${(bytes / 1024).toFixed(1)} KB`;
  };

  const preStyle: React.CSSProperties = {
    fontSize: '0.75rem', fontFamily: 'monospace', backgroundColor: '#1e1e2e',
    color: '#cdd6f4', padding: '0.6rem', borderRadius: 6, overflow: 'auto',
    height: previewHeight, margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
  };

  return (
    <div style={{ marginTop: '0.75rem', borderTop: '1px solid #e2e8f0', paddingTop: '0.75rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
        <label style={{ fontSize: '0.78rem', fontWeight: 600, color: '#444' }}>Response Transform</label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: '0.78rem', color: '#555' }}>
          <input
            type="checkbox"
            checked={transform.enabled}
            onChange={e => setTransform({ enabled: e.target.checked })}
          />
          {transform.enabled ? 'Enabled' : 'Disabled'}
        </label>
      </div>

      {/* Field mapping editor */}
      {fieldEntries.length > 0 && (
        <FieldMappingEditor
          fieldEntries={fieldEntries}
          flattenList={flattenList}
          updateField={updateField}
          toggleFlatten={(arrName: string) => {
            const next = flattenList.includes(arrName)
              ? flattenList.filter(n => n !== arrName)
              : [...flattenList, arrName];
            setTransform({ flatten: next });
          }}
        />
      )}

      {/* Filters */}
      <div style={{ marginBottom: '0.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <label style={{ fontSize: '0.72rem', fontWeight: 600, color: '#555' }}>Filters</label>
          <button
            onClick={() => setTransform({ filters: [...filterList, { field: '', operator: 'is_empty', value: '' }] })}
            style={{
              padding: '1px 8px', fontSize: '0.68rem', border: '1px solid #ddd', borderRadius: 4,
              backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit', color: '#555',
            }}
          >
            + Add Filter
          </button>
        </div>
        {filterList.length > 0 && (
          <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, overflow: 'hidden' }}>
            {filterList.map((f, fi) => (
              <div key={fi} style={{
                display: 'grid', gridTemplateColumns: '1fr auto 1fr 24px', gap: 4,
                padding: '4px 8px', alignItems: 'center',
                borderBottom: fi < filterList.length - 1 ? '1px solid #f0f0f0' : 'none',
              }}>
                <select
                  value={f.field}
                  onChange={e => {
                    const next = [...filterList];
                    next[fi] = { ...f, field: e.target.value };
                    setTransform({ filters: next });
                  }}
                  style={{ ...inputStyle, fontSize: '0.75rem', padding: '2px 4px' }}
                >
                  <option value="">Select field</option>
                  {Object.keys(fields).map(fk => (
                    <option key={fk} value={fk}>{fk}</option>
                  ))}
                </select>
                <select
                  value={f.operator}
                  onChange={e => {
                    const next = [...filterList];
                    next[fi] = { ...f, operator: e.target.value };
                    setTransform({ filters: next });
                  }}
                  style={{ ...inputStyle, fontSize: '0.72rem', padding: '2px 4px', width: 'auto' }}
                >
                  <option value="is_empty">is empty</option>
                  <option value="is_not_empty">is not empty</option>
                  <option value="equals">equals</option>
                  <option value="not_equals">not equals</option>
                  <option value="contains">contains</option>
                  <option value="gt">&gt;</option>
                  <option value="lt">&lt;</option>
                </select>
                {!['is_empty', 'is_not_empty'].includes(f.operator) ? (
                  <input
                    type="text"
                    value={f.value}
                    onChange={e => {
                      const next = [...filterList];
                      next[fi] = { ...f, value: e.target.value };
                      setTransform({ filters: next });
                    }}
                    placeholder="value"
                    style={{ ...inputStyle, fontSize: '0.75rem', padding: '2px 6px' }}
                  />
                ) : <div />}
                <button
                  onClick={() => {
                    const next = filterList.filter((_, i) => i !== fi);
                    setTransform({ filters: next });
                  }}
                  style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#e53e3e', fontSize: '0.82rem', padding: 0 }}
                >
                  &times;
                </button>
              </div>
            ))}
          </div>
        )}
        {filterStats && (
          <div style={{ fontSize: '0.68rem', color: '#888', marginTop: 3 }}>
            Showing {filterStats.passing.toLocaleString()} of {filterStats.total.toLocaleString()} items
            ({filterStats.filtered.toLocaleString()} filtered out)
          </div>
        )}
      </div>

      {/* Request fields for sample fetch */}
      {(!isNew && (Object.keys(op.field_mapping || {}).length > 0 || (op.consolidator_config && (op.required_fields || []).length > 0))) ? (
        <div style={{ marginBottom: '0.5rem', padding: '0.5rem', backgroundColor: '#f8fafc', borderRadius: 6, border: '1px solid #e2e8f0' }}>
          <div style={{ fontSize: '0.7rem', fontWeight: 600, color: '#666', marginBottom: 4 }}>Test Parameters</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
            {(op.consolidator_config ? (op.required_fields || []) : Object.keys(op.field_mapping || {})).map(fieldKey => (
              <div key={fieldKey}>
                <label style={{ fontSize: '0.68rem', color: '#888', display: 'flex', alignItems: 'center', gap: 2 }}>
                  {fieldKey}
                  {(op.required_fields || []).includes(fieldKey) && (
                    <span style={{ color: '#e53e3e' }}>*</span>
                  )}
                </label>
                <input
                  value={sampleFields[fieldKey] || ''}
                  onChange={e => setSampleFields(prev => ({ ...prev, [fieldKey]: e.target.value }))}
                  placeholder={op.field_descriptions?.[fieldKey] || fieldKey}
                  style={{ ...inputStyle, fontSize: '0.75rem', padding: '3px 6px' }}
                />
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {/* Fetch sample + auto-map */}
      {!isNew && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: '0.5rem' }}>
          <button
            onClick={handleFetchSample}
            disabled={fetchingSample}
            style={{
              padding: '4px 10px', fontSize: '0.72rem', fontWeight: 500,
              border: '1px solid #cbd5e1', borderRadius: 5, backgroundColor: '#fff',
              color: '#555', cursor: fetchingSample ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
            }}
          >{fetchingSample ? 'Fetching...' : 'Fetch Sample Response'}</button>
          {fetchError && (
            <span style={{ fontSize: '0.72rem', color: '#e53e3e' }}>{fetchError}</span>
          )}
        </div>
      )}

      {/* Split preview */}
      {sampleResponse ? (
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div>
              <div style={{ fontSize: '0.68rem', fontWeight: 600, color: '#888', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                Raw Response <span style={{ fontWeight: 400, color: '#aaa' }}>— {formatSize(rawSize)}</span>
              </div>
              <pre style={preStyle}>{rawJson}</pre>
            </div>
            <div>
              <div style={{ fontSize: '0.68rem', fontWeight: 600, color: '#2563eb', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                Transformed Preview <span style={{ fontWeight: 400, color: previewSize < rawSize ? '#22c55e' : '#aaa' }}>— {formatSize(previewSize)}{previewSize > 0 && rawSize > 0 && previewSize < rawSize ? ` (${Math.round((1 - previewSize / rawSize) * 100)}% smaller)` : ''}</span>
              </div>
              <pre style={{ ...preStyle, backgroundColor: '#1a2332' }}>
                {previewJson || '(configure field mappings above)'}
              </pre>
            </div>
          </div>
          {/* Resize handle */}
          <div
            onMouseDown={handleResizeMouseDown}
            style={{
              height: 8, cursor: 'row-resize', display: 'flex', alignItems: 'center',
              justifyContent: 'center', marginTop: 2, borderRadius: 4,
              userSelect: 'none',
            }}
            title="Drag to resize"
          >
            <div style={{ width: 40, height: 3, backgroundColor: '#cbd5e1', borderRadius: 2 }} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Consolidator Tool Editor — chat-based AI builder
// ---------------------------------------------------------------------------

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  toolEvents?: { type: string; name?: string; input?: Record<string, unknown>; result?: Record<string, unknown>; config?: Record<string, unknown> }[];
}

function ConsolidatorToolEditor({
  op, idx, updateTool, setForm, labelStyle, inputStyle,
}: {
  op: ConnectorSpecTool;
  idx: number;
  updateTool: (index: number, field: keyof ConnectorSpecTool, value: unknown) => void;
  setForm: React.Dispatch<React.SetStateAction<ConnectorSpecFull>>;
  labelStyle: React.CSSProperties;
  inputStyle: React.CSSProperties;
}) {
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [streamingEvents, setStreamingEvents] = useState<ChatMessage['toolEvents']>([]);
  const [showConfig, setShowConfig] = useState(false);
  const [showTest, setShowTest] = useState(false);
  const [chatHeight] = useState(320);
  const [testParams, setTestParams] = useState<Record<string, string>>(() => {
    const params: Record<string, string> = {};
    for (const f of op.required_fields || []) params[f] = '';
    return params;
  });
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll within the chat container only (not the page)
  useEffect(() => {
    const el = chatEndRef.current;
    if (el?.parentElement) {
      el.parentElement.scrollTop = el.parentElement.scrollHeight;
    }
  }, [chatMessages, streamingText, streamingEvents]);

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await apiFetch('/api/connector-specs/norm/test-consolidator', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          consolidator_config: op.consolidator_config,
          params: testParams,
        }),
      });
      setTestResult(await res.json());
    } catch (err) {
      setTestResult({ success: false, error: String(err) });
    } finally {
      setTesting(false);
    }
  };

  const handleSend = async () => {
    const msg = chatInput.trim();
    if (!msg || chatLoading) return;

    const userMsg: ChatMessage = { role: 'user', content: msg };
    const newMessages = [...chatMessages, userMsg];
    setChatMessages(newMessages);
    setChatInput('');
    setChatLoading(true);
    setStreamingText('');
    setStreamingEvents([]);

    try {
      const currentTool = {
        action: op.action,
        description: op.description || '',
        required_fields: op.required_fields || [],
        field_descriptions: op.field_descriptions || {},
        consolidator_config: op.consolidator_config || {},
      };

      // Build messages for the API (simple role/content format)
      const apiMessages = newMessages.map(m => ({
        role: m.role,
        content: m.content,
      }));

      const token = getToken();
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const res = await fetch(`/api/connector-specs/norm/consolidator-chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ messages: apiMessages, current_tool: currentTool }),
      });

      if (!res.ok || !res.body) {
        const err = await res.text();
        setChatMessages(prev => [...prev, { role: 'assistant', content: `Error: ${err}` }]);
        setChatLoading(false);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let fullText = '';
      const events: ChatMessage['toolEvents'] = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (!raw || raw === ':') continue;
          try {
            const event = JSON.parse(raw);

            if (event.type === 'text') {
              fullText += event.text;
              setStreamingText(fullText);
            } else if (event.type === 'tool_use') {
              events.push({ type: 'tool_use', name: event.name, input: event.input });
              setStreamingEvents([...events]);
            } else if (event.type === 'tool_result') {
              events.push({ type: 'tool_result', name: event.name, result: event.result });
              setStreamingEvents([...events]);
            } else if (event.type === 'save') {
              const cfg = event.config;
              if (cfg) {
                if (cfg.action) updateTool(idx, 'action', cfg.action);
                if (cfg.description) updateTool(idx, 'description', cfg.description);
                if (cfg.required_fields) updateTool(idx, 'required_fields', cfg.required_fields);
                if (cfg.field_descriptions) updateTool(idx, 'field_descriptions', cfg.field_descriptions);
                if (cfg.consolidator_config) updateTool(idx, 'consolidator_config', cfg.consolidator_config);
              }
              events.push({ type: 'save', config: cfg });
              setStreamingEvents([...events]);
            } else if (event.type === 'complete' || event.type === 'error') {
              if (event.type === 'error') {
                fullText += `\n\nError: ${event.message}`;
              }
            }
          } catch { /* ignore parse errors */ }
        }
      }

      // Finalize: add assistant message with accumulated text + events
      setChatMessages(prev => [...prev, {
        role: 'assistant',
        content: fullText,
        toolEvents: events.length > 0 ? events : undefined,
      }]);
      setStreamingText('');
      setStreamingEvents([]);
    } catch (err) {
      setChatMessages(prev => [...prev, { role: 'assistant', content: `Error: ${err}` }]);
    } finally {
      setChatLoading(false);
    }
  };

  const renderToolEvent = (event: NonNullable<ChatMessage['toolEvents']>[number], i: number) => {
    if (event.type === 'tool_use') {
      const displayName = (event.name || '').replace('__', '.');
      return (
        <div key={i} style={{ fontSize: '0.72rem', color: '#7c3aed', padding: '4px 8px', backgroundColor: '#faf5ff', borderRadius: 4, marginBottom: 4, userSelect: 'text' }}>
          Calling <strong>{displayName}</strong>
          {event.input && Object.keys(event.input).length > 0 && (
            <span style={{ color: '#999', marginLeft: 4 }}>
              ({Object.entries(event.input).map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', ').slice(0, 120)})
            </span>
          )}
        </div>
      );
    }
    if (event.type === 'tool_result' && event.result) {
      const r = event.result as Record<string, unknown>;
      const isSuccess = r.success !== false;
      const summary = r._summary as string | undefined;
      const preview = r._preview as unknown[] | undefined;
      const hasData = r.data != null || preview != null;
      return (
        <div key={i} style={{ fontSize: '0.72rem', padding: '4px 8px', backgroundColor: isSuccess ? '#f0fdf4' : '#fef2f2', borderRadius: 4, marginBottom: 4, border: `1px solid ${isSuccess ? '#bbf7d0' : '#fecaca'}`, userSelect: 'text' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
            <span style={{ color: isSuccess ? '#16a34a' : '#dc2626' }}>{isSuccess ? '\u2713' : '\u2717'}</span>
            <span>{isSuccess ? 'Success' : 'Failed'}</span>
            {summary ? <span style={{ color: '#888', marginLeft: 4 }}>({summary})</span> : null}
            {r.error ? <span style={{ color: '#dc2626' }}>— {String(r.error)}</span> : null}
          </div>
          {hasData && (
            <details>
              <summary style={{ fontSize: '0.65rem', color: '#888', cursor: 'pointer' }}>
                {preview ? `Preview (${(preview as unknown[]).length} of ${summary || '?'})` : 'Response data'}
              </summary>
              <pre style={{ fontSize: '0.62rem', margin: '4px 0 0', overflow: 'auto', maxHeight: 150, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {JSON.stringify(preview || r.data, null, 2)}
              </pre>
            </details>
          )}
        </div>
      );
    }
    if (event.type === 'save') {
      return (
        <div key={i} style={{ fontSize: '0.72rem', color: '#16a34a', padding: '4px 8px', backgroundColor: '#f0fdf4', borderRadius: 4, marginBottom: 4 }}>
          Config saved to tool
        </div>
      );
    }
    return null;
  };

  return (
    <div>
      {/* Action + Description */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <div>
          <label style={labelStyle}>Action</label>
          <input
            type="text"
            value={op.action}
            onChange={e => updateTool(idx, 'action', e.target.value)}
            placeholder="get_stock_comparison"
            style={inputStyle}
          />
        </div>
        <div>
          <label style={labelStyle}>Description</label>
          <input
            type="text"
            value={op.description || ''}
            onChange={e => updateTool(idx, 'description', e.target.value || undefined)}
            placeholder="What this consolidator does"
            style={inputStyle}
          />
        </div>
      </div>

      {/* Chat area */}
      <div style={{
        border: '1px solid #e2e8f0', borderRadius: '8px 8px 0 0',
        height: chatHeight, minHeight: 120, overflowY: 'auto', padding: '0.5rem',
        backgroundColor: '#fafafa', userSelect: 'text', resize: 'vertical',
      }}>
        {chatMessages.length === 0 && !streamingText && (
          <div style={{ color: '#aaa', fontSize: '0.78rem', textAlign: 'center', padding: '2rem 1rem' }}>
            Describe what this consolidator should do, or ask to test/fix the current config.
          </div>
        )}
        {chatMessages.map((msg, i) => (
          <div key={i} style={{ marginBottom: '0.5rem' }}>
            <div style={{
              fontSize: '0.62rem', fontWeight: 600, color: msg.role === 'user' ? '#7c3aed' : '#555',
              textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 2,
            }}>
              {msg.role === 'user' ? 'You' : 'AI'}
            </div>
            {msg.toolEvents?.map((ev, j) => renderToolEvent(ev, j))}
            <div style={{ fontSize: '0.78rem', color: '#333', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
              {msg.content}
            </div>
          </div>
        ))}
        {/* Streaming state */}
        {chatLoading && (
          <div style={{ marginBottom: '0.5rem' }}>
            <div style={{ fontSize: '0.62rem', fontWeight: 600, color: '#555', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 2 }}>AI</div>
            {streamingEvents?.map((ev, j) => renderToolEvent(ev, j))}
            <div style={{ fontSize: '0.78rem', color: '#333', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
              {streamingText || <span style={{ color: '#aaa' }}>Thinking...</span>}
            </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {/* Input */}
      <div style={{ display: 'flex', gap: '0.3rem', marginBottom: '0.75rem' }}>
        <input
          type="text"
          value={chatInput}
          onChange={e => setChatInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !chatLoading) handleSend(); }}
          placeholder="e.g. fetch stocktake templates, filter by category, then get stock on hand..."
          disabled={chatLoading}
          style={{ ...inputStyle, flex: 1, fontSize: '0.78rem', padding: '6px 10px' }}
        />
        <button
          onClick={handleSend}
          disabled={chatLoading || !chatInput.trim()}
          style={{
            padding: '6px 16px', fontSize: '0.78rem', fontWeight: 600,
            backgroundColor: chatLoading ? '#a78bfa' : '#7c3aed', color: '#fff',
            border: 'none', borderRadius: 6, flexShrink: 0,
            cursor: (chatLoading || !chatInput.trim()) ? 'not-allowed' : 'pointer',
            fontFamily: 'inherit',
          }}
        >
          {chatLoading ? '...' : 'Send'}
        </button>
      </div>

      {/* Manual test */}
      <div style={{ borderTop: '1px solid #e2e8f0', paddingTop: '0.5rem', marginBottom: '0.5rem' }}>
        <div
          onClick={() => setShowTest(!showTest)}
          style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', userSelect: 'none', marginBottom: showTest ? '0.4rem' : 0 }}
        >
          <span style={{ fontSize: '0.7rem', color: '#888' }}>{showTest ? '\u25BC' : '\u25B6'}</span>
          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666' }}>Manual Test</span>
        </div>
        {showTest && (
          <div>
            {(op.required_fields || []).length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.4rem' }}>
                {(op.required_fields || []).map(key => (
                  <div key={key} style={{ flex: '1 1 160px' }}>
                    <label style={{ fontSize: '0.62rem', color: '#888', textTransform: 'uppercase' }}>{key}</label>
                    <input
                      type="text"
                      value={testParams[key] || ''}
                      onChange={e => setTestParams(prev => ({ ...prev, [key]: e.target.value }))}
                      placeholder={(op.field_descriptions || {})[key] || key}
                      style={{ ...inputStyle, fontSize: '0.78rem', padding: '4px 8px', width: '100%' }}
                    />
                  </div>
                ))}
              </div>
            )}
            <button
              onClick={handleTest}
              disabled={testing}
              style={{
                padding: '5px 14px', fontSize: '0.78rem', fontWeight: 600,
                backgroundColor: '#111', color: '#fff', border: 'none', borderRadius: 6,
                cursor: testing ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
                opacity: testing ? 0.6 : 1,
              }}
            >
              {testing ? 'Testing...' : 'Test'}
            </button>
            {testResult ? (
              <div style={{
                marginTop: '0.4rem', padding: '0.5rem', userSelect: 'text',
                backgroundColor: testResult.success ? '#f0fdf4' : '#fef2f2',
                border: `1px solid ${testResult.success ? '#bbf7d0' : '#fecaca'}`,
                borderRadius: 6,
              }}>
                {(testResult._steps as { id: string; status: string; error?: string; duration_ms?: number; result_preview?: Record<string, unknown> }[] || []).map((s, i) => (
                  <div key={i} style={{ marginBottom: 4 }}>
                    <div style={{ fontSize: '0.72rem', display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ color: s.status === 'success' ? '#16a34a' : '#dc2626' }}>{s.status === 'success' ? '\u2713' : '\u2717'}</span>
                      <span style={{ fontWeight: 500 }}>{s.id}</span>
                      {s.duration_ms != null && <span style={{ color: '#999' }}>({s.duration_ms}ms)</span>}
                      {s.error && <span style={{ color: '#dc2626' }}>— {s.error}</span>}
                    </div>
                    {s.result_preview && (
                      <details style={{ marginLeft: 18, marginTop: 2 }}>
                        <summary style={{ fontSize: '0.65rem', color: '#888', cursor: 'pointer' }}>Step result</summary>
                        <pre style={{ fontSize: '0.62rem', margin: '2px 0 0', overflow: 'auto', maxHeight: 120, backgroundColor: '#f8fafc', padding: '4px 6px', borderRadius: 4, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                          {JSON.stringify(s.result_preview, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                ))}
                <details style={{ marginTop: 4 }}>
                  <summary style={{ fontSize: '0.68rem', color: '#888', cursor: 'pointer' }}>Raw JSON</summary>
                  <pre style={{ fontSize: '0.65rem', margin: '4px 0 0', overflow: 'auto', maxHeight: 150 }}>
                    {JSON.stringify(testResult, null, 2)}
                  </pre>
                </details>
              </div>
            ) : null}
          </div>
        )}
      </div>

      {/* Collapsible config JSON */}
      <div style={{ borderTop: '1px solid #e2e8f0', paddingTop: '0.5rem' }}>
        <div
          onClick={() => setShowConfig(!showConfig)}
          style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', userSelect: 'none', marginBottom: showConfig ? '0.4rem' : 0 }}
        >
          <span style={{ fontSize: '0.7rem', color: '#888' }}>{showConfig ? '\u25BC' : '\u25B6'}</span>
          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666' }}>Config JSON</span>
          <span style={{ fontSize: '0.68rem', color: '#aaa' }}>
            {((op.consolidator_config as Record<string, unknown>)?.steps as unknown[] || []).length} steps
          </span>
        </div>
        {showConfig && (
          <JsonTextarea
            value={op.consolidator_config}
            onChange={v => updateTool(idx, 'consolidator_config', v)}
            rows={10}
            autoResize
            style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.78rem' }}
          />
        )}
      </div>

      {/* Response Transform */}
      <ResponseTransformSection
        op={op}
        idx={idx}
        updateTool={updateTool}
        connectorName="norm"
        isNew={false}
        externalSample={testResult?.success ? testResult.data : undefined}
      />
    </div>
  );
}

export default function ConnectorSpecEditor({ spec, isNew, onSave, onCancel }: Props) {
  const [form, setForm] = useState<ConnectorSpecFull>(spec ?? { ...EMPTY_SPEC });
  const [saving, setSaving] = useState(false);
  const [collapsedTools, setCollapsedTools] = useState<Set<number>>(() => new Set(form.tools.map((_, i) => i)));

  // Consolidator builder state
  const [showConsolidatorBuilder, setShowConsolidatorBuilder] = useState(false);
  const [consolidatorPrompt, setConsolidatorPrompt] = useState('');
  const [consolidatorGenerating, setConsolidatorGenerating] = useState(false);
  const [consolidatorResult, setConsolidatorResult] = useState<Record<string, unknown> | null>(null);
  const [consolidatorTestParams, setConsolidatorTestParams] = useState<Record<string, string>>({});
  const [consolidatorTestResult, setConsolidatorTestResult] = useState<Record<string, unknown> | null>(null);
  const [consolidatorTesting, setConsolidatorTesting] = useState(false);

  // Base URL mode state
  const [baseUrlMode, setBaseUrlMode] = useState<'fixed' | 'dynamic'>(() =>
    (form.base_url_template || '').includes('{{ creds.') ? 'dynamic' : 'fixed'
  );
  const [domainFieldKey, setDomainFieldKey] = useState<string>(() => {
    const match = (form.base_url_template || '').match(/\{\{\s*creds\.(\w+)\s*\}\}/);
    return match?.[1] || 'subdomain';
  });
  const [domainFieldLabel, setDomainFieldLabel] = useState<string>(() => {
    const key = (form.base_url_template || '').match(/\{\{\s*creds\.(\w+)\s*\}\}/)?.[1];
    const existing = form.credential_fields.find(cf => cf.key === key);
    return existing?.label || 'Domain';
  });

  // Convert {{ creds.KEY }} to {{ domain }} for display
  const displayPattern = (template: string) =>
    template.replace(/\{\{\s*creds\.\w+\s*\}\}/g, '{{ domain }}');

  // Convert {{ domain }} to {{ creds.KEY }} for storage
  const storagePattern = (display: string, key: string) =>
    display.replace(/\{\{\s*domain\s*\}\}/g, `{{ creds.${key} }}`);

  // Sync credential field when dynamic mode params change
  const syncDomainCredField = (key: string, label: string, oldKey?: string) => {
    setForm(prev => {
      const filtered = prev.credential_fields.filter(cf => cf.key !== (oldKey || key));
      return {
        ...prev,
        credential_fields: [...filtered, { key, label, secret: false }],
      };
    });
  };

  const removeDomainCredField = (key: string) => {
    setForm(prev => ({
      ...prev,
      credential_fields: prev.credential_fields.filter(cf => cf.key !== key),
    }));
  };

  // Try Tool state
  const [tryToolAction, setTryToolAction] = useState('');
  const [tryToolFields, setTryToolFields] = useState<Record<string, string>>({});
  const [tryDryRunResult, setTryDryRunResult] = useState<Record<string, unknown> | null>(null);
  const [tryTestResult, setTryTestResult] = useState<Record<string, unknown> | null>(null);
  const [tryLoading, setTryLoading] = useState<'render' | 'test' | null>(null);
  const [tryError, setTryError] = useState<string | null>(null);

  const selectedTool = useMemo(
    () => form.tools.find(t => t.action === tryToolAction) ?? null,
    [form.tools, tryToolAction],
  );

  const toggleTool = (index: number) => {
    setCollapsedTools(prev => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index); else next.add(index);
      return next;
    });
  };

  const update = <K extends keyof ConnectorSpecFull>(key: K, value: ConnectorSpecFull[K]) => {
    setForm(prev => ({ ...prev, [key]: value }));
  };

  const updateTool = (index: number, field: keyof ConnectorSpecTool, value: unknown) => {
    setForm(prev => ({
      ...prev,
      tools: prev.tools.map((op, i) =>
        i === index ? { ...op, [field]: value } : op
      ),
    }));
  };

  const addTool = () => {
    setForm(prev => ({
      ...prev,
      tools: [...prev.tools, { ...EMPTY_TOOL }],
    }));
  };

  const removeTool = (index: number) => {
    setForm(prev => ({
      ...prev,
      tools: prev.tools.filter((_, i) => i !== index),
    }));
  };

  // Drag-to-reorder tools
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null);

  const handleDragEnd = () => {
    if (dragIdx !== null && dragOverIdx !== null && dragIdx !== dragOverIdx) {
      setForm(prev => {
        const tools = [...prev.tools];
        const [moved] = tools.splice(dragIdx, 1);
        tools.splice(dragOverIdx, 0, moved);
        // Rebuild collapsed set based on action names to preserve state
        const collapsedActions = new Set(
          Array.from(collapsedTools).map(i => prev.tools[i]?.action).filter(Boolean)
        );
        setCollapsedTools(new Set(
          tools.map((t, i) => collapsedActions.has(t.action) ? i : -1).filter(i => i >= 0)
        ));
        return { ...prev, tools };
      });
    }
    setDragIdx(null);
    setDragOverIdx(null);
  };

  const addCredentialField = () => {
    setForm(prev => ({
      ...prev,
      credential_fields: [...prev.credential_fields, { key: '', label: '', secret: false }],
    }));
  };

  const updateCredentialField = (index: number, field: string, value: string | boolean) => {
    setForm(prev => ({
      ...prev,
      credential_fields: prev.credential_fields.map((cf, i) =>
        i === index ? { ...cf, [field]: value } : cf
      ),
    }));
  };

  const removeCredentialField = (index: number) => {
    setForm(prev => ({
      ...prev,
      credential_fields: prev.credential_fields.filter((_, i) => i !== index),
    }));
  };

  const handleTryRun = async (mode: 'render' | 'test') => {
    if (!tryToolAction) return;
    setTryLoading(mode);
    setTryError(null);
    if (mode === 'render') setTryDryRunResult(null);
    else setTryTestResult(null);

    const endpoint = mode === 'render' ? 'dry-run' : 'test';
    try {
      const res = await apiFetch(`/api/connector-specs/${form.connector_name}/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          extracted_fields: tryToolFields,
          tool_action: tryToolAction,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setTryError(`${mode === 'render' ? 'Render' : 'Test'} failed (${res.status}): ${data.detail || JSON.stringify(data)}`);
        // Still show whatever data we got (e.g. rendered_request on partial failures)
        if (mode === 'test') setTryTestResult(data);
      } else if (mode === 'render') {
        setTryDryRunResult(data.rendered_request || data);
      } else {
        setTryTestResult(data);
      }
    } catch (err) {
      setTryError(`Network error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setTryLoading(null);
    }
  };

  const handleSubmit = async () => {
    setSaving(true);
    try {
      await onSave(form, isNew);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      {/* Consolidator Builder Modal */}
      {showConsolidatorBuilder && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.4)', zIndex: 1000,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{
            backgroundColor: '#fff', borderRadius: 12, width: '90%', maxWidth: 700,
            maxHeight: '85vh', overflow: 'auto', padding: '1.5rem',
            boxShadow: '0 8px 30px rgba(0,0,0,0.15)',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 700 }}>Create Consolidator</h3>
              <button onClick={() => { setShowConsolidatorBuilder(false); setConsolidatorResult(null); setConsolidatorTestResult(null); }} style={{
                border: 'none', background: 'none', fontSize: '1.2rem', cursor: 'pointer', color: '#999',
              }}>&times;</button>
            </div>

            {/* Step 1: Describe */}
            <div style={{ marginBottom: '1rem' }}>
              <label style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', display: 'block', marginBottom: 4 }}>
                Describe what this consolidator should do
              </label>
              <textarea
                value={consolidatorPrompt}
                onChange={e => setConsolidatorPrompt(e.target.value)}
                placeholder="e.g., Get stock on hand for a specific item today and 4 weeks ago, and return a comparison showing current vs historical quantity"
                rows={3}
                style={{
                  width: '100%', padding: '0.5rem', border: '1px solid #ddd', borderRadius: 6,
                  fontSize: '0.82rem', fontFamily: 'inherit', resize: 'vertical',
                }}
              />
              <button
                onClick={async () => {
                  if (!consolidatorPrompt.trim()) return;
                  setConsolidatorGenerating(true);
                  setConsolidatorResult(null);
                  try {
                    const res = await apiFetch('/api/connector-specs/norm/generate-consolidator', {
                      method: 'POST',
                      body: JSON.stringify({ description: consolidatorPrompt }),
                    });
                    const data = await res.json();
                    if (res.ok) {
                      setConsolidatorResult(data);
                      // Pre-fill test params
                      const fields = (data.required_fields || []) as string[];
                      const params: Record<string, string> = {};
                      fields.forEach((f: string) => { params[f] = ''; });
                      setConsolidatorTestParams(params);
                    } else {
                      setConsolidatorResult({ error: data.detail || 'Generation failed' });
                    }
                  } catch (err) {
                    setConsolidatorResult({ error: String(err) });
                  }
                  setConsolidatorGenerating(false);
                }}
                disabled={consolidatorGenerating || !consolidatorPrompt.trim()}
                style={{
                  marginTop: '0.5rem', padding: '6px 16px', fontSize: '0.78rem', fontWeight: 600,
                  backgroundColor: '#6366f1', color: '#fff', border: 'none', borderRadius: 6,
                  cursor: consolidatorGenerating ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
                }}
              >
                {consolidatorGenerating ? 'Generating...' : 'Generate with AI'}
              </button>
            </div>

            {/* Step 2: Preview generated config */}
            {(consolidatorResult && !consolidatorResult.error) ? (
              <div style={{ marginBottom: '1rem' }}>
                <label style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', display: 'block', marginBottom: 4 }}>
                  Generated Config
                </label>
                <div style={{ fontSize: '0.78rem', marginBottom: '0.5rem' }}>
                  <strong>{String(consolidatorResult.action || '')}</strong> — {String(consolidatorResult.description || '')}
                </div>
                <pre style={{
                  padding: '0.5rem', backgroundColor: '#1a202c', color: '#e2e8f0',
                  borderRadius: 6, fontSize: '0.7rem', overflow: 'auto', maxHeight: 200,
                }}>
                  {JSON.stringify(consolidatorResult.consolidator_config || consolidatorResult, null, 2)}
                </pre>

                {/* Step 3: Test */}
                <div style={{ marginTop: '0.75rem' }}>
                  <label style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', display: 'block', marginBottom: 4 }}>
                    Test with sample inputs
                  </label>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.5rem' }}>
                    {Object.keys(consolidatorTestParams).map(key => (
                      <div key={key} style={{ flex: '1 1 200px' }}>
                        <label style={{ fontSize: '0.65rem', color: '#888' }}>{key}</label>
                        <input
                          value={consolidatorTestParams[key] || ''}
                          onChange={e => setConsolidatorTestParams(prev => ({ ...prev, [key]: e.target.value }))}
                          placeholder={String((consolidatorResult.field_descriptions as Record<string, string>)?.[key] || key)}
                          style={{
                            width: '100%', padding: '3px 6px', border: '1px solid #ddd',
                            borderRadius: 4, fontSize: '0.78rem', fontFamily: 'inherit',
                          }}
                        />
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={async () => {
                      setConsolidatorTesting(true);
                      setConsolidatorTestResult(null);
                      try {
                        const res = await apiFetch('/api/connector-specs/norm/test-consolidator', {
                          method: 'POST',
                          body: JSON.stringify({
                            consolidator_config: consolidatorResult?.consolidator_config || {},
                            params: consolidatorTestParams,
                          }),
                        });
                        const data = await res.json();
                        setConsolidatorTestResult(data);
                      } catch (err) {
                        setConsolidatorTestResult({ success: false, error: String(err) });
                      }
                      setConsolidatorTesting(false);
                    }}
                    disabled={consolidatorTesting}
                    style={{
                      padding: '5px 14px', fontSize: '0.75rem', fontWeight: 600,
                      border: '1px solid #ddd', borderRadius: 6, backgroundColor: '#fff',
                      cursor: consolidatorTesting ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
                    }}
                  >
                    {consolidatorTesting ? 'Testing...' : 'Test'}
                  </button>
                </div>

                {/* Test result */}
                {consolidatorTestResult && (
                  <div style={{
                    marginTop: '0.5rem', padding: '0.5rem',
                    backgroundColor: (consolidatorTestResult as Record<string, unknown>).success ? '#f0fdf4' : '#fef2f2',
                    border: `1px solid ${(consolidatorTestResult as Record<string, unknown>).success ? '#bbf7d0' : '#fecaca'}`,
                    borderRadius: 6,
                  }}>
                    <pre style={{ fontSize: '0.7rem', margin: 0, overflow: 'auto', maxHeight: 200 }}>
                      {JSON.stringify(consolidatorTestResult, null, 2)}
                    </pre>
                  </div>
                )}

                {/* Step 4: Save */}
                <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.3rem' }}>
                  <button
                    onClick={() => {
                      // Add as a new tool to the form
                      const newTool: ConnectorSpecTool = {
                        ...EMPTY_TOOL,
                        action: String(consolidatorResult?.action || 'consolidator'),
                        method: 'GET',
                        description: String(consolidatorResult?.description || ''),
                        required_fields: (consolidatorResult?.required_fields || []) as string[],
                        field_descriptions: (consolidatorResult?.field_descriptions || {}) as Record<string, string>,
                        consolidator_config: (consolidatorResult?.consolidator_config || {}) as Record<string, unknown>,
                      };
                      setForm(prev => ({
                        ...prev,
                        tools: [...prev.tools, newTool],
                      }));
                      setShowConsolidatorBuilder(false);
                      setConsolidatorResult(null);
                      setConsolidatorTestResult(null);
                      setConsolidatorPrompt('');
                    }}
                    style={{
                      padding: '6px 18px', fontSize: '0.78rem', fontWeight: 600,
                      backgroundColor: '#111', color: '#fff', border: 'none', borderRadius: 6,
                      cursor: 'pointer', fontFamily: 'inherit',
                    }}
                  >
                    Add to Spec
                  </button>
                </div>
              </div>
            ) : null}

            {/* Error display */}
            {consolidatorResult?.error ? (
              <div style={{ color: '#dc2626', fontSize: '0.78rem', marginTop: '0.5rem' }}>
                {String(consolidatorResult.error)}
              </div>
            ) : null}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h3 style={{ margin: 0, fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          {isNew ? 'New Connector Spec' : `Edit: ${form.display_name}`}
        </h3>
        <button onClick={onCancel} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: '0.85rem', color: '#999' }}>
          &#8592; Back to list
        </button>
      </div>

      {/* Basic Info */}
      <div style={sectionStyle}>
        <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.82rem', fontWeight: 600, color: '#444' }}>Basic</h4>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
          <div>
            <label style={labelStyle}>Connector Name</label>
            <input
              type="text"
              value={form.connector_name}
              onChange={e => update('connector_name', e.target.value)}
              disabled={!isNew}
              placeholder="e.g. bamboohr"
              style={{ ...inputStyle, backgroundColor: isNew ? '#fff' : '#f7f7f7' }}
            />
          </div>
          <div>
            <label style={labelStyle}>Display Name</label>
            <input
              type="text"
              value={form.display_name}
              onChange={e => update('display_name', e.target.value)}
              placeholder="e.g. BambooHR"
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Category</label>
            <input
              type="text"
              value={form.category || ''}
              onChange={e => update('category', e.target.value || null)}
              placeholder="e.g. hr, procurement"
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Execution Mode</label>
            <select
              value={form.execution_mode}
              onChange={e => update('execution_mode', e.target.value as 'template' | 'agent' | 'internal')}
              style={inputStyle}
            >
              <option value="template">Template</option>
              <option value="agent">Agent</option>
              <option value="internal">Internal</option>
            </select>
          </div>
        </div>
      </div>

      {/* Auth */}
      <div style={sectionStyle}>
        <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.82rem', fontWeight: 600, color: '#444' }}>Authentication</h4>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
          <div>
            <label style={labelStyle}>Auth Type</label>
            <select
              value={form.auth_type}
              onChange={e => update('auth_type', e.target.value)}
              style={inputStyle}
            >
              <option value="none">None (Internal)</option>
              <option value="bearer">Bearer Token</option>
              <option value="api_key_header">API Key Header</option>
              <option value="basic">Basic Auth</option>
              <option value="oauth2">OAuth2</option>
            </select>
          </div>
          <div>
            <label style={labelStyle}>Base URL Mode</label>
            <select
              value={baseUrlMode}
              onChange={e => {
                const mode = e.target.value as 'fixed' | 'dynamic';
                if (mode === 'fixed' && baseUrlMode === 'dynamic') {
                  // Strip template vars for a clean starting point
                  const clean = (form.base_url_template || '').replace(/\{\{[^}]*\}\}/g, '').replace(/\/+$/, '');
                  update('base_url_template', clean || null);
                  removeDomainCredField(domainFieldKey);
                } else if (mode === 'dynamic' && baseUrlMode === 'fixed') {
                  update('base_url_template', '');
                  syncDomainCredField(domainFieldKey, domainFieldLabel);
                }
                setBaseUrlMode(mode);
              }}
              style={inputStyle}
            >
              <option value="fixed">Fixed URL</option>
              <option value="dynamic">User-provided domain</option>
            </select>
          </div>
        </div>
        {baseUrlMode === 'fixed' ? (
          <div style={{ marginTop: '0.75rem' }}>
            <label style={labelStyle}>Base URL</label>
            <input
              type="text"
              value={form.base_url_template || ''}
              onChange={e => update('base_url_template', e.target.value || null)}
              placeholder="https://api.example.com/v1"
              style={inputStyle}
            />
          </div>
        ) : (
          <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <div>
              <label style={labelStyle}>URL Pattern (use {'{{ domain }}'} as placeholder)</label>
              <input
                type="text"
                value={displayPattern(form.base_url_template || '')}
                onChange={e => {
                  const stored = storagePattern(e.target.value, domainFieldKey);
                  update('base_url_template', stored || null);
                }}
                placeholder="https://{{ domain }}.bamboohr.com/api"
                style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem' }}
              />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
              <div>
                <label style={labelStyle}>Field Key</label>
                <input
                  type="text"
                  value={domainFieldKey}
                  onChange={e => {
                    const newKey = e.target.value;
                    const oldKey = domainFieldKey;
                    setDomainFieldKey(newKey);
                    // Update the template to use the new key
                    if (form.base_url_template) {
                      update('base_url_template', form.base_url_template.replace(
                        new RegExp(`\\{\\{\\s*creds\\.${oldKey}\\s*\\}\\}`, 'g'),
                        `{{ creds.${newKey} }}`
                      ));
                    }
                    syncDomainCredField(newKey, domainFieldLabel, oldKey);
                  }}
                  placeholder="subdomain"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Field Label</label>
                <input
                  type="text"
                  value={domainFieldLabel}
                  onChange={e => {
                    setDomainFieldLabel(e.target.value);
                    syncDomainCredField(domainFieldKey, e.target.value);
                  }}
                  placeholder="BambooHR Subdomain (e.g. mycompany)"
                  style={inputStyle}
                />
              </div>
            </div>
          </div>
        )}
        <div style={{ marginTop: '0.75rem' }}>
          <label style={labelStyle}>Auth Config (JSON)</label>
          <JsonTextarea
            value={form.auth_config}
            onChange={v => update('auth_config', (v ?? {}) as Record<string, unknown>)}
            rows={3}
            style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem', resize: 'vertical' }}
          />
        </div>
      </div>

      {/* OAuth Config (shown when auth_type is oauth2) */}
      {form.auth_type === 'oauth2' && (
        <div style={sectionStyle}>
          <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.82rem', fontWeight: 600, color: '#444' }}>OAuth 2.0 Configuration</h4>
          <div style={{
            marginBottom: '0.75rem',
            padding: '8px 10px',
            backgroundColor: '#f0f4f8',
            border: '1px solid #d2dce6',
            borderRadius: 6,
            fontSize: '0.82rem',
            color: '#444',
            wordBreak: 'break-all',
          }}>
            <span style={{ fontWeight: 500, color: '#555' }}>Redirect URI: </span>
            {typeof window !== 'undefined' ? `${window.location.origin}/api/oauth/callback` : '(loading...)'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
            <div>
              <label style={labelStyle}>Authorize URL</label>
              <input
                type="text"
                value={form.oauth_config?.authorize_url || ''}
                onChange={e => update('oauth_config', { ...form.oauth_config, authorize_url: e.target.value } as ConnectorSpecFull['oauth_config'])}
                placeholder="https://provider.com/oauth/authorize"
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>Token URL</label>
              <input
                type="text"
                value={form.oauth_config?.token_url || ''}
                onChange={e => update('oauth_config', { ...form.oauth_config, token_url: e.target.value } as ConnectorSpecFull['oauth_config'])}
                placeholder="https://provider.com/oauth/token"
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>Client ID</label>
              <input
                type="text"
                value={form.oauth_config?.client_id || ''}
                onChange={e => update('oauth_config', { ...form.oauth_config, client_id: e.target.value } as ConnectorSpecFull['oauth_config'])}
                placeholder="your-client-id"
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>Client Secret</label>
              <input
                type="password"
                value={form.oauth_config?.client_secret || ''}
                onChange={e => update('oauth_config', { ...form.oauth_config, client_secret: e.target.value } as ConnectorSpecFull['oauth_config'])}
                placeholder="your-client-secret"
                style={inputStyle}
              />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={labelStyle}>Scopes</label>
              <input
                type="text"
                value={form.oauth_config?.scopes || ''}
                onChange={e => update('oauth_config', { ...form.oauth_config, scopes: e.target.value } as ConnectorSpecFull['oauth_config'])}
                placeholder="e.g. core:time:rw"
                style={inputStyle}
              />
            </div>
          </div>
        </div>
      )}

      {/* Credential Fields */}
      <div style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <h4 style={{ margin: 0, fontSize: '0.82rem', fontWeight: 600, color: '#444' }}>Credential Fields</h4>
          <button onClick={addCredentialField} style={{
            padding: '3px 10px', fontSize: '0.75rem', border: '1px solid #ddd', borderRadius: 4,
            backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit',
          }}>
            + Add
          </button>
        </div>
        {form.credential_fields.map((cf, idx) => (
          <div key={idx} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: '0.5rem' }}>
            <input
              type="text"
              value={cf.key}
              onChange={e => updateCredentialField(idx, 'key', e.target.value)}
              placeholder="key"
              style={{ ...inputStyle, flex: 1 }}
            />
            <input
              type="text"
              value={cf.label}
              onChange={e => updateCredentialField(idx, 'label', e.target.value)}
              placeholder="label"
              style={{ ...inputStyle, flex: 1 }}
            />
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.78rem', whiteSpace: 'nowrap' }}>
              <input
                type="checkbox"
                checked={cf.secret}
                onChange={e => updateCredentialField(idx, 'secret', e.target.checked)}
              />
              Secret
            </label>
            <button onClick={() => removeCredentialField(idx)} style={{
              border: 'none', background: 'none', cursor: 'pointer', color: '#e53e3e', fontSize: '0.9rem',
            }}>
              &#10005;
            </button>
          </div>
        ))}
      </div>

      {/* Connection Test */}
      <div style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <h4 style={{ margin: 0, fontSize: '0.82rem', fontWeight: 600, color: '#444' }}>Connection Test</h4>
          {!form.test_request ? (
            <button
              onClick={() => update('test_request', { method: 'GET', path_template: '', headers: {}, success_status_codes: [200], timeout_seconds: 15 })}
              style={{
                padding: '3px 10px', fontSize: '0.75rem', border: '1px solid #ddd', borderRadius: 4,
                backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              + Add Test
            </button>
          ) : (
            <button
              onClick={() => update('test_request', null)}
              style={{
                padding: '3px 10px', fontSize: '0.75rem', border: '1px solid #e53e3e', borderRadius: 4,
                backgroundColor: '#fff', color: '#e53e3e', cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              Remove
            </button>
          )}
        </div>
        {!form.test_request ? (
          <p style={{ color: '#999', fontSize: '0.78rem', margin: 0, fontStyle: 'italic' }}>
            No test configured. Add a lightweight API call (e.g. a GET to a health or list endpoint) to verify credentials from the Connectors tab.
          </p>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '100px 1fr', gap: '0.5rem', alignItems: 'start' }}>
            <div>
              <label style={labelStyle}>Method</label>
              <select
                value={form.test_request.method}
                onChange={e => update('test_request', { ...form.test_request, method: e.target.value } as TestRequest)}
                style={inputStyle}
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
                <option value="HEAD">HEAD</option>
              </select>
            </div>
            <div>
              <label style={labelStyle}>Path</label>
              <input
                type="text"
                value={form.test_request.path_template}
                onChange={e => update('test_request', { ...form.test_request, path_template: e.target.value } as TestRequest)}
                placeholder="/api/v1/ping"
                style={inputStyle}
              />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={labelStyle}>Success Status Codes (comma-separated)</label>
              <input
                type="text"
                value={(form.test_request.success_status_codes || [200]).join(', ')}
                onChange={e => update('test_request', {
                  ...form.test_request,
                  success_status_codes: e.target.value.split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n)),
                } as TestRequest)}
                placeholder="200"
                style={inputStyle}
              />
            </div>
          </div>
        )}
      </div>

      {/* Operations */}
      <div style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <h4 style={{ margin: 0, fontSize: '0.82rem', fontWeight: 600, color: '#444' }}>Tools</h4>
          <div style={{ display: 'flex', gap: '0.3rem' }}>
            {form.execution_mode === 'internal' && (
              <button onClick={() => setShowConsolidatorBuilder(true)} style={{
                padding: '3px 10px', fontSize: '0.75rem', border: '1px solid #6366f1', borderRadius: 4,
                backgroundColor: '#fff', color: '#6366f1', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 500,
              }}>
                + Consolidator
              </button>
            )}
            <button onClick={addTool} style={{
              padding: '3px 10px', fontSize: '0.75rem', border: '1px solid #ddd', borderRadius: 4,
              backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit',
            }}>
              + Add Tool
            </button>
          </div>
        </div>
        {form.tools.length > 0 && !isNew && (
          <div style={{
            border: '1px solid #d4e5f7',
            borderRadius: 8,
            padding: '0.75rem',
            marginBottom: '0.75rem',
            backgroundColor: '#f8fbff',
          }}>
            <h5 style={{ margin: '0 0 0.6rem', fontSize: '0.78rem', fontWeight: 600, color: '#444' }}>Try Tool</h5>
            <div style={{ marginBottom: '0.5rem' }}>
              <label style={labelStyle}>Tool</label>
              <select
                value={tryToolAction}
                onChange={e => {
                  const action = e.target.value;
                  setTryToolAction(action);
                  setTryDryRunResult(null);
                  setTryTestResult(null);
                  setTryError(null);
                  // Pre-populate field inputs for the selected tool
                  const tool = form.tools.find(t => t.action === action);
                  if (tool) {
                    const fields: Record<string, string> = {};
                    for (const key of Object.keys(tool.field_mapping)) {
                      // Extract example from format hint e.g. "(e.g., 2026-03-16T06:00:00+13:00)"
                      const hint = tool.field_descriptions?.[key] || '';
                      const exampleMatch = hint.match(/\(e\.g\.?,?\s*(.+?)\)\s*$/);
                      fields[key] = exampleMatch ? exampleMatch[1].trim() : '';
                    }
                    setTryToolFields(fields);
                  } else {
                    setTryToolFields({});
                  }
                }}
                style={inputStyle}
              >
                <option value="">Select a tool...</option>
                {form.tools.map((t, i) => (
                  <option key={`${t.action}-${i}`} value={t.action}>
                    {t.action} ({t.method} {t.path_template})
                  </option>
                ))}
              </select>
            </div>

            {selectedTool && (
              <>
                <div style={{ marginBottom: '0.5rem' }}>
                  <label style={{ ...labelStyle, marginBottom: 6 }}>Fields</label>
                  {Object.keys(selectedTool.field_mapping).length === 0 && (
                    <p style={{ color: '#999', fontSize: '0.78rem', margin: 0, fontStyle: 'italic' }}>
                      No fields defined for this tool.
                    </p>
                  )}
                  {Object.keys(selectedTool.field_mapping).map(fieldKey => (
                    <div key={fieldKey} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <label style={{
                        fontSize: '0.78rem',
                        fontWeight: 500,
                        color: '#555',
                        width: 130,
                        flexShrink: 0,
                      }}>
                        {fieldKey}
                        {selectedTool.required_fields.includes(fieldKey) && (
                          <span style={{ color: '#e53e3e', marginLeft: 2 }}>*</span>
                        )}
                      </label>
                      <input
                        type="text"
                        value={tryToolFields[fieldKey] ?? ''}
                        onChange={e => setTryToolFields(prev => ({ ...prev, [fieldKey]: e.target.value }))}
                        placeholder={selectedTool.field_descriptions?.[fieldKey] || selectedTool.field_mapping[fieldKey] || fieldKey}
                        style={{ ...inputStyle, flex: 1 }}
                      />
                    </div>
                  ))}
                </div>

                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <button
                    onClick={() => handleTryRun('render')}
                    disabled={tryLoading !== null}
                    style={{
                      padding: '5px 12px',
                      fontSize: '0.78rem',
                      fontWeight: 500,
                      border: 'none',
                      borderRadius: 6,
                      backgroundColor: '#c4a882',
                      color: '#fff',
                      cursor: tryLoading !== null ? 'not-allowed' : 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    {tryLoading === 'render' ? 'Rendering...' : 'Render'}
                  </button>
                  <button
                    onClick={() => handleTryRun('test')}
                    disabled={tryLoading !== null}
                    style={{
                      padding: '5px 12px',
                      fontSize: '0.78rem',
                      fontWeight: 500,
                      border: '1px solid #e53e3e',
                      borderRadius: 6,
                      backgroundColor: '#fff',
                      color: '#e53e3e',
                      cursor: tryLoading !== null ? 'not-allowed' : 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    {tryLoading === 'test' ? 'Testing...' : 'Test (Live)'}
                  </button>
                  <span style={{ fontSize: '0.72rem', color: '#999' }}>
                    Test makes a real HTTP call
                  </span>
                </div>

                {tryError && (
                  <p style={{ color: '#e53e3e', fontSize: '0.82rem', marginTop: '0.5rem', marginBottom: 0 }}>
                    {tryError}
                  </p>
                )}

                {tryDryRunResult && (
                  <div style={{ marginTop: '0.5rem' }}>
                    <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Rendered Request</div>
                    <pre style={{
                      padding: '0.75rem',
                      backgroundColor: '#1a202c',
                      color: '#e2e8f0',
                      borderRadius: 6,
                      fontSize: '0.78rem',
                      overflow: 'auto',
                      lineHeight: 1.5,
                      margin: 0,
                    }}>
                      {JSON.stringify(tryDryRunResult, null, 2)}
                    </pre>
                  </div>
                )}

                {tryTestResult && (() => {
                  const rendered = tryTestResult.rendered_request as Record<string, unknown> | undefined;
                  const payload = tryTestResult.response_payload;
                  const preStyle: React.CSSProperties = {
                    padding: '0.75rem', backgroundColor: '#1a202c', color: '#e2e8f0',
                    borderRadius: 6, fontSize: '0.72rem', overflow: 'auto', lineHeight: 1.5,
                    margin: 0, maxHeight: 300,
                  };
                  const labelStyle: React.CSSProperties = {
                    fontSize: '0.65rem', fontWeight: 600, color: '#666', marginBottom: 4,
                    textTransform: 'uppercase', letterSpacing: '0.03em',
                  };
                  return (
                    <div style={{ marginTop: '0.5rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: '0.5rem' }}>
                        <span style={{
                          fontSize: '0.72rem', fontWeight: 600, padding: '2px 8px', borderRadius: 10,
                          backgroundColor: tryTestResult.success ? '#d4edda' : '#f8d7da',
                          color: tryTestResult.success ? '#155724' : '#721c24',
                        }}>
                          {tryTestResult.success ? 'SUCCESS' : 'FAILED'}
                        </span>
                        {'error' in tryTestResult && Boolean(tryTestResult.error) && (
                          <span style={{ fontSize: '0.78rem', color: '#e53e3e' }}>{String(tryTestResult.error)}</span>
                        )}
                      </div>
                      {rendered && rendered.url ? (
                        <div style={{ marginBottom: '0.5rem' }}>
                          <div style={labelStyle}>Request</div>
                          <pre style={preStyle}>
                            {`${rendered.method} ${rendered.url}\n`}
                            {!!(rendered.headers && Object.keys(rendered.headers as Record<string, string>).length > 0) &&
                              `${Object.entries(rendered.headers as Record<string, string>).map(([k, v]) => `${k}: ${v}`).join('\n')}\n`}
                            {rendered.body ? `\n${typeof rendered.body === 'string' ? rendered.body : JSON.stringify(rendered.body, null, 2)}` : ''}
                          </pre>
                        </div>
                      ) : (
                        <div style={{ marginBottom: '0.5rem' }}>
                          <div style={labelStyle}>Request</div>
                          <div style={{ fontSize: '0.78rem', color: '#888', fontStyle: 'italic' }}>
                            No request was sent — check that required fields are filled in above.
                          </div>
                        </div>
                      )}
                      <div style={{ marginBottom: '0.5rem' }}>
                        <div style={labelStyle}>Response</div>
                        <pre style={preStyle}>
                          {payload ? JSON.stringify(payload, null, 2) : '(empty)'}
                        </pre>
                      </div>
                    </div>
                  );
                })()}
              </>
            )}
          </div>
        )}

        {form.tools.map((op, idx) => {
          const isCollapsed = collapsedTools.has(idx);
          const isDragOver = dragOverIdx === idx && dragIdx !== idx;
          return (
            <div
              key={idx}
              onDragOver={e => { e.preventDefault(); setDragOverIdx(idx); }}
              style={{
                border: isDragOver ? '1px solid #2563eb' : '1px solid #e2e8f0',
                borderRadius: 8,
                marginBottom: '0.5rem',
                backgroundColor: dragIdx === idx ? '#f0f4ff' : '#fff',
                overflow: 'hidden',
                opacity: dragIdx === idx ? 0.6 : 1,
                transition: 'border-color 0.1s, opacity 0.1s',
              }}
            >
              <div
                onClick={() => toggleTool(idx)}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '0.6rem 0.75rem',
                  cursor: 'pointer',
                  userSelect: 'none',
                  backgroundColor: isCollapsed ? '#fff' : '#fafafa',
                  borderBottom: isCollapsed ? 'none' : '1px solid #e2e8f0',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  {/* Drag handle */}
                  <span
                    draggable
                    onDragStart={() => setDragIdx(idx)}
                    onDragEnd={handleDragEnd}
                    style={{ cursor: 'grab', color: '#ccc', fontSize: '0.85rem', lineHeight: 1, padding: '0 4px', userSelect: 'none' }}
                    title="Drag to reorder"
                  >&#8942;&#8942;</span>
                  {isCollapsed
                    ? <ChevronRight size={14} strokeWidth={2} style={{ color: '#999' }} />
                    : <ChevronDown size={14} strokeWidth={2} style={{ color: '#999' }} />
                  }
                  <span style={{ fontWeight: 500, fontSize: '0.85rem', color: '#444' }}>
                    {op.action || `Tool ${idx + 1}`}
                  </span>
                  {op.consolidator_config ? (
                    <span style={{
                      fontSize: '0.65rem', fontWeight: 600, color: '#7c3aed',
                      backgroundColor: '#f5f3ff', padding: '1px 6px', borderRadius: 3, marginLeft: 4,
                    }}>Consolidator</span>
                  ) : (
                    <>
                      {op.method && (
                        <span style={{
                          fontSize: '0.7rem',
                          fontWeight: 600,
                          color: '#888',
                          backgroundColor: '#f0f0f0',
                          padding: '1px 6px',
                          borderRadius: 3,
                          marginLeft: 4,
                        }}>
                          {op.method}
                        </span>
                      )}
                      {op.path_template && (
                        <span style={{ fontSize: '0.75rem', color: '#aaa' }}>
                          {op.path_template}
                        </span>
                      )}
                      {op.response_transform?.enabled && (
                        <span style={{
                          fontSize: '0.65rem', fontWeight: 600, color: '#2563eb',
                          backgroundColor: '#eff6ff', padding: '1px 6px', borderRadius: 3, marginLeft: 4,
                        }}>Transform</span>
                      )}
                    </>
                  )}
                </div>
                <button onClick={(e) => { e.stopPropagation(); removeTool(idx); }} style={{
                  border: '1px solid #e53e3e', borderRadius: 4, backgroundColor: '#fff', color: '#e53e3e',
                  padding: '2px 8px', fontSize: '0.72rem', cursor: 'pointer', fontFamily: 'inherit',
                }}>
                  Remove
                </button>
              </div>
              {!isCollapsed && (
                <div style={{ padding: '0.75rem' }}>
                  {op.consolidator_config ? (
                    <ConsolidatorToolEditor
                      op={op}
                      idx={idx}
                      updateTool={updateTool}
                      setForm={setForm}
                      labelStyle={labelStyle}
                      inputStyle={inputStyle}
                    />
                  ) : (
                  <>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                    <div>
                      <label style={labelStyle}>Action</label>
                      <input
                        type="text"
                        value={op.action}
                        onChange={e => updateTool(idx, 'action', e.target.value)}
                        placeholder="create_employee"
                        style={inputStyle}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Method</label>
                      <select
                        value={op.method}
                        onChange={e => updateTool(idx, 'method', e.target.value)}
                        style={inputStyle}
                      >
                        <option value="GET">GET</option>
                        <option value="POST">POST</option>
                        <option value="PUT">PUT</option>
                        <option value="PATCH">PATCH</option>
                        <option value="DELETE">DELETE</option>
                      </select>
                    </div>
                    <div style={{ gridColumn: '1 / -1' }}>
                      <label style={labelStyle}>Description</label>
                      <input
                        type="text"
                        value={op.description || ''}
                        onChange={e => updateTool(idx, 'description', e.target.value || undefined)}
                        placeholder="e.g. Get a roster by date"
                        style={inputStyle}
                      />
                    </div>
                    <div style={{ gridColumn: '1 / -1' }}>
                      <label style={labelStyle}>Path Template</label>
                      <input
                        type="text"
                        value={op.path_template}
                        onChange={e => updateTool(idx, 'path_template', e.target.value)}
                        placeholder="/api/v1/employees"
                        style={inputStyle}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Success Status Codes (comma-separated)</label>
                      <input
                        type="text"
                        value={(op.success_status_codes || [200]).join(', ')}
                        onChange={e => updateTool(idx, 'success_status_codes', e.target.value.split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n)))}
                        placeholder="200, 201"
                        style={inputStyle}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Response Ref Path</label>
                      <input
                        type="text"
                        value={op.response_ref_path || ''}
                        onChange={e => updateTool(idx, 'response_ref_path', e.target.value || null)}
                        placeholder="body.id"
                        style={inputStyle}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Timeout (seconds)</label>
                      <input
                        type="number"
                        value={op.timeout_seconds || 30}
                        onChange={e => updateTool(idx, 'timeout_seconds', parseInt(e.target.value, 10) || 30)}
                        style={inputStyle}
                      />
                    </div>
                  </div>

                  {/* Fields */}
                  <div style={{ marginTop: '0.75rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                      <label style={{ ...labelStyle, marginBottom: 0 }}>Fields</label>
                      <button
                        onClick={() => {
                          let newKey = 'new_field';
                          let suffix = 1;
                          while (newKey in (op.field_mapping || {})) { newKey = `new_field_${suffix++}`; }
                          const updated = { ...(op.field_mapping || {}), [newKey]: '' };
                          updateTool(idx, 'field_mapping', updated);
                        }}
                        style={{
                          padding: '2px 8px', fontSize: '0.72rem', border: '1px solid #ddd', borderRadius: 4,
                          backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit',
                        }}
                      >
                        + Add Field
                      </button>
                    </div>
                    {Object.keys(op.field_mapping || {}).length === 0 && (
                      <p style={{ color: '#999', fontSize: '0.78rem', margin: '0 0 0.25rem', fontStyle: 'italic' }}>
                        No fields defined. Add fields to map source data to API parameters.
                      </p>
                    )}
                    {Object.keys(op.field_mapping || {}).length > 0 && (
                      <div style={{
                        border: '1px solid #e2e8f0',
                        borderRadius: 6,
                        overflow: 'hidden',
                      }}>
                        <div style={{
                          display: 'grid',
                          gridTemplateColumns: '1fr 1fr 1.5fr 70px 32px',
                          gap: 0,
                          backgroundColor: '#f7f7f7',
                          padding: '6px 10px',
                          borderBottom: '1px solid #e2e8f0',
                        }}>
                          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Field Name</span>
                          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.03em' }}>API Mapping</span>
                          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Format Hint</span>
                          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.03em', textAlign: 'center' }}>Required</span>
                          <span />
                        </div>
                        {Object.entries(op.field_mapping || {}).map(([fieldKey, apiMapping], fieldIdx) => (
                          <div
                            key={fieldIdx}
                            style={{
                              display: 'grid',
                              gridTemplateColumns: '1fr 1fr 1.5fr 70px 32px',
                              gap: 0,
                              alignItems: 'center',
                              padding: '4px 10px',
                              borderBottom: fieldIdx < Object.keys(op.field_mapping || {}).length - 1 ? '1px solid #f0f0f0' : 'none',
                            }}
                          >
                            <input
                              type="text"
                              value={fieldKey}
                              onChange={e => {
                                const newKey = e.target.value;
                                const entries = Object.entries(op.field_mapping || {});
                                entries[fieldIdx] = [newKey, apiMapping];
                                const newMapping = Object.fromEntries(entries);
                                const newRequired = op.required_fields.map(f => f === fieldKey ? newKey : f);
                                const newDescs = { ...(op.field_descriptions || {}) };
                                if (fieldKey in newDescs) {
                                  newDescs[newKey] = newDescs[fieldKey];
                                  delete newDescs[fieldKey];
                                }
                                setForm(prev => ({
                                  ...prev,
                                  tools: prev.tools.map((o, i) =>
                                    i === idx ? { ...o, field_mapping: newMapping, required_fields: newRequired, field_descriptions: newDescs } : o
                                  ),
                                }));
                              }}
                              placeholder="field_name"
                              style={{ ...inputStyle, border: 'none', padding: '4px 6px', fontSize: '0.82rem', backgroundColor: 'transparent' }}
                            />
                            <input
                              type="text"
                              value={apiMapping}
                              onChange={e => {
                                const entries = Object.entries(op.field_mapping || {});
                                entries[fieldIdx] = [fieldKey, e.target.value];
                                updateTool(idx, 'field_mapping', Object.fromEntries(entries));
                              }}
                              placeholder="apiFieldName"
                              style={{ ...inputStyle, border: 'none', padding: '4px 6px', fontSize: '0.82rem', fontFamily: 'monospace', backgroundColor: 'transparent' }}
                            />
                            <input
                              type="text"
                              value={(op.field_descriptions || {})[fieldKey] || ''}
                              onChange={e => {
                                const newDescs = { ...(op.field_descriptions || {}), [fieldKey]: e.target.value };
                                updateTool(idx, 'field_descriptions', newDescs);
                              }}
                              placeholder="e.g. Date in YYYY-MM-DD"
                              style={{ ...inputStyle, border: 'none', padding: '4px 6px', fontSize: '0.82rem', color: '#888', backgroundColor: 'transparent' }}
                            />
                            <div style={{ textAlign: 'center' }}>
                              <input
                                type="checkbox"
                                checked={op.required_fields.includes(fieldKey)}
                                onChange={e => {
                                  const newRequired = e.target.checked
                                    ? [...op.required_fields, fieldKey]
                                    : op.required_fields.filter(f => f !== fieldKey);
                                  updateTool(idx, 'required_fields', newRequired);
                                }}
                                style={{ cursor: 'pointer' }}
                              />
                            </div>
                            <button
                              onClick={() => {
                                const newMapping = { ...(op.field_mapping || {}) };
                                delete newMapping[fieldKey];
                                const newDescs = { ...(op.field_descriptions || {}) };
                                delete newDescs[fieldKey];
                                const newRequired = op.required_fields.filter(f => f !== fieldKey);
                                setForm(prev => ({
                                  ...prev,
                                  tools: prev.tools.map((o, i) =>
                                    i === idx ? { ...o, field_mapping: newMapping, required_fields: newRequired, field_descriptions: newDescs } : o
                                  ),
                                }));
                              }}
                              style={{
                                border: 'none', background: 'none', cursor: 'pointer', color: '#e53e3e',
                                fontSize: '0.85rem', padding: 0, lineHeight: 1,
                              }}
                            >
                              &#10005;
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div style={{ marginTop: '0.5rem' }}>
                    <label style={labelStyle}>Headers (JSON)</label>
                    <JsonTextarea
                      value={op.headers}
                      onChange={v => updateTool(idx, 'headers', v ?? {})}
                      autoResize
                      style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem' }}
                    />
                  </div>
                  <div style={{ marginTop: '0.5rem' }}>
                    <label style={labelStyle}>Field Schema (JSON)</label>
                    <div style={{ fontSize: '0.7rem', color: '#888', marginBottom: 4 }}>
                      Define JSON Schema for complex fields (e.g., arrays with required properties). Overrides the default string type.
                    </div>
                    <JsonTextarea
                      value={op.field_schema}
                      onChange={v => updateTool(idx, 'field_schema', v)}
                      autoResize
                      placeholder='{"lines": {"type": "array", "items": {"type": "object", "properties": {...}, "required": [...]}}}'
                      style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem' }}
                    />
                  </div>
                  <div style={{ marginTop: '0.5rem' }}>
                    <label style={labelStyle}>Request Body Template (JSON)</label>
                    <AutoResizeTextarea
                      value={op.request_body_template || ''}
                      onChange={e => updateTool(idx, 'request_body_template', e.target.value || null)}
                      placeholder=""
                      style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem' }}
                    />
                  </div>
                  <div style={{ marginTop: '0.5rem' }}>
                    <label style={labelStyle}>Display Component</label>
                    <input
                      value={op.display_component || ''}
                      onChange={e => updateTool(idx, 'display_component', e.target.value || null)}
                      placeholder="e.g. generic_table"
                      style={inputStyle}
                    />
                  </div>
                  <div style={{ marginTop: '0.5rem' }}>
                    <label style={labelStyle}>Display Props (JSON)</label>
                    <JsonTextarea
                      value={op.display_props}
                      onChange={v => updateTool(idx, 'display_props', v)}
                      rows={2}
                      placeholder='{"title": "Results"}'
                      style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem', resize: 'vertical' }}
                    />
                  </div>
                  <div style={{ marginTop: '0.5rem' }}>
                    <label style={labelStyle}>Working Document (JSON)</label>
                    <JsonTextarea
                      value={op.working_document}
                      onChange={v => updateTool(idx, 'working_document', v)}
                      rows={2}
                      placeholder='{"doc_type": "roster", "sync_mode": "auto", "ref_fields": ["search_date"]}'
                      style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem', resize: 'vertical' }}
                    />
                  </div>
                  <div style={{ marginTop: '0.5rem' }}>
                    <label style={labelStyle}>Summary Fields</label>
                    <input
                      value={(op.summary_fields || []).join(', ')}
                      onChange={e => {
                        const val = e.target.value.trim();
                        updateTool(idx, 'summary_fields', val ? val.split(',').map((s: string) => s.trim()).filter(Boolean) : null);
                      }}
                      placeholder="name, id, sku, price"
                      style={inputStyle}
                    />
                    <div style={{ fontSize: '0.7rem', color: '#999', marginTop: 2 }}>
                      Comma-separated field names to show when result is too large. Leave empty to use search-only mode.
                    </div>
                  </div>

                  {/* Response Transform */}
                  <ResponseTransformSection
                    op={op}
                    idx={idx}
                    updateTool={updateTool}
                    connectorName={form.connector_name}
                    isNew={isNew}
                  />
                  </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Agent Mode (conditional) */}
      {form.execution_mode === 'agent' && (
        <div style={sectionStyle}>
          <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.82rem', fontWeight: 600, color: '#444' }}>Agent Mode</h4>
          <div style={{ marginBottom: '0.75rem' }}>
            <label style={labelStyle}>API Documentation</label>
            <textarea
              value={form.api_documentation || ''}
              onChange={e => update('api_documentation', e.target.value || null)}
              rows={8}
              placeholder="Paste API docs for the LLM to reference..."
              style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem', resize: 'vertical', lineHeight: 1.5 }}
            />
          </div>
          <div>
            <label style={labelStyle}>Example Requests (JSON array)</label>
            <JsonTextarea
              value={form.example_requests}
              onChange={v => update('example_requests', (v ?? []) as Record<string, unknown>[])}
              rows={4}
              style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem', resize: 'vertical' }}
            />
          </div>
        </div>
      )}

      {/* Save / Cancel */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={handleSubmit}
          disabled={saving || !form.connector_name || !form.display_name}
          style={{
            padding: '8px 20px',
            fontSize: '0.85rem',
            fontWeight: 500,
            border: 'none',
            borderRadius: 6,
            backgroundColor: '#c4a882',
            color: '#fff',
            cursor: saving ? 'not-allowed' : 'pointer',
            fontFamily: 'inherit',
          }}
        >
          {saving ? 'Saving...' : isNew ? 'Create Spec' : 'Update Spec'}
        </button>
        <button
          onClick={onCancel}
          style={{
            padding: '8px 20px',
            fontSize: '0.85rem',
            fontWeight: 500,
            border: '1px solid #ddd',
            borderRadius: 6,
            backgroundColor: '#fff',
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
