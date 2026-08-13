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
import Combobox, { type ComboOption } from './Combobox';
import { recipeCost, type CostTables } from './recipeCost';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

const num = (v: unknown): number => (typeof v === 'number' ? v : parseFloat(String(v)) || 0);
const money = (n: number): string => `$${n.toFixed(2)}`;

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
interface RawMenuRecipe {
  id: string;
  name?: string;
  deletedAt?: unknown;
  currentVersion?: {
    yieldQuantity?: unknown;
    yieldUnitRatio?: unknown;
    lines?: Array<{ itemId?: string | null; recipeId?: string | null; quantity?: unknown; unitRatio?: unknown; unitId?: string | null; deletedAt?: unknown }>;
  } | null;
}

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
  const [query, setQuery] = useState('');
  const [recipes, setRecipes] = useState<RecipeOpt[]>([]);
  const [costTables, setCostTables] = useState<CostTables | null>(null);
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
      // Recipes drive the line autocomplete AND the cost of each dish; prices +
      // units feed the cost engine (see recipeCost.ts). The menu LIST is only for
      // the web picker — the embedded card is already on one menu.
      const [rRes, iRes, uRes] = await Promise.all([
        callComponentApi('menu_editor', 'list_recipes', {}, vid),
        callComponentApi('menu_editor', 'list_stock_items', {}, vid),
        callComponentApi('menu_editor', 'list_units', {}, vid),
      ]);
      const rawRecipes = ((rRes?.data as Array<RawMenuRecipe>) || []).filter((x) => !x.deletedAt);
      setRecipes(rawRecipes.map((x) => ({ id: x.id, name: (x.name || '').trim() })));

      // Cost from all items (incl. removed, which keep a last-known price); the
      // picker isn't shown here, so no active-only filter is needed.
      const allItems = (iRes?.data as Array<{ id: string; currentPrice?: unknown; countingUnitId?: string }>) || [];
      const rawUnits = ((uRes?.data as Array<{ id: string; ratio?: unknown; stockUnitType?: string; datestampDeleted?: unknown }>) || [])
        .filter((u) => !u.datestampDeleted);
      const recipeMap = new Map(rawRecipes.map((x) => {
        const cv = x.currentVersion || {};
        return [x.id, {
          yieldQuantity: num(cv.yieldQuantity),
          yieldUnitRatio: num(cv.yieldUnitRatio) || 1,
          lines: (cv.lines || []).map((l) => ({ itemId: l.itemId, recipeId: l.recipeId, quantity: num(l.quantity), unitRatio: num(l.unitRatio) || 1, unitId: l.unitId, deletedAt: l.deletedAt })),
        }] as const;
      }));
      const itemMap = new Map(allItems.map((i) => [i.id, { currentPrice: num(i.currentPrice), countingUnitId: i.countingUnitId }] as const));
      const unitTypeMap = new Map(rawUnits.filter((u) => u.stockUnitType).map((u) => [u.id, u.stockUnitType as string] as const));
      setCostTables({ recipes: recipeMap, items: itemMap, unitType: unitTypeMap });

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
  const recipeOptions: ComboOption[] = useMemo(
    () => recipes.map((r) => ({ id: r.id, name: r.name })),
    [recipes],
  );
  // A dish's food cost = the linked recipe's cost. Food-cost % is that against the
  // sell price. Green ≤30%, amber ≤40%, red above — the usual kitchen bands.
  const dishCost = (recipeId?: string | null) => (recipeId && costTables ? recipeCost(recipeId, costTables) : null);
  const fcColor = (pct: number): string => (pct <= 30 ? colors.success : pct <= 40 ? colors.warning : colors.error);
  // Menus list: filter by search, sort alphabetically.
  const visibleMenus = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q ? menus.filter((m) => (m.name || '').toLowerCase().includes(q)) : menus;
    return [...filtered].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  }, [menus, query]);

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

  // --- Editor view ---
  if (draft) {
    return (
      <div style={{ maxWidth: 760 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
          {!embedded && <button onClick={closeEditor} disabled={saving} style={ghost}>← Menus</button>}
          <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: colors.textPrimary }}>{isNewMenu ? 'New menu' : 'Edit menu'}</h2>
          {venueSelect}
          {notes}
        </div>
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
        </div>

        {draft.groups.map((g) => (
          <div key={g.id} style={{ border: `1px solid ${colors.border}`, borderRadius: 8, marginBottom: '0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', padding: '0.4rem 0.6rem', background: colors.selectedBg, borderTopLeftRadius: 7, borderTopRightRadius: 7 }}>
              <input value={g.name} onChange={(e) => renameSection(g.id, e.target.value)} style={{ ...input, fontWeight: 600, flex: 1, background: '#fff' }} />
              <button onClick={() => removeSection(g.id)} style={{ ...ghost, width: 28, padding: '5px 0', textAlign: 'center' }} title="Remove section">✕</button>
            </div>
            <div style={{ padding: '0.5rem 0.6rem' }}>
              {g.lines.length === 0 && (
                <div style={{ fontSize: '0.8rem', color: colors.textMuted, padding: '0.15rem 0 0.5rem' }}>No lines yet — add one below.</div>
              )}
              {g.lines.map((ln) => {
                const c = dishCost(ln.recipeId);
                const price = Number(ln.workingPrice) || 0;
                const fc = c && c.complete && price > 0 ? (c.cost / price) * 100 : null;
                return (
                  <div key={ln.id} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.4rem' }}>
                    <div style={{ flex: 1 }}>
                      <Combobox
                        value={ln.recipeId ? recipeName(ln.recipeId) : ln.name}
                        options={recipeOptions}
                        onType={(t) => updateLine(g.id, ln.id, { name: t, recipeId: null })}
                        onPick={(o) => updateLine(g.id, ln.id, { recipeId: o.id, stockItemId: null, name: o.name })}
                        placeholder="Search recipe or type a dish name"
                      />
                    </div>
                    <span style={{ color: colors.textMuted, fontSize: '0.8rem' }}>$</span>
                    <input
                      type="number" step="0.01" min="0"
                      value={ln.workingPrice}
                      onChange={(e) => updateLine(g.id, ln.id, { workingPrice: parseFloat(e.target.value) })}
                      style={{ ...input, width: 84, textAlign: 'right' }}
                    />
                    <div style={{ width: 96, textAlign: 'right', fontSize: '0.74rem', lineHeight: 1.25 }} title={!ln.recipeId ? 'Link a recipe to cost this dish' : c?.complete ? 'Linked recipe cost vs sell price' : 'The linked recipe has unpriced ingredients — cost incomplete'}>
                      {c?.complete ? (
                        <>
                          <div style={{ color: colors.textPrimary }}>{money(c.cost)}</div>
                          {fc != null && <div style={{ color: fcColor(fc), fontWeight: 600 }}>{fc.toFixed(0)}% FC</div>}
                        </>
                      ) : (
                        <span style={{ color: colors.textMuted }}>—</span>
                      )}
                    </div>
                    <button onClick={() => removeLine(g.id, ln.id)} style={{ ...ghost, width: 28, padding: '5px 0', textAlign: 'center' }} title="Remove line">✕</button>
                  </div>
                );
              })}
              <button onClick={() => addLine(g.id)} style={{ ...ghost, marginTop: 4 }}>+ Add line</button>
            </div>
          </div>
        ))}

        <button onClick={addSection} style={ghost}>+ Add section</button>
      </div>
    );
  }

  // --- List view ---
  return (
    <div>
      {/* Header — mirrors OrdersDashboard: title + count + venue + New button */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem', flexWrap: 'wrap', gap: '0.5rem' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: colors.textPrimary }}>Menus</h2>
          <span style={{ fontSize: '0.75rem', color: colors.textMuted }}>
            {loading ? 'Loading…' : `${visibleMenus.length}${query ? ` of ${menus.length}` : ''} menu${menus.length === 1 ? '' : 's'}`}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          {notes}
          {venueSelect}
          <button onClick={newMenu} disabled={!venueId} style={{ ...btn(colors.executive_chef), background: venueId ? colors.executive_chef : '#ccc', cursor: venueId ? 'pointer' : 'default' }}>+ New menu</button>
        </div>
      </div>

      {menus.length > 3 && (
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search menus…"
          style={{ ...input, width: '100%', maxWidth: 320, marginBottom: '0.75rem', boxSizing: 'border-box' }}
        />
      )}

      {loading ? (
        <div style={{ padding: '2rem', textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>Loading menus…</div>
      ) : visibleMenus.length === 0 ? (
        <div style={{ padding: '2rem', textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>
          {menus.length === 0 ? 'No menus yet.' : `No menus match “${query}”.`}
        </div>
      ) : (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 420 }}>
            <thead>
              <tr>
                <th style={thStyle}>Menu</th>
                <th style={{ ...thStyle, textAlign: 'right', width: 90 }}>Sections</th>
                <th style={{ ...thStyle, textAlign: 'right', width: 90 }}>Lines</th>
              </tr>
            </thead>
            <tbody>
              {visibleMenus.map((m) => {
                const lineCount = m.groups.reduce((n, g) => n + g.lines.length, 0);
                return (
                  <tr
                    key={m.id}
                    onClick={() => editMenu(m)}
                    style={{ cursor: 'pointer' }}
                    onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = colors.pageBg; }}
                    onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = ''; }}
                  >
                    <td style={{ ...tdStyle, fontWeight: 600 }}>{m.name || 'Untitled menu'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: colors.textSecondary }}>{m.groups.length}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: lineCount ? colors.textPrimary : colors.textMuted }}>{lineCount}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
