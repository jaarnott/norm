'use client';

import { useRef, useCallback } from 'react';

interface HomePanelProps {
  input: string;
  onInputChange: (value: string) => void;
  onSend: (e: React.FormEvent) => void;
  loading: boolean;
}

export default function HomePanel({ input, onInputChange, onSend, loading }: HomePanelProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleInput = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    onInputChange(e.target.value);
    const el = e.target;
    el.style.height = 'auto';
    const h = Math.min(el.scrollHeight, 150);
    el.style.height = h + 'px';
    el.style.overflow = el.scrollHeight > 150 ? 'auto' : 'hidden';
  }, [onInputChange]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSend(e as unknown as React.FormEvent);
    }
  }, [onSend]);

  return (
    <div style={{
      height: '100vh',
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

      <form onSubmit={onSend} style={{ display: 'flex', alignItems: 'flex-end', gap: '0.4rem', width: '100%', maxWidth: 768, padding: '0 1.5rem' }}>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder="e.g. Order stock, check a roster, or generate a sales report"
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
