'use client';

import { useId, useRef } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';

export interface SubNavItem {
  id: string;
  label: ReactNode;
  testId?: string;
}

export interface SubNavGroup {
  label: string;
  items: SubNavItem[];
}

/**
 * A page's own section list, down its left side (Settings): small group
 * labels over menu rows. The open section is the selected tile — no side
 * bar, which read as heavy in a list this long. A group with no items is
 * left out.
 *
 * Keyboard: only the open section is in the Tab order; Up/Down/Home/End move
 * between sections and Enter or Space opens one — so moving through the list
 * doesn't load every section on the way.
 */
export default function SubNav({ groups, value, onChange, label }: {
  groups: SubNavGroup[];
  value: string;
  onChange: (id: string) => void;
  label: string;
}) {
  const navRef = useRef<HTMLElement>(null);
  const baseId = useId();
  const shown = groups.filter(g => g.items.length > 0);
  const ids = shown.flatMap(g => g.items.map(i => i.id));
  // If the open section isn't listed, the first one takes the Tab stop.
  const tabStop = ids.includes(value) ? value : ids[0];

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const buttons = Array.from(navRef.current?.querySelectorAll<HTMLButtonElement>('[data-subnav-id]') ?? []);
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    let next = -1;
    if (e.key === 'ArrowDown') next = at < 0 ? 0 : Math.min(at + 1, buttons.length - 1);
    else if (e.key === 'ArrowUp') next = at < 0 ? 0 : Math.max(at - 1, 0);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = buttons.length - 1;
    if (next < 0 || !buttons[next]) return;
    e.preventDefault();
    buttons[next].focus();
  };

  return (
    <nav ref={navRef} aria-label={label} onKeyDown={onKeyDown}>
      {shown.map((group, gi) => {
        const headingId = `${baseId}-${gi}`;
        return (
          <div key={group.label} style={{ marginTop: gi === 0 ? 0 : 18 }}>
            <div id={headingId} className="n-eyebrow" style={{ padding: '0 10px 6px' }}>{group.label}</div>
            <ul aria-labelledby={headingId} style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 1 }}>
              {group.items.map(item => {
                const active = item.id === value;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      className="n-row"
                      data-subnav-id={item.id}
                      data-testid={item.testId}
                      aria-current={active ? 'page' : undefined}
                      tabIndex={item.id === tabStop ? 0 : -1}
                      onClick={() => onChange(item.id)}
                      style={{
                        padding: '7px 10px',
                        borderRadius: 'var(--radius-sm)',
                        color: active ? 'var(--text)' : 'var(--text-soft)',
                      }}
                    >
                      {item.label}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}
