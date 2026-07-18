'use client';

/**
 * Settings → MCP.
 *
 * Curate which Norm capabilities are exposed to external AI clients over MCP.
 * Candidates are computed server-side from every connector action and every
 * playbook, so a new one appears here the moment it exists — nothing is
 * registered in two places.
 *
 * The dangerous decisions are NOT toggles: read-vs-draft is derived from the
 * tool, scopes come from a fixed vocabulary, and write tools can't be exposed
 * directly. The server refuses anything unsafe; this UI surfaces the reason.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../../lib/api';

interface Capability {
  kind: 'connector' | 'playbook';
  target: string;
  action: string;
  tool_name: string;
  method: string;
  description: string;
  access: string | null;
  enabled: boolean;
  scopes: string[];
  grantable_scopes: string[];
  exposable: boolean;
  reason: string | null;
}
interface Scope { name: string; label: string; access_level: string; }

export default function McpPanel() {
  const [caps, setCaps] = useState<Capability[]>([]);
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [c, s] = await Promise.all([
        apiFetch('/api/mcp/capabilities'),
        apiFetch('/api/mcp/scopes'),
      ]);
      if (!c.ok) { setError('Failed to load MCP capabilities.'); return; }
      setCaps(await c.json());
      setScopes(s.ok ? await s.json() : []);
    } catch {
      setError('Failed to load MCP capabilities.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const scopeLabel = useMemo(() => {
    const m: Record<string, string> = {};
    scopes.forEach((s) => { m[s.name] = s.label; });
    return m;
  }, [scopes]);

  async function save(cap: Capability, changes: Partial<Capability>) {
    const next = { ...cap, ...changes };
    setSaving(cap.tool_name);
    try {
      const res = await apiFetch('/api/mcp/capabilities', {
        method: 'PUT',
        body: JSON.stringify({
          kind: next.kind,
          target: next.target,
          action: next.action,
          enabled: next.enabled,
          scopes: next.scopes,
        }),
      });
      if (!res.ok) {
        const d = await res.json();
        setError(d.detail || 'Could not save.');
        return;
      }
      setCaps((prev) =>
        prev.map((c) =>
          c.tool_name === cap.tool_name && c.kind === cap.kind ? next : c));
      setError('');
    } finally {
      setSaving('');
    }
  }

  function toggleScope(cap: Capability, scope: string) {
    const scopes = cap.scopes.includes(scope)
      ? cap.scopes.filter((s) => s !== scope)
      : [...cap.scopes, scope];
    save(cap, { scopes, enabled: cap.enabled && scopes.length > 0 });
  }

  if (loading) return <p>Loading…</p>;

  const connectors = caps.filter((c) => c.kind === 'connector');
  const playbooks = caps.filter((c) => c.kind === 'playbook');
  const exposableConnectors = connectors.filter((c) => c.exposable);
  const nonExposable = connectors.filter((c) => !c.exposable);

  return (
    <div style={{ maxWidth: 900 }}>
      <h3 style={{ margin: '0 0 0.25rem', fontSize: '1.1rem' }}>MCP — external AI access</h3>
      <p style={{ color: '#666', fontSize: '0.85rem', margin: '0 0 1rem' }}>
        Choose which Norm capabilities Claude (and other MCP clients) can use on
        behalf of a signed-in user. Read tools return data; workflow tools run a
        playbook and create drafts for approval in Norm.
      </p>
      {error && <p style={{ color: '#c0392b', fontSize: '0.85rem' }}>{error}</p>}

      <Section title={`Workflow tools (${playbooks.filter((p) => p.enabled).length} of ${playbooks.length} enabled)`}>
        {playbooks.map((c) => (
          <Row key={c.tool_name} cap={c} scopes={scopes} scopeLabel={scopeLabel}
               saving={saving === c.tool_name} onToggleEnabled={() => save(c, { enabled: !c.enabled })}
               onToggleScope={(s) => toggleScope(c, s)} />
        ))}
      </Section>

      <Section title={`Read tools (${exposableConnectors.filter((c) => c.enabled).length} of ${exposableConnectors.length} enabled)`}>
        {exposableConnectors.map((c) => (
          <Row key={c.tool_name} cap={c} scopes={scopes} scopeLabel={scopeLabel}
               saving={saving === c.tool_name} onToggleEnabled={() => save(c, { enabled: !c.enabled })}
               onToggleScope={(s) => toggleScope(c, s)} />
        ))}
      </Section>

      {nonExposable.length > 0 && (
        <Section title={`Not exposable (${nonExposable.length})`}>
          <p style={{ fontSize: '0.8rem', color: '#999', margin: '0 0 0.5rem' }}>
            These can&apos;t be direct MCP tools — write actions must go through a
            workflow, and some are conversation-only.
          </p>
          {nonExposable.map((c) => (
            <div key={c.tool_name} style={{ padding: '0.5rem 0', borderBottom: '1px solid #f0ece6', opacity: 0.7 }}>
              <code style={{ fontSize: '0.8rem' }}>{c.tool_name}</code>
              <span style={{ marginLeft: 8, fontSize: '0.72rem', color: '#a0522d' }}>{c.method}</span>
              <div style={{ fontSize: '0.75rem', color: '#999' }}>{c.reason}</div>
            </div>
          ))}
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '2rem' }}>
      <h4 style={{ fontSize: '0.8rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.75rem' }}>{title}</h4>
      {children}
    </div>
  );
}

function Row({ cap, scopes, scopeLabel, saving, onToggleEnabled, onToggleScope }: {
  cap: Capability; scopes: Scope[]; scopeLabel: Record<string, string>;
  saving: boolean; onToggleEnabled: () => void; onToggleScope: (s: string) => void;
}) {
  const relevant = scopes.filter((s) =>
    cap.grantable_scopes.includes(s.name) &&
    (cap.access === 'draft' ? true : s.access_level === 'read'));
  return (
    <div style={{ padding: '0.6rem 0', borderBottom: '1px solid #f0ece6', opacity: saving ? 0.6 : 1 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
        <input type="checkbox" checked={cap.enabled} onChange={onToggleEnabled}
               disabled={saving || cap.scopes.length === 0 && !cap.enabled} />
        <code style={{ fontSize: '0.82rem', fontWeight: 600 }}>{cap.tool_name}</code>
        {cap.access === 'draft' && <span style={{ fontSize: '0.68rem', background: '#f4e8d8', color: '#8a6d3b', padding: '1px 6px', borderRadius: 4 }}>draft</span>}
      </div>
      <div style={{ fontSize: '0.78rem', color: '#777', margin: '0.2rem 0 0.35rem 1.6rem' }}>{cap.description}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginLeft: '1.6rem' }}>
        {relevant.map((s) => (
          <label key={s.name} style={{ fontSize: '0.72rem', display: 'flex', alignItems: 'center', gap: '0.25rem', cursor: 'pointer' }}>
            <input type="checkbox" checked={cap.scopes.includes(s.name)} onChange={() => onToggleScope(s.name)} disabled={saving} />
            {scopeLabel[s.name] || s.name}
          </label>
        ))}
        {relevant.length === 0 && <span style={{ fontSize: '0.72rem', color: '#bbb' }}>no grantable scopes</span>}
      </div>
    </div>
  );
}
