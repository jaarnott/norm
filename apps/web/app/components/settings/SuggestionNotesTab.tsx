'use client';

/**
 * What people have told Norm about its suggestions — and a way to undo it.
 *
 * When someone clicks a suggestion on Home and explains it ("First Table is
 * our promo until the end of November") or says they'll act on it ("we'll cut
 * the Tuesday roster"), Norm notes it, and that suggestion goes quiet — for
 * everyone in the organisation (services/insights). This is where those notes
 * are seen and cleared, so a suggestion never disappears without a trace.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import PageState from '../ui/PageState';

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

const TONE: Record<Note['verdict'], BadgeTone> = {
  expected: 'neutral',
  acting: 'ok',
  not_useful: 'neutral',
};

const EFFECT: Record<Note['verdict'], string> = {
  expected: 'Norm stays quiet about this unless it changes a lot.',
  acting: 'Norm will check in a few weeks whether it worked.',
  not_useful: 'Norm shows suggestions like this less often.',
};

function day(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export default function SuggestionNotesTab() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    apiFetch('/api/insight-notes')
      .then(r => (r.ok ? r.json() : { notes: [] }))
      .then(d => setNotes(Array.isArray(d.notes) ? d.notes : []))
      .catch(() => setError('Couldn’t load the notes.'))
      .finally(() => setLoaded(true));
  }, []);

  useEffect(load, [load]);

  const clear = async (n: Note) => {
    setBusy(n.id);
    setError(null);
    try {
      const res = await apiFetch(`/api/insight-notes/${n.id}`, { method: 'DELETE' });
      if (!res.ok) {
        const d = await res.json().catch(() => null);
        setError(typeof d?.detail === 'string' ? d.detail : 'That note wasn’t cleared.');
        return;
      }
      setNotes(prev => prev.filter(x => x.id !== n.id));
    } finally {
      setBusy(null);
    }
  };

  const header = (
    <>
      <h2 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>
        What you&apos;ve told Norm about its suggestions
      </h2>
      <p style={{ margin: '4px 0 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        When you explain one of Norm&apos;s suggestions — a promotion, a minimum crew, a supplier deal —
        or say you&apos;ll act on it, Norm notes it here for everyone in your organisation. Clear a note
        and the suggestion can come back.
      </p>
    </>
  );

  if (!loaded) return <div style={{ maxWidth: 720, lineHeight: 1.45 }}>{header}<PageState kind="loading" title="Loading…" /></div>;

  return (
    <div data-testid="suggestion-notes" style={{ maxWidth: 720, lineHeight: 1.45 }}>
      {header}
      {error && <div style={{ marginBottom: 16 }}><PageState kind="error" title={error} /></div>}
      {notes.length === 0 ? (
        <div className="n-card">
          <PageState
            kind="empty"
            title="Nothing yet"
            detail={<>Click a suggestion on Home and tell Norm what&apos;s behind it — &ldquo;that&apos;s our First Table promo until the end of November&rdquo; — and it will appear here.</>}
          />
        </div>
      ) : notes.map(n => (
        <div key={n.id} className="n-card" style={{ padding: '14px 16px', marginBottom: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
            <span style={{ minWidth: 0, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{n.about}</span>
            <Badge tone={TONE[n.verdict] ?? 'neutral'}>{n.verdict_label}</Badge>
          </div>
          {n.reason && (
            <p style={{ margin: '4px 0 0', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>&ldquo;{n.reason}&rdquo;</p>
          )}
          <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            {EFFECT[n.verdict]}
            {n.until && ` Until ${day(n.until)}.`}
            {(n.by || n.created_at) && ` · ${[n.by, day(n.created_at)].filter(Boolean).join(', ')}`}
            {n.venue && ` · ${n.venue}`}
          </div>
          {n.can_clear && (
            <div style={{ marginTop: 12 }}>
              <Button size="sm" onClick={() => clear(n)} disabled={busy === n.id}>
                {busy === n.id ? 'Clearing…' : 'Clear'}
              </Button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
