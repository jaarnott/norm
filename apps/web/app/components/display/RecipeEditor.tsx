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
import { Check, ChevronRight, FileUp, GripVertical, History, LoaderCircle, Plus, Search, X } from 'lucide-react';
import { apiFetch, callComponentApi } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import Badge from '../ui/Badge';
import BackLink from '../ui/BackLink';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
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

// How much room the component has (see `fit` in the component).
type Fit = 'phone' | 'narrow' | 'wide';
const fitFor = (w: number): Fit => (w < 520 ? 'phone' : w < 720 ? 'narrow' : 'wide');

// ---- Looks. tokens.css classes do most of the work (.n-table, .n-input,
// .n-select, .n-label, .n-card); bundled into Claude, so tokens only.
// Numbers in inputs line up like the columns they sit in.
const numInput: React.CSSProperties = { textAlign: 'right', fontVariantNumeric: 'tabular-nums', padding: '0 8px' };
const tabular: React.CSSProperties = { fontVariantNumeric: 'tabular-nums' };

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
  // The tool result may be a raw Loaded recipe (has currentVersion) or the
  // get_recipes consolidator envelope {recipe: <summary|raw>, ...}. A raw
  // payload opens directly; a summary carries names but not the line/unit ids
  // editing needs, so we keep its id and self-load the full recipe below.
  const envelope =
    data && typeof data === 'object' && typeof (data as { recipe?: unknown }).recipe === 'object'
      ? ((data as { recipe: Record<string, unknown> }).recipe)
      : null;
  const initialRecipe =
    data && typeof data === 'object' && (data as { currentVersion?: unknown }).currentVersion
      ? (data as Record<string, unknown>)
      : envelope && (envelope as { currentVersion?: unknown }).currentVersion
        ? envelope
        : null;
  const selfLoadRecipeId = !initialRecipe && envelope?.id ? String(envelope.id) : null;

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

  // Layout only: how much room the component has — its OWN width, since a chat
  // column or Claude's frame is narrower than the window. Below 720px the
  // ingredient table becomes stacked lines; below 520px the recipe list
  // becomes one-column rows.
  const [fit, setFit] = useState<Fit>(() => (typeof window === 'undefined' ? 'wide' : fitFor(window.innerWidth)));
  const fitObserver = useRef<ResizeObserver | null>(null);
  const measureRef = useCallback((el: HTMLDivElement | null) => {
    fitObserver.current?.disconnect();
    fitObserver.current = null;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w > 0) setFit(fitFor(w));
    });
    ro.observe(el);
    fitObserver.current = ro;
  }, []);

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

  // Embedded self-load: a get_recipes summary names the recipe but doesn't
  // carry the raw payload the editor needs, so fetch it through the component
  // API — the same read the page's open flow uses.
  useEffect(() => {
    if (!embedded || !selfLoadRecipeId || !venueId || draft) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await callComponentApi('recipe_editor', 'get_recipe', { recipe_id: selfLoadRecipeId }, venueId);
        const raw = res?.data as Record<string, unknown> | undefined;
        if (!cancelled && raw?.currentVersion) setDraft(toDraft(raw));
      } catch { /* the embedded empty state below covers it */ }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embedded, selfLoadRecipeId, venueId]);

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

  // --- Presentation ---
  // A PAGE instance (props.persistVenue) gets the page header and sits on the
  // page's own frame; in a conversation, or inside Claude, it is one compact
  // card.
  const isPage = persistVenue && !embedded;

  const venuePicker = venues.length > 1 && (
    <VenueSelect venues={venues} value={venueId} onChange={changeVenue} />
  );
  const savedOk = savedNote && (
    <span role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-sm)', color: 'var(--ok)' }}>
      <Icon icon={Check} size={14} />
      {savedNote}
    </span>
  );
  const errorBlock = error && (
    <div style={{ marginBottom: 12 }}>
      <PageState kind="error" title={error} />
    </div>
  );

  // Unit picker, scoped to a stock unit type when known (so a solid ingredient
  // offers kg/g, not all 450+ units). Falls back to every unit if type unknown,
  // and always keeps the currently-selected unit visible.
  const unitSelect = (value: string | null, onChange: (id: string) => void, type?: string, style?: React.CSSProperties, label = 'Unit') => {
    const list = type ? units.filter((u) => !u.type || u.type === type || u.id === value) : units;
    return (
      <select className="n-select" aria-label={label} value={value || ''} onChange={(e) => onChange(e.target.value)} style={{ width: '100%', ...style }}>
        {!value && <option value="">Unit</option>}
        {list.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
      </select>
    );
  };

  // Embedded with no single recipe to show — a get_recipes search result, or
  // the self-load still in flight. The full picker page belongs to the web
  // app; in a chat card, Claude's own text already carries the matches.
  if (embedded && !draft) {
    return selfLoadRecipeId ? (
      <div className="n-card">
        <PageState kind="loading" title="Loading recipe…" />
      </div>
    ) : null;
  }

  if (draft) {
    const yieldUnitName = units.find((u) => u.id === draft.yield_unit_id)?.name || 'yield';
    // Version viewer: a viewVersionId that isn't in this recipe (or null) means
    // the current, editable version; any other is a past version shown read-only.
    const curVer = draft.versions.find((v) => v.current) || null;
    const selVer = (viewVersionId ? draft.versions.find((v) => v.id === viewVersionId) : null) || curVer;
    const viewingPast = !!selVer && !selVer.current;
    const viewLines = viewingPast && selVer ? selVer.lines : draft.lines;
    const viewYieldQty = viewingPast && selVer ? selVer.yield_quantity : draft.yield_quantity;
    const viewYieldUnit = viewingPast && selVer ? (selVer.yield_unit_name || 'yield') : yieldUnitName;
    const viewTotal = viewingPast && selVer ? linesTotal(selVer.lines) : totalCost;
    const showTotals = !!viewTotal && viewLines.some((l) => l.ref_id);

    const title = draft.recipe_id ? 'Edit recipe' : 'New recipe';
    const meta = draft.recipe_id ? undefined : 'This will be created in Loaded when you save.';
    // Same as the old disabled back button: does nothing while a save runs.
    const back = () => { if (!saving) closeEditor(); };
    const versionSelect = draft.recipe_id && draft.versions.length > 0 && (
      <select className="n-select" value={selVer?.id || ''} onChange={(e) => setViewVersionId(e.target.value)} title="Recipe version" aria-label="Recipe version">
        {draft.versions.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
      </select>
    );
    const actions = (
      <>
        {savedOk}
        {venuePicker}
        {versionSelect}
        <Button variant="primary" onClick={save} disabled={saving || viewingPast || !draft.name.trim()} title={viewingPast ? 'Switch to Current to edit' : undefined}>
          {saving ? 'Saving…' : draft.recipe_id ? 'Save to Loaded' : 'Create in Loaded'}
        </Button>
      </>
    );
    const SectionHeading = isPage ? 'h2' : 'h3';
    const sectionTitle: React.CSSProperties = { margin: '0 0 8px', fontSize: isPage ? 'var(--fs-md)' : 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' };

    // One ingredient line's pieces — laid out below as a table row when there
    // is room, else as stacked lines. A past version renders read-only text.
    const editable = !viewingPast;
    const linePieces = (l: EditLine) => {
      const lc = lineCostOf(l);
      const sc = stockCostOf(l);
      return {
        grip: editable ? (
          <span
            draggable
            onDragStart={(e) => { e.dataTransfer.setData('text/plain', l.key); e.dataTransfer.effectAllowed = 'move'; }}
            title="Drag to reorder"
            style={{ display: 'inline-flex', cursor: 'grab', color: 'var(--icon)', userSelect: 'none' }}
          >
            <Icon icon={GripVertical} size={16} />
          </span>
        ) : null,
        qty: editable ? (
          <input type="number" step="0.001" min="0" className="n-input" aria-label="Quantity" value={l.quantity} onChange={(e) => updateLine(l.key, { quantity: parseFloat(e.target.value) })} style={{ width: '100%', ...numInput }} />
        ) : (
          <>{+l.quantity.toFixed(3)}</>
        ),
        unit: editable
          ? unitSelect(l.unit_id, (u) => pickUnit(l.key, u), unitTypeForLine(l))
          : <span style={{ color: 'var(--text-soft)' }}>{l.unit_name || ''}</span>,
        ingredient: editable ? (
          <Combobox
            value={l.name}
            options={ingredientOptions}
            onType={(t) => updateLine(l.key, { name: t, ref_id: null })}
            onPick={(o) => pickIngredient(l.key, o)}
            placeholder="Search ingredient or sub-recipe"
          />
        ) : (
          <span style={{ color: 'var(--text)' }}>{l.name}</span>
        ),
        deleted: l.item_deleted ? <Badge tone="error" title="This stock item has been deleted in Loaded">Deleted</Badge> : null,
        stockUnit: stockUnitOf(l),
        stockCost: sc != null ? money(sc) : '—',
        cost: lc?.complete ? money(lc.cost) : '—',
        costStyle: { color: lc?.complete ? 'var(--text)' : 'var(--muted)' } as React.CSSProperties,
        costTitle: lc && !lc.complete ? 'No price for this ingredient yet' : undefined,
        remove: editable ? <IconButton icon={X} label="Remove ingredient" iconSize={16} onClick={() => removeLine(l.key)} /> : null,
        // Drag-to-reorder drop target (current version only).
        dnd: editable
          ? {
              onDragOver: (e: React.DragEvent) => e.preventDefault(),
              onDrop: (e: React.DragEvent) => { e.preventDefault(); const from = e.dataTransfer.getData('text/plain'); if (from) reorderLines(from, l.key); },
            }
          : {},
      };
    };
    const noLines = 'No ingredients yet — add one below.';

    // Wide: a table. Inputs fill their cells; editable rows sit a little
    // tighter. A past version is read-only, so it has no grip or remove column.
    const cell: React.CSSProperties = { paddingLeft: 6, paddingRight: 6, ...(editable ? { paddingTop: 6, paddingBottom: 6 } : {}) };
    const firstCell: React.CSSProperties = editable ? cell : { ...cell, paddingLeft: 12 };
    const lastCell: React.CSSProperties = editable ? cell : { ...cell, paddingRight: 12 };
    const gripCell: React.CSSProperties = { ...cell, width: 16, paddingLeft: 12, paddingRight: 0 };
    const removeCell: React.CSSProperties = { ...cell, width: 32, paddingLeft: 0, paddingRight: 8 };
    // The total lines up under the Recipe cost column: past the remove column
    // (32 + 8) and the cell's own padding (6) when there is one.
    const totalEdge = editable ? 46 : 12;
    const ingredientTable = (
      <table className="n-table">
        <thead>
          <tr>
            {editable && <th style={gripCell} />}
            <th className="num" style={{ ...firstCell, width: 84 }}>Qty</th>
            <th style={{ ...cell, width: 140 }}>Unit</th>
            <th style={cell}>Ingredient</th>
            <th style={{ ...cell, width: 92 }}>Stock unit</th>
            <th className="num" style={{ ...cell, width: 92 }}>Stock cost</th>
            <th className="num" style={{ ...lastCell, width: 96 }}>Recipe cost</th>
            {editable && <th style={removeCell} />}
          </tr>
        </thead>
        <tbody>
          {viewLines.length === 0 && (
            <tr><td colSpan={editable ? 8 : 6} style={{ paddingLeft: 12, color: 'var(--muted)' }}>{noLines}</td></tr>
          )}
          {viewLines.map((l) => {
            const p = linePieces(l);
            return (
              <tr key={l.key} {...p.dnd} style={l.item_deleted ? { background: 'var(--error-bg)' } : undefined}>
                {editable && <td style={gripCell}>{p.grip}</td>}
                <td className="num" style={firstCell}>{p.qty}</td>
                <td style={cell}>{p.unit}</td>
                <td style={cell}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>{p.ingredient}</div>
                    {p.deleted}
                  </div>
                </td>
                <td style={{ ...cell, color: 'var(--muted)' }}>{p.stockUnit}</td>
                <td className="num" style={{ ...cell, color: 'var(--muted)' }} title={editable ? 'Cost per stock unit' : undefined}>{p.stockCost}</td>
                <td className="num" style={{ ...lastCell, ...p.costStyle }} title={p.costTitle}>{p.cost}</td>
                {editable && <td style={removeCell}>{p.remove}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    );

    // Narrow: each line stacked — ingredient, then qty · unit · cost, then the
    // stock unit and its cost.
    const ingredientStack = (
      <div style={{ borderTop: '1px solid var(--line-strong)' }}>
        {viewLines.length === 0 && (
          <div style={{ padding: '10px 0', borderBottom: '1px solid var(--line)', color: 'var(--muted)' }}>{noLines}</div>
        )}
        {viewLines.map((l) => {
          const p = linePieces(l);
          const indent = editable ? 22 : 0;
          return (
            <div
              key={l.key}
              {...p.dnd}
              style={{
                padding: '10px 0', borderBottom: '1px solid var(--line)',
                ...(l.item_deleted ? { background: 'var(--error-bg)', margin: '0 -8px', paddingLeft: 8, paddingRight: 8 } : {}),
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {p.grip}
                <div style={{ flex: 1, minWidth: 0, fontWeight: editable ? undefined : 500, overflowWrap: 'anywhere' }}>{p.ingredient}</div>
                {p.remove}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: editable ? 8 : 2, paddingLeft: indent }}>
                {editable ? (
                  <>
                    <div style={{ flex: '0 0 84px' }}>{p.qty}</div>
                    <div style={{ flex: '1 1 0', minWidth: 0, maxWidth: 200 }}>{p.unit}</div>
                  </>
                ) : (
                  <span style={tabular}>{p.qty} {p.unit}</span>
                )}
                <div style={{ marginLeft: 'auto', textAlign: 'right', ...tabular }} title={p.costTitle}>
                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Recipe cost</div>
                  <div style={{ fontWeight: 600, ...p.costStyle }}>{p.cost}</div>
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '4px 12px', marginTop: 6, paddingLeft: indent, fontSize: 'var(--fs-sm)', color: 'var(--muted)', ...tabular }}>
                {p.deleted}
                <span>Stock unit {p.stockUnit || '—'}</span>
                <span title="Cost per stock unit">Stock cost {p.stockCost}</span>
              </div>
            </div>
          );
        })}
      </div>
    );

    const stacked = fit !== 'wide';
    const body = (
      <>
        {errorBlock}
        {viewingPast && (
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 12px', marginBottom: 16, borderRadius: 'var(--radius)', background: 'var(--info-bg)', color: 'var(--info)', fontSize: 'var(--fs-sm)' }}>
            <Icon icon={History} size={16} style={{ marginTop: 1 }} />
            <span>Viewing a past version — read-only. Switch to <strong>Current</strong> to edit.</span>
          </div>
        )}

        {/* Details: name · yield */}
        <div style={{ marginBottom: 24 }}>
          <label style={{ display: 'block', maxWidth: 640 }}>
            <span className="n-label">Name</span>
            <input
              className="n-input n-keep-size"
              value={draft.name}
              onChange={(e) => setName(e.target.value)}
              readOnly={viewingPast}
              placeholder="Recipe name"
              style={{ width: '100%', height: 40, fontSize: 'var(--fs-lg)', fontWeight: 600, ...(viewingPast ? { background: 'var(--surface)', borderColor: 'var(--line)' } : {}) }}
            />
          </label>
          <div style={{ marginTop: 12 }}>
            <span className="n-label">Yield</span>
            {viewingPast ? (
              <div style={{ color: 'var(--text)', ...tabular }}>{+viewYieldQty.toFixed(2)} {viewYieldUnit}</div>
            ) : (
              <div style={{ display: 'flex', gap: 8 }}>
                <input type="number" step="0.01" min="0" className="n-input" aria-label="Yield quantity" value={draft.yield_quantity} onChange={(e) => setYieldQty(parseFloat(e.target.value))} style={{ width: 88, ...numInput }} />
                {unitSelect(draft.yield_unit_id, setYieldUnit, undefined, { width: 160 }, 'Yield unit')}
              </div>
            )}
          </div>
        </div>

        {/* Ingredients */}
        <section>
          <SectionHeading style={sectionTitle}>Ingredients</SectionHeading>
          {stacked ? ingredientStack : ingredientTable}
          {(editable || showTotals) && (
            <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px 16px', padding: stacked ? '12px 0 0' : `12px ${totalEdge}px 0 12px` }}>
              {editable && <Button size="sm" icon={Plus} onClick={addLine}>Add ingredient</Button>}
              {viewTotal && showTotals && (
                <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', justifyContent: 'flex-end', gap: '4px 16px', marginLeft: 'auto', ...tabular }}>
                  {viewYieldQty > 0 && (
                    <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{money(viewTotal.cost / viewYieldQty)} / {viewYieldUnit}</span>
                  )}
                  <span style={{ fontSize: 'var(--fs-md)', fontWeight: 700, color: 'var(--text)' }}>{viewTotal.complete ? '' : '~'}{money(viewTotal.cost)} total</span>
                </div>
              )}
            </div>
          )}
          {viewTotal && showTotals && !viewTotal.complete && (
            <div style={{ marginTop: 6, padding: stacked ? 0 : `0 ${totalEdge}px 0 12px`, textAlign: 'right', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
              ~ Some lines have no price yet, so the total is a partial estimate.
            </div>
          )}
        </section>

        {/* Method — below the ingredients */}
        <section style={{ marginTop: 24 }}>
          <SectionHeading style={sectionTitle}>Method / notes</SectionHeading>
          <HtmlField html={draft.notes} resetKey={draft.recipe_id || 'new'} onChange={setNotes} placeholder="Add a method or notes…" />
        </section>
      </>
    );

    if (isPage) {
      return (
        <div ref={measureRef}>
          <PageHeader back={{ label: 'Recipes', onClick: back }} title={title} meta={meta} actions={actions} />
          {body}
        </div>
      );
    }
    return (
      <div ref={measureRef} className="n-card" style={{ padding: 16 }}>
        <div style={{ marginBottom: 16 }}>
          {!embedded && (
            <div style={{ marginBottom: 6 }}>
              <BackLink label="Recipes" onClick={back} />
            </div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <h2 style={{ margin: '0 auto 0 0', fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>{title}</h2>
            {actions}
          </div>
          {meta && <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta}</div>}
        </div>
        {body}
      </div>
    );
  }

  // --- The recipes list ---
  const countMeta = loading ? 'Loading…' : `${visibleRecipes.length}${query ? ` of ${recipes.length}` : ''} recipe${recipes.length === 1 ? '' : 's'}`;
  const listActions = (
    <>
      {savedOk}
      {venuePicker}
      {/* One primary per screen: while an extracted draft is on offer, its
          "Start a new recipe from this" is the primary. */}
      <Button variant={extracted ? 'secondary' : 'primary'} icon={Plus} onClick={startNew} disabled={!venueId}>New recipe</Button>
    </>
  );
  const toolbar = (
    <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
      <span style={{ position: 'relative', display: 'inline-flex', flex: '1 1 220px', maxWidth: fit === 'phone' ? undefined : 320 }}>
        <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
        <input
          className="n-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search recipes…"
          aria-label="Search recipes"
          style={{ width: '100%', paddingLeft: 34 }}
        />
      </span>
      {/* Upload a recipe document → extract a structured draft. Web-only: the
          MCP iframe has no multipart upload. */}
      {!embedded && (
        <span style={{ display: 'inline-flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <label className="n-btn n-btn--secondary" style={{ cursor: extracting ? 'default' : 'pointer', opacity: extracting ? 0.6 : 1 }}>
            <Icon icon={extracting ? LoaderCircle : FileUp} size={16} style={extracting ? { animation: 'n-spin 1s linear infinite' } : undefined} />
            {extracting ? 'Reading document…' : 'Extract recipe from document'}
            <input
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,image/*,application/pdf"
              disabled={extracting}
              onChange={(e) => { onDoc(e.target.files?.[0] || null); e.target.value = ''; }}
              style={{ display: 'none' }}
            />
          </label>
          <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>PDF or image</span>
        </span>
      )}
    </div>
  );

  const extractedCard = extracted && (
    <div className="n-card" style={{ padding: 16, marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)', overflowWrap: 'anywhere' }}>{extracted.name || 'Untitled recipe'}</div>
          {(extracted.yield_quantity != null || extracted.yield_unit) && (
            <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              Yields {extracted.yield_quantity ?? ''} {extracted.yield_unit || ''}
            </div>
          )}
        </div>
        <IconButton icon={X} label="Dismiss" onClick={() => setExtracted(null)} style={{ margin: '-6px -6px 0 0' }} />
      </div>
      {!!extracted.ingredients?.length && (
        <ul style={{ margin: '10px 0 0', paddingLeft: 18, color: 'var(--text)', ...tabular }}>
          {extracted.ingredients.map((ing, i) => (
            <li key={i} style={{ marginBottom: 2 }}>
              {ing.quantity ?? ''} {ing.unit || ''} {ing.name || ''}
            </li>
          ))}
        </ul>
      )}
      {extracted.method && (
        <p style={{ margin: '10px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', whiteSpace: 'pre-wrap' }}>{extracted.method}</p>
      )}
      <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
        <Button variant="primary" onClick={() => fromExtracted(extracted)}>Start a new recipe from this</Button>
        <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          You&apos;ll match each ingredient to a Loaded stock item and unit before it saves.
        </span>
      </div>
    </div>
  );

  // Phones: one-column rows (name, then count · yield), like the other lists.
  const phoneRows = (
    <table className="n-table" style={{ borderTop: '1px solid var(--line)' }}>
      <tbody>
        {visibleRecipes.map((r) => (
          <tr key={r.id} onClick={() => openRecipe(r.id)} style={{ cursor: 'pointer' }}>
            <td style={{ paddingLeft: 4, paddingRight: 4 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, color: 'var(--text)', overflowWrap: 'anywhere' }}>
                    {r.name}
                    {r.prep && <span style={{ marginLeft: 8 }}><Badge>Prep</Badge></span>}
                  </div>
                  <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                    {r.ingredients} ingredient{r.ingredients === 1 ? '' : 's'}{r.yieldText !== '—' ? ` · Yield ${r.yieldText}` : ''}
                  </div>
                </div>
                <Icon icon={ChevronRight} size={16} tone="muted" />
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  const tableRows = (
    <table className="n-table">
      <thead>
        <tr>
          <th>Recipe</th>
          <th className="num" style={{ width: 110 }}>Ingredients</th>
          <th style={{ width: 160 }}>Yield</th>
        </tr>
      </thead>
      <tbody>
        {visibleRecipes.map((r) => (
          <tr key={r.id} onClick={() => openRecipe(r.id)} style={{ cursor: 'pointer' }}>
            <td style={{ fontWeight: 500, color: 'var(--text)' }}>
              {r.name}
              {r.prep && <span style={{ marginLeft: 8 }}><Badge>Prep</Badge></span>}
            </td>
            <td className="num" style={{ color: r.ingredients ? 'var(--text)' : 'var(--muted)' }}>{r.ingredients}</td>
            <td style={{ color: 'var(--text-soft)' }}>{r.yieldText}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  // A failed load reads as the error above, never as "No recipes."
  const listBody = loading ? (
    <PageState kind="loading" title="Loading recipes…" />
  ) : visibleRecipes.length === 0 ? (
    error && recipes.length === 0 ? null : (
      <PageState kind="empty" title={recipes.length === 0 ? 'No recipes.' : `No recipes match “${query}”.`} />
    )
  ) : fit === 'phone' ? phoneRows : tableRows;

  if (isPage) {
    return (
      <div ref={measureRef}>
        <PageHeader title="Recipes" meta={countMeta} actions={listActions}>
          {toolbar}
        </PageHeader>
        {extractedCard}
        {errorBlock}
        {listBody}
      </div>
    );
  }
  return (
    <div ref={measureRef} className="n-card" style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        <div style={{ minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>Recipes</h2>
          <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{countMeta}</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>{listActions}</div>
      </div>
      <div style={{ marginBottom: 12 }}>{toolbar}</div>
      {extractedCard}
      {errorBlock}
      {listBody}
    </div>
  );
}
