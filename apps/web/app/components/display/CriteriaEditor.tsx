'use client';

import { useState, useEffect, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';

interface Criterion {
  id: string;
  text: string;
  required: boolean;
  category?: string;
}

function extractCriteria(data: Record<string, unknown>): {
  scope: string;
  position_name: string;
  criteria: Criterion[];
} {
  const scope = String(data.scope || 'company');
  const position_name = String(data.position_name || '');

  let rawCriteria: Record<string, unknown>[] = [];
  const val = data.criteria;
  if (Array.isArray(val)) {
    rawCriteria = val;
  }

  const criteria: Criterion[] = rawCriteria.map((c, i) => ({
    id: String(c.id || i),
    text: String(c.text || ''),
    required: Boolean(c.required ?? true),
    category: c.category ? String(c.category) : undefined,
  }));

  return { scope, position_name, criteria };
}

export default function CriteriaEditor({ data, props, onAction, threadId }: DisplayBlockProps) {
  const workingDocId = (data as Record<string, unknown>)?.working_document_id as string | undefined;

  const [docData, setDocData] = useState<Record<string, unknown> | null>(workingDocId ? null : data);
  const [docVersion, setDocVersion] = useState(1);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!workingDocId || !threadId) return;
    apiFetch(`/api/threads/${threadId}/working-documents/${workingDocId}`)
      .then(res => res.ok ? res.json() : null)
      .then(doc => {
        if (doc) {
          setDocData(doc.data);
          setDocVersion(doc.version);
        }
      })
      .catch(() => {});
  }, [workingDocId, threadId]);

  useEffect(() => {
    if (workingDocId) return;
    setDocData(data);
  }, [data, workingDocId]);

  const parsed = extractCriteria(docData || data);
  const [criteria, setCriteria] = useState<Criterion[]>(parsed.criteria);
  const [adding, setAdding] = useState(false);
  const [newText, setNewText] = useState('');
  const [newCategory, setNewCategory] = useState('');
  const [newRequired, setNewRequired] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setCriteria(parsed.criteria);
  }, [docData]);

  const interactive = !!onAction || !!workingDocId;
  const title = (props?.title as string) || (parsed.scope === 'position' ? `Criteria: ${parsed.position_name}` : 'Company Criteria');

  const patchDoc = useCallback(async (ops: Record<string, unknown>[]) => {
    if (!workingDocId || !threadId) return;
    try {
      const res = await apiFetch(`/api/threads/${threadId}/working-documents/${workingDocId}`, {
        method: 'PATCH',
        body: JSON.stringify({ ops, version: docVersion }),
      });
      if (res.ok) {
        const updated = await res.json();
        setDocData(updated.data);
        setDocVersion(updated.version);
        setCriteria(extractCriteria(updated.data).criteria);
      }
    } catch { /* ignore */ }
  }, [workingDocId, threadId, docVersion]);

  const handleToggleRequired = useCallback((id: string, required: boolean) => {
    setCriteria(prev => prev.map(c => c.id === id ? { ...c, required } : c));
    setDirty(true); setSaved(false);
    patchDoc([{ op: 'update_criterion', criterion_id: id, fields: { required } }]);
  }, [patchDoc]);

  const handleUpdateText = useCallback((id: string, text: string) => {
    setCriteria(prev => prev.map(c => c.id === id ? { ...c, text } : c));
    setDirty(true); setSaved(false);
    patchDoc([{ op: 'update_criterion', criterion_id: id, fields: { text } }]);
  }, [patchDoc]);

  const handleRemove = useCallback((id: string) => {
    setCriteria(prev => prev.filter(c => c.id !== id));
    setDirty(true); setSaved(false);
    patchDoc([{ op: 'remove_criterion', criterion_id: id }]);
  }, [patchDoc]);

  const handleAdd = useCallback(() => {
    if (!newText.trim()) return;
    const criterion: Criterion = {
      id: String(Date.now()).slice(-8),
      text: newText,
      required: newRequired,
      category: newCategory || undefined,
    };
    setCriteria(prev => [...prev, criterion]);
    setAdding(false);
    setNewText('');
    setNewCategory('');
    setNewRequired(true);
    setDirty(true); setSaved(false);
    patchDoc([{ op: 'add_criterion', fields: { id: criterion.id, text: criterion.text, required: criterion.required, category: criterion.category } }]);
  }, [newText, newCategory, newRequired, patchDoc]);

  const handleSubmit = useCallback(async () => {
    setSaving(true);
    try {
      const res = await apiFetch('/api/connectors/norm_hr/execute/save_criteria', {
        method: 'POST',
        body: JSON.stringify({
          params: {
            scope: parsed.scope,
            position_name: parsed.position_name || undefined,
            criteria: criteria.map(c => ({ id: c.id, text: c.text, required: c.required, category: c.category })),
          },
        }),
      });
      if (res.ok) {
        setDirty(false);
        setSaved(true);
      } else {
        console.error('[CriteriaEditor] save failed:', res.status, await res.text().catch(() => ''));
      }
    } finally { setSaving(false); }
  }, [parsed.scope, parsed.position_name, criteria]);

  const inputStyle: React.CSSProperties = {
    padding: '4px 8px', border: '1px solid #d1d5db', borderRadius: 4,
    fontSize: '0.82rem', fontFamily: 'inherit', boxSizing: 'border-box', outline: 'none',
  };

  return (
    <div style={{
      border: '1px solid #e5e7eb', borderRadius: 10, overflow: 'hidden',
      backgroundColor: '#fff', marginBottom: '0.75rem',
      boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
    }}>
      {/* Header */}
      <div style={{
        padding: '1rem 1.25rem',
        borderBottom: '1px solid #e5e7eb',
        background: 'linear-gradient(to bottom, #fafafa, #fff)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <span style={{ fontSize: '1rem', fontWeight: 700, color: '#111' }}>{title}</span>
          <span style={{
            fontSize: '0.68rem', fontWeight: 600, padding: '2px 8px', borderRadius: 10,
            color: parsed.scope === 'company' ? '#1e40af' : '#065f46',
            backgroundColor: parsed.scope === 'company' ? '#dbeafe' : '#d1fae5',
            border: `1px solid ${parsed.scope === 'company' ? '#93c5fd' : '#6ee7b7'}`,
          }}>
            {parsed.scope === 'company' ? 'Company' : parsed.position_name}
          </span>
        </div>
        <div style={{ fontSize: '0.78rem', color: '#6b7280', marginTop: '0.25rem' }}>
          {criteria.length} criteri{criteria.length === 1 ? 'on' : 'a'} defined
        </div>
      </div>

      {/* Criteria list */}
      <div style={{ padding: '0.5rem 1.25rem' }}>
        {criteria.length === 0 && !adding && (
          <div style={{ padding: '1.5rem', textAlign: 'center', color: '#9ca3af', fontSize: '0.82rem' }}>
            No criteria defined yet
          </div>
        )}
        {criteria.map(c => (
          <div key={c.id} style={{
            display: 'flex', alignItems: 'center', gap: '0.5rem',
            padding: '0.5rem 0', borderBottom: '1px solid #f3f4f6',
          }}>
            {interactive && (
              <input
                type="checkbox"
                checked={c.required}
                onChange={e => handleToggleRequired(c.id, e.target.checked)}
                title={c.required ? 'Required' : 'Optional'}
                style={{ accentColor: '#2563eb', cursor: 'pointer' }}
              />
            )}
            <div style={{ flex: 1, minWidth: 0 }}>
              {interactive ? (
                <input
                  value={c.text}
                  onChange={e => handleUpdateText(c.id, e.target.value)}
                  style={{ ...inputStyle, width: '100%', border: 'none', padding: '2px 0', fontSize: '0.85rem' }}
                />
              ) : (
                <span style={{ fontSize: '0.85rem', color: '#111' }}>{c.text}</span>
              )}
            </div>
            {c.category && (
              <span style={{
                fontSize: '0.65rem', fontWeight: 600, padding: '1px 6px', borderRadius: 8,
                backgroundColor: '#f3f4f6', color: '#6b7280',
              }}>
                {c.category}
              </span>
            )}
            {!interactive && (
              <span style={{
                fontSize: '0.65rem', fontWeight: 600, padding: '1px 6px', borderRadius: 8,
                backgroundColor: c.required ? '#dbeafe' : '#f3f4f6',
                color: c.required ? '#1e40af' : '#9ca3af',
              }}>
                {c.required ? 'Required' : 'Optional'}
              </span>
            )}
            {interactive && (
              <button onClick={() => handleRemove(c.id)} title="Remove" style={{
                border: 'none', background: 'none', cursor: 'pointer',
                color: '#d1d5db', fontSize: '0.85rem', padding: '0 4px',
                transition: 'color 0.15s',
              }} onMouseEnter={e => (e.currentTarget.style.color = '#ef4444')} onMouseLeave={e => (e.currentTarget.style.color = '#d1d5db')}>
                &#10005;
              </button>
            )}
          </div>
        ))}
      </div>

      {/* Add criterion */}
      {interactive && (
        <div style={{ padding: '0 1.25rem 0.5rem' }}>
          {!adding ? (
            <button onClick={() => setAdding(true)} style={{
              margin: '0.25rem 0', padding: '5px 12px', fontSize: '0.75rem', fontWeight: 500,
              border: '1px dashed #d1d5db', borderRadius: 6, backgroundColor: 'transparent',
              color: '#6b7280', cursor: 'pointer', fontFamily: 'inherit',
            }}>+ Add criterion</button>
          ) : (
            <div style={{
              margin: '0.25rem 0', padding: '0.6rem 0.75rem',
              border: '1px solid #dbeafe', borderRadius: 8, backgroundColor: '#f8fafc',
              display: 'flex', gap: '0.5rem', alignItems: 'flex-end', flexWrap: 'wrap',
            }}>
              <div style={{ flex: 2, minWidth: 200 }}>
                <label style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase' }}>Criterion</label>
                <input value={newText} onChange={e => setNewText(e.target.value)}
                  placeholder="e.g., Must have valid work visa" style={{ ...inputStyle, width: '100%' }} autoFocus />
              </div>
              <div style={{ flex: 0, minWidth: 100 }}>
                <label style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase' }}>Category</label>
                <input value={newCategory} onChange={e => setNewCategory(e.target.value)}
                  placeholder="e.g., Legal" style={{ ...inputStyle, width: 100 }} />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.75rem', color: '#6b7280', cursor: 'pointer' }}>
                <input type="checkbox" checked={newRequired} onChange={e => setNewRequired(e.target.checked)} style={{ accentColor: '#2563eb' }} />
                Required
              </label>
              <button onClick={handleAdd} style={{
                padding: '5px 14px', fontSize: '0.75rem', fontWeight: 600,
                backgroundColor: '#111', color: '#fff', border: 'none', borderRadius: 6,
                cursor: 'pointer', fontFamily: 'inherit',
              }}>Add</button>
              <button onClick={() => setAdding(false)} style={{
                padding: '5px 12px', fontSize: '0.75rem',
                backgroundColor: 'transparent', color: '#6b7280', border: '1px solid #d1d5db', borderRadius: 6,
                cursor: 'pointer', fontFamily: 'inherit',
              }}>Cancel</button>
            </div>
          )}
        </div>
      )}

      {/* Save button */}
      {interactive && (
        <div style={{
          padding: '0.75rem 1.25rem', borderTop: '1px solid #f3f4f6',
          display: 'flex', justifyContent: 'flex-end',
        }}>
          <button onClick={handleSubmit} disabled={saving || criteria.length === 0 || !dirty} style={{
            padding: '8px 24px', fontSize: '0.82rem', fontWeight: 600,
            border: 'none', borderRadius: 8,
            backgroundColor: !dirty ? (saved ? '#d1fae5' : '#e5e7eb') : '#111',
            color: !dirty ? (saved ? '#065f46' : '#9ca3af') : '#fff',
            cursor: saving || criteria.length === 0 || !dirty ? 'not-allowed' : 'pointer',
            fontFamily: 'inherit',
            transition: 'background-color 0.2s, color 0.2s',
          }}>{saving ? 'Saving...' : saved && !dirty ? 'Saved' : 'Save Criteria'}</button>
        </div>
      )}
    </div>
  );
}
