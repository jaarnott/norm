'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw, Search } from 'lucide-react';
import type { DisplayBlockProps } from '../DisplayBlockRenderer';
import { apiFetch } from '../../../lib/api';
import { formatMoney } from '../../../lib/format';
import { useActiveVenue } from '../../../hooks/useActiveVenue';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import VenueSelect, { type VenueOption } from '../../ui/VenueSelect';
import {
  ChangeDialog, ItemEditDialog, MergeDialog, SupplierEditDialog, UnitEditDialog,
} from './CatalogueDialogs';
import {
  baseUnit, itemMergeTargets, itemUnitType, search, supplierMergeTargets, unitMergeTargets,
  type Catalogue, type CatalogueItem, type CatalogueSupplier, type CatalogueUnit, type Change,
} from './catalogueLogic';

// Loaded Stock → Catalogue (docs/plans/stock-catalogue-page.md): a venue's
// stock items, units and suppliers, with edit, delete and merge. Every change
// goes through the same tools Norm uses in chat (get_stock, manage_stock_item,
// manage_supplier) via routers/stock_catalogue.py: the tool's own preview
// first, then Confirm. Active records by default; "Show deactivated" adds
// retired items and suppliers and deleted units, which can be restored.

type TabId = 'items' | 'units' | 'suppliers';
type Dialog =
  | { kind: 'change'; change: Change }
  | { kind: 'edit-item'; item: CatalogueItem }
  | { kind: 'edit-unit'; unit: CatalogueUnit }
  | { kind: 'edit-supplier'; supplier: CatalogueSupplier }
  | { kind: 'merge-item'; item: CatalogueItem }
  | { kind: 'merge-unit'; unit: CatalogueUnit }
  | { kind: 'merge-supplier'; supplier: CatalogueSupplier };

/** Rows a table shows before "Show more". */
const PAGE = 100;

const TITLE = 'Catalogue';
const nowrap: React.CSSProperties = { whiteSpace: 'nowrap' };
const muted: React.CSSProperties = { color: 'var(--muted)' };
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString() : '—');
const count = (n: number | undefined) => (n ?? 0).toLocaleString();

function Actions({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end', flexWrap: 'nowrap' }}>{children}</div>;
}

/** A phone's row: the name, one line of details, its badges, then its
 *  buttons — instead of a table that scrolls sideways to reach them. */
function NarrowRow({ title, meta, badges, actions, dim }: {
  title: React.ReactNode; meta: React.ReactNode; badges?: React.ReactNode; actions: React.ReactNode; dim?: boolean;
}) {
  return (
    <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--line)', opacity: dim ? 0.7 : 1 }}>
      <div style={{ fontWeight: 500, color: 'var(--text)', overflowWrap: 'anywhere' }}>{title}</div>
      <div style={{ ...muted, fontSize: 'var(--fs-sm)', marginTop: 2, overflowWrap: 'anywhere' }}>{meta}</div>
      {badges && <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>{badges}</div>}
      <div style={{ display: 'flex', gap: 4, marginTop: 6, marginLeft: -8 }}>{actions}</div>
    </div>
  );
}

function MoreRows({ shown, total, onMore }: { shown: number; total: number; onMore: () => void }) {
  if (shown >= total) return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: 12 }}>
      <Button size="sm" onClick={onMore}>Show {Math.min(PAGE, total - shown)} more of {count(total - shown)}</Button>
    </div>
  );
}

