'use client';

/**
 * A single pinned app as a main-panel page: the display-registry adapter that
 * turns { props: { slug } } into the AppRunner. Exists so a pinned app renders
 * through the same FunctionalPage machinery as Invoices and friends.
 */

import AppRunner from './AppRunner';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';

export default function AppPage({ props }: DisplayBlockProps) {
  const slug = (props?.slug as string) || '';
  if (!slug) return <div style={{ padding: '2rem', color: '#888' }}>No app selected.</div>;
  return <AppRunner slug={slug} />;
}
