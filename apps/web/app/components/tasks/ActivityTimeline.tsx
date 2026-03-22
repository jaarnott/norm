'use client';

import { useState, useMemo } from 'react';
import type { ConversationMessage, LlmCall, ToolCallRecord } from '../../types';

function classifyMessage(msg: ConversationMessage, index: number): {
  label: string;
  icon: string;
} {
  const text = msg.text.toLowerCase();

  if (msg.role === 'user' && index === 0) {
    return { label: 'User request received', icon: '📩' };
  }
  if (msg.role === 'user') {
    return { label: 'User replied', icon: '💬' };
  }

  // Assistant messages — classify by content
  if (text.includes('changed from') || text.includes('updated from') || text.includes('updated and ready'))
    return { label: 'Task revised', icon: '✏️' };
  if (text.includes('which venue') || text.includes('which location') || text.includes('what venue'))
    return { label: 'Clarification requested: venue', icon: '❓' };
  if (text.includes('which product') || text.includes('what product'))
    return { label: 'Clarification requested: product', icon: '❓' };
  if (text.includes('how many') || text.includes('quantity'))
    return { label: 'Clarification requested: quantity', icon: '❓' };
  if (text.includes('?'))
    return { label: 'Clarification requested', icon: '❓' };
  if (text.includes('draft order') || text.includes('order created') || text.includes('ready for review') || text.includes('ready for your approval'))
    return { label: 'Draft order created', icon: '📋' };
  if (text.includes('approved'))
    return { label: 'Order approved', icon: '✅' };
  if (text.includes('submitted'))
    return { label: 'Order submitted', icon: '🚀' };
  if (text.includes('set up') || text.includes('setup') || text.includes('onboarding'))
    return { label: 'Employee setup initiated', icon: '👤' };

  return { label: 'Assistant responded', icon: '🤖' };
}

function formatTime(dateStr?: string | null): string {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '';
  }
}

const CODE_STYLE: React.CSSProperties = {
  backgroundColor: '#f5f5f5',
  border: '1px solid #e2e2e2',
  borderRadius: 4,
  padding: '0.5rem',
  margin: 0,
  fontSize: '0.7rem',
  lineHeight: 1.4,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};

const SECTION_LABEL_STYLE: React.CSSProperties = {
  fontSize: '0.65rem',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: '#999',
  marginBottom: '0.25rem',
};

function DetailBox({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '0.5rem' }}>
      <div style={SECTION_LABEL_STYLE}>{label}</div>
      {children}
    </div>
  );
}

