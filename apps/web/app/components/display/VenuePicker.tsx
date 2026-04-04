'use client';

import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface Venue {
  id: string;
  name: string;
}

export default function VenuePicker({ data, onAction }: DisplayBlockProps) {
  const venues = (data?.venues as Venue[]) || [];

  if (venues.length === 0) return null;

  const handleClick = (message: string) => {
    if (onAction) {
      onAction({ connector_name: 'norm', action: 'send_message', params: { message } });
    }
  };

  const btnStyle: React.CSSProperties = {
    padding: '6px 14px',
    fontSize: '0.82rem',
    fontWeight: 500,
    border: '1px solid #e5e7eb',
    borderRadius: 8,
    backgroundColor: '#fff',
    color: '#374151',
    cursor: 'pointer',
    fontFamily: 'inherit',
    transition: 'all 0.15s',
  };

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.4rem' }}>
      {venues.map(v => (
        <button
          key={v.id}
          onClick={() => handleClick(v.name)}
          style={btnStyle}
          onMouseEnter={e => { e.currentTarget.style.borderColor = '#111'; e.currentTarget.style.backgroundColor = '#f9fafb'; }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = '#e5e7eb'; e.currentTarget.style.backgroundColor = '#fff'; }}
        >
          {v.name}
        </button>
      ))}
      <button
        onClick={() => handleClick('all venues')}
        style={{ ...btnStyle, color: '#6366f1', borderColor: '#c7d2fe' }}
        onMouseEnter={e => { e.currentTarget.style.borderColor = '#6366f1'; e.currentTarget.style.backgroundColor = '#eef2ff'; }}
        onMouseLeave={e => { e.currentTarget.style.borderColor = '#c7d2fe'; e.currentTarget.style.backgroundColor = '#fff'; }}
      >
        All venues
      </button>
    </div>
  );
}
