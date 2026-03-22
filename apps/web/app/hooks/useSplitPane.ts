'use client';

import { useState, useRef, useCallback, useEffect } from 'react';

export interface SplitPaneState {
  containerRef: React.RefObject<HTMLDivElement | null>;
  topPaneHeight: number | null;
  isDragging: boolean;
  handleDragStart: (e: React.MouseEvent) => void;
  handleSplitDoubleClick: () => void;
  setTopPaneHeight: (h: number | null) => void;
}

export function useSplitPane(headerSelector?: string): SplitPaneState {
  const containerRef = useRef<HTMLDivElement>(null);
  const [topPaneHeight, setTopPaneHeight] = useState<number | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // Measure fixed header height (if any) above the split area
  const getHeaderHeight = useCallback(() => {
    if (!containerRef.current || !headerSelector) return 0;
    const el = containerRef.current.querySelector(headerSelector) as HTMLElement | null;
    return el?.offsetHeight || 0;
  }, [headerSelector]);

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleSplitDoubleClick = useCallback(() => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const headerH = getHeaderHeight();
    const available = rect.height - headerH;
    if (topPaneHeight && topPaneHeight > available * 0.8) {
      setTopPaneHeight(null);
    } else {
      setTopPaneHeight(available);
    }
  }, [topPaneHeight, getHeaderHeight]);

  useEffect(() => {
    if (!isDragging) return;
    const handleMove = (e: MouseEvent) => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const headerH = getHeaderHeight();
      const available = rect.height - headerH;
      const offsetY = e.clientY - rect.top - headerH;
      const clamped = Math.max(0, Math.min(offsetY, available));
      setTopPaneHeight(clamped);
    };
    const handleUp = () => setIsDragging(false);
    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
    return () => {
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
    };
  }, [isDragging, getHeaderHeight]);

  return { containerRef, topPaneHeight, isDragging, handleDragStart, handleSplitDoubleClick, setTopPaneHeight };
}
