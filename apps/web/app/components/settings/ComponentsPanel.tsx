'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import type { OperationMapping, ConnectorSpecFull } from '../../types';

// Components that support document sync operations
const EDITABLE_COMPONENTS = [
  {
    key: 'roster_editor',
    label: 'Roster Editor',
    doc_type: 'roster',
    operations: [
      { op: 'load', label: 'Load Roster', defaultMethod: 'GET', isLoad: true },
      { op: 'add_shift', label: 'Add Shift', defaultMethod: 'POST' },
      { op: 'update_shift', label: 'Update Shift', defaultMethod: 'PUT' },
      { op: 'delete_shift', label: 'Delete Shift', defaultMethod: 'DELETE' },
    ],
  },
  {
    key: 'purchase_order_editor',
    label: 'Purchase Order Editor',
    doc_type: 'order',
    operations: [
      { op: 'load', label: 'Load Order', defaultMethod: 'GET', isLoad: true },
      { op: 'add_line', label: 'Add Line', defaultMethod: 'POST' },
      { op: 'update_line', label: 'Update Line', defaultMethod: 'PUT' },
      { op: 'remove_line', label: 'Remove Line', defaultMethod: 'DELETE' },
      { op: 'submit_order', label: 'Submit Order', defaultMethod: 'POST' },
    ],
  },
  {
    key: 'criteria_editor',
    label: 'Criteria Editor',
    doc_type: 'criteria',
    operations: [
      { op: 'load', label: 'Load Criteria', defaultMethod: 'GET', isLoad: true },
      { op: 'add_criterion', label: 'Add Criterion', defaultMethod: 'POST' },
      { op: 'update_criterion', label: 'Update Criterion', defaultMethod: 'PUT' },
      { op: 'remove_criterion', label: 'Remove Criterion', defaultMethod: 'DELETE' },
    ],
  },
];

const inputStyle: React.CSSProperties = {
  padding: '6px 8px', border: '1px solid #e2ddd7', borderRadius: 6,
  fontSize: '0.78rem', fontFamily: 'inherit', outline: 'none', width: '100%',
  boxSizing: 'border-box',
};

