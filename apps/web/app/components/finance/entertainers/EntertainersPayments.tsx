'use client';

/**
 * Entertainers — Payments (`payroll:write`): the approved performances
 * with what each is owed, the invoice check's verdict, the Xero bill, and
 * who was paid when with what reference; "mark paid for payroll week" for
 * the payroll lane; a per-performer statement. The page is hidden from
 * anyone without `payroll:write` (the registry gate) and the API 403s the
 * rest, so this component only has to say so.
 */
import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import { CalendarCheck } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import VenueSelect from '../../ui/VenueSelect';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { useActiveVenue } from '../../../hooks/useActiveVenue';
import { ToastHost, toast } from '../shared/Toasts';
import { entGet, entSend, errorText } from './api';
import { dateLabel, money, todayIn } from './format';
import { LANE_SHORT, addDays, sumCents, weekStart } from './logic';
import { CheckChip, ChipRow, FilterChip, KpiRow, KpiTile, LaneBadge, Notice, RowCard, StatusChip, Toggle } from './ui';
import { invalidateEntMeta, readPageParam, setPageParam, useEntMeta } from './useMeta';
import { ModeBanner, OffState } from './EntertainersRegister';
import PaymentDialog from './payments/PaymentDialog';
import StatementView from './payments/StatementView';
import type { Lane, PaymentRow, Performance } from './types';

interface Payload {
  rows: PaymentRow[];
  totals: { pending_cents: number; paid_cents: number; count: number };
}

type View = 'rows' | 'statement';

