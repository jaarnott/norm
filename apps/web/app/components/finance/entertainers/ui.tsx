'use client';

/**
 * Presentational pieces the entertainer pages share: status chips, the
 * section card, labelled facts and fields, a money input, KPI tiles, the
 * inline write-error strip, a copy button. Inline styles on design tokens,
 * like the rest of Norm.
 */
import { useEffect, useId, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Check, Copy, RotateCcw, TriangleAlert } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import { CHECK_LABEL, CHECK_TONE, EMAIL_STATUS_TONE, LANE_SHORT, STATUS_LABEL, STATUS_TONE } from './logic';
import type { CheckStatus, EmailStatus, Lane, PerformanceStatus } from './types';

export function StatusChip({ status }: { status: PerformanceStatus }) {
  return <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>;
}

export function LaneBadge({ lane }: { lane: Lane | null | undefined }) {
  if (!lane) return <Badge tone="warn">No lane yet</Badge>;
  return <Badge tone={lane === 'invoice' || lane === 'exemption' ? 'info' : 'neutral'}>{LANE_SHORT[lane]}</Badge>;
}

export function CheckChip({ status }: { status: CheckStatus | null | undefined }) {
  if (!status) return <span style={{ color: 'var(--muted)' }}>—</span>;
  return (
    <Badge tone={CHECK_TONE[status]}>
      {status === 'matched' && <Icon icon={Check} size={12} />}
      {CHECK_LABEL[status]}
    </Badge>
  );
}

/** delivered green, bounced/failed red — the Emails tab and the Performers tab agree. */
export function EmailStatusBadge({ status }: { status: EmailStatus | null | undefined }) {
  if (!status) return <Badge tone="neutral">Unknown</Badge>;
  return <Badge tone={EMAIL_STATUS_TONE[status]}>{status.charAt(0).toUpperCase() + status.slice(1)}</Badge>;
}

