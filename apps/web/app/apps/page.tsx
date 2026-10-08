'use client';

/**
 * Your apps — everything you built plus everything shared with you.
 * Standalone route for v1; nav integration into the main shell comes with the
 * builder. Redirects to login when there is no session.
 */

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ChevronRight } from 'lucide-react';
import { apiFetch, getToken } from '../lib/api';
import AppIcon from '../components/ui/AppIcon';
import Icon from '../components/ui/Icon';
import PageHeader from '../components/ui/PageHeader';
import PageState from '../components/ui/PageState';

interface AppRow {
  slug: string; name: string; description?: string | null; icon?: string | null;
  visibility: string; mine: boolean; access: string;
  /** built into Norm (Norm Hiring, Norm Training) — everyone in the org has it */
  builtin?: boolean;
}

export default function AppsPage() {
  const router = useRouter();
  const [apps, setApps] = useState<AppRow[] | null>(null);
  // A failed load still leaves an empty list; this only lets the page say so.
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return; }
    apiFetch('/api/apps')
      .then((r) => { setFailed(!r.ok); return r.ok ? r.json() : { apps: [] }; })
      .then((d) => setApps(d.apps ?? []))
      .catch(() => { setFailed(true); setApps([]); });
  }, [router]);

  // The same rows as the Apps page in the shell: the app's line icon on a
  // tile, name 14/600, who it belongs to, a 13px muted description.
  return (
    <main className="n-page" style={{ minHeight: '100dvh', paddingBottom: 32, background: 'var(--canvas)' }}>
      <PageHeader
        title="Apps"
        back={{ label: 'Norm', onClick: () => router.push('/app') }}
        meta="Apps you built, and apps shared with you. Private until you share them."
      />
      {apps === null ? (
        <PageState kind="loading" title="Loading apps…" />
      ) : failed ? (
        <PageState kind="error" title="Couldn’t load your apps" detail="Refresh the page to try again." />
      ) : apps.length === 0 ? (
        <PageState
          kind="empty"
          title="No apps yet"
          detail={<>Describe one to Norm in chat — &ldquo;build me a weekly venue performance dashboard&rdquo; — and it will appear here.</>}
        />
      ) : (
        <div className="n-card" style={{ overflow: 'hidden' }}>
          {apps.map((a, i) => (
            <Link
              key={a.slug}
              href={`/apps/${a.slug}`}
              style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', borderTop: i ? '1px solid var(--line)' : undefined, textDecoration: 'none', color: 'inherit' }}
            >
              <span aria-hidden style={{ flex: '0 0 auto', width: 32, height: 32, borderRadius: 'var(--radius)', background: 'var(--surface-alt)', color: 'var(--text-soft)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
                <AppIcon app={a} size="menu" tone="inherit" />
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{a.name}</span>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                    {a.builtin ? 'Built into Norm' : a.mine ? 'Yours' : `Shared · ${a.access}`}
                    {!a.builtin && a.visibility !== 'private' && ` · ${a.visibility}`}
                  </span>
                </span>
                {a.description && (
                  <span title={a.description} style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {a.description}
                  </span>
                )}
              </span>
              <Icon icon={ChevronRight} size={16} tone="muted" />
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
