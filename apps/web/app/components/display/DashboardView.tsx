'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import type { SavedReport } from '../../types';
import { apiFetch } from '../../lib/api';
import Chart from './Chart';
import { RefreshCw, Share2, Check, Settings, Maximize2, LayoutGrid } from 'lucide-react';
import ChartFullScreenModal from './dashboard/ChartFullScreenModal';
import ChartConfigPanel from './dashboard/ChartConfigPanel';
import DrillDownPanel from './dashboard/DrillDownPanel';
import DashboardPicker from './dashboard/DashboardPicker';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import Icon from '../ui/Icon';

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
// Space between tiles: white cards on the cream page need a visible gutter.
const TILE_GAP = 12;

/** "Refreshes every 5 min" — the auto-refresh interval, in words. */
function autoRefreshLabel(seconds: number): string {
  return seconds < 60 ? `Refreshes every ${seconds} sec` : `Refreshes every ${Math.round(seconds / 60)} min`;
}

export default function DashboardView({ data, props }: DisplayBlockProps) {
  const agentSlug = (data?.agent_slug as string) || (props?.agent_slug as string) || '';
  const directReportId = (data?.report_id as string) || '';
  const [dashboard, setDashboard] = useState<SavedReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshingCharts, setRefreshingCharts] = useState<Set<string>>(new Set());
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [debugInfo, setDebugInfo] = useState<Record<string, unknown>[] | null>(null);
  const [refreshErrors, setRefreshErrors] = useState<Record<string, unknown>[] | null>(null);
  const [expandedChartId, setExpandedChartId] = useState<string | null>(null);
  const [inspectedChartId, setInspectedChartId] = useState<string | null>(null);
  const [drillDown, setDrillDown] = useState<{ title: string; rows: Record<string, unknown>[] } | null>(null);
  const [venues, setVenues] = useState<{ id: string; name: string }[]>([]);
  // '' means "All Venues" here — a dashboard-only view we don't persist. The
  // shared page venue is only honoured when this is a PAGE instance.
  const persistVenue = !!props?.persistVenue;
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [selectedVenue, setSelectedVenue] = useState<string>('');
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const { isMobile, isTablet } = useBreakpoint();

  const initialRefreshDone = useRef(false);

  // Load dashboard — by direct report_id or by agent slug
  useEffect(() => {
    initialRefreshDone.current = false;
    if (directReportId) {
      apiFetch(`/api/reports/${directReportId}`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) { setDashboard(d); } })
        .catch(() => {})
        .finally(() => setLoading(false));
    } else if (agentSlug) {
      apiFetch(`/api/reports/dashboards/${agentSlug}`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.dashboard) { setDashboard(d.dashboard); } })
        .catch(() => {})
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, [agentSlug, directReportId]);

  // Load venues
  useEffect(() => {
    apiFetch('/api/venues')
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.venues?.length) {
          setVenues(d.venues);
          // Open on the venue the user last picked elsewhere, if they still have
          // access; otherwise leave it on "All Venues".
          if (rememberedVenue && d.venues.some((v: { id: string }) => v.id === rememberedVenue)) {
            setSelectedVenue(rememberedVenue);
          }
        }
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = useCallback(async (filters?: { venue_id?: string }) => {
    if (!dashboard?.id || !dashboard.charts?.length) return;
    setRefreshing(true);

    const globalFilters: Record<string, string> = {};
    const venueId = filters?.venue_id !== undefined ? filters.venue_id : selectedVenue;
    if (venueId) {
      globalFilters.venue_id = venueId;
    } else {
      globalFilters.venue_id = '__all__';
    }
    const body = JSON.stringify({ global_filters: globalFilters });

    // Mark all charts as refreshing
    const chartIds = dashboard.charts.map(c => c.id);
    setRefreshingCharts(new Set(chartIds));

    // Fire per-chart refreshes in parallel
    const promises = chartIds.map(async (chartId) => {
      try {
        const res = await apiFetch(`/api/reports/${dashboard.id}/charts/${chartId}/refresh`, {
          method: 'POST', body,
        });
        if (res.ok) {
          const { chart: updatedChart } = await res.json();
          if (updatedChart) {
            setDashboard(prev => {
              if (!prev) return prev;
              return {
                ...prev,
                charts: prev.charts.map(c => c.id === chartId ? updatedChart : c),
              };
            });
          }
        }
      } catch { /* ignore */ }
      setRefreshingCharts(prev => {
        const next = new Set(prev);
        next.delete(chartId);
        return next;
      });
    });

    await Promise.all(promises);
    setLastRefreshed(new Date());
    setRefreshing(false);
  }, [dashboard?.id, dashboard?.charts, selectedVenue]);

  // Auto-refresh on initial load — show cached data immediately, refresh in background
  useEffect(() => {
    if (dashboard && !loading && !initialRefreshDone.current) {
      initialRefreshDone.current = true;
      handleRefresh();
    }
  }, [dashboard, loading, handleRefresh]);

  // Re-run when the chosen venue changes after the first load — covers both a
  // manual pick and the programmatic seed from the remembered venue once venues
  // finish loading. Skipped before the initial refresh so it never double-fires.
  useEffect(() => {
    if (initialRefreshDone.current) handleRefresh({ venue_id: selectedVenue });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVenue]);

  // Auto-refresh — handleRefresh in deps so interval always uses the latest venue selection
  useEffect(() => {
    if (!dashboard?.refresh_interval_seconds || !dashboard.id) return;
    intervalRef.current = setInterval(() => {
      handleRefresh();
    }, dashboard.refresh_interval_seconds * 1000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [dashboard?.id, dashboard?.refresh_interval_seconds, handleRefresh]);

  if (loading) {
    return <PageState kind="loading" title="Loading dashboard…" />;
  }

  const reloadDashboard = () => {
    setLoading(true);
    setShowPicker(false);
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
  };

  if (!dashboard || showPicker) {
    return (
      <DashboardPicker
        agentSlug={agentSlug}
        onDashboardSelected={reloadDashboard}
        asPage={persistVenue}
      />
    );
  }

  const layout = dashboard.layout || [];
  const chartMap = new Map(dashboard.charts.map(c => [c.id, c]));

  // Calculate grid height
  const maxRow = layout.reduce((max, item) => Math.max(max, (item.row || 1) + (item.rowSpan || 8)), 1);

  // Phones and tablets stack the tiles full width, in the order the desktop
  // grid reads (top to bottom, left to right). Tablets used to keep each
  // tile's desktop row but pin it to column 1, so tiles sharing a row (the
  // KPI strip, side-by-side charts) were drawn on top of each other.
  const stacked = isMobile || isTablet;
  const tiles = stacked
    ? [...layout].sort((a, b) => ((a.row || 1) - (b.row || 1)) || ((a.col || 1) - (b.col || 1)))
    : layout;

  const meta = [
    dashboard.description,
    dashboard.refresh_interval_seconds ? autoRefreshLabel(dashboard.refresh_interval_seconds) : null,
  ].filter(Boolean).join(' · ') || undefined;

  // '' (all venues) is this view's own value; the picker spells it 'all'.
  const venuePicker = venues.length > 0 ? (
    <VenueSelect
      venues={venues}
      value={selectedVenue || 'all'}
      allowAll
      onChange={id => {
        const venueId = id === 'all' ? '' : id;
        setSelectedVenue(venueId);
        if (venueId && persistVenue) setActiveVenue(venueId);
      }}
    />
  ) : null;

  const actions = (
    <>
      {venuePicker}
      <Button
        onClick={() => handleRefresh()}
        disabled={refreshing}
        title={lastRefreshed ? `Last refreshed: ${lastRefreshed.toLocaleTimeString()}` : 'Refresh'}
      >
        <Icon icon={RefreshCw} size="inline" style={refreshing ? { animation: 'n-spin 1s linear infinite' } : undefined} />
        {refreshing ? 'Refreshing…' : 'Refresh'}
      </Button>
      <Button
        icon={dashboard.is_published ? Check : Share2}
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
        style={dashboard.is_published ? { color: 'var(--ok)' } : undefined}
      >
        {dashboard.is_published ? 'Published' : 'Share'}
      </Button>
      {agentSlug && (
        <Button variant="quiet" icon={LayoutGrid} onClick={() => setShowPicker(true)}>Change dashboard</Button>
      )}
    </>
  );

  return (
    // A page sits in FunctionalPage's frame; in a conversation (or the
    // template editor) the view keeps a little breathing room of its own.
    <div style={persistVenue ? undefined : { padding: 8 }}>
      {persistVenue ? (
        <PageHeader title={dashboard.title} meta={meta} actions={actions} titleOnPhone />
      ) : (
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{dashboard.title}</h2>
            {meta && <p style={{ margin: '2px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta}</p>}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>{actions}</div>
        </div>
      )}

      {layout.length === 0 && (
        <PageState kind="empty" title="This dashboard has no charts yet." detail="Ask Norm to add one, or change to another dashboard." />
      )}

      {/* Grid */}
      <div style={stacked ? {
        display: 'flex',
        flexDirection: 'column',
        gap: TILE_GAP,
      } : {
        display: 'grid',
        gridTemplateColumns: 'repeat(24, 1fr)',
        gridAutoRows: ROW_HEIGHT,
        gap: TILE_GAP,
        minHeight: maxRow * ROW_HEIGHT,
      }}>
        {tiles.map(item => {
          const chart = chartMap.get(item.chart_id);
          if (!chart) return null;

          // Embedded component type — render a domain component instead of a chart
          const isEmbedded = chart.chart_type === 'component';
          const componentKey = isEmbedded ? (chart.chart_spec as unknown as Record<string, unknown>)?.component_key as string : null;
          const EmbeddedComponent = componentKey ? getEmbeddableComponent(componentKey) : null;

          // Responsive grid placement
          const colSpan = item.colSpan || 24;
          const mobileHeight = (item.rowSpan || 8) * ROW_HEIGHT;
          const gridStyle: React.CSSProperties = stacked
            ? { height: mobileHeight, minHeight: mobileHeight, width: '100%' }
            : {
                gridColumn: `${item.col || 1} / span ${colSpan}`,
                gridRow: `${item.row || 1} / span ${item.rowSpan || 8}`,
              };

          return (
            <div
              key={item.chart_id}
              style={{
                ...gridStyle,
                overflow: 'hidden',
                position: 'relative',
              }}
              className="n-card dashboard-chart-tile"
            >
              {/* Per-chart loading indicator */}
              {refreshingCharts.has(chart.id) && (
                <div style={{
                  position: 'absolute', top: 0, left: 0, right: 0, height: 2, zIndex: 11,
                  background: 'linear-gradient(90deg, transparent, var(--brand-soft), transparent)',
                  animation: 'shimmer 1.5s infinite',
                }} />
              )}
              {/* Chart action buttons — on hover or keyboard focus */}
              <div
                className="chart-inspect-btn"
                style={{
                  position: 'absolute', top: 6, right: 6, zIndex: 10,
                  display: 'flex', gap: 2, opacity: 0, transition: 'opacity 0.15s',
                  backgroundColor: 'var(--bg)', borderRadius: 'var(--radius)',
                }}
              >
                <IconButton icon={Maximize2} label="Full screen" iconSize={16} onClick={() => setExpandedChartId(chart.id)} />
                <IconButton icon={Settings} label="Chart settings" iconSize={16} onClick={() => setInspectedChartId(chart.id)} />
              </div>
              {isEmbedded && EmbeddedComponent ? (
                // Inset like the card it is, so the component never runs into the tile's edge.
                <div style={{ height: '100%', overflow: 'auto', padding: '12px 14px 14px', boxSizing: 'border-box' }}>
                  {chart.chart_spec?.title && (
                    <div style={{ marginBottom: 8, fontSize: 'var(--fs-base)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>
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
                  // The tile is titled with the chart's own title (what the full-screen
                  // view and chart settings show); a spec without one read "Chart".
                  props={{ ...chart.chart_spec, ...(chart.title ? { title: chart.title } : {}), chart_type: chart.chart_type, fillContainer: true } as Record<string, unknown>}
                  hideAddToReport
                  fillContainer
                  // The tile is the card; the chart draws no second border inside it.
                  hideBorder
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
        <details style={{ marginTop: 16 }}>
          <summary style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--muted)', cursor: 'pointer' }}>
            Refresh details
            {refreshErrors && refreshErrors.length > 0 && (
              <span style={{ color: 'var(--error)', marginLeft: 6 }}>{refreshErrors.length} error{refreshErrors.length > 1 ? 's' : ''}</span>
            )}
          </summary>
          <div style={{ marginTop: 6 }}>
            {refreshErrors && refreshErrors.length > 0 && (
              <div style={{ marginBottom: 8 }}>
                {refreshErrors.map((err, i) => (
                  <div key={i} style={{ fontSize: 'var(--fs-xs)', color: 'var(--error)', padding: '3px 0' }}>
                    <strong>{String(err.title || err.chart_id)}</strong>: {String(err.error)}
                  </div>
                ))}
              </div>
            )}
            {debugInfo && (
              <pre style={{
                fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-mono)', color: 'var(--text-soft)', backgroundColor: 'var(--surface-alt)',
                padding: 8, borderRadius: 'var(--radius-sm)', overflow: 'auto', maxHeight: 300,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word', border: '1px solid var(--line)',
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
        @keyframes shimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
        .dashboard-chart-tile:hover .chart-inspect-btn,
        .dashboard-chart-tile:focus-within .chart-inspect-btn { opacity: 1 !important; }
      `}</style>
    </div>
  );
}
