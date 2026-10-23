'use client';

/**
 * Book a performance, or edit one. Rate × booked clock hours is the agreed
 * amount (shown as it is typed; the API works it out the same way). A
 * weekly repeat picks weekdays and an end (a date, or a number of
 * performances — turned into the last date, which is what the API stores);
 * the dates it will make are previewed. Editing one of a series asks
 * whether the change is for this one, this and the following, or all.
 */
import { useId, useMemo, useState } from 'react';
import { Trash2 } from 'lucide-react';
import Dialog, { ConfirmDialog } from '../../../ui/Dialog';
import Button from '../../../ui/Button';
import { entSend, errorText, isStale } from '../api';
import { dateLabel, money } from '../format';
import { WEEKDAY_SHORT, agreedCents, centsToInput, clockMinutes, endsNextDay as impliesNextDay, endsOnForCount, hoursLabel, isoWeekday, parseMoney, recurrenceDates } from '../logic';
import { DialogActions, Field, FieldGrid, Hint, MoneyInput, Toggle, WriteError, inputStyle } from '../ui';
import type { EntMeta, Performance, Performer, Series } from '../types';

export type SeriesScope = 'this' | 'following' | 'all';

export default function BookingDialog({ meta, performers, editing, defaultVenueId, defaultDate, onSaved, onDeleted, onClose }: {
  meta: EntMeta;
  performers: Performer[];
  editing?: Performance | null;
  defaultVenueId?: string | null;
  defaultDate?: string;
  onSaved: (performance: Performance, series: Series[] | null) => void;
  onDeleted?: (id: string) => void;
  onClose: () => void;
}) {
  const base = useId();
  const id = (k: string) => `${base}-${k}`;
  const [performerId, setPerformerId] = useState(editing?.performer_id ?? '');
  const [venueId, setVenueId] = useState(editing?.venue_id ?? defaultVenueId ?? meta.venues[0]?.id ?? '');
  const [date, setDate] = useState(editing?.performance_date ?? defaultDate ?? '');
  const [start, setStart] = useState(editing?.start_time ?? '20:00');
  const [end, setEnd] = useState(editing?.end_time ?? '23:00');
  const [nextDay, setNextDay] = useState(editing?.ends_next_day ?? false);
  const [nextDayTouched, setNextDayTouched] = useState(!!editing);
  const [rate, setRate] = useState(centsToInput(editing?.rate_cents ?? null));
  const [notes, setNotes] = useState(editing?.notes ?? '');
  const [repeat, setRepeat] = useState(false);
  const [weekdays, setWeekdays] = useState<number[]>(() => (defaultDate ? [isoWeekday(defaultDate)] : []));
  const [endMode, setEndMode] = useState<'until' | 'count'>('until');
  const [until, setUntil] = useState('');
  const [count, setCount] = useState('8');
  const [scope, setScope] = useState<SeriesScope>('this');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; stale: boolean } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const isSeries = !!editing?.series_id;
  const effectiveNextDay = nextDayTouched ? nextDay : impliesNextDay(start, end);
  const rateCents = parseMoney(rate);
  const minutes = clockMinutes(start, end, effectiveNextDay);
  const agreed = rateCents !== null ? agreedCents(rateCents, start, end, effectiveNextDay) : null;

  const preview = useMemo(() => {
    if (!repeat || !date) return [];
    const n = Number(count);
    return recurrenceDates(date, endMode === 'until' ? { weekdays, until: until || null } : { weekdays, count: Number.isFinite(n) ? n : null }, 60);
  }, [repeat, date, weekdays, endMode, until, count]);

  const choices = useMemo(() => {
    const list = performers.filter((p) => p.is_active || p.id === editing?.performer_id);
    return list.slice().sort((a, b) => a.name.localeCompare(b.name));
  }, [performers, editing?.performer_id]);

  const save = async () => {
    if (!performerId) return setError({ text: 'Pick a performer.', stale: false });
    if (!venueId) return setError({ text: 'Pick a venue.', stale: false });
    if (!date) return setError({ text: 'Pick a date.', stale: false });
    if (minutes === null) return setError({ text: 'Enter a start and an end time.', stale: false });
    if (rateCents === null || rateCents <= 0) return setError({ text: 'Enter the hourly rate.', stale: false });
    if (repeat && weekdays.length === 0) return setError({ text: 'Tick at least one weekday to repeat on.', stale: false });
    if (repeat && endMode === 'until' && !until) return setError({ text: 'Pick the date the repeat ends on.', stale: false });
    if (repeat && endMode === 'count' && !(Number(count) >= 1)) return setError({ text: 'How many performances should it make?', stale: false });
    setBusy(true);
    setError(null);
    try {
      if (editing) {
        const res = await entSend<{ performance: Performance } | Performance>('PUT', `/performances/${editing.id}`, {
          performer_id: performerId,
          performance_date: date,
          start_time: start,
          end_time: end,
          ends_next_day: effectiveNextDay,
          rate_cents: rateCents,
          notes: notes.trim() || null,
          version: editing.version,
          scope: isSeries ? scope : 'this',
        });
        onSaved('performance' in res ? res.performance : res, null);
      } else {
        const endsOn = endMode === 'until' ? until : endsOnForCount(date, weekdays, Number(count));
        const res = await entSend<{ performance: Performance; series: Series[] | null }>('POST', '/performances', {
          venue_id: venueId,
          performer_id: performerId,
          performance_date: date,
          start_time: start,
          end_time: end,
          ends_next_day: effectiveNextDay,
          rate_cents: rateCents,
          notes: notes.trim() || null,
          repeat: repeat ? { weekdays: weekdays.slice().sort(), ends_on: endsOn } : undefined,
        });
        onSaved(res.performance, res.series);
      }
      onClose();
    } catch (e) {
      setError({ text: errorText(e), stale: isStale(e) });
    }
    setBusy(false);
  };

  const remove = async () => {
    if (!editing) return;
    await entSend('DELETE', `/performances/${editing.id}?scope=${isSeries ? scope : 'this'}&version=${editing.version}`);
    onDeleted?.(editing.id);
    onClose();
  };

  const title = editing ? 'Edit booking' : 'Book a performer';
  return (
    <Dialog title={title} width={560} onClose={() => { if (!busy) onClose(); }} closeButton>
      <FieldGrid>
        <Field label="Performer" htmlFor={id('performer')} wide>
          <select id={id('performer')} className="n-select" style={inputStyle} value={performerId} onChange={(e) => setPerformerId(e.target.value)} autoFocus={!editing}>
            <option value="">Choose a performer</option>
            {choices.map((p) => (
              <option key={p.id} value={p.id}>{p.name}{p.onboarding_status !== 'accepted' ? ` (${p.onboarding_status})` : ''}{!p.is_active ? ' (inactive)' : ''}</option>
            ))}
          </select>
        </Field>
        <Field label="Venue" htmlFor={id('venue')}>
          <select id={id('venue')} className="n-select" style={inputStyle} value={venueId} onChange={(e) => setVenueId(e.target.value)} disabled={!!editing}>
            {meta.venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
        </Field>
        <Field label="Date" htmlFor={id('date')}>
          <input id={id('date')} className="n-input" style={inputStyle} type="date" value={date} onChange={(e) => { setDate(e.target.value); if (!repeat && e.target.value) setWeekdays([isoWeekday(e.target.value)]); }} />
        </Field>
        <Field label="Start" htmlFor={id('start')}>
          <input id={id('start')} className="n-input" style={inputStyle} type="time" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="End" htmlFor={id('end')}>
          <input id={id('end')} className="n-input" style={inputStyle} type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <Field label="Ends after midnight" htmlFor={id('next')}>
          <div style={{ paddingTop: 6 }}>
            <Toggle id={id('next')} checked={effectiveNextDay} onChange={(v) => { setNextDay(v); setNextDayTouched(true); }}>Finishes the next day</Toggle>
          </div>
        </Field>
        <Field label="Rate per hour" htmlFor={id('rate')} hint={minutes !== null && agreed !== null ? `${hoursLabel(minutes)} booked → ${money(agreed)} agreed` : 'Agreed amount = rate × booked hours'}>
          <MoneyInput id={id('rate')} value={rate} onChange={setRate} suffix="/ hour" />
        </Field>
        <Field label="Notes" htmlFor={id('notes')} wide>
          <input id={id('notes')} className="n-input" style={inputStyle} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Set, sound check, who booked them…" />
        </Field>
      </FieldGrid>

      {!editing && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line-soft)' }}>
          <Toggle checked={repeat} onChange={setRepeat}>Repeat weekly</Toggle>
          {repeat && (
            <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div role="group" aria-label="Weekdays" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {WEEKDAY_SHORT.map((label, i) => {
                  const day = i + 1;
                  const on = weekdays.includes(day);
                  return (
                    <button
                      key={day}
                      type="button"
                      aria-pressed={on}
                      onClick={() => setWeekdays((w) => (on ? w.filter((d) => d !== day) : [...w, day]))}
                      style={{
                        minWidth: 44, height: 34, padding: '0 10px', borderRadius: 999, cursor: 'pointer', fontFamily: 'inherit', fontSize: 'var(--fs-sm)', fontWeight: on ? 600 : 500,
                        border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line)'}`, background: on ? 'var(--accent-soft)' : 'var(--bg)', color: on ? 'var(--accent)' : 'var(--text)',
                      }}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center' }}>
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)' }}>
                  <input type="radio" name={id('endmode')} checked={endMode === 'until'} onChange={() => setEndMode('until')} /> Until
                </label>
                <input className="n-input" type="date" aria-label="Repeat until" value={until} min={date || undefined} disabled={endMode !== 'until'} onChange={(e) => setUntil(e.target.value)} />
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)' }}>
                  <input type="radio" name={id('endmode')} checked={endMode === 'count'} onChange={() => setEndMode('count')} /> After
                </label>
                <input className="n-input" type="number" inputMode="numeric" min={1} max={60} aria-label="Number of performances" value={count} disabled={endMode !== 'count'} onChange={(e) => setCount(e.target.value)} style={{ width: 72 }} />
                <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>performances</span>
              </div>
              {date && (
                <Hint>
                  {preview.length === 0 ? 'Pick a weekday.' : `${preview.length} performance${preview.length === 1 ? '' : 's'}: ${preview.slice(0, 6).map((d) => dateLabel(d, { year: false })).join(', ')}${preview.length > 6 ? `, … ${dateLabel(preview[preview.length - 1], { year: false })}` : ''}. Norm books up to a year ahead${agreed !== null ? `, ${money(agreed)} each` : ''}.`}
                </Hint>
              )}
            </div>
          )}
        </div>
      )}

      {editing && isSeries && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line-soft)' }}>
          <div className="n-label">This booking repeats. Apply the change to</div>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
            {(['this', 'following', 'all'] as SeriesScope[]).map((s) => (
              <label key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)' }}>
                <input type="radio" name={id('scope')} checked={scope === s} onChange={() => setScope(s)} />
                {s === 'this' ? 'This one' : s === 'following' ? 'This and the following' : 'All not yet approved'}
              </label>
            ))}
          </div>
        </div>
      )}

      <WriteError message={error?.text ?? null} stale={error?.stale} onReload={onClose} />
      <DialogActions
        left={editing && editing.performance_status === 'scheduled' && editing.payment_status !== 'paid' ? (
          <Button variant="danger" icon={Trash2} onClick={() => setConfirmDelete(true)} disabled={busy}>Delete</Button>
        ) : null}
      >
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : editing ? 'Save' : repeat ? 'Book series' : 'Book'}</Button>
      </DialogActions>

      {confirmDelete && editing && (
        <ConfirmDialog
          title={isSeries ? `Delete ${scope === 'this' ? 'this performance' : scope === 'following' ? 'this and the following' : 'every unapproved performance in the series'}?` : 'Delete this performance?'}
          confirmLabel="Delete"
          danger
          onClose={() => setConfirmDelete(false)}
          onConfirm={remove}
        >
          Only scheduled performances can be deleted; approved ones must be reset first. {isSeries && scope !== 'this' ? 'The repeat rule changes with it.' : ''}
        </ConfirmDialog>
      )}
    </Dialog>
  );
}
