const API = '';

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('norm_token');
}

export function setToken(token: string): void {
  localStorage.setItem('norm_token', token);
}

export function clearToken(): void {
  localStorage.removeItem('norm_token');
  localStorage.removeItem('norm_user');
}

/** A 401 means the session is gone — go to the login page.
 *
 * REDIRECT, never reload: reloading remounts the page, whose boot fetches
 * (e.g. the pinned-apps nav's /api/apps) 401 again and reload forever — an
 * incognito visit could never even reach the login form (16 Aug 2026).
 * Already on /login (or during SSR) it does nothing, so the login page's own
 * requests can never bounce it. */
function redirectToLogin(): void {
  clearToken();
  if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
    window.location.href = '/login';
  }
}

export function getStoredUser(): { id: string; email: string; full_name: string; role: string; permissions?: string[]; org_role?: { name: string; display_name: string } | null } | null {
  if (typeof window === 'undefined') return null;
  const raw = localStorage.getItem('norm_user');
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function setStoredUser(user: { id: string; email: string; full_name: string; role: string; permissions?: string[]; org_role?: { name: string; display_name: string } | null }): void {
  localStorage.setItem('norm_user', JSON.stringify(user));
}

export async function apiStream(
  url: string,
  body: Record<string, unknown>,
  onEvent: (event: { type: string; text?: string; message?: string; data?: unknown; domain?: string; thread_id?: string; title?: string; agent_label?: string; used?: number; quota?: number }) => void,
): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${API}${url}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });

  if (res.status === 401) {
    redirectToLogin();
    return;
  }

  if (!res.ok || !res.body) {
    const text = await res.text();
    onEvent({ type: 'error', message: `API error (${res.status}): ${text}` });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  let receivedTerminal = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Parse SSE lines
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    // Collect all events from this chunk, then process them with
    // async breaks so React can render between each one.
    const events: unknown[] = [];
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          events.push(JSON.parse(line.slice(6)));
        } catch (e) {
          console.warn('Failed to parse SSE event:', line.slice(0, 200), e);
        }
      }
    }

    for (const evt of events) {
      const event = evt as { type: string; text?: string; message?: string; data?: unknown; domain?: string; thread_id?: string; title?: string; agent_label?: string };
      onEvent(event);
      if (event.type === 'complete' || event.type === 'error') {
        receivedTerminal = true;
        return;
      }
      // Yield to the browser so React can flush the state update and paint
      await new Promise(r => setTimeout(r, 0));
    }
  }

  // Stream ended without a complete/error event — the socket was cut (e.g.
  // Cloud Run's request timeout) rather than the turn actually failing. The
  // backend worker is never cancelled: it keeps running and commits the answer
  // moments later. Signal that distinctly (not as a hard error) so the caller
  // can poll the thread for the result instead of showing a false failure.
  if (!receivedTerminal) {
    onEvent({ type: 'stream_dropped', message: 'Connection lost — the response may still be processing.' });
  }
}

export async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init?.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (!headers.has('Content-Type') && init?.body && typeof init.body === 'string') {
    headers.set('Content-Type', 'application/json');
  }

  const res = await fetch(`${API}${url}`, { ...init, headers });

  if (res.status === 401) {
    redirectToLogin();
  }

  // A connector whose authorization has died surfaces the same way from every
  // backend path (a data fetch, a working-document load, a component-api call):
  // the error carries "Reconnect <connector> in Settings → Connectors". Catch it
  // once, here, and fire an app-wide event so the reconnect card can pop up
  // wherever the user hit it — no per-page wiring, and no persistent banner.
  if (!res.ok && res.status >= 400 && typeof window !== 'undefined') {
    detectConnectorAuthFailure(res.clone());
  }

  return res;
}

// Every way the backend says "this connector isn't usable" — a dead token, no
// token, or no credentials at all — across all connectors and endpoints. The
// connector name is the capture group. This is the one place that has to know
// the shapes, so a page never has to.
const CONNECTOR_FAILURE_PATTERNS: RegExp[] = [
  /reconnect\s+([a-z0-9_]+)\s+in\s+settings/i, // dead/expired OAuth token
  /no credentials(?:\s+configured)?\s+for\s+([a-z0-9_]+)/i, // never set up
  /no\s+([a-z0-9_]+)\s+access token/i, // token missing
  /([a-z0-9_]+)\s+authorization failed/i, // auth rejected
];

/** Fire `norm:connector-auth` with the connector name if a response is a
 *  connector-connection failure. Best-effort; never throws into the caller. */
async function detectConnectorAuthFailure(res: Response): Promise<void> {
  try {
    const text = await res.text();
    let connector: string | null = null;
    // Structured form first: {detail:{error:'connector_auth', connector_name}}.
    try {
      const detail = JSON.parse(text)?.detail;
      if (detail && typeof detail === 'object' && detail.error === 'connector_auth') {
        connector = detail.connector_name || null;
      }
    } catch { /* not JSON — fall through to message matching */ }
    if (!connector) {
      for (const re of CONNECTOR_FAILURE_PATTERNS) {
        const m = re.exec(text);
        if (m) { connector = m[1]; break; }
      }
    }
    if (connector) {
      window.dispatchEvent(
        new CustomEvent('norm:connector-auth', { detail: { connector } }),
      );
    }
  } catch { /* best-effort */ }
}

/**
 * Call a component API endpoint directly (bypasses the LLM tool system).
 * Components use this for data loading and write operations.
 */
export async function callComponentApi(
  componentKey: string,
  actionName: string,
  params: Record<string, unknown> | unknown[] = {},
  venueId?: string,
): Promise<{ data: unknown; status_code: number; error?: boolean }> {
  const res = await apiFetch(`/api/component-api/${componentKey}/${actionName}`, {
    method: 'POST',
    body: JSON.stringify({ venue_id: venueId, params }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
    throw new Error(body.detail || `Component API error: ${res.status}`);
  }
  return res.json();
}
