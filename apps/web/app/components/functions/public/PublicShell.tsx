'use client';

import type { ReactNode } from 'react';
import { Link2Off, LoaderCircle, TriangleAlert } from 'lucide-react';
import Icon from '../../ui/Icon';
import Button from '../../ui/Button';
import type { Brand } from './brand';

/**
 * The frame of every customer page (quote, event portal, print): the venue's
 * name or logo on a white band under a thin rule in the venue's colour, one
 * centred column, the venue's contact line at the foot. No Norm chrome — the
 * customer is dealing with the venue. Phone first: 16px gutters, the column
 * never wider than it needs to be.
 */
export default function PublicShell({ brand, actions, width = 760, children }: {
  brand: Brand | null;
  actions?: ReactNode;
  width?: number;
  children: ReactNode;
}) {
  return (
    <div className="fp-shell" style={{ minHeight: '100dvh', display: 'flex', flexDirection: 'column', background: 'var(--canvas)', lineHeight: 1.45 }}>
      <PublicStyles />
      {(brand || actions) && <header
        className="fp-header"
        style={{
          background: 'var(--bg)',
          borderTop: `4px solid ${brand?.accent ?? 'var(--line-strong)'}`,
          borderBottom: '1px solid var(--line)',
        }}
      >
        <div style={{ maxWidth: width, margin: '0 auto', padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <VenueMark brand={brand} />
          {actions && <div className="fp-no-print" style={{ marginLeft: 'auto', display: 'flex', gap: 8, flexWrap: 'wrap' }}>{actions}</div>}
        </div>
      </header>}
      <main style={{ flex: 1, width: '100%', maxWidth: width, margin: '0 auto', padding: '20px 16px 48px' }}>{children}</main>
      {brand?.footer && (
        <footer style={{ borderTop: '1px solid var(--line)', background: 'var(--bg)' }}>
          <div style={{ maxWidth: width, margin: '0 auto', padding: '14px 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)', whiteSpace: 'pre-line' }}>
            {brand.footer}
          </div>
        </footer>
      )}
    </div>
  );
}

function VenueMark({ brand }: { brand: Brand | null }) {
  if (!brand) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
      {brand.logo ? (
        // eslint-disable-next-line @next/next/no-img-element -- a venue upload served by the API, not a build asset
        <img src={brand.logo} alt={brand.name} style={{ display: 'block', height: 40, width: 'auto', maxWidth: 180, objectFit: 'contain' }} />
      ) : (
        <span style={{ fontFamily: brand.headingFont ?? undefined, fontSize: 'var(--fs-lg)', fontWeight: 700, letterSpacing: '0.01em', color: 'var(--text)' }}>
          {brand.wordmark || brand.name}
        </span>
      )}
      {brand.tagline && (
        <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', minWidth: 0 }}>{brand.tagline}</span>
      )}
    </div>
  );
}

/** One full-page message: a dead link, a load failure, "on its way". */
export function PublicMessage({ kind, title, detail, action, brand = null }: {
  kind: 'gone' | 'error' | 'info';
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
  brand?: Brand | null;
}) {
  const icon = kind === 'gone' ? Link2Off : TriangleAlert;
  return (
    <PublicShell brand={brand} width={560}>
      <div role={kind === 'error' ? 'alert' : undefined} className="n-card" style={{ marginTop: 24, padding: '28px 24px', textAlign: 'center' }}>
        {kind !== 'info' && <Icon icon={icon} size={28} tone="muted" style={{ display: 'block', margin: '0 auto 12px' }} />}
        <h1 style={{ margin: 0, fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--text)' }}>{title}</h1>
        {detail && <p style={{ margin: '8px 0 0', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>{detail}</p>}
        {action && <div style={{ marginTop: 18 }}>{action}</div>}
      </div>
    </PublicShell>
  );
}

/** The one "this link isn't available" page — expired, revoked, unknown or not live look the same. */
export function LinkGone() {
  return (
    <PublicMessage
      kind="gone"
      title="This link isn't available"
      detail="It may have expired or been replaced. Please contact the venue and they'll send you a new one."
    />
  );
}

export function LoadFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <PublicMessage
      kind="error"
      title="This page didn't load"
      detail={message}
      action={<Button variant="primary" onClick={onRetry}>Try again</Button>}
    />
  );
}

export function PublicLoading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div role="status" style={{ minHeight: '60dvh', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--muted)', fontSize: 'var(--fs-base)' }}>
      <PublicStyles />
      <Icon icon={LoaderCircle} size={18} tone="muted" style={{ animation: 'n-spin 1s linear infinite' }} />
      {label}
    </div>
  );
}

/** A notice band inside the page (booked, closed, conflict…). */
export function Notice({ tone, children, action }: { tone: 'ok' | 'warn' | 'info' | 'error' | 'neutral'; children: ReactNode; action?: ReactNode }) {
  const colours = {
    ok: ['var(--ok-bg)', 'var(--ok)'],
    warn: ['var(--warn-bg)', 'var(--warn)'],
    info: ['var(--info-bg)', 'var(--info)'],
    error: ['var(--error-bg)', 'var(--error)'],
    neutral: ['var(--surface-alt)', 'var(--text-soft)'],
  }[tone];
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: '12px 14px', borderRadius: 'var(--radius)', background: colours[0], color: colours[1], fontSize: 'var(--fs-base)' }}
    >
      <div style={{ flex: '1 1 220px', minWidth: 0 }}>{children}</div>
      {action}
    </div>
  );
}

/** What inline styles can't say: print rules and a few responsive grids. */
export function PublicStyles() {
  return (
    <style>{`
      .fp-two { display: grid; grid-template-columns: 1fr; gap: 12px; }
      @media (min-width: 640px) { .fp-two { grid-template-columns: 1fr 1fr; } }
      .fp-facts { display: flex; flex-wrap: wrap; gap: 6px 16px; }
      .fp-fact { display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-base); color: var(--text-soft); }
      .fp-print-only { display: none; }
      @media print {
        @page { margin: 14mm; }
        html, body, .fp-shell { background: #fff !important; }
        .fp-no-print { display: none !important; }
        .fp-print-only { display: block !important; }
        .fp-header { border-bottom-color: #ccc !important; }
        .fp-avoid-break { break-inside: avoid; page-break-inside: avoid; }
        .n-card { box-shadow: none !important; }
        /* The proposal frame must be free to break across pages. */
        .fp-proposal-card { overflow: visible !important; border: none !important; border-radius: 0 !important; }
        a { color: inherit; }
      }
    `}</style>
  );
}
