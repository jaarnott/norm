import { describe, expect, it } from 'vitest';
import {
  bottomSellers, buildCompare, clockLabel, comparisonLabel, groupSplit, isConnectionError, periodTotal,
  previousPeriod, rangeLabel, readDoc, searchItems, shiftDate, staffLines, topSellers, tradingDateOf,
  type DailyDoc, type ItemRow,
} from './salesLogic';

// Shapes copied from real get_sales answers (Freeman & Grey, Oct 2026).
const week = (over: Partial<DailyDoc['window']> = {}): DailyDoc['window'] => ({
  start: '2026-10-12T07:00:00+13:00', end: '2026-10-19T06:59:59+13:00', label: 'Last week', day_start: '07:00', ...over,
});

const lastWeek: DailyDoc = {
  window: week(),
  rows: [
    { date: '2026-10-12', day: 'Monday', actual: 199.95, budget: 850, last_year: 0 },
    { date: '2026-10-13', day: 'Tuesday', actual: 4782.6, budget: 4500, last_year: 0 },
    { date: '2026-10-17', day: 'Saturday', actual: 16164.5, budget: 11000, last_year: 0 },
  ],
};

const weekBefore: DailyDoc = {
  window: week({ start: '2026-10-05T07:00:00+13:00', label: 'Week of 05 Oct' }),
  rows: [
    { date: '2026-10-05', day: 'Monday', actual: 1374.25 },
    { date: '2026-10-06', day: 'Tuesday', actual: 2154.15 },
    { date: '2026-10-10', day: 'Saturday', actual: 17767.4 },
  ],
};

describe('readDoc', () => {
  it('finds the document inside data', () => {
    const { doc, error } = readDoc<DailyDoc>({ success: true, data: lastWeek, error: null }, 'rows');
    expect(error).toBeNull();
    expect(doc?.rows).toHaveLength(3);
  });
  it("returns the tool's own error, not an empty document", () => {
    const body = { success: true, data: { window: {}, error: 'Missing required fields: posIdentifier' }, error: null };
    expect(readDoc(body, 'rows')).toEqual({ doc: null, error: 'Missing required fields: posIdentifier' });
  });
  it('spots a connection failure', () => {
    expect(isConnectionError('No credentials configured for loadedhub')).toBe(true);
    expect(isConnectionError('Could not resolve the period')).toBe(false);
  });
});

describe('periods', () => {
  it('steps calendar dates across months and years', () => {
    expect(shiftDate('2026-10-12', -7)).toBe('2026-10-05');
    expect(shiftDate('2026-03-01', -1)).toBe('2026-02-28');
    expect(shiftDate('2026-01-01', -1)).toBe('2025-12-31');
  });
  it('names the period before, in words the tool resolves itself', () => {
    expect(previousPeriod('last week', '2026-10-12T07:00:00+13:00')).toBe('week beginning 2026-10-05');
    expect(previousPeriod('this week', '2026-10-19T07:00:00+13:00')).toBe('last week');
    expect(previousPeriod('this month', '2026-10-01T07:00:00+13:00')).toBe('last month');
    expect(previousPeriod('last month', '2026-09-01T07:00:00+12:00')).toBe('August 2026');
    expect(previousPeriod('last month', '2026-01-01T07:00:00+13:00')).toBe('December 2025');
    expect(previousPeriod('yesterday', '2026-10-22T07:00:00+13:00')).toBe('2026-10-21');
  });
  it('labels the comparison for the period', () => {
    expect(comparisonLabel('previous', 'last week')).toBe('Week before');
    expect(comparisonLabel('previous', 'last month')).toBe('Month before');
    expect(comparisonLabel('previous', 'yesterday')).toBe('Day before');
    expect(comparisonLabel('budget', 'last week')).toBe('Budget');
  });
  it('formats dates and the day start for people', () => {
    expect(rangeLabel('2026-10-12', '2026-10-18')).toBe('Mon 12 Oct – Sun 18 Oct 2026');
    expect(rangeLabel('2026-10-22', '2026-10-22')).toBe('Thu 22 Oct 2026');
    expect(clockLabel('07:00')).toBe('7:00am');
    expect(clockLabel('17:30')).toBe('5:30pm');
  });
  it('puts the small hours on the previous trading day', () => {
    const w = week({ incomplete: true, through: '2026-10-24T02:10:00+13:00' });
    expect(tradingDateOf(w)).toBe('2026-10-23');
    expect(tradingDateOf(week({ incomplete: true, through: '2026-10-23T23:50:30+13:00' }))).toBe('2026-10-23');
    expect(tradingDateOf(week())).toBeNull();
  });
});

