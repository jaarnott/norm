'use client';

import { useState, type ReactNode } from 'react';
import { Minus, Plus, X } from 'lucide-react';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';

/**
 * The client portal's inputs. Phone first: full-width fields, 16px text on
 * touch screens (globals.css), 40px+ targets, labels above, the hint and any
 * error tied to the input (aria-describedby) so a screen reader reads them.
 */

export function Card({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="n-card fp-avoid-break" style={{ padding: 16 }} aria-label={title}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>{title}</h3>
        {aside}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>{children}</div>
    </section>
  );
}

function described(id: string, help?: ReactNode, error?: string | null): string | undefined {
  const ids = [help ? `${id}-help` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ');
  return ids || undefined;
}

function Hint({ id, help, error }: { id: string; help?: ReactNode; error?: string | null }) {
  return (
    <>
      {help && <p id={`${id}-help`} style={{ margin: '2px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{help}</p>}
      {error && <p id={`${id}-error`} style={{ margin: '4px 0 0', fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--error)' }}>{error}</p>}
    </>
  );
}

const inputStyle = { width: '100%' } as const;

/** Enough rows to show what's written (long imported notes), up to a dozen. */
function rowsFor(value: string, min: number): number {
  const lines = value.split('\n').reduce((n, line) => n + Math.max(1, Math.ceil(line.length / 60)), 0);
  return Math.min(12, Math.max(min, lines));
}

export function TextField({ id, label, value, onChange, disabled, help, error, multiline, rows = 3, type = 'text', inputMode, placeholder, maxLength = 5000, autoComplete, prefix, width }: {
  id: string;
  label: ReactNode;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  help?: ReactNode;
  error?: string | null;
  multiline?: boolean;
  rows?: number;
  type?: 'text' | 'time' | 'tel' | 'email' | 'url';
  inputMode?: 'numeric' | 'decimal' | 'text' | 'tel' | 'email' | 'url';
  placeholder?: string;
  maxLength?: number;
  autoComplete?: string;
  prefix?: string;
  width?: number;
}) {
  const common = {
    id,
    value,
    disabled,
    placeholder,
    maxLength,
    autoComplete,
    'aria-describedby': described(id, help, error),
    'aria-invalid': error ? true : undefined,
    className: 'n-input',
  };
  return (
    <div>
      <label htmlFor={id} className="n-label">{label}</label>
      {multiline ? (
        <textarea {...common} rows={rowsFor(value, rows)} onChange={(e) => onChange(e.target.value)} style={inputStyle} />
      ) : prefix ? (
        <div style={{ position: 'relative', maxWidth: width }}>
          <span aria-hidden style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)' }}>{prefix}</span>
          <input {...common} type={type} inputMode={inputMode} onChange={(e) => onChange(e.target.value)} style={{ ...inputStyle, paddingLeft: 22 }} />
        </div>
      ) : (
        <input {...common} type={type} inputMode={inputMode} onChange={(e) => onChange(e.target.value)} style={{ ...inputStyle, maxWidth: width }} />
      )}
      <Hint id={id} help={help} error={error} />
    </div>
  );
}

/** A read-only fact the venue set (payment, deposit, bar tab). */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="n-label">{label}</div>
      <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text)' }}>{children}</div>
    </div>
  );
}

export function YesNo({ id, label, value, onChange, disabled, help }: {
  id: string;
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
  help?: ReactNode;
}) {
  const option = (on: boolean, text: string) => (
    <button
      type="button"
      aria-pressed={value === on}
      disabled={disabled}
      onClick={() => onChange(on)}
      style={{
        minWidth: 72,
        minHeight: 40,
        padding: '0 16px',
        border: 'none',
        borderLeft: on ? 'none' : '1px solid var(--line-strong)',
        background: value === on ? 'var(--primary)' : 'var(--bg)',
        color: value === on ? 'var(--on-primary)' : 'var(--text)',
        fontWeight: value === on ? 600 : 500,
        cursor: disabled ? 'default' : 'pointer',
      }}
    >
      {text}
    </button>
  );
  return (
    <div role="group" aria-labelledby={`${id}-label`} aria-describedby={help ? `${id}-help` : undefined}>
      <div id={`${id}-label`} className="n-label">{label}</div>
      <div style={{ display: 'inline-flex', border: '1px solid var(--line-strong)', borderRadius: 'var(--radius)', overflow: 'hidden', opacity: disabled ? 0.6 : 1 }}>
        {option(true, 'Yes')}
        {option(false, 'No')}
      </div>
      <Hint id={id} help={help} />
    </div>
  );
}

/** Radio choices drawn as cards (food timing). */
export function ChoiceCards({ id, label, value, options, onChange, disabled }: {
  id: string;
  label: string;
  value: string;
  options: { value: string; label: string; detail?: string }[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset style={{ margin: 0, padding: 0, border: 0, minWidth: 0 }}>
      <legend className="n-label" style={{ padding: 0 }}>{label}</legend>
      <div className="fp-two">
        {options.map((o) => {
          const on = value === o.value;
          return (
            <label
              key={o.value}
              htmlFor={`${id}-${o.value}`}
              style={{
                display: 'flex',
                gap: 10,
                alignItems: 'flex-start',
                padding: '10px 12px',
                borderRadius: 'var(--radius)',
                border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line)'}`,
                background: on ? 'var(--accent-soft)' : 'var(--bg)',
                cursor: disabled ? 'default' : 'pointer',
              }}
            >
              <input
                id={`${id}-${o.value}`}
                type="radio"
                name={id}
                value={o.value}
                checked={on}
                disabled={disabled}
                onChange={() => onChange(o.value)}
                style={{ marginTop: 3, accentColor: 'var(--primary)' }}
              />
              <span>
                <span style={{ display: 'block', fontWeight: 600, color: 'var(--text)' }}>{o.label}</span>
                {o.detail && <span style={{ display: 'block', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{o.detail}</span>}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

/** Pick-any chips plus your own entries (what the bar tab covers). */
export function Chips({ id, label, help, selected, presets, toggle, onChange, disabled }: {
  id: string;
  label: string;
  help?: ReactNode;
  selected: string[];
  presets: string[];
  /** The list after turning `item` on or off (exclusive choices live here). */
  toggle: (list: string[], item: string) => string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}) {
  const [custom, setCustom] = useState('');
  const isOn = (item: string) => selected.some((s) => s.toLowerCase() === item.toLowerCase());
  const extras = selected.filter((s) => !presets.some((p) => p.toLowerCase() === s.toLowerCase()));
  const add = () => {
    const text = custom.trim();
    if (!text) return;
    if (!isOn(text)) onChange(toggle(selected, text));
    setCustom('');
  };
  const chip = (item: string, on: boolean, onClick: () => void, removable = false) => (
    <button
      key={item}
      type="button"
      aria-pressed={removable ? undefined : on}
      aria-label={removable ? `Remove ${item}` : undefined}
      disabled={disabled}
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        minHeight: 36,
        padding: '0 12px',
        borderRadius: 999,
        border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line-strong)'}`,
        background: on ? 'var(--accent-soft)' : 'var(--bg)',
        color: on ? 'var(--accent-strong)' : 'var(--text)',
        fontWeight: on ? 600 : 500,
        fontSize: 'var(--fs-sm)',
        cursor: disabled ? 'default' : 'pointer',
      }}
    >
      {item}
      {removable && <Icon icon={X} size={14} />}
    </button>
  );
  return (
    <div role="group" aria-labelledby={`${id}-label`} aria-describedby={help ? `${id}-help` : undefined}>
      <div id={`${id}-label`} className="n-label">{label}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {presets.map((p) => chip(p, isOn(p), () => onChange(toggle(selected, p))))}
        {extras.map((e) => chip(e, true, () => onChange(selected.filter((s) => s !== e)), true))}
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 8, maxWidth: 420 }}>
        <input
          id={`${id}-custom`}
          className="n-input"
          value={custom}
          disabled={disabled}
          placeholder="Something else…"
          aria-label={`Add to ${label.toLowerCase()}`}
          maxLength={120}
          onChange={(e) => setCustom(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          style={{ flex: 1, minWidth: 0 }}
        />
        <button type="button" className="n-btn n-btn--secondary" onClick={add} disabled={disabled || !custom.trim()}>Add</button>
      </div>
      <Hint id={id} help={help} />
    </div>
  );
}

/** One row of a list (run sheet, security, entertainment) with its remove button. */
export function Row({ label, onRemove, disabled, children }: { label: string; onRemove: () => void; disabled?: boolean; children: ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: 10, borderRadius: 'var(--radius)', background: 'var(--surface)', border: '1px solid var(--line-soft)' }}>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexWrap: 'wrap', gap: 8 }}>{children}</div>
      {!disabled && <IconButton icon={X} label={`Remove ${label}`} onClick={onRemove} />}
    </div>
  );
}

/** A compact labelled input inside a Row. */
export function RowInput({ label, value, onChange, type = 'text', width, disabled, placeholder }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: 'text' | 'time' | 'url';
  width?: number | string;
  disabled?: boolean;
  placeholder?: string;
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: width ? `0 0 ${typeof width === 'number' ? `${width}px` : width}` : '1 1 160px', minWidth: 0 }}>
      <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{label}</span>
      <input className="n-input" type={type} value={value} disabled={disabled} placeholder={placeholder} maxLength={5000} onChange={(e) => onChange(e.target.value)} style={{ width: '100%' }} />
    </label>
  );
}

