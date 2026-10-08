'use client';

import { useCallback, useRef, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import { useComposerAttachments, AttachButton, AttachmentChips, type PendingAttachment } from './AttachmentComposer';

const MAX_HEIGHT = 150;

/** Grow the box with its text, up to MAX_HEIGHT, then scroll inside it. */
function fit(el: HTMLTextAreaElement | null) {
  if (!el) return;
  el.style.height = 'auto';
  const h = Math.min(el.scrollHeight, MAX_HEIGHT);
  el.style.height = h + 'px';
  el.style.overflow = h >= MAX_HEIGHT ? 'auto' : 'hidden';
}

/**
 * The one message box — Home, a conversation and every page use it, so they
 * look and behave the same: attach button, a white field with a visible edge,
 * a near-black Send. The caller places it (centred on Home, docked at the
 * bottom elsewhere) and decides what a send carries.
 */
export default function Composer({ onSend, loading, venueId, highlight = false, inputTestId, sendTestId, placeholder = 'Message Norm…' }: {
  onSend: (text: string, attachments: PendingAttachment[]) => void;
  loading: boolean;
  /** Uploads are tagged with the venue the page is looking at. */
  venueId?: string | null;
  /** A thread is waiting on the user: the field's edge turns tan. */
  highlight?: boolean;
  inputTestId?: string;
  sendTestId?: string;
  placeholder?: string;
}) {
  const [value, setValue] = useState('');
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const att = useComposerAttachments(venueId);

  // A message needs text (routing keys off it); attachments ride alongside.
  const submit = useCallback(() => {
    if (!value.trim()) return;
    onSend(value, att.items);
    setValue('');
    att.clear();
    requestAnimationFrame(() => fit(ref.current));
  }, [value, onSend, att]);

  return (
    // pointer-events: the composer may float over a page whose fade lets
    // clicks through; the composer itself always takes them.
    <div style={{ maxWidth: 768, margin: '0 auto', width: '100%', pointerEvents: 'auto' }}>
      <AttachmentChips items={att.items} remove={att.remove} uploading={att.uploading} />
      <form
        onSubmit={(e) => { e.preventDefault(); submit(); }}
        style={{ display: 'flex', alignItems: 'flex-end', gap: 6 }}
      >
        <AttachButton onPick={att.addFiles} disabled={loading} />
        <textarea
          ref={(el) => { ref.current = el; fit(el); }}
          data-testid={inputTestId}
          aria-label="Message Norm"
          value={value}
          onChange={(e) => { setValue(e.target.value); fit(e.target); }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={placeholder}
          rows={1}
          style={{
            flex: 1,
            minWidth: 0,
            minHeight: 50,
            maxHeight: MAX_HEIGHT,
            padding: '13px 20px',
            fontSize: 'var(--fs-md)',
            lineHeight: 1.4,
            color: 'var(--text)',
            background: 'var(--bg)',
            border: `1px solid ${highlight ? 'var(--accent)' : 'var(--field)'}`,
            borderRadius: 24,
            resize: 'none',
            boxSizing: 'border-box',
            overflow: 'hidden',
          }}
        />
        <button
          type="submit"
          data-testid={sendTestId}
          disabled={loading}
          aria-label={loading ? 'Sending' : undefined}
          style={{
            flex: '0 0 auto',
            minWidth: 72,
            height: 50,
            padding: '0 20px',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 'var(--fs-base)',
            fontWeight: 600,
            color: 'var(--on-primary)',
            background: 'var(--primary)',
            border: 'none',
            borderRadius: 24,
            cursor: loading ? 'not-allowed' : 'pointer',
          }}
        >
          {loading
            ? <LoaderCircle size={18} strokeWidth={2} aria-hidden style={{ animation: 'n-spin 1s linear infinite' }} />
            : 'Send'}
        </button>
      </form>
    </div>
  );
}
