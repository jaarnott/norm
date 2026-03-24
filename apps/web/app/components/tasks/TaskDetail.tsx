'use client';

import { useState, useRef, useEffect, memo } from 'react';
import { Package, UserRound, BarChart3, HelpCircle, type LucideIcon } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Task, ProcurementTask, HrTask, ConversationMessage, ToolCallRecord, DisplayBlock, WidgetAction } from '../../types';
import ActivityTimeline from './ActivityTimeline';
import DisplayBlockRenderer, { FULL_WIDTH_COMPONENTS } from '../display/DisplayBlockRenderer';
import SplitDragHandle from '../layout/SplitDragHandle';
import { useSplitPane } from '../../hooks/useSplitPane';
import { colors } from '../../lib/theme';

const DOMAIN_ICONS: Record<string, LucideIcon> = {
  procurement: Package,
  hr: UserRound,
  reports: BarChart3,
};

function getDomainColor(domain: string): string {
  return (colors as unknown as Record<string, string>)[domain] || colors.unknown;
}

const STATUS_STYLES: Record<string, { bg: string; color: string }> = {
  awaiting_approval: { bg: '#fff3cd', color: '#856404' },
  awaiting_tool_approval: { bg: '#e8daef', color: '#6c3483' },
  awaiting_user_input: { bg: '#f5f0ea', color: '#8a7356' },
  needs_clarification: { bg: '#f8d7da', color: '#721c24' },
  needs_information: { bg: '#f8d7da', color: '#721c24' },
  in_progress: { bg: '#d1ecf1', color: '#0c5460' },
  completed: { bg: '#d4edda', color: '#155724' },
  approved: { bg: '#d4edda', color: '#155724' },
  rejected: { bg: '#e2e3e5', color: '#383d41' },
  submitted: { bg: '#cce5ff', color: '#004085' },
};

const ss = (s: string) => STATUS_STYLES[s] || { bg: '#e2e3e5', color: '#383d41' };

function getTaskTitle(task: Task): string {
  return task.title || '';
}

// -- Tab types --

type TabKey = 'conversation' | 'details' | 'activity';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'conversation', label: 'Conversation' },
  { key: 'details', label: 'Details' },
  { key: 'activity', label: 'Activity' },
];

// -- Thinking steps (intermediate LLM reasoning during tool loop) --

