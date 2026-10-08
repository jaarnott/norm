'use client';

import { ChevronRight, Layers, Store } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import Icon from '../ui/Icon';

interface Venue {
  id: string;
  name: string;
}

/** One choice: a full-width row in the card. Hover comes from .n-option, so no
 *  inline background here; the focus ring sits inside the card's clipped edge. */
const rowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 10,
  minHeight: 40,
  padding: '8px 14px',
  borderTop: '1px solid var(--line-soft)',
  fontSize: 'var(--fs-base)',
  fontWeight: 500,
  lineHeight: 1.35,
  outlineOffset: -2,
};

export default function VenuePicker({ data, onAction }: DisplayBlockProps) {
  const venues = (data?.venues as Venue[]) || [];

  if (venues.length === 0) return null;

  const handleClick = (message: string) => {
    if (onAction) {
      onAction({ connector_name: 'norm', action: 'send_message', params: { message } });
    }
  };

  return (
    <div className="n-card" role="group" aria-label="Choose a venue" style={{ maxWidth: 460, marginTop: '0.4rem', overflow: 'hidden' }}>
      <div style={{ padding: '12px 14px 10px', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>
        Choose a venue
      </div>
      {venues.map(v => (
        <button key={v.id} type="button" className="n-option" onClick={() => handleClick(v.name)} style={rowStyle}>
          <Icon icon={Store} tone="muted" />
          <span style={{ flex: 1, minWidth: 0 }}>{v.name}</span>
          <Icon icon={ChevronRight} size="dense" tone="muted" />
        </button>
      ))}
      <button type="button" className="n-option" onClick={() => handleClick('all venues')} style={rowStyle}>
        <Icon icon={Layers} tone="muted" />
        <span style={{ flex: 1, minWidth: 0 }}>All venues</span>
        <Icon icon={ChevronRight} size="dense" tone="muted" />
      </button>
    </div>
  );
}