describe('buildCompare', () => {
  it('matches the period before day for day', () => {
    const c = buildCompare(lastWeek, 'previous', weekBefore);
    expect(c.rows[0]).toMatchObject({ date: '2026-10-12', other: 1374.25, otherDate: '2026-10-05', diff: -1174.3 });
    expect(c.actual).toBe(21147.05);
    expect(c.other).toBe(21295.8);
    expect(c.pct).toBe(-0.7);
    expect(c.best).toEqual({ date: '2026-10-17', day: 'Saturday', actual: 16164.5 });
    expect(c.otherRange).toEqual(['2026-10-05', '2026-10-10']);
  });
  it('counts only the days both months have', () => {
    const sep: DailyDoc = { window: week(), rows: [{ date: '2026-09-29', day: 'Tuesday', actual: 100 }, { date: '2026-09-30', day: 'Wednesday', actual: 200 }] };
    const aug: DailyDoc = { window: week(), rows: [{ date: '2026-08-29', day: 'Saturday', actual: 50 }] };
    const c = buildCompare(sep, 'previous', aug);
    expect(c.rows[1]).toMatchObject({ other: null, diff: null });
    expect([c.actual, c.other, c.days]).toEqual([100, 50, 1]);
    expect(c.otherRange).toEqual(['2026-08-29', '2026-08-29']);
  });
  it('reads budget and last year off the same rows', () => {
    const b = buildCompare(lastWeek, 'budget');
    expect(b.other).toBe(16350);
    expect(b.otherEmpty).toBe(false);
    const ly = buildCompare(lastWeek, 'last_year');
    expect(ly.otherEmpty).toBe(true);
    expect(ly.pct).toBeNull();
  });
  it('leaves days not traded yet out of the totals', () => {
    const thisWeek: DailyDoc = {
      window: week({ start: '2026-10-19T07:00:00+13:00', incomplete: true, through: '2026-10-23T23:50:30+13:00' }),
      rows: [
        { date: '2026-10-23', day: 'Friday', actual: 7138.55, budget: 6000 },
        { date: '2026-10-24', day: 'Saturday', actual: 0, budget: 12100 },
      ],
    };
    const c = buildCompare(thisWeek, 'budget');
    expect(c.rows[1]).toMatchObject({ future: true, actual: null, diff: null });
    expect(c.other).toBe(6000);
    expect(c.days).toBe(1);
    expect(periodTotal(thisWeek)).toBe(7138.55);
  });
});

const items: ItemRow[] = [
  { item: 'Vodka', group: 'MAIN', category: 'Beverage', sales: 2933, quantity: 245 },
  { item: 'Buffalo Chicken', group: '10inch PIZZAS', category: 'Food', sales: 657, quantity: 55 },
  { item: 'Mixed Platter.', group: 'PLATTERS', category: 'Food', sales: 510, quantity: 6 },
  { item: 'Chambord', group: 'LIQUERS', category: 'Beverage', sales: 0, quantity: 1 },
  { item: 'Room hire', group: 'FUNCTION', category: 'Function', sales: 300, quantity: 1 },
  { item: 'Mystery', group: 'MISC', category: 'Uncategorised', sales: 40, quantity: 2 },
  { item: '(others)', sales: 9999, quantity: 999 },
];

describe('products', () => {
  it('splits sales by revenue group, the three groups first', () => {
    const split = groupSplit(items);
    expect(split.map((g) => g.group)).toEqual(['Food', 'Beverage', 'Function', 'Unassigned']);
    expect(split[0]).toMatchObject({ sales: 1167, items: 2, revenueGroup: true });
    expect(split[2].revenueGroup).toBe(false);
    expect(split.reduce((s, g) => s + g.share, 0)).toBeCloseTo(100, 0);
  });
  it('ranks top and bottom sellers, skipping the roll-up and $0 items', () => {
    expect(topSellers(items, 'sales', 2).map((r) => r.item)).toEqual(['Vodka', 'Buffalo Chicken']);
    expect(bottomSellers(items, 'sales', 2).map((r) => r.item)).toEqual(['Mystery', 'Room hire']);
    expect(bottomSellers(items, 'quantity', 2).map((r) => r.item)).toEqual(['Chambord', 'Room hire']);
  });
  it('searches names and POS groups', () => {
    expect(searchItems(items, 'pizza').map((r) => r.item)).toEqual(['Buffalo Chicken']);
    expect(searchItems(items, '')).toHaveLength(6);
  });
});

describe('staff', () => {
  it('adds the average order and share of sales', () => {
    const lines = staffLines([
      { staff: 'Santi', orders: 468, sales: 8626.5 },
      { staff: 'Matt', orders: 731, sales: 12049 },
      { staff: 'Dale', orders: 0, sales: 25 },
    ]);
    expect(lines[0]).toMatchObject({ staff: 'Matt', average: 16.48 });
    expect(lines[2].average).toBeNull();
    expect(lines[0].share + lines[1].share + lines[2].share).toBeCloseTo(100, 0);
  });
});
