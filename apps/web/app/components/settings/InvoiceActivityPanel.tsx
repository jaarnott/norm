'use client';

import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, FileText, RefreshCw } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import { requestSidePanel } from '../../lib/sidePanel';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import ReceiveInvoiceEditor from '../display/ReceiveInvoiceEditor';
import InvoicePdfPane from './InvoicePdfPane';
import { CopyableId } from '../threads/ActivityTimeline';
import { UnitCheckLines, type Proposal } from './UnitCheckLines';
import {
  activityTags,
  createdWords,
  deleteNote,
  filtersFromLink,
  lastDays,
  modeShort,
  outcomeShort,
  outcomeWords,
  plural,
  rowLevel,
  type ActivityRow,
} from './invoiceActivity';

/**
 * Invoice activity (owner, 19 Oct 2026): what Norm did with each invoice.
 *
 * It replaced the Dojo, which loaded every venue's outstanding invoices from
 * Loaded so a person could review them by hand, and often failed doing it.
 * Norm handles invoices itself now, so this is a review of what happened, with
 * the things that might be worth checking picked out at the top. Nothing here
 * is a to-do list. It reads Norm's own records only, so it opens quickly
 * whatever the number of venues.
 */

interface Venue { id: string; name: string | null; organization: string | null }

interface Kpis {
  invoices: number;
  received: number;
  received_by_norm: number;
  received_by_person: number;
  changed_by_person: number;
  not_finished: number;
  deleted: number;
  unusual_deletions: number;
  created_invoices: number;
  created: Record<string, number>;
}

interface Data {
  period: { start: string; end: string };
  venues: Venue[];
  kpis: Kpis;
  rows: ActivityRow[];
  total: number;
  offset: number;
}

interface TimelineItem extends ActivityRow {
  type: 'outcome' | 'event';
  first_at?: string | null;
}

const PAGE = 100;

const OUTCOMES: [string, string][] = [
  ['', 'Everything'],
  ['received_by_norm', 'Received by Norm'],
  ['received_by_person', 'Received by a person'],
  ['changed_by_person', 'Changed by a person'],
  ['deleted', 'Deleted'],
  ['unusual_deletions', 'Unusual deletions'],
  ['created', 'Created something in Loaded'],
  ['not_finished', 'Stopped, waiting or failed'],
  ['stopped', 'Stopped'],
  ['waiting', 'Waiting for approval'],
  ['failed', 'Failed'],
];

const MODES: [string, string][] = [
  ['', 'Any mode'],
  ['autopilot', 'Autopilot'],
  ['approve_fixes', 'Approve fixes'],
  ['approve_all', 'Approve all'],
  ['app', "Handled in Norm's app"],
  ['claude', 'Handled on a card in Claude'],
];

function when(iso: string | null | undefined): string {
  if (!iso) return '';
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
}

