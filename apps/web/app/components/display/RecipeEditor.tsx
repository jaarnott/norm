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

import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiFetch, callComponentApi } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { colors } from '../../lib/theme';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface Unit { id: string; name: string; ratio: number }
interface Opt { id: string; name: string; kind: 'item' | 'recipe' }
interface EditLine {
  key: string;
  kind: 'item' | 'recipe';
  ref_id: string | null;
  name: string;
  unit_id: string | null;
  unit_name: string | null;
  unit_ratio: number;
  quantity: number;
}
interface Draft {
  recipe_id: string;
  version_id: string;
  name: string;
  is_counted_in_stocktake: boolean;
  yield_quantity: number;
  yield_unit_id: string | null;
  lines: EditLine[];
}
interface VenueOpt { id: string; name: string }

function uid(): string {
  try { return crypto.randomUUID(); } catch { return 'k' + Math.random().toString(36).slice(2); }
}
const num = (v: unknown) => (typeof v === 'number' ? v : parseFloat(String(v)) || 0);

// A Loaded recipe payload (from get_recipe) -> editable Draft (display units).
function toDraft(r: Record<string, unknown>): Draft | null {
  const cv = (r.currentVersion as Record<string, unknown>) || null;
  if (!cv) return null;
  const yr = num(cv.yieldUnitRatio) || 1;
  const rawLines = (cv.lines as Array<Record<string, unknown>>) || [];
  const lines: EditLine[] = rawLines
    .filter((l) => !l.deletedAt)
    .map((l) => {
      const ratio = num(l.unitRatio) || 1;
      const isItem = !!l.itemId;
      return {
        key: uid(),
        kind: isItem ? 'item' : 'recipe',
        ref_id: (l.itemId as string) || (l.recipeId as string) || null,
        name: (l.itemName as string) || (l.recipeName as string) || '',
        unit_id: (l.unitId as string) || null,
        unit_name: (l.unitName as string) || null,
        unit_ratio: ratio,
        quantity: num(l.quantity) / ratio,
      };
    });
  return {
    recipe_id: r.id as string,
    version_id: cv.id as string,
    name: (r.name as string) || '',
    is_counted_in_stocktake: !!r.isCountedInStocktake,
    yield_quantity: num(cv.yieldQuantity) / yr,
    yield_unit_id: (cv.yieldUnitId as string) || null,
    lines,
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

  const [recipes, setRecipes] = useState<{ id: string; name: string }[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [opts, setOpts] = useState<Opt[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(
    embedded && initialRecipe ? toDraft(initialRecipe) : null,
  );

  // Upload → extract a draft recipe from a document (web-only; review-only —
  // see the note in the card. Saving it as a NEW Loaded recipe needs a create
  // path that doesn't exist yet).
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
      const rl = ((rRes?.data as Array<{ id: string; name: string; deletedAt?: unknown }>) || []).filter((x) => !x.deletedAt);
      if (!embedded) setRecipes(rl.map((x) => ({ id: x.id, name: x.name })));
      setUnits(((uRes?.data as Array<{ id: string; name: string; ratio: number; datestampDeleted?: unknown }>) || [])
        .filter((u) => !u.datestampDeleted)
        .map((u) => ({ id: u.id, name: u.name, ratio: num(u.ratio) })));
      const items = ((iRes?.data as Array<{ id: string; name: string; datestampDeleted?: unknown }>) || [])
        .filter((i) => !i.datestampDeleted)
        .map((i) => ({ id: i.id, name: (i.name || '').trim(), kind: 'item' as const }));
      const subs = rl.map((x) => ({ id: x.id, name: x.name, kind: 'recipe' as const }));
      setOpts([...subs, ...items]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load recipes');
    }
    setLoading(false);
  }, [embedded]);

  useEffect(() => { if (venueId) loadRefs(venueId); }, [venueId, loadRefs]);

  const unitById = useMemo(() => new Map(units.map((u) => [u.id, u])), [units]);
  const optByName = useMemo(() => new Map(opts.map((o) => [o.name, o])), [opts]);

  const changeVenue = (vid: string) => {
    setVenueId(vid);
    if (persistVenue) setActiveVenue(vid);
    setDraft(null);
  };

  const openRecipe = async (id: string) => {
    setError(null);
    setSavedNote(null);
    try {
      const res = await callComponentApi('recipe_editor', 'get_recipe', { recipe_id: id }, venueId || undefined);
      const d = toDraft((res?.data as Record<string, unknown>) || {});
      if (!d) { setError('This recipe has no editable version.'); return; }
      setDraft(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load recipe');
    }
  };
  const closeEditor = () => setDraft(null);

  // --- Draft mutations ---
  const setName = (name: string) => setDraft((d) => (d ? { ...d, name } : d));
  const setYieldQty = (q: number) => setDraft((d) => (d ? { ...d, yield_quantity: q } : d));
  const setYieldUnit = (unitId: string) => setDraft((d) => (d ? { ...d, yield_unit_id: unitId } : d));
  const addLine = () => setDraft((d) => (d ? { ...d, lines: [...d.lines, { key: uid(), kind: 'item', ref_id: null, name: '', unit_id: null, unit_name: null, unit_ratio: 1, quantity: 0 }] } : d));
  const removeLine = (key: string) => setDraft((d) => (d ? { ...d, lines: d.lines.filter((l) => l.key !== key) } : d));
  const updateLine = (key: string, patch: Partial<EditLine>) => setDraft((d) => (d ? { ...d, lines: d.lines.map((l) => (l.key === key ? { ...l, ...patch } : l)) } : d));

  const pickIngredient = (key: string, typed: string) => {
    const hit = optByName.get(typed);
    if (hit) updateLine(key, { kind: hit.kind, ref_id: hit.id, name: hit.name });
    else updateLine(key, { name: typed });
  };
  const pickUnit = (key: string, unitId: string) => {
    const u = unitById.get(unitId);
    updateLine(key, { unit_id: unitId, unit_name: u?.name ?? null, unit_ratio: u?.ratio ?? 1 });
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
    const recipe = {
      recipe_id: draft.recipe_id,
      version_id: draft.version_id,
      name: draft.name,
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
    try {
      const res = await apiFetch('/api/recipe-editor/save', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, recipe }),
      });
      if (!res.ok) {
        const t = await res.json().catch(() => ({}));
        throw new Error((t as { detail?: string }).detail || `Save failed (${res.status})`);
      }
      setSavedNote('Recipe saved to Loaded.');
      if (!embedded) closeEditor();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    }
    setSaving(false);
  };

  // --- Styles ---
  const input: React.CSSProperties = { padding: '5px 8px', fontSize: '0.85rem', border: `1px solid ${colors.border}`, borderRadius: 6, fontFamily: 'inherit' };
  const btn = (bg: string, fg = '#fff'): React.CSSProperties => ({ padding: '5px 12px', fontSize: '0.8rem', fontWeight: 600, border: 'none', borderRadius: 6, background: bg, color: fg, cursor: 'pointer', fontFamily: 'inherit' });
  const ghost: React.CSSProperties = { ...btn('#fff', colors.textSecondary), border: `1px solid ${colors.border}` };

  const venueBar = (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
      <span style={{ fontSize: '0.95rem', fontWeight: 700, color: colors.executive_chef }}>Recipes</span>
      {venues.length > 1 && (
        <select value={venueId || ''} onChange={(e) => changeVenue(e.target.value)} style={input}>
          {venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
      )}
      {savedNote && <span style={{ fontSize: '0.8rem', color: colors.success }}>{savedNote}</span>}
      {error && <span style={{ fontSize: '0.8rem', color: '#e53e3e' }}>{error}</span>}
    </div>
  );

  const unitSelect = (value: string | null, onChange: (id: string) => void) => (
    <select value={value || ''} onChange={(e) => onChange(e.target.value)} style={{ ...input, width: 120 }}>
      {!value && <option value="">unit</option>}
      {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
    </select>
  );

  if (draft) {
    return (
      <div style={{ maxWidth: 820 }}>
        {venueBar}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.6rem', flexWrap: 'wrap' }}>
          <input value={draft.name} onChange={(e) => setName(e.target.value)} placeholder="Recipe name" style={{ ...input, fontSize: '1rem', fontWeight: 600, flex: 1, minWidth: 220 }} />
          <button onClick={save} disabled={saving || !draft.name.trim()} style={btn(colors.executive_chef)}>{saving ? 'Saving…' : 'Save to Loaded'}</button>
          {!embedded && <button onClick={closeEditor} disabled={saving} style={ghost}>Cancel</button>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.75rem' }}>
          <span style={{ fontSize: '0.8rem', color: colors.textMuted }}>Yield</span>
          <input type="number" step="0.01" min="0" value={draft.yield_quantity} onChange={(e) => setYieldQty(parseFloat(e.target.value))} style={{ ...input, width: 90, textAlign: 'right' }} />
          {unitSelect(draft.yield_unit_id, setYieldUnit)}
        </div>

        <div style={{ border: `1px solid ${colors.border}`, borderRadius: 8, overflow: 'hidden' }}>
          <div style={{ display: 'flex', gap: '0.4rem', padding: '0.35rem 0.6rem', background: colors.selectedBg, fontSize: '0.72rem', color: colors.textMuted, fontWeight: 600 }}>
            <span style={{ flex: 1 }}>Ingredient</span><span style={{ width: 90, textAlign: 'right' }}>Qty</span><span style={{ width: 120 }}>Unit</span><span style={{ width: 24 }} />
          </div>
          <div style={{ padding: '0.4rem 0.6rem' }}>
            {draft.lines.map((l) => (
              <div key={l.key} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.35rem' }}>
                <input list={`ings-${venueId}`} value={l.name} onChange={(e) => pickIngredient(l.key, e.target.value)} placeholder="Ingredient or sub-recipe" style={{ ...input, flex: 1 }} />
                <input type="number" step="0.001" min="0" value={l.quantity} onChange={(e) => updateLine(l.key, { quantity: parseFloat(e.target.value) })} style={{ ...input, width: 90, textAlign: 'right' }} />
                {unitSelect(l.unit_id, (u) => pickUnit(l.key, u))}
                <button onClick={() => removeLine(l.key)} style={ghost} title="Remove">✕</button>
              </div>
            ))}
            <button onClick={addLine} style={{ ...ghost, marginTop: 4 }}>+ Add ingredient</button>
          </div>
        </div>

        <datalist id={`ings-${venueId}`}>
          {opts.map((o) => <option key={`${o.kind}-${o.id}`} value={o.name} />)}
        </datalist>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 820 }}>
      {venueBar}

      {/* Upload a recipe document → extract a structured draft. Web-only: the
          MCP iframe has no multipart upload. Review-only for now — see note. */}
      {!embedded && (
        <div style={{ margin: '0.25rem 0 0.9rem' }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', padding: '0.5rem 0.85rem', border: `1px dashed ${colors.border}`, borderRadius: 8, background: '#fff', cursor: extracting ? 'default' : 'pointer', fontWeight: 600, color: colors.textPrimary, opacity: extracting ? 0.6 : 1 }}>
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
          <div style={{ marginTop: '0.7rem', color: colors.textMuted, fontSize: '0.78rem', fontStyle: 'italic' }}>
            Extracted for review. Creating a new Loaded recipe from a document isn&apos;t available yet — copy these into an existing recipe above.
          </div>
        </div>
      )}

      {error && !draft && <div style={{ color: colors.error, padding: '0.4rem 0' }}>{error}</div>}
      {loading && <div style={{ color: colors.textMuted, padding: '1rem 0' }}>Loading…</div>}
      {!loading && recipes.length === 0 && <div style={{ color: colors.textMuted, padding: '1rem 0' }}>No recipes.</div>}
      {recipes.map((r) => (
        <button key={r.id} onClick={() => openRecipe(r.id)} style={{ display: 'block', width: '100%', padding: '0.55rem 0.8rem', marginBottom: '0.35rem', textAlign: 'left', border: `1px solid ${colors.border}`, borderRadius: 8, background: '#fff', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 600, color: colors.textPrimary }}>
          {r.name}
        </button>
      ))}
    </div>
  );
}
