'use client';

import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { X } from 'lucide-react';
import Button from './Button';
import IconButton from './IconButton';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A modal over a dimmed page. It takes focus when it opens and hands it back
 * to whatever had it when it closes; Tab stays inside it; Escape or a click
 * on the dimmed page closes it.
 */
export default function Dialog({ title, width, onClose, closeButton = false, describedBy, children }: {
  title: string;
  width: number;
  onClose: () => void;
  closeButton?: boolean;
  /** Id of the element that says what the dialog is about (a confirmation's body). */
  describedBy?: string;
  children: ReactNode;
}) {
  const titleId = useId();
  const boxRef = useRef<HTMLDivElement>(null);
  // The latest onClose, so the Escape listener needn't be re-added each render.
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });
  // A click closes only when it both started and ended on the dimmed page —
  // not a text selection dragged out of the box.
  const pressedOutside = useRef(false);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    boxRef.current?.focus();
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); closeRef.current(); }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      // The opener may be gone (the row it sat in was just deleted).
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  const trapTab = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab' || !boxRef.current) return;
    const items = Array.from(boxRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) { e.preventDefault(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    const at = document.activeElement;
    if (e.shiftKey && (at === first || at === boxRef.current)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus(); }
  };

  return (
    <div
      onMouseDown={e => { pressedOutside.current = e.target === e.currentTarget; }}
      onClick={e => { if (pressedOutside.current && e.target === e.currentTarget) onClose(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000, padding: 16,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        backgroundColor: 'rgba(26, 26, 26, 0.35)',
      }}
    >
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={describedBy}
        tabIndex={-1}
        onKeyDown={trapTab}
        // Clicks inside stay inside: the dialog is often opened from a row
        // that toggles on click.
        onClick={e => e.stopPropagation()}
        style={{
          width, maxWidth: '100%', maxHeight: '80vh', overflowY: 'auto', boxSizing: 'border-box',
          padding: 24, borderRadius: 'var(--radius-lg)', backgroundColor: 'var(--bg)',
          boxShadow: '0 12px 40px rgba(26, 26, 26, 0.18)', outline: 'none', lineHeight: 1.45,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
          <h3 id={titleId} style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h3>
          {closeButton && <IconButton icon={X} label="Close" onClick={onClose} style={{ margin: '-6px -8px -6px 0' }} />}
        </div>
        {children}
      </div>
    </div>
  );
}

/**
 * "Are you sure?" for an action that can't be taken back. While the action
 * runs the buttons wait and the dialog can't be dismissed; if it fails, the
 * reason shows inside the dialog and it stays open to try again.
 *
 * `onConfirm` performs the action: throw an Error whose message says what
 * went wrong to keep the dialog open; resolve and the dialog closes.
 */
export function ConfirmDialog({ title, children, confirmLabel, busyLabel, cancelLabel = 'Cancel', danger = false, onConfirm, onClose }: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  /** Shown on the confirm button while the action runs ("Deleting…"). */
  busyLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const bodyId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const actionsRef = useRef<HTMLDivElement>(null);
  // The confirm button was disabled while the action ran, which drops focus;
  // after a failure, put it back there for another try.
  useEffect(() => {
    if (error) actionsRef.current?.querySelector<HTMLButtonElement>('button:last-of-type')?.focus();
  }, [error]);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : 'Something went wrong. Please try again.');
      setBusy(false);
      return;
    }
    onClose();
  };

  // Nothing closes it mid-flight: the outcome has to be seen.
  const dismiss = () => { if (!busy) onClose(); };

  return (
    <Dialog title={title} width={420} onClose={dismiss} describedBy={bodyId}>
      <div id={bodyId} style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>{children}</div>
      {error && (
        <div role="alert" style={{
          marginTop: 12, padding: '8px 12px', fontSize: 'var(--fs-sm)',
          color: 'var(--error)', backgroundColor: 'var(--error-bg)', borderRadius: 'var(--radius)',
        }}>{error}</div>
      )}
      <div ref={actionsRef} style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 20 }}>
        <Button onClick={dismiss} disabled={busy}>{cancelLabel}</Button>
        <Button variant={danger ? 'danger' : 'primary'} onClick={confirm} disabled={busy} aria-busy={busy || undefined}>
          {busy ? (busyLabel ?? `${confirmLabel}…`) : confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}
