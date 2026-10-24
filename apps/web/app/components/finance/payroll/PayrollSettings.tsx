'use client';

/**
 * Payroll — the settings view behind the page's gear. Four sections: the pay entities and
 * their Ace codes; the leave types Loaded holds for each entity and the Ace
 * style each goes out as (from Loaded, or set here for a company's own
 * type); the dated
 * statutory rules this module applies (read-only here — a new rule is a
 * dated row the owner's accountant signs off); and the Xero account
 * mapping for the journal and the intercompany documents. The section names
 * stay as they are: server messages point at them ("Payroll settings →
 * Entities"). The page draws the section tabs, the back link and the entity
 * picker (Leave types and Xero mapping only) in its header; an in-page
 * view rather than a dialog, because the shared dialog closes every open
 * dialog on Escape and these sections open dialogs of their own.
 *
 * Link: `?page=payroll&tab=settings&view=entities|leave|rules|xero&venue=<id>`.
 */
import Badge from '../../ui/Badge';
import PageState from '../../ui/PageState';
import DataTable, { type Column } from '../shared/DataTable';
import PageShell, { Note, Stack } from '../shared/PageShell';
import { dateLabel } from '../shared/format';
import { useFinQuery, useFinanceApi } from '../shared/useFinanceApi';
import EntitiesTab from './settings/EntitiesTab';
import LeaveTypesTab from './settings/LeaveTypesTab';
import MappingTab from './settings/MappingTab';
import type { Entity, PayrollMeta, Rule } from './types';
import type { SettingsView } from './workspace';

const RULE_LABELS: Record<string, string> = {
  sick_eligibility: 'Sick leave — who qualifies',
  sick_day_hours: 'Sick leave — hours in a day',
  post_freeze_watch: 'Orbit freeze watch (cut-over runbook)',
};

function params(p: Record<string, unknown>): string {
  return Object.entries(p).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' · ');
}

function RulesTab() {
  const q = useFinQuery<{ rules: Rule[] }>('/api/payroll/rules');
  const columns: Column<Rule>[] = [
    { key: 'rule', label: 'Rule', render: (r) => <span style={{ fontWeight: 500 }}>{RULE_LABELS[r.rule] ?? r.rule.replace(/_/g, ' ')}</span>, nowrap: true },
    { key: 'effective_from', label: 'Effective from', render: (r) => dateLabel(r.effective_from), nowrap: true },
    { key: 'params', label: 'What it says', sortable: false, value: (r) => params(r.params) },
    { key: 'source', label: 'Source', hideOnPhone: true },
    { key: 'is_platform_default', label: 'Scope', sortable: false, render: (r) => (r.is_platform_default ? <Badge tone="neutral">Platform default</Badge> : <Badge tone="info">This organisation</Badge>) },
  ];
  if (q.error && !q.data) return <PageState kind="error" title="Could not load the rules." detail={q.error} />;
  if (!q.data) return <PageState kind="loading" title="Loading…" />;
  const rows = q.data.rules.filter((r) => r.rule !== 'post_freeze_watch' || !r.is_platform_default);
  return (
    <Stack>
      <Note tone="plain">The statutory rules this module applies, each dated so a change in the law is a new row and a test, not a code change. Public-holiday pay is priced by Loaded; KiwiSaver and ESCT by Ace. An override for this organisation is added by the coordinator with the accountant&rsquo;s sign-off.</Note>
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} csvName="Payroll rules" emptyTitle="No rules." />
    </Stack>
  );
}

export default function PayrollSettings({ meta, entity, view, onEntitiesChanged }: {
  meta: PayrollMeta;
  /** The entity chosen in the page header (Leave types and Xero mapping). */
  entity: Entity | null;
  view: SettingsView;
  /** An entity was edited: reload the page's meta so the header picker sees it. */
  onEntitiesChanged: () => void;
}) {
  const api = useFinanceApi();
  const canWrite = !!meta.can_write;
  const entities = meta.entities ?? [];

  let body: React.ReactNode;
  if (view === 'entities') body = <EntitiesTab api={api} entities={entities} canWrite={canWrite} onChanged={onEntitiesChanged} />;
  else if (view === 'rules') body = <RulesTab />;
  else if (!entity) body = <PageState kind="empty" title="No entities." />;
  else if (view === 'leave') body = <LeaveTypesTab key={entity.venue_id} api={api} entity={entity} canWrite={canWrite} />;
  else body = <MappingTab key={entity.venue_id} api={api} entity={entity} entities={entities} canWrite={canWrite} />;

  return <PageShell isPage embedded title="Payroll settings" testId="payroll-settings">{body}</PageShell>;
}
