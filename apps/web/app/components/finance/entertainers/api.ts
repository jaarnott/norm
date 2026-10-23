/**
 * Entertainers — the one HTTP helper the four staff pages use.
 *
 * Wraps lib/api `apiFetch` (auth header, X-Organization, 401 → login) and
 * turns the API's error shape into an exception the UI can show: the
 * entertainers router answers `{detail: {code, message}}` (codes
 * not_found | forbidden | invalid | conflict | not_live), and FastAPI
 * validation errors arrive as `{detail: [{msg}]}`.
 */
import { apiFetch } from '../../../lib/api';

export class EntApiError extends Error {
  status: number;
  code: string | null;
  detail: unknown;

  constructor(status: number, code: string | null, message: string, detail: unknown) {
    super(message);
    this.name = 'EntApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  /** Shadow mode refused a write that reaches outside Norm (mark paid, a bill). */
  get notLive(): boolean {
    return this.code === 'not_live';
  }

  /** Someone else saved the row since it was loaded. */
  get conflict(): boolean {
    return this.status === 409 && this.code !== 'not_live';
  }

  get forbidden(): boolean {
    return this.status === 403;
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
  throw new EntApiError(res.status, code, message, detail ?? body);
}

/** `?a=1&b=x` from an object; null/undefined/'' values are dropped. */
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

const BASE = '/api/entertainers';

/** GET /api/entertainers{path}. */
export async function entGet<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  return parse<T>(await apiFetch(`${BASE}${path}${qs(params)}`));
}

/** POST/PUT/DELETE /api/entertainers{path} with an optional JSON body. */
export async function entSend<T>(method: 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<T> {
  return parse<T>(
    await apiFetch(`${BASE}${path}`, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

/** Multipart upload to /api/entertainers{path} (documents → ent_files). */
export async function entUpload<T>(path: string, form: FormData): Promise<T> {
  return parse<T>(await apiFetch(`${BASE}${path}`, { method: 'POST', body: form }));
}

/** Fetch a file's bytes (a document, an invoice) and open them in a new tab.
 *  The URL needs the bearer header, so a plain link can't open it. */
export async function openEntFile(path: string, filename?: string): Promise<void> {
  const res = await apiFetch(`${BASE}${path}`);
  if (!res.ok) await parse(res);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const win = window.open(url, '_blank', 'noopener');
  if (!win) {
    // A popup blocker: fall back to a download.
    const a = document.createElement('a');
    a.href = url;
    a.download = filename ?? 'file';
    a.click();
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** What to tell a person when a call failed: the API's own sentence (it is
 *  written for people), or the plain reason in shadow mode. */
export function errorText(e: unknown): string {
  if (e instanceof EntApiError) {
    if (e.notLive) return e.message || 'Not available until entertainers go live in Norm.';
    if (e.forbidden) return e.message || "You don't have permission to do that.";
    return e.message || `Something went wrong (${e.status}).`;
  }
  if (e instanceof Error && e.message) return e.message;
  return 'Something went wrong. Please try again.';
}

/** A 409 that a reload fixes (someone else saved first), not the read-only one. */
export function isStale(e: unknown): boolean {
  return e instanceof EntApiError && e.conflict;
}
