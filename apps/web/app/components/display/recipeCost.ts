/**
 * Recipe cost engine — pure, testable math shared by the recipe and menu editors.
 *
 * Loaded stores no per-recipe cost, so we compute it from stock-item purchase
 * prices and unit ratios. Every unit carries a `ratio` to the base unit of its
 * type (Weight: Kilo=1, Gram=0.001; Volume: Litre=1, ml=0.001; …), so a line's
 * quantity in base units is `quantity * unitRatio`. A stock item's `currentPrice`
 * is already denominated in that base unit (verified live: OIL CANOLA counted in
 * "20 L" is $3.02/L, not $3.02/20 L; a 700 mL vodka is $46.53/L) — so the counting
 * unit is NOT applied to the price.
 *
 *   item line cost   = quantity * unitRatio * currentPrice
 *   subrecipe line   = quantity * unitRatio * (subRecipeCost / subYieldBase)
 *
 * A line is only costed when its unit and the referenced item's counting unit are
 * the same unit *type* (you can't price grams against a "each"-counted item), and
 * when a price exists. Anything uncomputable leaves `complete = false` so callers
 * can flag the total as partial rather than quietly under-reporting.
 */

export interface CostLine {
  itemId?: string | null;
  recipeId?: string | null;
  quantity?: number | null;
  unitRatio?: number | null;
  unitId?: string | null;
  deletedAt?: unknown;
}

export interface CostRecipe {
  yieldQuantity: number;
  yieldUnitRatio: number;
  lines: CostLine[];
}

export interface CostItem {
  currentPrice: number; // per the item's base/stock unit
  countingUnitId?: string | null; // used only to check unit-type compatibility
}

export interface CostTables {
  recipes: Map<string, CostRecipe>;
  items: Map<string, CostItem>;
  unitType: Map<string, string>; // unitId -> stockUnitType (e.g. "Weight")
}

export interface CostResult {
  cost: number;
  complete: boolean;
}

const num = (v: unknown): number => (typeof v === 'number' && isFinite(v) ? v : 0);

export function lineBaseQty(l: CostLine): number {
  return num(l.quantity) * num(l.unitRatio);
}

/** Cost of one line. `seen` guards sub-recipe recursion against cycles. */
export function lineCost(l: CostLine, t: CostTables, seen: Set<string> = new Set()): CostResult {
  const baseQty = lineBaseQty(l);

  if (l.itemId) {
    const item = t.items.get(l.itemId);
    if (!item || !num(item.currentPrice)) return { cost: 0, complete: false };
    // Only cost when the line's unit and the item's counting unit share a type,
    // so the two ratios are on the same base scale.
    const lineType = l.unitId ? t.unitType.get(l.unitId) : undefined;
    const itemType = item.countingUnitId ? t.unitType.get(item.countingUnitId) : undefined;
    if (lineType && itemType && lineType !== itemType) return { cost: 0, complete: false };
    // currentPrice is already per base unit — the counting unit does not scale it.
    return { cost: baseQty * num(item.currentPrice), complete: true };
  }

  if (l.recipeId) {
    if (seen.has(l.recipeId)) return { cost: 0, complete: false };
    const sub = t.recipes.get(l.recipeId);
    if (!sub) return { cost: 0, complete: false };
    const subYieldBase = num(sub.yieldQuantity) * num(sub.yieldUnitRatio);
    if (!subYieldBase) return { cost: 0, complete: false };
    const subCost = recipeCost(l.recipeId, t, seen);
    return { cost: baseQty * (subCost.cost / subYieldBase), complete: subCost.complete };
  }

  return { cost: 0, complete: false };
}

/** Total cost of a recipe already in the index. */
export function recipeCost(id: string, t: CostTables, seen: Set<string> = new Set()): CostResult {
  if (seen.has(id)) return { cost: 0, complete: false };
  const r = t.recipes.get(id);
  if (!r) return { cost: 0, complete: false };
  const next = new Set(seen);
  next.add(id);
  let cost = 0;
  let complete = true;
  for (const l of r.lines) {
    if (l.deletedAt) continue;
    const c = lineCost(l, t, next);
    cost += c.cost;
    if (!c.complete) complete = false;
  }
  return { cost, complete };
}

/** Total cost of a set of draft lines (the recipe being edited isn't in the index
 *  yet). `selfId` seeds the cycle guard so a line referencing this recipe can't
 *  recurse into itself. */
export function draftCost(lines: CostLine[], t: CostTables, selfId?: string): CostResult {
  const seen = new Set<string>();
  if (selfId) seen.add(selfId);
  let cost = 0;
  let complete = true;
  for (const l of lines) {
    if (l.deletedAt) continue;
    const c = lineCost(l, t, seen);
    cost += c.cost;
    if (!c.complete) complete = false;
  }
  return { cost, complete };
}
