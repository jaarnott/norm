'use client';

import { useState, useRef, useCallback } from 'react';

interface HomePanelProps {
  onSend: (message: string) => void;
  loading: boolean;
}

export default function HomePanel({ onSend, loading }: HomePanelProps) {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleInput = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (input.trim()) { onSend(input); setInput(''); }
    }
  }, [onSend, input]);

  return (
    <div className="full-height" style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: '#faf8f5',
    }}>
      <div style={{ textAlign: 'center', marginBottom: '1.5rem' }}>
        <div style={{ fontSize: '1.8rem', fontWeight: 700, color: '#111', marginBottom: '0.25rem' }}>
          Norm
        </div>
        <div style={{ fontSize: '1rem', color: '#999' }}>
          AI Operations Control — What would you like to do?
        </div>
      </div>

      <form onSubmit={e => { e.preventDefault(); if (input.trim()) { onSend(input); setInput(''); } }} style={{ display: 'flex', alignItems: 'flex-end', gap: '0.4rem', width: '100%', maxWidth: 768, padding: '0 1.5rem' }}>
        <textarea
          data-testid="home-message-input"
          ref={el => {
            (textareaRef as React.MutableRefObject<HTMLTextAreaElement | null>).current = el;
            if (el) { el.style.height = 'auto'; const h = Math.min(el.scrollHeight, 150); el.style.height = h + 'px'; el.style.overflow = h >= 150 ? 'auto' : 'hidden'; }
          }}
          value={input}
          onChange={e => {
            handleInput(e);
            const el = e.target; el.style.height = 'auto'; const h = Math.min(el.scrollHeight, 150); el.style.height = h + 'px'; el.style.overflow = h >= 150 ? 'auto' : 'hidden';
          }}
          onKeyDown={handleKeyDown}
          placeholder="Get Norm to do it"
          rows={1}
          style={{
            flex: 1,
            minHeight: 50,
            maxHeight: 150,
            padding: '14px 1.5rem',
            fontSize: '1rem',
            border: '1px solid #ddd',
            borderRadius: 24,
            outline: 'none',
            fontFamily: 'inherit',
            resize: 'none',
            lineHeight: '1.4',
            boxSizing: 'border-box',
            overflow: 'hidden',
          }}
        />
        <button
          data-testid="home-send-btn"
          type="submit"
          disabled={loading}
          style={{
            height: 50,
            padding: '0 1rem',
            fontSize: '0.95rem',
            fontWeight: 600,
            backgroundColor: '#111',
            color: '#fff',
            border: 'none',
            borderRadius: 24,
            cursor: loading ? 'not-allowed' : 'pointer',
            fontFamily: 'inherit',
          }}
        >
          {loading ? '...' : 'Send'}
        </button>
      </form>
    </div>
  );
}
