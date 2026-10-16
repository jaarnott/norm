'use client';

/**
 * Norm Functions — Inbox: the venue mailboxes (health strip, thread list,
 * conversations, the composer and drafts) and, on the same page, an
 * enquiry's proposal editor.
 *
 * Deep links (inbox/nav.ts): `?page=functions-inbox&mail_thread=<account>:<thread>`,
 * `&view=proposal&enquiry=<id>`, `&view=compose[&enquiry=<id>]`. On a page
 * the open view is kept in the address bar (Back goes back to the list); in a
 * conversation the same component keeps it to itself.
 *
 * Opening the Inbox asks for a "sync now" of the connected mailboxes. In
 * shadow mode everything that writes is disabled; reading still works.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { PenLine } from 'lucide-react';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import Button from '../ui/Button';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import ModeBanner from './shared/ModeBanner';
import { useFunctionsMeta } from './shared/meta';
import { errorText, ReadOnlyHint } from './inbox/bits';
import { inboxApi } from './inbox/inboxApi';
import MailboxStrip from './inbox/MailboxStrip';
import { routeFromSearch, searchForRoute, searchWithoutInbox, type InboxRoute } from './inbox/nav';
import NewEmail from './inbox/NewEmail';
import ThreadList, { DEFAULT_FILTERS, type ListFilters } from './inbox/ThreadList';
import ThreadView from './inbox/ThreadView';
import type { MailboxesResponse } from './inbox/types';
import ProposalEditor from './proposals/ProposalEditor';

/** The nearest ancestor that scrolls (the page frame), to keep the list's place. */
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  for (let p = el?.parentElement ?? null; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if ((o === 'auto' || o === 'scroll') && p.scrollHeight > p.clientHeight) return p;
  }
  return null;
}

