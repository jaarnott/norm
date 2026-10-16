'use client';

/**
 * Staff copies of uploaded images. `GET /api/functions/files/{id}` needs the
 * bearer token, so an `<img src>` can't load it directly: the bytes are
 * fetched once per file and shown from a blob: URL. File ids are content
 * addresses (the bytes never change), so the blob is kept for the session.
 */
import { useEffect, useState } from 'react';
import { apiFetch } from '../../../lib/api';

const cache = new Map<string, Promise<string>>();

export function fileBlobUrl(fileId: string): Promise<string> {
  let p = cache.get(fileId);
  if (!p) {
    p = apiFetch(`/api/functions/files/${encodeURIComponent(fileId)}`).then(async (res) => {
      if (!res.ok) throw new Error(`Image unavailable (${res.status})`);
      return URL.createObjectURL(await res.blob());
    });
    p.catch(() => cache.delete(fileId));
    cache.set(fileId, p);
  }
  return p;
}

/** `{url, failed}` for a file id; url is null until loaded (or when none). */
export function useFileUrl(fileId: string | null | undefined): { url: string | null; failed: boolean } {
  const [state, setState] = useState<{ id: string; url: string | null; failed: boolean } | null>(null);
  useEffect(() => {
    if (!fileId) return;
    let alive = true;
    fileBlobUrl(fileId).then(
      (url) => alive && setState({ id: fileId, url, failed: false }),
      () => alive && setState({ id: fileId, url: null, failed: true }),
    );
    return () => {
      alive = false;
    };
  }, [fileId]);
  if (!fileId || !state || state.id !== fileId) return { url: null, failed: false };
  return { url: state.url, failed: state.failed };
}
