'use client';

import { useState, useMemo, useEffect, useRef } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import {
  ArrowRight, Bot, Brain, Check, ChevronRight, CircleCheck, CircleHelp, CircleX, ClipboardList, Copy,
  Inbox, MessageSquare, MessageSquareText, Pencil, Send, Sparkles, TriangleAlert, UserPlus, Wrench, Zap,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import type { ConversationMessage, LlmCall, ToolCallRecord } from '../../types';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon, { type IconTone } from '../ui/Icon';
import Tabs from '../ui/Tabs';

function classifyMessage(msg: ConversationMessage, index: number): {
  label: string;
  icon: LucideIcon;
} {
  const text = msg.text.toLowerCase();

  if (msg.role === 'user' && index === 0) {
    return { label: 'User request received', icon: Inbox };
  }
  if (msg.role === 'user') {
    return { label: 'User replied', icon: MessageSquare };
  }

  // Assistant messages — classify by content
  if (text.includes('changed from') || text.includes('updated from') || text.includes('updated and ready'))
    return { label: 'Thread revised', icon: Pencil };
  if (text.includes('which venue') || text.includes('which location') || text.includes('what venue'))
    return { label: 'Clarification requested: venue', icon: CircleHelp };
  if (text.includes('which product') || text.includes('what product'))
    return { label: 'Clarification requested: product', icon: CircleHelp };
  if (text.includes('how many') || text.includes('quantity'))
    return { label: 'Clarification requested: quantity', icon: CircleHelp };
  if (text.includes('?'))
    return { label: 'Clarification requested', icon: CircleHelp };
  if (text.includes('draft order') || text.includes('order created') || text.includes('ready for review') || text.includes('ready for your approval'))
    return { label: 'Draft order created', icon: ClipboardList };
  if (text.includes('approved'))
    return { label: 'Order approved', icon: CircleCheck };
  if (text.includes('submitted'))
    return { label: 'Order submitted', icon: Send };
  if (text.includes('set up') || text.includes('setup') || text.includes('onboarding'))
    return { label: 'Employee setup initiated', icon: UserPlus };

  return { label: 'Assistant responded', icon: Bot };
}

function formatTime(dateStr?: string | null): string {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    const day = d.getDate();
    const mon = d.toLocaleString([], { month: 'short' });
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    return `${day} ${mon} ${time}`;
  } catch {
    return '';
  }
}

export function CopyableThreadId({ threadId }: { threadId: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(threadId).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      title="Click to copy the thread ID"
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6, maxWidth: '100%',
        padding: '3px 8px', border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)',
        backgroundColor: copied ? 'var(--ok-bg)' : 'var(--bg)',
        color: copied ? 'var(--ok)' : 'var(--muted)',
        fontSize: 'var(--fs-2xs)', fontFamily: 'var(--font-mono)', lineHeight: 1.4,
        cursor: 'pointer', whiteSpace: 'nowrap',
      }}
    >
      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>Thread {threadId}</span>
      <Icon icon={copied ? Check : Copy} size="meta" />
      {copied && <span>Copied</span>}
    </button>
  );
}

// Prompts, payloads and results: the warm-dark code surface, monospace.
const CODE_STYLE: CSSProperties = {
  backgroundColor: 'var(--code-bg)',
  color: 'var(--code-text)',
  borderRadius: 'var(--radius-sm)',
  padding: '10px 12px',
  margin: 0,
  fontFamily: 'var(--font-mono)',
  fontSize: 'var(--fs-xs)',
  lineHeight: 1.5,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};

// Small figures beside a status: durations, token counts, the model name.
const META_STYLE: CSSProperties = {
  fontSize: 'var(--fs-xs)',
  color: 'var(--muted)',
  fontVariantNumeric: 'tabular-nums',
};

const TIME_STYLE: CSSProperties = { ...META_STYLE, whiteSpace: 'nowrap' };

