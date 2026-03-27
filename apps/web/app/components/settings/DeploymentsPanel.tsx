'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import { Check, ChevronDown, ChevronRight, Circle, ExternalLink, RefreshCw, Rocket, RotateCcw, X } from 'lucide-react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EnvironmentDeploy {
  image_tag: string;
  git_sha: string;
  status: string;
  started_at: string;
  commit_message: string;
}

interface Environment {
  name: string;
  latest_deploy: EnvironmentDeploy | null;
}

interface Deployment {
  id: string;
  environment: string;
  image_tag: string;
  git_sha: string;
  commit_message: string;
  status: 'pending' | 'running' | 'success' | 'failed';
  started_at: string;
  completed_at: string | null;
  logs_url: string | null;
  triggered_by: string | null;
}

type DiffStatus = 'added' | 'modified' | 'removed' | 'unchanged';

interface FieldChange {
  field: string;
  old_value: string;
  new_value: string;
}

interface DiffItem {
  key: string;
  status: DiffStatus;
  changes?: FieldChange[];
}

interface DiffResult {
  connector_specs: DiffItem[];
  agent_configs: DiffItem[];
  agent_bindings: DiffItem[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  if (diffMs < 0) return 'just now';
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

function healthColor(status: string | undefined): string {
  if (!status) return '#666';
  if (status === 'success') return '#48bb78';
  if (status === 'running' || status === 'pending') return '#ecc94b';
  return '#fc8181';
}

function statusBadge(status: string): { bg: string; color: string } {
  switch (status) {
    case 'pending': return { bg: 'rgba(236, 201, 75, 0.15)', color: '#ecc94b' };
    case 'running': return { bg: 'rgba(66, 153, 225, 0.15)', color: '#4299e1' };
    case 'success': return { bg: 'rgba(72, 187, 120, 0.15)', color: '#48bb78' };
    case 'failed': return { bg: 'rgba(252, 129, 129, 0.15)', color: '#fc8181' };
    default: return { bg: 'rgba(160, 174, 192, 0.15)', color: '#a0aec0' };
  }
}

// ---------------------------------------------------------------------------
// Diff badge helpers
// ---------------------------------------------------------------------------

function diffBadgeStyle(status: DiffStatus): { bg: string; color: string } {
  switch (status) {
    case 'added': return { bg: 'rgba(72, 187, 120, 0.15)', color: '#48bb78' };
    case 'modified': return { bg: 'rgba(236, 201, 75, 0.15)', color: '#ecc94b' };
    case 'removed': return { bg: 'rgba(252, 129, 129, 0.15)', color: '#fc8181' };
    case 'unchanged': return { bg: 'rgba(160, 174, 192, 0.1)', color: '#718096' };
  }
}

/** Transform backend diff format {added:[], modified:[], removed:[], unchanged:[]} into flat DiffItem[]. */
function normalizeDiffSection(section: DiffItem[] | Record<string, unknown[]>): DiffItem[] {
  if (Array.isArray(section)) return section;
  const items: DiffItem[] = [];
  for (const status of ['added', 'modified', 'removed', 'unchanged'] as DiffStatus[]) {
    const arr = (section as Record<string, unknown[]>)[status];
    if (Array.isArray(arr)) {
      for (const item of arr) {
        const raw = item as Record<string, unknown>;
        const key = (raw.connector_name || raw.agent_slug || `${raw.agent_slug}:${raw.connector_name}`) as string;
        items.push({ key, status, changes: raw.changes as FieldChange[] | undefined });
      }
    }
  }
  return items;
}

function normalizeDiffResult(raw: Record<string, unknown>): DiffResult {
  return {
    connector_specs: normalizeDiffSection(raw.connector_specs as DiffItem[] | Record<string, unknown[]>),
    agent_configs: normalizeDiffSection(raw.agent_configs as DiffItem[] | Record<string, unknown[]>),
    agent_bindings: normalizeDiffSection(raw.agent_bindings as DiffItem[] | Record<string, unknown[]>),
  };
}

function diffSummary(items: DiffItem[]): string {
  const added = items.filter(i => i.status === 'added').length;
  const modified = items.filter(i => i.status === 'modified').length;
  const removed = items.filter(i => i.status === 'removed').length;
  const unchanged = items.filter(i => i.status === 'unchanged').length;
  return `${added} added, ${modified} modified, ${removed} removed, ${unchanged} unchanged`;
}

// ---------------------------------------------------------------------------
// Configuration Sync sub-component
// ---------------------------------------------------------------------------

function ConfigurationSync({ sectionStyle, headingStyle }: {
  sectionStyle: React.CSSProperties;
  headingStyle: React.CSSProperties;
}) {
  const ALL_ENVS = ['local', 'testing', 'staging', 'production'] as const;
  type Env = typeof ALL_ENVS[number];

  // Detect current environment from hostname, filter source options
  const currentEnv: Env = typeof window !== 'undefined'
    ? (window.location.hostname === 'localhost' || window.location.hostname.includes('.app.github.dev')) ? 'local'
      : window.location.hostname.startsWith('testing.') ? 'testing'
      : window.location.hostname.startsWith('staging.') ? 'staging'
      : 'production'
    : 'local';

  const ENVS = ALL_ENVS.filter(env => {
    if (env === currentEnv) return false; // can't sync from self
    if (env === 'local' && currentEnv !== 'local') return false; // local only available locally
    return true;
  });

  const [sourceEnv, setSourceEnv] = useState<Env>(ENVS[0] || 'testing');
  const [comparing, setComparing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [diffResult, setDiffResult] = useState<DiffResult | null>(null);
  const [remoteConfig, setRemoteConfig] = useState<Record<string, unknown> | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['connector_specs', 'agent_configs', 'agent_bindings']));
  const [expandedUnchanged, setExpandedUnchanged] = useState<Set<string>>(new Set());
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const clearFeedback = useCallback(() => {
    const timer = setTimeout(() => setFeedback(null), 5000);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (feedback) {
      const cleanup = clearFeedback();
      return cleanup;
    }
  }, [feedback, clearFeedback]);

  const isPushMode = currentEnv === 'local';

  const handleCompare = async () => {
    setComparing(true);
    setFeedback(null);
    setDiffResult(null);
    setSelected(new Set());
    try {
      if (isPushMode) {
        // Push mode: diff local against remote
        const diffRes = await apiFetch('/api/admin/config-diff-remote', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ environment: sourceEnv }),
        });
        if (!diffRes.ok) {
          const d = await diffRes.json();
          setFeedback({ type: 'error', message: d.detail || 'Failed to diff against remote' });
          setComparing(false);
          return;
        }
        const diff = normalizeDiffResult(await diffRes.json());
        setDiffResult(diff);
        setRemoteConfig({}); // Push doesn't need remote config stored
      } else {
        // Pull mode: fetch remote config then diff against local
        const fetchRes = await apiFetch('/api/admin/config-fetch-remote', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ environment: sourceEnv }),
        });
        if (!fetchRes.ok) {
          const d = await fetchRes.json();
          setFeedback({ type: 'error', message: d.detail || 'Failed to fetch remote config' });
          setComparing(false);
          return;
        }
        const configData = await fetchRes.json();
        setRemoteConfig(configData);

        const diffRes = await apiFetch('/api/admin/config-diff', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(configData),
        });
        if (!diffRes.ok) {
          const d = await diffRes.json();
          setFeedback({ type: 'error', message: d.detail || 'Failed to compute diff' });
          setComparing(false);
          return;
        }
        const diff = normalizeDiffResult(await diffRes.json());
        setDiffResult(diff);
      }
    } catch (e) {
      setFeedback({ type: 'error', message: String(e) });
    }
    setComparing(false);
  };

  const toggleSection = (section: string) => {
    setExpandedSections(prev => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section); else next.add(section);
      return next;
    });
  };

  const toggleUnchanged = (section: string) => {
    setExpandedUnchanged(prev => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section); else next.add(section);
      return next;
    });
  };

  const selectableItems = diffResult
    ? [
        ...diffResult.connector_specs.filter(i => i.status === 'added' || i.status === 'modified').map(i => `connector_specs:${i.key}`),
        ...diffResult.agent_configs.filter(i => i.status === 'added' || i.status === 'modified').map(i => `agent_configs:${i.key}`),
        ...diffResult.agent_bindings.filter(i => i.status === 'added' || i.status === 'modified').map(i => `agent_bindings:${i.key}`),
      ]
    : [];

  const toggleItem = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(selectableItems));
  const deselectAll = () => setSelected(new Set());

  const handleApply = async () => {
    if (selected.size === 0) return;
    setApplying(true);
    setFeedback(null);
    try {
      const selectedItems: Record<string, string[]> = { connector_specs: [], agent_configs: [], agent_bindings: [] };
      for (const id of selected) {
        const [section, key] = id.split(':');
        if (selectedItems[section]) selectedItems[section].push(key);
      }

      let res: Response;
      if (isPushMode) {
        // Push mode: push local config to remote
        res = await apiFetch('/api/admin/config-push-remote', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ environment: sourceEnv, selections: selectedItems }),
        });
      } else {
        // Pull mode: import remote config into local DB
        res = await apiFetch('/api/admin/config-import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ config: remoteConfig, selections: selectedItems }),
        });
      }

      if (res.ok) {
        const verb = isPushMode ? `pushed to ${sourceEnv}` : 'applied locally';
        setFeedback({ type: 'success', message: `Selected configuration items ${verb} successfully.` });
        setDiffResult(null);
        setRemoteConfig(null);
        setSelected(new Set());
      } else {
        const d = await res.json();
        setFeedback({ type: 'error', message: d.detail || (isPushMode ? 'Push failed' : 'Import failed') });
      }
    } catch (e) {
      setFeedback({ type: 'error', message: String(e) });
    }
    setApplying(false);
  };


  const btnBase: React.CSSProperties = {
    padding: '6px 14px',
    fontSize: '0.75rem',
    fontWeight: 600,
    borderRadius: 5,
    cursor: 'pointer',
    fontFamily: 'inherit',
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  };

  const renderDiffSection = (title: string, sectionKey: string, items: DiffItem[]) => {
    const isExpanded = expandedSections.has(sectionKey);
    const unchangedExpanded = expandedUnchanged.has(sectionKey);
    const changed = items.filter(i => i.status !== 'unchanged');
    const unchanged = items.filter(i => i.status === 'unchanged');

    return (
      <div key={sectionKey} style={{ marginBottom: '0.75rem' }}>
        <button
          onClick={() => toggleSection(sectionKey)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: '#e2e8f0',
            fontSize: '0.8rem',
            fontWeight: 600,
            fontFamily: 'inherit',
            padding: '4px 0',
            width: '100%',
            textAlign: 'left',
          }}
        >
          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {title}
          <span style={{ fontSize: '0.68rem', color: '#718096', fontWeight: 400, marginLeft: 4 }}>
            {diffSummary(items)}
          </span>
        </button>
        {isExpanded && (
          <div style={{ marginLeft: '1.25rem', marginTop: '0.35rem' }}>
            {changed.map(item => {
              const badge = diffBadgeStyle(item.status);
              const itemId = `${sectionKey}:${item.key}`;
              const isSelectable = item.status === 'added' || item.status === 'modified';
              return (
                <div key={`${item.status}:${item.key}`} style={{ padding: '6px 0', borderBottom: '1px solid #1a1a2e' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {isSelectable && (
                      <input
                        type="checkbox"
                        checked={selected.has(itemId)}
                        onChange={() => toggleItem(itemId)}
                        style={{ accentColor: '#c4a882', cursor: 'pointer' }}
                      />
                    )}
                    <span style={{ fontSize: '0.78rem', color: '#e2e8f0', fontFamily: 'monospace' }}>{item.key}</span>
                    <span style={{
                      fontSize: '0.62rem',
                      fontWeight: 600,
                      padding: '1px 7px',
                      borderRadius: 3,
                      backgroundColor: badge.bg,
                      color: badge.color,
                      textTransform: 'uppercase',
                      letterSpacing: '0.03em',
                    }}>
                      {item.status}
                    </span>
                  </div>
                  {item.status === 'modified' && item.changes && item.changes.length > 0 && (
                    <div style={{ marginLeft: isSelectable ? 28 : 0, marginTop: 4 }}>
                      {item.changes.map(ch => (
                        <div key={ch.field} style={{ fontSize: '0.7rem', color: '#a0aec0', marginBottom: 2, fontFamily: 'monospace' }}>
                          <span style={{ color: '#718096' }}>{ch.field}:</span>{' '}
                          <span style={{ color: '#fc8181', textDecoration: 'line-through' }}>{ch.old_value}</span>{' '}
                          <span style={{ color: '#48bb78' }}>{ch.new_value}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
            {unchanged.length > 0 && (
              <div style={{ marginTop: 4 }}>
                <button
                  onClick={() => toggleUnchanged(sectionKey)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4,
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    color: '#4a5568',
                    fontSize: '0.72rem',
                    fontFamily: 'inherit',
                    padding: '4px 0',
                  }}
                >
                  {unchangedExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                  {unchanged.length} unchanged
                </button>
                {unchangedExpanded && unchanged.map(item => {
                  const badge = diffBadgeStyle(item.status);
                  return (
                    <div key={item.key} style={{ padding: '3px 0 3px 1.25rem', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: '0.75rem', color: '#4a5568', fontFamily: 'monospace' }}>{item.key}</span>
                      <span style={{
                        fontSize: '0.6rem',
                        fontWeight: 600,
                        padding: '1px 6px',
                        borderRadius: 3,
                        backgroundColor: badge.bg,
                        color: badge.color,
                        textTransform: 'uppercase',
                      }}>
                        {item.status}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      {/* ============ CONFIGURATION SYNC ============ */}
      <div style={sectionStyle}>
        <h3 style={{ ...headingStyle, marginBottom: '0.75rem' }}>Configuration Sync</h3>

        {feedback && (
          <div style={{
            padding: '0.5rem 0.75rem',
            backgroundColor: feedback.type === 'success' ? 'rgba(72, 187, 120, 0.1)' : 'rgba(252, 129, 129, 0.1)',
            border: `1px solid ${feedback.type === 'success' ? 'rgba(72, 187, 120, 0.3)' : 'rgba(252, 129, 129, 0.3)'}`,
            borderRadius: 6,
            color: feedback.type === 'success' ? '#48bb78' : '#fc8181',
            fontSize: '0.78rem',
            marginBottom: '0.75rem',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}>
            {feedback.message}
            <button
              onClick={() => setFeedback(null)}
              style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'inherit', padding: 2 }}
            >&times;</button>
          </div>
        )}

        {/* Source selector + Compare */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: '0.75rem', flexWrap: 'wrap' }}>
          <label style={{ fontSize: '0.78rem', color: '#a0aec0' }}>{isPushMode ? 'Push to:' : 'Pull from:'}</label>
          <select
            value={sourceEnv}
            onChange={e => setSourceEnv(e.target.value as Env)}
            style={{
              padding: '5px 10px',
              fontSize: '0.78rem',
              backgroundColor: '#1a1a2e',
              color: '#e2e8f0',
              border: '1px solid #2a2a4a',
              borderRadius: 5,
              fontFamily: 'inherit',
              cursor: 'pointer',
            }}
          >
            {ENVS.map(env => (
              <option key={env} value={env}>{env.charAt(0).toUpperCase() + env.slice(1)}</option>
            ))}
          </select>
          <button
            onClick={handleCompare}
            disabled={comparing}
            style={{
              ...btnBase,
              border: '1px solid #c4a882',
              backgroundColor: 'transparent',
              color: '#c4a882',
              cursor: comparing ? 'not-allowed' : 'pointer',
              opacity: comparing ? 0.6 : 1,
            }}
          >
            <RefreshCw size={12} style={comparing ? { animation: 'spin 1s linear infinite' } : undefined} />
            {comparing ? 'Comparing...' : 'Compare'}
          </button>
        </div>

        {/* Diff view */}
        {diffResult && (
          <div style={{ marginBottom: '0.75rem', padding: '0.75rem', backgroundColor: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 6 }}>
            {/* Select All / Deselect All */}
            {selectableItems.length > 0 && (
              <div style={{ display: 'flex', gap: 8, marginBottom: '0.75rem' }}>
                <button
                  onClick={selectAll}
                  style={{ ...btnBase, border: '1px solid #2a2a4a', backgroundColor: 'transparent', color: '#a0aec0', padding: '4px 10px', fontSize: '0.7rem' }}
                >
                  Select All
                </button>
                <button
                  onClick={deselectAll}
                  style={{ ...btnBase, border: '1px solid #2a2a4a', backgroundColor: 'transparent', color: '#a0aec0', padding: '4px 10px', fontSize: '0.7rem' }}
                >
                  Deselect All
                </button>
              </div>
            )}

            {renderDiffSection('Connector Specs', 'connector_specs', diffResult.connector_specs)}
            {renderDiffSection('Agent Configs', 'agent_configs', diffResult.agent_configs)}
            {renderDiffSection('Agent Bindings', 'agent_bindings', diffResult.agent_bindings)}

            {/* Apply Selected */}
            <div style={{ marginTop: '0.75rem', display: 'flex', justifyContent: 'flex-end' }}>
              <button
                onClick={handleApply}
                disabled={selected.size === 0 || applying}
                style={{
                  ...btnBase,
                  border: 'none',
                  backgroundColor: selected.size === 0 ? '#2a2a4a' : '#c4a882',
                  color: selected.size === 0 ? '#4a5568' : '#0f0f1a',
                  cursor: selected.size === 0 || applying ? 'not-allowed' : 'pointer',
                }}
              >
                <Check size={13} />
                {applying ? (isPushMode ? 'Pushing...' : 'Applying...') : `${isPushMode ? 'Push' : 'Apply'} Selected (${selected.size})`}
              </button>
            </div>
          </div>
        )}

      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function DeploymentsPanel() {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [promoteTarget, setPromoteTarget] = useState<{ sha: string; imageTag: string; commitMessage: string } | null>(null);
  const [promoting, setPromoting] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState<{ env: string; currentImageTag: string } | null>(null);
  const [rollingBack, setRollingBack] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [envRes, depRes] = await Promise.all([
        apiFetch('/api/admin/environments'),
        apiFetch('/api/admin/deployments'),
      ]);
      if (envRes.ok) {
        const data = await envRes.json();
        setEnvironments(data.environments || []);
      }
      if (depRes.ok) {
        const data = await depRes.json();
        setDeployments(data.deployments || []);
      }
      setError(null);
    } catch (e) {
      setError(String(e));
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handlePromote = async () => {
    if (!promoteTarget) return;
    setPromoting(true);
    setError(null);
    try {
      const res = await apiFetch('/api/admin/promote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_tag: promoteTarget.imageTag, target_environment: 'production' }),
      });
      if (res.ok) {
        setPromoteTarget(null);
        await fetchData();
      } else {
        const d = await res.json();
        setError(d.detail || 'Promotion failed');
      }
    } catch (e) {
      setError(String(e));
    }
    setPromoting(false);
  };

  const handleRollback = async () => {
    if (!rollbackTarget) return;
    setRollingBack(true);
    setError(null);
    try {
      const res = await apiFetch('/api/admin/rollback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_tag: rollbackTarget.currentImageTag, target_environment: rollbackTarget.env }),
      });
      if (res.ok) {
        setRollbackTarget(null);
        await fetchData();
      } else {
        const d = await res.json();
        setError(d.detail || 'Rollback failed');
      }
    } catch (e) {
      setError(String(e));
    }
    setRollingBack(false);
  };

  // Determine if staging has a newer deploy than production
  const stagingEnv = environments.find(e => e.name === 'staging');
  const prodEnv = environments.find(e => e.name === 'production');
  const canPromote = !!(
    stagingEnv?.latest_deploy &&
    prodEnv?.latest_deploy &&
    stagingEnv.latest_deploy.git_sha !== prodEnv.latest_deploy.git_sha &&
    stagingEnv.latest_deploy.status === 'success'
  ) || !!(
    stagingEnv?.latest_deploy &&
    !prodEnv?.latest_deploy &&
    stagingEnv.latest_deploy.status === 'success'
  );

  // --- Styles ---
  const cardStyle: React.CSSProperties = {
    flex: 1,
    padding: '1rem',
    backgroundColor: '#1a1a2e',
    border: '1px solid #2a2a4a',
    borderRadius: 8,
    minWidth: 0,
  };
  const sectionStyle: React.CSSProperties = {
    marginBottom: '1.25rem',
    padding: '1rem',
    backgroundColor: '#0f0f1a',
    border: '1px solid #2a2a4a',
    borderRadius: 8,
  };
  const headingStyle: React.CSSProperties = {
    fontSize: '0.82rem',
    fontWeight: 600,
    color: '#c4a882',
    marginBottom: '0.75rem',
    margin: 0,
  };

  if (loading) return <div style={{ padding: '1rem', color: '#888' }}>Loading deployments...</div>;

  return (
    <div data-testid="deployments-panel">
      {error && (
        <div style={{ padding: '0.5rem 0.75rem', backgroundColor: 'rgba(252, 129, 129, 0.1)', border: '1px solid rgba(252, 129, 129, 0.3)', borderRadius: 6, color: '#fc8181', fontSize: '0.8rem', marginBottom: '0.75rem' }}>
          {error}
          <button onClick={() => setError(null)} style={{ float: 'right', border: 'none', background: 'none', cursor: 'pointer', color: '#fc8181' }}>&times;</button>
        </div>
      )}

      {/* ============ ENVIRONMENT CARDS ============ */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Environments</h3>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          {['testing', 'staging', 'production'].map(envName => {
            const env = environments.find(e => e.name === envName);
            const deploy = env?.latest_deploy;
            const isProd = envName === 'production';
            return (
              <div key={envName} style={cardStyle}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: '0.5rem' }}>
                  <Circle
                    size={10}
                    fill={healthColor(deploy?.status)}
                    stroke="none"
                  />
                  <span style={{ fontSize: '0.85rem', fontWeight: 600, color: '#e2e8f0', textTransform: 'capitalize' }}>
                    {envName}
                  </span>
                </div>
                {deploy ? (
                  <>
                    <div style={{ fontSize: '0.78rem', color: '#a0aec0', marginBottom: 2 }}>
                      <span style={{ fontFamily: 'monospace', color: '#c4a882' }}>{shortSha(deploy.git_sha)}</span>
                    </div>
                    <div style={{ fontSize: '0.72rem', color: '#718096', marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {deploy.commit_message}
                    </div>
                    <div style={{ fontSize: '0.68rem', color: '#4a5568' }}>
                      {relativeTime(deploy.started_at)}
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: '0.75rem', color: '#4a5568' }}>No deployments yet</div>
                )}
                {deploy && (
                  <div style={{ display: 'flex', gap: 6, marginTop: '0.5rem' }}>
                    {isProd && canPromote && stagingEnv?.latest_deploy && (
                      <button
                        onClick={() => setPromoteTarget({
                          sha: stagingEnv.latest_deploy!.git_sha,
                          imageTag: stagingEnv.latest_deploy!.image_tag,
                          commitMessage: stagingEnv.latest_deploy!.commit_message,
                        })}
                        style={{
                          padding: '5px 12px',
                          fontSize: '0.72rem',
                          fontWeight: 600,
                          border: '1px solid #c4a882',
                          borderRadius: 5,
                          backgroundColor: 'transparent',
                          color: '#c4a882',
                          cursor: 'pointer',
                          fontFamily: 'inherit',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 4,
                        }}
                      >
                        <Rocket size={12} />
                        Promote
                      </button>
                    )}
                    <button
                      onClick={() => setRollbackTarget({ env: envName, currentImageTag: deploy.image_tag })}
                      style={{
                        padding: '5px 12px',
                        fontSize: '0.72rem',
                        fontWeight: 600,
                        border: '1px solid #718096',
                        borderRadius: 5,
                        backgroundColor: 'transparent',
                        color: '#718096',
                        cursor: 'pointer',
                        fontFamily: 'inherit',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                      }}
                    >
                      <RotateCcw size={12} />
                      Rollback
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ============ DEPLOY HISTORY ============ */}
      <div style={sectionStyle}>
        <h3 style={{ ...headingStyle, marginBottom: '0.75rem' }}>Deploy History</h3>
        {deployments.length === 0 ? (
          <div style={{ fontSize: '0.78rem', color: '#4a5568' }}>No deployments recorded.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', fontSize: '0.75rem', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #2a2a4a', color: '#718096', textAlign: 'left' }}>
                  <th style={{ padding: '6px 8px', fontWeight: 600 }}>Environment</th>
                  <th style={{ padding: '6px 8px', fontWeight: 600 }}>Status</th>
                  <th style={{ padding: '6px 8px', fontWeight: 600 }}>SHA</th>
                  <th style={{ padding: '6px 8px', fontWeight: 600 }}>Commit Message</th>
                  <th style={{ padding: '6px 8px', fontWeight: 600 }}>Time</th>
                  <th style={{ padding: '6px 8px', fontWeight: 600 }}>Logs</th>
                </tr>
              </thead>
              <tbody>
                {deployments.map(dep => {
                  const badge = statusBadge(dep.status);
                  return (
                    <tr key={dep.id} style={{ borderBottom: '1px solid #1a1a2e' }}>
                      <td style={{ padding: '6px 8px', color: '#e2e8f0', textTransform: 'capitalize' }}>{dep.environment}</td>
                      <td style={{ padding: '6px 8px' }}>
                        <span style={{
                          fontSize: '0.65rem',
                          fontWeight: 600,
                          padding: '2px 8px',
                          borderRadius: 3,
                          backgroundColor: badge.bg,
                          color: badge.color,
                        }}>
                          {dep.status}
                        </span>
                      </td>
                      <td style={{ padding: '6px 8px', fontFamily: 'monospace', color: '#c4a882' }}>{shortSha(dep.git_sha)}</td>
                      <td style={{ padding: '6px 8px', color: '#a0aec0', maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {dep.commit_message}
                      </td>
                      <td style={{ padding: '6px 8px', color: '#4a5568' }}>{relativeTime(dep.started_at)}</td>
                      <td style={{ padding: '6px 8px' }}>
                        {dep.logs_url ? (
                          <a href={dep.logs_url} target="_blank" rel="noreferrer" style={{ color: '#4299e1', display: 'flex', alignItems: 'center', gap: 3 }}>
                            <ExternalLink size={12} />
                            Logs
                          </a>
                        ) : (
                          <span style={{ color: '#4a5568' }}>—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ============ CONFIGURATION SYNC ============ */}
      <ConfigurationSync sectionStyle={sectionStyle} headingStyle={headingStyle} />

      {/* ============ PROMOTE MODAL ============ */}
      {promoteTarget && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
          }}
          onClick={() => !promoting && setPromoteTarget(null)}
        >
          <div
            style={{
              backgroundColor: '#1a1a2e',
              border: '1px solid #2a2a4a',
              borderRadius: 10,
              padding: '1.5rem',
              width: '100%',
              maxWidth: 440,
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ margin: 0, fontSize: '0.9rem', fontWeight: 600, color: '#e2e8f0' }}>
                Promote to Production
              </h3>
              <button
                onClick={() => !promoting && setPromoteTarget(null)}
                style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#718096', padding: 2 }}
              >
                <X size={16} />
              </button>
            </div>
            <div style={{ fontSize: '0.8rem', color: '#a0aec0', marginBottom: '0.75rem' }}>
              Deploy <span style={{ fontFamily: 'monospace', color: '#c4a882', fontWeight: 600 }}>{shortSha(promoteTarget.sha)}</span> to production?
            </div>
            <div style={{ fontSize: '0.75rem', color: '#718096', marginBottom: '1.25rem', padding: '0.5rem 0.75rem', backgroundColor: '#0f0f1a', borderRadius: 6, border: '1px solid #2a2a4a' }}>
              {promoteTarget.commitMessage}
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setPromoteTarget(null)}
                disabled={promoting}
                style={{
                  padding: '6px 16px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  border: '1px solid #2a2a4a',
                  borderRadius: 6,
                  backgroundColor: 'transparent',
                  color: '#a0aec0',
                  cursor: promoting ? 'not-allowed' : 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                Cancel
              </button>
              <button
                onClick={handlePromote}
                disabled={promoting}
                style={{
                  padding: '6px 16px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  border: 'none',
                  borderRadius: 6,
                  backgroundColor: '#c4a882',
                  color: '#0f0f1a',
                  cursor: promoting ? 'not-allowed' : 'pointer',
                  fontFamily: 'inherit',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                }}
              >
                <Rocket size={14} />
                {promoting ? 'Deploying...' : 'Confirm Deploy'}
              </button>
            </div>
          </div>
        </div>
      )}
      {/* ============ ROLLBACK MODAL ============ */}
      {rollbackTarget && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
          }}
          onClick={() => !rollingBack && setRollbackTarget(null)}
        >
          <div
            style={{
              backgroundColor: '#1a1a2e',
              border: '1px solid #2a2a4a',
              borderRadius: 10,
              padding: '1.5rem',
              width: '100%',
              maxWidth: 440,
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ margin: 0, fontSize: '0.9rem', fontWeight: 600, color: '#e2e8f0' }}>
                Rollback {rollbackTarget.env}
              </h3>
              <button
                onClick={() => !rollingBack && setRollbackTarget(null)}
                style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#718096', padding: 2 }}
              >
                <X size={16} />
              </button>
            </div>
            <div style={{ fontSize: '0.8rem', color: '#a0aec0', marginBottom: '1.25rem' }}>
              This will redeploy the <strong>previous successful version</strong> of <span style={{ color: '#c4a882', textTransform: 'capitalize' }}>{rollbackTarget.env}</span>.
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setRollbackTarget(null)}
                disabled={rollingBack}
                style={{
                  padding: '6px 16px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  border: '1px solid #2a2a4a',
                  borderRadius: 6,
                  backgroundColor: 'transparent',
                  color: '#a0aec0',
                  cursor: rollingBack ? 'not-allowed' : 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleRollback}
                disabled={rollingBack}
                style={{
                  padding: '6px 16px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  border: 'none',
                  borderRadius: 6,
                  backgroundColor: '#fc8181',
                  color: '#0f0f1a',
                  cursor: rollingBack ? 'not-allowed' : 'pointer',
                  fontFamily: 'inherit',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                }}
              >
                <RotateCcw size={14} />
                {rollingBack ? 'Rolling back...' : 'Confirm Rollback'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
