// The Catalogue page's logic, kept apart from the page so it can be tested:
// the shapes get_stock (view 'catalogue') returns, searching, which records a
// merge may land on, and an item edit turned into manage_stock_item's
// `update` params (only what changed — the tool merges the rest).

export interface CatalogueVariant {
  variant_id: string;
  supplier_id: string | null;
  supplier: string | null;
  stock_code: string | null;
  unit_id: string | null;
  unit: string | null;
  unit_cost: number | null;
  default: boolean;
}

export interface CatalogueItem {
  id: string;
  name: string;
  group_id: string | null;
  group: string | null;
  /** 0 weight, 1 volume, 2 count (Loaded's item unitType). */
  unit_type?: number | null;
  counting_unit_id: string | null;
  counting_unit: string | null;
  counting_unit_ratio: number | null;
  ordering_unit_id: string | null;
  ordering_unit: string | null;
  ordering_unit_ratio: number | null;
  default_supplier_id: string | null;
  default_supplier: string | null;
  /** 1 GST, 0 exempt (Loaded's sales tax sort order). */
  sales_tax?: number | null;
  system_item?: boolean;
  /** Shared with other Loaded companies: retiring it removes their copies,
   *  so it is never retired or merged away here (merged INTO is fine). */
  shared?: boolean;
  deactivated: boolean;
  deactivated_at?: string | null;
  variants: CatalogueVariant[];
}

export interface CatalogueUnit {
  id: string;
  name: string;
  /** 'Weight' | 'Volume' | 'Count' */
  type: string;
  ratio: number;
  items: number;
  deactivated: boolean;
  deactivated_at?: string | null;
  /** Live in Loaded, but not a measurement — Norm never books invoices in it. */
  hidden_by_norm?: boolean;
  /** One of Loaded's standard units: Loaded never lets it change. */
  standard?: boolean;
}

export interface CatalogueSupplier {
  id: string;
  name: string;
  contact: string | null;
  email: string | null;
  phone: string | null;
  customer_number: string | null;
  last_order_at: string | null;
  items: number;
  deactivated: boolean;
  deactivated_at?: string | null;
}

export interface CatalogueGroup {
  id: string;
  name: string;
  category: string | null;
}

export interface Catalogue {
  venue: string;
  include_deleted: boolean;
  items: CatalogueItem[];
  units: CatalogueUnit[];
  suppliers: CatalogueSupplier[];
  groups: CatalogueGroup[];
  counts: Record<string, number>;
  warning?: string;
}

/** The tool's preview card, as /api/stock-catalogue/preview returns it. */
export interface PreviewCard {
  title?: string;
  target?: string;
  venue?: string;
  changes?: { field: string; before?: unknown; after?: unknown }[];
  warnings?: string[];
  fingerprint?: string | null;
}

export type Tool = 'manage_stock_item' | 'manage_supplier';

/** One change the page asks a tool for. */
export interface Change {
  tool: Tool;
  params: Record<string, unknown>;
  /** What the confirm button says ("Merge", "Retire" …). */
  verb: string;
  danger?: boolean;
}

const ITEM_TYPES: Record<number, string> = { 0: 'Weight', 1: 'Volume', 2: 'Count' };

/** The unit type ('Weight' …) an item counts in. */
export function itemUnitType(item: Pick<CatalogueItem, 'unit_type'>): string | null {
  return item.unit_type == null ? null : ITEM_TYPES[item.unit_type] ?? null;
}

/** What a unit's size is measured in. */
export function baseUnit(type: string | null | undefined): string {
  if (type === 'Weight') return 'kg';
  if (type === 'Volume') return 'L';
  return 'each';
}

const fold = (s: unknown) => String(s ?? '').toLowerCase();

/** Rows whose text matches every word of the query, deactivated ones only
 *  when asked for. */
export function search<T extends { deactivated: boolean }>(
  rows: T[],
  query: string,
  showDeactivated: boolean,
  text: (row: T) => (string | null | undefined)[],
): T[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  return rows.filter(r => {
    if (r.deactivated && !showDeactivated) return false;
    if (!words.length) return true;
    const hay = text(r).map(fold).join(' ');
    return words.every(w => hay.includes(w));
  });
}

/** Items another may be merged into: live, a different item, the same unit
 *  type (the tool refuses anything else). */
