'use client';

/**
 * Norm Functions — Settings. Per venue: the Functions profile (areas,
 * approvers, policies, menu links, event fields), the proposal brand and
 * image library, pre-order menu and reply templates, web forms; plus the
 * venue mailboxes and a pointer to the organisation's company settings.
 *
 * Deep links: `?page=functions-settings&view=venues|brand|templates|forms|
 * mailboxes|company`, with `venue=<id>`, `kind=replies` (templates) and
 * `form=<id>` (forms). The tab is kept in the address bar as it changes.
 */
import { useState, type ReactNode } from 'react';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import Tabs from '../ui/Tabs';
import VenueSelect from '../ui/VenueSelect';
import ModeBanner from './shared/ModeBanner';
import { readPageParam, useFunctionsMeta } from './shared/meta';
import BrandTab from './settings/BrandTab';
import CompanyTab from './settings/CompanyTab';
import FormsTab from './settings/FormsTab';
import MailboxesTab from './settings/MailboxesTab';
import TemplatesTab, { type TemplateKind } from './settings/TemplatesTab';
import VenuesTab from './settings/VenuesTab';
import { setSettingsUrl } from './settings/nav';
import { useVenueProfile } from './settings/useVenueProfile';

type TabId = 'venues' | 'brand' | 'templates' | 'forms' | 'mailboxes' | 'company';

const TABS: { id: TabId; label: string }[] = [
  { id: 'venues', label: 'Venues' },
  { id: 'brand', label: 'Brand' },
  { id: 'templates', label: 'Templates' },
  { id: 'forms', label: 'Web forms' },
  { id: 'mailboxes', label: 'Mailboxes' },
  { id: 'company', label: 'Company' },
];
const VENUE_TABS = new Set<TabId>(['venues', 'brand', 'templates', 'forms']);

function tabFrom(view: string | null): TabId {
  if (view === 'reply-templates' || view === 'menus') return 'templates';
  return TABS.some((t) => t.id === view) ? (view as TabId) : 'venues';
}

export default function FunctionsSettings({ props }: DisplayBlockProps) {
  const isPage = !!props?.persistVenue;
  const { meta, error, reload } = useFunctionsMeta();
  // Deep-link params, read once on mount.
  const [link, setLink] = useState(() => ({
    view: readPageParam('view'),
    venue: readPageParam('venue'),
    kind: readPageParam('kind'),
    form: readPageParam('form'),
  }));
  const [tab, setTab] = useState<TabId>(() => tabFrom(link.view));
  const [chosen, setChosen] = useState<string | null>(link.venue);
  const [shared, setShared] = useActiveVenue();

  const venues = meta?.venues ?? [];
  // The first of: this page's own choice (or a deep link), the app's page
  // venue, the remembered venue — that the caller can use here.
  const candidates = [chosen, props?.activeVenueId as string | undefined, isPage ? shared : null];
  const venue = candidates.map((id) => venues.find((v) => v.id === id)).find(Boolean) ?? venues[0] ?? null;
  const profile = useVenueProfile(venue?.id ?? null);

  // A deep link's form / template kind applies to the first visit only.
  const changeTab = (id: string) => {
    setTab(id as TabId);
    setLink((l) => ({ ...l, view: null, kind: null, form: null }));
    if (isPage) setSettingsUrl({ view: id, kind: null, form: null });
  };
  const changeVenue = (id: string) => {
    setChosen(id);
    setLink((l) => ({ ...l, form: null }));
    if (isPage) {
      setShared(id);
      setSettingsUrl({ venue: null, form: null });
    }
  };

  const title = 'Functions settings';
  const showVenuePicker = VENUE_TABS.has(tab) && venues.length > 1;
  const header = (
    <PageHeader
      title={title}
      meta="Venues, proposal brand, templates, web forms and mailboxes"
      actions={showVenuePicker && venue ? (
        <VenueSelect venues={venues} value={venue.id} onChange={changeVenue} testId="functions-settings-venue" />
      ) : undefined}
    >
      <Tabs items={TABS} value={tab} onChange={changeTab} label="Functions settings sections" />
    </PageHeader>
  );

  let body: ReactNode;
  if (error) {
    body = <PageState kind="error" title="Couldn’t load Functions settings" detail={error} />;
  } else if (!meta) {
    body = <PageState kind="loading" title="Loading…" />;
  } else if (VENUE_TABS.has(tab) && !venue) {
    body = <PageState kind="empty" title="No venues to set up" detail="You don’t have access to any venue using Functions. Ask an owner for access." />;
  } else if (tab === 'venues' && venue) {
    body = (
      <VenuesTab
        meta={meta}
        venue={venue}
        profile={profile.profile}
        loading={profile.loading}
        error={profile.error}
        setProfile={profile.setProfile}
        reload={profile.reload}
        onMetaChanged={reload}
      />
    );
  } else if (tab === 'brand' && venue) {
    body = (
      <BrandTab
        meta={meta}
        venue={venue}
        profile={profile.profile}
        loading={profile.loading}
        error={profile.error}
        setProfile={profile.setProfile}
        onMetaChanged={reload}
      />
    );
  } else if (tab === 'templates' && venue) {
    body = (
      <TemplatesTab
        meta={meta}
        venueId={venue.id}
        initialKind={(link.kind === 'replies' || link.view === 'reply-templates' ? 'replies' : 'menus') as TemplateKind}
        onKindChange={(k) => { if (isPage) setSettingsUrl({ view: 'templates', kind: k === 'replies' ? 'replies' : null }); }}
      />
    );
  } else if (tab === 'forms' && venue) {
    body = (
      <FormsTab
        key={venue.id}
        meta={meta}
        venueId={venue.id}
        venueTz={venue.timezone_valid && venue.timezone ? venue.timezone : meta.company.default_timezone}
        initialFormId={link.form}
        onFormChange={(formId) => { if (isPage) setSettingsUrl({ view: 'forms', form: formId }); }}
      />
    );
  } else if (tab === 'mailboxes') {
    body = <MailboxesTab meta={meta} />;
  } else {
    body = meta ? <CompanyTab meta={meta} /> : null;
  }

  return (
    <div data-testid="functions-settings">
      <ModeBanner mode={meta?.mode} />
      {isPage ? header : (
        <div style={{ marginBottom: 12 }}>
          <h3 style={{ margin: '0 0 8px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>{title}</h3>
          <Tabs items={TABS} value={tab} onChange={changeTab} label="Functions settings sections" />
        </div>
      )}
      {body}
    </div>
  );
}
