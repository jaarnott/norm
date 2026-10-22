'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import type { SavedReport } from '../../types';
import { autoRefreshLabel, cacheIsFresh, tileInterval, tilesRefreshLabel } from '../../lib/dashboardRefresh';
import { apiFetch } from '../../lib/api';
import { onChange } from '../../lib/liveChanges';
import Chart from './Chart';
import { RefreshCw, Share2, Check, Settings, Maximize2, LayoutGrid, Copy, History } from 'lucide-react';
import ChartFullScreenModal from './dashboard/ChartFullScreenModal';
import ChartConfigPanel from './dashboard/ChartConfigPanel';
import DrillDownPanel from './dashboard/DrillDownPanel';
import DashboardPicker from './dashboard/DashboardPicker';
import DashboardHistory from './dashboard/DashboardHistory';
import DashboardPage from './dashboard/DashboardPage';
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
    // Norm's own apps as a tile (the HR board embeds Hiring), and the chef's
    // menu engineering grid.
    app_runner: () => import('../apps/AppPage'),
    menu_engineering: () => import('./MenuEngineering'),
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

/**
 * An AI agent's Dashboard page, or one tile dashboard shown in a conversation.
 *
 * Dashboards are becoming pages of code, like apps (docs/plans/
 * apps-and-dashboards-one-system.md): once an agent's page has any of those
 * (the person's own, one shared with them, or a template), it shows that —
 * DashboardPage. Until then it shows the tile dashboard as before.
 */
export default function DashboardView({ data, props }: DisplayBlockProps) {
  const agentSlug = (data?.agent_slug as string) || (props?.agent_slug as string) || '';
  const directReportId = (data?.report_id as string) || '';
  const pageMode = !!agentSlug && !directReportId;
  const [tiles, setTiles] = useState<boolean | null>(pageMode ? null : true);

  useEffect(() => {
    if (!pageMode) return;
    const check = () => apiFetch(`/api/dashboards/${agentSlug}`)
      .then((r) => (r.ok ? r.json() : { tiles: true }))
      .then((d) => setTiles(d.tiles !== false))
      .catch(() => setTiles(true));
    check();
    // Norm made or changed a dashboard: the page may have its first one now.
    return onChange('dashboard', ({ agent }) => { if (!agent || agent === agentSlug) check(); });
  }, [pageMode, agentSlug]);

  if (tiles === null) return <div className="n-page"><PageState kind="loading" title="Loading the dashboard…" /></div>;
  if (!tiles) return <DashboardPage agentSlug={agentSlug} />;
  return <TileDashboardView data={data} props={props} />;
}

