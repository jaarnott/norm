'use client';

/**
 * The enquiry page's forms: new enquiry (new or existing contact), edit the
 * event details, a reason prompt (close / reopen / override / revoke /
 * withdraw), follow-up, contact edit and "record the booking" for bookings
 * with no minimum spend. Each posts to the staff API and hands the fresh
 * record back; refusals stay inside the dialog in the API's own words.
 */
import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import Button from '../../ui/Button';
import { fnGet, fnSend } from '../shared/api';
import type { FnMeta } from '../shared/meta';
import { Field, FormDialog, FormGrid, inputStyle, Muted } from './bits';
import { channelLabel, sourceLabel } from './labels';
import type { Contact, EnquiryDetail, EventRow, Page, StaffChannel, VenueArea, VenueProfile } from './types';

const GROUP_WIDE = '';

/** '' → null, for optional text fields. */
const orNull = (v: string) => (v.trim() === '' ? null : v.trim());
const intOrNull = (v: string) => {
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : null;
};

// ---------------------------------------------------------------------------
// Reason
// ---------------------------------------------------------------------------

/** Ask for a reason, then run `onSubmit(reason)`. */
export function ReasonDialog({ title, intro, label = 'Reason', required = true, submitLabel, danger = false, placeholder, onSubmit, onClose, onReload }: {
  onReload?: () => void;
  title: string;
  intro?: ReactNode;
  label?: string;
  required?: boolean;
  submitLabel: string;
  danger?: boolean;
  placeholder?: string;
  onSubmit: (reason: string | null) => Promise<void>;
  onClose: () => void;
}) {
  const [reason, setReason] = useState('');
  return (
    <FormDialog
      title={title}
      submitLabel={submitLabel}
      danger={danger}
      submitDisabled={required && !reason.trim()}
      onSubmit={() => onSubmit(orNull(reason))}
      onClose={onClose}
      onReload={onReload}
    >
      {intro && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{intro}</div>}
      <Field label={required ? label : `${label} (optional)`}>
        {(id) => (
          <textarea id={id} className="n-input" rows={3} style={inputStyle} value={reason} placeholder={placeholder}
            onChange={(e) => setReason(e.target.value)} autoFocus />
        )}
      </Field>
    </FormDialog>
  );
}

// ---------------------------------------------------------------------------
// Follow-up
// ---------------------------------------------------------------------------

