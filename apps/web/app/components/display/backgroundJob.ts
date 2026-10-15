/**
 * Pure display logic for the background-job card (BackgroundJobCard): how a
 * job's state reads, how long it has run and roughly how long it has left.
 */

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

export interface JobProgress {
  total?: number;
  done?: number;
  received?: number;
  needs_you?: number;
  failed?: number;
  current?: string;
  last_outcome?: string;
}

export interface BackgroundJob {
  id: string;
  kind: string;
  title: string;
  status: JobStatus;
  cancel_requested: boolean;
  progress: JobProgress;
  error?: string | null;
  elapsed_seconds: number;
  stalled: boolean;
  attempts: number;
}

export const isActive = (s: JobStatus) => s === 'queued' || s === 'running';

export function statusLabel(job: Pick<BackgroundJob, 'status' | 'cancel_requested' | 'stalled'>): string {
  if (job.status === 'running' && job.cancel_requested) return 'Stopping…';
  if (job.status === 'running' && job.stalled) return 'Picking back up…';
  return {
    queued: 'Waiting to start',
    running: 'Running',
    done: 'Finished',
    failed: "Didn't finish",
    cancelled: 'Stopped',
  }[job.status];
}

/** 75 → "1m 15s"; 3700 → "1h 1m". */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

/** Rough time left from the pace so far — none until a few are done (the
 *  first ones include reading every copy, so they run slow). */
export function estimateRemaining(progress: JobProgress, elapsedSeconds: number): number | null {
  const done = progress.done ?? 0;
  const total = progress.total ?? 0;
  if (done < 3 || total <= done || elapsedSeconds <= 0) return null;
  return Math.round((elapsedSeconds / done) * (total - done));
}

export function percent(progress: JobProgress): number {
  const total = progress.total ?? 0;
  if (!total) return 0;
  return Math.min(100, Math.round(((progress.done ?? 0) / total) * 100));
}
