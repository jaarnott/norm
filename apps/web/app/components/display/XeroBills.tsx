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

import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
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
import ConnectPrompt from './ConnectPrompt';

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
  const loadConnectInfo = useCallback(async (): Promise<Set<string>> => {
    const info = await apiFetch('/api/connectors/xero/connect-info')
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
    const ok = new Set<string>(
      ((info?.venues ?? []) as { venue_id: string; status: string }[])
        .filter((v) => v.status === 'connected')
        .map((v) => v.venue_id),
    );
    setConnected(ok);
    return ok;
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch('/api/venues').then((r) => (r.ok ? r.json() : null)).catch(() => null),
      loadConnectInfo(),
    ]).then(([d, ok]) => {
      if (cancelled) return;
      const list: VenueOption[] = d ? (Array.isArray(d) ? d : (d.venues ?? [])) : [];
      setVenues(list);
      // Nothing chosen anywhere yet (no block venue, no remembered venue):
      // start on the first connected venue, else the first venue.
      if (!selectedVenue && !propVenue && !rememberedVenue && list.length) {
        setSelectedVenue(list.find((v) => ok.has(v.id))?.id ?? list[0].id);
      }
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Only the latest request may land: switching venue while a load is in
  // flight must never leave the previous venue's bills under the new name.
  const loadSeq = useRef(0);
  const load = useCallback(async (venueId: string, statusFilter: StatusFilter, supplierFilter: string) => {
    const seq = ++loadSeq.current;
    const stale = () => seq !== loadSeq.current;
    setLoading(true);
    setError(null);
    setDoc(null);
    try {
      const params: Record<string, string> = { venue_id: venueId, status: statusFilter };
      if (supplierFilter.trim()) params.supplier = supplierFilter.trim();
      const res = await apiFetch('/api/connectors/xero/execute/get_bills', {
        method: 'POST',
        body: JSON.stringify({ params }),
      });
      const body = await res.json().catch(() => ({}));
      if (stale()) return;
      if (!res.ok) {
        setError(body?.detail || body?.error || `Could not load bills (${res.status})`);
        return;
      }
      const next = extractDoc(body?.data ?? body);
      const docError = (body?.data as Record<string, unknown>)?.error ?? body?.error;
      if (!next) {
        setError(typeof docError === 'string' ? docError : 'Xero returned no bills document');
        return;
      }
      setDoc(next);
    } catch (e) {
      if (stale()) return;
      setError(e instanceof Error ? e.message : 'Could not load bills');
    } finally {
      if (!stale()) setLoading(false);
    }
  }, []);

  // Page: load whenever the venue or filters change. Block: only on an
  // explicit refresh or filter change (the initial document came with it).
  const [touched, setTouched] = useState(false);
  const venueConnected = !connected || !selectedVenue || connected.has(selectedVenue);

  // The page follows the app's venue (FunctionalPage passes it once the app's
  // venues have settled, and again whenever it changes). A remembered venue
  // with no Xero connection stays selected and gets the connect prompt — the
  // page never jumps to a different venue on its own.
  const userPicked = useRef(false);
  const propVenue = (props?.activeVenueId as string) || null;
  useEffect(() => {
    if (!persistVenue || !propVenue || userPicked.current) return;
    if (propVenue !== selectedVenue) setSelectedVenue(propVenue);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propVenue, persistVenue]);

  useEffect(() => {
    if (!selectedVenue) return;
    if (!persistVenue && !touched) return;
    // On the page, wait until connect-info has said which venues are
    // connected: the executor would otherwise fall back to another venue's
    // credentials for an unconnected one.
    if (persistVenue && connected === null) return;
    if (connected && !connected.has(selectedVenue)) {
      loadSeq.current += 1; // an in-flight load for the previous venue may not land
      setDoc(null);
      setError('not connected');
      setLoading(false);
      return;
    }
    void load(selectedVenue, status, supplier);
    // supplier is applied on Enter / blur via `touched`, not every keystroke
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVenue, status, reloadKey, persistVenue, connected]);

  // The connect card below runs the OAuth popup; its callback page posts
  // `oauth-complete` to this window. Re-read which venues are connected and
  // load the bills without the person having to leave the page.
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type !== 'oauth-complete') return;
      if (!/xero/i.test(String(event.data?.connector || ''))) return;
      void loadConnectInfo().then(() => { setTouched(true); setReloadKey((k) => k + 1); });
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [loadConnectInfo]);

  const handleVenueChange = (id: string) => {
    userPicked.current = true;
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
      <Button size="sm" variant="quiet" icon={RefreshCw} onClick={() => { setTouched(true); void loadConnectInfo().then(() => setReloadKey((k) => k + 1)); }} aria-label="Refresh">
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
  const venueName = venues.find((v) => v.id === selectedVenue)?.name || 'This venue';
  const settling = persistVenue && (connected === null || venues.length === 0);
  // Not connected: the same connect card chat and Settings → Connections use,
  // right here — the person signs in to Xero, picks the organisation, and the
  // page loads the bills when the popup reports back.
  const connectHere = (
    <ConnectPrompt
      connector="xero"
      venueId={selectedVenue}
      compact={!persistVenue}
      title={`${venueName} is not connected to Xero`}
      detail="Connect it below: sign in to Xero as someone with admin on the organisation and choose it."
    />
  );
  const body = loading || settling ? (
    inCard(<PageState kind="loading" title="Loading bills from Xero…" />)
  ) : notConnected ? (
    inCard(connectHere)
  ) : error ? (
    inCard(<PageState kind="error" title="Could not load bills." detail={error} />)
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
