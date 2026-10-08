'use client';

import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import IconButton from '../../ui/IconButton';

interface DrillDownPanelProps {
  title: string;
  rows: Record<string, unknown>[];
  onClose: () => void;
}

/** A raw data key as a sentence-case heading: order_count / orderCount → "Order count". */
function columnLabel(key: string): string {
  const words = key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim()
    .replace(/ ([A-Z])(?=[a-z])/g, (_, c: string) => ` ${c.toLowerCase()}`);
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// The drawer's text inset: the title and the table's first/last columns line up on it.
const INSET = 20;

export default function DrillDownPanel({ title, rows, onClose }: DrillDownPanelProps) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  if (rows.length === 0) return null;

  const columns = Object.keys(rows[0]).filter(k => !k.startsWith('_'));
  // Number columns right-align (.n-table .num), header included.
  const numeric = new Set(columns.filter(col =>
    rows.some(r => typeof r[col] === 'number') && rows.every(r => r[col] == null || typeof r[col] === 'number')));
  const edge = (i: number) => ({
    ...(i === 0 ? { paddingLeft: INSET } : {}),
    ...(i === columns.length - 1 ? { paddingRight: INSET } : {}),
  });

  return createPortal(
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        backgroundColor: 'rgba(26, 26, 26, 0.35)',
        display: 'flex', justifyContent: 'flex-end',
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={e => e.stopPropagation()}
        style={{
          width: '50vw', maxWidth: 600, minWidth: 320, height: '100%',
          backgroundColor: 'var(--bg)', borderLeft: '1px solid var(--line)', boxShadow: '-8px 0 32px rgba(26, 26, 26, 0.12)',
          display: 'flex', flexDirection: 'column', color: 'var(--text)', lineHeight: 1.45,
          animation: 'slideIn 0.2s ease-out',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12,
          padding: `14px 12px 14px ${INSET}px`, borderBottom: '1px solid var(--line)',
        }}>
          <div style={{ minWidth: 0, paddingTop: 4 }}>
            <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.35, color: 'var(--text)' }}>{title}</h2>
            <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{rows.length} row{rows.length !== 1 ? 's' : ''}</div>
          </div>
          <IconButton icon={X} label="Close" onClick={onClose} />
        </div>

        {/* Data table */}
        <div style={{ flex: 1, overflow: 'auto' }}>
          <table className="n-table">
            <thead>
              <tr>
                {columns.map((col, ci) => (
                  <th key={col} className={numeric.has(col) ? 'num' : undefined} style={edge(ci)}>{columnLabel(col)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {columns.map((col, ci) => (
                    <td key={col} className={numeric.has(col) ? 'num' : undefined} style={edge(ci)}>
                      {typeof row[col] === 'number'
                        ? (row[col] as number).toLocaleString('en-NZ', { maximumFractionDigits: 2 })
                        : String(row[col] ?? '')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <style>{`@keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }`}</style>
    </div>,
    document.body,
  );
}
