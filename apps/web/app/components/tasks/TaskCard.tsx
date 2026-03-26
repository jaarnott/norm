'use client';

import { useState } from 'react';
import { Package, UserRound, BarChart3, HelpCircle, Timer, type LucideIcon } from 'lucide-react';
import type { Task, ProcurementTask, HrTask } from '../../types';
import { colors } from '../../lib/theme';

const DOMAIN_ICONS: Record<string, LucideIcon> = {
  procurement: Package,
  hr: UserRound,
  reports: BarChart3,
};

const STATUS_STYLES: Record<string, { bg: string; color: string; label: string }> = {
  awaiting_approval: { bg: '#fff3cd', color: '#856404', label: 'Awaiting approval' },
  awaiting_tool_approval: { bg: '#e8daef', color: '#6c3483', label: 'Tool approval needed' },
  awaiting_user_input: { bg: '#f5f0ea', color: '#8a7356', label: 'Needs input' },
  needs_clarification: { bg: '#f8d7da', color: '#721c24', label: 'Needs input' },
  needs_information: { bg: '#f8d7da', color: '#721c24', label: 'Needs input' },
  in_progress: { bg: '#d1ecf1', color: '#0c5460', label: 'Working' },
  completed: { bg: '#d4edda', color: '#155724', label: 'Completed' },
  approved: { bg: '#d4edda', color: '#155724', label: 'Approved' },
  rejected: { bg: '#e2e3e5', color: '#383d41', label: 'Rejected' },
  submitted: { bg: '#cce5ff', color: '#004085', label: 'Submitted' },
};

function getTaskTitle(task: Task): string {
  return task.title || '';
}

function getTaskSummary(task: Task): string {
  if (task.domain === 'procurement') {
    const t = task as ProcurementTask;
    const parts: string[] = [];
    if (t.quantity) parts.push(`${t.quantity} case${t.quantity !== 1 ? 's' : ''}`);
    if (t.product?.name) parts.push(t.product.name);
    if (t.venue?.name) parts.push(`\u2014 ${t.venue.name}`);
    return parts.join(' ') || task.message || 'Procurement request';
  }
  if (task.domain === 'hr') {
    const t = task as HrTask;
    const parts: string[] = [];
    if (t.employee_name) parts.push(t.employee_name);
    if (t.role) parts.push(`\u2014 ${t.role}`);
    if (t.venue?.name) parts.push(`\u2014 ${t.venue.name}`);
    return parts.join(' ') || task.message || 'HR request';
  }
  return task.message || '';
}

