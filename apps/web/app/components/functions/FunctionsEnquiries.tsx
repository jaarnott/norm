'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';
import Button from '../ui/Button';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import ModeBanner from './shared/ModeBanner';
import { fnGet } from './shared/api';
import { readPageParam, useFunctionsMeta } from './shared/meta';
import EnquiryDetail from './enquiries/EnquiryDetail';
import { NewEnquiryDialog } from './enquiries/EnquiryDialogs';
import EnquiryList from './enquiries/EnquiryList';
import { filtersFromParams } from './enquiries/filters';
import { setUrlParams } from './enquiries/nav';
import type { Contact } from './enquiries/types';

/** The nearest scrolling ancestor (the page pane), to put the list back where
 *  it was after an enquiry closes. */
function scroller(el: HTMLElement | null): HTMLElement | null {
  for (let n = el?.parentElement ?? null; n; n = n.parentElement) {
    const o = getComputedStyle(n).overflowY;
    if (o === 'auto' || o === 'scroll') return n;
  }
  return null;
}

/**
 * Norm Functions — Enquiries. The list, and one enquiry as a sub-page
 * (`?page=functions-enquiries&enquiry=<id>`; `&new=1[&contact=<id>]` opens
 * "New enquiry"; list filters `stage`, `next_action`, `due_only`, `q`). The
 * list stays mounted under an open enquiry, so its filters, page and scroll
 * are still there on the way back.
 */
/** Page instances on screen: the address bar is only tidied when the last one
 *  goes (the app shell remounts a page in place to open it at new params, and
 *  React's development double-mount unmounts once in between). */
let live = 0;

export default function FunctionsEnquiries({ props }: DisplayBlockProps) {
  const page = !!props?.persistVenue;
  const { meta, error, reload } = useFunctionsMeta();
  // Deep-link params belong to the page instance only, never to a copy of
  // this component inside a conversation.
  const param = (name: string) => (page ? readPageParam(name) : null);
  const [openId, setOpenId] = useState<string | null>(() => param('enquiry'));
  const [listSeen, setListSeen] = useState(() => !param('enquiry'));
  const [initialFilters] = useState(() => filtersFromParams(param));
  // `?new=1[&contact=<id>]` opens "New enquiry" (with that contact, once
  // it's loaded).
  const [creating, setCreating] = useState<{ contact: Contact | null; pendingContactId?: string | null } | null>(() =>
    param('new') === '1' ? { contact: null, pendingContactId: param('contact') } : null);
  const [refreshKey, setRefreshKey] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listScroll = useRef(0);

  // One-shot params (the form, list filters) are read above; drop them from
  // the address bar so they don't fight the toolbar on a reload.
  useEffect(() => {
    if (!page) return;
    setUrlParams('enquiries', {
      new: null, contact: null, stage: null, next_action: null, due_only: null, q: null, sort: null,
      created_from: null, created_to: null, preferred_from: null, preferred_to: null,
    });
  }, [page]);

  const pendingContactId = creating?.pendingContactId ?? null;
  useEffect(() => {
    if (!pendingContactId) return;
    let alive = true;
    fnGet<Contact>(`/contacts/${pendingContactId}`)
      .then((c) => { if (alive) setCreating({ contact: c }); })
      .catch(() => { if (alive) setCreating({ contact: null }); });
    return () => { alive = false; };
  }, [pendingContactId]);

  // Leaving the page (another menu page opened): take this page's params out
  // of the address bar, so a reload doesn't bring it back. Checked a tick
  // later, so React's development double-mount doesn't strip a deep link.
  useEffect(() => {
    if (!page) return;
    live += 1;
    return () => {
      live -= 1;
      setTimeout(() => {
        if (live === 0 && readPageParam('page') === 'functions-enquiries') setUrlParams('enquiries', { page: null, enquiry: null, new: null, contact: null });
      }, 0);
    };
  }, [page]);

  const open = useCallback((id: string) => {
    const sc = scroller(rootRef.current);
    if (!openId && sc) listScroll.current = sc.scrollTop;
    setOpenId(id);
    if (page) setUrlParams('enquiries', { enquiry: id });
    if (sc) sc.scrollTop = 0;
  }, [openId, page]);

  const close = useCallback(() => {
    setOpenId(null);
    setListSeen(true);
    setRefreshKey((k) => k + 1);
    if (page) setUrlParams('enquiries', { enquiry: null });
    const sc = scroller(rootRef.current);
    requestAnimationFrame(() => { if (sc) sc.scrollTop = listScroll.current; });
  }, [page]);

  if (error) {
    return (
      <div>
        {page && <PageHeader title="Enquiries" />}
        <PageState kind="error" title="Couldn’t open Functions" detail={error} action={<Button size="sm" onClick={reload}>Try again</Button>} />
      </div>
    );
  }
  if (!meta) {
    return (
      <div>
        {page && <PageHeader title="Enquiries" />}
        <PageState kind="loading" title="Loading…" />
      </div>
    );
  }

  return (
    <div ref={rootRef}>
      <ModeBanner mode={meta.mode} />
      {openId && (
        <EnquiryDetail key={openId} id={openId} meta={meta} page={page} onBack={close} onOpenEnquiry={open} />
      )}
      {listSeen && (
        <div style={openId ? { display: 'none' } : undefined}>
          <EnquiryList
            meta={meta}
            page={page}
            initialFilters={initialFilters}
            refreshKey={refreshKey}
            onOpen={open}
            onCreate={() => setCreating({ contact: null })}
          />
        </div>
      )}
      {creating && !creating.pendingContactId && meta.can_write && (
        <NewEnquiryDialog
          meta={meta}
          presetContact={creating.contact}
          onCreated={(d) => { setCreating(null); setRefreshKey((k) => k + 1); open(d.enquiry.id); }}
          onClose={() => setCreating(null)}
        />
      )}
    </div>
  );
}
