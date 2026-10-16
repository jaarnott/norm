'use client';

/**
 * Timings: arrival and seated times, the timing preference and notes, and
 * the run sheet — rows of time (or a label like "after speeches") and
 * activity, with headings, reordered with up/down buttons. "Copy from
 * another event" pulls a run sheet into the draft (there is no template
 * library any more — the plan replaced it with this).
 */
import { useEffect, useId, useState } from 'react';
import { ArrowDown, ArrowUp, CopyPlus, Heading2, Plus, Search, Trash2 } from 'lucide-react';
import Button from '../../../ui/Button';
import Dialog from '../../../ui/Dialog';
import Icon from '../../../ui/Icon';
import IconButton from '../../../ui/IconButton';
import { fnGet, fnSend } from '../../shared/api';
import { dateLabel, timeLabel } from '../../shared/format';
import { changedFields, moveItem, normaliseTime, textOrNull, timingNextDay } from '../logic';
import { Fact, FactGrid, Field, FieldGrid, SectionCard, inputStyle } from '../ui';
import { errorText, useLoad } from '../useLoad';
import type { EventDetail, EventFields, EventList, TimingRow } from '../types';
import { useEditor } from './kit';
import type { SectionCtx, Write } from './kit';

interface TimingsDraft {
  arrival_time: string;
  seated_time: string;
  timing_preference: string;
  timing_notes: string;
  rows: TimingRow[];
}

function draftOf(d: EventDetail): TimingsDraft {
  const e = d.event;
  return {
    arrival_time: e.arrival_time ?? '',
    seated_time: e.seated_time ?? '',
    timing_preference: e.timing_preference ?? '',
    timing_notes: e.timing_notes ?? '',
    rows: d.children.timings.map((r) => ({ ...r })),
  };
}

function fieldsOf(dr: TimingsDraft): Partial<EventFields> {
  return {
    arrival_time: normaliseTime(dr.arrival_time),
    seated_time: normaliseTime(dr.seated_time),
    timing_preference: textOrNull(dr.timing_preference),
    timing_notes: textOrNull(dr.timing_notes),
  };
}

/** Rows as the API takes them; empty rows are dropped, and a time after
 *  midnight at a late event is marked next day. */
export function cleanTimings(rows: TimingRow[], ev: Pick<EventFields, 'start_time' | 'end_time' | 'ends_next_day'>) {
  return rows
    .map((r) => {
      const at = r.is_heading ? null : normaliseTime(r.at_time);
      return {
        at_time: at,
        ends_next_day: r.is_heading ? false : timingNextDay(at, ev.start_time, ev.end_time, ev.ends_next_day),
        label: r.is_heading ? null : textOrNull(r.label),
        activity: textOrNull(r.activity),
        is_heading: !!r.is_heading,
      };
    })
    .filter((r) => r.at_time || r.label || r.activity);
}

