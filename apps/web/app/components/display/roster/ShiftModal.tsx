'use client';

import { useId, useState, type CSSProperties } from 'react';
import { Plus, Trash2, X } from 'lucide-react';
import type { Shift, ShiftFormData, ShiftBreak } from './shared';
import { staffName, calcHours } from './shared';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';

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

const FIELD: CSSProperties = { width: '100%' };
// Two time fields share a row on a wide panel and stack on a phone.
const BREAK_TIME: CSSProperties = { flex: '1 1 170px', minWidth: 0 };

function ShiftForm({ initial, onSave, onCancel, onDelete, saving, staffOptions, roleOptions }: {
  initial: ShiftFormData;
  onSave: (data: ShiftFormData) => void;
  onCancel: () => void;
  /** Editing a shift that may be removed: drawn on the left of the footer. */
  onDelete?: () => void;
  saving: boolean;
  staffOptions?: StaffOption[];
  roleOptions?: RoleOption[];
}) {
  const [form, setForm] = useState(initial);
  const id = useId();

  // A removed break that already exists upstream must be sent back with
  // deletedAt set — dropping it from the array would leave it in place.
  const updateBreak = (i: number, patch: Partial<ShiftBreak>) =>
    setForm(f => ({
      ...f,
      breaks: (f.breaks || []).map((b, j) => (j === i ? { ...b, ...patch } : b)),
    }));
  const removeBreak = (i: number) =>
    setForm(f => ({
      ...f,
      breaks: (f.breaks || []).flatMap((b, j) => {
        if (j !== i) return [b];
        return b.id ? [{ ...b, deletedAt: new Date().toISOString() }] : [];
      }),
    }));
  const activeBreaks = (form.breaks || []).filter(b => !b.deletedAt);
  const breakMinutes = Math.round(
    activeBreaks.reduce((sum, b) => sum + calcHours(b.breakStart, b.breakEnd) * 60, 0),
  );

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px 16px' }}>
        <div>
          <label className="n-label" htmlFor={`${id}-staff`}>Staff member</label>
          {staffOptions && staffOptions.length > 0 ? (
            <select
              id={`${id}-staff`}
              className="n-select"
              value={form.staff_member_id}
              onChange={e => setForm(f => ({ ...f, staff_member_id: e.target.value }))}
              style={FIELD}
            >
              <option value="">Select staff…</option>
              {staffOptions.map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          ) : (
            <input id={`${id}-staff`} className="n-input" value={form.staff_member_id} onChange={e => setForm(f => ({ ...f, staff_member_id: e.target.value }))} placeholder="Staff member ID" style={FIELD} />
          )}
        </div>
        <div>
          <label className="n-label" htmlFor={`${id}-role`}>Role</label>
          {roleOptions && roleOptions.length > 0 ? (
            <select
              id={`${id}-role`}
              className="n-select"
              value={form.role_id}
              onChange={e => setForm(f => ({ ...f, role_id: e.target.value }))}
              style={FIELD}
            >
              <option value="">Select role…</option>
              {roleOptions.map(r => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
          ) : (
            <input id={`${id}-role`} className="n-input" value={form.role_id} onChange={e => setForm(f => ({ ...f, role_id: e.target.value }))} placeholder="Role ID" style={FIELD} />
          )}
        </div>
        <div>
          <label className="n-label" htmlFor={`${id}-in`}>Clock in</label>
          <input id={`${id}-in`} className="n-input" type="datetime-local" value={form.clockin_time ? form.clockin_time.slice(0, 16) : ''}
            onChange={e => setForm(f => ({ ...f, clockin_time: e.target.value ? e.target.value + ':00' : '' }))} style={FIELD} />
        </div>
        <div>
          <label className="n-label" htmlFor={`${id}-out`}>Clock out</label>
          <input id={`${id}-out`} className="n-input" type="datetime-local" value={form.clockout_time ? form.clockout_time.slice(0, 16) : ''}
            onChange={e => setForm(f => ({ ...f, clockout_time: e.target.value ? e.target.value + ':00' : '' }))} style={FIELD} />
        </div>
      </div>

      {/* Breaks. Unpaid time is deducted from paid hours, so getting these
          right matters for what someone is actually paid. */}
      <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
          <span className="n-label" style={{ margin: 0 }}>
            Breaks{activeBreaks.length > 0 && ` (${breakMinutes} min)`}
          </span>
          <Button
            size="sm"
            icon={Plus}
            onClick={() => setForm(f => ({
              ...f,
              breaks: [...(f.breaks || []), {
                breakStart: f.clockin_time, breakEnd: f.clockin_time, paid: false,
              } as ShiftBreak],
            }))}
          >
            Add break
          </Button>
        </div>
        {activeBreaks.length === 0 && (
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No breaks on this shift.</div>
        )}
        {(form.breaks || []).map((b, i) => b.deletedAt ? null : (
          <div key={b.id || i} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <input
              type="datetime-local" className="n-input" aria-label="Break start" value={(b.breakStart || '').slice(0, 16)}
              onChange={e => updateBreak(i, { breakStart: e.target.value ? e.target.value + ':00' : '' })}
              style={BREAK_TIME} />
            <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>to</span>
            <input
              type="datetime-local" className="n-input" aria-label="Break end" value={(b.breakEnd || '').slice(0, 16)}
              onChange={e => updateBreak(i, { breakEnd: e.target.value ? e.target.value + ':00' : '' })}
              style={BREAK_TIME} />
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer' }}>
                <input type="checkbox" checked={!!b.paid} style={{ margin: 0, accentColor: 'var(--accent)' }}
                  onChange={e => updateBreak(i, { paid: e.target.checked })} />
                Paid
              </label>
              <IconButton icon={X} iconSize={16} label="Remove break" onClick={() => removeBreak(i)} />
            </span>
          </div>
        ))}
      </div>

      {/* Destructive on the left, the way out and the one primary on the right. */}
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginTop: 20 }}>
        {onDelete && (
          <Button variant="danger" icon={Trash2} onClick={onDelete} disabled={saving}>Delete shift</Button>
        )}
        <span style={{ flex: 1 }} />
        <Button variant="secondary" onClick={onCancel} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={() => onSave(form)} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
      </div>
    </div>
  );
}

