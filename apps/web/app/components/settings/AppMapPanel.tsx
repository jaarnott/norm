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
import { apiFetch } from '../../lib/api';
import type { TeamApp } from '../../hooks/useTeam';

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

const TYPE_STYLE: Record<string, { color: string; bg: string }> = {
  consolidator: { color: '#2e7d4f', bg: '#eef6f0' },
  'norm function': { color: '#2e5a7d', bg: '#e8f0f6' },
  raw: { color: '#b04a4a', bg: '#fbecec' },
  'CB tool': { color: '#8a5a1f', bg: '#fdf3e4' },
  backend: { color: '#6b6b6b', bg: '#f0ebe5' },
};

const cell: React.CSSProperties = { padding: '6px 8px', verticalAlign: 'top', borderTop: '1px solid #eee', fontSize: '0.7rem' };
const ul: React.CSSProperties = { margin: 0, paddingLeft: 14 };

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

  if (error) return <div style={{ color: '#b04a4a', fontSize: '0.8rem' }}>{error}</div>;
  if (!data) return <div style={{ color: '#8a8a8a', fontSize: '0.8rem' }}>Loading the App Map…</div>;

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

  const list = (items: React.ReactNode[]) => items.length
    ? <ul style={ul}>{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    : <span style={{ color: '#bbb' }}>—</span>;

  return (
    <div style={{ width: '100%' }}>
      <h3 style={{ margin: '0 0 0.3rem', fontSize: '0.9rem' }}>App Map</h3>
      <p style={{ fontSize: '0.72rem', color: '#8a8a8a', margin: '0 0 0.8rem' }}>
        Every App, the team member it belongs to, and everything it owns — derived live from the catalog.
        Each tool, component and skill belongs to exactly one App (Norm Core is the one App shared by every
        member). Edit through <code>scripts/sync_marketplace_catalog.py</code>.
      </p>

      <div style={{
        border: `1px solid ${problems.length ? '#e8b4b4' : '#bcd9c6'}`, background: problems.length ? '#fbecec' : '#eef6f0',
        borderRadius: 8, padding: '0.6rem 0.8rem', marginBottom: '0.8rem', fontSize: '0.74rem',
      }}>
        {!f.armed ? (
          <span style={{ color: '#8a5a1f' }}>Tool ownership isn&apos;t armed yet — no App declares its tools, so the ownership checks are dormant.</span>
        ) : problems.length === 0 ? (
          <span style={{ color: '#2e7d4f', fontWeight: 600 }}>✓ Everything is owned by exactly one App.</span>
        ) : (
          <>
            <div style={{ fontWeight: 700, color: '#b04a4a', marginBottom: 4 }}>Unassigned or conflicting ({problems.length})</div>
            <ul style={ul}>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, marginBottom: '0.6rem', fontSize: '0.72rem', alignItems: 'center' }}>
        <label>Member{' '}
          <select value={member} onChange={(e) => setMember(e.target.value)} style={{ fontSize: '0.72rem' }}>
            <option value="">all</option>
            {members.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label>Needs{' '}
          <select value={connection} onChange={(e) => setConnection(e.target.value)} style={{ fontSize: '0.72rem' }}>
            <option value="">anything</option>
            {connections.map((c) => <option key={c} value={c}>{names[c] ?? c}</option>)}
          </select>
        </label>
        <span style={{ color: '#8a8a8a' }}>{apps.length} Apps</span>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', background: '#fff', border: '1px solid #e2ddd7' }}>
          <thead>
            <tr style={{ background: '#faf8f5', textAlign: 'left', fontSize: '0.68rem', color: '#6b6b6b' }}>
              {['App', 'Member', 'Tools', 'Menu pages', 'Chat components', 'Skills', 'Needs'].map((h) => (
                <th key={h} style={{ padding: '6px 8px' }}>{h}</th>
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
                    <strong>{a.name}</strong>{!a.switchable && ' ✦'}
                    <div style={{ color: '#aaa', fontFamily: 'monospace', fontSize: '0.62rem' }}>{a.slug}</div>
                  </td>
                  <td style={cell}>{a.member_name ?? a.member ?? <span style={{ color: '#b04a4a' }}>none</span>}</td>
                  <td style={cell}>{list(a.tools.map((t) => {
                    const st = TYPE_STYLE[t.type] ?? TYPE_STYLE.backend;
                    return (
                      <span key={t.key}>
                        <span style={{ fontFamily: 'monospace' }}>{t.key}</span>{' '}
                        <span style={{ fontSize: '0.58rem', fontWeight: 700, color: st.color, background: st.bg, borderRadius: 6, padding: '0 5px' }}>{t.type}</span>
                        {t.writes && <span style={{ fontSize: '0.58rem', color: '#8a5a1f' }}> · writes</span>}
                        {!t.exists && <span style={{ fontSize: '0.58rem', color: '#b04a4a' }}> · MISSING</span>}
                        {t.engine_only && <span style={{ fontSize: '0.58rem', color: '#b04a4a' }}> · engine-only</span>}
                      </span>
                    );
                  }))}</td>
                  <td style={cell}>{list([
                    ...pages.map((c) => <span key={c.key}>{c.shared ? `${c.label} (every member)` : c.label} <span style={{ color: '#aaa' }}>({c.key})</span></span>),
                    ...(a.app_platform && !appComps.some((c) => c.app_page)
                      ? [<span key="screen">{a.name} <span style={{ color: '#aaa' }}>(App-platform screen)</span></span>] : []),
                    ...appComps.filter((c) => c.app_page).map((c) => <span key={c.key}>{c.label} <span style={{ color: '#aaa' }}>(App-platform, {c.key})</span></span>),
                  ])}</td>
                  <td style={cell}>{list([
                    ...chat.map((c) => <span key={c.key} style={{ fontFamily: 'monospace' }}>{c.key}</span>),
                    ...appComps.map((c) => (
                      <span key={`open-${c.key}`}>
                        <span style={{ fontFamily: 'monospace' }}>{c.key}</span>{' '}
                        <span style={{ color: '#8a8a8a' }}>opens at: {(c.inputs ?? []).map((i) => i.name).join(', ') || 'start only'}</span>
                      </span>
                    )),
                  ])}</td>
                  <td style={cell}>{list(a.skills.map((s) => <span key={s.slug}>{s.label}{s.enabled === false && <span style={{ color: '#aaa' }}> (disabled)</span>}</span>))}</td>
                  <td style={cell}>{a.required_connections.length
                    ? list(a.required_connections.map((c) => <span key={c.connector}>{c.display_name}</span>))
                    : <span style={{ color: '#8a8a8a' }}>runs on Norm</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
