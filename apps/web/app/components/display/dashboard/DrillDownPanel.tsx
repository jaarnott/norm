'use client';

import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

interface DrillDownPanelProps {
  title: string;
  rows: Record<string, unknown>[];
  onClose: () => void;
}

export default function DrillDownPanel({ title, rows, onClose }: DrillDownPanelProps) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  if (rows.length === 0) return null;

  const columns = Object.keys(rows[0]).filter(k => !k.startsWith('_'));

  return createPortal(
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        backgroundColor: 'rgba(0, 0, 0, 0.4)',
        display: 'flex', justifyContent: 'flex-end',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '50vw', maxWidth: 600, minWidth: 320, height: '100vh',
          backgroundColor: '#fff', boxShadow: '-4px 0 20px rgba(0,0,0,0.1)',
          display: 'flex', flexDirection: 'column',
          animation: 'slideIn 0.2s ease-out',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.75rem 1rem', borderBottom: '1px solid #f0ebe5',
        }}>
          <div>
            <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#333' }}>{title}</div>
            <div style={{ fontSize: '0.68rem', color: '#999' }}>{rows.length} row{rows.length !== 1 ? 's' : ''}</div>
          </div>
          <button onClick={onClose} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 32, height: 32, border: 'none', borderRadius: 6,
            backgroundColor: '#f5f5f5', cursor: 'pointer',
          }}>
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        {/* Data table */}
        <div style={{ flex: 1, overflow: 'auto', padding: '0.5rem' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
            <thead>
              <tr>
                {columns.map(col => (
                  <th key={col} style={{
                    padding: '6px 10px', textAlign: 'left', fontSize: '0.68rem',
                    fontWeight: 600, color: '#888', borderBottom: '2px solid #f0ebe5',
                    whiteSpace: 'nowrap', textTransform: 'uppercase', letterSpacing: '0.03em',
                  }}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} style={{ borderBottom: '1px solid #f8f8f5' }}>
                  {columns.map(col => (
                    <td key={col} style={{ padding: '6px 10px', color: '#333' }}>
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
