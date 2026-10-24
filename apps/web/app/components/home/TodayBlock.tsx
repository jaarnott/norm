'use client';

import { ChevronRight, GraduationCap, ClipboardList, type LucideIcon } from 'lucide-react';
import Icon from '../ui/Icon';
import type { TodayItem } from '../../types';

/** The icon for a kind of item; anything new reads as a checklist line. */
function iconFor(kind: string): LucideIcon {
  return kind === 'training' ? GraduationCap : ClipboardList;
}

/** The item's due date as a Date, or null when it has none it can read. */
function dueDate(item: Pick<TodayItem, 'due_at'>): Date | null {
  if (!item.due_at) return null;
  const due = new Date(item.due_at.length === 10 ? `${item.due_at}T12:00:00` : item.due_at);
  return Number.isNaN(due.getTime()) ? null : due;
}

/** Is the item late? Training says so with `overdue`; a provider's item
 *  (services/today.py) says `error`, which the block shows the same way:
 *  an accent icon, and "Overdue" when it has a due date. */
export function isOverdue(item: Pick<TodayItem, 'due_at' | 'tone'>): boolean {
  return item.tone === 'overdue' || item.tone === 'error';
}

/** "Due today", "Due Fri 25 Oct" or "Overdue", from the item's due date. */
export function dueLabel(item: Pick<TodayItem, 'due_at' | 'tone'>, now: Date = new Date()): string | null {
  if (!item.due_at) return null;
  if (isOverdue(item)) return 'Overdue';
  const due = dueDate(item);
  if (!due) return null;
  const sameDay = due.getFullYear() === now.getFullYear() && due.getMonth() === now.getMonth() && due.getDate() === now.getDate();
  if (sameDay) return 'Due today';
  return `Due ${due.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}`;
}

/** The items in groups by venue, in first-seen order, for a manager whose
 *  items come from more than one venue. One venue (or none named) is one
 *  unnamed group, so nothing changes for a person at a single venue. */
export function groupByVenue(items: TodayItem[]): { venueId: string | null; items: TodayItem[] }[] {
  const groups = new Map<string | null, TodayItem[]>();
  for (const item of items) {
    const key = item.venue_id || null;
    const list = groups.get(key);
    if (list) list.push(item); else groups.set(key, [item]);
  }
  const named = [...groups.keys()].filter(k => k !== null);
  if (named.length < 2) return [{ venueId: null, items }];
  return [...groups.entries()].map(([venueId, list]) => ({ venueId, items: list }));
}

/**
 * Home's Today block: what is due for this person today, gathered from the
 * team apps (GET /api/me/today). Rendered only when there is something; a
 * row opens the app it came from.
 */
export default function TodayBlock({ items, title, onOpen, venueNames }: {
  items: TodayItem[];
  /** "Today" for venue staff; "Today at your venues" for a manager. */
  title?: string;
  onOpen?: (item: TodayItem) => void;
  /** Venue id -> name, for the group headings when items come from more
   *  than one venue. Without it the groups have no heading. */
  venueNames?: Record<string, string>;
}) {
  if (!items.length) return null;
  const groups = groupByVenue(items);
  return (
    <section aria-label={title ?? 'Today'} data-testid="today-block" className="n-card n-today">
      {title && (
        <div style={{ padding: '8px 12px 4px', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>{title}</div>
      )}
      {groups.map((group, g) => {
        const heading = group.venueId ? venueNames?.[group.venueId] : undefined;
        return (
          <div key={group.venueId ?? `group-${g}`}>
            {heading && (
              <div data-testid="today-venue" style={{ padding: '6px 12px 2px', fontSize: 'var(--fs-xs)', fontWeight: 600, letterSpacing: '0.02em', textTransform: 'uppercase', color: 'var(--muted)' }}>{heading}</div>
            )}
            {group.items.map((item, i) => {
              const due = dueLabel(item);
              const overdue = isOverdue(item);
              const soon = item.tone === 'due' || item.tone === 'warn';
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
                    <span className={`n-badge ${overdue ? 'n-badge--error' : soon ? 'n-badge--accent' : ''}`}>{due}</span>
                  )}
                  <Icon icon={ChevronRight} size={16} tone="muted" />
                </button>
              );
            })}
          </div>
        );
      })}
    </section>
  );
}
