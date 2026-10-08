'use client';

import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import Chart from '../Chart';
import IconButton from '../../ui/IconButton';
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
        backgroundColor: 'rgba(26, 26, 26, 0.35)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={chart.title}
        onClick={e => e.stopPropagation()}
        style={{
          width: '92vw', height: '85vh',
          backgroundColor: 'var(--bg)', borderRadius: 'var(--radius-lg)',
          boxShadow: '0 12px 40px rgba(26, 26, 26, 0.18)',
          overflow: 'hidden', position: 'relative',
          display: 'flex', flexDirection: 'column', color: 'var(--text)', lineHeight: 1.45,
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
          padding: '12px 12px 12px 24px', borderBottom: '1px solid var(--line)',
        }}>
          <h2 style={{
            margin: 0, minWidth: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3,
            color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>{chart.title}</h2>
          <IconButton icon={X} label="Close" onClick={onClose} />
        </div>

        {/* Chart — the modal is the frame, so the chart drops its own border */}
        <div style={{ flex: 1, padding: 12, overflow: 'auto' }}>
          <Chart
            data={{ rows: chart.data, ...chart.chart_spec }}
            props={{ ...chart.chart_spec } as Record<string, unknown>}
            hideAddToReport
            hideBorder
            height={600}
          />
        </div>
      </div>
    </div>,
    document.body,
  );
}
