'use client';

/**
 * Recipe editor — LoadedHub recipes.
 *
 * Reads are DIRECT loadedhub (callComponentApi('recipe_editor', ...)); the SAVE
 * is the one write and routes through the Cook Brothers App via
 * POST /api/recipe-editor/save (sandbox-api maps it to norm__save_recipe inside
 * Claude). Loaded stores line quantities in BASE units; the editor works in
 * DISPLAY units (qty / unitRatio) and converts back only where Loaded's raw
 * shape needs it — the save payload carries display quantities, which is what
 * kitchen_loadedhub_update_recipe expects.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch, callComponentApi } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { colors } from '../../lib/theme';
import Combobox, { type ComboOption } from './Combobox';
import HtmlField from './HtmlField';
import { type CostTables } from './recipeCost';
import { setPageDocument } from '../../lib/pageDocument';
import { formatMoney } from '../../lib/format';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface Unit { id: string; name: string; ratio: number; type?: string }
interface Opt { id: string; name: string; kind: 'item' | 'recipe' }
// A row in the recipes table — summarised from the list payload's currentVersion.
interface RecipeRow { id: string; name: string; prep: boolean; ingredients: number; yieldText: string }
interface EditLine {
  key: string;
  kind: 'item' | 'recipe';
  ref_id: string | null;
  name: string;
  unit_id: string | null;
  unit_name: string | null;
  unit_ratio: number;
  quantity: number;
  // The referenced item/component's own stock unit (read-only, from Loaded) —
  // used for the Stock Unit / Stock Cost columns, mirroring Loaded's editor.
  stock_unit_name: string | null;
  stock_unit_ratio: number;
  // The stock item/component has been deleted in Loaded, but the line remains on
  // the recipe (Loaded keeps costing it). Shown as a normal line, flagged.
  item_deleted: boolean;
}
// A recipe version for the read-only viewer. Loaded returns a `versions` array
// plus a `currentVersion`; only the current one is editable, the rest are history.
interface RecipeVersion {
  id: string;
  label: string;
  current: boolean;
  yield_quantity: number;
  yield_unit_name: string | null;
  lines: EditLine[];
}
interface Draft {
  recipe_id: string; // '' for a not-yet-created recipe
  version_id: string;
  name: string;
  notes: string;
  is_counted_in_stocktake: boolean;
  yield_quantity: number;
  yield_unit_id: string | null;
  lines: EditLine[];
  versions: RecipeVersion[];
}
interface VenueOpt { id: string; name: string }

function uid(): string {
  try { return crypto.randomUUID(); } catch { return 'k' + Math.random().toString(36).slice(2); }
}
const num = (v: unknown) => (typeof v === 'number' ? v : parseFloat(String(v)) || 0);

// Map one raw Loaded version's lines into editable lines (DISPLAY units, quantity
// / unitRatio). A line whose `deletedAt` is set is NOT removed from the recipe —
// it means the referenced stock item/component was deleted in Loaded; Loaded keeps
// the line and keeps costing it, so we keep it too and just flag it (item_deleted).
function mapRawLines(cv: Record<string, unknown>): EditLine[] {
  const rawLines = (cv.lines as Array<Record<string, unknown>>) || [];
  return rawLines.map((l) => {
    const ratio = num(l.unitRatio) || 1;
    return {
      key: uid(),
      kind: l.itemId ? 'item' : 'recipe',
      ref_id: (l.itemId as string) || (l.recipeId as string) || null,
      name: (l.itemName as string) || (l.recipeName as string) || '',
      unit_id: (l.unitId as string) || null,
      unit_name: (l.unitName as string) || null,
      unit_ratio: ratio,
      quantity: num(l.quantity) / ratio,
      stock_unit_name: (l.stockUnitName as string) || null,
      stock_unit_ratio: num(l.stockUnitRatio) || 1,
      item_deleted: !!l.deletedAt,
    };
  });
}

// The recipe's versions, for the read-only viewer. Loaded returns a `versions`
// array (each with its own lines/yield) plus `currentVersion`.
function versionsFromRaw(r: Record<string, unknown>): RecipeVersion[] {
  const cur = (r.currentVersion as Record<string, unknown>) || {};
  const curId = (cur.id as string) || '';
  const raw = (r.versions as Array<Record<string, unknown>>) || (cur.id ? [cur] : []);
  return raw.map((v, i) => {
    const yr = num(v.yieldUnitRatio) || 1;
    const isCurrent = (v.id as string) === curId;
    const from = (v.validFrom as string) || '';
    const label = isCurrent ? 'Current' : from ? `From ${from.slice(0, 10)}` : `Version ${raw.length - i}`;
    return {
      id: (v.id as string) || uid(),
      label,
      current: isCurrent,
      yield_quantity: num(v.yieldQuantity) / yr,
      yield_unit_name: (v.yieldUnitName as string) || null,
      lines: mapRawLines(v),
    };
  });
}

// A Loaded recipe payload (from get_recipe) -> editable Draft (display units).
function toDraft(r: Record<string, unknown>): Draft | null {
  const cv = (r.currentVersion as Record<string, unknown>) || null;
  if (!cv) return null;
  const yr = num(cv.yieldUnitRatio) || 1;
  return {
    recipe_id: r.id as string,
    version_id: cv.id as string,
    name: (r.name as string) || '',
    notes: (cv.notes as string) || (r.notes as string) || '',
    is_counted_in_stocktake: !!r.isCountedInStocktake,
    yield_quantity: num(cv.yieldQuantity) / yr,
    yield_unit_id: (cv.yieldUnitId as string) || null,
    lines: mapRawLines(cv),
    versions: versionsFromRaw(r),
  };
}

// A blank draft for a brand-new recipe (recipe_id '' => the save creates it).
function blankDraft(seed?: Partial<Draft>): Draft {
  return {
    recipe_id: '',
    version_id: '',
    name: '',
    notes: '',
    is_counted_in_stocktake: false,
    yield_quantity: 1,
    yield_unit_id: null,
    lines: [],
    versions: [],
    ...seed,
  };
}

interface RawLine {
  itemId?: string | null;
  recipeId?: string | null;
  quantity?: unknown;
  unitRatio?: unknown;
  unitId?: string | null;
  deletedAt?: unknown;
}
interface RawRecipe {
  id: string;
  name?: string;
  deletedAt?: unknown;
  prepRecipe?: boolean;
  currentVersion?: {
    yieldQuantity?: unknown;
    yieldUnitRatio?: unknown;
    yieldUnitName?: string;
    lines?: RawLine[];
  } | null;
}

const money = (n: number): string => formatMoney(n);

// Summarise a list-payload recipe for the table (ingredient count uses the same
// deletedAt filter the editor's toDraft uses, so the count matches what you'd
// see on opening the recipe).
function summarizeRecipe(x: RawRecipe): RecipeRow {
  const cv = x.currentVersion || {};
  const yr = num(cv.yieldUnitRatio) || 1;
  const yq = num(cv.yieldQuantity) / yr;
  const lines = (cv.lines || []).filter((l) => !l.deletedAt);
  const yieldText = cv.yieldUnitName && yq ? `${+yq.toFixed(2)} ${cv.yieldUnitName}` : '—';
  // Loaded pads some names (" COMPONENT - …") — trim so display and the
  // alphabetical sort aren't thrown by leading whitespace.
  return { id: x.id, name: (x.name || '').trim(), prep: !!x.prepRecipe, ingredients: lines.length, yieldText };
}

// The recipe currently open in the editor, held at MODULE scope so it survives a
// remount. Sending a chat message flips the functional page from full-height to
// split view, which remounts this component and would otherwise reset the local
// draft back to the list — losing the recipe the user was on. Restoring from here
// keeps them on it (and the working-doc id/version, so the agent-edit poll keeps
// running). Cleared when the user returns to the list (draft → null).
let openSession: { venueId: string | null; draft: Draft; workingDocId: string | null; docVersion: number } | null = null;

// A recipe another page (e.g. Menu Engineering) asked us to open. Set before the
// Recipes page mounts; consumed once the editor has a venue. Module scope so it
// survives the navigation remount, like openSession.
let pendingOpenRecipeId: string | null = null;
export function requestOpenRecipe(recipeId: string) { pendingOpenRecipeId = recipeId; }

// A recipe working-document's data (built server-side by recipe_document.py) ->
// the editable Draft. Loaded lines already carry a stable id; we key rows on it
// so an agent edit picked up by the poll re-renders in place.
function fdLine(l: Record<string, unknown>): EditLine {
  return {
    key: (l.id as string) || uid(),
    kind: l.kind === 'recipe' ? 'recipe' : 'item',
    ref_id: (l.ref_id as string) ?? null,
    name: (l.name as string) || '',
    unit_id: (l.unit_id as string) ?? null,
    unit_name: (l.unit_name as string) ?? null,
    unit_ratio: num(l.unit_ratio) || 1,
    quantity: num(l.quantity),
    stock_unit_name: (l.stock_unit_name as string) ?? null,
    stock_unit_ratio: num(l.stock_unit_ratio) || 1,
    item_deleted: !!l.item_deleted,
  };
}
function fromDoc(d: Record<string, unknown>): Draft {
  const arr = (v: unknown) => (Array.isArray(v) ? (v as Array<Record<string, unknown>>) : []);
  return {
    recipe_id: (d.recipe_id as string) || '',
    version_id: (d.version_id as string) || '',
    name: (d.name as string) || '',
    notes: (d.notes as string) || '',
    is_counted_in_stocktake: !!d.is_counted_in_stocktake,
    yield_quantity: num(d.yield_quantity),
    yield_unit_id: (d.yield_unit_id as string) || null,
    lines: arr(d.lines).map(fdLine),
    versions: arr(d.versions).map((v) => ({
      id: (v.id as string) || uid(),
      label: (v.label as string) || 'Version',
      current: !!v.current,
      yield_quantity: num(v.yield_quantity),
      yield_unit_name: (v.yield_unit_name as string) ?? null,
      lines: arr(v.lines).map(fdLine),
    })),
  };
}

export default function RecipeEditor({ data, props }: DisplayBlockProps) {
  const embedded = !!props?.embedded;
  const initialRecipe =
    data && typeof data === 'object' && (data as { currentVersion?: unknown }).currentVersion
      ? (data as Record<string, unknown>)
      : null;

  const persistVenue = !!props?.persistVenue;
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOpt[]>([]);
  const [venueId, setVenueId] = useState<string | null>(
    (props?.activeVenueId as string) || rememberedVenue || null,
  );

  const [recipes, setRecipes] = useState<RecipeRow[]>([]);
  const [query, setQuery] = useState('');
  const [units, setUnits] = useState<Unit[]>([]);
  const [opts, setOpts] = useState<Opt[]>([]);
  const [costTables, setCostTables] = useState<CostTables | null>(null);
  // Per-ingredient cost from Loaded's own costs endpoint (ref_id -> cost per base
  // unit + the item/component's stock unit). This is what Loaded's editor uses, so
  // our Stock cost / Recipe cost columns match it exactly.
  const [costsById, setCostsById] = useState<Map<string, { cost: number; unitName: string | null; unitRatio: number }>>(new Map());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // The version being viewed. null = the current (editable) version; any other id
  // is a past version shown read-only. Falls back to current if it isn't in the
  // open recipe (e.g. after switching recipes), so no reset effect is needed.
  const [viewVersionId, setViewVersionId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(() =>
    embedded && initialRecipe ? toDraft(initialRecipe) : openSession?.draft ?? null,
  );
  // Working-document backing (web/page only): the open recipe is a shared draft
  // the agent can also edit. Restored from the session so the remount doesn't
  // drop the doc id/version the poll needs.
  const [workingDocId, setWorkingDocId] = useState<string | null>(() => (embedded ? null : openSession?.workingDocId ?? null));
  const [docVersion, setDocVersion] = useState<number>(() => (embedded ? 0 : openSession?.docVersion ?? 0));
  const lastVersionRef = useRef<number>(openSession?.docVersion ?? 0);
  const lastEditRef = useRef<number>(0);

  // Upload → extract a draft recipe from a document (web-only). The extracted
  // draft can be turned into a NEW Loaded recipe (fromExtracted → blank draft →
  // create), once the user maps each ingredient to a real stock item + unit.
  interface Extracted { name?: string; yield_quantity?: number | null; yield_unit?: string | null; ingredients?: Array<{ name?: string; quantity?: number | null; unit?: string | null }>; method?: string | null }
  const [extracted, setExtracted] = useState<Extracted | null>(null);
  const [extracting, setExtracting] = useState(false);

  const onDoc = async (file: File | null) => {
    if (!file) return;
    setExtracting(true);
    setError(null);
    setExtracted(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('extraction_target', 'recipe');
      if (venueId) fd.append('venue_id', venueId);
      const up = await apiFetch('/api/uploads', { method: 'POST', body: fd });
      if (!up.ok) throw new Error('Upload failed');
      const upj = await up.json();
      const ex = await apiFetch(`/api/uploads/${upj.id}/extract-recipe`, { method: 'POST', body: '{}' });
      if (!ex.ok) {
        const t = await ex.json().catch(() => ({}));
        throw new Error((t as { detail?: string }).detail || 'Extraction failed');
      }
      setExtracted(((await ex.json()) as { recipe?: Extracted }).recipe || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not extract a recipe');
    }
    setExtracting(false);
  };

  useEffect(() => {
    apiFetch('/api/venues')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d?.venues?.length) {
          setVenues(d.venues);
          if (!venueId) {
            const rem = rememberedVenue && d.venues.some((v: VenueOpt) => v.id === rememberedVenue) ? rememberedVenue : null;
            setVenueId(rem || d.venues[0].id);
          }
        }
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Reference data: recipes (picker + sub-recipe options), units, stock items.
  const loadRefs = useCallback(async (vid: string) => {
    setLoading(true);
    setError(null);
    try {
      const [rRes, uRes, iRes] = await Promise.all([
        callComponentApi('recipe_editor', 'list_recipes', {}, vid),
        callComponentApi('recipe_editor', 'list_units', {}, vid),
        callComponentApi('recipe_editor', 'list_stock_items', {}, vid),
      ]);
      const rl = ((rRes?.data as RawRecipe[]) || []).filter((x) => !x.deletedAt);
      if (!embedded) setRecipes(rl.map(summarizeRecipe));

      const rawUnits = ((uRes?.data as Array<{ id: string; name: string; ratio: number; stockUnitType?: string; datestampDeleted?: unknown }>) || [])
        .filter((u) => !u.datestampDeleted);
      setUnits(rawUnits.map((u) => ({ id: u.id, name: u.name, ratio: num(u.ratio), type: u.stockUnitType })));

      // Loaded marks a removed stock item with datestampRemoved (not
      // datestampDeleted). Removed items are kept OUT of the ingredient picker
      // (nobody should add an item that was removed years ago) but kept IN the
      // cost index — a recipe that still lists a removed item can be costed from
      // its last-known price.
      const allItems = (iRes?.data as Array<{ id: string; name: string; currentPrice?: unknown; countingUnitId?: string; datestampDeleted?: unknown; datestampRemoved?: unknown }>) || [];
      const activeItems = allItems.filter((i) => !i.datestampDeleted && !i.datestampRemoved);
      const items = activeItems.map((i) => ({ id: i.id, name: (i.name || '').trim(), kind: 'item' as const }));
      const subs = rl.map((x) => ({ id: x.id, name: (x.name || '').trim(), kind: 'recipe' as const }));
      setOpts([...subs, ...items]);

      // Cost tables here serve only the ingredient picker (an item's counting unit
      // + unit types for the unit dropdown). Line COSTS come straight from Loaded's
      // costs endpoint (see fetchCosts) so they match Loaded's editor exactly.
      const itemMap = new Map(allItems.map((i) => [i.id, { currentPrice: num(i.currentPrice), countingUnitId: i.countingUnitId }] as const));
      const unitTypeMap = new Map(rawUnits.filter((u) => u.stockUnitType).map((u) => [u.id, u.stockUnitType as string] as const));
      setCostTables({ recipes: new Map(), items: itemMap, unitType: unitTypeMap });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load recipes');
    }
    setLoading(false);
  }, [embedded]);

  useEffect(() => { if (venueId) loadRefs(venueId); }, [venueId, loadRefs]);

  // Pull Loaded's Live cost for every ingredient/component the recipe references
  // (items and sub-recipes), keyed by ref_id. This is the same endpoint Loaded's
  // own editor uses, so the Stock cost / Recipe cost columns match it exactly.
  const fetchCosts = useCallback(async (lines: EditLine[], vid: string) => {
    const itemIds = Array.from(new Set(lines.filter((l) => l.kind === 'item' && l.ref_id).map((l) => l.ref_id as string)));
    const recipeIds = Array.from(new Set(lines.filter((l) => l.kind === 'recipe' && l.ref_id).map((l) => l.ref_id as string)));
    if (!itemIds.length && !recipeIds.length) { setCostsById(new Map()); return; }
    const ts = new Date().toISOString();
    const parts: string[] = [];
    for (const id of itemIds) parts.push('itemIdTimeStrings=' + encodeURIComponent(`${id},${ts}`));
    for (const id of recipeIds) parts.push('recipeIdTimeStrings=' + encodeURIComponent(`${id},${ts}`));
    parts.push('priceType=Live');
    try {
      const res = await callComponentApi('recipe_editor', 'get_costs', { q: parts.join('&') }, vid);
      const data = res?.data as { itemCosts?: Record<string, Array<Record<string, unknown>>>; recipeCosts?: Record<string, Array<Record<string, unknown>>> } | undefined;
      const m = new Map<string, { cost: number; unitName: string | null; unitRatio: number }>();
      for (const src of [data?.itemCosts, data?.recipeCosts]) {
        for (const [id, arr] of Object.entries(src || {})) {
          const c = Array.isArray(arr) ? arr[0] : null;
          if (c) m.set(id, { cost: num(c.cost), unitName: (c.unitName as string) ?? null, unitRatio: num(c.unitRatio) || 1 });
        }
      }
      setCostsById(m);
    } catch { /* keep last-known costs */ }
  }, []);

  // Re-fetch only when the SET of referenced ingredients changes (not on every
  // qty/unit keystroke — those recompute locally from the cached per-base cost).
  const costRefKey = draft ? draft.lines.map((l) => `${l.kind}:${l.ref_id ?? ''}`).sort().join('|') : '';
  useEffect(() => {
    if (draft && venueId) fetchCosts(draft.lines, venueId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [costRefKey, venueId, fetchCosts]);

  // Mirror the open recipe into module scope so a functional-page remount (which
  // happens the moment you send a chat message) restores it instead of dropping
  // you back on the list. Page instance only — the embedded card owns its own.
  useEffect(() => {
    if (embedded) return;
    openSession = draft ? { venueId, draft, workingDocId, docVersion } : null;
    // Publish the open recipe so a chat message can tell the agent exactly what
    // the user is editing (which recipe, which venue, and the current lines so it
    // can resolve "the salt"). Cleared when back on the list.
    setPageDocument(
      draft
        ? {
            kind: 'recipe',
            recipe_id: draft.recipe_id || null,
            venue_id: venueId,
            working_document_id: workingDocId,
            name: draft.name,
            yield: { quantity: draft.yield_quantity, unit_id: draft.yield_unit_id },
            lines: draft.lines.map((l) => ({ id: l.key, kind: l.kind, ref_id: l.ref_id, name: l.name, quantity: l.quantity, unit: l.unit_name })),
          }
        : null,
    );
  }, [draft, venueId, embedded, workingDocId, docVersion]);

  // Clear the published document when the editor unmounts for good (navigating to
  // another page), so a stale recipe isn't attached to an unrelated page's chat.
  useEffect(() => () => { if (!embedded) setPageDocument(null); }, [embedded]);

  // Catch-up poll: while a recipe is open as a working document, re-read it so an
  // edit the agent made (it patches the same {recipe_id, venue} doc) appears here.
  // Skips a refresh within 3s of a local edit so it never clobbers active typing.
  useEffect(() => {
    if (!workingDocId) return;
    const id = setInterval(async () => {
      try {
        const res = await apiFetch(`/api/working-documents/${workingDocId}`);
        if (!res.ok) return;
        const doc = await res.json();
        if (!doc || typeof doc.version !== 'number' || doc.version <= lastVersionRef.current) return;
        if (Date.now() - lastEditRef.current < 3000) return;
        lastVersionRef.current = doc.version;
        setDocVersion(doc.version);
        setDraft(fromDoc(doc.data || {}));
      } catch { /* transient — retry next tick */ }
    }, 3000);
    return () => clearInterval(id);
  }, [workingDocId]);

  const unitById = useMemo(() => new Map(units.map((u) => [u.id, u])), [units]);
  const ingredientOptions: ComboOption[] = useMemo(
    () => opts.map((o) => ({ id: o.id, name: o.name, kind: o.kind, sublabel: o.kind === 'recipe' ? 'Sub-recipe' : 'Stock item' })),
    [opts],
  );
  // Recipes list: filter by the search box, sort alphabetically.
  const visibleRecipes = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q ? recipes.filter((r) => r.name.toLowerCase().includes(q)) : recipes;
    return [...filtered].sort((a, b) => a.name.localeCompare(b.name));
  }, [recipes, query]);

  // The unit type a line should be measured in (for scoping the unit picker):
  // an item's own counting-unit type, else the type of the unit already chosen.
  const unitTypeForLine = (l: EditLine): string | undefined => {
    if (l.kind === 'item' && l.ref_id) {
      const it = costTables?.items.get(l.ref_id);
      if (it?.countingUnitId) return costTables?.unitType.get(it.countingUnitId);
    }
    return l.unit_id ? costTables?.unitType.get(l.unit_id) : undefined;
  };
  // Recipe cost of one line = qty (in the line's unit) × Loaded's per-base cost.
  // qty × unitRatio is the base quantity; Loaded's `cost` is per base unit. A line
  // with no cost yet (endpoint not returned, or a brand-new ingredient) is
  // incomplete, so the total shows a "~".
  const lineCostOf = (l: EditLine): { cost: number; complete: boolean } | null => {
    if (!l.ref_id) return null;
    const c = costsById.get(l.ref_id);
    if (!c) return { cost: 0, complete: false };
    return { cost: l.quantity * l.unit_ratio * c.cost, complete: true };
  };
  // Loaded's "Stock cost" column: cost of ONE of the ingredient's stock units =
  // per-base cost × stock-unit ratio.
  const stockCostOf = (l: EditLine): number | null => {
    const c = l.ref_id ? costsById.get(l.ref_id) : undefined;
    if (!c) return null;
    return c.cost * (c.unitRatio || 1);
  };
  // Loaded's "Stock unit" column — the ingredient's own stock unit from the costs
  // endpoint (falls back to the recipe line's stored stock unit).
  const stockUnitOf = (l: EditLine): string => {
    const c = l.ref_id ? costsById.get(l.ref_id) : undefined;
    return c?.unitName || l.stock_unit_name || '';
  };
  const linesTotal = (lines: EditLine[]): { cost: number; complete: boolean } => {
    let cost = 0;
    let complete = true;
    for (const l of lines) {
      if (!l.ref_id) continue;
      const lc = lineCostOf(l);
      if (!lc || !lc.complete) { complete = false; continue; }
      cost += lc.cost;
    }
    return { cost, complete };
  };
  // Total recipe cost + cost per yield unit (recomputed as the draft/costs change).
  const totalCost = useMemo(() => (draft ? linesTotal(draft.lines) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [draft, costsById]);

  const clearDoc = () => { setWorkingDocId(null); setDocVersion(0); lastVersionRef.current = 0; };

  const changeVenue = (vid: string) => {
    setVenueId(vid);
    if (persistVenue) setActiveVenue(vid);
    setDraft(null);
    clearDoc();
  };

  // Open a recipe as a shared working document, so the agent can edit the same
  // draft and the poll above reflects it. Falls back to a plain read if the doc
  // route is unavailable.
  const openRecipe = async (id: string) => {
    setError(null);
    setSavedNote(null);
    if (!venueId) { setError('Select a venue first.'); return; }
    try {
      const res = await apiFetch('/api/recipe-editor/open', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, recipe_id: id }),
      });
      if (!res.ok) {
        const t = await res.json().catch(() => ({}));
        throw new Error((t as { detail?: string }).detail || 'Failed to open recipe');
      }
      const doc = await res.json();
      lastVersionRef.current = doc.version ?? 0;
      lastEditRef.current = Date.now();
      setWorkingDocId(doc.id);
      setDocVersion(doc.version ?? 0);
      setDraft(fromDoc(doc.data || {}));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to open recipe');
    }
  };
  const closeEditor = () => { setDraft(null); clearDoc(); };

  // Consume a cross-page "open this recipe" request (Menu Engineering row click)
  // once we have a venue. Runs on the Recipes page mount after navigation.
  useEffect(() => {
    if (!embedded && venueId && pendingOpenRecipeId) {
      const id = pendingOpenRecipeId;
      pendingOpenRecipeId = null;
      openRecipe(id);
    }
  }, [venueId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Start a blank new recipe (the save creates it in Loaded). No working doc yet
  // — it doesn't exist to share until it's been created.
  const startNew = () => { setError(null); setSavedNote(null); setExtracted(null); clearDoc(); setDraft(blankDraft()); };

  // Turn an extracted document draft into a new-recipe draft. The ingredient
  // names/quantities pre-fill the lines, but each line still needs a real Loaded
  // stock item + unit (ref_id/unit_id) picked from the autocomplete before it can
  // save — extraction gives loose text, Loaded needs ids.
  const fromExtracted = (e: Extracted) => {
    setError(null); setSavedNote(null); setExtracted(null); clearDoc();
    setDraft(blankDraft({
      name: e.name || '',
      notes: e.method || '',
      yield_quantity: typeof e.yield_quantity === 'number' ? e.yield_quantity : 1,
      lines: (e.ingredients || []).map((ing) => ({
        key: uid(), kind: 'item' as const, ref_id: null,
        name: ing.name || '', unit_id: null, unit_name: null, unit_ratio: 1,
        quantity: typeof ing.quantity === 'number' ? ing.quantity : 0,
        stock_unit_name: null, stock_unit_ratio: 1, item_deleted: false,
      })),
    }));
  };

  // --- Draft mutations --- (touch() marks recent local edits so the agent-edit
  // poll doesn't refresh over active typing).
  const touch = () => { lastEditRef.current = Date.now(); };
  const setName = (name: string) => { touch(); setDraft((d) => (d ? { ...d, name } : d)); };
  const setNotes = (notes: string) => { touch(); setDraft((d) => (d ? { ...d, notes } : d)); };
  const setYieldQty = (q: number) => { touch(); setDraft((d) => (d ? { ...d, yield_quantity: q } : d)); };
  const setYieldUnit = (unitId: string) => { touch(); setDraft((d) => (d ? { ...d, yield_unit_id: unitId } : d)); };
  const addLine = () => { touch(); setDraft((d) => (d ? { ...d, lines: [...d.lines, { key: uid(), kind: 'item', ref_id: null, name: '', unit_id: null, unit_name: null, unit_ratio: 1, quantity: 0, stock_unit_name: null, stock_unit_ratio: 1, item_deleted: false }] } : d)); };
  const removeLine = (key: string) => { touch(); setDraft((d) => (d ? { ...d, lines: d.lines.filter((l) => l.key !== key) } : d)); };
  const updateLine = (key: string, patch: Partial<EditLine>) => { touch(); setDraft((d) => (d ? { ...d, lines: d.lines.map((l) => (l.key === key ? { ...l, ...patch } : l)) } : d)); };
  // Drag-to-reorder ingredient lines. Moves `fromKey` to just before `toKey`
  // (dropping on the last row appends). Current version only (see the render).
  const reorderLines = (fromKey: string, toKey: string) => {
    if (fromKey === toKey) return;
    touch();
    setDraft((d) => {
      if (!d) return d;
      const lines = [...d.lines];
      const from = lines.findIndex((l) => l.key === fromKey);
      const to = lines.findIndex((l) => l.key === toKey);
      if (from < 0 || to < 0) return d;
      const [moved] = lines.splice(from, 1);
      lines.splice(to, 0, moved);
      return { ...d, lines };
    });
  };

  const pickUnit = (key: string, unitId: string) => {
    const u = unitById.get(unitId);
    updateLine(key, { unit_id: unitId, unit_name: u?.name ?? null, unit_ratio: u?.ratio ?? 1 });
  };
  // Picking an ingredient sets the ref + a sensible default unit (a stock item's
  // own counting unit), so cost shows immediately and the unit is the right type.
  const pickIngredient = (key: string, o: ComboOption) => {
    const patch: Partial<EditLine> = { kind: (o.kind as 'item' | 'recipe') || 'item', ref_id: o.id, name: o.name };
    if (o.kind !== 'recipe') {
      const it = costTables?.items.get(o.id);
      const u = it?.countingUnitId ? unitById.get(it.countingUnitId) : undefined;
      if (u) {
        patch.unit_id = u.id; patch.unit_name = u.name; patch.unit_ratio = u.ratio;
        // A stock item's stock unit IS its counting unit (Stock Cost columns).
        patch.stock_unit_name = u.name; patch.stock_unit_ratio = u.ratio;
      }
    }
    updateLine(key, patch);
  };

  const save = async () => {
    if (!draft || !venueId) return;
    if (draft.lines.some((l) => !l.ref_id || !l.unit_id)) {
      setError('Every line needs an ingredient and a unit.');
      return;
    }
    setSaving(true);
    setError(null);
    setSavedNote(null);
    const isNew = !draft.recipe_id;
    const recipe: Record<string, unknown> = {
      name: draft.name,
      notes: draft.notes,
      is_counted_in_stocktake: draft.is_counted_in_stocktake,
      yield_quantity: num(draft.yield_quantity),
      yield_unit_id: draft.yield_unit_id,
      lines: draft.lines.map((l) => ({
        kind: l.kind,
        ref_id: l.ref_id,
        name: l.name,
        unit_id: l.unit_id,
        unit_name: l.unit_name,
        unit_ratio: l.unit_ratio,
        quantity: num(l.quantity),
      })),
    };
    // recipe_id absent (or create:true) => the CB tool creates the recipe.
    if (isNew) recipe.create = true;
    else { recipe.recipe_id = draft.recipe_id; recipe.version_id = draft.version_id; }
    try {
      const res = await apiFetch('/api/recipe-editor/save', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, recipe }),
      });
      if (!res.ok) {
        const t = await res.json().catch(() => ({}));
        throw new Error((t as { detail?: string }).detail || `Save failed (${res.status})`);
      }
      setSavedNote(isNew ? 'Recipe created in Loaded.' : 'Recipe saved to Loaded.');
      if (!embedded) { closeEditor(); if (venueId) loadRefs(venueId); }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    }
    setSaving(false);
  };

  // --- Styles (mirror InvoicesDashboard/OrdersDashboard) ---
  const input: React.CSSProperties = { padding: '5px 8px', fontSize: '0.85rem', border: `1px solid ${colors.border}`, borderRadius: 6, fontFamily: 'inherit' };
  const btn = (bg: string, fg = '#fff'): React.CSSProperties => ({ padding: '5px 12px', fontSize: '0.8rem', fontWeight: 600, border: 'none', borderRadius: 6, background: bg, color: fg, cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap' });
  const ghost: React.CSSProperties = { ...btn('#fff', colors.textSecondary), border: `1px solid ${colors.border}` };
  const selectStyle: React.CSSProperties = { padding: '3px 8px', fontSize: '0.75rem', border: `1px solid ${colors.border}`, borderRadius: 6, fontFamily: 'inherit', color: colors.textSecondary, backgroundColor: '#fff' };
  const thStyle: React.CSSProperties = { padding: '8px 12px', textAlign: 'left', fontSize: '0.72rem', fontWeight: 600, color: colors.textSecondary, borderBottom: `2px solid ${colors.border}`, whiteSpace: 'nowrap' };
  const tdStyle: React.CSSProperties = { padding: '8px 12px', fontSize: '0.8rem', color: colors.textPrimary, borderBottom: `1px solid ${colors.borderLight}` };

  const venueSelect = venues.length > 1 && (
    <select value={venueId || ''} onChange={(e) => changeVenue(e.target.value)} style={selectStyle}>
      {venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
    </select>
  );
  const notes = (
    <>
      {savedNote && <span style={{ fontSize: '0.8rem', color: colors.success }}>{savedNote}</span>}
      {error && <span style={{ fontSize: '0.8rem', color: colors.error }}>{error}</span>}
    </>
  );

  // Unit picker, scoped to a stock unit type when known (so a solid ingredient
  // offers kg/g, not all 450+ units). Falls back to every unit if type unknown,
  // and always keeps the currently-selected unit visible.
  const unitSelect = (value: string | null, onChange: (id: string) => void, type?: string, width = 120) => {
    const list = type ? units.filter((u) => !u.type || u.type === type || u.id === value) : units;
    return (
      <select value={value || ''} onChange={(e) => onChange(e.target.value)} style={{ ...input, width }}>
        {!value && <option value="">unit</option>}
        {list.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
      </select>
    );
  };

  if (draft) {
    const yieldUnitName = units.find((u) => u.id === draft.yield_unit_id)?.name || 'yield';
    const lbl: React.CSSProperties = { fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: colors.textMuted };
    // Version viewer: a viewVersionId that isn't in this recipe (or null) means
    // the current, editable version; any other is a past version shown read-only.
    const curVer = draft.versions.find((v) => v.current) || null;
    const selVer = (viewVersionId ? draft.versions.find((v) => v.id === viewVersionId) : null) || curVer;
    const viewingPast = !!selVer && !selVer.current;
    const viewLines = viewingPast && selVer ? selVer.lines : draft.lines;
    const viewYieldQty = viewingPast && selVer ? selVer.yield_quantity : draft.yield_quantity;
    const viewYieldUnit = viewingPast && selVer ? (selVer.yield_unit_name || 'yield') : yieldUnitName;
    const viewTotal = viewingPast && selVer ? linesTotal(selVer.lines) : totalCost;
    const versionSelect = draft.recipe_id && draft.versions.length > 0 && (
      <select value={selVer?.id || ''} onChange={(e) => setViewVersionId(e.target.value)} style={selectStyle} title="Recipe version">
        {draft.versions.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
      </select>
    );
    return (
      <div style={{ maxWidth: 1080 }}>
        {/* Header: back · title · venue · version · save */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
          {!embedded && <button onClick={closeEditor} disabled={saving} style={ghost}>← Recipes</button>}
          <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: colors.textPrimary }}>{draft.recipe_id ? 'Edit recipe' : 'New recipe'}</h2>
          {venueSelect}
          {versionSelect}
          <span style={{ flex: 1 }} />
          {notes}
          <button onClick={save} disabled={saving || viewingPast || !draft.name.trim()} title={viewingPast ? 'Switch to Current to edit' : undefined} style={btn(colors.executive_chef)}>{saving ? 'Saving…' : draft.recipe_id ? 'Save to Loaded' : 'Create in Loaded'}</button>
        </div>
        {viewingPast && (
          <div style={{ fontSize: '0.78rem', color: colors.textMuted, background: colors.selectedBg, border: `1px solid ${colors.borderLight}`, borderRadius: 8, padding: '0.5rem 0.7rem', marginBottom: '0.9rem' }}>
            Viewing a past version — read-only. Switch to <strong>Current</strong> to edit.
          </div>
        )}

        {/* Details card: name · yield · live cost */}
        <div style={{ border: `1px solid ${colors.border}`, borderRadius: 10, padding: '1rem 1.1rem', marginBottom: '1.1rem', background: '#fff' }}>
          {!draft.recipe_id && <div style={{ fontSize: '0.78rem', color: colors.textMuted, marginBottom: '0.6rem' }}>This will be created in Loaded when you save.</div>}
          <input value={draft.name} onChange={(e) => setName(e.target.value)} readOnly={viewingPast} placeholder="Recipe name"
            style={{ fontSize: '1.2rem', fontWeight: 700, color: colors.textPrimary, width: '100%', boxSizing: 'border-box', border: 'none', borderBottom: `1px solid ${colors.border}`, padding: '2px 0 7px', fontFamily: 'inherit', outline: 'none' }} />
          <div style={{ marginTop: '0.9rem' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', width: 'fit-content' }}>
              <span style={lbl}>Yield</span>
              {viewingPast ? (
                <div style={{ fontSize: '0.85rem', color: colors.textPrimary }}>{+viewYieldQty.toFixed(2)} {viewYieldUnit}</div>
              ) : (
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <input type="number" step="0.01" min="0" value={draft.yield_quantity} onChange={(e) => setYieldQty(parseFloat(e.target.value))} style={{ ...input, width: 80, textAlign: 'right' }} />
                  {unitSelect(draft.yield_unit_id, setYieldUnit)}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Ingredients */}
        <div style={{ ...lbl, marginBottom: '0.35rem' }}>Ingredients</div>
        <div style={{ border: `1px solid ${colors.border}`, borderRadius: 8 }}>
          <div style={{ display: 'flex', gap: '0.4rem', padding: '0.5rem 0.7rem', background: colors.selectedBg, fontSize: '0.68rem', color: colors.textMuted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', borderTopLeftRadius: 7, borderTopRightRadius: 7 }}>
            <span style={{ width: 18 }} /><span style={{ width: 66 }}>Qty</span><span style={{ width: 104 }}>Unit</span><span style={{ flex: 1, paddingLeft: '0.2rem' }}>Ingredient</span><span style={{ width: 88 }}>Stock unit</span><span style={{ width: 92, textAlign: 'right' }}>Stock cost</span><span style={{ width: 84, textAlign: 'right' }}>Recipe cost</span><span style={{ width: 28 }} />
          </div>
          <div style={{ padding: '0.6rem 0.7rem' }}>
            {viewLines.length === 0 && (
              <div style={{ fontSize: '0.85rem', color: colors.textMuted, padding: '0.15rem 0 0.5rem' }}>No ingredients yet — add one below.</div>
            )}
            {viewingPast
              ? viewLines.map((l) => {
                  const lc = lineCostOf(l);
                  const sc = stockCostOf(l);
                  return (
                    <div key={l.key} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.45rem', background: l.item_deleted ? 'rgba(229,72,77,0.08)' : undefined, borderRadius: 6 }}>
                      <span style={{ width: 18 }} />
                      <span style={{ width: 66, fontSize: '0.85rem', color: colors.textPrimary }}>{+l.quantity.toFixed(3)}</span>
                      <span style={{ width: 104, fontSize: '0.85rem', color: colors.textMuted, paddingLeft: '0.2rem' }}>{l.unit_name || ''}</span>
                      <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '0.35rem', paddingLeft: '0.2rem' }}>
                        <span style={{ fontSize: '0.85rem', color: colors.textPrimary }}>{l.name}</span>
                        {l.item_deleted && <span title="This stock item has been deleted in Loaded" style={{ fontSize: '0.55rem', fontWeight: 700, color: '#e5484d', border: '1px solid #e5484d', padding: '1px 5px', borderRadius: 4, textTransform: 'uppercase', letterSpacing: '0.03em', whiteSpace: 'nowrap' }}>deleted</span>}
                      </div>
                      <span style={{ width: 88, fontSize: '0.85rem', color: colors.textMuted }}>{stockUnitOf(l)}</span>
                      <span style={{ width: 92, textAlign: 'right', fontSize: '0.85rem', color: colors.textMuted }}>{sc != null ? money(sc) : '—'}</span>
                      <span style={{ width: 84, textAlign: 'right', fontSize: '0.85rem', color: lc?.complete ? colors.textPrimary : colors.textMuted }} title={lc && !lc.complete ? 'No price for this ingredient yet' : undefined}>{lc?.complete ? money(lc.cost) : '—'}</span>
                      <span style={{ width: 28 }} />
                    </div>
                  );
                })
              : draft.lines.map((l) => {
                  const lc = lineCostOf(l);
                  const sc = stockCostOf(l);
                  return (
                    <div key={l.key}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={(e) => { e.preventDefault(); const from = e.dataTransfer.getData('text/plain'); if (from) reorderLines(from, l.key); }}
                      style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.45rem', background: l.item_deleted ? 'rgba(229,72,77,0.08)' : undefined, borderRadius: 6 }}>
                      <span draggable onDragStart={(e) => { e.dataTransfer.setData('text/plain', l.key); e.dataTransfer.effectAllowed = 'move'; }} title="Drag to reorder" style={{ width: 18, textAlign: 'center', cursor: 'grab', color: colors.textMuted, fontSize: '0.85rem', userSelect: 'none' }}>⠿</span>
                      <input type="number" step="0.001" min="0" value={l.quantity} onChange={(e) => updateLine(l.key, { quantity: parseFloat(e.target.value) })} style={{ ...input, width: 66, textAlign: 'left', fontSize: '0.85rem' }} />
                      {unitSelect(l.unit_id, (u) => pickUnit(l.key, u), unitTypeForLine(l), 104)}
                      <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                        <div style={{ flex: 1 }}>
                          <Combobox
                            value={l.name}
                            options={ingredientOptions}
                            onType={(t) => updateLine(l.key, { name: t, ref_id: null })}
                            onPick={(o) => pickIngredient(l.key, o)}
                            placeholder="Search ingredient or sub-recipe"
                          />
                        </div>
                        {l.item_deleted && <span title="This stock item has been deleted in Loaded" style={{ fontSize: '0.55rem', fontWeight: 700, color: '#e5484d', border: '1px solid #e5484d', padding: '1px 5px', borderRadius: 4, textTransform: 'uppercase', letterSpacing: '0.03em', whiteSpace: 'nowrap' }}>deleted</span>}
                      </div>
                      <span style={{ width: 88, fontSize: '0.85rem', color: colors.textMuted }}>{stockUnitOf(l)}</span>
                      <span style={{ width: 92, textAlign: 'right', fontSize: '0.85rem', color: colors.textMuted }} title="Cost per stock unit">{sc != null ? money(sc) : '—'}</span>
                      <span style={{ width: 84, textAlign: 'right', fontSize: '0.85rem', color: lc?.complete ? colors.textPrimary : colors.textMuted }} title={lc && !lc.complete ? 'No price for this ingredient yet' : undefined}>
                        {lc?.complete ? money(lc.cost) : '—'}
                      </span>
                      <button onClick={() => removeLine(l.key)} style={{ ...ghost, width: 28, padding: '5px 0', textAlign: 'center' }} title="Remove">✕</button>
                    </div>
                  );
                })}
            {!viewingPast && <button onClick={addLine} style={{ ...ghost, marginTop: 4 }}>+ Add ingredient</button>}
          </div>
          {viewTotal && viewLines.some((l) => l.ref_id) && (
            <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'baseline', gap: '1.25rem', padding: '0.6rem 0.75rem', borderTop: `1px solid ${colors.borderLight}`, background: colors.pageBg, borderBottomLeftRadius: 7, borderBottomRightRadius: 7 }}>
              {viewYieldQty > 0 && (
                <span style={{ fontSize: '0.8rem', color: colors.textMuted }}>{money(viewTotal.cost / viewYieldQty)} / {viewYieldUnit}</span>
              )}
              <span style={{ fontSize: '0.95rem', fontWeight: 700, color: colors.textPrimary }}>{viewTotal.complete ? '' : '~'}{money(viewTotal.cost)} total</span>
            </div>
          )}
        </div>
        {viewTotal && !viewTotal.complete && viewLines.some((l) => l.ref_id) && (
          <div style={{ fontSize: '0.72rem', color: colors.textMuted, marginTop: '0.4rem' }}>~ Some lines have no price yet, so the total is a partial estimate.</div>
        )}

        {/* Method — below the ingredients */}
        <div style={{ marginTop: '1.25rem' }}>
          <div style={{ ...lbl, marginBottom: '0.35rem' }}>Method / notes</div>
          <HtmlField html={draft.notes} resetKey={draft.recipe_id || 'new'} onChange={setNotes} placeholder="Add a method or notes…" />
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* Header — mirrors OrdersDashboard: title + count + venue + New button */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem', flexWrap: 'wrap', gap: '0.5rem' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: colors.textPrimary }}>Recipes</h2>
          <span style={{ fontSize: '0.75rem', color: colors.textMuted }}>
            {loading ? 'Loading…' : `${visibleRecipes.length}${query ? ` of ${recipes.length}` : ''} recipe${recipes.length === 1 ? '' : 's'}`}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          {notes}
          {venueSelect}
          <button onClick={startNew} disabled={!venueId} style={{ ...btn(colors.executive_chef), background: venueId ? colors.executive_chef : '#ccc', cursor: venueId ? 'pointer' : 'default' }}>+ New recipe</button>
        </div>
      </div>

      {/* Search box */}
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search recipes…"
        style={{ ...input, width: '100%', maxWidth: 320, marginBottom: '0.75rem', boxSizing: 'border-box' }}
      />

      {/* Upload a recipe document → extract a structured draft. Web-only: the
          MCP iframe has no multipart upload. */}
      {!embedded && (
        <div style={{ margin: '0 0 0.9rem' }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', padding: '0.5rem 0.85rem', border: `1px dashed ${colors.border}`, borderRadius: 8, background: '#fff', cursor: extracting ? 'default' : 'pointer', fontWeight: 600, color: colors.textSecondary, fontSize: '0.8rem', opacity: extracting ? 0.6 : 1 }}>
            {extracting ? 'Reading document…' : 'Extract recipe from document'}
            <input
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,image/*,application/pdf"
              disabled={extracting}
              onChange={(e) => { onDoc(e.target.files?.[0] || null); e.target.value = ''; }}
              style={{ display: 'none' }}
            />
          </label>
          <span style={{ marginLeft: '0.6rem', color: colors.textMuted, fontSize: '0.82rem' }}>PDF or image</span>
        </div>
      )}

      {extracted && (
        <div style={{ margin: '0 0 1rem', padding: '0.9rem 1rem', border: `1px solid ${colors.border}`, borderRadius: 10, background: '#fbf7f4' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '1rem' }}>
            <strong style={{ color: colors.textPrimary, fontSize: '1.05rem' }}>{extracted.name || 'Untitled recipe'}</strong>
            <button onClick={() => setExtracted(null)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: colors.textMuted, fontSize: '1.1rem', lineHeight: 1 }} aria-label="Dismiss">×</button>
          </div>
          {(extracted.yield_quantity != null || extracted.yield_unit) && (
            <div style={{ color: colors.textMuted, fontSize: '0.85rem', marginTop: '0.15rem' }}>
              Yields {extracted.yield_quantity ?? ''} {extracted.yield_unit || ''}
            </div>
          )}
          {!!extracted.ingredients?.length && (
            <ul style={{ margin: '0.6rem 0 0', paddingLeft: '1.1rem', color: colors.textPrimary }}>
              {extracted.ingredients.map((ing, i) => (
                <li key={i} style={{ marginBottom: '0.15rem' }}>
                  {ing.quantity ?? ''} {ing.unit || ''} {ing.name || ''}
                </li>
              ))}
            </ul>
          )}
          {extracted.method && (
            <p style={{ margin: '0.6rem 0 0', color: colors.textSecondary, fontSize: '0.88rem', whiteSpace: 'pre-wrap' }}>{extracted.method}</p>
          )}
          <div style={{ marginTop: '0.8rem', display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
            <button onClick={() => fromExtracted(extracted)} style={btn(colors.executive_chef)}>Start a new recipe from this</button>
            <span style={{ color: colors.textMuted, fontSize: '0.78rem', fontStyle: 'italic' }}>
              You&apos;ll match each ingredient to a Loaded stock item and unit before it saves.
            </span>
          </div>
        </div>
      )}

      {error && !draft && <div style={{ color: colors.error, padding: '0.4rem 0' }}>{error}</div>}

      {loading ? (
        <div style={{ padding: '2rem', textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>Loading recipes…</div>
      ) : visibleRecipes.length === 0 ? (
        <div style={{ padding: '2rem', textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>
          {recipes.length === 0 ? 'No recipes.' : `No recipes match “${query}”.`}
        </div>
      ) : (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 480 }}>
            <thead>
              <tr>
                <th style={thStyle}>Recipe</th>
                <th style={{ ...thStyle, textAlign: 'right', width: 110 }}>Ingredients</th>
                <th style={{ ...thStyle, width: 160 }}>Yield</th>
              </tr>
            </thead>
            <tbody>
              {visibleRecipes.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => openRecipe(r.id)}
                  style={{ cursor: 'pointer' }}
                  onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = colors.pageBg; }}
                  onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = ''; }}
                >
                  <td style={{ ...tdStyle, fontWeight: 600 }}>
                    {r.name}
                    {r.prep && (
                      <span style={{ marginLeft: '0.5rem', fontSize: '0.62rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: colors.executive_chef, border: `1px solid ${colors.executive_chef}`, borderRadius: 4, padding: '1px 5px', verticalAlign: 'middle' }}>Prep</span>
                    )}
                  </td>
                  <td style={{ ...tdStyle, textAlign: 'right', color: r.ingredients ? colors.textPrimary : colors.textMuted }}>{r.ingredients}</td>
                  <td style={{ ...tdStyle, color: colors.textSecondary }}>{r.yieldText}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