export function AddButton({ onClick, children, disabled }: { onClick: () => void; children: ReactNode; disabled?: boolean }) {
  return (
    <button type="button" className="n-btn n-btn--secondary n-btn--sm" onClick={onClick} disabled={disabled} style={{ alignSelf: 'flex-start' }}>
      <Icon icon={Plus} size={14} />
      {children}
    </button>
  );
}

/** − quantity + for a dish (clients change quantities only). */
export function Stepper({ id, name, value, onChange, disabled }: { id: string; name: string; value: number; onChange: (value: number) => void; disabled?: boolean }) {
  const btn = { width: 40, height: 40, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', border: 'none', background: 'var(--bg)', color: 'var(--text)', cursor: disabled ? 'default' : 'pointer' } as const;
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', border: '1px solid var(--line-strong)', borderRadius: 999, overflow: 'hidden', flex: '0 0 auto', opacity: disabled ? 0.6 : 1 }}>
      <button type="button" aria-label={`Fewer ${name}`} disabled={disabled || value <= 0} onClick={() => onChange(value - 1)} style={btn}>
        <Icon icon={Minus} size={14} />
      </button>
      <input
        id={id}
        aria-label={`Quantity of ${name}`}
        inputMode="numeric"
        value={String(value)}
        disabled={disabled}
        onChange={(e) => {
          const digits = e.target.value.replace(/\D/g, '');
          onChange(digits ? Number(digits) : 0);
        }}
        onFocus={(e) => e.target.select()}
        style={{ width: 44, height: 40, border: 'none', borderLeft: '1px solid var(--line)', borderRight: '1px solid var(--line)', textAlign: 'center', fontVariantNumeric: 'tabular-nums', background: 'var(--bg)', color: 'var(--text)' }}
      />
      <button type="button" aria-label={`More ${name}`} disabled={disabled} onClick={() => onChange(value + 1)} style={btn}>
        <Icon icon={Plus} size={14} />
      </button>
    </div>
  );
}
