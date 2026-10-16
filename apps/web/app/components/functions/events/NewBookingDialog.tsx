'use client';

/**
 * A booking entered directly — seated dining and group bookings only. Large
 * events (minimum-spend types) are booked when the customer confirms an
 * option on the enquiry, so they aren't offered here (the API refuses them).
 */
import { useEffect, useId, useState } from 'react';
import { Search, UserPlus } from 'lucide-react';
import Dialog from '../../ui/Dialog';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import { fnGet, fnSend } from '../shared/api';
import type { FnMeta } from '../shared/meta';
import { endsNextDay, normaliseTime, textOrNull } from './logic';
import { useLoad, errorText } from './useLoad';
import { Field, FieldGrid, inputStyle } from './ui';
import type { Area, Contact, EventDetail } from './types';

interface ContactHit extends Contact {
  counts?: { enquiries: number; events: number };
}

export default function NewBookingDialog({ meta, defaultVenueId, today, onCreated, onClose }: {
  meta: FnMeta;
  defaultVenueId: string | null;
  today: string;
  onCreated: (eventId: string) => void;
  onClose: () => void;
}) {
  const f = {
    venue: useId(), type: useId(), name: useId(), date: useId(), start: useId(), end: useId(), guests: useId(), area: useId(),
    search: useId(), cName: useId(), cEmail: useId(), cPhone: useId(), cCompany: useId(), note: useId(),
  };
  const types = meta.booking_types.filter((b) => !b.min_spend);
  const usable = meta.venues.filter((v) => v.timezone_valid);
  const [venueId, setVenueId] = useState(defaultVenueId && usable.some((v) => v.id === defaultVenueId) ? defaultVenueId : usable[0]?.id ?? '');
  const [bookingType, setBookingType] = useState(types[0]?.value ?? '');
  const [name, setName] = useState('');
  const [date, setDate] = useState(today);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [guests, setGuests] = useState('');
  const [areaId, setAreaId] = useState('');
  const [note, setNote] = useState('');
  // Contact: pick an existing one, or type a new one (matched by email server-side).
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [picked, setPicked] = useState<ContactHit | null>(null);
  const [adding, setAdding] = useState(false);
  const [contact, setContact] = useState({ name: '', email: '', phone: '', company: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  const hits = useLoad(debounced.length >= 2 && !picked ? `contacts:${debounced}` : null, () =>
    fnGet<{ rows: ContactHit[] }>('/contacts', { q: debounced, limit: 8 }),
  );
  const profile = useLoad(venueId ? `profile:${venueId}` : null, () => fnGet<{ areas: Area[] }>(`/venues/${venueId}/profile`));
  const areas = (profile.data?.areas ?? []).filter((a) => a.active && (!a.booking_types.length || a.booking_types.includes(bookingType)));

  const create = async () => {
    if (!venueId || !date || !bookingType) {
      setError('Choose a venue, a booking type and a date.');
      return;
    }
    if (!picked && !(adding && contact.name.trim() && contact.email.trim())) {
      setError('Choose the contact, or add a new one with a name and email.');
      return;
    }
    const s = normaliseTime(start);
    const e = normaliseTime(end);
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        venue_id: venueId,
        booking_type: bookingType,
        event_date: date,
        name: textOrNull(name),
        start_time: s,
        end_time: e,
        ends_next_day: endsNextDay(s, e),
        guests: guests.trim() ? Number(guests) : null,
        area_id: areaId || null,
        note: textOrNull(note),
      };
      if (picked) body.contact_id = picked.id;
      else body.contact = { name: contact.name.trim(), email: contact.email.trim(), phone: textOrNull(contact.phone), company: textOrNull(contact.company) };
      for (const k of Object.keys(body)) if (body[k] === null) delete body[k];
      const created = await fnSend<EventDetail>('POST', '/events', body);
      onCreated(created.event.id);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <Dialog title="New booking" width={620} onClose={() => { if (!busy) onClose(); }} closeButton>
      <p style={{ marginTop: 0, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        For seated dining and group bookings. Large events are booked when the customer confirms a minimum-spend option on the enquiry.
      </p>

      <h3 className="n-eyebrow" style={{ margin: '4px 0 8px' }}>Contact</h3>
      {picked ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', border: '1px solid var(--line)', borderRadius: 'var(--radius)', marginBottom: 12 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontWeight: 600 }}>{picked.name || '(no name)'}{picked.company ? <span style={{ fontWeight: 400, color: 'var(--muted)' }}> · {picked.company}</span> : null}</div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{picked.email || picked.phone || '—'}</div>
          </div>
          <Button size="sm" onClick={() => { setPicked(null); setQuery(''); }}>Change</Button>
        </div>
      ) : adding ? (
        <div style={{ marginBottom: 12 }}>
          <FieldGrid>
            <Field label="Name" htmlFor={f.cName}><input id={f.cName} className="n-input" style={inputStyle} value={contact.name} onChange={(e) => setContact({ ...contact, name: e.target.value })} /></Field>
            <Field label="Email" htmlFor={f.cEmail}><input id={f.cEmail} type="email" className="n-input" style={inputStyle} value={contact.email} onChange={(e) => setContact({ ...contact, email: e.target.value })} /></Field>
            <Field label="Phone (optional)" htmlFor={f.cPhone}><input id={f.cPhone} className="n-input" style={inputStyle} value={contact.phone} onChange={(e) => setContact({ ...contact, phone: e.target.value })} /></Field>
            <Field label="Company (optional)" htmlFor={f.cCompany}><input id={f.cCompany} className="n-input" style={inputStyle} value={contact.company} onChange={(e) => setContact({ ...contact, company: e.target.value })} /></Field>
          </FieldGrid>
          <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>An existing contact with this email is used instead of creating a second one.</div>
          <Button variant="link" onClick={() => setAdding(false)} style={{ marginTop: 6, fontSize: 'var(--fs-sm)' }}>Search existing contacts instead</Button>
        </div>
      ) : (
        <div style={{ marginBottom: 12 }}>
          <label className="n-label" htmlFor={f.search}>Find a contact</label>
          <span style={{ position: 'relative', display: 'block' }}>
            <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
            <input id={f.search} className="n-input" style={{ ...inputStyle, paddingLeft: 32 }} value={query} placeholder="Name, company, email or phone"
              onChange={(e) => setQuery(e.target.value)} autoComplete="off" />
          </span>
          {debounced.length >= 2 && (
            <div className="n-card" style={{ marginTop: 4, maxHeight: 220, overflowY: 'auto', padding: 4 }}>
              {hits.loading && <div style={{ padding: 8, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Searching…</div>}
              {hits.error !== undefined && <div style={{ padding: 8, fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{errorText(hits.error)}</div>}
              {hits.data && !hits.data.rows.length && <div style={{ padding: 8, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No matching contacts.</div>}
              {hits.data?.rows.map((c) => (
                <button key={c.id} type="button" className="n-option" style={{ borderRadius: 'var(--radius-sm)' }} onClick={() => setPicked(c)}>
                  <div style={{ fontWeight: 600 }}>{c.name || '(no name)'}{c.company ? <span style={{ fontWeight: 400, color: 'var(--muted)' }}> · {c.company}</span> : null}</div>
                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{c.email || c.phone || '—'}</div>
                </button>
              ))}
            </div>
          )}
          <Button size="sm" icon={UserPlus} onClick={() => setAdding(true)} style={{ marginTop: 8 }}>Add a new contact</Button>
        </div>
      )}

      <h3 className="n-eyebrow" style={{ margin: '8px 0 8px' }}>Booking</h3>
      <FieldGrid>
        <Field label="Venue" htmlFor={f.venue}>
          <select id={f.venue} className="n-select" style={inputStyle} value={venueId} onChange={(e) => { setVenueId(e.target.value); setAreaId(''); }}>
            {usable.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
        </Field>
        <Field label="Booking type" htmlFor={f.type}>
          <select id={f.type} className="n-select" style={inputStyle} value={bookingType} onChange={(e) => { setBookingType(e.target.value); setAreaId(''); }}>
            {types.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </Field>
        <Field label="Event name (optional)" htmlFor={f.name} wide>
          <input id={f.name} className="n-input" style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Sam’s 30th" />
        </Field>
        <Field label="Date" htmlFor={f.date}>
          <input id={f.date} type="date" className="n-input" style={inputStyle} value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
        <Field label="Guests" htmlFor={f.guests}>
          <input id={f.guests} type="number" min={0} inputMode="numeric" className="n-input" style={inputStyle} value={guests} onChange={(e) => setGuests(e.target.value)} />
        </Field>
        <Field label="Start" htmlFor={f.start}>
          <input id={f.start} type="time" className="n-input" style={inputStyle} value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="Finish" htmlFor={f.end} hint={endsNextDay(start, end) ? 'Finishes after midnight (next day)' : undefined}>
          <input id={f.end} type="time" className="n-input" style={inputStyle} value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        {areas.length > 0 && (
          <Field label="Area (optional)" htmlFor={f.area} wide>
            <select id={f.area} className="n-select" style={inputStyle} value={areaId} onChange={(e) => setAreaId(e.target.value)}>
              <option value="">No particular area</option>
              {areas.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </Field>
        )}
        <Field label="Note for the log (optional)" htmlFor={f.note} wide>
          <input id={f.note} className="n-input" style={inputStyle} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Booked by phone" />
        </Field>
      </FieldGrid>

      {error && (
        <div role="alert" style={{ marginTop: 12, padding: '8px 12px', fontSize: 'var(--fs-sm)', color: 'var(--error)', background: 'var(--error-bg)', borderRadius: 'var(--radius)' }}>
          {error}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 18 }}>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={create} disabled={busy} aria-busy={busy || undefined}>{busy ? 'Booking…' : 'Create booking'}</Button>
      </div>
    </Dialog>
  );
}
