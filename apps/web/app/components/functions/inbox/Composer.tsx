'use client';

/**
 * The email composer — reply, reply all, forward or a new email from a venue
 * mailbox — and its Norm draft.
 *
 * Drafts live in Norm (Save draft) and can be handed to the mailbox (Send to
 * Gmail): Gmail then owns it, the copy here is locked, and "Bring back from
 * Gmail" takes it back. Sends go through Norm's outbox with an idempotency
 * key kept for this composer (useSendFlow): a 202 is polled, a pricing
 * warning asks for a reason and resends the same email, an unconnected
 * mailbox points at Settings. The quoted history and the signature are shown
 * read-only under the editor — the server appends them when it sends.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { LoaderCircle, Send, X } from 'lucide-react';
import { ConfirmDialog } from '../../ui/Dialog';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import { FnApiError, fnGet } from '../shared/api';
import { errorText, fieldLabel, MailboxNotice, Notice, ReadOnlyHint, type NoticeTone } from './bits';
import { cleanTemplateHtml } from './cleanHtml';
import {
  composerActions,
  contentFingerprint,
  draftFields,
  EMPTY_FIELDS,
  fieldsFromDraft,
  READ_ONLY_HINT,
  sendProblem,
  type ComposerFields,
} from './composer';
import { inboxApi } from './inboxApi';
import { RichTextField } from './RichText';
import SandboxedHtml from './SandboxedHtml';
import TemplatePicker from './TemplatePicker';
import { fillTemplate, firstName, isBlankHtml, mailboxReady, unfilledBlanks } from './text';
import type { ComposeMode, Draft, Mailbox, OutboxResult, PreparedCompose, ReplyTemplate, TemplateStage } from './types';
import { useSendFlow } from './useSendFlow';

export const MODE_LABEL: Record<ComposeMode, string> = {
  reply: 'Reply',
  reply_all: 'Reply all',
  forward: 'Forward',
  new: 'New email',
};

export interface ComposerProps {
  accountId: string;
  mailbox: Mailbox | null;
  mode: ComposeMode;
  /** The conversation answered or forwarded (null for a new email). */
  threadId: string | null;
  /** The message answered (null = the conversation's last). */
  messageId?: string | null;
  enquiryId?: string | null;
  eventId?: string | null;
  /** An open draft to continue. */
  draft?: Draft | null;
  /** Starting values for a new email (e.g. the enquiry's contact). */
  initial?: Partial<ComposerFields>;
  /** To / subject when Gmail can't be asked (unconnected mailbox). */
  fallback?: { to: string[]; subject: string };
  venueId: string | null;
  bookingType?: string | null;
  templateStage: TemplateStage;
  clientName?: string | null;
  senderName?: string | null;
  canWrite: boolean;
  onSent: (result: OutboxResult) => void;
  onClose: () => void;
  /** A draft was saved, handed over, brought back or discarded. */
  onDraftChanged?: () => void;
  /** This draft was discarded (hide it before the reload says so). */
  onDraftDiscarded?: (draftId: string) => void;
}

type Busy = null | 'save' | 'gmail' | 'bring' | 'discard' | 'reload';

