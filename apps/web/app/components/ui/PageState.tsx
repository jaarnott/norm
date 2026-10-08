'use client';

import type { ReactNode } from 'react';
import { LoaderCircle, TriangleAlert } from 'lucide-react';
import Icon from './Icon';

/**
 * Loading, empty and error states, one look on every page. Errors are shown
 * as errors — a failed load must never read as "No purchase orders found."
 */
export default function PageState({ kind, title, detail, action }: {
  kind: 'loading' | 'empty' | 'error';
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
}) {
  if (kind === 'error') {
    return (
      <div role="alert" style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '12px 14px', borderRadius: 'var(--radius)', background: 'var(--error-bg)', color: 'var(--error)', fontSize: 'var(--fs-base)' }}>
        <Icon icon={TriangleAlert} size={16} style={{ marginTop: 1 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>{title}</div>
          {detail && <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)' }}>{detail}</div>}
        </div>
        {action}
      </div>
    );
  }
  return (
    <div role={kind === 'loading' ? 'status' : undefined} style={{ padding: '40px 16px', textAlign: 'center', color: 'var(--muted)', fontSize: 'var(--fs-base)' }}>
      {kind === 'loading' && (
        <Icon icon={LoaderCircle} size={18} tone="muted" style={{ display: 'block', margin: '0 auto 8px', animation: 'n-spin 1s linear infinite' }} />
      )}
      <div style={{ color: kind === 'empty' ? 'var(--text-soft)' : 'var(--muted)', fontWeight: kind === 'empty' ? 500 : 400 }}>{title}</div>
      {detail && <div style={{ marginTop: 4, fontSize: 'var(--fs-sm)' }}>{detail}</div>}
      {action && <div style={{ marginTop: 12 }}>{action}</div>}
    </div>
  );
}
