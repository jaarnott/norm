'use client';

/**
 * "Who gets it": the organisation's weekly report emails (automated tasks
 * this page set up, schedule.ts), each with its people, day and time, last
 * and next run, and Send now / Change / Pause / Remove.
 *
 * Send now runs the task for real (POST /api/automated-tasks/{id}/run,
 * mode "live"): Norm reads last week's figures, writes the report and emails
 * it. The request waits for the run, a minute or two.
 */

import React, { useState } from 'react';
import { Mail, MessageSquare, Pencil, Plus, Send, Trash2 } from 'lucide-react';
import { apiFetch } from '../../../lib/api';
import type { AutomatedTask, AutomatedTaskRun } from '../../../types';
import Badge, { type BadgeTone } from '../../ui/Badge';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import { ConfirmDialog } from '../../ui/Dialog';
import { Section } from '../../functions/enquiries/bits';
import { parseSettings, readSchedule, scheduleLabel } from './schedule';

export type EmailApproval = 'always' | 'ask' | null;

interface Notice { taskId: string; tone: 'ok' | 'warn' | 'error'; text: string }

const STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  active: { label: 'On', tone: 'ok' },
  paused: { label: 'Paused', tone: 'warn' },
  draft: { label: 'Off', tone: 'neutral' },
};

const RUN_WORDS: Record<string, string> = {
  success: 'done',
  waiting_for_approval: 'waiting for approval to send',
  error: 'failed',
  skipped_unhired: 'skipped (Reports isn’t hired)',
  declined: 'not sent (declined)',
  superseded: 'not sent (replaced by a later run)',
  running: 'running now',
};

function when(iso: string | null | undefined, timezone: string | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  try {
    return new Intl.DateTimeFormat('en-NZ', {
      weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
      ...(timezone ? { timeZone: timezone } : {}),
    }).format(d);
  } catch {
    return d.toLocaleString('en-NZ');
  }
}

async function detail(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  const d = body?.detail ?? body?.error;
  return typeof d === 'string' && d ? d : `${fallback} (error ${res.status})`;
}

