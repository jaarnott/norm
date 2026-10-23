'use client';

/**
 * Norm Finance — Budgets (`finance_budgets`).
 *
 * The locked daily sales budget per venue and fiscal year (the real asset,
 * 04 §12 A), in four tabs:
 *
 *  - **Day grid** — Day · Locked budget · Loaded budget · Note for one
 *    venue-month. The Loaded column is Orbit's Managers Budget habit kept
 *    (plan §8): type a number, see the card, confirm, and Loaded has it —
 *    `loadedhub.set_budgets_typed` through the page helper, recorded
 *    `source=manual`; the card is the per-person always-allow point, so the
 *    preview may come back already done. "Sync month to Loaded" sends the
 *    locked month ($0 days only with "allow zero"; days Loaded has no
 *    budget for are created). The locked column is edited only on an
 *    unlocked (draft) version.
 *  - **Versions** — import / manual / forecast versions, lock and unlock
 *    (`budgets:write`); the current budget is the latest locked one.
 *  - **Monthly lines** — the carried line items (D33), a value or a ratio.
 *  - **Import** — paste or a CSV of `date, amount, note` (Orbit's 12-block
 *    tab as an optional parser) → preview → apply as a new version.
 */
import { useEffect, useMemo, useState } from 'react';
import { Download, RefreshCw, Send } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import PageShell, { ModeBadge, Note, Section } from '../shared/PageShell';
import { csvFilename, downloadText, toCsv } from '../shared/csv';
import { formatCents } from '../shared/money';
import { toast } from '../shared/Toasts';
import { errorText, finGet, finSend, useFinQuery } from '../shared/useFinanceApi';
import BudgetImport from './BudgetImport';
import BudgetLines from './BudgetLines';
import BudgetVersions from './BudgetVersions';
import PushDialog, { type PushRequest } from './PushDialog';
import { FinanceModeBanner, FiscalYearControl, MonthNav, NotReady, Small, cellInput, readPageParam, totalRow, venueOptions, writePageParam } from './bits';
import { centsInput, dayCell, defaultMonth, fiscalYearMonths, fiscalYearOf, longDate, monthLabel, parseAmount, sumCents, type YearMonth } from './logic';
import type { BudgetDay, DaysDoc, FinMeta, SyncMonthDoc, VersionsDoc } from './types';
import { holds, useFinanceMeta, useFinanceVenue } from './useFinanceMeta';

const TABS = [
  { id: 'grid', label: 'Day grid' },
  { id: 'versions', label: 'Versions' },
  { id: 'lines', label: 'Monthly lines' },
  { id: 'import', label: 'Import' },
] as const;
type TabId = (typeof TABS)[number]['id'];

/** One Loaded-budget cell: type, leave the cell (or Enter), and the card opens. */
function LoadedCell({ day, currency, disabled, title, onCommit }: { day: BudgetDay; currency: string; disabled: boolean; title?: string; onCommit: (cents: number) => void }) {
  // Remounted by its key when Loaded's value changes, so no effect is needed.
  const [text, setText] = useState(() => centsInput(day.loaded_cents));
  const commit = () => {
    const v = parseAmount(text);
    if (v === null) { setText(centsInput(day.loaded_cents)); return; }
    if (v === day.loaded_cents) return;
    onCommit(v);
  };
  if (disabled) return <span title={title} style={{ color: day.loaded_cents === null ? 'var(--muted)' : undefined }}>{formatCents(day.loaded_cents, currency, 0)}</span>;
  return (
    <input
      className="n-input"
      inputMode="decimal"
      aria-label={`Loaded budget for ${longDate(day.date)}`}
      value={text}
      placeholder={day.loaded_cents === null ? 'none' : undefined}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setText(centsInput(day.loaded_cents)); }}
      style={cellInput}
    />
  );
}

