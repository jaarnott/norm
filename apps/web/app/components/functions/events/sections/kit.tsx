'use client';

/**
 * What every event-page section shares: the context the page hands down,
 * and `useEditor`, the read ⇄ edit cycle of one section.
 *
 * One section is edited at a time. A save sends the version of the event as
 * the page loaded it, so someone else's save in between is a 409 ("reload"),
 * never a silent overwrite; steps of a multi-part save chain the version each
 * response returns.
 */
import { useState } from 'react';
import { Pencil } from 'lucide-react';
import Button from '../../../ui/Button';
import type { FnMeta } from '../../shared/meta';
import { READ_ONLY_HINT, WriteError } from '../ui';
import { errorText, isStale } from '../useLoad';
import type { EventDetail } from '../types';

export type EditKey = 'details' | 'timings' | 'beverages' | 'food' | 'contact';

/** A write step: gets the note (asked for on a confirmed event) and `apply`
 *  to show a step's result at once; returns the final detail, or null when
 *  the page should refetch (menu writes return the menu, not the event). */
export type Write = (note: string | null, apply: (d: EventDetail) => void) => Promise<EventDetail | null>;

export interface SectionCtx {
  detail: EventDetail;
  meta: FnMeta;
  currency: string;
  timeZone: string;
  /** Live, and the event isn't cancelled. */
  canWrite: boolean;
  /** Why writes are off, for the disabled buttons' hint. */
  lockReason: string | null;
  editing: EditKey | null;
  setEditing: (key: EditKey | null) => void;
  save: (write: Write) => Promise<boolean>;
  /** Put a write's fresh event detail on the page (actions that need no note). */
  apply: (d: EventDetail) => void;
  /** Refetch the event and leave edit mode (after "changed by someone else"). */
  reload: () => void;
  /** Is this field on for the event's venue × booking type? */
  on: (key: string) => boolean;
}

export const CANCELLED_HINT = 'This event is cancelled — reinstate it to make changes.';

export function lockReasonFor(detail: EventDetail, canWrite: boolean): string | null {
  if (!canWrite) return READ_ONLY_HINT;
  if (detail.event.cancelled_at) return CANCELLED_HINT;
  return null;
}

export function useEditor<D>(key: EditKey, ctx: SectionCtx, makeDraft: () => D) {
  const editing = ctx.editing === key;
  const [draft, setDraft] = useState<D | null>(null);
  const [base, setBase] = useState<D | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const start = () => {
    const d = makeDraft();
    setDraft(d);
    setBase(d);
    setError(null);
    ctx.setEditing(key);
  };
  const cancel = () => {
    ctx.setEditing(null);
    setError(null);
  };
  const submit = async (write: Write) => {
    setBusy(true);
    setError(null);
    try {
      const ok = await ctx.save(write);
      if (ok) ctx.setEditing(null);
    } catch (e) {
      setError(e);
    }
    setBusy(false);
  };
  const update = (patch: Partial<D>) => setDraft((d) => (d ? { ...d, ...patch } : d));

  const otherEditing = ctx.editing !== null && !editing;
  const editButton = (
    <Button
      size="sm"
      icon={Pencil}
      onClick={start}
      disabled={!ctx.canWrite || otherEditing}
      title={ctx.lockReason ?? (otherEditing ? 'Finish the section you’re editing first' : undefined)}
    >
      Edit
    </Button>
  );
  /** Cancel / Save under the form; `onSave` builds the write and submits it. */
  const footer = (onSave: () => void) => editing ? (
    <>
      <Button onClick={cancel} disabled={busy}>Cancel</Button>
      <Button variant="primary" onClick={onSave} disabled={busy} aria-busy={busy || undefined}>
        {busy ? 'Saving…' : 'Save'}
      </Button>
    </>
  ) : null;
  const errorStrip = (
    <WriteError
      message={error ? errorText(error) : null}
      stale={isStale(error)}
      onReload={() => {
        setError(null);
        ctx.reload();
      }}
    />
  );

  return { editing: editing && draft !== null, draft: draft as D, base: base as D, busy, error, setError, start, cancel, submit, update, editButton, footer, errorStrip };
}
