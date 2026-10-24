'use client';

/**
 * Admin App Map — the platform's Apps, live. Every App, the ONE AI agent it
 * belongs to (Norm Core: all), its screens by the system each is for, the
 * tools it owns and the ones it shares from other Apps, its skills and what it
 * works with — derived by the server from the catalog and specs on every load,
 * never a hand-kept copy. The problems box on top is the validator's findings
 * made visible. The Connections tab lists the systems Apps work with.
 * docs/plans/agents-and-apps-restructure.md.
 */

import { Fragment, useEffect, useMemo, useState } from 'react';
import { CircleCheck, TriangleAlert } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import type { TeamApp, TeamTool } from '../../hooks/useTeam';
import { placementNote, teamName } from '../team/teamApps';
import AppIcon from '../ui/AppIcon';
import Badge, { type BadgeTone } from '../ui/Badge';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';
import Tabs from '../ui/Tabs';

interface MapApp extends TeamApp { member_name?: string }
interface Findings {
  armed: boolean;
  unowned_tools: string[];
  unowned_components: string[];
  unowned_skills: string[];
  double_claims: { kind: string; key: string; apps: string[] }[];
  unknown_components: string[];
  bad_members: { app: string; member: string | null; problem: string }[];
  apps_without_screen?: string[];
  unowned_uses?: string[];
  /** Team apps (docs/plans/team-apps-skeleton.md §3.3): a team row with no
   *  screen placed on it yet (expected before the Kitchen build), a screen
   *  naming a team app the catalog lacks ("app/page → team"), a team tool
   *  no App owns. */
  team_apps_without_screens?: string[];
  unknown_team?: string[];
  team_tools_unowned?: string[];
}
interface MapSystem { key: string; name: string; status: 'live' | 'built_in' | 'coming_soon'; apps: string[] }
/** A team app as the App Map lists it: every screen placed on it, every
 *  tool it names — the platform's layout, switches and systems ignored. */
interface MapTeamScreen { page_id: string; label: string; app_slug: string; app_name: string; member: string | null; teams?: string[]; team_only: boolean }
interface MapTeamApp { slug: string; name: string; icon?: string | null; tagline: string; tools: TeamTool[]; screens: MapTeamScreen[] }

// An App claims TOOLS — consolidators and built-ins. An endpoint (or a
// missing row) in a claim is a problem, shown in red.
const TYPE_STYLE: Record<string, { tone: BadgeTone; label: string }> = {
  consolidator: { tone: 'ok', label: 'Consolidator' },
  'built-in': { tone: 'info', label: 'Built-in' },
  endpoint: { tone: 'error', label: 'Endpoint' },
  missing: { tone: 'error', label: 'Missing' },
};
const SYSTEM_STATUS: Record<MapSystem['status'], { tone: BadgeTone; label: string }> = {
  live: { tone: 'ok', label: 'Live' },
  built_in: { tone: 'info', label: 'Built into Norm' },
  coming_soon: { tone: 'neutral', label: 'Coming soon' },
};

