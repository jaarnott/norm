'use client';

/**
 * Profit & Loss — the `profit_and_loss` display component (Finance → Profit &
 * Loss, docs/plans/agents-and-apps-restructure.md §5).
 *
 * For one month and one venue: income, cost of sales, gross profit and its
 * share of income, operating expenses by the organisation's own Xero groups,
 * and net profit — against the month before and the same month last year,
 * with a plain-words line on what moved (worked out here from the numbers;
 * no AI call). The logic lives in ./profitAndLoss/statement.ts.
 *
 * Data: three `xero.get_profit_and_loss` calls in parallel, one per month,
 * each with a plain-English `period` ("September 2026") that Norm resolves.
 * Not Xero's own comparison columns — those shift the end date and drop the
 * last days of a longer month (see statement.ts).
 *
 * Venues work as on Finance → Bills (XeroBills.tsx): the page never asks
 * Xero for a venue that has no Xero connection of its own, because the
 * executor would fall back to another venue's credentials — another
 * company's books. Such a venue gets the connect card instead.
 */

import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import { StatTile, TileRow } from '../functions/enquiries/bits';
import ConnectPrompt from './ConnectPrompt';
import {
  buildStatement, change, comparisonMonths, extractPlDoc, lastFullMonth, money0, monthInWords, monthKey,
  monthLabel, monthOfDoc, monthOptions, monthShort, movementLine, parseMonthKey, pct1, periodEmpty,
  shareOfIncome, signedMoney0, INCOME_BLOCKS,
  type BlockKey, type Month, type PlDoc, type Statement, type StatementBlock, type Trio,
} from './profitAndLoss/statement';

interface VenueOption { id: string; name: string }
type Docs = [PlDoc | null, PlDoc | null, PlDoc | null];
type Errors = [string | null, string | null, string | null];

type RowKind = 'block' | 'group' | 'line' | 'headline';
interface Row {
  key: string;
  label: string;
  values: Trio;
  kind: RowKind;
  indent: number;
  /** A rise in this row raises profit (income); otherwise it lowers it (costs). */
  raisesProfit: boolean;
  groupKey?: string;
  hasLines?: boolean;
}

const NOT_CONNECTED = /No credentials|not connected|needs_reconnect|reconnect/i;

/** Green when the change helps profit, red when it hurts, muted when it's small. */
function toneFor(amount: number, raisesProfit: boolean, base: number | null): string {
  if (Math.abs(amount) < Math.max(1, Math.abs(base ?? 0) * 0.005)) return 'var(--muted)';
  return (amount > 0) === raisesProfit ? 'var(--ok)' : 'var(--error)';
}

function pctText(pct: number | null): string | null {
  if (pct === null || !Number.isFinite(pct) || Math.abs(pct) >= 1000) return null;
  return `${pct > 0 ? '+' : ''}${pct.toFixed(Math.abs(pct) < 10 ? 1 : 0)}%`;
}

/** The statement's rows in reading order, groups opened per `isOpen`. */
function statementRows(st: Statement, isOpen: (groupKey: string) => boolean): Row[] {
  const out: Row[] = [];
  const blockRows = (b: StatementBlock) => {
    const raises = INCOME_BLOCKS.has(b.key);
    out.push({ key: `b:${b.key}`, label: b.label, values: b.total, kind: 'block', indent: 0, raisesProfit: raises });
    for (const g of b.groups) {
      if (g.plain) {
        for (const l of g.lines) out.push({ key: l.key, label: l.name, values: l.values, kind: 'line', indent: 1, raisesProfit: raises });
        continue;
      }
      out.push({ key: `g:${g.key}`, label: g.title, values: g.values, kind: 'group', indent: 1, raisesProfit: raises, groupKey: g.key, hasLines: g.lines.length > 0 });
      if (isOpen(g.key)) {
        for (const l of g.lines) out.push({ key: l.key, label: l.name, values: l.values, kind: 'line', indent: 2, raisesProfit: raises });
      }
    }
  };
  const block = (k: BlockKey) => st.blocks.find((b) => b.key === k);
  const h = st.headline;
  const add = (k: BlockKey) => { const b = block(k); if (b) blockRows(b); };
  add('income');
  add('cost_of_sales');
  out.push({ key: 'h:gross', label: 'Gross profit', values: h.gross_profit, kind: 'headline', indent: 0, raisesProfit: true });
  add('other_income');
  add('operating_expenses');
  if (block('other')) {
    out.push({ key: 'h:operating', label: 'Operating profit', values: st.operatingProfit, kind: 'headline', indent: 0, raisesProfit: true });
    add('other');
  }
  out.push({ key: 'h:net', label: (h.net_profit[0] ?? 0) < 0 ? 'Net loss' : 'Net profit', values: h.net_profit, kind: 'headline', indent: 0, raisesProfit: true });
  return out;
}

