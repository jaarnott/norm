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
    clearToken();
    window.location.reload();
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

  // Stream ended without a complete/error event — connection was likely dropped
  if (!receivedTerminal) {
    onEvent({ type: 'error', message: 'Connection lost — the response may still be processing. Retrying…' });
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
    clearToken();
    window.location.reload();
  }

  return res;
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