function LlmCallDetail({ call }: { call: LlmCall }) {
  const [showRaw, setShowRaw] = useState(false);

  return (
    <div style={{
      marginTop: '0.4rem',
      marginBottom: '0.5rem',
      marginLeft: '0.25rem',
      border: '1px solid #e8e8e8',
      borderRadius: 6,
      padding: '0.75rem',
      backgroundColor: '#fafafa',
      fontSize: '0.75rem',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem' }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          backgroundColor: call.status === 'success' ? '#48bb78' : '#f56565',
          display: 'inline-block',
        }} />
        <span style={{ fontWeight: 600, color: call.status === 'success' ? '#2f855a' : '#c53030' }}>
          {call.status === 'success' ? 'Success' : 'Error'}
        </span>
        {call.duration_ms != null && (
          <span style={{ color: '#999', marginLeft: 'auto' }}>{call.duration_ms}ms</span>
        )}
        {(call.input_tokens != null || call.output_tokens != null) && (
          <span style={{ color: '#bbb', fontSize: '0.68rem', marginLeft: '0.5rem' }}>
            {call.input_tokens ?? 0}in / {call.output_tokens ?? 0}out tokens
          </span>
        )}
      </div>

      {call.error_message && (
        <div style={{
          padding: '0.4rem 0.6rem', backgroundColor: '#fff5f5',
          border: '1px solid #fed7d7', borderRadius: 4, color: '#c53030',
          marginBottom: '0.5rem', whiteSpace: 'pre-wrap',
        }}>
          {call.error_message}
        </div>
      )}

      {call.system_prompt ? (
        <DetailBox label="System Prompt">
          <pre style={CODE_STYLE}>{call.system_prompt}</pre>
        </DetailBox>
      ) : (
        <div style={{ color: '#bbb', fontSize: '0.7rem', marginBottom: '0.5rem', fontStyle: 'italic' }}>
          System prompt not available (open Activity tab after the call completes to load full data)
        </div>
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
        <DetailBox label="User Prompt">
          <pre style={CODE_STYLE}>{call.user_prompt}</pre>
        </DetailBox>
      )}

      {(call.raw_response || call.parsed_response) && (
        <DetailBox label="Response">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
            {call.raw_response && call.parsed_response && (
              <button
                onClick={() => setShowRaw(!showRaw)}
                style={{
                  fontSize: '0.6rem', padding: '0.1rem 0.4rem', borderRadius: 3,
                  border: '1px solid #ddd', backgroundColor: '#fff', cursor: 'pointer', color: '#666',
                }}
              >
                {showRaw ? 'Parsed' : 'Raw'}
              </button>
            )}
          </div>
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

function ToolCallDetail({ tc }: { tc: ToolCallRecord }) {
  return (
    <div style={{
      marginTop: '0.4rem',
      marginBottom: '0.5rem',
      marginLeft: '0.25rem',
      border: '1px solid #e8e8e8',
      borderRadius: 6,
      padding: '0.75rem',
      backgroundColor: '#fafafa',
      fontSize: '0.75rem',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem' }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          backgroundColor: tc.status === 'failed' ? '#f56565' : '#48bb78',
          display: 'inline-block',
        }} />
        <span style={{ fontWeight: 600, color: tc.status === 'failed' ? '#c53030' : '#2f855a' }}>
          {tc.status}
        </span>
        <span style={{
          fontSize: '0.6rem', fontWeight: 600, padding: '0.1rem 0.35rem',
          borderRadius: 3, backgroundColor: '#e8f0fe', color: '#1a56db',
        }}>
          {tc.method}
        </span>
        {tc.duration_ms != null && (
          <span style={{ color: '#999', marginLeft: 'auto' }}>{tc.duration_ms}ms</span>
        )}
      </div>

      {tc.error_message && (
        <div style={{
          padding: '0.4rem 0.6rem', backgroundColor: '#fff5f5',
          border: '1px solid #fed7d7', borderRadius: 4, color: '#c53030',
          marginBottom: '0.5rem',
        }}>
          {tc.error_message}
        </div>
      )}

      {tc.input_params && (
        <DetailBox label="Input">
          <pre style={CODE_STYLE}>{JSON.stringify(tc.input_params, null, 2)}</pre>
        </DetailBox>
      )}

      {tc.result_payload ? (
        <DetailBox label="Result">
          <pre style={CODE_STYLE}>{JSON.stringify(tc.result_payload, null, 2)}</pre>
        </DetailBox>
      ) : (
        <div style={{ color: '#bbb', fontSize: '0.7rem', fontStyle: 'italic' }}>
          Result not yet available
        </div>
      )}
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

export default function ActivityTimeline({ messages, createdAt, domain, llmCalls, toolCalls, thinkingSteps, approval, integrationRun }: ActivityTimelineProps) {
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set());
  const [expandedMessages, setExpandedMessages] = useState<Set<number>>(new Set());

  const summary = useMemo(() => {
    const calls = llmCalls || [];
    const tools = (toolCalls || []).filter(tc => tc.status !== 'pending_approval');
    let inputTokens = 0;
    let outputTokens = 0;
    let llmDuration = 0;
    let toolDuration = 0;
    for (const c of calls) {
      inputTokens += c.input_tokens ?? 0;
      outputTokens += c.output_tokens ?? 0;
      llmDuration += c.duration_ms ?? 0;
    }
    for (const t of tools) {
      toolDuration += t.duration_ms ?? 0;
    }
    const totalTokens = inputTokens + outputTokens;
    const totalDuration = ((llmDuration + toolDuration) / 1000).toFixed(1);
    return { inputTokens, outputTokens, totalTokens, llmCount: calls.length, toolCount: tools.length, totalDuration };
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

  const routingCall = (llmCalls || []).find(c => c.call_type === 'routing');
  const routingSortKey = routingCall ? routingCall.created_at : createdAt;

  const routingEvents = [
    { type: 'routing' as const, label: 'Supervisor analysed request', icon: '🧠', time: formatTime(routingSortKey), sortKey: routingSortKey, sortOrder: 0 },
    { type: 'routing' as const, label: `Routed to ${domain} agent`, icon: '➡️', time: formatTime(routingSortKey), sortKey: routingSortKey, sortOrder: 1 },
  ];

  const messageEvents = messages.map((m, i) => {
    const classified = classifyMessage(m, i);
    const msgTime = m.created_at || createdAt;
    return { type: 'message' as const, ...classified, time: formatTime(msgTime), text: m.text, role: m.role, sortKey: msgTime, sortOrder: 0, index: i };
  });

  const llmEvents = (llmCalls || []).map((call, idx) => ({
    type: 'llm' as const,
    call,
    label: LLM_CALL_TYPE_LABELS[call.call_type] ?? `LLM call (${call.call_type})`,
    icon: '⚡',
    time: formatTime(call.created_at),
    sortKey: call.created_at,
    sortOrder: 0,
    idx,
  }));

  const toolEvents = (toolCalls || []).filter(tc => tc.status !== 'pending_approval').map((tc, idx) => ({
    type: 'tool' as const,
    tc,
    label: `${tc.action} (${tc.connector_name})`,
    icon: '⚙️',
    time: formatTime(tc.created_at),
    sortKey: tc.created_at,
    sortOrder: 0,
    idx,
  }));

  const approvalEvents: { type: 'approval'; label: string; icon: string; detail: string; time: string; sortKey: string; sortOrder: number }[] = [];
  if (approval) {
    const isApproved = approval.action === 'approved';
    approvalEvents.push({
      type: 'approval' as const,
      label: isApproved ? 'Task approved' : 'Task rejected',
      icon: isApproved ? '✅' : '❌',
      detail: `by ${approval.performed_by}`,
      time: formatTime(approval.performed_at),
      sortKey: approval.performed_at,
      sortOrder: 0,
    });
  }

  const submissionEvents: { type: 'submission'; label: string; icon: string; detail: string; time: string; sortKey: string; sortOrder: number }[] = [];
  if (integrationRun) {
    const isSuccess = integrationRun.status === 'success';
    const connectorLabel = integrationRun.connector;
    let detail = `via ${connectorLabel}`;
    if (isSuccess && integrationRun.reference) detail += ` — ref: ${integrationRun.reference}`;
    if (!isSuccess && integrationRun.error) detail += ` — ${integrationRun.error}`;
    submissionEvents.push({
      type: 'submission' as const,
      label: isSuccess ? 'Submitted to external system' : 'Submission failed',
      icon: isSuccess ? '🚀' : '⚠️',
      detail,
      time: formatTime(integrationRun.submitted_at),
      sortKey: integrationRun.submitted_at,
      sortOrder: 0,
    });
  }

  // Thinking steps — SSE events shown to user during processing (no timestamps).
  // Place them between routing and the first LLM call using interpolated sort keys.
  const firstLlmTime = llmEvents.length > 0 ? llmEvents[0].sortKey : createdAt;
  const thinkingEvents = (thinkingSteps || []).map((step, idx) => {
    const isReasoning = step.startsWith('[reasoning] ');
    return {
      type: 'thinking' as const,
      label: isReasoning ? step.slice('[reasoning] '.length) : step,
      icon: isReasoning ? '🗨️' : '💭',
      isReasoning,
      time: '',
      sortKey: firstLlmTime,
      sortOrder: 2 + idx, // after routing (sortOrder 0/1), before LLM calls (sortOrder 0 but later timestamp)
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

  return (
    <div style={{ marginBottom: '1rem' }}>
      <div style={{
        fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase',
        letterSpacing: '0.06em', color: '#999', marginBottom: '0.6rem',
      }}>
        Activity
      </div>
      {summary.totalTokens > 0 && (
        <div style={{
          fontSize: '0.7rem', color: '#888', marginBottom: '0.6rem',
          padding: '0.4rem 0.6rem', backgroundColor: '#f8fafc', borderRadius: 6,
          border: '1px solid #e8e8e8', display: 'flex', gap: '0.6rem', flexWrap: 'wrap',
        }}>
          <span><strong>{summary.totalTokens.toLocaleString()}</strong> tokens <span style={{ color: '#aaa' }}>({summary.inputTokens.toLocaleString()} in / {summary.outputTokens.toLocaleString()} out)</span></span>
          <span style={{ color: '#ccc' }}>&middot;</span>
          <span>{summary.llmCount} LLM calls</span>
          <span style={{ color: '#ccc' }}>&middot;</span>
          <span>{summary.toolCount} tool calls</span>
          <span style={{ color: '#ccc' }}>&middot;</span>
          <span>{summary.totalDuration}s</span>
        </div>
      )}
      <div style={{ position: 'relative', paddingLeft: '1.5rem' }}>
        <div style={{
          position: 'absolute', left: '0.45rem', top: 4, bottom: 4,
          width: 2, backgroundColor: '#e8e8e8', borderRadius: 1,
        }} />

        {allEvents.map((evt, i) => {
          if (evt.type === 'llm') {
            const key = `llm-${evt.call.id}`;
            const isExpanded = expandedItems.has(key);
            return (
              <div key={key} style={{ marginBottom: '0.5rem', position: 'relative' }}>
                <div onClick={() => toggle(key)} style={{ display: 'flex', alignItems: 'flex-start', cursor: 'pointer' }}>
                  <div style={{
                    position: 'absolute', left: '-1.15rem', top: 2,
                    width: 8, height: 8, borderRadius: '50%', backgroundColor: '#c4a882',
                  }} />
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: '0.78rem', color: '#333' }}>{evt.icon} {evt.label}</span>
                    <span style={{ fontSize: '0.65rem', color: '#aaa', marginLeft: '0.35rem' }}>{evt.call.model}</span>
                    {evt.call.duration_ms != null && (
                      <span style={{
                        fontSize: '0.6rem', fontWeight: 600, padding: '0.1rem 0.35rem',
                        borderRadius: 8, backgroundColor: '#fefcbf', color: '#975a16', marginLeft: '0.4rem',
                      }}>
                        {evt.call.duration_ms}ms
                      </span>
                    )}
                    {evt.call.input_tokens != null && (
                      <span style={{
                        fontSize: '0.6rem', fontWeight: 600, padding: '0.1rem 0.35rem',
                        borderRadius: 8, backgroundColor: '#f0f0f0', color: '#666', marginLeft: '0.3rem',
                      }}>
                        {((evt.call.input_tokens ?? 0) + (evt.call.output_tokens ?? 0)).toLocaleString()} tokens
                      </span>
                    )}
                  </div>
                  <span style={{ fontSize: '0.65rem', color: '#bbb', marginLeft: '0.5rem', whiteSpace: 'nowrap' }}>
                    {evt.time}
                  </span>
                </div>
                {isExpanded && <LlmCallDetail call={evt.call} />}
              </div>
            );
          }

          if (evt.type === 'tool') {
            const key = `tool-${evt.tc.id}`;
            const isExpanded = expandedItems.has(key);
            const isFailed = evt.tc.status === 'failed';
            return (
              <div key={key} style={{ marginBottom: '0.5rem', position: 'relative' }}>
                <div onClick={() => toggle(key)} style={{ display: 'flex', alignItems: 'flex-start', cursor: 'pointer' }}>
                  <div style={{
                    position: 'absolute', left: '-1.15rem', top: 2,
                    width: 8, height: 8, borderRadius: '50%',
                    backgroundColor: isFailed ? '#f56565' : '#4d65ff',
                  }} />
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: '0.78rem', color: isFailed ? '#c53030' : '#333' }}>
                      {evt.icon} {evt.label}
                    </span>
                    {evt.tc.duration_ms != null && (
                      <span style={{
                        fontSize: '0.6rem', fontWeight: 600, padding: '0.1rem 0.35rem',
                        borderRadius: 8, backgroundColor: '#e8f0fe', color: '#1a56db', marginLeft: '0.4rem',
                      }}>
                        {evt.tc.duration_ms}ms
                      </span>
                    )}
                    {isFailed && (
                      <span style={{
                        fontSize: '0.6rem', fontWeight: 600, padding: '0.1rem 0.35rem',
                        borderRadius: 8, backgroundColor: '#fff5f5', color: '#c53030', marginLeft: '0.4rem',
                      }}>
                        FAILED
                      </span>
                    )}
                  </div>
                  <span style={{ fontSize: '0.65rem', color: '#bbb', marginLeft: '0.5rem', whiteSpace: 'nowrap' }}>
                    {evt.time}
                  </span>
                </div>
                {isExpanded && <ToolCallDetail tc={evt.tc} />}
              </div>
            );
          }

          if (evt.type === 'approval') {
            const isApproved = evt.label === 'Task approved';
            return (
              <div key={`approval-${i}`} style={{ display: 'flex', alignItems: 'flex-start', marginBottom: '0.5rem', position: 'relative' }}>
                <div style={{
                  position: 'absolute', left: '-1.15rem', top: 2,
                  width: 8, height: 8, borderRadius: '50%',
                  backgroundColor: isApproved ? '#28a745' : '#dc3545',
                }} />
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: '0.78rem', color: '#333', fontWeight: 600 }}>{evt.icon} {evt.label}</span>
                  <span style={{ fontSize: '0.72rem', color: '#888', marginLeft: '0.4rem' }}>{evt.detail}</span>
                </div>
                <span style={{ fontSize: '0.65rem', color: '#bbb', marginLeft: '0.5rem', whiteSpace: 'nowrap' }}>{evt.time}</span>
              </div>
            );
          }

          if (evt.type === 'submission') {
            const isSuccess = evt.label === 'Submitted to external system';
            return (
              <div key={`submission-${i}`} style={{ display: 'flex', alignItems: 'flex-start', marginBottom: '0.5rem', position: 'relative' }}>
                <div style={{
                  position: 'absolute', left: '-1.15rem', top: 2,
                  width: 8, height: 8, borderRadius: '50%',
                  backgroundColor: isSuccess ? '#4d65ff' : '#dc3545',
                }} />
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: '0.78rem', color: '#333', fontWeight: 600 }}>{evt.icon} {evt.label}</span>
                  <div style={{ fontSize: '0.72rem', color: '#666', marginTop: '0.15rem' }}>{evt.detail}</div>
                </div>
                <span style={{ fontSize: '0.65rem', color: '#bbb', marginLeft: '0.5rem', whiteSpace: 'nowrap' }}>{evt.time}</span>
              </div>
            );
          }

          if (evt.type === 'thinking') {
            return (
              <div key={`thinking-${evt.idx}`} style={{ display: 'flex', alignItems: 'flex-start', marginBottom: '0.35rem', position: 'relative' }}>
                <div style={{
                  position: 'absolute', left: '-1.15rem', top: 2,
                  width: 8, height: 8, borderRadius: '50%', backgroundColor: '#b0b0b0',
                }} />
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: '0.72rem', color: '#888' }}>{evt.icon} {evt.label}</span>
                  <span style={{
                    fontSize: '0.6rem', fontWeight: 600, marginLeft: '0.4rem',
                    padding: '0.1rem 0.35rem', borderRadius: 8,
                    backgroundColor: evt.isReasoning ? '#eef6ff' : '#f3f0f8',
                    color: evt.isReasoning ? '#2563eb' : '#6c3483',
                  }}>
                    {evt.isReasoning ? 'reasoning' : 'event'}
                  </span>
                </div>
              </div>
            );
          }

          // Routing or message events
          if (evt.type === 'message') {
            const isUser = evt.role === 'user';
            const dotColor = isUser ? '#c4a882' : '#48bb78';
            const isExpanded = expandedMessages.has(evt.index);
            const preview = evt.text.length > 120 ? evt.text.slice(0, 120) + '…' : evt.text;
            return (
              <div key={`msg-${i}`} style={{ marginBottom: '0.5rem', position: 'relative' }}>
                <div
                  onClick={() => toggleMsg(evt.index)}
                  style={{ display: 'flex', alignItems: 'flex-start', cursor: 'pointer' }}
                >
                  <div style={{
                    position: 'absolute', left: '-1.15rem', top: 2,
                    width: 8, height: 8, borderRadius: '50%', backgroundColor: dotColor,
                  }} />
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: '0.78rem', color: '#333' }}>{evt.icon} {evt.label}</span>
                    <span style={{
                      fontSize: '0.6rem', fontWeight: 600, marginLeft: '0.4rem',
                      padding: '0.1rem 0.35rem', borderRadius: 8,
                      backgroundColor: '#f0faf4', color: '#2f855a',
                    }}>
                      conversation
                    </span>
                  </div>
                  <span style={{ fontSize: '0.65rem', color: '#bbb', marginLeft: '0.5rem', whiteSpace: 'nowrap' }}>{evt.time}</span>
                </div>
                <div style={{
                  marginTop: '0.25rem',
                  marginLeft: '0.1rem',
                  padding: '0.4rem 0.6rem',
                  backgroundColor: isUser ? '#faf8f5' : '#f8fff9',
                  border: `1px solid ${isUser ? '#e8ddd0' : '#c3e6cb'}`,
                  borderRadius: 6,
                  fontSize: '0.75rem',
                  color: '#555',
                  lineHeight: 1.4,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}>
                  {isExpanded ? evt.text : preview}
                  {evt.text.length > 120 && (
                    <span style={{ color: '#999', marginLeft: '0.25rem', cursor: 'pointer' }}>
                      {isExpanded ? ' (less)' : ''}
                    </span>
                  )}
                </div>
              </div>
            );
          }

          // Routing events
          return (
            <div key={`routing-${i}`} style={{ display: 'flex', alignItems: 'flex-start', marginBottom: '0.5rem', position: 'relative' }}>
              <div style={{
                position: 'absolute', left: '-1.15rem', top: 2,
                width: 8, height: 8, borderRadius: '50%', backgroundColor: '#ccc',
              }} />
              <div style={{ flex: 1 }}>
                <span style={{ fontSize: '0.78rem', color: '#555' }}>{evt.icon} {evt.label}</span>
              </div>
              <span style={{ fontSize: '0.65rem', color: '#bbb', marginLeft: '0.5rem', whiteSpace: 'nowrap' }}>{evt.time}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
