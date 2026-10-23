import { describe, it, expect } from 'vitest';
import {
  bucketsWithoutSales, canOpen, categoryOf, cogsShareExGst, countKind, countLabel, defaultFilter,
  expectedOnHand, filterRows, formatPct, formatPoints, isHubMissing, isLegacyMissing, previousCount,
  snapshotMatches, sortRows, stocktakedCategories, valueByCategory,
  type PurchaseCogsRow, type StocktakeRow,
} from './logic';

// Rows as get_stocktakes view 'list' returns them (Mr Murdochs and Freeman &
// Grey in production, Oct 2026), trimmed.
const ROWS: StocktakeRow[] = [
  { id: 'p1', title: '4/06/2026 - All Food', template: 'All Food', completed: null, pending: true },
  { id: 'a1', title: '6 October 2026 - Ad-hoc count', template: 'Ad-hoc count', completed: '2026-10-06T00:01:00+00:00', pending: false },
  { id: 'a2', title: '3 October 2026 - Kegs', template: 'Ad-hoc count', completed: '2026-10-03T06:25:00+00:00', pending: false },
  { id: 'b3', title: '15/09/2026 - All Beverage', template: 'All Beverage', completed: '2026-09-15T03:17:00+00:00', pending: false },
  // Counted 30 June, completed two months later — sorts by when it was completed.
  { id: 'b2', title: '30/06/2026 - All Beverage', template: 'All Beverage', completed: '2026-08-31T11:39:00+12:00', pending: false },
  { id: 'f2', title: '1/07/2026 - All Food', template: 'All Food', completed: '2026-08-31T11:26:00+12:00', pending: false },
  { id: 'd1', title: '25/06/2026 - BOTW DAILIES', template: 'BOTW DAILIES', completed: '2026-06-25T15:10:00+12:00', pending: false },
  { id: 'b1', title: '23/06/2026 - All Beverage', template: 'All Beverage', completed: '2026-06-23T11:15:11+12:00', pending: false },
  { id: 'f1', title: '22/06/2026 - All Food', template: 'All Food', completed: '2026-06-22T09:44:23+12:00', pending: false },
];

describe('kinds of count', () => {
  it('tells full, area and ad-hoc counts apart', () => {
    expect(countKind('All Food')).toBe('full');
    expect(countKind('All Other Stock')).toBe('full');
    expect(countKind('BOTW DAILIES')).toBe('area');
    expect(countKind('Ad-hoc count')).toBe('adhoc');
    expect(countKind('')).toBe('adhoc');
    expect(categoryOf('All Beverage')).toBe('Beverage');
    expect(categoryOf('DAILY KEGS')).toBeNull();
  });

  it('labels what was counted in words', () => {
    expect(countLabel({ template: 'All Food', title: '22/06/2026 - All Food' })).toBe('Food — full count');
    expect(countLabel({ template: 'BOTW DAILIES', title: 'x' })).toBe('BOTW DAILIES');
    expect(countLabel({ template: 'Ad-hoc count', title: '3 October 2026 - Kegs' })).toBe('Ad-hoc: Kegs');
    expect(countLabel({ template: 'Ad-hoc count', title: '6 October 2026 - Ad-hoc count' })).toBe('Ad-hoc count');
  });

  it('opens completed full and area counts only', () => {
    const by = Object.fromEntries(ROWS.map((r) => [r.id, r]));
    expect(canOpen(by.f2)).toBe(true);
    expect(canOpen(by.d1)).toBe(true);
    expect(canOpen(by.a1)).toBe(false);
    expect(canOpen(by.p1)).toBe(false);
  });
});

