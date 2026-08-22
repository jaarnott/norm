'use client';

// Attaching files to a chat message. Shared by every composer (the in-thread
// InputBar, the HomePanel landing box, and a FunctionalPage's box) so the
// upload/chip logic lives in one place. Each file is POSTed to /api/uploads the
// moment it's picked (returns an id); on send the ids ride the message as
// attachment_ids, and the backend re-reads the bytes and hands them to the
// model.

import { useState, useRef, useCallback } from 'react';
import { Paperclip, FileText, X } from 'lucide-react';
import { apiFetch } from '../../lib/api';

export interface PendingAttachment {
  upload_id: string;
  filename: string;
  content_type?: string;
}

/** Second argument every composer's onSend passes back up to page.tsx. */
export interface SendOptions {
  pageContext?: { page_id: string; agent: string };
  attachments?: PendingAttachment[];
}

// What the file picker offers. PDFs/images go to the model as native blocks;
// Office and text files are extracted to text server-side (app/services/attachments.py).
export const ATTACH_ACCEPT = [
  '.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif',
  '.docx', '.xlsx', '.pptx',
  '.txt', '.md', '.csv', '.json',
  'image/*',
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'text/plain', 'text/csv', 'text/markdown', 'application/json',
].join(',');

/** Upload-and-track the files a composer has staged for its next send. */
export function useComposerAttachments(venueId?: string | null) {
  const [items, setItems] = useState<PendingAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addFiles = useCallback(async (files: FileList | File[] | null) => {
    const list = files ? Array.from(files) : [];
    if (list.length === 0) return;
    setError(null);
    setUploading(true);
    try {
      for (const file of list) {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('extraction_target', 'chat');
        if (venueId) fd.append('venue_id', venueId);
        const res = await apiFetch('/api/uploads', { method: 'POST', body: fd });
        if (!res.ok) {
          const t = await res.json().catch(() => ({}));
          throw new Error((t as { detail?: string }).detail || `Couldn't upload ${file.name}`);
        }
        const j = await res.json();
        setItems(prev => [...prev, {
          upload_id: j.id,
          filename: j.filename || file.name,
          content_type: j.content_type || file.type,
        }]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  }, [venueId]);

  const remove = useCallback((id: string) => {
    setItems(prev => prev.filter(a => a.upload_id !== id));
  }, []);
  const clear = useCallback(() => setItems([]), []);

  return { items, uploading, error, addFiles, remove, clear };
}

/** The paperclip button + its own hidden file input. */
export function AttachButton({ onPick, disabled }: {
  onPick: (files: FileList | null) => void;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={ref}
        type="file"
        multiple
        accept={ATTACH_ACCEPT}
        style={{ display: 'none' }}
        onChange={e => { onPick(e.target.files); e.target.value = ''; }}
      />
      <button
        type="button"
        data-testid="attach-btn"
        disabled={disabled}
        title="Attach files"
        onClick={() => ref.current?.click()}
        style={{
          height: 50, width: 44, flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          backgroundColor: 'transparent', color: disabled ? '#ccc' : '#888',
          border: '1px solid #ddd', borderRadius: 24,
          cursor: disabled ? 'not-allowed' : 'pointer',
        }}
      >
        <Paperclip size={18} />
      </button>
    </>
  );
}

const chipStyle: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 5,
  fontSize: '0.75rem', color: '#444',
  backgroundColor: '#f5f0ea', border: '1px solid #e6ddd0',
  borderRadius: 14, padding: '3px 8px', maxWidth: 240,
};

/** Removable chips for files staged in a composer (pre-send). */
export function AttachmentChips({ items, remove, uploading }: {
  items: PendingAttachment[];
  remove: (id: string) => void;
  uploading?: boolean;
}) {
  if (items.length === 0 && !uploading) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, maxWidth: 768, margin: '0 auto 8px', width: '100%' }}>
      {items.map(a => (
        <span key={a.upload_id} data-testid="attach-chip" style={chipStyle}>
          <FileText size={13} style={{ flexShrink: 0 }} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.filename}</span>
          <X
            size={13}
            style={{ cursor: 'pointer', flexShrink: 0, color: '#999' }}
            onClick={() => remove(a.upload_id)}
          />
        </span>
      ))}
      {uploading && <span style={{ ...chipStyle, color: '#999' }}>Uploading…</span>}
    </div>
  );
}

/** Read-only chips shown inside a sent user bubble in the transcript. */
export function SentAttachmentChips({ attachments }: {
  attachments?: { filename: string; content_type?: string }[];
}) {
  if (!attachments || attachments.length === 0) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8, justifyContent: 'flex-end' }}>
      {attachments.map((a, i) => (
        <span key={i} style={chipStyle}>
          <FileText size={13} style={{ flexShrink: 0 }} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.filename}</span>
        </span>
      ))}
    </div>
  );
}
