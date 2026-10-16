'use client';

/**
 * One enquiry, as a sub-page of Enquiries (`?enquiry=<id>`): the header with
 * its stage and the close / reopen / archive actions, what's due next, then
 * the event details, minimum-spend options, food pre-order and activity —
 * with the contact, bookings, proposal, linked mail, follow-up and notes
 * beside them (under them on a phone). Everything comes from
 * `GET /api/functions/enquiries/{id}` and every change re-reads it.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import {
  Archive, ArchiveRestore, CalendarCheck, CalendarDays, CalendarX, Check, CircleCheck, CircleDollarSign, CircleX, Copy, Eye,
  FileText, History, Link2, Link2Off, ListChecks, Mail, Pencil, Plus, RotateCcw, Send, ShieldAlert, ShieldCheck, ShieldOff, Unlink, Users, X,
  type LucideIcon,
} from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import { ConfirmDialog } from '../../ui/Dialog';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import { fnGet, fnSend, FnApiError } from '../shared/api';
import { dateLabel, instantLabel, relativeLabel, timeRangeLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import {
  DoneNote, ErrorNote, Fields, FnHeader, Muted, Popover, READ_ONLY_HINT, Section, useAction, useWidth,
} from './bits';
import { ConfirmBookingDialog, EditContactDialog, EditEnquiryDialog, FollowUpDialog, ReasonDialog } from './EnquiryDialogs';
import {
  dateRangeLabel, dayOf, decodeEntities, detailText, displayName, dueBadge, eventStatusBadge, moneyAuto, sentence,
  sourceLabel, stageLabel, stageTone,
} from './labels';
import MenusSection from './MenusSection';
import { mailThreadParams, openFnPage } from './nav';
import OptionsSection from './OptionsSection';
import type { Contact, EnquiryDetail as Detail, TimelineItem } from './types';

const TWO_COLUMNS_FROM = 900;

type Dialog = 'edit' | 'close' | 'reopen' | 'archive' | 'follow-up' | 'contact' | 'book' | null;

export default function EnquiryDetail({ id, meta, page, onBack, onOpenEnquiry }: {
  id: string;
  meta: FnMeta;
  page: boolean;
  onBack: () => void;
  /** Open another enquiry (from the contact's history) in place. */
  onOpenEnquiry: (id: string) => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; missing: boolean } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [booked, setBooked] = useState<{ eventId: string; created: boolean } | null>(null);
  const { busy, error, setError, run } = useAction();
  const [boxRef, width] = useWidth();
  const wide = width >= TWO_COLUMNS_FROM;

  const load = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true);
    try {
      const d = await fnGet<Detail>(`/enquiries/${id}`);
      setDetail(d);
      setLoadError(null);
    } catch (e) {
      const missing = e instanceof FnApiError && e.status === 404;
      if (!quiet || missing) setLoadError({ message: e instanceof Error ? e.message : String(e), missing });
    } finally {
      setRefreshing(false);
    }
  }, [id]);

  // The page keys this component by enquiry id, so a fresh mount = a fresh load.
  useEffect(() => { void load(); }, [load]);

  const quietReload = useCallback(() => { void load(true); }, [load]);
  const back = { label: 'Back to enquiries', onClick: onBack };

  if (loadError && !detail) {
    return (
      <>
        <FnHeader page={page} title="Enquiry" back={back} />
        <PageState
          kind="error"
          title={loadError.missing ? 'This enquiry isn’t available' : 'Couldn’t load this enquiry'}
          detail={loadError.missing ? 'It may have been removed, or it’s at a venue you don’t have access to.' : loadError.message}
          action={loadError.missing ? undefined : <Button size="sm" onClick={() => void load()}>Try again</Button>}
        />
      </>
    );
  }
  if (!detail) {
    return (
      <>
        <FnHeader page={page} title="Enquiry" back={back} />
        <PageState kind="loading" title="Loading enquiry…" />
      </>
    );
  }

  const e = detail.enquiry;
  const tz = detail.venue?.timezone || meta.company.default_timezone;
  const canWrite = meta.can_write;
  const lost = detail.stage === 'lost';
  const liveEvent = detail.events.find((ev) => !ev.cancelled_at) ?? null;
  const latestClosure = detail.closures.length ? detail.closures[detail.closures.length - 1] : null;
  const bookingType = meta.booking_types.find((b) => b.value === e.booking_type);
  const canBook = !liveEvent && !lost && !bookingType?.min_spend && !detail.options.some((o) => !o.withdrawn_at);

  const setFromWrite = (d: Detail) => { setDetail(d); setError(null); };
  const writeTitle = canWrite ? undefined : READ_ONLY_HINT;

  const header = (
    <FnHeader
      page={page}
      back={back}
      title={e.subject || displayName(detail.contact?.name, 'Enquiry')}
      status={
        <>
          <Badge tone={stageTone(detail.stage)}>{stageLabel(detail.stage)}</Badge>
          {e.archived_at && <Badge>Archived</Badge>}
          <RepeatContact detail={detail} tz={tz} onOpenEnquiry={onOpenEnquiry} />
          {refreshing && <Muted>Updating…</Muted>}
        </>
      }
      meta={[
        e.booking_label || (e.booking_type ? sentence(e.booking_type) : 'Booking type not set'),
        detail.venue?.name ?? 'Group-wide',
        `received ${dayOf(e.created_at, tz)}`,
        e.source ? `via ${sourceLabel(e.source).toLowerCase()}` : null,
      ].filter(Boolean).join(' · ')}
      actions={
        <>
          {!lost && !liveEvent && (
            <Button icon={X} onClick={() => setDialog('close')} disabled={!canWrite} title={writeTitle ?? 'Close as lost'} data-testid="fn-enquiry-close">Close</Button>
          )}
          {lost && (
            <Button icon={RotateCcw} onClick={() => setDialog('reopen')} disabled={!canWrite} title={writeTitle} data-testid="fn-enquiry-reopen">Reopen</Button>
          )}
          {e.archived_at ? (
            <Button variant="quiet" icon={ArchiveRestore} disabled={!canWrite || !!busy} title={writeTitle}
              onClick={() => run('unarchive', () => fnSend<Detail>('POST', `/enquiries/${e.id}/unarchive`)).then((d) => { if (d) setFromWrite(d); })}>
              Unarchive
            </Button>
          ) : (
            <Button variant="quiet" icon={Archive} onClick={() => setDialog('archive')} disabled={!canWrite} title={writeTitle}>Archive</Button>
          )}
        </>
      }
    />
  );

  // ---- the band under the header: what's due, why it's closed, or booked ----
  let band: ReactNode = null;
  if (lost && latestClosure) {
    band = (
      <Band tone="muted">
        <span style={{ flex: 1, minWidth: 0 }}>
          <strong style={{ fontWeight: 600 }}>Closed as lost</strong>
          {latestClosure.reason ? ` — ${latestClosure.reason}` : ''}
          <span style={{ display: 'block', marginTop: 2 }}>
            <Muted>{[latestClosure.actor_label, instantLabel(latestClosure.occurred_at, tz)].filter(Boolean).join(' · ')}</Muted>
          </span>
        </span>
      </Band>
    );
  } else if (liveEvent) {
    const st = eventStatusBadge(liveEvent.status);
    band = (
      <Band tone="ok">
        <span style={{ flex: 1, minWidth: 0 }}>
          <strong style={{ fontWeight: 600 }}>Booked</strong> for {dateLabel(liveEvent.event_date)}
          {liveEvent.start_time ? ` · ${timeRangeLabel(liveEvent.start_time, liveEvent.end_time, liveEvent.ends_next_day)}` : ''}
          {' '}<Badge tone={st.tone}>{st.label}</Badge>
        </span>
        <Button size="sm" icon={CalendarCheck} onClick={() => openFnPage('events', { event: liveEvent.id })}>Open the event</Button>
      </Band>
    );
  } else if (detail.next_action && detail.next_action.kind !== 'none') {
    const due = dueBadge(detail.next_action);
    band = (
      <Band tone={detail.next_action.overdue || detail.next_action.due_today ? 'accent' : 'muted'}>
        <span style={{ flex: 1, minWidth: 0 }}>
          <strong style={{ fontWeight: 600 }}>Next: {detail.next_action.label}</strong>
          {due && <> <Badge tone={due.tone}>{due.label}</Badge></>}
          {detail.next_action.why && <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{detail.next_action.why}</span>}
        </span>
      </Band>
    );
  } else if (detail.next_action?.why) {
    band = <Band tone="muted"><Muted>{detail.next_action.why}</Muted></Band>;
  }

  // ---- sections ----
  const details = (
    <Section
      title="Event details"
      actions={<Button size="sm" variant="quiet" icon={Pencil} onClick={() => setDialog('edit')} disabled={!canWrite} title={writeTitle}>Edit</Button>}
    >
      <Fields
        items={[
          { label: 'Venue', value: detail.venue?.name ?? <Muted>Not decided (group-wide)</Muted> },
          { label: 'Booking type', value: e.booking_label || (e.booking_type ? sentence(e.booking_type) : <Muted>Not set</Muted>) },
          { label: 'Preferred date', value: dateRangeLabel(e.preferred_date_start, e.preferred_date_end) },
          { label: 'Time', value: timeRangeLabel(e.preferred_start_time, e.preferred_end_time, e.preferred_ends_next_day) || <Muted>Not given</Muted> },
          { label: 'Guests', value: e.guests ?? <Muted>Not given</Muted> },
          { label: 'Value', value: detail.value_cents ? moneyAuto(detail.value_cents, meta.company.currency) : <Muted>—</Muted> },
          { label: 'Owner', value: detail.owner_name ?? <Muted>Nobody</Muted> },
          { label: 'Source', value: sourceLabel(e.source) },
        ]}
      />
      {e.client_note && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 2 }}>Note from the customer</div>
          <div style={{ fontSize: 'var(--fs-base)', whiteSpace: 'pre-wrap' }}>{e.client_note}</div>
        </div>
      )}
      <Attribution attribution={e.attribution} />
      <div style={{ marginTop: 10, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
        Created {instantLabel(e.created_at, tz, true)}{e.created_by_label ? ` by ${e.created_by_label}` : ''}
      </div>
    </Section>
  );

  const contact = <ContactSection detail={detail} meta={meta} onEdit={() => setDialog('contact')} />;

  const bookings = (detail.events.length > 0 || canBook) ? (
    <Section
      title="Bookings"
      actions={canBook ? (
        <Button size="sm" variant="primary" icon={CalendarCheck} onClick={() => setDialog('book')} disabled={!canWrite} title={writeTitle} data-testid="fn-record-booking">
          Record booking
        </Button>
      ) : undefined}
    >
      {booked && (
        <DoneNote>
          <span style={{ flex: 1 }}>{booked.created ? 'Booked — the event is created.' : 'This enquiry was already booked.'}</span>
          <Button size="sm" onClick={() => openFnPage('events', { event: booked.eventId })}>Open the event</Button>
        </DoneNote>
      )}
      {detail.events.length === 0 ? (
        <Muted>No booking yet. When the customer says yes, record it here — it creates the event.</Muted>
      ) : (
        <ul style={listReset}>
          {detail.events.map((ev) => {
            const st = eventStatusBadge(ev.status);
            return (
              <li key={ev.id}>
                <button type="button" className="n-row" onClick={() => openFnPage('events', { event: ev.id })} style={{ alignItems: 'flex-start' }}>
                  <Icon icon={ev.cancelled_at ? CalendarX : CalendarDays} size={16} tone="muted" style={{ marginTop: 2 }} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: 'block' }}>{ev.name || 'Event'}</span>
                    <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontWeight: 400 }}>
                      {[dateLabel(ev.event_date), ev.venue_name, ev.guests ? `${ev.guests} guests` : null, moneyAuto(ev.expected_spend_cents, meta.company.currency)].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  <Badge tone={st.tone}>{st.label}</Badge>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  ) : null;

  const proposal = <ProposalSection detail={detail} tz={tz} canWrite={canWrite} onChanged={quietReload} />;
  const threads = <ThreadsSection detail={detail} tz={tz} canWrite={canWrite} onChanged={quietReload} />;

  const followUp = (
    <Section
      title="Follow-up"
      actions={
        <>
          <Button size="sm" variant="quiet" onClick={() => setDialog('follow-up')} disabled={!canWrite || lost} title={writeTitle}>
            {e.follow_up_date ? 'Change' : 'Set follow-up'}
          </Button>
          {e.follow_up_date && (
            <Button size="sm" variant="quiet" disabled={!canWrite || !!busy} title={writeTitle}
              onClick={() => run('clear-follow-up', () => fnSend<Detail>('DELETE', `/enquiries/${e.id}/follow-up`)).then((d) => { if (d) setFromWrite(d); })}>
              Clear
            </Button>
          )}
        </>
      }
    >
      {e.follow_up_date ? (
        <div>
          <div style={{ fontWeight: 500 }}>{dateLabel(e.follow_up_date)}</div>
          {e.follow_up_reason && <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', whiteSpace: 'pre-wrap' }}>{e.follow_up_reason}</div>}
        </div>
      ) : <Muted>None set — Norm reminds you on day 7 and day 14 after our last email.</Muted>}
    </Section>
  );

  const notes = <NotesSection key={`notes:${e.version}`} detail={detail} canWrite={canWrite} onSaved={setFromWrite} onReload={quietReload} />;
  const activity = <ActivitySection detail={detail} tz={tz} />;
  const options = <OptionsSection meta={meta} detail={detail} onChanged={quietReload} onReload={quietReload} />;
  const menus = <MenusSection meta={meta} detail={detail} onChanged={quietReload} onReload={quietReload} />;

  return (
    <div ref={boxRef}>
      {header}
      {band}
      <ErrorNote error={error} onReload={quietReload} onDismiss={() => setError(null)} />
      <div style={{ marginTop: 12 }}>
        {wide ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(300px, 360px)', gap: 16, alignItems: 'start' }}>
            <div style={column}>{details}{options}{menus}{activity}</div>
            <div style={column}>{contact}{bookings}{proposal}{threads}{followUp}{notes}</div>
          </div>
        ) : (
          <div style={column}>{contact}{details}{bookings}{options}{menus}{proposal}{threads}{followUp}{notes}{activity}</div>
        )}
      </div>

      {dialog === 'edit' && <EditEnquiryDialog meta={meta} detail={detail} onSaved={setFromWrite} onClose={() => setDialog(null)} onReload={quietReload} />}
      {dialog === 'close' && (
        <ReasonDialog
          title="Close this enquiry"
          intro="It's marked lost. Say why — the reason shows on the enquiry and in reports."
          submitLabel="Close as lost"
          danger
          placeholder="e.g. Booked elsewhere; no reply after day 14 call"
          onSubmit={async (reason) => setFromWrite(await fnSend<Detail>('POST', `/enquiries/${e.id}/close`, { reason }))}
          onClose={() => setDialog(null)}
          onReload={quietReload}
        />
      )}
      {dialog === 'reopen' && (
        <ReasonDialog
          title="Reopen this enquiry"
          intro={meta.reopen_rule}
          submitLabel="Reopen"
          placeholder="e.g. Customer emailed asking for a new date"
          onSubmit={async (reason) => setFromWrite(await fnSend<Detail>('POST', `/enquiries/${e.id}/reopen`, { reason }))}
          onClose={() => setDialog(null)}
          onReload={quietReload}
        />
      )}
      {dialog === 'archive' && (
        <ConfirmDialog
          title="Archive this enquiry?"
          confirmLabel="Archive"
          busyLabel="Archiving…"
          onConfirm={async () => { setFromWrite(await fnSend<Detail>('POST', `/enquiries/${e.id}/archive`)); }}
          onClose={() => setDialog(null)}
        >
          It leaves the lists and reports. Nothing is deleted: tick “Include archived” on Enquiries to find it again and unarchive it.
        </ConfirmDialog>
      )}
      {dialog === 'follow-up' && (
        <FollowUpDialog
          initialDate={e.follow_up_date}
          initialReason={e.follow_up_reason}
          onSubmit={async (date, reason) => setFromWrite(await fnSend<Detail>('PUT', `/enquiries/${e.id}/follow-up`, { date, reason }))}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'contact' && detail.contact && (
        <EditContactDialog contact={detail.contact} onSaved={() => quietReload()} onClose={() => setDialog(null)} onReload={quietReload} />
      )}
      {dialog === 'book' && (
        <ConfirmBookingDialog
          meta={meta}
          detail={detail}
          onBooked={(ev, created) => { setBooked({ eventId: ev.id, created }); quietReload(); }}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}

const column: CSSProperties = { display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 };
const listReset: CSSProperties = { listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 2 };

function Band({ tone, children }: { tone: 'accent' | 'ok' | 'muted'; children: ReactNode }) {
  const look: Record<string, CSSProperties> = {
    accent: { background: 'var(--accent-soft)', borderColor: 'var(--brand-soft)' },
    ok: { background: 'var(--ok-bg)', borderColor: 'var(--line)' },
    muted: { background: 'var(--surface-alt)', borderColor: 'var(--line)' },
  };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '10px 14px', border: '1px solid', borderRadius: 'var(--radius-lg)', fontSize: 'var(--fs-base)', ...look[tone] }}>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lead source details
// ---------------------------------------------------------------------------

const ATTRIBUTION_LABELS: Record<string, string> = {
  utm_source: 'UTM source', utm_medium: 'UTM medium', utm_campaign: 'UTM campaign', utm_term: 'UTM term',
  utm_content: 'UTM content', gclid: 'Google click id', gbraid: 'Google click id (gbraid)', wbraid: 'Google click id (wbraid)',
  referrer: 'Referrer', landing_page: 'Landing page',
};

function Attribution({ attribution }: { attribution: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const entries = Object.entries(attribution || {}).filter(([, v]) => v);
  if (!entries.length) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <Button variant="link" size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? 'Hide lead source details' : 'Lead source details'}
      </Button>
      {open && (
        <dl style={{ margin: '8px 0 0', display: 'grid', gridTemplateColumns: 'minmax(120px, max-content) 1fr', gap: '4px 12px', fontSize: 'var(--fs-sm)' }}>
          {entries.map(([k, v]) => (
            <div key={k} style={{ display: 'contents' }}>
              <dt style={{ color: 'var(--muted)' }}>{ATTRIBUTION_LABELS[k] ?? sentence(k)}</dt>
              <dd style={{ margin: 0, overflowWrap: 'anywhere' }}>{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Contact
// ---------------------------------------------------------------------------

function ContactSection({ detail, meta, onEdit }: {
  detail: Detail;
  meta: FnMeta;
  onEdit: () => void;
}) {
  const c: Contact | null = detail.contact;
  if (!c) return <Section title="Contact"><Muted>No contact on this enquiry.</Muted></Section>;
  const anonymised = !!c.anonymised_at;
  return (
    <Section
      title="Contact"
      actions={
        <>
          {!anonymised && <Button size="sm" variant="quiet" icon={Pencil} onClick={onEdit} disabled={!meta.can_write} title={meta.can_write ? undefined : READ_ONLY_HINT}>Edit</Button>}
          <Button size="sm" variant="quiet" onClick={() => openFnPage('contacts', { contact: c.id })}>Open</Button>
        </>
      }
    >
      <div style={{ fontWeight: 600 }}>
        {displayName(c.name, anonymised ? 'Anonymised contact' : 'Unnamed contact')}
        {anonymised && <> <Badge>Anonymised</Badge></>}
      </div>
      {c.company && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{c.company}</div>}
      <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 2, fontSize: 'var(--fs-sm)' }}>
        {(c.emails ?? []).map((em) => (
          <a key={em.email} href={`mailto:${em.email}`} style={{ color: 'var(--accent)', overflowWrap: 'anywhere' }}>
            {em.email}{em.is_primary && (c.emails?.length ?? 0) > 1 ? ' (primary)' : ''}
          </a>
        ))}
        {c.phone && <a href={`tel:${c.phone.replace(/\s+/g, '')}`} style={{ color: 'var(--accent)' }}>{c.phone}</a>}
        {!c.emails?.length && !c.phone && <Muted>No email or phone on record.</Muted>}
      </div>
      {c.notes && <div style={{ marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', whiteSpace: 'pre-wrap' }}>{c.notes}</div>}
    </Section>
  );
}

/** "Repeat contact": the contact's other enquiries and bookings, from the
 *  header — each opens in place (enquiries) or on the Events page. */
function RepeatContact({ detail, tz, onOpenEnquiry }: { detail: Detail; tz: string; onOpenEnquiry: (id: string) => void }) {
  const history = detail.contact_history;
  const others = history.enquiries.length + history.events.length;
  if (!others) return null;
  return (
    <Popover
      width={340}
      trigger={({ open, toggle, id }) => (
        <button
          type="button"
          className="n-badge n-badge--accent"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={id}
          title="Their other enquiries and bookings"
          data-testid="fn-repeat-contact"
          style={{ border: 'none', cursor: 'pointer', fontFamily: 'inherit' }}
        >
          <Icon icon={Users} size={12} />
          Repeat contact · {others}
        </button>
      )}
    >
      {(close) => (
        <div>
          {history.enquiries.length > 0 && <div className="n-eyebrow" style={{ padding: '4px 8px' }}>Other enquiries</div>}
          {history.enquiries.map((h) => (
            <button key={h.id} type="button" className="n-option" onClick={() => { close(); onOpenEnquiry(h.id); }} style={popRow}>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.subject || 'No subject'}</span>
                <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{[h.venue_name || 'Group-wide', `received ${dayOf(h.created_at, tz)}`].join(' · ')}</span>
              </span>
              <Badge tone={stageTone(h.stage)}>{stageLabel(h.stage)}</Badge>
            </button>
          ))}
          {history.events.length > 0 && <div className="n-eyebrow" style={{ padding: '8px 8px 4px' }}>Other bookings</div>}
          {history.events.map((ev) => {
            const st = eventStatusBadge(ev.status);
            return (
              <button key={ev.id} type="button" className="n-option" onClick={() => { close(); openFnPage('events', { event: ev.id }); }} style={popRow}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.name || 'Event'}</span>
                  <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{[dateLabel(ev.event_date), ev.venue_name].filter(Boolean).join(' · ')}</span>
                </span>
                <Badge tone={st.tone}>{st.label}</Badge>
              </button>
            );
          })}
        </div>
      )}
    </Popover>
  );
}

const popRow: CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, borderRadius: 'var(--radius-sm)', padding: '6px 8px' };

// ---------------------------------------------------------------------------
// Proposal
// ---------------------------------------------------------------------------

function ProposalSection({ detail, tz, canWrite, onChanged }: { detail: Detail; tz: string; canWrite: boolean; onChanged: () => void }) {
  const p = detail.proposal;
  const [copied, setCopied] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [now] = useState(() => Date.now());
  const link = detail.quote_links.find((l) => !l.revoked_at && (!l.expires_at || Date.parse(l.expires_at) > now)) ?? null;
  const sent = p.versions > 0 || !!p.last_sent_at;
  // The newest link that was revoked, when no live one replaced it yet.
  const revoked = !link
    ? [...detail.quote_links].filter((l) => l.revoked_at).sort((a, b) => (b.revoked_at ?? '').localeCompare(a.revoked_at ?? ''))[0] ?? null
    : null;
  const openEditor = () => openFnPage('inbox', { view: 'proposal', enquiry: detail.enquiry.id });
  const copy = async () => {
    if (!link?.url) return;
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — the link is still selectable below */
    }
  };
  let status: ReactNode;
  if (p.versions > 0 || p.last_sent_at) status = <Badge tone="info">Sent</Badge>;
  else if (p.exists) status = <Badge>Draft</Badge>;
  else status = <Muted>Not started.</Muted>;
  return (
    <Section
      title="Proposal"
      actions={<Button size="sm" icon={p.exists ? FileText : Plus} onClick={openEditor} data-testid="fn-open-proposal">{p.exists ? 'Open proposal' : 'Start proposal'}</Button>}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>{status}
        {p.exists && p.updated_at && <Muted>updated {relativeLabel(p.updated_at)}</Muted>}
        {!p.exists && p.last_sent_at && <Muted>— the document itself is still in Orbit</Muted>}
      </div>
      <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 2, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        {p.last_sent_at && <span>Last sent {instantLabel(p.last_sent_at, tz)}{p.versions ? ` · ${p.versions} ${p.versions === 1 ? 'version' : 'versions'}` : ''}</span>}
        {p.last_viewed_at && <span>Customer viewed {instantLabel(p.last_viewed_at, tz)}</span>}
        {p.last_sent_at && !p.last_viewed_at && <Muted>Not viewed yet</Muted>}
      </div>
      {p.brand_changed && <div style={{ marginTop: 6 }}><Badge tone="warn">The venue’s brand changed since it was started</Badge></div>}
      {link && !link.url && (
        <div style={{ marginTop: 8, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          Customer link live{link.last_used_at ? ` · last opened ${relativeLabel(link.last_used_at)}` : ''}{link.expires_at ? ` · expires ${dayOf(link.expires_at, tz)}` : ''}
        </div>
      )}
      {link?.url && (
        <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input className="n-input" readOnly value={link.url} aria-label="Customer quote link" style={{ flex: 1, minWidth: 0, fontSize: 'var(--fs-xs)' }} onFocus={(ev) => ev.currentTarget.select()} />
          <IconButton icon={copied ? Check : Copy} label={copied ? 'Copied' : 'Copy link'} iconSize={16} onClick={copy} />
        </div>
      )}
      {sent && link && (
        <div style={{ marginTop: 8 }}>
          <Button
            size="sm"
            variant="quiet"
            icon={Link2Off}
            onClick={() => setRevoking(true)}
            disabled={!canWrite}
            title={canWrite ? 'Stop the customer’s quote link working' : READ_ONLY_HINT}
            data-testid="fn-revoke-quote-link"
          >
            Revoke quote link
          </Button>
        </div>
      )}
      {sent && revoked && (
        <div style={{ marginTop: 8, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          Customer link revoked {instantLabel(revoked.revoked_at, tz)} — the next send issues a new one.
        </div>
      )}
      {revoking && (
        <ConfirmDialog
          title="Revoke the quote link?"
          confirmLabel="Revoke link"
          busyLabel="Revoking…"
          danger
          onConfirm={async () => { await fnSend('POST', `/enquiries/${detail.enquiry.id}/quote-link/revoke`); onChanged(); }}
          onClose={() => setRevoking(false)}
        >
          The customer’s quote link stops working straight away: anyone who opens it — including from an email already sent —
          is told the link isn’t valid, and can’t confirm or decline an option there. Nothing is sent to them. The next time
          the proposal is sent, Norm issues a new link.
        </ConfirmDialog>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Linked mail
// ---------------------------------------------------------------------------

function ThreadsSection({ detail, tz, canWrite, onChanged }: { detail: Detail; tz: string; canWrite: boolean; onChanged: () => void }) {
  const [unlinking, setUnlinking] = useState<string | null>(null);
  return (
    <Section title="Mail" meta={detail.threads.length ? `${detail.threads.length} linked` : undefined}>
      {detail.threads.length === 0 ? (
        <div>
          <Muted>No mail linked. Link threads from the Inbox.</Muted>
          <div style={{ marginTop: 8 }}><Button size="sm" icon={Mail} onClick={() => openFnPage('inbox')}>Open the Inbox</Button></div>
        </div>
      ) : (
        <ul style={listReset}>
          {detail.threads.map((t) => (
            <li key={t.link_id} style={{ display: 'flex', alignItems: 'flex-start', gap: 4 }}>
              <button type="button" className="n-row" onClick={() => openFnPage('inbox', mailThreadParams(t.account_id, t.gmail_thread_id))} style={{ alignItems: 'flex-start', flex: 1, minWidth: 0 }}>
                <Icon icon={Mail} size={16} tone="muted" style={{ marginTop: 2 }} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{decodeEntities(t.subject) || 'No subject'}</span>
                  <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontWeight: 400 }}>
                    {t.mirrored
                      ? [t.last_from_name || t.last_from_email, t.last_message_at ? instantLabel(t.last_message_at, tz) : null, t.message_count ? `${t.message_count} ${t.message_count === 1 ? 'message' : 'messages'}` : null].filter(Boolean).join(' · ')
                      : `${t.mailbox} · not synced yet`}
                  </span>
                </span>
                {!!t.unread_count && <Badge tone="accent">{t.unread_count} new</Badge>}
              </button>
              {canWrite && <IconButton icon={Unlink} label="Unlink this thread" iconSize={16} onClick={() => setUnlinking(t.link_id)} />}
            </li>
          ))}
        </ul>
      )}
      {unlinking && (
        <ConfirmDialog
          title="Unlink this thread?"
          confirmLabel="Unlink"
          busyLabel="Unlinking…"
          onConfirm={async () => { await fnSend('DELETE', `/thread-links/${unlinking}`); onChanged(); }}
          onClose={() => setUnlinking(null)}
        >
          The mail stays in the mailbox; it just stops counting towards this enquiry’s replies and stage.
        </ConfirmDialog>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Notes
// ---------------------------------------------------------------------------

function NotesSection({ detail, canWrite, onSaved, onReload }: { detail: Detail; canWrite: boolean; onSaved: (d: Detail) => void; onReload: () => void }) {
  const saved = detail.enquiry.notes ?? '';
  const [text, setText] = useState(saved);
  const { busy, error, setError, run } = useAction();
  const dirty = text !== saved;
  const save = () => run('notes', () => fnSend<Detail>('PATCH', `/enquiries/${detail.enquiry.id}`, { version: detail.enquiry.version, notes: text.trim() || null }))
    .then((d) => { if (d) onSaved(d); });
  return (
    <Section title="Notes" meta="Staff only">
      <textarea
        className="n-input"
        aria-label="Internal notes"
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={!canWrite}
        placeholder={canWrite ? 'Anything the team should know' : READ_ONLY_HINT}
        style={{ width: '100%' }}
      />
      <ErrorNote error={error} onReload={onReload} onDismiss={() => setError(null)} />
      {dirty && canWrite && (
        <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
          <Button size="sm" variant="primary" onClick={save} disabled={!!busy}>{busy ? 'Saving…' : 'Save notes'}</Button>
          <Button size="sm" variant="quiet" onClick={() => setText(saved)} disabled={!!busy}>Discard</Button>
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Activity
// ---------------------------------------------------------------------------

const KIND_ICONS: Record<string, LucideIcon> = {
  enquiry_created: Plus, enquiry_lost: CircleX, enquiry_reopened: RotateCcw,
  option_added: CircleDollarSign, option_withdrawn: CircleDollarSign,
  option_approve: ShieldCheck, option_override: ShieldAlert, option_revoke: ShieldOff,
  customer_confirmed: CircleCheck, customer_declined: CircleX,
  proposal_sent: FileText, proposal_viewed: Eye,
  thread_linked: Link2, thread_unlinked: Unlink, email_in: Mail, email_out: Send,
  event_created: CalendarCheck, event_edited: CalendarDays, event_link_sent: Send,
  event_cancelled: CalendarX, event_reinstated: CalendarCheck,
  section_confirmed: ListChecks, section_skipped: ListChecks, section_reopened: ListChecks,
};

const SHOWN = 12;

function ActivitySection({ detail, tz }: { detail: Detail; tz: string }) {
  const [all, setAll] = useState(false);
  const items = detail.timeline;
  const shown = useMemo(() => (all ? items : items.slice(0, SHOWN)), [all, items]);
  return (
    <Section title="Activity" meta={items.length ? `${items.length} ${items.length === 1 ? 'entry' : 'entries'}` : undefined}>
      {items.length === 0 ? <Muted>Nothing yet.</Muted> : (
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column' }}>
          {shown.map((it, i) => <ActivityRow key={`${it.at}:${it.kind}:${i}`} item={it} tz={tz} last={i === shown.length - 1} />)}
        </ol>
      )}
      {items.length > SHOWN && (
        <div style={{ marginTop: 8 }}>
          <Button size="sm" variant="quiet" icon={History} onClick={() => setAll((a) => !a)}>{all ? 'Show fewer' : `Show all ${items.length}`}</Button>
        </div>
      )}
    </Section>
  );
}

function ActivityRow({ item, tz, last }: { item: TimelineItem; tz: string; last: boolean }) {
  const ref = item.ref ?? {};
  const target: (() => void) | null = ref.account_id && ref.gmail_thread_id
    ? () => openFnPage('inbox', mailThreadParams(ref.account_id as string, ref.gmail_thread_id as string))
    : ref.event_id ? () => openFnPage('events', { event: ref.event_id as string }) : null;
  const text = detailText(item.detail);
  const body = (
    <>
      <span style={{ display: 'block', fontWeight: 500, color: 'var(--text)' }}>{item.title}</span>
      {text && <span style={clamp2}>{text}</span>}
      <span style={{ display: 'block', marginTop: 2, color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>{[item.actor, instantLabel(item.at, tz)].filter(Boolean).join(' · ')}</span>
    </>
  );
  return (
    <li style={{ display: 'flex', gap: 10, paddingBottom: last ? 0 : 12 }}>
      <span style={{ flex: '0 0 auto', width: 28, height: 28, borderRadius: '50%', background: 'var(--surface-alt)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
        <Icon icon={KIND_ICONS[item.kind] ?? History} size={14} tone="muted" />
      </span>
      {target ? (
        <button type="button" onClick={target} style={activityButton} title={ref.account_id ? 'Open in the Inbox' : 'Open the event'}>{body}</button>
      ) : (
        <span style={{ flex: 1, minWidth: 0, fontSize: 'var(--fs-base)' }}>{body}</span>
      )}
    </li>
  );
}

// Two lines of an email snippet, then an ellipsis.
const clamp2: CSSProperties = {
  marginTop: 1, color: 'var(--text-soft)', fontSize: 'var(--fs-sm)', overflow: 'hidden',
  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflowWrap: 'anywhere',
};

const activityButton: CSSProperties = {
  flex: 1, minWidth: 0, padding: 0, border: 'none', background: 'none', font: 'inherit', fontSize: 'var(--fs-base)',
  textAlign: 'left', cursor: 'pointer', color: 'inherit',
};
