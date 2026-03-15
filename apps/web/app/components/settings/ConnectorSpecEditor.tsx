'use client';

import { useState } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';
import type { ConnectorSpecFull, ConnectorSpecOperation } from '../../types';

interface Props {
  spec: ConnectorSpecFull | null;
  isNew: boolean;
  onSave: (spec: ConnectorSpecFull, isNew: boolean) => void;
  onCancel: () => void;
}

const EMPTY_OPERATION: ConnectorSpecOperation = {
  action: '',
  method: 'POST',
  path_template: '',
  headers: {},
  required_fields: [],
  field_mapping: {},
  request_body_template: null,
  success_status_codes: [200, 201],
  response_ref_path: null,
  timeout_seconds: 30,
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
  operations: [],
  api_documentation: null,
  example_requests: [],
  credential_fields: [],
  oauth_config: null,
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

export default function ConnectorSpecEditor({ spec, isNew, onSave, onCancel }: Props) {
  const [form, setForm] = useState<ConnectorSpecFull>(spec ?? { ...EMPTY_SPEC });
  const [saving, setSaving] = useState(false);
  const [collapsedOps, setCollapsedOps] = useState<Set<number>>(() => new Set(form.operations.map((_, i) => i)));

  const toggleOp = (index: number) => {
    setCollapsedOps(prev => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index); else next.add(index);
      return next;
    });
  };

  const update = <K extends keyof ConnectorSpecFull>(key: K, value: ConnectorSpecFull[K]) => {
    setForm(prev => ({ ...prev, [key]: value }));
  };

  const updateOperation = (index: number, field: keyof ConnectorSpecOperation, value: unknown) => {
    setForm(prev => ({
      ...prev,
      operations: prev.operations.map((op, i) =>
        i === index ? { ...op, [field]: value } : op
      ),
    }));
  };

  const addOperation = () => {
    setForm(prev => ({
      ...prev,
      operations: [...prev.operations, { ...EMPTY_OPERATION }],
    }));
  };

  const removeOperation = (index: number) => {
    setForm(prev => ({
      ...prev,
      operations: prev.operations.filter((_, i) => i !== index),
    }));
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
          <div>
            <label style={labelStyle}>Enabled</label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.85rem', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={e => update('enabled', e.target.checked)}
              />
              Active
            </label>
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
            <label style={labelStyle}>Base URL Template</label>
            <input
              type="text"
              value={form.base_url_template || ''}
              onChange={e => update('base_url_template', e.target.value || null)}
              placeholder="https://api.example.com/v1"
              style={inputStyle}
            />
          </div>
        </div>
        <div style={{ marginTop: '0.75rem' }}>
          <label style={labelStyle}>Auth Config (JSON)</label>
          <textarea
            value={JSON.stringify(form.auth_config, null, 2)}
            onChange={e => {
              try { update('auth_config', JSON.parse(e.target.value)); } catch { /* ignore */ }
            }}
            rows={3}
            style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem', resize: 'vertical' }}
          />
        </div>
      </div>

      {/* OAuth Config (shown when auth_type is oauth2) */}
      {form.auth_type === 'oauth2' && (
        <div style={sectionStyle}>
          <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.82rem', fontWeight: 600, color: '#444' }}>OAuth 2.0 Configuration</h4>
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

      {/* Operations */}
      <div style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <h4 style={{ margin: 0, fontSize: '0.82rem', fontWeight: 600, color: '#444' }}>Operations</h4>
          <button onClick={addOperation} style={{
            padding: '3px 10px', fontSize: '0.75rem', border: '1px solid #ddd', borderRadius: 4,
            backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit',
          }}>
            + Add Operation
          </button>
        </div>
        {form.operations.map((op, idx) => {
          const isCollapsed = collapsedOps.has(idx);
          return (
            <div key={idx} style={{
              border: '1px solid #e2e8f0',
              borderRadius: 8,
              marginBottom: '0.5rem',
              backgroundColor: '#fff',
              overflow: 'hidden',
            }}>
              <div
                onClick={() => toggleOp(idx)}
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
                  {isCollapsed
                    ? <ChevronRight size={14} strokeWidth={2} style={{ color: '#999' }} />
                    : <ChevronDown size={14} strokeWidth={2} style={{ color: '#999' }} />
                  }
                  <span style={{ fontWeight: 500, fontSize: '0.85rem', color: '#444' }}>
                    {op.action || `Operation ${idx + 1}`}
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
                <button onClick={(e) => { e.stopPropagation(); removeOperation(idx); }} style={{
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
                        onChange={e => updateOperation(idx, 'action', e.target.value)}
                        placeholder="create_employee"
                        style={inputStyle}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Method</label>
                      <select
                        value={op.method}
                        onChange={e => updateOperation(idx, 'method', e.target.value)}
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
                      <label style={labelStyle}>Path Template</label>
                      <input
                        type="text"
                        value={op.path_template}
                        onChange={e => updateOperation(idx, 'path_template', e.target.value)}
                        placeholder="/api/v1/employees"
                        style={inputStyle}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Success Status Codes (comma-separated)</label>
                      <input
                        type="text"
                        value={op.success_status_codes.join(', ')}
                        onChange={e => updateOperation(idx, 'success_status_codes', e.target.value.split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n)))}
                        placeholder="200, 201"
                        style={inputStyle}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Response Ref Path</label>
                      <input
                        type="text"
                        value={op.response_ref_path || ''}
                        onChange={e => updateOperation(idx, 'response_ref_path', e.target.value || null)}
                        placeholder="body.id"
                        style={inputStyle}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Timeout (seconds)</label>
                      <input
                        type="number"
                        value={op.timeout_seconds}
                        onChange={e => updateOperation(idx, 'timeout_seconds', parseInt(e.target.value, 10) || 30)}
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
                          updateOperation(idx, 'field_mapping', updated);
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
                          gridTemplateColumns: '1fr 1fr 70px 32px',
                          gap: 0,
                          backgroundColor: '#f7f7f7',
                          padding: '6px 10px',
                          borderBottom: '1px solid #e2e8f0',
                        }}>
                          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Field Name</span>
                          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.03em' }}>API Mapping</span>
                          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.03em', textAlign: 'center' }}>Required</span>
                          <span />
                        </div>
                        {Object.entries(op.field_mapping).map(([fieldKey, apiMapping], fieldIdx) => (
                          <div
                            key={fieldIdx}
                            style={{
                              display: 'grid',
                              gridTemplateColumns: '1fr 1fr 70px 32px',
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
                                setForm(prev => ({
                                  ...prev,
                                  operations: prev.operations.map((o, i) =>
                                    i === idx ? { ...o, field_mapping: newMapping, required_fields: newRequired } : o
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
                                updateOperation(idx, 'field_mapping', Object.fromEntries(entries));
                              }}
                              placeholder="apiFieldName"
                              style={{ ...inputStyle, border: 'none', padding: '4px 6px', fontSize: '0.82rem', fontFamily: 'monospace', backgroundColor: 'transparent' }}
                            />
                            <div style={{ textAlign: 'center' }}>
                              <input
                                type="checkbox"
                                checked={op.required_fields.includes(fieldKey)}
                                onChange={e => {
                                  const newRequired = e.target.checked
                                    ? [...op.required_fields, fieldKey]
                                    : op.required_fields.filter(f => f !== fieldKey);
                                  updateOperation(idx, 'required_fields', newRequired);
                                }}
                                style={{ cursor: 'pointer' }}
                              />
                            </div>
                            <button
                              onClick={() => {
                                const newMapping = { ...op.field_mapping };
                                delete newMapping[fieldKey];
                                const newRequired = op.required_fields.filter(f => f !== fieldKey);
                                setForm(prev => ({
                                  ...prev,
                                  operations: prev.operations.map((o, i) =>
                                    i === idx ? { ...o, field_mapping: newMapping, required_fields: newRequired } : o
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
                    <textarea
                      value={JSON.stringify(op.headers, null, 2)}
                      onChange={e => {
                        try { updateOperation(idx, 'headers', JSON.parse(e.target.value)); } catch { /* ignore */ }
                      }}
                      rows={2}
                      style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem', resize: 'vertical' }}
                    />
                  </div>
                  <div style={{ marginTop: '0.5rem' }}>
                    <label style={labelStyle}>Request Body Template (JSON)</label>
                    <textarea
                      value={op.request_body_template || ''}
                      onChange={e => updateOperation(idx, 'request_body_template', e.target.value || null)}
                      rows={4}
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
            <textarea
              value={JSON.stringify(form.example_requests, null, 2)}
              onChange={e => {
                try { update('example_requests', JSON.parse(e.target.value)); } catch { /* ignore */ }
              }}
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
