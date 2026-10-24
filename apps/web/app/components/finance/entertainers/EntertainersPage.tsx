'use client';

/**
 * Entertainers — one page with four tabs: Roster (the default), Performers,
 * Payments (only for someone with the payroll permission) and Emails.
 * `?page=entertainers&tab=roster&performance=<id>` is the closing manager's
 * link: it opens that performance's approval dialog on the Roster.
 *
 * The page owns everything the tabs share, so it draws each of them once:
 * the meta (a Settings save refreshes every tab), the trial-run banner, the
 * header with its counts, the venue picker (Roster, Payments and Emails;
 * Performers is organisation-wide and hides it), the Settings button for
 * those who may change the settings, the active tab's primary action, and
 * the one toast host (PageShell's). The tab, the venue and each tab's own
 * params live in the address bar (useWorkspaceTab); switching tab clears the
 * tab's params, so a performer chosen on Emails never opens a record on
 * Performers. A requested Payments tab without the permission shows the
 * Roster instead, keeping `performance`, so an old payments link still
 * opens the performance.
 */
import { useState } from 'react';
import { Plus, Settings } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import VenueSelect from '../../ui/VenueSelect';
import { useActiveVenue } from '../../../hooks/useActiveVenue';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import PageShell, { ModeBadge } from '../shared/PageShell';
import { WorkspaceTabContext, usePublishFinanceTab, useWorkspaceTab } from '../shared/useWorkspaceTab';
import { headerSummary } from './logic';
import { Notice } from './ui';
import { invalidateEntMeta, useEntMeta } from './useMeta';
import { ENT_TABS, ENT_TAB_LABEL, entTabsAllowed } from './workspace';
import type { EntTab, EntTabProps } from './workspace';
import EntertainersRoster from './EntertainersRoster';
import EntertainersRegister from './EntertainersRegister';
import EntertainersPayments from './EntertainersPayments';
import EntertainersEmails from './EntertainersEmails';
import SettingsDialog from './settings/SettingsDialog';
import type { EntMode } from './types';

/** "Trial run" at the top of the page while entertainers are in shadow
 *  (the code value stays `shadow`). */
function ModeBanner({ mode }: { mode: EntMode | undefined }) {
  if (mode !== 'shadow') return null;
  return (
    <div style={{ marginBottom: 12 }}>
      <Notice tone="warn">Trial run — Orbit is still the live system. Bookings and approvals made here stay in Norm; emails go only to the payroll contact, and nothing is paid from here yet.</Notice>
    </div>
  );
}

const PRIMARY: Partial<Record<EntTab, string>> = { roster: 'Book performer', performers: 'Add performer' };

export default function EntertainersPage({ props }: DisplayBlockProps) {
  const isPage = !!props?.persistVenue;
  const { meta, error, off, loading, reload } = useEntMeta();
  const { isMobile } = useBreakpoint();
  const ws = useWorkspaceTab<EntTab>({
    pageId: 'entertainers',
    tabs: ENT_TABS,
    // Narrow while the meta loads; a Payments deep link lands once it has.
    allowed: entTabsAllowed(meta),
    defaultTab: 'roster',
    syncUrl: isPage,
  });
  const tab = ws.tab;

  // The venue: the address bar's `venue` first (a deep link, or a choice made
  // here), then the venue the page was opened with, then the app's
  // remembered one; else every venue. Choosing a real venue remembers it for
  // the other pages; "All venues" stays on this page.
  const [remembered, remember] = useActiveVenue();
  const known = (id: string | null | undefined): id is string =>
    !!id && (id === 'all' || !!meta?.venues.some((v) => v.id === id));
  const venueId = [ws.param('venue'), props?.activeVenueId as string | undefined, isPage ? remembered : null].find(known) ?? 'all';
  const setVenueId = (v: string) => {
    ws.setParams({ venue: v });
    if (isPage && v !== 'all') remember(v);
  };

  // The header's primary action, for the tab it was pressed on.
  const [primaryFor, setPrimaryFor] = useState<EntTab | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  usePublishFinanceTab({
    page: 'entertainers',
    tab,
    label: ENT_TAB_LABEL[tab],
    venue_id: tab === 'performers' || venueId === 'all' ? null : venueId,
    enabled: isPage && !!meta,
  });

  if (off) {
    return (
      <PageShell isPage={isPage} title="Entertainers">
        <PageState kind="empty" title="Entertainers isn’t switched on for this organisation" detail="A platform admin turns it on (a trial run first, then live)." />
      </PageShell>
    );
  }
  if (loading || !meta) {
    return (
      <PageShell isPage={isPage} title="Entertainers">
        {loading ? (
          <PageState kind="loading" title="Loading…" />
        ) : (
          <PageState kind="error" title="Couldn’t load entertainers" detail={error ?? undefined} action={<Button size="sm" onClick={reload}>Try again</Button>} />
        )}
      </PageShell>
    );
  }

  const tabProps: EntTabProps = {
    meta,
    reload,
    isPage,
    venueId,
    setVenueId,
    param: ws.param,
    setParams: (p) => ws.setParams(p),
    primaryOpen: primaryFor === tab,
    closePrimary: () => setPrimaryFor(null),
  };

  const primary = PRIMARY[tab];
  const actions = (
    <>
      {tab !== 'performers' && <VenueSelect venues={meta.venues} value={venueId} onChange={setVenueId} allowAll />}
      {meta.can_edit_settings && (
        isMobile
          ? <IconButton icon={Settings} label="Entertainers settings" onClick={() => setSettingsOpen(true)} />
          : <Button icon={Settings} onClick={() => setSettingsOpen(true)}>Settings</Button>
      )}
      {primary && (
        <Button variant="primary" icon={Plus} onClick={() => setPrimaryFor(tab)} disabled={tab === 'roster' && meta.venues.length === 0}>
          {primary}
        </Button>
      )}
    </>
  );

  const allowed = entTabsAllowed(meta);

  return (
    <WorkspaceTabContext.Provider value={ws.context}>
      <PageShell
        isPage={isPage}
        title="Entertainers"
        meta={headerSummary(meta.counts, meta.can_pay)}
        status={<ModeBadge mode={meta.mode} />}
        actions={actions}
        toolbar={
          <Tabs
            label="Entertainers"
            value={tab}
            onChange={(id) => { setPrimaryFor(null); ws.setTab(id as EntTab); }}
            items={allowed.map((t) => ({ id: t, label: ENT_TAB_LABEL[t], testId: `ent-tab-${t}` }))}
          />
        }
        testId="entertainers-page"
      >
        <ModeBanner mode={meta.mode} />
        {tab === 'roster' && <EntertainersRoster {...tabProps} />}
        {tab === 'performers' && <EntertainersRegister {...tabProps} />}
        {tab === 'payments' && <EntertainersPayments {...tabProps} />}
        {tab === 'emails' && <EntertainersEmails {...tabProps} />}
        {settingsOpen && (
          <SettingsDialog
            meta={meta}
            onSaved={() => { setSettingsOpen(false); invalidateEntMeta(); reload(); }}
            onClose={() => setSettingsOpen(false)}
          />
        )}
      </PageShell>
    </WorkspaceTabContext.Provider>
  );
}
