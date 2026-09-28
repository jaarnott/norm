'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import type { AgentConfig } from '../../types';
import type { TeamApp } from '../../hooks/useTeam';

const labelStyle: React.CSSProperties = { fontSize: '0.75rem', fontWeight: 600, color: '#888', textTransform: 'uppercase' as const, marginBottom: 4, display: 'block' };
const inputStyle: React.CSSProperties = { width: '100%', padding: '6px 8px', border: '1px solid #ddd', borderRadius: 6, fontSize: '0.85rem', fontFamily: 'inherit', boxSizing: 'border-box' as const };

// In unified mode only these rows carry a real prompt: the Norm prompt (base)
// and the router's classifier prompt. Everyone else has a personality line.
const PROMPT_OWNERS = new Set(['base', 'router']);

export default function AgentsPanel() {
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<AgentConfig | null>(null);
  const [form, setForm] = useState({ description: '', system_prompt: '', persona: '' });
  // Unified mode: one Norm prompt (the base row) + a personality per member.
  const [unified, setUnified] = useState(false);
  const [saving, setSaving] = useState(false);
  // Apps v3: an agent's reach is the Apps bound to it (the App Map), not the
  // retired per-agent connection bindings.
  const [mapApps, setMapApps] = useState<TeamApp[] | null>(null);
  useEffect(() => {
    apiFetch('/api/admin/app-map')
      .then((r) => (r.ok ? r.json() : { apps: [] }))
      .then((d) => setMapApps(d.apps ?? []))
      .catch(() => setMapApps([]));
  }, []);
  const appsFor = (slug: string) => (mapApps ?? []).filter((a) => a.member === slug);
  const coreApps = () => (mapApps ?? []).filter((a) => a.member === '*');

  const fetchAgents = useCallback(async () => {
    try {
      const res = await apiFetch('/api/agents');
      if (res.ok) {
        const data = await res.json();
        setAgents(data.agents || []);
        setUnified(!!data.unified_prompt);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchAgents(); }, [fetchAgents]);

  const openEdit = (agent: AgentConfig) => {
    setEditing(agent);
    setForm({ description: agent.description || '', system_prompt: agent.system_prompt || '', persona: agent.persona || '' });
  };

  const handleSave = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/agents/${editing.slug}`, {
        method: 'PUT',
        body: JSON.stringify(
          unified && !PROMPT_OWNERS.has(editing.slug)
            ? { description: form.description || null, persona: form.persona }
            : { system_prompt: form.system_prompt || null, description: form.description || null },
        ),
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
    // Clearing the base row's prompt switches EVERY conversation back to the
    // per-agent prompts, in every environment — worth a second click.
    if (editing.slug === 'base' && !window.confirm(
      'Clear the Norm prompt? Every conversation, in every environment, falls back to the old per-agent prompts.',
    )) return;
    try {
      const res = await apiFetch(`/api/agents/${editing.slug}/reset-prompt`, { method: 'POST' });
      if (res.ok) {
        await fetchAgents();
        setEditing(null);
      }
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

        {unified && !PROMPT_OWNERS.has(editing.slug) && (
          <div style={{ marginBottom: 12 }}>
            <label style={labelStyle}>Personality</label>
            <input value={form.persona} onChange={e => setForm(f => ({ ...f, persona: e.target.value }))} style={inputStyle}
              placeholder="In here you're the…" />
            <div style={{ fontSize: '0.72rem', color: '#8a8a8a', marginTop: 4 }}>
              Tone only — rules, tools and approvals all come from the Norm prompt and this agent&apos;s Apps.
            </div>
          </div>
        )}

        {/* System Prompt (legacy per-agent mode, or the Norm/router prompt in unified mode) */}
        {(!unified || PROMPT_OWNERS.has(editing.slug)) && (
        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>{unified && editing.slug === 'base' ? 'Norm prompt (every conversation)' : 'System Prompt'}</label>
          <textarea
            value={form.system_prompt}
            onChange={e => setForm(f => ({ ...f, system_prompt: e.target.value }))}
            rows={18}
            style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.78rem', resize: 'vertical', lineHeight: 1.5 }}
          />
        </div>
        )}

        {/* Apps & tools — derived from the App Map (read-only) */}
        <div style={{ marginBottom: 16 }}>
          <label style={labelStyle}>Apps &amp; tools</label>
          <div style={{ fontSize: '0.74rem', color: '#8a8a8a', marginBottom: 8 }}>
            What this agent can use comes from the Apps bound to it. Change it in the catalog seed;
            see everything in Settings → App Map.
          </div>
          {mapApps === null ? (
            <div style={{ fontSize: '0.78rem', color: '#999' }}>Loading…</div>
          ) : (
            <>
              {[...appsFor(editing.slug), ...coreApps()].map((a) => (
                <div key={a.slug} style={{ border: '1px solid #edf2f7', borderRadius: 8, padding: '0.6rem 0.75rem', marginBottom: '0.5rem', backgroundColor: '#fafafa' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span style={{ fontWeight: 600, fontSize: '0.84rem' }}>{a.name}</span>
                    {a.member === '*' && <span style={{ fontSize: '0.64rem', color: '#8a8a8a' }}>shared by every agent</span>}
                    {!a.switchable && <span style={{ fontSize: '0.62rem', fontWeight: 700, color: '#2e5a7d', background: '#e8f0f6', borderRadius: 8, padding: '1px 7px' }}>ALWAYS ON</span>}
                    <span style={{ flex: 1 }} />
                    <span style={{ fontSize: '0.7rem', color: '#8a8a8a' }}>
                      {a.required_connections.length ? `uses ${a.required_connections.map((c) => c.display_name).join(', ')}` : 'runs on Norm'}
                    </span>
                  </div>
                  {a.tools.length > 0 ? (
                    <ul style={{ margin: 0, paddingLeft: 16 }}>
                      {a.tools.map((t) => (
                        <li key={t.key} style={{ fontSize: '0.76rem', color: '#444' }}>
                          <span style={{ fontFamily: 'monospace' }}>{t.key}</span>
                          <span style={{ color: '#999' }}> · {t.type}{t.writes ? ' · writes' : ''}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div style={{ fontSize: '0.74rem', color: '#999' }}>no chat tools</div>
                  )}
                  {a.skills.length > 0 && (
                    <div style={{ fontSize: '0.72rem', color: '#6b6b6b', marginTop: 4 }}>
                      Skills: {a.skills.map((sk) => sk.label).join(', ')}
                    </div>
                  )}
                </div>
              ))}
              {appsFor(editing.slug).length === 0 && editing.slug !== 'base' && (
                <div style={{ fontSize: '0.76rem', color: '#8a8a8a' }}>No Apps are bound to this agent yet — it uses Norm Core only.</div>
              )}
            </>
          )}
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={handleSave} disabled={saving} style={{
            padding: '6px 20px', fontSize: '0.8rem', fontWeight: 600, border: 'none', borderRadius: 6,
            backgroundColor: '#c4a882', color: '#fff', cursor: saving ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
          }}>{saving ? 'Saving...' : 'Save'}</button>
          {/* In unified mode a member's old prompt is the rollback copy — nothing
              reads it, so there's nothing to clear from here. */}
          {editing.has_prompt && (!unified || PROMPT_OWNERS.has(editing.slug)) && (
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
                {mapApps && appsFor(agent.slug).length > 0 && (
                  <span style={{ fontSize: '0.65rem', color: '#999' }}>{appsFor(agent.slug).length} app{appsFor(agent.slug).length !== 1 ? 's' : ''}</span>
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
