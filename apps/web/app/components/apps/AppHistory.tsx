'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';
import Dialog from '../ui/Dialog';
import Button from '../ui/Button';
import PageState from '../ui/PageState';

interface Version {
  version: number;
  current: boolean;
  note: string | null;
  by: string | null;
  at: string | null;
}

/**
 * Every saved version of a dashboard or app, newest first. Bringing one back
 * saves it as the newest version, so nothing in between is lost and it can be
 * undone too. (Rich lost a chart to a change made on the wrong copy, and
 * nothing could bring it back — 21 Oct 2026.)
 */
export default function AppHistory({ slug, onClose, onRestored }: {
  slug: string;
  onClose: () => void;
  onRestored: () => void;
}) {
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const base = `/api/apps/${encodeURIComponent(slug)}`;

  useEffect(() => {
    apiFetch(`${base}/versions`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => setVersions(d.versions || []))
      .catch(() => setError('The history could not be loaded.'));
  }, [base]);

  const bringBack = async (version: number) => {
    setBusy(version);
    setError(null);
    const res = await apiFetch(`${base}/versions/${version}/restore`, { method: 'POST' });
    setBusy(null);
    if (res.ok) onRestored();
    else {
      const json = await res.json().catch(() => ({}));
      setError(json?.detail || `Version ${version} could not be brought back.`);
    }
  };

  return (
    <Dialog title="History" width={520} onClose={onClose} closeButton>
      {error && <div role="alert" style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)', marginBottom: 12 }}>{error}</div>}
      {!versions && !error && <PageState kind="loading" title="Loading history…" />}
      {versions && versions.length > 0 && (
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, maxHeight: 420, overflow: 'auto' }}>
          {versions.map((v) => (
            <li key={v.version} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0', borderBottom: '1px solid var(--line)' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 'var(--fs-base)' }}>
                  Version {v.version}{v.current ? ' · current' : ''}
                </div>
                <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>
                  {[v.at ? new Date(v.at).toLocaleString() : null, v.by].filter(Boolean).join(' · ')}
                </div>
                {v.note && <div style={{ color: 'var(--text-soft)', fontSize: 'var(--fs-sm)' }}>{v.note}</div>}
              </div>
              {!v.current && (
                <Button size="sm" disabled={busy !== null} onClick={() => bringBack(v.version)}>
                  {busy === v.version ? 'Bringing back…' : 'Bring back'}
                </Button>
              )}
            </li>
          ))}
        </ol>
      )}
    </Dialog>
  );
}
