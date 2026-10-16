/**
 * Image uploads with progress. fetch() can't report upload progress, so
 * these go through XMLHttpRequest with the same bearer token apiFetch sends.
 * The API judges the bytes (JPEG / PNG / GIF / WebP, ≤ 10 MB, ≤ 40 MP —
 * F3-api.md §12); the checks here only catch the obvious before a wait.
 */
import { getToken } from '../../../lib/api';
import { FnApiError } from '../shared/api';
import { errorFromBody } from './errors';

export const IMAGE_ACCEPT = 'image/jpeg,image/png,image/gif,image/webp';
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const IMAGE_TYPES = new Set(IMAGE_ACCEPT.split(','));

/** Why this file won't be accepted, or null. Light: the API is the authority. */
export function imageProblem(file: { name: string; type: string; size: number }): string | null {
  if (file.type && !IMAGE_TYPES.has(file.type)) {
    return `${file.name} isn’t a JPEG, PNG, GIF or WebP image.`;
  }
  if (file.size > MAX_IMAGE_BYTES) {
    return `${file.name} is over 10 MB — make it smaller and try again.`;
  }
  return null;
}

/**
 * POST a multipart form to /api/functions{path}, reporting progress (0–1).
 * Resolves with the JSON response; rejects with an FnApiError carrying the
 * API's message.
 */
export function uploadForm<T>(path: string, form: FormData, onProgress?: (fraction: number) => void): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/functions${path}`);
    const token = getToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.total ? e.loaded / e.total : 0);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as T);
      } else if (xhr.status === 401) {
        reject(new FnApiError(401, null, 'Your session has ended — sign in again to upload.', body));
      } else {
        reject(errorFromBody(xhr.status, body, xhr.statusText));
      }
    };
    xhr.onerror = () =>
      reject(new FnApiError(0, null, 'The upload didn’t reach Norm — check your connection and try again.', null));
    xhr.send(form);
  });
}

/** One image file → `fn_files` (`POST /api/functions/files`). */
export function uploadImage<T>(file: File, onProgress?: (fraction: number) => void): Promise<T> {
  const form = new FormData();
  form.append('file', file, file.name);
  return uploadForm<T>('/files', form, onProgress);
}