function CopyTimingsDialog({ currentId, onCopy, onClose }: {
  currentId: string;
  onCopy: (rows: TimingRow[], mode: 'append' | 'replace') => void;
  onClose: () => void;
}) {
  const searchId = useId();
  const [query, setQuery] = useState('');
  const [q, setQ] = useState('');
  const [picked, setPicked] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);
  const results = useLoad(`copy-search:${q}`, () =>
    fnGet<EventList>('/events', { q: q || undefined, sort: 'date_desc', include_cancelled: true, limit: 25 }),
  );
  const source = useLoad(picked ? `copy-source:${picked}` : null, () => fnGet<EventDetail>(`/events/${picked}`));
  const rows = source.data?.children.timings ?? [];

  return (
    <Dialog title="Copy a run sheet from another event" width={560} onClose={onClose} closeButton>
      {!picked ? (
        <>
          <label className="n-label" htmlFor={searchId}>Find the event</label>
          <span style={{ position: 'relative', display: 'block' }}>
            <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
            <input id={searchId} className="n-input" style={{ ...inputStyle, paddingLeft: 32 }} placeholder="Event or contact name" value={query}
              onChange={(e) => setQuery(e.target.value)} autoFocus />
          </span>
          <div style={{ marginTop: 8, maxHeight: 320, overflowY: 'auto' }}>
            {results.loading && <div style={{ padding: 8, color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>Searching…</div>}
            {results.error !== undefined && <div style={{ padding: 8, color: 'var(--error)', fontSize: 'var(--fs-sm)' }}>{errorText(results.error)}</div>}
            {results.data?.rows.filter((r) => r.id !== currentId).map((r) => (
              <button key={r.id} type="button" className="n-option" style={{ borderRadius: 'var(--radius-sm)', padding: '8px 10px' }} onClick={() => setPicked(r.id)}>
                <div style={{ fontWeight: 600, fontSize: 'var(--fs-base)' }}>{r.name || 'Event'}</div>
                <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{[dateLabel(r.event_date), r.venue_name, r.contact_name].filter(Boolean).join(' · ')}</div>
              </button>
            ))}
            {results.data && !results.data.rows.length && <div style={{ padding: 8, color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>No events match.</div>}
          </div>
        </>
      ) : (
        <>
          {source.loading && <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>Loading its run sheet…</div>}
          {source.error !== undefined && <div style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)' }}>{errorText(source.error)}</div>}
          {source.data && (
            <>
              <div style={{ fontWeight: 600 }}>{source.data.event.name}</div>
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', marginBottom: 10 }}>{dateLabel(source.data.event.event_date)} · {source.data.venue?.name}</div>
              {rows.length ? (
                <ol style={{ margin: 0, paddingLeft: 18, maxHeight: 260, overflowY: 'auto', fontSize: 'var(--fs-sm)' }}>
                  {rows.map((r, i) => (
                    <li key={r.id ?? i} style={{ fontWeight: r.is_heading ? 600 : 400 }}>
                      {r.is_heading ? r.activity : [timeLabel(r.at_time), r.label, r.activity].filter(Boolean).join(' — ')}
                    </li>
                  ))}
                </ol>
              ) : (
                <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>That event has no run sheet.</div>
              )}
            </>
          )}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 18 }}>
            <Button onClick={() => setPicked(null)}>Back</Button>
            <Button disabled={!rows.length} onClick={() => { onCopy(rows, 'replace'); onClose(); }}>Replace this run sheet</Button>
            <Button variant="primary" disabled={!rows.length} onClick={() => { onCopy(rows, 'append'); onClose(); }}>Add to the end</Button>
          </div>
        </>
      )}
    </Dialog>
  );
}

export default function TimingsSection({ ctx }: { ctx: SectionCtx }) {
  const f = { arrival: useId(), seated: useId(), pref: useId(), notes: useId() };
  const { detail, on } = ctx;
  const ev = detail.event;
  const ed = useEditor('timings', ctx, () => draftOf(detail));
  const dr = ed.draft;
  const [copying, setCopying] = useState(false);
  const showSchedule = on('timings.schedule');

  const save = () => {
    const fields = changedFields(fieldsOf(ed.base), fieldsOf(dr));
    const before = cleanTimings(ed.base.rows, ev);
    const after = cleanTimings(dr.rows, ev);
    const rowsChanged = showSchedule && JSON.stringify(before) !== JSON.stringify(after);
    if (!Object.keys(fields).length && !rowsChanged) {
      ed.cancel();
      return;
    }
    const write: Write = async (note, apply) => {
      let d = detail;
      if (Object.keys(fields).length) {
        d = await fnSend<EventDetail>('PATCH', `/events/${ev.id}`, { version: d.event.version, note, ...fields });
        apply(d);
      }
      if (rowsChanged) {
        d = await fnSend<EventDetail>('PUT', `/events/${ev.id}/children/timings`, { version: d.event.version, rows: after, note });
        apply(d);
      }
      return d;
    };
    void ed.submit(write);
  };

  const setRow = (i: number, patch: Partial<TimingRow>) => ed.update({ rows: dr.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  const blank = (is_heading: boolean): TimingRow => ({ at_time: null, ends_next_day: false, label: null, activity: '', is_heading });
  const anyField = on('timings.arrival_time') || on('timings.seated_time') || on('timings.preference') || on('timings.notes');
  if (!anyField && !showSchedule) return null;

  return (
    <SectionCard
      id="fn-event-timings"
      title="Timings"
      actions={!ed.editing && ed.editButton}
      footer={ed.footer(save)}
    >
      {!ed.editing ? (
        <>
          {anyField && (
            <FactGrid>
              {on('timings.arrival_time') && <Fact label="Arrival">{timeLabel(ev.arrival_time)}</Fact>}
              {on('timings.seated_time') && <Fact label="Seated">{timeLabel(ev.seated_time)}</Fact>}
              {on('timings.preference') && <Fact label="Timing preference">{ev.timing_preference}</Fact>}
              {on('timings.notes') && <Fact label="Timing notes" wide>{ev.timing_notes}</Fact>}
            </FactGrid>
          )}
          {showSchedule && (
            <div style={{ marginTop: anyField ? 14 : 0 }}>
              <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--muted)', marginBottom: 4 }}>Run sheet</div>
              {detail.children.timings.length ? (
                <table className="n-table" style={{ fontSize: 'var(--fs-sm)' }}>
                  <thead><tr><th style={{ width: '30%' }}>Time</th><th>Activity</th></tr></thead>
                  <tbody>
                    {detail.children.timings.map((r, i) => r.is_heading ? (
                      <tr key={r.id ?? i}><td colSpan={2} style={{ fontWeight: 600, background: 'var(--surface)' }}>{r.activity}</td></tr>
                    ) : (
                      <tr key={r.id ?? i}>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          {[r.at_time ? timeLabel(r.at_time) + (r.ends_next_day ? ' (next day)' : '') : null, r.label].filter(Boolean).join(' · ') || '—'}
                        </td>
                        <td>{r.activity}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No run sheet yet.</div>
              )}
            </div>
          )}
        </>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {anyField && (
            <FieldGrid>
              {on('timings.arrival_time') && (
                <Field label="Arrival" htmlFor={f.arrival}>
                  <input id={f.arrival} type="time" className="n-input" style={inputStyle} value={dr.arrival_time} onChange={(e) => ed.update({ arrival_time: e.target.value })} />
                </Field>
              )}
              {on('timings.seated_time') && (
                <Field label="Seated" htmlFor={f.seated}>
                  <input id={f.seated} type="time" className="n-input" style={inputStyle} value={dr.seated_time} onChange={(e) => ed.update({ seated_time: e.target.value })} />
                </Field>
              )}
              {on('timings.preference') && (
                <Field label="Timing preference" htmlFor={f.pref}>
                  <input id={f.pref} className="n-input" style={inputStyle} value={dr.timing_preference} placeholder="e.g. Speeches before mains" onChange={(e) => ed.update({ timing_preference: e.target.value })} />
                </Field>
              )}
              {on('timings.notes') && (
                <Field label="Timing notes" htmlFor={f.notes} wide>
                  <textarea id={f.notes} className="n-input" rows={2} style={inputStyle} value={dr.timing_notes} onChange={(e) => ed.update({ timing_notes: e.target.value })} />
                </Field>
              )}
            </FieldGrid>
          )}
          {showSchedule && (
            <div>
              <div className="n-label">Run sheet</div>
              {dr.rows.map((r, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', padding: '6px 0', borderBottom: '1px solid var(--line-soft)' }}>
                  <span style={{ display: 'inline-flex' }}>
                    <IconButton icon={ArrowUp} label="Move up" iconSize={16} disabled={i === 0} onClick={() => ed.update({ rows: moveItem(dr.rows, i, i - 1) })} />
                    <IconButton icon={ArrowDown} label="Move down" iconSize={16} disabled={i === dr.rows.length - 1} onClick={() => ed.update({ rows: moveItem(dr.rows, i, i + 1) })} />
                  </span>
                  {r.is_heading ? (
                    <input aria-label="Heading" className="n-input" placeholder="Heading, e.g. Reception" value={r.activity ?? ''} onChange={(e) => setRow(i, { activity: e.target.value })}
                      style={{ flex: '1 1 240px', fontWeight: 600 }} />
                  ) : (
                    <>
                      <input aria-label="Time" type="time" className="n-input" value={r.at_time ?? ''} onChange={(e) => setRow(i, { at_time: e.target.value })} style={{ flex: '0 1 120px' }} />
                      <input aria-label="Or a label" className="n-input" placeholder="or e.g. After speeches" value={r.label ?? ''} onChange={(e) => setRow(i, { label: e.target.value })} style={{ flex: '1 1 140px' }} />
                      <input aria-label="Activity" className="n-input" placeholder="Activity" value={r.activity ?? ''} onChange={(e) => setRow(i, { activity: e.target.value })} style={{ flex: '2 1 200px' }} />
                    </>
                  )}
                  <IconButton icon={Trash2} label="Remove row" onClick={() => ed.update({ rows: dr.rows.filter((_, j) => j !== i) })} />
                </div>
              ))}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                <Button size="sm" icon={Plus} onClick={() => ed.update({ rows: [...dr.rows, blank(false)] })}>Add a time</Button>
                <Button size="sm" icon={Heading2} onClick={() => ed.update({ rows: [...dr.rows, blank(true)] })}>Add a heading</Button>
                <Button size="sm" variant="quiet" icon={CopyPlus} onClick={() => setCopying(true)}>Copy from another event</Button>
              </div>
            </div>
          )}
        </div>
      )}
      {copying && (
        <CopyTimingsDialog
          currentId={ev.id}
          onClose={() => setCopying(false)}
          onCopy={(rows, mode) => {
            const copied = rows.map((r) => ({ at_time: r.at_time, ends_next_day: false, label: r.label, activity: r.activity, is_heading: r.is_heading }));
            ed.update({ rows: mode === 'replace' ? copied : [...dr.rows, ...copied] });
          }}
        />
      )}
      {ed.errorStrip}
    </SectionCard>
  );
}
