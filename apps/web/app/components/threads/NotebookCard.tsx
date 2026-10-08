'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, FileText } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { NotebookFile } from '../../types';

/**
 * The conversation's notebook — the memory tool's files, as the model keeps
 * them. Rendered as markdown, whatever shape the model chose: tested, it
 * writes a sensible file unprompted (headings per item, a status line) and
 * never a checklist, so nothing here parses or counts. "Progress" is
 * whatever the model wrote.
 */
export default function NotebookCard({ files }: { files: NotebookFile[] }) {
  // Collapsed by default: the notes are Norm's working, not the answer.
  const [open, setOpen] = useState(false);
  if (!files || files.length === 0) return null;
  const when = files
    .map(f => f.updated_at)
    .filter(Boolean)
    .sort()
    .slice(-1)[0];
  return (
    <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', margin: '0.5rem 0', background: 'var(--bg)' }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', padding: '8px 12px', background: 'none', border: 'none', cursor: 'pointer', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', textAlign: 'left' }}
      >
        {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
        <FileText size={14} aria-hidden style={{ color: 'var(--icon)' }} />
        <span style={{ fontWeight: 600, color: 'var(--text)' }}>Working notes</span>
        <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>
          {files.length === 1 ? files[0].path.replace(/^\/memories\//, '') : `${files.length} files`}
          {when ? ` · ${new Date(when).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
        </span>
      </button>
      {open && files.map(f => (
        <div key={f.path} style={{ padding: '8px 14px 12px', borderTop: '1px solid var(--line-soft)', fontSize: 'var(--fs-sm)', lineHeight: 1.55, color: 'var(--text)' }}>
          {files.length > 1 && <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)', margin: '4px 0' }}>{f.path}</div>}
          <div className="markdown-message">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{f.content}</ReactMarkdown>
          </div>
        </div>
      ))}
    </div>
  );
}