function money(v: number | null | undefined): string {
  return typeof v === 'number' ? `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '';
}

const muted: React.CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' };
const sectionTitle: React.CSSProperties = { margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' };

/** A card at the top: a count for the period that filters the stream. */
function KpiCard({ label, value, note, active, onClick, extra }: {
  label: string;
  value: number;
  note?: string;
  active: boolean;
  onClick: () => void;
  extra?: React.ReactNode;
}) {
  return (
    <div className="n-card" style={{
      display: 'flex', flexDirection: 'column', padding: 0, minWidth: 0,
      ...(active ? { borderColor: 'var(--accent)', boxShadow: '0 0 0 1px var(--accent)', background: 'var(--accent-soft)' } : {}),
    }}>
      <button type="button" aria-pressed={active} onClick={onClick}
        title={active ? 'Show every invoice again' : 'Show only these invoices in the stream'}
        style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-start', textAlign: 'left', padding: '12px 14px 10px', border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit', color: 'var(--text)' }}>
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{label}</div>
        <div style={{ fontSize: 'var(--fs-2xl)', fontWeight: 700, lineHeight: 1.25, fontVariantNumeric: 'tabular-nums' }}>{value.toLocaleString()}</div>
        {note && <div style={{ fontSize: 'var(--fs-xs, 11px)', color: 'var(--muted)', marginTop: 2 }}>{note}</div>}
      </button>
      {extra && <div style={{ padding: '0 14px 10px' }}>{extra}</div>}
    </div>
  );
}

interface Detail {
  items: TimelineItem[];
  unit_check: Proposal[];
  document: { id: string; file_id: string | null; reviewed: boolean; received: boolean; deleted: boolean } | null;
}

/**
 * One invoice opened in place: what happened, oldest first, then the receive
 * card exactly as Norm kept it, with the invoice copy beside it. Nothing is
 * reviewed again (the card's `record` mode).
 */
function InvoiceDetail({ row }: { row: ActivityRow }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The copy on its own (no card kept): shown on request, and the side
  // menu folds away for the width, as the card's own copy button does.
  const [copyOpen, setCopyOpen] = useState(false);
  useEffect(() => {
    if (!copyOpen) return;
    requestSidePanel(true);
    return () => requestSidePanel(false);
  }, [copyOpen]);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/invoice-activity/invoice/${encodeURIComponent(row.venue_id)}/${encodeURIComponent(row.invoice_id)}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setDetail({ items: data.items || [], unit_check: data.unit_check || [], document: data.document || null });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load this invoice');
    }
  }, [row.venue_id, row.invoice_id]);
  useEffect(() => { load(); }, [load]);

  if (error) return <div role="alert" style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)', padding: '8px 0' }}>{error}</div>;
  if (!detail) return <div style={{ padding: '8px 0' }}><PageState kind="loading" title="Opening the invoice…" /></div>;
  const doc = detail.document;
  return (
    <div style={{ padding: '10px 0 4px' }}>
      {/* Loaded's invoice id, to hand over for an investigation (Dojo only). */}
      <div style={{ marginBottom: 6 }}>
        <CopyableId label="Invoice" value={row.invoice_id} title="Copy this invoice's id, to ask about it" />
      </div>
      <ol aria-label="What happened" style={{ listStyle: 'none', margin: '0 0 10px', padding: 0 }}>
        {detail.items.map((it) => {
          const level = rowLevel(it);
          const tags = activityTags(it);
          return (
            <li key={it.key} style={{ fontSize: 'var(--fs-sm)', padding: '3px 0' }}>
              <span style={{ color: 'var(--muted)' }}>
                {it.type === 'event' && it.first_at && it.first_at !== it.at ? `${when(it.first_at)} to ${when(it.at)}` : when(it.at)}
              </span>
              {' · '}<strong style={{ fontWeight: 600 }}>{outcomeWords(it)}</strong>
              {/* A delete's note says more about its reason: right after it,
                  before the level it has nothing to do with. */}
              {deleteNote(it) ? <span style={{ color: 'var(--text-soft)' }}> — {deleteNote(it)}</span> : null}
              {level ? <span style={{ color: 'var(--text-soft)' }}> · {level.label} ({level.switches.toLowerCase()})</span> : null}
              {it.reasons?.length ? <span style={{ color: 'var(--text-soft)' }}>: {it.reasons.join('; ')}</span> : null}
              {tags.length > 0 && <span style={{ color: 'var(--muted)' }}> · {tags.map((t) => t.text).join(' · ')}</span>}
            </li>
          );
        })}
      </ol>
      {detail.unit_check.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ ...sectionTitle, fontSize: 'var(--fs-sm)' }}>Daily unit check</div>
          <UnitCheckLines proposals={detail.unit_check}
            onReplace={(np) => setDetail((d) => (d ? { ...d, unit_check: d.unit_check.map((x) => (x.id === np.id ? np : x)) } : d))}
            onReload={load} />
        </div>
      )}
      {doc ? (
        <ReceiveInvoiceEditor
          data={{ working_document_id: doc.id, venue_id: row.venue_id }}
          props={{ activeVenueId: row.venue_id, record: true }}
        />
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
            <span style={{ ...muted, flex: '1 1 320px' }}>
              Norm didn&rsquo;t keep its review of this invoice: before 19 October, Autopilot batches kept the review only
              for invoices they couldn&rsquo;t finish, so there is no card to show.
            </span>
            <Button size="sm" icon={FileText} onClick={() => setCopyOpen((v) => !v)} aria-pressed={copyOpen}>
              {copyOpen ? 'Hide the copy' : 'Show the copy'}
            </Button>
          </div>
          {copyOpen && (
            <InvoicePdfPane onClose={() => setCopyOpen(false)}
              src={`/api/invoice-fixes/file?venue_id=${encodeURIComponent(row.venue_id)}&invoice_id=${encodeURIComponent(row.invoice_id)}`} />
          )}
        </>
      )}
    </div>
  );
}

const cell: React.CSSProperties = { minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };

/** One invoice in the stream, on one line; opens in place below. */
function StreamRow({ r, open, onToggle, compact }: { r: ActivityRow; open: boolean; onToggle: () => void; compact: boolean }) {
  const tags = activityTags(r);
  const tone = r.outcome === 'stopped' || r.outcome === 'failed' ? 'warn' : r.outcome === 'deleted' ? 'neutral' : r.by === 'person' && r.person_changes ? 'accent' : 'ok';
  const level = rowLevel(r);
  const modeText = modeShort(r);
  // What Norm did, or why it stopped, in one line; the full text on hover.
  const said = r.reasons?.length ? r.reasons.join('; ') : tags.map((t) => t.text).join(' · ');
  const total = money(r.total ?? r.activity?.total ?? null);
  return (
    <div style={{ borderTop: '1px solid var(--line)' }}>
      <div role="button" tabIndex={0} aria-expanded={open} onClick={onToggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}
        style={{
          display: 'grid', alignItems: 'center', gap: 10, padding: '7px 14px', cursor: 'pointer', fontSize: 'var(--fs-sm)',
          // On a phone the badge goes under the supplier, so the supplier and
          // the invoice number keep the width (a badge column took it all).
          gridTemplateColumns: compact
            ? '70px minmax(0, 1fr) 14px'
            : '118px minmax(0, 1.8fr) minmax(0, 1fr) 84px minmax(0, 2fr) minmax(0, 2.2fr) 14px',
          ...(compact ? { gridTemplateAreas: '"when who chev" "when what chev"', rowGap: 4 } : {}),
          background: open ? 'var(--surface)' : undefined,
        }}>
        <span style={{ ...cell, color: 'var(--muted)', ...(compact ? { gridArea: 'when', whiteSpace: 'normal', lineHeight: 1.3 } : {}) }}>{when(r.at)}</span>
        <span style={{ ...cell, ...(compact ? { gridArea: 'who' } : {}) }} title={[r.supplier_name, r.reference_number].filter(Boolean).join(' · ')}>
          <strong style={{ fontWeight: 600 }}>{r.supplier_name || 'Unknown supplier'}</strong>
          {r.reference_number ? <span style={{ color: 'var(--muted)' }}> · {r.reference_number}</span> : null}
        </span>
        {!compact && <span style={{ ...cell, color: 'var(--text-soft)' }}>{r.venue_name}</span>}
        {!compact && <span style={{ ...cell, textAlign: 'right' }}>{total}</span>}
        <span style={{ ...cell, display: 'flex', gap: 6, alignItems: 'center', ...(compact ? { gridArea: 'what' } : {}) }}>
          <Badge tone={tone} title={outcomeWords(r)}>{outcomeShort(r)}</Badge>
          {!compact && <span style={{ ...cell, color: 'var(--muted)' }} title={level?.switches || 'Level not recorded'}>{modeText}</span>}
        </span>
        {!compact && <span style={{ ...cell, color: 'var(--text-soft)' }} title={said}>{said}</span>}
        <span style={compact ? { gridArea: 'chev' } : undefined}>
          <Icon icon={open ? ChevronDown : ChevronRight} size="dense" tone="muted" />
        </span>
      </div>
      {open && <div style={{ padding: '0 14px 10px', background: 'var(--surface)' }}><InvoiceDetail row={r} /></div>}
    </div>
  );
}

export default function InvoiceActivityPanel({ initialView }: { initialView?: string } = {}) {
  const fromLink = useMemo(() => filtersFromLink(initialView), [initialView]);
  const ids = { from: useId(), to: useId(), mode: useId(), outcome: useId(), supplier: useId() };
  const [period, setPeriod] = useState(() => (fromLink.start && fromLink.end ? { start: fromLink.start, end: fromLink.end } : lastDays(7)));
  const [venueIds, setVenueIds] = useState<string[]>(fromLink.venues);
  const [mode, setMode] = useState('');
  const [outcome, setOutcome] = useState('');
  const [supplier, setSupplier] = useState('');
  const [query, setQuery] = useState('');
  const [data, setData] = useState<Data | null>(null);
  const [rows, setRows] = useState<ActivityRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // The invoice opened in place (one at a time).
  const [expanded, setExpanded] = useState<string | null>(null);
  const { isMobile } = useBreakpoint();

  const rangeOk = period.start <= period.end;
  const params = useCallback((extra: Record<string, string | number> = {}) => {
    const p = new URLSearchParams({ start: period.start, end: period.end });
    // The dates are the viewer's own calendar days.
    try { p.set('tz', Intl.DateTimeFormat().resolvedOptions().timeZone); } catch { /* UTC */ }
    if (venueIds.length) p.set('venue_ids', venueIds.join(','));
    if (mode) p.set('mode', mode);
    if (outcome) p.set('outcome', outcome);
    if (query) p.set('supplier', query);
    for (const [k, v] of Object.entries(extra)) p.set(k, String(v));
    return p.toString();
  }, [period, venueIds, mode, outcome, query]);

  const load = useCallback(async () => {
    if (!rangeOk) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/invoice-activity?${params({ limit: PAGE })}`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Error ${res.status}`);
      setData(body as Data);
      setRows(body.rows || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the activity');
    } finally {
      setLoading(false);
    }
  }, [params, rangeOk]);
  useEffect(() => { load(); }, [load]);

  const more = async () => {
    try {
      const res = await apiFetch(`/api/invoice-activity?${params({ limit: PAGE, offset: rows.length })}`);
      const body = await res.json().catch(() => ({}));
      // Rows that arrived since the first page shift the list: never show
      // one twice (both copies would open together).
      if (res.ok) setRows((list) => {
        const seen = new Set(list.map((r) => r.key));
        return [...list, ...((body.rows || []) as ActivityRow[]).filter((r) => !seen.has(r.key))];
      });
    } catch { /* the button stays for another try */ }
  };

  const opener = (k: string) => ({ open: expanded === k, onToggle: () => setExpanded((e) => (e === k ? null : k)) });
  // A card sets the stream's filter; pressing it again clears it.
  const pick = (o: string) => setOutcome((cur) => (cur === o ? '' : o));
  const k = data?.kpis;
  const venueNames = (data?.venues || []).filter((v) => venueIds.includes(v.id)).map((v) => v.name);

  return (
    <div style={{ lineHeight: 1.45, color: 'var(--text)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
        <h3 style={{ flex: 1, minWidth: 180, margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3 }}>Invoice activity</h3>
        <Button size="sm" icon={RefreshCw} onClick={load} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</Button>
      </div>
      <div style={{ ...muted, marginBottom: 14 }}>
        What Norm did with each invoice. Press a card to see just those invoices in the stream.
      </div>

      {/* Filters: the cards and the stream follow the dates and venues. */}
      <div className="n-card" style={{ padding: '10px 14px', marginBottom: 14, display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <label htmlFor={ids.from} style={muted}>From</label>
          <input id={ids.from} type="date" className="n-input" value={period.start} max={period.end}
            onChange={(e) => e.target.value && setPeriod((p) => ({ ...p, start: e.target.value }))} />
          <label htmlFor={ids.to} style={muted}>to</label>
          <input id={ids.to} type="date" className="n-input" value={period.end} min={period.start}
            onChange={(e) => e.target.value && setPeriod((p) => ({ ...p, end: e.target.value }))} />
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <Button size="sm" variant="quiet" onClick={() => setPeriod(lastDays(7))}>Last 7 days</Button>
          <Button size="sm" variant="quiet" onClick={() => setPeriod(lastDays(30))}>Last 30 days</Button>
        </div>
        <details style={{ position: 'relative' }}>
          <summary className="n-select" style={{ cursor: 'pointer', listStyle: 'none', minWidth: 160 }}>
            {venueIds.length === 0 ? 'All venues' : venueNames.length === 1 ? venueNames[0] : `${venueIds.length} venues`}
          </summary>
          <div className="n-card" style={{ position: 'absolute', zIndex: 5, marginTop: 4, padding: 10, maxHeight: 320, overflowY: 'auto', minWidth: 240, background: 'var(--bg)' }}>
            <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 'var(--fs-sm)', marginBottom: 6 }}>
              <input type="checkbox" checked={venueIds.length === 0} onChange={() => setVenueIds([])} /> All venues
            </label>
            {(data?.venues || []).map((v) => (
              <label key={v.id} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 'var(--fs-sm)', marginBottom: 4 }}>
                <input type="checkbox" checked={venueIds.includes(v.id)}
                  onChange={(e) => setVenueIds((list) => (e.target.checked ? [...list, v.id] : list.filter((x) => x !== v.id)))} />
                <span>{v.name}{v.organization ? <span style={{ color: 'var(--muted)' }}> · {v.organization}</span> : null}</span>
              </label>
            ))}
          </div>
        </details>
        {!rangeOk && <span role="alert" style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>Pick a start before the end.</span>}
      </div>

      {error && <div style={{ marginBottom: 12 }}><PageState kind="error" title={error} /></div>}
      {loading && !data && <PageState kind="loading" title="Loading…" />}

      {data && k && (
        <>
          <div role="group" aria-label="Filter the stream" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, marginBottom: 20 }}>
            <KpiCard label="Received by Norm" value={k.received_by_norm}
              note={k.received ? `${Math.round((100 * k.received_by_norm) / k.received)}% of everything received` : 'Nothing received'}
              active={outcome === 'received_by_norm'} onClick={() => pick('received_by_norm')} />
            <KpiCard label="Changed by a person" value={k.changed_by_person}
              note={`of ${k.received_by_person.toLocaleString()} received by people`}
              active={outcome === 'changed_by_person'} onClick={() => pick('changed_by_person')} />
            <KpiCard label="Stopped" value={k.not_finished} note="Norm couldn't finish these"
              active={outcome === 'not_finished'} onClick={() => pick('not_finished')} />
            <KpiCard label="Unusual deletions" value={k.unusual_deletions} note="Not a duplicate or a statement"
              active={outcome === 'unusual_deletions'} onClick={() => pick('unusual_deletions')}
              extra={
                <button type="button" aria-pressed={outcome === 'deleted'} onClick={() => pick('deleted')}
                  title={outcome === 'deleted' ? 'Show every invoice again' : 'Show every deletion, routine ones too'}
                  style={{ padding: 0, border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit', fontSize: 'var(--fs-xs, 11px)', color: 'var(--accent)', fontWeight: outcome === 'deleted' ? 700 : 500, textDecoration: 'underline' }}>
                  {plural(k.deleted, 'deletion', 'deletions')} in all
                </button>
              } />
            <KpiCard label="Created in Loaded" value={k.created_invoices}
              note={createdWords(k.created)
                // New suppliers first: they shape how later invoices are read.
                ? `invoices, making ${createdWords({ suppliers: k.created.suppliers, units: k.created.units, brands: k.created.brands, stock_items: k.created.stock_items })}`
                : 'Nothing new'}
              active={outcome === 'created'} onClick={() => pick('created')} />
          </div>

          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
            <h4 style={{ ...sectionTitle, flex: '1 1 160px' }}>
              Invoice stream <span style={{ fontWeight: 400, color: 'var(--muted)' }}>· {plural(data.total, 'invoice', 'invoices')}</span>
            </h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <label htmlFor={ids.outcome} style={{ fontSize: 'var(--fs-xs, 11px)', color: 'var(--muted)' }}>What happened</label>
              <select id={ids.outcome} className="n-select" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
                {OUTCOMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <label htmlFor={ids.mode} style={{ fontSize: 'var(--fs-xs, 11px)', color: 'var(--muted)' }}>Mode</label>
              <select id={ids.mode} className="n-select" value={mode} onChange={(e) => setMode(e.target.value)}>
                {MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); setQuery(supplier.trim()); }} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <label htmlFor={ids.supplier} style={{ fontSize: 'var(--fs-xs, 11px)', color: 'var(--muted)' }}>Supplier</label>
              <input id={ids.supplier} className="n-input" value={supplier} placeholder="Any supplier"
                onChange={(e) => setSupplier(e.target.value)} onBlur={() => setQuery(supplier.trim())} />
            </form>
          </div>
          {rows.length === 0 ? (
            <PageState kind="empty" title="No invoices match" />
          ) : (
            <div className="n-card" style={{ overflow: 'hidden' }}>
              {rows.map((r) => <StreamRow key={r.key} r={r} compact={isMobile} {...opener(r.key)} />)}
            </div>
          )}
          {rows.length < data.total && (
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: 10 }}>
              <Button size="sm" onClick={more}>Show more ({data.total - rows.length} left)</Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
