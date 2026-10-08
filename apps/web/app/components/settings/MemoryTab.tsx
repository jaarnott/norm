'use client';

/**
 * What Norm has learned — review, edit and remove.
 *
 * This is the other half of the memory design, not a nice-to-have. Norm writes
 * memories on its own — a fact you state (personal or company-wide) applies
 * straight away — so there has to be somewhere to see every one of them and
 * undo it. The *candidate* queue is narrower now: only memories Norm *inferred*
 * from your behaviour (a repeated edit, a rejection) wait here for a thumbs-up.
 */

import { useCallback, useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { Check } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import PageState from '../ui/PageState';

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

/** A group heading inside the section (Waiting for you, In use). */
const groupTitle: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8,
  margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, lineHeight: 1.35, color: 'var(--text)',
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

  // Section header: 18px/600 title and its muted line, as on every settings tab.
  const header = (
    <>
      <h2 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>What Norm has learned</h2>
      <p style={{ margin: '4px 0 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        Norm remembers how you like answers and what your business calls things. It never
        remembers anything that changes a figure or approves spending — those are rules it
        follows, not preferences it learns.
      </p>
    </>
  );

  if (!loaded) return <div style={{ maxWidth: 720, lineHeight: 1.45 }}>{header}<PageState kind="loading" title="Loading…" /></div>;

  const candidates = memories.filter((m) => m.status === 'candidate');
  const active = memories.filter((m) => m.status === 'active');

  const card = (m: Memory, isCandidate: boolean) => {
    const isEditing = editing === m.id;
    return (
      <div key={m.id} className="n-card" style={{ padding: '14px 16px', marginBottom: 10 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
          <span style={{ minWidth: 0, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{m.title}</span>
          <span style={{ flex: '0 0 auto', fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>
            {m.scope === 'org' ? 'Everyone' : 'Just you'} · {m.type}
          </span>
        </div>

        {isEditing ? (
          <textarea
            className="n-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            aria-label={`Edit: ${m.title}`}
            style={{ display: 'block', width: '100%', marginTop: 8 }}
          />
        ) : (
          <p style={{ margin: '4px 0 0', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>{m.body}</p>
        )}

        <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          {TYPE_HINT[m.type] || m.type}
          {m.created_at && ` · learned ${new Date(m.created_at).toLocaleDateString()}`}
          {m.trigger && ` · from ${m.trigger.replace('_', ' ')}`}
          {m.last_used_at && ` · last used ${new Date(m.last_used_at).toLocaleDateString()}`}
          {!m.last_used_at && m.status === 'active' && ' · never used'}
        </div>

        {/* One primary per card: Approve, or Save while editing. */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 12 }}>
          {isCandidate && (
            <Button
              size="sm"
              variant={isEditing ? 'secondary' : 'primary'}
              icon={Check}
              onClick={() => act(m.id, `/api/memories/${m.id}/approve`, { method: 'POST' })}
              disabled={busy === m.id}
            >
              Approve
            </Button>
          )}
          {isEditing ? (
            <>
              <Button
                size="sm"
                variant="primary"
                onClick={() => act(m.id, `/api/memories/${m.id}`, {
                  method: 'PATCH', body: JSON.stringify({ body: draft }),
                })}
                disabled={busy === m.id}
              >
                Save
              </Button>
              <Button size="sm" variant="quiet" onClick={() => { setEditing(null); setError(null); }}>
                Cancel
              </Button>
            </>
          ) : (
            <Button size="sm" onClick={() => { setEditing(m.id); setDraft(m.body); setError(null); }}>
              Edit
            </Button>
          )}
          {/* Discarding a suggestion is a decline; forgetting one in use is a delete. */}
          <Button
            size="sm"
            variant={isCandidate ? 'secondary' : 'danger'}
            onClick={() => act(m.id, `/api/memories/${m.id}`, { method: 'DELETE' })}
            disabled={busy === m.id}
          >
            {isCandidate ? 'Discard' : 'Forget'}
          </Button>
        </div>
      </div>
    );
  };

  return (
    <div style={{ maxWidth: 720, lineHeight: 1.45 }}>
      {header}

      {error && <div style={{ marginBottom: 16 }}><PageState kind="error" title={error} /></div>}

      {candidates.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h3 style={groupTitle}>
            Waiting for you <Badge tone="accent">{candidates.length}</Badge>
          </h3>
          <p style={{ margin: '2px 0 10px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            Norm noticed these from how you work but isn&apos;t sure yet, so it won&apos;t use them until you approve.
          </p>
          {candidates.map((m) => card(m, true))}
        </div>
      )}

      <h3 style={{ ...groupTitle, marginBottom: 10 }}>
        In use <span style={{ fontWeight: 400, color: 'var(--muted)' }}>{active.length}</span>
      </h3>
      {active.length === 0 ? (
        <div className="n-card">
          <PageState
            kind="empty"
            title="Nothing yet"
            detail={<>Tell Norm something like &ldquo;remember that we call the back bar the annex&rdquo; and it will appear here.</>}
          />
        </div>
      ) : (
        active.map((m) => card(m, false))
      )}
    </div>
  );
}
