'use client';

/**
 * One App-platform app as a display block — a pinned app's page, or the card
 * `norm.open_app` puts in a conversation. Turns { slug, component?, inputs? }
 * (from props for a page, from data for a tool result) into the AppRunner, so
 * a pinned app renders through the same FunctionalPage machinery as Invoices
 * and friends, and Norm can open Hiring on one job's pipeline in chat.
 *
 * FunctionalPage marks a page instance with `persistVenue`; anything else is a
 * card in a conversation, which `fill`s the split pane when ThreadDetail puts
 * it there.
 */

import AppRunner from './AppRunner';
import PageState from '../ui/PageState';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';

export default function AppPage({ data, props }: DisplayBlockProps) {
  const pick = (k: string) => props?.[k] ?? data?.[k];
  const slug = (pick('slug') as string) || '';
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
