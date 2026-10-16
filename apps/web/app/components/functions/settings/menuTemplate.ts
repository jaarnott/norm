/**
 * Pre-order menu template editing: sections (a `header` row and the dishes
 * under it), the default % per dish in a set menu, and reordering. The API
 * keeps the template as one ordered list of rows (templates.py), so the
 * editor does too; sections are read off the list, never stored.
 *
 * A set menu's default % say how a section's guests split across its dishes
 * (menus.distribute_set_menu turns them into quantities, largest remainder);
 * they should add up to 100 per section. An even split is base + remainder
 * — three dishes are 34 / 33 / 33, which is how the imported menus read.
 */
import type { TemplateItem } from './types';

/** A row being edited: the API item plus a stable key for React. */
export interface DraftItem extends TemplateItem {
  uid: string;
}

export interface Section {
  /** Index of the header row; null for dishes above the first heading. */
  headerIndex: number | null;
  /** Indexes of the section's dishes, in order. */
  itemIndices: number[];
}

let seq = 0;
export function newUid(): string {
  seq += 1;
  return `row-${seq}`;
}

export function toDraft(items: TemplateItem[]): DraftItem[] {
  return items.map((it) => ({ ...it, dietaries: [...(it.dietaries ?? [])], uid: newUid() }));
}

export function blankDish(): DraftItem {
  return {
    uid: newUid(),
    kind: 'item',
    name: '',
    description: null,
    unit_price_cents: null,
    default_percent: 0,
    dietaries: [],
    loaded_recipe_id: null,
    loaded_stock_item_id: null,
  };
}

export function blankHeading(): DraftItem {
  return { ...blankDish(), kind: 'header', default_percent: null };
}

/** The sections, in order. Dishes before the first heading form a section
 *  with `headerIndex: null` (only listed when there are any). */
export function sectionsOf(items: Pick<TemplateItem, 'kind'>[]): Section[] {
  const out: Section[] = [];
  let current: Section = { headerIndex: null, itemIndices: [] };
  items.forEach((item, i) => {
    if (item.kind === 'header') {
      if (current.headerIndex !== null || current.itemIndices.length) out.push(current);
      current = { headerIndex: i, itemIndices: [] };
    } else {
      current.itemIndices.push(i);
    }
  });
  if (current.headerIndex !== null || current.itemIndices.length) out.push(current);
  return out;
}

/** The section's default % added up (blank counts as 0). */
export function sectionTotal(items: Pick<TemplateItem, 'default_percent'>[], section: Section): number {
  return section.itemIndices.reduce((sum, i) => sum + (Number(items[i]?.default_percent) || 0), 0);
}

/** `n` whole percentages that add up to 100, the remainder on the first ones. */
export function evenSplit(n: number): number[] {
  if (n <= 0) return [];
  const base = Math.floor(100 / n);
  const remainder = 100 - base * n;
  return Array.from({ length: n }, (_, i) => base + (i < remainder ? 1 : 0));
}

/** The items with this section's dishes split evenly. */
export function splitSectionEvenly<T extends TemplateItem>(items: T[], section: Section): T[] {
  const shares = evenSplit(section.itemIndices.length);
  const next = [...items];
  section.itemIndices.forEach((idx, i) => {
    next[idx] = { ...next[idx], default_percent: shares[i] };
  });
  return next;
}

/** Sections of a set menu whose dishes don't add up to 100 %. */
export function unbalancedSections(items: TemplateItem[]): { section: Section; total: number }[] {
  return sectionsOf(items)
    .filter((s) => s.itemIndices.length > 0)
    .map((section) => ({ section, total: sectionTotal(items, section) }))
    .filter(({ total }) => total !== 100);
}

/** Swap row `index` with its neighbour `delta` (−1 up, +1 down); a move past
 *  either end is ignored. */
export function moveRow<T>(items: T[], index: number, delta: -1 | 1): T[] {
  const j = index + delta;
  if (index < 0 || index >= items.length || j < 0 || j >= items.length) return items;
  const next = [...items];
  [next[index], next[j]] = [next[j], next[index]];
  return next;
}

/** Insert a dish at the end of a section (or the end of the list). */
export function addDishTo<T extends TemplateItem>(items: T[], section: Section | null, dish: T): T[] {
  if (!section) return [...items, dish];
  const last = section.itemIndices.length
    ? section.itemIndices[section.itemIndices.length - 1]
    : (section.headerIndex ?? items.length - 1);
  const next = [...items];
  next.splice(last + 1, 0, dish);
  return next;
}

/**
 * The rows as the API takes them (`items` on POST/PATCH): no editor keys, no
 * ids (the list replaces the template's dishes), blank rows dropped, a set
 * menu's dishes unpriced (the API drops those prices anyway).
 */
export function toPayload(items: DraftItem[], isSetMenu: boolean): Omit<TemplateItem, 'id'>[] {
  return items
    .filter((it) => it.name.trim())
    .map((it) => ({
      kind: it.kind,
      name: it.name.trim(),
      description: it.kind === 'item' ? (it.description ?? '').trim() || null : null,
      unit_price_cents: it.kind === 'item' && !isSetMenu ? it.unit_price_cents : null,
      default_percent: it.kind === 'item' ? (it.default_percent ?? null) : null,
      dietaries: it.kind === 'item' ? it.dietaries : [],
      loaded_recipe_id: it.kind === 'item' ? it.loaded_recipe_id : null,
      loaded_stock_item_id: it.kind === 'item' ? it.loaded_stock_item_id : null,
    }));
}
