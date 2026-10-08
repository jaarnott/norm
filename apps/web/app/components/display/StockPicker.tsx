'use client';

import { ChevronRight } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import Icon from '../ui/Icon';

interface Candidate {
  id: string;
  name: string;
  group: string;
}

interface AmbiguousItem {
  query: string;
  quantity: number;
  candidates: Candidate[];
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

export default function StockPicker({ data, onAction }: DisplayBlockProps) {
  const items = (data?.needs_selection as AmbiguousItem[]) || [];

  if (items.length === 0) return null;

  const handleSelect = (candidate: Candidate) => {
    if (onAction) {
      onAction({ connector_name: 'norm', action: 'send_message', params: { message: candidate.name } });
    }
  };

  return (
    <div className="n-card" style={{ maxWidth: 460, marginTop: '0.4rem', overflow: 'hidden' }}>
      {items.map((item, i) => (
        <div key={i} role="group" aria-label={`Select ${item.query}`} style={i > 0 ? { borderTop: '1px solid var(--line)' } : undefined}>
          <div style={{ padding: '12px 14px 10px' }}>
            <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>Select {item.query}</div>
            <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Qty {item.quantity}</div>
          </div>
          {item.candidates.map(c => (
            <button key={c.id} type="button" className="n-option" onClick={() => handleSelect(c)} style={rowStyle}>
              <span style={{ flex: 1, minWidth: 0 }}>{c.name}</span>
              {c.group && (
                <span style={{ maxWidth: '45%', textAlign: 'right', fontSize: 'var(--fs-sm)', fontWeight: 400, color: 'var(--muted)' }}>
                  {c.group}
                </span>
              )}
              <Icon icon={ChevronRight} size="dense" tone="muted" />
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}
