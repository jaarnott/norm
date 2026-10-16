'use client';

/**
 * A small rich-text editor for reply templates: bold, italic, lists, links
 * and placeholders, over a contentEditable area. What it is given is cleaned
 * first (richText.cleanHtml) — template bodies come from other staff and the
 * Orbit import, and innerHTML would otherwise run whatever they contain.
 *
 * Uncontrolled between resets, like HtmlField: React writing innerHTML on
 * every keystroke would fight the caret, so the DOM is written only when
 * `resetKey` changes (another template opened) and read back on input.
 */
import { useEffect, useRef } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Bold, Italic, Link2, List, ListOrdered, RemoveFormatting } from 'lucide-react';
import Icon from '../../ui/Icon';
import { cleanHtml, safeHref } from './richText';

const TOOLS: { icon: LucideIcon; label: string; run: () => void }[] = [
  { icon: Bold, label: 'Bold', run: () => document.execCommand('bold') },
  { icon: Italic, label: 'Italic', run: () => document.execCommand('italic') },
  { icon: List, label: 'Bulleted list', run: () => document.execCommand('insertUnorderedList') },
  { icon: ListOrdered, label: 'Numbered list', run: () => document.execCommand('insertOrderedList') },
  {
    icon: Link2,
    label: 'Link',
    run: () => {
      const url = window.prompt('Link to (https://, mailto: or a placeholder like {{quote_link}})');
      const href = safeHref(url);
      if (href) document.execCommand('createLink', false, href);
    },
  },
  { icon: RemoveFormatting, label: 'Clear formatting', run: () => document.execCommand('removeFormat') },
];

export default function RichTextEditor({ html, resetKey, onChange, disabled, labelledBy, placeholders = [] }: {
  html: string;
  resetKey: string;
  onChange: (html: string) => void;
  disabled?: boolean;
  labelledBy?: string;
  placeholders?: string[];
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.innerHTML = cleanHtml(html);
    // Only when another template is opened — see the note above.
  }, [resetKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const apply = (run: () => void) => {
    if (disabled || !ref.current) return;
    ref.current.focus();
    run();
    onChange(ref.current.innerHTML);
  };

  const insert = (text: string) => apply(() => document.execCommand('insertText', false, text));

  return (
    <div style={{ border: '1px solid var(--field)', borderRadius: 'var(--radius)', background: 'var(--bg)', overflow: 'hidden' }}>
      <style>{`
        .fn-rte { min-height: 180px; max-height: 420px; overflow-y: auto; padding: 10px 12px; outline: none;
          font-size: var(--fs-base); line-height: 1.5; color: var(--text); }
        .fn-rte:focus { box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--focus) 35%, transparent); }
        .fn-rte p { margin: 0 0 8px; }
        .fn-rte ul, .fn-rte ol { margin: 4px 0 8px 20px; padding: 0; }
        .fn-rte a { color: var(--accent); }
        .fn-rte[contenteditable="false"] { background: var(--surface); color: var(--text-soft); }
      `}</style>
      {!disabled && (
        <div role="toolbar" aria-label="Formatting" style={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', padding: 4, borderBottom: '1px solid var(--line)', background: 'var(--surface)' }}>
          {TOOLS.map((t) => (
            <button
              key={t.label}
              type="button"
              className="n-icon-btn"
              aria-label={t.label}
              title={t.label}
              // Keep the text selection: a mousedown on the button would move focus.
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => apply(t.run)}
            >
              <Icon icon={t.icon} size={16} />
            </button>
          ))}
          {placeholders.length > 0 && (
            <select
              className="n-select"
              aria-label="Insert a placeholder"
              value=""
              onChange={(e) => { if (e.target.value) insert(e.target.value); }}
              style={{ height: 30, marginLeft: 'auto', fontSize: 'var(--fs-sm)' }}
            >
              <option value="">Insert placeholder…</option>
              {placeholders.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          )}
        </div>
      )}
      <div
        ref={ref}
        className="fn-rte"
        role="textbox"
        aria-multiline="true"
        aria-labelledby={labelledBy}
        contentEditable={!disabled}
        suppressContentEditableWarning
        onInput={(e) => onChange((e.currentTarget as HTMLDivElement).innerHTML)}
      />
    </div>
  );
}