export default function EntertainersPayments({ props }: DisplayBlockProps) {
  const isPage = !!props?.persistVenue;
  const { meta, error, off, loading, reload } = useEntMeta();
  const { isMobile } = useBreakpoint();
  const ids = { from: useId(), to: useId(), week: useId() };
  const today = todayIn('Pacific/Auckland');
  // The app's shared, remembered page venue; "All venues" stays page-local.
  const [remembered, remember] = useActiveVenue();
  const [venueId, setVenueIdState] = useState<string>(() => (isPage && remembered ? remembered : 'all'));
  const setVenueId = (v: string) => {
    setVenueIdState(v);
    if (isPage && v !== 'all') remember(v);
  };
  const [status, setStatus] = useState<'pending' | 'paid' | 'all'>('pending');
  const [lane, setLane] = useState<Lane | 'all'>('all');
  const [from, setFrom] = useState(addDays(today, -90));
  const [to, setTo] = useState(addDays(today, 7));
  const [view, setView] = useState<View>('rows');
  const [data, setData] = useState<Payload | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(() => (isPage ? readPageParam('performance') : null));
  const [statementFor, setStatementFor] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [weekOf, setWeekOf] = useState(weekStart(today));
  const [bulkBusy, setBulkBusy] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!meta || !meta.can_pay) return;
    let alive = true;
    entGet<Payload>('/payments', { venue_id: venueId === 'all' ? undefined : venueId, status, from, to, lane: lane === 'all' ? undefined : lane }).then(
      (res) => { if (alive) { setData(res); setListError(null); } },
      (e: unknown) => { if (alive) setListError(errorText(e)); },
    );
    return () => { alive = false; };
  }, [meta, venueId, status, lane, from, to, tick]);

  const refresh = useCallback(() => { setTick((t) => t + 1); invalidateEntMeta(); }, []);

  const open = (id: string | null) => {
    setOpenId(id);
    if (isPage) setPageParam('performance', id);
  };

  const rows = useMemo(() => data?.rows ?? [], [data]);
  const openRow = openId ? rows.find((r) => r.id === openId) ?? null : null;
  const payrollRows = rows.filter((r) => (r.lane === 'payroll_employee' || r.lane === 'payroll_contractor') && r.payment_status !== 'paid');

  const replaceRow = (next: Performance) => {
    setData((d) => (d ? { ...d, rows: d.rows.map((r) => (r.id === next.id ? { ...r, ...next } : r)) } : d));
    refresh();
  };

  /** Pay the ticked payroll-lane rows for one pay week: the API stamps
   *  `payroll_week_start` on each, so the Ace entertainer file and the
   *  reconciliation see them as paid in that week. */
  const markWeek = async () => {
    const ids = Array.from(picked);
    if (!ids.length) return;
    setBulkBusy(true);
    try {
      await entSend('POST', '/payments/mark-paid-week', { week_start: weekOf, performance_ids: ids, venue_id: venueId === 'all' ? undefined : venueId });
      toast(`${ids.length} performance${ids.length === 1 ? '' : 's'} marked paid for the week of ${dateLabel(weekOf)}.`, 'ok');
      setPicked(new Set());
      refresh();
    } catch (e) {
      toast(errorText(e), 'error');
    }
    setBulkBusy(false);
  };

  if (off) return <OffState title="Payments" />;
  if (loading) return (<><PageHeader title="Payments" /><PageState kind="loading" title="Loading…" /></>);
  if (!meta) return (<><PageHeader title="Payments" /><PageState kind="error" title="Couldn’t load payments" detail={error ?? undefined} action={<Button size="sm" onClick={reload}>Try again</Button>} /></>);
  if (!meta.can_pay) {
    return (<><PageHeader title="Payments" /><PageState kind="empty" title="Payments is for finance" detail="You need the payroll permission to see what performers are owed and to mark them paid." /></>);
  }

  const queries = rows.filter((r) => r.invoice_check_status === 'query').length;
  const checking = rows.filter((r) => r.invoice_check_status === 'pending').length;
  const notPayable = rows.filter((r) => !r.performer.payable && r.payment_status !== 'paid').length;
  const pendingShown = sumCents(rows.filter((r) => r.payment_status !== 'paid'), (r) => r.actual_cents ?? r.agreed_cents);

  return (
    <div style={{ width: '100%' }}>
      <PageHeader
        title="Payments"
        meta={data ? `${data.totals.count} approved performance${data.totals.count === 1 ? '' : 's'} in range` : undefined}
        actions={<VenueSelect venues={meta.venues} value={venueId} onChange={setVenueId} allowAll />}
      >
        <Tabs label="Payments views" value={view} onChange={(id) => setView(id as View)} items={[{ id: 'rows', label: 'Payments' }, { id: 'statement', label: 'Statement' }]} />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
          <ChipRow label="Payment status">
            <FilterChip active={status === 'pending'} onClick={() => setStatus('pending')}>To pay</FilterChip>
            <FilterChip active={status === 'paid'} onClick={() => setStatus('paid')}>Paid</FilterChip>
            <FilterChip active={status === 'all'} onClick={() => setStatus('all')}>All</FilterChip>
          </ChipRow>
          <ChipRow label="Lane">
            <FilterChip active={lane === 'all'} onClick={() => setLane('all')}>All lanes</FilterChip>
            {meta.lanes.map((l) => <FilterChip key={l} active={lane === l} onClick={() => setLane(l)}>{LANE_SHORT[l]}</FilterChip>)}
          </ChipRow>
          <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', fontSize: 'var(--fs-sm)' }}>
            <label htmlFor={ids.from} className="n-label" style={{ margin: 0 }}>From</label>
            <input id={ids.from} className="n-input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            <label htmlFor={ids.to} className="n-label" style={{ margin: 0 }}>to</label>
            <input id={ids.to} className="n-input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </span>
        </div>
      </PageHeader>

      <ModeBanner mode={meta.mode} />

      {data && view === 'rows' && (
        <KpiRow>
          <KpiTile label="To pay (in range)" value={money(status === 'paid' ? data.totals.pending_cents : pendingShown)} tone="warn" />
          <KpiTile label="Paid (in range)" value={money(data.totals.paid_cents)} tone="ok" />
          <KpiTile label="Invoice queries" value={queries} tone={queries ? 'error' : 'neutral'} />
          <KpiTile label="Checking" value={checking} tone={checking ? 'info' : 'neutral'} />
          <KpiTile label="Not payable yet" value={notPayable} tone={notPayable ? 'warn' : 'neutral'} />
        </KpiRow>
      )}

      {listError ? (
        <PageState kind="error" title="Couldn’t load payments" detail={listError} action={<Button size="sm" onClick={refresh}>Try again</Button>} />
      ) : data === null ? (
        <PageState kind="loading" title="Loading payments…" />
      ) : view === 'statement' ? (
        <StatementView rows={rows} performerId={statementFor} onPerformer={setStatementFor} onOpen={(r) => open(r.id)} />
      ) : rows.length === 0 ? (
        <PageState kind="empty" title="Nothing to pay in this range" detail="Approved performances appear here once a manager approves them on the Roster." />
      ) : (
        <>
          {payrollRows.length > 0 && status !== 'paid' && (
            <div style={{ marginBottom: 12 }}>
              <Notice
                tone="neutral"
                action={
                  <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <label htmlFor={ids.week} className="n-label" style={{ margin: 0 }}>Pay week starting</label>
                    <input id={ids.week} className="n-input" type="date" value={weekOf} onChange={(e) => setWeekOf(weekStart(e.target.value || today))} />
                    <Button size="sm" variant="primary" icon={CalendarCheck} onClick={markWeek} disabled={bulkBusy || picked.size === 0 || meta.mode !== 'live'}>
                      {bulkBusy ? 'Marking…' : `Mark ${picked.size || ''} paid for payroll week`}
                    </Button>
                  </span>
                }
              >
                Tick payroll-lane performances to mark them paid for a pay week{meta.mode !== 'live' ? ' (live mode only)' : ''}.
              </Notice>
            </div>
          )}
          {isMobile ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {rows.map((r) => (
                <RowCard key={r.id} onClick={() => open(r.id)} ariaLabel={`Open ${r.performer_name} on ${dateLabel(r.performance_date)}`}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 600, flex: '1 1 auto' }}>{r.performer_name}</span>
                    <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>{money(r.actual_cents ?? r.agreed_cents)}</span>
                  </div>
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginTop: 2 }}>{dateLabel(r.performance_date, { year: false })} · {r.venue_name}</div>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6, alignItems: 'center' }}>
                    <LaneBadge lane={r.lane} />
                    <StatusChip status={r.status} />
                    {(r.lane === 'invoice' || r.lane === 'exemption') && <CheckChip status={r.invoice_check_status} />}
                    {!r.performer.payable && r.payment_status !== 'paid' && <Badge tone="warn">Not payable</Badge>}
                  </div>
                </RowCard>
              ))}
            </div>
          ) : (
            <div className="n-card" style={{ overflowX: 'auto' }}>
              <table className="n-table" style={{ minWidth: 900 }}>
                <thead>
                  <tr>
                    {status !== 'paid' && <th style={{ width: 32 }}><span className="n-eyebrow" style={{ fontSize: 'var(--fs-2xs)' }}>Week</span></th>}
                    <th>Date</th>
                    <th>Performer</th>
                    <th>Venue</th>
                    <th className="num">Amount</th>
                    <th>Status</th>
                    <th>Invoice</th>
                    <th>Bill</th>
                    <th>Paid</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const payrollLane = r.lane === 'payroll_employee' || r.lane === 'payroll_contractor';
                    const tickable = payrollLane && r.payment_status !== 'paid' && r.status !== 'mismatch' && r.performer.payable;
                    return (
                      <tr key={r.id} onClick={() => open(r.id)} style={{ cursor: 'pointer' }}>
                        {status !== 'paid' && (
                          <td onClick={(e) => e.stopPropagation()}>
                            {tickable && (
                              <Toggle checked={picked.has(r.id)} onChange={(v) => setPicked((s) => { const n = new Set(s); if (v) n.add(r.id); else n.delete(r.id); return n; })}>
                                <span className="sr-only" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>Pay {r.performer_name} this week</span>
                              </Toggle>
                            )}
                          </td>
                        )}
                        <td style={{ whiteSpace: 'nowrap' }}>{dateLabel(r.performance_date, { year: false })}</td>
                        <td>
                          <div style={{ fontWeight: 500 }}>{r.performer_name}</div>
                          <div style={{ display: 'flex', gap: 4, marginTop: 2 }}><LaneBadge lane={r.lane} />{!r.performer.payable && r.payment_status !== 'paid' && <Badge tone="warn">Not payable</Badge>}</div>
                        </td>
                        <td style={{ color: 'var(--text-soft)' }}>{r.venue_name}</td>
                        <td className="num" style={{ fontWeight: 600 }}>{money(r.actual_cents ?? r.agreed_cents)}</td>
                        <td><StatusChip status={r.status} /></td>
                        <td>{r.lane === 'invoice' || r.lane === 'exemption' ? <CheckChip status={r.invoice_check_status} /> : <span style={{ color: 'var(--muted)' }}>n/a</span>}</td>
                        <td style={{ fontSize: 'var(--fs-sm)' }}>{r.bill && !r.bill.voided_at ? `${r.bill.xero_status ?? 'DRAFT'} ${r.bill.xero_number ?? ''}` : <span style={{ color: 'var(--muted)' }}>—</span>}</td>
                        <td style={{ fontSize: 'var(--fs-sm)', whiteSpace: 'nowrap' }}>
                          {r.payment_status === 'paid' ? <>{dateLabel(r.paid_at?.slice(0, 10) ?? null, { year: false })}{r.paid_reference ? <span style={{ color: 'var(--muted)' }}> · {r.paid_reference}</span> : null}</> : <span style={{ color: 'var(--muted)' }}>Pending</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {openRow && (
        <PaymentDialog row={openRow} meta={meta} onChanged={replaceRow} onClose={() => { open(null); refresh(); }} />
      )}
      {openId && !openRow && data && (
        <div style={{ marginTop: 12 }}>
          <Notice tone="info" action={<Button size="sm" onClick={() => open(null)}>Dismiss</Button>}>That performance isn&rsquo;t in the current filter — widen the dates or the venue to open it.</Notice>
        </div>
      )}
      {isPage && <ToastHost />}
    </div>
  );
}
