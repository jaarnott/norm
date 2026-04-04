'use client';

import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import Chart from '../Chart';
import type { SavedReportChart } from '../../../types';

interface ChartFullScreenModalProps {
  chart: SavedReportChart;
  onClose: () => void;
}

export default function ChartFullScreenModal({ chart, onClose }: ChartFullScreenModalProps) {
  // Close on ESC
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return createPortal(
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        backgroundColor: 'rgba(0, 0, 0, 0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '92vw', height: '85vh',
          backgroundColor: '#fff', borderRadius: 12,
          overflow: 'hidden', position: 'relative',
          display: 'flex', flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.75rem 1rem', borderBottom: '1px solid #f0ebe5',
        }}>
          <span style={{ fontSize: '0.9rem', fontWeight: 600, color: '#333' }}>{chart.title}</span>
          <button onClick={onClose} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 32, height: 32, border: 'none', borderRadius: 6,
            backgroundColor: '#f5f5f5', cursor: 'pointer',
          }}>
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        {/* Chart */}
        <div style={{ flex: 1, padding: '1rem', overflow: 'auto' }}>
          <Chart
            data={{ rows: chart.data, ...chart.chart_spec }}
            props={{ ...chart.chart_spec } as Record<string, unknown>}
            hideAddToReport
            height={600}
          />
        </div>
      </div>
    </div>,
    document.body,
  );
}
