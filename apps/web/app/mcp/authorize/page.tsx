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

  if (loading) return <Shell><p style={S.muted}>Loading…</p></Shell>;
  if (error) return <Shell><p style={S.errorBox}>{error}</p></Shell>;
  if (!ctx) return null;

  const noVenues = currentOrg && currentOrg.venues.length === 0;
  const selectableScopes = approvedScopes.filter(grantableHere);
  const canApprove = !submitting && !noVenues && selectableScopes.length > 0;

  return (
    <Shell>
      <header style={{ display: 'flex', alignItems: 'center', gap: '0.85rem', marginBottom: '1.25rem' }}>
        {ctx.client.logo_uri ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={ctx.client.logo_uri} alt="" width={44} height={44}
               style={{ borderRadius: 10, objectFit: 'cover', flexShrink: 0 }} />
        ) : (
          <div style={S.logoFallback}>{initial(ctx.client.client_name)}</div>
        )}
        <h1 style={S.h1}>
          {/* client_name is DCR-supplied — rendered as text, never HTML */}
          <strong style={{ fontWeight: 700 }}>{ctx.client.client_name}</strong> wants to connect to your Norm account
        </h1>
      </header>

      <p style={S.signedIn}>
        Signed in as <strong style={{ color: '#555', fontWeight: 600 }}>{ctx.user.full_name}</strong> · {ctx.user.email}
      </p>

      {ctx.organizations.length > 1 && (
        <Section title="Organization">
          {ctx.organizations.map((o) => (
            <label key={o.organization_id} style={S.row}>
              <input type="radio" name="org" checked={orgId === o.organization_id}
                     onChange={() => { setOrgId(o.organization_id); setVenueIds(o.venues.map((v) => v.id)); }}
                     style={S.control} />
              <span>{o.organization_name} <span style={S.muted}>· {o.role_display_name}</span></span>
            </label>
          ))}
        </Section>
      )}

      <Section title="Venues" hint="Claude will only see data for the venues you select.">
        {noVenues ? (
          <p style={S.errorBox}>You don&apos;t have access to any venues in this organization.</p>
        ) : (
          currentOrg?.venues.map((v) => (
            <label key={v.id} style={S.row}>
              <input type="checkbox" checked={venueIds.includes(v.id)}
                     onChange={(e) => setVenueIds(e.target.checked
                       ? [...venueIds, v.id]
                       : venueIds.filter((id) => id !== v.id))}
                     style={S.control} />
              <span>{v.name}</span>
            </label>
          ))
        )}
      </Section>

      <Section title="Permissions" hint="Draft actions prepare items for you to approve in Norm — Claude can never submit or send them.">
        {ctx.requested_scopes.map((s) => {
          const allowed = grantableHere(s.scope);
          const isDraft = s.access_level === 'draft';
          const checked = allowed && approvedScopes.includes(s.scope);
          return (
            <label key={s.scope} style={{ ...S.scopeRow, opacity: allowed ? 1 : 0.55, cursor: allowed ? 'pointer' : 'not-allowed' }}>
              <input type="checkbox" disabled={!allowed} checked={checked}
                     onChange={(e) => setApprovedScopes(e.target.checked
                       ? [...approvedScopes, s.scope]
                       : approvedScopes.filter((x) => x !== s.scope))}
                     style={{ ...S.control, marginTop: '0.15rem' }} />
              <span style={{ lineHeight: 1.45 }}>
                <span style={{ fontWeight: 600, color: '#2a2a2a' }}>{s.label}</span>
                {isDraft && <span style={S.draftBadge}>draft</span>}
                <span style={{ display: 'block', fontSize: '0.8rem', color: '#8a8378', marginTop: 1 }}>
                  {s.description}
                  {!allowed && <span style={{ color: '#b07a4a' }}> — your role doesn&apos;t allow this</span>}
                </span>
              </span>
            </label>
          );
        })}
      </Section>

      <p style={{ ...S.muted, fontSize: '0.78rem', margin: '1.25rem 0 1rem' }}>
        You can disconnect at any time in Settings → Connections.
      </p>
      <div style={{ display: 'flex', gap: '0.6rem' }}>
        <button onClick={() => submit('deny')} disabled={submitting} style={S.denyBtn}>
          Deny
        </button>
        <button onClick={() => submit('approve')} disabled={!canApprove}
                style={{ ...S.approveBtn, opacity: canApprove ? 1 : 0.5, cursor: canApprove ? 'pointer' : 'not-allowed' }}>
          {submitting ? 'Authorizing…' : 'Approve'}
        </button>
      </div>
    </Shell>
  );
}

