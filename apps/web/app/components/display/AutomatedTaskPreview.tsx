'use client';

import { useState, useCallback } from 'react';
import { Check, ChevronRight } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import { memberName } from '../../lib/memberNames';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';

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

// active → ok · paused → warn · draft (a newly proposed task) → neutral.
const STATUS_TONES: Record<string, BadgeTone> = {
  active: 'ok',
  paused: 'warn',
  draft: 'neutral',
};

const DIVIDER = '1px solid var(--line-soft)';

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
  // Saving activates the task; until then it is whatever it was proposed as.
  const status = saved ? 'active' : String(taskData.status || 'draft');
  const meta = [agentSlug ? memberName(agentSlug) : '', formatSchedule(scheduleType, scheduleConfig)].filter(Boolean).join(' · ');

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
    <div className="n-card" style={{ overflow: 'hidden', lineHeight: 1.45 }}>
      {/* Header */}
      <div style={{ padding: '14px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{title}</div>
            {meta && <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta}</div>}
          </div>
          {status && (
            <Badge tone={STATUS_TONES[status] ?? 'neutral'}>{status.charAt(0).toUpperCase() + status.slice(1)}</Badge>
          )}
        </div>
        {description && (
          <p style={{ margin: '10px 0 0', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>{description}</p>
        )}

        {/* What the task will be asked to do — anyone activating it can read
            it before they do. */}
        {prompt && (
          <div style={{ marginTop: 10 }}>
            <button
              type="button"
              onClick={() => setShowPrompt(!showPrompt)}
              aria-expanded={showPrompt}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 0',
                border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit',
                fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--muted)',
              }}
            >
              <Icon
                icon={ChevronRight}
                size={14}
                style={{ transform: showPrompt ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}
              />
              {showPrompt ? 'Hide instructions' : 'Show instructions'}
            </button>
            {showPrompt && (
              <div style={{ marginTop: 6 }}>
                <div className="n-eyebrow" style={{ marginBottom: 4 }}>Prompt</div>
                <pre style={{
                  margin: 0, padding: '8px 10px', maxHeight: 280, overflow: 'auto',
                  backgroundColor: 'var(--surface)', border: DIVIDER, borderRadius: 'var(--radius-sm)',
                  fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', lineHeight: 1.5,
                  color: 'var(--text-soft)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                }}>{prompt}</pre>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Test result */}
      {testResult && (
        <div style={{
          padding: '10px 16px', borderTop: DIVIDER,
          backgroundColor: (testResult as Record<string, unknown>).success ? 'var(--ok-bg)' : 'var(--error-bg)',
        }}>
          <div className="n-eyebrow" style={{ marginBottom: 2 }}>Test result</div>
          {testResult.data ? (
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
              {String((testResult.data as Record<string, unknown>)?.result_summary || 'Task completed successfully').slice(0, 500)}
            </div>
          ) : null}
          {testResult.error ? (
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>
              {String(testResult.error)}
            </div>
          ) : null}
        </div>
      )}

      {/* Actions */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'flex-end', flexWrap: 'wrap', gap: 8,
        padding: '12px 16px', borderTop: DIVIDER,
      }}>
        <Button onClick={handleTest} disabled={testing || !taskId}>{testing ? 'Testing…' : 'Test'}</Button>
        {saved ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--ok)' }}>
            <Icon icon={Check} size={16} />
            Saved &amp; active
          </span>
        ) : (
          <Button variant="primary" onClick={handleSave} disabled={saving || !taskId}>
            {saving ? 'Saving…' : 'Save & activate'}
          </Button>
        )}
      </div>
    </div>
  );
}
