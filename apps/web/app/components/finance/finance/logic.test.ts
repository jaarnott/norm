import { describe, expect, it } from 'vitest';
import {
  addDays,
  addMonths,
  band,
  budgetPnl,
  centsInput,
  dayCell,
  daysInMonth,
  defaultMonth,
  detectImportLayout,
  fiscalYearLabel,
  fiscalYearMonths,
  fiscalYearOf,
  fiscalYearRange,
  groupIntoWeeks,
  holidaySyncText,
  isoWeekday,
  mondayOf,
  parseAmount,
  parseRatio,
  pct,
  pushOutcome,
  rangeLabel,
  sumCents,
  summaryCsv,
  variancePct,
} from './logic';
import type { BudgetPlanPeriod, Figures } from './types';

describe('fiscal years from the organisation’s start month', () => {
  it('names the year by the calendar year it ends in', () => {
    // NZ: April start — Apr 2026 to Mar 2027 is FY2027.
    expect(fiscalYearOf('2026-04-01', 4)).toBe(2027);
    expect(fiscalYearOf('2027-03-31', 4)).toBe(2027);
    expect(fiscalYearOf('2026-03-31', 4)).toBe(2026);
    // AU: July start.
    expect(fiscalYearOf('2026-07-01', 7)).toBe(2027);
    expect(fiscalYearOf('2026-06-30', 7)).toBe(2026);
    // A January start is the calendar year.
    expect(fiscalYearOf('2026-12-31', 1)).toBe(2026);
  });

  it('lists the twelve months first to last', () => {
    const months = fiscalYearMonths(2027, 4);
    expect(months[0]).toEqual({ year: 2026, month: 4 });
    expect(months[8]).toEqual({ year: 2026, month: 12 });
    expect(months[9]).toEqual({ year: 2027, month: 1 });
    expect(months[11]).toEqual({ year: 2027, month: 3 });
    expect(fiscalYearMonths(2026, 1)[0]).toEqual({ year: 2026, month: 1 });
    expect(fiscalYearLabel(2027, 4)).toBe('FY2027 · Apr 2026 – Mar 2027');
    expect(fiscalYearLabel(2026, 1)).toBe('2026');
    expect(fiscalYearRange(2027, 4)).toEqual({ from: '2026-04-01', to: '2027-03-31' });
    expect(fiscalYearRange(2027, 7)).toEqual({ from: '2026-07-01', to: '2027-06-30' });
  });

  it('opens a grid on today’s month when it is in the year, else the first', () => {
    expect(defaultMonth(2027, 4, '2026-10-20')).toEqual({ year: 2026, month: 10 });
    expect(defaultMonth(2028, 4, '2026-10-20')).toEqual({ year: 2027, month: 4 });
  });
});

describe('calendar arithmetic without the browser zone', () => {
  it('steps months and days across year ends', () => {
    expect(addMonths({ year: 2026, month: 12 }, 1)).toEqual({ year: 2027, month: 1 });
    expect(addMonths({ year: 2027, month: 1 }, -1)).toEqual({ year: 2026, month: 12 });
    expect(daysInMonth({ year: 2026, month: 2 })).toBe(28);
    expect(daysInMonth({ year: 2028, month: 2 })).toBe(29);
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
  });

  it('knows weekdays and Mondays', () => {
    expect(isoWeekday('2026-10-19')).toBe(1); // Monday
    expect(isoWeekday('2026-10-18')).toBe(7); // Sunday
    expect(mondayOf('2026-10-21')).toBe('2026-10-19');
    expect(mondayOf('2026-10-19')).toBe('2026-10-19');
    expect(dayCell('2026-10-19')).toBe('Mon 19');
    expect(dayCell('2026-10-05', 1)).toBe('Mon 5');
    expect(rangeLabel('2026-10-12', '2026-10-18')).toBe('12 – 18 Oct 2026');
    expect(rangeLabel('2026-09-28', '2026-10-04')).toBe('28 Sep – 4 Oct 2026');
    expect(rangeLabel('2026-12-29', '2027-01-04')).toBe('29 Dec 2026 – 4 Jan 2027');
  });

  it('groups a month into Monday-to-Sunday weeks for subtotals', () => {
    const rows = ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05', '2026-10-06'].map((date) => ({ date }));
    const weeks = groupIntoWeeks(rows.reverse());
    expect(weeks.map((w) => w.week_start)).toEqual(['2026-09-28', '2026-10-05']);
    expect(weeks[0].rows.map((r) => r.date)).toEqual(['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']);
    expect(weeks[1].rows).toHaveLength(2);
  });
});