export default function StockCatalogue({ props }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [venueId, setVenueId] = useState<string | null>((props?.activeVenueId as string) || rememberedVenue || null);

  const [tab, setTab] = useState<TabId>('items');
  const [showDeactivated, setShowDeactivated] = useState(false);
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(PAGE);
  const [open, setOpen] = useState<Set<string>>(new Set());

  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const { isMobile } = useBreakpoint();

  useEffect(() => {
    apiFetch('/api/venues').then(r => (r.ok ? r.json() : null)).then(d => {
      if (d?.venues?.length) {
        setVenues(d.venues);
        if (!venueId) {
          const remembered = rememberedVenue && d.venues.some((v: VenueOption) => v.id === rememberedVenue) ? rememberedVenue : null;
          setVenueId(remembered || d.venues[0].id);
        }
      }
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async (vid: string, withDeactivated: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const r = await apiFetch(`/api/stock-catalogue?venue_id=${encodeURIComponent(vid)}&include_deleted=${withDeactivated}`);
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `Couldn't load the catalogue (${r.status})`);
      setCatalogue(d as Catalogue);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setCatalogue(null);
    }
    setLoading(false);
  }, []);

  // Deactivated records are fetched only when asked for (Loaded's older item
  // list is a second, bigger read).
  useEffect(() => { if (venueId) load(venueId, showDeactivated); }, [venueId, showDeactivated, load]);
  useEffect(() => { setLimit(PAGE); }, [tab, query, showDeactivated, venueId]);

  const changeVenue = (vid: string) => {
    setVenueId(vid);
    setMessage(null);
    if (persistVenue) setActiveVenue(vid);
  };

  const items = useMemo(() => (catalogue ? search(catalogue.items, query, showDeactivated, i => [
    i.name, i.group, i.default_supplier, ...i.variants.flatMap(v => [v.stock_code, v.supplier]),
  ]) : []), [catalogue, query, showDeactivated]);
  const units = useMemo(() => (catalogue ? search(catalogue.units, query, showDeactivated, u => [u.name, u.type]) : []), [catalogue, query, showDeactivated]);
  const suppliers = useMemo(() => (catalogue ? search(catalogue.suppliers, query, showDeactivated, s => [
    s.name, s.contact, s.email, s.customer_number,
  ]) : []), [catalogue, query, showDeactivated]);

  const run = (change: Change) => setDialog({ kind: 'change', change });
  const done = (text: string) => {
    setDialog(null);
    setMessage(text);
    if (venueId) load(venueId, showDeactivated);
  };

  const c = catalogue?.counts ?? {};
  const meta = catalogue
    ? `${count(c.items)} items · ${count(c.units)} units · ${count(c.suppliers)} suppliers`
    : 'Stock items, units and suppliers from Loaded.';
  const tabs = [
    { id: 'items', label: 'Items', testId: 'catalogue-tab-items' },
    { id: 'units', label: 'Units', testId: 'catalogue-tab-units' },
    { id: 'suppliers', label: 'Suppliers', testId: 'catalogue-tab-suppliers' },
  ];

  const actions = (
    <>
      {venues.length > 1 && <VenueSelect venues={venues} value={venueId} onChange={changeVenue} />}
      <IconButton icon={RefreshCw} label="Reload" onClick={() => venueId && load(venueId, showDeactivated)} disabled={loading || !venueId} />
    </>
  );

  const toolbar = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
      <span style={{ position: 'relative', flex: '1 1 260px', maxWidth: 420 }}>
        <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
        <input className="n-input" placeholder={`Search ${tab}…`} aria-label={`Search ${tab}`} value={query}
          onChange={e => setQuery(e.target.value)} style={{ width: '100%', boxSizing: 'border-box', paddingLeft: 34 }} />
      </span>
      <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer' }}>
        <input type="checkbox" role="switch" checked={showDeactivated} onChange={e => setShowDeactivated(e.target.checked)} data-testid="catalogue-show-deactivated" />
        Show deactivated
      </label>
    </div>
  );

  const body = () => {
    if (!venueId) return <PageState kind="empty" title="Choose a venue" />;
    if (error) return <PageState kind="error" title="Couldn't load the catalogue" detail={error} />;
    if (!catalogue) return <PageState kind="loading" title="Loading the catalogue from Loaded…" />;
    if (tab === 'items') return itemsTable();
    if (tab === 'units') return unitsTable();
    return suppliersTable();
  };

  const itemActions = (i: CatalogueItem) => (i.deactivated ? (
    <Button size="sm" variant="quiet" onClick={() => run({ tool: 'manage_stock_item', params: { op: 'restore', item_id: i.id }, verb: 'Restore' })}>Restore</Button>
  ) : (
    <>
      <Button size="sm" variant="quiet" onClick={() => setDialog({ kind: 'edit-item', item: i })}>Edit</Button>
      <Button size="sm" variant="quiet" onClick={() => setDialog({ kind: 'merge-item', item: i })}>Merge</Button>
      <Button size="sm" variant="quiet" onClick={() => run({ tool: 'manage_stock_item', params: { op: 'retire', item_id: i.id }, verb: 'Retire', danger: true })}>Retire</Button>
    </>
  ));

  const itemsTable = () => {
    if (!items.length) return <PageState kind="empty" title={query ? 'No items match' : 'No stock items'} />;
    const shown = items.slice(0, limit);
    if (isMobile) {
      return (
        <div className="n-card" data-testid="catalogue-items">
          {shown.map(i => (
            <NarrowRow key={i.id} dim={i.deactivated} title={i.name}
              meta={[i.group, i.counting_unit && `counted in ${i.counting_unit}`, i.default_supplier,
                !i.deactivated && `${i.variants.length} variant${i.variants.length === 1 ? '' : 's'}`].filter(Boolean).join(' · ')}
              badges={i.deactivated ? <Badge>Retired {day(i.deactivated_at)}</Badge> : undefined}
              actions={itemActions(i)} />
          ))}
          <MoreRows shown={shown.length} total={items.length} onMore={() => setLimit(l => l + PAGE)} />
        </div>
      );
    }
    return (
      <div className="n-card" style={{ overflowX: 'auto' }}>
        <table className="n-table" style={{ minWidth: 760 }} data-testid="catalogue-items">
          <thead>
            <tr><th>Item</th><th>Group</th><th>Counted in</th><th>Default supplier</th><th className="num">Variants</th><th aria-label="Actions" /></tr>
          </thead>
          <tbody>
            {shown.map(i => {
              const isOpen = open.has(i.id);
              const toggle = () => setOpen(s => { const n = new Set(s); if (n.has(i.id)) n.delete(i.id); else n.add(i.id); return n; });
              return (
                <React.Fragment key={i.id}>
                  <tr style={i.deactivated ? { opacity: 0.7 } : undefined}>
                    <td>
                      <button type="button" onClick={toggle} aria-expanded={isOpen} disabled={!i.variants.length}
                        style={{ display: 'flex', alignItems: 'center', gap: 4, padding: 0, border: 'none', background: 'transparent', font: 'inherit', color: 'inherit', textAlign: 'left', cursor: i.variants.length ? 'pointer' : 'default' }}>
                        {i.variants.length > 0 && <Icon icon={isOpen ? ChevronDown : ChevronRight} size={14} tone="muted" />}
                        <span style={{ fontWeight: 500 }}>{i.name}</span>
                      </button>
                      {i.deactivated && <div style={{ marginTop: 2 }}><Badge>Retired {day(i.deactivated_at)}</Badge></div>}
                    </td>
                    <td style={muted}>{i.group || '—'}</td>
                    <td style={nowrap}>{i.counting_unit || '—'}</td>
                    <td>{i.default_supplier || <span style={muted}>—</span>}</td>
                    <td className="num">{i.deactivated ? '—' : i.variants.length}</td>
                    <td><Actions>{itemActions(i)}</Actions></td>
                  </tr>
                  {isOpen && i.variants.map(v => (
                    <tr key={v.variant_id} style={{ fontSize: 'var(--fs-sm)' }}>
                      <td style={{ paddingLeft: 32 }}>{v.supplier || '—'}{v.default && <span style={{ ...muted, marginLeft: 6 }}>(default)</span>}</td>
                      <td style={muted}>{v.stock_code || 'no code'}</td>
                      <td style={nowrap}>{v.unit || '—'}</td>
                      <td>{v.unit_cost == null ? '—' : `${formatMoney(v.unit_cost)} per ${v.unit || 'unit'}`}</td>
                      <td /><td />
                    </tr>
                  ))}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
        <MoreRows shown={shown.length} total={items.length} onMore={() => setLimit(l => l + PAGE)} />
      </div>
    );
  };

  const unitActions = (u: CatalogueUnit) => (u.deactivated ? (
    <Button size="sm" variant="quiet" onClick={() => run({ tool: 'manage_stock_item', params: { op: 'restore_unit', unit_id: u.id }, verb: 'Restore' })}>Restore</Button>
  ) : (
    <>
      <Button size="sm" variant="quiet" onClick={() => setDialog({ kind: 'edit-unit', unit: u })}>Edit</Button>
      <Button size="sm" variant="quiet" onClick={() => setDialog({ kind: 'merge-unit', unit: u })}>Merge</Button>
      <Button size="sm" variant="quiet" onClick={() => run({ tool: 'manage_stock_item', params: { op: 'delete_unit', unit_id: u.id }, verb: 'Delete', danger: true })}>Delete</Button>
    </>
  ));
  const unitBadges = (u: CatalogueUnit) => (u.deactivated || u.hidden_by_norm ? (
    <>
      {u.deactivated && <Badge>Deleted {day(u.deactivated_at)}</Badge>}
      {u.hidden_by_norm && <Badge title="Not a measurement, so Norm never books invoices in it">Not used by Norm</Badge>}
    </>
  ) : undefined);

  const unitsTable = () => {
    if (!units.length) return <PageState kind="empty" title={query ? 'No units match' : 'No units'} />;
    const shown = units.slice(0, limit);
    if (isMobile) {
      return (
        <div className="n-card" data-testid="catalogue-units">
          {shown.map(u => (
            <NarrowRow key={u.id} dim={u.deactivated} title={u.name}
              meta={[`${u.type}, ${u.ratio} ${baseUnit(u.type)}`, !u.deactivated && `${u.items} item${u.items === 1 ? '' : 's'}`].filter(Boolean).join(' · ')}
              badges={unitBadges(u)} actions={unitActions(u)} />
          ))}
          <MoreRows shown={shown.length} total={units.length} onMore={() => setLimit(l => l + PAGE)} />
        </div>
      );
    }
    return (
      <div className="n-card" style={{ overflowX: 'auto' }}>
        <table className="n-table" style={{ minWidth: 640 }} data-testid="catalogue-units">
          <thead>
            <tr><th>Unit</th><th>Kind</th><th className="num">Size</th><th className="num">Items</th><th aria-label="Actions" /></tr>
          </thead>
          <tbody>
            {shown.map(u => (
              <tr key={u.id} style={u.deactivated ? { opacity: 0.7 } : undefined}>
                <td>
                  <span style={{ fontWeight: 500 }}>{u.name}</span>
                  {unitBadges(u) && <div style={{ display: 'flex', gap: 4, marginTop: 2 }}>{unitBadges(u)}</div>}
                </td>
                <td style={muted}>{u.type}</td>
                <td className="num" style={nowrap}>{u.ratio} {baseUnit(u.type)}</td>
                <td className="num">{u.deactivated ? '—' : u.items}</td>
                <td><Actions>{unitActions(u)}</Actions></td>
              </tr>
            ))}
          </tbody>
        </table>
        <MoreRows shown={shown.length} total={units.length} onMore={() => setLimit(l => l + PAGE)} />
      </div>
    );
  };

  const supplierActions = (s: CatalogueSupplier) => (s.deactivated ? (
    <Button size="sm" variant="quiet" onClick={() => run({ tool: 'manage_supplier', params: { op: 'restore', supplier: s.id }, verb: 'Restore' })}>Restore</Button>
  ) : (
    <>
      <Button size="sm" variant="quiet" onClick={() => setDialog({ kind: 'edit-supplier', supplier: s })}>Edit</Button>
      <Button size="sm" variant="quiet" onClick={() => setDialog({ kind: 'merge-supplier', supplier: s })}>Merge</Button>
      <Button size="sm" variant="quiet" onClick={() => run({ tool: 'manage_supplier', params: { op: 'retire', supplier: s.id }, verb: 'Retire', danger: true })}>Retire</Button>
    </>
  ));

  const suppliersTable = () => {
    if (!suppliers.length) return <PageState kind="empty" title={query ? 'No suppliers match' : 'No suppliers'} />;
    const shown = suppliers.slice(0, limit);
    if (isMobile) {
      return (
        <div className="n-card" data-testid="catalogue-suppliers">
          {shown.map(s => (
            <NarrowRow key={s.id} dim={s.deactivated} title={s.name}
              meta={[s.contact, s.email, s.phone, !s.deactivated && `${s.items} item${s.items === 1 ? '' : 's'}`].filter(Boolean).join(' · ')}
              badges={s.deactivated ? <Badge>Retired {day(s.deactivated_at)}</Badge> : undefined}
              actions={supplierActions(s)} />
          ))}
          <MoreRows shown={shown.length} total={suppliers.length} onMore={() => setLimit(l => l + PAGE)} />
        </div>
      );
    }
    return (
      <div className="n-card" style={{ overflowX: 'auto' }}>
        <table className="n-table" style={{ minWidth: 760 }} data-testid="catalogue-suppliers">
          <thead>
            <tr><th>Supplier</th><th>Contact</th><th>Email</th><th>Customer no.</th><th>Last order</th><th className="num">Items</th><th aria-label="Actions" /></tr>
          </thead>
          <tbody>
            {shown.map(s => (
              <tr key={s.id} style={s.deactivated ? { opacity: 0.7 } : undefined}>
                <td>
                  <span style={{ fontWeight: 500 }}>{s.name}</span>
                  {s.deactivated && <div style={{ marginTop: 2 }}><Badge>Retired {day(s.deactivated_at)}</Badge></div>}
                </td>
                <td>{s.contact || <span style={muted}>—</span>}{s.phone && <div style={{ ...muted, fontSize: 'var(--fs-xs)' }}>{s.phone}</div>}</td>
                <td style={{ overflowWrap: 'anywhere' }}>{s.email || <span style={muted}>—</span>}</td>
                <td style={muted}>{s.customer_number || '—'}</td>
                <td style={{ ...muted, ...nowrap }}>{day(s.last_order_at)}</td>
                <td className="num">{s.deactivated ? '—' : s.items}</td>
                <td><Actions>{supplierActions(s)}</Actions></td>
              </tr>
            ))}
          </tbody>
        </table>
        <MoreRows shown={shown.length} total={suppliers.length} onMore={() => setLimit(l => l + PAGE)} />
      </div>
    );
  };

  const dialogs = () => {
    if (!dialog || !catalogue || !venueId) return null;
    const close = () => setDialog(null);
    switch (dialog.kind) {
      case 'change':
        return <ChangeDialog change={dialog.change} venueId={venueId} onClose={close} onDone={done} />;
      case 'edit-item':
        return <ItemEditDialog item={dialog.item} catalogue={catalogue} onClose={close} onChange={run} />;
      case 'edit-unit':
        return <UnitEditDialog unit={dialog.unit} onClose={close} onChange={run} />;
      case 'edit-supplier':
        return <SupplierEditDialog supplier={dialog.supplier} onClose={close} onChange={run} />;
      case 'merge-item': {
        const from = dialog.item;
        return (
          <MergeDialog title={`Merge ${from.name} into…`} from={from} targets={itemMergeTargets(catalogue.items, from)}
            describe={t => [t.group, t.counting_unit, t.default_supplier].filter(Boolean).join(' · ')}
            explain={`Everything that uses ${from.name} moves to the item you pick — its supplier variants, every received invoice line, open orders, recipes and POS links — then ${from.name} is retired. Only items counted by ${(itemUnitType(from) || 'the same kind').toLowerCase()} are listed.`}
            onClose={close}
            onPick={t => run({ tool: 'manage_stock_item', params: { op: 'merge', item_id: from.id, into_item_id: t.id }, verb: 'Merge', danger: true })} />
        );
      }
      case 'merge-unit': {
        const from = dialog.unit;
        return (
          <MergeDialog title={`Merge ${from.name} into…`} from={from} targets={unitMergeTargets(catalogue.units, from)}
            describe={t => `${t.type} · ${t.ratio} ${baseUnit(t.type)} · ${t.items} item${t.items === 1 ? '' : 's'}`}
            explain={`Items, supplier variants, open orders, recipes and POS links in ${from.name} move to the unit you pick, then ${from.name} is deleted. Only units of the same kind and size (${from.ratio} ${baseUnit(from.type)}) are listed — anything else would change what the quantities mean.`}
            onClose={close}
            onPick={t => run({ tool: 'manage_stock_item', params: { op: 'merge_units', unit_id: from.id, into_unit_id: t.id }, verb: 'Merge', danger: true })} />
        );
      }
      case 'merge-supplier': {
        const from = dialog.supplier;
        return (
          <MergeDialog title={`Merge ${from.name} into…`} from={from} targets={supplierMergeTargets(catalogue.suppliers, from)}
            describe={t => [t.contact, t.email, `${t.items} item${t.items === 1 ? '' : 's'}`].filter(Boolean).join(' · ')}
            explain={`Loaded's own merge: ${from.name}'s item variants, orders and invoices move to the supplier you pick, then ${from.name} is retired. Its aliases and tenders stay behind.`}
            onClose={close}
            onPick={t => run({ tool: 'manage_supplier', params: { op: 'merge', supplier: from.id, into_supplier: t.id }, verb: 'Merge', danger: true })} />
        );
      }
    }
  };

  return (
    <div style={{ width: '100%' }} data-testid="stock-catalogue">
      <PageHeader title={TITLE} meta={meta} actions={actions}>
        <Tabs items={tabs} value={tab} onChange={id => { setTab(id as TabId); setQuery(''); }} label="Catalogue" />
      </PageHeader>
      <div style={{ marginBottom: 12 }}>{toolbar}</div>
      {message && (
        <div role="status" style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 'var(--radius)', background: 'var(--ok-bg, var(--surface-alt))', color: 'var(--text)', fontSize: 'var(--fs-sm)', display: 'flex', justifyContent: 'space-between', gap: 8 }}>
          <span>{message}</span>
          <button type="button" onClick={() => setMessage(null)} style={{ border: 'none', background: 'transparent', color: 'var(--muted)', cursor: 'pointer', font: 'inherit' }}>Dismiss</button>
        </div>
      )}
      {catalogue?.warning && <div style={{ marginBottom: 12, color: 'var(--warn)', fontSize: 'var(--fs-sm)' }}>{catalogue.warning}</div>}
      {loading && catalogue && <div style={{ ...muted, fontSize: 'var(--fs-sm)', marginBottom: 8 }}>Refreshing…</div>}
      {body()}
      {dialogs()}
    </div>
  );
}
