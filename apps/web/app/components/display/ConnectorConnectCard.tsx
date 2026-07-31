'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

type VenueStatus = 'connected' | 'needs_reconnect' | 'not_connected';

interface VenueRow {
  venue_id: string;
  venue_name: string;
  status: VenueStatus;
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
    border: '1px solid #e5e7eb', borderRadius: 10, padding: '0.85rem 1rem',
    marginTop: '0.5rem', fontFamily: 'inherit', maxWidth: 460,
  };
  const label = info?.display_name || connectorName;

  if (error && !info) {
    return <div style={box}><span style={{ color: '#b91c1c', fontSize: '0.85rem' }}>{error}</span></div>;
  }
  if (!info) {
    return <div style={box}><span style={{ color: '#6b7280', fontSize: '0.85rem' }}>Loading {label}…</span></div>;
  }

  const isOAuth = info.auth_type === 'oauth2';

  return (
    <div style={box}>
      <div style={{ fontWeight: 600, fontSize: '0.92rem', marginBottom: '0.6rem' }}>
        Connect {label}
      </div>

      {isOAuth ? (
        info.venues.length === 0 ? (
          <div style={{ fontSize: '0.83rem', color: '#6b7280' }}>No venues you can connect.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
            {info.venues.map(v => {
              const broken = v.status === 'needs_reconnect';
              const connected = v.status === 'connected';
              return (
                <div key={v.venue_id} style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                  <span style={{ flex: 1, fontSize: '0.85rem', color: '#374151' }}>{v.venue_name}</span>
                  {connected ? (
                    <span style={{ fontSize: '0.72rem', fontWeight: 600, padding: '1px 8px', borderRadius: 8, backgroundColor: '#d1fae5', color: '#065f46' }}>Connected</span>
                  ) : (
                    <button
                      onClick={() => startOAuth(v.venue_id)}
                      disabled={busy === v.venue_id}
                      title={v.last_auth_error || undefined}
                      style={{
                        padding: '4px 12px', fontSize: '0.76rem', fontWeight: 600, border: 'none',
                        borderRadius: 6, cursor: busy === v.venue_id ? 'default' : 'pointer',
                        backgroundColor: broken ? '#b91c1c' : '#111', color: '#fff', fontFamily: 'inherit',
                        opacity: busy === v.venue_id ? 0.6 : 1,
                      }}
                    >
                      {busy === v.venue_id ? 'Opening…' : broken ? 'Reconnect' : 'Connect'}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )
      ) : savedKey ? (
        <div style={{ fontSize: '0.83rem', color: '#065f46' }}>Saved. {label} is connected.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {info.credential_fields.map(f => (
            <div key={f.key}>
              <label style={{ display: 'block', fontSize: '0.76rem', color: '#555', marginBottom: 3 }}>{f.label}</label>
              <input
                type={f.secret ? 'password' : 'text'}
                autoComplete="off"
                value={form[f.key] || ''}
                onChange={e => setForm({ ...form, [f.key]: e.target.value })}
                placeholder={`Enter ${f.label.toLowerCase()}`}
                style={{ width: '100%', padding: '7px 9px', border: '1px solid #ddd', borderRadius: 6, fontSize: '0.84rem', fontFamily: 'inherit', boxSizing: 'border-box' }}
              />
            </div>
          ))}
          <button
            onClick={saveApiKey}
            disabled={busy === '__apikey__'}
            style={{ alignSelf: 'flex-start', padding: '5px 14px', fontSize: '0.78rem', fontWeight: 600, border: 'none', borderRadius: 6, backgroundColor: '#111', color: '#fff', cursor: 'pointer', fontFamily: 'inherit' }}
          >
            {busy === '__apikey__' ? 'Saving…' : 'Save & connect'}
          </button>
        </div>
      )}

      {error && info && <div style={{ marginTop: '0.5rem', color: '#b91c1c', fontSize: '0.8rem' }}>{error}</div>}
    </div>
  );
}
