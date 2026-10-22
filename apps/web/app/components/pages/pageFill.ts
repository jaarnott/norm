'use client';

import { createContext, useContext, useEffect } from 'react';

/**
 * Lets a component on a menu page ask FunctionalPage for the whole content
 * area — no gutters, no outer scroll — while it shows something that scrolls
 * itself (the Apps page with an app open). Outside a page it does nothing.
 */
export const PageFillContext = createContext<(on: boolean, floatComposer?: boolean) => void>(() => {});

/** ``floatComposer``: keep the message box floating over the page, as on a
 * page that doesn't fill (a Dashboard page), instead of docked under it. The
 * component leaves room at the end of its own scroll for it. */
export function useRequestPageFill(on: boolean, floatComposer = false) {
  const request = useContext(PageFillContext);
  useEffect(() => {
    request(on, floatComposer);
    return () => request(false);
  }, [on, floatComposer, request]);
}
