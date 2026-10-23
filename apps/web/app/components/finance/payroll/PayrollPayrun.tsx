'use client';

/**
 * Loaded Payroll — Payrun. One entity (a venue that runs its own pay run),
 * one pay week: the snapshot state and the close, then everything the
 * entity pays — Staff (with the Security chip and the "shifts at this
 * venue" view that replaces the old Timeclock screen), Leave, Gratuities,
 * Entertainers, the Hygiene lists, and the Ace Exports with the fallback
 * pack. Every figure comes from the stored snapshot through
 * `/api/payroll/weeks/{id}/payrun`; nothing is recomputed here.
 *
 * Deep links: `?page=payroll-payrun&venue=<id>&week=<monday>&view=staff|
 * timeclock|security|salaried|leave|gratuities|entertainers|hygiene|exports`.
 */
import { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import { readPageParam } from '../../functions/shared/meta';
import DataTable, { type Column } from '../shared/DataTable';
import Money, { useMoney } from '../shared/Money';
import NoAccess from '../shared/NoAccess';
import PageShell, { Note, Section, Stack } from '../shared/PageShell';
import WeekPicker from '../shared/WeekPicker';
import { dateLabel, formatHours, personName, plural } from '../shared/format';
import { useFinQuery, useFinanceApi } from '../shared/useFinanceApi';
import { weLabel, weekRangeLabel } from '../shared/weeks';
import ExportsTab from './payrun/ExportsTab';
import GratuitiesTab from './payrun/GratuitiesTab';
import HygieneTab from './payrun/HygieneTab';
import SnapshotPanel from './payrun/SnapshotPanel';
import StaffTab from './payrun/StaffTab';
import { PAYRUN_TABS, chipFrom, statusLabel, tabFrom, type PayrunTab } from './payrun/logic';
import type { EntertainerLine, LeaveRow, PayrunDoc } from './types';
import { openPayrollPage, usePayrollPage } from './usePayrollMeta';
import { useWeek } from './useWeek';

/** The states the meta can leave a payroll page in, shared by every page. */
export function MetaGate({ status, error, loading, children }: { status: number | null; error: string | null; loading: boolean; children: React.ReactNode }) {
  if (status === 403) return <NoAccess />;
  if (status === 404) return <PageState kind="empty" title="Loaded Payroll is not switched on for this organisation." detail="An owner switches it on in Settings once the pay-week shadow period starts." />;
  if (error) return <PageState kind="error" title="Could not load Loaded Payroll." detail={error} />;
  if (loading) return <PageState kind="loading" title="Loading…" />;
  return <>{children}</>;
}

function LeaveTab({ doc, venueId, weekStart }: { doc: PayrunDoc; venueId: string; weekStart: string }) {
  const money = useMoney();
  const rows = doc.leave.rows;
  const columns: Column<LeaveRow>[] = [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'pay_code', label: 'Pay code', render: (r) => r.pay_code || <Badge tone="error">Missing</Badge> },
    { key: 'leave_type_name', label: 'Leave type' },
    { key: 'dates', label: 'Days', sortable: false, value: (r) => r.dates.join(', '), render: (r) => r.dates.map((d) => dateLabel(d, { year: false })).join(', ') },
    { key: 'hours', label: 'Hours', align: 'right', value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'cost_cents', label: 'Cost (Loaded)', align: 'right', render: (r) => <Money cents={r.cost_cents} />, csv: (r) => r.cost_cents / 100 },
    { key: 'ace_style', label: 'Ace style', value: (r) => r.ace_style ?? '', render: (r) => (r.unpaid ? <span style={{ color: 'var(--text-soft)' }}>Unpaid — left out</span> : r.ace_style ? `#${r.ace_style}${r.ace_style_from === 'override' ? ' (set in Norm)' : ''}` : <Badge tone="error">{doc.leave.setup_error ? 'Unknown' : 'No style'}</Badge>) },
  ];
  const total = rows.reduce((s, r) => s + (r.cost_cents || 0), 0);
  const hours = rows.reduce((s, r) => s + Number(r.hours || 0), 0);
  return (
    <Stack>
      <Note tone="plain">
        What Loaded holds as leave in the snapshot, and the Ace style each leave type goes out as (Loaded&rsquo;s own types from Loaded; a company&rsquo;s own type from Payroll settings → Leave types). What is actually paid is decided on the Leave page — annual, sick and other leave each have their own rules.{' '}
        <Button size="xs" variant="link" onClick={() => openPayrollPage('payroll-leave', { venue: venueId, week: weekStart })}>Open the Leave page</Button>
      </Note>
      {doc.leave.setup_error && <Note tone="warn">Could not read the leave types from Loaded: {doc.leave.setup_error}</Note>}
      <DataTable columns={columns} rows={rows} rowKey={(r) => `${r.leave_request_id}:${r.staff_member_id}`} csvName={`Leave ${doc.entity.venue_name} ${weLabel(weekStart)}`} emptyTitle="No leave in this snapshot." footer={[plural(rows.length, 'request'), null, null, null, formatHours(hours), money(total), null]} rowTone={(r) => (r.ace_style || r.unpaid ? undefined : 'error')} />
    </Stack>
  );
}

