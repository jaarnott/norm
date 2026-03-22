'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '../../lib/api';
import type { SavedReport, SavedReportChart, ReportGridItem } from '../../types';
import Chart from './Chart';

const ROW_HEIGHT = 40; // px per grid row
const GRID_COLS = 24;

interface Props {
  data: { report_id?: string };
  props?: Record<string, unknown>;
}

export default function ReportBuilder({ data }: Props) {
  const [report, setReport] = useState<SavedReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');

  const reportId = data?.report_id;

  const fetchReport = useCallback(async () => {
    if (!reportId) return;
    try {
      const res = await apiFetch(`/api/reports/${reportId}`);
      if (res.ok) {
        const r = await res.json();
        setReport(r);
        setTitleDraft(r.title);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [reportId]);

  useEffect(() => { fetchReport(); }, [fetchReport]);

  // Re-fetch when the data prop changes (e.g., new chart added via "+ Report")
  useEffect(() => {
    if (reportId && !loading) fetchReport();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // --- Layout helpers ---

  const getChart = (chartId: string) => report?.charts.find(c => c.id === chartId);

  // Migrate any old layout formats to grid items
  const layout: ReportGridItem[] = (() => {
    const raw = report?.layout || [];
    if (raw.length === 0) {
      // Auto-generate from charts
      return (report?.charts || []).map((chart, i) => ({
        chart_id: chart.id, col: 1, row: 1 + i * 8, colSpan: GRID_COLS, rowSpan: 8,
      }));
    }
    // Check if already grid format
    if (raw[0] && 'colSpan' in raw[0]) return raw as ReportGridItem[];
    // Old row-based or flat format — convert
    const items: ReportGridItem[] = [];
    let nextRow = 1;
    for (const item of raw) {
      const r = item as Record<string, unknown>;
      if ('columns' in r && Array.isArray(r.columns)) {
        // Row-based format
        let colOffset = 1;
        for (const col of r.columns as { chart_id: string; width: number }[]) {
          items.push({ chart_id: col.chart_id, col: colOffset, row: nextRow, colSpan: Math.round((col.width || 12) * GRID_COLS / 12), rowSpan: 8 });
          colOffset += col.width || 12;
        }
        nextRow += 4;
      } else if ('chart_id' in r) {
        // Flat format
        items.push({ chart_id: r.chart_id as string, col: 1, row: nextRow, colSpan: GRID_COLS, rowSpan: 8 });
        nextRow += 4;
      }
    }
    // Validate against actual charts
    const chartIds = new Set(report?.charts.map(c => c.id) || []);
    const valid = items.filter(i => chartIds.has(i.chart_id));
    if (valid.length > 0) return valid;
    // Fallback
    return (report?.charts || []).map((chart, i) => ({
      chart_id: chart.id, col: 1, row: 1 + i * 4, colSpan: 12, rowSpan: 4,
    }));
  })();

  const updateLayout = async (newLayout: ReportGridItem[]) => {
    if (!reportId || !report) return;
    setReport({ ...report, layout: newLayout });
    await apiFetch(`/api/reports/${reportId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ layout: newLayout }),
    });
  };

  const updateReport = async (patch: Record<string, unknown>) => {
    if (!reportId) return;
    const res = await apiFetch(`/api/reports/${reportId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (res.ok) setReport(await res.json());
  };

  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [dragPreview, setDragPreview] = useState<{ chartId: string; col: number; row: number; colSpan: number; rowSpan: number } | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  const deleteChart = async (chartId: string) => {
    if (!reportId) return;
    await apiFetch(`/api/reports/${reportId}/charts/${chartId}`, { method: 'DELETE' });
    setConfirmDelete(null);
    fetchReport();
  };

  const refreshAll = async () => {
    if (!reportId) return;
    setRefreshing(true);
    try {
      const res = await apiFetch(`/api/reports/${reportId}/refresh`, { method: 'POST' });
      if (res.ok) setReport(await res.json());
    } catch { /* ignore */ }
    setRefreshing(false);
  };

  const updateGridItem = (chartId: string, patch: Partial<ReportGridItem>) => {
    const newLayout = layout.map(item =>
      item.chart_id === chartId ? { ...item, ...patch } : item
    );
    updateLayout(newLayout);
  };

  const handleMoveStart = (chartId: string, e: React.MouseEvent) => {
    const item = layout.find(i => i.chart_id === chartId);
    if (!item || !gridRef.current) return;
    e.preventDefault();
    const gridRect = gridRef.current.getBoundingClientRect();
    const colWidth = gridRect.width / GRID_COLS;
    const startX = e.clientX;
    const startY = e.clientY;
    const startCol = item.col;
    const startRow = item.row;

    const onMove = (ev: MouseEvent) => {
      const deltaX = ev.clientX - startX;
      const deltaY = ev.clientY - startY;
      const deltaCols = Math.round(deltaX / colWidth);
      const deltaRows = Math.round(deltaY / ROW_HEIGHT);
      const newCol = Math.max(1, Math.min(GRID_COLS + 1 - item.colSpan, startCol + deltaCols));
      const newRow = Math.max(1, startRow + deltaRows);
      setDragPreview({ chartId, col: newCol, row: newRow, colSpan: item.colSpan, rowSpan: item.rowSpan });
    };
    const onUp = (ev: MouseEvent) => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      if (dragPreview) {
        updateGridItem(chartId, { col: dragPreview.col, row: dragPreview.row });
      }
      setDragPreview(null);
    };
    // Use a ref to get the latest dragPreview in onUp
    const onUpWithRef = (ev: MouseEvent) => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUpWithRef);
      const deltaX = ev.clientX - startX;
      const deltaY = ev.clientY - startY;
      const deltaCols = Math.round(deltaX / colWidth);
      const deltaRows = Math.round(deltaY / ROW_HEIGHT);
      const newCol = Math.max(1, Math.min(GRID_COLS + 1 - item.colSpan, startCol + deltaCols));
      const newRow = Math.max(1, startRow + deltaRows);
      if (newCol !== startCol || newRow !== startRow) {
        updateGridItem(chartId, { col: newCol, row: newRow });
      }
      setDragPreview(null);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUpWithRef);
  };

  if (loading) return <div style={{ padding: '1rem', color: '#888' }}>Loading report...</div>;
  if (!report) return <div style={{ padding: '1rem', color: '#888' }}>Report not found.</div>;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '0.5rem 1rem', borderBottom: '1px solid #e2e8f0', backgroundColor: '#fafafa', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {editingTitle ? (
            <input
              value={titleDraft}
              onChange={e => setTitleDraft(e.target.value)}
              onBlur={() => { setEditingTitle(false); updateReport({ title: titleDraft }); }}
              onKeyDown={e => { if (e.key === 'Enter') { setEditingTitle(false); updateReport({ title: titleDraft }); } }}
              autoFocus
              style={{ fontSize: '0.95rem', fontWeight: 700, border: '1px solid #ccc', borderRadius: 4, padding: '2px 6px', fontFamily: 'inherit' }}
            />
          ) : (
            <h2
              onClick={() => setEditingTitle(true)}
              style={{ fontSize: '0.95rem', fontWeight: 700, color: '#333', margin: 0, cursor: 'pointer' }}
              title="Click to rename"
            >{report.title}</h2>
          )}
          <span style={{ fontSize: '0.7rem', color: '#aaa' }}>{report.charts.length} chart{report.charts.length !== 1 ? 's' : ''}</span>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button
            onClick={refreshAll}
            disabled={refreshing}
            style={{
              padding: '4px 10px', fontSize: '0.72rem', fontWeight: 600,
              border: '1px solid #cbd5e1', borderRadius: 5, backgroundColor: '#fff',
              color: '#555', cursor: refreshing ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
            }}
          >{refreshing ? 'Refreshing...' : 'Refresh All'}</button>
          <button
            onClick={() => updateReport({ status: 'saved' })}
            style={{
              padding: '4px 10px', fontSize: '0.72rem', fontWeight: 600,
              border: 'none', borderRadius: 5, backgroundColor: '#c4a882',
              color: '#fff', cursor: 'pointer', fontFamily: 'inherit',
            }}
          >{report.status === 'saved' ? 'Saved' : 'Save Report'}</button>
        </div>
      </div>

      {/* Grid */}
      <div style={{ flex: 1, overflow: 'auto', padding: '0.75rem' }}>
        {layout.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '3rem', color: '#999', fontSize: '0.85rem' }}>
            No charts yet. Ask Norm for data in the conversation below, then click &ldquo;+ Report&rdquo; on a chart to add it here.
          </div>
        ) : (
          <div
            ref={gridRef}
            style={{
              display: 'grid',
              gridTemplateColumns: `repeat(${GRID_COLS}, 1fr)`,
              gridAutoRows: ROW_HEIGHT,
              gap: 4,
              position: 'relative',
            }}
          >
            {layout.map(item => {
              const chart = getChart(item.chart_id);
              if (!chart) return null;
              return (
                <GridChartCell
                  key={item.chart_id}
                  item={item}
                  chart={chart}
                  onResize={(patch) => updateGridItem(item.chart_id, patch)}
                  onDelete={() => setConfirmDelete(item.chart_id)}
                  onMoveStart={(e) => handleMoveStart(item.chart_id, e)}
                  isBeingDragged={dragPreview?.chartId === item.chart_id}
                />
              );
            })}
            {/* Drop preview ghost */}
            {dragPreview && (
              <div style={{
                gridColumn: `${dragPreview.col} / span ${dragPreview.colSpan}`,
                gridRow: `${dragPreview.row} / span ${dragPreview.rowSpan}`,
                backgroundColor: 'rgba(196, 168, 130, 0.15)',
                border: '2px dashed #c4a882',
                borderRadius: 8,
                pointerEvents: 'none',
              }} />
            )}
          </div>
        )}

        {/* Delete confirmation modal */}
        {confirmDelete && (
          <div style={{
            position: 'fixed', inset: 0, zIndex: 9999,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            backgroundColor: 'rgba(0,0,0,0.3)',
          }}>
            <div style={{
              backgroundColor: '#fff', borderRadius: 12, padding: '1.5rem', maxWidth: 360, width: '90%',
              boxShadow: '0 10px 40px rgba(0,0,0,0.15)', textAlign: 'center',
            }}>
              <div style={{ fontSize: '1.5rem', marginBottom: '0.75rem' }}>&#128465;</div>
              <h3 style={{ fontSize: '0.95rem', fontWeight: 600, color: '#333', margin: '0 0 0.5rem' }}>Remove chart?</h3>
              <p style={{ fontSize: '0.82rem', color: '#888', margin: '0 0 1.25rem' }}>
                This will remove the chart from your report. The underlying data is not affected.
              </p>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
                <button
                  onClick={() => setConfirmDelete(null)}
                  style={{
                    padding: '8px 20px', fontSize: '0.82rem', fontWeight: 500,
                    border: '1px solid #ddd', borderRadius: 8, backgroundColor: '#fff',
                    color: '#666', cursor: 'pointer', fontFamily: 'inherit',
                  }}
                >Cancel</button>
                <button
                  onClick={() => deleteChart(confirmDelete)}
                  style={{
                    padding: '8px 20px', fontSize: '0.82rem', fontWeight: 600,
                    border: 'none', borderRadius: 8, backgroundColor: '#e53e3e',
                    color: '#fff', cursor: 'pointer', fontFamily: 'inherit',
                  }}
                >Remove</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Grid chart cell with resize handles
// ---------------------------------------------------------------------------

function GridChartCell({
  item, chart, onResize, onDelete, onMoveStart, isBeingDragged,
}: {
  item: ReportGridItem;
  chart: SavedReportChart;
  onResize: (patch: Partial<ReportGridItem>) => void;
  onDelete: () => void;
  onMoveStart: (e: React.MouseEvent) => void;
  isBeingDragged?: boolean;
}) {
  const cellRef = useRef<HTMLDivElement>(null);
  const [localCol, setLocalCol] = useState(item.col);
  const [localRow, setLocalRow] = useState(item.row);
  const [localColSpan, setLocalColSpan] = useState(item.colSpan);
  const [localRowSpan, setLocalRowSpan] = useState(item.rowSpan);
  const [isDragging, setIsDragging] = useState(false);

  // Sync local state when item changes from outside
  if (!isDragging) {
    if (localCol !== item.col) setLocalCol(item.col);
    if (localRow !== item.row) setLocalRow(item.row);
    if (localColSpan !== item.colSpan) setLocalColSpan(item.colSpan);
    if (localRowSpan !== item.rowSpan) setLocalRowSpan(item.rowSpan);
  }

  // Right-edge resize (colSpan) — delta-based, commit on mouseup
  const handleWidthResize = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startSpan = item.colSpan;
    const grid = cellRef.current?.parentElement;
    if (!grid) return;
    const colWidth = grid.getBoundingClientRect().width / GRID_COLS;
    setIsDragging(true);

    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientX - startX;
      const deltaCols = Math.round(delta / colWidth);
      setLocalColSpan(Math.max(1, Math.min(GRID_COLS + 1 - item.col, startSpan + deltaCols)));
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      setIsDragging(false);
      const grid2 = cellRef.current?.parentElement;
      if (!grid2) return;
      const colW = grid2.getBoundingClientRect().width / GRID_COLS;
      const delta = (window.event as MouseEvent)?.clientX ?? startX;
      const finalDelta = delta - startX;
      const finalSpan = Math.max(1, Math.min(GRID_COLS + 1 - item.col, startSpan + Math.round(finalDelta / colW)));
      onResize({ colSpan: finalSpan });
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  // Left-edge resize — moves col and adjusts colSpan (right edge stays fixed)
  const handleLeftResize = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startCol = item.col;
    const rightEdge = item.col + item.colSpan; // fixed right edge
    const grid = cellRef.current?.parentElement;
    if (!grid) return;
    const colWidth = grid.getBoundingClientRect().width / GRID_COLS;
    setIsDragging(true);

    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientX - startX;
      const deltaCols = Math.round(delta / colWidth);
      const newCol = Math.max(1, Math.min(rightEdge - 1, startCol + deltaCols));
      setLocalCol(newCol);
      setLocalColSpan(rightEdge - newCol);
    };
    const onUp = (ev: MouseEvent) => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      setIsDragging(false);
      const delta = ev.clientX - startX;
      const deltaCols = Math.round(delta / colWidth);
      const newCol = Math.max(1, Math.min(rightEdge - 1, startCol + deltaCols));
      onResize({ col: newCol, colSpan: rightEdge - newCol });
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  // Top-edge resize — moves row and adjusts rowSpan (bottom edge stays fixed)
  const handleTopResize = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startY = e.clientY;
    const startRow = item.row;
    const bottomEdge = item.row + item.rowSpan; // fixed bottom edge
    setIsDragging(true);

    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientY - startY;
      const deltaRows = Math.round(delta / ROW_HEIGHT);
      const newRow = Math.max(1, Math.min(bottomEdge - 2, startRow + deltaRows));
      setLocalRow(newRow);
      setLocalRowSpan(bottomEdge - newRow);
    };
    const onUp = (ev: MouseEvent) => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      setIsDragging(false);
      const delta = ev.clientY - startY;
      const deltaRows = Math.round(delta / ROW_HEIGHT);
      const newRow = Math.max(1, Math.min(bottomEdge - 2, startRow + deltaRows));
      onResize({ row: newRow, rowSpan: bottomEdge - newRow });
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  // Bottom-edge resize (rowSpan) — delta-based, commit on mouseup
  const handleHeightResize = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startY = e.clientY;
    const startSpan = item.rowSpan;
    setIsDragging(true);

    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientY - startY;
      const deltaRows = Math.round(delta / ROW_HEIGHT);
      setLocalRowSpan(Math.max(2, startSpan + deltaRows));
    };
    const onUp = (ev: MouseEvent) => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      setIsDragging(false);
      const delta = ev.clientY - startY;
      const deltaRows = Math.round(delta / ROW_HEIGHT);
      const finalSpan = Math.max(2, startSpan + deltaRows);
      onResize({ rowSpan: finalSpan });
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  // Corner resize — handles both width and height in one drag
  const handleCornerResize = (
    colEdge: 'left' | 'right',
    rowEdge: 'top' | 'bottom',
  ) => (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startY = e.clientY;
    const startCol = item.col;
    const startRow = item.row;
    const startColSpan = item.colSpan;
    const startRowSpan = item.rowSpan;
    const rightEdge = startCol + startColSpan;
    const bottomEdge = startRow + startRowSpan;
    const grid = cellRef.current?.parentElement;
    if (!grid) return;
    const colWidth = grid.getBoundingClientRect().width / GRID_COLS;
    setIsDragging(true);

    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const deltaCols = Math.round(dx / colWidth);
      const deltaRows = Math.round(dy / ROW_HEIGHT);

      if (colEdge === 'right') {
        setLocalColSpan(Math.max(1, Math.min(GRID_COLS + 1 - startCol, startColSpan + deltaCols)));
      } else {
        const newCol = Math.max(1, Math.min(rightEdge - 1, startCol + deltaCols));
        setLocalCol(newCol);
        setLocalColSpan(rightEdge - newCol);
      }
      if (rowEdge === 'bottom') {
        setLocalRowSpan(Math.max(2, startRowSpan + deltaRows));
      } else {
        const newRow = Math.max(1, Math.min(bottomEdge - 2, startRow + deltaRows));
        setLocalRow(newRow);
        setLocalRowSpan(bottomEdge - newRow);
      }
    };
    const onUp = (ev: MouseEvent) => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      setIsDragging(false);
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const deltaCols = Math.round(dx / colWidth);
      const deltaRows = Math.round(dy / ROW_HEIGHT);
      const patch: Partial<ReportGridItem> = {};

      if (colEdge === 'right') {
        patch.colSpan = Math.max(1, Math.min(GRID_COLS + 1 - startCol, startColSpan + deltaCols));
      } else {
        const newCol = Math.max(1, Math.min(rightEdge - 1, startCol + deltaCols));
        patch.col = newCol;
        patch.colSpan = rightEdge - newCol;
      }
      if (rowEdge === 'bottom') {
        patch.rowSpan = Math.max(2, startRowSpan + deltaRows);
      } else {
        const newRow = Math.max(1, Math.min(bottomEdge - 2, startRow + deltaRows));
        patch.row = newRow;
        patch.rowSpan = bottomEdge - newRow;
      }
      onResize(patch);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  const [hovered, setHovered] = useState(false);
  const showControls = hovered || isDragging || isBeingDragged;

  return (
    <div
      ref={cellRef}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => { if (!isDragging) setHovered(false); }}
      onMouseDown={(e) => {
        const tag = (e.target as HTMLElement).tagName;
        const isInteractive = tag === 'BUTTON' || tag === 'INPUT' || tag === 'SELECT' || tag === 'LABEL';
        const isResizeHandle = (e.target as HTMLElement).closest('[data-resize-handle]');
        if (!isInteractive && !isResizeHandle) {
          onMoveStart(e);
        }
      }}
      style={{
        gridColumn: `${isDragging ? localCol : item.col} / span ${isDragging ? localColSpan : item.colSpan}`,
        gridRow: `${isDragging ? localRow : item.row} / span ${isDragging ? localRowSpan : item.rowSpan}`,
        position: 'relative',
        cursor: isBeingDragged ? 'grabbing' : (hovered ? 'grab' : 'default'),
        opacity: isBeingDragged ? 0.5 : 1,
        transition: isBeingDragged ? 'none' : 'opacity 0.15s',
      }}
    >
      {/* Chart — fills entire cell */}
      <Chart
        data={{ rows: chart.data as Record<string, unknown>[], script: chart.script }}
        props={{ chart_type: chart.chart_type, title: chart.title, ...chart.chart_spec }}
        hideAddToReport
        onRemove={onDelete}
        fillContainer
        hideBorder={!showControls}
      />

      {/* Resize handles — only visible on hover */}
      {showControls && (<>
        <div data-resize-handle onMouseDown={handleWidthResize}
          style={{ position: 'absolute', top: 0, right: 0, width: 6, bottom: 0, cursor: 'col-resize', zIndex: 1 }}>
          <div style={{ position: 'absolute', top: '50%', right: 0, transform: 'translateY(-50%)', width: 3, height: 24, backgroundColor: '#ccc', borderRadius: 2 }} />
        </div>
        <div data-resize-handle onMouseDown={handleLeftResize}
          style={{ position: 'absolute', top: 0, left: 0, width: 6, bottom: 0, cursor: 'col-resize', zIndex: 1 }}>
          <div style={{ position: 'absolute', top: '50%', left: 0, transform: 'translateY(-50%)', width: 3, height: 24, backgroundColor: '#ccc', borderRadius: 2 }} />
        </div>
        <div data-resize-handle onMouseDown={handleHeightResize}
          style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 6, cursor: 'row-resize', zIndex: 1 }}>
          <div style={{ position: 'absolute', bottom: 0, left: '50%', transform: 'translateX(-50%)', width: 24, height: 3, backgroundColor: '#ccc', borderRadius: 2 }} />
        </div>
        <div data-resize-handle onMouseDown={handleTopResize}
          style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 6, cursor: 'row-resize', zIndex: 1 }}>
          <div style={{ position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)', width: 24, height: 3, backgroundColor: '#ccc', borderRadius: 2 }} />
        </div>
        <div data-resize-handle onMouseDown={handleCornerResize('right', 'bottom')}
          style={{ position: 'absolute', bottom: 0, right: 0, width: 10, height: 10, cursor: 'nwse-resize', zIndex: 2 }} />
        <div data-resize-handle onMouseDown={handleCornerResize('left', 'top')}
          style={{ position: 'absolute', top: 0, left: 0, width: 10, height: 10, cursor: 'nwse-resize', zIndex: 2 }} />
        <div data-resize-handle onMouseDown={handleCornerResize('right', 'top')}
          style={{ position: 'absolute', top: 0, right: 0, width: 10, height: 10, cursor: 'nesw-resize', zIndex: 2 }} />
        <div data-resize-handle onMouseDown={handleCornerResize('left', 'bottom')}
          style={{ position: 'absolute', bottom: 0, left: 0, width: 10, height: 10, cursor: 'nesw-resize', zIndex: 2 }} />
      </>)}
    </div>
  );
}
