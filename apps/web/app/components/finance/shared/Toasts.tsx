'use client';

/**
 * Finance pages — a small toast: one line at the bottom of the page that
 * goes away on its own. Used for a write that failed outside a dialog (a
 * row's delete, a sign-off, a refresh) and for "Saved" confirmations.
 *
 * `toast()` can be called from anywhere (an API hook, an event handler);
 * `<ToastHost />` is mounted once by PageShell. Module state, no context, so
 * the hook and the host need no shared ancestor.
 *
 * One host draws: a workspace page (Payroll, Entertainers, Budgets) mounts
 * one, and its tab bodies use PageShell `embedded`, which mounts none. Should
 * a second host mount anyway, only the first one mounted renders, so a toast
 * is never shown or announced twice.
 */
import { useEffect, useId, useSyncExternalStore } from 'react';
import { CircleCheck, CircleAlert, TriangleAlert } from 'lucide-react';
import Icon from '../../ui/Icon';

export type ToastTone = 'ok' | 'error' | 'warn';

export interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
}

type Listener = (toasts: Toast[]) => void;

let seq = 0;
let current: Toast[] = [];
const listeners = new Set<Listener>();

function emit(): void {
  for (const l of listeners) l(current);
}

/** Show a toast; it clears itself after `ms` (errors stay a little longer). */
export function toast(message: string, tone: ToastTone = 'error', ms?: number): void {
  const id = ++seq;
  current = [...current, { id, message, tone }];
  emit();
  const ttl = ms ?? (tone === 'error' ? 8000 : 4000);
  setTimeout(() => dismiss(id), ttl);
}

export function dismiss(id: number): void {
  if (!current.some((t) => t.id === id)) return;
  current = current.filter((t) => t.id !== id);
  emit();
}

const ICONS = { ok: CircleCheck, error: CircleAlert, warn: TriangleAlert } as const;

const EMPTY: Toast[] = [];

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

// The mounted hosts, oldest first; only hosts[0] draws.
let hosts: string[] = [];
const hostListeners = new Set<() => void>();

function subscribeHosts(listener: () => void): () => void {
  hostListeners.add(listener);
  return () => { hostListeners.delete(listener); };
}

function setHosts(next: string[]): void {
  hosts = next;
  for (const l of hostListeners) l();
}

export function ToastHost() {
  const id = useId();
  useEffect(() => {
    setHosts([...hosts, id]);
    return () => setHosts(hosts.filter((h) => h !== id));
  }, [id]);
  const drawing = useSyncExternalStore(subscribeHosts, () => hosts[0] === id, () => false);
  const toasts = useSyncExternalStore(subscribe, () => current, () => EMPTY);
  if (!drawing || toasts.length === 0) return null;
  return (
    <div
      aria-live="polite"
      style={{
        position: 'fixed', left: 16, right: 16, bottom: 'calc(16px + env(safe-area-inset-bottom))', zIndex: 1100,
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, pointerEvents: 'none',
      }}
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role={t.tone === 'error' ? 'alert' : 'status'}
          onClick={() => dismiss(t.id)}
          style={{
            pointerEvents: 'auto', cursor: 'pointer', maxWidth: 560, width: '100%', boxSizing: 'border-box',
            display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 14px',
            borderRadius: 'var(--radius)', border: '1px solid var(--line)',
            background: t.tone === 'ok' ? 'var(--ok-bg)' : t.tone === 'warn' ? 'var(--warn-bg)' : 'var(--error-bg)',
            color: t.tone === 'ok' ? 'var(--ok)' : t.tone === 'warn' ? 'var(--warn)' : 'var(--error)',
            fontSize: 'var(--fs-sm)', boxShadow: '0 8px 24px rgba(26, 26, 26, 0.12)',
          }}
        >
          <Icon icon={ICONS[t.tone]} size="dense" style={{ marginTop: 2 }} />
          <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{t.message}</span>
        </div>
      ))}
    </div>
  );
}
