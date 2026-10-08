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

import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { Check, ChevronRight, Plus, Search, Trash2, X } from 'lucide-react';
import { apiFetch, callComponentApi } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import Combobox, { type ComboOption } from './Combobox';
import { recipeCost, costRecipeFromVersion, type CostTables } from './recipeCost';
import { formatMoney } from '../../lib/format';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import BackLink from '../ui/BackLink';
import Icon from '../ui/Icon';
import Badge, { type BadgeTone } from '../ui/Badge';

const num = (v: unknown): number => (typeof v === 'number' ? v : parseFloat(String(v)) || 0);
const money = (n: number): string => formatMoney(n);

// --- Layout ---
// Below this width (a phone, a narrow chat card) a dish's price and cost fold
// under its name, so a line fits without scrolling sideways.
const COMPACT_BELOW = 680;
// A dish line on a wide editor: Dish · Price · Cost · Food cost · remove.
const LINE_GRID: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'minmax(0, 1fr) 104px 84px 80px 40px',
  columnGap: 12,
  alignItems: 'center',
};
/** Column label over the dish lines: the .n-table header type, without the fill. */
const COL_LABEL: CSSProperties = { fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)', whiteSpace: 'nowrap' };
/** An icon or "$" sitting inside the left edge of an .n-input. */
const FIELD_ADORN: CSSProperties = { position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' };
/** A menu's name in the list: plain text that is a button, for keyboard reach. */
const ROW_LINK: CSSProperties = {
  padding: 0, border: 'none', background: 'none', cursor: 'pointer', textAlign: 'left',
  fontFamily: 'inherit', fontSize: 'inherit', lineHeight: 'inherit', fontWeight: 500, color: 'var(--text)',
};

/** The header inside a conversation or a Claude card: compact, no page title. */
function CardHeader({ title, meta, back, status, actions }: {
  title: string;
  meta?: ReactNode;
  back?: { label: string; onClick: () => void; disabled?: boolean };
  status?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div style={{ padding: '14px 16px 12px' }}>
      {back && (
        <div style={{ marginBottom: 6 }}>
          <BackLink label={back.label} onClick={back.onClick} disabled={back.disabled} />
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.35, color: 'var(--text)' }}>{title}</div>
            {status}
          </div>
          {meta && <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta}</div>}
        </div>
        {actions && <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>{actions}</div>}
      </div>
    </div>
  );
}

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

  // Layout only: the editor's own width picks the compact dish lines (see
  // COMPACT_BELOW) — a phone, or a narrow card in a conversation or Claude.
  const nameId = useId();
  const editorRef = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(() => typeof window !== 'undefined' && window.innerWidth < COMPACT_BELOW);
  const editing = draft !== null;
  useEffect(() => {
    const el = editorRef.current;
    if (!editing || !el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setCompact(w < COMPACT_BELOW);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [editing]);

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
      // costRecipeFromVersion converts Loaded's base-unit quantities/yield to the
      // engine's display convention — without it sub-recipe costs are ~1000x off.
      const recipeMap = new Map(rawRecipes.map((x) => [x.id, costRecipeFromVersion(x.currentVersion || {})] as const));
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
  const fcTone = (pct: number): BadgeTone => (pct <= 30 ? 'ok' : pct <= 40 ? 'warn' : 'error');
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
  // A save error belongs to the editor; leaving it must not make the list
  // read as "Couldn't load menus".
  const closeEditor = () => { setDraft(null); setError(null); };

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

  // --- Presentation ---
  // A PAGE (Executive chef › Menus) draws the page header and sits straight on
  // the cream page; in a conversation or inside Claude it is one compact card.
  const isPage = persistVenue && !embedded;
  const venuePicker = venues.length > 1 ? <VenueSelect venues={venues} value={venueId} onChange={changeVenue} /> : null;
  const savedStatus = savedNote ? (
    <span role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--ok)' }}>
      <Icon icon={Check} size="dense" />
      {savedNote}
    </span>
  ) : null;

  // --- Editor view ---
  if (draft) {
    const title = isNewMenu ? 'New menu' : 'Edit menu';
    const lineTotal = draft.groups.reduce((n, g) => n + g.lines.length, 0);
    const meta = `${draft.groups.length} section${draft.groups.length === 1 ? '' : 's'} · ${lineTotal} line${lineTotal === 1 ? '' : 's'}`;
    // Back does nothing while a save is in flight (it was a disabled button).
    const back = embedded ? undefined : { label: 'Menus', onClick: closeEditor, disabled: saving };
    const saveButton = (
      <Button variant="primary" onClick={save} disabled={saving || !draft.name.trim()}>
        {saving ? 'Saving…' : isNewMenu ? 'Create in Loaded' : 'Save to Loaded'}
      </Button>
    );
    // Narrow: the venue picker and Save share one full-width row under the
    // title (as on the phone mock) instead of wrapping onto two.
    const sharedRow = compact && venuePicker ? (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ display: 'flex', flex: '1 1 auto', minWidth: 0 }}>{venuePicker}</div>
        {saveButton}
      </div>
    ) : null;
    const actions = sharedRow ? undefined : <>{venuePicker}{saveButton}</>;
    const pad = compact ? 12 : 16; // a section's inner gutter
    const rowRule: CSSProperties = { borderBottom: '1px solid var(--line)' };

    const body = (
      <>
        {error && (
          <div style={{ marginBottom: 12 }}>
            <PageState kind="error" title={error} />
          </div>
        )}
        <div style={{ marginBottom: 16 }}>
          <label className="n-label" htmlFor={nameId}>Menu name</label>
          <input
            id={nameId}
            className="n-input"
            value={draft.name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Menu name"
            style={{ width: '100%', maxWidth: 560, fontSize: 'var(--fs-md)', fontWeight: 600 }}
          />
        </div>

        {draft.groups.map((g) => (
          <div
            key={g.id}
            className={isPage ? 'n-card' : undefined}
            style={isPage
              ? { marginBottom: 12 }
              : { marginBottom: 12, border: '1px solid var(--line)', borderRadius: 'var(--radius)', background: 'var(--bg)' }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: `10px ${pad - 4}px 10px ${pad}px`, ...rowRule }}>
              <input
                className="n-input"
                aria-label="Section name"
                value={g.name}
                onChange={(e) => renameSection(g.id, e.target.value)}
                style={{ flex: '1 1 auto', minWidth: 0, maxWidth: 480, fontWeight: 600 }}
              />
              <IconButton icon={Trash2} label="Remove section" onClick={() => removeSection(g.id)} style={{ marginLeft: 'auto' }} />
            </div>
            <div style={{ padding: `0 ${pad}px 12px` }}>
              {g.lines.length === 0 ? (
                <div style={{ padding: '12px 0 4px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No lines yet — add one below.</div>
              ) : !compact && (
                <div aria-hidden="true" style={{ ...LINE_GRID, padding: '10px 0 6px', borderBottom: '1px solid var(--line-strong)' }}>
                  <span style={COL_LABEL}>Dish</span>
                  <span style={{ ...COL_LABEL, textAlign: 'right' }}>Price</span>
                  <span style={{ ...COL_LABEL, textAlign: 'right' }}>Cost</span>
                  <span style={{ ...COL_LABEL, textAlign: 'right' }}>Food cost</span>
                  <span />
                </div>
              )}
              {g.lines.map((ln) => {
                const c = dishCost(ln.recipeId);
                const price = Number(ln.workingPrice) || 0;
                const fc = c && c.complete && price > 0 ? (c.cost / price) * 100 : null;
                const costTitle = !ln.recipeId ? 'Link a recipe to cost this dish' : c?.complete ? 'Linked recipe cost vs sell price' : 'The linked recipe has unpriced ingredients — cost incomplete';
                const cost = c?.complete
                  ? <span style={{ color: 'var(--text)' }}>{money(c.cost)}</span>
                  : <span style={{ color: 'var(--muted)' }}>—</span>;
                const fcBadge = fc != null ? <Badge tone={fcTone(fc)}>{fc.toFixed(0)}% FC</Badge> : null;
                const dishField = (
                  <Combobox
                    value={ln.recipeId ? recipeName(ln.recipeId) : ln.name}
                    options={recipeOptions}
                    onType={(t) => updateLine(g.id, ln.id, { name: t, recipeId: null })}
                    onPick={(o) => updateLine(g.id, ln.id, { recipeId: o.id, stockItemId: null, name: o.name })}
                    placeholder="Search recipe or type a dish name"
                  />
                );
                const priceField = (
                  <span style={{ position: 'relative', display: 'block' }}>
                    <span aria-hidden="true" style={{ ...FIELD_ADORN, fontSize: 'var(--fs-base)', color: 'var(--muted)' }}>$</span>
                    <input
                      type="number" step="0.01" min="0"
                      value={ln.workingPrice}
                      onChange={(e) => updateLine(g.id, ln.id, { workingPrice: parseFloat(e.target.value) })}
                      aria-label={`Price of ${(ln.recipeId ? recipeName(ln.recipeId) : ln.name) || 'this dish'}`}
                      className="n-input"
                      style={{ width: '100%', paddingLeft: 22, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
                    />
                  </span>
                );
                const remove = <IconButton icon={X} label="Remove line" iconSize={16} onClick={() => removeLine(g.id, ln.id)} />;

                if (compact) {
                  // Dish and remove on one row; price, cost and food cost under it.
                  return (
                    <div key={ln.id} style={{ padding: '10px 0', ...rowRule }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>{dishField}</div>
                        {remove}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
                        <div style={{ width: 104 }}>{priceField}</div>
                        <div title={costTitle} style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', fontVariantNumeric: 'tabular-nums' }}>
                          <span style={{ color: 'var(--muted)' }}>Cost</span>
                          {cost}
                          {fcBadge}
                        </div>
                      </div>
                    </div>
                  );
                }
                return (
                  <div key={ln.id} style={{ ...LINE_GRID, padding: '8px 0', ...rowRule }}>
                    <div style={{ minWidth: 0 }}>{dishField}</div>
                    {priceField}
                    <div title={costTitle} style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{cost}</div>
                    <div title={costTitle} style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fcBadge}</div>
                    <div style={{ justifySelf: 'end' }}>{remove}</div>
                  </div>
                );
              })}
              <Button variant="quiet" size="sm" icon={Plus} onClick={() => addLine(g.id)} style={{ marginTop: 8, marginLeft: -8 }}>Add line</Button>
            </div>
          </div>
        ))}

        <Button icon={Plus} onClick={addSection}>Add section</Button>
      </>
    );

    return isPage ? (
      <div ref={editorRef}>
        <PageHeader back={back} title={title} meta={meta} status={savedStatus} actions={actions}>{sharedRow}</PageHeader>
        {body}
      </div>
    ) : (
      <div ref={editorRef} className="n-card">
        <CardHeader back={back} title={title} meta={meta} status={savedStatus} actions={actions} />
        <div style={{ padding: '0 16px 16px' }}>
          {sharedRow && <div style={{ marginBottom: 12 }}>{sharedRow}</div>}
          {body}
        </div>
      </div>
    );
  }

  // --- List view ---
  const meta = loading ? 'Loading…' : `${visibleMenus.length}${query ? ` of ${menus.length}` : ''} menu${menus.length === 1 ? '' : 's'}`;
  const actions = (
    <>
      {venuePicker}
      <Button variant="primary" icon={Plus} onClick={newMenu} disabled={!venueId}>New menu</Button>
    </>
  );
  const search = menus.length > 3 ? (
    <span style={{ position: 'relative', display: 'block', maxWidth: 320 }}>
      <Icon icon={Search} size={16} tone="muted" style={FIELD_ADORN} />
      <input
        className="n-input"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search menus…"
        aria-label="Search menus"
        style={{ width: '100%', paddingLeft: 34 }}
      />
    </span>
  ) : null;
  // In a card the table runs edge to edge, its outer cells on the card's gutter.
  const edgeL: CSSProperties | undefined = isPage ? undefined : { paddingLeft: 16 };
  const edgeR: CSSProperties = isPage ? { paddingLeft: 0 } : { paddingLeft: 0, paddingRight: 16 };
  // A load that failed with nothing to show is an error, never "No menus yet";
  // one that failed over a list already on screen keeps the list, error above.
  const errorBanner = error && !loading && menus.length > 0 ? <PageState kind="error" title={error} /> : null;
  const content = loading ? (
    <PageState kind="loading" title="Loading menus…" />
  ) : error && menus.length === 0 ? (
    <PageState kind="error" title="Couldn’t load menus" detail={error} />
  ) : visibleMenus.length === 0 ? (
    <PageState kind="empty" title={menus.length === 0 ? 'No menus yet.' : `No menus match “${query}”.`} />
  ) : (
    <table className="n-table">
      <thead>
        <tr>
          <th style={edgeL}>Menu</th>
          <th className="num" style={{ width: 1 }}>Sections</th>
          <th className="num" style={{ width: 1 }}>Lines</th>
          <th style={{ width: 1, ...edgeR }} />
        </tr>
      </thead>
      <tbody>
        {visibleMenus.map((m) => {
          const lineCount = m.groups.reduce((n, g) => n + g.lines.length, 0);
          return (
            <tr key={m.id} onClick={() => editMenu(m)} style={{ cursor: 'pointer' }}>
              <td style={edgeL}>
                {/* The row opens the menu; the name is a button so the row is reachable by keyboard. */}
                <button type="button" style={ROW_LINK}>{m.name || 'Untitled menu'}</button>
              </td>
              <td className="num" style={{ color: 'var(--text-soft)' }}>{m.groups.length}</td>
              <td className="num" style={{ color: lineCount ? 'var(--text)' : 'var(--muted)' }}>{lineCount}</td>
              <td style={{ width: 1, ...edgeR }}>
                <Icon icon={ChevronRight} size={16} tone="muted" style={{ display: 'block' }} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );

  if (isPage) {
    return (
      <div>
        <PageHeader title="Menus" meta={meta} status={savedStatus} actions={actions}>{search}</PageHeader>
        {errorBanner && <div style={{ marginBottom: 12 }}>{errorBanner}</div>}
        {content}
      </div>
    );
  }
  return (
    <div className="n-card" style={{ overflowX: 'auto' }}>
      <CardHeader title="Menus" meta={meta} status={savedStatus} actions={actions} />
      {search && <div style={{ padding: '0 16px 12px' }}>{search}</div>}
      {errorBanner && <div style={{ padding: '0 16px 12px' }}>{errorBanner}</div>}
      {content}
    </div>
  );
}
