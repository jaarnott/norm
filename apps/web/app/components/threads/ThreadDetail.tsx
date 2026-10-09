'use client';

import { useState, useRef, useEffect, memo } from 'react';
import { ChevronRight, RotateCcw, Timer, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import type { Thread, ProcurementThread, HrThread, ConversationMessage, ToolCallRecord, DisplayBlock, WidgetAction } from '../../types';
import ActivityTimeline from './ActivityTimeline';
import NotebookCard from './NotebookCard';
import DisplayBlockRenderer, { FULL_WIDTH_COMPONENTS } from '../display/DisplayBlockRenderer';

/** Check if a display block should render full-width above conversation. */
function isFullWidthBlock(b: DisplayBlock): boolean {
  if (FULL_WIDTH_COMPONENTS.has(b.component)) return true;
  if (b.component === 'mcp_embed' && b.props?.container_hint === 'full_page') return true;
  return false;
}
import SplitDragHandle from '../layout/SplitDragHandle';
import { useSplitPane } from '../../hooks/useSplitPane';
import { getStoredUser } from '../../lib/api';
import { parseThinkingStep } from '../../lib/thinkingSteps';
import { SentAttachmentChips, type SendOptions } from '../chat/AttachmentComposer';
import Composer from '../chat/Composer';
import Button from '../ui/Button';
import Badge, { type BadgeTone } from '../ui/Badge';
import Tabs from '../ui/Tabs';

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
      borderLeft: '2px solid var(--line)',
      paddingLeft: '0.75rem',
    }}>
      <button
        onClick={() => { if (!isStreaming) setUserCollapsed(!userCollapsed); }}
        style={{
          background: 'none',
          border: 'none',
          cursor: isStreaming ? 'default' : 'pointer',
          fontSize: 'var(--fs-sm)',
          color: 'var(--muted)',
          padding: '0.25rem 0',
          display: 'flex',
          alignItems: 'center',
          gap: '0.35rem',
        }}
      >
        {isStreaming ? (
          <span className="thinking-dot" aria-hidden style={{ fontSize: '0.65rem' }}>&#9679;</span>
        ) : (
          <ChevronRight size={14} aria-hidden style={{ transition: 'transform 0.15s', transform: showSteps ? 'rotate(90deg)' : 'none' }} />
        )}
        {isStreaming ? 'Working…' : `${steps.length} reasoning step${steps.length > 1 ? 's' : ''}`}
      </button>
      {showSteps && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginTop: '0.3rem' }}>
          {steps.map((step, i) => (
            // Tags ([ts:], [reasoning], [test]) are bookkeeping; a test step's
            // own words already say "(simulated)", so it needs no badge here.
            <div key={i} style={{
              fontSize: 'var(--fs-sm)',
              color: 'var(--muted)',
              lineHeight: 1.5,
            }}>
              {parseThinkingStep(step).text}
            </div>
          ))}
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
      <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--muted)', fontSize: 'var(--fs-base)' }}>
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
              ? m.display_blocks.filter(b => !isFullWidthBlock(b))
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
                backgroundColor: isUser ? 'var(--selected)' : 'transparent',
                color: 'var(--text)',
                fontSize: 'var(--fs-md)',
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
            {isUser && <SentAttachmentChips attachments={m.attachments} />}
            {/* Inline display blocks render below the message text. Flex gap:
                a batch review emits many sibling cards on one message — they
                need breathing room between them. */}
            {displayBlocks.length > 0 && (
              <div style={{ marginTop: '0.5rem', display: 'flex', flexDirection: 'column', gap: 15 }}>
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
  <div style={{ display: 'flex', padding: '0.4rem 0', borderBottom: '1px solid var(--line-soft)' }}>
    <span style={{ width: 120, fontSize: 'var(--fs-sm)', color: 'var(--muted)', flexShrink: 0 }}>{label}</span>
    <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text)' }}>{value}</span>
  </div>
);