export default function Composer(props: ComposerProps) {
  const { accountId, mailbox, mode, threadId, canWrite } = props;
  const [fields, setFields] = useState<ComposerFields>(() =>
    props.draft ? fieldsFromDraft(props.draft) : { ...EMPTY_FIELDS, ...props.initial },
  );
  const [resetKey, setResetKey] = useState(0);
  const [draft, setDraftState] = useState<Draft | null>(props.draft ?? null);
  // The send runner reads the draft through a ref: a retry runs the same
  // closure, and must PATCH the version the last save returned.
  const draftRef = useRef<Draft | null>(props.draft ?? null);
  const [savedFp, setSavedFp] = useState<string | null>(() =>
    props.draft ? contentFingerprint(mode, threadId, fieldsFromDraft(props.draft)) : null,
  );
  const [prepared, setPrepared] = useState<PreparedCompose | null>(null);
  // Why Gmail couldn't give the defaults; `mailbox` = the mailbox can't be used.
  const [prepareProblem, setPrepareProblem] = useState<{ message: string; mailbox: boolean } | null>(null);
  const [showCc, setShowCc] = useState(() => !!(props.draft && (props.draft.cc.length || props.draft.bcc.length)));
  const [showQuoted, setShowQuoted] = useState(false);
  const [busy, setBusy] = useState<Busy>(null);
  const [note, setNote] = useState<{ tone: NoticeTone; text: string } | null>(null);
  const [reason, setReason] = useState('');
  const [confirm, setConfirm] = useState<null | 'discard' | 'close'>(null);
  const [pendingTemplate, setPendingTemplate] = useState<ReplyTemplate | null>(null);
  const flow = useSendFlow();
  const { onSent } = props;

  const setDraft = (d: Draft | null) => {
    draftRef.current = d;
    setDraftState(d);
  };

  // Defaults from the live thread: recipients, subject, quoted history and the
  // mailbox signature. An unconnected mailbox can't be asked — then the
  // fallback fills To/Subject so a draft can still be written.
  useEffect(() => {
    let alive = true;
    inboxApi
      .prepare({ account_id: accountId, mode, gmail_thread_id: threadId, message_id: props.messageId ?? null })
      .then((p) => {
        if (!alive) return;
        setPrepared(p);
        if (!props.draft) {
          setFields((f) => ({
            ...f,
            to: f.to || p.to.join(', '),
            cc: f.cc || p.cc.join(', '),
            subject: f.subject || p.subject,
          }));
          if (p.cc.length) setShowCc(true);
        }
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setPrepareProblem({
          message: errorText(err),
          mailbox: err instanceof FnApiError && err.code === 'mailbox_unavailable',
        });
        if (!props.draft && props.fallback) {
          const fb = props.fallback;
          setFields((f) => ({ ...f, to: f.to || fb.to.join(', '), subject: f.subject || fb.subject }));
        }
      });
    return () => {
      alive = false;
    };
    // Once per composer: its mode, thread and draft are fixed for its life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Whatever path finished the send (first try, a poll, "check again") —
  // reported once per result, however often the parent re-renders.
  const reported = useRef<OutboxResult | null>(null);
  useEffect(() => {
    if (flow.phase.kind !== 'sent' || reported.current === flow.phase.result) return;
    reported.current = flow.phase.result;
    onSent(flow.phase.result);
  }, [flow.phase, onSent]);

  const sending = flow.phase.kind === 'busy' || (flow.phase.kind === 'sending' && !flow.phase.stuck);
  const ready = mailboxReady(mailbox?.health) && !prepareProblem?.mailbox;
  const actions = composerActions({
    canWrite,
    mailboxReady: ready,
    mode,
    draft,
    busy: busy !== null || sending || flow.phase.kind === 'sending',
  });
  const locked = draft?.owner === 'gmail';
  const fp = contentFingerprint(mode, threadId, fields);
  const dirty = draft ? fp !== savedFp : !isBlankHtml(fields.body);
  const blanks = unfilledBlanks(fields.body);
  const inReplyTo = draft?.in_reply_to_gmail_message_id ?? prepared?.in_reply_to_gmail_message_id ?? props.messageId ?? null;

  const update = (patch: Partial<ComposerFields>) => {
    setFields((f) => ({ ...f, ...patch }));
    if (note?.tone === 'ok') setNote(null);
  };

  const fail = (err: unknown) => setNote({ tone: 'error', text: errorText(err) });

  const saveDraft = async (): Promise<Draft | null> => {
    setBusy('save');
    setNote(null);
    try {
      const current = draftRef.current;
      const d = current
        ? await inboxApi.updateDraft(current.id, current.version, draftFields(fields, mode))
        : await inboxApi.createDraft({
            account_id: accountId,
            kind: 'email',
            gmail_thread_id: mode === 'new' ? null : threadId,
            in_reply_to_gmail_message_id: mode === 'new' ? null : inReplyTo,
            enquiry_id: props.enquiryId ?? null,
            event_id: props.eventId ?? null,
            ...draftFields(fields, mode),
          });
      setDraft(d);
      setSavedFp(fp);
      setNote({ tone: 'ok', text: 'Draft saved.' });
      props.onDraftChanged?.();
      return d;
    } catch (err) {
      fail(err);
      return null;
    } finally {
      setBusy(null);
    }
  };

  const sendToGmail = async () => {
    const d = dirty || !draftRef.current ? await saveDraft() : draftRef.current;
    if (!d) return;
    setBusy('gmail');
    try {
      setDraft(await inboxApi.draftToGmail(d.id, d.version));
      setNote({ tone: 'info', text: 'Handed to Gmail. Finish and send it there — or bring it back to edit it here.' });
      props.onDraftChanged?.();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const loadDraft = (d: Draft) => {
    setDraft(d);
    const f = fieldsFromDraft(d);
    setFields(f);
    setSavedFp(contentFingerprint(mode, threadId, f));
    setResetKey((k) => k + 1);
  };

  const bringBack = async () => {
    const d = draftRef.current;
    if (!d) return;
    setBusy('bring');
    setNote(null);
    try {
      const back = await inboxApi.bringBack(d.id);
      props.onDraftChanged?.();
      if (back.status === 'sent') {
        setDraft(back);
        setNote({ tone: 'ok', text: 'It was already sent from Gmail.' });
      } else {
        loadDraft(back);
        setNote({ tone: 'ok', text: 'Back in Norm — edit it here.' });
      }
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const reloadDraft = async () => {
    const d = draftRef.current;
    if (!d) return;
    setBusy('reload');
    try {
      loadDraft(await fnGet<Draft>(`/inbox/drafts/${encodeURIComponent(d.id)}`));
      flow.reset();
      setNote({ tone: 'info', text: 'Reloaded the latest version of this draft.' });
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const discard = async () => {
    const d = draftRef.current;
    if (d) {
      await inboxApi.discardDraft(d.id, d.version);
      props.onDraftDiscarded?.(d.id);
      props.onDraftChanged?.();
    }
    props.onClose();
  };

  const send = async () => {
    setNote(null);
    const problem = sendProblem(mode, fields);
    if (problem) {
      setNote({ tone: 'error', text: problem });
      return;
    }
    const snapshot = fields;
    await flow.send(fp, async (key, overrideReason) => {
      const current = draftRef.current;
      if (current) {
        // A saved draft is sent as the draft (so it is marked sent) — with
        // whatever was typed since saving.
        const d = await inboxApi.updateDraft(current.id, current.version, draftFields(snapshot, mode));
        setDraft(d);
        return inboxApi.sendDraft(d.id, { idempotency_key: key, price_override_reason: overrideReason });
      }
      const body = draftFields(snapshot, mode);
      return inboxApi.composeSend({
        account_id: accountId,
        mode,
        to: body.to,
        cc: body.cc,
        bcc: body.bcc,
        subject: body.subject || null,
        body_html: body.body_html,
        gmail_thread_id: mode === 'new' ? null : threadId,
        in_reply_to_gmail_message_id: mode === 'new' ? null : inReplyTo,
        enquiry_id: props.enquiryId ?? null,
        event_id: props.eventId ?? null,
        idempotency_key: key,
        price_override_reason: overrideReason,
      });
    });
  };

  const insertTemplate = (t: ReplyTemplate) => {
    const values = { client_name: firstName(props.clientName), sender_name: firstName(props.senderName) || props.senderName };
    const body = fillTemplate(cleanTemplateHtml(t.body_html), values);
    setFields((f) => ({ ...f, body, subject: f.subject || (t.subject ? fillTemplate(t.subject, values) : f.subject) }));
    setResetKey((k) => k + 1);
    setPendingTemplate(null);
  };

  const close = () => {
    if (dirty && !locked && canWrite) setConfirm(draft ? 'close' : 'discard');
    else props.onClose();
  };

  const phase = flow.phase;
  const mailboxProblem =
    phase.kind === 'mailbox'
      ? phase.message
      : !ready
        ? (prepareProblem?.message ?? `${mailbox?.google_email ?? 'This mailbox'} isn’t connected to Norm yet.`)
        : null;
  const quoted = prepared?.quoted_html;
  const signature = prepared?.signature_html;
  // `extra` sits inside the field's right end (Gmail's "Cc Bcc"), so every row lines up.
  const fieldRow = (label: string, input: ReactNode, extra?: ReactNode) => (
    <div style={{ display: 'grid', gridTemplateColumns: '58px minmax(0, 1fr)', alignItems: 'center', gap: 8 }}>
      <span style={fieldLabel}>{label}</span>
      <span style={{ position: 'relative', display: 'block', minWidth: 0 }}>
        {input}
        {extra && <span style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)' }}>{extra}</span>}
      </span>
    </div>
  );
  const input = (value: string, onChange: (v: string) => void, label: string, placeholder?: string) => (
    <input
      className="n-input"
      aria-label={label}
      value={value}
      placeholder={placeholder}
      readOnly={!actions.editable}
      onChange={(e) => onChange(e.target.value)}
      style={{ width: '100%', minWidth: 0, paddingRight: label === 'To' && !showCc ? 64 : undefined }}
    />
  );

  return (
    <section
      className="n-card"
      aria-label={MODE_LABEL[mode]}
      data-testid="fn-composer"
      style={{ padding: '12px 14px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <h3 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600 }}>{locked ? 'Draft in Gmail' : MODE_LABEL[mode]}</h3>
        {locked && <Badge tone="info">In Gmail</Badge>}
        {draft && !locked && draft.status === 'open' && <Badge tone="info">Draft</Badge>}
        <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          from {mailbox?.google_email ?? prepared?.from ?? 'the venue mailbox'}
        </span>
        <IconButton icon={X} label="Close" onClick={close} style={{ marginLeft: 'auto' }} />
      </div>

      {fieldRow(
        'To',
        input(fields.to, (v) => update({ to: v }), 'To', mode === 'reply' || mode === 'reply_all' ? 'Whoever wrote last' : 'name@example.com'),
        !showCc && actions.editable ? (
          <button type="button" className="n-btn n-btn--link" onClick={() => setShowCc(true)} style={{ fontSize: 'var(--fs-sm)' }}>
            Cc/Bcc
          </button>
        ) : undefined,
      )}
      {showCc && fieldRow('Cc', input(fields.cc, (v) => update({ cc: v }), 'Cc'))}
      {showCc && fieldRow('Bcc', input(fields.bcc, (v) => update({ bcc: v }), 'Bcc'))}
      {fieldRow('Subject', input(fields.subject, (v) => update({ subject: v }), 'Subject', mode === 'new' || mode === 'forward' ? '' : 'Re: …'))}

      {actions.editable ? (
        <RichTextField
          html={fields.body}
          resetKey={`${draft?.id ?? 'new'}-${resetKey}`}
          onChange={(body) => update({ body })}
          placeholder="Write your message"
          toolbarExtra={
            <TemplatePicker
              venueId={props.venueId}
              bookingType={props.bookingType}
              stage={props.templateStage}
              onPick={(t) => (isBlankHtml(fields.body) ? insertTemplate(t) : setPendingTemplate(t))}
            />
          }
        />
      ) : (
        <div style={{ padding: '8px 10px', border: '1px solid var(--line)', borderRadius: 'var(--radius)', background: 'var(--surface)' }}>
          {isBlankHtml(fields.body) ? (
            <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>No message yet.</span>
          ) : (
            <SandboxedHtml html={fields.body} title="Draft message" />
          )}
        </div>
      )}

      {actions.editable && blanks.length > 0 && (
        <Notice tone="warn">Fill in the blanks before sending: {blanks.join(', ')}</Notice>
      )}

      {quoted && (
        <div>
          <button type="button" className="n-btn n-btn--link" onClick={() => setShowQuoted((v) => !v)} style={{ fontSize: 'var(--fs-sm)' }}>
            {showQuoted ? 'Hide the quoted conversation' : 'Show the quoted conversation'}
          </button>
          <span style={{ marginLeft: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>added when it’s sent</span>
          {showQuoted && (
            <div style={{ marginTop: 6, padding: '8px 10px', borderLeft: '2px solid var(--line-strong)', background: 'var(--surface)' }}>
              <SandboxedHtml html={quoted} title="Quoted conversation" />
            </div>
          )}
        </div>
      )}
      {signature && (
        <div>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 4 }}>Signature — added when it’s sent</div>
          <div style={{ padding: '8px 10px', border: '1px dashed var(--line)', borderRadius: 'var(--radius)', maxHeight: 220, overflow: 'auto' }}>
            <SandboxedHtml html={signature} title="Mailbox signature" />
          </div>
        </div>
      )}

      {mailboxProblem && <MailboxNotice message={mailboxProblem} />}
      {prepareProblem && !prepareProblem.mailbox && (
        <Notice tone="info">Couldn’t read the conversation from Gmail ({prepareProblem.message}) — the quoted history is still added when it’s sent.</Notice>
      )}
      <SendPhaseNotice
        phase={phase}
        reason={reason}
        onReason={setReason}
        onOverride={() => flow.override(reason.trim())}
        onEdit={flow.reset}
        onRetry={flow.retry}
        onCheckAgain={flow.checkAgain}
        onReload={draft ? reloadDraft : undefined}
      />
      {note && <Notice tone={note.tone}>{note.text}</Notice>}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {locked ? (
          <Button variant="primary" onClick={bringBack} disabled={!actions.bringBack}>
            {busy === 'bring' ? 'Bringing back…' : 'Bring back from Gmail'}
          </Button>
        ) : (
          <>
            <Button variant="primary" icon={Send} onClick={send} disabled={!actions.send} data-testid="fn-composer-send">
              {sending ? 'Sending…' : 'Send'}
            </Button>
            <Button onClick={saveDraft} disabled={!actions.save || (!!draft && !dirty)}>
              {busy === 'save' ? 'Saving…' : draft && !dirty ? 'Saved' : 'Save draft'}
            </Button>
            <Button onClick={sendToGmail} disabled={!actions.toGmail}>
              {busy === 'gmail' ? 'Handing over…' : 'Send to Gmail'}
            </Button>
          </>
        )}
        {(draft || dirty) && actions.discard && (
          <Button variant="quiet" onClick={() => setConfirm('discard')} style={{ marginLeft: 'auto' }}>
            Discard
          </Button>
        )}
      </div>
      {actions.hint && (actions.hint === READ_ONLY_HINT ? <ReadOnlyHint /> : (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{actions.hint}</div>
      ))}

      {confirm === 'discard' && (
        <ConfirmDialog
          title={draft ? 'Discard this draft?' : 'Discard this email?'}
          confirmLabel="Discard"
          busyLabel="Discarding…"
          danger
          onConfirm={async () => {
            try {
              await discard();
            } catch (err) {
              throw new Error(errorText(err));
            }
          }}
          onClose={() => setConfirm(null)}
        >
          {draft?.owner === 'gmail'
            ? 'It is deleted from Gmail too.'
            : draft
              ? 'The saved draft is deleted. This can’t be undone.'
              : 'What you’ve written here is thrown away.'}
        </ConfirmDialog>
      )}
      {confirm === 'close' && (
        <ConfirmDialog
          title="Close without saving?"
          confirmLabel="Close"
          cancelLabel="Keep editing"
          onConfirm={async () => props.onClose()}
          onClose={() => setConfirm(null)}
        >
          Your saved draft stays; the changes since you last saved are lost.
        </ConfirmDialog>
      )}
      {pendingTemplate && (
        <ConfirmDialog
          title="Replace your message?"
          confirmLabel="Use the template"
          onConfirm={async () => insertTemplate(pendingTemplate)}
          onClose={() => setPendingTemplate(null)}
        >
          “{pendingTemplate.name}” replaces what you’ve written so far.
        </ConfirmDialog>
      )}
    </section>
  );
}

/** What happened to the last send attempt, with the way forward. */
export function SendPhaseNotice({
  phase,
  reason,
  onReason,
  onOverride,
  onEdit,
  onRetry,
  onCheckAgain,
  onReload,
}: {
  phase: ReturnType<typeof useSendFlow>['phase'];
  reason: string;
  onReason: (v: string) => void;
  onOverride: () => void;
  onEdit: () => void;
  onRetry: () => void;
  onCheckAgain: () => void;
  onReload?: () => void;
}) {
  switch (phase.kind) {
    case 'pricing':
      return (
        <div
          role="alert"
          data-testid="fn-pricing-warning"
          style={{ padding: '10px 12px', borderRadius: 'var(--radius)', background: 'var(--warn-bg)', color: 'var(--warn)' }}
        >
          <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>This email quotes a price</div>
          <div style={{ fontSize: 'var(--fs-sm)', marginTop: 2 }}>{phase.message}</div>
          <label style={{ display: 'block', marginTop: 8 }}>
            <span className="n-label" style={{ color: 'var(--warn)' }}>
              Reason to send it anyway (recorded with the email)
            </span>
            <textarea
              className="n-input"
              rows={2}
              value={reason}
              onChange={(e) => onReason(e.target.value)}
              placeholder="e.g. The customer asked for a rough price on the phone"
              style={{ width: '100%' }}
            />
          </label>
          <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
            <Button variant="primary" size="sm" onClick={onOverride} disabled={!reason.trim()}>
              Send anyway
            </Button>
            <Button size="sm" onClick={onEdit}>
              Edit the email
            </Button>
          </div>
        </div>
      );
    case 'sending':
      return (
        <Notice
          tone="info"
          title={phase.stuck ? 'Still sending' : 'Sending…'}
          action={
            phase.stuck ? (
              <Button size="sm" onClick={onCheckAgain}>
                Check again
              </Button>
            ) : (
              <Icon icon={LoaderCircle} size={16} style={{ animation: 'n-spin 1s linear infinite' }} />
            )
          }
        >
          {phase.stuck
            ? 'Gmail hasn’t confirmed it yet. Norm’s outbox finishes it within about ten minutes — it is never sent twice, and you can leave this page.'
            : 'Waiting for Gmail to confirm.'}
        </Notice>
      );
    case 'failed':
      return (
        <Notice tone="error" title="Not sent" action={<Button size="sm" onClick={onRetry}>Try again</Button>}>
          {phase.message}
        </Notice>
      );
    case 'conflict':
      return (
        <Notice tone="error" action={onReload ? <Button size="sm" onClick={onReload}>Reload</Button> : undefined}>
          {phase.message}
        </Notice>
      );
    case 'not_live':
      return <Notice tone="warn">{READ_ONLY_HINT} {phase.message}</Notice>;
    case 'invalid':
    case 'error':
      return <Notice tone="error">{phase.message}</Notice>;
    case 'sent':
      return <Notice tone="ok">Sent.</Notice>;
    default:
      return null;
  }
}

