'use client';

import { useState } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface ToolCallSummary {
  id: string;
  action: string;
  connector_name: string;
  method: string;
  summary: string;
  input_params: Record<string, unknown> | null;
}

export default function ToolApprovalCard({ data, onAction }: DisplayBlockProps) {
  const [showDetails, setShowDetails] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);

  const toolCalls = (data.tool_calls as ToolCallSummary[]) || [];
  const status = (data.status as string) || 'pending';
  const threadId = data.thread_id as string;

  const handleApprove = async () => {
    setLoading(true);
    await onAction?.({ connector_name: '_system', action: 'tool_approve', params: { thread_id: threadId } });
    setLoading(false);
  };

  const handleReject = async () => {
    setLoading(true);
    await onAction?.({ connector_name: '_system', action: 'tool_reject', params: { thread_id: threadId } });
    setLoading(false);
  };

  const toggleDetails = (id: string) => {
    setShowDetails(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const isPending = status === 'pending';
  const isApproved = status === 'approved';

  return (
    <div style={{
      border: `1px solid ${isPending ? '#e2ddd7' : isApproved ? '#c3e6cb' : '#e2e3e5'}`,
      borderRadius: 10,
      backgroundColor: isPending ? '#faf8f5' : isApproved ? '#f8fdf9' : '#f7f7f8',
      padding: '0.85rem 1rem',
      marginTop: '0.5rem',
    }}>
      {/* Header */}
      <div style={{ marginBottom: toolCalls.length > 0 ? '0.6rem' : 0 }}>
        <span style={{
          fontSize: '0.65rem', fontWeight: 600, padding: '2px 8px', borderRadius: 4,
          backgroundColor: isPending ? '#f5f0ea' : isApproved ? '#d4edda' : '#e2e3e5',
          color: isPending ? '#a08060' : isApproved ? '#155724' : '#666',
        }}>
          {isPending ? 'Approval Required' : isApproved ? 'Approved' : 'Declined'}
        </span>
      </div>

      {/* Tool call summaries */}
      {toolCalls.map(tc => (
        <div key={tc.id} style={{
          backgroundColor: '#fff',
          border: '1px solid #f0ebe5',
          borderRadius: 8,
          padding: '0.6rem 0.75rem',
          marginBottom: '0.4rem',
        }}>
          <div style={{ fontSize: '0.78rem', fontWeight: 500, color: '#333', marginBottom: '0.2rem' }}>
            {tc.summary}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <span style={{
              fontSize: '0.62rem', fontWeight: 600, padding: '1px 5px', borderRadius: 3,
              backgroundColor: '#f5f0ea', color: '#a08060',
            }}>
              {tc.action}
            </span>
            <span style={{ fontSize: '0.65rem', color: '#bbb' }}>{tc.connector_name}</span>
          </div>

          {tc.input_params && (
            <>
              <button
                onClick={() => toggleDetails(tc.id)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit',
                  fontSize: '0.68rem', color: '#bbb', padding: '0.25rem 0 0', display: 'flex', alignItems: 'center', gap: 4,
                }}
              >
                <span style={{
                  display: 'inline-block', transition: 'transform 0.15s',
                  transform: showDetails.has(tc.id) ? 'rotate(90deg)' : 'rotate(0deg)',
                  fontSize: '0.55rem',
                }}>&#9654;</span>
                {showDetails.has(tc.id) ? 'Hide details' : 'Show details'}
              </button>
              {showDetails.has(tc.id) && (
                <pre style={{
                  fontSize: '0.68rem', color: '#888', backgroundColor: '#faf8f5',
                  padding: '0.4rem', borderRadius: 6, marginTop: '0.3rem',
                  overflow: 'auto', maxHeight: 250, lineHeight: 1.4,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  border: '1px solid #f0ebe5',
                }}>
                  {JSON.stringify(tc.input_params, null, 2)}
                </pre>
              )}
            </>
          )}
        </div>
      ))}

      {/* Action buttons — bottom right */}
      {isPending && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.4rem', marginTop: '0.5rem' }}>
          <button
            onClick={handleReject}
            disabled={loading}
            style={{
              padding: '0.35rem 1rem', fontSize: '0.75rem', fontWeight: 500,
              border: '1px solid #e2ddd7', borderRadius: 6, cursor: loading ? 'not-allowed' : 'pointer',
              backgroundColor: '#fff', color: '#888', fontFamily: 'inherit',
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? '...' : 'Decline'}
          </button>
          <button
            onClick={handleApprove}
            disabled={loading}
            style={{
              padding: '0.35rem 1rem', fontSize: '0.75rem', fontWeight: 500,
              border: 'none', borderRadius: 6, cursor: loading ? 'not-allowed' : 'pointer',
              backgroundColor: '#a08060', color: '#fff', fontFamily: 'inherit',
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? '...' : 'Approve'}
          </button>
        </div>
      )}
    </div>
  );
}
