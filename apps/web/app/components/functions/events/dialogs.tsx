'use client';

/**
 * The Events page's dialogs: a note/reason dialog (post-confirmation edits,
 * skip, reopen, cancel, reinstate) and "Send to client".
 */
import { useEffect, useId, useState } from 'react';
import type { ReactNode } from 'react';
import { Check, Copy, ExternalLink, Send } from 'lucide-react';
import Dialog from '../../ui/Dialog';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import { fnGet, fnSend, FnApiError, newIdempotencyKey } from '../shared/api';
import { instantLabel } from '../shared/format';
import { splitEmails, textToHtml } from './logic';
import { mailboxesLink } from './nav';
import { errorText } from './useLoad';
import type { EventDetail, PortalLink, SendResult } from './types';

/**
 * Asks for a note or reason, then runs the action. While it runs the dialog
 * can't be dismissed; a failure shows inside it and it stays open to retry.
 */
export function NoteDialog({ title, children, label, placeholder, required, confirmLabel, danger = false, onConfirm, onClose }: {
  title: string;
  children?: ReactNode;
  label: string;
  placeholder?: string;
  required: boolean;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: (note: string | null) => Promise<void>;
  onClose: () => void;
}) {
  const fieldId = useId();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const missing = required && !note.trim();

  const confirm = async () => {
    if (missing) {
      setError(`${label} is needed.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onConfirm(note.trim() || null);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
      return;
    }
    onClose();
  };

  return (
    <Dialog title={title} width={460} onClose={() => { if (!busy) onClose(); }}>
      {children && <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)', marginBottom: 12 }}>{children}</div>}
      <label className="n-label" htmlFor={fieldId}>{label}{required ? '' : ' (optional)'}</label>
      <textarea
        id={fieldId}
        className="n-input"
        rows={3}
        value={note}
        placeholder={placeholder}
        onChange={(e) => setNote(e.target.value)}
        style={{ width: '100%' }}
        autoFocus
      />
      {error && (
        <div role="alert" style={{ marginTop: 10, padding: '8px 12px', fontSize: 'var(--fs-sm)', color: 'var(--error)', background: 'var(--error-bg)', borderRadius: 'var(--radius)' }}>
          {error}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 18 }}>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant={danger ? 'danger' : 'primary'} onClick={confirm} disabled={busy} aria-busy={busy || undefined}>
          {busy ? `${confirmLabel}…` : confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}

/** The newest live portal link, if any. */
export function currentPortalLink(links: PortalLink[]): PortalLink | null {
  const live = links.filter((l) => !l.revoked_at);
  if (!live.length) return null;
  return live.slice().sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
}

export function CopyButton({ text, label = 'Copy link' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);
  return (
    <Button
      size="sm"
      icon={copied ? Check : Copy}
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => setCopied(true), () => setCopied(false));
      }}
    >
      {copied ? 'Copied' : label}
    </Button>
  );
}

interface OutboxStatus {
  outbox_id: string;
  status: 'sent' | 'sending' | 'failed';
  error: string | null;
}

/**
 * "Send to client": emails the portal link from the venue mailbox through
 * F4's outbox. One idempotency key per dialog — a retry or a double click is
 * the same email. A 202 means Gmail's answer was lost; the outbox settles it,
 * so the dialog polls a little and says so.
 */
export function SendToClientDialog({ detail, timeZone, onSent, onClose, onCopyLink }: {
  detail: EventDetail;
  timeZone: string;
  onSent: () => void;
  onClose: () => void;
  /** Switch to "Copy client link" (offered when the mailbox can't send). */
  onCopyLink?: () => void;
}) {
  const ids = { to: useId(), cc: useId(), subject: useId(), body: useId() };
  const contactEmails = (detail.contact?.emails ?? []).map((e) => e.email);
  const [to, setTo] = useState(detail.contact?.email ?? contactEmails[0] ?? '');
  const [cc, setCc] = useState('');
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [key] = useState(newIdempotencyKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; mailbox: boolean } | null>(null);
  const [result, setResult] = useState<SendResult | null>(null);
  const [settled, setSettled] = useState<OutboxStatus | null>(null);
  const existing = currentPortalLink(detail.portal_links);

  // A 202 ("sending"): look at the outbox a few times until it settles.
  useEffect(() => {
    if (!result || result.status !== 'sending') return;
    let tries = 0;
    let alive = true;
    const timer = setInterval(() => {
      tries += 1;
      fnGet<OutboxStatus>(`/inbox/outbox/${result.outbox_id}`)
        .then((s) => {
          if (!alive) return;
          if (s.status !== 'sending') {
            setSettled(s);
            clearInterval(timer);
            onSent();
          }
        })
        .catch(() => { /* keep the "sending" message */ });
      if (tries >= 12) clearInterval(timer);
    }, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [result, onSent]);

  const send = async () => {
    const recipients = splitEmails(to);
    if (!recipients.length) {
      setError({ text: 'Who should get the link? Add an email address.', mailbox: false });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fnSend<SendResult>('POST', `/events/${detail.event.id}/send-to-client`, {
        to: recipients,
        cc: splitEmails(cc),
        subject: subject.trim() || null,
        body_html: message.trim() ? textToHtml(message) : null,
        idempotency_key: key,
      });
      setResult(res);
      if (res.status !== 'sending') onSent();
    } catch (e) {
      const mailbox = e instanceof FnApiError && e.code === 'mailbox_unavailable';
      setError({ text: errorText(e), mailbox });
    }
    setBusy(false);
  };

  const status = settled?.status ?? result?.status;
  const done = status === 'sent';

  return (
    <Dialog title="Send to client" width={520} onClose={() => { if (!busy) onClose(); }}>
      {result ? (
        <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
          {done && <p style={{ marginTop: 0 }}>Sent. The client can open the event, check each section and confirm it.</p>}
          {status === 'sending' && (
            <p style={{ marginTop: 0 }}>
              Sending — Gmail hasn’t confirmed it yet. Norm settles this within about ten minutes; sending again with this dialog
              won’t send a second email.
            </p>
          )}
          {status === 'failed' && <p style={{ marginTop: 0, color: 'var(--error)' }}>Gmail refused it: {settled?.error || result.error || 'no reason given'}.</p>}
          {result.portal_url && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <code style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', overflowWrap: 'anywhere', flex: '1 1 240px' }}>{result.portal_url}</code>
              <CopyButton text={result.portal_url} />
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
            <Button variant="primary" onClick={onClose}>Done</Button>
          </div>
        </div>
      ) : (
        <>
          <p style={{ marginTop: 0, fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
            Emails the client a link to their event from the venue mailbox. They sign in with a one-time code sent to the address
            you use here, then check and confirm each section.
          </p>
          {existing && (
            <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              A link was first sent {instantLabel(existing.created_at, timeZone)}
              {existing.last_used_at ? `; the client last opened it ${instantLabel(existing.last_used_at, timeZone)}` : ''}. Sending
              again emails the same link.
            </p>
          )}
          <div style={{ display: 'grid', gap: 12 }}>
            <div>
              <label className="n-label" htmlFor={ids.to}>To</label>
              <input id={ids.to} className="n-input" value={to} onChange={(e) => setTo(e.target.value)} style={{ width: '100%' }} placeholder="client@example.com" />
              {contactEmails.length > 1 && (
                <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Other addresses on file: {contactEmails.filter((e) => e !== to).join(', ')}</div>
              )}
            </div>
            <div>
              <label className="n-label" htmlFor={ids.cc}>Cc (optional)</label>
              <input id={ids.cc} className="n-input" value={cc} onChange={(e) => setCc(e.target.value)} style={{ width: '100%' }} />
            </div>
            <div>
              <label className="n-label" htmlFor={ids.subject}>Subject (optional)</label>
              <input id={ids.subject} className="n-input" value={subject} onChange={(e) => setSubject(e.target.value)} style={{ width: '100%' }}
                placeholder={`${detail.event.name || 'Your event'} — details to confirm`} />
            </div>
            <div>
              <label className="n-label" htmlFor={ids.body}>Message (optional)</label>
              <textarea id={ids.body} className="n-input" rows={5} value={message} onChange={(e) => setMessage(e.target.value)} style={{ width: '100%' }}
                placeholder="Leave blank for Norm’s standard message." />
              <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                Write {'{{portal_link}}'} where the link should go; otherwise it’s added at the end.
              </div>
            </div>
          </div>
          {error && (
            <div role="alert" style={{ marginTop: 12, padding: '8px 12px', fontSize: 'var(--fs-sm)', color: 'var(--error)', background: 'var(--error-bg)', borderRadius: 'var(--radius)' }}>
              {error.text}
              {error.mailbox && (
                <div style={{ marginTop: 6, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                  <Button variant="link" onClick={mailboxesLink}>
                    Connect the venue mailbox <Icon icon={ExternalLink} size={12} />
                  </Button>
                  {onCopyLink && <Button variant="link" onClick={onCopyLink}>Copy the link to send it yourself</Button>}
                </div>
              )}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 18 }}>
            <Button onClick={onClose} disabled={busy}>Cancel</Button>
            <Button variant="primary" icon={Send} onClick={send} disabled={busy} aria-busy={busy || undefined}>
              {busy ? 'Sending…' : 'Send'}
            </Button>
          </div>
        </>
      )}
    </Dialog>
  );
}

interface PortalLinkResult {
  url: string;
  emails: string[];
  link_sent_at: string;
}

/**
 * "Copy client link": for when the venue mailbox isn't connected (or staff
 * would rather write the email themselves). Norm emails nothing — it allows
 * the addresses into the portal, records the link as sent, and hands back
 * the link to paste into your own email.
 */
export function CopyClientLinkDialog({ detail, timeZone, onDone, onClose }: {
  detail: EventDetail;
  timeZone: string;
  onDone: () => void;
  onClose: () => void;
}) {
  const emailsId = useId();
  const [emails, setEmails] = useState(detail.contact?.email ?? detail.contact?.emails?.[0]?.email ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PortalLinkResult | null>(null);
  const [copied, setCopied] = useState<boolean | null>(null);

  const make = async () => {
    setBusy(true);
    setError(null);
    try {
      const list = splitEmails(emails);
      const res = await fnSend<PortalLinkResult>('POST', `/events/${detail.event.id}/portal-link`, list.length ? { emails: list } : {});
      setResult(res);
      onDone();
      try {
        await navigator.clipboard.writeText(res.url);
        setCopied(true);
      } catch {
        setCopied(false);
      }
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  };

  return (
    <Dialog title="Copy the client’s link" width={520} onClose={() => { if (!busy) onClose(); }}>
      {result ? (
        <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
          <p style={{ marginTop: 0 }}>
            {copied ? 'Copied. ' : ''}Paste the link into your email to the client. They sign in with a one-time code sent to{' '}
            {result.emails.join(', ')}. The event now shows as sent to the client ({instantLabel(result.link_sent_at, timeZone)}).
          </p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <code style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', overflowWrap: 'anywhere', flex: '1 1 240px' }}>{result.url}</code>
            <CopyButton text={result.url} label={copied ? 'Copy again' : 'Copy link'} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
            <Button variant="primary" onClick={onClose}>Done</Button>
          </div>
        </div>
      ) : (
        <>
          <p style={{ marginTop: 0, fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
            For when the venue mailbox isn’t connected, or you’d rather write the email yourself. Norm doesn’t send anything: it
            gives you the link to the client’s event page to paste into your own email, and marks the event as sent to the client.
          </p>
          <label className="n-label" htmlFor={emailsId}>Who may sign in</label>
          <input id={emailsId} className="n-input" value={emails} onChange={(e) => setEmails(e.target.value)} style={{ width: '100%' }} placeholder="client@example.com" />
          <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            They get a one-time code at this address when they open the link. Separate several with commas.
          </div>
          {error && (
            <div role="alert" style={{ marginTop: 12, padding: '8px 12px', fontSize: 'var(--fs-sm)', color: 'var(--error)', background: 'var(--error-bg)', borderRadius: 'var(--radius)' }}>
              {error}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 18 }}>
            <Button onClick={onClose} disabled={busy}>Cancel</Button>
            <Button variant="primary" icon={Copy} onClick={make} disabled={busy} aria-busy={busy || undefined}>
              {busy ? 'Getting the link…' : 'Get and copy the link'}
            </Button>
          </div>
        </>
      )}
    </Dialog>
  );
}
