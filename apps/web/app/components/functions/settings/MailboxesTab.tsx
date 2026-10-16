'use client';

/**
 * Settings → Mailboxes: each venue's functions mailbox — whether Norm is
 * connected to it, when it last synced, what went wrong — with Connect /
 * Reconnect (Google's consent screen, in a popup) and "Sync now". Connecting
 * and syncing work in shadow mode too: mailboxes are connected before going
 * live. Connecting needs the Connections permission (settings:connectors).
 */
import { useCallback, useEffect, useState } from 'react';
import { Mail, RefreshCw } from 'lucide-react';
import Badge, { type BadgeTone } from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageState from '../../ui/PageState';
import { getStoredUser } from '../../../lib/api';
import { can } from '../../../lib/permissions';
import { FnApiError, fnGet, fnSend } from '../shared/api';
import { instantLabel, relativeLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { errorText } from './errors';
import type { Mailbox, MailboxList } from './types';
import { Notice, Panel, Section, help, muted } from './ui';

const HEALTH: Record<Mailbox['health']['state'], { tone: BadgeTone; label: string }> = {
  connected: { tone: 'ok', label: 'Connected' },
  syncing: { tone: 'info', label: 'First sync running' },
  needs_reconnect: { tone: 'error', label: 'Needs reconnecting' },
  not_connected: { tone: 'warn', label: 'Not connected' },
  disabled: { tone: 'neutral', label: 'Switched off' },
};

const FOLDER_LABELS: Record<string, string> = { enquiry: 'Enquiry', reservation: 'Reservation', other: 'Other' };

/** What to say when connecting can't start. */
function connectProblem(e: unknown): string {
  if (e instanceof FnApiError) {
    if (e.code === 'google_not_configured') {
      return 'Norm’s Google sign-in isn’t set up for venue mailboxes yet. A platform admin needs to add Norm’s Google OAuth client (id and secret) to the venue mailbox connector — then Connect will work.';
    }
    if (e.code === 'mailbox_setup') {
      return `Venue mailboxes aren’t set up on this Norm yet — a platform admin has to finish the setup first. Details: ${e.message}`;
    }
    if (e.status === 403) return 'Connecting a mailbox needs the Connections permission — ask an owner of the organisation.';
  }
  return errorText(e);
}

function lagText(seconds: number | null): string {
  if (seconds === null) return '';
  if (seconds < 90) return 'up to date';
  const m = Math.round(seconds / 60);
  return m < 90 ? `${m} min behind` : `${Math.round(m / 60)} h behind`;
}

export default function MailboxesTab({ meta }: { meta: FnMeta }) {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<{ tick: number; data: MailboxList | null; error: string | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, { tone: 'error' | 'ok' | 'info'; text: string; link?: string }>>({});
  const mayConnect = can(getStoredUser(), 'settings:connectors');
  const tz = meta.company.default_timezone;

  useEffect(() => {
    let alive = true;
    fnGet<MailboxList>('/inbox/mailboxes').then(
      (data) => alive && setState({ tick, data, error: null }),
      (e) => alive && setState((s) => ({ tick, data: s?.data ?? null, error: errorText(e) })),
    );
    return () => {
      alive = false;
    };
  }, [tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  // Google's result page (in the popup) tells its opener it's done.
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if ((event.data as { type?: string } | null)?.type === 'oauth-complete') reload();
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [reload]);

  const note = (key: string, n: { tone: 'error' | 'ok' | 'info'; text: string; link?: string } | null) =>
    setNotes((all) => {
      const next = { ...all };
      if (n) next[key] = n;
      else delete next[key];
      return next;
    });

  const connect = async (venueId: string) => {
    setBusy(`connect:${venueId}`);
    note(venueId, null);
    try {
      const { authorize_url } = await fnSend<{ authorize_url: string }>('POST', '/inbox/mailboxes/connect', { venue_id: venueId });
      const popup = window.open(authorize_url, `norm_mailbox_${venueId}`, 'width=600,height=720');
      note(venueId, popup
        ? { tone: 'info', text: 'Finish signing in to Google in the window that opened — this page updates when you’re done.' }
        : { tone: 'info', text: 'Your browser blocked the Google window.', link: authorize_url });
    } catch (e) {
      note(venueId, { tone: 'error', text: connectProblem(e) });
    } finally {
      setBusy(null);
    }
  };

  const syncNow = async (m: Mailbox) => {
    setBusy(`sync:${m.id}`);
    note(m.venue_id, null);
    try {
      const r = await fnSend<{ results: { account_id: string; ok?: boolean; skipped?: string; messages?: number }[] }>('POST', `/inbox/mailboxes/${m.id}/sync`);
      const res = r.results.find((x) => x.account_id === m.id);
      let text = 'Synced.';
      if (!res) text = 'Nothing to sync — the mailbox isn’t connected.';
      else if (res.skipped === 'fresh') text = 'Already up to date — it synced moments ago.';
      else if (res.skipped) text = 'Another sync is running right now — try again in a minute.';
      else if (typeof res.messages === 'number') text = `Synced — ${res.messages} message${res.messages === 1 ? '' : 's'} updated.`;
      note(m.venue_id, { tone: res && res.ok === false ? 'error' : 'ok', text });
      reload();
    } catch (e) {
      note(m.venue_id, { tone: 'error', text: errorText(e) });
    } finally {
      setBusy(null);
    }
  };

  const data = state?.data;
  if (state?.error && !data) return <PageState kind="error" title="Couldn’t load the mailboxes" detail={state.error} />;
  if (!data) return <PageState kind="loading" title="Loading mailboxes…" />;

  const byId = new Map(data.mailboxes.map((m) => [m.id, m]));
  const broken = data.mailboxes.filter((m) => m.health.state === 'needs_reconnect').length;

  return (
    <Section
      title="Mailboxes"
      meta="The venue inboxes Norm reads and sends from. Norm checks them every minute."
      actions={<Button size="sm" variant="quiet" icon={RefreshCw} onClick={reload}>Refresh</Button>}
    >
      {broken > 0 && (
        <Notice tone="error" style={{ marginBottom: 12 }}>
          {broken === 1 ? 'A mailbox needs' : `${broken} mailboxes need`} reconnecting — Google stopped accepting Norm’s sign-in. Sends from
          {broken === 1 ? ' it' : ' them'} are paused until someone reconnects.
        </Notice>
      )}
      {!mayConnect && (
        <p style={{ ...help, marginTop: 0, marginBottom: 12 }}>Connecting a mailbox needs the Connections permission — ask an owner if one needs connecting.</p>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {data.venues.map((v) => {
          const boxes = v.mailbox_ids.map((id) => byId.get(id)).filter((m): m is Mailbox => !!m);
          const n = notes[v.venue_id];
          return (
            <Panel key={v.venue_id}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>{v.venue_name}</div>
              {boxes.length === 0 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <span style={muted}>No mailbox connected yet.</span>
                  {mayConnect && (
                    <Button size="sm" icon={Mail} onClick={() => connect(v.venue_id)} disabled={!!busy}>
                      {busy === `connect:${v.venue_id}` ? 'Starting…' : 'Connect a mailbox'}
                    </Button>
                  )}
                </div>
              )}
              {boxes.map((m) => {
                const h = HEALTH[m.health.state] ?? HEALTH.not_connected;
                const missing = Object.entries(m.folders).filter(([, ok]) => !ok).map(([k]) => FOLDER_LABELS[k] ?? k);
                const needsConnect = m.health.state === 'not_connected' || m.health.state === 'needs_reconnect';
                return (
                  <div key={m.id} style={{ borderTop: boxes.indexOf(m) ? '1px solid var(--line-soft)' : 'none', paddingTop: boxes.indexOf(m) ? 10 : 0, marginTop: boxes.indexOf(m) ? 10 : 0 }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
                      <Icon icon={Mail} size={18} tone="muted" style={{ marginTop: 2 }} />
                      <div style={{ flex: '1 1 240px', minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <span style={{ fontWeight: 500, overflowWrap: 'anywhere' }}>{m.google_email}</span>
                          <Badge tone={h.tone}>{h.label}</Badge>
                          {m.health.lagging && <Badge tone="warn">Behind</Badge>}
                        </div>
                        <div style={{ ...muted, marginTop: 2 }}>
                          {m.display_name ? `${m.display_name} · ` : ''}
                          {m.health.last_ok_at
                            ? `Last synced ${relativeLabel(m.health.last_ok_at)} (${instantLabel(m.health.last_ok_at, tz)})${m.health.connected ? ` · ${lagText(m.health.lag_seconds)}` : ''}`
                            : m.health.state === 'not_connected' ? 'Imported from Orbit — connect it so Norm can read and send.' : 'Not synced yet'}
                        </div>
                        {m.health.last_error && (
                          <div style={{ marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>
                            {m.health.last_error}{m.health.consecutive_failures > 1 ? ` (failed ${m.health.consecutive_failures} times in a row)` : ''}
                          </div>
                        )}
                        {missing.length > 0 && m.health.connected && (
                          <div style={{ marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--warn)' }}>
                            No “Actioned – {missing.join('” / “Actioned – ')}” label in this mailbox, so filing threads there won’t show in Gmail.
                          </div>
                        )}
                      </div>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {m.health.connected && (
                          <Button size="sm" icon={RefreshCw} onClick={() => syncNow(m)} disabled={!!busy}>
                            {busy === `sync:${m.id}` ? 'Syncing…' : 'Sync now'}
                          </Button>
                        )}
                        {mayConnect && (needsConnect || m.health.connected) && (
                          <Button size="sm" variant={m.health.state === 'needs_reconnect' ? 'primary' : needsConnect ? 'secondary' : 'quiet'} icon={Mail} onClick={() => connect(v.venue_id)} disabled={!!busy}>
                            {busy === `connect:${v.venue_id}` ? 'Starting…' : m.health.state === 'not_connected' ? 'Connect' : 'Reconnect'}
                          </Button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
              {n && (
                <Notice tone={n.tone} style={{ marginTop: 10 }}>
                  {n.text}
                  {n.link && <> <a href={n.link} target="_blank" rel="noopener noreferrer" style={{ color: 'inherit', fontWeight: 600 }}>Continue to Google</a></>}
                </Notice>
              )}
            </Panel>
          );
        })}
      </div>
      {data.venues.length === 0 && <Panel><PageState kind="empty" title="No venues to show" /></Panel>}
    </Section>
  );
}
