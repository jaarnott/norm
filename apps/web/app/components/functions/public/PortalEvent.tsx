'use client';

import { useCallback, useEffect, useReducer, useRef, useState, type FormEvent } from 'react';
import { CalendarDays, CircleCheck, Clock, CloudCheck, CloudOff, LoaderCircle, LogOut, MapPin, Printer, Users, Wallet, X } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import Tabs from '../../ui/Tabs';
import { dateLabel, timeRangeLabel } from '../shared/format';
import PublicShell, { Notice } from './PublicShell';
import { Card } from './PortalControls';
import { DetailsTab, DrinksTab, FoodTab } from './PortalSections';
import { brandOf } from './brand';
import { LIST_LABELS, SECTIONS, SECTION_LABELS, STATUS_LABELS, fieldLabel } from './portalFields';
import { buildChanges, describeKeys, dirtyKeys, initialState, isClean, mergeDirty, reducer, type PortalState } from './portalModel';
import { PublicApiError, friendlyError, publicGet, publicSend, tokenPath } from './publicApi';
import type { PortalPayload, PortalSignedOut, PublicVenue, SectionKey } from './types';

/** How long typing must pause before an autosave. */
const SAVE_AFTER_MS = 1200;
const RETRY_AFTER_MS = 5000;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const STATUS_TONES = { booked: 'neutral', sent_to_client: 'accent', in_progress: 'info', confirmed: 'ok', cancelled: 'error' } as const;

