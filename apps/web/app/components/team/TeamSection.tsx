'use client';

/**
 * Your AI team — the marketplace, rebuilt around the hierarchy
 * (Organisation → AI agents → Apps → Components → Connections).
 * It is Settings → Organisation → AI team; it has no page of its own.
 *
 * AI agents are the jobs you hire Norm to perform. Every App — Norm-built,
 * integration-backed, community or built by your team — is bound to the
 * member(s) that use it and has an enabled state. A member's card lists ALL
 * its Apps; once hired, each App switches on/off right there (owners only).
 * The "All apps" list below is the same set, flattened, naming who each App
 * is bound to. Components, tools and playbooks are never shown.
 */

import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { ChefHat, CircleUserRound, HandPlatter, Info, ShieldCheck, type LucideIcon } from 'lucide-react';
import { apiFetch, getActiveOrg, getToken } from '../../lib/api';
import { AGENTS } from '../layout/Sidebar';
import AppRunner from '../apps/AppRunner';
import AppIcon from '../ui/AppIcon';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';
import {
  TEAM_CHANGED_EVENT,
  useTeam,
  type IncludedMember,
  type TeamApp,
  type TeamAppInfo,
  type TeamMember,
} from '../../hooks/useTeam';
import { groupBySystem, placementNote, seenByLine, teamToolsLine, type RoleReach } from './teamApps';

interface PageUser { role: string; permissions?: string[] }

/** A team app's line icon when its catalog row carries none resolveAppIcon
 *  knows (docs/plans/team-apps-skeleton.md §2 gives each an emoji it maps). */
const TEAM_FALLBACK_ICON: Record<string, LucideIcon> = {
  kitchen: ChefHat,
  front_of_house: HandPlatter,
  managers: ShieldCheck,
};

interface CatalogApp {
  slug: string; name: string; description: string; tier: string;
  price_cents: number; status: string;
  composition: { app_slug?: string; agents?: string[] };
}
interface ConnVenue { venue_id: string; venue_name: string; status: string; scope?: string }

// Author marker only — no "Norm app vs integration" distinction (owner,
// 28 Sep): an App either needs connections or runs on Norm, and that's shown
// by its connection line.
const AUTHOR: Record<string, string | undefined> = {
  custom: 'Built by your team',
  community: 'Community',
};

const price = (cents: number) => `$${(cents / 100).toFixed(cents % 100 ? 2 : 0)}`;

const memberIcon = (slug: string) => AGENTS.find((a) => a.id === slug);

/* Shared styles. AI agents are never colour-coded: one neutral icon tile. */
const ICON_TILE: CSSProperties = {
  flex: '0 0 auto', width: 36, height: 36, borderRadius: 'var(--radius)',
  background: 'var(--surface-alt)', color: 'var(--text-soft)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};
const TITLE: CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
/** An AI agent's name on its card — a step below the section titles. */
const CARD_TITLE: CSSProperties = { ...TITLE, fontSize: 'var(--fs-md)' };
const INTRO: CSSProperties = { margin: '4px 0 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const META: CSSProperties = { fontSize: 'var(--fs-xs)', color: 'var(--muted)' };
const ELLIPSIS: CSSProperties = { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };
// Member cards: two columns where there's room, one on a phone.
const MEMBER_GRID: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, max(400px, calc(50% - 9px))), 1fr))', gap: 16 };

