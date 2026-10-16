/**
 * Norm Functions — the one HTTP helper every Functions page uses.
 *
 * Wraps lib/api `apiFetch` (auth header, 401 → login) and turns the API's
 * error shape into an exception the UI can show: Functions services raise
 * `{detail: {code, message}}` (services/functions/errors.http_exception), and
 * FastAPI validation errors arrive as `{detail: [{msg}]}`.
 */
import { apiFetch } from '../../../lib/api';

export class FnApiError extends Error {
  status: number;
  code: string | null;
  detail: unknown;

  constructor(status: number, code: string | null, message: string, detail: unknown) {
    super(message);
    this.name = 'FnApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  /** Shadow mode (or an org not yet live) refused a write. */
  get notLive(): boolean {
    return this.code === 'not_live';
  }

  /** Someone else saved the record since it was loaded. */
  get conflict(): boolean {
    return this.status === 409 && this.code !== 'not_live';
  }
}

async function parse<T>(res: Response): Promise<T> {
  if (res.ok) {
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error body */
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  let code: string | null = null;
  let message = res.statusText || `Request failed (${res.status})`;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const d = detail as { code?: unknown; message?: unknown };
    if (typeof d.code === 'string') code = d.code;
    if (typeof d.message === 'string') message = d.message;
  } else if (typeof detail === 'string') {
    message = detail;
  } else if (Array.isArray(detail)) {
    message = detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
      .join('; ');
  }
  throw new FnApiError(res.status, code, message, detail ?? body);
}

/** `?a=1&b=x` from an object; null/undefined/'' values and empty arrays are dropped,
 *  arrays repeat the key (`stage=new&stage=replied`). */
export function qs(params?: Record<string, unknown>): string {
  if (!params) return '';
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) {
      for (const item of v) if (item !== undefined && item !== null && item !== '') sp.append(k, String(item));
    } else {
      sp.append(k, String(v));
    }
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

/** GET /api/functions{path}. */
export async function fnGet<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  return parse<T>(await apiFetch(`/api/functions${path}${qs(params)}`));
}

/** POST/PUT/PATCH/DELETE /api/functions{path} with an optional JSON body. */
export async function fnSend<T>(
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
): Promise<T> {
  return parse<T>(
    await apiFetch(`/api/functions${path}`, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

/** Multipart upload to /api/functions{path} (images → fn_files). */
export async function fnUpload<T>(path: string, form: FormData): Promise<T> {
  return parse<T>(await apiFetch(`/api/functions${path}`, { method: 'POST', body: form }));
}

/** A fresh idempotency key for one send (keep it while retrying the same send). */
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
