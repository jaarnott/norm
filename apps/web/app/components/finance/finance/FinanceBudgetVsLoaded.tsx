'use client';

/**
 * Norm Finance — Budget vs Loaded (`finance_budget_vs_loaded`).
 *
 * Orbit's Budget vs Forecast, with the column that was missing: the locked
 * budget (Norm's store), Loaded's live daily budget (`get_budgets`) and the
 * actual sales, per month of the fiscal year, then per day with week
 * subtotals when a month is opened. The variance keeps Orbit's convention —
 * Loaded minus locked, green above, red below — and adds the share.
 *
 * Under the grid: the push ledger (`fin_budget_pushes` — who sent what to
 * Loaded, when, before and after, and from which door), and "Restore
 * Loaded budgets", which plans a put-back from the ledger's before values
 * and sends it through the card (`loadedhub.set_budgets`, `source=restore`).
 * A day Norm created in Loaded cannot be restored — Loaded has no delete.
 */
import { useMemo, useState } from 'react';
import { ArrowLeft, Download, History, RefreshCw } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import DataTable, { type Column } from '../shared/DataTable';
import PageShell, { Field, FormGrid, ModeBadge, Note, Section, Stack } from '../shared/PageShell';
import { csvFilename, downloadText, toCsv } from '../shared/csv';
import { instantLabel } from '../shared/format';
import { formatCents, formatSignedCents } from '../shared/money';
import { errorText, finSend, useFinQuery } from '../shared/useFinanceApi';
import PushDialog, { type PushRequest } from './PushDialog';
import { FinanceModeBanner, FiscalYearControl, NotReady, Small, Variance, subtotalRow, totalRow, venueOptions } from './bits';
import { daysInMonth, dayCell, fiscalYearMonths, fiscalYearOf, fiscalYearRange, groupIntoWeeks, longDate, monthLabel, rangeLabel, sumCents, type YearMonth } from './logic';
import type { FinMeta, PushRow, RestorePlan, VsLoadedDay, VsLoadedMonthDoc, VsLoadedYearDoc } from './types';
import { holds, useFinanceMeta, useFinanceVenue } from './useFinanceMeta';

const SOURCE_LABEL: Record<PushRow['source'], string> = { manual: 'Typed on the grid', chat: 'Asked in chat', forecast: 'From the forecast', restore: 'Restored' };

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

