'use client';

import { useState } from 'react';
import type { Shift, ShiftFormData } from './shared';
import { staffName, formInputStyle } from './shared';

export interface StaffOption {
  id: string;
  name: string;
}

export interface RoleOption {
  id: string;
  name: string;
}

interface ShiftModalProps {
  editingShift: Shift | null;
  addingNew: boolean;
  saving: boolean;
  onSave: (data: ShiftFormData) => void;
  onDelete?: (shift: Shift) => void;
  onClose: () => void;
  staffOptions?: StaffOption[];
  roleOptions?: RoleOption[];
}

function ShiftForm({ initial, onSave, onCancel, saving, staffOptions, roleOptions }: {
  initial: ShiftFormData;
  onSave: (data: ShiftFormData) => void;
  onCancel: () => void;
  saving: boolean;
  staffOptions?: StaffOption[];
  roleOptions?: RoleOption[];
}) {
  const [form, setForm] = useState(initial);
  return (
    <div style={{ marginTop: '0.5rem' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.4rem', marginBottom: '0.4rem' }}>
        <div>
          <label style={{ fontSize: '0.72rem', color: '#666', fontWeight: 500 }}>Staff Member</label>
          {staffOptions && staffOptions.length > 0 ? (
            <select
              value={form.staff_member_id}
              onChange={e => setForm(f => ({ ...f, staff_member_id: e.target.value }))}
              style={formInputStyle}
            >
              <option value="">Select staff...</option>
              {staffOptions.map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          ) : (
            <input value={form.staff_member_id} onChange={e => setForm(f => ({ ...f, staff_member_id: e.target.value }))} placeholder="Staff Member ID" style={formInputStyle} />
          )}
        </div>
        <div>
          <label style={{ fontSize: '0.72rem', color: '#666', fontWeight: 500 }}>Role</label>
          {roleOptions && roleOptions.length > 0 ? (
            <select
              value={form.role_id}
              onChange={e => setForm(f => ({ ...f, role_id: e.target.value }))}
              style={formInputStyle}
            >
              <option value="">Select role...</option>
              {roleOptions.map(r => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
          ) : (
            <input value={form.role_id} onChange={e => setForm(f => ({ ...f, role_id: e.target.value }))} placeholder="Role ID" style={formInputStyle} />
          )}
        </div>
        <div>
          <label style={{ fontSize: '0.72rem', color: '#666', fontWeight: 500 }}>Clock In</label>
          <input type="datetime-local" value={form.clockin_time ? form.clockin_time.slice(0, 16) : ''}
            onChange={e => setForm(f => ({ ...f, clockin_time: e.target.value ? e.target.value + ':00' : '' }))} style={formInputStyle} />
        </div>
        <div>
          <label style={{ fontSize: '0.72rem', color: '#666', fontWeight: 500 }}>Clock Out</label>
          <input type="datetime-local" value={form.clockout_time ? form.clockout_time.slice(0, 16) : ''}
            onChange={e => setForm(f => ({ ...f, clockout_time: e.target.value ? e.target.value + ':00' : '' }))} style={formInputStyle} />
        </div>
      </div>
      <div style={{ display: 'flex', gap: '0.4rem' }}>
        <button onClick={() => onSave(form)} disabled={saving} style={{
          padding: '4px 12px', fontSize: '0.75rem', fontWeight: 600,
          backgroundColor: '#28a745', color: '#fff', border: 'none', borderRadius: 4,
          cursor: saving ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
        }}>{saving ? '...' : 'Save'}</button>
        <button onClick={onCancel} disabled={saving} style={{
          padding: '4px 12px', fontSize: '0.75rem',
          backgroundColor: '#fff', color: '#666', border: '1px solid #ddd', borderRadius: 4,
          cursor: 'pointer', fontFamily: 'inherit',
        }}>Cancel</button>
      </div>
    </div>
  );
}

export default function ShiftModal({ editingShift, addingNew, saving, onSave, onDelete, onClose, staffOptions, roleOptions }: ShiftModalProps) {
  if (!editingShift && !addingNew) return null;

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 1000,
      backgroundColor: 'rgba(0,0,0,0.35)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        backgroundColor: '#fff', borderRadius: 10,
        boxShadow: '0 8px 30px rgba(0,0,0,0.18)',
        width: '100%', maxWidth: 460, padding: '1.25rem',
        maxHeight: '90vh', overflowY: 'auto',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
          <span style={{ fontSize: '0.9rem', fontWeight: 600, color: '#333' }}>
            {editingShift ? 'Edit Shift' : 'New Shift'}
          </span>
          <button onClick={onClose} style={{
            border: 'none', background: 'none', cursor: 'pointer',
            fontSize: '1.1rem', color: '#999', lineHeight: 1, padding: '2px 6px',
          }}>&#10005;</button>
        </div>
        {editingShift && (
          <div style={{ fontSize: '0.78rem', color: '#666', marginBottom: '0.25rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span>{staffName(editingShift)} &middot; {editingShift.roleName}</span>
            {onDelete && (
              <button onClick={() => onDelete(editingShift)} disabled={saving} style={{
                border: 'none', background: 'none', cursor: 'pointer',
                fontSize: '0.75rem', color: '#e53e3e', fontFamily: 'inherit',
              }}>Delete shift</button>
            )}
          </div>
        )}
        <ShiftForm
          initial={editingShift ? {
            staff_member_id: String(editingShift.staffMemberId || ''),
            role_id: String(editingShift.roleId || ''),
            clockin_time: String(editingShift.clockinTime || ''),
            clockout_time: String(editingShift.clockoutTime || ''),
          } : { staff_member_id: '', role_id: '', clockin_time: '', clockout_time: '' }}
          onSave={onSave}
          onCancel={onClose}
          saving={saving}
          staffOptions={staffOptions}
          roleOptions={roleOptions}
        />
      </div>
    </div>
  );
}
