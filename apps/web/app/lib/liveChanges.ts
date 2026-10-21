/**
 * What Norm just changed, told to whatever is showing it.
 *
 * Asked from an App's page to change the App, Norm saved a new version and the
 * page kept showing the old one: Rich had to leave, refresh the browser and
 * open the App again after every change (21 Oct 2026). A dashboard Norm
 * changes had the same gap. When a chat turn (or an approval) comes back, the
 * page diffs the conversation's tool calls with what it had before and
 * announces each change that has just run; the App runner and the dashboard
 * view listen and reload themselves.
 */

import type { ToolCallRecord } from '../types';

export const APP_SAVED = 'norm:app-saved';
export const DASHBOARD_CHANGED = 'norm:dashboard-changed';

export interface AppSaved { slug: string }
export interface DashboardChanged { dashboardId: string | null; agent: string | null }

export type Change =
  | { kind: 'app'; detail: AppSaved }
  | { kind: 'dashboard'; detail: DashboardChanged };

/** Each call's status before the turn, by id. */
export function statusesOf(calls: ToolCallRecord[] | null | undefined): Map<string, string> {
  return new Map((calls || []).map(c => [c.id, c.status]));
}

const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);

/** The changes that ran in this turn: executed now, not executed before. */
export function changesIn(
  calls: ToolCallRecord[] | null | undefined,
  before: Map<string, string>,
): Change[] {
  const out: Change[] = [];
  for (const c of calls || []) {
    if (c.status !== 'executed' || before.get(c.id) === 'executed') continue;
    const input = c.input_params || {};
    const result = c.result_payload || {};
    if (c.connector_name === 'norm' && c.action === 'save_app') {
      const slug = str(result.slug) || str(input.slug);
      if (slug) out.push({ kind: 'app', detail: { slug } });
    } else if (c.connector_name === 'norm_reports' && c.action === 'manage_dashboard') {
      out.push({
        kind: 'dashboard',
        detail: {
          dashboardId: str(result.dashboard_id) || str(input.dashboard_id),
          agent: str(result.agent),
        },
      });
    }
  }
  return out;
}

// The App's own page (/apps/<slug>) is often open in another tab while the
// person talks to Norm in this one, so a change is told to every tab.
const CHANNEL = 'norm-live-changes';
// A channel also reaches other listeners in the same tab, which have the
// window event already: each tab ignores what it sent itself.
const TAB = Math.random().toString(36).slice(2);

function channel(): BroadcastChannel | null {
  if (typeof window === 'undefined' || typeof BroadcastChannel === 'undefined') return null;
  try {
    return new BroadcastChannel(CHANNEL);
  } catch {
    return null;
  }
}

/** Tell this tab and the others about each change (an App runner or a
 * dashboard showing it reloads). */
export function announceChanges(
  calls: ToolCallRecord[] | null | undefined,
  before: Map<string, string>,
): void {
  if (typeof window === 'undefined') return;
  const changes = changesIn(calls, before);
  if (!changes.length) return;
  const others = channel();
  for (const change of changes) {
    const name = change.kind === 'app' ? APP_SAVED : DASHBOARD_CHANGED;
    window.dispatchEvent(new CustomEvent(name, { detail: change.detail }));
    others?.postMessage({ from: TAB, change });
  }
  others?.close();
}

/** A person saved an app by hand (Edit mode): other tabs showing it update. */
export function announceAppSaved(slug: string): void {
  if (typeof window === 'undefined') return;
  const others = channel();
  others?.postMessage({ from: TAB, change: { kind: 'app', detail: { slug } } });
  others?.close();
}

/** Listen for one kind of change, from this tab or another. Returns the
 * unsubscribe. */
export function onChange(kind: 'app', handler: (d: AppSaved) => void): () => void;
export function onChange(kind: 'dashboard', handler: (d: DashboardChanged) => void): () => void;
export function onChange(kind: Change['kind'], handler: (d: never) => void): () => void {
  if (typeof window === 'undefined') return () => {};
  const name = kind === 'app' ? APP_SAVED : DASHBOARD_CHANGED;
  const here = (ev: Event) => handler((ev as CustomEvent).detail as never);
  window.addEventListener(name, here);
  const others = channel();
  if (others) {
    others.onmessage = (ev: MessageEvent<{ from: string; change: Change }>) => {
      const { from, change } = ev.data || ({} as { from?: string; change?: Change });
      if (from !== TAB && change?.kind === kind) handler(change.detail as never);
    };
  }
  return () => {
    window.removeEventListener(name, here);
    others?.close();
  };
}
