'use client';

import type { ReactNode } from 'react';

/**
 * The sign-in family of screens — sign in, register, reset password, accept an
 * invite, and the Claude consent screen — share one frame: the tan wordmark,
 * a line saying what this screen is for, one white card, and what sits under
 * it (the "create an account" link). One header, never two.
 */
export default function AuthShell({ subtitle, titleId, children, below, wide = false }: {
  /** What this screen is for — it is the page's heading. Omit it when the
   *  card carries its own heading (the consent screen). */
  subtitle?: ReactNode;
  /** id for the heading, so a form can be labelled by it. */
  titleId?: string;
  children: ReactNode;
  below?: ReactNode;
  /** The consent screen lists a lot; it gets a wider card. */
  wide?: boolean;
}) {
  return (
    <div style={{
      minHeight: '100dvh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 24, backgroundColor: 'var(--canvas)', lineHeight: 1.45,
    }}>
      <div style={{ width: '100%', maxWidth: wide ? 520 : 380 }}>
        <main>
          <header style={{ textAlign: 'center' }}>
            <div aria-hidden style={{ fontSize: 34, fontWeight: 800, lineHeight: 1.1, letterSpacing: '-0.02em', color: 'var(--brand)' }}>Norm</div>
            {subtitle ? (
              <h1 id={titleId} style={{ margin: '4px 0 24px', fontSize: 'var(--fs-md)', fontWeight: 400, color: 'var(--text-soft)' }}>
                {subtitle}
              </h1>
            ) : <div style={{ height: 20 }} />}
          </header>
          <div style={{
            backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)',
            padding: 28, boxShadow: '0 1px 2px rgba(26, 26, 26, 0.04)',
          }}>
            {children}
          </div>
          {below && <div style={{ marginTop: 20, textAlign: 'center', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>{below}</div>}
        </main>
        <footer style={{ marginTop: 40, textAlign: 'center', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          Norm · AI operations for hospitality
        </footer>
      </div>
    </div>
  );
}

/** The auth screens' field: a 44px input with its label above it. */
export const authInput: React.CSSProperties = {
  display: 'block', width: '100%', height: 44, padding: '0 12px',
  border: '1px solid var(--field)', borderRadius: 'var(--radius)', backgroundColor: 'var(--bg)',
  fontSize: 16, color: 'var(--text)',
};
export const authLabel: React.CSSProperties = {
  display: 'block', marginBottom: 6, fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)',
};
export const authSubmit: React.CSSProperties = {
  display: 'flex', width: '100%', height: 44, marginTop: 20, fontSize: 'var(--fs-md)',
};
export const authLink: React.CSSProperties = {
  padding: 0, border: 'none', background: 'none', cursor: 'pointer',
  color: 'var(--accent)', textDecoration: 'underline', textUnderlineOffset: 2, fontSize: 'inherit',
};
