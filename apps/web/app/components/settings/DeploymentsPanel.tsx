'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import { Circle, ExternalLink, Rocket, X } from 'lucide-react';

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
// Component
// ---------------------------------------------------------------------------

export default function DeploymentsPanel() {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [promoteTarget, setPromoteTarget] = useState<{ sha: string; imageTag: string; commitMessage: string } | null>(null);
  const [promoting, setPromoting] = useState(false);

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
                {isProd && canPromote && stagingEnv?.latest_deploy && (
                  <button
                    onClick={() => setPromoteTarget({
                      sha: stagingEnv.latest_deploy!.git_sha,
                      imageTag: stagingEnv.latest_deploy!.image_tag,
                      commitMessage: stagingEnv.latest_deploy!.commit_message,
                    })}
                    style={{
                      marginTop: '0.5rem',
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
    </div>
  );
}
