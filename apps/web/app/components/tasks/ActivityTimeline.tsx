'use client';

import { useState } from 'react';
import type { ConversationMessage, LlmCall } from '../../types';

function classifyMessage(msg: ConversationMessage, index: number, total: number): {
  label: string;
  icon: string;
} {
  const text = msg.text.toLowerCase();

  if (msg.role === 'user' && index === 0) {
    return { label: 'User request received', icon: '\u{1F4E9}' };
  }
  if (msg.role === 'user') {
    return { label: 'User replied', icon: '\u{1F4AC}' };
  }

  // Assistant messages — classify by content

  // Revisions (check first — revision messages contain "changed" / "updated")
  if (text.includes('changed from') || text.includes('updated from') || text.includes('updated and ready'))
    return { label: 'Task revised', icon: '\u{270F}\u{FE0F}' };

  if (text.includes('which venue') || text.includes('which location') || text.includes('what venue'))
    return { label: 'Clarification requested: venue', icon: '\u{2753}' };
  if (text.includes('which product') || text.includes('what product'))
    return { label: 'Clarification requested: product', icon: '\u{2753}' };
  if (text.includes('how many') || text.includes('quantity'))
    return { label: 'Clarification requested: quantity', icon: '\u{2753}' };
  if (text.includes('?'))
    return { label: 'Clarification requested', icon: '\u{2753}' };
  if (text.includes('draft order') || text.includes('order created') || text.includes('ready for review') || text.includes('ready for your approval'))
    return { label: 'Draft order created', icon: '\u{1F4CB}' };
  if (text.includes('approved'))
    return { label: 'Order approved', icon: '\u{2705}' };
  if (text.includes('submitted'))
    return { label: 'Order submitted', icon: '\u{1F680}' };
  if (text.includes('set up') || text.includes('setup') || text.includes('onboarding'))
    return { label: 'Employee setup initiated', icon: '\u{1F464}' };

  return { label: 'Agent responded', icon: '\u{1F916}' };
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
      {/* Status indicator */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem' }}>
        <span style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          backgroundColor: call.status === 'success' ? '#48bb78' : '#f56565',
          display: 'inline-block',
        }} />
        <span style={{ fontWeight: 600, color: call.status === 'success' ? '#2f855a' : '#c53030' }}>
          {call.status === 'success' ? 'Success' : 'Error'}
        </span>
        {call.duration_ms != null && (
          <span style={{ color: '#999', marginLeft: 'auto' }}>{call.duration_ms}ms</span>
        )}
      </div>

      {call.error_message && (
        <div style={{
          padding: '0.4rem 0.6rem',
          backgroundColor: '#fff5f5',
          border: '1px solid #fed7d7',
          borderRadius: 4,
          color: '#c53030',
          marginBottom: '0.5rem',
          whiteSpace: 'pre-wrap',
        }}>
          {call.error_message}
        </div>
      )}

      {/* System prompt */}
      <div style={{ marginBottom: '0.5rem' }}>
        <div style={{
          fontSize: '0.65rem', fontWeight: 600, textTransform: 'uppercase',
          letterSpacing: '0.04em', color: '#999', marginBottom: '0.25rem',
        }}>
          System Prompt
        </div>
        <pre style={{
          maxHeight: 150,
          overflow: 'auto',
          backgroundColor: '#f5f5f5',
          border: '1px solid #e2e2e2',
          borderRadius: 4,
          padding: '0.5rem',
          margin: 0,
          fontSize: '0.7rem',
          lineHeight: 1.4,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}>
          {call.system_prompt}
        </pre>
      </div>

      {/* User prompt */}
      <div style={{ marginBottom: '0.5rem' }}>
        <div style={{
          fontSize: '0.65rem', fontWeight: 600, textTransform: 'uppercase',
          letterSpacing: '0.04em', color: '#999', marginBottom: '0.25rem',
        }}>
          User Prompt
        </div>
        <pre style={{
          maxHeight: 150,
          overflow: 'auto',
          backgroundColor: '#f5f5f5',
          border: '1px solid #e2e2e2',
          borderRadius: 4,
          padding: '0.5rem',
          margin: 0,
          fontSize: '0.7rem',
          lineHeight: 1.4,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}>
          {call.user_prompt}
        </pre>
      </div>

      {/* Response */}
      {(call.raw_response || call.parsed_response) && (
        <div>
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.5rem',
            marginBottom: '0.25rem',
          }}>
            <span style={{
              fontSize: '0.65rem', fontWeight: 600, textTransform: 'uppercase',
              letterSpacing: '0.04em', color: '#999',
            }}>
              Response
            </span>
            {call.raw_response && call.parsed_response && (
              <button
                onClick={() => setShowRaw(!showRaw)}
                style={{
                  fontSize: '0.6rem',
                  padding: '0.1rem 0.4rem',
                  borderRadius: 3,
                  border: '1px solid #ddd',
                  backgroundColor: '#fff',
                  cursor: 'pointer',
                  color: '#666',
                }}
              >
                {showRaw ? 'Parsed' : 'Raw'}
              </button>
            )}
          </div>
          <pre style={{
            maxHeight: 150,
            overflow: 'auto',
            backgroundColor: '#f5f5f5',
            border: '1px solid #e2e2e2',
            borderRadius: 4,
            padding: '0.5rem',
            margin: 0,
            fontSize: '0.7rem',
            lineHeight: 1.4,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}>
            {showRaw || !call.parsed_response
              ? call.raw_response
              : JSON.stringify(call.parsed_response, null, 2)}
          </pre>
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
  approval?: ApprovalInfo | null;
  integrationRun?: IntegrationRunInfo | null;
}

export default function ActivityTimeline({ messages, createdAt, domain, llmCalls, approval, integrationRun }: ActivityTimelineProps) {
  const [expandedCalls, setExpandedCalls] = useState<Set<string>>(new Set());

  if (!messages || messages.length === 0) return null;

  const toggleCall = (id: string) => {
    setExpandedCalls(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Find the earliest LLM routing call timestamp, or fall back to task createdAt
  const routingCall = (llmCalls || []).find(c => c.call_type === 'routing');
  const routingSortKey = routingCall ? routingCall.created_at : createdAt;

  // Build timeline events
  const routingEvents = [
    { type: 'routing' as const, label: 'Supervisor analysed request', icon: '\u{1F9E0}', time: formatTime(routingSortKey), sortKey: routingSortKey, sortOrder: 0 },
    { type: 'routing' as const, label: `Routed to ${domain} agent`, icon: '\u{27A1}\u{FE0F}', time: formatTime(routingSortKey), sortKey: routingSortKey, sortOrder: 1 },
  ];

  const messageEvents = messages.map((m, i) => {
    const classified = classifyMessage(m, i, messages.length);
    const msgTime = m.created_at || createdAt;
    return { type: 'message' as const, ...classified, time: formatTime(msgTime), text: m.text, role: m.role, sortKey: msgTime, sortOrder: 0 };
  });

  // Build LLM call events
  const llmEvents = (llmCalls || []).map(call => ({
    type: 'llm' as const,
    call,
    label: call.call_type === 'routing'
      ? `LLM routing (${call.model})`
      : call.call_type === 'execution'
        ? `LLM execution (${call.model})`
        : `LLM interpretation (${call.model})`,
    icon: '\u{26A1}',
    time: formatTime(call.created_at),
    sortKey: call.created_at,
    sortOrder: 0,
  }));

  // Build approval event
  const approvalEvents: { type: 'approval'; label: string; icon: string; detail: string; time: string; sortKey: string; sortOrder: number }[] = [];
  if (approval) {
    const isApproved = approval.action === 'approved';
    approvalEvents.push({
      type: 'approval' as const,
      label: isApproved ? 'Task approved' : 'Task rejected',
      icon: isApproved ? '\u{2705}' : '\u{274C}',
      detail: `by ${approval.performed_by}`,
      time: formatTime(approval.performed_at),
      sortKey: approval.performed_at,
      sortOrder: 0,
    });
  }

  // Build integration run (submission) event
  const submissionEvents: { type: 'submission'; label: string; icon: string; detail: string; time: string; sortKey: string; sortOrder: number }[] = [];
  if (integrationRun) {
    const isSuccess = integrationRun.status === 'success';
    const connectorLabel = integrationRun.connector === 'mock_supplier' ? 'Bidfood (mock)'
      : integrationRun.connector === 'mock_hr' ? 'HR System (mock)'
      : integrationRun.connector;
    let detail = `via ${connectorLabel}`;
    if (isSuccess && integrationRun.reference) {
      detail += ` \u2014 ref: ${integrationRun.reference}`;
    }
    if (!isSuccess && integrationRun.error) {
      detail += ` \u2014 ${integrationRun.error}`;
    }
    submissionEvents.push({
      type: 'submission' as const,
      label: isSuccess ? 'Submitted to external system' : 'Submission failed',
      icon: isSuccess ? '\u{1F680}' : '\u{26A0}\u{FE0F}',
      detail,
      time: formatTime(integrationRun.submitted_at),
      sortKey: integrationRun.submitted_at,
      sortOrder: 0,
    });
  }

  // Merge and sort by timestamp (oldest first), using sortOrder to break ties
  type TimelineEvent =
    | (typeof routingEvents)[number]
    | (typeof messageEvents)[number]
    | (typeof llmEvents)[number]
    | (typeof approvalEvents)[number]
    | (typeof submissionEvents)[number];

  const allEvents: TimelineEvent[] = [
    ...routingEvents,
    ...llmEvents,
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
        fontSize: '0.7rem',
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
        color: '#999',
        marginBottom: '0.6rem',
      }}>
        Activity
      </div>
      <div style={{ position: 'relative', paddingLeft: '1.5rem' }}>
        {/* Vertical line */}
        <div style={{
          position: 'absolute',
          left: '0.45rem',
          top: 4,
          bottom: 4,
          width: 2,
          backgroundColor: '#e8e8e8',
          borderRadius: 1,
        }} />

        {allEvents.map((evt, i) => {
          if (evt.type === 'llm') {
            const isExpanded = expandedCalls.has(evt.call.id);
            return (
              <div key={`llm-${evt.call.id}`} style={{ marginBottom: '0.5rem', position: 'relative' }}>
                <div
                  onClick={() => toggleCall(evt.call.id)}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{
                    position: 'absolute',
                    left: '-1.15rem',
                    top: 2,
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    backgroundColor: '#c4a882',
                  }} />
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: '0.78rem', color: '#333' }}>
                      {evt.icon} {evt.label}
                    </span>
                    {evt.call.duration_ms != null && (
                      <span style={{
                        fontSize: '0.6rem',
                        fontWeight: 600,
                        padding: '0.1rem 0.35rem',
                        borderRadius: 8,
                        backgroundColor: '#fefcbf',
                        color: '#975a16',
                        marginLeft: '0.4rem',
                      }}>
                        {evt.call.duration_ms}ms
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

          // Approval events
          if (evt.type === 'approval') {
            const isApproved = evt.label === 'Task approved';
            return (
              <div key={`approval-${i}`} style={{
                display: 'flex',
                alignItems: 'flex-start',
                marginBottom: '0.5rem',
                position: 'relative',
              }}>
                <div style={{
                  position: 'absolute',
                  left: '-1.15rem',
                  top: 2,
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  backgroundColor: isApproved ? '#28a745' : '#dc3545',
                }} />
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: '0.78rem', color: '#333', fontWeight: 600 }}>
                    {evt.icon} {evt.label}
                  </span>
                  <span style={{ fontSize: '0.72rem', color: '#888', marginLeft: '0.4rem' }}>
                    {evt.detail}
                  </span>
                </div>
                <span style={{ fontSize: '0.65rem', color: '#bbb', marginLeft: '0.5rem', whiteSpace: 'nowrap' }}>
                  {evt.time}
                </span>
              </div>
            );
          }

          // Submission events
          if (evt.type === 'submission') {
            const isSuccess = evt.label === 'Submitted to external system';
            return (
              <div key={`submission-${i}`} style={{
                display: 'flex',
                alignItems: 'flex-start',
                marginBottom: '0.5rem',
                position: 'relative',
              }}>
                <div style={{
                  position: 'absolute',
                  left: '-1.15rem',
                  top: 2,
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  backgroundColor: isSuccess ? '#4d65ff' : '#dc3545',
                }} />
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: '0.78rem', color: '#333', fontWeight: 600 }}>
                    {evt.icon} {evt.label}
                  </span>
                  <div style={{ fontSize: '0.72rem', color: '#666', marginTop: '0.15rem' }}>
                    {evt.detail}
                  </div>
                </div>
                <span style={{ fontSize: '0.65rem', color: '#bbb', marginLeft: '0.5rem', whiteSpace: 'nowrap' }}>
                  {evt.time}
                </span>
              </div>
            );
          }

          // Routing or message events
          const dotColor = evt.type === 'routing'
            ? '#ccc'
            : ('role' in evt && evt.role === 'user' ? '#c4a882' : '#48bb78');

          return (
            <div key={`${evt.type}-${i}`} style={{
              display: 'flex',
              alignItems: 'flex-start',
              marginBottom: '0.5rem',
              position: 'relative',
            }}>
              <div style={{
                position: 'absolute',
                left: '-1.15rem',
                top: 2,
                width: 8,
                height: 8,
                borderRadius: '50%',
                backgroundColor: dotColor,
              }} />
              <div style={{ flex: 1 }}>
                <span style={{ fontSize: '0.78rem', color: evt.type === 'routing' ? '#555' : '#333' }}>
                  {evt.icon} {evt.label}
                </span>
              </div>
              <span style={{ fontSize: '0.65rem', color: '#bbb', marginLeft: '0.5rem', whiteSpace: 'nowrap' }}>
                {evt.time}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
