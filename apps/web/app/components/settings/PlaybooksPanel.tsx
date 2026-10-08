'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Plus, Sparkles } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Button from '../ui/Button';
import BackLink from '../ui/BackLink';
import Badge from '../ui/Badge';
import PageState from '../ui/PageState';

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

const sectionTitle: React.CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
const fieldRow: React.CSSProperties = { marginBottom: 12 };
// The explanation after a field's name: lighter than the name itself.
const hint: React.CSSProperties = { fontWeight: 400, color: 'var(--muted)' };
// A read-only select (slug and agent are fixed once a playbook exists).
// backgroundColor, not background: the shorthand would drop .n-select's chevron.
const lockedSelect: React.CSSProperties = { backgroundColor: 'var(--surface)', color: 'var(--muted)', cursor: 'default' };

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
    const close = () => { setEditing(null); setIsNew(false); setError(null); };
    return (
      <div style={{ maxWidth: 860 }}>
        <div style={{ marginBottom: 8 }}>
          <BackLink label="Back to playbooks" onClick={close} />
        </div>
        <h3 style={{ ...sectionTitle, marginBottom: 16, overflowWrap: 'anywhere' }}>{isNew ? 'New playbook' : `Edit: ${editing.display_name}`}</h3>

        {error && <div style={{ marginBottom: 12 }}><PageState kind="error" title={error} /></div>}

        <div className="n-card" style={{ padding: 14, marginBottom: 16 }}>
          <label className="n-label" htmlFor="pb-ai">AI assistant</label>
          <div style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
            <input
              id="pb-ai"
              className="n-input"
              value={aiPrompt}
              onChange={e => setAiPrompt(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleGenerate(); } }}
              placeholder={editing.instructions ? 'Describe what to change...' : 'Describe the workflow you want to create...'}
              disabled={aiGenerating}
              style={{ flex: 1, minWidth: 0 }}
            />
            <Button icon={Sparkles} onClick={handleGenerate} disabled={aiGenerating || !aiPrompt.trim()}>
              {aiGenerating ? 'Generating…' : editing.instructions ? 'Update' : 'Generate'}
            </Button>
          </div>
          <p style={{ margin: 0, fontSize: 'var(--fs-xs)', color: 'var(--muted)', lineHeight: 1.4 }}>
            AI will {editing.instructions ? 'update' : 'generate'} the slug, name, description and instructions.
          </p>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, marginBottom: 12 }}>
          <div>
            <label className="n-label" htmlFor="pb-slug">Slug</label>
            <input id="pb-slug" className="n-input" value={editing.slug} onChange={e => update('slug', e.target.value)} disabled={!isNew} style={{ width: '100%' }} placeholder="weekly_sales_report" />
          </div>
          <div>
            <label className="n-label" htmlFor="pb-agent">Agent</label>
            <select id="pb-agent" className="n-select" value={editing.agent_slug} onChange={e => update('agent_slug', e.target.value)} disabled={!isNew} style={{ width: '100%', ...(isNew ? {} : lockedSelect) }}>
              <option value="">Select agent...</option>
              {agents.map((a, i) => <option key={`${a.slug}-${i}`} value={a.slug}>{a.display_name}</option>)}
            </select>
          </div>
        </div>

        <div style={fieldRow}>
          <label className="n-label" htmlFor="pb-name">Display name</label>
          <input id="pb-name" className="n-input" value={editing.display_name} onChange={e => update('display_name', e.target.value)} style={{ width: '100%' }} placeholder="Weekly Sales Report" />
        </div>

        <div style={fieldRow}>
          <label className="n-label" htmlFor="pb-when">When to use <span style={hint}>(the agent reads this to decide whether to open the playbook)</span></label>
          <input id="pb-when" className="n-input" value={editing.description} onChange={e => update('description', e.target.value)} style={{ width: '100%' }} placeholder="A standard weekly sales summary for one or more venues" />
        </div>

        <div style={fieldRow}>
          <label className="n-label" htmlFor="pb-instructions">Instructions <span style={hint}>(step-by-step guide the agent follows)</span></label>
          <textarea id="pb-instructions" ref={instructionsRef} className="n-input" value={editing.instructions} onChange={e => update('instructions', e.target.value)} style={{ display: 'block', width: '100%', minHeight: 200, overflow: 'hidden', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)', lineHeight: 1.5 }} placeholder="Step-by-step workflow instructions for the agent..." />
        </div>

        <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" checked={editing.enabled} onChange={e => update('enabled', e.target.checked)} id="pb-enabled" style={{ width: 16, height: 16, margin: 0, accentColor: 'var(--accent)', cursor: 'pointer' }} />
          <label htmlFor="pb-enabled" style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', cursor: 'pointer' }}>Enabled</label>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button variant="primary" onClick={handleSave} disabled={saving || !editing.slug || !editing.agent_slug || !editing.display_name}>
            {saving ? 'Saving…' : isNew ? 'Create playbook' : 'Save changes'}
          </Button>
          <Button onClick={close}>Cancel</Button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, minWidth: 0 }}>
          <h3 style={sectionTitle}>Playbooks</h3>
          {playbooks.length > 0 && (
            <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              {playbooks.length} {playbooks.length === 1 ? 'playbook' : 'playbooks'}
            </span>
          )}
        </div>
        <Button variant="primary" icon={Plus} onClick={() => { setEditing({ ...EMPTY }); setIsNew(true); }}>
          New playbook
        </Button>
      </div>

      <p style={{ margin: '0 0 20px', maxWidth: 760, fontSize: 'var(--fs-sm)', color: 'var(--muted)', lineHeight: 1.5 }}>
        Playbooks are step-by-step guides for specific jobs. The agent sees each playbook&rsquo;s name and when to use it, and opens the full instructions when a request matches. A playbook guides the agent — it never limits which tools it can use.
      </p>

      {playbooks.length === 0 ? (
        <PageState kind="empty" title="No playbooks yet." detail="Create one to give your agents focused workflow instructions." />
      ) : (
        Object.entries(grouped).map(([agentSlug, pbs]) => {
          // Fallback reads "time attendance", not "time_attendance" (the eyebrow uppercases it).
          const agentName = agents.find(a => a.slug === agentSlug)?.display_name || agentSlug.replace(/_/g, ' ');
          return (
            <section key={agentSlug} style={{ marginBottom: 20 }}>
              <h4 className="n-eyebrow" style={{ margin: '0 0 6px' }}>{agentName}</h4>
              <div className="n-card" style={{ overflow: 'hidden' }}>
                {pbs.map((pb, i) => (
                  <div key={pb.slug} style={{
                    display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12, rowGap: 8,
                    padding: '10px 14px', borderTop: i > 0 ? '1px solid var(--line)' : 'none',
                    backgroundColor: pb.enabled ? 'var(--bg)' : 'var(--surface)',
                  }}>
                    <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, rowGap: 2 }}>
                        <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: pb.enabled ? 'var(--text)' : 'var(--muted)' }}>{pb.display_name}</span>
                        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', overflowWrap: 'anywhere' }}>{pb.slug}</span>
                        {!pb.enabled && <Badge tone="warn">Disabled</Badge>}
                      </div>
                      {pb.description && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginTop: 2 }}>{pb.description}</div>}
                    </div>
                    <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                      <Button size="sm" onClick={() => { setEditing(pb); setIsNew(false); }}>Edit</Button>
                      <Button size="sm" variant="danger" onClick={() => handleDelete(pb.slug)}>Delete</Button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          );
        })
      )}
    </div>
  );
}
