'use client';

import { useCallback, useEffect, useState } from 'react';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';
import Button from '../ui/Button';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import ModeBanner from './shared/ModeBanner';
import { readPageParam, useFunctionsMeta } from './shared/meta';
import ContactDetail from './contacts/ContactDetail';
import ContactList from './contacts/ContactList';
import { setUrlParams } from './enquiries/nav';

/**
 * Norm Functions — Contacts. The list, and one contact as a sub-page
 * (`?page=functions-contacts&contact=<id>`). The list stays mounted under an
 * open contact so its search and page survive the way back.
 */
/** Page instances on screen: the address bar is only tidied when the last one
 *  goes (the app shell remounts a page in place to open it at new params, and
 *  React's development double-mount unmounts once in between). */
let live = 0;

export default function FunctionsContacts({ props }: DisplayBlockProps) {
  const page = !!props?.persistVenue;
  const { meta, error, reload } = useFunctionsMeta();
  const [openId, setOpenId] = useState<string | null>(() => (page ? readPageParam('contact') : null));
  const [listSeen, setListSeen] = useState(() => !openId);
  const [listKey, setListKey] = useState(0);

  // Leaving the page: drop its params (a tick later — see FunctionsEnquiries).
  useEffect(() => {
    if (!page) return;
    live += 1;
    return () => {
      live -= 1;
      setTimeout(() => {
        if (live === 0 && readPageParam('page') === 'functions-contacts') setUrlParams('contacts', { page: null, contact: null });
      }, 0);
    };
  }, [page]);

  const open = useCallback((id: string) => {
    setOpenId(id);
    if (page) setUrlParams('contacts', { contact: id });
  }, [page]);
  const close = useCallback(() => {
    setOpenId(null);
    setListSeen(true);
    // Re-read the list: the contact may have been edited, archived or deleted.
    setListKey((k) => k + 1);
    if (page) setUrlParams('contacts', { contact: null });
  }, [page]);

  if (error) {
    return (
      <div>
        {page && <PageHeader title="Contacts" />}
        <PageState kind="error" title="Couldn’t open Functions" detail={error} action={<Button size="sm" onClick={reload}>Try again</Button>} />
      </div>
    );
  }
  if (!meta) {
    return (
      <div>
        {page && <PageHeader title="Contacts" />}
        <PageState kind="loading" title="Loading…" />
      </div>
    );
  }
  return (
    <div>
      <ModeBanner mode={meta.mode} />
      {openId && <ContactDetail key={openId} id={openId} meta={meta} page={page} onBack={close} />}
      {listSeen && (
        <div style={openId ? { display: 'none' } : undefined}>
          <ContactList meta={meta} page={page} refreshKey={listKey} onOpen={open} />
        </div>
      )}
    </div>
  );
}
