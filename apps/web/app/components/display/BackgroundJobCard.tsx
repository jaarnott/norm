'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../lib/api';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import {
  type BackgroundJob,
  estimateRemaining,
  formatDuration,
  isActive,
  percent,
  statusLabel,
} from './backgroundJob';

const POLL_MS = 3000;

/**
 * A background job's live card in the conversation (services/background_jobs
 * — long work that runs outside the chat request). Polls the job while it
 * runs: progress, what it found so far, the invoice it is on, how long it has
 * taken and roughly how long is left, with a Cancel. When the job ends, Norm
 * posts its summary into the conversation; the card reloads the thread so it
 * appears.
 */
export default function BackgroundJobCard({ data, onAction }: DisplayBlockProps) {
  const jobId = (data?.job_id as string) || '';
  const [job, setJob] = useState<BackgroundJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const wasActive = useRef<boolean | null>(null);

  const load = useCallback(async () => {
    if (!jobId) return;
    try {
      const res = await apiFetch(`/api/jobs/${jobId}`);
      if (!res.ok) throw new Error(res.status === 404 ? 'This job is no longer available' : `Error ${res.status}`);
      const next = (await res.json()) as BackgroundJob;
      setJob(next);
      setError(null);
      // It just ended while this card watched: Norm's summary is now in the
      // conversation — reload the thread so it shows.
      if (wasActive.current && !isActive(next.status)) {
        onAction?.({ connector_name: 'norm', action: 'reload_thread', params: {} });
      }
      wasActive.current = isActive(next.status);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the job');
    }
  }, [jobId, onAction]);

  useEffect(() => {
    load();
  }, [load]);

  const active = job ? isActive(job.status) : true;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [active, load]);

  const cancel = async () => {
    if (!job || cancelling) return;
    setCancelling(true);
    try {
      const res = await apiFetch(`/api/jobs/${job.id}/cancel`, { method: 'POST' });
      if (res.ok) setJob((await res.json()) as BackgroundJob);
    } finally {
      setCancelling(false);
    }
  };

  if (!jobId) return null;
  if (!job) {
    return (
      <div className="n-card" style={{ padding: '12px 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        {error || 'Loading the job…'}
      </div>
    );
  }

  const p = job.progress || {};
  const pct = percent(p);
  const left = active ? estimateRemaining(p, job.elapsed_seconds) : null;
  const tone: BadgeTone =
    job.status === 'done' ? 'ok' : job.status === 'failed' ? 'error' : job.status === 'cancelled' ? 'neutral' : 'info';
  const counts: [string, number | undefined, BadgeTone][] = [
    ['received', p.received, 'ok'],
    ['need you', p.needs_you, 'accent'],
    ["couldn't be reviewed", p.failed, 'error'],
  ];

  return (
    <div className="n-card" style={{ padding: '14px 16px', display: 'grid', gap: 10 }} data-testid="background-job-card">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600, color: 'var(--text)' }}>{job.title}</span>
        <Badge tone={tone}>{statusLabel(job)}</Badge>
        {active && !job.cancel_requested && (
          <span style={{ marginLeft: 'auto' }}>
            <Button size="sm" onClick={cancel} disabled={cancelling} title="Stops after the invoice it is on — everything done so far stays saved">
              {cancelling ? 'Stopping…' : 'Cancel'}
            </Button>
          </span>
        )}
      </div>

      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={p.total || 0}
        aria-valuenow={p.done || 0}
        aria-label={`${p.done || 0} of ${p.total || 0} done`}
        style={{ height: 8, borderRadius: 999, background: 'var(--surface-alt)', overflow: 'hidden' }}
      >
        <div style={{ width: `${pct}%`, height: '100%', background: job.status === 'failed' ? 'var(--muted-soft)' : 'var(--accent)', transition: 'width 0.4s ease' }} />
      </div>

      <div style={{ display: 'flex', gap: '4px 12px', flexWrap: 'wrap', alignItems: 'center', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        <span style={{ color: 'var(--text)' }}>
          {p.done || 0} of {p.total || 0} done
        </span>
        {counts
          .filter(([, n]) => n)
          .map(([label, n, t]) => (
            <Badge key={label} tone={t}>
              {n} {label}
            </Badge>
          ))}
        <span>{formatDuration(job.elapsed_seconds)}{left != null ? ` · about ${formatDuration(left)} left` : ''}</span>
      </div>

      {active && p.current && job.status === 'running' && !job.stalled && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Last: {p.current}</div>
      )}
      {job.stalled && active && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          The server restarted — it carries on from where it got to in a moment.
        </div>
      )}
      {!active && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          {job.status === 'failed' && job.error ? `${job.error} · ` : ''}
          Norm&apos;s summary is in the conversation below.
        </div>
      )}
      {active && (
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          {job.cancel_requested
            ? 'Stopping after the invoice in hand — everything done so far is saved.'
            : 'You can leave this page — it keeps going, and Norm posts the result here and emails you when it’s done.'}
        </div>
      )}
    </div>
  );
}
