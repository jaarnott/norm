'use client';

/**
 * Hides children unless the viewer holds an organisation scope.
 *
 * Where the scopes come from: `/api/auth/me` returns `permissions` (the
 * person's role's scopes; a platform admin gets every scope) and
 * app/page.tsx stores that answer as the signed-in user (`getStoredUser`),
 * which is what the page menu's own gate (`pageAllowed` in ThreadList.tsx,
 * F0) reads too. A page that has loaded its module's meta can pass the
 * server's own list (`meta.scopes`) instead — that is the authority when
 * the two differ (a role edited since sign-in).
 *
 * This is a convenience for the menu and the buttons, never the guard: the
 * server refuses every route and tool on its own.
 */
import { useSyncExternalStore, type ReactNode } from 'react';
import { getStoredUser } from '../../../lib/api';

export interface Permissions {
  /** null until the stored user is read (first render on the server). */
  permissions: string[] | null;
  isAdmin: boolean;
  holds: (scope: string) => boolean;
}

/** The stored user's raw JSON — a stable string, so the store never re-renders for nothing. */
function snapshot(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return localStorage.getItem('norm_user');
  } catch {
    return null;
  }
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (e: StorageEvent) => { if (e.key === 'norm_user') onChange(); };
  window.addEventListener('storage', onStorage);
  return () => window.removeEventListener('storage', onStorage);
}

/** The viewer's org scopes, from the stored sign-in; `scopes` (a module
 *  meta's list) overrides them when given. */
export function usePermissions(scopes?: string[] | null): Permissions {
  const raw = useSyncExternalStore(subscribe, snapshot, () => null);
  const user = raw ? getStoredUser() : null;
  const stored = user ? (user.permissions ?? []) : null;
  const isAdmin = user?.role === 'admin';
  const permissions = scopes ?? stored;
  return {
    permissions,
    isAdmin,
    holds: (scope: string) => isAdmin || (permissions?.includes(scope) ?? false),
  };
}

export default function PermissionGate({ scope, scopes, fallback = null, children }: {
  scope: string;
  /** The server's list for this module (`meta.scopes`), when the page has it. */
  scopes?: string[] | null;
  fallback?: ReactNode;
  children: ReactNode;
}) {
  const { holds } = usePermissions(scopes);
  return <>{holds(scope) ? children : fallback}</>;
}