function timeAgo(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function getDomainColor(domain: string): string {
  return (colors as unknown as Record<string, string>)[domain] || colors.unknown;
}

function formatSchedule(type: string, config: Record<string, unknown>): string {
  const hour = config.hour as number | undefined;
  const minute = config.minute as number | undefined;
  const time = hour != null ? `${String(hour).padStart(2, '0')}:${String(minute ?? 0).padStart(2, '0')}` : '';
  const day = config.day_of_week as string | undefined;
  if (type === 'daily' && time) return `Daily at ${time}`;
  if (type === 'weekly' && day) return `${day.charAt(0).toUpperCase() + day.slice(1)}s at ${time}`;
  if (type === 'monthly') return `Day ${config.day_of_month || 1} at ${time}`;
  const labels: Record<string, string> = { manual: 'Manual', hourly: 'Hourly', daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly' };
  return labels[type] || type;
}

interface TaskCardProps {
  task: Task;
  isSelected: boolean;
  onClick: () => void;
  onRemove: () => void;
  compact?: boolean;
  'data-testid'?: string;
}

export default function TaskCard({ task, isSelected, onClick, onRemove, compact, 'data-testid': testId }: TaskCardProps) {
  const [confirming, setConfirming] = useState(false);
  const dc = getDomainColor(task.domain);
  const DomainIcon = DOMAIN_ICONS[task.domain] || HelpCircle;
  const ss = STATUS_STYLES[task.status] || { bg: '#e2e3e5', color: '#383d41', label: task.status.replace(/_/g, ' ') };
  const isWaiting = task.status === 'awaiting_user_input' || task.status === 'needs_clarification';
  const isAutomated = !!task.automated_task;

  if (confirming) {
    return (
      <div
        style={{
          padding: '0.85rem 1rem',
          borderBottom: '1px solid #f0f0f0',
          backgroundColor: '#fef2f2',
          borderLeft: '3px solid #dc3545',
        }}
      >
        <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#333', marginBottom: '0.5rem' }}>
          Remove this thread?
        </div>
        <div style={{ fontSize: '0.82rem', color: '#666', marginBottom: '0.6rem' }}>
          {getTaskTitle(task)} — {getTaskSummary(task)}
        </div>
        <div style={{ display: 'flex', gap: '0.4rem' }}>
          <button
            onClick={(e) => { e.stopPropagation(); onRemove(); }}
            style={{
              padding: '0.3rem 0.8rem',
              fontSize: '0.82rem',
              fontWeight: 600,
              backgroundColor: '#dc3545',
              color: '#fff',
              border: 'none',
              borderRadius: 5,
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            Remove
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setConfirming(false); }}
            style={{
              padding: '0.3rem 0.8rem',
              fontSize: '0.82rem',
              fontWeight: 500,
              backgroundColor: '#fff',
              color: '#555',
              border: '1px solid #ddd',
              borderRadius: 5,
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (compact) {
    const dotColor = dc;
    return (
      <div
        onClick={onClick}
        className="compact-card"
        data-testid={testId}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.45rem 1rem',
          cursor: 'pointer',
          backgroundColor: isSelected ? '#f5f0ea' : 'transparent',
          borderLeft: `3px solid ${isSelected ? dc : 'transparent'}`,
          borderBottom: '1px solid #f8f8f8',
          transition: 'background-color 0.1s',
        }}
      >
        {isAutomated ? (
          <Timer size={12} strokeWidth={2} style={{ color: '#9ca3af', flexShrink: 0 }} />
        ) : (
          <span style={{
            width: 7, height: 7, borderRadius: '50%',
            backgroundColor: dotColor, flexShrink: 0,
          }} />
        )}
        <span style={{
          flex: 1, fontSize: '0.85rem', color: '#333',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {isAutomated ? (task.automated_task?.title || getTaskTitle(task)) : (getTaskSummary(task) || getTaskTitle(task))}
        </span>
        <button
          onClick={(e) => { e.stopPropagation(); setConfirming(true); }}
          title="Remove"
          className="compact-remove"
          style={{
            border: 'none', background: 'none', cursor: 'pointer',
            fontSize: '0.78rem', color: '#ccc', padding: '0 2px',
            lineHeight: 1, fontFamily: 'inherit', flexShrink: 0,
            opacity: 0, transition: 'opacity 0.15s',
          }}
        >
          {'\u2715'}
        </button>
      </div>
    );
  }

  return (
    <div
      onClick={onClick}
      data-testid={testId}
      style={{
        padding: '0.85rem 1rem',
        borderBottom: '1px solid #f0f0f0',
        cursor: 'pointer',
        backgroundColor: isSelected ? '#f5f0ea' : isWaiting ? '#fdf6ee' : 'transparent',
        borderLeft: `3px solid ${isSelected ? dc : 'transparent'}`,
        transition: 'background-color 0.15s',
      }}
    >
      {/* Top row: agent + time */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.3rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
          <DomainIcon size={14} strokeWidth={1.75} style={{ color: dc }} />
          <span style={{
            fontSize: '0.75rem',
            fontWeight: 600,
            color: dc,
            textTransform: 'uppercase',
            letterSpacing: '0.03em',
          }}>
            {task.domain}
          </span>
          {isAutomated && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 3,
              fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af',
              padding: '1px 6px', borderRadius: 8, backgroundColor: '#f3f4f6',
            }}>
              <Timer size={10} strokeWidth={2} /> Saved
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <span style={{ fontSize: '0.72rem', color: '#aaa' }}>
            {timeAgo(task.created_at)}
          </span>
          <button
            onClick={(e) => { e.stopPropagation(); setConfirming(true); }}
            title="Remove"
            style={{
              border: 'none',
              background: 'none',
              cursor: 'pointer',
              fontSize: '0.82rem',
              color: '#ccc',
              padding: '0 2px',
              lineHeight: 1,
              fontFamily: 'inherit',
            }}
          >
            {'\u2715'}
          </button>
        </div>
      </div>

      {/* Title */}
      <div style={{ fontSize: '0.95rem', fontWeight: 600, color: '#1a1a1a', marginBottom: '0.2rem' }}>
        {getTaskTitle(task)}
      </div>

      {/* Summary */}
      <div style={{
        fontSize: '0.85rem',
        color: '#666',
        marginBottom: '0.4rem',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}>
        {isAutomated && task.automated_task
          ? formatSchedule(task.automated_task.schedule_type, task.automated_task.schedule_config)
          : getTaskSummary(task)
        }
      </div>

      {/* Status badge */}
      <span style={{
        fontSize: '0.72rem',
        fontWeight: 600,
        padding: '0.15rem 0.5rem',
        borderRadius: 10,
        backgroundColor: ss.bg,
        color: ss.color,
        textTransform: 'capitalize',
      }}>
        {ss.label}
      </span>
    </div>
  );
}