export default function TeamSection({ user }: { user?: PageUser | null }) {
  const team = useTeam(getToken());
  const [catalog, setCatalog] = useState<CatalogApp[]>([]);
  const [connInfo, setConnInfo] = useState<Record<string, ConnVenue[] | 'loading'>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [retiring, setRetiring] = useState<TeamMember | null>(null);
  const [openSlug, setOpenSlug] = useState<string | null>(null);
  const [detail, setDetail] = useState<string | null>(null);

  const isAdmin = user?.role === 'admin';
  const isOwner = isAdmin || !!user?.permissions?.includes('billing:manage');

  // The organisation's roles, for "Seen by" on each team app card: a role
  // sees a team app when its apps are open (null) or name the slug. null =
  // not loaded (no organisation, no org:read, or the fetch failed) — the
  // line is left out rather than guessed.
  const [roles, setRoles] = useState<RoleReach[] | null>(null);
  useEffect(() => {
    const orgId = getActiveOrg();
    if (!orgId) return;
    apiFetch(`/api/organizations/${encodeURIComponent(orgId)}/roles`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setRoles(d ? ((d.roles ?? []) as RoleReach[]) : null))
      .catch(() => setRoles(null));
  }, []);

  // Only for what /api/team deliberately omits: pending community submissions
  // (admin approval) and each team app's publish state.
  const loadCatalog = useCallback(() => {
    apiFetch('/api/marketplace')
      .then((r) => (r.ok ? r.json() : { apps: [] }))
      .then((d) => setCatalog(((d.apps ?? []) as CatalogApp[]).filter((a) => a.tier === 'user')))
      .catch(() => setCatalog([]));
  }, []);
  useEffect(() => { loadCatalog(); }, [loadCatalog]);

  // Per-venue readiness for every connection any App needs.
  useEffect(() => {
    const connectors = new Set<string>();
    for (const a of team.apps) for (const c of a.required_connections) connectors.add(c.connector);
    for (const conn of connectors) {
      if (connInfo[conn]) continue;
      setConnInfo((p) => ({ ...p, [conn]: 'loading' }));
      apiFetch(`/api/connectors/${conn}/connect-info`)
        .then((r) => (r.ok ? r.json() : { venues: [] }))
        .then((d) => setConnInfo((p) => ({ ...p, [conn]: d.venues ?? [] })))
        .catch(() => setConnInfo((p) => ({ ...p, [conn]: [] })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team.apps]);

  const memberName = (slug: string) =>
    team.members.find((m) => m.slug === slug)?.name
    ?? team.included.find((m) => m.slug === slug)?.name
    ?? memberIcon(slug)?.label
    ?? slug;

  const changed = () => {
    window.dispatchEvent(new CustomEvent(TEAM_CHANGED_EVENT));
    team.refresh();
    loadCatalog();
  };

  const setEntitled = async (slug: string, enabled: boolean, label: string) => {
    if (busy) return false;
    setBusy(slug);
    setNotice(null);
    try {
      const r = await apiFetch(`/api/marketplace/${encodeURIComponent(slug)}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' });
      if (r.ok) { changed(); return true; }
      if (r.status === 403) setNotice('Only organisation owners can change the team.');
      else setNotice((await r.json().catch(() => ({}))).detail || `Couldn't update ${label}`);
      return false;
    } finally {
      setBusy(null);
    }
  };

  /** Switch one of an App's screens on or off in its AI agent's menu
   *  (owners): an organisation can take hiring from BambooHR and reviews
   *  from Hospo HR (services/screens.py). Team placements are separate. */
  const setScreen = async (pageId: string, enabled: boolean, label: string) => {
    if (busy) return;
    setBusy(`screen:${pageId}`);
    setNotice(null);
    try {
      const r = await apiFetch(`/api/marketplace/screens/${encodeURIComponent(pageId)}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' });
      if (r.ok) changed();
      else if (r.status === 403) setNotice('Only organisation owners can change the team.');
      else setNotice((await r.json().catch(() => ({}))).detail || `Couldn't update ${label}`);
    } finally {
      setBusy(null);
    }
  };

  /** Switch one screen's placement in one team app on or off (owners). The
   *  same screen can be on in Kitchen and off in Front of house; the agent's
   *  menu is not touched (docs/plans/team-apps-skeleton.md §3.1). */
  const setPlacement = async (pageId: string, teamSlug: string, enabled: boolean, label: string) => {
    if (busy) return;
    setBusy(`screen:${pageId}@${teamSlug}`);
    setNotice(null);
    try {
      const r = await apiFetch(
        `/api/marketplace/screens/${encodeURIComponent(pageId)}/teams/${encodeURIComponent(teamSlug)}/${enabled ? 'enable' : 'disable'}`,
        { method: 'POST' },
      );
      if (r.ok) changed();
      else if (r.status === 403) setNotice('Only organisation owners can change the team.');
      else setNotice((await r.json().catch(() => ({}))).detail || `Couldn't update ${label}`);
    } finally {
      setBusy(null);
    }
  };

  /** "uses LoadedHub" — plus, when not every venue is connected, how many
   *  are (amber when one needs reconnecting). Per-venue detail on hover. */
  const connectionLine = (a: TeamApp): ReactNode =>
    a.required_connections.length === 0 ? 'runs on Norm' : a.required_connections.map((c, i) => {
      const info = connInfo[c.connector];
      const venues = Array.isArray(info) ? info : [];
      const connected = venues.filter((v) => v.status === 'connected').length;
      const reconnect = venues.some((v) => v.status === 'needs_reconnect');
      const note = venues.length === 0 || connected === venues.length ? null
        : connected === 0 && !reconnect ? 'not connected'
        : `${connected} of ${venues.length} venues connected`;
      return (
        <span key={c.connector} title={venues.map((v) => `${v.venue_name}: ${v.status.replace('_', ' ')}`).join('\n') || undefined}>
          {i > 0 && ' · '}uses {c.display_name}
          {note && <span style={reconnect ? { color: 'var(--warn)' } : undefined}> · {note}</span>}
        </span>
      );
    });

  const needsConnections = (apps: TeamApp[]) =>
    apps.some((a) =>
      a.enabled &&
      a.required_connections.some((c) => {
        const info = connInfo[c.connector];
        return Array.isArray(info) && info.length > 0 && info.every((v) => v.status !== 'connected');
      }),
    );

  /** On/off for an App whose member is hired (owners only). */
  const appSwitch = (a: TeamApp, live: boolean) => {
    if (!a.switchable) return <Badge>Always on</Badge>;
    if (!live) return <Badge>{a.bundled ? 'Included' : 'Optional'}</Badge>;
    return toggle(a.enabled, a.name, busy === a.slug, () => setEntitled(a.slug, !a.enabled, a.name));
  };

  /** The team page's switch: owners only. */
  const toggle = (on: boolean, label: string, isBusy: boolean, onClick: () => void) => {
    const locked = !isOwner || isBusy;
    return (
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={label}
        disabled={locked}
        onClick={onClick}
        title={isOwner ? (on ? `Switch ${label} off` : `Switch ${label} on`) : 'Only organisation owners can change the team.'}
        style={{
          position: 'relative', flex: '0 0 auto', width: 36, height: 20, padding: 0,
          border: 'none', borderRadius: 999, cursor: locked ? 'default' : 'pointer',
          background: on ? 'var(--primary)' : 'var(--bg)',
          boxShadow: on ? 'none' : 'inset 0 0 0 1px var(--field)',
          opacity: locked ? 0.45 : 1,
          transition: 'background-color 0.15s',
        }}
      >
        <span
          aria-hidden
          style={{
            position: 'absolute', borderRadius: '50%',
            ...(on
              ? { top: 2, left: 18, width: 16, height: 16, background: 'var(--on-primary)' }
              : { top: 4, left: 4, width: 12, height: 12, background: 'var(--field)' }),
            transition: 'left 0.15s, top 0.15s, width 0.15s, height 0.15s',
          }}
        />
      </button>
    );
  };

  /** A member's App list. `live` = hired, so the switches work. */
  const appList = (apps: TeamApp[], live: boolean, fallback?: LucideIcon) => (
    apps.length === 0 ? (
      <p style={{ margin: '12px 0 0', paddingTop: 8, borderTop: '1px solid var(--line-soft)', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        No apps yet — chat skills only for this role.
      </p>
    ) : (
      <ul style={{ listStyle: 'none', margin: '12px 0 0', padding: 0 }}>
        {apps.map((a, i) => (
          <li key={a.slug} style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, padding: i === apps.length - 1 ? '8px 0 0' : '8px 0', borderTop: '1px solid var(--line-soft)' }}>
            <AppIcon app={a} size="inline" fallback={fallback} />
            <span title={a.bound_to_all ? 'Shared by every AI agent' : undefined}
              style={{ ...ELLIPSIS, flex: '0 0 auto', maxWidth: '60%', fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)' }}>
              {a.name}
            </span>
            {a.kind === 'custom' && <Badge>Built by your team</Badge>}
            {a.price_cents > 0 && <span style={{ flex: '0 0 auto', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)' }}>{price(a.price_cents)}/mo</span>}
            <span style={{ ...ELLIPSIS, ...META, flex: '0 1 auto' }}>{connectionLine(a)}</span>
            <span style={{ flex: '0 0 auto', marginLeft: 'auto', display: 'inline-flex' }}>{appSwitch(a, live)}</span>
          </li>
        ))}
      </ul>
    )
  );

  /** An App's screens, grouped by the system each is for, with the agent-menu
   *  switch per screen (none for a team-only screen, which isn't in the menu). A screen for a system this organisation hasn't
   *  connected says so; one placed in team apps says "Also in Kitchen and
   *  Front of house" or "Kitchen only" (its switches there are on the team
   *  app cards below). */
  const screenList = (a: TeamApp) => {
    const groups = groupBySystem(a.screens ?? []);
    return (
      <div style={{ display: 'grid', gap: 8 }}>
        {groups.map(([system, items]) => (
          <div key={system || 'none'}>
            {groups.length > 1 && system && (
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 2 }}>With {system}</div>
            )}
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {items.map((sc) => {
                const placed = placementNote(sc, team.teamApps);
                return (
                  <li key={sc.page_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                    <span style={{ ...ELLIPSIS, flex: '1 1 auto' }}>
                      {sc.label}
                      {!sc.available && sc.connection_name && (
                        <span style={{ color: 'var(--muted)' }}> — needs {sc.connection_name}</span>
                      )}
                      {placed && <span style={{ color: 'var(--muted)' }}> · {placed}</span>}
                    </span>
                    {/* A team-only screen is not in the agent's menu, so an
                        agent-menu switch would do nothing: its switches are on
                        the team app cards below. */}
                    {!sc.team_only && (
                      <span style={{ flex: '0 0 auto', display: 'inline-flex' }}>
                        {toggle(sc.switched_on, sc.label, busy === `screen:${sc.page_id}`, () => setScreen(sc.page_id, !sc.switched_on, sc.label))}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    );
  };

  /** The screens the catalog places in a team app, from the job apps that are
   *  on: the same screen the agent's menu has, each with its own switch for
   *  this one team app (`placementsOff`). A switched-off placement stays
   *  listed, off, so it can be turned back on here; a screen whose system the
   *  organisation lacks says so. */
  const placedIn = (slug: string) => team.apps.flatMap((a) =>
    a.kind === 'app' && a.enabled
      ? (a.screens ?? []).filter((sc) => (sc.teams ?? []).includes(slug)).map((sc) => ({ ...sc, app_name: a.name, member: a.member }))
      : []);

  /** One team app's card (docs/plans/team-apps-skeleton.md §4.3): icon, name
   *  and tagline; the screens placed in it by system, each naming the job app
   *  and AI agent it comes from, with a switch for this team app alone (the
   *  owner's rule of 24 Oct: one screen, the same in every team app it is in,
   *  switched per team app — the agent's menu has its own switch in the job
   *  app's detail); what Norm may use for someone who reaches only this team
   *  app; and which roles see it. */
  const teamAppCard = (t: TeamAppInfo) => {
    const placed = placedIn(t.slug);
    const groups = groupBySystem(placed);
    return (
      <section key={t.slug} className="n-card" aria-labelledby={`team-app-${t.slug}`} data-testid={`team-card-${t.slug}`} style={{ minWidth: 0, padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
          <span style={ICON_TILE}><AppIcon app={{ slug: t.slug, icon: t.icon }} size={20} tone="inherit" fallback={TEAM_FALLBACK_ICON[t.slug]} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h3 id={`team-app-${t.slug}`} style={CARD_TITLE}>{t.name}</h3>
            {t.tagline && <p style={{ margin: '2px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{t.tagline}</p>}
          </div>
        </div>
        <div style={{ marginTop: 12, paddingTop: 8, borderTop: '1px solid var(--line-soft)' }}>
          <div className="n-eyebrow" style={{ marginBottom: 4 }}>Screens</div>
          {placed.length === 0 ? (
            <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No screens yet</p>
          ) : (
            <div style={{ display: 'grid', gap: 8 }}>
              {groups.map(([system, items]) => (
                <div key={system || 'none'}>
                  {groups.length > 1 && system && (
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 2 }}>With {system}</div>
                  )}
                  <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                    {items.map((sc) => {
                      const key = `${sc.page_id}@${t.slug}`;
                      const on = !team.placementsOff.has(key);
                      return (
                        <li key={sc.page_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                          <span style={{ ...ELLIPSIS, flex: '1 1 auto' }}>
                            {sc.label}
                            <span style={{ color: 'var(--muted)' }}>
                              {' '}· from {sc.app_name}{sc.member ? ` (${memberName(sc.member)})` : ''}
                              {!sc.available && sc.connection_name ? ` — needs ${sc.connection_name}` : ''}
                              {sc.team_only ? ` · ${placementNote(sc, team.teamApps)}` : ''}
                            </span>
                          </span>
                          <span style={{ flex: '0 0 auto', display: 'inline-flex' }}>
                            {toggle(on, `${sc.label} in ${t.name}`, busy === `screen:${key}`, () => setPlacement(sc.page_id, t.slug, !on, sc.label))}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
        <p style={{ margin: '10px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{teamToolsLine(t.tools)}</p>
        {roles && (
          <p style={{ margin: '4px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{seenByLine(roles, t.slug)}</p>
        )}
      </section>
    );
  };

  /** What an App unlocks: its menu pages, what it can do in chat, the skills
   *  it knows, the data it keeps, and what it needs connected. */
  const capabilityPanel = (a: TeamApp) => {
    const section = (title: string, body: ReactNode) => (
      <div style={{ marginTop: 12 }}>
        <div className="n-eyebrow" style={{ marginBottom: 4 }}>{title}</div>
        {body}
      </div>
    );
    const li = (children: ReactNode, key: string) => (
      <li key={key} style={{ margin: '2px 0' }}>{children}</li>
    );
    const list = (items: ReactNode) => (
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{items}</ul>
    );
    const empty = (text: string) => <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{text}</span>;
    const strong = (text: string) => <strong style={{ fontWeight: 600, color: 'var(--text)' }}>{text}</strong>;
    const pages = a.components.filter((c) => c.page || c.shared);
    // App-platform components come from the app's own version (see
    // services/app_components.py): the screens Norm can open, and where.
    const appComponents = a.components.filter((c) => c.inputs !== undefined);
    const chatComponents = a.components.filter((c) => !c.page && !c.shared && c.inputs === undefined);
    const openAt = appComponents.flatMap((c) => (c.inputs ?? []).map((i) => ({ ...i, key: `${c.key}.${i.name}` })));
    return (
      <div style={{ borderTop: '1px solid var(--line-soft)', marginTop: 10, width: '100%' }}>
        {a.description && <p style={{ margin: '10px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{a.description}</p>}
        {a.screens && a.screens.length > 0 ? section('Screens', screenList(a)) : section('Menu pages', pages.length || a.app_platform ? list(
          <>
            {pages.map((c) => li(
              <>{c.shared ? `${c.label} — in every AI agent's menu` : `${c.label} — in the ${memberName(a.member ?? '')} menu`}</>,
              c.key,
            ))}
            {a.app_platform && (appComponents.some((c) => c.app_page)
              ? appComponents.filter((c) => c.app_page).map((c) => li(<>{c.label} — in the {memberName(a.member ?? '')} menu</>, c.key))
              : li(<>{a.name} — its own screen, in the {memberName(a.member ?? '')} menu</>, 'screen'))}
          </>,
        ) : empty('None — works in chat'))}
        {openAt.length > 0 && section('Norm can open it at…', list(
          openAt.map((i) => li(<>{strong(i.name.replace(/_/g, ' '))}{i.description ? <span style={{ color: 'var(--muted)' }}> — {i.description}</span> : null}</>, i.key)),
        ))}
        {chatComponents.length > 0 && section('Shows in chat', list(
          chatComponents.map((c) => li(<>{c.label}{c.description ? ` — ${c.description}` : ''}</>, c.key)),
        ))}
        {section('In chat it can…', a.tools.length ? list(
          a.tools.map((t) => li(
            <>
              {strong(t.label)}
              {t.writes && <span style={{ marginLeft: 6 }}><Badge>Can make changes</Badge></span>}
              {t.owned === false && <span style={{ marginLeft: 6 }}><Badge>Shared</Badge></span>}
              {t.description ? <span style={{ color: 'var(--muted)' }}> — {t.description}</span> : null}
            </>,
            t.key,
          )),
        ) : empty('Nothing yet'))}
        {a.skills.length > 0 && section('Knows how to…', list(
          a.skills.map((sk) => li(<>{sk.label}</>, sk.slug)),
        ))}
        {a.data.length > 0 && section('Keeps its own data', (
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{a.data.map((d) => d.replace(/_/g, ' ')).join(' · ')}</div>
        ))}
        {section('Works with', <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{a.works_with?.length ? a.works_with.join(' · ') : connectionLine(a)}</div>)}
      </div>
    );
  };

  const retireDialog = retiring && (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(26, 26, 26, 0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, zIndex: 60 }}>
      <div role="dialog" aria-modal="true" aria-labelledby="team-retire-title"
        style={{ width: '100%', maxWidth: 440, background: 'var(--bg)', borderRadius: 'var(--radius-lg)', padding: '20px 24px', boxShadow: '0 8px 30px rgba(0, 0, 0, 0.18)' }}>
        <h2 id="team-retire-title" style={{ ...TITLE, marginBottom: 8 }}>Retire {retiring.name}?</h2>
        <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)', lineHeight: 1.5 }}>
          <p style={{ margin: '0 0 8px' }}>
            The {retiring.name} tab and its Apps leave Norm and its scheduled routines pause.
            Your conversations and records stay — read them any time from Home. Billing stops next cycle.
          </p>
          {retiring.works_with.map((conn) => {
            const others = [...team.members.filter((m) => m.hired && m.slug !== retiring.slug), ...team.included]
              .filter((m) => m.works_with.includes(conn));
            const name = team.apps.flatMap((a) => a.required_connections).find((c) => c.connector === conn)?.display_name ?? conn;
            return (
              <p key={conn} style={{ margin: '0 0 6px' }}>
                {others.length > 0
                  ? `${name} stays connected — ${others.map((o) => o.name).join(', ')} still uses it.`
                  : `${name} stays connected; nothing else uses it, so you can also disconnect it under Settings → Connections.`}
              </p>
            );
          })}
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 20 }}>
          <Button variant="secondary" onClick={() => setRetiring(null)}>
            Keep {retiring.name}
          </Button>
          <Button variant="danger"
            onClick={async () => { const m = retiring; setRetiring(null); await setEntitled(m.catalog_slug, false, m.name); }}>
            Retire
          </Button>
        </div>
      </div>
    </div>
  );

  if (openSlug) {
    // The app opens in place of the section, with a way back.
    return (
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100%' }}>
        <AppRunner slug={openSlug} variant="page" back={{ label: 'AI team', onClick: () => setOpenSlug(null) }} />
      </div>
    );
  }

  /** One AI agent's tile: icon, name, status, price and the hire/retire
   *  action, its tagline, then every App it brings. */
  const memberTile = (t: {
    slug: string; name: string; icon: ReactNode; status?: ReactNode; price: string;
    action?: ReactNode; tagline?: string | null; apps?: TeamApp[]; live?: boolean; fallback?: LucideIcon;
  }) => (
    <section key={t.slug} className="n-card" aria-labelledby={`team-member-${t.slug}`} style={{ minWidth: 0, padding: 16 }}>
      {/* Price and action sit at the right; where the tile is too narrow
          (phones) they wrap under the name, lined up with it. */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px 12px' }}>
        <div style={{ flex: '1 1 260px', minWidth: 0, display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={ICON_TILE}>{t.icon}</span>
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '4px 8px' }}>
            <h3 id={`team-member-${t.slug}`} style={CARD_TITLE}>{t.name}</h3>
            {t.status}
          </div>
        </div>
        <div style={{ flex: '0 0 auto', paddingLeft: 48, display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>{t.price}</span>
          {t.action}
        </div>
      </div>
      {t.tagline && <p style={{ margin: '8px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{t.tagline}</p>}
      {t.apps && appList(t.apps, !!t.live, t.fallback)}
    </section>
  );

  const memberGlyph = (slug: string) => <Icon icon={memberIcon(slug)?.icon ?? CircleUserRound} size={20} />;
  // Norm Core serves every AI agent, so it's listed once, on Norm's own card.
  const ownApps = (apps: TeamApp[]) => apps.filter((a) => !a.bound_to_all);

  const includedCard = (m: IncludedMember) => memberTile({
    slug: m.slug,
    name: m.name,
    icon: memberGlyph(m.slug),
    status: (
      <>
        <Badge>Included</Badge>
        {needsConnections(m.apps) && <Badge tone="warn">Needs connections</Badge>}
      </>
    ),
    price: 'Free',
    tagline: m.tagline,
    apps: ownApps(m.apps),
    live: true,
    fallback: memberIcon(m.slug)?.icon,
  });

  const memberCard = (m: TeamMember) => memberTile({
    slug: m.slug,
    name: m.name,
    icon: memberGlyph(m.slug),
    status: m.hired && (
      <>
        <Badge tone="ok">On your team</Badge>
        {needsConnections(m.apps) && <Badge tone="warn">Needs connections</Badge>}
      </>
    ),
    price: m.price_cents > 0 ? `${price(m.price_cents)}/month · all venues` : 'Free',
    // Not hireable = no catalog row behind it yet (Functions, switched on by
    // the organisation's Functions mode) — nothing to hire or retire.
    action: isOwner && m.hireable && (m.hired ? (
      <Button size="sm" variant="secondary" onClick={() => setRetiring(m)}>
        Retire
      </Button>
    ) : (
      <Button size="sm" variant="primary" disabled={busy === m.catalog_slug} onClick={() => setEntitled(m.catalog_slug, true, m.name)}>
        Hire
      </Button>
    )),
    tagline: m.tagline,
    apps: ownApps(m.apps),
    live: m.hired,
    fallback: memberIcon(m.slug)?.icon,
  });

  // Norm itself: always there, nothing to hire or switch.
  const normCard = memberTile({
    slug: 'norm',
    name: 'Norm',
    icon: <span style={{ fontSize: 'var(--fs-md)', fontWeight: 700, color: 'var(--text)', letterSpacing: '-0.02em' }}>N</span>,
    status: <Badge>Included</Badge>,
    price: 'Free',
    tagline: 'Your assistant — chat, general help and connection setup.',
    apps: team.apps.filter((a) => a.bound_to_all),
    live: true,
  });

  const hiredMembers = team.members.filter((m) => m.hired);
  const availableMembers = team.members.filter((m) => m.hireable && !m.hired);
  const pending = catalog.filter((c) => c.status === 'pending');
  const publishedFrom = (appSlug?: string) =>
    appSlug ? catalog.find((c) => c.composition.app_slug === appSlug) : undefined;

  return (
    <div>
      {retireDialog}
      <h2 id="team-yours" style={TITLE}>Your AI team</h2>
      <p style={INTRO}>
        {isOwner
          ? 'AI agents do the jobs you hire Norm for. Each brings the apps it needs — switch any app on or off.'
          : 'AI agents do the jobs you hire Norm for. Ask an organisation owner to hire or change apps.'}
      </p>
      {notice && (
        <div role="status" style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 12px', marginBottom: 16, borderRadius: 'var(--radius)', background: 'var(--info-bg)', color: 'var(--info)', fontSize: 'var(--fs-sm)' }}>
          <Icon icon={Info} size={16} style={{ marginTop: 1 }} />
          <span>{notice}</span>
        </div>
      )}

      {!team.loaded ? (
        <PageState kind="loading" title="Loading your team…" />
      ) : (
        <>
          {/* Your team — Norm itself, the always-included members, then who
              you've hired. Two columns where there's room. */}
          <section aria-labelledby="team-yours">
            <div style={MEMBER_GRID}>
              {normCard}
              {team.included.map(includedCard)}
              {hiredMembers.map(memberCard)}
            </div>
            {!team.gatingActive && (
              <p style={{ margin: '12px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                Team hiring isn&apos;t switched on for this platform yet — every capability is currently included.
              </p>
            )}
          </section>

          {availableMembers.length > 0 && (
            <section aria-labelledby="team-available" style={{ marginTop: 32 }}>
              <h2 id="team-available" style={{ ...TITLE, marginBottom: 12 }}>Available to hire</h2>
              <div style={MEMBER_GRID}>
                {availableMembers.map(memberCard)}
              </div>
            </section>
          )}

          {/* Team apps — what venue staff see in the apps section. Each is a
              second place a job app's screens show; a screen is the same in
              every team app it is placed in, and each placement has its own
              switch here (docs/plans/team-apps-skeleton.md). Absent until the
              catalog has team rows. */}
          {team.teamApps.length > 0 && (
            <section aria-labelledby="team-apps" style={{ marginTop: 32 }} data-testid="team-apps">
              <h2 id="team-apps" style={TITLE}>Team apps</h2>
              <p style={{ margin: '2px 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                What venue staff open on a phone or the kitchen tablet. A team app shows screens from the AI agents&apos; apps that are on.
                The same screen can be in more than one team app; each switch here is for that team app alone, and a role in
                Settings → Roles decides who sees each team app.
              </p>
              <div style={MEMBER_GRID}>
                {team.teamApps.map(teamAppCard)}
              </div>
            </section>
          )}

          {/* All apps — the same Apps, flattened, naming who each is bound to */}
          <section aria-labelledby="team-all-apps" style={{ marginTop: 32 }}>
            <h2 id="team-all-apps" style={TITLE}>All apps</h2>
            <p style={{ margin: '2px 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              Every app on your platform and the AI agent it belongs to — open one to see what it unlocks. Build your own by chatting with Norm.
            </p>
            {team.apps.length === 0 ? (
              <PageState kind="empty" title="No apps yet." />
            ) : (
              <ul className="n-card" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {team.apps.map((a, i) => {
                  const k = AUTHOR[a.kind];
                  const pub = a.kind === 'custom' ? publishedFrom(a.app_slug) : undefined;
                  const open = detail === a.slug;
                  return (
                    <li key={a.slug} style={{ padding: '10px 16px', borderTop: i > 0 ? '1px solid var(--line-soft)' : undefined }}>
                      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px 12px' }}>
                        <div style={{ flex: '1 1 240px', minWidth: 0, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '4px 10px' }}>
                          <AppIcon app={a} size="inline" fallback={memberIcon(a.member ?? '')?.icon} />
                          {a.kind === 'custom' && a.app_slug ? (
                            <Button variant="link" onClick={() => setOpenSlug(a.app_slug!)}>
                              {a.name}
                            </Button>
                          ) : (
                            <span style={{ fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)' }}>{a.name}</span>
                          )}
                          <Badge tone={a.enabled ? 'ok' : 'neutral'}>{a.enabled ? 'On' : 'Off'}</Badge>
                          {k && <Badge>{k}</Badge>}
                          {pub && <Badge tone="info">{pub.status === 'pending' ? 'Pending approval' : 'Published'}</Badge>}
                          {a.price_cents > 0 && <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)' }}>{price(a.price_cents)}/mo</span>}
                        </div>
                        <div style={{ flex: '0 0 auto', paddingLeft: 26, display: 'flex', alignItems: 'center', gap: 12 }}>
                          <span style={META}>
                            {a.bound_to_all ? 'Bound to every AI agent' : a.member ? `Bound to ${memberName(a.member)}` : 'Not bound to an AI agent'}
                          </span>
                          <Button size="sm" variant="secondary" aria-expanded={open} onClick={() => setDetail(open ? null : a.slug)}>
                            {open ? 'Hide' : 'What it unlocks'}
                          </Button>
                          {isOwner && a.kind === 'custom' && a.app_slug && !pub && (
                            <Button size="sm" variant="secondary" disabled={busy === a.slug}
                              onClick={async () => {
                                setBusy(a.slug); setNotice(null);
                                try {
                                  const r = await apiFetch('/api/marketplace/submit', { method: 'POST', body: JSON.stringify({ app_slug: a.app_slug }) });
                                  if (r.ok) { setNotice(`${a.name} submitted — pending approval.`); loadCatalog(); }
                                  else if (r.status === 403) setNotice('Only organisation owners can publish apps.');
                                } finally { setBusy(null); }
                              }}>
                              Publish
                            </Button>
                          )}
                        </div>
                      </div>
                      {open && capabilityPanel(a)}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {isAdmin && pending.length > 0 && (
            <section aria-labelledby="team-awaiting-approval" style={{ marginTop: 32 }}>
              <h2 id="team-awaiting-approval" style={{ ...TITLE, marginBottom: 12 }}>Awaiting approval</h2>
              <ul className="n-card" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {pending.map((c, i) => (
                  <li key={c.slug} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px 12px', padding: '10px 16px', borderTop: i > 0 ? '1px solid var(--line-soft)' : undefined }}>
                    <span style={{ fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)' }}>{c.name}</span>
                    <span style={{ flex: '1 1 200px', minWidth: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{c.description}</span>
                    <Button size="sm" variant="secondary"
                      onClick={async () => { const r = await apiFetch(`/api/marketplace/${c.slug}/approve`, { method: 'POST' }); if (r.ok) changed(); }}>
                      Approve
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