describe('grid maths', () => {
  it('sums a nullable column, and stays blank when nothing is filled', () => {
    expect(sumCents([{ v: 100 }, { v: null }, { v: 250 }], (r) => r.v)).toBe(350);
    expect(sumCents([{ v: null }, { v: undefined }], (r) => r.v)).toBeNull();
    expect(sumCents([], (r: { v: number }) => r.v)).toBeNull();
  });

  it('bands a variance Loaded − locked: above is green, below is red', () => {
    expect(band(1)).toBe('above');
    expect(band(-1)).toBe('below');
    expect(band(0)).toBe('level');
    expect(band(null)).toBe('level');
    expect(variancePct(5000, 100000)).toBe(0.05);
    expect(variancePct(5000, 0)).toBeNull();
    expect(variancePct(null, 100)).toBeNull();
  });
});

describe('number formats', () => {
  it('shows ratios, signed when asked', () => {
    expect(pct(0.2892)).toBe('28.9%');
    expect(pct(-0.03, 1, true)).toBe('−3.0%');
    expect(pct(0.062, 1, true)).toBe('+6.2%');
    expect(pct(0.062)).toBe('6.2%');
    expect(pct(null)).toBe('—');
  });

  it('reads typed dollars and percentages', () => {
    expect(parseAmount('1,978')).toBe(197800);
    expect(parseAmount('$1,978.50')).toBe(197850);
    expect(parseAmount('(200)')).toBe(-20000);
    expect(parseAmount('-200')).toBe(-20000);
    expect(parseAmount('')).toBeNull();
    expect(parseAmount('abc')).toBeNull();
    expect(centsInput(197800)).toBe('1978');
    expect(centsInput(197850)).toBe('1978.50');
    expect(centsInput(null)).toBe('');
    expect(parseRatio('29')).toBe(0.29);
    expect(parseRatio('29%')).toBe(0.29);
    expect(parseRatio('0.29')).toBe(0.29);
    expect(parseRatio('')).toBeNull();
  });
});

describe('the import parser toggle', () => {
  it('spots Orbit’s twelve-block spreadsheet tab, else reads date,amount,note', () => {
    const orbitRow = Array.from({ length: 60 }, (_, i) => (i % 5 === 0 ? '2026-04-01' : '100')).join(',');
    expect(detectImportLayout([orbitRow, orbitRow, orbitRow].join('\n'))).toBe('orbit');
    expect(detectImportLayout('date,amount,note\n2026-04-01,1978,Quiz night\n2026-04-02,2392')).toBe('generic');
    expect(detectImportLayout('')).toBe('generic');
  });
});

const figures = (over: Partial<Figures>): Figures =>
  ({
    sales: 61234.5, gst: 7987.1, sales_ex_gst: 53247.4, budget: 60000, budget_pct: 1.02, vs_budget: 1234.5,
    orders: 1810, order_value: 61240, average_order: 33.83, bills: 1750, discounts: 912.3, voids: 143.2, void_count: 12, waste: 20,
    food_sales: 30100, beverage_sales: 29600, other_sales: 500, food_share: 0.5, beverage_share: 0.49, split_mismatch: 1034.5,
    wages: 15400, wage_pct: 0.2892, food_wages: 6200, food_wage_pct: 0.23, beverage_wages: 9200, beverage_wage_pct: 0.17, other_wages: 0,
    hours: 552, kitchen_hours: 210, rostered_cost: 15100, rostered_hours: 540, actual_cost: 15300, actual_hours: 552,
    hours_variance: 12, cost_variance: 200, cash_variance: -12.4, total_variance: -31, cashup_buckets: 7,
    ...over,
  }) as Figures;

const plan: BudgetPlanPeriod = {
  start: '2026-10-12', end: '2026-10-18', days_with_budget: 7, budget: 36896, budget_cents: 3689600, budget_ex_gst: 32083.48,
  lines: { wages: 9304.2, wage_pct: 0.29, cogs_pct: 0.31, cogs: 9945.9, leave: 420, opex: 2770, depreciation: 230, rebates_pct: 0.02 },
  adjustments: { wages: 150, leave: 0 },
};

