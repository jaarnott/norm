'use client';

/**
 * Your AI Team — the marketplace, reborn around the hierarchy
 * (Organisation → AI Team Members → Apps → Components → Connections).
 *
 * Members are the jobs you hire Norm to perform; each unlocks the Apps needed
 * to do that job. Every App has an enabled state: hiring a member enables its
 * bundled Apps by default, individual Apps can be switched off, and a priced
 * App is enabled for its own monthly price. Connections are plumbing — each
 * App names what it works in, with per-venue readiness dots. Components,
 * tools and playbooks are never shown.
 *
 * The member card is the PRIMARY place Apps are managed; the shelves below
 * ("Built by your team" / "From the community") are discovery.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiFetch, getToken } from '../../lib/api';
import { AGENTS } from '../layout/Sidebar';
import { APP_PAGES_CHANGED_EVENT } from '../apps/AppsDashboard';
import AppRunner from '../apps/AppRunner';
import { TEAM_CHANGED_EVENT, useTeam, type TeamApp, type TeamMember } from '../../hooks/useTeam';

interface PageUser { role: string; permissions?: string[] }

interface AppRow {
  slug: string; name: string; description?: string | null; icon?: string | null;
  visibility: string; mine: boolean; access: string; pinned: boolean; agent: string;
}
interface CatalogApp {
  slug: string; name: string; description: string; tier: string; bundled: boolean;
  price_cents: number; status: string; enabled: boolean;
  composition: { app_slug?: string; agents?: string[] };
}
interface ConnVenue { venue_id: string; venue_name: string; status: string }

const STATUS_DOT: Record<string, string> = {
  connected: '#2e7d4f',
  needs_reconnect: '#b8860b',
  not_connected: '#b0aca4',
};

const price = (cents: number) => `$${(cents / 100).toFixed(cents % 100 ? 2 : 0)}`;

const memberIcon = (slug: string) => AGENTS.find((a) => a.id === slug);

export default function TeamPage({ user }: { user?: PageUser | null }) {
  const team = useTeam(getToken());
  const [apps, setApps] = useState<AppRow[] | null>(null);
  const [catalog, setCatalog] = useState<CatalogApp[] | null>(null);
  const [connInfo, setConnInfo] = useState<Record<string, ConnVenue[] | 'loading'>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [retiring, setRetiring] = useState<TeamMember | null>(null);
  const [openSlug, setOpenSlug] = useState<string | null>(null);

  const isAdmin = user?.role === 'admin';
  const isOwner = isAdmin || !!user?.permissions?.includes('billing:manage');

  const load = useCallback(() => {
    apiFetch('/api/apps')
      .then((r) => (r.ok ? r.json() : { apps: [] }))
      .then((d) => setApps(d.apps ?? []))
      .catch(() => setApps([]));
    apiFetch('/api/marketplace')
      .then((r) => (r.ok ? r.json() : { apps: [] }))
      .then((d) => setCatalog(d.apps ?? []))
      .catch(() => setCatalog([]));
  }, []);
  useEffect(() => { load(); }, [load]);

  // Per-venue readiness for every connection any member works with — the dots
  // are the whole "is it actually ready for Bessie's?" story.
  useEffect(() => {
    const connectors = new Set<string>();
    for (const m of team.members) for (const c of m.works_with) connectors.add(c);
    for (const conn of connectors) {
      if (connInfo[conn]) continue;
      setConnInfo((p) => ({ ...p, [conn]: 'loading' }));
      apiFetch(`/api/connectors/${conn}/connect-info`)
        .then((r) => (r.ok ? r.json() : { venues: [] }))
        .then((d) => setConnInfo((p) => ({ ...p, [conn]: d.venues ?? [] })))
        .catch(() => setConnInfo((p) => ({ ...p, [conn]: [] })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team.members]);

  const displayNames: Record<string, string> = {};
  for (const m of team.members)
    for (const a of m.apps)
      for (const c of a.required_connections) displayNames[c.connector] = c.display_name;

  const changed = () => {
    window.dispatchEvent(new CustomEvent(TEAM_CHANGED_EVENT));
    team.refresh();
    load();
  };

  const setEntitled = async (slug: string, enabled: boolean, label: string) => {
    if (busy) return false;
    setBusy(slug);
    setNotice(null);
    try {
      const r = await apiFetch(`/api/marketplace/${slug}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' });
      if (r.ok) { changed(); return true; }
      if (r.status === 403) setNotice('Only organisation owners can change the team.');
      else setNotice((await r.json().catch(() => ({}))).detail || `Couldn't update ${label}`);
      return false;
    } finally {
      setBusy(null);
    }
  };

  const hire = async (m: TeamMember) => {
    if (await setEntitled(m.catalog_slug, true, m.name)) setExpanded(m.slug);
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
      <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
        {a.required_connections.map((c) => (
          <span key={c.connector} style={{ display: 'inline-flex', gap: 4, alignItems: 'center', color: '#6b6b6b' }}>
            uses {c.display_name} {dots(c.connector)}
          </span>
        ))}
      </span>
    );

  const needsConnections = (m: TeamMember) =>
    m.apps.some((a) =>
      a.enabled &&
      a.required_connections.some((c) => {
        const info = connInfo[c.connector];
        return Array.isArray(info) && info.some((v) => v.status !== 'connected');
      }),
    );

  const badge = (text: string, color: string, bg: string) => (
    <span style={{ fontSize: '0.6rem', fontWeight: 700, color, background: bg, borderRadius: 8, padding: '2px 8px', whiteSpace: 'nowrap', textTransform: 'uppercase', letterSpacing: '0.03em' }}>{text}</span>
  );

  const appSwitch = (m: TeamMember, a: TeamApp) => (
    <button
      type="button"
      disabled={!isOwner || busy === a.slug}
      onClick={() => setEntitled(a.slug, !a.enabled, a.name)}
      title={isOwner ? (a.enabled ? `Switch ${a.name} off` : `Switch ${a.name} on`) : 'Only organisation owners can change the team.'}
      style={{
        fontSize: '0.62rem', fontWeight: 700, borderRadius: 10, padding: '2px 10px',
        border: '1px solid', cursor: isOwner ? 'pointer' : 'default', fontFamily: 'inherit',
        borderColor: a.enabled ? '#bcd9c6' : '#d8d4cc',
        background: a.enabled ? '#eef6f0' : '#fff',
        color: a.enabled ? '#2e7d4f' : '#8a8a8a',
      }}
    >
      {a.enabled ? 'On' : 'Off'}
    </button>
  );

  const appList = (m: TeamMember) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
      {m.apps.map((a) => (
        <div key={a.slug} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: '0.74rem' }}>
          {m.hired ? appSwitch(m, a) : badge(a.bundled ? 'included' : 'optional', '#6b6b6b', '#f0ebe5')}
          <span style={{ fontWeight: 600 }}>{a.name}</span>
          {a.price_cents > 0 && <span style={{ color: '#8a5a1f', fontWeight: 600 }}>{price(a.price_cents)}/mo</span>}
          <span style={{ fontSize: '0.68rem' }}>{connectionLine(a)}</span>
        </div>
      ))}
      {m.apps.length === 0 && (
        <span style={{ fontSize: '0.72rem', color: '#8a8a8a' }}>Chat skills only for now — Apps for this role are coming.</span>
      )}
    </div>
  );

  const retireDialog = retiring && (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60 }}>
      <div style={{ background: '#fff', borderRadius: 10, padding: '1.2rem 1.4rem', maxWidth: 440, boxShadow: '0 8px 30px rgba(0,0,0,0.2)' }}>
        <h3 style={{ margin: '0 0 0.5rem', fontSize: '0.95rem' }}>Retire {retiring.name}?</h3>
        <div style={{ fontSize: '0.76rem', color: '#555', lineHeight: 1.55 }}>
          <p style={{ margin: '0 0 0.5rem' }}>
            The {retiring.name} tab and its pages leave the sidebar and its scheduled routines pause.
            Your conversations and records stay — read them any time from Home. Billing stops next cycle.
          </p>
          {retiring.works_with.map((conn) => {
            const others = team.members.filter((m) => m.hired && m.slug !== retiring.slug && m.works_with.includes(conn));
            const name = displayNames[conn] ?? conn;
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

  const hiredMembers = team.members.filter((m) => m.hired);
  const availableMembers = team.members.filter((m) => m.hireable && !m.hired);
  const community = (catalog ?? []).filter((c) => c.tier === 'user');
  const teamBuilt = apps ?? [];
  const submissionFor = (slug: string) => community.find((c) => c.composition.app_slug === slug);

  const memberCard = (m: TeamMember) => {
    const meta = memberIcon(m.slug);
    const isOpen = expanded === m.slug;
    return (
      <div key={m.slug} style={{ border: '1px solid #e2ddd7', borderRadius: 10, background: '#fff', padding: '0.8rem 1rem', marginBottom: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {meta && <meta.icon size={20} strokeWidth={1.75} color={meta.color} />}
          <button type="button" onClick={() => setExpanded(isOpen ? null : m.slug)}
            style={{ border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit', fontSize: '0.9rem', fontWeight: 700, padding: 0 }}>
            {m.name}
          </button>
          {m.hired && badge('on your team', '#2e7d4f', '#eef6f0')}
          {m.hired && needsConnections(m) && badge('needs connections', '#8a5a1f', '#fdf3e4')}
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: '0.72rem', color: m.price_cents ? '#333' : '#8a8a8a', fontWeight: 600 }}>
            {m.price_cents > 0 ? `${price(m.price_cents)}/month · all venues` : 'free'}
          </span>
          {isOwner && !m.hired && (
            <button type="button" disabled={busy === m.catalog_slug} onClick={() => hire(m)}
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
        {(isOpen || !m.hired) && appList(m)}
        {isOpen && m.hired && (
          <div style={{ marginTop: 8, fontSize: '0.68rem', color: '#8a8a8a' }}>
            + Add App — enable one from the shelves below, or build your own by chatting with Norm.
          </div>
        )}
      </div>
    );
  };

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '1.2rem 1rem', overflowY: 'auto', height: '100%' }}>
      {retireDialog}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Your AI Team</h2>
        {!isOwner && <span style={{ fontSize: '0.7rem', color: '#8a8a8a' }}>ask an organisation owner to hire</span>}
      </div>
      <p style={{ fontSize: '0.74rem', color: '#6b6b6b', lineHeight: 1.55, margin: '0.3rem 0 1rem' }}>
        AI Team Members are the jobs you hire Norm to perform — each one unlocks the Apps and
        capabilities needed to do that job. Apps work inside the systems you already use: connect each
        system once per venue, and every App that works in it is ready. Hired but not yet connected?
        The Apps stay visible and tell you exactly what to connect. Retire a team member any time —
        your conversations and records stay.
      </p>
      {notice && (
        <div style={{ fontSize: '0.72rem', color: '#8a5a1f', background: '#fdf3e4', border: '1px solid #f0dfc0', borderRadius: 6, padding: '6px 10px', marginBottom: 10 }}>
          {notice}
        </div>
      )}

      {/* Shelf 1: your team */}
      <h3 style={{ fontSize: '0.8rem', margin: '0.8rem 0 0.5rem', color: '#555' }}>Your team</h3>
      <div style={{ border: '1px solid #e2ddd7', borderRadius: 10, background: '#faf8f5', padding: '0.7rem 1rem', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: '0.86rem', fontWeight: 700 }}>Norm</span>
        {badge('included free · always on', '#2e7d4f', '#eef6f0')}
        <span style={{ fontSize: '0.72rem', color: '#6b6b6b' }}>
          chat, reports and connection setup — plus Reports and App Builder, always on your team
        </span>
      </div>
      {hiredMembers.map(memberCard)}
      {team.gatingActive && hiredMembers.length === 0 && (
        <div style={{ fontSize: '0.74rem', color: '#8a8a8a', marginBottom: 10 }}>No one hired yet — meet the team below.</div>
      )}

      {/* Shelf 2: available to hire */}
      {availableMembers.length > 0 && (
        <>
          <h3 style={{ fontSize: '0.8rem', margin: '1.1rem 0 0.5rem', color: '#555' }}>Available to hire</h3>
          {availableMembers.map(memberCard)}
        </>
      )}
      {!team.gatingActive && team.loaded && (
        <div style={{ fontSize: '0.72rem', color: '#8a8a8a', margin: '0.4rem 0 1rem' }}>
          Team hiring isn&apos;t switched on for this platform yet — every capability is currently included.
        </div>
      )}

      {/* Shelf 3: more apps (discovery — managed on the member cards) */}
      <h3 style={{ fontSize: '0.8rem', margin: '1.1rem 0 0.2rem', color: '#555' }}>More apps</h3>
      <p style={{ fontSize: '0.7rem', color: '#8a8a8a', margin: '0 0 0.5rem' }}>
        Your team members come with their Apps. Add more here — or build your own by chatting with Norm.
      </p>
      {teamBuilt.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: '0.68rem', fontWeight: 700, color: '#8a8a8a', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>Built by your team</div>
          {teamBuilt.map((a) => {
            const sub = submissionFor(a.slug);
            return (
              <div key={a.slug} style={{ display: 'flex', alignItems: 'center', gap: 10, border: '1px solid #e2ddd7', borderRadius: 8, background: '#fff', padding: '0.5rem 0.8rem', marginBottom: 6, fontSize: '0.76rem' }}>
                <button type="button" onClick={() => setOpenSlug(a.slug)}
                  style={{ border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 600, fontSize: '0.78rem', padding: 0 }}>
                  {a.icon ? `${a.icon} ` : ''}{a.name}
                </button>
                {badge(a.mine ? 'yours' : 'shared', '#6b6b6b', '#f0ebe5')}
                {sub && badge(sub.status === 'pending' ? 'pending approval' : 'published', '#2e5a7d', '#e8f0f6')}
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: '0.66rem', color: '#8a8a8a' }}>appears under {memberIcon(a.agent)?.label ?? a.agent}</span>
                {isOwner && !sub && (
                  <button type="button" disabled={busy === a.slug}
                    onClick={async () => {
                      setBusy(a.slug); setNotice(null);
                      try {
                        const r = await apiFetch('/api/marketplace/submit', { method: 'POST', body: JSON.stringify({ app_slug: a.slug }) });
                        if (r.ok) { setNotice(`${a.name} submitted — pending approval.`); load(); }
                        else if (r.status === 403) setNotice('Only organisation owners can publish apps.');
                      } finally { setBusy(null); }
                    }}
                    style={{ fontSize: '0.68rem', border: '1px solid #d8d4cc', borderRadius: 5, background: '#fff', color: '#6b6b6b', cursor: 'pointer', padding: '3px 10px', fontFamily: 'inherit' }}>
                    Publish
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
      {community.length > 0 && (
        <div>
          <div style={{ fontSize: '0.68rem', fontWeight: 700, color: '#8a8a8a', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>From the community</div>
          {community.map((c) => (
            <div key={c.slug} style={{ display: 'flex', alignItems: 'center', gap: 10, border: '1px solid #e2ddd7', borderRadius: 8, background: '#fff', padding: '0.5rem 0.8rem', marginBottom: 6, fontSize: '0.76rem' }}>
              <span style={{ fontWeight: 600 }}>{c.name}</span>
              {c.status === 'pending' && badge('pending approval', '#8a5a1f', '#fdf3e4')}
              {c.price_cents > 0 && <span style={{ color: '#8a5a1f', fontWeight: 600 }}>{price(c.price_cents)}/mo</span>}
              <span style={{ flex: 1, fontSize: '0.7rem', color: '#8a8a8a' }}>{c.description}</span>
              {isAdmin && c.status === 'pending' && (
                <button type="button" onClick={async () => { const r = await apiFetch(`/api/marketplace/${c.slug}/approve`, { method: 'POST' }); if (r.ok) load(); }}
                  style={{ fontSize: '0.68rem', border: '1px solid #2e7d4f', borderRadius: 5, background: '#eef6f0', color: '#2e7d4f', cursor: 'pointer', padding: '3px 10px', fontFamily: 'inherit' }}>
                  Approve
                </button>
              )}
              {isOwner && c.status === 'active' && (
                <button type="button" disabled={busy === c.slug} onClick={() => setEntitled(c.slug, !c.enabled, c.name)}
                  style={{ fontSize: '0.68rem', border: '1px solid #d8d4cc', borderRadius: 5, background: c.enabled ? '#fff' : '#2e7d4f', color: c.enabled ? '#6b6b6b' : '#fff', cursor: 'pointer', padding: '3px 10px', fontFamily: 'inherit' }}>
                  {c.enabled ? 'Disable' : 'Enable'}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <div style={{ height: 40 }} />
    </div>
  );
}