function EntertainersTab({ doc, weekStart }: { doc: PayrunDoc; weekStart: string }) {
  const money = useMoney();
  const rows = doc.entertainers.lines;
  const columns: Column<EntertainerLine>[] = [
    { key: 'performer', label: 'Performer', render: (r) => r.performer || r.performance_id, nowrap: true },
    { key: 'pay_code', label: 'Payroll code', render: (r) => r.pay_code || <Badge tone="error">Missing</Badge> },
    { key: 'hours', label: 'Hours', align: 'right', value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'rate_cents', label: 'Rate', align: 'right', render: (r) => <Money cents={r.rate_cents} />, csv: (r) => r.rate_cents / 100 },
    { key: 'amount_cents', label: 'Amount', align: 'right', render: (r) => <Money cents={r.amount_cents} />, csv: (r) => r.amount_cents / 100 },
    { key: 'payment_status', label: 'State', render: (r) => (r.payment_status === 'paid' ? <Badge tone="ok">Paid this week</Badge> : r.payment_status ? <Badge tone="warn">{r.payment_status}</Badge> : null) },
  ];
  return (
    <Stack>
      {!doc.entertainers.available && <Note tone="warn">The Entertainers module is not available yet, so no entertainer lines are in this pay run.</Note>}
      <Note tone="plain">Performers paid through payroll: approved performances not yet paid, and those marked paid in this week. Marking paid happens on Entertainers → Payments.</Note>
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.performance_id} csvName={`Entertainers via payroll ${doc.entity.venue_name} ${weLabel(weekStart)}`} emptyTitle="No entertainer lines this week." footer={[plural(rows.length, 'performance'), null, null, null, money(rows.reduce((s, r) => s + r.amount_cents, 0)), null]} />
    </Stack>
  );
}