function ThinkingSteps({ steps, isStreaming }: { steps: string[]; isStreaming: boolean }) {
  const [userCollapsed, setUserCollapsed] = useState(false);

  if (!steps || steps.length === 0) return null;

  // Show expanded while streaming; once done, allow collapse
  const showSteps = isStreaming || !userCollapsed;

  return (
    <div style={{
      margin: '0.5rem 0',
      borderLeft: '2px solid #e0e0e0',
      paddingLeft: '0.75rem',
    }}>
      <button
        onClick={() => { if (!isStreaming) setUserCollapsed(!userCollapsed); }}
        style={{
          background: 'none',
          border: 'none',
          cursor: isStreaming ? 'default' : 'pointer',
          fontSize: '0.75rem',
          color: '#999',
          fontFamily: 'inherit',
          padding: '0.25rem 0',
          display: 'flex',
          alignItems: 'center',
          gap: '0.35rem',
        }}
      >
        {isStreaming ? (
          <span className="thinking-dot" style={{ fontSize: '0.65rem' }}>&#9679;</span>
        ) : (
          <span style={{
            display: 'inline-block',
            transition: 'transform 0.15s',
            transform: showSteps ? 'rotate(90deg)' : 'rotate(0deg)',
            fontSize: '0.65rem',
          }}>
            &#9654;
          </span>
        )}
        {isStreaming ? 'Working...' : `${steps.length} reasoning step${steps.length > 1 ? 's' : ''}`}
      </button>
      {showSteps && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginTop: '0.3rem' }}>
          {steps.map((step, i) => (
            <div key={i} style={{
              fontSize: '0.8rem',
              color: '#888',
              lineHeight: 1.5,
              fontStyle: 'italic',
            }}>
              {step}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// -- Chat conversation view --

export const ConversationView = memo(function ConversationView({ messages, onWidgetAction, taskId, hideFullWidthBlocks }: {
  messages: ConversationMessage[];
  onWidgetAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  taskId?: string;
  hideFullWidthBlocks?: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  const lastMessageText = messages[messages.length - 1]?.text;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, lastMessageText]);

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
        const hasDisplayBlocks = !isUser && m.display_blocks && m.display_blocks.length > 0;
        const hasTable = !isUser && /\|.+\|/.test(m.text);
        const wideContent = hasDisplayBlocks || hasTable;
        return (
          <div
            key={i}
            style={{
              maxWidth: hasDisplayBlocks ? 950 : 768,
              margin: '0 auto',
              width: '100%',
              display: 'flex',
              justifyContent: isUser ? 'flex-end' : 'flex-start',
            }}
          >
            <div style={{
              maxWidth: isUser ? '80%' : wideContent ? '100%' : '90%',
              padding: '0.75rem 1rem',
              borderRadius: isUser ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
              backgroundColor: isUser ? '#f5f0ea' : 'transparent',
              color: '#333',
              fontSize: '1rem',
              lineHeight: 1.6,
              wordBreak: 'break-word',
              whiteSpace: isUser ? 'pre-wrap' : undefined,
            }}>
              {!isUser && m.display_blocks && m.display_blocks.length > 0 && (() => {
                const blocks = hideFullWidthBlocks
                  ? m.display_blocks.filter(b => !FULL_WIDTH_COMPONENTS.has(b.component))
                  : m.display_blocks;
                if (blocks.length === 0) return null;
                return (
                  <div style={{ marginBottom: '0.5rem' }}>
                    {blocks.map((block: DisplayBlock, bi: number) => (
                      <DisplayBlockRenderer key={bi} block={block} onAction={onWidgetAction} taskId={taskId} />
                    ))}
                  </div>
                );
              })()}
              {isUser ? m.text : (
                <div className="markdown-message">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
});

// -- Detail components --

const DetailRow = ({ label, value }: { label: string; value: string }) => (
  <div style={{ display: 'flex', padding: '0.35rem 0', borderBottom: '1px solid #f5f5f5' }}>
    <span style={{ width: 120, fontSize: '0.78rem', color: '#888', flexShrink: 0 }}>{label}</span>
    <span style={{ fontSize: '0.78rem', fontWeight: 500, color: '#222' }}>{value}</span>
  </div>
);

const Btn = ({ label, bg, onClick, 'data-testid': testId }: { label: string; bg: string; onClick: () => void; 'data-testid'?: string }) => (
  <button data-testid={testId} onClick={onClick} style={{
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
            <Btn data-testid="approve-btn" label="Approve" bg="#28a745" onClick={() => onAction(task.id, 'approve')} />
            <Btn data-testid="reject-btn" label="Reject" bg="#dc3545" onClick={() => onAction(task.id, 'reject')} />
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

// -- Tool call history with expandable details --

function ToolCallHistory({ toolCalls }: { toolCalls: ToolCallRecord[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  return (
    <div style={{ marginTop: '0.75rem', padding: '0.5rem 0' }}>
      {toolCalls.map(tc => {
        const isFailed = tc.status === 'failed';
        const isExpanded = expandedId === tc.id;
        return (
          <div key={tc.id} style={{ marginBottom: '0.4rem' }}>
            <div
              onClick={() => setExpandedId(isExpanded ? null : tc.id)}
              style={{
                padding: '0.4rem 0.75rem',
                borderRadius: 8,
                backgroundColor: isFailed ? '#fff5f5' : '#f0f7ff',
                border: `1px solid ${isFailed ? '#f5c6cb' : '#d4e5f7'}`,
                fontSize: '0.75rem',
                color: '#555',
                maxWidth: '80%',
                cursor: 'pointer',
                display: 'inline-block',
              }}
            >
              <span style={{ fontWeight: 600, color: isFailed ? '#c53030' : '#004085' }}>{tc.action}</span>
              <span style={{ color: '#888', marginLeft: 4 }}>({tc.connector_name})</span>
              {tc.duration_ms != null && (
                <span style={{ color: '#aaa', marginLeft: 4 }}>{tc.duration_ms}ms</span>
              )}
              {isFailed && (
                <span style={{
                  fontSize: '0.65rem', fontWeight: 600, color: '#c53030',
                  backgroundColor: '#fed7d7', padding: '1px 5px', borderRadius: 3, marginLeft: 6,
                }}>FAILED</span>
              )}
              <span style={{
                display: 'inline-block', marginLeft: 6, fontSize: '0.6rem', color: '#aaa',
                transition: 'transform 0.15s', transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
              }}>&#9654;</span>
            </div>
            {isExpanded && (
              <div style={{
                marginTop: '0.3rem',
                padding: '0.6rem 0.75rem',
                borderRadius: 6,
                backgroundColor: '#fafafa',
                border: '1px solid #eee',
                maxWidth: '80%',
                fontSize: '0.75rem',
              }}>
                {tc.error_message && (
                  <div style={{ color: '#c53030', marginBottom: '0.4rem' }}>
                    <span style={{ fontWeight: 600 }}>Error: </span>{tc.error_message}
                  </div>
                )}
                {tc.rendered_request && (
                  <div style={{ marginBottom: '0.4rem' }}>
                    <div style={{ fontWeight: 600, color: '#555', marginBottom: '0.2rem' }}>Rendered Request</div>
                    <pre style={{
                      padding: '0.5rem',
                      backgroundColor: '#1a202c',
                      color: '#e2e8f0',
                      borderRadius: 4,
                      fontSize: '0.72rem',
                      overflow: 'auto',
                      lineHeight: 1.4,
                      margin: 0,
                    }}>
                      {JSON.stringify(tc.rendered_request, null, 2)}
                    </pre>
                  </div>
                )}
                {tc.input_params && (
                  <div style={{ marginBottom: '0.4rem' }}>
                    <div style={{ fontWeight: 600, color: '#555', marginBottom: '0.2rem' }}>Input</div>
                    <pre style={{
                      padding: '0.5rem',
                      backgroundColor: '#1a202c',
                      color: '#e2e8f0',
                      borderRadius: 4,
                      fontSize: '0.72rem',
                      overflow: 'auto',
                      lineHeight: 1.4,
                      margin: 0,
                    }}>
                      {JSON.stringify(tc.input_params, null, 2)}
                    </pre>
                  </div>
                )}
                {tc.result_payload && (
                  <div>
                    <div style={{ fontWeight: 600, color: '#555', marginBottom: '0.2rem' }}>Response</div>
                    <pre style={{
                      padding: '0.5rem',
                      backgroundColor: '#1a202c',
                      color: '#e2e8f0',
                      borderRadius: 4,
                      fontSize: '0.72rem',
                      overflow: 'auto',
                      lineHeight: 1.4,
                      margin: 0,
                      maxHeight: 200,
                    }}>
                      {JSON.stringify(tc.result_payload, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// -- Conversation extras (thinking, approvals, summary cards) --

function ConversationExtras({ task, loading, onAction, isProcurement, isHr, isTerminal }: {
  task: Task; loading: boolean; onAction: (taskId: string, action: string) => void;
  isProcurement: boolean; isHr: boolean; isTerminal: boolean;
}) {
  return (
    <>
      {loading && task.thinking_steps && task.thinking_steps.length > 0 && (
        <ThinkingSteps steps={task.thinking_steps} isStreaming={loading} />
      )}
      {task.status === 'awaiting_tool_approval' && (
        <div style={{ marginTop: '1rem', border: '1px solid #e8daef', borderRadius: 8, padding: '0.85rem', backgroundColor: '#f9f4fc' }}>
          {task.tool_calls && task.tool_calls.filter(tc => tc.status === 'pending_approval').length > 0 && (
            <div style={{ marginBottom: '0.75rem' }}>
              <div style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#6c3483', marginBottom: '0.5rem' }}>Pending Tool Calls</div>
              {task.tool_calls.filter(tc => tc.status === 'pending_approval').map(tc => (
                <div key={tc.id} style={{ padding: '0.5rem 0.75rem', border: '1px solid #e2e8f0', borderRadius: 6, backgroundColor: '#fff', marginBottom: '0.4rem', fontSize: '0.82rem' }}>
                  <span style={{ fontWeight: 600 }}>{tc.action}</span>
                  <span style={{ color: '#888', marginLeft: 6 }}>on {tc.connector_name}</span>
                  <span style={{ fontSize: '0.7rem', fontWeight: 600, color: '#888', backgroundColor: '#f0f0f0', padding: '1px 6px', borderRadius: 3, marginLeft: 8 }}>{tc.method}</span>
                  {tc.input_params && (<div style={{ fontSize: '0.75rem', color: '#666', marginTop: 4, fontFamily: 'monospace' }}>{JSON.stringify(tc.input_params)}</div>)}
                </div>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <Btn data-testid="approve-btn" label="Approve" bg="#28a745" onClick={() => onAction(task.id, 'approve')} />
            <Btn data-testid="reject-btn" label="Reject" bg="#dc3545" onClick={() => onAction(task.id, 'reject')} />
          </div>
        </div>
      )}
      {task.tool_calls && task.tool_calls.filter(tc => tc.status === 'executed' || tc.status === 'failed').length > 0 &&
        (() => { try { return localStorage.getItem('norm_show_tool_details') !== 'false'; } catch { return true; } })() && (
        <ToolCallHistory toolCalls={task.tool_calls.filter(tc => tc.status === 'executed' || tc.status === 'failed')} />
      )}
      {(task.status === 'awaiting_approval' || task.status === 'approved') && (
        <div style={{ marginTop: '1rem', border: '1px solid #eee', borderRadius: 8, padding: '0.85rem', backgroundColor: '#fafafa' }}>
          {isProcurement && (() => { const t = task as ProcurementTask; return (<><DetailRow label="Product" value={t.product?.name || '?'} /><DetailRow label="Quantity" value={`${t.quantity ?? '?'} ${t.product?.unit ?? 'case'}(s)`} /><DetailRow label="Venue" value={t.venue?.name || '?'} />{t.supplier && <DetailRow label="Supplier" value={t.supplier} />}</>); })()}
          {isHr && (() => { const t = task as HrTask; return (<><DetailRow label="Name" value={t.employee_name || '?'} /><DetailRow label="Role" value={t.role || '?'} /><DetailRow label="Venue" value={t.venue?.name || '?'} /><DetailRow label="Start date" value={t.start_date || '?'} /></>); })()}
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
            {task.status === 'awaiting_approval' && (<><Btn data-testid="approve-btn" label="Approve" bg="#28a745" onClick={() => onAction(task.id, 'approve')} /><Btn data-testid="reject-btn" label="Reject" bg="#dc3545" onClick={() => onAction(task.id, 'reject')} /></>)}
            {task.status === 'approved' && (<Btn label={isProcurement ? 'Submit to Supplier' : 'Submit Setup'} bg="#4d65ff" onClick={() => onAction(task.id, 'submit')} />)}
          </div>
        </div>
      )}
      {isTerminal && (
        <div style={{ marginTop: '1rem', border: '1px solid #eee', borderRadius: 8, padding: '0.85rem', backgroundColor: '#fafafa' }}>
          {isProcurement && (() => { const t = task as ProcurementTask; return (<><DetailRow label="Product" value={t.product?.name || '?'} /><DetailRow label="Quantity" value={`${t.quantity ?? '?'} ${t.product?.unit ?? 'case'}(s)`} /><DetailRow label="Venue" value={t.venue?.name || '?'} />{t.supplier && <DetailRow label="Supplier" value={t.supplier} />}</>); })()}
          {isHr && (() => { const t = task as HrTask; return (<><DetailRow label="Name" value={t.employee_name || '?'} /><DetailRow label="Role" value={t.role || '?'} /><DetailRow label="Venue" value={t.venue?.name || '?'} /><DetailRow label="Start date" value={t.start_date || '?'} /></>); })()}
          {task.status === 'submitted' && task.integration_run ? (
            <div style={{ marginTop: '0.75rem', borderTop: '1px solid #eee', paddingTop: '0.75rem' }}>
              <div style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: task.integration_run.status === 'success' ? '#28a745' : '#dc3545', marginBottom: '0.4rem' }}>
                {task.integration_run.status === 'success' ? 'Submitted successfully' : 'Submission failed'}
              </div>
              {task.integration_run.reference && <DetailRow label="Reference" value={task.integration_run.reference} />}
              <DetailRow label="Connector" value={task.integration_run.connector} />
              {task.integration_run.submitted_at && <DetailRow label="Submitted" value={new Date(task.integration_run.submitted_at).toLocaleString()} />}
              {task.integration_run.error && <DetailRow label="Error" value={task.integration_run.error} />}
              {task.approval && (<><DetailRow label="Approved by" value={task.approval.performed_by} /><DetailRow label="Approved at" value={new Date(task.approval.performed_at).toLocaleString()} /></>)}
            </div>
          ) : (
            <div style={{ marginTop: '0.6rem', fontSize: '0.8rem', color: task.status === 'submitted' ? '#28a745' : '#888', fontStyle: 'italic' }}>
              {task.status === 'submitted' ? (isProcurement ? 'Order sent to supplier' : 'Employee setup submitted') : 'Rejected'}
            </div>
          )}
          {task.status === 'rejected' && task.approval && (
            <div style={{ marginTop: '0.5rem' }}><DetailRow label="Rejected by" value={task.approval.performed_by} /><DetailRow label="Rejected at" value={new Date(task.approval.performed_at).toLocaleString()} /></div>
          )}
        </div>
      )}
      {task.integration_run?.status === 'failed' && task.status === 'approved' && (
        <div style={{ marginTop: '1rem', border: '1px solid #f5c6cb', borderRadius: 8, padding: '0.85rem', backgroundColor: '#fff5f5' }}>
          <div style={{ fontSize: '0.8rem', color: '#dc3545', marginBottom: '0.5rem' }}>Submission failed: {task.integration_run.error || 'Unknown error'}</div>
          <Btn label="Retry" bg="#dc3545" onClick={() => onAction(task.id, 'submit')} />
        </div>
      )}
    </>
  );
}

// -- Main component --

const InputBar = memo(function InputBar({ onSend, loading, highlight }: { onSend: (msg: string) => void; loading: boolean; highlight?: boolean }) {
  const [value, setValue] = useState('');
  return (
    <div style={{ padding: '12px 24px 24px' }}>
      <form onSubmit={e => { e.preventDefault(); if (value.trim()) { onSend(value); setValue(''); } }} style={{ maxWidth: 768, margin: '0 auto', display: 'flex', alignItems: 'flex-end', gap: '0.4rem' }}>
        <textarea
          data-testid="message-input"
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              if (value.trim()) { onSend(value); setValue(''); }
            }
          }}
          placeholder="Message Norm..."
          rows={1}
          style={{
            flex: 1, minHeight: 50, maxHeight: 150,
            padding: '14px 1.5rem', fontSize: '1rem',
            border: highlight ? '1px solid #c4a882' : '1px solid #ddd',
            borderRadius: 24, outline: 'none', fontFamily: 'inherit',
            resize: 'none', lineHeight: '1.4', boxSizing: 'border-box', overflow: 'hidden',
          }}
        />
        <button data-testid="send-btn" type="submit" disabled={loading} style={{
          height: 50, padding: '0 1rem', fontSize: '0.8rem', fontWeight: 600,
          backgroundColor: '#111', color: '#fff', border: 'none', borderRadius: 24,
          cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
        }}>
          {loading ? '...' : 'Send'}
        </button>
      </form>
    </div>
  );
});

interface TaskDetailProps {
  task: Task;
  onAction: (taskId: string, action: string) => void;
  onWidgetAction?: (taskId: string, action: WidgetAction) => Promise<Record<string, unknown> | void>;
  onSend: (message: string) => void;
  loading: boolean;
  openTask: Task | null;
}

export default function TaskDetail({ task, onAction, onWidgetAction, onSend, loading, openTask }: TaskDetailProps) {
  const [activeTab, setActiveTab] = useState<TabKey>('conversation');
  const dc = getDomainColor(task.domain);
  const DomainIcon = DOMAIN_ICONS[task.domain] || HelpCircle;
  const stl = ss(task.status);
  const isProcurement = task.domain === 'procurement';
  const isHr = task.domain === 'hr';
  const isTerminal = task.status === 'submitted' || task.status === 'rejected';

  // --- Resizable split pane ---
  const { containerRef, topPaneHeight, isDragging, handleDragStart, handleSplitDoubleClick } = useSplitPane('[data-split-header]');

  // Extract the latest full-width display block.
  // Only show split layout if no newer message has non-full-width display blocks
  // (e.g., automated_task_preview should cancel the split screen from an earlier report_builder)
  const messages = task.conversation || [];
  let latestFullWidthBlock: DisplayBlock | null = null;
  let foundNewerInlineBlock = false;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.display_blocks && m.display_blocks.length > 0) {
      const fw = m.display_blocks.find(b => FULL_WIDTH_COMPONENTS.has(b.component));
      if (fw && !foundNewerInlineBlock) {
        latestFullWidthBlock = fw;
        break;
      }
      // This message has display blocks but none are full-width — mark as newer inline
      if (!fw) foundNewerInlineBlock = true;
    }
  }
  const hasSplitLayout = !!latestFullWidthBlock;

  // --- Shared UI pieces ---

  const tabsRow = (
    <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #eee' }}>
      {TABS.map(tab => (
        <button
          key={tab.key}
          data-testid={`tab-${tab.key}`}
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
  );

  const inputBar = <InputBar onSend={onSend} loading={loading} highlight={!!openTask} />;

  return (
    <div ref={containerRef} style={{
      height: '100vh',
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: '#fff',
      userSelect: isDragging ? 'none' : undefined,
    }}>
      {/* Header — hidden when split layout is active */}
      {!hasSplitLayout && (
        <div data-split-header style={{
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
          {tabsRow}
        </div>
      )}
      {/* Minimal header for split pane (needed for useSplitPane to find) */}
      {hasSplitLayout && <div data-split-header style={{ height: 0 }} />}

      {hasSplitLayout ? (
        <>
          {/* Top pane: full-width component */}
          <div style={{
            height: topPaneHeight ?? '50%',
            flexShrink: 0,
            overflowY: 'auto',
          }}>
            <div style={{ padding: '0.75rem 0.5rem 0.75rem 1.5rem', minHeight: '100%' }}>
              <DisplayBlockRenderer
                block={latestFullWidthBlock!}
                onAction={onWidgetAction ? (action) => onWidgetAction(task.id, action) : undefined}
                taskId={task.id}
              />
            </div>
          </div>

          {/* Drag handle */}
          <SplitDragHandle
            isDragging={isDragging}
            topPaneHeight={topPaneHeight}
            containerRef={containerRef}
            onMouseDown={handleDragStart}
            onDoubleClick={handleSplitDoubleClick}
          />

          {/* Bottom pane: tabs + content + input */}
          <div style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            minHeight: 0,
          }}>
            {tabsRow}
            <div style={{ flex: 1, overflowY: 'auto', padding: '1.25rem 1.5rem' }}>
              {activeTab === 'conversation' && (
                <div style={{ maxWidth: 950, margin: '0 auto' }}>
                  <ConversationView
                    messages={messages}
                    onWidgetAction={onWidgetAction ? (action) => onWidgetAction(task.id, action) : undefined}
                    taskId={task.id}
                    hideFullWidthBlocks
                  />
            <div style={{ maxWidth: 768, margin: '0 auto' }}>
              <ConversationExtras task={task} loading={loading} onAction={onAction} isProcurement={isProcurement} isHr={isHr} isTerminal={isTerminal} />
            </div>
                </div>
              )}
              {activeTab === 'details' && <DetailsView task={task} onAction={onAction} />}
              {activeTab === 'activity' && (
                <ActivityTimeline messages={messages} createdAt={task.created_at} domain={task.domain}
                  llmCalls={task.llm_calls}
                  toolCalls={task.tool_calls}
                  thinkingSteps={task.thinking_steps}
                  approval={task.approval} integrationRun={task.integration_run} />
              )}
            </div>
            {inputBar}
          </div>
        </>
      ) : (
        <>
          {/* Non-split: tab content + input */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '1.25rem 1.5rem' }}>
            {activeTab === 'conversation' && (
              <div style={{ maxWidth: 950, margin: '0 auto' }}>
                <ConversationView messages={messages}
                  onWidgetAction={onWidgetAction ? (action) => onWidgetAction(task.id, action) : undefined}
                  taskId={task.id}
                />
                <div style={{ maxWidth: 768, margin: '0 auto' }}>
              <ConversationExtras task={task} loading={loading} onAction={onAction} isProcurement={isProcurement} isHr={isHr} isTerminal={isTerminal} />
            </div>
              </div>
            )}
            {activeTab === 'details' && <DetailsView task={task} onAction={onAction} />}
            {activeTab === 'activity' && (
              <ActivityTimeline messages={messages} createdAt={task.created_at} domain={task.domain}
                llmCalls={task.llm_calls}
                toolCalls={task.tool_calls}
                thinkingSteps={task.thinking_steps}
                approval={task.approval} integrationRun={task.integration_run} />
            )}
          </div>
          {inputBar}
        </>
      )}
    </div>
  );
}
