'use client';

/**
 * The venue mailboxes' health above the thread list: connected, syncing,
 * needs reconnecting, or not connected yet (the importer's placeholders —
 * their imported thread list shows, bodies don't). Connecting happens in
 * Settings → Mailboxes.
 */
import { Mail } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import { Notice } from './bits';
import { openMailboxSettings } from './nav';
import { agoLabel, healthBadge } from './text';
import type { Mailbox } from './types';

export default function MailboxStrip({ mailboxes, venueId, now }: { mailboxes: Mailbox[]; venueId: string; now: Date }) {
  const shown = venueId === 'all' ? mailboxes : mailboxes.filter((m) => m.venue_id === venueId);
  const connect = (
    <Button size="sm" onClick={openMailboxSettings}>
      Connect in Settings
    </Button>
  );

  if (!shown.length) {
    return (
      <Notice tone="info" title="No mailbox for this venue yet" action={connect}>
        Connect the venue’s functions mailbox to see and answer its email here.
      </Notice>
    );
  }

  const placeholders = shown.filter((m) => m.health.state === 'not_connected');
  const dead = shown.filter((m) => m.health.state === 'needs_reconnect');
  // All unconnected and several of them: one sentence says it better than six chips.
  if (placeholders.length === shown.length && shown.length > 1) {
    return (
      <Notice tone="warn" title={`None of the ${shown.length} venue mailboxes is connected yet`} action={connect}>
        You’re seeing the conversations imported from Orbit. Connect the mailboxes to read messages, reply and stay in sync.
      </Notice>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {dead.length > 0 && (
        <Notice tone="error" title={dead.length === 1 ? `${dead[0].google_email} needs reconnecting` : `${dead.length} mailboxes need reconnecting`} action={connect}>
          Google stopped Norm’s access, so mail isn’t syncing and sends from {dead.length === 1 ? 'it' : 'them'} are refused.
        </Notice>
      )}
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }} aria-label="Venue mailboxes">
        {shown.map((m) => {
          const badge = healthBadge(m.health);
          const synced = m.health.last_ok_at ? `synced ${agoLabel(m.health.last_ok_at, now)}` : null;
          return (
            <li
              key={m.id}
              title={m.health.last_error ? `Last error: ${m.health.last_error}` : undefined}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                maxWidth: '100%',
                padding: '4px 6px 4px 10px',
                border: '1px solid var(--line)',
                borderRadius: 999,
                background: 'var(--bg)',
                fontSize: 'var(--fs-sm)',
              }}
            >
              <Icon icon={Mail} size={14} tone="muted" />
              {venueId === 'all' && <span style={{ fontWeight: 500, whiteSpace: 'nowrap' }}>{m.venue_name}</span>}
              <span style={{ color: 'var(--muted)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {m.google_email}
              </span>
              {synced && m.health.connected && <span style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>· {synced}</span>}
              <Badge tone={badge.tone}>{badge.label}</Badge>
            </li>
          );
        })}
        {placeholders.length > 0 && dead.length === 0 && (
          <li>
            <button type="button" className="n-btn n-btn--link" onClick={openMailboxSettings} style={{ fontSize: 'var(--fs-sm)' }}>
              Connect in Settings
            </button>
          </li>
        )}
      </ul>
    </div>
  );
}
