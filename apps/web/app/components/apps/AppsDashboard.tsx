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

import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { apiFetch, getStoredUser } from '../../lib/api';
import { setPageDocument } from '../../lib/pageDocument';
import { AGENTS } from '../layout/Sidebar';
import AppRunner from './AppRunner';
import AppIcon from '../ui/AppIcon';
import type { AppIconSource } from '../ui/appIcons';
import { useRequestPageFill } from '../pages/pageFill';
import BackLink from '../ui/BackLink';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
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
  /** built into Norm (Norm Hiring, Norm Training) — everyone in the org has it */
  builtin?: boolean;
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

/** A venue's connection state, as the tone of its badge. */
const CONN_TONE: Record<string, BadgeTone> = {
  connected: 'ok',
  needs_reconnect: 'warn',
  not_connected: 'neutral',
};

// One row look for both lists (and the standalone /apps route): the app's
// line icon on a tile, name 14/600, a 13px muted description, actions right.
// The row wraps on a phone, so the actions drop under the name.
const ROW: CSSProperties = { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px 12px', padding: '12px 16px' };
const ROW_TEXT: CSSProperties = { flex: '1 1 220px', minWidth: 0 };
const NAME_LINE: CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' };
const NAME: CSSProperties = { fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' };
const DESCRIPTION: CSSProperties = { marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };
const ACTIONS: CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'flex-end', flexWrap: 'wrap', gap: '8px 12px', marginLeft: 'auto' };
const DIVIDER = '1px solid var(--line)';

function IconTile({ app }: { app: AppIconSource }) {
  return (
    <span aria-hidden style={{ flex: '0 0 auto', width: 32, height: 32, borderRadius: 'var(--radius)', background: 'var(--surface-alt)', color: 'var(--text-soft)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
      <AppIcon app={app} size="menu" tone="inherit" />
    </span>
  );
}

/** A list's title with its one muted line; smaller in a conversation. */
function SectionHead({ title, meta, page }: { title: string; meta?: ReactNode; page: boolean }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
      <h2 style={{ margin: 0, fontSize: page ? 'var(--fs-lg)' : 'var(--fs-md)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h2>
      {meta && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta}</span>}
    </div>
  );
}

export default function AppsDashboard({ props }: DisplayBlockProps) {
  const [apps, setApps] = useState<AppRow[] | null>(null);
  const [catalog, setCatalog] = useState<CatalogApp[] | null>(null);
  const [openSlug, setOpenSlug] = useState<string | null>(
    (props?.openSlug as string) || null,
  );
  // A page instance (FunctionalPage marks it) hands an open app the whole area.
  const isPage = !!props?.persistVenue;
  useRequestPageFill(isPage && !!openSlug);
  const [busy, setBusy] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [connInfo, setConnInfo] = useState<Record<string, ConnVenue[] | 'loading'>>({});
  const [notice, setNotice] = useState<string | null>(null);
  // A failed load still leaves an empty list (as before); these only let the
  // page say it failed instead of showing "No apps yet".
  const [appsFailed, setAppsFailed] = useState(false);
  const [catalogFailed, setCatalogFailed] = useState(false);
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
      .then((r) => { setAppsFailed(!r.ok); return r.ok ? r.json() : { apps: [] }; })
      // Norm's built-in apps (Hiring, Training) aren't built by the team —
      // they live in their team member's menu and on the Team page.
      .then((d) => setApps(((d.apps ?? []) as AppRow[]).filter((a) => !a.builtin)))
      .catch(() => { setAppsFailed(true); setApps([]); });
    apiFetch('/api/marketplace')
      .then((r) => { setCatalogFailed(!r.ok); return r.ok ? r.json() : { apps: [] }; })
      // Hierarchy v2: team members (tier 'agent') and their Apps (tier 'app')
      // are hired/managed on the team page — this hub keeps the community
      // shelf and the team's own apps only.
      .then((d) => setCatalog(((d.apps ?? []) as CatalogApp[]).filter((a) => a.tier !== 'agent' && a.tier !== 'app')))
      .catch(() => { setCatalogFailed(true); setCatalog([]); });
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
    // On the Apps page the app takes the whole area, like any app page; in a
    // conversation it is a card with a way back to the list.
    return isPage ? (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <AppRunner slug={openSlug} variant="page" back={{ label: 'All apps', onClick: () => setOpenSlug(null) }} />
      </div>
    ) : (
      <div>
        <div style={{ marginBottom: 8 }}><BackLink label="All apps" onClick={() => setOpenSlug(null)} /></div>
        <AppRunner slug={openSlug} variant="embedded" />
      </div>
    );
  }

  const catalogList = catalog ?? [];
  const appList = apps ?? [];
  const showTeam = !marketplaceOnly;
  const loading = catalog === null || (showTeam && apps === null);
  // The marketplace keeps its heading only when it has something to show.
  const showMarketplace = catalogFailed || catalogList.length > 0;

  const marketplace = (
    <section style={{ marginBottom: 28 }}>
      <SectionHead
        title="Marketplace"
        meta="Apps for your organisation — owners enable them; connections are managed per venue"
        page={isPage}
      />
      {catalogFailed ? (
        <PageState kind="error" title="Couldn’t load the marketplace" detail="Refresh the page to try again." />
      ) : (
        <div className="n-card" style={{ overflow: 'hidden' }}>
          {catalogList.map((app, i) => {
            const comp = app.composition || {};
            const pages = (comp.components || []).filter((c) => c.page);
            const isOpen = expanded === app.slug;
            return (
              <div key={app.slug} style={{ borderTop: i ? DIVIDER : undefined }}>
                <div style={{ ...ROW, cursor: 'pointer', background: isOpen ? 'var(--surface)' : undefined }} onClick={() => expand(app)}>
                  <IconTile app={app} />
                  <div style={ROW_TEXT}>
                    <div style={NAME_LINE}>
                      <span style={NAME}>{app.name}</span>
                      <Badge>{TIER_LABEL[app.tier] ?? app.tier}</Badge>
                      {app.status === 'active' && app.enabled && <Badge tone="ok">Enabled</Badge>}
                      {app.status === 'pending' && <Badge tone="warn">Pending approval</Badge>}
                      {app.price_cents > 0 && (
                        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>${(app.price_cents / 100).toFixed(0)}/mo</span>
                      )}
                    </div>
                    {app.description && <div style={DESCRIPTION}>{app.description}</div>}
                  </div>
                  <div style={ACTIONS}>
                    {app.status === 'pending' && isAdmin && (
                      <Button size="sm" variant="secondary" onClick={(e) => { e.stopPropagation(); void approveApp(app); }}>
                        Approve
                      </Button>
                    )}
                    {app.status === 'active' && (
                      app.enabled ? (
                        <Button size="sm" variant="secondary" disabled={busy === app.slug}
                          onClick={(e) => { e.stopPropagation(); void setEnabled(app, false); }}>
                          Disable
                        </Button>
                      ) : (
                        <Button size="sm" variant="secondary" disabled={busy === app.slug}
                          onClick={(e) => { e.stopPropagation(); void setEnabled(app, true); }}>
                          Enable
                        </Button>
                      )
                    )}
                    <Icon icon={isOpen ? ChevronDown : ChevronRight} size={16} tone="muted" />
                  </div>
                </div>

                {isOpen && (
                  <div style={{ display: 'grid', gap: 12, padding: '4px 16px 14px 60px', background: 'var(--surface)', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                    {pages.length > 0 && (
                      <div>
                        <div className="n-eyebrow" style={{ marginBottom: 4 }}>Pages</div>
                        {pages.map((c) => (
                          <div key={c.key} style={{ padding: '2px 0' }}>
                            <span style={{ fontWeight: 500, color: 'var(--text)' }}>{c.page!.label}</span>
                            <span style={{ color: 'var(--muted)' }}> — under {agentLabel(c.agent)}</span>
                            {c.description ? <span style={{ color: 'var(--muted)' }}> · {c.description}</span> : null}
                          </div>
                        ))}
                      </div>
                    )}
                    {(comp.connections || []).length > 0 && (
                      <div>
                        <div className="n-eyebrow" style={{ marginBottom: 4 }}>Connections this app uses</div>
                        {(comp.connections || []).map((conn) => {
                          const info = connInfo[conn];
                          return (
                            <div key={conn} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', padding: '3px 0' }}>
                              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--text)', marginRight: 2 }}>{conn}</span>
                              {info === 'loading' || !info ? (
                                <span style={{ color: 'var(--muted)' }}>Checking…</span>
                              ) : (
                                <>
                                  {(info as ConnVenue[]).map((v) => (
                                    <Badge key={v.venue_id} tone={CONN_TONE[v.status] ?? 'neutral'} title={v.status.replace('_', ' ')}>
                                      {v.venue_name}
                                    </Badge>
                                  ))}
                                  {(info as ConnVenue[]).some((v) => v.status !== 'connected') && (
                                    <span style={{ color: 'var(--warn)' }}>Connect in Settings → Connections</span>
                                  )}
                                </>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {pages.length === 0 && (comp.connections || []).length === 0 && (
                      <div style={{ color: 'var(--muted)' }}>No pages or connections.</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );

  const teamApps = (
    <section>
      <SectionHead
        title="Built by your team"
        // On the page the header already says how to build one.
        meta={isPage ? undefined : 'Describe a new one to the App Builder in chat'}
        page={isPage}
      />
      {appsFailed ? (
        <PageState kind="error" title="Couldn’t load your team’s apps" detail="Refresh the page to try again." />
      ) : appList.length === 0 ? (
        <PageState
          kind="empty"
          title="No apps yet"
          detail={<>Try &ldquo;build me a weekly venue performance dashboard&rdquo;.</>}
        />
      ) : (
        <div className="n-card" style={{ overflow: 'hidden' }}>
          {appList.map((a, i) => {
            const sub = submissionFor(a.slug);
            return (
              <div key={a.slug} style={{ ...ROW, borderTop: i ? DIVIDER : undefined }}>
                <IconTile app={a} />
                <div style={{ ...ROW_TEXT, cursor: 'pointer' }} onClick={() => setOpenSlug(a.slug)}>
                  <div style={NAME_LINE}>
                    <span style={NAME}>{a.name}</span>
                    <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                      {a.builtin ? 'Built into Norm' : a.mine ? 'Yours' : `Shared · ${a.access}`}
                      {!a.builtin && a.visibility !== 'private' && ` · ${a.visibility}`}
                    </span>
                    {sub && (
                      <Badge tone={sub.status === 'pending' ? 'warn' : 'ok'}>
                        {sub.status === 'pending' ? 'In review' : 'In marketplace'}
                      </Badge>
                    )}
                  </div>
                  {a.description && <div style={DESCRIPTION}>{a.description}</div>}
                </div>
                <div style={ACTIONS}>
                  {!sub && !a.builtin && (
                    <Button size="sm" variant="quiet" title="Publish this app to the marketplace (owners only)"
                      onClick={() => { void submitApp(a); }} disabled={busy === a.slug}>
                      Publish
                    </Button>
                  )}
                  <label title={`Show this app as a page link under ${agentLabel(a.agent)} (only for you)`}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', whiteSpace: 'nowrap', cursor: 'pointer' }}>
                    <input type="checkbox" checked={a.pinned} disabled={busy === a.slug}
                      onChange={() => { void togglePin(a); }}
                      style={{ width: 16, height: 16, margin: 0, accentColor: 'var(--accent)', cursor: 'pointer' }} />
                    Show in {agentLabel(a.agent)}
                  </label>
                  <Button size="sm" variant="secondary" onClick={() => setOpenSlug(a.slug)}>
                    Open
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );

  // A page sits straight on the page frame (FunctionalPage's gutters); in a
  // conversation there is no page header, just the lists.
  return (
    <div>
      {isPage && (
        <PageHeader
          title="Apps"
          meta={showTeam ? 'Describe a new app to the App Builder in chat, and it appears here.' : undefined}
        />
      )}
      {notice && (
        <div role="status" style={{ margin: '0 0 16px', padding: '8px 12px', borderRadius: 'var(--radius)', background: 'var(--warn-bg)', color: 'var(--warn)', fontSize: 'var(--fs-sm)' }}>
          {notice}
        </div>
      )}
      {loading ? (
        <PageState kind="loading" title="Loading apps…" />
      ) : (
        <>
          {showMarketplace && marketplace}
          {marketplaceOnly && !showMarketplace && <PageState kind="empty" title="No apps in the marketplace yet" />}
          {showTeam && teamApps}
        </>
      )}
    </div>
  );
}
