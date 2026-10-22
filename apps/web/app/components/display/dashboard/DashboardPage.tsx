'use client';

import { useCallback, useEffect, useState } from 'react';
import { Check, Copy, LayoutGrid, Share2 } from 'lucide-react';
import { apiFetch } from '../../../lib/api';
import { onChange } from '../../../lib/liveChanges';
import AppRunner from '../../apps/AppRunner';
import { useRequestPageFill } from '../../pages/pageFill';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import PageChooser, { type DashboardRef } from './PageChooser';

/**
 * An AI agent's Dashboard page showing a dashboard that is a page of code —
 * the person's own, one shared with their organisation, or one of Norm's
 * templates — exactly as an app's own page shows: Norm's background, the
 * venue picker, the message box floating over it, and Edit for its owner.
 * (Put inside a tile dashboard as one tile, Rich's dashboard lost its Edit
 * button and its background, 22 Oct 2026.)
 */
export default function DashboardPage({ agentSlug }: { agentSlug: string }) {
  const [shown, setShown] = useState<DashboardRef | null | undefined>(undefined);
  const [choosing, setChoosing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  // Showing a dashboard, the page is the dashboard's, as an app's own page is:
  // the whole area, scrolling inside itself under the floating message box.
  useRequestPageFill(!!shown && !choosing, true);

  const load = useCallback(() => {
    apiFetch(`/api/dashboards/${agentSlug}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => setShown(d.dashboard ?? null))
      .catch(() => setShown(null));
  }, [agentSlug]);
  useEffect(load, [load]);

  // Norm made, copied, shared or switched a dashboard for this page.
  useEffect(() => onChange('dashboard', ({ agent }) => { if (!agent || agent === agentSlug) load(); }), [agentSlug, load]);

  const post = async (path: string, body: unknown) => {
    setBusy(true);
    setMessage(null);
    const res = await apiFetch(path, { method: 'POST', body: JSON.stringify(body) });
    setBusy(false);
    if (res.ok) { load(); return; }
    const json = await res.json().catch(() => ({}));
    setMessage(json?.detail || `That didn’t work (error ${res.status}).`);
  };

  if (shown === undefined) return <div className="n-page"><PageState kind="loading" title="Loading the dashboard…" /></div>;
  if (choosing || shown === null) {
    return (
      <PageChooser
        agentSlug={agentSlug}
        onChosen={() => { setChoosing(false); load(); }}
        onCancel={shown ? () => setChoosing(false) : undefined}
      />
    );
  }

  const meta = shown.source === 'mine'
    ? (shown.shared ? 'Yours · shared with your organisation' : 'Yours · only you see it')
    : shown.source === 'template'
      ? 'Norm’s template · make a copy to change it'
      : `${shown.owner}’s, shared with you · make a copy to change it`;

  const actions = (
    <>
      <Button size="sm" variant="secondary" icon={LayoutGrid} onClick={() => setChoosing(true)}>Change dashboard</Button>
      {shown.source !== 'mine' && (
        <Button size="sm" variant="secondary" icon={Copy} disabled={busy} onClick={() => post('/api/dashboards/copy', { ref: shown.ref })}>
          {busy ? 'Copying…' : 'Make a copy'}
        </Button>
      )}
      {shown.source === 'mine' && (
        <Button
          size="sm"
          variant="secondary"
          icon={shown.shared ? Check : Share2}
          disabled={busy}
          title={shown.shared ? 'Everyone in your organisation can use it. Click to stop sharing.' : 'Let everyone in your organisation use it as it is.'}
          onClick={() => {
            if (shown.shared && !window.confirm('Stop sharing it? Anyone using it goes back to their next choice.')) return;
            post('/api/dashboards/share', { slug: shown.slug, shared: !shown.shared });
          }}
        >
          {shown.shared ? 'Shared' : 'Share'}
        </Button>
      )}
    </>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {message && (
        <div role="alert" style={{ margin: '12px 24px 0', color: 'var(--error)', fontSize: 'var(--fs-sm)' }}>{message}</div>
      )}
      <div style={{ flex: 1, minHeight: 0 }}>
        <AppRunner key={shown.ref} slug={shown.slug} variant="page" meta={meta} extraActions={actions} bottomGap={112} />
      </div>
    </div>
  );
}
