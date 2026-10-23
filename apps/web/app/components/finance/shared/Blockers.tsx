'use client';

/**
 * What stops the next step, said plainly, each line optionally a link to
 * the place that fixes it (the reconciliation's blockers → its rows; the
 * export's refusals → the people without a pay code). Warnings sit under
 * them in the softer tone.
 */
import type { ReactNode } from 'react';
import { CircleAlert, TriangleAlert } from 'lucide-react';
import Icon from '../../ui/Icon';

export interface BlockerItem {
  text: ReactNode;
  /** Jump to the thing that fixes it. */
  onClick?: () => void;
  linkLabel?: string;
}

export type Blocker = string | BlockerItem;

function asItem(b: Blocker): BlockerItem {
  return typeof b === 'string' ? { text: b } : b;
}

function List({ items, tone }: { items: BlockerItem[]; tone: 'error' | 'warn' }) {
  const color = tone === 'error' ? 'var(--error)' : 'var(--warn)';
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
      {items.map((it, i) => (
        <li key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 'var(--fs-sm)', color }}>
          <Icon icon={tone === 'error' ? CircleAlert : TriangleAlert} size="dense" style={{ marginTop: 2 }} />
          <span style={{ flex: 1, minWidth: 0 }}>
            {it.text}
            {it.onClick && (
              <>
                {' '}
                <button type="button" onClick={it.onClick} style={{ background: 'none', border: 'none', padding: 0, font: 'inherit', color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline' }}>
                  {it.linkLabel ?? 'Show'}
                </button>
              </>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function Blockers({ blockers = [], warnings = [], title = 'Before the next step', okText, compact = false }: {
  blockers?: Blocker[];
  warnings?: Blocker[];
  title?: string;
  /** Shown when there is nothing blocking ("Ready to push"). */
  okText?: ReactNode;
  compact?: boolean;
}) {
  const b = blockers.map(asItem);
  const w = warnings.map(asItem);
  if (b.length === 0 && w.length === 0) {
    if (!okText) return null;
    return (
      <div role="status" style={{ padding: compact ? '6px 10px' : '8px 12px', borderRadius: 'var(--radius)', background: 'var(--ok-bg)', color: 'var(--ok)', fontSize: 'var(--fs-sm)' }}>
        {okText}
      </div>
    );
  }
  return (
    <div
      role={b.length ? 'alert' : 'status'}
      style={{
        padding: compact ? '8px 10px' : '10px 12px', borderRadius: 'var(--radius)',
        background: b.length ? 'var(--error-bg)' : 'var(--warn-bg)', border: '1px solid var(--line)',
      }}
    >
      {!compact && title && <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: b.length ? 'var(--error)' : 'var(--warn)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{title}</div>}
      {b.length > 0 && <List items={b} tone="error" />}
      {w.length > 0 && <div style={{ marginTop: b.length ? 8 : 0 }}><List items={w} tone="warn" /></div>}
    </div>
  );
}
