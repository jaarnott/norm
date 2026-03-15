'use client';

import { useState, useMemo, useCallback, useEffect } from 'react';
import Sidebar from './components/layout/Sidebar';
import TaskList from './components/tasks/TaskList';
import TaskDetail from './components/tasks/TaskDetail';
import RoutingIndicator from './components/routing/RoutingIndicator';
import HomePanel from './components/home/HomePanel';
import SettingsPanel from './components/settings/SettingsPanel';
import LoginForm from './components/auth/LoginForm';
import { apiFetch, getToken, setToken, clearToken, getStoredUser, setStoredUser } from './lib/api';
import { PanelLeft as PanelLeftIcon } from 'lucide-react';
import type { Task } from './types';

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
    setLoading(true);
    setRouting(true);
    setRoutingDomain(null);
    try {
      const res = await apiFetch('/api/messages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(selectedTaskId ? { message: input, task_id: selectedTaskId } : { message: input }),
      });
      if (!res.ok) {
        const text = await res.text();
        console.error('API error:', res.status, text);
        alert(`API error (${res.status}). Is the backend running on port 8000?`);
        return;
      }
      const data: Task = await res.json();
      setRoutingDomain(data.domain);

      // Brief delay so the routing indicator is visible
      await new Promise(r => setTimeout(r, 600));

      setTasks(prev => {
        const idx = prev.findIndex(t => t.id === data.id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = data;
          return next;
        }
        return [data, ...prev];
      });
      setSelectedTaskId(data.id);
      setInput('');
    } catch (err) {
      console.error('Failed to send message:', err);
    } finally {
      setLoading(false);
      setRouting(false);
      setRoutingDomain(null);
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
        width: panelCollapsed ? 0 : 360,
        minWidth: panelCollapsed ? 0 : 360,
        borderRight: panelCollapsed ? 'none' : '1px solid #e2ddd7',
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