function TileDashboardView({ data, props }: DisplayBlockProps) {
  const agentSlug = (data?.agent_slug as string) || (props?.agent_slug as string) || '';
  const directReportId = (data?.report_id as string) || '';
  const [dashboard, setDashboard] = useState<SavedReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshingCharts, setRefreshingCharts] = useState<Set<string>>(new Set());
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [debugInfo, setDebugInfo] = useState<Record<string, unknown>[] | null>(null);
  const [refreshErrors, setRefreshErrors] = useState<Record<string, unknown>[] | null>(null);
  // Why a tile has nothing to show, per chart, from the last refresh.
  const [chartErrors, setChartErrors] = useState<Record<string, string>>({});
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
  // The period control, for dashboards whose charts take a plain-English
  // `period` (the cost-of-goods template). '' = each chart's own default.
  // null until the viewer picks one; the dashboard's own default applies
  // meanwhile (a template names it: Operations opens on today).
  const [pickedPeriod, setPickedPeriod] = useState<string | null>(null);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  // When each tile's refresh last STARTED (ms). Stamped at the start so a
  // slow tile is never fired twice, and seeded from the cache's own time on
  // open so a fresh tile is next due when its interval elapses from then.
  const lastStartedRef = useRef<Record<string, number>>({});
  const [showPicker, setShowPicker] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [copying, setCopying] = useState(false);
  const { isMobile, isTablet } = useBreakpoint();

  const initialRefreshDone = useRef(false);
  const selectedPeriod = pickedPeriod
    ?? ((dashboard?.global_filters as { default_period?: string } | null)?.default_period || 'this_month');

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

  // Norm changed this dashboard, or made or switched the one this AI agent's
  // page shows: load it again, as on opening the page.
  const shownIdRef = useRef<string | null>(null);
  shownIdRef.current = dashboard?.id ?? null;
  useEffect(() => onChange('dashboard', ({ dashboardId, agent }) => {
    const mine = !!dashboardId && dashboardId === shownIdRef.current;
    const url = directReportId
      ? (dashboardId === directReportId ? `/api/reports/${directReportId}` : null)
      : (mine || (!!agent && agent === agentSlug)) && agentSlug ? `/api/reports/dashboards/${agentSlug}` : null;
    if (!url) return;
    apiFetch(url)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        const next = directReportId ? d : d?.dashboard;
        if (!next) return;
        initialRefreshDone.current = false;
        lastStartedRef.current = {};
        setDashboard(next);
      })
      .catch(() => {});
  }), [agentSlug, directReportId]);

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

  const handleRefresh = useCallback(async (filters?: { venue_id?: string; period?: string }, only?: string[]) => {
    if (!dashboard?.id || !dashboard.charts?.length) return;
    // No ids = every tile (the Refresh button, a venue or period change).
    const chartIds = only ? only.filter(id => dashboard.charts.some(c => c.id === id)) : dashboard.charts.map(c => c.id);
    if (chartIds.length === 0) return;
    const now = Date.now();
    for (const id of chartIds) lastStartedRef.current[id] = now;
    setRefreshing(true);

    const globalFilters: Record<string, string> = {};
    const venueId = filters?.venue_id !== undefined ? filters.venue_id : selectedVenue;
    if (venueId) {
      globalFilters.venue_id = venueId;
    } else {
      globalFilters.venue_id = '__all__';
    }
    // Harmless for a dashboard without period charts: the API applies it
    // only to a chart that takes a `period`.
    globalFilters.period = filters?.period !== undefined ? filters.period : selectedPeriod;
    const body = JSON.stringify({ global_filters: globalFilters });

    setRefreshingCharts(prev => new Set([...prev, ...chartIds]));
    const errors: Record<string, string> = {};

    // Fire per-chart refreshes in parallel
    const promises = chartIds.map(async (chartId) => {
      try {
        const res = await apiFetch(`/api/reports/${dashboard.id}/charts/${chartId}/refresh`, {
          method: 'POST', body,
        });
        if (res.ok) {
          const { chart: updatedChart, error } = await res.json();
          if (error) errors[chartId] = String(error);
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
    // Keep the other tiles' reasons; replace only the ones just refreshed.
    setChartErrors(prev => {
      const next = { ...prev };
      for (const id of chartIds) { if (errors[id]) next[id] = errors[id]; else delete next[id]; }
      return next;
    });
    setLastRefreshed(new Date());
    setRefreshing(false);
  }, [dashboard?.id, dashboard?.charts, selectedVenue, selectedPeriod]);

  // On open: show the cached rows at once and re-read only the tiles whose
  // cache is stale for this venue and period (or has nothing in it).
  useEffect(() => {
    if (dashboard && !loading && !initialRefreshDone.current) {
      initialRefreshDone.current = true;
      const stale: string[] = [];
      for (const c of dashboard.charts) {
        if (cacheIsFresh(c, dashboard.refresh_interval_seconds, selectedVenue, selectedPeriod)) {
          lastStartedRef.current[c.id] = Date.parse(c.data_meta!.refreshed_at);
        } else {
          stale.push(c.id);
        }
      }
      if (stale.length) handleRefresh(undefined, stale);
    }
  }, [dashboard, loading, handleRefresh, selectedVenue, selectedPeriod]);

  // Re-run when the chosen venue changes after the first load — covers both a
  // manual pick and the programmatic seed from the remembered venue once venues
  // finish loading. Skipped before the initial refresh so it never double-fires.
  useEffect(() => {
    if (initialRefreshDone.current) handleRefresh({ venue_id: selectedVenue });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVenue]);
  useEffect(() => {
    if (initialRefreshDone.current && pickedPeriod) handleRefresh({ period: pickedPeriod });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickedPeriod]);

  // Auto-refresh, per tile: a 30-second tick refreshes the tiles whose own
  // interval has elapsed since their last refresh started. One timer firing
  // every tile at once was the burst that timed the slow reads out.
  useEffect(() => {
    if (!dashboard?.id) return;
    const tick = () => {
      const now = Date.now();
      const due = dashboard.charts
        .filter(c => {
          const interval = tileInterval(c, dashboard.refresh_interval_seconds);
          return interval > 0 && now - (lastStartedRef.current[c.id] || 0) >= interval * 1000;
        })
        .map(c => c.id);
      if (due.length) handleRefresh(undefined, due);
    };
    intervalRef.current = setInterval(tick, 30_000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [dashboard?.id, dashboard?.charts, dashboard?.refresh_interval_seconds, handleRefresh]);

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
  // A dashboard that is one App shows the App whole, filling the page — a
  // person's choice; asked for a dashboard, Norm builds tiles.
  const soleChart = layout.length === 1 ? chartMap.get(layout[0].chart_id) : undefined;
  const soleAppProps = soleChart?.chart_type === 'component'
    && (soleChart.chart_spec as unknown as Record<string, unknown>)?.component_key === 'app_runner'
    ? ((soleChart.chart_spec as unknown as Record<string, unknown>).component_props as Record<string, unknown> | undefined) || {}
    : null;

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
    tilesRefreshLabel(dashboard.charts.map(c => tileInterval(c, dashboard.refresh_interval_seconds))),
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

  // Shown only when a chart takes a period — month and week views of the
  // same numbers, resolved on the venue's calendar by the API.
  const hasPeriod = dashboard.charts.some(c => !!c.script?.params?.period);
  const periodPicker = hasPeriod ? (
    <select
      className="n-select"
      aria-label="Period"
      value={selectedPeriod}
      onChange={e => setPickedPeriod(e.target.value)}
    >
      <optgroup label="Days">
        <option value="today">Today</option>
        <option value="yesterday">Yesterday</option>
        <option value="last_7_days">Last 7 days</option>
      </optgroup>
      <optgroup label="Months">
        <option value="this_month">This month</option>
        <option value="last_month">Last month</option>
        <option value="last_12_months">Last 12 months</option>
      </optgroup>
      <optgroup label="Weeks">
        <option value="this_week">This week</option>
        <option value="last_week">Last week</option>
        <option value="last_13_weeks">Last 13 weeks</option>
      </optgroup>
    </select>
  ) : null;

  // A dashboard shared with you is view-only: only its owner may change it.
  const canEdit = dashboard.is_own !== false;

  // Another dashboard in place of this one (a copy, a version brought back):
  // its tiles load at once, as on opening the page.
  const showDashboard = (next: SavedReport) => {
    initialRefreshDone.current = false;
    lastStartedRef.current = {};
    setDashboard(next);
  };

  // The copy can wait behind tiles still loading, so the button says so.
  const makeCopy = async () => {
    setCopying(true);
    try {
      const res = await apiFetch(`/api/reports/${dashboard.id}/copy`, {
        method: 'POST',
        body: JSON.stringify({ make_active: true }),
      });
      if (res.ok) showDashboard(await res.json());
    } finally {
      setCopying(false);
    }
  };

  const actions = (
    <>
      {periodPicker}
      {venuePicker}
      <Button
        onClick={() => handleRefresh()}
        disabled={refreshing}
        title={lastRefreshed ? `Last refreshed: ${lastRefreshed.toLocaleTimeString()}` : 'Refresh'}
      >
        <Icon icon={RefreshCw} size="inline" style={refreshing ? { animation: 'n-spin 1s linear infinite' } : undefined} />
        {refreshing ? 'Refreshing…' : 'Refresh'}
      </Button>
      {canEdit && <Button
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
      </Button>}
      {canEdit
        ? <Button variant="quiet" icon={History} onClick={() => setShowHistory(true)}>History</Button>
        : <Button icon={Copy} onClick={makeCopy} disabled={copying} title="Only the person who shared it can change it. Your copy is yours to change.">{copying ? 'Copying…' : 'Make a copy'}</Button>}
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

      {soleAppProps && (() => {
        const AppTile = getEmbeddableComponent('app_runner');
        return AppTile ? (
          <div style={{ height: 'calc(100vh - 180px)', minHeight: 480 }}>
            <AppTile data={{}} props={{ ...soleAppProps, fill: true }} />
          </div>
        ) : null;
      })()}

      {/* Grid */}
      {!soleAppProps && <div style={stacked ? {
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
              title={(() => {
                const interval = tileInterval(chart, dashboard.refresh_interval_seconds);
                const cadence = interval > 0 ? autoRefreshLabel(interval).replace('Refreshes', 'refreshes') : 'refreshes manually';
                const at = chart.data_meta?.refreshed_at ? new Date(chart.data_meta.refreshed_at) : null;
                return at ? `Refreshed ${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · ${cadence}` : cadence;
              })()}
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
                {canEdit && <IconButton icon={Settings} label="Chart settings" iconSize={16} onClick={() => setInspectedChartId(chart.id)} />}
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
                  emptyReason={chartErrors[chart.id]}
                  loading={refreshingCharts.has(chart.id)}
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
      </div>}

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

      {showHistory && (
        <DashboardHistory
          reportId={dashboard.id}
          onClose={() => setShowHistory(false)}
          onRestored={next => { setShowHistory(false); showDashboard(next); }}
        />
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
