'use client';

/**
 * One event (`?page=functions-events&event=<id>`): the header with its
 * status and actions (send to client, print, cancel / reinstate), the money,
 * then the sections — details, timings, beverages, food — each read-only
 * until Edit, with the client, portal link, related records and activity in
 * the side column.
 *
 * Every write returns the fresh event, which replaces what's on screen. On a
 * confirmed event a write first asks for a note: the change is logged as made
 * after confirmation, and the client won't be asked to confirm it again.
 */
import { useCallback, useState } from 'react';
import { Ban, Link2, Printer, RotateCcw, Send } from 'lucide-react';
import Button from '../../ui/Button';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import { fnGet, fnSend } from '../shared/api';
import { dateLabel, money, timeRangeLabel, instantLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { CopyClientLinkDialog, NoteDialog, SendToClientDialog } from './dialogs';
import { STATUS_LABEL } from './logic';
import PrintView, { PRINT_KINDS } from './PrintView';
import { ActivityCard, ContactCard, LinksCard, PortalCard } from './sections/AsideCards';
import BeveragesSection from './sections/BeveragesSection';
import DetailsSection, { useFileUrl } from './sections/DetailsSection';
import FoodSection from './sections/FoodSection';
import { lockReasonFor } from './sections/kit';
import type { EditKey, SectionCtx, Write } from './sections/kit';
import TimingsSection from './sections/TimingsSection';
import { EditedFlag, Hint, MenuButton, READ_ONLY_HINT, StatusBadge } from './ui';
import { errorText, useLoad } from './useLoad';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import type { EventDetail, PrintKind } from './types';

interface NoteAsk {
  run: (note: string | null) => Promise<void>;
  resolve: (ok: boolean) => void;
}

function MoneyStrip({ detail, currency, taxLabel }: { detail: EventDetail; currency: string; taxLabel: string }) {
  const m = detail.money;
  const items: [string, string, boolean][] = [
    ['Expected spend', money(m.expected_spend_cents, currency), true],
    ['Minimum spend', m.min_spend_cents !== null ? money(m.min_spend_cents, currency) : '—', false],
    ['Food', money(m.food_cents, currency), false],
    ['Bar tab', money(m.bar_tab_cents, currency), false],
  ];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 140px), 1fr))', gap: 8, marginBottom: 16 }}>
      {items.map(([label, value, strong]) => (
        <div key={label} className="n-card" style={{ padding: '10px 12px' }}>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{label}{strong ? ` · ${taxLabel}` : ''}</div>
          <div style={{ fontSize: strong ? 'var(--fs-xl)' : 'var(--fs-lg)', fontWeight: strong ? 700 : 600, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
        </div>
      ))}
    </div>
  );
}

