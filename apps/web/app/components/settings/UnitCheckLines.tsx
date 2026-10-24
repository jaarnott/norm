'use client';

import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { FileText } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import { requestSidePanel } from '../../lib/sidePanel';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import InvoicePdfPane from './InvoicePdfPane';

/**
 * The daily unit check (owner, 18 Oct 2026).
 *
 * Each morning Norm compares every line it received the day before with the
 * invoice it read and with its own catalogue. It lists the lines it thinks are
 * booked in the wrong unit (corrections), and the lines it booked on an answer
 * it wasn't sure of (to confirm). Nothing is changed in Loaded until a person
 * acts here; applying or changing touches only the line's unit — the invoice's
 * quantities and dollars stay as received. Confirming or changing a "to
 * confirm" line is kept in Norm's catalogue, so the product isn't asked again.
 *
 * Since 19 Oct 2026 the lines are shown in Settings → Invoice activity, under
 * "Units to look at", filtered by the page's dates and venues.
 *
 * Each line says where the unit it is booked in came from (a person's change
 * on the card, a decision on Norm's suggestion, Norm's reading, Loaded's
 * draft), and each invoice's copy opens beside its lines, as on the receive
 * card (owner, 24 Oct 2026). An invoice with no copy (an Ordermentum order)
 * opens the one Norm draws from its data instead.
 */

export interface Proposal {
  id: string;
  run_id: string;
  venue_name: string | null;
  invoice_id: string;
  invoice_number: string | null;
  supplier_name: string | null;
  invoice_date: string | null;
  item_name: string | null;
  code: string | null;
  printed: { description?: string; quantity?: number; unit?: string | null; unit_of_measure?: string | null; unit_price_ex_tax?: number } | null;
  quantity: number | null;
  unit_cost: number | null;
  booked_unit_name: string | null;
  invoice_says: string | null;
  catalogue_says: string | null;
  proposed_unit_name: string | null;
  reason: string | null;
  kind: 'correct' | 'confirm';
  norm_estimate: { unit?: string | null; confidence?: string | null; why?: string | null } | null;
  status: 'open' | 'applied' | 'confirmed' | 'dismissed' | 'failed';
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
  error: string | null;
  venue_id?: string | null;
  /** Where the booked unit came from, off the invoice's record in Norm. */
  origin?: { who: 'person' | 'norm' | 'loaded' | 'unknown'; text: string; at: string | null } | null;
  /** The invoice's copy in Loaded, to show beside its lines. */
  file_id?: string | null;
}

/** Who set the booked unit, as a badge beside "where it came from". */
const ORIGIN: Record<NonNullable<Proposal['origin']>['who'], { label: string; tone: BadgeTone } | null> = {
  person: { label: "A person's choice", tone: 'info' },
  norm: { label: 'Norm', tone: 'neutral' },
  loaded: { label: 'Loaded', tone: 'neutral' },
  unknown: null,
};

const STATUS: Record<Proposal['status'], { label: string; tone: BadgeTone }> = {
  open: { label: 'To review', tone: 'accent' },
  applied: { label: 'Applied', tone: 'ok' },
  confirmed: { label: 'Confirmed', tone: 'ok' },
  dismissed: { label: 'Dismissed', tone: 'neutral' },
  failed: { label: "Couldn't apply", tone: 'error' },
};

function when(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
}

function money(v: number | null | undefined): string {
  return typeof v === 'number' ? `$${v.toFixed(2)}` : '';
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 'var(--fs-xs, 11px)', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.3 }}>{label}</div>
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)', overflowWrap: 'anywhere' }}>{children}</div>
    </div>
  );
}

type Action = 'apply' | 'confirm' | 'change' | 'dismiss';

