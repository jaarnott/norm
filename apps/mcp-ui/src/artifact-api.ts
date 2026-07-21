/**
 * Artifact implementation of apps/web/app/lib/api for the Claude Artifact bundle.
 *
 * Sibling of sandbox-api.ts, and the same trick: the build swaps this in for
 * `lib/api` (see vite.artifact.config.ts) so the SAME RosterEditor the web app
 * mounts runs unmodified inside a claude.ai Artifact.
 *
 * What differs is the direction of the connection. In the MCP App the host
 * hands the iframe a tool-call channel back into the Norm session that opened
 * it. An Artifact has no such session: it is a page claude.ai hosts, and it
 * reaches Norm OUTWARD through `window.claude.mcp`, with the viewer's own
 * connector credentials. So there is no working document to address and no
 * component-API bridge to page through — only the tools the published manifest
 * declares, which artifact-main.tsx calls directly.
 *
 * Everything here therefore answers 404. That is not a stub: RosterEditor
 * already treats reference data as optional (`props.embedded` suppresses the
 * venue fetch, and the staff roll falls back to whoever is on the roster), so
 * a 404 lands it in exactly the read-only state this build wants. When the
 * roster write path is opened to artifacts, this file — not the component — is
 * where the tool calls go.
 */

export function getToken(): string | null {
  return null;
}
export function setToken(_token: string): void {}
export function clearToken(): void {}

export function getStoredUser(): {
  id: string;
  email: string;
  full_name: string;
  role: string;
  permissions?: string[];
  org_role?: { name: string; display_name: string } | null;
} | null {
  // No Norm session here — the viewer is authenticated to their connector, not
  // to Norm's web app. Components read this only for admin-gated extras.
  return null;
}
export function setStoredUser(_user: unknown): void {}

export async function apiStream(): Promise<never> {
  throw new Error('apiStream is not available in a Claude Artifact');
}

/** A minimal Response-shaped object — components use ok/status/json() only. */
function jsonResponse(ok: boolean, data: unknown, status: number): Response {
  return {
    ok,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
  } as unknown as Response;
}

export async function apiFetch(_url: string, _init?: RequestInit): Promise<Response> {
  return jsonResponse(false, { detail: 'Not available in this surface' }, 404);
}

export async function callComponentApi(
  _componentKey: string,
  _actionName: string,
  _params: Record<string, unknown> | unknown[] = {},
  _venueId?: string,
): Promise<{ data: unknown; status_code: number; error?: boolean }> {
  // Reference data (leave, unavailability, the staff roll) is unreachable from
  // an artifact: those are component-API actions, not manifest tools. Answer
  // the way an empty result would rather than throwing — the warnings layer
  // degrades to "two shifts overlap" instead of "Sam is on leave", which is
  // the honest reduction, not a crash.
  return { data: [], status_code: 404, error: true };
}
