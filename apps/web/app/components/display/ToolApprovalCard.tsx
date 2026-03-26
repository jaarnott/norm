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
  const taskId = data.task_id as string;

  const handleApprove = async () => {
    setLoading(true);
    await onAction?.({ connector_name: '_system', action: 'tool_approve', params: { task_id: taskId } });
    setLoading(false);
  };

  const handleReject = async () => {
    setLoading(true);
    await onAction?.({ connector_name: '_system', action: 'tool_reject', params: { task_id: taskId } });
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
      border: `1px solid ${isPending ? '#e8daef' : isApproved ? '#c3e6cb' : '#d6d8db'}`,
      borderRadius: 10,
      backgroundColor: isPending ? '#f9f4fc' : isApproved ? '#f0faf3' : '#f7f7f8',
      padding: '1rem',
      marginTop: '0.5rem',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
        <span style={{ fontSize: '0.95rem' }}>{isPending ? '🔐' : isApproved ? '✅' : '❌'}</span>
        <span style={{ fontSize: '0.82rem', fontWeight: 600, color: '#333' }}>
          {isPending ? 'Approval Required' : isApproved ? 'Approved' : 'Rejected'}
        </span>
      </div>

      {/* Tool call summaries */}
      {toolCalls.map(tc => (
        <div key={tc.id} style={{
          backgroundColor: '#fff',
          border: '1px solid #e8e4de',
          borderRadius: 8,
          padding: '0.75rem',
          marginBottom: '0.5rem',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.3rem' }}>
            <span style={{ fontSize: '0.82rem', fontWeight: 600, color: '#333' }}>
              {tc.summary}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <span style={{
              fontSize: '0.65rem', fontWeight: 600, padding: '1px 6px', borderRadius: 4,
              backgroundColor: '#f0f0f0', color: '#888',
            }}>
              {tc.action}
            </span>
            <span style={{ fontSize: '0.68rem', color: '#aaa' }}>on {tc.connector_name}</span>
          </div>

          {/* Show details toggle */}
          {tc.input_params && (
            <>
              <button
                onClick={() => toggleDetails(tc.id)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit',
                  fontSize: '0.7rem', color: '#999', padding: '0.3rem 0 0', display: 'flex', alignItems: 'center', gap: 4,
                }}
              >
                <span style={{
                  display: 'inline-block', transition: 'transform 0.15s',
                  transform: showDetails.has(tc.id) ? 'rotate(90deg)' : 'rotate(0deg)',
                  fontSize: '0.6rem',
                }}>&#9654;</span>
                {showDetails.has(tc.id) ? 'Hide details' : 'Show details'}
              </button>
              {showDetails.has(tc.id) && (
                <pre style={{
                  fontSize: '0.7rem', color: '#666', backgroundColor: '#f8f8f8',
                  padding: '0.5rem', borderRadius: 6, marginTop: '0.4rem',
                  overflow: 'auto', maxHeight: 300, lineHeight: 1.4,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                }}>
                  {JSON.stringify(tc.input_params, null, 2)}
                </pre>
              )}
            </>
          )}
        </div>
      ))}

      {/* Action buttons */}
      {isPending && (
        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
          <button
            onClick={handleApprove}
            disabled={loading}
            style={{
              padding: '0.45rem 1.25rem', fontSize: '0.82rem', fontWeight: 600,
              border: 'none', borderRadius: 6, cursor: loading ? 'not-allowed' : 'pointer',
              backgroundColor: '#28a745', color: '#fff', fontFamily: 'inherit',
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? '...' : 'Approve'}
          </button>
          <button
            onClick={handleReject}
            disabled={loading}
            style={{
              padding: '0.45rem 1.25rem', fontSize: '0.82rem', fontWeight: 600,
              border: 'none', borderRadius: 6, cursor: loading ? 'not-allowed' : 'pointer',
              backgroundColor: '#dc3545', color: '#fff', fontFamily: 'inherit',
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? '...' : 'Reject'}
          </button>
        </div>
      )}
    </div>
  );
}