// The findings box: dormant (not armed), all owned, or problems.
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
  const [data, setData] = useState<{ apps: MapApp[]; systems?: MapSystem[]; team_apps?: MapTeamApp[]; findings: Findings } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<'apps' | 'connections'>('apps');
  const [member, setMember] = useState('');
  const [worksWith, setWorksWith] = useState('');

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
    (!worksWith || (a.works_with ?? []).includes(worksWith))), [data, member, worksWith]);

  const header = (
    <>
      <h3 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>App map</h3>
      <p style={{ margin: '2px 0 16px', maxWidth: 760, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        Every App, the AI agent it belongs to, its screens by the system each is for, and its tools and skills —
        derived live from the catalog. Each tool is owned by one App and may be shared by others; a hired agent
        gets every tool of its Apps. Edit through <code style={inlineCode}>scripts/sync_marketplace_catalog.py</code>.
      </p>
    </>
  );

  if (error) return <div style={root}>{header}<PageState kind="error" title={error} /></div>;
  if (!data) return <div style={root}>{header}<PageState kind="loading" title="Loading the App Map…" /></div>;

  const f = data.findings;
  const teamApps = data.team_apps ?? [];
  const problems = [
    ...f.unowned_tools.map((k) => `Tool ${k} belongs to no App`),
    ...(f.unowned_uses ?? []).map((k) => `Tool ${k} is shared by an App, but no App owns it`),
    ...f.unowned_components.map((k) => `Component ${k} belongs to no App`),
    ...f.unowned_skills.map((k) => `Skill ${k} belongs to no App`),
    ...f.double_claims.map((d) => `${d.kind} ${d.key} is owned by ${d.apps.join(' and ')}`),
    ...f.unknown_components.map((k) => `Component ${k} isn't in the web registry`),
    ...f.bad_members.map((b) => `${b.app}: ${b.problem}`),
    ...(f.apps_without_screen ?? []).map((k) => `${k} has no screen`),
    ...(f.unknown_team ?? []).map((k) => `Screen ${k} is placed on a team app the catalog doesn't have`),
    ...(f.team_tools_unowned ?? []).map((k) => `Team tool ${k} is owned by no App`),
    ...(f.team_apps_without_screens ?? []).map((k) => `Team app ${teamName(teamApps, k)} has no screens placed on it yet`),
  ];
  const members = Array.from(new Set(data.apps.filter((a) => a.member && a.member !== '*').map((a) => a.member as string)));
  const memberNames: Record<string, string> = {};
  for (const a of data.apps) if (a.member && a.member_name) memberNames[a.member] = a.member_name;
  const systems = Array.from(new Set(data.apps.flatMap((a) => a.works_with ?? []))).sort();
  const appName: Record<string, string> = Object.fromEntries(data.apps.map((a) => [a.slug, a.name]));

  const list = (items: React.ReactNode[]) => items.length
    ? <ul style={ul}>{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    : <span style={{ color: 'var(--muted)' }}>—</span>;

  const callout = CALLOUT[!f.armed ? 'warn' : problems.length ? 'error' : 'ok'];

  /** Screens grouped by the system each is for; one a team app also shows
   *  says "· also in Kitchen" or "· Kitchen only". */
  const screens = (a: MapApp) => {
    const all = a.screens ?? [];
    if (!all.length) {
      const shared = a.components.filter((c) => c.shared);
      return list(shared.map((c) => <span key={c.key}>{c.label} <span style={meta}>(every agent)</span></span>));
    }
    const groups = new Map<string, React.ReactNode[]>();
    for (const s of all) {
      const key = s.connection_name ?? 'Any';
      const note = placementNote(s, teamApps, { inline: true });
      groups.set(key, [...(groups.get(key) ?? []), (
        <span key={s.page_id}>{s.label}{note && <span style={meta}> · {note}</span>}</span>
      )]);
    }
    return list([...groups.entries()].map(([system, items]) => (
      <span key={system}>
        <span style={meta}>With {system}:</span>{' '}
        {items.map((item, i) => <Fragment key={i}>{i > 0 && ', '}{item}</Fragment>)}
      </span>
    )));
  };

  const tools = (items: TeamTool[]) => list(items.map((t) => {
    const st = TYPE_STYLE[t.type];
    return (
      <span key={t.key}>
        <span style={mono}>{t.key}</span>{' '}
        <Badge tone={st?.tone ?? 'error'}>{st?.label ?? t.type}</Badge>
        {t.owned === false && <> <Badge tone="info" title="Owned by another App, shared with this one">Shared</Badge></>}
        {t.owned !== false && (t.shared_with?.length ?? 0) > 0 && (
          <span style={{ ...flag, color: 'var(--muted)' }}> · also in {(t.shared_with ?? []).map((s) => appName[s] ?? s).join(', ')}</span>
        )}
        {t.writes && <span style={{ ...flag, color: 'var(--warn)' }}> · writes</span>}
        {!t.exists && <span style={{ ...flag, fontWeight: 600, color: 'var(--error)' }}> · missing</span>}
        {t.engine_only && <span style={{ ...flag, color: 'var(--error)' }}> · engine-only</span>}
      </span>
    );
  }));

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
            <span style={{ fontWeight: 600 }}>Everything has an owner, and every App has a screen.</span>
          ) : (
            <>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Problems ({problems.length})</div>
              <ul style={{ ...ul, color: 'var(--text)' }}>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
            </>
          )}
        </div>
      </div>

      <div style={{ marginBottom: 12 }}>
        <Tabs
          label="App map"
          items={[{ id: 'apps', label: 'Apps' }, { id: 'connections', label: 'Connections' }]}
          value={tab}
          onChange={(id) => setTab(id as 'apps' | 'connections')}
        />
      </div>

      {tab === 'connections' ? (
        <div className="n-card" style={{ overflowX: 'auto' }}>
          <table className="n-table">
            <thead><tr>{['Connection', 'Status', 'Apps that work with it'].map((h) => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>
              {(data.systems ?? []).map((s) => (
                <tr key={s.key}>
                  <td style={cell}><strong style={{ fontWeight: 600 }}>{s.name}</strong></td>
                  <td style={cell}><Badge tone={SYSTEM_STATUS[s.status].tone}>{SYSTEM_STATUS[s.status].label}</Badge></td>
                  <td style={cell}>{s.apps.length ? s.apps.join(', ') : <span style={meta}>None yet</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            <label className="n-label" style={filterLabel}>AI agent
              <select className="n-select" value={member} onChange={(e) => setMember(e.target.value)}>
                <option value="">All</option>
                {members.map((m) => <option key={m} value={m}>{memberNames[m] ?? m}</option>)}
              </select>
            </label>
            <label className="n-label" style={filterLabel}>Works with
              <select className="n-select" value={worksWith} onChange={(e) => setWorksWith(e.target.value)}>
                <option value="">Anything</option>
                {systems.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <span style={meta}>{apps.length} Apps</span>
          </div>

          <div className="n-card" style={{ overflowX: 'auto' }}>
            <table className="n-table" style={{ minWidth: 1100 }}>
              <thead>
                <tr>
                  {['App', 'AI agent', 'Screens', 'Tools', 'Chat components', 'Skills', 'Works with'].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {apps.map((a) => {
                  const chat = a.components.filter((c) => !c.page && !c.shared && c.inputs === undefined);
                  const appComps = a.components.filter((c) => c.inputs !== undefined);
                  return (
                    <tr key={a.slug}>
                      <td style={cell}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
                          <strong style={{ fontWeight: 600 }}>{a.name}</strong>
                          {!a.switchable && <Badge title="Always on while its agent is hired">Always on</Badge>}
                        </div>
                        <div style={{ ...mono, fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>{a.slug}</div>
                      </td>
                      <td style={cell}>{a.member_name ?? a.member ?? <Badge tone="error">None</Badge>}</td>
                      <td style={cell}>{screens(a)}</td>
                      <td style={cell}>{tools(a.tools)}</td>
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
                      <td style={cell}>{(a.works_with ?? []).length
                        ? list((a.works_with ?? []).map((w) => <span key={w} style={{ whiteSpace: 'nowrap' }}>{w}</span>))
                        : <span style={meta}>runs on Norm</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Team apps — a second place a job app's screens show (docs/plans/
              team-apps-skeleton.md). Absent until the catalog has team rows. */}
          {teamApps.length > 0 && (
            <div style={{ marginTop: 24 }} data-testid="map-team-apps">
              <h4 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>Team apps</h4>
              <p style={{ margin: '2px 0 12px', maxWidth: 760, ...meta }}>
                What venue staff open on a phone or the kitchen tablet. A team app owns nothing: its screens are the Apps&apos;
                screens placed in it (a component&apos;s <code style={inlineCode}>teams</code> list — the same screen in every team
                app it names, switched per team app), and its tools are the ones Norm may use for a role that reaches only this
                team app (<code style={inlineCode}>uses_tools</code>). Switches and systems are ignored here; who sees each one is
                the role.
              </p>
              <div className="n-card" style={{ overflowX: 'auto' }}>
                <table className="n-table" style={{ minWidth: 760 }}>
                  <thead>
                    <tr>{['Team app', 'Screens', 'Tools for Norm'].map((h) => <th key={h}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {teamApps.map((t) => (
                      <tr key={t.slug} data-testid={`map-team-${t.slug}`}>
                        <td style={cell}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <AppIcon app={{ slug: t.slug, icon: t.icon }} size="menu" />
                            <strong style={{ fontWeight: 600 }}>{t.name}</strong>
                          </div>
                          <div style={{ ...mono, fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>{t.slug}</div>
                          {t.tagline && <div style={{ ...meta, maxWidth: 280 }}>{t.tagline}</div>}
                        </td>
                        <td style={cell}>
                          {t.screens.length ? list(t.screens.map((s) => (
                            <span key={`${s.app_slug}/${s.page_id}`}>
                              {s.label}
                              <span style={meta}>
                                {' '}· from {s.app_name}{s.member ? ` (${memberNames[s.member] ?? s.member})` : ''}
                                {s.team_only ? ` · ${placementNote(s, teamApps, { inline: true })}` : ''}
                              </span>
                            </span>
                          ))) : <span style={meta}>No screens yet</span>}
                        </td>
                        <td style={cell}>{t.tools.length ? tools(t.tools) : <span style={meta}>No tools for Norm yet</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
