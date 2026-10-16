'use client';

/**
 * Presentational pieces the Events views share: status badges, the section
 * card, labelled fields, money inputs, a small popover menu, and the inline
 * write-error strip. Inline styles on design tokens, like the rest of Norm.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { CalendarCheck, CircleCheck, CircleX, PencilLine, RotateCcw, Send, TriangleAlert } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import { EDITED_LABEL, STATUS_LABEL, STATUS_TONE } from './logic';
import type { EventStatus } from './types';

/** One icon per status, so the calendar never relies on colour alone. */
export const STATUS_ICON: Record<EventStatus, LucideIcon> = {
  booked: CalendarCheck,
  sent_to_client: Send,
  in_progress: PencilLine,
  confirmed: CircleCheck,
  cancelled: CircleX,
};

/** Status colours as token pairs (text on its own tint, ≥5.2:1). */
export const STATUS_COLOURS: Record<EventStatus, { fg: string; bg: string }> = {
  booked: { fg: 'var(--text-soft)', bg: 'var(--line-soft)' },
  sent_to_client: { fg: 'var(--info)', bg: 'var(--info-bg)' },
  in_progress: { fg: 'var(--warn)', bg: 'var(--warn-bg)' },
  confirmed: { fg: 'var(--ok)', bg: 'var(--ok-bg)' },
  cancelled: { fg: 'var(--error)', bg: 'var(--error-bg)' },
};

export function StatusBadge({ status }: { status: EventStatus }) {
  return <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>;
}

export function EditedFlag({ compact = false }: { compact?: boolean }) {
  return (
    <Badge tone="accent" title="The client changed something after the event was confirmed">
      <Icon icon={TriangleAlert} size={12} />
      {compact ? 'Edited' : EDITED_LABEL}
    </Badge>
  );
}

export const READ_ONLY_HINT = 'Read-only while Orbit is live';

/** A white card for one part of the event page, with its own header row. */
export function SectionCard({ id, title, badge, actions, children, footer }: {
  id?: string;
  title: string;
  badge?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const headingId = useId();
  return (
    <section id={id} aria-labelledby={headingId} className="n-card" style={{ padding: '14px 16px', minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <h2 id={headingId} style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, color: 'var(--text)' }}>{title}</h2>
        {badge}
        {actions && <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>{actions}</div>}
      </div>
      {children}
      {footer && <div style={{ marginTop: 14, display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>{footer}</div>}
    </section>
  );
}

/** Read view: label over value, in a responsive grid of pairs. */
export function FactGrid({ children }: { children: ReactNode }) {
  return (
    <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 200px), 1fr))', gap: '12px 20px' }}>
      {children}
    </dl>
  );
}

export function Fact({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  const empty = children === null || children === undefined || children === '' || children === false;
  return (
    <div style={{ minWidth: 0, gridColumn: wide ? '1 / -1' : undefined }}>
      <dt style={{ fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--muted)', marginBottom: 2 }}>{label}</dt>
      <dd style={{ margin: 0, fontSize: 'var(--fs-base)', color: empty ? 'var(--muted)' : 'var(--text)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {empty ? '—' : children}
      </dd>
    </div>
  );
}

/** Edit view: a labelled control. */
export function Field({ label, htmlFor, children, hint, wide = false }: {
  label: string;
  htmlFor?: string;
  children: ReactNode;
  hint?: ReactNode;
  wide?: boolean;
}) {
  return (
    <div style={{ minWidth: 0, gridColumn: wide ? '1 / -1' : undefined }}>
      <label className="n-label" htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{hint}</div>}
    </div>
  );
}

export function FieldGrid({ children }: { children: ReactNode }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 210px), 1fr))', gap: '12px 16px' }}>
      {children}
    </div>
  );
}

export const inputStyle: CSSProperties = { width: '100%' };

/** A money input in dollars (the form converts to cents on save). */
export function MoneyInput({ id, value, onChange, placeholder = '0', ariaLabel, disabled }: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  ariaLabel?: string;
  disabled?: boolean;
}) {
  return (
    <span style={{ position: 'relative', display: 'block' }}>
      <span aria-hidden="true" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)', fontSize: 'var(--fs-base)' }}>$</span>
      <input
        id={id}
        className="n-input"
        inputMode="decimal"
        value={value}
        placeholder={placeholder}
        aria-label={ariaLabel}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        style={{ ...inputStyle, paddingLeft: 22 }}
      />
    </span>
  );
}

/** A failed write, said plainly, with Reload when someone else saved first. */
export function WriteError({ message, stale, onReload }: { message: string | null; stale?: boolean; onReload?: () => void }) {
  if (!message) return null;
  return (
    <div role="alert" style={{
      display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginTop: 12, padding: '8px 12px',
      borderRadius: 'var(--radius)', background: 'var(--error-bg)', color: 'var(--error)', fontSize: 'var(--fs-sm)',
    }}>
      <Icon icon={TriangleAlert} size={14} />
      <span style={{ flex: '1 1 200px' }}>{message}</span>
      {stale && onReload && <Button size="sm" icon={RotateCcw} onClick={onReload}>Reload</Button>}
    </div>
  );
}

/** A quiet note under a control or a card ("Read-only while Orbit is live"). */
export function Hint({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', ...style }}>{children}</div>;
}

/** A button that opens a short list of choices (Print ▾). Closes on choice,
 *  Escape or a click elsewhere. */
export function MenuButton({ label, icon, items, align = 'right', disabled }: {
  label: string;
  icon?: LucideIcon;
  items: { id: string; label: string; onSelect: () => void }[];
  align?: 'left' | 'right';
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={boxRef} style={{ position: 'relative', display: 'inline-flex' }}>
      <Button icon={icon} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined} disabled={disabled} onClick={() => setOpen((o) => !o)}>
        {label}
      </Button>
      {open && (
        <div
          id={menuId}
          role="menu"
          className="n-card"
          style={{
            position: 'absolute', top: 'calc(100% + 4px)', [align]: 0, zIndex: 30, minWidth: 190, padding: 4,
            boxShadow: '0 8px 24px rgba(26, 26, 26, 0.12)',
          }}
        >
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              className="n-option"
              style={{ borderRadius: 'var(--radius-sm)', fontSize: 'var(--fs-base)', padding: '8px 10px' }}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Rows of a small editable list (security, entertainment, run sheet). */
export const listRowStyle: CSSProperties = {
  display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap', padding: '8px 0', borderBottom: '1px solid var(--line-soft)',
};
