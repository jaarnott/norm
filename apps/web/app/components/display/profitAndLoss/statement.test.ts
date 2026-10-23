import { describe, it, expect } from 'vitest';
import {
  assignBlocks, buildStatement, comparisonMonths, mergeOrdered, monthLabel, monthOfDoc, monthOptions,
  movementLine, biggestMoves, money0, signedMoney0, extractPlDoc, monthInWords,
  type PlDoc,
} from './statement';
import { SEP_2026, AUG_2026, SEP_2025 } from './fixtures';

describe('months', () => {
  it('starts the picker at the last full month and steps back across a year', () => {
    const opts = monthOptions(new Date(2026, 0, 15), 3);
    expect(opts.map(monthLabel)).toEqual(['December 2025', 'November 2025', 'October 2025']);
  });

  it('compares with the month before and the same month a year earlier', () => {
    expect(comparisonMonths({ year: 2026, month: 1 }).map(monthLabel))
      .toEqual(['January 2026', 'December 2025', 'January 2025']);
    expect(monthInWords({ year: 2025, month: 12 }, { year: 2026, month: 1 })).toBe('December 2025');
    expect(monthInWords({ year: 2026, month: 8 }, { year: 2026, month: 9 })).toBe('August');
  });

  it('reads a document as a month only when it is a whole calendar month', () => {
    expect(monthOfDoc(SEP_2026)).toEqual({ year: 2026, month: 9 });
    expect(monthOfDoc({ from: '2026-02-01', to: '2026-02-28' })).toEqual({ year: 2026, month: 2 });
    expect(monthOfDoc({ from: '2026-09-01', to: '2026-09-23' })).toBeNull();
  });
});

describe('sections follow the organisation layout, closed by Xero totals', () => {
  it('puts wages under cost of sales and income tax below operating profit (real layout)', () => {
    const { blocks, sure } = assignBlocks(AUG_2026);
    const byTitle = Object.fromEntries((AUG_2026.sections || []).map((s, i) => [s.title ?? '(computed)', blocks[i]]));
    expect(sure).toBe(true);
    expect(byTitle['Income']).toBe('income');
    expect(byTitle['Cost of Goods']).toBe('cost_of_sales');
    expect(byTitle['Salaries & Wages']).toBe('cost_of_sales');
    expect(byTitle['Less Operating Expenses']).toBe('operating_expenses');
    expect(byTitle['Utility Costs']).toBe('operating_expenses');
    expect(byTitle['(computed)']).toBeNull(); // Operating Profit row
    expect(byTitle['Non-Operating Expenses']).toBe('other');
  });

  it('keeps Xero default-layout other income out of operating expenses', () => {
    const doc: PlDoc = {
      totals: { income: [1000], cost_of_sales: [400], gross_profit: [600], operating_expenses: [300], net_profit: [350] },
      sections: [
        { title: 'Income', total: [1000], rows: [{ name: 'Sales', values: [1000] }] },
        { title: 'Less Cost of Sales', total: [400], rows: [{ name: 'Purchases', values: [400] }] },
        { title: null, total: null, rows: [{ name: 'Gross Profit', values: [600] }] },
        { title: 'Plus Other Income', total: [50], rows: [{ name: 'Interest Income', values: [50] }] },
        { title: 'Less Operating Expenses', total: [300], rows: [{ name: 'Rent', values: [300] }] },
      ],
    };
    const { blocks, sure } = assignBlocks(doc);
    expect(blocks).toEqual(['income', 'cost_of_sales', null, 'other_income', 'operating_expenses']);
    expect(sure).toBe(true);
  });

  it('flags a layout that does not add up instead of pretending', () => {
    const doc: PlDoc = {
      totals: { income: [1000], cost_of_sales: [400], operating_expenses: [300], net_profit: [300] },
      sections: [
        { title: 'Income', total: [999], rows: [{ name: 'Sales', values: [999] }] },
        { title: 'Costs', total: [700], rows: [{ name: 'Purchases', values: [700] }] },
      ],
    };
    expect(assignBlocks(doc).sure).toBe(false);
  });
});

