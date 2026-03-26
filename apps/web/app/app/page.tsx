'use client';

import { useState, useMemo, useCallback, useEffect } from 'react';
import Sidebar from '../components/layout/Sidebar';
import ThreadList from '../components/tasks/ThreadList';
import ThreadDetail from '../components/tasks/ThreadDetail';
import RoutingIndicator from '../components/routing/RoutingIndicator';
import HomePanel from '../components/home/HomePanel';
import SettingsPanel from '../components/settings/SettingsPanel';
import LoginForm from '../components/auth/LoginForm';
import FunctionalPage from '../components/pages/FunctionalPage';
import QuotaExceededModal from '../components/display/QuotaExceededModal';
import { FUNCTIONAL_PAGES } from '../components/pages/pageRegistry';
import { apiFetch, apiStream, getToken, setToken, clearToken, getStoredUser, setStoredUser } from '../lib/api';
import { PanelLeft as PanelLeftIcon, ArrowLeft } from 'lucide-react';
import { useBreakpoint } from '../hooks/useBreakpoint';
import type { Thread, WidgetAction, VenueDetail } from '../types';

type AuthUser = { id: string; email: string; full_name: string; role: string; permissions: string[]; org_role: { name: string; display_name: string } | null };

export default function Home() {
  const [token, setTokenState] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeAgent, setActiveAgent] = useState('home');
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'awaiting_approval' | 'awaiting_user_input' | 'completed'>('all');
  const [routing, setRouting] = useState(false);
  const [routingDomain, setRoutingDomain] = useState<string | null>(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [activePage, setActivePage] = useState<string | null>(null);
  const [venues, setVenues] = useState<VenueDetail[]>([]);
  const [activeVenueId, setActiveVenueId] = useState<string | null>(null);
  const [quotaExceeded, setQuotaExceeded] = useState<{ used: number; quota: number } | null>(null);
  const { isMobile } = useBreakpoint();
  const [mobileView, setMobileView] = useState<'list' | 'detail' | 'home' | 'settings'>('home');

  // Check for existing auth on mount
  useEffect(() => {
    const existing = getToken();
    if (existing) {
      // Validate token
      apiFetch('/api/auth/me')
        .then(res => res.ok ? res.json() : null)
        .then(data => {
          if (data) {
            setTokenState(existing);
            setUser(data);
            setStoredUser(data);
          } else {
            clearToken();
          }
        })
        .catch(() => clearToken())
        .finally(() => setAuthChecked(true));
    } else {
      setAuthChecked(true);
    }
  }, []);

  // Load threads when authenticated
  useEffect(() => {
    if (!token) return;
    apiFetch('/api/threads')
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data?.threads?.length) setThreads(data.threads);
      })
      .catch(() => {});
  }, [token]);

  // Load venues when authenticated
  useEffect(() => {
    if (!token) return;
    apiFetch('/api/venues')
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data?.venues) {
          setVenues(data.venues);
          // Auto-select first venue if none selected
          if (!activeVenueId && data.venues.length > 0) {
            setActiveVenueId(data.venues[0].id);
          }
        }
      })
      .catch(() => {});
  }, [token]);

  const handleAuthSuccess = useCallback((newToken: string, newUser: { id: string; email: string; full_name: string; role: string }) => {
    setToken(newToken);
    setTokenState(newToken);
    // Login response has basic user info; fetch /me for full profile with permissions
    apiFetch('/api/auth/me')
      .then(res => res.ok ? res.json() : null)
      .then(fullUser => {
        const u = fullUser || { ...newUser, permissions: [], org_role: null };
        setUser(u);
        setStoredUser(u);
      })
      .catch(() => {
        const u = { ...newUser, permissions: [] as string[], org_role: null };
        setUser(u);
        setStoredUser(u);
      });
  }, []);

  const handleLogout = useCallback(() => {
    clearToken();
    setTokenState(null);
    setUser(null);
    setThreads([]);
    setSelectedThreadId(null);
    setActiveAgent('home');
  }, []);

  const selectedThread = threads.find(t => t.id === selectedThreadId) || null;
  const openThread = threads.find(t => t.status === 'awaiting_user_input');

  // Fetch full thread detail when selecting a thread that only has summary data
  useEffect(() => {
    if (!selectedThreadId || selectedThreadId.startsWith('_pending_')) return;
    const thread = threads.find(t => t.id === selectedThreadId);
    if (!thread || thread.conversation) return; // already has full data
    apiFetch(`/api/threads/${selectedThreadId}`)
      .then(res => res.ok ? res.json() : null)
      .then(full => {
        if (full) setThreads(prev => prev.map(t => t.id === selectedThreadId ? full : t));
      })
      .catch(() => {});
  }, [selectedThreadId]);

  // Thread counts per agent
  const threadCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    threads.forEach(t => {
      counts[t.domain] = (counts[t.domain] || 0) + 1;
    });
    return counts;
  }, [threads]);

  const sendMessage = useCallback(async (messageText: string) => {
    if (!messageText.trim()) return;

    const threadIdForRequest = selectedThreadId;
    setLoading(true);

    // Create an optimistic thread so the conversation view appears immediately
    const optimisticId = `_pending_${Date.now()}`;
    if (!threadIdForRequest) {
      const optimistic: Thread = {
        id: optimisticId,
        domain: 'unknown',
        intent: '',
        title: null,
        message: messageText,
        status: 'in_progress',
        tags: [],
        created_at: new Date().toISOString(),
        conversation: [{ role: 'user', text: messageText }],
        thinking_steps: [],
      };
      setThreads(prev => [optimistic, ...prev]);
      setSelectedThreadId(optimisticId);
    } else {
      // Existing thread — append user message optimistically
      setThreads(prev => prev.map(t =>
        t.id === threadIdForRequest
          ? { ...t, conversation: [...(t.conversation || []), { role: 'user', text: messageText }], thinking_steps: ['Working on it\u2026'] }
          : t
      ));
    }

    const currentId = threadIdForRequest || optimisticId;
    // realThreadId is the confirmed backend ID — used for recovery re-fetches.
    let realThreadId: string | null = threadIdForRequest;
    let streamErrored = false;
    let tokenBuffer = '';
    let streamMode: 'pending' | 'tool' | 'conversation' = 'pending';
    const TOOL_PREFIX = '[Tool]';
    let displayedLength = 0;
    let animFrameId: number | null = null;

    const typewriterLoop = () => {
      if (displayedLength < tokenBuffer.length) {
        const backlog = tokenBuffer.length - displayedLength;
        // Adaptive speed: 1 char when nearly caught up, up to 4 when large backlog
        const charsPerFrame = Math.max(1, Math.min(4, Math.floor(backlog / 20)));
        displayedLength = Math.min(displayedLength + charsPerFrame, tokenBuffer.length);
        const visibleText = tokenBuffer.slice(0, displayedLength);
        setThreads(prev => prev.map(t => {
          if (t.id !== currentId) return t;
          const conv = t.conversation || [];
          const last = conv[conv.length - 1];
          if (last?.role === 'streaming') {
            return { ...t, conversation: [...conv.slice(0, -1), { ...last, text: visibleText }] };
          }
          return { ...t, thinking_steps: [], conversation: [...conv.filter(m => m.role !== 'streaming'), { role: 'streaming' as const, text: visibleText }] };
        }));
      }
      animFrameId = requestAnimationFrame(typewriterLoop);
    };

    const startTypewriter = () => {
      if (!animFrameId) animFrameId = requestAnimationFrame(typewriterLoop);
    };

    const stopTypewriter = () => {
      if (animFrameId) { cancelAnimationFrame(animFrameId); animFrameId = null; }
    };

    try {
      await apiStream(
        '/api/messages/stream',
        threadIdForRequest
          ? { message: messageText, thread_id: threadIdForRequest }
          : { message: messageText },
        (event) => {
          if (event.type === 'thread_created') {
            // Store the real thread ID for recovery — but don't remap the
            // optimistic thread yet to avoid a flash where selectedThread is null.
            realThreadId = event.thread_id as string;
          } else if (event.type === 'routing') {
            setThreads(prev => prev.map(t =>
              t.id === currentId ? {
                ...t,
                domain: event.domain || t.domain,
                title: event.title || t.title,
                thinking_steps: [`I'll get the ${event.agent_label || event.domain} agent to look at this one…`],
              } : t
            ));
          } else if (event.type === 'stream_cancel') {
            // LLM is calling a tool — clear any streaming conversation text
            stopTypewriter();
            streamMode = 'pending';
            tokenBuffer = '';
            displayedLength = 0;
            setThreads(prev => prev.map(t => {
              if (t.id !== currentId) return t;
              return { ...t, conversation: (t.conversation || []).filter(m => m.role !== 'streaming') };
            }));
          } else if (event.type === 'thinking') {
            // Tool reasoning from backend — show as thinking step, reset for next stream
            stopTypewriter();
            streamMode = 'pending';
            tokenBuffer = '';
            displayedLength = 0;
            setThreads(prev => prev.map(t => {
              if (t.id !== currentId) return t;
              return {
                ...t,
                thinking_steps: [...(t.thinking_steps || []), event.text || ''],
              };
            }));
          } else if (event.type === 'token') {
            tokenBuffer += event.text || '';

            if (streamMode === 'pending') {
              // Check if this is tool reasoning (starts with [Tool]) or conversation
              if (tokenBuffer.startsWith(TOOL_PREFIX)) {
                streamMode = 'tool';
                const explanation = tokenBuffer.slice(TOOL_PREFIX.length).replace(/^\s+/, '');
                setThreads(prev => prev.map(t => {
                  if (t.id !== currentId) return t;
                  return {
                    ...t,
                    thinking_steps: [...(t.thinking_steps || []).filter(s => !s.startsWith('🔧 ')), '🔧 ' + explanation],
                    conversation: (t.conversation || []).filter(m => m.role !== 'streaming'),
                  };
                }));
              } else if (tokenBuffer.length >= TOOL_PREFIX.length) {
                // Not a tool prefix — this is the final conversation response
                streamMode = 'conversation';
                startTypewriter();
              }
            } else if (streamMode === 'tool') {
              const explanation = tokenBuffer.slice(TOOL_PREFIX.length).replace(/^\s+/, '');
              setThreads(prev => prev.map(t => {
                if (t.id !== currentId) return t;
                return {
                  ...t,
                  thinking_steps: [...(t.thinking_steps || []).filter(s => !s.startsWith('🔧 ')), '🔧 ' + explanation],
                };
              }));
            } else {
              // conversation mode — typewriter loop handles rendering
              // tokenBuffer is already updated, loop will pick it up
            }
          } else if (event.type === 'complete') {
            stopTypewriter();
            const data = event.data as Thread;
            setThreads(prev => {
              const idx = prev.findIndex(t => t.id === currentId);
              if (idx >= 0) {
                const next = [...prev];
                // Preserve automated_task metadata (not returned by stream)
                const existing = next[idx];
                next[idx] = { ...data, automated_task: data.automated_task ?? existing.automated_task };
                return next;
              }
              return [data, ...prev];
            });
            setSelectedThreadId(data.id);
            // Backfill full LLM call data (system prompts, etc.) from the detail endpoint
            apiFetch(`/api/threads/${data.id}`).then(r => r.ok ? r.json() : null).then(full => {
              if (full) {
                setThreads(prev => prev.map(t => t.id === data.id ? {
                  ...t,
                  llm_calls: full.llm_calls ?? t.llm_calls,
                  tool_calls: full.tool_calls ?? t.tool_calls,
                  automated_task: full.automated_task ?? t.automated_task,
                } : t));
              }
            }).catch(() => {});
          } else if (event.type === 'quota_exceeded') {
            setQuotaExceeded({ used: event.used ?? 0, quota: event.quota ?? 0 });
            streamErrored = true;
          } else if (event.type === 'error') {
            console.error('Stream error:', event.message);
            streamErrored = true;
          }
        },
      );

      // If the stream errored, try to recover by re-fetching the real thread.
      // realThreadId is set from the task_created event, so this works for both
      // new conversations and follow-ups.
      if (streamErrored && realThreadId) {
        try {
          const res = await apiFetch(`/api/threads/${realThreadId}`);
          if (res.ok) {
            const freshThread = await res.json();
            setThreads(prev => prev.map(t => t.id === currentId ? freshThread : t));
            setSelectedThreadId(freshThread.id);
            streamErrored = false;
          }
        } catch (e) { console.error(e); }
      }

      if (streamErrored) {
        setThreads(prev => prev.map(t =>
          t.id === currentId
            ? {
                ...t,
                thinking_steps: [],
                status: t.status === 'in_progress' ? 'completed' : t.status,
                conversation: [
                  ...(t.conversation || []),
                  { role: 'assistant' as const, text: 'Something went wrong. Please try again.' },
                ],
              }
            : t
        ));
      }
    } catch (err) {
      console.error('Failed to send message:', err);
      if (realThreadId) {
        try {
          const res = await apiFetch(`/api/threads/${realThreadId}`);
          if (res.ok) {
            const freshThread = await res.json();
            setThreads(prev => prev.map(t => t.id === currentId ? freshThread : t));
            setSelectedThreadId(freshThread.id);
          }
        } catch (e) { console.error(e); }
      }
    } finally {
      setLoading(false);
    }
  }, [selectedThreadId]);

  const handleNewChat = useCallback(() => {
    setSelectedThreadId(null);
    setActivePage(null);
    setActiveAgent('home');
    setMobileView('home');
  }, []);

  const handleSelectPage = useCallback((pageId: string) => {
    setActivePage(pageId);
    setSelectedThreadId(null);
  }, []);

  const handleAction = useCallback(async (threadId: string, action: string) => {
    // Reload just re-fetches the thread detail
    if (action === 'reload') {
      try {
        const res = await apiFetch(`/api/threads/${threadId}`);
        if (res.ok) {
          const updated = await res.json();
          setThreads(prev => prev.map(t => (t.id === threadId ? updated : t)));
        }
      } catch { /* ignore */ }
      return;
    }
    try {
      const res = await apiFetch(`/api/threads/${threadId}/${action}`, {
        method: 'POST',
      });
      if (!res.ok) {
        console.error('Action error:', res.status, await res.text());
        return;
      }
      const updated = await res.json();
      setThreads(prev => prev.map(t => (t.id === threadId ? updated : t)));
    } catch (err) {
      console.error(`Failed to ${action}:`, err);
    }
  }, []);

  const handleWidgetAction = useCallback(async (threadId: string, action: WidgetAction): Promise<Record<string, unknown> | void> => {
    // Check if a report builder is currently open for this thread
    if (action.action === 'get_active_report') {
      const thread = threads.find(t => t.id === threadId);
      if (thread?.conversation) {
        for (const msg of [...thread.conversation].reverse()) {
          const rb = msg.display_blocks?.find((b: { component: string }) => b.component === 'report_builder');
          if (rb) return { report_id: (rb.data as Record<string, unknown>).report_id };
        }
      }
      return { report_id: null };
    }

    // Handle tool approval/rejection from ToolApprovalCard
    if (action.action === 'tool_approve' || action.action === 'tool_reject') {
      const targetAction = action.action === 'tool_approve' ? 'approve' : 'reject';
      await handleAction(threadId, targetAction);
      return { ok: true };
    }

    // Navigate to an automated task's conversation
    if (action.action === 'open_automated_task' && action.params?.conversation_thread_id) {
      const convThreadId = action.params.conversation_thread_id as string;
      // Fetch the thread if not already in the list
      if (!threads.find(t => t.id === convThreadId)) {
        try {
          const res = await apiFetch(`/api/threads/${convThreadId}`);
          if (res.ok) {
            const full = await res.json();
            setThreads(prev => [full, ...prev]);
          }
        } catch { /* ignore */ }
      }
      setSelectedThreadId(convThreadId);
      setActivePage(null);
      return { ok: true };
    }

    // Handle report builder open client-side (no backend needed)
    if (action.action === 'open_report_builder' && action.params?.report_id) {
      setThreads(prev => prev.map(t => {
        if (t.id !== threadId) return t;
        const msgs = [...(t.conversation || [])];
        const reportBlock = {
          component: 'report_builder',
          data: { report_id: action.params.report_id, _ts: Date.now() },
          props: {},
        };
        // Remove any existing report_builder blocks first, then add the new one
        for (const msg of msgs) {
          if (msg.display_blocks) {
            msg.display_blocks = msg.display_blocks.filter(
              (b: { component: string }) => b.component !== 'report_builder'
            );
          }
        }
        // Add to the last assistant message's display_blocks
        const lastAssistant = [...msgs].reverse().find(m => m.role === 'assistant');
        if (lastAssistant) {
          lastAssistant.display_blocks = [...(lastAssistant.display_blocks || []), reportBlock];
        }
        return { ...t, conversation: msgs };
      }));
      return { ok: true };
    }

    try {
      const res = await apiFetch(`/api/threads/${threadId}/widget-action`, {
        method: 'POST',
        body: JSON.stringify(action),
      });
      if (!res.ok) {
        console.error('Widget action error:', res.status);
        return;
      }
      const result = await res.json();

      if (result.status === 'pending_approval') {
        // Re-fetch thread to show approval UI
        const threadRes = await apiFetch(`/api/threads/${threadId}`);
        if (threadRes.ok) {
          const updated = await threadRes.json();
          setThreads(prev => prev.map(t => t.id === threadId ? updated : t));
        }
      }

      return result;
    } catch (err) {
      console.error('Widget action failed:', err);
    }
  }, [threads]);

  const removeThread = useCallback(async (threadId: string) => {
    try {
      await apiFetch(`/api/threads/${threadId}`, { method: 'DELETE' });
    } catch (e) { console.error(e); }
    setThreads(prev => prev.filter(t => t.id !== threadId));
    if (selectedThreadId === threadId) setSelectedThreadId(null);
  }, [selectedThreadId]);

  const handleSelectThreadMobile = useCallback((id: string) => {
    setSelectedThreadId(id);
    setMobileView('detail');
  }, []);

  const handleSelectAgentMobile = useCallback((id: string) => {
    setActiveAgent(id);
    if (id === 'settings') {
      setMobileView('settings');
    } else {
      setMobileView('list');
    }
  }, []);

  const handleMobileBack = useCallback(() => {
    setMobileView('list');
    setSelectedThreadId(null);
    setActivePage(null);
  }, []);

  // Show nothing while checking auth
  if (!authChecked) return null;

  // Show login if not authenticated
  if (!token || !user) {
    return <LoginForm onSuccess={handleAuthSuccess} />;
  }

  // Mobile layout: single panel at a time with bottom tab bar
  if (isMobile) {
    const renderMobileContent = () => {
      if (mobileView === 'settings') {
        return (
          <div style={{ flex: 1, overflow: 'auto', paddingBottom: 0 }}>
            <SettingsPanel />
          </div>
        );
      }
      if (mobileView === 'detail' || mobileView === 'home' && selectedThread) {
        const content = activePage ? (() => {
          const pageConfig = FUNCTIONAL_PAGES.find(p => p.id === activePage);
          if (!pageConfig) return null;
          return <FunctionalPage config={pageConfig} thread={selectedThread} onSend={sendMessage} loading={loading} onWidgetAction={handleWidgetAction} activeVenueId={activeVenueId} />;
        })() : selectedThread ? (
          <ThreadDetail thread={selectedThread} onAction={handleAction} onWidgetAction={handleWidgetAction} onSend={sendMessage} loading={loading} openThread={openThread || null} />
        ) : (
          <HomePanel onSend={sendMessage} loading={loading} />
        );
        return (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', paddingBottom: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', padding: '0.5rem', borderBottom: '1px solid #e2ddd7' }}>
              <button onClick={handleMobileBack} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                minWidth: 44, minHeight: 44, border: 'none', borderRadius: 8,
                backgroundColor: 'transparent', cursor: 'pointer',
              }}>
                <ArrowLeft size={20} strokeWidth={1.75} />
              </button>
              <span style={{ fontSize: '0.9rem', fontWeight: 600, color: '#111' }}>
                {selectedThread?.title || activePage || 'Back'}
              </span>
            </div>
            <div style={{ flex: 1, overflow: 'auto' }}>{content}</div>
          </div>
        );
      }
      if (mobileView === 'list') {
        return (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', paddingBottom: 0 }}>
            <RoutingIndicator isVisible={routing} resolvedDomain={routingDomain} />
            <ThreadList
              threads={threads}
              selectedId={selectedThreadId}
              onSelectThread={handleSelectThreadMobile}
              onRemoveThread={removeThread}
              activeAgent={activeAgent}
              filter={filter}
              onFilterChange={setFilter}
              onNewChat={handleNewChat}
              onSelectPage={(pageId) => { handleSelectPage(pageId); setMobileView('detail'); }}
            />
          </div>
        );
      }
      // home
      return (
        <div style={{ flex: 1, overflow: 'auto', paddingBottom: 0 }}>
          <HomePanel onSend={sendMessage} loading={loading} />
        </div>
      );
    };

    return (
      <div className="full-height" style={{ display: 'flex', flexDirection: 'column', fontFamily: 'system-ui, sans-serif', overflow: 'hidden' }}>
        {quotaExceeded && (
          <QuotaExceededModal used={quotaExceeded.used} quota={quotaExceeded.quota} onClose={() => setQuotaExceeded(null)} onTopUp={() => { setQuotaExceeded(null); setActiveAgent('settings'); setMobileView('settings'); }} onUpgrade={() => { setQuotaExceeded(null); setActiveAgent('settings'); setMobileView('settings'); }} />
        )}
        {renderMobileContent()}
        <Sidebar selected={activeAgent} onSelect={handleSelectAgentMobile} threadCounts={threadCounts} user={user} onLogout={handleLogout} />
      </div>
    );
  }

  // Desktop layout: three-panel
  return (
    <div style={{ display: 'flex', height: '100vh', fontFamily: 'system-ui, sans-serif', overflow: 'hidden' }}>
      {quotaExceeded && (
        <QuotaExceededModal
          used={quotaExceeded.used}
          quota={quotaExceeded.quota}
          onClose={() => setQuotaExceeded(null)}
          onTopUp={() => { setQuotaExceeded(null); setActiveAgent('settings'); }}
          onUpgrade={() => { setQuotaExceeded(null); setActiveAgent('settings'); }}
        />
      )}
      {/* Left Sidebar */}
      <Sidebar
        selected={activeAgent}
        onSelect={setActiveAgent}
        threadCounts={threadCounts}
        user={user}
        onLogout={handleLogout}
      />

      {/* Center Panel */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        width: (panelCollapsed || activeAgent === 'settings') ? 0 : 360,
        minWidth: (panelCollapsed || activeAgent === 'settings') ? 0 : 360,
        borderRight: (panelCollapsed || activeAgent === 'settings') ? 'none' : '1px solid #e2ddd7',
        overflow: 'hidden',
        transition: 'width 0.2s ease, min-width 0.2s ease',
      }}>
        <RoutingIndicator isVisible={routing} resolvedDomain={routingDomain} />
        <ThreadList
          threads={threads}
          selectedId={selectedThreadId}
          onSelectThread={setSelectedThreadId}
          onRemoveThread={removeThread}
          activeAgent={activeAgent}
          filter={filter}
          onFilterChange={setFilter}
          onNewChat={handleNewChat}
          onCollapsePanel={() => setPanelCollapsed(true)}
          onSelectPage={handleSelectPage}
        />
      </div>

      {/* Right Panel */}
      <div style={{ flex: 1, minWidth: 0, position: 'relative' }}>
        {panelCollapsed && (
          <button
            onClick={() => setPanelCollapsed(false)}
            title="Show panel"
            style={{
              position: 'absolute', top: 12, left: 12, zIndex: 10,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              width: 32, height: 32,
              border: '1px solid #e2ddd7', borderRadius: 8,
              backgroundColor: '#faf8f5', cursor: 'pointer', color: '#999',
            }}
          >
            <PanelLeftIcon size={16} strokeWidth={1.75} />
          </button>
        )}
        {activeAgent === 'settings' ? (
          <SettingsPanel />
        ) : activePage ? (() => {
          const pageConfig = FUNCTIONAL_PAGES.find(p => p.id === activePage);
          if (!pageConfig) return null;
          return (
            <FunctionalPage
              config={pageConfig}
              thread={selectedThread}
              onSend={sendMessage}
              loading={loading}
              onWidgetAction={handleWidgetAction}
              activeVenueId={activeVenueId}
            />
          );
        })() : selectedThread ? (
          <ThreadDetail
            thread={selectedThread}
            onAction={handleAction}
            onWidgetAction={handleWidgetAction}
            onSend={sendMessage}
            loading={loading}
            openThread={openThread || null}
          />
        ) : (
          <HomePanel
            onSend={sendMessage}
            loading={loading}
          />
        )}
      </div>
    </div>
  );
}
