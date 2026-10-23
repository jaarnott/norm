'use client';

/**
 * Loaded Payroll — Payroll settings. Four sections: the pay entities and
 * their Ace codes; the leave styles each entity signs off; the dated
 * statutory rules this module applies (read-only here — a new rule is a
 * dated row the owner's accountant signs off); and the Xero account
 * mapping for the journal and the intercompany documents.
 *
 * Deep links: `?page=payroll-settings&view=entities|leave|rules|xero&venue=<id>`.
 */
import { useState } from 'react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import { readPageParam } from '../../functions/shared/meta';
import DataTable, { type Column } from '../shared/DataTable';
import PageShell, { Note, Stack } from '../shared/PageShell';
import { dateLabel } from '../shared/format';
import { useFinQuery, useFinanceApi } from '../shared/useFinanceApi';
import { MetaGate } from './PayrollPayrun';
import EntitiesTab from './settings/EntitiesTab';
import LeaveStylesTab from './settings/LeaveStylesTab';
import MappingTab from './settings/MappingTab';
import type { Rule } from './types';
import { usePayrollPage } from './usePayrollMeta';

type TabId = 'entities' | 'leave' | 'rules' | 'xero';
const TABS: { id: TabId; label: string }[] = [
  { id: 'entities', label: 'Entities' },
  { id: 'leave', label: 'Leave styles' },
  { id: 'rules', label: 'Rules' },
  { id: 'xero', label: 'Xero mapping' },
];
const VENUE_TABS = new Set<TabId>(['leave', 'xero']);

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

export default function PayrollSettings({ props }: DisplayBlockProps) {
  const page = usePayrollPage(props);
  const api = useFinanceApi();
  const [tab, setTab] = useState<TabId>(() => { const v = readPageParam('view'); return TABS.some((t) => t.id === v) ? (v as TabId) : 'entities'; });
  const canWrite = !!page.meta?.can_write;
  const entities = page.meta?.entities ?? [];

  let body: React.ReactNode;
  if (tab === 'entities') body = <EntitiesTab api={api} entities={entities} canWrite={canWrite} onChanged={page.reload} />;
  else if (tab === 'rules') body = <RulesTab />;
  else if (!page.entity) body = <PageState kind="empty" title="No entities." />;
  else if (tab === 'leave') body = <LeaveStylesTab key={page.entity.venue_id} api={api} entity={page.entity} canWrite={canWrite} />;
  else body = <MappingTab key={page.entity.venue_id} api={api} entity={page.entity} entities={entities} canWrite={canWrite} />;

  return (
    <PageShell
      isPage={page.isPage}
      title="Payroll settings"
      meta="Entities, Ace codes, leave styles, the rules, and the Xero mapping"
      mode={page.meta?.mode}
      venues={VENUE_TABS.has(tab) ? page.venues : undefined}
      venueId={page.venueId}
      onVenueChange={page.setVenue}
      venueTestId="payroll-settings-venue"
      toolbar={<Tabs items={TABS} value={tab} onChange={(id) => setTab(id as TabId)} label="Payroll settings sections" />}
      testId="payroll-settings"
    >
      <MetaGate status={page.status} error={page.error} loading={page.loading}>{body}</MetaGate>
    </PageShell>
  );
}