// The expanded body of an LLM or tool call: a white card on the cream pane.
const DETAIL_CARD_STYLE: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
  margin: '8px 0 4px',
  padding: 14,
  fontSize: 'var(--fs-sm)',
  color: 'var(--text-soft)',
};

const NOTE_STYLE: CSSProperties = { color: 'var(--muted)', fontStyle: 'italic' };

const TOOL_STATUS_TONE: Partial<Record<ToolCallRecord['status'], BadgeTone>> = {
  executed: 'ok',
  approved: 'ok',
  failed: 'error',
  pending: 'info',
  pending_approval: 'warn',
};

function sentenceCase(s: string): string {
  const t = s.replace(/_/g, ' ');
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function DetailBox({ label, action, children }: { label: string; action?: ReactNode; children: ReactNode }) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 6 }}>
        <div className="n-eyebrow">{label}</div>
        {action}
      </div>
      {children}
    </div>
  );
}

function ErrorNote({ children, preserveLines }: { children: ReactNode; preserveLines?: boolean }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 8,
      padding: '8px 10px', borderRadius: 'var(--radius-sm)',
      backgroundColor: 'var(--error-bg)', color: 'var(--error)',
      whiteSpace: preserveLines ? 'pre-wrap' : undefined, wordBreak: 'break-word',
    }}>
      <Icon icon={TriangleAlert} size="dense" style={{ marginTop: 2 }} />
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

function LlmCallDetail({ call }: { call: LlmCall }) {
  const [showRaw, setShowRaw] = useState(false);
  const ok = call.status === 'success';

  return (
    <div className="n-card" style={DETAIL_CARD_STYLE}>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <Badge tone={ok ? 'ok' : 'error'}>{ok ? 'Success' : 'Error'}</Badge>
        {call.duration_ms != null && (
          <span style={{ ...META_STYLE, marginLeft: 'auto' }}>{call.duration_ms}ms</span>
        )}
        {(call.input_tokens != null || call.output_tokens != null) && (
          <span style={META_STYLE}>
            {callUsageLine(call)}
          </span>
        )}
      </div>

      {call.error_message && <ErrorNote preserveLines>{call.error_message}</ErrorNote>}

      {call.system_prompt ? (
        <DetailBox label="System prompt">
          <pre style={CODE_STYLE}>{call.system_prompt}</pre>
        </DetailBox>
      ) : (
        <div style={NOTE_STYLE}>Loading system prompt…</div>
      )}

      {call.tools_provided && call.tools_provided.length > 0 && (
        <DetailBox label={`Tools (${call.tools_provided.length})`}>
          <pre style={CODE_STYLE}>
            {call.tools_provided.map((t: Record<string, unknown>) =>
              `- ${t.name}: ${t.description || ''}`
            ).join('\n')}
          </pre>
        </DetailBox>
      )}

      {call.user_prompt && (
        <DetailBox label="User prompt">
          <pre style={CODE_STYLE}>{call.user_prompt}</pre>
        </DetailBox>
      )}

      {(call.raw_response || call.parsed_response) && (
        <DetailBox
          label="Response"
          action={call.raw_response && call.parsed_response ? (
            <Button variant="link" size="sm" onClick={() => setShowRaw(!showRaw)}>
              {showRaw ? 'Show parsed' : 'Show raw'}
            </Button>
          ) : undefined}
        >
          <pre style={CODE_STYLE}>
            {showRaw || !call.parsed_response
              ? call.raw_response
              : JSON.stringify(call.parsed_response, null, 2)}
          </pre>
        </DetailBox>
      )}
    </div>
  );
}

