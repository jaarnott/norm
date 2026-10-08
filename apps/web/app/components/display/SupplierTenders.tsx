'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import { formatMoney } from '../../lib/format';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import BackLink from '../ui/BackLink';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import Tabs from '../ui/Tabs';
import VenueSelect from '../ui/VenueSelect';

// Supplier tenders from Loaded: agreed price lists with a supplier for a date
// window, plus the review view — tendered price vs what each delivery actually
// charged. Loaded's own OAuth client can't be granted the tenders scopes, so
// reads bridge through the Cook Brothers App connection via
// /api/supplier-tenders/* (routers/supplier_tenders.py — the recipe-write
// path); creating/updating a tender goes through Norm in chat (the
// approve-gated procurement writes on the same CB tools).

interface VenueOption { id: string; name: string }
interface TenderLine {
  id?: string;
  stockItemId?: string;
  stockItemName?: string;
  brandName?: string | null;
  unitId?: string;
  unitName?: string | null;
  unitRatio?: number | null;
  unitCost?: number;
}
interface Tender {
  id?: string;
  supplierId?: string | null;
  supplierName?: string;
  name?: string;
  datestampStart?: string;
  datestampEnd?: string;
  datestampDeleted?: string | null;
  lines?: TenderLine[];
}
interface ReviewOrder {
  referenceNumber?: string | null;
  receivedAt?: string | null;
  invoicedAt?: string | null;
  unitCost?: number | null;
  quantityReceived?: number | null;
  unitName?: string | null;
  unitRatio?: number | null;
  creditRequest?: boolean | null;
  type?: string | null;
}
interface ReviewLine {
  id?: string;
  stockItemName?: string;
  brandName?: string | null;
  unitName?: string | null;
  unitRatio?: number | null;
  unitCost?: number;
  orders?: ReviewOrder[];
}
interface TenderReview {
  id?: string;
  supplierName?: string;
  name?: string;
  lines?: ReviewLine[];
}

const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString() : '—');
const isLive = (t: Tender) => {
  const now = Date.now();
  const s = t.datestampStart ? Date.parse(t.datestampStart) : undefined;
  const e = t.datestampEnd ? Date.parse(t.datestampEnd) : undefined;
  return (s === undefined || s <= now) && (e === undefined || e >= now);
};

// Loaded-style signed money: "-$15.94" rather than "$-15.94".
const signedMoney = (n: number) => `${n < 0 ? '-' : ''}${formatMoney(Math.abs(n))}`;
const dateInput = (iso?: string) => (iso ? iso.slice(0, 10) : '');
const longDay = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—';

const TITLE = 'Supplier Tenders';
const INTRO = 'Agreed supplier price lists from Loaded. Ask Norm to create or change a tender — changes need your approval.';

// Inside its date window today = active; otherwise it has lapsed.
const statusBadge = (t: Tender) => (isLive(t) ? <Badge tone="ok">Active</Badge> : <Badge>Expired</Badge>);

// Variance is tender minus paid: paying over the tender reads red, under it
// green, level (or unknown) muted.
const varianceColor = (v: number | null) =>
  v === null || Math.abs(v) < 0.005 ? 'var(--muted)' : v < 0 ? 'var(--error)' : 'var(--ok)';

// Review-period quick picks: neutral at rest, the tan tint when chosen.
const chip = (active: boolean): React.CSSProperties => ({
  flex: '0 0 auto', padding: '5px 12px', borderRadius: 999, cursor: 'pointer', whiteSpace: 'nowrap',
  fontFamily: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: active ? 600 : 500,
  border: `1px solid ${active ? 'var(--brand-soft)' : 'var(--line)'}`,
  background: active ? 'var(--accent-soft)' : 'var(--surface-alt)',
  color: active ? 'var(--accent)' : 'var(--text)',
});
// A clickable row's label as a real button, so the row is reachable from the
// keyboard. It has no onClick of its own: its click bubbles to the row's.
// Block-level flex, so an icon beside the text can't shift its baseline.
const rowLabel: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 4 };
const rowButton: React.CSSProperties = {
  ...rowLabel, padding: 0, border: 'none', background: 'transparent',
  font: 'inherit', color: 'inherit', textAlign: 'left', cursor: 'pointer',
};
const nowrap: React.CSSProperties = { whiteSpace: 'nowrap' };
const soft: React.CSSProperties = { color: 'var(--text-soft)' };

