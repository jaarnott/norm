'use client';

/**
 * The Apps page inside the main conversation panel — every app this user can
 * run, opened inline (the InvoicesDashboard pattern: list → open in place →
 * back), with a per-user "in nav" pin that adds the app to the page links
 * beside Invoices and friends.
 *
 * Pinning fires `norm:app-pages-changed` so the shell can refresh its dynamic
 * page list without a reload.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';
import AppRunner from './AppRunner';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';

export const APP_PAGES_CHANGED_EVENT = 'norm:app-pages-changed';

interface AppRow {
  slug: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  visibility: string;
  mine: boolean;
  access: string;
  pinned: boolean;
}

export default function AppsDashboard({ props }: DisplayBlockProps) {
  const [apps, setApps] = useState<AppRow[] | null>(null);
  const [openSlug, setOpenSlug] = useState<string | null>(
    (props?.openSlug as string) || null,
  );
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    apiFetch('/api/apps')
      .then((r) => (r.ok ? r.json() : { apps: [] }))
      .then((d) => setApps(d.apps ?? []))
      .catch(() => setApps([]));
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

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '1.2rem 1rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Apps</h2>
        <span style={{ fontSize: '0.72rem', color: '#8a8a8a' }}>
          built by you and your team — describe a new one to the App Builder in chat
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
        apps.map((a) => (
          <div key={a.slug}
            style={{ border: '1px solid #e5e2dc', borderRadius: 10, padding: '0.8rem 1rem', marginTop: 10, background: '#fff', display: 'flex', gap: 12, alignItems: 'center' }}>
            <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={() => setOpenSlug(a.slug)}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                <strong style={{ fontSize: '0.92rem' }}>{a.icon} {a.name}</strong>
                <span style={{ fontSize: '0.62rem', color: a.mine ? '#2e7d4f' : '#8a6d3b', whiteSpace: 'nowrap' }}>
                  {a.mine ? 'yours' : `shared · ${a.access}`}
                  {a.visibility !== 'private' && ` · ${a.visibility}`}
                </span>
              </div>
              <div style={{ fontSize: '0.72rem', color: '#8a8a8a', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {a.description}
              </div>
            </div>
            <label title="show this app as a page link in the nav (only for you)"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: '0.66rem', color: '#6b655c', whiteSpace: 'nowrap', cursor: 'pointer' }}>
              <input type="checkbox" checked={a.pinned} disabled={busy === a.slug}
                onChange={() => { void togglePin(a); }} />
              in nav
            </label>
            <button type="button" onClick={() => setOpenSlug(a.slug)}
              style={{ fontSize: '0.72rem', border: 'none', borderRadius: 5, background: '#2e7d4f', color: '#fff', cursor: 'pointer', padding: '4px 14px', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
              Open
            </button>
          </div>
        ))
      )}
    </div>
  );
}
