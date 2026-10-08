'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import Button from '../ui/Button';
import Badge from '../ui/Badge';

type VenueStatus = 'connected' | 'needs_reconnect' | 'not_connected';

interface VenueRow {
  venue_id: string;
  venue_name: string;
  status: VenueStatus;
  // Which provider-side company the stored token is actually for — shown so a
  // token minted for the wrong company can't hide behind a green "Connected".
  connected_as?: string | null;
  // Server-side id comparison (token company vs configured company) — names can
  // legitimately differ, so the flag, not the name, decides "wrong".
  wrong_company?: boolean;
  last_auth_error?: string | null;
}

interface ConnectInfo {
  connector_name: string;
  display_name: string;
  auth_type: string;
  credential_fields: { key: string; label: string; secret?: boolean }[];
  venues: VenueRow[];
}

/**
 * Connect / reconnect a connector from inside the conversation, per venue. The
 * agent emits this card by connector_name (on request, or automatically when a
 * fetch failed because the connector's authorization died); the card fetches
 * per-venue status and drives the flow:
 *   - OAuth connectors → the existing popup handshake (window.open the
 *     authorize URL, reconcile on the callback's postMessage).
 *   - API-key connectors → an inline credential form that POSTs same-origin to
 *     /api/connectors/{name}; the values never pass through the model.
 */
export default function ConnectorConnectCard({ data, onAction }: DisplayBlockProps) {
  const connectorName = (data?.connector_name as string) || '';
  const [info, setInfo] = useState<ConnectInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [savedKey, setSavedKey] = useState(false);

  const refresh = useCallback(() => {
    if (!connectorName) return;
    apiFetch(`/api/connectors/${connectorName}/connect-info`)
      .then(res => {
        if (!res.ok) throw new Error(`Couldn't load ${connectorName} (${res.status})`);
        return res.json();
      })
      .then(setInfo)
      .catch(e => setError(e.message));
  }, [connectorName]);

  useEffect(() => { refresh(); }, [refresh]);

  // The OAuth callback page posts this when the popup finishes.
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type === 'oauth-complete') {
        setBusy(null);
        refresh();
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [refresh]);

  const startOAuth = async (venueId: string) => {
    setBusy(venueId);
    setError(null);
    try {
      const res = await apiFetch(`/api/oauth/authorize/${connectorName}?venue_id=${encodeURIComponent(venueId)}`);
      if (!res.ok) {
        setError(`Couldn't start connection: ${(await res.text()).slice(0, 160)}`);
        setBusy(null);
        return;
      }
      const { authorize_url } = await res.json();
      const popup = window.open(authorize_url, 'oauth-popup', 'width=600,height=700,resizable=yes,scrollbars=yes');
      if (!popup) {
        setError('Popup was blocked — please allow popups and try again.');
        setBusy(null);
        return;
      }
      // Fallback to popup-closed in case the postMessage is missed.
      const poll = setInterval(() => {
        if (popup.closed) { clearInterval(poll); setBusy(null); refresh(); }
      }, 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  };

  const saveApiKey = async () => {
    setBusy('__apikey__');
    setError(null);
    try {
      const res = await apiFetch(`/api/connectors/${connectorName}`, {
        method: 'PUT',
        body: JSON.stringify({ config: form, enabled: true }),
      });
      if (!res.ok) {
        setError(`Couldn't save: ${(await res.text()).slice(0, 160)}`);
      } else {
        setSavedKey(true);
        setForm({});
        refresh();
        onAction?.({ connector_name: 'norm', action: 'send_message', params: { message: `I've connected ${info?.display_name || connectorName}.` } });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  if (!connectorName) return null;

  const box: React.CSSProperties = {
    border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: '0.9rem 1rem',
    marginTop: '0.5rem', maxWidth: 460, backgroundColor: 'var(--bg)',
  };
  const label = info?.display_name || connectorName;

  if (error && !info) {
    return <div style={box}><span role="alert" style={{ color: 'var(--error)', fontSize: 'var(--fs-base)' }}>{error}</span></div>;
  }
  if (!info) {
    return <div style={box}><span style={{ color: 'var(--muted)', fontSize: 'var(--fs-base)' }}>Loading {label}…</span></div>;
  }

  const isOAuth = info.auth_type === 'oauth2';

  return (
    <div style={box}>
      <div style={{ fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--text)', marginBottom: '0.7rem' }}>
        Connect {label}
      </div>

      {isOAuth ? (
        info.venues.length === 0 ? (
          <div style={{ fontSize: 'var(--fs-base)', color: 'var(--muted)' }}>No venues you can connect.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {info.venues.map(v => {
              const broken = v.status === 'needs_reconnect';
              const connected = v.status === 'connected';
              // A token bound to a different provider-side company than this
              // venue is a mis-connect — show it in red, never as a clean tick.
              const wrongCompany = !!(connected && v.wrong_company);
              return (
                <div key={v.venue_id} style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                  <span style={{ flex: 1, fontSize: 'var(--fs-base)', color: 'var(--text)' }}>
                    {v.venue_name}
                    {connected && v.connected_as && (
                      <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: wrongCompany ? 'var(--error)' : 'var(--muted)' }}>
                        as {v.connected_as}{wrongCompany ? ' — wrong company, reconnect' : ''}
                      </span>
                    )}
                  </span>
                  {connected && !wrongCompany ? (
                    <Badge tone="ok">Connected</Badge>
                  ) : (
                    <Button
                      size="sm"
                      variant={broken || wrongCompany ? 'danger' : 'primary'}
                      onClick={() => startOAuth(v.venue_id)}
                      disabled={busy === v.venue_id}
                      title={v.last_auth_error || undefined}
                    >
                      {busy === v.venue_id ? 'Opening…' : broken || wrongCompany ? 'Reconnect' : 'Connect'}
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        )
      ) : savedKey ? (
        <div style={{ fontSize: 'var(--fs-base)', color: 'var(--ok)' }}>Saved. {label} is connected.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
          {info.credential_fields.map(f => (
            <div key={f.key}>
              <label className="n-label" htmlFor={`cred-${connectorName}-${f.key}`}>{f.label}</label>
              <input
                id={`cred-${connectorName}-${f.key}`}
                className="n-input"
                type={f.secret ? 'password' : 'text'}
                autoComplete="off"
                value={form[f.key] || ''}
                onChange={e => setForm({ ...form, [f.key]: e.target.value })}
                placeholder={`Enter ${f.label.toLowerCase()}`}
                style={{ width: '100%' }}
              />
            </div>
          ))}
          <div>
            <Button variant="primary" onClick={saveApiKey} disabled={busy === '__apikey__'}>
              {busy === '__apikey__' ? 'Saving…' : 'Save & connect'}
            </Button>
          </div>
        </div>
      )}

      {error && info && <div role="alert" style={{ marginTop: '0.6rem', color: 'var(--error)', fontSize: 'var(--fs-sm)' }}>{error}</div>}
    </div>
  );
}
