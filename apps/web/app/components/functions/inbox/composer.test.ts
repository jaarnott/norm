import { describe, expect, it } from 'vitest';
import {
  composerActions,
  contentFingerprint,
  draftFields,
  draftMode,
  EMPTY_FIELDS,
  fieldsFromDraft,
  READ_ONLY_HINT,
  sendProblem,
  type ComposerSituation,
} from './composer';

const live: ComposerSituation = { canWrite: true, mailboxReady: true, mode: 'reply', draft: null, busy: false };

describe('composer actions', () => {
  it('allows everything on a live, connected mailbox', () => {
    expect(composerActions(live)).toMatchObject({ editable: true, send: true, save: true, toGmail: true, discard: true, hint: null });
  });

  it('is read-only in shadow mode', () => {
    const a = composerActions({ ...live, canWrite: false });
    expect(a).toMatchObject({ editable: false, send: false, save: false, toGmail: false, bringBack: false, discard: false });
    expect(a.hint).toBe(READ_ONLY_HINT);
  });

  it('locks a draft handed to Gmail until it is brought back', () => {
    const a = composerActions({ ...live, draft: { owner: 'gmail', status: 'open' } });
    expect(a).toMatchObject({ editable: false, send: false, save: false, toGmail: false, bringBack: true });
    expect(a.hint).toMatch(/in Gmail now/);
    expect(composerActions({ ...live, draft: { owner: 'norm', status: 'open' } }).bringBack).toBe(false);
  });

  it('does nothing with a draft that was already sent', () => {
    const a = composerActions({ ...live, draft: { owner: 'norm', status: 'sent' } });
    expect(a.send || a.save || a.discard).toBe(false);
    expect(a.hint).toBe('This draft was already sent.');
  });

  it('can still save a draft when the mailbox isn’t connected, but not send', () => {
    const a = composerActions({ ...live, mailboxReady: false });
    expect(a).toMatchObject({ send: false, toGmail: false, save: true });
    expect(a.hint).toMatch(/isn’t connected/);
  });

  it('saves forwards and reply-alls as drafts like any other email', () => {
    for (const mode of ['forward', 'reply_all', 'new'] as const) {
      expect(composerActions({ ...live, mode })).toMatchObject({ send: true, save: true, toGmail: true });
    }
  });

  it('holds every button while a request runs', () => {
    const a = composerActions({ ...live, busy: true });
    expect(a.send || a.save || a.toGmail || a.discard || a.editable).toBe(false);
  });
});

describe('composer content', () => {
  it('turns typed fields into a draft save and back', () => {
    const fields = { to: 'a@b.co; Casey <c@d.com>', cc: '', bcc: 'x@y.nz', subject: '  Re: Hi ', body: '<p>Hi</p>' };
    expect(draftFields(fields, 'reply_all')).toEqual({
      mode: 'reply_all',
      to: ['a@b.co', 'Casey <c@d.com>'],
      cc: [],
      bcc: ['x@y.nz'],
      subject: 'Re: Hi',
      body_html: '<p>Hi</p>',
    });
    const back = fieldsFromDraft({ to: ['a@b.co'], cc: [], bcc: [], subject: null, body_html: null });
    expect(back).toEqual({ ...EMPTY_FIELDS, to: 'a@b.co' });
  });

  it('reads an older draft without a mode from its thread', () => {
    expect(draftMode({ mode: null, gmail_thread_id: 't1' })).toBe('reply');
    expect(draftMode({ mode: undefined, gmail_thread_id: null })).toBe('new');
    expect(draftMode({ mode: 'forward', gmail_thread_id: 't1' })).toBe('forward');
  });

  it('treats re-typed but equal content as the same email', () => {
    const a = contentFingerprint('reply', 't1', { ...EMPTY_FIELDS, to: 'a@b.co,c@d.co', subject: 'Re: x', body: '<p>Hi</p>' });
    const b = contentFingerprint('reply', 't1', { ...EMPTY_FIELDS, to: ' a@b.co ; c@d.co ', subject: 'Re: x ', body: '<p>Hi</p>' });
    expect(a).toBe(b);
    expect(contentFingerprint('reply', 't1', { ...EMPTY_FIELDS, body: '<p>Hi!</p>' })).not.toBe(a);
    expect(contentFingerprint('reply_all', 't1', { ...EMPTY_FIELDS, to: 'a@b.co,c@d.co', subject: 'Re: x', body: '<p>Hi</p>' })).not.toBe(a);
  });

  it('says what to fix before sending', () => {
    expect(sendProblem('reply', { ...EMPTY_FIELDS, body: '<p>Thanks</p>' })).toBeNull(); // To comes from the thread
    expect(sendProblem('new', { ...EMPTY_FIELDS, subject: 'Hi', body: '<p>x</p>' })).toMatch(/Who is this to/);
    expect(sendProblem('new', { ...EMPTY_FIELDS, to: 'a@b.co', body: '<p>x</p>' })).toMatch(/subject/);
    expect(sendProblem('reply', { ...EMPTY_FIELDS, to: 'not-an-address', body: '<p>x</p>' })).toMatch(/isn’t an email address: not-an-address/);
    expect(sendProblem('reply', { ...EMPTY_FIELDS, to: 'a@b.co', body: '<p><br></p>' })).toBe('Write a message first.');
  });
});