describe('the list', () => {
  it('sorts completed newest first and the unfinished last', () => {
    expect(sortRows(ROWS).map((r) => r.id)).toEqual(['a1', 'a2', 'b3', 'b2', 'f2', 'd1', 'b1', 'f1', 'p1']);
  });

  it('filters by kind and status', () => {
    expect(filterRows(ROWS, 'full').map((r) => r.id)).toEqual(['b3', 'b2', 'f2', 'b1', 'f1']);
    expect(filterRows(ROWS, 'area').map((r) => r.id)).toEqual(['d1']);
    expect(filterRows(ROWS, 'adhoc').map((r) => r.id)).toEqual(['a1', 'a2']);
    expect(filterRows(ROWS, 'pending').map((r) => r.id)).toEqual(['p1']);
    expect(filterRows(ROWS, 'completed')).toHaveLength(8);
  });

  it('starts on full counts, or whatever the venue has', () => {
    expect(defaultFilter(ROWS)).toBe('full');
    expect(defaultFilter(ROWS.filter((r) => r.template === 'Ad-hoc count'))).toBe('completed');
    expect(defaultFilter(ROWS.filter((r) => r.pending))).toBe('pending');
  });

  it('pairs a count with the previous completed count of the SAME list', () => {
    const by = Object.fromEntries(ROWS.map((r) => [r.id, r]));
    expect(previousCount(ROWS, by.b3)?.id).toBe('b2');
    expect(previousCount(ROWS, by.f2)?.id).toBe('f1');
    expect(previousCount(ROWS, by.f1)).toBeNull(); // the pending 4/06 Food count is never a partner
    expect(previousCount(ROWS, by.d1)).toBeNull();
    expect(previousCount(ROWS, by.a1)).toBeNull(); // ad-hoc counts are never paired
  });

  it('knows which categories can be costed from stock', () => {
    expect(stocktakedCategories(ROWS).map((c) => c.category)).toEqual(['Beverage', 'Food']);
    // One Food count alone can't make a period.
    expect(stocktakedCategories(ROWS.filter((r) => r.id !== 'f1')).map((c) => c.category)).toEqual(['Beverage']);
  });
});

describe('one count', () => {
  it('expected on hand is opening + received − expected use', () => {
    // Free Range Eggs, Lott Cafe Jun→Sep 2026: counted 2001.337, variance −3655.67.
    const v = { opening: 2202.681, received: 24300, closing: 2001.337, expected_use: 20845.675 };
    expect(expectedOnHand(v)).toBeCloseTo(5657.006, 3);
    expect(v.closing - expectedOnHand(v)).toBeCloseTo(-3655.67, 2);
  });

  it('adds stock on hand up by category, biggest first', () => {
    const cats = valueByCategory({
      rows: [
        { name: 'SAUCE TOMATO', category: 'Dry Goods', quantity_on_hand: 10.5, value_on_hand: 220.26 },
        { name: 'RUMP STEAK', category: 'Meats', quantity_on_hand: 5, value_on_hand: 124.95 },
        { name: 'FLOUR', category: 'Dry Goods', quantity_on_hand: 2, value_on_hand: 30 },
        { name: 'EMPTY', category: 'Dairy', quantity_on_hand: 0, value_on_hand: 0 },
      ],
    });
    expect(cats.map((c) => [c.category, Math.round(c.value * 100) / 100, c.items])).toEqual([
      ['Dry Goods', 250.26, 2],
      ['Meats', 124.95, 1],
    ]);
    expect(cats[0].share + cats[1].share).toBeCloseTo(1, 6);
  });

  it('trusts the snapshot only when it matches the count', () => {
    expect(snapshotMatches(2843.83, 2843.8281)).toBe(true); // Dunedin Social Club, 17 Aug 2026
    expect(snapshotMatches(3100, 2843.83)).toBe(false);
  });

  it('puts cost of goods against sales ex GST', () => {
    // Dunedin 11→17 Aug 2026: $1,408.61 on $3,435.08 ex GST (Loaded says 35.7% — of $3,950 incl. GST).
    expect(formatPct(cogsShareExGst(1408.61, 3435.081))).toBe('41.0%');
    expect(cogsShareExGst(100, 0)).toBeNull();
  });
});

describe('purchases-based cost of goods', () => {
  it('flags months that bought stock with no sales', () => {
    const rows = [
      { bucket: 'July 2026', purchases: 21579.01, sales: 0 },
      { bucket: 'September 2026', purchases: 24599.65, sales: 76516.28 },
      { bucket: 'October 2026', purchases: 0, sales: 0 },
    ] as PurchaseCogsRow[];
    expect(bucketsWithoutSales(rows)).toEqual(['July 2026']);
  });

  it('formats shares and point changes', () => {
    expect(formatPct(0.2559)).toBe('25.6%');
    expect(formatPct(null)).toBe('—');
    expect(formatPoints(0.0674)).toBe('+6.7 points');
    expect(formatPoints(-0.0005)).toBe('no change');
    expect(formatPoints(-0.012)).toBe('−1.2 points');
  });
});

describe('errors', () => {
  it('recognises a missing Loaded Legacy login', () => {
    const msg = "could not list stocktakes: 'La Zeppa' isn't a venue you can use loaded_legacy at — use the venue's full name (list_venues), or connect it first";
    expect(isLegacyMissing(msg)).toBe(true);
    expect(isHubMissing(msg)).toBe(false);
    expect(isHubMissing('No credentials configured for loadedhub')).toBe(true);
    expect(isLegacyMissing('LoadedHub could not produce the stocktake report')).toBe(false);
  });
});
