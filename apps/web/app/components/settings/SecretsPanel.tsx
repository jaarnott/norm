'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';

interface Secret {
  key: string;
  value_masked: string;
  description: string | null;
  updated_at: string;
}

export default function SecretsPanel() {
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  // Create form state
  const [newKey, setNewKey] = useState('');
  const [newValue, setNewValue] = useState('');
  const [newDescription, setNewDescription] = useState('');

  const showFeedback = (type: 'success' | 'error', message: string) => {
    setFeedback({ type, message });
    setTimeout(() => setFeedback(null), 4000);
  };

  const fetchSecrets = useCallback(async () => {
    try {
      const res = await apiFetch('/api/admin/secrets');
      if (res.ok) {
        const data = await res.json();
        setSecrets(data.secrets || []);
      } else {
        showFeedback('error', `Failed to load secrets (${res.status})`);
      }
    } catch {
      showFeedback('error', 'Failed to load secrets');
    }
  }, []);

  useEffect(() => {
    fetchSecrets().finally(() => setLoading(false));
  }, [fetchSecrets]);

  const openCreate = () => {
    setCreating(true);
    setEditingKey(null);
    setNewKey('');
    setNewValue('');
    setNewDescription('');
  };

  const openEdit = (secret: Secret) => {
    setCreating(false);
    setEditingKey(secret.key);
    setEditValue('');
    setEditDescription(secret.description || '');
  };

  const closeForm = () => {
    setCreating(false);
    setEditingKey(null);
  };

  const handleCreate = async () => {
    if (!newKey || !newValue) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/admin/secrets/${encodeURIComponent(newKey)}`, {
        method: 'PUT',
        body: JSON.stringify({ value: newValue, description: newDescription || null }),
      });
      if (res.ok) {
        showFeedback('success', `Secret "${newKey}" created`);
        setCreating(false);
        await fetchSecrets();
      } else {
        const text = await res.text();
        showFeedback('error', `Failed to create secret: ${text}`);
      }
    } catch {
      showFeedback('error', 'Failed to create secret');
    }
    setSaving(false);
  };

  const handleUpdate = async () => {
    if (!editingKey || !editValue) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/admin/secrets/${encodeURIComponent(editingKey)}`, {
        method: 'PUT',
        body: JSON.stringify({ value: editValue, description: editDescription || null }),
      });
      if (res.ok) {
        showFeedback('success', `Secret "${editingKey}" updated`);
        setEditingKey(null);
        await fetchSecrets();
      } else {
        const text = await res.text();
        showFeedback('error', `Failed to update secret: ${text}`);
      }
    } catch {
      showFeedback('error', 'Failed to update secret');
    }
    setSaving(false);
  };

  const handleDelete = async (key: string) => {
    if (!confirm(`Delete secret "${key}"? This cannot be undone.`)) return;
    try {
      const res = await apiFetch(`/api/admin/secrets/${encodeURIComponent(key)}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        showFeedback('success', `Secret "${key}" deleted`);
        await fetchSecrets();
      } else {
        const text = await res.text();
        showFeedback('error', `Failed to delete secret: ${text}`);
      }
    } catch {
      showFeedback('error', 'Failed to delete secret');
    }
  };

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '0.5rem 0.65rem',
    fontSize: '0.82rem',
    border: '1px solid #e2ddd7',
    borderRadius: 6,
    backgroundColor: '#fff',
    fontFamily: 'inherit',
    color: '#1a1a1a',
    outline: 'none',
    boxSizing: 'border-box',
  };

  const labelStyle: React.CSSProperties = {
    fontSize: '0.75rem',
    fontWeight: 600,
    color: '#555',
    marginBottom: 4,
    display: 'block',
  };

  if (loading) {
    return <div style={{ fontSize: '0.85rem', color: '#999', padding: '2rem 0' }}>Loading secrets...</div>;
  }

  return (
    <div>
      {/* Feedback message */}
      {feedback && (
        <div style={{
          padding: '0.5rem 0.85rem',
          marginBottom: '1rem',
          borderRadius: 6,
          fontSize: '0.8rem',
          fontWeight: 500,
          backgroundColor: feedback.type === 'success' ? '#d4edda' : '#f8d7da',
          color: feedback.type === 'success' ? '#155724' : '#721c24',
          border: `1px solid ${feedback.type === 'success' ? '#c3e6cb' : '#f5c6cb'}`,
        }}>
          {feedback.message}
        </div>
      )}

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
        <h3 style={{ margin: 0, fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          System Secrets
        </h3>
        {!creating && editingKey === null && (
          <button
            onClick={openCreate}
            style={{
              padding: '0.4rem 0.85rem',
              fontSize: '0.78rem',
              fontWeight: 600,
              border: 'none',
              borderRadius: 6,
              backgroundColor: '#1a1a1a',
              color: '#fff',
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            Add Secret
          </button>
        )}
      </div>

      {/* Create form */}
      {creating && (
        <div style={{
          padding: '1.25rem',
          border: '1px solid #e2ddd7',
          borderRadius: 8,
          backgroundColor: '#fff',
          marginBottom: '1rem',
        }}>
          <h4 style={{ margin: '0 0 1rem', fontSize: '0.9rem', fontWeight: 600, color: '#1a1a1a' }}>
            Add New Secret
          </h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1rem' }}>
            <div>
              <label style={labelStyle}>Key</label>
              <input
                value={newKey}
                onChange={e => setNewKey(e.target.value)}
                placeholder="e.g. OPENAI_API_KEY"
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>Value</label>
              <input
                type="password"
                value={newValue}
                onChange={e => setNewValue(e.target.value)}
                placeholder="Secret value"
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>Description</label>
              <input
                value={newDescription}
                onChange={e => setNewDescription(e.target.value)}
                placeholder="Optional description"
                style={inputStyle}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              onClick={handleCreate}
              disabled={saving || !newKey || !newValue}
              style={{
                padding: '0.45rem 1rem',
                fontSize: '0.8rem',
                fontWeight: 600,
                border: 'none',
                borderRadius: 6,
                backgroundColor: saving ? '#999' : '#1a1a1a',
                color: '#fff',
                cursor: saving ? 'default' : 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {saving ? 'Saving...' : 'Create Secret'}
            </button>
            <button
              onClick={closeForm}
              style={{
                padding: '0.45rem 1rem',
                fontSize: '0.8rem',
                border: '1px solid #e2ddd7',
                borderRadius: 6,
                backgroundColor: '#fff',
                color: '#555',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Secrets list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {secrets.map(secret => (
          <div
            key={secret.key}
            style={{
              padding: '0.75rem 1rem',
              border: '1px solid #e2ddd7',
              borderRadius: 8,
              backgroundColor: '#fff',
            }}
          >
            {editingKey === secret.key ? (
              /* Inline edit form */
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.88rem', color: '#1a1a1a', marginBottom: '0.75rem' }}>
                  {secret.key}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginBottom: '0.75rem' }}>
                  <div>
                    <label style={labelStyle}>New Value</label>
                    <input
                      type="password"
                      value={editValue}
                      onChange={e => setEditValue(e.target.value)}
                      placeholder="Enter new value"
                      style={inputStyle}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>Description</label>
                    <input
                      value={editDescription}
                      onChange={e => setEditDescription(e.target.value)}
                      placeholder="Optional description"
                      style={inputStyle}
                    />
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button
                    onClick={handleUpdate}
                    disabled={saving || !editValue}
                    style={{
                      padding: '0.35rem 0.8rem',
                      fontSize: '0.78rem',
                      fontWeight: 600,
                      border: 'none',
                      borderRadius: 6,
                      backgroundColor: saving ? '#999' : '#1a1a1a',
                      color: '#fff',
                      cursor: saving ? 'default' : 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    {saving ? 'Saving...' : 'Update'}
                  </button>
                  <button
                    onClick={closeForm}
                    style={{
                      padding: '0.35rem 0.8rem',
                      fontSize: '0.78rem',
                      border: '1px solid #e2ddd7',
                      borderRadius: 6,
                      backgroundColor: '#fff',
                      color: '#555',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              /* Display row */
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.88rem', color: '#1a1a1a' }}>{secret.key}</div>
                  <div style={{ fontSize: '0.75rem', color: '#999', marginTop: 2, fontFamily: 'monospace' }}>
                    {secret.value_masked}
                  </div>
                  {secret.description && (
                    <div style={{ fontSize: '0.72rem', color: '#bbb', marginTop: 2 }}>{secret.description}</div>
                  )}
                  <div style={{ fontSize: '0.65rem', color: '#ccc', marginTop: 2 }}>
                    Updated: {new Date(secret.updated_at).toLocaleDateString()}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button
                    onClick={() => openEdit(secret)}
                    style={{
                      padding: '0.3rem 0.6rem',
                      fontSize: '0.72rem',
                      border: '1px solid #e2ddd7',
                      borderRadius: 4,
                      backgroundColor: '#fff',
                      color: '#555',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDelete(secret.key)}
                    style={{
                      padding: '0.3rem 0.6rem',
                      fontSize: '0.72rem',
                      border: '1px solid #f5c6cb',
                      borderRadius: 4,
                      backgroundColor: '#fff',
                      color: '#dc3545',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    Delete
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
        {secrets.length === 0 && !creating && (
          <div style={{ fontSize: '0.82rem', color: '#999' }}>No secrets configured yet.</div>
        )}
      </div>
    </div>
  );
}
