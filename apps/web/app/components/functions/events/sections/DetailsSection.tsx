'use client';

/**
 * Details: date and times, guests, booking type, area, the booked minimum
 * spend (read-only — it belongs to the enquiry's option), payment, deposit,
 * security, entertainment, A/V, notes and the table plan. A field shows only
 * when the venue has it on for this booking type.
 */
import { useEffect, useId, useState } from 'react';
import { ImageUp, Plus, Trash2, X } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import IconButton from '../../../ui/IconButton';
import { apiFetch } from '../../../../lib/api';
import { fnGet, fnSend, fnUpload } from '../../shared/api';
import { dateLabel, money, parseMoney, timeRangeLabel } from '../../shared/format';
import {
  AV_LABEL,
  DEPOSIT_STATUSES,
  ENTERTAINMENT_LABEL,
  PAYMENT_OPTIONS,
  SECTION_BADGE,
  centsToInput,
  changedFields,
  depositStatusLabel,
  endsNextDay,
  normalisePayment,
  normaliseTime,
  paymentLabel,
  sectionBadge,
  textOrNull,
} from '../logic';
import { Fact, FactGrid, Field, FieldGrid, MoneyInput, SectionCard, inputStyle, listRowStyle } from '../ui';
import { useLoad, errorText } from '../useLoad';
import type { Area, AvRow, EntertainmentRow, EventDetail, EventFields, SecurityRow } from '../types';
import { useEditor } from './kit';
import type { SectionCtx, Write } from './kit';
import SectionActions from './SectionActions';

/** Table-plan image writes (`table_plan_file_id` on PATCH /events/{id},
 *  landed with F3's follow-up). Kept as a switch so the read-only fallback
 *  stays one line away. */
const TABLE_PLAN_IMAGE_WRITES = true;

interface DetailsDraft {
  name: string;
  event_date: string;
  start_time: string;
  end_time: string;
  guests: string;
  booking_type: string;
  area_id: string;
  payment_method: string;
  deposit: string;
  deposit_status: string;
  notes: string;
  special_instructions: string;
  description: string;
  table_plan: string;
  table_plan_file_id: string | null;
  security: SecurityRow[];
  entertainment: EntertainmentRow[];
  av: AvRow[];
}

function draftOf(d: EventDetail): DetailsDraft {
  const e = d.event;
  return {
    name: e.name ?? '',
    event_date: e.event_date,
    start_time: e.start_time ?? '',
    end_time: e.end_time ?? '',
    guests: e.guests === null || e.guests === undefined ? '' : String(e.guests),
    booking_type: e.booking_type ?? '',
    area_id: e.area_id ?? '',
    payment_method: normalisePayment(e.payment_method),
    deposit: centsToInput(e.deposit_cents),
    deposit_status: e.deposit_status ?? '',
    notes: e.notes ?? '',
    special_instructions: e.special_instructions ?? '',
    description: e.description ?? '',
    table_plan: e.table_plan ?? '',
    table_plan_file_id: e.table_plan_file_id ?? null,
    security: d.children.security.map((r) => ({ ...r })),
    entertainment: d.children.entertainment.map((r) => ({ ...r })),
    av: d.children.av.map((r) => ({ ...r })),
  };
}

/** The draft as API fields (venue-local times, cents, nulls for blanks). */
function fieldsOf(dr: DetailsDraft): Partial<EventFields> {
  const s = normaliseTime(dr.start_time);
  const e = normaliseTime(dr.end_time);
  return {
    name: dr.name.trim(),
    event_date: dr.event_date,
    start_time: s,
    end_time: e,
    ends_next_day: endsNextDay(s, e),
    guests: dr.guests.trim() === '' ? null : Math.max(0, Math.round(Number(dr.guests))),
    booking_type: dr.booking_type || null,
    area_id: dr.area_id || null,
    payment_method: dr.payment_method || null,
    deposit_cents: parseMoney(dr.deposit),
    deposit_status: dr.deposit_status || null,
    notes: textOrNull(dr.notes),
    special_instructions: textOrNull(dr.special_instructions),
    description: textOrNull(dr.description),
    table_plan: textOrNull(dr.table_plan),
    ...(TABLE_PLAN_IMAGE_WRITES ? { table_plan_file_id: dr.table_plan_file_id } : {}),
  };
}

