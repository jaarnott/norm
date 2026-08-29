'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { colors } from '../../lib/theme';
import { formatMoney } from '../../lib/format';

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

export default function SupplierTenders({ props }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [venueId, setVenueId] = useState<string | null>((props?.activeVenueId as string) || rememberedVenue || null);

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

  const chip = (active: boolean): React.CSSProperties => ({
    padding: '4px 12px', fontSize: '0.72rem', fontWeight: 600, borderRadius: 14,
    border: `1px solid ${active ? colors.procurement : colors.border}`, cursor: 'pointer',
    background: active ? colors.procurement : '#fff', color: active ? '#fff' : colors.textSecondary,
  });
  const th: React.CSSProperties = { padding: '8px 10px', textAlign: 'left', fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: colors.textMuted, borderBottom: `1px solid ${colors.border}`, whiteSpace: 'nowrap' };
  const td: React.CSSProperties = { padding: '8px 10px', fontSize: '0.82rem', color: colors.textPrimary, borderBottom: `1px solid ${colors.borderLight}`, whiteSpace: 'nowrap' };
  const right: React.CSSProperties = { textAlign: 'right' };

  const header = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
      <h2 style={{ fontSize: '1.15rem', fontWeight: 700, margin: 0 }}>Supplier Tenders</h2>
      {venues.length > 1 && (
        <select value={venueId || ''} onChange={e => changeVenue(e.target.value)}
          style={{ padding: '4px 8px', fontSize: '0.78rem', border: `1px solid ${colors.border}`, borderRadius: 6, fontFamily: 'inherit' }}>
          {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
      )}
      <span style={{ fontSize: '0.72rem', color: colors.textMuted }}>
        Agreed supplier price lists from Loaded. Ask Norm to create or change a tender — changes need your approval.
      </span>
    </div>
  );

  if (loading) return <div>{header}<div style={{ padding: '1rem', color: colors.textMuted }}>Loading tenders…</div></div>;
  if (error) return <div>{header}<div style={{ padding: '1rem', color: '#b91c1c', fontSize: '0.85rem' }}>{error}</div></div>;

  // ── detail view ──────────────────────────────────────────────────────────
  if (open) {
    return (
      <div style={{ width: '100%' }}>
        {header}
        <button onClick={() => { setOpen(null); setReview(null); setReviewError(null); setTab('lines'); }}
          style={{ border: 'none', background: 'none', color: colors.textMuted, cursor: 'pointer', fontSize: '0.8rem', padding: 0, marginBottom: 8, fontFamily: 'inherit' }}>
          ← All tenders
        </button>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 4, flexWrap: 'wrap' }}>
          <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 700 }}>{open.name || 'Tender'}</h3>
          <span style={{ fontSize: '0.8rem', color: colors.textSecondary }}>{open.supplierName}</span>
          <span style={{ fontSize: '0.74rem', color: colors.textMuted }}>{day(open.datestampStart)} – {day(open.datestampEnd)}</span>
          {isLive(open)
            ? <span style={{ fontSize: '0.68rem', fontWeight: 700, color: '#065f46' }}>ACTIVE</span>
            : <span style={{ fontSize: '0.68rem', fontWeight: 700, color: colors.textMuted }}>EXPIRED</span>}
        </div>
        <div style={{ display: 'flex', gap: 8, margin: '10px 0', alignItems: 'center', flexWrap: 'wrap' }}>
          <button style={chip(tab === 'lines')} onClick={() => setTab('lines')}>Lines</button>
          <button style={chip(tab === 'review')} onClick={() => setTab('review')}>Price review</button>
          {tab === 'review' && (
            <>
              <button style={chip(quickPick === 'tender')} onClick={() => {
                const s = dateInput(open.datestampStart), e = dateInput(open.datestampEnd);
                setQuickPick('tender'); setReviewStart(s); setReviewEnd(e);
                if (venueId && open.id) loadReview(venueId, open.id, s, e);
              }}>Tender period</button>
              {[7, 30, 90].map(d => (
                <button key={d} style={chip(quickPick === d)} onClick={() => {
                  const now = new Date();
                  const e = now.toISOString().slice(0, 10);
                  const s = new Date(now.getTime() - d * 86400_000).toISOString().slice(0, 10);
                  setQuickPick(d); setReviewStart(s); setReviewEnd(e);
                  if (venueId && open.id) loadReview(venueId, open.id, s, e);
                }}>Last {d} days</button>
              ))}
              <span style={{ fontSize: '0.74rem', color: colors.textSecondary, marginLeft: 8 }}>Review period</span>
              <input type="date" value={reviewStart} onChange={e => { setQuickPick(null); setReviewStart(e.target.value); }}
                style={{ padding: '3px 6px', fontSize: '0.74rem', border: `1px solid ${colors.border}`, borderRadius: 6, fontFamily: 'inherit' }} />
              <span style={{ color: colors.textMuted }}>–</span>
              <input type="date" value={reviewEnd} onChange={e => { setQuickPick(null); setReviewEnd(e.target.value); }}
                style={{ padding: '3px 6px', fontSize: '0.74rem', border: `1px solid ${colors.border}`, borderRadius: 6, fontFamily: 'inherit' }} />
              <label style={{ fontSize: '0.74rem', color: colors.textSecondary, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                <input type="checkbox" checked={includeCredits} onChange={e => setIncludeCredits(e.target.checked)} />
                Include credits
              </label>
              <button
                onClick={() => { if (venueId && open?.id && reviewStart && reviewEnd) loadReview(venueId, open.id, reviewStart, reviewEnd); }}
                style={{ padding: '4px 14px', fontSize: '0.74rem', fontWeight: 600, borderRadius: 6, border: 'none', cursor: 'pointer', background: colors.procurement, color: '#fff' }}>
                Apply
              </button>
            </>
          )}
        </div>

        {tab === 'lines' && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%' }}>
              <thead><tr>
                <th style={th}>Item</th><th style={th}>Brand</th><th style={th}>Unit</th>
                <th style={{ ...th, ...right }}>Tendered cost</th>
              </tr></thead>
              <tbody>
                {(open.lines || []).map((l, i) => (
                  <tr key={l.id || i}>
                    <td style={td}>{l.stockItemName || '—'}</td>
                    <td style={td}>{l.brandName || '—'}</td>
                    <td style={td}>{l.unitName || '—'}</td>
                    <td style={{ ...td, ...right, fontWeight: 600 }}>{formatMoney(l.unitCost ?? 0)}</td>
                  </tr>
                ))}
                {!(open.lines || []).length && (
                  <tr><td style={{ ...td, color: colors.textMuted }} colSpan={4}>No lines on this tender.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'review' && (
          reviewLoading ? <div style={{ padding: '1rem', color: colors.textMuted }}>Comparing against deliveries…</div> :
          reviewError ? <div style={{ padding: '1rem', color: '#b91c1c', fontSize: '0.85rem' }}>{reviewError}</div> :
          !review ? <div style={{ padding: '1rem', color: colors.textMuted }}>No review data for this period.</div> : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                <thead><tr>
                  <th style={{ ...th, width: 28 }} />
                  <th style={th}>Item Name</th><th style={th}>Unit</th>
                  <th style={{ ...th, ...right }}>Unit Price</th>
                  <th style={{ ...th, ...right }}>Tender Price</th>
                  <th style={{ ...th, ...right }}>Variance</th>
                  <th style={{ ...th, ...right }}>Quantity</th>
                  <th style={{ ...th, ...right }}>Total Variance</th>
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
                      const vColor = (v: number | null) =>
                        v === null || Math.abs(v) < 0.005 ? colors.textMuted : v < 0 ? '#b91c1c' : '#065f46';
                      return (
                        <React.Fragment key={key}>
                          <tr
                            onClick={() => setExpanded(prev => {
                              const next = new Set(prev);
                              if (next.has(key)) next.delete(key); else next.add(key);
                              return next;
                            })}
                            style={{ cursor: orders.length ? 'pointer' : 'default', background: isOpen ? '#faf8f5' : undefined }}
                          >
                            <td style={{ ...td, color: colors.textMuted }}>{orders.length ? (isOpen ? '▾' : '▸') : ''}</td>
                            <td style={{ ...td, fontWeight: 600 }}>{l.stockItemName || '—'}</td>
                            <td style={td}>{l.unitName || '—'}</td>
                            <td style={{ ...td, ...right }}>{avgPaid !== null ? formatMoney(avgPaid) : '—'}</td>
                            <td style={{ ...td, ...right }}>{formatMoney(tender)}</td>
                            <td style={{ ...td, ...right, color: vColor(variance) }}>
                              {variance !== null ? signedMoney(variance) : '—'}
                            </td>
                            <td style={{ ...td, ...right }}>{totalQty ? +totalQty.toFixed(2) : '—'}</td>
                            <td style={{ ...td, ...right, fontWeight: 700, color: vColor(totalVariance), textDecoration: 'underline' }}>
                              {orders.length ? signedMoney(totalVariance) : '—'}
                            </td>
                          </tr>
                          {isOpen && orders.map((x, j) => (
                            <tr key={`${key}-o${j}`} style={{ background: '#fbfaf8' }}>
                              <td style={td} />
                              <td style={{ ...td, paddingLeft: 24, fontSize: '0.76rem', color: '#2a6bb5' }}>
                                {longDay(x.o.invoicedAt || x.o.receivedAt)} – Invoice # {x.o.referenceNumber || '—'}
                                {x.o.creditRequest ? <span style={{ marginLeft: 6, fontSize: '0.66rem', fontWeight: 700, color: '#8a6d3b' }}>CREDIT</span> : null}
                              </td>
                              <td style={{ ...td, fontSize: '0.76rem' }}>{x.o.unitName || l.unitName || '—'}</td>
                              <td style={{ ...td, ...right, fontSize: '0.76rem' }}>{x.paid !== null ? formatMoney(x.paid) : '—'}</td>
                              <td style={{ ...td, ...right, fontSize: '0.76rem' }}>{formatMoney(x.tenderInUnit)}</td>
                              <td style={{ ...td, ...right, fontSize: '0.76rem', color: vColor(x.variance) }}>
                                {x.variance !== null ? signedMoney(x.variance) : '—'}
                              </td>
                              <td style={{ ...td, ...right, fontSize: '0.76rem' }}>{x.qty}</td>
                              <td style={{ ...td, ...right, fontSize: '0.76rem', color: vColor(x.totalVariance) }}>
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
      </div>
    );
  }

  // ── list view ────────────────────────────────────────────────────────────
  return (
    <div style={{ width: '100%' }}>
      {header}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead><tr>
            <th style={th}>Tender</th><th style={th}>Supplier</th>
            <th style={th}>From</th><th style={th}>To</th>
            <th style={{ ...th, ...right }}>Lines</th><th style={th}>Status</th>
          </tr></thead>
          <tbody>
            {sorted.map((t, i) => (
              <tr key={t.id || i} onClick={() => {
                setOpen(t); setTab('lines'); setReview(null); setReviewError(null);
                setReviewStart(dateInput(t.datestampStart)); setReviewEnd(dateInput(t.datestampEnd));
                setQuickPick('tender');
              }}
                style={{ cursor: 'pointer' }}
                onMouseEnter={e => (e.currentTarget.style.background = '#faf8f5')}
                onMouseLeave={e => (e.currentTarget.style.background = '')}>
                <td style={{ ...td, fontWeight: 600 }}>{t.name || '—'}</td>
                <td style={td}>{t.supplierName || '—'}</td>
                <td style={td}>{day(t.datestampStart)}</td>
                <td style={td}>{day(t.datestampEnd)}</td>
                <td style={{ ...td, ...right }}>{(t.lines || []).length}</td>
                <td style={td}>{isLive(t)
                  ? <span style={{ fontSize: '0.68rem', fontWeight: 700, color: '#065f46' }}>ACTIVE</span>
                  : <span style={{ fontSize: '0.68rem', fontWeight: 700, color: colors.textMuted }}>EXPIRED</span>}</td>
              </tr>
            ))}
            {!sorted.length && (
              <tr><td style={{ ...td, color: colors.textMuted }} colSpan={6}>
                No tenders for this venue yet — ask Norm to create one from a supplier price list.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
