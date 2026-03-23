'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';

interface TestStep {
  step: number;
  description: string;
  selector: string | null;
}

interface E2ETest {
  id: string;
  name: string;
  description: string;
  playwright_script: string;
  steps: TestStep[];
  last_run_status: string | null;
  last_run_at: string | null;
  created_at: string | null;
}

interface TestRun {
  id: string;
  test_id: string | null;
  environment: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  error_message: string | null;
}

export default function TestsPanel() {
  // Test Builder state
  const [description, setDescription] = useState('');
  const [generating, setGenerating] = useState(false);
  const [generatedSteps, setGeneratedSteps] = useState<TestStep[]>([]);
  const [generatedScript, setGeneratedScript] = useState('');
  const [saveName, setSaveName] = useState('');
  const [saveError, setSaveError] = useState('');

  // Test Suite state
  const [tests, setTests] = useState<E2ETest[]>([]);
  const [loading, setLoading] = useState(true);
  const [environment, setEnvironment] = useState('testing');
  const [runningAll, setRunningAll] = useState(false);
  const [runningTest, setRunningTest] = useState<string | null>(null);

  const fetchTests = useCallback(async () => {
    try {
      const res = await apiFetch('/api/admin/tests');
      if (res.ok) {
        const data = await res.json();
        setTests(data.tests || []);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchTests(); }, [fetchTests]);

  const handleGenerate = async () => {
    if (!description.trim()) return;
    setGenerating(true);
    setGeneratedSteps([]);
    setGeneratedScript('');
    setSaveError('');
    try {
      const res = await apiFetch('/api/admin/tests/generate', {
        method: 'POST',
        body: JSON.stringify({ description }),
      });
      if (res.ok) {
        const data = await res.json();
        setGeneratedSteps(data.steps || []);
        setGeneratedScript(data.playwright_script || '');
      } else {
        setSaveError('Failed to generate test');
      }
    } catch {
      setSaveError('Failed to generate test');
    }
    setGenerating(false);
  };

  const handleSave = async () => {
    if (!saveName.trim()) {
      setSaveError('Please enter a test name');
      return;
    }
    try {
      const res = await apiFetch('/api/admin/tests', {
        method: 'POST',
        body: JSON.stringify({
          name: saveName,
          description,
          playwright_script: generatedScript,
          steps: generatedSteps,
        }),
      });
      if (res.ok) {
        setDescription('');
        setGeneratedSteps([]);
        setGeneratedScript('');
        setSaveName('');
        setSaveError('');
        fetchTests();
      } else {
        setSaveError('Failed to save test');
      }
    } catch {
      setSaveError('Failed to save test');
    }
  };

  const handleDelete = async (id: string) => {
    await apiFetch(`/api/admin/tests/${id}`, { method: 'DELETE' });
    fetchTests();
  };

  const handleRunSingle = async (id: string) => {
    setRunningTest(id);
    try {
      await apiFetch('/api/admin/tests/run', {
        method: 'POST',
        body: JSON.stringify({ environment, test_ids: [id] }),
      });
      fetchTests();
    } catch { /* ignore */ }
    setRunningTest(null);
  };

  const handleRunAll = async () => {
    setRunningAll(true);
    try {
      await apiFetch('/api/admin/tests/run', {
        method: 'POST',
        body: JSON.stringify({ environment }),
      });
      fetchTests();
    } catch { /* ignore */ }
    setRunningAll(false);
  };

  const passedCount = tests.filter(t => t.last_run_status === 'passed').length;
  const failedCount = tests.filter(t => t.last_run_status === 'failed' || t.last_run_status === 'error').length;

  const statusBadge = (status: string | null) => {
    if (!status) return <span style={{ color: '#888', fontSize: '0.75rem' }}>--</span>;
    const colors: Record<string, { bg: string; fg: string }> = {
      passed: { bg: '#d1fae5', fg: '#065f46' },
      failed: { bg: '#fee2e2', fg: '#991b1b' },
      error: { bg: '#fee2e2', fg: '#991b1b' },
      pending: { bg: '#fef3c7', fg: '#92400e' },
      running: { bg: '#dbeafe', fg: '#1e40af' },
    };
    const c = colors[status] || { bg: '#f3f4f6', fg: '#666' };
    return (
      <span style={{
        fontSize: '0.68rem', fontWeight: 600, padding: '2px 8px', borderRadius: 8,
        backgroundColor: c.bg, color: c.fg,
      }}>
        {status}
      </span>
    );
  };

  return (
    <div>
      {/* ── Test Builder ────────────────────────────────── */}
      <h3 style={{
        margin: '0 0 1rem', fontSize: '0.85rem', fontWeight: 600,
        color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em',
      }}>
        Test Builder
      </h3>

      <div style={{
        backgroundColor: '#fff', border: '1px solid #e5e7eb', borderRadius: 8,
        padding: '1rem', marginBottom: '1.5rem',
      }}>
        <textarea
          value={description}
          onChange={e => setDescription(e.target.value)}
          placeholder="Describe a user flow in natural language, e.g. 'Log in, navigate to settings, and verify the connectors tab loads'"
          style={{
            width: '100%', minHeight: 80, padding: '0.6rem', fontSize: '0.82rem',
            border: '1px solid #ddd', borderRadius: 6, fontFamily: 'inherit',
            resize: 'vertical', boxSizing: 'border-box',
          }}
        />
        <div style={{ marginTop: '0.5rem', display: 'flex', gap: 8, alignItems: 'center' }}>
          <button
            onClick={handleGenerate}
            disabled={generating || !description.trim()}
            style={{
              padding: '6px 16px', fontSize: '0.78rem', fontWeight: 600,
              border: 'none', borderRadius: 6, cursor: 'pointer', fontFamily: 'inherit',
              backgroundColor: generating || !description.trim() ? '#ccc' : '#111',
              color: '#fff',
            }}
          >
            {generating ? 'Generating...' : 'Generate Test'}
          </button>
          {saveError && <span style={{ fontSize: '0.75rem', color: '#dc2626' }}>{saveError}</span>}
        </div>

        {/* Generated output */}
        {generatedSteps.length > 0 && (
          <div style={{ marginTop: '1rem' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 600, color: '#333', marginBottom: '0.5rem' }}>
              Generated Steps
            </div>
            <div style={{
              backgroundColor: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 6,
              padding: '0.75rem',
            }}>
              {generatedSteps.map((s, i) => (
                <div key={i} style={{
                  display: 'flex', alignItems: 'flex-start', gap: 8,
                  padding: '4px 0', fontSize: '0.78rem', color: '#333',
                }}>
                  <span style={{
                    width: 20, height: 20, borderRadius: '50%', backgroundColor: '#e5e7eb',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '0.65rem', fontWeight: 600, color: '#666', flexShrink: 0,
                  }}>
                    {s.step}
                  </span>
                  <div>
                    <div>{s.description}</div>
                    {s.selector && (
                      <div style={{ fontSize: '0.68rem', color: '#999', fontFamily: 'monospace' }}>
                        {s.selector}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Save controls */}
            <div style={{ marginTop: '0.75rem', display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="text"
                value={saveName}
                onChange={e => setSaveName(e.target.value)}
                placeholder="Test name"
                style={{
                  flex: 1, padding: '6px 10px', fontSize: '0.78rem',
                  border: '1px solid #ddd', borderRadius: 6, fontFamily: 'inherit',
                }}
              />
              <button
                onClick={handleSave}
                disabled={!saveName.trim()}
                style={{
                  padding: '6px 16px', fontSize: '0.78rem', fontWeight: 600,
                  border: 'none', borderRadius: 6, cursor: 'pointer', fontFamily: 'inherit',
                  backgroundColor: !saveName.trim() ? '#ccc' : '#065f46',
                  color: '#fff',
                }}
              >
                Save to Suite
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Test Suite ──────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: '0.75rem',
      }}>
        <h3 style={{
          margin: 0, fontSize: '0.85rem', fontWeight: 600,
          color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em',
        }}>
          Test Suite
        </h3>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            value={environment}
            onChange={e => setEnvironment(e.target.value)}
            style={{
              padding: '4px 8px', fontSize: '0.75rem', border: '1px solid #ddd',
              borderRadius: 6, fontFamily: 'inherit', backgroundColor: '#fff',
            }}
          >
            <option value="testing">testing</option>
            <option value="staging">staging</option>
          </select>
          <button
            onClick={handleRunAll}
            disabled={runningAll || tests.length === 0}
            style={{
              padding: '5px 14px', fontSize: '0.75rem', fontWeight: 600,
              border: 'none', borderRadius: 6, cursor: 'pointer', fontFamily: 'inherit',
              backgroundColor: runningAll || tests.length === 0 ? '#ccc' : '#111',
              color: '#fff',
            }}
          >
            {runningAll ? 'Running...' : 'Run All'}
          </button>
        </div>
      </div>

      {loading ? (
        <div style={{ fontSize: '0.8rem', color: '#999', padding: '1rem 0' }}>Loading tests...</div>
      ) : tests.length === 0 ? (
        <div style={{
          fontSize: '0.8rem', color: '#999', padding: '2rem',
          textAlign: 'center', border: '1px dashed #ddd', borderRadius: 8,
        }}>
          No tests yet. Use the Test Builder above to generate and save tests.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {tests.map(t => (
            <div
              key={t.id}
              style={{
                display: 'flex', alignItems: 'center', gap: '0.75rem',
                padding: '0.6rem 0.75rem', backgroundColor: '#fff',
                border: '1px solid #e5e7eb', borderRadius: 6,
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '0.82rem', fontWeight: 500, color: '#111' }}>{t.name}</div>
                <div style={{ fontSize: '0.7rem', color: '#999', marginTop: 2 }}>
                  {t.last_run_at
                    ? `Last run: ${new Date(t.last_run_at).toLocaleString()}`
                    : 'Never run'}
                </div>
              </div>
              {statusBadge(t.last_run_status)}
              <button
                onClick={() => handleRunSingle(t.id)}
                disabled={runningTest === t.id}
                title="Run test"
                style={{
                  width: 28, height: 28, border: '1px solid #ddd', borderRadius: 6,
                  backgroundColor: '#fff', cursor: 'pointer', display: 'flex',
                  alignItems: 'center', justifyContent: 'center', fontSize: '0.82rem',
                  color: runningTest === t.id ? '#ccc' : '#333',
                }}
              >
                {runningTest === t.id ? '...' : '\u25B6'}
              </button>
              <button
                onClick={() => handleDelete(t.id)}
                title="Delete test"
                style={{
                  width: 28, height: 28, border: '1px solid #ddd', borderRadius: 6,
                  backgroundColor: '#fff', cursor: 'pointer', display: 'flex',
                  alignItems: 'center', justifyContent: 'center', fontSize: '0.9rem',
                  color: '#999',
                }}
              >
                &times;
              </button>
            </div>
          ))}

          {/* Summary */}
          <div style={{
            marginTop: '0.5rem', fontSize: '0.72rem', color: '#888',
            display: 'flex', gap: '1rem',
          }}>
            <span>{tests.length} test{tests.length !== 1 ? 's' : ''}</span>
            {passedCount > 0 && <span style={{ color: '#065f46' }}>{passedCount} passed</span>}
            {failedCount > 0 && <span style={{ color: '#991b1b' }}>{failedCount} failed</span>}
          </div>
        </div>
      )}
    </div>
  );
}
