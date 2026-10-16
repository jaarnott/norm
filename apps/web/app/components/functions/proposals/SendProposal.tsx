'use client';

/**
 * Send the proposal (F4 `POST /api/functions/proposals/{enquiry}/send`):
 * "proposal link" freezes the saved proposal and the options on offer as a
 * version and emails the customer a quote link (at {{quote_link}}, or after
 * the message) — the enquiry becomes quoted; "email only" is a plain email on
 * the enquiry's thread (and gets the pricing warning). Same send rules as the
 * composer: a kept idempotency key, 202 polling, reason-to-override.
 */
import { useState, type ReactNode } from 'react';
import { Copy, Send } from 'lucide-react';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import { fnSend } from '../shared/api';
import type { FnMeta } from '../shared/meta';
import { fieldLabel, MailboxNotice, Notice, ReadOnlyHint } from '../inbox/bits';
import { SendPhaseNotice } from '../inbox/Composer';
import { cleanTemplateHtml } from '../inbox/cleanHtml';
import { RichTextField } from '../inbox/RichText';
import TemplatePicker from '../inbox/TemplatePicker';
import { fingerprint } from '../inbox/sendFlow';
import {
  decodeEntities,
  fillTemplate,
  firstName,
  invalidAddresses,
  isBlankHtml,
  mailboxReady,
  parseAddresses,
  unfilledBlanks,
} from '../inbox/text';
import type { Mailbox, ReplyTemplate } from '../inbox/types';
import { useSendFlow } from '../inbox/useSendFlow';
import { defaultMessage } from './proposalDoc';
import type { EnquiryForProposal, ProposalSendResult } from './types';

type Mode = 'proposal' | 'email';

