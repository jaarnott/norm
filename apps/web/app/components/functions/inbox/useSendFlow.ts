'use client';

/**
 * One send's state for a composer or a send form, around the pure rules in
 * sendFlow.ts: the idempotency key lives here for as long as the screen does
 * (same content = same key, so a double click or a retry never sends twice),
 * API answers become phases, and a 202 is followed by polling the outbox.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { fnGet } from '../shared/api';
import { attemptSend, pollOutbox, type KeyState, type SendPhase } from './sendFlow';
import type { OutboxResult } from './types';

export type SendRunner = (idempotencyKey: string, priceOverrideReason?: string) => Promise<OutboxResult>;

const getOutbox = (outboxId: string) => fnGet<OutboxResult>(`/inbox/outbox/${encodeURIComponent(outboxId)}`);

export function useSendFlow() {
  const [phase, setPhase] = useState<SendPhase>({ kind: 'idle' });
  const key = useRef<KeyState | null>(null);
  const last = useRef<{ fingerprint: string; run: SendRunner } | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const settle = useCallback((next: SendPhase) => {
    // A sent email is done: the next one (even identical) is a new email.
    if (next.kind === 'sent') key.current = null;
    if (alive.current) setPhase(next);
    return next;
  }, []);

  const execute = useCallback(
    async (fingerprint: string, run: SendRunner, reason?: string): Promise<SendPhase> => {
      last.current = { fingerprint, run };
      setPhase({ kind: 'busy' });
      const attempt = await attemptSend(key.current, fingerprint, run, reason);
      key.current = attempt.key;
      const first = settle(attempt.phase);
      if (first.kind !== 'sending' || !alive.current) return first;
      return settle(await pollOutbox(first.result, getOutbox, { alive: () => alive.current }));
    },
    [settle],
  );

  /** Send this content (`fingerprint` says what "the same email" is). */
  const send = useCallback((fingerprint: string, run: SendRunner) => execute(fingerprint, run), [execute]);

  /** After a pricing warning: the same email again, with the reason recorded. */
  const override = useCallback(
    (reason: string) => (last.current ? execute(last.current.fingerprint, last.current.run, reason) : Promise.resolve(phase)),
    [execute, phase],
  );

  /** After a refused or unreachable send: the same email, the same key. */
  const retry = useCallback(
    () => (last.current ? execute(last.current.fingerprint, last.current.run) : Promise.resolve(phase)),
    [execute, phase],
  );

  /** A send still "sending" after polling stopped: ask the outbox once more. */
  const checkAgain = useCallback(async () => {
    if (phase.kind !== 'sending') return phase;
    return settle(await pollOutbox(phase.result, getOutbox, { maxPolls: 1, intervalMs: 0 }));
  }, [phase, settle]);

  const reset = useCallback(() => setPhase({ kind: 'idle' }), []);

  return { phase, send, override, retry, checkAgain, reset };
}
