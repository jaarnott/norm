'use client';

import { useState, useCallback } from 'react';
import { Check, Play } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Button from '../ui/Button';
import Badge from '../ui/Badge';
import Icon from '../ui/Icon';

interface Props {
  functionCode: string;
  onChange: (code: string) => void;
  /** The inputs to test with: the tool's required fields, then its optional ones. */
  fields: string[];
  /** The rest of the tool's consolidator_config (shapes, allowed_write_actions,
   *  max_api_calls, wraps…) — a test without it runs different code from production. */
  config: Record<string, unknown>;
  /** The tool's action: the engine resolves the invoice tools' level from it. */
  action: string;
}

/** What a preview run came back with (POST /connector-specs/norm/test-consolidator). */
interface PreviewCard { title?: string; target?: string; changes?: { field: string; before?: unknown; after?: unknown }[] }
interface PreviewWrite { connector: string; action: string; params?: Record<string, unknown> }

// The editor, the run log and the data preview all sit on the warm dark.
const codeSurface: React.CSSProperties = {
  backgroundColor: 'var(--code-bg)', color: 'var(--code-text)', fontFamily: 'var(--font-mono)',
};

// Log lines keep their status colour, mixed half-way toward the code text so
// each stays readable on --code-bg (5.6:1 and up; the pure tokens are ~2.3:1).
const onCode = (token: string) => `color-mix(in srgb, var(${token}) 50%, var(--code-text))`;
const logColor = (log: string) =>
  log.startsWith('ERROR') ? onCode('--error')
  : log.startsWith('API:') ? onCode('--info')
  : log.startsWith('Completed') ? onCode('--ok')
  : 'var(--code-text)';

export default function FunctionEditor({ functionCode, onChange, fields, config, action }: Props) {
  const [testParams, setTestParams] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    data: unknown;
    _logs?: string[];
    error?: string;
    previewed?: boolean;
    preview?: { card?: PreviewCard };
    preview_writes?: PreviewWrite[];
  } | null>(null);
  const [showData, setShowData] = useState(false);

  const handleTest = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await apiFetch('/api/connector-specs/norm/test-consolidator', {
        method: 'POST',
        // The whole config, as the agent runs it, in PREVIEW: it reads for real
        // and writes nothing — each write it would send comes back instead.
        body: JSON.stringify({
          consolidator_config: { ...config, function_code: functionCode },
          params: testParams,
          action,
          preview: true,
        }),
      });
      if (res.ok) {
        setTestResult(await res.json());
      } else {
        setTestResult({ success: false, data: null, error: `HTTP ${res.status}` });
      }
    } catch (e) {
      setTestResult({ success: false, data: null, error: String(e) });
    } finally {
      setTesting(false);
    }
  }, [functionCode, testParams, config, action]);

  const dataCount = testResult?.data
    ? Array.isArray(testResult.data) ? testResult.data.length
    : typeof testResult.data === 'object' ? Object.keys(testResult.data as Record<string, unknown>).length
    : 1
    : 0;

  return (
    <div>
      {/* Code editor */}
      <div style={{ marginBottom: 8 }}>
        <div className="n-eyebrow" style={{ marginBottom: 4 }}>
          Function
        </div>
        <textarea
          value={functionCode}
          onChange={e => onChange(e.target.value)}
          spellCheck={false}
          style={{
            ...codeSurface,
            width: '100%', minHeight: 250, padding: 12, fontSize: 'var(--fs-sm)',
            lineHeight: 1.5, border: '1px solid var(--code-bg)', borderRadius: 'var(--radius)',
            resize: 'vertical', boxSizing: 'border-box', tabSize: 4,
          }}
          onKeyDown={e => {
            // Tab key inserts spaces instead of switching focus
            if (e.key === 'Tab') {
              e.preventDefault();
              const target = e.target as HTMLTextAreaElement;
              const start = target.selectionStart;
              const end = target.selectionEnd;
              const newValue = functionCode.substring(0, start) + '    ' + functionCode.substring(end);
              onChange(newValue);
              requestAnimationFrame(() => {
                target.selectionStart = target.selectionEnd = start + 4;
              });
            }
          }}
        />
      </div>

      {/* Test panel */}
      <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: 10, backgroundColor: 'var(--surface)' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
          {fields.map(f => (
            <div key={f} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <label className="n-label" style={{ marginBottom: 0 }}>{f}:</label>
              <input
                className="n-input"
                value={testParams[f] || ''}
                onChange={e => setTestParams(p => ({ ...p, [f]: e.target.value }))}
                placeholder={f}
                style={{ width: 140 }}
              />
            </div>
          ))}
          <Button size="sm" icon={Play} onClick={handleTest} disabled={testing}>
            {testing ? 'Running…' : 'Run test'}
          </Button>
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            Reads for real; never writes — a write is shown below instead of sent.
          </span>
        </div>

        {/* What a write tool would have changed */}
        {testResult?.preview?.card && (
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)', marginBottom: 8 }}>
            <strong>Stopped at its preview</strong>
            {testResult.preview.card.title ? ` — ${testResult.preview.card.title}` : ''}
            {testResult.preview.card.target ? `: ${testResult.preview.card.target}` : ''}
            <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
              {(testResult.preview.card.changes || []).map((c, i) => (
                <li key={i}>
                  {c.field}: {c.before !== undefined ? `${String(c.before)} → ` : ''}{String(c.after)}
                </li>
              ))}
            </ul>
          </div>
        )}
        {(testResult?.preview_writes || []).length > 0 && (
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)', marginBottom: 8 }}>
            <strong>Writes it would have sent (not sent):</strong>
            <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
              {(testResult?.preview_writes || []).map((w, i) => (
                <li key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)' }}>{w.connector}.{w.action}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Logs */}
        {testResult?._logs && testResult._logs.length > 0 && (
          <div style={{
            ...codeSurface, borderRadius: 'var(--radius-sm)', padding: '8px 10px',
            marginBottom: 8, maxHeight: 200, overflowY: 'auto',
          }}>
            {testResult._logs.map((log, i) => (
              <div key={i} style={{ fontSize: 'var(--fs-xs)', lineHeight: 1.6, color: logColor(log) }}>
                <span aria-hidden="true" style={{ color: 'var(--icon)', marginRight: 6 }}>{'>'}</span>
                {log}
              </div>
            ))}
          </div>
        )}

        {/* Error */}
        {testResult?.error && (
          <div role="alert" style={{
            padding: '8px 10px', borderRadius: 'var(--radius-sm)', fontSize: 'var(--fs-sm)',
            backgroundColor: 'var(--error-bg)', color: 'var(--error)',
            marginBottom: 8,
          }}>
            {testResult.error}
          </div>
        )}

        {/* Result summary */}
        {testResult?.success && testResult.data != null && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Badge tone="ok">
              <Icon icon={Check} size={12} />
              {Array.isArray(testResult.data) ? `${dataCount} items` : typeof testResult.data === 'object' ? `${dataCount} keys` : String(testResult.data)}
            </Badge>
            <Button variant="secondary" size="sm" onClick={() => setShowData(!showData)}>
              {showData ? 'Hide data' : 'Show data'}
            </Button>
          </div>
        )}

        {/* Data preview */}
        {showData && testResult?.data != null && (
          <pre style={{
            ...codeSurface, fontSize: 'var(--fs-xs)', padding: '8px 10px', borderRadius: 'var(--radius-sm)', maxHeight: 300,
            overflow: 'auto', margin: '8px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {JSON.stringify(testResult.data, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
