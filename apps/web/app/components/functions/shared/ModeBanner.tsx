'use client';

import type { FunctionsMode } from './meta';

/**
 * Shown at the top of every Functions page while the org is in shadow mode:
 * Orbit is still the live system, Norm's copy is refreshed from it and is
 * read-only here.
 */
export default function ModeBanner({ mode }: { mode: FunctionsMode | undefined }) {
  if (mode !== 'shadow') return null;
  return (
    <div
      role="status"
      style={{
        background: 'var(--warn-bg)',
        color: 'var(--warn)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--radius)',
        padding: '8px 12px',
        fontSize: 'var(--fs-sm)',
        marginBottom: 12,
      }}
    >
      Shadow mode — Orbit is still the live system. This is a read-only copy, refreshed from Orbit; changes are made in
      Orbit until Norm Functions goes live.
    </div>
  );
}
