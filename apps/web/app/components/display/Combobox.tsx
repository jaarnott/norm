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

import { useEffect, useMemo, useRef, useState } from 'react';
import { colors } from '../../lib/theme';

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
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

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

  const inputStyle: React.CSSProperties = {
    padding: '5px 8px',
    fontSize: '0.85rem',
    border: `1px solid ${colors.border}`,
    borderRadius: 6,
    fontFamily: 'inherit',
    width: '100%',
    boxSizing: 'border-box',
    ...style,
  };

  const panel: React.CSSProperties = {
    position: 'absolute',
    top: '100%',
    left: 0,
    right: 0,
    zIndex: 50,
    marginTop: 2,
    background: '#fff',
    border: `1px solid ${colors.border}`,
    borderRadius: 8,
    boxShadow: '0 4px 16px rgba(0,0,0,0.1)',
    maxHeight: 260,
    overflowY: 'auto',
  };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <input
        value={value}
        placeholder={placeholder}
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
            setHi((i) => Math.min(i + 1, filtered.length - 1));
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setHi((i) => Math.max(i - 1, 0));
          } else if (e.key === 'Enter' && open && filtered[hi]) {
            e.preventDefault();
            pick(filtered[hi]);
          } else if (e.key === 'Escape') {
            setOpen(false);
          }
        }}
        style={inputStyle}
      />
      {open && filtered.length > 0 && (
        <div style={panel}>
          {filtered.map((o, i) => (
            <div
              key={`${o.kind || ''}-${o.id}`}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(o);
              }}
              onMouseEnter={() => setHi(i)}
              style={{
                padding: '0.4rem 0.6rem',
                cursor: 'pointer',
                borderBottom: i < filtered.length - 1 ? `1px solid ${colors.borderLight}` : 'none',
                background: i === hi ? colors.selectedBg : '#fff',
                display: 'flex',
                justifyContent: 'space-between',
                gap: '0.6rem',
                alignItems: 'baseline',
              }}
            >
              <span style={{ fontSize: '0.82rem', color: colors.textPrimary }}>{o.name}</span>
              {o.sublabel && (
                <span style={{ fontSize: '0.68rem', color: colors.textMuted, whiteSpace: 'nowrap' }}>
                  {o.sublabel}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      {open && q.length >= minChars && filtered.length === 0 && (
        <div style={{ ...panel, padding: '0.5rem 0.6rem', fontSize: '0.78rem', color: colors.textMuted }}>
          No match for &ldquo;{value}&rdquo;.
        </div>
      )}
    </div>
  );
}
