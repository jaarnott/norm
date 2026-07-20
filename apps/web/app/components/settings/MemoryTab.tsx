'use client';

/**
 * What Norm has learned — review, edit and remove.
 *
 * This is the other half of the memory design, not a nice-to-have. Norm writes
 * user-scoped memories on its own, so there has to be somewhere to see every
 * one of them and undo it. And org-scoped memories are written as *candidates*
 * that never reach a prompt until someone confirms them here — without this
 * screen they would queue forever.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';

interface Memory {
  id: string;
  scope: 'user' | 'org';
  type: string;
  title: string;
  body: string;
  why: string | null;
  how_to_apply: string | null;
  status: string;
  trigger: string | null;
  created_by: string;
  thread_id: string | null;
  created_at: string | null;
  last_used_at: string | null;
}

const TYPE_HINT: Record<string, string> = {
  vocabulary: 'What the business calls something',
  preference: 'How you like answers shaped',
  context: 'A fact about the business',
  correction: 'Something Norm got wrong',
};

export default function MemoryTab() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    apiFetch('/api/memories')
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setMemories(Array.isArray(d) ? d : []))
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  useEffect(load, [load]);

  const act = async (id: string, path: string, init?: RequestInit) => {
    setBusy(id);
    setError(null);
    try {
      const res = await apiFetch(path, init);
      if (!res.ok) {
        // A rejected edit carries the rule that refused it — showing it is the
        // point, otherwise the boundary looks arbitrary.
        const detail = await res.json().catch(() => null);
        const reason = detail?.detail?.reason || detail?.detail || 'That change was refused.';
        const where = detail?.detail?.belongs_in;
        setError(where ? `${reason} It belongs in: ${where}.` : String(reason));
        return;
      }
      setEditing(null);
      load();
    } finally {
      setBusy(null);
    }
  };

  if (!loaded) return <div style={{ color: '#999', fontSize: '0.85rem' }}>Loading…</div>;

  const candidates = memories.filter((m) => m.status === 'candidate');
  const active = memories.filter((m) => m.status === 'active');

  const card = (m: Memory, isCandidate: boolean) => (
    <div
      key={m.id}
      style={{
        border: `1px solid ${isCandidate ? '#f0e0bd' : '#eee'}`,
        background: isCandidate ? '#fffdf6' : '#fff',
        borderRadius: 10,
        padding: '0.9rem 1rem',
        marginBottom: '0.75rem',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '0.5rem' }}>
        <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>{m.title}</span>
        <span style={{ fontSize: '0.65rem', color: '#999', whiteSpace: 'nowrap' }}>
          {m.scope === 'org' ? 'everyone' : 'just you'} · {m.type}
        </span>
      </div>

      {editing === m.id ? (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={3}
          style={{
            width: '100%', marginTop: '0.5rem', fontSize: '0.8rem', padding: '0.5rem',
            border: '1px solid #ddd', borderRadius: 6, fontFamily: 'inherit',
          }}
        />
      ) : (
        <p style={{ margin: '0.4rem 0 0.6rem', fontSize: '0.8rem', color: '#444' }}>{m.body}</p>
      )}

      <div style={{ fontSize: '0.65rem', color: '#999', marginBottom: '0.6rem' }}>
        {TYPE_HINT[m.type] || m.type}
        {m.created_at && ` · learned ${new Date(m.created_at).toLocaleDateString()}`}
        {m.trigger && ` · from ${m.trigger.replace('_', ' ')}`}
        {m.last_used_at && ` · last used ${new Date(m.last_used_at).toLocaleDateString()}`}
        {!m.last_used_at && m.status === 'active' && ' · never used'}
      </div>

      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
        {isCandidate && (
          <button
            onClick={() => act(m.id, `/api/memories/${m.id}/approve`, { method: 'POST' })}
            disabled={busy === m.id}
            style={btn('#1a7f4b')}
          >
            Approve
          </button>
        )}
        {editing === m.id ? (
          <>
            <button
              onClick={() => act(m.id, `/api/memories/${m.id}`, {
                method: 'PATCH', body: JSON.stringify({ body: draft }),
              })}
              disabled={busy === m.id}
              style={btn('#333')}
            >
              Save
            </button>
            <button onClick={() => { setEditing(null); setError(null); }} style={btn('#999')}>
              Cancel
            </button>
          </>
        ) : (
          <button onClick={() => { setEditing(m.id); setDraft(m.body); setError(null); }} style={btn('#666')}>
            Edit
          </button>
        )}
        <button
          onClick={() => act(m.id, `/api/memories/${m.id}`, { method: 'DELETE' })}
          disabled={busy === m.id}
          style={btn('#a33')}
        >
          {isCandidate ? 'Discard' : 'Forget'}
        </button>
      </div>
    </div>
  );

  return (
    <div style={{ maxWidth: 720 }}>
      <h3 style={{ margin: '0 0 0.35rem', fontSize: '0.95rem', fontWeight: 600 }}>What Norm has learned</h3>
      <p style={{ margin: '0 0 1.25rem', fontSize: '0.8rem', color: '#777' }}>
        Norm remembers how you like answers and what your business calls things. It never
        remembers anything that changes a figure or approves spending — those are rules it
        follows, not preferences it learns.
      </p>

      {error && (
        <div style={{
          border: '1px solid #f0c9c9', background: '#fff7f7', color: '#a33',
          borderRadius: 8, padding: '0.6rem 0.75rem', fontSize: '0.78rem', marginBottom: '1rem',
        }}>
          {error}
        </div>
      )}

      {candidates.length > 0 && (
        <>
          <h4 style={{ margin: '0 0 0.15rem', fontSize: '0.82rem', fontWeight: 600 }}>
            Waiting for you ({candidates.length})
          </h4>
          <p style={{ margin: '0 0 0.75rem', fontSize: '0.72rem', color: '#999' }}>
            These affect everyone in your organisation, so Norm won&apos;t use them until approved.
          </p>
          {candidates.map((m) => card(m, true))}
          <div style={{ height: '1rem' }} />
        </>
      )}

      <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.82rem', fontWeight: 600 }}>
        In use ({active.length})
      </h4>
      {active.length === 0 ? (
        <p style={{ fontSize: '0.8rem', color: '#999' }}>
          Nothing yet. Tell Norm something like &ldquo;remember that we call the back bar the
          annex&rdquo; and it will appear here.
        </p>
      ) : (
        active.map((m) => card(m, false))
      )}
    </div>
  );
}

function btn(color: string): React.CSSProperties {
  return {
    fontSize: '0.72rem',
    padding: '0.3rem 0.7rem',
    borderRadius: 6,
    border: `1px solid ${color}`,
    background: '#fff',
    color,
    cursor: 'pointer',
  };
}
