'use client';

import { useState, useEffect, useCallback } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Check, Mail, TriangleAlert } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Badge from '../ui/Badge';
import type { BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';

interface EmailLogEntry {
  id: string;
  sender_type: string;
  sender_email: string;
  to_addresses: string[];
  subject: string;
  template_name: string | null;
  status: string;
  provider: string | null;
  error_message: string | null;
  created_at: string | null;
  sent_at: string | null;
}

interface EmailConnection {
  connector_name: string;
  connected: boolean;
  email: string | null;
}

/** A slug as words: "on_behalf" → "On behalf". */
function sentence(s: string): string {
  const words = s.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** sent = done, failed = error, queued (still to go out) = informational. */
function statusTone(status: string): BadgeTone {
  if (status === 'sent') return 'ok';
  if (status === 'failed') return 'error';
  if (status === 'queued') return 'info';
  return 'neutral';
}

// The log scrolls inside its card; its header row stays in view.
const stickyTh: CSSProperties = { position: 'sticky', top: 0, zIndex: 1 };

/** One settings section, as in the Settings design: an 18px title, an
 *  optional muted note under it, then the content (usually a white card). */
function Section({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section style={{ marginBottom: 28 }}>
      <h2 style={{ margin: note ? 0 : '0 0 12px', fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h2>
      {note && <p style={{ margin: '2px 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{note}</p>}
      {children}
    </section>
  );
}

/**
 * `canManage` (email:manage) shows Retry on a failed email. `canSendTest` is
 * for platform admins only: a test goes out from Norm's own address.
 */
export default function EmailTab({ canManage, canSendTest }: { canManage: boolean; canSendTest: boolean }) {
  const [logs, setLogs] = useState<EmailLogEntry[]>([]);
  const [connections, setConnections] = useState<EmailConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [testTo, setTestTo] = useState('');
  const [testSending, setTestSending] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [logsRes, connRes] = await Promise.all([
        apiFetch('/api/email/logs?limit=20'),
        apiFetch('/api/email/connections'),
      ]);
      if (logsRes.ok) {
        const data = await logsRes.json();
        setLogs(data.logs || []);
      }
      if (connRes.ok) {
        const data = await connRes.json();
        setConnections(data.connections || []);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleTestSend = async () => {
    if (!testTo) return;
    setTestSending(true);
    setTestResult(null);
    try {
      const res = await apiFetch('/api/email/send-test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to: testTo, template_name: 'task_complete', context: { task_title: 'Test Task', summary: 'This is a test email from Norm.' } }),
      });
      if (!res.ok) {
        const text = await res.text();
        setTestResult(`Failed (${res.status}): ${text}`);
        setTestSending(false);
        return;
      }
      const data = await res.json();
      setTestResult(data.status === 'sent' ? 'Email sent successfully!' : `Failed: ${data.error || 'Unknown error'}`);
      fetchData();
    } catch (e) {
      setTestResult(`Error: ${String(e)}`);
    }
    setTestSending(false);
  };

  const handleConnect = async (connector: string) => {
    try {
      const res = await apiFetch(`/api/oauth/authorize/${connector}`);
      if (res.ok) {
        const data = await res.json();
        const popup = window.open(data.authorize_url, '_blank', 'width=600,height=700');
        // Refresh connections when popup closes
        const timer = setInterval(() => {
          if (popup?.closed) {
            clearInterval(timer);
            fetchData();
          }
        }, 1000);
      }
    } catch { /* ignore */ }
  };

  const handleRetry = async (logId: string) => {
    await apiFetch(`/api/email/retry/${logId}`, { method: 'POST' });
    fetchData();
  };

  if (loading) return <PageState kind="loading" title="Loading email settings…" />;

  const testSent = testResult?.startsWith('Email sent') ?? false;

  return (
    <div style={{ lineHeight: 1.45 }}>
      {/* Connected Accounts */}
      <Section
        title="Connected email accounts"
        note="Connect your Gmail or Outlook to let Norm send emails on your behalf (e.g., POs to suppliers, candidate outreach)."
      >
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {['gmail', 'microsoft_outlook'].map(connector => {
            const conn = connections.find(c => c.connector_name === connector);
            const label = connector === 'gmail' ? 'Gmail' : 'Outlook';
            const connected = conn?.connected;
            return (
              <div key={connector} className="n-card" style={{ flex: '1 1 240px', minWidth: 0, padding: 16, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                  <Icon icon={Mail} tone="muted" />
                  <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{label}</span>
                  <span style={{ marginLeft: 'auto' }}>
                    {connected ? <Badge tone="ok">Connected</Badge> : <Badge>Not connected</Badge>}
                  </span>
                </div>
                {connected && conn?.email && (
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={conn.email}>
                    {conn.email}
                  </div>
                )}
                <div style={{ marginTop: 'auto', paddingTop: 6 }}>
                  {connected ? (
                    <Button size="sm" onClick={() => handleConnect(connector)}>Reconnect</Button>
                  ) : (
                    <Button size="sm" variant="primary" onClick={() => handleConnect(connector)}>Connect {label}</Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </Section>

      {/* Test Email */}
      {canSendTest && (
        <Section title="Send test email">
          <div className="n-card" style={{ padding: 16 }}>
            <label className="n-label" htmlFor="email-test-to">Recipient</label>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <input
                id="email-test-to"
                className="n-input"
                value={testTo}
                onChange={e => setTestTo(e.target.value)}
                placeholder="recipient@example.com"
                style={{ flex: '1 1 220px', minWidth: 0, maxWidth: 420 }}
              />
              <Button onClick={handleTestSend} disabled={testSending || !testTo}>
                {testSending ? 'Sending…' : 'Send test'}
              </Button>
            </div>
            {testResult && (
              <div role="status" style={{
                display: 'flex', alignItems: 'flex-start', gap: 6, marginTop: 10, fontSize: 'var(--fs-sm)',
                color: testSent ? 'var(--ok)' : 'var(--error)',
              }}>
                <Icon icon={testSent ? Check : TriangleAlert} size="dense" style={{ marginTop: 2 }} />
                <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{testResult}</span>
              </div>
            )}
          </div>
        </Section>
      )}

      {/* Email Logs */}
      <Section title="Recent emails">
        <div className="n-card" style={{ overflow: 'hidden' }}>
          {logs.length === 0 ? (
            <PageState kind="empty" title="No emails sent yet." />
          ) : (
            <div style={{ overflow: 'auto', maxHeight: 400 }}>
              <table className="n-table">
                <thead>
                  <tr>
                    <th style={stickyTh}>To</th>
                    <th style={stickyTh}>Subject</th>
                    <th style={stickyTh}>Type</th>
                    <th style={stickyTh}>Status</th>
                    <th style={stickyTh}>Date</th>
                    <th style={stickyTh}><span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' }}>Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map(log => (
                    <tr key={log.id}>
                      <td>{(log.to_addresses || []).join(', ')}</td>
                      <td style={{ color: 'var(--text-soft)', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={log.subject}>{log.subject}</td>
                      <td style={{ color: 'var(--text-soft)', whiteSpace: 'nowrap' }}>{sentence(log.sender_type)}</td>
                      <td><Badge tone={statusTone(log.status)}>{sentence(log.status)}</Badge></td>
                      <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                        {log.created_at ? new Date(log.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}
                      </td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {canManage && log.status === 'failed' && (
                          <Button variant="link" size="sm" onClick={() => handleRetry(log.id)}>Retry</Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Section>
    </div>
  );
}