function ToolCallResult({ tc }: { tc: ToolCallRecord }) {
  const [showFull, setShowFull] = useState(false);
  const hasSlimmed = !!tc.slimmed_content;

  return (
    <div>
      {hasSlimmed && (
        <div style={{ marginBottom: 10 }}>
          <Tabs
            label="Tool result view"
            items={[{ id: 'slimmed', label: 'What LLM saw' }, { id: 'full', label: 'Full result' }]}
            value={showFull ? 'full' : 'slimmed'}
            onChange={(id) => setShowFull(id === 'full')}
          />
        </div>
      )}
      <DetailBox label={hasSlimmed && !showFull ? 'Result (slimmed for LLM)' : 'Result'}>
        <pre style={CODE_STYLE}>
          {hasSlimmed && !showFull
            ? tc.slimmed_content
            : JSON.stringify(tc.result_payload, null, 2)
          }
        </pre>
      </DetailBox>
    </div>
  );
}

function ToolCallDetail({ tc }: { tc: ToolCallRecord }) {
  return (
    <div className="n-card" style={DETAIL_CARD_STYLE}>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <Badge tone={TOOL_STATUS_TONE[tc.status] ?? 'neutral'}>{sentenceCase(tc.status)}</Badge>
        {tc.method && <Badge>{tc.method}</Badge>}
        {tc.duration_ms != null && (
          <span style={{ ...META_STYLE, marginLeft: 'auto' }}>{tc.duration_ms}ms</span>
        )}
      </div>

      {tc.error_message && <ErrorNote>{tc.error_message}</ErrorNote>}

      {tc.input_params && (
        <DetailBox label="Input">
          <pre style={CODE_STYLE}>{JSON.stringify(tc.input_params, null, 2)}</pre>
        </DetailBox>
      )}

      {tc.result_payload ? (
        <ToolCallResult tc={tc} />
      ) : (
        <div style={NOTE_STYLE}>Result not yet available</div>
      )}
    </div>
  );
}

// ── Timeline layout ──
// The rail runs down the left gutter; each event's dot sits on it. LLM calls
// made inside another step are indented, and their (smaller) dot still sits
// on the rail.
const RAIL_GUTTER = 28;
const RAIL_CENTRE = 8;
const NEST_INDENT = 24;

function TimelineDot({ color, small, nested }: { color: string; small?: boolean; nested?: boolean }) {
  const size = small ? 6 : 8;
  return (
    <span aria-hidden style={{
      position: 'absolute',
      left: RAIL_CENTRE - size / 2 - RAIL_GUTTER - (nested ? NEST_INDENT : 0),
      // Centred on the first line of the row (line-height 1.45).
      top: `calc(0.75em - ${size / 2}px)`,
      width: size, height: size, borderRadius: '50%',
      backgroundColor: color,
      boxShadow: '0 0 0 3px var(--canvas)',
    }} />
  );
}

function EventLabel({ icon, tone = 'muted', style, children }: { icon: LucideIcon; tone?: IconTone; style?: CSSProperties; children: ReactNode }) {
  return (
    <span style={style}>
      <Icon icon={icon} size="dense" tone={tone} style={{ verticalAlign: '-2px', marginRight: 6 }} />
      {children}
    </span>
  );
}

