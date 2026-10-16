'use client';

import { useEffect, useState } from 'react';
import PortalEvent from './PortalEvent';
import PortalSignIn from './PortalSignIn';
import { LinkGone, LoadFailed, PublicLoading } from './PublicShell';
import { PublicApiError, friendlyError, publicGet, tokenPath } from './publicApi';
import type { PortalPayload, PortalSignedOut, PublicVenue } from './types';

type View =
  | { kind: 'loading' }
  | { kind: 'gone' }
  | { kind: 'error'; message: string }
  | { kind: 'signin'; venue: PublicVenue | null; notice?: string }
  | { kind: 'event'; payload: PortalPayload };

/**
 * /e/<token> — the client portal. The link alone shows only who it's from;
 * the event appears once a code emailed to an allowed address is verified
 * (the API then sets an httpOnly session cookie scoped to this link).
 */
export default function PortalPage({ token }: { token: string }) {
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    publicGet<PortalPayload | PortalSignedOut>(`/portal/${tokenPath(token)}`).then(
      (data) => {
        if (live) setView(data.authenticated ? { kind: 'event', payload: data } : { kind: 'signin', venue: data.venue });
      },
      (err) => {
        if (live) setView(err instanceof PublicApiError && err.notFound ? { kind: 'gone' } : { kind: 'error', message: friendlyError(err) });
      },
    );
    return () => {
      live = false;
    };
  }, [token, attempt]);

  switch (view.kind) {
    case 'loading':
      return <PublicLoading label="Loading your event…" />;
    case 'gone':
      return <LinkGone />;
    case 'error':
      return <LoadFailed message={view.message} onRetry={() => { setView({ kind: 'loading' }); setAttempt((n) => n + 1); }} />;
    case 'signin':
      return (
        <PortalSignIn
          token={token}
          venue={view.venue}
          notice={view.notice}
          onSignedIn={(payload) => setView({ kind: 'event', payload })}
        />
      );
    default:
      return (
        <PortalEvent
          // A fresh sign-in starts a fresh working copy.
          key={view.payload.email}
          token={token}
          initial={view.payload}
          onSignedOut={(venue, notice) => setView({ kind: 'signin', venue, notice })}
          onGone={() => setView({ kind: 'gone' })}
        />
      );
  }
}