export default function SendProposal({
  enquiry,
  meta,
  mailboxes,
  canWrite,
  onSent,
  onClose,
}: {
  enquiry: EnquiryForProposal;
  meta: FnMeta;
  mailboxes: Mailbox[];
  canWrite: boolean;
  onSent: (result: ProposalSendResult) => void;
  onClose: () => void;
}) {
  const client = firstName(enquiry.contact?.name);
  const [mode, setMode] = useState<Mode>('proposal');
  const [to, setTo] = useState(enquiry.contact?.email ?? '');
  const [cc, setCc] = useState('');
  const [subject, setSubject] = useState('');
  // '' = let Norm choose (the newest linked thread on a connected mailbox, else a new email).
  const [route, setRoute] = useState('');
  const [body, setBody] = useState(() => defaultMessage('proposal', client));
  const [resetKey, setResetKey] = useState(0);
  const [reason, setReason] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const flow = useSendFlow();

  const threads = enquiry.threads;
  const chosen = threads.find((t) => `${t.account_id}:${t.gmail_thread_id}` === route) ?? null;
  const venueMailboxes = mailboxes.filter((m) => m.venue_id === enquiry.enquiry.venue_id);
  const candidates = chosen
    ? mailboxes.filter((m) => m.id === chosen.account_id)
    : route === 'new'
      ? venueMailboxes
      : [...mailboxes.filter((m) => threads.some((t) => t.account_id === m.id)), ...venueMailboxes];
  const ready = candidates.some((m) => mailboxReady(m.health));
  const phase = flow.phase;
  const sent = phase.kind === 'sent' ? (phase.result as ProposalSendResult) : null;
  const busy = phase.kind === 'busy' || phase.kind === 'sending';
  const blanks = unfilledBlanks(body, ['quote_link']);
  const sendsOnThread = !!chosen || (route === '' && threads.length > 0);

  const switchMode = (next: Mode) => {
    // Swap the starting text only while it is still untouched.
    if (body === defaultMessage(mode, client)) {
      setBody(defaultMessage(next, client));
      setResetKey((k) => k + 1);
    }
    setMode(next);
    flow.reset();
  };

  const insertTemplate = (t: ReplyTemplate) => {
    setBody(fillTemplate(cleanTemplateHtml(t.body_html), { client_name: client, sender_name: firstName(meta.user.name) || meta.user.name }));
    if (t.subject && !subject) setSubject(t.subject);
    setResetKey((k) => k + 1);
  };

  const send = async () => {
    const toList = parseAddresses(to);
    const ccList = parseAddresses(cc);
    const bad = invalidAddresses([...toList, ...ccList]);
    if (bad.length) return setProblem(`Not an email address: ${bad.join(', ')}.`);
    if (!toList.length && !sendsOnThread) return setProblem('Who is this to? Add the customer’s email address.');
    if (isBlankHtml(body)) return setProblem('Write a short message to go with it.');
    setProblem(null);
    const fp = fingerprint({ mode, to: toList, cc: ccList, subject: subject.trim(), body, route });
    await flow.send(fp, (key, overrideReason) =>
      fnSend<ProposalSendResult>('POST', `/proposals/${encodeURIComponent(enquiry.enquiry.id)}/send`, {
        mode,
        body_html: body,
        subject: subject.trim() || null,
        to: toList.length ? toList : null,
        cc: ccList.length ? ccList : null,
        account_id: chosen?.account_id ?? (route === 'new' ? (venueMailboxes.find((m) => mailboxReady(m.health)) ?? venueMailboxes[0])?.id ?? null : null),
        gmail_thread_id: chosen?.gmail_thread_id ?? null,
        idempotency_key: key,
        price_override_reason: overrideReason,
      }),
    );
  };

  if (sent) {
    return (
      <Dialog title={sent.mode === 'proposal' ? 'Proposal sent' : 'Email sent'} width={520} onClose={() => onSent(sent)} closeButton>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Notice tone="ok">
            {sent.duplicate
              ? 'That email had already been sent — nothing went twice.'
              : sent.mode === 'proposal'
                ? 'The proposal was frozen as a new version and the customer has the link. The enquiry is now quoted.'
                : 'Sent on the enquiry’s email thread.'}
          </Notice>
          {sent.quote_url && (
            <div>
              <div style={fieldLabel}>The customer’s link</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4 }}>
                <input className="n-input" readOnly value={sent.quote_url} aria-label="Quote link" style={{ flex: 1, minWidth: 0 }} onFocus={(e) => e.target.select()} />
                <Button
                  icon={Copy}
                  onClick={() => {
                    navigator.clipboard?.writeText(sent.quote_url || '').then(() => setCopied(true), () => undefined);
                  }}
                >
                  {copied ? 'Copied' : 'Copy'}
                </Button>
              </div>
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button variant="primary" onClick={() => onSent(sent)}>
              Done
            </Button>
          </div>
        </div>
      </Dialog>
    );
  }

  const radio = (value: Mode, label: string, help: string) => (
    <label
      style={{
        flex: '1 1 220px',
        display: 'flex',
        gap: 8,
        alignItems: 'flex-start',
        padding: '10px 12px',
        border: `1px solid ${mode === value ? 'var(--brand-soft)' : 'var(--line)'}`,
        background: mode === value ? 'var(--accent-soft)' : 'var(--bg)',
        borderRadius: 'var(--radius)',
        cursor: 'pointer',
      }}
    >
      <input type="radio" name="send-mode" checked={mode === value} onChange={() => switchMode(value)} style={{ margin: '3px 0 0', accentColor: 'var(--accent)' }} />
      <span>
        <span style={{ display: 'block', fontWeight: 600, fontSize: 'var(--fs-base)' }}>{label}</span>
        <span style={{ display: 'block', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{help}</span>
      </span>
    </label>
  );
  const row = (label: string, control: ReactNode) => (
    <label style={{ display: 'grid', gridTemplateColumns: '64px minmax(0, 1fr)', alignItems: 'center', gap: 8 }}>
      <span style={fieldLabel}>{label}</span>
      {control}
    </label>
  );

  return (
    <Dialog title="Send the proposal" width={680} onClose={() => !busy && onClose()} closeButton>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div role="radiogroup" aria-label="What to send" style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {radio('proposal', 'Proposal link', 'Freezes this proposal and its options; the customer can confirm an option from the link.')}
          {radio('email', 'Email only', 'A plain email on the enquiry’s thread. Nothing is frozen and no link is sent.')}
        </div>

        {row(
          'Send as',
          <select className="n-select" value={route} onChange={(e) => setRoute(e.target.value)} style={{ width: '100%' }} aria-label="Send as">
            <option value="">{threads.length ? 'A reply on the enquiry’s latest email' : 'A new email from the venue mailbox'}</option>
            {threads.map((t) => (
              <option key={t.link_id} value={`${t.account_id}:${t.gmail_thread_id}`}>
                Reply on: {decodeEntities(t.subject) || '(no subject)'} — {t.mailbox}
              </option>
            ))}
            {threads.length > 0 && <option value="new">A new email from the venue mailbox</option>}
          </select>,
        )}
        {row('To', <input className="n-input" value={to} onChange={(e) => setTo(e.target.value)} aria-label="To" placeholder={sendsOnThread ? 'Whoever wrote last' : 'customer@example.com'} style={{ width: '100%' }} />)}
        {row('Cc', <input className="n-input" value={cc} onChange={(e) => setCc(e.target.value)} aria-label="Cc" style={{ width: '100%' }} />)}
        {row(
          'Subject',
          <input
            className="n-input"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            aria-label="Subject"
            placeholder={sendsOnThread ? 'Re: the thread’s subject' : decodeEntities(enquiry.enquiry.subject) || 'Your enquiry'}
            style={{ width: '100%' }}
          />,
        )}
        <div>
          <RichTextField
            html={body}
            resetKey={`send-${resetKey}`}
            onChange={setBody}
            minHeight={150}
            toolbarExtra={
              <TemplatePicker
                venueId={enquiry.enquiry.venue_id}
                bookingType={enquiry.enquiry.booking_type}
                stage={enquiry.stage === 'new' ? 'first_reply' : 'follow_up'}
                onPick={insertTemplate}
              />
            }
          />
          {mode === 'proposal' && (
            <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
              Put <code>{'{{quote_link}}'}</code> where the link should go — otherwise it’s added after your message. The venue signature is added too.
            </div>
          )}
        </div>
        {blanks.length > 0 && <Notice tone="warn">Fill in the blanks before sending: {blanks.join(', ')}</Notice>}
        {!ready && phase.kind !== 'mailbox' && (
          <MailboxNotice message={`${candidates[0]?.google_email ?? 'The venue’s mailbox'} isn’t connected to Norm yet, so nothing can be sent from it.`} />
        )}
        {phase.kind === 'mailbox' && <MailboxNotice message={phase.message} />}
        {problem && <Notice tone="error">{problem}</Notice>}
        <SendPhaseNotice
          phase={phase}
          reason={reason}
          onReason={setReason}
          onOverride={() => flow.override(reason.trim())}
          onEdit={flow.reset}
          onRetry={flow.retry}
          onCheckAgain={flow.checkAgain}
        />
        <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {!canWrite && <ReadOnlyHint />}
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" icon={Send} onClick={send} disabled={!canWrite || busy || !ready}>
            {busy ? 'Sending…' : mode === 'proposal' ? 'Send proposal' : 'Send email'}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
