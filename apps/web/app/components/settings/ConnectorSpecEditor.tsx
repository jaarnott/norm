'use client';

import { useState, useMemo, useEffect, useRef } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';
import type { ConnectorSpecFull, ConnectorSpecTool } from '../../types';
import { apiFetch } from '../../lib/api';

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
  request_body_template: null,
  success_status_codes: [200, 201],
  response_ref_path: null,
  timeout_seconds: 30,
  display_component: null,
  display_props: null,
  working_document: null,
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
        } catch {
          setValid(false);
        }
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

export default function ConnectorSpecEditor({ spec, isNew, onSave, onCancel }: Props) {
  const [form, setForm] = useState<ConnectorSpecFull>(spec ?? { ...EMPTY_SPEC });
  const [saving, setSaving] = useState(false);
  const [collapsedTools, setCollapsedTools] = useState<Set<number>>(() => new Set(form.tools.map((_, i) => i)));

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
              onChange={e => update('execution_mode', e.target.value as 'template' | 'agent')}
              style={inputStyle}
            >
              <option value="template">Template</option>
              <option value="agent">Agent</option>
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
            onChange={v => update('auth_config', v ?? {})}
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
                onChange={e => update('test_request', { ...form.test_request, method: e.target.value })}
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
                onChange={e => update('test_request', { ...form.test_request, path_template: e.target.value })}
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
                })}
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
          <button onClick={addTool} style={{
            padding: '3px 10px', fontSize: '0.75rem', border: '1px solid #ddd', borderRadius: 4,
            backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit',
          }}>
            + Add Tool
          </button>
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
                {form.tools.map(t => (
                  <option key={t.action} value={t.action}>
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

                {tryTestResult && (
                  <div style={{ marginTop: '0.5rem' }}>
                    <div style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      marginBottom: '0.4rem',
                    }}>
                      <span style={{
                        fontSize: '0.72rem',
                        fontWeight: 600,
                        padding: '2px 8px',
                        borderRadius: 10,
                        backgroundColor: tryTestResult.success ? '#d4edda' : '#f8d7da',
                        color: tryTestResult.success ? '#155724' : '#721c24',
                      }}>
                        {tryTestResult.success ? 'SUCCESS' : 'FAILED'}
                      </span>
                      {'error' in tryTestResult && Boolean(tryTestResult.error) && (
                        <span style={{ fontSize: '0.78rem', color: '#e53e3e' }}>
                          {String(tryTestResult.error)}
                        </span>
                      )}
                    </div>
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
                      {JSON.stringify(tryTestResult, null, 2)}
                    </pre>
                  </div>
                )}
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
              draggable
              onDragStart={() => setDragIdx(idx)}
              onDragOver={e => { e.preventDefault(); setDragOverIdx(idx); }}
              onDragEnd={handleDragEnd}
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
                    onMouseDown={e => e.stopPropagation()}
                    style={{ cursor: 'grab', color: '#ccc', fontSize: '0.85rem', lineHeight: 1, padding: '0 2px', userSelect: 'none' }}
                    title="Drag to reorder"
                  >&#8942;&#8942;</span>
                  {isCollapsed
                    ? <ChevronRight size={14} strokeWidth={2} style={{ color: '#999' }} />
                    : <ChevronDown size={14} strokeWidth={2} style={{ color: '#999' }} />
                  }
                  <span style={{ fontWeight: 500, fontSize: '0.85rem', color: '#444' }}>
                    {op.action || `Tool ${idx + 1}`}
                  </span>
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
                        value={op.success_status_codes.join(', ')}
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
                        value={op.timeout_seconds}
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
                          while (newKey in op.field_mapping) { newKey = `new_field_${suffix++}`; }
                          const updated = { ...op.field_mapping, [newKey]: '' };
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
                    {Object.keys(op.field_mapping).length === 0 && (
                      <p style={{ color: '#999', fontSize: '0.78rem', margin: '0 0 0.25rem', fontStyle: 'italic' }}>
                        No fields defined. Add fields to map source data to API parameters.
                      </p>
                    )}
                    {Object.keys(op.field_mapping).length > 0 && (
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
                        {Object.entries(op.field_mapping).map(([fieldKey, apiMapping], fieldIdx) => (
                          <div
                            key={fieldIdx}
                            style={{
                              display: 'grid',
                              gridTemplateColumns: '1fr 1fr 1.5fr 70px 32px',
                              gap: 0,
                              alignItems: 'center',
                              padding: '4px 10px',
                              borderBottom: fieldIdx < Object.keys(op.field_mapping).length - 1 ? '1px solid #f0f0f0' : 'none',
                            }}
                          >
                            <input
                              type="text"
                              value={fieldKey}
                              onChange={e => {
                                const newKey = e.target.value;
                                const entries = Object.entries(op.field_mapping);
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
                                const entries = Object.entries(op.field_mapping);
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
                                const newMapping = { ...op.field_mapping };
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
              onChange={v => update('example_requests', v ?? [])}
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
