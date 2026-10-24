'use client';

/**
 * The Budget app — the Forecast tab of the Budgets workspace
 * (FinanceBudgets.tsx). It draws no page header of its own: the workspace
 * holds the venue, the banner and the one toast host. Its month runs over
 * sixteen rolling months (twelve back, three ahead), independent of the
 * workspace's fiscal year, so the header hides that control on this tab.
 *
 * Two screens replace Orbit's eleven (plan §8): this month grid per venue —
 * Day · Actual · Forecast with driver chips · Loaded budget · Locked budget
 * · Override · Δ (final − Loaded), week and month totals — and the day
 * drawer. Above the grid, the previous month's accuracy: median error,
 * bias and the month-total error of the forecast made 28 days ahead, each
 * beside the Loaded budget as it stood 28 days before (the human benchmark
 * on the same strip, 05 §8.4). Plain tiles and a table, not a chart: three
 * numbers and their benchmark are read, not plotted.
 *
 * "Set Loaded budget" (a day in the drawer, a week, the month) sends the
 * override if any, else the forecast, through the card every time
 * (`loadedhub.set_budgets`, `source=forecast`, D6) — and only once the API
 * says four weeks of nightly scores exist for the venue. The level
 * correction is a per-venue switch, off by default, scored separately.
 * Public holidays are Loaded's: the Public Holiday pay adjustments in the
 * packages the venue's company activated, refreshed nightly; the settings
 * dialog's "Sync from Loaded" fills them now.
 */
import { useMemo, useState } from 'react';
import { Play, RefreshCw, Send, Settings } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { StatTile, TileRow } from '../../functions/enquiries/bits';
import { Section, Stack } from '../shared/PageShell';
import { csvFilename, downloadText, toCsv } from '../shared/csv';
import { formatCents } from '../shared/money';
import { toast } from '../shared/Toasts';
import { errorText, finSend, useFinQuery } from '../shared/useFinanceApi';
import DayDrawer from './DayDrawer';
import PushDialog, { type PushRequest } from './PushDialog';
import { Chip, MonthNav, Small, Variance, subtotalRow, totalRow } from './bits';
import { addMonths, dayCell, groupIntoWeeks, holidaySyncText, monthLabel, pct, sumCents, yearMonthOf, type YearMonth } from './logic';
import type { FinMeta, ForecastDay, ForecastDoc, HolidaySync, ScoreBlock, ScoresDoc, VenueSettings } from './types';
import { holds } from './useFinanceMeta';

/** A score as "29.0%", or an em dash. The API's percentages are already ×100. */
function score(v: number | null | undefined, dp = 1): string {
  return v === null || v === undefined || !Number.isFinite(v) ? '—' : `${v.toFixed(dp)}%`;
}

