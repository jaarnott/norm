'use client';

/**
 * Reports → Weekly Report — the `weekly_report` page
 * (docs/plans/agents-and-apps-restructure.md §5).
 *
 *  - Last week's report as the email holds it: sales against budget and last
 *    year by venue, wages, top sellers and cost of goods, read live from
 *    Loaded (`get_daily_performance`, `get_sales` items, and
 *    `get_cogs_detail_for_period` once per venue), every call with the plain
 *    period "last week" so each venue's trading day decides the dates.
 *  - Who gets it and when: the weekly report emails are Norm automated tasks
 *    this page sets up and changes through `/api/automated-tasks`
 *    (weeklyreport/schedule.ts), with Send now running one for real.
 *
 * Self-loading (the page registry's load action is `_none`).
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import type { AutomatedTask, AutomatedTaskRun } from '../../types';
import { apiFetch, getActiveOrg, getStoredUser } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect, { type VenueOption } from '../ui/VenueSelect';
import Button from '../ui/Button';
import ConnectPrompt from './ConnectPrompt';
import ReportPreview, { type Loadable } from './weeklyreport/ReportPreview';
import ReportSchedules, { type EmailApproval } from './weeklyreport/ReportSchedules';
import ScheduleDialog from './weeklyreport/ScheduleDialog';
import {
  cogsLine,
  extractDoc,
  toolError,
  toolMessage,
  tradingDays,
  type CogsDoc,
  type CogsLine,
  type ItemsDoc,
  type PerfDoc,
} from './weeklyreport/report';
import { REPORT_TITLE, isWeeklyReportTask } from './weeklyreport/schedule';

const EMAIL_KEY = 'norm_email.send_report_email';
const LOADING = { doc: null, loading: true, error: null };

async function runTool(action: string, params: Record<string, unknown>): Promise<{ body: unknown; error: string | null }> {
  try {
    const res = await apiFetch(`/api/connectors/loadedhub/execute/${action}`, {
      method: 'POST',
      body: JSON.stringify({ params }),
    });
    const body = await res.json().catch(() => ({}));
    return { body, error: toolError(res, body) };
  } catch (e) {
    return { body: null, error: e instanceof Error ? e.message : 'Could not reach Norm' };
  }
}

export default function WeeklyReport({ props, onAction }: DisplayBlockProps) {
  const persistVenue = !!props?.persistVenue;
  const propVenue = (props?.activeVenueId as string) || null;
  const { isMobile, isTablet } = useBreakpoint();
  const stacked = isMobile || isTablet;
  const [, setActiveVenue] = useActiveVenue();

  // Venues with a working Loaded connection (connect-info). The executor
  // would fall back to another venue's login for one without; the page only
  // ever asks for connected venues.
  const [connected, setConnected] = useState<VenueOption[] | null>(null);
  const [scope, setScope] = useState<string>('all');
  const [reloadKey, setReloadKey] = useState(0);

  const [perf, setPerf] = useState<Loadable<PerfDoc>>(LOADING);
  const [items, setItems] = useState<Loadable<ItemsDoc>>(LOADING);
  const [cogs, setCogs] = useState<{ lines: CogsLine[]; loading: boolean }>({ lines: [], loading: true });

  const [tasks, setTasks] = useState<AutomatedTask[] | null>(null);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasksError, setTasksError] = useState<string | null>(null);
  const [lastRuns, setLastRuns] = useState<Record<string, AutomatedTaskRun | null>>({});
  const [me, setMe] = useState<{ id: string; email: string } | null>(null);
  const [emailApproval, setEmailApproval] = useState<EmailApproval>(null);
  const [editing, setEditing] = useState<AutomatedTask | 'new' | null>(null);
  const [pageNotice, setPageNotice] = useState<string | null>(null);

  // ---- Who's connected, who's looking -------------------------------------
  const loadConnected = useCallback(async () => {
    const info = await apiFetch('/api/connectors/loadedhub/connect-info')
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
    const list: VenueOption[] = ((info?.venues ?? []) as { venue_id: string; venue_name: string; status: string }[])
      .filter((v) => v.status === 'connected')
      .map((v) => ({ id: v.venue_id, name: v.venue_name }));
    setConnected(list);
  }, []);

  useEffect(() => {
    void loadConnected();
    apiFetch('/api/auth/me')
      .then((r) => (r.ok ? r.json() : null))
      .then((u) => { if (u?.id) setMe({ id: u.id, email: u.email }); })
      .catch(() => {});
    apiFetch('/api/approval-preferences')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const row = ((d?.tools ?? []) as { key: string; value?: string }[]).find((t) => t.key === EMAIL_KEY);
        setEmailApproval(row?.value === 'always' ? 'always' : row?.value === 'ask' ? 'ask' : null);
      })
      .catch(() => {});
  }, [loadConnected]);

  // ---- Last week's figures ------------------------------------------------
  // Loads follow the SET of connected venues, not the array: re-reading
  // connect-info (Refresh, a second mount in development) must not start a
  // second round of Loaded calls that cancels the first.
  const connectedKey = connected ? connected.map((v) => v.id).join(',') : null;
  const loadSeq = useRef(0);
  useEffect(() => {
    if (!connected || connected.length === 0) return;
    const seq = ++loadSeq.current;
    const live = () => seq === loadSeq.current;
    const one = scope !== 'all' ? connected.find((v) => v.id === scope) ?? null : null;
    // The venue whose Loaded login the group calls go through.
    const anchor = one?.id ?? (propVenue && connected.some((v) => v.id === propVenue) ? propVenue : connected[0].id);
    const venues = one ? {} : { venues: 'all' };

    setPerf(LOADING);
    setItems(LOADING);
    const targets = one ? [one] : connected;
    setCogs({ lines: targets.map((v) => cogsLine(v.id, v.name, null)), loading: true });

    void runTool('get_daily_performance', { venue_id: anchor, period: 'last week', interval: 'week', ...venues })
      .then(({ body, error }) => {
        if (!live()) return;
        const doc = extractDoc<PerfDoc>(body, 'by_venue') ?? extractDoc<PerfDoc>(body, 'summary');
        setPerf({ doc, loading: false, error: error ?? (doc ? null : toolMessage(body) ?? 'Loaded returned no figures for last week') });
      });
    void runTool('get_sales', { venue_id: anchor, period: 'last week', breakdown: 'items', top: 10, ...venues })
      .then(({ body, error }) => {
        if (!live()) return;
        const doc = extractDoc<ItemsDoc>(body, 'rows');
        setItems({ doc, loading: false, error: error ?? (doc ? null : toolMessage(body) ?? 'Loaded returned no items for last week') });
      });
    // Cost of goods is one venue per call; each row fills in as it answers.
    let pending = targets.length;
    for (const v of targets) {
      void runTool('get_cogs_detail_for_period', { venue_id: v.id, period: 'last week' }).then(({ body, error }) => {
        if (!live()) return;
        const doc = extractDoc<CogsDoc>(body, 'summary');
        const line = cogsLine(v.id, v.name, doc, error ?? (doc ? null : toolMessage(body) ?? 'No cost of goods from Loaded'));
        pending -= 1;
        setCogs((prev) => ({ lines: prev.lines.map((l) => (l.venueId === v.id ? line : l)), loading: pending > 0 }));
      });
    }
    // propVenue only picks which login the group calls use, and `connected`
    // is read through connectedKey (above).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectedKey, scope, reloadKey]);

  // ---- The report emails ---------------------------------------------------
  const loadTasks = useCallback(async () => {
    setTasksLoading(true);
    setTasksError(null);
    try {
      // Every task the person can see, then this page's own: a weekly report
      // made in chat and titled "Weekly Report" may be filed under any member.
      const res = await apiFetch('/api/automated-tasks');
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        setTasksError(typeof body?.detail === 'string' ? body.detail : `Could not load the report emails (error ${res.status})`);
        return;
      }
      // A platform admin is sent every organisation's tasks: keep the ones of
      // the organisation being acted in.
      const org = getActiveOrg() ?? getStoredUser()?.active_organization_id ?? null;
      const mine = ((body?.tasks ?? []) as AutomatedTask[]).filter(
        (t) => isWeeklyReportTask(t) && (!org || !t.organization_id || t.organization_id === org),
      );
      setTasks(mine);
      const runs = await Promise.all(mine.map((t) =>
        apiFetch(`/api/automated-tasks/${t.id}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => [t.id, ((d?.runs ?? []) as AutomatedTaskRun[])[0] ?? null] as const)
          .catch(() => [t.id, null] as const),
      ));
      setLastRuns(Object.fromEntries(runs));
    } catch (e) {
      setTasksError(e instanceof Error ? e.message : 'Could not load the report emails');
    } finally {
      setTasksLoading(false);
    }
  }, []);

  useEffect(() => { void loadTasks(); }, [loadTasks]);

  const openTask = useCallback(async (task: AutomatedTask) => {
    let threadId = task.conversation_thread_id;
    if (!threadId) {
      const res = await apiFetch(`/api/automated-tasks/${task.id}/ensure-conversation`, { method: 'POST' }).catch(() => null);
      if (res?.ok) threadId = (await res.json().catch(() => null))?.conversation_thread_id ?? null;
    }
    if (threadId && onAction) {
      await onAction({ connector_name: 'norm', action: 'open_automated_task', params: { conversation_thread_id: threadId } });
    }
  }, [onAction]);

  const allowEmails = useCallback(async () => {
    const res = await apiFetch('/api/approval-preferences', {
      method: 'PUT',
      body: JSON.stringify({ key: EMAIL_KEY, value: 'always' }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(typeof body?.detail === 'string' ? body.detail : `Could not change it (error ${res.status})`);
    }
    setEmailApproval('always');
  }, []);

  // ---- Page ----------------------------------------------------------------
  const venueNames = useMemo(() => (connected ?? []).map((v) => v.name), [connected]);
  const oneVenue = scope !== 'all' || (connected?.length ?? 0) === 1;
  const week = tradingDays(perf.doc?.window);
  const venueCount = perf.doc?.by_venue?.length ?? 0;
  const meta = perf.loading
    ? 'Last week'
    : [week ? `Last week: ${week}` : 'Last week', venueCount > 1 ? `${venueCount} venues` : null].filter(Boolean).join(' · ');

  const handleScope = (id: string) => {
    setScope(id);
    if (persistVenue && id !== 'all') setActiveVenue(id);
  };

  const actions = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      {connected && connected.length > 1 && (
        <VenueSelect venues={connected} value={scope} onChange={handleScope} allowAll label="Venues in the report" />
      )}
      <Button
        size="sm"
        variant="quiet"
        icon={RefreshCw}
        onClick={() => { void loadConnected().then(() => setReloadKey((k) => k + 1)); void loadTasks(); }}
      >
        Refresh
      </Button>
    </div>
  );

  if (connected === null) {
    return (
      <div>
        <PageHeader title={REPORT_TITLE} meta="Last week" />
        <div className="n-card"><PageState kind="loading" title="Loading…" /></div>
      </div>
    );
  }

  if (connected.length === 0) {
    return (
      <div>
        <PageHeader title={REPORT_TITLE} />
        <ConnectPrompt
          connector="loadedhub"
          venueId={propVenue}
          title="No venue is connected to Loaded"
          detail="The weekly report reads sales, wages and cost of goods from Loaded. Connect a venue to see it."
        />
      </div>
    );
  }

  return (
    <div>
      <PageHeader title={REPORT_TITLE} meta={meta} actions={actions} />
      {pageNotice && (
        <div role="status" style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 'var(--radius)', fontSize: 'var(--fs-sm)', color: 'var(--warn)', background: 'var(--warn-bg)' }}>
          {pageNotice}
        </div>
      )}

      <ReportSchedules
        tasks={tasks}
        loading={tasksLoading}
        error={tasksError}
        lastRuns={lastRuns}
        myUserId={me?.id ?? null}
        emailApproval={emailApproval}
        stacked={stacked}
        onAdd={() => setEditing('new')}
        onEdit={(t) => setEditing(t)}
        onChanged={() => void loadTasks()}
        onOpenTask={(t) => void openTask(t)}
        onAllowEmails={allowEmails}
      />

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', margin: '24px 0 10px' }}>
        <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>Last week&rsquo;s report</h2>
        <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          {week ? `What the email holds. Its subject: ${REPORT_TITLE} — ${week}` : 'What the email holds'}
        </span>
      </div>
      <ReportPreview perf={perf} items={items} cogs={cogs} oneVenue={oneVenue} stacked={stacked} onRetry={() => setReloadKey((k) => k + 1)} />

      {editing && (
        <ScheduleDialog
          task={editing === 'new' ? null : editing}
          venueNames={venueNames}
          defaultRecipient={me?.email ?? null}
          timezone={perf.doc?.window?.timezone ?? null}
          dayStart={perf.doc?.window?.day_start ?? null}
          onSaved={(notice) => { setPageNotice(notice ?? null); void loadTasks(); }}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
