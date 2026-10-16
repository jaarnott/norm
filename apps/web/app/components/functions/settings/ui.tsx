'use client';

/**
 * Small building blocks shared by the Settings tabs: section headings, form
 * fields, notices, the save row, image thumbnails, uploads with progress and
 * copy buttons. Everything is styled with the design tokens (tokens.css).
 */
import { useCallback, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Check, Copy, ImageOff, Lock, Upload } from 'lucide-react';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import { READ_ONLY_HINT, errorText } from './errors';
import { IMAGE_ACCEPT, imageProblem } from './upload';
import { useFileUrl } from './useFileUrl';
import { openNormSettings } from './nav';

export const help: CSSProperties = { margin: '4px 0 0', fontSize: 'var(--fs-xs)', color: 'var(--muted)', lineHeight: 1.45 };
export const muted: CSSProperties = { color: 'var(--muted)', fontSize: 'var(--fs-sm)' };
export const fullWidth: CSSProperties = { width: '100%' };
export const mono: CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' };

/** A titled block of the page: 18px/600 title, one muted line, actions right. */
export function Section({ title, meta, actions, children, id }: {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  return (
    <section id={id} style={{ marginBottom: 28 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={{ minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h2>
          {meta && <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta}</div>}
        </div>
        {actions && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>{actions}</div>}
      </div>
      {children}
    </section>
  );
}

/** A white card around a block of controls. */
export function Panel({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div className="n-card" style={{ padding: 16, minWidth: 0, ...style }}>{children}</div>;
}

/** Label + control + optional help line. Pass the control's id as htmlFor. */
export function Field({ label, htmlFor, hint, children, style }: {
  label: ReactNode;
  htmlFor?: string;
  hint?: ReactNode;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div style={{ minWidth: 0, ...style }}>
      <label className="n-label" htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <p style={help}>{hint}</p>}
    </div>
  );
}

/** A responsive grid of fields. */
export function FieldGrid({ children, min = 200 }: { children: ReactNode; min?: number }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(min(${min}px, 100%), 1fr))`, gap: 14 }}>
      {children}
    </div>
  );
}

const NOTICE: Record<'error' | 'warn' | 'info' | 'ok', { bg: string; fg: string }> = {
  error: { bg: 'var(--error-bg)', fg: 'var(--error)' },
  warn: { bg: 'var(--warn-bg)', fg: 'var(--warn)' },
  info: { bg: 'var(--info-bg)', fg: 'var(--info)' },
  ok: { bg: 'var(--ok-bg)', fg: 'var(--ok)' },
};

/** A tinted message line; errors are announced. */
export function Notice({ tone, children, action, style }: {
  tone: 'error' | 'warn' | 'info' | 'ok';
  children: ReactNode;
  action?: ReactNode;
  style?: CSSProperties;
}) {
  const c = NOTICE[tone];
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '8px 12px', borderRadius: 'var(--radius)', background: c.bg, color: c.fg,
        fontSize: 'var(--fs-sm)', lineHeight: 1.45, ...style,
      }}
    >
      <div style={{ flex: '1 1 220px', minWidth: 0 }}>{children}</div>
      {action}
    </div>
  );
}

/** "Read-only while Orbit is live" — beside the controls shadow mode disables. */
export function ReadOnlyNote({ style }: { style?: CSSProperties }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--muted)', ...style }}>
      <Icon icon={Lock} size={14} tone="muted" />
      {READ_ONLY_HINT}
    </span>
  );
}

/**
 * The bottom row of an editable block: Save (the block's one primary), a
 * "Saved" note, the API's refusal, and — in shadow mode — the read-only hint
 * instead of the button.
 */
export function SaveRow({ canWrite, dirty, saving, saved, error, onSave, onDiscard, label = 'Save', extra, disabled }: {
  canWrite: boolean;
  dirty: boolean;
  saving: boolean;
  saved?: boolean;
  error?: string | null;
  onSave: () => void;
  onDiscard?: () => void;
  label?: string;
  extra?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {canWrite ? (
          <>
            <Button variant="primary" onClick={onSave} disabled={!dirty || saving || disabled} aria-busy={saving || undefined}>
              {saving ? 'Saving…' : label}
            </Button>
            {onDiscard && dirty && !saving && <Button variant="quiet" onClick={onDiscard}>Discard changes</Button>}
            {saved && !dirty && (
              <span role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, ...muted }}>
                <Icon icon={Check} size={14} /> Saved
              </span>
            )}
          </>
        ) : (
          <ReadOnlyNote />
        )}
        {extra}
      </div>
      {error && <Notice tone="error" style={{ marginTop: 10 }}>{error}</Notice>}
    </div>
  );
}

/** A labelled native checkbox (accent-coloured), optional description. */
export function CheckboxRow({ checked, onChange, label, description, disabled, id }: {
  checked: boolean;
  onChange: (on: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  id?: string;
}) {
  const autoId = useId();
  const inputId = id ?? autoId;
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
      <input
        id={inputId}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-describedby={description ? `${inputId}-d` : undefined}
        onChange={(e) => onChange(e.target.checked)}
        style={{ flex: '0 0 auto', width: 16, height: 16, margin: '2px 0 0', accentColor: 'var(--accent)', cursor: disabled ? 'default' : 'pointer' }}
      />
      <div style={{ minWidth: 0 }}>
        <label htmlFor={inputId} style={{ fontSize: 'var(--fs-base)', color: disabled ? 'var(--muted)' : 'var(--text)', cursor: disabled ? 'default' : 'pointer' }}>
          {label}
        </label>
        {description && <span id={`${inputId}-d`} style={{ display: 'block', ...help, marginTop: 2 }}>{description}</span>}
      </div>
    </div>
  );
}

/** A pill that toggles (booking types, dietary codes): tan tint when on. */
export function Chip({ on, onClick, children, disabled, title }: {
  on: boolean;
  onClick: () => void;
  children: ReactNode;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      disabled={disabled}
      onClick={onClick}
      title={title}
      style={{
        flex: '0 0 auto', padding: '4px 10px', borderRadius: 999, whiteSpace: 'nowrap',
        fontFamily: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: on ? 600 : 500,
        border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line)'}`,
        background: on ? 'var(--accent-soft)' : 'var(--bg)',
        color: on ? 'var(--accent)' : 'var(--text-soft)',
        cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.6 : 1,
      }}
    >
      {children}
    </button>
  );
}

