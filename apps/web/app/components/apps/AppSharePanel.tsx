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
import { apiFetch } from '../../lib/api';

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
    <div style={{ border: '1px solid #e5e2dc', borderRadius: 10, padding: '0.9rem 1.1rem', marginTop: 14, background: '#fbfaf8' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
        <strong style={{ fontSize: '0.8rem' }}>Sharing</strong>
        <span style={{ fontSize: '0.64rem', color: '#8a8a8a' }}>
          {visibility === 'private' ? 'private — only you' : `visible: ${visibility}`}
        </span>
      </div>

      {shares.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          {shares.map((s) => (
            <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.74rem', padding: '3px 0' }}>
              <span>{s.label}</span>
              {writes.length > 0 && (
                <span style={{ fontSize: '0.6rem', color: s.write_actions_approved ? '#2e7d4f' : '#8a8a8a' }}>
                  {s.write_actions_approved ? '· writes approved' : '· read-only for them'}
                </span>
              )}
              <button type="button" onClick={() => revoke(s.id)} disabled={busy}
                style={{ marginLeft: 'auto', fontSize: '0.62rem', border: '1px solid #d8d4cc', borderRadius: 4, background: '#fff', color: '#8a2f2f', cursor: 'pointer', padding: '1px 8px', fontFamily: 'inherit' }}>
                revoke
              </button>
            </div>
          ))}
        </div>
      )}

      {candidates && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select value={pick} onChange={(e) => setPick(e.target.value)}
            style={{ font: 'inherit', fontSize: '0.74rem', padding: '3px 8px', maxWidth: 260 }}>
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
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: '0.68rem', color: '#6b5626' }}
              title={`This app can perform: ${writes.join(', ')}. Unapproved, those actions are refused when they run it.`}>
              <input type="checkbox" checked={approveWrites} onChange={(e) => setApproveWrites(e.target.checked)} />
              approve its writes ({writes.join(', ')})
            </label>
          )}
          <button type="button" onClick={grant} disabled={!pick || busy}
            style={{ fontSize: '0.72rem', border: 'none', borderRadius: 5, background: '#2e7d4f', color: '#fff', cursor: !pick || busy ? 'not-allowed' : 'pointer', padding: '4px 12px', fontFamily: 'inherit', opacity: !pick || busy ? 0.5 : 1 }}>
            Share
          </button>
        </div>
      )}
      {error && <div style={{ color: '#c0392b', fontSize: '0.7rem', marginTop: 6 }}>✗ {error}</div>}
    </div>
  );
}
