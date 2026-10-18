'use client';

/**
 * The one way a page asks for a connection: a centred heading, one soft line
 * of detail, the connect card under it, and (when the page can go on without
 * it) a Dismiss. Used by the app's reconnect panel (a page load failed on a
 * dead token, or a ?connect= deep link) and by pages that need a connector of
 * their own (Finance → Bills). Chat blocks use the compact form.
 *
 * The card itself is ConnectorConnectCard — the same per-venue Connect /
 * Reconnect rows chat and Settings → Connections show — so the flow, the
 * statuses and the "connected as" line are identical everywhere.
 */

import React from 'react';
import type { WidgetAction } from '../../types';
import ConnectorConnectCard from './ConnectorConnectCard';
import Button from '../ui/Button';

export default function ConnectPrompt({
  connector,
  venueId,
  title,
  detail,
  reconnect = false,
  compact = false,
  onAction,
  onDismiss,
}: {
  connector: string;
  /** Show only this venue's row (a page about one venue). */
  venueId?: string | null;
  title: string;
  detail?: string;
  reconnect?: boolean;
  /** Inside a chat block: smaller heading, no outer spacing. */
  compact?: boolean;
  onAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  onDismiss?: () => void;
}) {
  return (
    <div style={{ maxWidth: 560, margin: '0 auto', padding: compact ? 0 : '2.5rem 1rem 1rem', textAlign: 'center' }}>
      <h2 style={{ margin: '0 0 0.35rem', fontSize: compact ? 'var(--fs-md)' : 'var(--fs-xl)', fontWeight: 700, color: 'var(--text)' }}>
        {title}
      </h2>
      {detail && (
        <div style={{ fontSize: compact ? 'var(--fs-sm)' : 'var(--fs-base)', color: 'var(--text-soft)', marginBottom: compact ? '0.5rem' : '0.75rem' }}>
          {detail}
        </div>
      )}
      <div style={{ width: '100%', maxWidth: 460, margin: '0 auto', textAlign: 'left' }}>
        <ConnectorConnectCard
          data={{ connector_name: connector, reconnect, ...(venueId ? { venue_id: venueId } : {}) }}
          onAction={onAction ?? (async () => {})}
        />
      </div>
      {onDismiss && (
        <Button size="sm" variant="secondary" onClick={onDismiss} style={{ marginTop: '1rem' }}>
          Dismiss
        </Button>
      )}
    </div>
  );
}