export default function EventPage({ eventId, meta, isPage, onBack }: {
  eventId: string;
  meta: FnMeta;
  isPage: boolean;
  onBack: () => void;
}) {
  const { data: detail, error, loading, refreshing, reload, setData } = useLoad(`event:${eventId}`, () => fnGet<EventDetail>(`/events/${eventId}`));
  const [editing, setEditing] = useState<EditKey | null>(null);
  const [ask, setAsk] = useState<NoteAsk | null>(null);
  const [dialog, setDialog] = useState<'send' | 'copy' | 'cancel' | 'reinstate' | null>(null);
  const [printing, setPrinting] = useState<PrintKind | null>(null);
  const { isDesktop } = useBreakpoint();
  const planUrl = useFileUrl(printing ? detail?.event.table_plan_file_id : null);

  const confirmed = detail?.status.status === 'confirmed';
  const save = useCallback(
    (write: Write) =>
      new Promise<boolean>((resolve, reject) => {
        const run = async (note: string | null) => {
          const res = await write(note, setData);
          if (res) setData(res);
          else reload();
        };
        if (confirmed) setAsk({ run, resolve });
        else run(null).then(() => resolve(true), reject);
      }),
    [confirmed, setData, reload],
  );
  const reloadAndReset = useCallback(() => {
    setEditing(null);
    reload();
  }, [reload]);

  const back = { label: 'Events', onClick: onBack };
  if (loading) {
    return (
      <>
        {isPage && <PageHeader title="Event" back={back} />}
        <PageState kind="loading" title="Loading the event…" />
      </>
    );
  }
  if (!detail) {
    return (
      <>
        {isPage && <PageHeader title="Event" back={back} />}
        <PageState kind="error" title="Couldn’t open this event" detail={errorText(error)} action={<Button size="sm" onClick={reload}>Try again</Button>} />
      </>
    );
  }

  const ev = detail.event;
  const venue = meta.venues.find((v) => v.id === ev.venue_id);
  const timeZone = detail.venue?.timezone || venue?.timezone || meta.company.default_timezone;
  const cancelled = !!ev.cancelled_at;
  const canWrite = meta.can_write && !cancelled;
  const ctx: SectionCtx = {
    detail,
    meta,
    currency: meta.company.currency,
    timeZone,
    canWrite,
    lockReason: lockReasonFor(detail, meta.can_write),
    editing,
    setEditing,
    save,
    apply: setData,
    reload: reloadAndReset,
    on: (key) => detail.enabled_fields?.[key] !== false,
  };

  const metaLine = [
    dateLabel(ev.event_date),
    timeRangeLabel(ev.start_time, ev.end_time, ev.ends_next_day),
    detail.venue?.name,
    detail.area?.name,
    ev.guests ? `${ev.guests} guests` : null,
    ev.booking_label,
  ].filter(Boolean).join(' · ');

  const status = (
    <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
      <StatusBadge status={detail.status.status} />
      {detail.status.edited_since_confirmed && <EditedFlag />}
    </span>
  );

  const actions = (
    <>
      <MenuButton
        label="Print"
        icon={Printer}
        items={PRINT_KINDS.map((k) => ({ id: k.id, label: k.label, onSelect: () => setPrinting(k.id) }))}
      />
      {cancelled ? (
        <Button icon={RotateCcw} onClick={() => setDialog('reinstate')} disabled={!meta.can_write} title={meta.can_write ? undefined : ctx.lockReason ?? undefined}>
          Reinstate
        </Button>
      ) : (
        <>
          <Button variant="danger" icon={Ban} onClick={() => setDialog('cancel')} disabled={!canWrite || editing !== null} title={ctx.lockReason ?? undefined}>
            Cancel event
          </Button>
          <Button icon={Link2} onClick={() => setDialog('copy')} disabled={!canWrite}
            title={ctx.lockReason ?? 'Get the client’s link to paste into your own email (when the venue mailbox isn’t connected)'}>
            Copy client link
          </Button>
          <Button variant="primary" icon={Send} onClick={() => setDialog('send')} disabled={!canWrite} title={ctx.lockReason ?? undefined}>
            Send to client
          </Button>
        </>
      )}
    </>
  );

  const venueTzWarning = venue && !venue.timezone_valid;
  const main = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
      <DetailsSection ctx={ctx} />
      <TimingsSection ctx={ctx} />
      <BeveragesSection ctx={ctx} />
      <FoodSection ctx={ctx} />
    </div>
  );
  const aside = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
      <ContactCard ctx={ctx} />
      {!cancelled && <PortalCard ctx={ctx} onSend={() => setDialog('send')} onCopyLink={() => setDialog('copy')} />}
      <LinksCard ctx={ctx} />
      {isDesktop && <ActivityCard ctx={ctx} />}
    </div>
  );

  return (
    <div style={{ width: '100%' }}>
      {isPage ? (
        <PageHeader title={ev.name || 'Event'} back={back} status={status} meta={metaLine} actions={actions} />
      ) : (
        <div style={{ marginBottom: 12 }}>
          <Button variant="link" onClick={onBack} style={{ fontSize: 'var(--fs-sm)' }}>All events</Button>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
            <h2 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600 }}>{ev.name || 'Event'}</h2>
            {status}
          </div>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 8 }}>{metaLine}</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{actions}</div>
        </div>
      )}

      {!meta.can_write && <Hint style={{ marginBottom: 12 }}>{READ_ONLY_HINT}</Hint>}
      {cancelled && (
        <div role="status" style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 'var(--radius)', background: 'var(--error-bg)', color: 'var(--error)', fontSize: 'var(--fs-base)' }}>
          <strong>Cancelled</strong> {instantLabel(ev.cancelled_at, timeZone, true)}{ev.cancel_reason ? ` — ${ev.cancel_reason}` : ''}. Reinstate it to make changes.
        </div>
      )}
      {venueTzWarning && (
        <div role="status" style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 'var(--radius)', background: 'var(--warn-bg)', color: 'var(--warn)', fontSize: 'var(--fs-sm)' }}>
          {venue.name} has no valid time zone, so changes to its events will be refused. Set it in Functions settings.
        </div>
      )}
      {detail.status.status === 'confirmed' && detail.status.confirmed_at && !cancelled && (
        <div style={{ marginBottom: 12, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          {STATUS_LABEL.confirmed} {instantLabel(detail.status.confirmed_at, timeZone, true)}. Changes from here on are logged as made after confirmation.
        </div>
      )}
      {refreshing && <div role="status" style={{ marginBottom: 8, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Refreshing…</div>}

      <MoneyStrip detail={detail} currency={meta.company.currency} taxLabel={meta.company.tax_label} />

      {isDesktop ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(280px, 340px)', gap: 16, alignItems: 'start' }}>
          {main}
          {aside}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {aside}
          {main}
          <ActivityCard ctx={ctx} />
        </div>
      )}

      {ask && (
        <NoteDialog
          title="This event is already confirmed"
          label="What changed, and why"
          placeholder="e.g. Guest count up to 42, agreed by phone"
          required
          confirmLabel="Save change"
          onConfirm={async (note) => {
            await ask.run(note);
            ask.resolve(true);
          }}
          onClose={() => {
            ask.resolve(false);
            setAsk(null);
          }}
        >
          The client has confirmed this event. They won’t be asked to confirm this change, so let them know if it affects them. The
          note goes in the event’s log.
        </NoteDialog>
      )}

      {dialog === 'send' && (
        <SendToClientDialog detail={detail} timeZone={timeZone} onSent={reload} onClose={() => setDialog(null)} onCopyLink={() => setDialog('copy')} />
      )}
      {dialog === 'copy' && (
        <CopyClientLinkDialog detail={detail} timeZone={timeZone} onDone={reload} onClose={() => setDialog(null)} />
      )}
      {dialog === 'cancel' && (
        <NoteDialog
          title="Cancel this event?"
          label="Reason"
          placeholder="e.g. Client postponed to next year"
          required
          danger
          confirmLabel="Cancel event"
          onConfirm={async (reason) => {
            const d = await fnSend<EventDetail>('POST', `/events/${ev.id}/cancel`, { reason, version: ev.version });
            setData(d);
          }}
          onClose={() => setDialog(null)}
        >
          The event comes off the calendar and its enquiry is closed as lost. You can reinstate it later.
        </NoteDialog>
      )}
      {dialog === 'reinstate' && (
        <NoteDialog
          title="Reinstate this event?"
          label="Reason"
          placeholder="e.g. Back on — client confirmed by phone"
          required={false}
          confirmLabel="Reinstate"
          onConfirm={async (reason) => {
            const d = await fnSend<EventDetail>('POST', `/events/${ev.id}/reinstate`, { reason });
            setData(d);
          }}
          onClose={() => setDialog(null)}
        >
          The event goes back on the calendar and its enquiry is reopened.
        </NoteDialog>
      )}
      {printing && (
        <PrintView eventId={ev.id} kind={printing} timeZone={timeZone} tablePlanUrl={printing !== 'food' ? planUrl : null} onClose={() => setPrinting(null)} />
      )}
    </div>
  );
}
