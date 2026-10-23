'use client';

import { ChevronRight, GraduationCap, ClipboardList, type LucideIcon } from 'lucide-react';
import Icon from '../ui/Icon';
import type { TodayItem } from '../../types';

/** The icon for a kind of item; anything new reads as a checklist line. */
function iconFor(kind: string): LucideIcon {
  return kind === 'training' ? GraduationCap : ClipboardList;
}

/** "Due today", "Due Fri 25 Oct" or "Overdue", from the item's due date. */
export function dueLabel(item: Pick<TodayItem, 'due_at' | 'tone'>, now: Date = new Date()): string | null {
  if (!item.due_at) return null;
  if (item.tone === 'overdue') return 'Overdue';
  const due = new Date(item.due_at.length === 10 ? `${item.due_at}T12:00:00` : item.due_at);
  if (Number.isNaN(due.getTime())) return null;
  const sameDay = due.getFullYear() === now.getFullYear() && due.getMonth() === now.getMonth() && due.getDate() === now.getDate();
  if (sameDay) return 'Due today';
  return `Due ${due.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}`;
}

/**
 * Home's Today block: what is due for this person today, gathered from the
 * team apps (GET /api/me/today). Rendered only when there is something; a
 * row opens the app it came from.
 */
export default function TodayBlock({ items, title, onOpen }: {
  items: TodayItem[];
  /** "Today" for venue staff; "Today at your venues" for a manager. */
  title?: string;
  onOpen?: (item: TodayItem) => void;
}) {
  if (!items.length) return null;
  return (
    <section aria-label={title ?? 'Today'} data-testid="today-block" className="n-card n-today">
      {title && (
        <div style={{ padding: '8px 12px 4px', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>{title}</div>
      )}
      {items.map((item, i) => {
        const due = dueLabel(item);
        const overdue = item.tone === 'overdue';
        return (
          <button key={`${item.kind}:${item.title}:${i}`} type="button" className="n-today-row" onClick={() => onOpen?.(item)}>
            <Icon icon={iconFor(item.kind)} size={18} tone={overdue ? 'accent' : 'muted'} />
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: 'block', fontSize: 'var(--fs-base)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.title}</span>
              {item.detail && (
                <span style={{ display: 'block', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.detail}</span>
              )}
            </span>
            {due && (
              <span className={`n-badge ${overdue ? 'n-badge--error' : item.tone === 'due' ? 'n-badge--accent' : ''}`}>{due}</span>
            )}
            <Icon icon={ChevronRight} size={16} tone="muted" />
          </button>
        );
      })}
    </section>
  );
}