/** A UTC fact where the customer is: "Mon 12 Oct, 3:04 pm". */
function factTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('en-NZ', { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
}

const describe = (keys: string[], payload: PortalPayload) =>
  describeKeys(keys, payload, { field: fieldLabel, list: (k) => LIST_LABELS[k] });

/**
 * The signed-in client portal: the event, its three sections (autosaved),
 * confirming each section, and who else can see it. All state changes go
 * through portalModel's reducer; this component runs the saves.
 */
export default function PortalEvent({ token, initial, onSignedOut, onGone }: {
  token: string;
  initial: PortalPayload;
  onSignedOut: (venue: PublicVenue | null, notice?: string) => void;
  onGone: () => void;
}) {
  const [state, dispatch] = useReducer(reducer, initial, initialState);
  const [tab, setTab] = useState<SectionKey>('details');
  const [confirming, setConfirming] = useState<SectionKey | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const latest = useRef<PortalState>(state);
  const busy = useRef(false);
  const path = `/portal/${tokenPath(token)}`;

  useEffect(() => {
    latest.current = state;
  });

  /** Sort a failed request into retry / undo / conflict / signed out. */
  const fail = useCallback((err: unknown): string => {
    const message = friendlyError(err);
    if (err instanceof PublicApiError) {
      if (err.sessionRequired) {
        onSignedOut(latest.current.server.venue, 'Please sign in again to keep editing. Changes from the last few seconds may not have been saved.');
        return message;
      }
      if (err.notFound) {
        onGone();
        return message;
      }
      if (err.status === 409) {
        dispatch({ type: 'saveFailed', outcome: 'conflict', message: err.message });
        return err.message;
      }
      if (err.status >= 400 && err.status < 500 && err.status !== 429) {
        dispatch({ type: 'saveFailed', outcome: 'rejected', message: err.message });
        return err.message;
      }
    }
    dispatch({ type: 'saveFailed', outcome: 'retry', message });
    return message;
  }, [onGone, onSignedOut]);

  const save = useCallback(async () => {
    const s = latest.current;
    if (busy.current || s.sending || isClean(s.dirty) || s.save === 'conflict') return;
    busy.current = true;
    const body = buildChanges(s, s.dirty);
    dispatch({ type: 'saveStart' });
    try {
      dispatch({ type: 'saveDone', payload: await publicSend<PortalPayload>('PUT', `${path}/event`, body) });
    } catch (err) {
      fail(err);
    } finally {
      busy.current = false;
    }
  }, [fail, path]);

  // Autosave once typing pauses; every edit restarts the wait.
  useEffect(() => {
    if (state.save !== 'pending' || state.sending) return;
    const timer = window.setTimeout(() => void save(), SAVE_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [state.save, state.sending, state.draft, save]);

  // Offline or a server hiccup: try again in a moment.
  useEffect(() => {
    if (state.save !== 'retry') return;
    const timer = window.setTimeout(() => void save(), RETRY_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [state.save, save]);

  // Leaving with changes not yet saved: let the browser ask.
  useEffect(() => {
    const onLeave = (e: BeforeUnloadEvent) => {
      const s = latest.current;
      if (!isClean(mergeDirty(s.dirty, s.sending))) e.preventDefault();
    };
    window.addEventListener('beforeunload', onLeave);
    return () => window.removeEventListener('beforeunload', onLeave);
  }, []);

  const reload = async () => {
    try {
      const next = await publicGet<PortalPayload | PortalSignedOut>(path);
      if (!next.authenticated) onSignedOut(next.venue);
      else dispatch({ type: 'refreshed', payload: next });
    } catch (err) {
      if (err instanceof PublicApiError && err.notFound) onGone();
      else setConfirmError(friendlyError(err));
    }
  };

  const confirm = async (section: SectionKey) => {
    const s = latest.current;
    if (busy.current || s.sending || s.save === 'conflict') return;
    busy.current = true;
    setConfirming(section);
    setConfirmError(null);
    setFlash(null);
    // "Save and confirm": unsaved edits go in the same transaction.
    const changes = isClean(s.dirty) ? undefined : buildChanges(s, s.dirty);
    if (changes) dispatch({ type: 'saveStart' });
    try {
      const next = await publicSend<PortalPayload>('POST', `${path}/confirm`, { sections: [section], ...(changes ? { changes } : {}) });
      dispatch({ type: 'saveDone', payload: next });
      setFlash(
        next.status.status === 'confirmed'
          ? "That's everything confirmed — thank you. The venue has been told."
          : `${SECTION_LABELS[section]} confirmed — thank you. The venue has been told.`,
      );
    } catch (err) {
      setConfirmError(fail(err));
    } finally {
      busy.current = false;
      setConfirming(null);
    }
  };

  const signOut = async () => {
    try {
      await publicSend('POST', `${path}/logout`);
    } catch {
      /* the cookie may already be gone; sign out here regardless */
    }
    onSignedOut(state.server.venue);
  };

  const p = state.server;
  const ev = p.event;
  const brand = brandOf(p.venue);
  const cancelled = p.status.status === 'cancelled';
  const locked = cancelled || state.save === 'conflict';
  const sections = p.status.sections;
  const allDone = SECTIONS.every((s) => sections[s].state === 'confirmed' || sections[s].state === 'skipped');
  const unsaved = describe(dirtyKeys(mergeDirty(state.dirty, state.sending)), p);

  return (
    <PublicShell
      brand={brand}
      actions={
        <>
          <a className="n-btn n-btn--secondary n-btn--sm" href={`/e/${tokenPath(token)}/print`} target="_blank" rel="noopener noreferrer">
            <Icon icon={Printer} size={14} />
            Print
          </a>
          <Button size="sm" variant="quiet" icon={LogOut} onClick={() => void signOut()}>Sign out</Button>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <section aria-labelledby="event-title">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <h1 id="event-title" style={{ margin: 0, fontSize: 'var(--fs-xl)', fontWeight: 700, lineHeight: 1.25, color: 'var(--text)' }}>{ev.name}</h1>
            <Badge tone={STATUS_TONES[p.status.status]}>{STATUS_LABELS[p.status.status]}</Badge>
          </div>
          <div className="fp-facts" style={{ marginTop: 8 }}>
            <span className="fp-fact"><Icon icon={CalendarDays} size={14} tone="muted" />{dateLabel(ev.event_date)}</span>
            {timeRangeLabel(ev.start_time, ev.end_time, ev.ends_next_day) && (
              <span className="fp-fact"><Icon icon={Clock} size={14} tone="muted" />{timeRangeLabel(ev.start_time, ev.end_time, ev.ends_next_day)}</span>
            )}
            {typeof ev.guests === 'number' && <span className="fp-fact"><Icon icon={Users} size={14} tone="muted" />{ev.guests} guests</span>}
            {ev.area_name && <span className="fp-fact"><Icon icon={MapPin} size={14} tone="muted" />{ev.area_name}</span>}
            {p.min_spend && <span className="fp-fact"><Icon icon={Wallet} size={14} tone="muted" />Minimum spend {p.min_spend.amount_text}</span>}
          </div>
          <p style={{ margin: '6px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Signed in as {p.email}</p>
        </section>

        {cancelled && <Notice tone="error">This event has been cancelled. If that doesn&apos;t sound right, please contact the venue.</Notice>}
        {!cancelled && p.status.edited_since_confirmed && (
          <Notice tone="info">You&apos;ve changed things since confirming — the venue has been told. Re-confirm each section once it&apos;s final.</Notice>
        )}
        {!cancelled && allDone && !p.status.edited_since_confirmed && !flash && (
          <Notice tone="ok">Everything&apos;s confirmed — thank you. You can still make changes here; the venue will be told.</Notice>
        )}
        {state.save === 'conflict' && (
          <Notice tone="warn" action={<Button variant="primary" size="sm" onClick={() => void reload()}>Reload</Button>}>
            <strong>The venue changed this event while you were editing.</strong> Reload to see their changes
            {unsaved.length ? <> — your unsaved changes ({unsaved.join(', ')}) are kept wherever the venue didn&apos;t change the same thing.</> : '.'}
          </Notice>
        )}
        {state.overridden.length > 0 && (
          <Notice tone="info" action={<IconButton icon={X} label="Dismiss" onClick={() => dispatch({ type: 'dismissOverridden' })} />}>
            The venue had also changed {describe(state.overridden, p).join(', ')}, so we kept their version. Check it, and change it again if you need to.
          </Notice>
        )}
        {flash && (
          <Notice tone="ok" action={<IconButton icon={X} label="Dismiss" onClick={() => setFlash(null)} />}>{flash}</Notice>
        )}

        <div
          className="fp-no-print"
          style={{ position: 'sticky', top: 0, zIndex: 5, background: 'var(--canvas)', display: 'flex', alignItems: 'center', gap: 12, justifyContent: 'space-between', flexWrap: 'wrap' }}
        >
          <div style={{ flex: '1 1 260px', minWidth: 0 }}>
            <Tabs
              label="Event sections"
              value={tab}
              onChange={(id) => setTab(id as SectionKey)}
              items={SECTIONS.map((s) => ({
                id: s,
                label: (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    {SECTION_LABELS[s]}
                    {sections[s].state === 'confirmed' && <Icon icon={CircleCheck} size={14} style={{ color: 'var(--ok)' }} label="confirmed" />}
                  </span>
                ),
              }))}
            />
          </div>
          <SaveStatus state={state} onRetry={() => void save()} />
        </div>

        <div role="tabpanel" aria-label={SECTION_LABELS[tab]}>
          {tab === 'details' && <DetailsTab state={state} dispatch={dispatch} locked={locked} />}
          {tab === 'beverages' && <DrinksTab state={state} dispatch={dispatch} locked={locked} />}
          {tab === 'food' && <FoodTab state={state} dispatch={dispatch} locked={locked} />}
          <ConfirmBar
            section={tab}
            state={state}
            pending={confirming === tab}
            disabled={locked || !!confirming || state.save === 'saving'}
            error={confirmError}
            onConfirm={() => void confirm(tab)}
          />
        </div>

        <People token={token} state={state} onUpdate={(next) => dispatch({ type: 'refreshed', payload: next })} locked={cancelled} />
      </div>
    </PublicShell>
  );
}

function SaveStatus({ state, onRetry }: { state: PortalState; onRetry: () => void }) {
  const base = { display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', minHeight: 32 } as const;
  switch (state.save) {
    case 'pending':
    case 'saving':
      return <span role="status" style={{ ...base, color: 'var(--muted)' }}><Icon icon={LoaderCircle} size={14} style={{ animation: 'n-spin 1s linear infinite' }} />Saving…</span>;
    case 'saved':
      return <span role="status" style={{ ...base, color: 'var(--muted)' }}><Icon icon={CloudCheck} size={14} />All changes saved</span>;
    case 'retry':
      return (
        <span role="status" style={{ ...base, color: 'var(--warn)' }}>
          <Icon icon={CloudOff} size={14} />Not saved yet — retrying
          <button type="button" className="n-btn n-btn--link" onClick={onRetry} style={{ fontSize: 'inherit' }}>Retry now</button>
        </span>
      );
    case 'rejected':
      return <span role="alert" style={{ ...base, color: 'var(--error)' }}>Couldn&apos;t save: {state.error} That change was undone.</span>;
    case 'conflict':
      return <span role="status" style={{ ...base, color: 'var(--warn)' }}><Icon icon={CloudOff} size={14} />Not saved</span>;
    default:
      return null;
  }
}

function ConfirmBar({ section, state, pending, disabled, error, onConfirm }: {
  section: SectionKey;
  state: PortalState;
  pending: boolean;
  disabled: boolean;
  error: string | null;
  onConfirm: () => void;
}) {
  const s = state.server.status.sections[section];
  const label = SECTION_LABELS[section];
  const cancelled = state.server.status.status === 'cancelled';
  if (cancelled) return null;
  const edited = state.server.status.edited_since_confirmed;
  let text: string;
  let button: string | null;
  if (s.state === 'confirmed') {
    text = `${label} confirmed${s.at ? ` ${factTime(s.at)}` : ''}${s.by_client ? '' : ' by the venue'}.${edited ? ' Re-confirm once your changes are final.' : ''}`;
    button = `Re-confirm ${label.toLowerCase()}`;
  } else if (s.state === 'skipped') {
    text = 'The venue has marked this section as not needed.';
    button = null;
  } else {
    text = 'When this looks right, confirm it so the venue knows it’s final. You can still make changes afterwards.';
    button = `Confirm ${label.toLowerCase()}`;
  }
  return (
    <div className="n-card fp-no-print" style={{ marginTop: 12, padding: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', background: s.state === 'confirmed' ? 'var(--ok-bg)' : 'var(--bg)' }}>
      <div style={{ flex: '1 1 240px', minWidth: 0, display: 'flex', gap: 8, alignItems: 'flex-start', color: s.state === 'confirmed' ? 'var(--ok)' : 'var(--text-soft)' }}>
        {s.state === 'confirmed' && <Icon icon={CircleCheck} size={18} style={{ marginTop: 1 }} />}
        <span style={{ fontSize: 'var(--fs-base)' }}>{text}</span>
      </div>
      {button && (
        <Button variant={s.state === 'confirmed' && !edited ? 'secondary' : 'primary'} onClick={onConfirm} disabled={disabled} aria-busy={pending || undefined}>
          {pending ? 'Confirming…' : button}
        </Button>
      )}
      {error && <p role="alert" style={{ flexBasis: '100%', margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{error}</p>}
    </div>
  );
}

function People({ token, state, onUpdate, locked }: { token: string; state: PortalState; onUpdate: (p: PortalPayload) => void; locked: boolean }) {
  const [email, setEmail] = useState('');
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const people = state.server.people;
  const me = state.server.email.toLowerCase();

  const invite = async (e: FormEvent) => {
    e.preventDefault();
    const address = email.trim();
    if (!EMAIL.test(address)) {
      setMessage({ tone: 'error', text: 'Enter an email address like name@example.com.' });
      return;
    }
    const already = people.some((x) => x.email.toLowerCase() === address.toLowerCase());
    setSending(true);
    setMessage(null);
    try {
      const next = await publicSend<PortalPayload>('POST', `/portal/${tokenPath(token)}/invite`, { email: address });
      onUpdate(next);
      setEmail('');
      setMessage({ tone: 'ok', text: already ? `${address} already has access.` : `We've emailed ${address} a link. They'll sign in with their own code.` });
    } catch (err) {
      setMessage({ tone: 'error', text: friendlyError(err) });
    } finally {
      setSending(false);
    }
  };

  return (
    <Card title="Who can see this page">
      <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {people.map((person) => (
          <li key={person.email} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 'var(--fs-base)' }}>
            <span style={{ color: 'var(--text)', overflowWrap: 'anywhere' }}>{person.email}{person.email.toLowerCase() === me ? ' (you)' : ''}</span>
            <Badge tone={person.verified ? 'ok' : 'neutral'}>{person.verified ? 'Has signed in' : 'Invited'}</Badge>
          </li>
        ))}
      </ul>
      {!locked && (
        <form onSubmit={invite} style={{ margin: 0 }}>
          <label htmlFor="invite-email" className="n-label">Invite a colleague</label>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input
              id="invite-email"
              className="n-input"
              type="email"
              autoComplete="off"
              placeholder="colleague@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              maxLength={320}
              aria-describedby="invite-help"
              style={{ flex: '1 1 220px', minWidth: 0 }}
            />
            <Button type="submit" variant="secondary" disabled={sending || !email.trim()}>{sending ? 'Sending…' : 'Send invite'}</Button>
          </div>
          <p id="invite-help" style={{ margin: '4px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            They&apos;ll get an email with a link to this page, and can view and change it too.
          </p>
          {message && (
            <p role={message.tone === 'error' ? 'alert' : 'status'} style={{ margin: '6px 0 0', fontSize: 'var(--fs-sm)', color: message.tone === 'error' ? 'var(--error)' : 'var(--ok)' }}>
              {message.text}
            </p>
          )}
        </form>
      )}
    </Card>
  );
}
