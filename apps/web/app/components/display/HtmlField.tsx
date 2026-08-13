'use client';

/**
 * HtmlField — a lightweight rich-text field for recipe method / notes.
 *
 * Loaded stores the method as HTML (headings, <ol> steps, <b> …). A plain
 * textarea shows the raw tags; this renders the formatting and edits it in place
 * via contentEditable. It's uncontrolled between resets — React setting innerHTML
 * on every keystroke would fight the caret — so we only write the DOM when
 * `resetKey` changes (i.e. a different recipe is opened) and read it back on input.
 */

import { useEffect, useRef } from 'react';
import { colors } from '../../lib/theme';

interface HtmlFieldProps {
  html: string;
  resetKey: string;
  onChange: (html: string) => void;
  placeholder?: string;
  minHeight?: number;
}

export default function HtmlField({ html, resetKey, onChange, placeholder, minHeight = 90 }: HtmlFieldProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current && ref.current.innerHTML !== (html || '')) {
      ref.current.innerHTML = html || '';
    }
    // Only re-sync the DOM when the source recipe changes, not on every edit.
  }, [resetKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <style>{`
        .norm-html-field:empty:before { content: attr(data-placeholder); color: ${colors.textMuted}; }
        .norm-html-field:focus { outline: none; border-color: ${colors.primary}; }
        .norm-html-field ol, .norm-html-field ul { margin: 0.2rem 0 0.2rem 1.1rem; padding: 0; }
        .norm-html-field li { margin: 0.1rem 0; }
        .norm-html-field p { margin: 0.25rem 0; }
      `}</style>
      <div
        ref={ref}
        className="norm-html-field"
        contentEditable
        suppressContentEditableWarning
        data-placeholder={placeholder}
        onInput={(e) => onChange((e.currentTarget as HTMLDivElement).innerHTML)}
        style={{
          minHeight,
          maxHeight: 260,
          overflowY: 'auto',
          padding: '8px 10px',
          fontSize: '0.85rem',
          lineHeight: 1.45,
          color: colors.textPrimary,
          border: `1px solid ${colors.border}`,
          borderRadius: 6,
          background: '#fff',
          fontFamily: 'inherit',
          boxSizing: 'border-box',
        }}
      />
    </>
  );
}
