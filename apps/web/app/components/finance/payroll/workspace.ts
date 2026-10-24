/**
 * The Payroll page's pure rules: its tabs and what each needs, which entity
 * a tab shows for the address bar's `venue`, the week a `week` param means,
 * and where a reconciliation blocker sends people. The page component
 * (PayrollPage.tsx) wires these to the URL; nothing here touches React.
 */
import { isMonday, mondayOf } from '../shared/weeks';

export type PayrollTab =
  | 'hours'
  | 'checks'
  | 'leave'
  | 'gratuities'
  | 'entertainers'
  | 'intercompany'
  | 'reconciliation'
  | 'ace-files'
  | 'trial-run'
  | 'settings';

/** Every tab id the URL may carry (`?page=payroll&tab=…`). Settings sits
 *  behind the gear, not in the strip. */
export const PAYROLL_TAB_IDS: readonly PayrollTab[] = [
  'hours', 'checks', 'leave', 'gratuities', 'entertainers', 'intercompany', 'reconciliation', 'ace-files', 'trial-run', 'settings',
];

export const TAB_LABELS: Record<PayrollTab, string> = {
  hours: 'Hours',
  checks: 'Checks',
  leave: 'Leave',
  gratuities: 'Gratuities',
  entertainers: 'Entertainers',
  intercompany: 'Intercompany',
  reconciliation: 'Reconciliation',
  'ace-files': 'Ace files',
  'trial-run': 'Trial run',
  settings: 'Payroll settings',
};

/** Tabs that offer "All entities" and land on it when no entity is given. */
export const GROUP_TABS: ReadonlySet<PayrollTab> = new Set<PayrollTab>(['intercompany', 'reconciliation']);

/** Tabs built from the week's pay-run document (one fetch, shared). */
export const DOC_TABS: ReadonlySet<PayrollTab> = new Set<PayrollTab>(['hours', 'leave', 'gratuities', 'entertainers']);

/** Tabs with no entity or week of their own: the pickers hide there. */
export const NO_WEEK_TABS: ReadonlySet<PayrollTab> = new Set<PayrollTab>(['trial-run', 'settings']);

/** The tabs this organisation can open: Trial run only while payroll is in its trial run (`shadow`). */
export function allowedTabs(mode: string | null | undefined): PayrollTab[] {
  return PAYROLL_TAB_IDS.filter((t) => t !== 'trial-run' || mode === 'shadow');
}

/** The tab strip: every allowed tab except Settings (the gear opens it). */
export function stripTabs(mode: string | null | undefined): { id: PayrollTab; label: string }[] {
  return allowedTabs(mode).filter((t) => t !== 'settings').map((id) => ({ id, label: TAB_LABELS[id] }));
}

export type SettingsView = 'entities' | 'leave' | 'rules' | 'xero';

export const SETTINGS_VIEWS: { id: SettingsView; label: string }[] = [
  { id: 'entities', label: 'Entities' },
  { id: 'leave', label: 'Leave types' },
  { id: 'rules', label: 'Rules' },
  { id: 'xero', label: 'Xero mapping' },
];

/** `view=` on the settings tab → its section (Entities when unknown). */
export function settingsView(view: string | null | undefined): SettingsView {
  return SETTINGS_VIEWS.some((v) => v.id === view) ? (view as SettingsView) : 'entities';
}

/** Settings sections that need one entity (and so show the entity picker). */
export const ENTITY_SETTINGS: ReadonlySet<SettingsView> = new Set<SettingsView>(['leave', 'xero']);

/** Whether the entity picker shows on this tab (and settings section). */
export function showsEntityPicker(tab: PayrollTab, view?: SettingsView): boolean {
  if (tab === 'trial-run') return false;
  if (tab === 'settings') return !!view && ENTITY_SETTINGS.has(view);
  return true;
}

/**
 * The entity a tab shows. Intercompany and Reconciliation land on All
 * entities when the address bar names no entity (or names `all`, or one
 * the person can't see). Every other tab needs one entity: the one named,
 * else the first fallback the person can see (the last single entity
 * chosen, the app's page venue, the remembered venue), else the first.
 */
