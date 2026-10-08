'use client';

import { useId, useRef, useState } from 'react';
import { Calendar, ChevronDown } from 'lucide-react';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import Tabs from '../ui/Tabs';

interface DateRange {
  start: string;
  end: string;
  label: string;
}

interface DateRangePickerProps {
  value?: { start: string; end: string };
  onChange: (range: { start: string; end: string }) => void;
}

/** Popover width; it opens leftwards from the trigger unless that would run off-screen. */
const POPOVER_WIDTH = 264;

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
    { start: toIso(thisMonday), end: toIso(todayEnd), label: 'This week' },
    { start: toIso(lastMonday), end: toIso(lastSunday), label: 'Last week' },
    { start: toIso(thisMonthStart), end: toIso(todayEnd), label: 'This month' },
    { start: toIso(last30), end: toIso(todayEnd), label: 'Last 30 days' },
  ];
}

const MODE_TABS = [
  { id: 'presets', label: 'Presets' },
  { id: 'custom', label: 'Custom' },
];

export default function DateRangePicker({ value, onChange }: DateRangePickerProps) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<'presets' | 'custom'>('presets');
  const [customStart, setCustomStart] = useState(value?.start ? toDateInput(value.start) : '');
  const [customEnd, setCustomEnd] = useState(value?.end ? toDateInput(value.end) : '');
  // Which edge the popover hangs from: the trigger's right edge normally; its
  // left edge when the trigger sits near the left of a narrow screen.
  const [alignLeft, setAlignLeft] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const fieldId = useId();
  const presets = getPresets();

  const activeLabel = value
    ? presets.find(p => p.start.slice(0, 10) === value.start.slice(0, 10) && p.end.slice(0, 10) === value.end.slice(0, 10))?.label || 'Custom'
    : 'Date range';

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <Button
        variant="secondary"
        size="sm"
        icon={Calendar}
        aria-expanded={open}
        onClick={() => {
          const r = wrapRef.current?.getBoundingClientRect();
          if (r) setAlignLeft(r.right - POPOVER_WIDTH < 8);
          setOpen(!open);
        }}
        // No range chosen yet: the label reads as a placeholder.
        style={value ? undefined : { color: 'var(--muted)' }}
      >
        {activeLabel}
        <Icon icon={ChevronDown} size="dense" tone="muted" />
      </Button>

      {open && (
        <div className="n-card" style={{
          position: 'absolute', top: '100%', zIndex: 50, marginTop: 6,
          ...(alignLeft ? { left: 0 } : { right: 0 }),
          width: POPOVER_WIDTH, maxWidth: 'calc(100vw - 32px)', overflow: 'hidden',
          boxShadow: '0 8px 24px rgba(26, 26, 26, 0.12)',
        }}>
          {/* The wrapper repeats the strip's hairline so it runs edge to edge. */}
          <div style={{ padding: '0 14px', boxShadow: 'inset 0 -1px 0 var(--line)' }}>
            <Tabs
              items={MODE_TABS}
              value={mode}
              onChange={(id) => setMode(id === 'custom' ? 'custom' : 'presets')}
              label="Date range"
            />
          </div>

          {mode === 'presets' ? (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, padding: 12 }}>
              {presets.map(p => {
                const selected = activeLabel === p.label;
                return (
                  <button
                    key={p.label}
                    type="button"
                    aria-pressed={selected}
                    className="n-btn n-btn--secondary n-btn--sm"
                    onClick={() => { onChange({ start: p.start, end: p.end }); setOpen(false); }}
                    style={{
                      borderRadius: 999,
                      ...(selected ? {
                        backgroundColor: 'var(--accent-soft)', borderColor: 'var(--brand-soft)',
                        color: 'var(--accent)', fontWeight: 600,
                      } : {}),
                    }}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          ) : (
            <div style={{ padding: 12 }}>
              <label className="n-label" htmlFor={`${fieldId}-start`}>Start</label>
              <input id={`${fieldId}-start`} className="n-input" type="date" value={customStart}
                onChange={e => setCustomStart(e.target.value)} style={{ width: '100%' }} />
              <label className="n-label" htmlFor={`${fieldId}-end`} style={{ marginTop: 10 }}>End</label>
              <input id={`${fieldId}-end`} className="n-input" type="date" value={customEnd}
                onChange={e => setCustomEnd(e.target.value)} style={{ width: '100%' }} />
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  if (customStart && customEnd) {
                    onChange({ start: `${customStart}T00:00:00`, end: `${customEnd}T23:59:59` });
                    setOpen(false);
                  }
                }}
                disabled={!customStart || !customEnd}
                style={{ width: '100%', marginTop: 12 }}
              >Apply</Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
