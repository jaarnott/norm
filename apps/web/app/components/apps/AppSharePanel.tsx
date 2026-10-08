'use client';

/**
 * Who can run this app — the owner's sharing panel.
 *
 * Two decisions live here and they are deliberately separate clicks:
 * sharing (who may RUN the app) and approving writes (whether it may complete
 * its declared write actions when THEY run it). Writes default to off, and the
 * approval names the exact actions — the server refuses an approval from
 * anyone who couldn't perform those actions themselves.
 */

import { useCallback, useEffect, useState } from 'react';
import { TriangleAlert } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';

interface ShareRow {
  id: string;
  principal_type: string;
  principal_id: string;
  label: string;
  access: string;
  write_actions_approved: boolean;
}

interface Candidates {
  users: { id: string; label: string }[];
  venues: { id: string; label: string }[];
  organization: { id: string; label: string };
}

export default function AppSharePanel({ slug, onChanged }: { slug: string; onChanged?: () => void }) {
  const [shares, setShares] = useState<ShareRow[]>([]);
  const [writes, setWrites] = useState<string[]>([]);
  const [visibility, setVisibility] = useState('private');
  const [candidates, setCandidates] = useState<Candidates | null>(null);
  const [pick, setPick] = useState('');
  const [approveWrites, setApproveWrites] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  const load = useCallback(async () => {
    const r = await apiFetch(`/api/apps/${slug}/shares`);
    if (r.status === 403) { setForbidden(true); return; }
    if (!r.ok) return;
    const d = await r.json();
    setShares(d.shares ?? []);
    setWrites(d.writes ?? []);
    setVisibility(d.visibility ?? 'private');
    const c = await apiFetch(`/api/apps/${slug}/share-candidates`);
    if (c.ok) setCandidates(await c.json());
    else if (c.status === 403) setCandidates(null); // can view, cannot grant
  }, [slug]);

  useEffect(() => { void load(); }, [load]);

  const grant = async () => {
    if (!pick || busy) return;
    const [type, id] = pick.split('::');
    setBusy(true); setError(null);
    try {
      const r = await apiFetch(`/api/apps/${slug}/share`, {
        method: 'POST',
        body: JSON.stringify({
          principal_type: type,
          principal_id: id,
          access: 'view',
          approve_writes: approveWrites,
        }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({ detail: `Error ${r.status}` }));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Error ${r.status}`);
      }
      setPick(''); setApproveWrites(false);
      await load();
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not share');
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (shareId: string) => {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const r = await apiFetch(`/api/apps/${slug}/share/${shareId}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`Error ${r.status}`);
      await load();
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not revoke');
    } finally {
      setBusy(false);
    }
  };

  if (forbidden) return null; // not the author — sharing is not theirs to see

  return (
    <div className="n-card" style={{ padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <h2 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>Sharing</h2>
        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          {visibility === 'private' ? 'Private — only you' : `Visible: ${visibility}`}
        </span>
      </div>

      {shares.length > 0 && (
        <ul style={{ listStyle: 'none', margin: '0 0 12px', padding: 0 }}>
          {shares.map((s) => (
            <li key={s.id} style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, padding: '6px 0', borderTop: '1px solid var(--line-soft)' }}>
              <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text)', minWidth: 0, overflowWrap: 'anywhere' }}>{s.label}</span>
              <Badge>{`Can ${s.access}`}</Badge>
              {writes.length > 0 && (
                s.write_actions_approved
                  ? <Badge tone="ok">Writes approved</Badge>
                  : <Badge>Read-only for them</Badge>
              )}
              <Button size="sm" variant="quiet" onClick={() => revoke(s.id)} disabled={busy} style={{ marginLeft: 'auto' }}>
                Revoke
              </Button>
            </li>
          ))}
        </ul>
      )}

      {candidates && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="n-select" aria-label="Share with" value={pick} onChange={(e) => setPick(e.target.value)}
            style={{ maxWidth: '100%', minWidth: 0, width: 260 }}>
            <option value="">Share with…</option>
            {candidates.users.length > 0 && (
              <optgroup label="People">
                {candidates.users.map((u) => (
                  <option key={u.id} value={`user::${u.id}`}>{u.label}</option>
                ))}
              </optgroup>
            )}
            {candidates.venues.length > 0 && (
              <optgroup label="Venues (everyone there)">
                {candidates.venues.map((v) => (
                  <option key={v.id} value={`venue::${v.id}`}>{v.label}</option>
                ))}
              </optgroup>
            )}
            <optgroup label="Company">
              <option value={`organization::${candidates.organization.id}`}>
                Everyone at {candidates.organization.label}
              </option>
            </optgroup>
          </select>
          {writes.length > 0 && (
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer' }}
              title={`This app can perform: ${writes.join(', ')}. Unapproved, those actions are refused when they run it.`}>
              <input type="checkbox" checked={approveWrites} onChange={(e) => setApproveWrites(e.target.checked)}
                style={{ width: 16, height: 16, margin: 0, accentColor: 'var(--accent)', cursor: 'pointer' }} />
              Approve its writes ({writes.join(', ')})
            </label>
          )}
          <Button size="sm" variant="primary" onClick={grant} disabled={!pick || busy}>
            Share
          </Button>
        </div>
      )}
      {error && (
        <div role="alert" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>
          <Icon icon={TriangleAlert} size={14} />
          {error}
        </div>
      )}
    </div>
  );
}