export function ProposalCard({ p, onDone, onSettled }: { p: Proposal; onDone: (p: Proposal) => void; onSettled: () => void }) {
  const [busy, setBusy] = useState<Action | null>(null);
  const [noting, setNoting] = useState(false);
  const [note, setNote] = useState('');
  const [choosing, setChoosing] = useState(false);
  const [units, setUnits] = useState<{ id: string; name: string; kind: string | null }[] | null>(null);
  const [unitId, setUnitId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const open = p.status === 'open' || p.status === 'failed';
  const confirm = p.kind === 'confirm';

  const act = async (kind: Action) => {
    setBusy(kind);
    setError(null);
    try {
      const body = kind === 'dismiss' ? { note: note || null } : kind === 'change' ? { unit_id: unitId } : {};
      const res = await apiFetch(`/api/supplier-invoice-specs/dojo/unit-check/proposals/${p.id}/${kind}`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setNoting(false);
      setChoosing(false);
      // Confirming or changing settles the product's other open lines too.
      if (kind === 'confirm' || kind === 'change') onSettled();
      else onDone(data as Proposal);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    } finally {
      setBusy(null);
    }
  };

  const choose = async () => {
    setChoosing(true);
    setError(null);
    if (units) return;
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/dojo/unit-check/proposals/${p.id}/units`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setUnits(data.units || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the units');
      setChoosing(false);
    }
  };

  const printed = p.printed;
  return (
    <div className="n-card" style={{ padding: '12px 14px', marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--fs-base)', color: 'var(--text)', overflowWrap: 'anywhere' }}>
            {p.item_name || 'Line'}{p.code ? <span style={{ fontWeight: 400, color: 'var(--muted)' }}> · {p.code}</span> : null}
          </div>
          {printed?.description && printed.description !== p.item_name && (
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', overflowWrap: 'anywhere' }}>
              Printed: “{printed.description}”
            </div>
          )}
        </div>
        {confirm && open && <Badge tone="warn" title="The invoice shows no size and Norm wasn't sure of this product">Norm wasn&rsquo;t sure</Badge>}
        <Badge tone={STATUS[p.status].tone}>{STATUS[p.status].label}</Badge>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '8px 16px', marginBottom: 8 }}>
        <Field label="Quantity · price">{p.quantity ?? ''}{p.unit_cost != null ? ` × ${money(p.unit_cost)}` : ''}</Field>
        <Field label="Invoice shows">{p.invoice_says || '—'}</Field>
        {confirm ? (
          <Field label="Norm's estimate">
            {p.norm_estimate?.unit ? `${p.norm_estimate.unit} (${p.norm_estimate.confidence || 'low'} confidence)` : 'none'}
          </Field>
        ) : (
          <Field label="Norm's catalogue">{p.catalogue_says || '—'}</Field>
        )}
        <Field label="Booked as"><span style={{ textDecoration: p.status === 'applied' ? 'line-through' : undefined }}>{p.booked_unit_name || '—'}</span></Field>
        {(!confirm || p.status === 'applied') && <Field label={p.status === 'applied' ? 'Now' : 'Proposed'}><strong>{p.proposed_unit_name || '—'}</strong></Field>}
      </div>
      {p.origin && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginBottom: 8, display: 'flex', gap: 6, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <span style={{ color: 'var(--muted)' }}>Where &lsquo;{p.booked_unit_name || 'the unit'}&rsquo; came from:</span>
          {ORIGIN[p.origin.who] && <Badge tone={ORIGIN[p.origin.who]!.tone}>{ORIGIN[p.origin.who]!.label}</Badge>}
          <span>{p.origin.text}{p.origin.at ? ` ${when(p.origin.at)}` : ''}</span>
        </div>
      )}
      {p.reason && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginBottom: 8 }}>{p.reason}</div>}
      {(p.error || error) && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)', marginBottom: 8 }}>{error || p.error}</div>
      )}
      {!open && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          {STATUS[p.status].label} by {p.decided_by || 'someone'} {when(p.decided_at)}{p.note ? ` — “${p.note}”` : ''}
        </div>
      )}
      {open && noting && (
        <div style={{ marginBottom: 8 }}>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
            placeholder="Why it's not a mistake (optional)"
            style={{ width: '100%', boxSizing: 'border-box', fontSize: 'var(--fs-sm)', padding: 8, borderRadius: 8, border: '1px solid var(--line)', background: 'transparent', color: 'var(--text)' }} />
        </div>
      )}
      {open && choosing && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 8 }}>
          <select className="n-select" aria-label="The right unit" value={unitId} onChange={(e) => setUnitId(e.target.value)}
            disabled={!units} style={{ flex: 1, minWidth: 160 }}>
            <option value="">{units ? 'Choose the right unit…' : 'Loading the venue’s units…'}</option>
            {(units || []).map((u) => <option key={u.id} value={u.id}>{u.name}{u.kind ? ` (${u.kind.toLowerCase()})` : ''}</option>)}
          </select>
          <Button size="sm" variant="quiet" onClick={() => setChoosing(false)} disabled={busy !== null}>Cancel</Button>
          <Button size="sm" variant="primary" onClick={() => act('change')} disabled={busy !== null || !unitId}
            title="Book the line in this unit in Loaded (only the unit changes), and keep it as Norm's answer for the product">
            {busy === 'change' ? 'Changing…' : 'Change'}
          </Button>
        </div>
      )}
      {open && !choosing && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, flexWrap: 'wrap' }}>
          {noting ? (
            <>
              <Button size="sm" variant="quiet" onClick={() => setNoting(false)} disabled={busy !== null}>Cancel</Button>
              <Button size="sm" onClick={() => act('dismiss')} disabled={busy !== null}>
                {busy === 'dismiss' ? 'Dismissing…' : 'Dismiss'}
              </Button>
            </>
          ) : (
            <>
              <Button size="sm" onClick={() => setNoting(true)} disabled={busy !== null}>Dismiss</Button>
              <Button size="sm" onClick={choose} disabled={busy !== null}>Change to…</Button>
              {confirm ? (
                <Button size="sm" variant="primary" onClick={() => act('confirm')} disabled={busy !== null || !p.booked_unit_name}
                  title="The unit is right. Norm keeps it for this product; nothing changes in Loaded.">
                  {busy === 'confirm' ? 'Confirming…' : `Confirm: ${p.booked_unit_name || ''}`}
                </Button>
              ) : (
                <Button size="sm" variant="primary" onClick={() => act('apply')} disabled={busy !== null || !p.proposed_unit_name}
                  title="Change this line's unit in Loaded. Only the unit changes; quantities and dollars stay as received.">
                  {busy === 'apply' ? 'Applying…' : `Apply: ${p.proposed_unit_name || ''}`}
                </Button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** An invoice's copy beside its lines, as on the receive card: half the width
 *  each, the copy sticky while the lines scroll; narrower, it stacks above.
 *  The Settings menu folds away while it is open, as the card's copy does. */
function CopyBeside({ src, title, label, onClose, children }: {
  src: string;
  title: string;
  label: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    requestSidePanel(true);
    return () => requestSidePanel(false);
  }, []);
  const rowRef = useRef<HTMLDivElement>(null);
  const [sideBySide, setSideBySide] = useState(true);
  useEffect(() => {
    const el = rowRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([e]) => setSideBySide(e.contentRect.width >= 2 * 420 + 12));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // Its own tab: opened inside the click (a popup opened after the fetch is
  // blocked), then pointed at the copy once it has arrived.
  const popOut = async () => {
    const w = window.open('about:blank', '_blank');
    try {
      const r = await apiFetch(src);
      if (!r.ok) throw new Error(`Error ${r.status}`);
      const url = URL.createObjectURL(await r.blob());
      if (w && !w.closed) w.location.replace(url);
      else {
        const a = document.createElement('a');
        a.href = url;
        a.download = `invoice-${label || 'copy'}.pdf`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
      if (w && !w.closed) w.close();
    }
  };
  return (
    <div ref={rowRef} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <div style={{ flex: '1 1 420px', minWidth: 0, ...(sideBySide ? { position: 'sticky' as const, top: 8 } : {}) }}>
        <InvoicePdfPane src={src} title={title} onClose={onClose} onPopOut={popOut} />
      </div>
      <div style={{ flex: '1 1 420px', minWidth: 0 }}>{children}</div>
    </div>
  );
}

/** The flagged lines, grouped by venue and invoice. */
export function UnitCheckLines({ proposals, onReplace, onReload }: {
  proposals: Proposal[];
  onReplace: (p: Proposal) => void;
  /** Confirming or changing settles the product's other open lines too. */
  onReload: () => void;
}) {
  // One copy open at a time: the invoice whose lines it sits beside.
  const [copyFor, setCopyFor] = useState<string | null>(null);
  const groups = useMemo(() => {
    const out: { venue: string; invoices: { key: string; label: string; items: Proposal[] }[] }[] = [];
    for (const p of proposals) {
      const venue = p.venue_name || 'Venue';
      let g = out.find((x) => x.venue === venue);
      if (!g) { g = { venue, invoices: [] }; out.push(g); }
      const key = `${p.venue_id || venue}:${p.invoice_id}`;
      let inv = g.invoices.find((x) => x.key === key);
      if (!inv) {
        inv = { key, label: [p.supplier_name, p.invoice_number, p.invoice_date].filter(Boolean).join(' · '), items: [] };
        g.invoices.push(inv);
      }
      inv.items.push(p);
    }
    return out;
  }, [proposals]);

  return (
    <>
      {groups.map((g) => (
        <Fragment key={g.venue}>
          <h4 style={{ margin: '14px 0 8px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>{g.venue}</h4>
          {g.invoices.map((inv) => {
            const first = inv.items[0];
            const venue = encodeURIComponent(first.venue_id || '');
            const src = !first.venue_id
              ? null
              : first.file_id
                ? `/api/invoice-fixes/file?venue_id=${venue}&file_id=${encodeURIComponent(first.file_id)}`
                : `/api/invoice-fixes/norm-copy?venue_id=${venue}&invoice_id=${encodeURIComponent(first.invoice_id)}`;
            const what = first.file_id ? 'the invoice copy' : 'Norm’s copy';
            const canView = !!src;
            const open = canView && copyFor === inv.key;
            const cards = inv.items.map((p) => <ProposalCard key={p.id} p={p} onDone={onReplace} onSettled={onReload} />);
            return (
              <div key={inv.key} style={{ marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                  <div style={{ flex: '1 1 200px', minWidth: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{inv.label}</div>
                  {canView && (
                    <Button size="sm" variant="quiet" icon={FileText} aria-pressed={open}
                      title={first.file_id ? undefined : 'No invoice copy came with this order: Norm rebuilt one from its data'}
                      onClick={() => setCopyFor(open ? null : inv.key)}>
                      {open ? `Hide ${what}` : `View ${what}`}
                    </Button>
                  )}
                </div>
                {open ? (
                  <CopyBeside src={src!} title={first.file_id ? 'Invoice copy' : 'Norm’s copy'} label={first.invoice_number || ''} onClose={() => setCopyFor(null)}>
                    {cards}
                  </CopyBeside>
                ) : cards}
              </div>
            );
          })}
        </Fragment>
      ))}
    </>
  );
}
