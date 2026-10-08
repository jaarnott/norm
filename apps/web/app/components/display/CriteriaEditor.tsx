'use client';

import { useState, useEffect, useCallback, useId } from 'react';
import { Check, Plus, X } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

/** Inner dividers: the faintest rule, as in the approval card. */
const LINE = '1px solid var(--line-soft)';

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
  const title = (props?.title as string) || (parsed.scope === 'position' ? `Criteria: ${parsed.position_name}` : 'Company criteria');
  const fieldId = useId();

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

  // The category column only exists when some criterion has a category.
  const hasCategory = criteria.some(c => c.category);
  const checkbox: React.CSSProperties = { width: 16, height: 16, margin: 0, accentColor: 'var(--accent)', cursor: 'pointer' };
  const checkLabel: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer',
  };

  return (
    <div className="n-card" style={{ overflow: 'hidden', marginBottom: '0.75rem' }}>
      {/* Header */}
      <div style={{ padding: '14px 16px 12px', borderBottom: LINE }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{title}</span>
          <Badge>{parsed.scope === 'company' ? 'Company' : parsed.position_name}</Badge>
        </div>
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', marginTop: 2 }}>
          {criteria.length} criteri{criteria.length === 1 ? 'on' : 'a'} defined
        </div>
      </div>

      {/* Criteria list. A row keeps its controls on one line when there is
          room; on a phone the text takes the full width and the category,
          "Required" and remove drop to a second line. */}
      <div style={{ padding: '0 16px' }}>
        {criteria.length === 0 && !adding && (
          <PageState kind="empty" title="No criteria defined yet" />
        )}
        {criteria.map((c, i) => (
          <div key={c.id} style={{
            display: 'flex', flexWrap: 'wrap', alignItems: 'center', columnGap: 12, rowGap: 6,
            padding: interactive ? '8px 0' : '10px 0',
            borderBottom: interactive || i < criteria.length - 1 ? LINE : 'none',
          }}>
            <div style={{ flex: '1 1 240px', minWidth: 0 }}>
              {interactive ? (
                <input
                  className="n-input"
                  aria-label={`Criterion ${i + 1}`}
                  value={c.text}
                  onChange={e => handleUpdateText(c.id, e.target.value)}
                  style={{ width: '100%' }}
                />
              ) : (
                <span style={{ fontSize: 'var(--fs-base)', color: 'var(--text)' }}>{c.text}</span>
              )}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginLeft: 'auto' }}>
              {hasCategory && (
                <span style={{ minWidth: 104 }}>
                  {c.category && <Badge>{c.category}</Badge>}
                </span>
              )}
              {interactive ? (
                <label style={checkLabel}>
                  <input
                    type="checkbox"
                    checked={c.required}
                    onChange={e => handleToggleRequired(c.id, e.target.checked)}
                    title={c.required ? 'Required' : 'Optional'}
                    style={checkbox}
                  />
                  Required
                </label>
              ) : (
                <Badge tone={c.required ? 'info' : 'neutral'}>{c.required ? 'Required' : 'Optional'}</Badge>
              )}
              {interactive && (
                <IconButton icon={X} label="Remove" onClick={() => handleRemove(c.id)} />
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Add criterion */}
      {interactive && (
        <div style={{ padding: '10px 16px 12px' }}>
          {!adding ? (
            <Button variant="quiet" size="sm" icon={Plus} onClick={() => setAdding(true)} style={{ marginLeft: -12 }}>
              Add criterion
            </Button>
          ) : (
            <div style={{
              padding: 12, border: '1px solid var(--line)', borderRadius: 'var(--radius)',
              backgroundColor: 'var(--surface)',
              display: 'flex', flexWrap: 'wrap', gap: '10px 12px', alignItems: 'flex-end',
            }}>
              <div style={{ flex: '2 1 220px', minWidth: 0 }}>
                <label className="n-label" htmlFor={`${fieldId}-text`}>Criterion</label>
                <input id={`${fieldId}-text`} className="n-input" value={newText} onChange={e => setNewText(e.target.value)}
                  placeholder="e.g., Must have valid work visa" style={{ width: '100%' }} autoFocus />
              </div>
              <div style={{ flex: '1 1 130px', minWidth: 0 }}>
                <label className="n-label" htmlFor={`${fieldId}-category`}>Category</label>
                <input id={`${fieldId}-category`} className="n-input" value={newCategory} onChange={e => setNewCategory(e.target.value)}
                  placeholder="e.g., Legal" style={{ width: '100%' }} />
              </div>
              <label style={{ ...checkLabel, minHeight: 34 }}>
                <input type="checkbox" checked={newRequired} onChange={e => setNewRequired(e.target.checked)} style={checkbox} />
                Required
              </label>
              <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
                <Button variant="quiet" size="sm" onClick={() => setAdding(false)}>Cancel</Button>
                <Button variant="secondary" size="sm" onClick={handleAdd}>Add</Button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Save button */}
      {interactive && (
        <div style={{ padding: '12px 16px', borderTop: LINE, display: 'flex', justifyContent: 'flex-end' }}>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={saving || criteria.length === 0 || !dirty}
            icon={saved && !dirty ? Check : undefined}
            // Just saved: the button reads as a green confirmation, not a faded one.
            style={saved && !dirty ? { backgroundColor: 'var(--ok-bg)', color: 'var(--ok)', opacity: 1 } : undefined}
          >{saving ? 'Saving…' : saved && !dirty ? 'Saved' : 'Save criteria'}</Button>
        </div>
      )}
    </div>
  );
}
