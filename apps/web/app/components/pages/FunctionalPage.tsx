'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '../../lib/api';
import { useSplitPane } from '../../hooks/useSplitPane';
import SplitDragHandle from '../layout/SplitDragHandle';
import DisplayBlockRenderer from '../display/DisplayBlockRenderer';
import { ConversationView } from '../threads/ThreadDetail';
import type { SendOptions } from '../chat/AttachmentComposer';
import Composer from '../chat/Composer';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import BackLink from '../ui/BackLink';
import { PageFillContext } from './pageFill';
import type { FunctionalPageConfig } from './pageRegistry';
import type { Thread, WidgetAction } from '../../types';

interface FunctionalPageProps {
  config: FunctionalPageConfig;
  thread: Thread | null;
  onSend: (message: string, opts?: SendOptions) => void;
  loading: boolean;
  onWidgetAction?: (threadId: string, action: WidgetAction) => Promise<Record<string, unknown> | void>;
  activeVenueId?: string | null;
}

// Fades the page out under the floating composer — from cream, the page's own
// colour, so nothing reads as a white band across the content.
const COMPOSER_FADE = 'linear-gradient(to bottom, rgba(250, 248, 245, 0) 0%, var(--canvas) 45%)';

/**
 * Every menu page: one frame (cream, 24px gutters, no max-width), the page's
 * own header, and the composer. A page that owns its whole area (an app)
 * `fill`s: no gutters, no outer scroll, the composer docked below it rather
 * than floating over it.
 *
 * page.tsx keys this by page id, so moving between pages never shows the last
 * page's data under the next page's title.
 */
