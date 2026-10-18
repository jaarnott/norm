// Calls to /api/admin/suggestions: a refusal (422) comes back as the reasons
// a person can read, so every form shows them next to the field it edited.
import { apiFetch } from '../../../lib/api';

export interface Result<T = unknown> {
  ok: boolean;
  data?: T;
  problems?: string[];
}

export async function call<T = unknown>(method: string, path: string, body?: unknown): Promise<Result<T>> {
  try {
    const res = await apiFetch(`/api/admin/suggestions${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => null);
    if (res.ok) return { ok: true, data: data as T };
    const detail = data?.detail;
    const problems = Array.isArray(detail?.problems) ? detail.problems : [typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`];
    return { ok: false, problems };
  } catch {
    return { ok: false, problems: ['Norm couldn’t be reached.'] };
  }
}