function TimelineRow({ dotColor, smallDot, nested, secondary, spacing = 8, time, onClick, expanded, after, children }: {
  dotColor: string;
  smallDot?: boolean;
  nested?: boolean;
  /** Secondary rows (thinking steps) use the smaller text size. */
  secondary?: boolean;
  spacing?: number;
  time?: string;
  onClick?: () => void;
  /** Set for rows that open: draws the chevron. */
  expanded?: boolean;
  after?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div style={{
      position: 'relative',
      marginLeft: nested ? NEST_INDENT : 0,
      marginBottom: spacing,
      fontSize: secondary ? 'var(--fs-sm)' : 'var(--fs-base)',
    }}>
      <div onClick={onClick} style={{ display: 'flex', alignItems: 'baseline', gap: 8, cursor: onClick ? 'pointer' : undefined }}>
        <TimelineDot color={dotColor} small={smallDot} nested={nested} />
        {/* The time is the last item of the wrapping line, pushed right: on a
            wide pane it ends the first line; on a phone it drops under the
            label instead of squeezing it into a narrow column. */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', columnGap: 8, rowGap: 2 }}>
          {children}
          {time && <span style={{ ...TIME_STYLE, marginLeft: 'auto' }}>{time}</span>}
        </div>
        <span style={{ flex: '0 0 12px', width: 12 }}>
          {expanded !== undefined && (
            <Icon icon={ChevronRight} size="meta" tone="muted" style={{ transition: 'transform 0.15s', transform: expanded ? 'rotate(90deg)' : 'none' }} />
          )}
        </span>
      </div>
      {after}
    </div>
  );
}

interface ApprovalInfo {
  action: string;
  performed_by: string;
  performed_at: string;
}

interface IntegrationRunInfo {
  connector: string;
  status: string;
  reference: string | null;
  submitted_at: string;
  error: string | null;
}

interface ActivityTimelineProps {
  messages: ConversationMessage[];
  createdAt: string;
  domain: string;
  threadId?: string;
  llmCalls?: LlmCall[];
  toolCalls?: ToolCallRecord[];
  thinkingSteps?: string[];
  approval?: ApprovalInfo | null;
  integrationRun?: IntegrationRunInfo | null;
}

const LLM_CALL_TYPE_LABELS: Record<string, string> = {
  routing: 'Routing LLM call',
  tool_use: 'Tool-use LLM call',
  interpretation: 'Interpretation LLM call',
  execution: 'Execution LLM call',
  spec_generation: 'Spec generation LLM call',
};

/** Every token one call processed: full-price input, both cache parts, output. */
function callTokenTotal(c: LlmCall): number {
  return (c.input_tokens ?? 0) + (c.cache_read_tokens ?? 0) + (c.cache_write_tokens ?? 0) + (c.output_tokens ?? 0);
}

function callUsageLine(c: LlmCall): string {
  const parts = [`${(c.input_tokens ?? 0).toLocaleString()} in`];
  if (c.cache_read_tokens) parts.push(`${c.cache_read_tokens.toLocaleString()} cache read`);
  if (c.cache_write_tokens) parts.push(`${c.cache_write_tokens.toLocaleString()} cache write`);
  parts.push(`${(c.output_tokens ?? 0).toLocaleString()} out`);
  if (c.cost_usd != null) parts.push(`$${c.cost_usd.toFixed(3)}`);
  return parts.join(' · ');
}

export default function ActivityTimeline({ messages, createdAt, domain, threadId, llmCalls, toolCalls, thinkingSteps, approval, integrationRun }: ActivityTimelineProps) {
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set());
  const [expandedMessages, setExpandedMessages] = useState<Set<number>>(new Set());
  // Enriched LLM calls — keyed by call ID, populated lazily when any call lacks system_prompt
  const [enrichedCalls, setEnrichedCalls] = useState<Record<string, LlmCall>>({});
  const fetchingRef = useRef(false);

  // Fetch full call data if any displayed llm_call is missing system_prompt
  const needsFetch = (llmCalls || []).some(c => !c.system_prompt);

  useEffect(() => {
    if (!needsFetch || !threadId || fetchingRef.current) return;
    fetchingRef.current = true;
    apiFetch(`/api/threads/${threadId}`)
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data) {
          const calls: LlmCall[] = data.llm_calls || [];
          const byId: Record<string, LlmCall> = {};
          for (const c of calls) byId[c.id] = c;
          setEnrichedCalls(byId);
        }
      })
      .catch(() => {})
      .finally(() => { fetchingRef.current = false; });
  }, [needsFetch, threadId]);

  const summary = useMemo(() => {
    const calls = llmCalls || [];
    const tools = (toolCalls || []).filter(tc => tc.status !== 'pending_approval');
    let inputTokens = 0;
    let outputTokens = 0;
    let cacheRead = 0;
    let cacheWrite = 0;
    let billable = 0;
    let cost = 0;
    let llmDuration = 0;
    let toolDuration = 0;
    for (const c of calls) {
      inputTokens += c.input_tokens ?? 0;
      outputTokens += c.output_tokens ?? 0;
      cacheRead += c.cache_read_tokens ?? 0;
      cacheWrite += c.cache_write_tokens ?? 0;
      billable += c.billable_tokens ?? (c.input_tokens ?? 0) + (c.output_tokens ?? 0);
      cost += c.cost_usd ?? 0;
      llmDuration += c.duration_ms ?? 0;
    }
    for (const t of tools) {
      toolDuration += t.duration_ms ?? 0;
    }
    // Every token the model processed: full-price input, both cache parts, output.
    const totalTokens = inputTokens + cacheRead + cacheWrite + outputTokens;
    const totalDuration = ((llmDuration + toolDuration) / 1000).toFixed(1);
    return {
      inputTokens, outputTokens, cacheRead, cacheWrite, billable, cost, totalTokens,
      llmCount: calls.length, toolCount: tools.length, totalDuration,
    };
  }, [llmCalls, toolCalls]);

  if (!messages || messages.length === 0) return null;

  const toggle = (id: string) => {
    setExpandedItems(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleMsg = (idx: number) => {
    setExpandedMessages(prev => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx); else next.add(idx);
      return next;
    });
  };

  // Show ALL routing calls (initial + follow-up reclassifications)
  const routingCalls = (llmCalls || []).filter(c => c.call_type === 'routing');
  const firstRoutingKey = routingCalls.length > 0 ? routingCalls[0].created_at : createdAt;

  const routingEvents = routingCalls.length > 0
    ? routingCalls.flatMap((rc, idx) => {
        const isFollowup = idx > 0 || (rc.parsed_response as Record<string, unknown>)?.action != null;
        const action = (rc.parsed_response as Record<string, unknown>)?.action as string | undefined;
        const reason = (rc.parsed_response as Record<string, unknown>)?.reason as string | undefined;

        if (isFollowup && action) {
          return [{
            type: 'routing' as const,
            label: `Supervisor: ${action}${reason ? ` (${reason})` : ''}`,
            icon: Brain,
            time: formatTime(rc.created_at),
            sortKey: rc.created_at,
            sortOrder: 0,
          }];
        }
        return [
          { type: 'routing' as const, label: 'Supervisor analysed request', icon: Brain, time: formatTime(rc.created_at), sortKey: rc.created_at, sortOrder: 0 },
          { type: 'routing' as const, label: `Routed to ${domain} agent`, icon: ArrowRight, time: formatTime(rc.created_at), sortKey: rc.created_at, sortOrder: 1 },
        ];
      })
    : [
        { type: 'routing' as const, label: 'Supervisor analysed request', icon: Brain, time: formatTime(firstRoutingKey), sortKey: firstRoutingKey, sortOrder: 0 },
        { type: 'routing' as const, label: `Routed to ${domain} agent`, icon: ArrowRight, time: formatTime(firstRoutingKey), sortKey: firstRoutingKey, sortOrder: 1 },
      ];

  const messageEvents = messages.map((m, i) => {
    const classified = classifyMessage(m, i);
    const msgTime = m.created_at || createdAt;
    return { type: 'message' as const, ...classified, time: formatTime(msgTime), text: m.text, role: m.role, sortKey: msgTime, sortOrder: 0, index: i };
  });

  const llmEvents = (llmCalls || []).map((call, idx) => {
    const isNested = call.call_type !== 'routing' && call.call_type !== 'tool_use';
    return {
      type: 'llm' as const,
      call,
      label: LLM_CALL_TYPE_LABELS[call.call_type] ?? `LLM call (${call.call_type})`,
      icon: Sparkles,
      time: formatTime(call.created_at),
      sortKey: call.created_at,
      sortOrder: 0,
      idx,
      nested: isNested,
    };
  });

  const toolEvents = (toolCalls || []).filter(tc => tc.status !== 'pending_approval').map((tc, idx) => ({
    type: 'tool' as const,
    tc,
    label: `${tc.action} (${tc.connector_name})`,
    icon: Wrench,
    time: formatTime(tc.created_at),
    sortKey: tc.created_at,
    sortOrder: 0,
    idx,
  }));

  const approvalEvents: { type: 'approval'; label: string; icon: LucideIcon; detail: string; time: string; sortKey: string; sortOrder: number }[] = [];
  if (approval) {
    const isApproved = approval.action === 'approved';
    approvalEvents.push({
      type: 'approval' as const,
      label: isApproved ? 'Thread approved' : 'Thread rejected',
      icon: isApproved ? CircleCheck : CircleX,
      detail: `by ${approval.performed_by}`,
      time: formatTime(approval.performed_at),
      sortKey: approval.performed_at,
      sortOrder: 0,
    });
  }

  const submissionEvents: { type: 'submission'; label: string; icon: LucideIcon; detail: string; time: string; sortKey: string; sortOrder: number }[] = [];
  if (integrationRun) {
    const isSuccess = integrationRun.status === 'success';
    const connectorLabel = integrationRun.connector;
    let detail = `via ${connectorLabel}`;
    if (isSuccess && integrationRun.reference) detail += ` — ref: ${integrationRun.reference}`;
    if (!isSuccess && integrationRun.error) detail += ` — ${integrationRun.error}`;
    submissionEvents.push({
      type: 'submission' as const,
      label: isSuccess ? 'Submitted to external system' : 'Submission failed',
      icon: isSuccess ? Send : TriangleAlert,
      detail,
      time: formatTime(integrationRun.submitted_at),
      sortKey: integrationRun.submitted_at,
      sortOrder: 0,
    });
  }

  // Thinking steps — SSE events captured during processing.
  // Steps may have an embedded timestamp prefix: "[ts:ISO8601] text"
  const firstLlmTime = llmEvents.length > 0 ? llmEvents[0].sortKey : createdAt;
  const TS_PREFIX_RE = /^\[ts:([^\]]+)\]\s*/;
  const thinkingEvents = (thinkingSteps || []).map((step, idx) => {
    const tsMatch = step.match(TS_PREFIX_RE);
    const stepTime = tsMatch ? tsMatch[1] : '';
    const stepText = tsMatch ? step.slice(tsMatch[0].length) : step;
    const isReasoning = stepText.startsWith('[reasoning] ');
    return {
      type: 'thinking' as const,
      label: isReasoning ? stepText.slice('[reasoning] '.length) : stepText,
      icon: isReasoning ? MessageSquareText : Zap,
      isReasoning,
      time: stepTime ? formatTime(stepTime) : '',
      sortKey: stepTime || firstLlmTime,
      sortOrder: stepTime ? 0 : 2 + idx,
      idx,
    };
  });

  type TimelineEvent =
    | (typeof routingEvents)[number]
    | (typeof messageEvents)[number]
    | (typeof llmEvents)[number]
    | (typeof toolEvents)[number]
    | (typeof thinkingEvents)[number]
    | (typeof approvalEvents)[number]
    | (typeof submissionEvents)[number];

  const allEvents: TimelineEvent[] = [
    ...routingEvents,
    ...thinkingEvents,
    ...llmEvents,
    ...toolEvents,
    ...messageEvents,
    ...approvalEvents,
    ...submissionEvents,
  ].sort((a, b) => {
    const timeA = new Date(a.sortKey).getTime();
    const timeB = new Date(b.sortKey).getTime();
    if (timeA !== timeB) return timeA - timeB;
    return a.sortOrder - b.sortOrder;
  });

  const figure: CSSProperties = { color: 'var(--text)', fontWeight: 600 };

  return (
    <div style={{ marginBottom: 16, lineHeight: 1.45 }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap',
        gap: 8, marginBottom: 10,
      }}>
        <div className="n-eyebrow">
          Activity
        </div>
        {threadId && <CopyableThreadId threadId={threadId} />}
      </div>
      {/* Run totals. The bold figure leads each item, so the gap separates
          them — no dots, which strand at a line end when the strip wraps. */}
      {summary.totalTokens > 0 && (
        <div style={{
          display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', columnGap: 18, rowGap: 2,
          marginBottom: 16, padding: '8px 12px',
          backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)',
          fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', fontVariantNumeric: 'tabular-nums',
        }}>
          {summary.cost > 0 && <span><strong style={figure}>${summary.cost.toFixed(2)}</strong></span>}
          <span title="Every token the model processed, cache included">
            <strong style={figure}>{summary.totalTokens.toLocaleString()}</strong> tokens{' '}
            <span style={{ color: 'var(--muted)' }}>
              ({summary.inputTokens.toLocaleString()} full-price in · {summary.cacheRead.toLocaleString()} cache read · {summary.cacheWrite.toLocaleString()} cache write · {summary.outputTokens.toLocaleString()} out)
            </span>
          </span>
          <span title="What plan limits count: cache reads count ~0.05, cache writes 1.25">
            <strong style={figure}>{summary.billable.toLocaleString()}</strong> billable
          </span>
          {summary.cacheWrite > 0 && (
            <span title="Tokens read back from the cache for each token written to it — higher is better">
              cache <strong style={figure}>{(summary.cacheRead / summary.cacheWrite).toFixed(1)}:1</strong>
            </span>
          )}
          <span><strong style={figure}>{summary.llmCount}</strong> LLM calls</span>
          <span><strong style={figure}>{summary.toolCount}</strong> tool calls</span>
          <span><strong style={figure}>{summary.totalDuration}s</strong></span>
        </div>
      )}
      <div style={{ position: 'relative', paddingLeft: RAIL_GUTTER }}>
        <div aria-hidden style={{
          position: 'absolute', left: RAIL_CENTRE - 1, top: 8, bottom: 8,
          width: 2, backgroundColor: 'var(--line)', borderRadius: 1,
        }} />

        {allEvents.map((evt, i) => {
          if (evt.type === 'llm') {
            const key = `llm-${evt.call.id}`;
            const isExpanded = expandedItems.has(key);
            // Use enriched call data if available (has system_prompt etc.)
            const enrichedCall = enrichedCalls[evt.call.id];
            const displayCall = enrichedCall ?? evt.call;
            return (
              <TimelineRow
                key={key}
                nested={evt.nested}
                smallDot={evt.nested}
                dotColor={evt.nested ? 'var(--muted-soft)' : 'var(--icon)'}
                time={evt.time}
                onClick={() => toggle(key)}
                expanded={isExpanded}
                after={isExpanded && <LlmCallDetail call={displayCall} />}
              >
                <EventLabel icon={evt.icon} style={{ color: 'var(--text)' }}>{evt.label}</EventLabel>
                <span style={META_STYLE}>{displayCall.model}</span>
                {displayCall.duration_ms != null && (
                  <Badge>{displayCall.duration_ms}ms</Badge>
                )}
                {displayCall.input_tokens != null && (
                  <Badge>
                    {callTokenTotal(displayCall).toLocaleString()} tokens
                    {displayCall.cost_usd != null && ` · $${displayCall.cost_usd.toFixed(3)}`}
                  </Badge>
                )}
              </TimelineRow>
            );
          }

          if (evt.type === 'tool') {
            const key = `tool-${evt.tc.id}`;
            const isExpanded = expandedItems.has(key);
            const isFailed = evt.tc.status === 'failed';
            return (
              <TimelineRow
                key={key}
                dotColor={isFailed ? 'var(--error)' : 'var(--icon)'}
                time={evt.time}
                onClick={() => toggle(key)}
                expanded={isExpanded}
                after={isExpanded && <ToolCallDetail tc={evt.tc} />}
              >
                <EventLabel icon={evt.icon} tone={isFailed ? 'inherit' : 'muted'} style={{ color: isFailed ? 'var(--error)' : 'var(--text)' }}>
                  {evt.label}
                </EventLabel>
                {evt.tc.duration_ms != null && (
                  <Badge>{evt.tc.duration_ms}ms</Badge>
                )}
                {isFailed && <Badge tone="error">Failed</Badge>}
              </TimelineRow>
            );
          }

          if (evt.type === 'approval') {
            const isApproved = evt.label === 'Thread approved';
            return (
              <TimelineRow key={`approval-${i}`} dotColor={isApproved ? 'var(--ok)' : 'var(--error)'} time={evt.time}>
                <EventLabel icon={evt.icon} style={{ color: 'var(--text)', fontWeight: 600 }}>{evt.label}</EventLabel>
                <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{evt.detail}</span>
              </TimelineRow>
            );
          }

          if (evt.type === 'submission') {
            const isSuccess = evt.label === 'Submitted to external system';
            return (
              <TimelineRow
                key={`submission-${i}`}
                dotColor={isSuccess ? 'var(--ok)' : 'var(--error)'}
                time={evt.time}
                after={<div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{evt.detail}</div>}
              >
                <EventLabel icon={evt.icon} style={{ color: 'var(--text)', fontWeight: 600 }}>{evt.label}</EventLabel>
              </TimelineRow>
            );
          }

          if (evt.type === 'thinking') {
            return (
              <TimelineRow key={`thinking-${evt.idx}`} secondary spacing={6} dotColor="var(--muted-soft)" time={evt.time}>
                <EventLabel icon={evt.icon} style={{ color: 'var(--muted)' }}>{evt.label}</EventLabel>
                <Badge tone={evt.isReasoning ? 'info' : 'neutral'}>
                  {evt.isReasoning ? 'Reasoning' : 'Event'}
                </Badge>
              </TimelineRow>
            );
          }

          // Routing or message events
          if (evt.type === 'message') {
            const isUser = evt.role === 'user';
            const isExpanded = expandedMessages.has(evt.index);
            const preview = evt.text.length > 120 ? evt.text.slice(0, 120) + '…' : evt.text;
            return (
              <TimelineRow
                key={`msg-${i}`}
                dotColor="var(--icon)"
                time={evt.time}
                onClick={() => toggleMsg(evt.index)}
                expanded={evt.text.length > 120 ? isExpanded : undefined}
                after={
                  <div style={{
                    marginTop: 4,
                    padding: '8px 10px',
                    backgroundColor: isUser ? 'var(--surface-alt)' : 'var(--bg)',
                    border: `1px solid ${isUser ? 'var(--surface-alt)' : 'var(--line)'}`,
                    borderRadius: 'var(--radius)',
                    fontSize: 'var(--fs-sm)',
                    color: 'var(--text-soft)',
                    lineHeight: 1.45,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}>
                    {isExpanded ? evt.text : preview}
                    {evt.text.length > 120 && (
                      <span style={{ color: 'var(--muted)', marginLeft: '0.25rem', cursor: 'pointer' }}>
                        {isExpanded ? ' (less)' : ''}
                      </span>
                    )}
                  </div>
                }
              >
                <EventLabel icon={evt.icon} style={{ color: 'var(--text)' }}>{evt.label}</EventLabel>
                <Badge>Conversation</Badge>
              </TimelineRow>
            );
          }

          // Routing events. A follow-up's label carries the supervisor's whole
          // reason: let it fill the line and wrap beside the time on a wide
          // pane (the time drops under it only when the pane is narrow).
          return (
            <TimelineRow key={`routing-${i}`} dotColor="var(--muted-soft)" time={evt.time}>
              <EventLabel icon={evt.icon} style={{ color: 'var(--text-soft)', flex: '1 1 0', minWidth: 'min(100%, 260px)' }}>{evt.label}</EventLabel>
            </TimelineRow>
          );
        })}
      </div>
    </div>
  );
}
