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
 * A line whose unit *type* differs from the item's counting unit is still costed
 * the way Loaded does: Weight<->Volume is priced assuming density ~1, and a Count
 * ("each") side — which has no measure to convert — contributes $0. Only a missing
 * price leaves `complete = false`, so callers can flag the total as partial rather
 * than quietly under-reporting.
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

/** A raw Loaded recipe version (currentVersion / a versions[] element), as far as
 *  costing needs it. */
export interface RawCostVersion {
  yieldQuantity?: unknown;
  yieldUnitRatio?: unknown;
  lines?: Array<{
    itemId?: string | null;
    recipeId?: string | null;
    quantity?: unknown;
    unitRatio?: unknown;
    unitId?: string | null;
    deletedAt?: unknown;
  }>;
}

/** Build a CostRecipe (for the sub-recipe index) from a raw Loaded recipe version.
 *
 *  The engine's convention is `quantity * unitRatio = base` — i.e. `quantity` is a
 *  DISPLAY quantity, exactly what the editor's `toDraft` produces. But Loaded gives
 *  a version's line quantities AND its yield ALREADY in base units, so they must be
 *  divided by the unit ratio here to match. Feeding the raw base quantities in
 *  directly (the old behaviour) double-applied the ratio: a sub-recipe whose yield
 *  unit isn't the base — e.g. a Gram-yield component, ratio 0.001 — was costed
 *  ~1000x too high (an 80 g line read $1872 instead of $2.14). */
export function costRecipeFromVersion(cv: RawCostVersion | null | undefined): CostRecipe {
  const yr = num(cv?.yieldUnitRatio) || 1;
  return {
    yieldQuantity: num(cv?.yieldQuantity) / yr,
    yieldUnitRatio: yr,
    lines: (cv?.lines || []).map((l) => {
      const r = num(l.unitRatio) || 1;
      return {
        itemId: l.itemId ?? null,
        recipeId: l.recipeId ?? null,
        quantity: num(l.quantity) / r,
        unitRatio: r,
        unitId: l.unitId ?? null,
        deletedAt: l.deletedAt,
      };
    }),
  };
}

/** Cost of one line. `seen` guards sub-recipe recursion against cycles. */
export function lineCost(l: CostLine, t: CostTables, seen: Set<string> = new Set()): CostResult {
  const baseQty = lineBaseQty(l);

  if (l.itemId) {
    const item = t.items.get(l.itemId);
    if (!item || !num(item.currentPrice)) return { cost: 0, complete: false };
    // Loaded still costs a line whose unit *type* differs from the item's counting
    // unit (e.g. buttermilk counted per "600 mL" but used by the gram). Weight and
    // Volume are both continuous measures on a shared numeric base scale, so it
    // prices them assuming density ~1 (1 kg ~ 1 L) — we fall through to the normal
    // baseQty * price for that. But a Count ("Each") on either side has no measure
    // to convert (you can't turn grams into "each" without a pack size), so it
    // contributes nothing — matching Loaded treating e.g. tap water counted "each"
    // as ~free — while still counting as costed so it doesn't blank the recipe.
    const lineType = l.unitId ? t.unitType.get(l.unitId) : undefined;
    const itemType = item.countingUnitId ? t.unitType.get(item.countingUnitId) : undefined;
    if (lineType && itemType && lineType !== itemType) {
      if (lineType === 'Count' || itemType === 'Count') return { cost: 0, complete: true };
    }
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
