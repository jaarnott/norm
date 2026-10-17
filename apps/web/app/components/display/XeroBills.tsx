'use client';

/**
 * Bills in Xero — the `xero_bills` display component.
 *
 * Renders the document `xero.get_bills` returns (summary tiles, suppliers,
 * one row per bill with a link into Xero). Two surfaces, one component:
 *
 *  - in a conversation the block arrives with the consolidator's result as
 *    `data` and renders it as-is (zero tokens: the model only read the
 *    slimmed summary);
 *  - on the Finance → Bills page (`persistVenue`) it loads itself through
 *    `POST /api/connectors/xero/execute/get_bills`, because the page loader
 *    runs raw GET rows only, not consolidators.
 *
 * Filters re-fetch on both surfaces. Amounts are in the organisation's
 * currency (NZ and AU organisations sit side by side; the tenant decides).
 */

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { ExternalLink, Paperclip, RefreshCw } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import { formatMoney } from '../../lib/format';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import { StatTile, TileRow } from '../functions/enquiries/bits';
import XeroSettingsPanel from './XeroSettingsPanel';

interface Bill {
  invoice_id: string;
  number: string | null;
  reference: string | null;
  supplier: string | null;
  contact_id: string | null;
  date: string | null;
  due_date: string | null;
  status: string | null;
  subtotal: number | null;
  tax: number | null;
  total: number | null;
  amount_due: number;
  amount_paid: number | null;
  currency: string | null;
  overdue: boolean;
  days_overdue: number;
  has_attachment: boolean;
  url: string | null;
}

interface SupplierRow { supplier: string; count: number; total: number; amount_due: number; overdue: number }

interface BillsDoc {
  venue?: string | null;
  organisation?: string | null;
  currency?: string | null;
  as_of?: string;
  filters?: { status?: string; from?: string | null; to?: string | null; window?: string | null; supplier?: string | null };
  summary?: { count: number; total: number; amount_due: number; overdue: number; overdue_count: number; due_7_days: number };
  by_supplier?: SupplierRow[];
  bills?: Bill[];
  shown?: number;
}

interface VenueOption { id: string; name: string }

type StatusFilter = 'unpaid' | 'awaiting_approval' | 'draft' | 'paid' | 'all';
type TileFilter = 'all' | 'overdue' | 'due_soon';

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'unpaid', label: 'Unpaid' },
  { value: 'awaiting_approval', label: 'Awaiting approval' },
  { value: 'draft', label: 'Draft' },
  { value: 'paid', label: 'Paid' },
  { value: 'all', label: 'All' },
];

/** The consolidator's document, wherever the caller put it. */
function extractDoc(data: unknown): BillsDoc | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (Array.isArray(d.bills)) return d as BillsDoc;
  const inner = d.data;
  if (inner && typeof inner === 'object' && Array.isArray((inner as Record<string, unknown>).bills)) {
    return inner as BillsDoc;
  }
  return null;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short', year: 'numeric' });
}

