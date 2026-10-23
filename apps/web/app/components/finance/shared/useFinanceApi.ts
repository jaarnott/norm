'use client';

/**
 * Finance pages — the one HTTP layer every Payroll, Entertainers and Finance
 * page uses. Wraps lib/api `apiFetch` (auth header, X-Organization, 401 →
 * login) and turns the API's error shape into an exception the UI can show:
 * the finance routers raise `{detail: {code, message}}`
 * (services/finance_core/errors), the page-write routes raise
 * `{detail: {error, changed, fingerprint}}` on a 409, and FastAPI validation
 * errors arrive as `{detail: [{msg}]}`.
 *
 * Reads throw (the page renders the error, or NoAccess on a 403). Writes go
 * through `run()`, which shows the failure as a toast and resolves to
 * undefined, so a row button needs no error state of its own; a dialog that
 * wants the reason inline calls the raw method instead.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../../../lib/api';
import { toast } from './Toasts';

export class FinApiError extends Error {
  status: number;
  code: string | null;
  detail: unknown;

  constructor(status: number, code: string | null, message: string, detail: unknown) {
    super(message);
    this.name = 'FinApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  /** Shadow mode refused a write (send `rehearsal: true` or wait for live). */
  get notLive(): boolean {
    return this.code === 'not_live';
  }

  /** Someone else saved the record since it was loaded (send the new version). */
  get conflict(): boolean {
    return this.status === 409 && this.code !== 'not_live';
  }

  get forbidden(): boolean {
    return this.status === 403;
  }

  get notFound(): boolean {
    return this.status === 404;
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
    const d = detail as { code?: unknown; message?: unknown; error?: unknown };
    if (typeof d.code === 'string') code = d.code;
    if (typeof d.message === 'string') message = d.message;
    else if (typeof d.error === 'string') message = d.error;
  } else if (typeof detail === 'string') {
    message = detail;
  } else if (Array.isArray(detail)) {
    message = detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
      .join('; ');
  }
  if (res.status === 403 && !code) code = 'forbidden';
  if (res.status === 404 && !code) code = 'not_found';
  throw new FinApiError(res.status, code, message, detail ?? body);
}

/** `?a=1&b=x` from an object; null/undefined/'' values are dropped, arrays repeat the key. */
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

export async function finGet<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  return parse<T>(await apiFetch(`${path}${qs(params)}`));
}

export async function finSend<T>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown): Promise<T> {
  return parse<T>(await apiFetch(path, { method, body: body === undefined ? undefined : JSON.stringify(body) }));
}

/** Multipart upload (a bond consent PDF, the owner's Ace files). */
export async function finUpload<T>(path: string, form: FormData): Promise<T> {
  return parse<T>(await apiFetch(path, { method: 'POST', body: form }));
}

/** Fetch a download route and hand the file to the browser. */
export async function finDownload(path: string, filename: string): Promise<void> {
  const res = await apiFetch(path);
  if (!res.ok) await parse(res);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** The API's own words for a refusal (they are written for people). */
export function errorText(e: unknown): string {
  if (e instanceof FinApiError) return e.message || 'Something went wrong. Please try again.';
  if (e instanceof Error && e.message) return e.message;
  return 'Something went wrong. Please try again.';
}

export interface FinanceApi {
  get: <T>(path: string, params?: Record<string, unknown>) => Promise<T>;
  post: <T>(path: string, body?: unknown) => Promise<T>;
  put: <T>(path: string, body?: unknown) => Promise<T>;
  del: <T = void>(path: string, body?: unknown) => Promise<T>;
  upload: <T>(path: string, form: FormData) => Promise<T>;
  download: (path: string, filename: string) => Promise<void>;
  /** Which write is in flight (its key), for disabling buttons. */
  busy: string | null;
  /** Run a write: the failure becomes a toast, the result (or undefined) comes back. */
  run: <T>(key: string, fn: () => Promise<T>, doneMessage?: string) => Promise<T | undefined>;
}

/**
 * The typed client for one API prefix (`/api/payroll`, `/api/entertainers`,
 * `/api/finance`). Paths are relative to it; an absolute `/api/...` path is
 * used as given, so the connector routes are reachable through the same
 * object.
 */
export function useFinanceApi(base = '/api/payroll'): FinanceApi {
  const [busy, setBusy] = useState<string | null>(null);
  const full = useCallback((path: string) => (path.startsWith('/api/') ? path : `${base}${path}`), [base]);
  const run = useCallback(async <T,>(key: string, fn: () => Promise<T>, doneMessage?: string): Promise<T | undefined> => {
    setBusy(key);
    try {
      const out = await fn();
      if (doneMessage) toast(doneMessage, 'ok');
      return out;
    } catch (e) {
      toast(errorText(e), 'error');
      return undefined;
    } finally {
      setBusy(null);
    }
  }, []);
  return useMemo<FinanceApi>(() => ({
    get: (path, params) => finGet(full(path), params),
    post: (path, body) => finSend('POST', full(path), body),
    put: (path, body) => finSend('PUT', full(path), body),
    del: (path, body) => finSend('DELETE', full(path), body),
    upload: (path, form) => finUpload(full(path), form),
    download: (path, filename) => finDownload(full(path), filename),
    busy,
    run,
  }), [full, busy, run]);
}

export interface FinQuery<T> {
  /** The latest data — the last good answer stays while the next one loads. */
  data: T | null;
  error: string | null;
  loading: boolean;
  /** The failed request's HTTP status (403 = no access, 404 = missing or not yours). */
  status: number | null;
  code: string | null;
  retry: () => void;
  /** Replace the data in place after a write, without refetching. */
  setData: (next: T | null | ((prev: T | null) => T | null)) => void;
}

/**
 * GET a finance endpoint and re-GET whenever `path`, `params` or `refresh`
 * change (`path` null = not yet). Loading is derived from which request the
 * last answer belongs to, so nothing is set synchronously in an effect.
 */
export function useFinQuery<T>(path: string | null, params: Record<string, unknown> = {}, refresh = 0): FinQuery<T> {
  const [tick, setTick] = useState(0);
  const request = path ? JSON.stringify([path, params]) : null;
  const key = request ? `${request}#${refresh}#${tick}` : null;
  const [result, setResult] = useState<{ key: string; data: T | null; error: string | null; status: number | null; code: string | null } | null>(null);
  useEffect(() => {
    if (!request || !key) return;
    const [p, q] = JSON.parse(request) as [string, Record<string, unknown>];
    let alive = true;
    finGet<T>(p, q)
      .then((data) => { if (alive) setResult({ key, data, error: null, status: null, code: null }); })
      .catch((e: unknown) => {
        if (!alive) return;
        const error = errorText(e);
        const status = e instanceof FinApiError ? e.status : null;
        const code = e instanceof FinApiError ? e.code : null;
        setResult((prev) => ({ key, data: prev?.data ?? null, error, status, code }));
      });
    return () => { alive = false; };
  }, [request, key]);
  const retry = useCallback(() => setTick((t) => t + 1), []);
  const setData = useCallback((next: T | null | ((prev: T | null) => T | null)) => {
    setResult((prev) => {
      const value = typeof next === 'function' ? (next as (p: T | null) => T | null)(prev?.data ?? null) : next;
      return { key: prev?.key ?? key ?? '', data: value, error: null, status: null, code: null };
    });
  }, [key]);
  const settled = !!key && result?.key === key;
  return {
    data: result?.data ?? null,
    error: settled ? result?.error ?? null : null,
    loading: !!key && !settled,
    status: settled ? result?.status ?? null : null,
    code: settled ? result?.code ?? null : null,
    retry,
    setData,
  };
}