export default function ReportSchedules({
  tasks, loading, error, lastRuns, myUserId, emailApproval, stacked,
  onAdd, onEdit, onChanged, onOpenTask, onAllowEmails,
}: {
  tasks: AutomatedTask[] | null;
  loading: boolean;
  error: string | null;
  lastRuns: Record<string, AutomatedTaskRun | null>;
  myUserId: string | null;
  emailApproval: EmailApproval;
  stacked: boolean;
  onAdd: () => void;
  onEdit: (task: AutomatedTask) => void;
  onChanged: () => void;
  onOpenTask: (task: AutomatedTask) => void;
  onAllowEmails: () => Promise<void>;
}) {
  const [sending, setSending] = useState<AutomatedTask | null>(null);
  const [removing, setRemoving] = useState<AutomatedTask | null>(null);
  const [allowing, setAllowing] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const list = tasks ?? [];
  // Scheduled sends run as the person who set the task up, with their
  // approvals. Only the viewer's own setting is known here.
  const mine = list.some((t) => t.created_by && t.created_by === myUserId);
  const asksFirst = emailApproval === 'ask' && (mine || list.length === 0);

  const sendNow = async (task: AutomatedTask) => {
    const res = await apiFetch(`/api/automated-tasks/${task.id}/run`, {
      method: 'POST',
      body: JSON.stringify({ mode: 'live' }),
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `The report could not be sent (error ${res.status}).`);
    if (body?.success === false) throw new Error(body?.error || 'The report could not be sent.');
    const status = body?.data?.status;
    if (status === 'waiting_for_approval') {
      setNotice({ taskId: task.id, tone: 'warn', text: 'Norm has written the report and is waiting for you to approve the email. Open the task to approve it.' });
    } else {
      const summary = typeof body?.data?.result_summary === 'string' ? body.data.result_summary.split('\n')[0] : '';
      setNotice({ taskId: task.id, tone: 'ok', text: summary || 'Done: the report has been sent.' });
    }
    onChanged();
  };

  const toggle = async (task: AutomatedTask) => {
    setBusy(task.id);
    setNotice(null);
    try {
      const res = await apiFetch(`/api/automated-tasks/${task.id}/${task.status === 'active' ? 'pause' : 'resume'}`, { method: 'POST' });
      if (!res.ok) setNotice({ taskId: task.id, tone: 'error', text: await detail(res, 'That didn’t work') });
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  const remove = async (task: AutomatedTask) => {
    const res = await apiFetch(`/api/automated-tasks/${task.id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await detail(res, 'Could not remove it'));
    onChanged();
  };

  const row = (task: AutomatedTask, i: number) => {
    const s = parseSettings(task.prompt);
    const sched = readSchedule(task.schedule_config);
    const tz = (task.schedule_config?.timezone as string | undefined) || undefined;
    const status = STATUS[task.status] ?? STATUS.draft;
    const run = lastRuns[task.id];
    const next = task.status === 'active' ? when(task.next_run_at, tz) : null;
    const last = run ? when(run.started_at, tz) : when(task.last_run_at, tz);
    const lastWords = run ? RUN_WORDS[run.status] ?? run.status : null;
    const rowNotice = notice?.taskId === task.id ? notice : null;
    return (
      <li key={task.id} data-testid={`weekly-report-task-${task.id}`} style={{ padding: '12px 16px', borderTop: i ? '1px solid var(--line-soft)' : undefined }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <div style={{ minWidth: 0, flex: '1 1 260px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <Badge tone={status.tone}>{status.label}</Badge>
              <span style={{ fontWeight: 600, color: 'var(--text)' }}>{task.schedule_type === 'weekly' ? scheduleLabel(sched) : 'Not on a weekly schedule'}</span>
              <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>{s.venues.toLowerCase() === 'all' ? 'All venues' : s.venues}</span>
              {(task.waiting_for_approval ?? 0) > 0 && <Badge tone="warn">Approval needed</Badge>}
            </div>
            <div style={{ marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', overflowWrap: 'anywhere' }}>
              To {s.recipients.length ? s.recipients.join(', ') : <span style={{ color: 'var(--error)' }}>nobody yet</span>}
            </div>
            <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)', display: 'flex', flexWrap: 'wrap', gap: '2px 12px' }}>
              {next && <span>Next {next}</span>}
              <span>{last ? `Last ran ${last}${lastWords ? `: ${lastWords}` : ''}` : 'Not sent yet'}</span>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <Button size="sm" icon={Send} onClick={() => { setNotice(null); setSending(task); }} disabled={s.recipients.length === 0}>Send now</Button>
            <Button size="sm" variant="quiet" icon={Pencil} onClick={() => onEdit(task)}>Change</Button>
            <Button size="sm" variant="quiet" onClick={() => void toggle(task)} disabled={busy === task.id}>
              {task.status === 'active' ? 'Pause' : 'Turn on'}
            </Button>
            <IconButton icon={MessageSquare} label="Open the task's conversation" iconSize={16} onClick={() => onOpenTask(task)} />
            <IconButton icon={Trash2} label="Remove" iconSize={16} onClick={() => setRemoving(task)} />
          </div>
        </div>
        {rowNotice && (
          <div role="status" style={{
            marginTop: 8, padding: '8px 12px', borderRadius: 'var(--radius)', fontSize: 'var(--fs-sm)',
            color: `var(--${rowNotice.tone})`, background: `var(--${rowNotice.tone}-bg)`,
            display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
          }}>
            <span style={{ flex: 1, minWidth: 0 }}>{rowNotice.text}</span>
            {rowNotice.tone === 'warn' && <Button size="xs" onClick={() => onOpenTask(task)}>Open the task</Button>}
          </div>
        )}
      </li>
    );
  };

  const body = loading && !tasks ? (
    <PageState kind="loading" title="Loading who gets it…" />
  ) : error ? (
    <div style={{ padding: 12 }}><PageState kind="error" title="Could not load the report emails." detail={error} /></div>
  ) : list.length === 0 ? (
    <PageState
      kind="empty"
      title="Nobody gets this report by email yet."
      detail="Choose who gets it and when. Norm sends it every week."
      action={<Button variant="primary" icon={Mail} onClick={onAdd}>Set up the weekly email</Button>}
    />
  ) : (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>{list.map(row)}</ul>
  );

  return (
    <>
      <Section
        title="Who gets it"
        meta={list.length ? `${list.length} email${list.length === 1 ? '' : 's'} set up` : undefined}
        actions={list.length ? <Button size="sm" variant="quiet" icon={Plus} onClick={onAdd}>{stacked ? 'Add' : 'Add another'}</Button> : undefined}
        padded={false}
      >
        {body}
        {asksFirst && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '10px 16px',
            borderTop: '1px solid var(--line-soft)', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', background: 'var(--surface)',
            // The card's last row: its fill keeps to the card's rounded corners.
            borderBottomLeftRadius: 'calc(var(--radius-lg) - 1px)', borderBottomRightRadius: 'calc(var(--radius-lg) - 1px)',
          }}>
            <span style={{ flex: '1 1 240px', minWidth: 0 }}>
              Norm asks you before it emails a report, so each week&rsquo;s report waits for your approval.
            </span>
            <Button size="sm" onClick={() => setAllowing(true)}>Let Norm send it without asking</Button>
          </div>
        )}
      </Section>

      {sending && (
        <ConfirmDialog
          title="Send last week’s report now?"
          confirmLabel="Send now"
          busyLabel="Sending…"
          onConfirm={() => sendNow(sending)}
          onClose={() => setSending(null)}
        >
          Norm will put last week&rsquo;s report together and email it to {parseSettings(sending.prompt).recipients.join(', ')}. It takes a minute or two.
          {emailApproval === 'ask' && sending.created_by === myUserId && (
            <> Norm asks you before it emails a report, so you&rsquo;ll approve the email in the task&rsquo;s conversation before it goes.</>
          )}
        </ConfirmDialog>
      )}
      {removing && (
        <ConfirmDialog
          title="Stop this weekly email?"
          confirmLabel="Remove"
          busyLabel="Removing…"
          danger
          onConfirm={() => remove(removing)}
          onClose={() => setRemoving(null)}
        >
          {parseSettings(removing.prompt).recipients.join(', ') || 'Nobody'} will stop getting the weekly report. Its past runs are removed too.
        </ConfirmDialog>
      )}
      {allowing && (
        <ConfirmDialog
          title="Let Norm email reports without asking?"
          confirmLabel="Allow"
          busyLabel="Saving…"
          onConfirm={onAllowEmails}
          onClose={() => setAllowing(false)}
        >
          Norm will send report emails without asking you first: this weekly report and any other report a task of yours emails. You can change this in Settings, under Approvals.
        </ConfirmDialog>
      )}
    </>
  );
}
