'use client';

import { useState } from 'react';
import { MessageCircle, Timer, X, type LucideIcon } from 'lucide-react';
import type { Thread, ProcurementThread, HrThread } from '../../types';
import { threadAccent, threadLabel } from '../../lib/threadApps';
import { memberName } from '../../lib/memberNames';
import { AGENTS } from '../layout/Sidebar';
import Icon from '../ui/Icon';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';

/** Statuses that need someone — the only ones a card shows. Approval is warn
 *  (waiting on a person), input is the tan accent (waiting on YOU). */
const STATUS_BADGES: Record<string, { tone: BadgeTone; label: string }> = {
  awaiting_approval: { tone: 'warn', label: 'Awaiting approval' },
  awaiting_tool_approval: { tone: 'warn', label: 'Approval needed' },
  awaiting_user_input: { tone: 'accent', label: 'Needs input' },
  needs_clarification: { tone: 'accent', label: 'Needs input' },
};

/** The team member's own icon — a thread without one is Norm's. */
function memberIcon(member: string): LucideIcon {
  return AGENTS.find(a => a.id === member)?.icon ?? MessageCircle;
}

/** "BambooHR · Loaded Reports" from the Apps it used; a thread from before
 *  Apps names its member in words ("Time & attendance", not the slug). */
function cardLabel(thread: Thread): string {
  if (thread.apps && thread.apps.length) return threadLabel(thread);
  const member = threadAccent(thread);
  return member === 'norm' ? 'Norm' : memberName(member);
}

function getThreadTitle(thread: Thread): string {
  return thread.title || '';
}

function getThreadSummary(thread: Thread): string {
  if (thread.domain === 'procurement') {
    const t = thread as ProcurementThread;
    const parts: string[] = [];
    if (t.quantity) parts.push(`${t.quantity} case${t.quantity !== 1 ? 's' : ''}`);
    if (t.product?.name) parts.push(t.product.name);
    if (t.venue?.name) parts.push(`\u2014 ${t.venue.name}`);
    return parts.join(' ') || thread.message || 'Procurement request';
  }
  if (thread.domain === 'hr') {
    const t = thread as HrThread;
    const parts: string[] = [];
    if (t.employee_name) parts.push(t.employee_name);
    if (t.role) parts.push(`\u2014 ${t.role}`);
    if (t.venue?.name) parts.push(`\u2014 ${t.venue.name}`);
    return parts.join(' ') || thread.message || 'HR request';
  }
  return thread.message || '';
}

/** Short, like a messaging app: now, 5m, 18h, 9d. */
function timeAgo(dateStr: string): string {
  const mins = Math.floor((Date.now() - new Date(dateStr).getTime()) / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
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

interface ThreadCardProps {
  thread: Thread;
  isSelected: boolean;
  onClick: () => void;
  onRemove: () => void;
  compact?: boolean;
  'data-testid'?: string;
}

export default function ThreadCard({ thread, isSelected, onClick, onRemove, compact, 'data-testid': testId }: ThreadCardProps) {
  const [confirming, setConfirming] = useState(false);
  const MemberIcon = memberIcon(threadAccent(thread));
  const badge = STATUS_BADGES[thread.status];
  const isAutomated = !!thread.automated_task;
  const runWaiting = (thread.automated_task?.waiting_for_approval ?? 0) > 0;
  // Selected: the selected tile plus a 3px accent bar on the left edge.
  const rowState = {
    backgroundColor: isSelected ? 'var(--selected)' : undefined,
    boxShadow: isSelected ? 'inset 3px 0 0 var(--accent)' : undefined,
  };
  const removeButton = (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); setConfirming(true); }}
      title="Remove"
      aria-label="Remove thread"
      className={compact ? 'n-icon-btn compact-remove' : 'n-icon-btn'}
      style={{ width: 24, height: 24, flexShrink: 0, ...(compact ? { opacity: 0, transition: 'opacity 0.15s' } : {}) }}
    >
      <X size={14} aria-hidden />
    </button>
  );

  if (confirming) {
    return (
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--line)', backgroundColor: 'var(--error-bg)' }}>
        <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>
          Remove this thread?
        </div>
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginBottom: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {getThreadTitle(thread) || getThreadSummary(thread)}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <Button size="sm" variant="danger" onClick={(e) => { e.stopPropagation(); onRemove(); }}>Remove</Button>
          <Button size="sm" variant="secondary" onClick={(e) => { e.stopPropagation(); setConfirming(false); }}>Cancel</Button>
        </div>
      </div>
    );
  }

  if (compact) {
    // Home's list: one line per thread — icon, title, then when (or a
    // scheduled run that is waiting on an approval).
    return (
      <div
        onClick={onClick}
        className="compact-card n-thread-row"
        data-testid={testId}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '8px 12px 8px 16px',
          cursor: 'pointer',
          ...rowState,
        }}
      >
        <Icon icon={isAutomated ? Timer : MemberIcon} size={15} tone="muted" />
        <span style={{
          flex: 1, minWidth: 0, fontSize: 'var(--fs-base)', color: 'var(--text)',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {isAutomated ? (thread.automated_task?.title || getThreadTitle(thread)) : (getThreadSummary(thread) || getThreadTitle(thread))}
        </span>
        {runWaiting ? (
          <span title="A run is waiting for your approval"><Badge tone="warn">Approval needed</Badge></span>
        ) : (
          <span style={{ flexShrink: 0, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{timeAgo(thread.created_at)}</span>
        )}
        {removeButton}
      </div>
    );
  }

  return (
    <div
      onClick={onClick}
      data-testid={testId}
      className="n-thread-row"
      style={{
        padding: '12px 12px 12px 16px',
        borderBottom: '1px solid var(--line)',
        cursor: 'pointer',
        ...rowState,
      }}
    >
      {/* Top row: what it used + when */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Icon icon={MemberIcon} size={14} tone="muted" />
        <span style={{
          flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--muted)',
        }}>
          {cardLabel(thread)}
        </span>
        {isAutomated && <Badge><Timer size={10} strokeWidth={2} aria-hidden /> Saved</Badge>}
        <span style={{ flexShrink: 0, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          {timeAgo(thread.created_at)}
        </span>
        {removeButton}
      </div>

      {/* Title */}
      <div style={{ marginTop: 2, fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>
        {getThreadTitle(thread)}
      </div>

      {/* Summary */}
      <div style={{
        marginTop: 2,
        fontSize: 'var(--fs-sm)',
        color: 'var(--text-soft)',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}>
        {isAutomated && thread.automated_task
          ? formatSchedule(thread.automated_task.schedule_type, thread.automated_task.schedule_config)
          : getThreadSummary(thread)
        }
      </div>

      {/* What needs someone: a scheduled run stopped at an approval, or the
          thread itself is waiting. */}
      {(runWaiting || badge) && (
        <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
          {runWaiting && <Badge tone="warn">Approval needed</Badge>}
          {badge && <Badge tone={badge.tone}>{badge.label}</Badge>}
        </div>
      )}
    </div>
  );
}
