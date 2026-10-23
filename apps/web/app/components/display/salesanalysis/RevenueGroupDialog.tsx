'use client';

/**
 * Move a POS item to another revenue group, through the same tool Norm uses
 * in chat (loadedhub.set_revenue_group), the way the stock catalogue makes
 * its changes: /preview returns the tool's own approval card (before → after
 * for every POS item with that name, nothing written); Confirm runs it with
 * the card's fingerprint, so it stops if the items moved meanwhile. Pressing
 * Confirm is the approval and the server records it.
 *
 * The two routes don't exist yet: they belong in a page router like
 * routers/stock_catalogue.py (see the Sales Analysis hand-over). Until they
 * are there, GROUP_CHANGES_LIVE stays false and the page shows each item's
 * group read-only.
 */

import React, { useState } from 'react';
import { TriangleAlert } from 'lucide-react';
import { apiFetch } from '../../../lib/api';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import { REVENUE_GROUPS } from './salesLogic';

/** Flip to true once /api/sales-analysis/revenue-group/{preview,run} exist. */
export const GROUP_CHANGES_LIVE = true;

const PREVIEW = '/api/sales-analysis/revenue-group/preview';
const RUN = '/api/sales-analysis/revenue-group/run';

interface PreviewCard {
  title?: string;
  target?: string;
  changes?: { field: string; before?: unknown; after?: unknown }[];
  warnings?: string[];
  note?: string | null;
  fingerprint?: string | null;
}

interface RunResult {
  changed?: number;
  already_in_group?: number;
  note?: string;
  not_kept_count?: number;
  error?: string;
}

async function post(path: string, body: unknown) {
  const r = await apiFetch(path, { method: 'POST', body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Something went wrong (${r.status})`);
  return data;
}

/** What happened, in a sentence. */
export function doneSentence(item: string, group: string, d: RunResult | null | undefined): string {
  if (!d) return `${item} is now in ${group}.`;
  if (!d.changed) return d.note || `${item} was already in ${group}.`;
  const kept = d.not_kept_count ? ` Loaded didn't keep ${d.not_kept_count} of them — check the item in Loaded.` : '';
  return `${item} moved to ${group}${d.changed > 1 ? ` (${d.changed} POS items with that name)` : ''}.${kept}`;
}

const soft: React.CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' };
const errorBox: React.CSSProperties = {
  marginTop: 12, padding: '8px 12px', fontSize: 'var(--fs-sm)',
  color: 'var(--error)', backgroundColor: 'var(--error-bg)', borderRadius: 'var(--radius)',
};

function CardRows({ card }: { card: PreviewCard }) {
  return (
    <div>
      {card.target && <div style={{ fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>{card.target}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(90px, max-content) 1fr', columnGap: 12, rowGap: 4, fontSize: 'var(--fs-sm)' }}>
        {(card.changes ?? []).slice(0, 40).map((c, i) => (
          <div key={i} style={{ display: 'contents' }}>
            <span style={{ color: 'var(--muted)', overflowWrap: 'anywhere' }}>{c.field}</span>
            <span style={{ color: 'var(--text)' }}>
              {c.before !== undefined && c.before !== null && c.before !== '' && (
                <>
                  <span style={{ color: 'var(--muted)', textDecoration: 'line-through' }}>{String(c.before)}</span>
                  <span aria-hidden style={{ color: 'var(--icon)', margin: '0 5px' }}>→</span>
                </>
              )}
              <span style={{ fontWeight: 500 }}>{c.after == null ? '' : String(c.after)}</span>
            </span>
          </div>
        ))}
      </div>
      {card.note && <div style={{ ...soft, marginTop: 8 }}>{card.note}</div>}
      {(card.warnings ?? []).map((w, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--warn)', marginTop: 8 }}>
          <TriangleAlert size={14} aria-hidden style={{ flex: '0 0 auto', marginTop: 2 }} />
          <span>{w}</span>
        </div>
      ))}
    </div>
  );
}

const GROUP_CHOICES = [...REVENUE_GROUPS, 'Unassigned'] as const;

export default function RevenueGroupDialog({ item, current, venueId, onClose, onDone }: {
  item: string;
  /** The group the sales figures show it under now. */
  current: string;
  venueId: string;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [group, setGroup] = useState<string>(GROUP_CHOICES.find((g) => g !== current) ?? 'Food');
  const [card, setCard] = useState<PreviewCard | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const body = { venue_id: venueId, revenue_group: group, items: [item] };

  const review = async () => {
    setBusy(true);
    setError(null);
    try {
      const d = await post(PREVIEW, body);
      if (d.card) setCard(d.card);
      else onDone(doneSentence(item, group, d.done));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const d = await post(RUN, { ...body, fingerprint: card?.fingerprint ?? null });
      if (d.changed) {
        // The items moved since the preview: show them as they are now.
        setCard(d.changed);
        setError('These items changed since you looked. Check the change again, then confirm.');
        setBusy(false);
        return;
      }
      if (d.data?.error) throw new Error(String(d.data.error));
      onDone(doneSentence(item, group, d.data));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const dismiss = () => { if (!busy) onClose(); };
  return (
    <Dialog title={card?.title || 'Change revenue group'} width={520} onClose={dismiss}>
      {!card && (
        <>
          <div style={{ ...soft, marginBottom: 12 }}>
            <strong style={{ color: 'var(--text)' }}>{item}</strong> is reported under {current} now. Every POS item with this
            name moves together.
          </div>
          <label style={{ display: 'block', fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text-soft)', marginBottom: 4 }} htmlFor="sa-group">
            Revenue group
          </label>
          <select id="sa-group" className="n-select" value={group} onChange={(e) => setGroup(e.target.value)} style={{ width: '100%' }}>
            {GROUP_CHOICES.map((g) => <option key={g} value={g}>{g === 'Unassigned' ? 'No group (unassigned)' : g}</option>)}
          </select>
        </>
      )}
      {card && <CardRows card={card} />}
      {error && <div role="alert" style={errorBox}>{error}</div>}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 20 }}>
        <Button onClick={dismiss} disabled={busy}>Cancel</Button>
        {card ? (
          <Button variant="primary" onClick={confirm} disabled={busy} aria-busy={busy || undefined}>
            {busy ? 'Saving…' : 'Change group'}
          </Button>
        ) : (
          <Button variant="primary" onClick={review} disabled={busy || group === current} aria-busy={busy || undefined}>
            {busy ? 'Checking…' : 'Review change'}
          </Button>
        )}
      </div>
    </Dialog>
  );
}
