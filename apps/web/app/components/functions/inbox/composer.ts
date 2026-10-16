/**
 * Norm Functions Inbox — the composer's rules, kept pure: which actions a
 * draft allows (one owner at a time: Norm, or Gmail after "Send to Gmail"),
 * what a save or a send carries, and what to fix before sending.
 */
import { fingerprint } from './sendFlow';
import { invalidAddresses, isBlankHtml, parseAddresses } from './text';
import type { ComposeMode, Draft } from './types';

/** What the person is editing — address boxes as typed. */
export interface ComposerFields {
  to: string;
  cc: string;
  bcc: string;
  subject: string;
  body: string;
}

export const EMPTY_FIELDS: ComposerFields = { to: '', cc: '', bcc: '', subject: '', body: '' };

export const READ_ONLY_HINT = 'Read-only while Orbit is live.';

export interface ComposerSituation {
  /** The organisation is live (shadow mode = read-only). */
  canWrite: boolean;
  /** The venue mailbox is connected: it can send and take drafts. */
  mailboxReady: boolean;
  mode: ComposeMode;
  draft: Pick<Draft, 'owner' | 'status'> | null;
  busy: boolean;
}

export interface ComposerActions {
  editable: boolean;
  send: boolean;
  save: boolean;
  toGmail: boolean;
  bringBack: boolean;
  discard: boolean;
  /** Why something is unavailable, as a sentence; null when nothing is. */
  hint: string | null;
}

const NOTHING: ComposerActions = {
  editable: false,
  send: false,
  save: false,
  toGmail: false,
  bringBack: false,
  discard: false,
  hint: null,
};

export function composerActions(s: ComposerSituation): ComposerActions {
  if (!s.canWrite) return { ...NOTHING, hint: READ_ONLY_HINT };
  if (s.draft && s.draft.status !== 'open') {
    return { ...NOTHING, hint: `This draft was already ${s.draft.status}.` };
  }
  if (s.draft?.owner === 'gmail') {
    // Gmail owns it: the Norm copy is locked until it is brought back.
    return {
      ...NOTHING,
      bringBack: !s.busy,
      discard: !s.busy,
      hint: 'This draft is in Gmail now — send it from Gmail, or bring it back to edit it here.',
    };
  }
  return {
    editable: !s.busy,
    send: !s.busy && s.mailboxReady,
    save: !s.busy,
    toGmail: !s.busy && s.mailboxReady,
    bringBack: false,
    discard: !s.busy,
    hint: s.mailboxReady ? null : 'This mailbox isn’t connected, so it can’t send yet — you can still save a draft.',
  };
}

/** The fields a draft save (create or PATCH) carries. */
export function draftFields(f: ComposerFields, mode: ComposeMode = 'reply') {
  return {
    mode,
    to: parseAddresses(f.to),
    cc: parseAddresses(f.cc),
    bcc: parseAddresses(f.bcc),
    subject: f.subject.trim(),
    body_html: f.body,
  };
}

/** How a saved draft goes out (older drafts carry no mode). */
export function draftMode(d: Pick<Draft, 'mode' | 'gmail_thread_id'>): ComposeMode {
  return d.mode ?? (d.gmail_thread_id ? 'reply' : 'new');
}

export function fieldsFromDraft(d: Pick<Draft, 'to' | 'cc' | 'bcc' | 'subject' | 'body_html'>): ComposerFields {
  return {
    to: (d.to || []).join(', '),
    cc: (d.cc || []).join(', '),
    bcc: (d.bcc || []).join(', '),
    subject: d.subject || '',
    body: d.body_html || '',
  };
}

/** "The same email" for the idempotency key and for unsaved-changes checks:
 *  addresses compared as parsed, the subject trimmed. */
export function contentFingerprint(mode: ComposeMode, threadId: string | null, f: ComposerFields): string {
  return fingerprint({ threadId, ...draftFields(f, mode) });
}

/** What to fix before sending, in a sentence (null = good to go). The server
 *  checks the same things; this just says so before the round trip. */
export function sendProblem(mode: ComposeMode, f: ComposerFields): string | null {
  const to = parseAddresses(f.to);
  const all = [...to, ...parseAddresses(f.cc), ...parseAddresses(f.bcc)];
  const bad = invalidAddresses(all);
  if (bad.length) return `${bad.length === 1 ? 'This isn’t' : 'These aren’t'} an email address: ${bad.join(', ')}.`;
  // A reply with no To goes back to whoever wrote last (the server works it out).
  if (!to.length && (mode === 'new' || mode === 'forward')) return 'Who is this to? Add at least one recipient.';
  if (!f.subject.trim() && (mode === 'new' || mode === 'forward')) return 'The email needs a subject.';
  if (isBlankHtml(f.body)) return 'Write a message first.';
  return null;
}
