/**
 * Whether the signed-in user may use `scope` — from the permissions /auth/me
 * returned (the stored user). Platform admins pass, as they do in the API.
 * Settings hides what the API would refuse rather than letting a click fail
 * with a 403.
 */
export function can(user: { role: string; permissions?: string[] } | null, scope: string): boolean {
  if (!user) return false;
  if (user.role === 'admin') return true;
  return !!user.permissions?.includes(scope);
}