function signedScore(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)}%`;
}

function AccuracyStrip({ scores, month, weeksOfScores }: { scores: ScoresDoc | null; month: YearMonth; weeksOfScores: number }) {
  const [all, setAll] = useState(false);
  const h = scores?.horizons ?? {};
  const main: ScoreBlock | undefined = h['28'] ?? h['7'] ?? h['1'];
  const mainLabel = h['28'] ? '28 days ahead' : h['7'] ? '7 days ahead' : '1 day ahead';
  const bench = scores?.benchmarks?.loaded_budget_28;
  const label = scores ? monthLabel({ year: scores.year, month: scores.month }) : monthLabel(addMonths(month, -1));
  if (!scores || !main || !main.n) {
    return (
      <Section title="How the forecast is doing" meta={label}>
        <PageState kind="empty" title="No scores yet." detail={`Each night Norm scores the forecasts made 1, 7 and 28 days earlier against what the day took, and the Loaded budget as it stood 28 days before. ${weeksOfScores} week${weeksOfScores === 1 ? '' : 's'} of scores so far; "Set Loaded budget" appears after four.`} />
      </Section>
    );
  }
  const rows: { key: string; label: string; s: ScoreBlock | undefined }[] = [
    { key: 'h1', label: 'Forecast, 1 day ahead', s: h['1'] },
    { key: 'h7', label: 'Forecast, 7 days ahead', s: h['7'] },
    { key: 'h28', label: 'Forecast, 28 days ahead', s: h['28'] },
    { key: 'loaded', label: 'Loaded budget, as it stood 28 days before', s: bench },
    { key: 'locked', label: 'Locked budget', s: scores.benchmarks?.locked_budget },
  ];
  return (
    <Section title="How the forecast is doing" meta={`${label} · ${main.n} days scored · forecast ${mainLabel}, beside the Loaded budget 28 days before`} actions={<Button size="xs" variant="quiet" onClick={() => setAll((v) => !v)} aria-expanded={all}>{all ? 'Fewer' : 'All horizons'}</Button>}>
      <TileRow min={170}>
        <StatTile label="Median daily error" value={score(main.median_ape)} sub={`Loaded budget ${score(bench?.median_ape)} · target ≤ 25%`} />
        <StatTile label="Bias" value={signedScore(main.bias_pct)} sub={`Loaded budget ${signedScore(bench?.bias_pct)} · + means over-forecast`} />
        <StatTile label="Month total error" value={score(main.monthly_sum_error_pct)} sub={`Loaded budget ${score(bench?.monthly_sum_error_pct)} · target ≤ 10%`} />
        <StatTile label="Days within ±20%" value={pct(main.within_20, 0)} sub={`Loaded budget ${pct(bench?.within_20, 0)} · within ±10%: ${pct(main.within_10, 0)}`} />
      </TileRow>
      {all && (
        <div style={{ overflowX: 'auto', marginTop: 10 }}>
          <table className="n-table" style={{ width: '100%' }} aria-label="Accuracy by horizon">
            <thead>
              <tr>
                <th>Series</th>
                <th className="num">Days</th>
                <th className="num">Median error</th>
                <th className="num">Mean error</th>
                <th className="num">Bias</th>
                <th className="num">Within ±10%</th>
                <th className="num">Within ±20%</th>
                <th className="num">Month total</th>
              </tr>
            </thead>
            <tbody>
              {rows.filter((r) => r.s).map((r) => (
                <tr key={r.key}>
                  <td>{r.label}</td>
                  <td className="num">{r.s!.n}</td>
                  <td className="num">{score(r.s!.median_ape)}</td>
                  <td className="num">{score(r.s!.mape)}</td>
                  <td className="num">{signedScore(r.s!.bias_pct)}</td>
                  <td className="num">{pct(r.s!.within_10, 0)}</td>
                  <td className="num">{pct(r.s!.within_20, 0)}</td>
                  <td className="num">{score(r.s!.monthly_sum_error_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function SettingsDialog({ meta, venueId, onClose, onChanged }: { meta: FinMeta; venueId: string; onClose: () => void; onChanged: () => void }) {
  const q = useFinQuery<VenueSettings>('/api/finance/settings', { venue_id: venueId });
  const [busy, setBusy] = useState<string | null>(null);
  const canSync = holds(meta, 'budgets:write');
  const venue = meta.venues.find((v) => v.id === venueId);
  const s = q.data;

  const save = async (patch: Partial<VenueSettings>) => {
    if (!s) return;
    setBusy('settings');
    try {
      q.setData(await finSend<VenueSettings>('PUT', '/api/finance/settings', { venue_id: venueId, version: s.version, ...patch }));
      toast('Saved.', 'ok');
      onChanged();
    } catch (e) {
      toast(errorText(e), 'error');
      q.retry();
    } finally {
      setBusy(null);
    }
  };

  const sync = async () => {
    setBusy('sync');
    try {
      const r = await finSend<HolidaySync>('POST', '/api/finance/holidays/sync', { venue_id: venueId });
      toast(holidaySyncText(r), 'ok');
      onChanged();
    } catch (e) {
      toast(errorText(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Dialog title={`Forecast settings — ${venue?.name ?? ''}`} width={480} onClose={onClose} closeButton>
      {q.error ? (
        <PageState kind="error" title="Couldn’t load the settings." detail={q.error} />
      ) : !s ? (
        <PageState kind="loading" title="Loading…" />
      ) : (
        <Stack gap={14}>
          <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
            <input type="checkbox" checked={s.level_correction_on} disabled={busy !== null} onChange={(e) => void save({ level_correction_on: e.target.checked })} style={{ marginTop: 3, accentColor: 'var(--accent)' }} />
            <span style={{ fontSize: 'var(--fs-sm)' }}>
              <strong>Level correction</strong>
              <div style={{ color: 'var(--text-soft)' }}>Nudges the baseline by how the last four weeks compare with the four before (halved, clipped to ±15%). Off by default — on the evidence it does not help overall; it is scored separately so you can see.</div>
            </span>
          </label>
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            Closed on: <strong style={{ color: 'var(--text)' }}>{s.closed_weekdays.length ? s.closed_weekdays.map((d) => ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][d - 1]).join(', ') : 'no fixed days'}</strong>
            <div style={{ marginTop: 2 }}>A weekday with no trade in most of the last eight weeks is treated as closed too.</div>
          </div>
          <div style={{ borderTop: '1px solid var(--line-soft)', paddingTop: 12, display: 'flex', gap: 12, alignItems: 'flex-start', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
              <strong style={{ color: 'var(--text)' }}>Public holidays</strong>
              <div>From Loaded&rsquo;s pay adjustments: every pay adjustment of type Public Holiday in the packages {venue?.name ?? 'this venue'}&rsquo;s company has activated in Loaded, regional anniversary days included. Refreshed every night.</div>
            </span>
            {canSync && <Button size="sm" onClick={() => void sync()} disabled={busy !== null} style={{ flexShrink: 0 }}>{busy === 'sync' ? 'Reading Loaded…' : 'Sync from Loaded'}</Button>}
          </div>
        </Stack>
      )}
    </Dialog>
  );
}

/** A week or month line of the grid. */
function SumRow({ label, rows, style, action, isMobile, currency, showAction }: { label: string; rows: ForecastDay[]; style: React.CSSProperties; action?: React.ReactNode; isMobile: boolean; currency: string; showAction: boolean }) {
  const actual = sumCents(rows, (d) => d.actual_cents);
  const final = sumCents(rows, (d) => d.final_cents);
  const loaded = sumCents(rows, (d) => d.loaded_cents);
  const locked = sumCents(rows, (d) => d.locked_cents);
  return (
    <tr style={style}>
      <td>{label}</td>
      <td className="num">{formatCents(actual, currency, 0)}</td>
      <td className="num">{formatCents(final, currency, 0)}</td>
      <td className="num">{formatCents(loaded, currency, 0)}</td>
      {!isMobile && <td className="num">{formatCents(locked, currency, 0)}</td>}
      {!isMobile && <td />}
      <td className="num"><Variance cents={final === null || loaded === null ? null : final - loaded} base={loaded} currency={currency} /></td>
      {showAction && <td className="num">{action}</td>}
    </tr>
  );
}

export default function ForecastTab({ meta, venueId, canWrite, reload }: { meta: FinMeta; venueId: string; canWrite: boolean; reload: () => void }) {
  const [month, setMonth] = useState<YearMonth | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [day, setDay] = useState<string | null>(null);
  const [push, setPush] = useState<{ title: string; request: PushRequest } | null>(null);
  const [settings, setSettings] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const { isMobile } = useBreakpoint();
  const current = month ?? yearMonthOf(meta.today);
  const months = useMemo(() => {
    const now = yearMonthOf(meta.today);
    return Array.from({ length: 16 }, (_, i) => addMonths(now, i - 12));
  }, [meta.today]);
  const q = useFinQuery<ForecastDoc>('/api/finance/forecast', { venue_id: venueId, year: current.year, month: current.month }, refresh);

  const live = meta.finance_mode === 'live';
  const currency = meta.currency;
  const doc = q.data;
  const venue = meta.venues.find((v) => v.id === venueId);
  const pushReady = doc?.forecast_push_ready ?? venue?.forecast_push_ready ?? false;
  const weeksOfScores = doc?.weeks_of_scores ?? venue?.weeks_of_scores ?? 0;
  const days = doc?.days ?? [];
  const weeks = groupIntoWeeks(days);

  const pushFor = (rows: ForecastDay[], title: string) => {
    const list = rows.filter((d) => d.final_cents !== null && d.actual_cents === null).map((d) => ({ date: d.date, amount_cents: d.final_cents as number }));
    if (list.length === 0) { toast('Nothing to send: every day here has already traded, or has no forecast.', 'warn'); return; }
    setPush({ title, request: { venue_id: venueId, kind: 'forecast', days: list, vintage_id: doc?.vintage?.id ?? null } });
  };

  const runNow = async () => {
    setBusy('run');
    try {
      const r = await finSend<{ days: number }>('POST', '/api/finance/forecast/run', { venue_id: venueId });
      toast(`Forecast rebuilt — ${r.days} days.`, 'ok');
      setRefresh((n) => n + 1);
    } catch (e) {
      toast(errorText(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  const csv = () => downloadText(
    csvFilename(`forecast ${venue?.name ?? ''} ${monthLabel(current)}`),
    toCsv(['Date', 'Day', 'Actual', 'Forecast', 'Drivers', 'Loaded budget', 'Locked budget', 'Override', 'Final', 'Final − Loaded', 'Holiday', 'Flags'], days.map((d) => [d.date, dayCell(d.date, d.weekday), d.actual_cents === null ? null : d.actual_cents / 100, d.forecast_cents === null ? null : d.forecast_cents / 100, d.drivers.map((c) => c.label).join('; '), d.loaded_cents === null ? null : d.loaded_cents / 100, d.locked_cents === null ? null : d.locked_cents / 100, d.override_cents === null ? null : d.override_cents / 100, d.final_cents === null ? null : d.final_cents / 100, d.delta_cents === null ? null : d.delta_cents / 100, d.holiday, d.flags.join('; ')])),
  );

  const gridMeta = [
    doc?.vintage ? `forecast as of ${doc.vintage.as_of} · ${doc.vintage.method}` : null,
    canWrite && !pushReady ? `"Set Loaded budget" appears after four weeks of scores (${weeksOfScores} so far)` : null,
  ].filter(Boolean).join(' · ') || undefined;

  return (
    <div data-testid="fin-forecasting" style={{ minWidth: 0 }}>
      <Stack>
        <div role="toolbar" aria-label="Forecast" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <MonthNav months={months} value={current} onChange={setMonth} />
          <Button size="sm" variant="quiet" icon={RefreshCw} aria-label="Refresh" onClick={() => setRefresh((n) => n + 1)} />
          <Button size="sm" variant="quiet" icon={Settings} onClick={() => setSettings(true)}>Forecast settings</Button>
          {canWrite && <Button size="sm" icon={Play} onClick={() => void runNow()} disabled={busy === 'run'} title="Rebuild tonight's forecast now from the cached sales">{busy === 'run' ? 'Running…' : 'Run now'}</Button>}
          {canWrite && pushReady && (
            <Button size="sm" variant="primary" icon={Send} disabled={!live || days.length === 0} title={!live ? 'Loaded is written only once Budget is live.' : undefined} onClick={() => pushFor(days, `Set Loaded budget — ${monthLabel(current)}`)}>
              Set Loaded budget for the month
            </Button>
          )}
        </div>
        <AccuracyStrip scores={doc?.accuracy ?? null} month={current} weeksOfScores={weeksOfScores} />
        <Section
          title={monthLabel(current)}
          meta={gridMeta}
          padded={false}
          actions={<Button size="xs" variant="quiet" onClick={csv} disabled={days.length === 0}>CSV</Button>}
        >
          {q.error ? (
            <div style={{ padding: 12 }}><PageState kind="error" title="Couldn’t load the forecast." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} /></div>
          ) : !doc ? (
            <PageState kind="loading" title="Loading…" />
          ) : days.length === 0 ? (
            <PageState kind="empty" title="No forecast for this month yet." detail={doc.vintage ? 'The vintage does not cover it.' : canWrite ? 'Press Run now to build one from the cached sales; the nightly job keeps it fresh.' : 'The nightly job builds one once sales have been cached.'} />
          ) : (
            <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch', opacity: q.loading ? 0.6 : 1 }}>
              <table className="n-table" style={{ width: '100%', minWidth: isMobile ? undefined : 760 }} aria-label={`Forecast for ${monthLabel(current)}`}>
                <thead>
                  <tr>
                    <th>Day</th>
                    <th className="num">Actual</th>
                    <th className="num">Forecast</th>
                    <th className="num">Loaded</th>
                    {!isMobile && <th className="num">Locked</th>}
                    {!isMobile && <th className="num">Override</th>}
                    <th className="num">Δ vs Loaded</th>
                    {canWrite && pushReady && <th />}
                  </tr>
                </thead>
                <tbody>
                  {weeks.map((w) => (
                    <WeekBlock key={w.week_start} week={w} isMobile={isMobile} currency={currency} canWrite={canWrite} pushReady={pushReady} live={live} onOpen={setDay} onPushWeek={() => pushFor(w.rows, `Set Loaded budget — week of ${dayCell(w.week_start)}`)} />
                  ))}
                  <SumRow label="Month" rows={days} style={totalRow} isMobile={isMobile} currency={currency} showAction={canWrite && pushReady} />
                </tbody>
              </table>
              <div style={{ padding: '8px 12px 12px' }}>
                <Small style={{ marginTop: 0 }}>Forecast = the override where one is set, else the median of the last eight same weekdays (closed days, holidays and driver days left out, function trade removed) plus the day’s uplifts. Open a day for the working. Δ is the forecast against Loaded’s current budget.</Small>
                {doc.warnings.map((w) => <Small key={w} tone="warn">{w}</Small>)}
              </div>
            </div>
          )}
        </Section>
      </Stack>
      {day && (
        <DayDrawer meta={meta} venueId={venueId} date={day} canWrite={canWrite} pushReady={pushReady} vintageId={doc?.vintage?.id ?? null} onClose={() => setDay(null)} onChanged={() => setRefresh((n) => n + 1)} />
      )}
      {push && <PushDialog title={push.title} intro="The forecast (or an override) for each day still to trade, sent to Loaded. A card every time — the forecast never writes Loaded on its own." request={push.request} onDone={() => setRefresh((n) => n + 1)} onClose={() => setPush(null)} />}
      {settings && <SettingsDialog meta={meta} venueId={venueId} onClose={() => setSettings(false)} onChanged={() => { setRefresh((n) => n + 1); reload(); }} />}
    </div>
  );
}

function WeekBlock({ week, isMobile, currency, canWrite, pushReady, live, onOpen, onPushWeek }: {
  week: { week_start: string; rows: ForecastDay[] };
  isMobile: boolean;
  currency: string;
  canWrite: boolean;
  pushReady: boolean;
  live: boolean;
  onOpen: (date: string) => void;
  onPushWeek: () => void;
}) {
  return (
    <>
      {week.rows.map((d) => {
        const past = d.actual_cents !== null;
        return (
          <tr key={d.date} onClick={() => onOpen(d.date)} style={{ cursor: 'pointer', background: d.weekday >= 6 ? 'var(--surface)' : undefined }}>
            <td style={{ whiteSpace: 'nowrap' }}>
              <Button variant="link" size="sm" onClick={(e) => { e.stopPropagation(); onOpen(d.date); }} aria-label={`Open ${d.date}`}>{dayCell(d.date, d.weekday)}</Button>
              {d.holiday && <Badge tone="neutral" title="Public holiday">{d.holiday}</Badge>}
              {d.drivers.length > 0 && (
                <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap', marginLeft: 6 }}>
                  {d.drivers.map((c, i) => <Chip key={i} chip={c} />)}
                </span>
              )}
              {d.flags.includes('thin_history') && <Badge tone="warn" title="Fewer than four usable same-weekday samples">thin history</Badge>}
            </td>
            <td className="num" style={{ fontWeight: past ? 500 : 400, color: past ? undefined : 'var(--muted)' }}>{formatCents(d.actual_cents, currency, 0)}</td>
            <td className="num" style={{ fontWeight: past ? 400 : 500, color: past ? 'var(--text-soft)' : undefined }}>
              {formatCents(d.override_cents ?? d.forecast_cents, currency, 0)}
              {d.override_cents !== null && <Badge tone="info" title={`The forecast was ${formatCents(d.forecast_cents, currency, 0)}`}>override</Badge>}
            </td>
            <td className="num">{formatCents(d.loaded_cents, currency, 0)}</td>
            {!isMobile && <td className="num" style={{ color: 'var(--text-soft)' }}>{formatCents(d.locked_cents, currency, 0)}</td>}
            {!isMobile && <td className="num" style={{ color: 'var(--text-soft)' }}>{d.override_cents === null ? '' : formatCents(d.override_cents, currency, 0)}</td>}
            <td className="num"><Variance cents={d.delta_cents} base={d.loaded_cents} currency={currency} /></td>
            {canWrite && pushReady && <td />}
          </tr>
        );
      })}
      <SumRow
        label={`Week of ${dayCell(week.week_start)}`}
        rows={week.rows}
        style={subtotalRow}
        isMobile={isMobile}
        currency={currency}
        showAction={canWrite && pushReady}
        action={canWrite && pushReady ? <Button size="xs" variant="quiet" icon={Send} disabled={!live} onClick={onPushWeek}>Set week</Button> : undefined}
      />
    </>
  );
}

