'use client';

import { createContext, useContext, useEffect } from 'react';

/**
 * Lets a component on a menu page ask FunctionalPage for the whole content
 * area — no gutters, no outer scroll — while it shows something that scrolls
 * itself (the Apps page with an app open). Outside a page it does nothing.
 */
export const PageFillContext = createContext<(on: boolean) => void>(() => {});

export function useRequestPageFill(on: boolean) {
  const request = useContext(PageFillContext);
  useEffect(() => {
    request(on);
    return () => request(false);
  }, [on, request]);
}