/** Two-way segmented control (Pre-order menus / Reply templates). */
export function Segmented<T extends string>({ items, value, onChange, label }: {
  items: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
      {items.map((it) => (
        <Chip key={it.id} on={it.id === value} onClick={() => onChange(it.id)}>{it.label}</Chip>
      ))}
    </div>
  );
}

/** Buttons for reordering: up / down in a list, left / right in a row of
 *  tiles (photos). */
export function MoveButtons({ index, count, onMove, disabled, what, axis = 'vertical' }: {
  index: number;
  count: number;
  onMove: (delta: -1 | 1) => void;
  disabled?: boolean;
  what: string;
  axis?: 'vertical' | 'horizontal';
}) {
  const across = axis === 'horizontal';
  return (
    <span style={{ display: 'inline-flex' }}>
      <IconButton icon={across ? ArrowLeft : ArrowUp} iconSize={16} label={`Move ${what} ${across ? 'earlier' : 'up'}`}
        disabled={disabled || index === 0} onClick={() => onMove(-1)} />
      <IconButton icon={across ? ArrowRight : ArrowDown} iconSize={16} label={`Move ${what} ${across ? 'later' : 'down'}`}
        disabled={disabled || index >= count - 1} onClick={() => onMove(1)} />
    </span>
  );
}

/** An uploaded image (staff copy, fetched with the session's token). */
export function AuthImage({ fileId, alt, width, height, fit = 'cover', style }: {
  fileId: string | null | undefined;
  alt: string;
  width: number | string;
  height: number | string;
  fit?: 'cover' | 'contain';
  style?: CSSProperties;
}) {
  const { url, failed } = useFileUrl(fileId);
  const box: CSSProperties = {
    width, height, flex: '0 0 auto', borderRadius: 'var(--radius-sm)', background: 'var(--surface-alt)',
    border: '1px solid var(--line)', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center', ...style,
  };
  if (!url) {
    return (
      <span style={box} role={failed ? 'img' : undefined} aria-label={failed ? `${alt} (unavailable)` : undefined}>
        {failed && <Icon icon={ImageOff} size={16} tone="muted" />}
      </span>
    );
  }
  return (
    <span style={box}>
      {/* eslint-disable-next-line @next/next/no-img-element -- a blob: URL of an authenticated image; next/image can't load it */}
      <img src={url} alt={alt} style={{ width: '100%', height: '100%', objectFit: fit, display: 'block' }} />
    </span>
  );
}

