'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import { useSplitPane } from '../../hooks/useSplitPane';
import SplitDragHandle from '../layout/SplitDragHandle';
import DisplayBlockRenderer from '../display/DisplayBlockRenderer';
import { ConversationView } from '../tasks/ThreadDetail';
import type { FunctionalPageConfig } from './pageRegistry';
import type { Thread, WidgetAction } from '../../types';

interface FunctionalPageProps {
  config: FunctionalPageConfig;
  thread: Thread | null;
  onSend: (message: string) => void;
  loading: boolean;
  onWidgetAction?: (threadId: string, action: WidgetAction) => Promise<Record<string, unknown> | void>;
  activeVenueId?: string | null;
}

export default function FunctionalPage({ config, thread, onSend, loading, onWidgetAction, activeVenueId }: FunctionalPageProps) {
  const [input, setInput] = useState('');
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [workingDocId, setWorkingDocId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingData, setLoadingData] = useState(true);
  const [activeReportId, setActiveReportId] = useState<string | null>(null);
  const { containerRef, topPaneHeight, isDragging, handleDragStart, handleSplitDoubleClick } = useSplitPane();

  // Reset report view when switching pages
  useEffect(() => { setActiveReportId(null); }, [config.id]);

  // Load data on mount — create a working document so edits sync in background
  useEffect(() => {
    // Skip data load for self-loading components (e.g., SavedReportsBoard)
    if (config.loadAction.connector === '_none') {
      setLoadingData(false);
      return;
    }
    // Don't load external connector data without a venue selected
    if (!activeVenueId && config.loadAction.connector !== 'norm') {
      setLoadingData(false);
      setData(null);
      setLoadError(null);
      return;
    }
    setLoadingData(true);
    setLoadError(null);
    setData(null);
    const params = config.loadAction.defaultParams();
    apiFetch('/api/working-documents/from-connector', {
      method: 'POST',
      body: JSON.stringify({
        connector_name: config.loadAction.connector,
        action: config.loadAction.action,
        params: { ...params, ...(activeVenueId ? { venue_id: activeVenueId } : {}) },
        doc_type: config.id,
        venue_id: activeVenueId || undefined,
      }),
    })
      .then(async res => {
        if (!res.ok) {
          const text = await res.text();
          try {
            const d = JSON.parse(text);
            setLoadError(d.error || d.detail || `Failed to load data (${res.status})`);
          } catch {
            setLoadError(`Failed to load data (${res.status})`);
          }
          return;
        }
        const result = await res.json();
        setWorkingDocId(result.id);
        setData({ working_document_id: result.id, ...result.data });
      })
      .catch(err => setLoadError(err.message))
      .finally(() => setLoadingData(false));
  }, [config.id, activeVenueId]);

  const handleAction = useCallback(async (action: WidgetAction): Promise<Record<string, unknown> | void> => {
    // Handle report builder open locally
    if (action.action === 'open_report_builder' && action.params?.report_id) {
      setActiveReportId(action.params.report_id as string);
      return { ok: true };
    }

    // Navigate to automated task conversation — pass through to page handler
    if (action.action === 'open_automated_task' && action.params?.conversation_thread_id && onWidgetAction) {
      return onWidgetAction(thread?.id || '_nav', action);
    }

    if (thread && onWidgetAction) {
      return onWidgetAction(thread.id, action);
    }
    // No task yet — execute directly via connector
    try {
      const res = await apiFetch(`/api/connectors/${action.connector_name}/execute/${action.action}`, {
        method: 'POST',
        body: JSON.stringify({ params: action.params }),
      });
      if (res.ok) {
        return await res.json();
      }
    } catch { /* ignore */ }
  }, [thread, onWidgetAction]);

  const messages = thread?.conversation || [];
  const hasConversation = !!thread;

  const inputBar = (
    <div style={{ padding: '12px 24px 24px' }}>
      <form onSubmit={e => { e.preventDefault(); if (input.trim()) { onSend(input); setInput(''); } }} style={{ maxWidth: 768, margin: '0 auto', display: 'flex', alignItems: 'flex-end', gap: '0.4rem' }}>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              if (input.trim()) { onSend(input); setInput(''); }
            }
          }}
          placeholder="Message Norm..."
          rows={1}
          style={{
            flex: 1, minHeight: 50, maxHeight: 150,
            padding: '14px 1.5rem', fontSize: '1rem',
            border: '1px solid #ddd', borderRadius: 24, outline: 'none', fontFamily: 'inherit',
            resize: 'none', lineHeight: '1.4', boxSizing: 'border-box', overflow: 'hidden',
          }}
        />
        <button type="submit" disabled={loading} style={{
          height: 50, padding: '0 1rem', fontSize: '0.8rem', fontWeight: 600,
          backgroundColor: '#111', color: '#fff', border: 'none', borderRadius: 24,
          cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
        }}>
          {loading ? '...' : 'Send'}
        </button>
      </form>
    </div>
  );

  const noVenueSelected = !activeVenueId && config.loadAction.connector !== 'norm' && config.loadAction.connector !== '_none';

  const componentBlock = noVenueSelected ? (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, color: '#999', fontSize: '0.9rem' }}>
      Select a venue to load {config.label.toLowerCase()}
    </div>
  ) : (data || config.loadAction.connector === '_none') ? (
    <DisplayBlockRenderer
      block={{
        component: config.component,
        data: data || {},
        props: { ...config.componentProps, activeVenueId },
      }}
      onAction={handleAction}
      threadId={thread?.id}
    />
  ) : null;

  // If a report is open, show the Report Builder full-screen
  if (activeReportId) {
    return (
      <div style={{ height: '100vh', position: 'relative', backgroundColor: '#fff' }}>
        <div style={{ height: '100%', overflowY: 'auto', paddingBottom: '100px' }}>
          <div style={{ padding: '0.5rem 1rem', borderBottom: '1px solid #e2e8f0' }}>
            <button
              onClick={() => setActiveReportId(null)}
              style={{
                border: 'none', background: 'none', color: '#888', cursor: 'pointer',
                fontSize: '0.82rem', fontFamily: 'inherit', padding: '4px 0',
              }}
            >&larr; Back to Reports</button>
          </div>
          <div style={{ height: 'calc(100vh - 150px)' }}>
            <DisplayBlockRenderer
              block={{
                component: 'report_builder',
                data: { report_id: activeReportId },
                props: {},
              }}
              onAction={handleAction}
              threadId={thread?.id}
            />
          </div>
        </div>
        <div style={{
          position: 'absolute', bottom: 0, left: 0, right: 0,
          padding: '20px 0 0',
          background: 'radial-gradient(ellipse at bottom, rgba(255,255,255,0.95) 60%, transparent 100%)',
        }}>
          {inputBar}
        </div>
      </div>
    );
  }

  // Phase 1: Full-height component (no conversation yet)
  if (!hasConversation) {
    return (
      <div style={{ height: '100vh', position: 'relative', backgroundColor: '#fff' }}>
        <div style={{ height: '100%', overflowY: 'auto', padding: '1rem 1.5rem', paddingBottom: '100px' }}>
          {loadingData && (
            <div style={{ padding: '2rem', textAlign: 'center', color: '#999' }}>Loading...</div>
          )}
          {loadError && (
            <div style={{ padding: '2rem', textAlign: 'center', color: '#e53e3e' }}>{loadError}</div>
          )}
          {componentBlock}
        </div>
        <div style={{
          position: 'absolute', bottom: 0, left: 0, right: 0,
          padding: '20px 0 0',
          background: 'radial-gradient(ellipse at bottom, rgba(255,255,255,0.95) 60%, transparent 100%)',
        }}>
          {inputBar}
        </div>
      </div>
    );
  }

  // Phase 2: Split view — component top, conversation bottom
  return (
    <div ref={containerRef} style={{
      height: '100vh', display: 'flex', flexDirection: 'column',
      backgroundColor: '#fff', userSelect: isDragging ? 'none' : undefined,
    }}>
      {/* Top pane: component */}
      <div style={{
        height: topPaneHeight ?? '50%',
        flexShrink: 0,
        overflowY: 'scroll',
      }}>
        <div style={{ padding: '0.75rem 0.5rem 0.75rem 1.5rem' }}>
          {loadingData && <div style={{ padding: '1rem', color: '#999' }}>Loading...</div>}
          {loadError && <div style={{ padding: '1rem', color: '#e53e3e' }}>{loadError}</div>}
          {componentBlock}
        </div>
      </div>

      <SplitDragHandle
        isDragging={isDragging}
        topPaneHeight={topPaneHeight}
        containerRef={containerRef}
        onMouseDown={handleDragStart}
        onDoubleClick={handleSplitDoubleClick}
      />

      {/* Bottom pane: conversation + input */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: '1.25rem 1.5rem' }}>
          <div style={{ maxWidth: 768, margin: '0 auto' }}>
            <ConversationView
              messages={messages}
              onWidgetAction={onWidgetAction && thread ? (action) => onWidgetAction(thread.id, action) : undefined}
              threadId={thread?.id}
            />
          </div>
        </div>
        {inputBar}
      </div>
    </div>
  );
}