export default function FunctionalPage({ config, thread, onSend, loading, onWidgetAction, activeVenueId }: FunctionalPageProps) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingData, setLoadingData] = useState(config.loadAction.connector !== '_none');
  const [activeReportId, setActiveReportId] = useState<string | null>(null);
  // A page component can ask for the whole area (the Apps page, once it has
  // opened an app). App pages always have it.
  const [fillRequested, setFillRequested] = useState(false);
  const fill = config.component === 'app_runner' || fillRequested;
  const { containerRef, topPaneHeight, isDragging, handleDragStart, handleSplitDoubleClick } = useSplitPane();

  // The floating composer covers the bottom of the page; pad the page by its
  // real height so the last rows can always scroll clear of it.
  const dockRef = useRef<HTMLDivElement | null>(null);
  const [dockHeight, setDockHeight] = useState(96);
  const observeDock = useCallback((el: HTMLDivElement | null) => {
    dockRef.current = el;
    if (!el) return;
    setDockHeight(el.offsetHeight);
    const ro = new ResizeObserver(() => setDockHeight(el.offsetHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Load data on mount — create a working document so edits sync in background
  useEffect(() => {
    // Skip data load for self-loading components (e.g., SavedReportsBoard)
    if (config.loadAction.connector === '_none') {
      setLoadingData(false);
      return;
    }
    setLoadingData(true);
    setLoadError(null);
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

    // Open a recipe (Menu Engineering row click) — pass through to the page handler
    // which switches to the Recipes page and preloads it.
    if (action.action === 'open_recipe' && action.params?.recipe_id && onWidgetAction) {
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

  const composer = (
    <Composer
      onSend={(text, attachments) => onSend(text, { pageContext: { page_id: config.id, agent: config.agent }, attachments })}
      loading={loading}
      venueId={activeVenueId}
    />
  );
  const floatingComposer = (
    <div
      ref={observeDock}
      className="n-composer-dock"
      // Clicks pass through the fade to the page; only the composer takes them.
      // z-index 10: above a page's sticky parts (grid columns use ≤5), below its
      // dropdowns (20+) and dialogs (1000).
      style={{ position: 'absolute', bottom: 0, left: 0, right: 0, zIndex: 10, paddingTop: 28, background: COMPOSER_FADE, pointerEvents: 'none' }}
    >
      {composer}
    </div>
  );

  // If a report is open, show the Report Builder full-screen
  if (activeReportId) {
    return (
      <div style={{ height: '100%', position: 'relative', backgroundColor: 'var(--canvas)' }}>
        <div style={{ height: '100%', overflowY: 'auto', paddingBottom: dockHeight + 16 }}>
          <div style={{ padding: '14px 24px 8px' }}>
            <BackLink label="Reports" onClick={() => setActiveReportId(null)} />
          </div>
          <div style={{ height: 'calc(100dvh - 150px)' }}>
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
        {floatingComposer}
      </div>
    );
  }

  const componentBlock = (data || config.loadAction.connector === '_none') ? (
    <DisplayBlockRenderer
      block={{
        component: config.component,
        data: data || {},
        // persistVenue marks this as a PAGE instance: its venue selector may
        // read from and write to the shared, remembered page venue. The same
        // component rendered inside a conversation gets no such flag, so a
        // conversation never moves the page venue or inherits it.
        props: { ...config.componentProps, activeVenueId, persistVenue: true },
      }}
      onAction={handleAction}
      threadId={thread?.id}
    />
  ) : null;

  // While FunctionalPage itself is loading the page's data, it shows the
  // page's title; once loaded, the component draws its own header.
  const pageBody = loadingData ? (
    <><PageHeader title={config.label} /><PageState kind="loading" title="Loading…" /></>
  ) : loadError ? (
    <><PageHeader title={config.label} /><PageState kind="error" title={`Couldn’t load ${config.label}`} detail={loadError} /></>
  ) : componentBlock;

  // ONE layout for both states. The with-conversation and without-conversation
  // views used to be two different element trees, so the FIRST message from a
  // page moved the component to a new tree position — React remounted it and
  // every bit of its local state (an open app, an editor draft, scroll) reset
  // to zero, which read as "Norm navigated me back to the base page". Keeping
  // the component pane at a stable position and only resizing/adding siblings
  // around it means sending a message never remounts what you're looking at.
  // (The same holds for `fill`: only the wrapper's props change.)
  return (
    <PageFillContext.Provider value={setFillRequested}>
      <div ref={containerRef} style={{
        height: '100%', display: 'flex', flexDirection: 'column', position: 'relative',
        backgroundColor: 'var(--canvas)', userSelect: isDragging ? 'none' : undefined,
      }}>
        {/* Component pane — full height until a conversation exists. */}
        <div style={{
          ...(hasConversation ? { height: topPaneHeight ?? '50%', flexShrink: 0 } : { flex: 1 }),
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          overflowY: fill ? 'hidden' : 'auto',
        }}>
          <div
            className={fill ? undefined : 'n-page'}
            style={fill
              ? { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }
              : { paddingBottom: hasConversation ? 20 : dockHeight + 16 }}
          >
            {fill && pageBody !== componentBlock ? <div className="n-page">{pageBody}</div> : pageBody}
          </div>
        </div>

        {hasConversation && (
          <SplitDragHandle
            isDragging={isDragging}
            topPaneHeight={topPaneHeight}
            containerRef={containerRef}
            onMouseDown={handleDragStart}
            onDoubleClick={handleSplitDoubleClick}
          />
        )}

        {/* Conversation + composer. Without a conversation the composer floats
            over the bottom of the page — or, on a page that fills its area,
            sits docked under it. */}
        {hasConversation ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
            <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>
              <div style={{ maxWidth: 768, margin: '0 auto' }}>
                <ConversationView
                  messages={messages}
                  onWidgetAction={onWidgetAction && thread ? (action) => onWidgetAction(thread.id, action) : undefined}
                  threadId={thread?.id}
                />
              </div>
            </div>
            <div className="n-composer-dock">{composer}</div>
          </div>
        ) : fill ? (
          <div className="n-composer-dock" style={{ paddingTop: 8 }}>{composer}</div>
        ) : floatingComposer}
      </div>
    </PageFillContext.Provider>
  );
}
