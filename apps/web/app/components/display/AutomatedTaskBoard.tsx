'use client';

import { useState, useEffect, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import type { AutomatedTask, AutomatedTaskRun } from '../../types';

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

const RUN_STATUS_STYLES: Record<string, { bg: string; color: string }> = {
  success: { bg: '#d1fae5', color: '#065f46' },
  error: { bg: '#fef2f2', color: '#991b1b' },
  running: { bg: '#dbeafe', color: '#1e40af' },
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

export default function AutomatedTaskBoard({ data }: DisplayBlockProps) {
  const initialTasks = ((data as Record<string, unknown>)?.tasks as AutomatedTask[]) || [];
  const [tasks, setTasks] = useState<AutomatedTask[]>(initialTasks);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [runs, setRuns] = useState<AutomatedTaskRun[]>([]);
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [runningId, setRunningId] = useState<string | null>(null);

  useEffect(() => {
    setTasks(initialTasks);
  }, [data]);

  const agentSlug = tasks.length > 0 ? tasks[0].agent_slug : '';

  const reload = useCallback(async () => {
    const res = await apiFetch(`/api/automated-tasks?agent_slug=${agentSlug}`);
    if (res.ok) {
      const result = await res.json();
      setTasks(result.tasks || []);
    }
  }, [agentSlug]);

  const handleRun = useCallback(async (taskId: string, mode: string = 'live') => {
    setRunningId(taskId);
    try {
      await apiFetch(`/api/automated-tasks/${taskId}/run`, {
        method: 'POST',
        body: JSON.stringify({ mode }),
      });
      await reload();
    } finally {
      setRunningId(null);
    }
  }, [reload]);

  const handlePause = useCallback(async (taskId: string) => {
    await apiFetch(`/api/automated-tasks/${taskId}/pause`, { method: 'POST' });
    await reload();
  }, [reload]);

  const handleResume = useCallback(async (taskId: string) => {
    await apiFetch(`/api/automated-tasks/${taskId}/resume`, { method: 'POST' });
    await reload();
  }, [reload]);

  const handleDelete = useCallback(async (taskId: string) => {
    await apiFetch(`/api/automated-tasks/${taskId}`, { method: 'DELETE' });
    setSelectedId(null);
    setRuns([]);
    await reload();
  }, [reload]);

  const handleViewHistory = useCallback(async (taskId: string) => {
    if (selectedId === taskId) {
      setSelectedId(null);
      return;
    }
    setSelectedId(taskId);
    setLoadingRuns(true);
    try {
      const res = await apiFetch(`/api/automated-tasks/${taskId}/runs`);
      if (res.ok) {
        const result = await res.json();
        setRuns(result.runs || []);
      }
    } finally {
      setLoadingRuns(false);
    }
  }, [selectedId]);

  if (tasks.length === 0) {
    return (
      <div style={{ padding: '3rem', textAlign: 'center', color: '#9ca3af' }}>
        <div style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>No automated tasks yet</div>
        <div style={{ fontSize: '0.82rem' }}>Ask Norm to create one — e.g., "Set up a daily task to check BambooHR candidates"</div>
      </div>
    );
  }

  return (
    <div data-testid="auto-task-board" style={{ padding: '0.5rem' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {tasks.map(task => {
          const ss = STATUS_STYLES[task.status] || STATUS_STYLES.draft;
          const isSelected = selectedId === task.id;
          const isRunning = runningId === task.id;
          return (
            <div key={task.id} data-testid={`auto-task-card-${task.id}`}>
              <div style={{
                border: '1px solid #e5e7eb', borderRadius: 10,
                backgroundColor: '#fff', padding: '0.75rem 1rem',
                boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' }}>
                  <span style={{ fontSize: '0.9rem', fontWeight: 600, color: '#111', flex: 1 }}>{task.title}</span>
                  <span style={{
                    fontSize: '0.65rem', fontWeight: 600, padding: '2px 8px', borderRadius: 10,
                    backgroundColor: ss.bg, color: ss.color,
                  }}>{task.status}</span>
                </div>

                {task.description && (
                  <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: '0.4rem' }}>{task.description}</div>
                )}

                <div style={{ display: 'flex', gap: '1rem', fontSize: '0.72rem', color: '#9ca3af', marginBottom: '0.5rem' }}>
                  <span>{formatSchedule(task.schedule_type, task.schedule_config)}</span>
                  <span>Last run: {timeAgo(task.last_run_at)}</span>
                </div>

                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button onClick={() => handleRun(task.id)} disabled={isRunning} style={{
                    padding: '4px 12px', fontSize: '0.72rem', fontWeight: 600,
                    border: 'none', borderRadius: 6, backgroundColor: '#111', color: '#fff',
                    cursor: isRunning ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
                  }}>{isRunning ? 'Running...' : 'Run Now'}</button>

                  {task.status === 'active' ? (
                    <button onClick={() => handlePause(task.id)} style={{
                      padding: '4px 12px', fontSize: '0.72rem', fontWeight: 500,
                      border: '1px solid #d1d5db', borderRadius: 6, backgroundColor: '#fff', color: '#6b7280',
                      cursor: 'pointer', fontFamily: 'inherit',
                    }}>Pause</button>
                  ) : task.status === 'paused' || task.status === 'draft' ? (
                    <button onClick={() => handleResume(task.id)} style={{
                      padding: '4px 12px', fontSize: '0.72rem', fontWeight: 500,
                      border: '1px solid #d1d5db', borderRadius: 6, backgroundColor: '#fff', color: '#6b7280',
                      cursor: 'pointer', fontFamily: 'inherit',
                    }}>Activate</button>
                  ) : null}

                  <button onClick={() => handleViewHistory(task.id)} style={{
                    padding: '4px 12px', fontSize: '0.72rem', fontWeight: 500,
                    border: '1px solid #d1d5db', borderRadius: 6,
                    backgroundColor: isSelected ? '#f3f4f6' : '#fff', color: '#6b7280',
                    cursor: 'pointer', fontFamily: 'inherit',
                  }}>History</button>

                  <button onClick={() => handleDelete(task.id)} style={{
                    padding: '4px 12px', fontSize: '0.72rem', fontWeight: 500,
                    border: '1px solid #fecaca', borderRadius: 6, backgroundColor: '#fff', color: '#dc2626',
                    cursor: 'pointer', fontFamily: 'inherit', marginLeft: 'auto',
                  }}>Delete</button>
                </div>
              </div>

              {/* Run history */}
              {isSelected && (
                <div style={{
                  marginTop: '0.25rem', padding: '0.5rem 1rem',
                  border: '1px solid #e5e7eb', borderRadius: 8,
                  backgroundColor: '#fafafa', fontSize: '0.78rem',
                }}>
                  <div style={{ fontWeight: 600, color: '#6b7280', marginBottom: '0.4rem', fontSize: '0.72rem', textTransform: 'uppercase' }}>
                    Run History
                  </div>
                  {loadingRuns ? (
                    <div style={{ color: '#9ca3af' }}>Loading...</div>
                  ) : runs.length === 0 ? (
                    <div style={{ color: '#9ca3af' }}>No runs yet</div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                      {runs.map(run => {
                        const rs = RUN_STATUS_STYLES[run.status] || RUN_STATUS_STYLES.running;
                        return (
                          <div key={run.id} style={{
                            display: 'flex', alignItems: 'center', gap: '0.5rem',
                            padding: '0.3rem 0', borderBottom: '1px solid #f3f4f6',
                          }}>
                            <span style={{
                              fontSize: '0.65rem', fontWeight: 600, padding: '1px 6px', borderRadius: 8,
                              backgroundColor: rs.bg, color: rs.color,
                            }}>{run.status}</span>
                            {run.mode === 'test' && (
                              <span style={{
                                fontSize: '0.6rem', fontWeight: 600, padding: '1px 5px', borderRadius: 6,
                                backgroundColor: '#e0e7ff', color: '#4338ca',
                              }}>TEST</span>
                            )}
                            <span style={{ color: '#9ca3af', fontSize: '0.72rem' }}>
                              {run.started_at ? new Date(run.started_at).toLocaleString() : ''}
                            </span>
                            {run.duration_ms != null && (
                              <span style={{ color: '#d1d5db', fontSize: '0.68rem' }}>{(run.duration_ms / 1000).toFixed(1)}s</span>
                            )}
                            {run.tool_calls_count > 0 && (
                              <span style={{ color: '#d1d5db', fontSize: '0.68rem' }}>{run.tool_calls_count} tools</span>
                            )}
                            <span style={{ flex: 1, color: '#6b7280', fontSize: '0.72rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {run.result_summary?.slice(0, 100) || run.error_message?.slice(0, 100) || ''}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
