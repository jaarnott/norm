'use client';

import { useState, useEffect } from 'react';
import { breakpoints } from '../lib/theme';

interface BreakpointState {
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
}

export function useBreakpoint(): BreakpointState {
  const [state, setState] = useState<BreakpointState>({
    isMobile: false,
    isTablet: false,
    isDesktop: true,
  });

  useEffect(() => {
    const mobileQuery = window.matchMedia(`(max-width: ${breakpoints.mobile - 1}px)`);
    const tabletQuery = window.matchMedia(`(min-width: ${breakpoints.mobile}px) and (max-width: ${breakpoints.tablet - 1}px)`);

    const update = () => {
      const mobile = mobileQuery.matches;
      const tablet = tabletQuery.matches;
      setState({ isMobile: mobile, isTablet: tablet, isDesktop: !mobile && !tablet });
    };

    update();
    mobileQuery.addEventListener('change', update);
    tabletQuery.addEventListener('change', update);
    return () => {
      mobileQuery.removeEventListener('change', update);
      tabletQuery.removeEventListener('change', update);
    };
  }, []);

  return state;
}
