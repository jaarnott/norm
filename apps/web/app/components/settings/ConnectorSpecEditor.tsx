'use client';

import { useState, useMemo, useEffect, useRef } from 'react';
import { ChevronRight, ChevronDown, Check, X, Plus, GripVertical, CornerDownRight } from 'lucide-react';
import type { ConnectorSpecFull, ConnectorSpecTool, SpecInventoryRow, TestRequest } from '../../types';
import { apiFetch, getToken } from '../../lib/api';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import FunctionEditor from './FunctionEditor';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import Icon from '../ui/Icon';
import Badge from '../ui/Badge';
import BackLink from '../ui/BackLink';

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
  endpoints: [],
  api_documentation: null,
  example_requests: [],
  credential_fields: [],
  oauth_config: null,
  test_request: null,
  created_at: '',
  updated_at: null,
};

// Tools and API endpoints are two lists in the database (Sep 2026 — see
// docs/tool-architecture-strategy.md). The editor keeps ONE array so row
// indexes, drag-to-reorder and collapse state work unchanged; each row carries
// the list it belongs to (tools first, then endpoints), stripped again on save.
function withListMarkers(spec: ConnectorSpecFull): ConnectorSpecFull {
  if (!Array.isArray(spec.endpoints)) return spec; // not split yet: one list
  return {
    ...spec,
    tools: [
      ...spec.tools.map(t => ({ ...t, _list: 'tools' as const })),
      ...spec.endpoints.map(t => ({ ...t, _list: 'endpoints' as const })),
    ],
  };
}

function toStoredSpec(form: ConnectorSpecFull, split: boolean): ConnectorSpecFull {
  const strip = (row: ConnectorSpecTool): ConnectorSpecTool => {
    const { _list: _ignored, ...rest } = row;
    void _ignored;
    return rest;
  };
  if (!split) return { ...form, tools: form.tools.map(strip) };
  return {
    ...form,
    tools: form.tools.filter(t => t._list !== 'endpoints').map(strip),
    endpoints: form.tools.filter(t => t._list === 'endpoints').map(strip),
  };
}

