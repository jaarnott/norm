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
import { MousePointerClick } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';

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
  suggested_scopes: string[];
  ui: { resource: string; component: string | null; name: string } | null;
  exposable: boolean;
  reason: string | null;
}
interface Scope { name: string; label: string; access_level: string; }

// Checkboxes tick in the accent, as on the approval card; tool names are mono
// and may break anywhere so a long one can't push a phone row sideways.
const checkboxStyle: React.CSSProperties = { width: 16, height: 16, margin: 0, flex: '0 0 auto', accentColor: 'var(--accent)', cursor: 'pointer' };
const codeStyle: React.CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)', overflowWrap: 'anywhere' };

/** Marks a tool that renders a real Norm component inside Claude. */
function InteractiveBadge({ title }: { title?: string }) {
  return (
    <Badge title={title}>
      <Icon icon={MousePointerClick} size={12} />
      Interactive
    </Badge>
  );
}

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

  // Turning a tool on shouldn't be a two-step scope-then-enable dance. If it
  // has no scopes yet, apply its suggested (natural) scope so one click does
  // the sensible thing; the admin can still refine under "Adjust permissions".
  function toggleEnabled(cap: Capability) {
    if (cap.enabled) {
      save(cap, { enabled: false }); // keep scopes, just switch off
      return;
    }
    const scopes = cap.scopes.length
      ? cap.scopes
      : cap.suggested_scopes;
    if (!scopes.length) {
      setError(
        `${cap.tool_name}: pick a permission under "Adjust permissions" first.`
      );
      return;
    }
    save(cap, { enabled: true, scopes });
  }

  // Ticking a scope enables the tool; unticking the last one turns it off.
  function toggleScope(cap: Capability, scope: string) {
    const scopes = cap.scopes.includes(scope)
      ? cap.scopes.filter((s) => s !== scope)
      : [...cap.scopes, scope];
    save(cap, { scopes, enabled: scopes.length > 0 });
  }

  if (loading) return <PageState kind="loading" title="Loading MCP capabilities…" />;

  const connectors = caps.filter((c) => c.kind === 'connector');
  const playbooks = caps.filter((c) => c.kind === 'playbook');
  const exposableConnectors = connectors.filter((c) => c.exposable);
  const nonExposable = connectors.filter((c) => !c.exposable);

  return (
    <div style={{ maxWidth: 900, lineHeight: 1.45 }}>
      <h3 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>MCP — external AI access</h3>
      <p style={{ margin: '4px 0 8px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        Choose which Norm capabilities Claude (and other MCP clients) can use on
        behalf of a signed-in user. Read tools return data; workflow tools run a
        playbook and create drafts for approval in Norm.
      </p>
      <p style={{ margin: '0 0 20px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        Anything marked <InteractiveBadge />{' '}
        renders a real Norm component inside Claude that the user can act on.
        Everything else returns plain data, which Claude lays out itself — that&apos;s
        deliberate, it formats tables and charts better than we can embed them.
      </p>
      {error && (
        <div style={{ marginBottom: 20 }}>
          <PageState kind="error" title={error} />
        </div>
      )}

      {/* A failed load shows only the error above, never empty sections. */}
      {caps.length === 0 && !error && (
        <PageState kind="empty" title="No capabilities to expose yet." />
      )}

      {caps.length > 0 && (
        <>
          <Section title="Workflow tools" meta={`${playbooks.filter((p) => p.enabled).length} of ${playbooks.length} enabled`}>
            {playbooks.map((c, i) => (
              <Row key={c.tool_name} cap={c} scopes={scopes} scopeLabel={scopeLabel} divider={i > 0}
                   saving={saving === c.tool_name} onToggleEnabled={() => toggleEnabled(c)}
                   onToggleScope={(s) => toggleScope(c, s)} />
            ))}
          </Section>

          <Section title="Read tools" meta={`${exposableConnectors.filter((c) => c.enabled).length} of ${exposableConnectors.length} enabled`}>
            {exposableConnectors.map((c, i) => (
              <Row key={c.tool_name} cap={c} scopes={scopes} scopeLabel={scopeLabel} divider={i > 0}
                   saving={saving === c.tool_name} onToggleEnabled={() => toggleEnabled(c)}
                   onToggleScope={(s) => toggleScope(c, s)} />
            ))}
          </Section>
        </>
      )}

      {nonExposable.length > 0 && (
        <Section
          title="Not exposable"
          meta={`${nonExposable.length} ${nonExposable.length === 1 ? 'tool' : 'tools'}`}
          intro={<>
            These can&apos;t be direct MCP tools — write actions must go through a
            workflow, and some are conversation-only.
          </>}
        >
          {nonExposable.map((c, i) => (
            <div key={c.tool_name} style={{ padding: '10px 16px', borderTop: i > 0 ? '1px solid var(--line)' : 'none' }}>
              <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, rowGap: 4 }}>
                <code style={{ ...codeStyle, color: 'var(--text-soft)' }}>{c.tool_name}</code>
                <Badge>{c.method}</Badge>
              </div>
              <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{c.reason}</div>
            </div>
          ))}
        </Section>
      )}
    </div>
  );
}

/** A titled group of rows in one card; renders no empty card. */
function Section({ title, meta, intro, children }: {
  title: string; meta?: string; intro?: React.ReactNode; children: React.ReactNode;
}) {
  const hasRows = Array.isArray(children) ? children.length > 0 : !!children;
  return (
    <section style={{ marginBottom: 28 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: 8, rowGap: 2, marginBottom: 8 }}>
        <h4 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h4>
        {meta && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta}</span>}
      </div>
      {intro && <p style={{ margin: '0 0 8px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{intro}</p>}
      {hasRows && <div className="n-card" style={{ overflow: 'hidden' }}>{children}</div>}
    </section>
  );
}

function Row({ cap, scopes, scopeLabel, saving, divider, onToggleEnabled, onToggleScope }: {
  cap: Capability; scopes: Scope[]; scopeLabel: Record<string, string>;
  saving: boolean; divider: boolean; onToggleEnabled: () => void; onToggleScope: (s: string) => void;
}) {
  const relevant = scopes.filter((s) =>
    cap.grantable_scopes.includes(s.name) &&
    (cap.access === 'draft' ? true : s.access_level === 'read'));

  // Which permission(s) this tool will use once enabled: what's granted now,
  // else its natural default. This is the line that answers "why HR on a POS
  // tool?" — it shows only the scope that fits, not the whole vocabulary.
  const effective = cap.scopes.length ? cap.scopes : cap.suggested_scopes;
  const permissionText = effective.length
    ? effective.map((s) => scopeLabel[s] || s).join(', ')
    : (relevant.length ? 'choose one under “Adjust permissions”' : 'no permission fits this tool yet');

  const [showScopes, setShowScopes] = useState(false);

  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '12px 16px', borderTop: divider ? '1px solid var(--line)' : 'none', opacity: saving ? 0.6 : 1 }}>
      <input type="checkbox" checked={cap.enabled} onChange={onToggleEnabled}
             aria-label={`Expose ${cap.tool_name}`}
             disabled={saving || relevant.length === 0}
             style={{ ...checkboxStyle, marginTop: 2, cursor: saving || relevant.length === 0 ? 'default' : 'pointer' }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6, rowGap: 4 }}>
          <code style={{ ...codeStyle, fontWeight: 600, color: 'var(--text)', marginRight: 2 }}>{cap.tool_name}</code>
          {cap.access === 'draft' && <Badge tone="info">Draft</Badge>}
          {cap.ui && (
            <InteractiveBadge title={`Renders the ${cap.ui.name} component in Claude${cap.ui.component ? ` (${cap.ui.component})` : ''} instead of plain data`} />
          )}
        </div>
        <div style={{ margin: '4px 0 6px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{cap.description}</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', columnGap: 12, rowGap: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          <span>Permission: <span style={{ color: cap.enabled ? 'var(--ok)' : 'var(--muted)', fontWeight: cap.enabled ? 600 : 400 }}>{permissionText}</span></span>
          {relevant.length > 0 && (
            <Button variant="link" onClick={() => setShowScopes((v) => !v)} style={{ fontSize: 'var(--fs-xs)' }}>
              {showScopes ? 'Hide permissions' : 'Adjust permissions'}
            </Button>
          )}
        </div>
        {showScopes && (
          <div style={{ display: 'flex', flexWrap: 'wrap', columnGap: 16, rowGap: 6, marginTop: 8 }}>
            {relevant.map((s) => (
              <label key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text)', cursor: 'pointer' }}>
                <input type="checkbox" checked={cap.scopes.includes(s.name)} onChange={() => onToggleScope(s.name)} disabled={saving}
                       style={checkboxStyle} />
                {scopeLabel[s.name] || s.name}
                {cap.suggested_scopes.includes(s.name) && <span style={{ color: 'var(--ok)' }}>· suggested</span>}
              </label>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
