'use client';

/**
 * The Payroll page: one entity, one pay week, every part of the job as a
 * tab — Hours · Checks · Leave · Gratuities · Entertainers · Intercompany ·
 * Reconciliation · Ace files, then Trial run while payroll is in its trial
 * run (the code calls the mode `shadow`), and the settings behind the gear.
 *
 * The page owns everything the tabs share: the meta (loaded once), the one
 * entity and week picker (kept in the address bar, so a refresh, a bookmark
 * or a tab switch keeps them), the week record, the pay-run snapshot the
 * Hours, Leave, Gratuities and Entertainers tabs read (fetched once, only
 * once one of them opens), one refresh counter, one mode banner, one header
 * and one toast host. Tab bodies render `PageShell embedded`.
 *
 * Links: `?page=payroll&tab=<tab>&venue=<id>|all&week=<monday>`, plus
 * `view=` (Hours: timeclock|security|salaried; settings: entities|leave|
 * rules|xero) and `run=` (Trial run). Old page ids (payroll-payrun,
 * payroll-leave, …) are translated by the app shell before this mounts.
 */
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { RefreshCw, Settings } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import BackLink from '../../ui/BackLink';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import { useActiveVenue } from '../../../hooks/useActiveVenue';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import PageShell, { Note, Section, Stack } from '../shared/PageShell';
import WeekPicker from '../shared/WeekPicker';
import { useFinQuery, useFinanceApi } from '../shared/useFinanceApi';
import { usePublishFinanceTab, useWorkspaceTab, WorkspaceTabContext, type ParamUpdate, type WorkspaceTabContextValue } from '../shared/useWorkspaceTab';
import { mondayOf, weLabel, weekRangeLabel } from '../shared/weeks';
import MetaGate from './MetaGate';
import PayrollIntercompany from './PayrollIntercompany';
import PayrollLeave from './PayrollLeave';
import PayrollReconciliation from './PayrollReconciliation';
import PayrollSettings from './PayrollSettings';
import PayrollShadow from './PayrollShadow';
import EntertainersTab from './payrun/EntertainersTab';
import ExportsTab from './payrun/ExportsTab';
import GratuitiesTab from './payrun/GratuitiesTab';
import HygieneTab from './payrun/HygieneTab';
import { DriftNote, DriftSection, SnapshotActions, SnapshotCard } from './payrun/SnapshotPanel';
import StaffTab from './payrun/StaffTab';
import { chipFrom, statusLabel } from './payrun/logic';
import type { PayrollTabProps } from './tabProps';
import type { PayrunDoc } from './types';
import { usePayrollMeta } from './usePayrollMeta';
import { useWeek } from './useWeek';
import {
  allowedTabs, DOC_TABS, GROUP_TABS, NO_WEEK_TABS, PAYROLL_TAB_IDS, resolvePayrollVenue, SETTINGS_VIEWS, settingsView, showsEntityPicker,
  stripTabs, TAB_LABELS, venueForTab, weekFromParam, type PayrollTab,
} from './workspace';

