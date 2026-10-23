'use client';

/**
 * Forecasting — the day drawer: why the forecast for one day is what it is
 * (Orbit's DayForecastV2 layout kept, 05 §7 item 5; its maths not). The
 * eight same-weekday samples with the reason each was used, deflated or
 * left out; last year's rows as a reference, never blended; the uplifts;
 * the override box (who, when, why — it survives the nightly run); the
 * templated "why"; driver entry; and "Set Loaded budget" for the day when
 * four weeks of scores exist.
 */
import { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import { Field, FormGrid, Note } from '../shared/PageShell';
import { formatCents, formatSignedCents } from '../shared/money';
import { toast } from '../shared/Toasts';
import { errorText, finGet, finSend } from '../shared/useFinanceApi';
import PushDialog, { type PushRequest } from './PushDialog';
import { Small, totalRow } from './bits';
import { centsInput, dayCell, longDate, parseAmount, pct } from './logic';
import type { DayDoc, Driver, FinMeta, Sample } from './types';

const REASON: Record<string, string> = {
  ok: 'used',
  deflated: 'functions removed',
  closed: 'closed — left out',
  holiday: 'public holiday — left out',
  driver: 'a driver that day — left out',
  no_data: 'no sales read — left out',
};

const DRIVER_KIND_LABEL: Record<Driver['kind'], string> = { holiday: 'Holiday', event: 'Event', function: 'Function', manual: 'Driver' };

function Samples({ samples, currency }: { samples: Sample[]; currency: string }) {
  if (samples.length === 0) return <Small style={{ marginTop: 0 }}>No history yet for this weekday.</Small>;
  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="n-table" style={{ width: '100%' }} aria-label="The days behind the baseline">
        <thead>
          <tr>
            <th>Day</th>
            <th className="num">Actual</th>
            <th className="num">Used</th>
            <th>Why</th>
          </tr>
        </thead>
        <tbody>
          {samples.map((s) => {
            const out = s.used_cents === null;
            return (
              <tr key={s.date} style={out ? { color: 'var(--muted)' } : undefined}>
                <td style={{ whiteSpace: 'nowrap', textDecoration: out ? 'line-through' : undefined }}>{longDate(s.date)}</td>
                <td className="num">{formatCents(s.actual_cents, currency, 0)}</td>
                <td className="num" style={{ fontWeight: out ? 400 : 500 }}>{out ? '—' : formatCents(s.used_cents, currency, 0)}{s.event_spend_cents ? <Small style={{ marginTop: 0 }}>less {formatCents(s.event_spend_cents, currency, 0)} of functions</Small> : null}</td>
                <td style={{ fontSize: 'var(--fs-sm)' }}>{REASON[s.reason] ?? s.reason}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function DayDrawer({ meta, venueId, date, canWrite, pushReady, vintageId, onClose, onChanged }: {
  meta: FinMeta;
  venueId: string;
  date: string;
  canWrite: boolean;
  /** Four weeks of scores exist: "Set Loaded budget" may show. */
  pushReady: boolean;
  vintageId: string | null;
  onClose: () => void;
  /** An override or a driver changed: the grid reloads. */
  onChanged: () => void;
}) {
  const [doc, setDoc] = useState<DayDoc | null>(null);
  // The driver ROWS (with ids, for removing one): the day document carries
  // only the chips as the vintage saw them, and a driver added today shows
  // up in the vintage only after tonight's run.
  const [rows, setRows] = useState<Driver[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [overrideText, setOverrideText] = useState('');
  const [overrideReason, setOverrideReason] = useState('');
  const [driverKind, setDriverKind] = useState<Driver['kind']>('manual');
  const [driverLabel, setDriverLabel] = useState('');
  const [driverAmount, setDriverAmount] = useState('');
  const [driverMode, setDriverMode] = useState<'cents' | 'pct'>('cents');
  const [busy, setBusy] = useState<string | null>(null);
  const [push, setPush] = useState<PushRequest | null>(null);
  const currency = meta.currency;
  const live = meta.finance_mode === 'live';

  useEffect(() => {
    let alive = true;
    finGet<DayDoc>('/api/finance/forecast/day', { venue_id: venueId, date })
      .then((d) => {
        if (!alive) return;
        setDoc(d);
        setOverrideText(centsInput(d.override?.amount_cents ?? null));
        setOverrideReason(d.override?.reason ?? '');
      })
      .catch((e: unknown) => { if (alive) setError(errorText(e)); });
    finGet<Driver[]>('/api/finance/forecast/drivers', { venue_id: venueId, start: date, end: date })
      .then((r) => { if (alive) setRows(r); })
      .catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, [venueId, date, tick]);

  const run = async (key: string, fn: () => Promise<void>, done: string) => {
    setBusy(key);
    try {
      await fn();
      toast(done, 'ok');
      setTick((n) => n + 1);
      onChanged();
    } catch (e) {
      toast(errorText(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  const saveOverride = () => {
    const amount = parseAmount(overrideText);
    if (amount === null) { toast('Type the amount to use for the day.', 'warn'); return; }
    void run('override', () => finSend('PUT', '/api/finance/forecast/override', { venue_id: venueId, date, amount_cents: amount, reason: overrideReason.trim() || null }), 'Override saved — it replaces the forecast until cleared.');
  };
  const clearOverride = () => run('override', () => finSend('DELETE', `/api/finance/forecast/override?venue_id=${encodeURIComponent(venueId)}&date=${date}`), 'Override cleared.');
  const addDriver = () => {
    const label = driverLabel.trim();
    if (!label) { toast('Give the driver a label.', 'warn'); return; }
    const body: Record<string, unknown> = { venue_id: venueId, date, kind: driverKind, label };
    if (driverMode === 'cents') {
      const c = parseAmount(driverAmount);
      if (c === null) { toast('Type the uplift in dollars.', 'warn'); return; }
      body.uplift_cents = c;
    } else {
      const n = Number(driverAmount.replace(/[^0-9.\-]/g, ''));
      if (!Number.isFinite(n)) { toast('Type the uplift as a percentage.', 'warn'); return; }
      body.uplift_bp = Math.round(n * 100);
    }
    void run('driver', async () => { await finSend('POST', '/api/finance/forecast/drivers', body); setDriverLabel(''); setDriverAmount(''); }, 'Driver added — tonight’s run will use it.');
  };
  const removeDriver = (d: Driver) => run(`driver:${d.id}`, () => finSend('DELETE', `/api/finance/forecast/drivers/${encodeURIComponent(d.id)}`), 'Driver removed.');

  const final = doc ? (doc.override?.amount_cents ?? doc.forecast_cents) : null;
  const c = doc?.components;

  if (push) {
    return <PushDialog title={`Set Loaded budget — ${longDate(date)}`} intro="The forecast (or your override) for the day, sent to Loaded. A card every time: the forecast never writes Loaded on its own." request={push} onDone={() => { onChanged(); setTick((n) => n + 1); }} onClose={() => setPush(null)} />;
  }

  return (
    <Dialog title={longDate(date)} width={680} onClose={onClose} closeButton>
      {error ? (
        <PageState kind="error" title="Couldn’t open the day." detail={error} />
      ) : !doc ? (
        <PageState kind="loading" title="Loading…" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxHeight: '70vh', overflowY: 'auto', paddingRight: 2 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10 }}>
            {[
              ['Forecast', formatCents(doc.forecast_cents, currency, 0)],
              ['Override', doc.override ? formatCents(doc.override.amount_cents, currency, 0) : '—'],
              ['Loaded budget', formatCents(doc.loaded_cents, currency, 0)],
              ['Locked budget', formatCents(doc.locked_cents, currency, 0)],
              ['Actual', formatCents(doc.actual_cents, currency, 0)],
            ].map(([label, value]) => (
              <div key={label} style={{ padding: '8px 10px', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
                <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{label}</div>
                <div style={{ fontSize: 'var(--fs-lg)', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
              </div>
            ))}
          </div>
          {doc.holiday && <Note tone="warn">Public holiday: {doc.holiday}. Holiday days take last year’s same holiday where the venue traded, else the baseline — always worth a look.</Note>}
          {doc.why && <Note>{doc.why}</Note>}

          {c && (
            <section>
              <h3 style={{ margin: '0 0 6px', fontSize: 'var(--fs-base)' }}>Baseline — median of {c.median_of} {dayCell(date).slice(0, 3)}s</h3>
              <Samples samples={c.samples} currency={currency} />
              <div style={{ marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                Baseline {formatCents(doc.baseline_cents, currency, 0)}
                {c.level_correction ? ` · level correction ×${c.level_correction.factor.toFixed(3)} (last 4 weeks ÷ previous 4 = ${c.level_correction.ratio.toFixed(2)}, halved and clipped)` : ''}
              </div>
              {c.flags.length > 0 && <Small tone="warn">{c.flags.join(' · ')}</Small>}
            </section>
          )}

          {c && c.uplifts.length > 0 && (
            <section>
              <h3 style={{ margin: '0 0 6px', fontSize: 'var(--fs-base)' }}>Uplifts</h3>
              <table className="n-table" style={{ width: '100%' }} aria-label="Uplifts on the day">
                <tbody>
                  {c.uplifts.map((u, i) => (
                    <tr key={`${i}:${u.kind}:${u.label}`}>
                      <td>{DRIVER_KIND_LABEL[u.kind as Driver['kind']] ?? u.kind}: {u.label}</td>
                      <td className="num">{formatSignedCents(u.cents, currency, 0)}</td>
                    </tr>
                  ))}
                  <tr style={totalRow}>
                    <td>Forecast</td>
                    <td className="num">{formatCents(doc.forecast_cents, currency, 0)}</td>
                  </tr>
                </tbody>
              </table>
            </section>
          )}

          {rows.filter((d) => !d.archived_at).length > 0 && (
            <section>
              <h3 style={{ margin: '0 0 6px', fontSize: 'var(--fs-base)' }}>Drivers on this day</h3>
              <table className="n-table" style={{ width: '100%' }} aria-label="Drivers on this day">
                <tbody>
                  {rows.filter((d) => !d.archived_at).map((d) => (
                    <tr key={d.id}>
                      <td>{DRIVER_KIND_LABEL[d.kind] ?? d.kind}: {d.label}</td>
                      <td className="num">{d.uplift_cents !== null && d.uplift_cents !== undefined ? formatSignedCents(d.uplift_cents, currency, 0) : pct((d.uplift_bp ?? 0) / 10000, 1, true)}</td>
                      <td className="num" style={{ width: 40 }}>{canWrite && <Button size="xs" variant="quiet" icon={Trash2} aria-label={`Remove driver ${d.label}`} onClick={() => void removeDriver(d)} disabled={busy === `driver:${d.id}`} />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Small style={{ marginTop: 4 }}>A driver added today is used from tonight’s run (or press Run now).</Small>
            </section>
          )}

          {c?.last_year && (c.last_year.same_weekday || c.last_year.same_date) && (
            <section>
              <h3 style={{ margin: '0 0 6px', fontSize: 'var(--fs-base)' }}>Last year (for reference — never blended in)</h3>
              <table className="n-table" style={{ width: '100%' }} aria-label="Last year">
                <tbody>
                  {c.last_year.same_weekday && <tr><td>Same weekday · {longDate(c.last_year.same_weekday.date)}</td><td className="num">{formatCents(c.last_year.same_weekday.actual_cents, currency, 0)}</td></tr>}
                  {c.last_year.same_date && c.last_year.same_date.date !== c.last_year.same_weekday?.date && <tr><td>Same date · {longDate(c.last_year.same_date.date)}</td><td className="num">{formatCents(c.last_year.same_date.actual_cents, currency, 0)}</td></tr>}
                </tbody>
              </table>
            </section>
          )}

          {canWrite && (
            <section>
              <h3 style={{ margin: '0 0 6px', fontSize: 'var(--fs-base)' }}>Override</h3>
              <FormGrid min={160}>
                <Field label="Use this amount instead" id="fin-override-amount" hint="Replaces the forecast; kept with your name and the date; survives the nightly run.">
                  <input id="fin-override-amount" className="n-input" inputMode="decimal" value={overrideText} onChange={(e) => setOverrideText(e.target.value)} style={{ width: '100%' }} />
                </Field>
                <Field label="Why" id="fin-override-reason">
                  <input id="fin-override-reason" className="n-input" value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)} placeholder="Concert at Eden Park" style={{ width: '100%' }} />
                </Field>
              </FormGrid>
              <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <Button size="sm" variant="primary" onClick={saveOverride} disabled={busy === 'override'}>Save override</Button>
                {doc.override && <Button size="sm" variant="quiet" onClick={() => void clearOverride()} disabled={busy === 'override'}>Clear</Button>}
                {doc.override && <Small style={{ marginTop: 0 }}>Set {doc.override.created_at.slice(0, 10)}{doc.override.reason ? ` — ${doc.override.reason}` : ''}</Small>}
              </div>
            </section>
          )}

          {canWrite && (
            <section>
              <h3 style={{ margin: '0 0 6px', fontSize: 'var(--fs-base)' }}>Add a driver</h3>
              <FormGrid min={130}>
                <Field label="Kind" id="fin-driver-kind">
                  <select id="fin-driver-kind" className="n-select" value={driverKind} onChange={(e) => setDriverKind(e.target.value as Driver['kind'])} style={{ width: '100%' }}>
                    <option value="manual">Driver</option>
                    <option value="event">Event</option>
                    <option value="holiday">Holiday</option>
                  </select>
                </Field>
                <Field label="Label" id="fin-driver-label">
                  <input id="fin-driver-label" className="n-input" value={driverLabel} onChange={(e) => setDriverLabel(e.target.value)} placeholder="Round the Bays" style={{ width: '100%' }} />
                </Field>
                <Field label={driverMode === 'cents' ? 'Uplift ($)' : 'Uplift (%)'} id="fin-driver-amount">
                  <div style={{ display: 'flex', gap: 4 }}>
                    <input id="fin-driver-amount" className="n-input" inputMode="decimal" value={driverAmount} onChange={(e) => setDriverAmount(e.target.value)} style={{ flex: 1, minWidth: 0 }} />
                    <select aria-label="Uplift as" className="n-select" value={driverMode} onChange={(e) => setDriverMode(e.target.value as 'cents' | 'pct')}>
                      <option value="cents">$</option>
                      <option value="pct">%</option>
                    </select>
                  </div>
                </Field>
              </FormGrid>
              <div style={{ marginTop: 8 }}>
                <Button size="sm" onClick={addDriver} disabled={busy === 'driver'}>Add driver</Button>
              </div>
            </section>
          )}

          {canWrite && pushReady && (
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, paddingTop: 8, borderTop: '1px solid var(--line-soft)' }}>
              <Button
                variant="primary"
                disabled={final === null || !live}
                title={!live ? 'Loaded is written only once Norm Finance is live.' : undefined}
                onClick={() => final !== null && setPush({ venue_id: venueId, kind: 'forecast', days: [{ date, amount_cents: final }], vintage_id: vintageId, allow_zero: final === 0 })}
              >
                Set Loaded budget to {formatCents(final, currency, 0)}
              </Button>
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}
