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
      {/* It draws its own edge like a text field's: --field at rest, --focus
          with the same soft ring the inputs get when focused (globals.css
          leaves contenteditable areas to do this themselves). The look lives
          here, not inline: an inline border would beat the :focus rule. */}
      <style>{`
        .norm-html-field {
          overflow-y: auto; box-sizing: border-box; padding: 8px 10px;
          border: 1px solid var(--field); border-radius: var(--radius); background: var(--bg);
          font-family: inherit; font-size: var(--fs-base); line-height: 1.45; color: var(--text);
        }
        .norm-html-field:empty:before { content: attr(data-placeholder); color: var(--muted); }
        .norm-html-field:focus { outline: none; border-color: var(--focus); box-shadow: 0 0 0 2px color-mix(in srgb, var(--focus) 35%, transparent); }
        .norm-html-field ol, .norm-html-field ul { margin: 4px 0 4px 18px; padding: 0; }
        .norm-html-field li { margin: 2px 0; }
        .norm-html-field p { margin: 4px 0; }
      `}</style>
      <div
        ref={ref}
        className="norm-html-field"
        contentEditable
        suppressContentEditableWarning
        data-placeholder={placeholder}
        onInput={(e) => onChange((e.currentTarget as HTMLDivElement).innerHTML)}
        style={{ minHeight, maxHeight: 260 }}
      />
    </>
  );
}
