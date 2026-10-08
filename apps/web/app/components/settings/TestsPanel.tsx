'use client';

import { useState, useEffect, useCallback } from 'react';
import { ChevronDown, ChevronRight, LoaderCircle, Play, Sparkles, Trash2, TriangleAlert } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

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

// Run status → badge tone. A queued run is waiting on the runner, not on a
// person, so it stays neutral; a run in progress is informational.
const STATUS_TONES: Record<string, BadgeTone> = {
  passed: 'ok',
  failed: 'error',
  error: 'error',
  pending: 'neutral',
  running: 'info',
};

const sectionTitle: React.CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
const subTitle: React.CSSProperties = { fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)', marginBottom: 6 };
// Error text and logs from the runner read as code: the warm dark block.
const codeBlock: React.CSSProperties = {
  margin: 0, padding: '10px 12px', borderRadius: 'var(--radius)',
  background: 'var(--code-bg)', color: 'var(--code-text)',
  fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', lineHeight: 1.5,
  overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
};

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
    if (!status) return <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>—</span>;
    return (
      <Badge tone={STATUS_TONES[status] || 'neutral'}>
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </Badge>
    );
  };

  return (
    <div style={{ lineHeight: 1.45 }}>
      {/* ── Test builder ────────────────────────────────── */}
      <h3 style={{ ...sectionTitle, marginBottom: 12 }}>Test builder</h3>

      <div className="n-card" style={{ padding: 16, marginBottom: 32 }}>
        <textarea
          className="n-input"
          aria-label="Test description"
          value={description}
          onChange={e => setDescription(e.target.value)}
          placeholder="Describe a user flow in natural language, e.g. 'Log in, navigate to settings, and verify the connectors tab loads'"
          style={{ display: 'block', width: '100%', minHeight: 80 }}
        />
        <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          {/* Once steps exist, saving them is the card's main action. */}
          <Button
            variant={generatedSteps.length > 0 ? 'secondary' : 'primary'}
            icon={Sparkles}
            onClick={handleGenerate}
            disabled={generating || !description.trim()}
          >
            {generating ? 'Generating…' : 'Generate test'}
          </Button>
          {saveError && <span role="alert" style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{saveError}</span>}
        </div>

        {/* Generated output */}
        {generatedSteps.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div style={subTitle}>Generated steps</div>
            <div style={{
              background: 'var(--surface)', border: '1px solid var(--line)',
              borderRadius: 'var(--radius)', padding: '6px 12px',
            }}>
              {generatedSteps.map((s, i) => (
                <div key={i} style={{
                  display: 'flex', alignItems: 'flex-start', gap: 10,
                  padding: '6px 0', fontSize: 'var(--fs-base)', color: 'var(--text)',
                }}>
                  <span style={{
                    width: 22, height: 22, borderRadius: '50%', background: 'var(--selected)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 'var(--fs-2xs)', fontWeight: 600, color: 'var(--text-soft)', flexShrink: 0,
                    fontVariantNumeric: 'tabular-nums',
                  }}>
                    {s.step}
                  </span>
                  <div style={{ minWidth: 0, paddingTop: 1 }}>
                    <div>{s.description}</div>
                    {s.selector && (
                      <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontFamily: 'var(--font-mono)', overflowWrap: 'anywhere' }}>
                        {s.selector}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Save controls */}
            <div style={{ marginTop: 12, maxWidth: 560, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              <input
                type="text"
                className="n-input"
                aria-label="Test name"
                value={saveName}
                onChange={e => setSaveName(e.target.value)}
                placeholder="Test name"
                style={{ flex: '1 1 160px', minWidth: 0 }}
              />
              <Button variant="primary" onClick={handleSave} disabled={!saveName.trim()}>
                Save to suite
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* ── Test suite ──────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', gap: 12, marginBottom: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 10, minWidth: 0 }}>
          <h3 style={sectionTitle}>Test suite</h3>
          {!loading && tests.length > 0 && (
            <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              {tests.length} test{tests.length !== 1 ? 's' : ''}
              {passedCount > 0 && <> · <span style={{ color: 'var(--ok)' }}>{passedCount} passed</span></>}
              {failedCount > 0 && <> · <span style={{ color: 'var(--error)' }}>{failedCount} failed</span></>}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            className="n-select"
            aria-label="Environment"
            title="Environment"
            value={environment}
            onChange={e => setEnvironment(e.target.value)}
          >
            <option value="local">local</option>
            <option value="testing">testing</option>
            <option value="staging">staging</option>
          </select>
          <Button
            icon={Play}
            onClick={handleRunAll}
            disabled={runningAll || tests.length === 0}
          >
            {runningAll ? 'Running…' : 'Run all'}
          </Button>
        </div>
      </div>

      {loading ? (
        <PageState kind="loading" title="Loading tests…" />
      ) : tests.length === 0 ? (
        <div className="n-card">
          <PageState kind="empty" title="No tests yet." detail="Use the test builder above to generate and save tests." />
        </div>
      ) : (
        <div className="n-card" style={{ overflow: 'hidden' }}>
          {tests.map((t, index) => {
            const isExpanded = expandedId === t.id;
            const runs = runsByTest[t.id] || [];
            const latestRun = runs[0];
            const isRunning = runningTest === t.id;
            return (
              <div key={t.id} style={{ borderTop: index > 0 ? '1px solid var(--line)' : 'none' }}>
                <div
                  onClick={() => {
                    const next = isExpanded ? null : t.id;
                    setExpandedId(next);
                    if (next) fetchRunsForTest(next);
                  }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '10px 12px 10px 16px', cursor: 'pointer',
                  }}
                >
                  <Icon icon={isExpanded ? ChevronDown : ChevronRight} size="inline" tone="muted" />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)' }}>{t.name}</div>
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 2 }}>
                      {t.last_run_at
                        ? `Last run: ${new Date(t.last_run_at).toLocaleString()}`
                        : 'Never run'}
                    </div>
                  </div>
                  {statusBadge(t.last_run_status)}
                  <div style={{ display: 'flex', gap: 2 }}>
                    <button
                      type="button"
                      className="n-icon-btn"
                      onClick={(e) => { e.stopPropagation(); handleRunSingle(t.id); }}
                      disabled={isRunning}
                      aria-label={isRunning ? 'Running test' : 'Run test'}
                      title="Run test"
                    >
                      {isRunning
                        ? <Icon icon={LoaderCircle} size={16} style={{ animation: 'n-spin 1s linear infinite' }} />
                        : <Icon icon={Play} size={16} />}
                    </button>
                    <IconButton
                      icon={Trash2}
                      label="Delete test"
                      iconSize={16}
                      onClick={(e) => { e.stopPropagation(); handleDelete(t.id); }}
                    />
                  </div>
                </div>
                {isExpanded && (
                  <div style={{
                    borderTop: '1px solid var(--line-soft)', padding: '12px 16px 16px',
                    background: 'var(--surface)', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)',
                  }}>
                    {latestRun ? (
                      <>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', marginBottom: 10, fontVariantNumeric: 'tabular-nums' }}>
                          <span>Duration: <strong style={{ fontWeight: 600, color: 'var(--text)' }}>{latestRun.duration_ms ? (latestRun.duration_ms / 1000).toFixed(2) + 's' : '—'}</strong></span>
                          <span>Environment: <strong style={{ fontWeight: 600, color: 'var(--text)' }}>{latestRun.environment}</strong></span>
                          <span>Triggered by: <strong style={{ fontWeight: 600, color: 'var(--text)' }}>{latestRun.triggered_by || 'unknown'}</strong></span>
                        </div>
                        {latestRun.error_message && (
                          <div style={{ marginBottom: 10 }}>
                            <div style={{ ...subTitle, color: 'var(--error)', display: 'flex', alignItems: 'center', gap: 6 }}>
                              <Icon icon={TriangleAlert} size="dense" />
                              Error
                            </div>
                            <pre style={codeBlock}>{latestRun.error_message}</pre>
                          </div>
                        )}
                        {latestRun.stdout && (
                          <div style={{ marginBottom: 10 }}>
                            <div style={subTitle}>Logs</div>
                            <pre style={{ ...codeBlock, maxHeight: 300 }}>{latestRun.stdout}</pre>
                          </div>
                        )}
                        {!latestRun.error_message && !latestRun.stdout && (
                          <div style={{ color: 'var(--muted)' }}>No output captured.</div>
                        )}
                        {runs.length > 1 && (
                          <div style={{ marginTop: 12 }}>
                            <div style={subTitle}>Recent runs</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                              {runs.slice(1, 6).map(r => (
                                <div key={r.id} style={{
                                  display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '2px 12px',
                                  fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', fontVariantNumeric: 'tabular-nums',
                                }}>
                                  <span style={{ minWidth: 64 }}>{statusBadge(r.status)}</span>
                                  <span>{r.started_at ? new Date(r.started_at).toLocaleString() : '—'}</span>
                                  <span>{r.duration_ms ? (r.duration_ms / 1000).toFixed(2) + 's' : '—'}</span>
                                  <span style={{ color: 'var(--muted)' }}>{r.environment}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </>
                    ) : (
                      <div style={{ color: 'var(--muted)' }}>No runs yet. Click the play button to run this test.</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