// ── Presentation helpers ────────────────────────────────────────────────
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: '#faf8f5', fontFamily: 'system-ui, sans-serif', padding: '1.5rem',
    }}>
      <div style={{ width: '100%', maxWidth: 460 }}>
        <div style={{ textAlign: 'center', marginBottom: '1.25rem' }}>
          <span style={{ fontSize: '1.6rem', fontWeight: 800, color: '#a08060' }}>Norm</span>
        </div>
        <div style={{
          background: '#fff', border: '1px solid #ece6df', borderRadius: 16,
          boxShadow: '0 1px 2px rgba(60,50,40,0.04), 0 10px 30px rgba(60,50,40,0.07)',
          padding: '1.75rem',
        }}>
          {children}
        </div>
      </div>
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: '1.4rem', paddingTop: '1.4rem', borderTop: '1px solid #f2ede7' }}>
      <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#a39b8e', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: hint ? '0.3rem' : '0.6rem' }}>{title}</div>
      {hint && <p style={{ fontSize: '0.78rem', color: '#9a9284', margin: '0 0 0.6rem' }}>{hint}</p>}
      {children}
    </div>
  );
}

function initial(name: string): string {
  return (name.trim()[0] || '?').toUpperCase();
}

const S: Record<string, React.CSSProperties> = {
  h1: { fontSize: '1.1rem', fontWeight: 400, color: '#2a2a2a', lineHeight: 1.4, margin: 0 },
  signedIn: { fontSize: '0.82rem', color: '#8a8378', margin: '0.25rem 0 0' },
  muted: { color: '#9a9284' },
  logoFallback: {
    width: 44, height: 44, borderRadius: 10, flexShrink: 0, background: '#f0ebe5',
    color: '#a08060', fontWeight: 700, fontSize: '1.2rem',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
  },
  row: { display: 'flex', alignItems: 'center', gap: '0.6rem', padding: '0.4rem 0', fontSize: '0.9rem', color: '#3a3a3a', cursor: 'pointer' },
  scopeRow: { display: 'flex', alignItems: 'flex-start', gap: '0.6rem', padding: '0.5rem 0', fontSize: '0.9rem' },
  control: { accentColor: '#a08060', width: 16, height: 16, flexShrink: 0 },
  draftBadge: { fontSize: '0.66rem', background: '#f4e8d8', color: '#8a6d3b', padding: '1px 6px', borderRadius: 4, marginLeft: 6, verticalAlign: 'middle', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' },
  errorBox: { fontSize: '0.85rem', color: '#b0442f', background: '#fbeae6', border: '1px solid #f3d2c9', borderRadius: 8, padding: '0.6rem 0.75rem', margin: 0 },
  denyBtn: { flex: '0 0 auto', padding: '0.6rem 1.3rem', fontSize: '0.9rem', fontWeight: 500, color: '#6a6258', background: '#fff', border: '1px solid #ddd6cf', borderRadius: 9, cursor: 'pointer' },
  approveBtn: { flex: 1, padding: '0.6rem 1.3rem', fontSize: '0.9rem', fontWeight: 600, color: '#fff', background: '#8a6d3b', border: 'none', borderRadius: 9 },
};

export default function McpAuthorizePage() {
  return (
    <Suspense fallback={<Shell><p style={S.muted}>Loading…</p></Shell>}>
      <ConsentScreen />
    </Suspense>
  );
}
