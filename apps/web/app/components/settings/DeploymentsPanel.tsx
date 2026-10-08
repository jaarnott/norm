'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import { ExternalLink, Rocket, RotateCcw, X } from 'lucide-react';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

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

// A deploy's status as a badge: done → ok, queued → warn, in progress → info,
// failed → error; anything unexpected stays neutral.
function statusTone(status: string): BadgeTone {
  switch (status) {
    case 'success': return 'ok';
    case 'pending': return 'warn';
    case 'running': return 'info';
    case 'failed': return 'error';
    default: return 'neutral';
  }
}

function statusLabel(status: string): string {
  return status ? status.charAt(0).toUpperCase() + status.slice(1) : status;
}


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
  const sectionTitleStyle: React.CSSProperties = {
    margin: '0 0 12px',
    fontSize: 'var(--fs-lg)',
    fontWeight: 600,
    lineHeight: 1.3,
    color: 'var(--text)',
  };
  const overlayStyle: React.CSSProperties = {
    position: 'fixed',
    inset: 0,
    backgroundColor: 'rgba(26, 26, 26, 0.35)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 16,
    zIndex: 9999,
  };
  const dialogStyle: React.CSSProperties = {
    backgroundColor: 'var(--bg)',
    borderRadius: 'var(--radius-lg)',
    padding: 24,
    width: '100%',
    maxWidth: 440,
    boxShadow: '0 12px 40px rgba(26, 26, 26, 0.18)',
  };
  const dialogTitleStyle: React.CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
  const shaStyle: React.CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' };

  if (loading) return <PageState kind="loading" title="Loading deployments…" />;

  return (
    <div data-testid="deployments-panel" style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
      {error && (
        <PageState
          kind="error"
          title={error}
          action={<IconButton icon={X} label="Dismiss" iconSize={16} onClick={() => setError(null)} style={{ margin: '-6px -6px -6px 0' }} />}
        />
      )}

      {/* ============ ENVIRONMENT CARDS ============ */}
      <section>
        <h3 style={sectionTitleStyle}>Environments</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
          {['testing', 'staging', 'production'].map(envName => {
            const env = environments.find(e => e.name === envName);
            const deploy = env?.latest_deploy;
            const isProd = envName === 'production';
            return (
              <div key={envName} className="n-card" style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: 16, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 6 }}>
                  <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)', textTransform: 'capitalize' }}>
                    {envName}
                  </span>
                  {deploy && <Badge tone={statusTone(deploy.status)}>{statusLabel(deploy.status)}</Badge>}
                </div>
                {deploy ? (
                  <>
                    <div style={{ ...shaStyle, color: 'var(--text)' }}>{shortSha(deploy.git_sha)}</div>
                    <div title={deploy.commit_message} style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {deploy.commit_message}
                    </div>
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                      {relativeTime(deploy.started_at)}
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No deployments yet</div>
                )}
                {deploy && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 'auto', paddingTop: 12 }}>
                    {isProd && canPromote && stagingEnv?.latest_deploy && (
                      <Button
                        variant="primary"
                        size="sm"
                        icon={Rocket}
                        onClick={() => setPromoteTarget({
                          sha: stagingEnv.latest_deploy!.git_sha,
                          imageTag: stagingEnv.latest_deploy!.image_tag,
                          commitMessage: stagingEnv.latest_deploy!.commit_message,
                        })}
                      >
                        Promote
                      </Button>
                    )}
                    <Button
                      size="sm"
                      icon={RotateCcw}
                      onClick={() => setRollbackTarget({ env: envName, currentImageTag: deploy.image_tag })}
                    >
                      Rollback
                    </Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {/* ============ DEPLOY HISTORY ============ */}
      <section>
        <h3 style={sectionTitleStyle}>Deploy history</h3>
        {deployments.length === 0 ? (
          <div className="n-card">
            <PageState kind="empty" title="No deployments recorded." />
          </div>
        ) : (
          <div className="n-card" style={{ overflowX: 'auto' }}>
            <table className="n-table" style={{ minWidth: 720 }}>
              <thead>
                <tr>
                  <th>Environment</th>
                  <th>Status</th>
                  <th>SHA</th>
                  <th>Commit message</th>
                  <th>Time</th>
                  <th>Logs</th>
                </tr>
              </thead>
              <tbody>
                {deployments.map(dep => (
                  <tr key={dep.id}>
                    <td style={{ color: 'var(--text)', textTransform: 'capitalize', whiteSpace: 'nowrap' }}>{dep.environment}</td>
                    <td><Badge tone={statusTone(dep.status)}>{statusLabel(dep.status)}</Badge></td>
                    <td style={{ ...shaStyle, color: 'var(--text-soft)', whiteSpace: 'nowrap' }}>{shortSha(dep.git_sha)}</td>
                    {/* width 100% + maxWidth 0: the message takes the spare width and truncates. */}
                    <td title={dep.commit_message} style={{ width: '100%', maxWidth: 0, color: 'var(--text-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {dep.commit_message}
                    </td>
                    <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{relativeTime(dep.started_at)}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {dep.logs_url ? (
                        <a href={dep.logs_url} target="_blank" rel="noreferrer" className="n-btn n-btn--link" style={{ gap: 4, fontSize: 'var(--fs-sm)' }}>
                          <Icon icon={ExternalLink} size="meta" />
                          Logs
                        </a>
                      ) : (
                        <span style={{ color: 'var(--muted)' }}>—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ============ PROMOTE MODAL ============ */}
      {promoteTarget && (
        <div
          style={overlayStyle}
          onClick={() => !promoting && setPromoteTarget(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="deploy-promote-title"
            style={dialogStyle}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <h3 id="deploy-promote-title" style={dialogTitleStyle}>
                Promote to production
              </h3>
              <IconButton
                icon={X}
                label="Close"
                onClick={() => !promoting && setPromoteTarget(null)}
                style={{ margin: '-6px -8px -6px 0' }}
              />
            </div>
            <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)', marginBottom: 12 }}>
              Deploy <span style={{ ...shaStyle, fontWeight: 600, color: 'var(--text)' }}>{shortSha(promoteTarget.sha)}</span> to production?
            </div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginBottom: 20, padding: '8px 12px', backgroundColor: 'var(--surface)', borderRadius: 'var(--radius)', border: '1px solid var(--line)' }}>
              {promoteTarget.commitMessage}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-end' }}>
              <Button
                onClick={() => setPromoteTarget(null)}
                disabled={promoting}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                icon={Rocket}
                onClick={handlePromote}
                disabled={promoting}
              >
                {promoting ? 'Deploying…' : 'Confirm deploy'}
              </Button>
            </div>
          </div>
        </div>
      )}
      {/* ============ ROLLBACK MODAL ============ */}
      {rollbackTarget && (
        <div
          style={overlayStyle}
          onClick={() => !rollingBack && setRollbackTarget(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="deploy-rollback-title"
            style={dialogStyle}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <h3 id="deploy-rollback-title" style={dialogTitleStyle}>
                Rollback {rollbackTarget.env}
              </h3>
              <IconButton
                icon={X}
                label="Close"
                onClick={() => !rollingBack && setRollbackTarget(null)}
                style={{ margin: '-6px -8px -6px 0' }}
              />
            </div>
            <div style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)', marginBottom: 20 }}>
              This will redeploy the <strong style={{ fontWeight: 600, color: 'var(--text)' }}>previous successful version</strong> of <span style={{ fontWeight: 600, color: 'var(--text)', textTransform: 'capitalize' }}>{rollbackTarget.env}</span>.
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-end' }}>
              <Button
                onClick={() => setRollbackTarget(null)}
                disabled={rollingBack}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                icon={RotateCcw}
                onClick={handleRollback}
                disabled={rollingBack}
              >
                {rollingBack ? 'Rolling back…' : 'Confirm rollback'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