export default function ProfitAndLoss({ data, props }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const title = (props?.title as string) || 'Profit & Loss';
  const { isMobile } = useBreakpoint();

  const initial = useMemo(() => extractPlDoc(data), [data]);
  const blockVenue = (props?.activeVenueId as string) || ((data as Record<string, unknown>)?.venue_id as string) || null;

  const [sharedVenue, setActiveVenue] = useActiveVenue();
  const rememberedVenue = persistVenue ? sharedVenue : null;
  const [venues, setVenues] = useState<VenueOption[]>([]);
  // Which venues hold a Xero connection of their own (connect-info). The
  // page never asks Xero for any other venue — see the header comment.
  const [connected, setConnected] = useState<Set<string> | null>(null);
  const [selectedVenue, setSelectedVenue] = useState<string | null>(blockVenue || rememberedVenue || null);

  const [today] = useState(() => new Date());
  const [month, setMonth] = useState<Month>(() => monthOfDoc(initial) ?? lastFullMonth(today));
  const monthId = monthKey(month);
  const [docs, setDocs] = useState<Docs>([initial, null, null]);
  const [errors, setErrors] = useState<Errors>([null, null, null]);
  const [loading, setLoading] = useState<boolean>(persistVenue && !initial);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [touched, setTouched] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [allOpen, setAllOpen] = useState(false);
  const [phoneCompare, setPhoneCompare] = useState<1 | 2>(1);

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

  const propVenue = (props?.activeVenueId as string) || null;
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch('/api/venues').then((r) => (r.ok ? r.json() : null)).catch(() => null),
      loadConnectInfo(),
    ]).then(([d, ok]) => {
      if (cancelled) return;
      const list: VenueOption[] = d ? (Array.isArray(d) ? d : (d.venues ?? [])) : [];
      setVenues(list);
      // Nothing chosen anywhere yet: start on the first connected venue.
      if (!selectedVenue && !propVenue && !rememberedVenue && list.length) {
        setSelectedVenue(list.find((v) => ok.has(v.id))?.id ?? list[0].id);
      }
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Only the latest request may land: switching venue or month mid-load must
  // never leave the previous figures under the new name.
  const loadSeq = useRef(0);
  const load = useCallback(async (venueId: string, m: Month) => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    setDocs([null, null, null]);
    setErrors([null, null, null]);
    const one = async (period: string): Promise<{ doc: PlDoc | null; error: string | null }> => {
      try {
        const res = await apiFetch('/api/connectors/xero/execute/get_profit_and_loss', {
          method: 'POST',
          body: JSON.stringify({ params: { venue_id: venueId, period } }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) return { doc: null, error: body?.detail || body?.error || `Xero answered ${res.status}` };
        const doc = extractPlDoc(body?.data ?? body);
        const docError = (body?.data as Record<string, unknown>)?.error ?? body?.error;
        if (!doc) return { doc: null, error: typeof docError === 'string' && docError ? docError : 'Xero returned no profit and loss' };
        return { doc, error: null };
      } catch (e) {
        return { doc: null, error: e instanceof Error ? e.message : 'Could not reach Xero' };
      }
    };
    const results = await Promise.all(comparisonMonths(m).map((x) => one(monthLabel(x))));
    if (seq !== loadSeq.current) return;
    if (!results[0].doc) {
      setError(results[0].error || 'Could not load the profit and loss');
    } else {
      setDocs(results.map((r) => r.doc) as Docs);
      setErrors(results.map((r) => r.error) as Errors);
    }
    setLoading(false);
  }, []);

  const venueConnected = !connected || !selectedVenue || connected.has(selectedVenue);

  // The page follows the app's venue; a venue with no Xero connection stays
  // selected and gets the connect card — the page never jumps elsewhere.
  const userPicked = useRef(false);
  useEffect(() => {
    if (!persistVenue || !propVenue || userPicked.current) return;
    if (propVenue !== selectedVenue) setSelectedVenue(propVenue);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propVenue, persistVenue]);

  useEffect(() => {
    if (!selectedVenue) return;
    // A chat block that arrived with its document shows it as-is until the
    // person changes something (no Xero calls just for opening a thread).
    if (!persistVenue && initial && !touched) return;
    // Wait until connect-info has said which venues are connected.
    if (connected === null) return;
    if (!connected.has(selectedVenue)) {
      loadSeq.current += 1; // an in-flight load for the previous venue may not land
      setDocs([null, null, null]);
      setError('not connected');
      setLoading(false);
      return;
    }
    void load(selectedVenue, month);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVenue, monthId, reloadKey, persistVenue, connected]);

  // The connect card runs the OAuth popup; its callback posts
  // `oauth-complete` here. Re-read the connections and load.
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

  const months = useMemo(() => {
    const opts = monthOptions(today, 24);
    return opts.some((o) => monthKey(o) === monthId) ? opts : [month, ...opts];
  }, [today, month, monthId]);

  const st: Statement | null = useMemo(() => (docs[0] ? buildStatement(docs) : null), [docs]);
  const [, prevMonth, lastYear] = comparisonMonths(month);
  const labels: [string, string, string] = [monthShort(month), monthShort(prevMonth), monthShort(lastYear)];
  const wordLabels: [string, string, string] = [monthLabel(month), monthInWords(prevMonth, month), monthLabel(lastYear)];
  const doc = docs[0];

  const isOpen = useCallback((key: string) => allOpen || open.has(key), [allOpen, open]);
  const toggle = (key: string) => setOpen((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const rows = useMemo(() => (st ? statementRows(st, isOpen) : []), [st, isOpen]);
  const hasGroups = !!st?.blocks.some((b) => b.groups.some((g) => !g.plain && g.lines.length));

  const venuePicker = venues.length > 1 ? (
    <VenueSelect venues={venues} value={selectedVenue} onChange={handleVenueChange} />
  ) : null;

  const filters = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <select
        className="n-select"
        value={monthId}
        onChange={(e) => { const m = parseMonthKey(e.target.value); if (m) { setMonth(m); setTouched(true); } }}
        aria-label="Month"
      >
        {months.map((m) => <option key={monthKey(m)} value={monthKey(m)}>{monthLabel(m)}</option>)}
      </select>
      <Button
        size="sm"
        variant="quiet"
        icon={RefreshCw}
        onClick={() => { setTouched(true); void loadConnectInfo().then(() => setReloadKey((k) => k + 1)); }}
        aria-label="Refresh"
      >
        Refresh
      </Button>
      {venuePicker}
    </div>
  );

  // ── Tiles ──────────────────────────────────────────────────────────────
  const share = (v: number | null) => {
    const s = shareOfIncome(v, st?.headline.income[0] ?? null);
    return s === null ? null : <span style={{ display: 'block' }}>{pct1(s)} of income</span>;
  };
  const changeNote = (values: Trio, raisesProfit: boolean) => (
    <>
      {([1, 2] as const).map((p) => {
        const c = st?.present[p] ? change(values[0], values[p]) : null;
        if (!c) return null;
        return (
          <span key={p} style={{ display: 'block' }}>
            <span style={{ color: toneFor(c.amount, raisesProfit, values[p]), fontWeight: 600 }}>{signedMoney0(c.amount)}</span>
            {' '}on {labels[p]}
          </span>
        );
      })}
    </>
  );
  const tile = (label: string, values: Trio, raisesProfit: boolean, withShare: boolean) => (
    <StatTile
      label={label}
      value={money0(values[0])}
      sub={<>{withShare && share(values[0])}{changeNote(values, raisesProfit)}</>}
    />
  );

  const tiles = st ? (
    <div style={{ marginBottom: 12 }}>
      <TileRow min={isMobile ? 140 : 170}>
        {tile('Income', st.headline.income, true, false)}
        {tile('Cost of sales', st.headline.cost_of_sales, false, true)}
        {tile('Gross profit', st.headline.gross_profit, true, true)}
        {tile('Operating expenses', st.headline.operating_expenses, false, true)}
        {tile((st.headline.net_profit[0] ?? 0) < 0 ? 'Net loss' : 'Net profit', st.headline.net_profit, true, true)}
      </TileRow>
    </div>
  ) : null;

  // ── What moved ─────────────────────────────────────────────────────────
  const words = st
    ? ([1, 2] as const)
      .map((p) => ({ p, text: movementLine(st, p, wordLabels[p]) }))
      .filter((w): w is { p: 1 | 2; text: string } => !!w.text)
    : [];
  const missing = ([1, 2] as const).filter((p) => errors[p]);
  const whatMoved = st && (words.length || missing.length) ? (
    <div className="n-card" style={{ padding: '12px 14px', marginBottom: 12, fontSize: 'var(--fs-sm)', color: 'var(--text)', lineHeight: 1.5 }}>
      <div className="n-eyebrow" style={{ marginBottom: 6 }}>What moved</div>
      {words.map((w) => (
        <p key={w.p} style={{ margin: '0 0 4px' }}>
          <span style={{ fontWeight: 600 }}>Against {wordLabels[w.p]}: </span>{w.text}
        </p>
      ))}
      {missing.map((p) => (
        <p key={`e${p}`} style={{ margin: '0 0 4px', color: 'var(--muted)' }}>
          Could not read {monthLabel(p === 1 ? prevMonth : lastYear)} from Xero: {errors[p]}
        </p>
      ))}
    </div>
  ) : null;

  // ── Statement ──────────────────────────────────────────────────────────
  const rowStyle = (r: Row): React.CSSProperties => ({
    fontWeight: r.kind === 'line' ? 400 : r.kind === 'group' ? 500 : 600,
    color: r.kind === 'line' ? 'var(--text-soft)' : 'var(--text)',
    background: r.kind === 'headline' ? 'var(--surface-alt)' : undefined,
  });

  const nameCell = (r: Row, cellStyle?: React.CSSProperties) => {
    const canOpen = r.kind === 'group' && !!r.hasLines && !!r.groupKey;
    const opened = canOpen && isOpen(r.groupKey as string);
    return (
      <td
        style={{ ...rowStyle(r), ...cellStyle, paddingLeft: 12 + r.indent * 16, cursor: canOpen ? 'pointer' : undefined }}
        onClick={canOpen ? () => toggle(r.groupKey as string) : undefined}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          {canOpen && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); toggle(r.groupKey as string); }}
              aria-expanded={opened}
              aria-label={`${opened ? 'Hide' : 'Show'} the accounts in ${r.label}`}
              style={{ display: 'inline-flex', padding: 0, border: 'none', background: 'none', color: 'var(--muted)', cursor: 'pointer' }}
            >
              <Icon icon={opened ? ChevronDown : ChevronRight} size="dense" />
            </button>
          )}
          {r.label}
        </span>
      </td>
    );
  };

  const changeCell = (r: Row, p: 1 | 2, cellStyle?: React.CSSProperties, under?: React.ReactNode) => {
    const c = st?.present[p] ? change(r.values[0], r.values[p]) : null;
    const strong = r.kind === 'block' || r.kind === 'headline';
    const pt = c && Math.round(c.amount) !== 0 ? pctText(c.pct) : null;
    return (
      <td className="num" style={{ ...rowStyle(r), whiteSpace: 'nowrap', ...cellStyle }}>
        {c ? (
          <>
            <span style={{ color: strong ? toneFor(c.amount, r.raisesProfit, r.values[p]) : undefined }}>{signedMoney0(c.amount)}</span>
            {pt && <span style={{ marginLeft: 6, fontSize: 'var(--fs-xs)', fontWeight: 400, color: 'var(--muted)' }}>{pt}</span>}
          </>
        ) : '—'}
        {under}
      </td>
    );
  };

  const shareCell = (r: Row) => {
    const s = shareOfIncome(r.values[0], st?.headline.income[0] ?? null);
    return <td className="num" style={{ ...rowStyle(r), color: 'var(--muted)', fontWeight: 400 }}>{s === null ? '' : pct1(s)}</td>;
  };

  const desktopTable = st ? (
    <table className="n-table" style={{ width: '100%' }}>
      <thead>
        <tr>
          <th style={{ minWidth: 220 }}>Account</th>
          <th className="num">{labels[0]}</th>
          <th className="num" title="Share of this month's income">% of income</th>
          <th className="num">{labels[1]}</th>
          <th className="num">Change</th>
          <th className="num">{labels[2]}</th>
          <th className="num">Change</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            {nameCell(r)}
            <td className="num" style={rowStyle(r)}>{money0(r.values[0])}</td>
            {shareCell(r)}
            <td className="num" style={{ ...rowStyle(r), color: 'var(--text-soft)' }}>{st.present[1] ? money0(r.values[1]) : '—'}</td>
            {changeCell(r, 1)}
            <td className="num" style={{ ...rowStyle(r), color: 'var(--text-soft)' }}>{st.present[2] ? money0(r.values[2]) : '—'}</td>
            {changeCell(r, 2)}
          </tr>
        ))}
      </tbody>
    </table>
  ) : null;

  // Phone: one comparison at a time — account, this month, and the change
  // with the other month's figure under it.
  const tight: React.CSSProperties = { padding: '8px 6px' };
  const phoneTable = st ? (
    <>
      <div className="n-tabs" role="tablist" aria-label="Compare with" style={{ padding: '0 12px' }}>
        {([1, 2] as const).map((p) => (
          <button key={p} type="button" role="tab" className="n-tab" aria-selected={phoneCompare === p} onClick={() => setPhoneCompare(p)}>
            Against {labels[p]}
          </button>
        ))}
      </div>
      <table className="n-table" style={{ width: '100%', fontSize: 'var(--fs-sm)' }}>
        <thead>
          <tr>
            <th style={{ ...tight, paddingLeft: 12 }}>Account</th>
            <th className="num" style={tight}>{labels[0]}</th>
            <th className="num" style={{ ...tight, paddingRight: 12 }}>Change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              {nameCell(r, tight)}
              <td className="num" style={{ ...rowStyle(r), ...tight, whiteSpace: 'nowrap' }}>{money0(r.values[0])}</td>
              {changeCell(r, phoneCompare, { ...tight, paddingRight: 12 }, st.present[phoneCompare] ? (
                <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 400, color: 'var(--muted)' }}>was {money0(r.values[phoneCompare])}</div>
              ) : null)}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  ) : null;

  const statement = st ? (
    <div className="n-card" style={{ overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', padding: '10px 12px' }}>
        <div style={{ fontWeight: 600, color: 'var(--text)' }}>Profit and loss by group</div>
        {hasGroups && (
          <Button size="sm" variant="quiet" onClick={() => { setAllOpen((v) => !v); setOpen(new Set()); }}>
            {allOpen ? 'Groups only' : 'Show every account'}
          </Button>
        )}
      </div>
      {!st.sure && (
        <div style={{ padding: '0 12px 10px', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          This organisation&apos;s groups in Xero don&apos;t add up to its totals, so a group may sit under the wrong heading. The totals are Xero&apos;s own.
        </div>
      )}
      <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
        {isMobile ? phoneTable : desktopTable}
      </div>
    </div>
  ) : null;

  // ── Page ───────────────────────────────────────────────────────────────
  const inCard = (node: React.ReactNode) => (persistVenue ? node : <div className="n-card">{node}</div>);
  const notConnected = !venueConnected || (!!error && NOT_CONNECTED.test(error));
  const venueName = venues.find((v) => v.id === selectedVenue)?.name || 'This venue';
  const settling = persistVenue && (connected === null || venues.length === 0);
  // Not connected: the same connect card chat and Settings → Connections use.
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
    inCard(<PageState kind="loading" title={`Loading ${monthLabel(month)} from Xero…`} />)
  ) : notConnected ? (
    inCard(connectHere)
  ) : error ? (
    inCard(<PageState kind="error" title="Could not load the profit and loss." detail={error} />)
  ) : !st ? (
    inCard(<PageState kind="empty" title={selectedVenue ? 'No profit and loss to show.' : 'Choose a venue.'} />)
  ) : periodEmpty(st, 0) ? (
    inCard(<PageState kind="empty" title={`Xero has nothing posted for ${monthLabel(month)}.`} detail="Pick another month." />)
  ) : (
    <>
      {tiles}
      {whatMoved}
      {statement}
    </>
  );

  const meta = loading || notConnected
    ? ' '
    : doc
      ? [doc.organisation, `${monthLabel(month)}, from Xero, excluding GST${doc.currency ? ` (${doc.currency})` : ''}`].filter(Boolean).join(' · ')
      : '';

  if (persistVenue) {
    return (
      <div>
        <PageHeader title="Profit & Loss" meta={meta} actions={filters} />
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
      {body}
    </div>
  );
}
