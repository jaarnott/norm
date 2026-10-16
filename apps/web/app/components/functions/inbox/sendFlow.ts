/**
 * Norm Functions — the client side of every send (composer, drafts, proposal
 * sends), kept pure so vitest can pin it: the idempotency-key rule, what an
 * API answer means for the screen, and the outbox poll after a 202. The
 * React wiring is useSendFlow.ts.
 */
import { FnApiError, newIdempotencyKey } from '../shared/api';
import type { OutboxResult } from './types';

/**
 * One idempotency key per CONTENT. A retry or a double click of the same
 * email reuses it, so the server sends it once. Changing what is sent mints a
 * new key: the server replays a used key's ORIGINAL message (a failed send is
 * retried with its first content and Message-ID), so an edited email under an
 * old key would never go out as edited.
 */
export interface KeyState {
  key: string;
  fingerprint: string;
}

export function keyFor(prev: KeyState | null, fingerprint: string, mint: () => string = newIdempotencyKey): KeyState {
  return prev && prev.fingerprint === fingerprint ? prev : { key: mint(), fingerprint };
}

/** A stable string for "the same email": key order and undefined/null don't matter. */
export function fingerprint(parts: Record<string, unknown>): string {
  return JSON.stringify(
    Object.keys(parts)
      .sort()
      .map((k) => [k, parts[k] ?? null]),
  );
}

export type SendPhase =
  | { kind: 'idle' }
  | { kind: 'busy' }
  | { kind: 'sent'; result: OutboxResult }
  /** 202: Gmail's answer was lost; polling the outbox. `stuck` = stopped
   *  polling — the outbox reconciler settles it within ~10 minutes. */
  | { kind: 'sending'; result: OutboxResult; stuck: boolean }
  /** 409 pricing_warning: needs a reason to send anyway (same key). */
  | { kind: 'pricing'; message: string }
  /** 409 mailbox_unavailable: reconnect the venue mailbox. */
  | { kind: 'mailbox'; message: string }
  | { kind: 'not_live'; message: string }
  /** 409 conflict: a stale draft, a draft now in Gmail, already sent. */
  | { kind: 'conflict'; message: string }
  /** Gmail refused or couldn't be reached: nothing went; retry (same key). */
  | { kind: 'failed'; message: string }
  | { kind: 'invalid'; message: string }
  | { kind: 'error'; message: string };

export function phaseOfResult(result: OutboxResult): SendPhase {
  if (result.status === 'sent') return { kind: 'sent', result };
  if (result.status === 'sending') return { kind: 'sending', result, stuck: false };
  return {
    kind: 'failed',
    message: result.error ? `Gmail refused it: ${result.error}` : 'Gmail refused the email. Nothing was sent.',
  };
}

export function phaseOfError(err: unknown): SendPhase {
  if (err instanceof FnApiError) {
    switch (err.code) {
      case 'pricing_warning':
        return { kind: 'pricing', message: err.message };
      case 'mailbox_unavailable':
        return { kind: 'mailbox', message: err.message };
      case 'not_live':
        return { kind: 'not_live', message: err.message };
      case 'conflict':
        return { kind: 'conflict', message: err.message };
      case 'send_failed':
      case 'gmail_unavailable':
      case 'gmail_error':
        return { kind: 'failed', message: err.message };
      case 'invalid':
        return { kind: 'invalid', message: err.message };
    }
    if (err.status === 422) return { kind: 'invalid', message: err.message };
    if (err.status === 409) return { kind: 'conflict', message: err.message };
    if (err.status >= 500) return { kind: 'failed', message: err.message };
    return { kind: 'error', message: err.message };
  }
  // No answer at all (network): it may or may not have gone. Retrying with
  // the same key is safe either way — the server sends a key once.
  return { kind: 'failed', message: "Couldn't reach Norm. Try again — the same email is never sent twice." };
}

export interface SendAttempt {
  key: KeyState;
  phase: SendPhase;
}

/** One try: pick the key for this content (see `keyFor`), run the request,
 *  and read its answer. A pricing override is the same content again with a
 *  reason — so it goes under the same key, as the server expects. */
export async function attemptSend(
  prevKey: KeyState | null,
  contentFingerprint: string,
  run: (idempotencyKey: string, priceOverrideReason?: string) => Promise<OutboxResult>,
  priceOverrideReason?: string,
  mint?: () => string,
): Promise<SendAttempt> {
  const key = keyFor(prevKey, contentFingerprint, mint);
  try {
    return { key, phase: phaseOfResult(await run(key.key, priceOverrideReason)) };
  } catch (err) {
    return { key, phase: phaseOfError(err) };
  }
}

/** After a 202: ask the outbox until Gmail's answer is known, or give up
 *  politely (the reconciler finishes the job server-side). A failed poll is
 *  not a failed send, so it just tries again. */
export async function pollOutbox(
  initial: OutboxResult,
  get: (outboxId: string) => Promise<OutboxResult>,
  opts: {
    intervalMs?: number;
    maxPolls?: number;
    sleep?: (ms: number) => Promise<void>;
    alive?: () => boolean;
  } = {},
): Promise<SendPhase> {
  const {
    intervalMs = 4000,
    maxPolls = 30,
    sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
    alive = () => true,
  } = opts;
  let last = initial;
  for (let i = 0; i < maxPolls; i++) {
    await sleep(intervalMs);
    if (!alive()) break;
    try {
      last = await get(initial.outbox_id);
    } catch {
      continue;
    }
    if (last.status !== 'sending') return phaseOfResult(last);
  }
  return { kind: 'sending', result: last, stuck: true };
}
