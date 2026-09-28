'use client';

/**
 * Your AI Team — the marketplace, rebuilt around the hierarchy
 * (Organisation → AI Team Members → Apps → Components → Connections).
 *
 * Team members are the jobs you hire Norm to perform. Every App — Norm-built,
 * integration-backed, community or built by your team — is bound to the
 * member(s) that use it and has an enabled state. A member's card lists ALL
 * its Apps; once hired, each App switches on/off right there (owners only).
 * The "All apps" list below is the same set, flattened, naming who each App
 * is bound to. Components, tools and playbooks are never shown.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiFetch, getToken } from '../../lib/api';
import { AGENTS } from '../layout/Sidebar';
import AppRunner from '../apps/AppRunner';
import {
  TEAM_CHANGED_EVENT,
  useTeam,
  type IncludedMember,
  type TeamApp,
  type TeamMember,
} from '../../hooks/useTeam';

interface PageUser { role: string; permissions?: string[] }

interface CatalogApp {
  slug: string; name: string; description: string; tier: string;
  price_cents: number; status: string;
  composition: { app_slug?: string; agents?: string[] };
}
interface ConnVenue { venue_id: string; venue_name: string; status: string; scope?: string }

const STATUS_DOT: Record<string, string> = {
  connected: '#2e7d4f',
  needs_reconnect: '#b8860b',
  not_connected: '#b0aca4',
};

// Author marker only — no "Norm app vs integration" distinction (owner,
// 28 Sep): an App either needs connections or runs on Norm, and that's shown
// by its connection line.
const AUTHOR: Record<string, { text: string; color: string; bg: string } | undefined> = {
  custom: { text: 'Built by your team', color: '#8a5a1f', bg: '#fdf3e4' },
  community: { text: 'Community', color: '#2e7d4f', bg: '#eef6f0' },
};

const price = (cents: number) => `$${(cents / 100).toFixed(cents % 100 ? 2 : 0)}`;

const memberIcon = (slug: string) => AGENTS.find((a) => a.id === slug);

export default function TeamPage({ user }: { user?: PageUser | null }) {
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

  const dots = (connector: string) => {
    const info = connInfo[connector];
    if (!info || info === 'loading') return <span style={{ color: '#b0aca4', fontSize: '0.62rem' }}>…</span>;
    return (
      <span title={info.map((v) => `${v.venue_name}: ${v.status.replace('_', ' ')}`).join('\n')} style={{ letterSpacing: 1 }}>
        {info.map((v) => (
          <span key={v.venue_id} style={{ color: STATUS_DOT[v.status] ?? '#b0aca4' }}>●</span>
        ))}
      </span>
    );
  };

  const connectionLine = (a: TeamApp) =>
    a.required_connections.length === 0 ? (
      <span style={{ color: '#8a8a8a' }}>runs on Norm</span>
    ) : (
      <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {a.required_connections.map((c) => (
          <span key={c.connector} style={{ display: 'inline-flex', gap: 4, alignItems: 'center', color: '#6b6b6b' }}>
            uses {c.display_name} {dots(c.connector)}
          </span>
        ))}
      </span>
    );

  const needsConnections = (apps: TeamApp[]) =>
    apps.some((a) =>
      a.enabled &&
      a.required_connections.some((c) => {
        const info = connInfo[c.connector];
        return Array.isArray(info) && info.length > 0 && info.every((v) => v.status !== 'connected');
      }),
    );

  const badge = (text: string, color: string, bg: string) => (
    <span style={{ fontSize: '0.6rem', fontWeight: 700, color, background: bg, borderRadius: 8, padding: '2px 8px', whiteSpace: 'nowrap', textTransform: 'uppercase', letterSpacing: '0.03em' }}>{text}</span>
  );

  const appSwitch = (a: TeamApp, live: boolean) => {
    if (!a.switchable) return badge('always on', '#2e5a7d', '#e8f0f6');
    if (!live) return badge(a.bundled ? 'included' : 'optional', '#6b6b6b', '#f0ebe5');
    return (
      <button
        type="button"
        disabled={!isOwner || busy === a.slug}
        onClick={() => setEntitled(a.slug, !a.enabled, a.name)}
        title={isOwner ? (a.enabled ? `Switch ${a.name} off` : `Switch ${a.name} on`) : 'Only organisation owners can change the team.'}
        style={{
          minWidth: 42, fontSize: '0.62rem', fontWeight: 700, borderRadius: 10, padding: '2px 10px',
          border: '1px solid', cursor: isOwner ? 'pointer' : 'default', fontFamily: 'inherit',
          borderColor: a.enabled ? '#bcd9c6' : '#d8d4cc',
          background: a.enabled ? '#eef6f0' : '#fff',
          color: a.enabled ? '#2e7d4f' : '#8a8a8a',
        }}
      >
        {a.enabled ? 'On' : 'Off'}
      </button>
    );
  };

  /** A member's App list. `live` = hired, so the switches work. */
  const appList = (apps: TeamApp[], live: boolean) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10 }}>
      {apps.map((a) => (
        <div key={a.slug} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: '0.74rem' }}>
          {appSwitch(a, live)}
          <span style={{ fontWeight: 600 }}>{a.icon ? `${a.icon} ` : ''}{a.name}</span>
          {a.kind === 'custom' && badge('your team', '#8a5a1f', '#fdf3e4')}
          {a.bound_to_all && <span style={{ fontSize: '0.66rem', color: '#8a8a8a' }}>shared by every team member</span>}
          {a.price_cents > 0 && <span style={{ color: '#8a5a1f', fontWeight: 600 }}>{price(a.price_cents)}/mo</span>}
          <span style={{ fontSize: '0.68rem' }}>{connectionLine(a)}</span>
        </div>
      ))}
      {apps.length === 0 && (
        <span style={{ fontSize: '0.72rem', color: '#8a8a8a' }}>No Apps yet — chat skills only for this role.</span>
      )}
    </div>
  );

  /** What an App unlocks: its menu pages, what it can do in chat, the skills
   *  it knows, the data it keeps, and what it needs connected. */
  const capabilityPanel = (a: TeamApp) => {
    const section = (title: string, body: React.ReactNode) => (
      <div style={{ marginTop: 8 }}>
        <div style={{ fontSize: '0.64rem', fontWeight: 700, color: '#8a8a8a', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 3 }}>{title}</div>
        {body}
      </div>
    );
    const li = (children: React.ReactNode, key: string) => (
      <li key={key} style={{ fontSize: '0.72rem', color: '#444', margin: '2px 0' }}>{children}</li>
    );
    const pages = a.components.filter((c) => c.page || c.shared);
    // App-platform components come from the app's own version (see
    // services/app_components.py): the screens Norm can open, and where.
    const appComponents = a.components.filter((c) => c.inputs !== undefined);
    const chatComponents = a.components.filter((c) => !c.page && !c.shared && c.inputs === undefined);
    const openAt = appComponents.flatMap((c) => (c.inputs ?? []).map((i) => ({ ...i, key: `${c.key}.${i.name}` })));
    return (
      <div style={{ borderTop: '1px solid #eee', marginTop: 8, paddingTop: 4, width: '100%' }}>
        {a.description && <div style={{ fontSize: '0.72rem', color: '#6b6b6b', marginTop: 4 }}>{a.description}</div>}
        {section('Menu pages', pages.length || a.app_platform ? (
          <ul style={{ margin: 0, paddingLeft: 16 }}>
            {pages.map((c) => li(
              <>{c.shared ? `${c.label} — in every team member's menu` : `${c.label} — in the ${memberName(a.member ?? '')} menu`}</>,
              c.key,
            ))}
            {a.app_platform && (appComponents.some((c) => c.app_page)
              ? appComponents.filter((c) => c.app_page).map((c) => li(<>{c.label} — in the {memberName(a.member ?? '')} menu</>, c.key))
              : li(<>{a.name} — its own screen, in the {memberName(a.member ?? '')} menu</>, 'screen'))}
          </ul>
        ) : <span style={{ fontSize: '0.72rem', color: '#8a8a8a' }}>none — works in chat</span>)}
        {openAt.length > 0 && section('Norm can open it at…', (
          <ul style={{ margin: 0, paddingLeft: 16 }}>
            {openAt.map((i) => li(<><strong style={{ fontWeight: 600 }}>{i.name.replace(/_/g, ' ')}</strong>{i.description ? <span style={{ color: '#6b6b6b' }}> — {i.description}</span> : null}</>, i.key))}
          </ul>
        ))}
        {chatComponents.length > 0 && section('Shows in chat', (
          <ul style={{ margin: 0, paddingLeft: 16 }}>
            {chatComponents.map((c) => li(<>{c.label}{c.description ? ` — ${c.description}` : ''}</>, c.key))}
          </ul>
        ))}
        {section('In chat it can…', a.tools.length ? (
          <ul style={{ margin: 0, paddingLeft: 16 }}>
            {a.tools.map((t) => li(
              <>
                <strong style={{ fontWeight: 600 }}>{t.label}</strong>
                {t.writes && <span style={{ marginLeft: 6 }}>{badge('can make changes', '#8a5a1f', '#fdf3e4')}</span>}
                {t.description ? <span style={{ color: '#6b6b6b' }}> — {t.description}</span> : null}
              </>,
              t.key,
            ))}
          </ul>
        ) : <span style={{ fontSize: '0.72rem', color: '#8a8a8a' }}>nothing yet</span>)}
        {a.skills.length > 0 && section('Knows how to…', (
          <ul style={{ margin: 0, paddingLeft: 16 }}>
            {a.skills.map((sk) => li(<>{sk.label}</>, sk.slug))}
          </ul>
        ))}
        {a.data.length > 0 && section('Keeps its own data', (
          <div style={{ fontSize: '0.72rem', color: '#444' }}>{a.data.map((d) => d.replace(/_/g, ' ')).join(' · ')}</div>
        ))}
        {section('Works with', <div style={{ fontSize: '0.72rem' }}>{connectionLine(a)}</div>)}
      </div>
    );
  };

  const retireDialog = retiring && (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60 }}>
      <div style={{ background: '#fff', borderRadius: 10, padding: '1.2rem 1.4rem', maxWidth: 440, boxShadow: '0 8px 30px rgba(0,0,0,0.2)' }}>
        <h3 style={{ margin: '0 0 0.5rem', fontSize: '0.95rem' }}>Retire {retiring.name}?</h3>
        <div style={{ fontSize: '0.76rem', color: '#555', lineHeight: 1.55 }}>
          <p style={{ margin: '0 0 0.5rem' }}>
            The {retiring.name} tab and its Apps leave Norm and its scheduled routines pause.
            Your conversations and records stay — read them any time from Home. Billing stops next cycle.
          </p>
          {retiring.works_with.map((conn) => {
            const others = [...team.members.filter((m) => m.hired && m.slug !== retiring.slug), ...team.included]
              .filter((m) => m.works_with.includes(conn));
            const name = team.apps.flatMap((a) => a.required_connections).find((c) => c.connector === conn)?.display_name ?? conn;
            return (
              <p key={conn} style={{ margin: '0 0 0.35rem' }}>
                {others.length > 0
                  ? `${name} stays connected — ${others.map((o) => o.name).join(', ')} still uses it.`
                  : `${name} stays connected; nothing else uses it, so you can also disconnect it under Settings → Connections.`}
              </p>
            );
          })}
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: '0.9rem' }}>
          <button type="button" onClick={() => setRetiring(null)}
            style={{ fontSize: '0.74rem', border: '1px solid #d8d4cc', borderRadius: 6, background: '#fff', padding: '5px 12px', cursor: 'pointer', fontFamily: 'inherit' }}>
            Keep {retiring.name}
          </button>
          <button type="button"
            onClick={async () => { const m = retiring; setRetiring(null); await setEntitled(m.catalog_slug, false, m.name); }}
            style={{ fontSize: '0.74rem', border: '1px solid #c96b6b', borderRadius: 6, background: '#b04a4a', color: '#fff', padding: '5px 12px', cursor: 'pointer', fontFamily: 'inherit' }}>
            Retire
          </button>
        </div>
      </div>
    </div>
  );

  if (openSlug) {
    return (
      <div>
        <button type="button" onClick={() => setOpenSlug(null)}
          style={{ margin: '0.6rem 1rem 0', fontSize: '0.72rem', border: '1px solid #d8d4cc', borderRadius: 5, background: '#fff', color: '#6b6b6b', cursor: 'pointer', padding: '3px 10px', fontFamily: 'inherit' }}>
          ← Your AI team
        </button>
        <AppRunner slug={openSlug} />
      </div>
    );
  }

  const cardShell = (children: React.ReactNode, key: string) => (
    <div key={key} style={{ border: '1px solid #e2ddd7', borderRadius: 10, background: '#fff', padding: '0.8rem 1rem', marginBottom: 10 }}>
      {children}
    </div>
  );

  const includedCard = (m: IncludedMember) => {
    const meta = memberIcon(m.slug);
    return cardShell(
      <>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {meta && <meta.icon size={20} strokeWidth={1.75} color={meta.color} />}
          <span style={{ fontSize: '0.9rem', fontWeight: 700 }}>{m.name}</span>
          {badge('included free · always on', '#2e7d4f', '#eef6f0')}
          {needsConnections(m.apps) && badge('needs connections', '#8a5a1f', '#fdf3e4')}
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: '0.72rem', color: '#8a8a8a', fontWeight: 600 }}>free</span>
        </div>
        <div style={{ fontSize: '0.74rem', color: '#6b6b6b', marginTop: 4 }}>{m.tagline}</div>
        {appList(m.apps, true)}
      </>,
      m.slug,
    );
  };

  const memberCard = (m: TeamMember) => {
    const meta = memberIcon(m.slug);
    return cardShell(
      <>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {meta && <meta.icon size={20} strokeWidth={1.75} color={meta.color} />}
          <span style={{ fontSize: '0.9rem', fontWeight: 700 }}>{m.name}</span>
          {m.hired && badge('on your team', '#2e7d4f', '#eef6f0')}
          {m.hired && needsConnections(m.apps) && badge('needs connections', '#8a5a1f', '#fdf3e4')}
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: '0.72rem', color: m.price_cents ? '#333' : '#8a8a8a', fontWeight: 600 }}>
            {m.price_cents > 0 ? `${price(m.price_cents)}/month · all venues` : 'free'}
          </span>
          {isOwner && !m.hired && (
            <button type="button" disabled={busy === m.catalog_slug} onClick={() => setEntitled(m.catalog_slug, true, m.name)}
              style={{ fontSize: '0.74rem', fontWeight: 700, border: '1px solid #2e7d4f', borderRadius: 6, background: '#2e7d4f', color: '#fff', padding: '4px 14px', cursor: 'pointer', fontFamily: 'inherit' }}>
              Hire
            </button>
          )}
          {isOwner && m.hired && (
            <button type="button" onClick={() => setRetiring(m)}
              style={{ fontSize: '0.7rem', border: '1px solid #d8d4cc', borderRadius: 6, background: '#fff', color: '#8a6a6a', padding: '4px 10px', cursor: 'pointer', fontFamily: 'inherit' }}>
              Retire
            </button>
          )}
        </div>
        <div style={{ fontSize: '0.74rem', color: '#6b6b6b', marginTop: 4 }}>{m.tagline}</div>
        {appList(m.apps, m.hired)}
      </>,
      m.slug,
    );
  };

  const hiredMembers = team.members.filter((m) => m.hired);
  const availableMembers = team.members.filter((m) => m.hireable && !m.hired);
  const pending = catalog.filter((c) => c.status === 'pending');
  const publishedFrom = (appSlug?: string) =>
    appSlug ? catalog.find((c) => c.composition.app_slug === appSlug) : undefined;

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '1.2rem 1rem', overflowY: 'auto', height: '100%' }}>
      {retireDialog}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Your AI Team</h2>
        {!isOwner && <span style={{ fontSize: '0.7rem', color: '#8a8a8a' }}>ask an organisation owner to hire or change Apps</span>}
      </div>
      <p style={{ fontSize: '0.74rem', color: '#6b6b6b', lineHeight: 1.55, margin: '0.3rem 0 1rem' }}>
        AI Team Members are the jobs you hire Norm to perform — each one unlocks the Apps and
        capabilities needed to do that job. Switch any App on or off once its team member is hired.
        Apps work inside the systems you already use: connect each system once per venue, and every
        App that works in it is ready. Retire a team member any time — your conversations and records stay.
      </p>
      {notice && (
        <div style={{ fontSize: '0.72rem', color: '#8a5a1f', background: '#fdf3e4', border: '1px solid #f0dfc0', borderRadius: 6, padding: '6px 10px', marginBottom: 10 }}>
          {notice}
        </div>
      )}

      {/* Your team */}
      <h3 style={{ fontSize: '0.8rem', margin: '0.8rem 0 0.5rem', color: '#555' }}>Your team</h3>
      <div style={{ border: '1px solid #e2ddd7', borderRadius: 10, background: '#faf8f5', padding: '0.7rem 1rem', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: '0.86rem', fontWeight: 700 }}>Norm</span>
        {badge('included free · always on', '#2e7d4f', '#eef6f0')}
        <span style={{ fontSize: '0.72rem', color: '#6b6b6b' }}>your assistant — chat, general help and connection setup</span>
      </div>
      {team.included.map(includedCard)}
      {hiredMembers.map(memberCard)}
      {!team.gatingActive && team.loaded && (
        <div style={{ fontSize: '0.72rem', color: '#8a8a8a', margin: '0.4rem 0 1rem' }}>
          Team hiring isn&apos;t switched on for this platform yet — every capability is currently included.
        </div>
      )}

      {/* Available to hire */}
      {availableMembers.length > 0 && (
        <>
          <h3 style={{ fontSize: '0.8rem', margin: '1.1rem 0 0.5rem', color: '#555' }}>Available to hire</h3>
          {availableMembers.map(memberCard)}
        </>
      )}

      {/* All apps — the same Apps, flattened, naming who each is bound to */}
      <h3 style={{ fontSize: '0.8rem', margin: '1.1rem 0 0.2rem', color: '#555' }}>All apps</h3>
      <p style={{ fontSize: '0.7rem', color: '#8a8a8a', margin: '0 0 0.5rem' }}>
        Every App on your platform and the team member it belongs to — open one to see what it unlocks. Build your own by chatting with Norm.
      </p>
      {team.apps.map((a) => {
        const k = AUTHOR[a.kind];
        const pub = a.kind === 'custom' ? publishedFrom(a.app_slug) : undefined;
        const open = detail === a.slug;
        return (
          <div key={a.slug} style={{ display: 'flex', alignItems: 'center', gap: 10, border: '1px solid #e2ddd7', borderRadius: 8, background: '#fff', padding: '0.5rem 0.8rem', marginBottom: 6, fontSize: '0.76rem', flexWrap: 'wrap' }}>
            <span style={{ minWidth: 42, fontSize: '0.62rem', fontWeight: 700, color: a.enabled ? '#2e7d4f' : '#8a8a8a' }}>
              {a.enabled ? '● On' : '○ Off'}
            </span>
            {a.kind === 'custom' && a.app_slug ? (
              <button type="button" onClick={() => setOpenSlug(a.app_slug!)}
                style={{ border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 600, fontSize: '0.78rem', padding: 0 }}>
                {a.icon ? `${a.icon} ` : ''}{a.name}
              </button>
            ) : (
              <span style={{ fontWeight: 600 }}>{a.name}</span>
            )}
            {k && badge(k.text, k.color, k.bg)}
            {pub && badge(pub.status === 'pending' ? 'pending approval' : 'published', '#2e5a7d', '#e8f0f6')}
            {a.price_cents > 0 && <span style={{ color: '#8a5a1f', fontWeight: 600 }}>{price(a.price_cents)}/mo</span>}
            <span style={{ flex: 1 }} />
            <span style={{ fontSize: '0.68rem', color: '#6b6b6b' }}>
              {a.bound_to_all ? 'bound to every team member' : a.member ? `bound to ${memberName(a.member)}` : 'not bound to a team member'}
            </span>
            <button type="button" onClick={() => setDetail(open ? null : a.slug)}
              style={{ fontSize: '0.68rem', border: '1px solid #d8d4cc', borderRadius: 5, background: open ? '#f0ebe5' : '#fff', color: '#6b6b6b', cursor: 'pointer', padding: '3px 10px', fontFamily: 'inherit' }}>
              {open ? 'Hide' : 'What it unlocks'}
            </button>
            {isOwner && a.kind === 'custom' && a.app_slug && !pub && (
              <button type="button" disabled={busy === a.slug}
                onClick={async () => {
                  setBusy(a.slug); setNotice(null);
                  try {
                    const r = await apiFetch('/api/marketplace/submit', { method: 'POST', body: JSON.stringify({ app_slug: a.app_slug }) });
                    if (r.ok) { setNotice(`${a.name} submitted — pending approval.`); loadCatalog(); }
                    else if (r.status === 403) setNotice('Only organisation owners can publish apps.');
                  } finally { setBusy(null); }
                }}
                style={{ fontSize: '0.68rem', border: '1px solid #d8d4cc', borderRadius: 5, background: '#fff', color: '#6b6b6b', cursor: 'pointer', padding: '3px 10px', fontFamily: 'inherit' }}>
                Publish
              </button>
            )}
            {open && capabilityPanel(a)}
          </div>
        );
      })}
      {team.apps.length === 0 && team.loaded && (
        <div style={{ fontSize: '0.72rem', color: '#8a8a8a' }}>No Apps yet.</div>
      )}

      {isAdmin && pending.length > 0 && (
        <>
          <h3 style={{ fontSize: '0.8rem', margin: '1.1rem 0 0.4rem', color: '#555' }}>Awaiting approval</h3>
          {pending.map((c) => (
            <div key={c.slug} style={{ display: 'flex', alignItems: 'center', gap: 10, border: '1px solid #e2ddd7', borderRadius: 8, background: '#fff', padding: '0.5rem 0.8rem', marginBottom: 6, fontSize: '0.76rem' }}>
              <span style={{ fontWeight: 600 }}>{c.name}</span>
              <span style={{ flex: 1, fontSize: '0.7rem', color: '#8a8a8a' }}>{c.description}</span>
              <button type="button" onClick={async () => { const r = await apiFetch(`/api/marketplace/${c.slug}/approve`, { method: 'POST' }); if (r.ok) changed(); }}
                style={{ fontSize: '0.68rem', border: '1px solid #2e7d4f', borderRadius: 5, background: '#eef6f0', color: '#2e7d4f', cursor: 'pointer', padding: '3px 10px', fontFamily: 'inherit' }}>
                Approve
              </button>
            </div>
          ))}
        </>
      )}
      <div style={{ height: 40 }} />
    </div>
  );
}
