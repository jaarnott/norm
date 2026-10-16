'use client';

/**
 * A new email from a venue mailbox (`?view=compose`), optionally about an
 * enquiry (`&enquiry=<id>`: to its contact, from its venue's mailbox, and the
 * new conversation is linked to it).
 */
import { useEffect, useState } from 'react';
import Button from '../../ui/Button';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import { fnGet } from '../shared/api';
import type { FnMeta } from '../shared/meta';
import { errorText, fieldLabel } from './bits';
import Composer from './Composer';
import { openMailboxSettings } from './nav';
import { decodeEntities, mailboxReady } from './text';
import type { Mailbox, OutboxResult } from './types';

interface EnquiryForEmail {
  enquiry: { id: string; subject: string | null; booking_type: string | null; venue_id: string | null };
  contact: { name: string | null; email?: string | null } | null;
  stage: string;
}

export default function NewEmail({
  meta,
  mailboxes,
  canWrite,
  enquiryId,
  venueId,
  onBack,
  onSent,
}: {
  meta: FnMeta;
  mailboxes: Mailbox[];
  canWrite: boolean;
  enquiryId: string | null;
  /** The venue picked on the Inbox ('all' = any). */
  venueId: string;
  onBack: () => void;
  onSent: (accountId: string, result: OutboxResult) => void;
}) {
  const [loaded, setLoaded] = useState<{ id: string; data?: EnquiryForEmail; error?: string } | null>(null);
  const [choice, setChoice] = useState<string | null>(null);

  useEffect(() => {
    if (!enquiryId) return;
    let alive = true;
    fnGet<EnquiryForEmail>(`/enquiries/${encodeURIComponent(enquiryId)}`)
      .then((data) => alive && setLoaded({ id: enquiryId, data }))
      .catch((err) => alive && setLoaded({ id: enquiryId, error: errorText(err) }));
    return () => {
      alive = false;
    };
  }, [enquiryId]);

  const header = (meta2?: string) => <PageHeader title="New email" back={{ label: 'Inbox', onClick: onBack }} meta={meta2} />;
  const enquiry = enquiryId && loaded?.id === enquiryId ? loaded.data : null;
  if (enquiryId && loaded?.id === enquiryId && loaded.error) {
    return (
      <div>
        {header()}
        <PageState kind="error" title="Couldn’t load the enquiry" detail={loaded.error} />
      </div>
    );
  }
  if (enquiryId && !enquiry) {
    return (
      <div>
        {header()}
        <PageState kind="loading" title="Loading the enquiry…" />
      </div>
    );
  }
  if (!mailboxes.length) {
    return (
      <div>
        {header()}
        <PageState
          kind="empty"
          title="No venue mailbox yet"
          detail="Connect a venue’s functions mailbox to send email from Norm."
          action={<Button onClick={openMailboxSettings}>Connect in Settings</Button>}
        />
      </div>
    );
  }

  const wantVenue = enquiry?.enquiry.venue_id ?? (venueId !== 'all' ? venueId : null);
  const atVenue = mailboxes.filter((m) => !wantVenue || m.venue_id === wantVenue);
  const fallback = atVenue.find((m) => mailboxReady(m.health)) ?? atVenue[0] ?? mailboxes.find((m) => mailboxReady(m.health)) ?? mailboxes[0];
  const mailbox = mailboxes.find((m) => m.id === choice) ?? fallback;

  return (
    <div>
      {header(enquiry ? `About “${decodeEntities(enquiry.enquiry.subject) || 'the enquiry'}” — linked to it when sent` : undefined)}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={fieldLabel}>From</span>
          <select className="n-select" aria-label="Send from" value={mailbox.id} onChange={(e) => setChoice(e.target.value)} style={{ maxWidth: '100%' }}>
            {mailboxes.map((m) => (
              <option key={m.id} value={m.id}>
                {m.venue_name} — {m.google_email}
                {mailboxReady(m.health) ? '' : ' (not connected)'}
              </option>
            ))}
          </select>
        </label>
        <Composer
          key={mailbox.id}
          accountId={mailbox.id}
          mailbox={mailbox}
          mode="new"
          threadId={null}
          enquiryId={enquiryId}
          initial={enquiry ? { to: enquiry.contact?.email ?? '', subject: decodeEntities(enquiry.enquiry.subject) } : undefined}
          venueId={mailbox.venue_id}
          bookingType={enquiry?.enquiry.booking_type ?? null}
          templateStage={enquiry && enquiry.stage !== 'new' ? 'follow_up' : 'first_reply'}
          clientName={enquiry?.contact?.name ?? null}
          senderName={meta.user.name}
          canWrite={canWrite}
          onSent={(r) => onSent(mailbox.id, r)}
          onClose={onBack}
        />
      </div>
    </div>
  );
}
