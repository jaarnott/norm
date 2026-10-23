'use client';

import React, { useState } from 'react';
import { apiFetch } from '../../../lib/api';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import { formatDuration, isActive, statusLabel, type BackgroundJob } from '../backgroundJob';
import { movedText } from './catalogueLogic';

// A Catalogue merge running in the background (services/catalogue_job.py):
// it carries on when the page is closed, so this banner is how the page shows
// it — what has moved so far and what's left while it runs, the outcome when
// it ends. The page polls the job; this only draws it.

/** A merge job as /api/jobs returns it, with the merge's own progress. */
export interface CatalogueJob extends BackgroundJob {
  progress: BackgroundJob['progress'] & {
    moved?: Record<string, number>;
    remaining?: Record<string, number>;
    passes?: number;
  };
  result?: { result?: string; moved?: Record<string, number>; error?: string; failed?: unknown[] } | null;
  finished_at?: string | null;
}

const LEFT: [string, string, string][] = [
  ['invoices', 'invoice', 'invoices'],
  ['variants', 'variant', 'variants'],
  ['items', 'item', 'items'],
  ['orders', 'order', 'orders'],
  ['recipes', 'recipe', 'recipes'],
  ['pos_links', 'POS link', 'POS links'],
];

function leftText(remaining: Record<string, number> | undefined): string {
  return LEFT.filter(([k]) => remaining?.[k])
    .map(([k, one, many]) => `${remaining![k]} ${remaining![k] === 1 ? one : many}`)
    .join(', ');
}

/** What the banner says about a job, in one sentence. */
export function jobSentence(job: CatalogueJob): string {
  const p = job.progress ?? {};
  const r = job.result ?? {};
  const moved = movedText(r.moved ?? p.moved);
  if (isActive(job.status)) {
    if (job.status === 'queued') return 'Starting…';
    const left = leftText(p.remaining);
    return [moved ? `${moved} moved so far` : 'Working out what to move', left ? `${left} still to go` : '']
      .filter(Boolean).join(' · ');
  }
  if (job.status === 'done' && r.result === 'merged') return moved ? `Done — moved ${moved}.` : 'Done.';
  if (job.status === 'cancelled' || r.result === 'stopped') {
    return `Stopped${moved ? ` after moving ${moved}` : ''}. The old record is still active — merge again to finish.`;
  }
  const why = job.error || r.error || "It didn't finish.";
  return `${why}${moved ? ` It had moved ${moved}.` : ''}`;
}

export default function CatalogueJobBanner({ job, onChange, onDismiss }: {
  job: CatalogueJob;
  onChange: (job: CatalogueJob) => void;
  onDismiss: () => void;
}) {
  const [stopping, setStopping] = useState(false);
  const active = isActive(job.status);
  const ok = job.status === 'done' && job.result?.result === 'merged';
  const tone = active ? 'info' : ok ? 'ok' : job.status === 'cancelled' ? 'neutral' : 'error';
  const label = !active && job.status === 'done' && !ok ? 'Didn’t finish' : statusLabel(job);

  const stop = async () => {
    setStopping(true);
    try {
      const r = await apiFetch(`/api/jobs/${job.id}/cancel`, { method: 'POST' });
      if (r.ok) onChange((await r.json()) as CatalogueJob);
    } finally {
      setStopping(false);
    }
  };

  return (
    <div role="status" data-testid="catalogue-job" style={{
      marginBottom: 12, padding: '10px 14px', borderRadius: 'var(--radius)',
      border: '1px solid var(--line)', background: active ? 'var(--surface-alt)' : 'var(--bg)',
      display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 'var(--fs-sm)',
    }}>
      <div style={{ flex: '1 1 300px', minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, color: 'var(--text)' }}>{job.title}</span>
          <Badge tone={tone}>{label}</Badge>
          {job.elapsed_seconds > 0 && <span style={{ color: 'var(--muted)' }}>{formatDuration(job.elapsed_seconds)}</span>}
        </div>
        <div style={{ marginTop: 2, color: 'var(--text-soft)' }}>{jobSentence(job)}</div>
        {active && (
          <div style={{ marginTop: 2, color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>
            {job.stalled
              ? 'The server restarted — it carries on from where it got to in a moment.'
              : 'Runs in the background: you can leave this page, and you’ll get an email when it’s done.'}
          </div>
        )}
      </div>
      {active && !job.cancel_requested && (
        <Button size="sm" onClick={stop} disabled={stopping}
          title="Stops after the batch it is on. Everything moved so far stays moved; the old record stays active.">
          {stopping ? 'Stopping…' : 'Stop'}
        </Button>
      )}
      {!active && <Button size="sm" variant="quiet" onClick={onDismiss}>Dismiss</Button>}
    </div>
  );
}
