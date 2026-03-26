'use client';

import { useState, useEffect, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import type { AutomatedTask } from '../../types';

const SCHEDULE_LABELS: Record<string, string> = {
  manual: 'Manual',
  hourly: 'Hourly',
  daily: 'Daily',
  weekly: 'Weekly',
  monthly: 'Monthly',
};

const STATUS_STYLES: Record<string, { bg: string; color: string }> = {
  active: { bg: '#d1fae5', color: '#065f46' },
  paused: { bg: '#fef3c7', color: '#92400e' },
  draft: { bg: '#f3f4f6', color: '#6b7280' },
};

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

export default function AutomatedTaskBoard({ data, onAction }: DisplayBlockProps) {
  const initialTasks = ((data as Record<string, unknown>)?.tasks as AutomatedTask[]) || [];
  const [tasks, setTasks] = useState<AutomatedTask[]>(initialTasks);
  const [runningId, setRunningId] = useState<string | null>(null);

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

  if (tasks.length === 0) {
    return (
      <div style={{ padding: '3rem', textAlign: 'center', color: '#9ca3af' }}>
        <div style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>No automated tasks yet</div>
        <div style={{ fontSize: '0.82rem' }}>Ask Norm to create one — e.g., &ldquo;Set up a daily task to check BambooHR candidates&rdquo;</div>
      </div>
    );
  }

  return (
    <div data-testid="auto-task-board" style={{ padding: '0.5rem' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {tasks.map(task => {
          const ss = STATUS_STYLES[task.status] || STATUS_STYLES.draft;
          const isRunning = runningId === task.id;
          return (
            <div
              key={task.id}
              data-testid={`auto-task-card-${task.id}`}
              onClick={() => handleOpenTask(task)}
              style={{
                border: '1px solid #e5e7eb', borderRadius: 10,
                backgroundColor: '#fff', padding: '0.75rem 1rem',
                boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
                cursor: 'pointer',
                transition: 'border-color 0.15s',
              }}
              onMouseEnter={e => (e.currentTarget.style.borderColor = '#111')}
              onMouseLeave={e => (e.currentTarget.style.borderColor = '#e5e7eb')}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem' }}>
                <span style={{ fontSize: '0.9rem', fontWeight: 600, color: '#111', flex: 1 }}>{task.title}</span>
                <span style={{
                  fontSize: '0.6rem', fontWeight: 600, padding: '2px 8px', borderRadius: 10,
                  backgroundColor: ss.bg, color: ss.color,
                }}>{task.status}</span>
              </div>

              {task.description && (
                <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: '0.3rem' }}>{task.description}</div>
              )}

              <div style={{ display: 'flex', gap: '1rem', fontSize: '0.72rem', color: '#9ca3af', marginBottom: '0.4rem' }}>
                <span>{formatSchedule(task.schedule_type, task.schedule_config)}</span>
                <span>Last run: {timeAgo(task.last_run_at)}</span>
              </div>

              <div style={{ display: 'flex', gap: '0.3rem' }}>
                <button onClick={(e) => handleRun(e, task.id)} disabled={isRunning} style={{
                  padding: '3px 10px', fontSize: '0.68rem', fontWeight: 600,
                  border: 'none', borderRadius: 6, backgroundColor: '#111', color: '#fff',
                  cursor: isRunning ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
                }}>{isRunning ? 'Running...' : 'Run Now'}</button>

                <button onClick={(e) => handlePauseResume(e, task)} style={{
                  padding: '3px 10px', fontSize: '0.68rem', fontWeight: 500,
                  border: '1px solid #d1d5db', borderRadius: 6, backgroundColor: '#fff', color: '#6b7280',
                  cursor: 'pointer', fontFamily: 'inherit',
                }}>{task.status === 'active' ? 'Pause' : 'Activate'}</button>

                <button onClick={(e) => handleDelete(e, task.id)} style={{
                  padding: '3px 10px', fontSize: '0.68rem', fontWeight: 500,
                  border: '1px solid #fecaca', borderRadius: 6, backgroundColor: '#fff', color: '#dc2626',
                  cursor: 'pointer', fontFamily: 'inherit', marginLeft: 'auto',
                }}>Delete</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
