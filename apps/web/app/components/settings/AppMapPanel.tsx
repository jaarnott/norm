'use client';

/**
 * Admin App Map — the platform's App table, live (Apps v3). Every App, the
 * ONE team member it's bound to (Norm Core: all), and the tools, components,
 * skills and connections it owns — derived by the server from the catalog and
 * specs on every load, never a hand-kept copy. The Unassigned panel on top is
 * the validator's ownership findings made visible: anything owned by no App,
 * claimed twice, or bound to a member that doesn't exist.
 */

import { useEffect, useMemo, useState } from 'react';
import { CircleCheck, TriangleAlert } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import type { TeamApp } from '../../hooks/useTeam';
import Badge, { type BadgeTone } from '../ui/Badge';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';

interface MapApp extends TeamApp { member_name?: string }
interface Findings {
  armed: boolean;
  unowned_tools: string[];
  unowned_components: string[];
  unowned_skills: string[];
  double_claims: { kind: string; key: string; apps: string[] }[];
  unknown_components: string[];
  bad_members: { app: string; member: string | null; problem: string }[];
}

// Sep 2026: an App claims TOOLS — consolidators and built-ins. An endpoint
// (or a missing row) in a claim is a problem, shown in red.
const TYPE_STYLE: Record<string, { tone: BadgeTone; label: string }> = {
  consolidator: { tone: 'ok', label: 'Consolidator' },
  'built-in': { tone: 'info', label: 'Built-in' },
  endpoint: { tone: 'error', label: 'Endpoint' },
  missing: { tone: 'error', label: 'Missing' },
};

// The ownership findings box: dormant (not armed), all owned, or problems.
const CALLOUT = {
  warn: { color: 'var(--warn)', background: 'var(--warn-bg)', icon: TriangleAlert },
  ok: { color: 'var(--ok)', background: 'var(--ok-bg)', icon: CircleCheck },
  error: { color: 'var(--error)', background: 'var(--error-bg)', icon: TriangleAlert },
} as const;

const root: React.CSSProperties = { width: '100%', lineHeight: 1.45 };
const cell: React.CSSProperties = { verticalAlign: 'top' };
const ul: React.CSSProperties = { margin: 0, paddingLeft: 18 };
const mono: React.CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' };
const meta: React.CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const flag: React.CSSProperties = { fontSize: 'var(--fs-xs)' };
const filterLabel: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 8, margin: 0 };
const inlineCode: React.CSSProperties = {
  fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', background: 'var(--surface-alt)',
  padding: '1px 4px', borderRadius: 'var(--radius-sm)', color: 'var(--text-soft)',
};

