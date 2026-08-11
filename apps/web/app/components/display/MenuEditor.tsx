'use client';

/**
 * Menu editor — LoadedHub menus (core-api host).
 *
 * A venue's menus are a list of MenuModels: menu -> sections (groups) -> lines,
 * where each line references a recipe (or a stock item) and carries a sell price
 * (workingPrice). This is the web surface: it reads the list + recipe options
 * through callComponentApi('menu_editor', ...), edits a menu in local state, and
 * saves it back to Loaded via create_menu / update_menu (the same core-api CRUD
 * the roster editor's host uses).
 *
 * Loaded accepts client-generated GUIDs for new menus/sections/lines (Mercury's
 * own editor does the same), so new items get a crypto GUID and are sent as-is.
 *
 * The MCP-embedded (working-document-backed) surface is wired separately; the
 * edit helpers here are the seam it will reuse.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiFetch, callComponentApi } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { colors } from '../../lib/theme';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface MenuLine {
  id: string;
  name: string;
  workingPrice: number;
  recipeId?: string | null;
  stockItemId?: string | null;
  lineOrder?: number;
  salesTaxRateId?: number | null;
  stockUnitRatio?: string | null;
  menuId?: string;
}
interface MenuGroup { id: string; name: string; lines: MenuLine[] }
interface Menu { id: string; name: string; createdAt?: string; deletedAt?: string | null; groups: MenuGroup[] }
interface RecipeOpt { id: string; name: string }
interface VenueOpt { id: string; name: string }

function uid(): string {
  try { return crypto.randomUUID(); } catch { return 'new-' + Math.random().toString(36).slice(2); }
}

export default function MenuEditor({ data, props }: DisplayBlockProps) {
  // Embedded (a Claude card): the block hands us ONE menu in `data` and the
  // venue as a prop, so we open straight into editing it — no list/picker,
  // which the sandbox couldn't populate anyway.
  const embedded = !!props?.embedded;
  const initialMenu =
    data && typeof data === 'object' && Array.isArray((data as { groups?: unknown }).groups)
      ? (data as unknown as Menu)
      : null;

  // --- Venue (page-persistent, mirrors OrdersDashboard) ---
  const persistVenue = !!props?.persistVenue;
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOpt[]>([]);
  const [venueId, setVenueId] = useState<string | null>(
    (props?.activeVenueId as string) || rememberedVenue || null,
  );

  const [menus, setMenus] = useState<Menu[]>([]);
  const [recipes, setRecipes] = useState<RecipeOpt[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);

  // draft === the menu being edited (a local copy); isNewMenu decides create vs update.
  const [draft, setDraft] = useState<Menu | null>(
    embedded && initialMenu ? JSON.parse(JSON.stringify(initialMenu)) : null,
  );
  const [isNewMenu, setIsNewMenu] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiFetch('/api/venues')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d?.venues?.length) {
          setVenues(d.venues);
          if (!venueId) {
            const remembered = rememberedVenue && d.venues.some((v: VenueOpt) => v.id === rememberedVenue) ? rememberedVenue : null;
            setVenueId(remembered || d.venues[0].id);
          }
        }
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const loadMenus = useCallback(async (vid: string) => {
    setLoading(true);
    setError(null);
    try {
      // Recipes drive the line autocomplete on both surfaces. The menu LIST is
      // only for the web picker — the embedded card is already on one menu.
      const rRes = await callComponentApi('menu_editor', 'list_recipes', {}, vid);
      const r = (((rRes?.data as Array<{ id: string; name: string }>) || [])).map((x) => ({ id: x.id, name: x.name }));
      setRecipes(r);
      if (!embedded) {
        const mRes = await callComponentApi('menu_editor', 'list_menus', {}, vid);
        const m = (mRes?.data as Menu[]) || [];
        setMenus(Array.isArray(m) ? m : []);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load menus');
    }
    setLoading(false);
  }, [embedded]);

  useEffect(() => { if (venueId) loadMenus(venueId); }, [venueId, loadMenus]);

  const recipeName = useMemo(() => {
    const m = new Map(recipes.map((r) => [r.id, r.name]));
    return (id?: string | null) => (id ? m.get(id) || '' : '');
  }, [recipes]);

  const changeVenue = (vid: string) => {
    setVenueId(vid);
    if (persistVenue) setActiveVenue(vid);
    setDraft(null);
  };

  // --- Draft lifecycle ---
  const editMenu = (menu: Menu) => {
    setDraft(JSON.parse(JSON.stringify(menu)));
    setIsNewMenu(false);
    setSavedNote(null);
    setError(null);
  };
  const newMenu = () => {
    setDraft({ id: uid(), name: '', groups: [] });
    setIsNewMenu(true);
    setSavedNote(null);
    setError(null);
  };
  const closeEditor = () => { setDraft(null); };

  // --- Draft mutations (the seam the MCP working-doc path will reuse) ---
  const setName = (name: string) => setDraft((d) => (d ? { ...d, name } : d));
  const addSection = () => setDraft((d) => (d ? { ...d, groups: [...d.groups, { id: uid(), name: 'New section', lines: [] }] } : d));
  const renameSection = (gid: string, name: string) => setDraft((d) => (d ? { ...d, groups: d.groups.map((g) => (g.id === gid ? { ...g, name } : g)) } : d));
  const removeSection = (gid: string) => setDraft((d) => (d ? { ...d, groups: d.groups.filter((g) => g.id !== gid) } : d));
  const addLine = (gid: string) => setDraft((d) => (d ? {
    ...d,
    groups: d.groups.map((g) => (g.id === gid ? { ...g, lines: [...g.lines, { id: uid(), name: '', workingPrice: 0, recipeId: null, lineOrder: g.lines.length }] } : g)),
  } : d));
  const updateLine = (gid: string, lid: string, patch: Partial<MenuLine>) => setDraft((d) => (d ? {
    ...d,
    groups: d.groups.map((g) => (g.id === gid ? { ...g, lines: g.lines.map((ln) => (ln.id === lid ? { ...ln, ...patch } : ln)) } : g)),
  } : d));
  const removeLine = (gid: string, lid: string) => setDraft((d) => (d ? {
    ...d,
    groups: d.groups.map((g) => (g.id === gid ? { ...g, lines: g.lines.filter((ln) => ln.id !== lid) } : g)),
  } : d));

  const onPickRecipe = (gid: string, lid: string, typed: string) => {
    const hit = recipes.find((r) => r.name === typed);
    if (hit) updateLine(gid, lid, { recipeId: hit.id, stockItemId: null, name: hit.name });
    else updateLine(gid, lid, { name: typed });
  };

  const save = async () => {
    if (!draft || !venueId) return;
    setSaving(true);
    setError(null);
    setSavedNote(null);
    const payload = {
      id: draft.id,
      name: draft.name,
      groups: draft.groups.map((g) => ({
        id: g.id,
        name: g.name,
        lines: g.lines.map((ln, li) => ({
          id: ln.id,
          name: ln.name,
          workingPrice: Number(ln.workingPrice) || 0,
          recipeId: ln.recipeId || null,
          stockItemId: ln.stockItemId || null,
          lineOrder: li,
          salesTaxRateId: ln.salesTaxRateId ?? null,
        })),
      })),
    };
    try {
      const action = isNewMenu ? 'create_menu' : 'update_menu';
      const res = await callComponentApi('menu_editor', action, payload, venueId);
      const sc = (res as { status_code?: number })?.status_code;
      if ((res as { error?: boolean })?.error || (sc && sc >= 400)) {
        throw new Error(`Loaded rejected the save${sc ? ` (${sc})` : ''}`);
      }
      setSavedNote(isNewMenu ? 'Menu created in Loaded.' : 'Menu saved to Loaded.');
      // On the web we drop back to the refreshed list; the embedded card has no
      // list to return to, so it stays on the (now saved) menu.
      if (!embedded) {
        await loadMenus(venueId);
        closeEditor();
      }
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
      <span style={{ fontSize: '0.95rem', fontWeight: 700, color: colors.executive_chef }}>Menus</span>
      {venues.length > 1 && (
        <select value={venueId || ''} onChange={(e) => changeVenue(e.target.value)} style={input}>
          {venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
      )}
      {savedNote && <span style={{ fontSize: '0.8rem', color: colors.success }}>{savedNote}</span>}
      {error && <span style={{ fontSize: '0.8rem', color: '#e53e3e' }}>{error}</span>}
    </div>
  );

  // --- Editor view ---
  if (draft) {
    return (
      <div style={{ maxWidth: 760 }}>
        {venueBar}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <input
            value={draft.name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Menu name"
            style={{ ...input, fontSize: '1rem', fontWeight: 600, flex: 1 }}
          />
          <button onClick={save} disabled={saving || !draft.name.trim()} style={btn(colors.executive_chef)}>
            {saving ? 'Saving…' : isNewMenu ? 'Create in Loaded' : 'Save to Loaded'}
          </button>
          {!embedded && <button onClick={closeEditor} disabled={saving} style={ghost}>Cancel</button>}
        </div>

        {draft.groups.map((g) => (
          <div key={g.id} style={{ border: `1px solid ${colors.border}`, borderRadius: 8, marginBottom: '0.75rem', overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', padding: '0.4rem 0.6rem', background: colors.selectedBg }}>
              <input value={g.name} onChange={(e) => renameSection(g.id, e.target.value)} style={{ ...input, fontWeight: 600, flex: 1, background: '#fff' }} />
              <button onClick={() => removeSection(g.id)} style={ghost} title="Remove section">✕</button>
            </div>
            <div style={{ padding: '0.4rem 0.6rem' }}>
              {g.lines.map((ln) => (
                <div key={ln.id} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.35rem' }}>
                  <input
                    list={`recipes-${venueId}`}
                    value={ln.recipeId ? recipeName(ln.recipeId) : ln.name}
                    onChange={(e) => onPickRecipe(g.id, ln.id, e.target.value)}
                    placeholder="Recipe or dish name"
                    style={{ ...input, flex: 1 }}
                  />
                  <span style={{ color: colors.textMuted, fontSize: '0.8rem' }}>$</span>
                  <input
                    type="number" step="0.01" min="0"
                    value={ln.workingPrice}
                    onChange={(e) => updateLine(g.id, ln.id, { workingPrice: parseFloat(e.target.value) })}
                    style={{ ...input, width: 90, textAlign: 'right' }}
                  />
                  <button onClick={() => removeLine(g.id, ln.id)} style={ghost} title="Remove line">✕</button>
                </div>
              ))}
              <button onClick={() => addLine(g.id)} style={{ ...ghost, marginTop: 4 }}>+ Add line</button>
            </div>
          </div>
        ))}

        <button onClick={addSection} style={ghost}>+ Add section</button>

        <datalist id={`recipes-${venueId}`}>
          {recipes.map((r) => <option key={r.id} value={r.name} />)}
        </datalist>
      </div>
    );
  }

  // --- List view ---
  return (
    <div style={{ maxWidth: 760 }}>
      {venueBar}
      <div style={{ marginBottom: '0.75rem' }}>
        <button onClick={newMenu} style={btn(colors.executive_chef)} disabled={!venueId}>+ New menu</button>
      </div>
      {loading && <div style={{ color: colors.textMuted, padding: '1rem 0' }}>Loading…</div>}
      {!loading && menus.length === 0 && <div style={{ color: colors.textMuted, padding: '1rem 0' }}>No menus yet.</div>}
      {menus.map((m) => {
        const lineCount = m.groups.reduce((n, g) => n + g.lines.length, 0);
        return (
          <button
            key={m.id}
            onClick={() => editMenu(m)}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%',
              padding: '0.6rem 0.8rem', marginBottom: '0.4rem', textAlign: 'left',
              border: `1px solid ${colors.border}`, borderRadius: 8, background: '#fff', cursor: 'pointer', fontFamily: 'inherit',
            }}
          >
            <span style={{ fontWeight: 600, color: colors.textPrimary }}>{m.name}</span>
            <span style={{ fontSize: '0.78rem', color: colors.textMuted }}>
              {m.groups.length} section{m.groups.length === 1 ? '' : 's'} · {lineCount} line{lineCount === 1 ? '' : 's'}
            </span>
          </button>
        );
      })}
    </div>
  );
}
