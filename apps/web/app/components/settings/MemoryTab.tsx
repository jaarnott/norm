'use client';

/**
 * What Norm has learned — review, edit and remove. One list, two kinds:
 *
 * - Knowledge: memories. Norm writes them on its own — a fact you state
 *   (personal or company-wide) applies straight away — so there has to be
 *   somewhere to see every one of them and undo it. The *candidate* queue is
 *   narrower: only memories Norm *inferred* from your behaviour (a repeated
 *   edit, a rejection) wait here for a thumbs-up.
 * - Suggestion: what someone told Norm about one of Home's suggestions ("First
 *   Table is our promo until the end of November", "we'll cut the Tuesday
 *   roster"). The note quietens that suggestion for everyone in the
 *   organisation (services/insights); forgetting it lets the suggestion come
 *   back, so a suggestion never disappears without a trace.
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

/** A note on one of Home's suggestions (GET /api/insight-notes). */
interface Note {
  id: string;
  about: string;
  venue: string | null;
  verdict: 'expected' | 'acting' | 'not_useful';
  verdict_label: string;
  reason: string | null;
  until: string | null;
  by: string | null;
  created_at: string | null;
  can_clear: boolean;
}

type Learned = { kind: 'knowledge'; m: Memory } | { kind: 'suggestion'; n: Note };

const TYPE_HINT: Record<string, string> = {
  vocabulary: 'What the business calls something',
  preference: 'How you like answers shaped',
  context: 'A fact about the business',
  correction: 'Something Norm got wrong',
};

const EFFECT: Record<Note['verdict'], string> = {
  expected: 'Norm stays quiet about this unless it changes a lot.',
  acting: 'Norm will check in a few weeks whether it worked.',
  not_useful: 'Norm shows suggestions like this less often.',
};

/** A group heading inside the section (Waiting for you, In use). */
const groupTitle: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8,
  margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, lineHeight: 1.35, color: 'var(--text)',
};
const titleRow: CSSProperties = { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 };
const titleText: CSSProperties = { minWidth: 0, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' };
const bodyText: CSSProperties = { margin: '4px 0 0', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' };
const metaText: CSSProperties = { marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' };

function day(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

/** Knowledge and suggestion notes, newest first. */
function newestFirst(memories: Memory[], notes: Note[]): Learned[] {
  const all: Learned[] = [
    ...memories.map(m => ({ kind: 'knowledge' as const, m })),
    ...notes.map(n => ({ kind: 'suggestion' as const, n })),
  ];
  const at = (l: Learned) => (l.kind === 'knowledge' ? l.m.created_at : l.n.created_at) ?? '';
  return all.sort((a, b) => at(b).localeCompare(at(a)));
}

export default function MemoryTab() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    // Suggestion notes need reports:read; without it the list is knowledge only.
    Promise.all([
      apiFetch('/api/memories').then((r) => (r.ok ? r.json() : [])).catch(() => []),
      apiFetch('/api/insight-notes').then((r) => (r.ok ? r.json() : { notes: [] })).catch(() => ({ notes: [] })),
    ])
      .then(([m, n]) => {
        setMemories(Array.isArray(m) ? m : []);
        setNotes(Array.isArray(n?.notes) ? n.notes : []);
      })
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
        Norm remembers how you like answers, what your business calls things, and what you&apos;ve told it
        about its suggestions — a promotion, a minimum crew, a fix you&apos;re making. It never remembers
        anything that changes a figure or approves spending — those are rules it follows, not
        preferences it learns.
      </p>
    </>
  );

  if (!loaded) return <div style={{ maxWidth: 720, lineHeight: 1.45 }}>{header}<PageState kind="loading" title="Loading…" /></div>;

  const candidates = memories.filter((m) => m.status === 'candidate');
  const inUse = newestFirst(memories.filter((m) => m.status === 'active'), notes);

  const memoryCard = (m: Memory, isCandidate: boolean) => {
    const isEditing = editing === m.id;
    return (
      <div key={m.id} className="n-card" style={{ padding: '14px 16px', marginBottom: 10 }}>
        <div style={titleRow}>
          <span style={titleText}>{m.title}</span>
          <Badge>Knowledge</Badge>
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
          <p style={bodyText}>{m.body}</p>
        )}

        <div style={metaText}>
          {m.scope === 'org' ? 'Everyone' : 'Just you'} · {TYPE_HINT[m.type] || m.type}
          {m.created_at && ` · learned ${day(m.created_at)}`}
          {m.trigger && ` · from ${m.trigger.replace('_', ' ')}`}
          {m.last_used_at && ` · last used ${day(m.last_used_at)}`}
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
          {/* Discarding a candidate is a decline; forgetting one in use is a delete. */}
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

  // Forgetting a note lets its suggestion come back; only the person who made
  // it, or an owner, may (can_clear).
  const noteCard = (n: Note) => (
    <div key={n.id} className="n-card" style={{ padding: '14px 16px', marginBottom: 10 }}>
      <div style={titleRow}>
        <span style={titleText}>{n.about}</span>
        <Badge tone="info">Suggestion</Badge>
      </div>
      {n.reason && <p style={bodyText}>&ldquo;{n.reason}&rdquo;</p>}
      <div style={metaText}>
        Everyone · {n.verdict_label}. {EFFECT[n.verdict]}
        {n.until && ` Until ${day(n.until)}.`}
        {(n.by || n.created_at) && ` · ${[n.by, day(n.created_at)].filter(Boolean).join(', ')}`}
        {n.venue && ` · ${n.venue}`}
      </div>
      {n.can_clear && (
        <div style={{ marginTop: 12 }}>
          <Button size="sm" variant="danger" onClick={() => act(n.id, `/api/insight-notes/${n.id}`, { method: 'DELETE' })} disabled={busy === n.id}>
            Forget
          </Button>
        </div>
      )}
    </div>
  );

  return (
    <div data-testid="what-norm-learned" style={{ maxWidth: 720, lineHeight: 1.45 }}>
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
          {candidates.map((m) => memoryCard(m, true))}
        </div>
      )}

      <h3 style={{ ...groupTitle, marginBottom: 10 }}>
        In use <span style={{ fontWeight: 400, color: 'var(--muted)' }}>{inUse.length}</span>
      </h3>
      {inUse.length === 0 ? (
        <div className="n-card">
          <PageState
            kind="empty"
            title="Nothing yet"
            detail={<>Tell Norm something like &ldquo;remember that we call the back bar the annex&rdquo;, or click a suggestion on Home and tell it what&apos;s behind it, and it will appear here.</>}
          />
        </div>
      ) : (
        inUse.map((l) => (l.kind === 'knowledge' ? memoryCard(l.m, false) : noteCard(l.n)))
      )}
    </div>
  );
}