function SyncMonthDialog({ meta, venueId, month, onClose, onDone }: { meta: FinMeta; venueId: string; month: YearMonth; onClose: () => void; onDone: () => void }) {
  const [plan, setPlan] = useState<SyncMonthDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [allowZero, setAllowZero] = useState(false);
  const [push, setPush] = useState<PushRequest | null>(null);
  useEffect(() => {
    let alive = true;
    finGet<SyncMonthDoc>('/api/finance/budgets/sync-month', { venue_id: venueId, year: month.year, month: month.month })
      .then((p) => { if (alive) setPlan(p); })
      .catch((e: unknown) => { if (alive) setError(errorText(e)); });
    return () => { alive = false; };
  }, [venueId, month.year, month.month]);

  if (push) {
    return (
      <PushDialog
        title={`Sync ${monthLabel(month)} to Loaded`}
        intro="The locked budget for every day of the month, as Loaded will hold it. Amounts include GST."
        request={push}
        onDone={onDone}
        onClose={onClose}
      />
    );
  }
  const days = plan?.params.days ?? [];
  const total = days.reduce((s, d) => s + Math.round(d.amount * 100), 0);
  return (
    <Dialog title={`Sync ${monthLabel(month)} to Loaded`} width={480} onClose={onClose} closeButton>
      {error ? (
        <PageState kind="error" title="Can’t prepare the sync." detail={error} />
      ) : !plan ? (
        <PageState kind="loading" title="Reading the locked budget…" />
      ) : days.length === 0 ? (
        <PageState kind="empty" title="No locked budget for this month." detail="Lock a version on the Versions tab first." />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          <div>
            <strong style={{ color: 'var(--text)' }}>{days.length} day{days.length === 1 ? '' : 's'}</strong>, {formatCents(total, meta.currency, 0)} in total, will be set in Loaded from the locked budget.
          </div>
          {plan.days_without_budget.length > 0 && (
            <Note tone="warn">
              Loaded has no budget yet for {plan.days_without_budget.length} of these days — they will be created: {plan.days_without_budget.map((d) => dayCell(d)).join(', ')}. Loaded cannot delete a budget, so a created day stays.
            </Note>
          )}
          {plan.zero_days.length > 0 && (
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', cursor: 'pointer' }}>
              <input type="checkbox" checked={allowZero} onChange={(e) => setAllowZero(e.target.checked)} style={{ marginTop: 3, accentColor: 'var(--accent)' }} />
              <span>
                {plan.zero_days.length} day{plan.zero_days.length === 1 ? ' is' : 's are'} budgeted at $0 ({plan.zero_days.map((d) => dayCell(d)).join(', ')}). Send them as $0 too — otherwise Loaded keeps what it has for those days.
              </span>
            </label>
          )}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 6 }}>
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" onClick={() => setPush({ venue_id: venueId, kind: 'typed', allow_zero: allowZero, days: days.map((d) => ({ date: d.date, amount_cents: Math.round(d.amount * 100) })) })}>
              Continue
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function DayGrid({ meta, venueId, fy, canWrite }: { meta: FinMeta; venueId: string; fy: number; canWrite: boolean }) {
  const { isMobile } = useBreakpoint();
  const months = useMemo(() => fiscalYearMonths(fy, meta.fiscal_year_start_month), [fy, meta.fiscal_year_start_month]);
  const [month, setMonth] = useState<YearMonth>(() => defaultMonth(fy, meta.fiscal_year_start_month, meta.today));
  useEffect(() => { setMonth(defaultMonth(fy, meta.fiscal_year_start_month, meta.today)); }, [fy, meta.fiscal_year_start_month, meta.today]);
  const [versionId, setVersionId] = useState<string>('current');
  useEffect(() => { setVersionId('current'); }, [venueId, fy]);
  const [refresh, setRefresh] = useState(0);
  const versions = useFinQuery<VersionsDoc>('/api/finance/budgets/versions', { venue_id: venueId, fiscal_year: fy }, refresh);
  const days = useFinQuery<DaysDoc>('/api/finance/budgets/days', { venue_id: venueId, year: month.year, month: month.month, version_id: versionId === 'current' ? undefined : versionId }, refresh);
  const [edits, setEdits] = useState<Record<string, { amount_cents: number | null; note: string }>>({});
  const [saving, setSaving] = useState(false);
  const [push, setPush] = useState<{ request: PushRequest; title: string } | null>(null);
  const [sync, setSync] = useState(false);
  useEffect(() => { setEdits({}); }, [venueId, month.year, month.month, versionId]);

  const doc = days.data;
  const live = meta.finance_mode === 'live';
  const editable = !!doc && !doc.locked && !!doc.version_id && canWrite;
  const loadedEditable = canWrite && live;
  const loadedHint = !canWrite ? 'Editing budgets needs the Budgets permission on your role.' : !live ? 'Loaded is written only once Norm Finance is live.' : undefined;
  const currency = meta.currency;

  const rows = useMemo(() => (doc?.days ?? []).map((d) => ({ ...d, ...(edits[d.date] ? { amount_cents: edits[d.date].amount_cents, note: edits[d.date].note } : {}) })), [doc, edits]);
  const lockedTotal = sumCents(rows, (r) => r.amount_cents);
  const loadedTotal = sumCents(rows, (r) => r.loaded_cents);
  const actualTotal = sumCents(rows, (r) => r.actual_cents);
  const dirty = Object.keys(edits).length > 0;

  const saveEdits = async () => {
    if (!doc?.version_id) return;
    setSaving(true);
    try {
      const body = { days: Object.entries(edits).map(([date, e]) => ({ date, amount_cents: e.amount_cents ?? 0, note: e.note || null })) };
      const r = await finSend<{ saved: number }>('PUT', `/api/finance/budgets/versions/${encodeURIComponent(doc.version_id)}/days`, body);
      toast(`Saved ${r.saved} day${r.saved === 1 ? '' : 's'}.`, 'ok');
      setEdits({});
      setRefresh((n) => n + 1);
    } catch (e) {
      toast(errorText(e), 'error');
    } finally {
      setSaving(false);
    }
  };

  const csv = () => {
    downloadText(
      csvFilename(`budget ${meta.venues.find((v) => v.id === venueId)?.name ?? ''} ${monthLabel(month)}`),
      toCsv(['Date', 'Day', 'Locked budget', 'Loaded budget', 'Actual', 'Holiday', 'Note'], rows.map((r) => [r.date, dayCell(r.date, r.weekday), r.amount_cents === null ? null : r.amount_cents / 100, r.loaded_cents === null ? null : r.loaded_cents / 100, r.actual_cents === null ? null : r.actual_cents / 100, r.holiday, r.note])),
    );
  };

  const versionLabel = (id: string | null) => {
    const v = versions.data?.versions.find((x) => x.id === id);
    return v ? `${v.label || v.source}${v.locked_at ? '' : ' (draft)'}` : '—';
  };

  return (
    <Section
      title={monthLabel(month)}
      meta={doc ? (doc.locked ? `locked budget · ${versionLabel(doc.version_id)}` : doc.version_id ? `draft · ${versionLabel(doc.version_id)}` : 'no locked budget for this year') : undefined}
      padded={false}
      actions={
        <>
          <MonthNav months={months} value={month} onChange={setMonth} />
          <select aria-label="Budget version" className="n-select" value={versionId} onChange={(e) => setVersionId(e.target.value)} style={{ maxWidth: '100%' }}>
            <option value="current">Current locked budget</option>
            {(versions.data?.versions ?? []).map((v) => (
              <option key={v.id} value={v.id}>{v.label || v.source} · {v.locked_at ? 'locked' : 'draft'}</option>
            ))}
          </select>
          <Button size="sm" variant="quiet" icon={RefreshCw} aria-label="Refresh" onClick={() => setRefresh((n) => n + 1)} />
          <Button size="sm" variant="quiet" icon={Download} onClick={csv} disabled={rows.length === 0}>CSV</Button>
          {canWrite && (
            <Button size="sm" variant="primary" icon={Send} onClick={() => setSync(true)} disabled={!live || !doc?.locked} title={!live ? loadedHint : !doc?.locked ? 'Lock a version first' : undefined}>
              Sync month to Loaded
            </Button>
          )}
        </>
      }
    >
      {days.error ? (
        <div style={{ padding: 12 }}><PageState kind="error" title="Couldn’t load the month." detail={days.error} action={<Button size="sm" onClick={days.retry}>Try again</Button>} /></div>
      ) : !doc ? (
        <PageState kind="loading" title="Loading…" />
      ) : (
        <>
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch', opacity: days.loading ? 0.6 : 1 }}>
            <table className="n-table" style={{ width: '100%' }} aria-label={`Budget grid for ${monthLabel(month)}`}>
              <thead>
                <tr>
                  <th>Day</th>
                  <th className="num">Locked budget</th>
                  <th className="num">Loaded budget</th>
                  {!isMobile && <th className="num">Actual</th>}
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.date} style={d.weekday >= 6 ? { background: 'var(--surface)' } : undefined}>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {dayCell(d.date, d.weekday)}
                      {d.holiday && <span className="n-badge" style={{ marginLeft: 6 }} title="Public holiday">{d.holiday}</span>}
                    </td>
                    <td className="num">
                      {editable ? (
                        <input
                          className="n-input"
                          inputMode="decimal"
                          aria-label={`Locked budget for ${longDate(d.date)}`}
                          value={edits[d.date] ? centsInput(edits[d.date].amount_cents) : centsInput(d.amount_cents)}
                          onChange={(e) => setEdits((prev) => ({ ...prev, [d.date]: { amount_cents: parseAmount(e.target.value), note: prev[d.date]?.note ?? d.note ?? '' } }))}
                          style={cellInput}
                        />
                      ) : (
                        <span style={{ color: d.amount_cents === null ? 'var(--muted)' : undefined }}>{formatCents(d.amount_cents, currency, 0)}</span>
                      )}
                    </td>
                    <td className="num">
                      <LoadedCell
                        key={`${d.date}:${d.loaded_cents ?? 'none'}`}
                        day={d}
                        currency={currency}
                        disabled={!loadedEditable}
                        title={loadedHint}
                        onCommit={(v) => setPush({ title: `Set Loaded budget — ${longDate(d.date)}`, request: { venue_id: venueId, kind: 'typed', days: [{ date: d.date, amount_cents: v }], allow_zero: v === 0 } })}
                      />
                    </td>
                    {!isMobile && <td className="num" style={{ color: 'var(--text-soft)' }}>{formatCents(d.actual_cents, currency, 0)}</td>}
                    <td style={{ minWidth: 140 }}>
                      {editable ? (
                        <input
                          className="n-input"
                          aria-label={`Note for ${longDate(d.date)}`}
                          value={edits[d.date]?.note ?? d.note ?? ''}
                          onChange={(e) => setEdits((prev) => ({ ...prev, [d.date]: { amount_cents: prev[d.date]?.amount_cents ?? d.amount_cents, note: e.target.value } }))}
                          style={{ width: '100%', padding: '4px 8px' }}
                        />
                      ) : (
                        <span style={{ color: 'var(--text-soft)', fontSize: 'var(--fs-sm)' }}>{d.note || ''}</span>
                      )}
                    </td>
                  </tr>
                ))}
                <tr style={totalRow}>
                  <td>Total</td>
                  <td className="num">{formatCents(lockedTotal, currency, 0)}</td>
                  <td className="num">{formatCents(loadedTotal, currency, 0)}</td>
                  {!isMobile && <td className="num">{formatCents(actualTotal, currency, 0)}</td>}
                  <td />
                </tr>
              </tbody>
            </table>
          </div>
          <div style={{ padding: '8px 12px 12px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {editable ? (
              <>
                <Button size="sm" variant="primary" onClick={() => void saveEdits()} disabled={!dirty || saving}>{saving ? 'Saving…' : `Save changes${dirty ? ` (${Object.keys(edits).length})` : ''}`}</Button>
                <Button size="sm" variant="quiet" onClick={() => setEdits({})} disabled={!dirty || saving}>Discard</Button>
                <Small style={{ marginTop: 0 }}>Editing the draft version; lock it on the Versions tab to make it the current budget.</Small>
              </>
            ) : (
              <Small style={{ marginTop: 0 }}>
                {doc.locked ? 'The locked budget is read-only here — unlock it or create a new version on the Versions tab to change it.' : !doc.version_id ? 'No locked budget for this year yet: import one or create a version.' : 'Editing budgets needs the Budgets permission.'}
                {loadedHint ? ` ${loadedHint}` : ' Type into the Loaded column to set that day in Loaded.'}
              </Small>
            )}
            {doc.warnings.map((w) => <Small key={w} tone="warn" style={{ marginTop: 0 }}>{w}</Small>)}
          </div>
        </>
      )}
      {push && (
        <PushDialog title={push.title} intro="Loaded's budget for the day, as you typed it. Amounts include GST." request={push.request} onDone={() => setRefresh((n) => n + 1)} onClose={() => setPush(null)} />
      )}
      {sync && <SyncMonthDialog meta={meta} venueId={venueId} month={month} onClose={() => setSync(false)} onDone={() => setRefresh((n) => n + 1)} />}
    </Section>
  );
}