export function itemMergeTargets(items: CatalogueItem[], from: CatalogueItem): CatalogueItem[] {
  return items.filter(i => !i.deactivated && i.id !== from.id && !i.system_item && i.unit_type === from.unit_type);
}

/** Units another may be merged into: live, the same type and the same size. */
export function unitMergeTargets(units: CatalogueUnit[], from: CatalogueUnit): CatalogueUnit[] {
  return units.filter(
    u => !u.deactivated && u.id !== from.id && u.type === from.type
      && Math.abs(u.ratio - from.ratio) <= 1e-9 * Math.max(1, Math.abs(from.ratio)),
  );
}

export function supplierMergeTargets(suppliers: CatalogueSupplier[], from: CatalogueSupplier): CatalogueSupplier[] {
  return suppliers.filter(s => !s.deactivated && s.id !== from.id);
}

// ------------------------------------------------------------ item edit ----

export interface VariantDraft {
  /** Absent on a variant being added. */
  variant_id?: string;
  supplier_id: string;
  stock_code: string;
  unit_id: string;
  unit_cost: string;
  default: boolean;
  removed?: boolean;
}

export interface ItemDraft {
  name: string;
  group_id: string;
  counting_unit_id: string;
  ordering_unit_id: string;
  default_supplier_id: string;
  sales_tax: number;
  variants: VariantDraft[];
}

export function draftOf(item: CatalogueItem): ItemDraft {
  return {
    name: item.name ?? '',
    group_id: item.group_id ?? '',
    counting_unit_id: item.counting_unit_id ?? '',
    ordering_unit_id: item.ordering_unit_id ?? '',
    default_supplier_id: item.default_supplier_id ?? '',
    sales_tax: item.sales_tax ?? 1,
    variants: item.variants.map(v => ({
      variant_id: v.variant_id,
      supplier_id: v.supplier_id ?? '',
      stock_code: v.stock_code ?? '',
      unit_id: v.unit_id ?? '',
      unit_cost: v.unit_cost == null ? '' : String(v.unit_cost),
      default: v.default,
    })),
  };
}

const money = (s: string): number | null => {
  const n = Number(String(s).replace(/[$,\s]/g, ''));
  return String(s).trim() === '' || !isFinite(n) ? null : n;
};

/** What's wrong with a draft, in words, or null. */
export function draftProblem(draft: ItemDraft): string | null {
  if (!draft.name.trim()) return 'The item needs a name.';
  if (!draft.counting_unit_id || !draft.ordering_unit_id) return 'Choose a counting and an ordering unit.';
  for (const v of draft.variants) {
    if (v.removed) continue;
    if (!v.supplier_id || !v.unit_id) return 'Each supplier variant needs a supplier and a unit.';
    if (v.unit_cost.trim() !== '' && money(v.unit_cost) === null) return `"${v.unit_cost}" isn't a cost.`;
  }
  const defaults: Record<string, number> = {};
  const has: Record<string, number> = {};
  for (const v of draft.variants) {
    if (v.removed) continue;
    has[v.supplier_id] = (has[v.supplier_id] ?? 0) + 1;
    if (v.default) defaults[v.supplier_id] = (defaults[v.supplier_id] ?? 0) + 1;
  }
  for (const s of Object.keys(has)) {
    if ((defaults[s] ?? 0) !== 1) return 'Each supplier needs exactly one default variant.';
  }
  return null;
}

/** manage_stock_item `update` params for what changed, or null when nothing
 *  did. Units carry their ratio (the tool requires the pair). */
