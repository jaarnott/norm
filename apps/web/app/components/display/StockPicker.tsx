'use client';

import type { DisplayBlockProps } from './DisplayBlockRenderer';

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

export default function StockPicker({ data, onAction }: DisplayBlockProps) {
  const items = (data?.needs_selection as AmbiguousItem[]) || [];

  if (items.length === 0) return null;

  const handleSelect = (candidate: Candidate) => {
    if (onAction) {
      onAction({ connector_name: 'norm', action: 'send_message', params: { message: candidate.name } });
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '0.4rem' }}>
      {items.map((item, i) => (
        <div key={i}>
          <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: '0.3rem' }}>
            Select <strong>{item.query}</strong> (qty: {item.quantity}):
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
            {item.candidates.map(c => (
              <button
                key={c.id}
                onClick={() => handleSelect(c)}
                style={{
                  padding: '5px 12px',
                  fontSize: '0.78rem',
                  fontWeight: 500,
                  border: '1px solid #e5e7eb',
                  borderRadius: 8,
                  backgroundColor: '#fff',
                  color: '#374151',
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                  transition: 'all 0.15s',
                  textAlign: 'left',
                }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = '#111'; e.currentTarget.style.backgroundColor = '#f9fafb'; }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = '#e5e7eb'; e.currentTarget.style.backgroundColor = '#fff'; }}
              >
                {c.name}
                {c.group && <span style={{ color: '#9ca3af', fontSize: '0.7rem', marginLeft: 6 }}>{c.group}</span>}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