export default function FinanceBudgets({ props }: DisplayBlockProps) {
  const page = !!props?.persistVenue;
  const { meta, error, status, reload } = useFinanceMeta();
  const [venueId, setVenue] = useFinanceVenue(meta);
  const [fy, setFy] = useState<number | null>(null);
  // The tab rides in the address bar (`?view=`): the app shell remounts a
  // page when the shared venue changes, and a refresh should land back here.
  const [tab, setTab] = useState<TabId>(() => {
    const v = page ? readPageParam('view') : null;
    return TABS.some((t) => t.id === v) ? (v as TabId) : 'grid';
  });
  const choose = (id: string) => {
    if (!TABS.some((t) => t.id === id)) return;
    setTab(id as TabId);
    if (page) writePageParam('view', id === 'grid' ? null : id);
  };
  const fiscalYear = fy ?? (meta ? fiscalYearOf(meta.today, meta.fiscal_year_start_month) : 2027);

  if (!meta) return <NotReady title="Budgets" page={page} error={error} status={status} reload={reload} />;
  const canWrite = holds(meta, 'budgets:write');
  const venue = meta.venues.find((v) => v.id === venueId);

  return (
    <PageShell
      isPage={page}
      title="Budgets"
      meta={venue ? `${venue.name} · ${canWrite ? 'you can edit budgets' : 'read-only'}` : undefined}
      status={<ModeBadge mode={meta.finance_mode} />}
      venues={venueOptions(meta)}
      venueId={venueId}
      onVenueChange={setVenue}
      venueTestId="fin-budgets-venue"
      actions={<FiscalYearControl meta={meta} value={fiscalYear} onChange={setFy} />}
      toolbar={<Tabs items={TABS.map((t) => ({ id: t.id, label: t.label, testId: `fin-budgets-tab-${t.id}` }))} value={tab} onChange={choose} label="Budgets" />}
      testId="fin-budgets"
    >
      <FinanceModeBanner mode={meta.finance_mode} />
      {!venueId ? (
        <PageState kind="empty" title="No venues." detail="Ask an owner for access to a venue." />
      ) : tab === 'grid' ? (
        <DayGrid meta={meta} venueId={venueId} fy={fiscalYear} canWrite={canWrite} />
      ) : tab === 'versions' ? (
        <BudgetVersions meta={meta} venueId={venueId} fy={fiscalYear} canWrite={canWrite} />
      ) : tab === 'lines' ? (
        <BudgetLines meta={meta} venueId={venueId} fy={fiscalYear} canWrite={canWrite} />
      ) : (
        <BudgetImport meta={meta} venueId={venueId} fy={fiscalYear} canWrite={canWrite} onImported={() => setTab('versions')} />
      )}
      {!canWrite && tab !== 'grid' && <div style={{ marginTop: 8 }}><Badge tone="neutral">Read-only</Badge></div>}
    </PageShell>
  );
}
