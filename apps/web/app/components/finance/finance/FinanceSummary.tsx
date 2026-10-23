'use client';

/**
 * Norm Finance — Weekly Summary (`finance_summary`).
 *
 * One page in place of Orbit's Financial KPIs, Financial Report and
 * Financial Summary: `GET /api/finance/summary` runs
 * `loadedhub.get_weekly_financials` per venue the viewer may see (the
 * server decides; a group row comes back only for `payroll:read` holders),
 * F2's `finance_weekly` block renders the Loaded side, and under it the
 * budget P&L: the locked budget and the monthly lines beside the week's
 * actual sales and wages (adjusted by the payroll reconciliation), for the
 * week and the month to date — the Financial Summary's rows (04 §7, D33).
 * Printing is the browser's (no PDF, D21); the grid also downloads as CSV.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Download, Printer, RefreshCw } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import FinanceWeekly from '../../display/FinanceWeekly';
import { lastCompletedSunday, makeMoney, shiftWeek } from '../../display/financeWeekly';
import { printDocument } from '../../functions/events/PrintView';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageState from '../../ui/PageState';
import VenueSelect from '../../ui/VenueSelect';
import PageShell, { ModeBadge } from '../shared/PageShell';
import { csvFilename, downloadText } from '../shared/csv';
import { errorText, finGet } from '../shared/useFinanceApi';
import { FinanceModeBanner, NotReady, venueOptions } from './bits';
import { budgetPnl, pct, summaryCsv, type PnlRow } from './logic';
import type { BudgetPlan, SummaryDoc, VenueSummary } from './types';
import { useFinanceMeta } from './useFinanceMeta';

function PnlTable({ rows, money, title }: { rows: { label: string; week: PnlRow; mtd: PnlRow }[]; money: (v: number | null | undefined) => string; title: string }) {
  const fmt = (r: PnlRow, which: 'budget' | 'actual') => (r.kind === 'pct' ? pct(r[which]) : money(r[which]));
  const emphasis = (r: PnlRow): React.CSSProperties =>
    r.emphasis === 'bold' ? { fontWeight: 600 } : r.emphasis === 'subtotal' ? { fontWeight: 600, borderTop: '2px solid var(--line-strong)' } : r.emphasis === 'italic' ? { fontStyle: 'italic', color: 'var(--text-soft)', fontSize: 'var(--fs-sm)' } : {};
  return (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <table className="n-table" style={{ width: '100%', minWidth: 520 }} aria-label={title}>
        <thead>
          <tr>
            <th />
            <th colSpan={2} style={{ textAlign: 'center' }}>Week</th>
            <th colSpan={2} style={{ textAlign: 'center' }}>Month to date</th>
          </tr>
          <tr>
            <th />
            <th className="num">Actual</th>
            <th className="num">Budget</th>
            <th className="num">Actual</th>
            <th className="num">Budget</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            // Three rows read "as % of sales": the key needs the position too.
            <tr key={`${i}:${r.label}`}>
              <td style={{ ...emphasis(r.week), color: r.week.emphasis === 'italic' ? 'var(--text-soft)' : undefined }}>
                {r.label}
                {r.week.note && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontStyle: 'normal', fontWeight: 400 }}>{r.week.note}</div>}
              </td>
              <td className="num" style={{ ...emphasis(r.week), fontWeight: r.week.emphasis === 'italic' ? 400 : 500 }}>{fmt(r.week, 'actual')}</td>
              <td className="num" style={{ ...emphasis(r.week), color: 'var(--text-soft)' }}>{fmt(r.week, 'budget')}</td>
              <td className="num" style={emphasis(r.mtd)}>{fmt(r.mtd, 'actual')}</td>
              <td className="num" style={{ ...emphasis(r.mtd), color: 'var(--text-soft)' }}>{fmt(r.mtd, 'budget')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Fold({ title, meta, open, onToggle, children }: { title: string; meta?: string; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <section className="n-card" style={{ minWidth: 0, padding: 0, overflow: 'hidden' }}>
      <button type="button" onClick={onToggle} aria-expanded={open} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', background: 'transparent', border: 'none', font: 'inherit', color: 'var(--text)', cursor: 'pointer', textAlign: 'left' }}>
        <Icon icon={open ? ChevronDown : ChevronRight} size="dense" label={open ? 'Collapse' : 'Expand'} />
        <span style={{ fontWeight: 600, flex: 1 }}>{title}</span>
        {meta && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</span>}
      </button>
      {open && <div style={{ padding: '0 12px 12px' }}>{children}</div>}
    </section>
  );
}

/** The P&L rows for one venue, week beside month to date. */
function venuePnl(v: VenueSummary): { label: string; week: PnlRow; mtd: PnlRow }[] | null {
  if (!v.week || !v.mtd) return null;
  const plan = (v.week.budget_plan as unknown as BudgetPlan | null) ?? null;
  const week = budgetPnl(v.week, plan?.week ?? null, v.xero?.week ?? null);
  const mtd = budgetPnl(v.mtd, plan?.mtd ?? null, v.xero?.mtd ?? null);
  return week.map((w, i) => ({ label: w.label, week: w, mtd: mtd[i] ?? w }));
}