/** A white card on the cream pane: details, the legacy approval, the result. */
const detailCard: React.CSSProperties = {
  border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)',
  padding: '0.85rem 1rem', backgroundColor: 'var(--bg)',
};

function ProcurementDetails({ task }: { task: ProcurementThread }) {
  return (
    <div style={{ marginBottom: '1rem' }}>
      <div className="n-eyebrow" style={{ marginBottom: '0.5rem' }}>
        Order Details
      </div>
      <div style={detailCard}>
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
      <div className="n-eyebrow" style={{ marginBottom: '0.5rem' }}>
        Employee Details
      </div>
      <div style={{ ...detailCard, marginBottom: '0.75rem' }}>
        <DetailRow label="Name" value={task.employee_name || '?'} />
        <DetailRow label="Role" value={task.role || '?'} />
        <DetailRow label="Venue" value={task.venue?.name || '?'} />
        <DetailRow label="Start date" value={task.start_date || '?'} />
      </div>
      {task.checklist && task.checklist.length > 0 && (
        <div>
          <div className="n-eyebrow" style={{ marginBottom: '0.4rem' }}>
            Onboarding Checklist
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.15rem 1rem' }}>
            {task.checklist.map((c) => (
              <span key={c.item} style={{ fontSize: 'var(--fs-sm)', color: c.done ? 'var(--ok)' : 'var(--muted)' }}>
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
        <div className="n-eyebrow" style={{ marginBottom: '0.5rem' }}>
          Status
        </div>
        <div style={detailCard}>
          <DetailRow label="Status" value={task.status.replace(/_/g, ' ')} />
          <DetailRow label="Domain" value={task.domain} />
          <DetailRow label="Created" value={new Date(task.created_at).toLocaleString()} />
        </div>
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        {task.status === 'awaiting_approval' && (
          <>
            <Button variant="primary" data-testid="approve-btn" onClick={() => onAction(task.id, 'approve')}>Approve</Button>
            <Button variant="danger" data-testid="reject-btn" onClick={() => onAction(task.id, 'reject')}>Reject</Button>
          </>
        )}
        {task.status === 'approved' && (
          <Button variant="primary" onClick={() => onAction(task.id, 'submit')}>
            {isProcurement ? 'Submit to supplier' : 'Submit setup'}
          </Button>
        )}
        {isTerminal && (
          <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
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
                padding: '0.35rem 0.7rem',
                borderRadius: 'var(--radius)',
                backgroundColor: 'var(--bg)',
                border: `1px solid ${isFailed ? 'var(--error-bg)' : 'var(--line)'}`,
                fontSize: 'var(--fs-xs)',
                color: 'var(--text-soft)',
                maxWidth: '80%',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <span style={{ fontWeight: 600, color: isFailed ? 'var(--error)' : 'var(--text)' }}>{tc.action}</span>
              <span style={{ color: 'var(--muted)' }}>({tc.connector_name})</span>
              {tc.duration_ms != null && (
                <span style={{ color: 'var(--muted)' }}>{tc.duration_ms}ms</span>
              )}
              {isFailed && <Badge tone="error">Failed</Badge>}
              <ChevronRight size={12} aria-hidden style={{ color: 'var(--icon)', transition: 'transform 0.15s', transform: isExpanded ? 'rotate(90deg)' : 'none' }} />
            </div>
            {isExpanded && (
              <div style={{
                marginTop: '0.3rem',
                padding: '0.6rem 0.75rem',
                borderRadius: 'var(--radius)',
                backgroundColor: 'var(--bg)',
                border: '1px solid var(--line)',
                maxWidth: '80%',
                fontSize: 'var(--fs-xs)',
              }}>
                {tc.error_message && (
                  <div style={{ color: 'var(--error)', marginBottom: '0.4rem' }}>
                    <span style={{ fontWeight: 600 }}>Error: </span>{tc.error_message}
                  </div>
                )}
                {tc.rendered_request && (
                  <div style={{ marginBottom: '0.4rem' }}>
                    <div style={{ fontWeight: 600, color: 'var(--text-soft)', marginBottom: '0.2rem' }}>Rendered Request</div>
                    <pre style={{
                      padding: '0.5rem',
                      backgroundColor: 'var(--code-bg)',
                      color: 'var(--code-text)',
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
                    <div style={{ fontWeight: 600, color: 'var(--text-soft)', marginBottom: '0.2rem' }}>Input</div>
                    <pre style={{
                      padding: '0.5rem',
                      backgroundColor: 'var(--code-bg)',
                      color: 'var(--code-text)',
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
                    <div style={{ fontWeight: 600, color: 'var(--text-soft)', marginBottom: '0.2rem' }}>Response</div>
                    <pre style={{
                      padding: '0.5rem',
                      backgroundColor: 'var(--code-bg)',
                      color: 'var(--code-text)',
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
      {/* Show the working strip while this tab is streaming (loading) OR when the
          thread's turn is still running server-side (in_progress) — the latter is
          an open thread being polled after its send's stream was cut (e.g. mobile
          screen-lock), so it must read as "working", not stuck. */}
      {(loading || task.status === 'in_progress') && task.thinking_steps && task.thinking_steps.length > 0 && (
        <ThinkingSteps steps={task.thinking_steps} isStreaming={loading || task.status === 'in_progress'} />
      )}
      {/* The conversation's notebook — the model's working notes, as a card */}
      {task.notebook && task.notebook.length > 0 && <NotebookCard files={task.notebook} />}
      {/* Tool call history — admin only */}
      {isAdmin && task.tool_calls && task.tool_calls.filter(tc => tc.status === 'executed' || tc.status === 'failed').length > 0 &&
        (() => { try { return localStorage.getItem('norm_show_tool_details') !== 'false'; } catch { return true; } })() && (
        <ToolCallHistory toolCalls={task.tool_calls.filter(tc => tc.status === 'executed' || tc.status === 'failed')} />
      )}
      {(task.status === 'awaiting_approval' || task.status === 'approved') && (
        <div style={{ ...detailCard, marginTop: '1rem' }}>
          {isProcurement && (() => { const t = task as ProcurementThread; return (<><DetailRow label="Product" value={t.product?.name || '?'} /><DetailRow label="Quantity" value={`${t.quantity ?? '?'} ${t.product?.unit ?? 'case'}(s)`} /><DetailRow label="Venue" value={t.venue?.name || '?'} />{t.supplier && <DetailRow label="Supplier" value={t.supplier} />}</>); })()}
          {isHr && (() => { const t = task as HrThread; return (<><DetailRow label="Name" value={t.employee_name || '?'} /><DetailRow label="Role" value={t.role || '?'} /><DetailRow label="Venue" value={t.venue?.name || '?'} /><DetailRow label="Start date" value={t.start_date || '?'} /></>); })()}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '0.75rem' }}>
            {task.status === 'awaiting_approval' && (<><Button variant="danger" data-testid="reject-btn" onClick={() => onAction(task.id, 'reject')}>Reject</Button><Button variant="primary" data-testid="approve-btn" onClick={() => onAction(task.id, 'approve')}>Approve</Button></>)}
            {task.status === 'approved' && (<Button variant="primary" onClick={() => onAction(task.id, 'submit')}>{isProcurement ? 'Submit to supplier' : 'Submit setup'}</Button>)}
          </div>
        </div>
      )}
      {isTerminal && (
        <div style={{ ...detailCard, marginTop: '1rem' }}>
          {isProcurement && (() => { const t = task as ProcurementThread; return (<><DetailRow label="Product" value={t.product?.name || '?'} /><DetailRow label="Quantity" value={`${t.quantity ?? '?'} ${t.product?.unit ?? 'case'}(s)`} /><DetailRow label="Venue" value={t.venue?.name || '?'} />{t.supplier && <DetailRow label="Supplier" value={t.supplier} />}</>); })()}
          {isHr && (() => { const t = task as HrThread; return (<><DetailRow label="Name" value={t.employee_name || '?'} /><DetailRow label="Role" value={t.role || '?'} /><DetailRow label="Venue" value={t.venue?.name || '?'} /><DetailRow label="Start date" value={t.start_date || '?'} /></>); })()}
          {task.status === 'submitted' && task.integration_run ? (
            <div style={{ marginTop: '0.75rem', borderTop: '1px solid var(--line)', paddingTop: '0.75rem' }}>
              <div style={{ marginBottom: '0.4rem' }}>
                <Badge tone={task.integration_run.status === 'success' ? 'ok' : 'error'}>
                  {task.integration_run.status === 'success' ? 'Submitted successfully' : 'Submission failed'}
                </Badge>
              </div>
              {task.integration_run.reference && <DetailRow label="Reference" value={task.integration_run.reference} />}
              <DetailRow label="Connector" value={task.integration_run.connector} />
              {task.integration_run.submitted_at && <DetailRow label="Submitted" value={new Date(task.integration_run.submitted_at).toLocaleString()} />}
              {task.integration_run.error && <DetailRow label="Error" value={task.integration_run.error} />}
              {task.approval && (<><DetailRow label="Approved by" value={task.approval.performed_by} /><DetailRow label="Approved at" value={new Date(task.approval.performed_at).toLocaleString()} /></>)}
            </div>
          ) : (
            <div style={{ marginTop: '0.6rem', fontSize: 'var(--fs-sm)', color: task.status === 'submitted' ? 'var(--ok)' : 'var(--muted)' }}>
              {task.status === 'submitted' ? (isProcurement ? 'Order sent to supplier' : 'Employee setup submitted') : 'Rejected'}
            </div>
          )}
          {task.status === 'rejected' && task.approval && (
            <div style={{ marginTop: '0.5rem' }}><DetailRow label="Rejected by" value={task.approval.performed_by} /><DetailRow label="Rejected at" value={new Date(task.approval.performed_at).toLocaleString()} /></div>
          )}
        </div>
      )}
      {task.integration_run?.status === 'failed' && task.status === 'approved' && (
        <div style={{ ...detailCard, marginTop: '1rem', borderColor: 'var(--error-bg)' }}>
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)', marginBottom: '0.6rem' }}>Submission failed: {task.integration_run.error || 'Unknown error'}</div>
          <Button variant="secondary" onClick={() => onAction(task.id, 'submit')}>Retry</Button>
        </div>
      )}
    </>
  );
}

// -- Main component --

const InputBar = memo(function InputBar({ onSend, loading, highlight }: { onSend: (msg: string, opts?: SendOptions) => void; loading: boolean; highlight?: boolean }) {
  return (
    <div className="n-composer-dock">
      <Composer
        onSend={(text, attachments) => onSend(text, { attachments })}
        loading={loading}
        highlight={highlight}
        inputTestId="message-input"
        sendTestId="send-btn"
      />
    </div>
  );
});

// ---------------------------------------------------------------------------
// Automated Task Config Header
// ---------------------------------------------------------------------------

const SCHEDULE_LABELS: Record<string, string> = { manual: 'Manual', hourly: 'Hourly', daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly' };
const AT_STATUS: Record<string, BadgeTone> = { active: 'ok', paused: 'warn', draft: 'neutral' };
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

  const statusTone = AT_STATUS[at.status] ?? 'neutral';

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

  const fieldLabel = (text: string) => <div className="n-eyebrow" style={{ marginBottom: 4 }}>{text}</div>;
  const readBox: React.CSSProperties = {
    padding: '8px 10px', backgroundColor: 'var(--bg)', border: '1px solid var(--line)',
    borderRadius: 'var(--radius)', color: 'var(--text)', lineHeight: 1.5,
  };
  const toolChip: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px', borderRadius: 999,
    fontSize: 'var(--fs-xs)', fontWeight: 500, background: 'var(--line-soft)', color: 'var(--text-soft)',
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Timer size={16} strokeWidth={1.75} aria-hidden style={{ color: 'var(--icon)' }} />
        <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>Saved thread</span>
        <Badge tone={statusTone}>{at.status.charAt(0).toUpperCase() + at.status.slice(1)}</Badge>
        <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{formatAtSchedule(at.schedule_type, at.schedule_config)}</span>
        {(at.waiting_for_approval ?? 0) > 0 && (
          <Badge tone="warn">A run is waiting for your approval — see its card below</Badge>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <Button size="sm" variant="primary" onClick={handleRun}>Run now</Button>
          <Button size="sm" variant="secondary" onClick={handleToggle} disabled={toggling}>{at.status === 'active' ? 'Pause' : 'Activate'}</Button>
          <Button size="sm" variant="quiet" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>Settings</Button>
        </div>
      </div>

      {expanded && (
        <div style={{ marginTop: 12, fontSize: 'var(--fs-sm)', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {!editing ? (
            <>
              <div>
                {fieldLabel('Prompt')}
                <div style={{ ...readBox, whiteSpace: 'pre-wrap' }}>{at.prompt}</div>
              </div>
              {at.task_config && Object.keys(at.task_config).length > 0 && (
                <div>
                  {fieldLabel('Config')}
                  <pre style={{ ...readBox, margin: 0, fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-mono)', overflow: 'auto' }}>
                    {JSON.stringify(at.task_config, null, 2)}
                  </pre>
                </div>
              )}
              {at.thread_summary && (
                <div>
                  {fieldLabel('Summary')}
                  <div style={readBox}>{at.thread_summary}</div>
                </div>
              )}
              <div>
                {fieldLabel('Tools')}
                <div style={{ ...readBox, display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
                  {at.tool_filter && at.tool_filter.length > 0 ? (
                    at.tool_filter.map(action => <span key={action} style={toolChip}>{action}</span>)
                  ) : (
                    <span style={{ color: 'var(--muted)' }}>All tools (no filter)</span>
                  )}
                </div>
              </div>
              <div>
                <Button size="sm" variant="secondary" onClick={() => {
                  setForm({ prompt: at.prompt, schedule_type: at.schedule_type, schedule_config: { ...at.schedule_config }, tool_filter: at.tool_filter ? [...at.tool_filter] : null });
                  setEditing(true);
                  if (at.agent_slug && agentTools.length === 0) {
                    import('../../lib/api').then(({ apiFetch }) =>
                      apiFetch('/api/playbooks/tools/all')
                        .then(r => r.ok ? r.json() : null)
                        .then(d => { if (d?.tools) setAgentTools(d.tools); })
                        .catch(() => {})
                    );
                  }
                }}>Edit</Button>
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="n-eyebrow" style={{ display: 'block', marginBottom: 4 }} htmlFor={`at-prompt-${at.id}`}>Prompt</label>
                <textarea
                  id={`at-prompt-${at.id}`}
                  className="n-input"
                  value={form.prompt}
                  onChange={e => setForm(f => ({ ...f, prompt: e.target.value }))}
                  rows={4}
                  style={{ width: '100%' }}
                />
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span className="n-eyebrow">Schedule</span>
                <select className="n-select" aria-label="Schedule" value={form.schedule_type} onChange={e => setForm(f => ({ ...f, schedule_type: e.target.value }))}>
                  {Object.entries(SCHEDULE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                {['daily', 'weekly', 'monthly'].includes(form.schedule_type) && (
                  <input className="n-input" aria-label="Time" type="time" value={`${String((form.schedule_config.hour as number) ?? 9).padStart(2, '0')}:${String((form.schedule_config.minute as number) ?? 0).padStart(2, '0')}`} onChange={e => { const [h, m] = e.target.value.split(':').map(Number); setForm(f => ({ ...f, schedule_config: { ...f.schedule_config, hour: h, minute: m } })); }} />
                )}
                {form.schedule_type === 'weekly' && (
                  <select className="n-select" aria-label="Day" value={(form.schedule_config.day_of_week as string) || 'monday'} onChange={e => setForm(f => ({ ...f, schedule_config: { ...f.schedule_config, day_of_week: e.target.value } }))}>
                    {DAYS_OF_WEEK.map(d => <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>)}
                  </select>
                )}
              </div>
              {/* Tool Filter */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span className="n-eyebrow">Tool filter</span>
                  {form.tool_filter ? (
                    <button type="button" className="n-btn n-btn--link" style={{ fontSize: 'var(--fs-xs)' }} onClick={() => setForm(f => ({ ...f, tool_filter: null }))}>Clear filter (use all tools)</button>
                  ) : (
                    <button type="button" className="n-btn n-btn--link" style={{ fontSize: 'var(--fs-xs)' }} onClick={() => setForm(f => ({ ...f, tool_filter: [] }))}>Add filter</button>
                  )}
                </div>
                {form.tool_filter !== null && (
                  <>
                    {form.tool_filter.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
                        {form.tool_filter.map(action => (
                          <span key={action} style={toolChip}>
                            {action}
                            <button
                              type="button"
                              aria-label={`Remove ${action}`}
                              onClick={() => { const next = form.tool_filter!.filter(a => a !== action); setForm(f => ({ ...f, tool_filter: next.length > 0 ? next : [] })); }}
                              style={{ display: 'inline-flex', padding: 0, border: 'none', background: 'none', cursor: 'pointer', color: 'var(--icon)' }}
                            >
                              <X size={12} aria-hidden />
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                    <div style={{ position: 'relative' }}>
                      <input
                        type="text"
                        className="n-input"
                        aria-label="Search tools to add"
                        value={toolFilterInput}
                        onChange={e => { setToolFilterInput(e.target.value); setToolDropdownOpen(true); }}
                        onFocus={() => {
                          setToolDropdownOpen(true);
                          if (agentTools.length === 0 && at.agent_slug) {
                            import('../../lib/api').then(({ apiFetch }) =>
                              apiFetch('/api/playbooks/tools/all')
                                .then(r => r.ok ? r.json() : null)
                                .then(d => { if (d?.tools) setAgentTools(d.tools); })
                                .catch(() => {})
                            );
                          }
                        }}
                        onBlur={() => setTimeout(() => setToolDropdownOpen(false), 150)}
                        placeholder={agentTools.length > 0 ? 'Search tools to add…' : 'Loading tools…'}
                        style={{ width: '100%' }}
                      />
                      {toolDropdownOpen && agentTools.length > 0 && (() => {
                        const selected = new Set(form.tool_filter || []);
                        const filtered = agentTools
                          .filter(t => !selected.has(t.action))
                          .filter(t => !toolFilterInput || t.action.toLowerCase().includes(toolFilterInput.toLowerCase()) || t.description.toLowerCase().includes(toolFilterInput.toLowerCase()));
                        if (filtered.length === 0) return null;
                        return (
                          <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10, maxHeight: 180, overflowY: 'auto', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', boxShadow: '0 4px 12px rgba(26,26,26,0.08)', marginTop: 2 }}>
                            {filtered.map(t => (
                              <div
                                key={t.action}
                                className="n-option"
                                onMouseDown={e => {
                                  e.preventDefault();
                                  setForm(f => ({ ...f, tool_filter: [...(f.tool_filter || []), t.action] }));
                                  setToolFilterInput('');
                                }}
                                style={{ borderBottom: '1px solid var(--line-soft)' }}
                              >
                                <span style={{ fontWeight: 500 }}>{t.action}</span>
                                <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)', marginLeft: 6 }}>[{t.connector} · {t.method}]</span>
                                {t.description && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 1 }}>{t.description}</div>}
                              </div>
                            ))}
                          </div>
                        );
                      })()}
                    </div>
                  </>
                )}
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <Button size="sm" variant="primary" onClick={handleSave} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
                <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
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
  onSend?: (message: string, opts?: SendOptions) => void;
  /** Set when the last send in this conversation failed: re-sends it. */
  onRetry?: () => void;
  loading: boolean;
  openThread?: Thread | null;
  readOnly?: boolean;
}

export default function ThreadDetail({ thread, onAction, onWidgetAction, onSend, onRetry, loading, openThread, readOnly }: ThreadDetailProps) {
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
      const fw = m.display_blocks.find(b => isFullWidthBlock(b));
      if (fw && !foundNewerInlineBlock) {
        latestFullWidthBlock = fw;
        break;
      }
      // This message has display blocks but none are full-width — mark as newer inline
      if (!fw) foundNewerInlineBlock = true;
    }
  }
  const hasSplitLayout = !!latestFullWidthBlock;
  // An app fills the top pane and scrolls inside itself (its own dialogs then
  // open in view); every other full-width component scrolls with the pane.
  const fillsPane = latestFullWidthBlock?.component === 'app_runner';

  // --- Shared UI pieces ---

  const tabsRow = (
    <div style={{ padding: '0 24px' }}>
      <Tabs
        label="Thread view"
        items={TABS.map(t => ({ id: t.key, label: t.label, testId: `tab-${t.key}` }))}
        value={activeTab}
        onChange={(id) => setActiveTab(id as TabKey)}
      />
    </div>
  );

  const inputBar = !readOnly && onSend ? <InputBar onSend={onSend} loading={loading} highlight={!!openThread} /> : null;
  // Under the failure note of a send that didn't go through.
  const retryRow = !readOnly && onRetry && !loading ? (
    <div style={{ marginTop: '0.25rem' }}>
      <Button size="sm" variant="secondary" icon={RotateCcw} onClick={onRetry} data-testid="retry-send-btn">Try again</Button>
    </div>
  ) : null;

  return (
    <div ref={containerRef} style={{
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: 'var(--canvas)',
      userSelect: isDragging ? 'none' : undefined,
    }}>
      {/* Header — minimal: only AutomatedTaskHeader + admin tabs if needed.
          The tab strip draws its own rule; the saved-thread strip gets one
          only when there are no tabs under it. */}
      {!hasSplitLayout && (
        <div data-split-header>
          {thread.automated_task && (
            <div style={{ padding: '12px 24px', ...(isAdmin ? {} : { borderBottom: '1px solid var(--line)' }) }}>
              <AutomatedTaskHeader at={thread.automated_task} onUpdate={() => onAction(thread.id, 'reload')} onRun={onSend || (() => {})} />
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
            overflowY: fillsPane ? 'hidden' : 'auto',
            display: 'flex',
            flexDirection: 'column',
          }}>
            <div style={fillsPane
              ? { flex: 1, minHeight: 0, padding: '12px 24px', display: 'flex', flexDirection: 'column' }
              : { padding: '0.75rem 0.5rem 0.75rem 1.5rem', minHeight: '100%' }}>
              <DisplayBlockRenderer
                block={fillsPane ? { ...latestFullWidthBlock!, props: { ...latestFullWidthBlock!.props, fill: true } } : latestFullWidthBlock!}
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
                    {retryRow}
                    <ConversationExtras task={thread} loading={loading} onAction={onAction} isProcurement={isProcurement} isHr={isHr} isTerminal={isTerminal} isAdmin={!!isAdmin} />
                  </div>
                </>
              )}
              {activeTab === 'details' && <DetailsView task={thread} onAction={onAction} />}
              {activeTab === 'activity' && (
                <ActivityTimeline messages={messages} createdAt={thread.created_at} domain={thread.domain}
                  threadId={thread.id}
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
                  {retryRow}
                  <ConversationExtras task={thread} loading={loading} onAction={onAction} isProcurement={isProcurement} isHr={isHr} isTerminal={isTerminal} isAdmin={!!isAdmin} />
                </div>
              </>
            )}
            {activeTab === 'details' && <DetailsView task={thread} onAction={onAction} />}
            {activeTab === 'activity' && (
              <ActivityTimeline messages={messages} createdAt={thread.created_at} domain={thread.domain}
                threadId={thread.id}
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