function RestoreDialog({ venueId, from: from0, to: to0, onClose, onDone }: { venueId: string; from: string; to: string; onClose: () => void; onDone: () => void }) {
  const [from, setFrom] = useState(from0);
  const [to, setTo] = useState(to0);
  const [scope, setScope] = useState<'latest' | 'first'>('latest');
  const [plan, setPlan] = useState<RestorePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [push, setPush] = useState<PushRequest | null>(null);

  const planIt = async () => {
    setBusy(true);
    setError(null);
    try {
      setPlan(await finSend<RestorePlan>('POST', '/api/finance/pushes/restore/plan', { venue_id: venueId, from, to, scope }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (push) {
    return <PushDialog title="Restore Loaded budgets" intro="Each day goes back to the value Loaded held before Norm changed it." request={push} confirmLabel="Restore in Loaded" onDone={onDone} onClose={onClose} />;
  }
  return (
    <Dialog title="Restore Loaded budgets" width={520} onClose={onClose} closeButton>
      <Stack gap={10}>
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          Puts Loaded’s daily budget back to what it held before Norm’s pushes, from the ledger’s before values.
        </div>
        <FormGrid min={140}>
          <Field label="From" id="fin-restore-from"><input id="fin-restore-from" type="date" className="n-input" value={from} onChange={(e) => { setFrom(e.target.value); setPlan(null); }} style={{ width: '100%' }} /></Field>
          <Field label="To" id="fin-restore-to"><input id="fin-restore-to" type="date" className="n-input" value={to} onChange={(e) => { setTo(e.target.value); setPlan(null); }} style={{ width: '100%' }} /></Field>
          <Field label="Back to" id="fin-restore-scope">
            <select id="fin-restore-scope" className="n-select" value={scope} onChange={(e) => { setScope(e.target.value as 'latest' | 'first'); setPlan(null); }} style={{ width: '100%' }}>
              <option value="latest">Before the latest push</option>
              <option value="first">Before Norm’s first push</option>
            </select>
          </Field>
        </FormGrid>
        {error && <Note tone="error">{error}</Note>}
        {plan && (
          <div>
            {plan.days.length === 0 ? (
              <Note>Nothing to restore in this range.</Note>
            ) : (
              <Note>{plan.days.length} day{plan.days.length === 1 ? '' : 's'} would go back — {plan.days.slice(0, 8).map((d) => `${dayCell(d.date)} → ${formatCents(d.amount_cents, 'NZD', 0)}`).join(', ')}{plan.days.length > 8 ? '…' : ''}</Note>
            )}
            {plan.cannot_restore.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Note tone="warn">
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>Cannot be restored</div>
                  <ul style={{ margin: 0, paddingLeft: 18 }}>{plan.cannot_restore.map((c) => <li key={c}>{c}</li>)}</ul>
                </Note>
              </div>
            )}
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <Button onClick={onClose}>Cancel</Button>
          {!plan ? (
            <Button variant="primary" onClick={() => void planIt()} disabled={busy || !from || !to || from > to}>{busy ? 'Planning…' : 'Plan the restore'}</Button>
          ) : (
            <Button variant="primary" disabled={plan.days.length === 0} onClick={() => setPush({ venue_id: venueId, kind: 'restore', days: plan.days.map((d) => ({ date: d.date, amount_cents: d.amount_cents })), allow_zero: true })}>Continue</Button>
          )}
        </div>
      </Stack>
    </Dialog>
  );
}

function Ledger({ venueId, from, to, currency, refresh }: { venueId: string; from: string; to: string; currency: string; refresh: number }) {
  const q = useFinQuery<PushRow[]>('/api/finance/pushes', { venue_id: venueId, from, to, limit: 500 }, refresh);
  const columns: Column<PushRow>[] = [
    { key: 'pushed_at', label: 'When', value: (r) => r.pushed_at, render: (r) => instantLabel(r.pushed_at), nowrap: true },
    { key: 'date', label: 'Day', value: (r) => r.date, render: (r) => longDate(r.date), nowrap: true },
    { key: 'before_cents', label: 'Before', align: 'right', value: (r) => r.before_cents, csv: (r) => (r.before_cents === null ? null : r.before_cents / 100), csvLabel: 'Before ($)', render: (r) => (r.before_cents === null ? <span style={{ color: 'var(--muted)' }}>none</span> : formatCents(r.before_cents, currency, 0)) },
    { key: 'after_cents', label: 'After', align: 'right', value: (r) => r.after_cents, csv: (r) => r.after_cents / 100, csvLabel: 'After ($)', render: (r) => formatCents(r.after_cents, currency, 0) },
    { key: 'source', label: 'Source', value: (r) => SOURCE_LABEL[r.source] ?? r.source, hideOnPhone: true },
    { key: 'pushed_by_name', label: 'Who', value: (r) => r.pushed_by_name || (r.pushed_by ? 'someone' : 'Norm'), hideOnPhone: true },
    { key: 'method', label: 'Verb', value: (r) => (r.method === 'POST' ? 'created' : 'updated'), hideOnPhone: true },
    { key: 'ok', label: 'Result', value: (r) => (r.ok ? 'ok' : 'failed'), render: (r) => (r.ok ? <Badge tone="ok">Done</Badge> : <Badge tone="error">Failed</Badge>) },
  ];
  return (
    <Section title="Push ledger" meta={`${rangeLabel(from, to)} · every change Norm made to Loaded's budget`}>
      {q.error ? (
        <PageState kind="error" title="Couldn’t load the ledger." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} />
      ) : !q.data ? (
        <PageState kind="loading" title="Loading…" />
      ) : (
        <DataTable columns={columns} rows={q.data} rowKey={(r) => r.id} csvName={`budget pushes ${from} ${to}`} initialSort={{ key: 'pushed_at', dir: 'desc' }} emptyTitle="Norm has not changed Loaded's budget in this range." dense maxHeight={420} rowTone={(r) => (r.ok ? undefined : 'error')} />
      )}
    </Section>
  );
}

/** A subtotal or total line of the day table. */
function SumRow({ label, locked, loaded, actual, isMobile, currency, style }: { label: string; locked: number | null; loaded: number | null; actual: number | null; isMobile: boolean; currency: string; style?: React.CSSProperties }) {
  return (
    <tr style={style}>
      <td>{label}</td>
      <td className="num">{formatCents(locked, currency, 0)}</td>
      <td className="num">{formatCents(loaded, currency, 0)}</td>
      {!isMobile && <td className="num">{formatCents(actual, currency, 0)}</td>}
      <td className="num"><Variance cents={locked === null || loaded === null ? null : loaded - locked} base={locked} currency={currency} /></td>
      {!isMobile && <td className="num"><Variance cents={locked === null || actual === null ? null : actual - locked} base={locked} currency={currency} /></td>}
    </tr>
  );
}

function MonthView({ meta, venueId, month, refresh, onBack }: { meta: FinMeta; venueId: string; month: YearMonth; refresh: number; onBack: () => void }) {
  const { isMobile } = useBreakpoint();
  const q = useFinQuery<VsLoadedMonthDoc>('/api/finance/budgets/vs-loaded', { venue_id: venueId, year: month.year, month: month.month }, refresh);
  const currency = meta.currency;
  const days: VsLoadedDay[] = useMemo(() => (q.data?.days ?? []).map((d) => ({ ...d, variance_cents: d.variance_cents ?? (d.loaded_cents === null || d.locked_cents === null ? null : d.loaded_cents - d.locked_cents) })), [q.data]);
  const weeks = useMemo(() => groupIntoWeeks(days), [days]);
  const totals = {
    locked: sumCents(days, (d) => d.locked_cents),
    loaded: sumCents(days, (d) => d.loaded_cents),
    actual: sumCents(days, (d) => d.actual_cents),
  };
  const csv = () => downloadText(
    csvFilename(`budget vs loaded ${monthLabel(month)}`),
    toCsv(['Date', 'Day', 'Locked budget', 'Loaded budget', 'Actual', 'Loaded − locked', 'Actual − locked'], days.map((d) => [d.date, dayCell(d.date, d.weekday), d.locked_cents === null ? null : d.locked_cents / 100, d.loaded_cents === null ? null : d.loaded_cents / 100, d.actual_cents === null ? null : d.actual_cents / 100, d.variance_cents === null || d.variance_cents === undefined ? null : d.variance_cents / 100, d.actual_cents === null || d.locked_cents === null ? null : (d.actual_cents - d.locked_cents) / 100])),
  );
  return (
    <Section
      title={monthLabel(month)}
      meta="by day, with week subtotals"
      padded={false}
      actions={
        <>
          <Button size="sm" variant="quiet" icon={ArrowLeft} onClick={onBack}>All months</Button>
          <Button size="sm" variant="quiet" icon={Download} onClick={csv} disabled={days.length === 0}>CSV</Button>
        </>
      }
    >
      {q.error ? (
        <div style={{ padding: 12 }}><PageState kind="error" title="Couldn’t load the month." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} /></div>
      ) : !q.data ? (
        <PageState kind="loading" title="Loading…" />
      ) : (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          <table className="n-table" style={{ width: '100%' }} aria-label={`Budget vs Loaded for ${monthLabel(month)}`}>
            <thead>
              <tr>
                <th>Day</th>
                <th className="num">Locked</th>
                <th className="num">Loaded</th>
                {!isMobile && <th className="num">Actual</th>}
                <th className="num">Loaded − locked</th>
                {!isMobile && <th className="num">Actual − locked</th>}
              </tr>
            </thead>
            <tbody>
              {weeks.map((w) => (
                <WeekRows key={w.week_start} week={w} isMobile={isMobile} currency={currency} />
              ))}
              <SumRow label="Month" locked={totals.locked} loaded={totals.loaded} actual={totals.actual} isMobile={isMobile} currency={currency} style={totalRow} />
            </tbody>
          </table>
          {(q.data.warnings ?? []).map((w) => <Small key={w} tone="warn" style={{ padding: '0 12px 8px' }}>{w}</Small>)}
        </div>
      )}
    </Section>
  );
}

function WeekRows({ week, isMobile, currency }: { week: { week_start: string; rows: VsLoadedDay[] }; isMobile: boolean; currency: string }) {
  return (
    <>
      {week.rows.map((d) => (
        <tr key={d.date}>
          <td style={{ whiteSpace: 'nowrap' }}>
            {dayCell(d.date, d.weekday)}
            {d.holiday && <span className="n-badge" style={{ marginLeft: 6 }}>{d.holiday}</span>}
            {d.note && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{d.note}</div>}
          </td>
          <td className="num">{formatCents(d.locked_cents, currency, 0)}</td>
          <td className="num">{formatCents(d.loaded_cents, currency, 0)}</td>
          {!isMobile && <td className="num" style={{ color: 'var(--text-soft)' }}>{formatCents(d.actual_cents, currency, 0)}</td>}
          <td className="num"><Variance cents={d.variance_cents} base={d.locked_cents} currency={currency} /></td>
          {!isMobile && <td className="num"><Variance cents={d.locked_cents === null || d.actual_cents === null ? null : d.actual_cents - d.locked_cents} base={d.locked_cents} currency={currency} /></td>}
        </tr>
      ))}
      <SumRow
        label={`Week of ${dayCell(week.week_start)}`}
        locked={sumCents(week.rows, (d) => d.locked_cents)}
        loaded={sumCents(week.rows, (d) => d.loaded_cents)}
        actual={sumCents(week.rows, (d) => d.actual_cents)}
        isMobile={isMobile}
        currency={currency}
        style={subtotalRow}
      />
    </>
  );
}

export default function FinanceBudgetVsLoaded({ props }: DisplayBlockProps) {
  const page = !!props?.persistVenue;
  const { meta, error, status, reload } = useFinanceMeta();
  const [venueId, setVenue] = useFinanceVenue(meta);
  const [fy, setFy] = useState<number | null>(null);
  const [month, setMonth] = useState<YearMonth | null>(null);
  const [restore, setRestore] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const { isMobile } = useBreakpoint();
  const fiscalYear = fy ?? (meta ? fiscalYearOf(meta.today, meta.fiscal_year_start_month) : 2027);
  const startMonth = meta?.fiscal_year_start_month ?? 4;
  const year = useFinQuery<VsLoadedYearDoc>(meta && venueId && !month ? '/api/finance/budgets/vs-loaded' : null, { venue_id: venueId, fiscal_year: fiscalYear }, refresh);

  if (!meta) return <NotReady title="Budget vs Loaded" page={page} error={error} status={status} reload={reload} />;
  const canWrite = holds(meta, 'budgets:write');
  const currency = meta.currency;
  const range = month ? { from: `${month.year}-${pad(month.month)}-01`, to: `${month.year}-${pad(month.month)}-${pad(daysInMonth(month))}` } : fiscalYearRange(fiscalYear, startMonth);
  const months = fiscalYearMonths(fiscalYear, startMonth);
  const byKey = new Map((year.data?.months ?? []).map((m) => [`${m.year}-${m.month}`, m]));
  const t = year.data?.totals;

  return (
    <PageShell
      isPage={page}
      title="Budget vs Loaded"
      meta="the locked budget, Loaded's live budget and the actual sales"
      status={<ModeBadge mode={meta.finance_mode} />}
      venues={venueOptions(meta)}
      venueId={venueId}
      onVenueChange={(id) => { setVenue(id); setMonth(null); }}
      venueTestId="fin-vs-venue"
      actions={
        <>
          <FiscalYearControl meta={meta} value={fiscalYear} onChange={(v) => { setFy(v); setMonth(null); }} />
          <Button size="sm" variant="quiet" icon={RefreshCw} aria-label="Refresh" onClick={() => setRefresh((n) => n + 1)} />
          {canWrite && (
            <Button size="sm" icon={History} onClick={() => setRestore(true)} disabled={meta.finance_mode !== 'live'} title={meta.finance_mode !== 'live' ? 'Loaded is written only once Norm Finance is live.' : undefined}>
              Restore Loaded budgets
            </Button>
          )}
        </>
      }
      testId="fin-vs-loaded"
    >
      <FinanceModeBanner mode={meta.finance_mode} />
      {!venueId ? (
        <PageState kind="empty" title="No venues." />
      ) : (
        <Stack>
          {month ? (
            <MonthView meta={meta} venueId={venueId} month={month} refresh={refresh} onBack={() => setMonth(null)} />
          ) : (
            <Section title={`FY${fiscalYear} by month`} meta="open a month for its days" padded={false}>
              {year.error ? (
                <div style={{ padding: 12 }}><PageState kind="error" title="Couldn’t load the year." detail={year.error} action={<Button size="sm" onClick={year.retry}>Try again</Button>} /></div>
              ) : !year.data ? (
                <PageState kind="loading" title="Loading…" />
              ) : (
                <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                  <table className="n-table" style={{ width: '100%' }} aria-label={`Budget vs Loaded, FY${fiscalYear}`}>
                    <thead>
                      <tr>
                        <th>Month</th>
                        <th className="num">Locked</th>
                        <th className="num">Loaded</th>
                        {!isMobile && <th className="num">Actual</th>}
                        <th className="num">Loaded − locked</th>
                        {!isMobile && <th className="num">Actual − locked</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {months.map((m) => {
                        const r = byKey.get(`${m.year}-${m.month}`);
                        const locked = r?.locked_cents ?? null;
                        const loaded = r?.loaded_cents ?? null;
                        const actual = r?.actual_cents ?? null;
                        const variance = r?.variance_cents ?? (locked === null || loaded === null ? null : loaded - locked);
                        return (
                          <tr key={`${m.year}-${m.month}`} onClick={() => setMonth(m)} style={{ cursor: 'pointer' }}>
                            <td><Button variant="link" size="sm" onClick={(e) => { e.stopPropagation(); setMonth(m); }}>{monthLabel(m)}</Button></td>
                            <td className="num">{formatCents(locked, currency, 0)}</td>
                            <td className="num">{formatCents(loaded, currency, 0)}</td>
                            {!isMobile && <td className="num" style={{ color: 'var(--text-soft)' }}>{formatCents(actual, currency, 0)}</td>}
                            <td className="num"><Variance cents={variance} base={locked} currency={currency} /></td>
                            {!isMobile && <td className="num"><Variance cents={locked === null || actual === null ? null : actual - locked} base={locked} currency={currency} /></td>}
                          </tr>
                        );
                      })}
                      <tr style={totalRow}>
                        <td>Year</td>
                        <td className="num">{formatCents(t?.locked_cents, currency, 0)}</td>
                        <td className="num">{formatCents(t?.loaded_cents, currency, 0)}</td>
                        {!isMobile && <td className="num">{formatCents(t?.actual_cents, currency, 0)}</td>}
                        <td className="num"><Variance cents={t?.variance_cents} base={t?.locked_cents} currency={currency} /></td>
                        {!isMobile && <td className="num">{t && t.locked_cents !== null && t.actual_cents !== null ? <Variance cents={t.actual_cents - t.locked_cents} base={t.locked_cents} currency={currency} /> : '—'}</td>}
                      </tr>
                    </tbody>
                  </table>
                  <Small style={{ padding: '0 12px 10px' }}>Green: Loaded (or the actual) is above the locked budget; red: below. {formatSignedCents(0, currency, 0)} means level.</Small>
                  {(year.data.warnings ?? []).map((w) => <Small key={w} tone="warn" style={{ padding: '0 12px 8px' }}>{w}</Small>)}
                </div>
              )}
            </Section>
          )}
          <Ledger venueId={venueId} from={range.from} to={range.to} currency={currency} refresh={refresh} />
        </Stack>
      )}
      {restore && venueId && <RestoreDialog venueId={venueId} from={range.from} to={range.to} onClose={() => setRestore(false)} onDone={() => setRefresh((n) => n + 1)} />}
    </PageShell>
  );
}