export default function AppMapPanel() {
  const [data, setData] = useState<{ apps: MapApp[]; findings: Findings } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [member, setMember] = useState('');
  const [connection, setConnection] = useState('');

  useEffect(() => {
    apiFetch('/api/admin/app-map')
      .then(async (r) => {
        if (!r.ok) throw new Error(`Couldn't load the App Map (${r.status})`);
        setData(await r.json());
      })
      .catch((e) => setError(String(e.message || e)));
  }, []);

  const apps = useMemo(() => (data?.apps ?? []).filter((a) =>
    (!member || a.member === member || (a.bound_to_all && member !== '')) &&
    (!connection || a.required_connections.some((c) => c.connector === connection))), [data, member, connection]);

  const header = (
    <>
      <h3 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>App map</h3>
      <p style={{ margin: '2px 0 16px', maxWidth: 760, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        Every App, the team member it belongs to, and everything it owns — derived live from the catalog.
        Each tool, component and skill belongs to exactly one App (Norm Core is the one App shared by every
        member). Edit through <code style={inlineCode}>scripts/sync_marketplace_catalog.py</code>.
      </p>
    </>
  );

  if (error) return <div style={root}>{header}<PageState kind="error" title={error} /></div>;
  if (!data) return <div style={root}>{header}<PageState kind="loading" title="Loading the App Map…" /></div>;

  const f = data.findings;
  const problems = [
    ...f.unowned_tools.map((k) => `Tool ${k} belongs to no App`),
    ...f.unowned_components.map((k) => `Component ${k} belongs to no App`),
    ...f.unowned_skills.map((k) => `Skill ${k} belongs to no App`),
    ...f.double_claims.map((d) => `${d.kind} ${d.key} is claimed by ${d.apps.join(' and ')}`),
    ...f.unknown_components.map((k) => `Component ${k} isn't in the web registry`),
    ...f.bad_members.map((b) => `${b.app}: ${b.problem}`),
  ];
  const members = Array.from(new Set(data.apps.filter((a) => a.member && a.member !== '*').map((a) => a.member as string)));
  const connections = Array.from(new Set(data.apps.flatMap((a) => a.required_connections.map((c) => c.connector))));
  const names: Record<string, string> = {};
  for (const a of data.apps) for (const c of a.required_connections) names[c.connector] = c.display_name;
  // The member filter shows names, not slugs (the value stays the slug).
  const memberNames: Record<string, string> = {};
  for (const a of data.apps) if (a.member && a.member_name) memberNames[a.member] = a.member_name;

  const list = (items: React.ReactNode[]) => items.length
    ? <ul style={ul}>{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    : <span style={{ color: 'var(--muted)' }}>—</span>;

  const callout = CALLOUT[!f.armed ? 'warn' : problems.length ? 'error' : 'ok'];

  return (
    <div style={root}>
      {header}

      <div style={{
        display: 'flex', alignItems: 'flex-start', gap: 10, padding: '12px 14px', marginBottom: 16,
        borderRadius: 'var(--radius)', background: callout.background, color: callout.color, fontSize: 'var(--fs-base)',
      }}>
        <Icon icon={callout.icon} size={16} style={{ marginTop: 2 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          {!f.armed ? (
            <span>Tool ownership isn&apos;t armed yet — no App declares its tools, so the ownership checks are dormant.</span>
          ) : problems.length === 0 ? (
            <span style={{ fontWeight: 600 }}>Everything is owned by exactly one App.</span>
          ) : (
            <>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Unassigned or conflicting ({problems.length})</div>
              <ul style={{ ...ul, color: 'var(--text)' }}>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
            </>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <label className="n-label" style={filterLabel}>Member
          <select className="n-select" value={member} onChange={(e) => setMember(e.target.value)}>
            <option value="">All</option>
            {members.map((m) => <option key={m} value={m}>{memberNames[m] ?? m}</option>)}
          </select>
        </label>
        <label className="n-label" style={filterLabel}>Needs
          <select className="n-select" value={connection} onChange={(e) => setConnection(e.target.value)}>
            <option value="">Anything</option>
            {connections.map((c) => <option key={c} value={c}>{names[c] ?? c}</option>)}
          </select>
        </label>
        <span style={meta}>{apps.length} Apps</span>
      </div>

      <div className="n-card" style={{ overflowX: 'auto' }}>
        <table className="n-table" style={{ minWidth: 1100 }}>
          <thead>
            <tr>
              {['App', 'Member', 'Tools', 'Menu pages', 'Chat components', 'Skills', 'Needs'].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {apps.map((a) => {
              const pages = a.components.filter((c) => c.page || c.shared);
              const appComps = a.components.filter((c) => c.inputs !== undefined);
              const chat = a.components.filter((c) => !c.page && !c.shared && c.inputs === undefined);
              return (
                <tr key={a.slug}>
                  <td style={cell}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
                      <strong style={{ fontWeight: 600 }}>{a.name}</strong>
                      {!a.switchable && <Badge title="Always on while its member is hired">Always on</Badge>}
                    </div>
                    <div style={{ ...mono, fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>{a.slug}</div>
                  </td>
                  <td style={cell}>{a.member_name ?? a.member ?? <Badge tone="error">None</Badge>}</td>
                  <td style={cell}>{list(a.tools.map((t) => {
                    const st = TYPE_STYLE[t.type];
                    return (
                      <span key={t.key}>
                        <span style={mono}>{t.key}</span>{' '}
                        <Badge tone={st?.tone ?? 'error'}>{st?.label ?? t.type}</Badge>
                        {t.writes && <span style={{ ...flag, color: 'var(--warn)' }}> · writes</span>}
                        {!t.exists && <span style={{ ...flag, fontWeight: 600, color: 'var(--error)' }}> · missing</span>}
                        {t.engine_only && <span style={{ ...flag, color: 'var(--error)' }}> · engine-only</span>}
                      </span>
                    );
                  }))}</td>
                  <td style={cell}>{list([
                    ...pages.map((c) => <span key={c.key}>{c.shared ? `${c.label} (every member)` : c.label} <span style={meta}>({c.key})</span></span>),
                    ...(a.app_platform && !appComps.some((c) => c.app_page)
                      ? [<span key="screen">{a.name} <span style={meta}>(App-platform screen)</span></span>] : []),
                    ...appComps.filter((c) => c.app_page).map((c) => <span key={c.key}>{c.label} <span style={meta}>(App-platform, {c.key})</span></span>),
                  ])}</td>
                  <td style={cell}>{list([
                    ...chat.map((c) => <span key={c.key} style={mono}>{c.key}</span>),
                    ...appComps.map((c) => (
                      <span key={`open-${c.key}`}>
                        <span style={mono}>{c.key}</span>{' '}
                        <span style={meta}>opens at: {(c.inputs ?? []).map((i) => i.name).join(', ') || 'start only'}</span>
                      </span>
                    )),
                  ])}</td>
                  <td style={cell}>{list(a.skills.map((s) => <span key={s.slug}>{s.label}{s.enabled === false && <> <Badge tone="warn">Disabled</Badge></>}</span>))}</td>
                  <td style={cell}>{a.required_connections.length
                    ? list(a.required_connections.map((c) => <span key={c.connector} style={{ whiteSpace: 'nowrap' }}>{c.display_name}</span>))
                    : <span style={meta}>runs on Norm</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
