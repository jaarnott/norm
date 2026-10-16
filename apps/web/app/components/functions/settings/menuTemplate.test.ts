import { describe, expect, it } from 'vitest';
import {
  addDishTo,
  blankDish,
  evenSplit,
  moveRow,
  sectionTotal,
  sectionsOf,
  splitSectionEvenly,
  toDraft,
  toPayload,
  unbalancedSections,
} from './menuTemplate';
import type { TemplateItem } from './types';

const item = (name: string, pct: number | null = null, kind: 'header' | 'item' = 'item'): TemplateItem => ({
  kind,
  name,
  description: null,
  unit_price_cents: kind === 'item' ? 1200 : null,
  default_percent: pct,
  dietaries: [],
  loaded_recipe_id: null,
  loaded_stock_item_id: null,
});

// Bessie's Best, as imported: two sections, the first split 34 / 33 / 33.
const menu = [
  item('ENTREE', 0, 'header'),
  item('Salmon', 34),
  item('Tartare', 33),
  item('Parfait', 33),
  item('PRIME CUTS', 0, 'header'),
  item('Tomahawk', 50),
  item('Eye fillet', 40),
];

describe('menu template sections', () => {
  it('reads sections off the ordered rows', () => {
    expect(sectionsOf(menu)).toEqual([
      { headerIndex: 0, itemIndices: [1, 2, 3] },
      { headerIndex: 4, itemIndices: [5, 6] },
    ]);
  });

  it('keeps dishes above the first heading as their own section', () => {
    expect(sectionsOf([item('Bread'), item('MAINS', null, 'header'), item('Fish')])).toEqual([
      { headerIndex: null, itemIndices: [0] },
      { headerIndex: 1, itemIndices: [2] },
    ]);
    expect(sectionsOf([])).toEqual([]);
  });

  it('totals a section, counting blank as zero', () => {
    const [first, second] = sectionsOf(menu);
    expect(sectionTotal(menu, first)).toBe(100);
    expect(sectionTotal(menu, second)).toBe(90);
    expect(sectionTotal([item('A', null), item('B', 25)], { headerIndex: null, itemIndices: [0, 1] })).toBe(25);
  });

  it('flags the sections that do not add up to 100', () => {
    const off = unbalancedSections(menu);
    expect(off).toHaveLength(1);
    expect(off[0].section.headerIndex).toBe(4);
    expect(off[0].total).toBe(90);
  });
});

describe('even split', () => {
  it('splits into whole percentages that add up to 100, remainder first', () => {
    expect(evenSplit(3)).toEqual([34, 33, 33]);
    expect(evenSplit(2)).toEqual([50, 50]);
    expect(evenSplit(7).reduce((a, b) => a + b, 0)).toBe(100);
    expect(evenSplit(6)).toEqual([17, 17, 17, 17, 16, 16]);
    expect(evenSplit(0)).toEqual([]);
  });

  it('rewrites only the chosen section', () => {
    const second = sectionsOf(menu)[1];
    const next = splitSectionEvenly(menu, second);
    expect(next.map((i) => i.default_percent)).toEqual([0, 34, 33, 33, 0, 50, 50]);
    expect(menu[5].default_percent).toBe(50); // input untouched
  });
});

describe('editing rows', () => {
  it('moves a row up or down and ignores moves past the ends', () => {
    const rows = ['a', 'b', 'c'];
    expect(moveRow(rows, 1, -1)).toEqual(['b', 'a', 'c']);
    expect(moveRow(rows, 1, 1)).toEqual(['a', 'c', 'b']);
    expect(moveRow(rows, 0, -1)).toBe(rows);
    expect(moveRow(rows, 2, 1)).toBe(rows);
  });

  it('adds a dish at the end of its section', () => {
    const first = sectionsOf(menu)[0];
    const dish = { ...item('Soup', 0), name: 'Soup' };
    const next = addDishTo(menu, first, dish);
    expect(next.map((i) => i.name)).toEqual(['ENTREE', 'Salmon', 'Tartare', 'Parfait', 'Soup', 'PRIME CUTS', 'Tomahawk', 'Eye fillet']);
    // An empty section takes it straight after its heading.
    expect(addDishTo([item('A', 0, 'header')], { headerIndex: 0, itemIndices: [] }, dish).map((i) => i.name)).toEqual(['A', 'Soup']);
    expect(addDishTo(menu, null, dish).at(-1)?.name).toBe('Soup');
  });

  it('builds the API payload: no editor keys, blank rows dropped, set-menu dishes unpriced', () => {
    const draft = [...toDraft(menu), blankDish()];
    const setMenu = toPayload(draft, true);
    expect(setMenu).toHaveLength(7);
    expect(setMenu.every((i) => !('uid' in i) && !('id' in i))).toBe(true);
    expect(setMenu[1]).toMatchObject({ kind: 'item', name: 'Salmon', unit_price_cents: null, default_percent: 34 });
    expect(setMenu[0]).toMatchObject({ kind: 'header', default_percent: null, unit_price_cents: null });
    const aLaCarte = toPayload(draft, false);
    expect(aLaCarte[1].unit_price_cents).toBe(1200);
  });
});
