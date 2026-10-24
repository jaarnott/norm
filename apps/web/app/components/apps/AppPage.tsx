'use client';

/**
 * One App-platform app as a display block — a pinned app's page, or the card
 * `norm.open_app` puts in a conversation. Turns { slug, component?, inputs? }
 * (from props for a page, from data for a tool result) into the AppRunner, so
 * a pinned app renders through the same FunctionalPage machinery as Invoices
 * and friends, and Norm can open Hiring on one job's pipeline in chat.
 *
 * `norm.open_app` answers for a team app (Kitchen, Front of house, Managers —
 * docs/plans/team-apps-skeleton.md 3.6) with { team, name, page, label,
 * app_name } and no slug: that is a card naming the screen, whose button asks
 * the shell to open it inside the team app (OPEN_APP_PAGE_EVENT with
 * { team, page }); nothing is fetched, because a team app's screens are not
 * App-platform apps.
 *
 * FunctionalPage marks a page instance with `persistVenue`; anything else is a
 * card in a conversation, which `fill`s the split pane when ThreadDetail puts
 * it there.
 */

import { ExternalLink } from 'lucide-react';
import AppRunner, { OPEN_APP_PAGE_EVENT } from './AppRunner';
import AppIcon from '../ui/AppIcon';
import Button from '../ui/Button';
import PageState from '../ui/PageState';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';

/** The card for a team app's screen: "Kitchen · Prep", from its job app, and
 *  the way in. */
function TeamScreenCard({ team, name, page, label, appName }: {
  team: string; name: string; page: string; label: string; appName?: string | null;
}) {
  const open = () => window.dispatchEvent(new CustomEvent(OPEN_APP_PAGE_EVENT, { detail: { team, page } }));
  return (
    <div className="n-card" data-testid={`team-screen-card-${team}-${page}`} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 10px 10px 14px', flexWrap: 'wrap' }}>
      <AppIcon app={{ slug: team }} size="inline" />
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'block', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{name} · {label}</span>
        {appName && (
          <span style={{ display: 'block', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>From {appName}</span>
        )}
      </span>
      <Button size="sm" variant="secondary" icon={ExternalLink} onClick={open}>Open {label}</Button>
    </div>
  );
}

export default function AppPage({ data, props }: DisplayBlockProps) {
  const pick = (k: string) => props?.[k] ?? data?.[k];
  const slug = (pick('slug') as string) || '';
  const team = (pick('team') as string) || '';
  const page = (pick('page') as string) || '';
  if (!slug && team && page) {
    return (
      <TeamScreenCard
        team={team}
        name={(pick('name') as string) || team}
        page={page}
        label={(pick('label') as string) || page}
        appName={(pick('app_name') as string) || null}
      />
    );
  }
  if (!slug) return <PageState kind="empty" title="No app selected." />;
  return (
    <AppRunner
      slug={slug}
      component={(pick('component') as string) || undefined}
      inputs={(pick('inputs') as Record<string, unknown>) || undefined}
      variant={props?.persistVenue ? 'page' : 'embedded'}
      fill={!!props?.fill}
    />
  );
}
