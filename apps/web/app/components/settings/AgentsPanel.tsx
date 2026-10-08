'use client';

import { useState, useEffect, useCallback } from 'react';
import { ChevronRight } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import type { AgentConfig } from '../../types';
import type { TeamApp } from '../../hooks/useTeam';
import Badge from '../ui/Badge';
import BackLink from '../ui/BackLink';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';

// The small help line under a field label.
const helpStyle: React.CSSProperties = { fontSize: 'var(--fs-xs)', color: 'var(--muted)' };

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

  if (loading) return <PageState kind="loading" title="Loading agents…" />;

  // --- Detail/Edit View ---
  if (editing) {
    // The agent's own Apps, then Norm Core (shared by every agent).
    const apps = [...appsFor(editing.slug), ...coreApps()];
    return (
      <div style={{ maxWidth: 800, lineHeight: 1.45 }}>
        <div style={{ marginBottom: 8 }}>
          <BackLink label="Back to agents" onClick={() => setEditing(null)} />
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: 10, rowGap: 2, marginBottom: 16 }}>
          <h3 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{editing.display_name}</h3>
          <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{editing.slug}</span>
        </div>

        {/* Description */}
        <div style={{ marginBottom: 16 }}>
          <label className="n-label" htmlFor="agent-description">Description</label>
          <input id="agent-description" className="n-input" style={{ width: '100%' }}
            value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} placeholder="What this agent does..." />
        </div>

        {unified && !PROMPT_OWNERS.has(editing.slug) && (
          <div style={{ marginBottom: 16 }}>
            <label className="n-label" htmlFor="agent-persona">Personality</label>
            <input id="agent-persona" className="n-input" style={{ width: '100%' }}
              value={form.persona} onChange={e => setForm(f => ({ ...f, persona: e.target.value }))}
              placeholder="In here you're the…" />
            <div style={{ ...helpStyle, marginTop: 4 }}>
              Tone only — rules, tools and approvals all come from the Norm prompt and this agent&apos;s Apps.
            </div>
          </div>
        )}

        {/* System Prompt (legacy per-agent mode, or the Norm/router prompt in unified mode) */}
        {(!unified || PROMPT_OWNERS.has(editing.slug)) && (
        <div style={{ marginBottom: 16 }}>
          <label className="n-label" htmlFor="agent-system-prompt">{unified && editing.slug === 'base' ? 'Norm prompt (every conversation)' : 'System prompt'}</label>
          <textarea
            id="agent-system-prompt"
            className="n-input"
            value={form.system_prompt}
            onChange={e => setForm(f => ({ ...f, system_prompt: e.target.value }))}
            rows={18}
            style={{ display: 'block', width: '100%', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)', lineHeight: 1.5 }}
          />
        </div>
        )}

        {/* Apps & tools — derived from the App Map (read-only) */}
        <div style={{ marginBottom: 20 }}>
          <div className="n-label">Apps &amp; tools</div>
          <div style={{ ...helpStyle, marginBottom: 8 }}>
            What this agent can use comes from the Apps bound to it. Change it in the catalog seed;
            see everything in Settings → App map.
          </div>
          {mapApps === null ? (
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Loading…</div>
          ) : (
            <>
              {apps.length > 0 && (
                <div className="n-card" style={{ overflow: 'hidden' }}>
                  {apps.map((a, i) => (
                    <div key={a.slug} style={{ padding: '10px 14px', borderTop: i > 0 ? '1px solid var(--line)' : 'none' }}>
                      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, rowGap: 4, marginBottom: 4 }}>
                        <span style={{ fontWeight: 600, fontSize: 'var(--fs-base)', color: 'var(--text)' }}>{a.name}</span>
                        {a.member === '*' && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>shared by every agent</span>}
                        {!a.switchable && <Badge>Always on</Badge>}
                        <span style={{ flex: 1 }} />
                        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                          {a.required_connections.length ? `uses ${a.required_connections.map((c) => c.display_name).join(', ')}` : 'runs on Norm'}
                        </span>
                      </div>
                      {a.tools.length > 0 ? (
                        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                          {a.tools.map((t) => (
                            <li key={t.key} style={{ overflowWrap: 'anywhere' }}>
                              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>{t.key}</span>
                              <span style={{ color: 'var(--muted)' }}> · {t.type}{t.writes ? ' · writes' : ''}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No chat tools</div>
                      )}
                      {a.skills.length > 0 && (
                        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-soft)', marginTop: 4 }}>
                          Skills: {a.skills.map((sk) => sk.label).join(', ')}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {appsFor(editing.slug).length === 0 && editing.slug !== 'base' && (
                <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', marginTop: apps.length > 0 ? 8 : 0 }}>No Apps are bound to this agent yet — it uses Norm Core only.</div>
              )}
            </>
          )}
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          <Button variant="primary" onClick={handleSave} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
          {/* In unified mode a member's old prompt is the rollback copy — nothing
              reads it, so there's nothing to clear from here. */}
          {editing.has_prompt && (!unified || PROMPT_OWNERS.has(editing.slug)) && (
            <Button variant="danger" onClick={handleReset} style={{ marginLeft: 'auto' }}>Clear prompt</Button>
          )}
        </div>
      </div>
    );
  }

  // --- List View ---
  return (
    <div style={{ lineHeight: 1.45 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: 10, rowGap: 2, marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>Agents</h3>
        {agents.length > 0 && (
          <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            {agents.length} {agents.length === 1 ? 'agent' : 'agents'}
          </span>
        )}
      </div>

      {agents.length > 0 && (
        <div className="n-card" style={{ overflow: 'hidden' }}>
          {agents.map((agent, i) => (
            <button
              key={agent.slug}
              type="button"
              className="n-row"
              onClick={() => openEdit(agent)}
              style={{ borderRadius: 0, padding: '12px 16px', lineHeight: 1.4, borderTop: i > 0 ? '1px solid var(--line)' : 'none' }}
            >
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, rowGap: 2 }}>
                  <span style={{ fontWeight: 600, fontSize: 'var(--fs-base)', color: 'var(--text)' }}>{agent.display_name}</span>
                  <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 400, color: 'var(--muted)' }}>{agent.slug}</span>
                  {!agent.has_prompt && <Badge tone="error">No prompt</Badge>}
                </span>
                {agent.description && (
                  <span style={{ display: 'block', fontSize: 'var(--fs-sm)', fontWeight: 400, color: 'var(--muted)', marginTop: 2 }}>{agent.description}</span>
                )}
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 10, flex: '0 0 auto' }}>
                {mapApps && appsFor(agent.slug).length > 0 && (
                  <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 400, color: 'var(--muted)' }}>{appsFor(agent.slug).length} app{appsFor(agent.slug).length !== 1 ? 's' : ''}</span>
                )}
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text-soft)' }}>
                  Edit
                  <Icon icon={ChevronRight} size="dense" tone="muted" />
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
