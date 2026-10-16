'use client';

/**
 * Small presentational pieces the Dashboard, Enquiries, Contacts and Reports
 * pages share: the page header switch, sections, stat tiles, form fields, a
 * form dialog that keeps its error inside, and the error wording for the
 * Functions API's refusals.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { LoaderCircle, TrendingDown, TrendingUp, Minus } from 'lucide-react';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import Icon from '../../ui/Icon';
import PageHeader from '../../ui/PageHeader';
import { fnGet, FnApiError } from '../shared/api';

export const READ_ONLY_HINT = 'Read-only while Orbit is live';

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export interface ActionError {
  message: string;
  /** Someone else changed it (or its facts moved on): offer a reload. */
  conflict: boolean;
}

/** The API's own words for a refusal (they are written for people). */
export function toActionError(e: unknown): ActionError {
  if (e instanceof FnApiError) {
    return { message: e.message || 'Something went wrong. Please try again.', conflict: e.status === 409 && e.code === 'conflict' };
  }
  if (e instanceof Error && e.message) return { message: e.message, conflict: false };
  return { message: 'Something went wrong. Please try again.', conflict: false };
}

/** Busy flag + error for a page's write actions: `run('approve', () => …)`
 *  resolves to the result, or undefined when it failed (the error is set). */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<ActionError | null>(null);
  const run = useCallback(async <T,>(key: string, fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(key);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(toActionError(e));
      return undefined;
    } finally {
      setBusy(null);
    }
  }, []);
  return { busy, error, setError, run };
}

export interface FnQuery<T> {
  /** The latest data — the last good answer stays while the next one loads. */
  data: T | null;
  /** The current request failed (cleared while a new one runs). */
  error: string | null;
  /** A request for the current path + params hasn't answered yet. */
  loading: boolean;
  /** The failed request's HTTP status (404 = missing or not yours). */
  status: number | null;
  retry: () => void;
}

/**
 * GET a Functions endpoint and re-GET whenever `path`, `params` or `refresh`
 * change (`path` null = not yet). Loading is derived from which request the
 * last answer belongs to, so nothing is set synchronously in an effect.
 */
export function useFnQuery<T>(path: string | null, params: Record<string, unknown> = {}, refresh = 0): FnQuery<T> {
  const [tick, setTick] = useState(0);
  const request = path ? JSON.stringify([path, params]) : null;
  const key = request ? `${request}#${refresh}#${tick}` : null;
  const [result, setResult] = useState<{ key: string; data: T | null; error: string | null; status: number | null } | null>(null);
  useEffect(() => {
    if (!request || !key) return;
    const [p, q] = JSON.parse(request) as [string, Record<string, unknown>];
    let alive = true;
    fnGet<T>(p, q)
      .then((data) => { if (alive) setResult({ key, data, error: null, status: null }); })
      .catch((e: unknown) => {
        if (!alive) return;
        const error = e instanceof Error ? e.message : String(e);
        const status = e instanceof FnApiError ? e.status : null;
        setResult((prev) => ({ key, data: prev?.data ?? null, error, status }));
      });
    return () => { alive = false; };
  }, [request, key]);
  const retry = () => setTick((t) => t + 1);
  const settled = !!key && result?.key === key;
  return {
    data: result?.data ?? null,
    error: settled ? result?.error ?? null : null,
    loading: !!key && !settled,
    status: settled ? result?.status ?? null : null,
    retry,
  };
}

/** An inline refusal under the thing that failed, with Reload on a conflict. */
export function ErrorNote({ error, onReload, onDismiss }: { error: ActionError | null; onReload?: () => void; onDismiss?: () => void }) {
  if (!error) return null;
  return (
    <div role="alert" style={alertStyle}>
      <span style={{ flex: 1, minWidth: 0 }}>{error.message}</span>
      {error.conflict && onReload && <Button size="sm" onClick={onReload}>Reload</Button>}
      {onDismiss && <Button size="sm" variant="quiet" onClick={onDismiss}>Dismiss</Button>}
    </div>
  );
}

const alertStyle: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
  marginTop: 10, padding: '8px 12px', fontSize: 'var(--fs-sm)',
  color: 'var(--error)', background: 'var(--error-bg)', borderRadius: 'var(--radius)',
};