function cleanSecurity(rows: SecurityRow[]) {
  return rows
    .map((r) => {
      const s = normaliseTime(r.start_time);
      const e = normaliseTime(r.end_time);
      return { name: textOrNull(r.name), start_time: s, end_time: e, ends_next_day: endsNextDay(s, e), notes: textOrNull(r.notes) };
    })
    .filter((r) => r.name || r.start_time || r.end_time || r.notes);
}

function cleanEntertainment(rows: EntertainmentRow[]) {
  return rows.map((r) => {
    const s = normaliseTime(r.start_time);
    const e = normaliseTime(r.end_time);
    return {
      kind: r.kind,
      name: textOrNull(r.name),
      link: r.kind === 'playlist' ? textOrNull(r.link) : null,
      start_time: s,
      end_time: e,
      ends_next_day: endsNextDay(s, e),
      notes: textOrNull(r.notes),
    };
  });
}

function cleanAv(rows: AvRow[]) {
  return rows.map((r) => ({ kind: r.kind, details: textOrNull(r.details) }));
}

const sameList = (a: unknown[], b: unknown[]) => JSON.stringify(a) === JSON.stringify(b);

/** An image from `GET /files/{id}` (needs the bearer token, so a blob URL). */
export function useFileUrl(fileId: string | null | undefined): string | null {
  const { data } = useLoad(fileId ? `file:${fileId}` : null, async () => {
    const res = await apiFetch(`/api/functions/files/${fileId}`);
    if (!res.ok) throw new Error(`Image unavailable (${res.status})`);
    return URL.createObjectURL(await res.blob());
  });
  useEffect(() => () => { if (data) URL.revokeObjectURL(data); }, [data]);
  return data ?? null;
}

function TablePlanImage({ fileId }: { fileId: string }) {
  const url = useFileUrl(fileId);
  if (!url) return <span style={{ color: 'var(--muted)' }}>Loading image…</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer" style={{ display: 'inline-block' }}>
      {/* eslint-disable-next-line @next/next/no-img-element -- an authenticated blob: URL, not a static asset */}
      <img src={url} alt="Table plan" style={{ display: 'block', maxWidth: '100%', maxHeight: 260, borderRadius: 'var(--radius)', border: '1px solid var(--line)' }} />
    </a>
  );
}

