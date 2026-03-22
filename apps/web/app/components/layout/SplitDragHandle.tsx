'use client';

interface SplitDragHandleProps {
  isDragging: boolean;
  topPaneHeight: number | null;
  containerRef: React.RefObject<HTMLDivElement | null>;
  onMouseDown: (e: React.MouseEvent) => void;
  onDoubleClick: () => void;
}

export default function SplitDragHandle({ isDragging, topPaneHeight, containerRef, onMouseDown, onDoubleClick }: SplitDragHandleProps) {
  const containerHeight = containerRef.current?.getBoundingClientRect().height || 0;
  const paneCollapsed = topPaneHeight !== null && (
    topPaneHeight < 20 || topPaneHeight > containerHeight - 40
  );

  return (
    <div
      className="split-drag-handle"
      onMouseDown={onMouseDown}
      onDoubleClick={onDoubleClick}
      style={{
        height: paneCollapsed ? 10 : 1,
        flexShrink: 0,
        cursor: 'row-resize',
        backgroundColor: paneCollapsed ? '#f0f0f0' : '#e2e8f0',
        position: 'relative',
        transition: isDragging ? 'none' : 'height 0.15s',
      }}
    >
      <div style={{
        position: 'absolute', left: 0, right: 0, top: -6, bottom: -6,
        cursor: 'row-resize',
      }} />
      <div className="split-drag-pill" style={{
        position: 'absolute', left: '50%', top: '50%',
        transform: 'translate(-50%, -50%)',
        width: 32, height: 3, borderRadius: 2,
        backgroundColor: isDragging ? '#999' : '#ccc',
        opacity: paneCollapsed || isDragging ? 1 : 0,
        transition: 'opacity 0.15s',
      }} />
    </div>
  );
}
