'use client';

/**
 * The pay-week picker every payroll page carries: previous / next, the
 * week as a list ("WE 18 Oct 2026", with "this week" and "last week"
 * marked), and a jump back to this week. Value and change are `week_start`
 * Mondays; the venue's zone and day start decide which week "this week" is.
 */
import { ChevronLeft, ChevronRight } from 'lucide-react';
import IconButton from '../../ui/IconButton';
import Button from '../../ui/Button';
import { shiftWeek, thisWeekStart, weLabel, weekOptions, type VenueLike } from './weeks';

export default function WeekPicker({ value, onChange, venue, thisWeek, back = 52, ahead = 1, disabled = false, testId }: {
  /** The pay week's Monday. */
  value: string;
  onChange: (weekStart: string) => void;
  venue?: VenueLike | null;
  /** The API's own idea of this week (`meta.this_week_start`); computed from the venue when absent. */
  thisWeek?: string | null;
  back?: number;
  ahead?: number;
  disabled?: boolean;
  testId?: string;
}) {
  const current = thisWeek || thisWeekStart(venue);
  const last = shiftWeek(current, -1);
  const options = weekOptions(current, back, ahead);
  if (!options.includes(value)) options.push(value);
  options.sort((a, b) => (a < b ? 1 : a > b ? -1 : 0));
  const newest = options[0];
  const oldest = options[options.length - 1];

  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4, maxWidth: '100%' }} data-testid={testId}>
      <IconButton icon={ChevronLeft} label="Previous week" onClick={() => onChange(shiftWeek(value, -1))} disabled={disabled || value <= oldest} />
      <select
        className="n-select"
        aria-label="Pay week"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        style={{ maxWidth: 220 }}
      >
        {options.map((w) => (
          <option key={w} value={w}>
            {weLabel(w, true)}{w === current ? ' · this week' : w === last ? ' · last week' : ''}
          </option>
        ))}
      </select>
      <IconButton icon={ChevronRight} label="Next week" onClick={() => onChange(shiftWeek(value, 1))} disabled={disabled || value >= newest} />
      {value !== last && (
        <Button size="sm" variant="quiet" onClick={() => onChange(last)} disabled={disabled}>Last week</Button>
      )}
    </div>
  );
}
