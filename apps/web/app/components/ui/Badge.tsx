'use client';

import type { ReactNode } from 'react';

/**
 * Status pill: sentence case, 11px/600, text on its own tint (all ≥5.2:1).
 * ok = done/received/on · warn = waiting on someone (approval) · error =
 * failed/blocked · info = draft/informational · accent = needs your input ·
 * neutral = everything else.
 */
export type BadgeTone = 'neutral' | 'ok' | 'warn' | 'error' | 'info' | 'accent';

export default function Badge({ tone = 'neutral', children, title }: { tone?: BadgeTone; children: ReactNode; title?: string }) {
  return (
    <span className={tone === 'neutral' ? 'n-badge' : `n-badge n-badge--${tone}`} title={title}>
      {children}
    </span>
  );
}
