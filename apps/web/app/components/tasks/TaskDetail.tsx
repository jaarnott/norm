'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { Package, UserRound, BarChart3, HelpCircle, type LucideIcon } from 'lucide-react';
import type { Task, ProcurementTask, HrTask, ConversationMessage } from '../../types';
import ActivityTimeline from './ActivityTimeline';
import { colors } from '../../lib/theme';

const DOMAIN_ICONS: Record<string, LucideIcon> = {
  procurement: Package,
  hr: UserRound,
  reports: BarChart3,
};

function getDomainColor(domain: string): string {
  return (colors as Record<string, any>)[domain] || colors.unknown;
}

const STATUS_STYLES: Record<string, { bg: string; color: string }> = {
  awaiting_approval: { bg: '#fff3cd', color: '#856404' },
  awaiting_user_input: { bg: '#f5f0ea', color: '#8a7356' },
  needs_clarification: { bg: '#f8d7da', color: '#721c24' },
  needs_information: { bg: '#f8d7da', color: '#721c24' },
  approved: { bg: '#d4edda', color: '#155724' },
  rejected: { bg: '#e2e3e5', color: '#383d41' },
  submitted: { bg: '#cce5ff', color: '#004085' },
};

const ss = (s: string) => STATUS_STYLES[s] || { bg: '#e2e3e5', color: '#383d41' };

function getTaskTitle(task: Task): string {
  if (task.domain === 'procurement') return 'Draft stock order';
  if (task.domain === 'hr') return 'Employee setup';
  return task.intent || 'Task';
}

// -- Tab types --

type TabKey = 'conversation' | 'details' | 'activity';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'conversation', label: 'Conversation' },
  { key: 'details', label: 'Details' },
  { key: 'activity', label: 'Activity' },
];

// -- Chat conversation view --

