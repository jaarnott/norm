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
  const [open, setOpen] = useState(true);
  if (!files || files.length === 0) return null;
  const when = files
    .map(f => f.updated_at)
    .filter(Boolean)
    .sort()
    .slice(-1)[0];
  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, margin: '0.5rem 0', background: '#fafafa' }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', padding: '6px 10px', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.8rem', color: '#555' }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <FileText size={14} />
        <span style={{ fontWeight: 600 }}>Working notes</span>
        <span style={{ marginLeft: 'auto', color: '#888' }}>
          {files.length === 1 ? files[0].path.replace(/^\/memories\//, '') : `${files.length} files`}
          {when ? ` · ${new Date(when).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
        </span>
      </button>
      {open && files.map(f => (
        <div key={f.path} style={{ padding: '4px 14px 10px', borderTop: '1px solid #eee', fontSize: '0.85rem' }}>
          {files.length > 1 && <div style={{ color: '#888', fontSize: '0.75rem', margin: '4px 0' }}>{f.path}</div>}
          <div className="notebook-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{f.content}</ReactMarkdown>
          </div>
        </div>
      ))}
    </div>
  );
}
