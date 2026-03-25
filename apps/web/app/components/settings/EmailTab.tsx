'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';

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

const sectionStyle: React.CSSProperties = {
  marginBottom: '1.25rem', padding: '1rem', backgroundColor: '#fff',
  border: '1px solid #e2e8f0', borderRadius: 8,
};
const headingStyle: React.CSSProperties = {
  fontSize: '0.82rem', fontWeight: 600, color: '#333', marginBottom: '0.75rem', margin: 0,
};

export default function EmailTab() {
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
      const data = await res.json();
      setTestResult(data.status === 'sent' ? 'Email sent successfully!' : `Failed: ${data.error || 'Unknown error'}`);
      fetchData();
    } catch (e) {
      setTestResult(`Error: ${String(e)}`);
    }
    setTestSending(false);
  };

  const handleConnect = (connector: string) => {
    // Redirect to OAuth flow
    window.open(`/api/oauth/authorize/${connector}`, '_blank', 'width=600,height=700');
  };

  const handleRetry = async (logId: string) => {
    await apiFetch(`/api/email/retry/${logId}`, { method: 'POST' });
    fetchData();
  };

  if (loading) return <div style={{ padding: '1rem', color: '#888' }}>Loading email settings...</div>;

  return (
    <div>
      {/* Connected Accounts */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Connected Email Accounts</h3>
        <p style={{ fontSize: '0.72rem', color: '#888', margin: '0 0 0.75rem' }}>
          Connect your Gmail or Outlook to let Norm send emails on your behalf (e.g., POs to suppliers, candidate outreach).
        </p>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          {['gmail', 'microsoft_outlook'].map(connector => {
            const conn = connections.find(c => c.connector_name === connector);
            const label = connector === 'gmail' ? 'Gmail' : 'Outlook';
            const connected = conn?.connected;
            return (
              <div key={connector} style={{
                flex: 1, padding: '0.75rem', border: '1px solid #e2e8f0', borderRadius: 8,
                backgroundColor: connected ? '#f0fff4' : '#fff',
              }}>
                <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#333', marginBottom: 4 }}>{label}</div>
                {connected ? (
                  <>
                    <div style={{ fontSize: '0.72rem', color: '#48bb78', marginBottom: 8 }}>
                      Connected{conn?.email ? ` as ${conn.email}` : ''}
                    </div>
                    <button
                      onClick={() => handleConnect(connector)}
                      style={{
                        padding: '4px 10px', fontSize: '0.72rem', border: '1px solid #cbd5e1', borderRadius: 5,
                        backgroundColor: '#fff', color: '#666', cursor: 'pointer', fontFamily: 'inherit',
                      }}
                    >Reconnect</button>
                  </>
                ) : (
                  <>
                    <div style={{ fontSize: '0.72rem', color: '#999', marginBottom: 8 }}>Not connected</div>
                    <button
                      onClick={() => handleConnect(connector)}
                      style={{
                        padding: '4px 10px', fontSize: '0.72rem', fontWeight: 600, border: 'none', borderRadius: 5,
                        backgroundColor: '#c4a882', color: '#fff', cursor: 'pointer', fontFamily: 'inherit',
                      }}
                    >Connect {label}</button>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Test Email */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Send Test Email</h3>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            value={testTo}
            onChange={e => setTestTo(e.target.value)}
            placeholder="recipient@example.com"
            style={{
              flex: 1, padding: '6px 10px', border: '1px solid #ddd', borderRadius: 6,
              fontSize: '0.82rem', fontFamily: 'inherit',
            }}
          />
          <button
            onClick={handleTestSend}
            disabled={testSending || !testTo}
            style={{
              padding: '6px 14px', fontSize: '0.78rem', fontWeight: 600,
              border: 'none', borderRadius: 6, backgroundColor: '#c4a882',
              color: '#fff', cursor: (testSending || !testTo) ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
            }}
          >{testSending ? 'Sending...' : 'Send Test'}</button>
        </div>
        {testResult && (
          <div style={{
            marginTop: 8, fontSize: '0.75rem',
            color: testResult.startsWith('Email sent') ? '#48bb78' : '#e53e3e',
          }}>{testResult}</div>
        )}
      </div>

      {/* Email Logs */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Recent Emails</h3>
        {logs.length === 0 ? (
          <div style={{ color: '#999', fontSize: '0.78rem' }}>No emails sent yet.</div>
        ) : (
          <div style={{ overflow: 'auto', maxHeight: 400 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.75rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #eee', color: '#888', textAlign: 'left' }}>
                  <th style={{ padding: '4px 8px', fontWeight: 600 }}>To</th>
                  <th style={{ padding: '4px 8px', fontWeight: 600 }}>Subject</th>
                  <th style={{ padding: '4px 8px', fontWeight: 600 }}>Type</th>
                  <th style={{ padding: '4px 8px', fontWeight: 600 }}>Status</th>
                  <th style={{ padding: '4px 8px', fontWeight: 600 }}>Date</th>
                  <th style={{ padding: '4px 8px', fontWeight: 600 }}></th>
                </tr>
              </thead>
              <tbody>
                {logs.map(log => (
                  <tr key={log.id} style={{ borderBottom: '1px solid #f0f0f0' }}>
                    <td style={{ padding: '4px 8px', color: '#333' }}>{(log.to_addresses || []).join(', ')}</td>
                    <td style={{ padding: '4px 8px', color: '#555', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{log.subject}</td>
                    <td style={{ padding: '4px 8px' }}>
                      <span style={{
                        fontSize: '0.65rem', fontWeight: 600, padding: '1px 6px', borderRadius: 3,
                        backgroundColor: log.sender_type === 'system' ? '#f0f0f0' : '#eff6ff',
                        color: log.sender_type === 'system' ? '#666' : '#2563eb',
                      }}>{log.sender_type}</span>
                    </td>
                    <td style={{ padding: '4px 8px' }}>
                      <span style={{
                        fontSize: '0.65rem', fontWeight: 600, padding: '1px 6px', borderRadius: 3,
                        backgroundColor: log.status === 'sent' ? '#f0fff4' : log.status === 'failed' ? '#fff5f5' : '#fffaf0',
                        color: log.status === 'sent' ? '#22543d' : log.status === 'failed' ? '#c53030' : '#975a16',
                      }}>{log.status}</span>
                    </td>
                    <td style={{ padding: '4px 8px', color: '#999' }}>
                      {log.created_at ? new Date(log.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}
                    </td>
                    <td style={{ padding: '4px 8px' }}>
                      {log.status === 'failed' && (
                        <button
                          onClick={() => handleRetry(log.id)}
                          style={{
                            padding: '2px 8px', fontSize: '0.65rem', border: '1px solid #cbd5e1', borderRadius: 3,
                            backgroundColor: '#fff', color: '#555', cursor: 'pointer', fontFamily: 'inherit',
                          }}
                        >Retry</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
