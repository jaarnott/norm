'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Lightbulb, MessageCircle, X, type LucideIcon } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import { AGENTS } from '../layout/Sidebar';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';

/** One suggestion from GET /api/suggestions (services/suggestions). */
export interface Suggestion {
  id: string;
  kind: 'insight' | 'pending' | 'regular' | 'discover';
  /** A short headline (admin and tests); Home shows the prompt itself. */
  text: string;
  /** What a click sends — and what Home shows, as if helping to write it. */
  prompt: string;
  member: string;
  venue_id?: string | null;
  venue_name?: string | null;
  reason?: string | null;
}

type EventName = 'shown' | 'clicked' | 'dismissed';

/** The team member's own icon; an insight is Norm noticing something. */
export function suggestionIcon(s: Pick<Suggestion, 'kind' | 'member'>): LucideIcon {
  if (s.kind === 'insight') return Lightbulb;
  return AGENTS.find(a => a.id === s.member)?.icon ?? MessageCircle;
}

function report(events: { suggestion_id: string; event: EventName; venue_id?: string | null }[]) {
  if (!events.length) return;
  apiFetch('/api/suggestions/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ events }),
  }).catch(() => { /* learning is best-effort */ });
}

/**
 * Suggested prompts under the new-chat box, chosen for this person today
 * (docs/plans/home-suggested-prompts.md). A click sends the prompt straight
 * away; the × says "not useful" and it stays away. Nothing shows until they
 * load, so the box never waits on them.
 */
export default function Suggestions({ member, venueId, limit, onPick }: {
  member?: string;
  venueId?: string | null;
  limit: number;
  onPick: (s: Suggestion) => void;
}) {
  const [items, setItems] = useState<Suggestion[]>([]);
  const reported = useRef(new Set<string>());

  useEffect(() => {
    let live = true;
    const qs = new URLSearchParams({ limit: String(limit) });
    if (member && member !== 'home') qs.set('member', member);
    if (venueId) qs.set('venue_id', venueId);
    apiFetch(`/api/suggestions?${qs}`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (live && d) setItems(Array.isArray(d.suggestions) ? d.suggestions : []); })
      .catch(() => { /* no suggestions is fine */ });
    return () => { live = false; };
  }, [member, venueId, limit]);

  // Each suggestion is reported as shown once per visit — how Home learns
  // what someone never picks, and keeps an insight from repeating all week.
  useEffect(() => {
    const fresh = items.filter(s => !reported.current.has(s.id));
    fresh.forEach(s => reported.current.add(s.id));
    report(fresh.map(s => ({ suggestion_id: s.id, event: 'shown', venue_id: s.venue_id ?? null })));
  }, [items]);

  const dismiss = useCallback((s: Suggestion) => {
    setItems(prev => prev.filter(x => x.id !== s.id));
    report([{ suggestion_id: s.id, event: 'dismissed', venue_id: s.venue_id ?? null }]);
  }, []);

  if (!items.length) return null;
  return (
    // Lines up with the message field: icons under its paperclip, text where
    // typed text starts, and no further right than the field (not under Send).
    <ul aria-label="Suggestions" data-testid="home-suggestions" className="n-suggestions">
      {items.map((s, i) => (
        <li key={s.id} className="n-suggestion">
          <button
            type="button"
            data-testid={`suggestion-${i}`}
            className="n-suggestion-btn"
            title={s.prompt}
            onClick={() => onPick(s)}
          >
            <Icon icon={suggestionIcon(s)} size={16} tone={s.kind === 'insight' ? 'accent' : 'muted'}
              style={{ flex: '0 0 auto' }} />
            {/* The prompt itself, one line: the person sees what will be sent. */}
            <span className="n-suggestion-text">{s.prompt}</span>
          </button>
          <IconButton icon={X} label="Not useful — hide this" iconSize={14}
            className="n-icon-btn--xs n-suggestion-dismiss" onClick={() => dismiss(s)} />
        </li>
      ))}
    </ul>
  );
}
