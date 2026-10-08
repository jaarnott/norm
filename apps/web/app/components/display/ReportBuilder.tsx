'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '../../lib/api';
import type { SavedReport, SavedReportChart, ReportGridItem } from '../../types';
import Chart from './Chart';
import DateRangePicker from './DateRangePicker';
import ChartConfigPanel from './dashboard/ChartConfigPanel';
import { RefreshCw, Settings } from 'lucide-react';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

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
  const [dateRange, setDateRange] = useState<{ start: string; end: string } | undefined>();
  const [inspectedChartId, setInspectedChartId] = useState<string | null>(null);
  const { isMobile, isTablet } = useBreakpoint();

  const reportId = data?.report_id;

  const fetchReport = useCallback(async () => {
    if (!reportId) return;
    try {
      const res = await apiFetch(`/api/reports/${reportId}`);
      if (res.ok) {
        const r = await res.json();
        setReport(r);
        setTitleDraft(r.title);
        if (r.global_filters?.start && r.global_filters?.end) {
          setDateRange({ start: r.global_filters.start, end: r.global_filters.end });
        }
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
      const r = item as unknown as Record<string, unknown>;
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

  // Local-only layout updates — no API call until user hits Save
  const updateLayout = (newLayout: ReportGridItem[]) => {
    if (!report) return;
    setReport({ ...report, layout: newLayout });
  };

  // Persist everything to the API (called on Save)
  const saveReport = async () => {
    if (!reportId || !report) return;
    const res = await apiFetch(`/api/reports/${reportId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: titleDraft || report.title, layout: report.layout, status: 'saved' }),
    });
    if (res.ok) setReport(await res.json());
  };

  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const ghostRef = useRef<HTMLDivElement>(null);

  const deleteChart = async (chartId: string) => {
    if (!reportId) return;
    await apiFetch(`/api/reports/${reportId}/charts/${chartId}`, { method: 'DELETE' });
    setConfirmDelete(null);
    fetchReport();
  };

  const refreshAll = async (filters?: { start?: string; end?: string }) => {
    if (!reportId) return;
    setRefreshing(true);
    try {
      const gf: Record<string, string> = {};
      const range = filters || dateRange;
      if (range?.start) gf.start = range.start;
      if (range?.end) gf.end = range.end;
      const res = await apiFetch(`/api/reports/${reportId}/refresh`, {
        method: 'POST',
        body: JSON.stringify({ global_filters: Object.keys(gf).length > 0 ? gf : null }),
      });
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
    const colWidth = gridRef.current.getBoundingClientRect().width / GRID_COLS;
    const startX = e.clientX;
    const startY = e.clientY;
    const startCol = item.col;
    const startRow = item.row;
    const pos = { col: startCol, row: startRow };

    // Show ghost via DOM
    const ghost = ghostRef.current;
    if (ghost) {
      ghost.style.display = 'block';
      ghost.style.gridColumn = `${startCol} / span ${item.colSpan}`;
      ghost.style.gridRow = `${startRow} / span ${item.rowSpan}`;
    }
    document.body.style.cursor = 'grabbing';

    const onMove = (ev: MouseEvent) => {
      const dx = Math.round((ev.clientX - startX) / colWidth);
      const dy = Math.round((ev.clientY - startY) / ROW_HEIGHT);
      pos.col = Math.max(1, Math.min(GRID_COLS + 1 - item.colSpan, startCol + dx));
      pos.row = Math.max(1, startRow + dy);
      if (ghost) {
        ghost.style.gridColumn = `${pos.col} / span ${item.colSpan}`;
        ghost.style.gridRow = `${pos.row} / span ${item.rowSpan}`;
      }
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      if (ghost) ghost.style.display = 'none';
      if (pos.col !== startCol || pos.row !== startRow) {
        updateGridItem(chartId, { col: pos.col, row: pos.row });
      }
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  if (loading) return <PageState kind="loading" title="Loading report…" />;
  if (!report) return <div style={{ padding: '0 24px' }}><PageState kind="error" title="Report not found" detail="It may have been deleted." /></div>;

  return (
    <div data-testid="report-builder" style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Header — the report's title and meta on the left, the toolbar on the
          right (it wraps under the title on narrow screens). It sits on the
          page's own background: no band, no rule. */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12,
        padding: '4px 24px 16px', flexShrink: 0,
      }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {editingTitle ? (
              <input
                className="n-input"
                aria-label="Report title"
                value={titleDraft}
                onChange={e => setTitleDraft(e.target.value)}
                onBlur={() => setEditingTitle(false)}
                onKeyDown={e => { if (e.key === 'Enter') setEditingTitle(false); }}
                autoFocus
                style={{ height: 'auto', maxWidth: '100%', padding: '2px 8px', fontSize: 'var(--fs-xl)', fontWeight: 700, letterSpacing: '-0.01em', lineHeight: 1.25 }}
              />
            ) : (
              <h2
                onClick={() => setEditingTitle(true)}
                style={{ margin: 0, fontSize: 'var(--fs-xl)', fontWeight: 700, letterSpacing: '-0.01em', lineHeight: 1.25, color: 'var(--text)', cursor: 'pointer', overflowWrap: 'anywhere' }}
                title="Click to rename"
              >{report.title}</h2>
            )}
            {report.is_dashboard && <Badge>Dashboard</Badge>}
          </div>
          <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            {report.charts.length} chart{report.charts.length !== 1 ? 's' : ''}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <DateRangePicker
            value={dateRange}
            onChange={(range) => {
              setDateRange(range);
              refreshAll(range);
              // Persist to report's global_filters
              if (reportId) {
                apiFetch(`/api/reports/${reportId}`, {
                  method: 'PATCH',
                  body: JSON.stringify({ global_filters: { start: range.start, end: range.end } }),
                }).catch(() => {});
              }
            }}
          />
          <Button
            icon={RefreshCw}
            onClick={() => refreshAll()}
            disabled={refreshing}
          >{refreshing ? 'Refreshing…' : 'Refresh all'}</Button>
          {!report.is_dashboard && (
            // One control: pick who the dashboard is for, then set it.
            <div style={{ display: 'flex' }}>
              <select
                id="promote-agent-select"
                className="n-select"
                aria-label="Dashboard for"
                defaultValue=""
                style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
              >
                <option value="" disabled>Agent…</option>
                <option value="reports">Reports</option>
                <option value="hr">HR</option>
                <option value="procurement">Procurement</option>
              </select>
              <Button
                style={{ marginLeft: -1, borderTopLeftRadius: 0, borderBottomLeftRadius: 0 }}
                onClick={async () => {
                  const select = document.getElementById('promote-agent-select') as HTMLSelectElement;
                  const slug = select?.value;
                  if (!slug) { select?.focus(); return; }
                  const res = await apiFetch(`/api/reports/${report.id}/promote-to-dashboard`, {
                    method: 'POST',
                    body: JSON.stringify({ agent_slug: slug }),
                  });
                  if (res.ok) {
                    const updated = await res.json();
                    setReport(updated);
                  }
                }}
              >Set as dashboard</Button>
            </div>
          )}
          <Button variant="primary" onClick={saveReport}>
            {report.status === 'saved' ? 'Saved' : 'Save report'}
          </Button>
        </div>
      </div>

      {/* Grid — each chart is a white card on the page's background */}
      <div style={{ flex: 1, overflow: 'auto', padding: '0 24px 24px' }}>
        {layout.length === 0 ? (
          <PageState
            kind="empty"
            title="No charts yet"
            detail={<>Ask Norm for data in the conversation below, then click &ldquo;Report&rdquo; on a chart to add it here.</>}
          />
        ) : (
          <div style={isMobile ? {} : { overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          <div
            ref={gridRef}
            style={isMobile ? {
              display: 'flex',
              flexDirection: 'column' as const,
              gap: 8,
              position: 'relative',
            } : {
              display: 'grid',
              gridTemplateColumns: isTablet ? 'repeat(12, 1fr)' : `repeat(${GRID_COLS}, 1fr)`,
              gridAutoRows: ROW_HEIGHT,
              gap: 4,
              position: 'relative',
              ...(!isTablet ? { minWidth: 600 } : {}),
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
                  isMobile={isMobile}
                  isTablet={isTablet}
                  onResize={(patch) => updateGridItem(item.chart_id, patch)}
                  onDelete={() => setConfirmDelete(item.chart_id)}
                  onMoveStart={(e) => handleMoveStart(item.chart_id, e)}
                  onInspect={() => setInspectedChartId(item.chart_id)}
                />
              );
            })}
            {/* Drop preview ghost — hidden by default, shown via DOM during drag */}
            <div
              ref={ghostRef}
              style={{
                display: 'none',
                backgroundColor: 'var(--accent-soft)',
                border: '2px dashed var(--brand-soft)',
                borderRadius: 'var(--radius-lg)',
                pointerEvents: 'none',
              }}
            />
          </div>
          </div>
        )}

        {/* Delete confirmation modal */}
        {confirmDelete && (
          <div style={{
            position: 'fixed', inset: 0, zIndex: 9999,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            backgroundColor: 'rgba(26, 26, 26, 0.35)', padding: 16,
          }}>
            <div role="alertdialog" aria-modal="true" aria-labelledby="remove-chart-title" style={{
              backgroundColor: 'var(--bg)', borderRadius: 'var(--radius-lg)', padding: 24, maxWidth: 400, width: '100%',
              boxShadow: '0 12px 40px rgba(26, 26, 26, 0.18)',
            }}>
              <h3 id="remove-chart-title" style={{ margin: '0 0 8px', fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>Remove chart?</h3>
              <p style={{ margin: '0 0 20px', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
                This will remove the chart from your report. The underlying data is not affected.
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-end' }}>
                <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
                <Button variant="danger" onClick={() => deleteChart(confirmDelete)}>Remove</Button>
              </div>
            </div>
          </div>
        )}

        {/* Chart config panel */}
        {inspectedChartId && reportId && (() => {
          const c = report.charts.find(ch => ch.id === inspectedChartId);
          return c ? (
            <ChartConfigPanel
              reportId={reportId}
              chart={c}
              venues={[]}
              onClose={() => setInspectedChartId(null)}
              onUpdated={() => fetchReport()}
            />
          ) : null;
        })()}

        {/* Cell chrome (globals.css has the handles): the card edge firms up
            on hover, and the settings button shows with it. Touch screens
            have no hover, so there the settings and remove buttons always show. */}
        <style>{`
          .grid-chart-cell.n-card:hover { border-color: var(--line-strong); }
          .grid-chart-cell:hover .chart-inspect-btn, .grid-chart-cell .chart-inspect-btn:focus-visible { opacity: 1 !important; }
          @media (pointer: coarse) {
            .grid-chart-cell .chart-inspect-btn, .grid-chart-cell .cell-chart-actions { opacity: 1 !important; }
          }
        `}</style>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Grid chart cell with resize handles
// ---------------------------------------------------------------------------

const MemoChart = React.memo(Chart);

function GridChartCell({
  item, chart, isMobile, isTablet, onResize, onDelete, onMoveStart, onInspect,
}: {
  item: ReportGridItem;
  chart: SavedReportChart;
  isMobile: boolean;
  isTablet: boolean;
  onResize: (patch: Partial<ReportGridItem>) => void;
  onDelete: () => void;
  onMoveStart: (e: React.MouseEvent) => void;
  onInspect: () => void;
}) {
  const cellRef = useRef<HTMLDivElement>(null);

  // Unified resize handler — pure DOM, zero React renders
  const handleResize = (edges: { left?: boolean; right?: boolean; top?: boolean; bottom?: boolean }) => (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const cell = cellRef.current;
    const grid = cell?.parentElement;
    if (!cell || !grid) return;
    const startX = e.clientX;
    const startY = e.clientY;
    const start = { col: item.col, row: item.row, colSpan: item.colSpan, rowSpan: item.rowSpan };
    const rightEdge = start.col + start.colSpan;
    const bottomEdge = start.row + start.rowSpan;
    const colWidth = grid.getBoundingClientRect().width / GRID_COLS;
    const d = { ...start };

    const cursor = (edges.left || edges.right) && (edges.top || edges.bottom)
      ? ((edges.left && edges.top) || (edges.right && edges.bottom) ? 'nwse-resize' : 'nesw-resize')
      : (edges.left || edges.right) ? 'col-resize' : 'row-resize';
    document.body.style.cursor = cursor;
    cell.style.userSelect = 'none';

    const onMove = (ev: MouseEvent) => {
      const dx = Math.round((ev.clientX - startX) / colWidth);
      const dy = Math.round((ev.clientY - startY) / ROW_HEIGHT);
      if (edges.right) d.colSpan = Math.max(1, Math.min(GRID_COLS + 1 - start.col, start.colSpan + dx));
      if (edges.left) { d.col = Math.max(1, Math.min(rightEdge - 1, start.col + dx)); d.colSpan = rightEdge - d.col; }
      if (edges.bottom) d.rowSpan = Math.max(2, start.rowSpan + dy);
      if (edges.top) { d.row = Math.max(1, Math.min(bottomEdge - 2, start.row + dy)); d.rowSpan = bottomEdge - d.row; }
      cell.style.gridColumn = `${d.col} / span ${d.colSpan}`;
      cell.style.gridRow = `${d.row} / span ${d.rowSpan}`;
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      cell.style.userSelect = '';
      onResize(d);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  return (
    <div
      ref={cellRef}
      className="grid-chart-cell n-card"
      onMouseDown={isMobile ? undefined : (e) => {
        const tag = (e.target as HTMLElement).tagName;
        const isInteractive = tag === 'BUTTON' || tag === 'INPUT' || tag === 'SELECT' || tag === 'LABEL';
        const isResizeHandle = (e.target as HTMLElement).closest('[data-resize-handle]');
        if (!isInteractive && !isResizeHandle) onMoveStart(e);
      }}
      style={isMobile ? {
        height: (item.rowSpan || 8) * ROW_HEIGHT,
        width: '100%',
        position: 'relative',
      } : isTablet ? {
        gridColumn: `1 / span ${Math.min(item.colSpan || 24, 12)}`,
        gridRow: `${item.row} / span ${item.rowSpan}`,
        position: 'relative',
      } : {
        gridColumn: `${item.col} / span ${item.colSpan}`,
        gridRow: `${item.row} / span ${item.rowSpan}`,
        position: 'relative',
      }}
    >
      {/* Settings — shows on hover. A chart's own header carries its actions
          (full screen, remove) at the right and centres its title, so settings
          takes the free left corner; a KPI has no header, so it takes the right. */}
      <IconButton
        icon={Settings}
        label="Chart settings"
        iconSize={16}
        onClick={onInspect}
        className="chart-inspect-btn"
        style={{
          position: 'absolute', top: 4, zIndex: 10,
          ...(chart.chart_type === 'kpi' ? { right: 4 } : { left: 4 }),
          opacity: 0, transition: 'opacity 0.15s',
        }}
      />
      {/* The cell is the card; the chart draws no edge of its own. */}
      <MemoChart
        data={{ rows: chart.data as Record<string, unknown>[], script: chart.script as unknown as Record<string, unknown> }}
        props={{ ...chart.chart_spec, chart_type: chart.chart_type, title: chart.title }}
        hideAddToReport
        onRemove={onDelete}
        fillContainer
        hideBorder
      />

      {/* Resize handles — hidden on mobile */}
      {!isMobile && <div className="cell-resize-handles">
        <div data-resize-handle onMouseDown={handleResize({ right: true })}
          style={{ position: 'absolute', top: 0, right: 0, width: 6, bottom: 0, cursor: 'col-resize', zIndex: 1 }}>
          <div style={{ position: 'absolute', top: '50%', right: 0, transform: 'translateY(-50%)', width: 3, height: 24, backgroundColor: 'var(--muted-soft)', borderRadius: 2 }} />
        </div>
        <div data-resize-handle onMouseDown={handleResize({ left: true })}
          style={{ position: 'absolute', top: 0, left: 0, width: 6, bottom: 0, cursor: 'col-resize', zIndex: 1 }}>
          <div style={{ position: 'absolute', top: '50%', left: 0, transform: 'translateY(-50%)', width: 3, height: 24, backgroundColor: 'var(--muted-soft)', borderRadius: 2 }} />
        </div>
        <div data-resize-handle onMouseDown={handleResize({ bottom: true })}
          style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 6, cursor: 'row-resize', zIndex: 1 }}>
          <div style={{ position: 'absolute', bottom: 0, left: '50%', transform: 'translateX(-50%)', width: 24, height: 3, backgroundColor: 'var(--muted-soft)', borderRadius: 2 }} />
        </div>
        <div data-resize-handle onMouseDown={handleResize({ top: true })}
          style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 6, cursor: 'row-resize', zIndex: 1 }}>
          <div style={{ position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)', width: 24, height: 3, backgroundColor: 'var(--muted-soft)', borderRadius: 2 }} />
        </div>
        <div data-resize-handle onMouseDown={handleResize({ right: true, bottom: true })}
          style={{ position: 'absolute', bottom: 0, right: 0, width: 10, height: 10, cursor: 'nwse-resize', zIndex: 2 }} />
        <div data-resize-handle onMouseDown={handleResize({ left: true, top: true })}
          style={{ position: 'absolute', top: 0, left: 0, width: 10, height: 10, cursor: 'nwse-resize', zIndex: 2 }} />
        <div data-resize-handle onMouseDown={handleResize({ right: true, top: true })}
          style={{ position: 'absolute', top: 0, right: 0, width: 10, height: 10, cursor: 'nesw-resize', zIndex: 2 }} />
        <div data-resize-handle onMouseDown={handleResize({ left: true, bottom: true })}
          style={{ position: 'absolute', bottom: 0, left: 0, width: 10, height: 10, cursor: 'nesw-resize', zIndex: 2 }} />
      </div>}
    </div>
  );
}
