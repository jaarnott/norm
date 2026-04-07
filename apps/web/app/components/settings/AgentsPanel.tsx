'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import type { AgentConfig, AgentBinding } from '../../types';

const labelStyle: React.CSSProperties = { fontSize: '0.75rem', fontWeight: 600, color: '#888', textTransform: 'uppercase' as const, marginBottom: 4, display: 'block' };
const inputStyle: React.CSSProperties = { width: '100%', padding: '6px 8px', border: '1px solid #ddd', borderRadius: 6, fontSize: '0.85rem', fontFamily: 'inherit', boxSizing: 'border-box' as const };

export default function AgentsPanel() {
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<AgentConfig | null>(null);
  const [form, setForm] = useState({ description: '', system_prompt: '' });
  const [saving, setSaving] = useState(false);

  const fetchAgents = useCallback(async () => {
    try {
      const res = await apiFetch('/api/agents');
      if (res.ok) {
        const data = await res.json();
        setAgents(data.agents || []);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchAgents(); }, [fetchAgents]);

  const openEdit = (agent: AgentConfig) => {
    setEditing(agent);
    setForm({ description: agent.description || '', system_prompt: agent.system_prompt || '' });
  };

  const handleSave = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/agents/${editing.slug}`, {
        method: 'PUT',
        body: JSON.stringify({ system_prompt: form.system_prompt || null, description: form.description || null }),
      });
      if (res.ok) {
        await fetchAgents();
        setEditing(null);
      }
    } catch { /* ignore */ }
    setSaving(false);
  };

  const handleReset = async () => {
    if (!editing) return;
    try {
      const res = await apiFetch(`/api/agents/${editing.slug}/reset-prompt`, { method: 'POST' });
      if (res.ok) {
        await fetchAgents();
        setEditing(null);
      }
    } catch { /* ignore */ }
  };

  const handleToggleCapability = async (binding: AgentBinding, capIndex: number) => {
    if (!editing) return;
    const updated = binding.capabilities.map((c, i) => i === capIndex ? { ...c, enabled: !c.enabled } : c);
    try {
      await apiFetch(`/api/agents/${editing.slug}/bindings/${binding.connector_name}`, {
        method: 'PUT',
        body: JSON.stringify({ capabilities: updated, enabled: binding.enabled }),
      });
      await fetchAgents();
      // Refresh editing state
      const res = await apiFetch(`/api/agents/${editing.slug}`);
      if (res.ok) { const d = await res.json(); setEditing(d); }
    } catch { /* ignore */ }
  };

  const handleDeleteBinding = async (connectorName: string) => {
    if (!editing) return;
    try {
      await apiFetch(`/api/agents/${editing.slug}/bindings/${connectorName}`, { method: 'DELETE' });
      await fetchAgents();
      const res = await apiFetch(`/api/agents/${editing.slug}`);
      if (res.ok) { const d = await res.json(); setEditing(d); }
    } catch { /* ignore */ }
  };

  const handleAddConnector = async (connectorName: string) => {
    if (!editing) return;
    try {
      await apiFetch(`/api/agents/${editing.slug}/bindings/${connectorName}`, {
        method: 'PUT',
        body: JSON.stringify({ capabilities: [], enabled: true }),
      });
      await fetchAgents();
      const res = await apiFetch(`/api/agents/${editing.slug}`);
      if (res.ok) { const d = await res.json(); setEditing(d); }
    } catch { /* ignore */ }
  };

  if (loading) return <div style={{ padding: '1rem', color: '#999' }}>Loading...</div>;

  // --- Detail/Edit View ---
  if (editing) {
    return (
      <div style={{ padding: '1rem', maxWidth: 800 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <div>
            <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700 }}>{editing.display_name}</h3>
            <span style={{ fontSize: '0.72rem', color: '#999' }}>{editing.slug}</span>
          </div>
          <button onClick={() => setEditing(null)} style={{ padding: '4px 12px', fontSize: '0.75rem', border: '1px solid #ddd', borderRadius: 6, backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit' }}>Back</button>
        </div>

        {/* Description */}
        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>Description</label>
          <input value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} style={inputStyle} placeholder="What this agent does..." />
        </div>

        {/* System Prompt */}
        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>System Prompt</label>
          <textarea
            value={form.system_prompt}
            onChange={e => setForm(f => ({ ...f, system_prompt: e.target.value }))}
            rows={18}
            style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.78rem', resize: 'vertical', lineHeight: 1.5 }}
          />
        </div>

        {/* Connector Bindings */}
        {editing.bindings.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <label style={labelStyle}>Connector Bindings</label>
            {editing.bindings.map(binding => (
              <div key={binding.connector_name} style={{ border: '1px solid #edf2f7', borderRadius: 8, padding: '0.75rem', marginBottom: '0.5rem', backgroundColor: '#fafafa' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <span style={{ fontWeight: 500, fontSize: '0.85rem' }}>{binding.connector_label}</span>
                  <button onClick={() => handleDeleteBinding(binding.connector_name)} style={{
                    padding: '2px 8px', fontSize: '0.72rem', border: '1px solid #e53e3e', borderRadius: 4,
                    backgroundColor: '#fff', color: '#e53e3e', cursor: 'pointer', fontFamily: 'inherit',
                  }}>Remove</button>
                </div>
                {binding.capabilities.map((cap, idx) => (
                  <label key={`${binding.connector_name}__${cap.action}__${idx}`} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.8rem', color: '#444', cursor: 'pointer', marginBottom: 2 }}>
                    <input type="checkbox" checked={cap.enabled} onChange={() => handleToggleCapability(binding, idx)} />
                    {cap.label}
                  </label>
                ))}
              </div>
            ))}
          </div>
        )}

        {/* Add Connector */}
        {editing.available_connectors && editing.available_connectors.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <label style={labelStyle}>Add Connector</label>
            <select defaultValue="" onChange={e => { if (e.target.value) { handleAddConnector(e.target.value); e.target.value = ''; } }}
              style={{ ...inputStyle, width: 'auto', cursor: 'pointer' }}>
              <option value="" disabled>Select a connector...</option>
              {editing.available_connectors.map(ac => (
                <option key={ac.connector_name} value={ac.connector_name}>{ac.display_name}</option>
              ))}
            </select>
          </div>
        )}

        {/* Actions */}
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={handleSave} disabled={saving} style={{
            padding: '6px 20px', fontSize: '0.8rem', fontWeight: 600, border: 'none', borderRadius: 6,
            backgroundColor: '#c4a882', color: '#fff', cursor: saving ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
          }}>{saving ? 'Saving...' : 'Save'}</button>
          {editing.has_prompt && (
            <button onClick={handleReset} style={{
              padding: '6px 20px', fontSize: '0.8rem', fontWeight: 500, border: '1px solid #ddd', borderRadius: 6,
              backgroundColor: '#fff', color: '#666', cursor: 'pointer', fontFamily: 'inherit',
            }}>Clear Prompt</button>
          )}
          <button onClick={() => setEditing(null)} style={{
            padding: '6px 20px', fontSize: '0.8rem', fontWeight: 500, border: '1px solid #ddd', borderRadius: 6,
            backgroundColor: '#fff', color: '#666', cursor: 'pointer', fontFamily: 'inherit',
          }}>Cancel</button>
        </div>
      </div>
    );
  }

  // --- List View ---
  return (
    <div style={{ padding: '1rem' }}>
      <h3 style={{ margin: '0 0 1rem', fontSize: '0.95rem', fontWeight: 700 }}>Agents</h3>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {agents.map(agent => (
          <div
            key={agent.slug}
            onClick={() => openEdit(agent)}
            style={{
              border: '1px solid #e2e8f0', borderRadius: 10, padding: '1rem',
              backgroundColor: '#fff', cursor: 'pointer',
              transition: 'border-color 0.15s, box-shadow 0.15s',
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = '#c4a882'; e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.06)'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.boxShadow = 'none'; }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontWeight: 600, fontSize: '0.9rem', color: '#333' }}>{agent.display_name}</span>
                <span style={{ fontSize: '0.72rem', color: '#bbb' }}>{agent.slug}</span>
                {!agent.has_prompt && (
                  <span style={{ fontSize: '0.65rem', backgroundColor: '#fee2e2', color: '#991b1b', padding: '2px 8px', borderRadius: 10, fontWeight: 500 }}>No prompt</span>
                )}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {agent.bindings.length > 0 && (
                  <span style={{ fontSize: '0.65rem', color: '#999' }}>{agent.bindings.length} connector{agent.bindings.length !== 1 ? 's' : ''}</span>
                )}
                <span style={{ fontSize: '0.72rem', color: '#c4a882' }}>Edit</span>
              </div>
            </div>
            {agent.description && (
              <div style={{ fontSize: '0.78rem', color: '#999', marginTop: 4 }}>{agent.description}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
