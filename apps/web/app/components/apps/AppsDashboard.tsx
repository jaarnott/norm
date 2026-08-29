'use client';

/**
 * The Apps page: the **Marketplace** (every catalog App — what it lights up,
 * what it costs, which connections it needs and their per-venue readiness,
 * enable/disable for organisation owners) above the team's own apps (opened
 * inline, with the per-user "in nav" pin — the original dashboard behaviour).
 *
 * Apps and Connections are separate things (docs/apps-marketplace-plan.md):
 * enabling an app is an org-level act; connecting a pipe is per-venue. A card
 * shows both so "enabled but not connected for Bessie" is visible, not a
 * mystery.
 *
 * Pinning fires `norm:app-pages-changed` so the shell can refresh its dynamic
 * page list without a reload.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiFetch, getStoredUser } from '../../lib/api';
import { setPageDocument } from '../../lib/pageDocument';
import { AGENTS } from '../layout/Sidebar';
import AppRunner from './AppRunner';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';

export const APP_PAGES_CHANGED_EVENT = 'norm:app-pages-changed';

/** Where a pinned app's page link appears. Apps can join another agent's menu
 *  now, so a bare "in nav" would leave people hunting for it. */
const agentLabel = (slug: string) =>
  AGENTS.find((a) => a.id === slug)?.label ?? 'nav';

interface AppRow {
  slug: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  visibility: string;
  mine: boolean;
  access: string;
  pinned: boolean;
  agent: string;
}

interface CatalogComponent {
  key: string;
  agent: string;
  page?: { id: string; label: string; icon?: string } | null;
  description?: string;
}
interface CatalogApp {
  slug: string;
  name: string;
  description: string;
  icon?: string | null;
  tier: string;
  bundled: boolean;
  price_cents: number;
  status: string;
  enabled: boolean;
  composition: {
    connections?: string[];
    app_slug?: string;
    origin_org?: string;
    agents?: string[];
    owns_agents?: string[];
    components?: CatalogComponent[];
  };
}
interface ConnVenue { venue_id: string; venue_name: string; status: string }

const TIER_LABEL: Record<string, string> = {
  integration: 'Integration',
  platform: 'Norm app',
  user: 'Community',
};

const STATUS_DOT: Record<string, string> = {
  connected: '#2e7d4f',
  needs_reconnect: '#b8860b',
  not_connected: '#b0aca4',
};