export default function ComponentsPanel() {
  const [specs, setSpecs] = useState<ConnectorSpecFull[]>([]);
  const [selectedComponent, setSelectedComponent] = useState(EDITABLE_COMPONENTS[0].key);
  const [selectedConnector, setSelectedConnector] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const component = EDITABLE_COMPONENTS.find(c => c.key === selectedComponent)!;

  // Load connector specs
  useEffect(() => {
    apiFetch('/api/connector-specs')
      .then(r => r.ok ? r.json() : { specs: [] })
      .then(data => {
        const list = Array.isArray(data?.specs) ? data.specs : [];
        setSpecs(list);
        if (list.length > 0 && !selectedConnector) {
          setSelectedConnector(list[0].connector_name);
        }
      })
      .catch(() => {});
  }, []);

  const activeSpec = specs.find(s => s.connector_name === selectedConnector);
  const mappings = activeSpec?.operation_mappings || [];

  // Get the mapping for a specific operation on the current component/connector
  const getMappingForOp = (opName: string): OperationMapping | undefined => {
    return mappings.find(m => m.operation === opName && m.doc_type === component.doc_type);
  };

  const toolActions = activeSpec?.tools?.map(t => t.action) || [];

  const updateMapping = useCallback((opName: string, field: string, value: unknown) => {
    if (!activeSpec) return;
    const existing = [...(activeSpec.operation_mappings || [])];
    const idx = existing.findIndex(m => m.operation === opName && m.doc_type === component.doc_type);

    if (idx >= 0) {
      existing[idx] = { ...existing[idx], [field]: value };
    } else {
      // Create new mapping
      const opDef = component.operations.find(o => o.op === opName);
      existing.push({
        operation: opName,
        doc_type: component.doc_type,
        target_action: '',
        method: opDef?.defaultMethod || 'POST',
        field_mapping: {},
        ref_fields: {},
        id_field: null,
        [field]: value,
      });
    }

    setSpecs(prev => prev.map(s =>
      s.connector_name === selectedConnector ? { ...s, operation_mappings: existing } : s
    ));
  }, [activeSpec, selectedConnector, component]);

  const removeMapping = useCallback((opName: string) => {
    if (!activeSpec) return;
    const filtered = (activeSpec.operation_mappings || []).filter(
      m => !(m.operation === opName && m.doc_type === component.doc_type)
    );
    setSpecs(prev => prev.map(s =>
      s.connector_name === selectedConnector ? { ...s, operation_mappings: filtered } : s
    ));
  }, [activeSpec, selectedConnector, component]);

  const handleSave = async () => {
    if (!activeSpec) return;
    setSaving(true);
    setFeedback(null);
    try {
      const res = await apiFetch(`/api/connector-specs/${activeSpec.connector_name}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operation_mappings: activeSpec.operation_mappings }),
      });
      if (res.ok) {
        setFeedback({ type: 'success', message: 'Mappings saved' });
        setTimeout(() => setFeedback(null), 3000);
      } else {
        const d = await res.json().catch(() => ({}));
        setFeedback({ type: 'error', message: d.detail || 'Failed to save' });
      }
    } catch {
      setFeedback({ type: 'error', message: 'Network error' });
    }
    setSaving(false);
  };

  return (
    <div>
      <h3 style={{ margin: '0 0 0.5rem', fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Component Sync Mappings
      </h3>
      <p style={{ color: '#999', fontSize: '0.78rem', margin: '0 0 1rem', lineHeight: 1.5 }}>
        Configure how each component syncs changes back to external systems. Select a component and connector to map operations to API actions.
      </p>

      {/* Component + Connector selectors */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1.25rem', flexWrap: 'wrap' }}>
        <div>
          <label style={{ display: 'block', fontSize: '0.7rem', fontWeight: 600, color: '#888', marginBottom: 4, textTransform: 'uppercase' }}>Component</label>
          <select
            value={selectedComponent}
            onChange={e => setSelectedComponent(e.target.value)}
            style={{ ...inputStyle, width: 200 }}
          >
            {EDITABLE_COMPONENTS.map(c => (
              <option key={c.key} value={c.key}>{c.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label style={{ display: 'block', fontSize: '0.7rem', fontWeight: 600, color: '#888', marginBottom: 4, textTransform: 'uppercase' }}>Connector</label>
          <select
            value={selectedConnector || ''}
            onChange={e => setSelectedConnector(e.target.value)}
            style={{ ...inputStyle, width: 200 }}
          >
            {specs.map(s => (
              <option key={s.connector_name} value={s.connector_name}>{s.display_name || s.connector_name}</option>
            ))}
          </select>
        </div>
      </div>

      {!activeSpec ? (
        <p style={{ color: '#bbb', fontSize: '0.82rem', fontStyle: 'italic' }}>Select a connector to configure mappings.</p>
      ) : (
        <>
          {/* Operations table */}
          {component.operations.map(opDef => {
            const isLoad = 'isLoad' in opDef && opDef.isLoad;
            const mapping = getMappingForOp(opDef.op);
            const isConfigured = !!mapping?.target_action;
            const fmEntries = Object.entries(mapping?.field_mapping || {});
            const refEntries = Object.entries(mapping?.ref_fields || {});

            return (
              <div key={opDef.op} style={{
                border: `1px solid ${isConfigured ? (isLoad ? '#bee3f8' : '#c3e6cb') : '#e8e4de'}`,
                borderRadius: 10, padding: '0.85rem', marginBottom: '0.75rem',
                backgroundColor: isConfigured ? (isLoad ? '#f0f8ff' : '#f8fdf9') : '#fafafa',
              }}>
                {/* Operation header */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span style={{ fontSize: '0.85rem', fontWeight: 600, color: '#333' }}>{opDef.label}</span>
                    <span style={{
                      fontSize: '0.65rem', fontFamily: 'monospace', padding: '1px 6px',
                      borderRadius: 4, backgroundColor: '#f0f0f0', color: '#888',
                    }}>{opDef.op}</span>
                    {isConfigured && (
                      <span style={{
                        fontSize: '0.6rem', fontWeight: 600, padding: '1px 6px',
                        borderRadius: 4,
                        backgroundColor: isLoad ? '#cce5ff' : '#d4edda',
                        color: isLoad ? '#004085' : '#155724',
                      }}>{isLoad ? 'Data Source' : 'Mapped'}</span>
                    )}
                  </div>
                  {mapping && (
                    <button onClick={() => removeMapping(opDef.op)} style={{
                      border: 'none', background: 'none', color: '#ccc', cursor: 'pointer', fontSize: '0.8rem',
                    }} title="Remove mapping">&#10005;</button>
                  )}
                </div>

                {/* Target action + method */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 80px', gap: 6, marginBottom: '0.5rem' }}>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.65rem', fontWeight: 600, color: '#aaa', marginBottom: 2 }}>Target Action</label>
                    <select
                      value={mapping?.target_action || ''}
                      onChange={e => updateMapping(opDef.op, 'target_action', e.target.value)}
                      style={inputStyle}
                    >
                      <option value="">Not mapped</option>
                      {toolActions.map(a => <option key={a} value={a}>{a}</option>)}
                    </select>
                  </div>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.65rem', fontWeight: 600, color: '#aaa', marginBottom: 2 }}>Method</label>
                    <select
                      value={mapping?.method || opDef.defaultMethod}
                      onChange={e => updateMapping(opDef.op, 'method', e.target.value)}
                      style={inputStyle}
                    >
                      <option value="GET">GET</option>
                      <option value="POST">POST</option>
                      <option value="PUT">PUT</option>
                      <option value="DELETE">DELETE</option>
                    </select>
                  </div>
                </div>

                {/* Field mapping */}
                {mapping?.target_action && (
                  <>
                    <div style={{ marginBottom: '0.5rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                        <span style={{ fontSize: '0.65rem', fontWeight: 600, color: '#888', textTransform: 'uppercase' }}>Field Mapping</span>
                        <button onClick={() => {
                          updateMapping(opDef.op, 'field_mapping', { ...(mapping.field_mapping || {}), '': '' });
                        }} style={{ border: 'none', background: 'none', fontSize: '0.65rem', color: '#999', cursor: 'pointer' }}>+ Add</button>
                      </div>
                      {fmEntries.length === 0 && (
                        <span style={{ fontSize: '0.72rem', color: '#ccc', fontStyle: 'italic' }}>No field mappings</span>
                      )}
                      {fmEntries.map(([k, v], fi) => (
                        <div key={fi} style={{ display: 'grid', gridTemplateColumns: '1fr 24px 1fr auto', gap: 4, alignItems: 'center', marginBottom: 2 }}>
                          <input value={k} onChange={e => {
                            const entries = Object.entries(mapping.field_mapping);
                            entries[fi] = [e.target.value, v];
                            updateMapping(opDef.op, 'field_mapping', Object.fromEntries(entries));
                          }} placeholder="component field" style={{ ...inputStyle, fontSize: '0.72rem', fontFamily: 'monospace' }} />
                          <span style={{ textAlign: 'center', color: '#ccc', fontSize: '0.7rem' }}>&#8594;</span>
                          <input value={v} onChange={e => {
                            const entries = Object.entries(mapping.field_mapping);
                            entries[fi] = [k, e.target.value];
                            updateMapping(opDef.op, 'field_mapping', Object.fromEntries(entries));
                          }} placeholder="API param" style={{ ...inputStyle, fontSize: '0.72rem', fontFamily: 'monospace' }} />
                          <button onClick={() => {
                            const next = { ...mapping.field_mapping };
                            delete next[k];
                            updateMapping(opDef.op, 'field_mapping', next);
                          }} style={{ border: 'none', background: 'none', color: '#ddd', cursor: 'pointer', fontSize: '0.75rem' }}>&#10005;</button>
                        </div>
                      ))}
                    </div>

                    {/* Ref fields */}
                    <div style={{ marginBottom: '0.5rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                        <span style={{ fontSize: '0.65rem', fontWeight: 600, color: '#888', textTransform: 'uppercase' }}>Ref Fields (from document context)</span>
                        <button onClick={() => {
                          updateMapping(opDef.op, 'ref_fields', { ...(mapping.ref_fields || {}), '': '' });
                        }} style={{ border: 'none', background: 'none', fontSize: '0.65rem', color: '#999', cursor: 'pointer' }}>+ Add</button>
                      </div>
                      {refEntries.map(([k, v], ri) => (
                        <div key={ri} style={{ display: 'grid', gridTemplateColumns: '1fr 24px 1fr auto', gap: 4, alignItems: 'center', marginBottom: 2 }}>
                          <input value={k} onChange={e => {
                            const entries = Object.entries(mapping.ref_fields || {});
                            entries[ri] = [e.target.value, v];
                            updateMapping(opDef.op, 'ref_fields', Object.fromEntries(entries));
                          }} placeholder="API param" style={{ ...inputStyle, fontSize: '0.72rem', fontFamily: 'monospace' }} />
                          <span style={{ textAlign: 'center', color: '#ccc', fontSize: '0.7rem' }}>&#8594;</span>
                          <input value={v} onChange={e => {
                            const entries = Object.entries(mapping.ref_fields || {});
                            entries[ri] = [k, e.target.value];
                            updateMapping(opDef.op, 'ref_fields', Object.fromEntries(entries));
                          }} placeholder="ref key" style={{ ...inputStyle, fontSize: '0.72rem', fontFamily: 'monospace' }} />
                          <button onClick={() => {
                            const next = { ...(mapping.ref_fields || {}) };
                            delete next[k];
                            updateMapping(opDef.op, 'ref_fields', next);
                          }} style={{ border: 'none', background: 'none', color: '#ddd', cursor: 'pointer', fontSize: '0.75rem' }}>&#10005;</button>
                        </div>
                      ))}
                    </div>

                    {/* ID field */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: '0.65rem', fontWeight: 600, color: '#888', textTransform: 'uppercase' }}>ID Field</span>
                      <input
                        value={mapping.id_field || ''}
                        onChange={e => updateMapping(opDef.op, 'id_field', e.target.value || null)}
                        placeholder="e.g. shift_id"
                        style={{ ...inputStyle, fontSize: '0.72rem', fontFamily: 'monospace', width: 160 }}
                      />
                    </div>
                  </>
                )}
              </div>
            );
          })}

          {/* Save button */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '1rem' }}>
            <button
              onClick={handleSave}
              disabled={saving}
              style={{
                padding: '8px 20px', fontSize: '0.85rem', fontWeight: 500,
                border: 'none', borderRadius: 6, backgroundColor: '#c4a882', color: '#fff',
                cursor: saving ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
                opacity: saving ? 0.6 : 1,
              }}
            >
              {saving ? 'Saving...' : 'Save Mappings'}
            </button>
            {feedback && (
              <span style={{
                fontSize: '0.78rem', fontWeight: 500,
                color: feedback.type === 'success' ? '#28a745' : '#dc3545',
              }}>
                {feedback.message}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