function ConversationView({ messages }: { messages: ConversationMessage[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length]);

  if (!messages || messages.length === 0) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: '#bbb', fontSize: '0.85rem' }}>
        No messages yet.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
      {messages.map((m, i) => {
        const isUser = m.role === 'user';
        return (
          <div
            key={i}
            style={{
              display: 'flex',
              justifyContent: isUser ? 'flex-end' : 'flex-start',
            }}
          >
            <div style={{
              maxWidth: '80%',
              padding: '0.75rem 1rem',
              borderRadius: isUser ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
              backgroundColor: isUser ? '#f0f0f0' : 'transparent',
              color: '#333',
              fontSize: '1rem',
              lineHeight: 1.6,
              wordBreak: 'break-word',
            }}>
              {m.text}
            </div>
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}

// -- Detail components --

const DetailRow = ({ label, value }: { label: string; value: string }) => (
  <div style={{ display: 'flex', padding: '0.35rem 0', borderBottom: '1px solid #f5f5f5' }}>
    <span style={{ width: 120, fontSize: '0.78rem', color: '#888', flexShrink: 0 }}>{label}</span>
    <span style={{ fontSize: '0.78rem', fontWeight: 500, color: '#222' }}>{value}</span>
  </div>
);

const Btn = ({ label, bg, onClick }: { label: string; bg: string; onClick: () => void }) => (
  <button onClick={onClick} style={{
    padding: '0.5rem 1.2rem', fontSize: '0.8rem', fontWeight: 600,
    backgroundColor: bg, color: '#fff', border: 'none', borderRadius: 6,
    cursor: 'pointer',
  }}>
    {label}
  </button>
);

function ProcurementDetails({ task }: { task: ProcurementTask }) {
  return (
    <div style={{ marginBottom: '1rem' }}>
      <div style={{
        fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase',
        letterSpacing: '0.06em', color: '#999', marginBottom: '0.5rem',
      }}>
        Order Details
      </div>
      <div style={{
        border: '1px solid #eee', borderRadius: 8,
        padding: '0.75rem', backgroundColor: '#fafafa',
      }}>
        <DetailRow label="Product" value={task.product?.name || '?'} />
        <DetailRow label="Quantity" value={`${task.quantity ?? '?'} ${task.product?.unit ?? 'case'}(s)`} />
        <DetailRow label="Venue" value={task.venue?.name || '?'} />
        {task.supplier && <DetailRow label="Supplier" value={task.supplier} />}
      </div>
    </div>
  );
}

function HrDetails({ task }: { task: HrTask }) {
  return (
    <div style={{ marginBottom: '1rem' }}>
      <div style={{
        fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase',
        letterSpacing: '0.06em', color: '#999', marginBottom: '0.5rem',
      }}>
        Employee Details
      </div>
      <div style={{
        border: '1px solid #eee', borderRadius: 8,
        padding: '0.75rem', backgroundColor: '#fafafa', marginBottom: '0.75rem',
      }}>
        <DetailRow label="Name" value={task.employee_name || '?'} />
        <DetailRow label="Role" value={task.role || '?'} />
        <DetailRow label="Venue" value={task.venue?.name || '?'} />
        <DetailRow label="Start date" value={task.start_date || '?'} />
      </div>
      {task.checklist && task.checklist.length > 0 && (
        <div>
          <div style={{
            fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase',
            letterSpacing: '0.06em', color: '#999', marginBottom: '0.4rem',
          }}>
            Onboarding Checklist
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.15rem 1rem' }}>
            {task.checklist.map((c) => (
              <span key={c.item} style={{ fontSize: '0.78rem', color: c.done ? '#28a745' : '#bbb' }}>
                {c.done ? '\u2713' : '\u2500'} {c.item}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DetailsView({ task, onAction }: { task: Task; onAction: (taskId: string, action: string) => void }) {
  const isProcurement = task.domain === 'procurement';
  const isHr = task.domain === 'hr';
  const isTerminal = task.status === 'submitted' || task.status === 'rejected';

  return (
    <div>
      {isProcurement && <ProcurementDetails task={task as ProcurementTask} />}
      {isHr && <HrDetails task={task as HrTask} />}

      {/* Status */}
      <div style={{ marginBottom: '1rem' }}>
        <div style={{
          fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase',
          letterSpacing: '0.06em', color: '#999', marginBottom: '0.5rem',
        }}>
          Status
        </div>
        <div style={{
          border: '1px solid #eee', borderRadius: 8,
          padding: '0.75rem', backgroundColor: '#fafafa',
        }}>
          <DetailRow label="Status" value={task.status.replace(/_/g, ' ')} />
          <DetailRow label="Domain" value={task.domain} />
          <DetailRow label="Created" value={new Date(task.created_at).toLocaleString()} />
        </div>
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        {task.status === 'awaiting_approval' && (
          <>
            <Btn label="Approve" bg="#28a745" onClick={() => onAction(task.id, 'approve')} />
            <Btn label="Reject" bg="#dc3545" onClick={() => onAction(task.id, 'reject')} />
          </>
        )}
        {task.status === 'approved' && (
          <Btn
            label={isProcurement ? 'Submit to Supplier' : 'Submit Setup'}
            bg="#4d65ff"
            onClick={() => onAction(task.id, 'submit')}
          />
        )}
        {isTerminal && (
          <span style={{ fontSize: '0.8rem', color: '#888', fontStyle: 'italic' }}>
            {task.status === 'submitted'
              ? (isProcurement ? 'Order sent to supplier' : 'Employee setup submitted')
              : 'Rejected'}
          </span>
        )}
      </div>
    </div>
  );
}

// -- Main component --

interface TaskDetailProps {
  task: Task;
  onAction: (taskId: string, action: string) => void;
  input: string;
  onInputChange: (value: string) => void;
  onSend: (e: React.FormEvent) => void;
  loading: boolean;
  openTask: Task | null;
}

export default function TaskDetail({ task, onAction, input, onInputChange, onSend, loading, openTask }: TaskDetailProps) {
  const [activeTab, setActiveTab] = useState<TabKey>('conversation');
  const dc = getDomainColor(task.domain);
  const DomainIcon = DOMAIN_ICONS[task.domain] || HelpCircle;
  const stl = ss(task.status);
  const isProcurement = task.domain === 'procurement';
  const isHr = task.domain === 'hr';
  const isTerminal = task.status === 'submitted' || task.status === 'rejected';

  return (
    <div style={{
      height: '100vh',
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: '#fff',
    }}>
      {/* Header */}
      <div style={{
        padding: '1rem 1.5rem 0',
        borderBottom: '1px solid #eee',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem' }}>
          <DomainIcon size={18} strokeWidth={1.75} style={{ color: dc }} />
          <span style={{
            fontSize: '0.72rem', fontWeight: 600, color: dc,
            textTransform: 'uppercase', letterSpacing: '0.04em',
          }}>
            {task.domain}
          </span>
          <span style={{
            fontSize: '0.65rem', fontWeight: 600,
            padding: '0.15rem 0.5rem', borderRadius: 10,
            backgroundColor: stl.bg, color: stl.color,
            textTransform: 'capitalize', marginLeft: 'auto',
          }}>
            {task.status.replace(/_/g, ' ')}
          </span>
        </div>
        <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#111', marginBottom: '0.6rem' }}>
          {getTaskTitle(task)}
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 0 }}>
          {TABS.map(tab => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              style={{
                padding: '0.45rem 1rem',
                fontSize: '0.78rem',
                fontWeight: activeTab === tab.key ? 600 : 400,
                color: activeTab === tab.key ? '#111' : '#999',
                backgroundColor: 'transparent',
                border: 'none',
                borderBottom: activeTab === tab.key ? '2px solid #111' : '2px solid transparent',
                cursor: 'pointer',
                fontFamily: 'inherit',
                marginBottom: -1,
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '1.25rem 1.5rem',
      }}>
        {activeTab === 'conversation' && (
          <>
            <ConversationView messages={task.conversation || []} />
            {/* Summary card + action buttons in conversation tab */}
            {(task.status === 'awaiting_approval' || task.status === 'approved') && (
              <div style={{
                marginTop: '1rem',
                border: '1px solid #eee',
                borderRadius: 8,
                padding: '0.85rem',
                backgroundColor: '#fafafa',
              }}>
                {isProcurement && (() => {
                  const t = task as ProcurementTask;
                  return (
                    <>
                      <DetailRow label="Product" value={t.product?.name || '?'} />
                      <DetailRow label="Quantity" value={`${t.quantity ?? '?'} ${t.product?.unit ?? 'case'}(s)`} />
                      <DetailRow label="Venue" value={t.venue?.name || '?'} />
                      {t.supplier && <DetailRow label="Supplier" value={t.supplier} />}
                    </>
                  );
                })()}
                {isHr && (() => {
                  const t = task as HrTask;
                  return (
                    <>
                      <DetailRow label="Name" value={t.employee_name || '?'} />
                      <DetailRow label="Role" value={t.role || '?'} />
                      <DetailRow label="Venue" value={t.venue?.name || '?'} />
                      <DetailRow label="Start date" value={t.start_date || '?'} />
                    </>
                  );
                })()}
                <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
                  {task.status === 'awaiting_approval' && (
                    <>
                      <Btn label="Approve" bg="#28a745" onClick={() => onAction(task.id, 'approve')} />
                      <Btn label="Reject" bg="#dc3545" onClick={() => onAction(task.id, 'reject')} />
                    </>
                  )}
                  {task.status === 'approved' && (
                    <Btn
                      label={isProcurement ? 'Submit to Supplier' : 'Submit Setup'}
                      bg="#4d65ff"
                      onClick={() => onAction(task.id, 'submit')}
                    />
                  )}
                </div>
              </div>
            )}
            {isTerminal && (
              <div style={{
                marginTop: '1rem',
                border: '1px solid #eee',
                borderRadius: 8,
                padding: '0.85rem',
                backgroundColor: '#fafafa',
              }}>
                {isProcurement && (() => {
                  const t = task as ProcurementTask;
                  return (
                    <>
                      <DetailRow label="Product" value={t.product?.name || '?'} />
                      <DetailRow label="Quantity" value={`${t.quantity ?? '?'} ${t.product?.unit ?? 'case'}(s)`} />
                      <DetailRow label="Venue" value={t.venue?.name || '?'} />
                      {t.supplier && <DetailRow label="Supplier" value={t.supplier} />}
                    </>
                  );
                })()}
                {isHr && (() => {
                  const t = task as HrTask;
                  return (
                    <>
                      <DetailRow label="Name" value={t.employee_name || '?'} />
                      <DetailRow label="Role" value={t.role || '?'} />
                      <DetailRow label="Venue" value={t.venue?.name || '?'} />
                      <DetailRow label="Start date" value={t.start_date || '?'} />
                    </>
                  );
                })()}
                {task.status === 'submitted' && task.integration_run ? (
                  <div style={{ marginTop: '0.75rem', borderTop: '1px solid #eee', paddingTop: '0.75rem' }}>
                    <div style={{
                      fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase',
                      letterSpacing: '0.06em', color: task.integration_run.status === 'success' ? '#28a745' : '#dc3545',
                      marginBottom: '0.4rem',
                    }}>
                      {task.integration_run.status === 'success' ? 'Submitted successfully' : 'Submission failed'}
                    </div>
                    {task.integration_run.reference && (
                      <DetailRow label="Reference" value={task.integration_run.reference} />
                    )}
                    <DetailRow label="Connector" value={task.integration_run.connector === 'mock_supplier' ? 'Bidfood' : task.integration_run.connector === 'mock_hr' ? 'HR System' : task.integration_run.connector} />
                    {task.integration_run.submitted_at && (
                      <DetailRow label="Submitted" value={new Date(task.integration_run.submitted_at).toLocaleString()} />
                    )}
                    {task.integration_run.error && (
                      <DetailRow label="Error" value={task.integration_run.error} />
                    )}
                    {task.approval && (
                      <>
                        <DetailRow label="Approved by" value={task.approval.performed_by} />
                        <DetailRow label="Approved at" value={new Date(task.approval.performed_at).toLocaleString()} />
                      </>
                    )}
                  </div>
                ) : (
                  <div style={{ marginTop: '0.6rem', fontSize: '0.8rem', color: task.status === 'submitted' ? '#28a745' : '#888', fontStyle: 'italic' }}>
                    {task.status === 'submitted'
                      ? (isProcurement ? 'Order sent to supplier' : 'Employee setup submitted')
                      : 'Rejected'}
                  </div>
                )}
                {task.status === 'rejected' && task.approval && (
                  <div style={{ marginTop: '0.5rem' }}>
                    <DetailRow label="Rejected by" value={task.approval.performed_by} />
                    <DetailRow label="Rejected at" value={new Date(task.approval.performed_at).toLocaleString()} />
                  </div>
                )}
              </div>
            )}
            {task.integration_run?.status === 'failed' && task.status === 'approved' && (
              <div style={{
                marginTop: '1rem',
                border: '1px solid #f5c6cb',
                borderRadius: 8,
                padding: '0.85rem',
                backgroundColor: '#fff5f5',
              }}>
                <div style={{ fontSize: '0.8rem', color: '#dc3545', marginBottom: '0.5rem' }}>
                  Submission failed: {task.integration_run.error || 'Unknown error'}
                </div>
                <Btn label="Retry" bg="#dc3545" onClick={() => onAction(task.id, 'submit')} />
              </div>
            )}
          </>
        )}

        {activeTab === 'details' && (
          <DetailsView task={task} onAction={onAction} />
        )}

        {activeTab === 'activity' && (
          <ActivityTimeline
            messages={task.conversation || []}
            createdAt={task.created_at}
            domain={task.domain}
            llmCalls={task.llm_calls}
            approval={task.approval}
            integrationRun={task.integration_run}
          />
        )}
      </div>

      {/* Input at bottom */}
      <div style={{
        padding: '12px 24px 24px',
      }}>
        <form onSubmit={onSend} style={{ display: 'flex', alignItems: 'flex-end', gap: '0.4rem' }}>
          <textarea
            value={input}
            onChange={e => {
              onInputChange(e.target.value);
              const el = e.target;
              el.style.height = 'auto';
              el.style.height = Math.min(el.scrollHeight, 150) + 'px';
            }}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                onSend(e as unknown as React.FormEvent);
              }
            }}
            placeholder={
              openTask?.clarification_question
                ? `Reply: ${openTask.clarification_question}`
                : 'Message Norm...'
            }
            rows={1}
            style={{
              flex: 1,
              minHeight: 50,
              maxHeight: 150,
              padding: '14px 0.8rem',
              fontSize: '0.85rem',
              border: openTask ? '1px solid #c4a882' : '1px solid #ddd',
              borderRadius: 6,
              outline: 'none',
              fontFamily: 'inherit',
              resize: 'none',
              lineHeight: '1.4',
              boxSizing: 'border-box',
            }}
          />
          <button
            type="submit"
            disabled={loading}
            style={{
              height: 50,
              padding: '0 1rem',
              fontSize: '0.8rem',
              fontWeight: 600,
              backgroundColor: '#111',
              color: '#fff',
              border: 'none',
              borderRadius: 6,
              cursor: loading ? 'not-allowed' : 'pointer',
              fontFamily: 'inherit',
            }}
          >
            {loading ? '...' : 'Send'}
          </button>
        </form>
      </div>
    </div>
  );
}
