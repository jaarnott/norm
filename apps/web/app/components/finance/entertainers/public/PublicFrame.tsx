'use client';

/**
 * The frame of a performer's page (portal, onboarding): no Norm chrome, one
 * centred column, phone first — 16px gutters, 44px tap targets, Figtree
 * from the root layout. A small wordmark line for the organisation when
 * the page knows it; nothing else to click away to.
 */
import type { ReactNode } from 'react';
import { Link2Off, LoaderCircle, TriangleAlert } from 'lucide-react';
import Icon from '../../../ui/Icon';
import Button from '../../../ui/Button';

export default function PublicFrame({ organisation, width = 600, children }: { organisation?: string | null; width?: number; children: ReactNode }) {
  return (
    <div style={{ minHeight: '100dvh', display: 'flex', flexDirection: 'column', background: 'var(--canvas)', lineHeight: 1.45 }}>
      <style>{`
        .ep-two { display: grid; grid-template-columns: 1fr; gap: 12px; }
        @media (min-width: 560px) { .ep-two { grid-template-columns: 1fr 1fr; } }
        .ep-btn-full { display: flex; width: 100%; height: 46px; font-size: var(--fs-md); }
        .ep-choice { display: block; width: 100%; text-align: left; padding: 14px 16px; border-radius: var(--radius-lg); border: 1px solid var(--line-strong); background: var(--bg); font: inherit; color: var(--text); cursor: pointer; min-height: 48px; }
        .ep-choice[aria-pressed="true"] { border-color: var(--brand-soft); background: var(--accent-soft); box-shadow: inset 0 0 0 1px var(--brand-soft); }
        .ep-check { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--bg); font-size: var(--fs-base); cursor: pointer; }
        .ep-check input { width: 18px; height: 18px; margin: 2px 0 0; accent-color: var(--accent); flex: 0 0 auto; }
        .ep-input { width: 100%; height: 46px; font-size: var(--fs-md); }
        @media print { .ep-no-print { display: none !important; } }
      `}</style>
      <header style={{ background: 'var(--bg)', borderTop: '4px solid var(--brand-soft)', borderBottom: '1px solid var(--line)' }}>
        <div style={{ maxWidth: width, margin: '0 auto', padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, letterSpacing: '0.01em', color: 'var(--text)' }}>{organisation || 'Performers'}</span>
        </div>
      </header>
      <main style={{ flex: 1, width: '100%', maxWidth: width, margin: '0 auto', padding: '20px 16px 56px', boxSizing: 'border-box' }}>{children}</main>
    </div>
  );
}

export function PublicMessage({ kind, title, detail, action, organisation }: {
  kind: 'gone' | 'error' | 'info' | 'done';
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
  organisation?: string | null;
}) {
  const icon = kind === 'gone' ? Link2Off : kind === 'error' ? TriangleAlert : null;
  return (
    <PublicFrame organisation={organisation} width={520}>
      <div role={kind === 'error' ? 'alert' : 'status'} className="n-card" style={{ marginTop: 24, padding: '28px 22px', textAlign: 'center' }}>
        {icon && <Icon icon={icon} size={28} tone="muted" style={{ display: 'block', margin: '0 auto 12px' }} />}
        <h1 style={{ margin: 0, fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--text)' }}>{title}</h1>
        {detail && <p style={{ margin: '8px 0 0', fontSize: 'var(--fs-md)', color: 'var(--text-soft)' }}>{detail}</p>}
        {action && <div style={{ marginTop: 18 }}>{action}</div>}
      </div>
    </PublicFrame>
  );
}

/** Expired, replaced, revoked, unknown, or not open yet — one page for all. */
export function LinkGone() {
  return (
    <PublicMessage
      kind="gone"
      title="This link has expired or moved"
      detail="Links are personal and only live for a while. Ask the venue or the finance team to send you a new one."
    />
  );
}

export function LoadFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <PublicMessage kind="error" title="This page didn’t load" detail={message} action={<Button variant="primary" onClick={onRetry}>Try again</Button>} />;
}

export function PublicLoading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div role="status" style={{ minHeight: '60dvh', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--muted)', fontSize: 'var(--fs-md)' }}>
      <Icon icon={LoaderCircle} size={18} tone="muted" style={{ animation: 'n-spin 1s linear infinite' }} />
      {label}
    </div>
  );
}

export function Card({ children, tone }: { children: ReactNode; tone?: 'accent' | 'warn' }) {
  return (
    <section className="n-card" style={{ padding: '16px', marginBottom: 12, borderColor: tone === 'accent' ? 'var(--brand-soft)' : tone === 'warn' ? '#e3c98f' : undefined }}>
      {children}
    </section>
  );
}

export function ErrorLine({ id, children }: { id?: string; children: ReactNode }) {
  return <p id={id} role="alert" style={{ margin: '8px 0 0', fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--error)' }}>{children}</p>;
}
