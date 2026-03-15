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

export function getStoredUser(): { id: string; email: string; full_name: string; role: string } | null {
  if (typeof window === 'undefined') return null;
  const raw = localStorage.getItem('norm_user');
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function setStoredUser(user: { id: string; email: string; full_name: string; role: string }): void {
  localStorage.setItem('norm_user', JSON.stringify(user));
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
