'use client';

import type { Task } from '../../types';
import TaskCard from './TaskCard';
import { SquarePen, Search, PanelLeftClose } from 'lucide-react';
import { FUNCTIONAL_PAGES } from '../pages/pageRegistry';

type FilterKey = 'all' | 'awaiting_approval' | 'awaiting_user_input' | 'completed';

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'awaiting_approval', label: 'Awaiting approval' },
  { key: 'awaiting_user_input', label: 'Needs input' },
  { key: 'completed', label: 'Completed' },
];

function applyFilter(tasks: Task[], filter: FilterKey): Task[] {
  if (filter === 'all') return tasks;
  if (filter === 'completed') return tasks.filter(t => t.status === 'submitted' || t.status === 'rejected');
  return tasks.filter(t => t.status === filter || (filter === 'awaiting_user_input' && t.status === 'needs_clarification'));
}

interface TaskListProps {
  tasks: Task[];
  selectedId: string | null;
  onSelectTask: (id: string) => void;
  onRemoveTask: (id: string) => void;
  activeAgent: string;
  filter: FilterKey;
  onFilterChange: (filter: FilterKey) => void;
  onNewChat: () => void;
  onCollapsePanel?: () => void;
  onSelectPage?: (pageId: string) => void;
}

export default function TaskList({ tasks, selectedId, onSelectTask, onRemoveTask, activeAgent, filter, onFilterChange, onNewChat, onCollapsePanel, onSelectPage }: TaskListProps) {
  // Filter by agent
  const agentFiltered = activeAgent === 'home' ? tasks : tasks.filter(t => t.domain === activeAgent);
  // Apply status filter
  const filtered = applyFilter(agentFiltered, filter);

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: '#faf8f5',
      overflow: 'hidden',
    }}>
      <style>{`
        .task-list-scroll {
          scrollbar-width: none;
        }
        .task-list-scroll:hover {
          scrollbar-width: thin;
          scrollbar-color: #ddd transparent;
        }
        .task-list-scroll::-webkit-scrollbar {
          width: 6px;
        }
        .task-list-scroll::-webkit-scrollbar-thumb {
          background: transparent;
          border-radius: 3px;
        }
        .task-list-scroll:hover::-webkit-scrollbar-thumb {
          background: #ddd;
        }
      `}</style>
      {/* Header */}
      <div style={{
        padding: '1rem 1rem 0.6rem',
        borderBottom: '1px solid #f0f0f0',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.15rem' }}>
          <div style={{ fontSize: '1.05rem', fontWeight: 700, color: '#111' }}>
            Norm
          </div>
          {onCollapsePanel && (
            <button
              onClick={onCollapsePanel}
              title="Hide panel"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 28,
                height: 28,
                border: 'none',
                borderRadius: 6,
                backgroundColor: 'transparent',
                cursor: 'pointer',
                color: '#999',
              }}
            >
              <PanelLeftClose size={16} strokeWidth={1.75} />
            </button>
          )}
        </div>
        <div style={{ fontSize: '0.78rem', color: '#999', marginBottom: '0.6rem' }}>
          AI Operations Control
        </div>
        <div style={{ marginTop: '1rem', marginBottom: '1rem' }}>
        <button
          onClick={onNewChat}
          style={{
            width: '100%',
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
            padding: '0.45rem 0',
            marginBottom: 0,
            fontSize: '0.95rem',
            fontWeight: 600,
            color: '#1a1a1a',
            backgroundColor: 'transparent',
            border: 'none',
            borderRadius: 8,
            cursor: 'pointer',
            fontFamily: 'inherit',
            textAlign: 'left',
          }}
        >
          <SquarePen size={20} strokeWidth={1.75} /> New chat
        </button>
        <button
          style={{
            width: '100%',
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
            padding: '0.45rem 0',
            marginBottom: 0,
            fontSize: '0.95rem',
            fontWeight: 600,
            color: '#1a1a1a',
            backgroundColor: 'transparent',
            border: 'none',
            borderRadius: 8,
            cursor: 'pointer',
            fontFamily: 'inherit',
            textAlign: 'left',
          }}
        >
          <Search size={20} strokeWidth={1.75} /> Search
        </button>
        {FUNCTIONAL_PAGES.filter(p => p.agent === activeAgent).map((page, idx) => {
          const Icon = page.icon;
          return (
            <button
              key={page.id}
              onClick={() => onSelectPage?.(page.id)}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                padding: '0.45rem 0',
                marginTop: idx === 0 ? '0.5rem' : 0,
                marginBottom: 0,
                fontSize: '0.95rem',
                fontWeight: 600,
                color: '#1a1a1a',
                backgroundColor: 'transparent',
                border: 'none',
                borderRadius: 8,
                cursor: 'pointer',
                fontFamily: 'inherit',
                textAlign: 'left',
              }}
            >
              <Icon size={20} strokeWidth={1.75} /> {page.label}
            </button>
          );
        })}
        </div>
        <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#333', marginBottom: '0.5rem' }}>
          {activeAgent === 'home' ? 'Recent threads' : `${activeAgent.charAt(0).toUpperCase() + activeAgent.slice(1)} Tasks`}
        </div>
        {/* Filters */}
        <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
          {FILTERS.map(f => (
            <button
              key={f.key}
              onClick={() => onFilterChange(f.key)}
              style={{
                fontSize: '0.75rem',
                fontWeight: filter === f.key ? 600 : 400,
                padding: '0.25rem 0.6rem',
                borderRadius: 12,
                border: filter === f.key ? '1px solid #c4a882' : '1px solid #e0e0e0',
                backgroundColor: filter === f.key ? '#f5f0ea' : 'transparent',
                color: filter === f.key ? '#c4a882' : '#666',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Task list */}
      <div className="task-list-scroll" style={{ flex: 1, overflowY: 'auto' }}>
        {filtered.length === 0 ? (
          <div style={{
            padding: '3rem 1.5rem',
            textAlign: 'center',
            color: '#bbb',
            fontSize: '0.85rem',
            lineHeight: 1.6,
          }}>
            No tasks yet. Try asking me to order stock, check a roster, or generate a report.
          </div>
        ) : (
          filtered.map(task => (
            <TaskCard
              key={task.id}
              task={task}
              isSelected={selectedId === task.id}
              onClick={() => onSelectTask(task.id)}
              onRemove={() => onRemoveTask(task.id)}
              compact={activeAgent === 'home'}
            />
          ))
        )}
      </div>
    </div>
  );
}