export function resolvePayrollVenue(opts: {
  raw: string | null | undefined;
  tab: PayrollTab;
  entityIds: readonly string[];
  fallbacks?: readonly (string | null | undefined)[];
}): { venueId: string | null; allVenues: boolean } {
  const { raw, tab, entityIds, fallbacks = [] } = opts;
  const known = (id: string | null | undefined): id is string => !!id && entityIds.includes(id);
  if (GROUP_TABS.has(tab)) {
    if (known(raw)) return { venueId: raw, allVenues: false };
    return { venueId: null, allVenues: true };
  }
  const pick = [raw !== 'all' ? raw : null, ...fallbacks].find(known);
  return { venueId: pick ?? entityIds[0] ?? null, allVenues: false };
}

/** The `venue` param to carry when switching to `target`: the entity chosen
 *  stays chosen; All entities stays only where it exists. */
export function venueForTab(target: PayrollTab, current: { venueId: string | null; allVenues: boolean }): string | undefined {
  if (current.allVenues) return GROUP_TABS.has(target) ? 'all' : undefined;
  return current.venueId ?? undefined;
}

/** A `week=` param → its Monday, or null when absent or not a date. */
export function weekFromParam(raw: string | null | undefined): string | null {
  if (!raw || !/^\d{4}-\d{2}-\d{2}$/.test(raw)) return null;
  const d = new Date(`${raw}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  const monday = mondayOf(raw);
  return isMonday(monday) ? monday : null;
}

/** Where a reconciliation blocker's "Open" goes. */
export type BlockerTarget =
  | { page: 'payroll'; tab: PayrollTab; params?: Record<string, string> }
  | { page: 'entertainers'; tab: 'payments' };

/**
 * A reconciliation blocker (the server's words, services/payroll/
 * reconciliation.py) → the place that fixes it. Mapping is tested before
 * leave: "Xero mapping missing: leave_entitlements" is a Xero mapping
 * fix, not a leave decision. Unpaid entertainer performances go to
 * Entertainers → Payments for people who can mark them paid, else to this
 * page's Entertainers tab. Blockers fixed on the Reconciliation tab itself
 * (ACE inputs, an adjustment's account code) have no link.
 */
export function blockerTarget(text: string, opts: { canPay: boolean }): BlockerTarget | null {
  if (/mapping|account/i.test(text)) return { page: 'payroll', tab: 'settings', params: { view: 'xero' } };
  if (/entertainer/i.test(text)) {
    if (/not marked paid/i.test(text) && opts.canPay) return { page: 'entertainers', tab: 'payments' };
    return { page: 'payroll', tab: 'entertainers' };
  }
  if (/leave|payout/i.test(text)) return { page: 'payroll', tab: 'leave' };
  if (/gratuit/i.test(text)) return { page: 'payroll', tab: 'gratuities' };
  if (/snapshot/i.test(text)) return { page: 'payroll', tab: 'hours' };
  return null;
}

/** The leave key both the pay-run snapshot and Loaded's live list use. */
export function leaveKey(r: { leave_request_id: string; staff_member_id: string }): string {
  return `${r.leave_request_id}:${r.staff_member_id}`;
}

/**
 * The snapshot's leave rows joined to Loaded's live requests on
 * `leaveKey`: `byKey` gives a requested row its snapshot row (for the pay
 * code and Ace style), `unmatched` lists snapshot rows Loaded no longer
 * shows, so nothing in the snapshot vanishes from the tab.
 */
export function joinLeave<S extends { leave_request_id: string; staff_member_id: string }>(
  snapshotRows: readonly S[],
  requested: readonly { leave_request_id: string; staff_member_id: string }[],
): { byKey: Map<string, S>; unmatched: S[] } {
  const byKey = new Map<string, S>();
  for (const s of snapshotRows) byKey.set(leaveKey(s), s);
  const live = new Set(requested.map(leaveKey));
  return { byKey, unmatched: snapshotRows.filter((s) => !live.has(leaveKey(s))) };
}

/** The Ace style a snapshot leave row goes out as, in words, and whether it is a problem. */
export function aceStyleText(
  r: { ace_style: string | null; ace_style_from?: 'loaded' | 'override' | null; unpaid?: boolean } | null | undefined,
  setupError?: string | null,
): { text: string; error: boolean } | null {
  if (!r) return null;
  if (r.unpaid) return { text: 'Unpaid — left out', error: false };
  if (r.ace_style) return { text: `#${r.ace_style}${r.ace_style_from === 'override' ? ' (set in Norm)' : ''}`, error: false };
  return { text: setupError ? 'Unknown' : 'No style', error: true };
}
