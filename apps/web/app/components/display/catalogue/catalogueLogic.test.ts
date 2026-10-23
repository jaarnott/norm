import { describe, expect, it } from 'vitest';
import {
  draftOf, draftProblem, itemMergeTargets, itemUpdateParams, resultSentence, search, unitMergeTargets,
  type CatalogueItem, type CatalogueUnit,
} from './catalogueLogic';

const each: CatalogueUnit = { id: 'u-each', name: 'Each', type: 'Count', ratio: 1, items: 3, deactivated: false };
const dozen: CatalogueUnit = { id: 'u-12', name: '12 Pack', type: 'Count', ratio: 12, items: 1, deactivated: false };
const ea: CatalogueUnit = { id: 'u-ea', name: 'EA', type: 'Count', ratio: 1, items: 1, deactivated: false };
const kg: CatalogueUnit = { id: 'u-kg', name: 'Kilo', type: 'Weight', ratio: 1, items: 9, deactivated: false };
const units = [each, dozen, ea, kg];

const lemon = (over: Partial<CatalogueItem> = {}): CatalogueItem => ({
  id: 'i-lemon', name: 'Lemon', group_id: 'g-fruit', group: 'Fruit', unit_type: 2,
  counting_unit_id: 'u-each', counting_unit: 'Each', counting_unit_ratio: 1,
  ordering_unit_id: 'u-each', ordering_unit: 'Each', ordering_unit_ratio: 1,
  default_supplier_id: 's-fresh', default_supplier: 'Fresh Co', sales_tax: 1, deactivated: false,
  variants: [{ variant_id: 'v1', supplier_id: 's-fresh', supplier: 'Fresh Co', stock_code: 'LEM', unit_id: 'u-each', unit: 'Each', unit_cost: 0.5, default: true }],
  ...over,
});

describe('itemUpdateParams', () => {
  it('sends nothing when nothing changed', () => {
    const item = lemon();
    expect(itemUpdateParams(item, draftOf(item), units)).toBeNull();
  });

  it('sends a changed unit with its ratio — the tool refuses one without the other', () => {
    const item = lemon();
    const draft = { ...draftOf(item), ordering_unit_id: 'u-12' };
    expect(itemUpdateParams(item, draft, units)).toEqual({
      op: 'update', item_id: 'i-lemon', changes: { orderingUnitId: 'u-12', orderingUnitRatio: 12 },
    });
  });

  it('sends only the variant fields that changed, by variant id', () => {
    const item = lemon();
    const draft = draftOf(item);
    draft.variants[0] = { ...draft.variants[0], unit_cost: '0.55' };
    expect(itemUpdateParams(item, draft, units)?.variant_changes).toEqual([{ variant_id: 'v1', unitCost: 0.55 }]);
  });

  it('adds and removes variants', () => {
    const item = lemon();
    const draft = draftOf(item);
    draft.variants[0].removed = true;
    draft.variants.push({ supplier_id: 's-other', stock_code: ' L2 ', unit_id: 'u-12', unit_cost: '$6.00', default: true });
    const params = itemUpdateParams(item, draft, units);
    expect(params?.remove_variants).toEqual(['v1']);
    expect(params?.add_suppliers).toEqual([{ supplierId: 's-other', stockCode: 'L2', unitId: 'u-12', unitCost: 6, defaultForSupplier: true }]);
  });
});

describe('draftProblem', () => {
  it('wants exactly one default per supplier', () => {
    const draft = draftOf(lemon());
    draft.variants.push({ supplier_id: 's-fresh', stock_code: 'LEM2', unit_id: 'u-12', unit_cost: '5', default: true });
    expect(draftProblem(draft)).toMatch(/exactly one default/);
    draft.variants[1].default = false;
    expect(draftProblem(draft)).toBeNull();
  });

  it('reads a cost it can understand, or says so', () => {
    const draft = draftOf(lemon());
    draft.variants[0].unit_cost = 'cheap';
    expect(draftProblem(draft)).toMatch(/isn't a cost/);
  });
});

describe('merge targets', () => {
  it('offers only live items of the same unit type', () => {
    const items = [
      lemon(), lemon({ id: 'i-lemons', name: 'Lemons' }), lemon({ id: 'i-juice', name: 'Lemon juice', unit_type: 1 }),
      lemon({ id: 'i-old', name: 'Old lemon', deactivated: true }),
    ];
    expect(itemMergeTargets(items, items[0]).map(i => i.id)).toEqual(['i-lemons']);
  });

  it('offers only units of the same kind and size — merging different sizes changes every quantity', () => {
    expect(unitMergeTargets(units, each).map(u => u.id)).toEqual(['u-ea']);
  });
});

describe('search', () => {
  it('hides deactivated rows unless asked, and matches every word', () => {
    const rows = [lemon(), lemon({ id: 'x', name: 'Lime', deactivated: true })];
    expect(search(rows, '', false, r => [r.name]).length).toBe(1);
    expect(search(rows, '', true, r => [r.name]).length).toBe(2);
    expect(search(rows, 'fresh lem', false, r => [r.name, r.default_supplier]).map(r => r.id)).toEqual(['i-lemon']);
  });
});

describe('resultSentence', () => {
  it('says what a merge moved', () => {
    expect(resultSentence({ result: 'merged', item: 'Lemons', into: 'Lemon', moved: { invoice_lines: 12, variants: 1, recipe_versions: 0 } }))
      .toBe('Merged Lemons into Lemon — moved 12 invoice lines, 1 supplier variant.');
  });
});
