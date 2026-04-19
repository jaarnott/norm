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
  stdout: string | null;
  triggered_by: string | null;
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
  const [environment, setEnvironment] = useState(
    typeof window !== 'undefined' && window.location.hostname === 'localhost'
      ? 'local'
      : 'testing'
  );
  const [runningAll, setRunningAll] = useState(false);
  const [runningTest, setRunningTest] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [runsByTest, setRunsByTest] = useState<Record<string, TestRun[]>>({});

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

  const fetchRunsForTest = useCallback(async (testId: string) => {
    try {
      const res = await apiFetch(`/api/admin/test-runs?test_id=${testId}&limit=10`);
      if (res.ok) {
        const data = await res.json();
        setRunsByTest(prev => ({ ...prev, [testId]: data.runs || [] }));
      }
    } catch { /* ignore */ }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { fetchTests(); }, [fetchTests]);

  // Poll while any test is running (pending/running status)
  useEffect(() => {
    const hasRunning = runningAll || runningTest !== null;
    if (!hasRunning) return;
    const interval = setInterval(() => {
      fetchTests();
      if (expandedId) fetchRunsForTest(expandedId);
    }, 2000);
    // Stop polling after 60s
    const timeout = setTimeout(() => {
      setRunningAll(false);
      setRunningTest(null);
    }, 60000);
    return () => {
      clearInterval(interval);
      clearTimeout(timeout);
    };
  }, [runningAll, runningTest, expandedId, fetchTests, fetchRunsForTest]);

  // Clear running flags when last run completes
  useEffect(() => {
    if (!runningTest && !runningAll) return;
    const t = tests.find(t => t.id === runningTest);
    if (t && t.last_run_status && t.last_run_status !== 'pending' && t.last_run_status !== 'running') {
      // Check if the last_run_at is recent (within last 2 min) to confirm this run completed
      const last = t.last_run_at ? new Date(t.last_run_at).getTime() : 0;
      if (Date.now() - last < 120000) {
        setRunningTest(null);
      }
    }
    if (runningAll && tests.every(t => t.last_run_status && t.last_run_status !== 'pending' && t.last_run_status !== 'running')) {
      const mostRecent = Math.max(...tests.map(t => t.last_run_at ? new Date(t.last_run_at).getTime() : 0));
      if (Date.now() - mostRecent < 120000) {
        setRunningAll(false);
      }
    }
  }, [tests, runningTest, runningAll]);

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
            <option value="local">local</option>
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
          {tests.map(t => {
            const isExpanded = expandedId === t.id;
            const runs = runsByTest[t.id] || [];
            const latestRun = runs[0];
            return (
              <div
                key={t.id}
                style={{
                  backgroundColor: '#fff',
                  border: '1px solid #e5e7eb',
                  borderRadius: 6,
                  overflow: 'hidden',
                }}
              >
                <div
                  onClick={() => {
                    const next = isExpanded ? null : t.id;
                    setExpandedId(next);
                    if (next) fetchRunsForTest(next);
                  }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '0.75rem',
                    padding: '0.6rem 0.75rem', cursor: 'pointer',
                  }}
                >
                  <span style={{ fontSize: '0.7rem', color: '#999', width: 10 }}>{isExpanded ? '▾' : '▸'}</span>
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
                    onClick={(e) => { e.stopPropagation(); handleRunSingle(t.id); }}
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
                    onClick={(e) => { e.stopPropagation(); handleDelete(t.id); }}
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
                {isExpanded && (
                  <div style={{ borderTop: '1px solid #e5e7eb', padding: '0.75rem', backgroundColor: '#fafafa', fontSize: '0.75rem' }}>
                    {latestRun ? (
                      <>
                        <div style={{ display: 'flex', gap: '1rem', marginBottom: '0.5rem', color: '#666' }}>
                          <span>Duration: <strong>{latestRun.duration_ms ? (latestRun.duration_ms / 1000).toFixed(2) + 's' : '—'}</strong></span>
                          <span>Environment: <strong>{latestRun.environment}</strong></span>
                          <span>Triggered by: <strong>{latestRun.triggered_by || 'unknown'}</strong></span>
                        </div>
                        {latestRun.error_message && (
                          <div style={{ marginBottom: '0.5rem' }}>
                            <div style={{ fontWeight: 600, color: '#991b1b', marginBottom: 2 }}>Error</div>
                            <pre style={{ margin: 0, padding: '0.5rem', backgroundColor: '#fff', border: '1px solid #fecaca', borderRadius: 4, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: '0.7rem' }}>{latestRun.error_message}</pre>
                          </div>
                        )}
                        {latestRun.stdout && (
                          <div style={{ marginBottom: '0.5rem' }}>
                            <div style={{ fontWeight: 600, color: '#333', marginBottom: 2 }}>Logs</div>
                            <pre style={{ margin: 0, padding: '0.5rem', backgroundColor: '#fff', border: '1px solid #ddd', borderRadius: 4, overflow: 'auto', maxHeight: 300, whiteSpace: 'pre-wrap', fontSize: '0.7rem' }}>{latestRun.stdout}</pre>
                          </div>
                        )}
                        {!latestRun.error_message && !latestRun.stdout && (
                          <div style={{ color: '#999', fontStyle: 'italic' }}>No output captured.</div>
                        )}
                        {runs.length > 1 && (
                          <div style={{ marginTop: '0.75rem' }}>
                            <div style={{ fontWeight: 600, color: '#333', marginBottom: 4 }}>Recent runs</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                              {runs.slice(1, 6).map(r => (
                                <div key={r.id} style={{ display: 'flex', gap: '0.75rem', fontSize: '0.7rem', color: '#666' }}>
                                  <span style={{ width: 60 }}>{statusBadge(r.status)}</span>
                                  <span>{r.started_at ? new Date(r.started_at).toLocaleString() : '—'}</span>
                                  <span>{r.duration_ms ? (r.duration_ms / 1000).toFixed(2) + 's' : '—'}</span>
                                  <span style={{ color: '#999' }}>{r.environment}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </>
                    ) : (
                      <div style={{ color: '#999', fontStyle: 'italic' }}>No runs yet. Click the play button to run this test.</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}

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
