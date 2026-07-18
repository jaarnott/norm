'use client';

/**
 * MCP consent screen.
 *
 * This lives in the Next.js app, not the API, because the user's session is in
 * localStorage (no auth cookie) — only the SPA can identify the browser user.
 * The API's /authorize endpoint validates the request and 302s here.
 *
 * The user approves which organization, which venues, and which permissions an
 * external client (Claude) may act with. Everything shown is server-supplied;
 * the client_name in particular is attacker-controlled (DCR) and is rendered as
 * text only — never as HTML.
 */

import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { apiFetch, getToken } from '../../lib/api';

interface Venue { id: string; name: string; }
interface OrgContext {
  organization_id: string;
  organization_name: string;
  role_display_name: string;
  venues: Venue[];
  grantable_scopes: string[];
}
interface RequestedScope {
  scope: string; label: string; description: string; access_level: string;
}
interface ConsentContext {
  client: { client_id: string; client_name: string; client_uri?: string; logo_uri?: string };
  user: { id: string; email: string; full_name: string };
  organizations: OrgContext[];
  requested_scopes: RequestedScope[];
}

function ConsentScreen() {
  const params = useSearchParams();
  const clientId = params.get('client_id') || '';
  const redirectUri = params.get('redirect_uri') || '';
  const scope = params.get('scope') || '';

  const [ctx, setCtx] = useState<ConsentContext | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const [orgId, setOrgId] = useState('');
  const [venueIds, setVenueIds] = useState<string[]>([]);
  const [approvedScopes, setApprovedScopes] = useState<string[]>([]);

  // Not logged in → stash params, bounce to login, come back here.
  useEffect(() => {
    if (!getToken()) {
      const here = `/mcp/authorize?${params.toString()}`;
      window.location.href = `/login?next=${encodeURIComponent(here)}`;
    }
  }, [params]);

  useEffect(() => {
    if (!getToken()) return;
    (async () => {
      try {
        const res = await apiFetch(
          `/api/mcp/oauth/consent-context?client_id=${encodeURIComponent(clientId)}` +
            `&scope=${encodeURIComponent(scope)}&redirect_uri=${encodeURIComponent(redirectUri)}`,
        );
        if (!res.ok) { setError('This authorization request is invalid or has expired.'); return; }
        const data: ConsentContext = await res.json();
        setCtx(data);
        if (data.organizations.length > 0) {
          const first = data.organizations[0];
          setOrgId(first.organization_id);
          setVenueIds(first.venues.map((v) => v.id));
        }
        // Pre-select every requested scope the chosen org can actually grant.
        setApprovedScopes(data.requested_scopes.map((s) => s.scope));
      } catch {
        setError('Could not load the authorization request.');
      } finally {
        setLoading(false);
      }
    })();
  }, [clientId, scope, redirectUri]);

  const currentOrg = ctx?.organizations.find((o) => o.organization_id === orgId);

  function grantableHere(s: string): boolean {
    return !!currentOrg?.grantable_scopes.includes(s);
  }

  async function submit(action: 'approve' | 'deny') {
    setSubmitting(true);
    try {
      const res = await apiFetch('/api/mcp/oauth/consent', {
        method: 'POST',
        body: JSON.stringify({
          client_id: clientId,
          redirect_uri: redirectUri,
          scope,
          state: params.get('state') || undefined,
          code_challenge: params.get('code_challenge') || '',
          code_challenge_method: params.get('code_challenge_method') || 'S256',
          resource: params.get('resource') || undefined,
          organization_id: orgId,
          venue_ids: venueIds,
          approved_scopes: action === 'approve'
            ? approvedScopes.filter(grantableHere)
            : [],
          action,
        }),
      });
      const data = await res.json();
      if (res.ok && data.redirect_to) {
        window.location.href = data.redirect_to;
      } else {
        setError(data.detail || 'Authorization failed.');
        setSubmitting(false);
      }
    } catch {
      setError('Authorization failed.');
      setSubmitting(false);
    }
  }

  if (loading) return <div className="mcp-consent"><p>Loading…</p></div>;
  if (error) return <div className="mcp-consent"><p className="mcp-error">{error}</p></div>;
  if (!ctx) return null;

  const noVenues = currentOrg && currentOrg.venues.length === 0;
  const selectableScopes = approvedScopes.filter(grantableHere);

  return (
    <div className="mcp-consent">
      <div className="mcp-card">
        <header className="mcp-header">
          {ctx.client.logo_uri ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={ctx.client.logo_uri} alt="" className="mcp-logo" />
          ) : null}
          {/* client_name is DCR-supplied — rendered as text, never HTML */}
          <h1><strong>{ctx.client.client_name}</strong> wants to access your Norm account</h1>
        </header>

        <p className="mcp-signed-in">
          Signed in as {ctx.user.full_name} ({ctx.user.email})
        </p>

        {ctx.organizations.length > 1 && (
          <section className="mcp-section">
            <h2>Organization</h2>
            {ctx.organizations.map((o) => (
              <label key={o.organization_id} className="mcp-radio">
                <input
                  type="radio" name="org" checked={orgId === o.organization_id}
                  onChange={() => { setOrgId(o.organization_id); setVenueIds(o.venues.map((v) => v.id)); }}
                />
                {o.organization_name} <span className="mcp-role">({o.role_display_name})</span>
              </label>
            ))}
          </section>
        )}

        <section className="mcp-section">
          <h2>Venues</h2>
          <p className="mcp-hint">Claude will only see data for the venues you select.</p>
          {noVenues ? (
            <p className="mcp-error">You don&apos;t have access to any venues in this organization.</p>
          ) : (
            currentOrg?.venues.map((v) => (
              <label key={v.id} className="mcp-check">
                <input
                  type="checkbox" checked={venueIds.includes(v.id)}
                  onChange={(e) =>
                    setVenueIds(e.target.checked
                      ? [...venueIds, v.id]
                      : venueIds.filter((id) => id !== v.id))}
                />
                {v.name}
              </label>
            ))
          )}
        </section>

        <section className="mcp-section">
          <h2>Permissions</h2>
          {ctx.requested_scopes.map((s) => {
            const allowed = grantableHere(s.scope);
            const isDraft = s.access_level === 'draft';
            return (
              <label key={s.scope} className={`mcp-scope ${isDraft ? 'mcp-scope-draft' : ''}`}>
                <input
                  type="checkbox" disabled={!allowed}
                  checked={allowed && approvedScopes.includes(s.scope)}
                  onChange={(e) =>
                    setApprovedScopes(e.target.checked
                      ? [...approvedScopes, s.scope]
                      : approvedScopes.filter((x) => x !== s.scope))}
                />
                <span>
                  <strong>{s.label}</strong>
                  {isDraft && <span className="mcp-badge">draft</span>}
                  <br /><span className="mcp-desc">{s.description}</span>
                  {!allowed && <span className="mcp-desc"> — your role doesn&apos;t allow this</span>}
                </span>
              </label>
            );
          })}
        </section>

        <footer className="mcp-footer">
          <p className="mcp-hint">You can disconnect at any time in Settings → Connections.</p>
          <div className="mcp-actions">
            <button className="mcp-deny" onClick={() => submit('deny')} disabled={submitting}>
              Deny
            </button>
            <button
              className="mcp-approve"
              onClick={() => submit('approve')}
              disabled={submitting || noVenues || selectableScopes.length === 0}
            >
              {submitting ? 'Authorizing…' : 'Approve'}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

export default function McpAuthorizePage() {
  return (
    <Suspense fallback={<div className="mcp-consent"><p>Loading…</p></div>}>
      <ConsentScreen />
    </Suspense>
  );
}
