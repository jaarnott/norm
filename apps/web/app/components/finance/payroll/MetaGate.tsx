'use client';

/**
 * The states `GET /api/payroll/meta` can leave the Payroll page in: no
 * access (403), Payroll not switched on for the organisation (404), an
 * error, or still loading. The Payroll page mounts it once, around every
 * tab.
 */
import type { ReactNode } from 'react';
import PageState from '../../ui/PageState';
import NoAccess from '../shared/NoAccess';

export default function MetaGate({ status, error, loading, children }: { status: number | null; error: string | null; loading: boolean; children: ReactNode }) {
  if (status === 403) return <NoAccess />;
  if (status === 404) return <PageState kind="empty" title="Payroll isn’t switched on for this organisation." detail="A platform admin turns it on for a trial run first, then live." />;
  if (error) return <PageState kind="error" title="Could not load Payroll." detail={error} />;
  if (loading) return <PageState kind="loading" title="Loading…" />;
  return <>{children}</>;
}
