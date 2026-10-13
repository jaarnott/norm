'use client';

/**
 * Combobox — a searchable typeahead for picking one option from a large list.
 *
 * Replaces the raw <datalist> pickers (which don't rank matches, show no
 * metadata, and render inconsistently across browsers). Matching is
 * case-insensitive substring with start-of-string matches ranked first, then
 * alphabetical — so typing "sal" surfaces "Salt" before "Balsamic". The parent
 * owns the text (`value`); `onType` fires on free typing, `onPick` on selection.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react';

export interface ComboOption {
  id: string;
  name: string;
  sublabel?: string;
  kind?: string;
}

interface ComboboxProps {
  value: string;
  options: ComboOption[];
  onType: (text: string) => void;
  onPick: (opt: ComboOption) => void;
  placeholder?: string;
  minChars?: number;
  max?: number;
  style?: React.CSSProperties;
  /** When set, the list ends with a "create" row for the typed text — the
   *  way out when nothing matches. Called with the text as typed. */
  onCreate?: (text: string) => void;
  /** The create row's label for the typed text (default: Create “text”). */
  createLabel?: (text: string) => string;
}

export default function Combobox({
  value,
  options,
  onType,
  onPick,
  placeholder,
  minChars = 1,
  max = 25,
  style,
  onCreate,
  createLabel,
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const listId = useId();

  const q = (value || '').trim().toLowerCase();
  const filtered = useMemo(() => {
    if (q.length < minChars) return [];
    const starts: ComboOption[] = [];
    const contains: ComboOption[] = [];
    for (const o of options) {
      const n = o.name.toLowerCase();
      if (n.startsWith(q)) starts.push(o);
      else if (n.includes(q)) contains.push(o);
    }
    const byName = (a: ComboOption, b: ComboOption) => a.name.localeCompare(b.name);
    starts.sort(byName);
    contains.sort(byName);
    return [...starts, ...contains].slice(0, max);
  }, [options, q, minChars, max]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, []);

  const pick = (o: ComboOption) => {
    onPick(o);
    setOpen(false);
  };
  // The create row sits after the matches and takes the next highlight index.
  const canCreate = !!onCreate && q.length >= minChars;
  const rows = filtered.length + (canCreate ? 1 : 0);
  const create = () => {
    onCreate?.((value || '').trim());
    setOpen(false);
  };

  // tokens.css does the work: .n-input for the field, .n-option for each
  // choice (hover, and aria-selected for the keyboard highlight).
  const inputStyle: React.CSSProperties = { width: '100%', ...style };

  const panel: React.CSSProperties = {
    position: 'absolute',
    top: '100%',
    left: 0,
    right: 0,
    zIndex: 50,
    marginTop: 4,
    padding: '4px 0',
    background: 'var(--bg)',
    border: '1px solid var(--line)',
    borderRadius: 'var(--radius)',
    boxShadow: '0 6px 18px rgba(26,26,26,0.12)',
    maxHeight: 260,
  };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <input
        className="n-input"
        value={value}
        placeholder={placeholder}
        role="combobox"
        aria-autocomplete="list"
        aria-controls={listId}
        aria-expanded={open && rows > 0}
        onChange={(e) => {
          onType(e.target.value);
          setOpen(true);
          setHi(0);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (!open && e.key === 'ArrowDown') {
            setOpen(true);
            return;
          }
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setHi((i) => Math.min(i + 1, rows - 1));
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setHi((i) => Math.max(i - 1, 0));
          } else if (e.key === 'Enter' && open && filtered[hi]) {
            e.preventDefault();
            pick(filtered[hi]);
          } else if (e.key === 'Enter' && open && canCreate && hi === filtered.length) {
            e.preventDefault();
            create();
          } else if (e.key === 'Escape') {
            setOpen(false);
          }
        }}
        style={inputStyle}
      />
      {open && rows > 0 && (
        <div id={listId} role="listbox" className="scroll-quiet" style={panel}>
          {filtered.map((o, i) => (
            <div
              key={`${o.kind || ''}-${o.id}`}
              role="option"
              aria-selected={i === hi}
              className="n-option"
              onMouseDown={(e) => {
                e.preventDefault();
                pick(o);
              }}
              onMouseEnter={() => setHi(i)}
              style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}
            >
              <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{o.name}</span>
              {o.sublabel && (
                <span style={{ flex: '0 0 auto', fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                  {o.sublabel}
                </span>
              )}
            </div>
          ))}
          {canCreate && (
            <div
              role="option"
              aria-selected={hi === filtered.length}
              className="n-option"
              onMouseDown={(e) => {
                e.preventDefault();
                create();
              }}
              onMouseEnter={() => setHi(filtered.length)}
              style={{
                display: 'flex', alignItems: 'baseline', gap: 10, color: 'var(--text-soft)',
                ...(filtered.length > 0 ? { borderTop: '1px solid var(--line)', marginTop: 4, paddingTop: 8 } : {}),
              }}
            >
              <span aria-hidden style={{ flex: '0 0 auto' }}>+</span>
              <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                {createLabel ? createLabel((value || '').trim()) : `Create “${(value || '').trim()}”`}
              </span>
            </div>
          )}
        </div>
      )}
      {open && q.length >= minChars && rows === 0 && (
        <div style={{ ...panel, padding: '8px 10px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          No match for &ldquo;{value}&rdquo;.
        </div>
      )}
    </div>
  );
}
