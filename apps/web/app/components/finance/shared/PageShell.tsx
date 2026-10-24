'use client';

/**
 * The frame every finance page sits in: the mode banner, the page header
 * (title, meta, status, actions, a toolbar row for tabs or filters), the
 * venue switcher, and the toast host. On a menu page (`isPage`, i.e. the
 * registry mounted it with `persistVenue`) it is the full PageHeader; in a
 * conversation it is a compact title row, the FnHeader pattern.
 *
 * `embedded`: the screen is a tab body inside a workspace page (Payroll,
 * Entertainers, Budgets) whose own PageShell already draws the banner, the
 * header and the one ToastHost — so it renders its children only. Two
 * ToastHosts would show and announce every toast twice.
 */
import type { ReactNode } from 'react';
import PageHeader from '../../ui/PageHeader';
import Badge from '../../ui/Badge';
import VenueSelect, { type VenueOption } from '../../ui/VenueSelect';
import { ToastHost } from './Toasts';

export type FinanceMode = 'off' | 'shadow' | 'live';

/** "Trial run — Orbit is still the system of record" at the top of the page
 *  while the module is in shadow (the code value stays `shadow`); nothing
 *  while it is live or unknown. */
export function ModeBanner({ mode, module = 'Payroll' }: { mode: FinanceMode | null | undefined; module?: string }) {
  if (mode !== 'shadow') return null;
  return (
    <div
      role="status"
      style={{
        background: 'var(--warn-bg)', color: 'var(--warn)', border: '1px solid var(--line)',
        borderRadius: 'var(--radius)', padding: '8px 12px', fontSize: 'var(--fs-sm)', marginBottom: 12,
      }}
    >
      <strong>Trial run</strong> — Orbit is still the system of record. Norm computes {module.toLowerCase()} beside it; nothing
      here reaches Ace or Xero. Inputs keyed here are rehearsals until the cut-over.
    </div>
  );
}

export function ModeBadge({ mode }: { mode: FinanceMode | null | undefined }) {
  if (mode === 'live') return <Badge tone="ok">Live</Badge>;
  if (mode === 'shadow') return <Badge tone="warn">Trial run</Badge>;
  if (mode === 'off') return <Badge tone="neutral">Off</Badge>;
  return null;
}

export default function PageShell({ isPage, embedded = false, title, meta, mode, module, status, actions, venues, venueId, onVenueChange, venueTestId, toolbar, children, testId }: {
  isPage: boolean;
  /** A tab body inside a workspace page: children only (no banner, header or toast host). */
  embedded?: boolean;
  title: ReactNode;
  meta?: ReactNode;
  mode?: FinanceMode | null;
  module?: string;
  status?: ReactNode;
  actions?: ReactNode;
  /** The venue switcher: the entities the person can see. Omit for a group page. */
  venues?: VenueOption[];
  venueId?: string | null;
  onVenueChange?: (venueId: string) => void;
  venueTestId?: string;
  /** Tabs, filters, the week picker — the row under the header. */
  toolbar?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  if (embedded) return <div data-testid={testId} style={{ minWidth: 0 }}>{children}</div>;
  const picker = venues && onVenueChange && venues.length > 0 ? (
    <VenueSelect venues={venues} value={venueId ?? venues[0]?.id ?? null} onChange={onVenueChange} testId={venueTestId} />
  ) : null;
  const statusNode = (
    <>
      <ModeBadge mode={mode} />
      {status}
    </>
  );
  const actionNode = picker || actions ? (
    <>
      {picker}
      {actions}
    </>
  ) : undefined;

  return (
    <div data-testid={testId} style={{ minWidth: 0 }}>
      <ModeBanner mode={mode} module={module} />
      {isPage ? (
        <PageHeader title={title} meta={meta} status={statusNode} actions={actionNode}>{toolbar}</PageHeader>
      ) : (
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{title}</span>
                {statusNode}
              </div>
              {meta && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</div>}
            </div>
            {actionNode && <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>{actionNode}</div>}
          </div>
          {toolbar && <div style={{ marginTop: 10 }}>{toolbar}</div>}
        </div>
      )}
      {children}
      <ToastHost />
    </div>
  );
}

/** A white card with a title row — the section every finance page is built from. */
export function Section({ title, meta, actions, children, padded = true, testId, tone }: {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  padded?: boolean;
  testId?: string;
  /** A tinted header for a section that needs attention. */
  tone?: 'warn' | 'error';
}) {
  return (
    <section className="n-card" data-testid={testId} style={{ minWidth: 0 }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', padding: '12px 16px',
        borderBottom: children ? '1px solid var(--line-soft)' : undefined,
        background: tone === 'warn' ? 'var(--warn-bg)' : tone === 'error' ? 'var(--error-bg)' : undefined,
        borderRadius: 'var(--radius-lg) var(--radius-lg) 0 0',
      }}>
        <div style={{ minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, color: tone === 'error' ? 'var(--error)' : tone === 'warn' ? 'var(--warn)' : 'var(--text)' }}>{title}</h2>
          {meta && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{meta}</span>}
        </div>
        {actions && <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>{actions}</div>}
      </div>
      {children && <div style={padded ? { padding: '12px 16px' } : undefined}>{children}</div>}
    </section>
  );
}

/** Label / value pairs in a responsive grid. */
export function Facts({ items, min = 150 }: { items: { label: string; value: ReactNode }[]; min?: number }) {
  return (
    <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: `repeat(auto-fill, minmax(${min}px, 1fr))`, gap: '10px 18px' }}>
      {items.map((it) => (
        <div key={it.label} style={{ minWidth: 0 }}>
          <dt style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 2 }}>{it.label}</dt>
          <dd style={{ margin: 0, fontSize: 'var(--fs-base)', color: 'var(--text)', overflowWrap: 'anywhere' }}>{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The vertical stack of sections a page is. */
export function Stack({ children, gap = 12 }: { children: ReactNode; gap?: number }) {
  return <div style={{ display: 'flex', flexDirection: 'column', gap, minWidth: 0 }}>{children}</div>;
}

/** A labelled control; `children` receives the id to put on the control. */
export function Field({ label, hint, id, children, style }: {
  label: ReactNode;
  hint?: ReactNode;
  id: string;
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div style={{ minWidth: 0, ...style }}>
      <label className="n-label" htmlFor={id}>{label}</label>
      {children}
      {hint && <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{hint}</div>}
    </div>
  );
}

export function FormGrid({ children, min = 180 }: { children: ReactNode; min?: number }) {
  return <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fill, minmax(${min}px, 1fr))`, gap: 12 }}>{children}</div>;
}

/** A small inline note: ok / warn / error / plain. */
export function Note({ tone = 'plain', children }: { tone?: 'ok' | 'warn' | 'error' | 'plain'; children: ReactNode }) {
  const color = tone === 'ok' ? 'var(--ok)' : tone === 'warn' ? 'var(--warn)' : tone === 'error' ? 'var(--error)' : 'var(--text-soft)';
  const bg = tone === 'ok' ? 'var(--ok-bg)' : tone === 'warn' ? 'var(--warn-bg)' : tone === 'error' ? 'var(--error-bg)' : 'var(--surface-alt)';
  return (
    <div role={tone === 'error' ? 'alert' : undefined} style={{ padding: '8px 12px', borderRadius: 'var(--radius)', background: bg, color, fontSize: 'var(--fs-sm)' }}>
      {children}
    </div>
  );
}