export default function FunctionsInbox({ props }: DisplayBlockProps) {
  const isPage = !!props?.persistVenue;
  const { meta, error: metaError, reload: reloadMeta } = useFunctionsMeta();
  const [route, setRoute] = useState<InboxRoute>(() =>
    isPage && typeof window !== 'undefined' ? routeFromSearch(window.location.search) : { view: 'list' },
  );
  const [subjectHint, setSubjectHint] = useState<string | null>(null);
  const [filters, setFilters] = useState<ListFilters>(DEFAULT_FILTERS);
  const [listRefresh, setListRefresh] = useState(0);
  const [mailboxTick, setMailboxTick] = useState(0);
  const [mailboxes, setMailboxes] = useState<{ tick: number; data?: MailboxesResponse; error?: string } | null>(null);
  const [sharedVenue, setSharedVenue] = useActiveVenue();
  const [venueChoice, setVenueChoice] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());
  const top = useRef<HTMLDivElement>(null);
  const listScroll = useRef(0);
  const pushed = useRef(0);
  const synced = useRef(false);

  // The address bar follows the view; the browser's Back button moves through it.
  useEffect(() => {
    if (!isPage) return;
    const onPop = () => {
      pushed.current = Math.max(0, pushed.current - 1);
      setRoute(routeFromSearch(window.location.search));
    };
    window.addEventListener('popstate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      // Another page opened from the menu: a reload shouldn't bring the Inbox back.
      const clean = searchWithoutInbox(window.location.search);
      if (clean !== window.location.search) window.history.replaceState(null, '', `${window.location.pathname}${clean}${window.location.hash}`);
    };
  }, [isPage]);
  useEffect(() => {
    if (!isPage) return;
    const next = searchForRoute(window.location.search, route);
    if (next !== window.location.search) window.history.replaceState(null, '', `${window.location.pathname}${next}${window.location.hash}`);
  }, [isPage, route]);

  const go = useCallback(
    (next: InboxRoute, { replace = false }: { replace?: boolean } = {}) => {
      if (isPage && !replace) {
        window.history.pushState(null, '', `${window.location.pathname}${searchForRoute(window.location.search, next)}${window.location.hash}`);
        pushed.current += 1;
      }
      setRoute(next);
    },
    [isPage],
  );

  const backToList = useCallback(() => {
    // Undo our own step when there is one (keeps the browser history tidy).
    if (isPage && pushed.current > 0) window.history.back();
    else go({ view: 'list' }, { replace: true });
  }, [isPage, go]);

  // Back on the list (our Back link or the browser's): where the reader was.
  useEffect(() => {
    if (route.view !== 'list' || !listScroll.current) return;
    const y = listScroll.current;
    const id = requestAnimationFrame(() => {
      const scroller = scrollParent(top.current);
      if (scroller) scroller.scrollTop = y;
    });
    return () => cancelAnimationFrame(id);
  }, [route]);

  const openThread = useCallback(
    (accountId: string, threadId: string, row?: { subject: string | null }) => {
      const scroller = scrollParent(top.current);
      listScroll.current = scroller?.scrollTop ?? 0;
      setSubjectHint(row?.subject ?? null);
      go({ view: 'thread', accountId, threadId });
      requestAnimationFrame(() => {
        const s = scrollParent(top.current);
        if (s) s.scrollTop = 0;
      });
    },
    [go],
  );

  // Mailboxes and their health (shared by every view).
  useEffect(() => {
    let alive = true;
    inboxApi
      .mailboxes()
      .then((data) => alive && setMailboxes({ tick: mailboxTick, data }))
      .catch((err) => alive && setMailboxes({ tick: mailboxTick, error: errorText(err) }));
    return () => {
      alive = false;
    };
  }, [mailboxTick]);

  // "Sync now" when the Inbox opens (mailboxes synced in the last ~20 s are
  // skipped server-side); fresh mail refreshes the list.
  useEffect(() => {
    if (!meta || synced.current) return;
    synced.current = true;
    inboxApi
      .syncNow()
      .then((r) => {
        if (!r.results.length) return;
        setMailboxTick((t) => t + 1);
        if (r.results.some((x) => (x.messages ?? 0) > 0)) setListRefresh((n) => n + 1);
      })
      .catch(() => undefined);
  }, [meta]);

  // Relative dates ("4 min ago") stay true while the page is open.
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(t);
  }, []);

  const frame = (body: ReactNode) => (
    <div ref={top} style={{ width: '100%' }} className={isPage ? undefined : 'n-card'}>
      <div style={isPage ? undefined : { padding: '12px 14px' }}>
        <ModeBanner mode={meta?.mode} />
        {body}
      </div>
    </div>
  );

  if (metaError) {
    return frame(
      <>
        <PageHeader title="Inbox" />
        <PageState kind="error" title="Couldn’t open Norm Functions" detail={metaError} action={<Button size="sm" onClick={reloadMeta}>Try again</Button>} />
      </>,
    );
  }
  if (!meta) return frame(<PageState kind="loading" title="Loading the inbox…" />);

  const canWrite = meta.can_write;
  const boxes = mailboxes?.data?.mailboxes ?? [];
  const remembered = (props?.activeVenueId as string | undefined) || sharedVenue;
  const venueId = venueChoice ?? (remembered && meta.venues.some((v) => v.id === remembered) ? remembered : 'all');
  const changeVenue = (v: string) => {
    setVenueChoice(v);
    setFilters((f) => ({ ...f, offset: 0 }));
    if (isPage && v !== 'all') setSharedVenue(v);
  };
  const timeZone = (venueId !== 'all' && meta.venues.find((v) => v.id === venueId)?.timezone) || meta.company.default_timezone;
  const changed = () => setListRefresh((n) => n + 1);

  // The list stays mounted (hidden) while a conversation is open, so coming
  // back finds it as it was — rows, filters, page and scroll position.
  const list = (
    <div hidden={route.view !== 'list'}>
      <PageHeader
        title="Inbox"
        meta="The venues’ functions mailboxes"
        actions={
          <>
            {meta.venues.length > 1 && <VenueSelect venues={meta.venues} value={venueId} onChange={changeVenue} allowAll />}
            <Button variant="primary" icon={PenLine} onClick={() => go({ view: 'compose', enquiryId: null })} disabled={!canWrite}>
              New email
            </Button>
          </>
        }
      />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {!canWrite && <ReadOnlyHint />}
        {mailboxes?.error ? (
          <PageState kind="error" title="Couldn’t load the mailboxes" detail={mailboxes.error} />
        ) : mailboxes?.data ? (
          <MailboxStrip mailboxes={boxes} venueId={venueId} now={now} />
        ) : null}
        <ThreadList
          venueId={venueId}
          mailboxes={boxes}
          filters={filters}
          onFilters={setFilters}
          onOpen={openThread}
          timeZone={timeZone}
          now={now}
          refreshKey={listRefresh}
        />
      </div>
    </div>
  );

  return frame(
    <>
      {(route.view === 'list' || route.view === 'thread') && list}
      {route.view === 'thread' && (
        <ThreadView
          key={`${route.accountId}:${route.threadId}`}
          accountId={route.accountId}
          threadId={route.threadId}
          meta={meta}
          mailboxes={boxes}
          canWrite={canWrite}
          initialSubject={subjectHint}
          onBack={backToList}
          onChanged={changed}
          now={now}
        />
      )}
      {route.view === 'proposal' && (
        <ProposalEditor key={route.enquiryId} enquiryId={route.enquiryId} meta={meta} mailboxes={boxes} canWrite={canWrite} now={now} />
      )}
      {route.view === 'compose' && (
        <NewEmail
          meta={meta}
          mailboxes={boxes}
          canWrite={canWrite}
          enquiryId={route.enquiryId}
          venueId={venueId}
          onBack={backToList}
          onSent={(accountId, r) => {
            changed();
            if (r.gmail_thread_id) go({ view: 'thread', accountId, threadId: r.gmail_thread_id }, { replace: true });
            else backToList();
          }}
        />
      )}
    </>,
  );
}