export default function AppsDashboard({ props }: DisplayBlockProps) {
  const [apps, setApps] = useState<AppRow[] | null>(null);
  const [catalog, setCatalog] = useState<CatalogApp[] | null>(null);
  const [openSlug, setOpenSlug] = useState<string | null>(
    (props?.openSlug as string) || null,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [connInfo, setConnInfo] = useState<Record<string, ConnVenue[] | 'loading'>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const isAdmin = getStoredUser()?.role === 'admin';
  // Settings → Apps mounts just the marketplace half: no team-apps section,
  // and no page-document publishing (that belongs to the conversation panel).
  const marketplaceOnly = !!props?.marketplaceOnly;

  // While LISTING, the chat sees the visible apps — "rename this app" with
  // one app on screen resolves without a question. An OPEN app publishes
  // itself from AppRunner (mounted below), which overwrites this; coming back
  // to the list republishes it here.
  useEffect(() => {
    if (marketplaceOnly || openSlug || apps === null) return undefined;
    setPageDocument({
      kind: 'apps_list',
      apps: apps.map((a) => ({ slug: a.slug, name: a.name })),
    });
    return () => setPageDocument(null);
  }, [openSlug, apps]);

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

  const togglePin = async (a: AppRow) => {
    if (busy) return;
    setBusy(a.slug);
    try {
      const r = await apiFetch(`/api/apps/${a.slug}/pin`, {
        method: 'POST',
        body: JSON.stringify({ pinned: !a.pinned }),
      });
      if (r.ok) {
        setApps((prev) =>
          (prev ?? []).map((x) => (x.slug === a.slug ? { ...x, pinned: !a.pinned } : x)),
        );
        window.dispatchEvent(new CustomEvent(APP_PAGES_CHANGED_EVENT));
      }
    } finally {
      setBusy(null);
    }
  };

  const setEnabled = async (app: CatalogApp, enabled: boolean) => {
    if (busy) return;
    setBusy(app.slug);
    setNotice(null);
    try {
      const r = await apiFetch(`/api/marketplace/${app.slug}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' });
      if (r.ok) {
        setCatalog((prev) => (prev ?? []).map((x) => (x.slug === app.slug ? { ...x, enabled } : x)));
      } else if (r.status === 403) {
        setNotice('Only organisation owners can enable or disable apps.');
      } else {
        const b = await r.json().catch(() => ({}));
        setNotice(b.detail || `Couldn't update ${app.name}`);
      }
    } finally {
      setBusy(null);
    }
  };

  const expand = (app: CatalogApp) => {
    const next = expanded === app.slug ? null : app.slug;
    setExpanded(next);
    if (next) {
      for (const conn of app.composition.connections || []) {
        if (!connInfo[conn]) {
          setConnInfo((p) => ({ ...p, [conn]: 'loading' }));
          apiFetch(`/api/connectors/${conn}/connect-info`)
            .then((r) => (r.ok ? r.json() : { venues: [] }))
            .then((d) => setConnInfo((p) => ({ ...p, [conn]: d.venues ?? [] })))
            .catch(() => setConnInfo((p) => ({ ...p, [conn]: [] })));
        }
      }
    }
  };

  const submitApp = async (a: AppRow) => {
    if (busy) return;
    setBusy(a.slug);
    setNotice(null);
    try {
      const r = await apiFetch('/api/marketplace/submit', {
        method: 'POST',
        body: JSON.stringify({ app_slug: a.slug }),
      });
      if (r.ok) {
        setNotice(`${a.name} submitted to the marketplace — pending approval.`);
        load();
      } else if (r.status === 403) {
        setNotice('Only organisation owners can publish apps to the marketplace.');
      }
    } finally {
      setBusy(null);
    }
  };

  const approveApp = async (app: CatalogApp) => {
    const r = await apiFetch(`/api/marketplace/${app.slug}/approve`, { method: 'POST' });
    if (r.ok) load();
  };

  // team app slug -> its marketplace submission (if any)
  const submissionFor = (slug: string) =>
    (catalog ?? []).find((c) => c.tier === 'user' && c.composition.app_slug === slug);

  if (openSlug) {
    return (
      <div>
        <button type="button" onClick={() => setOpenSlug(null)}
          style={{ margin: '0.6rem 1rem 0', fontSize: '0.72rem', border: '1px solid #d8d4cc', borderRadius: 5, background: '#fff', color: '#6b6b6b', cursor: 'pointer', padding: '3px 10px', fontFamily: 'inherit' }}>
          ← All apps
        </button>
        <AppRunner slug={openSlug} />
      </div>
    );
  }

  const badge = (text: string, color: string, bg: string) => (
    <span style={{ fontSize: '0.6rem', fontWeight: 700, color, background: bg, borderRadius: 8, padding: '2px 8px', whiteSpace: 'nowrap', textTransform: 'uppercase', letterSpacing: '0.03em' }}>{text}</span>
  );

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '1.2rem 1rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Marketplace</h2>
        <span style={{ fontSize: '0.72rem', color: '#8a8a8a' }}>
          apps for your organisation — owners enable them; connections are managed per venue
        </span>
      </div>
      {notice && (
        <div style={{ margin: '8px 0', padding: '6px 12px', borderRadius: 8, background: '#fdf6ec', color: '#8a6d3b', fontSize: '0.76rem' }}>{notice}</div>
      )}

      {catalog === null ? (
        <div style={{ color: '#888', padding: '1rem 0' }}>Loading marketplace…</div>
      ) : (
        catalog.map((app) => {
          const comp = app.composition || {};
          const pages = (comp.components || []).filter((c) => c.page);
          const isOpen = expanded === app.slug;
          return (
            <div key={app.slug} style={{ border: '1px solid #e5e2dc', borderRadius: 10, marginTop: 10, background: '#fff' }}>
              <div style={{ padding: '0.7rem 1rem', display: 'flex', gap: 12, alignItems: 'center', cursor: 'pointer' }} onClick={() => expand(app)}>
                <span style={{ fontSize: '1.2rem' }}>{app.icon}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <strong style={{ fontSize: '0.92rem' }}>{app.name}</strong>
                    {badge(TIER_LABEL[app.tier] ?? app.tier, '#6b655c', '#f2efe9')}
                    {app.status === 'pending' && badge('pending approval', '#8a6d3b', '#fdf6ec')}
                    {app.price_cents > 0 && (
                      <span style={{ fontSize: '0.68rem', color: '#6b655c' }}>${(app.price_cents / 100).toFixed(0)}/mo</span>
                    )}
                  </div>
                  <div style={{ fontSize: '0.72rem', color: '#8a8a8a', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {app.description}
                  </div>
                </div>
                {app.status === 'pending' && isAdmin && (
                  <button type="button" onClick={(e) => { e.stopPropagation(); void approveApp(app); }}
                    style={{ fontSize: '0.7rem', border: 'none', borderRadius: 5, background: '#8a6d3b', color: '#fff', cursor: 'pointer', padding: '4px 12px', fontFamily: 'inherit' }}>
                    Approve
                  </button>
                )}
                {app.status === 'active' && (
                  app.enabled ? (
                    <button type="button" disabled={busy === app.slug}
                      onClick={(e) => { e.stopPropagation(); void setEnabled(app, false); }}
                      style={{ fontSize: '0.7rem', border: '1px solid #d8d4cc', borderRadius: 5, background: '#fff', color: '#6b6b6b', cursor: 'pointer', padding: '4px 12px', fontFamily: 'inherit' }}>
                      Disable
                    </button>
                  ) : (
                    <button type="button" disabled={busy === app.slug}
                      onClick={(e) => { e.stopPropagation(); void setEnabled(app, true); }}
                      style={{ fontSize: '0.7rem', border: 'none', borderRadius: 5, background: '#2e7d4f', color: '#fff', cursor: 'pointer', padding: '4px 12px', fontFamily: 'inherit' }}>
                      Enable
                    </button>
                  )
                )}
                <span style={{ color: '#b0aca4' }}>{isOpen ? '▾' : '▸'}</span>
              </div>

              {isOpen && (
                <div style={{ borderTop: '1px solid #f0ede8', padding: '0.7rem 1rem 0.9rem', fontSize: '0.76rem', color: '#555' }}>
                  {pages.length > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ fontWeight: 700, fontSize: '0.66rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: '#8a8a8a', marginBottom: 3 }}>Pages</div>
                      {pages.map((c) => (
                        <div key={c.key}>
                          {c.page!.label} <span style={{ color: '#8a8a8a' }}>— under {agentLabel(c.agent)}</span>
                          {c.description ? <span style={{ color: '#b0aca4' }}> · {c.description}</span> : null}
                        </div>
                      ))}
                    </div>
                  )}
                  {(comp.connections || []).length > 0 && (
                    <div>
                      <div style={{ fontWeight: 700, fontSize: '0.66rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: '#8a8a8a', marginBottom: 3 }}>
                        Connections this app uses
                      </div>
                      {(comp.connections || []).map((conn) => {
                        const info = connInfo[conn];
                        return (
                          <div key={conn} style={{ marginBottom: 4 }}>
                            <span style={{ fontFamily: 'monospace', fontSize: '0.72rem' }}>{conn}</span>
                            {info === 'loading' || !info ? (
                              <span style={{ color: '#b0aca4' }}> — checking…</span>
                            ) : (
                              <span>
                                {(info as ConnVenue[]).map((v) => (
                                  <span key={v.venue_id} title={v.status.replace('_', ' ')}
                                    style={{ marginLeft: 8, whiteSpace: 'nowrap' }}>
                                    <span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: 4, background: STATUS_DOT[v.status] ?? '#b0aca4', marginRight: 3 }} />
                                    {v.venue_name}
                                  </span>
                                ))}
                                {(info as ConnVenue[]).some((v) => v.status !== 'connected') && (
                                  <span style={{ color: '#8a6d3b', marginLeft: 8 }}>· connect in Settings → Connections</span>
                                )}
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })
      )}

      {!marketplaceOnly && (<>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, margin: '1.6rem 0 4px' }}>
        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Built by your team</h2>
        <span style={{ fontSize: '0.72rem', color: '#8a8a8a' }}>
          describe a new one to the App Builder in chat
        </span>
      </div>
      {apps === null ? (
        <div style={{ color: '#888', padding: '1.5rem 0' }}>Loading…</div>
      ) : apps.length === 0 ? (
        <div style={{ border: '1px dashed #d8d4cc', borderRadius: 10, padding: '2rem', color: '#8a8a8a', fontSize: '0.85rem', marginTop: 10 }}>
          No apps yet. Tell the App Builder what you want — &ldquo;build me a weekly
          venue performance dashboard&rdquo; — and it will appear here.
        </div>
      ) : (
        apps.map((a) => {
          const sub = submissionFor(a.slug);
          return (
            <div key={a.slug}
              style={{ border: '1px solid #e5e2dc', borderRadius: 10, padding: '0.8rem 1rem', marginTop: 10, background: '#fff', display: 'flex', gap: 12, alignItems: 'center' }}>
              <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={() => setOpenSlug(a.slug)}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <strong style={{ fontSize: '0.92rem' }}>{a.icon} {a.name}</strong>
                  <span style={{ fontSize: '0.62rem', color: a.mine ? '#2e7d4f' : '#8a6d3b', whiteSpace: 'nowrap' }}>
                    {a.mine ? 'yours' : `shared · ${a.access}`}
                    {a.visibility !== 'private' && ` · ${a.visibility}`}
                  </span>
                  {sub && badge(sub.status === 'pending' ? 'in review' : 'in marketplace', '#6b655c', '#f2efe9')}
                </div>
                <div style={{ fontSize: '0.72rem', color: '#8a8a8a', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.description}
                </div>
              </div>
              {!sub && (
                <button type="button" title="Publish this app to the marketplace (owners only)"
                  onClick={() => { void submitApp(a); }} disabled={busy === a.slug}
                  style={{ fontSize: '0.68rem', border: '1px solid #d8d4cc', borderRadius: 5, background: '#fff', color: '#6b6b6b', cursor: 'pointer', padding: '4px 10px', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
                  Publish
                </button>
              )}
              <label title={`show this app as a page link under ${agentLabel(a.agent)} (only for you)`}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: '0.66rem', color: '#6b655c', whiteSpace: 'nowrap', cursor: 'pointer' }}>
                <input type="checkbox" checked={a.pinned} disabled={busy === a.slug}
                  onChange={() => { void togglePin(a); }} />
                in {agentLabel(a.agent)}
              </label>
              <button type="button" onClick={() => setOpenSlug(a.slug)}
                style={{ fontSize: '0.72rem', border: 'none', borderRadius: 5, background: '#2e7d4f', color: '#fff', cursor: 'pointer', padding: '4px 14px', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
                Open
              </button>
            </div>
          );
        })
      )}
      </>)}
    </div>
  );
}
