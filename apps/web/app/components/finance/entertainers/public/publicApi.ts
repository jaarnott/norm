/**
 * The performer pages' HTTP helper (/p portal, /join onboarding).
 *
 * Deliberately not lib/api's apiFetch: performers have no Norm login, so no
 * bearer token and no "401 → /login" redirect. Calls are same-origin
 * (`/api/public/...`): the load balancer routes /api on the app domain to
 * the API and Next's dev rewrite does it locally. The token lives only in
 * the path; nothing is written to browser storage, ever.
 *
 * Errors arrive as `{detail: {code, message, errors?}}` (the router's own),
 * `{detail: "text"}` (rate limits, with Retry-After) or `{detail: [...]}`.
 */

export class PublicApiError extends Error {
  status: number;
  code: string | null;
  /** Per-field messages when the API sent them (`{"field": "message"}`). */
  errors: Record<string, string> | null;
  retryAfter: number | null;

  constructor(status: number, code: string | null, message: string, errors: Record<string, string> | null = null, retryAfter: number | null = null) {
    super(message);
    this.name = 'PublicApiError';
    this.status = status;
    this.code = code;
    this.errors = errors;
    this.retryAfter = retryAfter;
  }

  /** Unknown, revoked, expired, wrong purpose, or the module isn't on — one answer for all. */
  get notFound(): boolean {
    return this.status === 404;
  }
}

/** A message for a failure that isn't the API speaking (offline, 5xx, 429). */
export function friendlyError(err: unknown): string {
  if (err instanceof PublicApiError) {
    if (err.status === 429) return err.retryAfter ? `Too many tries. Please wait ${err.retryAfter >= 60 ? `${Math.ceil(err.retryAfter / 60)} minute${err.retryAfter >= 120 ? 's' : ''}` : `${err.retryAfter} seconds`} and try again.` : 'Too many tries in a short time. Please wait a minute and try again.';
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
  const retry = res.headers.get('Retry-After');
  throw new PublicApiError(res.status, code, message, errors, retry ? Number(retry) || null : null);
}

const OPTS: RequestInit = { credentials: 'omit', cache: 'no-store' };

export async function publicGet<T>(path: string): Promise<T> {
  return parse<T>(await fetch(`/api/public${path}`, OPTS));
}

export async function publicSend<T>(method: 'POST' | 'PUT', path: string, body?: unknown): Promise<T> {
  return parse<T>(
    await fetch(`/api/public${path}`, {
      ...OPTS,
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

export async function publicUpload<T>(path: string, form: FormData): Promise<T> {
  return parse<T>(await fetch(`/api/public${path}`, { ...OPTS, method: 'POST', body: form }));
}

/** A path segment from the URL (tokens are [A-Za-z0-9_-]; anything else can't be ours). */
export function tokenPath(token: string): string {
  return encodeURIComponent(token);
}

export const portalPath = (token: string) => `/entertainers/${tokenPath(token)}`;
export const joinPath = (token: string) => `/join/${tokenPath(token)}`;

/** The client-side size limit, the same as the server's (10 MB). */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const UPLOAD_ACCEPT = 'application/pdf,image/jpeg,image/png,image/gif,image/webp';