function daysUntil(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const due = new Date(`${iso.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(due.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((due.getTime() - today.getTime()) / 86_400_000);
}

function statusTone(status: string | null): 'neutral' | 'ok' | 'warn' | 'error' | 'info' | 'accent' {
  switch (status) {
    case 'PAID': return 'ok';
    case 'AUTHORISED': return 'info';
    case 'SUBMITTED': return 'warn';
    case 'DRAFT': return 'neutral';
    case 'VOIDED': return 'error';
    default: return 'neutral';
  }
}

function statusLabel(status: string | null): string {
  switch (status) {
    case 'AUTHORISED': return 'Awaiting payment';
    case 'SUBMITTED': return 'Awaiting approval';
    case 'PAID': return 'Paid';
    case 'DRAFT': return 'Draft';
    case 'VOIDED': return 'Voided';
    default: return status || '—';
  }
}

export default function XeroBills({ data, props }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const title = (props?.title as string) || 'Bills in Xero';
  const { isMobile, isTablet } = useBreakpoint();
  const stacked = isMobile || isTablet;

  const initial = useMemo(() => extractDoc(data), [data]);
  const blockVenue = (props?.activeVenueId as string) || ((data as Record<string, unknown>)?.venue_id as string) || null;

  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  // Which venues hold a Xero connection (connect-info). The executor falls
  // back to another venue's credentials inside the organisation when a venue
  // has none; for Xero that is another company's books, so the page never
  // asks for an unconnected venue and says so instead.
  const [connected, setConnected] = useState<Set<string> | null>(null);
  const [selectedVenue, setSelectedVenue] = useState<string | null>(blockVenue || rememberedVenue || null);

  const [doc, setDoc] = useState<BillsDoc | null>(initial);
  const [loading, setLoading] = useState<boolean>(persistVenue && !initial);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusFilter>((initial?.filters?.status as StatusFilter) || 'unpaid');
  const [supplier, setSupplier] = useState<string>(initial?.filters?.supplier || '');
  const [tile, setTile] = useState<TileFilter>('all');
  const [reloadKey, setReloadKey] = useState(0);

  // Venues: the page needs a picker; a chat block only needs them when it
  // has to pick a venue itself (no venue on the block).
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch('/api/venues').then((r) => (r.ok ? r.json() : null)).catch(() => null),
      apiFetch('/api/connectors/xero/connect-info').then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]).then(([d, info]) => {
      if (cancelled) return;
      const list: VenueOption[] = d ? (Array.isArray(d) ? d : (d.venues ?? [])) : [];
      setVenues(list);
      const ok = new Set<string>(
        ((info?.venues ?? []) as { venue_id: string; status: string }[])
          .filter((v) => v.status === 'connected')
          .map((v) => v.venue_id),
      );
      setConnected(ok);
      if (!selectedVenue && list.length) {
        const remembered = rememberedVenue && list.some((v) => v.id === rememberedVenue) ? rememberedVenue : null;
        const firstConnected = list.find((v) => ok.has(v.id))?.id ?? null;
        setSelectedVenue((remembered && ok.has(remembered) ? remembered : null) || firstConnected || remembered || list[0].id);
      }
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = useCallback(async (venueId: string, statusFilter: StatusFilter, supplierFilter: string) => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = { venue_id: venueId, status: statusFilter };
      if (supplierFilter.trim()) params.supplier = supplierFilter.trim();
      const res = await apiFetch('/api/connectors/xero/execute/get_bills', {
        method: 'POST',
        body: JSON.stringify({ params }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(body?.detail || body?.error || `Could not load bills (${res.status})`);
        setDoc(null);
        return;
      }
      const next = extractDoc(body?.data ?? body);
      const docError = (body?.data as Record<string, unknown>)?.error ?? body?.error;
      if (!next) {
        setError(typeof docError === 'string' ? docError : 'Xero returned no bills document');
        setDoc(null);
        return;
      }
      setDoc(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load bills');
      setDoc(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // Page: load whenever the venue or filters change. Block: only on an
  // explicit refresh or filter change (the initial document came with it).
  const [touched, setTouched] = useState(false);
  const venueConnected = !connected || !selectedVenue || connected.has(selectedVenue);

  // The shared active venue is seeded before connect-info answers, so on the
  // page a remembered venue that has no Xero connection gives way to the
  // first venue that does. Not persisted: the person's venue choice stands
  // elsewhere.
  useEffect(() => {
    if (!persistVenue || !connected || !venues.length) return;
    if (selectedVenue && connected.has(selectedVenue)) return;
    const first = venues.find((v) => connected.has(v.id))?.id;
    if (first && first !== selectedVenue) setSelectedVenue(first);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, venues]);

  useEffect(() => {
    if (!selectedVenue) return;
    if (!persistVenue && !touched) return;
    // On the page, wait until connect-info has said which venues are
    // connected: the executor would otherwise fall back to another venue's
    // credentials for an unconnected one.
    if (persistVenue && connected === null) return;
    if (connected && !connected.has(selectedVenue)) {
      setDoc(null);
      setError('not connected');
      setLoading(false);
      return;
    }
    void load(selectedVenue, status, supplier);
    // supplier is applied on Enter / blur via `touched`, not every keystroke
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVenue, status, reloadKey, persistVenue, connected]);

  const handleVenueChange = (id: string) => {
    setSelectedVenue(id);
    setTouched(true);
    if (persistVenue) setActiveVenue(id);
  };

  const applySupplier = () => {
    setTouched(true);
    setReloadKey((k) => k + 1);
  };

  const bills = useMemo(() => {
    const rows = doc?.bills ?? [];
    if (tile === 'overdue') return rows.filter((b) => b.overdue);
    if (tile === 'due_soon') {
      return rows.filter((b) => {
        const d = daysUntil(b.due_date);
        return !b.overdue && b.amount_due > 0 && d !== null && d >= 0 && d <= 7;
      });
    }
    return rows;
  }, [doc, tile]);

  const summary = doc?.summary;
  const currency = doc?.currency || null;
  const money = (n: number | null | undefined) => formatMoney(n ?? 0);

  const venuePicker = venues.length > 1 ? (
    <VenueSelect venues={venues} value={selectedVenue} onChange={handleVenueChange} />
  ) : null;

  const filters = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <select
        className="n-select"
        value={status}
        onChange={(e) => { setStatus(e.target.value as StatusFilter); setTile('all'); setTouched(true); }}
        aria-label="Bill status"
      >
        {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <input
        className="n-input"
        placeholder="Supplier"
        value={supplier}
        onChange={(e) => setSupplier(e.target.value)}
        onBlur={applySupplier}
        onKeyDown={(e) => { if (e.key === 'Enter') applySupplier(); }}
        aria-label="Filter by supplier"
        style={{ width: 160 }}
      />
      <Button size="sm" variant="quiet" icon={RefreshCw} onClick={() => { setTouched(true); setReloadKey((k) => k + 1); }} aria-label="Refresh">
        Refresh
      </Button>
      {venuePicker}
    </div>
  );

  const tiles = summary ? (
    <div style={{ marginBottom: 12 }}>
      <TileRow min={150}>
        <StatTile
          label="Outstanding"
          value={money(summary.amount_due)}
          sub={`${summary.count} bill${summary.count === 1 ? '' : 's'}${currency ? ` · ${currency}` : ''}`}
          onClick={() => setTile('all')}
          selected={tile === 'all'}
        />
        <StatTile
          label="Overdue"
          value={money(summary.overdue)}
          sub={summary.overdue_count ? `${summary.overdue_count} bill${summary.overdue_count === 1 ? '' : 's'}` : 'Nothing overdue'}
          onClick={() => setTile('overdue')}
          selected={tile === 'overdue'}
        />
        <StatTile
          label="Due in 7 days"
          value={money(summary.due_7_days)}
          onClick={() => setTile('due_soon')}
          selected={tile === 'due_soon'}
        />
        <StatTile label="Total billed" value={money(summary.total)} sub={doc?.filters?.window || undefined} />
      </TileRow>
    </div>
  ) : null;

  const openInXero = (b: Bill) => b.url ? (
    <Button size="xs" variant="quiet" icon={ExternalLink} onClick={() => window.open(b.url as string, '_blank', 'noopener')}>
      Open
    </Button>
  ) : null;

  const table = (
    <table className="n-table" style={{ width: '100%' }}>
      <thead>
        <tr>
          <th>Supplier</th>
          <th>Number</th>
          <th>Date</th>
          <th>Due</th>
          <th>Status</th>
          <th className="num">Total</th>
          <th className="num">Amount due</th>
          <th aria-label="Open in Xero" />
        </tr>
      </thead>
      <tbody>
        {bills.map((b) => (
          <tr key={b.invoice_id}>
            <td style={{ fontWeight: 500 }}>
              {b.supplier || '—'}
              {b.has_attachment && <Icon icon={Paperclip} size="dense" tone="muted" label="Has attachment" />}
            </td>
            <td style={{ color: 'var(--text-soft)' }}>{b.number || '—'}{b.reference && b.reference !== b.number ? ` · ${b.reference}` : ''}</td>
            <td>{formatDate(b.date)}</td>
            <td style={{ color: b.overdue ? 'var(--error)' : undefined, fontWeight: b.overdue ? 600 : undefined }}>
              {formatDate(b.due_date)}{b.overdue ? ` (${b.days_overdue}d)` : ''}
            </td>
            <td><Badge tone={statusTone(b.status)}>{statusLabel(b.status)}</Badge></td>
            <td className="num">{money(b.total)}</td>
            <td className="num" style={{ fontWeight: 600 }}>{money(b.amount_due)}</td>
            <td style={{ textAlign: 'right' }}>{openInXero(b)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  const cards = (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {bills.map((b) => (
        <li key={b.invoice_id} className="n-card" style={{ padding: '10px 12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
            <span style={{ fontWeight: 600 }}>{b.supplier || '—'}</span>
            <span style={{ fontWeight: 600 }}>{money(b.amount_due)}</span>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 8px', marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)', alignItems: 'center' }}>
            <Badge tone={statusTone(b.status)}>{statusLabel(b.status)}</Badge>
            <span>{b.number || 'No number'}</span>
            <span style={{ color: b.overdue ? 'var(--error)' : undefined }}>Due {formatDate(b.due_date)}{b.overdue ? ` · ${b.days_overdue}d overdue` : ''}</span>
            <span>Total {money(b.total)}</span>
            {openInXero(b)}
          </div>
        </li>
      ))}
    </ul>
  );

  const inCard = (node: React.ReactNode) => (persistVenue ? node : <div className="n-card">{node}</div>);
  const notConnected = !venueConnected || (!!error && /No credentials|not connected|needs_reconnect|reconnect/i.test(error));
  const body = loading ? (
    inCard(<PageState kind="loading" title="Loading bills from Xero…" />)
  ) : error ? (
    inCard(
      <PageState
        kind="error"
        title={notConnected ? 'This venue is not connected to Xero.' : 'Could not load bills.'}
        detail={notConnected ? 'Connect it under Settings → Connections, then try again.' : error}
      />,
    )
  ) : !doc ? (
    inCard(<PageState kind="empty" title="No bills document." />)
  ) : bills.length === 0 ? (
    inCard(<PageState kind="empty" title={tile === 'all' ? 'No bills match.' : tile === 'overdue' ? 'Nothing overdue.' : 'Nothing due in the next 7 days.'} />)
  ) : stacked ? (
    cards
  ) : (
    <div className={persistVenue ? undefined : 'n-card'} style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      {table}
    </div>
  );

  const meta = loading
    ? ' '
    : doc
      ? [doc.organisation, doc.as_of ? `as of ${formatDate(doc.as_of)}` : null].filter(Boolean).join(' · ')
      : '';

  if (persistVenue) {
    return (
      <div>
        <PageHeader title="Bills" meta={meta} actions={filters} />
        {selectedVenue && venueConnected && (
          <XeroSettingsPanel venueId={selectedVenue} onSaved={() => { setTouched(true); setReloadKey((k) => k + 1); }} />
        )}
        {tiles}
        {body}
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>{title}</h2>
          {meta && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</span>}
        </div>
        {filters}
      </div>
      {tiles}
      {body}
    </div>
  );
}
