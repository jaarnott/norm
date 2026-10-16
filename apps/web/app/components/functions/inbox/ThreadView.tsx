'use client';

/**
 * One conversation (`?mail_thread=<account>:<thread>`): its messages oldest
 * first (bodies live from Gmail, sanitised, in sandboxed frames — or the
 * mirrored summary while the mailbox isn't connected), its folder, the
 * enquiry or event it is linked to, its Norm drafts, and the composer.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, ExternalLink, Forward, Link2, LoaderCircle, Paperclip, Plus, Reply, ReplyAll, Unlink } from 'lucide-react';
import { ConfirmDialog } from '../../ui/Dialog';
import Avatar from '../../ui/Avatar';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { FnApiError, fnGet } from '../shared/api';
import type { FnMeta } from '../shared/meta';
import { errorText, fieldLabel, MailboxNotice, Notice, ReadOnlyHint, type NoticeTone } from './bits';
import Composer from './Composer';
import { draftMode } from './composer';
import { CreateEnquiryDialog, LinkPicker } from './dialogs';
import { inboxApi } from './inboxApi';
import { openEnquiry, openEvent, openMailboxSettings } from './nav';
import SandboxedHtml from './SandboxedHtml';
import {
  agoLabel,
  decodeEntities,
  FOLDER_LABELS,
  fileSize,
  htmlToText,
  latestCustomer,
  mailboxReady,
  messageDateLabel,
  replySubject,
  senderLabel,
  templateStageFor,
} from './text';
import type { Attachment, ComposeMode, Draft, Folder, Mailbox, Message, OutboxResult, ThreadDetail, ThreadLink } from './types';
import { FOLDERS } from './types';

interface EnquiryContext {
  enquiry: { id: string; booking_type: string | null; subject: string | null };
  contact: { name: string | null } | null;
}

type ComposerState = { mode: ComposeMode; messageId: string | null; draft: Draft | null; key: number } | null;

export default function ThreadView({
  accountId,
  threadId,
  meta,
  mailboxes,
  canWrite,
  initialSubject,
  onBack,
  onChanged,
  now,
}: {
  accountId: string;
  threadId: string;
  meta: FnMeta;
  mailboxes: Mailbox[];
  canWrite: boolean;
  initialSubject?: string | null;
  onBack: () => void;
  /** Something the list shows changed (folder, link, draft, a reply). */
  onChanged: () => void;
  now: Date;
}) {
  const [tick, setTick] = useState(0);
  const key = `${accountId}|${threadId}|${tick}`;
  const [result, setResult] = useState<{ key: string; data?: ThreadDetail; error?: unknown } | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [composer, setComposer] = useState<ComposerState>(null);
  const [enquiryCtx, setEnquiryCtx] = useState<EnquiryContext | null>(null);
  const [notice, setNotice] = useState<{ tone: NoticeTone; text: string; enquiryId?: string } | null>(null);
  const [folderBusy, setFolderBusy] = useState(false);
  const [dialog, setDialog] = useState<null | 'link' | 'create' | { unlink: ThreadLink }>(null);
  const { isMobile } = useBreakpoint();
  const [discarded, setDiscarded] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    const [a, t] = key.split('|');
    inboxApi
      .thread(a, t)
      .then((data) => alive && setResult({ key, data }))
      .catch((error) => alive && setResult({ key, error }));
    return () => {
      alive = false;
    };
  }, [key]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  // Keep showing the last good copy while a reload runs.
  const data = result?.data && result.data.gmail_thread_id === threadId ? result.data : null;
  const error = result?.key === key ? result.error : null;
  const mailbox = mailboxes.find((m) => m.id === accountId) ?? null;
  const ready = mailboxReady(mailbox?.health);
  const venue = meta.venues.find((v) => v.id === (data?.venue_id ?? mailbox?.venue_id));
  const timeZone = venue?.timezone || meta.company.default_timezone;
  const ownAddresses = useMemo(() => mailboxes.map((m) => m.google_email), [mailboxes]);
  const enquiryLink = data?.links.find((l) => l.visible && l.enquiry_id) ?? null;
  const enquiryId = enquiryLink?.enquiry_id ?? null;

  // The linked enquiry's booking type and customer, for the template picker
  // and its {{client_name}} — read once a composer opens.
  useEffect(() => {
    if (!composer || !enquiryId || enquiryCtx?.enquiry.id === enquiryId) return;
    let alive = true;
    fnGet<EnquiryContext>(`/enquiries/${encodeURIComponent(enquiryId)}`)
      .then((d) => alive && setEnquiryCtx(d))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [composer, enquiryId, enquiryCtx]);

  if (!data) {
    const header = <PageHeader title={decodeEntities(initialSubject) || 'Conversation'} back={{ label: 'Inbox', onClick: onBack }} />;
    if (error) {
      const code = error instanceof FnApiError ? error.code : null;
      return (
        <div>
          {header}
          {code === 'mailbox_unavailable' ? (
            <MailboxNotice message={errorText(error)} />
          ) : (
            <PageState
              kind="error"
              title={code === 'not_found' ? 'This conversation isn’t available' : 'Couldn’t open this conversation'}
              detail={errorText(error)}
              action={code === 'not_found' ? undefined : <Button size="sm" onClick={reload}>Try again</Button>}
            />
          )}
        </div>
      );
    }
    return (
      <div>
        {header}
        <PageState kind="loading" title="Opening the conversation…" />
      </div>
    );
  }

  const messages = data.messages;
  const subject = decodeEntities(messages[0]?.subject ?? initialSubject) || '(no subject)';
  const last = messages[messages.length - 1];
  const isOpen = (m: Message) => expanded[m.gmail_message_id] ?? (m === last || !!m.unread);
  const customer = latestCustomer(messages, ownAddresses);
  const visibleLinks = data.links.filter((l) => l.visible);
  const hiddenLinks = data.links.length - visibleLinks.length;
  const openDrafts = data.drafts.filter((d) => d.status === 'open' && d.id !== composer?.draft?.id && !discarded.includes(d.id));

  const changed = () => {
    reload();
    onChanged();
  };

  const startCompose = (mode: ComposeMode, messageId: string | null = null, draft: Draft | null = null) => {
    setNotice(null);
    setComposer({ mode, messageId, draft, key: Date.now() });
  };

  const setFolder = async (value: string) => {
    const folder = (value || null) as Folder | null;
    setFolderBusy(true);
    setNotice(null);
    try {
      const r = await inboxApi.setFolder(accountId, threadId, folder);
      setResult((prev) => (prev?.data ? { ...prev, data: { ...prev.data, folder: r.folder, folder_sync_error: r.gmail_error } } : prev));
      if (r.gmail_error) setNotice({ tone: 'warn', text: `Filed in Norm. ${r.gmail_error}` });
      onChanged();
    } catch (err) {
      setNotice({ tone: 'error', text: errorText(err) });
    } finally {
      setFolderBusy(false);
    }
  };

  const onSent = (r: OutboxResult) => {
    setComposer(null);
    setNotice({
      tone: 'ok',
      text: r.duplicate ? 'That email had already been sent.' : r.status === 'sent' ? 'Sent.' : 'Sending — the outbox will finish it.',
    });
    changed();
  };

  const composeMode = composer?.mode;
  return (
    <div>
      <PageHeader
        title={subject}
        back={{ label: 'Inbox', onClick: onBack }}
        meta={[mailbox?.venue_name ?? venue?.name, data.mailbox, `${messages.length} message${messages.length === 1 ? '' : 's'}`]
          .filter(Boolean)
          .join(' · ')}
        status={data.folder ? <Badge>{FOLDER_LABELS[data.folder]}</Badge> : undefined}
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* Filing and records */}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <span style={fieldLabel}>Folder</span>
            <select
              className="n-select"
              aria-label="Folder"
              value={data.folder ?? ''}
              disabled={!canWrite || folderBusy}
              onChange={(e) => setFolder(e.target.value)}
            >
              <option value="">Unfiled</option>
              {FOLDERS.map((f) => (
                <option key={f} value={f}>
                  {FOLDER_LABELS[f]}
                  {mailbox && mailbox.folders[f] === false ? ' (no Gmail label)' : ''}
                </option>
              ))}
            </select>
          </label>
          {visibleLinks.map((l) => (
            <span
              key={l.link_id}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 2, maxWidth: '100%', padding: '2px 2px 2px 10px', border: '1px solid var(--line)', borderRadius: 999, background: 'var(--bg)' }}
            >
              <Icon icon={Link2} size={14} tone="muted" />
              <button
                type="button"
                className="n-btn n-btn--link"
                onClick={() => (l.event_id ? openEvent(l.event_id) : l.enquiry_id && openEnquiry(l.enquiry_id))}
                style={{ marginLeft: 4, fontSize: 'var(--fs-sm)', textDecoration: 'none', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}
                title={l.event_id ? 'Open the event' : 'Open the enquiry'}
              >
                {l.event_id ? 'Event' : 'Enquiry'}: {l.label || l.contact_name || 'untitled'}
              </button>
              {canWrite && <IconButton icon={Unlink} label="Unlink" iconSize={14} onClick={() => setDialog({ unlink: l })} />}
            </span>
          ))}
          {hiddenLinks > 0 && (
            <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Linked to a record at a venue you can’t see</span>
          )}
          <span style={{ display: 'inline-flex', gap: 8, flexWrap: 'wrap', marginLeft: 'auto' }}>
            <Button size="sm" icon={Link2} onClick={() => setDialog('link')} disabled={!canWrite}>
              Link to enquiry or event
            </Button>
            {!enquiryLink && (
              <Button size="sm" icon={Plus} onClick={() => setDialog('create')} disabled={!canWrite}>
                Create enquiry
              </Button>
            )}
          </span>
        </div>
        {!canWrite && <ReadOnlyHint />}
        {data.folder_sync_error && !notice && <Notice tone="warn">{data.folder_sync_error}</Notice>}
        {notice && (
          <Notice
            tone={notice.tone}
            action={
              notice.enquiryId ? (
                <Button size="sm" icon={ExternalLink} onClick={() => openEnquiry(notice.enquiryId!)}>
                  Open enquiry
                </Button>
              ) : undefined
            }
          >
            {notice.text}
          </Notice>
        )}

        {!data.bodies_available && (
          <Notice
            tone={ready ? 'error' : 'info'}
            title={ready ? 'Couldn’t read the messages from Gmail' : 'Showing the conversation summary'}
            action={
              ready ? (
                <Button size="sm" onClick={reload}>
                  Try again
                </Button>
              ) : (
                <Button size="sm" onClick={openMailboxSettings}>
                  Connect in Settings
                </Button>
              )
            }
          >
            {data.bodies_error || 'Connect this mailbox to read its messages.'}
          </Notice>
        )}
        {data.has_gmail_draft && <Notice tone="info">There’s also a draft on this conversation in Gmail.</Notice>}

        {/* Messages, oldest first */}
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }} aria-label="Messages">
          {messages.map((m) => (
            <MessageItem
              key={m.gmail_message_id}
              message={m}
              accountId={accountId}
              threadId={threadId}
              open={isOpen(m)}
              onToggle={() => setExpanded((e) => ({ ...e, [m.gmail_message_id]: !isOpen(m) }))}
              bodiesAvailable={data.bodies_available}
              compact={isMobile}
              timeZone={timeZone}
              now={now}
            />
          ))}
        </ol>

        {/* Drafts on this conversation */}
        {openDrafts.map((d) => (
          <DraftCard key={d.id} draft={d} now={now} onOpen={() => startCompose(draftMode(d), null, d)} />
        ))}

        {composer ? (
          <Composer
            key={composer.key}
            accountId={accountId}
            mailbox={mailbox}
            mode={composer.mode}
            threadId={threadId}
            messageId={composer.messageId}
            enquiryId={enquiryId}
            eventId={visibleLinks.find((l) => l.event_id)?.event_id ?? null}
            draft={composer.draft}
            fallback={{
              to: composeMode === 'forward' ? [] : customer?.from_email ? [customer.from_email] : [],
              subject: composeMode === 'forward' ? `Fwd: ${subject}` : replySubject(subject),
            }}
            venueId={data.venue_id}
            bookingType={enquiryCtx?.enquiry.id === enquiryId ? enquiryCtx?.enquiry.booking_type : null}
            templateStage={templateStageFor(messages)}
            clientName={(enquiryCtx?.enquiry.id === enquiryId ? enquiryCtx?.contact?.name : null) || enquiryLink?.contact_name || customer?.from_name}
            senderName={meta.user.name}
            canWrite={canWrite}
            onSent={onSent}
            onClose={() => setComposer(null)}
            onDraftChanged={changed}
            onDraftDiscarded={(id) => setDiscarded((ids) => [...ids, id])}
          />
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
            <Button variant="primary" icon={Reply} onClick={() => startCompose('reply')} disabled={!canWrite}>
              Reply
            </Button>
            <Button icon={ReplyAll} onClick={() => startCompose('reply_all')} disabled={!canWrite}>
              Reply all
            </Button>
            <Button icon={Forward} onClick={() => startCompose('forward')} disabled={!canWrite}>
              Forward
            </Button>
            {canWrite && !ready && (
              <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                The mailbox isn’t connected — you can write a draft, but not send it yet.
              </span>
            )}
          </div>
        )}
      </div>

      {dialog === 'link' && (
        <LinkPicker
          initialQuery={customer?.from_email ?? ''}
          onPick={async (target) => {
            await inboxApi.link(accountId, threadId, target);
            setNotice({ tone: 'ok', text: 'Linked.' });
            changed();
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'create' && (
        <CreateEnquiryDialog
          meta={meta}
          initial={{
            name: decodeEntities(customer?.from_name) || '',
            email: customer?.from_email ?? '',
            subject,
            venueId: data.venue_id,
          }}
          onCreate={async (body) => {
            const r = await inboxApi.createEnquiry(accountId, threadId, body);
            setNotice({ tone: 'ok', text: 'Enquiry created and linked to this conversation.', enquiryId: r.enquiry_id });
            changed();
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog && typeof dialog === 'object' && (
        <ConfirmDialog
          title="Unlink this conversation?"
          confirmLabel="Unlink"
          busyLabel="Unlinking…"
          onConfirm={async () => {
            try {
              await inboxApi.unlink(dialog.unlink.link_id);
            } catch (err) {
              throw new Error(errorText(err));
            }
            setNotice({ tone: 'ok', text: 'Unlinked.' });
            changed();
          }}
          onClose={() => setDialog(null)}
        >
          Its email stops counting towards {dialog.unlink.label ? `“${dialog.unlink.label}”` : 'that record'} (replies, quotes). You can link it again any time.
        </ConfirmDialog>
      )}
    </div>
  );
}

function MessageItem({
  message: m,
  accountId,
  threadId,
  open,
  onToggle,
  bodiesAvailable,
  compact,
  timeZone,
  now,
}: {
  message: Message;
  accountId: string;
  threadId: string;
  open: boolean;
  onToggle: () => void;
  bodiesAvailable: boolean;
  compact: boolean;
  timeZone: string;
  now: Date;
}) {
  const sender = senderLabel(decodeEntities(m.from_name), m.from_email);
  const recipients = [...m.to, ...m.cc];
  return (
    <li className="n-card" style={{ overflow: 'hidden', borderColor: m.unread ? 'var(--brand-soft)' : undefined }}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 10,
          width: '100%',
          padding: '10px 14px',
          border: 'none',
          background: 'transparent',
          font: 'inherit',
          color: 'inherit',
          textAlign: 'left',
          cursor: 'pointer',
        }}
      >
        {!compact && <Avatar name={sender} size={30} />}
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600 }} title={m.from_email ?? undefined}>
              {sender}
            </span>
            {m.from_name && m.from_email && !compact && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{m.from_email}</span>}
            {m.is_internal && <Badge>Internal</Badge>}
            {m.is_auto_reply && <Badge>Auto-reply</Badge>}
            {m.is_inbound === false && !m.is_internal && <Badge tone="info">Sent</Badge>}
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>
              {messageDateLabel(m.internal_date, timeZone, now)}
              <Icon icon={open ? ChevronDown : ChevronRight} size={14} tone="muted" />
            </span>
          </span>
          <span
            style={{
              display: 'block',
              marginTop: 2,
              fontSize: 'var(--fs-sm)',
              color: 'var(--muted)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: open ? 'normal' : 'nowrap',
            }}
          >
            {open ? (recipients.length ? `to ${recipients.join(', ')}` : '') : decodeEntities(m.snippet)}
          </span>
        </span>
      </button>
      {open && (
        <div style={{ padding: compact ? '0 12px 12px' : '0 14px 12px 54px' }}>
          {m.html ? (
            <SandboxedHtml html={m.html} title={`Message from ${sender}`} foldQuotes />
          ) : m.text ? (
            <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 'var(--fs-base)' }}>{m.text}</div>
          ) : (
            <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
              {decodeEntities(m.snippet)}
              {m.snippet && '…'}
              {!bodiesAvailable && (
                <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>The start of the message — the full text needs the connected mailbox.</div>
              )}
            </div>
          )}
          {m.attachments.length > 0 && <Attachments message={m} accountId={accountId} threadId={threadId} />}
        </div>
      )}
    </li>
  );
}

