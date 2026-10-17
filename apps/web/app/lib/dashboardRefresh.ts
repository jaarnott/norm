/**
 * Per-tile refresh for dashboards (Oct 2026).
 *
 * One dashboard-level timer fired every tile at once; the burst timed out the
 * slow twelve-month reads. Each tile now has its own cadence
 * (`chart_spec.refresh_interval_seconds`) and remembers what its cached rows
 * are for (`data_meta`), so a page open re-reads only what is stale.
 */
import type { SavedReportChart } from '../types';

/** "Refreshes every 5 min" — one interval, in words. */
export function autoRefreshLabel(seconds: number): string {
  return seconds < 60 ? `Refreshes every ${seconds} sec` : `Refreshes every ${Math.round(seconds / 60)} min`;
}

/** How often the whole set refreshes, in words — one cadence or a range. */
export function tilesRefreshLabel(intervals: number[]): string | null {
  const live = intervals.filter(s => s > 0);
  if (live.length === 0) return null;
  const lo = Math.min(...live), hi = Math.max(...live);
  if (lo === hi) return autoRefreshLabel(lo);
  const mins = (s: number) => (s < 60 ? `${s} sec` : `${Math.round(s / 60)}`);
  return `Tiles refresh every ${mins(lo)}–${mins(hi)} min`;
}

/** A tile's own auto-refresh in seconds: its spec's, else the dashboard's; 0 = manual. */
export function tileInterval(chart: SavedReportChart, dashboardInterval: number | null | undefined): number {
  const own = chart.chart_spec?.refresh_interval_seconds;
  if (typeof own === 'number' && Number.isFinite(own)) return Math.max(0, own);
  return dashboardInterval || 0;
}

/**
 * Whether a tile's cached rows still answer the page's question: fetched for
 * this venue and period, and younger than the tile's own interval (a manual
 * tile's rows never go stale on their own). Decided on open, so a tile whose
 * data is five seconds old is not re-read because someone switched tabs.
 */
export function cacheIsFresh(chart: SavedReportChart, dashboardInterval: number | null | undefined, venueId: string, period: string): boolean {
  const meta = chart.data_meta;
  if (!meta || !meta.refreshed_at || !(chart.data?.length)) return false;
  if (meta.venue_id !== (venueId || '__all__')) return false;
  if ((meta.period || '') !== (period || '')) return false;
  const interval = tileInterval(chart, dashboardInterval);
  if (interval === 0) return true;
  const ageMs = Date.now() - Date.parse(meta.refreshed_at);
  return Number.isFinite(ageMs) && ageMs >= 0 && ageMs < interval * 1000;
}

