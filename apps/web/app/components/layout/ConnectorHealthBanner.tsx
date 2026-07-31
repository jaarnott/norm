'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';

interface BrokenConnection {
  connector_name: string;
  connector_label: string;
  venue_id: string | null;
  venue_name: string | null;
  last_auth_error?: string | null;
}

/**
 * A top-of-app alert when a connector's authorization has broken (a rejected
 * token refresh — the state `bool(access_token)` can't see). This is what makes
 * an outage like the expired LoadedHub token visible before someone tries to
 * fetch and gets a dead end. Dismissible for the session; re-checks on mount.
 */
export default function ConnectorHealthBanner({ onFix }: { onFix?: () => void }) {
  const [broken, setBroken] = useState<BrokenConnection[]>([]);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiFetch('/api/connectors/health')
      .then(res => (res.ok ? res.json() : { broken: [] }))
      .then(data => {
        if (!cancelled) setBroken(data.broken || []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (dismissed || broken.length === 0) return null;

  // One connector can be broken across several venues; name the connector once
  // and list the venues, so "LoadedHub (La Zeppa, The Glass Goose)" reads cleanly.
  const byConnector = new Map<string, string[]>();
  for (const b of broken) {
    const list = byConnector.get(b.connector_label) || [];
    if (b.venue_name) list.push(b.venue_name);
    byConnector.set(b.connector_label, list);
  }
  const summary = Array.from(byConnector.entries())
    .map(([label, venues]) => (venues.length ? `${label} (${venues.join(', ')})` : label))
    .join(' · ');

  return (
    <div
      role="alert"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 1000,
        backgroundColor: '#b91c1c',
        color: '#fff',
        fontFamily: 'system-ui, sans-serif',
        fontSize: '0.82rem',
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
        padding: '0.5rem 0.9rem',
        boxShadow: '0 1px 4px rgba(0,0,0,0.2)',
      }}
    >
      <span style={{ fontWeight: 600 }}>Connection needs attention</span>
      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {summary} stopped working — data from {byConnector.size > 1 ? 'these' : 'this'} won&apos;t load until reconnected.
      </span>
      {onFix && (
        <button
          onClick={onFix}
          style={{
            padding: '3px 12px',
            fontSize: '0.76rem',
            fontWeight: 600,
            border: 'none',
            borderRadius: 6,
            backgroundColor: '#fff',
            color: '#b91c1c',
            cursor: 'pointer',
            fontFamily: 'inherit',
            whiteSpace: 'nowrap',
          }}
        >
          Reconnect
        </button>
      )}
      <button
        onClick={() => setDismissed(true)}
        aria-label="Dismiss"
        style={{
          border: 'none',
          background: 'transparent',
          color: '#fff',
          cursor: 'pointer',
          fontSize: '1rem',
          lineHeight: 1,
          padding: '0 2px',
        }}
      >
        ×
      </button>
    </div>
  );
}
