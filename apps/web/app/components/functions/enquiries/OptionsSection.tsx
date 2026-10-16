'use client';

/**
 * Minimum-spend options on an enquiry, per venue: each option's area,
 * window, amount, derived state and a budget / last-year reference, with the
 * approval actions the API says this person may take (`rights`), withdraw,
 * edit, and "record the customer's answer" — a confirmation creates the
 * event. Approval only means "OK to offer"; it never books anything.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { CalendarCheck, ChevronDown, ChevronRight, CircleCheck, Plus } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import { fnGet, fnSend } from '../shared/api';
import { dateLabel, instantLabel, parseMoney, timeRangeLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { DoneNote, ErrorNote, Field, FormDialog, FormGrid, inputStyle, Muted, READ_ONLY_HINT, Section, useAction } from './bits';
import { AreaSelect, ChannelPicker, ReasonDialog } from './EnquiryDialogs';
import { channelLabel, isLiveOption, moneyAuto, optionBadge } from './labels';
import { openFnPage } from './nav';
import type { EnquiryDetail, MinSpendOption, SalesReference, StaffChannel } from './types';

type RefState = { status: 'loading' } | { status: 'done'; data: SalesReference } | { status: 'error'; error: string };

type Open =
  | { kind: 'add' }
  | { kind: 'edit' | 'override' | 'revoke' | 'withdraw' | 'confirm' | 'decline'; option: MinSpendOption };

const MAX_AUTO_REFERENCES = 6;

export default function OptionsSection({ meta, detail, onChanged, onReload }: {
  meta: FnMeta;
  detail: EnquiryDetail;
  /** Re-read the enquiry quietly after a change. */
  onChanged: () => void;
  onReload: () => void;
}) {
  const canWrite = meta.can_write;
  const currency = meta.company.currency;
  const tz = detail.venue?.timezone || meta.company.default_timezone;
  const lost = detail.stage === 'lost';
  const [open, setOpen] = useState<Open | null>(null);
  const [booked, setBooked] = useState<{ eventId: string; created: boolean } | null>(null);
  const { busy, error, setError, run } = useAction();
  const venueNames = useMemo(() => new Map(meta.venues.map((v) => [v.id, v.name])), [meta]);
  const bookingType = meta.booking_types.find((b) => b.value === detail.enquiry.booking_type);

  // Group by venue: the enquiry's own venue first, then the rest by name.
  const groups = useMemo(() => {
    const by = new Map<string, MinSpendOption[]>();
    for (const o of detail.options) by.set(o.venue_id, [...(by.get(o.venue_id) ?? []), o]);
    const name = (id: string) => venueNames.get(id) ?? (id === detail.venue?.id ? detail.venue.name : 'Another venue');
    return [...by.entries()]
      .sort(([a], [b]) => (a === detail.enquiry.venue_id ? -1 : b === detail.enquiry.venue_id ? 1 : name(a).localeCompare(name(b))))
      .map(([venueId, options]) => ({ venueId, name: name(venueId), options }));
  }, [detail, venueNames]);

  // Budget / last year: a live call to LoadedHub per option, so only for
  // options still on offer, a few at a time, once per option + date.
  // Settled answers by option + venue + date; one asked for but not back yet
  // reads as loading.
  const [refs, setRefs] = useState<Record<string, RefState>>({});
  const requested = useRef(new Set<string>());
  const autoRefs = useMemo(
    () => detail.options.filter((o) => isLiveOption(o) && o.event_date).slice(0, MAX_AUTO_REFERENCES)
      .map((o) => ({ id: o.id, key: `${o.id}:${o.venue_id}:${o.event_date}` })),
    [detail.options],
  );
  useEffect(() => {
    for (const { id, key } of autoRefs) {
      if (requested.current.has(key)) continue;
      requested.current.add(key);
      fnGet<SalesReference>(`/min-spends/${id}/reference`)
        .then((data) => setRefs((r) => ({ ...r, [key]: { status: 'done', data } })))
        .catch((e: unknown) => setRefs((r) => ({ ...r, [key]: { status: 'error', error: e instanceof Error ? e.message : String(e) } })));
    }
  }, [autoRefs]);
  const referenceFor = (id: string): RefState | undefined => {
    const auto = autoRefs.find((a) => a.id === id);
    return auto ? refs[auto.key] ?? { status: 'loading' } : undefined;
  };

  const approve = (o: MinSpendOption) =>
    run(`approve:${o.id}`, () => fnSend('POST', `/min-spends/${o.id}/approve`)).then((r) => { if (r !== undefined) onChanged(); });

  const done = () => { setOpen(null); onChanged(); };

  const actions = canWrite && !lost ? (
    <Button size="sm" icon={Plus} onClick={() => { setError(null); setOpen({ kind: 'add' }); }} data-testid="fn-add-option">Add option</Button>
  ) : !canWrite ? <Muted>{READ_ONLY_HINT}</Muted> : null;

  return (
    <Section
      title="Minimum spends"
      meta={detail.options.length ? `${detail.options.filter(isLiveOption).length} on offer` : undefined}
      actions={actions}
      padded={false}
      testId="fn-options"
    >
      <div style={{ padding: '4px 16px 14px' }}>
        {detail.options.length === 0 && (
          <p style={{ margin: '10px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            {bookingType && !bookingType.min_spend
              ? `${bookingType.label} doesn’t need a minimum spend — record the booking when the customer confirms.`
              : 'No minimum-spend options yet. Add one per area or time the customer could book; the venue’s approver signs it off before it’s offered.'}
          </p>
        )}
        {booked && (
          <DoneNote>
            <span style={{ flex: 1 }}>{booked.created ? 'Booked — the event is created.' : 'This option was already booked.'}</span>
            <Button size="sm" icon={CalendarCheck} onClick={() => openFnPage('events', { event: booked.eventId })}>Open the event</Button>
          </DoneNote>
        )}
        <ErrorNote error={error} onReload={onReload} onDismiss={() => setError(null)} />
        {groups.map((g) => (
          <div key={g.venueId} style={{ marginTop: 12 }}>
            {groups.length > 1 && <div className="n-eyebrow" style={{ marginBottom: 6 }}>{g.name}</div>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {g.options.map((o) => (
                <OptionRow
                  key={o.id}
                  option={o}
                  currency={currency}
                  tz={tz}
                  reference={referenceFor(o.id)}
                  canWrite={canWrite && !lost}
                  busy={busy}
                  onApprove={() => approve(o)}
                  onOpen={(kind) => { setError(null); setOpen({ kind, option: o }); }}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      {open?.kind === 'add' && <OptionDialog meta={meta} detail={detail} onSaved={done} onClose={() => setOpen(null)} />}
      {open?.kind === 'edit' && <OptionDialog meta={meta} detail={detail} option={open.option} onSaved={done} onClose={() => setOpen(null)} onReload={onReload} />}
      {open?.kind === 'override' && (
        <ReasonDialog
          title="Override the approval"
          intro="You're the override approver: this option can be offered without the approver's sign-off. The reason is kept in the approvals log."
          submitLabel="Override"
          onSubmit={async (reason) => { await fnSend('POST', `/min-spends/${open.option.id}/override`, { reason }); done(); }}
          onClose={() => setOpen(null)}
          onReload={onReload}
        />
      )}
      {open?.kind === 'revoke' && (
        <ReasonDialog
          title="Revoke the approval"
          intro="The option goes back to needing approval and the customer can't confirm it until it's approved again."
          required={false}
          submitLabel="Revoke"
          danger
          onSubmit={async (reason) => { await fnSend('POST', `/min-spends/${open.option.id}/revoke`, { reason }); done(); }}
          onClose={() => setOpen(null)}
          onReload={onReload}
        />
      )}
      {open?.kind === 'withdraw' && (
        <ReasonDialog
          title="Withdraw this option"
          intro="It stays on record but is no longer offered."
          required={false}
          submitLabel="Withdraw"
          danger
          onSubmit={async (reason) => { await fnSend('POST', `/min-spends/${open.option.id}/withdraw`, { reason }); done(); }}
          onClose={() => setOpen(null)}
          onReload={onReload}
        />
      )}
      {(open?.kind === 'confirm' || open?.kind === 'decline') && (
        <ResponseDialog
          meta={meta}
          detail={detail}
          option={open.option}
          kind={open.kind}
          venueName={venueNames.get(open.option.venue_id) ?? 'this venue'}
          onDone={(result) => {
            setOpen(null);
            if (result) setBooked(result);
            onChanged();
          }}
          onClose={() => setOpen(null)}
        />
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// One option
// ---------------------------------------------------------------------------

function OptionRow({ option: o, currency, tz, reference, canWrite, busy, onApprove, onOpen }: {
  option: MinSpendOption;
  currency: string;
  tz: string;
  reference: RefState | undefined;
  canWrite: boolean;
  busy: string | null;
  onApprove: () => void;
  onOpen: (kind: 'edit' | 'override' | 'revoke' | 'withdraw' | 'confirm' | 'decline') => void;
}) {
  const [history, setHistory] = useState(false);
  const badge = optionBadge(o);
  const live = isLiveOption(o);
  const facts = [...o.approvals.map((a) => ({ at: a.occurred_at, key: a.id, text: approvalText(a, currency) })),
    ...o.responses.map((r) => ({ at: r.occurred_at, key: r.id, text: responseText(r) }))]
    .sort((a, b) => a.at.localeCompare(b.at));
  const lastApproval = [...o.approvals].reverse().find((a) => a.kind !== 'revoke');
  const waiting = o.state === 'proposed' && o.area_requires_approval && !o.rights.approve && !o.rights.override;
  const window = [o.event_date ? dateLabel(o.event_date) : 'No date yet', timeRangeLabel(o.start_time, o.end_time, o.ends_next_day)].filter(Boolean).join(' · ');

  return (
    <div style={{ ...rowBox, opacity: o.state === 'withdrawn' ? 0.7 : 1 }} data-testid="fn-option">
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0, flex: '1 1 240px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600 }}>{o.area_name || o.label || 'Minimum spend'}</span>
            <Badge tone={badge.tone}>{badge.label}</Badge>
          </div>
          {o.area_name && o.label && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{o.label}</div>}
          <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{window}</div>
        </div>
        <div style={{ textAlign: 'right', flex: '0 0 auto' }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--fs-md)' }}>{moneyAuto(o.amount_cents, currency)}</div>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            {o.food_cents > 0 ? `Food ${moneyAuto(o.food_cents, currency)} · total ${moneyAuto(o.total_cents, currency)}` : 'minimum spend'}
          </div>
        </div>
      </div>

      {live && <ReferenceLine state={reference} currency={currency} />}
      {o.notes && <div style={{ marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', whiteSpace: 'pre-wrap' }}>{o.notes}</div>}
      {o.state === 'withdrawn' && <div style={{ marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Withdrawn{o.withdraw_reason ? ` — ${o.withdraw_reason}` : ''}</div>}
      {o.state === 'approved' && lastApproval && (
        <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          {lastApproval.kind === 'override' ? 'Overridden' : 'Approved'} by {lastApproval.actor || 'someone'} · {instantLabel(lastApproval.occurred_at, tz)}
          {lastApproval.reason ? ` — ${lastApproval.reason}` : ''}
        </div>
      )}
      {waiting && o.rights.why && (
        <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--warn)' }}>Waiting for approval — {o.rights.why}</div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
        {o.event_id && (
          <Button size="sm" icon={CalendarCheck} onClick={() => openFnPage('events', { event: o.event_id })}>Open the event</Button>
        )}
        {canWrite && o.confirmable && o.state !== 'confirmed' && o.state !== 'withdrawn' && (
          <Button size="sm" icon={CircleCheck} onClick={() => onOpen('confirm')} data-testid="fn-option-confirm">Record customer confirmation</Button>
        )}
        {canWrite && o.rights.approve && o.state === 'proposed' && (
          <Button size="sm" onClick={onApprove} disabled={!!busy} aria-busy={busy === `approve:${o.id}` || undefined}>
            {busy === `approve:${o.id}` ? 'Approving…' : 'Approve'}
          </Button>
        )}
        {canWrite && o.rights.override && o.state === 'proposed' && <Button size="sm" onClick={() => onOpen('override')}>Override…</Button>}
        {canWrite && o.rights.revoke && o.state === 'approved' && <Button size="sm" variant="quiet" onClick={() => onOpen('revoke')}>Revoke</Button>}
        {canWrite && live && <Button size="sm" variant="quiet" onClick={() => onOpen('decline')}>Record decline</Button>}
        {canWrite && o.state !== 'withdrawn' && <Button size="sm" variant="quiet" onClick={() => onOpen('edit')}>Edit</Button>}
        {canWrite && (live || o.state === 'declined') && <Button size="sm" variant="quiet" onClick={() => onOpen('withdraw')}>Withdraw</Button>}
        {facts.length > 0 && (
          <Button size="sm" variant="quiet" onClick={() => setHistory((h) => !h)} aria-expanded={history}>
            <Icon icon={history ? ChevronDown : ChevronRight} size={14} />
            History ({facts.length})
          </Button>
        )}
      </div>
      {history && (
        <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: '8px 0 0', borderTop: '1px solid var(--line-soft)', display: 'flex', flexDirection: 'column', gap: 4 }}>
          {facts.map((f) => (
            <li key={f.key} style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-soft)' }}>
              <span style={{ color: 'var(--muted)' }}>{instantLabel(f.at, tz)}</span> · {f.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function approvalText(a: MinSpendOption['approvals'][number], currency: string): string {
  const who = a.actor || 'Someone';
  const amount = a.amount_cents !== null ? ` at ${moneyAuto(a.amount_cents, currency)}` : '';
  if (a.kind === 'revoke') return a.automatic ? `Approval lifted automatically — the option changed${a.reason ? ` (${a.reason})` : ''}` : `${who} revoked the approval${a.reason ? ` — ${a.reason}` : ''}`;
  if (a.kind === 'override') return `${who} overrode the approval${amount}${a.reason ? ` — ${a.reason}` : ''}`;
  return `${a.automatic ? 'Approved automatically' : `${who} approved`}${amount}`;
}

function responseText(r: MinSpendOption['responses'][number]): string {
  const what = r.kind === 'confirm' ? 'Customer confirmed' : 'Customer declined';
  const how = r.channel === 'quote_page' ? 'on the quote page' : `by ${channelLabel(r.channel).toLowerCase()}`;
  const by = r.recorded_by && r.channel !== 'quote_page' ? ` (recorded by ${r.recorded_by})` : '';
  return `${what} ${how}${by}${r.note ? ` — ${r.note}` : ''}`;
}

function ReferenceLine({ state, currency }: { state: RefState | undefined; currency: string }) {
  if (!state) return null;
  const box: CSSProperties = { marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' };
  if (state.status === 'loading') return <div style={box}>Checking budget and last year…</div>;
  if (state.status === 'error') return <div style={box} title={state.error}>Budget —  · Last year —</div>;
  const r = state.data;
  if (!r.available) return <div style={box} title={r.reason ?? undefined}>Budget — · Last year —{r.reason ? <span> ({r.reason})</span> : null}</div>;
  return (
    <div style={box}>
      Budget <strong style={{ fontWeight: 600, color: 'var(--text-soft)' }}>{moneyAuto(r.budget_cents, currency)}</strong>
      {' · '}Last year <strong style={{ fontWeight: 600, color: 'var(--text-soft)' }}>{moneyAuto(r.last_year_cents, currency)}</strong>
      {r.last_year_date && <span> ({dateLabel(r.last_year_date, { year: true })})</span>}
      {r.tax === 'included' && <span> · incl. tax</span>}
    </div>
  );
}

const rowBox: CSSProperties = {
  padding: '12px 14px', borderRadius: 'var(--radius)', border: '1px solid var(--line)', background: 'var(--bg)',
};

// ---------------------------------------------------------------------------
// Add / edit an option
// ---------------------------------------------------------------------------

function OptionDialog({ meta, detail, option, onSaved, onClose, onReload }: {
  onReload?: () => void;
  meta: FnMeta;
  detail: EnquiryDetail;
  option?: MinSpendOption;
  onSaved: () => void;
  onClose: () => void;
}) {
  const e = detail.enquiry;
  const booked = option?.state === 'confirmed';
  const defaultVenue = option?.venue_id ?? e.venue_id ?? (meta.venues.length === 1 ? meta.venues[0].id : '');
  const [venueId, setVenueId] = useState(defaultVenue);
  const [areaId, setAreaId] = useState(option?.area_id ?? '');
  const [amount, setAmount] = useState(option ? String(option.amount_cents / 100) : '');
  const [label, setLabel] = useState(option?.label ?? '');
  const [date, setDate] = useState(option ? option.event_date ?? '' : e.preferred_date_start ?? '');
  const [startTime, setStartTime] = useState(((option ? option.start_time : e.preferred_start_time) ?? '').slice(0, 5));
  const [endTime, setEndTime] = useState(((option ? option.end_time : e.preferred_end_time) ?? '').slice(0, 5));
  const [nextDay, setNextDay] = useState(option ? option.ends_next_day : e.preferred_ends_next_day);
  const [notes, setNotes] = useState(option?.notes ?? '');
  const [note, setNote] = useState('');
  const cents = parseMoney(amount);

  // The reference for what's typed (venue + date), shown while drafting.
  const refKey = venueId && date ? `${venueId}:${date}` : null;
  const [refState, setRefState] = useState<{ key: string; ref: RefState } | null>(null);
  useEffect(() => {
    if (!venueId || !date) return;
    let alive = true;
    const key = `${venueId}:${date}`;
    const t = setTimeout(() => {
      fnGet<SalesReference>(`/venues/${venueId}/sales-reference`, { date })
        .then((data) => { if (alive) setRefState({ key, ref: { status: 'done', data } }); })
        .catch((err: unknown) => { if (alive) setRefState({ key, ref: { status: 'error', error: err instanceof Error ? err.message : String(err) } }); });
    }, 400);
    return () => { alive = false; clearTimeout(t); };
  }, [venueId, date]);
  const reference: RefState | undefined = !refKey ? undefined : refState?.key === refKey ? refState.ref : { status: 'loading' };

  const approvedChange = option?.state === 'approved';
  // A booked minimum only comes down with an approver's sign-off.
  const lowering = booked && cents !== null && cents < option.amount_cents;
  const lowerBlocked = lowering && !option.rights.lower;
  const submit = async () => {
    const fields: Record<string, unknown> = booked
      ? { amount_cents: cents, label: label.trim() || null, notes: notes.trim() || null }
      : {
        venue_id: venueId,
        area_id: areaId || null,
        amount_cents: cents,
        label: label.trim() || null,
        event_date: date || null,
        start_time: startTime || null,
        end_time: endTime || null,
        ends_next_day: nextDay,
        notes: notes.trim() || null,
      };
    if (!option) {
      await fnSend('POST', `/enquiries/${e.id}/min-spends`, fields);
      onSaved();
      return;
    }
    const before: Record<string, unknown> = {
      venue_id: option.venue_id,
      area_id: option.area_id ?? null,
      amount_cents: option.amount_cents,
      label: option.label ?? null,
      event_date: option.event_date ?? null,
      start_time: option.start_time ? option.start_time.slice(0, 5) : null,
      end_time: option.end_time ? option.end_time.slice(0, 5) : null,
      ends_next_day: option.ends_next_day,
      notes: option.notes ?? null,
    };
    const body: Record<string, unknown> = { version: option.version };
    for (const [k, v] of Object.entries(fields)) if (v !== before[k]) body[k] = v;
    if (Object.keys(body).length === 1) { onSaved(); return; }
    if (booked) body.note = note.trim();
    await fnSend('PATCH', `/min-spends/${option.id}`, body);
    onSaved();
  };

  return (
    <FormDialog
      title={option ? 'Edit option' : 'Add a minimum-spend option'}
      width={560}
      submitLabel={option ? 'Save' : 'Add option'}
      submitDisabled={cents === null || cents < 0 || !venueId || (booked && !note.trim()) || lowerBlocked}
      onSubmit={submit}
      onClose={onClose}
      onReload={onReload}
    >
      {booked && <Muted>This option is booked: only the amount, label and notes can change, and the change is logged on the event. Lowering the minimum needs the venue’s minimum-spend approver.</Muted>}
      {lowerBlocked && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--warn)' }}>{option.rights.why ?? 'Only the venue’s minimum-spend approver can lower a booked minimum.'}</div>}
      {approvedChange && <Muted>Changing the venue, area, amount, date or times of an approved option takes its approval away until it’s approved again.</Muted>}
      {!booked && (
        <FormGrid>
          <Field label="Venue">
            {(id) => (
              <select id={id} className="n-select" style={inputStyle} value={venueId} onChange={(ev) => { setVenueId(ev.target.value); setAreaId(''); }}>
                {!venueId && <option value="">Choose a venue</option>}
                {meta.venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
              </select>
            )}
          </Field>
          <Field label="Area">{(id) => <AreaSelect id={id} venueId={venueId || null} value={areaId} onChange={setAreaId} bookingType={e.booking_type} />}</Field>
        </FormGrid>
      )}
      <FormGrid>
        <Field label={`Minimum spend (${meta.company.currency})`}>
          {(id) => <input id={id} className="n-input" style={inputStyle} inputMode="decimal" placeholder="0" value={amount} onChange={(ev) => setAmount(ev.target.value)} autoFocus />}
        </Field>
        <Field label="Label (optional)">
          {(id) => <input id={id} className="n-input" style={inputStyle} placeholder="e.g. Option A" value={label} onChange={(ev) => setLabel(ev.target.value)} />}
        </Field>
      </FormGrid>
      {!booked && (
        <>
          <FormGrid min={130}>
            <Field label="Date">{(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={date} onChange={(ev) => setDate(ev.target.value)} />}</Field>
            <Field label="Start">{(id) => <input id={id} type="time" className="n-input" style={inputStyle} value={startTime} onChange={(ev) => setStartTime(ev.target.value)} />}</Field>
            <Field label="Finish">{(id) => <input id={id} type="time" className="n-input" style={inputStyle} value={endTime} onChange={(ev) => setEndTime(ev.target.value)} />}</Field>
          </FormGrid>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            <input type="checkbox" checked={nextDay} onChange={(ev) => setNextDay(ev.target.checked)} />
            Finishes after midnight
          </label>
          <ReferenceLine state={reference} currency={meta.company.currency} />
        </>
      )}
      <Field label="Notes (optional)">{(id) => <textarea id={id} className="n-input" rows={2} style={inputStyle} value={notes} onChange={(ev) => setNotes(ev.target.value)} />}</Field>
      {booked && (
        <Field label="Why the change">{(id) => <input id={id} className="n-input" style={inputStyle} value={note} onChange={(ev) => setNote(ev.target.value)} placeholder="Logged on the event" />}</Field>
      )}
    </FormDialog>
  );
}

// ---------------------------------------------------------------------------
// The customer's answer, recorded by staff
// ---------------------------------------------------------------------------

function ResponseDialog({ meta, detail, option, kind, venueName, onDone, onClose }: {
  meta: FnMeta;
  detail: EnquiryDetail;
  option: MinSpendOption;
  kind: 'confirm' | 'decline';
  venueName: string;
  onDone: (booked: { eventId: string; created: boolean } | null) => void;
  onClose: () => void;
}) {
  const [channel, setChannel] = useState<StaffChannel>('email');
  const [email, setEmail] = useState(detail.contact?.email ?? '');
  const [name, setName] = useState(detail.enquiry.subject ?? '');
  const [note, setNote] = useState('');
  const confirm = kind === 'confirm';
  const others = detail.options.filter((o) => o.id !== option.id && isLiveOption(o)).length;
  const menusHere = detail.menus.filter((m) => m.venue_id === option.venue_id && !m.event_id).length;

  const submit = async () => {
    const body: Record<string, unknown> = { channel, note: note.trim() || null, customer_email: email.trim() || null };
    if (confirm) {
      body.name = name.trim() || null;
      const res = await fnSend<{ event: { event: { id: string } }; created: boolean }>('POST', `/min-spends/${option.id}/confirm`, body);
      onDone({ eventId: res.event.event.id, created: res.created });
    } else {
      await fnSend('POST', `/min-spends/${option.id}/decline`, body);
      onDone(null);
    }
  };

  return (
    <FormDialog
      title={confirm ? 'Record customer confirmation' : 'Record a decline'}
      width={520}
      submitLabel={confirm ? 'Book it' : 'Record decline'}
      busyLabel={confirm ? 'Booking…' : undefined}
      onSubmit={submit}
      onClose={onClose}
    >
      <div style={{ padding: '10px 12px', borderRadius: 'var(--radius)', background: 'var(--surface-alt)', fontSize: 'var(--fs-sm)' }}>
        <strong style={{ fontWeight: 600 }}>{option.area_name || option.label || 'Minimum spend'}</strong>
        {' · '}{moneyAuto(option.amount_cents, meta.company.currency)}
        {option.event_date && <> · {dateLabel(option.event_date)}</>}
        {(option.start_time || option.end_time) && <> · {timeRangeLabel(option.start_time, option.end_time, option.ends_next_day)}</>}
      </div>
      {confirm ? (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          The customer said yes to this option, so Norm creates the event at {venueName}
          {others > 0 ? `, withdraws the other ${others === 1 ? 'option' : `${others} options`}` : ''}
          {menusHere > 0 ? ` and moves ${menusHere === 1 ? 'the pre-order menu' : `the ${menusHere} pre-order menus`} at ${venueName} onto the event` : ''}.
        </div>
      ) : (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          A decline is kept on record; the customer can still confirm this option later.
        </div>
      )}
      <Field label={confirm ? 'How did they confirm?' : 'How did they tell you?'}>
        {(id) => <ChannelPicker id={id} value={channel} onChange={setChannel} channels={meta.staff_channels} />}
      </Field>
      <FormGrid>
        <Field label="Customer email (optional)">{(id) => <input id={id} type="email" className="n-input" style={inputStyle} value={email} onChange={(ev) => setEmail(ev.target.value)} />}</Field>
        {confirm && <Field label="Event name">{(id) => <input id={id} className="n-input" style={inputStyle} value={name} onChange={(ev) => setName(ev.target.value)} />}</Field>}
      </FormGrid>
      <Field label="Note (optional)">
        {(id) => <textarea id={id} className="n-input" rows={2} style={inputStyle} value={note} placeholder={confirm ? 'e.g. Confirmed on the phone with Sam' : 'e.g. Went with another venue'} onChange={(ev) => setNote(ev.target.value)} />}
      </Field>
    </FormDialog>
  );
}
