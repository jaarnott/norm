'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import type { SavedReport } from '../../types';
import { apiFetch } from '../../lib/api';
import Chart from './Chart';
import { RefreshCw, Share2, Check, Settings2 } from 'lucide-react';
import ChartFullScreenModal from './dashboard/ChartFullScreenModal';
import ChartConfigPanel from './dashboard/ChartConfigPanel';
import DrillDownPanel from './dashboard/DrillDownPanel';
import TemplateGallery from './dashboard/TemplateGallery';
import { useBreakpoint } from '../../hooks/useBreakpoint';

// Lazy imports for embeddable components (avoids circular deps with DisplayBlockRenderer)
import dynamic from 'next/dynamic';
const EMBEDDABLE_COMPONENTS: Record<string, React.ComponentType<DisplayBlockProps>> = {};

// Register embeddable components lazily on first use
function getEmbeddableComponent(key: string): React.ComponentType<DisplayBlockProps> | null {
  if (EMBEDDABLE_COMPONENTS[key]) return EMBEDDABLE_COMPONENTS[key];
  // Dynamic imports for components that can be embedded in dashboards
  const imports: Record<string, () => Promise<{ default: React.ComponentType<DisplayBlockProps> }>> = {
    hiring_board: () => import('./HiringBoard'),
    orders_dashboard: () => import('./OrdersDashboard'),
    roster_table: () => import('./RosterTable'),
    automated_task_board: () => import('./AutomatedTaskBoard'),
    generic_table: () => import('./GenericTable'),
    saved_reports_board: () => import('./SavedReportsBoard'),
  };
  if (imports[key]) {
    EMBEDDABLE_COMPONENTS[key] = dynamic(imports[key], { ssr: false }) as unknown as React.ComponentType<DisplayBlockProps>;
    return EMBEDDABLE_COMPONENTS[key];
  }
  return null;
}

const ROW_HEIGHT = 40;