export default function ShiftModal({ editingShift, addingNew, saving, onSave, onDelete, onClose, staffOptions, roleOptions }: ShiftModalProps) {
  const titleId = useId();
  if (!editingShift && !addingNew) return null;

  const who = editingShift
    ? [staffName(editingShift), editingShift.roleName].filter(Boolean).join(' · ')
    : '';

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 1000,
      backgroundColor: 'rgba(26, 26, 26, 0.35)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 16,
    }}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={e => e.stopPropagation()}
        style={{
          backgroundColor: 'var(--bg)', borderRadius: 'var(--radius-lg)',
          boxShadow: '0 12px 40px rgba(26, 26, 26, 0.18)',
          width: '100%', maxWidth: 520, boxSizing: 'border-box',
          padding: '20px clamp(16px, 5vw, 24px)',
          maxHeight: '100%', overflowY: 'auto',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
          <div style={{ minWidth: 0 }}>
            <h2 id={titleId} style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>
              {editingShift ? 'Edit shift' : 'New shift'}
            </h2>
            {who && (
              <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{who}</div>
            )}
          </div>
          <IconButton icon={X} label="Close" onClick={onClose} style={{ margin: '-4px -8px 0 0' }} />
        </div>
        <ShiftForm
          initial={editingShift ? {
            staff_member_id: String(editingShift.staffMemberId || ''),
            role_id: String(editingShift.roleId || ''),
            clockin_time: String(editingShift.clockinTime || ''),
            clockout_time: String(editingShift.clockoutTime || ''),
            breaks: editingShift.breaks || [],
          } : { staff_member_id: '', role_id: '', clockin_time: '', clockout_time: '', breaks: [] }}
          onSave={onSave}
          onCancel={onClose}
          onDelete={editingShift && onDelete ? () => onDelete(editingShift) : undefined}
          saving={saving}
          staffOptions={staffOptions}
          roleOptions={roleOptions}
        />
      </div>
    </div>
  );
}
