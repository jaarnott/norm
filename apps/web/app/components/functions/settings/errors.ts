/**
 * Errors on the Settings page. Functions services write their messages for
 * people (F3-api.md §0), so they are shown as they come; this only adds the
 * cases the API can't phrase for us (offline, an upload's raw XHR response).
 */
import { FnApiError } from '../shared/api';

/** Shown beside write controls while the organisation is in shadow mode. */
export const READ_ONLY_HINT = 'Read-only while Orbit is live';

/** The sentence to show for a failed call. */
export function errorText(e: unknown, fallback = 'Something went wrong — please try again.'): string {
  if (e instanceof FnApiError) return e.message || fallback;
  if (e instanceof TypeError) return 'Norm couldn’t be reached — check your connection and try again.';
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

/** True when the API refused because someone else saved first (a stale
 *  `version`) — the page offers a reload rather than a retry. */
export function isStale(e: unknown): boolean {
  return e instanceof FnApiError && e.status === 409 && e.code === 'conflict' && /reload/i.test(e.message);
}

/**
 * An error from a raw response body — the same reading as shared/api's
 * `parse`, for uploads sent with XMLHttpRequest (fetch can't report upload
 * progress). `{detail: {code, message}}` from a service, `{detail: "…"}`
 * from FastAPI/HTTPException, `{detail: [{msg}]}` from body validation.
 */
export function errorFromBody(status: number, body: unknown, statusText = ''): FnApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  let code: string | null = null;
  let message = statusText || `Request failed (${status})`;
  if (status === 413) message = 'That file is too big to upload.';
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
  return new FnApiError(status, code, message, detail ?? body);
}
