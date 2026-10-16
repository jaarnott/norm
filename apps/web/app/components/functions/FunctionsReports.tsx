'use client';

import { useEffect, useState } from 'react';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';
import Button from '../ui/Button';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import Tabs from '../ui/Tabs';
import VenueSelect from '../ui/VenueSelect';
import ModeBanner from './shared/ModeBanner';
import { readPageParam, useFunctionsMeta } from './shared/meta';
import { FnHeader } from './enquiries/bits';
import { setUrlParams } from './enquiries/nav';
import { useVenueFilter } from './enquiries/useVenueFilter';
import ApprovalsReport from './reports/ApprovalsReport';
import ConversionReport from './reports/ConversionReport';
import EnquiriesReport from './reports/EnquiriesReport';
import LeadSourcesReport from './reports/LeadSourcesReport';
import SalesReport from './reports/SalesReport';

const TABS = [
  { id: 'enquiries', label: 'Enquiries over time' },
  { id: 'conversion', label: 'Conversion' },
  { id: 'sales', label: 'Upcoming sales' },
  { id: 'leads', label: 'Lead sources' },
  { id: 'approvals', label: 'Approvals log' },
] as const;
type TabId = (typeof TABS)[number]['id'];

const isTab = (v: string | null): v is TabId => !!v && TABS.some((t) => t.id === v);

/**
 * Norm Functions — Reports: one page, five tabs (`?page=functions-reports&
 * view=enquiries|conversion|sales|leads|approvals`), all scoped by the venue
 * picker the Functions pages share. The numbers are the API's.
 */
/** Page instances on screen: the address bar is only tidied when the last one
 *  goes (the app shell remounts a page in place to open it at new params, and
 *  React's development double-mount unmounts once in between). */
let live = 0;

export default function FunctionsReports({ props }: DisplayBlockProps) {
  const page = !!props?.persistVenue;
  const { meta, error, reload } = useFunctionsMeta();
  const [venue, setVenue, venueOptions] = useVenueFilter(meta);
  const [tab, setTab] = useState<TabId>(() => {
    const v = page ? readPageParam('view') : null;
    return isTab(v) ? v : 'enquiries';
  });

  // Leaving the page: drop its params (a tick later — see FunctionsEnquiries).
  useEffect(() => {
    if (!page) return;
    live += 1;
    return () => {
      live -= 1;
      setTimeout(() => {
        if (live === 0 && readPageParam('page') === 'functions-reports') setUrlParams('reports', { page: null, view: null });
      }, 0);
    };
  }, [page]);

  const choose = (id: string) => {
    if (!isTab(id)) return;
    setTab(id);
    if (page) setUrlParams('reports', { view: id });
  };

  if (error) {
    return (
      <div>
        {page && <PageHeader title="Reports" />}
        <PageState kind="error" title="Couldn’t open Functions" detail={error} action={<Button size="sm" onClick={reload}>Try again</Button>} />
      </div>
    );
  }
  if (!meta) {
    return (
      <div>
        {page && <PageHeader title="Reports" />}
        <PageState kind="loading" title="Loading…" />
      </div>
    );
  }

  return (
    <div>
      <ModeBanner mode={meta.mode} />
      <FnHeader
        page={page}
        title="Functions reports"
        meta={venue === 'all' ? 'All your venues' : venueOptions.find((v) => v.id === venue)?.name}
        actions={venueOptions.length > 1 ? <VenueSelect venues={venueOptions} value={venue} onChange={setVenue} allowAll testId="fn-reports-venue" /> : undefined}
      >
        <Tabs items={TABS.map((t) => ({ id: t.id, label: t.label, testId: `fn-report-tab-${t.id}` }))} value={tab} onChange={choose} label="Reports" />
      </FnHeader>
      {tab === 'enquiries' && <EnquiriesReport meta={meta} venue={venue} />}
      {tab === 'conversion' && <ConversionReport meta={meta} venue={venue} />}
      {tab === 'sales' && <SalesReport meta={meta} venue={venue} />}
      {tab === 'leads' && <LeadSourcesReport meta={meta} venue={venue} />}
      {tab === 'approvals' && <ApprovalsReport meta={meta} venue={venue} />}
    </div>
  );
}