describe('merged statement (real September 2026 / August 2026 / September 2025)', () => {
  const st = buildStatement([SEP_2026, AUG_2026, SEP_2025]);

  it('carries Xero headline totals per period', () => {
    expect(st.headline.income).toEqual([210572.62, 237425.63, 155385.46]);
    expect(st.headline.net_profit).toEqual([39467.91, 35532.66, -1969.46]);
    expect(st.sure).toBe(true);
  });

  it('groups expenses by the organisation groups, a line missing in a month reading 0', () => {
    const opex = st.blocks.find((b) => b.key === 'operating_expenses')!;
    expect(opex.groups.map((g) => g.title)).toEqual([
      'Less Operating Expenses', 'Administrative & General', 'Advertising & Promotions',
      'Employment Related Expenses', 'Operating Costs', 'Property Costs', 'Utility Costs',
    ]);
    expect(opex.groups[0].plain).toBe(true);
    expect(opex.groups[0].values).toEqual([2333.39, 3585.76, 0]); // absent in Sep 2025
    const promos = opex.groups[2].lines.find((l) => l.name === 'Promotions')!;
    expect(promos.values).toEqual([0, 0, 1091.61]);
    expect(opex.total).toEqual([66884.7, 60513.11, 60851.16]);
  });

  it('shows non-operating lines below operating profit, which is computed', () => {
    const other = st.blocks.find((b) => b.key === 'other')!;
    expect(other.groups[0].title).toBe('Non-Operating Expenses');
    expect(other.total).toEqual([0, 23778.93, 7045.71]);
    expect(st.operatingProfit[2]).toBeCloseTo(5076.25, 2);
    expect(st.operatingProfit[1]).toBeCloseTo(59311.59, 2);
  });

  it('leaves a period it could not read empty rather than zero', () => {
    const partial = buildStatement([SEP_2026, null, SEP_2025]);
    expect(partial.present).toEqual([true, false, true]);
    expect(partial.headline.income[1]).toBeNull();
    expect(partial.blocks[0].groups[0].lines[0].values[1]).toBeNull();
  });
});

describe('plain words', () => {
  const st = buildStatement([SEP_2026, AUG_2026, SEP_2025]);

  it('names the net profit change, gross margin and the biggest line moves', () => {
    expect(movementLine(st, 1, 'August')).toBe(
      'Net profit was $39,468, $3,935 better than August. Gross profit was 50.5% of income, the same as in August. '
      + 'Biggest changes: Kitchen Sales down $20,124, Income Tax Expense down $15,978 and Rent up $10,562.',
    );
    expect(movementLine(st, 2, 'September 2025')).toBe(
      'Net profit was $39,468, $41,437 better than September 2025. Gross profit was 50.5% of income, up from 42.4%. '
      + 'Biggest changes: Bar Sales up $41,453, Kitchen Sales up $13,734 and Kitchen Team up $7,913.',
    );
  });

  it('says so when the comparison month has nothing posted', () => {
    const empty: PlDoc = { from: '2025-09-01', to: '2025-09-30', totals: {}, sections: [] };
    const s2 = buildStatement([SEP_2026, AUG_2026, empty]);
    expect(movementLine(s2, 2, 'September 2025')).toMatch(/nothing posted for September 2025/);
    expect(biggestMoves(s2, 1).length).toBe(3);
  });

  it('formats whole dollars with the sign in front', () => {
    expect(money0(-1976.06)).toBe('-$1,976');
    expect(signedMoney0(3075.4)).toBe('+$3,075');
    expect(signedMoney0(-0.2)).toBe('$0');
  });
});

describe('helpers', () => {
  it('merges keyed lists keeping each list order', () => {
    expect(mergeOrdered([['A', 'B', 'C'], ['X', 'A', 'D', 'C']])).toEqual(['X', 'A', 'D', 'B', 'C']);
    expect(mergeOrdered([['B', 'C'], ['A', 'B']])).toEqual(['A', 'B', 'C']);
  });

  it('finds the document in body.data or body', () => {
    expect(extractPlDoc({ success: true, data: SEP_2026 })).toBe(SEP_2026);
    expect(extractPlDoc(SEP_2026)).toBe(SEP_2026);
    expect(extractPlDoc({ success: false, data: { error: 'not connected' } })).toBeNull();
  });
});
