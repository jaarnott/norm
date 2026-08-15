'use client';

/**
 * Your apps — everything you built plus everything shared with you.
 * Standalone route for v1; nav integration into the main shell comes with the
 * builder. Redirects to login when there is no session.
 */

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { apiFetch, getToken } from '../lib/api';

interface AppRow {
  slug: string; name: string; description?: string | null; icon?: string | null;
  visibility: string; mine: boolean; access: string;
}

export default function AppsPage() {
  const router = useRouter();
  const [apps, setApps] = useState<AppRow[] | null>(null);

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return; }
    apiFetch('/api/apps')
      .then((r) => (r.ok ? r.json() : { apps: [] }))
      .then((d) => setApps(d.apps ?? []))
      .catch(() => setApps([]));
  }, [router]);

  return (
    <div style={{ maxWidth: 760, margin: '0 auto', padding: '2rem 1rem' }}>
      <h1 style={{ fontSize: '1.3rem', marginBottom: 4 }}>Apps</h1>
      <p style={{ color: '#8a8a8a', fontSize: '0.8rem', marginTop: 0 }}>
        Apps you built, and apps shared with you. Private until you share them.
      </p>
      {apps === null ? (
        <div style={{ color: '#888' }}>Loading…</div>
      ) : apps.length === 0 ? (
        <div style={{ border: '1px dashed #d8d4cc', borderRadius: 10, padding: '2rem', color: '#8a8a8a', fontSize: '0.85rem' }}>
          No apps yet. Describe one to Norm in chat — &ldquo;build me a weekly venue
          performance dashboard&rdquo; — and it will appear here.
        </div>
      ) : (
        apps.map((a) => (
          <Link key={a.slug} href={`/apps/${a.slug}`} style={{ textDecoration: 'none', color: 'inherit' }}>
            <div style={{ border: '1px solid #e5e2dc', borderRadius: 10, padding: '0.9rem 1.1rem', marginBottom: 10, background: '#fff', cursor: 'pointer', display: 'flex', gap: 12, alignItems: 'baseline' }}>
              <strong style={{ fontSize: '0.95rem' }}>{a.icon} {a.name}</strong>
              <span style={{ fontSize: '0.72rem', color: '#8a8a8a' }}>{a.description}</span>
              <span style={{ marginLeft: 'auto', fontSize: '0.62rem', color: a.mine ? '#2e7d4f' : '#8a6d3b', whiteSpace: 'nowrap' }}>
                {a.mine ? 'yours' : `shared · ${a.access}`}{a.visibility !== 'private' && ` · ${a.visibility}`}
              </span>
            </div>
          </Link>
        ))
      )}
    </div>
  );
}
