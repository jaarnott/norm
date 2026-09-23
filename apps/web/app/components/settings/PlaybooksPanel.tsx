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
  instructions: '', enabled: true, created_at: null, updated_at: null,
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
        ? { slug: editing.slug, agent_slug: editing.agent_slug, display_name: editing.display_name, description: editing.description, instructions: editing.instructions, enabled: editing.enabled }
        : { display_name: editing.display_name, description: editing.description, instructions: editing.instructions, enabled: editing.enabled };
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
    if (!editing || !aiPrompt.trim()) return;
    setAiGenerating(true);
    setError(null);
    try {
      const res = await apiFetch('/api/playbooks/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          description: aiPrompt,
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
            <input
              value={aiPrompt}
              onChange={e => setAiPrompt(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleGenerate(); } }}
              placeholder={editing.instructions ? 'Describe what to change...' : 'Describe the workflow you want to create...'}
              disabled={aiGenerating}
              style={{ ...inputStyle, flex: 1 }}
            />
            <button
              onClick={handleGenerate}
              disabled={aiGenerating || !aiPrompt.trim()}
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
            AI will {editing.instructions ? 'update' : 'generate'} the slug, name, description and instructions.
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
          <label style={labelStyle}>When to use (the agent reads this to decide whether to open the playbook)</label>
          <input value={editing.description} onChange={e => update('description', e.target.value)} style={inputStyle} placeholder="A standard weekly sales summary for one or more venues" />
        </div>

        <div style={{ marginBottom: '0.75rem' }}>
          <label style={labelStyle}>Instructions (step-by-step guide the agent follows)</label>
          <textarea ref={instructionsRef} value={editing.instructions} onChange={e => update('instructions', e.target.value)} style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem', resize: 'vertical', minHeight: 200, overflow: 'hidden' }} placeholder="Step-by-step workflow instructions for the agent..." />
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
        Playbooks are step-by-step guides for specific jobs. The agent sees each playbook's name and when to use it, and opens the full instructions when a request matches. A playbook guides the agent — it never limits which tools it can use.
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