describe('the budget P&L beside the week’s actuals', () => {
  it('works the budget side down from sales ex GST, as Orbit’s Summary did', () => {
    const rows = budgetPnl(figures({}), plan);
    const by = Object.fromEntries(rows.map((r) => [r.label + (r.emphasis === 'italic' ? ':' + rows.indexOf(r) : ''), r]));
    expect(by['Sales (incl GST)'].budget).toBe(36896);
    expect(by['Wages'].budget).toBe(9304.2);
    // Actual wages carry the week's report_target adjustments.
    expect(by['Wages'].actual).toBe(15550);
    expect(by['Wages'].note).toContain('+150');
    const gp = by['Gross profit (before rebates)'];
    expect(gp.budget).toBeCloseTo(32083.48 - 9304.2 - 420 - 9945.9, 2);
    // No cost of goods for the actual side without Xero: the actual GP stays blank, never a fake number.
    expect(gp.actual).toBeNull();
    expect(by['Operating profit'].budget).toBeCloseTo(gp.budget! - 2770, 2);
    expect(by['Net profit'].budget).toBeCloseTo(gp.budget! - 2770 - 230, 2);
    expect(rows.find((r) => r.label === 'Rebates (% of beverage purchases)')!.budget).toBe(0.02);
  });

  it('fills the actual cost lines only when Xero sent them', () => {
    const rows = budgetPnl(figures({}), plan, { cost_of_goods: 10000, operating_expenses: 3000, depreciation: 200, finance_costs: 50 });
    const cogs = rows.find((r) => r.label === 'Cost of goods')!;
    expect(cogs.actual).toBe(10000);
    expect(cogs.note).toBeUndefined();
    expect(rows.find((r) => r.label === 'Net profit')!.actual).toBeCloseTo(53247.4 - 15550 - 0 - 10000 - 3000 - 200, 2);
    expect(rows.find((r) => r.label === 'Finance costs')).toMatchObject({ budget: null, actual: 50 });
  });

  it('is all blanks with no plan and no figures', () => {
    const rows = budgetPnl(null, null);
    expect(rows.every((r) => r.budget === null && r.actual === null)).toBe(true);
  });
});

describe('CSV', () => {
  it('lays the summary out one column pair per venue, then the group', () => {
    const csv = summaryCsv(
      [{ venue: 'La Zeppa', venue_id: 'v1', week: figures({ budget_plan: { week: plan, mtd: plan } }), mtd: figures({}) }, { venue: 'Broken', venue_id: 'v2', error: 'no token' }],
      { week: figures({}), mtd: figures({}) },
      'WE 18 Oct',
      'MTD',
    );
    const lines = csv.replace(/^\uFEFF/, '').split('\r\n');
    expect(lines[0]).toBe('Line,La Zeppa · WE 18 Oct,La Zeppa · MTD,Group · WE 18 Oct,Group · MTD');
    expect(lines[1]).toBe('Sales (incl GST),61234.5,61234.5,61234.5,61234.5');
    expect(lines.find((l) => l.startsWith('Budget: Wages,'))).toBe('Budget: Wages,9304.2,9304.2,,');
    expect(lines.find((l) => l.startsWith('Actual: Wages,'))).toBe('Actual: Wages,15550,15550,15400,15400');
  });
});

describe('the push outcome', () => {
  it('counts what Loaded took, and says when nothing changed', () => {
    expect(pushOutcome({ updated: 6, created: 1, unchanged: 0, skipped_zero: ['2026-10-14', '2026-10-15'], failed: [] })).toBe('6 updated, 1 created, 2 at $0 skipped.');
    expect(pushOutcome({ failed: ['2026-10-13: API error 404'] })).toBe('1 failed.');
    expect(pushOutcome({ unchanged: 3 })).toBe('3 unchanged.');
    expect(pushOutcome({})).toBe('Nothing to change.');
    expect(pushOutcome(null)).toBe('Done.');
  });
});

describe('the public holiday sync toast', () => {
  it('says what changed and how far Loaded’s dates run', () => {
    expect(holidaySyncText({ inserted: 3, updated: 0, removed: 1, dates_until: '2026-12-26' })).toBe(
      'Public holidays from Loaded: 3 added, 1 removed. Loaded holds public holidays up to Sat 26 Dec 2026.',
    );
    expect(holidaySyncText({ inserted: 0, updated: 0, removed: 0, dates_until: null })).toBe(
      'Public holidays from Loaded: nothing new. Loaded holds no public holidays for this venue.',
    );
  });
});
