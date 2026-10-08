'use client';

import type { CSSProperties, ReactNode } from 'react';

/**
 * The frame every menu page sits in: the same gutters everywhere (24px, 16px
 * on phones — tokens.css .n-page), cream background, no max-width and no box
 * around the content. `fill` is for pages that own their whole area (app
 * pages, the roster grid): no padding, and a flex column the child can fill.
 */
export default function PageFrame({ children, fill = false, style }: { children: ReactNode; fill?: boolean; style?: CSSProperties }) {
  if (fill) {
    return <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, ...style }}>{children}</div>;
  }
  return <div className="n-page" style={style}>{children}</div>;
}