export function itemUpdateParams(item: CatalogueItem, draft: ItemDraft, units: CatalogueUnit[]): Record<string, unknown> | null {
  const ratio = (id: string) => units.find(u => u.id === id)?.ratio ?? null;
  const changes: Record<string, unknown> = {};
  if (draft.name.trim() !== item.name) changes.name = draft.name.trim();
  if (draft.group_id && draft.group_id !== item.group_id) changes.groupId = draft.group_id;
  if (draft.counting_unit_id !== item.counting_unit_id) {
    changes.countingUnitId = draft.counting_unit_id;
    changes.countingUnitRatio = ratio(draft.counting_unit_id);
  }
  if (draft.ordering_unit_id !== item.ordering_unit_id) {
    changes.orderingUnitId = draft.ordering_unit_id;
    changes.orderingUnitRatio = ratio(draft.ordering_unit_id);
  }
  if ((draft.default_supplier_id || null) !== (item.default_supplier_id || null)) {
    changes.defaultSupplierId = draft.default_supplier_id || null;
  }
  if (draft.sales_tax !== (item.sales_tax ?? 1)) changes.globalSalesTaxSortOrder = draft.sales_tax;

  const variant_changes: Record<string, unknown>[] = [];
  const add_suppliers: Record<string, unknown>[] = [];
  const remove_variants: string[] = [];
  for (const v of draft.variants) {
    const was = v.variant_id ? item.variants.find(o => o.variant_id === v.variant_id) : undefined;
    if (!was) {
      if (!v.removed) {
        add_suppliers.push({
          supplierId: v.supplier_id,
          stockCode: v.stock_code.trim() || null,
          unitId: v.unit_id,
          unitCost: money(v.unit_cost) ?? 0,
          defaultForSupplier: v.default,
        });
      }
      continue;
    }
    if (v.removed) {
      remove_variants.push(was.variant_id);
      continue;
    }
    const edit: Record<string, unknown> = {};
    if (v.supplier_id !== (was.supplier_id ?? '')) edit.supplierId = v.supplier_id;
    if (v.stock_code.trim() !== (was.stock_code ?? '')) edit.stockCode = v.stock_code.trim() || null;
    if (v.unit_id !== (was.unit_id ?? '')) edit.unitId = v.unit_id;
    const cost = money(v.unit_cost);
    if (cost !== was.unit_cost) edit.unitCost = cost;
    if (v.default !== was.default) edit.defaultForSupplier = v.default;
    if (Object.keys(edit).length) variant_changes.push({ variant_id: was.variant_id, ...edit });
  }

  const params: Record<string, unknown> = { op: 'update', item_id: item.id };
  if (Object.keys(changes).length) params.changes = changes;
  if (variant_changes.length) params.variant_changes = variant_changes;
  if (add_suppliers.length) params.add_suppliers = add_suppliers;
  if (remove_variants.length) params.remove_variants = remove_variants;
  return Object.keys(params).length > 2 ? params : null;
}

// ------------------------------------------------------------- results ----

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** What a merge has moved, as a list ("3 invoice lines, 1 open order"). */
export function movedText(moved: Record<string, number> | null | undefined): string {
  moved = moved ?? {};
  const bits: string[] = [];
  if (moved.invoice_lines) bits.push(plural(moved.invoice_lines, 'invoice line'));
  if (moved.variants) bits.push(plural(moved.variants, 'supplier variant'));
  if (moved.items) bits.push(plural(moved.items, 'item'));
  if (moved.orders) bits.push(plural(moved.orders, 'open order'));
  if (moved.recipe_versions) bits.push(plural(moved.recipe_versions, 'recipe version'));
  if (moved.pos_links) bits.push(plural(moved.pos_links, 'POS link'));
  if (moved.invoices && !moved.invoice_lines) bits.push(plural(moved.invoices, 'invoice'));
  return bits.join(', ');
}

/** One plain sentence for what a tool run did. */
export function resultSentence(data: Record<string, unknown> | null | undefined): string {
  if (!data) return 'Done.';
  const r = String(data.result ?? '');
  const moved = movedText(data.moved as Record<string, number> | undefined);
  // manage_stock_item's update names the item `name`; the other ops `item`.
  const what = (data.item ?? data.unit ?? data.supplier ?? data.name ?? '') as string;
  const into = (data.into ?? '') as string;
  if (r === 'merged') {
    return `Merged ${what} into ${into}${moved ? ` — moved ${moved}` : ''}.`;
  }
  if (r === 'retired' || r === 'deleted' || r === 'restored' || r === 'updated') {
    const verb = r === 'deleted' ? 'Deleted' : r === 'restored' ? 'Restored' : r === 'retired' ? 'Retired' : 'Saved';
    return `${verb} ${what || 'the change'}.`;
  }
  if (r.startsWith('no differences')) return 'Nothing had changed, so nothing was saved.';
  return r ? r.charAt(0).toUpperCase() + r.slice(1) + '.' : 'Done.';
}
