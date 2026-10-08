'use client';

import type { ReactNode } from 'react';
import BackLink from './BackLink';

/**
 * The one page header: optional back link, the title (20px/700), a status
 * slot beside it, one meta line under it, and actions on the right (venue
 * picker, secondary, one primary). `children` is a toolbar row underneath —
 * tabs, filters, a search box.
 */
export default function PageHeader({ title, meta, back, status, actions, children, titleOnPhone = false }: {
  title: ReactNode;
  /** Keep the title on phones even though the top bar names the page — for a
   *  title that says more than the menu label (which dashboard is open). A page
   *  with a back link is a sub-page and always keeps its title. */
  titleOnPhone?: boolean;
  meta?: ReactNode;
  back?: { label: string; onClick: () => void; disabled?: boolean };
  status?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header style={{ marginBottom: 16 }}>
      {back && (
        <div style={{ marginBottom: 6 }}>
          <BackLink label={back.label} onClick={back.onClick} disabled={back.disabled} />
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {/* .n-page-title: on phones the shell's top bar already names the
                page, so this is hidden there (globals.css). */}
            <h1 className={back || titleOnPhone ? undefined : 'n-page-title'} style={{ margin: 0, fontSize: 'var(--fs-xl)', fontWeight: 700, letterSpacing: '-0.01em', lineHeight: 1.25, color: 'var(--text)' }}>
              {title}
            </h1>
            {status}
          </div>
          {meta && <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta}</div>}
        </div>
        {actions && <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>{actions}</div>}
      </div>
      {children && <div style={{ marginTop: 12 }}>{children}</div>}
    </header>
  );
}
