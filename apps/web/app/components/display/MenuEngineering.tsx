'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { ArrowUpRight } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch, callComponentApi } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import Icon from '../ui/Icon';
import {
  ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, ZAxis,
  ReferenceArea, ReferenceLine, Tooltip,
} from 'recharts';

// --- Menu-engineering categories. The hexes are a CHART palette (the one place
// literal colours stay): validated colourblind-safe with the dataviz skill's
// validator, all pairs, on the white chart card. Position in the grid is the
// primary encoding. They colour MARKS only (dots, quadrant tints, keys) — text
// beside them wears text tokens, since #1baf7a is 2.8:1 on white. ---
type CatKey = 'star' | 'plow' | 'puzzle' | 'dog';
const CATS: Record<CatKey, { label: string; color: string }> = {
  star: { label: 'Stars', color: '#4a3aa7' },      // hi popularity, hi profit
  plow: { label: 'Plow horses', color: '#eb6834' }, // hi popularity, lo profit
  puzzle: { label: 'Puzzles', color: '#1baf7a' },   // lo popularity, hi profit
  dog: { label: 'Dogs', color: '#2a78d6' },         // lo popularity, lo profit
};

// The colour key beside a category name (legend, table, tooltip).
function CatDot({ cat, size = 8 }: { cat: CatKey; size?: number }) {
  return <span aria-hidden style={{ flex: '0 0 auto', width: size, height: size, borderRadius: 999, background: CATS[cat].color }} />;
}

// Quadrant names inside the chart: the one uppercase style (.n-eyebrow) drawn
// as SVG text — muted ink on the faint quadrant tint, never the series colour.
const quadrantLabel = (value: string, position: 'insideTopLeft' | 'insideTopRight' | 'insideBottomLeft' | 'insideBottomRight') => ({
  value, position, fill: 'var(--muted)', fontSize: 'var(--fs-2xs)', fontWeight: 600, letterSpacing: '0.06em',
});
const axisTick = { fontSize: 'var(--fs-xs)', fill: 'var(--text-soft)', fontWeight: 600 };

interface VenueOption { id: string; name: string }
interface CogsRow {
  posItemIdentifier?: string;
  posItemName?: string;
  posItemGroupName?: string;
  quantitySold?: number;
  salesExcludeTax?: number;
  cost?: number;
  discounts?: number;
}
interface MenuLine { name?: string; recipeId?: string | null }
interface Product {
  id: string;
  name: string;
  group: string;
  units: number;
  revenue: number;
  cost: number;
  gp: number;
  marginPct: number;
  cat: CatKey;
  plotX: number; // position inside the quadrant grid (0..2), jittered
  plotY: number;
  recipeId: string | null;
}

const money = (n: number) => `$${n.toLocaleString('en-NZ', { maximumFractionDigits: 0 })}`; // 0dp, 1,234
const pct = (n: number) => `${n.toFixed(2)}%`; // margin shown to 2dp
const qty = (n: number) => n.toLocaleString('en-NZ', { maximumFractionDigits: 0 }); // 0dp, 1,234

const median = (xs: number[]): number => {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
// Deterministic 32-bit hash of a string (FNV-1a), so a dot's spot inside its
// quadrant is stable across re-renders (no Math.random during render). The low
// and high 16 bits give two independent [0,1) offsets, so x and y don't correlate
// into diagonal streaks.
const hashInt = (s: string): number => {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193);
  return h >>> 0;
};

// Normalise a product/recipe name for fuzzy matching ("COCKTAIL - MARGARITA" ~
// "Margarita"): lowercase, strip non-alphanumerics to single spaces.
const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

// last-N-days window as YYYY-MM-DD (the COGS report accepts date-only bounds).
function periodRange(days: number): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - days);
  const fmt = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return { start: fmt(start), end: fmt(end) };
}
const PERIODS: { label: string; days: number }[] = [
  { label: 'Last 7 days', days: 7 },
  { label: 'Last 30 days', days: 30 },
  { label: 'Last 90 days', days: 90 },
];

