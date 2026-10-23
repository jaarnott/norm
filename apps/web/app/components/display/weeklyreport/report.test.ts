import { describe, it, expect } from 'vitest';
import { change, clockLabel, cogsLine, cogsTotal, extractDoc, formatPct, likeForLike, topSellers, tradingDays } from './report';

describe('trading days', () => {
  it('names the last trading day, not the morning the window ends on', () => {
    // get_daily_performance, period "last week", local data 23 Oct 2026.
    expect(tradingDays({ start: '2026-10-12T07:00:00+13:00', end: '2026-10-19T06:59:59+13:00', day_start: '07:00' })).toBe('Mon 12 Oct to Sun 18 Oct');
  });

  it('keeps a midnight-to-midnight window whole', () => {
    expect(tradingDays({ start: '2026-10-12T00:00:00+13:00', end: '2026-10-18T23:59:59+13:00', day_start: '00:00' })).toBe('Mon 12 Oct to Sun 18 Oct');
    expect(tradingDays({ start: '2026-12-28T07:00:00+13:00', end: '2027-01-04T06:59:59+13:00', day_start: '07:00' })).toBe('Mon 28 Dec to Sun 3 Jan');
    expect(tradingDays(undefined)).toBeNull();
    expect(clockLabel('07:00')).toBe('7:00am');
  });
});

describe('the figures', () => {
  it('compares the group with last year only where a venue traded a year ago', () => {
    const rows = [
      { venue: 'Bessie', sales: 64953.5, last_year: 70230 },
      { venue: 'Freeman', sales: 31628.55, last_year: 0 },
      { venue: 'DSC', sales: 35721.94, last_year: 25750.8 },
    ];
    const lfl = likeForLike(rows);
    expect(lfl.venues).toBe(2);
    expect(lfl.of).toBe(3);
    expect(lfl.sales).toBeCloseTo(100675.44, 2);
    expect(lfl.change).toBeCloseTo(100675.44 / 95980.8 - 1, 6);
  });

  it('formats signed and plain percentages', () => {
    expect(formatPct(0.0572, { signed: true })).toBe('+5.7%');
    expect(formatPct(-0.0654, { signed: true })).toBe('−6.5%');
    expect(formatPct(0.3052)).toBe('30.5%');
    expect(formatPct(null)).toBe('—');
    expect(change(100, 0)).toBeNull();
  });

  it('adds cost of goods up from each venue’s column sums', () => {
    const a = cogsLine('v1', 'DSC', { summary: { column_sums: { cost: 6785.96, revenue: 31068.19 } } });
    const b = cogsLine('v2', 'La Zeppa', null, 'Loaded timed out');
    expect(a.pct).toBeCloseTo(0.2184, 4);
    expect(b.pct).toBeNull();
    expect(cogsTotal([a, b])).toMatchObject({ revenue: 31068.19, cost: 6785.96, venues: 1 });
  });

  it('drops the (others) row from the top sellers', () => {
    const rows = [{ item: 'Speights GMA', sales: 8484, quantity: 1422 }, { item: '(others)', sales: 1, quantity: 1 }];
    expect(topSellers({ rows }).map((r) => r.item)).toEqual(['Speights GMA']);
  });

  it('finds a tool document in body.data or the body', () => {
    expect(extractDoc<{ rows: unknown[] }>({ success: true, data: { rows: [] } }, 'rows')).toEqual({ rows: [] });
    expect(extractDoc({ rows: [] }, 'rows')).toEqual({ rows: [] });
    expect(extractDoc({ success: false, error: 'x' }, 'rows')).toBeNull();
  });
});
