'use client';

/**
 * The thread's two record dialogs: link it to an enquiry or an event (search
 * picker), and create an enquiry from it (pre-filled from the customer).
 */
import { useEffect, useState, type ReactNode } from 'react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import { fnGet } from '../shared/api';
import { dateLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { errorText, Notice } from './bits';
import { invalidAddresses } from './text';

interface EnquiryHit {
  id: string;
  subject: string | null;
  contact_name: string | null;
  venue_name: string | null;
  stage: string;
  preferred_date_start: string | null;
}

interface EventHit {
  id: string;
  name: string | null;
  contact_name: string | null;
  venue_name: string | null;
  status: string;
  event_date: string | null;
}

type Kind = 'enquiry' | 'event';
export type LinkTarget = { enquiry_id: string } | { event_id: string };

const STAGE_TONE: Record<string, 'neutral' | 'ok' | 'warn' | 'error' | 'info' | 'accent'> = {
  new: 'accent',
  replied: 'neutral',
  quoted: 'info',
  confirmed: 'ok',
  lost: 'neutral',
  booked: 'neutral',
  sent_to_client: 'info',
  in_progress: 'warn',
  cancelled: 'neutral',
};
const sentence = (s: string) => (s.charAt(0).toUpperCase() + s.slice(1)).replace(/_/g, ' ');

const meta = (parts: (string | null | undefined)[]) => parts.filter(Boolean).join(' · ');

export function LinkPicker({
  initialQuery,
  onPick,
  onClose,
}: {
  initialQuery: string;
  onPick: (target: LinkTarget) => Promise<void>;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<Kind>('enquiry');
  const [q, setQ] = useState(initialQuery);
  const [debounced, setDebounced] = useState(initialQuery);
  const [found, setFound] = useState<{ key: string; rows?: (EnquiryHit | EventHit)[]; error?: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const key = `${kind}|${debounced}`;
  useEffect(() => {
    let alive = true;
    const [k, query] = [key.slice(0, key.indexOf('|')), key.slice(key.indexOf('|') + 1)];
    const req =
      k === 'enquiry'
        ? fnGet<{ rows: EnquiryHit[] }>('/enquiries', { q: query || undefined, limit: 12, sort: 'last_activity' })
        : fnGet<{ rows: EventHit[] }>('/events', { q: query || undefined, limit: 12, sort: 'date_desc' });
    req
      .then((r) => alive && setFound({ key, rows: r.rows }))
      .catch((err) => alive && setFound({ key, error: errorText(err) }));
    return () => {
      alive = false;
    };
  }, [key]);

  const pick = async (target: LinkTarget, id: string) => {
    setBusy(id);
    setError(null);
    try {
      await onPick(target);
      onClose();
    } catch (err) {
      setError(errorText(err));
      setBusy(null);
    }
  };

  const rows = found?.key === key ? found.rows : undefined;
  return (
    <Dialog title="Link to an enquiry or event" width={560} onClose={() => !busy && onClose()} closeButton>
      <Tabs
        label="Record type"
        items={[
          { id: 'enquiry', label: 'Enquiries' },
          { id: 'event', label: 'Events' },
        ]}
        value={kind}
        onChange={(id) => setKind(id as Kind)}
      />
      <input
        className="n-input"
        type="search"
        aria-label={kind === 'enquiry' ? 'Search enquiries' : 'Search events'}
        placeholder={kind === 'enquiry' ? 'Name, email, company or subject' : 'Event or contact name'}
        value={q}
        autoFocus
        onChange={(e) => setQ(e.target.value)}
        style={{ width: '100%', margin: '12px 0 8px' }}
      />
      {error && <Notice tone="error">{error}</Notice>}
      {found?.key === key && found.error ? (
        <PageState kind="error" title="Couldn’t search" detail={found.error} />
      ) : !rows ? (
        <PageState kind="loading" title="Searching…" />
      ) : rows.length === 0 ? (
        <PageState kind="empty" title={kind === 'enquiry' ? 'No enquiries match' : 'No events match'} />
      ) : (
        <ul role="listbox" aria-label="Results" style={{ listStyle: 'none', margin: 0, padding: 0, border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
          {rows.map((r, i) => {
            const isEnquiry = kind === 'enquiry';
            const e = r as EnquiryHit;
            const ev = r as EventHit;
            const title = isEnquiry ? e.subject || e.contact_name || 'Enquiry' : ev.name || ev.contact_name || 'Event';
            const status = isEnquiry ? e.stage : ev.status;
            const sub = isEnquiry
              ? meta([e.contact_name, e.venue_name, e.preferred_date_start ? dateLabel(e.preferred_date_start) : null])
              : meta([ev.contact_name, ev.venue_name, ev.event_date ? dateLabel(ev.event_date) : null]);
            return (
              <li key={r.id} style={{ borderTop: i ? '1px solid var(--line)' : 'none' }}>
                <button
                  type="button"
                  className="n-option"
                  disabled={!!busy}
                  onClick={() => pick(isEnquiry ? { enquiry_id: r.id } : { event_id: r.id }, r.id)}
                  style={{ padding: '8px 12px' }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ flex: 1, minWidth: 0, fontWeight: 600, fontSize: 'var(--fs-base)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {title}
                    </span>
                    <Badge tone={STAGE_TONE[status] ?? 'neutral'}>{sentence(status)}</Badge>
                  </span>
                  {sub && <span style={{ display: 'block', marginTop: 2, color: 'var(--muted)' }}>{busy === r.id ? 'Linking…' : sub}</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Dialog>
  );
}

export function CreateEnquiryDialog({
  meta: fnMeta,
  initial,
  onCreate,
  onClose,
}: {
  meta: FnMeta;
  initial: { name: string; email: string; subject: string; venueId: string | null };
  onCreate: (body: { contact_name?: string; contact_email?: string; subject?: string; venue_id?: string; booking_type?: string }) => Promise<void>;
  onClose: () => void;
}) {
  const [name, setName] = useState(initial.name);
  const [email, setEmail] = useState(initial.email);
  const [subject, setSubject] = useState(initial.subject);
  const [venueId, setVenueId] = useState(initial.venueId ?? '');
  const [bookingType, setBookingType] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const badEmail = email.trim() && invalidAddresses([email.trim()]).length > 0;
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onCreate({
        contact_name: name.trim() || undefined,
        contact_email: email.trim() || undefined,
        subject: subject.trim() || undefined,
        venue_id: venueId || undefined,
        booking_type: bookingType || undefined,
      });
      onClose();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  const field = (label: string, control: ReactNode, help?: string) => (
    <label style={{ display: 'block' }}>
      <span className="n-label">{label}</span>
      {control}
      {help && <span style={{ display: 'block', marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{help}</span>}
    </label>
  );

  return (
    <Dialog title="Create an enquiry from this email" width={480} onClose={() => !busy && onClose()}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {field('Customer name', <input className="n-input" value={name} onChange={(e) => setName(e.target.value)} style={{ width: '100%' }} />)}
        {field(
          'Customer email',
          <input className="n-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} style={{ width: '100%' }} />,
          email.trim() ? 'Matched to an existing contact with this address, if there is one.' : 'Leave blank to use whoever wrote in (a web form’s Reply-To).',
        )}
        {field('Subject', <input className="n-input" value={subject} onChange={(e) => setSubject(e.target.value)} style={{ width: '100%' }} />)}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 180px', minWidth: 0 }}>
            {field(
              'Venue',
              <select className="n-select" value={venueId} onChange={(e) => setVenueId(e.target.value)} style={{ width: '100%' }}>
                {fnMeta.venues.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>,
            )}
          </div>
          <div style={{ flex: '1 1 180px', minWidth: 0 }}>
            {field(
              'Booking type',
              <select className="n-select" value={bookingType} onChange={(e) => setBookingType(e.target.value)} style={{ width: '100%' }}>
                <option value="">Not sure yet</option>
                {fnMeta.booking_types.map((b) => (
                  <option key={b.value} value={b.value}>
                    {b.label}
                  </option>
                ))}
              </select>,
            )}
          </div>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={busy || !!badEmail}>
            {busy ? 'Creating…' : 'Create enquiry'}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
