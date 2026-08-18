'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch, callComponentApi } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { colors } from '../../lib/theme';
import {
  ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, ZAxis,
  ReferenceArea, ReferenceLine, Tooltip,
} from 'recharts';

// --- Menu-engineering categories (colours validated colourblind-safe via the
// dataviz skill's validator; position in the grid is the primary encoding). ---
type CatKey = 'star' | 'plow' | 'puzzle' | 'dog';
const CATS: Record<CatKey, { label: string; color: string }> = {
  star: { label: 'Stars', color: '#4a3aa7' },      // hi popularity, hi profit
  plow: { label: 'Plow Horses', color: '#eb6834' }, // hi popularity, lo profit
  puzzle: { label: 'Puzzles', color: '#1baf7a' },   // lo popularity, hi profit
  dog: { label: 'Dogs', color: '#2a78d6' },         // lo popularity, lo profit
};

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
  const chip = (active: boolean): React.CSSProperties => ({
    padding: '4px 12px', fontSize: '0.78rem', fontWeight: 600, borderRadius: 999,
    border: `1px solid ${active ? colors.executive_chef : colors.border}`, cursor: 'pointer',
    background: active ? colors.executive_chef : '#fff', color: active ? '#fff' : colors.textSecondary,
  });
  const th: React.CSSProperties = { padding: '8px 10px', textAlign: 'left', fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: colors.textMuted, borderBottom: `1px solid ${colors.border}`, whiteSpace: 'nowrap' };
  const td: React.CSSProperties = { padding: '8px 10px', fontSize: '0.82rem', color: colors.textPrimary, borderBottom: `1px solid ${colors.borderLight}`, whiteSpace: 'nowrap' };
  const numTd: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' };

  return (
    <div style={{ width: '100%' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.9rem', flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: colors.textPrimary }}>Menu Engineering</h2>
        {venues.length > 1 && (
          <select value={venueId || ''} onChange={(e) => changeVenue(e.target.value)} style={{ padding: '4px 8px', fontSize: '0.8rem', border: `1px solid ${colors.border}`, borderRadius: 6, background: '#fff', color: colors.textSecondary }}>
            {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
        )}
        <span style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: '0.35rem' }}>
          {PERIODS.map(p => <button key={p.days} onClick={() => setPeriodDays(p.days)} style={chip(periodDays === p.days)}>{p.label}</button>)}
        </div>
      </div>

      {/* Group filter */}
      <div style={{ display: 'flex', gap: '0.35rem', marginBottom: '0.9rem', flexWrap: 'wrap' }}>
        {groupOptions.map(g => <button key={g} onClick={() => setGroup(g)} style={chip(group === g)}>{g}</button>)}
      </div>

      {loading && <div style={{ fontSize: '0.85rem', color: colors.textMuted, padding: '2rem 0' }}>Loading product report…</div>}
      {error && <div style={{ fontSize: '0.85rem', color: colors.error, padding: '1rem 0' }}>{error}</div>}
      {!loading && !error && products.length === 0 && <div style={{ fontSize: '0.85rem', color: colors.textMuted, padding: '2rem 0' }}>No product sales in this period.</div>}

      {!loading && products.length > 0 && (
        <>
          {/* Scatter */}
          <div style={{ border: `1px solid ${colors.border}`, borderRadius: 10, background: '#fff', padding: '0.75rem 0.5rem 0.25rem' }}>
            <div style={{ height: 420 }}>
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 18, right: 24, bottom: 40, left: 18 }}>
                  {/* four equal quadrants (drawn first, behind the dots) */}
                  <ReferenceArea x1={0} x2={1} y1={1} y2={2} fill={CATS.plow.color} fillOpacity={0.07} stroke="none" label={{ value: 'PLOW HORSES', position: 'insideTopLeft', fill: CATS.plow.color, fontSize: 11, fontWeight: 700 }} />
                  <ReferenceArea x1={1} x2={2} y1={1} y2={2} fill={CATS.star.color} fillOpacity={0.07} stroke="none" label={{ value: 'STARS', position: 'insideTopRight', fill: CATS.star.color, fontSize: 11, fontWeight: 700 }} />
                  <ReferenceArea x1={0} x2={1} y1={0} y2={1} fill={CATS.dog.color} fillOpacity={0.07} stroke="none" label={{ value: 'DOGS', position: 'insideBottomLeft', fill: CATS.dog.color, fontSize: 11, fontWeight: 700 }} />
                  <ReferenceArea x1={1} x2={2} y1={0} y2={1} fill={CATS.puzzle.color} fillOpacity={0.07} stroke="none" label={{ value: 'PUZZLES', position: 'insideBottomRight', fill: CATS.puzzle.color, fontSize: 11, fontWeight: 700 }} />
                  <ReferenceLine x={1} stroke={colors.border} />
                  <ReferenceLine y={1} stroke={colors.border} />
                  <XAxis type="number" dataKey="plotX" domain={[0, 2]} ticks={[0.5, 1.5]} tickFormatter={(v) => (v < 1 ? 'Low' : 'High')} tick={{ fontSize: 12, fill: colors.textSecondary, fontWeight: 600 }} axisLine={{ stroke: colors.border }} tickLine={false}
                    label={{ value: 'Profitability  (margin)', position: 'bottom', offset: 8, fontSize: 12, fill: colors.textSecondary }} />
                  <YAxis type="number" dataKey="plotY" domain={[0, 2]} ticks={[0.5, 1.5]} tickFormatter={(v) => (v < 1 ? 'Low' : 'High')} tick={{ fontSize: 12, fill: colors.textSecondary, fontWeight: 600 }} axisLine={{ stroke: colors.border }} tickLine={false}
                    label={{ value: 'Popularity  (units sold)', angle: -90, position: 'insideLeft', offset: 10, fontSize: 12, fill: colors.textSecondary }} />
                  <ZAxis range={[80, 80]} />
                  <Tooltip cursor={{ strokeDasharray: '3 3' }} content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const p = payload[0].payload as Product;
                    return (
                      <div style={{ background: '#fff', border: `1px solid ${colors.border}`, borderRadius: 8, padding: '0.5rem 0.7rem', fontSize: '0.78rem', boxShadow: '0 2px 8px rgba(0,0,0,0.08)' }}>
                        <div style={{ fontWeight: 700, color: colors.textPrimary, marginBottom: 2 }}>{p.name}</div>
                        <div style={{ color: CATS[p.cat].color, fontWeight: 600, marginBottom: 4 }}>{CATS[p.cat].label}</div>
                        <div style={{ color: colors.textSecondary }}>{qty(p.units)} sold · {pct(p.marginPct)} margin</div>
                        <div style={{ color: colors.textSecondary }}>{money(p.revenue)} sales · {money(p.gp)} profit</div>
                        {p.recipeId && <div style={{ color: colors.executive_chef, marginTop: 3 }}>Click to open recipe →</div>}
                      </div>
                    );
                  }} />
                  {(Object.keys(CATS) as CatKey[]).map(k => (
                    <Scatter key={k} name={CATS[k].label} data={byCat[k]} fill={CATS[k].color} fillOpacity={0.9}
                      cursor="pointer"
                      onClick={(pt) => { const p = (pt as { payload?: Product })?.payload; if (p?.recipeId) openRecipe(p); }} />
                  ))}
                </ScatterChart>
              </ResponsiveContainer>
            </div>
            {/* Legend */}
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', justifyContent: 'center', padding: '0.4rem 0 0.6rem' }}>
              {(Object.keys(CATS) as CatKey[]).map(k => (
                <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.78rem', color: colors.textSecondary }}>
                  <span style={{ width: 10, height: 10, borderRadius: 999, background: CATS[k].color, display: 'inline-block' }} />
                  {CATS[k].label} <span style={{ color: colors.textMuted }}>({byCat[k].length})</span>
                </span>
              ))}
            </div>
          </div>

          {/* Table */}
          <div style={{ border: `1px solid ${colors.border}`, borderRadius: 10, marginTop: '1rem', overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={th}>Product</th>
                  <th style={th}>Category</th>
                  <th style={{ ...th, textAlign: 'right' }}>Units</th>
                  <th style={{ ...th, textAlign: 'right' }}>Sales</th>
                  <th style={{ ...th, textAlign: 'right' }}>Cost</th>
                  <th style={{ ...th, textAlign: 'right' }}>Gross profit</th>
                  <th style={{ ...th, textAlign: 'right' }}>Margin</th>
                </tr>
              </thead>
              <tbody>
                {[...products].sort((a, b) => b.units - a.units).map((p) => (
                  <tr key={p.id}
                    onClick={() => openRecipe(p)}
                    title={p.recipeId ? 'Open recipe' : 'No linked recipe'}
                    style={{ cursor: p.recipeId ? 'pointer' : 'default' }}
                    onMouseEnter={(e) => { if (p.recipeId) e.currentTarget.style.background = colors.selectedBg; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = ''; }}>
                    <td style={td}>
                      <span style={{ color: p.recipeId ? colors.executive_chef : colors.textPrimary, fontWeight: p.recipeId ? 600 : 400 }}>{p.name}</span>
                      {p.recipeId && <span style={{ color: colors.textMuted, fontSize: '0.72rem' }}> ↗</span>}
                    </td>
                    <td style={td}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                        <span style={{ width: 8, height: 8, borderRadius: 999, background: CATS[p.cat].color }} />
                        {CATS[p.cat].label}
                      </span>
                    </td>
                    <td style={numTd}>{qty(p.units)}</td>
                    <td style={numTd}>{money(p.revenue)}</td>
                    <td style={numTd}>{money(p.cost)}</td>
                    <td style={numTd}>{money(p.gp)}</td>
                    <td style={numTd}>{pct(p.marginPct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
