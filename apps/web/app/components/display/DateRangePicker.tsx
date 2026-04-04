'use client';

import { useState } from 'react';

interface DateRange {
  start: string;
  end: string;
  label: string;
}

interface DateRangePickerProps {
  value?: { start: string; end: string };
  onChange: (range: { start: string; end: string }) => void;
}

function toIso(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function toDateInput(iso: string): string {
  return iso.slice(0, 10);
}

function getPresets(): DateRange[] {
  const now = new Date();
  const todayStart = new Date(now); todayStart.setHours(0, 0, 0, 0);
  const todayEnd = new Date(now); todayEnd.setHours(23, 59, 59, 0);

  const yesterday = new Date(todayStart); yesterday.setDate(yesterday.getDate() - 1);
  const yesterdayEnd = new Date(yesterday); yesterdayEnd.setHours(23, 59, 59, 0);

  const day = now.getDay();
  const thisMonday = new Date(todayStart); thisMonday.setDate(now.getDate() - (day === 0 ? 6 : day - 1));
  const lastMonday = new Date(thisMonday); lastMonday.setDate(lastMonday.getDate() - 7);
  const lastSunday = new Date(thisMonday); lastSunday.setDate(lastSunday.getDate() - 1); lastSunday.setHours(23, 59, 59, 0);

  const thisMonthStart = new Date(now.getFullYear(), now.getMonth(), 1);
  const last30 = new Date(todayStart); last30.setDate(last30.getDate() - 30);

  return [
    { start: toIso(todayStart), end: toIso(todayEnd), label: 'Today' },
    { start: toIso(yesterday), end: toIso(yesterdayEnd), label: 'Yesterday' },
    { start: toIso(thisMonday), end: toIso(todayEnd), label: 'This Week' },
    { start: toIso(lastMonday), end: toIso(lastSunday), label: 'Last Week' },
    { start: toIso(thisMonthStart), end: toIso(todayEnd), label: 'This Month' },
    { start: toIso(last30), end: toIso(todayEnd), label: 'Last 30 Days' },
  ];
}

export default function DateRangePicker({ value, onChange }: DateRangePickerProps) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<'presets' | 'custom'>('presets');
  const [customStart, setCustomStart] = useState(value?.start ? toDateInput(value.start) : '');
  const [customEnd, setCustomEnd] = useState(value?.end ? toDateInput(value.end) : '');
  const presets = getPresets();

  const activeLabel = value
    ? presets.find(p => p.start.slice(0, 10) === value.start.slice(0, 10) && p.end.slice(0, 10) === value.end.slice(0, 10))?.label || 'Custom'
    : 'Date Range';

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          padding: '4px 10px', fontSize: '0.75rem', fontWeight: 500,
          border: '1px solid #e2ddd7', borderRadius: 6, backgroundColor: '#fff',
          cursor: 'pointer', fontFamily: 'inherit', color: value ? '#333' : '#999',
          display: 'flex', alignItems: 'center', gap: 4,
        }}
      >
        <span style={{ fontSize: '0.7rem' }}>📅</span>
        {activeLabel}
        <span style={{ fontSize: '0.6rem', color: '#bbb' }}>▼</span>
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, zIndex: 50, marginTop: 4,
          backgroundColor: '#fff', border: '1px solid #e2ddd7', borderRadius: 8,
          boxShadow: '0 4px 16px rgba(0,0,0,0.1)', minWidth: 220, overflow: 'hidden',
        }}>
          {/* Tabs */}
          <div style={{ display: 'flex', borderBottom: '1px solid #f0f0f0' }}>
            <button
              onClick={() => setMode('presets')}
              style={{
                flex: 1, padding: '6px', fontSize: '0.7rem', fontWeight: 600, border: 'none',
                backgroundColor: mode === 'presets' ? '#f5f0ea' : '#fff', color: mode === 'presets' ? '#a08060' : '#999',
                cursor: 'pointer', fontFamily: 'inherit',
              }}
            >Presets</button>
            <button
              onClick={() => setMode('custom')}
              style={{
                flex: 1, padding: '6px', fontSize: '0.7rem', fontWeight: 600, border: 'none',
                backgroundColor: mode === 'custom' ? '#f5f0ea' : '#fff', color: mode === 'custom' ? '#a08060' : '#999',
                cursor: 'pointer', fontFamily: 'inherit',
              }}
            >Custom</button>
          </div>

          {mode === 'presets' ? (
            <div style={{ padding: '4px 0' }}>
              {presets.map(p => (
                <button
                  key={p.label}
                  onClick={() => { onChange({ start: p.start, end: p.end }); setOpen(false); }}
                  style={{
                    display: 'block', width: '100%', padding: '6px 12px', fontSize: '0.75rem',
                    border: 'none', backgroundColor: activeLabel === p.label ? '#f5f0ea' : '#fff',
                    color: '#333', cursor: 'pointer', fontFamily: 'inherit', textAlign: 'left',
                  }}
                  onMouseEnter={e => { if (activeLabel !== p.label) e.currentTarget.style.backgroundColor = '#fafafa'; }}
                  onMouseLeave={e => { if (activeLabel !== p.label) e.currentTarget.style.backgroundColor = '#fff'; }}
                >
                  {p.label}
                </button>
              ))}
            </div>
          ) : (
            <div style={{ padding: '8px 12px' }}>
              <div style={{ marginBottom: 6 }}>
                <label style={{ display: 'block', fontSize: '0.65rem', fontWeight: 600, color: '#888', marginBottom: 2 }}>Start</label>
                <input type="date" value={customStart} onChange={e => setCustomStart(e.target.value)}
                  style={{ width: '100%', padding: '4px 6px', fontSize: '0.75rem', border: '1px solid #e2ddd7', borderRadius: 4, fontFamily: 'inherit' }} />
              </div>
              <div style={{ marginBottom: 8 }}>
                <label style={{ display: 'block', fontSize: '0.65rem', fontWeight: 600, color: '#888', marginBottom: 2 }}>End</label>
                <input type="date" value={customEnd} onChange={e => setCustomEnd(e.target.value)}
                  style={{ width: '100%', padding: '4px 6px', fontSize: '0.75rem', border: '1px solid #e2ddd7', borderRadius: 4, fontFamily: 'inherit' }} />
              </div>
              <button
                onClick={() => {
                  if (customStart && customEnd) {
                    onChange({ start: `${customStart}T00:00:00`, end: `${customEnd}T23:59:59` });
                    setOpen(false);
                  }
                }}
                disabled={!customStart || !customEnd}
                style={{
                  width: '100%', padding: '5px', fontSize: '0.75rem', fontWeight: 500,
                  border: 'none', borderRadius: 4, backgroundColor: '#a08060', color: '#fff',
                  cursor: customStart && customEnd ? 'pointer' : 'not-allowed', fontFamily: 'inherit',
                  opacity: customStart && customEnd ? 1 : 0.5,
                }}
              >Apply</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
