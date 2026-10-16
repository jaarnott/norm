'use client';

/**
 * One contact: their details and email addresses (edit, primary, add,
 * remove), what came of their enquiries, their enquiries and events (each
 * opens its own page), archive, delete when nothing hangs off them, and —
 * for owners — anonymise for a privacy request, which can't be undone.
 */
import { useState } from 'react';
import type { CSSProperties } from 'react';
import { Archive, ArchiveRestore, Pencil, Plus, ShieldX, Star, Trash2, X } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import { ConfirmDialog } from '../../ui/Dialog';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import { fnSend } from '../shared/api';
import { dateLabel, instantLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import {
  DoneNote, ErrorNote, Field, FnHeader, FormDialog, inputStyle, Muted, READ_ONLY_HINT, Section, StatTile, TileRow, useAction, useFnQuery, useWidth,
} from '../enquiries/bits';
import { EditContactDialog } from '../enquiries/EnquiryDialogs';
import { dayOf, displayName, eventStatusBadge, moneyAuto, pctLabel, stageLabel, stageTone } from '../enquiries/labels';
import { openFnPage } from '../enquiries/nav';
import type { Contact, ContactDetail as Detail } from '../enquiries/types';

interface AnonymiseResult {
  emails_removed: number;
  enquiries_scrubbed: number;
  events_scrubbed: number;
  form_submissions_scrubbed: number;
  portal_access_removed: number;
  mail_threads_purged: number;
  mail_messages_purged: number;
}

type Dialog = 'edit' | 'add-email' | 'archive' | 'delete' | 'anonymise' | { removeEmail: string } | null;

export default function ContactDetail({ id, meta, page, onBack }: { id: string; meta: FnMeta; page: boolean; onBack: () => void }) {
  const query = useFnQuery<Detail>(`/contacts/${id}`);
  const detail = query.data;
  const load = query.retry;
  const [dialog, setDialog] = useState<Dialog>(null);
  const [anonymised, setAnonymised] = useState<AnonymiseResult | null>(null);
  const { busy, error, setError, run } = useAction();
  const [boxRef, width] = useWidth();
  const narrow = width > 0 && width < 720;
  const tz = meta.company.default_timezone;
  const currency = meta.company.currency;
  const canWrite = meta.can_write;
  const writeTitle = canWrite ? undefined : READ_ONLY_HINT;


  const back = { label: 'Back to contacts', onClick: onBack };
  if (query.error && !detail) {
    const missing = query.status === 404;
    return (
      <>
        <FnHeader page={page} title="Contact" back={back} />
        <PageState
          kind="error"
          title={missing ? 'This contact isn’t available' : 'Couldn’t load this contact'}
          detail={missing ? 'They may have been deleted, or only have enquiries at venues you don’t have access to.' : query.error}
          action={missing ? undefined : <Button size="sm" onClick={query.retry}>Try again</Button>}
        />
      </>
    );
  }
  if (!detail) {
    return (
      <>
        <FnHeader page={page} title="Contact" back={back} />
        <PageState kind="loading" title="Loading contact…" />
      </>
    );
  }

  const c = detail;
  const isAnon = !!c.anonymised_at;
  const counts = c.counts;
  const deletable = counts.enquiries === 0 && counts.events === 0;
  // A write that returns the contact (without its enquiries) → re-read the page.
  const after = (r: unknown) => { if (r !== undefined) load(); };

  const header = (
    <FnHeader
      page={page}
      back={back}
      title={displayName(c.name, isAnon ? 'Anonymised contact' : 'Unnamed contact')}
      status={<>{c.archived_at && <Badge>Archived</Badge>}{isAnon && <Badge>Anonymised</Badge>}</>}
      meta={[c.company, `in Norm since ${dayOf(c.created_at, tz)}`].filter(Boolean).join(' · ')}
      actions={
        <>
          {!isAnon && (
            <Button icon={Plus} onClick={() => openFnPage('enquiries', { new: 1, contact: c.id })} disabled={!canWrite} title={writeTitle}>New enquiry</Button>
          )}
          {c.archived_at ? (
            <Button variant="quiet" icon={ArchiveRestore} disabled={!canWrite || !!busy} title={writeTitle}
              onClick={() => run('unarchive', () => fnSend<Contact>('POST', `/contacts/${c.id}/unarchive`)).then(after)}>Unarchive</Button>
          ) : (
            <Button variant="quiet" icon={Archive} disabled={!canWrite} title={writeTitle} onClick={() => setDialog('archive')}>Archive</Button>
          )}
          {deletable && (
            <Button variant="quiet" icon={Trash2} disabled={!canWrite} title={writeTitle} onClick={() => setDialog('delete')}>Delete</Button>
          )}
          {meta.user.is_owner && !isAnon && (
            <Button variant="danger" icon={ShieldX} disabled={!canWrite} title={writeTitle ?? 'Remove their personal details (privacy request)'} onClick={() => setDialog('anonymise')} data-testid="fn-contact-anonymise">
              Anonymise
            </Button>
          )}
        </>
      }
    />
  );

  const emails = c.emails ?? [];
  const details = (
    <Section
      title="Details"
      actions={!isAnon ? <Button size="sm" variant="quiet" icon={Pencil} onClick={() => setDialog('edit')} disabled={!canWrite} title={writeTitle}>Edit</Button> : undefined}
    >
      <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: narrow ? '1fr' : 'minmax(110px, max-content) 1fr', gap: narrow ? '2px' : '8px 16px', fontSize: 'var(--fs-base)' }}>
        <dt style={dt}>Company</dt><dd style={dd}>{c.company || <Muted>—</Muted>}</dd>
        <dt style={dt}>Phone</dt><dd style={dd}>{c.phone ? <a href={`tel:${c.phone.replace(/\s+/g, '')}`} style={{ color: 'var(--accent)' }}>{c.phone}</a> : <Muted>—</Muted>}</dd>
        <dt style={dt}>Email</dt>
        <dd style={dd}>
          {emails.length === 0 && <Muted>None on record</Muted>}
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
            {emails.map((em) => (
              <li key={em.email} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <a href={`mailto:${em.email}`} style={{ color: 'var(--accent)', overflowWrap: 'anywhere' }}>{em.email}</a>
                {em.is_primary && emails.length > 1 && <Badge tone="info">Primary</Badge>}
                {canWrite && !em.is_primary && (
                  <IconButton icon={Star} label={`Make ${em.email} the primary address`} iconSize={14}
                    onClick={() => run('primary', () => fnSend<Contact>('PUT', `/contacts/${c.id}/emails/primary`, { email: em.email })).then(after)} />
                )}
                {canWrite && <IconButton icon={X} label={`Remove ${em.email}`} iconSize={14} onClick={() => setDialog({ removeEmail: em.email })} />}
              </li>
            ))}
          </ul>
          {canWrite && !isAnon && (
            <div style={{ marginTop: 6 }}><Button size="sm" variant="quiet" icon={Plus} onClick={() => setDialog('add-email')}>Add email</Button></div>
          )}
        </dd>
        <dt style={dt}>Notes</dt><dd style={{ ...dd, whiteSpace: 'pre-wrap' }}>{c.notes || <Muted>—</Muted>}</dd>
      </dl>
      <ErrorNote error={error} onReload={() => load()} onDismiss={() => setError(null)} />
    </Section>
  );

  const enquiries = (
    <Section title="Enquiries" meta={`${c.enquiries.length}`} padded={c.enquiries.length === 0}>
      {c.enquiries.length === 0 ? <Muted>No enquiries.</Muted> : narrow ? (
        <ul style={listReset}>
          {c.enquiries.map((e) => (
            <li key={e.id}>
              <button type="button" className="n-row" onClick={() => openFnPage('enquiries', { enquiry: e.id })} style={{ alignItems: 'flex-start', borderRadius: 0, padding: '10px 16px' }}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block' }}>{e.subject || 'No subject'}</span>
                  <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontWeight: 400 }}>
                    {[e.venue_name || 'Group-wide', e.preferred_date_start ? dateLabel(e.preferred_date_start) : null, `received ${dayOf(e.created_at, tz)}`].filter(Boolean).join(' · ')}
                  </span>
                </span>
                <Badge tone={stageTone(e.stage)}>{stageLabel(e.stage)}</Badge>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <table className="n-table" aria-label="Their enquiries">
          <thead>
            <tr>
              <th scope="col">Enquiry</th>
              <th scope="col">Venue</th>
              <th scope="col">Event date</th>
              <th scope="col">Received</th>
              <th scope="col" className="num">Value</th>
              <th scope="col">Stage</th>
            </tr>
          </thead>
          <tbody>
            {c.enquiries.map((e) => (
              <tr key={e.id} onClick={() => openFnPage('enquiries', { enquiry: e.id })} style={{ cursor: 'pointer' }}>
                <td>
                  <button type="button" onClick={(ev) => { ev.stopPropagation(); openFnPage('enquiries', { enquiry: e.id }); }} style={linkCell}>{e.subject || 'No subject'}</button>
                  {e.archived_at && <span style={{ marginLeft: 6 }}><Badge>Archived</Badge></span>}
                </td>
                <td style={{ color: 'var(--text-soft)' }}>{e.venue_name || 'Group-wide'}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{e.preferred_date_start ? dateLabel(e.preferred_date_start) : '—'}</td>
                <td style={{ whiteSpace: 'nowrap', color: 'var(--text-soft)' }}>{dayOf(e.created_at, tz)}</td>
                <td className="num">{e.value_cents ? moneyAuto(e.value_cents, currency) : '—'}</td>
                <td><Badge tone={stageTone(e.stage)}>{stageLabel(e.stage)}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );

  const events = (
    <Section title="Events" meta={`${c.events.length}`} padded={c.events.length === 0}>
      {c.events.length === 0 ? <Muted>No events.</Muted> : narrow ? (
        <ul style={listReset}>
          {c.events.map((ev) => {
            const st = eventStatusBadge(ev.status);
            return (
              <li key={ev.id}>
                <button type="button" className="n-row" onClick={() => openFnPage('events', { event: ev.id })} style={{ alignItems: 'flex-start', borderRadius: 0, padding: '10px 16px' }}>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: 'block' }}>{ev.name || 'Event'}</span>
                    <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontWeight: 400 }}>
                      {[dateLabel(ev.event_date), ev.venue_name, moneyAuto(ev.expected_spend_cents, currency)].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  <Badge tone={st.tone}>{st.label}</Badge>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <table className="n-table" aria-label="Their events">
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Event</th>
              <th scope="col">Venue</th>
              <th scope="col" className="num">Guests</th>
              <th scope="col" className="num">Expected</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {c.events.map((ev) => {
              const st = eventStatusBadge(ev.status);
              return (
                <tr key={ev.id} onClick={() => openFnPage('events', { event: ev.id })} style={{ cursor: 'pointer' }}>
                  <td style={{ whiteSpace: 'nowrap' }}>{dateLabel(ev.event_date)}</td>
                  <td><button type="button" onClick={(e) => { e.stopPropagation(); openFnPage('events', { event: ev.id }); }} style={linkCell}>{ev.name || 'Event'}</button></td>
                  <td style={{ color: 'var(--text-soft)' }}>{ev.venue_name}</td>
                  <td className="num">{ev.guests ?? '—'}</td>
                  <td className="num">{moneyAuto(ev.expected_spend_cents, currency)}</td>
                  <td><Badge tone={st.tone}>{st.label}</Badge></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Section>
  );

  return (
    <div ref={boxRef}>
      {header}
      {anonymised && (
        <DoneNote>
          Anonymised. Removed {anonymised.emails_removed} email {anonymised.emails_removed === 1 ? 'address' : 'addresses'}; scrubbed {anonymised.enquiries_scrubbed} {anonymised.enquiries_scrubbed === 1 ? 'enquiry' : 'enquiries'} and {anonymised.events_scrubbed} {anonymised.events_scrubbed === 1 ? 'event' : 'events'}; removed {anonymised.portal_access_removed} portal {anonymised.portal_access_removed === 1 ? 'access' : 'accesses'}; purged {anonymised.mail_threads_purged} mail {anonymised.mail_threads_purged === 1 ? 'thread' : 'threads'} ({anonymised.mail_messages_purged} messages) from Norm.
        </DoneNote>
      )}
      <TileRow min={130}>
        <StatTile label="Enquiries" value={counts.enquiries} />
        <StatTile label="Open" value={counts.open} />
        <StatTile label="Confirmed" value={counts.confirmed} />
        <StatTile label="Lost" value={counts.lost} />
        <StatTile label="Events" value={counts.events} />
        <StatTile label="Conversion" value={pctLabel(counts.conversion_pct)} sub="Confirmed of closed" />
      </TileRow>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
        {details}
        {enquiries}
        {events}
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          {c.anonymised_at ? `Anonymised ${instantLabel(c.anonymised_at, tz, true)}. ` : ''}
          {c.archived_at ? `Archived ${instantLabel(c.archived_at, tz, true)}.` : ''}
        </div>
      </div>

      {dialog === 'edit' && <EditContactDialog contact={c} onSaved={() => load()} onClose={() => setDialog(null)} onReload={load} />}
      {dialog === 'add-email' && <AddEmailDialog contactId={c.id} onSaved={() => load()} onClose={() => setDialog(null)} />}
      {dialog && typeof dialog === 'object' && (
        <ConfirmDialog
          title="Remove this email address?"
          confirmLabel="Remove"
          busyLabel="Removing…"
          danger
          onConfirm={async () => { await fnSend('DELETE', `/contacts/${c.id}/emails?email=${encodeURIComponent(dialog.removeEmail)}`); load(); }}
          onClose={() => setDialog(null)}
        >
          {dialog.removeEmail} comes off this contact. Mail from it will no longer match them.
        </ConfirmDialog>
      )}
      {dialog === 'archive' && (
        <ConfirmDialog
          title="Archive this contact?"
          confirmLabel="Archive"
          busyLabel="Archiving…"
          onConfirm={async () => { await fnSend('POST', `/contacts/${c.id}/archive`); load(); }}
          onClose={() => setDialog(null)}
        >
          They’re hidden from the contacts list (tick “Include archived” to see them). Their enquiries and events are unchanged.
        </ConfirmDialog>
      )}
      {dialog === 'delete' && (
        <ConfirmDialog
          title="Delete this contact?"
          confirmLabel="Delete"
          busyLabel="Deleting…"
          danger
          onConfirm={async () => { await fnSend('DELETE', `/contacts/${c.id}`); onBack(); }}
          onClose={() => setDialog(null)}
        >
          They have no enquiries or events, so nothing else changes. This can’t be undone.
        </ConfirmDialog>
      )}
      {dialog === 'anonymise' && (
        <AnonymiseDialog
          contact={c}
          onDone={(r) => { setAnonymised(r); load(); }}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}

const dt: CSSProperties = { color: 'var(--muted)', fontSize: 'var(--fs-sm)' };
const dd: CSSProperties = { margin: 0, minWidth: 0, overflowWrap: 'anywhere' };
const listReset: CSSProperties = { listStyle: 'none', margin: 0, padding: 0 };
const linkCell: CSSProperties = {
  padding: 0, border: 'none', background: 'none', font: 'inherit', color: 'var(--text)', fontWeight: 500, textAlign: 'left', cursor: 'pointer',
};

function AddEmailDialog({ contactId, onSaved, onClose }: { contactId: string; onSaved: () => void; onClose: () => void }) {
  const [email, setEmail] = useState('');
  const [primary, setPrimary] = useState(false);
  return (
    <FormDialog
      title="Add an email address"
      submitLabel="Add"
      submitDisabled={!email.includes('@')}
      onSubmit={async () => { await fnSend('POST', `/contacts/${contactId}/emails`, { email: email.trim(), primary }); onSaved(); }}
      onClose={onClose}
    >
      <Field label="Email">{(id) => <input id={id} type="email" className="n-input" style={inputStyle} value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />}</Field>
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        <input type="checkbox" checked={primary} onChange={(e) => setPrimary(e.target.checked)} />
        Make it their primary address
      </label>
    </FormDialog>
  );
}

/**
 * Anonymise for a deletion or correction request — owners only, server-side
 * too. Spelled out before anything happens, and the button stays disabled
 * until the owner ticks that they understand it's permanent.
 */
function AnonymiseDialog({ contact, onDone, onClose }: { contact: Detail; onDone: (r: AnonymiseResult) => void; onClose: () => void }) {
  const [sure, setSure] = useState(false);
  return (
    <FormDialog
      title={`Anonymise ${displayName(contact.name)}?`}
      width={540}
      submitLabel="Anonymise permanently"
      busyLabel="Anonymising…"
      danger
      submitDisabled={!sure}
      onSubmit={async () => onDone(await fnSend<AnonymiseResult>('POST', `/contacts/${contact.id}/anonymise`, { confirm: true }))}
      onClose={onClose}
    >
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <p style={{ margin: 0 }}>For a privacy request. Norm will:</p>
        <ul style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <li>blank their name, company, phone and notes, and delete their email addresses;</li>
          <li>scrub what was written about them on their {contact.counts.enquiries} {contact.counts.enquiries === 1 ? 'enquiry' : 'enquiries'} and {contact.counts.events} {contact.counts.events === 1 ? 'event' : 'events'} — subjects, notes, their own note, allergies, bar-tab names, the table plan;</li>
          <li>remove their client-portal access, their web-form details and the click ids from their enquiries;</li>
          <li>delete Norm’s copy of every mail thread with their addresses (the mail stays in the venue’s Gmail).</li>
        </ul>
        <p style={{ margin: 0 }}>Amounts, dates, guest numbers, menus and every booking and status stay, so reports still add up. <strong style={{ fontWeight: 600, color: 'var(--text)' }}>This can’t be undone.</strong></p>
      </div>
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
        <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} data-testid="fn-anonymise-sure" />
        I understand their details are removed for good.
      </label>
    </FormDialog>
  );
}
