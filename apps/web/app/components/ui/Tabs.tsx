'use client';

import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';

export interface TabItem {
  id: string;
  label: ReactNode;
  testId?: string;
}

/** 'divider' draws a thin rule between groups of tabs (Settings uses it). */
export type TabEntry = TabItem | 'divider';

/**
 * Underline tabs that stay on one line and scroll sideways when they don't
 * fit — they never wrap a label onto two lines. Keyboard: only the selected
 * tab is in the Tab order; Left/Right/Home/End move between tabs and select
 * them (the WAI-ARIA tabs pattern, automatic activation).
 *
 * The selected tab is kept in view: on mount and whenever `value` changes the
 * strip scrolls sideways to show it, so a deep link to the ninth tab on a
 * phone doesn't open on a tab hidden off the edge. It sets the strip's
 * scrollLeft rather than calling scrollIntoView, which can also scroll the
 * page vertically.
 */
export default function Tabs({ items, value, onChange, label }: { items: TabEntry[]; value: string; onChange: (id: string) => void; label: string }) {
  const listRef = useRef<HTMLDivElement>(null);
  const tabs = items.filter((t): t is TabItem => t !== 'divider');

  useEffect(() => {
    const list = listRef.current;
    const tab = list?.querySelector<HTMLElement>(`[data-tab-id="${CSS.escape(value)}"]`);
    if (!list || !tab) return;
    // The tab's position along the strip's scrolled content.
    const left = tab.getBoundingClientRect().left - list.getBoundingClientRect().left + list.scrollLeft;
    const right = left + tab.offsetWidth;
    if (left < list.scrollLeft) list.scrollLeft = Math.max(0, left - 16);
    else if (right > list.scrollLeft + list.clientWidth) list.scrollLeft = right - list.clientWidth + 16;
  }, [value]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const at = tabs.findIndex((t) => t.id === value);
    let next = -1;
    if (e.key === 'ArrowRight') next = (at + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') next = (at - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = tabs.length - 1;
    if (next < 0 || !tabs[next]) return;
    e.preventDefault();
    onChange(tabs[next].id);
    listRef.current?.querySelector<HTMLButtonElement>(`[data-tab-id="${CSS.escape(tabs[next].id)}"]`)?.focus();
  };

  return (
    <div ref={listRef} role="tablist" aria-label={label} className="n-tabs" onKeyDown={onKeyDown}>
      {items.map((t, i) =>
        t === 'divider' ? (
          <span key={`divider-${i}`} className="n-tabs-divider" aria-hidden="true" />
        ) : (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={t.id === value}
            tabIndex={t.id === value ? 0 : -1}
            data-tab-id={t.id}
            data-testid={t.testId}
            className="n-tab"
            onClick={() => onChange(t.id)}
          >
            {t.label}
          </button>
        ),
      )}
    </div>
  );
}