export function FollowUpDialog({ initialDate, initialReason, onSubmit, onClose }: {
  initialDate: string | null;
  initialReason: string | null;
  onSubmit: (date: string, reason: string | null) => Promise<void>;
  onClose: () => void;
}) {
  const [date, setDate] = useState(initialDate ?? '');
  const [reason, setReason] = useState(initialReason ?? '');
  return (
    <FormDialog title="Follow-up" submitLabel="Set follow-up" submitDisabled={!date} onSubmit={() => onSubmit(date, orNull(reason))} onClose={onClose}>
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        A follow-up date becomes the next action and parks the automatic day 7 and day 14 reminders until then.
      </div>
      <Field label="Follow up on">
        {(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={date} onChange={(e) => setDate(e.target.value)} autoFocus />}
      </Field>
      <Field label="Reason (optional)">
        {(id) => (
          <textarea id={id} className="n-input" rows={2} style={inputStyle} value={reason} placeholder="e.g. Customer asked us to call back after their board meeting"
            onChange={(e) => setReason(e.target.value)} />
        )}
      </Field>
    </FormDialog>
  );
}

// ---------------------------------------------------------------------------
// Contact
// ---------------------------------------------------------------------------

export function EditContactDialog({ contact, onSaved, onClose, onReload }: {
  onReload?: () => void;
  contact: Contact;
  onSaved: (c: Contact) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(contact.name ?? '');
  const [company, setCompany] = useState(contact.company ?? '');
  const [phone, setPhone] = useState(contact.phone ?? '');
  const [notes, setNotes] = useState(contact.notes ?? '');
  const submit = async () => {
    const body: Record<string, unknown> = { version: contact.version };
    if (name.trim() !== (contact.name ?? '')) body.name = name.trim();
    if (orNull(company) !== (contact.company ?? null)) body.company = orNull(company);
    if (orNull(phone) !== (contact.phone ?? null)) body.phone = orNull(phone);
    if (orNull(notes) !== (contact.notes ?? null)) body.notes = orNull(notes);
    if (Object.keys(body).length === 1) return;
    onSaved(await fnSend<Contact>('PATCH', `/contacts/${contact.id}`, body));
  };
  return (
    <FormDialog title="Edit contact" submitLabel="Save" submitDisabled={!name.trim()} onSubmit={submit} onClose={onClose} onReload={onReload}>
      <FormGrid>
        <Field label="Name">{(id) => <input id={id} className="n-input" style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} autoFocus />}</Field>
        <Field label="Company">{(id) => <input id={id} className="n-input" style={inputStyle} value={company} onChange={(e) => setCompany(e.target.value)} />}</Field>
        <Field label="Phone">{(id) => <input id={id} type="tel" className="n-input" style={inputStyle} value={phone} onChange={(e) => setPhone(e.target.value)} />}</Field>
      </FormGrid>
      <Field label="Notes">{(id) => <textarea id={id} className="n-input" rows={3} style={inputStyle} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
      <Muted>Email addresses are managed on the contact&apos;s page.</Muted>
    </FormDialog>
  );
}

// ---------------------------------------------------------------------------
// Areas (for option and booking forms)
// ---------------------------------------------------------------------------

const areaCache = new Map<string, Promise<VenueArea[]>>();

/** A venue's active areas, from its Functions profile (cached per page load). */
export function useVenueAreas(venueId: string | null): { areas: VenueArea[]; error: string | null } {
  const [state, setState] = useState<{ venueId: string | null; areas: VenueArea[]; error: string | null }>({ venueId: null, areas: [], error: null });
  useEffect(() => {
    if (!venueId) return;
    let alive = true;
    let p = areaCache.get(venueId);
    if (!p) {
      p = fnGet<VenueProfile>(`/venues/${venueId}/profile`).then((prof) => prof.areas.filter((a) => a.active));
      p.catch(() => areaCache.delete(venueId as string));
      areaCache.set(venueId, p);
    }
    p.then((areas) => { if (alive) setState({ venueId, areas, error: null }); })
      .catch((e: unknown) => { if (alive) setState({ venueId, areas: [], error: e instanceof Error ? e.message : String(e) }); });
    return () => { alive = false; };
  }, [venueId]);
  return state.venueId === venueId ? { areas: state.areas, error: state.error } : { areas: [], error: null };
}

export function AreaSelect({ id, venueId, value, onChange, bookingType }: {
  id: string;
  venueId: string | null;
  value: string;
  onChange: (v: string) => void;
  bookingType?: string | null;
}) {
  const { areas, error } = useVenueAreas(venueId);
  // Areas offered for this booking type first; the rest stay choosable.
  const sorted = useMemo(() => {
    if (!bookingType) return areas;
    const fits = (a: VenueArea) => !a.booking_types?.length || a.booking_types.includes(bookingType);
    return [...areas.filter(fits), ...areas.filter((a) => !fits(a))];
  }, [areas, bookingType]);
  return (
    <>
      <select id={id} className="n-select" style={inputStyle} value={value} onChange={(e) => onChange(e.target.value)} disabled={!venueId}>
        <option value="">No particular area</option>
        {sorted.map((a) => (
          <option key={a.id} value={a.id}>{a.name}{a.requires_approval ? ' (needs approval)' : ''}</option>
        ))}
      </select>
      {error && <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--error)' }}>Couldn’t load areas: {error}</div>}
    </>
  );
}

// ---------------------------------------------------------------------------
// New enquiry
// ---------------------------------------------------------------------------

/**
 * Record a new enquiry — for a contact already in Norm (search by name,
 * company or email) or a new one (matched by email on the server, so a typed
 * address that already exists joins that contact).
 */
export function NewEnquiryDialog({ meta, presetContact, onCreated, onClose }: {
  meta: FnMeta;
  presetContact: Contact | null;
  onCreated: (d: EnquiryDetail) => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<'existing' | 'new'>(presetContact ? 'existing' : 'new');
  const [picked, setPicked] = useState<Contact | null>(presetContact);
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<Contact[]>([]);
  const [searching, setSearching] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [company, setCompany] = useState('');
  const [venueId, setVenueId] = useState(meta.venues.length === 1 ? meta.venues[0].id : GROUP_WIDE);
  const [bookingType, setBookingType] = useState('');
  const [subject, setSubject] = useState('');
  const [guests, setGuests] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [nextDay, setNextDay] = useState(false);
  const [source, setSource] = useState('manual');
  const [notes, setNotes] = useState('');

  const searchable = mode === 'existing' && query.trim().length >= 2;
  useEffect(() => {
    if (!searchable) return;
    let alive = true;
    const t = setTimeout(() => {
      setSearching(true);
      fnGet<Page<Contact>>('/contacts', { q: query.trim(), limit: 8 })
        .then((d) => { if (alive) setFound(d.rows); })
        .catch(() => { if (alive) setFound([]); })
        .finally(() => { if (alive) setSearching(false); });
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [query, searchable]);
  const matches = searchable ? found : [];

  const contactReady = mode === 'existing' ? !!picked : !!name.trim();
  const submit = async () => {
    const body: Record<string, unknown> = {
      venue_id: venueId || null,
      booking_type: bookingType || null,
      subject: orNull(subject),
      guests: intOrNull(guests),
      preferred_date_start: dateFrom || null,
      preferred_date_end: dateTo || dateFrom || null,
      preferred_start_time: startTime || null,
      preferred_end_time: endTime || null,
      preferred_ends_next_day: nextDay,
      source,
      notes: orNull(notes),
    };
    if (mode === 'existing' && picked) body.contact_id = picked.id;
    else body.contact = { name: name.trim(), email: orNull(email), phone: orNull(phone), company: orNull(company) };
    onCreated(await fnSend<EnquiryDetail>('POST', '/enquiries', body));
  };

  const staffSources = (meta.enquiry_sources as string[]).filter((s) => s !== 'import' && s !== 'agent');

  return (
    <FormDialog title="New enquiry" width={620} submitLabel="Create enquiry" busyLabel="Creating…" submitDisabled={!contactReady} onSubmit={submit} onClose={onClose}>
      <div role="radiogroup" aria-label="Contact" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <Button size="sm" variant={mode === 'new' ? 'primary' : 'secondary'} aria-pressed={mode === 'new'} onClick={() => setMode('new')}>New contact</Button>
        <Button size="sm" variant={mode === 'existing' ? 'primary' : 'secondary'} aria-pressed={mode === 'existing'} onClick={() => setMode('existing')}>Existing contact</Button>
      </div>
      {mode === 'existing' ? (
        picked ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '8px 12px', borderRadius: 'var(--radius)', background: 'var(--surface-alt)' }}>
            <span style={{ minWidth: 0 }}>
              <strong style={{ fontWeight: 600 }}>{picked.name || 'Unnamed contact'}</strong>
              <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>{[picked.company, picked.email].filter(Boolean).map((s) => ` · ${s}`).join('')}</span>
            </span>
            <Button size="sm" variant="quiet" onClick={() => setPicked(null)}>Change</Button>
          </div>
        ) : (
          <Field label="Find a contact" hint={searchable && searching ? 'Searching…' : searchable && !matches.length ? 'No contacts match — switch to “New contact”.' : undefined}>
            {(id) => (
              <>
                <input id={id} type="search" className="n-input" style={inputStyle} value={query} placeholder="Name, company, phone or email"
                  onChange={(e) => setQuery(e.target.value)} autoFocus />
                {matches.length > 0 && (
                  <div role="listbox" aria-label="Matching contacts" style={{ marginTop: 6, border: '1px solid var(--line)', borderRadius: 'var(--radius)', maxHeight: 220, overflowY: 'auto' }}>
                    {matches.map((c) => (
                      <button key={c.id} type="button" role="option" aria-selected={false} className="n-option" onClick={() => setPicked(c)}>
                        <span style={{ fontWeight: 500 }}>{c.name || 'Unnamed contact'}</span>
                        <span style={{ color: 'var(--muted)' }}>{[c.company, c.email].filter(Boolean).map((s) => ` · ${s}`).join('')}</span>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </Field>
        )
      ) : (
        <FormGrid>
          <Field label="Name">{(id) => <input id={id} className="n-input" style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} autoFocus />}</Field>
          <Field label="Email" hint="An address already in Norm joins that contact.">{(id) => <input id={id} type="email" className="n-input" style={inputStyle} value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
          <Field label="Phone">{(id) => <input id={id} type="tel" className="n-input" style={inputStyle} value={phone} onChange={(e) => setPhone(e.target.value)} />}</Field>
          <Field label="Company">{(id) => <input id={id} className="n-input" style={inputStyle} value={company} onChange={(e) => setCompany(e.target.value)} />}</Field>
        </FormGrid>
      )}
      <FormGrid>
        <Field label="Venue">
          {(id) => (
            <select id={id} className="n-select" style={inputStyle} value={venueId} onChange={(e) => setVenueId(e.target.value)}>
              <option value={GROUP_WIDE}>Not decided (group-wide)</option>
              {meta.venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Booking type">
          {(id) => (
            <select id={id} className="n-select" style={inputStyle} value={bookingType} onChange={(e) => setBookingType(e.target.value)}>
              <option value="">Not sure yet</option>
              {meta.booking_types.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
            </select>
          )}
        </Field>
        <Field label="Source">
          {(id) => (
            <select id={id} className="n-select" style={inputStyle} value={source} onChange={(e) => setSource(e.target.value)}>
              {staffSources.map((s) => <option key={s} value={s}>{sourceLabel(s)}</option>)}
            </select>
          )}
        </Field>
      </FormGrid>
      <Field label="Event">
        {(id) => <input id={id} className="n-input" style={inputStyle} value={subject} placeholder="e.g. End-of-year party for 60" onChange={(e) => setSubject(e.target.value)} />}
      </Field>
      <FormGrid min={140}>
        <Field label="Guests">{(id) => <input id={id} type="number" min={0} inputMode="numeric" className="n-input" style={inputStyle} value={guests} onChange={(e) => setGuests(e.target.value)} />}</Field>
        <Field label="Preferred date">{(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />}</Field>
        <Field label="Or a range, to">{(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={dateTo} min={dateFrom || undefined} onChange={(e) => setDateTo(e.target.value)} />}</Field>
        <Field label="Start">{(id) => <input id={id} type="time" className="n-input" style={inputStyle} value={startTime} onChange={(e) => setStartTime(e.target.value)} />}</Field>
        <Field label="Finish">{(id) => <input id={id} type="time" className="n-input" style={inputStyle} value={endTime} onChange={(e) => setEndTime(e.target.value)} />}</Field>
      </FormGrid>
      <label style={checkLabel}>
        <input type="checkbox" checked={nextDay} onChange={(e) => setNextDay(e.target.checked)} />
        Finishes after midnight
      </label>
      <Field label="Notes (internal)">{(id) => <textarea id={id} className="n-input" rows={3} style={inputStyle} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
    </FormDialog>
  );
}

const checkLabel = { display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' } as const;

// ---------------------------------------------------------------------------
// Edit details
// ---------------------------------------------------------------------------

export function EditEnquiryDialog({ meta, detail, onSaved, onClose, onReload }: {
  onReload?: () => void;
  meta: FnMeta;
  detail: EnquiryDetail;
  onSaved: (d: EnquiryDetail) => void;
  onClose: () => void;
}) {
  const e = detail.enquiry;
  const [subject, setSubject] = useState(e.subject ?? '');
  const [venueId, setVenueId] = useState(e.venue_id ?? GROUP_WIDE);
  const [bookingType, setBookingType] = useState(e.booking_type ?? '');
  const [guests, setGuests] = useState(e.guests === null ? '' : String(e.guests));
  const [dateFrom, setDateFrom] = useState(e.preferred_date_start ?? '');
  const [dateTo, setDateTo] = useState(e.preferred_date_end ?? '');
  const [startTime, setStartTime] = useState(e.preferred_start_time ?? '');
  const [endTime, setEndTime] = useState(e.preferred_end_time ?? '');
  const [nextDay, setNextDay] = useState(e.preferred_ends_next_day);
  const [source, setSource] = useState(e.source ?? 'manual');
  const [owner, setOwner] = useState(e.owner_user_id ?? '');
  const [clientNote, setClientNote] = useState(e.client_note ?? '');
  const venueKnown = !venueId || meta.venues.some((v) => v.id === venueId);

  const submit = async () => {
    const next: Record<string, unknown> = {
      subject: orNull(subject),
      venue_id: venueId || null,
      booking_type: bookingType || null,
      guests: intOrNull(guests),
      preferred_date_start: dateFrom || null,
      preferred_date_end: dateTo || dateFrom || null,
      preferred_start_time: startTime ? startTime.slice(0, 5) : null,
      preferred_end_time: endTime ? endTime.slice(0, 5) : null,
      preferred_ends_next_day: nextDay,
      source,
      owner_user_id: owner || null,
      client_note: orNull(clientNote),
    };
    const before: Record<string, unknown> = {
      subject: e.subject ?? null,
      venue_id: e.venue_id ?? null,
      booking_type: e.booking_type ?? null,
      guests: e.guests ?? null,
      preferred_date_start: e.preferred_date_start ?? null,
      preferred_date_end: e.preferred_date_end ?? null,
      preferred_start_time: e.preferred_start_time ? e.preferred_start_time.slice(0, 5) : null,
      preferred_end_time: e.preferred_end_time ? e.preferred_end_time.slice(0, 5) : null,
      preferred_ends_next_day: e.preferred_ends_next_day,
      source: e.source ?? 'manual',
      owner_user_id: e.owner_user_id ?? null,
      client_note: e.client_note ?? null,
    };
    const body: Record<string, unknown> = { version: e.version };
    for (const [k, v] of Object.entries(next)) if (v !== before[k]) body[k] = v;
    if (Object.keys(body).length === 1) return;
    onSaved(await fnSend<EnquiryDetail>('PATCH', `/enquiries/${e.id}`, body));
  };

  // Owners who can act at the chosen venue (anyone in the org for group-wide).
  const owners = meta.members.filter((m) => !venueId || m.venue_ids.includes(venueId));
  return (
    <FormDialog title="Edit enquiry" width={620} submitLabel="Save" onSubmit={submit} onClose={onClose} onReload={onReload}>
      <Field label="Event">{(id) => <input id={id} className="n-input" style={inputStyle} value={subject} onChange={(ev) => setSubject(ev.target.value)} autoFocus />}</Field>
      <FormGrid>
        <Field label="Venue" hint={detail.events.some((ev) => !ev.cancelled_at) ? 'A booked enquiry keeps its venue — change it on the event.' : undefined}>
          {(id) => (
            <select id={id} className="n-select" style={inputStyle} value={venueId} onChange={(ev) => setVenueId(ev.target.value)}>
              <option value={GROUP_WIDE}>Not decided (group-wide)</option>
              {!venueKnown && <option value={venueId}>{detail.venue?.name ?? 'Current venue'}</option>}
              {meta.venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Booking type">
          {(id) => (
            <select id={id} className="n-select" style={inputStyle} value={bookingType} onChange={(ev) => setBookingType(ev.target.value)}>
              <option value="">Not sure yet</option>
              {meta.booking_types.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
            </select>
          )}
        </Field>
        <Field label="Owner">
          {(id) => (
            <select id={id} className="n-select" style={inputStyle} value={owner} onChange={(ev) => setOwner(ev.target.value)}>
              <option value="">Nobody</option>
              {e.owner_user_id && !owners.some((m) => m.id === e.owner_user_id) && <option value={e.owner_user_id}>{detail.owner_name ?? 'Current owner'}</option>}
              {owners.map((m) => <option key={m.id} value={m.id}>{m.name || m.email}</option>)}
            </select>
          )}
        </Field>
        <Field label="Source">
          {(id) => (
            <select id={id} className="n-select" style={inputStyle} value={source} onChange={(ev) => setSource(ev.target.value)}>
              {(meta.enquiry_sources as string[]).map((s) => <option key={s} value={s}>{sourceLabel(s)}</option>)}
            </select>
          )}
        </Field>
      </FormGrid>
      <FormGrid min={140}>
        <Field label="Guests">{(id) => <input id={id} type="number" min={0} inputMode="numeric" className="n-input" style={inputStyle} value={guests} onChange={(ev) => setGuests(ev.target.value)} />}</Field>
        <Field label="Preferred date">{(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={dateFrom} onChange={(ev) => setDateFrom(ev.target.value)} />}</Field>
        <Field label="Or a range, to">{(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={dateTo} min={dateFrom || undefined} onChange={(ev) => setDateTo(ev.target.value)} />}</Field>
        <Field label="Start">{(id) => <input id={id} type="time" className="n-input" style={inputStyle} value={startTime.slice(0, 5)} onChange={(ev) => setStartTime(ev.target.value)} />}</Field>
        <Field label="Finish">{(id) => <input id={id} type="time" className="n-input" style={inputStyle} value={endTime.slice(0, 5)} onChange={(ev) => setEndTime(ev.target.value)} />}</Field>
      </FormGrid>
      <label style={checkLabel}>
        <input type="checkbox" checked={nextDay} onChange={(ev) => setNextDay(ev.target.checked)} />
        Finishes after midnight
      </label>
      <Field label="Note from the customer">{(id) => <textarea id={id} className="n-input" rows={3} style={inputStyle} value={clientNote} onChange={(ev) => setClientNote(ev.target.value)} />}</Field>
    </FormDialog>
  );
}

// ---------------------------------------------------------------------------
// Record a booking (no minimum spend)
// ---------------------------------------------------------------------------

export function ChannelPicker({ id, value, onChange, channels }: { id?: string; value: StaffChannel; onChange: (c: StaffChannel) => void; channels: string[] }) {
  return (
    <div id={id} role="radiogroup" aria-label="How the customer said yes" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {channels.map((c) => (
        <Button key={c} size="sm" variant={value === c ? 'primary' : 'secondary'} aria-pressed={value === c} onClick={() => onChange(c as StaffChannel)}>
          {channelLabel(c)}
        </Button>
      ))}
    </div>
  );
}

/**
 * The customer confirmed a booking that has no minimum spend (seated dining,
 * a group booking): create its event. Dates, times and venue default to the
 * enquiry's.
 */
export function ConfirmBookingDialog({ meta, detail, onBooked, onClose }: {
  meta: FnMeta;
  detail: EnquiryDetail;
  onBooked: (event: { id: string; event_date?: string | null }, created: boolean) => void;
  onClose: () => void;
}) {
  const e = detail.enquiry;
  const [channel, setChannel] = useState<StaffChannel>('email');
  const [venueId, setVenueId] = useState(e.venue_id ?? (meta.venues.length === 1 ? meta.venues[0].id : ''));
  const [areaId, setAreaId] = useState('');
  const [date, setDate] = useState(e.preferred_date_start ?? '');
  const [startTime, setStartTime] = useState((e.preferred_start_time ?? '').slice(0, 5));
  const [endTime, setEndTime] = useState((e.preferred_end_time ?? '').slice(0, 5));
  const [nextDay, setNextDay] = useState(e.preferred_ends_next_day);
  const [name, setName] = useState(e.subject ?? '');
  const [guests, setGuests] = useState(e.guests === null ? '' : String(e.guests));
  const [note, setNote] = useState('');

  const submit = async () => {
    const res = await fnSend<{ event: { event: EventRow & { id: string } }; created: boolean }>('POST', `/enquiries/${e.id}/confirm-booking`, {
      channel,
      venue_id: venueId || null,
      area_id: areaId || null,
      event_date: date || null,
      start_time: startTime || null,
      end_time: endTime || null,
      ends_next_day: nextDay,
      name: orNull(name),
      guests: intOrNull(guests),
      note: orNull(note),
    });
    onBooked(res.event.event, res.created);
  };

  return (
    <FormDialog title="Record the booking" width={560} submitLabel="Create the event" busyLabel="Booking…" submitDisabled={!venueId || !date} onSubmit={submit} onClose={onClose}>
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        The customer has said yes. This creates the event and marks the enquiry confirmed.
      </div>
      <Field label="How did they confirm?">{(id) => <ChannelPicker id={id} value={channel} onChange={setChannel} channels={meta.staff_channels} />}</Field>
      <FormGrid>
        <Field label="Venue">
          {(id) => (
            <select id={id} className="n-select" style={inputStyle} value={venueId} onChange={(ev) => { setVenueId(ev.target.value); setAreaId(''); }}>
              {!e.venue_id && <option value="">Choose a venue</option>}
              {meta.venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Area">{(id) => <AreaSelect id={id} venueId={venueId || null} value={areaId} onChange={setAreaId} bookingType={e.booking_type} />}</Field>
      </FormGrid>
      <Field label="Event name">{(id) => <input id={id} className="n-input" style={inputStyle} value={name} onChange={(ev) => setName(ev.target.value)} />}</Field>
      <FormGrid min={130}>
        <Field label="Date">{(id) => <input id={id} type="date" className="n-input" style={inputStyle} value={date} onChange={(ev) => setDate(ev.target.value)} />}</Field>
        <Field label="Start">{(id) => <input id={id} type="time" className="n-input" style={inputStyle} value={startTime} onChange={(ev) => setStartTime(ev.target.value)} />}</Field>
        <Field label="Finish">{(id) => <input id={id} type="time" className="n-input" style={inputStyle} value={endTime} onChange={(ev) => setEndTime(ev.target.value)} />}</Field>
        <Field label="Guests">{(id) => <input id={id} type="number" min={0} inputMode="numeric" className="n-input" style={inputStyle} value={guests} onChange={(ev) => setGuests(ev.target.value)} />}</Field>
      </FormGrid>
      <label style={checkLabel}>
        <input type="checkbox" checked={nextDay} onChange={(ev) => setNextDay(ev.target.checked)} />
        Finishes after midnight
      </label>
      <Field label="Note (optional)">{(id) => <textarea id={id} className="n-input" rows={2} style={inputStyle} value={note} placeholder="e.g. Confirmed by phone with Sam" onChange={(ev) => setNote(ev.target.value)} />}</Field>
    </FormDialog>
  );
}
