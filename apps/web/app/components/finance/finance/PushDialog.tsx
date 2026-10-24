'use client';

/**
 * A Loaded budget push from a Finance page, in a dialog: the shared
 * ApprovalCard previews through `POST /api/finance/push/preview` and
 * confirms through `/confirm` (F6's wrappers over the page helper, which
 * set the ledger's source from `kind` — `typed` for the Daily budget grid, `forecast`
 * for "Set Loaded budget", `restore` for the ledger's restore).
 *
 * The person's click is the approval; the card is what they approve. When
 * the preview comes back already settled (nothing to change, or the
 * person's standing permission let the typed push run), the card reports
 * it done without a second click — the page handles both.
 */
import { useMemo, type ReactNode } from 'react';
import Dialog from '../../ui/Dialog';
import ApprovalCard, { type ConfirmResult, type PageWrite } from '../shared/ApprovalCard';
import { toast } from '../shared/Toasts';
import { finSend } from '../shared/useFinanceApi';
import { pushOutcome, type PushData } from './logic';
import type { PushKind } from './types';

export interface PushRequest {
  venue_id: string;
  kind: PushKind;
  days: { date: string; amount_cents: number }[];
  allow_zero?: boolean;
  vintage_id?: string | null;
}

export default function PushDialog({ title, intro, request, confirmLabel = 'Send to Loaded', onDone, onClose, children }: {
  title: string;
  intro?: ReactNode;
  request: PushRequest;
  confirmLabel?: string;
  /** After the push ran (or had nothing to do): reload the grid. */
  onDone: () => void;
  onClose: () => void;
  children?: ReactNode;
}) {
  const write = useMemo<PageWrite>(() => ({
    preview: () => finSend('POST', '/api/finance/push/preview', request),
    confirm: (fingerprint) => finSend('POST', '/api/finance/push/confirm', { ...request, fingerprint }),
  }), [request]);

  const done = (result: ConfirmResult | { done: unknown }) => {
    const data = ('done' in result ? result.done : result.data) as PushData | null | undefined;
    const failed = data?.failed?.length ?? 0;
    toast(pushOutcome(data), failed ? 'warn' : 'ok');
    onDone();
    onClose();
  };

  return (
    <Dialog title={title} width={560} onClose={onClose} closeButton>
      <ApprovalCard write={write} confirmLabel={confirmLabel} busyLabel="Sending…" intro={intro} onDone={done} onCancel={onClose}>
        {children}
      </ApprovalCard>
    </Dialog>
  );
}
