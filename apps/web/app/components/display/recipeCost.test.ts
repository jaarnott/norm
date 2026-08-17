import { describe, it, expect } from 'vitest';
import { lineCost, recipeCost, draftCost, costRecipeFromVersion, type CostTables } from './recipeCost';

// Unit types: Kilo/Gram are Weight; Litre/ml are Volume; each is Count.
const UNIT_TYPE = new Map<string, string>([
  ['kilo', 'Weight'],
  ['gram', 'Weight'],
  ['litre', 'Volume'],
  ['ml', 'Volume'],
  ['each', 'Count'],
]);

// currentPrice is per the base unit (Kilo / Litre / each).
function tables(over: Partial<CostTables> = {}): CostTables {
  return {
    unitType: UNIT_TYPE,
    items: new Map([
      ['pistachio', { currentPrice: 74.14, countingUnitId: 'kilo' }], // $74.14/kg
      ['salt', { currentPrice: 1.2, countingUnitId: 'kilo' }], // $1.20/kg
      ['nopricenitem', { currentPrice: 0, countingUnitId: 'kilo' }],
    ]),
    recipes: new Map(),
    ...over,
  };
}

describe('item line cost with unit conversion', () => {
  it('costs 0.5 g of pistachio (@ $74.14/kg) as ~$0.037', () => {
    const c = lineCost({ itemId: 'pistachio', quantity: 0.5, unitRatio: 0.001, unitId: 'gram' }, tables());
    expect(c.complete).toBe(true);
    expect(c.cost).toBeCloseTo(0.03707, 4);
  });

  it('costs 0.01 kg of salt (@ $1.2/kg) as $0.012', () => {
    const c = lineCost({ itemId: 'salt', quantity: 0.01, unitRatio: 1, unitId: 'kilo' }, tables());
    expect(c.cost).toBeCloseTo(0.012, 6);
  });
});

describe('price is per base unit (counting unit does not scale it)', () => {
  it('costs 9 L of oil @ $3.0235/L as ~$27.21 (item counted in 20 L drums)', () => {
    const t = tables();
    t.items.set('oil', { currentPrice: 3.0235, countingUnitId: 'litre' });
    const c = lineCost({ itemId: 'oil', quantity: 9, unitRatio: 1, unitId: 'litre' }, t);
    expect(c.cost).toBeCloseTo(27.21, 2);
  });

  it('costs a 45 ml vodka pour @ $46.53/L as ~$2.09 (item counted in 700 ml bottles)', () => {
    const t = tables();
    t.items.set('vodka', { currentPrice: 46.529, countingUnitId: 'litre' });
    const c = lineCost({ itemId: 'vodka', quantity: 45, unitRatio: 0.001, unitId: 'ml' }, t);
    expect(c.cost).toBeCloseTo(2.094, 2);
  });
});

describe('incomplete lines', () => {
  it('is incomplete when the item has no price', () => {
    const c = lineCost({ itemId: 'nopricenitem', quantity: 1, unitRatio: 1, unitId: 'kilo' }, tables());
    expect(c).toEqual({ cost: 0, complete: false });
  });

  it('is incomplete when the item is unknown', () => {
    const c = lineCost({ itemId: 'ghost', quantity: 1, unitRatio: 1, unitId: 'kilo' }, tables());
    expect(c.complete).toBe(false);
  });

  it('contributes $0 (but stays complete) for a Count mismatch, like Loaded', () => {
    // Tap water counted "each" (Count) but used by mL (Volume) can't be converted
    // without a pack size — Loaded treats it as ~free, so it must not blank the recipe.
    const t = tables();
    t.items.set('water', { currentPrice: 5, countingUnitId: 'each' });
    const c = lineCost({ itemId: 'water', quantity: 150, unitRatio: 0.001, unitId: 'ml' }, t);
    expect(c).toEqual({ cost: 0, complete: true });
  });

  it('prices a Weight<->Volume mismatch assuming density ~1 (like Loaded)', () => {
    // Buttermilk counted per litre (Volume) but used by the gram (Weight): 500 g
    // is costed as 0.5 L * $/L.
    const t = tables();
    t.items.set('buttermilk', { currentPrice: 4, countingUnitId: 'litre' }); // $4/L
    const c = lineCost({ itemId: 'buttermilk', quantity: 500, unitRatio: 0.001, unitId: 'gram' }, t);
    expect(c.complete).toBe(true);
    expect(c.cost).toBeCloseTo(2.0, 6); // 0.5 (kg~L) * $4
  });

  it('has no cost for a line with neither item nor recipe', () => {
    expect(lineCost({ quantity: 1, unitRatio: 1 }, tables()).complete).toBe(false);
  });
});

describe('recipe total', () => {
  it('sums item lines and skips deleted lines', () => {
    const t = tables();
    t.recipes.set('dukkah', {
      yieldQuantity: 1,
      yieldUnitRatio: 1,
      lines: [
        { itemId: 'pistachio', quantity: 0.5, unitRatio: 0.001, unitId: 'gram' }, // 0.03707
        { itemId: 'salt', quantity: 0.01, unitRatio: 1, unitId: 'kilo' }, // 0.012
        { itemId: 'salt', quantity: 5, unitRatio: 1, unitId: 'kilo', deletedAt: '2021-01-01' }, // skipped
      ],
    });
    const c = recipeCost('dukkah', t);
    expect(c.complete).toBe(true);
    expect(c.cost).toBeCloseTo(0.04907, 4);
  });

  it('marks the total incomplete if any line is uncomputable', () => {
    const t = tables();
    t.recipes.set('r', {
      yieldQuantity: 1,
      yieldUnitRatio: 1,
      lines: [
        { itemId: 'salt', quantity: 1, unitRatio: 1, unitId: 'kilo' },
        { itemId: 'ghost', quantity: 1, unitRatio: 1, unitId: 'kilo' },
      ],
    });
    const c = recipeCost('r', t);
    expect(c.cost).toBeCloseTo(1.2, 6); // still sums what it can
    expect(c.complete).toBe(false);
  });
});

