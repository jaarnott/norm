'use client';

import { useState, useEffect, useCallback } from 'react';
import type { CSSProperties } from 'react';
import { Play, Trash2 } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import type { AutomatedTask } from '../../types';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';

const SCHEDULE_LABELS: Record<string, string> = {
  manual: 'Manual',
  hourly: 'Hourly',
  daily: 'Daily',
  weekly: 'Weekly',
  monthly: 'Monthly',
};

// active → ok · paused → warn · draft (and anything unexpected) → neutral.
const STATUS_TONES: Record<string, BadgeTone> = {
  active: 'ok',
  paused: 'warn',
  draft: 'neutral',
};

/** Below this width the table's columns crowd the task name, so the board
 *  shows one card per task instead (phones, iPad portrait, a dashboard tile). */
const TABLE_MIN_WIDTH = 720;

const DESCRIPTION: CSSProperties = {
  marginTop: 2,
  fontSize: 'var(--fs-sm)',
  color: 'var(--text-soft)',
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
};

function sentenceCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function formatSchedule(type: string, config: Record<string, unknown>): string {
  const hour = config.hour as number | undefined;
  const minute = config.minute as number | undefined;
  const time = hour != null ? `${String(hour).padStart(2, '0')}:${String(minute ?? 0).padStart(2, '0')}` : '';
  const day = config.day_of_week as string | undefined;
  if (type === 'daily' && time) return `Daily at ${time}`;
  if (type === 'weekly' && day) return `${day.charAt(0).toUpperCase() + day.slice(1)}s at ${time}`;
  if (type === 'monthly') return `Day ${config.day_of_month || 1} at ${time}`;
  return SCHEDULE_LABELS[type] || type;
}

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return 'Never';
  const d = new Date(dateStr);
  const now = new Date();
  const mins = Math.floor((now.getTime() - d.getTime()) / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export default function AutomatedTaskBoard({ data, props, onAction }: DisplayBlockProps) {
  const initialTasks = ((data as Record<string, unknown>)?.tasks as AutomatedTask[]) || [];
  const [tasks, setTasks] = useState<AutomatedTask[]>(initialTasks);
  const [runningId, setRunningId] = useState<string | null>(null);

  // A PAGE instance (FunctionalPage marks it with persistVenue) draws the page
  // header; in a conversation or a dashboard tile the board stays compact.
  const isPage = !!props?.persistVenue;

  // The board's own width decides table or cards — not the viewport, since
  // the menu panel, a split conversation or a dashboard tile can all make it
  // narrow on a wide screen.
  const [width, setWidth] = useState(0);
  const measure = useCallback((el: HTMLDivElement | null) => {
    if (!el) return;
    setWidth(el.offsetWidth);
    const ro = new ResizeObserver(() => setWidth(el.offsetWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => { setTasks(initialTasks); }, [data]);

  const agentSlug = tasks.length > 0 ? tasks[0].agent_slug : '';

  const reload = useCallback(async () => {
    const res = await apiFetch(`/api/automated-tasks?agent_slug=${agentSlug}`);
    if (res.ok) {
      const result = await res.json();
      setTasks(result.tasks || []);
    }
  }, [agentSlug]);

  const handleRun = useCallback(async (e: React.MouseEvent, taskId: string) => {
    e.stopPropagation();
    setRunningId(taskId);
    try {
      await apiFetch(`/api/automated-tasks/${taskId}/run`, { method: 'POST', body: JSON.stringify({ mode: 'live' }) });
      await reload();
    } finally { setRunningId(null); }
  }, [reload]);

  const handlePauseResume = useCallback(async (e: React.MouseEvent, task: AutomatedTask) => {
    e.stopPropagation();
    const endpoint = task.status === 'active' ? 'pause' : 'resume';
    await apiFetch(`/api/automated-tasks/${task.id}/${endpoint}`, { method: 'POST' });
    await reload();
  }, [reload]);

  const handleDelete = useCallback(async (e: React.MouseEvent, taskId: string) => {
    e.stopPropagation();
    await apiFetch(`/api/automated-tasks/${taskId}`, { method: 'DELETE' });
    await reload();
  }, [reload]);

  const handleOpenTask = useCallback(async (task: AutomatedTask) => {
    let convThreadId = task.conversation_thread_id;

    // If no conversation thread yet, create one (lightweight, no LLM call)
    if (!convThreadId) {
      const res = await apiFetch(`/api/automated-tasks/${task.id}/ensure-conversation`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        convThreadId = data.conversation_thread_id;
      }
    }

    if (convThreadId && onAction) {
      onAction({ connector_name: 'norm', action: 'open_automated_task', params: { conversation_thread_id: convThreadId } });
    }
  }, [onAction]);

  const compactTitle = (meta?: string) => (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
      <h2 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>Automated Tasks</h2>
      {meta && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</span>}
    </div>
  );

  if (tasks.length === 0) {
    const empty = (
      <PageState
        kind="empty"
        title="No automated tasks yet"
        detail={<>Ask Norm to create one — e.g., &ldquo;Set up a daily task to check BambooHR candidates&rdquo;</>}
      />
    );
    if (isPage) {
      return (
        <div>
          <PageHeader title="Automated Tasks" />
          {empty}
        </div>
      );
    }
    return (
      <div style={{ padding: 8 }}>
        {compactTitle()}
        {empty}
      </div>
    );
  }

  const countLabel = `${tasks.length} task${tasks.length !== 1 ? 's' : ''} configured`;
  const toolCount = (task: AutomatedTask) => task.tool_filter?.length ?? 0;

  const badges = (task: AutomatedTask) => (
    <>
      {task.status && <Badge tone={STATUS_TONES[task.status] ?? 'neutral'}>{sentenceCase(task.status)}</Badge>}
      {(task.waiting_for_approval ?? 0) > 0 && (
        <Badge tone="warn" title="A run is waiting for your approval">Approval needed</Badge>
      )}
    </>
  );

  // Run and pause are text buttons; delete is the icon at the end. One row of
  // the board, so no primary: the page's one primary is the composer's Send.
  const actions = (task: AutomatedTask, deleteAtEnd = false) => {
    const isRunning = runningId === task.id;
    return (
      <>
        <Button size="sm" icon={Play} onClick={(e) => handleRun(e, task.id)} disabled={isRunning}>
          {isRunning ? 'Running…' : 'Run now'}
        </Button>
        <Button size="sm" variant="quiet" onClick={(e) => handlePauseResume(e, task)}>
          {task.status === 'active' ? 'Pause' : 'Activate'}
        </Button>
        <IconButton
          icon={Trash2}
          label="Delete"
          iconSize={16}
          onClick={(e) => handleDelete(e, task.id)}
          style={deleteAtEnd ? { marginLeft: 'auto' } : undefined}
        />
      </>
    );
  };

  const table = (
    <table className="n-table" aria-label="Automated tasks">
      <thead>
        <tr>
          <th scope="col">Task</th>
          <th scope="col">Schedule</th>
          <th scope="col">Last run</th>
          <th scope="col">Status</th>
          <th scope="col" aria-label="Actions" />
        </tr>
      </thead>
      <tbody>
        {tasks.map(task => (
          <tr
            key={task.id}
            data-testid={`auto-task-card-${task.id}`}
            onClick={() => handleOpenTask(task)}
            style={{ cursor: 'pointer' }}
          >
            <td style={{ minWidth: 200 }}>
              <div style={{ fontWeight: 500, color: 'var(--text)' }}>{task.title}</div>
              {task.description && <div style={DESCRIPTION} title={task.description}>{task.description}</div>}
              {toolCount(task) > 0 && (
                <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Tools: {toolCount(task)} filtered</div>
              )}
            </td>
            <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{formatSchedule(task.schedule_type, task.schedule_config)}</td>
            <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{timeAgo(task.last_run_at)}</td>
            <td>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>{badges(task)}</div>
            </td>
            <td>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 4 }}>{actions(task)}</div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  const cards = (
    <ul role="list" aria-label="Automated tasks" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {tasks.map(task => (
        <li
          key={task.id}
          data-testid={`auto-task-card-${task.id}`}
          className="n-card"
          onClick={() => handleOpenTask(task)}
          onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--field)')}
          onMouseLeave={e => (e.currentTarget.style.borderColor = '')}
          style={{ padding: '14px 16px', cursor: 'pointer', transition: 'border-color 0.15s' }}
        >
          <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{task.title}</div>
          {task.description && <div style={{ ...DESCRIPTION, marginTop: 4 }} title={task.description}>{task.description}</div>}
          {/* Status first, then the muted schedule — the mobile page pattern. */}
          <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '4px 10px', marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            {badges(task)}
            <span>{formatSchedule(task.schedule_type, task.schedule_config)}</span>
            <span>Last run: {timeAgo(task.last_run_at)}</span>
            {toolCount(task) > 0 && <span>Tools: {toolCount(task)} filtered</span>}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10 }}>
            {actions(task, true)}
          </div>
        </li>
      ))}
    </ul>
  );

  const wide = width >= TABLE_MIN_WIDTH;

  return (
    <div data-testid="auto-task-board" style={isPage ? undefined : { padding: 8 }}>
      {isPage ? <PageHeader title="Automated Tasks" meta={countLabel} /> : compactTitle(countLabel)}
      <div ref={measure}>
        {!wide ? cards : isPage ? table : (
          // In a conversation the table sits on its own white card; a wide
          // row scrolls inside the card rather than losing its actions.
          <div className="n-card" style={{ overflowX: 'auto' }}>{table}</div>
        )}
      </div>
    </div>
  );
}
