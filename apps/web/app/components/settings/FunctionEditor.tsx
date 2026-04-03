'use client';

import { useState, useCallback } from 'react';
import { apiFetch } from '../../lib/api';

interface Props {
  functionCode: string;
  onChange: (code: string) => void;
  requiredFields: string[];
  connectorName: string;
}

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '4px 8px', fontSize: '0.78rem', fontFamily: 'inherit',
  border: '1px solid #e2e8f0', borderRadius: 4, boxSizing: 'border-box' as const,
};

const btnSmall: React.CSSProperties = {
  padding: '4px 12px', fontSize: '0.72rem', fontWeight: 600, border: 'none',
  borderRadius: 6, cursor: 'pointer', fontFamily: 'inherit',
};

export default function FunctionEditor({ functionCode, onChange, requiredFields, connectorName }: Props) {
  const [testParams, setTestParams] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    data: unknown;
    _logs?: string[];
    error?: string;
  } | null>(null);
  const [showData, setShowData] = useState(false);

  const handleTest = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await apiFetch('/api/connector-specs/norm/test-consolidator', {
        method: 'POST',
        body: JSON.stringify({
          consolidator_config: { function_code: functionCode },
          params: testParams,
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
  }, [functionCode, testParams]);

  const dataCount = testResult?.data
    ? Array.isArray(testResult.data) ? testResult.data.length
    : typeof testResult.data === 'object' ? Object.keys(testResult.data as Record<string, unknown>).length
    : 1
    : 0;

  return (
    <div>
      {/* Code editor */}
      <div style={{ marginBottom: '0.5rem' }}>
        <div style={{ fontSize: '0.68rem', fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.03em', marginBottom: 4 }}>
          Function
        </div>
        <textarea
          value={functionCode}
          onChange={e => onChange(e.target.value)}
          spellCheck={false}
          style={{
            width: '100%', minHeight: 250, padding: '0.75rem', fontSize: '0.78rem',
            fontFamily: "'Fira Code', 'Cascadia Code', 'Consolas', monospace",
            lineHeight: 1.5, border: '1px solid #e2e8f0', borderRadius: 8,
            backgroundColor: '#1e1e2e', color: '#cdd6f4', resize: 'vertical',
            boxSizing: 'border-box', tabSize: 4,
            outline: 'none',
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
      <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: '0.6rem', backgroundColor: '#fafbfc' }}>
        <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap', marginBottom: '0.4rem' }}>
          {requiredFields.map(f => (
            <div key={f} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <label style={{ fontSize: '0.68rem', color: '#6b7280', fontWeight: 500 }}>{f}:</label>
              <input
                value={testParams[f] || ''}
                onChange={e => setTestParams(p => ({ ...p, [f]: e.target.value }))}
                placeholder={f}
                style={{ ...inputStyle, width: 140, fontSize: '0.72rem' }}
              />
            </div>
          ))}
          <button
            onClick={handleTest}
            disabled={testing}
            style={{ ...btnSmall, backgroundColor: '#111', color: '#fff', opacity: testing ? 0.6 : 1 }}
          >
            {testing ? 'Running...' : 'Run Test'}
          </button>
        </div>

        {/* Logs */}
        {testResult?._logs && testResult._logs.length > 0 && (
          <div style={{
            backgroundColor: '#1e1e2e', borderRadius: 6, padding: '0.5rem',
            marginBottom: '0.4rem', maxHeight: 200, overflowY: 'auto',
          }}>
            {testResult._logs.map((log, i) => (
              <div key={i} style={{
                fontSize: '0.72rem', fontFamily: 'monospace', lineHeight: 1.6,
                color: log.startsWith('ERROR') ? '#f87171'
                  : log.startsWith('API:') ? '#93c5fd'
                  : log.startsWith('Completed') ? '#86efac'
                  : '#cdd6f4',
              }}>
                <span style={{ color: '#6b7280', marginRight: 6 }}>{'>'}</span>
                {log}
              </div>
            ))}
          </div>
        )}

        {/* Error */}
        {testResult?.error && (
          <div style={{
            padding: '0.4rem 0.6rem', borderRadius: 6, fontSize: '0.72rem',
            backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b',
            marginBottom: '0.4rem',
          }}>
            {testResult.error}
          </div>
        )}

        {/* Result summary */}
        {testResult?.success && testResult.data != null && (
          <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
            <span style={{
              fontSize: '0.72rem', fontWeight: 600, padding: '2px 8px', borderRadius: 10,
              backgroundColor: '#d1fae5', color: '#065f46',
            }}>
              ✓ {Array.isArray(testResult.data) ? `${dataCount} items` : typeof testResult.data === 'object' ? `${dataCount} keys` : String(testResult.data)}
            </span>
            <button
              onClick={() => setShowData(!showData)}
              style={{ ...btnSmall, border: '1px solid #d1d5db', backgroundColor: '#fff', color: '#555', fontWeight: 500, padding: '2px 8px' }}
            >
              {showData ? 'Hide Data' : 'Show Data'}
            </button>
          </div>
        )}

        {/* Data preview */}
        {showData && testResult?.data != null && (
          <pre style={{
            fontSize: '0.7rem', fontFamily: 'monospace', backgroundColor: '#1e1e2e',
            color: '#cdd6f4', padding: '0.5rem', borderRadius: 6, maxHeight: 300,
            overflow: 'auto', marginTop: '0.4rem', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {JSON.stringify(testResult.data, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
