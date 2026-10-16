'use client';

/** Small shared pieces of the Inbox and proposal screens. */
import type { CSSProperties, ReactNode } from 'react';
import { CircleCheck, Info, Lock, TriangleAlert } from 'lucide-react';
import { FnApiError } from '../shared/api';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import { openMailboxSettings } from './nav';

export type NoticeTone = 'ok' | 'info' | 'warn' | 'error';

const NOTICE_ICON = { ok: CircleCheck, info: Info, warn: TriangleAlert, error: TriangleAlert } as const;

/** A tinted message box: text on its own tint, an optional action at the end. */
export function Notice({ tone, title, children, action }: { tone: NoticeTone; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        flexWrap: 'wrap',
        padding: '9px 12px',
        borderRadius: 'var(--radius)',
        background: `var(--${tone}-bg)`,
        color: `var(--${tone})`,
        fontSize: 'var(--fs-sm)',
        lineHeight: 1.45,
      }}
    >
      <Icon icon={NOTICE_ICON[tone]} size={16} style={{ marginTop: 1 }} />
      <div style={{ flex: '1 1 220px', minWidth: 0 }}>
        {title && <div style={{ fontWeight: 600 }}>{title}</div>}
        {children}
      </div>
      {action && <div style={{ flex: '0 0 auto' }}>{action}</div>}
    </div>
  );
}

/** The mailbox can't be used (placeholder, dead grant): say so and point at
 *  Settings → Mailboxes, where it is connected. */
export function MailboxNotice({ message }: { message: string }) {
  return (
    <Notice
      tone="warn"
      title="Mailbox not connected"
      action={
        <Button size="sm" onClick={openMailboxSettings}>
          Connect in Settings
        </Button>
      }
    >
      {message}
    </Notice>
  );
}

/** The shadow-mode hint next to disabled write controls. */
export function ReadOnlyHint({ children = 'Read-only while Orbit is live.' }: { children?: ReactNode }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
      <Icon icon={Lock} size={14} />
      {children}
    </span>
  );
}

/** A refusal as a sentence for the person (the API writes them for people). */
export function errorText(err: unknown): string {
  if (err instanceof FnApiError) {
    if (err.notLive) return 'Read-only while Orbit is live — this can’t be changed in Norm yet.';
    return err.message;
  }
  return err instanceof Error && err.message ? err.message : 'Something went wrong. Please try again.';
}

export const fieldLabel: CSSProperties = {
  fontSize: 'var(--fs-sm)',
  fontWeight: 500,
  color: 'var(--text-soft)',
};
