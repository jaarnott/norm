'use client';

import { useState, useMemo, useCallback, useEffect } from 'react';
import Sidebar from './components/layout/Sidebar';
import TaskList from './components/tasks/TaskList';
import TaskDetail from './components/tasks/TaskDetail';
import RoutingIndicator from './components/routing/RoutingIndicator';
import HomePanel from './components/home/HomePanel';
import SettingsPanel from './components/settings/SettingsPanel';
import LoginForm from './components/auth/LoginForm';
import { apiFetch, apiStream, getToken, setToken, clearToken, getStoredUser, setStoredUser } from './lib/api';
import { PanelLeft as PanelLeftIcon } from 'lucide-react';
import type { Task, WidgetAction } from './types';

type AuthUser = { id: string; email: string; full_name: string; role: string };

export default function Home() {
  const [token, setTokenState] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [input, setInput] = useState('');
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeAgent, setActiveAgent] = useState('home');
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'awaiting_approval' | 'awaiting_user_input' | 'completed'>('all');
  const [routing, setRouting] = useState(false);
  const [routingDomain, setRoutingDomain] = useState<string | null>(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

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

  // Load tasks when authenticated
  useEffect(() => {
    if (!token) return;
    apiFetch('/api/tasks')
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data?.tasks?.length) setTasks(data.tasks);
      })
      .catch(() => {});
  }, [token]);

  const handleAuthSuccess = useCallback((newToken: string, newUser: AuthUser) => {
    setToken(newToken);
    setStoredUser(newUser);
    setTokenState(newToken);
    setUser(newUser);
  }, []);

  const handleLogout = useCallback(() => {
    clearToken();
    setTokenState(null);
    setUser(null);
    setTasks([]);
    setSelectedTaskId(null);
    setActiveAgent('home');
  }, []);

  const selectedTask = tasks.find(t => t.id === selectedTaskId) || null;
  const openTask = tasks.find(t => t.status === 'awaiting_user_input');

  // Task counts per agent
  const taskCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    tasks.forEach(t => {
      counts[t.domain] = (counts[t.domain] || 0) + 1;
    });
    return counts;
  }, [tasks]);

  const sendMessage = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;

    const messageText = input;
    const taskIdForRequest = selectedTaskId;
    setInput('');
    setLoading(true);

    // Create an optimistic task so the conversation view appears immediately
    const optimisticId = `_pending_${Date.now()}`;
    if (!taskIdForRequest) {
      const optimistic: Task = {
        id: optimisticId,
        domain: 'unknown',
        intent: '',
        title: null,
        message: messageText,
        status: 'in_progress',
        created_at: new Date().toISOString(),
        conversation: [{ role: 'user', text: messageText }],
        thinking_steps: [],
      };
      setTasks(prev => [optimistic, ...prev]);
      setSelectedTaskId(optimisticId);
    } else {
      // Existing task — append user message optimistically
      setTasks(prev => prev.map(t =>
        t.id === taskIdForRequest
          ? { ...t, conversation: [...(t.conversation || []), { role: 'user', text: messageText }], thinking_steps: ['Working on it\u2026'] }
          : t
      ));
    }

    const currentId = taskIdForRequest || optimisticId;
    // realTaskId is the confirmed backend ID — used for recovery re-fetches.
    let realTaskId: string | null = taskIdForRequest;
    let streamErrored = false;
    let tokenBuffer = '';
    let streamMode: 'pending' | 'tool' | 'conversation' = 'pending';
    const TOOL_PREFIX = '[Tool] ';

    try {
      await apiStream(
        '/api/messages/stream',
        taskIdForRequest ? { message: messageText, task_id: taskIdForRequest } : { message: messageText },
        (event) => {
          if (event.type === 'task_created') {
            // Store the real task ID for recovery — but don't remap the
            // optimistic task yet to avoid a flash where selectedTask is null.
            realTaskId = event.task_id as string;
          } else if (event.type === 'routing') {
            setTasks(prev => prev.map(t =>
              t.id === currentId ? {
                ...t,
                domain: event.domain || t.domain,
                title: event.title || t.title,
                thinking_steps: [`I'll get the ${event.agent_label || event.domain} agent to look at this one…`],
              } : t
            ));
          } else if (event.type === 'stream_cancel') {
            // LLM called a tool but didn't use [Tool] prefix — clear any
            // streaming message that was accidentally shown in conversation.
            streamMode = 'pending';
            tokenBuffer = '';
            setTasks(prev => prev.map(t => {
              if (t.id !== currentId) return t;
              const conv = t.conversation || [];
              return { ...t, conversation: conv.filter(m => m.role !== 'streaming') };
            }));
          } else if (event.type === 'thinking') {
            // New LLM iteration starting — reset token buffer for next stream
            streamMode = 'pending';
            tokenBuffer = '';
            setTasks(prev => prev.map(t => {
              if (t.id !== currentId) return t;
              return {
                ...t,
                thinking_steps: [...(t.thinking_steps || []), event.text || ''],
              };
            }));
          } else if (event.type === 'token') {
            tokenBuffer += event.text || '';

            if (streamMode === 'pending') {
              if (tokenBuffer.startsWith(TOOL_PREFIX)) {
                // Detected [Tool] prefix — route to thinking steps
                streamMode = 'tool';
                const explanation = tokenBuffer.slice(TOOL_PREFIX.length);
                setTasks(prev => prev.map(t => {
                  if (t.id !== currentId) return t;
                  return {
                    ...t,
                    thinking_steps: [...(t.thinking_steps || []).filter(s => !s.startsWith('🔧 ')), '🔧 ' + explanation],
                    conversation: (t.conversation || []).filter(m => m.role !== 'streaming'),
                  };
                }));
              } else if (tokenBuffer.length >= TOOL_PREFIX.length) {
                // Enough chars to know it's NOT a tool prefix — it's the final answer
                streamMode = 'conversation';
                setTasks(prev => prev.map(t => {
                  if (t.id !== currentId) return t;
                  return {
                    ...t,
                    thinking_steps: [],
                    conversation: [...(t.conversation || []).filter(m => m.role !== 'streaming'),
                      { role: 'streaming' as const, text: tokenBuffer }],
                  };
                }));
              }
              // else: still accumulating, wait for more tokens
            } else if (streamMode === 'tool') {
              // More tokens for tool explanation — update the thinking step
              const explanation = tokenBuffer.slice(TOOL_PREFIX.length);
              setTasks(prev => prev.map(t => {
                if (t.id !== currentId) return t;
                return {
                  ...t,
                  thinking_steps: [...(t.thinking_steps || []).filter(s => !s.startsWith('🔧 ')), '🔧 ' + explanation],
                };
              }));
            } else {
              // conversation mode — append to streaming message
              setTasks(prev => prev.map(t => {
                if (t.id !== currentId) return t;
                const conv = t.conversation || [];
                const last = conv[conv.length - 1];
                if (last?.role === 'streaming') {
                  return { ...t, conversation: [...conv.slice(0, -1), { ...last, text: last.text + (event.text || '') }] };
                }
                return { ...t, conversation: [...conv, { role: 'streaming' as const, text: event.text || '' }] };
              }));
            }
          } else if (event.type === 'complete') {
            const data = event.data as Task;
            setTasks(prev => {
              const idx = prev.findIndex(t => t.id === currentId);
              if (idx >= 0) {
                const next = [...prev];
                next[idx] = data;
                return next;
              }
              return [data, ...prev];
            });
            setSelectedTaskId(data.id);
          } else if (event.type === 'error') {
            console.error('Stream error:', event.message);
            streamErrored = true;
          }
        },
      );

      // If the stream errored, try to recover by re-fetching the real task.
      // realTaskId is set from the task_created event, so this works for both
      // new conversations and follow-ups.
      if (streamErrored && realTaskId) {
        try {
          const res = await apiFetch(`/api/tasks/${realTaskId}`);
          if (res.ok) {
            const freshTask = await res.json();
            setTasks(prev => prev.map(t => t.id === currentId ? freshTask : t));
            setSelectedTaskId(freshTask.id);
            streamErrored = false;
          }
        } catch { /* re-fetch failed too */ }
      }

      if (streamErrored) {
        setTasks(prev => prev.map(t =>
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
      if (realTaskId) {
        try {
          const res = await apiFetch(`/api/tasks/${realTaskId}`);
          if (res.ok) {
            const freshTask = await res.json();
            setTasks(prev => prev.map(t => t.id === currentId ? freshTask : t));
            setSelectedTaskId(freshTask.id);
          }
        } catch { /* re-fetch failed too, user can retry manually */ }
      }
    } finally {
      setLoading(false);
    }
  }, [input, selectedTaskId]);

  const handleNewChat = useCallback(() => {
    setSelectedTaskId(null);
    setActiveAgent('home');
  }, []);

  const handleAction = useCallback(async (taskId: string, action: string) => {
    try {
      const res = await apiFetch(`/api/tasks/${taskId}/${action}`, {
        method: 'POST',
      });
      if (!res.ok) {
        console.error('Action error:', res.status, await res.text());
        return;
      }
      const updated = await res.json();
      setTasks(prev => prev.map(t => (t.id === taskId ? updated : t)));
    } catch (err) {
      console.error(`Failed to ${action}:`, err);
    }
  }, []);

  const handleWidgetAction = useCallback(async (taskId: string, action: WidgetAction): Promise<Record<string, unknown> | void> => {
    try {
      const res = await apiFetch(`/api/tasks/${taskId}/widget-action`, {
        method: 'POST',
        body: JSON.stringify(action),
      });
      if (!res.ok) {
        console.error('Widget action error:', res.status);
        return;
      }
      const result = await res.json();

      if (result.status === 'pending_approval') {
        // Re-fetch task to show approval UI
        const taskRes = await apiFetch(`/api/tasks/${taskId}`);
        if (taskRes.ok) {
          const updated = await taskRes.json();
          setTasks(prev => prev.map(t => t.id === taskId ? updated : t));
        }
      }

      return result;
    } catch (err) {
      console.error('Widget action failed:', err);
    }
  }, []);

  const removeTask = useCallback(async (taskId: string) => {
    try {
      await apiFetch(`/api/tasks/${taskId}`, { method: 'DELETE' });
    } catch {
      // still remove locally even if API fails
    }
    setTasks(prev => prev.filter(t => t.id !== taskId));
    if (selectedTaskId === taskId) setSelectedTaskId(null);
  }, [selectedTaskId]);

  // Show nothing while checking auth
  if (!authChecked) return null;

  // Show login if not authenticated
  if (!token || !user) {
    return <LoginForm onSuccess={handleAuthSuccess} />;
  }

  return (
    <div style={{ display: 'flex', height: '100vh', fontFamily: 'system-ui, sans-serif', overflow: 'hidden' }}>
      {/* Left Sidebar */}
      <Sidebar
        selected={activeAgent}
        onSelect={setActiveAgent}
        taskCounts={taskCounts}
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
        {/* Routing indicator */}
        <RoutingIndicator isVisible={routing} resolvedDomain={routingDomain} />

        {/* Task list */}
        <TaskList
          tasks={tasks}
          selectedId={selectedTaskId}
          onSelectTask={setSelectedTaskId}
          onRemoveTask={removeTask}
          activeAgent={activeAgent}
          filter={filter}
          onFilterChange={setFilter}
          onNewChat={handleNewChat}
          onCollapsePanel={() => setPanelCollapsed(true)}
        />
      </div>

      {/* Right Panel */}
      <div style={{ flex: 1, minWidth: 0, position: 'relative' }}>
        {panelCollapsed && (
          <button
            onClick={() => setPanelCollapsed(false)}
            title="Show panel"
            style={{
              position: 'absolute',
              top: 12,
              left: 12,
              zIndex: 10,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 32,
              height: 32,
              border: '1px solid #e2ddd7',
              borderRadius: 8,
              backgroundColor: '#faf8f5',
              cursor: 'pointer',
              color: '#999',
            }}
          >
            <PanelLeftIcon size={16} strokeWidth={1.75} />
          </button>
        )}
        {activeAgent === 'settings' ? (
          <SettingsPanel />
        ) : selectedTask ? (
          <TaskDetail
            task={selectedTask}
            onAction={handleAction}
            onWidgetAction={handleWidgetAction}
            input={input}
            onInputChange={setInput}
            onSend={sendMessage}
            loading={loading}
            openTask={openTask || null}
          />
        ) : (
          <HomePanel
            input={input}
            onInputChange={setInput}
            onSend={sendMessage}
            loading={loading}
          />
        )}
      </div>
    </div>
  );
}