export default function DashboardView({ data, props }: DisplayBlockProps) {
  const agentSlug = (data?.agent_slug as string) || (props?.agent_slug as string) || '';
  const [dashboard, setDashboard] = useState<SavedReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [debugInfo, setDebugInfo] = useState<Record<string, unknown>[] | null>(null);
  const [refreshErrors, setRefreshErrors] = useState<Record<string, unknown>[] | null>(null);
  const [expandedChartId, setExpandedChartId] = useState<string | null>(null);
  const [inspectedChartId, setInspectedChartId] = useState<string | null>(null);
  const [drillDown, setDrillDown] = useState<{ title: string; rows: Record<string, unknown>[] } | null>(null);
  const [venues, setVenues] = useState<{ id: string; name: string }[]>([]);
  const [selectedVenue, setSelectedVenue] = useState<string>('');
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  const { isMobile, isTablet } = useBreakpoint();

  // Load dashboard for agent
  useEffect(() => {
    if (!agentSlug) { setLoading(false); return; }
    apiFetch(`/api/reports/dashboards/${agentSlug}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.dashboard) {
          setDashboard(d.dashboard);
          setLastRefreshed(new Date());
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [agentSlug]);

  // Load venues
  useEffect(() => {
    apiFetch('/api/venues')
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.venues?.length) {
          setVenues(d.venues);
        }
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = useCallback(async (filters?: { venue_id?: string }) => {
    if (!dashboard?.id) return;
    setRefreshing(true);
    try {
      const globalFilters: Record<string, string> = {};
      const venueId = filters?.venue_id !== undefined ? filters.venue_id : selectedVenue;
      if (venueId) {
        globalFilters.venue_id = venueId;
      } else {
        // Explicitly signal "All Venues" so backend doesn't fall through to script/report venue
        globalFilters.venue_id = '__all__';
      }

      const res = await apiFetch(`/api/reports/${dashboard.id}/refresh`, {
        method: 'POST',
        body: JSON.stringify({ global_filters: globalFilters }),
      });
      if (res.ok) {
        const updated = await res.json();
        setDashboard(updated);
        setLastRefreshed(new Date());
        setDebugInfo(updated.refresh_debug || null);
        setRefreshErrors(updated.refresh_errors || null);
      }
    } catch { /* ignore */ }
    setRefreshing(false);
  }, [dashboard?.id, selectedVenue]);

  // Auto-refresh — handleRefresh in deps so interval always uses the latest venue selection
  useEffect(() => {
    if (!dashboard?.refresh_interval_seconds || !dashboard.id) return;
    intervalRef.current = setInterval(() => {
      handleRefresh();
    }, dashboard.refresh_interval_seconds * 1000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [dashboard?.id, dashboard?.refresh_interval_seconds, handleRefresh]);

  if (loading) {
    return <div style={{ padding: '2rem', textAlign: 'center', color: '#999' }}>Loading dashboard...</div>;
  }

  if (!dashboard) {
    return (
      <TemplateGallery
        agentSlug={agentSlug}
        onInstantiated={() => {
          setLoading(true);
          apiFetch(`/api/reports/dashboards/${agentSlug}`)
            .then(r => r.ok ? r.json() : null)
            .then(d => {
              if (d?.dashboard) {
                setDashboard(d.dashboard);
                setLastRefreshed(new Date());
              }
            })
            .catch(() => {})
            .finally(() => setLoading(false));
        }}
      />
    );
  }

  const layout = dashboard.layout || [];
  const chartMap = new Map(dashboard.charts.map(c => [c.id, c]));

  // Calculate grid height
  const maxRow = layout.reduce((max, item) => Math.max(max, (item.row || 1) + (item.rowSpan || 8)), 1);

  return (
    <div style={{ padding: '0.5rem' }}>
      {/* Toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem', flexWrap: 'wrap', gap: '0.5rem' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: '#1a1a1a' }}>{dashboard.title}</h2>
          {dashboard.description && <p style={{ margin: '0.15rem 0 0', fontSize: '0.75rem', color: '#999' }}>{dashboard.description}</p>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {venues.length > 0 && (
            <select
              value={selectedVenue}
              onChange={e => { setSelectedVenue(e.target.value); handleRefresh({ venue_id: e.target.value }); }}
              style={{ padding: '4px 8px', fontSize: '0.75rem', border: '1px solid #e2ddd7', borderRadius: 6, fontFamily: 'inherit' }}
            >
              <option value="">All Venues</option>
              {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          )}
          <button
            onClick={() => handleRefresh()}
            disabled={refreshing}
            title={lastRefreshed ? `Last refreshed: ${lastRefreshed.toLocaleTimeString()}` : 'Refresh'}
            style={{
              display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px',
              fontSize: '0.72rem', fontWeight: 500, border: '1px solid #e2ddd7', borderRadius: 6,
              backgroundColor: '#fff', cursor: refreshing ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
              color: '#888',
            }}
          >
            <RefreshCw size={14} strokeWidth={1.75} style={{ animation: refreshing ? 'spin 1s linear infinite' : 'none' }} />
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
          <button
            onClick={async () => {
              const next = !dashboard.is_published;
              const res = await apiFetch(`/api/reports/${dashboard.id}`, {
                method: 'PATCH',
                body: JSON.stringify({ is_published: next }),
              });
              if (res.ok) {
                const updated = await res.json();
                setDashboard(updated);
              }
            }}
            title={dashboard.is_published ? 'Published to organisation — click to unpublish' : 'Publish to organisation'}
            style={{
              display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px',
              fontSize: '0.72rem', fontWeight: 500, border: '1px solid #e2ddd7', borderRadius: 6,
              backgroundColor: dashboard.is_published ? '#f0faf2' : '#fff',
              cursor: 'pointer', fontFamily: 'inherit',
              color: dashboard.is_published ? '#4f8a5e' : '#888',
            }}
          >
            {dashboard.is_published ? <Check size={14} strokeWidth={2} /> : <Share2 size={14} strokeWidth={1.75} />}
            {dashboard.is_published ? 'Published' : 'Share'}
          </button>
          {dashboard.refresh_interval_seconds && (
            <span style={{ fontSize: '0.62rem', color: '#bbb' }}>
              Auto: {dashboard.refresh_interval_seconds < 60 ? `${dashboard.refresh_interval_seconds}s` : `${Math.round(dashboard.refresh_interval_seconds / 60)}m`}
            </span>
          )}
        </div>
      </div>

      {/* Grid */}
      <div style={isMobile ? {
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      } : {
        display: 'grid',
        gridTemplateColumns: isTablet ? 'repeat(12, 1fr)' : 'repeat(24, 1fr)',
        gridAutoRows: ROW_HEIGHT,
        gap: 4,
        minHeight: isMobile ? undefined : maxRow * ROW_HEIGHT,
      }}>
        {layout.map(item => {
          const chart = chartMap.get(item.chart_id);
          if (!chart) return null;

          // Embedded component type — render a domain component instead of a chart
          const isEmbedded = chart.chart_type === 'component';
          const componentKey = isEmbedded ? (chart.chart_spec as unknown as Record<string, unknown>)?.component_key as string : null;
          const EmbeddedComponent = componentKey ? getEmbeddableComponent(componentKey) : null;

          // Responsive grid placement
          const colSpan = item.colSpan || 24;
          const gridStyle: React.CSSProperties = isMobile
            ? { minHeight: (item.rowSpan || 8) * ROW_HEIGHT }
            : isTablet
              ? {
                  gridColumn: `1 / span ${Math.min(colSpan <= 12 ? colSpan : 12, 12)}`,
                  gridRow: `${item.row || 1} / span ${item.rowSpan || 8}`,
                }
              : {
                  gridColumn: `${item.col || 1} / span ${colSpan}`,
                  gridRow: `${item.row || 1} / span ${item.rowSpan || 8}`,
                };

          return (
            <div
              key={item.chart_id}
              style={{
                ...gridStyle,
                border: '1px solid #f0ebe5',
                borderRadius: 10,
                backgroundColor: '#fff',
                overflow: 'hidden',
                position: 'relative',
              }}
              className="dashboard-chart-tile"
            >
              {/* Inspect button — visible on hover */}
              <button
                onClick={() => setInspectedChartId(chart.id)}
                className="chart-inspect-btn"
                title="Inspect chart config"
                style={{
                  position: 'absolute', top: 4, right: 4, zIndex: 10,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  width: 24, height: 24, border: 'none', borderRadius: 4,
                  backgroundColor: 'rgba(255,255,255,0.9)', cursor: 'pointer',
                  opacity: 0, transition: 'opacity 0.15s',
                  color: '#999',
                }}
              >
                <Settings2 size={13} strokeWidth={1.75} />
              </button>
              {isEmbedded && EmbeddedComponent ? (
                <div style={{ height: '100%', overflow: 'auto' }}>
                  {chart.chart_spec?.title && (
                    <div style={{ padding: '0.5rem 0.75rem 0', fontSize: '0.7rem', fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                      {(chart.chart_spec as unknown as Record<string, unknown>).title as string}
                    </div>
                  )}
                  <EmbeddedComponent
                    data={chart.data as unknown as Record<string, unknown>}
                    props={(chart.chart_spec as unknown as Record<string, unknown>)?.component_props as Record<string, unknown> || {}}
                  />
                </div>
              ) : (
                <Chart
                  data={{ rows: chart.data, ...chart.chart_spec }}
                  props={{ ...chart.chart_spec, chart_type: chart.chart_type, fillContainer: true } as Record<string, unknown>}
                  hideAddToReport
                  fillContainer
                  onExpand={() => setExpandedChartId(chart.id)}
                  onDrillDown={(payload) => {
                    const xAxisKey = ((chart.chart_spec as unknown as Record<string, unknown>)?.x_axis as Record<string, unknown> | undefined)?.key as string || '';
                    const matchingRows = (chart.data || []).filter(r => String(r[xAxisKey]) === payload.label);
                    setDrillDown({ title: `${chart.title} — ${payload.label}`, rows: matchingRows.length > 0 ? matchingRows : [payload.row] });
                  }}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Debug / errors panel */}
      {(refreshErrors || debugInfo) && (
        <details style={{ marginTop: '1rem' }}>
          <summary style={{ fontSize: '0.68rem', fontWeight: 600, color: '#999', cursor: 'pointer' }}>
            Refresh Details
            {refreshErrors && refreshErrors.length > 0 && (
              <span style={{ color: '#dc3545', marginLeft: 6 }}>{refreshErrors.length} error{refreshErrors.length > 1 ? 's' : ''}</span>
            )}
          </summary>
          <div style={{ marginTop: '0.4rem' }}>
            {refreshErrors && refreshErrors.length > 0 && (
              <div style={{ marginBottom: '0.5rem' }}>
                {refreshErrors.map((err, i) => (
                  <div key={i} style={{ fontSize: '0.7rem', color: '#dc3545', padding: '0.2rem 0' }}>
                    <strong>{String(err.title || err.chart_id)}</strong>: {String(err.error)}
                  </div>
                ))}
              </div>
            )}
            {debugInfo && (
              <pre style={{
                fontSize: '0.65rem', color: '#666', backgroundColor: '#f8f8f8',
                padding: '0.5rem', borderRadius: 6, overflow: 'auto', maxHeight: 300,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word', border: '1px solid #eee',
              }}>
                {JSON.stringify(debugInfo, null, 2)}
              </pre>
            )}
          </div>
        </details>
      )}

      {/* Drill-down panel */}
      {drillDown && <DrillDownPanel title={drillDown.title} rows={drillDown.rows} onClose={() => setDrillDown(null)} />}

      {/* Full-screen chart modal */}
      {expandedChartId && (() => {
        const chart = dashboard.charts.find(c => c.id === expandedChartId);
        return chart ? <ChartFullScreenModal chart={chart} onClose={() => setExpandedChartId(null)} /> : null;
      })()}

      {/* Chart config panel */}
      {inspectedChartId && (() => {
        const chart = dashboard.charts.find(c => c.id === inspectedChartId);
        return chart ? (
          <ChartConfigPanel
            reportId={dashboard.id}
            chart={chart}
            venues={venues}
            onClose={() => setInspectedChartId(null)}
            onUpdated={() => {
              apiFetch(`/api/reports/dashboards/${agentSlug}`)
                .then(r => r.ok ? r.json() : null)
                .then(d => { if (d?.dashboard) setDashboard(d.dashboard); })
                .catch(() => {});
            }}
          />
        ) : null;
      })()}

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        .dashboard-chart-tile:hover .chart-inspect-btn { opacity: 1 !important; }
      `}</style>
    </div>
  );
}
