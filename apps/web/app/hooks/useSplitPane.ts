'use client';

import { useState, useRef, useCallback } from 'react';

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
  const isDraggingRef = useRef(false);
  const latestHeight = useRef<number | null>(null);

  const getHeaderHeight = useCallback(() => {
    if (!containerRef.current || !headerSelector) return 0;
    const el = containerRef.current.querySelector(headerSelector) as HTMLElement | null;
    return el?.offsetHeight || 0;
  }, [headerSelector]);

  const findTopPane = useCallback(() => {
    if (!containerRef.current) return null;
    const header = containerRef.current.querySelector('[data-split-header]') as HTMLElement | null;
    return (header ? header.nextElementSibling : containerRef.current.firstElementChild) as HTMLElement | null;
  }, []);

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const container = containerRef.current;
    const topPane = findTopPane();
    if (!container || !topPane) return;
    const headerH = getHeaderHeight();

    isDraggingRef.current = true;
    container.style.userSelect = 'none';
    document.body.style.cursor = 'row-resize';

    const onMove = (ev: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      const available = rect.height - headerH;
      const offsetY = ev.clientY - rect.top - headerH;
      const clamped = Math.max(0, Math.min(offsetY, available));
      topPane.style.height = clamped + 'px';
      latestHeight.current = clamped;
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      isDraggingRef.current = false;
      container.style.userSelect = '';
      document.body.style.cursor = '';
      if (latestHeight.current !== null) {
        setTopPaneHeight(latestHeight.current);
      }
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [getHeaderHeight, findTopPane]);

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

  // isDragging is always false for React — all drag state is DOM-only
  return { containerRef, topPaneHeight, isDragging: false, handleDragStart, handleSplitDoubleClick, setTopPaneHeight };
}