/** A message's attachments: each chip downloads the file, live from Gmail
 *  (fetched with the bearer token, then saved from a blob). */
function Attachments({ message, accountId, threadId }: { message: Message; accountId: string; threadId: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const download = async (a: Attachment) => {
    const ref = a.part_id || a.attachment_id;
    if (!ref) return;
    setBusy(ref);
    setError(null);
    try {
      const { blob, filename } = await inboxApi.attachment(accountId, threadId, message.gmail_message_id, ref, a.filename);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30_000);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div style={{ marginTop: 10 }}>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexWrap: 'wrap', gap: 6 }} aria-label="Attachments">
        {message.attachments.map((a) => {
          const ref = a.part_id || a.attachment_id;
          return (
            <li key={ref || a.filename}>
              <button
                type="button"
                className="n-btn n-btn--secondary n-btn--sm"
                onClick={() => download(a)}
                disabled={!ref || busy !== null}
                title={`Download ${a.filename || 'the attachment'}`}
                style={{ maxWidth: '100%', fontWeight: 400 }}
              >
                <Icon icon={busy === ref ? LoaderCircle : Paperclip} size={14} tone="muted" style={busy === ref ? { animation: 'n-spin 1s linear infinite' } : undefined} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.filename || 'Attachment'}</span>
                <span style={{ color: 'var(--muted)' }}>{fileSize(a.size)}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {error && (
        <div style={{ marginTop: 6 }}>
          <Notice tone="error">{error}</Notice>
        </div>
      )}
    </div>
  );
}

function DraftCard({ draft, now, onOpen }: { draft: Draft; now: Date; onOpen: () => void }) {
  const inGmail = draft.owner === 'gmail';
  const preview = htmlToText(draft.body_html).slice(0, 160);
  return (
    <div className="n-card" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: '10px 14px', borderStyle: 'dashed' }}>
      <div style={{ flex: '1 1 240px', minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <Badge tone="info">{inGmail ? 'Draft in Gmail' : 'Draft'}</Badge>
          <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            {[draft.created_by_label, draft.updated_at ? `saved ${agoLabel(draft.updated_at, now)}` : null].filter(Boolean).join(' · ')}
          </span>
        </div>
        <div style={{ marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {preview || 'No message yet.'}
        </div>
      </div>
      <Button size="sm" onClick={onOpen}>
        {inGmail ? 'Open' : 'Continue editing'}
      </Button>
    </div>
  );
}
