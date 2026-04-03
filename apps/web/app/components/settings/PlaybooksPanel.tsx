'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '../../lib/api';

interface Playbook {
  id: string;
  slug: string;
  agent_slug: string;
  display_name: string;
  description: string;
  instructions: string;
  tool_filter: string[] | null;
  enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

interface AgentOption {
  slug: string;
  display_name: string;
}

const EMPTY: Playbook = {
  id: '', slug: '', agent_slug: '', display_name: '', description: '',
  instructions: '', tool_filter: null, enabled: true, created_at: null, updated_at: null,
};

const labelStyle: React.CSSProperties = { fontSize: '0.75rem', fontWeight: 600, color: '#888', textTransform: 'uppercase' as const, marginBottom: 4, display: 'block' };
const inputStyle: React.CSSProperties = { width: '100%', padding: '6px 8px', border: '1px solid #ddd', borderRadius: 6, fontSize: '0.85rem', fontFamily: 'inherit', boxSizing: 'border-box' as const };

export default function PlaybooksPanel() {
  const [playbooks, setPlaybooks] = useState<Playbook[]>([]);
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [editing, setEditing] = useState<Playbook | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aiPrompt, setAiPrompt] = useState('');
  const [aiGenerating, setAiGenerating] = useState(false);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);
  const [agentTools, setAgentTools] = useState<{ action: string; connector: string; method: string; description: string }[]>([]);
  const [toolFilterInput, setToolFilterInput] = useState('');
  const [toolDropdownOpen, setToolDropdownOpen] = useState(false);

  // Fetch tools when editing agent changes
  useEffect(() => {
    if (!editing?.agent_slug) { setAgentTools([]); return; }
    const slug = editing.agent_slug;
    apiFetch(`/api/playbooks/tools/${slug}`)
      .then(r => {
        if (!r.ok) { console.error('Failed to fetch tools:', r.status); return null; }
        return r.json();
      })
      .then(d => { if (d?.tools) setAgentTools(d.tools); })
      .catch(err => console.error('Tools fetch error:', err));
  }, [editing?.agent_slug]);

  // Auto-resize instructions textarea
  useEffect(() => {
    const el = instructionsRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = Math.max(200, el.scrollHeight) + 'px';
    }
  }, [editing?.instructions]);

  const fetchPlaybooks = useCallback(async () => {
    const res = await apiFetch('/api/playbooks');
    if (res.ok) {
      const data = await res.json();
      setPlaybooks(data.playbooks || []);
    }
  }, []);

  const fetchAgents = useCallback(async () => {
    const res = await apiFetch('/api/agents');
    if (res.ok) {
      const data = await res.json();
      setAgents((data.agents || []).map((a: { agent_slug: string; display_name: string }) => ({
        slug: a.agent_slug,
        display_name: a.display_name,
      })));
    }
  }, []);

  useEffect(() => { fetchPlaybooks(); fetchAgents(); }, [fetchPlaybooks, fetchAgents]);

  const handleSave = async () => {
    if (!editing) return;
    setSaving(true);
    setError(null);
    try {
      const url = isNew ? '/api/playbooks' : `/api/playbooks/${editing.slug}`;
      const method = isNew ? 'POST' : 'PUT';
      const body = isNew
        ? { slug: editing.slug, agent_slug: editing.agent_slug, display_name: editing.display_name, description: editing.description, instructions: editing.instructions, tool_filter: editing.tool_filter, enabled: editing.enabled }
        : { display_name: editing.display_name, description: editing.description, instructions: editing.instructions, tool_filter: editing.tool_filter, enabled: editing.enabled };
      const res = await apiFetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!res.ok) {
        const data = await res.json();
        setError(data.detail || `Save failed (${res.status})`);
        return;
      }
      await fetchPlaybooks();
      setEditing(null);
      setIsNew(false);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (slug: string) => {
    await apiFetch(`/api/playbooks/${slug}`, { method: 'DELETE' });
    await fetchPlaybooks();
    if (editing?.slug === slug) { setEditing(null); setIsNew(false); }
  };

  const update = (field: keyof Playbook, value: unknown) => {
    if (!editing) return;
    setEditing({ ...editing, [field]: value });
  };

  const handleGenerate = async () => {
    if (!editing || !aiPrompt.trim() || !editing.agent_slug) return;
    setAiGenerating(true);
    setError(null);
    try {
      const res = await apiFetch('/api/playbooks/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          description: aiPrompt,
          agent_slug: editing.agent_slug,
          current_instructions: editing.instructions || null,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.detail || `Generate failed (${res.status})`);
        return;
      }
      const result = await res.json();
      setEditing(prev => prev ? {
        ...prev,
        instructions: result.instructions || prev.instructions,
        display_name: result.display_name || prev.display_name,
        description: result.description || prev.description,
        tool_filter: result.tool_filter ?? prev.tool_filter,
        slug: (isNew && result.slug) ? result.slug : prev.slug,
      } : prev);
      setAiPrompt('');
    } catch (err) {
      setError(String(err));
    } finally {
      setAiGenerating(false);
    }
  };

  // Group playbooks by agent
  const grouped: Record<string, Playbook[]> = {};
  for (const pb of playbooks) {
    (grouped[pb.agent_slug] ||= []).push(pb);
  }

  if (editing) {
    return (
      <div style={{ padding: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>{isNew ? 'New Playbook' : `Edit: ${editing.display_name}`}</h3>
          <button onClick={() => { setEditing(null); setIsNew(false); setError(null); }} style={{ border: '1px solid #ddd', borderRadius: 6, padding: '4px 12px', fontSize: '0.8rem', cursor: 'pointer', fontFamily: 'inherit', backgroundColor: '#fff' }}>Cancel</button>
        </div>

        {error && <div style={{ padding: '0.5rem', backgroundColor: '#fef2f2', border: '1px solid #fecaca', borderRadius: 6, color: '#dc2626', fontSize: '0.8rem', marginBottom: '0.75rem' }}>{error}</div>}

        <div style={{
          marginBottom: '0.75rem', padding: '0.75rem', border: '1px solid #d4e5f7',
          borderRadius: 8, backgroundColor: '#f8fbff',
        }}>
          <label style={{ ...labelStyle, color: '#2563eb' }}>AI Assistant</label>
          <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
            <select value={editing.agent_slug} onChange={e => update('agent_slug', e.target.value)} disabled={!isNew} style={{ ...inputStyle, width: 150, flexShrink: 0, backgroundColor: isNew ? '#fff' : '#f5f5f5' }}>
              <option value="">Agent...</option>
              {agents.map((a, i) => <option key={`${a.slug}-${i}`} value={a.slug}>{a.display_name}</option>)}
            </select>
            <input
              value={aiPrompt}
              onChange={e => setAiPrompt(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleGenerate(); } }}
              placeholder={editing.instructions ? 'Describe what to change...' : 'Describe the workflow you want to create...'}
              disabled={aiGenerating || !editing.agent_slug}
              style={{ ...inputStyle, flex: 1 }}
            />
            <button
              onClick={handleGenerate}
              disabled={aiGenerating || !aiPrompt.trim() || !editing.agent_slug}
              style={{
                padding: '6px 14px', fontSize: '0.8rem', fontWeight: 600, border: 'none', borderRadius: 6,
                backgroundColor: '#2563eb', color: '#fff', cursor: aiGenerating ? 'not-allowed' : 'pointer',
                fontFamily: 'inherit', opacity: aiGenerating ? 0.6 : 1, whiteSpace: 'nowrap',
              }}
            >
              {aiGenerating ? 'Generating...' : editing.instructions ? 'Update' : 'Generate'}
            </button>
          </div>
          <p style={{ fontSize: '0.72rem', color: '#888', margin: 0, lineHeight: 1.4 }}>
            AI will {editing.instructions ? 'update' : 'generate'} the slug, name, description, instructions, and tool filter.
          </p>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '0.75rem' }}>
          <div>
            <label style={labelStyle}>Slug</label>
            <input value={editing.slug} onChange={e => update('slug', e.target.value)} disabled={!isNew} style={{ ...inputStyle, backgroundColor: isNew ? '#fff' : '#f5f5f5' }} placeholder="weekly_sales_report" />
          </div>
          <div>
            <label style={labelStyle}>Agent</label>
            <select value={editing.agent_slug} onChange={e => update('agent_slug', e.target.value)} disabled={!isNew} style={{ ...inputStyle, backgroundColor: isNew ? '#fff' : '#f5f5f5' }}>
              <option value="">Select agent...</option>
              {agents.map((a, i) => <option key={`${a.slug}-${i}`} value={a.slug}>{a.display_name}</option>)}
            </select>
          </div>
        </div>

        <div style={{ marginBottom: '0.75rem' }}>
          <label style={labelStyle}>Display Name</label>
          <input value={editing.display_name} onChange={e => update('display_name', e.target.value)} style={inputStyle} placeholder="Weekly Sales Report" />
        </div>

        <div style={{ marginBottom: '0.75rem' }}>
          <label style={labelStyle}>Description (used by router for matching)</label>
          <input value={editing.description} onChange={e => update('description', e.target.value)} style={inputStyle} placeholder="Generate a weekly sales comparison report for one or more venues" />
        </div>

        <div style={{ marginBottom: '0.75rem' }}>
          <label style={labelStyle}>Instructions (focused prompt for the agent)</label>
          <textarea ref={instructionsRef} value={editing.instructions} onChange={e => update('instructions', e.target.value)} style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem', resize: 'vertical', minHeight: 200, overflow: 'hidden' }} placeholder="Step-by-step workflow instructions for the agent..." />
        </div>

        <div style={{ marginBottom: '0.75rem' }}>
          <label style={labelStyle}>Tool Filter (leave empty for all tools)</label>
          {/* Selected tools as chips */}
          {(editing.tool_filter || []).length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
              {(editing.tool_filter || []).map(action => {
                const tool = agentTools.find(t => t.action === action);
                return (
                  <span key={action} style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    padding: '2px 8px', fontSize: '0.75rem', backgroundColor: '#eff6ff',
                    border: '1px solid #bfdbfe', borderRadius: 4, color: '#1e40af',
                  }}>
                    {action}
                    {tool && <span style={{ color: '#93c5fd', fontSize: '0.65rem' }}>{tool.method}</span>}
                    <button onClick={() => {
                      const next = (editing.tool_filter || []).filter(a => a !== action);
                      update('tool_filter', next.length > 0 ? next : null);
                    }} style={{ border: 'none', background: 'none', color: '#93c5fd', cursor: 'pointer', fontSize: '0.85rem', padding: 0, lineHeight: 1 }}>&times;</button>
                  </span>
                );
              })}
            </div>
          )}
          {/* Autocomplete input */}
          <div style={{ position: 'relative' }}>
            <input
              value={toolFilterInput}
              onChange={e => { setToolFilterInput(e.target.value); setToolDropdownOpen(true); }}
              onFocus={() => {
                setToolDropdownOpen(true);
                // Retry fetch if tools haven't loaded yet
                if (agentTools.length === 0 && editing?.agent_slug) {
                  apiFetch(`/api/playbooks/tools/${editing.agent_slug}`)
                    .then(r => r.ok ? r.json() : null)
                    .then(d => { if (d?.tools) setAgentTools(d.tools); })
                    .catch(() => {});
                }
              }}
              onBlur={() => setTimeout(() => setToolDropdownOpen(false), 150)}
              placeholder={agentTools.length > 0 ? 'Search tools to add...' : (editing?.agent_slug ? 'Loading tools...' : 'Select an agent to see available tools')}
              disabled={!editing?.agent_slug}
              style={inputStyle}
            />
            {toolDropdownOpen && agentTools.length > 0 && (() => {
              const selected = new Set(editing.tool_filter || []);
              const filtered = agentTools
                .filter(t => !selected.has(t.action))
                .filter(t => !toolFilterInput || t.action.toLowerCase().includes(toolFilterInput.toLowerCase()) || t.description.toLowerCase().includes(toolFilterInput.toLowerCase()));
              if (filtered.length === 0) return null;
              return (
                <div style={{
                  position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
                  maxHeight: 200, overflowY: 'auto', backgroundColor: '#fff',
                  border: '1px solid #ddd', borderRadius: 6, boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                  marginTop: 2,
                }}>
                  {filtered.map(t => (
                    <div
                      key={t.action}
                      onMouseDown={e => {
                        e.preventDefault();
                        update('tool_filter', [...(editing.tool_filter || []), t.action]);
                        setToolFilterInput('');
                      }}
                      style={{
                        padding: '6px 10px', cursor: 'pointer', fontSize: '0.82rem',
                        borderBottom: '1px solid #f5f5f5',
                      }}
                      onMouseEnter={e => (e.currentTarget.style.backgroundColor = '#f0f4ff')}
                      onMouseLeave={e => (e.currentTarget.style.backgroundColor = '#fff')}
                    >
                      <span style={{ fontWeight: 500 }}>{t.action}</span>
                      <span style={{ color: '#aaa', fontSize: '0.72rem', marginLeft: 6 }}>[{t.method}]</span>
                      {t.description && <div style={{ fontSize: '0.72rem', color: '#888', marginTop: 1 }}>{t.description}</div>}
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>
        </div>

        <div style={{ marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" checked={editing.enabled} onChange={e => update('enabled', e.target.checked)} id="pb-enabled" />
          <label htmlFor="pb-enabled" style={{ fontSize: '0.85rem', cursor: 'pointer' }}>Enabled</label>
        </div>

        <button onClick={handleSave} disabled={saving || !editing.slug || !editing.agent_slug || !editing.display_name} style={{
          padding: '8px 20px', fontSize: '0.85rem', fontWeight: 600, border: 'none', borderRadius: 6,
          backgroundColor: '#1a1a1a', color: '#fff', cursor: saving ? 'not-allowed' : 'pointer', fontFamily: 'inherit', opacity: saving ? 0.6 : 1,
        }}>
          {saving ? 'Saving...' : isNew ? 'Create Playbook' : 'Save Changes'}
        </button>
      </div>
    );
  }

  return (
    <div style={{ padding: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>Playbooks</h3>
        <button onClick={() => { setEditing({ ...EMPTY }); setIsNew(true); }} style={{
          padding: '4px 12px', fontSize: '0.8rem', border: '1px solid #1a1a1a', borderRadius: 6,
          backgroundColor: '#1a1a1a', color: '#fff', cursor: 'pointer', fontFamily: 'inherit',
        }}>
          + New Playbook
        </button>
      </div>

      <p style={{ color: '#888', fontSize: '0.8rem', margin: '0 0 1rem', lineHeight: 1.5 }}>
        Playbooks are focused instruction sets for specific workflows. The router auto-matches messages to the best playbook, giving the agent targeted guidance and filtered tools.
      </p>

      {playbooks.length === 0 ? (
        <p style={{ color: '#bbb', fontSize: '0.85rem', fontStyle: 'italic' }}>No playbooks yet. Create one to give your agents focused workflow instructions.</p>
      ) : (
        Object.entries(grouped).map(([agentSlug, pbs]) => {
          const agentName = agents.find(a => a.slug === agentSlug)?.display_name || agentSlug;
          return (
            <div key={agentSlug} style={{ marginBottom: '1rem' }}>
              <h4 style={{ margin: '0 0 0.5rem', fontSize: '0.82rem', fontWeight: 600, color: '#666' }}>{agentName}</h4>
              {pbs.map(pb => (
                <div key={pb.slug} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '0.6rem 0.75rem', border: '1px solid #e8e4de', borderRadius: 8,
                  marginBottom: '0.4rem', backgroundColor: pb.enabled ? '#fff' : '#f9f9f9',
                }}>
                  <div>
                    <span style={{ fontWeight: 500, fontSize: '0.85rem', color: pb.enabled ? '#333' : '#999' }}>{pb.display_name}</span>
                    <span style={{ fontSize: '0.72rem', color: '#aaa', marginLeft: 8 }}>{pb.slug}</span>
                    {!pb.enabled && <span style={{ fontSize: '0.65rem', color: '#e53e3e', marginLeft: 6, fontWeight: 600 }}>DISABLED</span>}
                    <div style={{ fontSize: '0.75rem', color: '#888', marginTop: 2 }}>{pb.description}</div>
                    {pb.tool_filter && <div style={{ fontSize: '0.68rem', color: '#6366f1', marginTop: 2 }}>Tools: {pb.tool_filter.join(', ')}</div>}
                  </div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button onClick={() => { setEditing(pb); setIsNew(false); }} style={{ padding: '3px 10px', fontSize: '0.75rem', border: '1px solid #ddd', borderRadius: 4, backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit' }}>Edit</button>
                    <button onClick={() => handleDelete(pb.slug)} style={{ padding: '3px 10px', fontSize: '0.75rem', border: '1px solid #e53e3e', borderRadius: 4, backgroundColor: '#fff', color: '#e53e3e', cursor: 'pointer', fontFamily: 'inherit' }}>Delete</button>
                  </div>
                </div>
              ))}
            </div>
          );
        })
      )}
    </div>
  );
}
