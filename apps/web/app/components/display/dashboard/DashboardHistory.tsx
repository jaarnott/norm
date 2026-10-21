'use client';

import { useEffect, useState } from 'react';
import type { SavedReport } from '../../../types';
import { apiFetch } from '../../../lib/api';
import Dialog from '../../ui/Dialog';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';

interface Version {
  version: number;
  created_at: string | null;
  by: string | null;
  note: string | null;
  title: string | null;
  tiles: number;
  current: boolean;
}

/**
 * Every version of a dashboard, newest first. Bringing one back saves it as
 * the newest version, so nothing in between is lost and it can be undone too.
 */
export default function DashboardHistory({ reportId, onClose, onRestored }: {
  reportId: string;
  onClose: () => void;
  onRestored: (dashboard: SavedReport) => void;
}) {
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    apiFetch(`/api/reports/${reportId}/versions`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => setVersions(d.versions || []))
      .catch(() => setError('The history could not be loaded.'));
  }, [reportId]);

  const bringBack = async (version: number) => {
    setBusy(version);
    setError(null);
    const res = await apiFetch(`/api/reports/${reportId}/versions/${version}/restore`, { method: 'POST' });
    setBusy(null);
    if (res.ok) onRestored(await res.json());
    else setError(`Version ${version} could not be brought back.`);
  };

  return (
    <Dialog title="Dashboard history" width={520} onClose={onClose} closeButton>
      {error && <div role="alert" style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)', marginBottom: 12 }}>{error}</div>}
      {!versions && !error && <PageState kind="loading" title="Loading history…" />}
      {versions && versions.length === 0 && (
        <p style={{ margin: 0, color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>
          No changes yet. Each change from now on is kept here.
        </p>
      )}
      {versions && versions.length > 0 && (
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, maxHeight: 420, overflow: 'auto' }}>
          {versions.map(v => (
            <li key={v.version} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0', borderBottom: '1px solid var(--line)' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 'var(--fs-base)' }}>
                  Version {v.version}{v.current ? ' · current' : ''}
                </div>
                <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>
                  {[
                    v.created_at ? new Date(v.created_at).toLocaleString() : null,
                    v.by,
                    `${v.tiles} tile${v.tiles === 1 ? '' : 's'}`,
                  ].filter(Boolean).join(' · ')}
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