const rowMeta: React.CSSProperties = { fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' };

// Text fields take .n-input, selects .n-select and labels .n-label (tokens.css);
// these only stretch a field to its column, or set code in monospace.
const fieldStyle: React.CSSProperties = { width: '100%' };
// Two equal columns on a wide card; one column once a column would drop under
// 220px (phones). Each track is at least half the row, so never more than two.
const twoColGrid: React.CSSProperties = {
  display: 'grid', gap: '0.75rem',
  gridTemplateColumns: 'repeat(auto-fit, minmax(max(220px, calc(50% - 0.375rem)), 1fr))',
};
const codeFieldStyle: React.CSSProperties = { width: '100%', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' };
// Inputs inside the field table: borderless — the table's rules are their edges.
const cellInputStyle: React.CSSProperties = {
  width: '100%', border: 'none', padding: '4px 6px', borderRadius: 'var(--radius-sm)',
  fontFamily: 'inherit', fontSize: 'var(--fs-base)', color: 'var(--text)', backgroundColor: 'transparent', boxSizing: 'border-box',
};

// Each group of settings is a white card on the cream page, titled like the
// cards in the design foundations: 18px/600 title, 13px muted note under it.
const sectionStyle: React.CSSProperties = { marginBottom: 16, padding: '20px 24px' };
const sectionTitle: React.CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
const sectionNote: React.CSSProperties = { margin: '2px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
// "Nothing here yet" lines inside a card.
const emptyNote: React.CSSProperties = { margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
// Small label over a group (Request, Response, a field list's column heads).
const groupLabel: React.CSSProperties = { fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)', marginBottom: 4 };
// JSON, requests and logs: the warm dark code block.
const codeBlock: React.CSSProperties = {
  margin: 0, padding: '10px 12px', borderRadius: 'var(--radius)',
  background: 'var(--code-bg)', color: 'var(--code-text)',
  fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', lineHeight: 1.5,
  overflow: 'auto',
};
const wrapCode: React.CSSProperties = { whiteSpace: 'pre-wrap', wordBreak: 'break-all' };
// A result on its status tint (passed / failed).
const resultBox = (ok: boolean): React.CSSProperties => ({
  padding: '8px 10px', borderRadius: 'var(--radius)', userSelect: 'text',
  background: ok ? 'var(--ok-bg)' : 'var(--error-bg)', color: 'var(--text)',
});
// Disclosure summary inside a result ("Raw JSON", "Step result").
const detailsSummary: React.CSSProperties = { fontSize: 'var(--fs-xs)', color: 'var(--muted)', cursor: 'pointer' };

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
      aria-invalid={valid ? undefined : true}
      className="n-input"
      style={{
        ...style,
        // Invalid JSON: a red edge. Not an outline — the focus ring
        // (globals.css, !important) would hide it while you type.
        ...(valid ? {} : { borderColor: 'var(--error)', boxShadow: '0 0 0 1px var(--error)' }),
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
      className="n-input"
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

/** Parse "output_name|round:2" into [name, options]. */
function parseFieldDest(dest: string): [string, Record<string, string>] {
  const pipeIdx = dest.indexOf('|');
  if (pipeIdx < 0) return [dest, {}];
  const name = dest.slice(0, pipeIdx);
  const opts: Record<string, string> = {};
  for (const part of dest.slice(pipeIdx + 1).split('|')) {
    const ci = part.indexOf(':');
    if (ci >= 0) opts[part.slice(0, ci).trim()] = part.slice(ci + 1).trim();
  }
  return [name, opts];
}

function applyFieldOptions(value: unknown, opts: Record<string, string>): unknown {
  if ('round' in opts && typeof value === 'number') {
    const dp = parseInt(opts.round, 10);
    return dp > 0 ? parseFloat(value.toFixed(dp)) : Math.round(value);
  }
  return value;
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
  for (const [src, destRaw] of Object.entries(regular)) {
    const [dest, opts] = parseFieldDest(destRaw);
    const val = resolveDotPath(item, src);
    if (val !== undefined) base[dest] = applyFieldOptions(val, opts);
  }

  // Array fields
  for (const [arrName, subFields] of Object.entries(arrayFields)) {
    const arrData = item[arrName];
    if (!Array.isArray(arrData)) continue;

    // Separate direct sub-fields from nested array sub-fields
    const direct: Record<string, string> = {};
    const nested: Record<string, Record<string, string>> = {};
    for (const [subSrc, subDest] of Object.entries(subFields)) {
      if (subSrc.includes('[].')) {
        const [innerArr, innerSub] = subSrc.split('[].', 2);
        if (!nested[innerArr]) nested[innerArr] = {};
        nested[innerArr][innerSub] = subDest;
      } else {
        direct[subSrc] = subDest;
      }
    }
    // Remove direct entries that have nested sub-field mappings (nested takes priority)
    for (const innerArr of Object.keys(nested)) {
      delete direct[innerArr];
    }

    const transformed = arrData
      .filter(el => el && typeof el === 'object')
      .map(el => {
        const row: Record<string, unknown> = {};
        const elObj = el as Record<string, unknown>;
        // Direct sub-fields
        for (const [subSrc, subDestRaw] of Object.entries(direct)) {
          const [subDest, subOpts] = parseFieldDest(subDestRaw);
          const val = resolveDotPath(elObj, subSrc);
          if (val !== undefined) row[subDest] = applyFieldOptions(val, subOpts);
        }
        // Nested array sub-fields (e.g., breaks[].breakStart)
        for (const [innerArr, innerFields] of Object.entries(nested)) {
          const innerData = elObj[innerArr];
          if (!Array.isArray(innerData)) continue;
          const innerTransformed = innerData
            .filter(ie => ie && typeof ie === 'object')
            .map(ie => {
              const innerRow: Record<string, unknown> = {};
              for (const [iSrc, iDestRaw] of Object.entries(innerFields)) {
                const [iDest, iOpts] = parseFieldDest(iDestRaw);
                const val = resolveDotPath(ie as Record<string, unknown>, iSrc);
                if (val !== undefined) innerRow[iDest] = applyFieldOptions(val, iOpts);
              }
              return innerRow;
            })
            .filter(r => Object.keys(r).length > 0);
          if (innerTransformed.length > 0) row[innerArr] = innerTransformed;
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
function extractLeafPaths(obj: Record<string, unknown>, prefix = '', siblingItems?: Record<string, unknown>[]): string[] {
  const paths: string[] = [];
  for (const [key, val] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (Array.isArray(val)) {
      // Collect all elements of this array across siblings to find one with data
      const allElements: Record<string, unknown>[] = [];
      // From current item
      for (const el of val) {
        if (el && typeof el === 'object' && !Array.isArray(el)) allElements.push(el as Record<string, unknown>);
      }
      // From sibling items (other roster entries / other shifts)
      if (allElements.length === 0 && siblingItems) {
        for (const sib of siblingItems) {
          const sibVal = sib[key];
          if (Array.isArray(sibVal)) {
            for (const el of sibVal) {
              if (el && typeof el === 'object' && !Array.isArray(el)) allElements.push(el as Record<string, unknown>);
            }
            if (allElements.length > 0) break;
          }
        }
      }

      if (allElements.length > 0) {
        // Recurse into the first object element, passing all elements as siblings
        paths.push(...extractLeafPaths(allElements[0], `${path}[]`, allElements));
      } else if (val.length === 0) {
        paths.push(path); // empty array, no siblings have data
      } else {
        paths.push(path); // array of primitives
      }
    } else if (val && typeof val === 'object') {
      paths.push(...extractLeafPaths(val as Record<string, unknown>, path, siblingItems));
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

  // Group array entries by top-level parent (first []. segment only)
  const arrayGroups: Record<string, [string, string][]> = {};
  for (const entry of arrayEntries) {
    const firstBracket = entry[0].indexOf('[].') ;
    const arrName = entry[0].slice(0, firstBracket);
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
    // Parse pipe-separated options: "output_name|round:2|tz|dow" → name + options
    const pipeIdx = dest.indexOf('|');
    const destName = pipeIdx >= 0 ? dest.slice(0, pipeIdx) : dest;
    const optionStr = pipeIdx >= 0 ? dest.slice(pipeIdx + 1) : '';
    const roundMatch = optionStr.match(/round:(\d+)/);
    const roundVal = roundMatch ? roundMatch[1] : '';
    const hasTz = /\btz\b/.test(optionStr);
    const hasDow = /\bdow\b/.test(optionStr);
    const dtVal = hasTz && hasDow ? 'tz+dow' : hasTz ? 'tz' : hasDow ? 'dow' : '';

    const included = dest !== '';
    const leaf = src.includes('[].') ? src.split('[].').pop() || src : (src.split('.').pop() || src);

    const rebuildDest = (name: string, round: string, dt: string) => {
      if (!name) return '';
      const parts = [name];
      if (round) parts.push(`round:${round}`);
      if (dt === 'tz' || dt === 'tz+dow') parts.push('tz');
      if (dt === 'dow' || dt === 'tz+dow') parts.push('dow');
      return parts.join('|');
    };

    return (
      <div key={src} style={{
        display: 'grid', gridTemplateColumns: 'auto 1fr 1fr auto auto', gap: 6, marginBottom: 2,
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
          {indent > 0 && <Icon icon={CornerDownRight} size="meta" tone="muted" style={{ marginRight: 4 }} />}
          <span style={{ fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-mono)', color: 'var(--text-soft)' }}>
            {src.includes('[].') ? src.split('[].').pop() : src}
          </span>
        </div>
        <input
          value={destName}
          onChange={e => updateField(src, src, rebuildDest(e.target.value, roundVal, dtVal))}
          placeholder="(excluded)"
          disabled={!included}
          className="n-input"
          style={{ ...fieldStyle, height: 30, fontSize: 'var(--fs-sm)', opacity: included ? 1 : 0.5 }}
        />
        <select
          value={roundVal}
          onChange={e => updateField(src, src, rebuildDest(destName || leaf, e.target.value, dtVal))}
          disabled={!included}
          style={{
            width: 48, fontSize: 'var(--fs-xs)', padding: '2px 2px', border: '1px solid var(--line-strong)',
            borderRadius: 'var(--radius-sm)', backgroundColor: 'var(--bg)', color: roundVal ? 'var(--text)' : 'var(--muted)',
            fontFamily: 'inherit', opacity: included ? 1 : 0.4, cursor: included ? 'pointer' : 'default',
          }}
        >
          <option value="">dp</option>
          <option value="0">0dp</option>
          <option value="1">1dp</option>
          <option value="2">2dp</option>
          <option value="3">3dp</option>
          <option value="4">4dp</option>
        </select>
        <select
          value={dtVal}
          onChange={e => updateField(src, src, rebuildDest(destName || leaf, roundVal, e.target.value))}
          disabled={!included}
          style={{
            width: 62, fontSize: 'var(--fs-xs)', padding: '2px 2px', border: '1px solid var(--line-strong)',
            borderRadius: 'var(--radius-sm)', backgroundColor: 'var(--bg)', color: dtVal ? 'var(--text)' : 'var(--muted)',
            fontFamily: 'inherit', opacity: included ? 1 : 0.4, cursor: included ? 'pointer' : 'default',
          }}
        >
          <option value="">date</option>
          <option value="tz">tz</option>
          <option value="dow">dow</option>
          <option value="tz+dow">tz+dow</option>
        </select>
      </div>
    );
  };

  return (
    <div style={{ marginBottom: '0.5rem' }}>
      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 4 }}>
        Fetch a sample response to generate field mappings. Toggle fields to include/exclude, and edit output names.
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr 1fr auto', gap: '0 6px', marginBottom: 4 }}>
        <div style={{ width: 20 }} />
        <div style={{ ...groupLabel, marginBottom: 0, padding: '0 2px' }}>Source field</div>
        <div style={{ ...groupLabel, marginBottom: 0, padding: '0 2px' }}>Output name</div>
        <div style={{ ...groupLabel, marginBottom: 0, width: 48, textAlign: 'center' }}>Round</div>
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
                <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--muted)', padding: '4px 0 2px 0', marginTop: 4, borderTop: '1px solid var(--line-soft)', paddingLeft: 26 }}>
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
          <div key={arrName} style={{ marginTop: 4, borderTop: '1px solid var(--line-soft)', paddingTop: 4 }}>
            {/* Array parent header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
              <button
                type="button"
                onClick={() => toggleCollapse(arrName)}
                aria-label={isCollapsed ? `Show ${arrName} fields` : `Hide ${arrName} fields`}
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', border: 'none', background: 'none', cursor: 'pointer', padding: 0, color: 'var(--icon)', width: 20 }}
              ><Icon icon={isCollapsed ? ChevronRight : ChevronDown} size="dense" /></button>
              <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, fontFamily: 'var(--font-mono)', color: 'var(--text-soft)' }}>
                {arrName}[]
              </span>
              <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{entries.length} fields</span>
              <button
                type="button"
                onClick={() => toggleFlatten(arrName)}
                title={isFlattened ? 'Flattened into parent rows — click to keep nested' : 'Click to flatten into parent rows'}
                aria-pressed={isFlattened}
                style={{
                  border: '1px solid ' + (isFlattened ? 'var(--brand-soft)' : 'var(--line-strong)'),
                  borderRadius: 999,
                  backgroundColor: isFlattened ? 'var(--accent-soft)' : 'var(--bg)',
                  color: isFlattened ? 'var(--accent)' : 'var(--text-soft)',
                  padding: '1px 8px',
                  fontSize: 'var(--fs-2xs)',
                  fontWeight: 600,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >{isFlattened ? 'Flattened' : 'Flatten'}</button>
            </div>
            {/* Array sub-fields — split into direct and nested sub-arrays */}
            {!isCollapsed && (() => {
              // Separate direct fields from nested array fields within this group
              const prefix = `${arrName}[].`;
              const directEntries: [string, string][] = [];
              const subArrayGroups: Record<string, [string, string][]> = {};

              for (const [src, dest] of entries) {
                const rest = src.slice(prefix.length);
                if (rest.includes('[].')) {
                  // Nested array: e.g., "breaks[].breakStart"
                  const subArrName = rest.split('[].')[0];
                  if (!subArrayGroups[subArrName]) subArrayGroups[subArrName] = [];
                  subArrayGroups[subArrName].push([src, dest]);
                } else {
                  directEntries.push([src, dest]);
                }
              }

              return (
                <>
                  {directEntries.map(([src, dest]) => renderFieldRow(src, dest, 24))}
                  {Object.entries(subArrayGroups).map(([subArrName, subEntries]) => {
                    const subKey = `${arrName}.${subArrName}`;
                    const subCollapsed = collapsed.has(subKey);
                    return (
                      <div key={subKey} style={{ marginTop: 2, marginLeft: 24 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                          <button
                            type="button"
                            onClick={() => toggleCollapse(subKey)}
                            aria-label={subCollapsed ? `Show ${subArrName} fields` : `Hide ${subArrName} fields`}
                            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', border: 'none', background: 'none', cursor: 'pointer', padding: 0, color: 'var(--icon)', width: 16 }}
                          ><Icon icon={subCollapsed ? ChevronRight : ChevronDown} size="meta" /></button>
                          <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, fontFamily: 'var(--font-mono)', color: 'var(--muted)' }}>
                            {subArrName}[]
                          </span>
                          <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--muted)' }}>{subEntries.length} fields</span>
                        </div>
                        {!subCollapsed && subEntries.map(([src, dest]) => renderFieldRow(src, dest, 40))}
                      </div>
                    );
                  })}
                </>
              );
            })()}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  toolEvents?: { type: string; name?: string; input?: Record<string, unknown>; result?: Record<string, unknown>; config?: Record<string, unknown> }[];
}

function ConsolidatorToolEditor({
  op, idx, updateTool, setForm,
}: {
  op: ConnectorSpecTool;
  idx: number;
  updateTool: (index: number, field: keyof ConnectorSpecTool, value: unknown) => void;
  setForm: React.Dispatch<React.SetStateAction<ConnectorSpecFull>>;
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
        <div key={i} style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', padding: '4px 8px', backgroundColor: 'var(--surface-alt)', borderRadius: 'var(--radius-sm)', marginBottom: 4, userSelect: 'text' }}>
          Calling <strong style={{ fontWeight: 600, color: 'var(--text)' }}>{displayName}</strong>
          {event.input && Object.keys(event.input).length > 0 && (
            <span style={{ color: 'var(--muted)', marginLeft: 4 }}>
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
        <div key={i} style={{ ...resultBox(isSuccess), padding: '4px 8px', marginBottom: 4, fontSize: 'var(--fs-sm)', borderRadius: 'var(--radius-sm)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
            <Icon icon={isSuccess ? Check : X} size="dense" style={{ color: isSuccess ? 'var(--ok)' : 'var(--error)' }} />
            <span style={{ fontWeight: 500, color: isSuccess ? 'var(--ok)' : 'var(--error)' }}>{isSuccess ? 'Success' : 'Failed'}</span>
            {summary ? <span style={{ color: 'var(--muted)', marginLeft: 4 }}>({summary})</span> : null}
            {r.error ? <span style={{ color: 'var(--error)' }}>— {String(r.error)}</span> : null}
          </div>
          {hasData && (
            <details>
              <summary style={detailsSummary}>
                {preview ? `Preview (${(preview as unknown[]).length} of ${summary || '?'})` : 'Response data'}
              </summary>
              <pre style={{ ...codeBlock, ...wrapCode, margin: '4px 0 0', maxHeight: 150 }}>
                {JSON.stringify(preview || r.data, null, 2)}
              </pre>
            </details>
          )}
        </div>
      );
    }
    if (event.type === 'save') {
      return (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-sm)', color: 'var(--ok)', padding: '4px 8px', backgroundColor: 'var(--ok-bg)', borderRadius: 'var(--radius-sm)', marginBottom: 4 }}>
          <Icon icon={Check} size="dense" />
          Config saved to tool
        </div>
      );
    }
    return null;
  };

  return (
    <div>
      {/* Action + Description: 1:2 on a wide card, stacked on a phone. */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '0.75rem' }}>
        <div style={{ flex: '1 1 180px', minWidth: 0 }}>
          <label className="n-label">Action</label>
          <input
            type="text"
            value={op.action}
            onChange={e => updateTool(idx, 'action', e.target.value)}
            placeholder="get_stock_comparison"
            className="n-input"
            style={fieldStyle}
          />
        </div>
        <div style={{ flex: '2 1 360px', minWidth: 0 }}>
          <label className="n-label">Description</label>
          <input
            type="text"
            value={op.description || ''}
            onChange={e => updateTool(idx, 'description', e.target.value || undefined)}
            placeholder="What this consolidator does"
            className="n-input"
            style={fieldStyle}
          />
        </div>
      </div>

      {/* Chat area */}
      <div style={{
        border: '1px solid var(--line)', borderRadius: 'var(--radius)',
        height: chatHeight, minHeight: 120, overflowY: 'auto', padding: '10px 12px',
        backgroundColor: 'var(--surface)', userSelect: 'text', resize: 'vertical',
      }}>
        {chatMessages.length === 0 && !streamingText && (
          <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)', textAlign: 'center', padding: '2rem 1rem' }}>
            Describe what this consolidator should do, or ask to test/fix the current config.
          </div>
        )}
        {chatMessages.map((msg, i) => (
          <div key={i} style={{ marginBottom: '0.75rem' }}>
            <div className="n-eyebrow" style={{ marginBottom: 2 }}>
              {msg.role === 'user' ? 'You' : 'AI'}
            </div>
            {msg.toolEvents?.map((ev, j) => renderToolEvent(ev, j) as React.ReactNode)}
            {msg.role === 'assistant' ? (
              <div className="markdown-message" style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', lineHeight: 1.5 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>{String(msg.content)}</ReactMarkdown>
              </div>
            ) : (
              <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                {String(msg.content)}
              </div>
            )}
          </div>
        ))}
        {/* Streaming state */}
        {chatLoading && (
          <div style={{ marginBottom: '0.75rem' }}>
            <div className="n-eyebrow" style={{ marginBottom: 2 }}>AI</div>
            {streamingEvents?.map((ev, j) => renderToolEvent(ev, j) as React.ReactNode)}
            {streamingText ? (
              <div className="markdown-message" style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', lineHeight: 1.5 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>{String(streamingText)}</ReactMarkdown>
              </div>
            ) : (
              <div style={{ fontSize: 'var(--fs-base)', color: 'var(--muted)', lineHeight: 1.5 }}>Thinking...</div>
            )}
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {/* Input */}
      <div style={{ display: 'flex', gap: 8, marginTop: 8, marginBottom: '0.75rem' }}>
        <input
          type="text"
          value={chatInput}
          onChange={e => setChatInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !chatLoading) handleSend(); }}
          placeholder="e.g. fetch stocktake templates, filter by category, then get stock on hand..."
          disabled={chatLoading}
          className="n-input"
          style={{ flex: 1, minWidth: 0 }}
        />
        <Button
          variant="primary"
          onClick={handleSend}
          disabled={chatLoading || !chatInput.trim()}
        >
          {chatLoading ? '...' : 'Send'}
        </Button>
      </div>

      {/* Manual test */}
      <div style={{ borderTop: '1px solid var(--line)', paddingTop: '0.75rem', marginBottom: '0.75rem' }}>
        <button
          type="button"
          onClick={() => setShowTest(!showTest)}
          aria-expanded={showTest}
          style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: 0, border: 'none', background: 'none',
            fontFamily: 'inherit', cursor: 'pointer', userSelect: 'none', marginBottom: showTest ? '0.5rem' : 0,
          }}
        >
          <Icon icon={showTest ? ChevronDown : ChevronRight} size="dense" tone="muted" />
          <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-soft)' }}>Manual test</span>
        </button>
        {showTest && (
          <div>
            {(op.required_fields || []).length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.5rem' }}>
                {(op.required_fields || []).map(key => (
                  <div key={key} style={{ flex: '1 1 160px' }}>
                    <label className="n-label">{key}</label>
                    <input
                      type="text"
                      value={testParams[key] || ''}
                      onChange={e => setTestParams(prev => ({ ...prev, [key]: e.target.value }))}
                      placeholder={(op.field_descriptions || {})[key] || key}
                      className="n-input"
                      style={fieldStyle}
                    />
                  </div>
                ))}
              </div>
            )}
            <Button size="sm" onClick={handleTest} disabled={testing}>
              {testing ? 'Testing...' : 'Test'}
            </Button>
            {testResult ? (
              <div style={{ ...resultBox(Boolean(testResult.success)), marginTop: '0.5rem' }}>
                {(testResult._steps as { id: string; status: string; error?: string; duration_ms?: number; result_preview?: Record<string, unknown> }[] || []).map((s, i) => (
                  <div key={i} style={{ marginBottom: 4 }}>
                    <div style={{ fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 4 }}>
                      <Icon icon={s.status === 'success' ? Check : X} size="dense" style={{ color: s.status === 'success' ? 'var(--ok)' : 'var(--error)' }} />
                      <span style={{ fontWeight: 500 }}>{s.id}</span>
                      {s.duration_ms != null && <span style={{ color: 'var(--muted)' }}>({s.duration_ms}ms)</span>}
                      {s.error && <span style={{ color: 'var(--error)' }}>— {s.error}</span>}
                    </div>
                    {s.result_preview && (
                      <details style={{ marginLeft: 18, marginTop: 2 }}>
                        <summary style={detailsSummary}>Step result</summary>
                        <pre style={{ ...codeBlock, ...wrapCode, margin: '2px 0 0', maxHeight: 120, padding: '6px 8px' }}>
                          {JSON.stringify(s.result_preview, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                ))}
                <details style={{ marginTop: 4 }}>
                  <summary style={detailsSummary}>Raw JSON</summary>
                  <pre style={{ ...codeBlock, margin: '4px 0 0', maxHeight: 150 }}>
                    {JSON.stringify(testResult, null, 2)}
                  </pre>
                </details>
              </div>
            ) : null}
          </div>
        )}
      </div>

      {/* Function Editor — always shown for consolidator tools */}
      <div style={{ borderTop: '1px solid var(--line)', paddingTop: '0.75rem', marginBottom: '0.5rem' }}>
        <FunctionEditor
          functionCode={((op.consolidator_config as Record<string, unknown>)?.function_code as string) || 'def run(params, call_api, log):\n    venue = params.get("venue", "")\n    log(f"Hello from {venue}")\n    return {"message": "Replace this with your function"}'}
          // Keep the rest of the config (max_api_calls, allowed_write_actions,
          // wraps, shapes) — replacing it with just the code silently dropped them.
          onChange={(code) => updateTool(idx, 'consolidator_config', { ...(op.consolidator_config || {}), function_code: code })}
          requiredFields={op.required_fields || []}
          connectorName="norm"
        />
      </div>

      {/* Config JSON removed — function editor is the only way to edit consolidator config */}

      {/* Endpoints return raw data (Sep 2026): shaping lives in the consolidator's shapes. */}
    </div>
  );
}

export default function ConnectorSpecEditor({ spec, isNew, onSave, onCancel }: Props) {
  const [form, setForm] = useState<ConnectorSpecFull>(() => withListMarkers(spec ?? { ...EMPTY_SPEC }));
  // Split into tools + endpoints lists (every connector since Sep 2026; new ones start split).
  const [split] = useState(() => Array.isArray((spec ?? EMPTY_SPEC).endpoints));
  const [saving, setSaving] = useState(false);
  // What each row is, who exposes it, what it calls and what calls it.
  const [inventory, setInventory] = useState<Record<string, SpecInventoryRow>>({});
  useEffect(() => {
    if (isNew || !spec?.connector_name) return;
    apiFetch(`/api/connector-specs/${spec.connector_name}/inventory`)
      .then(r => (r.ok ? r.json() : null))
      .then((d: { rows?: SpecInventoryRow[] } | null) => {
        if (d?.rows) setInventory(Object.fromEntries(d.rows.map(r => [r.action, r])));
      })
      .catch(() => {});
  }, [isNew, spec?.connector_name]);
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
  const [tryVenueId, setTryVenueId] = useState('');
  const [venues, setVenues] = useState<{ id: string; name: string }[]>([]);

  useEffect(() => {
    apiFetch('/api/venues').then(r => r.ok ? r.json() : null).then(d => {
      if (d?.venues) setVenues(d.venues);
    }).catch(() => {});
  }, []);

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
      tools: [...prev.tools, split ? { ...EMPTY_TOOL, _list: 'endpoints' as const } : { ...EMPTY_TOOL }],
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
        // A row stays in its own list: tools and endpoints reorder separately.
        if (prev.tools[dragIdx]?._list !== prev.tools[dragOverIdx]?._list) return prev;
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
          ...(tryVenueId ? { venue_id: tryVenueId } : {}),
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
      await onSave(toStoredSpec(form, split), isNew);
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
          <div role="dialog" aria-modal="true" aria-label="Create consolidator" style={{
            backgroundColor: 'var(--bg)', borderRadius: 'var(--radius-lg)', width: '90%', maxWidth: 700,
            maxHeight: '85vh', overflow: 'auto', padding: '20px 24px',
            boxShadow: '0 8px 30px rgba(0,0,0,0.15)',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: '1rem' }}>
              <h3 style={sectionTitle}>Create consolidator</h3>
              <IconButton
                icon={X}
                label="Close"
                onClick={() => { setShowConsolidatorBuilder(false); setConsolidatorResult(null); setConsolidatorTestResult(null); }}
              />
            </div>

            {/* Step 1: Describe */}
            <div style={{ marginBottom: '1rem' }}>
              <label className="n-label">
                Describe what this consolidator should do
              </label>
              <textarea
                value={consolidatorPrompt}
                onChange={e => setConsolidatorPrompt(e.target.value)}
                placeholder="e.g., Get stock on hand for a specific item today and 4 weeks ago, and return a comparison showing current vs historical quantity"
                rows={3}
                className="n-input"
                style={fieldStyle}
              />
              <Button
                variant={consolidatorResult && !consolidatorResult.error ? 'secondary' : 'primary'}
                style={{ marginTop: '0.5rem' }}
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
              >
                {consolidatorGenerating ? 'Generating...' : 'Generate with AI'}
              </Button>
            </div>

            {/* Step 2: Preview generated config */}
            {(consolidatorResult && !consolidatorResult.error) ? (
              <div style={{ marginBottom: '1rem' }}>
                <label className="n-label">
                  Generated config
                </label>
                <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', marginBottom: '0.5rem' }}>
                  <strong style={{ fontWeight: 600 }}>{String(consolidatorResult.action || '')}</strong> — {String(consolidatorResult.description || '')}
                </div>
                <pre style={{ ...codeBlock, maxHeight: 200 }}>
                  {JSON.stringify(consolidatorResult.consolidator_config || consolidatorResult, null, 2)}
                </pre>

                {/* Step 3: Test */}
                <div style={{ marginTop: '1rem' }}>
                  <label className="n-label">
                    Test with sample inputs
                  </label>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.5rem' }}>
                    {Object.keys(consolidatorTestParams).map(key => (
                      <div key={key} style={{ flex: '1 1 200px' }}>
                        <label className="n-label">{key}</label>
                        <input
                          value={consolidatorTestParams[key] || ''}
                          onChange={e => setConsolidatorTestParams(prev => ({ ...prev, [key]: e.target.value }))}
                          placeholder={String((consolidatorResult.field_descriptions as Record<string, string>)?.[key] || key)}
                          className="n-input"
                          style={fieldStyle}
                        />
                      </div>
                    ))}
                  </div>
                  <Button
                    size="sm"
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
                  >
                    {consolidatorTesting ? 'Testing...' : 'Test'}
                  </Button>
                </div>

                {/* Test result */}
                {consolidatorTestResult && (
                  <div style={{ marginTop: '0.5rem' }}>
                    <div style={{ marginBottom: 6 }}>
                      <Badge tone={(consolidatorTestResult as Record<string, unknown>).success ? 'ok' : 'error'}>
                        {(consolidatorTestResult as Record<string, unknown>).success ? 'Success' : 'Failed'}
                      </Badge>
                    </div>
                    <pre style={{ ...codeBlock, maxHeight: 200 }}>
                      {JSON.stringify(consolidatorTestResult, null, 2)}
                    </pre>
                  </div>
                )}

                {/* Step 4: Save */}
                <div style={{ marginTop: '1rem', display: 'flex', gap: 8 }}>
                  <Button
                    variant="primary"
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
                      setForm(prev => {
                        const tools = [...prev.tools];
                        const firstEndpoint = tools.findIndex(t => t._list === 'endpoints');
                        const row = split ? { ...newTool, _list: 'tools' as const } : newTool;
                        if (firstEndpoint >= 0) tools.splice(firstEndpoint, 0, row);
                        else tools.push(row);
                        return { ...prev, tools };
                      });
                      setShowConsolidatorBuilder(false);
                      setConsolidatorResult(null);
                      setConsolidatorTestResult(null);
                      setConsolidatorPrompt('');
                    }}
                  >
                    Add to spec
                  </Button>
                </div>
              </div>
            ) : null}

            {/* Error display */}
            {consolidatorResult?.error ? (
              <div role="alert" style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)', marginTop: '0.5rem' }}>
                {String(consolidatorResult.error)}
              </div>
            ) : null}
          </div>
        </div>
      )}

      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 6 }}>
          <BackLink label="Back to list" onClick={onCancel} />
        </div>
        <h3 style={{ margin: 0, fontSize: 'var(--fs-xl)', fontWeight: 700, letterSpacing: '-0.01em', lineHeight: 1.25, color: 'var(--text)', overflowWrap: 'anywhere' }}>
          {isNew ? 'New connector spec' : `Edit: ${form.display_name}`}
        </h3>
      </div>

      {/* Basic Info */}
      <div className="n-card" style={sectionStyle}>
        <h4 style={{ ...sectionTitle, marginBottom: 16 }}>Basic</h4>
        <div style={twoColGrid}>
          <div>
            <label className="n-label">Connector name</label>
            <input
              type="text"
              value={form.connector_name}
              onChange={e => update('connector_name', e.target.value)}
              disabled={!isNew}
              placeholder="e.g. bamboohr"
              className="n-input"
              style={fieldStyle}
            />
          </div>
          <div>
            <label className="n-label">Display name</label>
            <input
              type="text"
              value={form.display_name}
              onChange={e => update('display_name', e.target.value)}
              placeholder="e.g. BambooHR"
              className="n-input"
              style={fieldStyle}
            />
          </div>
          <div>
            <label className="n-label">Category</label>
            <input
              type="text"
              value={form.category || ''}
              onChange={e => update('category', e.target.value || null)}
              placeholder="e.g. hr, procurement"
              className="n-input"
              style={fieldStyle}
            />
          </div>
          <div>
            <label className="n-label">Execution mode</label>
            <select
              value={form.execution_mode}
              onChange={e => update('execution_mode', e.target.value as 'template' | 'agent' | 'internal')}
              className="n-select"
              style={fieldStyle}
            >
              <option value="template">Template</option>
              <option value="agent">Agent</option>
              <option value="internal">Internal</option>
            </select>
          </div>
        </div>
      </div>

      {/* Auth */}
      <div className="n-card" style={sectionStyle}>
        <h4 style={{ ...sectionTitle, marginBottom: 16 }}>Authentication</h4>
        <div style={twoColGrid}>
          <div>
            <label className="n-label">Auth type</label>
            <select
              value={form.auth_type}
              onChange={e => update('auth_type', e.target.value)}
              className="n-select"
              style={fieldStyle}
            >
              <option value="none">None (Internal)</option>
              <option value="bearer">Bearer token</option>
              <option value="api_key_header">API key header</option>
              <option value="basic">Basic auth</option>
              <option value="oauth2">OAuth2</option>
            </select>
          </div>
          <div>
            <label className="n-label">Base URL mode</label>
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
              className="n-select"
              style={fieldStyle}
            >
              <option value="fixed">Fixed URL</option>
              <option value="dynamic">User-provided domain</option>
            </select>
          </div>
        </div>
        {baseUrlMode === 'fixed' ? (
          <div style={{ marginTop: '0.75rem' }}>
            <label className="n-label">Base URL</label>
            <input
              type="text"
              value={form.base_url_template || ''}
              onChange={e => update('base_url_template', e.target.value || null)}
              placeholder="https://api.example.com/v1"
              className="n-input"
              style={fieldStyle}
            />
          </div>
        ) : (
          <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <div>
              <label className="n-label">URL pattern (use {'{{ domain }}'} as placeholder)</label>
              <input
                type="text"
                value={displayPattern(form.base_url_template || '')}
                onChange={e => {
                  const stored = storagePattern(e.target.value, domainFieldKey);
                  update('base_url_template', stored || null);
                }}
                placeholder="https://{{ domain }}.bamboohr.com/api"
                className="n-input"
                style={codeFieldStyle}
              />
            </div>
            <div style={twoColGrid}>
              <div>
                <label className="n-label">Field key</label>
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
                  className="n-input"
                  style={fieldStyle}
                />
              </div>
              <div>
                <label className="n-label">Field label</label>
                <input
                  type="text"
                  value={domainFieldLabel}
                  onChange={e => {
                    setDomainFieldLabel(e.target.value);
                    syncDomainCredField(domainFieldKey, e.target.value);
                  }}
                  placeholder="BambooHR Subdomain (e.g. mycompany)"
                  className="n-input"
                  style={fieldStyle}
                />
              </div>
            </div>
          </div>
        )}
        <div style={{ marginTop: '0.75rem' }}>
          <label className="n-label">Auth config (JSON)</label>
          <JsonTextarea
            value={form.auth_config}
            onChange={v => update('auth_config', (v ?? {}) as Record<string, unknown>)}
            rows={3}
            style={codeFieldStyle}
          />
        </div>
      </div>

      {/* OAuth Config (shown when auth_type is oauth2) */}
      {form.auth_type === 'oauth2' && (
        <div className="n-card" style={sectionStyle}>
          <h4 style={{ ...sectionTitle, marginBottom: 16 }}>OAuth 2.0 configuration</h4>
          <div style={{
            marginBottom: '0.75rem',
            padding: '8px 10px',
            backgroundColor: 'var(--info-bg)',
            borderRadius: 'var(--radius)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--info)',
            wordBreak: 'break-all',
          }}>
            <span style={{ fontWeight: 600 }}>Redirect URI: </span>
            {typeof window !== 'undefined' ? `${window.location.origin}/api/oauth/callback` : '(loading...)'}
          </div>
          <div style={twoColGrid}>
            <div>
              <label className="n-label">Authorize URL</label>
              <input
                type="text"
                value={form.oauth_config?.authorize_url || ''}
                onChange={e => update('oauth_config', { ...form.oauth_config, authorize_url: e.target.value } as ConnectorSpecFull['oauth_config'])}
                placeholder="https://provider.com/oauth/authorize"
                className="n-input"
                style={fieldStyle}
              />
            </div>
            <div>
              <label className="n-label">Token URL</label>
              <input
                type="text"
                value={form.oauth_config?.token_url || ''}
                onChange={e => update('oauth_config', { ...form.oauth_config, token_url: e.target.value } as ConnectorSpecFull['oauth_config'])}
                placeholder="https://provider.com/oauth/token"
                className="n-input"
                style={fieldStyle}
              />
            </div>
            <div>
              <label className="n-label">Client ID</label>
              <input
                type="text"
                value={form.oauth_config?.client_id || ''}
                onChange={e => update('oauth_config', { ...form.oauth_config, client_id: e.target.value } as ConnectorSpecFull['oauth_config'])}
                placeholder="your-client-id"
                className="n-input"
                style={fieldStyle}
              />
            </div>
            <div>
              <label className="n-label">Client secret</label>
              <input
                type="password"
                value={form.oauth_config?.client_secret || ''}
                onChange={e => update('oauth_config', { ...form.oauth_config, client_secret: e.target.value } as ConnectorSpecFull['oauth_config'])}
                placeholder="your-client-secret"
                className="n-input"
                style={fieldStyle}
              />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <label className="n-label">Scopes</label>
              <input
                type="text"
                value={form.oauth_config?.scopes || ''}
                onChange={e => update('oauth_config', { ...form.oauth_config, scopes: e.target.value } as ConnectorSpecFull['oauth_config'])}
                placeholder="e.g. core:time:rw"
                className="n-input"
                style={fieldStyle}
              />
            </div>
          </div>
        </div>
      )}

      {/* Credential Fields */}
      <div className="n-card" style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <h4 style={sectionTitle}>Credential fields</h4>
          <Button size="sm" icon={Plus} onClick={addCredentialField}>
            Add
          </Button>
        </div>
        {form.credential_fields.map((cf, idx) => (
          <div key={idx} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: '0.5rem' }}>
            <input
              type="text"
              value={cf.key}
              onChange={e => updateCredentialField(idx, 'key', e.target.value)}
              placeholder="key"
              className="n-input"
              style={{ flex: 1, minWidth: 0 }}
            />
            <input
              type="text"
              value={cf.label}
              onChange={e => updateCredentialField(idx, 'label', e.target.value)}
              placeholder="label"
              className="n-input"
              style={{ flex: 1, minWidth: 0 }}
            />
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', whiteSpace: 'nowrap' }}>
              <input
                type="checkbox"
                checked={cf.secret}
                onChange={e => updateCredentialField(idx, 'secret', e.target.checked)}
              />
              Secret
            </label>
            <IconButton icon={X} label="Remove credential field" onClick={() => removeCredentialField(idx)} />
          </div>
        ))}
      </div>

      {/* Connection Test */}
      <div className="n-card" style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <h4 style={sectionTitle}>Connection test</h4>
          {!form.test_request ? (
            <Button
              size="sm"
              icon={Plus}
              onClick={() => update('test_request', { method: 'GET', path_template: '', headers: {}, success_status_codes: [200], timeout_seconds: 15 })}
            >
              Add test
            </Button>
          ) : (
            <Button variant="danger" size="sm" onClick={() => update('test_request', null)}>
              Remove
            </Button>
          )}
        </div>
        {!form.test_request ? (
          <p style={emptyNote}>
            No test configured. Add a lightweight API call (e.g. a GET to a health or list endpoint) to verify credentials from the Connectors tab.
          </p>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '100px 1fr', gap: '0.75rem', alignItems: 'start' }}>
            <div>
              <label className="n-label">Method</label>
              <select
                value={form.test_request.method}
                onChange={e => update('test_request', { ...form.test_request, method: e.target.value } as TestRequest)}
                className="n-select"
                style={fieldStyle}
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
                <option value="HEAD">HEAD</option>
              </select>
            </div>
            <div>
              <label className="n-label">Path</label>
              <input
                type="text"
                value={form.test_request.path_template}
                onChange={e => update('test_request', { ...form.test_request, path_template: e.target.value } as TestRequest)}
                placeholder="/api/v1/ping"
                className="n-input"
                style={fieldStyle}
              />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <label className="n-label">Success status codes (comma-separated)</label>
              <input
                type="text"
                value={(form.test_request.success_status_codes || [200]).join(', ')}
                onChange={e => update('test_request', {
                  ...form.test_request,
                  success_status_codes: e.target.value.split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n)),
                } as TestRequest)}
                placeholder="200"
                className="n-input"
                style={fieldStyle}
              />
            </div>
          </div>
        )}
      </div>

      {/* Operations */}
      <div className="n-card" style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <div style={{ minWidth: 0, flex: '1 1 260px' }}>
            <h4 style={sectionTitle}>Tools</h4>
            <p style={sectionNote}>
              {split
                ? 'What an LLM can see — consolidators and built-ins. A tool reaches the agent only when an App claims it.'
                : 'Not split yet: tools and API endpoints are still one list.'}
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button size="sm" icon={Plus} onClick={() => setShowConsolidatorBuilder(true)}>
              Consolidator
            </Button>
            {!split && (
              <Button size="sm" icon={Plus} onClick={addTool}>
                Add tool
              </Button>
            )}
          </div>
        </div>
        {form.tools.length > 0 && !isNew && (
          <div style={{
            border: '1px solid var(--line)',
            borderRadius: 'var(--radius)',
            padding: '14px 16px',
            marginBottom: 16,
            backgroundColor: 'var(--surface)',
          }}>
            <h5 style={{ margin: '0 0 10px', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>Try tool</h5>
            <div style={{ marginBottom: '0.5rem' }}>
              <label className="n-label">Tool</label>
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
                className="n-select"
                style={fieldStyle}
              >
                <option value="">Select a tool...</option>
                {form.tools.map((t, i) => (
                  <option key={`${t.action}-${i}`} value={t.action}>
                    {t.action} ({t.method} {t.path_template})
                  </option>
                ))}
              </select>
            </div>

            {venues.length > 0 && (
              <div style={{ marginBottom: '0.5rem' }}>
                <label className="n-label">Venue</label>
                <select value={tryVenueId} onChange={e => setTryVenueId(e.target.value)} className="n-select" style={fieldStyle}>
                  <option value="">Any venue</option>
                  {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
                </select>
              </div>
            )}

            {selectedTool && (
              <>
                <div style={{ marginBottom: '0.5rem' }}>
                  <label className="n-label" style={{ marginBottom: 6 }}>Fields</label>
                  {Object.keys(selectedTool.field_mapping).length === 0 && (
                    <p style={emptyNote}>
                      No fields defined for this tool.
                    </p>
                  )}
                  {Object.keys(selectedTool.field_mapping).map(fieldKey => (
                    <div key={fieldKey} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <label className="n-label" style={{ width: 130, flexShrink: 0, marginBottom: 0, overflowWrap: 'anywhere' }}>
                        {fieldKey}
                        {selectedTool.required_fields.includes(fieldKey) && (
                          <span style={{ color: 'var(--error)', marginLeft: 2 }}>*</span>
                        )}
                      </label>
                      <input
                        type="text"
                        value={tryToolFields[fieldKey] ?? ''}
                        onChange={e => setTryToolFields(prev => ({ ...prev, [fieldKey]: e.target.value }))}
                        placeholder={selectedTool.field_descriptions?.[fieldKey] || selectedTool.field_mapping[fieldKey] || fieldKey}
                        className="n-input"
                        style={{ flex: 1, minWidth: 0 }}
                      />
                    </div>
                  ))}
                </div>

                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => handleTryRun('render')}
                    disabled={tryLoading !== null}
                  >
                    {tryLoading === 'render' ? 'Rendering...' : 'Render'}
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => handleTryRun('test')}
                    disabled={tryLoading !== null}
                  >
                    {tryLoading === 'test' ? 'Testing...' : 'Test (live)'}
                  </Button>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                    Test makes a real HTTP call
                  </span>
                </div>

                {tryError && (
                  <p role="alert" style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)', marginTop: '0.5rem', marginBottom: 0 }}>
                    {tryError}
                  </p>
                )}

                {tryDryRunResult && (
                  <div style={{ marginTop: '0.75rem' }}>
                    <div style={groupLabel}>Rendered request</div>
                    <pre style={codeBlock}>
                      {JSON.stringify(tryDryRunResult, null, 2)}
                    </pre>
                  </div>
                )}

                {tryTestResult && (() => {
                  const rendered = tryTestResult.rendered_request as Record<string, unknown> | undefined;
                  const payload = tryTestResult.response_payload;
                  const preStyle: React.CSSProperties = { ...codeBlock, maxHeight: 300 };
                  return (
                    <div style={{ marginTop: '0.75rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: '0.5rem' }}>
                        <Badge tone={tryTestResult.success ? 'ok' : 'error'}>
                          {tryTestResult.success ? 'Success' : 'Failed'}
                        </Badge>
                        {'error' in tryTestResult && Boolean(tryTestResult.error) && (
                          <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{String(tryTestResult.error)}</span>
                        )}
                      </div>
                      {rendered && rendered.url ? (
                        <div style={{ marginBottom: '0.75rem' }}>
                          <div style={groupLabel}>Request</div>
                          <pre style={preStyle}>
                            {`${rendered.method} ${rendered.url}\n`}
                            {!!(rendered.headers && Object.keys(rendered.headers as Record<string, string>).length > 0) &&
                              `${Object.entries(rendered.headers as Record<string, string>).map(([k, v]) => `${k}: ${v}`).join('\n')}\n`}
                            {rendered.body ? `\n${typeof rendered.body === 'string' ? rendered.body : JSON.stringify(rendered.body, null, 2)}` : ''}
                          </pre>
                        </div>
                      ) : (
                        <div style={{ marginBottom: '0.75rem' }}>
                          <div style={groupLabel}>Request</div>
                          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                            No request was sent — check that required fields are filled in above.
                          </div>
                        </div>
                      )}
                      <div style={{ marginBottom: '0.5rem' }}>
                        <div style={groupLabel}>Response</div>
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

        {(() => {
        const renderRow = (op: ConnectorSpecTool, idx: number) => {
          const isCollapsed = collapsedTools.has(idx);
          const inv = inventory[op.action];
          const isEndpoint = split ? op._list === 'endpoints' : inv?.kind === 'endpoint';
          const build = inv?.build ?? (op.consolidator_config ? 'consolidator' : null);
          const removeBlocked = isEndpoint
            ? (inv?.used_by.length ? `Used by ${inv.used_by.join(', ')} — change those first` : '')
            : (inv?.app ? `The ${inv.app.name} App claims this tool — remove the claim first` : '');
          const isDragOver = dragOverIdx === idx && dragIdx !== idx;
          return (
            <div
              key={idx}
              onDragOver={e => { e.preventDefault(); setDragOverIdx(idx); }}
              style={{
                border: isDragOver ? '1px solid var(--accent)' : '1px solid var(--line)',
                borderRadius: 'var(--radius)',
                marginBottom: 8,
                backgroundColor: dragIdx === idx ? 'var(--selected)' : 'var(--bg)',
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
                  flexWrap: 'wrap',
                  gap: '6px 12px',
                  padding: '10px 12px',
                  cursor: 'pointer',
                  userSelect: 'none',
                  backgroundColor: isCollapsed ? 'var(--bg)' : 'var(--surface)',
                  borderBottom: isCollapsed ? 'none' : '1px solid var(--line)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6, minWidth: 0 }}>
                  {/* Drag handle */}
                  <span
                    draggable
                    onDragStart={() => setDragIdx(idx)}
                    onDragEnd={handleDragEnd}
                    style={{ display: 'inline-flex', cursor: 'grab', color: 'var(--icon)', lineHeight: 1, padding: '0 2px', userSelect: 'none' }}
                    title="Drag to reorder"
                  ><Icon icon={GripVertical} size="dense" /></span>
                  <Icon icon={isCollapsed ? ChevronRight : ChevronDown} size="dense" tone="muted" />
                  <span style={{ fontWeight: 500, fontSize: 'var(--fs-base)', color: 'var(--text)', overflowWrap: 'anywhere' }}>
                    {op.action || (isEndpoint ? `Endpoint ${idx + 1}` : `Tool ${idx + 1}`)}
                  </span>
                  {!isEndpoint && build === 'built-in' ? (
                    <Badge title={inv?.code ? `Norm code runs instead of this row: ${inv.code}` : 'Norm code'}>Built-in</Badge>
                  ) : !isEndpoint && build === 'consolidator' ? (
                    <Badge>Consolidator</Badge>
                  ) : (
                    <>
                      {op.method && (
                        <Badge>{op.method}</Badge>
                      )}
                      {op.path_template && (
                        <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', overflowWrap: 'anywhere' }}>
                          {op.path_template}
                        </span>
                      )}
                    </>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px 10px' }}>
                  {!isEndpoint && inv && (
                    <Badge
                      tone={inv.app ? 'ok' : 'neutral'}
                      title={inv.app
                        ? `The ${inv.app.name} App puts this tool in front of the agent`
                        : 'No App claims this tool: the agent never sees it — other tools may use it'}>
                      {inv.app ? inv.app.name : 'Not exposed'}
                    </Badge>
                  )}
                  {!isEndpoint && inv && inv.uses.length > 0 && (
                    <span style={rowMeta} title={inv.uses.join('\n')}>uses {inv.uses.length}</span>
                  )}
                  {inv && inv.used_by.length > 0 && (
                    <span style={rowMeta} title={inv.used_by.join('\n')}>
                      used by {inv.used_by.map(u => u.split('.').pop()).slice(0, 2).join(', ')}
                      {inv.used_by.length > 2 ? ` +${inv.used_by.length - 2}` : ''}
                    </span>
                  )}
                  {isEndpoint && inv && inv.used_by.length === 0 && (
                    <span style={rowMeta} title="No tool calls this endpoint">unused</span>
                  )}
                  {!isEndpoint && inv && inv.calls_30d > 0 && (
                    <span style={rowMeta} title="Direct calls in the last 30 days">{inv.calls_30d} calls</span>
                  )}
                  <span style={rowMeta}
                    title="When this row was first added to the spec">
                    {op.added_at ? `added ${new Date(op.added_at).toLocaleDateString()}` : 'added —'}
                  </span>
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={!!removeBlocked}
                    title={removeBlocked || 'Remove (saved when you press Update spec)'}
                    onClick={(e) => { e.stopPropagation(); if (!removeBlocked) removeTool(idx); }}
                  >
                    Remove
                  </Button>
                </div>
              </div>
              {!isCollapsed && (
                <div style={{ padding: '14px 16px' }}>
                  {!isEndpoint && build === 'built-in' && (
                    <p style={{ margin: '0 0 0.75rem', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                      Built-in: Norm code{inv?.code ? <> at <code style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', background: 'var(--surface-alt)', padding: '1px 4px', borderRadius: 'var(--radius-sm)' }}>{inv.code}</code></> : ''} runs instead of this row.
                      The description and fields below are what the agent sees; the behaviour lives in code.
                    </p>
                  )}
                  {op.consolidator_config ? (
                    <ConsolidatorToolEditor
                      op={op}
                      idx={idx}
                      updateTool={updateTool}
                      setForm={setForm}
                    />
                  ) : (
                  <>
                  <div style={twoColGrid}>
                    <div>
                      <label className="n-label">Action</label>
                      <input
                        type="text"
                        value={op.action}
                        onChange={e => updateTool(idx, 'action', e.target.value)}
                        placeholder="create_employee"
                        className="n-input"
                        style={fieldStyle}
                      />
                    </div>
                    <div>
                      <label className="n-label">Method</label>
                      <select
                        value={op.method}
                        onChange={e => updateTool(idx, 'method', e.target.value)}
                        className="n-select"
                        style={fieldStyle}
                      >
                        <option value="GET">GET</option>
                        <option value="POST">POST</option>
                        <option value="PUT">PUT</option>
                        <option value="PATCH">PATCH</option>
                        <option value="DELETE">DELETE</option>
                      </select>
                    </div>
                    <div style={{ gridColumn: '1 / -1' }}>
                      <label className="n-label">Description</label>
                      <input
                        type="text"
                        value={op.description || ''}
                        onChange={e => updateTool(idx, 'description', e.target.value || undefined)}
                        placeholder="e.g. Get a roster by date"
                        className="n-input"
                        style={fieldStyle}
                      />
                    </div>
                    <div style={{ gridColumn: '1 / -1' }}>
                      <label className="n-label">Path template</label>
                      <input
                        type="text"
                        value={op.path_template}
                        onChange={e => updateTool(idx, 'path_template', e.target.value)}
                        placeholder="/api/v1/employees"
                        className="n-input"
                        style={fieldStyle}
                      />
                    </div>
                    <div>
                      <label className="n-label">Success status codes (comma-separated)</label>
                      <input
                        type="text"
                        value={(op.success_status_codes || [200]).join(', ')}
                        onChange={e => updateTool(idx, 'success_status_codes', e.target.value.split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n)))}
                        placeholder="200, 201"
                        className="n-input"
                        style={fieldStyle}
                      />
                    </div>
                    <div>
                      <label className="n-label">Response ref path</label>
                      <input
                        type="text"
                        value={op.response_ref_path || ''}
                        onChange={e => updateTool(idx, 'response_ref_path', e.target.value || null)}
                        placeholder="body.id"
                        className="n-input"
                        style={fieldStyle}
                      />
                    </div>
                    <div>
                      <label className="n-label">Timeout (seconds)</label>
                      <input
                        type="number"
                        value={op.timeout_seconds || 30}
                        onChange={e => updateTool(idx, 'timeout_seconds', parseInt(e.target.value, 10) || 30)}
                        className="n-input"
                        style={fieldStyle}
                      />
                    </div>
                  </div>

                  {/* Fields */}
                  <div style={{ marginTop: '1rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                      <label className="n-label" style={{ marginBottom: 0 }}>Fields</label>
                      <Button
                        size="sm"
                        icon={Plus}
                        onClick={() => {
                          let newKey = 'new_field';
                          let suffix = 1;
                          while (newKey in (op.field_mapping || {})) { newKey = `new_field_${suffix++}`; }
                          const updated = { ...(op.field_mapping || {}), [newKey]: '' };
                          updateTool(idx, 'field_mapping', updated);
                        }}
                      >
                        Add field
                      </Button>
                    </div>
                    {Object.keys(op.field_mapping || {}).length === 0 && (
                      <p style={{ ...emptyNote, margin: '0 0 0.25rem' }}>
                        No fields defined. Add fields to map source data to API parameters.
                      </p>
                    )}
                    {Object.keys(op.field_mapping || {}).length > 0 && (
                      <div style={{
                        border: '1px solid var(--line)',
                        borderRadius: 'var(--radius)',
                        overflowX: 'auto',
                        backgroundColor: 'var(--bg)',
                      }}>
                        {/* Scrolls sideways on a phone rather than squeezing five columns. */}
                        <div style={{ minWidth: 560 }}>
                          {/* Header row in the .n-table look: 12px/600 on the hover fill, strong rule. */}
                          <div style={{
                            display: 'grid',
                            gridTemplateColumns: '1fr 1fr 1.5fr 70px 40px',
                            gap: 0,
                            backgroundColor: 'var(--surface-alt)',
                            padding: '8px 10px',
                            borderBottom: '1px solid var(--line-strong)',
                          }}>
                            <span style={{ ...groupLabel, marginBottom: 0, paddingLeft: 6 }}>Field name</span>
                            <span style={{ ...groupLabel, marginBottom: 0, paddingLeft: 6 }}>API mapping</span>
                            <span style={{ ...groupLabel, marginBottom: 0, paddingLeft: 6 }}>Format hint</span>
                            <span style={{ ...groupLabel, marginBottom: 0, textAlign: 'center' }}>Required</span>
                            <span />
                          </div>
                          {Object.entries(op.field_mapping || {}).map(([fieldKey, apiMapping], fieldIdx) => (
                            <div
                              key={fieldIdx}
                              style={{
                                display: 'grid',
                                gridTemplateColumns: '1fr 1fr 1.5fr 70px 40px',
                                gap: 0,
                                alignItems: 'center',
                                padding: '4px 10px',
                                borderBottom: fieldIdx < Object.keys(op.field_mapping || {}).length - 1 ? '1px solid var(--line-soft)' : 'none',
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
                                style={cellInputStyle}
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
                                style={{ ...cellInputStyle, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' }}
                              />
                              <input
                                type="text"
                                value={(op.field_descriptions || {})[fieldKey] || ''}
                                onChange={e => {
                                  const newDescs = { ...(op.field_descriptions || {}), [fieldKey]: e.target.value };
                                  updateTool(idx, 'field_descriptions', newDescs);
                                }}
                                placeholder="e.g. Date in YYYY-MM-DD"
                                style={{ ...cellInputStyle, color: 'var(--muted)' }}
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
                              <IconButton
                                icon={X}
                                label={`Remove field ${fieldKey}`}
                                iconSize={14}
                                style={{ justifySelf: 'center' }}
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
                              />
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  <div style={{ marginTop: '0.75rem' }}>
                    <label className="n-label">Headers (JSON)</label>
                    <JsonTextarea
                      value={op.headers}
                      onChange={v => updateTool(idx, 'headers', v ?? {})}
                      autoResize
                      style={codeFieldStyle}
                    />
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <label className="n-label">Field schema (JSON)</label>
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 4 }}>
                      Define JSON Schema for complex fields (e.g., arrays with required properties). Overrides the default string type.
                    </div>
                    <JsonTextarea
                      value={op.field_schema}
                      onChange={v => updateTool(idx, 'field_schema', v)}
                      autoResize
                      placeholder='{"lines": {"type": "array", "items": {"type": "object", "properties": {...}, "required": [...]}}}'
                      style={codeFieldStyle}
                    />
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <label className="n-label">Request body template (JSON)</label>
                    <AutoResizeTextarea
                      value={op.request_body_template || ''}
                      onChange={e => updateTool(idx, 'request_body_template', e.target.value || null)}
                      placeholder=""
                      style={codeFieldStyle}
                    />
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <label className="n-label">Display component</label>
                    <input
                      value={op.display_component || ''}
                      onChange={e => updateTool(idx, 'display_component', e.target.value || null)}
                      placeholder="e.g. generic_table"
                      className="n-input"
                      style={fieldStyle}
                    />
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <label className="n-label">Display props (JSON)</label>
                    <JsonTextarea
                      value={op.display_props}
                      onChange={v => updateTool(idx, 'display_props', v)}
                      rows={2}
                      placeholder='{"title": "Results"}'
                      style={codeFieldStyle}
                    />
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <label className="n-label">Working document (JSON)</label>
                    <JsonTextarea
                      value={op.working_document}
                      onChange={v => updateTool(idx, 'working_document', v)}
                      rows={2}
                      placeholder='{"doc_type": "roster", "sync_mode": "auto", "ref_fields": ["search_date"]}'
                      style={codeFieldStyle}
                    />
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <label className="n-label">Summary fields</label>
                    <input
                      value={(op.summary_fields || []).join(', ')}
                      onChange={e => {
                        const val = e.target.value.trim();
                        updateTool(idx, 'summary_fields', val ? val.split(',').map((s: string) => s.trim()).filter(Boolean) : null);
                      }}
                      placeholder="name, id, sku, price"
                      className="n-input"
                      style={fieldStyle}
                    />
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 4 }}>
                      Comma-separated field names to show when result is too large. Leave empty to use search-only mode.
                    </div>
                  </div>

                  </>
                  )}
                </div>
              )}
            </div>
          );
        };
        const rowIdxs = form.tools.map((_, i) => i);
        const toolIdxs = split ? rowIdxs.filter(i => form.tools[i]._list !== 'endpoints') : rowIdxs;
        const endpointIdxs = split ? rowIdxs.filter(i => form.tools[i]._list === 'endpoints') : [];
        return (
          <>
            {toolIdxs.map(i => renderRow(form.tools[i], i))}
            {split && toolIdxs.length === 0 && (
              <p style={{ ...emptyNote, margin: '0 0 0.5rem' }}>No tools yet.</p>
            )}
            {split && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', margin: '24px 0 16px', paddingTop: 20, borderTop: '1px solid var(--line)' }}>
                <div style={{ minWidth: 0, flex: '1 1 260px' }}>
                  <h4 style={sectionTitle}>API endpoints</h4>
                  <p style={sectionNote}>
                    Building blocks — one call to {form.display_name || 'the outside system'} each. Tools call them; an LLM never sees them.
                  </p>
                </div>
                <Button size="sm" icon={Plus} onClick={addTool}>
                  Endpoint
                </Button>
              </div>
            )}
            {endpointIdxs.map(i => renderRow(form.tools[i], i))}
            {split && endpointIdxs.length === 0 && (
              <p style={emptyNote}>No endpoints.</p>
            )}
          </>
        );
        })()}
      </div>

      {/* Agent Mode (conditional) */}
      {form.execution_mode === 'agent' && (
        <div className="n-card" style={sectionStyle}>
          <h4 style={{ ...sectionTitle, marginBottom: 16 }}>Agent mode</h4>
          <div style={{ marginBottom: '0.75rem' }}>
            <label className="n-label">API documentation</label>
            <textarea
              value={form.api_documentation || ''}
              onChange={e => update('api_documentation', e.target.value || null)}
              rows={8}
              placeholder="Paste API docs for the LLM to reference..."
              className="n-input"
              style={{ ...codeFieldStyle, lineHeight: 1.5 }}
            />
          </div>
          <div>
            <label className="n-label">Example requests (JSON array)</label>
            <JsonTextarea
              value={form.example_requests}
              onChange={v => update('example_requests', (v ?? []) as Record<string, unknown>[])}
              rows={4}
              style={codeFieldStyle}
            />
          </div>
        </div>
      )}

      {/* Save / Cancel */}
      <div style={{ display: 'flex', gap: 8 }}>
        <Button
          variant="primary"
          onClick={handleSubmit}
          disabled={saving || !form.connector_name || !form.display_name}
        >
          {saving ? 'Saving...' : isNew ? 'Create spec' : 'Update spec'}
        </Button>
        <Button onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
