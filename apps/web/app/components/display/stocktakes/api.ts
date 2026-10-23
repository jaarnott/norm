/**
 * Running the Stocktakes app's Loaded tools from the page, the way the Bills
 * page runs xero.get_bills: `POST /api/connectors/loadedhub/execute/<action>`.
 *
 * Loaded's stocktake report is slow (about 50 s for a week of a small venue's
 * Food), so results are kept for ten minutes and a request already in flight
 * is shared: React's development double-mount, going back to the list and
 * re-opening a count, or switching tabs never asks Loaded twice.
 */

import { apiFetch } from '../../../lib/api';

export interface ToolResult<T> {
  doc: T | null;
  error: string | null;
}

const TTL_MS = 10 * 60_000;
const cache = new Map<string, { at: number; result: Promise<ToolResult<unknown>> }>();

async function run<T>(action: string, params: Record<string, unknown>): Promise<ToolResult<T>> {
  try {
    const res = await apiFetch(`/api/connectors/loadedhub/execute/${action}`, {
      method: 'POST',
      body: JSON.stringify({ params }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { doc: null, error: String(body?.detail || body?.error || `Loaded didn't answer (${res.status})`) };
    }
    const data = body?.data ?? null;
    if (data && typeof data === 'object' && typeof (data as Record<string, unknown>).error === 'string') {
      return { doc: null, error: (data as Record<string, string>).error };
    }
    if (body?.success === false || !data) {
      return { doc: null, error: String(body?.error || 'Loaded returned nothing') };
    }
    return { doc: data as T, error: null };
  } catch (e) {
    return { doc: null, error: e instanceof Error ? e.message : 'Could not reach Norm' };
  }
}

/** Run a read tool; `fresh` skips the cache (the Refresh button). Failures are
 *  never cached, so trying again really tries again. */
export function runLoadedTool<T>(action: string, params: Record<string, unknown>, fresh = false): Promise<ToolResult<T>> {
  const key = `${action}:${JSON.stringify(params)}`;
  const hit = cache.get(key);
  if (!fresh && hit && Date.now() - hit.at < TTL_MS) return hit.result as Promise<ToolResult<T>>;
  const result = run<T>(action, params);
  cache.set(key, { at: Date.now(), result });
  void result.then((r) => {
    if (r.error && cache.get(key)?.result === result) cache.delete(key);
  });
  return result;
}

// ── Remembered count values ────────────────────────────────────────────────
//
// A count's value only comes from Loaded's stocktake report (the list has no
// value, and the report takes up to a minute), so the list shows a value once
// the count has been opened. A completed count's value doesn't change, so it
// is remembered in this browser per venue — a convenience only: the page works
// the same without it.

const VALUES_KEY = 'norm_stocktake_values';

type ValueStore = Record<string, Record<string, number>>;

function readStore(): ValueStore {
  try {
    const raw = typeof window !== 'undefined' ? window.localStorage.getItem(VALUES_KEY) : null;
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === 'object' ? (parsed as ValueStore) : {};
  } catch {
    return {};
  }
}

export function readValues(venueId: string): Record<string, number> {
  return { ...(readStore()[venueId] || {}) };
}

export function rememberValues(venueId: string, values: Record<string, number>): void {
  try {
    const store = readStore();
    const merged = { ...(store[venueId] || {}), ...values };
    // Keep the newest 300 per venue; ids are UUIDs, so order of insertion is all we have.
    const ids = Object.keys(merged);
    store[venueId] = ids.length > 300 ? Object.fromEntries(ids.slice(-300).map((k) => [k, merged[k]])) : merged;
    window.localStorage.setItem(VALUES_KEY, JSON.stringify(store));
  } catch {
    /* storage unavailable (private window, blocked site data): values just aren't remembered */
  }
}