/** A one-line success note ("Booked — open the event"). */
export function DoneNote({ children }: { children: ReactNode }) {
  return (
    <div role="status" style={{ ...alertStyle, color: 'var(--ok)', background: 'var(--ok-bg)' }}>{children}</div>
  );
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

/** PageHeader on a page; a compact title row when the component sits in a
 *  conversation (no `persistVenue`). */
export function FnHeader({ page, title, meta, back, status, actions, children }: {
  page: boolean;
  title: ReactNode;
  meta?: ReactNode;
  back?: { label: string; onClick: () => void };
  status?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  if (page) return <PageHeader title={title} meta={meta} back={back} status={status} actions={actions}>{children}</PageHeader>;
  return (
    <div style={{ marginBottom: 12 }}>
      {back && <Button variant="link" size="sm" onClick={back.onClick}>{back.label}</Button>}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginTop: back ? 6 : 0 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{title}</span>
            {status}
          </div>
          {meta && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</div>}
        </div>
        {actions && <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>{actions}</div>}
      </div>
      {children && <div style={{ marginTop: 10 }}>{children}</div>}
    </div>
  );
}

/** `[ref, width]` — the element's width as it changes (the page pane is
 *  narrower than the viewport when the menu panel is open). */
export function useWidth(): [(el: HTMLDivElement | null) => (() => void) | void, number] {
  const [width, setWidth] = useState(0);
  const ref = useCallback((el: HTMLDivElement | null) => {
    if (!el || typeof ResizeObserver === 'undefined') return;
    setWidth(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/** A white card with a title row (title, a muted meta, actions on the right). */
export function Section({ title, meta, actions, children, padded = true, testId }: {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  padded?: boolean;
  testId?: string;
}) {
  return (
    <section className="n-card" data-testid={testId} style={{ minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', padding: '12px 16px', borderBottom: children ? '1px solid var(--line-soft)' : undefined }}>
        <div style={{ minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{title}</h2>
          {meta && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</span>}
        </div>
        {actions && <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>{actions}</div>}
      </div>
      {children && <div style={padded ? { padding: '12px 16px' } : undefined}>{children}</div>}
    </section>
  );
}

/** Label / value pairs in a responsive grid. */
export function Fields({ items, min = 160 }: { items: { label: string; value: ReactNode }[]; min?: number }) {
  return (
    <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: `repeat(auto-fill, minmax(${min}px, 1fr))`, gap: '12px 20px' }}>
      {items.map((it) => (
        <div key={it.label} style={{ minWidth: 0 }}>
          <dt style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 2 }}>{it.label}</dt>
          <dd style={{ margin: 0, fontSize: 'var(--fs-base)', color: 'var(--text)', overflowWrap: 'anywhere' }}>{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Muted({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)', ...style }}>{children}</span>;
}

export function Spinner({ label }: { label: string }) {
  return (
    <span role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
      <Icon icon={LoaderCircle} size="dense" tone="muted" style={{ animation: 'n-spin 1s linear infinite' }} />
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Stat tiles
// ---------------------------------------------------------------------------

/**
 * One headline number: label, value, a sub line and an optional change vs a
 * named period (the API's own change_pct). Clickable tiles are buttons and
 * show their selected state. Big figures use proportional digits.
 */
export function StatTile({ label, value, sub, changePct, changeLabel, onClick, selected = false, testId, title }: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  changePct?: number | null;
  changeLabel?: string;
  onClick?: () => void;
  selected?: boolean;
  testId?: string;
  title?: string;
}) {
  const [hover, setHover] = useState(false);
  const body = (
    <>
      <span style={{ display: 'block', fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text-soft)' }}>{label}</span>
      <span style={{ display: 'block', marginTop: 4, fontSize: 'var(--fs-2xl)', fontWeight: 700, lineHeight: 1.15, letterSpacing: '-0.01em', color: 'var(--text)' }}>{value}</span>
      {changePct !== undefined && changePct !== null && (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 6, fontSize: 'var(--fs-sm)' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontWeight: 600, color: changePct > 0 ? 'var(--ok)' : changePct < 0 ? 'var(--error)' : 'var(--muted)' }}>
            <Icon icon={changePct > 0 ? TrendingUp : changePct < 0 ? TrendingDown : Minus} size="dense" label={changePct > 0 ? 'Up' : changePct < 0 ? 'Down' : 'No change'} />
            {Math.abs(changePct)}%
          </span>
          {changeLabel && <span style={{ color: 'var(--muted)' }}>{changeLabel}</span>}
        </span>
      )}
      {sub && <span style={{ display: 'block', marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{sub}</span>}
    </>
  );
  const box: CSSProperties = {
    display: 'block', width: '100%', minWidth: 0, boxSizing: 'border-box', textAlign: 'left',
    padding: '12px 14px', borderRadius: 'var(--radius-lg)', fontFamily: 'inherit',
    background: selected ? 'var(--accent-soft)' : 'var(--bg)',
    border: `1px solid ${selected ? 'var(--brand-soft)' : 'var(--line)'}`,
  };
  if (!onClick) return <div style={box} data-testid={testId} title={title}>{body}</div>;
  // No shared class gives a tile a hover fill, so the hover is local state.
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      data-testid={testId}
      title={title}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ ...box, cursor: 'pointer', ...(hover && !selected ? { background: 'var(--surface-alt)' } : {}) }}
    >
      {body}
    </button>
  );
}

export function TileRow({ children, min = 170 }: { children: ReactNode; min?: number }) {
  return <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))`, gap: 10 }}>{children}</div>;
}

// ---------------------------------------------------------------------------
// Forms
// ---------------------------------------------------------------------------

/** A labelled control. `children` receives the id to put on the control. */
export function Field({ label, hint, children, style }: {
  label: string;
  hint?: ReactNode;
  children: (id: string) => ReactNode;
  style?: CSSProperties;
}) {
  const id = useId();
  return (
    <div style={{ minWidth: 0, ...style }}>
      <label className="n-label" htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{hint}</div>}
    </div>
  );
}

export const inputStyle: CSSProperties = { width: '100%' };

export function FormGrid({ children, min = 180 }: { children: ReactNode; min?: number }) {
  return <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fill, minmax(${min}px, 1fr))`, gap: 12 }}>{children}</div>;
}

/**
 * A dialog around a form. Submitting runs `onSubmit`; throw an Error (or let
 * the API's FnApiError through) to keep the dialog open with the reason
 * shown inside it. Nothing closes it while the request runs.
 */
export function FormDialog({ title, width = 480, submitLabel, busyLabel, danger = false, submitDisabled = false, onSubmit, onClose, onReload, children }: {
  title: string;
  width?: number;
  submitLabel: string;
  busyLabel?: string;
  danger?: boolean;
  submitDisabled?: boolean;
  onSubmit: () => Promise<void>;
  onClose: () => void;
  /** Offered when the save hits a conflict (someone else changed it): re-read
   *  the record and close, so the edit can be redone on fresh data. */
  onReload?: () => void;
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ActionError | null>(null);
  // Set on every mount: React's development double-mount runs the cleanup
  // once in between.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSubmit();
    } catch (e) {
      if (alive.current) {
        setError(toActionError(e));
        setBusy(false);
      }
      return;
    }
    if (alive.current) onClose();
  };
  const dismiss = () => { if (!busy) onClose(); };

  return (
    <Dialog title={title} width={width} onClose={dismiss} closeButton>
      <form onSubmit={(e) => { e.preventDefault(); if (!busy && !submitDisabled) void submit(); }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>{children}</div>
        {error && (
          <div role="alert" style={{ ...alertStyle, marginTop: 14 }}>
            <span style={{ flex: 1, minWidth: 0 }}>{error.message}</span>
            {error.conflict && onReload && <Button size="sm" onClick={() => { onReload(); onClose(); }}>Reload</Button>}
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 20 }}>
          <Button onClick={dismiss} disabled={busy}>Cancel</Button>
          <Button type="submit" variant={danger ? 'danger' : 'primary'} disabled={busy || submitDisabled} aria-busy={busy || undefined}>
            {busy ? (busyLabel ?? `${submitLabel}…`) : submitLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Popover
// ---------------------------------------------------------------------------

/** A small panel under a trigger; closes on Escape or a click elsewhere. */
export function Popover({ trigger, children, width = 320, align = 'left' }: {
  trigger: (props: { open: boolean; toggle: () => void; id: string }) => ReactNode;
  children: ReactNode | ((close: () => void) => ReactNode);
  width?: number;
  align?: 'left' | 'right';
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  const close = () => setOpen(false);
  return (
    <div ref={box} style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}>
      {trigger({ open, toggle: () => setOpen((o) => !o), id })}
      {open && (
        <div
          id={id}
          role="dialog"
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', [align]: 0, zIndex: 30,
            width, maxWidth: 'calc(100vw - 32px)', maxHeight: 360, overflowY: 'auto',
            background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)',
            boxShadow: '0 8px 28px rgba(26, 26, 26, 0.14)', padding: 8,
          }}
        >
          {typeof children === 'function' ? children(close) : children}
        </div>
      )}
    </div>
  );
}
