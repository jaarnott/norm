'use client';

/**
 * Rich text for the composer: Norm's HtmlField (contentEditable, as on the
 * recipe method) under a small toolbar — bold, italic, lists, link. The
 * toolbar works on any editable document, so the proposal editor puts it over
 * its sandboxed iframe too (`doc` = the iframe's document).
 */
import { useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { Bold, Image as ImageIcon, Italic, Link2, List, ListOrdered } from 'lucide-react';
import HtmlField from '../../display/HtmlField';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import { escapeHtml, normaliseUrl } from './text';

// Toolbar buttons must not take focus, or the editor loses its selection.
const keepSelection = (e: MouseEvent) => e.preventDefault();

function Tool({ label, icon, onClick, disabled }: { label: string; icon: typeof Bold; onClick: () => void; disabled: boolean }) {
  return <IconButton icon={icon} label={label} iconSize={16} onMouseDown={keepSelection} onClick={onClick} disabled={disabled} />;
}

export function RichToolbar({
  doc,
  focus,
  disabled = false,
  onImage,
  children,
}: {
  /** The document the editor lives in (execCommand runs there). */
  doc: () => Document | null;
  /** Put the caret back in the editor (the link box takes focus). */
  focus?: () => void;
  disabled?: boolean;
  /** Shows an image button (the proposal editor's library). */
  onImage?: () => void;
  /** Extra controls at the right end (e.g. the template picker). */
  children?: ReactNode;
}) {
  const [linkOpen, setLinkOpen] = useState(false);
  const [url, setUrl] = useState('');
  const saved = useRef<Range | null>(null);

  const run = (command: string, value?: string) => {
    const d = doc();
    if (d && !disabled) d.execCommand(command, false, value);
  };

  const openLink = () => {
    const sel = doc()?.getSelection();
    saved.current = sel && sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
    setUrl('');
    setLinkOpen(true);
  };

  const href = normaliseUrl(url);
  const applyLink = () => {
    const d = doc();
    if (!d || !href) return;
    focus?.();
    const sel = d.getSelection();
    if (sel && saved.current) {
      sel.removeAllRanges();
      sel.addRange(saved.current);
    }
    if (!sel || sel.isCollapsed) d.execCommand('insertHTML', false, `<a href="${escapeHtml(href)}">${escapeHtml(url.trim())}</a>`);
    else d.execCommand('createLink', false, href);
    setLinkOpen(false);
  };

  return (
    <div>
      <div
        role="toolbar"
        aria-label="Formatting"
        style={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', padding: '2px 0 6px' }}
      >
        <Tool label="Bold" icon={Bold} onClick={() => run('bold')} disabled={disabled} />
        <Tool label="Italic" icon={Italic} onClick={() => run('italic')} disabled={disabled} />
        <Tool label="Bulleted list" icon={List} onClick={() => run('insertUnorderedList')} disabled={disabled} />
        <Tool label="Numbered list" icon={ListOrdered} onClick={() => run('insertOrderedList')} disabled={disabled} />
        <Tool label="Link" icon={Link2} onClick={openLink} disabled={disabled} />
        {onImage && <Tool label="Image from the library" icon={ImageIcon} onClick={onImage} disabled={disabled} />}
        {children && <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>{children}</div>}
      </div>
      {linkOpen && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
          <input
            className="n-input"
            aria-label="Link address"
            placeholder="https://… or an email address"
            value={url}
            autoFocus
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                applyLink();
              }
              if (e.key === 'Escape') setLinkOpen(false);
            }}
            style={{ flex: '1 1 220px', minWidth: 0 }}
          />
          <Button size="sm" onClick={applyLink} disabled={!href}>
            Add link
          </Button>
          <Button size="sm" variant="quiet" onClick={() => setLinkOpen(false)}>
            Cancel
          </Button>
        </div>
      )}
    </div>
  );
}

/** The composer body: toolbar + HtmlField. HtmlField is uncontrolled between
 *  resets — bump `resetKey` to replace its content (a template, a draft). */
export function RichTextField({
  html,
  resetKey,
  onChange,
  placeholder,
  minHeight = 140,
  toolbarExtra,
}: {
  html: string;
  resetKey: string;
  onChange: (html: string) => void;
  placeholder?: string;
  minHeight?: number;
  toolbarExtra?: ReactNode;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  return (
    <div ref={wrap}>
      <RichToolbar
        doc={() => (typeof document === 'undefined' ? null : document)}
        focus={() => wrap.current?.querySelector<HTMLElement>('.norm-html-field')?.focus()}
      >
        {toolbarExtra}
      </RichToolbar>
      <HtmlField html={html} resetKey={resetKey} onChange={onChange} placeholder={placeholder} minHeight={minHeight} />
    </div>
  );
}
