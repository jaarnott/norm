'use client';

/**
 * Skip / reopen for one portal section. Skipping records that the client
 * confirmed some other way (by email, on the phone) — the API wants a note
 * saying so. Reopening hands the section back to the client to confirm.
 */
import { useState } from 'react';
import { Ban, RotateCcw } from 'lucide-react';
import Button from '../../../ui/Button';
import { fnSend } from '../../shared/api';
import { money } from '../../shared/format';
import { NoteDialog } from '../dialogs';
import { SECTION_LABEL } from '../logic';
import type { EventDetail, SectionKey } from '../types';
import type { SectionCtx } from './kit';

/** What the person is vouching for, in Orbit's words. */
function skipStatement(detail: EventDetail, section: SectionKey, currency: string): string {
  if (section === 'details') return 'The client has confirmed the event details by email or phone.';
  if (section === 'beverages') {
    const tab = detail.event.bar_tab_cents ?? 0;
    return tab > 0
      ? `The client has confirmed the bar tab of ${money(tab, currency)}.`
      : 'The client has confirmed they don’t want a bar tab or drinks package.';
  }
  const ordered = detail.menus.some((m) => m.items.some((i) => i.kind === 'item' && i.quantity > 0));
  return ordered ? 'The client has confirmed the food pre-order.' : 'The client has confirmed they don’t need a food pre-order.';
}

export default function SectionActions({ ctx, section }: { ctx: SectionCtx; section: SectionKey }) {
  const [open, setOpen] = useState<'skip' | 'reopen' | null>(null);
  const state = ctx.detail.sections[section];
  const done = state?.kind === 'confirmed' || state?.kind === 'skipped';
  const label = SECTION_LABEL[section].toLowerCase();
  const busyElsewhere = ctx.editing !== null;

  const act = async (action: 'skip' | 'reopen', note: string | null) => {
    const d = await fnSend<EventDetail>('POST', `/events/${ctx.detail.event.id}/sections/${section}/${action}`, { note });
    ctx.apply(d);
  };

  return (
    <>
      {done ? (
        <Button size="sm" variant="quiet" icon={RotateCcw} onClick={() => setOpen('reopen')} disabled={!ctx.canWrite || busyElsewhere}
          title={ctx.lockReason ?? `Ask the client to confirm the ${label} again`}>
          Reopen
        </Button>
      ) : (
        <Button size="sm" variant="quiet" icon={Ban} onClick={() => setOpen('skip')} disabled={!ctx.canWrite || busyElsewhere}
          title={ctx.lockReason ?? 'The client confirmed some other way'}>
          Skip
        </Button>
      )}
      {open === 'skip' && (
        <NoteDialog
          title={`Skip client confirmation — ${label}`}
          label="Note"
          placeholder="e.g. Confirmed by email on 4 June"
          required
          confirmLabel="Skip confirmation"
          onConfirm={(note) => act('skip', note)}
          onClose={() => setOpen(null)}
        >
          <p style={{ margin: '0 0 8px' }}>{skipStatement(ctx.detail, section, ctx.currency)}</p>
          <p style={{ margin: 0 }}>
            The {label} count as confirmed without the client confirming in their portal. Say how they confirmed — the note goes in
            the event’s log.
          </p>
        </NoteDialog>
      )}
      {open === 'reopen' && (
        <NoteDialog
          title={`Reopen ${label} for the client`}
          label="Note"
          placeholder="e.g. Client wants to change the menu"
          required={false}
          confirmLabel="Reopen"
          onConfirm={(note) => act('reopen', note)}
          onClose={() => setOpen(null)}
        >
          The client will be asked to confirm the {label} again in their portal
          {ctx.detail.status.status === 'confirmed' ? ', and the event goes back to not confirmed until they do' : ''}.
        </NoteDialog>
      )}
    </>
  );
}
