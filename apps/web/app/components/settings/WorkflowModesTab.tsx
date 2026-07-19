'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';

interface Mode { id: string; label: string; description: string }
interface Workflow { key: string; label: string; description: string }

export default function WorkflowModesTab() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [modes, setModes] = useState<Mode[]>([]);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    apiFetch('/api/workflow-modes')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        setWorkflows(d.workflows || []);
        setModes(d.modes || []);
        setSelected(d.selected || {});
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  const change = async (workflow: string, mode: string) => {
    setSaving(workflow);
    setSelected((prev) => ({ ...prev, [workflow]: mode }));
    try {
      await apiFetch('/api/workflow-modes', {
        method: 'POST',
        body: JSON.stringify({ workflow, mode }),
      });
    } finally {
      setSaving(null);
    }
  };

  if (!loaded) return <div style={{ color: '#999', fontSize: '0.85rem' }}>Loading…</div>;

  return (
    <div style={{ maxWidth: 720 }}>
      <h3 style={{ margin: '0 0 0.35rem', fontSize: '0.95rem', fontWeight: 600 }}>Workflow modes</h3>
      <p style={{ margin: '0 0 1.25rem', fontSize: '0.8rem', color: '#777' }}>
        Choose how much Norm does on its own for each workflow. These are your personal
        settings — you can also change them by asking Norm in a conversation.
      </p>
      {workflows.map((w) => {
        const current = selected[w.key] || 'unset';
        return (
          <div key={w.key} style={{ border: '1px solid #eee', borderRadius: 10, padding: '0.9rem 1rem', marginBottom: '0.85rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>{w.label}</span>
              {current === 'unset' && (
                <span style={{ fontSize: '0.65rem', color: '#b78a2f' }}>not set — Norm will ask</span>
              )}
              {saving === w.key && <span style={{ fontSize: '0.65rem', color: '#999' }}>saving…</span>}
            </div>
            <p style={{ margin: '0.2rem 0 0.7rem', fontSize: '0.75rem', color: '#888' }}>{w.description}</p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              {modes.map((m) => {
                const on = current === m.id;
                return (
                  <label key={m.id} style={{
                    display: 'flex', gap: '0.6rem', alignItems: 'flex-start', cursor: 'pointer',
                    border: `1px solid ${on ? '#a08060' : '#eee'}`, borderRadius: 8, padding: '0.5rem 0.65rem',
                    background: on ? '#faf8f5' : '#fff',
                  }}>
                    <input type="radio" name={w.key} checked={on} onChange={() => change(w.key, m.id)} style={{ marginTop: 2 }} />
                    <span>
                      <span style={{ fontSize: '0.8rem', fontWeight: 500 }}>{m.label}</span>
                      <span style={{ display: 'block', fontSize: '0.72rem', color: '#888', marginTop: 1 }}>{m.description}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
