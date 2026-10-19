'use client';

import { useEffect, useRef, useState } from 'react';
import { switchOrganization } from '../../lib/api';

export interface OrgChoice {
  id: string;
  name: string | null;
}

interface Props {
  organizations?: OrgChoice[];
  activeId?: string | null;
  /** 'rail' is the icon-only sidebar; 'row' is the mobile footer. */
  variant?: 'rail' | 'row';
}

function initials(name: string | null): string {
  const words = (name || '?').trim().split(/\s+/);
  return ((words[0]?.[0] || '?') + (words[1]?.[0] || '')).toUpperCase();
}

/**
 * Switch the organisation you are acting in. Shown only to someone who belongs
 * to two or more — everyone else never sees it. Choosing one reloads the app,
 * because every list on the page was fetched for the old organisation.
 */
export default function OrgSwitcher({ organizations, activeId, variant = 'rail' }: Props) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (btn.current && !btn.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  if (!organizations || organizations.length < 2) return null;
  const active = organizations.find(o => o.id === activeId) || organizations[0];

  const toggle = () => {
    const r = btn.current?.getBoundingClientRect();
    if (r) setPos({ left: r.right + 8, bottom: Math.max(8, window.innerHeight - r.bottom) });
    setOpen(o => !o);
  };

  return (
    <>
      <button
        ref={btn}
        type="button"
        data-testid="org-switcher"
        aria-label={`Organisation: ${active.name}. Switch organisation`}
        title={`${active.name} — switch organisation`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
        style={{
          width: variant === 'rail' ? 34 : 'auto',
          height: variant === 'rail' ? 26 : 36,
          padding: variant === 'rail' ? 0 : '0 10px',
          borderRadius: 8,
          border: '1px solid var(--line)',
          background: 'var(--surface, transparent)',
          color: 'var(--text-soft)',
          fontSize: 'var(--fs-sm)',
          fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        {variant === 'rail' ? initials(active.name) : active.name}
      </button>
      {open && pos && (
        <div
          role="menu"
          style={{
            position: 'fixed', left: variant === 'rail' ? pos.left : 12, bottom: pos.bottom, zIndex: 1000,
            minWidth: 200, padding: 6, borderRadius: 10, border: '1px solid var(--line)',
            background: 'var(--canvas)', boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
          }}
        >
          <div style={{ padding: '4px 8px 6px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Organisation</div>
          {organizations.map(o => (
            <button
              key={o.id}
              type="button"
              role="menuitemradio"
              aria-checked={o.id === active.id}
              onClick={() => (o.id === active.id ? setOpen(false) : switchOrganization(o.id))}
              style={{
                display: 'block', width: '100%', textAlign: 'left', padding: '8px 10px', borderRadius: 6,
                border: 'none', cursor: 'pointer', fontSize: 'var(--fs-base)',
                background: o.id === active.id ? 'var(--selected)' : 'transparent', color: 'var(--text)',
                fontWeight: o.id === active.id ? 600 : 400,
              }}
            >
              {o.name}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
