/**
 * Norm Functions — the customer pages' HTTP helper (/q, /e, /e/print).
 *
 * Deliberately not lib/api's apiFetch: customers have no Norm login, so no
 * bearer token and no "401 → /login" redirect. Calls are relative and
 * same-origin (`/api/public/...`): the load balancer routes /api on the app
 * domain to the API, and Next's dev rewrite does it locally. The portal's
 * session is an httpOnly cookie scoped to the link's API path, which the
 * browser only sends on a same-origin request — fetch's default credentials
 * mode — so nothing here ever handles a token.
 *
 * Errors arrive as `{detail: {code, message, errors?}}` (service errors),
 * `{detail: "text"}` (rate limits) or `{detail: [...]}` (body shape).
 */

export class PublicApiError extends Error {
  status: number;
  code: string | null;
  /** Per-field messages (forms) when the API sent them. */
  errors: Record<string, string> | null;

  constructor(status: number, code: string | null, message: string, errors: Record<string, string> | null = null) {
    super(message);
    this.name = 'PublicApiError';
    this.status = status;
    this.code = code;
    this.errors = errors;
  }

  /** Unknown, revoked, expired, or the organisation isn't live — one answer for all. */
  get notFound(): boolean {
    return this.status === 404;
  }

  /** The portal needs a (new) sign-in. */
  get sessionRequired(): boolean {
    return this.status === 401;
  }
}

/** A message for a failure that isn't the API speaking (offline, 5xx, 429). */
export function friendlyError(err: unknown): string {
  if (err instanceof PublicApiError) {
    if (err.status === 429) return 'Too many tries in a short time. Please wait a minute and try again.';
    if (err.status >= 500) return 'Something went wrong on our side. Please try again in a moment.';
    return err.message;
  }
  return "We couldn't connect. Check your internet connection and try again.";
}

async function parse<T>(res: Response): Promise<T> {
  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (res.ok) return body as T;
  const detail = (body as { detail?: unknown } | null)?.detail;
  let code: string | null = null;
  let message = `Request failed (${res.status})`;
  let errors: Record<string, string> | null = null;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const d = detail as { code?: unknown; message?: unknown; errors?: unknown };
    if (typeof d.code === 'string') code = d.code;
    if (typeof d.message === 'string') message = d.message;
    if (d.errors && typeof d.errors === 'object') errors = d.errors as Record<string, string>;
  } else if (typeof detail === 'string') {
    message = detail;
  } else if (Array.isArray(detail)) {
    message = 'Some of those details could not be saved.';
  }
  throw new PublicApiError(res.status, code, message, errors);
}

/** GET /api/public{path}. */
export async function publicGet<T>(path: string): Promise<T> {
  return parse<T>(await fetch(`/api/public${path}`, { credentials: 'same-origin', cache: 'no-store' }));
}

/** POST/PUT /api/public{path} with a JSON body. */
export async function publicSend<T>(method: 'POST' | 'PUT', path: string, body?: unknown): Promise<T> {
  return parse<T>(
    await fetch(`/api/public${path}`, {
      method,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

/** A path segment from the URL (tokens are [A-Za-z0-9_-]; anything else can't be ours). */
export function tokenPath(token: string): string {
  return encodeURIComponent(token);
}