/** Copy text to the clipboard, with a moment of "Copied". */
export function CopyButton({ text, label = 'Copy', size = 'sm', variant = 'secondary' }: {
  text: string;
  label?: string;
  size?: 'sm' | 'md';
  variant?: 'secondary' | 'quiet';
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copy = async () => {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      // Older browsers / insecure origins: a hidden textarea and execCommand.
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        ok = document.execCommand('copy');
      } catch {
        ok = false;
      }
      ta.remove();
    }
    if (!ok) return;
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1600);
  };
  return (
    <Button size={size} variant={variant} icon={copied ? Check : Copy} onClick={copy} aria-live="polite">
      {copied ? 'Copied' : label}
    </Button>
  );
}

/** One file on its way up. */
export interface UploadItem {
  key: string;
  name: string;
  progress: number;
  error: string | null;
}

/**
 * Upload several files one after another, tracking each one's progress and
 * failure. `send(file, onProgress)` does the request; a file that fails the
 * light client check never leaves the browser. Finished files drop out of
 * the list; failed ones stay until dismissed.
 */
export function useUploads(send: (file: File, onProgress: (f: number) => void) => Promise<void>) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const seq = useRef(0);
  const patch = (key: string, p: Partial<UploadItem>) =>
    setItems((list) => list.map((it) => (it.key === key ? { ...it, ...p } : it)));

  const start = useCallback(async (files: FileList | File[]) => {
    const queue = Array.from(files).map((file) => {
      seq.current += 1;
      return { file, key: `up-${seq.current}`, problem: imageProblem(file) };
    });
    setItems((list) => [
      ...list,
      ...queue.map((q) => ({ key: q.key, name: q.file.name, progress: 0, error: q.problem })),
    ]);
    for (const q of queue) {
      if (q.problem) continue;
      try {
        await send(q.file, (f) => patch(q.key, { progress: f }));
        setItems((list) => list.filter((it) => it.key !== q.key));
      } catch (e) {
        patch(q.key, { error: errorText(e, 'The upload failed — try again.') });
      }
    }
  }, [send]);

  const dismiss = (key: string) => setItems((list) => list.filter((it) => it.key !== key));
  return { items, start, dismiss, busy: items.some((it) => !it.error) };
}

/** Progress bars and errors for `useUploads`. */
export function UploadList({ items, onDismiss }: { items: UploadItem[]; onDismiss: (key: string) => void }) {
  if (!items.length) return null;
  return (
    <ul style={{ listStyle: 'none', margin: '10px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
      {items.map((it) => (
        <li key={it.key} style={{ fontSize: 'var(--fs-sm)' }}>
          {it.error ? (
            <Notice tone="error" action={<Button size="sm" variant="quiet" onClick={() => onDismiss(it.key)}>Dismiss</Button>}>
              {it.error}
            </Notice>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ flex: '0 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-soft)' }}>
                {it.name}
              </span>
              <span
                role="progressbar"
                aria-label={`Uploading ${it.name}`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(it.progress * 100)}
                style={{ flex: '1 1 120px', height: 6, borderRadius: 999, background: 'var(--surface-alt)', overflow: 'hidden' }}
              >
                <span style={{ display: 'block', height: '100%', width: `${Math.max(4, Math.round(it.progress * 100))}%`, background: 'var(--accent)', transition: 'width 0.15s' }} />
              </span>
              <span style={{ ...muted, fontVariantNumeric: 'tabular-nums' }}>{Math.round(it.progress * 100)}%</span>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/** A button that opens the file picker for images. */
export function UploadButton({ label, onFiles, multiple = true, disabled, variant = 'secondary', size = 'sm' }: {
  label: string;
  onFiles: (files: FileList) => void;
  multiple?: boolean;
  disabled?: boolean;
  variant?: 'secondary' | 'quiet' | 'primary';
  size?: 'sm' | 'md';
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept={IMAGE_ACCEPT}
        multiple={multiple}
        hidden
        onChange={(e) => {
          if (e.target.files?.length) onFiles(e.target.files);
          e.target.value = '';
        }}
      />
      <Button size={size} variant={variant} icon={Upload} disabled={disabled} onClick={() => input.current?.click()}>
        {label}
      </Button>
    </>
  );
}

/**
 * A button into Norm's own Settings (Company, Venues). When the app shell
 * doesn't take the request, it says where to find the screen instead.
 */
export function NormSettingsButton({ tab, label }: { tab: 'company' | 'venues'; label: string }) {
  const [unhandled, setUnhandled] = useState(false);
  const where = tab === 'company' ? 'Settings → Organisation → Company' : 'Settings → Organisation → Venues';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      <Button size="sm" onClick={() => setUnhandled(!openNormSettings(tab))}>{label}</Button>
      {unhandled && <span style={muted}>Open {where} (the gear at the bottom of the menu).</span>}
    </span>
  );
}