export default function PayrollPayrun({ props }: DisplayBlockProps) {
  const page = usePayrollPage(props);
  const api = useFinanceApi();
  const [link] = useState(() => readPageParam('view'));
  const [tab, setTab] = useState<PayrunTab>(() => tabFrom(link));
  const [which, setWhich] = useState<'current' | 'close'>('current');
  const [refresh, setRefresh] = useState(0);
  const weekState = useWeek(page.venueId, page.weekStart, refresh);
  const week = weekState.week;
  const doc = useFinQuery<PayrunDoc>(week ? `/api/payroll/weeks/${week.id}/payrun` : null, { snapshot: which }, refresh);
  const canWrite = !!page.meta?.can_write;
  const shadow = page.meta?.mode === 'shadow';
  const bump = () => setRefresh((n) => n + 1);

  const status = week ? statusLabel(week.status) : null;
  const toolbar = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      <WeekPicker value={page.weekStart} onChange={page.setWeekStart} venue={page.entity} thisWeek={page.meta?.this_week_start} />
      <Tabs items={PAYRUN_TABS} value={tab} onChange={(id) => setTab(id as PayrunTab)} label="Payrun sections" />
    </div>
  );

  let body: React.ReactNode;
  if (!page.entity) {
    body = <PageState kind="empty" title="No payroll entities." detail="None of the venues you can see runs a pay run yet. Switch one on in Payroll settings." />;
  } else if (weekState.error && !week) {
    body = <PageState kind="error" title="Could not open this pay week." detail={weekState.error} action={<Button size="sm" onClick={weekState.reload}>Try again</Button>} />;
  } else if (!week) {
    body = <PageState kind="loading" title={`Opening ${weLabel(page.weekStart, true)}…`} />;
  } else {
    const tz = week.window?.timezone || page.entity.timezone || 'Pacific/Auckland';
    const snapshotToggle = week.close_snapshot && week.current_snapshot && week.close_snapshot.id !== week.current_snapshot.id ? (
      <div role="tablist" aria-label="Which snapshot" style={{ display: 'inline-flex', gap: 4 }}>
        {(['current', 'close'] as const).map((w) => (
          <Button key={w} size="xs" variant={which === w ? 'primary' : 'quiet'} onClick={() => setWhich(w)} role="tab" aria-selected={which === w}>
            {w === 'current' ? 'Current snapshot' : 'Close snapshot'}
          </Button>
        ))}
      </div>
    ) : null;

    let tabBody: React.ReactNode;
    if (tab === 'hygiene') {
      tabBody = <HygieneTab week={week} api={api} canWrite={canWrite} refresh={refresh} />;
    } else if (tab === 'exports') {
      tabBody = <ExportsTab week={week} api={api} canWrite={canWrite} refresh={refresh} onChanged={bump} />;
    } else if ((doc.error && !doc.data && doc.code === 'invalid' && /snapshot/i.test(doc.error)) || (!week.current_snapshot && !doc.data)) {
      // The route answers 400 "No current snapshot for this week yet."
      tabBody = <PageState kind="empty" title={which === 'close' ? 'No close snapshot for this week yet.' : 'No snapshot for this week yet.'} detail={canWrite ? 'Take a snapshot to read this week’s clock-ins from Loaded.' : 'The Monday job takes one 30 minutes after the venue’s day start.'} />;
    } else if (doc.error && !doc.data) {
      tabBody = <PageState kind="error" title="Could not load the pay run." detail={doc.error} action={<Button size="sm" onClick={doc.retry}>Try again</Button>} />;
    } else if (!doc.data) {
      tabBody = <PageState kind="loading" title="Loading the pay run…" />;
    } else if (tab === 'staff') {
      tabBody = <StaffTab doc={doc.data} timeZone={tz} initialChip={chipFrom(link)} initialView={link === 'timeclock' ? 'shifts' : 'people'} weekLabel={weLabel(page.weekStart, true)} />;
    } else if (tab === 'leave') {
      tabBody = <LeaveTab doc={doc.data} venueId={page.entity.venue_id} weekStart={page.weekStart} />;
    } else if (tab === 'gratuities') {
      tabBody = <GratuitiesTab doc={doc.data} api={api} canWrite={canWrite} weekStart={page.weekStart} shadow={shadow} onChanged={bump} />;
    } else {
      tabBody = <EntertainersTab doc={doc.data} weekStart={page.weekStart} />;
    }

    body = (
      <Stack>
        {!page.entity.loaded_company_id && <Note tone="warn">{page.entity.venue_name} has no Loaded connection yet, so no snapshot can be taken. Connect it under Settings → Connections.</Note>}
        {!page.entity.payroll_on && <Note tone="warn">Payroll is switched off for {page.entity.venue_name} in Payroll settings — the Monday jobs skip it.</Note>}
        <SnapshotPanel week={week} api={api} canWrite={canWrite} onWeek={weekState.setWeek} onReload={bump} />
        <Section
          title={PAYRUN_TABS.find((t) => t.id === tab)?.label ?? 'Payrun'}
          meta={doc.data?.snapshot && tab !== 'hygiene' && tab !== 'exports' ? `${doc.data.snapshot.shift_count} shifts in the ${which} snapshot` : undefined}
          actions={(
            <>
              {tab !== 'hygiene' && tab !== 'exports' && snapshotToggle}
              <Button size="xs" variant="quiet" icon={RefreshCw} onClick={bump} aria-label="Refresh">Refresh</Button>
            </>
          )}
        >
          {tabBody}
        </Section>
      </Stack>
    );
  }

  return (
    <PageShell
      isPage={page.isPage}
      title="Payrun"
      meta={page.entity ? `${page.entity.venue_name} · ${weekRangeLabel(page.weekStart)}` : undefined}
      mode={page.meta?.mode}
      status={status ? <Badge tone={status.tone}>{status.label}</Badge> : undefined}
      venues={page.venues}
      venueId={page.venueId}
      onVenueChange={page.setVenue}
      venueTestId="payroll-payrun-venue"
      toolbar={page.meta ? toolbar : undefined}
      testId="payroll-payrun"
    >
      <MetaGate status={page.status} error={page.error} loading={page.loading}>{body}</MetaGate>
    </PageShell>
  );
}
