import { describe, expect, it, vi } from 'vitest';
import { FnApiError } from '../shared/api';
import { attemptSend, fingerprint, keyFor, phaseOfError, phaseOfResult, pollOutbox } from './sendFlow';
import type { OutboxResult } from './types';

const result = (status: OutboxResult['status'], extra: Partial<OutboxResult> = {}): OutboxResult => ({
  outbox_id: 'ob1',
  status,
  rfc822_message_id: '<m@norm>',
  gmail_message_id: status === 'sent' ? 'g1' : null,
  gmail_thread_id: 't1',
  duplicate: false,
  error: null,
  ...extra,
});

const apiError = (status: number, code: string | null, message = 'x') => new FnApiError(status, code, message, null);

let n = 0;
const mint = () => `key-${++n}`;

describe('idempotency keys', () => {
  it('keeps the key for the same content and mints one for new content', () => {
    const a = keyFor(null, 'fp1', mint);
    expect(keyFor(a, 'fp1', mint)).toBe(a);
    const b = keyFor(a, 'fp2', mint);
    expect(b.key).not.toBe(a.key);
    expect(b.fingerprint).toBe('fp2');
  });

  it('fingerprints content regardless of key order and missing values', () => {
    expect(fingerprint({ a: 1, b: null })).toBe(fingerprint({ b: undefined, a: 1 }));
    expect(fingerprint({ a: 1 })).not.toBe(fingerprint({ a: 2 }));
  });
});

describe('reading send answers', () => {
  it('maps outbox statuses', () => {
    expect(phaseOfResult(result('sent')).kind).toBe('sent');
    expect(phaseOfResult(result('sending'))).toMatchObject({ kind: 'sending', stuck: false });
    expect(phaseOfResult(result('failed', { error: 'quota' }))).toEqual({ kind: 'failed', message: 'Gmail refused it: quota' });
  });

  it('maps API refusals by code', () => {
    expect(phaseOfError(apiError(409, 'pricing_warning', 'quotes a price'))).toEqual({ kind: 'pricing', message: 'quotes a price' });
    expect(phaseOfError(apiError(409, 'mailbox_unavailable')).kind).toBe('mailbox');
    expect(phaseOfError(apiError(409, 'not_live')).kind).toBe('not_live');
    expect(phaseOfError(apiError(409, 'conflict')).kind).toBe('conflict');
    expect(phaseOfError(apiError(502, 'send_failed')).kind).toBe('failed');
    expect(phaseOfError(apiError(503, 'gmail_unavailable')).kind).toBe('failed');
    expect(phaseOfError(apiError(422, 'invalid', 'Who is this to?'))).toEqual({ kind: 'invalid', message: 'Who is this to?' });
    expect(phaseOfError(apiError(422, null)).kind).toBe('invalid'); // FastAPI body validation
    expect(phaseOfError(apiError(404, 'not_found')).kind).toBe('error');
    expect(phaseOfError(new TypeError('Failed to fetch')).kind).toBe('failed');
  });
});

describe('the pricing-warning flow', () => {
  it('re-sends the same content under the same key with the reason', async () => {
    const run = vi
      .fn<(key: string, reason?: string) => Promise<OutboxResult>>()
      .mockRejectedValueOnce(apiError(409, 'pricing_warning', 'This email quotes a price…'))
      .mockResolvedValueOnce(result('sent'));
    const first = await attemptSend(null, 'fp-body', run, undefined, mint);
    expect(first.phase).toEqual({ kind: 'pricing', message: 'This email quotes a price…' });
    const second = await attemptSend(first.key, 'fp-body', run, 'Customer asked for the price by phone', mint);
    expect(second.phase.kind).toBe('sent');
    expect(run.mock.calls[0][0]).toBe(run.mock.calls[1][0]); // same idempotency key
    expect(run.mock.calls[1][1]).toBe('Customer asked for the price by phone');
  });

  it('uses a new key once the person edits the email instead', async () => {
    const run = vi
      .fn<(key: string, reason?: string) => Promise<OutboxResult>>()
      .mockRejectedValueOnce(apiError(409, 'pricing_warning'))
      .mockResolvedValueOnce(result('sent'));
    const first = await attemptSend(null, 'with-price', run, undefined, mint);
    const second = await attemptSend(first.key, 'price-removed', run, undefined, mint);
    expect(second.phase.kind).toBe('sent');
    expect(run.mock.calls[0][0]).not.toBe(run.mock.calls[1][0]);
    expect(run.mock.calls[1][1]).toBeUndefined();
  });

  it('retries a refused send with the same key', async () => {
    const run = vi
      .fn<(key: string) => Promise<OutboxResult>>()
      .mockRejectedValueOnce(apiError(502, 'send_failed', 'Gmail said no'))
      .mockResolvedValueOnce(result('sent'));
    const first = await attemptSend(null, 'fp', run, undefined, mint);
    expect(first.phase).toEqual({ kind: 'failed', message: 'Gmail said no' });
    const again = await attemptSend(first.key, 'fp', run, undefined, mint);
    expect(again.phase.kind).toBe('sent');
    expect(run.mock.calls[0][0]).toBe(run.mock.calls[1][0]);
  });
});

describe('polling the outbox after a 202', () => {
  const noSleep = () => Promise.resolve();

  it('settles when Gmail confirms', async () => {
    const get = vi
      .fn<(id: string) => Promise<OutboxResult>>()
      .mockResolvedValueOnce(result('sending'))
      .mockRejectedValueOnce(new TypeError('network blip'))
      .mockResolvedValueOnce(result('sent'));
    const phase = await pollOutbox(result('sending'), get, { sleep: noSleep });
    expect(phase.kind).toBe('sent');
    expect(get).toHaveBeenCalledTimes(3);
    expect(get).toHaveBeenCalledWith('ob1');
  });

  it('reports a failure the reconciler found', async () => {
    const get = vi.fn<(id: string) => Promise<OutboxResult>>().mockResolvedValue(result('failed', { error: 'bounced' }));
    expect(await pollOutbox(result('sending'), get, { sleep: noSleep })).toEqual({
      kind: 'failed',
      message: 'Gmail refused it: bounced',
    });
  });

  it('gives up politely and leaves it to the outbox', async () => {
    const get = vi.fn<(id: string) => Promise<OutboxResult>>().mockResolvedValue(result('sending'));
    const phase = await pollOutbox(result('sending'), get, { sleep: noSleep, maxPolls: 3 });
    expect(phase).toMatchObject({ kind: 'sending', stuck: true });
    expect(get).toHaveBeenCalledTimes(3);
  });

  it('stops when the screen is gone', async () => {
    const get = vi.fn<(id: string) => Promise<OutboxResult>>().mockResolvedValue(result('sending'));
    const phase = await pollOutbox(result('sending'), get, { sleep: noSleep, alive: () => false });
    expect(get).not.toHaveBeenCalled();
    expect(phase.kind).toBe('sending');
  });
});