describe('sub-recipe costing', () => {
  it('costs a sub-recipe line by its cost-per-yield-base', () => {
    const t = tables();
    // stock @ $1.2/kg, sub yields 2 kg using 10 kg salt => $12 total, $6/kg
    t.recipes.set('base', {
      yieldQuantity: 2,
      yieldUnitRatio: 1, // 2 kg base
      lines: [{ itemId: 'salt', quantity: 10, unitRatio: 1, unitId: 'kilo' }], // $12
    });
    // parent uses 0.5 kg of base => 0.5 * ($12 / 2) = $3
    const c = lineCost({ recipeId: 'base', quantity: 0.5, unitRatio: 1, unitId: 'kilo' }, t);
    expect(c.complete).toBe(true);
    expect(c.cost).toBeCloseTo(3, 6);
  });

  it('guards against a recipe referencing itself (cycle)', () => {
    const t = tables();
    t.recipes.set('loop', {
      yieldQuantity: 1,
      yieldUnitRatio: 1,
      lines: [{ recipeId: 'loop', quantity: 1, unitRatio: 1, unitId: 'kilo' }],
    });
    const c = recipeCost('loop', t);
    expect(c.complete).toBe(false);
    expect(Number.isFinite(c.cost)).toBe(true);
  });
});

describe('draftCost', () => {
  it('sums draft lines and blocks self-reference via selfId', () => {
    const t = tables();
    t.recipes.set('self', { yieldQuantity: 1, yieldUnitRatio: 1, lines: [] });
    const lines = [
      { itemId: 'salt', quantity: 0.01, unitRatio: 1, unitId: 'kilo' }, // 0.012
      { recipeId: 'self', quantity: 1, unitRatio: 1, unitId: 'kilo' }, // blocked -> incomplete
    ];
    const c = draftCost(lines, t, 'self');
    expect(c.cost).toBeCloseTo(0.012, 6);
    expect(c.complete).toBe(false);
  });
});

describe('costRecipeFromVersion — the raw Loaded quantities are in BASE units', () => {
  it('converts base line quantities + yield to the engine display convention', () => {
    const cv = costRecipeFromVersion({
      yieldQuantity: 0.833, // base (kg); yield unit is Gram (ratio 0.001) => 833 g
      yieldUnitRatio: 0.001,
      lines: [{ itemId: 'trevally', quantity: 1.0, unitRatio: 1, unitId: 'kilo' }], // 1 kg base
    });
    expect(cv.yieldQuantity).toBeCloseTo(833, 6); // 0.833 / 0.001
    expect(cv.yieldUnitRatio).toBe(0.001);
    // yieldQuantity * yieldUnitRatio must recover the base yield (0.833 kg)
    expect(cv.yieldQuantity * cv.yieldUnitRatio).toBeCloseTo(0.833, 6);
    expect(cv.lines[0].quantity).toBe(1.0); // 1.0 / 1
  });

  it('costs an 80 g line of a Gram-yield component correctly (the $1872 bug)', () => {
    // A component that yields 833 g and uses 1 kg of $25/kg trevally costs $25 for
    // 833 g. An 80 g line of it must be ~80/833 * $25 = $2.40 — NOT ~$1872, which is
    // what feeding the base-unit yield straight in (double-applying the 0.001 ratio)
    // produced.
    const kingfish = costRecipeFromVersion({
      yieldQuantity: 0.833,
      yieldUnitRatio: 0.001,
      lines: [{ itemId: 'trevally', quantity: 1.0, unitRatio: 1, unitId: 'kilo' }],
    });
    const t = tables({ recipes: new Map([['kingfish', kingfish]]) });
    t.items.set('trevally', { currentPrice: 25, countingUnitId: 'kilo' }); // $25/kg

    const c = lineCost({ recipeId: 'kingfish', quantity: 80, unitRatio: 0.001, unitId: 'gram' }, t);
    expect(c.complete).toBe(true);
    expect(c.cost).toBeCloseTo(2.401, 2);
    expect(c.cost).toBeLessThan(10); // decisively not the ~1000x ($2400) regression
  });

  it('leaves a component whose yield unit IS the base (ratio 1) unchanged', () => {
    // GUINDILLA-style: yield unit Kilo (ratio 1) was always correct; the fix must
    // not disturb it.
    const guindilla = costRecipeFromVersion({
      yieldQuantity: 2, // base kg, yield unit Kilo
      yieldUnitRatio: 1,
      lines: [{ itemId: 'salt', quantity: 10, unitRatio: 1, unitId: 'kilo' }], // $12
    });
    const t = tables({ recipes: new Map([['guindilla', guindilla]]) });
    const c = lineCost({ recipeId: 'guindilla', quantity: 0.5, unitRatio: 1, unitId: 'kilo' }, t);
    expect(c.cost).toBeCloseTo(3, 6); // 0.5 * ($12 / 2 kg)
  });
});