export default function PayrollPage({ props }: DisplayBlockProps) {
  const isPage = !!props?.persistVenue;
  const metaState = usePayrollMeta();
  const meta = metaState.meta;
  const api = useFinanceApi();
  const { isMobile } = useBreakpoint();
  const ws = useWorkspaceTab<PayrollTab>({ pageId: 'payroll', tabs: PAYROLL_TAB_IDS, allowed: allowedTabs(meta?.mode), defaultTab: 'hours', syncUrl: isPage });
  const { tab, setTab, setParams, param } = ws;
  const view = settingsView(param('view'));

  // ── the one entity picker ────────────────────────────────────────────────
  const [shared, setShared] = useActiveVenue();
  const [lastSingle, setLastSingle] = useState<string | null>(null);
  const entities = useMemo(() => meta?.entities ?? [], [meta]);
  const entityIds = useMemo(() => entities.map((e) => e.venue_id), [entities]);
  const choice = resolvePayrollVenue({
    raw: param('venue'),
    tab,
    entityIds,
    fallbacks: [lastSingle, props?.activeVenueId as string | undefined, isPage ? shared : null],
  });
  // Remember the last single entity, so a tab handed "All entities" falls back to it.
  if (choice.venueId && choice.venueId !== lastSingle) setLastSingle(choice.venueId);
  const allVenues = choice.allVenues;
  const entity = entities.find((e) => e.venue_id === choice.venueId) ?? null;

  const setVenue = useCallback((id: string) => {
    setParams({ venue: id });
    if (id !== 'all') {
      setLastSingle(id);
      // "All entities" lives only in the address bar, never in the remembered venue.
      if (isPage) setShared(id);
    }
  }, [setParams, isPage, setShared]);

  // ── the one week picker ──────────────────────────────────────────────────
  const weekStart = weekFromParam(param('week')) ?? meta?.last_week_start ?? mondayOf(new Date().toISOString().slice(0, 10));
  const setWeekStart = useCallback((w: string) => setParams({ week: mondayOf(w) }), [setParams]);

  // ── tabs ─────────────────────────────────────────────────────────────────
  const [returnTab, setReturnTab] = useState<PayrollTab>('hours');
  const switchTab = useCallback((t: PayrollTab, params: ParamUpdate = {}) => {
    if (t === 'settings' && tab !== 'settings') setReturnTab(tab);
    setTab(t, { venue: venueForTab(t, choice), ...params } as Record<string, string | undefined>);
  }, [setTab, tab, choice]);
  const context = useMemo<WorkspaceTabContextValue>(
    () => ({ pageId: 'payroll', openTab: (t, params) => switchTab(t as PayrollTab, params) }),
    [switchTab],
  );

  // ── the week record and the pay-run snapshot ─────────────────────────────
  const [refresh, setRefresh] = useState(0);
  const bump = useCallback(() => setRefresh((n) => n + 1), []);
  const needsWeek = !!entity && !allVenues && !NO_WEEK_TABS.has(tab);
  const weekState = useWeek(needsWeek && entity ? entity.venue_id : null, weekStart, refresh);
  // useWeek keeps the last record while the next loads; only this entity's week counts.
  const week = weekState.week && entity && weekState.week.venue_id === entity.venue_id && weekState.week.week_start === weekStart ? weekState.week : null;

  const [which, setWhich] = useState<'current' | 'close'>('current');
  const toggleAvailable = !!(week?.close_snapshot && week.current_snapshot && week.close_snapshot.id !== week.current_snapshot.id);
  const effectiveWhich = toggleAvailable ? which : 'current';
  // Fetched only once a tab that reads it opens, then kept for the week, so
  // switching between those tabs never refetches.
  const [docFor, setDocFor] = useState<string | null>(null);
  if (week && DOC_TABS.has(tab) && docFor !== week.id) setDocFor(week.id);
  const doc = useFinQuery<PayrunDoc>(week && docFor === week.id ? `/api/payroll/weeks/${week.id}/payrun` : null, { snapshot: effectiveWhich }, refresh);
  const docData = doc.data && week && doc.data.snapshot?.week_id === week.id ? doc.data : null;

  const canWrite = !!meta?.can_write;
  const shadow = meta?.mode === 'shadow';

  // ── Norm knows which part of the job is open ─────────────────────────────
  const label = tab === 'settings'
    ? `Payroll settings — ${SETTINGS_VIEWS.find((v) => v.id === view)?.label ?? 'Entities'}`
    : `${TAB_LABELS[tab]}${allVenues ? ' — all entities' : ''}`;
  usePublishFinanceTab({
    page: 'payroll',
    tab,
    label,
    venue_id: allVenues || tab === 'trial-run' ? null : entity?.venue_id ?? null,
    week_start: NO_WEEK_TABS.has(tab) ? null : weekStart,
    enabled: isPage && !!meta,
  });

  // ── header ───────────────────────────────────────────────────────────────
  const snapshotActions = week && canWrite && !week.closed_at ? (
    <SnapshotActions week={week} api={api} canWrite={canWrite} onWeek={weekState.setWeek} onReload={bump} />
  ) : null;
  const headerSnapshotActions = !isMobile && !allVenues && !NO_WEEK_TABS.has(tab) ? snapshotActions : null;

  const pickerVenues = showsEntityPicker(tab, view) && entities.length > 0
    ? [...(GROUP_TABS.has(tab) ? [{ id: 'all', name: 'All entities' }] : []), ...entities.map((e) => ({ id: e.venue_id, name: e.venue_name }))]
    : undefined;

  const metaLine = tab === 'settings' ? 'Entities, Ace codes, leave types, the rules, and the Xero mapping'
    : tab === 'trial-run' ? 'Norm beside Orbit — what differs, and why'
    : allVenues ? `All entities · ${weekRangeLabel(weekStart)}`
    : entity ? `${entity.venue_name} · ${weekRangeLabel(weekStart)}`
    : undefined;

  const status = week && !allVenues && !NO_WEEK_TABS.has(tab) ? statusLabel(week.status) : null;

  const actions = meta ? (
    <>
      <IconButton icon={RefreshCw} label="Refresh" onClick={() => { bump(); if (tab === 'settings') metaState.reload(); }} />
      {headerSnapshotActions}
      {tab !== 'settings' && <IconButton icon={Settings} label="Payroll settings" onClick={() => switchTab('settings', { view: 'entities' })} />}
    </>
  ) : undefined;

  const toolbar = !meta ? undefined : tab === 'settings' ? (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>
      <div><BackLink label="Back to Payroll" onClick={() => switchTab(returnTab)} testId="payroll-settings-back" /></div>
      <Tabs items={SETTINGS_VIEWS} value={view} onChange={(id) => setParams({ view: id }, false)} label="Payroll settings sections" />
    </div>
  ) : (
    <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', alignItems: isMobile ? 'stretch' : 'center', gap: 10, flexWrap: 'wrap', minWidth: 0 }}>
      {tab !== 'trial-run' && (
        <div style={{ minWidth: 0 }}>
          <WeekPicker value={weekStart} onChange={setWeekStart} venue={entity ?? entities[0]} thisWeek={meta.this_week_start} testId="payroll-week" />
        </div>
      )}
      <div style={{ minWidth: 0, flex: isMobile ? undefined : '1 1 360px' }}>
        <Tabs items={stripTabs(meta.mode)} value={tab} onChange={(id) => switchTab(id as PayrollTab)} label="Payroll sections" />
      </div>
    </div>
  );

  // ── the open tab ─────────────────────────────────────────────────────────
  const snapshotToggle = toggleAvailable ? (
    <div role="tablist" aria-label="Which snapshot" style={{ display: 'inline-flex', gap: 4 }}>
      {(['current', 'close'] as const).map((w) => (
        <Button key={w} size="xs" variant={effectiveWhich === w ? 'primary' : 'quiet'} onClick={() => setWhich(w)} role="tab" aria-selected={effectiveWhich === w}>
          {w === 'current' ? 'Current snapshot' : 'Close snapshot'}
        </Button>
      ))}
    </div>
  ) : null;
  const shiftsMeta = docData?.snapshot ? `${docData.snapshot.shift_count} shifts in the ${effectiveWhich} snapshot` : undefined;

  /** The pay-run snapshot's states, for the tabs that read it. */
  const docGate = (render: (d: PayrunDoc) => ReactNode): ReactNode => {
    if (!week) return null;
    if ((doc.error && !docData && doc.code === 'invalid' && /snapshot/i.test(doc.error)) || (!week.current_snapshot && !docData)) {
      // The route answers 400 "No current snapshot for this week yet."
      return <PageState kind="empty" title={effectiveWhich === 'close' ? 'No close snapshot for this week yet.' : 'No snapshot for this week yet.'} detail={canWrite ? 'Take a snapshot to read this week’s clock-ins from Loaded.' : 'The Monday job takes one 30 minutes after the venue’s day start.'} />;
    }
    if (doc.error && !docData) return <PageState kind="error" title="Could not load the pay run." detail={doc.error} action={<Button size="sm" onClick={doc.retry}>Try again</Button>} />;
    if (!docData) return <PageState kind="loading" title="Loading the pay run…" />;
    return render(docData);
  };

  const tabProps: PayrollTabProps | null = meta ? { meta, entity, allVenues, weekStart, refresh, onChanged: bump, setVenue } : null;

  let body: ReactNode = null;
  if (meta && tabProps) {
    const trialEnded = param('tab') === 'trial-run' && tab !== 'trial-run' && meta.mode === 'live';
    let tabBody: ReactNode;
    if (tab === 'settings') {
      tabBody = <PayrollSettings meta={meta} entity={entity} view={view} onEntitiesChanged={metaState.reload} />;
    } else if (tab === 'trial-run') {
      tabBody = <PayrollShadow {...tabProps} run={param('run')} onRun={(id) => setParams({ run: id ?? undefined })} />;
    } else if (tab === 'intercompany') {
      tabBody = <PayrollIntercompany {...tabProps} />;
    } else if (tab === 'reconciliation') {
      tabBody = <PayrollReconciliation {...tabProps} week={week} />;
    } else if (!entity) {
      tabBody = <PageState kind="empty" title="No payroll entities." detail="None of the venues you can see runs a pay run yet. Switch one on in Payroll settings → Entities." />;
    } else {
      let inner: ReactNode;
      if (tab === 'leave') {
        const docNote = !week ? 'the week is still opening'
          : !week.current_snapshot ? 'none taken for this week yet'
          : doc.error && !docData ? `it could not be loaded: ${doc.error}`
          : !docData ? 'loading' : undefined;
        inner = <PayrollLeave {...tabProps} doc={docData} docNote={docNote} snapshotToggle={snapshotToggle} />;
      } else if (weekState.error && !week) {
        inner = <PageState kind="error" title="Could not open this pay week." detail={weekState.error} action={<Button size="sm" onClick={weekState.reload}>Try again</Button>} />;
      } else if (!week) {
        inner = <PageState kind="loading" title={`Opening ${weLabel(weekStart, true)}…`} />;
      } else if (tab === 'checks') {
        inner = <HygieneTab week={week} api={api} canWrite={canWrite} refresh={refresh} />;
      } else if (tab === 'ace-files') {
        inner = (
          <Stack>
            <DriftNote week={week} builds="The Ace files are built" />
            <ExportsTab week={week} api={api} canWrite={canWrite} refresh={refresh} onChanged={bump} />
          </Stack>
        );
      } else if (tab === 'hours') {
        const tz = week.window?.timezone || entity.timezone || 'Pacific/Auckland';
        const hoursView = param('view');
        inner = (
          <Stack>
            <SnapshotCard week={week} api={api} canWrite={canWrite} onWeek={weekState.setWeek} actions={isMobile ? snapshotActions : undefined} />
            <DriftSection week={week} />
            <Section title="Hours" meta={shiftsMeta} actions={(
              <>
                {snapshotToggle}
                <Button size="xs" variant="quiet" icon={RefreshCw} onClick={bump}>Refresh</Button>
              </>
            )}>
              {docGate((d) => (
                <StaffTab key={hoursView ?? ''} doc={d} timeZone={tz} initialChip={chipFrom(hoursView)} initialView={hoursView === 'timeclock' ? 'shifts' : 'people'} weekLabel={weLabel(weekStart, true)} />
              ))}
            </Section>
          </Stack>
        );
      } else if (tab === 'gratuities') {
        inner = (
          <Section title="Gratuities" meta={shiftsMeta} actions={snapshotToggle}>
            {docGate((d) => <GratuitiesTab doc={d} api={api} canWrite={canWrite} weekStart={weekStart} shadow={shadow} onChanged={bump} />)}
          </Section>
        );
      } else {
        inner = (
          <Section title="Entertainers" meta={shiftsMeta} actions={snapshotToggle}>
            {docGate((d) => <EntertainersTab doc={d} weekStart={weekStart} canPay={canWrite} />)}
          </Section>
        );
      }
      tabBody = (
        <Stack>
          {!entity.loaded_company_id && <Note tone="warn">{entity.venue_name} has no Loaded connection yet, so no snapshot can be taken. Connect it under Settings → Connections.</Note>}
          {!entity.payroll_on && <Note tone="warn">Payroll is switched off for {entity.venue_name} in Payroll settings — the Monday jobs skip it.</Note>}
          {inner}
        </Stack>
      );
    }
    body = (
      <Stack>
        {trialEnded && <Note tone="plain">The trial run has ended: Payroll is live, so there is no Trial run tab any more.</Note>}
        {tabBody}
      </Stack>
    );
  }

  return (
    <WorkspaceTabContext.Provider value={context}>
      <PageShell
        isPage={isPage}
        title={tab === 'settings' ? 'Payroll settings' : 'Payroll'}
        meta={metaLine}
        mode={meta?.mode}
        status={status ? <Badge tone={status.tone}>{status.label}</Badge> : undefined}
        venues={pickerVenues}
        venueId={allVenues ? 'all' : entity?.venue_id ?? null}
        onVenueChange={setVenue}
        venueTestId="payroll-venue"
        actions={actions}
        toolbar={toolbar}
        testId="payroll"
      >
        <MetaGate status={metaState.status} error={metaState.error} loading={metaState.loading}>{body}</MetaGate>
      </PageShell>
    </WorkspaceTabContext.Provider>
  );
}
