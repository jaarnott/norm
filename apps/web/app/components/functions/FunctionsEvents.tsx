'use client';

/**
 * Norm Functions — Events: the list and month calendar of bookings, and the
 * event page. `?page=functions-events&event=<id>` opens an event (staff
 * emails link there) and `&view=calendar` the calendar; opening an event adds
 * a history entry, so the browser's Back returns to the list.
 */
import { useCallback, useEffect, useState } from 'react';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import Button from '../ui/Button';
import ModeBanner from './shared/ModeBanner';
import { readPageParam, useFunctionsMeta } from './shared/meta';
import EventPage from './events/EventPage';
import EventsOverview from './events/EventsOverview';
import type { EventsView } from './events/EventsOverview';
import { todayIn } from './events/logic';
import { setPageParams } from './events/nav';

const viewParam = (): EventsView => (readPageParam('view') === 'calendar' ? 'calendar' : 'list');

export default function FunctionsEvents({ props, data }: DisplayBlockProps) {
  const isPage = !!props?.persistVenue;
  const { meta, error, loading, reload } = useFunctionsMeta();
  // In a conversation the agent may hand over an event to open.
  const given = (props?.event_id as string | undefined) ?? (data?.event_id as string | undefined) ?? null;
  const [eventId, setEventId] = useState<string | null>(() => (isPage ? readPageParam('event') : null) ?? given);
  const [view, setView] = useState<EventsView>(() => (isPage ? viewParam() : 'list'));
  // Whether this page pushed the open event onto history (then Back pops it).
  const [pushed, setPushed] = useState(false);

  useEffect(() => {
    if (!isPage) return;
    const onPop = () => {
      setEventId(readPageParam('event'));
      setView(viewParam());
      setPushed(false);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [isPage]);

  const openEvent = useCallback((id: string) => {
    if (isPage) {
      setPageParams({ event: id }, true);
      setPushed(true);
    }
    setEventId(id);
    if (typeof window !== 'undefined') window.scrollTo?.({ top: 0 });
  }, [isPage]);

  const closeEvent = useCallback(() => {
    if (isPage && pushed) {
      window.history.back();
      return;
    }
    if (isPage) setPageParams({ event: null });
    setEventId(null);
  }, [isPage, pushed]);

  const changeView = useCallback((v: EventsView) => {
    setView(v);
    if (isPage) setPageParams({ view: v === 'calendar' ? 'calendar' : null });
  }, [isPage]);

  if (loading) {
    return (
      <>
        {isPage && <PageHeader title="Events" />}
        <PageState kind="loading" title="Loading…" />
      </>
    );
  }
  if (!meta) {
    return (
      <>
        {isPage && <PageHeader title="Events" />}
        <PageState kind="error" title="Couldn’t load Norm Functions" detail={error ?? undefined} action={<Button size="sm" onClick={reload}>Try again</Button>} />
      </>
    );
  }

  const today = todayIn(meta.company.default_timezone || 'Pacific/Auckland');
  return (
    <div style={{ width: '100%' }}>
      <ModeBanner mode={meta.mode} />
      {eventId && <EventPage key={eventId} eventId={eventId} meta={meta} isPage={isPage} onBack={closeEvent} />}
      {/* The list stays mounted under an open event, so Back returns to the
          same range, filters and page without reloading. */}
      <div hidden={!!eventId}>
        <EventsOverview meta={meta} isPage={isPage} view={view} onView={changeView} today={today} onOpen={openEvent} />
      </div>
    </div>
  );
}