/** A white card for one part of a page or dialog, with its own header row. */
export function Section({ title, badge, actions, children, style }: {
  title: ReactNode;
  badge?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  style?: CSSProperties;
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="n-card" style={{ padding: '14px 16px', minWidth: 0, ...style }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <h2 id={headingId} style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>{title}</h2>
        {badge}
        {actions && <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>{actions}</div>}
      </div>
      {children}
    </section>
  );
}

/** Read view: label over value, in a responsive grid of pairs. */
export function FactGrid({ children, min = 180 }: { children: ReactNode; min?: number }) {
  return (
    <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: `repeat(auto-fill, minmax(min(100%, ${min}px), 1fr))`, gap: '10px 18px' }}>
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
export function Field({ label, htmlFor, children, hint, error, wide = false }: {
  label: ReactNode;
  htmlFor?: string;
  children: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  wide?: boolean;
}) {
  return (
    <div style={{ minWidth: 0, gridColumn: wide ? '1 / -1' : undefined }}>
      <label className="n-label" htmlFor={htmlFor}>{label}</label>
      {children}
      {error ? (
        <div role="alert" style={{ marginTop: 4, fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--error)' }}>{error}</div>
      ) : hint ? (
        <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{hint}</div>
      ) : null}
    </div>
  );
}

export function FieldGrid({ children, min = 200 }: { children: ReactNode; min?: number }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fill, minmax(min(100%, ${min}px), 1fr))`, gap: '12px 16px' }}>
      {children}
    </div>
  );
}

export const inputStyle: CSSProperties = { width: '100%' };

/** A money input in dollars (the form converts to cents on save). */
export function MoneyInput({ id, value, onChange, placeholder = '0', ariaLabel, disabled, suffix }: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  ariaLabel?: string;
  disabled?: boolean;
  suffix?: string;
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
        style={{ ...inputStyle, paddingLeft: 22, paddingRight: suffix ? 44 : undefined }}
      />
      {suffix && <span aria-hidden="true" style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>{suffix}</span>}
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

/** A notice band inside a page or dialog. */
export function Notice({ tone, children, action }: { tone: 'ok' | 'warn' | 'info' | 'error' | 'neutral'; children: ReactNode; action?: ReactNode }) {
  const colours = {
    ok: ['var(--ok-bg)', 'var(--ok)'],
    warn: ['var(--warn-bg)', 'var(--warn)'],
    info: ['var(--info-bg)', 'var(--info)'],
    error: ['var(--error-bg)', 'var(--error)'],
    neutral: ['var(--surface-alt)', 'var(--text-soft)'],
  }[tone];
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: '10px 12px', borderRadius: 'var(--radius)', background: colours[0], color: colours[1], fontSize: 'var(--fs-sm)' }}
    >
      <div style={{ flex: '1 1 220px', minWidth: 0 }}>{children}</div>
      {action}
    </div>
  );
}

/** A quiet note under a control or a card. */
export function Hint({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', ...style }}>{children}</div>;
}

/** One KPI: a figure with its label. */
export function KpiTile({ label, value, tone = 'neutral', onClick, active }: {
  label: string;
  value: ReactNode;
  tone?: 'neutral' | 'ok' | 'warn' | 'error' | 'info';
  onClick?: () => void;
  active?: boolean;
}) {
  const colour = tone === 'neutral' ? 'var(--text)' : `var(--${tone})`;
  const body = (
    <>
      <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--muted)' }}>{label}</div>
      <div style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, color: colour, fontVariantNumeric: 'tabular-nums', lineHeight: 1.2, marginTop: 2 }}>{value}</div>
    </>
  );
  const style: CSSProperties = {
    flex: '1 1 140px', minWidth: 0, padding: '10px 14px', textAlign: 'left',
    border: `1px solid ${active ? 'var(--brand-soft)' : 'var(--line)'}`, background: active ? 'var(--accent-soft)' : 'var(--bg)',
    borderRadius: 'var(--radius-lg)', font: 'inherit', cursor: onClick ? 'pointer' : 'default',
  };
  if (onClick) {
    return <button type="button" onClick={onClick} aria-pressed={active} style={style}>{body}</button>;
  }
  return <div style={style}>{body}</div>;
}

export function KpiRow({ children }: { children: ReactNode }) {
  return <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>{children}</div>;
}

/** Two or three joined buttons choosing one view (Payments / Statement) —
 *  a view switch inside a tab, so not another row of tabs. */
export function Segmented({ label, value, onChange, options }: {
  label: string;
  value: string;
  onChange: (id: string) => void;
  options: { id: string; label: ReactNode }[];
}) {
  return (
    <div role="group" aria-label={label} style={{ display: 'inline-flex', flex: '0 0 auto', border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden', background: 'var(--surface-alt)' }}>
      {options.map((o, i) => {
        const on = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.id)}
            style={{
              padding: '5px 14px', border: 'none', borderLeft: i ? '1px solid var(--line)' : 'none', cursor: 'pointer', whiteSpace: 'nowrap',
              fontFamily: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: on ? 600 : 500,
              background: on ? 'var(--accent-soft)' : 'transparent', color: on ? 'var(--accent)' : 'var(--text)',
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** A pill filter (status, lane). */
export function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      style={{
        flex: '0 0 auto', padding: '5px 12px', borderRadius: 999, cursor: 'pointer', whiteSpace: 'nowrap',
        fontFamily: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: active ? 600 : 500,
        border: `1px solid ${active ? 'var(--brand-soft)' : 'var(--line)'}`,
        background: active ? 'var(--accent-soft)' : 'var(--surface-alt)',
        color: active ? 'var(--accent)' : 'var(--text)',
      }}
    >
      {children}
    </button>
  );
}

export function ChipRow({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div role="group" aria-label={label} className="no-scrollbar" style={{ display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 2 }}>
      {children}
    </div>
  );
}

export function CopyButton({ text, label = 'Copy link', size = 'sm' }: { text: string; label?: string; size?: 'xs' | 'sm' | 'md' }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);
  return (
    <Button
      size={size}
      icon={copied ? Check : Copy}
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => setCopied(true), () => setCopied(false));
      }}
    >
      {copied ? 'Copied' : label}
    </Button>
  );
}

/** A labelled on/off switch drawn as a checkbox row (keyboard and screen-reader plain). */
export function Toggle({ id, checked, onChange, children, disabled }: { id?: string; checked: boolean; onChange: (v: boolean) => void; children: ReactNode; disabled?: boolean }) {
  const auto = useId();
  const fieldId = id ?? auto;
  return (
    <label htmlFor={fieldId} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: disabled ? 'var(--muted)' : 'var(--text)', cursor: disabled ? 'default' : 'pointer', minHeight: 32 }}>
      <input id={fieldId} type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} style={{ width: 16, height: 16, margin: 0, accentColor: 'var(--accent)' }} />
      {children}
    </label>
  );
}

/** The dialog's action row. */
export function DialogActions({ children, left }: { children: ReactNode; left?: ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 18 }}>
      {left}
      <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, flexWrap: 'wrap' }}>{children}</div>
    </div>
  );
}

/** A phone-width card row for a list that is a table on desktop. */
export function RowCard({ onClick, children, ariaLabel }: { onClick?: () => void; children: ReactNode; ariaLabel?: string }) {
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      aria-label={ariaLabel}
      className="n-card"
      style={{ display: 'block', width: '100%', textAlign: 'left', padding: '12px 14px', font: 'inherit', color: 'inherit', cursor: onClick ? 'pointer' : 'default', minWidth: 0 }}
    >
      {children}
    </Tag>
  );
}

/** A table with the page's look; `onRow` makes every row a button. */
export function Table({ head, children, minWidth = 640 }: { head: ReactNode; children: ReactNode; minWidth?: number }) {
  return (
    <div className="n-card" style={{ overflowX: 'auto' }}>
      <table className="n-table" style={{ minWidth }}>
        <thead><tr>{head}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export const rowButtonStyle: CSSProperties = { cursor: 'pointer' };

/** Shown in a cell of a clickable row: the whole row is the target. */
export function RowButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button type="button" className="n-btn n-btn--link" onClick={onClick} style={{ fontSize: 'var(--fs-sm)' }}>
      {label}
    </button>
  );
}
