'use client';

import { useState, useEffect, useCallback } from 'react';
import { CircleCheck, Plus } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';
import { ConfirmDialog } from '../ui/Dialog';

interface Secret {
  key: string;
  value_masked: string;
  description: string | null;
  updated_at: string;
}

// Fields take .n-input (edge, radius, font); they only need to fill the row.
const fieldStyle: React.CSSProperties = { width: '100%' };

export default function SecretsPanel() {
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  // The secret whose delete is waiting on the confirmation dialog.
  const [deletingKey, setDeletingKey] = useState<string | null>(null);

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

  // Throws on failure: the confirmation dialog shows why and stays open.
  const handleDelete = async (key: string) => {
    let res: Response;
    try {
      res = await apiFetch(`/api/admin/secrets/${encodeURIComponent(key)}`, {
        method: 'DELETE',
      });
    } catch {
      throw new Error('Failed to delete secret — check your connection and try again.');
    }
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(`Failed to delete secret: ${typeof body?.detail === 'string' ? body.detail : `error ${res.status}`}`);
    }
    showFeedback('success', `Secret "${key}" deleted`);
    await fetchSecrets();
  };

  if (loading) {
    return <PageState kind="loading" title="Loading secrets…" />;
  }

  return (
    <div style={{ lineHeight: 1.45 }}>
      {/* Feedback message */}
      {feedback && (
        <div style={{ marginBottom: 16 }}>
          {feedback.type === 'error' ? (
            <PageState kind="error" title={feedback.message} />
          ) : (
            <div role="status" style={{
              display: 'flex', alignItems: 'flex-start', gap: 10, padding: '12px 14px',
              borderRadius: 'var(--radius)', background: 'var(--ok-bg)', color: 'var(--ok)',
              fontSize: 'var(--fs-base)', fontWeight: 600,
            }}>
              <Icon icon={CircleCheck} size={16} style={{ marginTop: 1 }} />
              <div style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{feedback.message}</div>
            </div>
          )}
        </div>
      )}

      {deletingKey && (
        <ConfirmDialog
          title={`Delete ${deletingKey}?`}
          confirmLabel="Delete secret"
          busyLabel="Deleting…"
          danger
          onConfirm={() => handleDelete(deletingKey)}
          onClose={() => setDeletingKey(null)}
        >
          <p style={{ margin: 0 }}>
            Every environment shares this secret, and each one stops loading it from its next restart. This can’t be undone.
          </p>
        </ConfirmDialog>
      )}

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 10, minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>
            System secrets
          </h3>
          {secrets.length > 0 && (
            <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              {secrets.length} {secrets.length === 1 ? 'secret' : 'secrets'}
            </span>
          )}
        </div>
        {!creating && editingKey === null && (
          <Button variant="primary" icon={Plus} onClick={openCreate}>
            Add secret
          </Button>
        )}
      </div>

      {/* Create form */}
      {creating && (
        <div className="n-card" style={{ padding: 16, marginBottom: 16 }}>
          <h4 style={{ margin: '0 0 12px', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>
            Add new secret
          </h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 16, maxWidth: 560 }}>
            <div>
              <label className="n-label" htmlFor="secret-new-key">Key</label>
              <input
                id="secret-new-key"
                className="n-input"
                value={newKey}
                onChange={e => setNewKey(e.target.value)}
                placeholder="e.g. OPENAI_API_KEY"
                style={fieldStyle}
              />
            </div>
            <div>
              <label className="n-label" htmlFor="secret-new-value">Value</label>
              <input
                id="secret-new-value"
                className="n-input"
                type="password"
                value={newValue}
                onChange={e => setNewValue(e.target.value)}
                placeholder="Secret value"
                style={fieldStyle}
              />
            </div>
            <div>
              <label className="n-label" htmlFor="secret-new-description">Description</label>
              <input
                id="secret-new-description"
                className="n-input"
                value={newDescription}
                onChange={e => setNewDescription(e.target.value)}
                placeholder="Optional description"
                style={fieldStyle}
              />
            </div>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <Button
              variant="primary"
              onClick={handleCreate}
              disabled={saving || !newKey || !newValue}
            >
              {saving ? 'Saving…' : 'Create secret'}
            </Button>
            <Button onClick={closeForm}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* Secrets list */}
      {secrets.length > 0 && (
        <div className="n-card" style={{ overflow: 'hidden' }}>
          {secrets.map((secret, index) => {
            const isEditing = editingKey === secret.key;
            return (
              <div
                key={secret.key}
                style={{
                  padding: '12px 16px',
                  borderTop: index > 0 ? '1px solid var(--line)' : 'none',
                  background: isEditing ? 'var(--surface)' : undefined,
                }}
              >
                {isEditing ? (
                  /* Inline edit form */
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 'var(--fs-base)', color: 'var(--text)', marginBottom: 12, overflowWrap: 'anywhere' }}>
                      {secret.key}
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 12, maxWidth: 560 }}>
                      <div>
                        <label className="n-label" htmlFor="secret-edit-value">New value</label>
                        <input
                          id="secret-edit-value"
                          className="n-input"
                          type="password"
                          value={editValue}
                          onChange={e => setEditValue(e.target.value)}
                          placeholder="Enter new value"
                          style={fieldStyle}
                        />
                      </div>
                      <div>
                        <label className="n-label" htmlFor="secret-edit-description">Description</label>
                        <input
                          id="secret-edit-description"
                          className="n-input"
                          value={editDescription}
                          onChange={e => setEditDescription(e.target.value)}
                          placeholder="Optional description"
                          style={fieldStyle}
                        />
                      </div>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={handleUpdate}
                        disabled={saving || !editValue}
                      >
                        {saving ? 'Saving…' : 'Update'}
                      </Button>
                      <Button size="sm" onClick={closeForm}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  /* Display row */
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 'var(--fs-base)', color: 'var(--text)', overflowWrap: 'anywhere' }}>{secret.key}</div>
                      {secret.value_masked && (
                        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 2, fontFamily: 'var(--font-mono)' }}>
                          {secret.value_masked}
                        </div>
                      )}
                      {secret.description && (
                        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginTop: 2 }}>{secret.description}</div>
                      )}
                      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 2 }}>
                        Updated: {new Date(secret.updated_at).toLocaleDateString()}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 8, flex: '0 0 auto' }}>
                      <Button size="sm" onClick={() => openEdit(secret)}>
                        Edit
                      </Button>
                      <Button size="sm" variant="danger" onClick={() => setDeletingKey(secret.key)}>
                        Delete
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {secrets.length === 0 && !creating && (
        <div className="n-card">
          <PageState kind="empty" title="No secrets configured yet." />
        </div>
      )}
    </div>
  );
}