// Build a product-name -> recipeId map from the menus (menu lines carry both).
// Exact normalised match first, then a length-guarded substring fallback.
function buildRecipeMatcher(lines: MenuLine[]): (name: string) => string | null {
  const exact = new Map<string, string>();
  const subs: { n: string; id: string }[] = [];
  for (const l of lines) {
    if (!l.recipeId || !l.name) continue;
    const n = norm(l.name);
    if (!n) continue;
    if (!exact.has(n)) exact.set(n, l.recipeId);
    subs.push({ n, id: l.recipeId });
  }
  return (name: string) => {
    const p = norm(name);
    if (!p) return null;
    const hit = exact.get(p);
    if (hit) return hit;
    for (const s of subs) {
      const shorter = Math.min(s.n.length, p.length);
      if (shorter >= 5 && (s.n.includes(p) || p.includes(s.n))) return s.id;
    }
    return null;
  };
}

export default function MenuEngineering({ props, onAction }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [venueId, setVenueId] = useState<string | null>((props?.activeVenueId as string) || rememberedVenue || null);

  const [periodDays, setPeriodDays] = useState(30);
  const [group, setGroup] = useState<string>('All'); // All | Food | Beverage | <other>
  const [rows, setRows] = useState<CogsRow[]>([]);
  const [matchRecipe, setMatchRecipe] = useState<(name: string) => string | null>(() => () => null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch('/api/venues').then(r => r.ok ? r.json() : null).then(d => {
      if (d?.venues?.length) {
        setVenues(d.venues);
        if (!venueId) {
          const remembered = rememberedVenue && d.venues.some((v: VenueOption) => v.id === rememberedVenue) ? rememberedVenue : null;
          setVenueId(remembered || d.venues[0].id);
        }
      }
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async (vid: string, days: number) => {
    setLoading(true);
    setError(null);
    try {
      const { start, end } = periodRange(days);
      const [cogsRes, menuRes, linksRes] = await Promise.all([
        callComponentApi('menu_engineering', 'get_cogs_detail', { start, end }, vid),
        callComponentApi('menu_editor', 'list_menus', {}, vid),
        // Proper POS-item -> recipe id from the Cook Brothers App (exact product
        // names); {} when the venue isn't CB-connected, so we fall back to menus.
        apiFetch('/api/menu-engineering/recipe-links', { method: 'POST', body: JSON.stringify({ venue_id: vid, start, end }) })
          .then(r => r.ok ? r.json() : { links: {} }).catch(() => ({ links: {} })),
      ]);
      const cogs = (cogsRes?.data as CogsRow[]) || [];
      // list_menus -> flatten all menu lines (groups[].lines[]) for the fallback.
      const menus = (menuRes?.data as Array<{ groups?: Array<{ lines?: MenuLine[] }> }>) || [];
      const lines: MenuLine[] = [];
      for (const m of menus) for (const g of m.groups || []) for (const l of g.lines || []) lines.push(l);
      const menuMatch = buildRecipeMatcher(lines);
      const cbLinks = (linksRes?.links as Record<string, string>) || {};
      // Exact CB link by product name first (both come from the same POS names),
      // then the menu name-match fallback for venues without the CB app.
      const matcher = (name: string) => cbLinks[name] || cbLinks[name.trim()] || menuMatch(name);
      setRows(Array.isArray(cogs) ? cogs : []);
      setMatchRecipe(() => matcher);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load product report');
      setRows([]);
    }
    setLoading(false);
  }, []);

  useEffect(() => { if (venueId) load(venueId, periodDays); }, [venueId, periodDays, load]);

  const changeVenue = (vid: string) => { setVenueId(vid); if (persistVenue) setActiveVenue(vid); };

  // Groups present, for the filter chips.
  const groupOptions = useMemo(() => {
    const gs = new Set<string>();
    for (const r of rows) if (r.posItemGroupName) gs.add(r.posItemGroupName);
    return ['All', ...Array.from(gs).sort()];
  }, [rows]);

  // Compute products (units, margin, category) for the selected group.
  const products: Product[] = useMemo(() => {
    const filtered = rows.filter(r => (r.quantitySold || 0) > 0 && (r.salesExcludeTax || 0) > 0 && (group === 'All' || r.posItemGroupName === group));
    const base = filtered.map((r, i) => {
      const units = r.quantitySold || 0;
      const revenue = r.salesExcludeTax || 0;
      const cost = r.cost || 0;
      const gp = revenue - cost;
      return {
        id: r.posItemIdentifier || r.posItemName || `row-${i}`,
        name: r.posItemName || '(unnamed)',
        group: r.posItemGroupName || '',
        units, revenue, cost, gp,
        // Margin can't exceed 100% (gp <= revenue); a near-zero-revenue product can
        // produce an absurd negative % that blows out the axis — floor it at -100%.
        marginPct: revenue ? Math.max(-100, (gp / revenue) * 100) : 0,
      };
    });
    if (!base.length) return [];
    // Split the list in half on each axis (median): top half by units = high
    // popularity, top half by margin = high profitability. Each item then sits in
    // exactly one of four equal quadrants.
    const medUnits = median(base.map(p => p.units));
    const medMargin = median(base.map(p => p.marginPct));
    return base.map((p) => {
      const hiPop = p.units >= medUnits;
      const hiProfit = p.marginPct >= medMargin;
      const cat: CatKey = hiPop && hiProfit ? 'star' : hiPop ? 'plow' : hiProfit ? 'puzzle' : 'dog';
      // Place the dot inside its quadrant cell (a 1x1 box in the 0..2 grid) with a
      // stable jitter so dots spread out instead of stacking on one point. The two
      // halves of the hash give independent x/y offsets.
      const h = hashInt(p.id);
      const plotX = (hiProfit ? 1 : 0) + 0.1 + ((h & 0xffff) / 0xffff) * 0.8;
      const plotY = (hiPop ? 1 : 0) + 0.1 + (((h >>> 16) & 0xffff) / 0xffff) * 0.8;
      return { ...p, cat, plotX, plotY, recipeId: matchRecipe(p.name) };
    });
  }, [rows, group, matchRecipe]);

  const byCat = useMemo(() => {
    const m: Record<CatKey, Product[]> = { star: [], plow: [], puzzle: [], dog: [] };
    for (const p of products) m[p.cat].push(p);
    return m;
  }, [products]);

  const openRecipe = (p: Product) => { if (p.recipeId && onAction) onAction({ connector_name: 'norm', action: 'open_recipe', params: { recipe_id: p.recipeId } }); };

  // --- styles ---
  // Filter chips: neutral; the selected one takes the tan tint (as a selected chip does everywhere).
  const chip = (active: boolean): React.CSSProperties => ({
    flex: '0 0 auto', padding: '4px 12px', fontFamily: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: active ? 600 : 500,
    lineHeight: 1.4, whiteSpace: 'nowrap', borderRadius: 999, cursor: 'pointer',
    border: `1px solid ${active ? 'var(--brand-soft)' : 'var(--line-strong)'}`,
    background: active ? 'var(--accent-soft)' : 'var(--bg)', color: active ? 'var(--accent)' : 'var(--text-soft)',
  });
  const nowrap: React.CSSProperties = { whiteSpace: 'nowrap' };

  const periodLabel = PERIODS.find(p => p.days === periodDays)?.label || '';
  const venuePicker = venues.length > 1
    ? <VenueSelect venues={venues} value={venueId} onChange={changeVenue} />
    : null;

  // Period first, then product group: one filter row above everything it scopes.
  const filters = (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px 20px' }}>
      <div role="group" aria-label="Period" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {PERIODS.map(p => <button key={p.days} type="button" aria-pressed={periodDays === p.days} onClick={() => setPeriodDays(p.days)} style={chip(periodDays === p.days)}>{p.label}</button>)}
      </div>
      <div role="group" aria-label="Product group" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {groupOptions.map(g => <button key={g} type="button" aria-pressed={group === g} onClick={() => setGroup(g)} style={chip(group === g)}>{g}</button>)}
      </div>
    </div>
  );

  const renderTable = () => (
    <table className="n-table">
      <thead>
        <tr>
          <th>Product</th>
          <th>Category</th>
          <th className="num">Units</th>
          <th className="num">Sales</th>
          <th className="num">Cost</th>
          <th className="num">Gross profit</th>
          <th className="num">Margin</th>
        </tr>
      </thead>
      <tbody>
        {[...products].sort((a, b) => b.units - a.units).map((p) => (
          <tr key={p.id}
            onClick={() => openRecipe(p)}
            title={p.recipeId ? 'Open recipe' : 'No linked recipe'}
            style={{ cursor: p.recipeId ? 'pointer' : 'default' }}>
            <td style={nowrap}>
              {p.recipeId ? (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--accent)', fontWeight: 500 }}>
                  {p.name}
                  <Icon icon={ArrowUpRight} size="dense" tone="muted" />
                </span>
              ) : p.name}
            </td>
            <td style={nowrap}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--text-soft)' }}>
                <CatDot cat={p.cat} />
                {CATS[p.cat].label}
              </span>
            </td>
            <td className="num" style={nowrap}>{qty(p.units)}</td>
            <td className="num" style={nowrap}>{money(p.revenue)}</td>
            <td className="num" style={nowrap}>{money(p.cost)}</td>
            <td className="num" style={nowrap}>{money(p.gp)}</td>
            <td className="num" style={nowrap}>{pct(p.marginPct)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <div style={{ width: '100%' }}>
      {/* Header: the page header on a page; a compact title row in a conversation. */}
      {persistVenue ? (
        <PageHeader
          title="Menu Engineering"
          meta={!loading && !error && products.length > 0 ? `${qty(products.length)} products · ${periodLabel.toLowerCase()}` : periodLabel}
          actions={venuePicker}
        >
          {filters}
        </PageHeader>
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
            <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>Menu Engineering</h2>
            <span style={{ flex: 1 }} />
            {venuePicker}
          </div>
          <div style={{ marginBottom: 16 }}>{filters}</div>
        </>
      )}

      {loading && <PageState kind="loading" title="Loading product report…" />}
      {error && <PageState kind="error" title="Couldn’t load the product report" detail={error} />}
      {!loading && !error && products.length === 0 && <PageState kind="empty" title="No product sales in this period" />}

      {!loading && products.length > 0 && (
        <>
          {/* Scatter: the quadrant matrix on a white card */}
          <div className="n-card" style={{ padding: '12px 8px 4px' }}>
            <div style={{ height: 420 }}>
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 18, right: 24, bottom: 40, left: 18 }}>
                  {/* four equal quadrants (drawn first, behind the dots) */}
                  <ReferenceArea x1={0} x2={1} y1={1} y2={2} fill={CATS.plow.color} fillOpacity={0.07} stroke="none" label={quadrantLabel('PLOW HORSES', 'insideTopLeft')} />
                  <ReferenceArea x1={1} x2={2} y1={1} y2={2} fill={CATS.star.color} fillOpacity={0.07} stroke="none" label={quadrantLabel('STARS', 'insideTopRight')} />
                  <ReferenceArea x1={0} x2={1} y1={0} y2={1} fill={CATS.dog.color} fillOpacity={0.07} stroke="none" label={quadrantLabel('DOGS', 'insideBottomLeft')} />
                  <ReferenceArea x1={1} x2={2} y1={0} y2={1} fill={CATS.puzzle.color} fillOpacity={0.07} stroke="none" label={quadrantLabel('PUZZLES', 'insideBottomRight')} />
                  <ReferenceLine x={1} stroke="var(--line-strong)" />
                  <ReferenceLine y={1} stroke="var(--line-strong)" />
                  <XAxis type="number" dataKey="plotX" domain={[0, 2]} ticks={[0.5, 1.5]} tickFormatter={(v) => (v < 1 ? 'Low' : 'High')} tick={axisTick} axisLine={{ stroke: 'var(--line)' }} tickLine={false}
                    label={{ value: 'Profitability  (margin)', position: 'bottom', offset: 8, fontSize: 'var(--fs-xs)', fill: 'var(--muted)' }} />
                  <YAxis type="number" dataKey="plotY" domain={[0, 2]} ticks={[0.5, 1.5]} tickFormatter={(v) => (v < 1 ? 'Low' : 'High')} tick={axisTick} axisLine={{ stroke: 'var(--line)' }} tickLine={false}
                    label={{ value: 'Popularity  (units sold)', angle: -90, position: 'insideLeft', offset: 10, fontSize: 'var(--fs-xs)', fill: 'var(--muted)' }} />
                  {/* r≈5.9 with a 2px white ring (the stroke), so overlapping dots stay distinct */}
                  <ZAxis range={[110, 110]} />
                  {/* No crosshair: a dot's spot inside its quadrant is jitter, not a value to read off the axes. */}
                  <Tooltip cursor={false} content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const p = payload[0].payload as Product;
                    return (
                      <div style={{ background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: '8px 10px', fontSize: 'var(--fs-xs)', lineHeight: 1.45, color: 'var(--text-soft)', fontVariantNumeric: 'tabular-nums', boxShadow: '0 4px 12px rgba(26, 26, 26, 0.08)' }}>
                        <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)', marginBottom: 2 }}>{p.name}</div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 500, marginBottom: 4 }}><CatDot cat={p.cat} />{CATS[p.cat].label}</div>
                        <div>{qty(p.units)} sold · {pct(p.marginPct)} margin</div>
                        <div>{money(p.revenue)} sales · {money(p.gp)} profit</div>
                        {p.recipeId && (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 4, color: 'var(--accent)', fontWeight: 500 }}>
                            Click to open recipe <Icon icon={ArrowUpRight} size="meta" />
                          </div>
                        )}
                      </div>
                    );
                  }} />
                  {(Object.keys(CATS) as CatKey[]).map(k => (
                    <Scatter key={k} name={CATS[k].label} data={byCat[k]} fill={CATS[k].color} fillOpacity={0.9}
                      stroke="var(--bg)" strokeWidth={2}
                      cursor="pointer"
                      onClick={(pt) => { const p = (pt as { payload?: Product })?.payload; if (p?.recipeId) openRecipe(p); }} />
                  ))}
                </ScatterChart>
              </ResponsiveContainer>
            </div>
            {/* Legend */}
            <div style={{ display: 'flex', gap: '6px 16px', flexWrap: 'wrap', justifyContent: 'center', padding: '4px 8px 10px' }}>
              {(Object.keys(CATS) as CatKey[]).map(k => (
                <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                  <CatDot cat={k} size={10} />
                  {CATS[k].label} <span style={{ color: 'var(--muted)', fontVariantNumeric: 'tabular-nums' }}>({byCat[k].length})</span>
                </span>
              ))}
            </div>
          </div>

          {/* Table: straight on the page; in a card in a conversation. Scrolls
              sideways on its own when the columns don't fit (phones). */}
          {persistVenue
            ? <div style={{ marginTop: 20, overflowX: 'auto' }}>{renderTable()}</div>
            : <div className="n-card" style={{ marginTop: 16, overflowX: 'auto' }}>{renderTable()}</div>}
        </>
      )}
    </div>
  );
}
