'use client';

// Small pieces shared by the Suggestions admin cards.
import type { ReactNode } from 'react';
import Button from '../../ui/Button';
import type { Meta } from './types';

export const muted: React.CSSProperties = { fontSize: 'var(--fs-xs)', color: 'var(--muted)' };
export const sectionTitle: React.CSSProperties = { margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' };

/** A label, the control, and its help line beneath. */
export function Field({ label, help, changed, children }: { label: string; help?: ReactNode; changed?: boolean; children: ReactNode }) {
  return (
    <label style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: '4px 12px', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
      <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
        {label}
        {changed && <span style={{ marginLeft: 6, fontSize: 'var(--fs-xs)', color: 'var(--accent)' }}>changed</span>}
      </span>
      <span>{children}</span>
      {help && <span style={{ ...muted, gridColumn: '1 / -1' }}>{help}</span>}
    </label>
  );
}

export function Problems({ problems }: { problems?: string[] | null }) {
  if (!problems || !problems.length) return null;
  return (
    <div role="alert" style={{ margin: '8px 0', padding: '8px 10px', borderRadius: 'var(--radius)', background: 'var(--surface-alt)', fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
      {problems.map((p, i) => <div key={i}>{p}</div>)}
    </div>
  );
}

/** Save, back to the defaults, undo the last save — and who changed it last. */
export function Actions({ dirty, busy, changed, meta, onSave, onReset, onUndo, resetLabel = 'Use the defaults' }: {
  dirty: boolean;
  busy: boolean;
  changed: boolean;
  meta?: Meta;
  onSave: () => void;
  onReset?: () => void;
  onUndo?: () => void;
  resetLabel?: string;
}) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginTop: 12 }}>
      <Button variant="primary" size="sm" onClick={onSave} disabled={!dirty || busy}>{busy ? 'Saving…' : 'Save'}</Button>
      {onReset && changed && <Button size="sm" onClick={onReset} disabled={busy}>{resetLabel}</Button>}
      {onUndo && meta?.can_undo && <Button size="sm" variant="quiet" onClick={onUndo} disabled={busy}>Undo last save</Button>}
      {meta?.updated_by && (
        <span style={muted}>Last changed by {meta.updated_by}{meta.updated_at ? `, ${new Date(meta.updated_at).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}` : ''}</span>
      )}
    </div>
  );
}

export function Toggle({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      onClick={() => onChange(!on)}
      style={{ width: 36, height: 20, borderRadius: 10, border: '1px solid var(--line-strong)', background: on ? 'var(--accent)' : 'var(--surface-alt)', position: 'relative', cursor: disabled ? 'default' : 'pointer', padding: 0, flex: '0 0 auto' }}>
      <span style={{ position: 'absolute', top: 2, left: on ? 18 : 2, width: 14, height: 14, borderRadius: 7, background: on ? 'var(--bg)' : 'var(--muted)', transition: 'left 0.12s' }} />
    </button>
  );
}
