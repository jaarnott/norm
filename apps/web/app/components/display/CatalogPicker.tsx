'use client';

/**
 * The invoice card's picker for a line's stock item, unit or brand — ONE
 * dialog for all three, so they behave alike: search what Loaded has, pick
 * one, or switch to creating a new one (the card supplies that form).
 *
 * A FIXED height in every mode: the match list fills the space and scrolls,
 * so the box doesn't shrink and jump as the search narrows or a form's notes
 * come and go.
 *
 * Its own small dialog rather than ui/Dialog because it also runs inside
 * Claude's card, where the iframe is as tall as the card: a full-page
 * overlay would centre the box far from the line that was tapped. There it
 * is pinned at the tapped line instead (`top`).
 */

import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { Check, Plus, X } from 'lucide-react';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';

/** A touch screen: no field takes focus by itself there. Focusing raises the
 *  keyboard, and iOS then shifts the page to keep the field in view — by a
 *  different amount for every line, so the dialog seemed to open somewhere
 *  new each time. Without it, the dialog opens centred and stays put until
 *  the person taps the field. */
export const autoFocusField = () =>
  typeof window === 'undefined' || !window.matchMedia?.('(pointer: coarse)').matches;

export interface PickerOption {
  id: string;
  name: string;
  sub?: string | null;
}

export function PickerDialog({ title, subtitle, top, onClose, children }: {
  title: string;
  subtitle?: string | null;
  /** Inside Claude's card: pin the box this far from the card's top. */
  top?: number;
  onClose: () => void;
  children: ReactNode;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); closeRef.current(); }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  const pinned = typeof top === 'number';
  // The page behind stays still while the dialog is open: a scroll there is
  // another way iOS moves a fixed box. (Not inside Claude's card — that
  // frame is the card's own height and never scrolls.)
  useEffect(() => {
    if (pinned) return;
    const body = document.body;
    const before = body.style.overflow;
    body.style.overflow = 'hidden';
    return () => { body.style.overflow = before; };
  }, [pinned]);
  return (
    <div
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: pinned ? 'absolute' : 'fixed', inset: 0, zIndex: 1000, padding: 16,
        display: 'flex', justifyContent: 'center',
        alignItems: pinned ? 'flex-start' : 'center',
        paddingTop: pinned ? Math.max(16, top - 40) : 16,
        backgroundColor: 'rgba(26, 26, 26, 0.35)',
      }}
    >
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 460, maxWidth: '100%', height: pinned ? 480 : 'min(520px, 85vh)', overflow: 'hidden',
          boxSizing: 'border-box', padding: 20, borderRadius: 'var(--radius-lg)',
          backgroundColor: 'var(--bg)', boxShadow: '0 12px 40px rgba(26, 26, 26, 0.18)',
          display: 'flex', flexDirection: 'column', gap: 12, lineHeight: 1.45,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 'var(--fs-lg)', fontWeight: 600, color: 'var(--text)' }}>{title}</div>
            {subtitle && (
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', overflowWrap: 'anywhere' }}>{subtitle}</div>
            )}
          </div>
          <IconButton icon={X} label="Close" onClick={onClose} style={{ margin: '-6px -8px 0 0' }} />
        </div>
        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 12, overflowY: 'auto' }}>
          {children}
        </div>
      </div>
    </div>
  );
}

/** Search, the matches, and the way out to "create a new one". */
export function PickerList({
  query, onQuery, placeholder, options, currentId, onPick, noneLabel, onNone, createLabel, onCreate, emptyText, hint,
}: {
  query: string;
  onQuery: (q: string) => void;
  placeholder: string;
  options: PickerOption[];
  currentId?: string | null;
  onPick: (opt: PickerOption) => void;
  /** An explicit "nothing" choice (a brand). */
  noneLabel?: string;
  onNone?: () => void;
  createLabel: string;
  onCreate: () => void;
  emptyText: string;
  /** A small line above the list ("Best matches for this line"). */
  hint?: string | null;
}) {
  // The current choice is ticked AND in view when the list opens. Scrolled by
  // moving the LIST only — scrollIntoView can move the page too (iOS), which
  // shifts the dialog.
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const list = listRef.current;
    const sel = list?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (list && sel && sel.offsetTop + sel.offsetHeight > list.clientHeight) {
      list.scrollTop = sel.offsetTop - 8;
    }
  }, []);
  return (
    <>
      <input type="text" className="n-input" value={query} placeholder={placeholder} autoFocus={autoFocusField()}
        onChange={(e) => onQuery(e.target.value)} style={{ width: '100%' }} />
      {hint && <div style={{ marginBottom: -6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{hint}</div>}
      <div ref={listRef} className="scroll-quiet" role="listbox"
        style={{ position: 'relative', flex: 1, minHeight: 0, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: '4px 0' }}>
        {noneLabel && onNone && (
          <button type="button" className="n-option" role="option" aria-selected={!currentId} onClick={onNone}
            style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-soft)' }}>
            <span style={{ flex: 1 }}>{noneLabel}</span>
            {!currentId && <Icon icon={Check} size={14} />}
          </button>
        )}
        {options.map((o) => (
          <button key={o.id} type="button" className="n-option" role="option" aria-selected={o.id === currentId}
            onClick={() => onPick(o)} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ flex: 1, minWidth: 0 }}>
              {o.name}
              {o.sub ? <span style={{ color: 'var(--muted)' }}>{` · ${o.sub}`}</span> : null}
            </span>
            {o.id === currentId && <Icon icon={Check} size={14} />}
          </button>
        ))}
        {!options.length && (
          <div style={{ padding: '8px 12px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{emptyText}</div>
        )}
      </div>
      <Button variant="secondary" size="sm" icon={Plus} onClick={onCreate} style={{ alignSelf: 'flex-start' }}>
        {createLabel}
      </Button>
    </>
  );
}
