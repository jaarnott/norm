'use client';

import { useState, useMemo, useEffect, useRef } from 'react';
import { ChevronRight, ChevronDown, Check, X, Plus, GripVertical } from 'lucide-react';
import type { ConnectorSpecFull, ConnectorSpecTool, SpecInventoryRow, ToolApproval } from '../../types';
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
import Dialog, { ConfirmDialog } from '../ui/Dialog';

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

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  toolEvents?: { type: string; name?: string; input?: Record<string, unknown>; result?: Record<string, unknown>; config?: Record<string, unknown> }[];
}

function ConsolidatorToolEditor({
  op, idx, updateTool, reviewedFile,
}: {
  op: ConnectorSpecTool;
  idx: number;
  updateTool: (index: number, field: keyof ConnectorSpecTool, value: unknown) => void;
  /** config/consolidators/… when this code has a reviewed file. */
  reviewedFile?: string | null;
}) {
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [streamingEvents, setStreamingEvents] = useState<ChatMessage['toolEvents']>([]);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll within the chat container only (not the page)
  useEffect(() => {
    const el = chatEndRef.current;
    if (el?.parentElement) {
      el.parentElement.scrollTop = el.parentElement.scrollHeight;
    }
  }, [chatMessages, streamingText, streamingEvents]);

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
          Applied — press Update spec to save
        </div>
      );
    }
    return null;
  };

  return (
    <div>
      {reviewedFile && (
        <div role="note" style={{ ...resultBox(false), background: 'var(--warn-bg)', fontSize: 'var(--fs-sm)', marginBottom: '0.75rem' }}>
          This code has a reviewed file, <code>{reviewedFile}</code>. Its sync script copies that file over what is
          saved here — change the file (and run its sync) rather than editing here.
        </div>
      )}
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
            placeholder="What it does — the LLM reads this to decide when to call it"
            className="n-input"
            style={fieldStyle}
          />
        </div>
      </div>

      {/* Chat area */}
      <div style={{
        border: '1px solid var(--line)', borderRadius: 'var(--radius)',
        height: 320, minHeight: 120, overflowY: 'auto', padding: '10px 12px',
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

      {/* Function Editor — always shown for consolidator tools */}
      <div style={{ borderTop: '1px solid var(--line)', paddingTop: '0.75rem', marginBottom: '0.5rem' }}>
        <FunctionEditor
          functionCode={((op.consolidator_config as Record<string, unknown>)?.function_code as string) || 'def run(params, call_api, log):\n    venue = params.get("venue", "")\n    log(f"Hello from {venue}")\n    return {"message": "Replace this with your function"}'}
          // Keep the rest of the config (max_api_calls, allowed_write_actions,
          // wraps, shapes) — replacing it with just the code silently dropped them.
          onChange={(code) => updateTool(idx, 'consolidator_config', { ...(op.consolidator_config || {}), function_code: code })}
          fields={[...(op.required_fields || []), ...(op.optional_fields || [])]}
          config={(op.consolidator_config || {}) as Record<string, unknown>}
          action={op.action}
        />
      </div>
    </div>
  );
}

/** Every field a row takes — required, optional, and old field_mapping keys. */
function rowFields(t: ConnectorSpecTool): string[] {
  return Array.from(new Set([
    ...(t.required_fields || []),
    ...(t.optional_fields || []),
    ...Object.keys(t.field_mapping || {}),
  ]));
}

/** Calling this endpoint would change data: anything but GET, unless marked read-only. */
function isWriteRow(t: ConnectorSpecTool): boolean {
  if (t.read_only === true) return false;
  return (t.method || 'GET').toUpperCase() !== 'GET';
}

const hint: React.CSSProperties = { fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 4 };

/**
 * A row's fields as one table. Tools and MCP endpoints list theirs in
 * required_fields / optional_fields; an HTTP endpoint keys them in
 * field_mapping (field → API parameter). This edits whichever the row uses —
 * the old table read only field_mapping, so every tool showed "No fields".
 */
function FieldsEditor({ op, onPatch, withMapping, note }: {
  op: ConnectorSpecTool;
  onPatch: (patch: Partial<ConnectorSpecTool>) => void;
  withMapping: boolean;
  note: string;
}) {
  const fields = rowFields(op);
  const required = op.required_fields || [];
  const schema = (op.field_schema || {}) as Record<string, { description?: string } | undefined>;
  const columns = withMapping ? '1fr 1fr 1.5fr 70px 40px' : '1fr 2fr 70px 40px';

  const rekey = <T,>(obj: Record<string, T> | null | undefined, from: string, to: string | null) => {
    if (!obj || !(from in obj)) return obj;
    return Object.fromEntries(Object.entries(obj).flatMap(([k, v]) => (k !== from ? [[k, v]] : to === null ? [] : [[to, v]])));
  };
  const rename = (from: string, to: string) => {
    if (to !== from && fields.includes(to)) return; // would merge two fields
    onPatch({
      required_fields: required.map(f => (f === from ? to : f)),
      optional_fields: op.optional_fields?.map(f => (f === from ? to : f)),
      field_mapping: rekey(op.field_mapping, from, to) || {},
      field_descriptions: rekey(op.field_descriptions, from, to) || {},
      field_schema: rekey(op.field_schema, from, to) ?? op.field_schema,
    });
  };
  const remove = (key: string) => onPatch({
    required_fields: required.filter(f => f !== key),
    optional_fields: op.optional_fields?.filter(f => f !== key),
    field_mapping: rekey(op.field_mapping, key, null) || {},
    field_descriptions: rekey(op.field_descriptions, key, null) || {},
    field_schema: rekey(op.field_schema, key, null) ?? op.field_schema,
  });
  const setRequired = (key: string, on: boolean) => {
    const optional = (op.optional_fields || []).filter(f => f !== key);
    if (on) {
      onPatch({ required_fields: [...required, key], optional_fields: op.optional_fields && optional });
    } else {
      // A mapped field stays listed through field_mapping; anything else moves to optional.
      onPatch({
        required_fields: required.filter(f => f !== key),
        optional_fields: key in (op.field_mapping || {}) ? op.optional_fields : [...optional, key],
      });
    }
  };
  const add = () => {
    let key = 'new_field';
    for (let n = 1; fields.includes(key); n++) key = `new_field_${n}`;
    onPatch(withMapping
      ? { field_mapping: { ...(op.field_mapping || {}), [key]: '' } }
      : { optional_fields: [...(op.optional_fields || []), key] });
  };

  return (
    <div style={{ marginTop: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: 12, marginBottom: 6 }}>
        <div>
          <label className="n-label" style={{ marginBottom: 0 }}>Fields</label>
          <div style={{ ...hint, marginTop: 0 }}>{note}</div>
        </div>
        <Button size="sm" icon={Plus} onClick={add}>Add field</Button>
      </div>
      {fields.length === 0 ? (
        <p style={{ ...emptyNote, margin: '0 0 0.25rem' }}>No fields — it takes no input.</p>
      ) : (
        <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflowX: 'auto', backgroundColor: 'var(--bg)' }}>
          {/* Scrolls sideways on a phone rather than squeezing the columns. */}
          <div style={{ minWidth: withMapping ? 560 : 440 }}>
            <div style={{
              display: 'grid', gridTemplateColumns: columns, backgroundColor: 'var(--surface-alt)',
              padding: '8px 10px', borderBottom: '1px solid var(--line-strong)',
            }}>
              <span style={{ ...groupLabel, marginBottom: 0, paddingLeft: 6 }}>Field</span>
              {withMapping && <span style={{ ...groupLabel, marginBottom: 0, paddingLeft: 6 }}>API parameter</span>}
              <span style={{ ...groupLabel, marginBottom: 0, paddingLeft: 6 }}>Description</span>
              <span style={{ ...groupLabel, marginBottom: 0, textAlign: 'center' }}>Required</span>
              <span />
            </div>
            {fields.map((key, i) => (
              <div key={i} style={{
                display: 'grid', gridTemplateColumns: columns, alignItems: 'center', padding: '4px 10px',
                borderBottom: i < fields.length - 1 ? '1px solid var(--line-soft)' : 'none',
              }}>
                <input type="text" value={key} aria-label="Field name" onChange={e => rename(key, e.target.value)}
                  placeholder="field_name" style={{ ...cellInputStyle, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' }} />
                {withMapping && (
                  <input type="text" value={(op.field_mapping || {})[key] ?? ''} aria-label={`API parameter for ${key}`}
                    onChange={e => onPatch({ field_mapping: { ...(op.field_mapping || {}), [key]: e.target.value } })}
                    placeholder="apiFieldName" style={{ ...cellInputStyle, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' }} />
                )}
                <input type="text" value={(op.field_descriptions || {})[key] || ''} aria-label={`Description of ${key}`}
                  onChange={e => onPatch({ field_descriptions: { ...(op.field_descriptions || {}), [key]: e.target.value } })}
                  placeholder={schema[key]?.description || 'What to pass, e.g. a date as YYYY-MM-DD'}
                  style={{ ...cellInputStyle, color: 'var(--text-soft)' }} />
                <div style={{ textAlign: 'center' }}>
                  <input type="checkbox" aria-label={`${key} is required`} checked={required.includes(key)}
                    onChange={e => setRequired(key, e.target.checked)} style={{ cursor: 'pointer' }} />
                </div>
                <IconButton icon={X} label={`Remove field ${key}`} iconSize={14} style={{ justifySelf: 'center' }} onClick={() => remove(key)} />
              </div>
            ))}
          </div>
        </div>
      )}
      <div style={{ marginTop: '0.75rem' }}>
        <label className="n-label">Field schema (JSON)</label>
        <div style={{ ...hint, marginTop: 0, marginBottom: 4 }}>
          Types for fields that aren&rsquo;t plain text — lists, numbers, choices. Without it every field is sent as a string.
        </div>
        <JsonTextarea
          value={op.field_schema}
          onChange={v => onPatch({ field_schema: v as ConnectorSpecTool['field_schema'] })}
          autoResize
          placeholder='{"lines": {"type": "array", "items": {"type": "object", "properties": {...}, "required": [...]}}}'
          style={codeFieldStyle}
        />
      </div>
    </div>
  );
}

const EFFECT_HINT: Record<string, string> = {
  read: 'Changes nothing, so it runs without asking.',
  draft: 'Opens an editor for a person to finish — their Save on it is the approval.',
  write: 'Changes data. The approval decides whether a person is asked first.',
};

/** A tool's declared effect and, for a write, its approval policy. */
function ToolEffectEditor({ op, onPatch, claimed }: {
  op: ConnectorSpecTool;
  onPatch: (patch: Partial<ConnectorSpecTool>) => void;
  claimed: boolean;
}) {
  const approval = op.approval || {};
  const setApproval = (patch: Partial<ToolApproval>) => onPatch({ approval: { ...approval, ...patch } });
  const levels = approval.levels || [];
  return (
    <div style={{ marginTop: '1rem', paddingTop: '0.75rem', borderTop: '1px solid var(--line-soft)' }}>
      <div style={twoColGrid}>
        <div>
          <label className="n-label">What it does</label>
          <select
            value={op.effect || ''}
            onChange={e => {
              const effect = (e.target.value || null) as ConnectorSpecTool['effect'];
              onPatch(effect === 'write' && !op.approval
                ? { effect, approval: { default: 'ask', allow_auto: true } }
                : { effect });
            }}
            className="n-select"
            style={fieldStyle}
          >
            <option value="">Not declared</option>
            <option value="read">Read</option>
            <option value="draft">Draft</option>
            <option value="write">Write</option>
          </select>
          <div style={{ ...hint, color: !op.effect && claimed ? 'var(--warn)' : 'var(--muted)' }}>
            {op.effect
              ? EFFECT_HINT[op.effect]
              : claimed
                ? 'An App claims this tool, so it must declare one. Until then a GET runs and anything else asks.'
                : 'Not declared: a GET runs and anything else asks.'}
          </div>
        </div>
        {op.effect === 'write' && (
          <div>
            <label className="n-label">Approval</label>
            <select
              value={approval.default || 'ask'}
              onChange={e => setApproval({ default: e.target.value as 'ask' | 'auto' })}
              className="n-select"
              style={fieldStyle}
            >
              <option value="ask">Asks a person first</option>
              <option value="auto">Runs without asking</option>
            </select>
            {approval.default !== 'auto' && levels.length === 0 && (
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer' }}>
                <input type="checkbox" checked={!!approval.allow_auto} onChange={e => setApproval({ allow_auto: e.target.checked })} />
                Each person may switch it to &ldquo;always allow&rdquo;
              </label>
            )}
          </div>
        )}
        {op.effect === 'write' && (
          <div style={{ gridColumn: '1 / -1' }}>
            <label className="n-label">Label</label>
            <input
              type="text"
              value={approval.label || ''}
              onChange={e => setApproval({ label: e.target.value || undefined })}
              placeholder="update stock items"
              className="n-input"
              style={fieldStyle}
            />
            <div style={hint}>
              Completes &ldquo;Always allow Norm to …&rdquo; on the approval card and in Settings → Preferences.
            </div>
          </div>
        )}
      </div>
      {op.effect === 'write' && levels.length > 0 && (
        <p style={{ ...hint, marginTop: 8 }}>
          {levels.length} levels — {levels.map(l => l.label || l.id).join(' · ')}. Each person picks theirs in
          Settings → Preferences; the levels themselves are set by the config scripts.
        </p>
      )}
    </div>
  );
}

/** Settings for how a tool's result reaches the chat — only a tool's result is ever shown. */
function ToolDisplayEditor({ op, onPatch }: {
  op: ConnectorSpecTool;
  onPatch: (patch: Partial<ConnectorSpecTool>) => void;
}) {
  const set = [op.display_component, op.display_props, op.working_document, op.summary_fields?.length].filter(Boolean).length;
  return (
    <details style={{ marginTop: '1rem' }}>
      <summary style={{ ...detailsSummary, fontSize: 'var(--fs-sm)' }}>
        How the result is shown{set ? ` (${set} set)` : ''}
      </summary>
      <div style={{ marginTop: '0.75rem' }}>
        <label className="n-label">Display component</label>
        <input
          value={op.display_component || ''}
          onChange={e => onPatch({ display_component: e.target.value || null })}
          placeholder="e.g. recipe_editor"
          className="n-input"
          style={fieldStyle}
        />
        <div style={hint}>The card the result opens as in chat. Leave empty for a plain answer.</div>
      </div>
      <div style={{ marginTop: '0.75rem' }}>
        <label className="n-label">Display props (JSON)</label>
        <JsonTextarea
          value={op.display_props}
          onChange={v => onPatch({ display_props: v as ConnectorSpecTool['display_props'] })}
          rows={2}
          placeholder='{"title": "Results"}'
          style={codeFieldStyle}
        />
      </div>
      <div style={{ marginTop: '0.75rem' }}>
        <label className="n-label">Working document (JSON)</label>
        <JsonTextarea
          value={op.working_document}
          onChange={v => onPatch({ working_document: v as ConnectorSpecTool['working_document'] })}
          rows={2}
          placeholder='{"doc_type": "roster", "sync_mode": "auto", "ref_fields": ["search_date"]}'
          style={codeFieldStyle}
        />
        <div style={hint}>Keeps the result as a document the card edits and Norm reads back.</div>
      </div>
      <div style={{ marginTop: '0.75rem' }}>
        <label className="n-label">Summary fields</label>
        <input
          value={(op.summary_fields || []).join(', ')}
          onChange={e => {
            const val = e.target.value.trim();
            onPatch({ summary_fields: val ? val.split(',').map(f => f.trim()).filter(Boolean) : null });
          }}
          placeholder="name, id, sku, price"
          className="n-input"
          style={fieldStyle}
        />
        <div style={hint}>
          When a list result is too big for the model, keep only these fields of each item. Empty: the model gets a
          sample item and searches the rest.
        </div>
      </div>
    </details>
  );
}

/** How Norm signs in to the outside system — the spec's auth_type. */
const AUTH_OPTIONS: [string, string][] = [
  ['none', 'None'],
  ['bearer', 'Bearer token'],
  ['api_key_header', 'API key in a header'],
  ['api_key', 'API key'],
  ['basic', 'Username and password (basic)'],
  ['oauth2', 'OAuth'],
  ['custom', 'Custom'],
];

// A heading inside the connection dialog.
const dialogSection: React.CSSProperties = { margin: '22px 0 10px', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' };

export default function ConnectorSpecEditor({ spec, isNew, onSave, onCancel }: Props) {
  const [form, setForm] = useState<ConnectorSpecFull>(() => withListMarkers(spec ?? { ...EMPTY_SPEC }));
  // Split into tools + endpoints lists (every connector since Sep 2026; new ones start split).
  const [split] = useState(() => Array.isArray((spec ?? EMPTY_SPEC).endpoints));
  const [saving, setSaving] = useState(false);
  // What each row is, who exposes it, what it calls and what calls it.
  const [inventory, setInventory] = useState<Record<string, SpecInventoryRow>>({});
  // Until it arrives a row's kind is unknown — a tool without code may be a
  // built-in, so it isn't labelled a direct call before the inventory says so.
  const [inventoryLoaded, setInventoryLoaded] = useState(false);
  useEffect(() => {
    if (isNew || !spec?.connector_name) return;
    apiFetch(`/api/connector-specs/${spec.connector_name}/inventory`)
      .then(r => (r.ok ? r.json() : null))
      .then((d: { rows?: SpecInventoryRow[] } | null) => {
        if (d?.rows) {
          setInventory(Object.fromEntries(d.rows.map(r => [r.action, r])));
          setInventoryLoaded(true);
        }
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
  const [builderEffect, setBuilderEffect] = useState<'' | 'read' | 'draft' | 'write'>('');
  const [builderLabel, setBuilderLabel] = useState('');

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

  // The connection dialog edits the form in place; Cancel puts back what was
  // there when it opened (the base-URL helpers keep some of it in local state).
  const [connectionSnapshot, setConnectionSnapshot] = useState<{
    form: Pick<ConnectorSpecFull, 'auth_type' | 'auth_config' | 'base_url_template' | 'oauth_config' | 'credential_fields'>;
    baseUrlMode: 'fixed' | 'dynamic';
    domainFieldKey: string;
    domainFieldLabel: string;
  } | null>(null);
  const openConnection = () => setConnectionSnapshot({
    form: {
      auth_type: form.auth_type,
      auth_config: form.auth_config,
      base_url_template: form.base_url_template,
      oauth_config: form.oauth_config,
      credential_fields: form.credential_fields,
    },
    baseUrlMode,
    domainFieldKey,
    domainFieldLabel,
  });
  const cancelConnection = () => {
    if (connectionSnapshot) {
      setForm(prev => ({ ...prev, ...connectionSnapshot.form }));
      setBaseUrlMode(connectionSnapshot.baseUrlMode);
      setDomainFieldKey(connectionSnapshot.domainFieldKey);
      setDomainFieldLabel(connectionSnapshot.domainFieldLabel);
    }
    setConnectionSnapshot(null);
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

  const [confirmLiveWrite, setConfirmLiveWrite] = useState(false);
  const [removingIdx, setRemovingIdx] = useState<number | null>(null);


  // Only endpoints can be tried: the dry-run and test routes call one row
  // directly, which for a consolidator or built-in would skip its code.
  const tryRows = useMemo(
    () => form.tools.filter(t => (split ? t._list === 'endpoints' : !t.consolidator_config?.function_code) && t.action),
    [form.tools, split],
  );
  const selectedTool = useMemo(
    () => tryRows.find(t => t.action === tryToolAction) ?? null,
    [tryRows, tryToolAction],
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
    // Rows are tracked by position: the ones after the removed row move up one.
    setCollapsedTools(prev => new Set(Array.from(prev).filter(i => i !== index).map(i => (i > index ? i - 1 : i))));
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
      {confirmLiveWrite && selectedTool && (
        <ConfirmDialog
          title={`Call ${selectedTool.action} for real?`}
          confirmLabel="Call it"
          busyLabel="Calling…"
          danger
          onConfirm={() => handleTryRun('test')}
          onClose={() => setConfirmLiveWrite(false)}
        >
          <p style={{ margin: 0 }}>
            This is a write. It sends the request to {form.display_name || form.connector_name} and changes
            data for {venues.find(v => v.id === tryVenueId)?.name ?? 'the first connected venue'}.
            {form.execution_mode === 'template' ? ' Use Render to check the request without sending it.' : ''}
          </p>
        </ConfirmDialog>
      )}
      {removingIdx !== null && form.tools[removingIdx] && (() => {
        const row = form.tools[removingIdx];
        const file = inventory[row.action]?.reviewed_file;
        return (
          <ConfirmDialog
            title={`Remove ${row.action || 'this row'}?`}
            confirmLabel="Remove"
            danger
            onConfirm={async () => { removeTool(removingIdx); }}
            onClose={() => setRemovingIdx(null)}
          >
            <p style={{ margin: 0 }}>
              It leaves the spec when you press Update spec.
              {row.consolidator_config?.function_code
                ? file ? ` Its code goes with it; ${file} keeps the reviewed copy.` : ' Its code goes with it, and there is no other copy.'
                : ''}
            </p>
          </ConfirmDialog>
        );
      })()}
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
                      setBuilderEffect(['read', 'draft', 'write'].includes(data.effect) ? data.effect : '');
                      setBuilderLabel(data.approval?.label || '');
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
                  <div style={{ ...hint, marginTop: 0, marginBottom: 6 }}>Reads for real; never writes — any write it tries is shown, not sent.</div>
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
                            action: String(consolidatorResult?.action || ''),
                            preview: true,
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
                <div style={{ marginTop: '1rem', ...twoColGrid }}>
                  <div>
                    <label className="n-label" htmlFor="builder-effect">What it does</label>
                    <select
                      id="builder-effect"
                      value={builderEffect}
                      onChange={e => setBuilderEffect(e.target.value as typeof builderEffect)}
                      className="n-select"
                      style={fieldStyle}
                    >
                      <option value="">Choose…</option>
                      <option value="read">Read</option>
                      <option value="draft">Draft</option>
                      <option value="write">Write</option>
                    </select>
                    <div style={hint}>{builderEffect ? EFFECT_HINT[builderEffect] : 'Every tool declares one; it decides whether a person is asked.'}</div>
                  </div>
                  {builderEffect === 'write' && (
                    <div>
                      <label className="n-label" htmlFor="builder-label">Approval label</label>
                      <input
                        id="builder-label"
                        value={builderLabel}
                        onChange={e => setBuilderLabel(e.target.value)}
                        placeholder="update stock items"
                        className="n-input"
                        style={fieldStyle}
                      />
                      <div style={hint}>Asks a person first; completes &ldquo;Always allow Norm to …&rdquo;.</div>
                    </div>
                  )}
                </div>
                <div style={{ marginTop: '0.75rem', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <Button
                    variant="primary"
                    disabled={!builderEffect || (builderEffect === 'write' && !builderLabel.trim())}
                    onClick={() => {
                      const generated = (consolidatorResult?.approval || {}) as ToolApproval;
                      // Add as a new tool to the form
                      const newTool: ConnectorSpecTool = {
                        ...EMPTY_TOOL,
                        action: String(consolidatorResult?.action || 'consolidator'),
                        method: 'GET',
                        description: String(consolidatorResult?.description || ''),
                        required_fields: (consolidatorResult?.required_fields || []) as string[],
                        optional_fields: (consolidatorResult?.optional_fields || []) as string[],
                        field_descriptions: (consolidatorResult?.field_descriptions || {}) as Record<string, string>,
                        consolidator_config: (consolidatorResult?.consolidator_config || {}) as Record<string, unknown>,
                        effect: builderEffect || null,
                        ...(builderEffect === 'write'
                          ? { approval: { default: 'ask', allow_auto: true, ...generated, label: builderLabel.trim() } }
                          : {}),
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
                      setBuilderEffect('');
                      setBuilderLabel('');
                    }}
                  >
                    Add to tools
                  </Button>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Saved when you press Update spec</span>
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
            <label className="n-label">Who signs in</label>
            {/* Stored as the spec's category: "email" makes each person sign in
                with their own account (oauth.py), "_platform" is one Norm-wide
                key. Any other value is an old per-agent category and means
                "each venue". */}
            <select
              value={form.category === 'email' ? 'person' : form.category === '_platform' ? 'platform' : 'venue'}
              onChange={e => {
                const v = e.target.value;
                const legacy = form.category === 'email' || form.category === '_platform' ? null : form.category;
                update('category', v === 'person' ? 'email' : v === 'platform' ? '_platform' : legacy);
              }}
              className="n-select"
              style={fieldStyle}
            >
              <option value="venue">Each venue</option>
              <option value="person">Each person, with their own account</option>
              <option value="platform">Norm-wide (one key)</option>
            </select>
          </div>
          <div>
            <label className="n-label">Execution mode</label>
            <select
              value={form.execution_mode}
              onChange={e => update('execution_mode', e.target.value as ConnectorSpecFull['execution_mode'])}
              className="n-select"
              style={fieldStyle}
            >
              <option value="template">HTTP API (one request per endpoint)</option>
              <option value="mcp">MCP server (Orbit / Cook Brothers App)</option>
              <option value="internal">Built into Norm</option>
              {form.execution_mode === 'agent' && <option value="agent">Agent (legacy)</option>}
            </select>
          </div>
        </div>
        {(() => {
          // One line per fact; the dialog holds the fields themselves.
          const oauth = form.oauth_config;
          const scopes = (oauth?.scopes || '').split(/\s+/).filter(Boolean).length;
          const signIn = AUTH_OPTIONS.find(([v]) => v === form.auth_type)?.[1] ?? form.auth_type;
          const oauthBits = form.auth_type !== 'oauth2' ? [] : [
            oauth?.registration_url ? 'client registers itself' : oauth?.client_id ? `client ${oauth.client_id}` : 'no client ID yet',
            scopes ? `${scopes} ${scopes === 1 ? 'scope' : 'scopes'}` : 'no scopes',
          ];
          const address = form.execution_mode === 'internal' ? null
            : baseUrlMode === 'dynamic' ? `${displayPattern(form.base_url_template || '')} — each connection gives its ${domainFieldLabel || 'domain'}`
            : form.base_url_template || null;
          const asks = form.credential_fields.map(cf => cf.label || cf.key).filter(Boolean);
          const term: React.CSSProperties = { color: 'var(--muted)', whiteSpace: 'nowrap' };
          const desc: React.CSSProperties = { margin: 0, color: 'var(--text)', minWidth: 0, overflowWrap: 'anywhere' };
          return (
            <div style={{
              marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--line-soft)',
              display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap',
            }}>
              <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 14px', fontSize: 'var(--fs-sm)', flex: '1 1 320px', minWidth: 0 }}>
                <dt style={term}>Sign-in</dt>
                <dd style={desc}>{[signIn, ...oauthBits].join(' · ')}</dd>
                {form.execution_mode !== 'internal' && (
                  <>
                    <dt style={term}>{form.execution_mode === 'mcp' ? 'MCP server' : 'Address'}</dt>
                    <dd style={{ ...desc, color: address ? 'var(--text)' : 'var(--warn)' }}>{address || 'Not set'}</dd>
                  </>
                )}
                <dt style={term}>Asks for</dt>
                <dd style={{ ...desc, color: asks.length ? 'var(--text)' : 'var(--muted)' }}>{asks.length ? asks.join(', ') : 'Nothing'}</dd>
              </dl>
              <Button size="sm" onClick={openConnection}>Edit connection</Button>
            </div>
          );
        })()}
      </div>

      {/* Sign-in, address and what each connection asks for — set once, so
          they live in a dialog behind the summary in the Basic card. */}
      {connectionSnapshot && (
        <Dialog title="Connection" width={680} onClose={cancelConnection} closeButton>
          <p style={{ ...sectionNote, margin: '-8px 0 4px' }}>
            How Norm reaches {form.display_name || 'this system'} and signs in. Changes are saved with Update spec.
          </p>

          <h4 style={{ ...dialogSection, marginTop: 16 }}>Sign-in and address</h4>
          <div style={twoColGrid}>
            <div>
              <label className="n-label">Sign-in</label>
              <select
                value={form.auth_type}
                onChange={e => update('auth_type', e.target.value)}
                className="n-select"
                style={fieldStyle}
              >
                {AUTH_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              </select>
            </div>
            {form.execution_mode !== 'internal' && <div>
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
            </div>}
          </div>
          {form.execution_mode === 'internal' ? (
            <p style={{ ...emptyNote, marginTop: '0.75rem' }}>Built into Norm — no URL; its tools run Norm’s own code.</p>
          ) : baseUrlMode === 'fixed' ? (
            <div style={{ marginTop: '0.75rem' }}>
              <label className="n-label">{form.execution_mode === 'mcp' ? 'MCP server URL' : 'Base URL'}</label>
              <input
                type="text"
                value={form.base_url_template || ''}
                onChange={e => update('base_url_template', e.target.value || null)}
                placeholder={form.execution_mode === 'mcp' ? 'https://example.com/mcp' : 'https://api.example.com/v1'}
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

          {form.auth_type === 'oauth2' && (
            <>
              <h4 style={dialogSection}>OAuth</h4>
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
                <div style={{ color: 'var(--text-soft)', marginTop: 2 }}>
                  This environment’s. The spec is shared, so each environment (production, staging, local) uses its own
                  /api/oauth/callback — register every one with the provider.
                </div>
              </div>
              {form.oauth_config?.registration_url && (
                <p style={{ ...emptyNote, marginBottom: '0.75rem' }}>
                  This client registers itself (dynamic registration at {String(form.oauth_config.registration_url)}): the client ID
                  is filled in automatically and there is no secret. Leave them alone — editing them breaks the connection.
                </p>
              )}
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
                    placeholder={form.oauth_config?.registration_url ? 'filled in automatically' : 'your-client-id'}
                    readOnly={!!form.oauth_config?.registration_url}
                    className="n-input"
                    style={fieldStyle}
                  />
                </div>
                {!form.oauth_config?.registration_url && (
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
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 4 }}>
                      Never shown once saved. Leave the dots to keep it; type a new one to replace it.
                    </div>
                  </div>
                )}
                <div style={{ gridColumn: '1 / -1' }}>
                  <label className="n-label">Scopes</label>
                  <AutoResizeTextarea
                    value={form.oauth_config?.scopes || ''}
                    onChange={e => update('oauth_config', { ...form.oauth_config, scopes: e.target.value.replace(/\s*\n\s*/g, ' ') } as ConnectorSpecFull['oauth_config'])}
                    placeholder="e.g. core:time:rw"
                    style={codeFieldStyle}
                  />
                </div>
              </div>
            </>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: 12, margin: '22px 0 10px' }}>
            <div>
              <h4 style={{ ...dialogSection, margin: 0 }}>What each connection asks for</h4>
              <p style={{ ...sectionNote, margin: '2px 0 0' }}>
                Typed in when a venue or person connects. {'{{ creds.KEY }}'} and the auth config read each one by its key.
              </p>
            </div>
            <Button size="sm" icon={Plus} onClick={addCredentialField}>Add</Button>
          </div>
          {form.credential_fields.length === 0 && <p style={emptyNote}>None — the sign-in needs nothing typed in.</p>}
          {form.credential_fields.map((cf, idx) => (
            <div key={idx} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: '0.5rem' }}>
              <input
                type="text"
                value={cf.key}
                onChange={e => updateCredentialField(idx, 'key', e.target.value)}
                aria-label="Key"
                placeholder="key, e.g. api_key"
                className="n-input"
                style={{ flex: 1, minWidth: 0 }}
              />
              <input
                type="text"
                value={cf.label}
                onChange={e => updateCredentialField(idx, 'label', e.target.value)}
                aria-label="Label shown when connecting"
                placeholder="label, e.g. API key"
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

          <details style={{ marginTop: 20 }}>
            <summary style={{ ...detailsSummary, fontSize: 'var(--fs-sm)' }}>Advanced — auth config (JSON)</summary>
            <p style={{ ...sectionNote, margin: '8px 0 6px' }}>
              Where the sign-in reads its values, e.g. {'{"header_name": "X-API-Key"}'}. Most specs leave it as it is.
            </p>
            <JsonTextarea
              value={form.auth_config}
              onChange={v => update('auth_config', (v ?? {}) as Record<string, unknown>)}
              rows={3}
              style={codeFieldStyle}
            />
          </details>

          {/* Pinned to the bottom of the dialog, which scrolls when the OAuth fields are open. */}
          <div style={{
            position: 'sticky', bottom: -24, margin: '24px -24px -24px', padding: '12px 24px 24px',
            display: 'flex', justifyContent: 'flex-end', gap: 8,
            backgroundColor: 'var(--bg)', borderTop: '1px solid var(--line)',
          }}>
            <Button onClick={cancelConnection}>Cancel</Button>
            <Button variant="primary" onClick={() => setConnectionSnapshot(null)}>Done</Button>
          </div>
        </Dialog>
      )}

      {/* No "Connection test" card: nothing in the app runs a spec's
          test_request (Oct 2026), so it is left as stored and not offered. */}

      {/* Operations */}
      <div className="n-card" style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <div style={{ minWidth: 0, flex: '1 1 260px' }}>
            <h4 style={sectionTitle}>Tools</h4>
            <p style={sectionNote}>
              {split
                ? 'What an LLM can see — consolidators and built-ins. A tool reaches Norm or MCP only when an App claims it (Settings → App map).'
                : 'Not split yet: tools and API endpoints are still one list.'}
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button size="sm" icon={Plus} onClick={() => setShowConsolidatorBuilder(true)}>
              Consolidator
            </Button>
            {!split && (
              <Button size="sm" icon={Plus} onClick={addTool}>
                Endpoint
              </Button>
            )}
          </div>
        </div>
        {(() => {
        const renderRow = (op: ConnectorSpecTool, idx: number) => {
          const isCollapsed = collapsedTools.has(idx);
          const inv = inventory[op.action];
          const isEndpoint = split ? op._list === 'endpoints' : inv?.kind === 'endpoint';
          // A consolidator is a row with code; a built-in is Norm code registered
          // under the row's name. A tool that is neither makes its HTTP call raw.
          const build = isEndpoint ? 'endpoint'
            : op.consolidator_config?.function_code ? 'consolidator'
            : inv?.build === 'built-in' || (!inv && form.execution_mode === 'internal') ? 'built-in'
            : inventoryLoaded || isNew ? 'raw'
            : null;
          const isMcp = form.execution_mode === 'mcp';
          const showHttp = (build === 'endpoint' || build === 'raw') && form.execution_mode === 'template';
          const approval = op.approval || {};
          const effectBadge = isEndpoint ? null
            : op.effect === 'write'
              ? approval.levels?.length ? 'Write · by level' : approval.default === 'auto' ? 'Write · runs' : 'Write · asks'
              : op.effect === 'draft' ? 'Draft' : op.effect === 'read' ? 'Read' : null;
          const removeBlocked = isEndpoint
            ? (inv?.used_by.length ? `Used by ${inv.used_by.join(', ')} — change those first` : '')
            : (inv?.app ? `The ${inv.app.name} App claims this tool — remove the claim first` : '');
          const isDragOver = dragOverIdx === idx && dragIdx !== idx;
          const patch = (p: Partial<ConnectorSpecTool>) => setForm(prev => ({
            ...prev,
            tools: prev.tools.map((o, i) => (i === idx ? { ...o, ...p } : o)),
          }));
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
                    {op.action || (isEndpoint ? 'New endpoint' : 'New tool')}
                  </span>
                  {build === 'built-in' && (
                    <Badge title={inv?.code ? `Norm code runs when this tool is called: ${inv.code}` : 'Norm code runs when this tool is called'}>Built-in</Badge>
                  )}
                  {build === 'consolidator' && <Badge>Consolidator</Badge>}
                  {build === 'raw' && (
                    <Badge tone="warn" title="Neither a consolidator nor a built-in: an LLM calling it makes this HTTP call directly">Direct call</Badge>
                  )}
                  {effectBadge && (
                    <Badge
                      tone={op.effect === 'write' ? 'warn' : op.effect === 'draft' ? 'info' : 'neutral'}
                      title={op.effect === 'write' ? (approval.label ? `Approval: ${approval.label}` : 'Changes data') : EFFECT_HINT[op.effect || '']}
                    >{effectBadge}</Badge>
                  )}
                  {(isEndpoint || build === 'raw') && (isMcp
                    ? <Badge>{isWriteRow(op) ? 'Write' : 'Read'}</Badge>
                    : (
                      <>
                        {op.method && <Badge>{op.method}</Badge>}
                        {op.path_template && (
                          <span title={op.path_template} style={{
                            fontSize: 'var(--fs-sm)', color: 'var(--muted)', maxWidth: 'min(440px, 100%)',
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                          }}>
                            {op.path_template}
                          </span>
                        )}
                      </>
                    ))}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px 10px' }}>
                  {!isEndpoint && inv && (
                    <Badge
                      tone={inv.app ? 'ok' : 'neutral'}
                      title={inv.app
                        ? `The ${inv.app.name} App puts this tool in front of Norm and MCP`
                        : 'No App claims this tool, so no LLM sees it'}>
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
                    <span style={rowMeta} title="No consolidator calls this endpoint">unused</span>
                  )}
                  {!isEndpoint && inv && inv.calls_30d > 0 && (
                    <span style={rowMeta} title="Calls in the last 30 days">{inv.calls_30d} calls</span>
                  )}
                  {op.added_at && (
                    <span style={rowMeta} title="When this row was first added to the spec">
                      added {new Date(op.added_at).toLocaleDateString()}
                    </span>
                  )}
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={!!removeBlocked}
                    title={removeBlocked || 'Remove (saved when you press Update spec)'}
                    onClick={(e) => { e.stopPropagation(); if (!removeBlocked) setRemovingIdx(idx); }}
                  >
                    Remove
                  </Button>
                </div>
              </div>
              {!isCollapsed && (
                <div style={{ padding: '14px 16px' }}>
                  {build === 'consolidator' ? (
                    <ConsolidatorToolEditor
                      op={op}
                      idx={idx}
                      updateTool={updateTool}
                      reviewedFile={inv?.reviewed_file}
                    />
                  ) : (
                    <>
                      {build === 'built-in' && (
                        <p style={{ margin: '0 0 0.75rem', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                          Built-in: Norm code{inv?.code ? <> at <code style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', background: 'var(--surface-alt)', padding: '1px 4px', borderRadius: 'var(--radius-sm)' }}>{inv.code}</code></> : ''} runs
                          when it is called. What you edit here is what the LLM sees — the description and fields; the behaviour lives in code.
                        </p>
                      )}
                      {build === 'raw' && (
                        <p style={{ margin: '0 0 0.75rem', fontSize: 'var(--fs-sm)', color: 'var(--warn)' }}>
                          This tool has no code, so an LLM calling it makes the HTTP call below directly. Tools should be
                          consolidators: give it code with the Consolidator builder, or keep the call as an API endpoint.
                        </p>
                      )}
                      <div style={twoColGrid}>
                        <div>
                          <label className="n-label">Action</label>
                          <input
                            type="text"
                            value={op.action}
                            onChange={e => updateTool(idx, 'action', e.target.value)}
                            disabled={build === 'built-in'}
                            title={build === 'built-in' ? 'The name ties this row to its Norm code, so it can’t be changed here' : undefined}
                            placeholder={isEndpoint ? 'get_stock_items' : 'get_stock'}
                            className="n-input"
                            style={fieldStyle}
                          />
                        </div>
                        {(showHttp || (isEndpoint && isMcp)) && (
                          <div>
                            <label className="n-label">{isMcp ? 'Read or write' : 'Method'}</label>
                            <select
                              value={op.method}
                              onChange={e => (isMcp
                                ? patch({ method: e.target.value, read_only: e.target.value === 'GET' })
                                : updateTool(idx, 'method', e.target.value))}
                              className="n-select"
                              style={fieldStyle}
                            >
                              {isMcp ? (
                                <>
                                  <option value="GET">Read</option>
                                  <option value="POST">Write</option>
                                </>
                              ) : ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m => <option key={m} value={m}>{m}</option>)}
                            </select>
                          </div>
                        )}
                        <div style={{ gridColumn: '1 / -1' }}>
                          <label className="n-label">Description</label>
                          <input
                            type="text"
                            value={op.description || ''}
                            onChange={e => updateTool(idx, 'description', e.target.value || undefined)}
                            placeholder={isEndpoint
                              ? 'What it returns — for whoever writes a consolidator'
                              : 'What it does — the LLM reads this to decide when to call it'}
                            className="n-input"
                            style={fieldStyle}
                          />
                        </div>
                        {showHttp && (
                          <>
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
                          </>
                        )}
                      </div>
                    </>
                  )}

                  <FieldsEditor
                    op={op}
                    onPatch={patch}
                    withMapping={showHttp}
                    note={!isEndpoint
                      ? 'What the LLM passes; it reads each description.'
                      : isMcp
                        ? 'From the MCP server’s input schema.'
                        : 'What a caller passes, and the API parameter each fills.'}
                  />

                  {showHttp && (
                    <>
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
                        <label className="n-label">Request body template (JSON)</label>
                        <AutoResizeTextarea
                          value={op.request_body_template || ''}
                          onChange={e => updateTool(idx, 'request_body_template', e.target.value || null)}
                          placeholder={'{"name": "{{ name }}"}'}
                          style={codeFieldStyle}
                        />
                      </div>
                    </>
                  )}

                  {!isEndpoint && (
                    <>
                      <ToolEffectEditor op={op} onPatch={patch} claimed={!!inv?.app} />
                      <ToolDisplayEditor op={op} onPatch={patch} />
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
            {tryRows.length > 0 && !isNew && (
              <div style={{
                border: '1px solid var(--line)',
                borderRadius: 'var(--radius)',
                padding: '14px 16px',
                marginTop: 16,
                backgroundColor: 'var(--surface)',
              }}>
                <h5 style={{ margin: '0 0 4px', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>Try an endpoint</h5>
                <p style={{ ...sectionNote, margin: '0 0 10px' }}>
                  Runs the saved spec — press Update spec first if you changed the endpoint.
                  {form.execution_mode === 'template' ? ' Render shows the request without sending it.' : ''}
                </p>
                <div style={{ marginBottom: '0.5rem' }}>
                  <label className="n-label" htmlFor="try-endpoint">Endpoint</label>
                  <select
                    id="try-endpoint"
                    value={tryToolAction}
                    onChange={e => {
                      const action = e.target.value;
                      setTryToolAction(action);
                      setTryDryRunResult(null);
                      setTryTestResult(null);
                      setTryError(null);
                      // Pre-fill each field with the example in its hint, e.g. "(e.g., 2026-03-16)".
                      const row = tryRows.find(t => t.action === action);
                      const fields: Record<string, string> = {};
                      for (const key of row ? rowFields(row) : []) {
                        const exampleMatch = (row?.field_descriptions?.[key] || '').match(/\(e\.g\.?,?\s*(.+?)\)\s*$/);
                        fields[key] = exampleMatch ? exampleMatch[1].trim() : '';
                      }
                      setTryToolFields(fields);
                    }}
                    className="n-select"
                    style={fieldStyle}
                  >
                    <option value="">Select an endpoint…</option>
                    {tryRows.map((t, i) => (
                      <option key={`${t.action}-${i}`} value={t.action}>
                        {t.action}{form.execution_mode === 'template' ? ` — ${t.method}` : isWriteRow(t) ? ' — write' : ''}
                      </option>
                    ))}
                  </select>
                </div>

                {venues.length > 0 && (
                  <div style={{ marginBottom: '0.5rem' }}>
                    <label className="n-label" htmlFor="try-venue">Venue</label>
                    <select id="try-venue" value={tryVenueId} onChange={e => setTryVenueId(e.target.value)} className="n-select" style={fieldStyle}>
                      <option value="">First connected venue</option>
                      {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
                    </select>
                  </div>
                )}

                {selectedTool && (
                  <>
                    <div style={{ marginBottom: '0.5rem' }}>
                      <label className="n-label" style={{ marginBottom: 6 }}>Fields</label>
                      {rowFields(selectedTool).length === 0 && (
                        <p style={emptyNote}>This endpoint takes no fields.</p>
                      )}
                      {rowFields(selectedTool).map(fieldKey => (
                        <div key={fieldKey} style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '4px 8px', marginBottom: 6 }}>
                          <label className="n-label" style={{ width: 130, flexShrink: 0, marginBottom: 0, overflowWrap: 'anywhere' }}>
                            {fieldKey}
                            {selectedTool.required_fields.includes(fieldKey) && (
                              <span style={{ color: 'var(--error)', marginLeft: 2 }}>*</span>
                            )}
                          </label>
                          <input
                            type="text"
                            aria-label={fieldKey}
                            value={tryToolFields[fieldKey] ?? ''}
                            onChange={e => setTryToolFields(prev => ({ ...prev, [fieldKey]: e.target.value }))}
                            placeholder={selectedTool.field_descriptions?.[fieldKey] || selectedTool.field_mapping?.[fieldKey] || fieldKey}
                            className="n-input"
                            style={{ flex: '1 1 200px', minWidth: 0 }}
                          />
                        </div>
                      ))}
                    </div>

                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      {form.execution_mode === 'template' && (
                        <Button
                          size="sm"
                          onClick={() => handleTryRun('render')}
                          disabled={tryLoading !== null}
                        >
                          {tryLoading === 'render' ? 'Rendering…' : 'Render'}
                        </Button>
                      )}
                      <Button
                        variant={isWriteRow(selectedTool) ? 'danger' : 'primary'}
                        size="sm"
                        onClick={() => { if (isWriteRow(selectedTool)) setConfirmLiveWrite(true); else void handleTryRun('test'); }}
                        disabled={tryLoading !== null}
                      >
                        {tryLoading === 'test' ? 'Calling…' : 'Call it (live)'}
                      </Button>
                      <span style={{ fontSize: 'var(--fs-xs)', color: isWriteRow(selectedTool) ? 'var(--error)' : 'var(--muted)' }}>
                        {isWriteRow(selectedTool)
                          ? `A write: this changes data in ${form.display_name || 'the outside system'}`
                          : `A real call to ${form.display_name || 'the outside system'}`}
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
