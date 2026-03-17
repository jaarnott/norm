'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import type { AgentConfig, AgentBinding } from '../../types';
import ConnectorSpecsPanel from './ConnectorSpecsPanel';

interface ConnectorField {
  key: string;
  label: string;
  secret: boolean;
}

interface ConnectorMeta {
  name: string;
  label: string;
  domain: string;
  fields: ConnectorField[];
  auth_type?: string;
  configured: boolean;
  enabled: boolean;
  config: Record<string, string>;
  oauth_connected?: boolean;
}

type TestStatus = 'idle' | 'testing' | 'success' | 'error';
type SettingsTab = 'connectors' | 'agents' | 'specs';

export default function SettingsPanel() {
  const [activeTab, setActiveTab] = useState<SettingsTab>('connectors');

  // --- Connector state ---
  const [connectors, setConnectors] = useState<ConnectorMeta[]>([]);
  const [forms, setForms] = useState<Record<string, Record<string, string>>>({});
  const [testStatus, setTestStatus] = useState<Record<string, TestStatus>>({});
  const [testMessage, setTestMessage] = useState<Record<string, string>>({});
  const [testDetail, setTestDetail] = useState<Record<string, { rendered_request?: Record<string, unknown>; response?: Record<string, unknown> } | null>>({});
  const [saving, setSaving] = useState<Record<string, boolean>>({});

  // --- Agent state ---
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [agentForms, setAgentForms] = useState<Record<string, { system_prompt: string; description: string }>>({});
  const [agentSaving, setAgentSaving] = useState<Record<string, boolean>>({});

  const fetchConnectors = useCallback(async () => {
    try {
      const res = await apiFetch('/api/connectors');
      if (!res.ok) return;
      const data = await res.json();
      setConnectors(data.connectors);
      const initialForms: Record<string, Record<string, string>> = {};
      for (const c of data.connectors) {
        initialForms[c.name] = { ...c.config };
      }
      setForms(initialForms);
    } catch {
      // ignore
    }
  }, []);

  const fetchAgents = useCallback(async () => {
    try {
      const res = await apiFetch('/api/agents');
      if (!res.ok) return;
      const data = await res.json();
      setAgents(data.agents);
      const initialForms: Record<string, { system_prompt: string; description: string }> = {};
      for (const a of data.agents) {
        initialForms[a.slug] = {
          system_prompt: a.system_prompt || '',
          description: a.description || '',
        };
      }
      setAgentForms(initialForms);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => { fetchConnectors(); }, [fetchConnectors]);
  useEffect(() => { if (activeTab === 'agents') fetchAgents(); }, [activeTab, fetchAgents]);

  // --- Connector handlers ---
  const updateField = (connector: string, key: string, value: string) => {
    setForms(prev => ({
      ...prev,
      [connector]: { ...prev[connector], [key]: value },
    }));
  };

  const handleTest = async (name: string) => {
    setTestStatus(prev => ({ ...prev, [name]: 'testing' }));
    setTestMessage(prev => ({ ...prev, [name]: '' }));
    setTestDetail(prev => ({ ...prev, [name]: null }));
    try {
      const res = await apiFetch(`/api/connectors/${name}/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: forms[name] || {} }),
      });
      const data = await res.json();
      const detail = { rendered_request: data.rendered_request, response: data.response };
      if (data.success) {
        setTestStatus(prev => ({ ...prev, [name]: 'success' }));
        setTestMessage(prev => ({ ...prev, [name]: data.message || 'Connected' }));
      } else {
        setTestStatus(prev => ({ ...prev, [name]: 'error' }));
        setTestMessage(prev => ({ ...prev, [name]: data.error || 'Test failed' }));
      }
      setTestDetail(prev => ({ ...prev, [name]: detail }));
    } catch {
      setTestStatus(prev => ({ ...prev, [name]: 'error' }));
      setTestMessage(prev => ({ ...prev, [name]: 'Network error' }));
    }
  };

  const handleSave = async (name: string) => {
    setSaving(prev => ({ ...prev, [name]: true }));
    try {
      const res = await apiFetch(`/api/connectors/${name}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: forms[name] || {}, enabled: true }),
      });
      if (res.ok) {
        await fetchConnectors();
      }
    } catch {
      // ignore
    } finally {
      setSaving(prev => ({ ...prev, [name]: false }));
    }
  };

  const handleToggleEnabled = async (name: string) => {
    try {
      const res = await apiFetch(`/api/connectors/${name}/toggle`, { method: 'PATCH' });
      if (res.ok) {
        await fetchConnectors();
      }
    } catch {
      // ignore
    }
  };

  const handleDelete = async (name: string) => {
    try {
      await apiFetch(`/api/connectors/${name}`, { method: 'DELETE' });
      await fetchConnectors();
      setTestStatus(prev => ({ ...prev, [name]: 'idle' }));
      setTestMessage(prev => ({ ...prev, [name]: '' }));
    } catch {
      // ignore
    }
  };

  const handleOAuthConnect = async (name: string) => {
    try {
      const res = await apiFetch(`/api/oauth/authorize/${name}`);
      if (!res.ok) {
        let errMsg: string;
        try {
          const errJson = await res.json();
          errMsg = errJson.detail || JSON.stringify(errJson);
        } catch {
          errMsg = await res.text();
        }
        setTestStatus(prev => ({ ...prev, [name]: 'error' }));
        setTestMessage(prev => ({ ...prev, [name]: `OAuth error: ${errMsg}` }));
        return;
      }
      const data = await res.json();
      // Open the authorize URL in a popup
      const popup = window.open(data.authorize_url, `oauth_${name}`, 'width=600,height=700');
      // Listen for the callback message from the popup
      const handler = (event: MessageEvent) => {
        if (event.data?.type === 'oauth-complete') {
          window.removeEventListener('message', handler);
          fetchConnectors();
          if (event.data.success) {
            setTestStatus(prev => ({ ...prev, [name]: 'success' }));
            setTestMessage(prev => ({ ...prev, [name]: 'OAuth connected successfully' }));
          } else {
            setTestStatus(prev => ({ ...prev, [name]: 'error' }));
            setTestMessage(prev => ({ ...prev, [name]: 'OAuth connection failed' }));
          }
        }
      };
      window.addEventListener('message', handler);
      // Clean up listener after 5 minutes if popup closes without completing
      setTimeout(() => {
        window.removeEventListener('message', handler);
        if (popup && popup.closed) fetchConnectors();
      }, 300000);
    } catch {
      setTestStatus(prev => ({ ...prev, [name]: 'error' }));
      setTestMessage(prev => ({ ...prev, [name]: 'Failed to start OAuth flow' }));
    }
  };

  const handleOAuthDisconnect = async (name: string) => {
    try {
      await apiFetch(`/api/oauth/disconnect/${name}`, { method: 'POST' });
      await fetchConnectors();
      setTestStatus(prev => ({ ...prev, [name]: 'idle' }));
      setTestMessage(prev => ({ ...prev, [name]: '' }));
    } catch {
      // ignore
    }
  };

  // --- Agent handlers ---
  const updateAgentField = (slug: string, key: 'system_prompt' | 'description', value: string) => {
    setAgentForms(prev => ({
      ...prev,
      [slug]: { ...prev[slug], [key]: value },
    }));
  };

  const handleAgentSave = async (slug: string) => {
    setAgentSaving(prev => ({ ...prev, [slug]: true }));
    try {
      const form = agentForms[slug];
      const res = await apiFetch(`/api/agents/${slug}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          system_prompt: form?.system_prompt || null,
          description: form?.description || null,
        }),
      });
      if (res.ok) {
        await fetchAgents();
      }
    } catch {
      // ignore
    } finally {
      setAgentSaving(prev => ({ ...prev, [slug]: false }));
    }
  };

  const handleAgentReset = async (slug: string) => {
    try {
      const res = await apiFetch(`/api/agents/${slug}/reset-prompt`, { method: 'POST' });
      if (res.ok) {
        await fetchAgents();
      }
    } catch {
      // ignore
    }
  };

  const handleToggleCapability = async (slug: string, binding: AgentBinding, capIndex: number) => {
    const updated = binding.capabilities.map((c, i) =>
      i === capIndex ? { ...c, enabled: !c.enabled } : c
    );
    try {
      await apiFetch(`/api/agents/${slug}/bindings/${binding.connector_name}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capabilities: updated, enabled: binding.enabled }),
      });
      await fetchAgents();
    } catch {
      // ignore
    }
  };

  const handleDeleteBinding = async (slug: string, connectorName: string) => {
    try {
      await apiFetch(`/api/agents/${slug}/bindings/${connectorName}`, { method: 'DELETE' });
      await fetchAgents();
    } catch {
      // ignore
    }
  };

  const handleAddConnector = async (slug: string, connectorName: string) => {
    try {
      await apiFetch(`/api/agents/${slug}/bindings/${connectorName}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capabilities: [], enabled: true }),
      });
      await fetchAgents();
    } catch {
      // ignore
    }
  };

  const statusColor = (s: TestStatus) => {
    switch (s) {
      case 'testing': return '#c4a882';
      case 'success': return '#38a169';
      case 'error': return '#e53e3e';
      default: return '#999';
    }
  };

  const tabStyle = (tab: SettingsTab): React.CSSProperties => ({
    padding: '6px 16px',
    fontSize: '0.82rem',
    fontWeight: 500,
    border: 'none',
    borderBottom: activeTab === tab ? '2px solid #c4a882' : '2px solid transparent',
    backgroundColor: 'transparent',
    color: activeTab === tab ? '#c4a882' : '#666',
    cursor: 'pointer',
    fontFamily: 'inherit',
  });

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <div style={{
        padding: '1.25rem 1.5rem',
        borderBottom: '1px solid #eee',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
      }}>
        <span style={{ fontSize: '1.1rem' }}>&#9881;</span>
        <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>Settings</h2>
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 4, padding: '0 1.5rem', borderBottom: '1px solid #eee' }}>
        <button onClick={() => setActiveTab('connectors')} style={tabStyle('connectors')}>Connectors</button>
        <button onClick={() => setActiveTab('agents')} style={tabStyle('agents')}>Agents</button>
        <button onClick={() => setActiveTab('specs')} style={tabStyle('specs')}>Connector Specs</button>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '1.5rem' }}>
        {/* ============ CONNECTORS TAB ============ */}
        {activeTab === 'connectors' && (
          <>
            <h3 style={{ margin: '0 0 1rem', fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Connectors
            </h3>
            <div style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              marginBottom: '1rem', padding: '0.6rem 0.75rem',
              border: '1px solid #edf2f7', borderRadius: 8, backgroundColor: '#fafafa',
            }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem', color: '#555', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={(() => { try { return localStorage.getItem('norm_show_tool_details') !== 'false'; } catch { return true; } })()}
                  onChange={e => {
                    localStorage.setItem('norm_show_tool_details', String(e.target.checked));
                    // Force re-render
                    setConnectors(c => [...c]);
                  }}
                  style={{ cursor: 'pointer' }}
                />
                Show tool call details in conversations
              </label>
              <span style={{ fontSize: '0.72rem', color: '#999' }}>
                Toggle request/response cards in the chat view
              </span>
            </div>

            {connectors.map(c => {
              const status = testStatus[c.name] || 'idle';
              return (
                <div key={c.name} style={{
                  border: '1px solid #e2e8f0',
                  borderRadius: 10,
                  padding: '1.25rem',
                  marginBottom: '1rem',
                  backgroundColor: '#fff',
                  opacity: c.configured && !c.enabled ? 0.6 : 1,
                  transition: 'opacity 0.2s',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>{c.label}</span>
                      {c.configured && (
                        <span style={{
                          fontSize: '0.7rem',
                          backgroundColor: '#e6fffa',
                          color: '#234e52',
                          padding: '2px 8px',
                          borderRadius: 10,
                          fontWeight: 500,
                        }}>
                          Configured
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      {c.configured && (
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: '0.78rem', color: '#555' }}>
                          <div
                            onClick={() => handleToggleEnabled(c.name)}
                            style={{
                              width: 34,
                              height: 18,
                              borderRadius: 9,
                              backgroundColor: c.enabled ? '#38a169' : '#cbd5e0',
                              position: 'relative',
                              cursor: 'pointer',
                              transition: 'background-color 0.2s',
                            }}
                          >
                            <div style={{
                              width: 14,
                              height: 14,
                              borderRadius: '50%',
                              backgroundColor: '#fff',
                              position: 'absolute',
                              top: 2,
                              left: c.enabled ? 18 : 2,
                              transition: 'left 0.2s',
                              boxShadow: '0 1px 2px rgba(0,0,0,0.2)',
                            }} />
                          </div>
                          {c.enabled ? 'Active' : 'Inactive'}
                        </label>
                      )}
                      <span style={{ fontSize: '0.75rem', color: '#999' }}>{c.domain}</span>
                    </div>
                  </div>

                  {/* OAuth2 connectors: show Connect button instead of manual fields */}
                  {c.auth_type === 'oauth2' ? (
                    <>
                      {c.oauth_connected && (
                        <div style={{
                          fontSize: '0.78rem',
                          color: '#38a169',
                          marginBottom: '0.75rem',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 6,
                        }}>
                          <span style={{
                            width: 8,
                            height: 8,
                            borderRadius: '50%',
                            backgroundColor: '#38a169',
                            display: 'inline-block',
                          }} />
                          OAuth connected
                        </div>
                      )}

                      {/* Still show non-secret credential fields (e.g. subdomain) */}
                      {c.fields.filter(f => !f.secret).map(f => (
                        <div key={f.key} style={{ marginBottom: '0.75rem' }}>
                          <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                            {f.label}
                          </label>
                          <input
                            type="text"
                            value={forms[c.name]?.[f.key] || ''}
                            onChange={e => updateField(c.name, f.key, e.target.value)}
                            placeholder={`Enter ${f.label.toLowerCase()}`}
                            style={{
                              width: '100%',
                              padding: '8px 10px',
                              border: '1px solid #ddd',
                              borderRadius: 6,
                              fontSize: '0.85rem',
                              fontFamily: 'inherit',
                              boxSizing: 'border-box',
                              outline: 'none',
                            }}
                          />
                        </div>
                      ))}
                    </>
                  ) : (
                    <>
                      {c.fields.map(f => (
                        <div key={f.key} style={{ marginBottom: '0.75rem' }}>
                          <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                            {f.label}
                          </label>
                          <input
                            type={f.secret ? 'password' : 'text'}
                            value={forms[c.name]?.[f.key] || ''}
                            onChange={e => updateField(c.name, f.key, e.target.value)}
                            placeholder={f.secret ? '••••••••' : `Enter ${f.label.toLowerCase()}`}
                            style={{
                              width: '100%',
                              padding: '8px 10px',
                              border: '1px solid #ddd',
                              borderRadius: 6,
                              fontSize: '0.85rem',
                              fontFamily: 'inherit',
                              boxSizing: 'border-box',
                              outline: 'none',
                            }}
                          />
                        </div>
                      ))}
                    </>
                  )}

                  {status !== 'idle' && (
                    <div style={{
                      fontSize: '0.78rem',
                      color: statusColor(status),
                      marginBottom: '0.75rem',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                    }}>
                      <span style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        backgroundColor: statusColor(status),
                        display: 'inline-block',
                      }} />
                      {status === 'testing' ? 'Testing connection...' : testMessage[c.name]}
                    </div>
                  )}

                  {testDetail[c.name] && (status === 'success' || status === 'error') && (
                    <details style={{ marginBottom: '0.75rem', fontSize: '0.78rem' }}>
                      <summary style={{ cursor: 'pointer', color: '#666', marginBottom: '0.4rem' }}>
                        Show request &amp; response
                      </summary>
                      {testDetail[c.name]?.rendered_request && (
                        <div style={{ marginBottom: '0.4rem' }}>
                          <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.03em', marginBottom: 3 }}>Request</div>
                          <pre style={{
                            padding: '0.5rem', backgroundColor: '#1a202c', color: '#e2e8f0',
                            borderRadius: 6, fontSize: '0.75rem', overflow: 'auto', lineHeight: 1.4, margin: 0, maxHeight: 200,
                          }}>
                            {JSON.stringify(testDetail[c.name]?.rendered_request, null, 2)}
                          </pre>
                        </div>
                      )}
                      {testDetail[c.name]?.response && (
                        <div>
                          <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.03em', marginBottom: 3 }}>Response</div>
                          <pre style={{
                            padding: '0.5rem', backgroundColor: '#1a202c', color: '#e2e8f0',
                            borderRadius: 6, fontSize: '0.75rem', overflow: 'auto', lineHeight: 1.4, margin: 0, maxHeight: 200,
                          }}>
                            {JSON.stringify(testDetail[c.name]?.response, null, 2)}
                          </pre>
                        </div>
                      )}
                    </details>
                  )}

                  <div style={{ display: 'flex', gap: 8 }}>
                    {c.auth_type === 'oauth2' ? (
                      <>
                        {!c.oauth_connected ? (
                          <button
                            onClick={() => handleOAuthConnect(c.name)}
                            style={{
                              padding: '6px 14px',
                              fontSize: '0.8rem',
                              fontWeight: 500,
                              border: 'none',
                              borderRadius: 6,
                              backgroundColor: '#c4a882',
                              color: '#fff',
                              cursor: 'pointer',
                              fontFamily: 'inherit',
                            }}
                          >
                            Connect with OAuth
                          </button>
                        ) : (
                          <button
                            onClick={() => handleOAuthDisconnect(c.name)}
                            style={{
                              padding: '6px 14px',
                              fontSize: '0.8rem',
                              fontWeight: 500,
                              border: '1px solid #e53e3e',
                              borderRadius: 6,
                              backgroundColor: '#fff',
                              color: '#e53e3e',
                              cursor: 'pointer',
                              fontFamily: 'inherit',
                            }}
                          >
                            Disconnect
                          </button>
                        )}
                        {/* Save non-secret fields if any exist */}
                        {c.fields.some(f => !f.secret) && (
                          <button
                            onClick={() => handleSave(c.name)}
                            disabled={saving[c.name]}
                            style={{
                              padding: '6px 14px',
                              fontSize: '0.8rem',
                              fontWeight: 500,
                              border: '1px solid #ddd',
                              borderRadius: 6,
                              backgroundColor: '#fff',
                              cursor: saving[c.name] ? 'not-allowed' : 'pointer',
                              fontFamily: 'inherit',
                            }}
                          >
                            {saving[c.name] ? 'Saving...' : 'Save'}
                          </button>
                        )}
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => handleTest(c.name)}
                          disabled={status === 'testing'}
                          style={{
                            padding: '6px 14px',
                            fontSize: '0.8rem',
                            fontWeight: 500,
                            border: '1px solid #ddd',
                            borderRadius: 6,
                            backgroundColor: '#fff',
                            cursor: status === 'testing' ? 'not-allowed' : 'pointer',
                            fontFamily: 'inherit',
                          }}
                        >
                          Test
                        </button>
                        <button
                          onClick={() => handleSave(c.name)}
                          disabled={saving[c.name]}
                          style={{
                            padding: '6px 14px',
                            fontSize: '0.8rem',
                            fontWeight: 500,
                            border: 'none',
                            borderRadius: 6,
                            backgroundColor: '#c4a882',
                            color: '#fff',
                            cursor: saving[c.name] ? 'not-allowed' : 'pointer',
                            fontFamily: 'inherit',
                          }}
                        >
                          {saving[c.name] ? 'Saving...' : 'Save'}
                        </button>
                        {c.configured && (
                          <button
                            onClick={() => handleDelete(c.name)}
                            style={{
                              padding: '6px 14px',
                              fontSize: '0.8rem',
                              fontWeight: 500,
                              border: '1px solid #e53e3e',
                              borderRadius: 6,
                              backgroundColor: '#fff',
                              color: '#e53e3e',
                              cursor: 'pointer',
                              fontFamily: 'inherit',
                            }}
                          >
                            Remove
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </>
        )}

        {/* ============ CONNECTOR SPECS TAB ============ */}
        {activeTab === 'specs' && <ConnectorSpecsPanel />}

        {/* ============ AGENTS TAB ============ */}
        {activeTab === 'agents' && (
          <>
            <h3 style={{ margin: '0 0 1rem', fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Agent Configuration
            </h3>

            {agents.map(agent => (
              <div key={agent.slug} style={{
                border: '1px solid #e2e8f0',
                borderRadius: 10,
                padding: '1.25rem',
                marginBottom: '1rem',
                backgroundColor: '#fff',
              }}>
                {/* Header */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>{agent.display_name}</span>
                    <span style={{ fontSize: '0.75rem', color: '#999' }}>{agent.slug}</span>
                    <span style={{
                      fontSize: '0.7rem',
                      backgroundColor: agent.is_custom_prompt ? '#fef3c7' : '#e6fffa',
                      color: agent.is_custom_prompt ? '#92400e' : '#234e52',
                      padding: '2px 8px',
                      borderRadius: 10,
                      fontWeight: 500,
                    }}>
                      {agent.is_custom_prompt ? 'Custom' : 'Default'}
                    </span>
                  </div>
                </div>

                {/* Description */}
                <div style={{ marginBottom: '0.75rem' }}>
                  <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                    Description
                  </label>
                  <input
                    type="text"
                    value={agentForms[agent.slug]?.description || ''}
                    onChange={e => updateAgentField(agent.slug, 'description', e.target.value)}
                    placeholder="What this agent does..."
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      border: '1px solid #ddd',
                      borderRadius: 6,
                      fontSize: '0.85rem',
                      fontFamily: 'inherit',
                      boxSizing: 'border-box',
                      outline: 'none',
                    }}
                  />
                </div>

                {/* System Prompt */}
                <div style={{ marginBottom: '0.75rem' }}>
                  <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                    System Prompt
                  </label>
                  <textarea
                    value={agentForms[agent.slug]?.system_prompt || ''}
                    onChange={e => updateAgentField(agent.slug, 'system_prompt', e.target.value)}
                    rows={15}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      border: '1px solid #ddd',
                      borderRadius: 6,
                      fontSize: '0.82rem',
                      fontFamily: 'monospace',
                      boxSizing: 'border-box',
                      outline: 'none',
                      resize: 'vertical',
                      lineHeight: 1.5,
                    }}
                  />
                </div>

                {/* Connector Bindings */}
                {agent.bindings.length > 0 && (
                  <div style={{ marginBottom: '0.75rem' }}>
                    <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 8 }}>
                      Connector Bindings
                    </label>
                    {agent.bindings.map(binding => (
                      <div key={binding.connector_name} style={{
                        border: '1px solid #edf2f7',
                        borderRadius: 8,
                        padding: '0.75rem',
                        marginBottom: '0.5rem',
                        backgroundColor: '#fafafa',
                      }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                          <span style={{ fontWeight: 500, fontSize: '0.85rem' }}>{binding.connector_label}</span>
                          <button
                            onClick={() => handleDeleteBinding(agent.slug, binding.connector_name)}
                            style={{
                              padding: '2px 8px',
                              fontSize: '0.72rem',
                              border: '1px solid #e53e3e',
                              borderRadius: 4,
                              backgroundColor: '#fff',
                              color: '#e53e3e',
                              cursor: 'pointer',
                              fontFamily: 'inherit',
                            }}
                          >
                            Remove
                          </button>
                        </div>
                        {binding.capabilities.map((cap, idx) => (
                          <label key={`${binding.connector_name}__${cap.action}__${idx}`} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.8rem', color: '#444', cursor: 'pointer', marginBottom: 2 }}>
                            <input
                              type="checkbox"
                              checked={cap.enabled}
                              onChange={() => handleToggleCapability(agent.slug, binding, idx)}
                            />
                            {cap.label}
                          </label>
                        ))}
                      </div>
                    ))}
                  </div>
                )}

                {/* Add Connector */}
                {agent.available_connectors && agent.available_connectors.length > 0 && (
                  <div style={{ marginBottom: '0.75rem' }}>
                    <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                      Add Connector
                    </label>
                    <select
                      defaultValue=""
                      onChange={e => {
                        if (e.target.value) {
                          handleAddConnector(agent.slug, e.target.value);
                          e.target.value = '';
                        }
                      }}
                      style={{
                        padding: '6px 10px',
                        fontSize: '0.82rem',
                        border: '1px solid #ddd',
                        borderRadius: 6,
                        fontFamily: 'inherit',
                        backgroundColor: '#fff',
                        cursor: 'pointer',
                        outline: 'none',
                      }}
                    >
                      <option value="" disabled>Select a connector...</option>
                      {agent.available_connectors.map(ac => (
                        <option key={ac.connector_name} value={ac.connector_name}>
                          {ac.display_name}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                {/* Actions */}
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    onClick={() => handleAgentSave(agent.slug)}
                    disabled={agentSaving[agent.slug]}
                    style={{
                      padding: '6px 14px',
                      fontSize: '0.8rem',
                      fontWeight: 500,
                      border: 'none',
                      borderRadius: 6,
                      backgroundColor: '#c4a882',
                      color: '#fff',
                      cursor: agentSaving[agent.slug] ? 'not-allowed' : 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    {agentSaving[agent.slug] ? 'Saving...' : 'Save'}
                  </button>
                  {agent.is_custom_prompt && (
                    <button
                      onClick={() => handleAgentReset(agent.slug)}
                      style={{
                        padding: '6px 14px',
                        fontSize: '0.8rem',
                        fontWeight: 500,
                        border: '1px solid #ddd',
                        borderRadius: 6,
                        backgroundColor: '#fff',
                        cursor: 'pointer',
                        fontFamily: 'inherit',
                      }}
                    >
                      Reset to Default
                    </button>
                  )}
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