function OptionsList({ ctx }: { ctx: SectionCtx }) {
  const opts = ctx.detail.options;
  if (!opts.length) return <span style={{ color: 'var(--muted)' }}>No minimum spend on this booking</span>;
  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
      {opts.map((o) => {
        const approval = o.approvals.filter((a) => a.kind !== 'revoke').slice(-1)[0];
        const confirm = o.responses.filter((r) => r.kind === 'confirm').slice(-1)[0];
        return (
          <li key={o.id} style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <strong style={{ fontVariantNumeric: 'tabular-nums' }}>{money(o.amount_cents, ctx.currency, 0)}</strong>
            <span style={{ color: 'var(--text-soft)' }}>{[o.area_name, o.label].filter(Boolean).join(' · ')}</span>
            <Badge tone={o.state === 'confirmed' ? 'ok' : 'neutral'}>{o.state === 'confirmed' ? 'Confirmed' : o.state}</Badge>
            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', flexBasis: '100%' }}>
              {[
                approval ? `${approval.kind === 'override' ? 'Overridden' : 'Approved'} by ${approval.actor || 'Norm'}` : null,
                confirm ? `customer confirmed${confirm.channel && confirm.channel !== 'import' ? ` by ${confirm.channel.replace('_', ' ')}` : ''}` : null,
              ].filter(Boolean).join(' · ')}
              {o.food_cents > o.amount_cents ? ` · food ${money(o.food_cents, ctx.currency, 0)} is over the minimum` : ''}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export default function DetailsSection({ ctx }: { ctx: SectionCtx }) {
  const f = {
    name: useId(), date: useId(), start: useId(), end: useId(), guests: useId(), type: useId(), area: useId(), pay: useId(),
    dep: useId(), depStatus: useId(), notes: useId(), special: useId(), desc: useId(), plan: useId(), file: useId(),
  };
  const { detail, on } = ctx;
  const ev = detail.event;
  const ed = useEditor('details', ctx, () => draftOf(detail));
  const dr = ed.draft;
  const [uploading, setUploading] = useState(false);
  const profile = useLoad(ed.editing ? `profile:${ev.venue_id}` : null, () => fnGet<{ areas: Area[] }>(`/venues/${ev.venue_id}/profile`));
  const bookingTypes = ctx.meta.booking_types;
  const currentType = bookingTypes.find((b) => b.value === ev.booking_type);
  // A direct booking can't become a minimum-spend type (or back): those come from an option.
  const typeChoices = bookingTypes.filter((b) => !currentType || b.min_spend === currentType.min_spend);
  const entKinds = (['dj', 'playlist'] as const).filter((k) => on(`details.entertainment.${k}`));
  const avKinds = (['in_house_mic', 'in_house_tv', 'external_av'] as const).filter((k) => on(`details.av.${k}`));
  const badge = SECTION_BADGE[sectionBadge(detail, 'details')];

  const save = () => {
    if (!dr.name.trim()) {
      ed.setError(new Error('Give the event a name.'));
      return;
    }
    if (!dr.event_date) {
      ed.setError(new Error('The event needs a date.'));
      return;
    }
    const fields = changedFields(fieldsOf(ed.base), fieldsOf(dr));
    const lists = {
      security: [cleanSecurity(ed.base.security), cleanSecurity(dr.security)],
      entertainment: [cleanEntertainment(ed.base.entertainment), cleanEntertainment(dr.entertainment)],
      av: [cleanAv(ed.base.av), cleanAv(dr.av)],
    } as const;
    const changedLists = (Object.keys(lists) as (keyof typeof lists)[]).filter((k) => !sameList(lists[k][0], lists[k][1]));
    if (!Object.keys(fields).length && !changedLists.length) {
      ed.cancel();
      return;
    }
    const write: Write = async (note, apply) => {
      let d = detail;
      if (Object.keys(fields).length) {
        d = await fnSend<EventDetail>('PATCH', `/events/${ev.id}`, { version: d.event.version, note, ...fields });
        apply(d);
      }
      for (const kind of changedLists) {
        d = await fnSend<EventDetail>('PUT', `/events/${ev.id}/children/${kind}`, { version: d.event.version, rows: lists[kind][1], note });
        apply(d);
      }
      return d;
    };
    void ed.submit(write);
  };

  const upload = async (file: File) => {
    setUploading(true);
    ed.setError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const saved = await fnUpload<{ id: string }>('/files', form);
      ed.update({ table_plan_file_id: saved.id });
    } catch (e) {
      ed.setError(e);
    }
    setUploading(false);
  };

  const setRow = <K extends 'security' | 'entertainment' | 'av'>(kind: K, i: number, patch: Partial<DetailsDraft[K][number]>) =>
    ed.update({ [kind]: (dr[kind] as DetailsDraft[K][number][]).map((r, j) => (j === i ? { ...r, ...patch } : r)) } as Partial<DetailsDraft>);
  const removeRow = (kind: 'security' | 'entertainment' | 'av', i: number) =>
    ed.update({ [kind]: (dr[kind] as unknown[]).filter((_, j) => j !== i) } as Partial<DetailsDraft>);

  const areas = (profile.data?.areas ?? []).filter((a) => a.active || a.id === ev.area_id);
  const showPlan = !!(ev.table_plan || ev.table_plan_file_id || ev.booking_type === 'seated_dining');

  return (
    <SectionCard
      id="fn-event-details"
      title="Details"
      badge={<Badge tone={badge.tone}>{badge.label}</Badge>}
      actions={!ed.editing && (<><SectionActions ctx={ctx} section="details" />{ed.editButton}</>)}
      footer={ed.footer(save)}
    >
      {!ed.editing ? (
        <FactGrid>
          <Fact label="Date and time">
            {dateLabel(ev.event_date)}
            {ev.start_time ? `, ${timeRangeLabel(ev.start_time, ev.end_time, ev.ends_next_day)}` : ''}
          </Fact>
          <Fact label="Venue">{detail.venue?.name ?? null}</Fact>
          <Fact label="Guests">{ev.guests ?? null}</Fact>
          <Fact label="Booking type">{ev.booking_label}</Fact>
          {on('details.area') && <Fact label="Area">{detail.area?.name ?? null}</Fact>}
          {on('details.min_spend') && <Fact label="Minimum spend" wide><OptionsList ctx={ctx} /></Fact>}
          {on('details.payment') && (
            <Fact label="Payment">
              {paymentLabel(ev.payment_method)}
              {detail.invoice_contact ? ` — invoice ${[detail.invoice_contact.name, detail.invoice_contact.company].filter(Boolean).join(', ')}` : ''}
            </Fact>
          )}
          {on('details.deposit') && (
            <Fact label="Deposit">
              {ev.deposit_cents !== null && ev.deposit_cents !== undefined
                ? [money(ev.deposit_cents, ctx.currency), depositStatusLabel(ev.deposit_status)].filter(Boolean).join(' — ')
                : null}
            </Fact>
          )}
          {on('details.security') && (
            <Fact label="Security" wide>
              {detail.children.security.length
                ? detail.children.security.map((s, i) => (
                  <div key={s.id ?? i}>{[s.name || 'Guard', timeRangeLabel(s.start_time, s.end_time, s.ends_next_day), s.notes].filter(Boolean).join(' · ')}</div>
                ))
                : null}
            </Fact>
          )}
          {on('details.entertainment') && (
            <Fact label="Entertainment" wide>
              {detail.children.entertainment.filter((x) => entKinds.includes(x.kind)).length
                ? detail.children.entertainment.filter((x) => entKinds.includes(x.kind)).map((x, i) => (
                  <div key={x.id ?? i}>
                    {[ENTERTAINMENT_LABEL[x.kind], x.name, timeRangeLabel(x.start_time, x.end_time, x.ends_next_day), x.notes].filter(Boolean).join(' · ')}
                    {x.link && <> · <a href={x.link} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>link</a></>}
                  </div>
                ))
                : null}
            </Fact>
          )}
          {on('details.av') && (
            <Fact label="A/V" wide>
              {detail.children.av.filter((a) => avKinds.includes(a.kind)).length
                ? detail.children.av.filter((a) => avKinds.includes(a.kind)).map((a, i) => (
                  <div key={a.id ?? i}>{[AV_LABEL[a.kind], a.details].filter(Boolean).join(' — ')}</div>
                ))
                : null}
            </Fact>
          )}
          {on('details.notes') && <Fact label="Notes" wide>{ev.notes}</Fact>}
          {ev.special_instructions && <Fact label="Special instructions" wide>{ev.special_instructions}</Fact>}
          {ev.description && <Fact label="Description" wide>{ev.description}</Fact>}
          {showPlan && (
            <Fact label="Table plan" wide>
              {ev.table_plan || ev.table_plan_file_id ? (
                <>
                  {ev.table_plan && <div style={{ marginBottom: ev.table_plan_file_id ? 8 : 0 }}>{ev.table_plan}</div>}
                  {ev.table_plan_file_id && <TablePlanImage fileId={ev.table_plan_file_id} />}
                </>
              ) : null}
            </Fact>
          )}
        </FactGrid>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <FieldGrid>
            <Field label="Event name" htmlFor={f.name} wide>
              <input id={f.name} className="n-input" style={inputStyle} value={dr.name} onChange={(e) => ed.update({ name: e.target.value })} />
            </Field>
            <Field label="Date" htmlFor={f.date}>
              <input id={f.date} type="date" className="n-input" style={inputStyle} value={dr.event_date} onChange={(e) => ed.update({ event_date: e.target.value })} />
            </Field>
            <Field label="Start" htmlFor={f.start}>
              <input id={f.start} type="time" className="n-input" style={inputStyle} value={dr.start_time} onChange={(e) => ed.update({ start_time: e.target.value })} />
            </Field>
            <Field label="Finish" htmlFor={f.end} hint={endsNextDay(dr.start_time, dr.end_time) ? 'Finishes after midnight (next day)' : undefined}>
              <input id={f.end} type="time" className="n-input" style={inputStyle} value={dr.end_time} onChange={(e) => ed.update({ end_time: e.target.value })} />
            </Field>
            <Field label="Guests" htmlFor={f.guests}>
              <input id={f.guests} type="number" min={0} inputMode="numeric" className="n-input" style={inputStyle} value={dr.guests} onChange={(e) => ed.update({ guests: e.target.value })} />
            </Field>
            <Field label="Booking type" htmlFor={f.type}>
              <select id={f.type} className="n-select" style={inputStyle} value={dr.booking_type} onChange={(e) => ed.update({ booking_type: e.target.value })}>
                {typeChoices.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </Field>
            {on('details.area') && (
              <Field label="Area" htmlFor={f.area} hint={profile.loading ? 'Loading areas…' : profile.error !== undefined ? errorText(profile.error) : undefined}>
                <select id={f.area} className="n-select" style={inputStyle} value={dr.area_id} onChange={(e) => ed.update({ area_id: e.target.value })}>
                  <option value="">No particular area</option>
                  {areas.map((a) => (
                    <option key={a.id} value={a.id}>{a.name}{a.booking_types.length && dr.booking_type && !a.booking_types.includes(dr.booking_type) ? ' (not usually for this type)' : ''}</option>
                  ))}
                  {dr.area_id && !areas.some((a) => a.id === dr.area_id) && detail.area && <option value={detail.area.id}>{detail.area.name}</option>}
                </select>
              </Field>
            )}
            {on('details.payment') && (
              <Field label="Payment" htmlFor={f.pay}>
                <select id={f.pay} className="n-select" style={inputStyle} value={dr.payment_method} onChange={(e) => ed.update({ payment_method: e.target.value })}>
                  <option value="">Not set</option>
                  {PAYMENT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  {dr.payment_method && !PAYMENT_OPTIONS.some((o) => o.value === dr.payment_method) && <option value={dr.payment_method}>{dr.payment_method}</option>}
                </select>
              </Field>
            )}
            {on('details.deposit') && (
              <>
                <Field label="Deposit" htmlFor={f.dep}>
                  <MoneyInput id={f.dep} value={dr.deposit} onChange={(v) => ed.update({ deposit: v })} placeholder="None" />
                </Field>
                <Field label="Deposit status" htmlFor={f.depStatus}>
                  <select id={f.depStatus} className="n-select" style={inputStyle} value={dr.deposit_status} onChange={(e) => ed.update({ deposit_status: e.target.value })}>
                    <option value="">Not set</option>
                    {DEPOSIT_STATUSES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </Field>
              </>
            )}
          </FieldGrid>

          {on('details.security') && (
            <div>
              <div className="n-label">Security</div>
              {dr.security.map((r, i) => (
                <div key={i} style={listRowStyle}>
                  <input aria-label="Guard or company" className="n-input" placeholder="Name" value={r.name ?? ''} onChange={(e) => setRow('security', i, { name: e.target.value })} style={{ flex: '2 1 160px' }} />
                  <input aria-label="Start" type="time" className="n-input" value={r.start_time ?? ''} onChange={(e) => setRow('security', i, { start_time: e.target.value })} style={{ flex: '1 1 110px' }} />
                  <input aria-label="Finish" type="time" className="n-input" value={r.end_time ?? ''} onChange={(e) => setRow('security', i, { end_time: e.target.value })} style={{ flex: '1 1 110px' }} />
                  <input aria-label="Security notes" className="n-input" placeholder="Notes" value={r.notes ?? ''} onChange={(e) => setRow('security', i, { notes: e.target.value })} style={{ flex: '2 1 160px' }} />
                  <IconButton icon={Trash2} label="Remove security row" onClick={() => removeRow('security', i)} />
                </div>
              ))}
              <Button size="sm" icon={Plus} style={{ marginTop: 8 }}
                onClick={() => ed.update({ security: [...dr.security, { name: '', start_time: null, end_time: null, ends_next_day: false, notes: null }] })}>
                Add security
              </Button>
            </div>
          )}

          {on('details.entertainment') && entKinds.length > 0 && (
            <div>
              <div className="n-label">Entertainment</div>
              {dr.entertainment.map((r, i) => (
                <div key={i} style={listRowStyle}>
                  <Badge>{ENTERTAINMENT_LABEL[r.kind]}</Badge>
                  <input aria-label={`${ENTERTAINMENT_LABEL[r.kind]} name`} className="n-input" placeholder="Name" value={r.name ?? ''} onChange={(e) => setRow('entertainment', i, { name: e.target.value })} style={{ flex: '2 1 140px' }} />
                  {r.kind === 'playlist' && (
                    <input aria-label="Playlist link" className="n-input" placeholder="Link" value={r.link ?? ''} onChange={(e) => setRow('entertainment', i, { link: e.target.value })} style={{ flex: '2 1 160px' }} />
                  )}
                  <input aria-label="Start" type="time" className="n-input" value={r.start_time ?? ''} onChange={(e) => setRow('entertainment', i, { start_time: e.target.value })} style={{ flex: '1 1 110px' }} />
                  <input aria-label="Finish" type="time" className="n-input" value={r.end_time ?? ''} onChange={(e) => setRow('entertainment', i, { end_time: e.target.value })} style={{ flex: '1 1 110px' }} />
                  <IconButton icon={Trash2} label="Remove entertainment row" onClick={() => removeRow('entertainment', i)} />
                </div>
              ))}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                {entKinds.map((k) => (
                  <Button key={k} size="sm" icon={Plus}
                    onClick={() => ed.update({ entertainment: [...dr.entertainment, { kind: k, name: '', link: null, start_time: null, end_time: null, ends_next_day: false, notes: null }] })}>
                    Add {ENTERTAINMENT_LABEL[k] === 'DJ' ? 'DJ' : 'playlist'}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {on('details.av') && avKinds.length > 0 && (
            <div>
              <div className="n-label">A/V</div>
              {dr.av.map((r, i) => (
                <div key={i} style={listRowStyle}>
                  <Badge>{AV_LABEL[r.kind]}</Badge>
                  <input aria-label={`${AV_LABEL[r.kind]} details`} className="n-input" placeholder="Details" value={r.details ?? ''} onChange={(e) => setRow('av', i, { details: e.target.value })} style={{ flex: '1 1 200px' }} />
                  <IconButton icon={Trash2} label="Remove A/V row" onClick={() => removeRow('av', i)} />
                </div>
              ))}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                {avKinds.map((k) => (
                  <Button key={k} size="sm" icon={Plus} onClick={() => ed.update({ av: [...dr.av, { kind: k, details: null }] })}>
                    {AV_LABEL[k]}
                  </Button>
                ))}
              </div>
            </div>
          )}

          <FieldGrid>
            {on('details.notes') && (
              <Field label="Notes" htmlFor={f.notes} wide>
                <textarea id={f.notes} className="n-input" rows={3} style={inputStyle} value={dr.notes} onChange={(e) => ed.update({ notes: e.target.value })} />
              </Field>
            )}
            <Field label="Special instructions" htmlFor={f.special} wide>
              <textarea id={f.special} className="n-input" rows={2} style={inputStyle} value={dr.special_instructions} onChange={(e) => ed.update({ special_instructions: e.target.value })} />
            </Field>
            {(dr.description || ev.description) && (
              <Field label="Description" htmlFor={f.desc} wide>
                <textarea id={f.desc} className="n-input" rows={2} style={inputStyle} value={dr.description} onChange={(e) => ed.update({ description: e.target.value })} />
              </Field>
            )}
            {showPlan && (
              <Field label="Table plan" htmlFor={f.plan} wide>
                <textarea id={f.plan} className="n-input" rows={2} style={inputStyle} value={dr.table_plan} placeholder="Table layout" onChange={(e) => ed.update({ table_plan: e.target.value })} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                  {dr.table_plan_file_id && <TablePlanImage fileId={dr.table_plan_file_id} />}
                  {TABLE_PLAN_IMAGE_WRITES ? (
                    <>
                      <label htmlFor={f.file} className="n-btn n-btn--secondary n-btn--sm" style={{ cursor: uploading ? 'default' : 'pointer', opacity: uploading ? 0.45 : 1 }}>
                        <ImageUp size={14} strokeWidth={1.75} aria-hidden="true" />
                        {uploading ? 'Uploading…' : dr.table_plan_file_id ? 'Replace image' : 'Add an image'}
                      </label>
                      <input id={f.file} type="file" accept="image/jpeg,image/png,image/gif,image/webp" disabled={uploading} style={{ display: 'none' }}
                        onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void upload(file); }} />
                      {dr.table_plan_file_id && <Button size="sm" variant="quiet" icon={X} onClick={() => ed.update({ table_plan_file_id: null })}>Remove image</Button>}
                    </>
                  ) : (
                    <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>The table-plan image can’t be changed here yet.</span>
                  )}
                </div>
              </Field>
            )}
          </FieldGrid>
        </div>
      )}
      {ed.errorStrip}
    </SectionCard>
  );
}
