'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, Printer } from 'lucide-react';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import EventSheet from './EventSheet';
import PublicShell, { LinkGone, LoadFailed, PublicLoading } from './PublicShell';
import { brandOf } from './brand';
import { PublicApiError, friendlyError, publicGet, tokenPath } from './publicApi';
import type { PortalPayload } from './types';

type View = { kind: 'loading' } | { kind: 'gone' } | { kind: 'error'; message: string } | { kind: 'ready'; payload: PortalPayload };

/**
 * /e/<token>/print — the printable event sheet. Needs the portal session
 * (Orbit's print page needed only the link): without one it goes back to the
 * portal to sign in.
 */
export default function PrintPage({ token }: { token: string }) {
  const router = useRouter();
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const portal = `/e/${tokenPath(token)}`;

  useEffect(() => {
    let live = true;
    publicGet<PortalPayload>(`/portal/${tokenPath(token)}/print`).then(
      (payload) => {
        if (live) setView({ kind: 'ready', payload });
      },
      (err) => {
        if (!live) return;
        if (err instanceof PublicApiError && err.sessionRequired) router.replace(portal);
        else if (err instanceof PublicApiError && err.notFound) setView({ kind: 'gone' });
        else setView({ kind: 'error', message: friendlyError(err) });
      },
    );
    return () => {
      live = false;
    };
  }, [portal, router, token, attempt]);

  if (view.kind === 'loading') return <PublicLoading label="Preparing your event sheet…" />;
  if (view.kind === 'gone') return <LinkGone />;
  if (view.kind === 'error') return <LoadFailed message={view.message} onRetry={() => { setView({ kind: 'loading' }); setAttempt((n) => n + 1); }} />;

  return (
    <PublicShell
      brand={brandOf(view.payload.venue)}
      width={840}
      actions={
        <>
          <a className="n-btn n-btn--quiet n-btn--sm" href={portal}>
            <Icon icon={ArrowLeft} size={14} />
            Back to your event
          </a>
          <Button size="sm" variant="primary" icon={Printer} onClick={() => window.print()}>Print or save as PDF</Button>
        </>
      }
    >
      <EventSheet payload={view.payload} />
    </PublicShell>
  );
}
