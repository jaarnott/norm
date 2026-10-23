'use client';

import type { ReactNode } from 'react';

/** The 403 state: this person's role doesn't cover the page or the action. */
export default function NoAccess({ what = 'this page', detail }: { what?: string; detail?: ReactNode }) {
  return (
    <div role="status" style={{ padding: '32px 16px', textAlign: 'center', color: 'var(--text-soft)', fontSize: 'var(--fs-base)' }}>
      <div style={{ fontWeight: 600, color: 'var(--text)' }}>You don’t have access to {what}.</div>
      <div style={{ marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        {detail ?? 'Payroll pages need the Payroll permission on your role. Ask an owner if you should have it.'}
      </div>
    </div>
  );
}
