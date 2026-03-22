'use client';

import { useState, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';

const SCHEDULE_LABELS: Record<string, string> = {
  manual: 'Manual trigger only',
  hourly: 'Every hour',
  daily: 'Daily',
  weekly: 'Weekly',
  monthly: 'Monthly',
};

function formatSchedule(type: string, config: Record<string, unknown>): string {
  const base = SCHEDULE_LABELS[type] || type;
  const hour = config.hour as number | undefined;
  const minute = config.minute as number | undefined;
  const time = hour != null ? `at ${String(hour).padStart(2, '0')}:${String(minute ?? 0).padStart(2, '0')}` : '';
  const day = config.day_of_week as string | undefined;

  if (type === 'daily' && time) return `Daily ${time}`;
  if (type === 'weekly' && day) return `Weekly on ${day.charAt(0).toUpperCase() + day.slice(1)} ${time}`;
  if (type === 'monthly') return `Monthly on day ${config.day_of_month || 1} ${time}`;
  return base;
}

const AGENT_COLORS: Record<string, { bg: string; color: string }> = {
  hr: { bg: '#dbeafe', color: '#1e40af' },
  procurement: { bg: '#fef3c7', color: '#92400e' },
  reports: { bg: '#d1fae5', color: '#065f46' },
};

export default function AutomatedTaskPreview({ data, props, onAction }: DisplayBlockProps) {
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);

  const taskData = data as Record<string, unknown>;
  const title = String(taskData.title || '');
  const description = String(taskData.description || '');
  const agentSlug = String(taskData.agent_slug || '');
  const prompt = String(taskData.prompt || '');
  const scheduleType = String(taskData.schedule_type || 'manual');
  const scheduleConfig = (taskData.schedule_config || {}) as Record<string, unknown>;
  const taskId = String(taskData.id || '');
  const agentColor = AGENT_COLORS[agentSlug] || { bg: '#f3f4f6', color: '#374151' };

  const handleTest = useCallback(async () => {
    if (!taskId) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await apiFetch(`/api/automated-tasks/${taskId}/run`, {
        method: 'POST',
        body: JSON.stringify({ mode: 'test' }),
      });
      if (!res.ok) {
        const text = await res.text();
        setTestResult({ success: false, error: `Server error (${res.status}): ${text.slice(0, 200)}` });
        return;
      }
      const result = await res.json();
      setTestResult(result);
    } catch (err) {
      setTestResult({ success: false, error: String(err) });
    } finally {
      setTesting(false);
    }
  }, [taskId]);

  const handleSave = useCallback(async () => {
    if (!taskId) return;
    setSaving(true);
    try {
      await apiFetch(`/api/automated-tasks/${taskId}/resume`, { method: 'POST' });
      setSaved(true);
    } finally {
      setSaving(false);
    }
  }, [taskId]);

  return (
    <div style={{
      border: '1px solid #e5e7eb', borderRadius: 10, overflow: 'hidden',
      backgroundColor: '#fff', marginBottom: '0.75rem',
      boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
    }}>
      {/* Header */}
      <div style={{
        padding: '1rem 1.25rem',
        borderBottom: '1px solid #e5e7eb',
        background: 'linear-gradient(to bottom, #fafafa, #fff)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.4rem' }}>
          <span style={{ fontSize: '1rem', fontWeight: 700, color: '#111' }}>{title}</span>
          <span style={{
            fontSize: '0.68rem', fontWeight: 600, padding: '2px 8px', borderRadius: 10,
            backgroundColor: agentColor.bg, color: agentColor.color,
          }}>{agentSlug}</span>
          <span style={{
            fontSize: '0.68rem', fontWeight: 600, padding: '2px 8px', borderRadius: 10,
            backgroundColor: '#f3f4f6', color: '#6b7280',
          }}>{formatSchedule(scheduleType, scheduleConfig)}</span>
        </div>
        {description && (
          <div style={{ fontSize: '0.82rem', color: '#6b7280' }}>{description}</div>
        )}
      </div>

      {/* Prompt */}
      <div style={{ padding: '0.75rem 1.25rem', borderBottom: '1px solid #f3f4f6' }}>
        <button onClick={() => setShowPrompt(!showPrompt)} style={{
          background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.72rem',
          fontWeight: 600, color: '#9ca3af', fontFamily: 'inherit', padding: 0,
          display: 'flex', alignItems: 'center', gap: 4,
        }}>
          <span style={{ transform: showPrompt ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.15s', fontSize: '0.6rem' }}>&#9654;</span>
          Prompt
        </button>
        {showPrompt && (
          <pre style={{
            marginTop: '0.4rem', padding: '0.5rem', backgroundColor: '#f9fafb',
            border: '1px solid #e5e7eb', borderRadius: 6,
            fontSize: '0.78rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            color: '#374151', lineHeight: 1.5, margin: 0,
          }}>{prompt}</pre>
        )}
      </div>

      {/* Test result */}
      {testResult && (
        <div style={{
          padding: '0.75rem 1.25rem', borderBottom: '1px solid #f3f4f6',
          backgroundColor: (testResult as Record<string, unknown>).success ? '#f0fdf4' : '#fef2f2',
        }}>
          <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#6b7280', marginBottom: '0.3rem', textTransform: 'uppercase' }}>
            Test Result
          </div>
          {testResult.data ? (
            <div style={{ fontSize: '0.82rem', color: '#374151' }}>
              {String((testResult.data as Record<string, unknown>)?.result_summary || 'Task completed successfully').slice(0, 500)}
            </div>
          ) : null}
          {testResult.error ? (
            <div style={{ fontSize: '0.82rem', color: '#dc2626' }}>
              {String(testResult.error)}
            </div>
          ) : null}
        </div>
      )}

      {/* Actions */}
      <div style={{ padding: '0.75rem 1.25rem', display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
        <button onClick={handleTest} disabled={testing || !taskId} style={{
          padding: '8px 20px', fontSize: '0.82rem', fontWeight: 600,
          border: '1px solid #d1d5db', borderRadius: 8,
          backgroundColor: '#fff', color: '#374151',
          cursor: testing ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
        }}>{testing ? 'Testing...' : 'Test'}</button>
        <button onClick={handleSave} disabled={saving || saved || !taskId} style={{
          padding: '8px 20px', fontSize: '0.82rem', fontWeight: 600,
          border: 'none', borderRadius: 8,
          backgroundColor: saved ? '#d1fae5' : '#111',
          color: saved ? '#065f46' : '#fff',
          cursor: saving || saved ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
        }}>{saving ? 'Saving...' : saved ? 'Saved & Active' : 'Save & Activate'}</button>
      </div>
    </div>
  );
}