export default function SupplierTenders({ props }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [venueId, setVenueId] = useState<string | null>((props?.activeVenueId as string) || rememberedVenue || null);
  // Phones, and tablets (whose content area sits beside the menu panel), get
  // cards and a two-column lines table instead of wide tables.
  const { isMobile, isTablet } = useBreakpoint();
  const narrow = isMobile || isTablet;

  const [tenders, setTenders] = useState<Tender[]>([]);
  const [open, setOpen] = useState<Tender | null>(null);
  const [tab, setTab] = useState<'lines' | 'review'>('lines');
  // Review period defaults to the tender's own window (Loaded's behaviour) —
  // "last N days" on an expired tender always looked broken.
  const [reviewStart, setReviewStart] = useState('');
  const [reviewEnd, setReviewEnd] = useState('');
  // Which quick chip the current period came from ('tender' | 7 | 30 | 90);
  // cleared when the dates are edited by hand.
  const [quickPick, setQuickPick] = useState<'tender' | number | null>('tender');
  const [includeCredits, setIncludeCredits] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [review, setReview] = useState<TenderReview | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
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

  const load = useCallback(async (vid: string) => {
    setLoading(true);
    setError(null);
    setOpen(null);
    try {
      const r = await apiFetch('/api/supplier-tenders/list', {
        method: 'POST', body: JSON.stringify({ venue_id: vid }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        // 400 = venue not connected to the Cook Brothers App;
        // 501 = the CB App hasn't shipped its tender tools yet.
        throw new Error(body.detail || `Failed to load tenders (${r.status})`);
      }
      const rows = ((await r.json())?.data as Tender[]) || [];
      setTenders((Array.isArray(rows) ? rows : []).filter(t => !t.datestampDeleted));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tenders');
      setTenders([]);
    }
    setLoading(false);
  }, []);

  useEffect(() => { if (venueId) load(venueId); }, [venueId, load]);

  const loadReview = useCallback(async (vid: string, tenderId: string, startDate: string, endDate: string) => {
    setReviewLoading(true);
    setReview(null);
    setReviewError(null);
    setExpanded(new Set());
    try {
      const r = await apiFetch('/api/supplier-tenders/review', {
        method: 'POST',
        body: JSON.stringify({
          venue_id: vid,
          tender_id: tenderId,
          start_time: new Date(`${startDate}T00:00:00`).toISOString(),
          end_time: new Date(`${endDate}T23:59:59`).toISOString(),
        }),
      });
      if (!r.ok) {
        // Surface the API's own message ("venue not connected to the Cook
        // Brothers App", …) — a swallowed error here used to render as the
        // misleading "No review data for this window".
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `Review failed (${r.status})`);
      }
      setReview((((await r.json())?.data) as TenderReview) || null);
    } catch (e) {
      setReviewError(e instanceof Error ? e.message : 'Review failed');
      setReview(null);
    }
    setReviewLoading(false);
  }, []);

  // First entry to the review tab loads the tender's own period; Apply reloads.
  useEffect(() => {
    if (open?.id && venueId && tab === 'review' && reviewStart && reviewEnd && !review && !reviewLoading && !reviewError) {
      loadReview(venueId, open.id, reviewStart, reviewEnd);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open?.id, venueId, tab, reviewStart, reviewEnd]);

  const changeVenue = (vid: string) => { setVenueId(vid); if (persistVenue) setActiveVenue(vid); };

  const sorted = useMemo(
    () => [...tenders].sort((a, b) => (b.datestampStart || '').localeCompare(a.datestampStart || '')),
    [tenders],
  );

  const openTender = (t: Tender) => {
    setOpen(t); setTab('lines'); setReview(null); setReviewError(null);
    setReviewStart(dateInput(t.datestampStart)); setReviewEnd(dateInput(t.datestampEnd));
    setQuickPick('tender');
  };
  const closeTender = () => { setOpen(null); setReview(null); setReviewError(null); setTab('lines'); };

  const venuePicker = venues.length > 1
    ? <VenueSelect venues={venues} value={venueId} onChange={changeVenue} />
    : null;

  // A page gets the page header and sits straight on the cream frame. In a
  // conversation the same view is a compact card.
  const frame = (body: React.ReactNode) => (persistVenue ? (
    <div style={{ width: '100%' }}>
      <PageHeader title={TITLE} meta={INTRO} actions={venuePicker} />
      {body}
    </div>
  ) : (
    <div className="n-card" style={{ width: '100%', overflow: 'hidden', padding: '12px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
        <div style={{ flex: '1 1 240px', minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{TITLE}</h2>
          <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{INTRO}</div>
        </div>
        {venuePicker}
      </div>
      {body}
    </div>
  ));
  // Tables run edge to edge: on the page straight on the cream, in the card
  // out to its border (the card's own edge closes the last row).
  const tableWrap: React.CSSProperties = persistVenue
    ? { overflowX: 'auto' }
    : { overflowX: 'auto', marginLeft: -14, marginRight: -14, marginBottom: -12 };

  if (loading) return frame(<PageState kind="loading" title="Loading tenders…" />);
  if (error) return frame(<PageState kind="error" title="Couldn’t load tenders" detail={error} />);

  // ── detail view ──────────────────────────────────────────────────────────
  if (open) {
    const Heading = persistVenue ? 'h2' : 'h3';
    const lines = open.lines || [];
    return frame(
      <>
        <BackLink label="All tenders" onClick={closeTender} />
        <div style={{ margin: '8px 0 12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <Heading style={{ margin: 0, fontSize: persistVenue ? 'var(--fs-lg)' : 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>
              {open.name || 'Tender'}
            </Heading>
            {statusBadge(open)}
          </div>
          <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            {[open.supplierName, `${day(open.datestampStart)} – ${day(open.datestampEnd)}`].filter(Boolean).join(' · ')}
          </div>
        </div>

        <Tabs
          label="Tender"
          items={[{ id: 'lines', label: 'Lines' }, { id: 'review', label: 'Price review' }]}
          value={tab}
          onChange={id => setTab(id as 'lines' | 'review')}
        />

        {tab === 'review' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, margin: '12px 0' }}>
            <div role="group" aria-label="Quick periods" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              <button type="button" aria-pressed={quickPick === 'tender'} style={chip(quickPick === 'tender')} onClick={() => {
                const s = dateInput(open.datestampStart), e = dateInput(open.datestampEnd);
                setQuickPick('tender'); setReviewStart(s); setReviewEnd(e);
                if (venueId && open.id) loadReview(venueId, open.id, s, e);
              }}>Tender period</button>
              {[7, 30, 90].map(d => (
                <button key={d} type="button" aria-pressed={quickPick === d} style={chip(quickPick === d)} onClick={() => {
                  const now = new Date();
                  const e = now.toISOString().slice(0, 10);
                  const s = new Date(now.getTime() - d * 86400_000).toISOString().slice(0, 10);
                  setQuickPick(d); setReviewStart(s); setReviewEnd(e);
                  if (venueId && open.id) loadReview(venueId, open.id, s, e);
                }}>Last {d} days</button>
              ))}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', columnGap: 12, rowGap: 8 }}>
              <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>Review period</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, maxWidth: '100%' }}>
                <input type="date" className="n-input" aria-label="Review period from" value={reviewStart}
                  onChange={e => { setQuickPick(null); setReviewStart(e.target.value); }}
                  style={{ flex: '1 1 auto', minWidth: 0 }} />
                <span aria-hidden="true" style={{ color: 'var(--muted)' }}>–</span>
                <input type="date" className="n-input" aria-label="Review period to" value={reviewEnd}
                  onChange={e => { setQuickPick(null); setReviewEnd(e.target.value); }}
                  style={{ flex: '1 1 auto', minWidth: 0 }} />
              </span>
              <Button
                onClick={() => { if (venueId && open?.id && reviewStart && reviewEnd) loadReview(venueId, open.id, reviewStart, reviewEnd); }}>
                Apply
              </Button>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer' }}>
                <input type="checkbox" checked={includeCredits} onChange={e => setIncludeCredits(e.target.checked)}
                  style={{ margin: 0, accentColor: 'var(--accent)', cursor: 'pointer' }} />
                Include credits
              </label>
            </div>
          </div>
        )}

        {tab === 'lines' && (
          lines.length ? (
            <div style={{ ...tableWrap, marginTop: 12 }}>
              <table className="n-table">
                <thead><tr>
                  <th>Item</th>
                  {!narrow && <><th>Brand</th><th>Unit</th></>}
                  <th className="num">Tendered cost</th>
                </tr></thead>
                <tbody>
                  {lines.map((l, i) => {
                    // Narrow screens fold brand and unit under the item name.
                    const sub = [l.brandName, l.unitName].filter(Boolean).join(' · ');
                    return (
                      <tr key={l.id || i}>
                        <td style={{ fontWeight: 500 }}>
                          {l.stockItemName || '—'}
                          {narrow && sub && (
                            <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', fontWeight: 400, color: 'var(--muted)' }}>{sub}</div>
                          )}
                        </td>
                        {!narrow && (
                          <>
                            <td style={soft}>{l.brandName || '—'}</td>
                            <td style={{ ...soft, ...nowrap }}>{l.unitName || '—'}</td>
                          </>
                        )}
                        <td className="num" style={{ ...nowrap, fontWeight: 500 }}>{formatMoney(l.unitCost ?? 0)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : <PageState kind="empty" title="No lines on this tender." />
        )}

        {tab === 'review' && (
          reviewLoading ? <PageState kind="loading" title="Comparing against deliveries…" /> :
          reviewError ? <PageState kind="error" title="Couldn’t load the price review" detail={reviewError} /> :
          !review || !(review.lines || []).length ? <PageState kind="empty" title="No review data for this period." /> : (
            <div style={tableWrap}>
              <table className="n-table">
                <thead><tr>
                  <th>Item name</th><th>Unit</th>
                  <th className="num">Unit price</th>
                  <th className="num">Tender price</th>
                  <th className="num">Variance</th>
                  <th className="num">Quantity</th>
                  <th className="num">Total variance</th>
                </tr></thead>
                <tbody>
                  {[...(review.lines || [])]
                    .sort((a, b) => (a.stockItemName || '').localeCompare(b.stockItemName || ''))
                    .map((l, i) => {
                      const key = l.id || `line-${i}`;
                      const lr = l.unitRatio || 1;
                      const tender = l.unitCost ?? 0;
                      // Per-delivery figures stay in THAT delivery's unit; the
                      // summary normalises everything into the tender line's
                      // unit via the unit ratios (Loaded's own presentation).
                      const orders = (l.orders || [])
                        .filter(o => includeCredits || !o.creditRequest)
                        .map(o => {
                          const or = o.unitRatio || lr;
                          const tenderInUnit = (tender / lr) * or;
                          const paid = o.unitCost ?? null;
                          const variance = paid !== null ? tenderInUnit - paid : null;
                          const qty = o.quantityReceived ?? 0;
                          return {
                            o, tenderInUnit, paid, variance, qty,
                            totalVariance: variance !== null ? variance * qty : 0,
                            normQty: (qty * or) / lr,
                            normPaid: paid !== null ? (paid / or) * lr : null,
                          };
                        });
                      const totalQty = orders.reduce((s, x) => s + x.normQty, 0);
                      const avgPaid = totalQty
                        ? orders.reduce((s, x) => s + (x.normPaid ?? 0) * x.normQty, 0) / totalQty
                        : null;
                      const variance = avgPaid !== null ? tender - avgPaid : null;
                      const totalVariance = orders.reduce((s, x) => s + x.totalVariance, 0);
                      const isOpen = expanded.has(key);
                      const canOpen = orders.length > 0;
                      // The open row and its deliveries read as one block (the
                      // Orders page's expanded row).
                      const cell: React.CSSProperties = { ...nowrap, ...(isOpen && canOpen ? { background: 'var(--selected)' } : {}) };
                      const sub: React.CSSProperties = { ...nowrap, background: 'var(--surface-alt)', fontSize: 'var(--fs-sm)', paddingTop: 7, paddingBottom: 7 };
                      return (
                        <React.Fragment key={key}>
                          <tr
                            onClick={() => setExpanded(prev => {
                              const next = new Set(prev);
                              if (next.has(key)) next.delete(key); else next.add(key);
                              return next;
                            })}
                            style={{ cursor: canOpen ? 'pointer' : 'default' }}
                          >
                            <td style={{ ...cell, fontWeight: 500 }}>
                              {canOpen ? (
                                <button type="button" aria-expanded={isOpen} style={rowButton}>
                                  <Icon icon={isOpen ? ChevronDown : ChevronRight} size="dense" tone="muted"
                                    style={isOpen ? { color: 'var(--text-soft)' } : undefined} />
                                  {l.stockItemName || '—'}
                                </button>
                              ) : (
                                <span style={rowLabel}>
                                  <span aria-hidden="true" style={{ flex: '0 0 14px' }} />
                                  {l.stockItemName || '—'}
                                </span>
                              )}
                            </td>
                            <td style={{ ...cell, ...soft }}>{l.unitName || '—'}</td>
                            <td className="num" style={cell}>{avgPaid !== null ? formatMoney(avgPaid) : '—'}</td>
                            <td className="num" style={cell}>{formatMoney(tender)}</td>
                            <td className="num" style={{ ...cell, color: varianceColor(variance) }}>
                              {variance !== null ? signedMoney(variance) : '—'}
                            </td>
                            <td className="num" style={cell}>{totalQty ? +totalQty.toFixed(2) : '—'}</td>
                            <td className="num" style={{ ...cell, fontWeight: 600, color: varianceColor(totalVariance) }}>
                              {orders.length ? signedMoney(totalVariance) : '—'}
                            </td>
                          </tr>
                          {isOpen && orders.map((x, j) => (
                            <tr key={`${key}-o${j}`}>
                              {/* Indented to the item name, past the chevron. */}
                              <td style={{ ...sub, ...soft, paddingLeft: 30 }}>
                                {longDay(x.o.invoicedAt || x.o.receivedAt)} – Invoice # {x.o.referenceNumber || '—'}
                                {x.o.creditRequest ? <span style={{ marginLeft: 6 }}><Badge tone="info">Credit</Badge></span> : null}
                              </td>
                              <td style={{ ...sub, ...soft }}>{x.o.unitName || l.unitName || '—'}</td>
                              <td className="num" style={sub}>{x.paid !== null ? formatMoney(x.paid) : '—'}</td>
                              <td className="num" style={sub}>{formatMoney(x.tenderInUnit)}</td>
                              <td className="num" style={{ ...sub, color: varianceColor(x.variance) }}>
                                {x.variance !== null ? signedMoney(x.variance) : '—'}
                              </td>
                              <td className="num" style={sub}>{x.qty}</td>
                              <td className="num" style={{ ...sub, color: varianceColor(x.totalVariance) }}>
                                {signedMoney(x.totalVariance)}
                              </td>
                            </tr>
                          ))}
                        </React.Fragment>
                      );
                    })}
                </tbody>
              </table>
            </div>
          )
        )}
      </>,
    );
  }

  // ── list view ────────────────────────────────────────────────────────────
  if (!sorted.length) {
    return frame(
      <PageState kind="empty" title="No tenders for this venue yet" detail="Ask Norm to create one from a supplier price list." />,
    );
  }

  // Narrow page: one card per tender (the mobile page pattern), not a table.
  if (persistVenue && narrow) {
    return frame(
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {sorted.map((t, i) => {
          const n = (t.lines || []).length;
          return (
            <li key={t.id || i}>
              <button type="button" onClick={() => openTender(t)} style={{
                display: 'block', width: '100%', padding: '14px 16px', border: '1px solid var(--line)',
                borderRadius: 'var(--radius-lg)', background: 'var(--bg)', font: 'inherit', color: 'var(--text)',
                textAlign: 'left', cursor: 'pointer', fontVariantNumeric: 'tabular-nums',
              }}>
                <span style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
                  <span style={{ minWidth: 0, fontSize: 'var(--fs-base)', fontWeight: 600 }}>{t.name || '—'}</span>
                  <span style={{ flex: '0 0 auto', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{n} {n === 1 ? 'line' : 'lines'}</span>
                </span>
                <span style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', columnGap: 8, rowGap: 4, marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                  {statusBadge(t)}
                  <span>{t.supplierName || '—'}</span>
                  <span style={nowrap}>{day(t.datestampStart)} – {day(t.datestampEnd)}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>,
    );
  }

  return frame(
    <div style={tableWrap}>
      <table className="n-table">
        <thead><tr>
          <th>Tender</th><th>Supplier</th>
          <th>From</th><th>To</th>
          <th className="num">Lines</th><th>Status</th>
        </tr></thead>
        <tbody>
          {sorted.map((t, i) => (
            <tr key={t.id || i} onClick={() => openTender(t)} style={{ cursor: 'pointer' }}>
              <td style={{ fontWeight: 500 }}>
                <button type="button" style={rowButton}>{t.name || '—'}</button>
              </td>
              <td>{t.supplierName || '—'}</td>
              <td style={{ ...nowrap, ...soft }}>{day(t.datestampStart)}</td>
              <td style={{ ...nowrap, ...soft }}>{day(t.datestampEnd)}</td>
              <td className="num">{(t.lines || []).length}</td>
              <td>{statusBadge(t)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>,
  );
}
