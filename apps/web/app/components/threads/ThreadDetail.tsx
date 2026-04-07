'use client';

import { useState, useRef, useEffect, memo } from 'react';
import { Timer } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import type { Thread, ProcurementThread, HrThread, ConversationMessage, ToolCallRecord, DisplayBlock, WidgetAction } from '../../types';
import ActivityTimeline from './ActivityTimeline';
import DisplayBlockRenderer, { FULL_WIDTH_COMPONENTS } from '../display/DisplayBlockRenderer';
import SplitDragHandle from '../layout/SplitDragHandle';
import { useSplitPane } from '../../hooks/useSplitPane';
import { getStoredUser } from '../../lib/api';

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
          {steps.map((step, i) => {
            const display = step.replace(/^\[ts:[^\]]+\]\s*/, '');
            return (
              <div key={i} style={{
                fontSize: '0.8rem',
                color: '#888',
                lineHeight: 1.5,
                fontStyle: 'italic',
              }}>
                {display}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// -- Chat conversation view --

export const ConversationView = memo(function ConversationView({ messages, onWidgetAction, threadId, hideFullWidthBlocks }: {
  messages: ConversationMessage[];
  onWidgetAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  threadId?: string;
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
        const displayBlocks = (!isUser && m.display_blocks && m.display_blocks.length > 0)
          ? (hideFullWidthBlocks
              ? m.display_blocks.filter(b => !FULL_WIDTH_COMPONENTS.has(b.component))
              : m.display_blocks)
          : [];

        return (
          <div key={i} style={{ maxWidth: 768, margin: '0 auto', width: '100%' }}>
            {/* Message text constrained to 768px, centered within 950 */}
            <div style={{
              maxWidth: 768,
              margin: '0 auto',
              display: 'flex',
              justifyContent: isUser ? 'flex-end' : 'flex-start',
            }}>
              <div style={{
                maxWidth: isUser ? '80%' : hasTable ? '100%' : '90%',
                padding: isUser ? '0.75rem 1rem' : '0.75rem 0',
                borderRadius: isUser ? '18px 18px 4px 18px' : 0,
                backgroundColor: isUser ? '#f5f0ea' : 'transparent',
                color: '#333',
                fontSize: '1rem',
                lineHeight: 1.6,
                wordBreak: 'break-word',
                whiteSpace: isUser ? 'pre-wrap' : undefined,
              }}>
                {isUser ? m.text : (
                  <div className="markdown-message">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>{m.text}</ReactMarkdown>
                  </div>
                )}
              </div>
            </div>
            {/* Inline display blocks render below the message text */}
            {displayBlocks.length > 0 && (
              <div style={{ marginTop: '0.5rem' }}>
                {displayBlocks.map((block: DisplayBlock, bi: number) => (
                  <DisplayBlockRenderer key={bi} block={block} onAction={onWidgetAction} threadId={threadId} />
                ))}
              </div>
            )}
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

function ProcurementDetails({ task }: { task: ProcurementThread }) {
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

function HrDetails({ task }: { task: HrThread }) {
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

function DetailsView({ task, onAction }: { task: Thread; onAction: (threadId: string, action: string) => void }) {
  const isProcurement = task.domain === 'procurement';
  const isHr = task.domain === 'hr';
  const isTerminal = task.status === 'submitted' || task.status === 'rejected';

  return (
    <div>
      {isProcurement && <ProcurementDetails task={task as ProcurementThread} />}
      {isHr && <HrDetails task={task as HrThread} />}

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

function ConversationExtras({ task, loading, onAction, isProcurement, isHr, isTerminal, isAdmin }: {
  task: Thread; loading: boolean; onAction: (threadId: string, action: string) => void;
  isProcurement: boolean; isHr: boolean; isTerminal: boolean; isAdmin: boolean;
}) {
  return (
    <>
      {loading && task.thinking_steps && task.thinking_steps.length > 0 && (
        <ThinkingSteps steps={task.thinking_steps} isStreaming={loading} />
      )}
      {/* Tool call history — admin only */}
      {isAdmin && task.tool_calls && task.tool_calls.filter(tc => tc.status === 'executed' || tc.status === 'failed').length > 0 &&
        (() => { try { return localStorage.getItem('norm_show_tool_details') !== 'false'; } catch { return true; } })() && (
        <ToolCallHistory toolCalls={task.tool_calls.filter(tc => tc.status === 'executed' || tc.status === 'failed')} />
      )}
      {(task.status === 'awaiting_approval' || task.status === 'approved') && (
        <div style={{ marginTop: '1rem', border: '1px solid #eee', borderRadius: 8, padding: '0.85rem', backgroundColor: '#fafafa' }}>
          {isProcurement && (() => { const t = task as ProcurementThread; return (<><DetailRow label="Product" value={t.product?.name || '?'} /><DetailRow label="Quantity" value={`${t.quantity ?? '?'} ${t.product?.unit ?? 'case'}(s)`} /><DetailRow label="Venue" value={t.venue?.name || '?'} />{t.supplier && <DetailRow label="Supplier" value={t.supplier} />}</>); })()}
          {isHr && (() => { const t = task as HrThread; return (<><DetailRow label="Name" value={t.employee_name || '?'} /><DetailRow label="Role" value={t.role || '?'} /><DetailRow label="Venue" value={t.venue?.name || '?'} /><DetailRow label="Start date" value={t.start_date || '?'} /></>); })()}
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
            {task.status === 'awaiting_approval' && (<><Btn data-testid="approve-btn" label="Approve" bg="#28a745" onClick={() => onAction(task.id, 'approve')} /><Btn data-testid="reject-btn" label="Reject" bg="#dc3545" onClick={() => onAction(task.id, 'reject')} /></>)}
            {task.status === 'approved' && (<Btn label={isProcurement ? 'Submit to Supplier' : 'Submit Setup'} bg="#4d65ff" onClick={() => onAction(task.id, 'submit')} />)}
          </div>
        </div>
      )}
      {isTerminal && (
        <div style={{ marginTop: '1rem', border: '1px solid #eee', borderRadius: 8, padding: '0.85rem', backgroundColor: '#fafafa' }}>
          {isProcurement && (() => { const t = task as ProcurementThread; return (<><DetailRow label="Product" value={t.product?.name || '?'} /><DetailRow label="Quantity" value={`${t.quantity ?? '?'} ${t.product?.unit ?? 'case'}(s)`} /><DetailRow label="Venue" value={t.venue?.name || '?'} />{t.supplier && <DetailRow label="Supplier" value={t.supplier} />}</>); })()}
          {isHr && (() => { const t = task as HrThread; return (<><DetailRow label="Name" value={t.employee_name || '?'} /><DetailRow label="Role" value={t.role || '?'} /><DetailRow label="Venue" value={t.venue?.name || '?'} /><DetailRow label="Start date" value={t.start_date || '?'} /></>); })()}
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
          ref={el => {
            if (el) { el.style.height = 'auto'; const h = Math.min(el.scrollHeight, 150); el.style.height = h + 'px'; el.style.overflow = h >= 150 ? 'auto' : 'hidden'; }
          }}
          value={value}
          onChange={e => {
            setValue(e.target.value);
            const el = e.target; el.style.height = 'auto'; const h = Math.min(el.scrollHeight, 150); el.style.height = h + 'px'; el.style.overflow = h >= 150 ? 'auto' : 'hidden';
          }}
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

// ---------------------------------------------------------------------------
// Automated Task Config Header
// ---------------------------------------------------------------------------

const SCHEDULE_LABELS: Record<string, string> = { manual: 'Manual', hourly: 'Hourly', daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly' };
const AT_STATUS: Record<string, { bg: string; color: string }> = {
  active: { bg: '#d1fae5', color: '#065f46' },
  paused: { bg: '#fef3c7', color: '#92400e' },
  draft: { bg: '#f3f4f6', color: '#6b7280' },
};
const DAYS_OF_WEEK = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

function formatAtSchedule(type: string, config: Record<string, unknown>): string {
  const hour = config.hour as number | undefined;
  const minute = config.minute as number | undefined;
  const time = hour != null ? `${String(hour).padStart(2, '0')}:${String(minute ?? 0).padStart(2, '0')}` : '';
  const day = config.day_of_week as string | undefined;
  if (type === 'daily' && time) return `Daily at ${time}`;
  if (type === 'weekly' && day) return `${day.charAt(0).toUpperCase() + day.slice(1)}s at ${time}`;
  if (type === 'monthly') return `Day ${config.day_of_month || 1} at ${time}`;
  return SCHEDULE_LABELS[type] || type;
}

function AutomatedTaskHeader({ at, onUpdate, onRun }: {
  at: NonNullable<import('../../types').BaseThread['automated_task']>;
  onUpdate: () => void;
  onRun: (prompt: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    prompt: at.prompt,
    schedule_type: at.schedule_type,
    schedule_config: { ...at.schedule_config },
    tool_filter: at.tool_filter ? [...at.tool_filter] : null as string[] | null,
  });
  const [saving, setSaving] = useState(false);
  const [agentTools, setAgentTools] = useState<Array<{ action: string; method: string; description: string; connector: string }>>([]);
  const [toolFilterInput, setToolFilterInput] = useState('');
  const [toolDropdownOpen, setToolDropdownOpen] = useState(false);

  const ats = AT_STATUS[at.status] || AT_STATUS.draft;

  const handleRun = () => {
    onRun(at.prompt);
  };

  const handleToggle = async () => {
    setToggling(true);
    try {
      const endpoint = at.status === 'active' ? 'pause' : 'resume';
      await (await import('../../lib/api')).apiFetch(`/api/automated-tasks/${at.id}/${endpoint}`, { method: 'POST' });
      onUpdate();
    } finally { setToggling(false); }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await (await import('../../lib/api')).apiFetch(`/api/automated-tasks/${at.id}`, {
        method: 'PUT', body: JSON.stringify(form),
      });
      setEditing(false);
      onUpdate();
    } finally { setSaving(false); }
  };

  return (
    <div style={{ padding: '0.5rem 1.5rem', borderBottom: '1px solid #f3f4f6', backgroundColor: '#fafafa' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <Timer size={14} strokeWidth={2} style={{ color: '#9ca3af' }} />
        <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#6b7280' }}>Saved Thread</span>
        <span style={{ fontSize: '0.65rem', fontWeight: 600, padding: '1px 8px', borderRadius: 10, backgroundColor: ats.bg, color: ats.color }}>{at.status}</span>
        <span style={{ fontSize: '0.72rem', color: '#9ca3af' }}>{formatAtSchedule(at.schedule_type, at.schedule_config)}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
          <button onClick={handleRun} style={{
            padding: '3px 10px', fontSize: '0.68rem', fontWeight: 600,
            border: 'none', borderRadius: 6, backgroundColor: '#111', color: '#fff',
            cursor: 'pointer', fontFamily: 'inherit',
          }}>Run Now</button>
          <button onClick={handleToggle} disabled={toggling} style={{
            padding: '3px 10px', fontSize: '0.68rem', fontWeight: 500,
            border: '1px solid #d1d5db', borderRadius: 6, backgroundColor: '#fff', color: '#6b7280',
            cursor: 'pointer', fontFamily: 'inherit',
          }}>{at.status === 'active' ? 'Pause' : 'Activate'}</button>
          <button onClick={() => setExpanded(!expanded)} style={{
            padding: '3px 10px', fontSize: '0.68rem', fontWeight: 500,
            border: '1px solid #d1d5db', borderRadius: 6, backgroundColor: expanded ? '#f3f4f6' : '#fff', color: '#6b7280',
            cursor: 'pointer', fontFamily: 'inherit',
          }}>Settings</button>
        </div>
      </div>

      {expanded && (
        <div style={{ marginTop: '0.5rem', fontSize: '0.78rem' }}>
          {!editing ? (
            <>
              <div style={{ marginBottom: '0.4rem' }}>
                <span style={{ fontWeight: 600, color: '#6b7280', fontSize: '0.68rem', textTransform: 'uppercase' }}>Prompt</span>
                <div style={{ padding: '0.4rem 0.5rem', backgroundColor: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, marginTop: 2, whiteSpace: 'pre-wrap', color: '#374151', lineHeight: 1.5 }}>
                  {at.prompt}
                </div>
              </div>
              {at.task_config && Object.keys(at.task_config).length > 0 && (
                <div style={{ marginBottom: '0.4rem' }}>
                  <span style={{ fontWeight: 600, color: '#6b7280', fontSize: '0.68rem', textTransform: 'uppercase' }}>Config</span>
                  <pre style={{ padding: '0.4rem 0.5rem', backgroundColor: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, marginTop: 2, fontSize: '0.72rem', margin: 0, overflow: 'auto' }}>
                    {JSON.stringify(at.task_config, null, 2)}
                  </pre>
                </div>
              )}
              {at.thread_summary && (
                <div style={{ marginBottom: '0.4rem' }}>
                  <span style={{ fontWeight: 600, color: '#6b7280', fontSize: '0.68rem', textTransform: 'uppercase' }}>Summary</span>
                  <div style={{ padding: '0.4rem 0.5rem', backgroundColor: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, marginTop: 2, color: '#374151' }}>
                    {at.thread_summary}
                  </div>
                </div>
              )}
              <div style={{ marginBottom: '0.4rem' }}>
                <span style={{ fontWeight: 600, color: '#6b7280', fontSize: '0.68rem', textTransform: 'uppercase' }}>Tools</span>
                <div style={{ padding: '0.4rem 0.5rem', backgroundColor: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, marginTop: 2, display: 'flex', flexWrap: 'wrap', gap: '0.25rem', alignItems: 'center' }}>
                  {at.tool_filter && at.tool_filter.length > 0 ? (
                    at.tool_filter.map(action => (
                      <span key={action} style={{ fontSize: '0.68rem', padding: '2px 8px', borderRadius: 10, backgroundColor: '#eef2ff', color: '#4338ca', fontWeight: 500 }}>{action}</span>
                    ))
                  ) : (
                    <span style={{ fontSize: '0.72rem', color: '#9ca3af' }}>All tools (no filter)</span>
                  )}
                </div>
              </div>
              <button onClick={() => {
                setForm({ prompt: at.prompt, schedule_type: at.schedule_type, schedule_config: { ...at.schedule_config }, tool_filter: at.tool_filter ? [...at.tool_filter] : null });
                setEditing(true);
                if (at.agent_slug && agentTools.length === 0) {
                  import('../../lib/api').then(({ apiFetch }) =>
                    apiFetch(`/api/playbooks/tools/${at.agent_slug}`)
                      .then(r => r.ok ? r.json() : null)
                      .then(d => { if (d?.tools) setAgentTools(d.tools); })
                      .catch(() => {})
                  );
                }
              }} style={{
                padding: '4px 12px', fontSize: '0.72rem', fontWeight: 500,
                border: '1px solid #d1d5db', borderRadius: 6, backgroundColor: '#fff', color: '#374151',
                cursor: 'pointer', fontFamily: 'inherit', marginTop: '0.3rem',
              }}>Edit</button>
            </>
          ) : (
            <>
              <div style={{ marginBottom: '0.4rem' }}>
                <span style={{ fontWeight: 600, color: '#6b7280', fontSize: '0.68rem', textTransform: 'uppercase' }}>Prompt</span>
                <textarea
                  value={form.prompt}
                  onChange={e => setForm(f => ({ ...f, prompt: e.target.value }))}
                  rows={4}
                  style={{ width: '100%', padding: '6px 10px', fontSize: '0.78rem', fontFamily: 'inherit', border: '1px solid #e5e7eb', borderRadius: 6, resize: 'vertical', marginTop: 2, boxSizing: 'border-box' }}
                />
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.4rem', flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, color: '#6b7280', fontSize: '0.68rem', textTransform: 'uppercase' }}>Schedule</span>
                <select value={form.schedule_type} onChange={e => setForm(f => ({ ...f, schedule_type: e.target.value }))} style={{ padding: '4px 8px', fontSize: '0.78rem', fontFamily: 'inherit', border: '1px solid #e5e7eb', borderRadius: 6 }}>
                  {Object.entries(SCHEDULE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                {['daily', 'weekly', 'monthly'].includes(form.schedule_type) && (
                  <input type="time" value={`${String((form.schedule_config.hour as number) ?? 9).padStart(2, '0')}:${String((form.schedule_config.minute as number) ?? 0).padStart(2, '0')}`} onChange={e => { const [h, m] = e.target.value.split(':').map(Number); setForm(f => ({ ...f, schedule_config: { ...f.schedule_config, hour: h, minute: m } })); }} style={{ padding: '4px 8px', fontSize: '0.78rem', fontFamily: 'inherit', border: '1px solid #e5e7eb', borderRadius: 6 }} />
                )}
                {form.schedule_type === 'weekly' && (
                  <select value={(form.schedule_config.day_of_week as string) || 'monday'} onChange={e => setForm(f => ({ ...f, schedule_config: { ...f.schedule_config, day_of_week: e.target.value } }))} style={{ padding: '4px 8px', fontSize: '0.78rem', fontFamily: 'inherit', border: '1px solid #e5e7eb', borderRadius: 6, textTransform: 'capitalize' }}>
                    {DAYS_OF_WEEK.map(d => <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>)}
                  </select>
                )}
              </div>
              {/* Tool Filter */}
              <div style={{ marginBottom: '0.4rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: 4 }}>
                  <span style={{ fontWeight: 600, color: '#6b7280', fontSize: '0.68rem', textTransform: 'uppercase' }}>Tool Filter</span>
                  {form.tool_filter ? (
                    <button onClick={() => setForm(f => ({ ...f, tool_filter: null }))} style={{ fontSize: '0.65rem', color: '#6366f1', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit', padding: 0 }}>Clear filter (use all tools)</button>
                  ) : (
                    <button onClick={() => setForm(f => ({ ...f, tool_filter: [] }))} style={{ fontSize: '0.65rem', color: '#6366f1', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit', padding: 0 }}>Add filter</button>
                  )}
                </div>
                {form.tool_filter !== null && (
                  <>
                    {form.tool_filter.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem', marginBottom: 4 }}>
                        {form.tool_filter.map(action => (
                          <span key={action} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: '0.68rem', padding: '2px 8px', borderRadius: 10, backgroundColor: '#eef2ff', color: '#4338ca', fontWeight: 500 }}>
                            {action}
                            <span onClick={() => { const next = form.tool_filter!.filter(a => a !== action); setForm(f => ({ ...f, tool_filter: next.length > 0 ? next : [] })); }} style={{ cursor: 'pointer', fontWeight: 700, fontSize: '0.72rem', lineHeight: 1 }}>&times;</span>
                          </span>
                        ))}
                      </div>
                    )}
                    <div style={{ position: 'relative' }}>
                      <input
                        type="text"
                        value={toolFilterInput}
                        onChange={e => { setToolFilterInput(e.target.value); setToolDropdownOpen(true); }}
                        onFocus={() => {
                          setToolDropdownOpen(true);
                          if (agentTools.length === 0 && at.agent_slug) {
                            import('../../lib/api').then(({ apiFetch }) =>
                              apiFetch(`/api/playbooks/tools/${at.agent_slug}`)
                                .then(r => r.ok ? r.json() : null)
                                .then(d => { if (d?.tools) setAgentTools(d.tools); })
                                .catch(() => {})
                            );
                          }
                        }}
                        onBlur={() => setTimeout(() => setToolDropdownOpen(false), 150)}
                        placeholder={agentTools.length > 0 ? 'Search tools to add...' : 'Loading tools...'}
                        style={{ width: '100%', padding: '4px 8px', fontSize: '0.78rem', fontFamily: 'inherit', border: '1px solid #e5e7eb', borderRadius: 6, boxSizing: 'border-box' }}
                      />
                      {toolDropdownOpen && agentTools.length > 0 && (() => {
                        const selected = new Set(form.tool_filter || []);
                        const filtered = agentTools
                          .filter(t => !selected.has(t.action))
                          .filter(t => !toolFilterInput || t.action.toLowerCase().includes(toolFilterInput.toLowerCase()) || t.description.toLowerCase().includes(toolFilterInput.toLowerCase()));
                        if (filtered.length === 0) return null;
                        return (
                          <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10, maxHeight: 180, overflowY: 'auto', backgroundColor: '#fff', border: '1px solid #ddd', borderRadius: 6, boxShadow: '0 4px 12px rgba(0,0,0,0.1)', marginTop: 2 }}>
                            {filtered.map(t => (
                              <div
                                key={t.action}
                                onMouseDown={e => {
                                  e.preventDefault();
                                  setForm(f => ({ ...f, tool_filter: [...(f.tool_filter || []), t.action] }));
                                  setToolFilterInput('');
                                }}
                                style={{ padding: '5px 10px', cursor: 'pointer', fontSize: '0.78rem', borderBottom: '1px solid #f5f5f5' }}
                                onMouseEnter={e => (e.currentTarget.style.backgroundColor = '#f0f4ff')}
                                onMouseLeave={e => (e.currentTarget.style.backgroundColor = '#fff')}
                              >
                                <span style={{ fontWeight: 500 }}>{t.action}</span>
                                <span style={{ color: '#aaa', fontSize: '0.68rem', marginLeft: 6 }}>[{t.method}]</span>
                                {t.description && <div style={{ fontSize: '0.68rem', color: '#888', marginTop: 1 }}>{t.description}</div>}
                              </div>
                            ))}
                          </div>
                        );
                      })()}
                    </div>
                  </>
                )}
              </div>
              <div style={{ display: 'flex', gap: '0.3rem' }}>
                <button onClick={handleSave} disabled={saving} style={{
                  padding: '4px 12px', fontSize: '0.72rem', fontWeight: 600,
                  border: 'none', borderRadius: 6, backgroundColor: '#111', color: '#fff',
                  cursor: 'pointer', fontFamily: 'inherit',
                }}>{saving ? 'Saving...' : 'Save'}</button>
                <button onClick={() => setEditing(false)} style={{
                  padding: '4px 12px', fontSize: '0.72rem', fontWeight: 500,
                  border: '1px solid #d1d5db', borderRadius: 6, backgroundColor: '#fff', color: '#6b7280',
                  cursor: 'pointer', fontFamily: 'inherit',
                }}>Cancel</button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

interface ThreadDetailProps {
  thread: Thread;
  onAction: (threadId: string, action: string) => void;
  onWidgetAction?: (threadId: string, action: WidgetAction) => Promise<Record<string, unknown> | void>;
  onSend: (message: string) => void;
  loading: boolean;
  openThread: Thread | null;
}

export default function ThreadDetail({ thread, onAction, onWidgetAction, onSend, loading, openThread }: ThreadDetailProps) {
  const storedUser = getStoredUser();
  const isAdmin = storedUser?.role === 'admin';
  const [activeTab, setActiveTab] = useState<TabKey>('conversation');
  const isProcurement = thread.domain === 'procurement';
  const isHr = thread.domain === 'hr';
  const isTerminal = thread.status === 'submitted' || thread.status === 'rejected';

  // --- Resizable split pane ---
  const { containerRef, topPaneHeight, isDragging, handleDragStart, handleSplitDoubleClick } = useSplitPane('[data-split-header]');

  // Extract the latest full-width display block.
  // Only show split layout if no newer message has non-full-width display blocks
  // (e.g., automated_task_preview should cancel the split screen from an earlier report_builder)
  const messages = thread.conversation || [];
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

  const inputBar = <InputBar onSend={onSend} loading={loading} highlight={!!openThread} />;

  return (
    <div ref={containerRef} style={{
      height: '100vh',
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: '#fff',
      userSelect: isDragging ? 'none' : undefined,
    }}>
      {/* Header — minimal: only AutomatedTaskHeader + admin tabs if needed */}
      {!hasSplitLayout && (
        <div data-split-header style={{
          ...(thread.automated_task || isAdmin ? { borderBottom: '1px solid #eee' } : {}),
        }}>
          {thread.automated_task && (
            <div style={{ padding: '0.5rem 1.5rem 0' }}>
              <AutomatedTaskHeader at={thread.automated_task} onUpdate={() => onAction(thread.id, 'reload')} onRun={onSend} />
            </div>
          )}
          {isAdmin && tabsRow}
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
                onAction={onWidgetAction ? (action) => onWidgetAction(thread.id, action) : undefined}
                threadId={thread.id}
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
            {isAdmin && tabsRow}
            <div style={{ flex: 1, overflowY: 'auto', padding: '1.25rem 1.5rem' }}>
              {activeTab === 'conversation' && (
                <>
                  <ConversationView
                    messages={messages}
                    onWidgetAction={onWidgetAction ? (action) => onWidgetAction(thread.id, action) : undefined}
                    threadId={thread.id}
                    hideFullWidthBlocks
                  />
                  <div style={{ maxWidth: 768, margin: '0 auto' }}>
                    <ConversationExtras task={thread} loading={loading} onAction={onAction} isProcurement={isProcurement} isHr={isHr} isTerminal={isTerminal} isAdmin={!!isAdmin} />
                  </div>
                </>
              )}
              {activeTab === 'details' && <DetailsView task={thread} onAction={onAction} />}
              {activeTab === 'activity' && (
                <ActivityTimeline messages={messages} createdAt={thread.created_at} domain={thread.domain}
                  llmCalls={thread.llm_calls}
                  toolCalls={thread.tool_calls}
                  thinkingSteps={thread.thinking_steps}
                  approval={thread.approval} integrationRun={thread.integration_run} />
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
              <>
                <ConversationView messages={messages}
                  onWidgetAction={onWidgetAction ? (action) => onWidgetAction(thread.id, action) : undefined}
                  threadId={thread.id}
                />
                <div style={{ maxWidth: 768, margin: '0 auto' }}>
                  <ConversationExtras task={thread} loading={loading} onAction={onAction} isProcurement={isProcurement} isHr={isHr} isTerminal={isTerminal} isAdmin={!!isAdmin} />
                </div>
              </>
            )}
            {activeTab === 'details' && <DetailsView task={thread} onAction={onAction} />}
            {activeTab === 'activity' && (
              <ActivityTimeline messages={messages} createdAt={thread.created_at} domain={thread.domain}
                llmCalls={thread.llm_calls}
                toolCalls={thread.tool_calls}
                thinkingSteps={thread.thinking_steps}
                approval={thread.approval} integrationRun={thread.integration_run} />
            )}
          </div>
          {inputBar}
        </>
      )}
    </div>
  );
}