export default function FinanceSummary({ props }: DisplayBlockProps) {
  const page = !!props?.persistVenue;
  const { meta, error: metaError, status, reload } = useFinanceMeta();
  const [weekEnding, setWeekEnding] = useState<string>(() => lastCompletedSunday());
  const [venue, setVenue] = useState<string>('all');
  const [doc, setDoc] = useState<SummaryDoc | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const sheetRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLIFrameElement | null>(null);

  const load = useCallback(async (we: string, v: string) => {
    setLoading(true);
    setError(null);
    try {
      setDoc(await finGet<SummaryDoc>('/api/finance/summary', { week_ending: we, venues: v === 'all' ? undefined : v }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (meta) void load(weekEnding, venue);
  }, [meta, weekEnding, venue, load]);

  useEffect(() => () => { frameRef.current?.remove(); }, []);

  const money = useMemo(() => makeMoney(meta?.currency ?? null), [meta?.currency]);
  const weekLabel = doc?.week?.label?.replace('Week ending ', 'WE ') || 'Week';
  const mtdLabel = doc?.mtd?.label ? `MTD ${doc.mtd.label}` : 'Month to date';

  // F2's block draws its tiles from a group. Without one (a manager: no
  // `payroll:read`) each venue is shown as its own block, standing in as its
  // own "group" — the tiles then read that venue's figures, never a sum the
  // person may not see.
  const blocks = useMemo(() => {
    if (!doc) return [];
    const base = { week: doc.week, mtd: doc.mtd, warnings: doc.warnings, notes: doc.notes };
    if (doc.group) {
      const title = doc.venues.length === 1 ? doc.venues[0].venue : 'Weekly summary';
      return [{ key: 'all', data: { ...base, venues: doc.venues, group: { ...doc.group, venues: doc.venues.filter((v) => !v.error).length } }, title }];
    }
    return doc.venues.map((v) => ({
      key: v.venue_id || v.venue,
      data: v.error || !v.week || !v.mtd ? { ...base, venues: [v], group: null } : { ...base, venues: [v], group: { week: v.week, mtd: v.mtd, venues: 1 } },
      title: v.venue,
    }));
  }, [doc]);

  const printNow = () => {
    const sheet = sheetRef.current;
    if (!sheet) return;
    frameRef.current?.remove();
    const frame = document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.title = 'Weekly summary';
    frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden';
    frame.onload = () => {
      frame.contentWindow?.focus();
      frame.contentWindow?.print();
    };
    const heading = `<h1 style="font-size:16pt;margin:0 0 4px">Weekly summary</h1><div style="color:var(--text-soft);margin-bottom:12px">${doc?.week?.label || weekEnding}</div>`;
    frame.srcdoc = printDocument(`${heading}${sheet.innerHTML}`, `Weekly summary — ${doc?.week?.label || weekEnding}`);
    document.body.appendChild(frame);
    frameRef.current = frame;
  };

  const csv = () => {
    if (!doc) return;
    downloadText(csvFilename(`weekly summary ${weekEnding}`), summaryCsv(doc.venues, doc.group, weekLabel, mtdLabel));
  };

  if (!meta) return <NotReady title="Weekly summary" page={page} error={metaError} status={status} reload={reload} />;

  const toggle = (key: string) => setOpen((o) => ({ ...o, [key]: !(o[key] ?? false) }));
  const venues = venueOptions(meta);
  const actions = (
    <>
      {venues.length > 1 && <VenueSelect venues={venues} value={venue} onChange={setVenue} allowAll testId="fin-summary-venue" />}
      <Button size="sm" variant="quiet" onClick={() => setWeekEnding(shiftWeek(weekEnding, -1))} aria-label="Previous week">‹</Button>
      <input type="date" className="n-input" value={weekEnding} onChange={(e) => { if (e.target.value) setWeekEnding(e.target.value); }} aria-label="Week ending" style={{ width: 160 }} />
      <Button size="sm" variant="quiet" onClick={() => setWeekEnding(shiftWeek(weekEnding, 1))} aria-label="Next week">›</Button>
      <Button size="sm" variant="quiet" icon={RefreshCw} onClick={() => void load(weekEnding, venue)} aria-label="Refresh" />
      <Button size="sm" icon={Download} onClick={csv} disabled={!doc}>CSV</Button>
      <Button size="sm" icon={Printer} onClick={printNow} disabled={!doc}>Print</Button>
    </>
  );

  return (
    <PageShell isPage={page} title="Weekly summary" meta={doc?.week?.label} status={<ModeBadge mode={meta.finance_mode} />} actions={actions} testId="fin-summary">
      <FinanceModeBanner mode={meta.finance_mode} />
      <div ref={sheetRef}>
        {loading && !doc ? (
          <PageState kind="loading" title="Loading the weekly summary…" />
        ) : error ? (
          <PageState kind="error" title="Could not load the weekly summary." detail={error} action={<Button size="sm" onClick={() => void load(weekEnding, venue)}>Try again</Button>} />
        ) : !doc || doc.venues.length === 0 ? (
          <PageState kind="empty" title="No venues to summarise." detail="Ask an owner for access to a venue." />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, opacity: loading ? 0.6 : 1 }}>
            {blocks.map((b) => (
              <FinanceWeekly key={b.key} data={b.data as unknown as Record<string, unknown>} props={{ title: b.title }} />
            ))}
            {doc.venues.map((v) => {
              const rows = venuePnl(v);
              if (!rows) return null;
              const key = `pnl:${v.venue_id || v.venue}`;
              const plan = (v.week?.budget_plan as unknown as BudgetPlan | null) ?? null;
              const metaText = plan?.version ? `budget: ${plan.version.label || 'locked version'}` : 'no locked budget for this year';
              return (
                <Fold key={key} title={`${v.venue} — actual vs budget`} meta={metaText} open={open[key] ?? doc.venues.length === 1} onToggle={() => toggle(key)}>
                  <PnlTable rows={rows} money={money} title={`${v.venue} actual vs budget`} />
                  {plan?.notes && plan.notes.length > 0 && (
                    <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                      {plan.notes.map((n) => <li key={n}>{n}</li>)}
                    </ul>
                  )}
                </Fold>
              );
            })}
          </div>
        )}
      </div>
    </PageShell>
  );
}
