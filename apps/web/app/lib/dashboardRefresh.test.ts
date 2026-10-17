import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cacheIsFresh, tileInterval, tilesRefreshLabel } from './dashboardRefresh';
import type { SavedReportChart } from '../types';

function chart(overrides: Partial<SavedReportChart> = {}): SavedReportChart {
  return {
    id: 'c1',
    title: 'Purchases',
    chart_type: 'kpi',
    chart_spec: { chart_type: 'kpi', title: 'Purchases', x_axis: { key: 'x', label: 'x' }, series: [] },
    data: [{ purchases: 1 }],
    data_meta: { refreshed_at: '2026-10-17T01:00:00+00:00', venue_id: 'v1', period: 'this_month' },
    script: { connector: 'loadedhub', action: 'get_purchase_cogs', params: {} },
    position: 0,
    created_at: '2026-10-16T00:00:00+00:00',
    ...overrides,
  };
}

describe('tileInterval', () => {
  it("is the tile's own setting when it has one", () => {
    const c = chart({ chart_spec: { ...chart().chart_spec, refresh_interval_seconds: 1800 } });
    expect(tileInterval(c, 300)).toBe(1800);
  });

  it('follows the dashboard when the tile says nothing', () => {
    expect(tileInterval(chart(), 300)).toBe(300);
    expect(tileInterval(chart(), null)).toBe(0);
  });

  it('0 means manual even when the dashboard refreshes', () => {
    const c = chart({ chart_spec: { ...chart().chart_spec, refresh_interval_seconds: 0 } });
    expect(tileInterval(c, 300)).toBe(0);
  });
});

describe('tilesRefreshLabel', () => {
  it('names one cadence or a range, and nothing for manual-only boards', () => {
    expect(tilesRefreshLabel([300, 300])).toBe('Refreshes every 5 min');
    expect(tilesRefreshLabel([300, 900, 1800, 0])).toBe('Tiles refresh every 5–30 min');
    expect(tilesRefreshLabel([0, 0])).toBeNull();
    expect(tilesRefreshLabel([])).toBeNull();
  });
});

describe('cacheIsFresh', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-17T01:02:00+00:00')); // two minutes after the cache
  });
  afterEach(() => vi.useRealTimers());

  it('is fresh when the rows are for this venue and period and younger than the interval', () => {
    expect(cacheIsFresh(chart(), 300, 'v1', 'this_month')).toBe(true);
  });

  it('is stale once the interval has elapsed', () => {
    expect(cacheIsFresh(chart(), 60, 'v1', 'this_month')).toBe(false);
  });

  it('is stale for another venue, another period, or all venues', () => {
    expect(cacheIsFresh(chart(), 300, 'v2', 'this_month')).toBe(false);
    expect(cacheIsFresh(chart(), 300, 'v1', 'last_month')).toBe(false);
    expect(cacheIsFresh(chart(), 300, '', 'this_month')).toBe(false);
  });

  it("'' on the page is the cache's __all__", () => {
    const c = chart({ data_meta: { refreshed_at: '2026-10-17T01:00:00+00:00', venue_id: '__all__', period: 'this_month' } });
    expect(cacheIsFresh(c, 300, '', 'this_month')).toBe(true);
  });

  it('is never fresh without rows or without a record of what they are for', () => {
    expect(cacheIsFresh(chart({ data: [] }), 300, 'v1', 'this_month')).toBe(false);
    expect(cacheIsFresh(chart({ data_meta: null }), 300, 'v1', 'this_month')).toBe(false);
  });

  it('a manual tile keeps its rows for as long as they match', () => {
    const c = chart({ chart_spec: { ...chart().chart_spec, refresh_interval_seconds: 0 } });
    vi.setSystemTime(new Date('2026-10-18T01:00:00+00:00')); // a day later
    expect(cacheIsFresh(c, 300, 'v1', 'this_month')).toBe(true);
    expect(cacheIsFresh(c, 300, 'v2', 'this_month')).toBe(false);
  });
});
